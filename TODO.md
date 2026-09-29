# TODO / ideas

Rough notes on what's missing or could be added next. Not commitments, just a
working list, roughly in the order they'd make sense within each section.

## Known issues

- **Math signals pair samples by position, not by time.** `MathSignal`
  combines the last *n* samples of A and B index by index. With different
  sampling rates (the demo's `channel-1` at 100 Hz vs `channel-2` at 1 kHz —
  the Math menu offers both) or producers that started at different moments,
  it combines values from different times. Fix: interpolate B onto A's
  timestamps (`np.interp`) over their overlapping time range.
- **Relative time mode can draw signals out of step.** Each curve is shifted
  by its *own* newest sample (`TimeseriesPlot.refresh`), so a signal that
  arrives later than the others is drawn misaligned against them. Shift
  every curve (and the cursor readout) by the plot's newest timestamp
  instead.

## Next up

1. **Network/UDP producer template.** An `AsyncDataProducer` over an
   asyncio datagram endpoint, same shape as the existing producers. Sessions
   save it like any other producer (import path + constructor arguments).
2. **Remove plots, signals and producers at runtime.** Nothing can be taken
   out once added. Needed for the GUI setup below (a close button per plot,
   removing a signal and stopping its producer), and it would let File >
   Open Session replace the current window's setup instead of always
   opening a new window.
3. **Add producers and plots from the GUI.** Sessions rebuild a setup from a
   file, but creating one still takes code (or a hand-written JSON file).
   Dialogs to add a producer (type + arguments) and a plot would make the
   whole setup doable without Python.

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
- **Keyboard shortcuts.** Space to pause/resume, Ctrl+S / Ctrl+O for
  sessions, Ctrl+E to export.
- **Remember the window.** Save window size/position and toolbar toggle
  states in sessions too.

## Code quality

- **Type hints + mypy.** Add hints to the remaining untyped parameters
  (`model`, `producer`, `parent`, callbacks), then run mypy in CI.
- **Tests still missing:** producer pacing accuracy (does a producer keep up
  with real time?), the cursor readout, and `SerialProducer` (against a fake
  serial port, since pyserial isn't installed in CI).
- **CI on Linux.** Tests run on Windows only; Linux runners would need Qt's
  system libraries (libEGL, xkbcommon, ...) installed first.
- **CHANGELOG.md.** 0.2.0 changed the public API throughout (snake_case
  names, `attach_producer(producer, signals)`, `run()`, `register_signal`,
  ...). A changelog with a short migration list would help existing scripts.
- **API docs.** The README covers usage; reference docs could be generated
  from the docstrings (e.g. with pdoc) and published.

## Open questions

- **Custom GUI from a class, beyond buttons.** Producer buttons
  (`DataProducer.actions()`) and toolbar buttons/toggles (`@button` /
  `@toggle` + `add_toolbar`) work. A whole side panel from an add-on (its
  own widgets, e.g. a settings form) would need more. Is it needed?
- **Where producer buttons go.** They sit on the first of a producer's
  signals to get a row. With many buttons, a per-producer section could be
  clearer.
- **The `demo` launcher name** is generic. Another installed package with a
  `demo` script would collide, which is how the old `oszilab` install took
  it with it on uninstall. `oscilab-demo` would be safer.

## Dump

- contoller bt producer
