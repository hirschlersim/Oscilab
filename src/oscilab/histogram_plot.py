"""
Histograms: how one signal's values are distributed, over its rolling buffer.

Shows what a trace hides: whether noise is Gaussian, whether a sensor clips
at its limits (a spike at the edge), an ADC's quantization steps or missing
codes, a signal that sits at two levels. The title gives the count, mean
and standard deviation. The bins cover the data's own range, or a
fixed one (`value_range`) so the bars don't shift as that range changes.
"""

import numpy as np
import pyqtgraph as pg

from oscilab.display import TimeMode
from oscilab.grid import GridPlotWidget
from oscilab.signal_model import SignalModel


class HistogramPlot(GridPlotWidget):
    """One signal's value distribution — see the module docstring. Implements `oscilab.view.View`."""

    y_unit = ""  # Counts

    def __init__(
        self, title: str, model: SignalModel, bins: int = 50, value_range: tuple[float, float] | None = None
    ) -> None:
        super().__init__()
        self._title = title
        self._fg = "#ffffff"
        self.model = model
        self.bins = bins
        self.value_range = value_range  # None: the data's own min to max
        self.y_range_auto = True  # Off: the count axis is pinned to (y_range_min, y_range_max)
        self.y_range_min = 0.0
        self.y_range_max = 1.0

        self.setMenuEnabled(False)
        self.getAxis("bottom").enableAutoSIPrefix(False)
        self.setLabel("left", "Count")
        self.bars = self.plot(
            stepMode="center", fillLevel=0, pen=pg.mkPen("#4ea3ff", width=1), brush=pg.mkBrush(78, 163, 255, 120)
        )
        self.set_theme(bg="#202f46", fg="#ffffff")
        self.update_units()

    @property
    def signals(self) -> list[str]:
        return [self.model.label]

    @property
    def plot_item(self) -> pg.PlotItem:
        return self.plotItem

    def set_bins(self, bins: int, value_range: tuple[float, float] | None = None) -> None:
        """`bins` bars, spread over `value_range` — or, with None, over the data's own min to max."""
        if bins < 1:
            raise ValueError(f"bins must be at least 1, got {bins}")
        if value_range is not None and not value_range[0] < value_range[1]:
            raise ValueError(f"value_range must go from low to high, got {value_range}")
        self.bins = bins
        self.value_range = value_range

    def counts(self) -> tuple[np.ndarray, np.ndarray]:
        """(counts, bin edges) for the buffer as it is — `len(edges) == len(counts) + 1`."""
        _, data = self.model.snapshot()
        data = data[np.isfinite(data)]
        if data.size == 0:
            return np.zeros(0), np.zeros(1)
        counts, edges = np.histogram(data, bins=self.bins, range=self.value_range)
        return counts.astype(float), edges

    def refresh(self, now: float | None = None) -> None:  # noqa: ARG002 — no time axis
        counts, edges = self.counts()
        if counts.size:
            self.bars.setData(edges, counts)
        else:
            self.bars.clear()
        self.setTitle(self.__title_with_stats(), color=self._fg)
        self._settle_view()

    def __title_with_stats(self) -> str:
        _, data = self.model.snapshot()
        data = data[np.isfinite(data)]
        if data.size == 0:
            return self._title
        unit = f" {self.model.unit}" if self.model.unit else ""
        stats = f"n {data.size}   μ {data.mean():.4g}{unit}   σ {data.std():.4g}{unit}"  # noqa: RUF001 — the Greek sigma is meant
        return f"{self._title}  —  {stats}"

    def set_theme(self, bg: str, fg: str) -> None:
        self._fg = fg
        self.setBackground(bg)
        self.setTitle(self.__title_with_stats(), color=fg)
        for axis in ("left", "bottom"):
            self.getAxis(axis).setTextPen(fg)
            self.getAxis(axis).setPen(fg)

    def set_time_mode(self, mode: TimeMode) -> None:
        """No-op: there's no time axis."""

    def set_downsampling(self, enabled: bool) -> None:
        """No-op: bars already sum the samples up."""

    def set_visible(self, _label: str, visible: bool) -> None:
        """Show or hide the whole histogram — its signal row's Visible toggle."""
        self.setVisible(visible)

    def update_units(self) -> None:
        self.setLabel("bottom", self.model.display_name)  # The value axis

    def set_y_range(self, auto: bool, y_min: float = 0.0, y_max: float = 1.0) -> None:
        self.y_range_auto, self.y_range_min, self.y_range_max = auto, y_min, y_max
        if auto:
            self.enableAutoRange(axis="y")
        else:
            self.enableAutoRange(axis="y", enable=False)
            self.setYRange(y_min, y_max, padding=0)

    def live_y_range(self) -> tuple[float, float]:
        y_min, y_max = self.getViewBox().viewRange()[1]
        return y_min, y_max

    def reset_view(self) -> None:
        self.autoRange()
        self.enableAutoRange(axis="x")
        self.set_y_range(self.y_range_auto, self.y_range_min, self.y_range_max)
