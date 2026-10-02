"""
The plots' grid, drawn fast: in solid colors, behind the data.

pyqtgraph draws grid lines in the axis's color made translucent, over the
data — and Qt's raster engine draws translucent lines many times slower
than solid ones: the grid took about 25 ms of every frame, per plot, in a
1280 x 650 window (see `scripts/benchmark_refresh.py`). `GridPlotWidget`'s
axes draw the same lines in solid colors — each one the translucent color as
it looks over the plot's background — and behind the data, so the curves
stay on top of them, as on a scope's graticule.
"""

import pyqtgraph as pg
from PyQt6 import QtGui as qtg

GRID_ALPHA = 0.3  # How strongly the grid shows, from 0 to 1


class GridAxis(pg.AxisItem):
    """An axis whose ticks and grid lines are solid: their translucent colors blended over the plot's background."""

    def generateDrawSpecs(self, p: qtg.QPainter) -> tuple | None:  # noqa: N802 - pyqtgraph's name
        specs = super().generateDrawSpecs(p)
        if specs is None:
            return None
        axis_spec, tick_specs, text_specs = specs
        background = self.__background()
        return axis_spec, [(solid(pen, background), p1, p2) for pen, p1, p2 in tick_specs], text_specs

    def __background(self) -> qtg.QColor:
        scene = self.scene()
        views = scene.views() if scene is not None else []
        return views[0].backgroundBrush().color() if views else qtg.QColor("black")


def solid(pen: qtg.QPen, background: qtg.QColor) -> qtg.QPen:
    """`pen` in the solid color its translucent one has over `background`."""
    color = pen.color()
    alpha = color.alphaF()
    if alpha >= 1.0:
        return pen
    blended = qtg.QColor.fromRgbF(
        color.redF() * alpha + background.redF() * (1 - alpha),
        color.greenF() * alpha + background.greenF() * (1 - alpha),
        color.blueF() * alpha + background.blueF() * (1 - alpha),
    )
    pen = qtg.QPen(pen)
    pen.setColor(blended)
    return pen


class GridPlotWidget(pg.PlotWidget):
    """A plot with Oscilab's grid, drawn fast (see the module docstring)."""

    def __init__(self) -> None:
        super().__init__(axisItems={"left": GridAxis("left"), "bottom": GridAxis("bottom")})
        for name in ("left", "bottom"):
            self.getAxis(name).setZValue(-200)  # Behind the data: the plot area (its ViewBox) is at -100
        self.showGrid(x=True, y=True, alpha=GRID_ALPHA)

    def _settle_view(self) -> None:
        """
        Fit the axes to new data and move the curves to match now, not while
        painting, as pyqtgraph otherwise does: moved during a paint, they're
        all painted again right after, twice the painting every frame.
        Subclasses call this once their refresh has set the new data.
        Hidden, a plot isn't painted: it's left for when it's shown.
        """
        if self.isVisible():
            self.getViewBox().prepareForPaint()
