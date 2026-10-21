"""roof_form_sheet.py OUT.png SOURCE... -- reading aid (decides nothing): GSI aerial 30 m crops of the given buildings with
the footprint (yellow) and, for a fitted pitched roof, the ridge line (red) from roof_fields_fitted/<src>.roof_field.json;
caption = form, pitch, eave/ridge, RMS vs flat RMS. Used to check the roof_form_fit.py choices against the photo."""
import json, math, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
from fetch_aerial import utm_to_ll, tile_xy  # noqa: E402
meta = json.loads((HERE / "aerial/mosaic.json").read_text()); mos = Image.open(HERE / "aerial/mosaic.jpg").convert("RGB")
canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_64/canonical.world_ir.json").read_text())
term = json.loads((ROOT / ".run/fl4876_builds/build_reg_64/terrain_metadata.json").read_text())
foot = {}
for f in canon["canonical_features"]:
    if f.get("semantic_class") == "building_footprint":
        q = f["geometry"]["coordinates"]
        while isinstance(q[0][0], list):
            q = q[0]
        foot[str(f["feature_id"]).split(":", 2)[-1]] = q
px = lambda E, N: (lambda xy: ((xy[0] - meta["tile_x0"]) * 256, (xy[1] - meta["tile_y0"]) * 256))(tile_xy(*utm_to_ll(E, N)))
to_utm = lambda xm, ym: ((xm * term["content_scale"] - term["offset_x"]) / term["content_scale"] + term["origin_easting"],
                         (ym * term["content_scale"] - term["offset_y"]) / term["content_scale"] + term["origin_northing"])
F = ImageFont.load_default(size=12)
srcs = sys.argv[2:]
sheet = Image.new("RGB", (4 * 300, ((len(srcs) + 3) // 4) * 320))
for i, s in enumerate(srcs):
    q = foot[s]; E0 = sum(p[0] for p in q) / len(q); N0 = sum(p[1] for p in q) / len(q)
    c = px(E0, N0); r = px(E0 + 15, N0)[0] - c[0]; K = 300 / (2 * r)
    box = (c[0] - r, c[1] - r)
    im = mos.crop(tuple(int(v) for v in (c[0] - r, c[1] - r, c[0] + r, c[1] + r))).resize((300, 300), Image.LANCZOS)
    d = ImageDraw.Draw(im); to = lambda E, N: ((px(E, N)[0] - box[0]) * K, (px(E, N)[1] - box[1]) * K)
    d.line([to(*p[:2]) for p in q], fill=(255, 255, 0), width=2)
    fp = HERE / "roof_fields_fitted" / (s.replace("/", "_") + ".roof_field.json")
    cap = "flat"
    if fp.exists():
        doc = json.loads(fp.read_text()); rf = doc.get("roof_form")
        if rf:
            m = rf["model"]; th = m["th_rad"]; ua = (math.sin(th), math.cos(th)); ub = (math.cos(th), -math.sin(th))
            b0 = m.get("b0", 0.0); a0, a1 = m["a_min"], m["a_max"]
            if rf["form"] == "hip":
                a0, a1 = m["a0"] - m["d"], m["a0"] + m["d"]
            pts = [to_utm(m["cx_m"] + ua[0] * a + ub[0] * b0, m["cy_m"] + ua[1] * a + ub[1] * b0) for a in (a0, a1)]
            if rf["form"] != "shed":
                d.line([to(*p) for p in pts], fill=(255, 0, 0), width=3)
            cap = "%s %.0fdeg e%.1f r%.1f rms %.2f/%.2f" % (rf["form"], rf["pitch_deg"], rf["eave_m"], rf["ridge_m"], rf["rms_m"], rf["flat_rms_m"])
    sheet.paste(im, ((i % 4) * 300, (i // 4) * 320 + 20))
    ImageDraw.Draw(sheet).text(((i % 4) * 300 + 3, (i // 4) * 320 + 3), s[-14:] + " " + cap, fill=(255, 255, 0), font=F)
sheet.save(sys.argv[1]); print(sys.argv[1])
