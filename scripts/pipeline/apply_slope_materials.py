#!/usr/bin/env python3
"""Apply slope-based material variety to an A3D terrain.

Scans terrain patches and assigns materials based on slope steepness
and elevation, using the engine's shade[4][16] lookup system.

The visual cell uint16 stores:
  - bits 0-7: material ID (0-255)
  - bit 15 (0x8000): elevation flag (selects shade row 2-3 vs 0-1)

Usage:
  python3 scripts/pipeline/apply_slope_materials.py INPUT.a3d OUTPUT.a3d
  python3 scripts/pipeline/apply_slope_materials.py INPUT.a3d OUTPUT.a3d --slope-mat 3 --slope-threshold 0.3
"""
import argparse
import math
import struct
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "addons" / "io_asciicker" / "scene"))
import a3d_format as fmt

PATCH_WORLD = float(fmt.VISUAL_CELLS)  # 8
HC = fmt.HEIGHT_CELLS  # 4
HC1 = HC + 1  # 5


def sample_slope(height_map, u, v):
    """Compute terrain slope magnitude at visual cell (u, v) from height map."""
    # Map visual cell center to height map coordinates
    fx = (u + 0.5) * HC / fmt.VISUAL_CELLS
    fy = (v + 0.5) * HC / fmt.VISUAL_CELLS

    # Bilinear sample height
    ix = min(int(fx), HC - 1)
    iy = min(int(fy), HC - 1)
    fx_frac = fx - ix
    fy_frac = fy - iy

    h00 = height_map[iy][ix]
    h10 = height_map[iy][min(ix + 1, HC)]
    h01 = height_map[min(iy + 1, HC)][ix]
    h11 = height_map[min(iy + 1, HC)][min(ix + 1, HC)]

    # Gradient (finite difference)
    dhdx = (h10 - h00) * (1 - fy_frac) + (h11 - h01) * fy_frac
    dhdy = (h01 - h00) * (1 - fx_frac) + (h11 - h10) * fx_frac

    return math.sqrt(dhdx * dhdx + dhdy * dhdy)


def sample_height(height_map, u, v):
    """Sample terrain height at visual cell (u, v)."""
    fx = (u + 0.5) * HC / fmt.VISUAL_CELLS
    fy = (v + 0.5) * HC / fmt.VISUAL_CELLS
    ix = min(int(fx), HC - 1)
    iy = min(int(fy), HC - 1)
    fx_frac = fx - ix
    fy_frac = fy - iy
    h00 = height_map[iy][ix]
    h10 = height_map[iy][min(ix + 1, HC)]
    h01 = height_map[min(iy + 1, HC)][ix]
    h11 = height_map[min(iy + 1, HC)][min(ix + 1, HC)]
    return h00 * (1 - fx_frac) * (1 - fy_frac) + h10 * fx_frac * (1 - fy_frac) + \
           h01 * (1 - fx_frac) * fy_frac + h11 * fx_frac * fy_frac


def apply_slope_materials(input_path, output_path, slope_mat=3, slope_threshold=0.3,
                          steep_mat=7, steep_threshold=0.8,
                          set_elev_flags=True, only_grass=True):
    """Apply slope-based materials and elevation flags.

    The SBU terrain is bimodal: 98% flat (slope=0), 2% vertical walls (slope>10).
    So we use HEIGHT BANDS for material variety on flat ground, and steep slope
    for building walls only.

    Height bands (for grass cells on flat ground):
      - height <= 128 (baseline): keep Grass(1)
      - 128 < height <= 200: Dirt(2) — slight elevation
      - 200 < height <= 280: keep Grass(1) but set elev flag
      - 280 < height <= 320: Sand(4) — higher areas
      - height > 320: keep original

    Steep slope (>10): Stone(3) for building walls
    """
    with open(input_path, "rb") as f:
        header = fmt.A3DHeader.from_file(f)
        patches = [fmt.A3DPatch.from_file(f) for _ in range(header.num_patches)]
        rest = f.read()

    wall_count = 0
    dirt_count = 0
    sand_count = 0
    elev_count = 0
    total_cells = 0

    for p in patches:
        for v in range(fmt.VISUAL_CELLS):
            for u in range(fmt.VISUAL_CELLS):
                total_cells += 1
                old_val = p.visual[v][u]
                old_mat = old_val & 0xFF
                old_flags = old_val & 0xFF00

                if only_grass and old_mat != 1:
                    continue

                slope = sample_slope(p.height, u, v)
                height = sample_height(p.height, u, v)

                new_mat = old_mat
                new_flags = old_flags

                # Building walls (steep slope)
                if slope > 10.0:
                    new_mat = steep_mat
                    wall_count += 1
                # Height-based variety on flat ground
                elif 140 < height <= 200:
                    new_mat = 2  # Dirt
                    dirt_count += 1
                elif 290 < height <= 330:
                    new_mat = 4  # Sand
                    sand_count += 1

                # Elevation flag for shade variety
                if set_elev_flags and height > 200:
                    new_flags |= 0x8000
                    elev_count += 1
                else:
                    new_flags &= ~0x8000

                p.visual[v][u] = (new_flags & 0xFF00) | (new_mat & 0xFF)

    with open(output_path, "wb") as f:
        header.write(f)
        for p in patches:
            p.write(f)
        f.write(rest)

    print(f"Applied slope materials: {input_path} → {output_path}")
    print(f"  Total cells: {total_cells}")
    print(f"  Walls (slope>10) → mat {steep_mat}: {wall_count}")
    print(f"  Dirt (h 140-200): {dirt_count}")
    print(f"  Sand (h 290-330): {sand_count}")
    print(f"  Elevation flags set: {elev_count}")


def debug_slopes(input_path, sample_patches=1000):
    """Print slope value distribution for debugging thresholds."""
    with open(input_path, "rb") as f:
        header = fmt.A3DHeader.from_file(f)
        slopes = []
        for i in range(min(sample_patches, header.num_patches)):
            p = fmt.A3DPatch.from_file(f)
            for v in range(fmt.VISUAL_CELLS):
                for u in range(fmt.VISUAL_CELLS):
                    if (p.visual[v][u] & 0xFF) == 1:
                        slopes.append(sample_slope(p.height, u, v))

    slopes.sort()
    n = len(slopes)
    print(f"Slope distribution ({n} grass cells from {sample_patches} patches):")
    print(f"  Min:    {slopes[0]:.2f}")
    print(f"  25pct:  {slopes[n//4]:.2f}")
    print(f"  Median: {slopes[n//2]:.2f}")
    print(f"  75pct:  {slopes[3*n//4]:.2f}")
    print(f"  90pct:  {slopes[9*n//10]:.2f}")
    print(f"  95pct:  {slopes[19*n//20]:.2f}")
    print(f"  Max:    {slopes[-1]:.2f}")
    print(f"  Nonzero: {sum(1 for s in slopes if s > 0)}")
    print(f"  > 1:     {sum(1 for s in slopes if s > 1)}")
    print(f"  > 10:    {sum(1 for s in slopes if s > 10)}")
    print(f"  > 100:   {sum(1 for s in slopes if s > 100)}")


def main():
    parser = argparse.ArgumentParser(description="Apply slope-based material variety to A3D terrain")
    parser.add_argument("input", help="Input A3D file")
    parser.add_argument("output", help="Output A3D file")
    parser.add_argument("--slope-mat", type=int, default=3, help="Material for moderate slopes (default: 3=Stone)")
    parser.add_argument("--slope-threshold", type=float, default=0.3, help="Slope threshold for moderate (default: 0.3)")
    parser.add_argument("--steep-mat", type=int, default=7, help="Material for steep slopes (default: 7=Cobblestone)")
    parser.add_argument("--steep-threshold", type=float, default=0.8, help="Slope threshold for steep (default: 0.8)")
    parser.add_argument("--no-elev-flags", action="store_true", help="Don't set elevation flags")
    parser.add_argument("--all-materials", action="store_true", help="Apply to all materials, not just grass")
    parser.add_argument("--debug-slopes", action="store_true", help="Print slope distribution and exit")
    args = parser.parse_args()

    if args.debug_slopes:
        debug_slopes(args.input)
        return

    apply_slope_materials(
        args.input, args.output,
        slope_mat=args.slope_mat, slope_threshold=args.slope_threshold,
        steep_mat=args.steep_mat, steep_threshold=args.steep_threshold,
        set_elev_flags=not args.no_elev_flags,
        only_grass=not args.all_materials,
    )


if __name__ == "__main__":
    main()
