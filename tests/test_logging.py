"""
Logging: each window's messages in its own status bar, styled by severity;
everything, tracebacks included, in the log file.
"""

import threading
import time

import pytest

from oscilab import log
from oscilab.oscilab import Oscilab
from oscilab.producers import SyncDataProducer


def wait_until(qapp, condition, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out waiting for condition"
        qapp.processEvents()
        time.sleep(0.005)


class Shown:
    """Records what a window's status bar is asked to show: (text, timeout ms, stylesheet)."""

    def __init__(self, window: Oscilab, monkeypatch):
        self.messages: list[tuple[str, int, str]] = []
        bar = window.statusBar()
        monkeypatch.setattr(
            bar, "showMessage", lambda text, timeout=0: self.messages.append((text, timeout, bar.styleSheet()))
        )

    @property
    def last(self) -> tuple[str, int, str]:
        return self.messages[-1]


@pytest.fixture
def make_window(qapp):
    windows = []

    def make() -> Oscilab:
        window = Oscilab(app=qapp)
        windows.append(window)
        return window

    yield make
    for window in windows:
        window.close()


@pytest.fixture
def window(make_window):
    return make_window()


def test_messages_are_styled_by_severity(window, monkeypatch):
    shown = Shown(window, monkeypatch)

    window.log("plain")
    text, timeout, style = shown.last
    assert (text, timeout, style) == ("plain", 4_000, "")

    window.log("careful", level="warning")
    text, timeout, style = shown.last
    assert text == "⚠ careful"
    assert timeout == 8_000
    assert "#f0b429" in style

    window.log("broken", level="error")
    text, timeout, style = shown.last
    assert text == "✕ broken"
    assert timeout == 0  # stays until the next message
    assert "#ff6b6b" in style
    assert "bold" in style

    window.set_theme("light")
    window.log("broken", level="error")
    assert "#c41d1d" in shown.last[2]  # darker red, readable on the light status bar

    count = len(shown.messages)
    window.log("details", level="debug")  # log file only
    assert len(shown.messages) == count


def test_each_window_shows_only_its_own_messages(make_window, monkeypatch):
    a, b = make_window(), make_window()
    shown_a, shown_b = Shown(a, monkeypatch), Shown(b, monkeypatch)

    a.log("for a")
    assert [m[0] for m in shown_a.messages] == ["for a"]
    assert shown_b.messages == []


def test_messages_logged_from_another_thread_arrive(qapp, window, monkeypatch):
    shown = Shown(window, monkeypatch)
    worker = threading.Thread(target=lambda: window.logger.warning("from a worker"))
    worker.start()
    worker.join()

    wait_until(qapp, lambda: shown.messages)  # queued to the GUI thread
    assert shown.last[0] == "⚠ from a worker"


def test_a_closed_window_stops_receiving_messages(qapp, window, monkeypatch, capfd):
    shown = Shown(window, monkeypatch)
    window.close()
    window.log("after close")
    qapp.processEvents()

    assert shown.messages == []
    assert "Logging error" not in capfd.readouterr().err


def test_signal_row_messages_reach_the_status_bar(window, monkeypatch):
    window.register_signal("s", fs=1_000)
    window.add_timeseries_plot("P")
    window.link(plot="P", label="s")
    shown = Shown(window, monkeypatch)

    window.workspace.signal_list.rows["s"].apply_filter(True, 50.0, 4)
    assert shown.last[0] == "Filter for 's' enabled: cutoff=50.0Hz, order=4"


class Failing(SyncDataProducer):
    def __init__(self):
        super().__init__(fs=100.0)

    def _generate(self, _elapsed):
        raise RuntimeError("device unplugged")


def test_log_file_gets_everything_including_tracebacks(qapp, window, tmp_path):
    path = log.log_to_file(tmp_path / "logs" / "oscilab.log")
    try:
        assert log.log_to_file(tmp_path / "elsewhere.log") == path  # once per process
        window.log("details", level="debug")
        window.attach_producer(Failing(), "s")
        wait_until(qapp, lambda: "s" in window.workspace._producer_errors)
    finally:
        log.stop_logging_to_file()

    text = path.read_text(encoding="utf-8")
    assert "DEBUG" in text
    assert "details" in text
    assert "Traceback" in text  # from the producer's thread
    assert "RuntimeError: device unplugged" in text
    assert "Failing for s stopped: RuntimeError: device unplugged" in text  # what the status bar said
    assert log.log_file() is None


@pytest.mark.usefixtures("window")
def test_windows_joining_a_running_app_dont_start_a_log_file():
    assert log.log_file() is None  # Tests mustn't write into the user's profile
