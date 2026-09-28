# TODO / ideas

Rough notes on what's missing or could be added next. Not commitments, just a
working list.

- Mypy
- Public API naming: `Oscilab` uses camelCase (`addTimeSeriesPlot`,
  `addSpectrogramPlot`, `regSignal`, `attachProducer`), everything else is
  snake_case. Pick one before add-ons start coding against it —
  `ruff.toml` exempts these four names from N802 until then.
- pydantic (documentation)
- The test suite is still thin — most of this has been verified via
  one-off scripts. Covered so far: RingBuffer vs deque equivalence, the
  STFT cache correctness check, filter cutoff validation. Still missing:
  SignalModel filter/scaling/resize, MathSignal operators, CSV/mat/h5
  round-trips. These would catch
  regressions for free instead of re-deriving correctness by hand each time
  something nearby changes.
- Session save/load: persist signals, plot layout, links, colors,
  filter/scaling, storage backend, theme, cursor state to JSON and reload
  it — set up a layout once via the GUI instead of writing Python each time.
- Can the producers push multiple signals via vectors?
- A network/UDP producer template, same shape as the existing producers.


- detail of the channels behind a leabbel button i.e. the only get rederd if this button is pressed
- How to implement producer classes best
- how to implement custom gui buttons via a class
- How to hande json or similar for starting a config - maby also start the producers via the gui, and the only attach via code, same with the plots
