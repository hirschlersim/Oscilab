from collections import deque

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
    consumer below does every tick; see `scripts/benchmark_buffers.py`.

    `snapshot()` caches that `np.asarray()` conversion for the tick: every
    consumer (a plot's curve, `compute_metrics()`, a `MathSignal`, a
    spectrogram) used to redo its own `np.asarray(self.data)` independently,
    so a signal read by N consumers paid for the conversion N times over —
    call `snapshot()` instead and they share one. Invalidated on any write.
    """

    def __init__(self, label: str, fs: float, duration: float = 10.0, storage: Storage = Storage.ring) -> None:
        self.label = label
        self.fs = fs
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
        self.duration = duration
        maxlen = self.__maxlen_for(duration)
        self.time = self.__rebuilt(self.time, maxlen)
        self.data = self.__rebuilt(self.data, maxlen)
        self._cache = None

    def add_data(self, t: npt.ArrayLike, x: npt.ArrayLike) -> None:
        samples = np.asarray(x)

        if self.gain is not None and self.offset is not None:
            samples = (samples - self.offset) * self.gain

        if self.sos is not None and self.zi is not None:
            samples, self.zi = signal.sosfilt(sos=self.sos, x=samples, zi=self.zi)

        self.data.extend(samples)
        self.time.extend(np.asarray(t))
        self._cache = None

    def set_data(self, t: npt.ArrayLike, x: npt.ArrayLike) -> None:
        """
        Wholesale-replace the buffer (used by derived/math signals).

        Unlike `add_data`, this does not apply filter/scaling: those assume
        an incrementally-growing stream with persistent filter state, which
        doesn't hold for a value recomputed from scratch every tick.
        """
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
        if self._cache is None:
            self._cache = (np.asarray(self.time), np.asarray(self.data))
        return self._cache

    def compute_metrics(self) -> dict[str, float | None]:
        """Live stats over the current buffer: min, mean, max, peak-to-peak, RMS."""
        if not self.data:
            return {"min": None, "mean": None, "max": None, "ptp": None, "rms": None}

        _, arr = self.snapshot()
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
        self.sos, self.zi = sos, signal.sosfilt_zi(sos)

    def remove_filter(self) -> None:
        self.sos = None
        self.zi = None

    def add_scaling(self, gain: float, offset: float) -> None:
        self.gain = gain
        self.offset = offset

    def remove_scaling(self) -> None:
        self.gain = None
        self.offset = None
