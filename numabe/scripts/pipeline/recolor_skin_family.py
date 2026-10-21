#!/usr/bin/env python3
"""Duplicate XP sprite families with a deterministic palette remap.

This is a small proof-asset tool for alternate character skins. It keeps the
engine-side family prefixes stable while letting us regenerate obvious visual
variants (for example a green-shirt family) from the canonical human sheets.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.xp_core import XPFile


def parse_rgb(text: str) -> tuple[int, int, int]:
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(f"expected R,G,B, got {text!r}")
    try:
        rgb = tuple(int(p) for p in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid RGB value {text!r}") from exc
    for channel in rgb:
        if channel < 0 or channel > 255:
            raise argparse.ArgumentTypeError(f"RGB channel out of range in {text!r}")
    return rgb


def parse_map(text: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    if "=" not in text:
        raise argparse.ArgumentTypeError(f"expected SRC=DEST, got {text!r}")
    src, dst = text.split("=", 1)
    return parse_rgb(src), parse_rgb(dst)


def parse_family(text: str) -> tuple[str, str]:
    if ":" not in text:
        raise argparse.ArgumentTypeError(f"expected SRC_PREFIX:DST_PREFIX, got {text!r}")
    src, dst = text.split(":", 1)
    src = src.strip()
    dst = dst.strip()
    if not src or not dst:
        raise argparse.ArgumentTypeError(f"family prefixes must be non-empty: {text!r}")
    return src, dst


def recolor_file(src_path: Path, dst_path: Path,
    color_map: dict[tuple[int, int, int], tuple[int, int, int]]) -> None:
    xp = XPFile()
    xp.load(str(src_path))

    for layer in xp.layers[2:]:
        for y, row in enumerate(layer.data):
            for x, (glyph, fg, bg) in enumerate(row):
                new_fg = color_map.get(tuple(fg), tuple(fg))
                new_bg = color_map.get(tuple(bg), tuple(bg))
                row[x] = (glyph, new_fg, new_bg)

    dst_path.parent.mkdir(parents=True, exist_ok=True)
    xp.save(str(dst_path))


def main() -> int:
    parser = argparse.ArgumentParser(description="Duplicate XP sprite families with recolor remaps.")
    parser.add_argument(
        "--sprites-dir",
        default="assets/sprites",
        help="Directory containing family-prefix XP files (default: assets/sprites).",
    )
    parser.add_argument(
        "--family",
        action="append",
        required=True,
        type=parse_family,
        help="Family prefix mapping in the form SRC_PREFIX:DST_PREFIX. Repeat for each family.",
    )
    parser.add_argument(
        "--map",
        action="append",
        required=True,
        type=parse_map,
        help="Color remap in the form R,G,B=R,G,B. Repeat for each palette entry.",
    )
    args = parser.parse_args()

    sprites_dir = Path(args.sprites_dir)
    color_map = dict(args.map)

    for src_prefix, dst_prefix in args.family:
        src_files = sorted(sprites_dir.glob(f"{src_prefix}-*.xp"))
        if not src_files:
            raise SystemExit(f"no files found for family prefix {src_prefix!r} in {sprites_dir}")
        for src_path in src_files:
            suffix = src_path.name[len(src_prefix):]
            dst_path = sprites_dir / f"{dst_prefix}{suffix}"
            recolor_file(src_path, dst_path, color_map)
            print(f"wrote {dst_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
