"""Shared fixtures."""

import os

import pytest
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
    """A headless Workspace; any producers attached to it are stopped afterwards."""
    ws = Workspace()
    yield ws
    ws.stop_producers()
