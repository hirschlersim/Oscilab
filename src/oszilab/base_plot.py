import pyqtgraph as pg

class TimeseriesPlot(pg.PlotWidget):
    def __init__(self, title=None):
        super().__init__()
        
        # Main Properties
        self.color_bg = "#202f46"
        self.color_fg = '#ffffff'
        
        # Variabels
        self.colors = ["#FF5555", "#55FF55", "#5555FF", "#FFFF55", "#FF55FF"]
        self.curves = []
        self.get_datas = []

        # Style and Color
        self.setLabel('bottom', 'Time', units='s')
        self.setTitle(title, color=self.color_fg)
        self.showGrid(x=True, y=True, alpha=0.3,)
        self.setBackground(self.color_bg)
        self.addLegend()
        self.getAxis('left').setTextPen(self.color_fg)
        self.getAxis('bottom').setTextPen(self.color_fg)
        self.getAxis('left').setPen(self.color_fg)
        self.getAxis('bottom').setPen(self.color_fg)

        # Add Zoom and Click behaviour
        self.setMenuEnabled(False)
        self.getViewBox().setMouseMode(pg.ViewBox.RectMode)

    def update(self):
        for curve, get_data in zip(self.curves, self.get_datas):
            x,y = get_data()
            curve.setData(x, y)

    def add_signal(self, label:str, get_data):
        color = self.colors[len(self.curves) % len(self.colors)]
        curve = self.plot(pen=pg.mkPen(color, width=1.5), name=label, antialias=False)
        self.curves.append(curve)
        self.get_datas.append(get_data)

    def reset_view(self):
        self.autoRange()


# class SpectrogramPlot(QtWidgets.QWidget):
#     def __init__(self):
#         super().__init__()
        
#         # 1. Core Parameters & Buffers
#         self.fs = 2000.0
#         self.nperseg = 256
#         self.noverlap = 128
#         self.max_time_steps = 150
#         self.chunk_size = self.nperseg - self.noverlap
        
#         self.signal_buffer = np.zeros(self.nperseg)
#         self.spec_data = np.zeros((self.max_time_steps, self.nperseg // 2 + 1))
#         self.time_counter = 0.0
        
#         # 2. UI & Plot Setup
#         layout = QtWidgets.QHBoxLayout(self)
#         self.plot = pg.PlotWidget(labels={'bottom': 'Time (s)', 'left': 'Freq (Hz)'})
#         self.plot.getViewBox().invertY(False)  # 0 at bottom, increasing upwards
#         layout.addWidget(self.plot)
        
#         self.img = pg.ImageItem()
#         self.plot.addItem(self.img)
        
#         # Map pixels to real-world Time (X) and Frequency (Y)
#         dx, dy = self.chunk_size / self.fs, self.fs / self.nperseg
#         self.img.setTransform(pg.QtGui.QTransform().scale(dx, dy))
        
#         # Color Map
#         self.hist = pg.HistogramLUTWidget()
#         self.hist.setImageItem(self.img)
#         self.hist.gradient.setColorMap(pg.colormap.get('viridis'))
#         layout.addWidget(self.hist)

#         # 3. Update Timer
#         self.timer = QtCore.QTimer()
#         self.timer.timeout.connect(self.update_plot)
#         self.timer.start(50)

#     def update_plot(self):
#         # Generate mock signal (chirp + noise)
#         t = np.arange(self.chunk_size) / self.fs + self.time_counter
#         self.time_counter += self.chunk_size / self.fs
#         new_data = np.sin(2 * np.pi * (300 + 400 * np.sin(t)) * t) + np.random.normal(0, 0.3, self.chunk_size)
        
#         # Shift signal buffer left, insert new chunk at the end
#         self.signal_buffer = np.roll(self.signal_buffer, -self.chunk_size)
#         self.signal_buffer[-self.chunk_size:] = new_data
        
#         # Compute STFT and convert to dB
#         _, _, Zxx = signal.stft(self.signal_buffer, fs=self.fs, nperseg=self.nperseg, noverlap=self.noverlap)
#         amp_db = 20 * np.log10(np.abs(Zxx[:, -1]) + 1e-6)
        
#         # Scroll spectrogram matrix left, insert new freq column at the end
#         self.spec_data = np.roll(self.spec_data, -1, axis=0)
#         self.spec_data[-1, :] = amp_db
        
#         # Render image
#         self.img.setImage(self.spec_data.T, autoLevels=False)
#         if self.time_counter < 1.0: # Auto-level just for the first second
#             self.hist.setLevels(self.spec_data.min(), self.spec_data.max())