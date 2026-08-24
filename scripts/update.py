import sys
import numpy as np
from collections import deque
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QHBoxLayout, 
                             QVBoxLayout, QLabel, QPushButton, QDoubleSpinBox)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor
import pyqtgraph as pg

class SignalModel:
    """Manages the data buffer and properties for an individual signal."""
    def __init__(self, label, fs, duration, subplot_idx, color):
        self.label = label
        self.fs = fs
        self.subplot_idx = subplot_idx
        self.color = color
        self.scale = 1.0
        self.filter_enabled = False
        
        # Calculate buffer size based on duration and sampling frequency
        self.max_samples = int(fs * duration)
        self.x_data = np.linspace(-duration, 0, self.max_samples)
        self.y_data = deque(maxlen=self.max_samples)
        
        # Pre-fill buffer with zeros
        for _ in range(self.max_samples):
            self.y_data.append(0.0)
            
        # PyQtGraph curve reference (linked later during UI binding)
        self.curve = None

    def append_data(self, value):
        self.y_data.append(value)

    def get_data(self):
        y = np.array(self.y_data) * self.scale
        if self.filter_enabled:
            # Dummy example: Simple moving average filter
            y = np.convolve(y, np.ones(5)/5, mode='same')
        return self.x_data, y


class SignalControlRow(QWidget):
    """Dynamically generated sidebar UI entry for controlling a signal."""
    def __init__(self, signal_model, on_layout_change_cb):
        super().__init__()
        self.model = signal_model
        
        layout = QHBoxLayout()
        layout.setContentsMargins(2, 2, 2, 2)
        
        # Label
        self.lbl = QLabel(f"<b>{self.model.label}</b> (S{self.model.subplot_idx})")
        layout.addWidget(self.lbl)
        
        # Color Button (Toggles between preset colors for simplicity)
        self.btn_color = QPushButton("Color")
        self.btn_color.setStyleSheet(f"background-color: {self.model.color}; color: black;")
        self.btn_color.clicked.connect(self.change_color)
        layout.addWidget(self.btn_color)
        
        # Scaling Spinbox
        self.spin_scale = QDoubleSpinBox()
        self.spin_scale.setRange(0.1, 100.0)
        self.spin_scale.setValue(1.0)
        self.spin_scale.setSingleStep(0.1)
        self.spin_scale.valueChanged.connect(self.update_scale)
        layout.addWidget(self.spin_scale)
        
        # Filter Button
        self.btn_filter = QPushButton("Filter: OFF")
        self.btn_filter.setCheckable(True)
        self.btn_filter.clicked.connect(self.toggle_filter)
        layout.addWidget(self.btn_filter)
        
        self.setLayout(layout)

    def change_color(self):
        # Cyclical color switching for demonstration
        colors = ["#FF5555", "#55FF55", "#5555FF", "#FFFF55"]
        current_idx = colors.index(self.model.color) if self.model.color in colors else 0
        next_color = colors[(current_idx + 1) % len(colors)]
        
        self.model.color = next_color
        self.btn_color.setStyleSheet(f"background-color: {next_color}; color: black;")
        if self.model.curve:
            self.model.curve.setPen(pg.mkPen(next_color, width=1.5))

    def update_scale(self, val):
        self.model.scale = val

    def toggle_filter(self, checked):
        self.model.filter_enabled = checked
        self.btn_filter.setText("Filter: ON" if checked else "Filter: OFF")


class OsziWindow(QMainWindow):
    """The primary UI layout containing subplots and the sidebar controls."""
    def __init__(self, num_subplots):
        super().__init__()
        self.setWindowTitle("Live Oscilloscope")
        self.resize(1000, 600)
        
        # Main Layout: Sidebar on Left, Plots on Right
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        
        # Sidebar Setup
        self.sidebar = QVBoxLayout()
        self.sidebar.setAlignment(Qt.AlignmentFlag.AlignTop)
        sidebar_title = QLabel("<h3>Signal Controls</h3>")
        self.sidebar.addWidget(sidebar_title)
        
        sidebar_widget = QWidget()
        sidebar_widget.setLayout(self.sidebar)
        sidebar_widget.setFixedWidth(280)
        
        # Plots Layout
        self.plots_layout = QVBoxLayout()
        
        # Generate requested subplots automagically
        self.plot_widgets = []
        for i in range(num_subplots):
            pw = pg.PlotWidget(title=f"Subplot {i+1}")
            pw.showGrid(x=True, y=True, alpha=0.3)
            pw.setLabel('bottom', 'Time', units='s')
            self.plots_layout.addWidget(pw)
            self.plot_widgets.append(pw)
            
        main_layout.addWidget(sidebar_widget)
        main_layout.addLayout(self.plots_layout)

    def add_signal_ui(self, signal_model):
        # 1. Create sidebar controls
        row = SignalControlRow(signal_model, self)
        self.sidebar.addWidget(row)
        
        # 2. Attach PyqtGraph Curve to the target subplot (Subplot index is 1-based in your API)
        target_plot = self.plot_widgets[signal_model.subplot_idx - 1]
        curve = target_plot.plot(pen=pg.mkPen(signal_model.color, width=1.5))
        signal_model.curve = curve


class Oszi:
    """The master Class / Controller requested by the user API design."""
    def __init__(self, subplots=3, duration=30):
        self.app = QApplication(sys.argv)
        self.duration = duration
        self.num_subplots = subplots
        self.signals = []
        
        # Default color rotation for newly added signals
        self._color_palette = ["#FF5555", "#55FF55", "#5555FF", "#FFFF55", "#FF55FF"]
        
        # Initialize UI Window
        self.window = OsziWindow(num_subplots=subplots)
        
        # Refresh Timer (Sets GUI FPS, e.g., 30 ms -> ~33 FPS)
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_plots)
        self.timer.start(30)
        
        # Fake data generator variables
        self.phase = 0.0

    def add_time_signal(self, label, fs, subplot):
        if subplot > self.num_subplots or subplot < 1:
            raise ValueError(f"Subplot index {subplot} out of bounds.")
            
        color = self._color_palette[len(self.signals) % len(self._color_palette)]
        
        # Instantiating data engine
        model = SignalModel(label, fs, self.duration, subplot, color)
        self.signals.append(model)
        
        # Dynamically append to UI
        self.window.add_signal_ui(model)

    def update_plots(self):
        """High-frequency tick: simulates receiving data and repaints the canvas."""
        self.phase += 0.05
        
        for idx, sig in enumerate(self.signals):
            # Simulated real-time incoming data feed (e.g. from an ADC or Socket)
            # Higher index signals get different mathematical frequencies for variation
            freq_multiplier = idx + 1
            simulated_value = np.sin(self.phase * freq_multiplier) + (np.random.normal(0, 0.1))
            sig.append_data(simulated_value)
            
            # Redraw curves safely
            if sig.curve:
                x, y = sig.get_data()
                sig.curve.setData(x, y)

    def run(self):
        self.window.show()
        sys.exit(self.app.exec())



if __name__ == "__main__":
    # 1. Initialize your custom API layout
    oszi = Oszi(subplots=3, duration=10)
    
    # 2. Add your signals (automagically builds UI elements)
    oszi.add_time_signal(label="Power Grid", fs=200, subplot=1)
    oszi.add_time_signal(label="Current Phase", fs=200, subplot=2)
    oszi.add_time_signal(label="Thermal Sensor", fs=100, subplot=3)
    oszi.add_time_signal(label="Auxiliary Rail", fs=100, subplot=3) # Multiple signals on 1 subplot
    
    # 3. Start loop
    oszi.run()