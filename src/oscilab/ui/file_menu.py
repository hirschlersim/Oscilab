"""
The File menu: exporting plots and signals, the recording entries (from
`oscilab.ui.recording`), and opening and saving sessions.
"""

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Literal, NamedTuple

from PyQt6 import QtGui as qtg
from PyQt6 import QtWidgets as qtw

from oscilab.workspace import Workspace

if TYPE_CHECKING:
    from oscilab.oscilab import Oscilab


class _Export(NamedTuple):
    """One File-menu export format."""

    suffix: str  # Menu entry, e.g. ".png"
    tooltip: str
    file_filter: str  # QFileDialog filter, e.g. "PNG Image (*.png)"
    content: Literal["plots", "signals"]  # What gets exported (and must exist)
    write: Callable[[Workspace, str], object]  # The Workspace method that writes it


_EXPORTS = (
    _Export(".png", "Export every plot as a PNG image (raster)", "PNG Image (*.png)", "plots", Workspace.export_png),
    _Export(".svg", "Export every plot as an SVG image (vector)", "SVG Image (*.svg)", "plots", Workspace.export_svg),
    _Export(".pdf", "Export every plot as a PDF (vector)", "PDF File (*.pdf)", "plots", Workspace.export_pdf),
    _Export(
        ".csv",
        "Export every signal's data as CSV — one file per signal",
        "CSV File (*.csv)",
        "signals",
        Workspace.export_csv,
    ),
    _Export(
        ".mat",
        "Export every signal's data into one MATLAB .mat file",
        "MATLAB File (*.mat)",
        "signals",
        Workspace.export_mat,
    ),
    _Export(
        ".h5",
        "Export every signal's data into one HDF5 .h5 file",
        "HDF5 File (*.h5)",
        "signals",
        Workspace.export_h5,
    ),
)


class FileMenu:
    """The File menu; `recording` are the Save Stream and Replay entries, shown between exports and sessions."""

    def __init__(self, window: "Oscilab", recording: Sequence[qtg.QAction]) -> None:
        self.window = window
        menu = window.menuBar().addMenu("File")
        for export in _EXPORTS:
            action = qtg.QAction(export.suffix, window)
            action.setToolTip(export.tooltip)
            action.triggered.connect(lambda _checked=False, e=export: self.__export(e))
            menu.addAction(action)

        menu.addSeparator()
        menu.addActions(list(recording))

        menu.addSeparator()
        open_action = qtg.QAction("Open Session...", window)
        open_action.setToolTip(
            "Rebuild a saved setup: producers, plots and settings — in place of this one, or in a new window"
        )
        open_action.triggered.connect(self.__open_session)
        menu.addAction(open_action)
        save_action = qtg.QAction("Save Session...", window)
        save_action.setToolTip("Save this setup — producers, plots and settings, not the data — as JSON")
        save_action.triggered.connect(self.__save_session)
        menu.addAction(save_action)

    def __export(self, export: _Export) -> None:
        workspace = self.window.workspace
        has_content = workspace.views if export.content == "plots" else workspace.signals
        if not has_content:
            self.window.log(f"No {export.content} to export", level="warning")
            return

        path, _ = qtw.QFileDialog.getSaveFileName(
            self.window, f"Save {export.content.capitalize()} as {export.suffix}", "", export.file_filter
        )
        if path:
            try:
                export.write(workspace, path)
            except Exception as e:  # noqa: BLE001 — a file open in another program, say: reported (this is a Qt slot)
                self.__error(f"export the {export.content} as {export.suffix}", e)

    def __save_session(self) -> None:
        path, _ = qtw.QFileDialog.getSaveFileName(self.window, "Save Session", "", "Oscilab session (*.json)")
        if path:
            try:
                self.window.save_session(path)
            except Exception as e:  # noqa: BLE001 — reported, never fatal (this is a Qt slot)
                self.__error("save the session", e)

    def __open_session(self) -> None:
        path, _ = qtw.QFileDialog.getOpenFileName(self.window, "Open Session", "", "Oscilab session (*.json)")
        if not path:
            return
        replace = False
        if self.window.workspace.signals or self.window.workspace.views:
            choice = self.__ask_replace_or_new_window()
            if choice is None:
                return
            replace = choice
        try:
            self.window.open_session(path, replace=replace)
        except Exception as e:  # noqa: BLE001 — a bad file or a device that won't open: report it
            self.__error("open the session", e)

    def __ask_replace_or_new_window(self) -> bool | None:
        """For a session opened in a window in use: True to replace its setup, False for a new window, None to cancel."""
        box = qtw.QMessageBox(self.window)
        box.setIcon(qtw.QMessageBox.Icon.Question)
        box.setWindowTitle("Open Session")
        box.setText("This window is in use. Replace its setup with the session, or open it in a new window?")
        box.setInformativeText("Replacing stops and removes the current producers, signals and plots.")
        replace = box.addButton("Replace", qtw.QMessageBox.ButtonRole.AcceptRole)
        new_window = box.addButton("New Window", qtw.QMessageBox.ButtonRole.ActionRole)
        box.addButton(qtw.QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(replace)
        box.exec()
        clicked = box.clickedButton()
        if clicked is replace:
            return True
        if clicked is new_window:
            return False
        return None

    def __error(self, doing: str, error: Exception) -> None:
        """Say what couldn't be done, and why: in the status bar and in a message box."""
        self.window.log(f"Couldn't {doing}: {error}", level="error")
        qtw.QMessageBox.warning(self.window, f"Couldn't {doing}", str(error))
