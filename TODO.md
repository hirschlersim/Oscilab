# TODO / ideas

Rough notes on what's missing or could be added next. Not commitments, just a
working list, roughly in the order they'd make sense.

## Next up

1. **Session save/load (JSON).** Persist signals, plot layout, links,
   colors, filter/scaling, storage backend, theme and cursor state, and
   reload it: set a layout up once via the GUI instead of writing Python
   each time. Also covers starting producers from a config (or the GUI)
   instead of only from code. That needs a producer registry mapping a type
   name to its class and constructor arguments. Pydantic models for the
   schema would double as its documentation.
2. **Network/UDP producer template.** An `AsyncDataProducer` over an
   asyncio datagram endpoint, same shape as the existing producers, and the
   first entry in that registry.
3. **Restart a stopped producer.** A producer that stopped with an error
   stays stopped until the app restarts; add a "Restart" button next to
   the ⚠ on its row.
4. **Collapsible signal rows.** Show only color, name and Visible by
   default; metrics, Filter, Scaling and producer buttons appear (and are
   only built) when the row's label is clicked.

## Small fixes

- **`.mat` variable names.** The export names variables `<label>_time` /
  `<label>_data`, but labels like `channel-1` aren't valid MATLAB names
  (letters, digits and underscores, starting with a letter). Sanitize to
  `[A-Za-z]\w*`.

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
