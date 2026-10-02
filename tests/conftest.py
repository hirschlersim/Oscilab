"""Shared fixtures."""

import os

import pytest
from PyQt6 import QtCore as qtc
from PyQt6 import QtWidgets as qtw

from oscilab.workspace import Workspace


@pytest.fixture(scope="session")
def qapp():
    """
    A QApplication, needed before any widget can be constructed.

    Runs on Qt's offscreen platform so tests work without a display (CI, SSH).
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return qtw.QApplication.instance() or qtw.QApplication([])


@pytest.fixture
def workspace(qapp):  # noqa: ARG001 — widgets need the QApplication to exist
    """
    A headless Workspace; any producers attached to it are stopped afterwards,
    and its plots deleted — now, not whenever Python's garbage collector
    gets to them: that can be in the middle of a later test's event
    processing, or on a producer's thread, and pyqtgraph then crashes.
    """
    ws = Workspace()
    yield ws
    ws.stop_producers()
    for title in list(ws.views):
        ws.remove_plot(title)
    qtc.QCoreApplication.sendPostedEvents(None, qtc.QEvent.Type.DeferredDelete.value)
