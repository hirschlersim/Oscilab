"""
Save Stream in the window: the toolbar button and its drop-down, the File
menu entries, the settings, and recording while producers run.
"""

import time

import h5py
import numpy as np
import pytest
from PyQt6 import QtCore as qtc
from PyQt6 import QtGui as qtg
from PyQt6 import QtWidgets as qtw

from oscilab import SyntheticProducer
from oscilab.dialogs import StreamDialog
from oscilab.oscilab import Oscilab
from oscilab.recorder import Recorder


@pytest.fixture
def window(qapp, tmp_path):
    scope = Oscilab(app=qapp)
    scope.set_recording_options(tmp_path / "streams" / "run.h5", max_file_mb=None)
    yield scope
    scope.close()


@pytest.fixture
def shown(window, monkeypatch):
    """What the status bar showed."""
    messages: list[str] = []
    monkeypatch.setattr(window.statusBar(), "showMessage", lambda text, _timeout=0: messages.append(text))
    return messages


def wait_until(qapp, condition, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for condition"
        qapp.processEvents()
        time.sleep(0.005)


def samples_in(path, label: str) -> np.ndarray:
    with h5py.File(path) as f:
        return f[label]["data"][:]


def test_the_toolbar_button_has_the_settings_in_its_drop_down(window):
    button = window.toolbar.widgetForAction(window.stream_action)
    assert isinstance(button, qtw.QToolButton)
    assert button.popupMode() == qtw.QToolButton.ToolButtonPopupMode.MenuButtonPopup
    assert [a.text() for a in button.menu().actions()] == ["Save Stream Settings...", "Open Stream Folder"]

    file_menu = window.menuBar().actions()[0].menu()
    assert window.stream_action in file_menu.actions()  # The same toggle: on in one is on in the other


def test_saves_what_producers_send_until_its_turned_off(qapp, window, shown):
    window.attach_producer(SyntheticProducer(fs=1_000), "s")
    window.workspace.add_math_signal("twice", "+", "s", "s")

    window.stream_action.trigger()
    assert window.recording
    assert not window._stream_label.isHidden()  # Shown in the status bar
    assert "Saving: run_" in window._stream_label.text()
    wait_until(qapp, lambda: len(window.workspace.signals["s"].data) > 200)
    window.stream_action.trigger()

    assert not window.recording
    assert window._stream_label.isHidden()
    assert not window.stream_action.isChecked()
    assert shown[-1].startswith("✓ Stream saved: 1 file(s) in")
    (path,) = (window.recording_options[0].parent).glob("run_*_001.h5")
    with h5py.File(path) as f:
        assert set(f) == {"s"}  # Not the math signal: it's recomputed from the others
    assert len(samples_in(path, "s")) >= 200


def test_it_keeps_saving_while_the_display_is_paused(window):
    model = window.register_signal("s", fs=10)
    window.start_stop_action.trigger()  # Paused: the plots stop updating...
    window.set_recording(True)
    model.add_data(np.arange(5) / 10, np.arange(5.0))
    window.set_recording(False)
    (path,) = window.recording_options[0].parent.glob("run_*.h5")
    np.testing.assert_allclose(samples_in(path, "s"), np.arange(5.0))  # ...the recording doesn't


def test_nothing_to_save_is_reported(window, shown):
    window.set_recording(True)
    assert not window.recording
    assert not window.stream_action.isChecked()
    assert shown[-1] == "✕ Couldn't save the stream: There are no signals to record"


def test_a_path_that_cant_be_written_is_reported(window, shown, tmp_path):
    (tmp_path / "taken").write_text("a file, not a folder")
    window.set_recording_options(tmp_path / "taken" / "run.h5")
    window.register_signal("s", fs=10)
    window.set_recording(True)
    assert not window.recording
    assert shown[-1].startswith("✕ Couldn't save the stream:")


def test_a_write_error_turns_it_off_and_says_so(qapp, window, shown, monkeypatch):
    def fail(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(Recorder, "_Recorder__append", staticmethod(fail))
    model = window.register_signal("s", fs=10)
    window.set_recording(True)
    recorder = window._recorder
    model.add_data([0.0], [1.0])
    wait_until(qapp, lambda: recorder.error is not None)
    window._stream_timer.timeout.emit()  # Its next status check

    assert not window.recording
    assert not window.stream_action.isChecked()
    assert shown[-1] == "✕ Saving the stream stopped: OSError: disk full"


def test_the_settings_dialog_sets_the_path_and_file_size(window, tmp_path, monkeypatch):
    target = tmp_path / "elsewhere" / "capture.h5"
    monkeypatch.setattr(qtw.QFileDialog, "getSaveFileName", lambda *_args: (str(target), ""))

    def fill_in(dialog: StreamDialog) -> qtw.QDialog.DialogCode:
        assert dialog.edit_path.text() == str(tmp_path / "streams" / "run.h5")  # Shows what's set
        assert dialog.spin_size.text() == "No limit"
        next(b for b in dialog.findChildren(qtw.QPushButton) if b.text() == "Browse...").click()
        dialog.spin_size.setValue(250)
        return qtw.QDialog.DialogCode.Accepted

    monkeypatch.setattr(StreamDialog, "exec", fill_in)
    window.stream_settings_action.trigger()
    assert window.recording_options == (target, 250.0)


def test_a_size_limit_is_in_megabytes(window, monkeypatch):
    sizes = []
    monkeypatch.setattr(
        Recorder, "__init__", lambda _self, _models, _path, max_file_bytes: sizes.append(max_file_bytes)
    )
    monkeypatch.setattr(Recorder, "start", lambda _self: None)
    window.register_signal("s", fs=10)
    window.set_recording_options("run.h5", max_file_mb=2.5)
    window.set_recording(True)
    assert sizes == [2_500_000]


def test_options_are_checked(window):
    with pytest.raises(ValueError, match="must be positive"):
        window.set_recording_options("run.h5", max_file_mb=0)


def test_open_folder_opens_where_it_saves(window, monkeypatch):
    opened: list[qtc.QUrl] = []
    monkeypatch.setattr(qtg.QDesktopServices, "openUrl", opened.append)
    window.stream_folder_action.trigger()
    assert opened == [qtc.QUrl.fromLocalFile(str(window.recording_options[0].parent))]
    assert window.recording_options[0].parent.is_dir()  # Created, so there's something to open


@pytest.mark.parametrize("end", ["close", "clear"])
def test_closing_or_clearing_the_window_ends_the_recording(qapp, tmp_path, end):
    scope = Oscilab(app=qapp)
    scope.set_recording_options(tmp_path / "run.h5")
    scope.register_signal("s", fs=10).add_data([0.0], [1.0])
    scope.set_recording(True)
    recorder = scope._recorder

    getattr(scope, end)()

    assert not recorder.recording
    np.testing.assert_allclose(samples_in(recorder.files[0], "s"), [])  # Closed properly: readable
    scope.close()
