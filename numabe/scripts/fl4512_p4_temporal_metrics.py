#!/usr/bin/env python3
"""Run the offline FL-4512 P4 temporal replay and trajectory metric harness.

This program owns no rendering state.  It consumes the immutable v2 transaction
stream, invokes the canonical decision replayer, then emits P4 trajectory counts.
An optional presentation stream is a final-cell witness only; omitted presentation
evidence is recorded as UNMEASURED, never converted into a visual claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
REPLAYER = REPO / "scripts" / "analyze_fl4512_temporal_transactions.py"
EVICT_ABSENT_CTR = 3


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SystemExit(f"FAIL-CLOSED: missing JSONL input: {path}")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    if not rows:
        raise SystemExit(f"FAIL-CLOSED: empty JSONL input: {path}")
    return rows


def surface_key(surface_id: Any) -> str | None:
    if surface_id is None:
        return None
    return json.dumps(surface_id, sort_keys=True, separators=(",", ":"))


def frame_cell_key(row: dict[str, Any]) -> tuple[int, int, int]:
    cell = row.get("cell")
    if not isinstance(cell, list) or len(cell) != 2:
        raise ValueError("cell")
    return int(row["frame"]), int(cell[0]), int(cell[1])


def run_replayer(transactions: Path, margin_table: Path, replay_out: Path) -> dict[str, Any]:
    command = [
        sys.executable, str(REPLAYER), str(transactions),
        "--margin-table", str(margin_table), "--out", str(replay_out),
    ]
    completed = subprocess.run(command, cwd=REPO, text=True, capture_output=True)
    if not replay_out.is_file():
        raise SystemExit(
            "FAIL-CLOSED: canonical transaction replayer produced no analysis "
            f"(exit={completed.returncode} stderr={completed.stderr.strip()!r})")
    replay = json.loads(replay_out.read_text(encoding="utf-8"))
    return {
        "exit_code": completed.returncode,
        "verdict": replay.get("verdict"),
        "rederive_failures": replay.get("rederive_failures"),
        "analysis_sha256": sha256_file(replay_out),
    }


def analyze_transactions(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    failures: list[dict[str, Any]] = []
    decision_counts: Counter[str] = Counter()
    identities: dict[str, list[dict[str, Any]]] = defaultdict(list)
    events: Counter[str] = Counter()
    seen_frame_identity: set[tuple[int, str]] = set()
    previous_frame = -1

    for index, row in enumerate(rows):
        reasons: list[str] = []
        try:
            frame_key = frame_cell_key(row)
        except (KeyError, TypeError, ValueError):
            failures.append({"index": index, "reasons": ["frame_cell"]})
            continue
        frame = frame_key[0]
        if frame < previous_frame:
            reasons.append("stream_not_frame_ordered")
        previous_frame = max(previous_frame, frame)
        decision = row.get("decision")
        decision_counts[str(decision)] += 1
        sid = surface_key(row.get("sid_cur"))
        collision = bool(row.get("collision", {}).get("contaminated"))
        identity_gate = row.get("identity_gate")

        if decision == "EVICT":
            events["eviction"] += 1
            if row.get("presented_live") is not False:
                reasons.append("eviction_presented")
            if sid is not None or int(row.get("absent_ctr", -1)) != EVICT_ABSENT_CTR:
                reasons.append("eviction_contract")
        else:
            if sid is not None:
                pair = (frame, sid)
                if pair in seen_frame_identity and not collision:
                    reasons.append("same_frame_surface_id_collision_unflagged")
                seen_frame_identity.add(pair)
                identities[sid].append(row)
            if decision == "FIRST" and row.get("sid_prev") is not None:
                events["disocclusion"] += 1
            if collision or identity_gate == "COLLISION":
                events["collision"] += 1
                if decision != "REPLACE_IDENTITY":
                    reasons.append("collision_not_identity_replace")
                if int(row.get("g_selected", -1)) != int(row.get("g_star", -2)):
                    reasons.append("collision_not_current_argmin")

        if reasons:
            failures.append({"index": index, "frame": frame, "cell": row.get("cell"),
                             "reasons": reasons})

    continuity_pairs = 0
    stable_identity_glyph_switches = 0
    stable_identity_keep_switches = 0
    gap_returns = 0
    for sid, trajectory in identities.items():
        ordered = sorted(trajectory, key=lambda row: int(row["frame"]))
        for previous, current in zip(ordered, ordered[1:]):
            previous_frame = int(previous["frame"])
            current_frame = int(current["frame"])
            if current_frame == previous_frame:
                continue
            if current_frame != previous_frame + 1:
                gap_returns += 1
                expected = ("REPLACE_IDENTITY" if
                            (current.get("identity_gate") == "COLLISION" or
                             bool(current.get("collision", {}).get("contaminated")))
                            else "FIRST")
                if current.get("decision") != expected:
                    failures.append({
                        "frame": current_frame, "cell": current.get("cell"),
                        "surface_id": sid,
                        "reasons": [f"return_after_gap_not_{expected.lower()}"],
                    })
                continue
            continuity_pairs += 1
            if int(previous.get("g_selected", -1)) != int(current.get("g_selected", -1)):
                stable_identity_glyph_switches += 1
                if current.get("decision") == "KEEP":
                    stable_identity_keep_switches += 1
                    failures.append({
                        "frame": current_frame, "cell": current.get("cell"),
                        "surface_id": sid, "reasons": ["keep_changed_selected_glyph"],
                    })

    return {
        "transactions": len(rows),
        "decision_counts": dict(sorted(decision_counts.items())),
        "event_counts": dict(sorted(events.items())),
        "surface_id_trajectories": len(identities),
        "consecutive_identity_pairs": continuity_pairs,
        "return_after_gap": gap_returns,
        "stable_identity_glyph_switches": stable_identity_glyph_switches,
        "stable_identity_keep_switches": stable_identity_keep_switches,
    }, failures


def analyze_presentation(path: Path | None, transaction_rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if path is None:
        return {
            "status": "UNMEASURED",
            "reason": "presentation_frame_stream_absent",
            "definition": "final (GlyphId,fg,bg) signature changes for exact frame/cell witnesses",
        }, []

    rows = read_jsonl(path)
    failures: list[dict[str, Any]] = []
    signatures: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    for index, row in enumerate(rows):
        reasons: list[str] = []
        if row.get("schema") != "fl4512.temporal_presentation_frame.v1":
            reasons.append("presentation_schema")
        try:
            key = frame_cell_key(row)
            signature = (int(row["gid"]), int(row["fg"]), int(row["bg"]))
        except (KeyError, TypeError, ValueError):
            reasons.append("presentation_fields")
            key = None
            signature = None
        if key is not None:
            if key in signatures:
                reasons.append("duplicate_presentation_cell")
            else:
                signatures[key] = signature
        if reasons:
            failures.append({"index": index, "reasons": reasons})

    expected = {
        frame_cell_key(row): int(row["g_selected"])
        for row in transaction_rows if row.get("presented_live") is True
    }
    for key, selected_gid in expected.items():
        signature = signatures.get(key)
        if signature is None:
            failures.append({"frame": key[0], "cell": [key[1], key[2]],
                             "reasons": ["presented_cell_missing"]})
        elif signature[0] != selected_gid:
            failures.append({"frame": key[0], "cell": [key[1], key[2]],
                             "reasons": ["presented_gid_mismatch"],
                             "selected_gid": selected_gid, "presented_gid": signature[0]})

    visible_switches = 0
    comparable_pairs = 0
    by_cell: dict[tuple[int, int], list[tuple[int, tuple[int, int, int]]]] = defaultdict(list)
    for (frame, x, y), signature in signatures.items():
        by_cell[(x, y)].append((frame, signature))
    for trajectory in by_cell.values():
        ordered = sorted(trajectory)
        for (previous_frame, previous), (current_frame, current) in zip(ordered, ordered[1:]):
            if current_frame != previous_frame + 1:
                continue
            comparable_pairs += 1
            visible_switches += previous != current
    return {
        "status": "MEASURED" if not failures else "FAIL",
        "schema": "fl4512.temporal_presentation_frame.v1",
        "presentation_sha256": sha256_file(path),
        "presented_cells": len(signatures),
        "comparable_fixed_cell_pairs": comparable_pairs,
        "visible_signature_switches": visible_switches,
        "definition": "final (GlyphId,fg,bg) signature changes for exact frame/cell witnesses",
    }, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transactions", type=Path)
    parser.add_argument("--margin-table", type=Path, required=True)
    parser.add_argument("--replay-out", type=Path, required=True)
    parser.add_argument("--presentation-frame", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    rows = read_jsonl(args.transactions)
    replay = run_replayer(args.transactions, args.margin_table, args.replay_out)
    trajectory, failures = analyze_transactions(rows)
    presentation, presentation_failures = analyze_presentation(args.presentation_frame, rows)
    failures.extend(presentation_failures)
    if replay["verdict"] != "PASS":
        failures.append({"reasons": ["canonical_transaction_replay_failed"]})

    result = {
        "schema": "fl4512.p4_temporal_metrics.v1",
        "proof_status": "PROOF_OPEN",
        "verdict": "PASS" if not failures else "FAIL",
        "transactions_sha256": sha256_file(args.transactions),
        "margin_table_sha256": sha256_file(args.margin_table),
        "canonical_replay": replay,
        "trajectory": trajectory,
        "presentation": presentation,
        "failures": failures[:100],
        "failure_count": len(failures),
    }
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
