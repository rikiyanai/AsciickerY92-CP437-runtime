"""Explicit external side effects for the FL-4512 commit helper."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable


Runner = Callable[..., subprocess.CompletedProcess]


def push_origin(
    root: str | Path,
    authorized: bool,
    runner: Runner = subprocess.run,
) -> int | None:
    """Push only when the caller explicitly supplied ``--push``."""
    if not authorized:
        return None
    result = runner(
        ["git", "push", "-q", "origin", "main"],
        cwd=str(root),
        capture_output=True,
        check=False,
    )
    return result.returncode
