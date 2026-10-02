"""
Save and restore a whole Oscilab setup as JSON.

A session holds what it takes to rebuild a window: its size and position,
the display settings (theme, FPS, duration, time mode, downsampling), Save
Stream's settings, the producers (class, constructor arguments, the signals
they feed and their toggles' states), hand-fed and math signals with their
units, every plot with its links, pinned ranges and cursors, each signal
row's color, visibility, filter, scaling and expanded state, and your own
toolbar toggles' states. Buffered data isn't saved: producers start afresh.

The pydantic models below *are* the file format — their fields and
descriptions document it, and `Session.model_json_schema()` (or
`oscilab --schema`) prints it as a JSON Schema. Loading checks the whole
file — types, unknown keys (typos), and references between its parts, like
a plot showing a signal nothing provides — before building anything.

Producers are found by import path (`"module:ClassName"`), so your own
producer classes can be saved as well, as long as they live in an importable
module (not the script you run) and take JSON-friendly constructor
arguments. Loading a session imports those modules: only load files you
trust.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticSerializationError, to_jsonable_python

from oscilab.display import Fps, Theme, TimeMode
from oscilab.histogram_plot import HistogramPlot
from oscilab.logic_plot import LogicPlot
from oscilab.math_signal import OPERATORS
from oscilab.producers import DataProducer, resolve_producer
from oscilab.spectrogram_plot import SpectrogramPlot
from oscilab.timeseries_plot import TimeseriesPlot
from oscilab.trigger import Coupling, Slope, TriggerSettings, TriggerType, WindowCondition
from oscilab.xy_plot import XYPlot

if TYPE_CHECKING:
    from oscilab.oscilab import Oscilab
    from oscilab.signal_controller import SignalRow
    from oscilab.view import View


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")  # A misspelled key is an error, not silently ignored


class Range(_Model):
    """A pinned axis range. Where a range is optional, `null` means auto-fit."""

    min: float
    max: float


class Filter(_Model):
    """Low-pass Butterworth filter."""

    cutoff: float = Field(gt=0, description="Hz; must be below the signal's Nyquist frequency (fs/2)")
    order: int = Field(4, ge=1, le=20)


class Scaling(_Model):
    """output = (raw - offset) * gain"""

    gain: float = 1.0
    offset: float = 0.0


def _without_storage(data: Any) -> Any:
    """
    A producer's or signal's entry without "storage": files saved before the
    ring buffer was the only one named a buffer to use. Ignored, so they
    still open.
    """
    if isinstance(data, dict):
        return {key: value for key, value in data.items() if key != "storage"}
    return data


class ProducerSpec(_Model):
    type: str = Field(
        description='The producer class as "module:ClassName", e.g. "oscilab.producers:SyntheticProducer"'
    )
    args: dict[str, Any] = Field(default_factory=dict, description="Its constructor's keyword arguments")
    signals: list[str] = Field(min_length=1, description="One signal name per channel, in channel order")
    units: list[str] | None = Field(
        None, description='One unit per signal, in the same order ("" for none); null = the producer\'s own'
    )
    toggles: dict[str, bool] = Field(
        default_factory=dict, description="Its @toggle functions' states, keyed by method name"
    )

    @model_validator(mode="before")
    @classmethod
    def _from_old_files(cls, data: Any) -> Any:
        return _without_storage(data)

    @model_validator(mode="after")
    def _check_units(self) -> ProducerSpec:
        if self.units is not None and len(self.units) != len(self.signals):
            raise ValueError(f"{len(self.signals)} signal(s) {self.signals}, but {len(self.units)} unit(s)")
        return self


class ManualSignal(_Model):
    """A signal registered with `register_signal` and fed by your own code."""

    label: str
    fs: float = Field(gt=0, description="Sampling rate in Hz")
    unit: str = Field("", description='E.g. "V"; "" for none')

    @model_validator(mode="before")
    @classmethod
    def _from_old_files(cls, data: Any) -> Any:
        return _without_storage(data)


class WindowSpec(_Model):
    """The main window's inside (without the frame), in desktop pixels."""

    x: int
    y: int
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    maximized: bool = Field(False, description="x/y/width/height are then where it goes when restored down")


class RecordingSpec(_Model):
    """Save Stream's settings — not whether it's on: opening a session never starts a recording."""

    path: str = Field(description="Each recording's files are named after this, and the time it started")
    max_file_mb: float | None = Field(100.0, gt=0, description="The size of file it moves on at; null = no limit")


class TriggerSpec(_Model):
    """The trigger (see `oscilab.trigger`), saved while it's on; it runs when the session opens."""

    source: str = Field(description="The signal it triggers on")
    type: TriggerType = "edge"
    slope: Slope = "rising"
    level: float = 0.0
    hysteresis: float = Field(0.1, ge=0)
    low: float = Field(-0.5, description="A window trigger's range")
    high: float = 0.5
    condition: WindowCondition = "entering"
    coupling: Coupling = "dc"
    holdoff: float = Field(0.0, ge=0, description="Seconds")
    delay: float = Field(0.0, ge=0, description="Seconds")
    time_per_div: float = Field(0.01, gt=0, description="Seconds; a record is ten divisions")
    reference: float = Field(0.2, ge=0, le=1, description="Where the trigger sits in the record, 0 to 1")
    auto: bool = Field(True, description="A record anyway when no trigger comes; false: Normal")


class MathSignalSpec(_Model):
    """A math signal. Its unit follows from its operands'."""

    label: str
    operator: Literal[OPERATORS]  # type: ignore[valid-type]  # the operators the Math menu offers
    a: str = Field(description="First operand: a signal defined earlier in the session")
    b: str = Field(description="Second operand: a signal defined earlier in the session")


class TimeseriesPlotSpec(_Model):
    kind: Literal["timeseries"] = "timeseries"
    title: str
    signals: list[str] = Field(default_factory=list, description="Linked signals, in legend order")
    y_range: Range | None = None
    cursors: tuple[float, float] | None = Field(None, description="Cursor A/B positions on the time axis; null = off")

    @property
    def shown(self) -> list[str]:
        return self.signals


class LogicPlotSpec(_Model):
    """Logic lanes: on/off signals in a lane each."""

    kind: Literal["logic"] = "logic"
    title: str
    signals: list[str] = Field(default_factory=list, description="Linked signals, a lane each, top to bottom")
    threshold: float = Field(0.5, description="A value above it is high")
    y_range: Range | None = None
    cursors: tuple[float, float] | None = Field(None, description="Cursor A/B positions on the time axis; null = off")

    @property
    def shown(self) -> list[str]:
        return self.signals


class XYPlotSpec(_Model):
    """One signal against another."""

    kind: Literal["xy"] = "xy"
    title: str
    x: str
    y: str
    trail: float | None = Field(None, gt=0, description="Seconds of history drawn; null = the whole buffer")
    equal_axes: bool = False
    x_range: Range | None = None
    y_range: Range | None = None

    @property
    def shown(self) -> list[str]:
        return [self.x, self.y]


class HistogramSpec(_Model):
    """One signal's value distribution."""

    kind: Literal["histogram"] = "histogram"
    title: str
    signal: str
    bins: int = Field(50, ge=1, le=10_000)
    value_range: Range | None = Field(None, description="The range the bins cover; null = the data's own")
    y_range: Range | None = Field(None, description="The count axis")

    @property
    def shown(self) -> list[str]:
        return [self.signal]


class SpectrogramSpec(_Model):
    kind: Literal["spectrogram"] = "spectrogram"
    signal: str
    nperseg: int = Field(256, gt=1, description="STFT window length in samples")
    noverlap: int = Field(128, ge=0, description="Samples each window overlaps the previous one by")
    y_range: Range | None = Field(None, description="Frequency axis, Hz")
    color_levels: Range | None = Field(None, description="Colormap range, dB; null = auto snapshot")

    @property
    def title(self) -> str:
        return self.signal  # A spectrogram is named after its signal

    @property
    def shown(self) -> list[str]:
        return [self.signal]


type PlotSpec = TimeseriesPlotSpec | LogicPlotSpec | XYPlotSpec | HistogramSpec | SpectrogramSpec


class SignalSettings(_Model):
    """A signal row's state. Only signals shown in some plot have a row."""

    color: str | None = Field(None, description='Curve color, e.g. "#ff5555"; null = the plot\'s default')
    visible: bool = True
    expanded: bool = Field(False, description="Row details (metrics, Filter/Scaling) open")
    filter: Filter | None = Field(None, description="null = off")
    scaling: Scaling | None = Field(None, description="null = off")


class Session(_Model):
    """An Oscilab setup: display settings, data sources, plots and per-signal settings."""

    version: Literal[1] = 1
    window: WindowSpec | None = Field(None, description="null = the default size and position")
    theme: Theme = "dark"
    fps: Fps = 30
    duration: float = Field(10.0, gt=0, description="Seconds of history kept and shown per signal")
    time_mode: TimeMode = "global"
    downsampling: bool = True
    recording: RecordingSpec | None = Field(None, description="null = the default settings")
    producers: list[ProducerSpec] = Field(default_factory=list)
    manual_signals: list[ManualSignal] = Field(default_factory=list)
    math_signals: list[MathSignalSpec] = Field(default_factory=list, description="In order: each may use earlier ones")
    plots: list[
        Annotated[
            TimeseriesPlotSpec | LogicPlotSpec | XYPlotSpec | HistogramSpec | SpectrogramSpec,
            Field(discriminator="kind"),
        ]
    ] = Field(default_factory=list, description="Top to bottom")
    signal_settings: dict[str, SignalSettings] = Field(default_factory=dict, description="Keyed by signal name")
    toggles: dict[str, bool] = Field(
        default_factory=dict,
        description='Your toolbar toggles\' states: add_toolbar ones keyed "ClassName.method", '
        "add_toolbar_toggle ones by their text",
    )
    trigger: TriggerSpec | None = Field(None, description="null = not triggering: the plots scroll")

    @model_validator(mode="after")
    def _check_references(self) -> Session:
        defined = self.__defined_signals()
        titles: set[str] = set()
        for plot in self.plots:
            title = plot.title
            if title in titles:
                raise ValueError(f"Plot '{title}' is defined twice")
            titles.add(title)
            for label in plot.shown:
                if label not in defined:
                    raise ValueError(f"Plot '{title}' shows '{label}', which no producer or signal provides")

        for label in self.signal_settings:
            if label not in defined:
                raise ValueError(f"signal_settings has '{label}', which no producer or signal provides")
        if self.trigger is not None and self.trigger.source not in defined:
            raise ValueError(f"The trigger's source '{self.trigger.source}' isn't a signal the session provides")
        return self

    def __defined_signals(self) -> set[str]:
        """Every signal name the session provides; each defined once, math operands before use."""
        defined: set[str] = set()

        def define(label: str) -> None:
            if label in defined:
                raise ValueError(f"Signal '{label}' is defined twice")
            defined.add(label)

        for label in (label for producer in self.producers for label in producer.signals):
            define(label)
        for signal in self.manual_signals:
            define(signal.label)
        for math in self.math_signals:
            for operand in (math.a, math.b):
                if operand not in defined:
                    raise ValueError(f"Math signal '{math.label}' uses '{operand}', which isn't defined before it")
            define(math.label)
        return defined


# --- saving ---------------------------------------------------------------


def capture(window: Oscilab) -> Session:
    """The current setup of `window`, as a Session."""
    ws = window.workspace

    producers = []
    for producer in dict.fromkeys(ws.producers.values()):  # Listed once per signal it feeds
        labels = [label for label, p in ws.producers.items() if p is producer]
        units = [ws.signals[label].unit for label in labels]
        own_units = list(producer.units) or [""] * len(labels)
        producers.append(
            ProducerSpec(
                type=producer.type_path(),
                args=_jsonable_args(producer),
                signals=labels,
                units=None if units == own_units else units,
                toggles=window.producer_toggle_states(producer),
            )
        )

    x, y, width, height, maximized = window.window_geometry()
    recording_path, max_file_mb = window.recording_options
    return Session(
        window=WindowSpec(x=x, y=y, width=width, height=height, maximized=maximized),
        theme=window.theme,
        fps=window.fps,
        duration=ws.duration,
        time_mode=ws.time_mode,
        downsampling=ws.downsampling,
        recording=RecordingSpec(path=str(recording_path), max_file_mb=max_file_mb),
        producers=producers,
        manual_signals=[
            ManualSignal(label=label, fs=model.fs, unit=model.unit)
            for label, model in ws.signals.items()
            if label not in ws.producers and label not in ws.math_signals
        ],
        math_signals=[
            MathSignalSpec(label=m.label, operator=m.operator, a=m.model_a.label, b=m.model_b.label)
            for m in ws.math_signals.values()
        ],
        plots=[_capture_view(title, view) for title, view in ws.views.items()],
        signal_settings={label: _capture_row(row) for label, row in ws.signal_list.rows.items()},
        toggles=window.toggle_states(),
        trigger=TriggerSpec(**asdict(window.trigger_settings)) if window.trigger_settings is not None else None,
    )


def _jsonable_args(producer: DataProducer) -> dict[str, Any]:
    args = {}
    for name, value in producer.config().items():
        try:
            args[name] = to_jsonable_python(value)
        except PydanticSerializationError as e:
            raise ValueError(
                f"Can't save {type(producer).__name__}: its argument {name}={value!r} "
                f"({type(value).__name__}) can't be written as JSON"
            ) from e
    return args


def _pinned(auto: bool, lo: float, hi: float) -> Range | None:
    return None if auto else Range(min=lo, max=hi)


def _capture_view(title: str, view: View) -> PlotSpec:
    y_range = _pinned(view.y_range_auto, view.y_range_min, view.y_range_max)
    if isinstance(view, TimeseriesPlot):  # Logic lanes too
        region = view.cursor_region.getRegion() if view.cursor_region is not None else None
        if isinstance(view, LogicPlot):
            return LogicPlotSpec(
                title=title, signals=view.signals, threshold=view.threshold, y_range=y_range, cursors=region
            )
        return TimeseriesPlotSpec(title=title, signals=view.signals, y_range=y_range, cursors=region)
    if isinstance(view, XYPlot):
        return XYPlotSpec(
            title=title,
            x=view.x_model.label,
            y=view.y_model.label,
            trail=view.trail,
            equal_axes=view.equal_axes,
            x_range=_pinned(view.x_range_auto, view.x_range_min, view.x_range_max),
            y_range=y_range,
        )
    if isinstance(view, HistogramPlot):
        value_range = Range(min=view.value_range[0], max=view.value_range[1]) if view.value_range else None
        return HistogramSpec(
            title=title, signal=view.model.label, bins=view.bins, value_range=value_range, y_range=y_range
        )
    if isinstance(view, SpectrogramPlot):
        return SpectrogramSpec(
            signal=title,
            nperseg=view.nperseg,
            noverlap=view.noverlap,
            y_range=y_range,
            color_levels=_pinned(view.color_levels_auto, view.color_levels_min, view.color_levels_max),
        )
    raise TypeError(f"Don't know how to save a {type(view).__name__}")


def _capture_row(row: SignalRow) -> SignalSettings:
    filt, scale = row.filter_settings, row.scaling_settings
    return SignalSettings(
        color=row.color if row.has_curve else None,
        visible=row.visible,
        expanded=row.expanded,
        filter=Filter(cutoff=filt["cutoff"], order=filt["order"]) if filt["enabled"] else None,
        scaling=Scaling(gain=scale["gain"], offset=scale["offset"]) if scale["enabled"] else None,
    )


def save(window: Oscilab, path: str | Path) -> Path:
    """Write `window`'s setup to `path` (`.json` is added if it has no suffix)."""
    path = Path(path)
    if not path.suffix:
        path = path.with_suffix(".json")
    path.write_text(capture(window).model_dump_json(indent=2), encoding="utf-8")
    return path


# --- loading --------------------------------------------------------------


def load(window: Oscilab, path: str | Path, replace: bool = False) -> None:
    """Read a session file and rebuild it in `window` (see `restore`)."""
    restore(window, Session.model_validate_json(Path(path).read_text(encoding="utf-8")), replace=replace)


def restore(window: Oscilab, session: Session, replace: bool = False) -> None:
    """
    Rebuild `session` in `window`, which must still be empty — unless
    `replace`, which clears it first (see `Oscilab.load_session`).
    """
    ws = window.workspace
    in_use = bool(ws.signals or ws.views)
    if in_use and not replace:
        raise ValueError("A session can only be loaded into an empty window, unless it replaces what's there")

    # Construct every producer before touching the window, so an unknown
    # type, bad arguments or a device that won't open leave it as it was.
    producers = _build_producers(window, session.producers, pause_current=in_use)
    if in_use:
        window.clear()

    _restore_settings(window, session)  # First, so the signals and plots created below start with them

    for producer, spec in zip(producers, session.producers, strict=True):
        window.attach_producer(producer, spec.signals, units=spec.units)
        window.set_producer_toggle_states(producer, spec.toggles)
    for manual in session.manual_signals:
        window.register_signal(manual.label, manual.fs, unit=manual.unit)
    for math in session.math_signals:
        ws.add_math_signal(math.label, math.operator, math.a, math.b)

    for plot in session.plots:
        _restore_plot(window, plot)

    for label, settings in session.signal_settings.items():
        row = ws.signal_list.rows.get(label)
        if row is not None:  # Signals not shown in any plot have no row
            _restore_row(row, settings)

    window.set_toggle_states(session.toggles)  # Ones your code adds later switch when it does
    if session.trigger is not None:
        window.set_trigger(TriggerSettings(**session.trigger.model_dump()))


def _restore_settings(window: Oscilab, session: Session) -> None:
    if session.window is not None:
        geometry = session.window
        window.set_window_geometry(geometry.x, geometry.y, geometry.width, geometry.height, geometry.maximized)
    window.set_theme(session.theme)
    window.set_fps(session.fps)
    window.set_duration(session.duration)
    window.set_time_mode(session.time_mode)
    window.set_downsampling(session.downsampling)
    if session.recording is not None:
        window.set_recording_options(session.recording.path, session.recording.max_file_mb)


def _build_producers(window: Oscilab, specs: list[ProducerSpec], pause_current: bool) -> list[DataProducer]:
    """
    Construct the session's producers. With `pause_current`, the window's
    running producers stop first — one of them may hold a device (a serial
    port, say) that the session's producers open too — and start again if
    one of the new ones can't be built.
    """
    ws = window.workspace
    running = [ws.signals_fed_by(p)[0] for p in dict.fromkeys(ws.producers.values()) if p.running]
    paused = running if pause_current else []
    for label in paused:
        ws.stop_producer(label)
    try:
        return [_build_producer(spec) for spec in specs]
    except Exception:
        for label in paused:
            ws.start_producer(label)
        raise


def _build_producer(spec: ProducerSpec) -> DataProducer:
    cls = resolve_producer(spec.type)
    try:
        return cls(**spec.args)
    except TypeError as e:  # Arguments that don't fit the constructor
        raise ValueError(f"Can't create {spec.type} with {spec.args}: {e}") from e


def _restore_plot(window: Oscilab, spec: PlotSpec) -> None:
    view: View
    if isinstance(spec, TimeseriesPlotSpec | LogicPlotSpec):
        if isinstance(spec, LogicPlotSpec):
            view = plot = window.add_logic_plot(spec.title, threshold=spec.threshold)
        else:
            view = plot = window.add_timeseries_plot(spec.title)
        for label in spec.signals:
            window.link(plot=spec.title, label=label)
        if spec.cursors is not None:
            window.set_cursors(spec.title, enabled=True)
            plot.move_cursors(*spec.cursors)
    elif isinstance(spec, XYPlotSpec):
        view = xy = window.add_xy_plot(spec.title, spec.x, spec.y, trail=spec.trail)
        xy.set_equal_axes(spec.equal_axes)
        if spec.x_range is not None:
            xy.set_x_range(False, spec.x_range.min, spec.x_range.max)
    elif isinstance(spec, HistogramSpec):
        value_range = (spec.value_range.min, spec.value_range.max) if spec.value_range else None
        view = window.add_histogram(spec.signal, bins=spec.bins, value_range=value_range, title=spec.title)
    else:
        view = spectrogram = window.add_spectrogram_plot(spec.signal, nperseg=spec.nperseg, noverlap=spec.noverlap)
        if spec.color_levels is not None:
            spectrogram.set_color_levels(False, spec.color_levels.min, spec.color_levels.max)
    if spec.y_range is not None:
        view.set_y_range(False, spec.y_range.min, spec.y_range.max)


def _restore_row(row: SignalRow, settings: SignalSettings) -> None:
    if settings.color is not None and row.has_curve:
        row.set_color(settings.color)
    row.set_visible(settings.visible)
    if settings.filter is not None:
        row.apply_filter(True, settings.filter.cutoff, settings.filter.order)
    if settings.scaling is not None:
        row.apply_scaling(True, settings.scaling.gain, settings.scaling.offset)
    row.set_expanded(settings.expanded)
