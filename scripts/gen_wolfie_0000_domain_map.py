#!/usr/bin/env python3
"""
Generate bare-combo domain map for wolfie-0000.xp.

Classifies every cell across all 144 frames (8 angles × 2 proj × 9 anim)
into: mount_rear, mount_front, rider, background.

The rear/front split uses the body/legs boundary from wolfie-0100.json
as geometric authority, adjusted for walk-frame leg displacement.

Output: docs/research/ascii/semantic_maps/source_overlay_domains/
        wolfie-0000-source-overlay-domain.json
"""

import json
import sys
from pathlib import Path
from collections import defaultdict

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.xp_core import XPFile

# ── constants ──────────────────────────────────────────────────────────
MAGENTA = (255, 0, 255)
YELLOW = (255, 255, 85)

ANGLES = 8
PROJECTIONS = 2
ANIMS = [1, 8]  # 1 idle + 8 walk = 9 per angle/proj
FRAME_W = 10
FRAME_H = 12
FRAMES_PER_ROW = 18  # 9 anims × 2 proj
ROWS = 8  # angle rows

# Per-angle body/legs boundary row (from wolfie-0100.json analysis).
# The "split_row" is the first row where legs start (inclusive).
# Cells at row < split_row that exist in L2 → mount_rear
# Cells at row >= split_row that exist in L2 → mount_front
def compute_split_row(l2_data, l3_data, fx: int, fy: int) -> tuple:
    """Find the split row between rider and mount body.

    Returns (split_row, rider_above) where:
    - rider_above=True means rider is above mount (proj 0 style):
      L2 at y < split_row = mount_rear, y >= split_row = mount_front
    - rider_above=False means rider is below mount (proj 1 style):
      L2 at y < split_row = mount_front, y >= split_row = mount_rear

    The split row is at the boundary between L2 mass and L3 mass.
    """
    l2_rows = []
    l3_rows = []
    for y in range(fy, fy + FRAME_H):
        for x in range(fx, fx + FRAME_W):
            _, _, bg2 = l2_data.data[y][x]
            if bg2 != MAGENTA and bg2 != YELLOW:
                l2_rows.append(y - fy)
            _, _, bg3 = l3_data.data[y][x]
            if bg3 != MAGENTA:
                l3_rows.append(y - fy)

    if not l3_rows:
        return FRAME_H, True

    l2_center = sum(l2_rows) / len(l2_rows) if l2_rows else 0
    l3_center = sum(l3_rows) / len(l3_rows)

    rider_above = l3_center < l2_center

    if rider_above:
        split_row = max(l3_rows) + 1
    else:
        split_row = min(l3_rows)

    return split_row, rider_above


def frame_origin(frame_index: int) -> tuple[int, int]:
    """Convert linear frame index (0-143) to (col_offset, row_offset) in pixels.
    
    Layout: 18 frames per row (9 proj0 + 9 proj1), 8 rows (angles).
    Each frame is 10 cols × 12 rows.
    """
    frames_per_angle = FRAMES_PER_ROW  # 18 = 9 anims × 2 proj
    total_anims = sum(ANIMS)  # 9
    angle = frame_index // frames_per_angle
    remainder = frame_index % frames_per_angle
    proj = 0 if remainder < total_anims else 1
    anim_idx = remainder if proj == 0 else remainder - total_anims
    fx = remainder * FRAME_W  # linear column position
    fy = angle * FRAME_H
    return fx, fy, angle, proj, anim_idx


def classify_position(
    glyph_l2: int, fg_l2: tuple, bg_l2: tuple,
    glyph_l3: int, fg_l3: tuple, bg_l3: tuple,
    y: int, split_row: int, rider_above: bool,
) -> list[dict]:
    """Classify a position into zero, one, or two domain cells.

    A single (x,y) position can have cells from both L2 (mount) and L3 (rider).
    Returns a list of cell dicts with domain classification.
    """
    cells = []

    # L2 classification
    has_l2 = bg_l2 != MAGENTA
    is_l2_fill = bg_l2 == YELLOW
    if has_l2 and not is_l2_fill:
        # Mount body cell: determine rear vs front based on rider position
        if rider_above:
            # Rider above: cells above split = rear, below = front
            domain = "mount_rear" if y < split_row else "mount_front"
        else:
            # Rider below: cells above split = front, below = rear
            domain = "mount_front" if y < split_row else "mount_rear"
        cells.append({
            "domain": domain,
            "source_layer": 2,
            "glyph": int(glyph_l2),
            "fg": f"#{fg_l2[0]:02x}{fg_l2[1]:02x}{fg_l2[2]:02x}",
            "bg": f"#{bg_l2[0]:02x}{bg_l2[1]:02x}{bg_l2[2]:02x}",
        })

    # L3 classification
    has_l3 = bg_l3 != MAGENTA
    if has_l3:
        cells.append({
            "domain": "rider",
            "source_layer": 3,
            "glyph": int(glyph_l3),
            "fg": f"#{fg_l3[0]:02x}{fg_l3[1]:02x}{fg_l3[2]:02x}",
            "bg": f"#{bg_l3[0]:02x}{bg_l3[1]:02x}{bg_l3[2]:02x}",
        })

    # Background: no L2 mount cell AND no L3 cell → background
    if not cells:
        cells.append({"domain": "background", "source_layer": None,
                       "glyph": 32})

    return cells


def generate_domain_map() -> dict:
    """Generate the full bare-combo domain map."""
    xp = XPFile(str(REPO_ROOT / "assets" / "sprites" / "wolfie-0000.xp"))
    l2 = xp.layers[2]
    l3 = xp.layers[3]

    output = {
        "schema_version": "0.1.0",
        "family": "wolfie",
        "reference_xp": "../../../../assets/sprites/wolfie-0000.xp",
        "frame_w": FRAME_W,
        "frame_h": FRAME_H,
        "grid_layout": {
            "angles": ANGLES,
            "projections": PROJECTIONS,
            "anim_counts": ANIMS,
            "frames_per_row": FRAMES_PER_ROW,
            "rows": ROWS,
        },
        "transparent_bg": "#ff00ff",
        "contract": "bare_combo_domain_v1",
        "domains": {
            "mount_rear": {
                "description": "Wolf mount body cells behind the rider (drawn first)",
                "slot_affinity": "mount",
            },
            "mount_front": {
                "description": "Wolf mount body/leg cells in front of the rider (drawn last)",
                "slot_affinity": "mount",
            },
            "rider": {
                "description": "Rider body cells from layer 3 overlay",
                "slot_affinity": "body",
                "source_layer": 3,
            },
            "background": {
                "description": "Transparent or subcell-fill cells",
                "slot_affinity": None,
            },
        },
        "frames": {},
    }

    total_frames = ANGLES * PROJECTIONS * sum(ANIMS)  # 8 * 2 * 9 = 144
    for frame_idx in range(total_frames):
        fx, fy, angle, proj, anim_idx = frame_origin(frame_idx)

        # Compute split row dynamically from rider position
        split_row, rider_above = compute_split_row(l2, l3, fx, fy)

        # Collect cells by domain
        domains = defaultdict(list)

        for y in range(fy, fy + FRAME_H):
            ly = y - fy
            for x in range(fx, fx + FRAME_W):
                lx = x - fx
                g2, fg2, bg2 = l2.data[y][x]
                g3, fg3, bg3 = l3.data[y][x]

                for cell_info in classify_position(
                    g2, fg2, bg2, g3, fg3, bg3, ly, split_row, rider_above
                ):
                    domain = cell_info.pop("domain")
                    cell = {"x": lx, "y": ly}
                    cell.update(cell_info)
                    domains[domain].append(cell)

        # Build frame entry
        regions = []
        for domain_name in ["mount_rear", "rider", "mount_front", "background"]:
            cells = domains.get(domain_name, [])
            if not cells:
                continue
            min_x = min(c["x"] for c in cells)
            min_y = min(c["y"] for c in cells)
            max_x = max(c["x"] for c in cells)
            max_y = max(c["y"] for c in cells)
            region = {
                "name": domain_name,
                "bbox": [min_x, min_y, max_x, max_y],
                "confidence": "high",
                "cell_count": len(cells),
                "semantic_cells": sorted(cells, key=lambda c: (c["y"], c["x"])),
            }
            if domain_name in ("mount_rear", "mount_front"):
                region["slot_affinity"] = "mount"
            elif domain_name == "rider":
                region["slot_affinity"] = "body"
            # source_layer is per-cell (preserved from classify_position)
            regions.append(region)

        anim_name = "idle" if anim_idx == 0 else f"walk_{anim_idx}"
        frame_key = str(frame_idx)
        output["frames"][frame_key] = {
            "angle": angle,
            "projection": proj,
            "anim_index": anim_idx,
            "anim_name": anim_name,
            "split_row": split_row,
            "rider_above": rider_above,
            "regions": regions,
        }

    return output


def main():
    print("Generating wolfie-0000 bare-combo domain map...")
    domain_map = generate_domain_map()

    output_path = (
        REPO_ROOT
        / "docs"
        / "research"
        / "ascii"
        / "semantic_maps"
        / "source_overlay_domains"
        / "wolfie-0000-source-overlay-domain.json"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(domain_map, f, indent=2)

    print(f"Wrote {len(domain_map['frames'])} frames to {output_path}")
    print(f"File size: {output_path.stat().st_size:,} bytes")

    # Quick stats
    total_cells = 0
    domain_counts = defaultdict(int)
    for fk, fv in domain_map["frames"].items():
        for r in fv["regions"]:
            domain_counts[r["name"]] += r["cell_count"]
            total_cells += r["cell_count"]
    print(f"\nTotal classified cells: {total_cells:,}")
    for name in ["mount_rear", "rider", "mount_front", "background"]:
        print(f"  {name}: {domain_counts[name]:,}")


if __name__ == "__main__":
    main()
