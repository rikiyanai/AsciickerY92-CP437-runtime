# Ad hoc script: Patch legacy-yy-block XP projection metadata for FL-4137 geometry contract
# Created: 2026-05-29
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
import gzip
import struct
import sys
from pathlib import Path

DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

def digit_for(value: int) -> int:
    if value < 0 or value >= len(DIGITS):
        raise ValueError(f"digit out of range: {value}")
    return ord(DIGITS[value])

def main() -> int:
    if len(sys.argv) != 3:
        print("usage: patch_xp_y_proj.py <xp-path> <y-proj-int>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    y_proj = int(sys.argv[2])
    raw = bytearray(gzip.open(path, "rb").read())
    version, layers = struct.unpack_from("<ii", raw, 0)
    if layers < 1:
        raise ValueError(f"{path}: expected at least one layer, got {layers}")
    width, height = struct.unpack_from("<ii", raw, 8)
    if width < 2 or height < 1:
        raise ValueError(f"{path}: layer0 too small {width}x{height}")
    layer0_off = 16
    cell_off = layer0_off + (1 * height + 0) * 10
    old_glyph = struct.unpack_from("<I", raw, cell_off)[0]
    struct.pack_into("<I", raw, cell_off, digit_for(y_proj))
    path.write_bytes(gzip.compress(bytes(raw)))
    print(f"patched {path}: version={version} layers={layers} size={width}x{height} y_proj glyph {old_glyph!r}->{digit_for(y_proj)!r} value={y_proj}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
