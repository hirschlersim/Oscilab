"""
Benchmark: deque(maxlen=...) vs RingBuffer for Oscilab's actual access pattern.

The real workload per signal, every refresh tick (default 30ms):
  1. One or more small `extend()` calls from producer chunks (~10 samples).
  2. One `np.asarray(buffer)` conversion for plotting/metrics/export.

Run: uv run python scripts/benchmark_buffers.py
"""

import time
from collections import deque

import numpy as np

from oscilab.ring_buffer import RingBuffer


def bench(make_buffer, maxlen: int, chunk_size: int, n_chunks: int, reads_per_write: int) -> float:
    buf = make_buffer(maxlen)
    chunk = np.random.default_rng(0).normal(size=chunk_size)

    start = time.perf_counter()
    for i in range(n_chunks):
        buf.extend(chunk)
        if i % reads_per_write == 0:
            _ = np.asarray(buf)
    return time.perf_counter() - start


def make_deque(maxlen):
    return deque(maxlen=maxlen)


def make_ring(maxlen):
    return RingBuffer(maxlen)


if __name__ == "__main__":
    # (duration_s, fs) pairs covering the app's realistic range: default 10s
    # display window at typical sensor rates, plus a higher-rate case similar
    # to the demo's spectrogram channel.
    configs = [
        (10, 100),    # default duration, typical sensor: maxlen 1_000
        (10, 1000),   # default duration, spectrogram-rate signal: maxlen 10_000
        (60, 1000),   # longest duration option, high rate: maxlen 60_000
    ]
    chunk_size = 10
    n_chunks = 20_000  # ~= 200_000 samples pushed through, several minutes of "live" data
    reads_per_write = 1  # every tick both extends and reads, matching Workspace.refresh()

    print(f"{'duration':>9} {'fs':>6} {'maxlen':>8} | {'deque (s)':>10} {'ring (s)':>10} {'speedup':>9}")
    for duration, fs in configs:
        maxlen = int(duration * fs)
        t_deque = bench(make_deque, maxlen, chunk_size, n_chunks, reads_per_write)
        t_ring = bench(make_ring, maxlen, chunk_size, n_chunks, reads_per_write)
        speedup = t_deque / t_ring
        print(f"{duration:>8}s {fs:>6} {maxlen:>8} | {t_deque:>10.3f} {t_ring:>10.3f} {speedup:>8.2f}x")
