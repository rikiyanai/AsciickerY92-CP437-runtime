#!/usr/bin/env python3
# =============================================================================
# fl4512_verify_spacetime_track_owner.py   (FL-4652 / FL-4512 U6 v2)
# =============================================================================
# Compile and execute the isolated U6 v2 structural prototype fixture
# (tests/engine_tests/fl4512_spacetime_track_owner_test.cpp).
#
# It builds the standalone C++ unit test with a plain host compiler
# (no GL/SDL/presenter) and runs it. A non-zero exit from either step is a
# prototype diagnostic failure, never product proof.
#
# The fixture exercises the U6 v2 objective's core properties in isolation:
#   provenance fail-closed, artifact hash seal, deterministic lower-GlyphId
#   ties, the temporal identity term, transform-explained switches, topology
#   event forgiveness, the loop-closure term, and bounded track-store lifecycle.
# Evidence lane: FL-4652 diagnostic / FL-4512 structural prototype.
# =============================================================================

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


# Repository root is the parent of scripts/. The fixture includes project
# headers by both root-relative and engine-relative paths.
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests/engine_tests/fl4512_spacetime_track_owner_test.cpp"


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="fl4512-spacetime-track-owner-") as directory:
        binary = Path(directory) / "fixture"
        # -Wall -Wextra keeps the isolated owner honest about dead/again logic.
        subprocess.run(
            ["c++", "-std=c++17", "-Wall", "-Wextra",
             "-I", str(ROOT), "-I", str(ROOT / "engine"),
             str(SOURCE), "-o", str(binary)],
            check=True,
            cwd=ROOT,
        )
        subprocess.run([str(binary)], check=True, cwd=ROOT)
    # Neutral completion line (FL-4652 evidence lane); the fixture itself prints
    # per-property results.
    print("fl4512.spacetime_track_owner.v2 prototype fixture: OK")


if __name__ == "__main__":
    main()
