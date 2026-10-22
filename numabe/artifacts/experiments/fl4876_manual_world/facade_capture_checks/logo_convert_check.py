"""logo_convert_check.py IMAGE.png OUT_DIR [COLS_M ROWS_M] -- run facade_capture.js convertSprite (logo mode)
headless in Node on IMAGE (RGBA), with the cp437_8x8 glyph masks computed exactly as the page does, then render
the returned cells to OUT_DIR/<stem>_cells.png (checkerboard = transparent) and print the cell census.
Checks the converter only; the browser panel itself is not exercised."""
import base64
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path("/Users/r/Projects/asciicker-Y9-2")
UI = ROOT / "artifacts/experiments/fl4876_manual_world/map_editor_ui/facade_capture.js"
src, out = Path(sys.argv[1]), Path(sys.argv[2])
w_m, h_m = (float(sys.argv[3]), float(sys.argv[4])) if len(sys.argv) > 4 else (3.0, 1.0)
out.mkdir(parents=True, exist_ok=True)
img = Image.open(src).convert("RGBA")
font = Image.open(ROOT / "assets/fonts/cp437_8x8.png").convert("RGB")
gw = font.width // 16
m = []
for g in range(256):
    for py in range(8):
        for px in range(8):
            r, gg, b = font.getpixel(((g & 15) * gw + px * gw // 8, (g >> 4) * gw + py * gw // 8))
            m.append(((r * 2 + gg * 3 + b + 3) // 6) / 255)
job = out / "job.json"
job.write_text(json.dumps({"w": img.width, "h": img.height, "rgba": base64.b64encode(img.tobytes()).decode(),
                           "m": m, "band": {"kind": "sign", "mode": "sprite", "x0_m": 0, "x1_m": w_m, "y0_m": 1, "y1_m": 1 + h_m}}))
js = out / "run.js"
js.write_text(r"""
const fs = require('fs'), vm = require('vm');
const [ui, jobPath] = process.argv.slice(2), job = JSON.parse(fs.readFileSync(jobPath, 'utf8'));
const src = fs.readFileSync(ui, 'utf8').replace('return {open, close, onSelect,', 'return {convertSprite, open, close, onSelect,');
const ctx = {document: {getElementById: () => null}, S: {metaScale: 3.4213}, RT: {f: {}}, Module: {}, status() {}, api() {}, setTimeout, clearTimeout};
vm.createContext(ctx); vm.runInContext(src + ';globalThis.T=FC;', ctx);
const st = ctx.T.state, m = Float32Array.from(job.m), stats = {};
for (let g = 0; g < 256; g++) { let s1 = 0, s2 = 0; for (let k = 0; k < 64; k++) { const a = m[g * 64 + k]; s1 += a; s2 += a * a; }
  const S00 = 64 - 2 * s1 + s2, S01 = s1 - s2, S11 = s2; stats[g] = {s1, s2, S00, S01, S11, det: S00 * S11 - S01 * S01}; }
st.masks = {m, stats}; st.imgType = 'logo'; st.enc = 'blocks'; st.contrast = 1;
st.img = {width: job.w, height: job.h}; st.srcPixels = Uint8ClampedArray.from(Buffer.from(job.rgba, 'base64'));
const cells = ctx.T.convertSprite(job.band);
console.log(JSON.stringify(cells));
""")
r = subprocess.run(["node", str(js), str(UI), str(job)], capture_output=True, text=True)
if r.returncode:
    sys.exit(r.stderr[-2000:])
cells = json.loads(r.stdout)
(out / (src.stem + "_cells.json")).write_text(json.dumps(cells))
rows, cols = len(cells), len(cells[0])
pic = Image.new("RGB", (cols * 8, rows * 8))
census = {"transparent": 0, "half_with_transparent_side": 0, "opaque": 0}
for y, row in enumerate(cells):
    for x, c in enumerate(row):
        if c is None:
            census["transparent"] += 1
        elif c[2] is None:
            census["half_with_transparent_side"] += 1
        else:
            census["opaque"] += 1
        for py in range(8):
            for px in range(8):
                chk = 85 if ((px >> 2) + (py >> 2)) % 2 else 119
                if c is None:
                    col = (chk,) * 3
                else:
                    a = m[c[0] * 64 + py * 8 + px]
                    if c[2] is None:
                        col = tuple(c[1]) if a >= .5 else (chk,) * 3
                    else:
                        col = tuple(int(c[2][q] + a * (c[1][q] - c[2][q])) for q in range(3))
                pic.putpixel((x * 8 + px, y * 8 + py), col)
pic = pic.resize((pic.width * 3, pic.height * 3), Image.NEAREST)
dst = out / (src.stem + "_cells.png")
pic.save(dst)
print(json.dumps({"cells": [cols, rows], **census, "out": str(dst)}))
