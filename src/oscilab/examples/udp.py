"""
Samples over UDP, live: `uv run oscilab-udp`.

Listens on port 9000 for lines of three comma-separated values —
"1.23,4.56,7.89" — and plots them over time, and x against y. Send them from
anything on your network: an ESP32 or Pico W on Wi-Fi, a simulation, a
script; the window's title shows the address to send to. (The first time,
Windows asks whether to let Python through its firewall: allow it on
private networks.)

    uv run oscilab-udp --demo    # sends itself a Lissajous figure, to try it without a sender
    uv run oscilab-udp --rate 500 --port 9001
"""

import argparse
import math
import socket
import threading
import time
from collections.abc import Sequence

from oscilab import Oscilab, UdpProducer
from oscilab.network import local_address


def send_demo(port: int, rate: float) -> None:
    """Stand in for a sender: x, y a 3:2 Lissajous figure, z noise, ten samples a packet."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        n = 0
        start = time.perf_counter()
        while True:
            time.sleep(10 / rate)
            lines = []
            while n < (time.perf_counter() - start) * rate:
                t = n / rate
                lines.append(f"{math.sin(3 * t):.4f},{math.sin(2 * t + 0.5):.4f},{0.1 * math.sin(97 * t) ** 3:.4f}")
                n += 1
            if lines:
                sender.sendto(("\n".join(lines) + "\n").encode(), ("127.0.0.1", port))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plot comma-separated samples arriving over UDP")
    parser.add_argument("--port", type=int, default=9000, help="the UDP port to listen on (9000)")
    parser.add_argument("--rate", type=float, default=100.0, help="the rate the sender samples at, Hz (100)")
    parser.add_argument("--demo", action="store_true", help="send ourselves a Lissajous figure")
    args = parser.parse_args(argv)

    scope = Oscilab()
    scope.attach_producer(UdpProducer(port=args.port, channels=("x", "y", "z"), fs=args.rate), ["x", "y", "z"])
    scope.add_timeseries_plot("Samples")
    for label in ("x", "y", "z"):
        scope.link(plot="Samples", label=label)
    xy = scope.add_xy_plot("x against y", "x", "y", trail=5.0)
    xy.set_equal_axes(True)

    scope.setWindowTitle(f"Oscilab — send UDP to {local_address()}:{args.port}")
    if args.demo:
        threading.Thread(target=send_demo, args=(args.port, args.rate), daemon=True).start()
    return scope.run()


if __name__ == "__main__":
    raise SystemExit(main())
