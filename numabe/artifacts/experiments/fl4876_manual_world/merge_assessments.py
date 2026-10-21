"""merge_assessments.py [--write] -- transcribe the hand assessments into the owner
assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_building_appearance.json.

It decides nothing new: per building it keeps every hand record (from the owner itself and from
assess/group_*.json); where only one record has a lit colour, that colour is used; where several lit
records disagree (any channel differing by > 45), the building is listed as CONFLICT for the person
to decide (its wall colour is left null until then; owner records already in the file win). Roof colours
come from the hand-reviewed aerial pass (aerial/roofs_reviewed.json, accepted/moved points only).
Without --write it only prints the summary and the conflicts."""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OWNER = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_building_appearance.json"
doc = json.loads(OWNER.read_text())
owner_b = doc.get("buildings", {})
groups = sorted((HERE / "assess").glob("group_*.json"))
recs = {}
pan = dict(doc.get("panoramas", {}))
# Hand decision (main session, 2026-10-12) on the street pass 2 records: a wall colour is adopted only
# when it was sampled on a lit face (or in flat overcast light) without a colour cast. These records keep
# their description and storeys, but their measured wall/trim values are kept as evidence only.
WALL_NOT_ADOPTED = {
    "feature:building_footprint:way/1507398062": "sampled through a tinted rear-cab windscreen on the shaded NW wall; reads dark green, assessor saw cream",
    "feature:building_footprint:way/1507398029": "only hazy windscreen-glare frame, wall in shade",
    "feature:building_footprint:way/225367806": "face in sky-lit shade against strong backlight (cool and dark)",
    "feature:building_footprint:way/225367799": "backlit, face in open shade (muted)",
    "feature:building_footprint:way/225563355": "lane face in open shade (dim)",
    "feature:building_footprint:way/225562523": "street face in shade against sun flare; assessor notes true colour is lighter",
    "feature:building_footprint:way/1507403795": "facade in shade against low sun; blue-grey cast, paint likely white",
    "feature:building_footprint:way/1507403778": "overexposed, values clipped at white",
    "feature:building_footprint:way/1507403697": "face in shade with a blue cast",
    "feature:building_footprint:way/1507403612": "shaded side of the lane, cool cast; hue uncertain per assessor",
}
CAST = "greenish panorama cast at 1 m from the wall"
for g in groups:
    d = json.loads(g.read_text())
    for cid, p in (d.get("panoramas") or {}).items():
        pan.setdefault(cid, dict(p, assessed_by=g.stem))
    for fid, b in (d.get("buildings") or {}).items():
        b = dict(b, _src=g.stem)
        why = WALL_NOT_ADOPTED.get(fid) or (CAST if g.stem == "group_s2_01" and "ae7676de7832" in fid else None)
        if why and b.get("wall_rgb"):
            b["wall_not_adopted"] = {"measured_wall_rgb": b.pop("wall_rgb"),
                                     "measured_trim_rgb": b.pop("trim_rgb", None), "reason": why}
        recs.setdefault(fid, []).append(b)
roofs = json.loads((HERE / "aerial" / "roofs_reviewed.json").read_text())
out = {}
conflicts = []
for fid in sorted(set(recs) | set(owner_b) | set(roofs)):
    base = dict(owner_b.get(fid) or {})
    rs = recs.get(fid, [])
    lit = [r for r in rs if r.get("wall_rgb")]
    if not base.get("wall_rgb") and lit:
        if len(lit) == 1 or all(max(abs(a - b) for a, b in zip(lit[0]["wall_rgb"], r["wall_rgb"])) <= 45 for r in lit[1:]):
            best = max(lit, key=lambda r: len(sum([e.get("samples", []) for e in r.get("evidence", [])], [])))
            for k in ("what", "wall_rgb", "trim_rgb", "storeys", "ground_floor", "features"):
                if best.get(k) is not None and base.get(k) is None:
                    base[k] = best[k]
        else:
            conflicts.append((fid, [(r["_src"], r["wall_rgb"], r.get("what")) for r in lit]))
            base["wall_conflict"] = [{"source": r["_src"], "wall_rgb": r["wall_rgb"], "what": r.get("what")} for r in lit]
    for r in rs:
        for k in ("what", "storeys", "ground_floor", "features", "wall_not_adopted", "identity_doubt",
                  "no_wall_rgb_reason"):
            if base.get(k) is None and r.get(k) is not None:
                base[k] = r[k]
        if r.get("not_visible") and not base.get("what"):
            base["street_view"] = {"state": "not_visible", "checked": r.get("checked"),
                                   "reason": r.get("reason"), "assessed_by": r["_src"]}
    ev = list(base.get("evidence", []))
    for r in rs:
        for e in r.get("evidence", []) or []:
            ev.append(dict(e, assessed_by=r["_src"], lighting=r.get("lighting")))
    rv = roofs.get(fid)
    if rv and rv.get("roof_rgb") and base.get("roof_rgb") is None:
        base["roof_rgb"] = rv["roof_rgb"]
        ev.append({"source": "GSI seamless aerial photo (hand-reviewed roof point)", "block": rv["block"],
                   "point_px": rv["point_px"], "decision": rv["decision"], "rgb": rv["roof_rgb"]})
    elif rv:
        base.setdefault("roof_note", "aerial roof %s: %s" % (rv["decision"], rv.get("reason", "")))
    base["evidence"] = ev
    out[fid] = base
n_wall = sum(1 for b in out.values() if b.get("wall_rgb"))
n_roof = sum(1 for b in out.values() if b.get("roof_rgb"))
n_seen = sum(1 for f in out if recs.get(f) or (owner_b.get(f) or {}).get("evidence"))
print("buildings %d | street-assessed %d | wall colour %d | roof colour %d | conflicts %d | panoramas %d"
      % (len(out), n_seen, n_wall, n_roof, len(conflicts), len(pan)))
for fid, cs in conflicts:
    print("CONFLICT", fid, cs)
if "--write" in sys.argv:
    doc["buildings"] = out
    doc["panoramas"] = pan
    OWNER.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    print("written", OWNER)
