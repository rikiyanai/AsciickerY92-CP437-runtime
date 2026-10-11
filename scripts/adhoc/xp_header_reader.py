#!/usr/bin/env python3
"""Read XP file header only (no full parse): version, layers, sheet dimensions.

Fast header-only reader for REXPaint .xp files. Reads just the first 16 bytes
to get version, layer count, and sheet width/height without decompressing the
entire file. Useful for quick surveys of sprite directories or CI checks.

Also dumps non-transparent cell count when --detail is passed (full parse).

Origin: Codex session history — multiple python3 -c one-liners that read XP
  headers via gzip+struct to inspect player-*.xp, bigbee-*.xp, wolack-*.xp
  dimensions (codex history.jsonl, entries near bigbee/wolack research).

Generalized: added --detail mode for cell stats, JSON output, glob-based
  multi-file mode.

Usage:
  python3 scripts/adhoc/xp_header_reader.py assets/sprites/player-0100.xp
  python3 scripts/adhoc/xp_header_reader.py assets/sprites/player-0100.xp --detail
  python3 scripts/adhoc/xp_header_reader.py "assets/sprites/bigbee-*.xp" --json
"""

from __future__ import annotations

import argparse
import glob
import gzip
import json
import struct
import sys
from pathlib import Path

# REXPaint XP format constants
XP_VERSION = -1
TRANSPARENT_BG = (255, 0, 255)  # magenta = transparent
XP_CELL_SIZE = 10  # bytes: 4 (glyph) + 3 (fg RGB) + 3 (bg RGB)


def read_xp_header(path: Path) -> dict:
    """Read XP header only. Returns dict with version, layers, width, height."""
    result = {"path": str(path), "error": None}
    try:
        with gzip.open(path, "rb") as f:
            data = f.read(16)
        offset = 0
        result["version"] = struct.unpack_from("<i", data, offset)[0]
        offset += 4
        result["num_layers"] = struct.unpack_from("<i", data, offset)[0]
        offset += 4
        result["sheet_w"] = struct.unpack_from("<i", data, offset)[0]
        offset += 4
        result["sheet_h"] = struct.unpack_from("<i", data, offset)[0]
    except Exception as exc:
        result["error"] = str(exc)
    return result


def read_xp_detail(path: Path) -> dict:
    """Full parse: read all layers and count non-transparent cells."""
    result = read_xp_header(path)
    if result["error"]:
        return result

    try:
        with gzip.open(path, "rb") as f:
            data = f.read()

        offset = 8  # skip version + num_layers
        w = result["sheet_w"]
        h = result["sheet_h"]
        nl = result["num_layers"]

        layers = []
        for li in range(nl):
            non_trans = 0
            cells = []
            for x in range(w):
                col = []
                for y in range(h):
                    glyph = struct.unpack_from("<i", data, offset)[0]
                    offset += 4
                    fg = struct.unpack_from("BBB", data, offset)
                    offset += 3
                    bg = struct.unpack_from("BBB", data, offset)
                    offset += 3
                    if bg != TRANSPARENT_BG:
                        non_trans += 1
                    col.append({"glyph": glyph, "fg": list(fg), "bg": list(bg)})
                cells.append(col)
            layers.append({"index": li, "non_transparent_cells": non_trans})
        result["layers"] = layers
    except Exception as exc:
        result["error"] = str(exc)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="XP file path(s) or glob pattern(s)")
    parser.add_argument("--detail", action="store_true",
                        help="Full parse with per-layer cell stats")
    parser.add_argument("--json", action="store_true",
                        help="JSON output")
    args = parser.parse_args()

    # Expand globs
    files: list[Path] = []
    for p in args.paths:
        if "*" in p or "?" in p:
            files.extend(Path(g) for g in sorted(glob.glob(p)))
        else:
            files.append(Path(p))

    results = []
    for fp in files:
        if not fp.is_file():
            results.append({"path": str(fp), "error": "file not found"})
            continue
        if args.detail:
            results.append(read_xp_detail(fp))
        else:
            results.append(read_xp_header(fp))

    if args.json:
        json.dump(results if len(results) > 1 else results[0], sys.stdout, indent=2)
        print()
        return

    # Table output
    for r in results:
        if r.get("error"):
            print(f"{r['path']}: ERROR: {r['error']}")
            continue

        w = r.get("sheet_w", "?")
        h = r.get("sheet_h", "?")
        nl = r.get("num_layers", "?")
        print(f"{r['path']}: sheet={w}x{h}  layers={nl}  version={r.get('version', '?')}")

        if args.detail and "layers" in r:
            for layer in r["layers"]:
                nc = layer["non_transparent_cells"]
                pct = (nc / (w * h) * 100) if w and h else 0
                print(f"  Layer {layer['index']}: {nc} non-transparent cells ({pct:.1f}%)")


if __name__ == "__main__":
    main()
