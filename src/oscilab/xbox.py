"""
An Xbox controller's sticks, triggers and buttons, as signals.

    from oscilab import XboxControllerProducer

    pad = XboxControllerProducer()
    scope.attach_producer(pad, pad.channels)  # One signal per channel, named like it

Reads the controller through Windows' XInput API — Windows only. That covers
Xbox One S and Series controllers over Bluetooth: pair it first, in Windows'
Settings > Bluetooth & devices > Add device > Bluetooth, holding the
controller's pair button until its Xbox logo flashes fast. Over USB or the
Xbox Wireless Adapter works the same way.

Channels, all of them by default (pass `channels=` for a subset, in any
order):

- the sticks: `left_x`, `left_y`, `right_x`, `right_y` — from -1 to 1, right
  and up positive, 0 inside XInput's recommended dead zone (`deadzone=False`
  for the raw values);
- the triggers: `left_trigger`, `right_trigger` — from 0 to 1;
- the buttons: `a`, `b`, `x`, `y`, `lb`, `rb`, `back`, `start`,
  `left_stick`, `right_stick` (pressing the sticks in), `dpad_up`,
  `dpad_down`, `dpad_left`, `dpad_right` — 1 while held, 0 otherwise. (XInput
  doesn't report the Xbox button.)

The controller is read `fs` times a second. `slot` picks one of up to four
(0-3, in the order Windows numbered them); by default, the first one
connected when the producer starts. With no controller there — or once it
disconnects, e.g. to sleep — the producer stops with an error, and its rows'
Restart button tries again. Its Producers-menu entry has "Rumble": a short
buzz, to tell which controller it's reading.
"""

import ctypes
import math
import sys
import time
from collections.abc import Sequence
from typing import NamedTuple

import numpy as np
from PyQt6 import QtCore as qtc

from oscilab.producers import SyncDataProducer
from oscilab.toolbar import button

STICKS = ("left_x", "left_y", "right_x", "right_y")
TRIGGERS = ("left_trigger", "right_trigger")
BUTTONS = {  # Their bits in XInput's `wButtons`
    "a": 0x1000,
    "b": 0x2000,
    "x": 0x4000,
    "y": 0x8000,
    "lb": 0x0100,
    "rb": 0x0200,
    "back": 0x0020,
    "start": 0x0010,
    "left_stick": 0x0040,
    "right_stick": 0x0080,
    "dpad_up": 0x0001,
    "dpad_down": 0x0002,
    "dpad_left": 0x0004,
    "dpad_right": 0x0008,
}
CHANNELS = (*STICKS, *TRIGGERS, *BUTTONS)

# XInput's recommended dead zones (XINPUT_GAMEPAD_LEFT_THUMB_DEADZONE, ...),
# as a fraction of full scale
_LEFT_DEADZONE = 7849 / 32767
_RIGHT_DEADZONE = 8689 / 32767
_TRIGGER_THRESHOLD = 30 / 255

_RUMBLE_S = 0.4


class PadState(NamedTuple):
    """One raw XInput reading (its XINPUT_GAMEPAD)."""

    buttons: int  # Bit flags, see BUTTONS
    left_trigger: int  # 0..255
    right_trigger: int
    left_x: int  # -32768..32767
    left_y: int
    right_x: int
    right_y: int


class _Gamepad(ctypes.Structure):
    _fields_ = (
        ("wButtons", ctypes.c_uint16),
        ("bLeftTrigger", ctypes.c_uint8),
        ("bRightTrigger", ctypes.c_uint8),
        ("sThumbLX", ctypes.c_int16),
        ("sThumbLY", ctypes.c_int16),
        ("sThumbRX", ctypes.c_int16),
        ("sThumbRY", ctypes.c_int16),
    )


class _State(ctypes.Structure):
    _fields_ = (("dwPacketNumber", ctypes.c_uint32), ("Gamepad", _Gamepad))


class _Vibration(ctypes.Structure):
    _fields_ = (("wLeftMotorSpeed", ctypes.c_uint16), ("wRightMotorSpeed", ctypes.c_uint16))


class XInput:
    """Windows' XInput API, through ctypes: read a controller, set its rumble motors."""

    ERROR_DEVICE_NOT_CONNECTED = 1167
    _dll: ctypes.CDLL  # A WinDLL: declared here too, for type checkers on other platforms

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("XboxControllerProducer needs Windows: it reads the controller through XInput")
        for name in ("xinput1_4", "xinput9_1_0", "xinput1_3"):  # Newest first; 1_4 ships with Windows 8+
            try:
                self._dll = ctypes.WinDLL(name)
                break
            except OSError:
                continue
        else:
            raise OSError("XInput isn't installed: no xinput1_4.dll, xinput9_1_0.dll or xinput1_3.dll")

    def get_state(self, slot: int) -> PadState | None:
        """The controller in `slot` (0-3) right now, or None if there's none connected there."""
        state = _State()
        result = self._dll.XInputGetState(slot, ctypes.byref(state))
        if result == self.ERROR_DEVICE_NOT_CONNECTED:
            return None
        if result != 0:
            raise OSError(f"XInputGetState failed with error {result}")
        pad = state.Gamepad
        return PadState(
            pad.wButtons, pad.bLeftTrigger, pad.bRightTrigger, pad.sThumbLX, pad.sThumbLY, pad.sThumbRX, pad.sThumbRY
        )

    def set_vibration(self, slot: int, left: float, right: float) -> None:
        """Run the low-frequency (`left`) and high-frequency (`right`) motors, each 0 (off) to 1."""
        vibration = _Vibration(round(left * 0xFFFF), round(right * 0xFFFF))
        self._dll.XInputSetState(slot, ctypes.byref(vibration))


def _load_xinput() -> XInput:
    return XInput()


class XboxControllerProducer(SyncDataProducer):
    """An Xbox controller's sticks, triggers and buttons — see the module docstring."""

    def __init__(
        self,
        fs: float = 100.0,
        slot: int | None = None,
        channels: Sequence[str] = CHANNELS,
        deadzone: bool = True,
        parent: qtc.QObject | None = None,
    ) -> None:
        unknown = [name for name in channels if name not in CHANNELS]
        if unknown or not channels:
            raise ValueError(f"Unknown channel(s) {unknown}; choose from {list(CHANNELS)}")
        if slot is not None and slot not in range(4):
            raise ValueError(f"slot must be 0-3 (or None for the first controller connected), got {slot}")
        super().__init__(fs=fs, interval_s=1 / fs, parent=parent)
        self.channels = tuple(channels)
        self.slot = slot
        self.deadzone = deadzone
        self._xinput = _load_xinput()
        self._reading: int | None = None  # The slot read while running
        self._samples_emitted = 0
        self._run_origin = 0  # Samples emitted before the current run started
        self._rumble_until: float | None = None

    def on_start(self) -> None:
        # Carry on from the samples already sent, as the synthetic producers do
        self._run_origin = self._samples_emitted
        if self.slot is None:
            self._reading = self.__first_connected()
        elif self._xinput.get_state(self.slot) is None:
            raise RuntimeError(f"No Xbox controller in slot {self.slot}. {_PAIRING_HINT}")
        else:
            self._reading = self.slot

    def __first_connected(self) -> int:
        for slot in range(4):
            if self._xinput.get_state(slot) is not None:
                return slot
        raise RuntimeError(f"No Xbox controller connected. {_PAIRING_HINT}")

    def _generate(self, elapsed: float) -> tuple[np.ndarray, np.ndarray]:
        slot = self.__slot()
        pad = self._xinput.get_state(slot)
        if pad is None:
            raise RuntimeError(f"The Xbox controller in slot {slot} disconnected")
        if self._rumble_until is not None and time.monotonic() >= self._rumble_until:
            self.__stop_rumble()

        due = self._run_origin + int(elapsed * self.fs) - self._samples_emitted
        if due <= 0:
            return np.array([]), np.empty((len(self.channels), 0))
        n = np.arange(self._samples_emitted, self._samples_emitted + due)
        self._samples_emitted += due
        # Every sample due holds the state just read: the controller is polled, not streamed
        values = self.values(pad)
        return n / self.fs, np.repeat(values[:, np.newaxis], due, axis=1)

    def values(self, pad: PadState) -> np.ndarray:
        """The values of `channels` for one reading, in that order."""
        left_x, left_y = self.__stick(pad.left_x, pad.left_y, _LEFT_DEADZONE)
        right_x, right_y = self.__stick(pad.right_x, pad.right_y, _RIGHT_DEADZONE)
        readings = {
            "left_x": left_x,
            "left_y": left_y,
            "right_x": right_x,
            "right_y": right_y,
            "left_trigger": self.__trigger(pad.left_trigger),
            "right_trigger": self.__trigger(pad.right_trigger),
        }
        readings |= {name: float(bool(pad.buttons & bit)) for name, bit in BUTTONS.items()}
        return np.array([readings[name] for name in self.channels])

    def __stick(self, raw_x: int, raw_y: int, deadzone: float) -> tuple[float, float]:
        x, y = max(raw_x / 32767, -1.0), max(raw_y / 32767, -1.0)
        if not self.deadzone:
            return x, y
        # Radial: the stick's distance from center counts, not each axis on its
        # own, and it's rescaled so moving just past the dead zone starts at 0.
        magnitude = math.hypot(x, y)
        if magnitude <= deadzone:
            return 0.0, 0.0
        scale = min((magnitude - deadzone) / (1 - deadzone), 1.0) / magnitude
        return x * scale, y * scale

    def __trigger(self, raw: int) -> float:
        value = raw / 255
        if not self.deadzone:
            return value
        return max(value - _TRIGGER_THRESHOLD, 0.0) / (1 - _TRIGGER_THRESHOLD)

    def __slot(self) -> int:
        if self._reading is None:
            raise RuntimeError("Not started")
        return self._reading

    @button("Rumble", tooltip="A short buzz, to tell which controller this is")
    def rumble(self) -> None:
        self._xinput.set_vibration(self.__slot(), 0.5, 0.5)
        self._rumble_until = time.monotonic() + _RUMBLE_S

    def __stop_rumble(self) -> None:
        self._rumble_until = None
        self._xinput.set_vibration(self.__slot(), 0.0, 0.0)

    def on_stop(self) -> None:
        if self._rumble_until is not None:
            self.__stop_rumble()


_PAIRING_HINT = (
    "Pair it in Windows' Settings > Bluetooth & devices > Add device (or plug it in), "
    "wake it with the Xbox button, then press Restart."
)
