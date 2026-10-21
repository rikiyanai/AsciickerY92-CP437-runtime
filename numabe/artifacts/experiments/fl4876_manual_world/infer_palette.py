"""infer_palette.py [--write] -- derives the Numabe TYPE PALETTE for buildings that no photo shows lit,
from local evidence only (operator 2026-10-13: "triangulate between multiple sources ... pictures based on
typical palette"). Values written here are INFERRED, never manual, and live in their own owner file so they
cannot be mistaken for hand-sampled colours.

Tiers (first available wins, per building):
  1. hand-sampled lit wall in authored_building_appearance.json  -> not touched here (manual owner).
  1b. hand_judged: the building carries a wall_judgement (hand target colour from its street description,
     judge_described_walls.py, or from the per-building card review, merge_wall_reviews.py). The wall is
     the hand-sampled lit Numabe wall nearest (CIE76) to that target, so every colour stays a real
     photographed Numabe colour; the basis names target, family and source building.
  2. type palette: the building's PLATEAU usage class (assess/plateau_attrs.json); the wall is one of the
     lit walls hand-sampled on Numabe buildings of the SAME class, chosen deterministically from the
     building id (so neighbours vary like the real street). Basis names the source building.
  3. district palette: the same choice from all hand-sampled lit walls when the class has < 4 samples or
     the building has no PLATEAU match.
Roofs are not inferred here (709/715 have hand-reviewed aerial roof points; the TMG point-cloud RGB is
orthophoto colour, equal on walls and roofs, so it is not a wall source -- see pc_colour_probe.py).
Writes assets/source_bundles/numabe_connected_neighborhood_scope_20260915/inferred_building_palette.json."""
import hashlib, json, math, sys
from collections import defaultdict
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "palette_proposal"))
from lab import rgb_to_lab, lch_to_rgb  # noqa: E402


def lch_to_lab(L, C, h):
    return L, C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
ROOT = HERE.parents[2]
B = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
own = json.loads((B / "authored_building_appearance.json").read_text())["buildings"]
attrs = json.loads((HERE / "assess" / "plateau_attrs.json").read_text())
canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_46/canonical.world_ir.json").read_text())
ids = [f["feature_id"] for f in canon["canonical_features"] if f.get("semantic_class") == "building_footprint"]
cls = lambda k: (attrs.get(k) or {}).get("usage_label") or "unmatched"
samples = defaultdict(list)
for k, v in own.items():
    if v.get("wall_rgb"):
        samples[cls(k)].append((k, v["wall_rgb"], v.get("trim_rgb")))
district = sorted(s for lst in samples.values() for s in lst)
out, n = {}, defaultdict(int)
for k in ids:
    if (own.get(k) or {}).get("wall_rgb"):
        continue
    c = cls(k)
    j = (own.get(k) or {}).get("wall_judgement")
    if j:
        # nearest real lit sample to the hand target (CIE76 in CIELAB)
        tgt = lch_to_lab(*j["target_lch"])
        # within-family variation: the hand family fixes the target; real streets vary, so pick (by id hash)
        # among the up-to-5 lit samples nearest the target that lie within dE 6 of it (else the nearest one).
        ranked = sorted(district, key=lambda s: math.dist(rgb_to_lab(s[1]), tgt))
        near = [s for s in ranked[:5] if math.dist(rgb_to_lab(s[1]), tgt) <= 6.0] or ranked[:1]
        hj = int.from_bytes(hashlib.sha256(k.encode()).digest()[:4], "big")
        src, wall, trim = near[hj % len(near)]
        if math.dist(rgb_to_lab(wall), tgt) > 10.0:
            # no photographed Numabe wall within dE 10 (e.g. green scaffold mesh): the hand target itself
            wall, trim, src = list(lch_to_rgb(*j["target_lch"])), None, "hand target (no lit sample within dE 10)"
        out[k] = {"wall_rgb": wall, "trim_rgb": trim, "tier": "hand_judged", "usage_class": c,
                  "source_building": src, "family": j["family"], "target_lch": j["target_lch"],
                  "dE76": round(math.dist(rgb_to_lab(wall), tgt), 1), "judgement_evidence": j.get("evidence"),
                  "basis": "hand judgement %s (%s): %s -> nearest lit Numabe sample %s"
                           % (j["family"], j.get("evidence"), j.get("basis"), src)}
        n["hand_judged"] += 1
        continue
    pool = sorted(samples.get(c, []))
    tier = "type_palette"
    if len(pool) < 4:
        pool, tier = district, "district_palette"
    h = int.from_bytes(hashlib.sha256(k.encode()).digest()[:4], "big")
    src, wall, trim = pool[h % len(pool)]
    out[k] = {"wall_rgb": wall, "trim_rgb": trim, "tier": tier, "usage_class": c,
              "source_building": src, "pool_size": len(pool),
              "basis": "inferred: lit wall hand-sampled on %s (same %s class, %d samples), chosen by id hash"
                       % (src, c if tier == "type_palette" else "district", len(pool))}
    n[tier] += 1
print("buildings", len(ids), "| hand-sampled", sum(1 for k in ids if (own.get(k) or {}).get("wall_rgb")),
      "| inferred", dict(n), "| class samples", {c: len(v) for c, v in sorted(samples.items())})
if "--write" in sys.argv:
    (B / "inferred_building_palette.json").write_text(json.dumps({
        "schema": "y92.fl4876.inferred_building_palette.v1",
        "owner": "artifacts/experiments/fl4876_manual_world/infer_palette.py (derived; never manual)",
        "method": __doc__, "buildings": out}, ensure_ascii=False, indent=1) + "\n")
    print("written", B / "inferred_building_palette.json")
