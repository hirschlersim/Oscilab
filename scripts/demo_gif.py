"""
Record the README's GIF: synthetic signals scrolling live in a time plot and
a spectrogram, then the trigger turned on, holding each record steady.

    uv run scripts/demo_gif.py            # writes docs/demo.gif
    uv run scripts/demo_gif.py --help     # its size, frame rate and timing

The window is grabbed every frame, offscreen (the same pixels on any
screen), and the frames written as a GIF with Pillow (in the bench group,
through matplotlib).
"""

import argparse
import itertools
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from PyQt6 import QtCore as qtc
from PyQt6 import QtGui as qtg

from oscilab import Oscilab, SyntheticProducer


def main() -> int:
    parser = argparse.ArgumentParser(description="Record the README's GIF")
    parser.add_argument("-o", "--output", type=Path, default=Path("docs/demo.gif"), help="the GIF (docs/demo.gif)")
    # Tall enough for the trigger's panel: the window would grow mid-recording
    parser.add_argument(
        "--size", type=int, nargs=2, default=(1100, 900), metavar=("W", "H"), help="window size (1100 900)"
    )
    parser.add_argument("--fps", type=int, default=12, help="frames a second (12)")
    parser.add_argument("--live", type=float, default=3.0, help="seconds scrolling, before the trigger (3)")
    parser.add_argument("--triggered", type=float, default=4.5, help="seconds triggered (4.5)")
    args = parser.parse_args()
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    if sys.platform == "win32":
        # Offscreen, Qt finds no fonts on Windows by itself: Windows' own, and
        # its default UI font (the style sheets name it too, but not the plots)
        os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", "C:/Windows"), "Fonts")))

    scope = Oscilab()
    if sys.platform == "win32":
        scope.app.setFont(qtg.QFont("Segoe UI", 9))
    scope.resize(*args.size)
    duration = 4.0
    scope.set_duration(duration)
    scope.set_time_mode("relative")  # Rolling under a still axis, as on a scope

    # Two channels sampled together, so in step: the trigger holds both steady
    scope.attach_producer(SyntheticProducer(fs=1_000, freq=(1.5, 4.5)), ["ch-1", "ch-2"], units="V")
    scope.add_timeseries_plot("Scope")
    scope.link(plot="Scope", label="ch-1")
    scope.link(plot="Scope", label="ch-2")

    # Two tones in noise, mixed: two lines in the spectrogram
    scope.attach_producer(SyntheticProducer(fs=500, freq=(40.0, 110.0), noise=0.2), ["tone-a", "tone-b"])
    scope.workspace.add_math_signal("mix", "+", "tone-a", "tone-b")
    scope.add_spectrogram_plot("mix", nperseg=128, noverlap=96)

    frames: list[Image.Image] = []

    def grab() -> None:
        image = scope.grab().toImage().convertToFormat(qtg.QImage.Format.Format_RGB888)
        data = image.constBits().asstring(image.sizeInBytes())
        size = (image.width(), image.height())
        frames.append(Image.frombuffer("RGB", size, data, "raw", "RGB", image.bytesPerLine(), 1).copy())

    capture = qtc.QTimer(scope)
    capture.setTimerType(qtc.Qt.TimerType.PreciseTimer)
    capture.timeout.connect(grab)
    warm_up = int((duration + 0.5) * 1000)  # Until the buffers are full: the plots span the whole duration
    qtc.QTimer.singleShot(warm_up, lambda: capture.start(1000 // args.fps))
    qtc.QTimer.singleShot(warm_up + int(args.live * 1000), lambda: scope.trigger_action.setChecked(True))
    qtc.QTimer.singleShot(warm_up + int((args.live + args.triggered) * 1000), scope.close)
    scope.run()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    save_gif(frames, args.output, 1000 // args.fps)
    print(f"{args.output}: {len(frames)} frames, {args.output.stat().st_size / 1e6:.1f} MB")
    return 0


def save_gif(frames: list[Image.Image], path: Path, frame_ms: int) -> None:
    """
    The frames as a GIF, kept small: one palette for all of them, and in each
    frame after the first, what's the same as in the one before left
    transparent — the toolbar, the sidebar and the axes are stored once.
    """
    # The palette, from the first, middle and last frames side by side; one
    # index left free, for "unchanged"
    w, h = frames[0].size
    sample = Image.new("RGB", (3 * w, h))
    for i, frame in enumerate((frames[0], frames[len(frames) // 2], frames[-1])):
        sample.paste(frame, (i * w, 0))
    palette = sample.quantize(colors=255, dither=Image.Dither.NONE)

    unchanged = 255
    indexed = [np.asarray(frame.quantize(palette=palette, dither=Image.Dither.NONE)) for frame in frames]
    out = [Image.fromarray(indexed[0], "P")]
    for before, now in itertools.pairwise(indexed):
        out.append(Image.fromarray(np.where(now == before, unchanged, now).astype(np.uint8), "P"))
    for image in out:
        image.putpalette(palette.getpalette() or [])
    out[0].save(
        path,
        save_all=True,
        append_images=out[1:],
        duration=frame_ms,
        loop=0,
        transparency=unchanged,
        disposal=1,  # Each frame drawn over the one before
        optimize=False,  # It would merge palettes, renumbering "unchanged"
    )


if __name__ == "__main__":
    raise SystemExit(main())
