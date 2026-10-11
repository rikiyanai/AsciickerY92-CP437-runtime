#!/usr/bin/env python3
"""test_glyph_sidecar_parity.py — FL-4131 Phase 0 parity test runner.

Gate: glyph_sidecar_parsers_contract_parity

Runs the sidecar parity corpus (assets/glyphs/fixtures/sidecar_parity_corpus.json)
against the Python parser (scripts/glyph_sidecar.py) and, when the C test binary
is built, against the engine parser (engine/glyph_sidecar.cpp via a test harness).

Usage:
  python3 scripts/test_glyph_sidecar_parity.py            # Python-only
  python3 scripts/test_glyph_sidecar_parity.py --with-c   # Python + C binary (if built)

The Python-only run closes the gate for Phase 0. The C parity test requires the
test binary to be built (see Makefile target glyph_sidecar_test or build manually
with scripts/test_glyph_sidecar_parity.py --build-c).
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

# ── Paths ─────────────────────────────────────────────────────────────────────

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CORPUS_PATH = os.path.join(_REPO_ROOT, "assets", "glyphs", "fixtures", "sidecar_parity_corpus.json")
_C_TEST_SRC = os.path.join(_REPO_ROOT, "engine", "glyph_sidecar_test.c")
_C_TEST_BIN = "/tmp/glyph_sidecar_test"

sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
from glyph_sidecar import parse_corpus, run_corpus_case  # noqa: E402


# ── Python runner ─────────────────────────────────────────────────────────────

def run_python(corpus: dict) -> tuple[int, int]:
    cases = corpus.get("cases", [])
    passed = failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for case in cases:
            ok, detail = run_corpus_case(case, tmp)
            status = "PASS" if ok else "FAIL"
            print(f"  [{status}] Python {detail}")
            if ok:
                passed += 1
            else:
                failed += 1
    return passed, failed


# ── C runner ─────────────────────────────────────────────────────────────────

def _build_c_test() -> bool:
    """Build the C test binary. Returns True on success."""
    if not os.path.isfile(_C_TEST_SRC):
        print(f"[SKIP] C test source not found: {_C_TEST_SRC}", file=sys.stderr)
        print("[SKIP] C parity test requires engine/glyph_sidecar_test.c (Phase 0B build step).", file=sys.stderr)
        return False
    cjson_src = os.path.join(_REPO_ROOT, "engine", "third_party", "cjson", "cJSON.c")
    sidecar_src = os.path.join(_REPO_ROOT, "engine", "glyph_sidecar.cpp")
    inc_engine = os.path.join(_REPO_ROOT, "engine")
    inc_cjson = os.path.join(_REPO_ROOT, "engine", "third_party", "cjson")
    cmd = [
        "clang++", "-std=c++11", "-Wall",
        "-I", inc_engine, "-I", inc_cjson,
        cjson_src, sidecar_src, _C_TEST_SRC,
        "-o", _C_TEST_BIN,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[FAIL] C test build failed:\n{result.stderr}", file=sys.stderr)
        return False
    return True


def run_c(corpus: dict) -> tuple[int, int, bool]:
    """Run corpus through C binary. Returns (ok_count, fail_count, skipped).

    skipped=True means the C binary was not available and no cases were run.
    The caller must not report GATE PASS for C when skipped=True.
    """
    if not os.path.isfile(_C_TEST_BIN):
        if not _build_c_test():
            print("[SKIP] C parity test skipped (no binary).")
            return 0, 0, True

    cases = corpus.get("cases", [])
    ok_count = fail_count = 0
    with tempfile.TemporaryDirectory() as tmp:
        for case in cases:
            case_json = json.dumps(case)
            result = subprocess.run(
                [_C_TEST_BIN, tmp, case_json],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                detail = result.stdout.strip()
                print(f"  [PASS] C     {detail}")
                ok_count += 1
            else:
                detail = (result.stdout + result.stderr).strip()
                print(f"  [FAIL] C     {detail}")
                fail_count += 1
    return ok_count, fail_count, False


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="FL-4131 sidecar parity test runner")
    parser.add_argument("--with-c", action="store_true", help="Also run C parity test")
    parser.add_argument("--build-c", action="store_true", help="Build C test binary and exit")
    parser.add_argument("--corpus", default=_CORPUS_PATH, help="Corpus path")
    args = parser.parse_args()

    if args.build_c:
        ok = _build_c_test()
        sys.exit(0 if ok else 1)

    corpus = parse_corpus(args.corpus)
    print(f"Corpus: {args.corpus} ({len(corpus.get('cases', []))} cases)\n")

    print("=== Python parser ===")
    py_ok, py_failed = run_python(corpus)

    c_ok = c_failed = 0
    c_skipped = False
    if args.with_c:
        print("\n=== C engine parser ===")
        c_ok, c_failed, c_skipped = run_c(corpus)

    total_failed = py_failed + c_failed
    total_ok = py_ok + c_ok
    print(f"\nResult: {total_ok} ok, {total_failed} failed")
    if total_failed:
        print("[GATE FAIL] glyph_sidecar_parsers_contract_parity: FAILED")
    else:
        print("[GATE PASS] glyph_sidecar_parsers_contract_parity: Python parser OK")
        if args.with_c:
            if c_skipped:
                print("[GATE SKIP] glyph_sidecar_parsers_contract_parity: C engine parser NOT tested (engine/glyph_sidecar_test.c not built)")
            else:
                print("[GATE PASS] glyph_sidecar_parsers_contract_parity: C engine parser OK")
        else:
            print("[NOTE] Run with --with-c to include C engine parity check")
    # Exit 1 if any test failed, OR if --with-c was requested but C was skipped
    # (skipped C parity is not a pass for the glyph_sidecar_parsers_contract_parity gate).
    should_pass = (total_failed == 0) and not (args.with_c and c_skipped)
    sys.exit(0 if should_pass else 1)


if __name__ == "__main__":
    main()
