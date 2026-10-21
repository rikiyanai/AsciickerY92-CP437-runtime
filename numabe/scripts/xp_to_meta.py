#!/usr/bin/env python3
import argparse
import gzip
import json
import os
import struct
import sys


def read_xp_header(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    if len(data) < 16:
        raise ValueError("XP file too small for header")
    version, layers, width, height = struct.unpack_from("<4i", data, 0)
    return version, layers, width, height


def build_stub(input_path, width, height, layers, xp_version, font):
    base = os.path.basename(input_path)
    png = os.path.splitext(base)[0] + ".png"
    return {
        "version": 1,
        "source": base,
        "xp_header": {
            "version": xp_version,
            "layers": layers,
            "width": width,
            "height": height,
        },
        "size": {"width": width, "height": height},
        "png": png,
        "font": font,
        "map_path": None,
        "camera": {
            "pos": [None, None, None],
            "yaw": None,
            "zoom": None,
            "perspective": None,
            "scene_shift": None,
            "cam_shift": None,
        },
        "player": {
            "pos": [None, None, None],
            "dir": None,
        },
        "light": {
            "dir": [None, None, None],
            "ambience": None,
        },
        "water": None,
        "metadata_incomplete": True,
        "notes": "Fill camera/light/water/map_path from a live capture (F10) or manual notes.",
    }


def write_json(path, obj):
    with open(path, "w", encoding="ascii") as f:
        json.dump(obj, f, indent=2, ensure_ascii=True)
        f.write("\n")


def parse_args():
    parser = argparse.ArgumentParser(description="Generate stub shot metadata for .xp captures.")
    parser.add_argument("inputs", nargs="+", help="Input .xp files")
    parser.add_argument("--font", default="assets/fonts/cp437_16x16.png.bdf", help="Font used for PNG conversion")
    return parser.parse_args()


def main():
    args = parse_args()
    for path in args.inputs:
        xp_version, layers, width, height = read_xp_header(path)
        stub = build_stub(path, width, height, layers, xp_version, args.font)
        out = os.path.splitext(path)[0] + ".json"
        write_json(out, stub)
        print("Wrote", out)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Error:", exc, file=sys.stderr)
        sys.exit(1)
