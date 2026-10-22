#!/usr/bin/env python3
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", _without_comments(source))


def _function(source: str, name: str) -> str:
    match = re.search(
        rf"\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{",
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


def _replace_in_function(
    source: str,
    name: str,
    old: str,
    new: str,
) -> str:
    function = _function(source, name)
    assert function.count(old) == 1, (name, old, function.count(old))
    mutated = function.replace(old, new, 1)
    assert source.count(function) == 1, name
    return source.replace(function, mutated, 1)


def _assert_unified_foliage_cycle_contract(src: str) -> None:
    compact = _compact(src)
    ticks = _compact(_function(src, "foliage_clump_cycle_ticks"))
    phase = _compact(_function(src, "foliage_static_cycle_phase_from_ticks"))
    saved = _compact(_function(src, "foliage_saved_static_cycle_glyph"))
    selector = _compact(_function(src, "foliage_glyph_for_coverage"))

    # One quantized clock and one world-clump phase owner feed both the
    # three-member and six-member saved families. Projection cannot alter the
    # selected family, phase, or authored morphology.
    assert ticks == (
        "foliage_clump_cycle_ticks(){"
        "returnuint(floor(params.feature_params.x*5.0));}"
    )
    assert "uintseed_phase=clump_id%family_size;" in phase
    assert saved.count("foliage_clump_cycle_ticks()") == 1
    assert saved.count("foliage_static_cycle_phase_from_ticks(") == 2
    assert "if(!c.present||c.role==FOLIAGE_ROLE_FLOWER)returnGLYPH_ID_NONE;" in saved
    assert "c.blade_id*83492791u" in saved
    assert ")%5u;" in saved

    # RQ-184's five reviewed same-cell glyph families remain explicit. None is
    # a camera-facing particle, a screen-space branch, or a replacement for
    # authored flower morphology.
    for family in (
        "uintthai[3]=uint[3](1842u,1843u,1844u);",
        "uintmongolian[3]=uint[3](1845u,1846u,1847u);",
        "uintarabic6[6]=uint[6](894u,1734u,1735u,1736u,1737u,1738u);",
        "uintarabic3[3]=uint[3](930u,1833u,1834u);",
        "uintarabic_ramp[3]=uint[3](1848u,1849u,936u);",
    ):
        assert family in saved

    assert selector == (
        "foliage_glyph_for_coverage(FoliageCoveragec){"
        "intbase_glyph=foliage_base_glyph_for_coverage(c);"
        "intcycle_glyph=foliage_saved_static_cycle_glyph(c);"
        "returncycle_glyph!=GLYPH_ID_NONE?cycle_glyph:base_glyph;}"
    )
    assert compact.count("intfoliage_saved_static_cycle_glyph(FoliageCoveragec)") == 1
    assert compact.count("intfoliage_glyph_for_coverage(FoliageCoveragec)") == 1
    for function in (ticks, phase, saved, selector):
        for forbidden in (
            "VSB_PROJECTION_",
            "view_identity",
            "camera",
            "screen",
        ):
            assert forbidden not in function


def test_foliage_flower_admission_matches_june20_product_grammar() -> None:
    src = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    assert "const float FOLIAGE_FLOWER_FREQUENCY     = 0.72;" in src
    assert "if (slot_role == FOLIAGE_ROLE_FLOWER || seed1 < FOLIAGE_FLOWER_FREQUENCY * 0.24) variant = FOLIAGE_VARIANT_FLOWER;" in src
    assert "if (slot_role == FOLIAGE_ROLE_FLOWER) variant = FOLIAGE_VARIANT_FLOWER;" not in src
    assert "void compute_product_foliage_fact(" in src
    assert "foliage_glyph = uint(glyph);" in src


def _assert_g45_bloom_contract(src: str) -> None:
    compact = _compact(src)
    scatter = _compact(_function(src, "foliage_scatter_accept_anchor"))
    blade = _compact(_function(src, "foliage_blade_from_anchor"))
    compute = _compact(_function(src, "compute_product_foliage_fact"))
    finish = _compact(_function(src, "q3_probe_payload_finish_foliage"))

    assert "constintFOLIAGE_SLOTS_PER_GRASS_CELL=8;" in compact
    assert "constintFOLIAGE_CLUMP_SLOT_BLOOM=6;" in compact
    assert (
        "floatslot_budget=(slot==FOLIAGE_CLUMP_SLOT_BLOOM)?"
        "density*0.90:density;" in scatter
    )
    assert scatter.count("FOLIAGE_CLUMP_SLOT_BLOOM") == 1
    assert (
        "elseif(slot==FOLIAGE_CLUMP_SLOT_BLOOM)"
        "slot_role=FOLIAGE_ROLE_FLOWER;" in blade
    )
    assert blade.count("slot_role=FOLIAGE_ROLE_FLOWER;") == 1
    role_slice = blade[
        blade.index("intslot_role=") : blade.index("intvariant=")
    ]
    for forbidden in (
        "VSB_PROJECTION_",
        "view_identity",
        "camera",
        "feature_params",
        "quantized_time",
    ):
        assert forbidden not in role_slice

    # Flower-looking atlas variation may appear on another blade, but it must
    # not mutate that blade's authored role into a blossom after admission.
    assert (
        "if(slot_role==FOLIAGE_ROLE_FLOWER||"
        "seed1<FOLIAGE_FLOWER_FREQUENCY*0.24)"
        "variant=FOLIAGE_VARIANT_FLOWER;" in blade
    )
    assert "cov.role=FOLIAGE_ROLE_FLOWER" not in compact
    assert "foliage_apply_ortho_winner_flower_promotion" not in compact
    assert "FOLIAGE_ORTHO_WINNER_FLOWER" not in compact

    # The two product routes may strengthen a genuine blossom after it wins;
    # neither may promote a different role, inspect projection, or use time.
    for route in (compute, finish):
        assert "if(cov.role==FOLIAGE_ROLE_FLOWER){" in route
        assert "cov.coverage=max(cov.coverage,0.46);" in route
        assert "cov.density=FOLIAGE_DENSITY_MEDIUM;" in route
        for forbidden in (
            "VSB_PROJECTION_",
            "view_identity",
            "camera",
            "feature_params.x",
            "quantized_time",
        ):
            assert forbidden not in route


def test_only_the_genuine_world_bloom_slot_authors_a_terrain_flower_role() -> None:
    _assert_g45_bloom_contract(_expand_includes(PRODUCT.read_text(encoding="utf-8")))


def test_both_projections_keep_authored_flowers_and_one_saved_cycle_owner() -> None:
    """Flowers retain authored morphology in both projection registers.

    Background for a reader who has not seen this lane before: every foliage
    cell on screen is one character. Which character it draws is chosen by
    `foliage_base_glyph_for_coverage()`, which holds hand-authored glyph
    numbers -- little drawn blossom shapes that live in the extended glyph
    atlas -- for each foliage role.

    On 2026-07-31 an override called `foliage_ortho_flower_patch_glyph()` was
    added that ignored that authored table for orthographic flowers and
    returned three plain typewriter characters instead: 0x2A '*', 0x2B '+' and
    0x23 '#'. That is the exact defect this FL exists to remove -- a meadow
    made of punctuation rather than of drawn shapes -- so the override was
    deleted. This test is the tripwire that stops it coming back.

    It asserts two things:
      1. The override function and its three-character tables are gone, and
         nothing calls it.
      2. One projection-independent selector excludes flowers from the saved
         RQ-184 wind cycle and falls back to the authored flower glyph.
    """
    src = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    # 1. The CP437 override must not exist as CODE. Each string below is a
    #    distinct fingerprint of it: the function definition, its two
    #    hard-coded punctuation tables, and the call site. The bare name is
    #    deliberately NOT banned -- the deletion note left in the shader
    #    mentions it by name so a future reader knows what was removed and
    #    why, and that prose must not trip this gate.
    assert "int foliage_ortho_flower_patch_glyph(FoliageCoverage c) {" not in src
    assert "return foliage_ortho_flower_patch_glyph(c);" not in src
    assert "int[4](0x2A, 0x2B, 0x23, 0x2A)" not in src
    assert "int[4](0x2A, 0x2B, 0x2A, 0x23)" not in src

    # 2. The authored terrain flower table must still be reachable. These are
    #    the extended-atlas ids for drawn blossoms; `mid_flowers` is the set a
    #    promoted flower lands on, because the winner promotion lifts SPARSE
    #    density to MEDIUM before the glyph is chosen.
    assert (
        "int dense_flowers[8] = int[8](620, 622, 628, 630, 647, 629, 624, 625);"
        in src
    )
    assert "int mid_flowers[4]   = int[4](620, 622, 628, 630);" in src

    _assert_unified_foliage_cycle_contract(src)


@pytest.mark.parametrize(
    ("function", "old", "new"),
    (
        (
            "foliage_saved_static_cycle_glyph",
            "if (!c.present || c.role == FOLIAGE_ROLE_FLOWER) return GLYPH_ID_NONE;",
            "if (!c.present) return GLYPH_ID_NONE;",
        ),
        (
            "foliage_saved_static_cycle_glyph",
            ")) % 5u;",
            ")) % 4u;",
        ),
        (
            "foliage_saved_static_cycle_glyph",
            "c.blade_id * 83492791u",
            "c.clump_id * 83492791u",
        ),
        (
            "foliage_saved_static_cycle_glyph",
            "uint arabic_ramp[3] = uint[3](1848u, 1849u, 936u);",
            "uint arabic_ramp[3] = uint[3](1848u, 1849u, 1849u);",
        ),
        (
            "foliage_clump_cycle_ticks",
            "return uint(floor(params.feature_params.x * 5.0));",
            "return uint(floor(params.feature_params.x * 4.0));",
        ),
        (
            "foliage_glyph_for_coverage",
            "int base_glyph = foliage_base_glyph_for_coverage(c);",
            "int base_glyph = product_vsb_frame.view_identity.z "
            "== VSB_PROJECTION_ORTHOGRAPHIC "
            "? foliage_base_glyph_for_coverage(c) : GLYPH_ID_NONE;",
        ),
    ),
)
def test_unified_foliage_cycle_owner_mutations_turn_red(
    function: str,
    old: str,
    new: str,
) -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    mutant = _replace_in_function(source, function, old, new)
    with pytest.raises(AssertionError):
        _assert_unified_foliage_cycle_contract(mutant)


def _assert_g45_species_contract(src: str) -> None:
    compact = _compact(src)
    selector = _compact(_function(src, "foliage_flower_species_ink"))
    compute = _compact(_function(src, "compute_product_foliage_fact"))
    finish = _compact(_function(src, "q3_probe_payload_finish_foliage"))

    # Four fixed species replace both projection-specific warm ramps. White is
    # a real fourth hash bucket, not a highlight mixed over another species.
    constants = (
        "constvec3FOLIAGE_FLOWER_HUE_PINK=vec3(1.00,0.42,0.72);",
        "constvec3FOLIAGE_FLOWER_HUE_YELLOW=vec3(1.00,0.86,0.24);",
        "constvec3FOLIAGE_FLOWER_HUE_BLUE=vec3(0.38,0.58,1.00);",
        "constvec3FOLIAGE_FLOWER_HUE_WHITE=vec3(1.00,0.98,0.92);",
    )
    for constant in constants:
        assert constant in compact
    assert "uintspecies=h%4u;" in selector
    assert "if(species==1u){hue=FOLIAGE_FLOWER_HUE_YELLOW;}" in selector
    assert "elseif(species==2u){hue=FOLIAGE_FLOWER_HUE_BLUE;}" in selector
    assert "elseif(species==3u){hue=FOLIAGE_FLOWER_HUE_WHITE;}" in selector
    assert "vec3(1.0,0.78,0.20)" not in compact
    assert "flower_lift" not in compact

    # Species identity is derived only from the world clump and world cell.
    assert "uint(clump_id" not in selector  # clump_id is already an integer fact.
    assert "uint(cell.x)*374761393u" in selector
    assert "uint(cell.y)*668265263u" in selector
    for forbidden in (
        "view_identity",
        "VSB_PROJECTION_",
        "camera",
        "feature_params",
        "quantized_time",
        "clock",
    ):
        assert forbidden not in selector

    # Terrain and retained foliage use the same selector before consuming the
    # surface's carried root band. Neither route reopens projection ownership.
    route_contracts = (
        (
            compute,
            "foliage_flower_species_ink(cov.clump_id,terr_cell,foliage_fg);",
            "foliage_apply_root_terrain_ramp_band(terrain_root_ramp_band,cov,foliage_fg);",
        ),
        (
            finish,
            "foliage_flower_species_ink(cov.clump_id,cell,foliage_fg);",
            "foliage_apply_root_terrain_ramp_band(foliage_root_ramp_band,cov,foliage_fg);",
        ),
    )
    assert compact.count("foliage_fg=foliage_flower_species_ink(") == 2
    for route, species_call, root_band_call in route_contracts:
        assert route.count(species_call) == 1
        assert route.count(root_band_call) == 1
        assert route.index(species_call) < route.index(root_band_call)
        for forbidden in ("view_identity", "VSB_PROJECTION_", "camera"):
            assert forbidden not in route


def test_four_world_fixed_species_reach_both_product_foliage_routes() -> None:
    _assert_g45_species_contract(_expand_includes(PRODUCT.read_text(encoding="utf-8")))


def test_species_keeps_root_band_weather_shadow_and_night_finishers() -> None:
    src = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    _assert_g45_species_contract(src)
    compiler = _compact(_function(src, "compile_probe_subcell_payload"))
    finalizer = _compact(_function(src, "vsb_product_finalize_record"))
    grass_guard = _compact(
        _function(src, "q3_probe_payload_apply_grass_family_palette_guard")
    )

    foliage_at = compiler.index("q3_probe_payload_apply_foliage(")
    cast_at = compiler.index("q3_probe_payload_apply_cast_shadow_hue(")
    weather_at = compiler.index("q3_probe_payload_apply_weather(")
    flower_fact_at = compiler.index(
        "boolterrain_flower=context.surface_kind==OWNER_TERRAIN&&"
        "foliage_product_present&&"
        "foliage_product_role==FOLIAGE_ROLE_FLOWER;"
    )
    terrain_guard_at = compiler.index(
        "if(context.surface_kind==OWNER_TERRAIN&&!terrain_flower){"
        "q3_probe_payload_apply_grass_family_palette_guard("
    )
    dither_at = compiler.index("q3_probe_payload_apply_surface_stable_dither(")
    flower_guard_at = compiler.index(
        "if(terrain_flower){"
        "q3_probe_payload_apply_grass_family_palette_guard("
    )
    water_at = compiler.index("q3_probe_payload_apply_water(")
    light_at = compiler.index("q3_probe_payload_apply_shadow_and_lit(")
    moss_guard_at = compiler.index(
        "if(context.surface_kind==OWNER_STATIC_MESH&&"
        "context.material==STATIC_WORLD_MOSS_MAT){"
        "q3_probe_payload_apply_grass_family_palette_guard("
    )
    assert (
        foliage_at < cast_at < weather_at < flower_fact_at < terrain_guard_at
        < dither_at < flower_guard_at < water_at < light_at
        < moss_guard_at
    )
    assert (
        "boolflower=foliage_product_present&&"
        "foliage_product_role==FOLIAGE_ROLE_FLOWER;" in grass_guard
    )
    assert "if(!flower){fg=product_grass_family_palette_guard_color(" in grass_guard
    assert "}bg=product_grass_family_palette_guard_color(" in grass_guard
    assert "glyph" not in grass_guard
    assert "floatdaylight=1.0-product_day_night_factor();" in grass_guard
    colour_guard = _compact(
        _function(src, "product_grass_family_palette_guard_color")
    )
    assert "if(day<=0.0)returncolor;" in colour_guard

    # The flower-specific hue is upstream of final scene night and neither
    # bypasses nor replaces the existing land day/night owner.
    assert "floatnight=product_day_night_factor();" in finalizer
    assert (
        "record.r0.y!=OWNER_SKY&&!input_value.context.payload_from_backstop"
        in finalizer
    )
    assert "product_apply_scene_land_daynight_pair(" in finalizer


@pytest.mark.parametrize(
    ("function", "old", "new"),
    (
        (
            "foliage_blade_from_anchor",
            "slot == FOLIAGE_CLUMP_SLOT_BLOOM",
            "slot == FOLIAGE_CLUMP_SLOT_TIP",
        ),
        (
            "foliage_flower_species_ink",
            "uint species = h % 4u;",
            "uint species = h % 3u;",
        ),
        (
            "foliage_flower_species_ink",
            "hue = FOLIAGE_FLOWER_HUE_WHITE;",
            "hue = FOLIAGE_FLOWER_HUE_PINK;",
        ),
        (
            "foliage_flower_species_ink",
            "uint h = clump_id",
            "uint h = clump_id ^ product_vsb_frame.view_identity.z",
        ),
        (
            "foliage_blade_from_anchor",
            "int slot_role = FOLIAGE_ROLE_STEM;",
            "int slot_role = product_vsb_frame.view_identity.z "
            "== VSB_PROJECTION_ORTHOGRAPHIC "
            "? FOLIAGE_ROLE_FLOWER : FOLIAGE_ROLE_STEM;",
        ),
        (
            "compute_product_foliage_fact",
            "foliage_fg = foliage_flower_species_ink(\n"
            "\t\t\tcov.clump_id, terr_cell, foliage_fg);",
            "foliage_fg = mix(foliage_fg, vec3(1.0, 0.78, 0.20), 0.82);",
        ),
        (
            "q3_probe_payload_finish_foliage",
            "foliage_fg = foliage_apply_root_terrain_ramp_band(\n"
            "\t\tfoliage_root_ramp_band, cov, foliage_fg);",
            "foliage_fg = foliage_apply_root_terrain_ramp_band(\n"
            "\t\t6u, cov, foliage_fg);",
        ),
    ),
)
def test_g45_species_owner_mutations_turn_red(
    function: str,
    old: str,
    new: str,
) -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    mutant = _replace_in_function(source, function, old, new)
    with pytest.raises(AssertionError):
        _assert_g45_species_contract(mutant)
        _assert_g45_bloom_contract(mutant)


def _assert_projection_independent_winner_publication(product: str) -> None:
    main = _function(product, "main")

    # Product resolves one stable winner from the four staged quarter records.
    # Projection is not an input to role, species, glyph, or publication.
    staged = main.index("staged_final_records[int(slot)] = final_record;")
    owner_start = "bool fl4330_owner_uniform = true;"
    assert owner_start in main
    start = main.index(owner_start)
    end = main.index("bool fl4722_water_quad", start)
    block = _compact(main[start:end])
    assert staged < start
    assert block.count("for(intslot=0;slot<4;slot+=1){") == 2
    assert "VSB_PROJECTION_" not in block
    assert "view_identity" not in block
    assert "camera" not in block
    assert "screen" not in block

    # Invalid quarters and quarters from different world Products fail closed
    # before any winner broadcast. Owner kind alone is not Product identity.
    assert (
        "if(!final_product_status_is_valid(status)){"
        "fl4330_owner_uniform=false;continue;}" in block
    )
    assert "fl4330_source_key=staged_final_records[slot].r1.y;" in block
    assert "fl4330_matcell=staged_final_records[slot].r1.w;" in block
    assert (
        "elseif(owner!=fl4330_owner||"
        "staged_final_records[slot].r1.y!=fl4330_source_key||"
        "staged_final_records[slot].r1.w!=fl4330_matcell){"
        "fl4330_owner_uniform=false;}" in block
    )
    assert (
        "if(fl4330_owner_seen&&fl4330_owner_uniform&&"
        "fl4330_foliage_found){" in block
    )

    # Stable Product facts, never quarter order, choose the shared glyph.
    assert (
        "uvec4key=uvec4(priority,staged_final_records[slot].r1.y,"
        "staged_final_records[slot].r1.w,"
        "staged_final_records[slot].r0.z);" in block
    )
    assert (
        "staged_final_records[slot].r0.z=fl4330_glyph;" in block
    )

    # A flower shares winner chroma but retains each corner's already-lit luma.
    # Background is not rewritten, so the four independent paper values survive.
    assert "winner_chroma=fl4330_fg/winner_luma;" in block
    assert (
        "floatcorner_luma=dot(staged_final_records[slot].fg.rgb,"
        "vec3(0.2126,0.7152,0.0722));" in block
    )
    assert (
        "staged_final_records[slot].fg.rgb=clamp("
        "winner_chroma*corner_luma,vec3(0.0),vec3(1.0));" in block
    )
    assert ".bg.rgb=" not in block


def test_both_projections_publish_one_stable_foliage_winner() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    compositor = (ROOT / "godot_project/render/ascii/raster/ascii_compositor.glsl").read_text(
        encoding="utf-8"
    )

    _assert_projection_independent_winner_publication(product)

    # Both painters consume the already-published fact and interpolate the four
    # retained foreground/background corners. Projection is not a second
    # Product winner, role, or colour-assembly owner.
    assert "bool fl4330_foliage_cell = foliage_product_present;" in compositor
    assert "fl4330_ortho_foliage_cell" not in compositor
    assert "fl4330_slot_x = slot ^ 1u" in compositor
    assert "fl4330_slot_y = slot ^ 2u" in compositor
    assert "fl4330_slot_xy = slot ^ 3u" in compositor
    assert "resolved.fg = clamp(" in compositor
    assert "resolved.bg = clamp(" in compositor


@pytest.mark.parametrize(
    ("old", "new"),
    (
        (
            "bool fl4330_owner_uniform = true;",
            "bool fl4330_owner_uniform = product_vsb_frame.view_identity.z "
            "== VSB_PROJECTION_ORTHOGRAPHIC;",
        ),
        (
            "uvec4 fl4330_key = uvec4(0u);\n\n"
            "\tfor (int slot = 0; slot < 4; slot += 1) {",
            "uvec4 fl4330_key = uvec4(0u);\n\n"
            "\tfor (int slot = 0; slot < 3; slot += 1) {",
        ),
        (
            "fl4330_owner_seen && fl4330_owner_uniform "
            "&& fl4330_foliage_found",
            "fl4330_owner_seen && fl4330_foliage_found",
        ),
        (
            "|| staged_final_records[slot].r1.y != fl4330_source_key\n",
            "",
        ),
        (
            "|| staged_final_records[slot].r1.w != fl4330_matcell\n",
            "",
        ),
        (
            "float corner_luma = dot(",
            "float corner_luma = 1.0 + 0.0 * dot(",
        ),
        (
            "staged_final_records[slot].fg.rgb = clamp(",
            "staged_final_records[slot].bg.rgb = clamp(",
        ),
    ),
)
def test_foliage_winner_publication_mutations_turn_red(
    old: str,
    new: str,
) -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    mutant = _replace_in_function(source, "main", old, new)
    with pytest.raises(AssertionError):
        _assert_projection_independent_winner_publication(mutant)
