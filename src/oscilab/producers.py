"""
Background data producers.

A `DataProducer` runs on its own `QThread` and periodically emits new
`(t, x)` chunks via the `data_ready` signal. `oscilab.workspace.Workspace`
connects that signal to the target `SignalModel.add_data`, so producers
never touch Qt widgets or models directly — only the queued signal/slot
connection crosses the thread boundary, which Qt handles safely.

Two concrete bases to subclass, depending on what your hardware API looks
like — both still run on their own `QThread`, only what happens *inside*
that thread differs:

- `SyncDataProducer` — for blocking/synchronous APIs (most serial
  libraries, vendor SDKs that block on a read call). See `SerialProducer`.
- `AsyncDataProducer` — for `asyncio`-based APIs (async serial/network
  clients). See `AsyncSyntheticProducer`.

A producer can also expose custom buttons on its signal's row — e.g. a
"Reconnect" or "Zero" action — by overriding `actions()`.
"""

import asyncio
import csv
import time
from abc import ABCMeta, abstractmethod
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PyQt6 import QtCore as qtc


class _ProducerMeta(type(qtc.QThread), ABCMeta):
    """
    Combines PyQt's own metaclass with `ABCMeta`.

    Without this, `@abstractmethod` on a `QThread` subclass is purely
    decorative — Python only enforces abstract methods on classes whose
    metaclass actually derives from `ABCMeta`, and `QThread`'s metaclass
    (`sip.wrappertype`) doesn't. With it, forgetting to implement
    `_generate`/`_run` fails at instantiation time instead of silently
    working until the method happens to get called.
    """


class DataProducer(qtc.QThread, metaclass=_ProducerMeta):
    """
    Base class for background data producers. Subclass `SyncDataProducer`
    or `AsyncDataProducer` instead of this directly — see the module
    docstring for which one fits your hardware API.
    """

    data_ready = qtc.pyqtSignal(object, object)  # (t: np.ndarray, x: np.ndarray)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False

    def stop(self):
        self._running = False
        self.wait()

    def actions(self) -> dict[str, Callable[[], None]]:
        """
        Override to expose custom buttons on this producer's signal row,
        e.g. `{"Reconnect": self.reconnect, "Zero": self.zero_calibration}`.
        Added once, when the producer is attached via `Workspace.attach_producer`.
        """
        return {}


class SyncDataProducer(DataProducer):
    """
    For blocking/synchronous hardware APIs.

    Subclasses implement `_generate(elapsed)`, returning whatever `(t, x)`
    samples are due given `elapsed` real seconds since the producer started.

    Pacing is driven by a wall-clock target (`next_tick`), not a fixed
    `msleep(interval_ms)` per iteration: OS sleep calls routinely overshoot
    their requested duration (Windows' default timer granularity is ~15ms,
    so a naive 10ms-interval producer only achieves ~60% of real-time
    speed). Tracking elapsed wall time and asking the subclass "how much is
    due by now" self-corrects for that overshoot instead of accumulating it.
    """

    def __init__(self, interval_s: float = 0.02, parent=None):
        super().__init__(parent)
        self.interval_s = interval_s

    def run(self):
        self._running = True
        start = time.perf_counter()
        next_tick = start

        while self._running:
            elapsed = time.perf_counter() - start
            t, x = self._generate(elapsed)
            if len(t):
                self.data_ready.emit(t, x)

            next_tick += self.interval_s
            delay = next_tick - time.perf_counter()
            if delay > 0:
                self.msleep(max(1, int(delay * 1000)))
            else:
                next_tick = time.perf_counter()  # Fell behind: resync instead of free-running

    @abstractmethod
    def _generate(self, elapsed: float) -> tuple[np.ndarray, np.ndarray]:
        """
        Return whatever new (time, value) samples are due by `elapsed`
        real seconds since the producer started.
        """
        raise NotImplementedError


class AsyncDataProducer(DataProducer):
    """
    For `asyncio`-based hardware APIs (async serial/network clients, ...).

    Subclasses implement `async def _run(self)`, awaiting real I/O directly
    and calling `self.emit_data(t, x)` whenever new samples are ready — no
    poll-on-a-timer shape forced onto code that doesn't naturally fit one.
    Runs its own asyncio event loop on this producer's own `QThread`, so it
    doesn't need (or interfere with) Qt's own event loop.
    """

    def run(self):
        self._running = True
        asyncio.run(self._run())

    def emit_data(self, t, x):
        """Call from inside `_run()` whenever a new chunk of samples is ready."""
        if len(t):
            self.data_ready.emit(t, x)

    @abstractmethod
    async def _run(self):
        """
        Await real I/O and call `self.emit_data(t, x)` as samples arrive.
        Check `self._running` periodically (e.g. in the loop condition) so
        `stop()` can actually end the loop promptly.
        """
        raise NotImplementedError


class SyntheticProducer(SyncDataProducer):
    """Generates a noisy sine wave — useful for demos and testing."""

    def __init__(
        self,
        fs: float,
        freq: float = 5.0,
        amplitude: float = 1.0,
        noise: float = 0.05,
        chunk_size: int = 10,
        parent=None,
    ):
        super().__init__(interval_s=chunk_size / fs, parent=parent)
        self.fs = fs
        self.freq = freq
        self.amplitude = amplitude
        self.noise = noise
        self._rng = np.random.default_rng()
        self._samples_emitted = 0

    def _generate(self, elapsed):
        samples_due = int(elapsed * self.fs) - self._samples_emitted
        if samples_due <= 0:
            return np.array([]), np.array([])

        n = np.arange(self._samples_emitted, self._samples_emitted + samples_due)
        self._samples_emitted += samples_due

        t = n / self.fs
        x = self.amplitude * np.sin(2 * np.pi * self.freq * t)
        if self.noise:
            x = x + self._rng.normal(0, self.noise, size=x.shape)
        return t, x


class FileReplayProducer(SyncDataProducer):
    """Replays a recorded `time,value` CSV file as if it were live data, at 1x speed."""

    def __init__(self, path, chunk_size: int = 10, loop: bool = False, parent=None):
        self.path = Path(path)
        self.loop = loop
        self._time, self._data = self.__load(self.path)
        self._cursor = 0
        self._loop_offset = 0.0  # elapsed-time origin of the current loop pass

        dt = float(np.mean(np.diff(self._time))) if len(self._time) > 1 else 0.02
        super().__init__(interval_s=max(dt * chunk_size, 0.001), parent=parent)

    @staticmethod
    def __load(path: Path) -> tuple[np.ndarray, np.ndarray]:
        t, x = [], []
        with path.open(newline="") as f:
            reader = csv.reader(f)
            next(reader, None)  # header
            for row in reader:
                if len(row) < 2:
                    continue
                t.append(float(row[0]))
                x.append(float(row[1]))
        return np.asarray(t), np.asarray(x)

    def _generate(self, elapsed):
        if len(self._time) == 0:
            self._running = False
            return np.array([]), np.array([])

        # How far into the recording `elapsed` real seconds corresponds to,
        # regardless of how unevenly the thread actually got woken up.
        target_t = self._time[0] + (elapsed - self._loop_offset)
        end = min(int(np.searchsorted(self._time, target_t, side="right")), len(self._time))

        if end <= self._cursor:
            if self._cursor >= len(self._time):
                if self.loop:
                    self._cursor = 0
                    self._loop_offset = elapsed
                else:
                    self._running = False  # End of file, not looping: stop.
            return np.array([]), np.array([])

        t_chunk = self._time[self._cursor : end]
        x_chunk = self._data[self._cursor : end]
        self._cursor = end
        return t_chunk, x_chunk


class SerialProducer(SyncDataProducer):
    r"""
    Reads real hardware over a serial port, one line per sample (e.g.
    `"1.23\n"`). Needs `pyserial` (`pip install pyserial`) — not a hard
    dependency of Oscilab itself, since most users won't need real hardware
    acquisition.

    This is a template more than a one-size-fits-all class: real hardware
    framing (binary packets, multiple channels per line, checksums, ...)
    will need a different `_generate()`, but the pacing/threading/`actions()`
    shape below is reusable as-is. Also demonstrates a custom action button
    ("Reconnect") via `actions()`.
    """

    def __init__(self, port: str, fs: float, baudrate: int = 115200, chunk_size: int = 10, parent=None):
        try:
            import serial  # noqa: PLC0415 — optional dependency, only needed by this producer
        except ImportError as e:
            raise ImportError("SerialProducer needs pyserial: pip install pyserial") from e

        super().__init__(interval_s=chunk_size / fs, parent=parent)
        self._serial_module = serial
        self.port = port
        self.baudrate = baudrate
        self.fs = fs
        self.chunk_size = chunk_size
        self._connection = serial.Serial(port, baudrate=baudrate, timeout=1.0)
        self._sample_index = 0

    def _generate(self, _elapsed):
        # Blocking reads are fine here — this runs on this producer's own
        # QThread, never the GUI thread. Real hardware may need a tighter
        # per-line timeout than the 1.0s default so a quiet channel doesn't
        # stall the whole pacing loop.
        values = []
        for _ in range(self.chunk_size):
            line = self._connection.readline()
            if not line:
                break
            try:
                values.append(float(line))
            except ValueError:
                continue  # Malformed line — skip it rather than stall

        if not values:
            return np.array([]), np.array([])

        n = np.arange(self._sample_index, self._sample_index + len(values))
        self._sample_index += len(values)
        return n / self.fs, np.asarray(values)

    def actions(self):
        return {"Reconnect": self.reconnect}

    def reconnect(self):
        self._connection.close()
        self._connection.open()


class AsyncSyntheticProducer(AsyncDataProducer):
    """
    The same sine wave as `SyntheticProducer`, but written against
    `AsyncDataProducer` instead of `SyncDataProducer` — a template for
    wrapping an asyncio-based hardware client (`aioserial`, an async
    network client, ...) rather than something you'd use for real demo
    data. Also demonstrates a custom action button ("Reset Phase").
    """

    def __init__(self, fs: float, freq: float = 5.0, amplitude: float = 1.0, chunk_size: int = 10, parent=None):
        super().__init__(parent=parent)
        self.fs = fs
        self.freq = freq
        self.amplitude = amplitude
        self.chunk_size = chunk_size
        self._sample_index = 0

    async def _run(self):
        interval_s = self.chunk_size / self.fs
        while self._running:
            n = np.arange(self._sample_index, self._sample_index + self.chunk_size)
            self._sample_index += self.chunk_size
            t = n / self.fs
            x = self.amplitude * np.sin(2 * np.pi * self.freq * t)
            self.emit_data(t, x)
            await asyncio.sleep(interval_s)  # Real code: `await client.read_chunk()` etc.

    def actions(self):
        return {"Reset Phase": self.reset_phase}

    def reset_phase(self):
        # Illustrative custom action — a real one might re-sync a device's
        # onboard clock, send a calibration command, etc.
        self._sample_index = 0
