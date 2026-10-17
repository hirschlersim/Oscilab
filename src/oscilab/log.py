"""
Logging: one loguru logger, three destinations.

- The console: loguru's default stderr handler.
- A log file (`log_to_file`), rotated at 1 MB with the last 5 kept — what to
  attach to a bug report, producers' tracebacks included. Oscilab turns it
  on when it starts the application itself (the demo, the `oscilab`
  command); `log_file()` says where it is.
- Each window's status bar (`StatusLine`), for messages logged through that
  window's `Oscilab.logger` — which its Workspace and signal rows use too —
  colored, marked and timed by severity. Messages logged through the plain
  `loguru.logger` (a producer's traceback, say) go to the console and the
  file only.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from loguru import logger
from PyQt6 import QtCore as qtc
from PyQt6 import QtWidgets as qtw
from PyQt6 import sip

if TYPE_CHECKING:
    from loguru import Message

_FILE_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {thread.name} | {name}:{function}:{line} | {message}"
_file_sink: dict[str, Any] = {"id": None, "path": None}


def default_log_path() -> Path:
    """The per-user log file, e.g. `%LOCALAPPDATA%/Oscilab/logs/oscilab.log` on Windows."""
    base = qtc.QStandardPaths.writableLocation(qtc.QStandardPaths.StandardLocation.AppLocalDataLocation)
    return Path(base) / "logs" / "oscilab.log"


def log_to_file(path: str | Path | None = None) -> Path:
    """
    Also write every log message, with tracebacks, to `path` (default:
    `default_log_path()`). Once per process: later calls return the file
    already in use.
    """
    if _file_sink["id"] is None:
        path = Path(path) if path is not None else default_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _file_sink["id"] = logger.add(
            path,
            level="DEBUG",
            format=_FILE_FORMAT,
            rotation="1 MB",
            retention=5,
            encoding="utf-8",
            diagnose=False,  # Tracebacks without every local variable's value (think: big arrays)
        )
        _file_sink["path"] = path
    return _file_sink["path"]


def log_file() -> Path | None:
    """Where the log file is, if `log_to_file` has been called."""
    return _file_sink["path"]


def stop_logging_to_file() -> None:
    """Stop writing the log file; `log_to_file` starts one again."""
    if _file_sink["id"] is not None:
        logger.remove(_file_sink["id"])
        _file_sink.update(id=None, path=None)


class StatusLine(qtc.QObject):
    """
    Shows one window's log messages in its status bar, styled by severity.

    Receives the messages logged through a logger bound to `window_id`
    (`logger.bind(window=window_id)`), from any thread: they're handed to the
    GUI thread through a queued signal. Debug messages aren't shown.
    """

    _received = qtc.pyqtSignal(str, str)  # (level name, message)

    # Per severity: (marker, color on the dark theme, color on the light one,
    # bold, how long it stays in ms — 0 until the next message). The colors
    # have at least 4.5:1 contrast on each theme's status bar. The ✓/✕
    # markers are plain text symbols, so they take the message's color (✔/✖
    # get drawn from the emoji font on Windows, in their own colors).
    _STYLES: ClassVar[dict[str, tuple[str, str | None, str | None, bool, int]]] = {
        "INFO": ("", None, None, False, 4_000),
        "SUCCESS": ("✓ ", "#4ade80", "#17702e", False, 4_000),
        "WARNING": ("⚠ ", "#f0b429", "#8a5a00", False, 8_000),
        "ERROR": ("✕ ", "#ff6b6b", "#c41d1d", True, 0),
        "CRITICAL": ("✕ ", "#ff6b6b", "#c41d1d", True, 0),
    }

    def __init__(self, status_bar: qtw.QStatusBar, window_id: int, theme: str = "dark") -> None:
        super().__init__(status_bar)  # Lives on the GUI thread, and goes away with the status bar
        self._bar = status_bar
        self.theme = theme  # "dark" or "light": picks the colors
        self._received.connect(self._show)
        self._handler_id: int | None = logger.add(
            self._write,
            level="INFO",
            format="{message}",
            filter=lambda record: record["extra"].get("window") == window_id,
        )

    def close(self) -> None:
        """Stop showing messages (the window is closing)."""
        if self._handler_id is not None:
            logger.remove(self._handler_id)
            self._handler_id = None

    def _write(self, message: "Message") -> None:
        # A loguru sink: runs on whichever thread logged the message.
        if not sip.isdeleted(self):
            self._received.emit(message.record["level"].name, message.record["message"])

    def _show(self, level: str, message: str) -> None:
        marker, dark, light, bold, timeout = self._STYLES.get(level, self._STYLES["INFO"])
        color = dark if self.theme == "dark" else light
        rules = ([f"color: {color};"] if color else []) + (["font-weight: bold;"] if bold else [])
        self._bar.setStyleSheet(f"QStatusBar {{ {' '.join(rules)} }}" if rules else "")
        self._bar.showMessage(marker + message, timeout)
