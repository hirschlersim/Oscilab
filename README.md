- Mypy
- pydantic(documentation)

- There's still no automated test suite — everything in this whole conversation has been verified via one-off scripts. A tests/ directory covering the cheap, high-value cases (RingBuffer vs deque equivalence, MathSignal operators, the STFT cache correctness check I wrote by hand, CSV/mat/h5 round-trips) would catch regressions for free instead of relying on me re-deriving correctness each time something nearby changes.

- 100% Test coverage

- Session save/load: persist signals, plot layout, links, colors, filter/scaling, storage backend, theme, cursor state to JSON and reload it — set up a layout once via the GUI instead of writing Python each time.

- can the producers push multiple signals via vektors?

- A network/UDP producer template, same shape.
