# TODO: Oscilab as a portfolio piece

What a reviewer sees in their first two minutes counts most: few will run
it, fewer will read the code. In order:

1. **Make the repo public.** First look through the history for anything
   that shouldn't be shown. Public also makes GitHub Pages free (step 5).

2. **Start up faster.** `import oscilab` takes 7–11 s on battery, most of it
   `scipy.signal`, which only filters and spectrograms need: import it when
   one is first used. A slow first launch spoils a demo.

3. **A GIF at the top of the README**: live scrolling plots, the trigger
   catching an edge, a spectrogram. Below, one screenshot per showcase
   (Xbox controller, phone sensors, UDP).

4. **A tight README top section**: a one-sentence pitch, the GIF, badges
   (CI, coverage, Python 3.12, license, docs), a 30-second quick start, the
   commands table. The deep detail moves to the docs site. For the coverage
   badge, measure coverage in CI, with a floor of about 90 %.

5. **Publish the API docs on GitHub Pages.** CI already builds them (the
   `api-docs` artifact, `scripts/docs.py`): deploy the latest from `main`.

6. **Publish on PyPI**, so anyone can run it without cloning:
   `uvx --from oscilab oscilab-demo`.

7. **A Windows `.exe` on GitHub Releases** (PyInstaller), for people
   without Python.

8. **The performance story**, as a README section: the benchmark figures
   before and after the fixes, with what was found (the grid at 25 ms a
   frame, every frame painted twice) and what fixed it.

9. **A short video with real hardware**: an Xbox controller, a phone's
   tilt, an ESP32 over UDP, each live in about 30 seconds.

10. **A "How it works" page, written in your own words**: 4–5 short design
    notes (producers handing data over through queued Qt signals, the
    thread-safe ring buffer, the trigger's interpolated crossings and
    holdoff, the incremental STFT cache, the domain layer apart from the
    window's parts) and a Mermaid architecture diagram. Interviewers will
    ask about the design: these notes are how you show you own it.

11. **A CHANGELOG and tagged releases** (it's at 0.2.0).

12. **A "write your own producer" guide**: ten lines from your own sensor
    to a live plot.

13. **FFT and measurements** (frequency, amplitude, RMS on screen): what
    makes it read as a real oscilloscope.

14. **The pitch, for a CV or LinkedIn**: "Built Oscilab, a real-time
    oscilloscope and data-acquisition app in Python/Qt: it streams any
    source (serial, UDP, phone sensors, game controllers) into live plots
    at 30 FPS, with an NI-style trigger, HDF5 recording and replay —
    profiled and optimized, backed by about 540 tests, strict typing and
    CI."
