"""
The workspace's plots (`oscilab.workspace.Workspace`): creating each kind —
its "Plot:" panel, with that kind's header buttons — linking signals to them
and taking them off, removing them, the time plots' shared X axis and their
cursor readouts. A signal's row in the list comes and goes with the plots
showing it.
"""

from typing import TYPE_CHECKING

from PyQt6 import QtWidgets as qtw

from oscilab.dialogs import HistogramDialog, RangeDialog
from oscilab.histogram_plot import HistogramPlot
from oscilab.logic_plot import LogicPlot
from oscilab.signal_controller import SignalController, SignalRow
from oscilab.spectrogram_plot import SpectrogramPlot
from oscilab.timeseries_plot import TimeseriesPlot
from oscilab.view import View
from oscilab.xy_plot import XYPlot

if TYPE_CHECKING:
    from oscilab.workspace import Workspace


def _ask_range(
    parent: qtw.QWidget | None,
    title: str,
    auto: bool,
    pinned: tuple[float, float],
    live: tuple[float, float],
    unit: str = "",
) -> tuple[bool, float, float] | None:
    """
    Ask for an Auto/Min/Max range, pre-filled with what's in effect right
    now — the `live` auto-fitted range while in Auto, else the `pinned` one —
    so there's always a real number to start adjusting from, not a stale
    placeholder. Returns `(auto, lo, hi)`, or None if cancelled.
    """
    lo, hi = live if auto else pinned
    dialog = RangeDialog(title=title, current_auto=auto, current_min=lo, current_max=hi, unit=unit, parent=parent)
    if dialog.exec() != qtw.QDialog.DialogCode.Accepted:
        return None
    return dialog.get_values()


class Plotting:
    """Creates and removes one workspace's plots, which it keeps in its registries (`views`, `controllers`)."""

    def __init__(self, workspace: "Workspace") -> None:
        self.workspace = workspace

    def add_plot(self, title: str) -> tuple[TimeseriesPlot, SignalController]:
        """See `Workspace.add_plot`."""
        self.__check_new_title(title)
        return self.__add_time_plot(title, TimeseriesPlot(title=title))

    def add_logic_plot(self, title: str, threshold: float) -> tuple[LogicPlot, SignalController]:
        """See `Workspace.add_logic_plot`."""
        self.__check_new_title(title)
        plot, controller = self.__add_time_plot(title, LogicPlot(title=title, threshold=threshold))

        def ask_threshold() -> None:
            value, ok = qtw.QInputDialog.getDouble(
                controller, f"Threshold | {title}", "High above:", plot.threshold, -1e12, 1e12, 6
            )
            if ok:
                plot.set_threshold(value)
                self.workspace.log(f"Logic threshold for '{title}' set to {value:g}")

        controller.add_header_button("Threshold...", "The value above which a signal counts as high", ask_threshold)
        return plot, controller

    def __add_time_plot[P: TimeseriesPlot](self, title: str, plot: P) -> tuple[P, SignalController]:
        first_plot = next(iter(self.workspace.plots.values()), None)
        controller = self.__add_view(title, plot)
        # Fires on every drag, so the readout stays in sync even while
        # paused (the timer-driven refresh loop covers it while running).
        plot.cursor_changed.connect(lambda t=title: self.__refresh_cursor_readout(t))

        # Keep every plot's time axis (pan/zoom) in sync with the first one,
        # so scrubbing/zooming one plot moves them all together.
        if first_plot is not None:
            plot.setXLink(first_plot)
        return plot, controller

    def add_spectrogram(self, label: str, nperseg: int, noverlap: int) -> tuple[SpectrogramPlot, SignalController]:
        """See `Workspace.add_spectrogram`."""
        self.__check_new_title(label)
        if label not in self.workspace.signals:
            raise ValueError(f"Unknown signal '{label}'")

        model = self.workspace.signals[label]
        spectrogram = SpectrogramPlot(label=label, model=model, nperseg=nperseg, noverlap=noverlap)
        controller = self.__add_view(label, spectrogram)

        def open_color_dialog() -> None:
            levels = spectrogram.img.getLevels()  # None until the first STFT frame is drawn
            chosen = _ask_range(
                controller,
                f"Color Levels | {label}",
                auto=spectrogram.color_levels_auto,
                pinned=(spectrogram.color_levels_min, spectrogram.color_levels_max),
                live=(float(levels[0]), float(levels[1])) if levels is not None else (0.0, 1.0),
                unit="dB",
            )
            if chosen:
                auto, lo, hi = chosen
                spectrogram.set_color_levels(auto, lo, hi)
                self.workspace.log(f"Color levels for '{label}' set to {'Auto' if auto else f'[{lo:g}, {hi:g}] dB'}")

        controller.add_header_button(
            "Color", "Pin exact colormap levels instead of the one-time auto snapshot", open_color_dialog
        )

        row = self.__row(label, has_curve=False)
        row.visibility_changed.connect(spectrogram.set_visible)
        return spectrogram, controller

    def add_xy_plot(self, title: str, x: str, y: str, trail: float | None) -> tuple[XYPlot, SignalController]:
        """See `Workspace.add_xy_plot`."""
        self.__check_new_title(title)
        signals = self.workspace.signals
        for label in (x, y):
            if label not in signals:
                raise ValueError(f"Unknown signal '{label}'")
        if x == y:
            raise ValueError("An XY plot needs two different signals")
        if trail is not None and trail <= 0:
            raise ValueError(f"trail must be positive (or None for the whole buffer), got {trail}")

        plot = XYPlot(title, signals[x], signals[y], trail=trail)
        controller = self.__add_view(title, plot)

        def ask_x_range() -> None:
            chosen = _ask_range(
                controller,
                f"X-Axis | {title}",
                auto=plot.x_range_auto,
                pinned=(plot.x_range_min, plot.x_range_max),
                live=plot.live_x_range(),
                unit=plot.x_model.unit,
            )
            if chosen:
                plot.set_x_range(*chosen)

        controller.add_header_button("X-Axis", "Pin an exact X-axis range instead of always auto-fitting", ask_x_range)
        equal = controller.add_header_button("Equal Axes", "Draw one unit as long on X as on Y", lambda: None)
        equal.setCheckable(True)
        equal.toggled.connect(plot.set_equal_axes)
        for label in (x, y):
            row = self.__row(label, has_curve=False)
            row.visibility_changed.connect(plot.set_visible)
        return plot, controller

    def add_histogram(
        self, label: str, bins: int, value_range: tuple[float, float] | None, title: str | None
    ) -> tuple[HistogramPlot, SignalController]:
        """See `Workspace.add_histogram`."""
        title = title or f"Histogram: {label}"
        self.__check_new_title(title)
        if label not in self.workspace.signals:
            raise ValueError(f"Unknown signal '{label}'")

        plot = HistogramPlot(title, self.workspace.signals[label], bins=bins, value_range=value_range)
        plot.set_bins(bins, value_range)  # Checks them
        controller = self.__add_view(title, plot)

        def ask_bins() -> None:
            dialog = HistogramDialog(bins=plot.bins, value_range=plot.value_range, parent=controller)
            if dialog.exec() == qtw.QDialog.DialogCode.Accepted:
                _, chosen_bins, chosen_range = dialog.get_values()
                plot.set_bins(chosen_bins, chosen_range)
                self.workspace.log(f"Histogram '{title}': {chosen_bins} bins")

        controller.add_header_button("Bins...", "How many bars, over which range of values", ask_bins)
        row = self.__row(label, has_curve=False)
        row.visibility_changed.connect(plot.set_visible)
        return plot, controller

    def __check_new_title(self, title: str) -> None:
        if title in self.workspace.views:
            raise ValueError(f"Plot '{title}' already exists")

    def __add_view(self, title: str, view: View) -> SignalController:
        """
        Register `view` under `title` with the current theme, time mode and
        downsampling applied, and give it a "Plot:" panel with a Y-Axis
        button. Returns the panel, for any buttons specific to the view.
        """
        workspace = self.workspace
        view.set_theme(*workspace.plot_theme)
        view.set_time_mode(workspace.time_mode)
        view.set_downsampling(workspace.downsampling)

        controller = SignalController(title=f"Plot: {title}")
        controller.add_header_button(
            "Y-Axis",
            "Pin an exact Y-axis range instead of always auto-fitting",
            lambda _checked=False: self.__open_y_axis_dialog(title),
        )
        workspace.views[title] = view
        workspace.controllers[title] = controller
        return controller

    def __open_y_axis_dialog(self, title: str) -> None:
        view = self.workspace.views[title]
        chosen = _ask_range(
            self.workspace.controllers[title],
            f"Y-Axis | {title}",
            auto=view.y_range_auto,
            pinned=(view.y_range_min, view.y_range_max),
            live=view.live_y_range(),
            unit=view.y_unit,
        )
        if chosen:
            auto, lo, hi = chosen
            view.set_y_range(auto, lo, hi)
            unit = f" {view.y_unit}" if view.y_unit else ""
            self.workspace.log(f"Y-axis for '{title}' set to {'Auto' if auto else f'[{lo:g}, {hi:g}]{unit}'}")

    # --- signals on plots --------------------------------------------------------------------

    def link(self, plot: str, label: str) -> None:
        """See `Workspace.link`."""
        workspace = self.workspace
        if plot not in workspace.views:
            raise ValueError(f"Unknown plot '{plot}'")
        if plot not in workspace.plots:
            raise ValueError(f"'{plot}' isn't a time-series plot; signals can only be linked to those")
        if label not in workspace.signals:
            raise ValueError(f"Unknown signal '{label}'")

        target_plot = workspace.plots[plot]
        if label in target_plot.curves:
            raise ValueError(f"'{label}' is already shown in '{plot}'")

        color = target_plot.add_signal(label=label, model=workspace.signals[label])
        row = self.__row(label, has_curve=True)
        row.visibility_changed.connect(target_plot.set_visible)
        row.color_changed.connect(target_plot.set_color)
        row.set_color(color)  # Sync the row's swatch to the color the plot assigned

    def __row(self, label: str, has_curve: bool) -> SignalRow:
        """
        Return this signal's one row in the flat `signal_list`, creating
        it on first use. A signal linked to a second/third plot reuses the
        same row rather than getting another one — that row's toggles just
        end up wired to every plot it's linked to (see `link`/`add_spectrogram`).
        """
        workspace = self.workspace
        rows = workspace.signal_list.rows
        if label in rows:
            row = rows[label]
            if has_curve:
                row.enable_curve()  # e.g. was spectrogram-only, now also plotted as a curve
            return row

        row = workspace.signal_list.add_signal(
            label=label,
            model=workspace.signals[label],
            derived=label in workspace.math_signals,
            has_curve=has_curve,
        )
        row.restart_requested.connect(workspace.restart_producer)
        row.remove_requested.connect(self.__remove_signal_from_row)
        row.unit_requested.connect(self.__set_unit_from_row)
        error = workspace.producer_error(label)
        if error is not None:
            row.set_error(error)
        return row

    def __remove_signal_from_row(self, label: str) -> None:
        try:
            self.workspace.remove_signal(label)
        except ValueError as e:  # E.g. another math signal uses it: say so (this is a Qt slot)
            self.workspace.log(str(e), level="warning")

    def __set_unit_from_row(self, label: str, unit: str) -> None:
        try:
            self.workspace.set_unit(label, unit)
        except ValueError as e:  # A Qt slot: report it, don't raise
            self.workspace.log(str(e), level="warning")

    def unlink(self, plot: str, label: str) -> None:
        """See `Workspace.unlink`."""
        if plot not in self.workspace.plots:
            raise ValueError(f"Unknown time-series plot '{plot}'")
        target = self.workspace.plots[plot]
        if label not in target.curves:
            raise ValueError(f"'{label}' isn't shown in '{plot}'")
        self.__disconnect_row(label, target)
        target.remove_signal(label)
        self.__drop_row_if_unshown(label)

    def remove_plot(self, title: str) -> None:
        """See `Workspace.remove_plot`."""
        workspace = self.workspace
        if title not in workspace.views:
            raise ValueError(f"Unknown plot '{title}'")
        view = workspace.views.pop(title)
        controller = workspace.controllers.pop(title)

        shown = view.signals
        for label in shown:
            self.__disconnect_row(label, view)
        if isinstance(view, TimeseriesPlot):
            view.setXLink(None)
            self.__relink_x_axes()
        for widget in (view, controller):
            widget.hide()
            widget.deleteLater()
        for label in shown:
            self.__drop_row_if_unshown(label)
        workspace.log(f"Removed plot '{title}'")

    def __disconnect_row(self, label: str, view: View) -> None:
        row = self.workspace.signal_list.rows[label]
        row.visibility_changed.disconnect(view.set_visible)
        if isinstance(view, TimeseriesPlot):
            row.color_changed.disconnect(view.set_color)

    def __drop_row_if_unshown(self, label: str) -> None:
        workspace = self.workspace
        shown = any(label in view.signals for view in workspace.views.values())
        if not shown and label in workspace.signal_list.rows:
            workspace.signal_list.remove_signal(label)

    def __relink_x_axes(self) -> None:
        """Link every time-series plot's X axis to the first one again (e.g. after the first was removed)."""
        plots = list(self.workspace.plots.values())
        for plot in plots:
            plot.setXLink(None)
        for plot in plots[1:]:
            plot.setXLink(plots[0])

    # --- cursors -----------------------------------------------------------------------------

    def set_cursors(self, plot: str, enabled: bool) -> None:
        """See `Workspace.set_cursors`."""
        if plot not in self.workspace.plots:
            raise ValueError(f"Unknown plot '{plot}'")
        self.workspace.plots[plot].set_cursors(enabled)
        if enabled:
            self.__refresh_cursor_readout(plot)  # Show a reading immediately, not just on the next tick/drag
        else:
            self.workspace.controllers[plot].set_cursor_readout(None)
        self.workspace.log(f"Cursors {'enabled' if enabled else 'disabled'} on '{plot}'")

    def refresh_cursor_readouts(self) -> None:
        """Every time plot's cursor readout, where cursors are on (every refresh)."""
        for title in self.workspace.plots:
            self.__refresh_cursor_readout(title)

    def __refresh_cursor_readout(self, title: str) -> None:
        """
        Recompute and display `title`'s cursor readout, if cursors are on.
        Called every refresh tick and also directly on every cursor drag, so
        it stays live even while the refresh timer is stopped (paused).
        """
        readout = self.workspace.plots[title].cursor_readout()
        if readout is not None:
            self.workspace.controllers[title].set_cursor_readout(self.__format_cursor_readout(readout))

    def __format_cursor_readout(self, readout: dict) -> str:
        freq_text = f"{readout['freq']:.3f} Hz" if readout["freq"] is not None else "—"
        lines = [f"Cursors  ΔT: {readout['dt']:+.4f} s   1/ΔT: {freq_text}"]
        for label, v in readout["signals"].items():
            name = self.workspace.signals[label].display_name
            lines.append(f"  {name}: A={v['value_a']:+.3f}  B={v['value_b']:+.3f}  Δ={v['delta']:+.3f}")
        return "\n".join(lines)
