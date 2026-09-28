"""
Rolling-buffer backend choice for `SignalModel`.

Exposed on `Oscilab` as `Oscilab.storage` (and on any instance as
`oszi.storage`), so callers write `oszi.regSignal(..., storage=oszi.storage.ring)`
instead of passing a bare string.
"""

from enum import Enum


class Storage(Enum):
    # collections.deque(maxlen=...) — simple, no pre-allocation
    deque = "deque"
    # RingBuffer — pre-allocated numpy circular buffer, much faster np.asarray()
    # reads; see scripts/benchmark_buffers.py
    ring = "ring"
