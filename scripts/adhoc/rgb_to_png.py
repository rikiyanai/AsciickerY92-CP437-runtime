#!/usr/bin/env python3
"""Convert raw RGB binary data to a viewable PNG image.

Originally extracted from Claude session 625f92a4-5c07-42a3-81a8-2c03ae839606
(picoCAD E2E debugging — raw render target inspection).

Usage:
    python3 scripts/adhoc/rgb_to_png.py <input.raw> <output.png> [--width 160] [--height 90]

The raw file must be a flat RGB binary (no header) with exactly width*height*3 bytes.
"""
import sys
import struct
import zlib
import argparse
from pathlib import Path


def make_png(data: bytes, width: int, height: int) -> bytes:
    """Build a PNG from raw RGB pixel data."""
    expected = width * height * 3
    if len(data) != expected:
        raise ValueError(
            f"Expected {expected} bytes ({width}x{height}x3), got {len(data)}"
        )

    def chunk(tag: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(tag + body) & 0xFFFFFFFF
        return struct.pack('>I', len(body)) + tag + body + struct.pack('>I', crc)

    # Add filter byte (0x00 = None) before each row
    raw = b''.join(
        b'\x00' + data[y * width * 3:(y + 1) * width * 3]
        for y in range(height)
    )

    png = (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress(raw, 9))
        + chunk(b'IEND', b'')
    )
    return png


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert raw RGB binary to PNG."
    )
    parser.add_argument("input", help="Path to raw RGB binary file")
    parser.add_argument("output", nargs="?", help="Path to output PNG (default: input path with .png extension)")
    parser.add_argument("--width", type=int, default=160, help="Image width in pixels (default: 160)")
    parser.add_argument("--height", type=int, default=90, help="Image height in pixels (default: 90)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"ERROR: input file not found: {input_path}")
        return 1

    output_path = Path(args.output) if args.output else input_path.with_suffix('.png')

    data = input_path.read_bytes()
    png = make_png(data, args.width, args.height)
    output_path.write_bytes(png)

    print(f"Saved {output_path} ({len(png)} bytes)")
    print(f"  Image: {args.width}x{args.height}")
    print(f"  Source: {input_path} ({len(data)} bytes raw RGB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
