import threading
from collections import deque
from collections.abc import Callable

import numpy as np
import numpy.typing as npt
from scipy import signal

from oscilab.ring_buffer import RingBuffer
from oscilab.storage import Storage

type RollingBuffer = RingBuffer | deque[float]


class SignalModel:
    """
    Holds the rolling data buffer and DSP state for a single signal.

    Pure data/DSP class — no Qt dependency, so it can be driven from any
    thread (see `oscilab.producers.DataProducer`) and unit-tested in
    isolation.

    `storage` (an `oscilab.storage.Storage` member) picks the rolling-buffer
    backend: `Storage.ring` (default — a pre-allocated numpy circular
    buffer, see `oscilab.ring_buffer.RingBuffer`) or `Storage.deque`
    (`collections.deque(maxlen=...)`). Both expose the same
    `extend`/`clear`/`len`/`[0]`/`[-1]`/`np.asarray()` surface, so the rest of
    this class doesn't need to care which one is in use — `ring` defaults on
    because it measures 12-126x faster at exactly the operation every
    consumer below does every tick; see `oscilab.benchmarks.buffers`.

    `snapshot()` caches that `np.asarray()` conversion for the tick: every
    consumer (a plot's curve, `compute_metrics()`, a `MathSignal`, a
    spectrogram) used to redo its own `np.asarray(self.data)` independently,
    so a signal read by N consumers paid for the conversion N times over —
    call `snapshot()` instead and they share one. Invalidated on any write.

    Writes and `snapshot()` hold a lock, so the time and data buffers stay
    in step for a reader on another thread: a signal fed by hand may be fed
    from any thread while the GUI thread draws it.
    """

    def __init__(
        self, label: str, fs: float, duration: float = 10.0, storage: Storage = Storage.ring, unit: str = ""
    ) -> None:
        if fs <= 0:
            raise ValueError(f"fs must be positive, got {fs}")
        if duration <= 0:
            raise ValueError(f"duration must be positive, got {duration}")
        self.label = label
        self.fs = fs
        self.unit = (
            unit  # E.g. "V", "°C"; "" for none. Change it through `Workspace.set_unit`, which updates the plots.
        )
        self.duration = duration  # How many seconds of history the buffer holds
        self.storage = Storage(storage)  # Accepts a Storage member or its string value
        maxlen = self.__maxlen_for(duration)
        self.data = self.__make_buffer(maxlen)
        self.time = self.__make_buffer(maxlen)
        self.sos: np.ndarray | None = None  # Filter
        self.zi: np.ndarray | None = None
        self.gain: float | None = None  # Scaling
        self.offset: float | None = None
        self._cache: tuple[np.ndarray, np.ndarray] | None = None  # (time_array, data_array), see `snapshot()`
        self._listeners: list[Callable[[np.ndarray, np.ndarray], None]] = []
        self._lock = threading.Lock()  # Keeps `time` and `data` in step, see the class docstring

    @property
    def display_name(self) -> str:
        """The label with its unit, as legends and readouts show it: `"channel-1 (V)"`."""
        return f"{self.label} ({self.unit})" if self.unit else self.label

    def __maxlen_for(self, duration: float) -> int:
        return max(1, round(duration * self.fs))

    def __make_buffer(self, maxlen: int) -> RollingBuffer:
        return RingBuffer(maxlen) if self.storage is Storage.ring else deque(maxlen=maxlen)

    def __rebuilt(self, existing: RollingBuffer, maxlen: int) -> RollingBuffer:
        """New empty buffer of `maxlen`, refilled with `existing`'s current tail."""
        buffer = self.__make_buffer(maxlen)
        values = np.asarray(existing)
        buffer.extend(values[-maxlen:] if len(values) > maxlen else values)
        return buffer

    def set_duration(self, duration: float) -> None:
        """
        Resize the rolling buffer to hold `duration` seconds at this signal's fs.

        The most recent samples that still fit are kept; older ones are
        dropped. Growing the duration doesn't back-fill lost history.
        """
        if duration <= 0:
            raise ValueError(f"duration must be positive, got {duration}")
        maxlen = self.__maxlen_for(duration)
        with self._lock:
            self.duration = duration
            self.time = self.__rebuilt(self.time, maxlen)
            self.data = self.__rebuilt(self.data, maxlen)
            self._cache = None

    def add_data(self, t: npt.ArrayLike, x: npt.ArrayLike) -> None:
        """
        Append samples: `t` their times, `x` their values, in pairs — arrays
        of the same length, or one time and one value. Scaled, then filtered,
        if those are on.
        """
        times = np.atleast_1d(np.asarray(t, dtype=float))
        samples = np.atleast_1d(np.asarray(x, dtype=float))
        if times.ndim != 1 or times.shape != samples.shape:
            raise ValueError(f"'{self.label}': times {times.shape} and values {samples.shape} don't pair up")

        if self.gain is not None and self.offset is not None:
            samples = (samples - self.offset) * self.gain

        with self._lock:
            if self.sos is not None:
                samples = self.__filtered(self.sos, samples)
            self.data.extend(samples)
            self.time.extend(times)
            self._cache = None
        for listener in tuple(self._listeners):  # A copy: one may be removed from another thread meanwhile
            listener(times, samples)

    def __filtered(self, sos: np.ndarray, samples: np.ndarray) -> np.ndarray:
        """
        `samples` low-pass filtered, the filter's state carried over from the
        last chunk. It starts in the steady state for the first sample, as if
        the signal had always been there — not for 1.0, which made the output
        climb from 1 to wherever the signal was. NaN passes through unfiltered:
        in the filter, it would stay in its state, and every sample after it
        would come out NaN.
        """
        finite = np.isfinite(samples)
        if not finite.any():
            return samples
        if self.zi is None:
            self.zi = signal.sosfilt_zi(sos) * samples[finite][0]
        filtered = samples.copy()
        filtered[finite], self.zi = signal.sosfilt(sos=sos, x=samples[finite], zi=self.zi)
        return filtered

    def add_listener(self, listener: Callable[[np.ndarray, np.ndarray], None]) -> None:
        """
        Call `listener(t, x)` with every chunk `add_data` takes in, as stored
        (filtered and scaled) — on the thread that called `add_data`. How a
        recording (`oscilab.recorder`) gets every sample.
        """
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[np.ndarray, np.ndarray], None]) -> None:
        self._listeners.remove(listener)

    def set_data(self, t: npt.ArrayLike, x: npt.ArrayLike) -> None:
        """
        Wholesale-replace the buffer (used by derived/math signals).

        Unlike `add_data`, this does not apply filter/scaling: those assume
        an incrementally-growing stream with persistent filter state, which
        doesn't hold for a value recomputed from scratch every tick.
        """
        with self._lock:
            self.time.clear()
            self.time.extend(np.asarray(t))
            self.data.clear()
            self.data.extend(np.asarray(x))
            self._cache = None

    def snapshot(self) -> tuple[np.ndarray, np.ndarray]:
        """
        (time_array, data_array) for the buffer's current contents,
        computed once and cached until the next write — see class docstring.
        """
        with self._lock:
            if self._cache is None:
                self._cache = (np.asarray(self.time), np.asarray(self.data))
            return self._cache

    def compute_metrics(self) -> dict[str, float | None]:
        """
        Live stats over the current buffer: min, mean, max, peak-to-peak, RMS —
        of its finite values, so a NaN (A / B where B is 0, say) doesn't make
        them all NaN for as long as it's in the buffer.
        """
        _, arr = self.snapshot()
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            return {"min": None, "mean": None, "max": None, "ptp": None, "rms": None}

        lo, hi = float(arr.min()), float(arr.max())
        return {
            "min": lo,
            "mean": float(arr.mean()),
            "max": hi,
            "ptp": hi - lo,
            "rms": float(np.sqrt(np.dot(arr, arr) / len(arr))),  # same as np.sqrt(np.mean(np.square(arr)))
        }

    def add_filter(self, cutoff: float = 50.0, order: int = 4) -> None:
        sos = signal.butter(N=order, Wn=cutoff, btype="low", analog=False, fs=self.fs, output="sos")
        self.sos, self.zi = sos, None  # The state starts from the next sample filtered, see `add_data`

    def remove_filter(self) -> None:
        self.sos = None
        self.zi = None

    def add_scaling(self, gain: float, offset: float) -> None:
        self.gain = gain
        self.offset = offset

    def remove_scaling(self) -> None:
        self.gain = None
        self.offset = None
