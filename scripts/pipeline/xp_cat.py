#!/usr/bin/env python3
"""xp_cat.py -- dump a REXPaint .xp file to the terminal as ANSI true-color art.

Usage:
    python3 scripts/pipeline/xp_cat.py <file.xp>
    python3 scripts/pipeline/xp_cat.py <file.xp> --layer 0
    python3 scripts/pipeline/xp_cat.py <file.xp> --hb
    python3 scripts/pipeline/xp_cat.py <file.xp> --info

Modes:
    (default)  One terminal row per XP row. Glyphs + fg/bg colors.
    --hb       Half-block: two XP rows per terminal row, bg colors as pixels.
               Glyph content is ignored. Good for color-block art.

Layer selection:
    Default is the visual layer: index 2 if the file has >=3 layers, else last.
    Use --layer N to pick a specific index.

Info/errors go to stderr so piped output stays clean.
"""

import sys
import os
import argparse
import contextlib

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '..', '..'))

try:
    from scripts.pipeline.xp_core import XPFile
except ImportError:
    from xp_core import XPFile  # type: ignore

_TRANSPARENT = (255, 0, 255)
_RESET = '\033[0m'


def _fg(r, g, b):
    return f'\033[38;2;{r};{g};{b}m'


def _bg(r, g, b):
    return f'\033[48;2;{r};{g};{b}m'


def _char(glyph: int) -> str:
    if glyph == 0:
        return ' '
    try:
        return bytes([glyph & 0xFF]).decode('cp437')
    except Exception:
        return '?'


def render_normal(layer) -> None:
    """One terminal row per XP row. Glyphs + true-color fg/bg."""
    out = []
    for y in range(layer.height):
        row = []
        for x in range(layer.width):
            glyph, fg, bg = layer.data[y][x]
            ch = _char(glyph)

            bg_seq = '\033[49m' if bg == _TRANSPARENT else _bg(*bg)

            if ch == ' ':
                row.append(bg_seq + ' ' + _RESET)
            else:
                row.append(bg_seq + _fg(*fg) + ch + _RESET)
        out.append(''.join(row))
    sys.stdout.write('\n'.join(out) + '\n')


def render_halfblock(layer) -> None:
    """Two XP rows per terminal row using ▀/▄ half-block characters.

    Treats bg color of each XP cell as a pixel. Fg/glyph are ignored.
    """
    UPPER = '\u2580'  # ▀
    LOWER = '\u2584'  # ▄

    for y in range(0, layer.height, 2):
        row = []
        for x in range(layer.width):
            top_bg = layer.data[y][x][2]
            bot_bg = layer.data[y + 1][x][2] if y + 1 < layer.height else _TRANSPARENT

            top_t = top_bg == _TRANSPARENT
            bot_t = bot_bg == _TRANSPARENT

            if top_t and bot_t:
                row.append(' ')
            elif top_t:
                row.append('\033[49m' + _fg(*bot_bg) + LOWER + _RESET)
            elif bot_t:
                row.append('\033[49m' + _fg(*top_bg) + UPPER + _RESET)
            else:
                row.append(_fg(*top_bg) + _bg(*bot_bg) + UPPER + _RESET)
        sys.stdout.write(''.join(row) + '\n')


def _visual_layer_idx(xp: XPFile) -> int:
    """Layer 2 for >=3-layer sprites (engine-aligned); else last layer."""
    return 2 if len(xp.layers) >= 3 else len(xp.layers) - 1


def main():
    parser = argparse.ArgumentParser(
        description='Dump a .xp file to terminal as ANSI true-color art.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('file', help='Path to .xp file')
    parser.add_argument('--layer', '-l', type=int, default=None,
                        help='Layer index to render (default: visual layer)')
    parser.add_argument('--hb', '--half-block', action='store_true',
                        help='Half-block mode: bg colors as pixels, 2 XP rows per terminal row')
    parser.add_argument('--info', '-i', action='store_true',
                        help='Print layer info only, do not render')
    args = parser.parse_args()

    if not os.path.exists(args.file):
        print(f'xp_cat: not found: {args.file}', file=sys.stderr)
        sys.exit(1)

    # xp_core prints "Loading…" / "Loaded N layers." to stdout — redirect to stderr
    with contextlib.redirect_stdout(sys.stderr):
        xp = XPFile(args.file)

    if not xp.layers:
        print(f'xp_cat: no layers in {args.file}', file=sys.stderr)
        sys.exit(1)

    vis = _visual_layer_idx(xp)
    print(f'{os.path.basename(args.file)}  {len(xp.layers)} layer(s)', file=sys.stderr)
    for i, layer in enumerate(xp.layers):
        tag = ' ← visual' if i == vis else ''
        print(f'  [{i}] {layer.width}×{layer.height}{tag}', file=sys.stderr)

    if args.info:
        return

    idx = args.layer if args.layer is not None else vis

    if idx < 0 or idx >= len(xp.layers):
        print(f'xp_cat: layer {idx} out of range (0–{len(xp.layers) - 1})', file=sys.stderr)
        sys.exit(1)

    layer = xp.layers[idx]

    if args.hb:
        render_halfblock(layer)
    else:
        render_normal(layer)


if __name__ == '__main__':
    main()
