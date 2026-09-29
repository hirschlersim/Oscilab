"""
Live spectrogram (STFT) view of a single signal's rolling buffer.

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
    """Live STFT of one signal (see module docstring). Implements `oscilab.view.View`."""

    y_unit = "Hz"  # The Y axis is frequency

    def __init__(self, label: str, model, nperseg: int = 256, noverlap: int = 128):
        super().__init__()
        self.label = label
        self.model = model
        self.nperseg = nperseg
        self.noverlap = min(noverlap, nperseg - 1)
        self._levels_initialized = False
        self.time_mode = "global"  # "global" (as recorded) or "relative" ([-duration, 0], now at 0)
        self.y_range_auto = True  # Off: Y (frequency) pinned to (y_range_min, y_range_max)
        self.y_range_min = 0.0
        self.y_range_max = 1.0
        self.color_levels_auto = True  # Off: colormap pinned to (color_levels_min, color_levels_max) dB
        self.color_levels_min = 0.0
        self.color_levels_max = 1.0

        # STFT column cache — see module docstring.
        self._cached_times = np.array([])  # absolute center-time of each cached column
        self._cached_cols = None  # (n_freq_bins, n_cached_columns), in dB

        layout = qtw.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot = pg.PlotWidget()
        self.plot.setLabel("left", "Frequency", units="Hz")
        self.plot.getViewBox().invertY(False)  # 0 Hz at the bottom, increasing upwards
        self.plot.setMenuEnabled(False)
        layout.addWidget(self.plot)

        self.img = pg.ImageItem()
        self.img.setColorMap(pg.colormap.get("viridis"))
        self.plot.addItem(self.img)

        self.set_theme(bg="#202f46", fg="#ffffff")
        self.__update_axis_label()

    def set_theme(self, bg: str, fg: str):
        self.plot.setBackground(bg)
        self.plot.setTitle(self.label, color=fg)  # Which signal this spectrogram belongs to
        self.plot.getAxis("left").setTextPen(fg)
        self.plot.getAxis("bottom").setTextPen(fg)
        self.plot.getAxis("left").setPen(fg)
        self.plot.getAxis("bottom").setPen(fg)

    def set_time_mode(self, mode: str):
        """
        "global": x-axis shows time as recorded. "relative": shifted so the
        newest sample sits at 0 and the buffer's start sits at -duration.
        """
        if mode not in ("global", "relative"):
            raise ValueError(f"Unknown time mode '{mode}'")
        self.time_mode = mode
        self.__update_axis_label()
        # Recompute the image transform under the new mode right away, then
        # re-fit + resume auto-follow (see reset_view()), so a prior manual
        # pan/zoom doesn't leave the view stuck showing the old mode's
        # now-irrelevant range.
        self.refresh()
        self.reset_view()

    def __update_axis_label(self):
        label = "Time (relative to now)" if self.time_mode == "relative" else "Time"
        self.plot.setLabel("bottom", label, units="s")

    @property
    def plot_item(self) -> pg.PlotItem:
        return self.plot.plotItem

    def live_y_range(self) -> tuple[float, float]:
        y_min, y_max = self.plot.getViewBox().viewRange()[1]
        return y_min, y_max

    def set_downsampling(self, _enabled: bool):
        """No-op: a spectrogram's pixels are already STFT-bin aggregates, not raw samples."""

    def set_y_range(self, auto: bool, y_min: float = 0.0, y_max: float = 1.0):
        """
        Auto: frequency axis keeps auto-fitting to the image (0..Nyquist,
        as before). Manual: pin exactly [y_min, y_max] Hz.
        """
        self.y_range_auto = auto
        self.y_range_min = y_min
        self.y_range_max = y_max
        if auto:
            self.plot.enableAutoRange(axis="y")
        else:
            self.plot.enableAutoRange(axis="y", enable=False)
            self.plot.setYRange(y_min, y_max, padding=0)

    def set_color_levels(self, auto: bool, lo: float = 0.0, hi: float = 1.0):
        """
        Auto: colormap levels auto-snapshot from the first STFT frame and
        then hold (today's behavior). Manual: pin exactly [lo, hi] dB.
        """
        self.color_levels_auto = auto
        self.color_levels_min = lo
        self.color_levels_max = hi
        if auto:
            self._levels_initialized = False  # let the next frame take a fresh auto snapshot
        else:
            self.img.setLevels((lo, hi))
            self._levels_initialized = True  # stop the auto-snapshot logic in refresh() from overriding this

    def refresh(self):
        """Extend the STFT with the samples that arrived since last tick and redraw (called each tick)."""
        time_full, data_full = self.model.snapshot()
        if len(data_full) < self.nperseg:
            return  # Not enough samples yet for even one STFT window

        stft = self.__update_cache(time_full, data_full)
        if stft is None or self._cached_times.size == 0:
            return
        freqs, preview_cols, preview_times = stft

        if preview_cols.size:
            display_cols = np.concatenate((self._cached_cols, preview_cols), axis=1)
            display_times = np.concatenate((self._cached_times, preview_times))
        else:
            display_cols, display_times = self._cached_cols, self._cached_times

        self.__draw(display_cols, display_times, freqs, newest=time_full[-1])

    def __update_cache(self, time_full: np.ndarray, data_full: np.ndarray):
        """
        Add the columns that are new since last tick to the cache and evict
        the ones that have scrolled out of the buffer.

        Returns `(freqs, preview_cols, preview_times)` — the preview being
        this tick's not-yet-cacheable trailing columns, see below — or None
        if there isn't enough data yet for a single STFT window.
        """
        hop = self.nperseg - self.noverlap
        if self._cached_times.size and not time_full[0] <= self._cached_times[-1] <= time_full[-1]:
            # The cache no longer overlaps the buffer at all (e.g. paused for
            # longer than the buffer's duration while producers kept writing),
            # so there's nothing to continue from — start over.
            self._cached_cols = None
            self._cached_times = np.array([])

        # Only recompute the STFT over new data (plus a little context so the
        # windows overlap correctly) instead of the entire rolling buffer —
        # already-cached columns are reused as-is.
        if self._cached_times.size:
            # scipy.signal.stft centers a slice's column k on the slice's own
            # sample k*hop. Starting the slice a whole number of hops before
            # the sample the last cached column is centered on keeps every
            # new column on that same grid, and makes columns 0..steps_back
            # exactly the ones already cached. (Aligning to a hop multiple of
            # the *buffer* index instead breaks once the buffer wraps: index 0
            # then shifts by however many samples arrived since last tick.)
            last_idx = self.__nearest_index(time_full, self._cached_times[-1])
            steps_back = min(-(-self.nperseg // hop), last_idx // hop)  # ceil(nperseg / hop), if available
            context_start = last_idx - steps_back * hop
            first_new = steps_back + 1
        else:
            context_start = 0
            first_new = 0

        data_slice = data_full[context_start:]
        if len(data_slice) < self.nperseg:
            return None  # Not enough new+context data yet for even one window

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
        safe_end = max(first_new, amp_db.shape[1] - margin)
        safe_amp_db = amp_db[:, first_new:safe_end]
        safe_col_times = col_times[first_new:safe_end]

        if safe_amp_db.size:
            if self._cached_cols is None:
                self._cached_cols = safe_amp_db
                self._cached_times = safe_col_times
            else:
                self._cached_cols = np.concatenate((self._cached_cols, safe_amp_db), axis=1)
                self._cached_times = np.concatenate((self._cached_times, safe_col_times))

        # Evict cached columns that have scrolled out of the buffer's
        # current window, the same way `SignalModel`'s own buffer ages out.
        evict = int(np.searchsorted(self._cached_times, time_full[0], side="left"))
        if evict > 0:
            self._cached_cols = self._cached_cols[:, evict:]
            self._cached_times = self._cached_times[evict:]

        # Transient preview: the margin columns just excluded from the
        # cache, appended only to what's *displayed* this tick — not stored,
        # so next tick recomputes them fresh (some will graduate into the
        # permanent cache once they're no longer the trailing edge). Columns
        # centered past the newest sample are mostly zero padding; skip them.
        preview_amp_db = amp_db[:, safe_end:]
        preview_col_times = col_times[safe_end:]
        preview_keep = preview_col_times <= time_full[-1] + 0.5 / self.model.fs
        preview_amp_db = preview_amp_db[:, preview_keep]
        preview_col_times = preview_col_times[preview_keep]

        return f, preview_amp_db, preview_col_times

    def __draw(self, cols: np.ndarray, times: np.ndarray, freqs: np.ndarray, newest: float):
        """Show `cols` (freq bins x columns, dB) with each column centered on its time."""
        now = newest if self.time_mode == "relative" else 0.0
        dt = (self.nperseg - self.noverlap) / self.model.fs
        df = float(freqs[1] - freqs[0]) if len(freqs) > 1 else self.model.fs / self.nperseg

        self.img.setImage(cols.T, autoLevels=False)
        transform = qtg.QTransform()
        transform.translate(times[0] - dt / 2 - now, 0)
        transform.scale(dt, df)
        self.img.setTransform(transform)

        if not self._levels_initialized:
            self.img.setLevels((float(cols.min()), float(cols.max())))
            self._levels_initialized = True

    @staticmethod
    def __nearest_index(times: np.ndarray, t: float) -> int:
        """Index of the sample in sorted `times` closest to `t`."""
        i = int(np.searchsorted(times, t))
        if i > 0 and (i == len(times) or t - times[i - 1] <= times[i] - t):
            i -= 1
        return i

    def reset_view(self):
        self.plot.autoRange()
        # autoRange() only does a ONE-TIME fit — it actually leaves the
        # ViewBox's continuous auto-range OFF afterward (both axes), which is
        # how the time axis normally keeps following new data as the image
        # updates each tick. Without re-enabling it, the view would silently
        # freeze in place instead of resuming its live scroll.
        self.plot.enableAutoRange(axis="x")
        if self.y_range_auto:
            self.plot.enableAutoRange(axis="y")
        else:
            # A manual frequency range survives Reset View instead of being
            # wiped by it. Color levels are untouched by autoRange anyway.
            self.plot.setYRange(self.y_range_min, self.y_range_max, padding=0)
