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
    dependent view over the overlapping tail of two existing models' buffers.
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
        """Return (t, x) over the overlapping tail of both source buffers."""
        n = min(len(self.model_a.data), len(self.model_b.data))
        if n == 0:
            return np.array([]), np.array([])

        t_a, data_a = self.model_a.snapshot()
        _, data_b = self.model_b.snapshot()
        t = t_a[-n:]
        x = self._fn(data_a[-n:], data_b[-n:])
        return t, x
