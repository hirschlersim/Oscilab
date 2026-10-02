"""SignalModel: rolling buffer, scaling/filter pipeline, resizing, metrics."""

import threading

import numpy as np
import pytest
from scipy import signal

from oscilab.signal_model import SignalModel


def feed_chunks(model: SignalModel, t: np.ndarray, x: np.ndarray, chunk: int):
    for i in range(0, len(t), chunk):
        model.add_data(t[i : i + chunk], x[i : i + chunk])


def test_buffer_keeps_only_the_last_duration_seconds():
    model = SignalModel(label="s", fs=10.0, duration=1.0)  # 10 samples
    t = np.arange(25) / 10.0
    feed_chunks(model, t, t * 2, chunk=4)

    time_arr, data_arr = model.snapshot()
    np.testing.assert_array_equal(time_arr, t[-10:])
    np.testing.assert_array_equal(data_arr, t[-10:] * 2)


def test_set_duration_keeps_the_newest_samples():
    model = SignalModel(label="s", fs=10.0, duration=2.0)
    t = np.arange(20) / 10.0
    model.add_data(t, t)

    model.set_duration(0.5)  # shrink: keep the newest 5
    np.testing.assert_array_equal(model.snapshot()[0], t[-5:])

    model.set_duration(3.0)  # grow: no back-fill of what was dropped...
    np.testing.assert_array_equal(model.snapshot()[0], t[-5:])

    t_more = 2.0 + np.arange(30) / 10.0  # ...but room for 30 samples again
    model.add_data(t_more, t_more)
    assert len(model.snapshot()[0]) == 30


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

    model.remove_scaling()
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
    expected, _ = signal.sosfilt(sos, x, zi=signal.sosfilt_zi(sos) * x[0])  # Steady from the first sample on
    np.testing.assert_allclose(model.snapshot()[1], expected, atol=1e-12)


def test_scaling_is_applied_before_the_filter():
    fs = 100.0
    x = np.linspace(0.0, 1.0, 50)
    model = SignalModel(label="s", fs=fs)
    model.add_scaling(3.0, 0.5)
    model.add_filter(cutoff=10.0, order=2)
    model.add_data(np.arange(50) / fs, x)

    sos = signal.butter(N=2, Wn=10.0, btype="low", fs=fs, output="sos")
    scaled = (x - 0.5) * 3.0
    expected, _ = signal.sosfilt(sos, scaled, zi=signal.sosfilt_zi(sos) * scaled[0])
    np.testing.assert_allclose(model.snapshot()[1], expected)


def test_removing_the_filter_passes_raw_values_again():
    model = SignalModel(label="s", fs=100.0)
    model.add_filter(cutoff=10.0)
    model.remove_filter()
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


# --- what add_data takes -------------------------------------------------------------------


def test_one_time_and_one_value_is_a_sample():
    # A sensor read by hand gives one value at a time
    model = SignalModel(label="s", fs=10.0)
    model.add_data(0.1, 2.5)
    model.add_data(0.2, 3)
    assert model.snapshot()[0].tolist() == [0.1, 0.2]
    assert model.snapshot()[1].tolist() == [2.5, 3.0]


def test_times_and_values_must_pair_up():
    model = SignalModel(label="s", fs=10.0)
    with pytest.raises(ValueError, match="don't pair up"):
        model.add_data([0.0, 0.1, 0.2], [1.0, 2.0])
    assert len(model.data) == len(model.time) == 0  # Nothing half-written: the buffers stay in step


@pytest.mark.parametrize(("fs", "duration"), [(0.0, 10.0), (-1.0, 10.0), (10.0, 0.0)])
def test_rate_and_duration_must_be_positive(fs, duration):
    with pytest.raises(ValueError, match="must be positive"):
        SignalModel(label="s", fs=fs, duration=duration)


def test_a_new_duration_must_be_positive():
    with pytest.raises(ValueError, match="must be positive"):
        SignalModel(label="s", fs=10.0).set_duration(0)


def test_time_and_data_stay_in_step_while_written_from_another_thread():
    # A signal fed by hand may be fed from any thread while the GUI thread draws it
    model = SignalModel(label="s", fs=1_000.0, duration=1.0)
    stop = threading.Event()

    def write() -> None:
        n = 0
        while not stop.is_set():
            model.add_data(np.arange(n, n + 7) / 1_000.0, np.ones(7))
            n += 7

    writer = threading.Thread(target=write)
    writer.start()
    try:
        for _ in range(2_000):
            t, x = model.snapshot()
            assert t.shape == x.shape
            model._cache = None  # Read afresh each time, not the cached arrays
    finally:
        stop.set()
        writer.join()


# --- metrics and the filter with NaN in the data ----------------------------------------------


def test_metrics_leave_nan_out():
    # A / B is NaN where B is 0: that one sample mustn't make every metric NaN
    model = SignalModel(label="s", fs=1.0)
    model.add_data(np.arange(4.0), [1.0, np.nan, 3.0, -1.0])
    metrics = model.compute_metrics()
    assert (metrics["min"], metrics["max"], metrics["mean"]) == (-1.0, 3.0, 1.0)
    assert metrics["rms"] == pytest.approx(np.sqrt((1 + 9 + 1) / 3))


def test_metrics_of_nothing_but_nan_are_none():
    model = SignalModel(label="s", fs=1.0)
    model.add_data([0.0, 1.0], [np.nan, np.nan])
    assert set(model.compute_metrics().values()) == {None}


def test_the_filter_starts_where_the_signal_is():
    # Not from 1.0: a signal at 25 came out climbing from 1 to 25
    model = SignalModel(label="s", fs=100.0)
    model.add_filter(cutoff=5.0, order=4)
    model.add_data(np.arange(50) / 100.0, np.full(50, 25.0))
    np.testing.assert_allclose(model.snapshot()[1], 25.0)


def test_nan_passes_the_filter_without_spoiling_what_comes_after():
    model = SignalModel(label="s", fs=100.0)
    model.add_filter(cutoff=5.0, order=4)
    model.add_data([0.00, 0.01, 0.02], [2.0, np.nan, 2.0])
    model.add_data([0.03, 0.04], [2.0, 2.0])
    data = model.snapshot()[1]
    assert np.isnan(data[1])
    np.testing.assert_allclose(data[[0, 2, 3, 4]], 2.0)


def test_a_chunk_of_nothing_but_nan_leaves_the_filter_waiting():
    model = SignalModel(label="s", fs=100.0)
    model.add_filter(cutoff=5.0, order=4)
    model.add_data([0.0, 0.01], [np.nan, np.nan])
    model.add_data([0.02, 0.03], [7.0, 7.0])
    np.testing.assert_allclose(model.snapshot()[1][2:], 7.0)  # Started from the first real sample
