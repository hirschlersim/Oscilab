"""
SerialProducer, against a fake serial port: pyserial isn't a dependency of
Oscilab (nor installed in CI), so a stand-in `serial` module takes its place.
"""

import sys
import time
import types

import numpy as np
import pytest
from PyQt6 import QtWidgets as qtw

from oscilab.producers import SerialProducer


class FakeSerial:
    """Stands in for `serial.Serial`: `readline()` serves `lines`, then b"" (as a read timeout does)."""

    def __init__(self, port: str, baudrate: int, timeout: float):
        self.port, self.baudrate, self.timeout = port, baudrate, timeout
        self.lines: list[bytes] = []
        self.is_open = True
        self.calls: list[str] = []

    def readline(self) -> bytes:
        return self.lines.pop(0) if self.lines else b""

    def open(self) -> None:
        self.is_open = True
        self.calls.append("open")

    def close(self) -> None:
        self.is_open = False
        self.calls.append("close")


@pytest.fixture(autouse=True)
def fake_pyserial(monkeypatch):
    module = types.ModuleType("serial")
    module.Serial = FakeSerial  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "serial", module)


def wait_until(condition, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for condition"
        qtw.QApplication.processEvents()  # Delivers the producer's queued data
        time.sleep(0.005)


def port_of(producer: SerialProducer) -> FakeSerial:
    port = producer._connection
    assert isinstance(port, FakeSerial)
    return port


def reading(lines: list[bytes], **kwargs) -> tuple[SerialProducer, FakeSerial]:
    """A producer mid-run (as `_generate` sees it on its thread) with `lines` waiting on its port."""
    producer = SerialProducer("COM3", fs=100.0, **kwargs)
    port = port_of(producer)
    port.lines = list(lines)
    producer._running = True
    return producer, port


def test_opens_the_port_it_was_given():
    producer = SerialProducer("COM7", fs=50.0, baudrate=9600)
    port = port_of(producer)
    assert (port.port, port.baudrate, port.timeout) == ("COM7", 9600, 1.0)
    assert producer.config() == {"port": "COM7", "fs": 50.0, "baudrate": 9600}  # What a session saves


def test_one_sample_per_line_timed_by_fs():
    producer, _ = reading([b"1.5\n", b"-2.5\n", b"3\n"], chunk_size=2)

    t, x = producer._generate(0.0)
    np.testing.assert_allclose(t, [0.0, 0.01])
    np.testing.assert_allclose(x, [[1.5, -2.5]])

    t, x = producer._generate(0.0)  # Time carries on from the samples already read
    np.testing.assert_allclose(t, [0.02])
    np.testing.assert_allclose(x, [[3.0]])


def test_comma_separated_values_fill_several_channels():
    producer, _ = reading([b"1,2,3\n", b"4.5,5.5,6.5\r\n"], channels=("x", "y", "z"))

    _, x = producer._generate(0.0)
    np.testing.assert_allclose(x, [[1.0, 4.5], [2.0, 5.5], [3.0, 6.5]])  # One row per channel


def test_malformed_lines_are_skipped():
    producer, _ = reading([b"garbage\n", b"1,2\n", b"4,5,6\n", b"7,8,9,10\n"], channels=("x", "y", "z"))

    t, x = producer._generate(0.0)
    assert len(t) == 1  # Only the line with one value per channel
    np.testing.assert_allclose(x, [[4.0], [5.0], [6.0]])


def test_a_quiet_port_gives_an_empty_chunk():
    producer, _ = reading([])

    t, x = producer._generate(0.0)
    assert t.size == 0
    assert x.size == 0


def test_a_stop_doesnt_wait_out_the_chunk():
    producer, port = reading([b"1\n", b"2\n"])
    producer._running = False

    t, _ = producer._generate(0.0)
    assert t.size == 0
    assert len(port.lines) == 2  # Didn't read on


def test_reconnect_closes_and_reopens_the_port():
    producer, port = reading([])
    producer.reconnect()
    assert port.calls == ["close", "open"]
    assert port.is_open


def test_runs_on_its_thread_and_closes_the_port_when_it_stops(workspace):
    producer = SerialProducer("COM3", fs=100.0, channels=("a", "b"))
    port = port_of(producer)
    port.lines = [b"1,10\n", b"2,20\n", b"3,30\n"]
    workspace.attach_producer(producer, ["a", "b"])

    b = workspace.signals["b"]
    wait_until(lambda: len(b.data) == 3)
    np.testing.assert_allclose(np.asarray(b.data), [10.0, 20.0, 30.0])

    workspace.stop_producer("a")
    assert not port.is_open  # on_stop() closed it
    workspace.start_producer("a")
    wait_until(lambda: port.is_open)  # on_start() reopens it, on the producer's thread


def test_says_what_to_install_without_pyserial(monkeypatch):
    monkeypatch.setitem(sys.modules, "serial", None)  # As if not installed: importing it fails
    with pytest.raises(ImportError, match="pip install pyserial"):
        SerialProducer("COM3", fs=100.0)
