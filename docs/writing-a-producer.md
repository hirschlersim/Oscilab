# Writing a producer

A producer is how your own data gets into Oscilab: a sensor, an instrument,
a file, another program. It runs on a thread of its own and hands Oscilab
chunks of samples; Oscilab stores them, plots them, triggers on them and
records them, like any other signal.

## From your sensor to a live plot

```python
import numpy as np

from oscilab import Oscilab, SyncDataProducer


class Thermometer(SyncDataProducer):
    units = ("°C",)

    def _generate(self, elapsed):
        return np.array([elapsed]), np.array([read_temperature()])  # Your sensor's reading, now


scope = Oscilab()
scope.attach_producer(Thermometer(fs=10, interval_s=0.1), "temperature")
scope.add_timeseries_plot("Lab")
scope.link(plot="Lab", label="temperature")
scope.run()
```

`read_temperature()` stands for your device's API. That's all a producer
needs: `_generate(elapsed)` returns the samples that are new since the last
call, as `(t, x)`: their times in seconds, and their values. It's called
every `interval_s` seconds, on the producer's own thread, so it may block
on a read. `fs` is the sampling rate: it sizes the signal's buffer, and
the spectrogram and filter go by it.

`attach_producer` registers a signal per channel, named as you say, and
starts the producer. From there, the signal is like any other: link it to
plots, give it a filter, trigger on it, record it.

## More than one channel, more than one sample

A producer with several channels names them, and returns `x` with one row
per channel: shape `(channels, n)`. A device that delivers samples in
blocks returns the whole block at once, with each sample's time:

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

An API that hands you one row per *sample*, shape `(n, channels)`, just
needs a `.T`. A single-channel producer can return a 1-D `x`. With no
timestamps of its own, a device's samples are numbered: `n / fs`.

## Blocking or asyncio

- **`SyncDataProducer`** is for blocking APIs: most serial libraries, and
  vendor SDKs that block on a read. Implement `_generate(elapsed)`. A
  producer that makes its samples itself (a simulation, a file played back)
  should return whatever is due by `elapsed`, not a fixed number per call:
  sleeps overshoot, and a fixed number would fall behind real time.
- **`AsyncDataProducer`** is for `asyncio` APIs: async serial or network
  clients. Implement `async def _run(self)`, awaiting your reads and calling
  `self.emit_data(t, x)` as samples arrive. Stopping cancels it, so it never
  waits on a read that doesn't come.

## The device: opening, closing, functions

- **`on_start()` / `on_stop()`** run on the producer's thread whenever it
  starts or stops: at a restart, at the end of its data, on an error, at
  exit. Open and close the device there.
- **Functions in the menu and toolbar**: mark a method `@button("Zero")`,
  or `@toggle("Heater")` for an on/off one that's passed the new state.
  It shows in the producer's entry in the Producers menu, and with
  `toolbar=True` in the toolbar too. It runs on the producer's thread
  between two reads, so it can use the same connection safely.

## When something goes wrong

An exception in a producer stops that producer only. The error shows in
the status bar and on its signals' rows, the traceback goes to the log
file, and the row's **Restart** button starts it again; everything else
carries on.

## In a saved session

File > Save Session saves a producer as its class and the arguments it was
constructed with, which Oscilab records by itself. To be rebuilt from a
session, the class has to be in a module that can be imported (not the
script you run), and its arguments JSON-friendly: numbers, strings, lists,
paths.

## Producers to read

The ones Oscilab comes with are short, and cover most kinds of source:

- `SerialProducer` ([producers.py](../src/oscilab/producers.py)): lines
  of numbers from a serial port, with a Reconnect button.
- `UdpProducer` ([udp.py](../src/oscilab/udp.py)): packets over the
  network, text or binary; an `AsyncDataProducer`.
- `SensorLoggerProducer` ([sensor_logger.py](../src/oscilab/sensor_logger.py)):
  a phone's readings, pushed over HTTP with their own timestamps.
- `XboxControllerProducer` ([xbox.py](../src/oscilab/xbox.py)): a device
  read through a C API (ctypes), with a Rumble button.
- `AsyncSyntheticProducer` ([producers.py](../src/oscilab/producers.py)):
  the template for an asyncio client.

The [API reference](https://hirschlersim.github.io/Oscilab/oscilab/producers.html)
has every detail of the base classes.
