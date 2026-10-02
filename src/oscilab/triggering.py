"""
Triggering the workspace's time plots (`oscilab.workspace.Workspace`), as a
scope does — the acquisition itself is `oscilab.trigger.Acquisition`: feeding
it its source signal every refresh, capturing the record around each trigger
into every time plot, and the level lines on the plots showing the source,
which set the level when dragged.
"""

from typing import TYPE_CHECKING

import numpy as np

from oscilab.logic_plot import LogicPlot
from oscilab.trigger import Acquisition, TriggerSettings

if TYPE_CHECKING:
    from oscilab.workspace import Workspace


class Triggering:
    """One workspace's trigger: the acquisition while triggering is on, and what it shows on the time plots."""

    def __init__(self, workspace: "Workspace") -> None:
        self.workspace = workspace
        self.acquisition: Acquisition | None = None  # While triggering is on

    def set(self, settings: TriggerSettings | None) -> Acquisition | None:
        """See `Workspace.set_trigger`."""
        workspace = self.workspace
        if settings is None:
            self.acquisition = None
            for plot in workspace.plots.values():
                plot.show_live()
                plot.set_trigger_levels([])
            return None
        if settings.source not in workspace.signals:
            raise ValueError(f"Unknown trigger source '{settings.source}'")
        if settings.span > workspace.duration:
            raise ValueError(
                f"A record of {settings.span:g} s doesn't fit the {workspace.duration:g} s of history kept: "
                "choose less time per division, or a longer duration"
            )
        if self.acquisition is not None:
            self.acquisition.apply(settings)  # Checks them
        else:
            self.acquisition = Acquisition(settings)  # Checks them
            self.acquisition.run()
        self.__update_levels()
        return self.acquisition

    def tick(self) -> None:
        """Feed the acquisition its source; on a trigger, every time plot shows the record (every refresh)."""
        acquisition = self.acquisition
        signals = self.workspace.signals
        if acquisition is None or acquisition.settings.source not in signals:
            return
        event = acquisition.tick(*signals[acquisition.settings.source].snapshot())
        if event is not None:
            self.__show_record(acquisition.settings, event)
        self.__update_levels()

    def __show_record(self, settings: TriggerSettings, event: float) -> None:
        """Every time-based plot shows the record around `event`: the same one, as a scope's channels."""
        start, end = settings.window(event)
        for plot in self.workspace.plots.values():
            record = {}
            for label in plot.signals:
                t, x = self.workspace.signals[label].snapshot()
                # One sample past each end, so the curves run right to the edges
                first = max(int(np.searchsorted(t, start)) - 1, 0)
                last = int(np.searchsorted(t, end, side="right")) + 1
                record[label] = (t[first:last].copy(), x[first:last].copy())
            plot.show_record(record, event, start, end)

    def __update_levels(self) -> None:
        """The trigger level(s) as dashed lines on the plots showing its source, where values are drawn."""
        acquisition = self.acquisition
        for plot in self.workspace.plots.values():
            shows_source = acquisition is not None and acquisition.settings.source in plot.signals
            if not shows_source or isinstance(plot, LogicPlot) or acquisition is None:
                plot.set_trigger_levels([])
                continue
            s = acquisition.settings
            offset = acquisition.detector.offset  # AC coupling: the level is relative to the average
            if s.type == "window":
                levels = [s.low + offset, s.high + offset]
            elif s.type in ("edge", "hysteresis"):
                levels = [s.level + offset]
            else:
                levels = []
            plot.set_trigger_levels(levels, self.__level_moved)

    def __level_moved(self, index: int, value: float) -> None:
        """A level line was dragged: the trigger takes its new level, and the workspace tells `trigger_changed`."""
        acquisition = self.acquisition
        if acquisition is None:
            return
        s = acquisition.settings
        value -= acquisition.detector.offset
        if s.type == "window":
            low, high = (value, s.high) if index == 0 else (s.low, value)
            s.low, s.high = min(low, high), max(low, high)
        else:
            s.level = value
        self.workspace.log(f"Trigger level set to {value:g}")
        if self.workspace.trigger_changed is not None:
            self.workspace.trigger_changed()
