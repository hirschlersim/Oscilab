"""
The commands: each showcase sets up its window and its data comes in, each
benchmark prints or saves its results — so an API change can't break one
unnoticed. Data sources are stand-ins: a fake controller, readings and
packets sent from here, on localhost.
"""

import functools
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Sequence
from importlib import metadata

import pytest

from oscilab import xbox
from oscilab.examples import sensor_logger as sensor_logger_example
from oscilab.examples import udp as udp_example
from oscilab.examples import xbox as xbox_example
from oscilab.oscilab import Oscilab
from oscilab.sensor_logger import attach_sensor_logger
from oscilab.udp import UdpProducer
from oscilab.xbox import PadState

HOST = "127.0.0.1"  # Not every interface, as the commands listen on: no firewall prompt for the tests


def free_port(kind: socket.SocketKind) -> int:
    with socket.socket(socket.AF_INET, kind) as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


def run_example(
    qapp, monkeypatch, main: Callable[[Sequence[str]], int], argv: list[str], until: Callable[[Oscilab], bool]
) -> Oscilab:
    """Run `main(argv)`, its window's event loop only until `until(window)`: the window, still open."""
    windows: list[Oscilab] = []

    def run(window: Oscilab) -> int:
        windows.append(window)
        deadline = time.monotonic() + 5.0
        while not until(window):
            assert time.monotonic() < deadline, "timed out waiting for the example"
            qapp.processEvents()
            time.sleep(0.01)
        return 0

    monkeypatch.setattr(Oscilab, "run", run)
    assert main(argv) == 0
    return windows[0]


def has_data(label: str) -> Callable[[Oscilab], bool]:
    return lambda window: len(window.workspace.signals[label].data) > 0


# --- the showcases ---------------------------------------------------------------------------


class OneController:
    """XInput with a controller in slot 0, its left stick pushed right."""

    def get_state(self, slot: int) -> PadState | None:
        return PadState(0, 0, 0, 32767, 0, 0, 0) if slot == 0 else None

    def set_vibration(self, slot: int, left: float, right: float) -> None:
        pass


def test_oscilab_xbox(qapp, monkeypatch):
    monkeypatch.setattr(xbox, "_load_xinput", OneController)
    window = run_example(qapp, monkeypatch, xbox_example.main, [], until=has_data("left_x"))
    try:
        assert set(window.workspace.views) == {"Left stick", "Sticks", "Triggers", "Buttons"}
        assert window.workspace.signals["left_x"].snapshot()[1][-1] == pytest.approx(1.0)
    finally:
        window.close()


def test_oscilab_sensor_logger(qapp, monkeypatch):
    port = free_port(socket.SOCK_STREAM)
    monkeypatch.setattr(
        sensor_logger_example, "attach_sensor_logger", functools.partial(attach_sensor_logger, host=HOST)
    )
    reading = {"name": "accelerometer", "time": time.time_ns(), "values": {"x": 1.5, "y": 0.0, "z": 9.8}}
    body = json.dumps({"payload": [reading]}).encode()

    def sent_and_received(window: Oscilab) -> bool:
        try:
            request = urllib.request.Request(f"http://{HOST}:{port}/data", data=body, method="POST")
            urllib.request.urlopen(request, timeout=1).close()  # noqa: S310 — a URL built here, on localhost
        except OSError:
            return False  # Not listening yet
        return has_data("accelerometer x")(window)

    window = run_example(
        qapp, monkeypatch, sensor_logger_example.main, ["--port", str(port), "--rate", "50"], until=sent_and_received
    )
    try:
        assert {"Acceleration", "Rotation rate", "Tilt", "Histogram: accelerometer x"} == set(window.workspace.views)
        assert window.workspace.signals["accelerometer x"].snapshot()[1][0] == 1.5
        assert f":{port}/data" in window.windowTitle()
    finally:
        window.close()


def test_oscilab_udp(qapp, monkeypatch):
    port = free_port(socket.SOCK_DGRAM)
    monkeypatch.setattr(udp_example, "UdpProducer", functools.partial(UdpProducer, host=HOST))

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:

        def sent_and_received(window: Oscilab) -> bool:
            sender.sendto(b"0.5,-0.5,0.1\n", (HOST, port))  # Resent until its thread listens: UDP drops the early ones
            return has_data("x")(window)

        window = run_example(qapp, monkeypatch, udp_example.main, ["--port", str(port)], until=sent_and_received)
    try:
        assert set(window.workspace.views) == {"Samples", "x against y"}
        assert window.workspace.signals["y"].snapshot()[1][0] == -0.5
    finally:
        window.close()


# --- the benchmarks -------------------------------------------------------------------------------


def run_module(module: str, *args: str) -> str:
    """`python -m module args`, headless, in a process of its own: what it printed."""
    env = os.environ | {"QT_QPA_PLATFORM": "offscreen"}
    result = subprocess.run(  # noqa: S603 — this interpreter, our own module
        [sys.executable, "-m", module, *args], capture_output=True, text=True, env=env, timeout=120, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_oscilab_bench(tmp_path):
    # In a process of its own: it styles the whole application, and quiets its logging
    figure = tmp_path / "figure.png"
    output = run_module(
        "oscilab.benchmarks.refresh",
        *("--channels", "1", "--rates", "100", "--warmup", "0.1", "--seconds", "0.2"),
        *("--size", "640x480", "--no-show", "--output", str(figure)),
    )
    assert "1 ch x       100 Hz" in output
    assert figure.stat().st_size > 0


# --- the commands themselves ------------------------------------------------------------------------


def test_every_command_runs_a_main():
    commands = {
        entry.name: entry
        for entry in metadata.entry_points(group="console_scripts")
        if entry.value.startswith("oscilab.")
    }
    expected = {
        "oscilab",
        "oscilab-demo",
        "oscilab-xbox",
        "oscilab-sensor-logger",
        "oscilab-udp",
        "oscilab-bench",
    }
    assert expected <= set(commands)
    for name in expected:
        assert callable(commands[name].load()), name
