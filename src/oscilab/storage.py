"""
Rolling-buffer backend choice for `SignalModel`.

Exposed on `Oscilab` as `Oscilab.storage` (and on any instance as
`scope.storage`), so callers write `scope.attach_producer(..., storage=scope.storage.ring)`
instead of passing a bare string.
"""

from enum import Enum


class Storage(Enum):
    # collections.deque(maxlen=...) — simple, no pre-allocation
    deque = "deque"
    # RingBuffer — pre-allocated numpy circular buffer, much faster np.asarray()
    # reads; see oscilab.benchmarks.buffers
    ring = "ring"
