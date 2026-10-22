#!/usr/bin/env python3
"""Identity invariant gate (FL-4876 ENTITY-GATE-01). Generic; no place names.

Asks the question no other gate asked: does every identity-bearing thing in
the map belong to a resolved canonical entity, on that entity's host, with one
physical storefront owner per mapped business?

  G1 conservation   every registered observation is consumed by exactly one
                    entity or listed unresolved.
  G2 ownership      every place front, custom identity plane and sign sprite
                    names an entity whose state is "entity".
  G3 host           its parent building is the entity's resolved host.
  G4 uniqueness     a mapped business entity has at most one storefront
                    owner (place front), no observation-derived identity plane,
                    and no two fronts overlap one face's street slot.
  G5 presentation   a brand logo uses only a crop the resolver marked eligible
                    (explicitly bound observation that supports the host).

usage: check_identity_invariants.py RUN_ROOT   (exit 1 on any violation)
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

SCHEMA = "y92.identity_invariant_gate.v1"


def _load(p: Path, default):
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else default


def _instances(doc):
    return doc if isinstance(doc, list) else (doc or {}).get("instances", [])


def run(run_root: Path, entities_path: Path | None = None) -> dict:
    run_root = Path(run_root)
    ents_doc = _load(Path(entities_path) if entities_path else
                     run_root / "observation_registration" / "identity_entities.json", None)
    v = []
    if ents_doc is None:
        return {"schema": SCHEMA, "pass": False,
                "violations": [{"rule": "G1", "reason": "identity_entities.json absent"}]}
    ents = {e["entity_id"]: e for e in ents_doc["entities"]}
    by_place = {e["place_id"]: e for e in ents_doc["entities"] if e.get("kind") == "mapped_place"}
    reg = _load(run_root / "observation_registration" / "registered_instances.json", [])

    # G1
    o2e = ents_doc.get("observation_to_entity", {})
    unresolved = {u["instance_id"] for u in ents_doc.get("unresolved", [])}
    for inst in reg:
        iid = inst["instance_id"]
        if (iid in o2e) == (iid in unresolved):
            v.append({"rule": "G1", "subject": iid,
                      "reason": "observation consumed %s and unresolved %s" % (iid in o2e, iid in unresolved)})

    # G2-G4: place fronts (anchors are world units; widths metres)
    meta = _load(run_root / "terrain_metadata.json", {})
    sc = float((meta.get("projection") or meta).get("content_scale", 1.0))
    fronts = _instances(_load(run_root / "place_front_instances.json", []))
    owners = defaultdict(list)
    slots = defaultdict(list)
    for f in fronts:
        fid = f["feature_id"]
        e = by_place.get(fid)
        if e is None or e.get("state") != "entity":
            v.append({"rule": "G2", "subject": fid, "reason": "place front without a resolved entity",
                      "entity_state": e and e.get("state")})
            continue
        owners[e["entity_id"]].append(fid)
        host = (e.get("host") or {}).get("building_id")
        if f.get("parent_building_feature_id") != host:
            v.append({"rule": "G3", "subject": fid, "reason": "front parent is not the entity host",
                      "parent": f.get("parent_building_feature_id"), "host": host})
        slots[(f.get("parent_building_feature_id"), f.get("front_edge_index"))].append(f)
    for eid, fids in owners.items():
        if len(fids) > 1:
            v.append({"rule": "G4", "subject": eid, "reason": "more than one storefront owner", "fronts": fids})
    for key, fs in slots.items():
        for i in range(len(fs)):
            for j in range(i + 1, len(fs)):
                a, b = fs[i], fs[j]
                d = math.dist(a["front_anchor_world_xy"], b["front_anchor_world_xy"])
                if d / sc < ((a.get("fitted_width_m") or 0) + (b.get("fitted_width_m") or 0)) / 2.0:
                    v.append({"rule": "G4", "subject": "%s#f%s" % key,
                              "reason": "two storefronts overlap one face slot",
                              "fronts": [a["feature_id"], b["feature_id"]]})

    # G2-G4: custom identity planes
    plan = _load(run_root / "plan" / "population" / "population_plan.json", {"carriers": []})
    pid_entity = {p["placement_id"]: (p.get("evidence") or {}).get("entity_id")
                  for c in plan.get("carriers", []) for p in c.get("placements", [])}
    for r in _instances(_load(run_root / "lower_custom_instances.json", [])):
        pid = r.get("placement_id")
        eid = pid_entity.get(pid) or pid_entity.get("obs-" + str(pid))
        e = ents.get(eid)
        if e is None or e.get("state") != "entity" or e.get("kind") != "observed_place":
            v.append({"rule": "G2" if e is None else "G4", "subject": pid,
                      "reason": "identity plane without a resolved observed-place entity",
                      "entity": eid, "entity_state": e and e.get("state"), "entity_kind": e and e.get("kind")})
            continue
        if r.get("parent") != (e.get("host") or {}).get("building_id"):
            v.append({"rule": "G3", "subject": pid, "reason": "plane parent is not the entity host",
                      "parent": r.get("parent"), "host": e["host"]["building_id"]})

    # G2/G5: sign sprites
    for s in _load(run_root / "sign_sprites_receipt.json", {}).get("signs", []):
        if s.get("kind") == "brand_front_logo":
            fid = str(s["placement_id"]).split("place_front:", 1)[-1]
            e = by_place.get(fid)
            if e is None or e.get("state") != "entity":
                v.append({"rule": "G2", "subject": s["placement_id"], "reason": "logo without entity"})
            elif s.get("card_id"):
                pass  # CARD-FIX-01: a hand-authored card uses no crop, so G5 (crop eligibility) has nothing to test
            elif s.get("annotation_id") not in set(e.get("crop_eligible_annotations") or []):
                v.append({"rule": "G5", "subject": s["placement_id"],
                          "reason": "logo crop not eligible for this entity", "annotation": s.get("annotation_id")})
        else:
            eid = pid_entity.get(s.get("placement_id")) or pid_entity.get("obs-" + str(s.get("placement_id")))
            e = ents.get(eid)
            if e is None or e.get("state") != "entity":
                v.append({"rule": "G2", "subject": s.get("placement_id"),
                          "reason": "sign sprite without a resolved entity"})

    by_rule = defaultdict(int)
    for x in v:
        by_rule[x["rule"]] += 1
    return {"schema": SCHEMA, "pass": not v, "violations": v, "counts": dict(by_rule),
            "checked": {"observations": len(reg), "place_fronts": len(fronts),
                        "entities": len(ents)}}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(argv[0])
    ent = Path(argv[1]) if len(argv) > 1 else None
    doc = run(root, ent)
    if ent is None:  # an audit with an external entity file writes nothing into the run
        (root / "identity_invariant_gate.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print("violations by reason:", dict(Counter(x["reason"] for x in doc["violations"])))
    print("identity invariant gate:", "PASS" if doc["pass"] else "FAIL", doc.get("counts"))
    for x in doc["violations"][:20]:
        print("  ", json.dumps(x, ensure_ascii=False)[:240])
    return 0 if doc["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
