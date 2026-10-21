"""Focused FL-4184 Product contract for contact-local grass rustle.

This source contract proves the ownership separation only.  It does not claim
headed Y8 visual acceptance.
"""
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"


def _body(source: str, signature: str) -> str:
    start = source.index(signature)
    open_brace = source.index("{", start)
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace:index + 1]
    raise AssertionError(f"unterminated function: {signature}")


def _code_without_line_comments(source: str) -> str:
    return "\n".join(line.split("//", 1)[0] for line in source.splitlines())


def test_player_proximity_cannot_change_stable_scatter_admission() -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    density = _body(source, "float foliage_scatter_density_at(")
    coverage = _body(source, "FoliageCoverage foliage_blade_coverage_at_cell(")

    assert "float foliage_scatter_density_at(ivec2 anchor_cell, FoliageSurfaceFacts sf)" in source
    assert "player_xz" not in density
    assert "exclusion_factor" not in density
    assert "foliage_player_exclusion_factor_from_player" not in density
    assert "foliage_scatter_density_at(anchor, anchor_facts)" in coverage


def test_rustle_uses_only_coverage_contact_and_preserves_flowers() -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    rustle = _body(source, "uint foliage_player_rustle_phase_offset(")
    saved = _body(source, "int foliage_saved_static_cycle_glyph(")
    code = _code_without_line_comments(rustle + "\n" + saved)

    assert "float contact_strength = clamp(1.0 - c.exclusion_factor, 0.0, 1.0);" in rustle
    assert "if (!c.present || c.role == FOLIAGE_ROLE_FLOWER || family_size == 0u) return 0u;" in rustle
    assert "params.feature_params.x * FOLIAGE_PRODUCT_PLAYER_RUSTLE_HZ" in rustle
    assert "c.clump_id ^ (c.blade_id * 2654435761u)" in rustle
    assert saved.count("foliage_static_cycle_phase_from_ticks(") == 2
    assert "uint rustle_phase3 = foliage_player_rustle_phase_offset(c, 3u);" in saved
    assert "uint rustle_phase6 = foliage_player_rustle_phase_offset(c, 6u);" in saved
    assert ") % 5u;" in saved
    for forbidden in ("camera", "cursor", "Input", "screen", "view_identity", "VSB_PROJECTION_"):
        assert forbidden not in code


def test_far_contact_is_an_exact_zero_offset_and_existing_families_remain() -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    rustle = _body(source, "uint foliage_player_rustle_phase_offset(")
    saved = _body(source, "int foliage_saved_static_cycle_glyph(")

    assert "if (contact_strength <= 0.0) return 0u;" in rustle
    for family in ("thai[3]", "mongolian[3]", "arabic6[6]", "arabic3[3]", "arabic_ramp[3]"):
        assert family in saved
    assert "(phase3 + rustle_phase3) % 3u" in saved
    assert "(phase6 + rustle_phase6) % 6u" in saved
