"""
Domain layer: owns signals, plots, controllers and producers.

`Oscilab` (the QMainWindow) only builds window chrome and delegates all
registry/wiring logic here. Keeping this Qt-widget-light makes it possible
to test linking/producer logic without spinning up a full window.
"""

import csv
import re
from collections.abc import Sequence
from pathlib import Path

import h5py
import pyqtgraph.exporters as pg_exporters
from PyQt6 import QtCore as qtc
from PyQt6 import QtGui as qtg
from PyQt6 import QtWidgets as qtw
from scipy.io import savemat

from oscilab.axis_dialog import RangeDialog
from oscilab.math_signal import MathSignal
from oscilab.producers import DataProducer
from oscilab.signal_controller import SignalController
from oscilab.signal_model import SignalModel
from oscilab.spectrogram_plot import SpectrogramPlot
from oscilab.storage import Storage
from oscilab.timeseries_plot import TimeseriesPlot


def _safe_name(name: str) -> str:
    """Sanitize a plot/signal label for use as a filename fragment."""
    return re.sub(r"[^\w\-]+", "_", name).strip("_") or "signal"


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


class _PdfExporter(pg_exporters.Exporter):
    """
    pyqtgraph ships Image/SVG/HDF5 exporters plus a `PrintExporter` that
    always pops up an OS print dialog — nothing for a direct-to-file PDF.
    Renders straight to a `QPdfWriter` instead, same pattern `ImageExporter`
    uses internally (`getSourceRect()` + `setExportMode()` + scene.render()).
    """

    def export(self, file_name: str):
        source_rect = self.getSourceRect()
        writer = qtg.QPdfWriter(file_name)
        writer.setResolution(96)
        writer.setPageSize(
            qtg.QPageSize(qtc.QSizeF(source_rect.width(), source_rect.height()), qtg.QPageSize.Unit.Point)
        )
        writer.setPageMargins(qtc.QMarginsF(0, 0, 0, 0))
        target_rect = qtc.QRectF(0, 0, source_rect.width(), source_rect.height())

        painter = qtg.QPainter(writer)
        try:
            self.setExportMode(True, {"antialias": True})
            self.getScene().render(painter, target_rect, source_rect)
        finally:
            self.setExportMode(False)
            painter.end()


class Workspace:
    def __init__(self, log=None):
        self._log = log or (lambda *_args, **_kwargs: None)

        self.signals: dict[str, SignalModel] = {}
        self.math_signals: dict[str, MathSignal] = {}
        self.plots: dict[str, TimeseriesPlot] = {}
        self.spectrograms: dict[str, SpectrogramPlot] = {}
        self.signal_list = SignalController(title="Signals:")  # ONE flat row per signal, however many plots use it
        self.controllers: dict[str, SignalController] = {}  # per-plot: header buttons only (Y-Axis/Color), no rows
        self.producers: dict[str, DataProducer] = {}  # signal label -> the producer feeding it
        self._producer_errors: dict[str, str] = {}  # signal label -> why its producer stopped
        self._producers_with_buttons: set[DataProducer] = set()  # action buttons placed already
        self._plot_theme = ("#202f46", "#ffffff")  # (background, foreground), applied to new plots too
        self._duration = 10.0  # seconds of history kept/displayed, applied to new signals too
        self._time_mode = "global"  # "global" or "relative", applied to new plots too
        self._downsampling = False  # min-max curve downsampling, applied to new plots too

    def add_plot(self, title: str) -> tuple[TimeseriesPlot, SignalController]:
        if title in self.plots:
            raise ValueError(f"Plot '{title}' already exists")

        plot = TimeseriesPlot(title=title)
        plot.set_theme(*self._plot_theme)
        plot.set_time_mode(self._time_mode)
        plot.set_downsampling(self._downsampling)
        controller = SignalController(title="Plot:")

        def open_y_axis_dialog():
            chosen = _ask_range(
                controller,
                f"Y-Axis | {title}",
                auto=plot.y_range_auto,
                pinned=(plot.y_range_min, plot.y_range_max),
                live=tuple(plot.getViewBox().viewRange()[1]),
            )
            if chosen:
                auto, lo, hi = chosen
                plot.set_y_range(auto, lo, hi)
                self._log(f"Y-axis for '{title}' set to {'Auto' if auto else f'[{lo:g}, {hi:g}]'}")

        controller.add_header_button(
            "Y-Axis", "Pin an exact Y-axis range instead of always auto-fitting", open_y_axis_dialog
        )
        # Fires on every drag, so the readout stays in sync even while
        # paused (the timer-driven `refresh()` loop covers it while running).
        plot.cursor_changed.connect(lambda t=title: self.__refresh_cursor_readout(t))

        # Keep every plot's time axis (pan/zoom) in sync with the first one,
        # so scrubbing/zooming one plot moves them all together.
        if self.plots:
            plot.setXLink(next(iter(self.plots.values())))

        self.plots[title] = plot
        self.controllers[title] = controller
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
        if label in self.plots or label in self.spectrograms:
            raise ValueError(f"Plot '{label}' already exists")
        if label not in self.signals:
            raise ValueError(f"Unknown signal '{label}'")

        model = self.signals[label]
        spectrogram = SpectrogramPlot(label=label, model=model, nperseg=nperseg, noverlap=noverlap)
        spectrogram.set_theme(*self._plot_theme)
        spectrogram.set_time_mode(self._time_mode)

        controller = SignalController(title="Plot:")

        def open_y_axis_dialog():
            chosen = _ask_range(
                controller,
                f"Y-Axis | {label}",
                auto=spectrogram.freq_range_auto,
                pinned=(spectrogram.freq_range_min, spectrogram.freq_range_max),
                live=tuple(spectrogram.plot.getViewBox().viewRange()[1]),
                unit="Hz",
            )
            if chosen:
                auto, lo, hi = chosen
                spectrogram.set_y_range(auto, lo, hi)
                self._log(f"Frequency axis for '{label}' set to {'Auto' if auto else f'[{lo:g}, {hi:g}] Hz'}")

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
            "Y-Axis", "Pin an exact frequency range instead of always auto-fitting", open_y_axis_dialog
        )
        controller.add_header_button(
            "Color", "Pin exact colormap levels instead of the one-time auto snapshot", open_color_dialog
        )

        derived = label in self.math_signals
        row = self._link_row(label, model, derived=derived, has_curve=False)
        row.visibility_changed.connect(lambda _label, visible: spectrogram.setVisible(visible))

        self.spectrograms[label] = spectrogram
        self.controllers[label] = controller
        return spectrogram, controller

    def reg_signal(self, label: str, fs: float, storage: Storage = Storage.ring) -> SignalModel:
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
        if plot not in self.plots:
            raise ValueError(f"Unknown plot '{plot}'")
        if label not in self.signals:
            raise ValueError(f"Unknown signal '{label}'")

        model = self.signals[label]
        target_plot = self.plots[plot]
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
        if label in self._producer_errors:
            row.set_error(self._producer_errors[label])

        # A producer's buttons (e.g. "Reconnect") act on the whole producer,
        # not one channel — so they go on the first of its signals to get a row.
        producer = self.producers.get(label)
        if producer is not None and producer not in self._producers_with_buttons:
            self._producers_with_buttons.add(producer)
            for text, action in producer.actions().items():
                row.add_action_button(
                    text,
                    f"{type(producer).__name__}: {text}",
                    lambda _checked=False, p=producer, a=action, n=text: self.__queue_action(p, a, n),
                )
        return row

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

        models = [self.reg_signal(label, fs=producer.fs, storage=storage) for label in labels]

        def deliver(t, x):
            # Runs on the GUI thread: Qt queues signals emitted from the
            # producer's thread, so this never races the refresh loop.
            for model, row in zip(models, x, strict=True):
                model.add_data(t, row)

        producer.data_ready.connect(deliver)
        producer.error.connect(lambda message: self.__on_producer_error(labels, message))
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

    def __queue_action(self, producer: DataProducer, action, name: str):
        if not producer.queue_action(action):
            self._log(f"'{name}' ignored: {type(producer).__name__} isn't running", level="warning")

    def stop_producers(self):
        for producer in dict.fromkeys(self.producers.values()):  # a multi-channel producer is listed once per signal
            producer.stop()

    def refresh(self):
        for math_signal in self.math_signals.values():
            t, x = math_signal.compute()
            self.signals[math_signal.label].set_data(t, x)

        for title, plot in self.plots.items():
            plot.refresh()
            self.__refresh_cursor_readout(title)
        for spectrogram in self.spectrograms.values():
            spectrogram.refresh()
        self.signal_list.refresh()

    def reset_views(self):
        for plot in self.plots.values():
            plot.reset_view()
        for spectrogram in self.spectrograms.values():
            spectrogram.reset_view()

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
        readout = self.plots[title].cursor_readout()
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
        for plot in self.plots.values():
            plot.set_theme(bg, fg)
        for spectrogram in self.spectrograms.values():
            spectrogram.set_theme(bg, fg)

    def set_time_mode(self, mode: str):
        """
        Apply a time-axis mode ("global"/"relative") now, and remember it
        for plots added later.
        """
        self._time_mode = mode
        for plot in self.plots.values():
            plot.set_time_mode(mode)
        for spectrogram in self.spectrograms.values():
            spectrogram.set_time_mode(mode)

    def set_downsampling(self, enabled: bool):
        """
        Apply min-max curve downsampling now, and remember it for plots
        added later. Spectrograms are unaffected — their pixels are already
        an STFT-bin aggregate, not raw samples.
        """
        self._downsampling = enabled
        for plot in self.plots.values():
            plot.set_downsampling(enabled)
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
        """
        Every plot's/spectrogram's underlying `PlotItem`, keyed by title —
        shared by every image-format exporter below.
        """
        items = {title: plot.plotItem for title, plot in self.plots.items()}
        items.update({title: spec.plot.plotItem for title, spec in self.spectrograms.items()})
        return items

    def __export_plots(self, base_path, suffix: str, exporter_cls, format_name: str) -> list[Path]:
        """
        Shared driver for export_png/export_svg/export_pdf: run
        `exporter_cls(plot_item).export(path)` for every plot.

        With a single plot, `base_path` is used as-is; with several, each
        gets the plot's title appended to the filename.
        """
        base_path = Path(base_path)
        written = []
        plot_items = self.__plot_items()

        for title, plot_item in plot_items.items():
            path = self.__spread_path(base_path, title, len(plot_items), suffix)
            try:
                exporter_cls(plot_item).export(str(path))
            except Exception as e:  # noqa: BLE001 — see below; any exporter failure is reported, not fatal
                # pyqtgraph's SVGExporter is known to break against some Qt6
                # versions (its SVG path post-processor assumes older Qt SVG
                # serialization); don't let that crash the app or silently
                # produce a partial file — report it and keep going with any
                # other plots/formats.
                self._log(f"Failed to export '{title}' to {format_name}: {e}", level="error")
                continue
            written.append(path)

        self._log(f"Exported {len(written)} plot(s) to {format_name}")
        return written

    def export_png(self, base_path) -> list[Path]:
        """Export every plot to a PNG next to `base_path` (raster)."""
        return self.__export_plots(base_path, ".png", pg_exporters.ImageExporter, "PNG")

    def export_svg(self, base_path) -> list[Path]:
        """Export every plot to an SVG next to `base_path` (vector)."""
        return self.__export_plots(base_path, ".svg", pg_exporters.SVGExporter, "SVG")

    def export_pdf(self, base_path) -> list[Path]:
        """Export every plot to a PDF next to `base_path` (vector)."""
        return self.__export_plots(base_path, ".pdf", _PdfExporter, "PDF")

    def export_csv(self, base_path) -> list[Path]:
        """Export every signal's (time, value) buffer to a CSV next to `base_path`."""
        base_path = Path(base_path)
        written = []

        for label, model in self.signals.items():
            path = self.__spread_path(base_path, label, len(self.signals), ".csv")
            time_arr, data_arr = model.snapshot()
            with path.open("w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["time", "value"])
                writer.writerows(zip(time_arr, data_arr, strict=True))
            written.append(path)

        self._log(f"Exported {len(written)} signal(s) to CSV")
        return written

    def export_mat(self, path) -> Path:
        """
        Export every signal's (time, value) buffer into one MATLAB .mat file.

        Unlike CSV/PNG (one file per plot/signal), .mat/.h5 bundle every
        signal into a single file — the idiomatic layout for both formats.
        Variables are named `<signal>_time` / `<signal>_data`.
        """
        path = Path(path).with_suffix(".mat")
        mat_dict = {}
        for label, model in self.signals.items():
            safe = _safe_name(label)
            time_arr, data_arr = model.snapshot()
            mat_dict[f"{safe}_time"] = time_arr
            mat_dict[f"{safe}_data"] = data_arr

        savemat(str(path), mat_dict)
        self._log(f"Exported {len(self.signals)} signal(s) to {path.name}")
        return path

    def export_h5(self, path) -> Path:
        """
        Export every signal's (time, value) buffer into one HDF5 .h5 file,
        one group per signal (with `fs` as a group attribute).
        """
        path = Path(path).with_suffix(".h5")
        with h5py.File(path, "w") as f:
            for label, model in self.signals.items():
                time_arr, data_arr = model.snapshot()
                group = f.create_group(_safe_name(label))
                group.create_dataset("time", data=time_arr)
                group.create_dataset("data", data=data_arr)
                group.attrs["fs"] = model.fs
                group.attrs["label"] = label

        self._log(f"Exported {len(self.signals)} signal(s) to {path.name}")
        return path

    @staticmethod
    def __spread_path(base_path: Path, name: str, count: int, suffix: str) -> Path:
        """base_path as-is for a single item; base_path_<name><suffix> for several."""
        if count <= 1:
            return base_path.with_suffix(suffix)
        return base_path.with_name(f"{base_path.stem}_{_safe_name(name)}{suffix}")
