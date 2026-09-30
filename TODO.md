# TODO / ideas

---

## Next up

1. **Network/UDP producer template.** An `AsyncDataProducer` over an
   asyncio datagram endpoint, same shape as the existing producers. Sessions
   save it like any other producer (import path + constructor arguments).

## Features

- **Trigger.** Oscilloscope-style triggering: freeze the display when a
  signal crosses a level (rising/falling edge), in single-shot or repeating
  mode, so a fast event can be looked at instead of scrolling past.

## Code quality

- **Publish the API docs.** CI builds them on every run (the `api-docs`
  artifact, see `scripts/docs.py`); GitHub Pages could host the latest from
  `main`. Needs Pages turned on for the repo — for a private repo, that takes
  a paid GitHub plan.
