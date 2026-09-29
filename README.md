# Oscilab

A desktop oscilloscope / signal-visualization application built with
PyQt6 and pyqtgraph. Stream live or replayed signals into scrolling
time-series plots and live STFT spectrograms, with per-signal filtering,
scaling, derived (math) channels, and export to PNG/SVG/PDF/CSV/MAT/HDF5.

Started as a way to explore real-time DSP visualization and signal-chain
architecture in Python; still under active development (see
[TODO.md](TODO.md)).

## Features

- **Time-series plots** — multi-signal, auto-scrolling, with global or
  "relative to now" time modes, peak-preserving downsampling for large
  buffers, manual or auto Y-ranging, and classic two-cursor (ΔT, 1/ΔT,
  per-signal value) measurement.
- **Live spectrograms** — STFT view per signal, computed incrementally
  (only new samples are transformed each tick, not the whole rolling
  buffer — see [Notable implementation details](#notable-implementation-details)).
- **Per-signal controls** — visibility, IIR filtering, offset/gain
  scaling, curve color, and live Min/Mean/Max/Peak-to-Peak/RMS metrics.
  Rows start collapsed; click a signal's name for its details.
- **Signal math** — derive a new signal as `A op B` (`+ - * /`) from two
  existing signals.
- **Pluggable data producers** — background `QThread`-based producers
  with a synchronous or `asyncio` base class, multi-channel, with their
  own buttons on the signal rows; an error stops only the producer that
  hit it. Ships with a synthetic signal generator, a CSV file-replay
  producer, and templates for serial and `asyncio` sources.
- **Two storage backends** — a plain `collections.deque` or a
  thread-safe, pre-allocated numpy ring buffer, selectable per signal
  (see `scripts/benchmark_buffers.py` for the throughput difference).
- **Light/dark theming**, tooltips throughout, and export to
  PNG/SVG/PDF/CSV/MAT/HDF5.

## Architecture

```
producers.py          Background QThread data sources (Sync/Async base classes)
    │
signal_model.py       Rolling buffer, filtering, scaling, metrics per signal
math_signal.py         └─ derived signal (A op B)
    │
view.py               The interface every plot type implements
timeseries_plot.py     ├─ scrolling time-series plot
spectrogram_plot.py    └─ live STFT spectrogram
    │
signal_controller.py  Per-signal sidebar controls (SignalRow / SignalController)
dialogs.py            Modal dialogs: range, filter, scaling, math
    │
workspace.py          Domain layer: owns all registries and wiring
exporters.py           └─ PNG/SVG/PDF images, CSV/.mat/.h5 data
    │
oscilab.py            QMainWindow: menus, toolbar, window chrome
    │
demo.py               Example usage / `uv run demo` entry point
```

Roughly bottom-up: data model → producers → plots → per-signal controls →
the `Workspace` layer that wires everything together → the `QMainWindow`
shell on top. To add a plot type (FFT, XY, ...), implement `View` in
[view.py](src/oscilab/view.py) and add a `Workspace.add_*` method for it —
theming, time modes, Reset View, the Y-Axis button and image export then
work for it without further changes.

## Getting started

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run demo
```

If `uv run demo` says `program not found` in an environment that also had
the project installed under its old name (`oszilab`), uninstalling that
took the shared `demo` launcher with it —
`uv sync --reinstall-package oscilab` puts it back.

[`demo.py`](src/oscilab/demo.py) is a good starting point for the public
API:

```python
from oscilab import AsyncSyntheticProducer, Oscilab, SyntheticProducer

oszi = Oscilab()

# Each producer registers its own signals, one per channel, at its own fs
oszi.attach_producer(SyntheticProducer(fs=100, freq=5.0), "channel-1", storage=oszi.storage.deque)
oszi.attach_producer(SyntheticProducer(fs=1_000, freq=(1.0, 10.0)), ["channel-2", "channel-3"])
oszi.attach_producer(AsyncSyntheticProducer(fs=200, freq=2.0), "async")  # has a "Reset Phase" button

oszi.add_timeseries_plot("Sensors")
oszi.link(plot="Sensors", label="channel-1")
oszi.link(plot="Sensors", label="channel-2")
oszi.link(plot="Sensors", label="async")

oszi.add_spectrogram_plot(label="channel-3")

oszi.run()  # shows the window; blocks until it's closed
```

### Writing a producer

To feed your own hardware or file source, subclass `SyncDataProducer` (for
blocking APIs) or `AsyncDataProducer` (for `asyncio`-based ones) from
`producers.py` and implement its one abstract method:

```python
class ImuProducer(SyncDataProducer):
    channels = ("ax", "ay", "az")

    def __init__(self, port):
        super().__init__(fs=500)
        self.device = open_my_device(port)

    def _generate(self, elapsed):
        t, samples = self.device.read()  # samples: shape (3, n), one row per channel
        return t, samples

    def on_start(self):  # runs on the producer's thread before each run, restarts included
        self.device.open()

    def on_stop(self):  # runs on the producer's thread, however it stops
        self.device.close()

    def actions(self):  # buttons on the signal's row, run on the producer's thread
        return {"Zero": self.device.zero}


oszi.attach_producer(ImuProducer("COM3"), ["acc-x", "acc-y", "acc-z"])
```

`x` is shaped `(channels, n)`; a single-channel producer can return a 1-D
array. An exception inside a producer stops only that producer — it's shown
in the status bar and on its signals' rows instead of taking the app down,
and the row's **Restart** button starts it again.

## Notable implementation details

A few things worth reading the source for if you're interested in the DSP
or real-time-systems side rather than the UI:

- **Incremental STFT caching** ([spectrogram_plot.py](src/oscilab/spectrogram_plot.py)) —
  recomputing `scipy.signal.stft` over the entire rolling buffer every
  frame doesn't scale, so only the new samples (plus enough context for
  window overlap) are transformed each tick and merged into a persistent
  column cache. Getting this right required anchoring each recompute a
  whole number of hops before the last cached column, so new columns land
  on the same time grid even after the rolling buffer wraps and its
  indices shift, and permanently excluding the last `ceil(nperseg / hop)`
  columns of any partial slice from the cache — `scipy`'s edge
  zero-padding (`padded=True`) silently corrupts them otherwise. A separate, non-cached "preview tail" recomputed
  every tick keeps the display caught up to "now" without ever caching a
  wrong value.
- **Wall-clock-corrected producer pacing** ([producers.py](src/oscilab/producers.py)) —
  a naive fixed `sleep(interval)` loop drifts badly under OS timer
  granularity (Windows' default ~15 ms in particular); producers instead
  track elapsed wall-clock time and emit however many samples are due,
  self-correcting each iteration.
- **Thread-safe ring buffer** ([ring_buffer.py](src/oscilab/ring_buffer.py)) —
  a pre-allocated numpy circular buffer as a drop-in for
  `deque(maxlen=...)`, guarded by a lock. Producer data is delivered on
  the GUI thread via queued Qt signals, but a signal fed by hand may be
  written from any thread.

## Testing

```bash
uv run pytest
```

Lint and formatting (config in `ruff.toml`):

```bash
uv run ruff check
uv run ruff format
```

Tests run headless (Qt's offscreen platform), so they work without a
display, and CI runs them plus ruff on every pull request. They cover the
ring buffer, signal model (filter/scaling/resize), math signals, the
spectrogram's incremental STFT cache, the producer base classes (threads,
errors, actions, multi-channel data), workspace rules, and the File-menu
exports — see [TODO.md](TODO.md) for what isn't covered yet.

## Status

Documentation is a work in progress — see [TODO.md](TODO.md) for the
running list of what's next.

## License

MIT — see [LICENSE](LICENSE).
