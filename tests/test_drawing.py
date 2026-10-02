"""
What keeps drawing fast (see scripts/benchmark_refresh.py): the grid drawn
solid behind the data, downsampling by default, a precise refresh timer, the
toolbar left alone while nothing changes — and the Load bar, which counts
everything the GUI thread does, painting included.
"""

import time

import numpy as np
import pytest
from PyQt6 import QtCore as qtc
from PyQt6 import QtGui as qtg

from oscilab.grid import GridAxis, solid
from oscilab.oscilab import Oscilab
from oscilab.session import Session
from oscilab.trigger import TriggerSettings
from oscilab.ui.load import BusyMeter


@pytest.fixture
def window(qapp):
    scope = Oscilab(app=qapp)
    yield scope
    scope.close()


def run(seconds: float, every_ms: int, tick) -> None:
    """Run the event loop for `seconds`, calling `tick` every `every_ms`."""
    timer = qtc.QTimer()
    timer.setTimerType(qtc.Qt.TimerType.PreciseTimer)
    timer.timeout.connect(tick)
    timer.start(every_ms)
    loop = qtc.QEventLoop()
    qtc.QTimer.singleShot(round(seconds * 1000), loop.quit)
    loop.exec()
    timer.stop()


# --- the grid ----------------------------------------------------------------------------------


def test_a_translucent_pen_becomes_the_color_it_shows_over_the_background():
    pen = qtg.QPen(qtg.QColor(255, 255, 255, 51))  # 20 % white
    color = solid(pen, qtg.QColor(0, 0, 100)).color()
    assert color.alpha() == 255
    assert color.red() == pytest.approx(51, abs=1)
    assert color.blue() == pytest.approx(51 + 80, abs=1)  # 20 % of 255, and 80 % of 100


def test_a_solid_pen_stays_as_it_is():
    pen = qtg.QPen(qtg.QColor("red"))
    assert solid(pen, qtg.QColor("black")) is pen


@pytest.mark.parametrize("kind", ["timeseries", "logic", "xy", "histogram"])
def test_every_plot_draws_its_grid_solid_behind_the_data(qapp, workspace, kind):
    for label in ("a", "b"):
        workspace.register_signal(label, fs=10).add_data(np.arange(20) / 10, np.arange(20.0))
    add = {
        "timeseries": lambda: workspace.add_plot("P"),
        "logic": lambda: workspace.add_logic_plot("P"),
        "xy": lambda: workspace.add_xy_plot("P", "a", "b"),
        "histogram": lambda: workspace.add_histogram("a"),
    }
    plot, _ = add[kind]()
    plot.resize(400, 300)
    plot.show()
    qapp.processEvents()

    for name in ("left", "bottom"):
        axis = plot.getAxis(name)
        assert isinstance(axis, GridAxis)
        assert axis.zValue() < plot.getViewBox().zValue()  # Under the curves
        image = qtg.QImage(400, 300, qtg.QImage.Format.Format_ARGB32_Premultiplied)
        painter = qtg.QPainter(image)
        specs = axis.generateDrawSpecs(painter)
        painter.end()
        assert specs is not None
        _, lines, _ = specs
        assert lines
        assert all(pen.color().alpha() == 255 for pen, _, _ in lines)
    plot.hide()


@pytest.mark.parametrize("kind", ["timeseries", "xy", "histogram"])
def test_a_refresh_paints_the_plot_once(qapp, workspace, kind):
    models = [workspace.register_signal(label, fs=100) for label in ("a", "b")]
    add = {
        "timeseries": lambda: workspace.add_plot("P"),
        "xy": lambda: workspace.add_xy_plot("P", "a", "b"),
        "histogram": lambda: workspace.add_histogram("a"),
    }
    plot, _ = add[kind]()
    if kind == "timeseries":
        workspace.link(plot="P", label="a")
    plot.resize(400, 300)
    plot.show()

    paints: list[bool] = []

    class Count(qtc.QObject):
        def eventFilter(self, _watched, event):  # noqa: N802 — Qt's name
            if event.type() == qtc.QEvent.Type.Paint:
                paints.append(True)
            return False

    counter = Count()
    plot.viewport().installEventFilter(counter)
    for tick in range(10):
        if tick == 5:
            paints.clear()  # Settled in
        t = (tick * 10 + np.arange(10)) / 100
        for i, model in enumerate(models):
            model.add_data(t, np.sin(t * (i + 1)) * (tick + 1))  # Growing: the axes move every time
        workspace.refresh()
        qapp.processEvents()
        qapp.processEvents()
    # Not twice each: the axes moved before painting, not during it. (The
    # histogram's title, its stats, can change width: a layout more, now and then.)
    assert len(paints) <= 6
    plot.hide()


# --- downsampling ------------------------------------------------------------------------------


def test_downsampling_is_on_by_default(window):
    assert window.workspace.downsampling
    assert window.downsample_action.isChecked()
    assert window.downsample_action.text() == "Downsampled"
    assert Session().downsampling  # Also for a session that doesn't say

    window.register_signal("s", fs=10)
    plot = window.add_timeseries_plot("P")
    window.link(plot="P", label="s")
    opts = plot.curves["s"].opts
    assert opts["autoDownsample"]
    assert opts["clipToView"]
    assert opts["downsampleMethod"] == "peak"
    assert opts["autoDownsampleFactor"] == 2.0  # Two min-max pairs per pixel column, not pyqtgraph's five


def test_full_detail_draws_every_sample(window):
    window.register_signal("s", fs=10)
    plot = window.add_timeseries_plot("P")
    window.link(plot="P", label="s")
    window.set_downsampling(False)
    assert window.downsample_action.text() == "Full Detail"
    assert not plot.curves["s"].opts["autoDownsample"]


# --- the refresh loop --------------------------------------------------------------------------


def test_the_refresh_timer_is_precise(window):
    # A coarse one comes round to Windows' 15.6 ms clock ticks: 21 frames a second for 30
    assert window.refresh_timer.timerType() == qtc.Qt.TimerType.PreciseTimer


@pytest.mark.parametrize("triggering", [False, True])
def test_the_trigger_button_is_left_alone_while_its_state_stays(window, triggering):
    window.register_signal("s", fs=1_000).add_data(np.arange(1000) / 1000, np.zeros(1000))
    if triggering:
        window.set_trigger(TriggerSettings(source="s", auto=False))  # Waits: a flat line never crosses
    window.refresh_timer.timeout.emit()
    changes: list[bool] = []
    window.trigger_ui.action.changed.connect(lambda: changes.append(True))

    for _ in range(5):
        window.refresh_timer.timeout.emit()
    assert changes == []  # Each change would lay out the toolbar again, every frame


# --- the Load bar ------------------------------------------------------------------------------


def test_the_busy_meter_counts_the_time_the_thread_works(qapp):  # noqa: ARG001 — needs the event loop
    meter = BusyMeter()
    worked: list[bool] = []

    def work() -> None:
        if not worked:
            time.sleep(0.2)  # Busy, as far as the event loop goes
            worked.append(True)

    before = meter.busy()
    run(0.6, 50, work)
    busy = meter.busy() - before
    assert 0.2 <= busy < 0.45  # The sleep, but not the time it waited


def test_the_load_bar_shows_how_busy_the_gui_thread_is(window):
    window.refresh_timer.stop()  # Its updates are driven here
    load = window.load_ui

    run(0.4, 30, load.update)
    assert load.bar.value() < 30  # Next to nothing to do

    def busy_frame() -> None:
        time.sleep(0.025)  # 25 of every 30 ms: painting, say
        load.update()

    run(0.6, 30, busy_frame)
    assert load.bar.value() > 50
