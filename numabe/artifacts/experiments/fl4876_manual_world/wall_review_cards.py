"""wall_review_cards.py OUT_DIR [PER_BATCH] -- reading aid (decides nothing) for the per-building hand review of the
walls no photo shows. For every building with neither a lit wall sample nor a wall_judgement it draws a card:
  left  : GSI z18 aerial (CC BY 4.0) 32 m square around the footprint, footprint outlined yellow, neighbours with a
          known wall outlined in their wall colour, north up, 5 m ticks;
  right : id tail, PLATEAU usage / height / storeys, hand-reviewed roof colour swatch, the 5 nearest buildings with a
          known wall (hand-sampled lit wall or hand judgement) as swatch + distance + description.
Six cards per sheet (sheet_NN.png) and a matching batch file batch_NN.json with the same facts as text.
Batches are grouped by geography (sorted along a snake over a 60 m grid) so a reviewer sees whole blocks."""
import json, math, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
from fetch_aerial import utm_to_ll, tile_xy  # noqa: E402
sys.path.insert(0, str(HERE / "palette_proposal"))
from lab import lch_to_rgb  # noqa: E402
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
PER = int(sys.argv[2]) if len(sys.argv) > 2 else 60
B = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
bl = json.loads((B / "authored_building_appearance.json").read_text())["buildings"]
attrs = json.loads((HERE / "assess" / "plateau_attrs.json").read_text())
canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_61/canonical.world_ir.json").read_text())
meta = json.loads((HERE / "aerial" / "mosaic.json").read_text())
mos = Image.open(HERE / "aerial" / "mosaic.jpg").convert("RGB")
F = ImageFont.load_default(size=12); G = ImageFont.load_default(size=11)


def ring(f):
    c = f["geometry"]["coordinates"]
    while isinstance(c[0][0], list):
        c = c[0]
    return [tuple(q[:2]) for q in c]


foot = {f["feature_id"]: ring(f) for f in canon["canonical_features"] if f.get("semantic_class") == "building_footprint"}
cen = {k: (sum(q[0] for q in r) / len(r), sum(q[1] for q in r) / len(r)) for k, r in foot.items()}


def known(k):
    v = bl.get(k) or {}
    if v.get("wall_rgb"):
        return tuple(v["wall_rgb"]), "lit sample"
    j = v.get("wall_judgement")
    if j:
        return lch_to_rgb(*j["target_lch"]), "judged " + j["family"]
    return None


todo = [k for k in foot if not (bl.get(k) or {}).get("wall_rgb") and not (bl.get(k) or {}).get("wall_judgement")]
todo.sort(key=lambda k: (int(cen[k][1] // 60), (cen[k][0] if int(cen[k][1] // 60) % 2 == 0 else -cen[k][0])))
kn = [k for k in foot if known(k)]


def pxl(E, N):
    lo, la = utm_to_ll(E, N); x, y = tile_xy(lo, la)
    return (x - meta["tile_x0"]) * 256, (y - meta["tile_y0"]) * 256


def card(k):
    E0, N0 = cen[k]; R = 16.0
    c = pxl(E0, N0); ex = pxl(E0 + R, N0); r = ex[0] - c[0]; K = 260 / (2 * r)
    box = (c[0] - r, c[1] - r, c[0] + r, c[1] + r)
    im = mos.crop(tuple(int(v) for v in box)).resize((260, 260), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    to = lambda q: ((pxl(*q)[0] - box[0]) * K, (pxl(*q)[1] - box[1]) * K)
    for n in kn:
        if math.dist(cen[n], (E0, N0)) < 30:
            d.line([to(q) for q in foot[n]], fill=known(n)[0], width=2)
    d.line([to(q) for q in foot[k]], fill=(255, 255, 0), width=2)
    for m in range(-15, 16, 5):
        x = 130 + m * 130 / R
        d.line([(x, 0), (x, 5)], fill=(255, 255, 0)); d.line([(0, x), (5, x)], fill=(255, 255, 0))
    v = bl.get(k) or {}; a = attrs.get(k) or {}
    nb = sorted(kn, key=lambda n: math.dist(cen[n], (E0, N0)))[:5]
    facts = {"id": k, "usage": a.get("usage_label", "unmatched"), "height_m": a.get("height_m"),
             "storeys": a.get("storeys") if a.get("storeys") != 9999 else None,
             "footprint_m2": round(abs(sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(foot[k], foot[k][1:] + foot[k][:1]))) / 2, 1),
             "roof_rgb": v.get("roof_rgb"), "description": v.get("what"),
             "street_view": (v.get("street_view") or {}).get("reason", "")[:200],
             "neighbours": [{"id": n, "dist_m": round(math.dist(cen[n], (E0, N0)), 1), "wall_rgb": list(known(n)[0]),
                             "kind": known(n)[1], "usage": (attrs.get(n) or {}).get("usage_label"),
                             "description": str((bl.get(n) or {}).get("what"))[:90]} for n in nb]}
    cv = Image.new("RGB", (560, 290), (12, 12, 16)); cv.paste(im, (0, 26)); dd = ImageDraw.Draw(cv)
    dd.text((4, 4), k.split(":")[-1][-34:], fill=(255, 255, 0), font=F)
    t = "%s  h %s m  st %s  %s m2" % (facts["usage"], facts["height_m"], facts["storeys"], facts["footprint_m2"])
    dd.text((266, 28), t, fill=(230, 230, 230), font=G)
    if facts["roof_rgb"]:
        dd.rectangle([266, 46, 300, 62], fill=tuple(facts["roof_rgb"])); dd.text((306, 48), "roof (aerial, hand)", fill=(200, 200, 200), font=G)
    y = 72
    for nbr in facts["neighbours"]:
        dd.rectangle([266, y, 290, y + 14], fill=tuple(nbr["wall_rgb"]))
        dd.text((296, y), "%4.0fm %s %s" % (nbr["dist_m"], nbr["kind"][:18], str(nbr["usage"])[:14]), fill=(220, 220, 220), font=G)
        dd.text((296, y + 13), nbr["description"][:44], fill=(150, 170, 150), font=G)
        y += 34
    return cv, facts


bi = 0
for s in range(0, len(todo), PER):
    batch = todo[s:s + PER]; facts_all = []
    for p in range(0, len(batch), 6):
        sheet = Image.new("RGB", (2 * 560, 3 * 290), (0, 0, 0))
        for i, k in enumerate(batch[p:p + 6]):
            cv, f = card(k); f["sheet"] = "sheet_%02d_%02d.png" % (bi, p // 6); f["slot"] = i
            f["card"] = "card_%02d_%03d.png" % (bi, p + i); cv.save(out / f["card"])
            facts_all.append(f); sheet.paste(cv, ((i % 2) * 560, (i // 2) * 290))
        sheet.save(out / ("sheet_%02d_%02d.png" % (bi, p // 6)))
    (out / ("batch_%02d.json" % bi)).write_text(json.dumps({"buildings": facts_all}, ensure_ascii=False, indent=1))
    bi += 1
print("buildings to review", len(todo), "batches", bi, "->", out)
