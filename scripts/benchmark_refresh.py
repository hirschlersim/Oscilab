"""
Benchmark: how many channels, at which sample rate, Oscilab keeps up with.

Every refresh tick redraws each curve from its whole buffer, on the GUI
thread — the thread that also takes in every block the producers send. This
runs the real thing for a grid of channel counts x sample rates: a producer's
blocks queued to the GUI thread, the window's own refresh timer, the
painting, with every buffer full (the steady state). It measures the GUI
thread's time per frame drawn: everything it did, divided by the frames.

A setup is fast enough when that's less than `--max-load` of the frame
budget (33 ms at 30 FPS) — the rest keeps the window responsive — and the
data doesn't pile up waiting to be taken in.

The result is a figure of the time per frame over channels x sample rate,
with the fast-enough area outlined, saved as a PNG (`--output`) and shown.

    uv run --group bench scripts/benchmark_refresh.py
    uv run --group bench scripts/benchmark_refresh.py --full-detail --fps 60
    uv run --group bench scripts/benchmark_refresh.py --help

The window is on screen while it runs, maximized unless given a `--size`.
Leave it be: covering or minimizing it changes what gets painted.
"""

import argparse
import ctypes
import math
import sys
import time
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import matplotlib as mpl
import numpy as np
from loguru import logger
from matplotlib import pyplot as plt
from matplotlib.lines import Line2D
from PyQt6 import QtCore as qtc
from PyQt6 import QtWidgets as qtw

from oscilab import Oscilab, SyncDataProducer
from oscilab.display import FPS_CHOICES

BACKLOG_LIMIT = 0.25  # Seconds of data sent but not taken in yet: more, and it's falling behind
OVERLOADED = 3.0  # Frame budgets per frame: so far from keeping up that a bigger setup can't either


class BlockProducer(SyncDataProducer):
    """
    `channels` sines at `fs`, sent in blocks of 1 / `block_rate` seconds, as
    a DAQ card sends them. One second of them is computed up front and then
    repeated, so producing costs next to nothing: what's measured is Oscilab.
    """

    def __init__(self, fs: float, channels: int, block_rate: float) -> None:
        super().__init__(fs=fs, interval_s=1 / block_rate)
        self.channels = tuple(f"ch{i + 1}" for i in range(channels))
        t = np.arange(round(fs)) / fs
        freqs = 1.0 + np.arange(channels)  # Whole cycles per second, so the repeats join up
        noise = np.random.default_rng(0).normal(0, 0.05, (channels, t.size))
        self.table = np.sin(2 * np.pi * freqs[:, np.newaxis] * t) + noise
        self.sent = 0  # Samples sent so far

    def history(self, channel: int, duration: float) -> tuple[np.ndarray, np.ndarray]:
        """The `duration` seconds of `channel` before it started, to fill a buffer with."""
        n = np.arange(-round(duration * self.fs), 0)
        return n / self.fs, self.table[channel].take(n, mode="wrap")

    def _generate(self, elapsed: float) -> tuple[np.ndarray, np.ndarray]:
        due = int(elapsed * self.fs) - self.sent
        if due <= 0:
            return np.array([]), np.array([])
        n = np.arange(self.sent, self.sent + due)
        self.sent += due
        return n / self.fs, self.table.take(n, axis=1, mode="wrap")


@dataclass
class Result:
    channels: int
    fs: float
    frame_ms: float = math.nan  # The GUI thread's time per frame drawn
    fps: float = math.nan  # Frames drawn per second
    backlog: float = math.nan  # Seconds of data sent but not taken in yet, at the end
    measured: bool = True  # False: skipped, as a smaller setup was already overloaded

    def share(self, fps: int) -> float:
        """`frame_ms` as a share of the frame budget at `fps`."""
        return self.frame_ms * fps / 1000

    def fast_enough(self, max_load: float, fps: int) -> bool:
        return self.measured and self.share(fps) <= max_load and self.backlog <= BACKLOG_LIMIT

    def overloaded(self, fps: int) -> bool:
        return self.share(fps) > OVERLOADED or self.backlog > 4 * BACKLOG_LIMIT


class Bench:
    """One window, set up with each grid cell's channels and producer in turn."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.app = qtw.QApplication(sys.argv[:1])
        self.app.setStyle("windows11")  # Styled as Oscilab is: the style sheet costs painting time too
        self.app.setStyleSheet(resources.files("oscilab.assets").joinpath("style_dark.qss").read_text("utf-8"))
        logger.remove()  # Oscilab logs every producer and plot: keep the output to the results

        self.window = Oscilab(app=self.app)
        self.window.set_fps(args.fps)
        self.window.set_duration(args.duration)
        self.window.set_downsampling(not args.full_detail)
        if args.size is None:
            self.window.showMaximized()
        else:
            width, height = (int(n) for n in args.size.lower().split("x"))
            self.window.resize(width, height)
            self.window.show()
        self.run_for(0.2)  # Laid out and shown
        dpr = self.window.devicePixelRatioF()
        self.size = f"{self.window.width()}x{self.window.height()}" + (f" (x{dpr:g} pixels)" if dpr != 1 else "")

        self.meter = self.window.load_ui.meter  # The window's Load bar's
        self.frames = 0
        self.window.refresh_timer.timeout.connect(self.__count_frame)

    def __count_frame(self) -> None:
        self.frames += 1

    def run_for(self, seconds: float) -> None:
        loop = qtc.QEventLoop()
        qtc.QTimer.singleShot(round(seconds * 1000), loop.quit)
        loop.exec()

    def __measured(self) -> tuple[float, float]:
        """After the warm-up: (ms per frame, frames per second)."""
        self.run_for(self.args.warmup)
        busy = self.meter.busy()
        self.frames = 0
        start = time.perf_counter()
        self.run_for(self.args.seconds)
        elapsed = time.perf_counter() - start
        busy = self.meter.busy() - busy
        frame_ms = 1000 * busy / self.frames if self.frames else math.inf
        return frame_ms, self.frames / elapsed

    def idle(self) -> tuple[float, float]:
        """The empty window's (ms per frame, frames per second): the least a frame costs, the most there are."""
        return self.__measured()

    def measure(self, channels: int, fs: float) -> Result:
        args, window = self.args, self.window
        producer = BlockProducer(fs, channels, args.block_rate)
        labels = list(producer.channels)
        models = window.attach_producer(producer, labels)
        for channel, model in enumerate(models):
            model.add_data(*producer.history(channel, args.duration))  # Full buffers, as after a while
        for first in range(0, channels, args.per_plot):
            title = f"Plot {first // args.per_plot + 1}"
            window.add_timeseries_plot(title)
            for label in labels[first : first + args.per_plot]:
                window.link(title, label)

        frame_ms, fps = self.__measured()
        backlog = (producer.sent - 1) / fs - models[0].time[-1]

        window.clear()
        self.run_for(0.1)  # The plots go
        return Result(channels, fs, frame_ms=frame_ms, fps=fps, backlog=max(backlog, 0.0))

    def run(self) -> list[Result]:
        results: list[Result] = []
        overloaded: list[Result] = []
        for channels in sorted(self.args.channels):
            for fs in sorted(self.args.rates):
                if any(o.channels <= channels and o.fs <= fs for o in overloaded):
                    results.append(Result(channels, fs, measured=False))
                    print(f"{channels:>4} ch x {fs:>9g} Hz: skipped, a smaller setup was already overloaded")
                    continue
                result = self.measure(channels, fs)
                results.append(result)
                if result.overloaded(self.args.fps):
                    overloaded.append(result)
                verdict = "fast enough" if result.fast_enough(self.args.max_load / 100, self.args.fps) else "too slow"
                print(
                    f"{channels:>4} ch x {fs:>9g} Hz: {result.frame_ms:6.1f} ms per frame "
                    f"({result.share(self.args.fps):5.0%} of the budget), {result.fps:4.1f} FPS, "
                    f"backlog {result.backlog * 1000:5.0f} ms -> {verdict}"
                )
        self.window.close()
        return results


def rate_text(fs: float) -> str:
    return f"{fs / 1000:g}k" if fs >= 1000 else f"{fs:g}"


def cell_text(result: Result) -> str:
    if not result.measured:
        return "skipped"
    lines = [f"{result.frame_ms:.0f} ms", f"{result.fps:.0f} FPS"]
    if result.backlog > BACKLOG_LIMIT:
        lines.append(f"lags {result.backlog:.1f} s")
    return "\n".join(lines)


def outline(ok: np.ndarray) -> list[tuple[list[int], list[int]]]:
    """The edges between fast-enough cells and the rest (or the grid's border), as line segments."""
    segments = []
    rows, cols = ok.shape
    for i in range(rows):
        for j in range(cols):
            if not ok[i, j]:
                continue
            if i == 0 or not ok[i - 1, j]:
                segments.append(([j, j + 1], [i, i]))
            if i == rows - 1 or not ok[i + 1, j]:
                segments.append(([j, j + 1], [i + 1, i + 1]))
            if j == 0 or not ok[i, j - 1]:
                segments.append(([j, j], [i, i + 1]))
            if j == cols - 1 or not ok[i, j + 1]:
                segments.append(([j + 1, j + 1], [i, i + 1]))
    return segments


def figure(results: list[Result], args: argparse.Namespace, window: str, idle: tuple[float, float]) -> plt.Figure:
    channels = sorted({r.channels for r in results})
    rates = sorted({r.fs for r in results})
    share = np.full((len(rates), len(channels)), np.nan)
    ok = np.zeros(share.shape, dtype=bool)

    fig, ax = plt.subplots(figsize=(2.5 + 1.1 * len(channels), 2.0 + 0.7 * len(rates)), layout="constrained")
    for r in results:
        i, j = rates.index(r.fs), channels.index(r.channels)
        share[i, j] = 100 * r.share(args.fps) if r.measured else np.nan
        ok[i, j] = r.fast_enough(args.max_load / 100, args.fps)
        light_cell = r.measured and 40 < share[i, j] < 140  # The colormap's light middle
        ax.text(
            j + 0.5,
            i + 0.5,
            cell_text(r),
            ha="center",
            va="center",
            fontsize=8,
            color="black" if light_cell else "white",
        )

    ax.set_facecolor("#6b1f1f")  # Skipped cells: overloaded
    mesh = ax.pcolormesh(np.ma.masked_invalid(share), cmap="RdYlGn_r", vmin=0, vmax=200, edgecolors="white")
    for xs, ys in outline(ok):
        ax.plot(xs, ys, color="black", linewidth=3, solid_capstyle="round")
    budget = 1000 / args.fps
    fig.colorbar(mesh, ax=ax, label=f"GUI thread time per frame [% of {budget:.0f} ms, a frame at {args.fps} FPS]")

    ax.set_xticks(np.arange(len(channels)) + 0.5, [str(c) for c in channels])
    ax.set_yticks(np.arange(len(rates)) + 0.5, [rate_text(fs) for fs in rates])
    ax.set_xlabel(f"Channels ({args.per_plot} per plot)")
    ax.set_ylabel("Sample rate per channel [Hz]")
    settings = (
        f"{args.fps} FPS, {args.duration:g} s of history, {window}, "
        f"{'full detail' if args.full_detail else 'downsampled'}, {args.block_rate:g} blocks/s\n"
        f"Empty window: {idle[0]:.0f} ms per frame, {idle[1]:.0f} FPS"
    )
    ax.set_title(f"Where Oscilab keeps up\n{settings}", fontsize=9)
    ax.legend(
        handles=[Line2D([], [], color="black", linewidth=3)],
        labels=[f"Fast enough: under {args.max_load:g} % of the frame time, and no backlog"],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.1),
        frameon=False,
        fontsize=8,
    )
    return fig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0].strip())
    parser.add_argument("--channels", type=int, nargs="+", default=[1, 2, 4, 8, 16, 32], help="channel counts")
    parser.add_argument(
        "--rates",
        type=float,
        nargs="+",
        default=[100, 300, 1_000, 3_000, 10_000, 30_000, 100_000],
        help="sample rates per channel, in Hz",
    )
    parser.add_argument("--per-plot", type=int, default=4, help="signals per time plot (default: 4)")
    parser.add_argument("--fps", type=int, choices=FPS_CHOICES, default=30, help="refresh rate (default: 30)")
    parser.add_argument("--duration", type=float, default=10.0, help="seconds of history kept (default: 10)")
    parser.add_argument("--full-detail", action="store_true", help="with the toolbar's Downsample off")
    parser.add_argument("--block-rate", type=float, default=100.0, help="blocks per second sent (default: 100)")
    parser.add_argument("--size", help="window size, e.g. 1280x800 (default: maximized)")
    parser.add_argument("--warmup", type=float, default=1.0, help="seconds before measuring each (default: 1)")
    parser.add_argument("--seconds", type=float, default=2.0, help="seconds measured each (default: 2)")
    parser.add_argument(
        "--max-load", type=float, default=75.0, help="%% of the frame time still fast enough (default: 75)"
    )
    parser.add_argument("--output", type=Path, default=Path("benchmark_refresh.png"), help="the figure's PNG")
    parser.add_argument("--no-show", action="store_true", help="only save the figure, don't show it")
    return parser.parse_args()


class _PowerStatus(ctypes.Structure):
    """Windows' SYSTEM_POWER_STATUS."""

    _fields_ = (
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    )


def on_battery() -> bool:
    """Whether the computer runs on battery, as far as it can tell: then the CPU's likely slowed down."""
    if sys.platform != "win32":
        return False
    status = _PowerStatus()
    return bool(ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status))) and status.ACLineStatus == 0


def main() -> None:
    args = parse_args()
    bench = Bench(args)
    offscreen = bench.app.platformName() == "offscreen"
    if args.no_show or offscreen:
        mpl.use("Agg")  # Nothing to show it on, or not wanted
    print(f"Measuring {len(args.channels)} x {len(args.rates)} setups, {args.warmup + args.seconds:g} s each ...")
    window = f"{bench.size} window"
    if on_battery():
        window += ", on battery"
        print("On battery: the CPU likely runs slower than on mains power, and so does Oscilab")
    print(f"Window: {bench.size}")
    idle = bench.idle()
    print(f"Empty window: {idle[0]:.1f} ms per frame, {idle[1]:.1f} FPS")
    results = bench.run()

    fig = figure(results, args, window, idle)
    fig.savefig(args.output, dpi=150)
    print(f"Saved {args.output.resolve()}")
    if not (args.no_show or offscreen):
        plt.show()


if __name__ == "__main__":
    main()
