"""
Workspace: registration/linking rules and CSV/.mat/.h5 exports.

Runs headless — Workspace builds widgets (plots, signal rows) but never
needs a main window.
"""

import csv
import re

import h5py
import numpy as np
import pytest
from PyQt6 import QtWidgets as qtw
from scipy.io import loadmat

from oscilab import workspace as workspace_module
from oscilab.view import View
from oscilab.workspace import Workspace


def fill(workspace: Workspace, label: str, fs: float, n: int):
    """Register `label` and write `n` samples of a ramp into it."""
    model = workspace.register_signal(label, fs=fs)
    t = np.arange(n) / fs
    model.add_data(t, t * 10)
    return t, t * 10


# --- registration and linking ---------------------------------------------


def test_duplicate_signal_is_rejected(workspace):
    workspace.register_signal("a", fs=10)
    with pytest.raises(ValueError, match="already registered"):
        workspace.register_signal("a", fs=10)


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
    workspace.register_signal("a", fs=10)
    with pytest.raises(ValueError, match=message):
        call(workspace)


def test_plot_titles_are_unique_across_plot_types(workspace):
    workspace.register_signal("s", fs=1_000)
    workspace.add_spectrogram("s")
    with pytest.raises(ValueError, match="already exists"):
        workspace.add_plot("s")


def test_signals_link_only_to_time_series_plots(workspace):
    workspace.register_signal("s", fs=1_000)
    workspace.add_spectrogram("s")
    with pytest.raises(ValueError, match="isn't a time-series plot"):
        workspace.link(plot="s", label="s")


def test_every_plot_type_implements_view(workspace):
    workspace.register_signal("s", fs=1_000)
    workspace.add_plot("P")
    workspace.add_spectrogram("s")
    assert all(isinstance(view, View) for view in workspace.views.values())
    assert set(workspace.plots) == {"P"}
    assert set(workspace.spectrograms) == {"s"}


def test_y_axis_button_works_for_every_plot_type(workspace, monkeypatch):
    asked = []

    def fake_ask_range(_parent, title, **kwargs):
        asked.append((title, kwargs["unit"]))
        return False, 1.0, 2.0  # "pin the range to [1, 2]"

    monkeypatch.setattr(workspace_module, "_ask_range", fake_ask_range)
    workspace.register_signal("s", fs=1_000)
    workspace.add_plot("P")
    workspace.add_spectrogram("s")

    for title in ("P", "s"):
        buttons = workspace.controllers[title].findChildren(qtw.QPushButton)
        next(b for b in buttons if b.text() == "Y-Axis").click()

    assert asked == [("Y-Axis | P", ""), ("Y-Axis | s", "Hz")]
    for view in workspace.views.values():
        assert (view.y_range_auto, view.y_range_min, view.y_range_max) == (False, 1.0, 2.0)


def test_a_signal_on_two_plots_gets_one_row(workspace):
    workspace.add_plot("P1")
    workspace.add_plot("P2")
    workspace.register_signal("a", fs=10)
    workspace.link(plot="P1", label="a")
    workspace.link(plot="P2", label="a")

    assert list(workspace.signal_list.rows) == ["a"]
    assert "a" in workspace.plots["P1"].curves
    assert "a" in workspace.plots["P2"].curves


def test_set_duration_applies_to_existing_and_future_signals(workspace):
    workspace.register_signal("before", fs=10)
    workspace.set_duration(3.0)
    workspace.register_signal("after", fs=10)

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


def test_mat_variable_names_are_valid_in_matlab(workspace, tmp_path):
    labels = ["channel-1", "1st", "temp °C", "a-b", "a_b", "__", "x" * 80]
    for i, label in enumerate(labels):
        workspace.register_signal(label, fs=10).add_data([0.0], [float(i)])

    mat = loadmat(workspace.export_mat(tmp_path / "out"))
    names = [key for key in mat if not key.startswith("__")]  # skip loadmat's own __header__ etc.

    assert len(names) == 2 * len(labels)  # every signal kept, none overwritten by a clash
    for name in names:
        assert re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", name), name  # MATLAB's isvarname
    assert mat["channel_1_data"].item() == 0.0
    assert mat["sig_1st_data"].item() == 1.0
    assert mat["temp_C_data"].item() == 2.0
    assert mat["a_b_data"].item() == 3.0
    assert mat["a_b_2_data"].item() == 4.0  # "a_b" clashed with "a-b"
    assert mat["sig_data"].item() == 5.0


def test_h5_round_trip(workspace, tmp_path):
    t, x = fill(workspace, "channel 1", fs=250, n=7)
    path = workspace.export_h5(tmp_path / "out.h5")

    with h5py.File(path, "r") as f:
        group = f["channel_1"]  # sanitized group name...
        assert group.attrs["label"] == "channel 1"  # ...original label kept as an attribute
        assert group.attrs["fs"] == 250
        np.testing.assert_allclose(group["time"][()], t)
        np.testing.assert_allclose(group["data"][()], x)
