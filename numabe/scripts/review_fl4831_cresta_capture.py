#!/usr/bin/env python3
"""Make bounded, unresampled inspection crops from an existing Cresta capture.

Declared output: .run/fl4831_cresta_capture_review/<source-parent>/.
These are derived inspection copies, never replacement renderer evidence.
"""
import argparse
import hashlib
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".run/fl4831_cresta_capture_review"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--box", nargs=4, required=True, type=int, metavar=("X", "Y", "W", "H"))
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    if Path(args.name).name != args.name:
        parser.error("name must be a single filename stem")
    x, y, w, h = args.box
    with Image.open(args.source) as image:
        if x < 0 or y < 0 or w < 1 or h < 1 or x + w > image.width or y + h > image.height:
            parser.error("crop must be wholly inside the preserved capture")
        if max(w, h) > 512:
            parser.error("inspection crops are limited to 512px; do not resample native masks")
        output = OUTPUT / args.source.resolve().parent.name
        output.mkdir(parents=True, exist_ok=True)
        target = output / (args.name + ".png")
        if target.exists():
            parser.error("preserve earlier inspection copies; choose a new name")
        image.crop((x, y, x + w, y + h)).save(target)
    print("DERIVED_UNRESAMPLED_CROP", target)
    print("SOURCE", args.source.resolve(), "sha256", hashlib.sha256(args.source.read_bytes()).hexdigest())
    print("BOX_XYWH", args.box, "runtime_acceptance=OPEN")


if __name__ == "__main__":
    main()
