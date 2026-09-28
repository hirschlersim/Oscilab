from PyQt6 import QtWidgets as qtw
from PyQt6 import QtGui as qtg
from PyQt6 import QtCore as qtc

import pyqtgraph as pg

from loguru import logger


class SignalController(qtw.QGroupBox):
    """Container holding `SignalRow`s and/or plot-level header buttons.

    Used two ways: a single flat instance (`Workspace.signal_list`) holds
    one row per registered signal regardless of how many plots show it —
    linking the same signal to a second plot reuses its existing row rather
    than adding another. Each plot also gets its own small instance holding
    only header buttons (e.g. "Y-Axis") and no rows, since axis range is a
    per-plot property, not a per-signal one.
    """

    def __init__(self, title: str = "Controls:", parent=None):
        super().__init__(title, parent)

        self.layout = qtw.QVBoxLayout(self)
        self.layout.setAlignment(qtc.Qt.AlignmentFlag.AlignTop)
        self.rows: dict[str, "SignalRow"] = {}

        self._header_layout = qtw.QHBoxLayout()
        self._header_layout.setContentsMargins(0, 0, 0, 0)
        self.layout.addLayout(self._header_layout)

        # Live cursor readout (Cursors menu) — hidden until cursors are
        # enabled on this plot; updated in place each tick, not rebuilt.
        self._cursor_label = qtw.QLabel()
        self._cursor_label.setStyleSheet("font-size: 9pt; color: gray; font-family: Consolas, monospace;")
        self._cursor_label.setToolTip("Classic two-cursor measurement: value at each cursor, ΔT, 1/ΔT, ΔValue")
        self._cursor_label.setVisible(False)
        self.layout.addWidget(self._cursor_label)

    def add_header_button(self, text: str, tooltip: str, callback) -> qtw.QPushButton:
        """Add a plot-level button (e.g. "Y-Axis") above the signal rows."""
        button = qtw.QPushButton(text)
        button.setToolTip(tooltip)
        button.clicked.connect(callback)
        self._header_layout.addWidget(button)
        return button

    def set_cursor_readout(self, text: str | None):
        """Show (with `text`) or hide (`None`) this plot's cursor readout."""
        if text is None:
            self._cursor_label.setVisible(False)
            self._cursor_label.clear()
        else:
            self._cursor_label.setText(text)
            self._cursor_label.setVisible(True)

    def add_signal(self, label, model, derived: bool = False, has_curve: bool = True):
        assert label not in self.rows, f"Signal '{label}' already added"

        row = SignalRow(label=label, model=model, derived=derived, has_curve=has_curve, parent=self)
        self.rows[label] = row
        self.layout.addWidget(row)
        return row

    def update(self):
        """Refresh live metric readouts for every row (called each tick)."""
        for row in self.rows.values():
            row.update_metrics()


class SignalRow(qtw.QWidget):
    """UI + wiring for a single signal: color, visibility, filter, scaling, metrics."""

    visibility_changed = qtc.pyqtSignal(str, bool)  # (label, visible)
    color_changed = qtc.pyqtSignal(str, str)  # (label, color hex)

    _METRICS = (("Min", "min"), ("Mean", "mean"), ("Max", "max"), ("P-P", "ptp"), ("RMS", "rms"))
    _METRIC_TOOLTIPS = {
        "Min": "Minimum value in the current buffer",
        "Mean": "Average value in the current buffer",
        "Max": "Maximum value in the current buffer",
        "P-P": "Peak-to-peak: Max minus Min",
        "RMS": "Root mean square: a measure of the signal's overall magnitude",
    }

    def __init__(self, label: str, model, derived: bool = False, has_curve: bool = True, parent=None):
        super().__init__(parent)
        self.label = label
        self.model = model
        self.derived = derived
        self.has_curve = has_curve
        self._color = "#888888"

        outer = qtw.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 6)

        # Row 1: color, name, toggle, dialogs
        controls = qtw.QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)

        self.color_button = qtw.QPushButton()
        self.color_button.setFixedSize(18, 18)
        self.color_button.setToolTip("Curve color")
        self.color_button.clicked.connect(self.__pick_color)
        controls.addWidget(self.color_button)

        self.name_label = qtw.QLabel(label)
        controls.addWidget(self.name_label)

        self.visibility_button = qtw.QPushButton()
        self.visibility_button.setCheckable(True)
        self.visibility_button.setToolTip("Show or hide this signal's curve without touching its data")
        self.visibility_button.toggled.connect(self.__on_visibility_toggled)

        self.filter_button = qtw.QPushButton("Filter")
        self.filter_button.setToolTip("Apply a low-pass Butterworth filter to this signal")
        self.filter_button.clicked.connect(self.__open_filter_dialog)

        self.scaling_button = qtw.QPushButton("Scaling")
        self.scaling_button.setToolTip("Apply a gain/offset scale to this signal")
        self.scaling_button.clicked.connect(self.__open_scaling_dialog)

        controls.addStretch()  # Push all buttons to the right, flush with each other
        controls.addWidget(self.visibility_button)
        controls.addWidget(self.filter_button)
        controls.addWidget(self.scaling_button)
        outer.addLayout(controls)

        if derived:
            # A math signal is fully recomputed each tick, not accumulated
            # incrementally, so filter/scaling's persistent state doesn't apply.
            for button in (self.filter_button, self.scaling_button):
                button.setEnabled(False)
                button.setToolTip("Not available for derived (math) signals")

        if not has_curve:
            # E.g. a spectrogram: there's no plotted curve to color.
            self.color_button.setEnabled(False)
            self.color_button.setToolTip("No curve color — this signal isn't plotted as a line")

        # Row 2: live metrics
        metrics_layout = qtw.QHBoxLayout()
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        self.metric_labels: dict[str, qtw.QLabel] = {}
        for title, _ in self._METRICS:
            metric_label = qtw.QLabel(self.__format_metric(title, None))
            metric_label.setStyleSheet("font-size: 9pt; color: gray; font-family: Consolas, monospace;")
            metric_label.setToolTip(self._METRIC_TOOLTIPS[title])
            self.metric_labels[title] = metric_label
            metrics_layout.addWidget(metric_label)
        metrics_layout.addStretch()
        outer.addLayout(metrics_layout)

        # Row 3: producer-specific action buttons (e.g. "Reconnect", "Zero"),
        # under the line — empty until a producer with actions() is attached
        # (see `Workspace.attach_producer` / `add_action_button` below).
        self._actions_layout = qtw.QHBoxLayout()
        self._actions_layout.setContentsMargins(0, 0, 0, 0)
        outer.addLayout(self._actions_layout)

        # Last-entered dialog values, kept around so re-opening a dialog (or
        # briefly toggling it off) doesn't lose what was typed in.
        # Default cutoff well below Nyquist (fs/2) — a flat 50 Hz is invalid at fs <= 100.
        self.filter_settings = {"enabled": False, "cutoff": min(50.0, model.fs / 4), "order": 4}
        self.scaling_settings = {"enabled": False, "gain": 1.0, "offset": 0.0}

        self.__on_visibility_toggled(False)  # Init button state: visible by default
        self.set_color(self._color)

    def update_metrics(self):
        metrics = self.model.compute_metrics()
        for title, field in self._METRICS:
            self.metric_labels[title].setText(self.__format_metric(title, metrics[field]))

    @staticmethod
    def __format_metric(title: str, value: float | None) -> str:
        # Fixed width/decimals (and a monospace font on the label) so the
        # readout doesn't reflow as values cross order-of-magnitude boundaries.
        formatted = f"{value:+9.3f}" if value is not None else f"{'—':>9}"
        return f"{title}: {formatted}"

    def set_color(self, color: str):
        """Apply a curve color from either the color picker or an initial assignment."""
        self._color = color
        self.color_button.setStyleSheet(f"background-color: {color}; border: 1px solid #444;")
        self.color_changed.emit(self.label, color)

    def add_action_button(self, text: str, tooltip: str, callback) -> qtw.QPushButton:
        """Add a producer-specific action button (e.g. "Reconnect"), under
        the metrics line — see `oscilab.producers.DataProducer.actions`."""
        button = qtw.QPushButton(text)
        button.setToolTip(tooltip)
        button.clicked.connect(callback)
        self._actions_layout.addWidget(button)
        return button

    def enable_curve(self):
        """Upgrade a spectrogram-only row once its signal also gets plotted
        as a curve somewhere (e.g. linked to a `TimeseriesPlot` afterward)."""
        if self.has_curve:
            return
        self.has_curve = True
        self.color_button.setEnabled(True)
        self.color_button.setToolTip("Curve color")

    def __pick_color(self):
        chosen = qtw.QColorDialog.getColor(qtg.QColor(self._color), self, f"Color | {self.label}")
        if chosen.isValid():
            self.set_color(chosen.name())

    def __on_visibility_toggled(self, checked):
        """checked == True means "hidden" (red); unchecked means "visible" (green)."""
        visible = not checked

        if checked:
            self.visibility_button.setText("Hidden")
            self.visibility_button.setStyleSheet("background-color: red; color: white;")
            self.visibility_button.setIcon(qtw.QApplication.style().standardIcon(qtw.QStyle.StandardPixmap.SP_DialogNoButton))
        else:
            self.visibility_button.setText("Visible")
            self.visibility_button.setStyleSheet("background-color: green; color: white;")
            self.visibility_button.setIcon(qtw.QApplication.style().standardIcon(qtw.QStyle.StandardPixmap.SP_DialogYesButton))

        self.visibility_changed.emit(self.label, visible)

    def __open_filter_dialog(self):
        settings = self.filter_settings
        dialog = FilterDialog(
            label=self.label,
            filter_enabled=settings["enabled"],
            current_cutoff=settings["cutoff"],
            current_order=settings["order"],
            sampling_rate=self.model.fs,
            parent=self,
        )

        if dialog.exec() == qtw.QDialog.DialogCode.Accepted:
            enabled, cutoff, order = dialog.get_values()

            if enabled:
                try:
                    self.model.add_filter(cutoff=cutoff, order=order)
                except ValueError as e:
                    # An unhandled exception in a Qt slot aborts the whole app.
                    logger.warning(f"Filter for '{self.label}' not applied: {e}")
                    enabled = False
                else:
                    logger.info(f"Filter for '{self.label}' enabled: cutoff={cutoff}Hz, order={order}")

            # Save settings for next dialog open
            self.filter_settings = {"enabled": enabled, "cutoff": cutoff, "order": order}

            if not enabled:
                self.model.rem_filter()
                logger.info(f"Filter for '{self.label}' disabled.")

            # Update button color: highlighted if active, default otherwise
            self.__set_active_style(self.filter_button, enabled)

    def __open_scaling_dialog(self):
        settings = self.scaling_settings
        dialog = ScalingDialog(
            label=self.label,
            scaling_enabled=settings["enabled"],
            current_gain=settings["gain"],
            current_offset=settings["offset"],
            parent=self,
        )

        if dialog.exec() == qtw.QDialog.DialogCode.Accepted:
            enabled, gain, offset = dialog.get_values()

            # Save settings for next dialog open
            self.scaling_settings = {"enabled": enabled, "gain": gain, "offset": offset}

            if enabled:
                self.model.add_scaling(gain, offset)
                logger.info(f"Scaling for '{self.label}' enabled: gain={gain}, offset={offset}")
            else:
                self.model.rem_scaling()
                logger.info(f"Scaling for '{self.label}' disabled.")

            # Update button color: highlighted if active, default otherwise
            self.__set_active_style(self.scaling_button, enabled)

    @staticmethod
    def __set_active_style(button, active: bool):
        # Red = this signal's data is being altered (filtered/scaled); empty
        # falls back to the normal blue QSS button style when untouched.
        button.setStyleSheet("background-color: red; color: white;" if active else "")




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
