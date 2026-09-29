"""Derived signals computed live from two existing SignalModels."""

import numpy as np

_FUNCS = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: np.divide(a, b, out=np.full_like(a, np.nan, dtype=float), where=b != 0),
}
OPERATORS = tuple(_FUNCS)  # A new operation only needs an entry in _FUNCS; the Math menu follows


class MathSignal:
    """
    `label = model_a <operator> model_b`, recomputed from scratch each tick.

    Not a `DataProducer` — no new samples originate here; it derives a
    dependent view over the time range both models' buffers cover, at A's
    timestamps (and so A's sampling rate). B is linearly interpolated onto
    them, so the two may have different rates or start times.
    """

    def __init__(self, label: str, model_a, model_b, operator: str):
        if operator not in _FUNCS:
            raise ValueError(f"Unsupported operator '{operator}'")
        self.label = label
        self.model_a = model_a
        self.model_b = model_b
        self.operator = operator
        self._fn = _FUNCS[operator]

    def compute(self) -> tuple[np.ndarray, np.ndarray]:
        """Return (t, x) for A's timestamps within the time range both sources cover."""
        t_a, data_a = self.model_a.snapshot()
        t_b, data_b = self.model_b.snapshot()
        if t_a.size == 0 or t_b.size == 0:
            return np.array([]), np.array([])

        # Pairing samples by position instead would combine values from
        # different moments whenever the rates or start times differ.
        overlap = (t_a >= t_b[0]) & (t_a <= t_b[-1])
        t = t_a[overlap]
        x = self._fn(data_a[overlap], np.interp(t, t_b, data_b))
        return t, x
