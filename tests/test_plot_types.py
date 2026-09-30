"""
Logic lanes, XY plots and histograms: what each draws, how they fit the
workspace (rows, units, removal), the Plots menu, and sessions.
"""

import numpy as np
import pytest
from PyQt6 import QtWidgets as qtw

from oscilab import session
from oscilab.dialogs import HistogramDialog, PlotDialog, XYDialog
from oscilab.histogram_plot import HistogramPlot
from oscilab.logic_plot import LogicPlot
from oscilab.oscilab import Oscilab
from oscilab.view import View
from oscilab.workspace import Workspace
from oscilab.xy_plot import XYPlot


def signal(workspace: Workspace, label: str, fs: float, values, unit: str = ""):
    """Register `label` holding `values`, one every 1/fs seconds from 0."""
    model = workspace.register_signal(label, fs=fs, unit=unit)
    values = np.asarray(values, dtype=float)
    model.add_data(np.arange(len(values)) / fs, values)
    return model


def drawn(item) -> tuple[np.ndarray, np.ndarray]:
    x, y = item.getData()
    return np.asarray(x), np.asarray(y)


# --- logic lanes ----------------------------------------------------------------------


def test_logic_lanes_stack_top_to_bottom_high_above_the_threshold(workspace):
    signal(workspace, "a", 10, [0, 1, 0.7, 0.2])
    signal(workspace, "b", 10, [1, 1, 0, 0])
    plot, _ = workspace.add_logic_plot("L")
    workspace.link(plot="L", label="a")
    workspace.link(plot="L", label="b")
    workspace.refresh()

    assert (plot.lane("a"), plot.lane("b")) == (1.5, 0.0)  # The first added on top
    np.testing.assert_allclose(drawn(plot.curves["a"])[1], 1.5 + np.array([0, 1, 1, 0]))
    np.testing.assert_allclose(drawn(plot.curves["b"])[1], [1, 1, 0, 0])
    ticks = plot.getAxis("left")._tickLevels[0]
    assert [name for _position, name in ticks] == ["a", "b"]  # Named on the Y axis

    plot.set_threshold(0.8)
    workspace.refresh()
    np.testing.assert_allclose(drawn(plot.curves["a"])[1], 1.5 + np.array([0, 1, 0, 0]))


def test_logic_lanes_are_a_time_plot_like_the_others(workspace):
    signal(workspace, "a", 10, [0, 1])
    first, _ = workspace.add_plot("T")
    logic, _ = workspace.add_logic_plot("L")
    workspace.link(plot="L", label="a")

    assert "L" in workspace.plots  # So Signals..., cursors, colors and time modes apply
    assert logic.getViewBox().linkedView(0) is first.getViewBox()  # Zooming time moves both
    assert logic.y_unit == ""
    y_min, y_max = logic.live_y_range()
    assert (y_min, y_max) == pytest.approx((-0.25, 1.25))  # One lane, fitted


# --- XY -----------------------------------------------------------------------------------


def test_xy_takes_y_at_xs_timestamps(workspace):
    signal(workspace, "x", 10, np.arange(10))  # 0..0.9 s
    signal(workspace, "y", 100, 2 * np.arange(50) / 100)  # y = 2t, 0..0.49 s
    plot, _ = workspace.add_xy_plot("XY", "x", "y")
    workspace.refresh()

    xs, ys = drawn(plot.curve)
    np.testing.assert_allclose(xs, [0, 1, 2, 3, 4])  # Only where both have data
    np.testing.assert_allclose(ys, [0.0, 0.2, 0.4, 0.6, 0.8])
    np.testing.assert_allclose(drawn(plot.head), ([4], [0.8]))  # The newest point


def test_xy_draws_only_its_trail(workspace):
    signal(workspace, "x", 10, np.arange(30))
    signal(workspace, "y", 10, np.arange(30))
    plot, _ = workspace.add_xy_plot("XY", "x", "y", trail=0.5)
    workspace.refresh()
    np.testing.assert_allclose(drawn(plot.curve)[0], [24, 25, 26, 27, 28, 29])  # The last half second


def test_xy_axes_are_named_after_their_signals(workspace):
    signal(workspace, "x", 10, [0], unit="V")
    signal(workspace, "y", 10, [0], unit="A")
    plot, _ = workspace.add_xy_plot("XY", "x", "y")
    assert "x (V)" in plot.getAxis("bottom").labelString()
    assert "y (A)" in plot.getAxis("left").labelString()
    assert plot.y_unit == "A"

    workspace.set_unit("x", "mV")
    assert "x (mV)" in plot.getAxis("bottom").labelString()


def test_xy_panel_has_x_axis_and_equal_axes(workspace):
    signal(workspace, "x", 10, [0])
    signal(workspace, "y", 10, [0])
    plot, controller = workspace.add_xy_plot("XY", "x", "y")
    buttons = {b.text(): b for b in controller.findChildren(qtw.QPushButton)}
    assert {"Y-Axis", "X-Axis", "Equal Axes"} <= set(buttons)

    buttons["Equal Axes"].click()
    assert plot.equal_axes
    assert plot.getViewBox().state["aspectLocked"]
    plot.set_x_range(False, -1.0, 1.0)
    assert plot.live_x_range() == pytest.approx((-1.0, 1.0))


def test_hiding_either_signal_hides_the_trace(workspace):
    signal(workspace, "x", 10, [0])
    signal(workspace, "y", 10, [0])
    plot, _ = workspace.add_xy_plot("XY", "x", "y")
    rows = workspace.signal_list.rows
    assert set(rows) == {"x", "y"}  # Each gets its row

    rows["y"].set_visible(False)
    assert not plot.curve.isVisible()
    rows["y"].set_visible(True)
    assert plot.curve.isVisible()


@pytest.mark.parametrize(
    ("x", "y", "trail", "message"),
    [("x", "x", None, "two different signals"), ("x", "nope", None, "Unknown signal"), ("x", "y", 0, "positive")],
)
def test_xy_mistakes_are_refused(workspace, x, y, trail, message):
    signal(workspace, "x", 10, [0])
    signal(workspace, "y", 10, [0])
    with pytest.raises(ValueError, match=message):
        workspace.add_xy_plot("XY", x, y, trail=trail)
    assert "XY" not in workspace.views


# --- histograms ----------------------------------------------------------------------------


def test_a_histogram_counts_the_buffer(workspace):
    values = [0.0, 0.1, 0.1, 0.5, 0.9, 1.0]
    signal(workspace, "s", 10, values, unit="V")
    plot, _ = workspace.add_histogram("s", bins=2)
    workspace.refresh()

    counts, edges = plot.counts()
    np.testing.assert_allclose(edges, [0.0, 0.5, 1.0])
    np.testing.assert_allclose(counts, [3, 3])
    title = plot.plotItem.titleLabel.text
    assert title.startswith("Histogram: s")
    assert f"n 6   μ {np.mean(values):.4g} V" in title


def test_a_fixed_range_keeps_the_bins_still(workspace):
    signal(workspace, "s", 10, [0.2, 0.4, 5.0])
    plot, _ = workspace.add_histogram("s", bins=4, value_range=(0.0, 1.0))
    counts, edges = plot.counts()
    np.testing.assert_allclose(edges, [0, 0.25, 0.5, 0.75, 1.0])
    np.testing.assert_allclose(counts, [1, 1, 0, 0])  # 5.0 is outside the range


def test_an_empty_signal_draws_nothing(workspace):
    workspace.register_signal("s", fs=10)
    plot, _ = workspace.add_histogram("s")
    workspace.refresh()
    assert plot.bars.getData()[0] is None
    assert plot.plotItem.titleLabel.text == "Histogram: s"


def test_the_bins_button_changes_them(workspace, monkeypatch):
    signal(workspace, "s", 10, [0, 1])
    plot, controller = workspace.add_histogram("s")

    def answer(dialog: HistogramDialog) -> qtw.QDialog.DialogCode:
        assert dialog.combo_signal is None  # Just the bins, for an existing histogram
        dialog.spin_bins.setValue(7)
        dialog.check_auto.setChecked(False)
        dialog.spin_min.setValue(-2)
        dialog.spin_max.setValue(2)
        return qtw.QDialog.DialogCode.Accepted

    monkeypatch.setattr(HistogramDialog, "exec", answer)
    next(b for b in controller.findChildren(qtw.QPushButton) if b.text() == "Bins...").click()
    assert (plot.bins, plot.value_range) == (7, (-2.0, 2.0))


@pytest.mark.parametrize(("bins", "value_range"), [(0, None), (10, (1.0, 1.0))])
def test_bad_bins_are_refused(workspace, bins, value_range):
    signal(workspace, "s", 10, [0])
    with pytest.raises(ValueError, match=r"bins|low to high"):
        workspace.add_histogram("s", bins=bins, value_range=value_range)


# --- in the workspace ----------------------------------------------------------------------------


def test_every_new_plot_is_a_view(workspace):
    signal(workspace, "a", 10, [0])
    signal(workspace, "b", 10, [0])
    workspace.add_logic_plot("L")
    workspace.add_xy_plot("XY", "a", "b")
    workspace.add_histogram("a")
    assert all(isinstance(view, View) for view in workspace.views.values())


def test_removing_a_signal_takes_the_plots_made_for_it(workspace):
    signal(workspace, "a", 10, [0])
    signal(workspace, "b", 10, [0])
    workspace.add_math_signal("sum", "+", "a", "b")
    workspace.add_logic_plot("L")
    workspace.link(plot="L", label="sum")
    workspace.link(plot="L", label="a")
    workspace.add_xy_plot("XY", "a", "sum")
    workspace.add_histogram("sum")

    workspace.remove_signal("sum")

    assert set(workspace.views) == {"L"}  # The XY plot and histogram of it went...
    assert workspace.plots["L"].signals == ["a"]  # ...the lanes lost its lane
    assert set(workspace.signal_list.rows) == {"a"}


def test_a_row_stays_while_any_plot_shows_its_signal(workspace):
    signal(workspace, "a", 10, [0])
    signal(workspace, "b", 10, [0])
    workspace.add_xy_plot("XY", "a", "b")
    workspace.add_histogram("a")
    workspace.remove_plot("XY")
    assert set(workspace.signal_list.rows) == {"a"}  # Still in the histogram
    workspace.remove_plot("Histogram: a")
    assert workspace.signal_list.rows == {}


def test_hiding_a_histograms_signal_hides_it(workspace):
    signal(workspace, "a", 10, [0])
    plot, _ = workspace.add_histogram("a")
    workspace.signal_list.rows["a"].set_visible(False)
    assert plot.isHidden()


# --- the Plots menu, and sessions ------------------------------------------------------------------


@pytest.fixture
def window(qapp):
    scope = Oscilab(app=qapp)
    for label, unit in (("a", "V"), ("b", "A")):
        scope.register_signal(label, fs=10, unit=unit).add_data(np.arange(5) / 10, np.arange(5.0))
    yield scope
    scope.close()


def menu_action(window: Oscilab, text: str):
    menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "Plots")
    return next(a for a in menu.actions() if a.text() == text)


def test_the_plots_menu_offers_every_type(window):
    menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "Plots")
    assert [a.text() for a in menu.actions()] == [
        "Add Time Series Plot...",
        "Add Logic Plot...",
        "Add XY Plot...",
        "Add Histogram...",
        "Add Spectrogram...",
    ]


def test_add_logic_plot_from_the_menu(window, monkeypatch):
    def answer(dialog: PlotDialog) -> qtw.QDialog.DialogCode:
        assert dialog.windowTitle() == "Add Logic Plot"
        assert dialog.edit_title.text() == "Logic 1"
        dialog.set_checked(["a", "b"])
        return qtw.QDialog.DialogCode.Accepted

    monkeypatch.setattr(PlotDialog, "exec", answer)
    menu_action(window, "Add Logic Plot...").trigger()
    plot = window.workspace.views["Logic 1"]
    assert isinstance(plot, LogicPlot)
    assert plot.signals == ["a", "b"]
    assert "Logic 1" in [a.text() for a in window.cursors_menu.actions()]


def test_add_xy_plot_from_the_menu(window, monkeypatch):
    def answer(dialog: XYDialog) -> qtw.QDialog.DialogCode:
        assert (dialog.edit_title.text(), dialog.combo_x.currentText(), dialog.combo_y.currentText()) == (
            "XY 1",
            "a",
            "b",
        )
        dialog.spin_trail.setValue(2.0)
        return qtw.QDialog.DialogCode.Accepted

    monkeypatch.setattr(XYDialog, "exec", answer)
    menu_action(window, "Add XY Plot...").trigger()
    plot = window.workspace.views["XY 1"]
    assert isinstance(plot, XYPlot)
    assert (plot.x_model.label, plot.y_model.label, plot.trail) == ("a", "b", 2.0)


def test_add_histogram_from_the_menu(window, monkeypatch):
    def answer(dialog: HistogramDialog) -> qtw.QDialog.DialogCode:
        dialog.combo_signal.setCurrentText("b")
        dialog.spin_bins.setValue(20)
        return qtw.QDialog.DialogCode.Accepted

    monkeypatch.setattr(HistogramDialog, "exec", answer)
    menu_action(window, "Add Histogram...").trigger()
    menu_action(window, "Add Histogram...").trigger()  # The same again: a title of its own
    assert set(window.workspace.views) == {"Histogram: b", "Histogram: b (2)"}
    assert isinstance(window.workspace.views["Histogram: b"], HistogramPlot)


def test_sessions_rebuild_them(qapp, window, tmp_path):
    logic = window.add_logic_plot("L", threshold=2.5)
    window.link(plot="L", label="a")
    window.set_cursors("L", enabled=True)
    logic.move_cursors(0.1, 0.3)
    xy = window.add_xy_plot("XY", "a", "b", trail=1.5)
    xy.set_equal_axes(True)
    xy.set_x_range(False, -1.0, 1.0)
    window.add_histogram("b", bins=12, value_range=(0.0, 4.0), title="Spread")
    path = window.save_session(tmp_path / "s.json")

    restored = Oscilab(app=qapp)
    try:
        restored.load_session(path)
        assert session.capture(restored).plots == session.capture(window).plots
        assert isinstance(restored.workspace.views["L"], LogicPlot)
        assert restored.workspace.views["L"].threshold == 2.5
    finally:
        restored.close()
