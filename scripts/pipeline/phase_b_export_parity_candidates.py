#!/usr/bin/env python3
"""Export Step B parity candidates from the FL-4088 evidence ledger.

This is a read-only candidate packet generator. It does not mutate ledger rows,
does not attach parity receipts, and does not emit upstream_parity_verified.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LEDGER = REPO_ROOT / "docs" / "research" / "source_layer_evidence" / "ledger.json"
DEFAULT_OUT_DIR = REPO_ROOT / "docs" / "research" / "source_layer_evidence" / "parity_candidates"

SCHEMA_VERSION = "source_layer_parity_candidates.v1"
FL_REF = "FL-4088"
TARGET_STATE = "candidate_pending_parity"
FORBIDDEN_OUTPUT_STATE = "upstream_parity_verified"

ROW_ID_RE = re.compile(r"^(?P<family>[a-z0-9_-]+)-(?P<ahsw>[0-9a-fA-F]{4})-L(?P<layer>[0-9]+)$")


def fail(message: str, code: int = 2) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(code)


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        fail(f"missing input: {path}")
    except json.JSONDecodeError as exc:
        fail(f"malformed JSON in {path}: {exc}")


def parse_row_id(row_id: str) -> dict[str, Any]:
    match = ROW_ID_RE.match(row_id)
    if not match:
        return {
            "family": None,
            "ahsw": None,
            "armor": None,
            "helmet": None,
            "shield": None,
            "weapon": None,
        }
    ahsw = match.group("ahsw").lower()
    return {
        "family": match.group("family"),
        "ahsw": ahsw,
        "armor": int(ahsw[0], 16),
        "helmet": int(ahsw[1], 16),
        "shield": int(ahsw[2], 16),
        "weapon": int(ahsw[3], 16),
    }


def in_scope(row: dict[str, Any], scope: str) -> bool:
    row_id = str(row.get("row_id", ""))
    if scope == "all":
        return True
    if scope == "wolfie":
        return row_id.startswith("wolfie-")
    fail(f"unknown scope: {scope}")


def layer_merge_check(raw_layer_index: Any, source_kind: str) -> str:
    if source_kind == "composite_source":
        return (
            "Confirm LoadSprite() final rendered sprite uses the declared composite "
            "source row as loaded final output."
        )
    if isinstance(raw_layer_index, int) and raw_layer_index >= 3:
        return (
            "Confirm LoadSprite() merges this L3+ source layer into the final L2 "
            "rendered sprite; the raw layer is participation evidence, not an "
            "independently rendered sprite."
        )
    return "Confirm LoadSprite() consumes this layer in the rendered source path."


def required_checks(row: dict[str, Any], parsed: dict[str, Any]) -> list[dict[str, str]]:
    upstream_xp_path = row.get("upstream_xp_path")
    source_kind = str(row.get("source_kind", ""))
    raw_layer_index = row.get("raw_layer_index")
    family = parsed.get("family") or "unknown"
    ahsw = parsed.get("ahsw") or "unknown"
    tuple_text = (
        f"A={parsed.get('armor')} H={parsed.get('helmet')} "
        f"S={parsed.get('shield')} W={parsed.get('weapon')}"
        if parsed.get("ahsw") is not None
        else "unparsed A/H/S/W tuple"
    )
    checks = [
        {
            "kind": "loadsprites_filename_tuple",
            "question": (
                f"Does upstream LoadSprites() load {upstream_xp_path} for "
                f"family={family}, ahsw={ahsw} ({tuple_text})?"
            ),
            "required_citation": "upstream game.cpp LoadSprites() AHSW loop",
        },
        {
            "kind": "getsprite_runtime_selector",
            "question": (
                "Can upstream GetSprite() select this family/tuple for a rendered "
                "state? For mounted wolf crossbow, confirm the crossbow attack "
                "selector maps back to the wolfie idle/walk family and does not "
                "claim a wolack crossbow source."
            ),
            "required_citation": "upstream game.cpp GetSprite() mounted selector",
        },
        {
            "kind": "loadsprite_layer_merge",
            "question": layer_merge_check(raw_layer_index, source_kind),
            "required_citation": "upstream sprite.cpp LoadSprite() L3+ merge path",
        },
        {
            "kind": "semantic_mask_join",
            "question": (
                "Does an approved Semantic Mask cover the visible cells claimed by "
                "this source row?"
            ),
            "required_citation": "Semantic Mask manifest row and hash",
        },
    ]
    return checks


def build_candidate(row: dict[str, Any]) -> dict[str, Any]:
    row_id = str(row.get("row_id"))
    parsed = parse_row_id(row_id)
    if row.get("evidence_state") == FORBIDDEN_OUTPUT_STATE:
        fail(f"refusing forbidden state in input row {row_id}: {FORBIDDEN_OUTPUT_STATE}")
    evidence = [item for item in row.get("evidence", []) if isinstance(item, dict)]
    evidence_kinds = sorted({str(item.get("kind")) for item in evidence if item.get("kind")})
    return {
        "row_id": row_id,
        "current_evidence_state": row.get("evidence_state"),
        "upstream_xp_path": row.get("upstream_xp_path"),
        "raw_layer_index": row.get("raw_layer_index"),
        "source_kind": row.get("source_kind"),
        "ahsw": parsed,
        "decoded_l0": row.get("decoded_l0"),
        "evidence_summary": {
            "item_count": len(evidence),
            "kinds": evidence_kinds,
            "has_direct_user_visual_update": "direct_user_visual_update" in evidence_kinds,
            "has_upstream_sha_check": "upstream_sha_check" in evidence_kinds,
        },
        "byte_shape_identity_peers": row.get("byte_shape_identity_peers", []),
        "semantic_mask_refs": row.get("semantic_mask_refs", []),
        "existing_parity_receipt": row.get("parity_receipt"),
        "required_upstream_checks": required_checks(row, parsed),
        "parity_recommendation": None,
    }


def build_packet(ledger: dict[str, Any], scope: str) -> dict[str, Any]:
    rows = ledger.get("rows")
    if not isinstance(rows, list):
        fail("ledger.rows must be a list")
    selected = [
        row for row in rows
        if isinstance(row, dict)
        and row.get("evidence_state") == TARGET_STATE
        and in_scope(row, scope)
    ]
    candidates = [build_candidate(row) for row in sorted(selected, key=lambda r: str(r.get("row_id", "")))]
    return {
        "schema_version": SCHEMA_VERSION,
        "fl_ref": FL_REF,
        "surface_kind": "source_layer_parity_candidate_packet",
        "scope": scope,
        "source_ledger": "docs/research/source_layer_evidence/ledger.json",
        "target_evidence_state": TARGET_STATE,
        "candidate_count": len(candidates),
        "compiler_use_boundary": (
            "Candidate export is not compiler usable state. Compiler use requires "
            "upstream rendered-state parity, row-specific human visual verification, "
            "and Semantic Mask coverage."
        ),
        "candidates": candidates,
    }


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=False) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--scope", choices=("wolfie", "all"), required=True)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--write", action="store_true", help="write the packet; default is dry-run")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ledger = load_json(args.ledger)
    packet = build_packet(ledger, args.scope)
    out_path = args.out or (DEFAULT_OUT_DIR / f"{args.scope}.json")
    if args.write:
        write_json(out_path, packet)
        action = f"wrote {out_path}"
    else:
        action = f"dry-run output={out_path}"
    print(
        "OK Step B parity candidate export "
        f"scope={args.scope} candidates={packet['candidate_count']}; {action}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
