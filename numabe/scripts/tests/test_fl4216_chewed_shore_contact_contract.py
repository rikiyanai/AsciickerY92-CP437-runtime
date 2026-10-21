"""F11 shoreline/contact ownership contracts; viewport acceptance stays open."""

from __future__ import annotations
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"
VSB = ROOT / "godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl"


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


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", _without_comments(source))


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


def _mix(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
    amount: float,
) -> tuple[float, float, float]:
    return tuple(a * (1.0 - amount) + b * amount for a, b in zip(left, right))


def _contact_pair(
    ink: tuple[float, float, float],
    paper: tuple[float, float, float],
    daylight: float,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    water_foam = (0.88, 0.96, 0.98)
    contact = _mix(water_foam, paper, 0.22)
    amount = max(0.0, min(1.0, daylight))
    return _mix(ink, contact, amount), _mix(paper, contact, amount)


def _assert_contract(product: str, vsb: str) -> None:
    product_code = _compact(product)
    vsb_code = _compact(vsb)
    sample = _compact(_function(product, "product_water_sample"))
    fragment = _compact(
        _function(product, "product_fl4216_chewed_fragment_at")
    )
    moving_strength = _compact(
        _function(product, "product_fl4216_break_strength")
    )
    moving_active = _compact(
        _function(product, "product_fl4216_break_active")
    )
    universal_break = _compact(
        _function(product, "product_fl4216_universal_break")
    )
    glyph = _compact(_function(product, "water_glyph_candidate"))
    paint = _compact(
        _function(product, "product_fl4216_apply_reflection_pair")
    )
    decode = _compact(
        _function(product, "product_fl4216_decode_reflected_material")
    )
    night = _compact(_function(product, "product_apply_night_water_pair"))
    day = _compact(_function(product, "product_apply_day_water_pair"))
    packer = _compact(_function(vsb, "fl4216_reflection_word"))
    direct_contact = _compact(
        _function(vsb, "fl4216_direct_water_solid_contact")
    )
    current_camera_query = _compact(
        _function(vsb, "query_current_camera_surface")
    )
    # Match the actual entry point, not the explanatory comment containing
    # the token `main()` earlier in the shader.
    vsb_main = _compact(_function(vsb, "void main"))

    # One broad finite world field controls paper foam and terrain-break gaps.
    # Every solid family is reduced to the same nearest shoreline ring before
    # this sample is built; no geometry-family oscillator survives downstream.
    assert "constfloatFL4216_BREAK_FRAGMENT_MIN=0.35;" in product_code
    assert product_code.count("product_fl4216_chewed_fragment_at(") == 2
    assert "world_xz*0.22+vec2(4.1,-7.3)" in fragment
    assert "returnsmoothstep(0.48,0.72,shore_noise);" in fragment
    for forbidden in (
        "camera",
        "projection",
        "ViewRay",
        "screen",
        "clock",
        "time",
    ):
        assert forbidden.lower() not in fragment.lower()
    assert "max(shore_proximity*0.45" not in sample
    assert "mix(s.bg,vec3(0.52,0.76,0.86),shore_proximity*0.18)" not in sample
    fragment_law = (
        "1.0-smoothstep(0.30,2.35,s.shore_dist_cells)",
        "smoothstep(0.04,0.22,s.water_depth)",
        "1.0-smoothstep(0.92,1.55,s.water_depth)",
        "floatchewed_fragment=product_fl4216_chewed_fragment_at(s.world_xz);",
        "shore_reach*shallow_depth*chewed_fragment",
        "floatmoving_break_strength=0.0;",
        "boolmoving_break_active=false;",
        "product_fl4216_universal_break(",
        "floatwaterline_floor=s.direct_water_solid_contact?1.0:"
        "1.0-smoothstep(0.0,0.60,s.shore_dist_cells);",
        "floatbreak_fragment=mix(chewed_fragment,mix(0.62,1.0,chewed_fragment),waterline_floor);",
        "s.break_strength=moving_break_strength*break_fragment;",
        "s.break_active=moving_break_active&&"
        "break_fragment>=FL4216_BREAK_FRAGMENT_MIN;",
    )
    cursor = -1
    for token in fragment_law:
        cursor = sample.find(token, cursor + 1)
        assert cursor >= 0, token
    assert "chewed_fragment" not in moving_strength
    assert "chewed_fragment" not in moving_active
    assert "s.direct_water_solid_contact?min(s.shore_dist_cells,0.18):s.shore_dist_cells" in moving_strength
    assert "s.direct_water_solid_contact?min(s.shore_dist_cells,0.18):s.shore_dist_cells" in moving_active
    assert "persistent_tooth" not in sample
    assert "s.break_strength=max(s.break_strength,0.42*break_fragment)" not in sample
    assert "product_fl4216_break_strength(s,time,wave_stage)" in universal_break
    assert "product_fl4216_break_active(s,time,wave_stage)" in universal_break
    assert universal_break.count("product_fl4216_break_strength(") == 1
    assert universal_break.count("product_fl4216_break_active(") == 1
    for forbidden in (
        "direct_water_solid_contact",
        "contact_phase",
        "contact_wave",
        "contact_strength",
        "FL4216_BREAK_PERIOD",
        "camera",
        "projection",
        "receiver",
        "gl_FragCoord",
    ):
        assert forbidden.lower() not in universal_break.lower()
    # F8 now eases only the body-band coordinate from normalized daytime depth
    # to the legacy raw-depth night look.  It must occur after every shared
    # shoreline fact, so it cannot gate the F11 fragment, foam, or break owner.
    body_start = sample.index(
        "uintdepth_band=product_water_depth_band(s.depth_fade);"
    )
    assert "product_day_night_factor" not in sample[:body_start]
    assert "daylight" not in sample[:body_start]
    assert sample.count("product_day_night_factor()") == 1
    assert (
        "floatdepth_band_daylight=clamp("
        "1.0-product_day_night_factor(),0.0,1.0);" in sample[body_start:]
    )
    assert (
        "if(s.break_active&&s.break_dir>=0&&s.break_stage>=0){"
        in glyph
    )

    # The rejected static contact band is gone. Reflection remains F10-only;
    # F11 direct contacts can affect paint only through the shared break fields.
    assert "direct_water_solid_contact" not in paint
    assert "direct_boat_contact" not in paint
    assert "contact_colour" not in paint
    assert "contact_amount" not in paint
    assert "mix(WATER_FOAM,paper,0.22)" not in paint
    assert "scene_query" not in paint
    assert (
        "floatshoreline_mirror_admission=smoothstep(0.75,2.26,s.shore_dist_cells);"
        in day
    )
    assert "contact_free_mirror_admission" not in day
    assert "s.direct_water_solid_contact" not in day

    # F11 uses one reserved status bit in the existing record. Its producer is
    # the direct pre-water hit; F10's mirrored query stays intact but carries no
    # contact eligibility.
    assert "constuintVISIBLE_SURFACE_ABI_VERSION=4u;" in vsb_code
    assert "uintreflection_word;" in vsb_code
    assert packer.count("scene_query_dynamic_tlas_prevalidated(") == 1
    assert packer.count("scene_query_static_tlas(") == 1
    assert packer.count("scene_query_heightfield(") == 1
    assert "STATUS_DIRECT_WATER_SOLID_CONTACT=1u<<29u" in vsb_code
    assert "FL4216_REFLECTION_AUTHORED_SHADE_MAX=7u" in vsb_code
    assert "FL4216_DIRECT_WATER_CONTACT_DEPTH=0.72" in vsb_code
    assert "hit.owner_kind!=OWNER_TERRAIN" in direct_contact
    assert "hit.owner_kind!=OWNER_STATIC_MESH" in direct_contact
    assert "hit.owner_kind!=OWNER_DYNAMIC_MESH" in direct_contact
    assert "floatdepth_below_water=WATER_SURFACE_Y-hit.position.y;" in direct_contact
    assert "depth_below_water>=0.0" in direct_contact
    assert "depth_below_water<=FL4216_DIRECT_WATER_CONTACT_DEPTH" in direct_contact
    assert "scene_query" not in direct_contact
    assert "reflect" not in direct_contact
    # Direct contact belongs to the one current-camera query owner shared by
    # both projections. No perspective-only secondary query may compete with
    # or annotate a retained candidate after visibility has been selected.
    assert "pre_water_direct_contact=false;" in current_camera_query
    assert "fl4216_direct_water_solid_contact(nearest);" in current_camera_query
    assert "query_current_camera_surface(" in vsb_main
    assert "query_perspective_water_source(" not in vsb
    assert "probe_candidate_to_hit(record_index,ray,nearest" not in vsb_main
    assert "STATUS_DIRECT_WATER_SOLID_CONTACT" in vsb_main
    assert "FL4216_REFLECTION_WATER_CONTACT" not in vsb_code
    assert "WATER_CONTACT" not in packer
    assert "scene_query" not in decode
    assert "water_contact" not in decode

    # The shared shoreline facts intentionally affect day and night. Ordinary
    # night equations remain consumers of those facts and do not recompute the
    # moving break, contact, reflection ray, or fragment field.
    assert "moon_glitter" in night
    assert "max(s.break_strength,0.35)" in night
    assert "s.break_active" in night
    for forbidden in (
        "product_fl4216_break_strength(",
        "product_fl4216_break_active(",
        "chewed_fragment",
        "direct_water_solid_contact",
        "scene_query",
    ):
        assert forbidden not in night
    # Reflection admission is derived only from the shared shoreline distance;
    # direct contact and submerged-source branches cannot become a second gate.
    assert (
        "floatshoreline_mirror_admission=smoothstep(0.75,2.26,s.shore_dist_cells);"
        in day
    )
    assert "if(PRODUCT_FL4216_REFLECTION_RAY_ENABLED){" in day
    assert "||s.direct_water_solid_contact" not in day
    assert "floatbreak_ink_mix=s.break_strength*(s.break_active?0.58:0.42);" in day
    assert "floatbreak_mix=s.stable_cutover_paint?s.break_strength*0.10:0.16;" in day
    assert "contact_free_mirror_admission" not in day
    assert "submerged_visibility" not in day[day.index("floatshoreline_mirror_admission=") :]


def test_chewed_fragment_gates_foam_strength_and_break_glyph() -> None:
    _assert_contract(
        _expand_includes(PRODUCT.read_text(encoding="utf-8")),
        VSB.read_text(encoding="utf-8"),
    )


def test_contact_full_day_converges_and_daylight_is_continuous() -> None:
    ink = (0.11, 0.37, 0.51)
    paper = (0.32, 0.68, 0.76)
    differences = []
    for daylight in (0.0, 0.25, 0.5, 0.75, 1.0):
        out_ink, out_paper = _contact_pair(ink, paper, daylight)
        differences.append(sum(abs(a - b) for a, b in zip(out_ink, out_paper)))
    assert differences[0] > differences[1] > differences[2] > differences[3]
    assert differences[4] < 1.0e-12
    assert _contact_pair(ink, paper, 0.0) == (ink, paper)


def test_vsb_query_abi_and_ordinary_night_owner_remain_connected() -> None:
    _assert_contract(
        _expand_includes(PRODUCT.read_text(encoding="utf-8")),
        VSB.read_text(encoding="utf-8"),
    )


def test_f11_shared_break_owner_mutations_turn_red() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    vsb = VSB.read_text(encoding="utf-8")
    for injected in (
        "if (s.direct_water_solid_contact) return;",
        "float contact_phase = time / FL4216_BREAK_PERIOD;",
    ):
        mutant = _replace_in_function(
            product,
            "product_fl4216_universal_break",
            "\twave_stage = 0;",
            "\twave_stage = 0;\n\t" + injected,
        )
        with pytest.raises(AssertionError):
            _assert_contract(mutant, vsb)


def test_f11_direct_contact_paint_mutation_turns_red() -> None:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    vsb = VSB.read_text(encoding="utf-8")
    mutant = _replace_in_function(
        product,
        "product_apply_day_water_pair",
        "float break_ink_mix = s.break_strength * (s.break_active ? 0.58 : 0.42);",
        "float break_ink_mix = s.break_strength * (s.break_active ? 0.58 : 0.41);",
    )
    with pytest.raises(AssertionError):
        _assert_contract(mutant, vsb)


def test_second_vsb_query_mutation_turns_red() -> None:
    vsb = VSB.read_text(encoding="utf-8")
    mutant = _replace_in_function(
        vsb,
        "fl4216_reflection_word",
        "query_valid = scene_query_static_tlas(mirrored_ray, reflected);",
        "query_valid = scene_query_static_tlas(mirrored_ray, reflected);\n"
        "        query_valid = scene_query_static_tlas(mirrored_ray, reflected);",
    )
    with pytest.raises(AssertionError):
        _assert_contract(_expand_includes(PRODUCT.read_text(encoding="utf-8")), mutant)


def test_current_camera_contact_owner_mutation_turns_red() -> None:
    """Removing the current query-owned contact producer must turn red."""
    vsb = VSB.read_text(encoding="utf-8")
    mutant = _replace_in_function(
        vsb,
        "query_current_camera_surface",
        "    pre_water_direct_contact = fl4216_direct_water_solid_contact(nearest);",
        "    pre_water_direct_contact = false;",
    )
    with pytest.raises(AssertionError):
        _assert_contract(_expand_includes(PRODUCT.read_text(encoding="utf-8")), mutant)


def test_terrain_contact_exclusion_mutation_turns_red() -> None:
    vsb = VSB.read_text(encoding="utf-8")
    mutant = _replace_in_function(
        vsb,
        "fl4216_direct_water_solid_contact",
        "hit.owner_kind != OWNER_TERRAIN\n"
        "                && hit.owner_kind != OWNER_STATIC_MESH",
        "hit.owner_kind != OWNER_STATIC_MESH",
    )
    with pytest.raises(AssertionError):
        _assert_contract(_expand_includes(PRODUCT.read_text(encoding="utf-8")), mutant)
