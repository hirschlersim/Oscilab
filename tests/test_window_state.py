"""
What a session remembers about the window itself: its size and position,
and the states of your toolbar toggles and producers' toggles.
"""

import threading
import time

import numpy as np
import pytest
from PyQt6 import QtGui as qtg

from oscilab import SyncDataProducer, button, session, toggle
from oscilab.oscilab import Oscilab

MAIN_THREAD = threading.get_ident()


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


class Bench:
    def __init__(self):
        self.calls: list[tuple[str, bool]] = []

    @toggle("Lamp")
    def lamp(self, on: bool):
        self.calls.append(("lamp", on))

    @toggle("Fan", checked=True)
    def fan(self, on: bool):
        self.calls.append(("fan", on))

    @button("Zero")
    def zero(self):
        self.calls.append(("zero", True))


class Heater(SyncDataProducer):
    def __init__(self):
        super().__init__(fs=10.0)
        self.calls: list[tuple[bool, int]] = []

    def _generate(self, _elapsed):
        return np.array([]), np.array([])

    @toggle("Heat", checked=True)
    def heat(self, on: bool):
        self.calls.append((on, threading.get_ident()))


# --- size and position ----------------------------------------------------------


def test_the_window_goes_where_it_was(make_window, tmp_path):
    original = make_window()
    original.set_window_geometry(30, 40, 700, 500)
    path = original.save_session(tmp_path / "s.json")

    restored = make_window()
    restored.load_session(path)
    assert restored.window_geometry() == (30, 40, 700, 500, False)


def test_maximized_remembers_where_it_restores_to(make_window):
    window = make_window()
    window.set_window_geometry(30, 40, 700, 500, maximized=True)
    assert window.isMaximized()
    assert window.window_geometry() == (30, 40, 700, 500, True)

    window.set_window_geometry(30, 40, 700, 500)
    assert not window.isMaximized()


def test_a_position_off_every_screen_comes_back_onto_one(make_window):
    window = make_window()
    window.set_window_geometry(50_000, 50_000, 700, 500)  # A monitor that's since been unplugged

    screen = window.screen().availableGeometry()
    assert window.window_geometry() == (screen.x() + 50, screen.y() + 50, 700, 500, False)
    assert qtg.QGuiApplication.screenAt(window.geometry().center()) is not None


def test_a_session_without_a_window_keeps_the_default_placement(make_window):
    window = make_window()
    before = window.window_geometry()
    session.restore(window, session.Session())  # E.g. written by hand, or before windows were saved
    assert window.window_geometry() == before


# --- your toolbar toggles ---------------------------------------------------------


def test_toggles_are_keyed_by_class_and_method(make_window):
    window = make_window()
    window.add_toolbar(Bench())
    window.add_toolbar(Bench())  # A second one of the same class
    window.add_toolbar_toggle("Mute", lambda _on: None)

    assert window.toggle_states() == {
        "Bench.lamp": False,
        "Bench.fan": True,
        "Bench.lamp#2": False,
        "Bench.fan#2": True,
        "Mute": False,  # One-offs by their text
    }


def test_setting_toggle_states_calls_their_methods(make_window):
    window = make_window()
    bench = Bench()
    window.add_toolbar(bench)

    window.set_toggle_states({"Bench.lamp": True, "Bench.fan": True})
    assert bench.calls == [("lamp", True)]  # Fan was on already: nothing to do
    assert window.toggle_states() == {"Bench.lamp": True, "Bench.fan": True}


def test_toggles_from_a_session_switch_when_their_toolbar_is_added(make_window, tmp_path):
    original = make_window()
    original.add_toolbar(Bench())
    original.set_toggle_states({"Bench.lamp": True, "Bench.fan": False})
    path = original.save_session(tmp_path / "s.json")

    restored = make_window()
    restored.load_session(path)  # Before the script adds its tools
    assert restored.toggle_states() == {"Bench.lamp": True, "Bench.fan": False}  # Still saved along

    bench = Bench()
    restored.add_toolbar(bench)
    assert bench.calls == [("lamp", True), ("fan", False)]
    lamp = next(a for a in restored.toolbar.actions() if a.text() == "Lamp")
    assert lamp.isChecked()


# --- producers' toggles ----------------------------------------------------------


def test_a_producers_toggles_are_saved_with_it(qapp, make_window, monkeypatch):
    window = make_window()
    heater = Heater()
    window.attach_producer(heater, "t")
    assert window.producer_toggle_states(heater) == {"heat": True}

    window.set_producer_toggle_states(heater, {"heat": False})
    wait_until(qapp, lambda: heater.calls)
    (on, thread), *_ = heater.calls
    assert not on
    assert thread != MAIN_THREAD  # On the producer's thread, as from its menu
    assert window.producer_toggle_states(heater) == {"heat": False}

    shown = []
    monkeypatch.setattr(window.statusBar(), "showMessage", lambda text, _timeout=0: shown.append(text))
    window.set_producer_toggle_states(heater, {"cool": True})  # E.g. renamed since the session was saved
    assert shown[-1] == "⚠ Heater has no toggle 'cool'"
