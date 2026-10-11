#!/usr/bin/env python3
"""Assemble render_harness PNG frames into GIFs (Pillow).

  make_gif.py single <frames_dir> <out.gif> [--fps 30]
  make_gif.py combine <out.gif> <label>=<frames_dir> [...] [--fps 30]
              [--font assets/fonts/cp437_16x16.png]

Frames are mapped onto the xterm 6x6x6 cube palette the renderer outputs
(0.2-step levels, as the GL/web cell shaders decode them), without dithering,
so colours are exact.  GIF delays are centiseconds; 30 fps is emitted as a
repeating 3/3/4 cs cadence (average 33.3 ms).  `combine` places clips side by
side and draws each label with the CP437 font atlas.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]


def _cube_palette_image() -> Image.Image:
    pal = []
    for i in range(216):
        r, g, b = i // 36, (i // 6) % 6, i % 6
        pal += [r * 51, g * 51, b * 51]
    pal += [0, 0, 0] * (256 - 216)
    img = Image.new("P", (1, 1))
    img.putpalette(pal)
    return img


def _durations(n: int, fps: int) -> list[int]:
    out, acc = [], 0.0
    for i in range(n):
        target = (i + 1) * 1000.0 / fps
        d = int(round((target - acc) / 10.0)) * 10
        d = max(d, 20)
        out.append(d)
        acc += d
    return out


def _load_frames(d: Path) -> list[Image.Image]:
    files = sorted(d.glob("frame_*.png"))
    if not files:
        sys.exit(f"no frame_*.png in {d}")
    return [Image.open(f).convert("RGB") for f in files]


def _save(frames: list[Image.Image], out: Path, fps: int) -> None:
    pal = _cube_palette_image()
    q = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]
    out.parent.mkdir(parents=True, exist_ok=True)
    q[0].save(out, save_all=True, append_images=q[1:], duration=_durations(len(q), fps),
              loop=0, optimize=False, disposal=1)
    print(f"wrote {out} ({len(q)} frames, {frames[0].size[0]}x{frames[0].size[1]})")


def _draw_label(img: Image.Image, text: str, font: Image.Image, x: int, y: int) -> None:
    cw, ch = font.size[0] // 16, font.size[1] // 16
    for i, c in enumerate(text.encode("cp437", "replace")):
        gx, gy = (c % 16) * cw, (c // 16) * ch
        glyph = font.crop((gx, gy, gx + cw, gy + ch)).convert("L")
        img.paste((255, 255, 255), (x + i * cw, y, x + (i + 1) * cw, y + ch), glyph)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("single")
    s.add_argument("frames", type=Path)
    s.add_argument("out", type=Path)
    s.add_argument("--fps", type=int, default=30)
    c = sub.add_parser("combine")
    c.add_argument("out", type=Path)
    c.add_argument("clips", nargs="+")
    c.add_argument("--fps", type=int, default=30)
    c.add_argument("--font", type=Path, default=REPO_ROOT / "assets/fonts/cp437_16x16.png")
    a = ap.parse_args()

    if a.cmd == "single":
        _save(_load_frames(a.frames), a.out, a.fps)
        return 0

    font = Image.open(a.font).convert("RGB")
    ch = font.size[1] // 16
    clips = []
    for spec in a.clips:
        label, _, d = spec.partition("=")
        clips.append((label, _load_frames(Path(d))))
    n = min(len(f) for _, f in clips)
    w, h = clips[0][1][0].size
    frames = []
    for i in range(n):
        canvas = Image.new("RGB", (w * len(clips), h + ch), (0, 0, 0))
        for k, (label, fr) in enumerate(clips):
            canvas.paste(fr[i], (k * w, ch))
            _draw_label(canvas, label, font, k * w + (w - len(label) * (font.size[0] // 16)) // 2, 0)
        frames.append(canvas)
    _save(frames, a.out, a.fps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
