"""
A phone's sensors, live, from the Sensor Logger app (Android and iOS).

    from oscilab.sensor_logger import attach_sensor_logger, push_url

    attach_sensor_logger(scope, ["accelerometer", "gyroscope"], fs=100)
    print(push_url())  # e.g. http://192.168.1.23:8000/data — enter it in the app

Sensor Logger's HTTP Push sends every enabled sensor's readings to a URL
you give it, as JSON, once a second by default (Plus and Pro can send more
often). Oscilab is the server: in the app, turn on Settings > Data
Streaming > HTTP Push, enter the URL `push_url()` gives — this PC's address
on the local network, the phone on the same Wi-Fi — and start recording.
The first time, Windows asks whether to let Python through its firewall:
allow it on private networks. Opening the URL in the phone's browser tells
you whether it gets through.

One `SensorLoggerProducer` per sensor: the sensors run at different rates,
and a producer's channels share one. The producers on one port share its
listener. Readings keep the phone's timestamps, as seconds since Oscilab
started listening, so the sensors stay in step; they arrive in batches, so
the plots trail the phone by the push period. `fs` should match the
sampling rate set in the app — each producer measures what arrives
(`measured_rate`) and warns in the log if they differ.

The channels and units of the common sensors are known (`SENSORS`); for
others, or other values (the orientation's quaternion `qx`...), pass
`channels=` with the keys the app sends.
"""

import json
import queue
import socket
import threading
import time
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any

import numpy as np
from loguru import logger
from PyQt6 import QtCore as qtc

from oscilab.producers import SyncDataProducer

if TYPE_CHECKING:
    from oscilab.oscilab import Oscilab

# Each sensor's values, and their units, as Sensor Logger documents them
SENSORS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "accelerometer": (("x", "y", "z"), ("m/s²",) * 3),  # Without gravity
    "gravity": (("x", "y", "z"), ("m/s²",) * 3),
    "totalacceleration": (("x", "y", "z"), ("m/s²",) * 3),  # With it
    "gyroscope": (("x", "y", "z"), ("rad/s",) * 3),
    "magnetometer": (("x", "y", "z"), ("µT",) * 3),
    "orientation": (("yaw", "pitch", "roll"), ("rad",) * 3),
    "barometer": (("pressure", "relativeAltitude"), ("hPa", "m")),
    "microphone": (("dBFS",), ("dBFS",)),
    "location": (("latitude", "longitude", "altitude", "speed"), ("°", "°", "m", "m/s")),
    "battery": (("batteryLevel",), ("",)),
}

_MAX_BODY = 50_000_000  # Bytes; a message's worth of readings is far smaller
_RATE_AFTER_S = 2.0  # Of readings, before measuring the rate they arrive at

type _Batch = tuple[list[int], list[dict[str, Any]]]  # (times in ns, values) of one sensor's readings

_origin_ns: int | None = None  # Time 0 of every Sensor Logger signal: when Oscilab first listened


def push_url(port: int = 8000) -> str:
    """The URL to enter in Sensor Logger's HTTP Push: this PC's address on its local network."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 80))  # Picks the interface the default route uses; sends nothing
            address = probe.getsockname()[0]
        except OSError:  # No network: only this PC can reach it
            address = "127.0.0.1"
    return f"http://{address}:{port}/data"


class _Listener:
    """An HTTP server on one port, handing each sensor's readings to the producers that asked for them."""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self._lock = threading.Lock()
        self._subscribers: dict[str, list[queue.SimpleQueue[_Batch]]] = {}
        self._server: ThreadingHTTPServer | None = None

    def subscribe(self, sensor: str) -> queue.SimpleQueue[_Batch]:
        """A queue that gets `sensor`'s readings from now on. Starts listening, for the first one."""
        with self._lock:
            if self._server is None:
                self.__start()
            readings: queue.SimpleQueue[_Batch] = queue.SimpleQueue()
            self._subscribers.setdefault(sensor, []).append(readings)
            return readings

    def unsubscribe(self, sensor: str, readings: queue.SimpleQueue[_Batch]) -> None:
        """Stop sending `sensor`'s readings to `readings`. Stops listening, after the last one."""
        with self._lock:
            subscribers = self._subscribers.get(sensor, [])
            if readings in subscribers:
                subscribers.remove(readings)
            if not any(self._subscribers.values()) and self._server is not None:
                self._server.shutdown()
                self._server.server_close()
                self._server = None
                logger.info(f"Stopped listening for Sensor Logger on port {self.port}")

    def __start(self) -> None:
        global _origin_ns  # noqa: PLW0603 — one time origin for every listener, so their sensors line up
        if _origin_ns is None:
            _origin_ns = time.time_ns()
        server = ThreadingHTTPServer((self.host, self.port), _Handler)
        server.daemon_threads = True
        server.listener = self  # type: ignore[attr-defined]  # For _Handler
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
        self._server = server
        logger.info(f"Listening for Sensor Logger's HTTP Push: {push_url(self.port)}")

    def deliver(self, message: dict[str, Any]) -> int:
        """Hand a message's readings to the producers that want them. Returns how many readings it had."""
        batches: dict[str, _Batch] = {}
        payload = message.get("payload", [])
        for reading in payload if isinstance(payload, list) else []:
            if not isinstance(reading, dict):
                continue
            name, t, values = reading.get("name"), reading.get("time"), reading.get("values")
            if isinstance(name, str) and isinstance(t, int | float) and isinstance(values, dict):
                times, rows = batches.setdefault(name, ([], []))
                times.append(int(t))
                rows.append(values)
        with self._lock:
            for name, batch in batches.items():
                for readings in self._subscribers.get(name, []):
                    readings.put(batch)
        return sum(len(times) for times, _ in batches.values())


class _Handler(BaseHTTPRequestHandler):
    """Sensor Logger POSTs its readings here; a browser's GET gets told it's reached Oscilab."""

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        if not 0 < length <= _MAX_BODY:
            self.__reply(413 if length else 400, "Expected a JSON body")
            return
        try:
            message = json.loads(self.rfile.read(length))
        except ValueError:
            self.__reply(400, "Not JSON")
            return
        if not isinstance(message, dict):
            self.__reply(400, "Expected a JSON object")
            return
        count = self.server.listener.deliver(message)  # type: ignore[attr-defined]
        self.__reply(200, f"{count} readings")

    def do_GET(self) -> None:
        port = self.server.listener.port  # type: ignore[attr-defined]
        self.__reply(200, f"Oscilab is listening for Sensor Logger's HTTP Push on port {port}.")

    def __reply(self, status: int, text: str) -> None:
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 — http.server's name
        logger.debug(f"Sensor Logger push: {format % args}")  # Not a line on stderr per request


_listeners: dict[tuple[str, int], _Listener] = {}
_listeners_lock = threading.Lock()


def _listener(host: str, port: int) -> _Listener:
    with _listeners_lock:
        return _listeners.setdefault((host, port), _Listener(host, port))


class SensorLoggerProducer(SyncDataProducer):
    """One phone sensor, from Sensor Logger's HTTP Push — see the module docstring."""

    def __init__(
        self,
        sensor: str,
        port: int = 8000,
        fs: float = 100.0,
        channels: Sequence[str] | None = None,
        host: str = "0.0.0.0",  # noqa: S104 — every interface: the phone reaches it over the network
        parent: qtc.QObject | None = None,
    ) -> None:
        known_channels, known_units = SENSORS.get(sensor, ((), ()))
        channels = tuple(channels) if channels is not None else known_channels
        if not channels:
            raise ValueError(
                f"Sensor Logger's '{sensor}' isn't one Oscilab knows ({', '.join(SENSORS)}): "
                "pass channels=, the keys of its values"
            )
        super().__init__(fs=fs, interval_s=0.02, parent=parent)
        self.sensor = sensor
        self.port = port
        self.host = host
        self.channels = channels
        known = dict(zip(known_channels, known_units, strict=True))
        self.units = tuple(known.get(channel, "") for channel in channels)
        self.measured_rate: float | None = None  # Hz, once a couple of seconds of readings have arrived
        self._readings: queue.SimpleQueue[_Batch] | None = None
        self._last_ns: int | None = None  # The newest reading sent on, so none goes out twice or out of order
        self._first: tuple[int, int] | None = None  # (time in ns, readings so far) since the first, for the rate

    def on_start(self) -> None:
        try:
            self._readings = _listener(self.host, self.port).subscribe(self.sensor)
        except OSError as e:
            raise RuntimeError(f"Can't listen on port {self.port}: {e}") from e

    def on_stop(self) -> None:
        if self._readings is not None:
            _listener(self.host, self.port).unsubscribe(self.sensor, self._readings)
            self._readings = None

    def _generate(self, _elapsed: float) -> tuple[np.ndarray, np.ndarray]:
        times: list[int] = []
        rows: list[dict[str, Any]] = []
        while self._readings is not None:
            try:
                batch_times, batch_rows = self._readings.get_nowait()
            except queue.Empty:
                break
            times += batch_times
            rows += batch_rows
        if not times:
            return np.array([]), np.empty((len(self.channels), 0))

        order = sorted(range(len(times)), key=times.__getitem__)
        if self._last_ns is not None:
            order = [i for i in order if times[i] > self._last_ns]  # A reading sent again, or late
        if not order:
            return np.array([]), np.empty((len(self.channels), 0))
        self._last_ns = times[order[-1]]
        self.__measure(times[order[0]], times[order[-1]], len(order))

        origin = _origin_ns if _origin_ns is not None else times[order[0]]
        t = np.array([(times[i] - origin) / 1e9 for i in order])
        x = np.array([[_number(rows[i].get(channel)) for i in order] for channel in self.channels])
        return t, x

    def __measure(self, first_ns: int, last_ns: int, count: int) -> None:
        """Measure the rate readings arrive at, once there are a couple of seconds of them."""
        if self.measured_rate is not None:
            return
        if self._first is None:
            self._first = (first_ns, 0)
        start_ns, seen = self._first
        seen += count
        self._first = (start_ns, seen)
        span_s = (last_ns - start_ns) / 1e9
        if span_s < _RATE_AFTER_S:
            return
        self.measured_rate = (seen - 1) / span_s
        logger.info(f"Sensor Logger's {self.sensor} arrives at about {self.measured_rate:.0f} Hz")
        if abs(self.measured_rate - self.fs) > 0.2 * self.fs:
            logger.warning(
                f"Sensor Logger's {self.sensor} arrives at about {self.measured_rate:.0f} Hz, but its producer has "
                f"fs={self.fs:g}: set fs to match, so the plots keep the full duration"
            )


def _number(value: object) -> float:
    """A reading's value as a float; NaN for one that isn't a number (a battery state, say)."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return float(value)
    return float("nan")


def attach_sensor_logger(
    scope: "Oscilab",
    sensors: Sequence[str] = ("accelerometer", "gyroscope"),
    port: int = 8000,
    fs: float = 100.0,
    host: str = "0.0.0.0",  # noqa: S104 — every interface, as the producers'
) -> dict[str, list[str]]:
    """
    Attach one `SensorLoggerProducer` per sensor to `scope`, their signals
    named "<sensor> <value>" ("accelerometer x"). Returns each sensor's signal
    names. `fs` is the sampling rate set in the app.
    """
    names = {}
    for sensor in sensors:
        producer = SensorLoggerProducer(sensor, port=port, fs=fs, host=host)
        names[sensor] = [f"{sensor} {channel}" for channel in producer.channels]
        scope.attach_producer(producer, names[sensor])
    return names
