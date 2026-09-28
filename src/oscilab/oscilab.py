import ctypes
import sys
from collections.abc import Sequence
from importlib import metadata, resources
from typing import ClassVar

from loguru import logger
from PyQt6 import QtCore as qtc
from PyQt6 import QtGui as qtg
from PyQt6 import QtWidgets as qtw

from oscilab.math_signal import OPERATORS, MathDialog
from oscilab.producers import DataProducer
from oscilab.storage import Storage
from oscilab.workspace import Workspace


class Oscilab(qtw.QMainWindow):
    """
    Main window: chrome only (menu, toolbar, status bar, refresh loop).

    All signal/plot/producer bookkeeping lives in `self.workspace`
    (`oscilab.workspace.Workspace`).
    """

    # Exposed as `Oscilab.storage` / `oszi.storage` so callers write e.g.
    # `oszi.attach_producer(..., storage=oszi.storage.deque)` instead of a bare string.
    storage = Storage

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

        # Initialize QApplication
        if app is None:
            self.app = qtw.QApplication([])
            self.app.setStyle("windows11")
            self.app.setStyleSheet(self.__load_qss("dark"))
        else:
            self.app = app

        with resources.as_file(icon_path) as path:
            app_icon = qtg.QIcon(str(path))
        self.app.setWindowIcon(app_icon)

        super().__init__()
        self.setWindowIcon(app_icon)  # Also set on the window itself, for the title bar/taskbar

        # Splash Screen
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
        self.workspace = Workspace(log=self.log)

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
        self.refresh_timer.start(30)

        self.__build_menu_bar()
        self.__build_toolbar()  # After the menu bar: Start/Stop keeps View > Reset View in sync
        self.__build_status_bar()

        self.log("Oscilab ready")

    def __build_menu_bar(self):
        self.__build_file_menu()
        self.__build_view_menu()
        self.__build_math_menu()
        self.__build_cursors_menu()
        self.__build_help_menu()

    def __build_file_menu(self):
        file_menu = self.menuBar().addMenu("File")
        # Save - png
        png_action = qtg.QAction(".png", self)
        png_action.setToolTip("Export every plot as a PNG image (raster)")
        png_action.triggered.connect(self.__save_png)
        file_menu.addAction(png_action)
        # Save - svg
        svg_action = qtg.QAction(".svg", self)
        svg_action.setToolTip("Export every plot as an SVG image (vector)")
        svg_action.triggered.connect(self.__save_svg)
        file_menu.addAction(svg_action)
        # Save - pdf
        pdf_action = qtg.QAction(".pdf", self)
        pdf_action.setToolTip("Export every plot as a PDF (vector)")
        pdf_action.triggered.connect(self.__save_pdf)
        file_menu.addAction(pdf_action)
        # Save - csv
        csv_action = qtg.QAction(".csv", self)
        csv_action.setToolTip("Export every signal's data as CSV — one file per signal")
        csv_action.triggered.connect(self.__save_csv)
        file_menu.addAction(csv_action)
        # Save - mat
        mat_action = qtg.QAction(".mat", self)
        mat_action.setToolTip("Export every signal's data into one MATLAB .mat file")
        mat_action.triggered.connect(self.__save_mat)
        file_menu.addAction(mat_action)
        # Save - h5
        h5_action = qtg.QAction(".h5", self)
        h5_action.setToolTip("Export every signal's data into one HDF5 .h5 file")
        h5_action.triggered.connect(self.__save_h5)
        file_menu.addAction(h5_action)

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

    def __build_math_menu(self):
        math_menu = self.menuBar().addMenu("Math")
        math_menu.menuAction().setToolTip("Create a new signal computed live from two existing ones")
        for operator in OPERATORS:
            math_action = qtg.QAction(operator, self)
            math_action.setToolTip(f"Create a new signal as A {operator} B, picked from a dialog")
            math_action.triggered.connect(lambda _checked=False, op=operator: self.__open_math_dialog(op))
            math_menu.addAction(math_action)

    def __build_cursors_menu(self):
        # One checkable entry per timeseries plot, added as
        # add_timeseries_plot() creates them (see there).
        self.cursors_menu = self.menuBar().addMenu("Cursors")
        self.cursors_menu.menuAction().setToolTip("Toggle classic two-cursor time/value measurements on a plot")
        self._cursor_actions: dict[str, qtg.QAction] = {}

    def __build_help_menu(self):
        help_menu = self.menuBar().addMenu("Help")
        # About Oscilab
        about_action = qtg.QAction("About Oscilab", self)
        about_action.setToolTip("Show the installed Oscilab version")
        about_action.triggered.connect(self.__show_about)
        help_menu.addAction(about_action)
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
        self.toolbar.addSeparator()
        # Duration dropdown — how many seconds of history are kept/displayed
        duration_box = qtw.QComboBox()
        duration_box.addItems([f"{s} s" for s in (5, 10, 15, 20, 30, 45, 60)])
        duration_box.setCurrentText("10 s")
        duration_box.setToolTip("How many seconds of history each signal keeps and displays")
        duration_box.currentTextChanged.connect(self.__set_duration)
        self.toolbar.addWidget(duration_box)
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
        self._cursor_actions[title] = cursor_action

        return plot

    def add_spectrogram_plot(self, label: str, nperseg: int = 256, noverlap: int = 128):
        """Add a live STFT spectrogram for an already-registered signal."""
        spectrogram, controller = self.workspace.add_spectrogram(label, nperseg=nperseg, noverlap=noverlap)
        self.plot_layout.addWidget(spectrogram)
        self.__insert_plot_controller(controller)
        return spectrogram

    def __insert_plot_controller(self, controller: qtw.QWidget):
        """
        Add a per-plot "Plot:" panel above the flat "Signals:" list, which
        stays pinned to the bottom of the sidebar no matter how many plots
        get added, or in what order.
        """
        self.controller_layout.insertWidget(self.controller_layout.count() - 1, controller)

    def reg_signal(self, label: str, fs: float, storage: Storage = Storage.ring):
        """
        Register a signal you feed yourself (producer-fed signals are
        registered by `attach_producer`). `storage`: `self.storage.ring`
        (default) or `self.storage.deque` — see `oscilab.ring_buffer.RingBuffer`.
        """
        return self.workspace.reg_signal(label, fs, storage=storage)

    def link(self, plot: str, label: str):
        self.workspace.link(plot, label)
        self.log(f"Linked signal '{label}' to plot '{plot}'")

    def attach_producer(self, producer: DataProducer, signals: str | Sequence[str], storage: Storage = Storage.ring):
        """
        Register one signal per producer channel, named by `signals` (a single
        label for a single-channel producer), and start the producer feeding
        them. The signals use the producer's `fs`.
        """
        return self.workspace.attach_producer(producer, signals, storage=storage)

    def log(self, message: str, level: str = "info", timeout_ms: int = 4000):
        """Central logging: routes to loguru and the status bar at once."""
        getattr(logger, level, logger.info)(message)
        if hasattr(self, "statusBar"):
            self.statusBar().showMessage(message, timeout_ms)

    def show(self):
        super().show()
        self.splash.finish(self)
        self.app.exec()

    def closeEvent(self, event):
        self.workspace.stop_producers()
        super().closeEvent(event)

    def __refresh(self):
        timer = qtc.QElapsedTimer()
        timer.start()

        self.workspace.refresh()

        progress = int((timer.elapsed() / self.refresh_timer.interval()) * 100)
        self.load_bar.setValue(min(progress, 100))

    def __save_png(self):
        if not self.workspace.plots and not self.workspace.spectrograms:
            self.log("No plots to export", level="warning")
            return

        path, _ = qtw.QFileDialog.getSaveFileName(self, "Save Plots as PNG", "", "PNG Image (*.png)")
        if not path:
            return
        self.workspace.export_png(path)

    def __save_svg(self):
        if not self.workspace.plots and not self.workspace.spectrograms:
            self.log("No plots to export", level="warning")
            return

        path, _ = qtw.QFileDialog.getSaveFileName(self, "Save Plots as SVG", "", "SVG Image (*.svg)")
        if not path:
            return
        self.workspace.export_svg(path)

    def __save_pdf(self):
        if not self.workspace.plots and not self.workspace.spectrograms:
            self.log("No plots to export", level="warning")
            return

        path, _ = qtw.QFileDialog.getSaveFileName(self, "Save Plots as PDF", "", "PDF File (*.pdf)")
        if not path:
            return
        self.workspace.export_pdf(path)

    def __save_csv(self):
        if not self.workspace.signals:
            self.log("No signals to export", level="warning")
            return

        path, _ = qtw.QFileDialog.getSaveFileName(self, "Save Signals as CSV", "", "CSV File (*.csv)")
        if not path:
            return
        self.workspace.export_csv(path)

    def __save_mat(self):
        if not self.workspace.signals:
            self.log("No signals to export", level="warning")
            return

        path, _ = qtw.QFileDialog.getSaveFileName(self, "Save Signals as MATLAB .mat", "", "MATLAB File (*.mat)")
        if not path:
            return
        self.workspace.export_mat(path)

    def __save_h5(self):
        if not self.workspace.signals:
            self.log("No signals to export", level="warning")
            return

        path, _ = qtw.QFileDialog.getSaveFileName(self, "Save Signals as HDF5 .h5", "", "HDF5 File (*.h5)")
        if not path:
            return
        self.workspace.export_h5(path)

    def __load_qss(self, theme: str) -> str:
        with resources.files("oscilab.assets").joinpath(f"style_{theme}.qss").open("r") as f:
            return f.read()

    def __set_theme(self, theme: str):
        self.app.setStyleSheet(self.__load_qss(theme))
        self.workspace.set_theme(*self._PLOT_THEMES[theme])
        self.log(f"Theme set to {theme.capitalize()}")

    def __set_time_mode(self, mode: str):
        self.workspace.set_time_mode(mode)
        self.log(f"Time axis set to {mode.capitalize()}")

    def __set_cursors(self, plot: str, enabled: bool):
        self.workspace.set_cursors(plot, enabled)

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

            Version: {version}

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
        fps = int(text.split()[0])  # "30 FPS" -> 30

        self.refresh_timer.start(1000 // fps)

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
