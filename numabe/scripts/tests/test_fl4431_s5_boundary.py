from __future__ import annotations
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"

from scripts.tests.test_fl4359_rain_snow_unowned_bg import (
    _expand_includes as _expand_product_includes,
)


def _product_source() -> str:
    # Foliage scatter selection moved to product_foliage.glslinc in the
    # Sep-12 handoff extraction. Guards read the expanded translation unit.
    return _expand_product_includes(_expand_includes(PRODUCT.read_text(encoding="utf-8")))


def _function_body(source: str, signature: str) -> str:
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


def _without_line_comments(source: str) -> str:
    return "\n".join(line.split("//", 1)[0] for line in source.splitlines())


def test_s5b_does_not_change_bloom_admission() -> None:
    source = _product_source()
    body = _function_body(source, "bool foliage_scatter_accept_anchor(")
    # Bloom is a sparse habitat-cluster role, not a global density scalar:
    # clusters only, at 0.45x density; fill at 0.62x; all other slots full.
    assert "float bloom_budget = (cluster > 0.72 && habitat.b > 0.32) ? density * 0.45 : 0.0;" in body
    assert "slot == FOLIAGE_CLUMP_SLOT_BLOOM ? bloom_budget" in body
    assert "density * (slot == FOLIAGE_CLUMP_SLOT_FILL ? 0.62 : 1.0)" in body
    assert "density * 0.90" not in body
    assert "bloom_scale" not in body
    assert "VSB_PROJECTION_ORTHOGRAPHIC" not in body


def test_s5b_defers_flower_priority_to_actual_mask_coverage() -> None:
    source = _product_source()
    depth = _function_body(source, "float foliage_accent_depth_pull(")
    coverage = _function_body(source, "FoliageCoverage foliage_blade_coverage_at_cell(")

    assert "float foliage_accent_depth_pull(int variant, float local_v)" in source
    assert "return accent * 0.10 * smoothstep(0.45, 0.94, local_v);" in depth
    assert "shape" not in depth
    assert "flower * 0.42" not in depth
    assert "bool candidate_is_flower = mask.shape == FOLIAGE_SHAPE_FLOWER;" in coverage
    assert "bool incumbent_is_flower = c.present && c.role == FOLIAGE_ROLE_FLOWER;" in coverage
    assert "if (candidate_is_flower != incumbent_is_flower)" in coverage
    assert "if (!candidate_is_flower) continue;" in coverage
    assert "else if (depth_key <= c.depth_key)" in coverage
    assert "VSB_PROJECTION_ORTHOGRAPHIC" not in coverage


def test_saved_cycle_has_one_projection_independent_selector() -> None:
    source = _product_source()
    ticks = _function_body(source, "uint foliage_clump_cycle_ticks(")
    saved = _function_body(source, "int foliage_saved_static_cycle_glyph(")
    selector = _function_body(source, "int foliage_glyph_for_coverage(")

    assert "uint foliage_clump_cycle_ticks()" in source
    assert "params.feature_params.x * 5.0" in ticks
    assert saved.count("foliage_clump_cycle_ticks()") == 1
    assert saved.count("foliage_static_cycle_phase_from_ticks(") == 2
    assert "c.role == FOLIAGE_ROLE_FLOWER" in saved
    assert ") % 5u;" in saved
    assert selector.count("foliage_saved_static_cycle_glyph(c)") == 1
    assert "cycle_glyph != GLYPH_ID_NONE ? cycle_glyph : base_glyph" in selector
    for body in map(_without_line_comments, (ticks, saved, selector)):
        assert "VSB_PROJECTION_" not in body
        assert "view_identity" not in body
        assert "camera" not in body
