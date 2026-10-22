"""derived_fix_audit.py -- listing aid only (decides nothing): for every authored spatial-coherence
decision that targets a derived observation placement (obs-inst-NNNN / linear_obs-inst-...), print the
placement as build_reg_43 emitted it (module, E/N, instance -> annotation -> capture) and the accepted
authored scene fixtures within 8 m (id, module, distance, reviewer), so each decision can be judged by
hand: already owned by an authored fixture, or a real missing object. Writes assess/derived_fix_audit.json."""
import json, math, re
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
B = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
dec = json.loads((B / "authored_spatial_coherence_decisions.json").read_text())["decisions"]
fx = [f for f in json.loads((B / "authored_scene_fixtures.json").read_text())["fixtures"]
      if (f.get("manual_review") or {}).get("status") == "accepted"]
R43 = ROOT / ".run/fl4876_builds/build_reg_43"
plan = json.loads((R43 / "plan/population/population_plan.json").read_text())
pls = {}
for c in plan["carriers"]:
    for p in c.get("placements", []):
        pls[str(p.get("placement_id"))] = dict(p, _carrier=c.get("carrier_id"))
inst = {i["instance_id"]: i for i in json.loads((R43 / "observation_registration/registered_instances.json").read_text())}
cur = {i["instance_id"] for i in json.loads((ROOT / ".run/fl4876_builds/build_reg_51/observation_registration/registered_instances.json").read_text())}
rows = []
for d in dec:
    ids = re.findall(r"inst-\d+", d["target_id"] + json.dumps(d.get("parameters") or {}))
    if not ids:
        continue
    for iid in sorted(set(ids)):
        p = pls.get("obs-" + iid) or {}
        i = inst.get(iid) or {}
        near = []
        if p.get("E") is not None:
            for f in fx:
                dd = math.hypot(f["E"] - p["E"], f["N"] - p["N"])
                if dd <= 8:
                    near.append({"fixture_id": f["fixture_id"], "module_id": f["module_id"], "dist_m": round(dd, 2),
                                 "reviewed_by": str(f["manual_review"].get("reviewed_by"))[:60]})
        rows.append({"decision_id": d["decision_id"], "action": d["action"], "target_id": d["target_id"],
                     "instance_id": iid, "registered_now": iid in cur,
                     "reg43_module": p.get("module_id"), "reg43_E": p.get("E"), "reg43_N": p.get("N"),
                     "reg43_carrier": p.get("_carrier"),
                     "annotation_ids": i.get("annotation_ids") or i.get("source_annotation_ids"),
                     "capture_ids": i.get("capture_ids") or ([i.get("capture_id")] if i.get("capture_id") else None),
                     "decision_rationale": d.get("rationale"), "decision_evidence": d.get("evidence"),
                     "authored_within_8m": sorted(near, key=lambda r: r["dist_m"])})
(HERE / "assess" / "derived_fix_audit.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
for r in rows:
    print(r["decision_id"], r["instance_id"], "now" if r["registered_now"] else "withheld", r["reg43_module"],
          r["reg43_E"] and round(r["reg43_E"], 1), r["reg43_N"] and round(r["reg43_N"], 1))
    for n in r["authored_within_8m"][:5]:
        print("    ", n["fixture_id"], n["module_id"], n["dist_m"], n["reviewed_by"][:30])
