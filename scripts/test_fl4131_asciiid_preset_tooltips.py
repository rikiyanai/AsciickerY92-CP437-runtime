#!/usr/bin/env python3
"""FL-4131 ASCIIID preset tooltip contract checks."""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ASCIIID = REPO_ROOT / "editor" / "asciiid.cpp"


def main() -> int:
    text = ASCIIID.read_text(encoding="utf-8")
    errors: list[str] = []
    if "FormatAsciiidExtendedPresetTooltip" not in text:
        errors.append("missing FormatAsciiidExtendedPresetTooltip helper")
    if "GlyphIds/fallbacks:" not in text:
        errors.append("extended preset tooltip does not label GlyphIds/fallbacks")
    if "AsciiidGlyphFallbackByte(preset.glyphs[i])" not in text:
        errors.append("extended preset tooltip does not derive fallback bytes from preset glyph ids")
    if not re.search(r'SetTooltip\([^;]*tooltip\)', text):
        errors.append("ImGui tooltip is not using the formatted preset tooltip text")
    if "FL4131_DUMP_EXTENDED_PRESET_TOOLTIP" not in text:
        errors.append("headed CDP proof cannot query the formatted preset tooltip")
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("PASS: FL-4131 ASCIIID extended preset tooltips include GlyphIds/fallbacks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
