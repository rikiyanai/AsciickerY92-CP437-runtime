#!/usr/bin/env python3
"""Guard the FL-4231/FL-4359 stable world-Product terrain detail owner.

Product uses the selected Product's stable anchor for both the discrete glyph
row and the unquantized foreground/background matlib interpolation scalar.
Camera motion inside one semantic terrain cell therefore cannot change b55
paint in the R5 stability stage. The anchor is currently the VSB world-cell
center, so this contract does not claim the later VSB/Ebert intra-cell
continuous-footprint result. Terrain roles, edges, and mountains continue to
consume the receiver tuple.
"""

from __future__ import annotations
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"


def _compact(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    return re.sub(r"\s+", "", source)


def _function(source: str, signature: str) -> str:
    start = source.index(signature)
    opening = source.index("{", start)
    depth = 0
    for cursor in range(opening, len(source)):
        if source[cursor] == "{":
            depth += 1
        elif source[cursor] == "}":
            depth -= 1
            if depth == 0:
                return source[start : cursor + 1]
    raise AssertionError(f"unterminated function: {signature}")


def _replace_in_function(source: str, signature: str, old: str, new: str) -> str:
    function = _function(source, signature)
    assert function.count(old) == 1, (signature, old, function.count(old))
    return source.replace(function, function.replace(old, new, 1), 1)


def _assert_contract(source: str) -> None:
    detail = _compact(_function(source, "ProductTerrainDetailFacts product_terrain_detail_facts_from_corners("))
    stable_detail = _compact(_function(source, "ProductTerrainDetailFacts product_terrain_detail_facts("))
    shade = _compact(_function(source, "Q3ProbeTerrainShade q3_probe_payload_compile_terrain_shade("))
    material = _compact(_function(source, "bool q3_probe_payload_apply_material("))

    # One Product detail owner consumes the stable Product anchor and the four
    # authored corner heights. Camera-ray and screen coordinates are forbidden.
    assert source.count("ProductTerrainDetailFacts product_terrain_detail_facts_from_corners(") == 1
    assert source.count("ProductTerrainDetailFacts product_terrain_detail_facts(") == 1
    assert "ProductTerrainDetailPair" not in source
    assert detail.count("corner_height(") == 0
    assert stable_detail.count("corner_height(") == 4
    assert "stable_anchor_world" in stable_detail
    assert "terrain_world.xz" in detail
    assert "product_terrain_world_detail_noise(" in detail
    assert "facts.local_jitter=(product_terrain_world_detail_noise(world_relative_xz)-0.5)*0.090;" in detail
    for forbidden in (
        "inv_view_proj",
        "ViewRay",
        "ray.",
        "ansi_xy",
        "slot_xy",
        "camera",
        "view_identity",
        "effective_sun_dir",
    ):
        assert forbidden not in detail, forbidden

    # The stable Product anchor controls both the discrete glyph row and the
    # unquantized matlib interpolation scalar. This stage's "continuous" value
    # means interpolation between authored rows at the retained cell-center
    # anchor; it does not claim the later view-independent intra-cell footprint
    # or grant camera-ray ownership of paint.
    assert (
        "ProductTerrainDetailFactsstable_detail="
        "product_terrain_detail_facts(cell,stable_anchor_world);"
    ) in shade
    assert "terrain_ground_world" not in shade
    assert "continuous_detail" not in shade
    assert "stable_detail.local_jitter" in shade
    assert "shade.shd=product_quantise_shd(stable_lit,dither_t);" in shade
    assert "shade.shd_continuous=stable_lit*15.0;" in shade
    assert "floor(stable_anchor_world.y)" in shade
    assert "shade.detail_normal" not in material
    assert "product_terrain_ramp_facts(" in material
    assert "matlib_fg,matlib_bg,nrm," in material
    assert "precisevec3nrm=vsb_product_selected_terrain_receiver.normal;" in material
    assert "terrain_subcell_world_xz(" not in source
    assert "product_bilinear_normal(" not in source


def test_world_ground_detail_owner_is_connected() -> None:
    _assert_contract(_expand_includes(PRODUCT.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    ("signature", "old", "new"),
    (
        (
            "ProductTerrainDetailFacts product_terrain_detail_facts_from_corners(",
            "facts.local_jitter =",
            "facts.local_jitter = 0.0; // mutation",
        ),
        (
            "Q3ProbeTerrainShade q3_probe_payload_compile_terrain_shade(",
            "+ jitter + stable_detail.local_jitter",
            "+ jitter",
        ),
        (
            "Q3ProbeTerrainShade q3_probe_payload_compile_terrain_shade(",
            "vec3 stable_anchor_world,",
            "vec3 terrain_ground_world,",
        ),
        (
            "Q3ProbeTerrainShade q3_probe_payload_compile_terrain_shade(",
            "shade.shd_continuous = stable_lit * 15.0;",
            "shade.shd_continuous = float(shade.shd);",
        ),
    ),
)
def test_detail_reuse_or_second_owner_mutations_turn_red(
    signature: str, old: str, new: str
) -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    mutant = _replace_in_function(source, signature, old, new)
    with pytest.raises(AssertionError):
        _assert_contract(mutant)
