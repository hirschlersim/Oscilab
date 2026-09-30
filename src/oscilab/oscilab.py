import ctypes
import functools
import itertools
import sys
from collections.abc import Callable, Mapping, Sequence
from importlib import metadata, resources
from pathlib import Path
from typing import ClassVar, Literal, NamedTuple

from loguru import logger
from PyQt6 import QtCore as qtc
from PyQt6 import QtGui as qtg
from PyQt6 import QtWidgets as qtw

from oscilab import session
from oscilab.dialogs import (
    HistogramDialog,
    MathDialog,
    PlotDialog,
    ReplayDialog,
    SpectrogramDialog,
    StreamDialog,
    XYDialog,
)
from oscilab.display import FPS_CHOICES, TIME_MODES, Fps, Theme, TimeMode
from oscilab.histogram_plot import HistogramPlot
from oscilab.log import StatusLine, log_file, log_to_file
from oscilab.logic_plot import LogicPlot
from oscilab.math_signal import OPERATORS
from oscilab.producers import DataProducer
from oscilab.recorder import Recorder
from oscilab.replay import RecordingReplayProducer, recorded_signals, recording_files, replay_labels
from oscilab.signal_controller import SignalController
from oscilab.signal_model import SignalModel
from oscilab.spectrogram_plot import SpectrogramPlot
from oscilab.storage import Storage
from oscilab.timeseries_plot import TimeseriesPlot
from oscilab.toolbar import ToolbarItem, toolbar_items
from oscilab.trigger import TIME_PER_DIV, TriggerSettings, autoset
from oscilab.trigger_panel import TriggerPanel
from oscilab.workspace import Workspace, signals_caption
from oscilab.xy_plot import XYPlot


class _ProducerEntry(NamedTuple):
    """What the window shows for one attached producer."""

    label: str  # One of the signals it feeds, to ask the workspace about it
    menu: qtw.QMenu  # Its Producers submenu
    start: qtg.QAction
    stop: qtg.QAction
    functions: list[qtg.QAction]  # Its @button / @toggle methods, some also in the toolbar
    toolbar_separator: qtg.QAction | None  # After its toolbar controls, if it has any
    toggles: dict[str, qtg.QAction]  # By method name


class _Export(NamedTuple):
    """One File-menu export format."""

    suffix: str  # Menu entry, e.g. ".png"
    tooltip: str
    file_filter: str  # QFileDialog filter, e.g. "PNG Image (*.png)"
    content: Literal["plots", "signals"]  # What gets exported (and must exist)
    write: Callable[[Workspace, str], object]  # The Workspace method that writes it


_EXPORTS = (
    _Export(
        ".png",
        "Export every plot as a PNG image (raster)",
        "PNG Image (*.png)",
        "plots",
        Workspace.export_png,
    ),
    _Export(
        ".svg",
        "Export every plot as an SVG image (vector)",
        "SVG Image (*.svg)",
        "plots",
        Workspace.export_svg,
    ),
    _Export(
        ".pdf",
        "Export every plot as a PDF (vector)",
        "PDF File (*.pdf)",
        "plots",
        Workspace.export_pdf,
    ),
    _Export(
        ".csv",
        "Export every signal's data as CSV — one file per signal",
        "CSV File (*.csv)",
        "signals",
        Workspace.export_csv,
    ),
    _Export(
        ".mat",
        "Export every signal's data into one MATLAB .mat file",
        "MATLAB File (*.mat)",
        "signals",
        Workspace.export_mat,
    ),
    _Export(
        ".h5",
        "Export every signal's data into one HDF5 .h5 file",
        "HDF5 File (*.h5)",
        "signals",
        Workspace.export_h5,
    ),
)


_window_ids = itertools.count(1)  # Tells each window's log messages apart (see oscilab.log)


class Oscilab(qtw.QMainWindow):
    """
    Main window: chrome only (menu, toolbar, status bar, refresh loop).

    All signal/plot/producer bookkeeping lives in `self.workspace`
    (`oscilab.workspace.Workspace`).
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
        self.splash: qtw.QSplashScreen | None = None
        if starting_app:
            with resources.as_file(icon_path) as path:
                pixmap = qtg.QPixmap(str(path))
            pixmap = pixmap.scaled(
                pixmap.width() // 2,
                pixmap.height() // 2,
                qtc.Qt.AspectRatioMode.KeepAspectRatio,
                qtc.Qt.TransformationMode.SmoothTransformation,
            )
            self.splash = qtw.QSplashScreen(pixmap)
            self.splash.show()

        # Main Properties
        self.setWindowTitle("Oscilab")
        self.resize(1920 // 3, 1080 // 3)
        self.move(50, 50)

        # Domain layer
        self.workspace = Workspace(logger=self.logger)
        self._fps: Fps = 30

        # Custom toggles, for sessions (see `toggle_states`)
        self._toggles: dict[str, qtg.QAction] = {}
        self._pending_toggles: dict[str, bool] = {}  # Set before their toolbar was added

        # Layout
        self.controller_layout = qtw.QVBoxLayout()
        self.controller_layout.setAlignment(qtc.Qt.AlignmentFlag.AlignTop)
        self.plot_layout = qtw.QVBoxLayout()
        self.plot_layout.setAlignment(qtc.Qt.AlignmentFlag.AlignTop)
        # Combine in "Master"
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

        self.__build_stream_actions()  # Before the menu bar and toolbar, which both show them
        self.__build_trigger()

        self.__build_menu_bar()
        self.__build_toolbar()  # After the menu bar: Start/Stop keeps View > Reset View in sync
        self.__build_status_bar()

        self.log("Oscilab ready")

    def __build_menu_bar(self) -> None:
        self.__build_file_menu()
        self.__build_view_menu()
        self.__build_plots_menu()
        self.__build_math_menu()
        self.__build_producers_menu()
        self.__build_cursors_menu()
        self.__build_help_menu()

    def __build_stream_actions(self) -> None:
        """Save Stream's toggle and settings — in the toolbar, with a drop-down, and the File menu."""
        documents = qtc.QStandardPaths.writableLocation(qtc.QStandardPaths.StandardLocation.DocumentsLocation)
        self._recording_path = Path(documents) / "Oscilab" / "recording.h5"
        self._recording_max_mb: float | None = 100.0
        self._recorder: Recorder | None = None  # While recording (see oscilab.recorder)
        self.stream_action = qtg.QAction("Save Stream", self)
        self.stream_action.setCheckable(True)
        self.stream_action.setIcon(self._dot_icon("gray"))
        self.stream_action.setToolTip(
            "While on, record every signal to HDF5 files on disk — every sample, not just what the plots keep.\n"
            "Where to, and how big each file gets: Save Stream Settings, in its drop-down"
        )
        self.stream_action.toggled.connect(self.__set_recording)
        self.stream_settings_action = qtg.QAction("Save Stream Settings...", self)
        self.stream_settings_action.setToolTip("Where the stream is saved, and the size at which it starts a new file")
        self.stream_settings_action.triggered.connect(self.__stream_settings_dialog)
        self.stream_folder_action = qtg.QAction("Open Stream Folder", self)
        self.stream_folder_action.setToolTip("Open the folder the stream is saved in")
        self.stream_folder_action.triggered.connect(self.__open_stream_folder)
        self.replay_action = qtg.QAction("Replay Recording...", self)
        self.replay_action.setToolTip("Play a Save Stream recording back — a file, or a folder of them — looped or not")
        self.replay_action.triggered.connect(self.__replay_dialog)
        self._stream_timer = qtc.QTimer(self)  # Its own: the refresh loop stops while paused, recording doesn't
        self._stream_timer.setInterval(500)
        self._stream_timer.timeout.connect(self.__update_stream_status)

    def __build_file_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        for export in _EXPORTS:
            action = qtg.QAction(export.suffix, self)
            action.setToolTip(export.tooltip)
            action.triggered.connect(lambda _checked=False, e=export: self.__export(e))
            file_menu.addAction(action)

        file_menu.addSeparator()
        file_menu.addActions(
            [self.stream_action, self.stream_settings_action, self.stream_folder_action, self.replay_action]
        )

        file_menu.addSeparator()
        open_action = qtg.QAction("Open Session...", self)
        open_action.setToolTip(
            "Rebuild a saved setup: producers, plots and settings — in place of this one, or in a new window"
        )
        open_action.triggered.connect(self.__open_session_dialog)
        file_menu.addAction(open_action)
        save_action = qtg.QAction("Save Session...", self)
        save_action.setToolTip("Save this setup — producers, plots and settings, not the data — as JSON")
        save_action.triggered.connect(self.__save_session_dialog)
        file_menu.addAction(save_action)

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

    def __build_plots_menu(self) -> None:
        plots_menu = self.menuBar().addMenu("Plots")
        plots_menu.menuAction().setToolTip("Add plots; each plot's panel can change its signals or remove it")
        entries = (
            ("Add Time Series Plot...", "A new scrolling plot, showing the signals you pick", self.__add_plot_dialog),
            (
                "Add Logic Plot...",
                "On/off signals (buttons, digital lines) in a lane each, like a logic analyzer",
                self.__add_logic_plot_dialog,
            ),
            ("Add XY Plot...", "One signal against another: a joystick, a Lissajous figure", self.__add_xy_dialog),
            ("Add Histogram...", "How one signal's values are distributed", self.__add_histogram_dialog),
            ("Add Spectrogram...", "A live STFT spectrogram of one signal", self.__add_spectrogram_dialog),
        )
        for text, tooltip, open_dialog in entries:
            action = qtg.QAction(text, self)
            action.setToolTip(tooltip)
            action.triggered.connect(open_dialog)
            plots_menu.addAction(action)

    def __build_math_menu(self) -> None:
        math_menu = self.menuBar().addMenu("Math")
        math_menu.menuAction().setToolTip("Create a new signal computed live from two existing ones")
        for operator in OPERATORS:
            math_action = qtg.QAction(operator, self)
            math_action.setToolTip(f"Create a new signal as A {operator} B, picked from a dialog")
            math_action.triggered.connect(lambda _checked=False, op=operator: self.__open_math_dialog(op))
            math_menu.addAction(math_action)

    def __build_producers_menu(self) -> None:
        # One submenu per attached producer, added by attach_producer()
        self.producers_menu = self.menuBar().addMenu("Producers")
        self.producers_menu.menuAction().setToolTip("Start and stop the attached producers, and run their functions")
        self.producers_menu.setToolTipsVisible(True)
        self._no_producers_action: qtg.QAction | None = None
        self.__show_no_producers(True)
        self._producer_entries: dict[DataProducer, _ProducerEntry] = {}
        self.producers_menu.aboutToShow.connect(self.__update_producer_menus)

    def __show_no_producers(self, show: bool) -> None:
        """The disabled "No producers attached" placeholder in the Producers menu."""
        if show and self._no_producers_action is None:
            self._no_producers_action = self.producers_menu.addAction("No producers attached")
            self._no_producers_action.setEnabled(False)
        elif not show and self._no_producers_action is not None:
            self.producers_menu.removeAction(self._no_producers_action)
            self._no_producers_action = None

    def __build_cursors_menu(self) -> None:
        # One checkable entry per timeseries plot, added as
        # add_timeseries_plot() creates them (see there).
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
        for action, entries in (
            (self.stream_action, [self.stream_settings_action, self.stream_folder_action]),
            (self.trigger_action, self._trigger_commands),
        ):
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
        self._stream_label = qtw.QLabel()  # "● Saving: ..." while the stream's being saved
        self._stream_label.setToolTip("Save Stream is recording to this file")
        self._stream_label.hide()
        self.statusBar().addPermanentWidget(self._stream_label)
        load_label = qtw.QLabel("Load [%]:")
        load_tooltip = "Percent of the refresh interval spent redrawing on the last tick"
        load_label.setToolTip(load_tooltip)
        self.statusBar().addPermanentWidget(load_label)
        self.load_bar = qtw.QProgressBar()
        self.load_bar.setRange(0, 100)
        self.load_bar.setToolTip(load_tooltip)
        self.statusBar().addPermanentWidget(self.load_bar)

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
        self.plot_layout.addWidget(plot)
        self.__insert_plot_controller(controller)

        cursor_action = qtg.QAction(title, self)
        cursor_action.setCheckable(True)
        cursor_action.setToolTip(f"Toggle measurement cursors on '{title}'")
        cursor_action.toggled.connect(lambda checked, t=title: self.__set_cursors(t, checked))
        self.cursors_menu.addAction(cursor_action)

        controller.add_header_button(
            "Signals...", "Choose which signals this plot shows", lambda: self.__edit_plot_signals(title)
        )
        self.__add_remove_button(controller, title)

    def add_xy_plot(self, title: str, x: str, y: str, trail: float | None = None) -> XYPlot:
        """`y` against `x`, the last `trail` seconds of them or (None) all they hold (see `oscilab.xy_plot`)."""
        plot, controller = self.workspace.add_xy_plot(title, x, y, trail=trail)
        self.plot_layout.addWidget(plot)
        self.__insert_plot_controller(controller)
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
        self.plot_layout.addWidget(plot)
        self.__insert_plot_controller(controller)
        self.__add_remove_button(controller, title)
        return plot

    def add_spectrogram_plot(self, label: str, nperseg: int = 256, noverlap: int = 128) -> SpectrogramPlot:
        """Add a live STFT spectrogram for an already-registered signal."""
        spectrogram, controller = self.workspace.add_spectrogram(label, nperseg=nperseg, noverlap=noverlap)
        self.plot_layout.addWidget(spectrogram)
        self.__insert_plot_controller(controller)
        self.__add_remove_button(controller, label)
        return spectrogram

    def __add_remove_button(self, controller: SignalController, title: str) -> None:
        controller.add_header_button("Remove", "Remove this plot; its signals stay", lambda: self.remove_plot(title))

    def remove_plot(self, title: str) -> None:
        """Remove a plot (time series or spectrogram); its signals stay."""
        self.workspace.remove_plot(title)
        for action in self.cursors_menu.actions():
            if action.text() == title:
                self.cursors_menu.removeAction(action)
                action.deleteLater()

    def unlink(self, plot: str, label: str) -> None:
        """Take a signal off a time-series plot."""
        self.workspace.unlink(plot, label)
        self.log(f"Removed signal '{label}' from plot '{plot}'")

    def __insert_plot_controller(self, controller: qtw.QWidget) -> None:
        """
        Add a per-plot "Plot:" panel above the flat "Signals:" list, which
        stays pinned to the bottom of the sidebar no matter how many plots
        get added, or in what order.
        """
        self.controller_layout.insertWidget(self.controller_layout.count() - 1, controller)

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

    def link(self, plot: str, label: str) -> None:
        self.workspace.link(plot, label)
        self.log(f"Linked signal '{label}' to plot '{plot}'")

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
        self.__add_producer_menu(producer)
        return models

    def __add_producer_menu(self, producer: DataProducer) -> None:
        self.__show_no_producers(False)
        labels = self.workspace.signals_fed_by(producer)
        caption = f"{type(producer).__name__}: {signals_caption(labels)}"
        menu = qtw.QMenu(caption, self.producers_menu)
        self.producers_menu.addMenu(menu)
        menu.setToolTipsVisible(True)
        menu.menuAction().setToolTip(f"Feeds {', '.join(labels)}")
        start = qtg.QAction("Start", menu)
        start.setToolTip("Start producing again (after Stop, or an error)")
        start.triggered.connect(lambda: self.__producer_command(self.workspace.start_producer, labels[0]))
        stop = qtg.QAction("Stop", menu)
        stop.setToolTip("Stop producing; the data received so far stays")
        stop.triggered.connect(lambda: self.__producer_command(self.workspace.stop_producer, labels[0]))
        remove = qtg.QAction("Remove", menu)
        remove.setToolTip("Stop it, and remove it along with the signals it feeds")
        remove.triggered.connect(lambda: self.__producer_command(self.detach_producer, labels[0]))
        menu.addActions([start, stop, remove])

        items = toolbar_items(producer, required=False)
        if items:
            menu.addSeparator()
        functions, in_toolbar = [], []
        toggles: dict[str, qtg.QAction] = {}
        for item, method in items:
            run = functools.partial(self.__run_on_producer, producer, method)
            action = self.__control_action(item, run)
            menu.addAction(action)
            functions.append(action)
            if item.kind == "toggle":
                toggles[item.name] = action
            if item.toolbar:  # The same action: a toggle shows the same state in both places
                action.setToolTip(f"{action.toolTip()} ({caption})")  # Which producer's, in the toolbar
                in_toolbar.append(action)
        separator = None
        if in_toolbar:
            self.toolbar.addActions(in_toolbar)
            separator = self.toolbar.addSeparator()

        self._producer_entries[producer] = _ProducerEntry(labels[0], menu, start, stop, functions, separator, toggles)
        self.__update_producer_menus()

    def __remove_producer_menu(self, producer: DataProducer) -> None:
        entry = self._producer_entries.pop(producer)
        self.producers_menu.removeAction(entry.menu.menuAction())
        entry.menu.deleteLater()
        for action in entry.functions:
            self.toolbar.removeAction(action)
            action.deleteLater()
        if entry.toolbar_separator is not None:
            self.toolbar.removeAction(entry.toolbar_separator)
        self.__show_no_producers(not self._producer_entries)

    def __update_producer_menus(self) -> None:
        """Start/Stop enabled, and a dot for the state: running green, stopped gray, error red."""
        colors = {"running": "green", "stopped": "gray", "error": "red"}
        for entry in self._producer_entries.values():
            state = self.workspace.producer_state(entry.label)
            entry.menu.menuAction().setIcon(self._dot_icon(colors[state]))
            entry.start.setEnabled(state != "running")
            entry.stop.setEnabled(state == "running")

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
            self.__remove_producer_menu(producer)
        return labels

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

    def __producer_command(self, command: Callable[[str], object], label: str) -> None:
        self.__run_tool(command.__name__, command, label)
        self.__update_producer_menus()

    def add_toolbar(self, tools: object) -> list[qtg.QAction]:
        """
        Put the `@button` / `@toggle` methods of `tools` — an instance of your
        own class, or the class itself if they're classmethods — in the
        toolbar, after the built-in controls. See `oscilab.toolbar`.
        """
        if isinstance(tools, DataProducer):
            # Its methods would run on the GUI thread, racing its own reads
            raise TypeError(
                f"{type(tools).__name__} is a producer: mark its methods toolbar=True instead,"
                " and they run on its own thread"
            )
        cls = tools if isinstance(tools, type) else type(tools)
        actions = []
        for item, method in toolbar_items(tools):
            action = self.__control_action(item, method)
            if item.kind == "toggle":
                self.__register_toggle(f"{cls.__name__}.{item.name}", action)
            actions.append(action)
        self.toolbar.addActions(actions)
        self.toolbar.addSeparator()  # Keeps each class's controls together
        return actions

    def add_toolbar_button(self, text: str, callback: Callable[[], object], tooltip: str = "") -> qtg.QAction:
        """A toolbar button calling `callback()` — e.g. a method of your own class — when clicked."""
        action = self.__control_action(ToolbarItem("button", text, tooltip), callback)
        self.toolbar.addAction(action)
        return action

    def add_toolbar_toggle(
        self, text: str, callback: Callable[[bool], object], checked: bool = False, tooltip: str = ""
    ) -> qtg.QAction:
        """
        A toolbar toggle calling `callback(on)` with its new state whenever it
        changes. `checked`: the initial state, which isn't reported to `callback`.
        """
        action = self.__control_action(ToolbarItem("toggle", text, tooltip, checked), callback)
        self.__register_toggle(text, action)
        self.toolbar.addAction(action)
        return action

    def __register_toggle(self, key: str, action: qtg.QAction) -> None:
        # A second instance of the same class gets "Class.method#2", ...
        unique, n = key, 1
        while unique in self._toggles:
            n += 1
            unique = f"{key}#{n}"
        self._toggles[unique] = action
        if unique in self._pending_toggles:
            action.setChecked(self._pending_toggles.pop(unique))  # Runs its method, as a click would

    def toggle_states(self) -> dict[str, bool]:
        """
        Whether each of your toolbar toggles is on — from `add_toolbar`, keyed
        `"ClassName.method"`, or `add_toolbar_toggle`, keyed by its text.
        Sessions save these (a producer's toggles are saved with it).
        """
        return {**self._pending_toggles, **{key: action.isChecked() for key, action in self._toggles.items()}}

    def set_toggle_states(self, states: Mapping[str, bool]) -> None:
        """
        Switch your toolbar toggles on or off (keyed as `toggle_states` has
        them), calling their methods as a click would. A toggle that isn't in
        the toolbar yet switches as soon as `add_toolbar` adds it — so a
        session can be loaded before your tools are.
        """
        for key, on in states.items():
            if key in self._toggles:
                self._toggles[key].setChecked(on)
            else:
                self._pending_toggles[key] = on

    def producer_toggle_states(self, producer: DataProducer) -> dict[str, bool]:
        """Whether each of `producer`'s `@toggle` functions is on, keyed by method name."""
        entry = self._producer_entries.get(producer)
        return {name: action.isChecked() for name, action in entry.toggles.items()} if entry else {}

    def set_producer_toggle_states(self, producer: DataProducer, states: Mapping[str, bool]) -> None:
        """
        Switch `producer`'s `@toggle` functions (keyed by method name) on or
        off, running them on its thread as a click would. Names it doesn't
        have are skipped with a warning.
        """
        entry = self._producer_entries.get(producer)
        toggles = entry.toggles if entry else {}
        for name, on in states.items():
            if name in toggles:
                toggles[name].setChecked(on)
            else:
                self.log(f"{type(producer).__name__} has no toggle '{name}'", level="warning")

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

    def __control_action(self, item: ToolbarItem, callback: Callable) -> qtg.QAction:
        """
        The QAction for a `@button` / `@toggle` control, wherever it goes. A
        toggle shows its state as a green/gray dot in the toolbar, and as a
        check mark in a menu.
        """
        action = qtg.QAction(item.text, self)
        action.setToolTip(item.tooltip or item.text)
        if item.kind == "button":
            action.triggered.connect(lambda _checked=False: self.__run_tool(item.text, callback))
        else:
            action.setCheckable(True)
            action.setChecked(item.checked)
            action.setIcon(self._dot_icon("green" if item.checked else "gray"))
            action.setIconVisibleInMenu(False)  # So a menu shows its check mark instead
            action.toggled.connect(lambda on: self.__run_toggle(action, callback, on))
        return action

    @staticmethod
    def __run_on_producer(producer: DataProducer, method: Callable[..., object], *args: object) -> None:
        """Queue a producer's control method onto its own thread, between reads."""
        if not producer.queue_action(functools.partial(method, *args)):
            raise RuntimeError(f"{type(producer).__name__} isn't running")

    def __run_tool(self, name: str, callback: Callable[..., object], *args: object) -> bool:
        """Run a custom toolbar callback. An exception is reported (status bar, log file), never fatal."""
        try:
            callback(*args)
        except Exception as e:  # noqa: BLE001 — raised in a Qt slot, it would abort the whole app
            self.logger.opt(exception=e).error(f"'{name}' failed: {type(e).__name__}: {e}")
            return False
        return True

    def __run_toggle(self, action: qtg.QAction, callback: Callable[[bool], object], on: bool) -> None:
        if self.__run_tool(action.text(), callback, on):
            action.setIcon(self._dot_icon("green" if on else "gray"))
        else:
            with qtc.QSignalBlocker(action):
                action.setChecked(not on)  # It didn't take: show the state it's still in

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

    def set_cursors(self, plot: str, enabled: bool) -> None:
        """Two-cursor measurement on a time-series plot, as in the Cursors menu."""
        action = next((a for a in self.cursors_menu.actions() if a.text() == plot), None)
        if action is None:
            raise ValueError(f"Unknown time-series plot '{plot}'")
        action.setChecked(enabled)

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
        self.__stop_recording()  # After the producers: the last of their data gets saved too
        self._status_line.close()
        Oscilab._extra_windows.discard(self)
        super().closeEvent(event)

    def __refresh(self) -> None:
        timer = qtc.QElapsedTimer()
        timer.start()

        self.workspace.refresh()
        self.__update_trigger_status()

        progress = int((timer.elapsed() / self.refresh_timer.interval()) * 100)
        self.load_bar.setValue(min(progress, 100))

    def __export(self, export: _Export) -> None:
        workspace = self.workspace
        has_content = workspace.views if export.content == "plots" else workspace.signals
        if not has_content:
            self.log(f"No {export.content} to export", level="warning")
            return

        path, _ = qtw.QFileDialog.getSaveFileName(
            self, f"Save {export.content.capitalize()} as {export.suffix}", "", export.file_filter
        )
        if path:
            export.write(workspace, path)

    # --- Trigger ---------------------------------------------------------------------

    def __build_trigger(self) -> None:
        """The Trigger toggle (the toolbar; its commands in the drop-down) and its panel, atop the sidebar."""
        self.trigger_action = qtg.QAction("Trigger", self)
        self.trigger_action.setCheckable(True)
        self.trigger_action.setIcon(self._dot_icon("gray"))
        self.trigger_action.setToolTip(
            "Trigger the time plots, as a scope does: each shows the record around a trigger, held until the next.\n"
            "Its settings: the Trigger panel; Run, Single, Stop and Force: the drop-down"
        )
        self.trigger_action.toggled.connect(self.__set_triggering)
        self._trigger_commands = []
        for text, tip, command in (
            ("Run", "Acquire record after record", "run"),
            ("Single", "Acquire one record, then stop", "single"),
            ("Stop", "Stop acquiring; the record on show stays", "stop"),
            ("Force Trigger", "Take a record now, trigger or not", "force"),
            ("Autoset", "Level and timebase from the source signal", "autoset"),
        ):
            action = qtg.QAction(text, self)
            action.setToolTip(tip)
            action.triggered.connect(lambda _checked=False, c=command: self.__trigger_command(c))
            self._trigger_commands.append(action)

        self.trigger_panel = TriggerPanel()
        self.trigger_panel.hide()
        self.controller_layout.insertWidget(0, self.trigger_panel)  # Atop the sidebar, above the plots' panels
        self.trigger_panel.changed.connect(self.__trigger_edited)
        for signal, command in (
            (self.trigger_panel.run_requested, "run"),
            (self.trigger_panel.single_requested, "single"),
            (self.trigger_panel.stop_requested, "stop"),
            (self.trigger_panel.force_requested, "force"),
            (self.trigger_panel.autoset_requested, "autoset"),
        ):
            signal.connect(lambda c=command: self.__trigger_command(c))
        self.workspace.trigger_changed = self.__trigger_moved

    @property
    def trigger_settings(self) -> TriggerSettings | None:
        """The trigger's settings while triggering is on, else None."""
        acquisition = self.workspace.trigger
        return acquisition.settings if acquisition is not None else None

    def set_trigger(self, settings: TriggerSettings | None) -> None:
        """
        Trigger the time plots on `settings` — as the Trigger button and
        panel do (see `oscilab.trigger`) — or, with None, go back to
        scrolling. Run, Single, Stop and Force: `workspace.trigger`.
        """
        self.workspace.set_trigger(settings)
        with qtc.QSignalBlocker(self.trigger_action):
            self.trigger_action.setChecked(settings is not None)
        self.trigger_panel.setVisible(settings is not None)
        if settings is not None:
            self.trigger_panel.set_signals(list(self.workspace.signals))
            self.trigger_panel.set_settings(settings)
            self.trigger_panel.set_unit(self.workspace.signals[settings.source].unit)
        self.__update_trigger_status()

    def __set_triggering(self, on: bool) -> None:
        if not on:
            self.set_trigger(None)
            self.log("Trigger off: the plots scroll again")
            return
        source = self.__default_trigger_source()
        if source is None:
            self.log("No signals to trigger on", level="warning")
            with qtc.QSignalBlocker(self.trigger_action):
                self.trigger_action.setChecked(False)
            return
        self.set_trigger(self.__autoset(source))
        self.log(f"Triggering on '{source}'")

    def __default_trigger_source(self) -> str | None:
        """The first signal a time plot shows — or, with none shown, the first signal."""
        for plot in self.workspace.plots.values():
            if plot.signals:
                return plot.signals[0]
        return next(iter(self.workspace.signals), None)

    def __autoset(self, source: str) -> TriggerSettings:
        """Auto Setup on `source`, the record kept to what the buffers hold."""
        settings = autoset(source, *self.workspace.signals[source].snapshot())
        fitting = [step for step in TIME_PER_DIV if 10 * step <= self.workspace.duration]
        if fitting and settings.time_per_div > fitting[-1]:
            settings.time_per_div = fitting[-1]
        return settings

    def __trigger_edited(self) -> None:
        settings = self.trigger_panel.settings()
        try:
            self.workspace.set_trigger(settings)
        except ValueError as e:  # E.g. a window upside down, or a record longer than the buffers
            self.log(str(e), level="warning")
            return
        self.trigger_panel.set_unit(self.workspace.signals[settings.source].unit)

    def __trigger_moved(self) -> None:
        """A level line was dragged on a plot: the panel shows the new level."""
        if self.workspace.trigger is not None:
            self.trigger_panel.set_settings(self.workspace.trigger.settings)

    def __trigger_command(self, command: str) -> None:
        acquisition = self.workspace.trigger
        if acquisition is None:
            self.log("Turn the trigger on first", level="warning")
            return
        if command == "autoset":
            self.set_trigger(self.__autoset(acquisition.settings.source))
        elif command == "run":
            acquisition.run()
        elif command == "single":
            acquisition.single_shot()
        elif command == "stop":
            acquisition.stop()
        else:
            acquisition.force()
        self.__update_trigger_status()

    def __update_trigger_status(self) -> None:
        """The acquisition's status on the toolbar button and in the panel."""
        acquisition = self.workspace.trigger
        if acquisition is None:
            if self.trigger_action.isChecked():  # Turned off by the workspace: its source went
                self.set_trigger(None)
            self.trigger_action.setText("Trigger")
            self.trigger_action.setIcon(self._dot_icon("gray"))
            return
        text, color = acquisition.status()
        self.trigger_action.setText(f"Trigger: {text}")
        self.trigger_action.setIcon(self._dot_icon(color))
        self.trigger_panel.set_status(text, color)
        self.trigger_panel.set_signals(list(self.workspace.signals))

    # --- Save Stream ----------------------------------------------------------------

    @property
    def recording(self) -> bool:
        """Whether Save Stream is on."""
        return self._recorder is not None

    def set_recording(self, on: bool) -> None:
        """Start or stop saving the stream, as the Save Stream button does (see `oscilab.recorder`)."""
        self.stream_action.setChecked(on)

    @property
    def recording_options(self) -> tuple[Path, float | None]:
        """Save Stream's base file path, and the size in MB its files reach before the next (None: no limit)."""
        return self._recording_path, self._recording_max_mb

    def set_recording_options(self, path: str | Path, max_file_mb: float | None = 100.0) -> None:
        """
        Where Save Stream records to — each recording's files are named after
        `path` and the time it started — and the size in MB at which it moves
        on to a new file (None: no limit). Applies from the next recording.
        """
        if max_file_mb is not None and max_file_mb <= 0:
            raise ValueError(f"max_file_mb must be positive (or None for no limit), got {max_file_mb}")
        self._recording_path = Path(path)
        self._recording_max_mb = max_file_mb

    def __set_recording(self, on: bool) -> None:
        if on:
            self.__start_recording()
        else:
            self.__stop_recording()

    def __start_recording(self) -> None:
        ws = self.workspace
        models = {label: model for label, model in ws.signals.items() if label not in ws.math_signals}
        max_bytes = round(self._recording_max_mb * 1_000_000) if self._recording_max_mb else None
        try:
            recorder = Recorder(models, self._recording_path, max_file_bytes=max_bytes)
            first = recorder.start()
        except (ValueError, OSError) as e:  # No signals, or a path that can't be written: report, don't raise
            self.log(f"Couldn't save the stream: {e}", level="error")
            with qtc.QSignalBlocker(self.stream_action):
                self.stream_action.setChecked(False)
            return
        self._recorder = recorder
        self.stream_action.setIcon(self._dot_icon("red"))
        self._stream_timer.start()
        self.__update_stream_status()
        self.log(f"Saving the stream of {len(models)} signal(s) to {first.name}")

    def __stop_recording(self) -> None:
        recorder = self._recorder
        if recorder is None:
            return
        self._recorder = None
        files = recorder.stop()
        self._stream_timer.stop()
        self._stream_label.hide()
        with qtc.QSignalBlocker(self.stream_action):  # Also when a write error ended it
            self.stream_action.setChecked(False)
        self.stream_action.setIcon(self._dot_icon("gray"))
        if recorder.error is not None:
            error = recorder.error
            self.logger.opt(exception=error).error(f"Saving the stream stopped: {type(error).__name__}: {error}")
        else:
            self.log(f"Stream saved: {len(files)} file(s) in {files[0].parent}", level="success")

    def __update_stream_status(self) -> None:
        recorder = self._recorder
        if recorder is None:
            return
        if recorder.error is not None:
            self.__stop_recording()
            return
        size_mb = recorder.current_size / 1_000_000
        self._stream_label.setText(
            f"<span style='color: #e5484d'>●</span> Saving: {recorder.files[-1].name} ({size_mb:.1f} MB)"
        )
        self._stream_label.show()

    def __stream_settings_dialog(self) -> None:
        path, max_mb = self.recording_options
        dialog = StreamDialog(str(path), max_mb, parent=self)
        if dialog.exec() != qtw.QDialog.DialogCode.Accepted:
            return
        new_path, new_max_mb = dialog.get_values()
        if not new_path:
            self.log("Save Stream needs a file to save to", level="warning")
            return
        self.set_recording_options(new_path, new_max_mb)
        limit = f"a new file every {new_max_mb:g} MB" if new_max_mb else "no size limit"
        self.log(f"Save Stream: to {new_path}, {limit}")

    def __open_stream_folder(self) -> None:
        folder = self._recording_path.parent
        folder.mkdir(parents=True, exist_ok=True)
        qtg.QDesktopServices.openUrl(qtc.QUrl.fromLocalFile(str(folder)))

    def replay_recording(self, path: str | Path, signals: Sequence[str] | None = None, loop: bool = False) -> list[str]:
        """
        Replay a Save Stream recording — a file, or a folder of them, played
        in name order — with one `RecordingReplayProducer` per signal (every
        one by default), and show them in a new plot. With `loop`, after the
        last file it starts again from the first. Each keeps its recorded
        name, with " (replay)" added where that's taken. Returns their names.
        """
        chosen = recorded_signals(path) if signals is None else list(signals)
        if not chosen:
            raise ValueError(f"No signals to replay from {path}")
        producers = [RecordingReplayProducer(path, label, loop=loop) for label in chosen]  # All, before attaching any
        names = replay_labels(list(self.workspace.signals), chosen)
        for producer, name in zip(producers, names, strict=True):
            self.attach_producer(producer, name)

        title, n = f"Replay: {Path(path).name}", 2
        while title in self.workspace.views:
            title, n = f"Replay: {Path(path).name} ({n})", n + 1
        self.add_timeseries_plot(title)
        for name in names:
            self.link(plot=title, label=name)
        self.log(f"Replaying {len(names)} signal(s) from {Path(path).name}{', looped' if loop else ''}")
        return names

    def __replay_dialog(self) -> None:
        def scan(path: str) -> tuple[list[str], list[str]]:
            return [file.name for file in recording_files(path)], recorded_signals(path)

        dialog = ReplayDialog(str(self.recording_options[0].parent), scan, parent=self)
        if dialog.exec() != qtw.QDialog.DialogCode.Accepted:
            return
        path, signals, loop = dialog.get_values()
        if not signals:
            self.log("No signals ticked to replay", level="warning")
            return
        try:
            self.replay_recording(path, signals, loop=loop)
        except (ValueError, OSError) as e:  # E.g. a file being recorded to right now: report it, don't raise
            self.log(f"Couldn't replay {path}: {e}", level="error")

    def __save_session_dialog(self) -> None:
        path, _ = qtw.QFileDialog.getSaveFileName(self, "Save Session", "", "Oscilab session (*.json)")
        if path:
            try:
                self.save_session(path)
            except Exception as e:  # noqa: BLE001 — reported, never fatal (this is a Qt slot)
                self.__session_error("save", e)

    def __open_session_dialog(self) -> None:
        path, _ = qtw.QFileDialog.getOpenFileName(self, "Open Session", "", "Oscilab session (*.json)")
        if not path:
            return
        replace = False
        if self.workspace.signals or self.workspace.views:
            choice = self.__ask_replace_or_new_window()
            if choice is None:
                return
            replace = choice
        try:
            self.open_session(path, replace=replace)
        except Exception as e:  # noqa: BLE001 — a bad file or a device that won't open: report it
            self.__session_error("open", e)

    def __ask_replace_or_new_window(self) -> bool | None:
        """For a session opened in a window in use: True to replace its setup, False for a new window, None to cancel."""
        box = qtw.QMessageBox(self)
        box.setIcon(qtw.QMessageBox.Icon.Question)
        box.setWindowTitle("Open Session")
        box.setText("This window is in use. Replace its setup with the session, or open it in a new window?")
        box.setInformativeText("Replacing stops and removes the current producers, signals and plots.")
        replace = box.addButton("Replace", qtw.QMessageBox.ButtonRole.AcceptRole)
        new_window = box.addButton("New Window", qtw.QMessageBox.ButtonRole.ActionRole)
        box.addButton(qtw.QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(replace)
        box.exec()
        clicked = box.clickedButton()
        if clicked is replace:
            return True
        if clicked is new_window:
            return False
        return None

    def __session_error(self, verb: str, error: Exception) -> None:
        self.log(f"Couldn't {verb} session: {error}", level="error")
        qtw.QMessageBox.warning(self, f"Couldn't {verb} session", str(error))

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

    def __set_cursors(self, plot: str, enabled: bool) -> None:
        self.workspace.set_cursors(plot, enabled)

    def __add_plot_dialog(self) -> None:
        self.__add_time_plot_dialog("Plot", "Time Series Plot", self.add_timeseries_plot)

    def __add_logic_plot_dialog(self) -> None:
        self.__add_time_plot_dialog("Logic", "Logic Plot", self.add_logic_plot)

    def __add_time_plot_dialog(self, name: str, kind: str, add: Callable[[str], TimeseriesPlot]) -> None:
        dialog = PlotDialog(
            signal_labels=list(self.workspace.signals), title=self.__free_title(name), parent=self, kind=kind
        )
        if dialog.exec() != qtw.QDialog.DialogCode.Accepted:
            return
        title, labels = dialog.get_values()
        if not self.__title_is_free(title):
            return
        add(title)
        for label in labels:
            self.link(plot=title, label=label)

    def __free_title(self, name: str) -> str:
        """`name 1`, `name 2`, ...: the first no plot has."""
        titles = self.workspace.views
        return next(f"{name} {n}" for n in range(1, len(titles) + 2) if f"{name} {n}" not in titles)

    def __title_is_free(self, title: str) -> bool:
        """Whether a plot can take `title`; says why not, if it can't."""
        if not title:
            self.log("A plot needs a title", level="warning")
            return False
        if title in self.workspace.views:
            self.log(f"Plot '{title}' already exists", level="warning")
            return False
        return True

    def __add_xy_dialog(self) -> None:
        labels = list(self.workspace.signals)
        if len(labels) < 2:
            self.log("An XY plot needs two signals", level="warning")
            return
        dialog = XYDialog(signal_labels=labels, title=self.__free_title("XY"), parent=self)
        if dialog.exec() != qtw.QDialog.DialogCode.Accepted:
            return
        title, x, y, trail = dialog.get_values()
        if not self.__title_is_free(title):
            return
        try:
            self.add_xy_plot(title, x, y, trail=trail)
        except ValueError as e:  # The same signal twice: say so (this is a Qt slot)
            self.log(str(e), level="warning")

    def __add_histogram_dialog(self) -> None:
        if not self.workspace.signals:
            self.log("No signals yet", level="warning")
            return
        dialog = HistogramDialog(signal_labels=list(self.workspace.signals), parent=self)
        if dialog.exec() != qtw.QDialog.DialogCode.Accepted:
            return
        label, bins, value_range = dialog.get_values()
        if label is None:
            return
        title, n = f"Histogram: {label}", 2
        while title in self.workspace.views:  # Another of the same signal
            title, n = f"Histogram: {label} ({n})", n + 1
        try:
            self.add_histogram(label, bins=bins, value_range=value_range, title=title)
        except ValueError as e:  # A range from high to low: say so
            self.log(str(e), level="warning")

    def __add_spectrogram_dialog(self) -> None:
        # A spectrogram is keyed by its signal's name, so a signal can have one
        candidates = [label for label in self.workspace.signals if label not in self.workspace.views]
        if not candidates:
            message = "Every signal already has a spectrogram" if self.workspace.signals else "No signals yet"
            self.log(message, level="warning")
            return
        dialog = SpectrogramDialog(signal_labels=candidates, parent=self)
        if dialog.exec() != qtw.QDialog.DialogCode.Accepted:
            return
        label, nperseg = dialog.get_values()
        self.add_spectrogram_plot(label, nperseg=nperseg, noverlap=nperseg // 2)

    def __edit_plot_signals(self, title: str) -> None:
        shown = list(self.workspace.plots[title].curves)
        dialog = PlotDialog(
            signal_labels=list(self.workspace.signals), shown=shown, title=title, title_editable=False, parent=self
        )
        if dialog.exec() != qtw.QDialog.DialogCode.Accepted:
            return
        _, wanted = dialog.get_values()
        for label in shown:
            if label not in wanted:
                self.unlink(plot=title, label=label)
        for label in wanted:
            if label not in shown:
                self.link(plot=title, label=label)

    def __open_math_dialog(self, operator: str) -> None:
        signal_labels = list(self.workspace.signals.keys())
        plot_titles = list(self.workspace.plots.keys())
        if len(signal_labels) < 2:
            self.log("Need at least two signals to do math", level="warning")
            return
        if not plot_titles:
            self.log("Add a plot before creating a math signal", level="warning")
            return

        dialog = MathDialog(operator=operator, signal_labels=signal_labels, plot_titles=plot_titles, parent=self)
        if dialog.exec() != qtw.QDialog.DialogCode.Accepted:
            return

        signal_a, signal_b, label, plot = dialog.get_values()
        if not label:
            self.log("Math signal needs a name", level="warning")
            return
        if label in self.workspace.signals:
            self.log(f"Signal '{label}' already exists", level="warning")
            return

        self.workspace.add_math_signal(label=label, operator=operator, signal_a=signal_a, signal_b=signal_b)
        self.workspace.link(plot=plot, label=label)

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

    @staticmethod
    def _dot_icon(color: str) -> qtg.QIcon:
        pixmap = qtg.QPixmap(24, 24)
        pixmap.fill(qtg.QColor("transparent"))
        painter = qtg.QPainter(pixmap)
        painter.setBrush(qtg.QColor(color))
        painter.setPen(qtg.QColor(color))
        painter.drawEllipse(2, 2, 20, 20)
        painter.end()
        return qtg.QIcon(pixmap)

    def __start_stop(self, checked: bool) -> None:
        if checked:
            self.start_stop_action.setText("Stopped")
            self.start_stop_action.setIcon(self._dot_icon("red"))
            self.refresh_timer.stop()

        else:
            self.start_stop_action.setText("Running")
            self.start_stop_action.setIcon(self._dot_icon("green"))
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
            self.downsample_action.setIcon(self._dot_icon("red"))
        else:
            self.downsample_action.setText("Full Detail")
            self.downsample_action.setIcon(self._dot_icon("green"))

        self.workspace.set_downsampling(checked)
