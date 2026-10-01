# TODO / ideas

---

## Code quality

- **Split the workspace**, as the window was (`oscilab.ui`): `workspace.py`
  is 907 lines. The triggering — feeding the acquisition, capturing records
  into the time plots, the level lines — would be a class of its own, as
  could the plot creation and removal; the workspace keeps the registries.
- **Smoke-test the demo and the example scripts.** Nothing runs `demo.py`,
  `scripts/xbox_controller.py`, `sensor_logger.py` or `udp_example.py` today
  (0% coverage): run each headless for a second, with a fake controller,
  phone or UDP sender, so an API change can't break them unnoticed.
- **Coverage in CI**, with a floor (about 90% — it's 92% now), so it can't
  drift down unnoticed.
- **Merge the pending Dependabot update** (`uv.lock` only) once CI is green.
- **A refresh benchmark.** Every tick redraws each curve from its whole
  buffer on the GUI thread (10 s at 1 kHz: 10,000 points a curve, 30 times a
  second). Measure the load against channels × sample rate, as
  `scripts/benchmark_buffers.py` does for the buffers, then fix what it
  shows: drawing only the visible range, thinning big buffers
  automatically, skipping signals that haven't changed.
- **Publish the API docs.** CI builds them on every run (the `api-docs`
  artifact, see `scripts/docs.py`); GitHub Pages could host the latest from
  `main`. Needs Pages turned on for the repo — for a private repo, that takes
  a paid GitHub plan.

## Features

- **Measurements**, as on a scope's front panel: Vpp, RMS, frequency,
  period, rise and fall time, duty cycle of the triggered record, in a table.
- **Persistence**: successive triggered records drawn over each other,
  fading — jitter and variation at a glance.
- **Math expressions**: `a*b + 2`, `abs(a)`, a derivative or integral, a
  moving average — not only A op B.
- **FFT/PSD plot** of a signal.

## Everyday use

- **Remember settings between runs** (`QSettings`): theme, recording folder,
  the last session, window position — today only a saved session keeps them.
- **Resizable layout**: dividers to drag between the plots, and a sidebar
  that collapses; today the plots are a stack of equal height.

## Distribution

- **A standalone Windows `.exe`** (PyInstaller), or a PyPI package, so
  colleagues can run Oscilab without Python or uv.
