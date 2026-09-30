"""
The two-cursor measurement on a time-series plot: what `cursor_readout()`
reports, and the readout text its panel shows.
"""

import numpy as np
import pytest

from oscilab.workspace import Workspace


def ramp(workspace: Workspace, label: str, fs: float = 100.0, slope: float = 2.0, seconds: float = 10.0):
    """Register `label` holding `slope * t` for t in [0, seconds)."""
    model = workspace.register_signal(label, fs=fs)
    t = np.arange(round(seconds * fs)) / fs
    model.add_data(t, slope * t)
    return model


@pytest.fixture
def plot(workspace):
    ramp(workspace, "ramp")
    plot, _ = workspace.add_plot("P")
    workspace.link(plot="P", label="ramp")
    workspace.refresh()
    return plot


def test_nothing_to_read_while_cursors_are_off(plot):
    assert plot.cursor_readout() is None
    with pytest.raises(ValueError, match="Cursors are off"):
        plot.move_cursors(1.0, 2.0)


def test_times_and_values_at_both_cursors(workspace, plot):
    workspace.set_cursors("P", enabled=True)
    plot.move_cursors(2.0, 5.0)

    readout = plot.cursor_readout()
    assert (readout["t_a"], readout["t_b"]) == pytest.approx((2.0, 5.0))
    assert readout["dt"] == pytest.approx(3.0)
    assert readout["freq"] == pytest.approx(1 / 3)
    assert readout["signals"]["ramp"] == pytest.approx({"value_a": 4.0, "value_b": 10.0, "delta": 6.0})


def test_cursors_in_either_order_read_the_same(workspace, plot):
    workspace.set_cursors("P", enabled=True)
    plot.move_cursors(5.0, 2.0)

    readout = plot.cursor_readout()
    assert (readout["t_a"], readout["t_b"], readout["dt"]) == pytest.approx((2.0, 5.0, 3.0))
    assert readout["signals"]["ramp"]["delta"] == pytest.approx(6.0)


def test_a_cursor_between_samples_reads_the_nearest_one(workspace, plot):
    workspace.set_cursors("P", enabled=True)
    plot.move_cursors(2.004, 2.006)  # Samples every 10 ms: 2.00 and 2.01

    values = plot.cursor_readout()["signals"]["ramp"]
    assert (values["value_a"], values["value_b"]) == pytest.approx((4.0, 4.02))


def test_zero_width_has_no_frequency(workspace, plot):
    workspace.set_cursors("P", enabled=True)
    plot.move_cursors(3.0, 3.0)

    readout = plot.cursor_readout()
    assert readout["dt"] == 0
    assert readout["freq"] is None


def test_every_shown_signal_is_read_at_its_own_rate(workspace, plot):
    ramp(workspace, "fast", fs=1_000.0, slope=-1.0)
    ramp(workspace, "empty", seconds=0.0)
    ramp(workspace, "hidden")
    for label in ("fast", "empty", "hidden"):
        workspace.link(plot="P", label=label)
    plot.set_visible("hidden", False)
    workspace.refresh()
    workspace.set_cursors("P", enabled=True)
    plot.move_cursors(1.0, 1.5)

    signals = plot.cursor_readout()["signals"]
    assert set(signals) == {"ramp", "fast"}  # Not the one without data, nor the hidden one
    assert signals["fast"] == pytest.approx({"value_a": -1.0, "value_b": -1.5, "delta": -0.5})


def test_the_panel_shows_the_readout_and_follows_a_drag(workspace, plot):
    label = workspace.controllers["P"]._cursor_label
    workspace.set_cursors("P", enabled=True)
    assert not label.isHidden()

    plot.move_cursors(2.0, 4.0)  # No refresh tick needed: dragging updates it (so it works while paused)
    assert label.text().splitlines() == [
        "Cursors  ΔT: +2.0000 s   1/ΔT: 0.500 Hz",
        "  ramp: A=+4.000  B=+8.000  Δ=+4.000",
    ]

    workspace.set_cursors("P", enabled=False)
    assert label.isHidden()
    assert plot.cursor_readout() is None
