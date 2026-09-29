"""
SignalRow: collapsed by default, details built on first expand, metrics only
computed while expanded, and settings applied without the dialogs.
"""

import numpy as np
import pytest
from PyQt6 import QtWidgets as qtw

from oscilab.signal_controller import SignalRow
from oscilab.signal_model import SignalModel


@pytest.fixture
def model():
    model = SignalModel(label="s", fs=100.0)
    model.add_data(np.arange(10) / 100, np.arange(10.0))
    return model


@pytest.fixture
def row(qapp, model):  # noqa: ARG001
    return SignalRow(label="s", model=model)


def button_texts(row: SignalRow) -> list[str]:
    return [b.text() for b in row.findChildren(qtw.QPushButton)]


def test_collapsed_by_default_with_no_details_built(row):
    assert not row.expanded
    assert row._details is None
    assert "Filter" not in button_texts(row)
    assert "Visible" in button_texts(row)  # the header is always there


def test_clicking_the_name_expands_and_collapses(row):
    row.name_button.click()
    assert row.expanded
    assert row._details.isVisibleTo(row)
    assert {"Filter", "Scaling"} <= set(button_texts(row))
    assert row.metric_labels["Max"].text().endswith("+9.000")  # filled right away

    row.name_button.click()
    assert not row.expanded
    assert not row._details.isVisibleTo(row)


def test_metrics_are_only_computed_while_expanded(row, model, monkeypatch):
    calls = []
    original = model.compute_metrics
    monkeypatch.setattr(model, "compute_metrics", lambda: calls.append(1) or original())

    row.update_metrics()
    assert calls == []

    row.set_expanded(True)
    calls.clear()
    row.update_metrics()
    assert calls == [1]


def test_settings_applied_while_collapsed_show_once_expanded(row, model):
    assert row.apply_filter(enabled=True, cutoff=10.0, order=2)
    row.apply_scaling(enabled=True, gain=2.0, offset=1.0)
    assert model.sos is not None
    assert (model.gain, model.offset) == (2.0, 1.0)

    row.set_expanded(True)
    assert "red" in row.filter_button.styleSheet()  # "active" highlight
    assert "red" in row.scaling_button.styleSheet()


def test_an_invalid_cutoff_leaves_the_filter_off(row, model):
    assert not row.apply_filter(enabled=True, cutoff=50.0, order=4)  # Nyquist at fs=100
    assert model.sos is None
    assert row.filter_settings["enabled"] is False


def test_derived_signals_cannot_be_filtered(qapp, model):  # noqa: ARG001
    row = SignalRow(label="s", model=model, derived=True)
    row.set_expanded(True)
    assert not row.filter_button.isEnabled()
    assert not row.scaling_button.isEnabled()


def test_visibility_round_trip(row):
    seen = []
    row.visibility_changed.connect(lambda _label, visible: seen.append(visible))
    row.set_visible(False)
    assert not row.visible
    row.set_visible(True)
    assert row.visible
    assert seen == [False, True]
