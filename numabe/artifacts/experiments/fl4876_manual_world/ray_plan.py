"""ray_sheet.py OUT.png CAM_E CAM_N BEARING[,BEARING..] MARK_E,MARK_N[;...] [R_M]
Plan sketch (list/measure only): footprints (build_reg_73 canonical) within R m of the camera, labelled by
way id tail, the camera rays, and marks. Prints which footprints each ray hits first."""
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[3]
canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_73/canonical.world_ir.json").read_text())
fps = {f["feature_id"]: f["geometry"]["coordinates"] for f in canon["canonical_features"]
       if f.get("semantic_class") == "building_footprint"}
roads = [f for f in canon["canonical_features"] if f.get("geometry", {}).get("type") == "LineString"]
out = sys.argv[1]
cE, cN = float(sys.argv[2]), float(sys.argv[3])
bears = [float(b) for b in sys.argv[4].split(",")]
marks = [tuple(float(v) for v in m.split(",")) for m in sys.argv[5].split(";")] if len(sys.argv) > 5 and sys.argv[5] else []
R = float(sys.argv[6]) if len(sys.argv) > 6 else 60
S = 12  # px per m
W = int(2 * R * S)
im = Image.new("RGB", (W, W), (250, 250, 250))
dr = ImageDraw.Draw(im)
font = ImageFont.load_default()


def px(E, N):
    return ((E - cE + R) * S, (cN - N + R) * S)


def ring_of(c):
    r = c[0] if isinstance(c[0][0], list) else c
    return [tuple(p[:2]) for p in r]


for f in roads:
    pts = [tuple(p[:2]) for p in f["geometry"]["coordinates"]]
    if any(abs(p[0] - cE) < R and abs(p[1] - cN) < R for p in pts):
        dr.line([px(*p) for p in pts], fill=(200, 200, 230), width=2)
near = {}
for k, c in fps.items():
    r = ring_of(c)
    if any(abs(p[0] - cE) < R and abs(p[1] - cN) < R for p in r):
        near[k] = r
        dr.polygon([px(*p) for p in r], outline=(60, 60, 60), fill=(225, 220, 210))
        cx = sum(p[0] for p in r) / len(r)
        cy = sum(p[1] for p in r) / len(r)
        dr.text(px(cx, cy), k.split("/")[-1][-6:], fill=(0, 0, 160), font=font)


def hit(a, b, p, q):
    (x1, y1), (x2, y2), (x3, y3), (x4, y4) = a, b, p, q
    d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
    if abs(d) < 1e-12:
        return None
    t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / d
    u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / d
    return t if 0 <= u <= 1 and t > 0 else None


for b in bears:
    rad = math.radians(b)
    end = (cE + math.sin(rad) * R * 1.4, cN + math.cos(rad) * R * 1.4)
    dr.line([px(cE, cN), px(*end)], fill=(220, 40, 40), width=2)
    hits = []
    for k, r in near.items():
        for i in range(len(r)):
            t = hit((cE, cN), end, r[i], r[(i + 1) % len(r)])
            if t is not None:
                hits.append((t * R * 1.4, k, i))
    hits.sort()
    print("bearing %.1f first hits:" % b, [(round(d, 1), k.split("/")[-1], i) for d, k, i in hits[:4]])
for (E, N) in marks:
    x, y = px(E, N)
    dr.ellipse([x - 5, y - 5, x + 5, y + 5], outline=(0, 150, 0), width=3)
dr.ellipse([W / 2 - 6, W / 2 - 6, W / 2 + 6, W / 2 + 6], fill=(220, 40, 40))
dr.text((6, 6), "N up, %d px/m, cam E%.0f N%.0f" % (S, cE, cN), fill=(0, 0, 0), font=font)
im.save(out)
print(out)
