"""Derived signals computed live from two existing SignalModels."""

import numpy as np
from PyQt6 import QtWidgets as qtw

OPERATORS = ("+", "-", "*", "/")  # New operations only need an entry here and in _FUNCS

_FUNCS = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: np.divide(a, b, out=np.full_like(a, np.nan, dtype=float), where=b != 0),
}


class MathSignal:
    """
    `label = model_a <operator> model_b`, recomputed from scratch each tick.

    Not a `DataProducer` — no new samples originate here; it derives a
    dependent view over the overlapping tail of two existing models' buffers.
    """

    def __init__(self, label: str, model_a, model_b, operator: str):
        if operator not in _FUNCS:
            raise ValueError(f"Unsupported operator '{operator}'")
        self.label = label
        self.model_a = model_a
        self.model_b = model_b
        self.operator = operator
        self._fn = _FUNCS[operator]

    def compute(self) -> tuple[np.ndarray, np.ndarray]:
        """Return (t, x) over the overlapping tail of both source buffers."""
        n = min(len(self.model_a.data), len(self.model_b.data))
        if n == 0:
            return np.array([]), np.array([])

        t_a, data_a = self.model_a.snapshot()
        _, data_b = self.model_b.snapshot()
        t = t_a[-n:]
        x = self._fn(data_a[-n:], data_b[-n:])
        return t, x


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
