"""
Domain layer: owns signals, plots, controllers and producers.

`Oscilab` (the QMainWindow) only builds window chrome and delegates all
registry/wiring logic here. Keeping this Qt-widget-light makes it possible
to test linking/producer logic without spinning up a full window.

The workspace keeps the registries; two parts of it do the rest: its plots'
creation and removal (`oscilab.plotting`), and triggering them
(`oscilab.triggering`).
"""

from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from loguru import logger as default_logger
from PyQt6 import QtCore as qtc

from oscilab import exporters
from oscilab.display import TimeMode
from oscilab.histogram_plot import HistogramPlot
from oscilab.logic_plot import LogicPlot
from oscilab.math_signal import MathSignal, derived_unit
from oscilab.plotting import Plotting
from oscilab.producers import DataProducer
from oscilab.signal_controller import SignalController
from oscilab.signal_model import SignalModel
from oscilab.spectrogram_plot import SpectrogramPlot
from oscilab.timeseries_plot import TimeseriesPlot
from oscilab.trigger import TIME_PER_DIV, Acquisition, TriggerSettings
from oscilab.triggering import Triggering
from oscilab.view import View  # noqa: TC001 - imported at runtime, so the API docs can resolve `views`
from oscilab.xy_plot import XYPlot

if TYPE_CHECKING:
    from loguru import Logger


def signals_caption(labels: list[str]) -> str:
    """A producer's signals, named briefly: all of them up to three, else the first and last."""
    return ", ".join(labels) if len(labels) <= 3 else f"{labels[0]} … {labels[-1]}"


class _ProducerLink(qtc.QObject):
    """
    Receives a producer's signals on the GUI thread and feeds its signals.

    A child of the producer — whose QThread object lives on the GUI thread,
    unlike the thread it runs — so it's deleted along with the producer, and
    Qt drops any deliveries still queued for it then. Connecting plain Python
    callbacks instead let deliveries queued before a window closed be invoked
    after its producers had been garbage-collected: a hard crash.
    """

    def __init__(self, producer: DataProducer, models: list[SignalModel], on_error: Callable[[str], None]) -> None:
        super().__init__(producer)
        self._models = models
        self._on_error = on_error
        producer.data_ready.connect(self.deliver)
        producer.error.connect(self.report_error)

    @qtc.pyqtSlot(object, object)
    def deliver(self, t: np.ndarray, x: np.ndarray) -> None:
        # Queued from the producer's thread, so this never races the refresh loop
        for model, row in zip(self._models, x, strict=True):
            model.add_data(t, row)

    @qtc.pyqtSlot(str)
    def report_error(self, message: str) -> None:
        self._on_error(message)


class Workspace:
    def __init__(self, logger: "Logger | None" = None) -> None:
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
        self._time_mode: TimeMode = "global"  # "global" or "relative", applied to new plots too
        self._downsampling = True  # min-max curve downsampling, applied to new plots too
        self.trigger_changed: Callable[[], None] | None = None  # Told when a level line is dragged

        self.plotting = Plotting(self)
        self.triggering = Triggering(self)

    def log(self, message: str, level: str = "info") -> None:
        """Log `message`, as from where this was called — to the window's status bar too, in a window."""
        self._logger.opt(depth=1).log(level.upper(), message)

    @property
    def duration(self) -> float:
        """Seconds of history each signal keeps (see `set_duration`)."""
        return self._duration

    @property
    def time_mode(self) -> TimeMode:
        return self._time_mode

    @property
    def downsampling(self) -> bool:
        return self._downsampling

    @property
    def plot_theme(self) -> tuple[str, str]:
        """The plots' (background, foreground) colors (see `set_theme`)."""
        return self._plot_theme

    @property
    def plots(self) -> dict[str, TimeseriesPlot]:
        """The time-based plots signals are `link`ed to — time series and logic lanes — keyed by title."""
        return {title: view for title, view in self.views.items() if isinstance(view, TimeseriesPlot)}

    @property
    def spectrograms(self) -> dict[str, SpectrogramPlot]:
        """The spectrograms, keyed by their signal's label."""
        return {title: view for title, view in self.views.items() if isinstance(view, SpectrogramPlot)}

    # --- plots (`oscilab.plotting`) --------------------------------------------------------------

    def add_plot(self, title: str) -> tuple[TimeseriesPlot, SignalController]:
        """A time-series plot; `link` signals to it."""
        return self.plotting.add_plot(title)

    def add_logic_plot(self, title: str, threshold: float = 0.5) -> tuple[LogicPlot, SignalController]:
        """
        Logic lanes (`oscilab.logic_plot`): on/off signals in a lane each,
        high above `threshold`. `link` signals to it, as to a time-series plot.
        """
        return self.plotting.add_logic_plot(title, threshold)

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
        return self.plotting.add_spectrogram(label, nperseg, noverlap)

    def add_xy_plot(self, title: str, x: str, y: str, trail: float | None = None) -> tuple[XYPlot, SignalController]:
        """
        `y` against `x` (`oscilab.xy_plot`): the last `trail` seconds of
        them, or (None) everything in their buffers.
        """
        return self.plotting.add_xy_plot(title, x, y, trail)

    def add_histogram(
        self, label: str, bins: int = 50, value_range: tuple[float, float] | None = None, title: str | None = None
    ) -> tuple[HistogramPlot, SignalController]:
        """
        `label`'s value distribution (`oscilab.histogram_plot`), in `bins`
        bars over `value_range` — or the data's own range. Titled
        "Histogram: <label>" unless given one.
        """
        return self.plotting.add_histogram(label, bins, value_range, title)

    def link(self, plot: str, label: str) -> None:
        """Show a signal on a time-series plot. It gets one row in `signal_list`, however many plots show it."""
        self.plotting.link(plot, label)

    def unlink(self, plot: str, label: str) -> None:
        """Take a signal off a time-series plot. Its row goes once no plot shows it."""
        self.plotting.unlink(plot, label)

    def remove_plot(self, title: str) -> None:
        """
        Remove a plot, of any kind. Its signals stay; rows of signals no plot
        shows anymore go.
        """
        self.plotting.remove_plot(title)

    def set_cursors(self, plot: str, enabled: bool) -> None:
        """Toggle classic two-cursor measurement on `plot` (see `TimeseriesPlot.set_cursors`)."""
        self.plotting.set_cursors(plot, enabled)

    # --- signals ---------------------------------------------------------------------------------

    def register_signal(self, label: str, fs: float, unit: str = "") -> SignalModel:
        """
        Register a signal you feed yourself, via its `SignalModel.add_data`.
        Producer-fed signals are registered by `attach_producer` instead.
        `unit`: e.g. "V" (see `set_unit`).
        """
        if label in self.signals:
            raise ValueError(f"Signal '{label}' already registered")

        model = SignalModel(label=label, fs=fs, duration=self._duration, unit=unit)
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

        model_a, model_b = self.signals[signal_a], self.signals[signal_b]
        unit = derived_unit(operator, model_a.unit, model_b.unit)
        derived_model = SignalModel(label=label, fs=model_a.fs, duration=self._duration, unit=unit)

        self.signals[label] = derived_model
        self.math_signals[label] = MathSignal(label=label, model_a=model_a, model_b=model_b, operator=operator)
        self.log(f"Created math signal '{label}' = {signal_a} {operator} {signal_b}")
        return derived_model

    def set_unit(self, label: str, unit: str) -> None:
        """
        Change a signal's unit — e.g. "V", "°C"; "" for none. Legends, Y axes,
        its row and the cursor readouts follow, and so do the math signals
        computed from it: a math signal's unit always derives from its
        operands' (see `oscilab.math_signal.derived_unit`).
        """
        if label not in self.signals:
            raise ValueError(f"Unknown signal '{label}'")
        if label in self.math_signals:
            raise ValueError(f"'{label}' is a math signal: its unit follows from its operands'")

        unit = unit.strip()
        self.signals[label].unit = unit
        changed = {label}
        for math in self.math_signals.values():  # In definition order: operands come first
            if {math.model_a.label, math.model_b.label} & changed:
                self.signals[math.label].unit = derived_unit(math.operator, math.model_a.unit, math.model_b.unit)
                changed.add(math.label)

        for view in self.views.values():
            if changed.intersection(view.signals):
                view.update_units()
        for changed_label in changed:
            if changed_label in self.signal_list.rows:
                self.signal_list.rows[changed_label].update_unit()
        self.log(f"Unit of '{label}' set to {unit}" if unit else f"Unit of '{label}' removed")

    def remove_signal(self, label: str) -> None:
        """
        Remove a math signal, or one you registered yourself, from every plot
        and the list. A producer-fed signal can't go on its own — its producer
        keeps feeding it; detach the producer instead (`detach_producer`).
        """
        if label not in self.signals:
            raise ValueError(f"Unknown signal '{label}'")
        if label in self.producers:
            raise ValueError(f"'{label}' is fed by {type(self.producers[label]).__name__}; detach the producer instead")
        self.__refuse_if_used_by_math([label])

        self.__drop_signal(label)
        self.log(f"Removed signal '{label}'")

    def __refuse_if_used_by_math(self, labels: list[str]) -> None:
        users = [m.label for m in self.math_signals.values() if {m.model_a.label, m.model_b.label} & set(labels)]
        if users:
            names = ", ".join(repr(label) for label in labels)
            raise ValueError(f"{names} used by math signal(s) {', '.join(users)}; remove those first")

    def __drop_signal(self, label: str) -> None:
        """
        Take `label` off every plot and out of the list and the registries:
        off the time-based plots showing it, and the plots made for it
        (a spectrogram, histogram or XY plot of it) go.
        """
        for title, view in list(self.views.items()):
            if label not in view.signals:
                continue
            if isinstance(view, TimeseriesPlot):
                self.unlink(title, label)
            else:
                self.remove_plot(title)
        if self.trigger is not None and self.trigger.settings.source == label:
            self.set_trigger(None)  # Nothing left to trigger on
            self.log(f"Trigger turned off: its source '{label}' was removed", level="warning")
        self.math_signals.pop(label, None)
        del self.signals[label]

    # --- producers -------------------------------------------------------------------------------

    def attach_producer(
        self,
        producer: DataProducer,
        signals: str | Sequence[str],
        units: str | Sequence[str] | None = None,
    ) -> list[SignalModel]:
        """
        Register one signal per producer channel, at the producer's `fs`, and
        start the producer feeding them.

        `signals` names them in `producer.channels` order: a single label for
        a single-channel producer, otherwise one label per channel. `units`
        the same way — or one unit for every channel; by default, the
        producer's own `units`.
        """
        labels = [signals] if isinstance(signals, str) else list(signals)
        if len(labels) != len(producer.channels):
            raise ValueError(
                f"{type(producer).__name__} has {len(producer.channels)} channel(s) {producer.channels}, "
                f"but {len(labels)} signal name(s) were given"
            )
        channel_units = self.__channel_units(producer, units)
        # Check every name up front, so a bad call leaves nothing half-registered.
        if len(set(labels)) != len(labels):
            raise ValueError(f"Duplicate signal names in {labels}")
        for label in labels:
            if label in self.signals:
                raise ValueError(f"Signal '{label}' already registered")

        models = [
            self.register_signal(label, fs=producer.fs, unit=unit)
            for label, unit in zip(labels, channel_units, strict=True)
        ]

        _ProducerLink(producer, models, on_error=lambda message: self.__on_producer_error(labels, message))
        for label in labels:
            self.producers[label] = producer

        producer.start()
        self.log(f"Started {type(producer).__name__} for {', '.join(repr(label) for label in labels)}")
        return models

    @staticmethod
    def __channel_units(producer: DataProducer, units: str | Sequence[str] | None) -> list[str]:
        count = len(producer.channels)
        if units is None:
            units = producer.units or [""] * count
        if isinstance(units, str):
            return [units] * count
        if len(units) != count:
            raise ValueError(
                f"{type(producer).__name__} has {count} channel(s) {producer.channels}, "
                f"but {len(units)} unit(s) {tuple(units)} were given"
            )
        return list(units)

    def __on_producer_error(self, labels: list[str], message: str) -> None:
        # Named briefly, so the reason still fits the status bar for a producer of many channels
        producer = type(self.producers[labels[0]]).__name__
        self.log(f"{producer} for {signals_caption(labels)} stopped: {message}", level="error")
        for label in labels:
            self._producer_errors[label] = message
            if label in self.signal_list.rows:
                self.signal_list.rows[label].set_error(message)

    def start_producer(self, label: str) -> None:
        """Start the producer feeding `label` (and its other signals) again, if it's stopped."""
        producer = self.__producer_feeding(label)
        if not producer.running:
            self.__start(producer, "Started")

    def stop_producer(self, label: str) -> None:
        """Stop the producer feeding `label` (and its other signals); its data stays."""
        producer = self.__producer_feeding(label)
        if producer.running:
            producer.stop()
            self.log(f"Stopped {self.__describe(producer)}")

    def restart_producer(self, label: str) -> None:
        """
        Restart the producer feeding `label` (and its other signals), e.g.
        after it stopped with an error. A running producer is stopped first.
        """
        producer = self.__producer_feeding(label)
        producer.stop()
        self.__start(producer, "Restarted")

    def __start(self, producer: DataProducer, verb: str) -> None:
        producer.wait()  # An error's on_stop() may still be finishing
        for name in self.signals_fed_by(producer):
            self._producer_errors.pop(name, None)
            if name in self.signal_list.rows:
                self.signal_list.rows[name].clear_error()
        producer.start()
        self.log(f"{verb} {self.__describe(producer)}")

    def producer_state(self, label: str) -> str:
        """The producer feeding `label`: "running", "stopped", or "error" (stopped by one)."""
        producer = self.__producer_feeding(label)
        if producer.running:
            return "running"
        return "error" if label in self._producer_errors else "stopped"

    def producer_error(self, label: str) -> str | None:
        """Why the producer feeding `label` stopped, if an error stopped it — else None."""
        return self._producer_errors.get(label)

    def __producer_feeding(self, label: str) -> DataProducer:
        if label not in self.producers:
            raise ValueError(f"No producer feeds '{label}'")
        return self.producers[label]

    def signals_fed_by(self, producer: DataProducer) -> list[str]:
        """The signals `producer` feeds, in channel order."""
        return [label for label, p in self.producers.items() if p is producer]

    def __describe(self, producer: DataProducer) -> str:
        return f"{type(producer).__name__} for {', '.join(repr(name) for name in self.signals_fed_by(producer))}"

    def detach_producer(self, label: str) -> list[str]:
        """
        Stop the producer feeding `label` and remove it, along with every
        signal it feeds, from the plots, the list and this workspace. Refused
        while a math signal uses one of those signals. Returns their labels.
        """
        producer = self.__producer_feeding(label)
        labels = self.signals_fed_by(producer)
        self.__refuse_if_used_by_math(labels)

        producer.stop()
        link = producer.findChild(_ProducerLink)
        if link is not None:
            link.deleteLater()  # No more deliveries: the models are going
        for fed in labels:
            self.__drop_signal(fed)
            del self.producers[fed]
            self._producer_errors.pop(fed, None)
        self.log(f"Removed {self.__describe(producer)}")
        return labels

    def stop_producers(self) -> None:
        for producer in dict.fromkeys(self.producers.values()):  # a multi-channel producer is listed once per signal
            producer.stop()

    # --- triggering (`oscilab.triggering`) -------------------------------------------------------

    @property
    def trigger(self) -> Acquisition | None:
        """The trigger's acquisition while triggering is on (see `set_trigger`), else None."""
        return self.triggering.acquisition

    def set_trigger(self, settings: TriggerSettings | None) -> Acquisition | None:
        """
        Trigger the time-based plots (`oscilab.trigger`): each shows the
        record around a trigger, held until the next, instead of scrolling —
        all of them the same record, as a scope's channels. Starts running;
        while triggering, new settings keep Run / Single / Stop as it was.
        None turns triggering off, back to scrolling live. Returns the
        acquisition, to Run, Single, Stop or Force.
        """
        return self.triggering.set(settings)

    # --- refresh and view settings ---------------------------------------------------------------

    def refresh(self) -> None:
        for math_signal in self.math_signals.values():
            t, x = math_signal.compute()
            self.signals[math_signal.label].set_data(t, x)

        self.triggering.tick()
        self.__refresh_views()
        self.signal_list.refresh()

    def __refresh_views(self) -> None:
        now = self.__newest_shown_time()
        for view in self.views.values():
            view.refresh(now)
        self.plotting.refresh_cursor_readouts()

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

    def reset_views(self) -> None:
        for view in self.views.values():
            view.reset_view()

    def set_theme(self, bg: str, fg: str) -> None:
        """Apply a plot color theme now, and remember it for plots added later."""
        self._plot_theme = (bg, fg)
        for view in self.views.values():
            view.set_theme(bg, fg)

    def set_time_mode(self, mode: TimeMode) -> None:
        """
        Apply a time-axis mode ("global"/"relative") now, and remember it
        for plots added later.
        """
        self._time_mode = mode
        for view in self.views.values():
            view.set_time_mode(mode)
        self.__refresh_views()  # On the shared "now" right away, even while paused

    def set_downsampling(self, enabled: bool) -> None:
        """
        Apply min-max curve downsampling now, and remember it for plots
        added later. (Spectrograms ignore it — their pixels are already
        STFT-bin aggregates, not raw samples.)
        """
        self._downsampling = enabled
        for view in self.views.values():
            view.set_downsampling(enabled)
        self.log(f"Downsampling {'enabled' if enabled else 'disabled'}")

    def set_duration(self, duration: float) -> None:
        """
        Resize every signal's rolling buffer to hold `duration` seconds,
        and remember it for signals registered later. The trigger's record
        must fit: if it no longer does, it gets the most time per division
        that does (see `set_trigger`).
        """
        if duration <= 0:
            raise ValueError(f"duration must be positive, got {duration}")
        self._duration = duration
        for model in self.signals.values():
            model.set_duration(duration)
        self.log(f"Display duration set to {duration:g}s")
        self.__fit_trigger()

    def __fit_trigger(self) -> None:
        """A record longer than the history kept never completes: the trigger would wait forever."""
        trigger = self.trigger
        if trigger is None or trigger.settings.span <= self._duration:
            return
        fitting = [step for step in TIME_PER_DIV if replace(trigger.settings, time_per_div=step).span <= self._duration]
        if fitting:
            self.set_trigger(replace(trigger.settings, time_per_div=fitting[-1]))
            self.log(f"Trigger: {fitting[-1]:g} s a division now, so its record fits the history kept", level="warning")
        else:
            self.set_trigger(None)
            self.log("Trigger turned off: not even its shortest record fits the history kept", level="warning")
        if self.trigger_changed is not None:
            self.trigger_changed()

    # --- exports ---------------------------------------------------------------------------------

    def __plot_items(self) -> dict:
        """Every view's `PlotItem`, keyed by title — what the image exporters render."""
        return {title: view.plot_item for title, view in self.views.items()}

    def __export_images(self, base_path: str | Path, suffix: str) -> list[Path]:
        written, failed = exporters.export_images(self.__plot_items(), base_path, suffix)
        format_name = suffix.lstrip(".").upper()
        for title, error in failed.items():
            self.log(f"Failed to export '{title}' to {format_name}: {error}", level="error")
        self.log(f"Exported {len(written)} plot(s) to {format_name}", level="success")
        return written

    def export_png(self, base_path: str | Path) -> list[Path]:
        """Export every plot to a PNG next to `base_path` (raster)."""
        return self.__export_images(base_path, ".png")

    def export_svg(self, base_path: str | Path) -> list[Path]:
        """Export every plot to an SVG next to `base_path` (vector)."""
        return self.__export_images(base_path, ".svg")

    def export_pdf(self, base_path: str | Path) -> list[Path]:
        """Export every plot to a PDF next to `base_path` (vector)."""
        return self.__export_images(base_path, ".pdf")

    def export_csv(self, base_path: str | Path) -> list[Path]:
        """Export every signal's (time, value) buffer to a CSV next to `base_path`."""
        written = exporters.export_csv(self.signals, base_path)
        self.log(f"Exported {len(written)} signal(s) to CSV", level="success")
        return written

    def export_mat(self, path: str | Path) -> Path:
        """Export every signal's (time, value) buffer into one MATLAB .mat file."""
        written = exporters.export_mat(self.signals, path)
        self.log(f"Exported {len(self.signals)} signal(s) to {written.name}", level="success")
        return written

    def export_h5(self, path: str | Path) -> Path:
        """Export every signal's (time, value) buffer into one HDF5 .h5 file."""
        written = exporters.export_h5(self.signals, path)
        self.log(f"Exported {len(self.signals)} signal(s) to {written.name}", level="success")
        return written
