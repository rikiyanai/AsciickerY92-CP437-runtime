#!/usr/bin/env python3
"""
2026-10-08 -- Synthetic offscreen render of UFO rapture frames (no browser)

Output: docs/previews/ufo/beam-frame.png

WHAT THIS IS (and is not)
  This is NOT the engine's Render() pipeline: no terrain rasterizer, no depth
  buffer, no camera. It composites a hand-made flat scene (sky + textured
  grass, CP437 cells) with the REAL pieces of this feature:
    * the death clip frame 0 from assets/sprites/plydie-body.xp (angle 0),
      raised by the server lift RaptureEval(t).body_z_offset (~12.3 z/row),
    * the beam cells, shaded by engine/rapture_beam.h itself: the script
      compiles scripts/test_rapture_beam.cpp and calls its --shade-grid mode,
    * the UFO sheet assets/sprites/ufo.xp at hover height (RAPTURE_UFO_HOVER_Z
      = 128 z ~= 10 rows above the foot), frame RaptureUfoFrame(t), with its
      swoosh emitter cells lightening what is beneath.
  Depth testing is skipped (the flat scene has nothing in front of the beam).
  Panels: t=58 (BEAM_OPEN, beam_len01 = 1/3) and t=96 (mid-LIFT).
"""
import gzip
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FONT = REPO / "assets" / "fonts" / "cp437_20x20.png"
OUT = REPO / "docs" / "previews" / "ufo" / "beam-frame.png"
KEY, MAGENTA, CYAN = (255, 255, 85), (255, 0, 255), (0, 255, 255)
Z_PER_ROW = 16.0 / (0.8660254 * 4.0 * 0.375)
HOVER_Z, LIFT_Z = 128.0, 72.0
W, H = 44, 30
FOOT = (22, 6)


def load_xp(path):
    raw = gzip.decompress(path.read_bytes())
    _, nl = struct.unpack_from("<iI", raw, 0)
    off, layers = 8, []
    for _ in range(nl):
        w, h = struct.unpack_from("<ii", raw, off)
        off += 8
        cells = [[None] * w for _ in range(h)]
        for x in range(w):
            for y in range(h):
                g, = struct.unpack_from("<I", raw, off)
                fg = tuple(raw[off + 4:off + 7])
                bg = tuple(raw[off + 7:off + 10])
                cells[y][x] = (g, fg, bg)
                off += 10
        layers.append(cells)
    return layers


def pal(rgb):
    r, g, b = ((v * 5 + 128) // 255 for v in rgb)
    return 16 + 36 * r + 6 * g + b


def rgb(p):
    p -= 16
    return (p // 36 * 51, (p // 6) % 6 * 51, p % 6 * 51)


def avg(cell):
    g, f, b = cell
    if g in (0, 32):
        return b
    if g == 219:
        return f
    fr, br = rgb(f), rgb(b)
    return pal(tuple((a + c) // 2 for a, c in zip(fr, br)))


def smoothstep(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def phase(t):
    if t < 54:
        return 0.0, 0.0
    if t < 66:
        return (t - 54) / 12.0, 0.0
    if t < 114:
        return 1.0, LIFT_Z * smoothstep((t - 66) / 48.0)
    return 0.0, LIFT_Z


def scene():
    """Bottom-row-first grid of (gl, fg_pal, bk_pal)."""
    grid = [[None] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            if y < 14:
                n = (x * 7 + y * 13 + (x * y) % 5) % 9
                dark, mid = pal((0, 102, 0)), pal((51, 153, 51))
                if n == 0:
                    grid[y][x] = (34, pal((102, 204, 51)), mid)    # tuft "
                elif n < 4:
                    grid[y][x] = (220, mid, dark)
                elif y == 13:
                    grid[y][x] = (223, mid, pal((102, 153, 204)))  # horizon
                else:
                    grid[y][x] = (32, mid, mid)
            else:
                sky = pal((51, 102, 153)) if y < 22 else pal((51, 51, 153))
                grid[y][x] = (32, sky, sky)
    return grid


def blit(grid, layers, x0, y0, fw, fh, ref_x, ref_y_cells, ax, ay, swoosh_ok):
    K, L = layers[0], layers[2]
    for fy in range(fh):          # fy = 0 is the TOP xp row
        for fx in range(fw):
            g, f, b = L[y0 + fy][x0 + fx]
            key = K[y0 + fy][x0 + fx][2]
            if b == MAGENTA:
                continue
            sx = ax - ref_x + fx
            sy = ay - ref_y_cells + (fh - 1 - fy)
            if not (0 <= sx < W and 0 <= sy < H):
                continue
            dg, dfg, dbk = grid[sy][sx]
            f_t, b_t = f == key, b == key
            if f_t and b_t:
                continue
            if swoosh_ok and b == CYAN:
                lt = lambda p: pal(tuple(min(255, c + 51) for c in rgb(p)))  # noqa: E731
                grid[sy][sx] = (g, pal(f), lt(avg(grid[sy][sx])))
                continue
            under = avg((dg, dfg, dbk))
            grid[sy][sx] = (g, under if f_t else pal(f), under if b_t else pal(b))


def build_harness(tmp):
    cxx = os.environ.get("CXX") or shutil.which("c++") or shutil.which("g++")
    exe = Path(tmp) / "beam"
    subprocess.run([cxx, "-std=c++17", "-O1", "-I", str(REPO / "engine"),
                    str(REPO / "scripts" / "test_rapture_beam.cpp"), "-o", str(exe)], check=True)
    return exe


def panel(exe, tmp, t, ufo, die, font):
    from PIL import Image
    len01, lift = phase(t)
    grid = scene()
    fx, fy = FOOT
    body_y = fy + int(round(lift / Z_PER_ROW))
    # plydie-body.xp: 11x11 frames, frame 0 angle 0 (lying, clip end), ref y 6 half
    blit(grid, die, 0, 0, 11, 11, 5, 3, fx, body_y, False)
    hull_y = fy + int(round(HOVER_Z / Z_PER_ROW))
    beam_rows = hull_y - fy
    gin, gout = Path(tmp) / "in.txt", Path(tmp) / "out.txt"
    with open(gin, "w") as f:
        f.write("%d %d\n" % (W, H))
        for y in range(H):
            for x in range(W):
                c = grid[y][x]
                f.write("%d %d %d %d\n" % (c[0], c[1], c[2], avg(c)))
    subprocess.run([str(exe), "--shade-grid", str(gin), str(gout), str(beam_rows),
                    "%.6f" % len01, str(t), "1234", str(fx), str(hull_y)], check=True)
    vals = gout.read_text().split()
    it = iter(vals[2:])
    for y in range(H):
        for x in range(W):
            grid[y][x] = (int(next(it)), int(next(it)), int(next(it)))
    frame = (t // 4) % 4
    blit(grid, ufo, frame * 13, 0, 13, 4, 6, 0, fx, hull_y, True)
    C = 20
    im = Image.new("RGB", (W * C, H * C))
    for y in range(H):
        for x in range(W):
            g, f, b = grid[y][x]
            sy = (H - 1 - y) * C
            im.paste(rgb(b), (x * C, sy, (x + 1) * C, sy + C))
            gm = font.crop(((g % 16) * C, (g // 16) * C, (g % 16 + 1) * C, (g // 16 + 1) * C))
            im.paste(rgb(f), (x * C, sy), gm)
    return im, len01


def main():
    from PIL import Image, ImageDraw, ImageFont
    font = Image.open(FONT).convert("L")
    ufo = load_xp(REPO / "assets" / "sprites" / "ufo.xp")
    die = load_xp(REPO / "assets" / "sprites" / "plydie-body.xp")
    try:
        lf = ImageFont.truetype("DejaVuSans.ttf", 16)
    except OSError:
        lf = ImageFont.load_default()
    with tempfile.TemporaryDirectory() as tmp:
        exe = build_harness(tmp)
        panels = [(t,) + panel(exe, tmp, t, ufo, die, font) for t in (58, 96)]
    pad, th = 16, 52
    out = Image.new("RGB", (pad + len(panels) * (W * 20 + pad), th + H * 20 + pad), (24, 24, 30))
    d = ImageDraw.Draw(out)
    d.text((pad, 6), "SYNTHETIC harness (flat hand-made scene, no engine Render()/depth): "
           "real rapture_beam.h shading + ufo.xp + plydie-body.xp", fill=(230, 230, 230), font=lf)
    for i, (t, im, len01) in enumerate(panels):
        x = pad + i * (W * 20 + pad)
        d.text((x, th - 22), "tick %d since death, beam_len01 %.2f" % (t, len01),
               fill=(200, 200, 120), font=lf)
        out.paste(im, (x, th))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.save(OUT)
    print("wrote", OUT.relative_to(REPO))


if __name__ == "__main__":
    sys.exit(main())
