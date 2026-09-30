"""
Replay recordings made with Save Stream (`oscilab.recorder`).

    from oscilab import RecordingReplayProducer

    scope.replay_recording("D:/runs", loop=True)  # Every signal, one producer each, in a new plot
    # or one signal at a time:
    scope.attach_producer(RecordingReplayProducer("D:/runs", "channel-1"), "channel-1")

Point it at one recording file, or at a folder: then every `.h5` file in it
plays, in name order — which, for Save Stream's files, is the order they
were recorded in: by start time, then part. The parts of one recording keep
their recorded times; a separate recording follows right after the one
before it, instead of after however long passed between them. With `loop`,
after the last file it starts again from the first, and time keeps going
forward.

A producer replays one signal, at the sampling rate it was recorded at and
with its unit: a recording's signals can have different rates, and a
producer's channels share one. The producers replaying one recording all
lay it out on the same timeline, so they stay in step. Files are read a
block at a time as they play, so a long recording doesn't have to fit in
memory.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

import h5py
import numpy as np
from PyQt6 import QtCore as qtc

from oscilab.producers import SyncDataProducer

_BLOCK = 65_536  # Samples read from a file at a time


class _Group(NamedTuple):
    """One signal in one file."""

    name: str  # The HDF5 group
    fs: float
    unit: str
    length: int
    first: float  # Time of its first sample (nan while empty)
    last: float


class _File(NamedTuple):
    path: Path
    recording: str  # Which recording it's part of: its "started" attribute (or the file, for others)
    signals: dict[str, _Group]  # By label


def recording_files(path: str | Path) -> list[Path]:
    """The files a replay of `path` plays: that file, or a folder's `.h5` files in name order."""
    path = Path(path)
    if path.is_dir():
        files = sorted(path.glob("*.h5"))
        if not files:
            raise ValueError(f"No .h5 files in {path}")
        return files
    if not path.is_file():
        raise ValueError(f"{path} doesn't exist")
    return [path]


def recorded_signals(path: str | Path) -> list[str]:
    """The signals in a recording (a file, or a folder of them), in the order they first appear."""
    return list(dict.fromkeys(label for file in _scan(path) for label in file.signals))


def _scan(path: str | Path) -> list[_File]:
    files = []
    for file_path in recording_files(path):
        with h5py.File(file_path, "r") as f:
            signals = {}
            for name, group in f.items():
                if not (isinstance(group, h5py.Group) and "time" in group and "data" in group):
                    continue  # Not a signal
                times = group["time"]
                length = times.shape[0]
                label = str(group.attrs.get("label", name))
                signals[label] = _Group(
                    name,
                    float(group.attrs.get("fs", 0.0)),
                    str(group.attrs.get("unit", "")),
                    length,
                    float(times[0]) if length else float("nan"),
                    float(times[-1]) if length else float("nan"),
                )
            recording = str(f.attrs.get("started", file_path))
        files.append(_File(file_path, recording, signals))
    return files


class _Segment(NamedTuple):
    """The part of the signal one file holds, and where on the timeline it plays."""

    path: Path
    group: str
    length: int
    shift: float  # Added to its recorded times


class _Timeline(NamedTuple):
    shifts: list[float]  # Per file
    start: float
    period: float  # One pass through every file, the gap before the next pass included


def _timeline(files: list[_File]) -> _Timeline:
    """
    One continuous timeline for every file: the parts of a recording keep
    their times, and each new recording starts one (slowest) sample period
    after the previous one ends. The same for every signal, so replays of
    them stay in step.
    """
    rates = [group.fs for file in files for group in file.signals.values() if group.fs > 0]
    gap = 1 / min(rates) if rates else 0.0
    shifts: list[float] = []
    start = end = float("nan")
    previous = None
    for file in files:
        spans = [(group.first, group.last) for group in file.signals.values() if group.length]
        shift = shifts[-1] if shifts else 0.0
        if spans:
            first, last = min(s for s, _ in spans), max(e for _, e in spans)
            if np.isnan(start):
                start = first
            elif file.recording != previous:
                shift = end + gap - first
            end = max(end, last + shift) if not np.isnan(end) else last + shift
            previous = file.recording
        shifts.append(shift)
    return _Timeline(shifts, start, end - start + gap)


class RecordingReplayProducer(SyncDataProducer):
    """Replays one signal of a Save Stream recording — see the module docstring."""

    def __init__(self, path: str | Path, signal: str, loop: bool = False, parent: qtc.QObject | None = None) -> None:
        files = _scan(path)
        found = [file.signals[signal] for file in files if signal in file.signals]
        if not found:
            available = recorded_signals(path)
            raise ValueError(f"'{signal}' isn't in {path}; it has {available}")
        timeline = _timeline(files)
        self._segments = [
            _Segment(file.path, file.signals[signal].name, file.signals[signal].length, shift)
            for file, shift in zip(files, timeline.shifts, strict=True)
            if signal in file.signals and file.signals[signal].length
        ]
        if not self._segments:
            raise ValueError(f"'{signal}' has no samples in {path}")

        fs = found[0].fs or 1.0  # For the signal's buffer and filters; the recorded times are played as they are
        super().__init__(fs=fs, parent=parent)
        self.path = Path(path)
        self.signal = signal
        self.loop = loop
        self.channels = (signal,)
        self.units = (found[0].unit,)
        self._period = max(timeline.period, 1 / fs)  # Never 0: each pass must come after the last
        self._position = timeline.start  # Timeline time played up to; a restart carries on from here
        self._resume_at = timeline.start
        # Where the next sample comes from: pass, segment, and row in it
        self._pass = 0
        self._segment = 0
        self._row = 0
        self._block: tuple[np.ndarray, np.ndarray] | None = None  # Rows from `_row` on, times on the timeline
        self._file: h5py.File | None = None
        self._file_path: Path | None = None

    def on_start(self) -> None:
        if self._segment == len(self._segments):  # Played to the end: Start plays it again, time still going forward
            self._pass += 1
            self._segment = 0
        self._resume_at = self._position

    def on_stop(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file, self._file_path = None, None

    def _generate(self, elapsed: float) -> tuple[np.ndarray, np.ndarray]:
        target = self._resume_at + elapsed  # Everything up to here on the timeline is due
        times, values = [], []
        while (block := self.__block()) is not None:
            t, x = block
            due = int(np.searchsorted(t, target, side="right"))
            if due:
                times.append(t[:due])
                values.append(x[:due])
                self.__advance(due)
            if due < len(t):
                break
        if block is None and not self.loop:
            self._running = False  # Played to the end: stop
        if not times:
            return np.array([]), np.array([])
        self._position = float(times[-1][-1])
        return np.concatenate(times), np.concatenate(values)

    def __block(self) -> tuple[np.ndarray, np.ndarray] | None:
        """The unplayed rows of the current block, read in if need be; None once every file has played."""
        if self._block is not None and len(self._block[0]):
            return self._block
        if self._segment == len(self._segments):
            if not self.loop:
                return None
            self._pass += 1
            self._segment = 0
        segment = self._segments[self._segment]
        file = self.__open(segment.path)
        stop = min(self._row + _BLOCK, segment.length)
        group = file[segment.group]
        shift = segment.shift + self._pass * self._period
        self._block = (group["time"][self._row : stop] + shift, group["data"][self._row : stop].astype(float))
        return self._block

    def __advance(self, rows: int) -> None:
        if self._block is not None:
            self._block = (self._block[0][rows:], self._block[1][rows:])
        self._row += rows
        if self._row >= self._segments[self._segment].length:  # On to the next file
            self._segment += 1
            self._row = 0
            self._block = None

    def __open(self, path: Path) -> h5py.File:
        if self._file is None or self._file_path != path:
            if self._file is not None:
                self._file.close()
            self._file, self._file_path = h5py.File(path, "r"), path
        return self._file


def replay_labels(taken: Sequence[str], labels: Sequence[str]) -> list[str]:
    """Names for replayed signals: as recorded, or "<label> (replay)", "(replay 2)", ... where that's taken."""
    names: list[str] = []
    for label in labels:
        name, n = label, 1
        while name in taken or name in names:
            name = f"{label} (replay)" if n == 1 else f"{label} (replay {n})"
            n += 1
        names.append(name)
    return names
