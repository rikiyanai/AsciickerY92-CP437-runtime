# Ad hoc script: Render final_render_cell_dump cells.jsonl to a PNG (center_rgb per screen_cell) for FL-4273 night/beam visual review
# Created: 2026-06-13
# Canonical gap: <describe what tool should own this>

import json, sys
from pathlib import Path
import numpy as np
from PIL import Image

def render(dump_dir, out_png, scale=6):
    dump_dir = Path(dump_dir)
    meta = json.load(open(dump_dir/"metadata.json"))
    ansi = meta.get("ansi_size", {})
    W = int(ansi.get("w", 0)); H = int(ansi.get("h", 0))
    img = np.zeros((H, W, 3), dtype=np.uint8)
    for line in open(dump_dir/"cells.jsonl"):
        r = json.loads(line)
        sc = r.get("screen_cell", {})
        x = sc.get("x"); y = sc.get("y")
        if x is None or y is None or not (0 <= x < W and 0 <= y < H):
            continue
        c = r.get("center_rgb") or r.get("avg_rgb") or r.get("bg_rgb") or {}
        img[y, x] = [int(c.get("r",0)), int(c.get("g",0)), int(c.get("b",0))]
    im = Image.fromarray(img, "RGB").resize((W*scale, H*scale), Image.NEAREST)
    im.save(out_png)
    print("wrote", out_png, f"{W}x{H} ->", im.size)

if __name__ == "__main__":
    render(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 6)
