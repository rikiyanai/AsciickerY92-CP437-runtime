#!/usr/bin/env python3
"""FL-4431 Product finite-water single-source contract.

One admitted finite-water slot owns the typed shoreline/contact fact query and
submerged-terrain reconstruction. The primary water compiler continuously
transmits that carried terrain value through every depth band, while the later
night pass rebuilds its sample-position-dependent values from the same fact.
Far-water backstop records never acquire the fact. FL-4216 F11 makes the
query-field representation the one geometry-family-neutral source; Product
must not retain the retired 13x13 shoreline scan or its binding-23 adapter.

The embedded post-cut fixture makes the red-first mutation witnesses executable
before the shipping shader adopts the seam.  This is structural evidence only:
it does not measure frame time, inspect pixels, or establish the 60 FPS target.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import math
import re
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path


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


def surface_fact_query_name(source: str) -> str:
    """Return the one typed F11 field-query function name.

    The contract permits the two reviewed spellings while keeping the ABI
    narrow. A missing query is a deliberate red-first failure until the new
    compiler/product seam is connected.
    """

    executable = without_comments(source)
    candidates = re.findall(
        r"\b(product_(?:surface_facts|water_interaction_field)_(?:query|fact))\s*\(",
        executable,
    )
    assert candidates, (
        "missing typed FL-4216 WaterInteractionField query in Product"
    )
    names = set(candidates)
    assert len(names) == 1, f"multiple typed shoreline query owners: {sorted(names)}"
    return candidates[0]


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


def assert_finite_water_single_source(source: str) -> None:
    whole = compact(source)
    materializer = compact(
        function_body(source, "product_finite_water_cell_facts")
    )
    sample = compact(function_body(source, "product_water_sample"))
    prepare = compact(function_body(source, "vsb_product_prepare_water"))
    water = compact(function_body(source, "q3_probe_payload_apply_water"))
    finalize = compact(function_body(source, "vsb_product_finalize_record"))
    main = compact(function_body(source, "main"))

    fact_struct = re.search(
        r"\bstruct\s+ProductFiniteWaterCellFacts\s*\{(.*?)\}\s*;",
        without_comments(source),
        re.S,
    )
    assert fact_struct is not None, (
        "missing ProductFiniteWaterCellFacts carried-value owner"
    )
    analytic_horizon = "boolanalytic_backstop;" in compact(
		fact_struct.group(1)
	)
    direct_contact = "booldirect_water_solid_contact;" in compact(
        fact_struct.group(1)
    )
    expected_fact_fields = (
        "boolvalid;"
		+ ("boolanalytic_backstop;" if analytic_horizon else "")
		+
        "ivec2cell;"
        "floatwater_depth;"
        "floatdepth_fade;"
        "floatwater_thickness;"
        "floatambient_time;"
        "boolstable_cutover_paint;"
        "floatshore_dist_cells;"
        "intbreak_dir;"
        "vec3bed;"
        "boolsubmerged_source_present;"
        "uintsubmerged_source_glyph;"
        "vec3submerged_source_fg;"
        "vec3submerged_source_bg;"
        + ("booldirect_water_solid_contact;" if direct_contact else "")
    )
    assert compact(fact_struct.group(1)) == expected_fact_fields
    assert (
        "ProductFiniteWaterCellFacts"
        "vsb_product_selected_finite_water_facts;" in whole
    )

    # The definition plus the finite-water adapter call are the only two
    # references. This is one materialization per admitted finite-water slot,
    # not one materialization per later paint phase.
    assert whole.count("product_finite_water_cell_facts(") == 2
    query_name = surface_fact_query_name(source)
    # Exactly one field query belongs inside the finite-water materializer.
    # The query receives the cell and sample world position, so Product does
    # not reconstruct terrain neighbours or branch by OWNER_TERRAIN versus
    # OWNER_STATIC_MESH. The same function is the sole typed field owner.
    assert whole.count(f"{query_name}(") == 2
    assert materializer.count(f"{query_name}(") == 1
    query_call = re.search(
        rf"{re.escape(query_name)}\s*\(([^;{{}}]*)\)",
        without_comments(function_body(source, "product_finite_water_cell_facts")),
        re.S,
    )
    assert query_call is not None, "finite-water materializer must query the typed field"
    query_args = compact(query_call.group(1))
    assert "cell" in query_args and "sample_world_xz" in query_args
    assert "for(" not in materializer and "while(" not in materializer
    assert materializer.count("water_submerged_bed_color(") == 1
    assert "facts.shore_dist_cells" in materializer
    assert "facts.break_dir" in materializer

    # Retired F11 ownership is forbidden in executable Product text. Comments
    # may explain the migration, but the old symbols and 13x13 scan cannot be
    # reachable runtime owners.
    executable = compact(without_comments(source))
    for retired in (
        "product_fl4216_shore_fact(",
        "cell_solid_in_water(",
        "SolidWaterCellBits",
    ):
        assert retired not in executable, f"retired shoreline owner remains: {retired}"
    assert not re.search(r"for\s*\(\s*int\s+(?:dx|dy)\s*=\s*-6", executable)

    # Preserve the finite-water four-corner column-depth arithmetic exactly.
    # The far-water branch has no lattice cell and therefore cannot call the
    # cell-fact materializer.  Stable cutover paint remains explicitly true.
    average = (
        "floatselected_average_height=0.25*("
        "input_value.selected_corner_heights.x"
        "+input_value.selected_corner_heights.y"
        "+input_value.selected_corner_heights.z"
        "+input_value.selected_corner_heights.w);"
        "input_value.facts.water_depth=max("
        "WATER_SURFACE_Y-selected_average_height,0.0);"
    )
    # FL-4481 seam diagnosis 2026-08-18: water refusals now assign named
    # 0x023x prepare-diagnostic codes (terrain's 0x021x precedent), so the
    # refusal is a coded block rather than a bare return.
    finite_materialization = (
        "vsb_product_selected_finite_water_facts="
        "product_finite_water_cell_facts("
        "semantic_cell,record.world_position.xz,input_value.facts.water_depth,"
        "product_water_animation_time(),true);"
        "if(!vsb_product_selected_finite_water_facts.valid){"
        "vsb_product_prepare_diagnostic=0x0238u;"
        "returnfalse;}"
    )
    assert average + finite_materialization in prepare
    backstop_start = prepare.find("if(backstop){")
    finite_start = prepare.find("}else{", backstop_start)
    materialize_at = prepare.find(
        "product_finite_water_cell_facts(", finite_start
    )
    assert backstop_start >= 0 and finite_start > backstop_start
    assert materialize_at > finite_start
    assert "product_finite_water_cell_facts(" not in prepare[
        backstop_start:finite_start
    ]

    # Every slot clears the private fact before decoding a new b64 record.
    # Without this reset, a terrain slot following water can inherit validity.
    reset = "vsb_product_selected_finite_water_facts.valid=false;"
    assert main.count(reset) == 1
    assert main.find(reset) < main.find("vsb_product_compile_input(")

    # The reconstruction keeps the original field order and arithmetic.  Shore
    # distance, direction, and bed are copied from the one carried cell fact;
    # continuous wave, noise, wake, and activity still use sample_world_xz.
    assert whole.count("product_water_sample(") == 3
    assert (
        "ProductWaterSampleproduct_water_sample("
        "ProductFiniteWaterCellFactscell_facts,"
        "vec2sample_world_xz)" in whole
    )
    assert (
        "s.cell=cell_facts.cell;"
        "s.world_xz=sample_world_xz;"
        "s.water_depth=cell_facts.water_depth;"
        "s.depth_fade=cell_facts.depth_fade;"
        "s.water_thickness=cell_facts.water_thickness;"
        "s.submerged_source_present=cell_facts.submerged_source_present;"
        "s.submerged_source_glyph=cell_facts.submerged_source_glyph;"
        "s.submerged_source_fg=cell_facts.submerged_source_fg;"
        + (
            "s.direct_water_solid_contact="
            "cell_facts.direct_water_solid_contact;"
            if direct_contact else ""
        )
        +
        "s.submerged_visibility=0.0;"
        "floatambient_time=cell_facts.ambient_time;"
        "floatwave_freq=0.72;"
        "vec2wave_uv=s.world_xz*wave_freq;" in sample
    )
    assert (
        "s.stable_cutover_paint="
        "cell_facts.stable_cutover_paint;"
        "s.shore_dist_cells=cell_facts.shore_dist_cells;"
        "s.break_dir=cell_facts.break_dir;" in sample
    )
    assert "product_fl4216_shore_fact(" not in sample
    assert "water_submerged_bed_color(" not in sample
    depth_band = compact(
        function_body(source, "product_water_depth_band")
    )
    body_band = compact(
        function_body(source, "product_day_water_body_band")
    )
    sky_colour = compact(
        function_body(source, "product_day_water_sky_colour")
    )
    mottle_gain = compact(
        function_body(source, "product_day_water_mottle_gain")
    )
    cloud_colour = compact(
        function_body(source, "product_day_water_cloud_colour_amount")
    )
    assert depth_band == (
        "if(depth_coordinate<0.30)return0u;"
        "if(depth_coordinate<WATER_SHALLOW_TRANSITION_END)return1u;"
        "return2u;"
    )
    assert whole.count("product_water_depth_band(") == 3
    assert "product_day_water_depth_band(" not in whole
    assert body_band == (
        "if(band==0u)returnWATER_SHALLOW_BG;"
        "if(band==1u)returnWATER_MID_BG;"
        "returnWATER_DEEP_BG;"
    )
    assert "constfloatWATER_BED_VISIBILITY_FALLOFF=0.82;" in whole
    assert sky_colour == (
        "floatsun_height=clamp(product_sun_dir().y,0.0,1.0);"
        "floathigh_day=smoothstep(0.06,0.44,sun_height);"
        "returnmix(vec3(0.82,0.67,0.59),"
        "RECEIPTED_DAY_SKY_COLOR,high_day);"
    )
    assert mottle_gain == (
        "floatmass=stable_value_noise("
        "world_xz*0.045+vec2(13.7,-8.9),0x4129A11Du);"
        "if(mass<0.38)return0.90;"
        "if(mass>0.66)return1.10;"
        "return1.0;"
    )
    # FL-4737 R1 (2026-08-13): the water cloud amount now walks up-sun to the
    # shared cloud plane through product_cloud_shadow_projected_world instead of
    # sampling the field straight overhead, so it registers with the land
    # receivers. The unprojected form is pinned as falsified below.
    assert cloud_colour == (
        "if(!baseline_feature_clouds_enabled())return0.0;"
        "floatcloud=product_cloud_shadow_projected_world("
        "world_xz,WATER_SURFACE_Y);"
        "floatcloud_band=cloud<0.24?0.0:(cloud<0.62?0.42:0.78);"
        "floatdaylight=clamp(1.0-product_day_night_factor(),0.0,1.0);"
        "returncloud_band*daylight;"
    )
    assert "product_cloud_density_at_sample_xz(world_xz)" not in cloud_colour
    # FL-4737 F8 revision 2 (2026-08-13, adversarial review AD1/AD2): the
    # retained source stays in the column as the bed. Revision 1 moved it to a
    # last-position composite, which capped every surface stage by column
    # thickness and moved full-night bytes. ONE composition site either way; this
    # one is the layer a transmitted fact belongs in.
    assert (
        "vec3bed=cell_facts.submerged_source_present?"
        "cell_facts.submerged_source_bg:cell_facts.bed;"
        "uintdepth_band=product_water_depth_band(s.depth_fade);"
        "uintlegacy_depth_band=product_water_depth_band(s.water_depth);"
        "floatdepth_band_daylight=clamp("
        "1.0-product_day_night_factor(),0.0,1.0);"
        "vec3water_body=mix("
        "product_day_water_body_band(legacy_depth_band),"
        "product_day_water_body_band(depth_band),"
        "depth_band_daylight);"
        "floatmotion_value=s.wave_height*0.62+"
        "(product_water_gradient_noise(s.world_xz*0.075)-0.5)*0.38;"
        "floatmotion_gain=motion_value>0.24?1.045:"
        "(motion_value<-0.24?0.955:1.0);"
        "floatmottle_gain=product_day_water_mottle_gain(s.world_xz);"
        "water_body=clamp(water_body*motion_gain*mottle_gain,"
        "vec3(0.0),vec3(1.0));"
        "floatbed_visibility_falloff=mix("
        "WATER_BED_VISIBILITY_FALLOFF,"
        "WATER_BED_VISIBILITY_FALLOFF_DAY,"
        "depth_band_daylight);"
        "floatbed_visibility=clamp("
        "1.0-s.depth_fade*bed_visibility_falloff,"
        "0.0,1.0);"
        "s.bed_transmission=bed_visibility;"
        "s.submerged_visibility=cell_facts.submerged_source_present?"
        "bed_visibility:0.0;"
        "s.bg=mix(water_body,bed,bed_visibility);" in sample
    )
    # One transmission law, one owner: the day/night slope blend is computed
    # exactly once and both the fog stage and the direct-source stage in the
    # pair read the carried result rather than recomputing the slope.
    assert whole.count("bed_visibility_falloff=mix(") == 1
    assert whole.count("s.bed_transmission=") == 1
    # FL-4737 D3 (2026-08-13 adversarial review): product_water_sample carries NO
    # cloud term. Anything added here is rescaled by the affine background fog
    # mix downstream in product_apply_day_water_pair, so no position inside this
    # function can deliver a depth-independent lift. Both the original
    # pre-bed-mix fold and the interim post-bed-mix form are falsified.
    assert "cloud" not in sample
    assert "WATER_SHALLOW_BED_MAX_VISIBILITY" not in whole
    assert "WATER_SURFACE_FILM_MIN" not in whole

    # Finite paint remains cell-centred. Analytic horizon water uses a bounded
    # fact constructor and the same ProductWaterSample owner.
    if analytic_horizon:
        assert (
            "if(context.surface_kind!=OWNER_WATER)return;"
            "ProductFiniteWaterCellFactswater_facts="
            "context.payload_from_backstop?"
            "product_analytic_water_cell_facts(" in water
        )
        assert "ivec2water_cell=water_facts.cell;" in water
        assert (
            "ProductWaterSamplewater=product_water_sample("
            "water_facts,semantic_water_xz);" in water
        )
    else:
        assert (
            "if(context.surface_kind!=OWNER_WATER"
            "||context.payload_from_backstop"
            "||facts.distant_water_sky_paint"
            "||!vsb_product_selected_finite_water_facts.valid)return;" in water
        )
        assert "ivec2water_cell=vsb_product_selected_finite_water_facts.cell;" in water
        assert (
            "ProductWaterSamplewater=product_water_sample("
            "vsb_product_selected_finite_water_facts,semantic_water_xz);" in water
        )
    assert (
        "record.r0.y==OWNER_WATER"
        "&&!input_value.context.payload_from_backstop"
        "&&vsb_product_selected_finite_water_facts.valid" in finalize
    )
    assert (
        "ProductWaterSamplewater_sample=product_water_sample("
        "vsb_product_selected_finite_water_facts,"
        "input_value.context.trace_receiver.xz);" in finalize
    )
    assert (
        "product_apply_night_water_pair("
        "water_sample,night,record.fg.rgb,record.bg.rgb);"
        "record.fg.a=1.0;"
        "record.bg.a=1.0;" in finalize
    )
    assert "semantic_water_xz" not in finalize


def _f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _f32_add(left: float, right: float) -> float:
    return _f32(_f32(left) + _f32(right))


def _f32_mul(left: float, right: float) -> float:
    return _f32(_f32(left) * _f32(right))


def _continuous_wave_sample(
    world_xz: tuple[float, float],
    phase: float,
) -> float:
    """Execute the shader's two-term f32 wave reconstruction."""

    uv_x = _f32_mul(world_xz[0], 0.72)
    uv_y = _f32_mul(world_xz[1], 0.72)
    first = _f32_mul(math.sin(_f32_add(uv_x, phase)), 0.62)
    second_phase = _f32_add(
        _f32_mul(uv_y, 1.27),
        -_f32_mul(phase, 0.82),
    )
    second = _f32_mul(math.sin(second_phase), 0.38)
    return _f32(max(-1.0, min(1.0, _f32_add(first, second))))


def assert_off_center_receiver_witness() -> None:
    """Prove that replacing receiver XZ with cell-centre XZ changes paint."""

    cell_center = (_f32(117.5), _f32(-42.5))
    trace_receiver = (_f32(117.8125), _f32(-42.21875))
    phase = _f32(1.375)
    center_value = _continuous_wave_sample(cell_center, phase)
    receiver_value = _continuous_wave_sample(trace_receiver, phase)
    assert struct.pack("<f", center_value) != struct.pack(
        "<f", receiver_value
    ), (
        "off-centre CPU witness did not diverge: "
        f"center={center_value} receiver={receiver_value}"
    )


def assert_terrain_bed_transmission_endpoints() -> None:
    """Pin the selected continuous terrain-bed law independently of GLSL text."""

    visibility = lambda depth_fade: max(0.0, min(1.0, 1.0 - depth_fade * 0.82))
    assert visibility(0.0) == 1.0
    assert abs(visibility(1.0) - 0.18) <= 1.0e-9


# This compact GLSL-shaped fixture is not a shader implementation.  It gives
# the red-first test an executable post-cut grammar so every destructive
# mutation is proven capable of turning the selected contract red before the
# shipping source adopts that grammar.
REFERENCE_SOURCE = r"""
const float WATER_BED_VISIBILITY_FALLOFF = 0.82;
const float WATER_BED_VISIBILITY_FALLOFF_DAY = 0.50;
struct ProductFiniteWaterCellFacts {
    bool valid;
    ivec2 cell;
    float water_depth;
    float depth_fade;
    float water_thickness;
    float ambient_time;
    bool stable_cutover_paint;
    float shore_dist_cells;
    int break_dir;
    vec3 bed;
    bool submerged_source_present;
    uint submerged_source_glyph;
    vec3 submerged_source_fg;
    vec3 submerged_source_bg;
};
struct ProductWaterSample {
    ivec2 cell;
    vec2 world_xz;
    float water_depth;
    float depth_fade;
    float water_thickness;
    float wave_height;
    float crest;
    float surface_foam;
    bool stable_cutover_paint;
    float shore_dist_cells;
    int break_dir;
    float shore_fade;
    float shore_foam;
    float wake;
    uint wake_source_id;
    float wake_along;
    float break_strength;
    bool break_active;
    int break_stage;
    uint activity_phase;
    vec3 bg;
    bool submerged_source_present;
    uint submerged_source_glyph;
    vec3 submerged_source_fg;
    float submerged_visibility;
    float bed_transmission;
};
struct ProductWaterSourcePaint {
    bool present;
    uint glyph;
    vec3 fg;
    vec3 bg;
};
uint vsb_product_selected_submerged_source_bits;
ProductFiniteWaterCellFacts vsb_product_selected_finite_water_facts;
vec3 water_submerged_bed_color(
    ivec2 cell, float water_depth, float time, bool stable_cutover_paint);
void product_surface_facts_query(
    ivec2 cell, vec2 sample_world_xz,
    out float dist_cells, out int dir_bucket) {}
ProductWaterSourcePaint product_fl4737_submerged_source_paint(
    uint bits, ivec2 water_cell
) {
    return ProductWaterSourcePaint(false, 0u, vec3(0.0), vec3(0.0));
}
vec3 water_submerged_bed_color(
    ivec2 cell, float water_depth, float time, bool stable_cutover_paint) {
    return vec3(float(cell.x), water_depth, time);
}
ProductFiniteWaterCellFacts product_finite_water_cell_facts(
    ivec2 cell,
    vec2 sample_world_xz,
    float water_depth,
    float ambient_time,
    bool stable_cutover_paint
) {
    ProductFiniteWaterCellFacts facts;
    facts.valid = false;
    facts.cell = cell;
    facts.water_depth = water_depth;
    facts.depth_fade = clamp(water_depth / 3.0, 0.0, 1.0);
    facts.water_thickness = water_depth;
    facts.ambient_time = ambient_time;
    facts.stable_cutover_paint = stable_cutover_paint;
    product_surface_facts_query(
        cell, sample_world_xz, facts.shore_dist_cells, facts.break_dir);
    facts.bed = water_submerged_bed_color(
        cell, water_depth, ambient_time, stable_cutover_paint);
    ProductWaterSourcePaint submerged =
        product_fl4737_submerged_source_paint(
            vsb_product_selected_submerged_source_bits, cell);
    facts.submerged_source_present = submerged.present;
    facts.submerged_source_glyph = submerged.glyph;
    facts.submerged_source_fg = submerged.fg;
    facts.submerged_source_bg = submerged.bg;
    facts.valid = true;
    return facts;
}
uint product_water_depth_band(float depth_coordinate) {
    if (depth_coordinate < 0.30) return 0u;
    if (depth_coordinate < WATER_SHALLOW_TRANSITION_END) return 1u;
    return 2u;
}
vec3 product_day_water_body_band(uint band) {
    if (band == 0u) return WATER_SHALLOW_BG;
    if (band == 1u) return WATER_MID_BG;
    return WATER_DEEP_BG;
}
vec3 product_day_water_sky_colour() {
    float sun_height = clamp(product_sun_dir().y, 0.0, 1.0);
    float high_day = smoothstep(0.06, 0.44, sun_height);
    return mix(vec3(0.82, 0.67, 0.59), RECEIPTED_DAY_SKY_COLOR, high_day);
}
float product_day_water_mottle_gain(vec2 world_xz) {
    float mass = stable_value_noise(
        world_xz * 0.045 + vec2(13.7, -8.9), 0x4129A11Du);
    if (mass < 0.38) return 0.90;
    if (mass > 0.66) return 1.10;
    return 1.0;
}
float product_day_water_cloud_colour_amount(vec2 world_xz) {
    if (!baseline_feature_clouds_enabled()) return 0.0;
    float cloud = product_cloud_shadow_projected_world(
        world_xz, WATER_SURFACE_Y);
    float cloud_band = cloud < 0.24 ? 0.0 : (cloud < 0.62 ? 0.42 : 0.78);
    float daylight = clamp(1.0 - product_day_night_factor(), 0.0, 1.0);
    return cloud_band * daylight;
}
ProductWaterSample product_water_sample(
    ProductFiniteWaterCellFacts cell_facts,
    vec2 sample_world_xz
) {
    ProductWaterSample s;
    s.cell = cell_facts.cell;
    s.world_xz = sample_world_xz;
    s.water_depth = cell_facts.water_depth;
    s.depth_fade = cell_facts.depth_fade;
    s.water_thickness = cell_facts.water_thickness;
    s.submerged_source_present = cell_facts.submerged_source_present;
    s.submerged_source_glyph = cell_facts.submerged_source_glyph;
    s.submerged_source_fg = cell_facts.submerged_source_fg;
    s.submerged_visibility = 0.0;
    float ambient_time = cell_facts.ambient_time;
    float wave_freq = 0.72;
    vec2 wave_uv = s.world_xz * wave_freq;
    s.wave_height = product_water_wave_shape(
        wave_uv, product_water_wave_phase());
    s.crest = smoothstep(0.72, 1.0, abs(s.wave_height));
    float surf_n = product_water_gradient_noise(s.world_xz * 0.23);
    s.surface_foam = smoothstep(0.55, 0.85, surf_n);
    s.stable_cutover_paint = cell_facts.stable_cutover_paint;
    s.shore_dist_cells = cell_facts.shore_dist_cells;
    s.break_dir = cell_facts.break_dir;
    float shore_proximity =
        1.0 - clamp(s.shore_dist_cells / 6.0, 0.0, 1.0);
    s.shore_fade = 1.0 - shore_proximity;
    float shore_noise =
        product_water_gradient_noise(s.world_xz * 0.60);
    float shore_band =
        (1.0 - smoothstep(0.65, 2.4, s.shore_dist_cells))
        * smoothstep(0.38, 0.84, shore_noise);
    s.shore_foam =
        clamp(max(shore_proximity * 0.45, shore_band), 0.0, 1.0);
    ProductWakeFact wake_fact = product_wake_fact(s.world_xz);
    s.wake = wake_fact.strength;
    s.wake_source_id = wake_fact.source_id;
    s.wake_along = wake_fact.along;
    int wave_stage = 0;
    s.break_strength =
        product_fl4216_break_strength(s, ambient_time, wave_stage);
    s.break_active =
        product_fl4216_break_active(s, ambient_time, wave_stage);
    s.break_stage = wave_stage;
    s.activity_phase = product_water_activity_phase(s, ambient_time);
    vec3 bed = cell_facts.submerged_source_present
        ? cell_facts.submerged_source_bg : cell_facts.bed;
    uint depth_band = product_water_depth_band(s.depth_fade);
    uint legacy_depth_band = product_water_depth_band(s.water_depth);
    float depth_band_daylight = clamp(
        1.0 - product_day_night_factor(), 0.0, 1.0);
    vec3 water_body = mix(
        product_day_water_body_band(legacy_depth_band),
        product_day_water_body_band(depth_band),
        depth_band_daylight);
    float motion_value = s.wave_height * 0.62
        + (product_water_gradient_noise(s.world_xz * 0.075) - 0.5) * 0.38;
    float motion_gain = motion_value > 0.24 ? 1.045
        : (motion_value < -0.24 ? 0.955 : 1.0);
    float mottle_gain = product_day_water_mottle_gain(s.world_xz);
    water_body = clamp(
        water_body * motion_gain * mottle_gain,
        vec3(0.0), vec3(1.0));
    float bed_visibility_falloff = mix(
        WATER_BED_VISIBILITY_FALLOFF,
        WATER_BED_VISIBILITY_FALLOFF_DAY,
        depth_band_daylight);
    float bed_visibility = clamp(
        1.0 - s.depth_fade * bed_visibility_falloff,
        0.0, 1.0);
    s.bed_transmission = bed_visibility;
    s.submerged_visibility = cell_facts.submerged_source_present
        ? bed_visibility : 0.0;
    s.bg = mix(water_body, bed, bed_visibility);
    return s;
}
bool vsb_product_prepare_water(
    VisibleSurfaceProduct record,
    ivec2 subcell,
    ivec2 subcell_extent,
    inout VsbProductCompileInput input_value
) {
    bool backstop = record.primitive_id == 999u;
    ivec2 semantic_cell;
    if (backstop) {
        input_value.context.payload_from_backstop = true;
        input_value.facts.water_depth = 0.0;
    } else {
        semantic_cell = ivec2(3, 5);
        float selected_average_height = 0.25 * (
            input_value.selected_corner_heights.x
            + input_value.selected_corner_heights.y
            + input_value.selected_corner_heights.z
            + input_value.selected_corner_heights.w);
        input_value.facts.water_depth =
            max(WATER_SURFACE_Y - selected_average_height, 0.0);
        vsb_product_selected_finite_water_facts =
            product_finite_water_cell_facts(
                semantic_cell,
                record.world_position.xz,
                input_value.facts.water_depth,
                product_water_animation_time(),
                true);
        if (!vsb_product_selected_finite_water_facts.valid) {
            vsb_product_prepare_diagnostic = 0x0238u;
            return false;
        }
    }
    return true;
}
void q3_probe_payload_apply_water(
    ProbePayloadContext context,
    ProbePayloadCompileFacts facts,
    inout uint glyph,
    inout vec3 fg,
    inout vec3 bg
) {
    if (context.surface_kind != OWNER_WATER
            || context.payload_from_backstop
            || facts.distant_water_sky_paint
            || !vsb_product_selected_finite_water_facts.valid) return;
    ivec2 water_cell = vsb_product_selected_finite_water_facts.cell;
    vec2 semantic_water_xz =
        params.terrain_params.xy + vec2(water_cell) + vec2(0.5);
    ProductWaterSample water = product_water_sample(
        vsb_product_selected_finite_water_facts,
        semantic_water_xz);
    glyph = water_glyph_candidate(water);
    fg = water_paint_fg(water);
    bg = water_paint_bg(water);
}
ProbeSubcellProductRecord vsb_product_finalize_record(
    ProbeSubcellProductRecord record,
    VsbProductCompileInput input_value,
    ivec2 subcell,
    VsbActorPaintSelection actor,
    out uint secondary_effect_contribution_bits
) {
    float night = product_day_night_factor();
    if (record.r0.y == OWNER_WATER
            && !input_value.context.payload_from_backstop
            && vsb_product_selected_finite_water_facts.valid) {
        ProductWaterSample water_sample = product_water_sample(
            vsb_product_selected_finite_water_facts,
            input_value.context.trace_receiver.xz);
        product_apply_night_water_pair(
            water_sample, night, record.fg.rgb, record.bg.rgb);
        record.fg.a = 1.0;
        record.bg.a = 1.0;
    }
    return record;
}
void main() {
    for (uint slot = 0u; slot < 4u; ++slot) {
        vsb_product_selected_finite_water_facts.valid = false;
        VsbProductCompileInput input_value =
            vsb_product_compile_input(
                visible, subcell, subcell_extent, ray, true);
    }
}
"""


def mutation_witnesses(source: str) -> dict[str, str]:
    finite_block = """\
    if (backstop) {
        input_value.context.payload_from_backstop = true;
        input_value.facts.water_depth = 0.0;
    } else {
        semantic_cell = ivec2(3, 5);
        float selected_average_height = 0.25 * (
            input_value.selected_corner_heights.x
            + input_value.selected_corner_heights.y
            + input_value.selected_corner_heights.z
            + input_value.selected_corner_heights.w);
        input_value.facts.water_depth =
            max(WATER_SURFACE_Y - selected_average_height, 0.0);
        vsb_product_selected_finite_water_facts =
            product_finite_water_cell_facts(
                semantic_cell,
                record.world_position.xz,
                input_value.facts.water_depth,
                product_water_animation_time(),
                true);
        if (!vsb_product_selected_finite_water_facts.valid) {
            vsb_product_prepare_diagnostic = 0x0238u;
            return false;
        }
    }"""
    backstop_admitted = """\
    vsb_product_selected_finite_water_facts =
        product_finite_water_cell_facts(
            semantic_cell,
            input_value.facts.water_depth,
            product_water_animation_time(),
            true);
    if (backstop) {
        input_value.context.payload_from_backstop = true;
        input_value.facts.water_depth = 0.0;
    } else {
        semantic_cell = ivec2(3, 5);
        float selected_average_height = 0.25 * (
            input_value.selected_corner_heights.x
            + input_value.selected_corner_heights.y
            + input_value.selected_corner_heights.z
            + input_value.selected_corner_heights.w);
        input_value.facts.water_depth =
            max(WATER_SURFACE_Y - selected_average_height, 0.0);
    }"""
    mutants = {
        "second_shore_scan_resurrected": replace_in_function(
            source,
            "vsb_product_finalize_record",
            "        ProductWaterSample water_sample = product_water_sample(",
            "        float restored_shore;\n"
            "        int restored_direction;\n"
            "        product_surface_facts_query(\n"
            "            input_value.context.material_cell,\n"
            "            input_value.context.trace_receiver.xz,\n"
            "            restored_shore, restored_direction);\n"
            "        ProductWaterSample water_sample = product_water_sample(",
        ),
        "center_xz_substituted_for_trace_receiver": replace_in_function(
            source,
            "vsb_product_finalize_record",
            "            input_value.context.trace_receiver.xz);",
            "            params.terrain_params.xy\n"
            "                + vec2(input_value.context.material_cell)\n"
            "                + vec2(0.5));",
        ),
        "carried_shore_distance_direction_drift": replace_in_function(
            source,
            "product_water_sample",
            "    s.shore_dist_cells = cell_facts.shore_dist_cells;\n"
            "    s.break_dir = cell_facts.break_dir;",
            "    s.shore_dist_cells = "
            "cell_facts.shore_dist_cells + 1.0;\n"
            "    s.break_dir = -cell_facts.break_dir;",
        ),
        "carried_bed_and_retained_source_omitted": replace_in_function(
            source,
            "product_water_sample",
            "    vec3 bed = cell_facts.submerged_source_present\n"
            "        ? cell_facts.submerged_source_bg : cell_facts.bed;",
            "    vec3 bed = WATER_DEEP_BG;",
        ),
        # FL-4737 F8 revision 2 (AD2 night guard): dropping the retained source
        # from the bed without a daylight gate moves full-night bytes.
        "retained_source_removed_from_bed_ungated": replace_in_function(
            source,
            "product_water_sample",
            "    vec3 bed = cell_facts.submerged_source_present\n"
            "        ? cell_facts.submerged_source_bg : cell_facts.bed;",
            "    vec3 bed = cell_facts.bed;",
        ),
        "hard_zero_deep_bed_restored": replace_in_function(
            source,
            "product_water_sample",
            "    float bed_visibility = clamp(\n"
            "        1.0 - s.depth_fade * bed_visibility_falloff,\n"
            "        0.0, 1.0);",
            "    float bed_visibility = (1.0 - smoothstep(\n"
            "        0.08, WATER_SHALLOW_TRANSITION_END, s.water_depth))\n"
            "        * 0.68;",
        ),
        # FL-4737 F8: the two-slope blend is the day/night transmission law. A
        # mutant that collapses it back to the single night slope restores the
        # 0.18 deep floor the operator rejected on 2026-08-13.
        "day_transmission_slope_collapsed_to_night": replace_in_function(
            source,
            "product_water_sample",
            "    float bed_visibility_falloff = mix(\n"
            "        WATER_BED_VISIBILITY_FALLOFF,\n"
            "        WATER_BED_VISIBILITY_FALLOFF_DAY,\n"
            "        depth_band_daylight);",
            "    float bed_visibility_falloff = "
            "WATER_BED_VISIBILITY_FALLOFF;",
        ),
        # The transmission the fog stage reads must be the transmission the
        # column actually used, not a fresh constant.
        "carried_bed_transmission_detached": replace_in_function(
            source,
            "product_water_sample",
            "    s.bed_transmission = bed_visibility;",
            "    s.bed_transmission = 0.0;",
        ),
        "day_band_reverted_to_raw_depth": replace_in_function(
            source,
            "product_water_sample",
            "    uint depth_band = product_water_depth_band(s.depth_fade);",
            "    uint depth_band = product_water_depth_band(s.water_depth);",
        ),
        "legacy_night_band_replaced_by_normalized_depth": replace_in_function(
            source,
            "product_water_sample",
            "    uint legacy_depth_band = "
            "product_water_depth_band(s.water_depth);",
            "    uint legacy_depth_band = "
            "product_water_depth_band(s.depth_fade);",
        ),
        "day_night_band_blend_hard_switched": replace_in_function(
            source,
            "product_water_sample",
            "    float depth_band_daylight = clamp(\n"
            "        1.0 - product_day_night_factor(), 0.0, 1.0);",
            "    float depth_band_daylight = step(\n"
            "        product_day_night_factor(), 0.5);",
        ),
        "validity_stale_across_slots": replace_in_function(
            source,
            "main",
            "        vsb_product_selected_finite_water_facts.valid = false;\n",
            "",
        ),
        "backstop_admitted_to_cell_materializer": replace_in_function(
            source,
            "vsb_product_prepare_water",
            finite_block,
            backstop_admitted,
        ),
        "stable_cutover_drifted_false": replace_in_function(
            source,
            "vsb_product_prepare_water",
            "                true);",
            "                false);",
        ),
        "column_depth_arithmetic_reordered": replace_in_function(
            source,
            "vsb_product_prepare_water",
            "        float selected_average_height = 0.25 * (\n"
            "            input_value.selected_corner_heights.x\n"
            "            + input_value.selected_corner_heights.y\n"
            "            + input_value.selected_corner_heights.z\n"
            "            + input_value.selected_corner_heights.w);",
            "        float selected_average_height = (\n"
            "            input_value.selected_corner_heights.x\n"
            "            + input_value.selected_corner_heights.y\n"
            "            + input_value.selected_corner_heights.z\n"
            "            + input_value.selected_corner_heights.w) * 0.25;",
        ),
    }
    reds: dict[str, str] = {}
    for name, mutant in mutants.items():
        try:
            assert_finite_water_single_source(mutant)
        except AssertionError as error:
            reds[name] = str(error) or "AssertionError"
            continue
        raise AssertionError(f"mutation stayed green: {name}")
    return reds


def assert_night_water_pair_single_source(source: str) -> None:
    """Pin one FG/BG author and one moon-reflection closure per water sample."""
    whole = compact(source)
    pair = compact(function_body(source, "product_apply_night_water_pair"))
    mask = compact(function_body(source, "night_moon_reflection_mask"))
    glitter = compact(function_body(source, "night_moon_water_glitter"))

    assert "night_water_paint(" not in whole
    expected_pair_refs = 3 if "boolanalytic_backstop;" in whole else 2
    assert whole.count("product_apply_night_water_pair(") == expected_pair_refs
    assert whole.count("night_moon_reflection_mask(") == 3
    assert whole.count("night_moon_water_glitter(") == 2
    assert (
        "floatnight_moon_reflection_mask("
        "ProductWaterSamples,floatwater_night_factor,floatmoon_scale)" in whole
    )
    assert (
        "floatnight_moon_water_glitter("
        "ProductWaterSamples,floattime,floatreflection_mask)" in whole
    )
    assert (
        "voidproduct_apply_night_water_pair("
        "ProductWaterSamples,floatwater_nf,"
        "inoutvec3foreground,inoutvec3background)" in whole
    )
    assert "product_day_night_factor(" not in mask
    assert "night_moon_reflection_width_scale(" not in mask
    assert "night_moon_reflection_mask(" not in glitter

    pair_law = (
        "if(!baseline_feature_night_enabled()&&water_nf<=0.0001)return;",
        "vec3day_foreground=foreground;",
        "vec3night_foreground=",
        "floatmoon_scale=night_moon_reflection_width_scale();",
        "floatmoon_envelope=night_moon_reflection_mask("
        "s,water_nf,moon_scale);",
        "floatmoon_glitter=night_moon_water_glitter("
        "s,product_water_animation_time(),moon_envelope);",
        "night_foreground=mix(",
        "floatordinary_activity=",
        "floatshoreline_break_activity=",
        "vec3ordinary_wave_blue=",
        "ordinary_activity*0.006);",
        "shoreline_break_activity*0.78);",
        "vec3day_background=background;",
        "vec3night_background=",
        "floatwater_night_blend=clamp(water_nf,0.0,1.0);",
        "foreground=mix(",
        "background=mix(",
    )
    cursor = 0
    for token in pair_law:
        position = pair.find(token, cursor)
        assert position >= 0, f"night-water pair law missing or reordered: {token}"
        cursor = position + len(token)
    assert pair.count("product_day_night_factor(") == 0
    assert pair.count("night_moon_reflection_width_scale(") == 1
    assert pair.count("night_moon_reflection_mask(") == 1
    assert pair.count("night_moon_water_glitter(") == 1
    assert pair.count("night_foreground=") == 4
    assert pair.count("night_background=") == 1
    assert "VSB_PROJECTION_" not in pair
    assert "view_identity.z" not in pair
    assert "surface_foam" not in pair[pair.find("floatshoreline_break_activity="):]
    assert "night_background=mix(" not in pair

    # Pin the selected sparse foreground-only night contract. Open-water foam
    # may not author bright patches, and moon glitter may not paint paper.
    exact_foreground = (
        "vec3moon_tint=vec3(0.62,0.74,1.00);"
        "vec3day_foreground=foreground;"
        "floatforeground_lum=dot("
        "day_foreground,vec3(0.2126,0.7152,0.0722));"
        "vec3night_foreground=clamp("
        "mix(day_foreground,vec3(foreground_lum)*moon_tint,0.45)*0.05"
        "+vec3(0.003,0.006,0.018),vec3(0.0),vec3(1.0));"
    )
    exact_background = (
        "floatbackground_lum=dot("
        "day_background,vec3(0.2126,0.7152,0.0722));"
        "vec3night_background=clamp("
        "mix(day_background,vec3(background_lum)*moon_tint,0.45)*0.24"
        "+vec3(0.012,0.020,0.046),vec3(0.0),vec3(1.0));"
    )
    exact_final_mix = (
        "foreground=mix("
        "day_foreground,night_foreground,water_night_blend);"
        "background=mix("
        "day_background,night_background,water_night_blend);"
    )
    assert exact_foreground in pair
    assert (
        "floatmoon_scale=night_moon_reflection_width_scale();"
        "floatmoon_envelope=night_moon_reflection_mask("
        "s,water_nf,moon_scale);"
        "floatmoon_glitter=night_moon_water_glitter("
        "s,product_water_animation_time(),moon_envelope);" in pair
    )
    assert exact_background in pair
    assert exact_final_mix in pair

    finalizer = compact(
        function_body(source, "vsb_product_finalize_record")
    )
    assert (
        "floatnight=product_day_night_factor();"
        "if(night>0.0001&&!bonfire_emissive){" in finalizer
    )
    assert (
        "product_apply_night_water_pair("
        "water_sample,night,record.fg.rgb,record.bg.rgb);" in finalizer
    )


def day_water_pair_errors(source: str) -> tuple[str, ...]:
    """Return named deviations from the single day-water FG/BG author."""
    errors: list[str] = []
    whole = compact(source)
    retired_helpers = (
        "water_paint_fg(",
        "water_paint_bg(",
        "product_apply_water_fog(",
        "product_water_fog_color(",
    )
    for helper in retired_helpers:
        if helper in whole:
            errors.append(f"retired split day-water helper survived: {helper}")

    if whole.count("product_apply_day_water_pair(") != 2:
        errors.append(
            "day-water pair must have one definition plus one q3 call"
        )
    try:
        pair = compact(
            function_body(source, "product_apply_day_water_pair")
        )
    except AssertionError:
        errors.append("typed day-water pair helper is missing")
        return tuple(errors)
    water = compact(function_body(source, "q3_probe_payload_apply_water"))
    finalizer = compact(function_body(source, "vsb_product_finalize_record"))

    signature = (
        "voidproduct_apply_day_water_pair("
        "ProductWaterSamples,uintglyph,"
        "inoutvec3foreground,inoutvec3background)"
    )
    if signature not in whole:
        errors.append("day-water pair signature drifted")
    if water.count("water_glyph_candidate(water)") != 1:
        errors.append("day-water q3 glyph candidate count drifted")
    glyph_call = water.find("glyph=water_glyph_candidate(water);")
    pair_call = water.find("product_apply_day_water_pair(water,glyph,fg,bg);")
    if glyph_call < 0 or pair_call <= glyph_call:
        errors.append("day-water glyph-to-pair call order drifted")
    # FL-4737 F8 (2026-08-13): four reads, not three. The fourth is the single
    # `water_daylight` fact the two F8 stages share (the fog transmission
    # exemption and the direct submerged-source composite). It is materialized
    # once next to fog_target so neither stage recomputes the day/night arc.
    if pair.count("product_day_night_factor()") != 4:
        errors.append("day-water fog target night read count drifted")
    if pair.count("floatwater_daylight=") != 1:
        errors.append("day-water daylight fact materialization count drifted")
    if pair.count("vec3fog_target=") != 1:
        errors.append("day-water fog target materialization count drifted")

    ordered_day_law = (
        "foreground=product_water_activity_ink(s.bg,s,glyph);",
        "background=s.bg;",
        "floattwilight_engagement=",
        "celestial_water_reflection_envelope(",
        "celestial_water_sparse_glitter(",
        "foreground=mix(",
        "if(s.break_strength>0.0){",
        "vec3fog_target=mix(",
        "floatforeground_fog_amount=",
        "floatbackground_fog_amount=",
        "product_fl4216_apply_reflection_pair(",
    )
    cursor = 0
    for token in ordered_day_law:
        position = pair.find(token, cursor)
        if position < 0:
            errors.append("day-water FG/BG equations or evaluation order drifted")
            break
        cursor = position + len(token)
    exact_day_fragments = (
        # FL-4216 F11 synthesis 2026-08-18: an admitted break carries an ink
        # floor (0.58 slope) so a short mesh collar reads as foam, while an
        # inactive band keeps the historical 0.42 fade.
        "floatbreak_ink_mix=s.break_strength*(s.break_active?0.58:0.42);",
        "s.stable_cutover_paint?break_ink_mix:0.62);",
        "floatbreak_mix=s.stable_cutover_paint?"
        "s.break_strength*0.10:0.16",
        "floatforeground_fog_amount=clamp(s.depth_fade,0.0,1.0)"
        "*clamp(WATER_FOG_FG_PEAK,0.0,1.0);",
        # FL-4737 F8 (2026-08-13): the background haze is the water COLUMN's
        # own scattering, so it applies to the fraction the column did NOT
        # transmit. Without the third factor the transmitted bed was attenuated
        # twice (once by bed_visibility, again here) and deep submerged terrain
        # reached the pixel at 0.18 * 0.54 = 0.097 -- the operator's "uniform
        # opaque fill". water_daylight keeps full-night amounts unchanged.
        "floatbackground_fog_amount=clamp(s.depth_fade,0.0,1.0)"
        "*clamp(WATER_FOG_BG_PEAK,0.0,1.0)"
        "*(1.0-clamp(s.bed_transmission,0.0,1.0)*water_daylight);",
        # FL-4737 F8 revision 2: the direct submerged source is composited ONCE,
        # at the TOP of the pair, and the ONE surface owner that used to erase it
        # -- the FL-4216 mirror mix -- yields at the call site whenever that
        # terrain/mesh source owns the transmitted column.
        "if(s.submerged_source_present){foreground=mix(foreground,"
        "s.submerged_source_fg,s.submerged_visibility);}",
        "1.0-product_day_night_factor(),shoreline_mirror_admission,",
        "floatshoreline_mirror_admission=smoothstep(0.75,2.26,"
        "s.shore_dist_cells);",
    )
    if any(fragment not in pair for fragment in exact_day_fragments):
        errors.append("day-water FG/BG equations or evaluation order drifted")
    # The direct-source composite is the FIRST stage and contains the only read
    # of the transmitted share. The mirror gate uses source presence, not that
    # share, so it cannot preserve part of the static reflected-paper band or
    # cap D3/F11 by column thickness.
    if pair.find("if(s.submerged_source_present){") > pair.find(
        "product_fl4216_apply_reflection_pair("
    ):
        errors.append("direct submerged source no longer precedes reflection")
    if pair.count("s.submerged_visibility") != 1:
        errors.append("transmitted-source share read count drifted")

    if "product_apply_day_water_pair(" in finalizer:
        errors.append("day-water pair moved into the night finalizer")
    if "boolanalytic_backstop;" in whole:
        if water.count("product_apply_night_water_pair(") != 1 or (
            "if(context.payload_from_backstop){"
            "floatnight=product_day_night_factor();" not in water
        ):
            errors.append("analytic horizon night-water gate drifted")
    elif "product_apply_night_water_pair(" in water:
        errors.append("night-water pair moved into q3 day paint")
    day_call = water.find(
        "product_apply_day_water_pair(water,glyph,fg,bg);"
    )
    night_call = finalizer.find(
        "product_apply_night_water_pair("
        "water_sample,night,record.fg.rgb,record.bg.rgb);"
    )
    if day_call < 0 or night_call < 0:
        errors.append("day-water pair no longer precedes night finalization")
    return tuple(errors)


def assert_day_water_pair_single_source(source: str) -> None:
    errors = day_water_pair_errors(source)
    assert not errors, "; ".join(errors)


def compile_product_shader(source: str) -> tuple[bool, str]:
    """Compile one damaging mutant before accepting its structural rejection."""
    compiler = shutil.which("glslangValidator")
    assert compiler is not None, "glslangValidator is required"
    lines = source.splitlines(keepends=True)
    if lines and lines[0].startswith("#["):
        source = "".join(lines[1:])
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".comp",
        encoding="utf-8",
        delete=False,
    ) as handle:
        handle.write(source)
        path = Path(handle.name)
    try:
        result = subprocess.run(
            [
                compiler,
                "--target-env",
                "vulkan1.1",
                "-S",
                "comp",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
    finally:
        path.unlink(missing_ok=True)
    diagnostic = "\n".join(
        text for text in (result.stdout, result.stderr) if text
    )
    return result.returncode == 0, diagnostic


def night_water_pair_mutants(source: str) -> dict[str, str]:
    """Return compile-valid damage shaped like each deleted duplicate owner."""
    restored_helper = """
vec3 fl4431_restored_night_water_paint(
    vec3 color, ProductWaterSample water_fact,
    float water_nf, bool foreground_lane
) {
    vec3 other = color;
    product_apply_night_water_pair(water_fact, water_nf, color, other);
    return foreground_lane ? color : other;
}
"""
    dual_call = replace_in_function(
        source,
        "vsb_product_finalize_record",
        "\t\t\tproduct_apply_night_water_pair(\n"
        "\t\t\t\twater_sample, night, record.fg.rgb, record.bg.rgb);",
        "\t\t\trecord.fg.rgb = fl4431_restored_night_water_paint(\n"
        "\t\t\t\trecord.fg.rgb, water_sample, night, true);\n"
        "\t\t\trecord.bg.rgb = fl4431_restored_night_water_paint(\n"
        "\t\t\t\trecord.bg.rgb, water_sample, night, false);",
    )
    dual_call = dual_call.replace(
        "ProbeSubcellProductRecord vsb_product_finalize_record(",
        restored_helper
        + "\nProbeSubcellProductRecord vsb_product_finalize_record(",
        1,
    )
    mutants = {
        "dual_bool_owner_restored": dual_call,
        "foreground_glitter_contamination": replace_in_function(
            source,
            "product_apply_night_water_pair",
            "\tfloat ordinary_activity = clamp(",
            "\tnight_foreground = mix(\n"
            "\t\tnight_foreground, vec3(1.0), moon_glitter * 0.10);\n"
            "\tfloat ordinary_activity = clamp(",
        ),
        "background_mask_recomputed": replace_in_function(
            source,
            "product_apply_night_water_pair",
            "s, water_nf, moon_scale);",
            "s, water_nf,\n"
            "\t\tnight_moon_reflection_width_scale());",
        ),
        "moon_width_recomputed": replace_in_function(
            source,
            "product_apply_night_water_pair",
            "s, water_nf, moon_scale);",
            "s, water_nf, night_moon_reflection_width_scale());",
        ),
        "second_day_night_read": replace_in_function(
            source,
            "product_apply_night_water_pair",
            "\tif (!baseline_feature_night_enabled()"
            " && water_nf <= 0.0001) return;",
            "\twater_nf = product_day_night_factor();\n"
            "\tif (!baseline_feature_night_enabled()"
            " && water_nf <= 0.0001) return;",
        ),
        "mask_not_forwarded_to_glitter": replace_in_function(
            source,
            "product_apply_night_water_pair",
            "s, product_water_animation_time(), moon_envelope);",
            "s, product_water_animation_time(),\n"
            "\t\tnight_moon_reflection_mask(s, water_nf, moon_scale));",
        ),
        "projection_fence_inserted": replace_in_function(
            source,
            "product_apply_night_water_pair",
            "\tif (!baseline_feature_night_enabled()"
            " && water_nf <= 0.0001) return;",
            "\tif (product_vsb_frame.view_identity.z\n"
            "\t\t\t== VSB_PROJECTION_PERSPECTIVE) return;\n"
            "\tif (!baseline_feature_night_enabled()"
            " && water_nf <= 0.0001) return;",
        ),
        "day_gate_widened": replace_in_function(
            source,
            "product_apply_night_water_pair",
            "water_nf <= 0.0001",
            "water_nf <= 0.001",
        ),
        "foreground_rec709_drift": replace_in_function(
            source,
            "product_apply_night_water_pair",
            "float foreground_lum = dot(\n"
            "\t\tday_foreground, vec3(0.2126, 0.7152, 0.0722));",
            "float foreground_lum = dot(\n"
            "\t\tday_foreground, vec3(0.2990, 0.5870, 0.1140));",
        ),
        "foreground_night_scale_drift": replace_in_function(
            source,
            "product_apply_night_water_pair",
            "\t\t\t0.45) * 0.05",
            "\t\t\t0.45) * 0.80",
        ),
        "background_night_scale_drift": replace_in_function(
            source,
            "product_apply_night_water_pair",
            "\t\t\t0.45) * 0.24",
            "\t\t\t0.45) * 0.42",
        ),
        "foreground_night_floor_drift": replace_in_function(
            source,
            "product_apply_night_water_pair",
            "\t\t\t0.45) * 0.05\n"
            "\t\t+ vec3(0.003, 0.006, 0.018), vec3(0.0), vec3(1.0));",
            "\t\t\t0.45) * 0.05\n"
            "\t\t+ vec3(0.020, 0.030, 0.060), vec3(0.0), vec3(1.0));",
        ),
        "background_night_floor_drift": replace_in_function(
            source,
            "product_apply_night_water_pair",
            "\t\t\t0.45) * 0.24\n"
            "\t\t+ vec3(0.012, 0.020, 0.046), vec3(0.0), vec3(1.0));",
            "\t\t\t0.45) * 0.24\n"
            "\t\t+ vec3(0.020, 0.030, 0.060), vec3(0.0), vec3(1.0));",
        ),
        "ordinary_wave_weight_drift": replace_in_function(
            source,
            "product_apply_night_water_pair",
            "ordinary_activity * 0.006);",
            "ordinary_activity * 0.18);",
        ),
        "shoreline_wave_weight_drift": replace_in_function(
            source,
            "product_apply_night_water_pair",
            "shoreline_break_activity * 0.78);",
            "shoreline_break_activity * 0.50);",
        ),
        "foreground_final_mix_reversed": replace_in_function(
            source,
            "product_apply_night_water_pair",
            "\t\tday_foreground, night_foreground, water_night_blend);",
            "\t\tnight_foreground, day_foreground, water_night_blend);",
        ),
        "background_final_mix_reversed": replace_in_function(
            source,
            "product_apply_night_water_pair",
            "\t\tday_background, night_background, water_night_blend);",
            "\t\tnight_background, day_background, water_night_blend);",
        ),
        "surface_foam_patch_restored": replace_in_function(
            source,
            "product_apply_night_water_pair",
            "max(s.break_strength, s.break_active ? 1.0 : 0.0)",
            "max(s.surface_foam, s.break_active ? 1.0 : 0.0)",
        ),
        "moon_tint_drift": replace_in_function(
            source,
            "product_apply_night_water_pair",
            "vec3 moon_tint = vec3(0.62, 0.74, 1.00);",
            "vec3 moon_tint = vec3(1.00, 0.20, 0.20);",
        ),
    }
    return mutants


def assert_night_water_pair_mutants(source: str) -> None:
    mutants = night_water_pair_mutants(source)
    assert len(mutants) == 19
    for name, mutant in mutants.items():
        assert mutant != source, f"mutant did not alter source: {name}"
        compiled, diagnostic = compile_product_shader(mutant)
        assert compiled, f"mutant was not compile-valid: {name}\n{diagnostic}"
        try:
            assert_night_water_pair_single_source(mutant)
        except AssertionError:
            continue
        raise AssertionError(f"night-water damaging mutant stayed green: {name}")


def _swap_pair_tokens(
    source: str,
    first: str,
    second: str,
) -> str:
    """Swap two unique tokens inside the day pair without a partial collision."""
    temporary = "FL4431_DAY_WATER_PAIR_SWAP_SENTINEL"
    swapped = replace_in_function(
        source,
        "product_apply_day_water_pair",
        first,
        temporary,
    )
    swapped = replace_in_function(
        swapped,
        "product_apply_day_water_pair",
        second,
        first,
    )
    return replace_in_function(
        swapped,
        "product_apply_day_water_pair",
        temporary,
        second,
    )


def day_water_pair_mutants(source: str) -> dict[str, tuple[str, str]]:
    """Build compile-valid defects and the exact contract reason each must hit."""
    duplicate_fog_target = replace_in_function(
        source,
        "product_apply_day_water_pair",
        "\tvec3 fog_target = mix(\n"
        "\t\tWATER_FOG_COLOR,\n"
        "\t\tWATER_FOG_COLOR_NIGHT,\n"
        "\t\tproduct_day_night_factor());",
        "\tvec3 fog_target = mix(\n"
        "\t\tWATER_FOG_COLOR,\n"
        "\t\tWATER_FOG_COLOR_NIGHT,\n"
        "\t\tproduct_day_night_factor());\n"
        "\tvec3 duplicate_fog_target = mix(\n"
        "\t\tWATER_FOG_COLOR,\n"
        "\t\tWATER_FOG_COLOR_NIGHT,\n"
        "\t\tproduct_day_night_factor());\n"
        "\tfog_target = mix(fog_target, duplicate_fog_target, 0.5);",
    )
    old_helpers = """
vec3 water_paint_fg(ProductWaterSample water) {
    return water.bg;
}
vec3 water_paint_bg(ProductWaterSample water) {
    return water.bg;
}
"""
    resurrected = source.replace(
        "void q3_probe_payload_apply_water(",
        old_helpers + "\nvoid q3_probe_payload_apply_water(",
        1,
    )
    resurrected = replace_in_function(
        resurrected,
        "q3_probe_payload_apply_water",
        "\tproduct_apply_day_water_pair(water, glyph, fg, bg);",
        "\tfg = water_paint_fg(water);\n"
        "\tbg = water_paint_bg(water);",
    )
    return {
        "second_glyph_candidate": (
            replace_in_function(
                source,
                "q3_probe_payload_apply_water",
                "\tglyph = water_glyph_candidate(water);",
                "\tglyph = water_glyph_candidate(water);\n"
                "\tglyph = water_glyph_candidate(water);",
            ),
            "day-water q3 glyph candidate count drifted",
        ),
        "duplicate_fog_target_night_read": (
            duplicate_fog_target,
            "day-water fog target night read count drifted",
        ),
        "foreground_background_fog_peaks_swapped": (
            _swap_pair_tokens(
                source,
                "WATER_FOG_FG_PEAK",
                "WATER_FOG_BG_PEAK",
            ),
            "day-water FG/BG equations or evaluation order drifted",
        ),
        "foreground_break_coefficient_drift": (
            replace_in_function(
                source,
                "product_apply_day_water_pair",
                "s.break_strength * (s.break_active ? 0.58 : 0.42)",
                "s.break_strength * (s.break_active ? 0.58 : 0.41)",
            ),
            "day-water FG/BG equations or evaluation order drifted",
        ),
        "input_background_substituted": (
            replace_in_function(
                source,
                "product_apply_day_water_pair",
                "\tbackground = s.bg;",
                "\tbackground = background;",
            ),
            "day-water FG/BG equations or evaluation order drifted",
        ),
        "old_split_helpers_resurrected": (
            resurrected,
            "retired split day-water helper survived: water_paint_fg(",
        ),
        "day_pair_call_removed": (
            replace_in_function(
                source,
                "q3_probe_payload_apply_water",
                "\tproduct_apply_day_water_pair(water, glyph, fg, bg);\n",
                "",
            ),
            "day-water pair must have one definition plus one q3 call",
        ),
    }


def assert_day_water_pair_mutants(source: str) -> None:
    mutants = day_water_pair_mutants(source)
    assert len(mutants) == 7
    for name, (mutant, expected_error) in mutants.items():
        assert mutant != source, f"mutant did not alter source: {name}"
        compiled, diagnostic = compile_product_shader(mutant)
        assert compiled, f"mutant was not compile-valid: {name}\n{diagnostic}"
        errors = day_water_pair_errors(mutant)
        assert expected_error in errors, (
            f"day-water mutant failed for wrong reason: {name}; "
            f"expected={expected_error!r}; actual={errors!r}"
        )


def main() -> int:
    assert_off_center_receiver_witness()
    assert_terrain_bed_transmission_endpoints()
    assert_finite_water_single_source(REFERENCE_SOURCE)
    reds = mutation_witnesses(REFERENCE_SOURCE)
    print(
        "FL4431_PRODUCT_FINITE_WATER_REFERENCE_PASS "
        "shore_bed_materializations=2->1 continuous_samples=2 "
        f"mutants={','.join(reds)}",
        flush=True,
    )
    source = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    assert_finite_water_single_source(source)
    assert_day_water_pair_single_source(source)
    assert_day_water_pair_mutants(source)
    assert_night_water_pair_single_source(source)
    assert_night_water_pair_mutants(source)
    print(
        "FL4431_PRODUCT_FINITE_WATER_SINGLE_SOURCE_PASS "
        "shore_bed_materializations=2->1 continuous_samples=2 "
        f"mutants={','.join(reds)} day_pair_mutants=7 "
        "night_pair_mutants=19"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
