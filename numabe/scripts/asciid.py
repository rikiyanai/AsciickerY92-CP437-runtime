#!/usr/bin/env python3
"""Operator entry point for ASCIIID Classic and shared A3D data operations.

The contract, conversion policy, and Classic launcher remain owned by their
implementation modules. This command is the single executable interface.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.a3d import compatibility, runtime_data_contract
from scripts.classic import app as classic_app


COMMANDS = {
    "check-contract": lambda args: runtime_data_contract.main(["--check", *args]),
    "summary-contract": lambda args: runtime_data_contract.main(["--summary", *args]),
    "downgrade-to-classic": compatibility.main,
    "launch-classic": classic_app.main,
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=tuple(COMMANDS))
    if not args or args[0] in {"-h", "--help"}:
        parser.parse_args(args)
        return 0
    command = args.pop(0)
    if command not in COMMANDS:
        parser.error(f"unknown command: {command}")
    return COMMANDS[command](args)


if __name__ == "__main__":
    raise SystemExit(main())
