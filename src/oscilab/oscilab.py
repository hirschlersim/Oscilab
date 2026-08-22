import ctypes
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Oscilab")  # Tell Windows to treat this script as a distinct application identifier

from PyQt6 import QtWidgets as qtw
from PyQt6 import QtGui as qtg
from PyQt6 import QtCore as qtc

from oscilab.timeseries_plot import TimeseriesPlot
from oscilab.signal_controller import SignalController
from oscilab.signal_model import SignalModel

from loguru import logger
from importlib import resources, metadata
import numpy as np



class Oscilab(qtw.QMainWindow):
    def __init__(self, app=None):
        # Initialize QApplication
        if app is None:
            self.app = qtw.QApplication([])
            self.app.setStyle('windows11')
            self.app.setWindowIcon(qtg.QIcon("icon.png"))
            with resources.files("oscilab.assets").joinpath("style.qss").open("r") as f:
                self.app.setStyleSheet(f.read())
        else:
            self.app = app
        super().__init__()

        # Splash Screen
        splash_path = resources.files("oscilab.assets").joinpath("icon.png")
        with resources.as_file(splash_path) as path:
            pixmap = qtg.QPixmap(str(path))
        pixmap = pixmap.scaled(pixmap.width() // 2, pixmap.height() // 2,
                               qtc.Qt.AspectRatioMode.KeepAspectRatio,
                               qtc.Qt.TransformationMode.SmoothTransformation)
        self.splash = qtw.QSplashScreen(pixmap)
        self.splash.show()

        # Main Properties
        self.setWindowTitle("Oscilab")
        self.resize(1920 // 3, 1080 // 3)
        self.move(50, 50)

        # Variabels
        self.signals = {}  # Label -> SignalModel
        self.plots = []  # Index -> Plot
        self.controller = []  # Index -> Controller
        self.color_bg = "#202f46"
        self.color_fg = '#ffffff'

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

        # Timer / Update loop
        self.refresh_timer = qtc.QTimer()
        self.refresh_timer.timeout.connect(self.__refresh)
        self.refresh_timer.start(30)

        # Menu bar
            # File
        file_menu = self.menuBar().addMenu("File")
                # Save - png
        png_action = qtg.QAction(".png", self)
        png_action.triggered.connect(lambda: print("save png"))
        png_action.setShortcut("Ctrl+S")
        file_menu.addAction(png_action)
                # Save - csv
        csv_action = qtg.QAction(".csv", self)
        csv_action.triggered.connect(lambda: print("save csv"))
        csv_action.setShortcut("Ctrl+D")
        file_menu.addAction(csv_action)
            # View
        view_menu = self.menuBar().addMenu("View")
                # Reset View's
        reset_views_action = qtg.QAction("Reset View", self)
        reset_views_action.triggered.connect(self.__reset_views)
        view_menu.addAction(reset_views_action)
            # Help
        help_menu = self.menuBar().addMenu("Help")
                # About Oscilab
        about_action = qtg.QAction("About Oscilab", self)
        about_action.triggered.connect(self.__show_about)
        help_menu.addAction(about_action)
                # About QT
        about_qt_action = qtg.QAction("About Qt", self)
        about_qt_action.triggered.connect(qtw.QApplication.aboutQt)
        help_menu.addAction(about_qt_action)

        # Toolbar
        self.toolbar = self.addToolBar("Controls")
        self.toolbar.setToolButtonStyle(qtc.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
            # Start-Stop Display
        self.start_stop_action = qtg.QAction(self)
        self.start_stop_action.setCheckable(True)
        self.__start_stop(checked=False)
        self.start_stop_action.toggled.connect(self.__start_stop)
        self.toolbar.addAction(self.start_stop_action)
        self.toolbar.addSeparator()
            # FPS dropdown
        fps_box = qtw.QComboBox()
        fps_box.addItems(["5 FPS", "10 FPS", "30 FPS", "60 FPS"])
        fps_box.setCurrentText("30 FPS")
        fps_box.currentTextChanged.connect(self.__set_fps)
        self.toolbar.addWidget(fps_box)
        self.toolbar.addSeparator()

        # Status bar
        self.statusBar().setSizeGripEnabled(False)  # Disables the resize diagonal gripper lines in the corner
        self.statusBar().addPermanentWidget(qtw.QLabel("Load [%]:"))
        self.load_bar = qtw.QProgressBar()
        self.load_bar.setRange(0, 100)
        self.statusBar().addPermanentWidget(self.load_bar)

        self.statusBar().showMessage("Saved successfully", 3000)  # TODO this should be the Main loggin function


    def addTimeSeriesPlot(self, title:str=None):
        plo = TimeseriesPlot(_title=title)
        con = SignalController()

        self.plots.append(plo)
        self.controller.append(con)
        self.plot_layout.addWidget(plo)
        self.controller_layout.addWidget(con)


    def regSignal(self, label:str, fs:float):
        assert label not in self.signals
        self.signals[label] = SignalModel(_label=label, _fs=fs)


    def link(self, subplot, label):
        subplot = subplot-1
        assert 0<=subplot and subplot<len(self.plots)

        self.plots[subplot].add_signal(label=label, model=self.signals[label])
        self.controller[subplot].add_signal(label=label, model=self.signals[label])
        

    def show(self):
        super().show()
        self.splash.finish(self)
        self.app.exec()


    def __refresh(self):
        timer = qtc.QElapsedTimer()
        timer.start()

        # for plot in self.plots:  # TODO update
        #     plot.update()

        progress = int((timer.elapsed() / self.refresh_timer.interval()) * 100)
        self.load_bar.setValue(progress)

        print(f"{timer.elapsed()} / {self.refresh_timer.interval()}ms")


    def __reset_views(self):
        for plot in self.plots:
            plot.reset_view()


    def __show_about(self):
        try:
            version = metadata.version("oszilab")
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
            """
        )

    def __start_stop(self, checked):
        def colored_icon(color):
            pixmap = qtg.QPixmap(24, 24)
            pixmap.fill(qtg.QColor("transparent"))
            painter = qtg.QPainter(pixmap)
            painter.setBrush(qtg.QColor(color))
            painter.setPen(qtg.QColor(color))
            painter.drawEllipse(2, 2, 20, 20)
            painter.end()
            return qtg.QIcon(pixmap)

        if checked:
            self.start_stop_action.setText("Stopped")
            self.start_stop_action.setIcon(colored_icon("red"))
            self.refresh_timer.stop()

        else:
            self.start_stop_action.setText("Running")
            self.start_stop_action.setIcon(colored_icon("green"))
            self.refresh_timer.start()

    def __set_fps(self, text):
        fps = int(text.split()[0])  # "30 FPS" -> 30

        self.refresh_timer.start(1000 // fps)
