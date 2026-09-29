"""`uv run demo`: a tour of the public API with synthetic data."""

from oscilab import AsyncSyntheticProducer, Oscilab, SyntheticProducer


def run() -> int:
    oszi = Oscilab()

    # Producers. Each registers its own signals — one per channel, at its own
    # sampling rate — and starts feeding them right away.
    #
    # "channel-1": a single-channel producer, stored in a deque instead of the
    # default ring buffer, to show both backends behave the same ("ring" is far
    # cheaper per refresh tick at scale; see scripts/benchmark_buffers.py).
    oszi.attach_producer(SyntheticProducer(fs=100, freq=5.0), "channel-1", storage=oszi.storage.deque)
    # "channel-2"/"channel-3": ONE two-channel producer — a 1 Hz and a 10 Hz
    # sine sampled together, like a multi-channel DAQ.
    oszi.attach_producer(SyntheticProducer(fs=1_000, freq=(1.0, 10.0)), ["channel-2", "channel-3"])
    # "async": an asyncio-based producer, the template for async hardware
    # clients. Its "Reset Phase" button is on the signal's row — click the
    # row's name to expand it.
    oszi.attach_producer(AsyncSyntheticProducer(fs=200, freq=2.0), "async")

    # A time series plot with three of them...
    oszi.add_timeseries_plot("Sensors")
    oszi.link(plot="Sensors", label="channel-1")
    oszi.link(plot="Sensors", label="channel-2")
    oszi.link(plot="Sensors", label="async")

    # ...and a live spectrogram (STFT) of the 10 Hz one.
    oszi.add_spectrogram_plot(label="channel-3")

    # Blocks until the window is closed; also try the Math, Cursors and
    # View > Time menus, and each row's details (click its name): live
    # metrics and the Filter/Scaling buttons.
    return oszi.run()


if __name__ == "__main__":
    run()
