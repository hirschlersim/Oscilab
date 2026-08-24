"""Live spectrogram (STFT) view of a single signal's rolling buffer.

Unlike a naive "recompute everything every tick" approach, this caches
already-computed STFT columns (`_cached_cols`/`_cached_times`) and each
refresh only runs `scipy.signal.stft` over the new samples since last tick
(plus a little context so the STFT windows overlap correctly) — not the
whole buffer. Columns whose time has scrolled out of the buffer's current
window get evicted from the cache, keeping it in sync with a `SignalModel`
that itself only holds a rolling window.
"""

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtGui as qtg
from PyQt6 import QtWidgets as qtw
from scipy import signal


class SpectrogramPlot(qtw.QWidget):
    def __init__(self, label: str, model, nperseg: int = 256, noverlap: int = 128):
        super().__init__()
        self.label = label
        self.model = model
        self.nperseg = nperseg
        self.noverlap = min(noverlap, nperseg - 1)
        self._levels_initialized = False
        self.time_mode = "global"  # "global" (as recorded) or "relative" ([-duration, 0], now at 0)
        self.freq_range_auto = True  # Off: Y (frequency) pinned to (freq_range_min, freq_range_max)
        self.freq_range_min = 0.0
        self.freq_range_max = 1.0
        self.color_levels_auto = True  # Off: colormap pinned to (color_levels_min, color_levels_max) dB
        self.color_levels_min = 0.0
        self.color_levels_max = 1.0

        # STFT column cache — see module docstring.
        self._cached_times = np.array([])  # absolute center-time of each cached column
        self._cached_cols = None  # (n_freq_bins, n_cached_columns), in dB

        layout = qtw.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot = pg.PlotWidget()
        self.plot.setLabel('left', 'Frequency', units='Hz')
        self.plot.getViewBox().invertY(False)  # 0 Hz at the bottom, increasing upwards
        self.plot.setMenuEnabled(False)
        layout.addWidget(self.plot)

        self.img = pg.ImageItem()
        self.img.setColorMap(pg.colormap.get('viridis'))
        self.plot.addItem(self.img)

        self.set_theme(bg="#202f46", fg="#ffffff")
        self.__update_axis_label()

    def set_theme(self, bg: str, fg: str):
        self.plot.setBackground(bg)
        self.plot.setTitle(self.label, color=fg)  # Which signal this spectrogram belongs to
        self.plot.getAxis('left').setTextPen(fg)
        self.plot.getAxis('bottom').setTextPen(fg)
        self.plot.getAxis('left').setPen(fg)
        self.plot.getAxis('bottom').setPen(fg)

    def set_time_mode(self, mode: str):
        """"global": x-axis shows time as recorded. "relative": shifted so the
        newest sample sits at 0 and the buffer's start sits at -duration."""
        assert mode in ("global", "relative"), f"Unknown time mode '{mode}'"
        self.time_mode = mode
        self.__update_axis_label()
        self.update()  # Recompute the image transform under the new mode right away...
        self.reset_view()  # ...then re-fit + resume auto-follow (see reset_view()),
                            # so a prior manual pan/zoom doesn't leave the view
                            # stuck showing the old mode's now-irrelevant range.

    def __update_axis_label(self):
        label = "Time (relative to now)" if self.time_mode == "relative" else "Time"
        self.plot.setLabel('bottom', label, units='s')

    def set_y_range(self, auto: bool, y_min: float = 0.0, y_max: float = 1.0):
        """Auto: frequency axis keeps auto-fitting to the image (0..Nyquist,
        as before). Manual: pin exactly [y_min, y_max] Hz."""
        self.freq_range_auto = auto
        self.freq_range_min = y_min
        self.freq_range_max = y_max
        if auto:
            self.plot.enableAutoRange(axis='y')
        else:
            self.plot.enableAutoRange(axis='y', enable=False)
            self.plot.setYRange(y_min, y_max, padding=0)

    def set_color_levels(self, auto: bool, lo: float = 0.0, hi: float = 1.0):
        """Auto: colormap levels auto-snapshot from the first STFT frame and
        then hold (today's behavior). Manual: pin exactly [lo, hi] dB."""
        self.color_levels_auto = auto
        self.color_levels_min = lo
        self.color_levels_max = hi
        if auto:
            self._levels_initialized = False  # let the next frame take a fresh auto snapshot
        else:
            self.img.setLevels((lo, hi))
            self._levels_initialized = True  # stop the auto-snapshot logic in update() from overriding this

    def update(self):
        time_full, data_full = self.model.snapshot()
        if len(data_full) < self.nperseg:
            return  # Not enough samples yet for even one STFT window

        # Only recompute the STFT over new data (plus a little context so the
        # windows overlap correctly) instead of the entire rolling buffer —
        # already-cached columns are reused as-is.
        hop = self.nperseg - self.noverlap
        if self._cached_times.size:
            start_idx = int(np.searchsorted(time_full, self._cached_times[-1], side="right"))
            raw_context_start = max(0, start_idx - self.nperseg)
            # scipy.signal.stft always treats a slice's own index 0 as its
            # local t=0, so segment centers land at context_start + k*hop.
            # Without aligning context_start to a hop multiple, that grid
            # doesn't coincide with the grid a full-buffer recompute would
            # use, and columns end up silently computed at the wrong time.
            context_start = (raw_context_start // hop) * hop
        else:
            context_start = 0

        data_slice = data_full[context_start:]
        if len(data_slice) < self.nperseg:
            return  # Not enough new+context data yet for even one window

        f, t_rel, zxx = signal.stft(data_slice, fs=self.model.fs, nperseg=self.nperseg, noverlap=self.noverlap)
        amp_db = 20 * np.log10(np.abs(zxx) + 1e-9)
        col_times = time_full[context_start] + t_rel

        # scipy pads *both* ends of whatever it's given (`padded=True`) so
        # the segment count divides evenly, on top of `boundary='zeros'`'s
        # own centering padding — so up to ceil(nperseg/hop) trailing
        # columns of any slice shorter than the full buffer can be
        # end-zero-padded, not real data (verified empirically across
        # several nperseg/noverlap ratios). Never let those into the
        # PERMANENT cache (this class never revisits an already-cached
        # column, so a wrong one would stay wrong forever) — but still show
        # them as a transient preview below, recomputed every tick, so the
        # display keeps reaching up to "now" instead of permanently lagging
        # by margin*hop seconds.
        margin = -(-self.nperseg // hop)  # ceil(nperseg / hop)
        safe_amp_db = amp_db[:, :-margin] if amp_db.shape[1] > margin else amp_db[:, :0]
        safe_col_times = col_times[:-margin] if len(col_times) > margin else col_times[:0]

        if self._cached_times.size:
            # Recomputing with fresh context can regenerate a column or two
            # already in the cache (or one slightly earlier, if alignment
            # shifted by a fraction of a hop) — keep only genuinely new ones.
            keep = safe_col_times > self._cached_times[-1]
            safe_amp_db = safe_amp_db[:, keep]
            safe_col_times = safe_col_times[keep]

        if safe_amp_db.size:
            if self._cached_cols is None:
                self._cached_cols = safe_amp_db
                self._cached_times = safe_col_times
            else:
                self._cached_cols = np.concatenate((self._cached_cols, safe_amp_db), axis=1)
                self._cached_times = np.concatenate((self._cached_times, safe_col_times))

        if self._cached_times.size == 0:
            return

        # Evict cached columns that have scrolled out of the buffer's
        # current window, the same way `SignalModel`'s own buffer ages out.
        evict = int(np.searchsorted(self._cached_times, time_full[0], side="left"))
        if evict > 0:
            self._cached_cols = self._cached_cols[:, evict:]
            self._cached_times = self._cached_times[evict:]

        if self._cached_times.size == 0:
            return

        # Transient preview: the margin columns just excluded from the
        # cache, appended only to what's *displayed* this tick — not stored,
        # so next tick recomputes them fresh (some will graduate into the
        # permanent cache once they're no longer the trailing edge).
        preview_amp_db = amp_db[:, -margin:]
        preview_col_times = col_times[-margin:]
        preview_keep = preview_col_times > self._cached_times[-1]
        preview_amp_db = preview_amp_db[:, preview_keep]
        preview_col_times = preview_col_times[preview_keep]

        if preview_amp_db.size:
            display_cols = np.concatenate((self._cached_cols, preview_amp_db), axis=1)
            display_times = np.concatenate((self._cached_times, preview_col_times))
        else:
            display_cols, display_times = self._cached_cols, self._cached_times

        now = self.model.time[-1] if self.time_mode == "relative" and self.model.time else 0.0
        dt = hop / self.model.fs
        df = float(f[1] - f[0]) if len(f) > 1 else self.model.fs / self.nperseg

        self.img.setImage(display_cols.T, autoLevels=False)
        transform = qtg.QTransform()
        transform.translate(display_times[0] - now, 0)
        transform.scale(dt, df)
        self.img.setTransform(transform)

        if not self._levels_initialized:
            self.img.setLevels((float(display_cols.min()), float(display_cols.max())))
            self._levels_initialized = True

    def reset_view(self):
        self.plot.autoRange()
        # autoRange() only does a ONE-TIME fit — it actually leaves the
        # ViewBox's continuous auto-range OFF afterward (both axes), which is
        # how the time axis normally keeps following new data as the image
        # updates each tick. Without re-enabling it, the view would silently
        # freeze in place instead of resuming its live scroll.
        self.plot.enableAutoRange(axis='x')
        if self.freq_range_auto:
            self.plot.enableAutoRange(axis='y')
        else:
            # A manual frequency range survives Reset View instead of being
            # wiped by it. Color levels are untouched by autoRange anyway.
            self.plot.setYRange(self.freq_range_min, self.freq_range_max, padding=0)
