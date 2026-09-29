import ctypes
import functools
import itertools
import sys
from collections.abc import Callable, Sequence
from importlib import metadata, resources
from pathlib import Path
from typing import ClassVar, Literal, NamedTuple

from loguru import logger
from PyQt6 import QtCore as qtc
from PyQt6 import QtGui as qtg
from PyQt6 import QtWidgets as qtw

from oscilab import session
from oscilab.dialogs import MathDialog, PlotDialog, SpectrogramDialog
from oscilab.log import StatusLine, log_file, log_to_file
from oscilab.math_signal import OPERATORS
from oscilab.producers import DataProducer
from oscilab.storage import Storage
from oscilab.toolbar import ToolbarItem, toolbar_items
from oscilab.workspace import Workspace


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


def _signals_caption(labels: list[str]) -> str:
    """Names a producer's Producers-menu entry by its signals, kept short for many channels."""
    return ", ".join(labels) if len(labels) <= 3 else f"{labels[0]} … {labels[-1]}"


class Oscilab(qtw.QMainWindow):
    """
    Main window: chrome only (menu, toolbar, status bar, refresh loop).

    All signal/plot/producer bookkeeping lives in `self.workspace`
    (`oscilab.workspace.Workspace`).
    """

    # Exposed as `Oscilab.storage` / `oszi.storage` so callers write e.g.
    # `oszi.attach_producer(..., storage=oszi.storage.deque)` instead of a bare string.
    storage = Storage

    _extra_windows: ClassVar[set["Oscilab"]] = set()  # Opened by open_session(); see there

    # (plot background, plot foreground) per theme — the QSS covers widget
    # chrome, but pyqtgraph's canvas colors are set programmatically.
    _PLOT_THEMES: ClassVar[dict[str, tuple[str, str]]] = {
        "dark": ("#202f46", "#ffffff"),
        "light": ("#f5f5f5", "#1a1a1a"),
    }

    def __init__(self, app=None):
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
        app = app or qtw.QApplication.instance()
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
        self._theme = "dark"  # Matches the QSS loaded above and Workspace's default plot colors
        window_id = next(_window_ids)
        self.logger = logger.bind(window=window_id)
        self._status_line = StatusLine(self.statusBar(), window_id, theme=self._theme)

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
        self._fps = 30

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

        self.__build_menu_bar()
        self.__build_toolbar()  # After the menu bar: Start/Stop keeps View > Reset View in sync
        self.__build_status_bar()

        self.log("Oscilab ready")

    def __build_menu_bar(self):
        self.__build_file_menu()
        self.__build_view_menu()
        self.__build_plots_menu()
        self.__build_math_menu()
        self.__build_producers_menu()
        self.__build_cursors_menu()
        self.__build_help_menu()

    def __build_file_menu(self):
        file_menu = self.menuBar().addMenu("File")
        for export in _EXPORTS:
            action = qtg.QAction(export.suffix, self)
            action.setToolTip(export.tooltip)
            action.triggered.connect(lambda _checked=False, e=export: self.__export(e))
            file_menu.addAction(action)

        file_menu.addSeparator()
        open_action = qtg.QAction("Open Session...", self)
        open_action.setToolTip(
            "Rebuild a saved setup: producers, plots and settings (in a new window if this one is in use)"
        )
        open_action.triggered.connect(self.__open_session_dialog)
        file_menu.addAction(open_action)
        save_action = qtg.QAction("Save Session...", self)
        save_action.setToolTip("Save this setup — producers, plots and settings, not the data — as JSON")
        save_action.triggered.connect(self.__save_session_dialog)
        file_menu.addAction(save_action)

    def __build_view_menu(self):
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
        for mode, action_label in (("global", "Global"), ("relative", "Relative")):
            time_action = qtg.QAction(action_label, self)
            time_action.setCheckable(True)
            time_action.setChecked(mode == "global")  # matches Workspace's default
            time_action.setToolTip(time_tooltips[mode])
            time_action.triggered.connect(lambda _checked=False, m=mode: self.__set_time_mode(m))
            self._time_action_group.addAction(time_action)
            time_menu.addAction(time_action)

    def __build_plots_menu(self):
        plots_menu = self.menuBar().addMenu("Plots")
        plots_menu.menuAction().setToolTip("Add plots; each plot's panel can change its signals or remove it")
        add_plot = qtg.QAction("Add Time Series Plot...", self)
        add_plot.setToolTip("A new scrolling plot, showing the signals you pick")
        add_plot.triggered.connect(self.__add_plot_dialog)
        plots_menu.addAction(add_plot)
        add_spectrogram = qtg.QAction("Add Spectrogram...", self)
        add_spectrogram.setToolTip("A live STFT spectrogram of one signal")
        add_spectrogram.triggered.connect(self.__add_spectrogram_dialog)
        plots_menu.addAction(add_spectrogram)

    def __build_math_menu(self):
        math_menu = self.menuBar().addMenu("Math")
        math_menu.menuAction().setToolTip("Create a new signal computed live from two existing ones")
        for operator in OPERATORS:
            math_action = qtg.QAction(operator, self)
            math_action.setToolTip(f"Create a new signal as A {operator} B, picked from a dialog")
            math_action.triggered.connect(lambda _checked=False, op=operator: self.__open_math_dialog(op))
            math_menu.addAction(math_action)

    def __build_producers_menu(self):
        # One submenu per attached producer, added by attach_producer()
        self.producers_menu = self.menuBar().addMenu("Producers")
        self.producers_menu.menuAction().setToolTip("Start and stop the attached producers, and run their functions")
        self.producers_menu.setToolTipsVisible(True)
        self._no_producers_action = self.producers_menu.addAction("No producers attached")
        self._no_producers_action.setEnabled(False)
        self._producer_menus: list[tuple[str, qtw.QMenu, qtg.QAction, qtg.QAction]] = []
        self.producers_menu.aboutToShow.connect(self.__update_producer_menus)

    def __build_cursors_menu(self):
        # One checkable entry per timeseries plot, added as
        # add_timeseries_plot() creates them (see there).
        self.cursors_menu = self.menuBar().addMenu("Cursors")
        self.cursors_menu.menuAction().setToolTip("Toggle classic two-cursor time/value measurements on a plot")

    def __build_help_menu(self):
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

    def __build_toolbar(self):
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
        fps_box.addItems(["5 FPS", "10 FPS", "30 FPS", "60 FPS"])
        fps_box.setCurrentText("30 FPS")
        fps_box.setToolTip("How often the plots redraw per second")
        fps_box.currentTextChanged.connect(self.__set_fps)
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

    def __build_status_bar(self):
        self.statusBar().setSizeGripEnabled(False)  # Disables the resize diagonal gripper lines in the corner
        load_label = qtw.QLabel("Load [%]:")
        load_tooltip = "Percent of the refresh interval spent redrawing on the last tick"
        load_label.setToolTip(load_tooltip)
        self.statusBar().addPermanentWidget(load_label)
        self.load_bar = qtw.QProgressBar()
        self.load_bar.setRange(0, 100)
        self.load_bar.setToolTip(load_tooltip)
        self.statusBar().addPermanentWidget(self.load_bar)

    def add_timeseries_plot(self, title: str):
        plot, controller = self.workspace.add_plot(title)
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
        return plot

    def add_spectrogram_plot(self, label: str, nperseg: int = 256, noverlap: int = 128):
        """Add a live STFT spectrogram for an already-registered signal."""
        spectrogram, controller = self.workspace.add_spectrogram(label, nperseg=nperseg, noverlap=noverlap)
        self.plot_layout.addWidget(spectrogram)
        self.__insert_plot_controller(controller)
        self.__add_remove_button(controller, label)
        return spectrogram

    def __add_remove_button(self, controller, title: str):
        controller.add_header_button("Remove", "Remove this plot; its signals stay", lambda: self.remove_plot(title))

    def remove_plot(self, title: str):
        """Remove a plot (time series or spectrogram); its signals stay."""
        self.workspace.remove_plot(title)
        for action in self.cursors_menu.actions():
            if action.text() == title:
                self.cursors_menu.removeAction(action)
                action.deleteLater()

    def unlink(self, plot: str, label: str):
        """Take a signal off a time-series plot."""
        self.workspace.unlink(plot, label)
        self.log(f"Removed signal '{label}' from plot '{plot}'")

    def __insert_plot_controller(self, controller: qtw.QWidget):
        """
        Add a per-plot "Plot:" panel above the flat "Signals:" list, which
        stays pinned to the bottom of the sidebar no matter how many plots
        get added, or in what order.
        """
        self.controller_layout.insertWidget(self.controller_layout.count() - 1, controller)

    def register_signal(self, label: str, fs: float, storage: Storage = Storage.ring):
        """
        Register a signal you feed yourself (producer-fed signals are
        registered by `attach_producer`). `storage`: `self.storage.ring`
        (default) or `self.storage.deque` — see `oscilab.ring_buffer.RingBuffer`.
        """
        return self.workspace.register_signal(label, fs, storage=storage)

    def link(self, plot: str, label: str):
        self.workspace.link(plot, label)
        self.log(f"Linked signal '{label}' to plot '{plot}'")

    def attach_producer(self, producer: DataProducer, signals: str | Sequence[str], storage: Storage = Storage.ring):
        """
        Register one signal per producer channel, named by `signals` (a single
        label for a single-channel producer), and start the producer feeding
        them. The signals use the producer's `fs`. It gets an entry in the
        Producers menu — Start, Stop, and its `@button` / `@toggle` methods,
        which run on its own thread; those marked `toolbar=True` go in the
        toolbar too (see `oscilab.toolbar`).
        """
        models = self.workspace.attach_producer(producer, signals, storage=storage)
        self.__add_producer_menu(producer)
        return models

    def __add_producer_menu(self, producer: DataProducer):
        if self._no_producers_action is not None:
            self.producers_menu.removeAction(self._no_producers_action)
            self._no_producers_action = None

        labels = self.workspace.signals_fed_by(producer)
        caption = f"{type(producer).__name__}: {_signals_caption(labels)}"
        menu = self.producers_menu.addMenu(caption)
        menu.setToolTipsVisible(True)
        menu.menuAction().setToolTip(f"Feeds {', '.join(labels)}")
        start = menu.addAction("Start")
        start.setToolTip("Start producing again (after Stop, or an error)")
        start.triggered.connect(lambda: self.__producer_command(self.workspace.start_producer, labels[0]))
        stop = menu.addAction("Stop")
        stop.setToolTip("Stop producing; the data received so far stays")
        stop.triggered.connect(lambda: self.__producer_command(self.workspace.stop_producer, labels[0]))

        items = toolbar_items(producer, required=False)
        if items:
            menu.addSeparator()
        in_toolbar = []
        for item, method in items:
            run = functools.partial(self.__run_on_producer, producer, method)
            action = self.__control_action(item, run)
            menu.addAction(action)
            if item.toolbar:  # The same action: a toggle shows the same state in both places
                action.setToolTip(f"{action.toolTip()} ({caption})")  # Which producer's, in the toolbar
                in_toolbar.append(action)
        if in_toolbar:
            self.toolbar.addActions(in_toolbar)
            self.toolbar.addSeparator()

        self._producer_menus.append((labels[0], menu, start, stop))
        self.__update_producer_menus()

    def __update_producer_menus(self):
        """Start/Stop enabled, and a dot for the state: running green, stopped gray, error red."""
        colors = {"running": "green", "stopped": "gray", "error": "red"}
        for label, menu, start, stop in self._producer_menus:
            state = self.workspace.producer_state(label)
            menu.menuAction().setIcon(self._dot_icon(colors[state]))
            start.setEnabled(state != "running")
            stop.setEnabled(state == "running")

    def __producer_command(self, command: Callable[[str], None], label: str):
        self.__run_tool(command.__name__, command, label)
        self.__update_producer_menus()

    def add_toolbar(self, tools) -> list[qtg.QAction]:
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
        actions = [self.__control_action(item, method) for item, method in toolbar_items(tools)]
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
        self.toolbar.addAction(action)
        return action

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
    def __run_on_producer(producer: DataProducer, method: Callable, *args):
        """Queue a producer's control method onto its own thread, between reads."""
        if not producer.queue_action(functools.partial(method, *args)):
            raise RuntimeError(f"{type(producer).__name__} isn't running")

    def __run_tool(self, name: str, callback: Callable, *args) -> bool:
        """Run a custom toolbar callback. An exception is reported (status bar, log file), never fatal."""
        try:
            callback(*args)
        except Exception as e:  # noqa: BLE001 — raised in a Qt slot, it would abort the whole app
            self.logger.opt(exception=e).error(f"'{name}' failed: {type(e).__name__}: {e}")
            return False
        return True

    def __run_toggle(self, action: qtg.QAction, callback: Callable[[bool], object], on: bool):
        if self.__run_tool(action.text(), callback, on):
            action.setIcon(self._dot_icon("green" if on else "gray"))
        else:
            with qtc.QSignalBlocker(action):
                action.setChecked(not on)  # It didn't take: show the state it's still in

    def log(self, message: str, level: str = "info"):
        """
        Log through this window's `logger`: to the console, the log file and
        this window's status bar. `level`: "debug", "info", "success",
        "warning", "error" or "critical".
        """
        self.logger.opt(depth=1).log(level.upper(), message)

    @property
    def theme(self) -> str:
        return self._theme

    @property
    def fps(self) -> int:
        return self._fps

    # The setters below go through the menus/toolbar, so those always show
    # what's in effect — same as if the user had picked it there.

    def set_theme(self, theme: str):
        """`"dark"` or `"light"`."""
        if theme not in self._PLOT_THEMES:
            raise ValueError(f"Unknown theme {theme!r}; expected one of {list(self._PLOT_THEMES)}")
        for action in self._theme_action_group.actions():
            action.setChecked(action.text() == theme.capitalize())
        self.__set_theme(theme)

    def set_fps(self, fps: int):
        """Redraw rate: one of the toolbar's choices (5, 10, 30, 60)."""
        text = f"{fps} FPS"
        if self._fps_box.findText(text) < 0:
            choices = [self._fps_box.itemText(i) for i in range(self._fps_box.count())]
            raise ValueError(f"fps must be one of {choices}, got {fps}")
        self._fps_box.setCurrentText(text)

    def set_duration(self, duration: float):
        """Seconds of history kept and shown per signal; any value, not just the toolbar's choices."""
        text = f"{duration:g} s"
        if self._duration_box.findText(text) < 0:
            self._duration_box.addItem(text)
        self._duration_box.setCurrentText(text)

    def set_time_mode(self, mode: str):
        """`"global"` (time as recorded) or `"relative"` (newest sample at 0)."""
        if mode not in ("global", "relative"):
            raise ValueError(f"Unknown time mode {mode!r}")
        for action in self._time_action_group.actions():
            action.setChecked(action.text().lower() == mode)
        self.__set_time_mode(mode)

    def set_downsampling(self, enabled: bool):
        self.downsample_action.setChecked(enabled)

    def set_cursors(self, plot: str, enabled: bool):
        """Two-cursor measurement on a time-series plot, as in the Cursors menu."""
        action = next((a for a in self.cursors_menu.actions() if a.text() == plot), None)
        if action is None:
            raise ValueError(f"Unknown time-series plot '{plot}'")
        action.setChecked(enabled)

    def save_session(self, path) -> Path:
        """Save this window's setup as JSON — see `oscilab.session`."""
        path = session.save(self, path)
        self.log(f"Session saved to {path.name}", level="success")
        return path

    def load_session(self, path):
        """Rebuild a saved setup in this window, which must still be empty."""
        session.load(self, path)
        self.log(f"Session loaded from {Path(path).name}", level="success")

    def open_session(self, path) -> "Oscilab":
        """
        Load a session into this window if it's still empty, otherwise into
        a new window (shown right away). Returns the window it's in.
        """
        if not (self.workspace.signals or self.workspace.views):
            self.load_session(path)
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

    def showEvent(self, event):
        super().showEvent(event)
        # Plain close(), not splash.finish(self): finish() spins a nested event
        # loop until the window is drawn, running timers (refresh, producers'
        # queued data) re-entrantly in the middle of showing the window.
        if self.splash is not None:
            self.splash.close()

    def closeEvent(self, event):
        self.workspace.stop_producers()
        self._status_line.close()
        Oscilab._extra_windows.discard(self)
        super().closeEvent(event)

    def __refresh(self):
        timer = qtc.QElapsedTimer()
        timer.start()

        self.workspace.refresh()

        progress = int((timer.elapsed() / self.refresh_timer.interval()) * 100)
        self.load_bar.setValue(min(progress, 100))

    def __export(self, export: _Export):
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

    def __save_session_dialog(self):
        path, _ = qtw.QFileDialog.getSaveFileName(self, "Save Session", "", "Oscilab session (*.json)")
        if path:
            try:
                self.save_session(path)
            except Exception as e:  # noqa: BLE001 — reported, never fatal (this is a Qt slot)
                self.__session_error("save", e)

    def __open_session_dialog(self):
        path, _ = qtw.QFileDialog.getOpenFileName(self, "Open Session", "", "Oscilab session (*.json)")
        if path:
            try:
                self.open_session(path)
            except Exception as e:  # noqa: BLE001 — a bad file or a device that won't open: report it
                self.__session_error("open", e)

    def __session_error(self, verb: str, error: Exception):
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

    def __set_theme(self, theme: str):
        self._theme = theme
        self._status_line.theme = theme
        self.app.setStyleSheet(self.__load_qss(theme))
        self.workspace.set_theme(*self._PLOT_THEMES[theme])
        self.log(f"Theme set to {theme.capitalize()}")

    def __set_time_mode(self, mode: str):
        self.workspace.set_time_mode(mode)
        self.log(f"Time axis set to {mode.capitalize()}")

    def __set_cursors(self, plot: str, enabled: bool):
        self.workspace.set_cursors(plot, enabled)

    def __add_plot_dialog(self):
        titles = self.workspace.views
        default = next(f"Plot {n}" for n in range(1, len(titles) + 2) if f"Plot {n}" not in titles)
        dialog = PlotDialog(signal_labels=list(self.workspace.signals), title=default, parent=self)
        if dialog.exec() != qtw.QDialog.DialogCode.Accepted:
            return
        title, labels = dialog.get_values()
        if not title:
            self.log("A plot needs a title", level="warning")
            return
        if title in titles:
            self.log(f"Plot '{title}' already exists", level="warning")
            return
        self.add_timeseries_plot(title)
        for label in labels:
            self.link(plot=title, label=label)

    def __add_spectrogram_dialog(self):
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

    def __edit_plot_signals(self, title: str):
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

    def __open_math_dialog(self, operator: str):
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

    def __reset_views(self):
        # Autofitting the view while data keeps streaming in would fight the
        # live scroll; only allow it while acquisition is paused. Resetting
        # itself must never touch the refresh timer / producers.
        if not self.start_stop_action.isChecked():
            self.log("Reset View is only available while paused", level="warning")
            return
        self.workspace.reset_views()

    def __show_about(self):
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

    def __start_stop(self, checked):
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

    def __set_fps(self, text):
        self._fps = int(text.split()[0])  # "30 FPS" -> 30
        # setInterval, not start(): changing FPS while paused mustn't resume
        self.refresh_timer.setInterval(1000 // self._fps)

    def __set_duration(self, text):
        duration = float(text.split()[0])  # "10 s" -> 10.0

        self.workspace.set_duration(duration)

    def __set_downsampling(self, checked: bool):
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
