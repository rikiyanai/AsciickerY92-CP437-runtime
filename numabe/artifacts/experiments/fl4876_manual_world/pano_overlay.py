"""pano_overlay.py CAPTURE_ID OFFSET_DEG OUT.png [RADIUS_M] -- reading aid only (decides nothing).

Draws, on the panorama, each nearby building's bearing span (top bracket, labelled with the tail
of its id and its distance) for a heading of (metadata heading + OFFSET_DEG). The person aligning
chooses OFFSET_DEG by eye until known landmarks sit under their brackets."""
import json, math, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
cid, out = sys.argv[1], sys.argv[3]
off = float(sys.argv[2].split(",")[0])
R = float(sys.argv[4]) if len(sys.argv) > 4 else 40.0
p = dict(json.loads((HERE / "pano_index.json").read_text())["panoramas"][cid])
# optional hand camera shift: OFFSET may be "deg" or "deg,dE,dN" (metres), for frames whose GPS is off
if "," in sys.argv[2]:
    _o, _de, _dn = (float(v) for v in sys.argv[2].split(","))
    off = _o; p["E"] += _de; p["N"] += _dn
canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_43/canonical.world_ir.json").read_text())
im = Image.open(ROOT / p["local_path"]).convert("RGB").resize((1024, 512))
W, H = im.size
d = ImageDraw.Draw(im); F = ImageFont.load_default(size=14)
hd = p["heading_deg"] + off
xof = lambda b: (0.5 + ((b - hd + 180) % 360 - 180) / 360.0) % 1.0 * W
rows = []
for f in canon["canonical_features"]:
    if f.get("semantic_class") != "building_footprint":
        continue
    ring = f["geometry"]["coordinates"][:-1]
    dist = min(math.dist((p["E"], p["N"]), q) for q in ring)
    if dist > R:
        continue
    bs = [math.degrees(math.atan2(q[0] - p["E"], q[1] - p["N"])) % 360 for q in ring]
    c = math.degrees(math.atan2(sum(q[0] for q in ring) / len(ring) - p["E"], sum(q[1] for q in ring) / len(ring) - p["N"])) % 360
    rel = [((b - c + 180) % 360) - 180 for b in bs]
    lo, hi = c + min(rel), c + max(rel)
    rows.append((dist, f["feature_id"], lo, hi, c))
rows.sort()
for k, (dist, fid, lo, hi, c) in enumerate(rows):
    lab = fid.split("/")[-1]; lab = lab[-6:] if not lab.startswith("bldg_") else "P" + lab[5:9]
    y = 4 + (k % 9) * 16
    x0, x1, xc = xof(lo), xof(hi), xof(c)
    col = (255, 230, 60) if dist < 15 else ((120, 255, 120) if dist < 28 else (120, 200, 255))
    if x1 >= x0:
        d.line([(x0, y + 11), (x1, y + 11)], fill=col, width=2)
    else:
        d.line([(x0, y + 11), (W, y + 11)], fill=col, width=2); d.line([(0, y + 11), (x1, y + 11)], fill=col, width=2)
    d.line([(xc, y + 12), (xc, H * 0.62)], fill=col, width=1)
    d.text((xc + 2, y), "%s %.0fm" % (lab, dist), fill=col, font=F)
d.text((4, H - 14), "%s offset %+.0f" % (cid, off), fill=(255, 255, 255), font=F)
im.save(out)
stem = out[:-4]
for k in range(3):  # thirds display at native size
    im.crop((k * 342, 0, min(W, (k + 1) * 342), 330)).save("%s_%d.png" % (stem, k))
print(out, len(rows), "buildings")
