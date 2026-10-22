"""sv_overlay.py SHOT.png URL X0 Y0 X1 Y1 OUT.png [RADIUS_M] -- reading aid only (decides nothing).

SHOT is a screen capture of the operator's Chrome window showing Google Street View; URL is the
tab's URL after load (it carries the pano position '@lat,lon,' and the view '<fov>y,<heading>h,<tilt>t').
X0 Y0 X1 Y1 is the Street View canvas rectangle inside SHOT (pixels, chosen by eye). Draws each nearby
building footprint's bearing span as a bracket (id tail + distance) for a pinhole view of that heading
and horizontal FOV across the canvas width, plus a 100-px grid in SHOT pixels for sample_px.py. Also
prints the pano position in EPSG:32654 and the spans."""
import json, math, re, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
from pyproj import Transformer
_T = Transformer.from_crs("EPSG:4326", "EPSG:32654", always_xy=True)
ll_to_utm = lambda lon, lat: _T.transform(lon, lat)
shot, url = sys.argv[1], sys.argv[2]
X0, Y0, X1, Y1 = (int(v) for v in sys.argv[3:7])
out = sys.argv[7]
R = float(sys.argv[8]) if len(sys.argv) > 8 else 45.0
m = re.search(r"@(-?[\d.]+),(-?[\d.]+),[\d.]+a,([\d.]+)y,([\d.]+)h", url)
lat, lon, fov, hd = (float(v) for v in m.groups())
cE, cN = ll_to_utm(lon, lat)
canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_46/canonical.world_ir.json").read_text())
im = Image.open(shot).convert("RGB")
d = ImageDraw.Draw(im); F = ImageFont.load_default(size=28)
W = X1 - X0
f = (W / 2) / math.tan(math.radians(fov / 2))


def xof(q):
    rel = ((math.degrees(math.atan2(q[0] - cE, q[1] - cN)) - hd + 180) % 360) - 180
    return None if abs(rel) >= 80 else X0 + W / 2 + f * math.tan(math.radians(rel))


rows = []
for ft in canon["canonical_features"]:
    if ft.get("semantic_class") != "building_footprint":
        continue
    ring = ft["geometry"]["coordinates"][:-1]
    dist = min(math.dist((cE, cN), q) for q in ring)
    if dist > R:
        continue
    xs = [x for x in (xof(q) for q in ring) if x is not None]
    if not xs:
        continue
    lo, hi = max(min(xs), X0 - 40), min(max(xs), X1 + 40)
    if hi < X0 or lo > X1:
        continue
    rows.append((dist, ft["feature_id"], lo, hi))
rows.sort()
for k, (dist, fid, lo, hi) in enumerate(rows):
    lab = fid.split("/")[-1]; lab = lab[-6:] if not lab.startswith("bldg_") else "P" + lab[5:9]
    y = Y0 + 10 + (k % 10) * 34
    col = (255, 230, 60) if dist < 15 else ((120, 255, 120) if dist < 28 else (120, 200, 255))
    d.line([(lo, y + 24), (hi, y + 24)], fill=col, width=4)
    d.line([(lo, y + 14), (lo, y + 34)], fill=col, width=4); d.line([(hi, y + 14), (hi, y + 34)], fill=col, width=4)
    d.text((min(max((lo + hi) / 2, X0), X1 - 160), y - 6), "%s %.0fm" % (lab, dist), fill=col, font=F)
for gx in range(X0 - X0 % 100, X1, 100):
    d.line([(gx, Y1 - 20), (gx, Y1)], fill=(255, 255, 0), width=2); d.text((gx + 2, Y1 - 50), str(gx), fill=(255, 255, 0), font=F)
for gy in range(Y0 - Y0 % 100, Y1, 100):
    d.line([(X0, gy), (X0 + 20, gy)], fill=(255, 255, 0), width=2); d.text((X0 + 24, gy - 14), str(gy), fill=(255, 255, 0), font=F)
im.save(out)
print("pano E %.2f N %.2f heading %.1f fov %.1f" % (cE, cN, hd, fov))
for dist, fid, lo, hi in rows[:14]:
    print("  %-48s %5.1f m  x %d-%d" % (fid, dist, lo, hi))
