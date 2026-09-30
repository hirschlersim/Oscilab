"""
Producer pacing: a producer running on its own thread keeps up with real
time — neither falling behind (OS sleeps overshoot; see `SyncDataProducer`)
nor running ahead of the clock.

Uses real threads and a real clock, over a second per producer, with
tolerances wide enough for a busy CI machine but far narrower than the ~40%
shortfall of a naive sleep-per-chunk loop.
"""

import threading
import time

import numpy as np
import pytest
from PyQt6 import QtCore as qtc

from oscilab.producers import AsyncSyntheticProducer, DataProducer, SyntheticProducer

FS = 1_000.0
RUN_S = 1.0


class Recorder:
    """Collects a producer's chunks as they're emitted, on its own thread."""

    def __init__(self, producer: DataProducer):
        self.chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        # Direct: recorded on the producer's thread as it emits, no event loop needed.
        # (PyQt6's stub leaves out connect()'s connection-type argument.)
        producer.data_ready.connect(self.record, qtc.Qt.ConnectionType.DirectConnection)  # type: ignore[call-arg]

    def record(self, t: np.ndarray, _x: np.ndarray) -> None:
        with self._lock:
            self.chunks.append(t)

    def times(self) -> np.ndarray:
        with self._lock:
            return np.concatenate(self.chunks) if self.chunks else np.array([])


def run_for(producer: DataProducer, seconds: float) -> tuple[np.ndarray, float]:
    """Run `producer` for `seconds` of wall-clock time: (every timestamp it emitted, how long it ran)."""
    recorder = Recorder(producer)
    start = time.perf_counter()
    producer.start()
    time.sleep(seconds)
    producer.stop()
    return recorder.times(), time.perf_counter() - start


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(lambda: SyntheticProducer(fs=FS, noise=0.0), id="sync"),
        pytest.param(lambda: AsyncSyntheticProducer(fs=FS), id="async"),
    ],
)
def test_keeps_up_with_real_time(qapp, make):  # noqa: ARG001 — QThreads need the QApplication
    producer = make()
    times, elapsed = run_for(producer, RUN_S)

    assert len(times) >= 0.9 * FS * elapsed  # A naive sleep loop delivers only ~60% on Windows
    assert len(times) <= FS * elapsed + 1  # Never ahead of the clock
    assert times[-1] == pytest.approx(elapsed, abs=0.1)  # Its newest sample is about "now"
    np.testing.assert_allclose(np.diff(times), 1 / FS)  # Evenly spaced: nothing dropped or repeated


def test_a_restart_carries_on_in_real_time(qapp):  # noqa: ARG001 — QThreads need the QApplication
    producer = SyntheticProducer(fs=FS, noise=0.0)
    first, _ = run_for(producer, RUN_S / 2)
    second, elapsed = run_for(producer, RUN_S / 2)

    assert second[0] == pytest.approx(first[-1] + 1 / FS)  # Continues where it stopped, no gap
    assert len(second) >= 0.9 * FS * elapsed  # The second run keeps up too
