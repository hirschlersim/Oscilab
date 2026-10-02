"""
Triggering in the workspace: the time-based plots show records around a
trigger instead of scrolling, all the same record, with level lines to drag.
"""

from dataclasses import replace

import numpy as np
import pytest

from oscilab.trigger import TriggerSettings
from oscilab.workspace import Workspace

FS = 1_000.0


class Feed:
    """Feeds signals chunk by chunk, refreshing the workspace after each, as the app does."""

    def __init__(self, workspace: Workspace, **signals):
        self.workspace = workspace
        self.functions = signals  # label -> f(t)
        self.models = {label: workspace.register_signal(label, fs=FS) for label in signals}
        self.n = 0

    def run(self, seconds: float, chunk: float = 0.02) -> None:
        for _ in range(round(seconds / chunk)):
            t = (self.n + np.arange(round(chunk * FS))) / FS
            self.n += t.size
            for label, f in self.functions.items():
                self.models[label].add_data(t, f(t))
            self.workspace.refresh()


def sine(t):
    return np.sin(2 * np.pi * 2 * t)  # 2 Hz: rising zero crossings at 0, 0.5, 1.0, ...


def trigger(**changes) -> TriggerSettings:
    base = TriggerSettings(source="s", level=0.0, time_per_div=0.01, reference=0.2, auto=False)  # 100 ms records
    return replace(base, **changes)


@pytest.fixture
def feed(workspace):
    workspace.set_downsampling(False)  # Every sample drawn: the curves are exactly the records
    feed = Feed(workspace, s=sine, c=lambda t: np.cos(2 * np.pi * 2 * t))
    for title, labels in (("A", ["s"]), ("B", ["c"])):
        workspace.add_plot(title)
        for label in labels:
            workspace.link(plot=title, label=label)
    return feed


def drawn(plot, label: str) -> tuple[np.ndarray, np.ndarray]:
    x, y = plot.curves[label].getData()
    return np.asarray(x), np.asarray(y)


def test_plots_scroll_until_the_first_record_then_hold_it(workspace, feed):
    feed.run(0.2)
    workspace.set_trigger(trigger())
    plot = workspace.plots["A"]
    feed.run(0.2)
    assert not plot.showing_record  # Armed at 0.2 s; the next crossing is at 0.5 s
    feed.run(0.4)

    assert plot.showing_record
    t, y = drawn(plot, "s")
    assert t[0] <= -0.02  # From 20% of 100 ms before the trigger...
    assert t[-1] >= 0.08  # ...to the rest after it, times counted from it
    assert np.interp(0.0, t, y) == pytest.approx(0.0, abs=0.01)  # The trigger: rising through 0 at t 0
    assert plot.getViewBox().viewRange()[0] == pytest.approx([-0.02, 0.08])
    assert "from trigger" in plot.getAxis("bottom").labelString()


def test_every_time_plot_shows_the_same_record(workspace, feed):
    workspace.set_trigger(trigger())
    feed.run(0.7)
    t_s, _ = drawn(workspace.plots["A"], "s")
    t_c, y_c = drawn(workspace.plots["B"], "c")
    np.testing.assert_allclose(t_c, t_s)  # The same stretch of time, around the same trigger
    assert np.interp(0.0, t_c, y_c) == pytest.approx(1.0, abs=0.01)  # The cosine at its peak then


def test_a_record_is_held_until_the_next_trigger(workspace, feed):
    workspace.set_trigger(trigger(level=0.99, type="edge"))  # Only near the sine's peaks
    feed.run(0.3)
    held = drawn(workspace.plots["A"], "s")[1].copy()
    feed.run(0.1)  # More data, no new trigger
    np.testing.assert_allclose(drawn(workspace.plots["A"], "s")[1], held)


def test_cursors_read_the_record_on_show(workspace, feed):
    workspace.set_trigger(trigger())
    feed.run(0.7)
    plot = workspace.plots["A"]
    workspace.set_cursors("A", enabled=True)
    plot.move_cursors(0.0, 0.05)
    values = plot.cursor_readout()["signals"]["s"]
    assert values["value_a"] == pytest.approx(0.0, abs=0.02)  # At the trigger
    assert values["value_b"] == pytest.approx(np.sin(2 * np.pi * 2 * 0.05), abs=0.02)  # 50 ms after it


@pytest.mark.usefixtures("feed")
def test_the_level_is_a_line_to_drag_on_the_plots_showing_the_source(workspace):
    told = []
    workspace.trigger_changed = lambda: told.append(True)
    workspace.set_trigger(trigger(level=0.25))
    plot_a, plot_b = workspace.plots["A"], workspace.plots["B"]
    (line,) = plot_a._level_lines
    assert line.value() == pytest.approx(0.25)
    assert plot_b._level_lines == []  # B doesn't show the source

    line.setValue(0.5)
    line.sigPositionChangeFinished.emit(line)  # As when a drag ends
    assert workspace.trigger.settings.level == pytest.approx(0.5)
    assert told


@pytest.mark.usefixtures("feed")
def test_a_window_trigger_has_two_lines(workspace):
    workspace.set_trigger(trigger(type="window", low=-0.2, high=0.3))
    assert [line.value() for line in workspace.plots["A"]._level_lines] == pytest.approx([-0.2, 0.3])


def test_turning_it_off_goes_back_to_scrolling(workspace, feed):
    workspace.set_trigger(trigger())
    feed.run(0.7)
    workspace.set_trigger(None)
    plot = workspace.plots["A"]
    assert not plot.showing_record
    assert plot._level_lines == []
    assert plot._trigger_mark is None
    assert "from trigger" not in plot.getAxis("bottom").labelString()


@pytest.mark.usefixtures("feed")
@pytest.mark.parametrize(
    ("changes", "message"),
    [({"source": "nope"}, "Unknown trigger source"), ({"time_per_div": 2.0}, "doesn't fit the 10 s")],
)
def test_impossible_triggers_are_refused(workspace, changes, message):
    with pytest.raises(ValueError, match=message):
        workspace.set_trigger(trigger(**changes))
    assert workspace.trigger is None


def test_removing_the_source_turns_it_off(workspace):
    workspace.register_signal("s", fs=FS)
    workspace.set_trigger(trigger())
    workspace.remove_signal("s")
    assert workspace.trigger is None


@pytest.mark.usefixtures("feed")
@pytest.mark.parametrize(
    ("line", "to", "low", "high"),
    [
        (0, -0.4, -0.4, 0.3),  # The low end
        (1, 0.6, -0.2, 0.6),  # The high end
        (0, 0.5, 0.3, 0.5),  # The low end dragged past the high one: they swap
    ],
)
def test_dragging_a_window_line_moves_its_end(workspace, line, to, low, high):
    workspace.set_trigger(trigger(type="window", low=-0.2, high=0.3))
    dragged = workspace.plots["A"]._level_lines[line]
    dragged.setValue(to)
    dragged.sigPositionChangeFinished.emit(dragged)
    settings = workspace.trigger.settings
    assert (settings.low, settings.high) == pytest.approx((low, high))


@pytest.mark.usefixtures("feed")
def test_an_immediate_trigger_has_no_level_lines(workspace):
    workspace.set_trigger(trigger(type="immediate"))
    assert workspace.plots["A"]._level_lines == []


@pytest.mark.usefixtures("feed")
def test_a_line_dragged_after_the_trigger_went_off_changes_nothing(workspace):
    workspace.set_trigger(trigger(level=0.25))
    (line,) = workspace.plots["A"]._level_lines
    workspace.set_trigger(None)
    line.sigPositionChangeFinished.emit(line)  # A drag that ends just after
    assert workspace.trigger is None
