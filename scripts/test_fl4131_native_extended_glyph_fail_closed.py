#!/usr/bin/env python3
"""FL-4131 native renderer must fail closed for unknown extended GlyphIds."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
TERM = ROOT / "platform" / "terminal_gl_present.cpp"
RESOLVE = ROOT / "engine" / "render" / "render_resolve.cpp"


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


term = TERM.read_text()
resolve = RESOLVE.read_text()

required_terms = [
    "NativeRenderGlyphSidecarWrite",
    "TermLoadCompiledGlyphAtlasPage",
    "kTermCompiledGlyphFirstId = 512",
    "kTermCompiledGlyphLastId = 631",
    "native_glyph_first_id = 512u",
    "native_glyph_last_id = 631u",
    "diagnostic_failure = true",
    "vec3(1.0, 0.0, 0.0)",
]
for needle in required_terms:
    if needle not in term:
        fail(f"native terminal renderer missing {needle!r}")

shader_block = term.split("uint sidecar_id = texture(sidecar, ansi_coord).r;", 1)[1]
shader_block = shader_block.split("color = vec4", 1)[0]
if "sidecar_id >= native_glyph_first_id && sidecar_id <= native_glyph_last_id" not in shader_block:
    fail("native shader does not bound-check extended GlyphIds before LUT sampling")
if "float lut_u = (float(sidecar_id) + 0.5) / lut_width;" not in shader_block:
    fail("native shader no longer samples admitted GlyphIds through the LUT")
if shader_block.find("diagnostic_failure = true") < shader_block.find("float lut_u"):
    fail("diagnostic branch appears before admitted LUT path; test needs review")

if "coverage != 0" not in resolve:
    fail("render resolve writes sidecar even for zero-coverage diagnostic cells")

print("PASS: FL-4131 native extended glyph rendering fails closed for unknown GlyphIds")
