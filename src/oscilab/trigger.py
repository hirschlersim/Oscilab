"""
Triggering, as a digitizer does it (NI-SCOPE's terms): what counts as a
trigger, where it sits in the record, and when a record is acquired.

Pure logic, no Qt: `TriggerDetector` finds trigger events in a signal's
samples as they come in; `Acquisition` runs the Run / Single / Stop cycle
and says which record to show. `Workspace` feeds them each refresh and
shows the records in the time-based plots.

The trigger (`TriggerSettings`):

- `type` — `"immediate"`: no condition, a record whenever armed (free
  running); `"edge"`: the source crosses `level` on the `slope` —
  `"rising"`, `"falling"` or `"either"`; `"hysteresis"`: an edge that only
  counts once the source has been `hysteresis` beyond the level on the other
  side first, so noise riding on it can't set it off; `"window"`: the source
  `"entering"` or `"leaving"` the range from `low` to `high`.
- `coupling` — `"dc"`: the source as it is; `"ac"`: its average (over about
  a second) taken off first, so the level is relative to it.
- `holdoff` — after a trigger, how long before another can count.
- `delay` — how long after the trigger event the record's reference point
  is.

The record (horizontal): it spans 10 divisions of `time_per_div`, with the
reference point `reference` of the way in (NI's reference position: 0.2 is
20% pre-trigger). Times in it are shown from the trigger event.

Acquisition: *Run* re-arms after every record; *Single* takes one and
stops; *Stop* holds the record on show. With `auto` (NI's Auto trigger
modifier), a record is taken anyway when no trigger comes for a while, so
the display never freezes waiting; turn it off (Normal) for slow or rare
events. *Force* takes a record right away, trigger or not. Crossing times
are interpolated between samples, so records line up to better than a
sample.
"""

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

TriggerType = Literal["immediate", "edge", "hysteresis", "window"]
Slope = Literal["rising", "falling", "either"]
Coupling = Literal["dc", "ac"]
WindowCondition = Literal["entering", "leaving"]
State = Literal["stopped", "armed", "waiting"]  # waiting: triggered, the rest of the record still to come
RecordKind = Literal["trigger", "auto", "forced", "immediate"]

TRIGGER_TYPES: tuple[TriggerType, ...] = ("immediate", "edge", "hysteresis", "window")
SLOPES: tuple[Slope, ...] = ("rising", "falling", "either")
COUPLINGS: tuple[Coupling, ...] = ("dc", "ac")
TIME_PER_DIV: tuple[float, ...] = tuple(  # 1-2-5, 10 µs to 1 s a division
    step * 10.0**exponent for exponent in range(-5, 1) for step in (1, 2, 5) if step * 10.0**exponent <= 1
)

_AC_TIME_CONSTANT_S = 1.0  # AC coupling takes off the average over about this long


@dataclass
class TriggerSettings:
    """What triggers a record, and how it's laid out — see the module docstring."""

    source: str = ""
    type: TriggerType = "edge"
    slope: Slope = "rising"
    level: float = 0.0
    hysteresis: float = 0.1
    low: float = -0.5
    high: float = 0.5
    condition: WindowCondition = "entering"
    coupling: Coupling = "dc"
    holdoff: float = 0.0  # s
    delay: float = 0.0  # s
    time_per_div: float = 0.01  # s
    reference: float = 0.2  # Where the reference point sits in the record, 0 to 1
    auto: bool = True

    @property
    def span(self) -> float:
        """How long a record is: 10 divisions."""
        return 10 * self.time_per_div

    def window(self, event: float) -> tuple[float, float]:
        """The record around a trigger `event`: from `reference` of the span before its reference point, on."""
        start = event + self.delay - self.reference * self.span
        return start, start + self.span

    @property
    def after(self) -> float:
        """How long after its trigger event a record ends."""
        return self.delay + (1 - self.reference) * self.span

    @property
    def auto_timeout(self) -> float:
        """With Auto, how long without a trigger before a record is taken anyway."""
        return max(self.span, 0.25)

    def check(self) -> None:
        """Refuse settings no digitizer would take."""
        if self.time_per_div <= 0:
            raise ValueError(f"time_per_div must be positive, got {self.time_per_div}")
        if not 0 <= self.reference <= 1:
            raise ValueError(f"reference must be from 0 to 1 (0% to 100%), got {self.reference}")
        if self.holdoff < 0:
            raise ValueError(f"holdoff can't be negative, got {self.holdoff}")
        if self.hysteresis < 0:
            raise ValueError(f"hysteresis can't be negative, got {self.hysteresis}")
        if self.type == "window" and not self.low < self.high:
            raise ValueError(f"a window trigger's low must be below its high, got {self.low} and {self.high}")


class TriggerDetector:
    """Finds trigger events in a source's samples, fed in order, a chunk at a time."""

    def __init__(self, settings: TriggerSettings) -> None:
        self.settings = settings
        self._last: tuple[float, float] | None = None  # (time, coupled value) of the last sample fed
        self._mean: float | None = None  # AC coupling's running average
        self._armed = {"rising": False, "falling": False}  # Hysteresis: been beyond the band, so an edge counts
        self._last_event = -math.inf

    @property
    def offset(self) -> float:
        """What coupling takes off the source before comparing: 0 for DC, its running average for AC."""
        return self._mean if self.settings.coupling == "ac" and self._mean is not None else 0.0

    def feed(self, t: np.ndarray, x: np.ndarray) -> list[float]:
        """Trigger events among these samples (after the ones fed before), holdoff applied."""
        events = []
        holdoff = self.settings.holdoff
        for t1, x1 in zip(t.tolist(), x.tolist(), strict=True):
            if not math.isfinite(x1):
                continue
            y1 = self.__couple(t1, x1)
            if self._last is not None:
                event = self.__event(*self._last, t1, y1)
                if event is not None and event >= self._last_event + holdoff:
                    events.append(event)
                    self._last_event = event
            self._last = (t1, y1)
        return events

    def __couple(self, t: float, x: float) -> float:
        if self.settings.coupling == "dc":
            return x
        if self._mean is None or self._last is None:
            self._mean = x
        else:
            dt = max(t - self._last[0], 0.0)
            self._mean += (1 - math.exp(-dt / _AC_TIME_CONSTANT_S)) * (x - self._mean)
        return x - self._mean

    def __event(self, t0: float, y0: float, t1: float, y1: float) -> float | None:
        """The trigger event between two consecutive samples, if there is one."""
        s = self.settings
        if s.type == "edge":
            return _crossing(t0, y0, t1, y1, s.level, s.slope)
        if s.type == "hysteresis":
            return self.__hysteresis(t0, y0, t1, y1)
        if s.type == "window":
            return self.__window(t0, y0, t1, y1)
        return None  # Immediate: no condition to find

    def __hysteresis(self, t0: float, y0: float, t1: float, y1: float) -> float | None:
        s = self.settings
        event = None
        for slope in ("rising", "falling"):
            if s.slope not in (slope, "either"):
                continue
            crossing = _crossing(t0, y0, t1, y1, s.level, slope)
            if crossing is not None and self._armed[slope]:
                self._armed[slope] = False
                event = crossing if event is None else min(event, crossing)
            beyond = y1 <= s.level - s.hysteresis if slope == "rising" else y1 >= s.level + s.hysteresis
            if beyond:
                self._armed[slope] = True
        return event

    def __window(self, t0: float, y0: float, t1: float, y1: float) -> float | None:
        s = self.settings
        was_inside, inside = s.low <= y0 <= s.high, s.low <= y1 <= s.high
        if s.condition == "entering" and not was_inside and inside:
            return _crossing(t0, y0, t1, y1, s.low if y0 < s.low else s.high, "either")
        if s.condition == "leaving" and was_inside and not inside:
            return _crossing(t0, y0, t1, y1, s.low if y1 < s.low else s.high, "either")
        return None


def _crossing(t0: float, y0: float, t1: float, y1: float, level: float, slope: Slope) -> float | None:
    """When the line between two samples crosses `level` on `slope`, interpolated; None if it doesn't."""
    rising = y0 < level <= y1
    falling = y0 > level >= y1
    if (rising and slope in ("rising", "either")) or (falling and slope in ("falling", "either")):
        return t0 + (level - y0) / (y1 - y0) * (t1 - t0)
    return None


class Acquisition:
    """
    Run / Single / Stop, as on a digitizer: fed the source's buffer every
    refresh, it says when a record is complete and should be shown.
    """

    def __init__(self, settings: TriggerSettings) -> None:
        settings.check()
        self.settings = settings
        self.detector = TriggerDetector(settings)
        self.state: State = "stopped"
        self.single = False  # Stop after the next record
        self.last: RecordKind | None = None  # What the record on show came from
        self._fed_up_to: float | None = None  # Source samples up to here are fed; None: just armed
        self._pending: list[float] = []  # Trigger events whose record isn't complete yet
        self._armed_at: float | None = None  # Source time of the last record, or of arming: Auto counts from it
        self._force = False

    def apply(self, settings: TriggerSettings) -> None:
        """New settings, keeping Run / Single / Stop — armed afresh with them, unless stopped."""
        settings.check()
        self.settings = settings
        self._pending.clear()
        self.detector = TriggerDetector(settings)
        if self.state != "stopped":
            self.__arm()

    @property
    def idle(self) -> float:
        """Seconds of the source since the last record ended (or since arming): how long it's been waiting."""
        if self._fed_up_to is None or self._armed_at is None:
            return 0.0
        return max(self._fed_up_to - self._armed_at, 0.0)

    def status(self) -> tuple[str, str]:
        """(What a scope's status line would say, a color for it): Stopped, Armed, Triggered or Auto."""
        if self.state == "stopped":
            return "Stopped", "#e5484d"
        if self.state == "waiting":
            return "Triggered", "#30a46c"
        if self.last in ("trigger", "immediate") and self.idle < 0.5:
            return "Triggered", "#30a46c"
        if self.last == "auto" and self.idle < self.settings.auto_timeout + 0.5:
            return "Auto", "#4ea3ff"
        return "Armed", "#f0b429"

    def run(self) -> None:
        """Acquire record after record."""
        self.single = False
        self.__arm()

    def single_shot(self) -> None:
        """Acquire one record, then stop."""
        self.single = True
        self.__arm()

    def stop(self) -> None:
        """Stop acquiring: the record on show stays."""
        self.state = "stopped"
        self._pending.clear()

    def force(self) -> None:
        """Take a record at the next refresh, trigger or not (NI's software trigger)."""
        if self.state == "stopped":
            self.single = True
            self.__arm()
        self._force = True

    def __arm(self) -> None:
        self.state = "armed"
        self.last = None  # No record yet, this time
        self._pending.clear()
        self._armed_at = None
        self.detector = TriggerDetector(self.settings)  # Only what comes after arming counts
        self._fed_up_to = None

    def tick(self, t: np.ndarray, x: np.ndarray) -> float | None:
        """
        Feed the source's buffer (`t`, `x`: everything it holds). Returns
        the trigger event of a record to show now — its window, from
        `settings.window(event)`, is all in the buffer — or None.
        """
        if self.state == "stopped" or t.size == 0:
            return None
        s = self.settings
        newest = float(t[-1])
        if self._fed_up_to is None:  # Just armed: from the newest sample on
            self.detector.feed(t[-1:], x[-1:])
            self._fed_up_to = newest
        new = t > self._fed_up_to
        events = self.detector.feed(t[new], x[new]) if np.any(new) else []
        self._fed_up_to = newest
        if self._armed_at is None:
            self._armed_at = newest

        if self._force:
            self._force = False
            return self.__take(newest - s.after, "forced")
        if s.type == "immediate":
            return self.__take(newest - s.after, "immediate")

        self._pending += [event for event in events if event - s.reference * s.span + s.delay >= float(t[0])]
        complete = [event for event in self._pending if event + s.after <= newest]
        if complete:
            event = complete[-1]  # The newest complete one, as a scope's display does
            self._pending = [e for e in self._pending if e > event]
            return self.__take(event, "trigger")
        self.state = "waiting" if self._pending else "armed"
        if s.auto and not self._pending and newest - self._armed_at >= s.auto_timeout:
            return self.__take(newest - s.after, "auto")
        return None

    def __take(self, event: float, kind: RecordKind) -> float:
        self.last = kind
        self._armed_at = event + self.settings.after
        self.state = "stopped" if self.single else "armed"
        return event


def autoset(source: str, t: np.ndarray, x: np.ndarray) -> TriggerSettings:
    """
    Settings that trigger on `source` right away, as a scope's Auto Setup
    picks them from the signal: a rising edge at the middle of its range,
    and time per division showing about two periods (from how often it
    crosses that middle), Auto on.
    """
    finite = np.isfinite(x)
    t, x = t[finite], x[finite]
    settings = TriggerSettings(source=source)
    if x.size < 2:
        return settings
    low, high = float(x.min()), float(x.max())
    settings.level = (low + high) / 2
    settings.hysteresis = (high - low) * 0.05
    # Noise makes a signal cross the level many times on its way through:
    # counted only once it's been well below since its last rising edge
    rising = _rising_edges(x, settings.level, band=(high - low) * 0.25)
    if rising.size >= 2:
        period = float(t[rising[-1]] - t[rising[0]]) / (rising.size - 1)
        wanted = 2 * period / 10  # Two periods over the ten divisions
        settings.time_per_div = min((step for step in TIME_PER_DIV if step >= wanted), default=TIME_PER_DIV[-1])
    return settings


def _rising_edges(x: np.ndarray, level: float, band: float) -> np.ndarray:
    """Indices where `x` reaches `level` from at least `band` below it: a Schmitt trigger's rising edges."""
    state = np.zeros(x.size, dtype=np.int8)
    state[x <= level - band] = -1
    state[x >= level] = 1
    changes = np.flatnonzero(state)  # In between, a Schmitt trigger keeps its last state
    held = state[changes]
    return changes[1:][(held[:-1] == -1) & (held[1:] == 1)]
