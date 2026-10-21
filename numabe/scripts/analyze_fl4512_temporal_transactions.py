#!/usr/bin/env python3
"""Re-derive every FL-4512 U6 decision from its serialized transaction."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from collections import Counter
from pathlib import Path

from fl4512_temporal_score_domains import INTEGER_SCORE_DOMAINS


DECISIONS = {"KEEP", "REPLACE_SCORE", "REPLACE_IDENTITY", "FIRST", "EVICT"}
SURFACE_ID_HASH_VERSION = "fl4208.surface_id_hash.fnv1a64.packed24.v1"
IDENTITY_GATES = {
    "VALID", "MISSING_SURFACE_ID", "COLLISION", "HISTORY_CAPACITY", "ABSENT"
}


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def f32_from_bits(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def f32(value: float) -> float:
    return f32_from_bits(struct.unpack("<I", struct.pack("<f", value))[0])


def load_table(path: Path) -> dict:
    table = json.loads(path.read_text(encoding="utf-8"))
    expected = table.get("table_sha256")
    payload = dict(table)
    payload.pop("table_sha256", None)
    if table.get("schema") != "fl4512.temporal_margin_table.v1":
        raise SystemExit("FAIL-CLOSED: margin-table schema mismatch")
    if expected != canonical_sha256(payload):
        raise SystemExit("FAIL-CLOSED: margin-table hash mismatch")
    if table.get("suppressed_real_change") != 0:
        raise SystemExit("FAIL-CLOSED: provisional table suppressed real change")
    if table.get("perceptual_claim") is not False:
        raise SystemExit("FAIL-CLOSED: provisional table carries perceptual claim")
    return table


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transactions")
    parser.add_argument("--margin-table", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    transaction_path = Path(args.transactions)
    table_path = Path(args.margin_table)
    table = load_table(table_path)
    rows = [json.loads(line) for line in transaction_path.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise SystemExit("FAIL-CLOSED: empty transaction stream")
    margin_rows = {
        (int(row["scorer_kind"]), int(row["scorer_version"])): row
        for row in table["rows"]
    }
    counts: Counter[str] = Counter()
    failures: list[dict] = []
    retained = 0
    presented_live_keys: set[tuple[int, int, int]] = set()
    for index, row in enumerate(rows):
        reasons: list[str] = []
        if row.get("schema") != "fl4512.temporal_transaction.v2":
            reasons.append("schema")
        if row.get("surface_id_hash_version") != SURFACE_ID_HASH_VERSION:
            reasons.append("surface_id_hash_version")
        if row.get("identity_gate") not in IDENTITY_GATES:
            reasons.append("identity_gate")
        decision = row.get("decision")
        if decision not in DECISIONS:
            reasons.append("decision_enum")
        else:
            counts[decision] += 1
        if decision == "EVICT":
            if row.get("presented_live") is not False:
                reasons.append("evict_presented_live")
            if row.get("sid_cur") is not None or int(row.get("absent_ctr", -1)) != 3:
                reasons.append("evict_contract")
            if int(row.get("g_selected", -1)) != int(row.get("g_star", -2)):
                reasons.append("evict_selected")
        else:
            if not isinstance(row.get("presented_live"), bool):
                reasons.append("presented_live_type")
            if row.get("presented_live") is True:
                cell = row.get("cell", [-1, -1])
                key = (int(row.get("frame", -1)), int(cell[0]), int(cell[1]))
                if key in presented_live_keys:
                    reasons.append("duplicate_presented_live_decision")
                presented_live_keys.add(key)
            g_star = int(row.get("g_star", -1))
            g_selected = int(row.get("g_selected", -1))
            if decision == "FIRST":
                if g_selected != g_star:
                    reasons.append("first_not_argmin")
                if row.get("sid_prev") is not None and int(row.get("absent_ctr", 0)) == 0:
                    reasons.append("first_without_disocclusion")
            if decision == "REPLACE_IDENTITY":
                if g_selected != g_star:
                    reasons.append("identity_not_argmin")
                identity_failure = (
                    bool(row.get("collision", {}).get("contaminated"))
                    or row.get("metadata_valid") is not True
                    or row.get("calibration_match") is not True
                    or row.get("winner_match") is not True
                    or row.get("previous_legal") is not True
                )
                if not identity_failure:
                    reasons.append("identity_without_failed_gate")
            if decision in {"KEEP", "REPLACE_SCORE"}:
                key = (int(row.get("scorer_kind_cur", 0)),
                       int(row.get("scorer_version_cur", 0)))
                margin = margin_rows.get(key)
                if not margin:
                    reasons.append("margin_row_missing")
                if row.get("margin_table_hash") != table["table_sha256"]:
                    reasons.append("table_hash")
                if margin and row.get("calibration_id_cur") != \
                        margin.get("calibration_id"):
                    reasons.append("calibration_id")
                for flag in ("previous_legal", "metadata_valid",
                             "calibration_match", "winner_match"):
                    if row.get(flag) is not True:
                        reasons.append(flag)
                if (int(row.get("scorer_kind_prev", 0)),
                    int(row.get("scorer_version_prev", 0))) != key:
                    reasons.append("scorer_change_not_identity")
                if row.get("calibration_id_prev") != row.get("calibration_id_cur"):
                    reasons.append("calibration_change_not_identity")
                keep = False
                if margin and row.get("score_domain") in INTEGER_SCORE_DOMAINS:
                    delta = int(row["delta_keep_u32"])
                    if delta != int(margin["delta_keep"]["value"]):
                        reasons.append("delta_u32")
                    keep = int(row["s_prev_u32"]) <= int(row["s_star_u32"]) + delta
                elif margin and row.get("score_domain") == \
                        "fl4260.shape6_weighted_l2.v1.binary32":
                    delta_bits = int(row["delta_keep_f32_bits"])
                    if delta_bits != int(margin["delta_keep"]["bits_le_u32"]):
                        reasons.append("delta_f32")
                    s_prev = f32_from_bits(int(row["s_prev_f32_bits"]))
                    s_star = f32_from_bits(int(row["s_star_f32_bits"]))
                    delta = f32_from_bits(delta_bits)
                    rhs = f32(s_star + delta)
                    if not all(math.isfinite(value) for value in
                               (s_prev, s_star, delta, rhs)):
                        reasons.append("nonfinite")
                    keep = s_prev <= rhs
                else:
                    reasons.append("score_domain")
                expected = "KEEP" if keep else "REPLACE_SCORE"
                if decision != expected:
                    reasons.append(f"decision_expected_{expected}")
                if decision == "KEEP":
                    retained += 1
                    if g_selected != int(row.get("g_prev", -1)):
                        reasons.append("keep_not_incumbent")
                elif g_selected != g_star:
                    reasons.append("replace_not_argmin")
        if reasons:
            failures.append({"index": index, "frame": row.get("frame"),
                             "cell": row.get("cell"), "reasons": reasons})

    result = {
        "schema": "fl4512.temporal_transaction_analysis.v2",
        "proof_status": "PROOF_OPEN",
        "verdict": "PASS" if not failures else "FAIL",
        "transactions": len(rows),
        "decision_counts": dict(sorted(counts.items())),
        "retained": retained,
        "presented_live_transactions": len(presented_live_keys),
        "rederive_failures": len(failures),
        "suppressed_real_change": 0 if not failures else None,
        "margin_table_sha256": table["table_sha256"],
        "transaction_sha256": hashlib.sha256(transaction_path.read_bytes()).hexdigest(),
        "failures": failures[:100],
    }
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
