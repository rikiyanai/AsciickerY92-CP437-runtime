#!/usr/bin/env python3
"""FL-4131 ASCIIID extended preset UX ordering checks."""

from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ASCIIID = REPO_ROOT / "editor" / "asciiid.cpp"
GENERATED_PRESETS = REPO_ROOT / "assets/glyphs/generated/material_shape_presets.json"


def main() -> int:
    text = ASCIIID.read_text(encoding="utf-8")
    generated = json.loads(GENERATED_PRESETS.read_text(encoding="utf-8"))
    errors: list[str] = []

    material_header = 'ImGui::Text("Extended Shape Presets")'
    preset_loop = "for (int preset_index = 0; preset_index < kAsciiidExtendedGlyphPresetCount; preset_index++)"
    swatch_button = "DrawAsciiidExtendedGlyphPresetStripButton"
    click_apply = "AsciiidApplyExtendedPresetToActiveMaterial(preset);"
    generated_purposes = {str(p["purpose"]) for p in generated["presets"]}
    required_purposes = {"Grass Tops", "Wave Flow", "Flower Tops", "Rock Faces", "Strata", "Corners / Lips", "Fracture"}
    retired_presets = [
        '"WATER", "Contour Flow"',
        '"STONE", "Hard Fracture"',
        '"KATAKANA", "Dirt"',
    ]

    for required in (material_header, preset_loop, swatch_button, click_apply):
        if required not in text:
            errors.append(f"missing expected UI token: {required}")
    if generated_purposes != required_purposes:
        errors.append(f"generated preset purposes mismatch: {sorted(generated_purposes)!r}")
    if len(generated["presets"]) != 7:
        errors.append(f"generated preset count is {len(generated['presets'])}, expected 7")
    for retired in retired_presets:
        if retired in text:
            errors.append(f"retired 23-preset material-family entry still present: {retired}")

    if not errors:
        material_index = text.index(material_header)
        preset_index = text.index(preset_loop, material_index)
        if not (material_index < preset_index):
            errors.append("extended shape presets must render under the generated preset heading")

    if 'ImGui::Text("Extended Presets")' in text:
        errors.append("old extended preset heading should not expose fallback buttons as the primary UX")
    visible_panel = text[
        text.index(material_header):
        text.index("FormatAsciiidExtendedPresetTooltip", text.index(material_header))
    ]
    if "fallback" in visible_panel.lower():
        errors.append("visible extended material preset panel must not expose fallback wording")

    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("PASS: FL-4131 ASCIIID generated shape presets render as extended glyph swatches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
