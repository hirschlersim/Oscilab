"""
Your own toolbar controls (`Oscilab.add_toolbar`, `add_toolbar_button`,
`add_toolbar_toggle`), and the QAction any `@button` / `@toggle` becomes —
in the toolbar or in a producer's menu. See `oscilab.toolbar`.
"""

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from PyQt6 import QtCore as qtc
from PyQt6 import QtGui as qtg

from oscilab.producers import DataProducer
from oscilab.toolbar import ToolbarItem, toolbar_items
from oscilab.ui.common import dot_icon, run_safely

if TYPE_CHECKING:
    from oscilab.oscilab import Oscilab


class CustomControls:
    """The window's custom controls, and their toggles' states for sessions."""

    def __init__(self, window: "Oscilab") -> None:
        self.window = window
        self._toggles: dict[str, qtg.QAction] = {}
        self._pending: dict[str, bool] = {}  # Toggle states set before their toolbar was added

    def action(self, item: ToolbarItem, callback: Callable[..., object]) -> qtg.QAction:
        """
        The QAction for a `@button` / `@toggle` control, wherever it goes. A
        toggle shows its state as a green/gray dot in the toolbar, and as a
        check mark in a menu.
        """
        action = qtg.QAction(item.text, self.window)
        action.setToolTip(item.tooltip or item.text)
        if item.kind == "button":
            action.triggered.connect(lambda _checked=False: run_safely(self.window.logger, item.text, callback))
        else:
            action.setCheckable(True)
            action.setChecked(item.checked)
            action.setIcon(dot_icon("green" if item.checked else "gray"))
            action.setIconVisibleInMenu(False)  # So a menu shows its check mark instead
            action.toggled.connect(lambda on: self.__run_toggle(action, callback, on))
        return action

    def __run_toggle(self, action: qtg.QAction, callback: Callable[[bool], object], on: bool) -> None:
        if run_safely(self.window.logger, action.text(), callback, on):
            action.setIcon(dot_icon("green" if on else "gray"))
        else:
            with qtc.QSignalBlocker(action):
                action.setChecked(not on)  # It didn't take: show the state it's still in

    def add_toolbar(self, tools: object) -> list[qtg.QAction]:
        """See `Oscilab.add_toolbar`."""
        if isinstance(tools, DataProducer):
            # Its methods would run on the GUI thread, racing its own reads
            raise TypeError(
                f"{type(tools).__name__} is a producer: mark its methods toolbar=True instead,"
                " and they run on its own thread"
            )
        cls = tools if isinstance(tools, type) else type(tools)
        actions = []
        for item, method in toolbar_items(tools):
            action = self.action(item, method)
            if item.kind == "toggle":
                self.__register(f"{cls.__name__}.{item.name}", action)
            actions.append(action)
        self.window.toolbar.addActions(actions)
        self.window.toolbar.addSeparator()  # Keeps each class's controls together
        return actions

    def add_button(self, text: str, callback: Callable[[], object], tooltip: str = "") -> qtg.QAction:
        """See `Oscilab.add_toolbar_button`."""
        action = self.action(ToolbarItem("button", text, tooltip), callback)
        self.window.toolbar.addAction(action)
        return action

    def add_toggle(
        self, text: str, callback: Callable[[bool], object], checked: bool = False, tooltip: str = ""
    ) -> qtg.QAction:
        """See `Oscilab.add_toolbar_toggle`."""
        action = self.action(ToolbarItem("toggle", text, tooltip, checked), callback)
        self.__register(text, action)
        self.window.toolbar.addAction(action)
        return action

    def __register(self, key: str, action: qtg.QAction) -> None:
        # A second instance of the same class gets "Class.method#2", ...
        unique, n = key, 1
        while unique in self._toggles:
            n += 1
            unique = f"{key}#{n}"
        self._toggles[unique] = action
        if unique in self._pending:
            action.setChecked(self._pending.pop(unique))  # Runs its method, as a click would

    def toggle_states(self) -> dict[str, bool]:
        """See `Oscilab.toggle_states`."""
        return {**self._pending, **{key: action.isChecked() for key, action in self._toggles.items()}}

    def set_toggle_states(self, states: Mapping[str, bool]) -> None:
        """See `Oscilab.set_toggle_states`."""
        for key, on in states.items():
            if key in self._toggles:
                self._toggles[key].setChecked(on)
            else:
                self._pending[key] = on
