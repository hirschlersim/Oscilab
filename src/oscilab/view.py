"""
The interface every plot type implements.

`Workspace` keeps every plot — time series, spectrograms, and any future
type (FFT, XY, ...) — in one registry and drives them all through this
interface: the refresh loop, theme, time mode, downsampling, Reset View,
the "Y-Axis" button and image export. A new plot type that implements it
gets all of those without touching `Workspace`; only what's unique to it
(like a spectrogram's color levels) needs its own wiring.
"""

from typing import Protocol, runtime_checkable

import pyqtgraph as pg


@runtime_checkable
class View(Protocol):
    y_range_auto: bool  # True: Y keeps auto-fitting; False: pinned to (y_range_min, y_range_max)
    y_range_min: float
    y_range_max: float
    y_unit: str  # Shown in the Y-Axis dialog, e.g. "Hz"; "" for none

    @property
    def plot_item(self) -> pg.PlotItem:
        """The `PlotItem` the image exporters render."""
        ...

    def refresh(self) -> None:
        """Redraw from the current signal buffers. Called every tick."""
        ...

    def set_theme(self, bg: str, fg: str) -> None: ...

    def set_time_mode(self, mode: str) -> None:
        """`"global"` (time as recorded) or `"relative"` (newest sample at 0)."""
        ...

    def set_downsampling(self, enabled: bool) -> None:
        """Min-max curve downsampling; a no-op where it doesn't apply."""
        ...

    def set_y_range(self, auto: bool, y_min: float = 0.0, y_max: float = 1.0) -> None: ...

    def live_y_range(self) -> tuple[float, float]:
        """The Y range currently on screen."""
        ...

    def reset_view(self) -> None:
        """Re-fit to the data (View > Reset View), keeping a pinned Y range."""
        ...
