# =============================================================
# test_fl4131_glyph_manifest_base_path_resolve.py
# =============================================================
# Focused regression for FL-4131 packaged-app manifest path ownership.
#
# Defect: fl4131_phase2_valid.xp opened under
#   <app>/Contents/Resources/bin/assets/glyphs/fixtures/
# while its sidecar manifest string
#   assets/glyphs/fixtures/extended_glyph_terrain_v1.json
# was fopen'd against process cwd (<app>/Contents/Resources/), missing
# the file. Relative manifests must join native base_path only.
#
# This test builds the existing engine/glyph_manifest_test.c harness
# (now including T0 resolve checks) and runs it from the repo root.
# =============================================================

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PARITY = ROOT / "scripts" / "test_glyph_manifest_parity.py"


def test_glyph_manifest_base_path_resolve_harness():
    """
    Run the C glyph_manifest_test binary (includes T0 base_path join checks).

    Skips only when no C++ compiler is available on the machine.
    """
    if not PARITY.is_file():
        pytest.fail(f"missing parity front door: {PARITY}")

    completed = subprocess.run(
        [sys.executable, str(PARITY), "--with-c"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    out = (completed.stdout or "") + (completed.stderr or "")
    if "[SKIP] No C++ compiler found" in out:
        pytest.skip("no C++ compiler for glyph_manifest_test harness")
    assert completed.returncode == 0, out[-4000:]
    # Relative join + portable absolute passthrough + fail-closed cases.
    for case_id in (
        "T0a",
        "T0b",
        "T0c",
        "T0d",
        "T0e",
        "T0f",
        "T0g",
        "T0h",
        "T0i",
        "T0j",
        "T0k",
        "T0l",
        "T0m",
        "T0n",
        "T0o",
        "T0p",
        "T0q",
        "T0r",
    ):
        assert f"[PASS] {case_id}" in out, f"missing {case_id} in:\n{out[-4000:]}"
    assert "relative joins base_path" in out
    assert "Windows drive absolute passes through" in out
    assert "UNC absolute passes through" in out
    assert "empty path fails closed" in out
    assert "overflow join fails closed" in out
    assert "absolute overflow fails closed" in out
