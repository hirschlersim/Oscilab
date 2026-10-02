"""The main window: run(), package exports, File-menu exports, range dialog pre-fill."""

from importlib import resources

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


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_a_toolbar_toggle_thats_on_looks_pressed_in(theme):
    # Without a rule of its own, the style sheet's plain background hides a checked
    # toolbar button's state: Downsampled, on from the start, looked off
    qss = resources.files("oscilab.assets").joinpath(f"style_{theme}.qss").read_text("utf-8")
    assert "QToolBar QToolButton:checked {" in qss


def test_an_export_that_fails_says_so(window, monkeypatch, tmp_path):
    # It used to fail silently: the traceback went to a console a window app doesn't show
    window.register_signal("s", fs=10).add_data([0.0], [1.0])
    target = tmp_path / "no such folder" / "out.csv"
    monkeypatch.setattr(qtw.QFileDialog, "getSaveFileName", lambda *_args: (str(target), ""))
    warnings: list[str] = []
    monkeypatch.setattr(qtw.QMessageBox, "warning", lambda _parent, title, _text: warnings.append(title))

    file_action(window, ".csv").trigger()

    assert warnings == ["Couldn't export the signals as .csv"]


# --- settings refused, Reset View, About, a session in a new window ----------------------------------


@pytest.mark.parametrize(
    ("call", "match"),
    [
        (lambda w: w.set_theme("purple"), "Unknown theme"),
        (lambda w: w.set_fps(25), "fps must be one of"),
        (lambda w: w.set_time_mode("sideways"), "Unknown time mode"),
        (lambda w: w.set_cursors("No Such Plot", enabled=True), "Unknown time-series plot"),
        (lambda w: w.set_duration(0), "must be positive"),
    ],
)
def test_settings_that_dont_exist_are_refused(window, call, match):
    with pytest.raises(ValueError, match=match):
        call(window)


def test_a_duration_the_toolbar_doesnt_offer_is_added_to_it(window):
    window.set_duration(2.5)
    assert window.workspace.duration == 2.5
    assert window._duration_box.currentText() == "2.5 s"


def test_reset_view_waits_for_a_pause(window, monkeypatch):
    messages: list[str] = []
    monkeypatch.setattr(window.statusBar(), "showMessage", lambda text, _timeout=0: messages.append(text))
    resets: list[bool] = []
    monkeypatch.setattr(window.workspace, "reset_views", lambda: resets.append(True))

    assert not window.reset_views_action.isEnabled()  # While running
    window._Oscilab__reset_views()  # As if it got through anyway
    assert messages[-1].endswith("Reset View is only available while paused")
    window.start_stop_action.trigger()  # Paused
    window.reset_views_action.trigger()
    assert resets == [True]


def test_about_shows_the_version(window, monkeypatch):
    shown: list[str] = []
    monkeypatch.setattr(qtw.QMessageBox, "about", lambda _parent, _title, text: shown.append(text))
    help_menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "Help")
    next(a for a in help_menu.actions() if a.text() == "About Oscilab").trigger()
    assert "Oscilab" in shown[0]


def test_a_session_that_fails_in_a_new_window_leaves_no_window_behind(window, tmp_path):
    window.register_signal("s", fs=10)  # In use: the session goes to a new window
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    before = set(Oscilab._extra_windows)
    with pytest.raises(ValueError):  # noqa: PT011 — pydantic's, its wording isn't ours
        window.open_session(broken)
    assert Oscilab._extra_windows == before
