from collections.abc import Callable

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
        # A triggered record on show instead of the live buffers (see `show_record`)
        self._record: dict[str, tuple[np.ndarray, np.ndarray]] | None = None
        self._record_range: tuple[float, float] = (0.0, 0.0)  # On screen, from the trigger
        self._trigger_mark: pg.InfiniteLine | None = None
        self._level_lines: list[pg.InfiniteLine] = []

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
        if self._record is not None:
            label = "Time (from trigger)"
        else:
            label = "Time (relative to now)" if self.time_mode == "relative" else "Time"
        self.setLabel("bottom", label, units="s")

    def refresh(self, now: float | None = None) -> None:
        """
        Redraw every visible curve from its signal's current buffer (called
        each tick). In relative time mode, `now` is shown as 0 — one shift for
        every curve, so signals that arrive with different delays stay in step
        (see `oscilab.view.View.refresh`).
        """
        if self._record is not None:
            return  # Holding a triggered record until the next one (see `show_record`)
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
            self._draw(label, x - self._shift, y)

    def _draw(self, label: str, t: np.ndarray, y: np.ndarray) -> None:
        """Show one signal's samples; `t` is already in screen time. Subclasses draw them their own way."""
        self.curves[label].setData(t, y)

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
        self._update_y_axis()
        return color

    def remove_signal(self, label: str) -> None:
        """Take a signal's curve (and its legend entry) off this plot."""
        self.removeItem(self.curves.pop(label))
        del self.models[label]
        self._update_y_axis()

    @property
    def signals(self) -> list[str]:
        """The labels of the signals shown, in legend order."""
        return list(self.curves)

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
        self._update_y_axis()

    def _update_y_axis(self) -> None:
        """Label the Y axis for the signals shown (called as they come, go, or change unit)."""
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

    @property
    def showing_record(self) -> bool:
        return self._record is not None

    def show_record(
        self, record: dict[str, tuple[np.ndarray, np.ndarray]], event: float, start: float, end: float
    ) -> None:
        """
        Show a triggered record — each signal's (time, value) from `start` to
        `end` — instead of the live buffers, held until the next one. Time on
        screen counts from the trigger `event`, marked "T".
        """
        self._record = record
        self._shift = event
        self._record_range = (start - event, end - event)
        for label in self.curves:
            if label in record:
                t, y = record[label]
                self._draw(label, t - event, y)
        if self._trigger_mark is None:
            self._trigger_mark = pg.InfiniteLine(
                pos=0.0, angle=90, pen=pg.mkPen("#f0b429", width=1, style=qtc.Qt.PenStyle.DashLine), label="T"
            )
            self.addItem(self._trigger_mark, ignoreBounds=True)
        self.__update_axis_label()
        self.__fit_record()

    def show_live(self) -> None:
        """Back to the live, scrolling buffers."""
        if self._record is None:
            return
        self._record = None
        if self._trigger_mark is not None:
            self.removeItem(self._trigger_mark)
            self._trigger_mark = None
        self.__update_axis_label()
        self.refresh()
        self.reset_view()

    def __fit_record(self) -> None:
        self.enableAutoRange(axis="x", enable=False)
        self.setXRange(*self._record_range, padding=0)

    def set_trigger_levels(self, levels: list[float], moved: Callable[[int, float], None] | None = None) -> None:
        """
        Dashed lines at the trigger's `levels` (one for an edge, two for a
        window; none: no lines). Dragging one calls `moved(index, level)`.
        """
        while len(self._level_lines) > len(levels):
            self.removeItem(self._level_lines.pop())
        while len(self._level_lines) < len(levels):
            index = len(self._level_lines)
            line = pg.InfiniteLine(
                angle=0, movable=True, pen=pg.mkPen("#f0b429", width=1, style=qtc.Qt.PenStyle.DashLine)
            )
            line.setHoverPen(pg.mkPen("#f0b429", width=3))
            if moved is not None:
                line.sigPositionChangeFinished.connect(lambda item, i=index: moved(i, float(item.value())))
            self.addItem(line, ignoreBounds=True)
            self._level_lines.append(line)
        for line, level in zip(self._level_lines, levels, strict=True):
            if not line.moving:  # Not while it's being dragged
                line.setValue(level)

    def reset_view(self) -> None:
        if self._record is not None:
            self.enableAutoRange(axis="y", enable=self.y_range_auto)
            self.__fit_record()
            return
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
            if self._record is not None and label in self._record:
                time_arr, data_arr = self._record[label]  # What's on show, not the live buffer
            elif model.time:
                time_arr, data_arr = model.snapshot()
            else:
                continue
            if time_arr.size == 0:
                continue
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
