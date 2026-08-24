"""Manual Auto/Min/Max range entry — reused for a plot's Y-axis and (for
spectrograms) its colormap level range. Same enable-checkbox-then-fields
shape as FilterDialog/ScalingDialog, just generic over a unit label.
"""

from PyQt6 import QtWidgets as qtw


class RangeDialog(qtw.QDialog):
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
            qtw.QDialogButtonBox.StandardButton.Ok
            | qtw.QDialogButtonBox.StandardButton.Cancel
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
