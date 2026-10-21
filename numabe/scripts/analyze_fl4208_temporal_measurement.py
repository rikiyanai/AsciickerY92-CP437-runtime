#!/usr/bin/env python3
"""Calibrate typed FL-4512 U6 margins from presentation-neutral N2 records."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from collections import Counter, defaultdict
from pathlib import Path

from fl4512_temporal_score_domains import (
    INTEGER_SCORE_DOMAINS,
    PRODUCT_TEMPORAL_SCORERS,
    SCORER_BY_ID,
    SCORER_BY_KEY,
)


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_margin_table(path: Path) -> dict:
    table = json.loads(path.read_text(encoding="utf-8"))
    expected = table.get("table_sha256")
    payload = dict(table)
    payload.pop("table_sha256", None)
    if table.get("schema") != "fl4512.temporal_margin_table.v1":
        raise SystemExit("FAIL-CLOSED: base margin-table schema mismatch")
    if expected != canonical_sha256(payload):
        raise SystemExit("FAIL-CLOSED: base margin-table hash mismatch")
    if table.get("method") not in {
            "nearest_rank_p99.v1", "mixed_policy_basis.v1"}:
        raise SystemExit("FAIL-CLOSED: base margin-table method mismatch")
    if not isinstance(table.get("policy_set_id"), str) or not table["policy_set_id"]:
        raise SystemExit("FAIL-CLOSED: base margin-table policy-set identity missing")
    if table.get("suppressed_real_change") != 0:
        raise SystemExit("FAIL-CLOSED: base table suppressed real change")
    if table.get("perceptual_claim") is not False:
        raise SystemExit("FAIL-CLOSED: base table carries perceptual claim")
    return table


def f32_from_bits(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def f32_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def candidate_replay_failures(row: dict, spec: object) -> list[str]:
    """Validate the presentation-neutral replay row used for one margin sample."""
    reasons: list[str] = []
    if row.get("scorer_id") != spec.scorer_id:
        reasons.append("scorer_id")
    if row.get("previous_legal") is not True:
        reasons.append("previous_legal")
    if row.get("metadata_valid") is not True:
        reasons.append("metadata_valid")
    if row.get("winner_match") is not True:
        reasons.append("winner_match")
    if row.get("collision") is not False:
        reasons.append("collision")
    if row.get("sid_cur") is None:
        reasons.append("sid_cur")
    if row.get("calibration_id_prev") != "unassigned" or \
            row.get("calibration_id_cur") != "unassigned":
        reasons.append("measurement_calibration_identity")
    if row.get("margin_table_hash") is not None:
        reasons.append("measurement_margin_table_hash")
    try:
        if (int(row["scorer_kind_prev"]), int(row["scorer_version_prev"])) != (
                spec.scorer_kind, spec.scorer_version):
            reasons.append("previous_scorer_identity")
        absent_ctr = int(row["absent_ctr"])
        if absent_ctr < 0 or absent_ctr > 2:
            reasons.append("absent_ctr")
        if int(row["g_prev"]) == int(row["g_star"]):
            reasons.append("not_a_raw_flip")
        if int(row["replay_gid"]) != int(row["g_star"]):
            reasons.append("replay_winner")
        if int(row["pool_revision"]) <= 0:
            reasons.append("pool_revision")
        for key in (
                "frame", "profile_namespace", "material_key", "selected_role",
                "descriptor_kind", "descriptor_version"):
            int(row[key])
        cell = row["cell"]
        if not isinstance(cell, list) or len(cell) != 2:
            reasons.append("cell")
        else:
            int(cell[0])
            int(cell[1])
        if spec.delta_type == "u32":
            s_prev = int(row["s_prev_u32"])
            s_star = int(row["s_star_u32"])
            margin = int(row["margin_u32"])
            if not all(0 <= value <= 0xFFFFFFFF
                       for value in (s_prev, s_star, margin)):
                reasons.append("u32_range")
            elif s_prev < s_star or margin != s_prev - s_star:
                reasons.append("u32_margin_identity")
        else:
            margin = f32_from_bits(int(row["margin_f32_bits"]))
            if not math.isfinite(margin) or margin < 0:
                reasons.append("binary32_margin")
    except (KeyError, TypeError, ValueError, OverflowError):
        reasons.append("required_field")
    return sorted(set(reasons))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("measurement", nargs="+")
    parser.add_argument("--out", required=True)
    parser.add_argument("--margin-table-out")
    parser.add_argument(
        "--target-scorer-id",
        action="append",
        help=("calibrate only this exact scorer identity; repeat for multiple "
              "independent domains. The default requires the complete product set."),
    )
    parser.add_argument(
        "--base-margin-table",
        help=("verified table whose independently calibrated non-target rows are "
              "preserved while target scorer kinds are replaced"),
    )
    args = parser.parse_args()
    if args.base_margin_table and not args.margin_table_out:
        parser.error("--base-margin-table requires --margin-table-out")
    if args.target_scorer_id:
        unknown = sorted(set(args.target_scorer_id) - set(SCORER_BY_ID))
        if unknown:
            parser.error(f"unknown --target-scorer-id value(s): {', '.join(unknown)}")
        target_specs = tuple(
            sorted({SCORER_BY_ID[item] for item in args.target_scorer_id},
                   key=lambda spec: spec.key)
        )
    else:
        target_specs = PRODUCT_TEMPORAL_SCORERS
    measurement_paths = [Path(item) for item in args.measurement]
    rows = [
        json.loads(line)
        for path in measurement_paths
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise SystemExit("FAIL-CLOSED: empty measurement")
    schemas = {row.get("schema") for row in rows}
    if schemas != {"fl4208.temporal_measurement.v2"}:
        raise SystemExit(f"FAIL-CLOSED: mixed schemas {sorted(schemas)}")

    summaries = [row for row in rows if row.get("record_kind") == "frame_summary"]
    cell_rows = [row for row in rows if row.get("record_kind") == "cell"]
    if not summaries:
        raise SystemExit("FAIL-CLOSED: measurement has no frame summaries")
    if not cell_rows:
        raise SystemExit("FAIL-CLOSED: measurement has no cell records")
    gates = Counter(row.get("gate") for row in cell_rows)
    frames = sorted({int(row["frame"]) for row in rows})
    candidates = [row for row in cell_rows
                  if row.get("gate") == "CALIBRATION_CANDIDATE"]
    flips = [row for row in candidates if row.get("g_prev") != row.get("g_star")]
    margins: list[float] = []
    by_role: dict[str, list[float]] = defaultdict(list)
    all_by_scorer: dict[tuple[int, int, str], list[float]] = defaultdict(list)
    for row in flips:
        scorer = (int(row["scorer_kind_cur"]), int(row["scorer_version_cur"]),
                  str(row["score_domain"]))
        spec = SCORER_BY_KEY.get(scorer)
        if spec is None:
            raise SystemExit(f"FAIL-CLOSED: unknown scorer identity {scorer!r}")
        if scorer[2] in INTEGER_SCORE_DOMAINS:
            margin = float(int(row["margin_u32"]))
        elif spec.delta_type == "ieee754_binary32":
            margin = f32_from_bits(int(row["margin_f32_bits"]))
        else:
            raise SystemExit(f"FAIL-CLOSED: unknown score domain {scorer[2]!r}")
        if not math.isfinite(margin) or margin < 0:
            raise SystemExit("FAIL-CLOSED: invalid native-domain margin")
        all_by_scorer[scorer].append(margin)
    required_scorers = {spec.key for spec in target_specs}
    by_scorer = {
        key: values for key, values in all_by_scorer.items()
        if key in required_scorers
    }
    target_flips = [
        row for row in flips
        if (int(row["scorer_kind_cur"]), int(row["scorer_version_cur"]),
            str(row["score_domain"])) in required_scorers
    ]
    for row in target_flips:
        key = (int(row["scorer_kind_cur"]), int(row["scorer_version_cur"]),
               str(row["score_domain"]))
        spec = SCORER_BY_KEY[key]
        margin = (float(int(row["margin_u32"]))
                  if spec.delta_type == "u32"
                  else f32_from_bits(int(row["margin_f32_bits"])))
        margins.append(margin)
        by_role[str(row.get("selected_role"))].append(margin)
    denominator = {
        key: sum(int(row.get(key, 0)) for row in summaries)
        for key in (
            "final_valid_cells", "surface_id_valid_cells",
            "surface_id_missing_cells", "distinct_surface_ids",
            "collision_ids", "collision_cells", "mesh_selected_cells",
            "mesh_surface_id_valid_cells", "mesh_surface_id_missing_cells",
            "mesh_owner_hash_collision_cells", "mesh_anchor_collision_cells",
        )
    }
    mesh_identity_ready = (
        denominator["mesh_surface_id_valid_cells"] > 0
        and denominator["mesh_owner_hash_collision_cells"] == 0
    )
    surface_identity_ready = (
        denominator["surface_id_valid_cells"] > 0
        and denominator["collision_cells"] == 0
    )
    identity_ready_by_scorer = {
        spec.key: (
            mesh_identity_ready if spec.scorer_id == "u7.mask.v1"
            else surface_identity_ready
        )
        for spec in target_specs
    }
    target_identity_ready = all(identity_ready_by_scorer.values())
    observed_scorers = set(by_scorer)
    all_observed_scorers = set(all_by_scorer)
    target_rows = [
        row for row in cell_rows
        if (int(row.get("scorer_kind_cur", 0)),
            int(row.get("scorer_version_cur", 0)),
            str(row.get("score_domain"))) in required_scorers
    ]
    target_gate_counts = Counter(row.get("gate") for row in target_rows)
    target_gate_counts_by_role: dict[str, Counter[str]] = defaultdict(Counter)
    target_gate_counts_by_fact_mask: dict[str, Counter[str]] = defaultdict(Counter)
    for row in target_rows:
        target_gate_counts_by_role[str(row.get("selected_role"))][
            str(row.get("gate"))] += 1
        target_gate_counts_by_fact_mask[str(row.get("fact_mask"))][
            str(row.get("gate"))] += 1
    bad_replay_rows = target_gate_counts.get("SCORER_PARITY_MISMATCH", 0) + \
        target_gate_counts.get("SCORE_DOMAIN_INVALID", 0)
    replay_failures = []
    for index, row in enumerate(target_flips):
        key = (int(row["scorer_kind_cur"]), int(row["scorer_version_cur"]),
               str(row["score_domain"]))
        reasons = candidate_replay_failures(row, SCORER_BY_KEY[key])
        if reasons:
            replay_failures.append({
                "flip_index": index,
                "frame": row.get("frame"),
                "cell": row.get("cell"),
                "reasons": reasons,
            })
    frames_contiguous = frames == list(range(frames[0], frames[-1] + 1))
    calibration_ready = (
        bool(target_flips) and bad_replay_rows == 0 and not replay_failures
        and target_identity_ready
        and frames_contiguous
        and observed_scorers == required_scorers
    )
    corpus_records = [
        {"path": str(path), "sha256": sha256_file(path)}
        for path in measurement_paths
    ]
    corpus_hash = canonical_sha256(corpus_records)
    margin_rows = []
    for (kind, version, domain), values in sorted(by_scorer.items()):
        spec = SCORER_BY_KEY[(kind, version, domain)]
        ordered = sorted(values)
        rank_index = min(len(ordered) - 1, max(0, math.ceil(0.99 * len(ordered)) - 1))
        delta = ordered[rank_index]
        row = {
            "scorer_kind": kind,
            "scorer_version": version,
            "scorer_id": spec.scorer_id,
            "score_domain": domain,
            "calibration_id": "provisional-n2",
            "policy_basis": "calibrated_nearest_rank_p99.v1",
            "corpus_hash": corpus_hash,
            "quantile_rank": {
                "numerator": 99, "denominator": 100,
                "nearest_rank_index_zero_based": rank_index,
                "sample_count": len(ordered),
            },
        }
        if spec.delta_type == "u32":
            row["delta_keep"] = {"type": "u32", "value": int(delta)}
        else:
            row["delta_keep"] = {
                "type": "ieee754_binary32", "bits_le_u32": f32_bits(delta),
                "decimal": delta,
            }
        margin_rows.append(row)
    result = {
        "schema": "fl4208.temporal_measurement_analysis.v1",
        "proof_status": "PROOF_OPEN",
        "measurements": corpus_records,
        "corpus_hash": corpus_hash,
        "rows": len(rows),
        "cell_rows": len(cell_rows),
        "frame_summary_rows": len(summaries),
        "frames": {
            "count": len(frames), "first": frames[0], "last": frames[-1],
            "contiguous": frames_contiguous,
        },
        "gate_counts": dict(sorted(gates.items())),
        "target_gate_counts": dict(sorted(target_gate_counts.items())),
        "target_gate_counts_by_role": {
            role: dict(sorted(counts.items()))
            for role, counts in sorted(target_gate_counts_by_role.items())
        },
        "target_gate_counts_by_fact_mask": {
            fact_mask: dict(sorted(counts.items()))
            for fact_mask, counts in sorted(target_gate_counts_by_fact_mask.items())
        },
        "scorer_parity_mismatch_samples": [
            {
                key: row.get(key) for key in (
                    "frame", "cell", "sid_cur", "g_prev", "g_star",
                    "replay_gid", "replay_claimed", "replay_selected_lane",
                    "fact_mask", "ramp_idx", "density_idx", "selected_role",
                    "profile_namespace", "material_key", "pool_revision",
                    "bundle_id", "bundle_state", "descriptor_kind",
                    "descriptor_version",
                )
            }
            for row in target_rows
            if row.get("gate") == "SCORER_PARITY_MISMATCH"
        ][:20],
        # A duplicate SurfaceId cannot become a trajectory sample. Keep a
        # bounded witness of the row the recorder retained so collision repair
        # can be routed without loading a production-sized corpus into memory.
        # The companion cell is intentionally not inferred: the v2 recorder
        # must emit it before tooling can name it authoritatively.
        "target_collision_samples": [
            {
                key: row.get(key) for key in (
                    "frame", "cell", "sid_cur", "g_prev", "g_star",
                    "fact_mask", "ramp_idx", "density_idx", "selected_role",
                    "profile_namespace", "material_key", "pool_revision",
                    "bundle_id", "bundle_state", "descriptor_kind",
                    "descriptor_version",
                )
            }
            for row in target_rows
            if row.get("gate") == "COLLISION"
        ][:20],
        "identity_denominator": denominator,
        "mesh_identity_ready": mesh_identity_ready,
        "surface_identity_ready": surface_identity_ready,
        "target_identity_ready": target_identity_ready,
        "identity_ready_by_scorer": {
            f"{kind}:{version}:{domain}": ready
            for (kind, version, domain), ready in sorted(
                identity_ready_by_scorer.items())
        },
        "calibration_ready": calibration_ready,
        "complete_product_calibration": (
            required_scorers == {spec.key for spec in PRODUCT_TEMPORAL_SCORERS}
        ),
        "required_product_scorers": [
            {
                "scorer_kind": spec.scorer_kind,
                "scorer_version": spec.scorer_version,
                "scorer_id": spec.scorer_id,
                "score_domain": spec.score_domain,
            }
            for spec in target_specs
        ],
        "missing_product_scorers": [
            {
                "scorer_kind": spec.scorer_kind,
                "scorer_version": spec.scorer_version,
                "scorer_id": spec.scorer_id,
                "score_domain": spec.score_domain,
            }
            for spec in target_specs if spec.key not in observed_scorers
        ],
        "unexpected_scorers": [
            {"scorer_kind": kind, "scorer_version": version, "score_domain": domain}
            for kind, version, domain in sorted(all_observed_scorers - required_scorers)
        ],
        "calibration_candidates": len(candidates),
        "candidate_raw_flips": len(target_flips),
        "all_candidate_raw_flips": len(flips),
        "candidate_replay_failures": replay_failures,
        "flip_margin_quantiles": {
            "min": min(margins) if margins else None,
            "p50": quantile(margins, 0.50),
            "p90": quantile(margins, 0.90),
            "p95": quantile(margins, 0.95),
            "p99": quantile(margins, 0.99),
            "max": max(margins) if margins else None,
        },
        "flip_margin_quantiles_by_role": {
            role: {
                "count": len(values), "p50": quantile(values, 0.50),
                "p90": quantile(values, 0.90), "p95": quantile(values, 0.95),
                "p99": quantile(values, 0.99), "max": max(values),
            }
            for role, values in sorted(by_role.items())
        },
        "flip_margin_quantiles_by_scorer": {
            f"{kind}:{version}:{domain}": {
                "count": len(values), "p50": quantile(values, 0.50),
                "p90": quantile(values, 0.90), "p95": quantile(values, 0.95),
                "p99": quantile(values, 0.99), "max": max(values),
            }
            for (kind, version, domain), values in sorted(by_scorer.items())
        },
        "provisional_margin_rows": margin_rows,
        "suppressed_real_change": 0 if bad_replay_rows == 0 else None,
        "decision_blockers": [
            *([] if calibration_ready else [
                "Every explicitly targeted scorer requires positive, parity-valid flip samples "
                "in its own typed score domain, contiguous frames, and collision-free identity "
                "evidence before a row can be emitted; legacy binary32 Harri samples cannot "
                "calibrate the integer v2 domain."]),
            "The provisional table carries no perceptual claim; D5 remains the "
            "held-out perceptual gate.",
        ],
    }
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if args.margin_table_out:
        if not calibration_ready:
            raise SystemExit("FAIL-CLOSED: calibration inputs are incomplete")
        table_corpus = list(corpus_records)
        table_rows = list(margin_rows)
        if args.base_margin_table:
            base = load_margin_table(Path(args.base_margin_table))
            target_kinds = {spec.scorer_kind for spec in target_specs}
            table_rows = [
                row for row in base["rows"]
                if int(row["scorer_kind"]) not in target_kinds
            ] + table_rows
            known_corpus = {(row["path"], row["sha256"]) for row in table_corpus}
            for record in base["corpus"]:
                key = (record["path"], record["sha256"])
                if key not in known_corpus:
                    table_corpus.append(record)
                    known_corpus.add(key)
        table_rows.sort(key=lambda row: (
            int(row["scorer_kind"]), int(row["scorer_version"]),
            str(row["score_domain"])))
        all_calibrated = all(
            row.get("policy_basis") == "calibrated_nearest_rank_p99.v1"
            for row in table_rows
        )
        table = {
            "schema": "fl4512.temporal_margin_table.v1",
            "policy_set_id": (
                "calibrated-policy-set-v1" if all_calibrated
                else "mixed-authored-calibrated-v1"
            ),
            "method": (
                "nearest_rank_p99.v1" if all_calibrated
                else "mixed_policy_basis.v1"
            ),
            "perceptual_claim": False,
            "suppressed_real_change": 0,
            "corpus": table_corpus,
            "corpus_hash": canonical_sha256(table_corpus),
            "rows": table_rows,
        }
        table["table_sha256"] = canonical_sha256(table)
        Path(args.margin_table_out).write_text(
            json.dumps(table, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
