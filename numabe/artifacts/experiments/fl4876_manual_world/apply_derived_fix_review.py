"""apply_derived_fix_review.py [--write] -- transcription only (decides nothing): applies the hand review of
the 16 decided spatial-coherence rows that targeted derived observation placements withheld since
MANUAL-WORLD-03 (assess/derived_fix_review.json for 12 instances, OWNED_BY_MAIN below for the rest).
  * owned  -> the decision row is marked superseded_by the accepted authored fixture that owns the object
  * missing-> the reviewer's complete accepted record is added to authored_scene_fixtures.json and the
             decision row is marked superseded_by that new record
Rows already superseded (0119 by REG-EDGE-01, 0417 by POS-4876-002, 0024 by POS-4876-035) are untouched;
the 0119 fence is nevertheless a missing physical object (no explicit position registers it), so its
reviewed record SF-4876-X002 is added. The pole span (canonical poles) and station facade rows stay."""
import json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
B = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
DEC, FX = B / "authored_spatial_coherence_decisions.json", B / "authored_scene_fixtures.json"
review = json.loads((HERE / "assess" / "derived_fix_review.json").read_text())["reviews"]
# main-session hand calls (cards in scratchpad/audit, 2026-10-13)
OWNED_BY_MAIN = {
    "inst-0459": ("railway geometry", "the crop is the ballasted double track at the station; the track is emitted from the mapped railway carriers, not a scene fixture"),
    "inst-0510": ("SF-4876-0425", "rail catenary portal 1.55 m from accepted overhead_gantry_wire SF-4876-0425"),
    "inst-0097": ("SF-4876-0150", "same tall clipped hedge on the retaining wall, 3.5 m from accepted trimmed_hedge SF-4876-0150"),
    "inst-0626": ("SF-4876-0141", "same vegetated stone channel bank, 3.3 m from accepted stone_lined_water_channel SF-4876-0141"),
    "inst-0124": ("SF-4876-0186", "same open drainage channel beside the hedge, 3.5 m from accepted open_and_covered_drain SF-4876-0186"),
    "inst-0129": ("SF-4876-0132", "same dense hedge along the curving path, 3.8 m from accepted trimmed_hedge SF-4876-0132"),
}
ROWS = {  # decision_id -> derived instance(s) it targeted
    "fl4876-manual-0517-axis": ["inst-0517"], "fl4876-manual-0459-rail": ["inst-0459"],
    "fl4876-manual-gantry-0462-0510": ["inst-0462", "inst-0510"],
    "fl4876-jev-visual-01-0097": ["inst-0097"], "fl4876-jev-visual-01-0602": ["inst-0602"],
    "fl4876-jev-visual-01-0626": ["inst-0626"], "fl4876-jev-visual-01-0303": ["inst-0303"],
    "fl4876-jev-visual-01-0124": ["inst-0124"], "fl4876-jev-visual-01-0129": ["inst-0129"],
    "fl4876-jev-visual-01-0138": ["inst-0138"], "fl4876-jev-visual-01-0583": ["inst-0583"],
    "fl4876-jev-visual-01-0635": ["inst-0635"], "fl4876-jev-visual-01-0498": ["inst-0498"],
    "fl4876-jev-visual-01-0230": ["inst-0230"], "fl4876-manual-gutter-0235": ["inst-0235"],
    "fl4876-manual-gutter-0593": ["inst-0593"],
}


def verdict(iid):
    if iid in OWNED_BY_MAIN:
        return "owned", OWNED_BY_MAIN[iid][0], OWNED_BY_MAIN[iid][1], None
    r = review[iid]
    if r["verdict"] == "owned":
        return "owned", r["owned_by"], r["reason"], None
    if r["verdict"] == "missing":
        return "missing", r["record"]["fixture_id"], r["reason"], r["record"]
    return "reject", None, r["reason"], None


dec = json.loads(DEC.read_text())
fx = json.loads(FX.read_text())
have = {f["fixture_id"] for f in fx["fixtures"]}
added, superseded = [], []
for row in dec["decisions"]:
    ids = ROWS.get(row["decision_id"])
    if not ids or row.get("status") != "decided":
        continue
    vs = [verdict(i) for i in ids]
    row["status"] = "superseded"
    row["superseded_by"] = ", ".join(sorted({str(v[1]) for v in vs}))
    row["superseded_basis"] = "; ".join("%s %s by %s: %s" % (i, v[0], v[1], v[2]) for i, v in zip(ids, vs))
    row["superseded_on"] = "2026-10-13"
    superseded.append((row["decision_id"], row["superseded_by"]))
# The reviewer's descriptive variant strings are not catalog variants (the mesh builder rejects them).
# Main-session mapping to the nearest catalog variant of the same module, by the photo description:
VARIANT = {"SF-4876-X001": "galvanized_silver",            # galvanized silver vertical-bar rail
           "SF-4876-X002": "dark_metal_rail_boundary",     # black metal railing on a concrete base
           "SF-4876-X003": "green_painted_shared_path",    # green painted shared path band
           "SF-4876-X004": "dark_stacked_block_boundary"}  # dark stacked block wall (the annotation's own variant)
for iid, r in review.items():
    if r["verdict"] == "missing" and r["record"]["fixture_id"] not in have:
        fx["fixtures"].append(r["record"]); added.append(r["record"]["fixture_id"])
for f in fx["fixtures"]:
    if f["fixture_id"] in VARIANT and f.get("variant") != VARIANT[f["fixture_id"]]:
        f["variant_description"] = f.get("variant")
        f["variant"] = VARIANT[f["fixture_id"]]
        print("variant", f["fixture_id"], "->", f["variant"])
for d, s in superseded:
    print("superseded", d, "->", s)
print("added", added)
if "--write" in sys.argv:
    DEC.write_text(json.dumps(dec, ensure_ascii=False, indent=2) + "\n")
    FX.write_text(json.dumps(fx, ensure_ascii=False, indent=1) + "\n")
    print("written", DEC.name, FX.name)
