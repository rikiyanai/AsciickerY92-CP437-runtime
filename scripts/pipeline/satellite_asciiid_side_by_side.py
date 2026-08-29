#!/usr/bin/env python3
"""Compose satellite imagery and an asciiid frame for one OSM world coordinate.

This is the visual proof front door for OSM building/material alignment:

  1. Capture asciiid with MCP `SET_CAMERA_VIEW ...` + `CAPTURE_FRAME <ppm>`.
  2. Run this script with the same world coordinate and captured frame.
  3. Inspect the single output PNG instead of comparing browser tabs by memory.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
from satellite_tiles import fetch_tile, latlon_to_tile  # noqa: E402
from satellite_view import DEFAULT_RUN, load_projection, world_to_latlon  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_font(size: int = 18):
    try:
        return ImageFont.truetype("Menlo.ttc", size)
    except OSError:
        return ImageFont.load_default()


def _label(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str) -> None:
    font = _load_font(18)
    pad = 6
    box = draw.textbbox(xy, text, font=font)
    rect = (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)
    draw.rectangle(rect, fill=(0, 0, 0))
    draw.text(xy, text, fill=(255, 255, 255), font=font)


def _fit(im: Image.Image, size: tuple[int, int]) -> Image.Image:
    out = im.convert("RGB")
    out.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, (24, 24, 24))
    x = (size[0] - out.width) // 2
    y = (size[1] - out.height) // 2
    canvas.paste(out, (x, y))
    return canvas


def _crop(im: Image.Image, crop: list[int] | None) -> Image.Image:
    if crop is None:
        return im
    left, top, right, bottom = crop
    if left < 0 or top < 0 or right <= left or bottom <= top:
        raise SystemExit("--asciiid-crop must be LEFT TOP RIGHT BOTTOM with positive area")
    width, height = im.size
    if right > width or bottom > height:
        raise SystemExit(f"--asciiid-crop {crop} exceeds frame size {width}x{height}")
    return im.crop((left, top, right, bottom))


def compose(args: argparse.Namespace) -> Path:
    run_dir = PROJECT_ROOT / "assets" / "meshes" / "osm_runs" / args.run_id
    proj = load_projection(run_dir)
    if not proj:
        raise SystemExit(f"missing projection metadata in {run_dir / 'terrain_metadata.json'}")
    lat, lon = world_to_latlon(args.world[0], args.world[1], proj)
    tx, ty = latlon_to_tile(lat, lon, args.zoom)
    cache_dir = args.cache_dir or (run_dir / "satellite_cache")
    tile_path, status = fetch_tile(args.zoom, tx, ty, cache_dir)
    if tile_path is None:
        raise SystemExit(f"satellite tile fetch failed: {status}")

    sat = Image.open(tile_path)
    frame = _crop(Image.open(args.asciiid_frame), args.asciiid_crop)
    panel_size = (args.panel_width, args.panel_height)
    sat_panel = _fit(sat, panel_size)
    frame_panel = _fit(frame, panel_size)

    gutter = 12
    header_h = 46
    out = Image.new("RGB", (panel_size[0] * 2 + gutter, panel_size[1] + header_h), (12, 12, 12))
    out.paste(sat_panel, (0, header_h))
    out.paste(frame_panel, (panel_size[0] + gutter, header_h))
    draw = ImageDraw.Draw(out)
    _label(draw, (10, 12), f"satellite z={args.zoom} tile=({tx},{ty}) lat/lon=({lat:.7f},{lon:.7f})")
    _label(draw, (panel_size[0] + gutter + 10, 12), f"asciiid world=({args.world[0]:.1f},{args.world[1]:.1f}) {args.asciiid_frame.name}")
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    out.save(output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=DEFAULT_RUN)
    parser.add_argument("--world", nargs=2, type=float, required=True, metavar=("X", "Y"))
    parser.add_argument("--asciiid-frame", type=Path, required=True, help="PPM/PNG captured from asciiid")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--zoom", type=int, default=19)
    parser.add_argument("--cache-dir", type=Path, default=None)
    parser.add_argument("--panel-width", type=int, default=800)
    parser.add_argument("--panel-height", type=int, default=600)
    parser.add_argument(
        "--asciiid-crop",
        nargs=4,
        type=int,
        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
        help="Crop the captured asciiid frame before fitting it, useful for excluding editor sidebars.",
    )
    args = parser.parse_args(argv)
    path = compose(args)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
