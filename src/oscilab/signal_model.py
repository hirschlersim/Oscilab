from collections import deque

import numpy as np
from scipy import signal

from oscilab.ring_buffer import RingBuffer
from oscilab.storage import Storage


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

    def __init__(self, label: str, fs: float, duration: float = 10.0, storage: Storage = Storage.ring):
        self.label = label
        self.fs = fs
        self.duration = duration  # How many seconds of history the buffer holds
        self.storage = Storage(storage)  # Accepts a Storage member or its string value
        maxlen = self.__maxlen_for(duration)
        self.data = self.__make_buffer(maxlen)
        self.time = self.__make_buffer(maxlen)
        self.sos = None  # Filter
        self.zi = None
        self.gain = None  # Scaling
        self.offset = None
        self._cache = None  # (time_array, data_array), see `snapshot()`

    def __maxlen_for(self, duration: float) -> int:
        return max(1, round(duration * self.fs))

    def __make_buffer(self, maxlen: int):
        return RingBuffer(maxlen) if self.storage is Storage.ring else deque(maxlen=maxlen)

    def __rebuilt(self, existing, maxlen):
        """New empty buffer of `maxlen`, refilled with `existing`'s current tail."""
        buffer = self.__make_buffer(maxlen)
        values = np.asarray(existing)
        buffer.extend(values[-maxlen:] if len(values) > maxlen else values)
        return buffer

    def set_duration(self, duration: float):
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

    def add_data(self, t, x):
        x = np.asarray(x)

        if self.gain is not None:
            x = (x - self.offset) * self.gain

        if self.sos is not None:
            x, self.zi = signal.sosfilt(sos=self.sos, x=x, zi=self.zi)

        self.data.extend(x)
        self.time.extend(t)
        self._cache = None

    def set_data(self, t, x):
        """
        Wholesale-replace the buffer (used by derived/math signals).

        Unlike `add_data`, this does not apply filter/scaling: those assume
        an incrementally-growing stream with persistent filter state, which
        doesn't hold for a value recomputed from scratch every tick.
        """
        self.time.clear()
        self.time.extend(t)
        self.data.clear()
        self.data.extend(x)
        self._cache = None

    def snapshot(self) -> tuple[np.ndarray, np.ndarray]:
        """
        (time_array, data_array) for the buffer's current contents,
        computed once and cached until the next write — see class docstring.
        """
        if self._cache is None:
            self._cache = (np.asarray(self.time), np.asarray(self.data))
        return self._cache

    def compute_metrics(self) -> dict:
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

    def add_filter(self, cutoff: float = 50.0, order: int = 4):
        self.sos = signal.butter(N=order, Wn=cutoff, btype="low", analog=False, fs=self.fs, output="sos")
        self.zi = signal.sosfilt_zi(self.sos)

    def rem_filter(self):
        self.sos = None
        self.zi = None

    def add_scaling(self, gain: float, offset: float):
        self.gain = gain
        self.offset = offset

    def rem_scaling(self):
        self.gain = None
        self.offset = None
