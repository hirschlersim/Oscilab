"""
Sensor Logger's HTTP Push: the listener, the producers it feeds, and a
window showing a phone's sensors. Pushes go to a real listener on
localhost, as the app's would over the network.
"""

import json
import socket
import time
import urllib.error
import urllib.request

import numpy as np
import pytest
from loguru import logger

from oscilab import sensor_logger
from oscilab.oscilab import Oscilab
from oscilab.sensor_logger import SENSORS, SensorLoggerProducer, attach_sensor_logger, push_url

HOST = "127.0.0.1"  # Not every interface, as the app needs: no firewall prompt for the tests


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


def push(port: int, payload: list[dict] | None = None, raw: bytes | None = None) -> tuple[int, str]:
    """POST as Sensor Logger does; returns (status, body)."""
    body = (
        raw
        if raw is not None
        else json.dumps({"messageId": 0, "sessionId": "s", "deviceId": "d", "payload": payload}).encode()
    )
    request = urllib.request.Request(
        f"http://{HOST}:{port}/data", data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 — a URL built here, on localhost
            return response.status, response.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def reading(name: str, t_ns: int, **values) -> dict:
    return {"name": name, "time": t_ns, "values": values}


def listening(sensor: str, port: int, **kwargs) -> SensorLoggerProducer:
    """A producer as its thread sees it once started: subscribed to its port's listener."""
    producer = SensorLoggerProducer(sensor, port=port, host=HOST, **kwargs)
    producer.on_start()
    return producer


@pytest.fixture
def port():
    return free_port()


def seconds(t_ns: int) -> float:
    """What a reading's phone time becomes: seconds since Oscilab first listened."""
    assert sensor_logger._origin_ns is not None
    return (t_ns - sensor_logger._origin_ns) / 1e9


# --- the producers ----------------------------------------------------------------------------


def test_known_sensors_have_their_channels_and_units():
    accelerometer = SensorLoggerProducer("accelerometer")
    assert (accelerometer.channels, accelerometer.units) == (("x", "y", "z"), ("m/s²",) * 3)
    assert SensorLoggerProducer("orientation", channels=["qw", "pitch"]).units == ("", "rad")  # Any values it sends
    assert set(SENSORS) >= {"accelerometer", "gyroscope", "magnetometer", "orientation", "location"}


def test_an_unknown_sensor_needs_its_channels():
    with pytest.raises(ValueError, match="pass channels="):
        SensorLoggerProducer("heartrate")
    assert SensorLoggerProducer("heartrate", channels=["bpm"]).channels == ("bpm",)


def test_its_arguments_are_saved_in_sessions():
    producer = SensorLoggerProducer("gyroscope", port=8123, fs=200.0)
    assert producer.config() == {"sensor": "gyroscope", "port": 8123, "fs": 200.0}


# --- pushes, as the app sends them ------------------------------------------------------------


def test_readings_arrive_in_time_order_on_the_phones_clock(port):
    producer = listening("accelerometer", port)
    base = time.time_ns()
    status, _ = push(port, [reading("accelerometer", base + 20_000_000, x=2.0, y=0.0, z=9.8),
                            reading("accelerometer", base, x=1.0, y=0.0, z=9.8),
                            reading("gyroscope", base, x=5.0, y=5.0, z=5.0)])  # fmt: skip
    assert status == 200

    t, x = producer._generate(0.0)
    np.testing.assert_allclose(t, [seconds(base), seconds(base + 20_000_000)])  # Sorted, 20 ms apart
    np.testing.assert_allclose(x, [[1.0, 2.0], [0.0, 0.0], [9.8, 9.8]])  # The gyroscope's isn't its
    producer.on_stop()


def test_a_reading_sent_again_or_late_isnt_passed_on_twice(port):
    producer = listening("microphone", port)
    base = time.time_ns()
    push(port, [reading("microphone", base + 1_000_000, dBFS=-40.0)])
    producer._generate(0.0)
    push(port, [reading("microphone", base, dBFS=-30.0), reading("microphone", base + 1_000_000, dBFS=-40.0)])
    t, _ = producer._generate(0.0)
    assert t.size == 0
    producer.on_stop()


def test_values_that_arent_numbers_read_as_nan(port):
    producer = listening("battery", port, channels=["batteryLevel", "batteryState"])
    push(port, [reading("battery", time.time_ns(), batteryLevel=0.8, batteryState="charging")])
    _, x = producer._generate(0.0)
    assert x[0, 0] == 0.8
    assert np.isnan(x[1, 0])
    producer.on_stop()


def test_producers_on_one_port_share_its_listener(port):
    accelerometer = listening("accelerometer", port)
    gyroscope = listening("gyroscope", port)
    now = time.time_ns()
    push(port, [reading("accelerometer", now, x=1, y=2, z=3), reading("gyroscope", now, x=4, y=5, z=6)])
    np.testing.assert_allclose(accelerometer._generate(0.0)[1][:, 0], [1, 2, 3])
    np.testing.assert_allclose(gyroscope._generate(0.0)[1][:, 0], [4, 5, 6])

    accelerometer.on_stop()
    assert push(port, [])[0] == 200  # Still listening, for the gyroscope
    gyroscope.on_stop()
    assert sensor_logger._listener(HOST, port)._server is None  # The last one gone: not listening anymore


def test_a_browser_is_told_it_reached_oscilab(port):
    producer = listening("accelerometer", port)
    with urllib.request.urlopen(f"http://{HOST}:{port}/data", timeout=5) as response:
        assert "Oscilab is listening" in response.read().decode()
    producer.on_stop()


@pytest.mark.parametrize(("raw", "status"), [(b"{not json", 400), (b"[1, 2]", 400), (b"", 400)])
def test_bad_pushes_are_turned_away(port, raw, status):
    producer = listening("accelerometer", port)
    assert push(port, raw=raw)[0] == status
    assert push(port, [])[0] == 200  # And it carries on
    producer.on_stop()


def test_a_port_in_use_is_reported(port):
    with socket.socket() as taken:
        taken.bind((HOST, port))
        taken.listen()
        with pytest.raises(RuntimeError, match=f"Can't listen on port {port}"):
            listening("accelerometer", port)


def test_it_measures_the_rate_readings_arrive_at(port):
    warnings: list[str] = []
    sink = logger.add(lambda message: warnings.append(message.record["message"]), level="WARNING")
    try:
        producer = listening("gyroscope", port, fs=100.0)
        base = time.time_ns()
        push(port, [reading("gyroscope", base + n * 20_000_000, x=0, y=0, z=0) for n in range(150)])  # 3 s at 50 Hz
        producer._generate(0.0)
        producer.on_stop()
    finally:
        logger.remove(sink)
    assert producer.measured_rate == pytest.approx(50.0)
    assert any("arrives at about 50 Hz, but its producer has fs=100" in w for w in warnings)


def test_the_push_url_is_this_pcs_address():
    url = push_url(8123)
    assert url.startswith("http://")
    assert url.endswith(":8123/data")


# --- in a window ----------------------------------------------------------------------------------


def test_a_phones_sensors_in_a_window(qapp, port):
    scope = Oscilab(app=qapp)
    try:
        names = attach_sensor_logger(scope, ["accelerometer", "gyroscope"], port=port, host=HOST)
        assert names == {
            "accelerometer": ["accelerometer x", "accelerometer y", "accelerometer z"],
            "gyroscope": ["gyroscope x", "gyroscope y", "gyroscope z"],
        }
        assert scope.workspace.signals["gyroscope z"].unit == "rad/s"

        deadline = time.monotonic() + 5.0
        while sensor_logger._listener(HOST, port)._server is None:  # Once a producer's thread is listening
            assert time.monotonic() < deadline, "timed out waiting for the listener"
            qapp.processEvents()
            time.sleep(0.005)
        base = time.time_ns()
        push(port, [reading("accelerometer", base + n * 10_000_000, x=n, y=0, z=9.8) for n in range(5)])
        model = scope.workspace.signals["accelerometer x"]
        while len(model.data) < 5:
            assert time.monotonic() < deadline, "timed out waiting for the readings"
            qapp.processEvents()
            time.sleep(0.005)
        np.testing.assert_allclose(np.asarray(model.data), [0, 1, 2, 3, 4])
    finally:
        scope.close()
