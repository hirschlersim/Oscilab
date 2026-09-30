"""`uv run oscilab-demo`: a tour of the public API with synthetic data."""

import time

from oscilab import AsyncSyntheticProducer, Oscilab, SyntheticProducer, button, toggle


class DemoTools:
    """
    Custom toolbar controls: methods of your own class, marked with @button /
    @toggle, show up in the toolbar next to Downsample.
    """

    def __init__(self, scope: Oscilab, noisy: SyntheticProducer) -> None:
        self.scope = scope
        self.noisy = noisy

    @toggle("Noise", tooltip="Add noise to channel-1", checked=True)
    def noise(self, on: bool) -> None:
        self.noisy.noise = 0.05 if on else 0.0

    @button("Mark", tooltip="Log a timestamped marker to the status bar and the log file")
    def mark(self) -> None:
        self.scope.log(f"Marker at {time.strftime('%H:%M:%S')}", level="success")


def run() -> int:
    scope = Oscilab()

    # Producers. Each registers its own signals — one per channel, at its own
    # sampling rate — and starts feeding them right away.
    #
    # "channel-1": a single-channel producer, stored in a deque instead of the
    # default ring buffer, to show both backends behave the same ("ring" is far
    # cheaper per refresh tick at scale; see scripts/benchmark_buffers.py).
    channel_1 = SyntheticProducer(fs=100, freq=5.0)
    scope.attach_producer(channel_1, "channel-1", storage=scope.storage.deque)
    # "channel-2"/"channel-3": ONE two-channel producer — a 1 Hz and a 10 Hz
    # sine sampled together, like a multi-channel DAQ.
    scope.attach_producer(SyntheticProducer(fs=1_000, freq=(1.0, 10.0)), ["channel-2", "channel-3"])
    # "async": an asyncio-based producer, the template for async hardware
    # clients. Its "Reset Phase" function — an @button(..., toolbar=True)
    # method — is in the toolbar, and in the Producers menu next to its Start
    # and Stop.
    scope.attach_producer(AsyncSyntheticProducer(fs=200, freq=2.0), "async")

    # A time series plot with three of them...
    scope.add_timeseries_plot("Sensors")
    scope.link(plot="Sensors", label="channel-1")
    scope.link(plot="Sensors", label="channel-2")
    scope.link(plot="Sensors", label="async")

    # ...and a live spectrogram (STFT) of the 10 Hz one.
    scope.add_spectrogram_plot(label="channel-3")

    # "Noise" and "Mark" in the toolbar (after "Reset Phase"), from the
    # DemoTools class above
    scope.add_toolbar(DemoTools(scope, channel_1))

    # Blocks until the window is closed; also try the Plots, Math, Producers,
    # Cursors and View > Time menus, each plot's panel (Signals..., Remove),
    # and each row's details (click its name): live metrics and the
    # Filter/Scaling buttons. File > Save Session keeps the whole setup;
    # `uv run oscilab <file>.json` reopens it without this script.
    return scope.run()


if __name__ == "__main__":
    run()
