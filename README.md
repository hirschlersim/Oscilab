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
- **Signal math** — derive a new signal as `A op B` (`+ - * /`) from two
  existing signals.
- **Pluggable data producers** — background `QThread`-based producers
  with a synchronous or `asyncio` base class; ships with a synthetic
  signal generator, a file-replay producer, and templates for serial
  and (soon) network sources.
- **Two storage backends** — a plain `collections.deque` or a
  thread-safe, pre-allocated numpy ring buffer, selectable per signal
  (see `scripts/benchmark_buffers.py` for the throughput difference).
- **Light/dark theming**, tooltips throughout, and export to
  PNG/SVG/PDF/CSV/MAT/HDF5.

## Architecture

```
producers.py        Background QThread data sources (Sync/Async base classes)
    │
signal_model.py      Rolling buffer, filtering, scaling, metrics per signal
math_signal.py        └─ derived signal (A op B)
    │
timeseries_plot.py   pyqtgraph-based plot widgets, reading model.snapshot()
spectrogram_plot.py
axis_dialog.py
    │
signal_controller.py  Per-signal sidebar controls (SignalRow / SignalController)
    │
workspace.py          Domain layer: owns all registries, wiring, export
    │
oscilab.py             QMainWindow: menus, toolbar, window chrome
    │
demo.py                Example usage / entry point
```

Roughly bottom-up: data model → producers → plots → per-signal controls →
the `Workspace` layer that wires everything together → the `QMainWindow`
shell on top.

## Getting started

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run demo
```

`demo.py` is a good starting point for the public API:

```python
from oscilab.oscilab import Oscilab
from oscilab.producers import SyntheticProducer

oszi = Oscilab()

# Each producer registers its own signals, one per channel, at its own fs
oszi.attach_producer(SyntheticProducer(fs=100, freq=5.0), "channel-1", storage=oszi.storage.deque)
oszi.attach_producer(SyntheticProducer(fs=1_000, freq=(1.0, 10.0)), ["channel-2", "channel-3"])

oszi.add_timeseries_plot("Sensors")
oszi.link(plot="Sensors", label="channel-1")
oszi.link(plot="Sensors", label="channel-2")

oszi.add_spectrogram_plot(label="channel-3")

oszi.show()
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

    def on_stop(self):  # runs on the producer's thread, however it stops
        self.device.close()

    def actions(self):  # buttons on the signal's row, run on the producer's thread
        return {"Zero": self.device.zero}


oszi.attach_producer(ImuProducer("COM3"), ["acc-x", "acc-y", "acc-z"])
```

`x` is shaped `(channels, n)`; a single-channel producer can return a 1-D
array. An exception inside a producer stops only that producer — it's shown
in the status bar and on its signals' rows instead of taking the app down.

## Notable implementation details

A few things worth reading the source for if you're interested in the DSP
or real-time-systems side rather than the UI:

- **Incremental STFT caching** ([spectrogram_plot.py](src/oscilab/spectrogram_plot.py)) —
  recomputing `scipy.signal.stft` over the entire rolling buffer every
  frame doesn't scale, so only the new samples (plus enough context for
  window overlap) are transformed each tick and merged into a persistent
  column cache. Getting this right required hop-aligning the recompute
  window to match a full-buffer STFT's time grid, and permanently
  excluding the last `ceil(nperseg / hop)` columns of any partial slice
  from the cache — `scipy`'s edge zero-padding (`padded=True`) silently
  corrupts them otherwise. A separate, non-cached "preview tail" recomputed
  every tick keeps the display caught up to "now" without ever caching a
  wrong value.
- **Wall-clock-corrected producer pacing** ([producers.py](src/oscilab/producers.py)) —
  a naive fixed `sleep(interval)` loop drifts badly under OS timer
  granularity (Windows' default ~15 ms in particular); producers instead
  track elapsed wall-clock time and emit however many samples are due,
  self-correcting each iteration.
- **Thread-safe ring buffer** ([ring_buffer.py](src/oscilab/ring_buffer.py)) —
  a pre-allocated numpy circular buffer as a drop-in for
  `deque(maxlen=...)`, guarded by a lock since producers run on their own
  `QThread`.

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
display. They cover the data side — ring buffer, signal model
(filter/scaling/resize), math signals, the spectrogram's incremental STFT
cache, workspace rules, and CSV/.mat/.h5 round-trips. Producers and the
image exports aren't covered yet — see [TODO.md](TODO.md).

## Status

Documentation is a work in progress — see [TODO.md](TODO.md) for the
running list of what's next.

## License

MIT — see [LICENSE](LICENSE).
