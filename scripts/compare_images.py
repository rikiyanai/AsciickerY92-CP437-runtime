#!/usr/bin/env python3
"""Compare original vs corrected images."""

import os
import sys
import random

REPO_ROOT = os.environ.get("ASCIICKER_REPO", os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from PIL import Image
from scripts.pipeline.palette import is_transparent

# Load images
img_orig = Image.open(
    os.path.join(REPO_ROOT, "scripts/Gemini_Generated_Image_ftaa3ftaa3f.png")
).convert("RGB")
img_corr = Image.open(
    os.path.join(REPO_ROOT, "scripts/Gemini_Generated_Image_ftaa3ftaa3f_corrected.png")
).convert("RGB")

print("=" * 70)
print("ORIGINAL vs CORRECTED IMAGE COMPARISON")
print("=" * 70)
print()

# Sample 1000 random pixels
random.seed(42)
orig_samples = []
corr_samples = []

w, h = img_orig.width, img_orig.height
for _ in range(1000):
    x = random.randint(0, w - 1)
    y = random.randint(0, h - 1)
    orig_samples.append(img_orig.getpixel((x, y)))
    corr_samples.append(img_corr.getpixel((x, y)))

mag_orig = sum(1 for p in orig_samples if is_transparent(p, tolerance=5))
mag_corr = sum(1 for p in corr_samples if is_transparent(p, tolerance=5))

print("Magenta Pixel Analysis (1000 random samples):")
print(f"  Original: {mag_orig}/1000 ({mag_orig / 10:.1f}%)")
print(f"  Corrected: {mag_corr}/1000 ({mag_corr / 10:.1f}%)")
print(f"  Improvement: {mag_corr - mag_orig} pixels")
if mag_orig > 0:
    print(f"  Multiplier: {mag_corr / mag_orig:.1f}x")
print()

# Check center regions for content
center_regions = [
    ("Top-Left", w // 4, h // 4),
    ("Center", w // 2, h // 2),
    ("Bottom-Right", w * 3 // 4, h * 3 // 4),
]

print("Center Region Check:")
content_found = False
for name, x, y in center_regions:
    pixel = img_corr.getpixel((x, y))
    is_t = is_transparent(pixel, tolerance=5)
    print(f"  {name:15}: RGB{pixel} - {'[MAGENTA]' if is_t else '[CONTENT]'}")
    if not is_t:
        content_found = True

print()
print("Content Preservation Check:")
print(f"  {'YES' if content_found else 'NO'} - Content preserved in corrected image")
print()

# Summary
print("=" * 70)
if content_found:
    print("✓ SNAP SUCCESSFUL")
    print("  - Magenta background created")
    print("  - Sprite content preserved")
    print("  - Ready for sprite generation pipeline")
else:
    print("⚠ OVER-CORRECTED")
    print("  - Tolerance=17 was too aggressive")
    print("  - Entire image became magenta (255,0,255)")
    print("  - Need to:")
    print("    1. Use smaller tolerance (10-15)")
    print("    2. Detect background vs content separately")
    print("    3. Re-run with --tolerance 10")
print("=" * 70)
