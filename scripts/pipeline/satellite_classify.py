#!/usr/bin/env python3
"""Classify satellite tile pixels into terrain material categories via HSV thresholds.

Usage:
  # Classify a tile and print statistics:
  python3 scripts/pipeline/satellite_classify.py --image tile.jpg

  # Classify and save color-coded visualization:
  python3 scripts/pipeline/satellite_classify.py --image tile.jpg --output classified.png

Material palette:
  0 = water (rgb 0,0,255)     H 140-200, S > 45, V 45-200
  1 = grass (rgb 0,180,0)     default (anything unmatched)
  2 = dirt  (rgb 139,90,43)   H 12-38, S 60-200, V 60-180
  3 = stone (rgb 150,150,150) S < 60, V 120-220 (low saturation = paved)
  4 = sand  (rgb 210,190,140) H 23-53, S 30-150, V 200-255

HSV convention: Pillow HSV mode (H=0-255, S=0-255, V=0-255).
"""
import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Material constants matching sbu_satellite_style_postprocess.py.
#
# FL-3838: WARNING — these names describe the CLASSIFIER output, NOT the
# rendered color!  The A3D palette renders mat_id=3 ("stone") as GREEN
# (BG=51,153,0 FG=255,255,153), same as grass.  mat_id=4 ("sand") renders
# as GREY (BG=153,153,153).  Before changing any material ID mapping or
# debugging terrain-colour bugs, always verify with:
#   python3 scripts/inspect_a3d.py <map.a3d> --terrain-colors
# The satellite_terrain_painter remaps untagged stone/sand pavement pixels to
# mat_id=2 (tan/sandy). OSM road ways own mat_id=4 so roads stay grey.
MAT_WATER = 0
MAT_GRASS = 1
MAT_DIRT = 2
MAT_STONE = 3
MAT_SAND = 4

MAT_NAMES = {0: "water", 1: "grass", 2: "dirt", 3: "stone", 4: "sand"}

# Visualization colours (RGB).
MAT_COLORS = {
    MAT_WATER: (0, 0, 255),
    MAT_GRASS: (0, 180, 0),
    MAT_DIRT: (139, 90, 43),
    MAT_STONE: (150, 150, 150),
    MAT_SAND: (210, 190, 140),
}

# ---------------------------------------------------------------------------
# Classifier table
# ---------------------------------------------------------------------------
# Each entry: (name, mat_id, hsv_ranges)
#
# HSV ranges use Pillow convention: H 0-255, S 0-255, V 0-255.
# Conversion from OpenCV (H 0-179) to Pillow (H 0-255): multiply by 255/179 ~ 1.42.
#
# Priority order: water checked first, then stone, dirt, sand.
# First match wins; unmatched pixels default to grass.
CLASSIFIERS = [
    # Water: blue hues, requires moderate saturation to avoid shadow-on-grass.
    # OpenCV H 90-130 -> Pillow H ~128-185.  S > 30 (Pillow scale).
    ("water", MAT_WATER, [((128, 30, 30), (185, 255, 200))]),
    # Stone: low saturation, mid-high brightness = paved/concrete.
    # Full H range (0-255), S < 55, V 80-220.
    # S raised from 40 to 55: shadowed concrete picks up vegetation hue reflection.
    ("stone", MAT_STONE, [((0, 0, 80), (255, 55, 220))]),
    # Dirt: warm brown hues, moderate saturation.
    # OpenCV H 8-25 -> Pillow H ~11-36.
    ("dirt", MAT_DIRT, [((11, 40, 40), (36, 200, 180))]),
    # Sand: warm hue, low-mid saturation, high brightness.
    # OpenCV H 15-35 -> Pillow H ~21-50.
    ("sand", MAT_SAND, [((21, 20, 160), (50, 100, 255))]),
]

# Shadow threshold: pixels darker than this are too ambiguous to classify.
SHADOW_V_THRESHOLD = 40

# Snow/cloud detection thresholds.
SNOW_S_MAX = 10       # very low saturation
SNOW_V_MIN = 230      # very bright
SNOW_PIXEL_RATIO = 0.40   # >40% snow pixels = unusable
SHADOW_PIXEL_RATIO = 0.30  # >30% shadow pixels = unusable


def classify_pixel(h: int, s: int, v: int,
                   classifiers: list | None = None,
                   shadow_threshold: int = SHADOW_V_THRESHOLD) -> int:
    """Classify a single HSV pixel (Pillow convention) into a material ID.

    Returns MAT_GRASS (1) if too dark or no classifier matches.
    """
    if v < shadow_threshold:
        return MAT_GRASS

    if classifiers is None:
        classifiers = CLASSIFIERS

    for _name, mat_id, hsv_ranges in classifiers:
        for (h_lo, s_lo, v_lo), (h_hi, s_hi, v_hi) in hsv_ranges:
            if h_lo <= h <= h_hi and s_lo <= s <= s_hi and v_lo <= v <= v_hi:
                return mat_id

    return MAT_GRASS


def classify_image(image: Image.Image,
                   classifiers: list | None = None) -> np.ndarray:
    """Classify all pixels in a PIL Image.

    Returns 2D numpy array (height x width) of material IDs (uint8).
    """
    if classifiers is None:
        classifiers = CLASSIFIERS

    hsv = image.convert("HSV")
    arr = np.array(hsv, dtype=np.int16)  # (H, W, 3)
    height, width = arr.shape[:2]

    h_ch = arr[:, :, 0]
    s_ch = arr[:, :, 1]
    v_ch = arr[:, :, 2]

    # Start everything as grass.
    result = np.full((height, width), MAT_GRASS, dtype=np.uint8)

    # Shadow gate: V < threshold stays grass.
    classifiable = v_ch >= SHADOW_V_THRESHOLD

    # Apply classifiers in priority order.  First match wins, so later
    # classifiers only write to pixels not yet claimed.
    claimed = ~classifiable  # shadow pixels are pre-claimed as grass

    for _name, mat_id, hsv_ranges in classifiers:
        for (h_lo, s_lo, v_lo), (h_hi, s_hi, v_hi) in hsv_ranges:
            match = (
                ~claimed
                & (h_ch >= h_lo) & (h_ch <= h_hi)
                & (s_ch >= s_lo) & (s_ch <= s_hi)
                & (v_ch >= v_lo) & (v_ch <= v_hi)
            )
            result[match] = mat_id
            claimed |= match

    return result


def detect_unusable_imagery(image: Image.Image) -> tuple[bool, str]:
    """Pre-scan a tile for snow/cloud coverage.

    Returns (is_unusable, reason).
    """
    hsv = image.convert("HSV")
    arr = np.array(hsv, dtype=np.int16)
    total = arr.shape[0] * arr.shape[1]

    s_ch = arr[:, :, 1]
    v_ch = arr[:, :, 2]

    # Snow/cloud: very low saturation + very bright.
    snow_count = int(np.sum((s_ch < SNOW_S_MAX) & (v_ch > SNOW_V_MIN)))
    snow_pct = snow_count / total

    if snow_pct > SNOW_PIXEL_RATIO:
        return True, f"snow/cloud: {snow_pct * 100:.1f}%"

    # Heavy shadow: very dark.
    shadow_count = int(np.sum(v_ch < SHADOW_V_THRESHOLD))
    shadow_pct = shadow_count / total

    if shadow_pct > SHADOW_PIXEL_RATIO:
        return True, f"shadow: {shadow_pct * 100:.1f}%"

    return False, ""


def main():
    parser = argparse.ArgumentParser(
        description="Classify satellite tile pixels into terrain materials"
    )
    parser.add_argument("--image", type=Path, required=True, help="Input tile image")
    parser.add_argument("--output", type=Path, default=None,
                        help="Save color-coded classification image")
    args = parser.parse_args()

    if not args.image.exists():
        print(f"ERROR: {args.image} not found", file=sys.stderr)
        return 1

    img = Image.open(args.image).convert("RGB")
    w, h = img.size
    print(f"Image: {args.image} ({w}x{h})")

    # Snow/cloud check.
    unusable, reason = detect_unusable_imagery(img)
    if unusable:
        print(f"WARNING: Imagery may be unusable -- {reason}")

    # Classify.
    materials = classify_image(img)

    # Statistics.
    total = w * h
    print(f"\nClassification ({total} pixels):")
    for mat_id in sorted(MAT_NAMES.keys()):
        count = int(np.sum(materials == mat_id))
        pct = count / total * 100
        print(f"  {MAT_NAMES[mat_id]:8s} (id={mat_id}): {count:7d} ({pct:5.1f}%)")

    # Visualization output.
    if args.output:
        vis = np.zeros((h, w, 3), dtype=np.uint8)
        for mat_id, color in MAT_COLORS.items():
            mask = materials == mat_id
            vis[mask] = color
        Image.fromarray(vis).save(args.output)
        print(f"\nVisualization saved: {args.output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
