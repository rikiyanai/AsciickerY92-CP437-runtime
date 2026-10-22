#!/usr/bin/env python3
"""Source contract: live compiled profiles never revive CP437 on absence."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESOLVER = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text()
PRESENTER = (ROOT / "platform/terminal_gl_present.cpp").read_text()


def test_empty_tonal_route_reaches_shared_absence_selector() -> None:
    selector = RESOLVER.split("static uint32_t Fl4260ProfileSelectGlyph", 1)[1]
    selector = selector.split("GlyphId Fl4260GetRawSelection", 1)[0]
    assert "const bool ramp_claims" not in selector
    assert "const bool density_claims" not in selector
    assert "Fl4260MultiBucketPickBestL2(lanes, 2" in selector
    assert "return Fl4260MultiBucketPickBestL2(lanes, 2" in selector


def test_live_material_path_has_no_cp437_none_fallback() -> None:
    live_path = RESOLVER.split("uint32_t gid = GLYPH_ID_NONE;", 1)[1]
    live_path = live_path.split("if (!glyph_id_is_extended", 1)[0]
    assert "cp437_default_cells++" not in live_path
    assert "Fl4131RuntimeEmitMarker(out, FL4131_MARKER_MISSING_GLYPH);" in live_path


def test_presenter_counts_cp437_as_an_invariant_failure_not_a_resolution() -> None:
    assert "silent_cp437_fallback=%d" in PRESENTER
    assert "which must be ZERO once the" in PRESENTER
