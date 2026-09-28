"""
SignalModel: rolling buffer, scaling/filter pipeline, resizing, metrics.

Parametrized over both storage backends wherever the buffer is involved —
they're meant to be interchangeable.
"""

import numpy as np
import pytest
from scipy import signal

from oscilab.signal_model import SignalModel
from oscilab.storage import Storage

BACKENDS = [Storage.ring, Storage.deque]


def feed_chunks(model: SignalModel, t: np.ndarray, x: np.ndarray, chunk: int):
    for i in range(0, len(t), chunk):
        model.add_data(t[i : i + chunk], x[i : i + chunk])


@pytest.mark.parametrize("storage", BACKENDS)
def test_buffer_keeps_only_the_last_duration_seconds(storage):
    model = SignalModel(label="s", fs=10.0, duration=1.0, storage=storage)  # 10 samples
    t = np.arange(25) / 10.0
    feed_chunks(model, t, t * 2, chunk=4)

    time_arr, data_arr = model.snapshot()
    np.testing.assert_array_equal(time_arr, t[-10:])
    np.testing.assert_array_equal(data_arr, t[-10:] * 2)


@pytest.mark.parametrize("storage", BACKENDS)
def test_set_duration_keeps_the_newest_samples(storage):
    model = SignalModel(label="s", fs=10.0, duration=2.0, storage=storage)
    t = np.arange(20) / 10.0
    model.add_data(t, t)

    model.set_duration(0.5)  # shrink: keep the newest 5
    np.testing.assert_array_equal(model.snapshot()[0], t[-5:])

    model.set_duration(3.0)  # grow: no back-fill of what was dropped...
    np.testing.assert_array_equal(model.snapshot()[0], t[-5:])

    t_more = 2.0 + np.arange(30) / 10.0  # ...but room for 30 samples again
    model.add_data(t_more, t_more)
    assert len(model.snapshot()[0]) == 30


@pytest.mark.parametrize("storage", ["ring", "deque"])
def test_storage_accepts_its_string_value(storage):
    assert SignalModel(label="s", fs=1.0, storage=storage).storage is Storage(storage)


def test_snapshot_is_cached_until_the_next_write():
    model = SignalModel(label="s", fs=10.0)
    model.add_data([0.0, 0.1], [1.0, 2.0])
    first = model.snapshot()
    assert model.snapshot() is first  # same tick, same arrays

    model.add_data([0.2], [3.0])
    assert model.snapshot()[1].tolist() == [1.0, 2.0, 3.0]

    model.set_data([5.0], [9.0])
    assert model.snapshot()[1].tolist() == [9.0]

    model.set_duration(5.0)
    assert model.snapshot()[1].tolist() == [9.0]


def test_scaling_subtracts_offset_then_applies_gain():
    model = SignalModel(label="s", fs=10.0)
    model.add_scaling(2.0, 1.0)
    model.add_data([0.0, 0.1, 0.2], [1.0, 2.0, 4.0])
    assert model.snapshot()[1].tolist() == [0.0, 2.0, 6.0]

    model.rem_scaling()
    model.add_data([0.3], [4.0])
    assert model.snapshot()[1][-1] == 4.0


def test_filter_state_carries_across_chunks():
    # Filtering chunk by chunk must match filtering the whole stream at once;
    # otherwise every chunk boundary would glitch.
    fs = 1_000.0
    rng = np.random.default_rng(0)
    t = np.arange(2_000) / fs
    x = np.sin(2 * np.pi * 5 * t) + rng.normal(0, 0.5, t.size)

    model = SignalModel(label="s", fs=fs, duration=10.0)
    model.add_filter(cutoff=20.0, order=4)
    feed_chunks(model, t, x, chunk=37)

    sos = signal.butter(N=4, Wn=20.0, btype="low", fs=fs, output="sos")
    expected, _ = signal.sosfilt(sos, x, zi=signal.sosfilt_zi(sos))
    np.testing.assert_allclose(model.snapshot()[1], expected, atol=1e-12)


def test_scaling_is_applied_before_the_filter():
    fs = 100.0
    x = np.linspace(0.0, 1.0, 50)
    model = SignalModel(label="s", fs=fs)
    model.add_scaling(3.0, 0.5)
    model.add_filter(cutoff=10.0, order=2)
    model.add_data(np.arange(50) / fs, x)

    sos = signal.butter(N=2, Wn=10.0, btype="low", fs=fs, output="sos")
    expected, _ = signal.sosfilt(sos, (x - 0.5) * 3.0, zi=signal.sosfilt_zi(sos))
    np.testing.assert_allclose(model.snapshot()[1], expected)


def test_removing_the_filter_passes_raw_values_again():
    model = SignalModel(label="s", fs=100.0)
    model.add_filter(cutoff=10.0)
    model.rem_filter()
    model.add_data([0.0, 0.01], [3.0, -3.0])
    assert model.snapshot()[1].tolist() == [3.0, -3.0]


def test_metrics_match_numpy():
    x = np.array([1.0, -2.0, 3.0, 0.5])
    model = SignalModel(label="s", fs=1.0)
    model.add_data(np.arange(4.0), x)

    metrics = model.compute_metrics()
    assert metrics["min"] == -2.0
    assert metrics["max"] == 3.0
    assert metrics["ptp"] == 5.0
    assert metrics["mean"] == pytest.approx(x.mean())
    assert metrics["rms"] == pytest.approx(np.sqrt(np.mean(x**2)))


def test_metrics_of_an_empty_buffer_are_none():
    model = SignalModel(label="s", fs=1.0)
    assert set(model.compute_metrics().values()) == {None}
