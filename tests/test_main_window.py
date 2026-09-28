"""The main window's File-menu exports, and the range dialogs' pre-fill."""

import numpy as np
import pytest
from PyQt6 import QtWidgets as qtw

from oscilab import workspace as workspace_module
from oscilab.axis_dialog import RangeDialog
from oscilab.oscilab import Oscilab


@pytest.fixture
def window(qapp):
    oszi = Oscilab(app=qapp)
    yield oszi
    oszi.splash.close()
    oszi.close()


def file_action(window: Oscilab, text: str):
    file_menu = window.menuBar().actions()[0].menu()
    return next(a for a in file_menu.actions() if a.text() == text)


def test_file_menu_lists_every_export(window):
    file_menu = window.menuBar().actions()[0].menu()
    assert [a.text() for a in file_menu.actions()] == [".png", ".svg", ".pdf", ".csv", ".mat", ".h5"]


@pytest.mark.parametrize("suffix", [".csv", ".png"])
def test_export_writes_the_chosen_file(window, tmp_path, monkeypatch, suffix):
    model = window.reg_signal("s", fs=10)
    model.add_data(np.arange(5) / 10, np.arange(5.0))
    window.add_timeseries_plot("P")
    window.link(plot="P", label="s")
    target = tmp_path / f"out{suffix}"
    monkeypatch.setattr(qtw.QFileDialog, "getSaveFileName", lambda *_args: (str(target), ""))

    file_action(window, suffix).trigger()

    assert target.exists()


def test_export_with_nothing_to_export_skips_the_dialog(window, monkeypatch):
    def fail(*_args):
        pytest.fail("save dialog opened with nothing to export")

    monkeypatch.setattr(qtw.QFileDialog, "getSaveFileName", fail)
    file_action(window, ".png").trigger()  # no plots
    file_action(window, ".csv").trigger()  # no signals


@pytest.mark.parametrize(("auto", "expected"), [(True, (True, -3.0, 3.0)), (False, (False, 0.5, 1.5))])
def test_range_dialog_prefills_what_is_in_effect(qapp, monkeypatch, auto, expected):  # noqa: ARG001
    # Accept the dialog untouched: what comes back is exactly what it was pre-filled with.
    monkeypatch.setattr(RangeDialog, "exec", lambda _self: qtw.QDialog.DialogCode.Accepted)
    chosen = workspace_module._ask_range(None, "Y", auto=auto, pinned=(0.5, 1.5), live=(-3.0, 3.0))
    assert chosen == expected


def test_range_dialog_cancel_returns_none(qapp, monkeypatch):  # noqa: ARG001
    monkeypatch.setattr(RangeDialog, "exec", lambda _self: qtw.QDialog.DialogCode.Rejected)
    assert workspace_module._ask_range(None, "Y", auto=True, pinned=(0, 1), live=(0, 1)) is None
