#!/usr/bin/env python3
"""Validate the FL-4088 source-layer evidence receipt surface.

This validates the new repo-owned surface:

  docs/research/source_layer_evidence/ledger.json
  docs/research/source_layer_evidence/schema.json

The ledger is a receipt and join surface. Passing validation means the JSON
shape is internally consistent; it is not a gameplay/render closure claim.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = REPO_ROOT / "docs" / "research" / "source_layer_evidence" / "ledger.json"
DEFAULT_SCHEMA = REPO_ROOT / "docs" / "research" / "source_layer_evidence" / "schema.json"

SCHEMA_VERSION = "source_layer_evidence_ledger.v1"
SURFACE_KIND = "source_layer_evidence_receipts"
FL_REF = "FL-4088"

ROW_STATES = {
    "evidence_only",
    "human_verified",
    "candidate_pending_parity",
    "candidate_pending_human_review",
    "upstream_parity_verified",
    "contradicted",
    "quarantined",
}
STEP_A_FORBIDDEN_STATES = {"upstream_parity_verified"}
SOURCE_KINDS = {
    "raw_layer",
    "composite_source",
    "non_upstream_derivation",
    "generated_reference",
    "legacy_reference",
    "metadata_layer",
}
BANNED_KEY_TOKEN = "authority"


def fail(code: int, message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(code)


def load_json_or_fail(path: Path, code: int, label: str) -> Any:
    if not path.is_file():
        fail(code, f"missing {label}: {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        fail(code, f"malformed {label} JSON ({path}): {exc}")
    except OSError as exc:
        fail(code, f"unreadable {label} ({path}): {exc}")


def find_banned_keys(value: Any, path: str = "<root>") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_path = f"{path}.{key}" if path != "<root>" else key
            if BANNED_KEY_TOKEN in str(key).lower():
                hits.append(key_path)
            hits.extend(find_banned_keys(child, key_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            hits.extend(find_banned_keys(child, f"{path}[{index}]"))
    return hits


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def require_type(errors: list[str], value: Any, expected: type | tuple[type, ...], path: str) -> bool:
    if not isinstance(value, expected):
        if isinstance(expected, tuple):
            names = " or ".join(t.__name__ for t in expected)
        else:
            names = expected.__name__
        errors.append(f"{path}: expected {names}, got {type(value).__name__}")
        return False
    return True


def validate_schema_file(schema: Any) -> list[str]:
    errors: list[str] = []
    if not require_type(errors, schema, dict, "schema"):
        return errors
    if schema.get("$id") != SCHEMA_VERSION:
        errors.append(f"schema.$id: expected {SCHEMA_VERSION!r}, got {schema.get('$id')!r}")
    props = schema.get("properties")
    if not isinstance(props, dict):
        errors.append("schema.properties: missing object")
    else:
        expected_props = {"schema_version", "surface_kind", "fl_ref", "rows"}
        actual_props = set(props.keys())
        if actual_props != expected_props:
            errors.append(
                f"schema.properties: expected {sorted(expected_props)}, got {sorted(actual_props)}"
            )
    state_enum = (
        schema.get("$defs", {})
        .get("evidence_state", {})
        .get("enum")
        if isinstance(schema.get("$defs"), dict)
        else None
    )
    if set(state_enum or []) != ROW_STATES:
        errors.append(f"schema.$defs.evidence_state.enum: expected {sorted(ROW_STATES)}")
    return errors


def validate_decoded_l0(row: dict[str, Any], row_path: str) -> list[str]:
    errors: list[str] = []
    decoded = row.get("decoded_l0")
    if not require_type(errors, decoded, dict, f"{row_path}.decoded_l0"):
        return errors
    for key in ("angles", "y_proj", "y_refl", "z_proj", "z_refl"):
        require_type(errors, decoded.get(key), int, f"{row_path}.decoded_l0.{key}")
    anim_len = decoded.get("anim_len")
    if require_type(errors, anim_len, list, f"{row_path}.decoded_l0.anim_len"):
        for index, item in enumerate(anim_len):
            require_type(errors, item, int, f"{row_path}.decoded_l0.anim_len[{index}]")
    return errors


def validate_row(row: Any, index: int, producer: str) -> list[str]:
    errors: list[str] = []
    row_path = f"rows[{index}]"
    if not require_type(errors, row, dict, row_path):
        return errors

    required = {
        "row_id",
        "upstream_xp_path",
        "raw_layer_index",
        "xp_sha256",
        "decoded_l0",
        "l1_provenance",
        "source_kind",
        "evidence",
        "evidence_state",
        "evidence_state_blockers",
    }
    missing = sorted(required - set(row.keys()))
    if missing:
        errors.append(f"{row_path}: missing required keys {missing}")

    extra = sorted(set(row.keys()) - {
        *required,
        "byte_shape_identity_peers",
        "parity_receipt",
        "semantic_mask_refs",
    })
    if extra:
        errors.append(f"{row_path}: unexpected keys {extra}")

    require_type(errors, row.get("row_id"), str, f"{row_path}.row_id")
    if row.get("upstream_xp_path") is not None:
        require_type(errors, row.get("upstream_xp_path"), str, f"{row_path}.upstream_xp_path")
    if row.get("raw_layer_index") is not None:
        require_type(errors, row.get("raw_layer_index"), int, f"{row_path}.raw_layer_index")
    xp_sha = row.get("xp_sha256")
    if xp_sha is not None:
        if require_type(errors, xp_sha, str, f"{row_path}.xp_sha256"):
            if len(xp_sha) != 64 or any(c not in "0123456789abcdef" for c in xp_sha):
                errors.append(f"{row_path}.xp_sha256: expected 64 lowercase hex characters")

    errors.extend(validate_decoded_l0(row, row_path))

    source_kind = row.get("source_kind")
    if source_kind not in SOURCE_KINDS:
        errors.append(f"{row_path}.source_kind: unknown value {source_kind!r}")

    evidence = row.get("evidence")
    if require_type(errors, evidence, list, f"{row_path}.evidence"):
        for e_index, item in enumerate(evidence):
            item_path = f"{row_path}.evidence[{e_index}]"
            if require_type(errors, item, dict, item_path):
                require_type(errors, item.get("kind"), str, f"{item_path}.kind")
                require_type(errors, item.get("detail"), str, f"{item_path}.detail")

    state = row.get("evidence_state")
    if state not in ROW_STATES:
        errors.append(f"{row_path}.evidence_state: unknown value {state!r}")
    if producer == "step-a" and state in STEP_A_FORBIDDEN_STATES:
        errors.append(f"{row_path}.evidence_state: Step A must not emit {state!r}")

    blockers = row.get("evidence_state_blockers")
    if require_type(errors, blockers, list, f"{row_path}.evidence_state_blockers"):
        for b_index, item in enumerate(blockers):
            require_type(errors, item, str, f"{row_path}.evidence_state_blockers[{b_index}]")

    for key in ("byte_shape_identity_peers", "semantic_mask_refs"):
        if key in row:
            value = row.get(key)
            if require_type(errors, value, list, f"{row_path}.{key}"):
                for v_index, item in enumerate(value):
                    require_type(errors, item, str, f"{row_path}.{key}[{v_index}]")

    if "parity_receipt" in row and row["parity_receipt"] is not None:
        require_type(errors, row["parity_receipt"], dict, f"{row_path}.parity_receipt")

    return errors


def validate_ledger(ledger: Any, producer: str) -> list[str]:
    errors: list[str] = []
    if not require_type(errors, ledger, dict, "ledger"):
        return errors
    expected_keys = {"schema_version", "surface_kind", "fl_ref", "rows"}
    actual_keys = set(ledger.keys())
    if actual_keys != expected_keys:
        errors.append(f"ledger keys: expected {sorted(expected_keys)}, got {sorted(actual_keys)}")
    if ledger.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"ledger.schema_version: expected {SCHEMA_VERSION!r}, got {ledger.get('schema_version')!r}"
        )
    if ledger.get("surface_kind") != SURFACE_KIND:
        errors.append(f"ledger.surface_kind: expected {SURFACE_KIND!r}, got {ledger.get('surface_kind')!r}")
    if ledger.get("fl_ref") != FL_REF:
        errors.append(f"ledger.fl_ref: expected {FL_REF!r}, got {ledger.get('fl_ref')!r}")
    rows = ledger.get("rows")
    if require_type(errors, rows, list, "ledger.rows"):
        seen: dict[str, int] = {}
        for index, row in enumerate(rows):
            errors.extend(validate_row(row, index, producer))
            if isinstance(row, dict) and isinstance(row.get("row_id"), str):
                row_id = row["row_id"]
                if row_id in seen:
                    errors.append(f"rows[{index}].row_id duplicates rows[{seen[row_id]}]: {row_id!r}")
                else:
                    seen[row_id] = index
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument(
        "--producer",
        choices=("ledger", "step-a"),
        default="ledger",
        help="use step-a to reject states Step A is forbidden to emit",
    )
    args = parser.parse_args()

    ledger = load_json_or_fail(args.ledger, 2, "ledger")
    schema = load_json_or_fail(args.schema, 2, "schema")

    banned = [(rel(args.ledger), find_banned_keys(ledger)), (rel(args.schema), find_banned_keys(schema))]
    banned_hits = [(path, hits) for path, hits in banned if hits]
    if banned_hits:
        for path, hits in banned_hits:
            print(f"Forbidden key token {BANNED_KEY_TOKEN!r} in {path}:", file=sys.stderr)
            for hit in hits:
                print(f"  {hit}", file=sys.stderr)
        fail(3, "forbidden key token found")

    schema_errors = validate_schema_file(schema)
    if schema_errors:
        print("Schema file errors:", file=sys.stderr)
        for error in schema_errors:
            print(f"  {error}", file=sys.stderr)
        fail(3, "schema validation failed")

    ledger_errors = validate_ledger(ledger, args.producer)
    if ledger_errors:
        print("Ledger errors:", file=sys.stderr)
        for error in ledger_errors:
            print(f"  {error}", file=sys.stderr)
        fail(4, "ledger validation failed")

    row_count = len(ledger.get("rows", [])) if isinstance(ledger, dict) else 0
    print(
        f"OK source_layer_evidence ledger={rel(args.ledger)} schema={rel(args.schema)} "
        f"rows={row_count} producer={args.producer}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
