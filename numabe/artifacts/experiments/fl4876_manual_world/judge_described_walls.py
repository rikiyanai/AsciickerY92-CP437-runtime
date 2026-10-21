"""judge_described_walls.py [--write] -- transcription of the main session's hand judgement (2026-10-13) of the wall
colour of buildings that have an eye-verified street-level DESCRIPTION (and often a shaded/backlit measurement)
but no lit wall sample. Before this they fell to the type palette (a random same-class colour), ignoring what the
assessor saw -- e.g. the tan-brick ミヨシヤ building drew a detached-house cream.

Writes buildings[id]["wall_judgement"] in authored_building_appearance.json:
  {"target_lch": [L, C, h], "family": ..., "evidence": "street_description", "basis": ..., "judged_by", "judged_on"}
infer_palette.py turns a judgement into the hand-sampled lit Numabe wall nearest to the target (tier hand_judged).
Buildings whose description names no wall colour are left for the per-building card review (wall_review_cards.py)."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
APP = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/authored_building_appearance.json"
P = "feature:building_footprint:"
J = {  # id: (family, target L, C, h, basis quoted from the record)
    "way/1507398029": ("kaihaku_greige", 85, 3, 110, "'pale grey-white wall'; shaded sample [184,192,184]"),
    "way/1507398062": ("torinoko_cream", 86, 10, 86, "'assessor saw cream' (windscreen-tinted sample rejected)"),
    "way/1507403547": ("ginnezu_silver", 66, 2, 90, "'grey rendered walls'"),
    "way/1507403612": ("kinari_ivory", 88, 6, 90, "'pale cream/grey-white plaster walls'"),
    "way/1507403664": ("kinari_ivory", 92, 2, 95, "'white modern corner house' (brown-tile south face is the minor face)"),
    "way/1507403739": ("kinari_ivory", 92, 2, 95, "'2-storey white rendered house'"),
    "way/1507403752": ("ginnezu_silver", 64, 2, 90, "'3-storey grey concrete/render building'"),
    "way/1507403754": ("torinoko_cream", 85, 10, 85, "'cream concrete/render wall'"),
    "way/1507403757": ("kaihaku_greige", 75, 6, 85, "'beige-grey stucco wall'"),
    "way/1507403772": ("karacha_tile", 38, 20, 40, "'dark red-brown brick-tile walls'"),
    "way/1507403773": ("torinoko_cream", 85, 11, 85, "'3-storey cream house'"),
    "way/1507403778": ("kinari_ivory", 94, 1, 95, "'white 3-storey block'; overexposed sample clipped at [250,250,248]"),
    "way/1507403795": ("kinari_ivory", 88, 2, 95, "'white/grey commercial building'; shaded sample [90,98,113] read 'paint likely white'"),
    "way/1507403798": ("karacha_tile", 36, 14, 55, "'dark brown wood cladding on the upper floor, beige plaster ground floor' (upper mass dominates)"),
    "way/1507403812": ("ainezu_bluegrey", 72, 8, 230, "'pale blue-grey ribbed tile cladding'"),
    "way/1507403814": ("torinoko_cream", 85, 11, 85, "'cream stucco upper wall, beige horizontal cladding'"),
    "way/225101506": ("karacha_tile", 30, 10, 55, "temple 山門: 'dark recessed timber' frame and wooden doors are the building; white plaster walls are separate boundary walls"),
    "way/225197357": ("suna_sand", 78, 12, 80, "'beige rendered 2-storey building'; backlit sample read 'light warm beige-grey'"),
    "way/225367799": ("kaihaku_greige", 70, 7, 75, "'warm grey-beige walls' (eye-verified; backlit sample [109,101,99])"),
    "way/225367806": ("kaihaku_greige", 80, 6, 290, "'pale grey-lavender walls'; sunlit gallery sample [214,197,183]"),
    "way/225369232": ("karacha_tile", 52, 15, 55, "'brown-tiled curved balcony front' over a white ground floor; shaded tile sample [129,121,113]"),
    "way/225369235": ("rikyu_mortar", 45, 25, 150, "'fully wrapped in scaffolding with green mesh sheet' (current state as photographed)"),
    "way/225369237": ("karacha_tile", 62, 20, 65, "'3-storey tan-brick-tiled building' (ミヨシヤ)"),
    "way/225482963": ("karacha_tile", 40, 22, 38, "'5-storey stepped apartment block in red-brown brick tile'"),
    "way/725138291": ("kaihaku_greige", 55, 6, 65, "'grey-brown concrete/render block'"),
    "way/725138436": ("ginnezu_silver", 65, 2, 90, "'low exposed-concrete block'"),
    "way/725138533": ("ginnezu_silver", 68, 2, 250, "'3-storey grey rendered building'"),
    "way/725138592": ("suna_sand", 78, 12, 80, "'small 2-storey beige house'"),
    "way/725138597": ("ginnezu_silver", 65, 2, 240, "'grey horizontal clapboard walls'"),
}
d = json.loads(APP.read_text())
bl = d["buildings"]
for k, (fam, L, C, h, basis) in J.items():
    rec = bl[P + k]
    assert not rec.get("wall_rgb"), k
    rec["wall_judgement"] = {"target_lch": [L, C, h], "family": fam, "evidence": "street_description",
                             "basis": "described as " + basis + " (record 'what': " + str(rec.get("what"))[:90] + ")",
                             "judged_by": "main session", "judged_on": "2026-10-13"}
print("judged", len(J))
if "--write" in sys.argv:
    APP.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n")
    print("written")
