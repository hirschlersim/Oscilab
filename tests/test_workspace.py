"""
Workspace: registration/linking rules and CSV/.mat/.h5 exports.

Runs headless — Workspace builds widgets (plots, signal rows) but never
needs a main window.
"""

import csv

import h5py
import numpy as np
import pytest
from scipy.io import loadmat

from oscilab.workspace import Workspace


def fill(workspace: Workspace, label: str, fs: float, n: int):
    """Register `label` and write `n` samples of a ramp into it."""
    model = workspace.reg_signal(label, fs=fs)
    t = np.arange(n) / fs
    model.add_data(t, t * 10)
    return t, t * 10


# --- registration and linking ---------------------------------------------


def test_duplicate_signal_is_rejected(workspace):
    workspace.reg_signal("a", fs=10)
    with pytest.raises(ValueError, match="already registered"):
        workspace.reg_signal("a", fs=10)


def test_duplicate_plot_is_rejected(workspace):
    workspace.add_plot("P")
    with pytest.raises(ValueError, match="already exists"):
        workspace.add_plot("P")


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda ws: ws.link(plot="missing", label="a"), "Unknown plot"),
        (lambda ws: ws.link(plot="P", label="missing"), "Unknown signal"),
        (lambda ws: ws.add_spectrogram("missing"), "Unknown signal"),
        (lambda ws: ws.add_math_signal("c", "+", "a", "missing"), "Unknown signal"),
        (lambda ws: ws.set_cursors("missing", enabled=True), "Unknown plot"),
    ],
)
def test_unknown_names_are_rejected(workspace, call, message):
    workspace.add_plot("P")
    workspace.reg_signal("a", fs=10)
    with pytest.raises(ValueError, match=message):
        call(workspace)


def test_a_signal_on_two_plots_gets_one_row(workspace):
    workspace.add_plot("P1")
    workspace.add_plot("P2")
    workspace.reg_signal("a", fs=10)
    workspace.link(plot="P1", label="a")
    workspace.link(plot="P2", label="a")

    assert list(workspace.signal_list.rows) == ["a"]
    assert "a" in workspace.plots["P1"].curves
    assert "a" in workspace.plots["P2"].curves


def test_set_duration_applies_to_existing_and_future_signals(workspace):
    workspace.reg_signal("before", fs=10)
    workspace.set_duration(3.0)
    workspace.reg_signal("after", fs=10)

    assert workspace.signals["before"].duration == 3.0
    assert workspace.signals["after"].duration == 3.0


def test_math_signal_is_recomputed_on_refresh(workspace):
    fill(workspace, "a", fs=10, n=5)
    fill(workspace, "b", fs=10, n=5)
    workspace.add_math_signal("sum", "+", "a", "b")

    workspace.refresh()

    _, a = workspace.signals["a"].snapshot()
    np.testing.assert_allclose(workspace.signals["sum"].snapshot()[1], a * 2)


# --- exports ----------------------------------------------------------------


def read_csv(path):
    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["time", "value"]
    return np.array(rows[1:], dtype=float).T


def test_csv_single_signal_uses_the_path_as_given(workspace, tmp_path):
    t, x = fill(workspace, "a", fs=10, n=8)
    written = workspace.export_csv(tmp_path / "out.csv")

    assert written == [tmp_path / "out.csv"]
    time_col, value_col = read_csv(written[0])
    np.testing.assert_allclose(time_col, t)
    np.testing.assert_allclose(value_col, x)


def test_csv_several_signals_get_one_file_each(workspace, tmp_path):
    fill(workspace, "channel-1", fs=10, n=3)
    fill(workspace, "a b/c", fs=10, n=3)  # characters that can't go in a filename
    written = workspace.export_csv(tmp_path / "out.csv")

    assert sorted(p.name for p in written) == ["out_a_b_c.csv", "out_channel-1.csv"]
    assert all(p.exists() for p in written)


def test_csv_holds_only_what_is_still_in_the_buffer(workspace, tmp_path):
    workspace.set_duration(1.0)
    t, x = fill(workspace, "a", fs=10, n=25)  # buffer keeps the last 10
    time_col, value_col = read_csv(workspace.export_csv(tmp_path / "out.csv")[0])

    np.testing.assert_allclose(time_col, t[-10:])
    np.testing.assert_allclose(value_col, x[-10:])


def test_mat_round_trip(workspace, tmp_path):
    t_a, x_a = fill(workspace, "a", fs=10, n=6)
    t_b, x_b = fill(workspace, "b", fs=100, n=4)
    path = workspace.export_mat(tmp_path / "out")  # suffix added

    assert path == tmp_path / "out.mat"
    mat = loadmat(path)
    np.testing.assert_allclose(mat["a_time"].ravel(), t_a)
    np.testing.assert_allclose(mat["a_data"].ravel(), x_a)
    np.testing.assert_allclose(mat["b_time"].ravel(), t_b)
    np.testing.assert_allclose(mat["b_data"].ravel(), x_b)


def test_h5_round_trip(workspace, tmp_path):
    t, x = fill(workspace, "channel 1", fs=250, n=7)
    path = workspace.export_h5(tmp_path / "out.h5")

    with h5py.File(path, "r") as f:
        group = f["channel_1"]  # sanitized group name...
        assert group.attrs["label"] == "channel 1"  # ...original label kept as an attribute
        assert group.attrs["fs"] == 250
        np.testing.assert_allclose(group["time"][()], t)
        np.testing.assert_allclose(group["data"][()], x)
