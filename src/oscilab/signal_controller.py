from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar, TypedDict

from loguru import logger as default_logger
from PyQt6 import QtCore as qtc
from PyQt6 import QtGui as qtg
from PyQt6 import QtWidgets as qtw

from oscilab.dialogs import FilterDialog, ScalingDialog
from oscilab.signal_model import SignalModel

if TYPE_CHECKING:
    from loguru import Logger


class FilterSettings(TypedDict):
    enabled: bool
    cutoff: float  # Hz
    order: int


class ScalingSettings(TypedDict):
    enabled: bool
    gain: float
    offset: float


class SignalController(qtw.QGroupBox):
    """
    Container holding `SignalRow`s and/or plot-level header buttons.

    Used two ways: a single flat instance (`Workspace.signal_list`) holds
    one row per registered signal regardless of how many plots show it —
    linking the same signal to a second plot reuses its existing row rather
    than adding another. Each plot also gets its own small instance holding
    only header buttons (e.g. "Y-Axis") and no rows, since axis range is a
    per-plot property, not a per-signal one.
    """

    def __init__(
        self, title: str = "Controls:", parent: qtw.QWidget | None = None, logger: "Logger | None" = None
    ) -> None:
        super().__init__(title, parent)
        self._logger = logger or default_logger  # Handed to its rows

        self._layout = qtw.QVBoxLayout(self)
        self._layout.setAlignment(qtc.Qt.AlignmentFlag.AlignTop)
        self.rows: dict[str, SignalRow] = {}

        self._header_layout = qtw.QHBoxLayout()
        self._header_layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addLayout(self._header_layout)

        # Live cursor readout (Cursors menu) — hidden until cursors are
        # enabled on this plot; updated in place each tick, not rebuilt.
        self._cursor_label = qtw.QLabel()
        self._cursor_label.setStyleSheet("font-size: 9pt; color: gray; font-family: Consolas, monospace;")
        self._cursor_label.setToolTip("Classic two-cursor measurement: value at each cursor, ΔT, 1/ΔT, ΔValue")
        self._cursor_label.setVisible(False)
        self._layout.addWidget(self._cursor_label)

    def add_header_button(self, text: str, tooltip: str, callback: Callable[..., object]) -> qtw.QPushButton:
        """Add a plot-level button (e.g. "Y-Axis") above the signal rows."""
        button = qtw.QPushButton(text)
        button.setToolTip(tooltip)
        button.clicked.connect(callback)
        self._header_layout.addWidget(button)
        return button

    def set_cursor_readout(self, text: str | None) -> None:
        """Show (with `text`) or hide (`None`) this plot's cursor readout."""
        if text is None:
            self._cursor_label.setVisible(False)
            self._cursor_label.clear()
        else:
            self._cursor_label.setText(text)
            self._cursor_label.setVisible(True)

    def add_signal(self, label: str, model: SignalModel, derived: bool = False, has_curve: bool = True) -> "SignalRow":
        if label in self.rows:
            raise ValueError(f"Signal '{label}' already added")

        row = SignalRow(
            label=label, model=model, derived=derived, has_curve=has_curve, parent=self, logger=self._logger
        )
        self.rows[label] = row
        self._layout.addWidget(row)
        return row

    def remove_signal(self, label: str) -> None:
        row = self.rows.pop(label)
        row.hide()
        row.deleteLater()

    def refresh(self) -> None:
        """Refresh live metric readouts for every row (called each tick)."""
        for row in self.rows.values():
            row.update_metrics()


class SignalRow(qtw.QWidget):
    """
    UI + wiring for a single signal.

    Collapsed by default: just the curve color, the name and Visible (plus
    Restart while its producer is stopped by an error). Clicking the name
    expands the details — live metrics and Filter/Scaling — which are only
    built the first time, and whose metrics are only computed while expanded.
    (A producer's own functions live in the Producers menu.)
    """

    visibility_changed = qtc.pyqtSignal(str, bool)  # (label, visible)
    restart_requested = qtc.pyqtSignal(str)  # (label) — its producer's Restart button
    remove_requested = qtc.pyqtSignal(str)  # (label) — a math signal's Remove button
    color_changed = qtc.pyqtSignal(str, str)  # (label, color hex)

    _METRICS = (("Min", "min"), ("Mean", "mean"), ("Max", "max"), ("P-P", "ptp"), ("RMS", "rms"))
    _METRIC_TOOLTIPS: ClassVar[dict[str, str]] = {
        "Min": "Minimum value in the current buffer",
        "Mean": "Average value in the current buffer",
        "Max": "Maximum value in the current buffer",
        "P-P": "Peak-to-peak: Max minus Min",
        "RMS": "Root mean square: a measure of the signal's overall magnitude",
    }
    _NAME_TOOLTIP = "Show or hide this signal's metrics and Filter/Scaling"

    def __init__(
        self,
        label: str,
        model: SignalModel,
        derived: bool = False,
        has_curve: bool = True,
        parent: qtw.QWidget | None = None,
        logger: "Logger | None" = None,
    ) -> None:
        super().__init__(parent)
        self._logger = logger or default_logger
        self.label = label
        self.model = model
        self.derived = derived
        self.has_curve = has_curve
        self._color = "#888888"

        # Last-applied settings, kept so re-opening a dialog (or briefly
        # toggling it off) doesn't lose what was typed in. Default cutoff well
        # below Nyquist (fs/2) — a flat 50 Hz is invalid at fs <= 100.
        self.filter_settings: FilterSettings = {"enabled": False, "cutoff": min(50.0, model.fs / 4), "order": 4}
        self.scaling_settings: ScalingSettings = {"enabled": False, "gain": 1.0, "offset": 0.0}

        # Built on first expand (see `set_expanded`)
        self._details: qtw.QWidget | None = None
        self.filter_button: qtw.QPushButton | None = None
        self.scaling_button: qtw.QPushButton | None = None
        self.metric_labels: dict[str, qtw.QLabel] = {}

        self._outer = qtw.QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 6)
        self._outer.addLayout(self.__build_header())

        self.__on_visibility_toggled(False)  # Init button state: visible by default
        self.set_color(self._color)

    def __build_header(self) -> qtw.QHBoxLayout:
        header = qtw.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        self.color_button = qtw.QPushButton()
        self.color_button.setFixedSize(18, 18)
        self.color_button.setToolTip("Curve color")
        self.color_button.clicked.connect(self.__pick_color)
        header.addWidget(self.color_button)
        if not self.has_curve:
            # E.g. a spectrogram: there's no plotted curve to color.
            self.color_button.setEnabled(False)
            self.color_button.setToolTip("No curve color — this signal isn't plotted as a line")

        # The name doubles as the expand/collapse toggle.
        self.name_button = qtw.QToolButton()
        self.name_button.setText(self.label)
        self.name_button.setCheckable(True)
        self.name_button.setAutoRaise(True)
        self.name_button.setToolButtonStyle(qtc.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.name_button.setArrowType(qtc.Qt.ArrowType.RightArrow)
        self.name_button.setToolTip(self._NAME_TOOLTIP)
        self.name_button.toggled.connect(self.set_expanded)
        header.addWidget(self.name_button)

        # Only shown while the producer is stopped by an error (see `set_error`).
        self.restart_button = qtw.QPushButton("Restart")
        self.restart_button.setToolTip("Start this signal's producer again")
        self.restart_button.clicked.connect(lambda: self.restart_requested.emit(self.label))
        self.restart_button.setVisible(False)
        header.addWidget(self.restart_button)

        header.addStretch()  # Push Visible to the right

        self.visibility_button = qtw.QPushButton()
        self.visibility_button.setCheckable(True)
        self.visibility_button.setToolTip("Show or hide this signal's curve without touching its data")
        self.visibility_button.toggled.connect(self.__on_visibility_toggled)
        header.addWidget(self.visibility_button)
        return header

    def __build_details(self) -> qtw.QWidget:
        details = qtw.QWidget()
        layout = qtw.QVBoxLayout(details)
        layout.setContentsMargins(24, 0, 0, 0)  # Indented under the name

        # Live metrics
        metrics_layout = qtw.QHBoxLayout()
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        for title, _ in self._METRICS:
            metric_label = qtw.QLabel(self.__format_metric(title, None))
            metric_label.setStyleSheet("font-size: 9pt; color: gray; font-family: Consolas, monospace;")
            metric_label.setToolTip(self._METRIC_TOOLTIPS[title])
            self.metric_labels[title] = metric_label
            metrics_layout.addWidget(metric_label)
        metrics_layout.addStretch()
        layout.addLayout(metrics_layout)

        # Filter/Scaling
        buttons_layout = qtw.QHBoxLayout()
        buttons_layout.setContentsMargins(0, 0, 0, 0)

        self.filter_button = qtw.QPushButton("Filter")
        self.filter_button.setToolTip("Apply a low-pass Butterworth filter to this signal")
        self.filter_button.clicked.connect(self.__open_filter_dialog)
        self.scaling_button = qtw.QPushButton("Scaling")
        self.scaling_button.setToolTip("Apply a gain/offset scale to this signal")
        self.scaling_button.clicked.connect(self.__open_scaling_dialog)
        if self.derived:
            # A math signal is fully recomputed each tick, not accumulated
            # incrementally, so filter/scaling's persistent state doesn't apply.
            for button in (self.filter_button, self.scaling_button):
                button.setEnabled(False)
                button.setToolTip("Not available for derived (math) signals")
        self.__set_active_style(self.filter_button, self.filter_settings["enabled"])
        self.__set_active_style(self.scaling_button, self.scaling_settings["enabled"])
        buttons_layout.addWidget(self.filter_button)
        buttons_layout.addWidget(self.scaling_button)
        if self.derived:
            # Math signals exist only in the GUI, so they can be removed from it
            # (producer-fed signals go with their producer).
            remove_button = qtw.QPushButton("Remove")
            remove_button.setToolTip("Remove this math signal from every plot and the list")
            remove_button.clicked.connect(lambda: self.remove_requested.emit(self.label))
            buttons_layout.addWidget(remove_button)
        buttons_layout.addStretch()
        layout.addLayout(buttons_layout)
        return details

    @property
    def expanded(self) -> bool:
        return self.name_button.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        """Show or hide the details (metrics, Filter/Scaling)."""
        if expanded and self._details is None:
            self._details = self.__build_details()
            self._outer.addWidget(self._details)
        if self._details is not None:
            self._details.setVisible(expanded)

        with qtc.QSignalBlocker(self.name_button):  # Also called directly, not only via the toggle
            self.name_button.setChecked(expanded)
        self.name_button.setArrowType(qtc.Qt.ArrowType.DownArrow if expanded else qtc.Qt.ArrowType.RightArrow)
        if expanded:
            self.update_metrics()  # Current values right away, not on the next tick

    def update_metrics(self) -> None:
        """Refresh the metric readouts — only while expanded; collapsed rows cost nothing per tick."""
        if not self.expanded:
            return
        metrics = self.model.compute_metrics()
        for title, field in self._METRICS:
            self.metric_labels[title].setText(self.__format_metric(title, metrics[field]))

    @staticmethod
    def __format_metric(title: str, value: float | None) -> str:
        # Fixed width/decimals (and a monospace font on the label) so the
        # readout doesn't reflow as values cross order-of-magnitude boundaries.
        formatted = f"{value:+9.3f}" if value is not None else f"{'—':>9}"
        return f"{title}: {formatted}"

    def set_color(self, color: str) -> None:
        """Apply a curve color from either the color picker or an initial assignment."""
        self._color = color
        self.color_button.setStyleSheet(f"background-color: {color}; border: 1px solid #444;")
        self.color_changed.emit(self.label, color)

    @property
    def color(self) -> str:
        return self._color

    @property
    def visible(self) -> bool:
        return not self.visibility_button.isChecked()  # Checked means "Hidden"

    def set_visible(self, visible: bool) -> None:
        self.visibility_button.setChecked(not visible)

    def set_error(self, message: str) -> None:
        """Flag that this signal's producer stopped with an error, and offer a Restart."""
        self.name_button.setText(f"⚠ {self.label}")
        self.name_button.setStyleSheet("color: red;")
        self.name_button.setToolTip(f"Producer stopped: {message}")
        self.restart_button.setVisible(True)

    def clear_error(self) -> None:
        self.name_button.setText(self.label)
        self.name_button.setStyleSheet("")
        self.name_button.setToolTip(self._NAME_TOOLTIP)
        self.restart_button.setVisible(False)

    def enable_curve(self) -> None:
        """
        Upgrade a spectrogram-only row once its signal also gets plotted
        as a curve somewhere (e.g. linked to a `TimeseriesPlot` afterward).
        """
        if self.has_curve:
            return
        self.has_curve = True
        self.color_button.setEnabled(True)
        self.color_button.setToolTip("Curve color")

    def apply_filter(self, enabled: bool, cutoff: float, order: int) -> bool:
        """
        Turn the low-pass filter on (with `cutoff` Hz, `order`) or off. Returns
        whether it's on: an invalid cutoff (at or above Nyquist) leaves it off.
        """
        if enabled:
            try:
                self.model.add_filter(cutoff=cutoff, order=order)
            except ValueError as e:
                # Reached from a Qt slot, where an unhandled exception aborts the app.
                self._logger.warning(f"Filter for '{self.label}' not applied: {e}")
                enabled = False
            else:
                self._logger.info(f"Filter for '{self.label}' enabled: cutoff={cutoff}Hz, order={order}")
        if not enabled:
            if self.filter_settings["enabled"]:
                self._logger.info(f"Filter for '{self.label}' disabled")
            self.model.remove_filter()

        self.filter_settings = {"enabled": enabled, "cutoff": cutoff, "order": order}
        if self.filter_button is not None:
            self.__set_active_style(self.filter_button, enabled)
        return enabled

    def apply_scaling(self, enabled: bool, gain: float, offset: float) -> None:
        """Turn gain/offset scaling on or off: output = (raw - offset) * gain."""
        if enabled:
            self.model.add_scaling(gain, offset)
            self._logger.info(f"Scaling for '{self.label}' enabled: gain={gain}, offset={offset}")
        else:
            if self.scaling_settings["enabled"]:
                self._logger.info(f"Scaling for '{self.label}' disabled")
            self.model.remove_scaling()

        self.scaling_settings = {"enabled": enabled, "gain": gain, "offset": offset}
        if self.scaling_button is not None:
            self.__set_active_style(self.scaling_button, enabled)

    def __pick_color(self) -> None:
        chosen = qtw.QColorDialog.getColor(qtg.QColor(self._color), self, f"Color | {self.label}")
        if chosen.isValid():
            self.set_color(chosen.name())

    def __on_visibility_toggled(self, checked: bool) -> None:
        """Checked == True means "hidden" (red); unchecked means "visible" (green)."""
        visible = not checked

        if checked:
            self.visibility_button.setText("Hidden")
            self.visibility_button.setStyleSheet("background-color: red; color: white;")
            icon = qtw.QStyle.StandardPixmap.SP_DialogNoButton
        else:
            self.visibility_button.setText("Visible")
            self.visibility_button.setStyleSheet("background-color: green; color: white;")
            icon = qtw.QStyle.StandardPixmap.SP_DialogYesButton
        if (style := qtw.QApplication.style()) is not None:
            self.visibility_button.setIcon(style.standardIcon(icon))

        self.visibility_changed.emit(self.label, visible)

    def __open_filter_dialog(self) -> None:
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
            self.apply_filter(*dialog.get_values())

    def __open_scaling_dialog(self) -> None:
        settings = self.scaling_settings
        dialog = ScalingDialog(
            label=self.label,
            scaling_enabled=settings["enabled"],
            current_gain=settings["gain"],
            current_offset=settings["offset"],
            parent=self,
        )
        if dialog.exec() == qtw.QDialog.DialogCode.Accepted:
            self.apply_scaling(*dialog.get_values())

    @staticmethod
    def __set_active_style(button: qtw.QPushButton, active: bool) -> None:
        # Red = this signal's data is being altered (filtered/scaled); empty
        # falls back to the normal blue QSS button style when untouched.
        button.setStyleSheet("background-color: red; color: white;" if active else "")
