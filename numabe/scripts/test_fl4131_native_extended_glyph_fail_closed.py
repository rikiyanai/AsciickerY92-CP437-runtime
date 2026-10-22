#!/usr/bin/env python3
"""FL-4131 native renderer must fail closed for unknown extended GlyphIds."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TERM = ROOT / "platform" / "terminal_gl_present.cpp"
RESOLVE = ROOT / "engine" / "render" / "render_resolve.cpp"


def test_native_renderer_uses_manifest_bounded_extended_glyph_range() -> None:
    term = TERM.read_text()
    required_terms = [
        "NativeRenderGlyphSidecarWrite",
        "TermLoadCompiledGlyphAtlasPage",
        "kTermCompiledGlyphFirstId = 512",
        "kTermCompiledGlyphLastIdMin = 647",
        "kTermCompiledGlyphLastIdMax = 16383",
        "TermLoadAtlasOfAtlasesMaxGlyphId",
        "term->native_glyph_last_id = (uint32_t)last_id",
        "const uint native_glyph_first_id = 512u",
        "uniform uint native_glyph_last_id",
        "diagnostic_failure = true",
    ]
    for needle in required_terms:
        assert needle in term, f"native terminal renderer missing {needle!r}"
    assert "kTermCompiledGlyphLastId = 631" not in term


def test_native_shader_bounds_checks_extended_glyphs_before_lut_sampling() -> None:
    term = TERM.read_text()
    shader_block = term.split("uint sidecar_id = texture(sidecar, ansi_coord).r;", 1)[1]
    shader_block = shader_block.split("color = vec4", 1)[0]

    assert (
        "sidecar_id >= native_glyph_first_id && sidecar_id <= native_glyph_last_id"
        in shader_block
    )
    assert "float lut_u = (float(sidecar_id) + 0.5) / lut_width;" in shader_block
    lut_index = shader_block.find("float lut_u")
    unknown_fallback_index = shader_block.find(
        "} else {\n\t\t\t\t\t\t\t\tdiagnostic_failure = true", lut_index
    )
    assert unknown_fallback_index > lut_index
    assert (
        "vec3 fg_color = diagnostic_failure ? vec3(1.0, 1.0, 0.0) : Pal(cell.x * 255.00);"
        in shader_block
    )
    assert (
        "vec3 bg_color = diagnostic_failure ? vec3(0.0, 0.0, 0.0) : Pal(cell.y * 255.00);"
        in shader_block
    )


def test_render_resolve_keeps_zero_coverage_diagnostic_sidecar_fail_closed() -> None:
    resolve = RESOLVE.read_text()
    assert "coverage != 0" not in resolve
    assert "diagnostic colors as a fail-safe" in resolve
