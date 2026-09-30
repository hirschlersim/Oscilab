"""
The Producers menu: every attached producer, with Start, Stop and its own
@button / @toggle functions — which run on the producer's thread, and with
toolbar=True go in the toolbar too.
"""

import threading
import time

import numpy as np
import pytest
from PyQt6 import QtWidgets as qtw

from oscilab import SyncDataProducer, SyntheticProducer, button, toggle
from oscilab.oscilab import Oscilab

MAIN_THREAD = threading.get_ident()


@pytest.fixture
def window(qapp):
    scope = Oscilab(app=qapp)
    yield scope
    scope.close()


def wait_until(qapp, condition, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for condition"
        qapp.processEvents()
        time.sleep(0.005)


class Device(SyncDataProducer):
    """A producer with functions of its own, recording which thread each ran on."""

    channels = ("a", "b")

    def __init__(self, fail: bool = False):
        super().__init__(fs=100.0, interval_s=0.005)
        self.fail = fail
        self.calls: list[tuple] = []

    def _generate(self, _elapsed):
        if self.fail:
            raise RuntimeError("device unplugged")
        return np.array([]), np.array([])

    @button("Zero", tooltip="Zero the sensor", toolbar=True)
    def zero(self):
        self.calls.append(("zero", threading.get_ident()))

    @toggle("Heater", toolbar=True)
    def heater(self, on: bool):
        self.calls.append(("heater", on, threading.get_ident()))

    @button("Explode")
    def explode(self):
        raise RuntimeError("action failed")


def submenus(window: Oscilab) -> dict[str, qtw.QMenu]:
    return {a.text(): a.menu() for a in window.producers_menu.actions() if a.menu() is not None}


def entry(menu: qtw.QMenu, text: str):
    return next(a for a in menu.actions() if a.text() == text)


def entries(menu: qtw.QMenu) -> list[str]:
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def toolbar_action(window: Oscilab, text: str):
    return next(a for a in window.toolbar.actions() if a.text() == text)


def refresh(window: Oscilab):
    window.producers_menu.aboutToShow.emit()  # As when it's opened: states catch up


def test_every_attached_producer_gets_an_entry(window):
    assert [a.text() for a in window.producers_menu.actions()] == ["No producers attached"]

    window.attach_producer(Device(), ["x", "y"])
    window.attach_producer(SyntheticProducer(fs=100), "s")

    menus = submenus(window)
    assert list(menus) == ["Device: x, y", "SyntheticProducer: s"]
    assert [a.text() for a in window.producers_menu.actions()] == list(menus)  # Placeholder gone
    assert entries(menus["Device: x, y"]) == ["Start", "Stop", "Zero", "Heater", "Explode"]
    assert entries(menus["SyntheticProducer: s"]) == ["Start", "Stop"]


def test_stop_and_start_from_the_menu(window):
    device = Device()
    window.attach_producer(device, ["x", "y"])
    menu = submenus(window)["Device: x, y"]
    start, stop = entry(menu, "Start"), entry(menu, "Stop")
    assert (start.isEnabled(), stop.isEnabled()) == (False, True)

    stop.trigger()
    assert not device.running
    assert (start.isEnabled(), stop.isEnabled()) == (True, False)

    start.trigger()
    assert device.running
    assert (start.isEnabled(), stop.isEnabled()) == (False, True)


def test_an_error_shows_and_start_recovers(qapp, window):
    device = Device(fail=True)
    window.attach_producer(device, ["x", "y"])
    wait_until(qapp, lambda: window.workspace.producer_state("x") == "error")
    refresh(window)
    menu = submenus(window)["Device: x, y"]
    assert entry(menu, "Start").isEnabled()

    device.fail = False
    entry(menu, "Start").trigger()
    assert window.workspace.producer_state("x") == "running"


def test_its_functions_run_on_the_producers_thread(qapp, window):
    device = Device()
    window.attach_producer(device, ["x", "y"])
    menu = submenus(window)["Device: x, y"]

    entry(menu, "Zero").trigger()
    entry(menu, "Heater").trigger()
    wait_until(qapp, lambda: len(device.calls) == 2)

    (zero, zero_thread), (heater, on, heater_thread) = device.calls
    assert (zero, heater, on) == ("zero", "heater", True)
    assert MAIN_THREAD not in (zero_thread, heater_thread)
    assert entry(menu, "Heater").isChecked()


def test_a_failing_function_stops_the_producer(qapp, window):
    window.attach_producer(Device(), ["x", "y"])
    entry(submenus(window)["Device: x, y"], "Explode").trigger()
    wait_until(qapp, lambda: window.workspace.producer_state("x") == "error")
    assert window.workspace._producer_errors["x"] == "RuntimeError: action failed"


def test_functions_are_refused_while_its_stopped(window, monkeypatch):
    shown = []
    monkeypatch.setattr(window.statusBar(), "showMessage", lambda text, _timeout=0: shown.append(text))
    device = Device()
    window.attach_producer(device, ["x", "y"])
    menu = submenus(window)["Device: x, y"]
    entry(menu, "Stop").trigger()

    entry(menu, "Zero").trigger()
    assert shown[-1] == "✕ 'Zero' failed: RuntimeError: Device isn't running"
    heater = entry(menu, "Heater")
    heater.trigger()
    assert not heater.isChecked()  # Didn't take, so it flips back
    assert device.calls == []


def test_toolbar_functions_go_in_the_toolbar_too(window):
    window.attach_producer(Device(), ["x", "y"])
    menu = submenus(window)["Device: x, y"]

    toolbar = [a.text() for a in window.toolbar.actions() if a.text()]
    assert toolbar[-3:] == ["Full Detail", "Zero", "Heater"]  # After Downsample; not Explode, it's menu-only
    assert entries(menu) == ["Start", "Stop", "Zero", "Heater", "Explode"]  # The menu has them all
    assert toolbar_action(window, "Zero") is entry(menu, "Zero")  # One control, in both places
    assert toolbar_action(window, "Zero").toolTip() == "Zero the sensor (Device: x, y)"  # Says whose it is


def test_from_the_toolbar_they_run_on_the_producers_thread_too(qapp, window):
    device = Device()
    window.attach_producer(device, ["x", "y"])

    toolbar_action(window, "Zero").trigger()
    wait_until(qapp, lambda: device.calls)
    assert device.calls[0][0] == "zero"
    assert device.calls[0][1] != MAIN_THREAD


def test_a_toggle_shows_the_same_state_in_the_toolbar_and_the_menu(qapp, window):
    device = Device()
    window.attach_producer(device, ["x", "y"])
    heater = toolbar_action(window, "Heater")

    heater.trigger()
    wait_until(qapp, lambda: device.calls)
    assert entry(submenus(window)["Device: x, y"], "Heater").isChecked()
    assert not heater.icon().isNull()  # Its green/gray dot, in the toolbar...
    assert not heater.isIconVisibleInMenu()  # ...and a check mark in the menu


def test_add_toolbar_refuses_a_producer(window):
    with pytest.raises(TypeError, match=r"toolbar=True"):
        window.add_toolbar(Device())  # Its methods would run on the GUI thread
