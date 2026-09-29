"""
Domain layer: owns signals, plots, controllers and producers.

`Oscilab` (the QMainWindow) only builds window chrome and delegates all
registry/wiring logic here. Keeping this Qt-widget-light makes it possible
to test linking/producer logic without spinning up a full window.
"""

from collections.abc import Sequence
from pathlib import Path

from loguru import logger as default_logger
from PyQt6 import QtCore as qtc
from PyQt6 import QtWidgets as qtw

from oscilab import exporters
from oscilab.dialogs import RangeDialog
from oscilab.math_signal import MathSignal
from oscilab.producers import DataProducer
from oscilab.signal_controller import SignalController
from oscilab.signal_model import SignalModel
from oscilab.spectrogram_plot import SpectrogramPlot
from oscilab.storage import Storage
from oscilab.timeseries_plot import TimeseriesPlot
from oscilab.view import View


def _ask_range(
    parent, title: str, auto: bool, pinned: tuple[float, float], live: tuple[float, float], unit: str = ""
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


class _ProducerLink(qtc.QObject):
    """
    Receives a producer's signals on the GUI thread and feeds its signals.

    A child of the producer — whose QThread object lives on the GUI thread,
    unlike the thread it runs — so it's deleted along with the producer, and
    Qt drops any deliveries still queued for it then. Connecting plain Python
    callbacks instead let deliveries queued before a window closed be invoked
    after its producers had been garbage-collected: a hard crash.
    """

    def __init__(self, producer: DataProducer, models: list[SignalModel], on_error):
        super().__init__(producer)
        self._models = models
        self._on_error = on_error
        producer.data_ready.connect(self.deliver)
        producer.error.connect(self.report_error)

    @qtc.pyqtSlot(object, object)
    def deliver(self, t, x):
        # Queued from the producer's thread, so this never races the refresh loop
        for model, row in zip(self._models, x, strict=True):
            model.add_data(t, row)

    @qtc.pyqtSlot(str)
    def report_error(self, message: str):
        self._on_error(message)


class Workspace:
    def __init__(self, logger=None):
        # Oscilab passes its window's logger, so messages reach its status bar
        self._logger = logger or default_logger

        self.signals: dict[str, SignalModel] = {}
        self.math_signals: dict[str, MathSignal] = {}
        self.views: dict[str, View] = {}  # Every plot, whatever its type, keyed by title
        # ONE flat row per signal, however many plots use it
        self.signal_list = SignalController(title="Signals:", logger=self._logger)
        self.controllers: dict[str, SignalController] = {}  # per-plot: header buttons only (Y-Axis/Color), no rows
        self.producers: dict[str, DataProducer] = {}  # signal label -> the producer feeding it
        self._producer_errors: dict[str, str] = {}  # signal label -> why its producer stopped
        self._plot_theme = ("#202f46", "#ffffff")  # (background, foreground), applied to new plots too
        self._duration = 10.0  # seconds of history kept/displayed, applied to new signals too
        self._time_mode = "global"  # "global" or "relative", applied to new plots too
        self._downsampling = False  # min-max curve downsampling, applied to new plots too

    def _log(self, message: str, level: str = "info"):
        self._logger.opt(depth=1).log(level.upper(), message)

    @property
    def duration(self) -> float:
        """Seconds of history each signal keeps (see `set_duration`)."""
        return self._duration

    @property
    def time_mode(self) -> str:
        return self._time_mode

    @property
    def downsampling(self) -> bool:
        return self._downsampling

    @property
    def plots(self) -> dict[str, TimeseriesPlot]:
        """The time-series plots (the ones signals are `link`ed to), keyed by title."""
        return {title: view for title, view in self.views.items() if isinstance(view, TimeseriesPlot)}

    @property
    def spectrograms(self) -> dict[str, SpectrogramPlot]:
        """The spectrograms, keyed by their signal's label."""
        return {title: view for title, view in self.views.items() if isinstance(view, SpectrogramPlot)}

    def add_plot(self, title: str) -> tuple[TimeseriesPlot, SignalController]:
        self.__check_new_title(title)
        first_plot = next(iter(self.plots.values()), None)

        plot = TimeseriesPlot(title=title)
        controller = self.__add_view(title, plot)
        # Fires on every drag, so the readout stays in sync even while
        # paused (the timer-driven `refresh()` loop covers it while running).
        plot.cursor_changed.connect(lambda t=title: self.__refresh_cursor_readout(t))

        # Keep every plot's time axis (pan/zoom) in sync with the first one,
        # so scrubbing/zooming one plot moves them all together.
        if first_plot is not None:
            plot.setXLink(first_plot)
        return plot, controller

    def add_spectrogram(
        self, label: str, nperseg: int = 256, noverlap: int = 128
    ) -> tuple[SpectrogramPlot, SignalController]:
        """
        Add a live STFT spectrogram plot bound directly to one signal.

        Keyed by `label` itself — a spectrogram is always bound to exactly
        one signal, so there's no separate title needed.

        Also gets a `SignalController` row like a regular plotted signal, so
        the spectrogram's source signal can still be filtered/scaled and its
        metrics watched — everything except curve color (nothing is drawn as
        a line) and "visible" toggles the whole spectrogram widget instead.
        """
        self.__check_new_title(label)
        if label not in self.signals:
            raise ValueError(f"Unknown signal '{label}'")

        model = self.signals[label]
        spectrogram = SpectrogramPlot(label=label, model=model, nperseg=nperseg, noverlap=noverlap)
        controller = self.__add_view(label, spectrogram)

        def open_color_dialog():
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
                self._log(f"Color levels for '{label}' set to {'Auto' if auto else f'[{lo:g}, {hi:g}] dB'}")

        controller.add_header_button(
            "Color", "Pin exact colormap levels instead of the one-time auto snapshot", open_color_dialog
        )

        derived = label in self.math_signals
        row = self._link_row(label, model, derived=derived, has_curve=False)
        row.visibility_changed.connect(spectrogram.set_visible)
        return spectrogram, controller

    def __check_new_title(self, title: str):
        if title in self.views:
            raise ValueError(f"Plot '{title}' already exists")

    def __add_view(self, title: str, view: View) -> SignalController:
        """
        Register `view` under `title` with the current theme, time mode and
        downsampling applied, and give it a "Plot:" panel with a Y-Axis
        button. Returns the panel, for any buttons specific to the view.
        """
        view.set_theme(*self._plot_theme)
        view.set_time_mode(self._time_mode)
        view.set_downsampling(self._downsampling)

        controller = SignalController(title=f"Plot: {title}")
        controller.add_header_button(
            "Y-Axis",
            "Pin an exact Y-axis range instead of always auto-fitting",
            lambda _checked=False: self.__open_y_axis_dialog(title),
        )
        self.views[title] = view
        self.controllers[title] = controller
        return controller

    def __open_y_axis_dialog(self, title: str):
        view = self.views[title]
        chosen = _ask_range(
            self.controllers[title],
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
            self._log(f"Y-axis for '{title}' set to {'Auto' if auto else f'[{lo:g}, {hi:g}]{unit}'}")

    def register_signal(self, label: str, fs: float, storage: Storage = Storage.ring) -> SignalModel:
        """
        Register a signal you feed yourself, via its `SignalModel.add_data`.
        Producer-fed signals are registered by `attach_producer` instead.
        """
        if label in self.signals:
            raise ValueError(f"Signal '{label}' already registered")

        model = SignalModel(label=label, fs=fs, duration=self._duration, storage=storage)
        self.signals[label] = model
        return model

    def add_math_signal(self, label: str, operator: str, signal_a: str, signal_b: str) -> SignalModel:
        """Register a new signal computed live as `signal_a <operator> signal_b`."""
        if label in self.signals:
            raise ValueError(f"Signal '{label}' already registered")
        if signal_a not in self.signals:
            raise ValueError(f"Unknown signal '{signal_a}'")
        if signal_b not in self.signals:
            raise ValueError(f"Unknown signal '{signal_b}'")

        model_a = self.signals[signal_a]
        derived_model = SignalModel(label=label, fs=model_a.fs, duration=self._duration)

        self.signals[label] = derived_model
        self.math_signals[label] = MathSignal(
            label=label, model_a=model_a, model_b=self.signals[signal_b], operator=operator
        )
        self._log(f"Created math signal '{label}' = {signal_a} {operator} {signal_b}")
        return derived_model

    def link(self, plot: str, label: str):
        if plot not in self.views:
            raise ValueError(f"Unknown plot '{plot}'")
        if plot not in self.plots:
            raise ValueError(f"'{plot}' isn't a time-series plot; signals can only be linked to those")
        if label not in self.signals:
            raise ValueError(f"Unknown signal '{label}'")

        model = self.signals[label]
        target_plot = self.plots[plot]
        if label in target_plot.curves:
            raise ValueError(f"'{label}' is already shown in '{plot}'")
        derived = label in self.math_signals

        color = target_plot.add_signal(label=label, model=model)
        row = self._link_row(label, model, derived=derived, has_curve=True)
        row.visibility_changed.connect(target_plot.set_visible)
        row.color_changed.connect(target_plot.set_color)
        row.set_color(color)  # Sync the row's swatch to the color the plot assigned

    def _link_row(self, label: str, model, derived: bool, has_curve: bool):
        """
        Return this signal's one row in the flat `signal_list`, creating
        it on first use. A signal linked to a second/third plot reuses the
        same row rather than getting another one — that row's toggles just
        end up wired to every plot it's linked to (see `link`/`add_spectrogram`).
        """
        if label in self.signal_list.rows:
            row = self.signal_list.rows[label]
            if has_curve:
                row.enable_curve()  # e.g. was spectrogram-only, now also plotted as a curve
            return row

        row = self.signal_list.add_signal(label=label, model=model, derived=derived, has_curve=has_curve)
        row.restart_requested.connect(self.restart_producer)
        row.remove_requested.connect(self.__remove_signal_from_row)
        if label in self._producer_errors:
            row.set_error(self._producer_errors[label])
        return row

    def unlink(self, plot: str, label: str):
        """Take a signal off a time-series plot. Its row goes once no plot shows it."""
        if plot not in self.plots:
            raise ValueError(f"Unknown time-series plot '{plot}'")
        target = self.plots[plot]
        if label not in target.curves:
            raise ValueError(f"'{label}' isn't shown in '{plot}'")
        self.__disconnect_row(label, target)
        target.remove_signal(label)
        self.__drop_row_if_unshown(label)

    def remove_plot(self, title: str):
        """
        Remove a plot — time series or spectrogram. Its signals stay; rows of
        signals no plot shows anymore go.
        """
        if title not in self.views:
            raise ValueError(f"Unknown plot '{title}'")
        view = self.views.pop(title)
        controller = self.controllers.pop(title)

        shown = list(view.curves) if isinstance(view, TimeseriesPlot) else [title]
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
        self._log(f"Removed plot '{title}'")

    def remove_signal(self, label: str):
        """
        Remove a math signal, or one you registered yourself, from every plot
        and the list. A producer-fed signal can't go on its own — its producer
        keeps feeding it; stop the producer instead.
        """
        if label not in self.signals:
            raise ValueError(f"Unknown signal '{label}'")
        if label in self.producers:
            raise ValueError(f"'{label}' is fed by {type(self.producers[label]).__name__}; stop the producer instead")
        users = [m.label for m in self.math_signals.values() if label in (m.model_a.label, m.model_b.label)]
        if users:
            raise ValueError(f"'{label}' is used by math signal(s) {', '.join(users)}; remove those first")

        for title, plot in list(self.plots.items()):
            if label in plot.curves:
                self.unlink(title, label)
        if label in self.spectrograms:
            self.remove_plot(label)
        self.math_signals.pop(label, None)
        del self.signals[label]
        self._log(f"Removed signal '{label}'")

    def __remove_signal_from_row(self, label: str):
        try:
            self.remove_signal(label)
        except ValueError as e:  # E.g. another math signal uses it: say so (this is a Qt slot)
            self._log(str(e), level="warning")

    def __disconnect_row(self, label: str, view: View):
        row = self.signal_list.rows[label]
        row.visibility_changed.disconnect(view.set_visible)
        if isinstance(view, TimeseriesPlot):
            row.color_changed.disconnect(view.set_color)

    def __drop_row_if_unshown(self, label: str):
        shown = label in self.spectrograms or any(label in plot.curves for plot in self.plots.values())
        if not shown and label in self.signal_list.rows:
            self.signal_list.remove_signal(label)

    def __relink_x_axes(self):
        """Link every time-series plot's X axis to the first one again (e.g. after the first was removed)."""
        plots = list(self.plots.values())
        for plot in plots:
            plot.setXLink(None)
        for plot in plots[1:]:
            plot.setXLink(plots[0])

    def attach_producer(
        self, producer: DataProducer, signals: str | Sequence[str], storage: Storage = Storage.ring
    ) -> list[SignalModel]:
        """
        Register one signal per producer channel, at the producer's `fs`, and
        start the producer feeding them.

        `signals` names them in `producer.channels` order: a single label for
        a single-channel producer, otherwise one label per channel.
        """
        labels = [signals] if isinstance(signals, str) else list(signals)
        if len(labels) != len(producer.channels):
            raise ValueError(
                f"{type(producer).__name__} has {len(producer.channels)} channel(s) {producer.channels}, "
                f"but {len(labels)} signal name(s) were given"
            )
        # Check every name up front, so a bad call leaves nothing half-registered.
        if len(set(labels)) != len(labels):
            raise ValueError(f"Duplicate signal names in {labels}")
        for label in labels:
            if label in self.signals:
                raise ValueError(f"Signal '{label}' already registered")

        models = [self.register_signal(label, fs=producer.fs, storage=storage) for label in labels]

        _ProducerLink(producer, models, on_error=lambda message: self.__on_producer_error(labels, message))
        for label in labels:
            self.producers[label] = producer

        producer.start()
        self._log(f"Started {type(producer).__name__} for {', '.join(repr(label) for label in labels)}")
        return models

    def __on_producer_error(self, labels: list[str], message: str):
        names = ", ".join(repr(label) for label in labels)
        self._log(f"Producer for {names} stopped: {message}", level="error")
        for label in labels:
            self._producer_errors[label] = message
            if label in self.signal_list.rows:
                self.signal_list.rows[label].set_error(message)

    def start_producer(self, label: str):
        """Start the producer feeding `label` (and its other signals) again, if it's stopped."""
        producer = self.__producer_feeding(label)
        if not producer.running:
            self.__start(producer, "Started")

    def stop_producer(self, label: str):
        """Stop the producer feeding `label` (and its other signals); its data stays."""
        producer = self.__producer_feeding(label)
        if producer.running:
            producer.stop()
            self._log(f"Stopped {self.__describe(producer)}")

    def restart_producer(self, label: str):
        """
        Restart the producer feeding `label` (and its other signals), e.g.
        after it stopped with an error. A running producer is stopped first.
        """
        producer = self.__producer_feeding(label)
        producer.stop()
        self.__start(producer, "Restarted")

    def __start(self, producer: DataProducer, verb: str):
        producer.wait()  # An error's on_stop() may still be finishing
        for name in self.signals_fed_by(producer):
            self._producer_errors.pop(name, None)
            if name in self.signal_list.rows:
                self.signal_list.rows[name].clear_error()
        producer.start()
        self._log(f"{verb} {self.__describe(producer)}")

    def producer_state(self, label: str) -> str:
        """The producer feeding `label`: "running", "stopped", or "error" (stopped by one)."""
        producer = self.__producer_feeding(label)
        if producer.running:
            return "running"
        return "error" if label in self._producer_errors else "stopped"

    def __producer_feeding(self, label: str) -> DataProducer:
        if label not in self.producers:
            raise ValueError(f"No producer feeds '{label}'")
        return self.producers[label]

    def signals_fed_by(self, producer: DataProducer) -> list[str]:
        """The signals `producer` feeds, in channel order."""
        return [label for label, p in self.producers.items() if p is producer]

    def __describe(self, producer: DataProducer) -> str:
        return f"{type(producer).__name__} for {', '.join(repr(name) for name in self.signals_fed_by(producer))}"

    def stop_producers(self):
        for producer in dict.fromkeys(self.producers.values()):  # a multi-channel producer is listed once per signal
            producer.stop()

    def refresh(self):
        for math_signal in self.math_signals.values():
            t, x = math_signal.compute()
            self.signals[math_signal.label].set_data(t, x)

        self.__refresh_views()
        self.signal_list.refresh()

    def __refresh_views(self):
        now = self.__newest_shown_time()
        for view in self.views.values():
            view.refresh(now)
        for title in self.plots:
            self.__refresh_cursor_readout(title)

    def __newest_shown_time(self) -> float | None:
        """
        "Now" for relative time: the newest timestamp among the signals shown
        in some plot. One value for every plot, so signals that arrive with
        different delays, on the same or on X-linked plots, stay in step.
        Signals not shown anywhere don't count (they might even run on
        another clock).
        """
        newest = [self.signals[label].time[-1] for label in self.signal_list.rows if self.signals[label].time]
        return max(newest, default=None)

    def reset_views(self):
        for view in self.views.values():
            view.reset_view()

    def set_cursors(self, plot: str, enabled: bool):
        """Toggle classic two-cursor measurement on `plot` (see `TimeseriesPlot.set_cursors`)."""
        if plot not in self.plots:
            raise ValueError(f"Unknown plot '{plot}'")
        self.plots[plot].set_cursors(enabled)
        if enabled:
            self.__refresh_cursor_readout(plot)  # Show a reading immediately, not just on the next tick/drag
        else:
            self.controllers[plot].set_cursor_readout(None)
        self._log(f"Cursors {'enabled' if enabled else 'disabled'} on '{plot}'")

    def __refresh_cursor_readout(self, title: str):
        """
        Recompute and display `title`'s cursor readout, if cursors are on.
        Called every refresh tick and also directly on every cursor drag, so
        it stays live even while the refresh timer is stopped (paused).
        """
        readout = self.views[title].cursor_readout()
        if readout is not None:
            self.controllers[title].set_cursor_readout(self.__format_cursor_readout(readout))

    @staticmethod
    def __format_cursor_readout(readout: dict) -> str:
        freq_text = f"{readout['freq']:.3f} Hz" if readout["freq"] is not None else "—"
        lines = [f"Cursors  ΔT: {readout['dt']:+.4f} s   1/ΔT: {freq_text}"]
        for label, v in readout["signals"].items():
            lines.append(f"  {label}: A={v['value_a']:+.3f}  B={v['value_b']:+.3f}  Δ={v['delta']:+.3f}")
        return "\n".join(lines)

    def set_theme(self, bg: str, fg: str):
        """Apply a plot color theme now, and remember it for plots added later."""
        self._plot_theme = (bg, fg)
        for view in self.views.values():
            view.set_theme(bg, fg)

    def set_time_mode(self, mode: str):
        """
        Apply a time-axis mode ("global"/"relative") now, and remember it
        for plots added later.
        """
        self._time_mode = mode
        for view in self.views.values():
            view.set_time_mode(mode)
        self.__refresh_views()  # On the shared "now" right away, even while paused

    def set_downsampling(self, enabled: bool):
        """
        Apply min-max curve downsampling now, and remember it for plots
        added later. (Spectrograms ignore it — their pixels are already
        STFT-bin aggregates, not raw samples.)
        """
        self._downsampling = enabled
        for view in self.views.values():
            view.set_downsampling(enabled)
        self._log(f"Downsampling {'enabled' if enabled else 'disabled'}")

    def set_duration(self, duration: float):
        """
        Resize every signal's rolling buffer to hold `duration` seconds,
        and remember it for signals registered later.
        """
        self._duration = duration
        for model in self.signals.values():
            model.set_duration(duration)
        self._log(f"Display duration set to {duration:g}s")

    def __plot_items(self) -> dict:
        """Every view's `PlotItem`, keyed by title — what the image exporters render."""
        return {title: view.plot_item for title, view in self.views.items()}

    def __export_images(self, base_path, suffix: str) -> list[Path]:
        written, failed = exporters.export_images(self.__plot_items(), base_path, suffix)
        format_name = suffix.lstrip(".").upper()
        for title, error in failed.items():
            self._log(f"Failed to export '{title}' to {format_name}: {error}", level="error")
        self._log(f"Exported {len(written)} plot(s) to {format_name}", level="success")
        return written

    def export_png(self, base_path) -> list[Path]:
        """Export every plot to a PNG next to `base_path` (raster)."""
        return self.__export_images(base_path, ".png")

    def export_svg(self, base_path) -> list[Path]:
        """Export every plot to an SVG next to `base_path` (vector)."""
        return self.__export_images(base_path, ".svg")

    def export_pdf(self, base_path) -> list[Path]:
        """Export every plot to a PDF next to `base_path` (vector)."""
        return self.__export_images(base_path, ".pdf")

    def export_csv(self, base_path) -> list[Path]:
        """Export every signal's (time, value) buffer to a CSV next to `base_path`."""
        written = exporters.export_csv(self.signals, base_path)
        self._log(f"Exported {len(written)} signal(s) to CSV", level="success")
        return written

    def export_mat(self, path) -> Path:
        """Export every signal's (time, value) buffer into one MATLAB .mat file."""
        path = exporters.export_mat(self.signals, path)
        self._log(f"Exported {len(self.signals)} signal(s) to {path.name}", level="success")
        return path

    def export_h5(self, path) -> Path:
        """Export every signal's (time, value) buffer into one HDF5 .h5 file."""
        path = exporters.export_h5(self.signals, path)
        self._log(f"Exported {len(self.signals)} signal(s) to {path.name}", level="success")
        return path
