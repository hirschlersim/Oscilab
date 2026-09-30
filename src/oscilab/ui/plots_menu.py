"""
The Plots menu — a new plot of any kind, from a dialog — and each time
plot's Signals... button; and the Math menu, a new signal as A op B.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from PyQt6 import QtGui as qtg
from PyQt6 import QtWidgets as qtw

from oscilab.dialogs import HistogramDialog, MathDialog, PlotDialog, SpectrogramDialog, XYDialog
from oscilab.math_signal import OPERATORS
from oscilab.timeseries_plot import TimeseriesPlot

if TYPE_CHECKING:
    from oscilab.oscilab import Oscilab


class PlotsMenu:
    """The Plots menu and its dialogs."""

    def __init__(self, window: "Oscilab") -> None:
        self.window = window
        menu = window.menuBar().addMenu("Plots")
        menu.menuAction().setToolTip("Add plots; each plot's panel can change its signals or remove it")
        entries = (
            ("Add Time Series Plot...", "A new scrolling plot, showing the signals you pick", self.__add_time_series),
            (
                "Add Logic Plot...",
                "On/off signals (buttons, digital lines) in a lane each, like a logic analyzer",
                self.__add_logic,
            ),
            ("Add XY Plot...", "One signal against another: a joystick, a Lissajous figure", self.__add_xy),
            ("Add Histogram...", "How one signal's values are distributed", self.__add_histogram),
            ("Add Spectrogram...", "A live STFT spectrogram of one signal", self.__add_spectrogram),
        )
        for text, tooltip, open_dialog in entries:
            action = qtg.QAction(text, window)
            action.setToolTip(tooltip)
            action.triggered.connect(open_dialog)
            menu.addAction(action)

    def __add_time_series(self) -> None:
        self.__add_time_plot("Plot", "Time Series Plot", self.window.add_timeseries_plot)

    def __add_logic(self) -> None:
        self.__add_time_plot("Logic", "Logic Plot", self.window.add_logic_plot)

    def __add_time_plot(self, name: str, kind: str, add: Callable[[str], TimeseriesPlot]) -> None:
        dialog = PlotDialog(
            signal_labels=list(self.window.workspace.signals),
            title=self.__free_title(name),
            parent=self.window,
            kind=kind,
        )
        if dialog.exec() != qtw.QDialog.DialogCode.Accepted:
            return
        title, labels = dialog.get_values()
        if not self.__title_is_free(title):
            return
        add(title)
        for label in labels:
            self.window.link(plot=title, label=label)

    def __free_title(self, name: str) -> str:
        """`name 1`, `name 2`, ...: the first no plot has."""
        titles = self.window.workspace.views
        return next(f"{name} {n}" for n in range(1, len(titles) + 2) if f"{name} {n}" not in titles)

    def __title_is_free(self, title: str) -> bool:
        """Whether a plot can take `title`; says why not, if it can't."""
        if not title:
            self.window.log("A plot needs a title", level="warning")
            return False
        if title in self.window.workspace.views:
            self.window.log(f"Plot '{title}' already exists", level="warning")
            return False
        return True

    def __add_xy(self) -> None:
        labels = list(self.window.workspace.signals)
        if len(labels) < 2:
            self.window.log("An XY plot needs two signals", level="warning")
            return
        dialog = XYDialog(signal_labels=labels, title=self.__free_title("XY"), parent=self.window)
        if dialog.exec() != qtw.QDialog.DialogCode.Accepted:
            return
        title, x, y, trail = dialog.get_values()
        if not self.__title_is_free(title):
            return
        try:
            self.window.add_xy_plot(title, x, y, trail=trail)
        except ValueError as e:  # The same signal twice: say so (this is a Qt slot)
            self.window.log(str(e), level="warning")

    def __add_histogram(self) -> None:
        workspace = self.window.workspace
        if not workspace.signals:
            self.window.log("No signals yet", level="warning")
            return
        dialog = HistogramDialog(signal_labels=list(workspace.signals), parent=self.window)
        if dialog.exec() != qtw.QDialog.DialogCode.Accepted:
            return
        label, bins, value_range = dialog.get_values()
        if label is None:
            return
        title, n = f"Histogram: {label}", 2
        while title in workspace.views:  # Another of the same signal
            title, n = f"Histogram: {label} ({n})", n + 1
        try:
            self.window.add_histogram(label, bins=bins, value_range=value_range, title=title)
        except ValueError as e:  # A range from high to low: say so
            self.window.log(str(e), level="warning")

    def __add_spectrogram(self) -> None:
        # A spectrogram is keyed by its signal's name, so a signal can have one
        workspace = self.window.workspace
        candidates = [label for label in workspace.signals if label not in workspace.views]
        if not candidates:
            message = "Every signal already has a spectrogram" if workspace.signals else "No signals yet"
            self.window.log(message, level="warning")
            return
        dialog = SpectrogramDialog(signal_labels=candidates, parent=self.window)
        if dialog.exec() != qtw.QDialog.DialogCode.Accepted:
            return
        label, nperseg = dialog.get_values()
        self.window.add_spectrogram_plot(label, nperseg=nperseg, noverlap=nperseg // 2)

    def edit_signals(self, title: str) -> None:
        """A time plot's Signals... button: tick the signals it shows."""
        shown = list(self.window.workspace.plots[title].curves)
        dialog = PlotDialog(
            signal_labels=list(self.window.workspace.signals),
            shown=shown,
            title=title,
            title_editable=False,
            parent=self.window,
        )
        if dialog.exec() != qtw.QDialog.DialogCode.Accepted:
            return
        _, wanted = dialog.get_values()
        for label in shown:
            if label not in wanted:
                self.window.unlink(plot=title, label=label)
        for label in wanted:
            if label not in shown:
                self.window.link(plot=title, label=label)


class MathMenu:
    """The Math menu: a new signal computed live as A op B."""

    def __init__(self, window: "Oscilab") -> None:
        self.window = window
        menu = window.menuBar().addMenu("Math")
        menu.menuAction().setToolTip("Create a new signal computed live from two existing ones")
        for operator in OPERATORS:
            action = qtg.QAction(operator, window)
            action.setToolTip(f"Create a new signal as A {operator} B, picked from a dialog")
            action.triggered.connect(lambda _checked=False, op=operator: self.__open(op))
            menu.addAction(action)

    def __open(self, operator: str) -> None:
        workspace = self.window.workspace
        signal_labels = list(workspace.signals)
        plot_titles = list(workspace.plots)
        if len(signal_labels) < 2:
            self.window.log("Need at least two signals to do math", level="warning")
            return
        if not plot_titles:
            self.window.log("Add a plot before creating a math signal", level="warning")
            return

        dialog = MathDialog(operator=operator, signal_labels=signal_labels, plot_titles=plot_titles, parent=self.window)
        if dialog.exec() != qtw.QDialog.DialogCode.Accepted:
            return
        signal_a, signal_b, label, plot = dialog.get_values()
        if not label:
            self.window.log("Math signal needs a name", level="warning")
            return
        if label in workspace.signals:
            self.window.log(f"Signal '{label}' already exists", level="warning")
            return

        workspace.add_math_signal(label=label, operator=operator, signal_a=signal_a, signal_b=signal_b)
        workspace.link(plot=plot, label=label)
