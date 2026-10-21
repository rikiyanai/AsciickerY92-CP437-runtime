#!/usr/bin/env python3
"""FL-4162 complete upstream-XP discovery front door.

The historical UV anchor editor remains owned by pipeline-v3.  This root command
is the operator-requested read-only discovery entry point and process-replaces
itself with the sole Source Layer Contract Viewer owner.  No rendering, contract
joining, label inference, and write path lives here.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def canonical_viewer_path() -> Path:
    """Resolve the in-repo authoritative pipeline-v3 viewer fail-closed."""
    return REPO_ROOT / "pipeline-v3" / "scripts" / "source_layer_contract_viewer.py"


def main(argv: list[str]) -> int:
    viewer = canonical_viewer_path()
    if not viewer.is_file():
        print(
            f"FAIL: canonical FL-4162 source contract viewer missing: {viewer}\n"
            "Run: git submodule update --init pipeline-v3",
            file=sys.stderr,
        )
        return 2
    os.execv(sys.executable, [sys.executable, str(viewer), *argv])
    return 2  # pragma: no cover - a successful exec never returns


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
