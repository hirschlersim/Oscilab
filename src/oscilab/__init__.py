"""
Oscilab: live signal visualization with PyQt6 and pyqtgraph.

The public API is importable from the package itself::

    from oscilab import Oscilab, SyntheticProducer

    oszi = Oscilab()
    oszi.attach_producer(SyntheticProducer(fs=100), "sine")
    oszi.add_timeseries_plot("Plot")
    oszi.link(plot="Plot", label="sine")
    oszi.run()
"""

from importlib import metadata

from oscilab.oscilab import Oscilab
from oscilab.producers import (
    AsyncDataProducer,
    AsyncSyntheticProducer,
    DataProducer,
    FileReplayProducer,
    SerialProducer,
    SyncDataProducer,
    SyntheticProducer,
)
from oscilab.signal_model import SignalModel
from oscilab.storage import Storage
from oscilab.toolbar import button, toggle

try:
    __version__ = metadata.version("oscilab")
except metadata.PackageNotFoundError:  # Running from a source tree that isn't installed
    __version__ = "0+unknown"

__all__ = [
    "AsyncDataProducer",
    "AsyncSyntheticProducer",
    "DataProducer",
    "FileReplayProducer",
    "Oscilab",
    "SerialProducer",
    "SignalModel",
    "Storage",
    "SyncDataProducer",
    "SyntheticProducer",
    "button",
    "toggle",
]
