# Ad hoc script: Audit OSM output terrain visual cell material shade elevation distribution
# Created: 2026-06-01
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Audit OSM A3D terrain visual cells by decoded mat_id/shade/elevation.

The stock inspect_a3d --terrain-colors groups raw 16-bit visual values, which is
useful for exact visual encodings but misleading when the operator asks for
terrain color/material balance. This script decodes bits 0-7 as material id,
bits 8-14 as shade, and bit 15 as elevation.
"""
import argparse
import importlib.util
import json
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FMT_PATH = REPO / "addons" / "io_asciicker" / "scene" / "a3d_format.py"
spec = importlib.util.spec_from_file_location("a3d_format", FMT_PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def read_a3d(path: Path):
    import struct
    with path.open("rb") as f:
        header = mod.A3DHeader.from_file(f)
        patches = [mod.A3DPatch.from_file(f) for _ in range(header.num_patches)]
        materials = [mod.A3DMaterial.read(f) for _ in range(256)]
    return header, patches, materials


def name_color(rgb):
    r, g, b = rgb
    if (r, g, b) == (0, 0, 0):
        return "black"
    if g > r and g > b:
        return "greenish"
    if r > g and r > b:
        return "reddish/tan"
    if b > r and b > g:
        return "bluish"
    if r == g == b:
        return f"grey-{r}"
    return f"rgb({r},{g},{b})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("map", type=Path)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    header, patches, materials = read_a3d(args.map)
    mat_counts = Counter()
    shade_counts = defaultdict(Counter)
    elev_counts = Counter()
    raw_counts = Counter()
    total = 0
    for p in patches:
        for row in p.visual:
            for raw in row:
                raw = int(raw)
                mat_id = raw & 0xFF
                shade = (raw >> 8) & 0x7F
                elev = 1 if (raw & 0x8000) else 0
                mat_counts[mat_id] += 1
                shade_counts[mat_id][shade] += 1
                elev_counts[(mat_id, elev)] += 1
                raw_counts[raw] += 1
                total += 1
    rows = []
    for mat_id, count in mat_counts.most_common():
        mat = materials[mat_id]
        flat = mat.shade[0][8]
        elev = mat.shade[1][8]
        ramp_glyphs = [mat.shade[ramp][8].gl for ramp in range(4)]
        rows.append({
            "mat_id": mat_id,
            "count": count,
            "pct": round(count * 100.0 / total, 3),
            "flat_glyph": flat.gl,
            "ramp_glyphs": ramp_glyphs,
            "flat_fg": list(flat.fg),
            "flat_bg": list(flat.bg),
            "flat_bg_name": name_color(flat.bg),
            "elev_fg": list(elev.fg),
            "elev_bg": list(elev.bg),
            "elev_bg_name": name_color(elev.bg),
            "shade_top": shade_counts[mat_id].most_common(8),
            "elev_cells": elev_counts[(mat_id, 1)],
            "elev_pct_within_mat": round(elev_counts[(mat_id, 1)] * 100.0 / count, 3),
        })
    result = {
        "map": str(args.map),
        "patches": header.num_patches,
        "total_visual_cells": total,
        "decoded_materials": rows,
        "raw_visual_top": raw_counts.most_common(20),
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"map={args.map}")
        print(f"patches={header.num_patches} cells={total}")
        print("mat_id pct count flat_bg ramp_glyphs elev% shade_top")
        for row in rows:
            print(
                f"{row['mat_id']:>6} {row['pct']:>7.3f}% {row['count']:>8} "
                f"bg={tuple(row['flat_bg'])} {row['flat_bg_name']:<12} "
                f"glyphs={row['ramp_glyphs']} "
                f"elev={row['elev_pct_within_mat']:>6.2f}% shades={row['shade_top'][:5]}"
            )

if __name__ == "__main__":
    main()
