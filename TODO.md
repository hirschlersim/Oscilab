# TODO / ideas

Rough notes on what's missing or could be added next. Not commitments, just a
working list.

- Mypy
- pydantic (documentation)
- There's still no automated test suite — everything so far has been
  verified via one-off scripts. A `tests/` directory covering the cheap,
  high-value cases (RingBuffer vs deque equivalence, MathSignal operators,
  the STFT cache correctness check, CSV/mat/h5 round-trips) would catch
  regressions for free instead of re-deriving correctness by hand each time
  something nearby changes.
- 100% test coverage
- Session save/load: persist signals, plot layout, links, colors,
  filter/scaling, storage backend, theme, cursor state to JSON and reload
  it — set up a layout once via the GUI instead of writing Python each time.
- Can the producers push multiple signals via vectors?
- A network/UDP producer template, same shape as the existing producers.
