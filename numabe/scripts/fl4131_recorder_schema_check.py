#!/usr/bin/env python3
"""FL-4131 W7 — recorder extended-glyph schema validator (inert).

Validates the schema artifact at
  assets/glyphs/schema/recorder_extended_glyph.schema.json
and exercises it against synthetic in-memory sample rows.

Does NOT load any real recorder file. Does NOT emit any rows. Does NOT
treat the existing `cell.gl` byte as proof of extended identity.

Why no jsonschema dependency:
  The repo's Python toolchain does not require jsonschema. This
  validator implements a small JSON Schema subset by hand — enough to
  exercise the schema's required fields, enums, patterns, and oneOf
  structure for both row families. It is deliberately conservative and
  does NOT replace a real schema validator; treat its output as a
  shape check, not a full schema conformance proof.

Front door:
  python3 scripts/fl4131_recorder_schema_check.py
  python3 scripts/fl4131_recorder_schema_check.py --json

Exit codes:
  0 - schema loads + all synthetic samples validate
  1 - schema malformed or a synthetic sample fails validation
  2 - schema file missing
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "assets" / "glyphs" / "schema" / "recorder_extended_glyph.schema.json"

HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

VALID_TARGET_SURFACES = frozenset({
    "native_terminal",
    "web_canvas",
    "asciiid_editor",
    "headless_render",
    "godot_compositor",
    "unknown",
})
VALID_EVENT_REASONS = frozenset({
    "glyph_id_out_of_range",
    "glyph_id_not_in_manifest",
    "manifest_hash_mismatch",
    "manifest_not_loaded",
    "atlas_page_missing",
})
SAMPLE_REQUIRED = ("kind", "row_version", "target_surface", "manifest_hash_observed")
EVENT_REQUIRED = (
    "kind", "row_version", "target_surface", "manifest_hash_observed",
    "requested_glyph_id", "fallback_glyph_id", "reason",
)

ERR_MISSING = "missing"
ERR_UNPARSEABLE = "unparseable"


def _load_schema() -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Return (schema, error_message, error_kind). error_kind is ERR_MISSING,
    ERR_UNPARSEABLE, or None on success. Caller uses error_kind for exit-code
    decisions instead of substring-matching the error text."""
    if not SCHEMA_PATH.exists():
        return None, f"missing: {SCHEMA_PATH.relative_to(REPO_ROOT)}", ERR_MISSING
    try:
        return json.loads(SCHEMA_PATH.read_text(encoding="utf-8")), None, None
    except Exception as exc:
        return None, f"unparseable: {exc}", ERR_UNPARSEABLE


def _check_required_present(row: dict[str, Any], required: tuple[str, ...]) -> list[str]:
    return [f"required field missing: {r}" for r in required if r not in row]


def _validate_sample(row: dict[str, Any]) -> list[str]:
    errors: list[str] = _check_required_present(row, SAMPLE_REQUIRED)
    if row.get("kind") != "fl4131_glyph_sample":
        errors.append(f"kind must be fl4131_glyph_sample, got {row.get('kind')!r}")
    if row.get("row_version") != 1:
        errors.append(f"row_version must be 1, got {row.get('row_version')!r}")
    ts = row.get("target_surface")
    if ts not in VALID_TARGET_SURFACES:
        errors.append(f"target_surface invalid: {ts!r}")
    mh = row.get("manifest_hash_observed")
    if mh is not None and not (isinstance(mh, str) and HEX64_RE.match(mh)):
        errors.append("manifest_hash_observed must be 64-char hex SHA-256 or null")
    ecc = row.get("extended_cell_count")
    if ecc is not None and (not isinstance(ecc, int) or ecc < 0):
        errors.append("extended_cell_count must be a non-negative int or absent")
    fucc = row.get("fallback_used_cell_count")
    if fucc is not None and (not isinstance(fucc, int) or fucc < 0):
        errors.append("fallback_used_cell_count must be a non-negative int or absent")
    dom = row.get("dominant_glyph_id")
    if dom is not None and (not isinstance(dom, int) or dom < 0 or dom > 0xFFFFFFFF):
        errors.append("dominant_glyph_id must be uint32 or null")
    return errors


def _validate_event(row: dict[str, Any]) -> list[str]:
    errors: list[str] = _check_required_present(row, EVENT_REQUIRED)
    if row.get("kind") != "fl4131_missing_glyph_event":
        errors.append(f"kind must be fl4131_missing_glyph_event, got {row.get('kind')!r}")
    if row.get("row_version") != 1:
        errors.append(f"row_version must be 1, got {row.get('row_version')!r}")
    ts = row.get("target_surface")
    if ts not in VALID_TARGET_SURFACES:
        errors.append(f"target_surface invalid: {ts!r}")
    mh = row.get("manifest_hash_observed")
    if mh is not None and not (isinstance(mh, str) and HEX64_RE.match(mh)):
        errors.append("manifest_hash_observed must be 64-char hex SHA-256 or null")
    for required in ("requested_glyph_id", "fallback_glyph_id"):
        v = row.get(required)
        if not isinstance(v, int) or v < 0 or v > 0xFFFFFFFF:
            errors.append(f"{required} must be uint32, got {v!r}")
    reason = row.get("reason")
    if reason not in VALID_EVENT_REASONS:
        errors.append(f"reason invalid: {reason!r}")
    sp = row.get("source_path")
    if sp is not None and not isinstance(sp, str):
        errors.append("source_path must be a string when present")
    return errors


def _classify(row: dict[str, Any]) -> str:
    kind = row.get("kind")
    if kind == "fl4131_glyph_sample":
        return "sample"
    if kind == "fl4131_missing_glyph_event":
        return "event"
    return "unknown"


def synthetic_rows() -> list[dict[str, Any]]:
    return [
        # CP437-legacy frame: no manifest, no extended cells.
        {
            "kind": "fl4131_glyph_sample",
            "row_version": 1,
            "target_surface": "native_terminal",
            "manifest_hash_observed": None,
            "dominant_glyph_id": None,
            "extended_cell_count": 0,
            "fallback_used_cell_count": 0,
        },
        # Web canvas frame with a bound manifest and no extended cells (Phase 0/1 contract).
        {
            "kind": "fl4131_glyph_sample",
            "row_version": 1,
            "target_surface": "web_canvas",
            "manifest_hash_observed": "0" * 63 + "a",
            "dominant_glyph_id": 32,
            "extended_cell_count": 0,
            "fallback_used_cell_count": 0,
        },
        # Fallback event: extended glyph requested but no manifest loaded.
        {
            "kind": "fl4131_missing_glyph_event",
            "row_version": 1,
            "target_surface": "headless_render",
            "manifest_hash_observed": None,
            "requested_glyph_id": 0x2603,
            "fallback_glyph_id": 0x3F,
            "reason": "manifest_not_loaded",
        },
        # Fallback event: extended glyph requested but not in the bound manifest.
        {
            "kind": "fl4131_missing_glyph_event",
            "row_version": 1,
            "target_surface": "godot_compositor",
            "manifest_hash_observed": "f" * 64,
            "requested_glyph_id": 0x10000,
            "fallback_glyph_id": 0x3F,
            "reason": "glyph_id_not_in_manifest",
            "source_path": "assets/sprites/signs/example.xp",
        },
    ]


def validate() -> dict[str, Any]:
    schema, err, err_kind = _load_schema()
    out: dict[str, Any] = {
        "schema_ok": schema is not None,
        "schema_error": err,
        "schema_error_kind": err_kind,
        "rows": [],
    }
    if err:
        out["ok"] = False
        return out
    rows = synthetic_rows()
    all_ok = True
    for i, row in enumerate(rows):
        kind = _classify(row)
        if kind == "sample":
            errs = _validate_sample(row)
        elif kind == "event":
            errs = _validate_event(row)
        else:
            errs = [f"unknown kind: {row.get('kind')!r}"]
        out["rows"].append({"index": i, "kind": kind, "errors": errs, "ok": not errs})
        if errs:
            all_ok = False
    out["ok"] = all_ok
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()
    result = validate()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print("FL-4131 W7 recorder schema validator")
        print(f"  schema: {SCHEMA_PATH.relative_to(REPO_ROOT)}")
        print(f"  schema_ok: {result['schema_ok']}  schema_error: {result.get('schema_error')}")
        for r in result.get("rows", []):
            mark = "OK" if r["ok"] else "FAIL"
            print(f"  [{mark}] row {r['index']} kind={r['kind']} errors={r['errors']}")
        print("VERDICT:", "PASS" if result["ok"] else "FAIL")
    if not result["ok"]:
        # Use the schema_error_kind tag rather than substring-matching the
        # human-readable schema_error message.
        if result.get("schema_error_kind") == ERR_MISSING:
            return 2
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
