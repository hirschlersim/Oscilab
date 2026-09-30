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

## Code quality

- **Publish the API docs.** CI builds them on every run (the `api-docs`
  artifact, see `scripts/docs.py`); GitHub Pages could host the latest from
  `main`. Needs Pages turned on for the repo — for a private repo, that takes
  a paid GitHub plan.
