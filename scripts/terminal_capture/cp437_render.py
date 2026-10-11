#!/usr/bin/env python3
"""Render captured terminal frames (ANSI text from `tmux capture-pane -p -e`)
into images/GIF using the repo's CP437 font atlas.

Each frame is replayed into a fresh `pyte` screen so SGR colours (16, 256 and
truecolor) and the client's UTF-8 CP437 glyphs are parsed properly; every cell
is then drawn as an atlas glyph (white-on-black mask) tinted with its fg/bg.

Usage:
  python3 scripts/terminal_capture/cp437_render.py FRAMES_DIR OUT.gif \
      [--cols 160 --rows 50 --cell 8 --atlas assets/fonts/cp437_16x16.png]

FRAMES_DIR holds frame_00000.ans ... and an optional timings.json
([seconds_since_start, ...]) used for per-frame GIF durations.
"""
import argparse
import json
import os
import sys

try:
    import pyte
except ImportError:  # pragma: no cover
    sys.exit("pyte is required: pip install pyte")
from PIL import Image

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# CP437 0x00-0x1F and 0x7F as the client emits them
# (platform/backend/host_terminal_ansi.cpp CP437[] table).
_LOW = [0x0020, 0x263A, 0x263B, 0x2665, 0x2666, 0x2663, 0x2660, 0x2022,
        0x25D8, 0x25CB, 0x25D9, 0x2642, 0x2640, 0x266A, 0x266B, 0x263C,
        0x25BA, 0x25C4, 0x2195, 0x203C, 0x00B6, 0x00A7, 0x25AC, 0x21A8,
        0x2191, 0x2193, 0x2192, 0x2190, 0x221F, 0x2194, 0x25B2, 0x25BC]


def unicode_to_cp437():
    table = {}
    for b in range(0x80, 0x100):
        table[bytes([b]).decode("cp437")] = b
    for b in range(0x20, 0x7F):
        table[chr(b)] = b
    for b, cp in enumerate(_LOW):
        table.setdefault(chr(cp), b)
    table[chr(0x2302)] = 0x7F
    table[" "] = 0x20
    table[" "] = 0xFF
    return table


U2CP = unicode_to_cp437()

_NAMED = {
    "black": (0, 0, 0), "red": (205, 0, 0), "green": (0, 205, 0),
    "brown": (205, 205, 0), "yellow": (205, 205, 0), "blue": (0, 0, 238),
    "magenta": (205, 0, 205), "cyan": (0, 205, 205), "white": (229, 229, 229),
    "brightblack": (127, 127, 127), "brightred": (255, 0, 0),
    "brightgreen": (0, 255, 0), "brightbrown": (255, 255, 0),
    "brightyellow": (255, 255, 0), "brightblue": (92, 92, 255),
    "brightmagenta": (255, 0, 255), "brightcyan": (0, 255, 255),
    "brightwhite": (255, 255, 255),
}
DEFAULT_FG = (229, 229, 229)
DEFAULT_BG = (0, 0, 0)


def colour(value, default):
    if not value or value == "default":
        return default
    if value in _NAMED:
        return _NAMED[value]
    if len(value) == 6:
        try:
            return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            pass
    return default


class Atlas:
    def __init__(self, path, cell):
        im = Image.open(path).convert("L")
        gw, gh = im.width // 16, im.height // 16
        self.cell = cell
        self.masks = []
        for i in range(256):
            g = im.crop(((i % 16) * gw, (i // 16) * gh,
                         (i % 16 + 1) * gw, (i // 16 + 1) * gh))
            if cell != gw:
                g = g.resize((cell, cell), Image.NEAREST)
            # binarize so every pixel is exactly fg or bg -> exact GIF palette
            self.masks.append(g.point(lambda v: 255 if v >= 128 else 0))


def parse_frame(text, cols, rows):
    screen = pyte.Screen(cols, rows)
    stream = pyte.Stream(screen)
    # capture-pane separates rows with bare LF and carries SGR state across
    # rows (it only emits attribute *changes*), so feed one continuous stream
    # with CR+LF and never reset attributes between rows.
    stream.feed(text.replace("\r", "").replace("\n", "\r\n"))
    return screen


def render_screen(screen, atlas, cols, rows):
    c = atlas.cell
    img = Image.new("RGB", (cols * c, rows * c), DEFAULT_BG)
    fg_cache = {}
    for y in range(rows):
        line = screen.buffer[y]
        for x in range(cols):
            ch = line[x]
            fg = colour(ch.fg, DEFAULT_FG)
            bg = colour(ch.bg, DEFAULT_BG)
            if ch.reverse:
                fg, bg = bg, fg
            code = U2CP.get(ch.data or " ", ord("?"))
            box = (x * c, y * c)
            img.paste(bg, box + (box[0] + c, box[1] + c))
            if code not in (0, 0x20, 0xFF):
                key = fg
                if key not in fg_cache:
                    fg_cache[key] = Image.new("RGB", (c, c), fg)
                img.paste(fg_cache[key], box, atlas.masks[code])
    return img


def render_dir(frames_dir, out, cols, rows, cell, atlas_path, fps_hint=15,
               max_frames=None, png_every=0):
    atlas = Atlas(atlas_path, cell)
    names = sorted(n for n in os.listdir(frames_dir) if n.endswith(".ans"))
    if max_frames:
        names = names[:max_frames]
    if not names:
        raise SystemExit("no frames in %s" % frames_dir)
    timings = None
    tpath = os.path.join(frames_dir, "timings.json")
    if os.path.exists(tpath):
        with open(tpath) as f:
            timings = json.load(f)
    frames = []
    for i, n in enumerate(names):
        with open(os.path.join(frames_dir, n), encoding="utf-8",
                  errors="replace") as f:
            img = render_screen(parse_frame(f.read(), cols, rows), atlas,
                                cols, rows)
        if png_every and i % png_every == 0:
            img.save(os.path.join(frames_dir, n[:-4] + ".png"))
        frames.append(img)
    durations = []
    for i in range(len(frames)):
        if timings and i + 1 < len(timings):
            d = (timings[i + 1] - timings[i]) * 1000.0
        else:
            d = 1000.0 / fps_hint
        durations.append(max(20, int(round(d / 10.0)) * 10))
    # Quantize all frames against one shared adaptive palette built from a
    # mosaic of sampled frames; with binarized glyphs every pixel is an exact
    # fg/bg colour, so this is lossless whenever <=256 colours are in use.
    sample = frames[::max(1, len(frames) // 16)]
    w, h = frames[0].size
    mosaic = Image.new("RGB", (w, h * len(sample)))
    for i, f in enumerate(sample):
        mosaic.paste(f, (0, i * h))
    pal = mosaic.quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    q = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    q[0].save(out, save_all=True, append_images=q[1:], duration=durations,
              loop=0, optimize=False, disposal=1)
    return len(frames), sum(durations) / 1000.0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("frames_dir")
    ap.add_argument("out")
    ap.add_argument("--cols", type=int, default=160)
    ap.add_argument("--rows", type=int, default=50)
    ap.add_argument("--cell", type=int, default=8,
                    help="output pixels per cell (atlas is 16x16)")
    ap.add_argument("--atlas", default=os.path.join(
        REPO, "assets", "fonts", "cp437_16x16.png"))
    ap.add_argument("--fps", type=float, default=15,
                    help="frame rate used when timings.json is absent")
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--png-every", type=int, default=0,
                    help="also write every Nth frame as PNG next to the .ans")
    a = ap.parse_args(argv)
    n, secs = render_dir(a.frames_dir, a.out, a.cols, a.rows, a.cell, a.atlas,
                         a.fps, a.max_frames, a.png_every)
    print("wrote %s (%d frames, %.1fs)" % (a.out, n, secs))


if __name__ == "__main__":
    main()
