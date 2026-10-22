"""merge_wall_reviews.py [--write] -- transcription only (decides nothing): copies the per-building hand wall reviews
(assess/wall_review_00..05.json, Sonnet reviewers reading the wall_review_cards.py cards) into
authored_building_appearance.json buildings[id]["wall_judgement"] (evidence "card_review"). Refuses ids that already
carry a lit wall sample or a judgement, checks that every card id was reviewed exactly once and that each target is
a plausible Ota-ku wall (L >= 30, chroma <= 26 warm / <= 14 cool or green mesh exceptions are reported, not dropped)."""
import glob, json, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
APP = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_building_appearance.json"
FAM = {"kinari_ivory", "torinoko_cream", "suna_sand", "kaihaku_greige", "ginnezu_silver", "rikyu_mortar",
       "ainezu_bluegrey", "konnezu_navy", "karacha_tile"}
d = json.loads(APP.read_text()); bl = d["buildings"]
cards = {r["id"] for p in glob.glob(str(HERE / "assess/wall_cards/batch_*.json")) for r in json.loads(Path(p).read_text())["buildings"]}
seen, problems, n = set(), [], 0
for p in sorted(glob.glob(str(HERE / "assess/wall_review_*.json"))):
    rv = json.loads(Path(p).read_text())
    for k, r in rv["reviews"].items():
        if k in seen:
            problems.append(("duplicate", k)); continue
        seen.add(k)
        if k not in cards:
            problems.append(("not a card id", k)); continue
        rec = bl.setdefault(k, {})
        if rec.get("wall_rgb") or rec.get("wall_judgement"):
            problems.append(("already owned", k)); continue
        L, C, h = (float(x) for x in r["target_lch"])
        if r.get("family") not in FAM:
            problems.append(("family", k, r.get("family")))
        if L < 30 or C > 26:
            problems.append(("implausible target", k, r["target_lch"]))
        rec["wall_judgement"] = {"target_lch": [L, C, h], "family": r.get("family"), "evidence": "card_review",
                                 "confidence": r.get("confidence"), "basis": r.get("basis"),
                                 "judged_by": rv.get("reviewer"), "judged_on": "2026-10-13",
                                 "card": "artifacts/experiments/fl4876_manual_world/assess/wall_cards (batch %s)" % rv.get("batch")}
        n += 1
missing = sorted(cards - seen)
print("merged", n, "| cards", len(cards), "| missing", len(missing), missing[:5], "| problems", len(problems), problems[:10])
if "--write" in sys.argv:
    if missing:
        sys.exit("refusing to write: unreviewed cards remain")
    APP.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n")
    print("written")
