"""
Plots from the GUI: the Plots menu adds them, each plot's panel changes its
signals or removes it. Dialogs are answered by patching their exec().
"""

import numpy as np
import pytest
from PyQt6 import QtWidgets as qtw

from oscilab import plotting, session
from oscilab.dialogs import HistogramDialog, MathDialog, PlotDialog, SpectrogramDialog, XYDialog
from oscilab.oscilab import Oscilab
from oscilab.session import TimeseriesPlotSpec


@pytest.fixture
def window(qapp):
    scope = Oscilab(app=qapp)
    for label in ("a", "b", "c"):
        scope.register_signal(label, fs=1_000).add_data(np.arange(10) / 1_000, np.zeros(10))
    yield scope
    scope.close()


def answer(monkeypatch, dialog_cls, fill=lambda _dialog: None, accept: bool = True):
    """Make `dialog_cls` return right away, after `fill(dialog)` sets its fields."""

    def exec_(dialog):
        fill(dialog)
        return qtw.QDialog.DialogCode.Accepted if accept else qtw.QDialog.DialogCode.Rejected

    monkeypatch.setattr(dialog_cls, "exec", exec_)


def menu_action(window: Oscilab, title: str, text: str):
    menu = next(a.menu() for a in window.menuBar().actions() if a.text() == title)
    return next(a for a in menu.actions() if a.text() == text)


def panel_button(window: Oscilab, title: str, text: str) -> qtw.QPushButton:
    panel = window.workspace.controllers[title]
    return next(b for b in panel.findChildren(qtw.QPushButton) if b.text() == text)


def add_plot(window, monkeypatch, title: str, signals: list[str]):
    def fill(dialog: PlotDialog):
        dialog.edit_title.setText(title)
        dialog.set_checked(signals)

    answer(monkeypatch, PlotDialog, fill)
    menu_action(window, "Plots", "Add Time Series Plot...").trigger()


def test_add_a_time_series_plot_from_the_menu(window, monkeypatch):
    add_plot(window, monkeypatch, "Sensors", ["a", "c"])

    plot = window.workspace.plots["Sensors"]
    assert list(plot.curves) == ["a", "c"]
    assert window.workspace.controllers["Sensors"].title() == "Plot: Sensors"
    assert [a.text() for a in window.cursors_menu.actions()] == ["Sensors"]


def test_the_title_is_suggested_and_checked(window, monkeypatch):
    suggested = []
    answer(monkeypatch, PlotDialog, lambda dialog: suggested.append(dialog.edit_title.text()), accept=False)
    menu_action(window, "Plots", "Add Time Series Plot...").trigger()
    assert suggested == ["Plot 1"]

    add_plot(window, monkeypatch, "", ["a"])  # Empty
    add_plot(window, monkeypatch, "P", ["a"])
    add_plot(window, monkeypatch, "P", ["b"])  # Duplicate
    assert list(window.workspace.views) == ["P"]
    assert list(window.workspace.plots["P"].curves) == ["a"]


def test_change_a_plots_signals_from_its_panel(window, monkeypatch):
    add_plot(window, monkeypatch, "P", ["a", "b"])

    answer(monkeypatch, PlotDialog, lambda dialog: dialog.set_checked(["b", "c"]))
    panel_button(window, "P", "Signals...").click()

    assert list(window.workspace.plots["P"].curves) == ["b", "c"]
    assert "a" not in window.workspace.signal_list.rows  # Shown nowhere now


def test_remove_a_plot_from_its_panel(window, monkeypatch, qapp):
    add_plot(window, monkeypatch, "P", ["a"])
    panel_button(window, "P", "Remove").click()
    qapp.processEvents()

    assert "P" not in window.workspace.views
    assert window.cursors_menu.actions() == []
    assert "a" in window.workspace.signals


def test_add_and_remove_a_spectrogram(window, monkeypatch, qapp):
    def fill(dialog: SpectrogramDialog):
        dialog.combo_signal.setCurrentText("b")
        dialog.combo_window.setCurrentText("512")

    answer(monkeypatch, SpectrogramDialog, fill)
    menu_action(window, "Plots", "Add Spectrogram...").trigger()

    spectrogram = window.workspace.spectrograms["b"]
    assert (spectrogram.nperseg, spectrogram.noverlap) == (512, 256)

    offered: list[str] = []
    answer(
        monkeypatch,
        SpectrogramDialog,
        lambda d: offered.extend(d.combo_signal.itemText(i) for i in range(d.combo_signal.count())),
        accept=False,
    )
    menu_action(window, "Plots", "Add Spectrogram...").trigger()
    assert offered == ["a", "c"]  # "b" has one already

    panel_button(window, "b", "Remove").click()
    qapp.processEvents()
    assert "b" not in window.workspace.views


def test_a_setup_built_in_the_gui_saves_as_a_session(window, monkeypatch):
    add_plot(window, monkeypatch, "Built", ["a", "b"])
    answer(monkeypatch, PlotDialog, lambda dialog: dialog.set_checked(["b"]))
    panel_button(window, "Built", "Signals...").click()

    (plot,) = session.capture(window).plots
    assert isinstance(plot, TimeseriesPlotSpec)
    assert (plot.title, plot.signals) == ("Built", ["b"])


# --- what's left of the Plots and Math menus, and the plots' own buttons -----------------------------


@pytest.fixture
def logged(window, monkeypatch) -> list[str]:
    """What the window said in its status bar."""
    messages: list[str] = []
    monkeypatch.setattr(window.statusBar(), "showMessage", lambda text, _timeout=0: messages.append(text))
    return messages


def test_add_a_logic_plot_from_the_menu(window, monkeypatch):
    def fill(dialog: PlotDialog):
        dialog.set_checked(["a"])

    answer(monkeypatch, PlotDialog, fill)
    menu_action(window, "Plots", "Add Logic Plot...").trigger()
    assert window.workspace.plots["Logic 1"].signals == ["a"]


def test_add_an_xy_plot_from_the_menu(window, monkeypatch):
    def fill(dialog: XYDialog):
        dialog.combo_x.setCurrentText("a")
        dialog.combo_y.setCurrentText("b")

    answer(monkeypatch, XYDialog, fill)
    menu_action(window, "Plots", "Add XY Plot...").trigger()
    assert window.workspace.views["XY 1"].signals == ["a", "b"]


def test_an_xy_plot_of_one_signal_against_itself_is_refused(window, monkeypatch, logged):
    def fill(dialog: XYDialog):
        dialog.combo_x.setCurrentText("a")
        dialog.combo_y.setCurrentText("a")

    answer(monkeypatch, XYDialog, fill)
    menu_action(window, "Plots", "Add XY Plot...").trigger()
    assert "XY 1" not in window.workspace.views
    assert logged[-1].endswith("An XY plot needs two different signals")


def test_two_histograms_of_one_signal_are_numbered(window, monkeypatch):
    answer(monkeypatch, HistogramDialog)  # The first signal, "a"
    for _ in range(2):
        menu_action(window, "Plots", "Add Histogram...").trigger()
    assert {"Histogram: a", "Histogram: a (2)"} <= set(window.workspace.views)


def test_a_histogram_range_from_high_to_low_is_refused(window, monkeypatch, logged):
    def fill(dialog: HistogramDialog):
        dialog.check_auto.setChecked(False)
        dialog.spin_min.setValue(5.0)
        dialog.spin_max.setValue(1.0)

    answer(monkeypatch, HistogramDialog, fill)
    menu_action(window, "Plots", "Add Histogram...").trigger()
    assert "Histogram: a" not in window.workspace.views
    assert "must go from low to high" in logged[-1]


@pytest.mark.parametrize(
    ("entry", "warning"),
    [
        ("Add XY Plot...", "An XY plot needs two signals"),
        ("Add Histogram...", "No signals yet"),
        ("Add Spectrogram...", "No signals yet"),
    ],
)
def test_menus_without_the_signals_they_need_say_so(qapp, monkeypatch, entry, warning):
    scope = Oscilab(app=qapp)
    messages: list[str] = []
    monkeypatch.setattr(scope.statusBar(), "showMessage", lambda text, _timeout=0: messages.append(text))
    menu_action(scope, "Plots", entry).trigger()
    assert messages[-1].endswith(warning)
    scope.close()


def test_once_every_signal_has_a_spectrogram_there_are_no_more(window, logged):
    for label in ("a", "b", "c"):
        window.add_spectrogram_plot(label)
    menu_action(window, "Plots", "Add Spectrogram...").trigger()
    assert logged[-1].endswith("Every signal already has a spectrogram")


def math(window, monkeypatch, operator: str, name: str | None = None, accept: bool = True) -> None:
    def fill(dialog: MathDialog):
        dialog.combo_a.setCurrentText("a")
        dialog.combo_b.setCurrentText("b")
        if name is not None:
            dialog.edit_label.setText(name)

    answer(monkeypatch, MathDialog, fill, accept=accept)
    menu_action(window, "Math", operator).trigger()


def test_a_math_signal_from_the_menu_is_shown_on_the_plot_picked(window, monkeypatch):
    add_plot(window, monkeypatch, "P", ["a"])
    math(window, monkeypatch, "-")
    assert window.workspace.math_signals["a-b"].operator == "-"
    assert "a-b" in window.workspace.plots["P"].curves


@pytest.mark.parametrize(("name", "warning"), [("", "Math signal needs a name"), ("c", "Signal 'c' already exists")])
def test_a_math_signal_needs_a_new_name(window, monkeypatch, logged, name, warning):
    add_plot(window, monkeypatch, "P", ["a"])
    math(window, monkeypatch, "+", name=name)
    assert not window.workspace.math_signals
    assert logged[-1].endswith(warning)


def test_a_cancelled_math_dialog_adds_nothing(window, monkeypatch):
    add_plot(window, monkeypatch, "P", ["a"])
    math(window, monkeypatch, "*", accept=False)
    assert not window.workspace.math_signals


def test_math_needs_a_plot_to_show_it_on(window, monkeypatch, logged):
    math(window, monkeypatch, "/")
    assert logged[-1].endswith("Add a plot before creating a math signal")


def test_math_needs_two_signals(qapp, monkeypatch):
    scope = Oscilab(app=qapp)
    messages: list[str] = []
    monkeypatch.setattr(scope.statusBar(), "showMessage", lambda text, _timeout=0: messages.append(text))
    menu_action(scope, "Math", "+").trigger()
    assert messages[-1].endswith("Need at least two signals to do math")
    scope.close()


def test_a_logic_plots_threshold_button(window, monkeypatch):
    window.add_logic_plot("L")
    monkeypatch.setattr(qtw.QInputDialog, "getDouble", lambda *_args: (2.5, True))
    panel_button(window, "L", "Threshold...").click()
    assert window.workspace.plots["L"].threshold == 2.5


def test_an_xy_plots_x_axis_and_equal_axes_buttons(window, monkeypatch):
    plot = window.add_xy_plot("XY", "a", "b")
    monkeypatch.setattr(plotting, "_ask_range", lambda *_args, **_kwargs: (False, -1e-6, 1e-6))
    panel_button(window, "XY", "X-Axis").click()
    assert (plot.x_range_auto, plot.x_range_min, plot.x_range_max) == (False, -1e-6, 1e-6)

    panel_button(window, "XY", "Equal Axes").click()
    assert plot.equal_axes


def test_a_spectrograms_color_button(window, monkeypatch):
    spectrogram = window.add_spectrogram_plot("a")
    monkeypatch.setattr(plotting, "_ask_range", lambda *_args, **_kwargs: (False, -90.0, -10.0))
    panel_button(window, "a", "Color").click()
    assert (spectrogram.color_levels_auto, spectrogram.color_levels_min) == (False, -90.0)


def test_a_histograms_bins_button(window, monkeypatch):
    plot = window.add_histogram("a")

    def fill(dialog: HistogramDialog):
        dialog.spin_bins.setValue(7)

    answer(monkeypatch, HistogramDialog, fill)
    panel_button(window, "Histogram: a", "Bins...").click()
    assert plot.bins == 7
