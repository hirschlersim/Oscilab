"""Correctness tests for RingBuffer: it must behave like deque(maxlen=...)
for the same sequence of writes, including wraparound. Performance is
covered separately by scripts/benchmark_buffers.py, not here — timing
numbers make flaky pass/fail assertions.
"""

from collections import deque

import numpy as np
import pytest

from oscilab.ring_buffer import RingBuffer


def as_list(buf) -> list[float]:
    return list(np.asarray(buf))


@pytest.mark.parametrize(
    "maxlen, chunks",
    [
        (5, [[1, 2, 3]]),  # partial fill, no wraparound
        (5, [[1, 2, 3], [4, 5]]),  # exact fill across two writes
        (5, [[1, 2, 3, 4, 5], [6, 7]]),  # wraps past the end of the backing array
        (5, [[1], [2], [3], [4], [5], [6], [7]]),  # many small single-element writes
        (5, [[1, 2, 3, 4, 5, 6, 7, 8]]),  # one write bigger than maxlen
        (5, [[1, 2, 3], [4, 5, 6, 7, 8, 9, 10]]),  # second write alone exceeds maxlen
        (1, [[1, 2, 3]]),  # maxlen of 1
    ],
)
def test_matches_deque(maxlen, chunks):
    ring = RingBuffer(maxlen)
    ref = deque(maxlen=maxlen)

    for chunk in chunks:
        ring.extend(chunk)
        ref.extend(chunk)
        assert as_list(ring) == list(ref)
        assert len(ring) == len(ref)
        if ref:
            assert ring[0] == ref[0]
            assert ring[-1] == ref[-1]


def test_empty_buffer():
    ring = RingBuffer(5)
    assert len(ring) == 0
    assert as_list(ring) == []
    with pytest.raises(IndexError):
        ring[0]


def test_extend_with_empty_chunk_is_a_no_op():
    ring = RingBuffer(5)
    ring.extend([1, 2, 3])
    ring.extend([])
    assert as_list(ring) == [1.0, 2.0, 3.0]


def test_clear():
    ring = RingBuffer(5)
    ring.extend([1, 2, 3])
    ring.clear()
    assert len(ring) == 0
    assert as_list(ring) == []
    ring.extend([4, 5])
    assert as_list(ring) == [4.0, 5.0]


def test_iteration_matches_array_order():
    ring = RingBuffer(3)
    ring.extend([1, 2, 3, 4, 5])  # wraps: contents should be [3, 4, 5]
    assert list(ring) == [3.0, 4.0, 5.0]


def test_maxlen_must_be_positive():
    with pytest.raises(AssertionError):
        RingBuffer(0)
