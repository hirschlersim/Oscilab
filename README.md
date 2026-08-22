- FFT
- PSD
- Butterworth filters
- FIR filters
- Wavelet transform
- spectrogram
- oscilloscope
- trigger
- recording
- can bus decoding / best to test for also

---

- np roll class

time vektor const as option i.e. -T - 0 for plot

downsampling
color the subplots via a spinner
cursor keyboard shortcuts


## 1. Architecture & Core Structure
- [ ] **Modularization & Refactoring**
  - [ ] Extract `SignalModel` and `SignalController` into an isolated, dedicated file/module.
  - [ ] Implement an Abstract Base Class (`ABC`) to enforce interfaces across components.
- [ ] **Signal Model Component**
  - [ ] Define and implement the `SignalModel` class.
  - [ ] Determine the optimal mechanism to push data to or retrieve data from the signal model function.
- [ ] **Signal Controller Component**
  - [ ] Build the `SignalController` class to orchestrate models and producers.

## 2. Concurrency & Data Flow
- [ ] **Data Producer Management**
  - [ ] Architect the spawning of background data producer classes.
  - [ ] Establish a robust, thread-safe mechanism to signal back to producer classes from the main application.
- [ ] **Multithreading**
  - [ ] Integrate `QThread` for background data ingestion/processing.
  - [ ] *Note:* Standard Python `collections.deque` appends are thread-safe, making them ideal for cross-thread data buffering.

## 3. Performance Optimization & Data Structures
- [ ] **Buffer Analysis**
  - [ ] Benchmark whether it is faster to collect streaming data using a `deque` and convert to a NumPy array, or manage a pre-allocated/dynamic NumPy array straight away.
- [ ] **Spectral Analysis Integration**
  - [ ] Investigate the best way to calculate spectral metrics (e.g., FFT/Spectrum extraction).
- [ ] **Rendering Optimization**
  - [ ] Implement min-max downsampling (e.g., dynamically downsampling to plot only a representative 70% of data points rather than the full, raw buffer length to preserve rendering performance).

## 4. Plotting, Math & Interactive Features
- [ ] **Plot Interactions & Controls**
  - [ ] Link axes across multiple plots to synchronize both panning and zooming.
  - [ ] Add active measurement cursors using `pyqtgraph.LinearRegionItem` (or similar).
  - [ ] Implement a comprehensive signal legend featuring dynamic show/hide toggle controls per signal.
- [ ] **Signal Analysis & Mathematics**
  - [ ] Add a Spectral (Spek) Plot visualizer.
  - [ ] Implement per-signal mathematical calculations: Addition (`+`), Subtraction (`-`), Multiplication (`*`), Division (`/`), etc.
  - [ ] Calculate and display real-time per-signal metrics:
    - [ ] Minimum (Min)
    - [ ] Maximum (Max)
    - [ ] Root Mean Square (RMS)
    - [ ] Peak-to-Peak
- [ ] **Oscilloscope Triggering Core**
  - [ ] Implement a formal triggering system configuring:
    - [ ] Trigger Source
    - [ ] Threshold Level
    - [ ] Trigger Edge (Rising/Falling)

## 5. UI/UX, Styling & Application Configuration
  - [ ] Implement dynamic Theme Switching (Dark Theme vs. Light Theme).
  - [ ] Investigate scope of capabilities allowed within a external `.qss` stylesheet file.
      in python code:
    self.btn_abort.setObjectName("DangerButton")
    css:
    QPushButton#DangerButton {
        background-color: #ff5555;
    }
    QPushButton#DangerButton:hover {
        background-color: #ff3333;
    }


## 6. Execution Control & Data Persistence
- [ ] **Data Logging & Persistence**
  - [ ] Allow seamless data saving operations while the acquisition engine is running.
  - [ ] Develop comprehensive multi-format exporters supporting:
    - [ ] Raster & Vector Graphics: `PNG`, `SVG`, `PDF`
    - [ ] Scientific Data Structures: `.mat` (MATLAB), `.h5` (Hierarchical Data Format v5)

## 7. Status Monitoring & Diagnostics
- [ ] **Status Bar Setup**
  - [ ] Complete full layout and styling setup for the bottom status bar.
  - [ ] Implement a unified `oszi.log()` function acting as a pipeline routing messages directly to a status bar text panel while simultaneously committing detailed system events via `loguru`.
  - [ ] Ensure user feedback notifications (e.g., "File Saved Successfully" messages) display on the status bar.
  - [ ] Centrall Logging for the Application, is done via Loguru AND the statusbar where everything is updated
