"""
Sessions: a saved setup rebuilds the same window, and bad files are refused
before anything is built.
"""

import json
import time
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError
from PyQt6 import QtCore as qtc
from PyQt6 import QtWidgets as qtw

from oscilab import session
from oscilab.__main__ import main
from oscilab.oscilab import Oscilab
from oscilab.producers import AsyncSyntheticProducer, SyncDataProducer, SyntheticProducer
from oscilab.session import Session
from oscilab.toolbar import toggle


class Constant(SyncDataProducer):
    """A producer defined outside oscilab — saved by its import path, like an add-on's."""

    units = ("V",)

    def __init__(self, value: float, fs: float = 50.0):
        super().__init__(fs=fs)
        self.value = value
        self.held = False

    def _generate(self, elapsed):
        n = int(elapsed * self.fs)
        return np.arange(n) / self.fs, np.full(n, self.value)

    @toggle("Hold")
    def hold(self, on: bool):
        self.held = on


class Bench:
    """Your own toolbar controls."""

    def __init__(self):
        self.calls: list[tuple[str, bool]] = []

    @toggle("Lamp")
    def lamp(self, on: bool):
        self.calls.append(("lamp", on))

    @toggle("Fan", checked=True)
    def fan(self, on: bool):
        self.calls.append(("fan", on))


@pytest.fixture
def make_window(qapp):
    windows = []

    def make() -> Oscilab:
        window = Oscilab(app=qapp)
        windows.append(window)
        return window

    yield make
    for window in windows + list(Oscilab._extra_windows):
        window.close()


def wait_until(qapp, condition, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for condition"
        qapp.processEvents()
        time.sleep(0.005)


def build_everything(window: Oscilab):
    """A setup that touches every part of a session."""
    window.set_theme("light")
    window.set_fps(10)
    window.set_duration(5)
    window.set_time_mode("relative")
    window.set_downsampling(True)
    window.set_recording_options("recordings/run.h5", max_file_mb=5)
    window.set_window_geometry(30, 40, 700, 500)

    window.attach_producer(SyntheticProducer(fs=100, freq=5.0), "slow", storage=window.storage.deque)
    window.attach_producer(SyntheticProducer(fs=1_000, freq=(1.0, 10.0), noise=0), ["a", "b"], units="V")
    window.attach_producer(AsyncSyntheticProducer(fs=200), "async")
    constant = Constant(3.0)
    window.attach_producer(constant, "constant")
    window.set_producer_toggle_states(constant, {"hold": True})
    window.register_signal("manual", fs=50, unit="mA")
    window.workspace.add_math_signal("sum", "+", "a", "b")
    window.set_unit("slow", "°C")
    window.add_toolbar(Bench())
    window.set_toggle_states({"Bench.lamp": True, "Bench.fan": False})

    plot = window.add_timeseries_plot("Main")
    for label in ("slow", "a", "sum", "manual", "constant"):
        window.link(plot="Main", label=label)
    plot.set_y_range(False, -2.0, 2.0)
    window.set_cursors("Main", enabled=True)
    plot.move_cursors(1.0, 2.5)

    spectrogram = window.add_spectrogram_plot("b", nperseg=128, noverlap=64)
    spectrogram.set_color_levels(False, -80.0, 0.0)

    rows = window.workspace.signal_list.rows
    rows["slow"].set_color("#123456")
    rows["slow"].apply_scaling(True, 2.0, 0.5)
    rows["a"].set_visible(False)
    rows["a"].apply_filter(True, 20.0, 2)
    rows["sum"].set_expanded(True)


# --- round trip ---------------------------------------------------------------


def test_a_saved_session_rebuilds_the_same_setup(qapp, make_window, tmp_path):
    original = make_window()
    build_everything(original)
    path = original.save_session(tmp_path / "setup")
    assert path.name == "setup.json"

    restored = make_window()
    restored.load_session(path)

    assert session.capture(restored) == session.capture(original)
    # The menus and toolbar show the loaded settings too
    assert restored.downsample_action.isChecked()
    assert restored.cursors_menu.actions()[0].isChecked()
    # And the producers are running
    wait_until(qapp, lambda: all(len(restored.workspace.signals[s].data) > 0 for s in ("slow", "a", "b", "constant")))
    assert restored.workspace.signals["constant"].snapshot()[1][-1] == 3.0


def test_the_file_is_plain_readable_json(make_window, tmp_path):
    window = make_window()
    build_everything(window)
    data = json.loads(window.save_session(tmp_path / "setup.json").read_text())

    assert data["producers"][1] == {
        "type": "oscilab.producers:SyntheticProducer",
        "args": {"fs": 1000, "freq": [1.0, 10.0], "noise": 0},
        "signals": ["a", "b"],
        "storage": "ring",
        "units": ["V", "V"],
        "toggles": {},
    }
    constant = data["producers"][3]
    assert constant["type"] == "test_session:Constant"
    assert constant["units"] is None  # The producer's own: not repeated
    assert constant["toggles"] == {"hold": True}
    assert data["manual_signals"][0]["unit"] == "mA"
    assert data["window"] == {"x": 30, "y": 40, "width": 700, "height": 500, "maximized": False}
    assert data["toggles"] == {"Bench.lamp": True, "Bench.fan": False}
    assert data["recording"] == {"path": str(Path("recordings/run.h5")), "max_file_mb": 5.0}
    assert data["signal_settings"]["a"]["filter"] == {"cutoff": 20.0, "order": 2}
    assert data["plots"][1] == {
        "kind": "spectrogram",
        "signal": "b",
        "nperseg": 128,
        "noverlap": 64,
        "y_range": None,
        "color_levels": {"min": -80.0, "max": 0.0},
    }


def test_open_session_uses_a_new_window_only_when_this_one_is_in_use(make_window, tmp_path):
    source = make_window()
    source.attach_producer(SyntheticProducer(fs=100), "s")
    path = source.save_session(tmp_path / "s.json")

    empty = make_window()
    assert empty.open_session(path) is empty

    other = source.open_session(path)
    assert other is not source
    assert "s" in other.workspace.signals


# --- refusing bad sessions ----------------------------------------------------


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"themes": "dark"}, "Extra inputs are not permitted"),
        ({"fps": 25}, "fps"),
        ({"plots": [{"kind": "timeseries", "title": "P", "signals": ["ghost"]}]}, "shows 'ghost'"),
        ({"plots": [{"kind": "spectrogram", "signal": "ghost"}]}, "shows 'ghost'"),
        ({"manual_signals": [{"label": "x", "fs": 1}, {"label": "x", "fs": 1}]}, "'x' is defined twice"),
        (
            {
                "manual_signals": [{"label": "x", "fs": 1}],
                "math_signals": [{"label": "y", "operator": "+", "a": "x", "b": "z"}],
            },
            "uses 'z'",
        ),
        ({"signal_settings": {"ghost": {}}}, "'ghost'"),
    ],
)
def test_invalid_sessions_are_refused(data, message):
    with pytest.raises(ValidationError, match=message):
        Session.model_validate(data)


@pytest.mark.parametrize(
    ("producer", "message"),
    [
        ({"type": "oscilab.producers:Nope", "signals": ["s"]}, "Can't find producer type"),
        (
            {"type": "oscilab.producers:SyntheticProducer", "args": {"fs": 100, "colour": 1}, "signals": ["s"]},
            "Can't create",
        ),
    ],
)
def test_a_producer_that_cant_be_built_leaves_the_window_untouched(make_window, tmp_path, producer, message):
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps({"producers": [producer], "plots": [{"kind": "timeseries", "title": "P", "signals": ["s"]}]})
    )
    window = make_window()

    with pytest.raises(ValueError, match=message):
        window.load_session(path)
    assert not window.workspace.signals
    assert not window.workspace.views


def test_sessions_load_only_into_an_empty_window(make_window, tmp_path):
    path = tmp_path / "empty.json"
    path.write_text("{}")
    window = make_window()
    window.register_signal("s", fs=10)
    with pytest.raises(ValueError, match="empty window"):
        window.load_session(path)


def test_saving_names_an_argument_that_isnt_json(make_window, tmp_path):
    window = make_window()
    window.attach_producer(SyntheticProducer(fs=100, freq=np.array([1.0, 2.0])), ["x", "y"])
    with pytest.raises(ValueError, match="freq"):
        window.save_session(tmp_path / "s.json")


def test_a_failing_open_from_the_menu_warns_instead_of_crashing(make_window, tmp_path, monkeypatch):
    path = tmp_path / "broken.json"
    path.write_text("{not json")
    warnings = []
    monkeypatch.setattr(qtw.QFileDialog, "getOpenFileName", lambda *_args: (str(path), ""))
    monkeypatch.setattr(qtw.QMessageBox, "warning", lambda _parent, title, _text: warnings.append(title))
    window = make_window()

    file_menu = window.menuBar().actions()[0].menu()
    next(a for a in file_menu.actions() if a.text() == "Open Session...").trigger()

    assert warnings == ["Couldn't open the session"]


# --- the `oscilab` command ------------------------------------------------------


def test_command_prints_the_schema(capsys):
    assert main(["--schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["title"] == "Session"
    assert "producers" in schema["properties"]


def test_command_opens_a_session(qapp, make_window, tmp_path):
    source = make_window()
    source.attach_producer(SyntheticProducer(fs=100), "s")
    source.add_timeseries_plot("P")
    source.link(plot="P", label="s")
    path = source.save_session(tmp_path / "s.json")
    source.close()

    opened = []

    def close_once_shown():
        # Wait for the window to be up, rather than guessing how long that takes
        shown = [w for w in qapp.topLevelWidgets() if isinstance(w, Oscilab) and w.isVisible()]
        if not shown:
            qtc.QTimer.singleShot(20, close_once_shown)
            return
        opened.extend(shown)
        qapp.closeAllWindows()

    qtc.QTimer.singleShot(0, close_once_shown)
    assert main([str(path)]) == 0
    assert any("P" in window.workspace.views for window in opened)


# --- replacing the setup in place -----------------------------------------------


class Exclusive(SyncDataProducer):
    """Holds a device only one producer can have open at a time, like a serial port."""

    taken = False

    def __init__(self, fs: float = 10.0):
        super().__init__(fs=fs)
        self._open = False
        self._acquire()

    def _acquire(self):
        if Exclusive.taken:
            raise RuntimeError("device busy")
        Exclusive.taken = self._open = True

    def on_start(self):
        if not self._open:
            self._acquire()

    def on_stop(self):
        Exclusive.taken = self._open = False

    def _generate(self, _elapsed):
        return np.array([]), np.array([])


def test_replace_swaps_the_whole_setup(make_window, tmp_path):
    source = make_window()
    source.attach_producer(SyntheticProducer(fs=100), "new")
    source.add_timeseries_plot("Fresh")
    source.link(plot="Fresh", label="new")
    path = source.save_session(tmp_path / "s.json")

    window = make_window()
    old = SyntheticProducer(fs=100)
    window.attach_producer(old, "old")
    window.register_signal("mine", fs=10)
    window.workspace.add_math_signal("twice", "+", "mine", "mine")
    window.add_timeseries_plot("Old")
    for label in ("old", "twice"):
        window.link(plot="Old", label=label)
    window.add_toolbar(Bench())

    assert window.open_session(path, replace=True) is window

    ws = window.workspace
    assert set(ws.signals) == {"new"}
    assert list(ws.views) == ["Fresh"]
    assert list(ws.signal_list.rows) == ["new"]
    assert not old.running
    assert [a.text() for a in window.producers_menu.actions()] == ["SyntheticProducer: new"]
    assert [a.text() for a in window.cursors_menu.actions()] == ["Fresh"]
    assert "Lamp" in [a.text() for a in window.toolbar.actions()]  # Your own tools stay


def test_replace_frees_a_device_for_the_sessions_producers(make_window, tmp_path):
    Exclusive.taken = False
    window = make_window()
    old = Exclusive()
    window.attach_producer(old, "device")
    path = window.save_session(tmp_path / "s.json")

    window.load_session(path, replace=True)  # Would be "device busy" if the old one still held it

    new = window.workspace.producers["device"]
    assert new is not old
    assert not old.running
    assert new.running


def test_a_session_that_cant_be_built_leaves_the_setup_as_it_was(make_window, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"producers": [{"type": "oscilab.producers:Nope", "signals": ["s"]}]}))
    window = make_window()
    window.attach_producer(SyntheticProducer(fs=100), "running")
    window.attach_producer(SyntheticProducer(fs=100), "idle")
    window.workspace.stop_producer("idle")

    with pytest.raises(ValueError, match="Can't find producer type"):
        window.load_session(path, replace=True)

    ws = window.workspace
    assert set(ws.signals) == {"running", "idle"}
    assert ws.producer_state("running") == "running"  # Started again
    assert ws.producer_state("idle") == "stopped"  # Left as it was


def answer_open_session(monkeypatch, path, choice: str):
    """File > Open Session: pick `path`, then press `choice` when asked what to do with a window in use."""
    monkeypatch.setattr(qtw.QFileDialog, "getOpenFileName", lambda *_args: (str(path), ""))

    def press(box):
        buttons = {b.text().replace("&", ""): b for b in box.buttons()}
        buttons[choice].click()

    monkeypatch.setattr(qtw.QMessageBox, "exec", press)


@pytest.mark.parametrize(
    ("choice", "replaced", "new_window"),
    [("Replace", True, False), ("New Window", False, True), ("Cancel", False, False)],
)
def test_open_session_asks_what_to_do_with_a_window_in_use(
    make_window, tmp_path, monkeypatch, choice, replaced, new_window
):
    source = make_window()
    source.attach_producer(SyntheticProducer(fs=100), "new")
    path = source.save_session(tmp_path / "s.json")
    window = make_window()
    window.register_signal("mine", fs=10)
    answer_open_session(monkeypatch, path, choice)

    file_menu = window.menuBar().actions()[0].menu()
    next(a for a in file_menu.actions() if a.text() == "Open Session...").trigger()

    assert set(window.workspace.signals) == ({"new"} if replaced else {"mine"})
    assert [set(w.workspace.signals) for w in Oscilab._extra_windows] == ([{"new"}] if new_window else [])


def test_a_failing_save_from_the_menu_warns_instead_of_crashing(make_window, tmp_path, monkeypatch):
    target = tmp_path / "no such folder" / "s.json"
    warnings = []
    monkeypatch.setattr(qtw.QFileDialog, "getSaveFileName", lambda *_args: (str(target), ""))
    monkeypatch.setattr(qtw.QMessageBox, "warning", lambda _parent, title, _text: warnings.append(title))
    window = make_window()

    file_menu = window.menuBar().actions()[0].menu()
    next(a for a in file_menu.actions() if a.text() == "Save Session...").trigger()

    assert warnings == ["Couldn't save the session"]


def test_cancelling_the_open_dialog_opens_nothing(make_window, monkeypatch):
    monkeypatch.setattr(qtw.QFileDialog, "getOpenFileName", lambda *_args: ("", ""))
    window = make_window()
    file_menu = window.menuBar().actions()[0].menu()
    next(a for a in file_menu.actions() if a.text() == "Open Session...").trigger()
    assert not window.workspace.signals
