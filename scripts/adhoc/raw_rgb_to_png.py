#!/usr/bin/env python3
"""
Convert raw RGB/RGBA binary data to PNG for visual inspection.

Useful for inspecting raw framebuffer dumps, render target exports, or
any raw pixel data from the asciicker engine.

Origin: /tmp/rgb_to_png.py (transcript 625f92a4, picoCAD pipeline session)
Generalized: --width/--height args, RGBA support, --verbose flag.

Usage:
    python3 scripts/adhoc/raw_rgb_to_png.py input.raw output.png --width 160 --height 90
    python3 scripts/adhoc/raw_rgb_to_png.py input.raw output.png --width 320 --height 200 --rgba
"""
import argparse
import struct
import zlib
import sys


def png_chunk(tag: bytes, body: bytes) -> bytes:
    """Build a PNG chunk: length + tag + body + CRC."""
    crc = zlib.crc32(tag + body) & 0xFFFFFFFF
    return struct.pack('>I', len(body)) + tag + body + struct.pack('>I', crc)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert raw RGB/RGBA binary data to PNG."
    )
    parser.add_argument("input", help="Path to raw binary file")
    parser.add_argument("output", help="Path to output PNG file")
    parser.add_argument("--width", type=int, required=True, help="Image width in pixels")
    parser.add_argument("--height", type=int, required=True, help="Image height in pixels")
    parser.add_argument(
        "--rgba",
        action="store_true",
        help="Input is RGBA (4 bytes/pixel) instead of RGB (3 bytes/pixel)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Print diagnostic info")

    args = parser.parse_args()

    bytes_per_pixel = 4 if args.rgba else 3
    expected_size = args.width * args.height * bytes_per_pixel

    with open(args.input, 'rb') as f:
        data = f.read()

    if len(data) != expected_size:
        print(
            f"Error: expected {expected_size} bytes for {args.width}x{args.height}x{bytes_per_pixel}, "
            f"got {len(data)}",
            file=sys.stderr,
        )
        return 1

    # Convert to RGBA (PNG color type 6 = RGBA, color type 2 = RGB)
    if args.rgba:
        raw_pixels = data  # already RGBA
        color_type = 6
    else:
        # Insert alpha byte (0xFF) after each RGB triple
        raw_pixels = b''.join(
            b'\x00' + data[y * args.width * 3:(y + 1) * args.width * 3]
            for y in range(args.height)
        )
        color_type = 2

    png = (
        b'\x89PNG\r\n\x1a\n'
        + png_chunk(
            b'IHDR',
            struct.pack('>IIBBBBB', args.width, args.height, 8, color_type, 0, 0, 0),
        )
        + png_chunk(b'IDAT', zlib.compress(raw_pixels, 9))
        + png_chunk(b'IEND', b'')
    )

    with open(args.output, 'wb') as f:
        f.write(png)

    if args.verbose:
        print(f"Saved {args.output} ({len(png)} bytes, {args.width}x{args.height})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
