"""
The trigger's controls (`oscilab.trigger`): the toolbar's Trigger button,
its status and drop-down of commands, and the panel atop the sidebar.
"""

from typing import TYPE_CHECKING

from PyQt6 import QtCore as qtc
from PyQt6 import QtGui as qtg

from oscilab.trigger import TIME_PER_DIV, TriggerSettings, autoset
from oscilab.trigger_panel import TriggerPanel
from oscilab.ui.common import dot_icon

if TYPE_CHECKING:
    from oscilab.oscilab import Oscilab


class TriggerControls:
    """The Trigger button, its commands and its panel, for one window."""

    def __init__(self, window: "Oscilab") -> None:
        self.window = window
        self.action = qtg.QAction("Trigger", window)
        self.action.setCheckable(True)
        self.action.setIcon(dot_icon("gray"))
        self.__shown = ("Trigger", "gray")  # The button's text and dot color
        self.action.setToolTip(
            "Trigger the time plots, as a scope does: each shows the record around a trigger, held until the next.\n"
            "Its settings: the Trigger panel; Run, Single, Stop and Force: the drop-down"
        )
        self.action.toggled.connect(self.__set_triggering)
        self.commands: list[qtg.QAction] = []  # The toolbar button's drop-down
        for text, tip, command in (
            ("Run", "Acquire record after record", "run"),
            ("Single", "Acquire one record, then stop", "single"),
            ("Stop", "Stop acquiring; the record on show stays", "stop"),
            ("Force Trigger", "Take a record now, trigger or not", "force"),
            ("Autoset", "Level and timebase from the source signal", "autoset"),
        ):
            action = qtg.QAction(text, window)
            action.setToolTip(tip)
            action.triggered.connect(lambda _checked=False, c=command: self.__command(c))
            self.commands.append(action)

        self.panel = TriggerPanel()
        self.panel.hide()
        window.controller_layout.insertWidget(0, self.panel)  # Atop the sidebar, above the plots' panels
        self.panel.changed.connect(self.__edited)
        for signal, command in (
            (self.panel.run_requested, "run"),
            (self.panel.single_requested, "single"),
            (self.panel.stop_requested, "stop"),
            (self.panel.force_requested, "force"),
            (self.panel.autoset_requested, "autoset"),
        ):
            signal.connect(lambda c=command: self.__command(c))
        window.workspace.trigger_changed = self.__moved

    @property
    def settings(self) -> TriggerSettings | None:
        acquisition = self.window.workspace.trigger
        return acquisition.settings if acquisition is not None else None

    def set(self, settings: TriggerSettings | None) -> None:
        """See `Oscilab.set_trigger`."""
        workspace = self.window.workspace
        workspace.set_trigger(settings)
        with qtc.QSignalBlocker(self.action):
            self.action.setChecked(settings is not None)
        self.panel.setVisible(settings is not None)
        if settings is not None:
            self.panel.set_signals(list(workspace.signals))
            self.panel.set_settings(settings)
            self.panel.set_unit(workspace.signals[settings.source].unit)
        self.update_status()

    def __set_triggering(self, on: bool) -> None:
        if not on:
            self.set(None)
            self.window.log("Trigger off: the plots scroll again")
            return
        source = self.__default_source()
        if source is None:
            self.window.log("No signals to trigger on", level="warning")
            with qtc.QSignalBlocker(self.action):
                self.action.setChecked(False)
            return
        self.set(self.__autoset(source))
        self.window.log(f"Triggering on '{source}'")

    def __default_source(self) -> str | None:
        """The first signal a time plot shows — or, with none shown, the first signal."""
        workspace = self.window.workspace
        for plot in workspace.plots.values():
            if plot.signals:
                return plot.signals[0]
        return next(iter(workspace.signals), None)

    def __autoset(self, source: str) -> TriggerSettings:
        """Auto Setup on `source`, the record kept to what the buffers hold."""
        workspace = self.window.workspace
        settings = autoset(source, *workspace.signals[source].snapshot())
        fitting = [step for step in TIME_PER_DIV if 10 * step <= workspace.duration]
        if fitting and settings.time_per_div > fitting[-1]:
            settings.time_per_div = fitting[-1]
        return settings

    def __edited(self) -> None:
        settings = self.panel.settings()
        try:
            self.window.workspace.set_trigger(settings)
        except ValueError as e:  # E.g. a window upside down, or a record longer than the buffers
            self.window.log(str(e), level="warning")
            return
        self.panel.set_unit(self.window.workspace.signals[settings.source].unit)

    def __moved(self) -> None:
        """A level line was dragged on a plot: the panel shows the new level."""
        if self.window.workspace.trigger is not None:
            self.panel.set_settings(self.window.workspace.trigger.settings)

    def __command(self, command: str) -> None:
        acquisition = self.window.workspace.trigger
        if acquisition is None:
            self.window.log("Turn the trigger on first", level="warning")
            return
        if command == "autoset":
            self.set(self.__autoset(acquisition.settings.source))
        elif command == "run":
            acquisition.run()
        elif command == "single":
            acquisition.single_shot()
        elif command == "stop":
            acquisition.stop()
        else:
            acquisition.force()
        self.update_status()

    def update_status(self) -> None:
        """The acquisition's status on the toolbar button and in the panel (every refresh)."""
        acquisition = self.window.workspace.trigger
        if acquisition is None:
            if self.action.isChecked():  # Turned off by the workspace: its source went
                self.set(None)
            self.__show("Trigger", "gray")
            return
        text, color = acquisition.status()
        self.__show(f"Trigger: {text}", color)
        self.panel.set_status(text, color)
        self.panel.set_signals(list(self.window.workspace.signals))

    def __show(self, text: str, color: str) -> None:
        """The toolbar button's text and dot — only when they change: each change lays out the toolbar anew."""
        if (text, color) == self.__shown:
            return
        self.__shown = (text, color)
        self.action.setText(text)
        self.action.setIcon(dot_icon(color))
