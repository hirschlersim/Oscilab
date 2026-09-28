"""
Fixed-capacity, pre-allocated numpy circular buffer.

Exposes the same surface `SignalModel` needs from `collections.deque(maxlen=...)`
— `extend()`, `clear()`, `len()`, `[0]`/`[-1]`, iteration, and `np.asarray()`
via `__array__` — so it's a drop-in alternative storage backend, picked per
signal via `SignalModel(..., storage="ring")` / `Workspace.reg_signal(...,
storage="ring")`. See `scripts/benchmark_buffers.py` for a deque-vs-ring
throughput comparison.

Thread-safe: producer data attached via `Workspace.attach_producer` arrives
on the GUI thread, but a signal fed by hand (`reg_signal` + `add_data`) may
be written from any thread while the GUI thread reads it via
`to_array()`/`__array__` every refresh tick. `deque.append`/`extend` are
atomic under the GIL, but writing into a
pre-allocated array is several separate steps (slice assignment, then
`_start`/`_count` bookkeeping) that a reader could observe mid-update — so
every public method here holds `self._lock` for its whole body.
"""

import threading

import numpy as np


class RingBuffer:
    def __init__(self, maxlen: int):
        if maxlen <= 0:
            raise ValueError("maxlen must be positive")
        self.maxlen = maxlen
        self._buf = np.empty(maxlen, dtype=np.float64)
        self._start = 0  # index of the oldest element
        self._count = 0  # number of valid elements currently stored
        self._lock = threading.Lock()

    def extend(self, values):
        values = np.asarray(values, dtype=np.float64)
        n = len(values)
        if n == 0:
            return

        with self._lock:
            if n >= self.maxlen:
                # Only the most recent `maxlen` values can survive anyway.
                self._buf[:] = values[-self.maxlen :]
                self._start = 0
                self._count = self.maxlen
                return

            end = (self._start + self._count) % self.maxlen
            first_len = min(n, self.maxlen - end)
            self._buf[end : end + first_len] = values[:first_len]
            remaining = n - first_len
            if remaining:
                self._buf[0:remaining] = values[first_len:]

            new_count = self._count + n
            if new_count > self.maxlen:
                self._start = (self._start + (new_count - self.maxlen)) % self.maxlen
                new_count = self.maxlen
            self._count = new_count

    def clear(self):
        with self._lock:
            self._start = 0
            self._count = 0

    def to_array(self) -> np.ndarray:
        with self._lock:
            if self._count == 0:
                return np.empty(0, dtype=np.float64)

            end = self._start + self._count
            if end <= self.maxlen:
                return self._buf[self._start : end].copy()

            # The valid range wraps past the end of the backing array.
            return np.concatenate((self._buf[self._start : self.maxlen], self._buf[0 : end - self.maxlen]))

    def __array__(self, dtype=None) -> np.ndarray:
        arr = self.to_array()
        return arr.astype(dtype) if dtype is not None else arr

    def __len__(self) -> int:
        with self._lock:
            return self._count

    def __iter__(self):
        return iter(self.to_array())  # to_array() takes a consistent snapshot under the lock

    def __getitem__(self, index):
        with self._lock:
            if self._count == 0:
                raise IndexError("RingBuffer is empty")
            if index == 0:
                return float(self._buf[self._start])
            if index == -1:
                return float(self._buf[(self._start + self._count - 1) % self.maxlen])
            raise IndexError("RingBuffer only supports [0] and [-1] indexing")
