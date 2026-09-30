"""
The trigger in the window: the toolbar's Trigger button and drop-down, the
sidebar panel, and sessions.
"""

import numpy as np
import pytest
from pydantic import ValidationError
from PyQt6 import QtWidgets as qtw

from oscilab.oscilab import Oscilab
from oscilab.session import Session
from oscilab.trigger import TriggerSettings

FS = 1_000.0


@pytest.fixture
def window(qapp):
    scope = Oscilab(app=qapp)
    scope.register_signal("s", fs=FS, unit="V")
    scope.add_timeseries_plot("P")
    scope.link(plot="P", label="s")
    scope.n = 0  # type: ignore[attr-defined]  # Samples fed so far
    feed(scope, 1.0)
    yield scope
    scope.close()


def feed(window: Oscilab, seconds: float, chunk: float = 0.02) -> None:
    """A 5 Hz sine from 1 V to 5 V, a chunk at a time, each followed by a refresh tick."""
    model = window.workspace.signals["s"]
    for _ in range(round(seconds / chunk)):
        t = (window.n + np.arange(round(chunk * FS))) / FS  # type: ignore[attr-defined]
        window.n += t.size  # type: ignore[attr-defined]
        model.add_data(t, 3 + 2 * np.sin(2 * np.pi * 5 * t))
        window.refresh_timer.timeout.emit()


def toolbar_menu(window: Oscilab) -> list[str]:
    button = window.toolbar.widgetForAction(window.trigger_action)
    return [a.text() for a in button.menu().actions()]


def test_turning_it_on_sets_itself_up_on_the_signal(window):
    assert window.trigger_panel.isHidden()
    window.trigger_action.trigger()

    settings = window.trigger_settings
    assert settings.source == "s"
    assert settings.level == pytest.approx(3.0, abs=0.05)  # Autoset: mid signal
    assert settings.time_per_div == 0.05  # ~two periods of 5 Hz a record
    assert not window.trigger_panel.isHidden()
    assert window.trigger_panel.spin_level.suffix() == " V"
    assert toolbar_menu(window) == ["Run", "Single", "Stop", "Force Trigger", "Autoset"]

    feed(window, 0.8)
    assert window.workspace.plots["P"].showing_record
    assert window.trigger_action.text() == "Trigger: Triggered"


def test_no_signals_no_trigger(qapp):
    scope = Oscilab(app=qapp)
    try:
        scope.trigger_action.trigger()
        assert not scope.trigger_action.isChecked()
        assert scope.workspace.trigger is None
    finally:
        scope.close()


def test_the_panel_shows_the_rows_its_type_uses_and_applies_edits(window):
    window.trigger_action.trigger()
    panel = window.trigger_panel
    panel.combo_type.setCurrentIndex(panel.combo_type.findData("window"))

    assert window.trigger_settings.type == "window"
    assert [panel.row_visible(row) for row in ("level", "low", "high", "condition")] == [False, True, True, True]
    panel.spin_high.setValue(4.0)
    assert window.trigger_settings.high == 4.0


def test_impossible_edits_are_turned_away(window, monkeypatch):
    shown = []
    monkeypatch.setattr(window.statusBar(), "showMessage", lambda text, _timeout=0: shown.append(text))
    window.trigger_action.trigger()
    panel = window.trigger_panel
    panel.combo_time_div.setCurrentIndex(panel.combo_time_div.findData(1.0))  # 10 s records: the buffer holds 10 s
    assert "doesn't fit" not in shown[-1]  # Exactly fits
    window.set_duration(5)
    panel.combo_time_div.setCurrentIndex(panel.combo_time_div.findData(0.5))
    panel.combo_time_div.setCurrentIndex(panel.combo_time_div.findData(1.0))
    assert "doesn't fit the 5 s" in shown[-1]
    assert window.trigger_settings.time_per_div == 0.5  # Kept the last that worked


def test_single_takes_one_record_and_stops(window):
    window.trigger_action.trigger()
    window.trigger_panel.command_buttons["Single"].click()
    feed(window, 0.8)
    assert window.trigger_action.text() == "Trigger: Stopped"
    held = window.workspace.plots["P"].curves["s"].getData()[1].copy()
    feed(window, 0.8)
    np.testing.assert_allclose(window.workspace.plots["P"].curves["s"].getData()[1], held)  # Held


def test_the_drop_down_commands(window):
    window.trigger_action.trigger()
    commands = {a.text(): a for a in window.toolbar.widgetForAction(window.trigger_action).menu().actions()}
    commands["Stop"].trigger()
    assert window.workspace.trigger.state == "stopped"
    commands["Run"].trigger()
    assert window.workspace.trigger.state == "armed"
    commands["Force Trigger"].trigger()
    feed(window, 0.02)
    assert window.workspace.trigger.last == "forced"


def test_dragging_the_level_line_shows_in_the_panel(window):
    window.trigger_action.trigger()
    (line,) = window.workspace.plots["P"]._level_lines
    line.setValue(4.5)
    line.sigPositionChangeFinished.emit(line)
    assert window.trigger_panel.spin_level.value() == pytest.approx(4.5)


def test_turning_it_off_goes_back_to_scrolling(window):
    window.trigger_action.trigger()
    feed(window, 0.8)
    window.trigger_action.trigger()
    assert window.workspace.trigger is None
    assert window.trigger_panel.isHidden()
    assert not window.workspace.plots["P"].showing_record
    assert window.trigger_action.text() == "Trigger"


def test_losing_the_source_turns_it_off(window):
    window.trigger_action.trigger()
    window.workspace.remove_signal("s")
    window.refresh_timer.timeout.emit()
    assert not window.trigger_action.isChecked()
    assert window.trigger_panel.isHidden()


def test_sessions_save_the_trigger(qapp, window, tmp_path):
    window.set_trigger(
        TriggerSettings(source="s", type="hysteresis", level=2.5, hysteresis=0.4, time_per_div=0.02, auto=False)
    )
    path = window.save_session(tmp_path / "s.json")

    restored = Oscilab(app=qapp)
    try:
        restored.load_session(path)
        assert restored.trigger_settings == window.trigger_settings
        assert restored.trigger_action.isChecked()
        assert restored.workspace.trigger.state == "armed"  # Running again
    finally:
        restored.close()


def test_a_session_triggering_on_a_signal_it_lacks_is_refused():
    with pytest.raises(ValidationError, match="trigger's source 'ghost'"):
        Session.model_validate({"trigger": {"source": "ghost"}})


def test_the_panel_is_atop_the_sidebar(window):
    window.trigger_action.trigger()
    assert window.controller_layout.itemAt(0).widget() is window.trigger_panel
    assert isinstance(window.trigger_panel, qtw.QGroupBox)
