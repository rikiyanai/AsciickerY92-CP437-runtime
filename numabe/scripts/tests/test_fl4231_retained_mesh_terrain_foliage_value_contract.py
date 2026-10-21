#!/usr/bin/env python3
"""FL-4231 retained mesh and terrain-foliage value-owner contract.

This destructive structural contract pins three source transactions:

* retained static meshes do not receive the historical dynamic-mover black
  silhouette treatment;
* the dirt/cliff plane palette remains terrain-role scoped and applies each
  coarse Product group only to its semantic mix contribution, preserving the
  complementary authored source value; and
* meadow foliage and retained-mesh moss consume the same carried seven-band
  terrain-family root fact, with no moss-local foliage shade selector.

It does not inspect pixels or establish operator acceptance.
"""

from __future__ import annotations
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", _without_comments(source))


def _function_source(source: str, name: str) -> str:
    match = re.search(
        rf"\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{",
        source,
        flags=re.S,
    )
    assert match is not None, f"missing function: {name}"
    brace = source.find("{", match.start())
    depth = 0
    for position in range(brace, len(source)):
        if source[position] == "{":
            depth += 1
        elif source[position] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : position + 1]
    raise AssertionError(f"unterminated function: {name}")


def _function_body(source: str, name: str) -> str:
    function = _function_source(source, name)
    return function[function.find("{") + 1 : function.rfind("}")]


def _replace_in_function(
    source: str,
    name: str,
    old: str,
    new: str,
) -> str:
    function = _function_source(source, name)
    assert function.count(old) == 1, (name, old, function.count(old))
    mutant_function = function.replace(old, new, 1)
    assert source.count(function) == 1, name
    return source.replace(function, mutant_function, 1)


def _assert_contract(source: str) -> None:
    whole = _compact(source)
    material = _compact(_function_body(source, "q3_probe_payload_apply_material"))
    morphology = _compact(
        _function_body(source, "product_compile_terrain_morphology")
    )
    compile_root = _compact(
        _function_body(source, "compile_probe_subcell_payload")
    )
    foliage = _compact(_function_body(source, "q3_probe_payload_apply_foliage"))
    compute_foliage = _compact(
        _function_body(source, "compute_product_foliage_fact")
    )
    terrain_ramp = _compact(
        _function_body(source, "foliage_apply_root_terrain_ramp_band")
    )
    finish_foliage = _compact(
        _function_body(source, "q3_probe_payload_finish_foliage")
    )
    static_rock_moss = _compact(
        _function_body(
            source, "q3_probe_payload_apply_static_rock_moss_material"
        )
    )
    grass_guard = _compact(
        _function_body(
            source, "q3_probe_payload_apply_grass_family_palette_guard"
        )
    )
    palette = _compact(
        _function_body(source, "product_apply_terrain_plane_palette")
    )
    grouped_luma = _compact(
        _function_body(source, "product_terrain_plane_group_luma")
    )
    hue_at_group_luma = _compact(
        _function_body(source, "product_terrain_hue_at_group_luma")
    )

    # b3b400433: preserve the exact historical silhouette math for dynamic
    # movers, and admit it only behind the dynamic-mesh owner discriminator.
    assert "product_apply_static_world_authored_dark_silhouette" not in whole
    dynamic_helper = _compact(
        _function_body(
            source, "product_apply_dynamic_mesh_authored_dark_silhouette"
        )
    )
    assert dynamic_helper == (
        "floatsilhouette=1.0-smoothstep(0.035,0.11,authored_shade);"
        "fg=mix(fg,vec3(0.012),silhouette);"
        "bg=mix(bg,vec3(0.0),silhouette*0.94);"
    )
    dynamic_admission = (
        "if(context.surface_kind==OWNER_DYNAMIC_MESH&&!closed_dynamic){"
        "product_apply_dynamic_mesh_authored_dark_silhouette("
        "fg,bg,authored_shade);"
        "}"
    )
    assert dynamic_admission in material
    assert whole.count(
        "product_apply_dynamic_mesh_authored_dark_silhouette("
    ) == 2
    static_shadow = "if(context.surface_kind==OWNER_STATIC_MESH){"
    assert static_shadow in material
    assert material.index(dynamic_admission) < material.index(static_shadow)

    # 98ad1d297: the palette has no screen/view/ray owner and can affect only
    # dirt or cliff terrain after the Product terrain record is resolved.
    assert (
        "if(family!=TERRAIN_FAMILY_DIRT"
        "&&family!=TERRAIN_FAMILY_CLIFF_ROCK)return;"
    ) in palette
    assert (
        "if(family==TERRAIN_FAMILY_DIRT&&!dirt_mountain"
        "&&!hollow&&!side&&!lip)return;"
    ) in palette
    for forbidden in (
        "view_identity",
        "ViewRay",
        "ansi_xy",
        "screen",
        "camera",
        "fract(",
        "stable_cell_noise",
    ):
        assert forbidden not in palette
    # FL-4231/FL-4336/FL-4775 lineage: 3f8614752 added the exact three-mass
    # endpoint, 88f122e82 removed it, and ac6fd141f restored it. The groups remain
    # the coarse terrain grammar, but their exact daylight endpoint must not
    # erase all material/profile and lighting detail. Apply the existing role
    # mix to the grouped contribution without reopening classifier/glyph owners
    # or inventing a second numeric detail span.
    assert "product_terrain_hue_preserve_luma" not in whole
    assert "role_gain" in palette
    assert (
        "floatdaylight=clamp(1.0-product_day_night_factor(),0.0,1.0);"
        in palette
    )
    assert "floatcloud_shadow" in _compact(
        _function_source(source, "product_apply_terrain_plane_palette")
    ).split("{", 1)[0]

    assert "ramp_band<=1u?0.38:(ramp_band<=4u?0.50:0.62)" in grouped_luma
    assert "ramp_band<=1u?0.22:(ramp_band<=4u?0.31:0.40)" in grouped_luma
    assert (
        "target_value=mix(source_luma,grouped_luma,clamp(daylight,0.0,1.0))"
        in hue_at_group_luma
    )
    assert (
        "hue_at_target_value=target_hue*(target_value/target_luma)"
        in hue_at_group_luma
    )
    assert (
        "mix(source,hue_at_target_value,amount)"
        in hue_at_group_luma
    )
    assert "source_at_value" not in hue_at_group_luma
    assert "detail_span" not in hue_at_group_luma
    assert "amount*daylight" not in hue_at_group_luma
    assert palette.count("product_terrain_hue_at_group_luma(") == 2
    assert palette.count("product_terrain_plane_group_luma(") == 2
    assert "product_day_cloud_light_attenuation(cloud_shadow,family)" in palette
    assert "ink_group_luma*=cloud_atten;" in palette
    assert "paper_group_luma*=cloud_atten;" in palette
    resolve = "TerrainMorphologymorphology=product_compile_terrain_morphology("
    palette_call = "product_apply_terrain_plane_palette("
    assert material.count(palette_call) == 1
    assert material.index(resolve) < material.index(palette_call)
    assert (
        "product_apply_terrain_plane_palette(family,"
        "vsb_product_selected_terrain_classification.product_dirt_mountain,"
        "cloud_shadow,"
        "terrain);" in material
    )

    # 88b67bb06: each admitted terrain-family surface publishes one selected
    # root band. Meadow grass and retained-mesh moss carry that fact into the
    # same foliage finish without reopening sun/normal/Bayer ownership there.
    assert (
        "foliage_root_ramp_band=ramp.enabled?ramp.product_band:"
        "uint(clamp(int(floor(float(shade.shd)*(7.0/16.0))),0,6));"
    ) in material
    assert (
            "q3_probe_payload_apply_material(context,glyph,family,fg,bg,"
            "terrain_face_role,terrain_elevation_row,foliage_root_ramp_band,"
            "terrain_cell_glyph_class,"
            "terrain_mountain_glyph_remapped,"
            "receiver_cast_shadow_attenuation);"
    ) in compile_root
    assert (
        "q3_probe_payload_apply_foliage(context,facts,terrain_face_role,"
        "foliage_root_ramp_band,glyph,fg,bg,markers,"
        "foliage_product_present,foliage_product_role)"
    ) in compile_root
    assert (
        "compute_product_foliage_fact(context.material_cell,"
        "context.surface_kind,context.material,facts.source_key,"
        "context.anchor_world,facts.world_depth,foliage_root_ramp_band,"
        "r16,r17,foliage_glyph,foliage_fg);"
    ) in foliage
    assert (
        "foliage_fg=foliage_apply_root_terrain_ramp_band("
        "terrain_root_ramp_band,cov,foliage_fg);"
    ) in compute_foliage
    assert (
        "foliage_fg=foliage_apply_root_terrain_ramp_band("
        "foliage_root_ramp_band,cov,foliage_fg);"
    ) in finish_foliage
    assert "foliage_apply_cel_ramp_stack(" not in whole

    # The retained adapter may shade its one admitted mesh surface from the
    # carried sun, normal, and stable dither. It must export the selected band
    # and never reopen those facts inside the moss foliage finish.
    assert (
        "uintcast_selected_ramp_band=uint(clamp("
        "int(floor(float(shd)*(7.0/16.0))),0,6));"
        "uintselected_surface_ramp_band=ramp.enabled?"
        "min(ramp.product_band,cast_selected_ramp_band):"
        "cast_selected_ramp_band;"
        "foliage_root_ramp_band=selected_surface_ramp_band;"
    ) in static_rock_moss
    assert static_rock_moss.count(
        "vsb_product_selected_effective_sun_dir"
    ) == 1
    assert (
        "q3_probe_payload_apply_static_rock_moss_material("
        "context,context.flags,receiver_cast_shadow_attenuation,glyph,family,"
        "foliage_root_ramp_band,fg,bg)"
    ) in material
    # FL-4359 approved stone-specific facet target supersedes the old rock
    # tan/brown/teal grouping. Terrain and the moss root-band contract stay.
    assert "product_signed_probe_normal(context.payload_normal)" in static_rock_moss
    assert "product_stable_probe_normal(" not in static_rock_moss
    assert (
        "if(context.material==STATIC_WORLD_ROCK_MAT){"
        "surface.fg=product_stone_facet_color(surface.fg,lambert_raw);"
        "surface.bg=product_stone_facet_color(surface.bg,lambert_raw);"
        "}"
    ) in static_rock_moss
    assert "product_apply_terrain_plane_palette(" not in static_rock_moss
    stone = _compact(_function_body(source, "product_stone_facet_color"))
    assert "floatvalue=rec709_luminance(source);" in stone
    assert "hue*(value/rec709_luminance(hue))" in stone
    for forbidden in ("ray", "camera", "view_identity", "depth", "time", "glyph", "texture"):
        assert forbidden not in stone

    # Moss shares grass-family palette treatment with terrain. Flower foreground
    # retains its authored species hue while its underlying grass paper still
    # takes the shared background guard. This is one owner, not a moss- or
    # flower-local color/shade pass.
    assert (
        "boolgrass_family_surface=context.surface_kind==OWNER_TERRAIN||"
        "(context.surface_kind==OWNER_STATIC_MESH&&"
        "context.material==STATIC_WORLD_MOSS_MAT);"
    ) in grass_guard
    assert (
        "boolflower=foliage_product_present&&"
        "foliage_product_role==FOLIAGE_ROLE_FLOWER;"
    ) in grass_guard
    assert (
        "if(!flower){fg=product_grass_family_palette_guard_color("
        in grass_guard
    )
    assert "}bg=product_grass_family_palette_guard_color(" in grass_guard
    assert grass_guard.count("product_grass_family_palette_guard_color(") == 2

    # Neither common root-band shaping nor the moss/terrain finish can reopen
    # lighting, height, or Bayer ownership after the surface selected its band.
    for forbidden in (
        "vsb_product_selected_effective_sun_dir",
        "cell_avg_height(",
        "foliage_bayer4(",
        "normalize(",
    ):
        assert forbidden not in terrain_ramp
        assert forbidden not in finish_foliage

    # The one shared morphology owner applies edge and mountain glyph rules
    # before quarter foliage may replace the base glyph. Quarter finalization
    # retains dither/contrast but cannot reopen those discrete writers.
    dither_call = "q3_probe_payload_apply_surface_stable_dither("
    edge_call = "q3_probe_payload_apply_world_cell_edge("
    mountain_call = "product_mountain_payload_glyph("
    assert morphology.index(edge_call) < morphology.index(mountain_call)
    assert edge_call not in compile_root
    assert mountain_call not in compile_root
    assert compile_root.index("q3_probe_payload_apply_foliage(") < (
        compile_root.index(dither_call)
    )
    assert "!foliage_product_present" in compile_root


def test_retained_mesh_terrain_palette_and_foliage_value_owners() -> None:
    _assert_contract(_expand_includes(PRODUCT.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    ("function", "old", "new"),
    (
        (
            "q3_probe_payload_apply_material",
            "if (context.surface_kind == OWNER_DYNAMIC_MESH && !closed_dynamic) {\n"
            "\t\tproduct_apply_dynamic_mesh_authored_dark_silhouette(",
            "if (context.surface_kind == OWNER_STATIC_MESH) {\n"
            "\t\tproduct_apply_dynamic_mesh_authored_dark_silhouette(",
        ),
        (
            "q3_probe_payload_apply_material",
            "foliage_root_ramp_band = ramp.enabled\n\t\t\t? ramp.product_band",
            "foliage_root_ramp_band = ramp.enabled\n\t\t\t? 6u",
        ),
        (
            "q3_probe_payload_finish_foliage",
            "foliage_fg = foliage_apply_root_terrain_ramp_band(\n"
            "\t\tfoliage_root_ramp_band, cov, foliage_fg);",
            "foliage_fg = foliage_apply_cel_ramp_stack(\n"
            "\t\tcell, cov, foliage_fg);",
        ),
        (
            "product_apply_terrain_plane_palette",
            "family != TERRAIN_FAMILY_DIRT\n"
            "\t\t\t&& family != TERRAIN_FAMILY_CLIFF_ROCK",
            "family != TERRAIN_FAMILY_GRASS\n"
            "\t\t\t&& family != TERRAIN_FAMILY_CLIFF_ROCK",
        ),
        (
            "q3_probe_payload_apply_static_rock_moss_material",
            "foliage_root_ramp_band = selected_surface_ramp_band;",
            "foliage_root_ramp_band = 6u",
        ),
        (
            "q3_probe_payload_apply_grass_family_palette_guard",
            "context.material == STATIC_WORLD_MOSS_MAT",
            "context.material == STATIC_WORLD_ROCK_MAT",
        ),
        (
            "product_terrain_plane_group_luma",
            "ramp_band <= 1u ? 0.38 : (ramp_band <= 4u ? 0.50 : 0.62)",
            "ramp_band <= 1u ? 0.50 : (ramp_band <= 4u ? 0.50 : 0.50)",
        ),
        (
            "product_terrain_hue_at_group_luma",
            "mix(source, hue_at_target_value, amount)",
            "mix(hue_at_target_value, hue_at_target_value, amount)",
        ),
        (
            "product_apply_terrain_plane_palette",
            "1.0 - product_day_night_factor()",
            "1.0",
        ),
        (
            "product_apply_terrain_plane_palette",
            "ink_group_luma *= cloud_atten;",
            "ink_group_luma *= 1.0;",
        ),
    ),
)
def test_contract_mutations_turn_red(
    function: str,
    old: str,
    new: str,
) -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    mutant = _replace_in_function(source, function, old, new)
    with pytest.raises(AssertionError):
        _assert_contract(mutant)


def test_grouped_target_hue_normalization_owns_the_requested_target_value() -> None:
    source = (0.31, 0.42, 0.17)
    target_hue = (0.36, 0.24, 0.11)
    weights = (0.2126, 0.7152, 0.0722)
    source_luma = sum(component * weight for component, weight in zip(source, weights))
    target_luma = sum(
        component * weight for component, weight in zip(target_hue, weights)
    )
    grouped_luma = 0.50
    hue_at_value = tuple(
        component * grouped_luma / target_luma for component in target_hue
    )
    normalized_luma = sum(
        component * weight for component, weight in zip(hue_at_value, weights)
    )
    assert normalized_luma == pytest.approx(grouped_luma)


def _assert_stone_contract(source: str) -> None:
    owner = _compact(_function_body(source, "q3_probe_payload_apply_static_rock_moss_material"))
    assert "product_signed_probe_normal(context.payload_normal)" in owner
    assert "product_stable_probe_normal(" not in owner
    assert "product_apply_terrain_plane_palette(" not in owner
    assert "product_stone_facet_color(" not in owner
    assert "product_apply_dynamic_mesh_authored_dark_silhouette(" not in owner
    stone = owner.split("if(context.material==STATIC_WORLD_ROCK_MAT){", 1)[1].split("returntrue;", 1)[0]
    assert "product_lighting_value(nrm,lambert_raw)" in stone
    for forbidden in ("product_terrain_glyph_for_product", "product_quantise_shd", "authored_shade"):
        assert forbidden not in stone
    finalizer = _compact(_function_body(source, "vsb_product_finalize_record"))
    assert "product_stone_crease_pair(input_value.retained_face,input_value.context.trace_receiver," in finalizer


def test_stone_owner_and_downward_normal_mutation() -> None:
    source = _expand_includes(PRODUCT.read_text())
    _assert_stone_contract(source)
    mutant = _replace_in_function(source, "q3_probe_payload_apply_static_rock_moss_material",
        "product_signed_probe_normal(context.payload_normal)",
        "product_stable_probe_normal(context.payload_normal)")
    with pytest.raises(AssertionError):
        _assert_stone_contract(mutant)


def test_grouped_palette_mix_retains_authored_source_value_order() -> None:
    """Day retains source order and night retains the prior value endpoint."""

    grouped_luma = 0.50
    palette_amount = 0.34

    def daylight_value(source_luma: float) -> float:
        return source_luma * (1.0 - palette_amount) + grouped_luma * palette_amount

    values = [daylight_value(value) for value in (0.32, 0.44, 0.56, 0.68)]
    assert values == pytest.approx((0.3812, 0.4604, 0.5396, 0.6188))
    assert values == sorted(values)
    assert len(set(values)) == 4
    assert daylight_value(0.68) - daylight_value(0.32) == pytest.approx(0.2376)

    # At night the target hue is normalized to source_luma. Rec.709 luma is
    # linear, so mixing source and target at the same value keeps source_luma
    # exactly for every established semantic mix amount.
    for source_luma in (0.18, 0.42, 0.73):
        night_target_luma = source_luma
        night_value = (
            source_luma * (1.0 - palette_amount)
            + night_target_luma * palette_amount
        )
        assert night_value == pytest.approx(source_luma)
