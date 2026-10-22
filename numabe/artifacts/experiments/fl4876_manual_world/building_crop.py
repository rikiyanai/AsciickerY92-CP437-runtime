"""building_crop.py CAPTURE_ID OFFSET_DEG BUILDING_TAIL OUT.png -- reading aid only (decides nothing).

Crops the panorama to one building's bearing span (+-8 deg) at the chosen heading offset, upscales
it x2 for reading, and draws a 25-px grid labelled in panorama pixel coordinates so the person can
name sample points (x, y in the 1024x512 panorama) for sample.py."""
import json, math, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
cid, tail, out = sys.argv[1], sys.argv[3], sys.argv[4]
_ov = [float(v) for v in sys.argv[2].split(",")]  # "deg" or "deg,dE,dN" (hand camera shift, metres)
off = _ov[0]
p = dict(json.loads((HERE / "pano_index.json").read_text())["panoramas"][cid])
if len(_ov) == 3:
    p["E"] += _ov[1]; p["N"] += _ov[2]
canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_43/canonical.world_ir.json").read_text())
f = next(f for f in canon["canonical_features"] if f.get("semantic_class") == "building_footprint"
         and f["feature_id"].endswith(tail))
ring = f["geometry"]["coordinates"][:-1]
im = Image.open(ROOT / p["local_path"]).convert("RGB").resize((1024, 512))
hd = p["heading_deg"] + off
bs = [math.degrees(math.atan2(q[0] - p["E"], q[1] - p["N"])) % 360 for q in ring]
c = math.degrees(math.atan2(sum(q[0] for q in ring) / len(ring) - p["E"], sum(q[1] for q in ring) / len(ring) - p["N"])) % 360
rel = [((b - c + 180) % 360) - 180 for b in bs]
lo, hi = c + min(rel) - 8, c + max(rel) + 8
xof = lambda b: (0.5 + ((b - hd + 180) % 360 - 180) / 360.0) * 1024
x0, x1 = int(xof(lo)), int(xof(hi))
if x1 <= x0:  # wraps the seam: show the side holding the centre
    xc = xof(c); x0, x1 = (int(xc) - 120, int(xc) + 120)
x0, x1 = max(0, x0), min(1024, x1)
crop = im.crop((x0, 40, x1, 400)).resize(((x1 - x0) * 2, 720))
d = ImageDraw.Draw(crop); F = ImageFont.load_default(size=13)
for gx in range((x0 // 25 + 1) * 25, x1, 25):
    X = (gx - x0) * 2; d.line([(X, 0), (X, 8)], fill=(255, 255, 0)); d.text((X + 1, 9), str(gx), fill=(255, 255, 0), font=F)
for gy in range(50, 400, 25):
    Y = (gy - 40) * 2; d.line([(0, Y), (8, Y)], fill=(255, 255, 0)); d.text((10, Y - 7), str(gy), fill=(255, 255, 0), font=F)
dist = min(math.dist((p["E"], p["N"]), q) for q in ring)
d.text((40, 700), "%s %.0f m  offset %+.0f  x %d-%d" % (f["feature_id"].split("/")[-1], dist, off, x0, x1),
       fill=(0, 255, 255), font=F)
crop.save(out); print(out, crop.size, "x %d-%d" % (x0, x1))
