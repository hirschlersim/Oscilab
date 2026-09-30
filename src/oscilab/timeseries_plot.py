import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore as qtc

from oscilab.display import TIME_MODES, TimeMode
from oscilab.signal_model import SignalModel


class TimeseriesPlot(pg.PlotWidget):
    """Scrolling multi-signal time-series plot. Implements `oscilab.view.View`."""

    cursor_changed = qtc.pyqtSignal()  # Cursor region dragged — fires even while paused

    def __init__(self, title: str | None = None) -> None:
        super().__init__()

        self._title = title
        self.time_mode: TimeMode = "global"  # "global" (as recorded) or "relative" ([-duration, 0], now at 0)
        self.downsampling = False  # Off by default — opt in via the toolbar "Downsample" button
        self.y_range_auto = True  # Off: Y is pinned to (y_range_min, y_range_max), sticky across Reset View
        self.y_range_min = 0.0
        self.y_range_max = 1.0
        self.cursor_region: pg.LinearRegionItem | None = None
        self._shift = 0.0  # Subtracted from time on screen by the last refresh(): `now` in relative mode

        # Curve colors, assigned round-robin as signals are added
        self.colors = ["#FF5555", "#55FF55", "#5555FF", "#FFFF55", "#FF55FF"]
        self.curves: dict[str, pg.PlotDataItem] = {}
        self.models: dict[str, SignalModel] = {}

        # Style and Color
        self.showGrid(x=True, y=True, alpha=0.3)
        self.addLegend()
        # Y ticks show the values as they are, like the metrics and cursor
        # readouts, instead of rescaling them to mV, k°C, ...
        self.getAxis("left").enableAutoSIPrefix(False)
        self.set_theme(bg="#202f46", fg="#ffffff")
        self.__update_axis_label()

        # Add Zoom and Click behaviour
        self.setMenuEnabled(False)
        self.getViewBox().setMouseMode(pg.ViewBox.RectMode)

    def set_theme(self, bg: str, fg: str) -> None:
        """Recolor background, title and axes — used for light/dark theme switching."""
        self.setBackground(bg)
        self.setTitle(self._title, color=fg)
        self.getAxis("left").setTextPen(fg)
        self.getAxis("bottom").setTextPen(fg)
        self.getAxis("left").setPen(fg)
        self.getAxis("bottom").setPen(fg)

    def set_time_mode(self, mode: TimeMode) -> None:
        """
        "global": x-axis shows time as recorded. "relative": shifted so the
        newest sample sits at 0 and the buffer's start sits at -duration.
        """
        if mode not in TIME_MODES:
            raise ValueError(f"Unknown time mode '{mode}'")
        self.time_mode = mode
        self.__update_axis_label()
        # Recompute curve data under the new mode right away, then re-fit +
        # resume auto-follow (see reset_view()), so a prior manual pan/zoom
        # doesn't leave the view stuck showing the old mode's now-irrelevant range.
        self.refresh()
        self.reset_view()

    def __update_axis_label(self) -> None:
        label = "Time (relative to now)" if self.time_mode == "relative" else "Time"
        self.setLabel("bottom", label, units="s")

    def refresh(self, now: float | None = None) -> None:
        """
        Redraw every visible curve from its signal's current buffer (called
        each tick). In relative time mode, `now` is shown as 0 — one shift for
        every curve, so signals that arrive with different delays stay in step
        (see `oscilab.view.View.refresh`).
        """
        if self.time_mode == "relative":
            self._shift = now if now is not None else self.__newest_time()
        else:
            self._shift = 0.0
        for label, curve in self.curves.items():
            if not curve.isVisible():
                continue
            model = self.models[label]
            if not model.time:
                continue
            x, y = model.snapshot()
            curve.setData(x - self._shift, y)

    def __newest_time(self) -> float:
        newest = [model.time[-1] for model in self.models.values() if model.time]
        return max(newest, default=0.0)

    def add_signal(self, label: str, model: SignalModel) -> str:
        color = self.colors[len(self.curves) % len(self.colors)]
        curve = self.plot(pen=pg.mkPen(color, width=1.5), name=model.display_name, antialias=False)
        curve.setDownsampling(auto=self.downsampling, method="peak")
        curve.setClipToView(self.downsampling)
        self.curves[label] = curve
        self.models[label] = model
        self.__update_y_label()
        return color

    def remove_signal(self, label: str) -> None:
        """Take a signal's curve (and its legend entry) off this plot."""
        self.removeItem(self.curves.pop(label))
        del self.models[label]
        self.__update_y_label()

    @property
    def units(self) -> list[str]:
        """The units of the signals shown, each once, in legend order ("" left out)."""
        return list(dict.fromkeys(model.unit for model in self.models.values() if model.unit))

    @property
    def y_unit(self) -> str:
        """The Y axis's unit, when every signal shown that has one shares it."""
        units = self.units
        return units[0] if len(units) == 1 else ""

    def update_units(self) -> None:
        """Show the signals' current units in the legend and on the Y axis."""
        for label, curve in self.curves.items():
            self.plotItem.legend.getLabel(curve).setText(self.models[label].display_name)
        self.__update_y_label()

    def __update_y_label(self) -> None:
        units = self.units
        if units:
            self.setLabel("left", "Value", units=", ".join(units))
        else:
            self.setLabel("left")  # No units: no label, as without any signals

    def set_downsampling(self, enabled: bool) -> None:
        """
        Min-max ("peak") downsampling: for every screen pixel column, keep
        only that column's min and max sample — a spike is never dropped
        just because there are more samples than pixels to draw them in.
        Paired with clip-to-view (skip processing off-screen points) since
        both are the same "render less without changing what you'd see"
        tradeoff. Applies to every curve, current and future.
        """
        self.downsampling = enabled
        for curve in self.curves.values():
            curve.setDownsampling(auto=enabled, method="peak")
            curve.setClipToView(enabled)

    def set_visible(self, label: str, visible: bool) -> None:
        """Show/hide a signal's curve without touching its data buffer."""
        if label in self.curves:
            self.curves[label].setVisible(visible)

    def set_color(self, label: str, color: str) -> None:
        if label in self.curves:
            self.curves[label].setPen(pg.mkPen(color, width=1.5))

    @property
    def plot_item(self) -> pg.PlotItem:
        return self.plotItem

    def live_y_range(self) -> tuple[float, float]:
        y_min, y_max = self.getViewBox().viewRange()[1]
        return y_min, y_max

    def set_y_range(self, auto: bool, y_min: float = 0.0, y_max: float = 1.0) -> None:
        """
        Auto: Y keeps auto-fitting to the data, as before. Manual: pin
        exactly [y_min, y_max] and stop auto-fitting Y on new data — X keeps
        auto-scrolling/fitting either way.
        """
        self.y_range_auto = auto
        self.y_range_min = y_min
        self.y_range_max = y_max
        if auto:
            self.enableAutoRange(axis="y")
        else:
            self.enableAutoRange(axis="y", enable=False)
            self.setYRange(y_min, y_max, padding=0)

    def reset_view(self) -> None:
        self.autoRange()
        # autoRange() only does a ONE-TIME fit — it actually leaves the
        # ViewBox's continuous auto-range OFF afterward (for both axes,
        # regardless of what triggered the reset), which is how the X-axis
        # normally keeps following new data as it streams in. Without
        # re-enabling it here, the plot would silently freeze in place the
        # next time data updates instead of resuming its live scroll.
        self.enableAutoRange(axis="x")
        if self.y_range_auto:
            self.enableAutoRange(axis="y")
        else:
            # A manual Y-range survives Reset View instead of being wiped by it.
            self.setYRange(self.y_range_min, self.y_range_max, padding=0)

    def set_cursors(self, enabled: bool) -> None:
        """
        Classic two-cursor scope measurement: a draggable region whose two
        edges are Cursor A/B. Read the result via `cursor_readout()`.
        """
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

    def move_cursors(self, t_a: float, t_b: float) -> None:
        """Put Cursor A and B at `t_a` and `t_b` (displayed time); cursors must be on."""
        if self.cursor_region is None:
            raise ValueError(f"Cursors are off on '{self._title}'")
        self.cursor_region.setRegion((t_a, t_b))

    def cursor_readout(self) -> dict | None:
        """
        Classic scope cursor readings, or None if cursors are off:
        Cursor A/B time (in the plot's own displayed coordinates), ΔT,
        1/ΔT, and per-visible-signal value at A/B and their delta.
        """
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
            # Displayed coordinates are shifted by the last refresh's `now` in
            # relative time mode (see `refresh()`); map back to real time to
            # look up samples. The shift cancels in `t_b - t_a`, so ΔT above
            # is already correct regardless of time mode.
            idx_a = int(np.argmin(np.abs(time_arr - (t_a + self._shift))))
            idx_b = int(np.argmin(np.abs(time_arr - (t_b + self._shift))))
            value_a, value_b = float(data_arr[idx_a]), float(data_arr[idx_b])
            signals[label] = {"value_a": value_a, "value_b": value_b, "delta": value_b - value_a}

        return {"t_a": t_a, "t_b": t_b, "dt": dt, "freq": freq, "signals": signals}


# Spectrogram/FFT visualization lives in `oscilab.spectrogram_plot.SpectrogramPlot`.
