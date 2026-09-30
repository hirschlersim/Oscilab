"""
The Producers menu: an entry per attached producer, with Start, Stop,
Remove and its own `@button` / `@toggle` functions — which run on its thread
(some also in the toolbar, with `toolbar=True`).
"""

import functools
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, NamedTuple

from PyQt6 import QtGui as qtg
from PyQt6 import QtWidgets as qtw

from oscilab.producers import DataProducer
from oscilab.toolbar import toolbar_items
from oscilab.ui.common import dot_icon, run_safely
from oscilab.ui.controls import CustomControls
from oscilab.workspace import signals_caption

if TYPE_CHECKING:
    from oscilab.oscilab import Oscilab


class _Entry(NamedTuple):
    """What the menu shows for one attached producer."""

    label: str  # One of the signals it feeds, to ask the workspace about it
    menu: qtw.QMenu  # Its submenu
    start: qtg.QAction
    stop: qtg.QAction
    functions: list[qtg.QAction]  # Its @button / @toggle methods, some also in the toolbar
    toolbar_separator: qtg.QAction | None  # After its toolbar controls, if it has any
    toggles: dict[str, qtg.QAction]  # By method name


class ProducersMenu:
    """The menu, and each producer's entry in it."""

    def __init__(self, window: "Oscilab", controls: CustomControls) -> None:
        self.window = window
        self.controls = controls
        self.menu = window.menuBar().addMenu("Producers")
        self.menu.menuAction().setToolTip("Start and stop the attached producers, and run their functions")
        self.menu.setToolTipsVisible(True)
        self._placeholder: qtg.QAction | None = None
        self.__show_placeholder(True)
        self._entries: dict[DataProducer, _Entry] = {}
        self.menu.aboutToShow.connect(self.update)

    def __show_placeholder(self, show: bool) -> None:
        """The disabled "No producers attached" entry."""
        if show and self._placeholder is None:
            self._placeholder = self.menu.addAction("No producers attached")
            self._placeholder.setEnabled(False)
        elif not show and self._placeholder is not None:
            self.menu.removeAction(self._placeholder)
            self._placeholder = None

    def add(self, producer: DataProducer) -> None:
        """An entry for a producer just attached."""
        self.__show_placeholder(False)
        workspace = self.window.workspace
        labels = workspace.signals_fed_by(producer)
        caption = f"{type(producer).__name__}: {signals_caption(labels)}"
        menu = qtw.QMenu(caption, self.menu)
        self.menu.addMenu(menu)
        menu.setToolTipsVisible(True)
        menu.menuAction().setToolTip(f"Feeds {', '.join(labels)}")
        start = qtg.QAction("Start", menu)
        start.setToolTip("Start producing again (after Stop, or an error)")
        start.triggered.connect(lambda: self.__command(workspace.start_producer, labels[0]))
        stop = qtg.QAction("Stop", menu)
        stop.setToolTip("Stop producing; the data received so far stays")
        stop.triggered.connect(lambda: self.__command(workspace.stop_producer, labels[0]))
        remove = qtg.QAction("Remove", menu)
        remove.setToolTip("Stop it, and remove it along with the signals it feeds")
        remove.triggered.connect(lambda: self.__command(self.window.detach_producer, labels[0]))
        menu.addActions([start, stop, remove])

        items = toolbar_items(producer, required=False)
        if items:
            menu.addSeparator()
        functions, in_toolbar = [], []
        toggles: dict[str, qtg.QAction] = {}
        for item, method in items:
            action = self.controls.action(item, functools.partial(self.__run_on_producer, producer, method))
            menu.addAction(action)
            functions.append(action)
            if item.kind == "toggle":
                toggles[item.name] = action
            if item.toolbar:  # The same action: a toggle shows the same state in both places
                action.setToolTip(f"{action.toolTip()} ({caption})")  # Which producer's, in the toolbar
                in_toolbar.append(action)
        separator = None
        if in_toolbar:
            self.window.toolbar.addActions(in_toolbar)
            separator = self.window.toolbar.addSeparator()

        self._entries[producer] = _Entry(labels[0], menu, start, stop, functions, separator, toggles)
        self.update()

    def remove(self, producer: DataProducer) -> None:
        """Take a detached producer's entry, and its toolbar controls, away."""
        entry = self._entries.pop(producer)
        self.menu.removeAction(entry.menu.menuAction())
        entry.menu.deleteLater()
        for action in entry.functions:
            self.window.toolbar.removeAction(action)
            action.deleteLater()
        if entry.toolbar_separator is not None:
            self.window.toolbar.removeAction(entry.toolbar_separator)
        self.__show_placeholder(not self._entries)

    def update(self) -> None:
        """Start/Stop enabled, and a dot for the state: running green, stopped gray, error red."""
        colors = {"running": "green", "stopped": "gray", "error": "red"}
        for entry in self._entries.values():
            state = self.window.workspace.producer_state(entry.label)
            entry.menu.menuAction().setIcon(dot_icon(colors[state]))
            entry.start.setEnabled(state != "running")
            entry.stop.setEnabled(state == "running")

    def __command(self, command: Callable[[str], object], label: str) -> None:
        run_safely(self.window.logger, command.__name__, command, label)
        self.update()

    @staticmethod
    def __run_on_producer(producer: DataProducer, method: Callable[..., object], *args: object) -> None:
        """Queue a producer's control method onto its own thread, between reads."""
        if not producer.queue_action(functools.partial(method, *args)):
            raise RuntimeError(f"{type(producer).__name__} isn't running")

    def toggle_states(self, producer: DataProducer) -> dict[str, bool]:
        """See `Oscilab.producer_toggle_states`."""
        entry = self._entries.get(producer)
        return {name: action.isChecked() for name, action in entry.toggles.items()} if entry else {}

    def set_toggle_states(self, producer: DataProducer, states: Mapping[str, bool]) -> None:
        """See `Oscilab.set_producer_toggle_states`."""
        entry = self._entries.get(producer)
        toggles = entry.toggles if entry else {}
        for name, on in states.items():
            if name in toggles:
                toggles[name].setChecked(on)
            else:
                self.window.log(f"{type(producer).__name__} has no toggle '{name}'", level="warning")
