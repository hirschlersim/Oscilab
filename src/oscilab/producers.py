"""
Background data producers.

A `DataProducer` runs on its own `QThread` and emits new `(t, x)` chunks via
the `data_ready` signal: `t` holds the chunk's timestamps, shape `(n,)`, and
`x` one row of samples per channel, shape `(len(channels), n)` — time on the
last axis, like numpy/scipy's signal functions. A single-channel producer
may return a plain 1-D `x` instead. APIs that hand you one row per *sample*,
shape `(n, channels)`, just need a `.T`.

`oscilab.workspace.Workspace.attach_producer` registers one signal per
channel, at the producer's `fs`, and splits each chunk across them.
Producers never touch Qt widgets or models directly — only the queued
signal/slot connection crosses the thread boundary, so data always lands
on the GUI thread.

Two concrete bases to subclass, depending on what your hardware API looks
like — both run on their own `QThread`, only what happens *inside* that
thread differs:

- `SyncDataProducer` — for blocking/synchronous APIs (most serial
  libraries, vendor SDKs that block on a read call). See `SerialProducer`.
- `AsyncDataProducer` — for `asyncio`-based APIs (async serial/network
  clients). See `AsyncSyntheticProducer`.

What every producer gets from the base class:

- An exception inside the producer's thread stops only that producer, never
  the app. It's reported through the `error` signal; the workspace shows it
  in the status bar and on the producer's signal rows.
- `on_start()` / `on_stop()` run on the producer's own thread whenever it
  starts or stops — including at app exit, at the end of its data, on an
  error, and on a restart — to acquire and release hardware.
- A producer that stopped can be started again (`Workspace.restart_producer`,
  or the Restart button on its row after an error). The built-in producers
  resume where they left off; time keeps running forward.
- Saving in a session (`oscilab.session`): the arguments a producer was
  constructed with are recorded automatically, so `config()` can rebuild
  it — as long as they're JSON-friendly (numbers, strings, lists, paths).
- An entry in the window's Producers menu, with Start and Stop — plus its
  own functions (e.g. "Reconnect", "Zero"): methods marked with `@button` /
  `@toggle` (from `oscilab.toolbar`), the same decorators as toolbar
  controls; with `toolbar=True` one goes in the toolbar too. They're queued
  and run on the producer's own thread between reads, so a method can safely
  touch the same connection the producer is reading from. An exception in
  one stops the producer like any other.
"""

import asyncio
import contextlib
import csv
import functools
import importlib
import inspect
import queue
import time
from abc import ABCMeta, abstractmethod
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from loguru import logger
from PyQt6 import QtCore as qtc
from PyQt6 import sip

from oscilab.toolbar import button


class _ProducerMeta(sip.wrappertype, ABCMeta):
    """
    Combines PyQt's own metaclass with `ABCMeta`.

    Without this, `@abstractmethod` on a `QThread` subclass is purely
    decorative — Python only enforces abstract methods on classes whose
    metaclass actually derives from `ABCMeta`, and `QThread`'s metaclass
    (`sip.wrappertype`) doesn't. With it, forgetting to implement
    `_generate`/`_run` fails at instantiation time instead of silently
    working until the method happens to get called.
    """


def _recording_init(init: Callable[..., None]) -> Callable[..., None]:
    """
    Wrap a producer class's `__init__` to record the arguments it was
    called with — only for the outermost call, i.e. the user's, not a
    subclass's `super().__init__(...)` — as `self._init_args`.
    """
    signature = inspect.signature(init)

    @functools.wraps(init)
    def wrapper(self: "DataProducer", *args: Any, **kwargs: Any) -> None:
        if "_init_args" not in self.__dict__:
            bound = signature.bind(self, *args, **kwargs)
            recorded: dict[str, Any] = {}
            for name, value in bound.arguments.items():
                kind = signature.parameters[name].kind
                if name in ("self", "parent"):
                    continue
                if kind is inspect.Parameter.VAR_KEYWORD:
                    recorded.update(value)
                elif kind is inspect.Parameter.VAR_POSITIONAL:
                    recorded[f"*{name}"] = list(value)  # Can't be rebuilt by keyword; config() refuses it
                else:
                    recorded[name] = value
            self.__dict__["_init_args"] = recorded
        init(self, *args, **kwargs)

    return wrapper


def resolve_producer(type_path: str) -> type["DataProducer"]:
    """
    The producer class for a `"module:QualifiedName"` path, as
    `DataProducer.type_path()` writes it (e.g.
    `"oscilab.producers:SyntheticProducer"`). Imports the module if needed.
    """
    module_name, _, qualname = type_path.partition(":")
    if not module_name or not qualname:
        raise ValueError(f"Producer type {type_path!r} isn't a 'module:ClassName' path")
    try:
        obj = importlib.import_module(module_name)
        for part in qualname.split("."):
            obj = getattr(obj, part)
    except (ImportError, AttributeError) as e:
        raise ValueError(f"Can't find producer type {type_path!r}: {e}") from e
    if not (isinstance(obj, type) and issubclass(obj, DataProducer)):
        raise TypeError(f"{type_path!r} isn't a DataProducer subclass")
    return obj


class DataProducer(qtc.QThread, metaclass=_ProducerMeta):
    """
    Base class for background data producers. Subclass `SyncDataProducer`
    or `AsyncDataProducer` instead of this directly — see the module
    docstring for which one fits your hardware API.
    """

    data_ready = qtc.pyqtSignal(object, object)  # (t: (n,), x: (len(channels), n))
    error = qtc.pyqtSignal(str)  # "ExceptionType: message" — the producer has stopped

    # One name per channel, in the row order of `x`. Override as a class
    # attribute, or set `self.channels` in `__init__` when the count depends
    # on constructor arguments.
    channels: tuple[str, ...] = ("value",)
    # Optionally, the unit of each channel, in the same order (e.g. ("V", "A"));
    # empty for none. `attach_producer(..., units=...)` can override it.
    units: tuple[str, ...] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "__init__" in cls.__dict__:
            cls.__init__ = _recording_init(cls.__init__)  # type: ignore[method-assign]  # Wraps it, same signature

    @classmethod
    def type_path(cls) -> str:
        """Where to find this class again: `"module:QualifiedName"` (see `resolve_producer`)."""
        return f"{cls.__module__}:{cls.__qualname__}"

    def config(self) -> dict[str, Any]:
        """
        The keyword arguments that rebuild this producer:
        `type(p)(**p.config())`. Recorded from its constructor call —
        only what was passed explicitly, so defaults can still change.
        """
        args = dict(self.__dict__.get("_init_args", {}))
        starred = [name for name in args if name.startswith("*")]
        if starred:
            raise ValueError(f"{type(self).__name__} takes {starred[0]}: can't be rebuilt from keyword arguments")
        return args

    def __init__(self, fs: float, parent: qtc.QObject | None = None) -> None:
        super().__init__(parent)
        if fs <= 0:
            raise ValueError(f"fs must be positive, got {fs}")
        self.fs = fs  # Sampling rate in Hz; every signal fed by this producer uses it
        self._running = False
        self._pending_actions: queue.SimpleQueue[Callable[[], object]] = queue.SimpleQueue()

    def start(self, *args: Any, **kwargs: Any) -> None:
        # Set here, not in run(): a stop() that lands before the new thread
        # gets scheduled must still win, instead of run() flipping it back.
        self._running = True
        self._pending_actions = queue.SimpleQueue()  # A restart doesn't replay old clicks
        super().start(*args, **kwargs)

    def run(self) -> None:
        """QThread entry point. Subclass `SyncDataProducer`/`AsyncDataProducer` rather than overriding this."""
        try:
            self.on_start()
            self._produce()
        except Exception as e:  # noqa: BLE001 — whatever went wrong, only this producer stops
            self.__report(e)
        finally:
            self._running = False
            try:
                self.on_stop()
            except Exception as e:  # noqa: BLE001 — same for a failing cleanup
                self.__report(e)

    def __report(self, e: Exception) -> None:
        logger.opt(exception=e).error(f"{type(self).__name__} stopped")
        self.error.emit(f"{type(e).__name__}: {e}")

    def stop(self) -> None:
        """Ask the producer to stop, and wait until it has (including `on_stop()`)."""
        self._running = False
        self.wait()

    @property
    def running(self) -> bool:
        """True from `start()` until it stops — or is asked to (its thread may still be finishing)."""
        return self._running

    def on_start(self) -> None:
        """
        Override to (re)acquire resources or reset per-run state. Runs on the
        producer's own thread before it starts producing — on every start,
        restarts included.
        """

    def on_stop(self) -> None:
        """
        Override to release resources (close a port, a socket, ...). Runs on
        the producer's own thread after it stops, for whatever reason.
        """

    def queue_action(self, action: Callable[[], object]) -> bool:
        """
        Run `action` on this producer's own thread, between reads — how its
        `@button` / `@toggle` methods get called. Returns False, without
        running it, if the producer isn't running.
        """
        if not self._running:
            return False
        self._pending_actions.put(action)
        return True

    def _run_pending_actions(self) -> None:
        while True:
            try:
                action = self._pending_actions.get_nowait()
            except queue.Empty:
                return
            action()

    def emit_data(self, t: npt.ArrayLike, x: npt.ArrayLike) -> None:
        """
        Send one chunk of samples: `t` shape `(n,)`, `x` shape
        `(len(self.channels), n)` — or 1-D for a single channel. Empty chunks
        are skipped.
        """
        t = np.asarray(t, dtype=float)
        if t.size == 0:
            return
        x = np.asarray(x, dtype=float)
        if x.ndim == 1:
            x = x[np.newaxis, :]
        expected = (len(self.channels), t.size)
        if x.shape != expected:
            raise ValueError(
                f"{type(self).__name__} emitted x with shape {x.shape}, expected {expected}: "
                f"one row per channel {self.channels}, one column per timestamp. "
                "Data with one row per sample needs a .T"
            )
        self.data_ready.emit(t, x)

    @abstractmethod
    def _produce(self) -> None:
        """Produce data until `self._running` goes False (see the Sync/Async bases)."""
        raise NotImplementedError


class SyncDataProducer(DataProducer):
    """
    For blocking/synchronous hardware APIs.

    Subclasses implement `_generate(elapsed)`, returning whatever `(t, x)`
    samples are due given `elapsed` real seconds since the producer started
    — counted from the latest start, so it restarts at 0 on a restart (see
    the module docstring for the shape of `x`). Setting
    `self._running = False` from inside `_generate` ends the producer, e.g.
    at the end of a file.

    Pacing is driven by a wall-clock target (`next_tick`), not a fixed
    `msleep(interval_ms)` per iteration: OS sleep calls routinely overshoot
    their requested duration (Windows' default timer granularity is ~15ms,
    so a naive 10ms-interval producer only achieves ~60% of real-time
    speed). Tracking elapsed wall time and asking the subclass "how much is
    due by now" self-corrects for that overshoot instead of accumulating it.
    """

    def __init__(self, fs: float, interval_s: float = 0.02, parent: qtc.QObject | None = None) -> None:
        super().__init__(fs=fs, parent=parent)
        self.interval_s = interval_s

    def _produce(self) -> None:
        start = time.perf_counter()
        next_tick = start

        while self._running:
            self._run_pending_actions()
            elapsed = time.perf_counter() - start
            t, x = self._generate(elapsed)
            self.emit_data(t, x)

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

    `_run()` runs as a task next to a small supervisor that runs queued
    actions and, on `stop()`, cancels `_run()` — so stopping never has to
    wait for a read that may never return.
    """

    ACTION_POLL_S = 0.02  # How often queued actions and stop requests are checked

    def _produce(self) -> None:
        asyncio.run(self.__supervise())

    async def __supervise(self) -> None:
        run_task = asyncio.create_task(self._run())
        try:
            while not run_task.done():
                if not self._running:
                    run_task.cancel()
                self._run_pending_actions()
                await asyncio.wait({run_task}, timeout=self.ACTION_POLL_S)
        finally:
            if not run_task.done():  # An action raised: take `_run()` down with it
                run_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await run_task

        if not run_task.cancelled():
            run_task.result()  # Re-raise whatever ended `_run()` with an error

    @abstractmethod
    async def _run(self) -> None:
        """
        Await real I/O and call `self.emit_data(t, x)` as samples arrive,
        e.g. in a `while self._running:` loop. `stop()` also cancels this
        coroutine if it's still awaiting.
        """
        raise NotImplementedError


class SyntheticProducer(SyncDataProducer):
    """
    Noisy sine waves — useful for demos and testing. Pass several
    frequencies as `freq` for a multi-channel producer, one sine per channel.
    """

    def __init__(
        self,
        fs: float,
        freq: float | Sequence[float] | np.ndarray = 5.0,
        amplitude: float = 1.0,
        noise: float = 0.05,
        chunk_size: int = 10,
        parent: qtc.QObject | None = None,
    ) -> None:
        super().__init__(fs=fs, interval_s=chunk_size / fs, parent=parent)
        self.freqs = np.atleast_1d(np.asarray(freq, dtype=float))
        self.channels = tuple(f"{f:g} Hz" for f in self.freqs)
        self.amplitude = amplitude
        self.noise = noise
        self._rng = np.random.default_rng()
        self._samples_emitted = 0
        self._run_origin = 0  # Samples emitted before the current run started

    def on_start(self) -> None:
        # `elapsed` restarts at 0 on every start; count from here so a
        # restarted producer continues right away instead of going quiet
        # until the new run's clock catches up with the old sample count.
        self._run_origin = self._samples_emitted

    def _generate(self, elapsed: float) -> tuple[np.ndarray, np.ndarray]:
        samples_due = self._run_origin + int(elapsed * self.fs) - self._samples_emitted
        if samples_due <= 0:
            return np.array([]), np.array([])

        n = np.arange(self._samples_emitted, self._samples_emitted + samples_due)
        self._samples_emitted += samples_due

        t = n / self.fs
        x = self.amplitude * np.sin(2 * np.pi * self.freqs[:, np.newaxis] * t)  # (channels, n)
        if self.noise:
            x = x + self._rng.normal(0, self.noise, size=x.shape)
        return t, x


class FileReplayProducer(SyncDataProducer):
    """
    Replays a recorded CSV file as if it were live data, at 1x speed.

    The first column is time, every further column one channel, named by the
    header row: `time,value` (e.g. an Oscilab CSV export) is one channel,
    `time,ax,ay,az` three. With `loop=True`, timestamps keep increasing from
    one pass to the next instead of jumping back to the start.
    """

    def __init__(
        self, path: str | Path, chunk_size: int = 10, loop: bool = False, parent: qtc.QObject | None = None
    ) -> None:
        path = Path(path)
        header, time_col, data = self.__load(path)
        dt = float(np.mean(np.diff(time_col))) if len(time_col) > 1 else 0.02

        super().__init__(fs=1.0 / dt, interval_s=max(dt * chunk_size, 0.001), parent=parent)
        self.path = path
        self.loop = loop
        self.channels = tuple(header[1:])
        self._time = time_col
        self._data = data  # (channels, n)
        # One pass spans first sample to one sample period past the last, so
        # the next pass continues exactly where this one ended.
        self._pass_duration = float(time_col[-1] - time_col[0]) + dt if len(time_col) else 0.0
        self._pass = 0
        self._cursor = 0
        self._loop_offset = 0.0  # elapsed-time origin of the current pass

    def on_start(self) -> None:
        # `elapsed` restarts at 0 on every start: line the replay clock up
        # with the next unsent sample, so a restart resumes where it stopped.
        if len(self._time):
            resume_t = self._time[min(self._cursor, len(self._time) - 1)]
            self._loop_offset = float(self._time[0] - resume_t)

    @staticmethod
    def __load(path: Path) -> tuple[list[str], np.ndarray, np.ndarray]:
        # UTF-8, as Oscilab writes it; "-sig" also drops the mark Excel puts in front
        with path.open(newline="", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header or len(header) < 2:
                raise ValueError(f"{path}: expected a header row like 'time,value'")
            # Skip blank or malformed rows rather than failing the whole file
            rows = [numbers for row in reader if (numbers := _numbers(row, len(header))) is not None]

        values = np.array(rows, dtype=float).reshape(-1, len(header))
        steps = np.diff(values[:, 0])
        if steps.size and (np.any(steps < 0) or not np.mean(steps) > 0):
            raise ValueError(f"{path}: the times in the first column must increase from row to row")
        return header, values[:, 0], np.ascontiguousarray(values[:, 1:].T)

    def _generate(self, elapsed: float) -> tuple[np.ndarray, np.ndarray]:
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
                    self._pass += 1
                    self._loop_offset += self._pass_duration
                else:
                    self._running = False  # End of file, not looping: stop.
            return np.array([]), np.array([])

        t_chunk = self._time[self._cursor : end] + self._pass * self._pass_duration
        x_chunk = self._data[:, self._cursor : end]
        self._cursor = end
        return t_chunk, x_chunk


def _numbers(row: list[str], width: int) -> list[float] | None:
    """A CSV row's values, or None for one that isn't `width` numbers: blank, cut short, a stray note."""
    if len(row) != width:
        return None
    try:
        return [float(value) for value in row]
    except ValueError:
        return None


class SerialProducer(SyncDataProducer):
    r"""
    Reads real hardware over a serial port, one line per sample: `"1.23\n"`,
    or comma-separated values for several channels (`"1.2,3.4,5.6\n"` with
    `channels=("x", "y", "z")`). Needs `pyserial` (`pip install pyserial`) —
    not a hard dependency of Oscilab itself, since most users won't need
    real hardware acquisition.

    This is a template more than a one-size-fits-all class: real hardware
    framing (binary packets, checksums, ...) will need a different
    `_generate()`, but the pacing/threading/`on_start()`/`on_stop()` shape
    below is reusable as-is. Also demonstrates a Producers-menu function
    ("Reconnect").
    """

    def __init__(
        self,
        port: str,
        fs: float,
        baudrate: int = 115200,
        chunk_size: int = 10,
        channels: Sequence[str] = ("value",),
        parent: qtc.QObject | None = None,
    ) -> None:
        try:
            import serial  # noqa: PLC0415 — optional dependency, only needed by this producer
        except ImportError as e:
            raise ImportError("SerialProducer needs pyserial: pip install pyserial") from e

        super().__init__(fs=fs, interval_s=chunk_size / fs, parent=parent)
        self.port = port
        self.baudrate = baudrate
        self.chunk_size = chunk_size
        self.channels = tuple(channels)
        self._connection = serial.Serial(port, baudrate=baudrate, timeout=1.0)
        self._sample_index = 0

    def _generate(self, _elapsed: float) -> tuple[np.ndarray, np.ndarray]:
        # Blocking reads are fine here — this runs on this producer's own
        # QThread, never the GUI thread. Real hardware may need a tighter
        # per-line timeout than the 1.0s default so a quiet channel doesn't
        # stall the whole pacing loop.
        rows = []
        for _ in range(self.chunk_size):
            if not self._running:
                break  # Don't make stop() wait out the rest of the chunk
            line = self._connection.readline()
            if not line:
                break
            try:
                values = [float(v) for v in line.split(b",")]
            except ValueError:
                continue  # Malformed line — skip it rather than stall
            if len(values) == len(self.channels):
                rows.append(values)

        if not rows:
            return np.array([]), np.array([])

        n = np.arange(self._sample_index, self._sample_index + len(rows))
        self._sample_index += len(rows)
        return n / self.fs, np.asarray(rows).T

    def on_start(self) -> None:
        if not self._connection.is_open:  # Closed by on_stop() when a previous run ended
            self._connection.open()

    def on_stop(self) -> None:
        self._connection.close()

    @button("Reconnect", tooltip="Close and reopen the serial port")
    def reconnect(self) -> None:
        """Close and reopen the serial port."""
        # Runs on this producer's own thread (see `DataProducer.queue_action`),
        # so it can't pull the port out from under an in-progress readline().
        self._connection.close()
        self._connection.open()


class AsyncSyntheticProducer(AsyncDataProducer):
    """
    The same sine wave as `SyntheticProducer`, but written against
    `AsyncDataProducer` instead of `SyncDataProducer` — a template for
    wrapping an asyncio-based hardware client (`aioserial`, an async
    network client, ...) rather than something you'd use for real demo
    data. Also demonstrates a producer function ("Reset Phase"), in its
    Producers-menu entry and, with `toolbar=True`, in the toolbar.
    """

    def __init__(
        self,
        fs: float,
        freq: float = 5.0,
        amplitude: float = 1.0,
        chunk_size: int = 10,
        parent: qtc.QObject | None = None,
    ) -> None:
        super().__init__(fs=fs, parent=parent)
        self.freq = freq
        self.amplitude = amplitude
        self.chunk_size = chunk_size
        self._sample_index = 0
        self._phase_origin = 0.0  # Time at which the sine's phase is zero

    async def _run(self) -> None:
        # Emit whatever's due by the wall clock rather than a fixed chunk per
        # sleep: sleeps overshoot (see SyncDataProducer), and a fixed chunk
        # would let this signal fall further and further behind real time.
        start = time.perf_counter()
        origin = self._sample_index  # Nonzero after a restart: continue from there
        while self._running:
            due = origin + int((time.perf_counter() - start) * self.fs) - self._sample_index
            if due > 0:
                n = np.arange(self._sample_index, self._sample_index + due)
                self._sample_index += due
                t = n / self.fs
                self.emit_data(t, self.amplitude * np.sin(2 * np.pi * self.freq * (t - self._phase_origin)))
            await asyncio.sleep(self.chunk_size / self.fs)  # Real code: `await client.read_chunk()` etc.

    @button("Reset Phase", tooltip="Restart the sine at phase 0", toolbar=True)
    def reset_phase(self) -> None:
        """Restart the sine at phase 0 from the next sample; time itself keeps running."""
        # Illustrative custom action — a real one might re-sync a device's
        # onboard clock, send a calibration command, etc.
        self._phase_origin = self._sample_index / self.fs
