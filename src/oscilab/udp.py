r"""
Samples over the network, as UDP packets: from a microcontroller on Wi-Fi
(an ESP32, a Pico W), a simulation, a test rig, another program.

    from oscilab import UdpProducer

    udp = UdpProducer(port=9000, channels=("x", "y", "z"), fs=100)
    scope.attach_producer(udp, ["x", "y", "z"])

The sender needs no connection and no library: it sends packets to this
PC's port whenever it has samples, whether Oscilab is running or not, and
nothing waits on a packet that's lost. Each packet holds one or more
samples, in one of two formats:

- text (the default): a line per sample, its values separated by commas —
  `"1.23,4.56,7.89\n"`. Easy to send from anything; a line that doesn't
  parse is skipped.
- binary, with `binary=` a `struct` format for one sample: `"<3f"` is three
  little-endian 32-bit floats. Compact, for fast streams: a packet holds as
  many whole samples as fit.

Time: by default the samples are numbered, the n-th at n / `fs` — the
sender samples at `fs`, as a serial device does. With `time_column=True`,
every sample's first value is its time in seconds instead, from the
sender's own clock: exact for irregular or bursty senders.

Sending one from Python:

    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(b"1.23,4.56,7.89\n", ("192.168.1.23", 9000))

The first time, Windows asks whether to let Python through its firewall:
allow it on private networks.
"""

import asyncio
import contextlib
import struct
from collections.abc import Sequence

import numpy as np
from PyQt6 import QtCore as qtc

from oscilab.producers import AsyncDataProducer

_FLUSH_S = 0.02  # Samples are passed on in chunks this often, not a signal per packet


class _Receiver(asyncio.DatagramProtocol):
    def __init__(self, producer: "UdpProducer") -> None:
        self._producer = producer

    def datagram_received(self, data: bytes, addr: tuple[str | int, ...]) -> None:  # noqa: ARG002 — any sender
        self._producer.take(data)


class UdpProducer(AsyncDataProducer):
    """Samples from UDP packets — see the module docstring."""

    def __init__(
        self,
        port: int = 9000,
        channels: Sequence[str] = ("value",),
        fs: float = 100.0,
        binary: str | None = None,
        time_column: bool = False,
        host: str = "0.0.0.0",  # noqa: S104 — every interface: senders reach it over the network
        parent: qtc.QObject | None = None,
    ) -> None:
        if not channels:
            raise ValueError("A UDP producer needs at least one channel")
        fields = len(channels) + int(time_column)
        if binary is not None:
            try:
                sample = struct.Struct(binary)
            except struct.error as e:
                raise ValueError(f"binary={binary!r} isn't a struct format: {e}") from e
            if len(sample.unpack(bytes(sample.size))) != fields:
                raise ValueError(
                    f"binary={binary!r} has {len(sample.unpack(bytes(sample.size)))} value(s) per sample, "
                    f"but {len(channels)} channel(s){' and a time' if time_column else ''} need {fields}"
                )
        super().__init__(fs=fs, parent=parent)
        self.port = port
        self.host = host
        self.channels = tuple(channels)
        self.binary = binary
        self.time_column = time_column
        self.skipped = 0  # Lines or bytes that weren't a whole sample
        self._fields = fields
        self._struct = struct.Struct(binary) if binary is not None else None
        self._pending: list[tuple[float, ...]] = []  # Samples received, not yet passed on
        self._samples = 0  # Numbered so far, for time without a time column

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _Receiver(self), local_addr=(self.host, self.port)
            )
        except OSError as e:
            raise RuntimeError(f"Can't listen on UDP port {self.port}: {e}") from e
        try:
            while self._running:
                await asyncio.sleep(_FLUSH_S)
                self._flush()
        finally:
            transport.close()
            with contextlib.suppress(Exception):  # Whatever came in last still goes out
                self._flush()

    def take(self, packet: bytes) -> None:
        """Parse one packet's samples (runs on this producer's thread, as they arrive)."""
        if self._struct is not None:
            whole = len(packet) - len(packet) % self._struct.size
            self.skipped += len(packet) - whole
            self._pending.extend(self._struct.iter_unpack(packet[:whole]))
            return
        for line in packet.decode("utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                values = tuple(float(v) for v in line.split(","))
            except ValueError:
                self.skipped += 1
                continue
            if len(values) == self._fields:
                self._pending.append(values)
            else:
                self.skipped += 1

    def _flush(self) -> None:
        if not self._pending:
            return
        samples = np.array(self._pending, dtype=float)
        self._pending = []
        if self.time_column:
            t, x = samples[:, 0], samples[:, 1:].T
        else:
            n = np.arange(self._samples, self._samples + len(samples))
            t, x = n / self.fs, samples.T
        self._samples += len(samples)
        self.emit_data(t, x)
