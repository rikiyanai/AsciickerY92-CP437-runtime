"""pano_crop.py CAPTURE_ID B0 B1 OUT.png [SCALE] [V0 V1] -- crop an equirectangular capture between compass
bearings B0..B1 (pano_index heading = centre column), vertical fraction V0..V1 (default .25-.7), with bearing ticks
every 10 deg. Read-only."""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[3]
idx = json.loads((ROOT / "artifacts/experiments/fl4876_manual_world/pano_index.json").read_text())["panoramas"]
cid, b0, b1, out = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
sc = float(sys.argv[5]) if len(sys.argv) > 5 else 3
v0, v1 = (float(sys.argv[6]), float(sys.argv[7])) if len(sys.argv) > 7 else (0.25, 0.7)
p = idx[cid]
im = Image.open(ROOT / p["local_path"]).convert("RGB")
W, H = im.size


import os  # noqa: E402
OFF = float(os.environ.get("OFF", "0"))  # appearance-owner heading_offset_deg: true bearing of the centre = heading + OFF


def col(b):
    return (((b - p["heading_deg"] - OFF) / 360.0 + 0.5) % 1.0) * W


x0, x1 = col(b0), col(b1)
if x1 < x0:
    x1 += W
    im2 = Image.new("RGB", (2 * W, H))
    im2.paste(im, (0, 0))
    im2.paste(im, (W, 0))
    im = im2
c = im.crop((int(x0), int(v0 * H), int(x1), int(v1 * H)))
c = c.resize((int(c.width * sc), int(c.height * sc)), Image.LANCZOS)
d = ImageDraw.Draw(c)
b = (int(b0) // 10 + 1) * 10
while b < b1:
    x = (col(b) - x0) % W * sc
    d.line([(x, 0), (x, 12)], fill=(255, 0, 0), width=2)
    d.text((x + 2, 12), str(b), fill=(255, 0, 0))
    b += 10
c.save(out)
print(out, c.size, "heading", p["heading_deg"], p["timestamp"])
