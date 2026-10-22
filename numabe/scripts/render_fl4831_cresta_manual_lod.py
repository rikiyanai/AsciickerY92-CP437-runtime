#!/usr/bin/env python3
"""Rasterize manually typed Cresta rows in the exact native font; never fit art.

Declared diagnostic output: .run/fl4831_cresta_lod_manual/ (overridable --out).
This authoring preview is not a Godot render or runtime delivery receipt.
"""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OWNER = ROOT/"assets/meshes/incoming/cresta_gx71.foreground.lod.manual.json"
OUTPUT = ROOT/".run/fl4831_cresta_lod_manual"


def render(owner=OWNER, out=OUTPUT):
    doc = json.loads(owner.read_text())
    font_path = ROOT/doc["font"]
    if hashlib.sha256(font_path.read_bytes()).hexdigest() != doc["font_sha256"]:
        raise ValueError("native font identity changed")
    font = ImageFont.truetype(str(font_path), doc["font_px"])
    catalog = json.loads((ROOT/"godot_project/assets/glyphs/true_ff/saitamaar/catalog.json").read_text())
    available = {g["char"] for g in catalog["glyphs"]}
    missing = set("".join(doc["rows"]))-available
    if missing:
        raise ValueError(f"manual native glyphs unavailable in runtime atlas: {sorted(missing)}")
    image = Image.new("L", tuple(doc["size"]), 255)
    draw = ImageDraw.Draw(image)
    widths = []
    for index, text in enumerate(doc["rows"]):
        width = font.getlength(text)
        if width > doc["size"][0]:
            raise ValueError(f"manual row {index} exceeds native canvas: {width}")
        widths.append(width)
        draw.text((0, index*doc["row_pitch_px"]), text, font=font, fill=0, anchor="la")
    image = image.point(lambda value: 255 if value > 127 else 0)
    out.mkdir(parents=True, exist_ok=True)
    path = out/"cresta-front-left-native.png"
    image.save(path)
    (out/"cresta-front-left-native.txt").write_text("\n".join(doc["rows"])+"\n")
    print("CRESTA_MANUAL_NATIVE_PREVIEW", path, "row_widths", widths, "runtime=NOT_IMPLEMENTED")
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=OUTPUT)
    args = parser.parse_args()
    render(out=args.out)


if __name__ == "__main__":
    main()
