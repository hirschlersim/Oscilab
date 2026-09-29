# TODO / ideas

Rough notes on what's missing or could be added next. Not commitments, just a
working list, roughly in the order they'd make sense.

## Next up

1. **Network/UDP producer template.** An `AsyncDataProducer` over an
   asyncio datagram endpoint, same shape as the existing producers. Sessions
   save it like any other producer (import path + constructor arguments).
2. **Add producers and plots from the GUI.** Sessions rebuild a setup from
   a file, but creating one still takes code (or a hand-written JSON file).
   Dialogs to add a producer (type + arguments) and a plot would make the
   whole setup doable without Python.

## Small fixes

- **`.mat` variable names.** The export names variables `<label>_time` /
  `<label>_data`, but labels like `channel-1` aren't valid MATLAB names
  (letters, digits and underscores, starting with a letter). Sanitize to
  `[A-Za-z]\w*`.
- have loggin work  via a oscilab logger, which has loguru, file, and a ststus line colord with properties according to the severity of the event

## Code quality

- **Type hints + mypy.** Add hints to the remaining untyped parameters
  (`model`, `producer`, `parent`, callbacks), then run mypy in CI.
- **Tests still missing:** producer pacing accuracy (does a producer keep
  up with real time?) and the cursor readout.
- **CI on Linux.** Tests run on Windows only; Linux runners would need Qt's
  system libraries (libEGL, xkbcommon, ...) installed first.

## Open questions

- **Custom GUI buttons from a class.** Buttons tied to a producer work
  (`DataProducer.actions()`, see the README). Buttons or panels that aren't
  tied to a producer (a toolbar action or a side panel from an add-on) would
  need an add-on API. What's actually needed?
- **Where producer buttons go.** They sit on the first of a producer's
  signals to get a row. With many buttons, a per-producer section could be
  clearer.
- **The `demo` launcher name** is generic. Another installed package with a
  `demo` script would collide, which is how the old `oszilab` install took
  it with it on uninstall. `oscilab-demo` would be safer.


## Dump

- contoller bt producer
