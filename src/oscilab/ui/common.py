"""Small pieces the window's parts share."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6 import QtGui as qtg

if TYPE_CHECKING:
    from loguru import Logger


def dot_icon(color: str) -> qtg.QIcon:
    """A filled circle: the state dots on the toolbar's buttons and in the menus."""
    pixmap = qtg.QPixmap(24, 24)
    pixmap.fill(qtg.QColor("transparent"))
    painter = qtg.QPainter(pixmap)
    painter.setBrush(qtg.QColor(color))
    painter.setPen(qtg.QColor(color))
    painter.drawEllipse(2, 2, 20, 20)
    painter.end()
    return qtg.QIcon(pixmap)


def run_safely(logger: "Logger", name: str, callback: Callable[..., object], *args: object) -> bool:
    """
    Run a button's or menu entry's callback. An exception is reported (status
    bar, log file) instead of raised: raised in a Qt slot, it would abort the
    whole app. Returns whether it ran without one.
    """
    try:
        callback(*args)
    except Exception as e:  # noqa: BLE001 — see the docstring
        logger.opt(exception=e).error(f"'{name}' failed: {type(e).__name__}: {e}")
        return False
    return True
