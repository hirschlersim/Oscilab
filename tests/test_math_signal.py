"""MathSignal: live `A <op> B` over the time range both signals cover."""

import numpy as np
import pytest

from oscilab.math_signal import OPERATORS, MathSignal
from oscilab.signal_model import SignalModel


def model_with(values, fs: float = 10.0, start: float = 0.0) -> SignalModel:
    model = SignalModel(label="m", fs=fs)
    model.add_data(start + np.arange(len(values)) / fs, np.asarray(values, dtype=float))
    return model


def ramp(fs: float, seconds: float, slope: float, start: float = 0.0) -> SignalModel:
    """A signal whose value is `slope * t`: linear, so interpolating it is exact."""
    t = start + np.arange(round(seconds * fs)) / fs
    model = SignalModel(label="m", fs=fs)
    model.add_data(t, slope * t)
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


def test_different_sampling_rates_are_combined_at_the_same_times():
    a = ramp(fs=100, seconds=1.0, slope=1.0)  # a(t) = t
    b = ramp(fs=1_000, seconds=1.0, slope=10.0)  # b(t) = 10 t, ten times as many samples
    t, x = MathSignal(label="c", model_a=a, model_b=b, operator="+").compute()

    np.testing.assert_array_equal(t, a.snapshot()[0])  # A's timestamps, so A's rate
    np.testing.assert_allclose(x, 11 * t)  # a(t) + b(t), sample for sample


def test_only_the_time_range_both_cover_is_used():
    a = model_with([0, 1, 2, 3, 4, 5, 6, 7, 8, 9])  # t = 0.0 .. 0.9
    b = model_with([30, 40, 50, 60, 70], start=0.3)  # t = 0.3 .. 0.7
    t, x = MathSignal(label="c", model_a=a, model_b=b, operator="+").compute()

    np.testing.assert_allclose(t, [0.3, 0.4, 0.5, 0.6, 0.7])
    np.testing.assert_allclose(x, [33, 44, 55, 66, 77])


def test_signals_that_dont_overlap_in_time_give_nothing():
    a = model_with([1, 2, 3])  # t = 0.0 .. 0.2
    b = model_with([1, 2, 3], start=5.0)  # t = 5.0 .. 5.2
    t, x = MathSignal(label="c", model_a=a, model_b=b, operator="+").compute()
    assert t.size == 0
    assert x.size == 0


def test_division_by_zero_is_nan_not_an_error():
    a, b = model_with([1, 2, 3]), model_with([1, 0, 2])
    _, x = MathSignal(label="c", model_a=a, model_b=b, operator="/").compute()

    assert x[0] == 1.0
    assert np.isnan(x[1])
    assert x[2] == 1.5


def test_empty_source_gives_empty_result():
    a, b = model_with([1, 2]), SignalModel(label="empty", fs=10.0)
    t, x = MathSignal(label="c", model_a=a, model_b=b, operator="+").compute()
    assert t.size == 0
    assert x.size == 0


def test_unknown_operator_is_rejected():
    a, b = model_with([1]), model_with([1])
    with pytest.raises(ValueError, match="Unsupported operator"):
        MathSignal(label="c", model_a=a, model_b=b, operator="%")
