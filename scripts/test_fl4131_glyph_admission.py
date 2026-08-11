#!/usr/bin/env python3
"""Check the FL-4131 fixture and native admission wiring contract.

This is a source-contract check. Runtime admission is verified separately by
starting a built client and observing the registry's admitted/fail-closed
diagnostics.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "assets/glyphs/fixtures"
REGISTRY = REPO_ROOT / "engine/sprite_registry.cpp"

EXPECTED_FIXTURES = (
    "fl4131_phase2_valid.xp",
    "fl4131_phase2_hash_mismatch.xp",
    "fl4131_phase2_unadmitted.xp",
    "fl4131_phase2_missing_manifest.xp",
    "fl4131_phase2_nosidecar.xp",
    "fl4131_extended_demo.xp",
)

REGISTRY_MARKERS = (
    "fl4131_phase2_valid.xp",
    "glyph_plane.cells[0] == 256 (EXTENDED_ADMITTED)",
    "fl4131_extended_demo.xp",
    "legacy demo fixture still fail-closed",
)


def main() -> int:
    errors: list[str] = []
    for name in EXPECTED_FIXTURES:
        if not (FIXTURES / name).is_file():
            errors.append(f"missing fixture: {name}")

    registry = REGISTRY.read_text(encoding="utf-8")
    for marker in REGISTRY_MARKERS:
        if marker not in registry:
            errors.append(f"native registry missing admission marker: {marker}")

    manifest_check = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/compile_glyph_manifest.py"),
            "--check",
            str(FIXTURES / "extended_glyph_material_additive_v1.json"),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    if manifest_check.returncode:
        errors.append("material glyph manifest failed validation")

    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1

    print("PASS: FL-4131 fixture and native admission wiring contract is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
