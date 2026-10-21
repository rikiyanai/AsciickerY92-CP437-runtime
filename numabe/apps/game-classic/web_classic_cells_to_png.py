#!/usr/bin/env python3
"""web_classic_cells_to_png.py - convert raw Classic AnsiCell frames to PNG.

PURPOSE: lets a human inspect the output of the Classic wasm renderer
(web_classic_smoke.mjs writes raw cells). No third-party dependencies: PNG
read/write uses zlib only (Pillow is not installed on this machine).

INPUT  raw file: width*height cells, 4 bytes each (fg, bk, gl, spare), row-major,
       exactly the AnsiCell layout of engine/render/render.h.
FONT   assets/fonts/cp437_8x8.png (128x128 palette PNG, 16x16 grid of 8x8
       glyphs). Glyph coverage = PNG alpha (tRNS) when present, otherwise
       non-black palette entry, matching the native font sampler's .a read.
PALETTE the Classic presenter's Pal() in platform/terminal_gl_present.cpp
       (term_fs_src_classic): p = clamp(p - 16, 0, 215); the cube is
       RGB = (p // 36, (p % 36) // 6, p % 6) * 0.2, so indices 0-15 render
       black and 232-255 clamp to white (the native shader does exactly this).
OUTPUT PNG at scale (default 1 = 8 px per cell).

Usage: web_classic_cells_to_png.py cells.raw WIDTH HEIGHT out.png [scale]
"""
import struct
import sys
import zlib


def read_png_indexed(path):
    data = open(path, 'rb').read()
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    pos = 8
    idat = b''
    plte = None
    trns = None
    w = h = depth = ctype = 0
    while pos < len(data):
        ln, = struct.unpack('>I', data[pos:pos + 4])
        typ = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b'IHDR':
            w, h, depth, ctype = struct.unpack('>IIBB', body[:10])
        elif typ == b'PLTE':
            plte = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif typ == b'tRNS':
            trns = list(body)
        elif typ == b'IDAT':
            idat += body
    raw = zlib.decompress(idat)
    assert ctype == 3 and depth == 8, 'font must be 8-bit palette PNG'
    stride = w
    rows = []
    prev = bytearray(stride)
    p = 0
    for _ in range(h):
        f = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        for i in range(stride):
            a = line[i - 1] if i > 0 else 0
            b = prev[i]
            c = prev[i - 1] if i > 0 else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif f == 4:
                pa = abs(b - c)
                pb = abs(a - c)
                pc = abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        rows.append(line)
        prev = line
    cover = []
    for line in rows:
        out = []
        for idx in line:
            if trns is not None:
                out.append(trns[idx] if idx < len(trns) else 255)
            else:
                out.append(0 if plte[idx] == (0, 0, 0) else 255)
        cover.append(out)
    return w, h, cover


def pal(p):
    p = min(max(p - 16, 0), 215)
    return ((p // 36) * 51, ((p % 36) // 6) * 51, (p % 6) * 51)


def write_png(path, w, h, rgb_rows):
    raw = b''.join(b'\x00' + bytes(r) for r in rgb_rows)

    def chunk(t, b):
        c = struct.pack('>I', len(b)) + t + b
        return c + struct.pack('>I', zlib.crc32(t + b) & 0xffffffff)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b'')
    open(path, 'wb').write(png)


def main():
    src, cw, ch, dst = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    scale = int(sys.argv[5]) if len(sys.argv) > 5 else 1
    cells = open(src, 'rb').read()
    assert len(cells) == cw * ch * 4, 'expected %d bytes got %d' % (cw * ch * 4, len(cells))
    fw, fh, cover = read_png_indexed('/Users/r/Projects/asciicker-Y9-2/assets/fonts/cp437_8x8.png')
    gw = fw // 16
    gh = fh // 16
    rows = []
    for cy in range(ch):
        glyph_rows = [bytearray() for _ in range(gh)]
        for cx in range(cw):
            fg, bk, gl, _sp = cells[(cy * cw + cx) * 4:(cy * cw + cx) * 4 + 4]
            fgc, bkc = pal(fg), pal(bk)
            gx, gy = (gl & 15) * gw, (gl >> 4) * gh
            for y in range(gh):
                row = glyph_rows[y]
                for x in range(gw):
                    a = cover[gy + y][gx + x] / 255.0
                    for k in range(3):
                        row.append(int(round(bkc[k] + (fgc[k] - bkc[k]) * a)))
        rows.extend(glyph_rows)
    if scale > 1:
        scaled = []
        for r in rows:
            e = bytearray()
            for i in range(0, len(r), 3):
                e += r[i:i + 3] * scale
            scaled.extend([e] * scale)
        rows = scaled
    write_png(dst, cw * gw * scale, ch * gh * scale, rows)
    # Frame statistics so a blank/uniform frame is detectable without viewing.
    distinct_gl = len(set(cells[i + 2] for i in range(0, len(cells), 4)))
    distinct_bk = len(set(cells[i + 1] for i in range(0, len(cells), 4)))
    distinct_cells = len(set(cells[i:i + 3] for i in range(0, len(cells), 4)))
    print('%s: %dx%d cells, distinct glyphs=%d distinct bk=%d distinct (fg,bk,gl)=%d'
          % (dst, cw, ch, distinct_gl, distinct_bk, distinct_cells))


if __name__ == '__main__':
    main()
