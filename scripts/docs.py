"""
Build the API reference from the docstrings, with pdoc.

    uv run --group docs scripts/docs.py           # browse it live, at localhost:8080
    uv run --group docs scripts/docs.py -o site   # write static HTML into site/

Documents the package itself (what `from oscilab import ...` offers) and the
modules someone extending Oscilab reads: writing producers, toolbar controls,
the session format, adding a plot type, and the workspace. Further arguments
go to pdoc (`--help` lists them).
"""

import sys

from pdoc.__main__ import cli

MODULES = [
    "oscilab",
    "oscilab.producers",
    "oscilab.xbox",
    "oscilab.toolbar",
    "oscilab.session",
    "oscilab.view",
    "oscilab.workspace",
    "oscilab.log",
    "oscilab.display",
    "oscilab.storage",
    "oscilab.signal_model",
    "oscilab.math_signal",
    "oscilab.ring_buffer",
    "oscilab.timeseries_plot",
    "oscilab.logic_plot",
    "oscilab.xy_plot",
    "oscilab.histogram_plot",
    "oscilab.spectrogram_plot",
    "oscilab.exporters",
    "oscilab.recorder",
    "oscilab.replay",
]

if __name__ == "__main__":
    cli([*MODULES, "--docformat", "markdown", *sys.argv[1:]])
