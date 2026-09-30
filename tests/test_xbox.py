"""
XboxControllerProducer, against a stand-in for XInput (and, on Windows, a
check that the real one loads). No controller needed.
"""

import sys
import threading
import time

import numpy as np
import pytest

from oscilab import xbox
from oscilab.xbox import BUTTONS, CHANNELS, PadState, XboxControllerProducer

REST = PadState(buttons=0, left_trigger=0, right_trigger=0, left_x=0, left_y=0, right_x=0, right_y=0)


class FakeXInput:
    """Controllers by slot; `pads[slot] = None` unplugs one."""

    def __init__(self, pads: dict[int, PadState | None]):
        self.pads = pads
        self.vibration: list[tuple[int, float, float]] = []
        self.threads: set[int] = set()

    def get_state(self, slot: int) -> PadState | None:
        self.threads.add(threading.get_ident())
        return self.pads.get(slot)

    def set_vibration(self, slot: int, left: float, right: float) -> None:
        self.vibration.append((slot, left, right))


@pytest.fixture
def xinput(monkeypatch):
    fake = FakeXInput({0: REST})
    monkeypatch.setattr(xbox, "_load_xinput", lambda: fake)
    return fake


def started(**kwargs) -> XboxControllerProducer:
    """A producer as its thread sees it, after on_start()."""
    producer = XboxControllerProducer(**kwargs)
    producer.on_start()
    return producer


def wait_until(qapp, condition, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for condition"
        qapp.processEvents()
        time.sleep(0.005)


# --- setup --------------------------------------------------------------------


def test_every_channel_by_default_or_the_ones_asked_for(xinput):  # noqa: ARG001 — patches XInput
    assert XboxControllerProducer().channels == CHANNELS
    assert len(CHANNELS) == 20
    assert XboxControllerProducer(channels=["a", "left_x"]).channels == ("a", "left_x")  # In the order given


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"channels": ["left_x", "guide"]}, r"Unknown channel\(s\) \['guide'\]"),
        ({"channels": []}, "Unknown channel"),
        ({"slot": 4}, "slot must be 0-3"),
    ],
)
def test_mistakes_are_refused(xinput, kwargs, message):  # noqa: ARG001 — patches XInput
    with pytest.raises(ValueError, match=message):
        XboxControllerProducer(**kwargs)


def test_reads_the_first_controller_connected_unless_told_which(xinput):
    xinput.pads = {0: None, 2: REST, 3: REST}
    assert started()._reading == 2
    assert started(slot=3)._reading == 3


def test_no_controller_says_how_to_connect_one(xinput):
    xinput.pads = {}
    with pytest.raises(RuntimeError, match=r"No Xbox controller connected\. Pair it .*Bluetooth"):
        started()
    with pytest.raises(RuntimeError, match="No Xbox controller in slot 1"):
        started(slot=1)


def test_its_arguments_are_saved_in_sessions(xinput):  # noqa: ARG001 — patches XInput
    producer = XboxControllerProducer(fs=50.0, slot=1, channels=("a", "b"))
    assert producer.config() == {"fs": 50.0, "slot": 1, "channels": ("a", "b")}


# --- readings -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw_x", "raw_y", "expected"),
    [
        (0, 0, (0.0, 0.0)),
        (5000, -5000, (0.0, 0.0)),  # Inside the dead zone: counts as centered
        (32767, 0, (1.0, 0.0)),
        (-32768, 0, (-1.0, 0.0)),
        (0, 32767, (0.0, 1.0)),  # Up is positive
        (32767, 32767, (np.sqrt(0.5), np.sqrt(0.5))),  # A corner reads as the edge of the circle
    ],
)
def test_sticks_read_minus_one_to_one_with_a_dead_zone(xinput, raw_x, raw_y, expected):  # noqa: ARG001
    producer = XboxControllerProducer(channels=["left_x", "left_y"])
    pad = REST._replace(left_x=raw_x, left_y=raw_y)
    np.testing.assert_allclose(producer.values(pad), expected, atol=1e-9)


def test_just_past_the_dead_zone_reads_near_zero(xinput):  # noqa: ARG001 — patches XInput
    producer = XboxControllerProducer(channels=["left_x"])
    (value,) = producer.values(REST._replace(left_x=7900))  # The dead zone ends at 7849
    assert 0 < value < 0.01


def test_without_the_dead_zone_values_are_raw(xinput):  # noqa: ARG001 — patches XInput
    producer = XboxControllerProducer(channels=["left_x", "right_trigger"], deadzone=False)
    np.testing.assert_allclose(producer.values(REST._replace(left_x=5000, right_trigger=15)), [5000 / 32767, 15 / 255])


def test_triggers_read_zero_to_one(xinput):  # noqa: ARG001 — patches XInput
    producer = XboxControllerProducer(channels=["left_trigger", "right_trigger"])
    np.testing.assert_allclose(producer.values(REST._replace(left_trigger=255, right_trigger=20)), [1.0, 0.0])


def test_each_button_reads_one_while_held(xinput):  # noqa: ARG001 — patches XInput
    producer = XboxControllerProducer(channels=list(BUTTONS))
    for name, bit in BUTTONS.items():
        values = dict(zip(BUTTONS, producer.values(REST._replace(buttons=bit)), strict=True))
        assert values == {other: float(other == name) for other in BUTTONS}


def test_samples_due_hold_the_state_just_read(xinput):
    xinput.pads[0] = REST._replace(buttons=BUTTONS["a"], right_trigger=255)
    producer = started(fs=100.0, channels=["a", "right_trigger"])

    t, x = producer._generate(0.03)
    np.testing.assert_allclose(t, [0.0, 0.01, 0.02])
    np.testing.assert_allclose(x, [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0]])

    t, x = producer._generate(0.03)  # Nothing new is due yet
    assert t.size == 0
    assert x.shape == (2, 0)


def test_a_disconnect_stops_it_with_an_error(xinput):
    producer = started()
    xinput.pads[0] = None  # Gone to sleep, say
    with pytest.raises(RuntimeError, match="slot 0 disconnected"):
        producer._generate(0.1)


def test_rumble_buzzes_briefly(xinput, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(xbox.time, "monotonic", lambda: now[0])
    producer = started()

    producer.rumble()
    assert xinput.vibration == [(0, 0.5, 0.5)]
    producer._generate(0.0)
    assert len(xinput.vibration) == 1  # Still buzzing

    now[0] += 0.5
    producer._generate(0.0)
    assert xinput.vibration[-1] == (0, 0.0, 0.0)


def test_stopping_stops_a_rumble(xinput):
    producer = started()
    producer.rumble()
    producer.on_stop()
    assert xinput.vibration[-1] == (0, 0.0, 0.0)


# --- in a window ------------------------------------------------------------------


def test_feeds_its_signals_from_its_own_thread(qapp, workspace, xinput):
    xinput.pads[0] = REST._replace(buttons=BUTTONS["b"])
    pad = XboxControllerProducer(channels=["left_x", "b"])
    workspace.attach_producer(pad, pad.channels)

    wait_until(qapp, lambda: len(workspace.signals["b"].data) > 0)
    assert set(np.asarray(workspace.signals["b"].data)) == {1.0}
    assert threading.get_ident() not in xinput.threads


def test_without_a_controller_its_rows_show_the_error(qapp, workspace, xinput):
    xinput.pads = {}
    workspace.attach_producer(XboxControllerProducer(channels=["a"]), "a")
    wait_until(qapp, lambda: workspace.producer_state("a") == "error")
    assert workspace._producer_errors["a"].startswith("RuntimeError: No Xbox controller connected")

    xinput.pads = {0: REST}  # Paired and woken: Restart picks it up
    workspace.restart_producer("a")
    assert workspace.producer_state("a") == "running"


@pytest.mark.skipif(sys.platform != "win32", reason="XInput is Windows-only")
def test_the_real_xinput_loads():
    reading = xbox.XInput().get_state(3)
    assert reading is None or isinstance(reading, PadState)  # A controller may or may not be connected
