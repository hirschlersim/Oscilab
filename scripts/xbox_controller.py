"""
An Xbox controller, live: `uv run scripts/xbox_controller.py`.

Pair the controller first — Windows' Settings > Bluetooth & devices > Add
device > Bluetooth, holding its pair button until the Xbox logo flashes fast
— or plug it in. Then move the sticks, pull the triggers and press buttons.
Started without one, the rows say so: connect it, and press Restart on one.
"""

from oscilab import Oscilab, XboxControllerProducer
from oscilab.xbox import STICKS, TRIGGERS


def main() -> int:
    scope = Oscilab()
    pad = XboxControllerProducer()
    scope.attach_producer(pad, pad.channels)  # One signal per channel, named like it: "left_x", "a", ...

    plots = {
        "Sticks": (STICKS, (-1.1, 1.1)),
        "Triggers": (TRIGGERS, (-0.1, 1.1)),
        "Buttons": (("a", "b", "x", "y", "lb", "rb"), (-0.1, 1.1)),  # Signals... adds the others
    }
    for title, (labels, (y_min, y_max)) in plots.items():
        plot = scope.add_timeseries_plot(title)
        for label in labels:
            scope.link(plot=title, label=label)
        plot.set_y_range(False, y_min, y_max)  # Fixed, instead of jumping as values change
    scope.set_duration(10)

    # "Rumble" is in the Producers menu: a short buzz from the controller
    return scope.run()


if __name__ == "__main__":
    raise SystemExit(main())
