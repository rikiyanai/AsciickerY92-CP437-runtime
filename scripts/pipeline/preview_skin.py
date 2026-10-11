"""
preview_skin.py -- Terminal side-by-side preview of a .xp sprite vs its green-tinted variant.

Renders ORIGINAL (left) and GREEN TINT (right) using actual CP437 glyphs with
ANSI truecolor, so you can verify the tint is correct before a headed test run.

Shows a 40×40 slice of layer 2 (the visual art layer) at 1:1 scale.
Use --x and --y to pan to a different region of the sprite sheet.

Usage:
    python -m scripts.pipeline.preview_skin [path/to/sprite.xp] [--x X] [--y Y] [--w W] [--h H]

    Defaults to assets/sprites/player-0001.xp, x=0 y=0 w=40 h=40.

Tint: (r//4, g, b//4) applied to fg and bg of art layers (1 and 2).
Layer 0 (metadata) is preserved untouched.
"""

import copy
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent.resolve()
DEFAULT_SPRITE = REPO_ROOT / "assets" / "sprites" / "player-0001.xp"

TRANSPARENT = (255, 0, 255)  # magic-pink = transparent cell


def _green(r: int, g: int, b: int) -> tuple:
    """Dim red and blue to ~25%, keep green full. Result is unmistakably green."""
    return (r // 4, g, b // 4)


def _tint_xp(xp):
    """Return a deep-copied XPFile with green tint applied to art layers 1 and 2."""
    tinted = copy.deepcopy(xp)
    for idx in (1, 2):
        if idx >= len(tinted.layers):
            continue
        layer = tinted.layers[idx]
        for y in range(layer.height):
            for x in range(layer.width):
                glyph, fg, bg = layer.data[y][x]
                new_fg = _green(*fg)
                new_bg = _green(*bg) if bg != TRANSPARENT else bg
                layer.data[y][x] = (glyph, new_fg, new_bg)
    return tinted


def _render_glyphs(layer, x0: int, y0: int, w: int, h: int) -> list[str]:
    """Render a region of an XPLayer using actual CP437 glyphs + ANSI truecolor.

    Returns a list of strings, one per row. Each row is exactly w visible chars wide
    (transparent cells become spaces).
    """
    RESET = "\033[0m"
    lines = []
    for y in range(y0, min(y0 + h, layer.height)):
        row = []
        for x in range(x0, min(x0 + w, layer.width)):
            glyph, fg, bg = layer.data[y][x]
            char = bytes([glyph & 0xFF]).decode("cp437")
            if bg == TRANSPARENT:
                if glyph in (0, 32):
                    row.append(" ")
                else:
                    # glyph on transparent bg: fg color, default terminal bg
                    row.append(
                        f"\033[38;2;{fg[0]};{fg[1]};{fg[2]}m{char}{RESET}"
                    )
            else:
                row.append(
                    f"\033[38;2;{fg[0]};{fg[1]};{fg[2]}m"
                    f"\033[48;2;{bg[0]};{bg[1]};{bg[2]}m"
                    f"{char}{RESET}"
                )
        # pad to w if layer is narrower than requested region
        if len(row) < w:
            row.extend([" "] * (w - len(row)))
        lines.append("".join(row))
    # pad to h if layer is shorter
    while len(lines) < h:
        lines.append(" " * w)
    return lines


def _parse_args() -> tuple:
    args = sys.argv[1:]
    src = DEFAULT_SPRITE
    x0, y0, w, h = 0, 0, 40, 40
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--x" and i + 1 < len(args):
            x0 = int(args[i + 1]); i += 2
        elif a == "--y" and i + 1 < len(args):
            y0 = int(args[i + 1]); i += 2
        elif a == "--w" and i + 1 < len(args):
            w = int(args[i + 1]); i += 2
        elif a == "--h" and i + 1 < len(args):
            h = int(args[i + 1]); i += 2
        elif not a.startswith("--"):
            src = Path(a); i += 1
        else:
            i += 1
    return src, x0, y0, w, h


def main() -> None:
    src, x0, y0, w, h = _parse_args()
    if not src.exists():
        print(f"ERROR: {src} not found", file=sys.stderr)
        sys.exit(1)

    from scripts.pipeline.xp_core import XPFile

    print(f"Loading {src.name} ...", flush=True)
    xp = XPFile(str(src))
    meta = xp.get_metadata()
    layer = xp.layers[2]  # layer 2 = visual art
    print(
        f"  {len(xp.layers)} layers  {layer.width}x{layer.height}  "
        f"angles={meta.get('angles')}  anims={meta.get('anims')}",
        flush=True,
    )
    print(f"  showing region x={x0} y={y0} w={w} h={h} (layer 2)", flush=True)

    tinted = _tint_xp(xp)
    tinted_layer = tinted.layers[2]

    orig_lines  = _render_glyphs(layer,        x0, y0, w, h)
    green_lines = _render_glyphs(tinted_layer, x0, y0, w, h)

    SEP = "  \033[2m║\033[0m  "
    label_l = f"ORIGINAL: {src.name}"
    label_r = "GREEN TINT"
    print()
    print(f"\033[1m  {label_l:<{w}}{SEP}{label_r}\033[0m")
    print("  " + "─" * w + "──\033[2m╫\033[0m──" + "─" * w)
    for l, r in zip(orig_lines, green_lines):
        print("  " + l + SEP + r)
    print()
    print(
        f"\033[2m  tip: pan with --x / --y, resize with --w / --h  "
        f"(sheet is {layer.width}x{layer.height})\033[0m"
    )


if __name__ == "__main__":
    main()
