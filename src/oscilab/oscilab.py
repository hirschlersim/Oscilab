"""
The main window, `Oscilab`: it puts the parts together — the menus,
toolbar, sidebar, plots, status bar and refresh loop — and is the API
scripts use. Each feature's menus, toolbar entries and dialogs are a part
of their own in `oscilab.ui`; the signals, plots and producers themselves
live in the window's `workspace` (`oscilab.workspace.Workspace`).
"""

import ctypes
import itertools
import sys
from collections.abc import Callable, Mapping, Sequence
from importlib import metadata, resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import ClassVar

from loguru import logger
from PyQt6 import QtCore as qtc
from PyQt6 import QtGui as qtg
from PyQt6 import QtWidgets as qtw

from oscilab import session
from oscilab.display import FPS_CHOICES, TIME_MODES, Fps, Theme, TimeMode
from oscilab.histogram_plot import HistogramPlot
from oscilab.log import StatusLine, log_file, log_to_file
from oscilab.logic_plot import LogicPlot
from oscilab.producers import DataProducer
from oscilab.signal_controller import SignalController
from oscilab.signal_model import SignalModel
from oscilab.spectrogram_plot import SpectrogramPlot
from oscilab.storage import Storage
from oscilab.timeseries_plot import TimeseriesPlot
from oscilab.trigger import TriggerSettings
from oscilab.ui.common import dot_icon
from oscilab.ui.controls import CustomControls
from oscilab.ui.file_menu import FileMenu
from oscilab.ui.plots_menu import MathMenu, PlotsMenu
from oscilab.ui.producers_menu import ProducersMenu
from oscilab.ui.recording import RecordingControls
from oscilab.ui.trigger import TriggerControls
from oscilab.workspace import Workspace
from oscilab.xy_plot import XYPlot

_window_ids = itertools.count(1)  # Tells each window's log messages apart (see oscilab.log)


class Oscilab(qtw.QMainWindow):
    """
    The main window — see the module docstring. Everything a script does
    goes through its methods: add plots, attach producers, save sessions, ...
    """

    # Exposed as `Oscilab.storage` / `scope.storage` so callers write e.g.
    # `scope.attach_producer(..., storage=scope.storage.deque)` instead of a bare string.
    storage = Storage

    _extra_windows: ClassVar[set["Oscilab"]] = set()  # Opened by open_session(); see there

    # (plot background, plot foreground) per theme — the QSS covers widget
    # chrome, but pyqtgraph's canvas colors are set programmatically.
    _PLOT_THEMES: ClassVar[dict[Theme, tuple[str, str]]] = {
        "dark": ("#202f46", "#ffffff"),
        "light": ("#f5f5f5", "#1a1a1a"),
    }

    def __init__(self, app: qtw.QApplication | None = None) -> None:
        if sys.platform == "win32":
            # Tell Windows to treat this as its own application (own taskbar
            # icon/grouping) rather than as python.exe. Must run before any
            # window is created.
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Oscilab")

        # Resolving the resource path is pure Python — safe before a QApplication
        # exists. Constructing QIcon/QPixmap from it is NOT: Qt requires a
        # QApplication/QGuiApplication to already exist first, so that has to
        # wait until after self.app is assigned below.
        icon_path = resources.files("oscilab.assets").joinpath("icon.png")

        # Initialize QApplication — or join the running one (a second
        # window, e.g. File > Open Session; Qt allows only one per process).
        running = qtw.QApplication.instance()
        app = app or (running if isinstance(running, qtw.QApplication) else None)
        starting_app = app is None
        self.app = self.__start_application() if starting_app else app

        with resources.as_file(icon_path) as path:
            app_icon = qtg.QIcon(str(path))
        self.app.setWindowIcon(app_icon)

        super().__init__()
        self.setWindowIcon(app_icon)  # Also set on the window itself, for the title bar/taskbar

        # Messages logged through `self.logger` also show in this window's
        # status bar, styled by severity (see oscilab.log). The Workspace and
        # its signal rows log through it too.
        self._theme: Theme = "dark"  # Matches the QSS loaded above and Workspace's default plot colors
        window_id = next(_window_ids)
        self.logger = logger.bind(window=window_id)
        status_bar = qtw.QStatusBar(self)
        self.setStatusBar(status_bar)
        self._status_line = StatusLine(status_bar, window_id, theme=self._theme)

        # Splash screen — only while the application itself is starting up.
        # Not for a window joining a running app (File > Open Session): a
        # QSplashScreen pumps the event loop whenever it repaints, which would
        # run other windows' timers in the middle of building this one.
        self.splash = self.__show_splash(icon_path) if starting_app else None

        # Main Properties
        self.setWindowTitle("Oscilab")
        self.resize(1920 // 3, 1080 // 3)
        self.move(50, 50)

        # Domain layer
        self.workspace = Workspace(logger=self.logger)
        self._fps: Fps = 30

        # Layout: the sidebar (the plots' panels, then the signal list), and the plots
        self.controller_layout = qtw.QVBoxLayout()
        self.controller_layout.setAlignment(qtc.Qt.AlignmentFlag.AlignTop)
        self.plot_layout = qtw.QVBoxLayout()
        self.plot_layout.setAlignment(qtc.Qt.AlignmentFlag.AlignTop)
        self.setCentralWidget(qtw.QWidget())
        main_layout = qtw.QHBoxLayout(self.centralWidget())
        main_layout.addLayout(self.controller_layout, stretch=0)
        main_layout.addLayout(self.plot_layout, stretch=1)

        # One flat list of every registered signal, regardless of how many
        # plots show it — added once, not per plot.
        self.controller_layout.addWidget(self.workspace.signal_list)

        # Timer / Update loop
        self.refresh_timer = qtc.QTimer()
        self.refresh_timer.timeout.connect(self.__refresh)
        self.refresh_timer.start(1000 // self._fps)

        # The features' parts (oscilab.ui) the menus and toolbar show
        self.controls_ui = CustomControls(self)
        self.recording_ui = RecordingControls(self)
        self.trigger_ui = TriggerControls(self)
        self.__build_menu_bar()
        self.__build_toolbar()  # After the menu bar: Start/Stop keeps View > Reset View in sync
        self.__build_status_bar()

        # The parts' handles scripts and tests reach for, on the window as before
        self.stream_action = self.recording_ui.stream_action
        self.stream_settings_action = self.recording_ui.settings_action
        self.stream_folder_action = self.recording_ui.folder_action
        self.replay_action = self.recording_ui.replay_action
        self.trigger_action = self.trigger_ui.action
        self.trigger_panel = self.trigger_ui.panel

        self.log("Oscilab ready")

    @staticmethod
    def __show_splash(icon_path: Traversable) -> qtw.QSplashScreen:
        with resources.as_file(icon_path) as path:
            pixmap = qtg.QPixmap(str(path))
        pixmap = pixmap.scaled(
            pixmap.width() // 2,
            pixmap.height() // 2,
            qtc.Qt.AspectRatioMode.KeepAspectRatio,
            qtc.Qt.TransformationMode.SmoothTransformation,
        )
        splash = qtw.QSplashScreen(pixmap)
        splash.show()
        return splash

    def __build_menu_bar(self) -> None:
        self.file_ui = FileMenu(self, self.recording_ui.menu_actions)
        self.__build_view_menu()
        self.plots_ui = PlotsMenu(self)
        self.math_ui = MathMenu(self)
        self.producers_ui = ProducersMenu(self, self.controls_ui)
        self.producers_menu = self.producers_ui.menu
        self.__build_cursors_menu()
        self.__build_help_menu()

    def __build_view_menu(self) -> None:
        view_menu = self.menuBar().addMenu("View")
        # Reset View's
        self.reset_views_action = qtg.QAction("Reset View", self)
        self.reset_views_action.setEnabled(False)  # Only meaningful while paused; kept in sync by __start_stop
        self.reset_views_action.setToolTip("Re-fit every plot to its data (only available while paused)")
        self.reset_views_action.triggered.connect(self.__reset_views)
        view_menu.addAction(self.reset_views_action)
        # Theme
        theme_menu = view_menu.addMenu("Theme")
        theme_menu.menuAction().setToolTip("Switch between dark and light color schemes")
        self._theme_action_group = qtg.QActionGroup(self)
        self._theme_action_group.setExclusive(True)
        for theme_name in self._PLOT_THEMES:
            theme_action = qtg.QAction(theme_name.capitalize(), self)
            theme_action.setCheckable(True)
            theme_action.setChecked(theme_name == "dark")  # matches the QSS loaded at startup
            theme_action.setToolTip(f"Switch the whole app to the {theme_name} theme")
            theme_action.triggered.connect(lambda _checked=False, t=theme_name: self.__set_theme(t))
            self._theme_action_group.addAction(theme_action)
            theme_menu.addAction(theme_action)
        # Time axis: Global (as recorded) vs Relative ([-duration, 0], now at 0)
        time_menu = view_menu.addMenu("Time")
        time_menu.menuAction().setToolTip("How every plot's time axis is labeled")
        time_tooltips = {
            "global": "Time axis shows time as recorded, e.g. since the app started",
            "relative": "Time axis is shifted so the newest sample is at 0 and older samples are negative",
        }
        self._time_action_group = qtg.QActionGroup(self)
        self._time_action_group.setExclusive(True)
        for mode in TIME_MODES:
            time_action = qtg.QAction(mode.capitalize(), self)
            time_action.setCheckable(True)
            time_action.setChecked(mode == "global")  # matches Workspace's default
            time_action.setToolTip(time_tooltips[mode])
            time_action.triggered.connect(lambda _checked=False, m=mode: self.__set_time_mode(m))
            self._time_action_group.addAction(time_action)
            time_menu.addAction(time_action)

    def __build_cursors_menu(self) -> None:
        # One checkable entry per time plot, added as they're created (see __show_time_plot)
        self.cursors_menu = self.menuBar().addMenu("Cursors")
        self.cursors_menu.menuAction().setToolTip("Toggle classic two-cursor time/value measurements on a plot")

    def __build_help_menu(self) -> None:
        help_menu = self.menuBar().addMenu("Help")
        # About Oscilab
        about_action = qtg.QAction("About Oscilab", self)
        about_action.setToolTip("Show the installed Oscilab version")
        about_action.triggered.connect(self.__show_about)
        help_menu.addAction(about_action)
        # Open Log Folder
        log_action = qtg.QAction("Open Log Folder", self)
        log_action.setToolTip("Show where the log file is, e.g. to attach it to a bug report")
        log_action.setEnabled(log_file() is not None)
        log_action.triggered.connect(
            lambda: qtg.QDesktopServices.openUrl(qtc.QUrl.fromLocalFile(str(log_file().parent)))
        )
        help_menu.addAction(log_action)
        # About QT
        about_qt_action = qtg.QAction("About Qt", self)
        about_qt_action.setToolTip("Show info about the Qt framework this app is built on")
        about_qt_action.triggered.connect(qtw.QApplication.aboutQt)
        help_menu.addAction(about_qt_action)

    def __build_toolbar(self) -> None:
        self.toolbar = self.addToolBar("Controls")
        self.toolbar.setToolButtonStyle(qtc.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        # Start-Stop Display
        self.start_stop_action = qtg.QAction(self)
        self.start_stop_action.setCheckable(True)
        self.start_stop_action.setToolTip("Pause or resume the refresh loop — plots stop updating while paused")
        self.__start_stop(checked=False)
        self.start_stop_action.toggled.connect(self.__start_stop)
        self.toolbar.addAction(self.start_stop_action)
        self.toolbar.addSeparator()
        # FPS dropdown
        fps_box = qtw.QComboBox()
        fps_box.addItems([f"{fps} FPS" for fps in FPS_CHOICES])
        fps_box.setCurrentText(f"{self._fps} FPS")
        fps_box.setToolTip("How often the plots redraw per second")
        fps_box.currentIndexChanged.connect(self.__set_fps)
        self.toolbar.addWidget(fps_box)
        self._fps_box = fps_box
        self.toolbar.addSeparator()
        # Duration dropdown — how many seconds of history are kept/displayed
        duration_box = qtw.QComboBox()
        duration_box.addItems([f"{s} s" for s in (5, 10, 15, 20, 30, 45, 60)])
        duration_box.setCurrentText("10 s")
        duration_box.setToolTip("How many seconds of history each signal keeps and displays")
        duration_box.currentTextChanged.connect(self.__set_duration)
        self.toolbar.addWidget(duration_box)
        self._duration_box = duration_box
        self.toolbar.addSeparator()
        # Downsample toggle — separate from Duration: it's about how much
        # gets *drawn*, not how much is *kept*.
        self.downsample_action = qtg.QAction(self)
        self.downsample_action.setCheckable(True)
        self.downsample_action.setToolTip(
            "Min-max downsampling: thin large buffers down to screen resolution\n"
            "for faster rendering, without dropping visible spikes"
        )
        self.__set_downsampling(checked=False)
        self.downsample_action.toggled.connect(self.__set_downsampling)
        self.toolbar.addAction(self.downsample_action)
        self.toolbar.addSeparator()
        # Save Stream and the Trigger, each with a drop-down of its commands
        self.__add_with_drop_down(self.recording_ui.stream_action, self.recording_ui.drop_down_actions)
        self.__add_with_drop_down(self.trigger_ui.action, self.trigger_ui.commands)

    def __add_with_drop_down(self, action: qtg.QAction, entries: list[qtg.QAction]) -> None:
        """A toolbar button for `action`, `entries` in its drop-down arrow."""
        self.toolbar.addAction(action)
        button = self.toolbar.widgetForAction(action)
        if isinstance(button, qtw.QToolButton):
            menu = qtw.QMenu(button)
            menu.addActions(entries)
            button.setMenu(menu)
            button.setPopupMode(qtw.QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.toolbar.addSeparator()

    def __build_status_bar(self) -> None:
        self.statusBar().setSizeGripEnabled(False)  # Disables the resize diagonal gripper lines in the corner
        self.statusBar().addPermanentWidget(self.recording_ui.label)
        load_label = qtw.QLabel("Load [%]:")
        load_tooltip = "Percent of the refresh interval spent redrawing on the last tick"
        load_label.setToolTip(load_tooltip)
        self.statusBar().addPermanentWidget(load_label)
        self.load_bar = qtw.QProgressBar()
        self.load_bar.setRange(0, 100)
        self.load_bar.setToolTip(load_tooltip)
        self.statusBar().addPermanentWidget(self.load_bar)

    # --- plots --------------------------------------------------------------------------

    def add_timeseries_plot(self, title: str) -> TimeseriesPlot:
        """A scrolling time-series plot; `link` signals to it."""
        plot, controller = self.workspace.add_plot(title)
        self.__show_time_plot(title, plot, controller)
        return plot

    def add_logic_plot(self, title: str, threshold: float = 0.5) -> LogicPlot:
        """
        Logic lanes: on/off signals in a lane each, high above `threshold`
        (see `oscilab.logic_plot`). `link` signals to it.
        """
        plot, controller = self.workspace.add_logic_plot(title, threshold=threshold)
        self.__show_time_plot(title, plot, controller)
        return plot

    def __show_time_plot(self, title: str, plot: TimeseriesPlot, controller: SignalController) -> None:
        self.__show_plot(plot, controller)
        cursor_action = qtg.QAction(title, self)
        cursor_action.setCheckable(True)
        cursor_action.setToolTip(f"Toggle measurement cursors on '{title}'")
        cursor_action.toggled.connect(lambda checked, t=title: self.workspace.set_cursors(t, checked))
        self.cursors_menu.addAction(cursor_action)

        controller.add_header_button(
            "Signals...", "Choose which signals this plot shows", lambda: self.plots_ui.edit_signals(title)
        )
        self.__add_remove_button(controller, title)

    def add_xy_plot(self, title: str, x: str, y: str, trail: float | None = None) -> XYPlot:
        """`y` against `x`, the last `trail` seconds of them or (None) all they hold (see `oscilab.xy_plot`)."""
        plot, controller = self.workspace.add_xy_plot(title, x, y, trail=trail)
        self.__show_plot(plot, controller)
        self.__add_remove_button(controller, title)
        return plot

    def add_histogram(
        self, label: str, bins: int = 50, value_range: tuple[float, float] | None = None, title: str | None = None
    ) -> HistogramPlot:
        """
        `label`'s value distribution in `bins` bars, over `value_range` or
        the data's own (see `oscilab.histogram_plot`). Titled
        "Histogram: <label>" unless given a title.
        """
        title = title or f"Histogram: {label}"
        plot, controller = self.workspace.add_histogram(label, bins=bins, value_range=value_range, title=title)
        self.__show_plot(plot, controller)
        self.__add_remove_button(controller, title)
        return plot

    def add_spectrogram_plot(self, label: str, nperseg: int = 256, noverlap: int = 128) -> SpectrogramPlot:
        """Add a live STFT spectrogram for an already-registered signal."""
        spectrogram, controller = self.workspace.add_spectrogram(label, nperseg=nperseg, noverlap=noverlap)
        self.__show_plot(spectrogram, controller)
        self.__add_remove_button(controller, label)
        return spectrogram

    def __show_plot(self, plot: qtw.QWidget, controller: SignalController) -> None:
        """
        The plot below the others, and its "Plot:" panel above the flat
        "Signals:" list — which stays at the bottom of the sidebar, however
        many plots get added, in whatever order.
        """
        self.plot_layout.addWidget(plot)
        self.controller_layout.insertWidget(self.controller_layout.count() - 1, controller)

    def __add_remove_button(self, controller: SignalController, title: str) -> None:
        controller.add_header_button("Remove", "Remove this plot; its signals stay", lambda: self.remove_plot(title))

    def remove_plot(self, title: str) -> None:
        """Remove a plot, of any kind; its signals stay."""
        self.workspace.remove_plot(title)
        for action in self.cursors_menu.actions():
            if action.text() == title:
                self.cursors_menu.removeAction(action)
                action.deleteLater()

    def link(self, plot: str, label: str) -> None:
        self.workspace.link(plot, label)
        self.log(f"Linked signal '{label}' to plot '{plot}'")

    def unlink(self, plot: str, label: str) -> None:
        """Take a signal off a time-series plot."""
        self.workspace.unlink(plot, label)
        self.log(f"Removed signal '{label}' from plot '{plot}'")

    def set_cursors(self, plot: str, enabled: bool) -> None:
        """Two-cursor measurement on a time-series plot, as in the Cursors menu."""
        action = next((a for a in self.cursors_menu.actions() if a.text() == plot), None)
        if action is None:
            raise ValueError(f"Unknown time-series plot '{plot}'")
        action.setChecked(enabled)

    # --- signals and producers -----------------------------------------------------------------

    def register_signal(self, label: str, fs: float, storage: Storage = Storage.ring, unit: str = "") -> SignalModel:
        """
        Register a signal you feed yourself (producer-fed signals are
        registered by `attach_producer`). `storage`: `self.storage.ring`
        (default) or `self.storage.deque` — see `oscilab.ring_buffer.RingBuffer`.
        `unit`: e.g. "V" (see `set_unit`).
        """
        return self.workspace.register_signal(label, fs, storage=storage, unit=unit)

    def set_unit(self, label: str, unit: str) -> None:
        """
        Change a signal's unit — e.g. "V", "°C"; "" for none — as its row's
        Unit button does. Shown in legends, on Y axes, in its row's metrics
        and in cursor readouts; math signals derive theirs from their operands.
        """
        self.workspace.set_unit(label, unit)

    def attach_producer(
        self,
        producer: DataProducer,
        signals: str | Sequence[str],
        storage: Storage = Storage.ring,
        units: str | Sequence[str] | None = None,
    ) -> list[SignalModel]:
        """
        Register one signal per producer channel, named by `signals` (a single
        label for a single-channel producer), and start the producer feeding
        them. The signals use the producer's `fs`, and its `units` unless you
        give them here: one per channel, or one for all (see `set_unit`).

        It gets an entry in the Producers menu — Start, Stop, and its
        `@button` / `@toggle` methods, which run on its own thread; those
        marked `toolbar=True` go in the toolbar too (see `oscilab.toolbar`).
        """
        models = self.workspace.attach_producer(producer, signals, storage=storage, units=units)
        self.producers_ui.add(producer)
        return models

    def detach_producer(self, label: str) -> list[str]:
        """
        Stop the producer feeding `label` and remove it — its Producers-menu
        entry and toolbar controls too — along with every signal it feeds, as
        its Remove entry does. Refused while a math signal uses one of them.
        Returns the labels removed.
        """
        producer = self.workspace.producers.get(label)
        labels = self.workspace.detach_producer(label)
        if producer is not None:
            self.producers_ui.remove(producer)
        return labels

    def producer_toggle_states(self, producer: DataProducer) -> dict[str, bool]:
        """Whether each of `producer`'s `@toggle` functions is on, keyed by method name."""
        return self.producers_ui.toggle_states(producer)

    def set_producer_toggle_states(self, producer: DataProducer, states: Mapping[str, bool]) -> None:
        """
        Switch `producer`'s `@toggle` functions (keyed by method name) on or
        off, running them on its thread as a click would. Names it doesn't
        have are skipped with a warning.
        """
        self.producers_ui.set_toggle_states(producer, states)

    def clear(self) -> None:
        """
        Remove the whole setup: every plot, math signal, signal you registered
        and producer (stopped first), ending a recording (Save Stream). The
        settings, your toolbar controls and the window itself stay.
        """
        self.set_recording(False)
        ws = self.workspace
        for title in list(ws.views):
            self.remove_plot(title)
        for label in reversed(list(ws.math_signals)):  # Each may use earlier ones
            ws.remove_signal(label)
        for label in [label for label in ws.signals if label not in ws.producers]:
            ws.remove_signal(label)
        for producer in dict.fromkeys(ws.producers.values()):
            self.detach_producer(ws.signals_fed_by(producer)[0])

    # --- your toolbar controls --------------------------------------------------------------------

    def add_toolbar(self, tools: object) -> list[qtg.QAction]:
        """
        Put the `@button` / `@toggle` methods of `tools` — an instance of your
        own class, or the class itself if they're classmethods — in the
        toolbar, after the built-in controls. See `oscilab.toolbar`.
        """
        return self.controls_ui.add_toolbar(tools)

    def add_toolbar_button(self, text: str, callback: Callable[[], object], tooltip: str = "") -> qtg.QAction:
        """A toolbar button calling `callback()` — e.g. a method of your own class — when clicked."""
        return self.controls_ui.add_button(text, callback, tooltip)

    def add_toolbar_toggle(
        self, text: str, callback: Callable[[bool], object], checked: bool = False, tooltip: str = ""
    ) -> qtg.QAction:
        """
        A toolbar toggle calling `callback(on)` with its new state whenever it
        changes. `checked`: the initial state, which isn't reported to `callback`.
        """
        return self.controls_ui.add_toggle(text, callback, checked, tooltip)

    def toggle_states(self) -> dict[str, bool]:
        """
        Whether each of your toolbar toggles is on — from `add_toolbar`, keyed
        `"ClassName.method"`, or `add_toolbar_toggle`, keyed by its text.
        Sessions save these (a producer's toggles are saved with it).
        """
        return self.controls_ui.toggle_states()

    def set_toggle_states(self, states: Mapping[str, bool]) -> None:
        """
        Switch your toolbar toggles on or off (keyed as `toggle_states` has
        them), calling their methods as a click would. A toggle that isn't in
        the toolbar yet switches as soon as `add_toolbar` adds it — so a
        session can be loaded before your tools are.
        """
        self.controls_ui.set_toggle_states(states)

    # --- trigger -------------------------------------------------------------------------------------

    @property
    def trigger_settings(self) -> TriggerSettings | None:
        """The trigger's settings while triggering is on, else None."""
        return self.trigger_ui.settings

    def set_trigger(self, settings: TriggerSettings | None) -> None:
        """
        Trigger the time plots on `settings` — as the Trigger button and
        panel do (see `oscilab.trigger`) — or, with None, go back to
        scrolling. Run, Single, Stop and Force: `workspace.trigger`.
        """
        self.trigger_ui.set(settings)

    # --- Save Stream and replay --------------------------------------------------------------------

    @property
    def recording(self) -> bool:
        """Whether Save Stream is on."""
        return self.recording_ui.recording

    def set_recording(self, on: bool) -> None:
        """Start or stop saving the stream, as the Save Stream button does (see `oscilab.recorder`)."""
        self.stream_action.setChecked(on)

    @property
    def recording_options(self) -> tuple[Path, float | None]:
        """Save Stream's base file path, and the size in MB its files reach before the next (None: no limit)."""
        return self.recording_ui.path, self.recording_ui.max_file_mb

    def set_recording_options(self, path: str | Path, max_file_mb: float | None = 100.0) -> None:
        """
        Where Save Stream records to — each recording's files are named after
        `path` and the time it started — and the size in MB at which it moves
        on to a new file (None: no limit). Applies from the next recording.
        """
        self.recording_ui.set_options(path, max_file_mb)

    def replay_recording(self, path: str | Path, signals: Sequence[str] | None = None, loop: bool = False) -> list[str]:
        """
        Replay a Save Stream recording — a file, or a folder of them, played
        in name order — with one `RecordingReplayProducer` per signal (every
        one by default), and show them in a new plot. With `loop`, after the
        last file it starts again from the first. Each keeps its recorded
        name, with " (replay)" added where that's taken. Returns their names.
        """
        return self.recording_ui.replay(path, signals, loop=loop)

    # --- display settings ---------------------------------------------------------------------------

    def log(self, message: str, level: str = "info") -> None:
        """
        Log through this window's `logger`: to the console, the log file and
        this window's status bar. `level`: "debug", "info", "success",
        "warning", "error" or "critical".
        """
        self.logger.opt(depth=1).log(level.upper(), message)

    @property
    def theme(self) -> Theme:
        return self._theme

    @property
    def fps(self) -> Fps:
        return self._fps

    # The setters below go through the menus/toolbar, so those always show
    # what's in effect — same as if the user had picked it there.

    def set_theme(self, theme: Theme) -> None:
        """`"dark"` or `"light"`."""
        if theme not in self._PLOT_THEMES:
            raise ValueError(f"Unknown theme {theme!r}; expected one of {list(self._PLOT_THEMES)}")
        for action in self._theme_action_group.actions():
            action.setChecked(action.text() == theme.capitalize())
        self.__set_theme(theme)

    def set_fps(self, fps: Fps) -> None:
        """Redraw rate: one of the toolbar's choices (5, 10, 30, 60)."""
        text = f"{fps} FPS"
        if self._fps_box.findText(text) < 0:
            choices = [self._fps_box.itemText(i) for i in range(self._fps_box.count())]
            raise ValueError(f"fps must be one of {choices}, got {fps}")
        self._fps_box.setCurrentText(text)

    def set_duration(self, duration: float) -> None:
        """Seconds of history kept and shown per signal; any value, not just the toolbar's choices."""
        text = f"{duration:g} s"
        if self._duration_box.findText(text) < 0:
            self._duration_box.addItem(text)
        self._duration_box.setCurrentText(text)

    def set_time_mode(self, mode: TimeMode) -> None:
        """`"global"` (time as recorded) or `"relative"` (newest sample at 0)."""
        if mode not in TIME_MODES:
            raise ValueError(f"Unknown time mode {mode!r}")
        for action in self._time_action_group.actions():
            action.setChecked(action.text().lower() == mode)
        self.__set_time_mode(mode)

    def set_downsampling(self, enabled: bool) -> None:
        self.downsample_action.setChecked(enabled)

    def set_window_geometry(self, x: int, y: int, width: int, height: int, maximized: bool = False) -> None:
        """
        Place the window: its inside (without the frame) at `x`, `y` on the
        desktop, `width` x `height` — and `maximized`, with that as where it
        goes when restored down. Kept on a screen: a position on a screen
        that's no longer connected moves to the current one.
        """
        rect = qtc.QRect(x, y, width, height)
        if qtg.QGuiApplication.screenAt(rect.center()) is None:
            screen = self.screen() or qtg.QGuiApplication.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                rect.setSize(rect.size().boundedTo(available.size()))
                rect.moveTopLeft(available.topLeft() + qtc.QPoint(50, 50))
        self.setGeometry(rect)
        maximize = qtc.Qt.WindowState.WindowMaximized
        state = self.windowState()
        self.setWindowState(state | maximize if maximized else state & ~maximize)

    def window_geometry(self) -> tuple[int, int, int, int, bool]:
        """`(x, y, width, height, maximized)`, as `set_window_geometry` takes them."""
        maximized = self.isMaximized()
        rect = self.normalGeometry() if maximized else self.geometry()
        return rect.x(), rect.y(), rect.width(), rect.height(), maximized

    # --- sessions and running ----------------------------------------------------------------------

    def save_session(self, path: str | Path) -> Path:
        """Save this window's setup as JSON — see `oscilab.session`."""
        written = session.save(self, path)
        self.log(f"Session saved to {written.name}", level="success")
        return written

    def load_session(self, path: str | Path, replace: bool = False) -> None:
        """
        Rebuild a saved setup in this window. It must still be empty — unless
        `replace`: then the current setup goes (see `clear`), but only once
        the session's producers have all been built, so a session that can't
        be loaded leaves the window as it was.
        """
        session.load(self, path, replace=replace)
        self.log(f"Session loaded from {Path(path).name}", level="success")

    def open_session(self, path: str | Path, replace: bool = False) -> "Oscilab":
        """
        Load a session into this window if it's still empty or `replace` is
        set (see `load_session`), otherwise into a new window, shown right
        away. Returns the window it's in.
        """
        if replace or not (self.workspace.signals or self.workspace.views):
            self.load_session(path, replace=replace)
            return self

        window = Oscilab(app=self.app)
        try:
            window.load_session(path)
        except Exception:
            window.close()
            raise
        Oscilab._extra_windows.add(window)  # Keeps it alive until it's closed
        window.show()
        return window

    def run(self) -> int:
        """Show the window and run the Qt event loop until it's closed. Returns the exit code."""
        self.show()
        return self.app.exec()

    def showEvent(self, event: qtg.QShowEvent | None) -> None:
        super().showEvent(event)
        # Plain close(), not splash.finish(self): finish() spins a nested event
        # loop until the window is drawn, running timers (refresh, producers'
        # queued data) re-entrantly in the middle of showing the window.
        if self.splash is not None:
            self.splash.close()

    def closeEvent(self, event: qtg.QCloseEvent | None) -> None:
        self.workspace.stop_producers()
        self.recording_ui.stop()  # After the producers: the last of their data gets saved too
        self._status_line.close()
        Oscilab._extra_windows.discard(self)
        super().closeEvent(event)

    def __refresh(self) -> None:
        timer = qtc.QElapsedTimer()
        timer.start()

        self.workspace.refresh()
        self.trigger_ui.update_status()

        progress = int((timer.elapsed() / self.refresh_timer.interval()) * 100)
        self.load_bar.setValue(min(progress, 100))

    def __start_application(self) -> qtw.QApplication:
        """The QApplication, when this window is the one starting it: styled, and logging to a file."""
        app = qtw.QApplication([])
        app.setApplicationName("Oscilab")  # Also names the per-user folders, e.g. for the log file
        app.setStyle("windows11")
        app.setStyleSheet(self.__load_qss("dark"))
        try:
            log_to_file()
        except OSError as e:  # A read-only profile, say: carry on without the file
            logger.warning(f"Can't write the log file: {e}")
        return app

    def __load_qss(self, theme: str) -> str:
        with resources.files("oscilab.assets").joinpath(f"style_{theme}.qss").open("r") as f:
            return f.read()

    def __set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self._status_line.theme = theme
        self.app.setStyleSheet(self.__load_qss(theme))
        self.workspace.set_theme(*self._PLOT_THEMES[theme])
        self.log(f"Theme set to {theme.capitalize()}")

    def __set_time_mode(self, mode: TimeMode) -> None:
        self.workspace.set_time_mode(mode)
        self.log(f"Time axis set to {mode.capitalize()}")

    def __reset_views(self) -> None:
        # Autofitting the view while data keeps streaming in would fight the
        # live scroll; only allow it while acquisition is paused. Resetting
        # itself must never touch the refresh timer / producers.
        if not self.start_stop_action.isChecked():
            self.log("Reset View is only available while paused", level="warning")
            return
        self.workspace.reset_views()

    def __show_about(self) -> None:
        try:
            version = metadata.version("oscilab")
        except metadata.PackageNotFoundError:
            version = "unknown (local development)"

        qtw.QMessageBox.about(
            self,
            "About Oscilab",
            f"""
            <h3>Oscilab</h3>

            Version: {version}<br>
            Log file: {log_file() or "off"}

            Signal visualization and analysis software.

            © 2026
            """,
        )

    def __start_stop(self, checked: bool) -> None:
        if checked:
            self.start_stop_action.setText("Stopped")
            self.start_stop_action.setIcon(dot_icon("red"))
            self.refresh_timer.stop()

        else:
            self.start_stop_action.setText("Running")
            self.start_stop_action.setIcon(dot_icon("green"))
            self.refresh_timer.start()

        # Reset View only makes sense while paused — while running, the next
        # refresh tick would immediately scroll/rescale past it anyway.
        self.reset_views_action.setEnabled(checked)

    def __set_fps(self, index: int) -> None:
        self._fps = FPS_CHOICES[index]
        # setInterval, not start(): changing FPS while paused mustn't resume
        self.refresh_timer.setInterval(1000 // self._fps)

    def __set_duration(self, text: str) -> None:
        duration = float(text.split(maxsplit=1)[0])  # "10 s" -> 10.0

        self.workspace.set_duration(duration)

    def __set_downsampling(self, checked: bool) -> None:
        # Green = full detail (nothing thinned, the default); red = downsampled
        # — same "green means everything's fully shown" convention as a
        # signal row's Visible/Hidden toggle.
        if checked:
            self.downsample_action.setText("Downsampled")
            self.downsample_action.setIcon(dot_icon("red"))
        else:
            self.downsample_action.setText("Full Detail")
            self.downsample_action.setIcon(dot_icon("green"))

        self.workspace.set_downsampling(checked)
