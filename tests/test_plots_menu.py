"""
Plots from the GUI: the Plots menu adds them, each plot's panel changes its
signals or removes it. Dialogs are answered by patching their exec().
"""

import numpy as np
import pytest
from PyQt6 import QtWidgets as qtw

from oscilab import session
from oscilab.dialogs import PlotDialog, SpectrogramDialog
from oscilab.oscilab import Oscilab


@pytest.fixture
def window(qapp):
    oszi = Oscilab(app=qapp)
    for label in ("a", "b", "c"):
        oszi.register_signal(label, fs=1_000).add_data(np.arange(10) / 1_000, np.zeros(10))
    yield oszi
    oszi.close()


def answer(monkeypatch, dialog_cls, fill=lambda _dialog: None, accept: bool = True):
    """Make `dialog_cls` return right away, after `fill(dialog)` sets its fields."""

    def exec_(dialog):
        fill(dialog)
        return qtw.QDialog.DialogCode.Accepted if accept else qtw.QDialog.DialogCode.Rejected

    monkeypatch.setattr(dialog_cls, "exec", exec_)


def menu_action(window: Oscilab, menu: str, text: str):
    menu = next(a.menu() for a in window.menuBar().actions() if a.text() == menu)
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

    offered = []
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

    plots = session.capture(window).plots
    assert [(p.title, p.signals) for p in plots] == [("Built", ["b"])]
