"""
The trigger's controls, in the sidebar — laid out like a digitizer's soft
front panel: the trigger (source, type, slope, level, ...), the horizontal
(time per division, reference position), and Run / Single / Stop / Force.

It only edits `TriggerSettings` and asks for commands; the window applies
them (`Workspace.set_trigger`) and shows the acquisition's status here.
"""

from PyQt6 import QtCore as qtc
from PyQt6 import QtWidgets as qtw

from oscilab.trigger import COUPLINGS, SLOPES, TIME_PER_DIV, TRIGGER_TYPES, Slope, TriggerSettings

_SLOPE_TEXT = {"rising": "↗ Rising", "falling": "↘ Falling", "either": "↕ Either"}
_TYPE_TIPS = {
    "immediate": "No condition: a record whenever armed — free running",
    "edge": "When the source crosses the level, on the slope",
    "hysteresis": "An edge that only counts once the source has been the hysteresis beyond the level first",
    "window": "When the source enters, or leaves, the range from low to high",
}
# The rows each trigger type uses (the others are hidden)
_ROWS_FOR = {
    "immediate": set(),
    "edge": {"slope", "level", "coupling", "holdoff"},
    "hysteresis": {"slope", "level", "hysteresis", "coupling", "holdoff"},
    "window": {"low", "high", "condition", "coupling", "holdoff"},
}
_OPTIONAL_ROWS = {"slope", "level", "hysteresis", "low", "high", "condition", "coupling", "holdoff"}


def time_text(seconds: float) -> str:
    """1e-05 -> "10 µs", 0.02 -> "20 ms", 1.0 -> "1 s"."""
    for unit, scale in (("s", 1.0), ("ms", 1e-3), ("µs", 1e-6)):
        if seconds >= scale * 0.999:
            return f"{seconds / scale:g} {unit}"
    return f"{seconds * 1e9:g} ns"


class TriggerPanel(qtw.QGroupBox):
    """The trigger's settings and commands — see the module docstring."""

    changed = qtc.pyqtSignal()  # A setting was edited: read them with `settings()`
    run_requested = qtc.pyqtSignal()
    single_requested = qtc.pyqtSignal()
    stop_requested = qtc.pyqtSignal()
    force_requested = qtc.pyqtSignal()
    autoset_requested = qtc.pyqtSignal()

    def __init__(self, parent: qtw.QWidget | None = None) -> None:
        super().__init__("Trigger:", parent)
        layout = qtw.QVBoxLayout(self)
        self.form = qtw.QFormLayout()
        self._rows: dict[str, int] = {}
        layout.addLayout(self.form)
        self.__build_trigger_rows()
        self.__build_horizontal_rows()
        layout.addLayout(self.__build_commands())
        self.status = qtw.QLabel()
        self.status.setToolTip("Armed: waiting for a trigger · Triggered · Auto: none came, a record anyway · Stopped")
        layout.addWidget(self.status)

        for combo in (self.combo_source, self.combo_type, self.combo_condition, self.combo_coupling):
            combo.currentIndexChanged.connect(self.__edited)
        self.combo_time_div.currentIndexChanged.connect(self.__edited)
        for spin in (self.spin_level, self.spin_hysteresis, self.spin_low, self.spin_high):
            spin.valueChanged.connect(self.__edited)
        for spin in (self.spin_holdoff, self.spin_delay, self.spin_reference):
            spin.valueChanged.connect(self.__edited)
        self.slope_group.buttonClicked.connect(self.__edited)
        self.check_auto.toggled.connect(self.__edited)
        self.set_settings(TriggerSettings())

    def __add_row(self, name: str, label: str, field: qtw.QWidget | qtw.QLayout) -> None:
        self._rows[name] = self.form.rowCount()
        self.form.addRow(label, field)

    def __build_trigger_rows(self) -> None:
        self.combo_source = qtw.QComboBox()
        self.combo_source.setToolTip("The signal whose crossing triggers a record")
        self.__add_row("source", "Source:", self.combo_source)

        self.combo_type = qtw.QComboBox()
        for type_key in TRIGGER_TYPES:
            self.combo_type.addItem(type_key.capitalize(), type_key)
            self.combo_type.setItemData(
                self.combo_type.count() - 1, _TYPE_TIPS[type_key], qtc.Qt.ItemDataRole.ToolTipRole
            )
        self.__add_row("type", "Type:", self.combo_type)

        slope_row = qtw.QHBoxLayout()
        self.slope_group = qtw.QButtonGroup(self)
        self.slope_buttons: dict[Slope, qtw.QPushButton] = {}
        for slope_key in SLOPES:
            button = qtw.QPushButton(_SLOPE_TEXT[slope_key])
            button.setCheckable(True)
            button.setMinimumWidth(0)
            self.slope_group.addButton(button)
            self.slope_buttons[slope_key] = button
            slope_row.addWidget(button)
        self.__add_row("slope", "Slope:", slope_row)

        self.spin_level = self.__value_spin("The level the source crosses")
        self.__add_row("level", "Level:", self.spin_level)
        self.spin_hysteresis = self.__value_spin("How far beyond the level the source must go first", minimum=0.0)
        self.__add_row("hysteresis", "Hysteresis:", self.spin_hysteresis)
        self.spin_low = self.__value_spin("The window's low end")
        self.__add_row("low", "Low:", self.spin_low)
        self.spin_high = self.__value_spin("The window's high end")
        self.__add_row("high", "High:", self.spin_high)
        self.combo_condition = qtw.QComboBox()
        self.combo_condition.addItem("Entering", "entering")
        self.combo_condition.addItem("Leaving", "leaving")
        self.__add_row("condition", "Condition:", self.combo_condition)

        self.combo_coupling = qtw.QComboBox()
        for coupling_key in COUPLINGS:
            self.combo_coupling.addItem(coupling_key.upper(), coupling_key)
        self.combo_coupling.setToolTip("AC takes the source's average (over about a second) off first")
        self.__add_row("coupling", "Coupling:", self.combo_coupling)
        self.spin_holdoff = self.__time_spin("After a trigger, how long before another can count")
        self.__add_row("holdoff", "Holdoff:", self.spin_holdoff)

    def __build_horizontal_rows(self) -> None:
        self.spin_delay = self.__time_spin("How long after the trigger the record's reference point is")
        self.__add_row("delay", "Delay:", self.spin_delay)
        self.combo_time_div = qtw.QComboBox()
        for seconds in TIME_PER_DIV:
            self.combo_time_div.addItem(f"{time_text(seconds)}/div", seconds)
        self.combo_time_div.setToolTip("A record spans ten divisions")
        self.__add_row("timebase", "Timebase:", self.combo_time_div)
        self.spin_reference = qtw.QDoubleSpinBox()
        self.spin_reference.setRange(0.0, 100.0)
        self.spin_reference.setDecimals(0)
        self.spin_reference.setSuffix(" %")
        self.spin_reference.setToolTip("Where the trigger sits in the record: how much of it is before")
        self.__add_row("reference", "Reference:", self.spin_reference)
        self.check_auto = qtw.QCheckBox("Auto trigger")
        self.check_auto.setToolTip("Take a record anyway when no trigger comes for a while — off: Normal")
        self.__add_row("auto", "", self.check_auto)

    def __build_commands(self) -> qtw.QGridLayout:
        buttons = qtw.QGridLayout()
        commands = (
            ("Run", "Acquire record after record", self.run_requested),
            ("Single", "Acquire one record, then stop", self.single_requested),
            ("Stop", "Stop acquiring; the record on show stays", self.stop_requested),
            ("Force", "Take a record now, trigger or not", self.force_requested),
            ("Autoset", "Level and timebase from the source signal", self.autoset_requested),
        )
        self.command_buttons: dict[str, qtw.QPushButton] = {}
        for index, (text, tip, signal) in enumerate(commands):
            button = qtw.QPushButton(text)
            button.setToolTip(tip)
            button.setMinimumWidth(0)
            button.clicked.connect(signal.emit)
            buttons.addWidget(button, index // 3, index % 3)
            self.command_buttons[text] = button
        return buttons

    @staticmethod
    def __value_spin(tip: str, minimum: float = -1e12) -> qtw.QDoubleSpinBox:
        spin = qtw.QDoubleSpinBox()
        spin.setRange(minimum, 1e12)
        spin.setDecimals(4)
        spin.setSingleStep(0.1)
        spin.setToolTip(tip)
        return spin

    @staticmethod
    def __time_spin(tip: str) -> qtw.QDoubleSpinBox:
        spin = qtw.QDoubleSpinBox()
        spin.setRange(0.0, 1e6)
        spin.setDecimals(3)
        spin.setSuffix(" ms")
        spin.setToolTip(tip)
        return spin

    def __edited(self, *_args: object) -> None:
        self.__show_rows_for(self.combo_type.currentData())
        self.changed.emit()

    def __show_rows_for(self, trigger_type: str) -> None:
        """Only the rows the trigger type uses."""
        used = _ROWS_FOR[trigger_type]
        for name in _OPTIONAL_ROWS:
            self.form.setRowVisible(self._rows[name], name in used)

    def row_visible(self, name: str) -> bool:
        """Whether the row `name` ("level", "low", ...) shows for the type chosen."""
        return name not in _OPTIONAL_ROWS or name in _ROWS_FOR[self.combo_type.currentData()]

    def set_signals(self, labels: list[str]) -> None:
        """The signals to trigger on, keeping the one chosen if it's still there."""
        if [self.combo_source.itemText(i) for i in range(self.combo_source.count())] == labels:
            return
        with qtc.QSignalBlocker(self.combo_source):
            current = self.combo_source.currentText()
            self.combo_source.clear()
            self.combo_source.addItems(labels)
            if current in labels:
                self.combo_source.setCurrentText(current)

    def set_unit(self, unit: str) -> None:
        """The source's unit, after the levels."""
        for spin in (self.spin_level, self.spin_hysteresis, self.spin_low, self.spin_high):
            spin.setSuffix(f" {unit}" if unit else "")

    def settings(self) -> TriggerSettings:
        """The settings as they stand in the panel."""
        slope = next(key for key, button in self.slope_buttons.items() if button.isChecked())
        return TriggerSettings(
            source=self.combo_source.currentText(),
            type=self.combo_type.currentData(),
            slope=slope,
            level=self.spin_level.value(),
            hysteresis=self.spin_hysteresis.value(),
            low=self.spin_low.value(),
            high=self.spin_high.value(),
            condition=self.combo_condition.currentData(),
            coupling=self.combo_coupling.currentData(),
            holdoff=self.spin_holdoff.value() / 1000,
            delay=self.spin_delay.value() / 1000,
            time_per_div=self.combo_time_div.currentData(),
            reference=self.spin_reference.value() / 100,
            auto=self.check_auto.isChecked(),
        )

    def set_settings(self, settings: TriggerSettings) -> None:
        """Show `settings`, without announcing them as edits."""
        widgets: list[qtc.QObject] = [
            self.combo_source, self.combo_type, self.combo_condition, self.combo_coupling, self.combo_time_div,
            self.spin_level, self.spin_hysteresis, self.spin_low, self.spin_high, self.spin_holdoff,
            self.spin_delay, self.spin_reference, self.check_auto, self.slope_group,
        ]  # fmt: skip
        blockers = [qtc.QSignalBlocker(widget) for widget in widgets]
        if settings.source:
            self.combo_source.setCurrentText(settings.source)
        self.combo_type.setCurrentIndex(self.combo_type.findData(settings.type))
        self.slope_buttons[settings.slope].setChecked(True)
        self.spin_level.setValue(settings.level)
        self.spin_hysteresis.setValue(settings.hysteresis)
        self.spin_low.setValue(settings.low)
        self.spin_high.setValue(settings.high)
        self.combo_condition.setCurrentIndex(self.combo_condition.findData(settings.condition))
        self.combo_coupling.setCurrentIndex(self.combo_coupling.findData(settings.coupling))
        self.spin_holdoff.setValue(settings.holdoff * 1000)
        self.spin_delay.setValue(settings.delay * 1000)
        nearest = min(TIME_PER_DIV, key=lambda step: abs(step - settings.time_per_div))
        self.combo_time_div.setCurrentIndex(self.combo_time_div.findData(nearest))
        self.spin_reference.setValue(settings.reference * 100)
        self.check_auto.setChecked(settings.auto)
        del blockers
        self.__show_rows_for(settings.type)

    def set_status(self, text: str, color: str) -> None:
        self.status.setText(f"<span style='color: {color}'>●</span> {text}")
