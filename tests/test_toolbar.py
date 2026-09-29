"""Custom toolbar buttons and toggles, defined as methods of your own class — or a producer's."""

import threading
import time

import numpy as np
import pytest
from PyQt6 import QtWidgets as qtw

from oscilab import SyncDataProducer, SyntheticProducer, button, toggle
from oscilab.oscilab import Oscilab, _signals_caption

MAIN_THREAD = threading.get_ident()


@pytest.fixture
def window(qapp):
    oszi = Oscilab(app=qapp)
    yield oszi
    oszi.close()


class Lab:
    def __init__(self):
        self.calls: list[object] = []

    @button("Zero", tooltip="Zero the sensor")
    def zero(self):
        self.calls.append("zero")

    @toggle("Heater", tooltip="Switch the heater")
    def heater(self, on: bool):
        self.calls.append(("heater", on))


def texts(window: Oscilab) -> list[str]:
    return [a.text() for a in window.toolbar.actions() if a.text()]  # Skips separators and combo boxes


def action(window: Oscilab, text: str):
    return next(a for a in window.toolbar.actions() if a.text() == text)


def test_controls_go_after_the_built_in_ones_in_definition_order(window):
    window.add_toolbar(Lab())
    assert texts(window)[-3:] == ["Full Detail", "Zero", "Heater"]  # "Full Detail" is the Downsample toggle


def test_a_button_calls_its_method(window):
    lab = Lab()
    window.add_toolbar(lab)
    action(window, "Zero").trigger()
    assert lab.calls == ["zero"]
    assert action(window, "Zero").toolTip() == "Zero the sensor"


def test_a_toggle_reports_its_new_state(window):
    lab = Lab()
    window.add_toolbar(lab)
    heater = action(window, "Heater")
    assert heater.isCheckable()
    assert not heater.isChecked()

    heater.trigger()
    heater.trigger()
    assert lab.calls == [("heater", True), ("heater", False)]


def test_a_toggle_can_start_on_without_calling_its_method(window):
    class Tools:
        def __init__(self):
            self.calls = []

        @toggle("Record", checked=True)
        def record(self, on):
            self.calls.append(on)

    tools = Tools()
    window.add_toolbar(tools)
    assert action(window, "Record").isChecked()
    assert tools.calls == []


def test_classmethods_work_passing_the_class_itself(window):
    class Shared:
        clicks = 0

        @classmethod
        @button("Count")
        def count(cls):
            cls.clicks += 1

        @button("Also")  # Either decorator order
        @classmethod
        def also(cls):
            cls.clicks += 10

    window.add_toolbar(Shared)
    action(window, "Count").trigger()
    action(window, "Also").trigger()
    assert Shared.clicks == 11


def test_regular_methods_need_an_instance(window):
    with pytest.raises(TypeError, match=r"Lab\.zero is a regular method: pass an instance"):
        window.add_toolbar(Lab)


def test_a_class_without_controls_is_refused(window):
    class Nothing:
        def helper(self):
            pass

    with pytest.raises(ValueError, match="no @button or @toggle"):
        window.add_toolbar(Nothing())


def test_base_class_controls_come_first_and_overrides_keep_their_place(window):
    class More(Lab):
        @button("Calibrate")
        def calibrate(self):
            self.calls.append("calibrate")

        @button("Zero!")  # Overrides Lab.zero
        def zero(self):
            self.calls.append("zero!")

    more = More()
    window.add_toolbar(more)
    assert texts(window)[-3:] == ["Zero!", "Heater", "Calibrate"]
    action(window, "Zero!").trigger()
    assert more.calls == ["zero!"]


class Broken:
    @button("Explode")
    def explode(self):
        raise RuntimeError("boom")

    @toggle("Stuck")
    def stuck(self, _on):
        raise OSError("device busy")


def test_a_failing_method_is_reported_not_fatal(window, monkeypatch):
    shown = []
    monkeypatch.setattr(window.statusBar(), "showMessage", lambda text, _timeout=0: shown.append(text))
    window.add_toolbar(Broken())

    action(window, "Explode").trigger()
    assert shown[-1] == "✕ 'Explode' failed: RuntimeError: boom"


def test_a_failing_toggle_flips_back(window):
    window.add_toolbar(Broken())
    stuck = action(window, "Stuck")
    stuck.trigger()
    assert not stuck.isChecked()  # Its method failed, so it's still off


# --- producers' controls ------------------------------------------------------


class Device(SyncDataProducer):
    """A producer with toolbar controls, recording which thread each ran on."""

    channels = ("a", "b")

    def __init__(self):
        super().__init__(fs=100.0, interval_s=0.005)
        self.calls: list[tuple] = []

    def _generate(self, _elapsed):
        return np.array([]), np.array([])

    @button("Zero", tooltip="Zero the sensor")
    def zero(self):
        self.calls.append(("zero", threading.get_ident()))

    @toggle("Heater")
    def heater(self, on: bool):
        self.calls.append(("heater", on, threading.get_ident()))

    @button("Explode")
    def explode(self):
        raise RuntimeError("action failed")


def wait_until(qapp, condition, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for condition"
        qapp.processEvents()
        time.sleep(0.005)


def test_producer_controls_go_in_the_toolbar_captioned_with_its_signals(window):
    window.attach_producer(Device(), ["x", "y"])

    assert texts(window)[-3:] == ["Zero", "Heater", "Explode"]
    captions = [label.text() for label in window.toolbar.findChildren(qtw.QLabel)]
    assert "x, y:" in captions  # Tells two producers' "Zero" buttons apart


def test_producer_controls_run_on_the_producer_thread(qapp, window):
    device = Device()
    window.attach_producer(device, ["x", "y"])

    action(window, "Zero").trigger()
    action(window, "Heater").trigger()
    wait_until(qapp, lambda: len(device.calls) == 2)

    (zero, zero_thread), (heater, on, heater_thread) = device.calls
    assert (zero, heater, on) == ("zero", "heater", True)
    assert MAIN_THREAD not in (zero_thread, heater_thread)


def test_a_failing_producer_control_stops_the_producer(qapp, window):
    window.attach_producer(Device(), ["x", "y"])
    action(window, "Explode").trigger()
    wait_until(qapp, lambda: "x" in window.workspace._producer_errors)
    assert window.workspace._producer_errors["x"] == "RuntimeError: action failed"


def test_producer_controls_are_refused_while_it_is_stopped(window, monkeypatch):
    shown = []
    monkeypatch.setattr(window.statusBar(), "showMessage", lambda text, _timeout=0: shown.append(text))
    device = Device()
    window.attach_producer(device, ["x", "y"])
    device.stop()

    action(window, "Zero").trigger()
    assert shown[-1] == "✕ 'Zero' failed: RuntimeError: Device isn't running"
    heater = action(window, "Heater")
    heater.trigger()
    assert not heater.isChecked()  # Didn't take, so it flips back
    assert device.calls == []


def test_producers_without_controls_add_nothing(window):
    before = len(window.toolbar.actions())
    window.attach_producer(SyntheticProducer(fs=100), "s")
    assert len(window.toolbar.actions()) == before


def test_long_captions_are_shortened():
    assert _signals_caption(["a"]) == "a"
    assert _signals_caption(["a", "b", "c"]) == "a, b, c"
    assert _signals_caption([f"ch{i}" for i in range(1, 17)]) == "ch1 … ch16"


def test_one_off_controls_take_any_callable(window):
    clicks = []
    window.add_toolbar_button("Ping", lambda: clicks.append("ping"))
    window.add_toolbar_toggle("Mute", clicks.append)
    action(window, "Ping").trigger()
    action(window, "Mute").trigger()
    assert clicks == ["ping", True]
