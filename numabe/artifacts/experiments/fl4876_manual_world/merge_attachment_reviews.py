"""merge_attachment_reviews.py [--write] -- transcription only (decides nothing): copies the hand attachment reviews
(assess/attach/attach_review_00..02.json, Sonnet reviewers reading the evidence photos of attach_batch_*.json) into
authored_building_appearance.json buildings[id]["attachments"]. Validates every item: known kind, face bearing equal
to one of the building's footprint faces (within 2 deg) unless rooftop, storey >= 1, fractions in range; invalid
items are reported and not written. Refuses to write while any batch id is unreviewed."""
import glob, json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
APP = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_building_appearance.json"
KINDS = {"balcony", "railing", "window_grille", "rooftop_railing", "stair_handrail"}
STYLES = {"dark_metal", "white_metal", "glass", "concrete_balustrade", "louvre_panel"}
d = json.loads(APP.read_text()); bl = d["buildings"]
batch = {}
for p in glob.glob(str(HERE / "assess/attach/attach_batch_*.json")):
    for r in json.loads(Path(p).read_text())["buildings"]:
        batch[r["id"]] = r
seen, bad, n_items, n_bld, skipped = set(), [], 0, 0, []
for p in sorted(glob.glob(str(HERE / "assess/attach/attach_review_*.json"))):
    rv = json.loads(Path(p).read_text())
    for k, r in rv["reviews"].items():
        if k not in batch:
            bad.append(("unknown id", k)); continue
        seen.add(k)
        faces = [f["outward_bearing_deg"] for f in batch[k]["faces"]]
        good = []
        for it in r.get("attachments") or []:
            why = []
            if it.get("kind") not in KINDS: why.append("kind")
            if it.get("style") and it["style"] not in STYLES: why.append("style")
            if it.get("kind") != "rooftop_railing":
                fb = it.get("face_bearing_deg")
                if fb is None or not any(abs((float(fb) - f + 180) % 360 - 180) <= 2.0 for f in faces): why.append("face")
            if int(it.get("storey") or 1) < 1: why.append("storey")
            for key in ("along_frac", "width_frac"):
                if it.get(key) is not None and not 0.0 <= float(it[key]) <= 1.0: why.append(key)
            if why:
                bad.append((k.split(":")[-1], it.get("kind"), why)); continue
            if k.endswith("way/225197357") and it.get("kind") == "stair_handrail":
                # main-session override: the reviewer's own reading puts the external steel stair on the
                # NEIGHBOURING building (left of this beige box), so it is not this building's attachment
                bad.append((k.split(":")[-1], "stair_handrail", ["dropped: stair is on the neighbour (reviewer note)"])); continue
            good.append({**it, "reviewed_by": rv.get("reviewer"), "reviewed_on": "2026-10-13"})
        if not good:
            skipped.append((k.split(":")[-1], r.get("skip_reason")))
        bl[k]["attachments"] = good
        bl[k]["attachments_review"] = {"confidence": r.get("confidence"), "skip_reason": r.get("skip_reason"),
                                       "reviewed_by": rv.get("reviewer"), "batch": rv.get("batch")}
        n_items += len(good); n_bld += bool(good)
missing = sorted(set(batch) - seen)
print("buildings with attachments", n_bld, "| items", n_items, "| invalid", len(bad), bad[:12], "| empty", len(skipped), skipped[:8],
      "| missing", len(missing))
if "--write" in sys.argv:
    if missing:
        sys.exit("refusing: unreviewed ids %s" % missing[:5])
    APP.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n"); print("written")
