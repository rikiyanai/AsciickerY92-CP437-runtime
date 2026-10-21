#!/usr/bin/env python3
# =============================================================
# fl4512_row_synthesis.py
# =============================================================
# FL-4512 universal research target, step 6: synthesized-vs-authored
# tonal rows under the frozen reduction rule (fl4512.reduction.v1).
#
# QUESTION THIS ANSWERS. The paper's RQ-U1 ladder varies the glyph
# supply S(B) (the first B glyphs of the frozen priority order) and
# asks how rendering changes. For tonal lanes the frozen row rule
# (R6) says: under budget B, a lane's row is the at-most-32 glyphs
# of S(B) closest (integer score, uniform weights, ties to lower
# GlyphId) to the AUTHORED row's centroid in quantized shape6
# space. Before any live run, this script measures OFFLINE how the
# synthesized rows relate to the human-authored rows: how much of
# the authored vocabulary survives at each budget, and how quickly
# synthesis converges to a stable row as the budget grows.
#
# Inputs (all committed, hash-pinned artifacts):
#   assets/glyphs/generated/fl4512.descriptor_census.v1.jsonl
#     - every admitted glyph's quantized six-region descriptor
#       (shape6_norm_q, the SS-C.10 q_F integers in [0,256])
#   assets/glyphs/generated/fl4512.reduction_order.v1.jsonl
#     - the frozen total priority order (rank -> glyph_id)
#   assets/glyphs/generated/fl4512.compiled_pool.v1.json
#     - the 64 authored tonal rows (8 materials x density[0..3]
#       + ramp[0..3]) the production selector actually scans
#
# SCOPE FACT DISCOVERED BY THIS STEP: the authored tonal rows draw
# from the LEGACY ADDITIVE pack (GlyphIds 512..671), which is not
# part of the universal census (material.morphology.v2 + unicode16).
# The universal supply REPLACES that vocabulary, so set overlap
# between authored and synthesized rows is zero by construction and
# meaningless. The honest comparison is DESCRIPTOR-SPACE closeness:
# how well can rows drawn from the universal supply reproduce the
# authored rows' ink distribution?
#
# METHOD, frozen for this artifact (fl4512.row_synthesis.v1):
#   member_q      = q_F(global6_norm) of each authored member, taken
#                   from the pool's own candidate descriptors (the
#                   additive pack has no census rows), with
#                   q_F(x) = floor(clamp(x,0,1)*256 + 0.5)  (SS-C.10).
#   centroid_q[k] = round(mean over authored members of member_q[k])
#                   -- "centroid in quantized space" (R6).
#   score(g)      = sum over k of (centroid_q[k] - g_q[k])^2
#                   -- the runtime scorer's uniform-weight integer
#                   metric; the constant weight factor q_W(1.0)=16
#                   scales every candidate identically and is
#                   dropped, exactly as in the reduction rule.
#   synth row(B)  = the min(32, |S(B)|) glyphs of S(B) with the
#                   smallest score, equal scores going to the lower
#                   GlyphId (the runtime tie rule).
#   fidelity(B)   = for each authored member, the integer distance to
#                   its NEAREST synthesized-row glyph; reported as
#                   mean/max over members ("can the synthesized row
#                   stand in for each authored glyph, and how far is
#                   the worst stand-in").
#
# Run:  python3 scripts/fl4512_row_synthesis.py
# Out:  assets/glyphs/generated/fl4512.row_synthesis.v1.json
# =============================================================
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
OUT = GENERATED / "fl4512.row_synthesis.v1.json"

# Budgets to evaluate. 1188 = the pure material seed (production
# vocabulary exactly); 1191 = seed + its exact-duplicate riders; the
# rest double up to the full admitted supply.
BUDGETS = [1188, 1191, 2048, 4096, 8192, 16384, 32768, 65536, 110604]

ROW_CAPACITY = 32  # runtime rows hold at most 32 candidates


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    # ---- load the frozen order and verify it against its manifest ----
    manifest = json.loads(ORDER_MANIFEST.read_text())
    order_sha = sha256_file(ORDER)
    if order_sha != manifest["order_sha256"]:
        raise SystemExit("reduction order does not match its manifest - regenerate step 4 first")

    # gid -> rank (position in the frozen priority order). Rank < B
    # means the glyph is inside supply S(B).
    rank_by_gid: dict[int, int] = {}
    for line in ORDER.read_text().splitlines():
        record = json.loads(line)
        rank_by_gid[int(record["glyph_id"])] = int(record["rank"])

    # ---- load the census quantized descriptors --------------------
    gids: list[int] = []
    q_rows: list[list[int]] = []
    for line in CENSUS.read_text().splitlines():
        record = json.loads(line)
        gids.append(int(record["glyph_id"]))
        q_rows.append(record["shape6_norm_q"])
    gid_arr = np.array(gids, dtype=np.int64)
    q_arr = np.array(q_rows, dtype=np.int64)          # (N, 6) integers in [0,256]
    rank_arr = np.array([rank_by_gid[g] for g in gids], dtype=np.int64)
    census_index = {g: i for i, g in enumerate(gids)}

    # ---- load the authored tonal rows ------------------------------
    pool = json.loads(POOL.read_text())
    tonal_rows = [
        row for row in pool["rows"]
        if row["lane_name"].startswith(("density", "ramp")) and row["candidate_count"] > 0
    ]

    results = []
    # Per-budget aggregates across all rows (for the summary table).
    agg_kept: dict[int, list[float]] = {b: [] for b in BUDGETS}
    agg_jaccard: dict[int, list[float]] = {b: [] for b in BUDGETS}

    def q_f(x: float) -> int:
        # SS-C.10 q_F: clamp to [0,1], scale by 256, round half-up.
        if not (x > 0.0):
            return 0
        if x > 1.0:
            x = 1.0
        return int(x * 256.0 + 0.5)

    for row in tonal_rows:
        # Authored members' quantized descriptors come from the pool's
        # own candidate descriptor records (see the SCOPE FACT above:
        # the additive-pack members have no census rows). Members in
        # the census would use identical q_F values; the source is the
        # same measurement chain either way.
        descriptors = {d["glyph_id"]: d for d in row.get("candidate_descriptors", [])}
        members = [g for g in row["candidate_glyph_ids"] if g in descriptors]
        dropped = len(row["candidate_glyph_ids"]) - len(members)
        if not members:
            continue
        member_q = np.array(
            [[q_f(v) for v in descriptors[g]["global6_norm"]] for g in members],
            dtype=np.int64)
        # R6 centroid: component-wise mean of the members' quantized
        # descriptors, rounded back to integers so the score stays exact.
        centroid_q = np.rint(member_q.mean(axis=0)).astype(np.int64)
        # Integer distance from the centroid to EVERY admitted glyph,
        # computed once; each budget then just masks by rank.
        dist = ((q_arr - centroid_q) ** 2).sum(axis=1)
        # One global ordering by (distance, gid): for any budget, the
        # synthesized row is the first 32 entries of this ordering whose
        # rank is inside the budget. Sorting once avoids 9 re-sorts.
        global_order = np.lexsort((gid_arr, dist))

        per_budget = {}
        for budget in BUDGETS:
            inside = rank_arr[global_order] < budget
            chosen_positions = global_order[inside][:ROW_CAPACITY]
            synth = [int(gid_arr[i]) for i in chosen_positions]
            synth_q = q_arr[chosen_positions]           # (<=32, 6)
            # Fidelity: for each authored member, the integer distance
            # to its nearest stand-in inside the synthesized row. A
            # mean near zero means the universal supply can reproduce
            # the authored look; the max names the worst-covered glyph.
            pair_d = ((member_q[:, None, :] - synth_q[None, :, :]) ** 2).sum(axis=2)
            nearest = pair_d.min(axis=1)                 # per authored member
            worst_at = int(np.argmax(nearest))
            per_budget[str(budget)] = {
                "synth_glyph_ids": synth,
                "member_nearest_dist_mean": round(float(nearest.mean()), 1),
                "member_nearest_dist_max": int(nearest.max()),
                "worst_covered_member_gid": int(members[worst_at]),
            }
            agg_kept[budget].append(float(nearest.mean()))
            agg_jaccard[budget].append(float(nearest.max()))

        results.append({
            "profile_namespace": row["profile_namespace"],
            "material_key": row["material_key"],
            "lane_name": row["lane_name"],
            "authored_count": len(members),
            "authored_glyph_ids": members,
            "authored_dropped_no_descriptor": dropped,
            "centroid_q": [int(v) for v in centroid_q],
            "budgets": per_budget,
        })

    # Aggregates across the 64 rows: the mean of per-row mean nearest
    # distances (how close synthesis gets on average) and the mean of
    # per-row WORST distances (how bad the worst stand-in typically is).
    summary = {
        str(b): {
            "mean_of_mean_nearest_dist": round(float(np.mean(agg_kept[b])), 1),
            "mean_of_max_nearest_dist": round(float(np.mean(agg_jaccard[b])), 1),
        }
        for b in BUDGETS
    }

    artifact = {
        "schema": "fl4512.row_synthesis.v1",
        "rule": "R6 of fl4512.reduction.v1 (centroid + uniform-weight integer score, ties lower GlyphId)",
        "row_capacity": ROW_CAPACITY,
        "budgets": BUDGETS,
        "inputs": {
            "census_sha256": manifest["inputs"]["census_sha256"],
            "order_sha256": order_sha,
            "pool_artifact_sha256": pool["artifact_sha256"],
        },
        "row_count": len(results),
        "summary": summary,
        "rows": results,
    }
    OUT.write_text(json.dumps(artifact, indent=1, sort_keys=True) + "\n")
    print(f"rows evaluated: {len(results)}")
    for budget in BUDGETS:
        s = summary[str(budget)]
        print(f"  B={budget:>6}  mean_nearest={s['mean_of_mean_nearest_dist']:>8.1f}  mean_worst={s['mean_of_max_nearest_dist']:>8.1f}")
    print(f"artifact: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
