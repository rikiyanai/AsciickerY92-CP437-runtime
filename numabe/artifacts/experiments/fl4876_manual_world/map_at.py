"""map_at.py CAPTURE_ID OUT.png [RADIUS_M] -- reading aid only (decides nothing): plan map around a
panorama camera with every building footprint outlined and labelled by the tail of its id, the
camera (cyan) and bearing spokes every 30 deg labelled with the panorama x fraction they fall at
(x = 0.5 + (bearing - heading)/360). North up."""
import json, math, sys
from pathlib import Path
from PIL import Image, ImageDraw
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
cid, out = sys.argv[1], sys.argv[2]
R = float(sys.argv[3]) if len(sys.argv) > 3 else 45.0
p = json.loads((HERE / "pano_index.json").read_text())["panoramas"][cid]
canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_43/canonical.world_ir.json").read_text())
from PIL import ImageFont
FONT = ImageFont.load_default(size=13)
PX = 500; S = PX / (2 * R)
cx, cy, hd = p["E"], p["N"], p["heading_deg"]
to = lambda q: (PX / 2 + (q[0] - cx) * S, PX / 2 - (q[1] - cy) * S)
img = Image.new("RGB", (PX, PX), (14, 14, 18)); d = ImageDraw.Draw(img)
for f in canon["canonical_features"]:
    g = f.get("geometry") or {}
    cs = g.get("coordinates") or []
    if g.get("kind") == "polyline" and any(abs(q[0] - cx) < R and abs(q[1] - cy) < R for q in cs):
        d.line([to(q) for q in cs], fill=(70, 70, 80), width=1)
for f in canon["canonical_features"]:
    if f.get("semantic_class") != "building_footprint":
        continue
    ring = f["geometry"]["coordinates"]
    if not any(abs(q[0] - cx) < R and abs(q[1] - cy) < R for q in ring):
        continue
    d.line([to(q) for q in ring], fill=(230, 200, 70), width=2)
    mx = sum(q[0] for q in ring[:-1]) / (len(ring) - 1); my = sum(q[1] for q in ring[:-1]) / (len(ring) - 1)
    lab = f["feature_id"].split("/")[-1]
    lab = lab[-6:] if not lab.startswith("bldg_") else "P" + lab[5:9]
    d.text(to((mx, my)), lab, fill=(255, 255, 255), font=FONT)
for b in range(0, 360, 30):
    r = math.radians(b)
    e = (cx + R * 0.95 * math.sin(r), cy + R * 0.95 * math.cos(r))
    d.line([to((cx, cy)), to(e)], fill=(0, 90, 120), width=1)
    x = (0.5 + ((b - hd + 180) % 360 - 180) / 360.0) % 1.0
    d.text(to(e), "%d° x=%.2f" % (b, x), fill=(0, 220, 255), font=FONT)
q = to((cx, cy)); d.ellipse((q[0] - 5, q[1] - 5, q[0] + 5, q[1] + 5), fill=(0, 230, 255))
d.text((6, 6), "%s heading %.1f  (1 px = %.2f m)" % (cid, hd, 1 / S), fill=(255, 255, 255))
img.save(out); print(out)
