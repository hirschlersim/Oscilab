"""
Logic lanes: on/off signals as a logic analyzer shows them, one lane each.

Where a time-series plot draws 0/1 signals on top of each other, this gives
each its own lane, stacked top to bottom in the order they're added: high
where a value is above the plot's `threshold`, low otherwise, drawn as
steps, labeled on the Y axis. Good for buttons, digital lines and enable
signals — and for reading the timing between their edges with the cursors.
"""

import numpy as np

from oscilab.timeseries_plot import TimeseriesPlot

_LANE = 1.5  # Lane pitch: high is 1 above its lane's low, then a gap of 0.5 to the next lane


class LogicPlot(TimeseriesPlot):
    """
    On/off signals in lanes — see the module docstring. A time-series plot
    otherwise: the time axis, time modes, cursors, curve colors, Signals...
    and X-linking with the other time-based plots work just the same.
    Implements `oscilab.view.View`.
    """

    def __init__(self, title: str | None = None, threshold: float = 0.5) -> None:
        super().__init__(title)
        self.threshold = threshold
        self.plotItem.legend.hide()  # The lanes are labeled on the Y axis instead
        self.getViewBox().setMouseEnabled(x=True, y=False)  # Zoom time, keep the lanes

    @property
    def y_unit(self) -> str:
        return ""  # Levels, not values

    def set_threshold(self, threshold: float) -> None:
        """A value above `threshold` is high."""
        self.threshold = threshold

    def lane(self, label: str) -> float:
        """Where `label`'s lane sits: its low level (its high is 1 above)."""
        return (len(self.curves) - 1 - list(self.curves).index(label)) * _LANE

    def _draw(self, label: str, t: np.ndarray, y: np.ndarray) -> None:
        high = (y > self.threshold).astype(float)
        self.curves[label].setData(t, self.lane(label) + high, stepMode="left")

    def _update_y_axis(self) -> None:
        # Each lane's name at its middle, instead of values
        self.getAxis("left").setTicks(
            [[(self.lane(label) + 0.5, model.display_name) for label, model in self.models.items()], []]
        )
        if self.y_range_auto:
            self.__fit_lanes()

    def set_y_range(self, auto: bool, y_min: float = 0.0, y_max: float = 1.0) -> None:
        super().set_y_range(auto, y_min, y_max)
        if auto:
            self.__fit_lanes()

    def reset_view(self) -> None:
        """Re-fit to the data, every lane in view (unless Y is pinned)."""
        super().reset_view()
        if self.y_range_auto:
            self.__fit_lanes()

    def __fit_lanes(self) -> None:
        self.enableAutoRange(axis="y", enable=False)
        self.setYRange(-0.25, max(len(self.curves), 1) * _LANE - 0.25, padding=0)
