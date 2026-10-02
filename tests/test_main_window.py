"""The main window: run(), package exports, File-menu exports, range dialog pre-fill."""

import numpy as np
import pytest
from PyQt6 import QtCore as qtc
from PyQt6 import QtWidgets as qtw

import oscilab
from oscilab import plotting
from oscilab.dialogs import RangeDialog
from oscilab.oscilab import Oscilab


@pytest.fixture
def window(qapp):
    scope = Oscilab(app=qapp)
    yield scope
    scope.close()


def test_public_api_is_importable_from_the_package():
    assert oscilab.__all__
    for name in oscilab.__all__:
        assert getattr(oscilab, name) is not None


def test_run_shows_the_window_until_it_is_closed(window):
    qtc.QTimer.singleShot(0, window.close)  # runs once the event loop is up
    assert window.run() == 0
    assert window.splash is None  # Only shown when Oscilab starts the application itself


def file_action(window: Oscilab, text: str):
    file_menu = window.menuBar().actions()[0].menu()
    return next(a for a in file_menu.actions() if a.text() == text)


def test_file_menu_lists_every_export_the_stream_then_sessions(window):
    file_menu = window.menuBar().actions()[0].menu()
    texts = [a.text() for a in file_menu.actions() if not a.isSeparator()]
    assert texts == [
        *(".png", ".svg", ".pdf", ".csv", ".mat", ".h5"),
        *("Save Stream", "Save Stream Settings...", "Open Stream Folder", "Replay Recording..."),
        *("Open Session...", "Save Session..."),
    ]


@pytest.mark.parametrize("suffix", [".png", ".svg", ".pdf", ".csv", ".mat", ".h5"])
def test_export_writes_the_chosen_file(window, tmp_path, monkeypatch, suffix):
    model = window.register_signal("s", fs=10)
    model.add_data(np.arange(5) / 10, np.arange(5.0))
    window.add_timeseries_plot("P")
    window.link(plot="P", label="s")
    target = tmp_path / f"out{suffix}"
    monkeypatch.setattr(qtw.QFileDialog, "getSaveFileName", lambda *_args: (str(target), ""))

    file_action(window, suffix).trigger()

    assert target.exists()
    assert target.stat().st_size > 0


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
    chosen = plotting._ask_range(None, "Y", auto=auto, pinned=(0.5, 1.5), live=(-3.0, 3.0))
    assert chosen == expected


def test_range_dialog_cancel_returns_none(qapp, monkeypatch):  # noqa: ARG001
    monkeypatch.setattr(RangeDialog, "exec", lambda _self: qtw.QDialog.DialogCode.Rejected)
    assert plotting._ask_range(None, "Y", auto=True, pinned=(0, 1), live=(0, 1)) is None
