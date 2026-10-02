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

from oscilab import plotting
from oscilab.trigger import TriggerSettings
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

    monkeypatch.setattr(plotting, "_ask_range", fake_ask_range)
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


@pytest.mark.parametrize("duration", [0, -1.0])
def test_a_duration_must_be_positive(workspace, duration):
    with pytest.raises(ValueError, match="must be positive"):
        workspace.set_duration(duration)


def test_a_shorter_duration_shortens_a_record_that_no_longer_fits(workspace):
    # Else the record could never complete: the trigger would wait forever
    workspace.register_signal("s", fs=1_000)
    workspace.set_trigger(TriggerSettings(source="s", time_per_div=0.5))  # 5 s records
    told: list[bool] = []
    workspace.trigger_changed = lambda: told.append(True)

    workspace.set_duration(2.0)

    assert workspace.trigger is not None
    assert workspace.trigger.settings.time_per_div == 0.2  # The most that fits: 2 s
    assert told  # The trigger panel shows it


def test_a_record_that_still_fits_is_left_alone(workspace):
    workspace.register_signal("s", fs=1_000)
    workspace.set_trigger(TriggerSettings(source="s", time_per_div=0.1))
    workspace.set_duration(5.0)
    assert workspace.trigger is not None
    assert workspace.trigger.settings.time_per_div == 0.1


def test_the_trigger_goes_off_when_no_record_fits(workspace):
    workspace.register_signal("s", fs=1_000_000)
    workspace.set_trigger(TriggerSettings(source="s", time_per_div=0.001))
    workspace.set_duration(1e-5)  # Less than the shortest record, 10 x 10 µs
    assert workspace.trigger is None


def test_math_signal_is_recomputed_on_refresh(workspace):
    fill(workspace, "a", fs=10, n=5)
    fill(workspace, "b", fs=10, n=5)
    workspace.add_math_signal("sum", "+", "a", "b")

    workspace.refresh()

    _, a = workspace.signals["a"].snapshot()
    np.testing.assert_allclose(workspace.signals["sum"].snapshot()[1], a * 2)


# --- relative time ----------------------------------------------------------


def lagging_pair(workspace: Workspace, fs: float = 10.0):
    """'a' has data up to t = 10.0 s; 'b', on the same clock but arriving later, up to 9.5 s."""
    t_a = np.arange(round(10.0 * fs) + 1) / fs
    t_b = np.arange(round(9.5 * fs) + 1) / fs
    workspace.register_signal("a", fs=fs).add_data(t_a, t_a)  # a(t) = t
    workspace.register_signal("b", fs=fs).add_data(t_b, 2 * t_b)  # b(t) = 2t


def newest_x(plot, label: str) -> float:
    # As set, before downsampling: that may leave out the last few samples, depending on the plot's width
    return float(plot.curves[label].getOriginalDataset()[0][-1])


def test_relative_time_puts_signals_on_one_clock(workspace):
    lagging_pair(workspace)
    plot, _ = workspace.add_plot("P")
    workspace.link(plot="P", label="a")
    workspace.link(plot="P", label="b")
    workspace.set_time_mode("relative")
    workspace.refresh()

    assert newest_x(plot, "a") == pytest.approx(0.0)
    assert newest_x(plot, "b") == pytest.approx(-0.5)  # Its newest sample is half a second older


def test_relative_time_is_shared_across_plots(workspace):
    lagging_pair(workspace)
    plot_a, _ = workspace.add_plot("A")
    plot_b, _ = workspace.add_plot("B")  # X-linked to A, so both axes must mean the same time
    workspace.link(plot="A", label="a")
    workspace.link(plot="B", label="b")
    workspace.set_time_mode("relative")
    workspace.refresh()

    assert newest_x(plot_a, "a") == pytest.approx(0.0)
    assert newest_x(plot_b, "b") == pytest.approx(-0.5)


def test_relative_cursor_readout_uses_the_shared_clock(workspace):
    lagging_pair(workspace)
    plot, _ = workspace.add_plot("P")
    workspace.link(plot="P", label="a")
    workspace.link(plot="P", label="b")
    workspace.set_time_mode("relative")
    workspace.refresh()
    workspace.set_cursors("P", enabled=True)
    plot.cursor_region.setRegion((-1.0, -0.5))  # t = 9.0 s and 9.5 s, for both signals

    readout = plot.cursor_readout()["signals"]
    assert readout["a"]["value_a"] == pytest.approx(9.0)
    assert readout["b"]["value_a"] == pytest.approx(18.0)  # b(9.0), not b at 9.0 s before b's own newest


def test_relative_spectrogram_uses_the_shared_clock(workspace):
    lagging_pair(workspace, fs=1_000.0)  # enough samples for STFT windows
    workspace.add_plot("P")
    workspace.link(plot="P", label="a")
    spectrogram, _ = workspace.add_spectrogram("b")
    workspace.set_time_mode("relative")
    workspace.refresh()

    right = spectrogram.img.mapRectToView(spectrogram.img.boundingRect()).right()
    assert -0.6 < right < -0.4  # b's newest column, 0.5 s before a's newest sample (± a column)


def test_signals_not_shown_anywhere_dont_move_now(workspace):
    lagging_pair(workspace)
    workspace.register_signal("elsewhere", fs=1).add_data([1e9], [0.0])  # Another clock entirely
    plot, _ = workspace.add_plot("P")
    workspace.link(plot="P", label="b")
    workspace.set_time_mode("relative")
    workspace.refresh()

    assert newest_x(plot, "b") == pytest.approx(0.0)


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


@pytest.mark.parametrize("labels", [["a b", "a_b"], ["Plot", "plot"], ["x/1", "x 1", "x_1"]])
def test_csv_files_of_names_that_clash_dont_overwrite_each_other(workspace, tmp_path, labels):
    # Made safe for a file name - and, on Windows, regardless of case - these share one
    for i, label in enumerate(labels):
        workspace.register_signal(label, fs=10).add_data([0.0], [float(i)])
    written = workspace.export_csv(tmp_path / "out.csv")

    assert len({path.name.lower() for path in written}) == len(labels)
    assert [read_csv(path)[1].item() for path in written] == list(range(len(labels)))


def test_csv_is_utf8(workspace, tmp_path):
    fill(workspace, "a", fs=10, n=2)
    written = workspace.export_csv(tmp_path / "out.csv")[0]
    written.read_bytes().decode("utf-8")  # Whatever the system's code page


def test_png_files_of_titles_that_clash_dont_overwrite_each_other(workspace, tmp_path):
    workspace.add_plot("P 1")
    workspace.add_plot("P_1")
    written = workspace.export_png(tmp_path / "out.png")
    assert sorted(path.name for path in written) == ["out_P_1.png", "out_P_1_2.png"]


def test_h5_keeps_signals_whose_names_clash(workspace, tmp_path):
    workspace.register_signal("a-b", fs=10).add_data([0.0], [1.0])
    workspace.register_signal("a_b", fs=10).add_data([0.0], [2.0])
    path = workspace.export_h5(tmp_path / "out.h5")  # Failed: the group "a_b" existed already

    with h5py.File(path, "r") as f:
        values = {group.attrs["label"]: group["data"][0] for group in f.values()}
    assert values == {"a-b": 1.0, "a_b": 2.0}


# --- removing at runtime ------------------------------------------------------


def test_unlink_takes_the_curve_and_legend_entry_off(workspace):
    fill(workspace, "a", fs=10, n=5)
    fill(workspace, "b", fs=10, n=5)
    plot, _ = workspace.add_plot("P")
    workspace.link(plot="P", label="a")
    workspace.link(plot="P", label="b")

    workspace.unlink("P", "a")

    assert list(plot.curves) == ["b"]
    assert [label.text for _sample, label in plot.plotItem.legend.items] == ["b"]
    assert "a" not in workspace.signal_list.rows  # No plot shows it anymore
    assert "a" in workspace.signals  # The signal itself stays


def test_a_row_stays_while_another_plot_still_shows_the_signal(workspace):
    fill(workspace, "a", fs=10, n=5)
    workspace.add_plot("P1")
    workspace.add_plot("P2")
    workspace.link(plot="P1", label="a")
    workspace.link(plot="P2", label="a")

    workspace.unlink("P1", "a")
    assert "a" in workspace.signal_list.rows
    workspace.signal_list.rows["a"].set_visible(False)  # Still wired to P2 only
    assert not workspace.plots["P2"].curves["a"].isVisible()


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda ws: ws.link(plot="P", label="a"), "already shown"),
        (lambda ws: ws.unlink("P", "b"), "isn't shown"),
        (lambda ws: ws.unlink("nope", "a"), "Unknown time-series plot"),
        (lambda ws: ws.remove_plot("nope"), "Unknown plot"),
    ],
)
def test_link_and_unlink_mistakes_are_refused(workspace, call, message):
    fill(workspace, "a", fs=10, n=5)
    fill(workspace, "b", fs=10, n=5)
    workspace.add_plot("P")
    workspace.link(plot="P", label="a")
    with pytest.raises(ValueError, match=message):
        call(workspace)


def test_remove_plot_keeps_the_signals(workspace):
    fill(workspace, "a", fs=10, n=5)
    workspace.add_plot("P")
    workspace.link(plot="P", label="a")

    workspace.remove_plot("P")

    assert "P" not in workspace.views
    assert "P" not in workspace.controllers
    assert "a" in workspace.signals
    assert "a" not in workspace.signal_list.rows


def test_removing_the_first_plot_relinks_the_others_time_axes(workspace):
    fill(workspace, "a", fs=10, n=5)
    for title in ("P1", "P2", "P3"):
        workspace.add_plot(title)

    workspace.remove_plot("P1")  # The one the others were linked to
    workspace.plots["P2"].setXRange(3.0, 4.0, padding=0)

    x_min, x_max = workspace.plots["P3"].getViewBox().viewRange()[0]
    assert (x_min, x_max) == pytest.approx((3.0, 4.0))


def test_a_removed_spectrogram_no_longer_follows_its_row(workspace, qapp):
    fill(workspace, "a", fs=1_000, n=10)
    workspace.add_plot("P")
    workspace.link(plot="P", label="a")
    workspace.add_spectrogram("a")

    workspace.remove_plot("a")
    qapp.processEvents()  # Lets the deleted widget actually go
    workspace.signal_list.rows["a"].set_visible(False)  # Would crash if still wired to it
    assert list(workspace.views) == ["P"]


def test_remove_signal_removes_a_math_signal_everywhere(workspace):
    fill(workspace, "a", fs=10, n=5)
    fill(workspace, "b", fs=10, n=5)
    workspace.add_math_signal("sum", "+", "a", "b")
    workspace.add_plot("P")
    workspace.link(plot="P", label="sum")
    workspace.add_spectrogram("sum")

    workspace.remove_signal("sum")

    assert "sum" not in workspace.signals
    assert "sum" not in workspace.math_signals
    assert "sum" not in workspace.plots["P"].curves
    assert "sum" not in workspace.views
    assert "sum" not in workspace.signal_list.rows
    workspace.refresh()  # Nothing left that still refers to it


def test_remove_signal_refuses_what_would_break(workspace):
    from oscilab.producers import SyntheticProducer  # noqa: PLC0415

    workspace.attach_producer(SyntheticProducer(fs=100), "fed")
    fill(workspace, "a", fs=10, n=5)
    workspace.add_math_signal("double", "+", "a", "a")

    with pytest.raises(ValueError, match="detach the producer instead"):
        workspace.remove_signal("fed")
    with pytest.raises(ValueError, match="used by math signal"):
        workspace.remove_signal("a")


def test_the_math_rows_remove_button_removes_it(workspace):
    fill(workspace, "a", fs=10, n=5)
    workspace.add_math_signal("double", "+", "a", "a")
    workspace.add_plot("P")
    workspace.link(plot="P", label="double")
    row = workspace.signal_list.rows["double"]
    row.set_expanded(True)

    next(b for b in row.findChildren(qtw.QPushButton) if b.text() == "Remove").click()
    assert "double" not in workspace.signals


def test_detaching_a_producer_takes_its_signals_with_it(workspace):
    from oscilab.producers import SyntheticProducer  # noqa: PLC0415

    producer = SyntheticProducer(fs=100, freq=(1.0, 2.0))
    workspace.attach_producer(producer, ["x", "y"])
    fill(workspace, "own", fs=10, n=5)
    plot, _ = workspace.add_plot("P")
    workspace.link(plot="P", label="x")
    workspace.link(plot="P", label="own")
    workspace.add_spectrogram("y")

    assert workspace.detach_producer("y") == ["x", "y"]  # Any of its signals names it

    assert not producer.running
    assert set(workspace.signals) == {"own"}
    assert workspace.producers == {}
    assert list(plot.curves) == ["own"]
    assert "y" not in workspace.views  # Its spectrogram went too
    assert list(workspace.signal_list.rows) == ["own"]


def test_detaching_is_refused_while_a_math_signal_uses_it(workspace):
    from oscilab.producers import SyntheticProducer  # noqa: PLC0415

    producer = SyntheticProducer(fs=100)
    workspace.attach_producer(producer, "x")
    workspace.add_math_signal("twice", "+", "x", "x")

    with pytest.raises(ValueError, match="used by math signal"):
        workspace.detach_producer("x")
    assert producer.running
    with pytest.raises(ValueError, match="No producer feeds"):
        workspace.detach_producer("twice")


def test_a_spectrogram_signal_shown_as_a_curve_too_gets_a_color(workspace):
    workspace.register_signal("s", fs=100)
    workspace.add_spectrogram("s")
    row = workspace.signal_list.rows["s"]
    assert not row.color_button.isEnabled()  # Nothing drawn as a line yet

    workspace.add_plot("P")
    workspace.link(plot="P", label="s")
    assert row.color_button.isEnabled()
