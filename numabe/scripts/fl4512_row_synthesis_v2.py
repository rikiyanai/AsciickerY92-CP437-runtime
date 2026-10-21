#!/usr/bin/env python3
"""Build coverage preserving tonal rows for the FL 4512 vocabulary study.

The rejected v1 rule selected the 32 glyphs nearest one row centroid. A denser
supply therefore concentrated the row around its mean and discarded the spread
present in the authored vocabulary. This v2 rule keeps the frozen nested supply
order and replaces only the bounded row rule.

For each authored descriptor target, candidates in S(B) are ordered by the
production fixed point distance, then by lower GlyphId. The row is filled in
balanced rounds across those target lists. The first round therefore places the
nearest available stand in for every distinct authored descriptor before any
target receives a second candidate. Because budgets are nested, each authored
target's nearest distance cannot increase with vocabulary size.

Run:
    python3 scripts/fl4512_row_synthesis_v2.py

Output:
    assets/glyphs/generated/fl4512.row_synthesis.v2.json
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "assets/glyphs/generated"
CENSUS = GENERATED / "fl4512.descriptor_census.v1.jsonl"
ORDER = GENERATED / "fl4512.reduction_order.v1.jsonl"
ORDER_MANIFEST = GENERATED / "fl4512.reduction_order.v1.manifest.json"
POOL = GENERATED / "fl4512.compiled_pool.v1.json"
V1 = GENERATED / "fl4512.row_synthesis.v1.json"
OUT = GENERATED / "fl4512.row_synthesis.v2.json"

BUDGETS = [1188, 1191, 2048, 4096, 8192, 16384, 32768, 65536, 110604]
ROW_CAPACITY = 32


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def q_f(value: float) -> int:
    """SS C.10 q_F with clamp and round half up."""
    if not value > 0.0:
        return 0
    if value > 1.0:
        value = 1.0
    return int(value * 256.0 + 0.5)


def main() -> int:
    manifest = json.loads(ORDER_MANIFEST.read_text())
    order_sha = sha256_file(ORDER)
    if order_sha != manifest["order_sha256"]:
        raise SystemExit("reduction order does not match its manifest")

    rank_by_gid: dict[int, int] = {}
    for line in ORDER.read_text().splitlines():
        record = json.loads(line)
        rank_by_gid[int(record["glyph_id"])] = int(record["rank"])

    gids: list[int] = []
    q_rows: list[list[int]] = []
    for line in CENSUS.read_text().splitlines():
        record = json.loads(line)
        gids.append(int(record["glyph_id"]))
        q_rows.append(record["shape6_norm_q"])
    gid_arr = np.asarray(gids, dtype=np.int64)
    q_arr = np.asarray(q_rows, dtype=np.int64)
    rank_arr = np.asarray([rank_by_gid[g] for g in gids], dtype=np.int64)
    census_index = {gid: index for index, gid in enumerate(gids)}

    pool = json.loads(POOL.read_text())
    tonal_rows = [
        row
        for row in pool["rows"]
        if row["lane_name"].startswith(("density", "ramp"))
        and row["candidate_count"] > 0
    ]

    # Candidate order is expensive to derive but authored descriptors repeat
    # across rows. Cache one global order and its per budget prefixes for each
    # distinct quantized target.
    target_prefix_cache: dict[tuple[int, ...], dict[int, list[int]]] = {}

    def prefixes_for(target: tuple[int, ...]) -> dict[int, list[int]]:
        cached = target_prefix_cache.get(target)
        if cached is not None:
            return cached
        target_arr = np.asarray(target, dtype=np.int64)
        distance = ((q_arr - target_arr) ** 2).sum(axis=1)
        global_order = np.lexsort((gid_arr, distance))
        prefixes: dict[int, list[int]] = {}
        for budget in BUDGETS:
            inside = rank_arr[global_order] < budget
            positions = global_order[inside][:ROW_CAPACITY]
            prefixes[budget] = [int(gid_arr[position]) for position in positions]
        target_prefix_cache[target] = prefixes
        return prefixes

    rows: list[dict] = []
    summary_mean: dict[int, list[float]] = {budget: [] for budget in BUDGETS}
    summary_max: dict[int, list[float]] = {budget: [] for budget in BUDGETS}
    summary_span: dict[int, list[float]] = {budget: [] for budget in BUDGETS}

    for row in tonal_rows:
        descriptors = {int(item["glyph_id"]): item for item in row.get("candidate_descriptors", [])}
        members = [int(gid) for gid in row["candidate_glyph_ids"] if int(gid) in descriptors]
        if not members:
            continue
        member_q = np.asarray(
            [[q_f(value) for value in descriptors[gid]["global6_norm"]] for gid in members],
            dtype=np.int64,
        )

        # Duplicate descriptors need one coverage target. The lowest authored
        # GlyphId names a duplicate group, while target ordering is descriptor
        # tuple then GlyphId so pool JSON order cannot affect synthesis.
        target_owner: dict[tuple[int, ...], int] = {}
        for gid, descriptor in zip(members, member_q):
            key = tuple(int(value) for value in descriptor)
            target_owner[key] = min(gid, target_owner.get(key, gid))
        targets = sorted(target_owner, key=lambda key: (key, target_owner[key]))
        target_prefixes = {target: prefixes_for(target) for target in targets}

        per_budget: dict[str, dict] = {}
        previous_mean: float | None = None
        previous_max: int | None = None
        for budget in BUDGETS:
            chosen: list[int] = []
            chosen_set: set[int] = set()
            # Balanced round robin. Depth zero admits every target's nearest
            # candidate. Later depths add equally deep neighborhoods until the
            # runtime row capacity is reached.
            for depth in range(ROW_CAPACITY):
                for target in targets:
                    candidate = target_prefixes[target][budget][depth]
                    if candidate in chosen_set:
                        continue
                    chosen.append(candidate)
                    chosen_set.add(candidate)
                    if len(chosen) == ROW_CAPACITY:
                        break
                if len(chosen) == ROW_CAPACITY:
                    break
            if not chosen:
                raise SystemExit(f"empty synthesized row at budget {budget}")

            chosen_positions = np.asarray([census_index[gid] for gid in chosen], dtype=np.int64)
            chosen_q = q_arr[chosen_positions]
            pair_distance = ((member_q[:, None, :] - chosen_q[None, :, :]) ** 2).sum(axis=2)
            nearest = pair_distance.min(axis=1)
            mean_nearest = float(nearest.mean())
            max_nearest = int(nearest.max())
            if previous_mean is not None and mean_nearest > previous_mean:
                raise SystemExit(
                    f"coverage regression in {row['material_key']} {row['lane_name']} "
                    f"at budget {budget}: {mean_nearest} > {previous_mean}"
                )
            if previous_max is not None and max_nearest > previous_max:
                raise SystemExit(
                    f"worst coverage regression in {row['material_key']} {row['lane_name']} "
                    f"at budget {budget}: {max_nearest} > {previous_max}"
                )
            previous_mean = mean_nearest
            previous_max = max_nearest

            axis_span_sum = int((chosen_q.max(axis=0) - chosen_q.min(axis=0)).sum())
            worst_at = int(np.argmax(nearest))
            per_budget[str(budget)] = {
                "synth_glyph_ids": chosen,
                "member_nearest_dist_mean": round(mean_nearest, 1),
                "member_nearest_dist_max": max_nearest,
                "worst_covered_member_gid": members[worst_at],
                "selected_axis_span_sum": axis_span_sum,
            }
            summary_mean[budget].append(mean_nearest)
            summary_max[budget].append(float(max_nearest))
            summary_span[budget].append(float(axis_span_sum))

        rows.append(
            {
                "profile_namespace": row["profile_namespace"],
                "material_key": row["material_key"],
                "lane_name": row["lane_name"],
                "authored_count": len(members),
                "distinct_authored_descriptor_count": len(targets),
                "authored_glyph_ids": members,
                "authored_dropped_no_descriptor": len(row["candidate_glyph_ids"]) - len(members),
                "budgets": per_budget,
            }
        )

    summary = {
        str(budget): {
            "mean_of_mean_nearest_dist": round(float(np.mean(summary_mean[budget])), 1),
            "mean_of_max_nearest_dist": round(float(np.mean(summary_max[budget])), 1),
            "mean_selected_axis_span_sum": round(float(np.mean(summary_span[budget])), 1),
        }
        for budget in BUDGETS
    }
    artifact = {
        "schema": "fl4512.row_synthesis.v2",
        "rule": "coverage targets with balanced nearest neighbor rounds, fixed point distance, lower GlyphId ties",
        "row_capacity": ROW_CAPACITY,
        "budgets": BUDGETS,
        "inputs": {
            "census_sha256": manifest["inputs"]["census_sha256"],
            "order_sha256": order_sha,
            "pool_artifact_sha256": pool["artifact_sha256"],
            "rejected_v1_sha256": sha256_file(V1),
        },
        "row_count": len(rows),
        "distinct_target_descriptor_count": len(target_prefix_cache),
        "all_rows_monotonic": True,
        "summary": summary,
        "rows": rows,
    }
    OUT.write_text(json.dumps(artifact, indent=1, sort_keys=True) + "\n")
    print(f"rows evaluated: {len(rows)}")
    print(f"distinct authored targets: {len(target_prefix_cache)}")
    for budget in BUDGETS:
        values = summary[str(budget)]
        print(
            f"  B={budget:>6}  mean_nearest={values['mean_of_mean_nearest_dist']:>8.1f}"
            f"  mean_worst={values['mean_of_max_nearest_dist']:>8.1f}"
            f"  mean_span={values['mean_selected_axis_span_sum']:>7.1f}"
        )
    print(f"artifact: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
