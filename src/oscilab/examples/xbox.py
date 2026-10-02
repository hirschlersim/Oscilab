"""
An Xbox controller, live: `uv run oscilab-xbox`.

Pair the controller first — Windows' Settings > Bluetooth & devices > Add
device > Bluetooth, holding its pair button until the Xbox logo flashes fast
— or plug it in. Then move the sticks, pull the triggers and press buttons.
Started without one, the rows say so: connect it, and press Restart on one.
"""

import argparse
from collections.abc import Sequence

from oscilab import Oscilab, XboxControllerProducer
from oscilab.xbox import BUTTONS, STICKS, TRIGGERS


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(description="Plot an Xbox controller's sticks, triggers and buttons live").parse_args(argv)
    scope = Oscilab()
    pad = XboxControllerProducer()
    scope.attach_producer(pad, pad.channels)  # One signal per channel, named like it: "left_x", "a", ...

    # Where the left stick is, and has been over the last two seconds
    stick = scope.add_xy_plot("Left stick", "left_x", "left_y", trail=2.0)
    stick.set_equal_axes(True)
    stick.set_x_range(False, -1.1, 1.1)
    stick.set_y_range(False, -1.1, 1.1)

    # Both sticks and the triggers over time, fixed ranges so they don't jump
    for title, labels, (y_min, y_max) in (("Sticks", STICKS, (-1.1, 1.1)), ("Triggers", TRIGGERS, (-0.1, 1.1))):
        plot = scope.add_timeseries_plot(title)
        for label in labels:
            scope.link(plot=title, label=label)
        plot.set_y_range(False, y_min, y_max)

    # Every button in a lane of its own
    scope.add_logic_plot("Buttons")
    for label in BUTTONS:
        scope.link(plot="Buttons", label=label)

    # "Rumble" is in the Producers menu: a short buzz from the controller
    return scope.run()


if __name__ == "__main__":
    raise SystemExit(main())
