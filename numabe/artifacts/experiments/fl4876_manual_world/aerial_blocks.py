"""aerial_blocks.py -- reading aid only: footprint outlines + id tails on the GSI aerial mosaic, cut
into 192x192-px blocks (shown x2) with a grid in mosaic pixel coordinates; the dot inside each
footprint is a SUGGESTED roof sample point that the person accepts or moves by eye. Writes
aerial/blocks/block_RR_CC.png and aerial/footprints_px.json (feature id -> outline px, dot px)."""
import json, math, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
from fetch_aerial import utm_to_ll, tile_xy  # noqa: E402  (re-runs nothing: tiles are cached)
A = HERE / "aerial"
meta = json.loads((A / "mosaic.json").read_text())
canon = json.loads((ROOT / ".run/fl4876_builds/build_reg_43/canonical.world_ir.json").read_text())
mos = Image.open(A / "mosaic.jpg").convert("RGB")
ov = mos.copy(); d = ImageDraw.Draw(ov); F = ImageFont.load_default(size=9)


def px(E, N):
    lo, la = utm_to_ll(E, N)
    x, y = tile_xy(lo, la)
    return (x - meta["tile_x0"]) * 256, (y - meta["tile_y0"]) * 256


def inside(p, ring):
    c = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > p[1]) != (y2 > p[1]) and p[0] < x1 + (p[1] - y1) * (x2 - x1) / (y2 - y1):
            c = not c
    return c


fp = {}
for f in canon["canonical_features"]:
    if f.get("semantic_class") != "building_footprint":
        continue
    ring = [px(*q[:2]) for q in f["geometry"]["coordinates"]]
    cx = sum(q[0] for q in ring[:-1]) / (len(ring) - 1); cy = sum(q[1] for q in ring[:-1]) / (len(ring) - 1)
    dot = (cx, cy)
    if not inside(dot, ring):  # nudge toward the nearest vertex until inside
        for k in range(1, 20):
            for q in ring[:-1]:
                t = (q[0] + (cx - q[0]) * (1 - k / 20.0), q[1] + (cy - q[1]) * (1 - k / 20.0))
                if inside(t, ring):
                    dot = t; break
            if dot != (cx, cy):
                break
    fp[f["feature_id"]] = {"outline": [[round(a, 1), round(b, 1)] for a, b in ring], "dot": [round(dot[0], 1), round(dot[1], 1)]}
    d.line(ring, fill=(255, 230, 0), width=1)
    d.ellipse((dot[0] - 1.5, dot[1] - 1.5, dot[0] + 1.5, dot[1] + 1.5), fill=(255, 0, 255))
    lab = f["feature_id"].split("/")[-1]; lab = lab[-4:] if not lab.startswith("bldg_") else "P" + lab[5:8]
    d.text((dot[0] + 2, dot[1] - 4), lab, fill=(255, 255, 255), font=F)
(A / "footprints_px.json").write_text(json.dumps(fp))
B = A / "blocks"; B.mkdir(exist_ok=True)
S, K = 128, 2.5
n = 0
for old in B.glob("block_*.png"):
    old.unlink()
for r in range(0, ov.height, S):
    for c in range(0, ov.width, S):
        blk = mos.crop((c, r, c + S, r + S)).resize((int(S * K), int(S * K)), Image.LANCZOS)
        dd = ImageDraw.Draw(blk); G = ImageFont.load_default(size=11)
        for fid, v in fp.items():
            ring = [((q[0] - c) * K, (q[1] - r) * K) for q in v["outline"]]
            if not any(0 <= q[0] < S * K and 0 <= q[1] < S * K for q in ring):
                continue
            dd.line(ring, fill=(255, 230, 0), width=1)
            dx, dy = (v["dot"][0] - c) * K, (v["dot"][1] - r) * K
            dd.ellipse((dx - 2, dy - 2, dx + 2, dy + 2), fill=(255, 0, 255))
            lab = fid.split("/")[-1]; lab = lab[-4:] if not lab.startswith("bldg_") else "P" + lab[5:8]
            dd.text((dx + 3, dy - 6), lab, fill=(255, 255, 255), font=G)
        for g in range(0, S, 32):
            dd.text((g * K + 1, 1), str(c + g), fill=(0, 255, 255), font=G)
            dd.text((1, g * K + 1), str(r + g), fill=(0, 255, 255), font=G)
        blk.save(B / ("block_%02d_%02d.png" % (r // S, c // S))); n += 1
print(len(fp), "footprints;", n, "blocks")
