from PyQt6 import QtWidgets as qtw
from PyQt6 import QtGui as qtg
from PyQt6 import QtCore as qtc

import pyqtgraph as pg

from loguru import logger


class SignalController(qtw.QGroupBox):
    def __init__(self):
        super().__init__("Controls")

        self.layout = qtw.QVBoxLayout(self)
        self.signals = []




    def add_signal(self, label, model):
        self.signals.append(model)
        gui = qtw.QHBoxLayout(self)
        # Name

        # On off

        # filter
        tmp =  FilterDialog(label="test")
        # scaling

        # build local layout

        # append master

        # Start / Stop toggle button TODO move into add signal function
        self.button = qtw.QPushButton()
        self.button.setCheckable(True)
        self.button.toggled.connect(self.__start_stop)
        self.__start_stop(False)  # Init Button States

        self.layout.addWidget(self.button)
        self.layout.addStretch() # Add empty space on the right side
        self.layout.addWidget(tmp)


    def __start_stop(self, checked):
        if checked:
            self.button.setText("Stopped")
            self.button.setStyleSheet("background-color: red; color: white;")
            self.button.setIcon(qtw.QApplication.style().standardIcon(qtw.QStyle.StandardPixmap.SP_MediaPause))
        else:
            self.button.setText("Running")
            self.button.setStyleSheet("background-color: green; color: white;")
            self.button.setIcon(qtw.QApplication.style().standardIcon(qtw.QStyle.StandardPixmap.SP_MediaPlay))




class FilterDialog(qtw.QDialog):
    def __init__(
        self,
        label: str,
        filter_enabled: bool = False,
        current_cutoff: float = 100.0,
        current_order: int = 4,
        sampling_rate: float = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Filter | {label}")
        self.setMinimumWidth(250)

        layout = qtw.QVBoxLayout(self)

        # Enable toggle
        self.chk_enabled = qtw.QCheckBox("Filter ON")
        self.chk_enabled.setChecked(filter_enabled)
        self.chk_enabled.toggled.connect(self._on_toggle)
        layout.addWidget(self.chk_enabled)

        # Cutoff frequency
        cutoff_layout = qtw.QHBoxLayout()
        cutoff_layout.addWidget(qtw.QLabel("Cutoff [Hz]:"))
        self.spin_cutoff = qtw.QDoubleSpinBox()
        self.spin_cutoff.setRange(0.01, 1e6)
        self.spin_cutoff.setDecimals(2)
        self.spin_cutoff.setSingleStep(1.0)
        self.spin_cutoff.setValue(current_cutoff)
        cutoff_layout.addWidget(self.spin_cutoff)
        layout.addLayout(cutoff_layout)

        # Order
        order_layout = qtw.QHBoxLayout()
        order_layout.addWidget(qtw.QLabel("Order:"))
        self.spin_order = qtw.QSpinBox()
        self.spin_order.setRange(1, 20)
        self.spin_order.setValue(current_order)
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
            qtw.QDialogButtonBox.StandardButton.Ok
            | qtw.QDialogButtonBox.StandardButton.Cancel
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
        offset_layout.addWidget(self.spin_offset)
        layout.addLayout(offset_layout)

        # Info label
        info_label = qtw.QLabel("Formula: output = (raw - offset) * gain")
        info_label.setStyleSheet("font-size: 9pt; color: gray;")
        layout.addWidget(info_label)

        # OK / Cancel buttons
        button_box = qtw.QDialogButtonBox(
            qtw.QDialogButtonBox.StandardButton.Ok
            | qtw.QDialogButtonBox.StandardButton.Cancel
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
