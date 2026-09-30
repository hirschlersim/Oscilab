"""
Save Stream — recording every signal to disk while it's on (`oscilab.recorder`)
— and Replay Recording, playing recordings back (`oscilab.replay`): the
toolbar button, its settings, the status bar's "● Saving" and the dialogs.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6 import QtCore as qtc
from PyQt6 import QtGui as qtg
from PyQt6 import QtWidgets as qtw

from oscilab.dialogs import ReplayDialog, StreamDialog
from oscilab.recorder import Recorder
from oscilab.replay import RecordingReplayProducer, recorded_signals, recording_files, replay_labels
from oscilab.ui.common import dot_icon

if TYPE_CHECKING:
    from oscilab.oscilab import Oscilab


class RecordingControls:
    """Save Stream and Replay Recording, for one window."""

    def __init__(self, window: "Oscilab") -> None:
        self.window = window
        documents = qtc.QStandardPaths.writableLocation(qtc.QStandardPaths.StandardLocation.DocumentsLocation)
        self.path = Path(documents) / "Oscilab" / "recording.h5"
        self.max_file_mb: float | None = 100.0
        self.recorder: Recorder | None = None  # While recording

        self.stream_action = qtg.QAction("Save Stream", window)
        self.stream_action.setCheckable(True)
        self.stream_action.setIcon(dot_icon("gray"))
        self.stream_action.setToolTip(
            "While on, record every signal to HDF5 files on disk — every sample, not just what the plots keep.\n"
            "Where to, and how big each file gets: Save Stream Settings, in its drop-down"
        )
        self.stream_action.toggled.connect(self.__set_recording)
        self.settings_action = qtg.QAction("Save Stream Settings...", window)
        self.settings_action.setToolTip("Where the stream is saved, and the size at which it starts a new file")
        self.settings_action.triggered.connect(self.__settings_dialog)
        self.folder_action = qtg.QAction("Open Stream Folder", window)
        self.folder_action.setToolTip("Open the folder the stream is saved in")
        self.folder_action.triggered.connect(self.__open_folder)
        self.replay_action = qtg.QAction("Replay Recording...", window)
        self.replay_action.setToolTip("Play a Save Stream recording back — a file, or a folder of them — looped or not")
        self.replay_action.triggered.connect(self.__replay_dialog)

        self.label = qtw.QLabel()  # "● Saving: ..." in the status bar, while recording
        self.label.setToolTip("Save Stream is recording to this file")
        self.label.hide()
        self.timer = qtc.QTimer(window)  # Its own: the refresh loop stops while paused, recording doesn't
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.__update_status)

    @property
    def menu_actions(self) -> list[qtg.QAction]:
        """The File menu's entries."""
        return [self.stream_action, self.settings_action, self.folder_action, self.replay_action]

    @property
    def drop_down_actions(self) -> list[qtg.QAction]:
        """The toolbar button's drop-down."""
        return [self.settings_action, self.folder_action]

    # --- Save Stream ---------------------------------------------------------------

    @property
    def recording(self) -> bool:
        return self.recorder is not None

    def set_options(self, path: str | Path, max_file_mb: float | None = 100.0) -> None:
        """See `Oscilab.set_recording_options`."""
        if max_file_mb is not None and max_file_mb <= 0:
            raise ValueError(f"max_file_mb must be positive (or None for no limit), got {max_file_mb}")
        self.path = Path(path)
        self.max_file_mb = max_file_mb

    def __set_recording(self, on: bool) -> None:
        if on:
            self.__start()
        else:
            self.stop()

    def __start(self) -> None:
        ws = self.window.workspace
        models = {label: model for label, model in ws.signals.items() if label not in ws.math_signals}
        max_bytes = round(self.max_file_mb * 1_000_000) if self.max_file_mb else None
        try:
            recorder = Recorder(models, self.path, max_file_bytes=max_bytes)
            first = recorder.start()
        except (ValueError, OSError) as e:  # No signals, or a path that can't be written: report, don't raise
            self.window.log(f"Couldn't save the stream: {e}", level="error")
            with qtc.QSignalBlocker(self.stream_action):
                self.stream_action.setChecked(False)
            return
        self.recorder = recorder
        self.stream_action.setIcon(dot_icon("red"))
        self.timer.start()
        self.__update_status()
        self.window.log(f"Saving the stream of {len(models)} signal(s) to {first.name}")

    def stop(self) -> None:
        """Stop recording, if it is: what's arrived is written, the file closed."""
        recorder = self.recorder
        if recorder is None:
            return
        self.recorder = None
        files = recorder.stop()
        self.timer.stop()
        self.label.hide()
        with qtc.QSignalBlocker(self.stream_action):  # Also when a write error ended it
            self.stream_action.setChecked(False)
        self.stream_action.setIcon(dot_icon("gray"))
        if recorder.error is not None:
            error = recorder.error
            self.window.logger.opt(exception=error).error(f"Saving the stream stopped: {type(error).__name__}: {error}")
        else:
            self.window.log(f"Stream saved: {len(files)} file(s) in {files[0].parent}", level="success")

    def __update_status(self) -> None:
        recorder = self.recorder
        if recorder is None:
            return
        if recorder.error is not None:
            self.stop()
            return
        size_mb = recorder.current_size / 1_000_000
        self.label.setText(
            f"<span style='color: #e5484d'>●</span> Saving: {recorder.files[-1].name} ({size_mb:.1f} MB)"
        )
        self.label.show()

    def __settings_dialog(self) -> None:
        dialog = StreamDialog(str(self.path), self.max_file_mb, parent=self.window)
        if dialog.exec() != qtw.QDialog.DialogCode.Accepted:
            return
        new_path, new_max_mb = dialog.get_values()
        if not new_path:
            self.window.log("Save Stream needs a file to save to", level="warning")
            return
        self.set_options(new_path, new_max_mb)
        limit = f"a new file every {new_max_mb:g} MB" if new_max_mb else "no size limit"
        self.window.log(f"Save Stream: to {new_path}, {limit}")

    def __open_folder(self) -> None:
        folder = self.path.parent
        folder.mkdir(parents=True, exist_ok=True)
        qtg.QDesktopServices.openUrl(qtc.QUrl.fromLocalFile(str(folder)))

    # --- Replay --------------------------------------------------------------------

    def replay(self, path: str | Path, signals: Sequence[str] | None = None, loop: bool = False) -> list[str]:
        """See `Oscilab.replay_recording`."""
        window = self.window
        chosen = recorded_signals(path) if signals is None else list(signals)
        if not chosen:
            raise ValueError(f"No signals to replay from {path}")
        producers = [RecordingReplayProducer(path, label, loop=loop) for label in chosen]  # All, before attaching any
        names = replay_labels(list(window.workspace.signals), chosen)
        for producer, name in zip(producers, names, strict=True):
            window.attach_producer(producer, name)

        title, n = f"Replay: {Path(path).name}", 2
        while title in window.workspace.views:
            title, n = f"Replay: {Path(path).name} ({n})", n + 1
        window.add_timeseries_plot(title)
        for name in names:
            window.link(plot=title, label=name)
        window.log(f"Replaying {len(names)} signal(s) from {Path(path).name}{', looped' if loop else ''}")
        return names

    def __replay_dialog(self) -> None:
        def scan(path: str) -> tuple[list[str], list[str]]:
            return [file.name for file in recording_files(path)], recorded_signals(path)

        dialog = ReplayDialog(str(self.path.parent), scan, parent=self.window)
        if dialog.exec() != qtw.QDialog.DialogCode.Accepted:
            return
        path, signals, loop = dialog.get_values()
        if not signals:
            self.window.log("No signals ticked to replay", level="warning")
            return
        try:
            self.replay(path, signals, loop=loop)
        except (ValueError, OSError) as e:  # E.g. a file being recorded to right now: report it, don't raise
            self.window.log(f"Couldn't replay {path}: {e}", level="error")
