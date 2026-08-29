#!/usr/bin/env python3
import argparse
import gzip
import os
import struct
import sys
import zlib


class BdfGlyph:
    def __init__(self, codepoint, width, height, xoff, yoff, rows):
        self.codepoint = codepoint
        self.width = width
        self.height = height
        self.xoff = xoff
        self.yoff = yoff
        self.rows = rows  # list of int bitmasks, top to bottom


def read_xp(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    if len(data) < 16:
        raise ValueError("XP file too small for header")

    version, layers, width, height = struct.unpack_from("<4i", data, 0)
    offset = 16
    layer_data = []
    for layer_index in range(layers):
        if layer_index == 0:
            lw, lh = width, height
        else:
            if offset + 8 > len(data):
                raise ValueError("XP layer header out of range")
            lw, lh = struct.unpack_from("<2i", data, offset)
            offset += 8
        count = lw * lh
        needed = count * 10
        if offset + needed > len(data):
            raise ValueError("XP layer data out of range")
        cells = []
        for _ in range(count):
            glyph = struct.unpack_from("<I", data, offset)[0]
            offset += 4
            fg = data[offset:offset + 3]
            offset += 3
            bg = data[offset:offset + 3]
            offset += 3
            cells.append((glyph, fg, bg))
        layer_data.append((lw, lh, cells))
    return version, layer_data


def load_bdf(path):
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    font_width = None
    font_height = None
    font_ascent = None
    font_descent = None
    glyphs = {}

    with open(path, "r", encoding="ascii", errors="ignore") as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("FONTBOUNDINGBOX"):
            parts = line.split()
            if len(parts) >= 3:
                font_width = int(parts[1])
                font_height = int(parts[2])
        elif line.startswith("FONT_ASCENT"):
            parts = line.split()
            if len(parts) >= 2:
                font_ascent = int(parts[1])
        elif line.startswith("FONT_DESCENT"):
            parts = line.split()
            if len(parts) >= 2:
                font_descent = int(parts[1])
        elif line.startswith("STARTCHAR"):
            codepoint = None
            bbx = None
            rows = []
            i += 1
            while i < len(lines):
                s = lines[i].strip()
                if s.startswith("ENCODING"):
                    parts = s.split()
                    if len(parts) >= 2:
                        codepoint = int(parts[1])
                elif s.startswith("BBX"):
                    parts = s.split()
                    if len(parts) >= 5:
                        bbx = tuple(int(p) for p in parts[1:5])
                elif s == "BITMAP":
                    if not bbx:
                        raise ValueError("BDF bitmap without BBX")
                    width, height, xoff, yoff = bbx
                    rows = []
                    for j in range(height):
                        i += 1
                        row_hex = lines[i].strip()
                        bits = int(row_hex, 16)
                        row_width = len(row_hex) * 4
                        rows.append((bits, row_width))
                elif s == "ENDCHAR":
                    if codepoint is not None and bbx:
                        width, height, xoff, yoff = bbx
                        row_bits = []
                        for bits, w in rows:
                            row_bits.append((bits, w))
                        glyphs[codepoint] = BdfGlyph(
                            codepoint, width, height, xoff, yoff, row_bits
                        )
                    break
                i += 1
        i += 1

    if font_width is None or font_height is None:
        raise ValueError("BDF missing FONTBOUNDINGBOX")
    if font_ascent is None:
        font_ascent = font_height
    if font_descent is None:
        font_descent = 0

    return glyphs, font_width, font_height, font_ascent, font_descent


def cp437_to_unicode(code):
    if code <= 0xFF:
        return ord(bytes([code]).decode("cp437"))
    return code


def get_glyph_mask(codepoint, glyphs, cell_w, cell_h, ascent, descent, cache):
    if codepoint in cache:
        return cache[codepoint]

    glyph = glyphs.get(codepoint)
    if not glyph:
        glyph = glyphs.get(ord(" "), None)
    mask = [0] * (cell_w * cell_h)
    if glyph:
        top = ascent - (glyph.yoff + glyph.height)
        left = glyph.xoff
        for row_idx, (bits, row_width) in enumerate(glyph.rows):
            y = top + row_idx
            if y < 0 or y >= cell_h:
                continue
            for x in range(glyph.width):
                bit = (bits >> (row_width - 1 - x)) & 1
                if not bit:
                    continue
                xx = left + x
                if 0 <= xx < cell_w:
                    mask[y * cell_w + xx] = 1
    cache[codepoint] = mask
    return mask


def render_layer(layer, glyphs, cell_w, cell_h, ascent, descent, scale):
    width, height, cells = layer
    if width <= 0 or height <= 0:
        raise ValueError("XP layer has invalid dimensions")
    if len(cells) != width * height:
        raise ValueError("XP layer cell count mismatch")

    img_w = width * cell_w
    img_h = height * cell_h
    img = bytearray(img_w * img_h * 3)

    cache = {}
    idx = 0
    for x in range(width):
        for y in range(height):
            glyph, fg, bg = cells[idx]
            idx += 1
            codepoint = cp437_to_unicode(glyph)
            mask = get_glyph_mask(codepoint, glyphs, cell_w, cell_h, ascent, descent, cache)

            base_x = x * cell_w
            base_y = y * cell_h
            for py in range(cell_h):
                row_start = (base_y + py) * img_w * 3 + base_x * 3
                mask_row = py * cell_w
                for px in range(cell_w):
                    off = row_start + px * 3
                    if mask[mask_row + px]:
                        img[off:off + 3] = fg
                    else:
                        img[off:off + 3] = bg

    if scale == 1:
        return img_w, img_h, img
    return scale_rgb(img, img_w, img_h, scale)


def scale_rgb(img, width, height, scale):
    if scale <= 1:
        return width, height, img
    new_w = width * scale
    new_h = height * scale
    out = bytearray(new_w * new_h * 3)
    for y in range(height):
        for x in range(width):
            src = (y * width + x) * 3
            pixel = img[src:src + 3]
            for sy in range(scale):
                row = ((y * scale + sy) * new_w + x * scale) * 3
                for sx in range(scale):
                    dst = row + sx * 3
                    out[dst:dst + 3] = pixel
    return new_w, new_h, out


def write_png(path, width, height, rgb):
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw.extend(rgb[y * stride:(y + 1) * stride])
    compressed = zlib.compress(raw, level=9)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = b"".join([
        b"\x89PNG\r\n\x1a\n",
        chunk(b"IHDR", ihdr),
        chunk(b"IDAT", compressed),
        chunk(b"IEND", b""),
    ])
    with open(path, "wb") as f:
        f.write(png)


def parse_args():
    parser = argparse.ArgumentParser(description="Convert Asciicker/RexPaint .xp to PNG.")
    parser.add_argument("input", help="Input .xp file (shot.xp or RexPaint .xp)")
    parser.add_argument("-o", "--output", help="Output .png path (default: input basename + .png)")
    parser.add_argument("--font", default="assets/fonts/cp437_12x12.png.bdf", help="BDF font path")
    parser.add_argument("--layer", type=int, default=-1, help="Layer index (default: last)")
    parser.add_argument("--scale", type=int, default=1, help="Integer scale factor (default: 1)")
    return parser.parse_args()


def main():
    args = parse_args()
    version, layers = read_xp(args.input)
    if not layers:
        raise ValueError("XP file has no layers")
    layer_index = args.layer if args.layer >= 0 else len(layers) - 1
    if layer_index < 0 or layer_index >= len(layers):
        raise ValueError("Layer index out of range")

    glyphs, cell_w, cell_h, ascent, descent = load_bdf(args.font)
    width, height, img = render_layer(layers[layer_index], glyphs, cell_w, cell_h, ascent, descent, args.scale)

    out = args.output
    if not out:
        base, _ = os.path.splitext(args.input)
        out = base + ".png"
    write_png(out, width, height, img)
    print("Wrote", out)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Error:", exc, file=sys.stderr)
        sys.exit(1)
