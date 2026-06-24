from PyQt6 import QtWidgets as qtw
from PyQt6 import QtGui as qtg
from PyQt6 import QtCore as qtc

import pyqtgraph as pg

from loguru import logger


class SignalController(qtw.QGroupBox):
    def __init__(self, ):
        super().__init__(title="Signal Controller")  # QGroupBox, set title
        self.setLayout(qtw.QHBoxLayout())  # Create an internal layout for inside the box

        # Start / Stop toggle button
        self.button = qtw.QPushButton()
        self.button.setCheckable(True)
        self.button.toggled.connect(self.__start_stop)
        self.__start_stop(checked=False)  # Init Button States
        self.layout().addWidget(self.button)
        
        self.layout().addStretch(1) # Add empty space on the right side
        
    def __start_stop(self, checked):
        if checked:
            self.button.setText("Stopped")
            self.button.setStyleSheet("background-color: red; color: white;")
            self.button.setIcon(qtw.QApplication.style().standardIcon(qtw.QStyle.StandardPixmap.SP_MediaPause))
        else:
            self.button.setText("Running")
            self.button.setStyleSheet("background-color: green; color: white;")
            self.button.setIcon(qtw.QApplication.style().standardIcon(qtw.QStyle.StandardPixmap.SP_MediaPlay))


class ScalingDialog(qtw.QDialog):
    """
    Dialog for entering Gain and Offset for a signal.
    """

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
    

class FilterDialog(qtw.QDialog):
    """
    Dialog for configuring a low-pass Butterworth filter for a signal.
    """

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
    


class Live_Plot(qtw.QWidget):
    """
    Live pyqtgraph plot with thread-safe, scrolling time-series signals
    and optional STFT spectrogram subplots.
    """
    # ... (skipping to line 1227 based on visible trace)

    def _on_visibility_toggled(self, label: str, checked: bool) -> None:
        # STFT spectrogram
        stft_state = self.stft_states.get(label)
        if stft_state is not None:
            stft_state.img_item.setVisible(checked)
            return

    def _on_color_button_clicked(self, label: str) -> None:
        """
        Open a color dialog for a time-domain signal curve.
        """
        if label not in self.signals:
            return

        curve = self.signals[label].curve
        pen = curve.opts.get("pen", None)
        initial_color = qtg.QColor("white")
        if pen is not None and hasattr(pen, "color"):
            initial_color = pen.color()

        color = qtw.QColorDialog.getColor(
            initial_color,
            parent=self,
            title=f"Choose color for '{label}'",
        )
        if not color.isValid():
            return

        curve.setPen(pg.mkPen(color, width=1))

        btn = self.channel_controls.get(label, {}).get("button")
        if btn is not None:
            btn.setStyleSheet(f"background-color: {color.name()};")

    def _on_scaling_button_clicked(self, label: str) -> None:
        """
        Open the Scaling dialog for the given signal.
        The Scaling button turns red when scaling is active.
        """
        if label not in self.signals:
            return

        settings = self.scaling_settings.get(label, {
            "enabled": False,
            "gain": 1.0,
            "offset": 0.0,
        })

        dialog = ScalingDialog(
            label=label,
            scaling_enabled=settings["enabled"],
            current_gain=settings["gain"],
            current_offset=settings["offset"],
            parent=self,
        )

        if dialog.exec() == qtw.QDialog.DialogCode.Accepted:
            enabled, gain, offset = dialog.get_values()

            # Save settings for next dialog open
            self.scaling_settings[label] = {
                "enabled": enabled,
                "gain": gain,
                "offset": offset,
            }

            if enabled:
                self.add_scaling2signal(label, gain, offset)
                logger.info(f"Scaling for '{label}' enabled: gain={gain}, offset={offset}")
            else:
                self.remove_scaling_from_signal(label)
                logger.info(f"Scaling for '{label}' disabled.")

            # Update button color: red if active, default otherwise
            self.update_scaling_button_style(label, enabled)

    def _on_filter_button_clicked(self, label: str) -> None:
        """
        Open the Filter dialog for the given signal.
        The Filter button turns red when a filter is active.
        """
        if label not in self.signals:
            return

        sig = self.signals[label]
        settings = self.filter_settings.get(label, {
            "enabled": False,
            "cutoff": 100.0,
            "order": 4,
        })

        dialog = FilterDialog(
            label=label,
            filter_enabled=settings["enabled"],
            current_cutoff=settings["cutoff"],
            current_order=settings["order"],
            sampling_rate=sig.sampling_rate,
            parent=self,
        )

        if dialog.exec() == qtw.QDialog.DialogCode.Accepted:
            enabled, cutoff, order = dialog.get_values()

            # Save settings for next dialog open
            self.filter_settings[label] = {
                "enabled": enabled,
                "cutoff": cutoff,
                "order": order,
            }

            if enabled:
                try:
                    self.add_filter2signal(label, cutoff, order)
                except ValueError as err:
                    logger.error(f"Filter error for '{label}': {err}")
                    qtw.QMessageBox.warning(
                        self,
                        f"Filter Error | {label}",
                        str(err),
                    )
                    # Revert: don't apply the filter
                    self.filter_settings[label]["enabled"] = False
                    self.remove_filter_from_signal(label)
                    self.update_filter_button_style(label, False)
                    return
            else:
                self.remove_filter_from_signal(label)
                logger.info(f"Filter for '{label}' disabled.")

            # Update button color: red if active, default otherwise
            self.update_filter_button_style(label, enabled)

    def _update_filter_button_style(self, label: str, active: bool) -> None:
        """
        Set the Filter button red if a filter is active, default otherwise.
        """
        controls = self.channel_controls.get(label)
        if not controls:
            return
        btn_filter = controls.get("btn_filter")
        if btn_filter is None:
            return

        if active:
            btn_filter.setStyleSheet(
                "background-color: #cc3333; color: white; font-weight: bold;"
            )
        else:
            btn_filter.setStyleSheet("")

    def _update_scaling_button_style(self, label: str, active: bool) -> None:
        """
        Set the Scaling button red if scaling is active, default otherwise.
        """
        controls = self.channel_controls.get(label)
        if not controls:
            return
        btn_scaling = controls.get("btn_scaling")
        if btn_scaling is None:
            return

        if active:
            btn_scaling.setStyleSheet(
                "background-color: #cc3333; color: white; font-weight: bold;"
            )
        else:
            btn_scaling.setStyleSheet("")
