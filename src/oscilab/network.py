"""What the network producers (`oscilab.udp`, `oscilab.sensor_logger`) share: where senders reach this PC."""

import socket


def local_address() -> str:
    """The PC's address on its local network, e.g. "192.168.1.23" — "127.0.0.1" without one."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(("192.0.2.1", 80))  # Picks the interface the default route uses; sends nothing
            return probe.getsockname()[0]
        except OSError:  # No network: only this PC can reach it
            return "127.0.0.1"
