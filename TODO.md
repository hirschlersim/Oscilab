# TODO / ideas

Rough notes on what's missing or could be added next. Not commitments, just a
working list.

- Mypy
- Public API naming: `Oscilab` uses camelCase (`addTimeSeriesPlot`,
  `addSpectrogramPlot`, `regSignal`, `attachProducer`), everything else is
  snake_case. Pick one before add-ons start coding against it —
  `ruff.toml` exempts these four names from N802 until then.
- pydantic (documentation)
- Tests: the data side is covered (RingBuffer, SignalModel, MathSignal,
  STFT cache, Workspace rules, CSV/mat/h5 round-trips). Still untested:
  producers (pacing, FileReplayProducer, stop()), cursor readout,
  PNG/SVG/PDF export.
- Bug: `FileReplayProducer(loop=True)` re-emits the file's original
  timestamps on every pass, so time jumps backwards at each loop.
- `.mat` export names variables `<label>_time`; labels like `channel-1`
  aren't valid MATLAB variable names. Check how MATLAB loads them and
  sanitize to `[A-Za-z]\w*` if needed.
- Session save/load: persist signals, plot layout, links, colors,
  filter/scaling, storage backend, theme, cursor state to JSON and reload
  it — set up a layout once via the GUI instead of writing Python each time.
- Can the producers push multiple signals via vectors?
- A network/UDP producer template, same shape as the existing producers.


- detail of the channels behind a leabbel button i.e. the only get rederd if this button is pressed
- How to implement producer classes best
- how to implement custom gui buttons via a class
- How to hande json or similar for starting a config - maby also start the producers via the gui, and the only attach via code, same with the plots
