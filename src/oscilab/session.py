"""
Save and restore a whole Oscilab setup as JSON.

A session holds what it takes to rebuild a window: the display settings
(theme, FPS, duration, time mode, downsampling), the producers (class,
constructor arguments, and the signals they feed), hand-fed and math
signals, every plot with its links, pinned ranges and cursors, and each
signal row's color, visibility, filter, scaling and expanded state. Buffered
data isn't saved: producers start afresh.

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

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticSerializationError, to_jsonable_python

from oscilab.display import Fps, Theme, TimeMode
from oscilab.math_signal import OPERATORS
from oscilab.producers import DataProducer, resolve_producer
from oscilab.spectrogram_plot import SpectrogramPlot
from oscilab.storage import Storage
from oscilab.timeseries_plot import TimeseriesPlot

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


class ProducerSpec(_Model):
    type: str = Field(
        description='The producer class as "module:ClassName", e.g. "oscilab.producers:SyntheticProducer"'
    )
    args: dict[str, Any] = Field(default_factory=dict, description="Its constructor's keyword arguments")
    signals: list[str] = Field(min_length=1, description="One signal name per channel, in channel order")
    storage: Storage = Field(Storage.ring, description="Buffer backend for its signals")


class ManualSignal(_Model):
    """A signal registered with `register_signal` and fed by your own code."""

    label: str
    fs: float = Field(gt=0, description="Sampling rate in Hz")
    storage: Storage = Storage.ring


class MathSignalSpec(_Model):
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


class SpectrogramSpec(_Model):
    kind: Literal["spectrogram"] = "spectrogram"
    signal: str
    nperseg: int = Field(256, gt=1, description="STFT window length in samples")
    noverlap: int = Field(128, ge=0, description="Samples each window overlaps the previous one by")
    y_range: Range | None = Field(None, description="Frequency axis, Hz")
    color_levels: Range | None = Field(None, description="Colormap range, dB; null = auto snapshot")


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
    theme: Theme = "dark"
    fps: Fps = 30
    duration: float = Field(10.0, gt=0, description="Seconds of history kept and shown per signal")
    time_mode: TimeMode = "global"
    downsampling: bool = False
    producers: list[ProducerSpec] = Field(default_factory=list)
    manual_signals: list[ManualSignal] = Field(default_factory=list)
    math_signals: list[MathSignalSpec] = Field(default_factory=list, description="In order: each may use earlier ones")
    plots: list[Annotated[TimeseriesPlotSpec | SpectrogramSpec, Field(discriminator="kind")]] = Field(
        default_factory=list, description="Top to bottom"
    )
    signal_settings: dict[str, SignalSettings] = Field(default_factory=dict, description="Keyed by signal name")

    @model_validator(mode="after")
    def _check_references(self) -> Session:
        defined = self.__defined_signals()
        titles: set[str] = set()
        for plot in self.plots:
            title, shown = (plot.title, plot.signals) if plot.kind == "timeseries" else (plot.signal, [plot.signal])
            if title in titles:
                raise ValueError(f"Plot '{title}' is defined twice")
            titles.add(title)
            for label in shown:
                if label not in defined:
                    raise ValueError(f"Plot '{title}' shows '{label}', which no producer or signal provides")

        for label in self.signal_settings:
            if label not in defined:
                raise ValueError(f"signal_settings has '{label}', which no producer or signal provides")
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
        producers.append(
            ProducerSpec(
                type=producer.type_path(),
                args=_jsonable_args(producer),
                signals=labels,
                storage=ws.signals[labels[0]].storage,
            )
        )

    return Session(
        theme=window.theme,
        fps=window.fps,
        duration=ws.duration,
        time_mode=ws.time_mode,
        downsampling=ws.downsampling,
        producers=producers,
        manual_signals=[
            ManualSignal(label=label, fs=model.fs, storage=model.storage)
            for label, model in ws.signals.items()
            if label not in ws.producers and label not in ws.math_signals
        ],
        math_signals=[
            MathSignalSpec(label=m.label, operator=m.operator, a=m.model_a.label, b=m.model_b.label)
            for m in ws.math_signals.values()
        ],
        plots=[_capture_view(title, view) for title, view in ws.views.items()],
        signal_settings={label: _capture_row(row) for label, row in ws.signal_list.rows.items()},
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


def _capture_view(title: str, view: View) -> TimeseriesPlotSpec | SpectrogramSpec:
    y_range = _pinned(view.y_range_auto, view.y_range_min, view.y_range_max)
    if isinstance(view, TimeseriesPlot):
        region = view.cursor_region.getRegion() if view.cursor_region is not None else None
        return TimeseriesPlotSpec(title=title, signals=list(view.curves), y_range=y_range, cursors=region)
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


def load(window: Oscilab, path: str | Path) -> None:
    """Read a session file and rebuild it in `window` (which must be empty)."""
    restore(window, Session.model_validate_json(Path(path).read_text(encoding="utf-8")))


def restore(window: Oscilab, session: Session) -> None:
    """Rebuild `session` in `window`, which must still be empty."""
    ws = window.workspace
    if ws.signals or ws.views:
        raise ValueError("A session can only be loaded into an empty window")

    # Construct every producer before touching the window, so an unknown
    # type, bad arguments or a device that won't open leave it as it was.
    producers = [_build_producer(spec) for spec in session.producers]

    # Settings first, so the signals and plots created below start with them
    window.set_theme(session.theme)
    window.set_fps(session.fps)
    window.set_duration(session.duration)
    window.set_time_mode(session.time_mode)
    window.set_downsampling(session.downsampling)

    for producer, spec in zip(producers, session.producers, strict=True):
        window.attach_producer(producer, spec.signals, storage=spec.storage)
    for manual in session.manual_signals:
        window.register_signal(manual.label, manual.fs, storage=manual.storage)
    for math in session.math_signals:
        ws.add_math_signal(math.label, math.operator, math.a, math.b)

    for plot in session.plots:
        _restore_plot(window, plot)

    for label, settings in session.signal_settings.items():
        row = ws.signal_list.rows.get(label)
        if row is not None:  # Signals not shown in any plot have no row
            _restore_row(row, settings)


def _build_producer(spec: ProducerSpec) -> DataProducer:
    cls = resolve_producer(spec.type)
    try:
        return cls(**spec.args)
    except TypeError as e:  # Arguments that don't fit the constructor
        raise ValueError(f"Can't create {spec.type} with {spec.args}: {e}") from e


def _restore_plot(window: Oscilab, spec: TimeseriesPlotSpec | SpectrogramSpec) -> None:
    view: View
    if spec.kind == "timeseries":
        view = plot = window.add_timeseries_plot(spec.title)
        for label in spec.signals:
            window.link(plot=spec.title, label=label)
        if spec.cursors is not None:
            window.set_cursors(spec.title, enabled=True)
            plot.move_cursors(*spec.cursors)
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
