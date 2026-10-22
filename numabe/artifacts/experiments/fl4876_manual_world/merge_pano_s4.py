"""merge_pano_s4.py [--write] [--drop FIXTURE_ID ...] [--alias NEW_ID=KEEP_ID ...] -- transcription only
(FL-4876 FIXTURE-PASS4; decides nothing).

Reads the hand reviews assess/pano_s4_NN.json (each already passing validate_pano_s4.py) and:
  * lists cross-batch DUPLICATE CANDIDATES: MISSING records of the same module within 4 m of each other, or within
    4 m of an existing accepted fixture of the same module. The parent decides each by hand and passes the
    decision back as --drop ID (record not added; it is the same object as an existing fixture) or
    --alias NEW=KEEP (sightings of NEW are folded into KEEP, NEW not added);
  * with --write, copies into the owners:
      authored_scene_fixtures.json: every MISSING record not dropped/aliased (sightings merged for aliases);
      authored_building_appearance.json "panoramas"[cid]: object_verdicts_pass4 (what/verdict/owned_by/same_as/
      reason) and pass4_notes / pass4_assessed_by.
Refuses to write when any duplicate candidate is undecided or any validator error remains."""
import glob
import json
import math
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
B = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
APP, FX = B / "authored_building_appearance.json", B / "authored_scene_fixtures.json"
files = sorted(glob.glob(str(HERE / "assess" / "pano_s4_[0-9][0-9].json")))
args = sys.argv[1:]
drops = {args[i + 1] for i, a in enumerate(args) if a == "--drop"}
aliases = dict(args[i + 1].split("=", 1) for i, a in enumerate(args) if a == "--alias")
# --keep A=B: a duplicate candidate the parent looked at and judged two different objects
keeps = {frozenset(args[i + 1].split("=", 1)) for i, a in enumerate(args) if a == "--keep"}
rejects = {}  # fixture_id -> reason ("reject ID # reason" lines)
moves = {}  # fixture_id -> (E, N, reason) ("move ID E N # reason" lines)
# --decisions FILE: one decision per line ("drop ID" / "alias NEW=KEEP" / "keep A=B" / "# comment")
for i, a in enumerate(args):
    if a == "--decisions":
        for line in Path(args[i + 1]).read_text().splitlines():
            parts = line.split("#", 1)[0].split()
            if len(parts) == 2 and parts[0] == "drop":
                drops.add(parts[1])
            elif len(parts) == 2 and parts[0] == "alias":
                k, v = parts[1].split("=", 1); aliases[k] = v
            elif len(parts) == 2 and parts[0] == "keep":
                keeps.add(frozenset(parts[1].split("=", 1)))
            elif len(parts) == 2 and parts[0] == "reject":  # owner record already written: set rejected
                rejects[parts[1]] = line.split("#", 1)[1].strip() if "#" in line else "rejected by hand"
            elif len(parts) == 4 and parts[0] == "move":  # owner record already written: new hand-chosen E N
                moves[parts[1]] = (float(parts[2]), float(parts[3]),
                                   line.split("#", 1)[1].strip() if "#" in line else "moved by hand")
chk = subprocess.run([sys.executable, str(HERE / "validate_pano_s4.py")] + files, capture_output=True, text=True)
print(chk.stdout.strip())
app, fx = json.loads(APP.read_text()), json.loads(FX.read_text())
existing = [f for f in fx["fixtures"] if (f.get("manual_review") or {}).get("status") == "accepted"
            and not str(f["fixture_id"]).startswith("SF-4876-S4")]  # pass-4 records compare among themselves only
new, reviews = {}, {}
for p in files:
    doc = json.loads(Path(p).read_text())
    for cid, r in doc["panoramas"].items():
        reviews[cid] = (doc.get("batch"), r)
        for o in r.get("objects") or []:
            if o.get("verdict") == "MISSING" and o.get("record"):
                new[o["record"]["fixture_id"]] = o["record"]
cands = []
ids = sorted(new)
for i, a in enumerate(ids):
    ra = new[a]
    for b in ids[i + 1:]:
        rb = new[b]
        if ra["module_id"] == rb["module_id"] and math.hypot(ra["E"] - rb["E"], ra["N"] - rb["N"]) <= 4:
            cands.append((a, b, round(math.hypot(ra["E"] - rb["E"], ra["N"] - rb["N"]), 1)))
    for f in existing:
        if f.get("module_id") == ra["module_id"] and math.hypot(f["E"] - ra["E"], f["N"] - ra["N"]) <= 4:
            cands.append((a, f["fixture_id"], round(math.hypot(f["E"] - ra["E"], f["N"] - ra["N"]), 1)))
undecided = [c for c in cands if c[0] not in drops and c[0] not in aliases and c[1] not in drops and c[1] not in aliases
             and frozenset(c[:2]) not in keeps]
print(json.dumps({"review_files": len(files), "panoramas": len(reviews), "missing_records": len(new),
                  "dup_candidates": len(cands), "undecided": len(undecided),
                  "drops": len(drops), "aliases": len(aliases)}))
for a, b, d in undecided[:200]:
    print("  DUP?", a, new[a]["module_id"], "<->", b, d, "m")
if "--write" not in args:
    sys.exit(0)
if chk.returncode != 0 or undecided:
    sys.exit("refusing to write: validator errors or undecided duplicate candidates")
by_id = {f["fixture_id"]: f for f in fx["fixtures"]}
added = 0
for fid, rec in new.items():
    if fid in drops:
        continue
    if fid in aliases:
        tgt = aliases[fid]
        while tgt in aliases:  # alias chains resolve to the final kept record
            tgt = aliases[tgt]
        keep = by_id.get(tgt) or new.get(tgt)
        if any(m.get("fixture_id") == fid for m in keep.get("merged_duplicates") or []):
            continue  # already folded on an earlier --write (re-runs are idempotent)
        keep.setdefault("sightings", []).extend(rec.get("sightings") or [])
        keep.setdefault("merged_duplicates", []).append({"fixture_id": fid, "E": rec["E"], "N": rec["N"],
                                                         "by": "parent hand decision (FIXTURE-PASS4 merge)"})
        continue
    if fid not in by_id:
        fx["fixtures"].append(rec)
        by_id[fid] = rec
        added += 1
rejected = 0
for fid, why in rejects.items():
    rec = by_id.get(fid)
    if rec is None:
        sys.exit("reject: no owner record " + fid)
    mr = rec.setdefault("manual_review", {})
    if mr.get("status") != "rejected":
        mr.update(status="rejected", rejected_on=time.strftime("%Y-%m-%d"),
                  rejection_reason=why + " (parent hand decision, FIXTURE-PASS4 merge decisions file)")
        rejected += 1
print("rejected", rejected)
moved = 0
for fid, (E, N, why) in moves.items():
    rec = by_id.get(fid)
    if rec is None:
        sys.exit("move: no owner record " + fid)
    if (round(rec["E"], 2), round(rec["N"], 2)) == (round(E, 2), round(N, 2)):
        continue  # already applied
    rec.setdefault("parent_moves", []).append({"from": [rec["E"], rec["N"]], "to": [E, N], "on": time.strftime("%Y-%m-%d"),
                                              "why": why + " (parent hand decision, FIXTURE-PASS4 merge decisions file)"})
    rec["E"], rec["N"] = E, N
    moved += 1
print("moved", moved)
for cid, (batch, r) in reviews.items():
    cur = app["panoramas"].setdefault(cid, {})
    cur["object_verdicts_pass4"] = [{k: o.get(k) for k in ("what", "verdict", "owned_by", "same_as", "reason")}
                                    for o in r.get("objects") or []]
    cur["pass4_assessed_by"] = batch
    if r.get("notes"):
        cur["pass4_notes"] = r["notes"]
FX.write_text(json.dumps(fx, ensure_ascii=False, indent=1) + "\n")
APP.write_text(json.dumps(app, ensure_ascii=False, indent=1) + "\n")
print("written: fixtures added", added, "panorama reviews", len(reviews))
