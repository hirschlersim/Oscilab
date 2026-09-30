# TODO / ideas

---

## Next up

1. **Network/UDP producer template.** An `AsyncDataProducer` over an
   asyncio datagram endpoint, same shape as the existing producers. Sessions
   save it like any other producer (import path + constructor arguments).

2. **xBox Controller via Bluetooth.** A producer that connects via Bluetooth to a Controller and sends all of its signals like Button Press Triggers etc to the UI,

3. **Open Session replaces the current setup.** Plots, links and math
   signals can now be removed at runtime, but producers are attached in code
   and stay — so a session opened in a window that's in use still gets a new
   window. Replacing the setup in place would need detaching producers
   (stop them, drop their signals).

## Features

- **Trigger.** Oscilloscope-style triggering: freeze the display when a
  signal crosses a level (rising/falling edge), in single-shot or repeating
  mode, so a fast event can be looked at instead of scrolling past.

- **Record to disk.** Stream every signal to an HDF5 file while running
  (start/stop from the toolbar), rather than only exporting what's still in
  the rolling buffer.

- **More plot types**, now that `View` makes them cheap to add: an FFT/PSD
  plot of a signal, and an XY plot (one signal against another).

- **Signal units.** A unit per signal (V, A, °C, ...), shown in the Y axis
  label, the legend and the row's metrics; saved in sessions.

- **Remember the window.** Save window size/position and toolbar toggle
  states in sessions too.

## Code quality

- **Tests still missing:** producer pacing accuracy (does a producer keep up
  with real time?), the cursor readout, and `SerialProducer` (against a fake
  serial port, since pyserial isn't installed in CI).
- **API docs.** The README covers usage; reference docs could be generated
  from the docstrings (e.g. with pdoc) and published.
