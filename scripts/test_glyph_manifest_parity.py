#!/usr/bin/env python3
"""FL-4131 Phase 2: Glyph manifest parser parity test.

Compares Python compile_glyph_manifest.py --hash output with the C engine
manifest loader (engine/glyph_manifest.cpp). Ensures both sides agree on:
  - valid manifest hash
  - hash mismatch
  - missing file
  - malformed JSON
  - admission set semantics
  - coverage lookup
  - fallback_glyph_id

Usage:
  python3 scripts/test_glyph_manifest_parity.py [--with-c]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "assets" / "glyphs" / "fixtures" / "extended_glyph_terrain_v1.json"
EXPECTED_HASH = "1ff4e22faf91a79fde8ae38c59d0736982a53aafef100d708651dd3f95c9d9cd"


def _find_compiler() -> str | None:
    for cc in ("clang++", "g++"):
        if subprocess.run(["which", cc], capture_output=True).returncode == 0:
            return cc
    return None


def _build_c_test(compiler: str) -> Path:
    out = Path(tempfile.gettempdir()) / "glyph_manifest_test"
    # Compile C sources
    c_objs = []
    for src in (
        str(REPO_ROOT / "engine" / "third_party" / "cjson" / "cJSON.c"),
        str(REPO_ROOT / "engine" / "glyph_manifest_test.c"),
    ):
        obj = Path(tempfile.gettempdir()) / (Path(src).name + ".o")
        ccmd = ["clang", "-std=c11", "-c", "-I", str(REPO_ROOT / "engine"), "-I", str(REPO_ROOT / "engine" / "third_party" / "cjson"), src, "-o", str(obj)]
        r = subprocess.run(ccmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[FAIL] C build step failed for {src}:\n{r.stderr}")
            sys.exit(1)
        c_objs.append(str(obj))
    # Compile C++ source
    cpp_obj = Path(tempfile.gettempdir()) / "glyph_manifest.cpp.o"
    cppcmd = [compiler, "-std=c++11", "-c", "-I", str(REPO_ROOT / "engine"), "-I", str(REPO_ROOT / "engine" / "third_party" / "cjson"), str(REPO_ROOT / "engine" / "glyph_manifest.cpp"), "-o", str(cpp_obj)]
    r = subprocess.run(cppcmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[FAIL] C++ build step failed:\n{r.stderr}")
        sys.exit(1)
    # Link
    lcmd = [compiler, "-std=c++11"] + c_objs + [str(cpp_obj), "-o", str(out)]
    r = subprocess.run(lcmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[FAIL] Link step failed:\n{r.stderr}")
        sys.exit(1)
    return out


def _run_c_test() -> int:
    compiler = _find_compiler()
    if not compiler:
        print("[SKIP] No C++ compiler found (tried clang++, g++)")
        return 0
    bin_path = _build_c_test(compiler)
    proc = subprocess.run([str(bin_path)], capture_output=True, text=True, cwd=str(REPO_ROOT))
    print(proc.stdout)
    if proc.returncode != 0:
        print(f"[FAIL] C test binary exited {proc.returncode}")
        if proc.stderr:
            print(proc.stderr)
        return 1
    return 0


def _run_python_checks() -> int:
    failures = 0

    # P1: valid manifest hash matches compile_glyph_manifest.py
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "compile_glyph_manifest.py"), "--hash=" + str(MANIFEST_PATH)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        print("[FAIL] compile_glyph_manifest.py --hash failed")
        failures += 1
    else:
        got = proc.stdout.strip()
        if got == EXPECTED_HASH:
            print(f"[PASS] Python hash matches expected {EXPECTED_HASH[:16]}...")
        else:
            print(f"[FAIL] Python hash mismatch: expected {EXPECTED_HASH}, got {got}")
            failures += 1

    # P2: schema validation rejects duplicate glyph_id
    bad_manifest = {
        "manifest_version": 1,
        "profile_kind": "extended_glyph_v1",
        "content_pack_id": "bad.duplicate.v1",
        "fallback_glyph_id": 256,
        "entries": [
            {"glyph_id": 256, "label": "DUP", "coverage_quadrants": 0},
            {"glyph_id": 256, "label": "DUP2", "coverage_quadrants": 0},
        ],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(bad_manifest, f)
        bad_path = f.name
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "compile_glyph_manifest.py"), "--check", bad_path],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    os.unlink(bad_path)
    if proc.returncode != 0:
        print("[PASS] Python validator rejects duplicate glyph_id")
    else:
        print("[FAIL] Python validator did not reject duplicate glyph_id")
        failures += 1

    # P3: sentinel fallback rejected
    bad_manifest2 = {
        "manifest_version": 1,
        "profile_kind": "extended_glyph_v1",
        "content_pack_id": "bad.sentinel.v1",
        "fallback_glyph_id": 0xFFFFFFFF,
        "entries": [
            {"glyph_id": 256, "label": "S", "coverage_quadrants": 0},
        ],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(bad_manifest2, f)
        bad_path = f.name
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "compile_glyph_manifest.py"), "--check", bad_path],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    os.unlink(bad_path)
    if proc.returncode != 0:
        print("[PASS] Python validator rejects sentinel fallback_glyph_id")
    else:
        print("[FAIL] Python validator did not reject sentinel fallback_glyph_id")
        failures += 1

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="FL-4131 manifest parity test")
    parser.add_argument("--with-c", action="store_true", help="Build and run C test harness")
    args = parser.parse_args()

    py_fail = _run_python_checks()
    c_fail = _run_c_test() if args.with_c else 0

    total = py_fail + c_fail
    if total == 0:
        print("\n[GATE PASS] glyph_manifest_parity: all checks OK")
    else:
        print(f"\n[GATE FAIL] glyph_manifest_parity: {total} failure(s)")
    return total


if __name__ == "__main__":
    sys.exit(main())
