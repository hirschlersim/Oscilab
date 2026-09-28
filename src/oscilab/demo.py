from oscilab.oscilab import Oscilab
from oscilab.producers import SyntheticProducer


def run():
    oszi = Oscilab()

    # Three signals from two producers. Each producer registers its own
    # signals, at its own sampling rate.
    #   - "channel-1" is stored in a deque, the others in the (default) ring
    #     buffer, to show both work identically. "ring" (a pre-allocated numpy
    #     circular buffer) is far cheaper per refresh tick at scale; see
    #     scripts/benchmark_buffers.py for the numbers.
    #   - "channel-2"/"channel-3" come from ONE two-channel producer (a 1 Hz
    #     and a 10 Hz sine, sampled together), like a multi-channel DAQ.
    oszi.attachProducer(SyntheticProducer(fs=100, freq=5.0), "channel-1", storage=oszi.storage.deque)
    oszi.attachProducer(SyntheticProducer(fs=1_000, freq=(1.0, 10.0)), ["channel-2", "channel-3"])

    # Time series plot with two of them
    oszi.addTimeSeriesPlot("Sensors")
    oszi.link(plot="Sensors", label="channel-1")
    oszi.link(plot="Sensors", label="channel-2")

    # Spectrogram (STFT) plot for a single, faster signal
    oszi.addSpectrogramPlot(label="channel-3")

    oszi.show()


if __name__ == "__main__":
    run()
