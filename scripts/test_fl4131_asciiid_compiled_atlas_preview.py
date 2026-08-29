#!/usr/bin/env python3
"""Regression checks for ASCIIID extended glyph preview using compiled atlas artifacts."""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ASCIIID = REPO_ROOT / "editor/asciiid.cpp"


def main() -> int:
    text = ASCIIID.read_text(encoding="utf-8", errors="replace")
    errors: list[str] = []
    required = [
        "kAsciiidCompiledAtlasPagePath",
        "assets/glyphs/atlases/material.additive.v1.page0_rgba8.json",
        "LoadAsciiidCompiledAtlasPage",
        "AsciiidCompiledAtlasGlyphCell",
        "compiled_atlas_pixels",
    ]
    for needle in required:
        if needle not in text:
            errors.append(f"missing compiled atlas preview hook: {needle}")
    sample_block = text.split("static AsciiidExtendedGlyphVisualSample SampleAsciiidExtendedGlyphVisual", 1)
    if len(sample_block) != 2:
        errors.append("missing SampleAsciiidExtendedGlyphVisual")
    else:
        body = sample_block[1].split("static bool DrawAsciiidExtendedGlyphButton", 1)[0]
        if "material_glyph_plane_coverage_display_glyph" in body:
            errors.append("ASCIIID extended preview still collapses GlyphIds to coverage display glyph")
        if "AsciiidCompiledAtlasGlyphCell" not in body:
            errors.append("ASCIIID extended preview does not sample compiled atlas cells")
    material_data = text.split('ImGui::Text("Material data")', 1)
    if len(material_data) != 2:
        errors.append("missing visible Material data preview")
    else:
        body = material_data[1].split('ImGui::Separator();\n\t\t\tImGui::Text("Row controls")', 1)[0]
        if "DrawAsciiidExtendedGlyphButton" not in body:
            errors.append("visible Material data preview does not render extended GlyphIds through compiled atlas")
        if "display_glyph_id > 0xFF" not in body:
            errors.append("visible Material data preview does not branch on extended GlyphIds")
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("PASS: FL-4131 ASCIIID extended preview uses compiled atlas artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
