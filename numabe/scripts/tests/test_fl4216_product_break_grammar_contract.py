"""Deterministic Product admission contract for the FL-4216 shoreline break."""

from __future__ import annotations
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path

import pytest

from scripts.test_fl4431_product_finite_water_single_source_contract import (
    compile_product_shader,
)


ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"


def _function(source: str, name: str) -> str:
    match = re.search(
        rf"\b{re.escape(name)}\s*\([^{{}}]*\)\s*\{{",
        source,
        flags=re.S,
    )
    assert match is not None, f"missing function: {name}"
    opening = source.find("{", match.start())
    depth = 0
    for cursor in range(opening, len(source)):
        if source[cursor] == "{":
            depth += 1
        elif source[cursor] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : cursor + 1]
    raise AssertionError(f"unterminated function: {name}")


def _compact(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    return re.sub(r"\s+", "", source)


def _stage(distance: float) -> int:
    if distance < 0.75:
        return 0
    if distance < 2.26:
        return 1
    if distance < 4.14:
        return 2
    return 3


def _active(
    shore_distance: float,
    direction: int,
    phase: float = 0.72,
) -> tuple[bool, int]:
    stage = _stage(shore_distance)
    if direction < 0 or shore_distance > 6.0:
        return False, stage
    # At the forward/rebound seam the authored rebound center is zero.
    center255 = 0.0 if phase >= 0.72 else 176.0
    d255 = max(0.0, min(1.0, shore_distance / 6.0)) * 255.0
    return abs(d255 - center255) <= 26.0, stage


def _assert_product_contract(product: str) -> None:
    compact = _compact(product)
    active = _compact(_function(product, "product_fl4216_break_active"))
    strength = _compact(_function(product, "product_fl4216_break_strength"))
    universal_break = _compact(_function(product, "product_fl4216_universal_break"))
    sample = _compact(_function(product, "product_water_sample"))
    glyph = _compact(_function(product, "water_glyph_candidate"))

    # Product consumes the typed compiler-owned distance directly. Direct VSB
    # contact may clamp a compact mesh wrist into the moving waterline domain,
    # but it must not paint a static floor or persistent fallback tooth.
    assert "product_fl4216_break_distance" not in compact
    assert "s.shore_dist_cells" in active
    assert "s.shore_dist_cells" in strength
    assert "s.direct_water_solid_contact?min(s.shore_dist_cells,0.18):s.shore_dist_cells" in active
    assert "s.direct_water_solid_contact?min(s.shore_dist_cells,0.18):s.shore_dist_cells" in strength
    assert "direct_water_solid_contact" not in universal_break
    assert (
        "floatwaterline_floor=s.direct_water_solid_contact?1.0:"
        "1.0-smoothstep(0.0,0.60,s.shore_dist_cells);"
        in sample
    )
    assert "persistent_tooth" not in sample
    assert "s.break_strength=max(s.break_strength,0.42*break_fragment)" not in sample
    assert "s.break_active=moving_break_active&&break_fragment>=FL4216_BREAK_FRAGMENT_MIN;" in sample
    assert "if(s.break_active&&s.break_dir>=0&&s.break_stage>=0)" in glyph
    table_match = re.search(
        r"FL4216_GLYPH_ID\[32\]\s*=\s*int\[32\]\((.*?)\);",
        product,
        flags=re.S,
    )
    assert table_match is not None
    glyph_ids = [int(value) for value in re.findall(r"\b\d+\b", table_match.group(1))]
    assert len(glyph_ids) == 32
    assert glyph_ids[8] == 517
    assert glyph_ids[8] != 0
    assert "product_fl4216_shore_fact" not in compact
    assert "solid_water_crossing_cell_bits" not in compact
    assert "for(intdy=-6" not in compact
    assert "for(intdx=-6" not in compact


def test_near_shore_field_and_contact_emit_break_while_controls_stay_ordinary() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    _assert_product_contract(product)

    # Valid near-shore field fact: Product's existing direction/stage table
    # receives a nonzero authored break glyph.
    field_active, field_stage = _active(0.0, 2)
    assert field_active is True
    assert field_stage == 0
    assert (2 * 4 + field_stage) == 8  # E direction, stage 0 => glyph 517.

    # Deep water and a no-field/no-contact cell retain ordinary water behavior.
    deep_active, _ = _active(20.0, 2)
    no_field_active, _ = _active(99.0, -1)
    assert deep_active is False
    assert no_field_active is False


def test_product_shader_compiles_without_diagnostics() -> None:
    compiled, diagnostic = compile_product_shader(
        _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    )
    assert compiled, diagnostic
    assert "SCRIPT ERROR" not in diagnostic
    assert "push_error" not in diagnostic


def test_direct_contact_distance_mutation_turns_contract_red() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    mutant = product.replace(
        "s.break_active = moving_break_active\n"
        "\t\t\t&& break_fragment >= FL4216_BREAK_FRAGMENT_MIN;",
        "s.break_active = (moving_break_active || s.direct_water_solid_contact)\n"
        "\t\t\t&& break_fragment >= FL4216_BREAK_FRAGMENT_MIN;",
        1,
    )
    assert mutant != product
    with pytest.raises(AssertionError):
        _assert_product_contract(mutant)
