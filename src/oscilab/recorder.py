"""
Stream signals to HDF5 files on disk as their samples arrive.

Where an export writes what's still in the rolling buffers, a recording keeps
every sample from the moment it starts until it stops, however long that
is. Each recording gets its own files, named after its base path and the
time it started — `recording.h5` becomes `recording_20260930-101500_001.h5`,
then `..._002.h5` and so on: with `max_file_bytes`, the recording moves on
to the next file once the current one reaches that size.

Every file is complete on its own, laid out like the File > .h5 export: one
group per signal, with `time` and `data` datasets and `fs`, `label` and
`unit` attributes; the file's own attributes say when the recording started
and which part it is. The values are what the signal holds — after its
filter and scaling, if they're on. Math signals aren't recorded: they're
recomputed from the others.

Writing happens on a thread of its own, so a slow disk never holds up the
GUI or the producers: `SignalModel.add_data` only hands each chunk over.
"""

import queue
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import h5py
import numpy as np

from oscilab.exporters import safe_name, unique_names
from oscilab.signal_model import SignalModel

_BATCH_S = 0.25  # Chunks gathered before each write
_FLUSH_EVERY_S = 1.0  # How often data reaches the disk, and a full file moves on to the next
_STOP = object()

type _Chunks = dict[str, list[tuple[np.ndarray, np.ndarray]]]  # label -> [(t, x), ...]


class Recorder:
    """Records `models` (keyed by label) into files based on `path` — see the module docstring."""

    def __init__(self, models: Mapping[str, SignalModel], path: str | Path, max_file_bytes: int | None = None) -> None:
        if not models:
            raise ValueError("There are no signals to record")
        if max_file_bytes is not None and max_file_bytes <= 0:
            raise ValueError(f"max_file_bytes must be positive (or None for no limit), got {max_file_bytes}")
        path = Path(path)
        self.models = dict(models)
        # Each signal's group in the files: `a-b` and `a_b` would share one
        self._groups = dict(zip(self.models, unique_names([safe_name(label) for label in self.models]), strict=True))
        self.base_path = path if path.suffix else path.with_suffix(".h5")
        self.max_file_bytes = max_file_bytes
        self.files: list[Path] = []  # Written so far, the current one last
        self.error: Exception | None = None  # What stopped the writing, if anything did
        self.current_size = 0  # Bytes in the current file, as of its last flush
        self._started = datetime.now()  # noqa: DTZ005 — local time names the files, as people read them
        self._queue: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._listeners: dict[str, Callable[[np.ndarray, np.ndarray], None]] = {}
        self._thread: threading.Thread | None = None

    @property
    def recording(self) -> bool:
        return self._thread is not None

    def start(self) -> Path:
        """Open the first file and start recording. Returns that file."""
        if self.recording:
            raise RuntimeError("Already recording")
        self.base_path.parent.mkdir(parents=True, exist_ok=True)
        file = self.__open_next_file()  # Here, not on the thread: an unwritable path fails this call
        for label, model in self.models.items():
            listener = self.__listener(label)
            model.add_listener(listener)
            self._listeners[label] = listener
        self._thread = threading.Thread(target=self.__write_loop, args=(file,), name="oscilab-recorder", daemon=True)
        self._thread.start()
        return self.files[-1]

    def stop(self) -> list[Path]:
        """Stop recording: what was handed over is written and the file closed. Returns every file written."""
        if self._thread is None:
            return list(self.files)
        for label, listener in self._listeners.items():
            self.models[label].remove_listener(listener)
        self._listeners.clear()
        self._queue.put(_STOP)
        self._thread.join()
        self._thread = None
        return list(self.files)

    def __listener(self, label: str) -> Callable[[np.ndarray, np.ndarray], None]:
        def hand_over(t: np.ndarray, x: np.ndarray) -> None:
            # Runs on whichever thread fed the signal: copy it, and leave the writing to ours
            self._queue.put((label, np.array(t, dtype=float), np.array(x, dtype=float)))

        return hand_over

    # --- on the writer thread --------------------------------------------------------

    def __write_loop(self, file: h5py.File) -> None:
        last_flush = time.monotonic()
        try:
            stopping = False
            while not stopping:
                chunks, stopping = self.__take()
                self.__append(file, chunks)
                if not stopping and time.monotonic() - last_flush >= _FLUSH_EVERY_S:
                    file = self.__flush(file)
                    last_flush = time.monotonic()
        except Exception as e:  # noqa: BLE001 — kept for the window to report: the recording ends here
            self.error = e
        finally:
            file.close()

    def __take(self) -> tuple[_Chunks, bool]:
        """What's handed over in the next `_BATCH_S`, and whether `stop()` was called."""
        chunks: _Chunks = {}
        deadline = time.monotonic() + _BATCH_S
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                item = self._queue.get(timeout=remaining)
            except queue.Empty:
                break
            if item is _STOP:
                return chunks, True
            label, t, x = item
            chunks.setdefault(label, []).append((t, x))
        return chunks, False

    def __append(self, file: h5py.File, chunks: _Chunks) -> None:
        for label, pieces in chunks.items():
            group = file[self._groups[label]]
            columns = {"time": [t for t, _ in pieces], "data": [x for _, x in pieces]}
            for name, arrays in columns.items():
                values = np.concatenate(arrays)
                dataset = group[name]
                end = dataset.shape[0]
                dataset.resize(end + len(values), axis=0)
                dataset[end:] = values

    def __flush(self, file: h5py.File) -> h5py.File:
        """Get it onto the disk; once it's reached `max_file_bytes`, carry on in the next file."""
        file.flush()
        self.current_size = file.id.get_filesize()
        if self.max_file_bytes is None or self.current_size < self.max_file_bytes:
            return file
        file.close()
        return self.__open_next_file()

    # --- on either -----------------------------------------------------------------

    def __open_next_file(self) -> h5py.File:
        part = len(self.files) + 1
        stamp = self._started.strftime("%Y%m%d-%H%M%S")
        path = self.base_path.with_name(f"{self.base_path.stem}_{stamp}_{part:03d}{self.base_path.suffix}")
        file = h5py.File(path, "w", track_order=True)  # Its signals listed in order, not alphabetically
        file.attrs["started"] = self._started.isoformat(timespec="seconds")
        file.attrs["part"] = part
        file.attrs["oscilab_version"] = _version()
        for label, model in self.models.items():
            group = file.create_group(self._groups[label])
            for name in ("time", "data"):
                group.create_dataset(name, shape=(0,), maxshape=(None,), dtype="f8", chunks=(4096,))
            group.attrs["fs"] = model.fs
            group.attrs["label"] = label
            group.attrs["unit"] = model.unit
        self.files.append(path)
        self.current_size = file.id.get_filesize()
        return file


def _version() -> str:
    try:
        return metadata.version("oscilab")
    except metadata.PackageNotFoundError:
        return "0+unknown"
