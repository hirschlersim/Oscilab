# Oscilab

A desktop oscilloscope / signal-visualization application built with
PyQt6 and pyqtgraph. Stream live or replayed signals into scrolling
time-series plots and live STFT spectrograms, with per-signal filtering,
scaling, derived (math) channels, and export to PNG/SVG/PDF/CSV/MAT/HDF5.

Started as a way to explore real-time DSP visualization and signal-chain
architecture in Python; still under active development (see
[TODO.md](TODO.md)).

## Features

- **Trigger** — as on an NI digitizer: edge, hysteresis, window or
  immediate; DC/AC coupling, holdoff, delay; a timebase and reference
  position; Run / Single / Stop / Force, Auto or Normal, and Autoset. The
  time plots hold the record around each trigger, the level a line to drag.
- **Time-series plots** — multi-signal, auto-scrolling, with global or
  "relative to now" time modes, peak-preserving downsampling for large
  buffers, manual or auto Y-ranging, and classic two-cursor (ΔT, 1/ΔT,
  per-signal value) measurement.
- **Live spectrograms** — STFT view per signal, computed incrementally
  (only new samples are transformed each tick, not the whole rolling
  buffer — see [Notable implementation details](#notable-implementation-details)).
- **Logic lanes** — on/off signals (buttons, digital lines) in a lane each,
  like a logic analyzer, on the same time axis as the other plots.
- **XY plots** — one signal against another: a joystick's path, Lissajous
  figures, I-V curves; the signals may run at different rates.
- **Histograms** — a signal's value distribution, with its mean and
  standard deviation: noise, clipping, quantization steps.
- **Per-signal controls** — visibility, IIR filtering, offset/gain
  scaling, curve color, unit, and live Min/Mean/Max/Peak-to-Peak/RMS
  metrics. Rows start collapsed; click a signal's name for its details.
- **Signal units** — V, A, °C, ... per signal, shown in legends, on Y axes,
  in metrics and cursor readouts; math signals derive theirs (V × A → V·A).
- **Signal math** — derive a new signal as `A op B` (`+ - * /`) from two
  existing signals.
- **Build the layout in the app** — add any kind of plot from the Plots
  menu; each plot's panel picks the signals it shows or removes it.
  Producers are attached in code, and started, stopped or
  removed from the Producers menu.
- **Pluggable data producers** — background `QThread`-based producers
  with a synchronous or `asyncio` base class, multi-channel; the Producers
  menu starts, stops and removes them and runs their own functions (which
  can go in the toolbar too). An error stops
  only the producer that hit it. Ships with a synthetic signal generator, a CSV file-replay
  producer, an Xbox controller producer (Bluetooth or USB, on Windows), a
  phone's sensors via the Sensor Logger app, samples over UDP (an ESP32 on
  Wi-Fi, a simulation, any program), and templates for serial and `asyncio`
  sources.
- **A ring buffer per signal** — a thread-safe, pre-allocated numpy
  circular buffer, read as one array every refresh.
- **Save Stream** — record every sample of every signal to HDF5 files
  while it's on, not just what's still in the buffers; a new file at a
  size you choose. **Replay Recording** plays them back — a file or a
  whole folder, looped if you like.
- **Light/dark theming**, tooltips throughout, and export to
  PNG/SVG/PDF/CSV/MAT/HDF5.

## Architecture

```
producers.py          Background QThread data sources (Sync/Async base classes)
toolbar.py             ├─ @button / @toggle: their functions in the menus and toolbar
xbox.py                ├─ an Xbox controller, through XInput
sensor_logger.py       ├─ a phone's sensors, from the Sensor Logger app
udp.py                 └─ samples over UDP
    │
signal_model.py       Rolling buffer, filtering, scaling, metrics per signal
ring_buffer.py         ├─ the thread-safe numpy circular buffer it keeps them in
math_signal.py         └─ derived signal (A op B)
    │
view.py               The interface every plot type implements
grid.py                ├─ the base of the plots with a grid: one paint a frame
timeseries_plot.py     ├─ scrolling time-series plot
logic_plot.py          ├─ logic lanes (a kind of time-series plot)
xy_plot.py             ├─ one signal against another
histogram_plot.py      ├─ a signal's value distribution
spectrogram_plot.py    └─ live STFT spectrogram
trigger.py            Triggering: detection and Run/Single/Stop (no Qt)
trigger_panel.py       └─ its controls, in the sidebar
    │
signal_controller.py  Per-signal sidebar controls (SignalRow / SignalController)
dialogs.py            Modal dialogs: range, filter, scaling, math
    │
workspace.py          Domain layer: owns all registries and wiring
plotting.py            ├─ its plots: creating them, linking signals, removing them
triggering.py          ├─ the trigger applied to its time plots
exporters.py           ├─ PNG/SVG/PDF images, CSV/.mat/.h5 data
recorder.py            ├─ Save Stream: every sample to HDF5, as it arrives
replay.py              └─ ...and playing those recordings back
    │
oscilab.py            QMainWindow: puts the parts together; the API scripts use
ui/                    ├─ each feature's menus, toolbar entries, panels, dialogs:
                       │    file_menu, plots_menu, producers_menu, controls,
                       │    recording (Save Stream, Replay), trigger
session.py             ├─ save/restore a whole setup as JSON
log.py                 └─ logging: console, log file, status bar
    │
demo.py               Example usage / `uv run oscilab-demo` entry point
examples/             The showcases, a command each: xbox, sensor_logger, udp
benchmarks/           How fast it is: refresh (`oscilab-bench`)
__main__.py           `uv run oscilab [session.json]` entry point
```

Roughly bottom-up: data model → producers → plots → per-signal controls →
the `Workspace` layer that wires everything together → the `QMainWindow`
on top, assembled from one small part per feature in `ui/` — so a new
feature's menus and panels go in a module of their own. To add a plot
type (an FFT, say), implement `View` in [view.py](src/oscilab/view.py) and
add an `add_*` method for it to `Plotting` in
[plotting.py](src/oscilab/plotting.py), which `Workspace` hands on —
theming, time modes, Reset View, the Y-Axis button and image export then
work for it without further changes.

## Getting started

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). Developed and
tested on Windows, where CI runs too. The Xbox producer needs Windows; the
rest is plain PyQt6 and should run on Linux and macOS, but that's untested.

```bash
git clone https://github.com/hirschlersim/Oscilab
cd Oscilab
uv sync
uv run oscilab-demo
```

Every showcase and benchmark is a command of its own; `--help` lists a
command's options:

| Command | What it does |
| --- | --- |
| `uv run oscilab [session.json]` | An empty window — or a saved session |
| `uv run oscilab-demo` | Synthetic signals: plots, a spectrogram, math, toolbar controls |
| `uv run oscilab-xbox` | An Xbox controller's sticks, triggers and buttons ([below](#an-xbox-controller)) |
| `uv run oscilab-sensor-logger` | Your phone's sensors, from the Sensor Logger app ([below](#your-phones-sensors-sensor-logger)) |
| `uv run oscilab-udp --demo` | Samples over UDP, sending itself some ([below](#samples-over-udp)) |
| `uv run oscilab-bench` | Where Oscilab keeps up: a figure of channels x sample rate |

### In your own project

Oscilab isn't on PyPI. Fork it, or add it to your project straight from
GitHub, or as a git submodule to change it in place:

```bash
uv add git+https://github.com/hirschlersim/Oscilab   # pip install git+https://... works too

git submodule add https://github.com/hirschlersim/Oscilab oscilab   # or this,
uv add --editable ./oscilab                                          # its changes live
```

[`demo.py`](src/oscilab/demo.py) is a good starting point for the public
API:

```python
from oscilab import AsyncSyntheticProducer, Oscilab, SyntheticProducer

scope = Oscilab()

# Each producer registers its own signals, one per channel, at its own fs
scope.attach_producer(SyntheticProducer(fs=100, freq=5.0), "channel-1", units="V")
scope.attach_producer(SyntheticProducer(fs=1_000, freq=(1.0, 10.0)), ["channel-2", "channel-3"])
scope.attach_producer(AsyncSyntheticProducer(fs=200, freq=2.0), "async")  # "Reset Phase" in the toolbar

scope.add_timeseries_plot("Sensors")
scope.link(plot="Sensors", label="channel-1")
scope.link(plot="Sensors", label="channel-2")
scope.link(plot="Sensors", label="async")

scope.add_spectrogram_plot(label="channel-3")

scope.run()  # shows the window; blocks until it's closed
```

### Writing a producer

To feed your own hardware or file source, subclass `SyncDataProducer` (for
blocking APIs) or `AsyncDataProducer` (for `asyncio`-based ones) from
`producers.py` and implement its one abstract method:

```python
from oscilab import SyncDataProducer, button


class ImuProducer(SyncDataProducer):
    channels = ("ax", "ay", "az")
    units = ("m/s²", "m/s²", "m/s²")  # Optional, one per channel

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

    @button("Zero", toolbar=True)  # in its Producers-menu entry and the toolbar; runs on its thread
    def zero(self):
        self.device.zero()


scope.attach_producer(ImuProducer("COM3"), ["acc-x", "acc-y", "acc-z"])
```

`x` is shaped `(channels, n)`; a single-channel producer can return a 1-D
array. An exception inside a producer stops only that producer — it's shown
in the status bar and on its signals' rows instead of taking the app down,
and the row's **Restart** button starts it again.

### An Xbox controller

`XboxControllerProducer` turns an Xbox One S / Series controller's sticks,
triggers and buttons into signals — over Bluetooth (pair it first, in
Windows' Settings → Bluetooth & devices → Add device), USB or the Xbox
Wireless Adapter. It reads the controller through Windows' XInput, so it
needs no extra packages, and works on Windows only.

```bash
uv run oscilab-xbox   # plots of its sticks, triggers and buttons
```

```python
from oscilab import XboxControllerProducer

pad = XboxControllerProducer()  # 20 channels: left_x ... right_y, left_trigger, right_trigger, a, b, ... dpad_right
scope.attach_producer(pad, pad.channels)  # one signal per channel, named like it

# Or just some, from a given controller (0-3; by default the first connected)
scope.attach_producer(XboxControllerProducer(slot=1, channels=["left_x", "left_y", "a"]), ["x", "y", "fire"])
```

Sticks read -1 to 1 (up and right positive) with XInput's recommended dead
zones (`deadzone=False` for raw values), triggers 0 to 1, buttons 1 while
held. With no controller connected, or once it goes to sleep, the producer
stops with an error on its rows; connect it and press **Restart**. Its
Producers-menu entry has **Rumble**, a short buzz to tell which controller
it's reading.

### Your phone's sensors (Sensor Logger)

The [Sensor Logger](https://www.tszheichoi.com/sensorlogger) app (Android
and iOS) can push its sensors' readings to a URL while it records — and
Oscilab can be that URL:

```bash
uv run oscilab-sensor-logger              # the app sampling at 50 Hz
uv run oscilab-sensor-logger --rate 400   # or whatever you've set it to
```

1. Phone and PC on the same Wi-Fi. The window's title shows the URL to push
   to, e.g. `http://192.168.1.23:8000/data`. The first time, Windows asks
   whether to let Python through its firewall: allow it on private networks.
2. In the app: Settings → Data Streaming → **HTTP Push**, enter that URL.
   Opening it in the phone's browser tells you whether the phone gets
   through.
3. Enable the accelerometer, gyroscope and orientation, and start recording.

The script plots acceleration and rotation rate over time, the phone's tilt
(roll against pitch), and how the acceleration is spread. The plots trail
the phone by the push period — a second, by default. The status bar says
the rate each sensor really arrives at: to go faster, set the app's
sampling rate higher and run the script with `--rate` at what it measured.

From your own code, one `SensorLoggerProducer` per sensor — the sensors run
at different rates — sharing one listener on the port:

```python
from oscilab.sensor_logger import attach_sensor_logger, push_url

attach_sensor_logger(scope, ["accelerometer", "gyroscope", "magnetometer"], fs=100)
print(push_url())  # the URL to enter in the app
```

Readings keep the phone's own timestamps, so its sensors stay in step. The
common sensors' channels and units are built in (accelerometer, gravity,
gyroscope, magnetometer, orientation, barometer, microphone, location,
battery); for other values pass `channels=`, e.g.
`SensorLoggerProducer("orientation", channels=["qx", "qy", "qz", "qw"])`.

### Samples over UDP

`UdpProducer` listens on a port for samples sent as UDP packets — from a
microcontroller on Wi-Fi (an ESP32, a Pico W), a simulation, a test rig,
any program. The sender needs no connection and no library, and doesn't
care whether Oscilab is running; a lost packet is just a gap.

```bash
uv run oscilab-udp          # plots lines of "x,y,z" sent to port 9000
uv run oscilab-udp --demo   # ...and sends itself some, to try it
```

```python
from oscilab import UdpProducer

scope.attach_producer(UdpProducer(port=9000, channels=("x", "y", "z"), fs=100), ["x", "y", "z"])
```

Each packet holds one or more samples, as text — a line per sample,
`"1.23,4.56,7.89\n"`, the easiest to send from anything — or binary, with
`binary=` a `struct` format for one sample (`"<3f"`: three little-endian
floats), compact for fast streams. By default the samples are numbered at
`fs`, as a serial device's are; with `time_column=True` each sample's first
value is its time in seconds, from the sender's clock. Sending from an
ESP32 (Arduino):

```cpp
udp.beginPacket("192.168.1.23", 9000);  // the PC — the window's title says where
udp.printf("%f,%f,%f\n", ax, ay, az);
udp.endPacket();
```

### Custom toolbar buttons and toggles

Write them as methods of your own class, mark them with `@button` or
`@toggle`, and hand an instance to `add_toolbar`. They appear after the
built-in controls, next to Downsample:

```python
from oscilab import button, toggle


class LabTools:
    def __init__(self, device):
        self.device = device

    @button("Zero", tooltip="Zero the sensor")
    def zero(self):
        self.device.zero()

    @toggle("Heater", tooltip="Switch the sample heater")
    def heater(self, on: bool):  # the new state, on every click
        self.device.set_heater(on)


scope.add_toolbar(LabTools(device))
```

`@classmethod`s work too; pass the class itself then. A method that raises
doesn't take the app down: the error shows in the status bar, the traceback
goes to the log file, and a toggle flips back. For a one-off,
`scope.add_toolbar_button("Zero", device.zero)` and `add_toolbar_toggle` take
any callable.

Producers use the same decorators (see the `ImuProducer` above), but their
functions go in the **Producers** menu: every attached producer has an entry
there with **Start**, **Stop**, **Remove** and its `@button` / `@toggle` methods, which
run on the producer's own thread between reads. Add `toolbar=True` to put
one in the toolbar as well — `@button("Zero", toolbar=True)`. It's the same
control in both places, so a toggle shows the same state in each; its
toolbar tooltip names the producer.

### Trigger

Like a digitizer's soft front panel (NI-SCOPE's terms). Turn **Trigger** on
in the toolbar: it sets itself up on the first signal a plot shows — a
rising edge through the middle of its range, a timebase showing about two
periods (**Autoset**) — and the time plots stop scrolling. Each shows the
*record* around the latest trigger instead, held until the next: time
counted from the trigger, marked **T**, and the level a dashed line you can
drag. All of them show the same record, as a scope's channels do; the
spectrograms, XY plots and histograms carry on live.

The **Trigger** panel, atop the sidebar:

- **Source** and **Type**: *Immediate* (free running), *Edge* (crossing the
  **Level** on the **Slope**: rising, falling or either), *Hysteresis* (an
  edge that only counts once the source has been the hysteresis beyond the
  level — noise can't set it off), *Window* (the source entering or leaving
  the range from **Low** to **High**)
- **Coupling**: DC, or AC to trigger relative to the average; **Holdoff**:
  how long after a trigger before another can count; **Delay**: where the
  record's reference point is after the trigger
- **Timebase**: time per division, a record spanning ten; **Reference**:
  where the trigger sits in it (20 % = a fifth before it)
- **Auto trigger**: a record anyway when no trigger comes for a while, so
  the display never freezes; off, it's *Normal*, for rare events
- **Run** / **Single** (one record, then hold) / **Stop** / **Force** (a
  record now), and **Autoset** — also in the toolbar button's drop-down,
  whose dot says **Armed** (amber), **Triggered** (green), **Auto** (blue) or
  **Stopped** (red)

Trigger times are interpolated between samples, so records line up to
better than a sample. The cursors measure within the record, from the
trigger. Sessions save the trigger; from code:

```python
from oscilab.trigger import TriggerSettings

scope.set_trigger(TriggerSettings(source="channel-1", level=0.5, time_per_div=0.01, reference=0.2))
scope.workspace.trigger.single_shot()  # .run(), .stop(), .force()
```

### Save Stream: recording to disk

The File menu's exports write what's still in the rolling buffers — the
last few seconds. **Save Stream**, in the toolbar, records everything: turn
it on, and every sample of every signal goes to HDF5 files on disk as it
arrives, until you turn it off. It keeps recording while the display is
paused.

Its drop-down (and the File menu) has **Save Stream Settings…**: the file
to save to, and the size at which it starts a new file (or no limit). Each
recording gets its own files, named after that path and when it started:
`recording.h5` → `recording_20260930-101500_001.h5`, then `…_002.h5`. By
default they go to `Documents\Oscilab`; **Open Stream Folder** gets you
there. From code:

```python
scope.set_recording_options("D:/runs/test.h5", max_file_mb=500)  # None: no limit
scope.set_recording(True)  # ... and False to stop
```

Each file stands on its own, laid out like the `.h5` export: a group per
signal with `time` and `data` datasets and `fs`, `label` and `unit`
attributes. The values are what the signal holds (after its filter and
scaling); math signals aren't recorded, since they're recomputed from the
others. The writing happens on a thread of its own, so a slow disk never
holds up the plots or the producers.

### Replaying a recording

**File → Replay Recording…** plays a recording back. Pick a file, or a
folder: then every `.h5` file in it plays, in name order, which for Save
Stream's files is the order they were recorded in (1, 2, … 5). The dialog
lists the files and the recorded signals to choose from. With **Loop**, after
the last file it starts again from the first (… 5, 1, 2, …), and time keeps
going forward.

```python
scope.replay_recording("D:/runs", loop=True)  # every signal, in a new plot
scope.replay_recording("D:/runs/test_20260930-101500_003.h5", signals=["channel-1"])
```

Each signal gets a `RecordingReplayProducer` of its own, with the rate and
unit it was recorded at — a recording's signals can have different rates,
and a producer's channels share one. They lay the files out on the same
timeline, so they stay in step: the parts of one recording keep their
times, and a separate recording in the same folder follows right after the
previous one ends. Replays can be stopped, started (from where they
stopped, or from the top once they've played through) and saved in
sessions like any producer. A signal whose name is taken already is
replayed as `name (replay)`.

### Signal units

Give a signal a unit and it shows in the legend (`channel-1 (V)`), on the
Y axis (`Value (V)`, or `Value (V, A)` for a plot of mixed units), after its
row's metrics and in the cursor readout. A producer can declare its
channels' units (`units`, next to `channels`, as the `ImuProducer` above
does); otherwise pass them when you attach it or register a signal:

```python
scope.attach_producer(daq, ["u", "i"], units=["V", "A"])  # or one unit for every channel: units="V"
scope.register_signal("temp", fs=10, unit="°C")
scope.set_unit("u", "mV")  # later — or with the Unit button in the signal's row
```

A math signal's unit follows from its operands', and keeps following them:
`u * i` is in V·A, `u / u` has none.

### Sessions

Set a layout up once, then reopen it without writing Python.
**File → Save Session…** writes the whole setup to a JSON file: the
window's size and position, the producers (class, constructor arguments
and their toggles' states), every plot with its links, pinned ranges and
cursors, each signal's unit, color, visibility, filter and scaling, the
display settings, and the states of your own toolbar toggles. Buffered data
isn't saved; producers start afresh.

```bash
uv run oscilab my-setup.json   # open a saved session
uv run oscilab --schema        # print the session file format (JSON Schema)
```

**File → Open Session…** rebuilds one. In a window that's already in use it
asks first: **Replace** its setup (producers stopped and removed, along
with every signal and plot), or open the session in a **New Window**. From
code, `scope.load_session(path)` loads into an empty window and
`scope.load_session(path, replace=True)` replaces; `scope.clear()` empties
a window and `scope.detach_producer(label)` removes one producer.

The file is checked before anything is built: unknown keys (typos), bad
values and references to signals that nothing provides are reported
instead of half-loading. Replacing only clears the window once all the
session's producers have been built; if one can't be, the current setup
stays and carries on. Its producers do pause while that's tried, so a
device one of them holds (a serial port, say) is free for the session's.

Your toolbar toggles are saved as `"ClassName.method"`. Loading a session
before `add_toolbar` adds them is fine: each one switches (calling its
method) as soon as it's added.

Producers are stored by import path (`"module:ClassName"`), so your own
producer classes can be saved too, as long as they're defined in an
importable module (not the script you run) and take JSON-friendly
constructor arguments. Loading a session imports those modules, so only
open session files you trust.

### Logging

Everything is logged with [loguru](https://github.com/Delgan/loguru), to
three places:

- **the console**;
- **a log file**, when Oscilab starts the application itself (`uv run oscilab-demo`,
  `uv run oscilab`): in your per-user app data folder
  (`%LOCALAPPDATA%\Oscilab\logs\oscilab.log` on Windows), rotated at 1 MB
  with the last 5 kept, producers' tracebacks included. **Help → Open Log
  Folder** gets you there, and **Help → About** shows the path;
- **the window's status bar**, styled by severity: info plain, warnings
  amber with ⚠ (8 s), errors red and bold with ✖ (until the next message).
  Debug messages go to the file only.

From your own code, log through the window so messages reach its status bar
too: `scope.log("Calibrated", level="success")`, or
`scope.logger.warning(...)` with loguru's full API.

## API reference

Generated from the docstrings with [pdoc](https://pdoc.dev):

```bash
uv run --group docs scripts/docs.py           # browse it live at localhost:8080
uv run --group docs scripts/docs.py -o site   # or write static HTML into site/
```

It covers what `from oscilab import ...` offers plus the modules to read when
extending Oscilab — producers, toolbar controls, the session format, the
`View` interface for new plot types, the workspace. CI builds it on every
run too: the HTML is in the run's `api-docs` artifact.

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
  every signal's pre-allocated numpy circular buffer, guarded by a lock.
  Producer data is delivered on
  the GUI thread via queued Qt signals, but a signal fed by hand may be
  written from any thread.

## Testing

```bash
uv run pytest
uv run coverage run -m pytest && uv run coverage report   # ...and what they leave out
```

Lint and formatting (config in `ruff.toml`), and type checking (config in
`pyproject.toml`):

```bash
uv run ruff check
uv run ruff format
uv run mypy
```

The code is fully type-annotated; mypy checks it strictly, and checks the
tests' use of it. Tests run headless (Qt's offscreen platform), so they work
without a display. CI runs them on every push and pull request, with ruff
and mypy; it fails below 90 % coverage, and opens a window from the built
wheel, as a fork or a `git+https://` install gets it.

They cover the ring buffer, signal model (filter/scaling/resize), math
signals and units, every plot type, the spectrogram's incremental STFT
cache, the producer base classes (threads, errors, actions, multi-channel
data, keeping up with real time), the bundled producers against stand-ins
(a fake serial port and XInput, HTTP pushes and UDP packets over
localhost), the trigger (detection, acquisition, its panel), the cursor
readout, the menus, toolbar and dialogs, Save Stream and replay, sessions
and the window's state, logging, workspace rules, the File-menu exports,
what keeps drawing fast, and every command.

## Status

Documentation is a work in progress — see [TODO.md](TODO.md) for the
running list of what's next.

## License

MIT — see [LICENSE](LICENSE).
