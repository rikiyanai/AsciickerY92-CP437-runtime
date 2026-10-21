#!/usr/bin/env python3
"""Per-object acceptance chain table (FL-4809 section 15).

Promoted from .run/numabe_connected/coverage01/build_chain.py (NUMABE-COVERAGE-01
artifact) into a repository lane stage. Every non-omitted feature must be
emitted and must have resolved spatial and physical verification rows.

Universe: canonical World IR features within the route-visible corridor. The
denominator comes from the same-generation inventory, never a literal count.

Stages: observed -> canonicalised -> decided -> attached ->
spatially_coherent -> physically_coherent -> emitted. Exactly one row per
feature with either full pass or a named first-failing stage. Absent
instance lists are never PASS.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from scripts.pipeline import geo_frames as GF  # noqa: E402
from scripts.pipeline import compile_route_streetscape_inventory as INV  # noqa: E402
def build(build_dir: Path, route_path: Path, generated_at: str,
          spatial_path: Path | None = None,
          metric_path: Path | None = None) -> tuple[dict, str, tuple[int, int, int]]:
    plan_path = build_dir / "plan/representation_plan.json"
    PLAN = json.loads(plan_path.read_text(encoding="utf-8"))
    WORLD = json.loads((build_dir / "canonical.world_ir.json").read_text(encoding="utf-8"))
    ENT = json.loads((build_dir / "canonical_entities.json").read_text(encoding="utf-8"))
    SPAT = json.loads((spatial_path or build_dir / "spatial_coherence.json").read_text(encoding="utf-8"))
    MET = json.loads((metric_path or build_dir / "object_metric.json").read_text(encoding="utf-8"))
    EMISSIONS = json.loads(
        (build_dir / "emission_manifest.json").read_text(encoding="utf-8"))
    if (EMISSIONS.get("errors")
            or EMISSIONS.get("blocking_terminal_states")):
        raise ValueError(
            "emission manifest contains errors or lowering debt")
    RULES = json.loads((build_dir / "plan/rules_resolved.json").read_text(encoding="utf-8"))

    route_doc = json.loads(route_path.read_text(encoding="utf-8"))
    route_xy = [GF.utm_forward(lon, lat) for lon, lat in INV._decode_polyline6(route_doc["trip"]["legs"][0]["shape"])]
    inv_rules = (RULES.get("inventory") or {})
    if (inv_rules.get("bin_length_m") is None
            or inv_rules.get("route_visible_corridor_m") is None):
        raise ValueError("resolved rules lack acceptance inventory geometry")
    BIN_M = float(inv_rules["bin_length_m"])
    CORR_M = float(inv_rules["route_visible_corridor_m"])
    stations = [route_xy[0]]
    acc = 0.0
    for a, b in zip(route_xy, route_xy[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        while acc + seg >= BIN_M:
            take = BIN_M - acc
            frac = take / seg if seg > 0 else 0.0
            nxt = (a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac)
            stations.append(nxt)
            a = nxt
            seg = math.hypot(b[0] - a[0], b[1] - a[1])
            acc = 0.0
        acc += seg

    observations = {str(o["observation_id"]): o for o in WORLD.get("source_observations", [])}
    stations.append(route_xy[-1])

    def tags_of(f):
        for oid in f.get("supporting_observation_ids", []):
            o = observations.get(str(oid))
            if o is not None:
                t = (o.get("attributes") or {}).get("tags")
                if isinstance(t, dict):
                    return t
        return {}
    universe = []
    for f in WORLD.get("canonical_features", []):
        pts = INV._sample_points(f.get("geometry", {}))
        if not pts:
            continue
        d = min(math.hypot(px - sx, py - sy) for px, py in pts for sx, sy in stations)
        if d <= CORR_M:
            universe.append(f)
    inventory = json.loads(
        (build_dir / "route_streetscape_inventory.json").read_text(
            encoding="utf-8"))
    expected_universe = int(inventory["counts"]["corridor_features"])
    if len(universe) != expected_universe:
        raise ValueError(
            f"chain universe mismatch: {len(universe)} != inventory "
            f"{expected_universe}")
    feature_bin = {}
    for f in universe:
        pts = INV._sample_points(f.get("geometry", {}))
        bi = min(range(len(stations)),
                 key=lambda i: min(math.hypot(px - stations[i][0], py - stations[i][1]) for px, py in pts))
        feature_bin[str(f["feature_id"])] = bi

    CANON_OBS = {}
    for f in WORLD.get("canonical_features", []):
        CANON_OBS[str(f["feature_id"])] = (f.get("record_kind"), f.get("conflict"), f.get("supporting_observation_ids") or [])
    plan_by_fid = INV._plan_rows_by_feature(PLAN)
    emitted_ids, present = INV._emission_ids(build_dir)
    bridge = INV._bridge_ids(ENT)
    debt_owed, _debt_frames = INV._debt_ids(json.loads((build_dir / "observation_debt.json").read_text(encoding="utf-8")))
    spatial_by_fid = {}
    spatial_rep = {}
    for r in SPAT.get("rows", []):
        spatial_by_fid.setdefault(str(r["feature_id"]), []).append(r)
        if r.get("representation_id"):
            spatial_rep.setdefault(str(r["representation_id"]), []).append(r)
    metric_by_inst = {}
    for r in MET.get("rows", []):
        metric_by_inst.setdefault(str(r.get("inst_name")), []).append(r)
    inst_by_fid = {}
    for row in EMISSIONS.get("emissions", []):
        if row.get("terminal_state") != "emitted" or not row.get("inst_name"):
            continue
        inst_by_fid.setdefault(str(row.get("feature_id")), []).append(
            str(row.get("inst_name")))
    BLD_INST = json.loads((build_dir / "building_instances.json").read_text(encoding="utf-8"))
    mass_inst_by_fid = {}
    for row in (BLD_INST if isinstance(BLD_INST, list) else BLD_INST.get("instances", [])):
        if row.get("inst_name") or row.get("name"):
            mass_inst_by_fid.setdefault(str(row.get("feature_id")), []).append(str(row.get("inst_name") or row.get("name")))

    OMIT_DISP = {"intentional_omission", "minimap_only"}
    rows = []
    rep_mismatch = 0
    for f in universe:
        fid = str(f["feature_id"])
        cls = str(f.get("semantic_class") or "")
        group = INV._group_for(cls, tags_of(f))
        stages = {"observed": "pass"}
        first_fail = []

        def fail(stage, reason):
            stages[stage] = "FAIL: " + reason
            if not first_fail:
                first_fail.append(stage)
        stages["observed"] = "pass:universe-member"
        rec, conf, obs = CANON_OBS[fid]
        cstate = conf.get("state", "none") if isinstance(conf, dict) else ("none" if "'none'" in str(conf) else str(conf)[:40])
        if rec == "canonical_feature" and cstate == "none" and obs:
            stages["canonicalised"] = "pass:canonical_feature record, no conflicts"
        else:
            fail("canonicalised", f"record_kind={rec} conflict={conf} obs={len(obs)}")
        # decided
        prow = plan_by_fid.get(fid)
        terminal_omit = False
        if prow is None:
            fail("decided", "no plan row")
        elif str(prow.get("disposition")) in OMIT_DISP:
            stages["decided"] = "omitted_with_reason: " + str(prow.get("reason") or prow.get("disposition"))
            terminal_omit = True
        elif str(prow.get("disposition")) == "observation_owed":
            fail("decided", "observation_owed debt")
        else:
            stages["decided"] = "pass:" + str(prow.get("disposition"))
        # attached
        if not terminal_omit and not first_fail:
            ast = prow.get("attachment_state") if prow else None
            if ast == "spatial_reconciliation_debt":
                fail("attached", "attachment_state=spatial_reconciliation_debt")
            elif prow is not None and prow.get("attachment"):
                stages["attached"] = "pass"
            else:
                fail("attached", "attachment_state=" + str(ast))
        # emitted (computed before spatial/physical for n/a routing)
        equiv = {fid} | bridge.get(fid, set())
        is_emitted = any(e in emitted_ids for e in equiv)
        rep_id = str(prow.get("representation_id")) if prow and prow.get("representation_id") else None
        if terminal_omit:
            outcome = "omitted"
        else:
            if is_emitted:
                stages["emitted"] = "pass"
            # spatial (representation_id join where present, feature fallback)
            srows = spatial_by_fid.get(fid, [])
            if rep_id and rep_id in spatial_rep:
                if {id(r) for r in spatial_rep[rep_id]} != {id(r) for r in srows}:
                    rep_mismatch += 1
            if srows:
                worst = "FAIL" if any(r["verdict"] == "FAIL" for r in srows) else (
                    "UNRESOLVED" if any(r["verdict"] == "UNRESOLVED" for r in srows) else "PASS")
                if worst != "PASS":
                    fail("spatially_coherent", ";".join(sorted({
                        r.get("reason", "")[:80] for r in srows
                        if r["verdict"] == worst}))[:200])
                else:
                    stages["spatially_coherent"] = "pass"
            elif is_emitted:
                fail("spatially_coherent",
                     "emitted but no spatial row (verifier coverage gap)")
            else:
                stages["spatially_coherent"] = "n/a: never emitted"
            # physical
            insts = set()
            for r in srows:
                if r.get("inst_name"):
                    insts.add(str(r["inst_name"]))
            insts |= set(inst_by_fid.get(fid, []))
            insts |= set(mass_inst_by_fid.get(fid, []))
            mrows = [m for i in insts for m in metric_by_inst.get(i, [])]
            if mrows:
                worst_metric = ("FAIL" if any(m["verdict"] == "FAIL" for m in mrows)
                                else "UNRESOLVED" if any(
                                    m["verdict"] == "UNRESOLVED" for m in mrows)
                                else "PASS")
                if worst_metric != "PASS":
                    fail("physically_coherent", ";".join(sorted({
                        m.get("reason", "")[:80] for m in mrows
                        if m["verdict"] == worst_metric}))[:200])
                else:
                    stages["physically_coherent"] = "pass"
            elif is_emitted:
                disp = str(prow.get("disposition")) if prow else "?"
                rk = str(prow.get("representation_kind")) if prow else "?"
                if disp == "surface_decal":
                    stages["physically_coherent"] = "n/a: decal has no 3D asset"
                elif rk == "synthetic_identity_proxy":
                    stages["physically_coherent"] = "n/a: identity proxy, no asset contract"
                else:
                    fail("physically_coherent",
                         "emitted but no metric row (verifier coverage gap)")
            else:
                stages["physically_coherent"] = "n/a: never emitted"
            if not is_emitted:
                dreason = "emitting plan row without emission" if prow is not None else "no emission record"
                if fid in debt_owed:
                    dreason += "; also in observation_debt (inventory counts debt)"
                fail("emitted", dreason)
            outcome = "pass" if not first_fail else "fail@" + first_fail[0]
        rows.append({"feature_id": fid, "semantic_class": cls, "group": group,
                     "bin": feature_bin[fid],
                     "representation_id": rep_id,
                     "disposition": str(prow.get("disposition")) if prow else None,
                     "stages": stages, "first_failing_stage": first_fail[0] if first_fail else None, "outcome": outcome})

    if len(rows) != len({r["feature_id"] for r in rows}):
        raise ValueError("chain row uniqueness/count mismatch")
    by_group = {}
    for r in rows:
        g = by_group.setdefault(r["group"], {
            "total": 0, "pass": 0, "omitted": 0, "fail": Counter()})
        g["total"] += 1
        if r["outcome"] == "pass":
            g["pass"] += 1
        elif r["outcome"] == "omitted":
            g["omitted"] += 1
        else:
            g["fail"][r["first_failing_stage"]] += 1
    by_bin = {}
    for r in rows:
        b = by_bin.setdefault(r["bin"], {"total": 0, "pass": 0, "fail": 0, "other": 0})
        b["total"] += 1
        if r["outcome"] == "pass":
            b["pass"] += 1
        elif r["outcome"].startswith("fail"):
            b["fail"] += 1
        else:
            b["other"] += 1
    doc = {"schema": "y92.acceptance_chain.v1", "generated_at": generated_at,
           "universe": expected_universe, "corridor_m": CORR_M,
           "bin_m": BIN_M,
           "rep_id_join_mismatches": rep_mismatch,
           "by_group": {k: {"total": v["total"], "pass": v["pass"],
                            "omitted": v["omitted"],
                            "fail": dict(v["fail"])} for k, v in sorted(by_group.items())},
           "by_bin": {str(k): v for k, v in sorted(by_bin.items())},
           "rows": sorted(rows, key=lambda r: (r["group"], r["feature_id"]))}
    lines = ["group | total pass omitted fail:stage", ""]
    for g in sorted(by_group):
        v = by_group[g]
        fails = " ".join(f"{k}={n}" for k, n in sorted(v["fail"].items())) or "-"
        lines.append(f"{g} | {v['total']} {v['pass']} {v['omitted']} {fails}")
    lines += ["", "bin25m | total pass fail other", ""]
    for b in sorted(by_bin):
        v = by_bin[b]
        lines.append(f"{b} | {v['total']} {v['pass']} {v['fail']} {v['other']}")
    lines += ["", "non-pass rows (feature | group | outcome):"]
    for r in doc["rows"]:
        if r["outcome"] != "pass":
            lines.append(f"{r['feature_id']} | {r['group']} | {r['outcome']}")
    text = "\n".join(lines) + "\n"
    summary = (sum(1 for r in rows if r["outcome"] == "pass"),
               sum(1 for r in rows if r["outcome"] == "omitted"),
               sum(1 for r in rows if r["outcome"].startswith("fail")))
    return doc, text, summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Per-object acceptance chain table (FL-4809 section 15)")
    ap.add_argument("--build-dir", type=Path, required=True)
    ap.add_argument("--route", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--text-out", type=Path, required=True)
    ap.add_argument("--spatial-coherence", type=Path, default=None,
                    help="evidence-run spatial receipt (default: build-dir file)")
    ap.add_argument("--object-metric", type=Path, default=None,
                    help="evidence-run metric receipt (default: build-dir file)")
    ap.add_argument("--generated-at", default=None)
    args = ap.parse_args(argv)
    generated_at = args.generated_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    doc, text, summary = build(args.build_dir, args.route, generated_at,
                               spatial_path=args.spatial_coherence,
                               metric_path=args.object_metric)
    args.out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    args.text_out.write_text(text, encoding="utf-8")
    print(f"[chain] pass={summary[0]} omit={summary[1]} fail={summary[2]}")
    if summary[2]:
        print(f"[chain] {summary[2]} FAIL rows; failing build", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
