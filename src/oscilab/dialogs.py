"""
Modal dialogs.

All share one shape: pre-filled with the values currently in effect, OK or
Cancel, and `get_values()` to read back what was entered. The
Filter/Scaling/Range ones put their fields behind an enable (or "Auto")
checkbox, so turning a setting off doesn't lose what was typed in.
"""

from PyQt6 import QtWidgets as qtw


class RangeDialog(qtw.QDialog):
    """
    Manual Auto/Min/Max range entry, generic over a unit label — used for a
    plot's Y-axis and a spectrogram's frequency axis and colormap levels.
    """

    def __init__(
        self,
        title: str,
        current_auto: bool = True,
        current_min: float = 0.0,
        current_max: float = 1.0,
        unit: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(250)

        layout = qtw.QVBoxLayout(self)

        # Auto toggle
        self.chk_auto = qtw.QCheckBox("Auto")
        self.chk_auto.setToolTip(
            "On: the range continuously fits to the data, like today.\n"
            "Off: pin an exact range that stays put — Reset View won't undo it."
        )
        self.chk_auto.setChecked(current_auto)
        self.chk_auto.toggled.connect(self._on_toggle)
        layout.addWidget(self.chk_auto)

        suffix = f" ({unit})" if unit else ""

        # Min
        min_layout = qtw.QHBoxLayout()
        min_layout.addWidget(qtw.QLabel(f"Min{suffix}:"))
        self.spin_min = qtw.QDoubleSpinBox()
        self.spin_min.setRange(-1e6, 1e6)
        self.spin_min.setDecimals(3)
        self.spin_min.setSingleStep(0.1)
        self.spin_min.setValue(current_min)
        self.spin_min.setToolTip("Lower bound of the manual range.")
        min_layout.addWidget(self.spin_min)
        layout.addLayout(min_layout)

        # Max
        max_layout = qtw.QHBoxLayout()
        max_layout.addWidget(qtw.QLabel(f"Max{suffix}:"))
        self.spin_max = qtw.QDoubleSpinBox()
        self.spin_max.setRange(-1e6, 1e6)
        self.spin_max.setDecimals(3)
        self.spin_max.setSingleStep(0.1)
        self.spin_max.setValue(current_max)
        self.spin_max.setToolTip("Upper bound of the manual range.")
        max_layout.addWidget(self.spin_max)
        layout.addLayout(max_layout)

        # OK / Cancel buttons
        button_box = qtw.QDialogButtonBox(
            qtw.QDialogButtonBox.StandardButton.Ok | qtw.QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # Initial state
        self._on_toggle(current_auto)

    def _on_toggle(self, checked: bool) -> None:
        """Enable/disable the min/max fields based on the Auto toggle."""
        self.spin_min.setEnabled(not checked)
        self.spin_max.setEnabled(not checked)

    def get_values(self) -> tuple[bool, float, float]:
        """Return (auto, min, max)."""
        return self.chk_auto.isChecked(), self.spin_min.value(), self.spin_max.value()


class FilterDialog(qtw.QDialog):
    """Low-pass Butterworth settings; the cutoff is capped just below Nyquist."""

    def __init__(
        self,
        label: str,
        filter_enabled: bool = False,
        current_cutoff: float = 100.0,
        current_order: int = 4,
        sampling_rate: float | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Filter | {label}")
        self.setMinimumWidth(250)

        layout = qtw.QVBoxLayout(self)

        # Enable toggle
        self.chk_enabled = qtw.QCheckBox("Filter ON")
        self.chk_enabled.setToolTip("Enable or disable the filter for this signal")
        self.chk_enabled.setChecked(filter_enabled)
        self.chk_enabled.toggled.connect(self._on_toggle)
        layout.addWidget(self.chk_enabled)

        # Cutoff frequency
        cutoff_layout = qtw.QHBoxLayout()
        cutoff_layout.addWidget(qtw.QLabel("Cutoff [Hz]:"))
        self.spin_cutoff = qtw.QDoubleSpinBox()
        self.spin_cutoff.setDecimals(2)
        # Butterworth design needs 0 < cutoff < Nyquist; cap the field just
        # below it so an invalid cutoff can't be entered in the first place.
        max_cutoff = sampling_rate / 2.0 - 0.01 if sampling_rate is not None else 1e6
        self.spin_cutoff.setRange(0.01, max_cutoff)
        self.spin_cutoff.setSingleStep(1.0)
        self.spin_cutoff.setValue(current_cutoff)
        self.spin_cutoff.setToolTip("Low-pass cutoff frequency — content above this is attenuated")
        cutoff_layout.addWidget(self.spin_cutoff)
        layout.addLayout(cutoff_layout)

        # Order
        order_layout = qtw.QHBoxLayout()
        order_layout.addWidget(qtw.QLabel("Order:"))
        self.spin_order = qtw.QSpinBox()
        self.spin_order.setRange(1, 20)
        self.spin_order.setValue(current_order)
        self.spin_order.setToolTip("Filter steepness — higher rolls off more sharply but adds more delay")
        order_layout.addWidget(self.spin_order)
        layout.addLayout(order_layout)

        # Info label
        if sampling_rate is not None:
            nyquist = sampling_rate / 2.0
            info_text = f"Low-pass Butterworth | Nyquist: {nyquist:.1f} Hz"
        else:
            info_text = "Low-pass Butterworth | Sampling rate unknown"
        info_label = qtw.QLabel(info_text)
        info_label.setStyleSheet("font-size: 9pt; color: gray;")
        layout.addWidget(info_label)

        # OK / Cancel buttons
        button_box = qtw.QDialogButtonBox(
            qtw.QDialogButtonBox.StandardButton.Ok | qtw.QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # Initial state
        self._on_toggle(filter_enabled)

    def _on_toggle(self, checked: bool) -> None:
        """Enable/disable the cutoff and order fields based on toggle."""
        self.spin_cutoff.setEnabled(checked)
        self.spin_order.setEnabled(checked)

    def get_values(self) -> tuple[bool, float, int]:
        """Return (enabled, cutoff Hz, order)."""
        return self.chk_enabled.isChecked(), self.spin_cutoff.value(), self.spin_order.value()


class ScalingDialog(qtw.QDialog):
    """Gain/offset settings: output = (raw - offset) * gain."""

    def __init__(
        self,
        label: str,
        scaling_enabled: bool = False,
        current_gain: float = 1.0,
        current_offset: float = 0.0,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Scaling | {label}")
        self.setMinimumWidth(250)

        layout = qtw.QVBoxLayout(self)

        # Enable toggle
        self.chk_enabled = qtw.QCheckBox("Scaling ON")
        self.chk_enabled.setToolTip("Enable or disable scaling for this signal")
        self.chk_enabled.setChecked(scaling_enabled)
        self.chk_enabled.toggled.connect(self._on_toggle)
        layout.addWidget(self.chk_enabled)

        # Gain
        gain_layout = qtw.QHBoxLayout()
        gain_layout.addWidget(qtw.QLabel("Gain:"))
        self.spin_gain = qtw.QDoubleSpinBox()
        self.spin_gain.setRange(-1e6, 1e6)
        self.spin_gain.setDecimals(6)
        self.spin_gain.setSingleStep(0.1)
        self.spin_gain.setValue(current_gain)
        self.spin_gain.setToolTip("Multiplier applied after subtracting the offset")
        gain_layout.addWidget(self.spin_gain)
        layout.addLayout(gain_layout)

        # Offset
        offset_layout = qtw.QHBoxLayout()
        offset_layout.addWidget(qtw.QLabel("Offset:"))
        self.spin_offset = qtw.QDoubleSpinBox()
        self.spin_offset.setRange(-1e6, 1e6)
        self.spin_offset.setDecimals(6)
        self.spin_offset.setSingleStep(0.1)
        self.spin_offset.setValue(current_offset)
        self.spin_offset.setToolTip("Subtracted from the raw value before applying gain")
        offset_layout.addWidget(self.spin_offset)
        layout.addLayout(offset_layout)

        # Info label
        info_label = qtw.QLabel("Formula: output = (raw - offset) * gain")
        info_label.setStyleSheet("font-size: 9pt; color: gray;")
        layout.addWidget(info_label)

        # OK / Cancel buttons
        button_box = qtw.QDialogButtonBox(
            qtw.QDialogButtonBox.StandardButton.Ok | qtw.QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # Initial state
        self._on_toggle(scaling_enabled)

    def _on_toggle(self, checked: bool) -> None:
        """Enable/disable the gain/offset fields based on toggle."""
        self.spin_gain.setEnabled(checked)
        self.spin_offset.setEnabled(checked)

    def get_values(self) -> tuple[bool, float, float]:
        """Return (enabled, gain, offset)."""
        return self.chk_enabled.isChecked(), self.spin_gain.value(), self.spin_offset.value()


class MathDialog(qtw.QDialog):
    """Pick two signals, a target plot, and a name for a new math signal."""

    def __init__(self, operator: str, signal_labels: list[str], plot_titles: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Math | {operator}")
        self.setMinimumWidth(280)

        layout = qtw.QVBoxLayout(self)
        form = qtw.QFormLayout()

        self.combo_a = qtw.QComboBox()
        self.combo_a.addItems(signal_labels)
        self.combo_a.setToolTip("First signal (A) in the expression")
        form.addRow("Signal A:", self.combo_a)

        form.addRow("Operator:", qtw.QLabel(operator))

        self.combo_b = qtw.QComboBox()
        self.combo_b.addItems(signal_labels)
        if len(signal_labels) > 1:
            self.combo_b.setCurrentIndex(1)
        self.combo_b.setToolTip("Second signal (B) in the expression")
        form.addRow("Signal B:", self.combo_b)

        self.edit_label = qtw.QLineEdit()
        self.edit_label.setToolTip("Name for the new derived signal (auto-suggested from A/B, editable)")
        form.addRow("New signal name:", self.edit_label)

        self.combo_plot = qtw.QComboBox()
        self.combo_plot.addItems(plot_titles)
        self.combo_plot.setToolTip("Which plot will display the new signal")
        form.addRow("Show on plot:", self.combo_plot)

        layout.addLayout(form)

        def suggest_label():
            a, b = self.combo_a.currentText(), self.combo_b.currentText()
            if a and b:
                self.edit_label.setText(f"{a}{operator}{b}")

        self.combo_a.currentTextChanged.connect(suggest_label)
        self.combo_b.currentTextChanged.connect(suggest_label)
        suggest_label()

        button_box = qtw.QDialogButtonBox(
            qtw.QDialogButtonBox.StandardButton.Ok | qtw.QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_values(self) -> tuple[str, str, str, str]:
        """Return (signal_a, signal_b, new_label, target_plot)."""
        return (
            self.combo_a.currentText(),
            self.combo_b.currentText(),
            self.edit_label.text().strip(),
            self.combo_plot.currentText(),
        )
