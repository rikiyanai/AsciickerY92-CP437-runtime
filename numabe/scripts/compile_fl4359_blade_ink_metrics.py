#!/usr/bin/env python3
"""Compile native atlas ink endpoints for rooted blade presentation (FL-4359)."""
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
GLYPHS = (42, 43, 44, 46, 179, 556, 559, 560, 561, 562, 563, 600, 601,
          740, 742, 745, 751, 756, 761, *range(764, 770), *range(774, 780))


def compile_metrics() -> None:
    atlas = Image.open(ROOT / "godot_project/assets/glyphs/atlases/material.additive.v1.page32.png").convert("RGBA")
    base_atlas = Image.open(ROOT / "godot_project/assets/font.png").convert("RGBA")
    morphology = Image.open(ROOT / "godot_project/assets/glyphs/atlases/material.morphology.v2.page32.png").convert("RGBA")
    lines = ["// FL-4359: generated from actual atlas ink; regenerate with",
             "// scripts/compile_fl4359_blade_ink_metrics.py.",
             "vec4 rooted_glyph_ink_endpoints(uint glyph) {"]
    bounds = []
    for glyph in GLYPHS:
        page = base_atlas if glyph < 256 else (morphology if glyph >= 672 else atlas)
        channel = 0 if glyph < 256 else 3
        size = page.width // 16
        index = glyph if glyph < 256 else glyph - (672 if glyph >= 672 else 512)
        cell = page.crop(((index % 16) * size, (index // 16) * size,
                           (index % 16 + 1) * size, (index // 16 + 1) * size))
        ink = [(x, y, cell.getpixel((x, y))[channel] / 255) for y in range(size) for x in range(size)
               if cell.getpixel((x, y))[channel] > 0]
        if not ink:
            raise ValueError(f"glyph {glyph} has no source ink")
        xmin, xmax = min(p[0] for p in ink), max(p[0] for p in ink)
        ymin, ymax = min(p[1] for p in ink), max(p[1] for p in ink)
        bottom = [p for p in ink if p[1] >= ymax - 2]
        top = [p for p in ink if p[1] <= ymin + 2]
        def centroid(points):
            weight = sum(p[2] for p in points)
            return [sum((p[a] + .5) * p[2] for p in points) / weight / size for a in range(2)]
        coords = centroid(bottom) + centroid(top)
        lines.append(f"    if (glyph == {glyph}u) return vec4({', '.join(f'{v:.8f}' for v in coords)});")
        bounds.append(f"    if (glyph == {glyph}u) return vec4({xmin / size:.8f}, {ymin / size:.8f}, {(xmax + 1) / size:.8f}, {(ymax + 1) / size:.8f});")
    lines += ["    return vec4(0.5, 0.9, 0.5, 0.1);", "}", "vec4 rooted_glyph_ink_bounds(uint glyph) {"]
    lines += bounds + ["    return vec4(0.0, 0.0, 1.0, 1.0);", "}", ""]
    (ROOT / "godot_project/shaders/rooted_glyph_ink_metrics.glslinc").write_text("\n".join(lines))


if __name__ == "__main__":
    compile_metrics()
