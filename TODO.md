# TODO / ideas

Rough notes on what's missing or could be added next. Not commitments, just a
working list.

- Mypy
- pydantic (documentation)
- Tests: covered are RingBuffer, SignalModel, MathSignal, the STFT cache,
  Workspace rules, CSV/mat/h5 round-trips and the producer base classes.
  Still untested: SyncDataProducer pacing accuracy, cursor readout,
  PNG/SVG/PDF export.
- Restarting a producer that stopped with an error (today it stays stopped
  until the app restarts).
- `.mat` export names variables `<label>_time`; labels like `channel-1`
  aren't valid MATLAB variable names. Check how MATLAB loads them and
  sanitize to `[A-Za-z]\w*` if needed.
- Session save/load: persist signals, plot layout, links, colors,
  filter/scaling, storage backend, theme, cursor state to JSON and reload
  it — set up a layout once via the GUI instead of writing Python each time.
- A network/UDP producer template, same shape as the existing producers.


- detail of the channels behind a leabbel button i.e. the only get rederd if this button is pressed
- How to implement producer classes best
- how to implement custom gui buttons via a class
- How to hande json or similar for starting a config - maby also start the producers via the gui, and the only attach via code, same with the plots
