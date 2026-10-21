#!/usr/bin/env python3
"""Validate semantic map JSON files in docs/research/ascii/semantic_maps/.

Two schema kinds live in this directory:
  - PRIMARY (grid-layout maps): require schema_version, family, reference_xp,
    grid_layout, frame_w, frame_h, frames.  These are the maps consumed by
    xp_uv_body_viewer.py and pipeline-v3.
  - SECONDARY (-roles, -spatial): have family + frames only.  Validated for
    JSON parse health only.

Usage (standalone):
    python3 scripts/validate_semantic_maps.py
    python3 scripts/validate_semantic_maps.py --verbose

Exit codes: 0 = all PASS, 1 = one or more failures.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAPS_DIR = REPO_ROOT / "docs" / "research" / "ascii" / "semantic_maps"

# Required keys for primary (grid-layout) maps
PRIMARY_REQUIRED_KEYS = {
    "schema_version", "family", "reference_xp", "grid_layout",
    "frame_w", "frame_h", "frames",
}
SECONDARY_SUFFIX = ("-roles.json", "-spatial.json")


def is_secondary(path: Path) -> bool:
    return any(path.name.endswith(s) for s in SECONDARY_SUFFIX)


def validate_primary(path: Path, verbose: bool) -> list[str]:
    errors: list[str] = []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        return [f"JSON parse error: {exc}"]

    missing = PRIMARY_REQUIRED_KEYS - set(data.keys())
    if missing:
        errors.append(f"missing required keys: {sorted(missing)}")

    ref_xp = data.get("reference_xp", "")
    if isinstance(ref_xp, str) and ref_xp:
        ref_path = (path.parent / ref_xp).resolve()
        if not ref_path.is_file():
            errors.append(f"reference_xp not found: {ref_xp}")
    elif "reference_xp" in data:
        errors.append(f"reference_xp is not a non-empty string: {ref_xp!r}")

    family = data.get("family")
    if not isinstance(family, str) or not family:
        errors.append(f"family must be a non-empty string, got {family!r}")

    frames = data.get("frames")
    if not isinstance(frames, dict) or not frames:
        errors.append("frames must be a non-empty dict")

    if verbose and not errors:
        sv = data.get("schema_version", "?")
        nframes = len(frames) if isinstance(frames, dict) else "?"
        print(f"    schema_version={sv} family={family!r} angles={nframes}")

    return errors


def validate_secondary(path: Path) -> list[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        return [f"JSON parse error: {exc}"]
    if not isinstance(data.get("frames"), dict):
        return ["frames must be a dict"]
    return []


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--verbose", action="store_true", help="Print per-file detail on PASS")
    args = p.parse_args()

    if not MAPS_DIR.is_dir():
        print(f"FAIL: semantic_maps dir not found: {MAPS_DIR}")
        return 1

    all_files = sorted(MAPS_DIR.glob("*.json"))
    if not all_files:
        print(f"FAIL: no JSON files found in {MAPS_DIR}")
        return 1

    pass_primary = fail_primary = pass_secondary = fail_secondary = 0
    any_failure = False

    print(f"Validating {len(all_files)} files in {MAPS_DIR.relative_to(REPO_ROOT)}\n")

    for path in all_files:
        rel = path.relative_to(REPO_ROOT)
        if is_secondary(path):
            errors = validate_secondary(path)
            if errors:
                fail_secondary += 1
                any_failure = True
                print(f"  FAIL [secondary] {rel}")
                for e in errors:
                    print(f"    {e}")
            else:
                pass_secondary += 1
                if args.verbose:
                    print(f"  PASS [secondary] {rel}")
        else:
            errors = validate_primary(path, args.verbose)
            if errors:
                fail_primary += 1
                any_failure = True
                print(f"  FAIL [primary]   {rel}")
                for e in errors:
                    print(f"    {e}")
            else:
                pass_primary += 1
                if not args.verbose:
                    print(f"  PASS [primary]   {rel}")

    total_pass = pass_primary + pass_secondary
    total_fail = fail_primary + fail_secondary
    total = len(all_files)
    print(f"\n{'=' * 60}")
    print(f"  Primary:   {pass_primary} PASS / {fail_primary} FAIL")
    print(f"  Secondary: {pass_secondary} PASS / {fail_secondary} FAIL")
    print(f"  Total:     {total_pass}/{total} PASS")
    if any_failure:
        print(f"  RESULT: FAIL ({total_fail} failures)")
        return 1
    print(f"  RESULT: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
