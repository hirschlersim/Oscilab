"""
`oscilab [session.json]` (or `python -m oscilab`): open Oscilab, optionally
rebuilding a setup saved with File > Save Session.
"""

import argparse
import json
import sys

from oscilab.oscilab import Oscilab
from oscilab.session import Session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oscilab", description="Live signal visualization.")
    parser.add_argument("session", nargs="?", help="a session file, saved with File > Save Session")
    parser.add_argument("--schema", action="store_true", help="print the session file format (JSON Schema) and exit")
    args = parser.parse_args(argv)

    if args.schema:
        print(json.dumps(Session.model_json_schema(), indent=2))  # noqa: T201 — it's the command's output
        return 0

    scope = Oscilab()
    if args.session:
        scope.load_session(args.session)
    return scope.run()


if __name__ == "__main__":
    sys.exit(main())
