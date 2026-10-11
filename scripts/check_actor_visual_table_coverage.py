#!/usr/bin/env python3
"""Check generated actor visual table coverage against reachable keys."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TABLE = REPO_ROOT / "engine" / "actor_visual_profile_table.generated.h"
DEFAULT_REACHABILITY = REPO_ROOT / "assets/actor_visual_profiles/current/server_reachable_keys.json"
DEFAULT_BINDINGS = REPO_ROOT / "assets/actor_visual_profiles/source/profile_bindings.json"
DEFAULT_CONTRACT_FREEZE = REPO_ROOT / "docs/research/ascii/semantic_maps/upstream_xp_cell_contract/family_contract_freeze.json"
DEFAULT_CELL_DECISIONS = REPO_ROOT / "docs/research/ascii/semantic_maps/upstream_xp_cell_contract/cell_role_decisions.jsonl"
DEFAULT_CUTOVER_RECEIPT = REPO_ROOT / "docs/research/ascii/semantic_maps/upstream_xp_cell_contract/compiler_cutover.json"
CUTOVER_RECEIPT_SCHEMA = "fl4162.compiler_cutover.v1"
CROSSBOW_ITEM_ID = 400 + 17


def _key_values(key: dict[str, int | list[int]]) -> tuple[int, ...]:
    values = [
        int(key["skin_id"]),
        int(key["actor_style_id"]),
        int(key["presentation_kind_id"]),
        int(key["variation_id"]),
        int(key["mount_id"]),
        int(key["rig_id"]),
        int(key["head_item_id"]),
        int(key["head_style_id"]),
        int(key["chest_item_id"]),
        int(key["chest_style_id"]),
        int(key["weapon_item_id"]),
        int(key["weapon_style_id"]),
        int(key["shield_item_id"]),
        int(key["shield_style_id"]),
    ]
    values.extend(int(v) for v in key["future_slot_kind_ids"])  # type: ignore[index]
    values.extend(int(v) for v in key["future_item_ids"])  # type: ignore[index]
    values.extend(int(v) for v in key["future_style_ids"])  # type: ignore[index]
    return tuple(values)


def _empty_key() -> dict[str, Any]:
    return {
        "skin_id": 0,
        "actor_style_id": 0,
        "presentation_kind_id": 0,
        "variation_id": 0,
        "mount_id": 0,
        "rig_id": 0,
        "head_item_id": 0,
        "head_style_id": 0,
        "chest_item_id": 0,
        "chest_style_id": 0,
        "weapon_item_id": 0,
        "weapon_style_id": 0,
        "shield_item_id": 0,
        "shield_style_id": 0,
        "future_slot_kind_ids": [0, 0, 0, 0],
        "future_item_ids": [0, 0, 0, 0],
        "future_style_ids": [0, 0, 0, 0],
    }


def _expected_keys_from_reachability(path: Path) -> set[tuple[int, ...]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("reachable_keys")
    if not isinstance(rows, list):
        raise SystemExit(f"{path}: missing reachable_keys array")
    out: set[tuple[int, ...]] = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("key"), dict):
            raise SystemExit(f"{path}: malformed reachable row")
        key = _empty_key()
        src = row["key"]
        for field in (
            "skin_id",
            "actor_style_id",
            "presentation_kind_id",
            "variation_id",
            "mount_id",
            "rig_id",
            "head_item_id",
            "head_style_id",
            "chest_item_id",
            "chest_style_id",
            "weapon_item_id",
            "weapon_style_id",
            "shield_item_id",
            "shield_style_id",
        ):
            key[field] = int(src.get(field, 0))
        future_slots = src.get("future_slots", [])
        if not isinstance(future_slots, list) or len(future_slots) > 4:
            raise SystemExit(f"{path}: malformed future_slots")
        for index, slot in enumerate(future_slots):
            key["future_slot_kind_ids"][index] = int(slot.get("slot_kind_id", 0))
            key["future_item_ids"][index] = int(slot.get("item_id", 0))
            key["future_style_ids"][index] = int(slot.get("visual_style_id", 0))
        out.add(_key_values(key))
    declared = doc.get("reachable_key_count")
    if isinstance(declared, int) and declared != len(out):
        raise SystemExit(f"{path}: reachable_key_count={declared} unique_keys={len(out)}")
    return out


def _literal_to_int(token: str) -> int:
    token = token.strip()
    if token == "ACTOR_VISUAL_PROFILE_ITEM_WEAPON_CROSSBOW_ID":
        return CROSSBOW_ITEM_ID
    return int(token)


def _table_keys(path: Path) -> set[tuple[int, ...]]:
    text = path.read_text(encoding="utf-8")
    marker = "static constexpr CompiledActorVisualRow kCompiledActorVisualRows[] = {"
    start = text.index(marker)
    end = text.index("};", start)
    block = text[start:end]
    rows = re.findall(r'\{\s*"[^"]+",\s*\{(.*?)\},\s*\d+,', block, re.S)
    keys: set[tuple[int, ...]] = set()
    for row in rows:
        scalars = [_literal_to_int(v) for v in re.findall(r"(ACTOR_VISUAL_PROFILE_ITEM_WEAPON_CROSSBOW_ID|\d+),", row)]
        array_values: list[int] = []
        for array in re.findall(r"\{([^{}]*)\}", row):
            array_values.extend(_literal_to_int(v) for v in array.split(",") if v.strip())
        scalar_prefix = scalars[:14]
        values = tuple(scalar_prefix + array_values[:12])
        if len(values) != 26:
            raise SystemExit(f"failed to parse table key with {len(values)} fields")
        keys.add(values)
    return keys


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _header_hash(text: str, name: str) -> str:
    match = re.search(
        rf'static constexpr const char\* {re.escape(name)} = "([0-9a-f]{{64}})";',
        text,
    )
    if not match:
        raise SystemExit(f"generated table lacks {name}")
    return match.group(1)


def _check_freshness(
    table: Path,
    reachability: Path,
    bindings: Path,
    contract_freeze: Path,
    cell_decisions: Path,
    cutover_receipt: Path,
) -> None:
    text = table.read_text(encoding="utf-8")
    expected_hashes = {
        "ACTOR_VISUAL_PROFILE_SERVER_REACHABILITY_ARTIFACT_SHA256": _sha256_file(reachability),
        "ACTOR_VISUAL_PROFILE_SOURCE_BINDINGS_SHA256": _sha256_file(bindings),
        "ACTOR_VISUAL_PROFILE_UPSTREAM_CONTRACT_FREEZE_SHA256": _sha256_file(contract_freeze),
        "ACTOR_VISUAL_PROFILE_UPSTREAM_CELL_DECISIONS_SHA256": _sha256_file(cell_decisions),
    }
    stale = [
        name
        for name, expected in expected_hashes.items()
        if _header_hash(text, name) != expected
    ]
    if stale:
        raise SystemExit(
            "actor visual generated table is stale: " + ", ".join(stale)
        )

    reachability_doc = json.loads(reachability.read_text(encoding="utf-8"))
    bindings_doc = json.loads(bindings.read_text(encoding="utf-8"))
    if bindings_doc.get("server_reachability_hash") != reachability_doc.get(
        "server_reachability_hash"
    ):
        raise SystemExit("profile bindings are stale against server reachability")
    if bindings_doc.get("upstream_contract_freeze_sha256") != _sha256_file(
        contract_freeze
    ):
        raise SystemExit("profile bindings are stale against frozen upstream contract")

    try:
        receipt = json.loads(cutover_receipt.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"missing compiler cutover receipt: {cutover_receipt}")
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"invalid compiler cutover receipt {cutover_receipt}: {exc}")
    if (
        receipt.get("schema") != CUTOVER_RECEIPT_SCHEMA
        or receipt.get("compiler_cutover_complete") is not True
        or receipt.get("runtime_authoritative") is not True
        or receipt.get("authority_owner") != "compiled_actor_visual_profile_table"
    ):
        raise SystemExit("compiler cutover receipt does not establish runtime ownership")
    receipt_hashes = receipt.get("source_hashes") or {}
    expected_receipt_hashes = {
        "family_contract_freeze_sha256": _sha256_file(contract_freeze),
        "cell_role_decisions_sha256": _sha256_file(cell_decisions),
        "server_reachable_keys_sha256": _sha256_file(reachability),
        "profile_bindings_sha256": _sha256_file(bindings),
        "generated_table_sha256": _sha256_file(table),
    }
    stale_receipt = [
        name
        for name, expected in expected_receipt_hashes.items()
        if receipt_hashes.get(name) != expected
    ]
    if stale_receipt:
        raise SystemExit(
            "compiler cutover receipt is stale: " + ", ".join(stale_receipt)
        )
    invariants = receipt.get("invariants") or {}
    required_invariants = (
        "render_operation_separate_from_semantic_contributions",
        "composite_semantic_contribution_sets_first_class",
        "server_catalog_is_reachability_owner",
        "local_ahsw_resolution_deleted",
        "local_reachability_enumeration_deleted",
        "per_key_legacy_merge_fallback_deleted",
    )
    missing_invariants = [name for name in required_invariants if invariants.get(name) is not True]
    if missing_invariants:
        raise SystemExit(
            "compiler cutover receipt lacks invariants: "
            + ", ".join(missing_invariants)
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", type=Path, default=DEFAULT_TABLE)
    parser.add_argument("--reachability", type=Path, default=DEFAULT_REACHABILITY)
    parser.add_argument("--bindings", type=Path, default=DEFAULT_BINDINGS)
    parser.add_argument("--contract-freeze", type=Path, default=DEFAULT_CONTRACT_FREEZE)
    parser.add_argument("--cell-decisions", type=Path, default=DEFAULT_CELL_DECISIONS)
    parser.add_argument("--cutover-receipt", type=Path, default=DEFAULT_CUTOVER_RECEIPT)
    args = parser.parse_args(argv)

    _check_freshness(
        args.table,
        args.reachability,
        args.bindings,
        args.contract_freeze,
        args.cell_decisions,
        args.cutover_receipt,
    )
    expected = _expected_keys_from_reachability(args.reachability)
    actual = _table_keys(args.table)
    missing = sorted(expected - actual)
    orphan = sorted(actual - expected)
    if missing or orphan:
        print(f"actor visual coverage mismatch: expected={len(expected)} actual={len(actual)} missing={len(missing)} orphan={len(orphan)}")
        for label, rows in (("missing", missing[:20]), ("orphan", orphan[:20])):
            for row in rows:
                print(f"{label}: {row}")
        return 1
    print(f"actor visual coverage ok: {len(actual)} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
