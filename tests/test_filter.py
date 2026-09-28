"""
Low-pass filter setup.

scipy's Butterworth design raises ValueError unless 0 < cutoff < fs/2, and
an exception inside a Qt slot aborts the whole app — so the UI must never
hand SignalModel a cutoff at or above Nyquist.
"""

import pytest

from oscilab.signal_controller import FilterDialog, SignalRow
from oscilab.signal_model import SignalModel


@pytest.mark.usefixtures("qapp")
def test_dialog_caps_cutoff_below_nyquist():
    # 50 Hz was the old default cutoff, and exactly Nyquist at fs=100.
    dialog = FilterDialog(label="s", filter_enabled=True, current_cutoff=50.0, sampling_rate=100.0)
    _, cutoff, order = dialog.get_values()

    assert cutoff < 50.0
    SignalModel(label="s", fs=100.0).add_filter(cutoff=cutoff, order=order)


@pytest.mark.parametrize("fs", [10.0, 100.0, 1_000.0])
@pytest.mark.usefixtures("qapp")
def test_default_cutoff_is_valid_for_the_signal(fs):
    model = SignalModel(label="s", fs=fs)
    row = SignalRow(label="s", model=model)
    cutoff = row.filter_settings["cutoff"]

    assert 0 < cutoff < fs / 2
    model.add_filter(cutoff=cutoff, order=row.filter_settings["order"])


def test_model_rejects_cutoff_at_nyquist():
    with pytest.raises(ValueError, match="critical frequencies"):
        SignalModel(label="s", fs=100.0).add_filter(cutoff=50.0)
