"""
XY plots: one signal against another, instead of each against time.

A joystick's X against its Y traces where the stick goes; two sines make a
Lissajous figure that shows their frequency ratio and phase; a voltage
against a current is a component's I-V curve. The newest point is marked.
The signals may run at different rates, or start at different times: Y is
taken at X's timestamps (interpolated), over the time both cover — the same
way math signals line their operands up.
"""

import numpy as np
import pyqtgraph as pg

from oscilab.display import TimeMode
from oscilab.grid import GridPlotWidget
from oscilab.signal_model import SignalModel

_CURVE = "#4ea3ff"


class XYPlot(GridPlotWidget):
    """`y` against `x` — see the module docstring. Implements `oscilab.view.View`."""

    def __init__(self, title: str, x: SignalModel, y: SignalModel, trail: float | None = None) -> None:
        super().__init__()
        self._title = title
        self.x_model = x
        self.y_model = y
        self.trail = trail  # Seconds of history drawn; None: the whole buffer
        self.equal_axes = False
        self.y_range_auto = True  # Off: pinned to (y_range_min, y_range_max), like X below
        self.y_range_min = 0.0
        self.y_range_max = 1.0
        self.x_range_auto = True
        self.x_range_min = 0.0
        self.x_range_max = 1.0
        self._hidden: set[str] = set()  # Signals whose rows say Hidden: the trace goes while any is

        self.setMenuEnabled(False)
        for axis in ("left", "bottom"):
            self.getAxis(axis).enableAutoSIPrefix(False)  # Values as they are, like everywhere else
        self.curve = self.plot(pen=pg.mkPen(_CURVE, width=1.5), antialias=False)
        self.head = self.plot(pen=None, symbol="o", symbolSize=9, antialias=True)  # The newest point
        self.set_theme(bg="#202f46", fg="#ffffff")
        self.update_units()

    @property
    def signals(self) -> list[str]:
        return [self.x_model.label, self.y_model.label]

    @property
    def y_unit(self) -> str:
        return self.y_model.unit

    @property
    def plot_item(self) -> pg.PlotItem:
        return self.plotItem

    def points(self) -> tuple[np.ndarray, np.ndarray]:
        """(x, y) as drawn: Y at X's timestamps, over the time both cover, within the trail."""
        t_x, x = self.x_model.snapshot()
        t_y, y = self.y_model.snapshot()
        if t_x.size == 0 or t_y.size == 0:
            return np.array([]), np.array([])
        keep = (t_x >= t_y[0]) & (t_x <= t_y[-1])
        if self.trail is not None:
            keep &= t_x >= min(t_x[-1], t_y[-1]) - self.trail
        t = t_x[keep]
        return x[keep], np.interp(t, t_y, y)

    def refresh(self, now: float | None = None) -> None:  # noqa: ARG002 — no time axis to shift
        x, y = self.points()
        self.curve.setData(x, y)
        self.head.setData(x[-1:], y[-1:])
        self._settle_view()

    def set_theme(self, bg: str, fg: str) -> None:
        self.setBackground(bg)
        self.setTitle(self._title, color=fg)
        for axis in ("left", "bottom"):
            self.getAxis(axis).setTextPen(fg)
            self.getAxis(axis).setPen(fg)
        self.head.setSymbolBrush(fg)
        self.head.setSymbolPen(fg)

    def set_time_mode(self, mode: TimeMode) -> None:
        """No-op: there's no time axis."""

    def set_downsampling(self, enabled: bool) -> None:
        """No-op: the trace isn't a function of X, so min-max thinning doesn't apply."""

    def set_visible(self, label: str, visible: bool) -> None:
        """Hide the trace while either signal's row says Hidden."""
        if visible:
            self._hidden.discard(label)
        else:
            self._hidden.add(label)
        for item in (self.curve, self.head):
            item.setVisible(not self._hidden)

    def update_units(self) -> None:
        """Each axis named after its signal, unit included."""
        self.setLabel("bottom", self.x_model.display_name)
        self.setLabel("left", self.y_model.display_name)

    def set_equal_axes(self, on: bool) -> None:
        """One unit is as long on X as on Y — a circle stays round."""
        self.equal_axes = on
        self.setAspectLocked(on)

    def set_x_range(self, auto: bool, x_min: float = 0.0, x_max: float = 1.0) -> None:
        self.x_range_auto, self.x_range_min, self.x_range_max = auto, x_min, x_max
        self.__apply_range("x")

    def set_y_range(self, auto: bool, y_min: float = 0.0, y_max: float = 1.0) -> None:
        self.y_range_auto, self.y_range_min, self.y_range_max = auto, y_min, y_max
        self.__apply_range("y")

    def live_x_range(self) -> tuple[float, float]:
        x_min, x_max = self.getViewBox().viewRange()[0]
        return x_min, x_max

    def live_y_range(self) -> tuple[float, float]:
        y_min, y_max = self.getViewBox().viewRange()[1]
        return y_min, y_max

    def reset_view(self) -> None:
        self.autoRange()
        self.__apply_range("x")
        self.__apply_range("y")

    def __apply_range(self, axis: str) -> None:
        auto = self.x_range_auto if axis == "x" else self.y_range_auto
        if auto:
            self.enableAutoRange(axis=axis)
        elif axis == "x":
            self.enableAutoRange(axis="x", enable=False)
            self.setXRange(self.x_range_min, self.x_range_max, padding=0)
        else:
            self.enableAutoRange(axis="y", enable=False)
            self.setYRange(self.y_range_min, self.y_range_max, padding=0)
