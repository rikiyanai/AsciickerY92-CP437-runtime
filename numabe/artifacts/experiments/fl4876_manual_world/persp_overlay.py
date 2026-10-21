"""persp_overlay.py CAPTURE CENTER_BEARING_DEG HFOV_DEG OUT.png [RADIUS_M] [dE dN | snap] -- reading aid
only (decides nothing). Promoted from the group_00 street pass scratch copy.

Many captures are perspective photos (1024x768), not equirectangular panoramas. This draws, on the
native photo, each nearby building's projected span for a pinhole camera whose image centre points
at CENTER_BEARING (chosen by eye) with horizontal field of view HFOV (chosen by eye; ~65 for a phone
main camera). The grid is labelled in the 1024x512 frame that sample.py uses (x same, y = native_y/1.5).
Also writes OUT_map.png: plan map with the view wedge, building tails, rail/road lines, poles, and
OUT_0/1/2.png thirds at native size (displayed x + 342*k = photo x)."""
import json, math, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
cid, B, FOV, out = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
R = float(sys.argv[5]) if len(sys.argv) > 5 else 60.0
p = json.loads((HERE / "pano_index.json").read_text())["panoramas"][cid]
canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_43/canonical.world_ir.json").read_text())
im = Image.open(ROOT / p["local_path"]).convert("RGB").resize((1024, 768))
W, H = im.size
d = ImageDraw.Draw(im); F = ImageFont.load_default(size=14)
f = (W / 2) / math.tan(math.radians(FOV / 2))
cx, cy = p["E"], p["N"]
if len(sys.argv) > 7:  # camera position shift (dE, dN) in metres, chosen by eye
    cx += float(sys.argv[6]); cy += float(sys.argv[7])
elif len(sys.argv) > 6 and sys.argv[6] == "snap":  # put a cab-view camera on the nearest Tamagawa-line centreline
    best = None
    for ft in canon["canonical_features"]:
        if ft.get("semantic_class") == "railway_boundary" and (ft.get("labels") or {}).get("name:en") == "Tokyu Tamagawa Line":
            cs = ft["geometry"]["coordinates"]
            for a, b in zip(cs, cs[1:]):
                vx, vy = b[0] - a[0], b[1] - a[1]; L = vx * vx + vy * vy or 1e-9
                t = max(0, min(1, ((cx - a[0]) * vx + (cy - a[1]) * vy) / L))
                q = (a[0] + t * vx, a[1] + t * vy); dd = math.dist(q, (cx, cy))
                if best is None or dd < best[0]:
                    best = (dd, q, ft["feature_id"])
    print("snap shift dE %.1f dN %.1f to %s" % (best[1][0] - cx, best[1][1] - cy, best[2]))
    cx, cy = best[1]


def xof(q):
    b = math.degrees(math.atan2(q[0] - cx, q[1] - cy))
    rel = ((b - B + 180) % 360) - 180
    if abs(rel) >= 85:
        return None
    return W / 2 + f * math.tan(math.radians(rel))


rows = []
for ft in canon["canonical_features"]:
    if ft.get("semantic_class") != "building_footprint":
        continue
    ring = ft["geometry"]["coordinates"][:-1]
    dist = min(math.dist((cx, cy), q) for q in ring)
    if dist > R:
        continue
    xs = [xof(q) for q in ring]
    xs = [x for x in xs if x is not None]
    if not xs:
        continue
    lo, hi = max(min(xs), -50), min(max(xs), W + 50)
    if hi < 0 or lo > W:
        continue
    rows.append((dist, ft["feature_id"], lo, hi))
rows.sort()
for k, (dist, fid, lo, hi) in enumerate(rows):
    lab = fid.split("/")[-1]; lab = lab[-6:] if not lab.startswith("bldg_") else "P" + lab[5:9]
    y = 4 + (k % 12) * 16
    col = (255, 230, 60) if dist < 15 else ((120, 255, 120) if dist < 28 else (120, 200, 255))
    d.line([(lo, y + 11), (hi, y + 11)], fill=col, width=2)
    d.line([(lo, y + 6), (lo, y + 16)], fill=col, width=2); d.line([(hi, y + 6), (hi, y + 16)], fill=col, width=2)
    xm = min(max((lo + hi) / 2, 0), W - 70)
    d.text((xm, y - 2), "%s %.0fm" % (lab, dist), fill=col, font=F)
for gx in range(0, W, 50):
    d.line([(gx, H - 10), (gx, H)], fill=(255, 255, 0)); d.text((gx + 1, H - 26), str(gx), fill=(255, 255, 0), font=F)
for gy in range(50, 512, 50):
    Y = gy * 1.5; d.line([(0, Y), (8, Y)], fill=(255, 255, 0)); d.text((10, Y - 7), str(gy), fill=(255, 255, 0), font=F)
d.text((300, H - 44), "%s bearing %.0f fov %.0f" % (cid, B, FOV), fill=(255, 255, 255), font=F)
im.save(out)
for k in range(3):
    im.crop((k * 342, 0, min(W, (k + 1) * 342), 300)).save("%s_%d.png" % (out[:-4], k))
PX = 400; S = PX / (2 * R)
to = lambda q: (PX / 2 + (q[0] - cx) * S, PX / 2 - (q[1] - cy) * S)
mp = Image.new("RGB", (PX, PX), (14, 14, 18)); md = ImageDraw.Draw(mp)
FS = ImageFont.load_default(size=11)
for ft in canon["canonical_features"]:
    g = ft.get("geometry") or {}
    cs = g.get("coordinates") or []
    sc = ft.get("semantic_class")
    if g.get("kind") == "polyline" and any(abs(q[0] - cx) < R and abs(q[1] - cy) < R for q in cs):
        col = (200, 80, 200) if sc == "railway_boundary" else (80, 80, 95)
        md.line([to(q) for q in cs], fill=col, width=2 if sc == "railway_boundary" else 1)
    if sc == "building_footprint" and any(abs(q[0] - cx) < R and abs(q[1] - cy) < R for q in cs):
        md.line([to(q) for q in cs], fill=(230, 200, 70), width=2)
        mx = sum(q[0] for q in cs[:-1]) / (len(cs) - 1); my = sum(q[1] for q in cs[:-1]) / (len(cs) - 1)
        lab = ft["feature_id"].split("/")[-1]; lab = lab[-6:] if not lab.startswith("bldg_") else "P" + lab[5:9]
        md.text(to((mx, my)), lab, fill=(255, 255, 255), font=F)
    if sc == "utility_pole" and g.get("kind") == "point":
        q = cs if isinstance(cs[0], (int, float)) else cs[0]
        if abs(q[0] - cx) < R and abs(q[1] - cy) < R:
            t = to(q); md.ellipse((t[0] - 2, t[1] - 2, t[0] + 2, t[1] + 2), fill=(255, 90, 90))
    if sc == "crossing" and g.get("kind") == "point" and abs(cs[0] - cx) < R and abs(cs[1] - cy) < R:
        t = to(cs); md.rectangle((t[0] - 3, t[1] - 3, t[0] + 3, t[1] + 3), outline=(0, 255, 0))
    if sc == "place" and (ft.get("labels") or {}).get("name:en") and abs(cs[0] - cx) < R and abs(cs[1] - cy) < R:
        t = to(cs); md.text(t, ft["labels"]["name:en"][:10], fill=(255, 140, 255), font=FS)
for a in (B - FOV / 2, B, B + FOV / 2):
    r = math.radians(a)
    md.line([to((cx, cy)), to((cx + R * math.sin(r), cy + R * math.cos(r)))], fill=(0, 200, 255), width=1)
q = to((cx, cy)); md.ellipse((q[0] - 5, q[1] - 5, q[0] + 5, q[1] + 5), fill=(0, 230, 255))
md.text((6, 6), "%s  meta heading %.1f  view %.0f  1px=%.2fm" % (cid, p["heading_deg"], B, 1 / S), fill=(255, 255, 255), font=F)
mp.save(out[:-4] + "_map.png")
print(out, len(rows), "buildings in view:", ", ".join("%s@%.0fm[%d-%d]" % (r[1].split('/')[-1][-6:], r[0], r[2], r[3]) for r in rows[:14]))
