"""
Replaying Save Stream recordings: a file or a folder, in order, on one
timeline for every signal, looped or not — and from the window.
"""

import time

import h5py
import numpy as np
import pytest
from PyQt6 import QtWidgets as qtw

from oscilab import recorder as recorder_module
from oscilab import session
from oscilab.dialogs import ReplayDialog
from oscilab.oscilab import Oscilab
from oscilab.recorder import Recorder
from oscilab.replay import RecordingReplayProducer, recorded_signals, recording_files, replay_labels
from oscilab.signal_model import SignalModel

GAP = 0.1  # One period of the slowest signal ("s", 10 Hz): what separates recordings on the timeline


def write_part(path, started: str, signals: dict[str, tuple[float, str, np.ndarray]]):
    """A file as Save Stream writes it; each signal's data is its time * 10."""
    with h5py.File(path, "w", track_order=True) as f:  # As Save Stream does: signals in order
        f.attrs["started"] = started
        for label, (fs, unit, t) in signals.items():
            group = f.create_group(label)
            group["time"] = t
            group["data"] = t * 10
            group.attrs.update(fs=fs, label=label, unit=unit)


@pytest.fixture
def folder(tmp_path):
    """
    Recording A in two parts (0-1 s, 1-2 s), then recording B, made later
    (at 100 s): "s" at 10 Hz in V, "f" at 100 Hz.
    """
    folder = tmp_path / "runs"
    folder.mkdir()
    write_part(
        folder / "rec_A_001.h5",
        "A",
        {"s": (10.0, "V", np.arange(0, 10) / 10), "f": (100.0, "", np.arange(0, 100) / 100)},
    )
    write_part(
        folder / "rec_A_002.h5",
        "A",
        {"s": (10.0, "V", np.arange(10, 20) / 10), "f": (100.0, "", np.arange(100, 200) / 100)},
    )
    write_part(
        folder / "rec_B_001.h5",
        "B",
        {"s": (10.0, "V", 100 + np.arange(5) / 10), "f": (100.0, "", 100 + np.arange(50) / 100)},
    )
    return folder


def playing(path, signal: str, loop: bool = False) -> RecordingReplayProducer:
    """A producer as its thread sees it once started."""
    producer = RecordingReplayProducer(path, signal, loop=loop)
    producer._running = True
    producer.on_start()
    return producer


def wait_until(qapp, condition, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for condition"
        qapp.processEvents()
        time.sleep(0.005)


# --- what gets played ---------------------------------------------------------------


def test_a_folder_plays_its_files_in_name_order(folder):
    assert [f.name for f in recording_files(folder)] == ["rec_A_001.h5", "rec_A_002.h5", "rec_B_001.h5"]
    assert recording_files(folder / "rec_A_002.h5") == [folder / "rec_A_002.h5"]  # Or just the one file
    assert recorded_signals(folder) == ["s", "f"]


@pytest.mark.parametrize(("name", "message"), [("empty", "No .h5 files"), ("missing", "doesn't exist")])
def test_nothing_to_play_is_refused(tmp_path, name, message):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match=message):
        recording_files(tmp_path / name)


def test_it_replays_at_the_recorded_rate_and_unit(folder):
    producer = RecordingReplayProducer(folder, "s")
    assert (producer.fs, producer.channels, producer.units) == (10.0, ("s",), ("V",))
    with pytest.raises(ValueError, match=r"'x' isn't in .*; it has \['s', 'f'\]"):
        RecordingReplayProducer(folder, "x")


# --- the timeline -----------------------------------------------------------------


def test_samples_play_in_real_time(folder):
    producer = playing(folder, "s")
    t, x = producer._generate(0.0)
    np.testing.assert_allclose(t, [0.0])  # Due right away
    t, x = producer._generate(0.35)
    np.testing.assert_allclose(t, [0.1, 0.2, 0.3])
    np.testing.assert_allclose(x, [1.0, 2.0, 3.0])


def test_parts_of_a_recording_keep_their_times_and_a_new_recording_follows_on(folder):
    producer = playing(folder, "s")
    t, _ = producer._generate(100.0)  # Everything
    np.testing.assert_allclose(t[:20], np.arange(20) / 10)  # A's two parts, as recorded
    b_start = 1.99 + GAP  # Right after A ends (its last sample: "f" at 1.99 s), not ~98 s later
    np.testing.assert_allclose(t[20:], b_start + np.arange(5) / 10)
    assert not producer._running  # Not looping: played to the end, it stops


def test_every_signal_of_a_recording_shares_the_timeline(folder):
    t_s, _ = playing(folder, "s")._generate(100.0)
    t_f, _ = playing(folder, "f")._generate(100.0)
    assert t_f[100] == pytest.approx(1.0)  # A's second part, at its recorded time
    assert t_f[200] == pytest.approx(t_s[20])  # B starts at the same moment for both


def test_a_loop_starts_again_from_the_first_file_going_forward(folder):
    producer = playing(folder, "s", loop=True)
    period = (1.99 + GAP + 0.49) + GAP  # Timeline's end ("f" in B) minus its start, and the gap before the next pass
    t, x = producer._generate(period + 0.15)
    assert producer._running
    np.testing.assert_allclose(t[-2:], [period, period + 0.1])  # Pass 2: file 1 again, later on the timeline
    np.testing.assert_allclose(x[-2:], [0.0, 1.0])
    assert np.all(np.diff(t) > 0)


def test_a_restart_carries_on_where_it_stopped(folder):
    producer = playing(folder, "s")
    producer._generate(0.45)  # Up to 0.4 s
    producer.on_stop()
    producer.on_start()
    t, _ = producer._generate(0.0)
    assert t.size == 0  # Nothing replayed twice
    t, _ = producer._generate(0.2)
    np.testing.assert_allclose(t, [0.5, 0.6])


def test_starting_it_again_after_the_end_plays_it_again(folder):
    producer = playing(folder, "s")
    end, _ = producer._generate(100.0)
    producer.on_stop()
    producer._running = True
    producer.on_start()
    t, x = producer._generate(1.0)
    assert t[0] > end[-1]  # Time goes on
    assert x[0] == 0.0  # From the first sample


def test_a_recording_plays_back_exactly_as_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder_module, "_FLUSH_EVERY_S", 0.0)  # Check the size (and roll over) after every write
    monkeypatch.setattr(recorder_module, "_BATCH_S", 0.01)
    model = SignalModel("m", fs=1_000.0, unit="A")
    recorder = Recorder({"m": model}, tmp_path / "rec" / "run.h5", max_file_bytes=60_000)
    recorder.start()
    for start in range(0, 20_000, 1_000):
        index = np.arange(start, start + 1_000)
        model.add_data(index / 1_000, np.sin(index / 50))
        time.sleep(0.03)
    files = recorder.stop()
    assert len(files) > 1

    t, x = playing(tmp_path / "rec", "m")._generate(1e6)
    np.testing.assert_allclose(t, np.arange(20_000) / 1_000)
    np.testing.assert_allclose(x, np.sin(np.arange(20_000) / 50))


# --- in the window ------------------------------------------------------------------


@pytest.fixture
def window(qapp):
    scope = Oscilab(app=qapp)
    yield scope
    scope.close()


def test_replay_recording_shows_every_signal_in_a_new_plot(qapp, window, folder):
    window.register_signal("s", fs=10)  # Taken: the replay gets another name
    names = window.replay_recording(folder, loop=True)

    assert names == ["s (replay)", "f"]
    assert list(window.workspace.plots["Replay: runs"].curves) == names
    assert window.workspace.signals["s (replay)"].unit == "V"
    wait_until(qapp, lambda: len(window.workspace.signals["f"].data) > 5)
    assert window.workspace.producers["f"].loop


def test_replayed_signals_are_named_apart_from_the_ones_there():
    assert replay_labels(["a", "a (replay)"], ["a", "b", "a"]) == ["a (replay 2)", "b", "a (replay 3)"]


def test_the_dialog_lists_the_files_and_signals_to_pick_from(window, folder, monkeypatch):
    def pick(dialog: ReplayDialog) -> qtw.QDialog.DialogCode:
        dialog.edit_path.setText(str(folder))
        dialog.edit_path.editingFinished.emit()
        assert dialog.files_label.text() == "3 file(s), played in this order: rec_A_001.h5, rec_A_002.h5, rec_B_001.h5"
        dialog.signal_list.item(1).setCheckState(dialog.signal_list.item(1).checkState().Unchecked)  # Not "f"
        dialog.check_loop.setChecked(True)
        return qtw.QDialog.DialogCode.Accepted

    monkeypatch.setattr(ReplayDialog, "exec", pick)
    window.replay_action.trigger()

    assert set(window.workspace.signals) == {"s"}
    assert window.workspace.producers["s"].loop


def test_the_dialog_says_when_theres_nothing_to_replay(qapp, tmp_path):  # noqa: ARG001 — a widget needs the QApplication
    dialog = ReplayDialog(str(tmp_path / "missing"), lambda path: ([f.name for f in recording_files(path)], []))
    assert "doesn't exist" in dialog.files_label.text()
    assert not dialog._ok.isEnabled()


def test_a_session_saves_the_replay(window, folder):
    window.replay_recording(folder, ["s"], loop=True)
    (spec,) = session.capture(window).producers
    assert spec.type == "oscilab.replay:RecordingReplayProducer"
    assert spec.args == {"path": str(folder), "signal": "s", "loop": True}


def test_replaying_a_recording_twice_numbers_its_plot(window, folder):
    window.replay_recording(folder)
    window.replay_recording(folder)
    assert {"Replay: runs", "Replay: runs (2)"} <= set(window.workspace.plots)


def test_a_recording_without_signals_is_refused(window, tmp_path):
    with h5py.File(tmp_path / "empty.h5", "w") as f:
        f.attrs["started"] = "never"
    with pytest.raises(ValueError, match="No signals to replay"):
        window.replay_recording(tmp_path / "empty.h5")


@pytest.fixture
def logged(window, monkeypatch) -> list[str]:
    messages: list[str] = []
    monkeypatch.setattr(window.statusBar(), "showMessage", lambda text, _timeout=0: messages.append(text))
    return messages


def test_the_dialog_with_no_signal_ticked_replays_nothing(window, folder, monkeypatch, logged):
    def pick(dialog: ReplayDialog) -> qtw.QDialog.DialogCode:
        dialog.edit_path.setText(str(folder))
        dialog.edit_path.editingFinished.emit()
        for row in range(dialog.signal_list.count()):
            dialog.signal_list.item(row).setCheckState(dialog.signal_list.item(row).checkState().Unchecked)
        return qtw.QDialog.DialogCode.Accepted

    monkeypatch.setattr(ReplayDialog, "exec", pick)
    window.replay_action.trigger()
    assert not window.workspace.signals
    assert logged[-1].endswith("No signals ticked to replay")


def test_the_dialog_says_when_a_replay_fails(window, folder, monkeypatch, logged):
    def pick(dialog: ReplayDialog) -> qtw.QDialog.DialogCode:
        dialog.edit_path.setText(str(folder))
        dialog.edit_path.editingFinished.emit()
        (folder / "rec_B_001.h5").write_bytes(b"not HDF5 any more")  # Between the scan and the replay
        return qtw.QDialog.DialogCode.Accepted

    monkeypatch.setattr(ReplayDialog, "exec", pick)
    window.replay_action.trigger()  # Must not raise: a Qt slot
    assert "Couldn't replay" in logged[-1]
