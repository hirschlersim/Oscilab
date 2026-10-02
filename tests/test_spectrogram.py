"""
Correctness of SpectrogramPlot's incremental STFT column cache.

Each refresh only transforms the samples that arrived since the last one,
so the cache has to line up with the columns a single STFT over the whole
recording would produce — same times, same values — including after the
rolling buffer has wrapped and started dropping old samples.
"""

import numpy as np
import pytest
from scipy import signal

from oscilab.signal_model import SignalModel
from oscilab.spectrogram_plot import SpectrogramPlot

FS = 1000.0
NPERSEG = 256
NOVERLAP = 128
HOP = NPERSEG - NOVERLAP
COL_DT = HOP / FS  # seconds between STFT columns


def make_recording(seconds: float) -> np.ndarray:
    rng = np.random.default_rng(0)
    n = int(seconds * FS)
    return np.sin(2 * np.pi * 50 * np.arange(n) / FS) + rng.normal(0, 0.1, n)


def make_spectrogram(duration: float = 2.0) -> tuple[SignalModel, SpectrogramPlot]:
    model = SignalModel(label="s", fs=FS, duration=duration)
    spec = SpectrogramPlot(label="s", model=model, nperseg=NPERSEG, noverlap=NOVERLAP)
    return model, spec


def feed(model, spec, x, start: int, stop: int, chunk: int, *, refresh: bool = True):
    """Write x[start:stop] in `chunk`-sized pieces, like a producer would, refreshing after each."""
    for i in range(start, stop, chunk):
        j = min(i + chunk, stop)
        model.add_data(np.arange(i, j) / FS, x[i:j])
        if refresh:
            spec.refresh()


# Chunk sizes that are and aren't multiples of the hop, including one
# smaller than a hop and one bigger than a whole STFT window.
@pytest.mark.parametrize("chunk", [7, 30, 128, 500])
@pytest.mark.usefixtures("qapp")
def test_cache_matches_single_full_stft(chunk):
    x = make_recording(5.0)  # 5 s through a 2 s buffer, so it wraps
    model, spec = make_spectrogram(duration=2.0)
    feed(model, spec, x, 0, len(x), chunk)

    times = spec._cached_times
    assert times.size > 0
    # Evenly spaced: no duplicate columns, nothing off the hop grid.
    np.testing.assert_allclose(np.diff(times), COL_DT)
    # Nothing older than the buffer's oldest sample.
    assert times[0] >= model.time[0]

    _, t_ref, z_ref = signal.stft(x, fs=FS, nperseg=NPERSEG, noverlap=NOVERLAP)
    ref_db = 20 * np.log10(np.abs(z_ref) + 1e-9)
    k = np.rint(times / COL_DT).astype(int)
    np.testing.assert_allclose(times, t_ref[k])
    assert spec._cached_cols is not None
    np.testing.assert_allclose(spec._cached_cols, ref_db[:, k], atol=1e-6)


@pytest.mark.usefixtures("qapp")
def test_relative_mode_image_ends_at_now():
    x = make_recording(5.0)
    model, spec = make_spectrogram(duration=2.0)
    spec.set_time_mode("relative")
    feed(model, spec, x, 0, len(x), chunk=30)

    rect = spec.img.mapRectToView(spec.img.boundingRect())
    # Each column is drawn centered on its time, so the newest one may
    # reach at most half a column past "now" (0)...
    assert rect.right() <= COL_DT / 2 + 1e-9
    # ...and the image spans no more than the buffer itself.
    assert rect.left() >= -(model.duration + COL_DT)


@pytest.mark.usefixtures("qapp")
def test_resumes_after_pause_longer_than_buffer():
    # While paused, producers keep writing but nothing refreshes — so the
    # buffer can roll past every cached column before the next update().
    x = make_recording(8.0)
    model, spec = make_spectrogram(duration=2.0)
    feed(model, spec, x, 0, 3000, chunk=30)
    feed(model, spec, x, 3000, 6000, chunk=30, refresh=False)
    feed(model, spec, x, 6000, len(x), chunk=30)

    times = spec._cached_times
    assert times.size > 0
    np.testing.assert_allclose(np.diff(times), COL_DT)
    assert times[0] >= model.time[0]


@pytest.mark.usefixtures("qapp")
def test_nan_in_the_signal_doesnt_spoil_the_color_levels():
    # The levels are taken once, from the first frame: NaN ones stayed until set by hand
    model, spec = make_spectrogram()
    x = make_recording(1.0)
    x[100] = np.nan  # A / B where B was 0, say
    model.add_data(np.arange(x.size) / model.fs, x)
    spec.refresh()
    levels = spec.img.getLevels()
    assert levels is not None
    assert np.all(np.isfinite(levels))
