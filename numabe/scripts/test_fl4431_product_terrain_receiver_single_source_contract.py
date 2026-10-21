#!/usr/bin/env python3
"""FL-4431 Product terrain-receiver exact-cardinal single-source contract.

One admitted terrain subcell materializes its source normal, centre and four
cardinal cell-average heights, material-boundary fact, and cell-centre world
point exactly once. Every live terrain consumer receives those immutable facts.
Water keeps its separate authored-surface materializer.

This is destructive structural evidence. It does not measure GPU time, inspect
the rendered scene, establish visual correctness, or claim the 60 FPS target.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
import tempfile
from pathlib import Path

import test_fl4057_probe_pipeline_contract as probe_contract


ROOT = Path(__file__).resolve().parent.parent
PRODUCT_PATH = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"


def without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def compact(source: str) -> str:
    return re.sub(r"\s+", "", without_comments(source))


def function_span(source: str, name: str) -> tuple[int, int]:
    match = re.search(
        rf"\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{",
        source,
        re.S,
    )
    assert match is not None, f"missing function definition: {name}"
    brace = source.find("{", match.start())
    depth = 0
    for position in range(brace, len(source)):
        if source[position] == "{":
            depth += 1
        elif source[position] == "}":
            depth -= 1
            if depth == 0:
                return match.start(), position + 1
    raise AssertionError(f"unterminated function: {name}")


def function_body(source: str, name: str) -> str:
    start, end = function_span(source, name)
    function = source[start:end]
    return function[function.find("{") + 1 : function.rfind("}")]


def replace_in_function(
    source: str,
    name: str,
    old: str,
    new: str,
) -> str:
    start, end = function_span(source, name)
    function = source[start:end]
    count = function.count(old)
    assert count == 1, (
        f"{name}: mutation target count={count} for {old!r}"
    )
    return source[:start] + function.replace(old, new, 1) + source[end:]


def assert_authoritative_terrain_admission(source: str) -> None:
    """Pin b64 terrain geometry as the sole Product preparation authority."""
    whole = compact(source)
    prepare = compact(function_body(source, "vsb_product_prepare_terrain"))
    compile_input = compact(function_body(source, "vsb_product_compile_input"))
    main = compact(function_body(source, "main"))

    # The resolve-era re-key closure reconstructed a second terrain hit from
    # ray depth and twenty height fetches after VSB had already selected the
    # exact triangle. Every helper, fact field, call edge, and diagnostic name
    # from that owner must remain physically absent.
    for retired in (
        "q3_probe_payload_apply_rekey",
        "q3_probe_payload_rekey_",
        "vsb_product_terrain_height",
        "q3_probe_payload_material_cell",
        "q3_probe_payload_stable_key_world",
        "rekey_",
        "Q3_REKEY",
    ):
        assert retired not in whole, f"retired terrain re-key owner: {retired}"

    facts_struct = re.search(
        r"\bstruct\s+ProbePayloadCompileFacts\s*\{(.*?)\}\s*;",
        without_comments(source),
        re.S,
    )
    assert facts_struct is not None
    assert compact(facts_struct.group(1)) == (
        "boolvalid;boolterrain_face_role;"
        "boolanalytic_water;floatanalytic_water_footprint;"
        "floatwater_horizon_mix;"
        "floatwater_depth;floatworld_depth;vec3frozen_ray_origin;"
        "uintsource_key;uintpreparation_diag_reason;"
        "uintpreparation_diag_detail;uintpayload_diag_markers;"
    )

    # Primitive identity owns the authored cell. World position is never
    # allowed to select a different cell after admission.
    assert (
        "uintlocal_primitive="
        "record.primitive_id-product_vsb_frame.heightfield_state.z;"
        in prepare
    )
    assert (
        "uvec2unsigned_cell="
        "uvec2(local_primitive%dims.x,local_primitive/dims.x);"
        "if(unsigned_cell.x>0xffffu||unsigned_cell.y>0xffffu)"
        in prepare
    )
    assert "ivec2cell=ivec2(unsigned_cell);" in prepare
    assert "floor(record.world_position.xz" not in prepare
    assert "floor(record.world_position.x" not in prepare

    # The stable anchor check plus receiver/classification materializers must
    # dominate publication of the direct b64 world point.
    anchor_gate = (
        "if(!all(equal(floatBitsToUint(expected_anchor),"
        "floatBitsToUint(record.stable_world_anchor))))"
    )
    receiver_call = (
        "vsb_product_selected_terrain_receiver="
        "q3_probe_payload_terrain_receiver_facts(cell,record.material);"
    )
    classification_call = (
        "vsb_product_selected_terrain_classification="
        "product_terrain_classification_facts("
        "cell,product_terrain_family(int(record.material)),"
        "input_value.selected_corner_heights);"
    )
    for required in (anchor_gate, receiver_call, classification_call):
        assert required in prepare
    assert prepare.index(anchor_gate) < prepare.index(receiver_call)
    assert prepare.index(receiver_call) < prepare.index(classification_call)
    assert "input_value.context.anchor_world=" not in prepare

    # The VSB-authored stable anchor owns discrete terrain morphology. The
    # quarter's selected receiver remains a separate continuous-paint input.
    assert "record.world_position" not in prepare
    assert "input_value.ray.near_world" not in prepare
    assert "input_value.ray.dir" not in prepare
    assert "record.depth" not in prepare
    assert prepare.count("texelFetch(terrain_corner_height_tex") == 0

    # The shallow input adapter may fetch the four selected corners once for
    # anchor/water validation. Terrain preparation itself may not replay any
    # corner sampler. Analytic horizon water likewise has no re-key call edge.
    assert compile_input.count("texelFetch(terrain_corner_height_tex") == 4
    assert "input_value.context.anchor_world=record.stable_world_anchor.xyz;" in compile_input
    assert "distant_water_sky_paint" not in main
    assert "apply_rekey" not in main


def assert_terrain_receiver_single_source(source: str) -> None:
    assert_authoritative_terrain_admission(source)
    whole = compact(source)
    materializer = compact(
        function_body(source, "q3_probe_payload_terrain_receiver_facts")
    )
    prepare = compact(
        function_body(source, "vsb_product_prepare_terrain")
    )
    shade = compact(
        function_body(source, "q3_probe_payload_compile_terrain_shade")
    )
    stable_shade = compact(
        function_body(source, "q3_probe_payload_compile_stable_terrain_shade")
    )
    material = compact(
        function_body(source, "q3_probe_payload_apply_material")
    )
    grammar = compact(
        function_body(source, "product_compile_terrain_product_morphology")
    )
    ramp = compact(
        function_body(source, "product_terrain_ramp_facts")
    )
    ramp_surface = compact(
        function_body(source, "product_terrain_ramp_facts_for_surface")
    )
    slope_contrast = compact(
        function_body(source, "product_apply_material_slope_value_contrast")
    )
    ramp_steps = compact(
        function_body(source, "product_apply_material_ramp_steps")
    )
    ridge = compact(function_body(source, "product_terrain_ridge_mask"))
    contact = compact(function_body(source, "product_terrain_lower_contact"))
    classification = compact(
        function_body(source, "product_terrain_classification_facts")
    )
    resolver = compact(
        function_body(source, "product_compile_terrain_morphology")
    )
    mountain_glyph = compact(
        function_body(source, "product_mountain_payload_glyph")
    )
    edge_fact = compact(
        function_body(source, "q3_probe_payload_clone_cell_edge_fact")
    )
    world_edge = compact(
        function_body(source, "q3_probe_payload_apply_world_cell_edge")
    )
    foliage = compact(
        function_body(source, "q3_probe_payload_apply_foliage")
    )
    foliage_ramp = compact(
        function_body(source, "foliage_apply_root_terrain_ramp_band")
    )
    shadow = compact(
        function_body(source, "q3_probe_payload_apply_shadow_and_lit")
    )
    prequant_receiver = compact(
        function_body(source, "q3_probe_payload_cast_is_prequant_receiver")
    )
    water_receiver = compact(
        function_body(source, "q3_probe_payload_cell_shadow_receiver")
    )
    payload_root = compact(
        function_body(source, "compile_probe_subcell_payload")
    )
    main = compact(function_body(source, "main"))

    # The value is a private Product invocation fact. It is not a binding and
    # it does not create a second stable compiler-context ABI.
    receiver_struct = re.search(
        r"\bstruct\s+ProductTerrainReceiverFacts\s*\{(.*?)\}\s*;",
        without_comments(source),
        re.S,
    )
    assert receiver_struct is not None
    assert compact(receiver_struct.group(1)) == (
        "floatcenter_height;floateast_height;floatsouth_height;"
        "floatwest_height;floatnorth_height;"
        "inteast_material;intsouth_material;"
        "intwest_material;intnorth_material;"
        "uintmaterial_boundary;"
        "vec3world;vec3normal;"
    )
    assert (
        "ProductTerrainReceiverFactsvsb_product_selected_terrain_receiver;"
        in whole
    )
    assert (
        "vsb_product_selected_terrain_receiver="
        "q3_probe_payload_terrain_receiver_facts("
        "cell,record.material);" in prepare
    )

    # One live call owns all five heights, four cardinal materials, the source
    # normal, and the derived boundary. Cardinal order is part of the contract.
    assert whole.count("q3_probe_payload_terrain_receiver_facts(") == 2
    assert materializer.count("cell_avg_height(") == 5
    assert materializer.count("product_source_cell_normal(receiver_cell)") == 1
    assert materializer.count("terrain_cell_mat(") == 4
    assert "terrain_cell_material_boundary(" not in whole
    assert (
        "precisefloatcenter_height=cell_avg_height(receiver_cell);"
        "precisevec3normal=product_source_cell_normal(receiver_cell);"
        "floateast_height=cell_avg_height("
        "receiver_cell+ivec2(1,0));"
        "floatsouth_height=cell_avg_height("
        "receiver_cell+ivec2(0,1));"
        "floatwest_height=cell_avg_height("
        "receiver_cell+ivec2(-1,0));"
        "floatnorth_height=cell_avg_height("
        "receiver_cell+ivec2(0,-1));"
        "ivec2dims=ivec2(max(params.misc.y,1),max(params.misc.z,1));"
        "inteast_material=int(current_material);"
        "intsouth_material=int(current_material);"
        "intwest_material=int(current_material);"
        "intnorth_material=int(current_material);"
        "if(receiver_cell.x+1<dims.x){"
        "east_material=int(terrain_cell_mat(receiver_cell+ivec2(1,0)));}"
        "if(receiver_cell.y+1<dims.y){"
        "south_material=int(terrain_cell_mat(receiver_cell+ivec2(0,1)));}"
        "if(receiver_cell.x>0){"
        "west_material=int(terrain_cell_mat(receiver_cell+ivec2(-1,0)));}"
        "if(receiver_cell.y>0){"
        "north_material=int(terrain_cell_mat(receiver_cell+ivec2(0,-1)));}"
        "uintmaterial_boundary="
        "east_material!=int(current_material)||"
        "south_material!=int(current_material)||"
        "west_material!=int(current_material)||"
        "north_material!=int(current_material)?1u:0u;"
        "precisevec2xz=params.terrain_params.xy"
        "+vec2(receiver_cell)+vec2(0.5);"
        "facts.center_height=center_height;"
        "facts.east_height=east_height;"
        "facts.south_height=south_height;"
        "facts.west_height=west_height;"
        "facts.north_height=north_height;"
        "facts.east_material=east_material;"
        "facts.south_material=south_material;"
        "facts.west_material=west_material;"
        "facts.north_material=north_material;"
        "facts.material_boundary=material_boundary;"
        "facts.world=vec3(xz.x,center_height,xz.y);"
        "facts.normal=normal;" in materializer
    )

    # The stable cell owns discrete shade and glyph morphology. The continuous
    # quarter receiver owns the Lambert carrier consumed by FG/BG paint. These
    # are intentionally separate domains: a moving quarter may alter paint but
    # must not re-elect the base terrain glyph.
    shade_struct = re.search(
        r"\bstruct\s+Q3ProbeTerrainShade\s*\{(.*?)\}\s*;",
        without_comments(source),
        re.S,
    )
    assert shade_struct is not None
    assert compact(shade_struct.group(1)) == (
        "intelv;intshd;floatshd_continuous;floatlambert_raw;"
    )
    assert (
        "ProductTerrainDetailFactsstable_detail="
        "product_terrain_detail_facts_from_corners("
        "cell,stable_anchor_world,corner_heights);" in stable_shade
    )
    assert (
        "precisefloatstable_lambert_raw=max(dot("
        "stable_detail.normal,vsb_product_selected_effective_sun_dir),0.0);"
        in stable_shade
    )
    assert "shade.shd=product_quantise_shd(stable_lit,dither_t);" in stable_shade
    assert stable_shade.count("max(dot(") == 1
    assert (
        "q3_probe_payload_compile_stable_terrain_shade("
        "cell,stable_anchor_world,corner_heights,flags,"
        "stable_cast_shadow_attenuation);" in shade
    )
    assert (
        "ProductTerrainDetailFactscontinuous_detail="
        "product_terrain_detail_facts_from_corners("
        "cell,continuous_sample_world,corner_heights);" in shade
    )
    assert (
        "precisefloatcontinuous_lambert_raw=max(dot("
        "continuous_detail.normal,"
        "vsb_product_selected_effective_sun_dir),0.0);"
        "shade.lambert_raw=continuous_lambert_raw;" in shade
    )
    assert shade.count("max(dot(") == 1
    assert "product_source_cell_normal(" not in shade
    assert (
        "q3_probe_payload_compile_terrain_shade("
        "context.material_cell,context.anchor_world,continuous_cast_receiver,"
        "context.terrain_corner_heights,context.flags,"
        "receiver_cast_shadow_attenuation,continuous_cast_shadow_attenuation)"
        in material
    )
    assert (
        "receiver_cast_shadow_attenuation="
        "q3_probe_payload_cast_shadow_attenuation("
        "context.anchor_world,stable_cast_detail.normal,"
        "OWNER_TERRAIN,-1,context.flags);" in material
    )
    assert (
        "continuous_cast_shadow_attenuation="
        "q3_probe_payload_cast_shadow_attenuation("
        "continuous_cast_receiver,continuous_cast_detail.normal,"
        "OWNER_TERRAIN,-1,context.flags);" in material
    )
    assert (
        "stable_lit_unshadowed*stable_cast_shadow_attenuation"
        in stable_shade
    )
    assert (
        "continuous_lit_unshadowed*continuous_cast_shadow_attenuation"
        in shade
    )
    assert (
        "precisevec3nrm="
        "vsb_product_selected_terrain_receiver.normal;" in material
    )
    assert (
        "product_apply_ambient_base("
        "matlib_fg,matlib_bg,nrm,shade.lambert_raw,cloud_shadow,family);"
        in material
    )
    # The mesh branch keeps its own face Lambert. Exactly one selected-sun dot
    # therefore remains in this whole helper; a second one is the deleted
    # terrain ambient owner returning.
    assert material.count(
        "max(dot(nrm,vsb_product_selected_effective_sun_dir),0.0)"
    ) == 1
    assert "shade.shd_continuous,cloud_shadow,family" not in material
    assert (
        "product_terrain_ramp_facts_for_surface("
        "cell,family,visual_elv,product_elv,"
        "vsb_product_selected_terrain_receiver.normal,"
        "vsb_product_selected_terrain_receiver.center_height,"
        "vsb_product_selected_terrain_receiver.east_height,"
        "vsb_product_selected_terrain_receiver.south_height)" in ramp
    )
    assert "shade.lambert_raw" not in ramp
    assert (
        "floatlambert=max(dot("
        "nrm,vsb_product_selected_effective_sun_dir),0.0);"
        in ramp_surface
    )
    assert "cell_avg_height(" not in slope_contrast
    assert "cell_avg_height(" not in ramp_steps
    assert "ramp.visual_raw" in ramp_steps

    # Foliage consumes the already selected terrain-family root band. The
    # deleted cel helper must stay absent: reopening any sun selector or normal
    # work here would create a second per-blade shade owner.
    assert "foliage_apply_cel_ramp_stack(" not in whole
    assert (
        "floatramp_band=clamp(float(root_ramp_band)/6.0,0.0,1.0);"
        in foliage_ramp
    )
    for forbidden_light_owner in (
        "vsb_product_selected_effective_sun_dir",
        "normalize(effective_sun_dir())",
        "normalize(vsb_product_selected_effective_sun_dir)",
        "vec3light_dir=SPLAT_SUN_DIR;",
        "vec3light_dir=product_moon_dir();",
    ):
        assert forbidden_light_owner not in foliage_ramp

    # The old face resolver performed a second matlib/lighting resolution.
    # Both its prototype and definition must stay physically absent.
    assert "matlib_face_resolve" not in whole

    # Ridge/contact/grammar laws consume the exact carried cardinal identities.
    # These checks bind direction, not merely field presence.
    assert (
        "floath=vsb_product_selected_terrain_receiver.center_height;"
        "floathn=vsb_product_selected_terrain_receiver.north_height;"
        "floaths=vsb_product_selected_terrain_receiver.south_height;"
        "floathe=vsb_product_selected_terrain_receiver.east_height;"
        "floathw=vsb_product_selected_terrain_receiver.west_height;"
        in ridge
    )
    for field in ("north", "east", "south", "west"):
        assert (
            f"vsb_product_selected_terrain_receiver.{field}_height-h>1.15"
            in contact
        )
    assert (
        "floathx=vsb_product_selected_terrain_receiver.east_height;"
        "floathz=vsb_product_selected_terrain_receiver.south_height;"
        "floathxn=vsb_product_selected_terrain_receiver.west_height;"
        "floathzn=vsb_product_selected_terrain_receiver.north_height;"
        in grammar
    )

    # The shallow adapter owns the four centre-corner fetches. Classification
    # receives those exact scalars in 00/10/01/11 order, then fetches only the
    # four extra dirt corners after the family gate. Non-dirt families therefore
    # add zero height-texture reads while preserving the same pure evaluator.
    classification_struct = re.search(
        r"\bstruct\s+ProductTerrainClassificationFacts\s*\{(.*?)\}\s*;",
        without_comments(source),
        re.S,
    )
    assert classification_struct is not None
    assert compact(classification_struct.group(1)) == (
        "intelevation_center;boolproduct_dirt_mountain;"
        "booledge_dirt_mountain;"
    )
    assert "terrain_cell_elevation_class(" not in whole
    assert (
        "ProductTerrainClassificationFacts"
        "product_terrain_classification_facts("
        "ivec2cell,intfamily,vec4selected_corner_heights)"
        in whole
    )
    assert "precisefloath00=" not in classification
    selected_corners = (
        "floath00=selected_corner_heights.x;"
        "floath10=selected_corner_heights.y;"
        "floath01=selected_corner_heights.z;"
        "floath11=selected_corner_heights.w;"
    )
    assert selected_corners in classification
    assert classification.count("corner_height(") == 4
    assert classification.count("terrain_elevation_class_from_corners(") == 3
    dirt_gate = "if(family!=TERRAIN_FAMILY_DIRT)returnfacts;"
    east_class = (
        "uintelevation_east="
        "terrain_elevation_class_from_corners(h10,h20,h11,h21);"
    )
    south_class = (
        "uintelevation_south="
        "terrain_elevation_class_from_corners(h01,h11,h02,h12);"
    )
    center_class = (
        "uintelevation_center="
        "terrain_elevation_class_from_corners(h00,h10,h01,h11);"
    )
    assert center_class in classification
    assert dirt_gate in classification
    assert east_class in classification
    assert south_class in classification
    assert classification.index(selected_corners) < classification.index(center_class)
    assert classification.index(center_class) < classification.index(dirt_gate)
    assert classification.index(dirt_gate) < classification.index("floath20=")
    assert "corner_height(" not in classification[:classification.index(dirt_gate)]
    assert classification.index(dirt_gate) < classification.index(east_class)
    assert classification.index(east_class) < classification.index(south_class)
    assert (
        "floatproduct_step_amount=max("
        "max(abs(hx-h),abs(hz-h)),"
        "max(abs(hxn-h),abs(hzn-h)));"
        "facts.product_dirt_mountain="
        "h>=6.0||product_step_amount>=1.15||elevation_sum>=2u;"
        in classification
    )
    assert (
        "precisefloatedge_step_amount=max("
        "max(abs(edge_hx-edge_h),abs(edge_hz-edge_h)),"
        "max(abs(edge_hxn-edge_h),abs(edge_hzn-edge_h)));"
        "facts.edge_dirt_mountain="
        "edge_h>=6.0||edge_step_amount>=1.15||elevation_sum>=2u;"
        in classification
    )
    assert (
        "vsb_product_selected_terrain_classification="
        "product_terrain_classification_facts("
        "cell,product_terrain_family(int(record.material)),"
        "input_value.selected_corner_heights);"
        in prepare
    )
    assert "intelv=elevation_center;" in grammar
    assert "morphology.elevation_row=elevation_row;" in resolver
    assert "terrain_elevation_class_from_corners(" not in resolver
    assert "product_terrain_classification_facts(" not in resolver
    assert "if(dirt_mountain){" in grammar
    assert (
        "booldirt_mountain="
        "vsb_product_selected_terrain_classification.product_dirt_mountain;"
        in resolver
    )
    assert "terrain_elevation_class_from_corners(" not in mountain_glyph
    assert "product_terrain_classification_facts(" not in mountain_glyph
    for retired in (
        "product_dirt_mountain_role(",
        "q3_probe_payload_dirt_mountain_role(",
        "product_terrain_slope_class(",
        "product_terrain_glyph_role(",
    ):
        assert retired not in whole
    assert (
        "uintproduct_pack_terrain_detail_fact("
        "ivec2cell,intfamily,uintboundary,uintglyph,"
        "uintweather_flags,floatshadow_density,floatdepth_key)"
        not in whole
    )

    # Ramp paint and packed Product metadata share one typed author while
    # preserving their distinct historical reductions.
    ramp_struct = re.search(
        r"\bstruct\s+ProductTerrainRampFacts\s*\{(.*?)\}\s*;",
        without_comments(source),
        re.S,
    )
    assert ramp_struct is not None
    assert compact(ramp_struct.group(1)) == (
        "boolenabled;floatvisual_raw;floatstep_amount;"
        "floatslope;uintproduct_band;"
    )
    assert whole.count("product_terrain_ramp_facts(") == 2
    assert "product_terrain_ramp_band_fact(" not in whole
    assert ramp_surface.count(
        "product_smooth_terrain_noise(cell,6.0,410211u)"
    ) == 1
    assert ramp_surface.count(
        "product_smooth_terrain_noise(cell,3.0,410223u)"
    ) == 1
    assert ramp_surface.count("vsb_product_selected_effective_sun_dir") == 1
    assert (
        "floatlambert=max(dot("
        "nrm,vsb_product_selected_effective_sun_dir),0.0);" in ramp_surface
    )
    assert "normalize(effective_sun_dir())" not in ramp_surface
    assert "normalize(vsb_product_selected_effective_sun_dir)" not in ramp_surface
    assert "SPLAT_SUN_DIR" not in ramp_surface
    assert "product_moon_dir(" not in ramp_surface
    assert (
        "facts.enabled=false;facts.visual_raw=0.0;"
        "facts.step_amount=0.0;facts.slope=0.0;"
        "facts.product_band=0u;" in ramp_surface
    )
    assert (
        "if((params.misc.w&2048)==0)returnfacts;" in ramp_surface
    )
    assert (
        "floatpatch_w=family==TERRAIN_FAMILY_GRASS?0.0:0.08;"
        "floatcell_w=family==TERRAIN_FAMILY_GRASS?0.0:0.04;"
        in ramp_surface
    )
    visual_raw = (
        "floatvisual_raw=lambert*0.70+nrm.y*0.22"
        "+slope*0.34+patch_lane*patch_w"
        "+cell_lane*cell_w;"
    )
    product_raw = (
        "floatproduct_raw=lambert*0.70+nrm.y*0.22"
        "+slope*0.34+patch_lane*0.08"
        "+cell_lane*0.04;"
    )
    assert visual_raw in ramp_surface
    assert product_raw in ramp_surface
    assert "floor(h*0.75)" not in ramp_surface
    segment_id = compact(function_body(source, "product_terrain_segment_id"))
    assert "continuity==1u?cell.y" in segment_id
    assert "continuity==2u?cell.x" in segment_id
    segment_phase = compact(
        function_body(source, "product_terrain_segment_phase")
    )
    assert "p.ramp_band" not in segment_phase
    assert "p.depth_class" not in segment_phase
    morphology = compact(
        function_body(source, "product_compile_terrain_product_morphology")
    )
    assert "floor(terrain_center_height/2.0)" not in morphology
    assert (
        "p.depth_class=uint(floor(clamp(step_amount/2.8,0.0,0.999)*8.0));"
        in morphology
    )
    assert ramp_surface.count("visual_elv") == 2
    assert ramp_surface.count("product_elv") == 2
    assert "if(visual_elv==1)visual_raw+=0.14;" in ramp_surface
    assert "if(visual_elv==3)visual_raw-=0.12;" in ramp_surface
    assert "if(product_elv==1)product_raw+=0.14;" in ramp_surface
    assert "if(product_elv==3)product_raw-=0.12;" in ramp_surface
    assert (
        "facts.product_band="
        "uint(floor(clamp(product_raw,0.0,0.999)*7.0));"
        in ramp_surface
    )
    ramp_call = (
        "ProductTerrainRampFactsramp=product_terrain_ramp_facts("
        "context.material_cell,family,elevation_row,"
        "vsb_product_selected_terrain_classification.elevation_center);"
    )
    assert ramp_call in resolver
    assert "ProductTerrainRampFactsramp=morphology.ramp;" in material
    assert (
        "product_apply_material_ramp_steps("
        "ramp,family,boundary!=0u,matlib_fg,matlib_bg);"
        in material
    )
    assert resolver.index(ramp_call) < resolver.index(
        "product_compile_terrain_product_morphology("
    )
    assert material.index("product_compile_terrain_morphology(") < (
        material.index("ProductTerrainRampFactsramp=morphology.ramp;")
    )
    assert material.index("ProductTerrainRampFactsramp=morphology.ramp;") < (
        material.index("product_apply_material_ramp_steps(")
    )
    assert "p.ramp_band=terrain_ramp_band;" in grammar
    assert "morphology.ramp=ramp;" in resolver
    # FL-4381 R3a (2026-08-13): the terrain product is handed the UNDROPPED
    # paint band. QD4 (2026-08-13 Codex round 4): the cloud already reaches
    # terrain once before this point, but the owner is FAMILY-DEPENDENT -- this
    # comment previously named the ambient multiply universally, which is false
    # for dirt/cliff. GRASS/UNKNOWN (and flat non-mountain dirt) keep the ambient
    # multiply because product_apply_terrain_plane_palette early-returns; for
    # DIRT/CLIFF_ROCK the palette's full-daylight luma renormalisation DISCARDS
    # the ambient multiply and the palette's own cloud_atten is the live
    # consumer. Passing the cloud-dropped band here made the ground consume the
    # same cloud a second time through ramp_lift and the grouped plane luma.
    # Only the foliage copy keeps the band step.
    assert (
        "ProductTerrainProductterrain="
        "product_compile_terrain_product_morphology("
        "matlib_glyph,family,boundary,context.material_cell,"
        "product_terrain_semantic_weather_flags(weather_flags),"
        "vsb_product_selected_terrain_receiver.normal,"
        "vsb_product_selected_terrain_receiver.center_height,"
        "vsb_product_selected_terrain_classification.elevation_center,"
        "dirt_mountain,terrain_ramp_band);" in resolver
    )
    assert "uintterrain_paint_ramp_band=foliage_root_ramp_band;" in material

    # The pure elevation evaluator retains the exact average, maximum, minimum,
    # span, and threshold law without any texture owner.
    elevation = compact(
        function_body(source, "terrain_elevation_class_from_corners")
    )
    assert "corner_height(" not in elevation
    assert "precise" not in elevation
    assert "floatavg_y=0.25*(h00+h10+h01+h11);" in elevation
    assert "floatmax_y=max(max(h00,h10),max(h01,h11));" in elevation
    assert "floatmin_y=min(min(h00,h10),min(h01,h11));" in elevation
    assert "floatspan_y=max_y-min_y;" in elevation
    assert "if(span_y>=6.0)return3u;" in elevation
    assert "if(span_y>=2.5)return2u;" in elevation
    assert "if(avg_y>=WATER_SURFACE_Y+5.0)return1u;" in elevation
    assert "return0u;" in elevation

    # The same carried world point owns the perspective heightfield marcher.
    # Material and finalization consume one carried boundary classification.
    assert (
        "precisevec3shadow_march_receiver="
        "vsb_product_selected_terrain_receiver.world;" in material
    )
    assert "cell_avg_height(context.material_cell)" not in material
    assert "ProductTerrainProductterrain=morphology.product;" in material
    assert (
        "product_terrain_apply_product_tone("
        "terrain,matlib_fg,matlib_bg,heightfield_shadow_attenuation);"
        in material
    )
    assert (
        "uintweather_flags,vec3terr_nrm,"
        "floatterrain_center_height,intelevation_center,"
        "booldirt_mountain,uintterrain_ramp_band)" in compact(
            source[function_span(
                source, "product_compile_terrain_product_morphology"
            )[0] : function_span(
                source, "product_compile_terrain_product_morphology"
            )[1]]
        )
    )
    assert (
        "p.depth_class=uint(floor(clamp("
        "step_amount/2.8,0.0,0.999)*8.0));" in grammar
    )
    assert "floath=terrain_center_height;" in grammar
    assert (
        "uintboundary="
        "vsb_product_selected_terrain_receiver.material_boundary;"
        in material
    )
    assert "terrain_cell_material_boundary(" not in material
    assert (
        "boolterrain_boundary_final="
        "vsb_product_selected_terrain_receiver.material_boundary!=0u;"
        in payload_root
    )
    assert "terrain_cell_material_boundary(" not in payload_root

    # Edge and sidewall now share one immutable fact per cardinal neighbor.
    # Center and cardinal materials are carried from the admitted receiver.
    # The edge helper reconstructs only the matching neighbor normal.
    assert "q3_probe_payload_clone_cell_edge_score(" not in whole
    assert "q3_probe_payload_clone_cell_sidewall_score(" not in whole
    assert whole.count("q3_probe_payload_clone_cell_edge_fact(") == 5
    assert world_edge.count("q3_probe_payload_clone_cell_edge_fact(") == 4
    assert "splat_mat_at(" not in edge_fact
    assert "splat_mat_at(" not in world_edge
    assert "neighbor_material" in edge_fact
    assert edge_fact.count("product_source_cell_normal(nbr_cell)") == 1
    assert "splat_mat_at(cell)" not in world_edge
    assert (
        "q3_probe_payload_apply_world_cell_edge("
        "context.material_cell,material,family,"
        "vsb_product_selected_terrain_classification.edge_dirt_mountain,"
        "terrain.glyph);"
        in resolver
    )
    assert (
        "terrain.glyph=product_mountain_payload_glyph("
        "terrain.glyph,uint(max(family,0)),context.material_cell,"
        "elevation_row,dirt_mountain);"
        in resolver
    )
    assert (
        "precisefloatnormal_delta=1.0-max(dot("
        "vsb_product_selected_terrain_receiver.normal,"
        "product_source_cell_normal(nbr_cell)),0.0);"
        "precisefloatnormal_score=normal_delta*2.15;"
        in edge_fact
    )
    assert (
        "fact.sidewall_score=smoothstep("
        "Q3_CLONE_SIDEWALL_HEIGHT_THRESHOLD,"
        "Q3_CLONE_SIDEWALL_HEIGHT_THRESHOLD+3.2,"
        "signed_height_delta)"
        "*mix(0.70,1.0,clamp(normal_delta*2.4,0.0,1.0));"
        in edge_fact
    )
    cardinal_calls = {
        "cr": "east",
        "cl": "west",
        "cd": "south",
        "cu": "north",
    }
    for cell_name, field in cardinal_calls.items():
        assert world_edge.count(
            f"{cell_name},"
            "center_material,"
            f"vsb_product_selected_terrain_receiver.{field}_material,"
            f"vsb_product_selected_terrain_receiver.{field}_height"
        ) == 1
    assert (
        "precisefloatfol_surface_y="
        "vsb_product_selected_terrain_receiver.center_height;"
        "boolfol_on_surface=abs("
        "context.anchor_world.y-fol_surface_y)<=1.75;"
        in foliage
    )

    # The admitted terrain compiler closure must contain no surviving cardinal
    # height reconstruction. Five materializer calls are the sole live source.
    live_terrain_bodies = {
        "ramp": ramp,
        "slope_contrast": slope_contrast,
        "ramp_steps": ramp_steps,
        "ridge": ridge,
        "contact": contact,
        "classification": classification,
        "resolver": resolver,
        "mountain_glyph": mountain_glyph,
        "grammar": grammar,
        "edge_fact": edge_fact,
        "world_edge": world_edge,
        "foliage": foliage,
        "material": material,
        "payload_root": payload_root,
    }
    for name, body in live_terrain_bodies.items():
        assert "cell_avg_height(" not in body, (
            f"{name}: surviving terrain height reconstruction"
        )

    # Terrain and retained static receivers consume their cast scalar before
    # shade/glyph quantisation. The late path excludes both owners and keeps
    # the generic cell helper water-only with its authored surface law intact.
    assert prequant_receiver == (
        "returnowner_kind==OWNER_TERRAIN||owner_kind==OWNER_STATIC_MESH;"
    )
    assert (
        "if(!q3_probe_payload_cast_is_prequant_receiver("
        "context.surface_kind)){" in shadow
    )
    assert "vsb_product_selected_terrain_receiver" not in shadow
    assert (
        "if(q3_probe_payload_cast_is_prequant_receiver("
        "context.surface_kind)){"
        "receiver_cast_shadow_attenuation="
        "q3_probe_payload_cast_shadow_attenuation("
        "context.anchor_world,material_normal,"
        "context.surface_kind,context.material_cell.x,context.flags);"
        "}" in material
    )
    assert shadow.count("q3_probe_payload_cell_shadow_receiver(") == 1
    assert (
        "if(context.surface_kind==OWNER_WATER){"
        "ivec2receiver_cell=ivec2(floor("
        "context.anchor_world.xz-params.terrain_params.xy));"
        "ProductShadowReceivercell_receiver="
        "q3_probe_payload_cell_shadow_receiver("
        "context.surface_kind,receiver_cell);" in shadow
    )
    assert "cell_avg_height(" not in water_receiver
    assert "product_source_cell_normal(" not in water_receiver
    assert "receiver.cell_keyed=owner_kind==OWNER_WATER;" in water_receiver
    assert (
        "receiver.world=vec3(xz.x,WATER_SURFACE_Y,xz.y);"
        "receiver.normal=vec3(0.0,1.0,0.0);" in water_receiver
    )

    # The shipping compiler call remains reachable exactly once from main.
    # The protected compiler still sequences material before shadow/lit.
    assert main.count("compile_probe_subcell_payload(") == 1
    assert (
        "compile_probe_subcell_payload("
        "input_value.context,input_value.facts,input_value.ray.dir)"
        in main
    )
    assert payload_root.count("q3_probe_payload_apply_material(") == 1
    assert payload_root.count("q3_probe_payload_apply_shadow_and_lit(") == 1
    assert payload_root.find("q3_probe_payload_apply_material(") < (
        payload_root.find("q3_probe_payload_apply_shadow_and_lit(")
    )


def terrain_lambert_and_foliage_mutants(source: str) -> dict[str, str]:
    """Recreate every deleted duplicate Lambert/light-direction owner."""
    resurrected_face_resolver = source.replace(
        "void product_apply_ambient_base(",
        "bool matlib_face_resolve();\n"
        "bool matlib_face_resolve() {\n"
        "\treturn false;\n"
        "}\n\n"
        "void product_apply_ambient_base(",
        1,
    )
    assert resurrected_face_resolver != source

    return {
        "ambient_lambert_owner_resurrected": replace_in_function(
            source,
            "q3_probe_payload_apply_material",
            "\t\tproduct_apply_ambient_base(\n"
            "\t\t\tmatlib_fg, matlib_bg, nrm, shade.lambert_raw, cloud_shadow, family);",
            "\t\tprecise float ambient_lambert_raw = max(dot(\n"
            "\t\t\tnrm, vsb_product_selected_effective_sun_dir), 0.0);\n"
            "\t\tproduct_apply_ambient_base(\n"
            "\t\t\tmatlib_fg, matlib_bg, nrm, ambient_lambert_raw, cloud_shadow, family);",
        ),
        "ambient_uses_shd_continuous_instead_of_carrier": replace_in_function(
            source,
            "q3_probe_payload_apply_material",
            "\t\t\tmatlib_fg, matlib_bg, nrm, shade.lambert_raw, cloud_shadow, family);",
            "\t\t\tmatlib_fg, matlib_bg, nrm, shade.shd_continuous, cloud_shadow, family);",
        ),
        "shade_carrier_assignment_removed_with_zero_substitute":
            replace_in_function(
                source,
                "q3_probe_payload_compile_terrain_shade",
                "\tshade.lambert_raw = continuous_lambert_raw;",
                "\tshade.lambert_raw = 0.0;",
            ),
        "lambert_clamp_removed": replace_in_function(
            source,
            "q3_probe_payload_compile_terrain_shade",
            "\tprecise float continuous_lambert_raw = max(dot(\n"
            "\t\tcontinuous_detail.normal,\n"
            "\t\tvsb_product_selected_effective_sun_dir), 0.0);",
            "\tprecise float continuous_lambert_raw = dot(\n"
            "\t\tcontinuous_detail.normal,\n"
            "\t\tvsb_product_selected_effective_sun_dir);",
        ),
        "lambert_sun_substituted": replace_in_function(
            source,
            "q3_probe_payload_compile_terrain_shade",
            "\t\tcontinuous_detail.normal,\n"
            "\t\tvsb_product_selected_effective_sun_dir), 0.0);",
            "\t\tcontinuous_detail.normal,\n"
            "\t\tnormalize(vec3(0.78, 0.48, -0.38))), 0.0);",
        ),
        "lambert_precise_removed": replace_in_function(
            source,
            "q3_probe_payload_compile_terrain_shade",
            "\tprecise float continuous_lambert_raw = max(dot(",
            "\tfloat continuous_lambert_raw = max(dot(",
        ),
        "foliage_local_sun_owner_resurrected": replace_in_function(
            source,
            "foliage_apply_root_terrain_ramp_band",
            "\tfloat ramp_band = clamp(float(root_ramp_band) / 6.0, 0.0, 1.0);",
            "\tfloat local_sun = max(\n"
            "\t\tvsb_product_selected_effective_sun_dir.y, 0.0);\n"
            "\tfloat ramp_band = clamp(\n"
            "\t\tfloat(root_ramp_band) / 6.0 + local_sun * 0.0, 0.0, 1.0);",
        ),
        "foliage_root_band_divisor_drifted": replace_in_function(
            source,
            "foliage_apply_root_terrain_ramp_band",
            "float(root_ramp_band) / 6.0",
            "float(root_ramp_band) / 7.0",
        ),
        "matlib_face_resolve_resurrected": resurrected_face_resolver,
    }


def terrain_authority_mutants(source: str) -> dict[str, str]:
    """Recreate each deleted re-key owner as valid Vulkan GLSL."""
    q3_helper_anchor = "void q3_probe_payload_apply_lit_gain("
    assert source.count(q3_helper_anchor) == 1
    resurrected_rekey_helper = (
        "void q3_probe_payload_apply_rekey(\n"
        "\tinout ProbePayloadContext context,\n"
        "\tinout ProbePayloadCompileFacts facts\n"
        ") {\n"
        "\tcontext.trace_receiver += vec3(float(facts.source_key & 0u));\n"
        "}\n\n"
    )
    legacy_rekey_root = source.replace(
        q3_helper_anchor,
        resurrected_rekey_helper + q3_helper_anchor,
        1,
    )

    height_helper_anchor = "bool vsb_product_descriptor_common_valid("
    assert source.count(height_helper_anchor) == 1
    legacy_height_sampler = source.replace(
        height_helper_anchor,
        "float vsb_product_terrain_height(vec2 world_xz) {\n"
        "\treturn texelFetch(\n"
        "\t\tterrain_corner_height_tex, ivec2(floor(world_xz)), 0).r;\n"
        "}\n\n"
        + height_helper_anchor,
        1,
    )

    far_water_rekey = source.replace(
        q3_helper_anchor,
        resurrected_rekey_helper + q3_helper_anchor,
        1,
    )
    far_water_rekey = replace_in_function(
        far_water_rekey,
		"q3_probe_payload_apply_water",
		"\tif (context.surface_kind != OWNER_WATER) return;",
		"\tif (context.surface_kind != OWNER_WATER) return;\n"
		"\tq3_probe_payload_apply_rekey(context, facts);",
    )

    fact_field = source.replace(
        "\tbool valid;\n"
		"\tbool terrain_face_role;",
        "\tbool valid;\n"
		"\tbool rekey_adopted;\n"
		"\tbool terrain_face_role;",
        1,
    )
    assert fact_field != source
    fact_field = replace_in_function(
        fact_field,
        "vsb_product_empty_compile_input",
        "\tinput_value.facts.valid = false;",
        "\tinput_value.facts.valid = false;\n"
        "\tinput_value.facts.rekey_adopted = false;",
    )

    corner_fetches = replace_in_function(
        source,
        "vsb_product_prepare_terrain",
        "\tvsb_product_selected_terrain_classification =\n"
        "\t\tproduct_terrain_classification_facts(\n"
        "\t\t\tcell,\n"
        "\t\t\tproduct_terrain_family(int(record.material)),\n"
        "\t\t\tinput_value.selected_corner_heights);",
        "\tvsb_product_selected_terrain_classification =\n"
        "\t\tproduct_terrain_classification_facts(\n"
        "\t\t\tcell,\n"
        "\t\t\tproduct_terrain_family(int(record.material)),\n"
        "\t\t\tinput_value.selected_corner_heights);\n"
        "\tfloat retired_rekey_corner_sum =\n"
        "\t\ttexelFetch(terrain_corner_height_tex, cell, 0).r\n"
        "\t\t+ texelFetch(terrain_corner_height_tex, cell + ivec2(1, 0), 0).r\n"
        "\t\t+ texelFetch(terrain_corner_height_tex, cell - ivec2(1, 0), 0).r\n"
        "\t\t+ texelFetch(terrain_corner_height_tex, cell + ivec2(0, 1), 0).r\n"
        "\t\t+ texelFetch(terrain_corner_height_tex, cell - ivec2(0, 1), 0).r;\n"
        "\tinput_value.facts.world_depth += retired_rekey_corner_sum * 0.0;",
    )

    return {
        "legacy_rekey_root_resurrected": legacy_rekey_root,
        "legacy_rekey_height_sampler_resurrected": legacy_height_sampler,
        "terrain_anchor_reconstructed_from_ray_depth": replace_in_function(
            source,
            "vsb_product_compile_input",
            "\tinput_value.context.anchor_world = record.stable_world_anchor.xyz;",
            "\tinput_value.context.anchor_world = vec3(\n"
            "\t\tinput_value.ray.near_world.x\n"
            "\t\t\t+ input_value.ray.dir.x * record.depth,\n"
            "\t\tinput_value.ray.near_world.y\n"
            "\t\t\t+ input_value.ray.dir.y * record.depth,\n"
            "\t\tinput_value.ray.near_world.z\n"
            "\t\t\t+ input_value.ray.dir.z * record.depth);",
        ),
        "terrain_cell_rederived_from_world_position": replace_in_function(
            source,
            "vsb_product_prepare_terrain",
            "\tivec2 cell = ivec2(unsigned_cell);",
            "\tivec2 cell = ivec2(floor(\n"
            "\t\trecord.world_position.xz\n"
            "\t\t\t- product_vsb_frame.heightfield_origin_cell.xy));",
        ),
        "terrain_authoritative_admission_removed": replace_in_function(
            source,
            "vsb_product_prepare_terrain",
            "\tif (!all(equal(\n"
            "\t\t\tfloatBitsToUint(expected_anchor),\n"
            "\t\t\tfloatBitsToUint(record.stable_world_anchor)))) {",
            "\tif (false && !all(equal(\n"
            "\t\t\tfloatBitsToUint(expected_anchor),\n"
            "\t\t\tfloatBitsToUint(record.stable_world_anchor)))) {",
        ),
        "far_water_rekey_call_resurrected": far_water_rekey,
        "rekey_fact_field_resurrected": fact_field,
        "terrain_rekey_corner_fetch_resurrected": corner_fetches,
    }


def mutation_witnesses(source: str) -> dict[str, str]:
    resurrected_ramp_owner = source.replace(
        "ProductTerrainProduct product_compile_terrain_product_morphology(",
        "uint product_terrain_ramp_band_fact(\n"
        "\tivec2 cell, int family, int elv\n"
        ") {\n"
        "\treturn product_terrain_ramp_facts(\n"
        "\t\tcell, family, elv, elv).product_band;\n"
        "}\n\n"
        "ProductTerrainProduct product_compile_terrain_product_morphology(",
        1,
    )
    assert resurrected_ramp_owner != source
    resurrected_ramp_owner = replace_in_function(
        resurrected_ramp_owner,
        "product_compile_terrain_product_morphology",
        "\tp.ramp_band = terrain_ramp_band;",
        "\tp.ramp_band = product_terrain_ramp_band_fact(\n"
        "\t\tterrain_cell, family, elevation_center);",
    )
    resurrected_elevation_owner = source.replace(
        "ProductTerrainClassificationFacts product_terrain_classification_facts(",
        "uint terrain_cell_elevation_class(ivec2 cell) {\n"
        "\treturn terrain_elevation_class_from_corners(\n"
        "\t\tcorner_height(cell + ivec2(0, 0)),\n"
        "\t\tcorner_height(cell + ivec2(1, 0)),\n"
        "\t\tcorner_height(cell + ivec2(0, 1)),\n"
        "\t\tcorner_height(cell + ivec2(1, 1)));\n"
        "}\n\n"
        "ProductTerrainClassificationFacts product_terrain_classification_facts(",
        1,
    )
    assert resurrected_elevation_owner != source
    resurrected_elevation_owner = replace_in_function(
        resurrected_elevation_owner,
        "product_terrain_classification_facts",
        "\tuint elevation_center =\n"
        "\t\tterrain_elevation_class_from_corners(h00, h10, h01, h11);",
        "\tuint elevation_center = terrain_cell_elevation_class(cell);",
    )
    mutants = {
        "east_west_cardinals_swapped": replace_in_function(
            source,
            "q3_probe_payload_terrain_receiver_facts",
            "\tfloat east_height =\n"
            "\t\tcell_avg_height(receiver_cell + ivec2(1, 0));\n"
            "\tfloat south_height =\n"
            "\t\tcell_avg_height(receiver_cell + ivec2(0, 1));\n"
            "\tfloat west_height =\n"
            "\t\tcell_avg_height(receiver_cell + ivec2(-1, 0));",
            "\tfloat east_height =\n"
            "\t\tcell_avg_height(receiver_cell + ivec2(-1, 0));\n"
            "\tfloat south_height =\n"
            "\t\tcell_avg_height(receiver_cell + ivec2(0, 1));\n"
            "\tfloat west_height =\n"
            "\t\tcell_avg_height(receiver_cell + ivec2(1, 0));",
        ),
        "surviving_cardinal_reconstruction": replace_in_function(
            source,
            "product_compile_terrain_product_morphology",
            "\tfloat hx = vsb_product_selected_terrain_receiver.east_height;",
            "\tfloat hx = cell_avg_height(terrain_cell + ivec2(1, 0));",
        ),
        "boundary_recomputed_during_finalization": replace_in_function(
            source,
            "compile_probe_subcell_payload",
            "\t\tbool terrain_boundary_final =\n"
            "\t\t\tvsb_product_selected_terrain_receiver.material_boundary != 0u;",
            "\t\tbool terrain_boundary_final =\n"
            "\t\t\tvsb_product_selected_terrain_receiver.east_material\n"
            "\t\t\t\t!= int(context.material);",
        ),
        "slope_consumer_bypasses_east_fact": replace_in_function(
            source,
            "product_terrain_ramp_facts",
            "\t\tvsb_product_selected_terrain_receiver.east_height,",
            "\t\tvsb_product_selected_terrain_receiver.center_height,",
        ),
        "wrong_cardinal_pairing": replace_in_function(
            source,
            "q3_probe_payload_apply_world_cell_edge",
            "\t\tq3_probe_payload_clone_cell_edge_fact(\n"
            "\t\t\tcl, center_material,\n"
            "\t\t\tvsb_product_selected_terrain_receiver.west_material,\n"
            "\t\t\tvsb_product_selected_terrain_receiver.west_height);",
            "\t\tq3_probe_payload_clone_cell_edge_fact(\n"
            "\t\t\tcl, center_material,\n"
            "\t\t\tvsb_product_selected_terrain_receiver.east_material,\n"
            "\t\t\tvsb_product_selected_terrain_receiver.east_height);",
        ),
        "second_normal_reconstruction": replace_in_function(
            source,
            "q3_probe_payload_clone_cell_edge_fact",
            "\tprecise float normal_delta = 1.0 - max(dot(",
            "\tprecise vec3 duplicate_neighbor_normal =\n"
            "\t\tproduct_source_cell_normal(nbr_cell);\n"
            "\tprecise float normal_delta = 1.0 - max(dot(",
        ),
        "center_material_texture_reload": replace_in_function(
            source,
            "q3_probe_payload_apply_world_cell_edge",
            "\tif (family != TERRAIN_FAMILY_DIRT\n"
            "\t\t\t&& family != TERRAIN_FAMILY_CLIFF_ROCK) return false;",
            "\tif (family != TERRAIN_FAMILY_DIRT\n"
            "\t\t\t&& family != TERRAIN_FAMILY_CLIFF_ROCK) return false;\n"
            "\tcenter_material = splat_mat_at(cell);",
        ),
        "downstream_neighbor_material_reload": replace_in_function(
            source,
            "q3_probe_payload_clone_cell_edge_fact",
            "\tQ3ProbePayloadCellEdgeFact fact;",
            "\tneighbor_material = splat_mat_at(nbr_cell);\n"
            "\tQ3ProbePayloadCellEdgeFact fact;",
        ),
        "border_fallback_changed": replace_in_function(
            source,
            "q3_probe_payload_terrain_receiver_facts",
            "\tint east_material = int(current_material);",
            "\tint east_material = -1;",
        ),
        "material_bounds_predicate_removed": replace_in_function(
            source,
            "q3_probe_payload_terrain_receiver_facts",
            "\tif (receiver_cell.x + 1 < dims.x) {",
            "\tif (true) {",
        ),
        "terrain_receiver_materializer_bypassed": replace_in_function(
            source,
            "vsb_product_prepare_terrain",
            "\tvsb_product_selected_terrain_receiver =\n"
            "\t\tq3_probe_payload_terrain_receiver_facts(\n"
            "\t\t\tcell, record.material);",
            "\tvsb_product_selected_terrain_receiver.world =\n"
            "\t\trecord.stable_world_anchor.xyz;",
        ),
        "resolver_center_reclassification": replace_in_function(
            source,
            "product_compile_terrain_product_morphology",
            "\tint elv = elevation_center;",
            "\tint elv = product_terrain_classification_facts(\n"
            "\t\tterrain_cell, family, vec4(0.0)).elevation_center;",
        ),
        "final_glyph_role_recomputed": replace_in_function(
            source,
            "product_compile_terrain_morphology",
            "\tbool dirt_mountain =\n"
            "\t\tvsb_product_selected_terrain_classification.product_dirt_mountain;",
            "\tbool dirt_mountain = product_terrain_classification_facts(\n"
            "\t\tcontext.material_cell, family,\n"
            "\t\tvec4(0.0)).product_dirt_mountain;",
        ),
        "edge_role_recomputed": replace_in_function(
            source,
            "product_compile_terrain_morphology",
            "\t\t\tvsb_product_selected_terrain_classification.edge_dirt_mountain,\n"
            "\t\t\tterrain.glyph);",
            "\t\t\tproduct_terrain_classification_facts(\n"
            "\t\t\t\tcontext.material_cell, family,\n"
            "\t\t\t\tvec4(0.0)).edge_dirt_mountain,\n"
            "\t\t\tterrain.glyph);",
        ),
        "edge_fed_product_boolean": replace_in_function(
            source,
            "product_compile_terrain_morphology",
            "\t\t\tvsb_product_selected_terrain_classification.edge_dirt_mountain,\n"
            "\t\t\tterrain.glyph);",
            "\t\t\tvsb_product_selected_terrain_classification.product_dirt_mountain,\n"
            "\t\t\tterrain.glyph);",
        ),
        "east_class_changed_to_west": replace_in_function(
            source,
            "product_terrain_classification_facts",
            "\t\tterrain_elevation_class_from_corners(h10, h20, h11, h21);",
            "\t\tterrain_elevation_class_from_corners(h00, h20, h11, h21);",
        ),
        "south_class_wrong_shared_vertices": replace_in_function(
            source,
            "product_terrain_classification_facts",
            "\t\tterrain_elevation_class_from_corners(h01, h11, h02, h12);",
            "\t\tterrain_elevation_class_from_corners(h00, h11, h02, h12);",
        ),
        "classification_center_fetch_resurrected": replace_in_function(
            source,
            "product_terrain_classification_facts",
            "\tfloat h00 = selected_corner_heights.x;\n"
            "\tfloat h10 = selected_corner_heights.y;\n"
            "\tfloat h01 = selected_corner_heights.z;\n"
            "\tfloat h11 = selected_corner_heights.w;",
            "\tfloat h00 = corner_height(cell + ivec2(0, 0));\n"
            "\tfloat h10 = corner_height(cell + ivec2(1, 0));\n"
            "\tfloat h01 = corner_height(cell + ivec2(0, 1));\n"
            "\tfloat h11 = corner_height(cell + ivec2(1, 1));",
        ),
        "classification_selected_y_z_swapped": replace_in_function(
            source,
            "product_terrain_classification_facts",
            "\tfloat h10 = selected_corner_heights.y;\n"
            "\tfloat h01 = selected_corner_heights.z;",
            "\tfloat h10 = selected_corner_heights.z;\n"
            "\tfloat h01 = selected_corner_heights.y;",
        ),
        "classification_selected_argument_replaced": replace_in_function(
            source,
            "vsb_product_prepare_terrain",
            "\t\t\tinput_value.selected_corner_heights);",
            "\t\t\tvec4(0.0));",
        ),
        "mountain_elevation_row_thread_removed": replace_in_function(
            source,
            "product_compile_terrain_morphology",
            "\t\televation_row,\n"
            "\t\tdirt_mountain);",
            "\t\t0,\n"
            "\t\tdirt_mountain);",
        ),
        "per_cell_elevation_fetch_owner_resurrected":
            resurrected_elevation_owner,
        "dirt_extra_corners_fetched_before_gate": replace_in_function(
            source,
            "product_terrain_classification_facts",
            "\tif (family != TERRAIN_FAMILY_DIRT) return facts;\n"
            "\tfloat h20 = corner_height(cell + ivec2(2, 0));\n"
            "\tfloat h21 = corner_height(cell + ivec2(2, 1));\n"
            "\tfloat h02 = corner_height(cell + ivec2(0, 2));\n"
            "\tfloat h12 = corner_height(cell + ivec2(1, 2));",
            "\tfloat h20 = corner_height(cell + ivec2(2, 0));\n"
            "\tfloat h21 = corner_height(cell + ivec2(2, 1));\n"
            "\tfloat h02 = corner_height(cell + ivec2(0, 2));\n"
            "\tfloat h12 = corner_height(cell + ivec2(1, 2));\n"
            "\tif (family != TERRAIN_FAMILY_DIRT) return facts;",
        ),
        "classification_precise_inserted": replace_in_function(
            source,
            "product_terrain_classification_facts",
            "\tfloat h00 = selected_corner_heights.x;",
            "\tprecise float h00 = selected_corner_heights.x;",
        ),
        "ramp_band_owner_resurrected": resurrected_ramp_owner,
        "ramp_patch_noise_called_twice": replace_in_function(
            source,
            "product_terrain_ramp_facts_for_surface",
            "\tfloat cell_lane =\n"
            "\t\tproduct_smooth_terrain_noise(cell, 3.0, 410223u) - 0.5;",
            "\tfloat duplicate_patch_lane =\n"
            "\t\tproduct_smooth_terrain_noise(cell, 6.0, 410211u) - 0.5;\n"
            "\tpatch_lane += duplicate_patch_lane * 0.0;\n"
            "\tfloat cell_lane =\n"
            "\t\tproduct_smooth_terrain_noise(cell, 3.0, 410223u) - 0.5;",
        ),
        "ramp_cell_noise_called_twice": replace_in_function(
            source,
            "product_terrain_ramp_facts_for_surface",
            "\tfloat patch_w = family == TERRAIN_FAMILY_GRASS ? 0.0 : 0.08;",
            "\tfloat duplicate_cell_lane =\n"
            "\t\tproduct_smooth_terrain_noise(cell, 3.0, 410223u) - 0.5;\n"
            "\tcell_lane += duplicate_cell_lane * 0.0;\n"
            "\tfloat patch_w = family == TERRAIN_FAMILY_GRASS ? 0.0 : 0.08;",
        ),
        "ramp_legacy_sun_selector_restored": replace_in_function(
            source,
            "product_terrain_ramp_facts_for_surface",
            "\tfloat lambert = max(dot(\n"
            "\t\tnrm, vsb_product_selected_effective_sun_dir), 0.0);",
            "\tfloat lambert = max(dot(\n"
            "\t\tnrm, normalize(effective_sun_dir())), 0.0);",
        ),
        "ramp_carried_sun_renormalized": replace_in_function(
            source,
            "product_terrain_ramp_facts_for_surface",
            "\tfloat lambert = max(dot(\n"
            "\t\tnrm, vsb_product_selected_effective_sun_dir), 0.0);",
            "\tfloat lambert = max(dot(\n"
            "\t\tnrm, normalize(vsb_product_selected_effective_sun_dir)), 0.0);",
        ),
        "ramp_splat_sun_substituted": replace_in_function(
            source,
            "product_terrain_ramp_facts_for_surface",
            "\tfloat lambert = max(dot(\n"
            "\t\tnrm, vsb_product_selected_effective_sun_dir), 0.0);",
            "\tfloat lambert = max(dot(\n"
            "\t\tnrm, normalize(vec3(0.78, 0.48, -0.38))), 0.0);",
        ),
        "ramp_moon_direction_substituted": replace_in_function(
            source,
            "product_terrain_ramp_facts_for_surface",
            "\tfloat lambert = max(dot(\n"
            "\t\tnrm, vsb_product_selected_effective_sun_dir), 0.0);",
            "\tfloat lambert = max(dot(nrm, product_moon_dir()), 0.0);",
        ),
        "ramp_product_uses_visual_elevation": replace_in_function(
            source,
            "product_terrain_ramp_facts_for_surface",
            "\tif (product_elv == 1) product_raw += 0.14;\n"
            "\tif (product_elv == 3) product_raw -= 0.12;",
            "\tif (visual_elv == 1) product_raw += 0.14;\n"
            "\tif (visual_elv == 3) product_raw -= 0.12;",
        ),
        "ramp_product_reuses_visual_raw": replace_in_function(
            source,
            "product_terrain_ramp_facts_for_surface",
            "\t\tuint(floor(clamp(product_raw, 0.0, 0.999) * 7.0));",
            "\t\tuint(floor(clamp(visual_raw, 0.0, 0.999) * 7.0));",
        ),
        "ramp_product_coefficient_drift": replace_in_function(
            source,
            "product_terrain_ramp_facts_for_surface",
            "\tfloat product_raw = lambert * 0.70 + nrm.y * 0.22\n"
            "\t\t+ slope * 0.34 + patch_lane * 0.08\n"
            "\t\t+ cell_lane * 0.04;",
            "\tfloat product_raw = lambert * 0.70 + nrm.y * 0.22\n"
            "\t\t+ slope * 0.34 + patch_lane * 0.09\n"
            "\t\t+ cell_lane * 0.04;",
        ),
        "terrain_phase_ns_uses_cross_feature_axis": replace_in_function(
            source,
            "product_terrain_segment_id",
            "continuity == 1u ? cell.y",
            "continuity == 1u ? cell.x",
        ),
        "terrain_phase_restores_ramp_band": replace_in_function(
            source,
            "product_terrain_segment_phase",
            "p.ridge_segment_id ^ (p.face_normal_class * 13u)",
            "p.ridge_segment_id ^ (p.ramp_band * 17u) ^ "
            "(p.face_normal_class * 13u)",
        ),
        "terrain_depth_restores_absolute_height": replace_in_function(
            source,
            "product_compile_terrain_product_morphology",
            "p.depth_class = uint(floor(clamp(step_amount / 2.8, 0.0, 0.999) * 8.0));",
            "p.depth_class = uint(clamp(floor(terrain_center_height / 2.0), 0.0, 7.0));",
        ),
        # The shared morphology result owns the discrete ramp fact. Recomputing
        # that fact in the quarter paint continuation restores a second owner.
        "resolver_recomputes_product_band": replace_in_function(
            source,
            "q3_probe_payload_apply_material",
            "\t\tProductTerrainRampFacts ramp = morphology.ramp;",
            "\t\tProductTerrainRampFacts ramp = product_terrain_ramp_facts(\n"
            "\t\t\tcontext.material_cell, family, shade.elv,\n"
            "\t\t\tvsb_product_selected_terrain_classification.elevation_center);",
        ),
    }
    mutants.update(terrain_lambert_and_foliage_mutants(source))
    mutants.update(terrain_authority_mutants(source))
    reds: dict[str, str] = {}
    compiler = probe_contract.glslang_bin()
    assert compiler is not None, "glslangValidator/glslang is required"
    with tempfile.TemporaryDirectory(
        prefix="fl4431-terrain-edge-fact-mutants-"
    ) as tmp_dir:
        for name, mutant in mutants.items():
            stages = probe_contract.split_stages(mutant)
            ok, detail = probe_contract.compile_stage(
                compiler, "compute", stages["compute"], Path(tmp_dir)
            )
            assert ok, f"mutation is not compile-valid: {name}: {detail}"
            try:
                assert_terrain_receiver_single_source(mutant)
            except AssertionError as error:
                reds[name] = str(error) or "AssertionError"
                continue
            raise AssertionError(f"mutation stayed green: {name}")
    return reds


def main() -> int:
    source = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    assert_terrain_receiver_single_source(source)
    reds = mutation_witnesses(source)
    print(
        "FL4431_PRODUCT_TERRAIN_RECEIVER_SINGLE_SOURCE_PASS "
        "cell_avg_calls_upper=58->5 source_normal_calls_upper=21->9 "
        "corner_height_fetches_upper=295->27 "
        "classification_corner_calls=8->4 non_dirt_extra_calls=4->0 "
        "legacy_rekey_corner_fetches_per_terrain_subcell=20->0 "
        f"mutation_count={len(reds)} mutants={','.join(reds)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
