"""
Display settings the window, the workspace, its plots and saved sessions
share: each one's choices, as a type (so a typo is a type error) and as the
tuple the menus and toolbar offer.
"""

from typing import Literal, get_args

Theme = Literal["dark", "light"]
TimeMode = Literal["global", "relative"]  # "global": time as recorded; "relative": the newest sample at 0
Fps = Literal[5, 10, 30, 60]  # Redraw rates

THEMES: tuple[Theme, ...] = get_args(Theme)
TIME_MODES: tuple[TimeMode, ...] = get_args(TimeMode)
FPS_CHOICES: tuple[Fps, ...] = get_args(Fps)

# The curves' line width, in pixels: Qt draws only 1-pixel lines the fast
# way; at 1.5, curves took twice as long (see scripts/benchmark_refresh.py).
CURVE_WIDTH = 1
