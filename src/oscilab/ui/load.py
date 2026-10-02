"""
The status bar's Load: how busy the GUI thread is — taking in the producers'
data, refreshing the plots and painting them, everything it does — as a
share of the time. Near 100 %, it can't keep up: frames get dropped, data
waits, and the window answers slowly.
"""

import time
from typing import TYPE_CHECKING

from PyQt6 import QtCore as qtc
from PyQt6 import QtWidgets as qtw

if TYPE_CHECKING:
    from oscilab.oscilab import Oscilab


class BusyMeter(qtc.QObject):
    """
    How long the event loop of the thread it's made on is busy: from when it
    wakes up until it goes back to waiting. Made on the GUI thread, that's
    everything the GUI thread does, painting included.
    """

    def __init__(self, parent: qtc.QObject | None = None) -> None:
        super().__init__(parent)
        dispatcher = qtc.QAbstractEventDispatcher.instance()
        if dispatcher is None:
            raise RuntimeError("No event dispatcher on this thread: create the QApplication first")
        dispatcher.awake.connect(self.__awake)
        dispatcher.aboutToBlock.connect(self.__about_to_block)
        self.__busy = 0.0  # Seconds, up to the last time it waited
        self.__awake_since: float | None = time.perf_counter()  # None while waiting

    @qtc.pyqtSlot()
    def __awake(self) -> None:
        if self.__awake_since is None:
            self.__awake_since = time.perf_counter()

    @qtc.pyqtSlot()
    def __about_to_block(self) -> None:
        if self.__awake_since is not None:
            self.__busy += time.perf_counter() - self.__awake_since
            self.__awake_since = None

    def busy(self) -> float:
        """Seconds busy since it was made."""
        running = time.perf_counter() - self.__awake_since if self.__awake_since is not None else 0.0
        return self.__busy + running


class LoadBar:
    """The status bar's "Load [%]" and its bar, for one window."""

    SMOOTHING = 0.3  # Each refresh moves the bar this share of the way: one slow frame doesn't make it flicker

    def __init__(self, window: "Oscilab") -> None:
        tooltip = (
            "How busy the GUI thread is: taking in data, refreshing and painting the plots.\n"
            "Near 100 %, it can't keep up: frames are dropped and the window answers slowly"
        )
        self.label = qtw.QLabel("Load [%]:")
        self.label.setToolTip(tooltip)
        self.bar = qtw.QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setToolTip(tooltip)
        self.meter = BusyMeter(window)
        self.__last = (time.perf_counter(), self.meter.busy())
        self.load = 0.0  # Share of the time busy, smoothed

    def update(self) -> None:
        """Take the share of the time busy since the last call (every refresh)."""
        now, busy = time.perf_counter(), self.meter.busy()
        last_now, last_busy = self.__last
        self.__last = (now, busy)
        if now <= last_now:
            return
        share = (busy - last_busy) / (now - last_now)
        self.load += (share - self.load) * self.SMOOTHING
        self.bar.setValue(min(round(100 * self.load), 100))
