#!/usr/bin/env python3
"""Build FL-4088 Step A source-layer evidence rows.

Default mode is a dry-run. Use --write to replace ledger.json. The writer is
deterministic and validates generated output with the Step A producer gate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = Path("/Users/r/Desktop/bundle_layer_audit_20260520")

DEFAULT_LEDGER = REPO_ROOT / "docs" / "research" / "source_layer_evidence" / "ledger.json"
DEFAULT_SCHEMA = REPO_ROOT / "docs" / "research" / "source_layer_evidence" / "schema.json"
DEFAULT_VERIFIER_STATE = AUDIT_DIR / "verifier_state_backups" / "latest.json"
DEFAULT_FULL_LEDGER = AUDIT_DIR / "full_source_layer_ledger_20260520.json"
DEFAULT_IDENTITY_HINTS = AUDIT_DIR / "identity_hints.json"
DEFAULT_USER_VISUAL_UPDATES = AUDIT_DIR / "user_visual_verification_updates_20260520.json"
DEFAULT_UPSTREAM_SPRITE_DIR = Path("/Users/r/Downloads/asciicker-pipeline-v3/sprites")
VALIDATOR = REPO_ROOT / "scripts" / "pipeline" / "validate_source_layer_evidence.py"

SCHEMA_VERSION = "source_layer_evidence_ledger.v1"
SURFACE_KIND = "source_layer_evidence_receipts"
FL_REF = "FL-4088"

SCOPES = ("wolfie", "all-verified", "all-343")
STEP_A_FORBIDDEN_STATE = "upstream_parity_verified"


def fail(message: str, code: int = 2) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(code)


def load_json(path: Path, label: str) -> Any:
    if not path.is_file():
        fail(f"missing {label}: {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        fail(f"malformed {label} JSON ({path}): {exc}")


def dump_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def parse_int(value: Any, field: str, row_id: str) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        fail(f"{row_id}: cannot parse {field} as int: {value!r}")


def parse_anim_len(value: Any, row_id: str) -> list[int]:
    if isinstance(value, list):
        return [parse_int(item, "l0_anim_len", row_id) for item in value]
    parts = [part.strip() for part in str(value or "").split(",") if part.strip()]
    if not parts:
        return []
    return [parse_int(part, "l0_anim_len", row_id) for part in parts]


def row_id_from_source_row(row: dict[str, Any]) -> str:
    stem = Path(str(row["source_xp_path"])).stem
    return f"{stem}-L{row['layer_index']}"


def verifier_alias_to_full_id(row_id: str) -> str:
    if row_id.startswith("wolfie-base-L"):
        return row_id.replace("wolfie-base-L", "wolfie-L", 1)
    if row_id.startswith("bigbee-base-L"):
        return row_id.replace("bigbee-base-L", "bigbee-L", 1)
    if row_id.startswith("player-nude-base-L"):
        return row_id.replace("player-nude-base-L", "player-nude-L", 1)
    return row_id


def family_for(row_id: str) -> str:
    return row_id.split("-", 1)[0]


def build_full_row_index(full_ledger: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = full_ledger.get("rows")
    if not isinstance(rows, list):
        fail("full source layer ledger missing rows list")
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if "source_xp_path" not in row or "layer_index" not in row:
            continue
        index[row_id_from_source_row(row)] = row
    return index


def build_direct_update_index(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = data.get("verified_rows")
    if not isinstance(rows, list):
        fail("user visual updates missing verified_rows list")
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if "source_xp_path" not in row or "raw_layer_index" not in row:
            continue
        row_id = f"{Path(str(row['source_xp_path'])).stem}-L{row['raw_layer_index']}"
        out[row_id] = row
    return out


def select_verifier_ids(verifier: dict[str, Any], scope: str) -> list[str]:
    if scope not in SCOPES:
        fail(f"unknown scope {scope!r}; expected one of {', '.join(SCOPES)}")
    selected: list[str] = []
    for row_id, state in verifier.items():
        if not isinstance(state, dict):
            continue
        status = str(state.get("status", "")).lower()
        if scope == "wolfie" and row_id.startswith("wolfie-"):
            selected.append(row_id)
        elif scope == "all-verified" and status == "accept":
            selected.append(row_id)
        elif scope == "all-343":
            selected.append(row_id)
    return sorted(selected)


def derive_source_kind(full_row: dict[str, Any], direct_update: dict[str, Any] | None) -> str:
    if direct_update and direct_update.get("source_kind"):
        return str(direct_update["source_kind"])
    layer_index = str(full_row.get("layer_index", ""))
    layer_kind = str(full_row.get("layer_kind", ""))
    semantic = str(full_row.get("full_ledger_semantic_label", ""))
    review = str(full_row.get("full_ledger_review_state", ""))
    if layer_index in {"0", "1"} or layer_kind.startswith("SYSTEM_L"):
        return "metadata_layer"
    if semantic.startswith("composite_source:") or "COMPOSITE_SOURCE" in str(full_row.get("ledger_statuses", "")):
        return "composite_source"
    if semantic.startswith("generator_reference:") or "generator_reference" in review:
        return "generated_reference"
    if semantic.startswith("legacy_manifest:"):
        return "legacy_reference"
    return "raw_layer"


def add_evidence(evidence: list[dict[str, str]], kind: str, detail: str | None) -> None:
    if detail:
        evidence.append({"kind": kind, "detail": " ".join(str(detail).split())})


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def upstream_sha_evidence(
    full_row: dict[str, Any],
    upstream_sprite_dir: Path | None,
    blockers: list[str],
) -> str | None:
    if upstream_sprite_dir is None:
        return None
    source_xp_path = str(full_row.get("source_xp_path", ""))
    if not source_xp_path.startswith("sprites/"):
        blockers.append("upstream_xp_path_not_under_sprites")
        return None
    xp_path = upstream_sprite_dir / Path(source_xp_path).name
    if not xp_path.is_file():
        blockers.append("upstream_xp_missing_for_sha_check")
        return f"upstream SHA check skipped: missing {xp_path}"
    actual = sha256_file(xp_path)
    expected = str(full_row.get("xp_sha256", ""))
    if actual != expected:
        blockers.append("upstream_xp_sha_mismatch")
        return f"upstream SHA mismatch for {source_xp_path}: expected {expected}, got {actual}"
    return f"upstream SHA verified for {source_xp_path}: {actual}"


def has_direct_row_evidence(verifier_state: dict[str, Any], direct_update: dict[str, Any] | None) -> bool:
    if direct_update:
        return True
    if verifier_state.get("auto_propagated_from"):
        return False
    pre_source = str(verifier_state.get("pre_source", "")).upper()
    if pre_source.startswith("HINT_") or pre_source.startswith("AUTO_"):
        return False
    return True


def status_state_and_blockers(
    verifier_state: dict[str, Any],
    full_row: dict[str, Any],
    direct_update: dict[str, Any] | None,
    source_kind: str,
) -> tuple[str, list[str]]:
    status = str(verifier_state.get("status", "")).lower()
    blockers: list[str] = []
    full_blockers = str(full_row.get("full_ledger_blockers", ""))
    review_state = str(full_row.get("full_ledger_review_state", ""))

    if source_kind == "generated_reference":
        blockers.append("generated_reference_not_direct_compiler_source")
    if source_kind == "legacy_reference" or "candidate_not_authority" in full_blockers:
        blockers.append("candidate_or_legacy_review_not_compiler_source")
    if "review_required" in review_state:
        blockers.append("audit_review_required")

    if status == "reject":
        return "quarantined", sorted(set([*blockers, "manual_status_reject"]))
    if status == "ambig":
        return "evidence_only", sorted(set([*blockers, "manual_status_ambig"]))
    if status == "partial":
        return "evidence_only", sorted(set([*blockers, "manual_status_partial"]))
    if status != "accept":
        return "evidence_only", sorted(set([*blockers, "manual_status_missing_or_unknown"]))

    if has_direct_row_evidence(verifier_state, direct_update) and not blockers:
        return "candidate_pending_parity", []
    if has_direct_row_evidence(verifier_state, direct_update):
        return "human_verified", sorted(set(blockers))
    return "evidence_only", sorted(set([*blockers, "needs_direct_row_visual_update"]))


def build_row(
    row_id: str,
    verifier_state: dict[str, Any],
    full_row: dict[str, Any],
    identity_hint: dict[str, Any] | None,
    direct_update: dict[str, Any] | None,
    upstream_sprite_dir: Path | None,
) -> dict[str, Any]:
    source_kind = derive_source_kind(full_row, direct_update)
    evidence: list[dict[str, str]] = []

    add_evidence(
        evidence,
        "verifier_state",
        (
            f"status={verifier_state.get('status', '')}; "
            f"corrected_label={verifier_state.get('corrected_label', '')}; "
            f"pre_source={verifier_state.get('pre_source', '')}; "
            f"pre_guess={verifier_state.get('pre_guess', '')}; "
            f"note={verifier_state.get('note', '')}; "
            f"ts={verifier_state.get('ts', '')}"
        ),
    )
    add_evidence(
        evidence,
        "full_source_layer_audit",
        (
            f"source_xp_path={full_row.get('source_xp_path')}; "
            f"raw_layer_index={full_row.get('layer_index')}; "
            f"semantic_label={full_row.get('full_ledger_semantic_label', '')}; "
            f"review_state={full_row.get('full_ledger_review_state', '')}; "
            f"confidence={full_row.get('full_ledger_confidence', '')}; "
            f"blockers={full_row.get('full_ledger_blockers', '')}"
        ),
    )
    add_evidence(evidence, "audit_glyph_shape", full_row.get("full_ledger_evidence") or full_row.get("evidence"))
    if direct_update:
        add_evidence(
            evidence,
            "direct_user_visual_update",
            (
                f"meaning={direct_update.get('meaning', '')}; "
                f"source_kind={direct_update.get('source_kind', '')}; "
                f"glyph_identifiers={direct_update.get('glyph_identifiers', '')}"
            ),
        )
    if identity_hint:
        add_evidence(
            evidence,
            "identity_hint",
            (
                f"hint_kind={identity_hint.get('hint_kind', '')}; "
                f"hint_status={identity_hint.get('hint_status', '')}; "
                f"hint_from={identity_hint.get('hint_from', '')}; "
                f"hint_diff_cells={identity_hint.get('hint_diff_cells', '')}; "
                f"hint_rationale={identity_hint.get('hint_rationale', '')}"
            ),
        )

    state, blockers = status_state_and_blockers(verifier_state, full_row, direct_update, source_kind)
    sha_detail = upstream_sha_evidence(full_row, upstream_sprite_dir, blockers)
    add_evidence(evidence, "upstream_sha_check", sha_detail)

    decoded_l0 = {
        "angles": parse_int(full_row.get("l0_angles"), "l0_angles", row_id),
        "anim_len": parse_anim_len(full_row.get("l0_anim_len"), row_id),
        "y_proj": parse_int(full_row.get("l0_y_proj"), "l0_y_proj", row_id),
        "y_refl": parse_int(full_row.get("l0_y_refl"), "l0_y_refl", row_id),
        "z_proj": parse_int(full_row.get("l0_z_proj"), "l0_z_proj", row_id),
        "z_refl": parse_int(full_row.get("l0_z_refl"), "l0_z_refl", row_id),
    }

    peers: list[str] = []
    if identity_hint and identity_hint.get("hint_from"):
        peers.append(str(identity_hint["hint_from"]))

    if state == STEP_A_FORBIDDEN_STATE:
        fail(f"{row_id}: Step A attempted forbidden state {STEP_A_FORBIDDEN_STATE}")

    return {
        "row_id": row_id,
        "upstream_xp_path": full_row.get("source_xp_path"),
        "raw_layer_index": parse_int(full_row.get("layer_index"), "layer_index", row_id),
        "xp_sha256": full_row.get("xp_sha256"),
        "decoded_l0": decoded_l0,
        "l1_provenance": {
            "source": "full_source_layer_ledger_20260520.json",
            "source_row_id": verifier_alias_to_full_id(row_id),
            "l0_contract": str(full_row.get("l0_contract", "")),
            "note": "Step A records evidence only; compiler use needs parity plus Semantic Mask coverage.",
        },
        "source_kind": source_kind,
        "evidence": evidence,
        "byte_shape_identity_peers": sorted(set(peers)),
        "evidence_state": state,
        "evidence_state_blockers": sorted(set(blockers)),
        "parity_receipt": None,
        "semantic_mask_refs": [],
    }


def build_ledger(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, int]]:
    verifier = load_json(args.verifier_state, "verifier state")
    if not isinstance(verifier, dict):
        fail("verifier state must be an object keyed by row id")
    full_ledger = load_json(args.full_ledger, "full source layer ledger")
    identity_hints = load_json(args.identity_hints, "identity hints")
    if not isinstance(identity_hints, dict):
        fail("identity hints must be an object keyed by row id")
    user_updates = load_json(args.user_visual_updates, "user visual updates")

    full_index = build_full_row_index(full_ledger)
    direct_update_index = build_direct_update_index(user_updates)
    selected_ids = select_verifier_ids(verifier, args.scope)
    if not selected_ids:
        fail(f"scope {args.scope!r} selected zero rows")

    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    upstream_sprite_dir = None if args.skip_upstream_sha_check else args.upstream_sprite_dir

    for row_id in selected_ids:
        full_id = verifier_alias_to_full_id(row_id)
        full_row = full_index.get(full_id)
        if not full_row:
            missing.append(f"{row_id} (full row id {full_id})")
            continue
        row = build_row(
            row_id=row_id,
            verifier_state=verifier[row_id],
            full_row=full_row,
            identity_hint=identity_hints.get(row_id),
            direct_update=direct_update_index.get(full_id),
            upstream_sprite_dir=upstream_sprite_dir,
        )
        rows.append(row)

    if missing:
        fail("selected rows missing from full source layer ledger: " + "; ".join(missing[:20]))

    rows.sort(key=lambda row: row["row_id"])
    ledger = {
        "schema_version": SCHEMA_VERSION,
        "surface_kind": SURFACE_KIND,
        "fl_ref": FL_REF,
        "rows": rows,
    }
    counts: dict[str, int] = {"selected": len(selected_ids), "rows": len(rows)}
    for row in rows:
        state = str(row["evidence_state"])
        counts[f"state:{state}"] = counts.get(f"state:{state}", 0) + 1
    return ledger, counts


def validate_generated_ledger(ledger: dict[str, Any], schema: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="step-a-evidence-") as tmp:
        ledger_path = Path(tmp) / "ledger.json"
        ledger_path.write_bytes(dump_json_bytes(ledger))
        cmd = [
            sys.executable,
            str(VALIDATOR),
            "--ledger",
            str(ledger_path),
            "--schema",
            str(schema),
            "--producer",
            "step-a",
        ]
        result = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if result.returncode != 0:
            if result.stdout:
                print(result.stdout, file=sys.stderr, end="")
            if result.stderr:
                print(result.stderr, file=sys.stderr, end="")
            fail("generated ledger failed validate_source_layer_evidence.py --producer step-a", 4)


def write_output(path: Path, ledger: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dump_json_bytes(ledger))


def existing_ledger_has_rows(path: Path) -> bool:
    if not path.exists():
        return False
    data = load_json(path, "existing ledger")
    rows = data.get("rows") if isinstance(data, dict) else None
    return isinstance(rows, list) and bool(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", required=True, choices=SCOPES)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--verifier-state", type=Path, default=DEFAULT_VERIFIER_STATE)
    parser.add_argument("--full-ledger", type=Path, default=DEFAULT_FULL_LEDGER)
    parser.add_argument("--identity-hints", type=Path, default=DEFAULT_IDENTITY_HINTS)
    parser.add_argument("--user-visual-updates", type=Path, default=DEFAULT_USER_VISUAL_UPDATES)
    parser.add_argument("--upstream-sprite-dir", type=Path, default=DEFAULT_UPSTREAM_SPRITE_DIR)
    parser.add_argument("--skip-upstream-sha-check", action="store_true")
    parser.add_argument("--out", type=Path, help="write preview ledger to this path without touching --ledger")
    parser.add_argument("--write", action="store_true", help="write generated rows to --ledger")
    parser.add_argument("--force-rewrite", action="store_true", help="allow replacing a non-empty ledger rows array")
    parser.add_argument("--print-json", action="store_true", help="print generated ledger JSON to stdout")
    args = parser.parse_args()

    if args.write and args.out:
        fail("--write and --out are mutually exclusive")
    if args.write and existing_ledger_has_rows(args.ledger) and not args.force_rewrite:
        fail(f"{args.ledger} already has rows; pass --force-rewrite to replace them")

    ledger, counts = build_ledger(args)
    validate_generated_ledger(ledger, args.schema)

    if args.write:
        write_output(args.ledger, ledger)
        action = f"wrote {args.ledger}"
    elif args.out:
        write_output(args.out, ledger)
        action = f"wrote preview {args.out}"
    else:
        action = "dry-run only; no files written"

    if args.print_json:
        sys.stdout.buffer.write(dump_json_bytes(ledger))
    else:
        summary = " ".join(f"{key}={counts[key]}" for key in sorted(counts))
        print(f"OK Step A evidence writer scope={args.scope} {summary}; {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
