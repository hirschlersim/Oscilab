"""
Recorder: every sample from start to stop reaches HDF5 files on disk, split
into parts at a size limit — however much the rolling buffers have dropped.
"""

import threading

import h5py
import numpy as np
import pytest

from oscilab import recorder as recorder_module
from oscilab.recorder import Recorder
from oscilab.signal_model import SignalModel


def feed(model: SignalModel, start: int, n: int) -> None:
    """Samples start..start+n-1 of a ramp: value = 2 * sample index."""
    index = np.arange(start, start + n)
    model.add_data(index / model.fs, 2.0 * index)


def read(files) -> dict[str, dict[str, np.ndarray]]:
    """Every signal's time and data across all `files`, in order."""
    columns: dict[str, dict[str, list[np.ndarray]]] = {}
    for path in files:
        with h5py.File(path) as f:
            for name, group in f.items():
                for column in ("time", "data"):
                    columns.setdefault(name, {}).setdefault(column, []).append(group[column][:])
    return {name: {column: np.concatenate(parts) for column, parts in cols.items()} for name, cols in columns.items()}


@pytest.fixture
def models():
    return {
        "a": SignalModel("a", fs=100.0, duration=1.0, unit="V"),  # Keeps only 100 samples
        "b": SignalModel("b", fs=10.0, duration=1.0),
    }


def test_records_every_sample_not_just_the_buffer(models, tmp_path):
    recorder = Recorder(models, tmp_path / "rec" / "run.h5")
    first = recorder.start()
    for start in range(0, 1000, 50):  # 10 s of "a": ten times what its buffer holds
        feed(models["a"], start, 50)
    feed(models["b"], 0, 7)
    files = recorder.stop()

    assert files == [first]
    assert first.parent == tmp_path / "rec"  # Created
    assert first.name.startswith("run_")
    assert first.name.endswith("_001.h5")
    data = read(files)
    np.testing.assert_allclose(data["a"]["time"], np.arange(1000) / 100)
    np.testing.assert_allclose(data["a"]["data"], 2.0 * np.arange(1000))
    np.testing.assert_allclose(data["b"]["data"], 2.0 * np.arange(7))
    assert len(models["a"].data) == 100  # While the buffer kept only the last second


def test_each_file_is_described_like_an_export(models, tmp_path):
    recorder = Recorder(models, tmp_path / "run")  # No suffix: .h5
    recorder.start()
    (path,) = recorder.stop()

    assert path.suffix == ".h5"
    with h5py.File(path) as f:
        assert f.attrs["part"] == 1
        assert "started" in f.attrs
        assert dict(f["a"].attrs) == {"fs": 100.0, "label": "a", "unit": "V"}
        assert f["b"]["time"].shape == (0,)  # Nothing arrived: empty, but there


def test_a_size_limit_splits_it_into_parts(models, tmp_path, monkeypatch):
    monkeypatch.setattr(recorder_module, "_FLUSH_EVERY_S", 0.0)  # Check the size after every write
    monkeypatch.setattr(recorder_module, "_BATCH_S", 0.01)
    recorder = Recorder(models, tmp_path / "run.h5", max_file_bytes=200_000)
    recorder.start()
    for start in range(0, 100_000, 5_000):
        feed(models["a"], start, 5_000)
        threading.Event().wait(0.02)  # Let the writer take each batch on its own
    files = recorder.stop()

    assert len(files) > 1
    assert [f.name[-6:] for f in files] == [f"{part:03d}.h5" for part in range(1, len(files) + 1)]
    np.testing.assert_allclose(read(files)["a"]["data"], 2.0 * np.arange(100_000))  # Nothing lost between parts
    for path in files:
        with h5py.File(path) as f:
            assert set(f) == {"a", "b"}  # Each part complete on its own


def test_values_are_recorded_as_the_signal_holds_them(models, tmp_path):
    models["a"].add_scaling(gain=10.0, offset=1.0)
    recorder = Recorder(models, tmp_path / "run.h5")
    recorder.start()
    models["a"].add_data([0.0, 0.01], [1.0, 2.0])
    np.testing.assert_allclose(read(recorder.stop())["a"]["data"], [0.0, 10.0])


def test_signals_fed_from_other_threads_are_recorded(models, tmp_path):
    recorder = Recorder(models, tmp_path / "run.h5")
    recorder.start()
    threads = [threading.Thread(target=feed, args=(models["a"], start, 100)) for start in range(0, 1000, 100)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    data = read(recorder.stop())["a"]
    np.testing.assert_allclose(np.sort(data["data"]), 2.0 * np.arange(1000))


def test_nothing_is_recorded_after_stop(models, tmp_path):
    recorder = Recorder(models, tmp_path / "run.h5")
    recorder.start()
    feed(models["a"], 0, 10)
    files = recorder.stop()
    feed(models["a"], 10, 10)
    assert len(read(files)["a"]["data"]) == 10
    assert not recorder.recording


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [({"models": {}}, "no signals to record"), ({"max_file_bytes": 0}, "must be positive")],
)
def test_mistakes_are_refused(models, tmp_path, kwargs, message):
    arguments = {"models": models, "path": tmp_path / "run.h5", **kwargs}
    with pytest.raises(ValueError, match=message):
        Recorder(**arguments)


def test_a_path_that_cant_be_written_fails_right_away(models, tmp_path):
    (tmp_path / "taken").write_text("a file, not a folder")
    with pytest.raises(OSError):  # noqa: PT011 — whichever OSError the platform raises
        Recorder(models, tmp_path / "taken" / "run.h5").start()


def test_a_write_error_ends_the_recording_and_is_kept(models, tmp_path, monkeypatch):
    def fail(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(Recorder, "_Recorder__append", staticmethod(fail))
    recorder = Recorder(models, tmp_path / "run.h5")
    recorder.start()
    feed(models["a"], 0, 10)
    recorder.stop()
    assert str(recorder.error) == "disk full"
