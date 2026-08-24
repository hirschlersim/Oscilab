from oscilab.oscilab import Oscilab
from oscilab.producers import SyntheticProducer


def run():

    oszi = Oscilab()

    # Time series plot with two signals — one on each storage backend, to
    # show both work identically. "ring" (a pre-allocated numpy circular
    # buffer) is far cheaper per refresh tick than "deque" at scale; see
    # scripts/benchmark_buffers.py for the numbers.
    oszi.addTimeSeriesPlot("Sensors")
    oszi.regSignal(label="channel-1", fs=100, storage=oszi.storage.deque)
    oszi.regSignal(label="channel-2", fs=1_000, storage=oszi.storage.ring)
    oszi.regSignal(label="channel-3", fs=1_000)
    oszi.link(plot="Sensors", label="channel-1")
    oszi.link(plot="Sensors", label="channel-2")

    # Spectrogram (STFT) plot for a single, faster signal
    oszi.addSpectrogramPlot(label="channel-2")

    # Feed all three with demo data sources (replace with real producers later)
    oszi.attachProducer(label="channel-1", producer=SyntheticProducer(fs=100, freq=5.0))
    oszi.attachProducer(label="channel-2", producer=SyntheticProducer(fs=1_000, freq=1.0))
    oszi.attachProducer(label="channel-3", producer=SyntheticProducer(fs=1_000, freq=10.0))

    oszi.show()


if __name__ == "__main__":
    run()
