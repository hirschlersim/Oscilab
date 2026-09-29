"""
Producers: multi-channel data, errors, cleanup, and where actions run.

Uses real producer threads. Data and errors cross back to the GUI thread as
queued Qt signals, so `wait_until` pumps the event loop while it waits.
"""

import asyncio
import threading
import time

import numpy as np
import pytest
from PyQt6 import QtWidgets as qtw

from oscilab.producers import (
    AsyncDataProducer,
    AsyncSyntheticProducer,
    FileReplayProducer,
    SyncDataProducer,
    SyntheticProducer,
)

MAIN_THREAD = threading.get_ident()


def wait_until(qapp, condition, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            pytest.fail("timed out waiting for condition")
        qapp.processEvents()
        time.sleep(0.005)


class Ramp(SyncDataProducer):
    """Channel k emits (k + 1) * t. Optionally fails, or emits the wrong shape."""

    def __init__(self, channels=("a",), fail_after: int | None = None, transpose: bool = False, fs: float = 1_000.0):
        super().__init__(fs=fs, interval_s=0.005)
        self.channels = tuple(channels)
        self.fail_after = fail_after
        self.transpose = transpose
        self.action_threads: list[int] = []
        self.on_stop_thread: int | None = None
        self._n = 0

    def _generate(self, _elapsed):
        if self.fail_after is not None and self._n >= self.fail_after:
            raise RuntimeError("device unplugged")
        t = np.arange(self._n, self._n + 10) / self.fs
        self._n += 10
        x = np.vstack([(k + 1) * t for k in range(len(self.channels))])
        return t, (x.T if self.transpose else x)

    def actions(self):
        return {"Mark": self.mark, "Explode": self.explode}

    def mark(self):
        self.action_threads.append(threading.get_ident())

    def explode(self):
        raise RuntimeError("action failed")

    def on_stop(self):
        self.on_stop_thread = threading.get_ident()


def action_buttons(workspace, label: str) -> dict[str, qtw.QPushButton]:
    row = workspace.signal_list.rows[label]
    row.set_expanded(True)  # Producer buttons live in a row's (collapsed by default) details
    return {b.text(): b for b in row.findChildren(qtw.QPushButton) if b.text() in ("Mark", "Explode")}


# --- attaching and multi-channel data ---------------------------------------


def test_channels_are_split_across_signals(qapp, workspace):
    producer = Ramp(channels=("a", "b", "c"))
    models = workspace.attach_producer(producer, ["x", "y", "z"])
    wait_until(qapp, lambda: len(models[2].data) >= 50)
    producer.stop()
    qapp.processEvents()

    t, x = workspace.signals["x"].snapshot()
    np.testing.assert_allclose(x, t)
    np.testing.assert_allclose(workspace.signals["y"].snapshot()[1], 2 * workspace.signals["y"].snapshot()[0])
    np.testing.assert_allclose(workspace.signals["z"].snapshot()[1], 3 * workspace.signals["z"].snapshot()[0])
    assert all(model.fs == producer.fs for model in models)


def test_single_channel_takes_a_plain_label(qapp, workspace):
    (model,) = workspace.attach_producer(SyntheticProducer(fs=500, freq=5.0), "s")
    wait_until(qapp, lambda: len(model.data) > 0)
    assert model.fs == 500


def test_signal_names_must_match_the_channels(workspace):
    producer = Ramp(channels=("a", "b"))
    with pytest.raises(ValueError, match=r"2 channel\(s\).*but 1 signal name"):
        workspace.attach_producer(producer, "only-one")
    assert not workspace.signals
    assert not producer.isRunning()


@pytest.mark.parametrize("signals", [["taken", "fresh"], ["dup", "dup"]])
def test_bad_names_leave_nothing_half_registered(workspace, signals):
    workspace.register_signal("taken", fs=10)
    with pytest.raises(ValueError, match=r"already registered|Duplicate"):
        workspace.attach_producer(Ramp(channels=("a", "b")), signals)
    assert list(workspace.signals) == ["taken"]


def test_synthetic_producer_makes_one_sine_per_frequency(qapp):  # noqa: ARG001
    producer = SyntheticProducer(fs=1_000, freq=(1.0, 10.0), noise=0)
    t, x = producer._generate(0.1)
    assert producer.channels == ("1 Hz", "10 Hz")
    assert x.shape == (2, t.size)
    np.testing.assert_allclose(x[1], np.sin(2 * np.pi * 10 * t))


def test_emit_data_accepts_1d_for_one_channel_and_rejects_wrong_shapes(qapp):  # noqa: ARG001
    received = []
    single, multi = Ramp(channels=("a",)), Ramp(channels=("a", "b"))
    single.data_ready.connect(lambda _t, x: received.append(x.shape))  # same thread: delivered directly

    single.emit_data([0.0, 0.1], [1.0, 2.0])
    assert received == [(1, 2)]

    with pytest.raises(ValueError, match=r"shape \(3, 2\), expected \(2, 3\).*\.T"):
        multi.emit_data([0.0, 0.1, 0.2], np.zeros((3, 2)))


def test_fs_must_be_positive(qapp):  # noqa: ARG001
    with pytest.raises(ValueError, match="fs must be positive"):
        Ramp(fs=0)


# --- errors and cleanup -----------------------------------------------------


def test_an_error_stops_only_that_producer(qapp, workspace):
    failing, healthy = Ramp(fail_after=30), Ramp()
    workspace.attach_producer(failing, "bad")
    workspace.attach_producer(healthy, "good")
    workspace.add_plot("P")
    workspace.link(plot="P", label="bad")

    wait_until(qapp, lambda: "bad" in workspace._producer_errors)
    count = len(workspace.signals["good"].data)
    wait_until(qapp, lambda: len(workspace.signals["good"].data) > count)  # still going

    assert failing.isFinished()
    assert healthy.isRunning()
    assert workspace._producer_errors["bad"] == "RuntimeError: device unplugged"
    name = workspace.signal_list.rows["bad"].name_button
    assert name.text().startswith("⚠")
    assert "device unplugged" in name.toolTip()


def test_a_row_created_after_the_error_still_shows_it(qapp, workspace):
    workspace.attach_producer(Ramp(fail_after=0), "bad")
    wait_until(qapp, lambda: "bad" in workspace._producer_errors)

    workspace.add_plot("P")
    workspace.link(plot="P", label="bad")
    assert workspace.signal_list.rows["bad"].name_button.text().startswith("⚠")


def test_a_wrong_shape_is_reported_with_a_hint(qapp, workspace):
    workspace.attach_producer(Ramp(channels=("a", "b"), transpose=True), ["x", "y"])
    wait_until(qapp, lambda: "x" in workspace._producer_errors)
    assert ".T" in workspace._producer_errors["x"]


@pytest.mark.parametrize("fail_after", [None, 0])
def test_on_stop_runs_on_the_producer_thread(qapp, workspace, fail_after):
    producer = Ramp(fail_after=fail_after)
    workspace.attach_producer(producer, "s")
    wait_until(qapp, lambda: len(workspace.signals["s"].data) > 0 or producer.isFinished())
    producer.stop()

    assert producer.on_stop_thread is not None
    assert producer.on_stop_thread != MAIN_THREAD


# --- actions ----------------------------------------------------------------


def test_actions_run_on_the_producer_thread(qapp, workspace):
    producer = Ramp(channels=("a", "b"))
    workspace.attach_producer(producer, ["x", "y"])
    workspace.add_plot("P")
    workspace.link(plot="P", label="x")
    workspace.link(plot="P", label="y")

    # One set of buttons per producer, on the first of its signals to get a row
    assert set(action_buttons(workspace, "x")) == {"Mark", "Explode"}
    assert action_buttons(workspace, "y") == {}

    action_buttons(workspace, "x")["Mark"].click()
    wait_until(qapp, lambda: producer.action_threads)
    assert producer.action_threads[0] != MAIN_THREAD


def test_a_failing_action_stops_the_producer(qapp, workspace):
    producer = Ramp()
    workspace.attach_producer(producer, "s")
    workspace.add_plot("P")
    workspace.link(plot="P", label="s")

    action_buttons(workspace, "s")["Explode"].click()
    wait_until(qapp, lambda: "s" in workspace._producer_errors)
    assert workspace._producer_errors["s"] == "RuntimeError: action failed"


def test_actions_are_refused_once_stopped(qapp, workspace):  # noqa: ARG001
    producer = Ramp()
    workspace.attach_producer(producer, "s")
    producer.stop()
    assert producer.queue_action(producer.mark) is False


def test_stop_right_after_start_does_not_hang(qapp):  # noqa: ARG001
    # stop() can land before the new thread even gets scheduled. Called from a
    # helper thread with a timeout, so a regression fails instead of hanging.
    for _ in range(20):
        producer = Ramp()
        producer.start()
        stopper = threading.Thread(target=producer.stop, daemon=True)
        stopper.start()
        stopper.join(timeout=2)
        hung = stopper.is_alive()
        if hung:
            producer._running = False  # let it end so the test run can still exit
            producer.wait()
        assert not hung, "stop() right after start() never returned"


# --- async producers --------------------------------------------------------


class Stuck(AsyncDataProducer):
    """Awaits a read that never completes — like a silent network socket."""

    def __init__(self, fail: bool = False):
        super().__init__(fs=100.0)
        self.fail = fail
        self.action_threads: list[int] = []
        self.on_stop_thread: int | None = None

    async def _run(self):
        if self.fail:
            raise ConnectionError("socket closed")
        await asyncio.Event().wait()

    def actions(self):
        return {"Mark": lambda: self.action_threads.append(threading.get_ident())}

    def on_stop(self):
        self.on_stop_thread = threading.get_ident()


def test_async_stop_does_not_wait_for_a_pending_read(qapp, workspace):  # noqa: ARG001
    producer = Stuck()
    producer.start()
    time.sleep(0.05)

    started = time.monotonic()
    producer.stop()
    assert time.monotonic() - started < 1.0
    assert producer.on_stop_thread not in (None, MAIN_THREAD)


def test_async_actions_run_on_the_producer_thread(qapp):
    producer = Stuck()
    producer.start()
    try:
        wait_until(qapp, lambda: producer.queue_action(producer.actions()["Mark"]))
        wait_until(qapp, lambda: producer.action_threads)
        assert producer.action_threads[0] != MAIN_THREAD
    finally:
        producer.stop()


def test_async_errors_are_reported(qapp, workspace):
    workspace.attach_producer(Stuck(fail=True), "s")
    wait_until(qapp, lambda: "s" in workspace._producer_errors)
    assert workspace._producer_errors["s"] == "ConnectionError: socket closed"


# --- file replay ------------------------------------------------------------


def test_file_replay_reads_channels_from_the_header(qapp, tmp_path):  # noqa: ARG001
    path = tmp_path / "rec.csv"
    path.write_text("time,ax,ay\n0.00,1,10\n0.01,2,20\n0.02,3,30\n")
    producer = FileReplayProducer(path)

    assert producer.channels == ("ax", "ay")
    assert producer.fs == pytest.approx(100.0)
    t, x = producer._generate(elapsed=1.0)
    np.testing.assert_allclose(t, [0.0, 0.01, 0.02])
    np.testing.assert_allclose(x, [[1, 2, 3], [10, 20, 30]])


def test_file_replay_loop_keeps_time_increasing(qapp, tmp_path):  # noqa: ARG001
    path = tmp_path / "rec.csv"
    path.write_text("time,value\n" + "".join(f"{i / 10:.1f},{i}\n" for i in range(5)))  # 0.0 .. 0.4 s
    producer = FileReplayProducer(path, loop=True)

    times = []
    for elapsed in np.arange(0.0, 2.0, 0.05):  # several passes' worth of ticks
        t, _ = producer._generate(elapsed)
        times.extend(t)

    np.testing.assert_allclose(np.diff(times), 0.1)  # continues across passes, never jumps back
    assert times[-1] > 1.0


def test_async_synthetic_reset_phase_keeps_time_running(qapp, workspace):
    producer = AsyncSyntheticProducer(fs=1_000, freq=3.0, chunk_size=10)
    (model,) = workspace.attach_producer(producer, "s")
    wait_until(qapp, lambda: len(model.data) >= 50)
    producer.queue_action(producer.reset_phase)
    wait_until(qapp, lambda: producer._phase_origin > 0)
    wait_until(qapp, lambda: model.snapshot()[0][-1] > producer._phase_origin)

    t, x = model.snapshot()
    assert np.all(np.diff(t) > 0)  # never jumps back
    restart = int(np.flatnonzero(np.isclose(t, producer._phase_origin))[0])
    assert x[restart] == pytest.approx(0.0)  # sine restarts at phase 0 there


# --- restarting -------------------------------------------------------------


class Flaky(Ramp):
    """Fails once, 20 samples into its first run; fine after a restart."""

    def __init__(self):
        super().__init__()
        self.start_threads: list[int] = []

    def on_start(self):
        self.start_threads.append(threading.get_ident())

    def _generate(self, elapsed):
        if len(self.start_threads) == 1 and self._n >= 20:
            raise RuntimeError("glitch")
        return super()._generate(elapsed)


def test_restart_button_restarts_a_failed_producer(qapp, workspace):
    producer = Flaky()
    (model,) = workspace.attach_producer(producer, "s")
    workspace.add_plot("P")
    workspace.link(plot="P", label="s")
    row = workspace.signal_list.rows["s"]

    wait_until(qapp, lambda: "s" in workspace._producer_errors)
    assert row.restart_button.isVisibleTo(row)
    count = len(model.data)

    row.restart_button.click()
    wait_until(qapp, lambda: len(model.data) > count)

    assert producer.isRunning()
    assert "s" not in workspace._producer_errors
    assert not row.name_button.text().startswith("⚠")
    assert not row.restart_button.isVisibleTo(row)
    assert len(producer.start_threads) == 2
    assert MAIN_THREAD not in producer.start_threads  # on_start runs on the producer's thread
    assert np.all(np.diff(model.snapshot()[0]) > 0)


def test_synthetic_producer_resumes_right_away_after_a_restart(qapp, workspace):
    (model,) = workspace.attach_producer(SyntheticProducer(fs=1_000), "s")
    wait_until(qapp, lambda: len(model.data) >= 300)  # ~0.3 s into the first run

    workspace.restart_producer("s")  # restarting a healthy producer works too
    count = len(model.data)
    started = time.monotonic()
    wait_until(qapp, lambda: len(model.data) > count + 20)

    # Without resuming from its last sample, it would stay silent for as
    # long as the first run lasted (~0.3 s) while its new clock caught up.
    assert time.monotonic() - started < 0.25
    t = model.snapshot()[0]
    np.testing.assert_allclose(np.diff(t), 1 / 1_000)  # no gap, never backwards


def test_file_replay_resumes_where_it_stopped(qapp, tmp_path):  # noqa: ARG001
    path = tmp_path / "rec.csv"
    path.write_text("time,value\n" + "".join(f"{i / 10:.1f},{i}\n" for i in range(10)))  # 0.0 .. 0.9 s
    producer = FileReplayProducer(path)

    producer.on_start()
    first, _ = producer._generate(elapsed=0.35)  # 0.0 .. 0.3
    producer.on_start()  # a restart: `elapsed` counts from 0 again
    resumed, _ = producer._generate(elapsed=0.25)

    np.testing.assert_allclose(first, [0.0, 0.1, 0.2, 0.3])
    np.testing.assert_allclose(resumed, [0.4, 0.5, 0.6])


def test_restart_needs_a_producer(workspace):
    workspace.register_signal("manual", fs=10)
    with pytest.raises(ValueError, match="No producer feeds 'manual'"):
        workspace.restart_producer("manual")
