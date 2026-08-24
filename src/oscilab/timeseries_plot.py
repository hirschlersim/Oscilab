import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore as qtc


class TimeseriesPlot(pg.PlotWidget):
    cursor_changed = qtc.pyqtSignal()  # Cursor region dragged — fires even while paused

    def __init__(self, _title=None):
        super().__init__()

        self._title = _title
        self.time_mode = "global"  # "global" (as recorded) or "relative" ([-duration, 0], now at 0)
        self.downsampling = False  # Off by default — opt in via the toolbar "Downsample" button
        self.y_range_auto = True  # Off: Y is pinned to (y_range_min, y_range_max), sticky across Reset View
        self.y_range_min = 0.0
        self.y_range_max = 1.0
        self.cursor_region: pg.LinearRegionItem | None = None

        # Variabels
        self.colors = ["#FF5555", "#55FF55", "#5555FF", "#FFFF55", "#FF55FF"]
        self.curves: dict[str, pg.PlotDataItem] = {}
        self.models: dict[str, object] = {}

        # Style and Color
        self.showGrid(x=True, y=True, alpha=0.3,)
        self.addLegend()
        self.set_theme(bg="#202f46", fg="#ffffff")
        self.__update_axis_label()

        # Add Zoom and Click behaviour
        self.setMenuEnabled(False)
        self.getViewBox().setMouseMode(pg.ViewBox.RectMode)


    def set_theme(self, bg: str, fg: str):
        """Recolor background, title and axes — used for light/dark theme switching."""
        self.color_bg = bg
        self.color_fg = fg
        self.setBackground(bg)
        self.setTitle(self._title, color=fg)
        self.getAxis('left').setTextPen(fg)
        self.getAxis('bottom').setTextPen(fg)
        self.getAxis('left').setPen(fg)
        self.getAxis('bottom').setPen(fg)


    def set_time_mode(self, mode: str):
        """"global": x-axis shows time as recorded. "relative": shifted so the
        newest sample sits at 0 and the buffer's start sits at -duration."""
        assert mode in ("global", "relative"), f"Unknown time mode '{mode}'"
        self.time_mode = mode
        self.__update_axis_label()
        self.update()  # Recompute curve data under the new mode right away...
        self.reset_view()  # ...then re-fit + resume auto-follow (see reset_view()),
                            # so a prior manual pan/zoom doesn't leave the view
                            # stuck showing the old mode's now-irrelevant range.


    def __update_axis_label(self):
        label = "Time (relative to now)" if self.time_mode == "relative" else "Time"
        self.setLabel('bottom', label, units='s')


    def update(self):
        for label, curve in self.curves.items():
            if not curve.isVisible():
                continue
            model = self.models[label]
            if not model.time:
                continue
            x, y = model.snapshot()
            if self.time_mode == "relative":
                x = x - x[-1]  # Newest sample at 0, older samples negative
            curve.setData(x, y)


    def add_signal(self, label:str, model) -> str:
        color = self.colors[len(self.curves) % len(self.colors)]
        curve = self.plot(pen=pg.mkPen(color, width=1.5), name=label, antialias=False)
        curve.setDownsampling(auto=self.downsampling, method='peak')
        curve.setClipToView(self.downsampling)
        self.curves[label] = curve
        self.models[label] = model
        return color


    def set_downsampling(self, enabled: bool):
        """Min-max ("peak") downsampling: for every screen pixel column, keep
        only that column's min and max sample — a spike is never dropped
        just because there are more samples than pixels to draw them in.
        Paired with clip-to-view (skip processing off-screen points) since
        both are the same "render less without changing what you'd see"
        tradeoff. Applies to every curve, current and future."""
        self.downsampling = enabled
        for curve in self.curves.values():
            curve.setDownsampling(auto=enabled, method='peak')
            curve.setClipToView(enabled)


    def set_visible(self, label: str, visible: bool):
        """Show/hide a signal's curve without touching its data buffer."""
        if label in self.curves:
            self.curves[label].setVisible(visible)


    def set_color(self, label: str, color: str):
        if label in self.curves:
            self.curves[label].setPen(pg.mkPen(color, width=1.5))


    def set_y_range(self, auto: bool, y_min: float = 0.0, y_max: float = 1.0):
        """Auto: Y keeps auto-fitting to the data, as before. Manual: pin
        exactly [y_min, y_max] and stop auto-fitting Y on new data — X keeps
        auto-scrolling/fitting either way."""
        self.y_range_auto = auto
        self.y_range_min = y_min
        self.y_range_max = y_max
        if auto:
            self.enableAutoRange(axis='y')
        else:
            self.enableAutoRange(axis='y', enable=False)
            self.setYRange(y_min, y_max, padding=0)


    def reset_view(self):
        self.autoRange()
        # autoRange() only does a ONE-TIME fit — it actually leaves the
        # ViewBox's continuous auto-range OFF afterward (for both axes,
        # regardless of what triggered the reset), which is how the X-axis
        # normally keeps following new data as it streams in. Without
        # re-enabling it here, the plot would silently freeze in place the
        # next time data updates instead of resuming its live scroll.
        self.enableAutoRange(axis='x')
        if self.y_range_auto:
            self.enableAutoRange(axis='y')
        else:
            # A manual Y-range survives Reset View instead of being wiped by it.
            self.setYRange(self.y_range_min, self.y_range_max, padding=0)


    def set_cursors(self, enabled: bool):
        """Classic two-cursor scope measurement: a draggable region whose two
        edges are Cursor A/B. Read the result via `cursor_readout()`."""
        if enabled and self.cursor_region is None:
            (x_min, x_max), _ = self.getViewBox().viewRange()
            span = x_max - x_min
            self.cursor_region = pg.LinearRegionItem(
                values=(x_min + span * 0.4, x_min + span * 0.6),
                brush=pg.mkBrush(255, 255, 255, 40),
                pen=pg.mkPen("#ffffff", width=1.5),
            )
            # Fires live while dragging, so the readout can stay in sync even
            # when the refresh timer is stopped (paused) — otherwise the
            # region visibly moves but its readout text goes stale.
            self.cursor_region.sigRegionChanged.connect(self.cursor_changed.emit)
            self.addItem(self.cursor_region)
        elif not enabled and self.cursor_region is not None:
            self.removeItem(self.cursor_region)
            self.cursor_region = None


    def cursor_readout(self) -> dict | None:
        """Classic scope cursor readings, or None if cursors are off:
        Cursor A/B time (in the plot's own displayed coordinates), ΔT,
        1/ΔT, and per-visible-signal value at A/B and their delta."""
        if self.cursor_region is None:
            return None

        t_a, t_b = self.cursor_region.getRegion()
        if t_a > t_b:
            t_a, t_b = t_b, t_a
        dt = t_b - t_a
        freq = (1.0 / dt) if dt > 0 else None

        signals = {}
        for label, curve in self.curves.items():
            if not curve.isVisible():
                continue
            model = self.models[label]
            if not model.time:
                continue

            time_arr, data_arr = model.snapshot()
            # Displayed coordinates are shifted by -now in relative time
            # mode (see `update()`); map back to the model's own real time
            # to look up samples. The shift cancels in `t_b - t_a`, so ΔT
            # above is already correct regardless of time mode.
            shift = time_arr[-1] if self.time_mode == "relative" else 0.0
            idx_a = int(np.argmin(np.abs(time_arr - (t_a + shift))))
            idx_b = int(np.argmin(np.abs(time_arr - (t_b + shift))))
            value_a, value_b = float(data_arr[idx_a]), float(data_arr[idx_b])
            signals[label] = {"value_a": value_a, "value_b": value_b, "delta": value_b - value_a}

        return {"t_a": t_a, "t_b": t_b, "dt": dt, "freq": freq, "signals": signals}


# Spectrogram/FFT visualization lives in `oscilab.spectrogram_plot.SpectrogramPlot`.
