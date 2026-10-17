"""
The interface every plot type implements.

`Workspace` keeps every plot — time series, logic lanes, XY plots,
histograms, spectrograms, and any future type — in one registry and
drives them all through this interface: the refresh loop, theme, time
mode, downsampling, Reset View, the "Y-Axis" button and image export. A
new plot type that implements it gets all of those without touching
`Workspace`; only what's unique to it (like a spectrogram's color levels)
needs its own wiring.
"""

from typing import Protocol, runtime_checkable

import pyqtgraph as pg

from oscilab.display import TimeMode


@runtime_checkable
class View(Protocol):
    """
    What a plot has to have for `Workspace` to drive it — see the module
    docstring. A protocol: any QWidget with these members is a view, without
    inheriting from this.
    """

    y_range_auto: bool  # True: Y keeps auto-fitting; False: pinned to (y_range_min, y_range_max)
    y_range_min: float
    y_range_max: float

    @property
    def y_unit(self) -> str:
        """The Y axis's unit, shown in the Y-Axis dialog, e.g. "Hz"; "" for none."""
        ...

    @property
    def signals(self) -> list[str]:
        """The labels of the signals it shows — what its rows' Visible toggles and units reach."""
        ...

    @property
    def plot_item(self) -> pg.PlotItem:
        """The `PlotItem` the image exporters render."""
        ...

    def refresh(self, now: float | None = None) -> None:
        """
        Redraw from the current signal buffers. Called every tick with `now`,
        the newest timestamp among all shown signals: relative time mode puts
        it at 0, the same for every plot. Without it, a plot uses its own
        newest sample.
        """
        ...

    def set_theme(self, bg: str, fg: str) -> None:
        """The background color `bg`, and `fg` for the title, axes and text: e.g. "#202f46", "#ffffff"."""
        ...

    def set_time_mode(self, mode: TimeMode) -> None:
        """`"global"` (time as recorded) or `"relative"` (newest sample at 0)."""
        ...

    def set_downsampling(self, enabled: bool) -> None:
        """Min-max curve downsampling; a no-op where it doesn't apply."""
        ...

    def set_visible(self, label: str, visible: bool) -> None:
        """Show or hide signal `label` — its row's Visible toggle."""
        ...

    def update_units(self) -> None:
        """Show the current units (`SignalModel.unit`) of the signals it shows — one has changed."""
        ...

    def set_y_range(self, auto: bool, y_min: float = 0.0, y_max: float = 1.0) -> None:
        """Auto: Y keeps fitting the data. Manual: pinned to [y_min, y_max] (the Y-Axis button)."""
        ...

    def live_y_range(self) -> tuple[float, float]:
        """The Y range currently on screen."""
        ...

    def reset_view(self) -> None:
        """Re-fit to the data (View > Reset View), keeping a pinned Y range."""
        ...

    # A view is a QWidget; removing a plot uses these two of its methods.
    def hide(self) -> None:
        """QWidget's: off the screen."""
        ...

    def deleteLater(self) -> None:  # noqa: N802 — Qt's name
        """QWidget's: deleted once control is back in the event loop."""
        ...
