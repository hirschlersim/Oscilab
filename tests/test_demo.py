"""
The demo (`oscilab-demo`) runs: its producers feed their signals, its plots
and toolbar controls are there — so an API change can't break it unnoticed.
"""

import time

import pytest

from oscilab import demo
from oscilab.oscilab import Oscilab


@pytest.fixture
def demo_window(qapp, monkeypatch):
    """The demo's window, after a moment of running, instead of blocking in its event loop."""
    windows: list[Oscilab] = []

    def run(window: Oscilab) -> int:
        windows.append(window)
        deadline = time.monotonic() + 5.0
        while not all(len(model.data) for model in window.workspace.signals.values()):
            assert time.monotonic() < deadline, "the demo's producers sent nothing"
            qapp.processEvents()
            time.sleep(0.01)
        return 0

    monkeypatch.setattr(Oscilab, "run", run)
    assert demo.run() == 0
    yield windows[0]
    windows[0].close()


def test_the_demo_feeds_its_signals(demo_window):
    workspace = demo_window.workspace
    assert list(workspace.signals) == ["channel-1", "channel-2", "channel-3", "async"]
    assert all(workspace.producer_state(label) == "running" for label in workspace.signals)
    assert workspace.signals["channel-1"].unit == "V"


def test_the_demo_shows_its_plots_and_controls(demo_window):
    workspace = demo_window.workspace
    assert workspace.plots["Sensors"].signals == ["channel-1", "channel-2", "async"]
    assert list(workspace.spectrograms) == ["channel-3"]
    toolbar = [action.text() for action in demo_window.toolbar.actions()]
    assert {"Reset Phase", "Noise", "Mark"} <= set(toolbar)
