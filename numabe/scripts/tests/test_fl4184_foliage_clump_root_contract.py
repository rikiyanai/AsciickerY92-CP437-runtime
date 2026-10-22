#!/usr/bin/env python3
"""FL-4184 world-fixed foliage clump-root ownership contract.

This is destructive source proof. It establishes the Product ownership and
query shape introduced by runtime commit 4a87d2aed; it is not headed visual
evidence and does not accept foliage appearance.
"""

from __future__ import annotations
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
import tempfile
from pathlib import Path

import pytest

from scripts import test_fl4057_probe_pipeline_contract as probe_contract


ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"

from scripts.tests.test_fl4359_rain_snow_unowned_bg import (
    _expand_includes as _expand_product_includes,
)


def _product_source() -> str:
    # Foliage ownership lives in product_foliage.glslinc since the Sep-12
    # handoff extraction. Contract and mutants run on the expanded unit.
    return _expand_product_includes(_expand_includes(PRODUCT.read_text(encoding="utf-8")))


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", _without_comments(source))


def _function(source: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{", source, re.S)
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


def _replace_in_function(source: str, name: str, old: str, new: str) -> str:
    function = _function(source, name)
    assert function.count(old) == 1, (name, old, function.count(old))
    return source.replace(function, function.replace(old, new, 1), 1)


def _replace_once(source: str, old: str, new: str) -> str:
    assert source.count(old) == 1, (old, source.count(old))
    return source.replace(old, new, 1)


def _assert_clump_root_contract(source: str) -> None:
    compact = _compact(source)
    patch_anchor = _compact(_function(source, "foliage_dense_card_patch_anchor"))
    patch_owner = _compact(_function(source, "foliage_dense_card_patch_owner"))
    density = _compact(_function(source, "foliage_scatter_density_at"))
    admission = _compact(_function(source, "foliage_scatter_accept_anchor"))
    blade = _compact(_function(source, "foliage_blade_from_anchor"))
    coverage = _compact(_function(source, "foliage_blade_coverage_at_cell"))
    foliage_finish = _compact(_function(source, "q3_probe_payload_apply_foliage"))

    # One historical patch anchor identifies each signed 4x4 tile. One helper
    # hashes that identity to exactly one clamped in-tile root.
    assert patch_anchor == (
        "foliage_dense_card_patch_anchor(ivec2cell){"
        "constintFOLIAGE_CLUMP_SIZE=4;"
        "ivec2q=ivec2(product_foliage_floor_div(cell.x,FOLIAGE_CLUMP_SIZE),"
        "product_foliage_floor_div(cell.y,FOLIAGE_CLUMP_SIZE));"
        "returnq*FOLIAGE_CLUMP_SIZE+ivec2(1,1);}"
    )
    assert patch_owner == (
        "foliage_dense_card_patch_owner(ivec2patch_anchor){"
        "ivec2patch_origin=patch_anchor-ivec2(1,1);"
        "ivec2offset=ivec2("
        "int(floor(stable_grid_noise(vec2(patch_anchor)+vec2(3.0,7.0),1.0,227u)*4.0)),"
        "int(floor(stable_grid_noise(vec2(patch_anchor)+vec2(11.0,5.0),1.0,229u)*4.0)));"
        "returnpatch_origin+clamp(offset,ivec2(0),ivec2(3));}"
    )
    for forbidden in (
        "view_identity",
        "VSB_PROJECTION_",
        "camera",
        "screen",
        "feature_params",
        "quantized_time",
    ):
        assert forbidden not in patch_anchor
        assert forbidden not in patch_owner

    # Density remains evaluated at the selected root. Admission is exact root
    # equality. One clump admission keeps the fan together; bloom admission
    # remains a habitat subset. The deleted radius/spacing owner stays absent.
    assert (
        "floatclump_budget=stable_grid_noise("
        "vec2(foliage_dense_card_patch_anchor(anchor_cell))*0.41,1.0,219u);"
        in density
    )
    assert (
        "ivec2patch_anchor=foliage_dense_card_patch_anchor(anchor_cell);"
        "ivec2patch_owner=foliage_dense_card_patch_owner(patch_anchor);"
        "if(any(notEqual(anchor_cell,patch_owner)))returnfalse;"
        in admission
    )
    assert "floatroll=stable_cell_noise(patch_anchor,233u);" in admission
    assert (
        "floatslot_budget=slot==FOLIAGE_CLUMP_SLOT_BLOOM?"
        "bloom_budget:density*(slot==FOLIAGE_CLUMP_SLOT_FILL?0.62:1.0);returnroll<=slot_budget;"
        in admission
    )
    for retired in (
        "FOLIAGE_SCATTER_MIN_SPACING_CELLS",
        "floatspacing_d=",
        "ivec2stable_owner=",
        "constintFOLIAGE_DENSE_QUERY_RADIUS=",
    ):
        assert retired not in compact

    # Placement, exported anchor, and clump identity use the selected root.
    # Binding glyph/species identity to patch_anchor would preserve the visible
    # axis-aligned 4x4 repetition even after root placement is randomized.
    # Wind stays one cached root fact and no projection becomes a placement owner.
    assert "ivec2patch_owner=foliage_dense_card_patch_owner(patch_anchor);" in blade
    assert "uintid=uint(patch_anchor.x)*73856093u" in blade
    assert "^uint(patch_anchor.y)*19349663u" in blade
    assert "vec2clump_center=vec2(patch_owner)+vec2(0.5);" in blade
    assert "b.anchor_cell=patch_owner;" in blade
    assert "vec2cell_xz=sf.world_origin.xz;" in blade
    assert "floatt=anchor_wind.quantized_time;" in blade
    assert "b.height_cells=(4.5+height_band*2.5)*role_height*mix(0.94,1.06,seed5);" in blade
    for forbidden in ("view_identity", "VSB_PROJECTION_", "camera", "screen"):
        assert forbidden not in blade

    # Radius two over patch coordinates is exactly a 5x5 / 25-root scan. The
    # four-cell stride reaches the complete 3.36..6.24-cell authored card span
    # derived from height 7..13 and the existing 0.48 vertical projection.
    assert "constintFOLIAGE_DENSE_QUERY_PATCH_RADIUS=2;" in compact
    assert (
        "for(intpz=-FOLIAGE_DENSE_QUERY_PATCH_RADIUS;"
        "pz<=FOLIAGE_DENSE_QUERY_PATCH_RADIUS;pz++){" in coverage
    )
    assert (
        "for(intpx=-FOLIAGE_DENSE_QUERY_PATCH_RADIUS;"
        "px<=FOLIAGE_DENSE_QUERY_PATCH_RADIUS;px++){" in coverage
    )
    assert "ivec2candidate_patch=target_patch+ivec2(px,pz)*4;" in coverage
    assert coverage.count("ivec2anchor=foliage_dense_card_patch_owner(candidate_patch);") == 1
    assert coverage.count("FoliageSurfaceFactsanchor_facts=foliage_surface_facts(anchor);") == 1
    assert coverage.count(
        "floatanchor_scatter_density=foliage_scatter_density_at("
        "anchor,anchor_facts);"
    ) == 1
    assert coverage.count(
        "foliage_anchor_wind_fact(anchor_facts.world_origin.xz);"
    ) == 1
    assert "floatcard_v_span=max(1.15,b.height_cells*0.48);" in coverage
    assert "c.anchor_id=product_foliage_clump_id(anchor);" in coverage
    assert "c.clump_id=product_foliage_clump_id(anchor);" in coverage
    assert "c.clump_id=product_foliage_clump_id(patch_anchor);" not in coverage

    # Terrain and retained mat-211 moss call this same coverage owner; moss has
    # no private root distribution, spacing, density, or wind implementation.
    assert compact.count("foliage_blade_coverage_at_cell(") == 3
    assert (
        "FoliageCoveragemoss_growth=foliage_blade_coverage_at_cell(moss_cell);"
        in foliage_finish
    )
    assert (
        "q3_probe_payload_finish_foliage(moss_cell,moss_growth,"
        "foliage_root_ramp_band,foliage_glyph,foliage_fg,coverage,shape,role);"
        in foliage_finish
    )


def test_foliage_clump_root_and_patch_query_contract() -> None:
    _assert_clump_root_contract(_product_source())


@pytest.mark.parametrize(
    ("name", "mutator"),
    (
        (
            "patch_size_drift",
            lambda source: _replace_in_function(
                source,
                "foliage_dense_card_patch_anchor",
                "const int FOLIAGE_CLUMP_SIZE = 4;",
                "const int FOLIAGE_CLUMP_SIZE = 5;",
            ),
        ),
        (
            "owner_hash_collapsed",
            lambda source: _replace_in_function(
                source,
                "foliage_dense_card_patch_owner",
                "1.0, 229u) * 4.0)",
                "1.0, 227u) * 4.0)",
            ),
        ),
        (
            "owner_range_shrunk",
            lambda source: _replace_in_function(
                source,
                "foliage_dense_card_patch_owner",
                "clamp(offset, ivec2(0), ivec2(3))",
                "clamp(offset, ivec2(0), ivec2(2))",
            ),
        ),
        (
            "admission_uses_lattice_anchor",
            lambda source: _replace_in_function(
                source,
                "foliage_scatter_accept_anchor",
                "notEqual(anchor_cell, patch_owner)",
                "notEqual(anchor_cell, patch_anchor)",
            ),
        ),
        (
            "placement_uses_lattice_anchor",
            lambda source: _replace_in_function(
                source,
                "foliage_blade_from_anchor",
                "vec2 clump_center = vec2(patch_owner) + vec2(0.5);",
                "vec2 clump_center = vec2(patch_anchor) + vec2(0.5);",
            ),
        ),
        (
            "root_export_reverts_to_input",
            lambda source: _replace_in_function(
                source,
                "foliage_blade_from_anchor",
                "b.anchor_cell    = patch_owner;",
                "b.anchor_cell    = anchor_cell;",
            ),
        ),
        (
            "patch_stride_collapsed",
            lambda source: _replace_in_function(
                source,
                "foliage_blade_coverage_at_cell",
                "target_patch + ivec2(px, pz) * 4;",
                "target_patch + ivec2(px, pz);",
            ),
        ),
        (
            "query_radius_shrunk",
            lambda source: _replace_once(
                source,
                "const int   FOLIAGE_DENSE_QUERY_PATCH_RADIUS = 2;",
                "const int   FOLIAGE_DENSE_QUERY_PATCH_RADIUS = 1;",
            ),
        ),
    ),
)
def test_foliage_clump_root_mutations_compile_and_turn_red(name, mutator) -> None:
    source = _product_source()
    mutant = mutator(source)
    compiler = probe_contract.glslang_bin()
    assert compiler is not None, "glslangValidator/glslang is required"
    with tempfile.TemporaryDirectory(prefix=f"fl4184-{name}-") as tmp:
        stages = probe_contract.split_stages(_expand_product_includes(mutant))
        ok, detail = probe_contract.compile_stage(
            compiler, "compute", stages["compute"], Path(tmp)
        )
    assert ok, f"mutation is not compile-valid: {name}: {detail}"
    with pytest.raises(AssertionError):
        _assert_clump_root_contract(mutant)
