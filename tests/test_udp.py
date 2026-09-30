"""
UdpProducer: parsing text and binary packets, timing samples, and real
packets over localhost into a workspace.
"""

import socket
import struct
import time

import numpy as np
import pytest
from PyQt6 import QtCore as qtc

from oscilab.udp import UdpProducer

HOST = "127.0.0.1"  # Not every interface: no firewall prompt for the tests


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


class Chunks:
    """What a producer emits, collected as it's emitted."""

    def __init__(self, producer: UdpProducer):
        self.t: list[np.ndarray] = []
        self.x: list[np.ndarray] = []
        producer.data_ready.connect(self.add, qtc.Qt.ConnectionType.DirectConnection)  # type: ignore[call-arg]

    def add(self, t: np.ndarray, x: np.ndarray) -> None:
        self.t.append(t)
        self.x.append(x)

    def all(self) -> tuple[np.ndarray, np.ndarray]:
        return np.concatenate(self.t), np.concatenate(self.x, axis=1)


def parsed(producer: UdpProducer, *packets: bytes) -> tuple[np.ndarray, np.ndarray]:
    """What `producer` passes on for `packets`, as its thread would."""
    chunks = Chunks(producer)
    for packet in packets:
        producer.take(packet)
    producer._flush()
    return chunks.all()


def wait_until(qapp, condition, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for condition"
        qapp.processEvents()
        time.sleep(0.005)


# --- formats -----------------------------------------------------------------------------------


def test_text_lines_are_samples_numbered_at_fs():
    producer = UdpProducer(channels=("a", "b"), fs=10.0)
    t, x = parsed(producer, b"1,2\n3,4\n", b"5,6")  # A packet may hold several lines, or one without a newline
    np.testing.assert_allclose(t, [0.0, 0.1, 0.2])
    np.testing.assert_allclose(x, [[1, 3, 5], [2, 4, 6]])

    t, _ = parsed(producer, b"7,8\n")
    np.testing.assert_allclose(t, [0.3])  # Numbering carries on


def test_a_time_column_keeps_the_senders_times():
    producer = UdpProducer(channels=("a",), time_column=True)
    t, x = parsed(producer, b"10.5,1\n10.52,2\n11.9,3\n")
    np.testing.assert_allclose(t, [10.5, 10.52, 11.9])
    np.testing.assert_allclose(x, [[1, 2, 3]])


def test_lines_that_dont_parse_are_skipped_and_counted():
    producer = UdpProducer(channels=("a", "b"))
    _, x = parsed(producer, b"1,2\nnope\n3\n4,5,6\n\n7,8\r\n")
    np.testing.assert_allclose(x, [[1, 7], [2, 8]])
    assert producer.skipped == 3  # "nope", and the ones with the wrong number of values


def test_binary_packets_hold_whole_samples():
    producer = UdpProducer(channels=("a", "b", "c"), binary="<3f")
    packet = struct.pack("<3f", 1, 2, 3) + struct.pack("<3f", 4, 5, 6)
    _, x = parsed(producer, packet + b"\x00\x01")  # And two stray bytes
    np.testing.assert_allclose(x, [[1, 4], [2, 5], [3, 6]])
    assert producer.skipped == 2


def test_binary_with_a_time_column():
    producer = UdpProducer(channels=("a",), binary="<df", time_column=True)
    t, x = parsed(producer, struct.pack("<df", 2.25, 7.0))
    np.testing.assert_allclose((t, x[0]), ([2.25], [7.0]))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"channels": ()}, "at least one channel"),
        ({"binary": "<3q7"}, "isn't a struct format"),
        ({"channels": ("a", "b"), "binary": "<3f"}, r"3 value\(s\) per sample, but 2 channel\(s\) need 2"),
        ({"channels": ("a",), "binary": "<f", "time_column": True}, "and a time need 2"),
    ],
)
def test_mistakes_are_refused(kwargs, message):
    with pytest.raises(ValueError, match=message):
        UdpProducer(**kwargs)


def test_its_arguments_are_saved_in_sessions():
    producer = UdpProducer(port=9123, channels=("x", "y"), binary="<2f")
    assert producer.config() == {"port": 9123, "channels": ("x", "y"), "binary": "<2f"}


# --- over the network ---------------------------------------------------------------------------


def test_packets_reach_a_workspace(qapp, workspace):
    port = free_port()
    producer = UdpProducer(port=port, channels=("a", "b"), fs=100.0, host=HOST)
    workspace.attach_producer(producer, ["a", "b"])
    a = workspace.signals["a"]

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        deadline = time.monotonic() + 5.0
        while not len(a.data):  # Resent until its thread is listening: UDP just drops the early ones
            assert time.monotonic() < deadline, "timed out waiting for the samples"
            sender.sendto(b"1,10\n2,20\n3,30\n", (HOST, port))
            qapp.processEvents()
            time.sleep(0.02)
    np.testing.assert_allclose(np.asarray(a.data)[:3], [1, 2, 3])
    np.testing.assert_allclose(np.asarray(workspace.signals["b"].data)[:3], [10, 20, 30])


def test_a_port_in_use_is_reported(qapp, workspace):
    port = free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as taken:
        taken.bind((HOST, port))
        workspace.attach_producer(UdpProducer(port=port, host=HOST), "a")
        wait_until(qapp, lambda: workspace.producer_state("a") == "error")
    assert f"Can't listen on UDP port {port}" in workspace._producer_errors["a"]


def test_stopping_closes_the_port_and_starting_opens_it_again(qapp, workspace):
    port = free_port()
    workspace.attach_producer(UdpProducer(port=port, host=HOST), "a")
    workspace.stop_producer("a")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as other:
        other.bind((HOST, port))  # Free again

    workspace.start_producer("a")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        deadline = time.monotonic() + 5.0
        while not len(workspace.signals["a"].data):
            assert time.monotonic() < deadline, "timed out waiting for the samples"
            sender.sendto(b"5\n", (HOST, port))
            qapp.processEvents()
            time.sleep(0.02)
    assert workspace.producer_state("a") == "running"
