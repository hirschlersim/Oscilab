"""
The dialogs' fields: what they're pre-filled with and what they give back —
and NumberBox, the field for values of any size.
"""

import pytest
from PyQt6 import QtGui as qtg
from PyQt6 import QtTest as qtt
from PyQt6 import QtWidgets as qtw

from oscilab.dialogs import FilterDialog, MathDialog, NumberBox, RangeDialog, ReplayDialog, ScalingDialog
from oscilab.signal_controller import SignalController
from oscilab.signal_model import SignalModel

pytestmark = pytest.mark.usefixtures("qapp")


# --- NumberBox ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "shown"),
    [(2.98e-7, "2.98e-07"), (0.00025, "0.00025"), (1.6e7, "1.6e+07"), (-3.5, "-3.5"), (0.0, "0")],
)
def test_a_number_box_shows_values_of_any_size(value, shown):
    # Fixed decimals rounded a 24-bit ADC's gain (about 3e-7) to 0
    box = NumberBox()
    box.setValue(value)
    assert box.text() == shown
    assert box.value() == value


def test_a_number_box_takes_scientific_notation_and_a_unit():
    box = NumberBox()
    box.setSuffix(" V")
    box.show()
    box.lineEdit().selectAll()
    qtt.QTest.keyClicks(box, "4.7e-9")  # type: ignore[call-arg, arg-type]  # Static in PyQt6, not in its stubs
    box.interpretText()
    assert box.value() == 4.7e-9
    assert box.text() == "4.7e-09 V"
    box.hide()


@pytest.mark.parametrize(
    ("text", "state"),
    [
        ("-", qtg.QValidator.State.Intermediate),  # On the way to a number
        ("2.5e-", qtg.QValidator.State.Intermediate),
        ("abc", qtg.QValidator.State.Invalid),
        ("1e-6", qtg.QValidator.State.Acceptable),
        ("1e20", qtg.QValidator.State.Intermediate),  # Out of range
    ],
)
def test_a_number_box_lets_a_number_be_typed(text, state):
    assert NumberBox().validate(text, 0)[0] == state


def test_a_number_box_keeps_its_value_for_text_that_isnt_one():
    box = NumberBox()
    box.setValue(1.5)
    assert box.valueFromText("abc") == 1.5


# --- the dialogs -------------------------------------------------------------------------------


def test_the_range_dialog_takes_microvolts():
    dialog = RangeDialog("Y-Axis", current_auto=False, current_min=-2.5e-6, current_max=2.5e-6, unit="V")
    assert dialog.get_values() == (False, -2.5e-6, 2.5e-6)


def test_the_scaling_dialog_fields_follow_its_toggle():
    dialog = ScalingDialog("s", scaling_enabled=False, current_gain=2.98e-7, current_offset=-8388608.0)
    assert not dialog.spin_gain.isEnabled()
    dialog.chk_enabled.setChecked(True)
    assert dialog.spin_gain.isEnabled()
    assert dialog.get_values() == (True, 2.98e-7, -8388608.0)


def test_the_filter_dialog_without_a_sampling_rate():
    dialog = FilterDialog("s", filter_enabled=True, current_cutoff=500.0, current_order=2)
    assert dialog.get_values() == (True, 500.0, 2)


def test_the_math_dialog_suggests_a_name():
    dialog = MathDialog("*", signal_labels=["v", "i"], plot_titles=["P"])
    assert dialog.get_values() == ("v", "i", "v*i", "P")
    dialog.combo_a.setCurrentText("i")
    assert dialog.edit_label.text() == "i*i"


def test_the_replay_dialog_says_why_a_path_wont_do():
    def scan(path: str) -> tuple[list[str], list[str]]:
        raise ValueError(f"No recordings in {path}")

    dialog = ReplayDialog("C:/nothing here", scan)
    assert dialog.files_label.text() == "No recordings in C:/nothing here"
    assert dialog.signal_list.count() == 0


# --- a signal row's Filter and Scaling dialogs ------------------------------------------------------


@pytest.fixture
def row():
    controller = SignalController(title="Signals:")
    row = controller.add_signal(label="s", model=SignalModel(label="s", fs=100.0), derived=False, has_curve=True)
    row.set_expanded(True)  # Its details, with the Filter and Scaling buttons
    yield row
    controller.deleteLater()


def accept_with(monkeypatch, dialog_type, set_up) -> None:
    """Have `dialog_type` accepted at once, its fields set by `set_up(dialog)`."""

    def exec_(dialog) -> qtw.QDialog.DialogCode:
        set_up(dialog)
        return qtw.QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialog_type, "exec", exec_)


def test_the_rows_filter_dialog_turns_the_filter_on(row, monkeypatch):
    def set_up(dialog: FilterDialog) -> None:
        assert dialog.spin_cutoff.maximum() == pytest.approx(49.99)  # Just below Nyquist
        dialog.chk_enabled.setChecked(True)
        dialog.spin_cutoff.setValue(10.0)

    accept_with(monkeypatch, FilterDialog, set_up)
    row.filter_button.click()
    assert row.filter_settings == {"enabled": True, "cutoff": 10.0, "order": 4}
    assert row.model.sos is not None


def test_the_rows_scaling_dialog_scales_the_signal(row, monkeypatch):
    def set_up(dialog: ScalingDialog) -> None:
        dialog.chk_enabled.setChecked(True)
        dialog.spin_gain.setValue(2.98e-7)
        dialog.spin_offset.setValue(100.0)

    accept_with(monkeypatch, ScalingDialog, set_up)
    row.scaling_button.click()
    row.model.add_data([0.0], [100.0 + 1e7])
    assert row.model.snapshot()[1][0] == pytest.approx(2.98)


def test_cancelling_a_rows_dialog_changes_nothing(row, monkeypatch):
    monkeypatch.setattr(ScalingDialog, "exec", lambda _self: qtw.QDialog.DialogCode.Rejected)
    row.scaling_button.click()
    assert row.model.gain is None
    assert not row.scaling_settings["enabled"]


def test_a_rows_color_button_picks_the_curves_color(row, monkeypatch):
    colors: list[str] = []
    row.color_changed.connect(lambda _label, color: colors.append(color))
    monkeypatch.setattr(qtw.QColorDialog, "getColor", lambda *_args: qtg.QColor("#123456"))
    row.color_button.click()
    assert colors == ["#123456"]


def test_cancelling_the_color_dialog_keeps_the_color(row, monkeypatch):
    colors: list[str] = []
    row.color_changed.connect(lambda _label, color: colors.append(color))
    monkeypatch.setattr(qtw.QColorDialog, "getColor", lambda *_args: qtg.QColor())  # Invalid: cancelled
    row.color_button.click()
    assert colors == []


def test_turning_filter_and_scaling_off_again(row):
    row.apply_filter(enabled=True, cutoff=10.0, order=2)
    row.apply_scaling(enabled=True, gain=2.0, offset=1.0)
    row.apply_filter(enabled=False, cutoff=10.0, order=2)
    row.apply_scaling(enabled=False, gain=2.0, offset=1.0)
    assert row.model.sos is None
    assert row.model.gain is None
