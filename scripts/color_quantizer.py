"""
ColorQuantizer - 16-color ANSI palette quantization using Euclidean distance.

This module provides functionality to map any RGB pixel color to the nearest
color in the 16-color ANSI palette used by Asciicker. The quantization uses
squared Euclidean distance to avoid expensive sqrt operations.

Palette mapping:
- Black (0): (0, 0, 0)
- Red (1): (128, 0, 0)
- Green (2): (0, 128, 0)
- Yellow (3): (128, 128, 0)
- Blue (4): (0, 0, 128)
- Magenta (5): (128, 0, 128)
- Cyan (6): (0, 128, 128)
- White (7): (192, 192, 192)
- Bright Black (8): (128, 128, 128)
- Bright Red (9): (255, 0, 0)
- Bright Green (10): (0, 255, 0)
- Bright Yellow (11): (255, 255, 0)
- Bright Blue (12): (0, 0, 255)
- Bright Magenta (13): (255, 0, 255)
- Bright Cyan (14): (0, 255, 255)
- Bright White (15): (255, 255, 255)
"""

# ANSI Colors (0-15) mapping to RGB
ANSI_COLORS = [
    (0, 0, 0),  # 0: Black
    (128, 0, 0),  # 1: Red
    (0, 128, 0),  # 2: Green
    (128, 128, 0),  # 3: Yellow
    (0, 0, 128),  # 4: Blue
    (128, 0, 128),  # 5: Magenta
    (0, 128, 128),  # 6: Cyan
    (192, 192, 192),  # 7: White
    (128, 128, 128),  # 8: Bright Black
    (255, 0, 0),  # 9: Bright Red
    (0, 255, 0),  # 10: Bright Green
    (255, 255, 0),  # 11: Bright Yellow
    (0, 0, 255),  # 12: Bright Blue
    (255, 0, 255),  # 13: Bright Magenta
    (0, 255, 255),  # 14: Bright Cyan
    (255, 255, 255),  # 15: Bright White
]


def quantize_rgb_to_index(r, g, b):
    """
    Quantize an RGB color to the nearest palette index.

    Uses squared Euclidean distance to find the closest color in ANSI_COLORS.
    No sqrt is computed since we only need to find the minimum distance.

    Args:
        r: Red component (0-255)
        g: Green component (0-255)
        b: Blue component (0-255)

    Returns:
        int: Index (0-15) of the nearest palette color
    """
    min_dist = float("inf")
    best_idx = 0

    for idx, (pr, pg, pb) in enumerate(ANSI_COLORS):
        # Squared Euclidean distance (no sqrt needed for ranking)
        dist = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2

        if dist < min_dist:
            min_dist = dist
            best_idx = idx

    return best_idx
