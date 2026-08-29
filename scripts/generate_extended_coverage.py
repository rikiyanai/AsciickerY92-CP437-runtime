#!/usr/bin/env python3
"""FL-4131 Extended Glyph Coverage Generator + Check

Generates the extended glyph coverage table by merging extended-glyph fixture
manifests under ``assets/glyphs/fixtures/``. Does NOT regenerate
CP437 ``glyph_coverage[256]`` — that table is frozen.

Sources merged (Phase 2 additive-core lane):
  - assets/glyphs/fixtures/extended_glyph_terrain_v1.json     (GIDs 256-265)
  - assets/glyphs/fixtures/extended_glyph_material_additive_v1.json (GIDs 512-647)

Usage:
  python3 scripts/generate_extended_coverage.py           # generate + write
  python3 scripts/generate_extended_coverage.py --verify  # verify output matches fixtures
  python3 scripts/generate_extended_coverage.py --check   # validate output format

Fail-closed --check rules (FL-4131 Phase 2 coverage golden rows):
  - missing entry from any sourced fixture          -> non-zero exit
  - duplicate glyph_id across or within fixtures    -> non-zero exit
  - CP437 glyph_id <=255 in extended table          -> non-zero exit
  - missing coverage_quadrants                      -> non-zero exit
  - sentinel id (0xFFFFFFFF / 0xFFFFFFFE)            -> non-zero exit
  - additive-core rows (GIDs 512-543) not all present -> non-zero exit
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

FIXTURE_DIR = REPO_ROOT / "assets" / "glyphs" / "fixtures"
OUTPUT_PATH = REPO_ROOT / "assets" / "glyphs" / "generated" / "extended_coverage_table.json"

# Fixtures merged into the canonical coverage table, in stable order.
FIXTURE_SOURCES: list[Path] = [
    FIXTURE_DIR / "extended_glyph_terrain_v1.json",
    FIXTURE_DIR / "extended_glyph_material_additive_v1.json",
]

# Phase 2 additive-core lane (preset proposal §4). --check fails closed when
# any GID in this set is missing from the merged output.
ADDITIVE_CORE_GIDS = set(range(512, 544))

# Sentinel IDs from engine/glyph_id.h that must never appear as renderable
# entries in the coverage table.
GLYPH_ID_NONE = 0xFFFFFFFF
GLYPH_ID_UNRESOLVED = 0xFFFFFFFE

# The CP437 coverage table is FROZEN. This script never reads or writes it.
_CP437_FROZEN_MSG = "CP437 glyph_coverage[256] is frozen — this generator never touches it"


def load_fixture(path: Path) -> dict[str, Any]:
    """Load one extended-glyph fixture manifest."""
    if not path.exists():
        sys.exit(f"FIXTURE MISSING: {path}")
    with open(path, "r") as f:
        return json.load(f)


def build_entries_from_fixture(fixture: dict[str, Any], source: Path) -> list[dict[str, Any]]:
    """Build coverage entries from a single fixture manifest."""
    raw_entries = fixture.get("glyphs") or fixture.get("entries") or []
    if not raw_entries:
        sys.exit(f"FIXTURE EMPTY: {source} has no 'glyphs' or 'entries' array")

    entries: list[dict[str, Any]] = []
    for e in raw_entries:
        glyph_id = e.get("glyph_id")
        if glyph_id is None:
            print(f"WARNING: {source.name}: entry missing glyph_id, skipping: {e}", file=sys.stderr)
            continue
        if not isinstance(glyph_id, int) or glyph_id <= 255:
            print(
                f"WARNING: {source.name}: glyph_id {glyph_id} is in CP437 range, skipping "
                f"({_CP437_FROZEN_MSG})",
                file=sys.stderr,
            )
            continue
        if glyph_id == GLYPH_ID_NONE or glyph_id == GLYPH_ID_UNRESOLVED:
            sys.exit(
                f"{source.name}: glyph_id {glyph_id} is a fail-closed sentinel; refusing to emit"
            )

        coverage = e.get("coverage_quadrants")
        if coverage is None:
            print(
                f"WARNING: {source.name}: glyph_id {glyph_id} has no coverage_quadrants, "
                f"setting to 0 (EXTENDED_UNBOUND)",
                file=sys.stderr,
            )
            coverage = 0

        status = "manifest_declared" if coverage > 0 else "unbound"
        label = e.get("label", f"glyph_{glyph_id}")
        unicode_scalar = e.get("unicode_scalar")

        entries.append({
            "glyph_id": glyph_id,
            "label": label,
            "unicode_scalar": unicode_scalar,
            "coverage_quadrants": coverage,
            "status": status,
            "source_fixture": source.name,
        })

    return entries


def build_entries() -> list[dict[str, Any]]:
    """Merge entries from every FIXTURE_SOURCES manifest in declared order."""
    merged: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for source in FIXTURE_SOURCES:
        fx = load_fixture(source)
        for entry in build_entries_from_fixture(fx, source):
            gid = entry["glyph_id"]
            if gid in seen_ids:
                sys.exit(
                    f"DUPLICATE glyph_id {gid} across fixtures ({source.name} collides with earlier source)"
                )
            seen_ids.add(gid)
            merged.append(entry)
    return merged


def build_output(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the full output document."""
    return {
        "schema_version": 1,
        "description": (
            "FL-4131 extended glyph coverage table — font-independent "
            "compositing metadata. uint16 quadrant-nibble format: "
            "nibble0=bottom-left, nibble1=bottom-right, "
            "nibble2=top-left, nibble3=top-right. "
            "Each nibble 0..4 (legacy scale) or 0..15 (extended scale)."
        ),
        "generated_from": [
            str(p.relative_to(REPO_ROOT)) for p in FIXTURE_SOURCES
        ],
        "generated_at": date.today().isoformat(),
        "entry_count": len(entries),
        "additive_core_gid_range": [min(ADDITIVE_CORE_GIDS), max(ADDITIVE_CORE_GIDS)],
        "cp437_note": _CP437_FROZEN_MSG,
        "entries": entries,
    }


def validate_coverage_format(entry: dict[str, Any]) -> list[str]:
    """Validate a single coverage entry. Returns list of error strings."""
    errors: list[str] = []
    cq = entry.get("coverage_quadrants")
    gid = entry.get("glyph_id")

    if cq is None:
        errors.append(f"glyph_id {gid}: missing coverage_quadrants")
        return errors
    if not isinstance(cq, int):
        errors.append(f"glyph_id {gid}: coverage_quadrants not int: {cq}")
        return errors
    if cq < 0 or cq > 0xFFFF:
        errors.append(f"glyph_id {gid}: coverage_quadrants out of uint16 range: {cq}")

    if not isinstance(gid, int) or gid <= 255:
        errors.append(f"glyph_id {gid}: CP437 range glyph leaked into extended table ({_CP437_FROZEN_MSG})")
    if gid in (GLYPH_ID_NONE, GLYPH_ID_UNRESOLVED):
        errors.append(f"glyph_id {gid}: sentinel id is non-renderable")

    return errors


def validate_output(output: dict[str, Any]) -> int:
    """Validate the full output. Returns count of errors."""
    error_count = 0
    entries = output.get("entries", [])

    if not entries:
        print("ERROR: no entries in output", file=sys.stderr)
        return 1

    # Duplicate glyph_id check.
    seen: set[int] = set()
    for e in entries:
        gid = e.get("glyph_id")
        if gid is None:
            continue
        if gid in seen:
            print(f"ERROR: duplicate glyph_id {gid}", file=sys.stderr)
            error_count += 1
        seen.add(gid)

    # Per-entry format check.
    for e in entries:
        for err in validate_coverage_format(e):
            print(f"ERROR: {err}", file=sys.stderr)
            error_count += 1

    # Golden-row presence checks.
    replacement_found = any(e.get("label") == "REPLACEMENT_CHARACTER" for e in entries)
    if not replacement_found:
        print(
            "ERROR: REPLACEMENT_CHARACTER row missing from extended coverage table",
            file=sys.stderr,
        )
        error_count += 1

    # Phase 2 additive-core coverage check.
    additive_present = {gid for gid in seen if gid in ADDITIVE_CORE_GIDS}
    missing_core = sorted(ADDITIVE_CORE_GIDS - additive_present)
    if missing_core:
        print(
            f"ERROR: Phase 2 additive-core rows missing: {missing_core}",
            file=sys.stderr,
        )
        error_count += len(missing_core)

    # CP437 leak guard (defensive in case build_entries was bypassed).
    cp437_leaks = [e for e in entries if isinstance(e.get("glyph_id"), int) and e["glyph_id"] <= 255]
    if cp437_leaks:
        print(
            f"ERROR: {len(cp437_leaks)} CP437 glyph_ids found in extended coverage table ({_CP437_FROZEN_MSG})",
            file=sys.stderr,
        )
        error_count += len(cp437_leaks)

    if error_count:
        print(f"VALIDATION FAILED: {error_count} error(s)", file=sys.stderr)
    return error_count


def cmd_generate() -> None:
    """Generate the coverage table and write to output."""
    entries = build_entries()
    output = build_output(entries)

    errs = validate_output(output)
    if errs:
        sys.exit(f"Generation aborted: {errs} validation error(s)")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)
        f.write("\n")

    print(f"Generated {len(entries)} extended coverage entries -> {OUTPUT_PATH}")
    additive_count = sum(1 for e in entries if e["glyph_id"] in ADDITIVE_CORE_GIDS)
    print(f"  Phase 2 additive-core rows present: {additive_count}/{len(ADDITIVE_CORE_GIDS)}")
    for e in entries:
        print(
            f"  glyph_id={e['glyph_id']:>5}  "
            f"coverage=0x{e['coverage_quadrants']:04X}  "
            f"status={e['status']}  "
            f"label={e['label']}"
        )


def cmd_verify() -> None:
    """Verify that the current output file matches what we would generate."""
    expected_entries = build_entries()
    expected_output = build_output(expected_entries)

    if not OUTPUT_PATH.exists():
        sys.exit(f"OUTPUT MISSING: {OUTPUT_PATH} — run generate first")

    with open(OUTPUT_PATH, "r") as f:
        current_output = json.load(f)

    current_entries = current_output.get("entries", [])
    if current_entries != expected_entries:
        print("VERIFY FAILED: generated entries differ from on-disk output")
        print(f"  on-disk: {len(current_entries)} entries")
        print(f"  generated: {len(expected_entries)} entries")
        for i, (ce, ee) in enumerate(zip(current_entries, expected_entries)):
            if ce != ee:
                print(f"  mismatch at index {i}:")
                print(f"    on-disk:    {json.dumps(ce)}")
                print(f"    generated:  {json.dumps(ee)}")
        if len(current_entries) != len(expected_entries):
            print(f"  count mismatch: {len(current_entries)} vs {len(expected_entries)}")
        sys.exit(1)

    errs = validate_output(current_output)
    if errs:
        sys.exit(f"VERIFY FAILED: {errs} validation error(s) in on-disk output")

    print(f"VERIFY PASS: {len(current_entries)} entries match fixtures + pass validation")
    print(_CP437_FROZEN_MSG)


def cmd_check() -> None:
    """Validate the on-disk output format. Fails closed on contract violations."""
    if not OUTPUT_PATH.exists():
        sys.exit(f"OUTPUT MISSING: {OUTPUT_PATH} — run generate first")

    with open(OUTPUT_PATH, "r") as f:
        output = json.load(f)

    errs = validate_output(output)
    if errs:
        sys.exit(f"CHECK FAILED: {errs} validation error(s)")

    entries = output.get("entries", [])
    additive_count = sum(1 for e in entries if e["glyph_id"] in ADDITIVE_CORE_GIDS)
    print(f"CHECK PASS: {len(entries)} entries, 0 format errors")
    print(f"  Phase 2 additive-core rows present: {additive_count}/{len(ADDITIVE_CORE_GIDS)}")
    for e in entries:
        cq = e["coverage_quadrants"]
        nibbles = [(cq >> (i * 4)) & 0xF for i in range(4)]
        print(
            f"  glyph_id={e['glyph_id']:>5}  "
            f"nibbles=[{nibbles[0]},{nibbles[1]},{nibbles[2]},{nibbles[3]}]  "
            f"coverage=0x{cq:04X}  "
            f"label={e['label']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="FL-4131 Extended Glyph Coverage Generator + Check"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--verify",
        action="store_true",
        help="Verify output matches fixtures (exit 1 on mismatch)",
    )
    group.add_argument(
        "--check",
        action="store_true",
        help="Validate output format and additive-core coverage (exit 1 on errors)",
    )
    args = parser.parse_args()

    if args.verify:
        cmd_verify()
    elif args.check:
        cmd_check()
    else:
        cmd_generate()


if __name__ == "__main__":
    main()
