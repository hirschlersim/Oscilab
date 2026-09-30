"""
Signal units: where they come from (producers, `attach_producer`,
`register_signal`, the row's Unit button, math operands) and where they show
(legends, Y axes, rows, cursor readouts, HDF5 exports).
"""

import h5py
import numpy as np
import pytest
from PyQt6 import QtWidgets as qtw

from oscilab.math_signal import derived_unit
from oscilab.producers import SyncDataProducer
from oscilab.workspace import Workspace


class Probe(SyncDataProducer):
    """Two channels with units of their own."""

    channels = ("volts", "amps")
    units = ("V", "A")

    def _generate(self, _elapsed):
        return np.array([]), np.array([[], []])


def legend(plot) -> list[str]:
    return [label.text for _sample, label in plot.plotItem.legend.items]


def y_label(plot) -> str:
    """The Y axis label as shown; "" while it's hidden."""
    axis = plot.getAxis("left")
    return axis.labelString() if axis.label.isVisible() else ""


@pytest.mark.parametrize(
    ("operator", "a", "b", "unit"),
    [
        ("+", "V", "V", "V"),
        ("-", "V", "A", ""),  # Mismatched: no unit makes sense
        ("*", "V", "A", "V·A"),
        ("*", "V", "", "V"),  # Times a plain number
        ("/", "V", "A", "V/A"),
        ("/", "V", "V", ""),  # A ratio
        ("/", "V", "", "V"),
        ("/", "", "s", "1/s"),
    ],
)
def test_a_math_signal_derives_its_unit(operator, a, b, unit):
    assert derived_unit(operator, a, b) == unit


def test_units_come_from_the_producer_unless_given(workspace):
    workspace.attach_producer(Probe(fs=10.0), ["v1", "i1"])
    workspace.attach_producer(Probe(fs=10.0), ["v2", "i2"], units=("mV", "mA"))
    workspace.attach_producer(Probe(fs=10.0), ["v3", "i3"], units="")  # One for every channel
    workspace.register_signal("t", fs=10.0, unit="°C")

    units = {label: model.unit for label, model in workspace.signals.items()}
    assert units == {"v1": "V", "i1": "A", "v2": "mV", "i2": "mA", "v3": "", "i3": "", "t": "°C"}


def test_one_unit_per_channel(workspace):
    with pytest.raises(ValueError, match=r"2 channel\(s\).*1 unit\(s\)"):
        workspace.attach_producer(Probe(fs=10.0), ["v", "i"], units=["V"])
    assert workspace.signals == {}  # Nothing half-registered


def test_legend_and_y_axis_show_the_units(workspace):
    workspace.register_signal("v", fs=10.0, unit="V")
    workspace.register_signal("plain", fs=10.0)
    plot, _ = workspace.add_plot("P")
    assert y_label(plot) == ""  # No units, no label

    workspace.link(plot="P", label="v")
    workspace.link(plot="P", label="plain")
    assert legend(plot) == ["v (V)", "plain"]
    assert "Value (V)" in y_label(plot)
    assert plot.y_unit == "V"  # What the Y-Axis dialog shows

    workspace.register_signal("i", fs=10.0, unit="A")
    workspace.link(plot="P", label="i")
    assert "Value (V, A)" in y_label(plot)
    assert plot.y_unit == ""  # Mixed: no single unit

    workspace.unlink("P", "i")
    workspace.unlink("P", "v")
    assert y_label(plot) == ""


def test_setting_a_unit_updates_everything_showing_it(workspace):
    workspace.register_signal("a", fs=100.0)
    workspace.register_signal("b", fs=100.0, unit="A")
    workspace.add_math_signal("power", "*", "a", "b")
    workspace.add_math_signal("double", "+", "power", "power")
    plot, _ = workspace.add_plot("P")
    for label in ("a", "power", "double"):
        workspace.link(plot="P", label=label)
    spectrogram, _ = workspace.add_spectrogram("a")
    row = workspace.signal_list.rows["a"]
    row.set_expanded(True)

    workspace.set_unit("a", " V ")

    assert workspace.signals["a"].unit == "V"
    assert workspace.signals["power"].unit == "V·A"  # Follows its operands...
    assert workspace.signals["double"].unit == "V·A"  # ...and so on down the chain
    assert legend(plot) == ["a (V)", "power (V·A)", "double (V·A)"]
    assert "Value (V, V·A)" in y_label(plot)
    assert spectrogram.plot.plotItem.titleLabel.text == "a (V)"
    assert row.unit_button.text() == "Unit: V"
    assert row.metric_labels["Min"].text().endswith(" " * 2)  # "—" padded where " V" goes, until there's data


def test_a_math_signals_unit_isnt_set_directly(workspace):
    workspace.register_signal("a", fs=10.0)
    workspace.add_math_signal("twice", "+", "a", "a")
    with pytest.raises(ValueError, match="follows from its operands"):
        workspace.set_unit("twice", "V")
    with pytest.raises(ValueError, match="Unknown signal"):
        workspace.set_unit("missing", "V")


def test_the_rows_unit_button_asks_for_it(workspace, monkeypatch):
    workspace.register_signal("a", fs=10.0)
    workspace.add_math_signal("twice", "+", "a", "a")
    workspace.add_plot("P")
    workspace.link(plot="P", label="a")
    workspace.link(plot="P", label="twice")
    rows = workspace.signal_list.rows
    for row in rows.values():
        row.set_expanded(True)
    asked = []

    def answer(_parent, _title, _label, text=""):
        asked.append(text)
        return "mV", True

    monkeypatch.setattr(qtw.QInputDialog, "getText", answer)
    rows["a"].unit_button.click()
    assert workspace.signals["a"].unit == "mV"
    rows["a"].unit_button.click()
    assert asked == ["", "mV"]  # Pre-filled with the current one

    assert not rows["twice"].unit_button.isEnabled()  # Derived from a's
    assert rows["twice"].unit_button.text() == "Unit: mV"


def test_metrics_show_the_unit(workspace):
    model = workspace.register_signal("a", fs=10.0, unit="V")
    model.add_data(np.arange(3) / 10, [1.0, 2.0, 3.0])
    workspace.add_plot("P")
    workspace.link(plot="P", label="a")
    row = workspace.signal_list.rows["a"]
    row.set_expanded(True)
    assert row.metric_labels["Max"].text() == "Max:    +3.000 V"


def test_the_cursor_readout_names_the_unit(workspace: Workspace):
    model = workspace.register_signal("a", fs=10.0, unit="V")
    model.add_data(np.arange(50) / 10, np.arange(50) / 10)
    plot, controller = workspace.add_plot("P")
    workspace.link(plot="P", label="a")
    workspace.set_cursors("P", enabled=True)
    plot.move_cursors(1.0, 2.0)
    assert controller._cursor_label.text().splitlines()[1].startswith("  a (V): A=+1.000")


def test_hdf5_exports_carry_the_unit(workspace, tmp_path):
    workspace.register_signal("a", fs=10.0, unit="V")
    path = workspace.export_h5(tmp_path / "data")
    with h5py.File(path) as f:
        assert f["a"].attrs["unit"] == "V"
