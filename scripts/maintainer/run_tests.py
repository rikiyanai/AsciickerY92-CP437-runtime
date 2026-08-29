#!/usr/bin/env python3
"""Run the maintainer test suite via pytest.

Picks up test paths from pyproject.toml (testpaths = ["tests", "scripts"]).
Exits 0 when all tests pass OR when no tests exist (clean project).
Exits non-zero when tests fail.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    py = sys.executable
    cmd = [
        py, "-m", "pytest",
        "--no-header",
        "-c", str(PROJECT_ROOT / "pyproject.toml"),
        *sys.argv[1:],
    ]

    # When run via startup_preflight no args are passed; the CI workflow
    # passes -v.  Default to a concise but informative run.
    # The testpaths are read from pyproject.toml by pytest itself.

    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=False,  # let output stream to terminal
        text=True,
    )

    # Exit 0 if pytest passed OR no tests were collected at all.
    if result.returncode == 0:
        return 0
    if result.returncode == 5:
        # pytest exit code 5 = no tests collected.  This is the normal
        # state when the project has no test files yet — do not block.
        return 0

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
