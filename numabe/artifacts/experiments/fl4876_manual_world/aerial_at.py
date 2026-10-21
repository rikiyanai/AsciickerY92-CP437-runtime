"""aerial_at.py E N R OUT.png [CAP ...] -- drawing aid only (decides nothing): the GSI z18 aerial mosaic
around E,N (radius R m, enlarged x4), with zone carriers outlined thinly (orange rails, white roads,
grey buildings), reviewed accepted fixtures (green dot + id tail), and the camera positions of the given
capture ids (cyan, with a capture-id tail). A 5 m tick grid in metres from E,N runs along the edges."""
import glob, json, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
from fetch_aerial import utm_to_ll, tile_xy  # noqa: E402
E0, N0, R, out = float(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
caps = sys.argv[5:]
meta = json.loads((HERE / "aerial" / "mosaic.json").read_text())
mos = Image.open(HERE / "aerial" / "mosaic.jpg").convert("RGB")


def px(E, N):
    lo, la = utm_to_ll(E, N)
    x, y = tile_xy(lo, la)
    return (x - meta["tile_x0"]) * 256, (y - meta["tile_y0"]) * 256


c = px(E0, N0); ex = px(E0 + R, N0); mpp = R / (ex[0] - c[0])
r = R / mpp
K = 4
box = (int(c[0] - r), int(c[1] - r), int(c[0] + r), int(c[1] + r))
im = mos.crop(box).resize(((box[2] - box[0]) * K, (box[3] - box[1]) * K), Image.LANCZOS)
d = ImageDraw.Draw(im); F = ImageFont.load_default(size=12)
to = lambda E, N: ((px(E, N)[0] - box[0]) * K, (px(E, N)[1] - box[1]) * K)
inside = lambda q: abs(q[0] - E0) < R and abs(q[1] - N0) < R
zones = json.loads((ROOT / ".run/fl4876_builds/build_reg_43/observation_registration/placement_zones.json").read_text())
for z in zones:
    pl = z.get("polyline_epsg32654") or []
    if not any(inside(q) for q in pl):
        continue
    cl = str(z.get("carrier_class"))
    col = (150, 150, 160) if cl == "building" else ((255, 140, 0) if cl.startswith("rail") else
                                                   ((60, 140, 255) if cl.startswith("water") else (255, 255, 255)))
    d.line([to(*q[:2]) for q in pl], fill=col, width=1)
for p in sorted(glob.glob(str(HERE / "assess" / "fixture_review_*.json"))):
    for fid, rv in json.loads(Path(p).read_text())["reviews"].items():
        if rv.get("status") == "accepted" and inside((rv["E"], rv["N"])):
            x, y = to(rv["E"], rv["N"]); d.ellipse((x - 4, y - 4, x + 4, y + 4), outline=(0, 255, 80), width=2)
            d.text((x + 5, y - 6), fid[-4:], fill=(0, 255, 80), font=F)
pano = json.loads((HERE / "pano_index.json").read_text())["panoramas"]
for cid in caps:
    k = next((k for k in pano if k.endswith(cid)), None)
    if k:
        x, y = to(pano[k]["E"], pano[k]["N"]); d.rectangle((x - 4, y - 4, x + 4, y + 4), outline=(0, 230, 255), width=2)
        d.text((x + 5, y + 2), cid[-6:], fill=(0, 230, 255), font=F)
W = im.size[0]
for m in range(-int(R), int(R) + 1, 5):
    x = W / 2 + m / mpp * K
    d.line([(x, 0), (x, 8)], fill=(255, 255, 0)); d.text((x + 2, 8), str(m), fill=(255, 255, 0), font=F)
    d.line([(0, W / 2 - m / mpp * K), (8, W / 2 - m / mpp * K)], fill=(255, 255, 0))
    d.text((10, W / 2 - m / mpp * K), str(m), fill=(255, 255, 0), font=F)
im.save(out); print(out, "m/px %.3f" % (mpp / K))
