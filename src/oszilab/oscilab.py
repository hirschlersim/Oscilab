import ctypes
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Oscilab")  # Tell Windows to treat this script as a distinct application identifier

from PyQt6 import QtWidgets as qtw
from PyQt6 import QtGui as qtg
from PyQt6 import QtCore as qtc

from oszilab.base_plot import TimeseriesPlot
from oszilab.signal_controller import SignalController
from oszilab.signal_model import SignalModel

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
            with resources.files("oszilab.assets").joinpath("style.qss").open("r") as f:
                self.app.setStyleSheet(f.read())
        else:
            self.app = app
        super().__init__()

        # Splash Screen
        splash_path = resources.files("oszilab.assets").joinpath("icon.png")
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
        self.color_bg = "#202f46"
        self.color_fg = '#ffffff'

        # Plots
        self.plots = []
        self.plot_layout = qtw.QVBoxLayout()

        # Timer / Update loop
        self.timer = qtc.QTimer()
        self.timer.timeout.connect(self.__refresh_ui_canvas)
        self.timer.start(30)

        # Define Action's
            # Save - png
        self.png_action = qtg.QAction(".png", self)
        self.png_action.triggered.connect(lambda: print("save png"))
        self.png_action.setShortcut("Ctrl+S")
            # Save - csv
        self.csv_action = qtg.QAction(".csv", self)
        self.csv_action.triggered.connect(lambda: print("save csv"))
        self.csv_action.setShortcut("Ctrl+D")
            # Start-Stop Display
        self.start_stop_action = qtg.QAction(self)
        self.start_stop_action.setCheckable(True)
        self.__start_stop(checked=False)
        self.start_stop_action.toggled.connect(self.__start_stop)
            # Reset View's
        self.reset_views_action = qtg.QAction("Reset View", self)
        self.reset_views_action.triggered.connect(self.__reset_views)
            # About
        self.about_action = qtg.QAction("About Oscilab", self)
        self.about_action.triggered.connect(self.__show_about)
        self.about_qt_action = qtg.QAction("About Qt", self)
        self.about_qt_action.triggered.connect(qtw.QApplication.aboutQt)

        # Menu bar
        file_menu = self.menuBar().addMenu("File")
        file_menu.addAction(self.png_action)
        file_menu.addAction(self.csv_action)
        view_menu = self.menuBar().addMenu("View")
        view_menu.addAction(self.reset_views_action)
        help_menu = self.menuBar().addMenu("Help")
        help_menu.addAction(self.about_action)
        help_menu.addSeparator()
        help_menu.addAction(self.about_qt_action)

        # Toolbar
        self.toolbar = self.addToolBar("Controls")
        self.toolbar.setToolButtonStyle(qtc.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.toolbar.addAction(self.start_stop_action)
        self.toolbar.addSeparator()


        # Status bar
        # self.statusBar().showMessage("Ready")
        # self.statusBar().setStyleSheet("""
        #     QStatusBar {
        #         background-color: #1aae1e;
        #         color: #aaaaaa;
        #         border-top: 1px solid #333333;
        #     }
        #     QStatusBar::item {
        #         border: none; /* Removes default rectangular line frames around widgets */
        #     }
        # """)

        # Create and add a real-time progress bar to track an operational task
        self.progress_bar = qtw.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(45)

        self.statusBar().addWidget(self.progress_bar)

        # self.statusBar().showMessage("Saved successfully", 3000)

        # Add a simple descriptive text companion label next to it
        self.status_label = qtw.QLabel("Buffer Level:")
        self.statusBar().insertWidget(0, self.status_label) # Injected at index position 0


        # Disables the resize diagonal gripper lines in the corner
        self.statusBar().setSizeGripEnabled(False)

        # Checks if the corner size grip is currently active
        is_grip_on = self.statusBar().isSizeGripEnabled()

        # Sidebar
        self.sidebar = qtw.QVBoxLayout()
        self.sidebar.setAlignment(qtc.Qt.AlignmentFlag.AlignTop)
        self.sidebar.addWidget(qtw.QLabel("<h3>Signal Controls:</h3>"))
        self.sidebar.addWidget(SignalController())
        from oszilab.signal_controller import ScalingDialog, FilterDialog  # TODO i need a buttn which then calls the widget
        self.sidebar.addWidget(ScalingDialog(label="Test"))
        self.sidebar.addWidget(FilterDialog(label="Test"))

        # Main layout
        self.setCentralWidget(qtw.QWidget())
        main_layout = qtw.QHBoxLayout(self.centralWidget())
        main_layout.addLayout(self.sidebar, stretch=0)
        main_layout.addLayout(self.plot_layout, stretch=1)

    def addTimeSeriesPlot(self, title:str=None):
        plot = TimeseriesPlot(title=title)
        self.plots.append(plot)
        self.plot_layout.addWidget(plot)

    def addStftPlot(self, title=None):
        pass

    def regSignal(self, label:str, fs:float):
        assert label not in self.signals
        self.signals[label] = SignalModel(label=label, fs=fs)

    def link(self, subplot, label):
        def help():
            time = np.arange(1000)
            value = np.random.random(size=(1000))

            return time, value

        subplot = subplot-1
        assert 0<=subplot and subplot<len(self.plots)

        self.plots[subplot-1].add_signal(label=label, get_data=help)

    def show(self):
        super().show()
        self.splash.finish(self)
        self.app.exec()

    def __refresh_ui_canvas(self):
        for plot in self.plots:
            plot.update()

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
        else:
            self.start_stop_action.setText("Running")
            self.start_stop_action.setIcon(colored_icon("green"))
