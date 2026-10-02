"""
Benchmark: deque(maxlen=...) vs RingBuffer for Oscilab's actual access pattern.

The real workload per signal, every refresh tick (default 30ms):
  1. One or more small `extend()` calls from producer chunks (~10 samples).
  2. One `np.asarray(buffer)` conversion for plotting/metrics/export.

    uv run oscilab-bench-buffers
    uv run oscilab-bench-buffers --chunks 2000   # quicker, rougher
"""

import argparse
import time
from collections import deque
from collections.abc import Callable, Sequence

import numpy as np

from oscilab.ring_buffer import RingBuffer

type Buffer = deque[float] | RingBuffer


def bench(make_buffer: Callable[[int], Buffer], maxlen: int, chunk_size: int, n_chunks: int) -> float:
    """Seconds for `n_chunks` ticks, each extending the buffer by a chunk and then reading it whole."""
    buf = make_buffer(maxlen)
    chunk = np.random.default_rng(0).normal(size=chunk_size)

    start = time.perf_counter()
    for _ in range(n_chunks):
        buf.extend(chunk)
        _ = np.asarray(buf)
    return time.perf_counter() - start


def make_deque(maxlen: int) -> Buffer:
    return deque(maxlen=maxlen)


def make_ring(maxlen: int) -> Buffer:
    return RingBuffer(maxlen)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Time deque against RingBuffer as Oscilab's signal buffers")
    parser.add_argument("--chunks", type=int, default=20_000, help="ticks timed per buffer (default: 20000)")
    args = parser.parse_args(argv)

    # (duration_s, fs) pairs covering the app's realistic range: default 10s
    # display window at typical sensor rates, plus a higher-rate case similar
    # to the demo's spectrogram channel.
    configs = [
        (10, 100),  # default duration, typical sensor: maxlen 1_000
        (10, 1000),  # default duration, spectrogram-rate signal: maxlen 10_000
        (60, 1000),  # longest duration option, high rate: maxlen 60_000
    ]
    chunk_size = 10  # 20_000 ticks of 10: ~200_000 samples, several minutes of "live" data

    print(f"{'duration':>9} {'fs':>6} {'maxlen':>8} | {'deque (s)':>10} {'ring (s)':>10} {'speedup':>9}")
    for duration, fs in configs:
        maxlen = duration * fs
        t_deque = bench(make_deque, maxlen, chunk_size, args.chunks)
        t_ring = bench(make_ring, maxlen, chunk_size, args.chunks)
        print(f"{duration:>8}s {fs:>6} {maxlen:>8} | {t_deque:>10.3f} {t_ring:>10.3f} {t_deque / t_ring:>8.2f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
