"""MathSignal: live `A <op> B` over the overlapping tail of two signals."""

import numpy as np
import pytest

from oscilab.math_signal import OPERATORS, MathSignal
from oscilab.signal_model import SignalModel


def model_with(values, fs: float = 10.0) -> SignalModel:
    model = SignalModel(_label="m", _fs=fs)
    model.add_data(np.arange(len(values)) / fs, np.asarray(values, dtype=float))
    return model


@pytest.mark.parametrize(
    ("operator", "expected"),
    [("+", [5.0, 7.0, 9.0]), ("-", [-3.0, -3.0, -3.0]), ("*", [4.0, 10.0, 18.0]), ("/", [0.25, 0.4, 0.5])],
)
def test_operators(operator, expected):
    a, b = model_with([1, 2, 3]), model_with([4, 5, 6])
    t, x = MathSignal(label="c", model_a=a, model_b=b, operator=operator).compute()

    np.testing.assert_allclose(x, expected)
    np.testing.assert_array_equal(t, a.snapshot()[0])


def test_every_menu_operator_is_implemented():
    a, b = model_with([1.0]), model_with([2.0])
    for operator in OPERATORS:
        MathSignal(label="c", model_a=a, model_b=b, operator=operator).compute()


def test_uses_the_overlapping_tail_when_lengths_differ():
    a = model_with([1, 2, 3, 4, 5])
    b = model_with([10, 20])
    t, x = MathSignal(label="c", model_a=a, model_b=b, operator="+").compute()

    assert x.tolist() == [14.0, 25.0]  # the newest two of each
    np.testing.assert_array_equal(t, a.snapshot()[0][-2:])


def test_division_by_zero_is_nan_not_an_error():
    a, b = model_with([1, 2, 3]), model_with([1, 0, 2])
    _, x = MathSignal(label="c", model_a=a, model_b=b, operator="/").compute()

    assert x[0] == 1.0
    assert np.isnan(x[1])
    assert x[2] == 1.5


def test_empty_source_gives_empty_result():
    a, b = model_with([1, 2]), SignalModel(_label="empty", _fs=10.0)
    t, x = MathSignal(label="c", model_a=a, model_b=b, operator="+").compute()
    assert t.size == 0
    assert x.size == 0


def test_unknown_operator_is_rejected():
    a, b = model_with([1]), model_with([1])
    with pytest.raises(ValueError, match="Unsupported operator"):
        MathSignal(label="c", model_a=a, model_b=b, operator="%")
