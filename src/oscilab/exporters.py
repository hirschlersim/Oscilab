"""
File exports: plots as PNG/SVG/PDF images, signal data as CSV/.mat/.h5.

Plain functions over `{title: PlotItem}` or `{label: SignalModel}` that
return what they wrote; `Workspace.export_*` calls them with its registries
and does the logging.

With several plots/signals, the one-file-per-item formats (images, CSV)
append each item's name to the chosen filename; .mat/.h5 bundle every
signal into the one file, the idiomatic layout for both.
"""

import csv
import re
from pathlib import Path

import h5py
import pyqtgraph as pg
import pyqtgraph.exporters as pg_exporters
from PyQt6 import QtCore as qtc
from PyQt6 import QtGui as qtg
from PyQt6.QtSvg import QSvgGenerator
from scipy.io import savemat

from oscilab.signal_model import SignalModel


def safe_name(name: str) -> str:
    """Sanitize a plot/signal label for use as a filename fragment."""
    return re.sub(r"[^\w\-]+", "_", name).strip("_") or "signal"


_MATLAB_NAME_MAX = 63  # MATLAB's `namelengthmax`


def matlab_names(labels: list[str]) -> dict[str, str]:
    """
    A valid, unique MATLAB variable-name base for each label: ASCII letters,
    digits and underscores, starting with a letter, and short enough that
    `<base>_time` / `<base>_data` stay within MATLAB's 63-character limit —
    e.g. `channel-1` → `channel_1`, `1st` → `sig_1st`. Labels that end up
    the same (`a-b`, `a_b`) are numbered: `a_b`, `a_b_2`.
    """
    longest = _MATLAB_NAME_MAX - len("_data")
    names: dict[str, str] = {}
    taken: set[str] = set()
    for label in labels:
        base = re.sub(r"[^A-Za-z0-9_]+", "_", label).strip("_")
        if not base[:1].isalpha():  # Empty, or starts with a digit
            base = f"sig_{base}".rstrip("_")
        base = candidate = base[:longest]
        n = 2
        while candidate in taken:
            tail = f"_{n}"
            candidate = base[: longest - len(tail)] + tail
            n += 1
        taken.add(candidate)
        names[label] = candidate
    return names


def spread_path(base_path: Path, name: str, count: int, suffix: str) -> Path:
    """`base_path` as-is for a single item; `<base_path>_<name><suffix>` for several."""
    if count <= 1:
        return base_path.with_suffix(suffix)
    return base_path.with_name(f"{base_path.stem}_{safe_name(name)}{suffix}")


class _PainterExporter(pg_exporters.Exporter):
    """
    Renders a plot straight onto a Qt paint device — the same
    `getSourceRect()` + `setExportMode()` + `scene.render()` pattern
    pyqtgraph's own ImageExporter uses internally. Subclasses supply the
    device via `_device()`.
    """

    def _device(self, file_name: str, width: float, height: float) -> qtg.QPaintDevice:
        raise NotImplementedError

    def export(self, file_name: str):
        source_rect = self.getSourceRect()
        device = self._device(file_name, source_rect.width(), source_rect.height())
        target_rect = qtc.QRectF(0, 0, source_rect.width(), source_rect.height())

        painter = qtg.QPainter(device)
        try:
            self.setExportMode(True, {"antialias": True})
            self.getScene().render(painter, target_rect, source_rect)
        finally:
            self.setExportMode(False)
            painter.end()


class _PdfExporter(_PainterExporter):
    """
    pyqtgraph ships no direct-to-file PDF exporter — its `PrintExporter`
    always pops up an OS print dialog — so render to a `QPdfWriter`.
    """

    def _device(self, file_name, width, height):
        writer = qtg.QPdfWriter(file_name)
        writer.setResolution(96)
        writer.setPageSize(qtg.QPageSize(qtc.QSizeF(width, height), qtg.QPageSize.Unit.Point))
        writer.setPageMargins(qtc.QMarginsF(0, 0, 0, 0))
        return writer


class _SvgExporter(_PainterExporter):
    """
    Stands in for pyqtgraph's SVGExporter, whose post-processor parses Qt's
    SVG path data in an older format and crashes on Qt 6.11 (pyqtgraph 0.14:
    "not enough values to unpack" in `correctCoordinates`). Qt's own
    `QSvgGenerator` writes the vector output without that step.
    """

    def _device(self, file_name, width, height):
        generator = QSvgGenerator()
        generator.setFileName(file_name)
        generator.setSize(qtc.QSize(round(width), round(height)))
        generator.setViewBox(qtc.QRectF(0, 0, width, height))
        return generator


IMAGE_EXPORTERS = {
    ".png": pg_exporters.ImageExporter,  # raster
    ".svg": _SvgExporter,  # vector
    ".pdf": _PdfExporter,  # vector
}


def export_images(
    plot_items: dict[str, pg.PlotItem], base_path, suffix: str
) -> tuple[list[Path], dict[str, Exception]]:
    """
    Render every plot to an image file of type `suffix` (a key of
    `IMAGE_EXPORTERS`). Returns `(written, failed)`, `failed` mapping a
    plot's title to what went wrong.

    A plot that fails to export is skipped and reported, not fatal: it
    shouldn't crash the app or stop the other plots from exporting.
    """
    base_path = Path(base_path)
    exporter_cls = IMAGE_EXPORTERS[suffix]
    written, failed = [], {}
    for title, plot_item in plot_items.items():
        path = spread_path(base_path, title, len(plot_items), suffix)
        try:
            exporter_cls(plot_item).export(str(path))
        except Exception as e:  # noqa: BLE001 — see docstring
            failed[title] = e
        else:
            written.append(path)
    return written, failed


def export_csv(signals: dict[str, SignalModel], base_path) -> list[Path]:
    """One `time,value` CSV per signal."""
    base_path = Path(base_path)
    written = []
    for label, model in signals.items():
        path = spread_path(base_path, label, len(signals), ".csv")
        time_arr, data_arr = model.snapshot()
        with path.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["time", "value"])
            writer.writerows(zip(time_arr, data_arr, strict=True))
        written.append(path)
    return written


def export_mat(signals: dict[str, SignalModel], path) -> Path:
    """
    Every signal into one MATLAB .mat file, as `<name>_time` / `<name>_data`
    variables, `<name>` being the label made a valid MATLAB variable name
    (see `matlab_names`): `channel-1` → `channel_1_time`, `channel_1_data`.
    """
    path = Path(path).with_suffix(".mat")
    names = matlab_names(list(signals))
    mat_dict = {}
    for label, model in signals.items():
        time_arr, data_arr = model.snapshot()
        mat_dict[f"{names[label]}_time"] = time_arr
        mat_dict[f"{names[label]}_data"] = data_arr
    savemat(str(path), mat_dict)
    return path


def export_h5(signals: dict[str, SignalModel], path) -> Path:
    """Every signal into one HDF5 file: a group per signal with `time`/`data` datasets and `fs`/`label` attributes."""
    path = Path(path).with_suffix(".h5")
    with h5py.File(path, "w") as f:
        for label, model in signals.items():
            time_arr, data_arr = model.snapshot()
            group = f.create_group(safe_name(label))
            group.create_dataset("time", data=time_arr)
            group.create_dataset("data", data=data_arr)
            group.attrs["fs"] = model.fs
            group.attrs["label"] = label
    return path
