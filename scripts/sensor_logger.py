"""
Your phone's sensors, live, from the Sensor Logger app:

    uv run scripts/sensor_logger.py              # the app sampling at 100 Hz
    uv run scripts/sensor_logger.py --rate 400   # ...or whatever you've set it to

1. Phone and PC on the same Wi-Fi. Run this; the window's title shows the
   URL to push to, e.g. http://192.168.1.23:8000/data. (The first time,
   Windows asks whether to let Python through its firewall: allow it on
   private networks.)
2. In Sensor Logger: Settings > Data Streaming > HTTP Push, enter that URL.
   Opening it in the phone's browser tells you whether the phone gets
   through.
3. Enable the accelerometer, gyroscope and orientation, and start recording.

The plots trail the phone by the push period (a second, by default). The
status bar says what rate each sensor really arrives at: to go faster, set
the app's sampling rate to its maximum, then run this with --rate at what
it measured.
"""

import argparse
import math

from PyQt6 import QtCore as qtc

from oscilab import Oscilab
from oscilab.sensor_logger import SensorLoggerProducer, attach_sensor_logger, push_url


def main() -> int:
    parser = argparse.ArgumentParser(description="Plot Sensor Logger's HTTP Push live")
    parser.add_argument("--rate", type=float, default=100.0, help="the sampling rate set in the app, Hz (100)")
    parser.add_argument("--port", type=int, default=8000, help="the port to listen on (8000)")
    args = parser.parse_args()

    scope = Oscilab()
    attach_sensor_logger(scope, ["accelerometer", "gyroscope", "orientation"], port=args.port, fs=args.rate)

    for title, sensor in (("Acceleration", "accelerometer"), ("Rotation rate", "gyroscope")):
        scope.add_timeseries_plot(title)
        for axis in "xyz":
            scope.link(plot=title, label=f"{sensor} {axis}")

    # Tilt the phone: the dot follows, over the last three seconds
    tilt = scope.add_xy_plot("Tilt", "orientation roll", "orientation pitch", trail=3.0)
    tilt.set_x_range(False, -math.pi, math.pi)  # Roll goes all the way round, pitch only up to vertical
    tilt.set_y_range(False, -math.pi / 2, math.pi / 2)

    # Narrow while the phone lies still, wide while it's shaken
    scope.add_histogram("accelerometer x", bins=60)

    url = push_url(args.port)
    scope.setWindowTitle(f"Oscilab — Sensor Logger: push to {url}")
    print(f"In Sensor Logger's HTTP Push, enter: {url}")

    # Once each sensor has sent a couple of seconds, say what rate it really arrives at
    producers = {
        p.sensor: p for p in dict.fromkeys(scope.workspace.producers.values()) if isinstance(p, SensorLoggerProducer)
    }
    reported: set[str] = set()

    def report_rates() -> None:
        for sensor, producer in producers.items():
            if producer.measured_rate is not None and sensor not in reported:
                reported.add(sensor)
                level = "info" if abs(producer.measured_rate - args.rate) <= 0.2 * args.rate else "warning"
                scope.log(
                    f"{sensor}: about {producer.measured_rate:.0f} Hz arriving (--rate {args.rate:g})", level=level
                )

    rates = qtc.QTimer(scope)
    rates.timeout.connect(report_rates)
    rates.start(1000)
    return scope.run()


if __name__ == "__main__":
    raise SystemExit(main())
