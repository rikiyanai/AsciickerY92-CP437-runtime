#!/usr/bin/env python3
# =============================================================
# fl4512_reduction_order.py
# =============================================================
# FL-4512 universal research target, step 4: the FROZEN
# deterministic reduction rule.
#
# The RQ-U1 fidelity-vs-vocabulary ladder needs, for every budget
# B, one specific supply S(B) and one specific way each runtime
# row (at most 32 candidates) is formed from it. Freezing that
# rule BEFORE any fidelity run is what makes the ladder an
# experiment instead of a tuning loop. This file is the frozen
# rule: the prose below is the contract, the code is the
# reference implementation, and the emitted artifact is the
# hash-pinned priority order the ladder must consume unchanged.
#
# --- FROZEN RULE, fl4512.reduction.v1 -------------------------
#
# (R1) TOTAL PRIORITY ORDER. Every admitted glyph receives one
#      priority rank. S(B) = the first B glyphs of the order, so
#      supplies are NESTED by construction: B1 < B2 implies
#      S(B1) is a subset of S(B2).
#
# (R2) SEED. Ranks 0..1187 are the material.morphology.v2 pack in
#      ascending GlyphId order. These 1,188 glyphs are the current
#      production vocabulary — the training-derived starting
#      point — so every ladder budget at or above 1,188 contains
#      the production baseline exactly.
#
# (R3) GROWTH. After the seed, ranks are assigned by deterministic
#      farthest-point sampling in the quantized descriptor space:
#      repeatedly select the unranked class representative whose
#      squared L2 distance over the six shape6_norm_q components
#      to its NEAREST already-ranked glyph is LARGEST. This is the
#      runtime scorer's own metric (score_int with uniform unit
#      weights, constant factor dropped), so "far" in the order
#      means "far" to the selector.
#
# (R4) TIES. Every tie — equal farthest distance — resolves to the
#      LOWEST GlyphId, the runtime's own tie rule.
#
# (R5) CLASSES, NOT MEMBERS. Only class representatives from
#      fl4512.signature_classes.v1 are ranked by R3; the other
#      members of a class inherit a rank immediately after their
#      representative (ascending GlyphId), because at census
#      resolution the scorer cannot distinguish them and ranking
#      them independently would spend budget on duplicates.
#      SEED EXCEPTION: members of classes whose representative is
#      in the R2 seed follow the seed BLOCK (ranks 1188+), not
#      their representative in place — otherwise duplicate riders
#      would push seed glyphs past rank 1187 and break R2's
#      guarantee that S(1188) is exactly the production pack.
#
# (R6) ROW RULE. For a tonal lane (shape6-scored) with an authored
#      row, the row under budget B is the at-most-32 glyphs of
#      S(B) with the smallest score_int (uniform weights) to the
#      lane's authored-row centroid in quantized space, ties to
#      lower GlyphId. For u7-mask and primitive-field lanes the
#      authored row is FILTERED to S(B) with no refill — the
#      structural vocabularies are authored per topology and no
#      centroid is defined for them; an emptied row resolves
#      through the compiled absence policy, never a stale glyph.
#
# (R7) FREEZE. The rule text (R1-R7), this implementation, and the
#      emitted order hash are frozen together. Any change is a new
#      rule version and invalidates comparisons with runs made
#      under v1.
# --------------------------------------------------------------
#
# Run:  python3 scripts/fl4512_reduction_order.py
# Out:  assets/glyphs/generated/fl4512.reduction_order.v1.jsonl
#       assets/glyphs/generated/fl4512.reduction_order.v1.manifest.json
# =============================================================

import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "assets/glyphs/generated"
CENSUS = GENERATED / "fl4512.descriptor_census.v1.jsonl"
CLASSES = GENERATED / "fl4512.signature_classes.v1.json"
OUT_JSONL = GENERATED / "fl4512.reduction_order.v1.jsonl"
OUT_MANIFEST = GENERATED / "fl4512.reduction_order.v1.manifest.json"

MATERIAL_PACK = "material.morphology.v2"


def main() -> int:
    classes_doc = json.loads(CLASSES.read_text())
    census_sha = hashlib.sha256(CENSUS.read_bytes()).hexdigest()
    if census_sha != classes_doc["census_sha256"]:
        raise SystemExit("signature classes were built from a different census — regenerate step 3 first")

    # Census rows: gid -> (pack, shape6_norm_q). Sorted input keeps
    # every downstream argmax/first-occurrence deterministic.
    pack_by_gid: dict[int, str] = {}
    q_by_gid: dict[int, tuple] = {}
    for line in CENSUS.read_text().splitlines():
        r = json.loads(line)
        pack_by_gid[int(r["glyph_id"])] = r["pack"]
        q_by_gid[int(r["glyph_id"])] = tuple(r["shape6_norm_q"])

    # R2 seed: the whole material pack, ascending GlyphId.
    seed = sorted(g for g, p in pack_by_gid.items() if p == MATERIAL_PACK)

    # R5: rank representatives only; members follow their rep.
    rep_members: dict[int, list[int]] = {
        c["representative"]: c["members"] for c in classes_doc["classes"]}
    seed_set = set(seed)
    # Representatives still needing a rank via R3 growth. A class
    # whose representative is seeded is already covered; its
    # non-seed members will follow the representative (R5).
    reps = sorted(r for r in rep_members if r not in seed_set)

    rep_arr = np.array(reps, dtype=np.int64)
    rep_q = np.array([q_by_gid[g] for g in reps], dtype=np.int64)
    seed_q = np.array([q_by_gid[g] for g in seed], dtype=np.int64)

    # min_d2[i] = squared distance from representative i to its
    # nearest already-ranked glyph (R3's farthest-point criterion).
    min_d2 = np.full(len(reps), np.iinfo(np.int64).max, dtype=np.int64)
    for s in seed_q:
        d2 = ((rep_q - s) ** 2).sum(axis=1)
        np.minimum(min_d2, d2, out=min_d2)

    order: list[int] = []
    emitted: set[int] = set()
    def emit(gid: int) -> None:
        # Growth-phase emitter: a representative followed by its
        # class members (R5's normal in-place rider rule).
        if gid in emitted:
            return
        emitted.add(gid)
        order.append(gid)
        for member in rep_members.get(gid, []):
            if member not in emitted:
                emitted.add(member)
                order.append(member)

    # R2: the seed block is PURE — all 1,188 material glyphs occupy
    # ranks 0..1187 with no riders interleaved, so S(1188) equals
    # the production pack exactly.
    for gid in seed:
        if gid not in emitted:
            emitted.add(gid)
            order.append(gid)
    # R5 seed exception: exact-signature duplicates of seeded glyphs
    # follow the seed block immediately (ascending representative,
    # then ascending member GlyphId — both already sorted). They are
    # indistinguishable from their seeded representative at census
    # resolution, so placing them here spends no descriptive budget.
    for rep in seed:
        for member in rep_members.get(rep, []):
            if member not in emitted:
                emitted.add(member)
                order.append(member)

    alive = np.ones(len(reps), dtype=bool)
    # Seeded representatives are already emitted; drop them from growth.
    for i, gid in enumerate(reps):
        if gid in emitted:
            alive[i] = False
    total = len(pack_by_gid)
    while len(order) < total:
        # R3: farthest first. np.argmax returns the FIRST maximum and
        # reps is ascending-GlyphId, so ties resolve to the lowest
        # GlyphId (R4) with no extra code path.
        masked = np.where(alive, min_d2, -1)
        i = int(np.argmax(masked))
        if masked[i] < 0:
            raise SystemExit("growth exhausted before covering the supply — census/classes disagree")
        alive[i] = False
        emit(int(rep_arr[i]))
        d2 = ((rep_q - rep_q[i]) ** 2).sum(axis=1)
        np.minimum(min_d2, d2, out=min_d2)
        if len(order) % 10000 < len(rep_members.get(int(rep_arr[i]), [1])):
            print(f"  ranked {len(order)}/{total}", flush=True)

    body = "".join(
        json.dumps({"rank": rank, "glyph_id": gid}, separators=(",", ":")) + "\n"
        for rank, gid in enumerate(order))
    OUT_JSONL.write_text(body)
    order_sha = hashlib.sha256(body.encode()).hexdigest()

    manifest = {
        "schema": "fl4512.reduction_order.v1",
        "rule": "fl4512.reduction.v1 (R1-R7 frozen in scripts/fl4512_reduction_order.py)",
        "order_jsonl": OUT_JSONL.name,
        "order_sha256": order_sha,
        "glyph_count": len(order),
        "seed": {"pack": MATERIAL_PACK, "count": len(seed)},
        "growth_metric": "sum over k of (q_a[k]-q_b[k])^2 on shape6_norm_q (runtime score_int, uniform unit weights)",
        "tie_rule": "lowest GlyphId",
        "inputs": {
            "census_sha256": census_sha,
            "signature_classes_class_count": classes_doc["class_count"],
        },
    }
    OUT_MANIFEST.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print(f"order: {len(order)} glyphs, sha256 {order_sha[:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
