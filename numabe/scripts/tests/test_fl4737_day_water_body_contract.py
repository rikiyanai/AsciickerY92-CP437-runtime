"""F8/F9 daytime water body, terrain-bed, and diffuse cloud-colour contract.

These mutation-backed source checks prove ownership and bounded arithmetic. They
do not prove target resemblance, temporal smoothness in the viewport, the still
missing shallow-contact pose pair, or operator acceptance.
"""

from __future__ import annotations
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"


def _function_body(source: str, name: str) -> str:
    match = re.search(
        rf"\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{", source, re.S
    )
    assert match is not None, f"missing function {name}"
    brace = source.find("{", match.start())
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1:index]
    raise AssertionError(f"unterminated function {name}")


def _compact(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    return re.sub(r"\s+", "", source)


def _assert_contract(source: str) -> None:
    whole = _compact(source)
    depth_band = _compact(_function_body(source, "product_water_depth_band"))
    body_band = _compact(_function_body(source, "product_day_water_body_band"))
    sample = _compact(_function_body(source, "product_water_sample"))
    mottle = _compact(_function_body(source, "product_day_water_mottle_gain"))
    cloud = _compact(
        _function_body(source, "product_day_water_cloud_colour_amount")
    )
    sky = _compact(_function_body(source, "product_day_water_sky_colour"))
    finalize = _compact(_function_body(source, "vsb_product_finalize_record"))
    day_pair = _compact(
        _function_body(source, "product_apply_day_water_pair")
    )

    assert "constvec3WATER_SHALLOW_BG=vec3(0.34,0.64,0.66);" in whole
    assert "constvec3WATER_MID_BG=vec3(0.25,0.56,0.60);" in whole
    assert "constvec3WATER_DEEP_BG=vec3(0.17,0.46,0.52);" in whole
    transition = re.search(
        r"const\s+float\s+WATER_SHALLOW_TRANSITION_END\s*=\s*([0-9.]+)\s*;",
        source,
    )
    assert transition is not None
    assert float(transition.group(1)) <= 0.82
    assert "constfloatWATER_BED_VISIBILITY_FALLOFF=0.82;" in whole
    # FL-4737 F8 (2026-08-13 operator falsifier, exact-P1 noon frame): the night
    # slope above is preserved byte for byte; daylight eases it to 0.50 so the
    # deepest admitted column transmits half the submerged input instead of the
    # 0.18 the operator rejected on 3785e14dd.
    assert "constfloatWATER_BED_VISIBILITY_FALLOFF_DAY=0.50;" in whole
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
    depth_selection = (
        "uintdepth_band=product_water_depth_band(s.depth_fade);"
        "uintlegacy_depth_band=product_water_depth_band(s.water_depth);"
        "floatdepth_band_daylight=clamp("
        "1.0-product_day_night_factor(),0.0,1.0);"
        "vec3water_body=mix("
        "product_day_water_body_band(legacy_depth_band),"
        "product_day_water_body_band(depth_band),"
        "depth_band_daylight);"
    )
    assert depth_selection in sample
    # ONE continuous law, TWO slopes, blended by the SAME daylight fact the
    # depth-band owner directly above uses. That shared factor is what makes the
    # full-night result byte-identical to the pre-F8 law.
    assert (
        "floatbed_visibility_falloff=mix("
        "WATER_BED_VISIBILITY_FALLOFF,"
        "WATER_BED_VISIBILITY_FALLOFF_DAY,"
        "depth_band_daylight);"
        "floatbed_visibility=clamp("
        "1.0-s.depth_fade*bed_visibility_falloff,"
        "0.0,1.0);" in sample
    )
    # The transmission is CARRIED, not recomputed: the fog stage in the pair
    # reads this exact number to avoid attenuating the transmitted bed twice.
    assert "s.bed_transmission=bed_visibility;" in sample
    assert whole.count("s.bed_transmission=") == 1
    # FL-4737 F8 revision 2 (2026-08-13, adversarial review AD1/AD2): the direct
    # submerged source STAYS in the column. Revision 1 deleted this substitution
    # and composited the source last instead, which (a) capped every surface
    # stage by column thickness and (b) being ungated, moved FULL NIGHT bytes --
    # the lake floor appeared at 0.1476 in submerged-source cells where canon is
    # 0.0000. Deleting it again is pinned as a night regression below.
    assert (
        "vec3bed=cell_facts.submerged_source_present?"
        "cell_facts.submerged_source_bg:cell_facts.bed;" in sample
    )
    assert "s.submerged_source_bg=" not in sample
    assert "s.bg=mix(water_body,bed,bed_visibility);" in sample
    assert "WATER_SHALLOW_BED_MAX_VISIBILITY" not in whole
    assert "WATER_SURFACE_FILM_MIN" not in whole

    assert "world_xz*0.045+vec2(13.7,-8.9)" in mottle
    assert mottle.count("stable_value_noise(") == 1
    assert "if(mass<0.38)return0.90;" in mottle
    assert "if(mass>0.66)return1.10;" in mottle
    assert "return1.0;" in mottle
    assert "product_day_water_mottle_gain(s.world_xz)" in sample

    assert "if(!baseline_feature_clouds_enabled())return0.0;" in cloud
    # FL-4737 R1 (2026-08-13): water registers with land by walking the SAME
    # up-sun ray to the shared cloud plane. Sampling the field straight overhead
    # put the water band roughly two cloud-mass widths from the shadow the same
    # cloud cast ashore, so the unprojected form is now falsified.
    assert (
        "product_cloud_shadow_projected_world(world_xz,WATER_SURFACE_Y)"
        in cloud
    )
    assert cloud.count("product_cloud_shadow_projected_world(") == 1
    assert "product_cloud_density_at_sample_xz(" not in cloud
    assert "stable_value_noise(" not in cloud
    assert "cloud<0.24?0.0:(cloud<0.62?0.42:0.78)" in cloud
    assert "1.0-product_day_night_factor()" in cloud
    assert "returncloud_band*daylight;" in cloud
    assert "product_day_water_cloud_colour_amount(s.world_xz)" in day_pair
    assert "product_sun_dir().y" in sky
    assert "RECEIPTED_DAY_SKY_COLOR" in sky
    # D3 (2026-08-13): `cloud_key` is gone. It was a MIX toward a pale
    # sky/shallow blend, and subtracting WATER_MID_BG from it produced a warm,
    # R-dominant difference vector that only read as "sky" when it landed on the
    # mid band. The replacement carries the sky's own chromaticity, so it stays
    # correct on a transmitted bed.
    assert "cloud_key" not in whole
    # FL-4737 D3 (2026-08-13 adversarial review): the water cloud writer left
    # product_water_sample entirely. Anything added there is rescaled by the
    # affine background fog mix in product_apply_day_water_pair
    # (`1 - WATER_FOG_BG_PEAK * depth_fade`), so no position inside this function
    # can deliver a depth-independent lift. product_water_sample must now carry
    # NO cloud term at all.
    assert "s.bg=mix(water_body,bed,bed_visibility);" in sample
    assert "cloud" not in sample
    assert "vec3cloud_water_band=" not in sample
    assert "vec3cloud_colour_delta=" not in sample

    # The single water cloud writer is downstream of the fog mix, is ADDITIVE
    # (so every depth-band difference survives untouched -- a blend toward one
    # common pale target is the falsified 9cf571d7-predecessor shape), and
    # carries the LIVE SKY chromaticity rescaled to a fixed luma budget. The
    # previous `cloud_key - WATER_MID_BG` difference vector was R-dominant and
    # tinted transmitted bed warm on shallow cells.
    assert "constfloatFL4737_CLOUD_SKY_LIFT_LUMA=0.11;" in whole
    # FL-4737 F8 (2026-08-13): the third factor. This fog is the water COLUMN's
    # own haze, so it applies to the fraction the column did NOT transmit. The
    # transmitted bed was already attenuated once by bed_visibility; hazing the
    # composite attenuated it a second time and delivered the deepest bed at
    # 0.18 * (1 - 0.46) = 0.097, which is the arithmetic behind the operator's
    # "uniform opaque pale-teal fill, submerged terrain invisible". Gated by
    # water_daylight so full-night amounts are unchanged.
    assert (
        "floatwater_daylight=clamp(1.0-product_day_night_factor(),0.0,1.0);"
        in day_pair
    )
    assert (
        "floatbackground_fog_amount=clamp(s.depth_fade,0.0,1.0)"
        "*clamp(WATER_FOG_BG_PEAK,0.0,1.0)"
        "*(1.0-clamp(s.bed_transmission,0.0,1.0)*water_daylight);"
        "background=clamp(mix(clamp(background,vec3(0.0),vec3(1.0)),"
        "fog_target,background_fog_amount),vec3(0.0),vec3(1.0));"
        "floatcloud_colour_amount="
        "product_day_water_cloud_colour_amount(s.world_xz);"
        "vec3cloud_sky_light=product_day_water_sky_colour();"
        "vec3cloud_sky_lift=cloud_sky_light"
        "*(FL4737_CLOUD_SKY_LIFT_LUMA"
        "/max(rec709_luminance(cloud_sky_light),0.0001));"
        "background=clamp(background+cloud_sky_lift*cloud_colour_amount,"
        "vec3(0.0),vec3(1.0));" in day_pair
    )
    # Exactly one water cloud writer in the whole file.
    assert whole.count("cloud_sky_lift*cloud_colour_amount") == 1

    # QD1 (2026-08-13 Codex round 4): PIN THE ACTUAL PAINT ORDER. An earlier
    # comment claimed shore foam and wake "remain later and stronger owners by
    # construction"; they are in fact EARLIER. Foam and wake paint inside
    # product_water_sample, break wash and fog inside the day pair, cloud lift
    # after the fog, and F10 reflection last. F11 now shares the earlier break
    # state and has no late static-band writer.
    assert (
        sample.index("s.bg=mix(water_body,bed,bed_visibility);")
        < sample.index("WATER_FOAM,s.shore_foam*0.18")
        < sample.index("wake_color,s.wake*0.72")
    )
    assert "cloud" not in sample          # and no cloud term among them
    # FL-4737 F8 revision 2: the direct submerged source is composited at the TOP
    # of the pair -- the transmitted-fact layer -- and everything below it is
    # either the column above the source or light on the surface. Revision 1's
    # last-position composite is pinned as falsified by the mutants below.
    assert (
        "foreground=mix(foreground,s.submerged_source_fg,s.submerged_visibility);"
        in day_pair
    )
    assert "direct_visibility" not in whole
    assert (
        day_pair.index("s.submerged_visibility")
        < day_pair.index("break_wash")
        < day_pair.index("background_fog_amount")
        < day_pair.index("cloud_sky_lift")
        < day_pair.index("product_fl4216_apply_reflection_pair(")
    )
    # FL-4216 owns one shoreline-distance admission law for reflection across
    # terrain, static, and dynamic crossings. Open water stays fully admitted;
    # the submerged-source transport must not install a second geometry-family
    # mirror gate at this downstream paint boundary.
    assert (
        "floatshoreline_mirror_admission=smoothstep("
        "0.75,2.26,s.shore_dist_cells);" in day_pair
    )
    assert (
        "1.0-product_day_night_factor(),"
        "shoreline_mirror_admission," in day_pair
    )
    reflection = _compact(
        _function_body(source, "product_fl4216_apply_reflection_pair")
    )
    assert "floatmirror_admission," in _compact(source)
    assert (
        "floatamount=FL4216_REFLECTION_SOURCE_INK_MAX"
        "*clamp(daylight,0.0,1.0)*mass_gain"
        "*clamp(mirror_admission,0.0,1.0);" in reflection
    )
    assert (
        "reflected.paper_mix*clamp(daylight,0.0,1.0)*mass_gain"
        "*clamp(mirror_admission,0.0,1.0)" in reflection
    )
    # Exactly two mirror_admission uses remain, both inside F10 reflection.
    # Universal F11 contact is break state and must not enter this helper.
    assert reflection.count("clamp(mirror_admission,0.0,1.0)") == 2
    assert "direct_water_solid_contact" not in reflection
    assert "direct_boat_contact" not in reflection
    assert "contact_amount" not in reflection
    # The direct submerged-source share has one owner: the composition at the
    # top of the pair. Reflection admission is shoreline keyed, so another read
    # here would recreate the retired competing mirror law.
    assert day_pair.count("s.submerged_visibility") == 1
    assert day_pair.count("s.submerged_source_fg") == 1
    assert "s.submerged_source_bg" not in day_pair
    # One definition plus exactly ONE call site. Before D3 there were two call
    # sites (product_water_sample and the day pair); the sample-side one is gone
    # with the writer it fed. A third occurrence means a second water cloud
    # consumer has appeared.
    assert whole.count("product_day_water_cloud_colour_amount(") == 2
    # Background only: the lift must not be written onto the ink.
    assert "foreground=clamp(foreground+cloud_sky_lift" not in day_pair
    assert "foreground+cloud_sky_lift" not in whole
    for forbidden in ("gl_FragCoord", "camera", "projection", "alpha"):
        assert forbidden not in mottle
        assert forbidden not in cloud
        assert forbidden not in sky
    assert "product_apply_night_water_pair" not in sample
    assert "product_apply_night_water_pair" not in mottle
    assert "product_apply_night_water_pair" not in cloud
    assert "product_apply_night_water_pair" not in sky

    # The pre-existing night pair stays the final full-night owner. F8/F9 modify
    # only the day sample; they neither add a second night palette nor alpha.
    assert (
        "product_apply_night_water_pair(water_sample,night,"
        "record.fg.rgb,record.bg.rgb);" in finalize
    )
    assert "record.fg.a=1.0;record.bg.a=1.0;" in finalize


def test_fl4737_day_water_body_contract() -> None:
    _assert_contract(_expand_includes(PRODUCT.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    ("old", "new"),
    (
        (
            "const float WATER_SHALLOW_TRANSITION_END = 0.82;",
            "const float WATER_SHALLOW_TRANSITION_END = 2.40;",
        ),
        (
            "uint depth_band = product_water_depth_band(s.depth_fade);",
            "uint depth_band = product_water_depth_band(s.water_depth);",
        ),
        (
            "uint legacy_depth_band = product_water_depth_band(s.water_depth);",
            "uint legacy_depth_band = product_water_depth_band(s.depth_fade);",
        ),
        (
            "float depth_band_daylight = clamp(\n"
            "\t\t1.0 - product_day_night_factor(), 0.0, 1.0);",
            "float depth_band_daylight = 1.0;",
        ),
        (
            "world_xz * 0.045 + vec2(13.7, -8.9)",
            "world_xz * 0.45 + vec2(13.7, -8.9)",
        ),
        ("if (mass < 0.38) return 0.90;", "return mass;"),
        (
            "float cloud_band = cloud < 0.24 ? 0.0 : "
            "(cloud < 0.62 ? 0.42 : 0.78);",
            "float cloud_band = cloud;",
        ),
        (
            "float cloud = product_cloud_shadow_projected_world(\n"
            "\t\tworld_xz, WATER_SURFACE_Y);",
            "float cloud = stable_value_noise(world_xz, 7u);",
        ),
        (
            "float cloud = product_cloud_shadow_projected_world(\n"
            "\t\tworld_xz, WATER_SURFACE_Y);",
            "float cloud = product_cloud_density_at_sample_xz(world_xz);",
        ),
        (
            "float daylight = clamp(1.0 - product_day_night_factor(), 0.0, 1.0);\n"
            "\treturn cloud_band * daylight;",
            "float daylight = 1.0;\n"
            "\treturn cloud_band * daylight;",
        ),
        # D3 mutants (2026-08-13): the luma budget, the sky chromaticity, the
        # additive form, and the post-fog position are each independently
        # falsifiable.
        (
            "const float FL4737_CLOUD_SKY_LIFT_LUMA = 0.11;",
            "const float FL4737_CLOUD_SKY_LIFT_LUMA = 0.02;",
        ),
        (
            "\tvec3 cloud_sky_light = product_day_water_sky_colour();",
            "\tvec3 cloud_sky_light = WATER_SHALLOW_BG;",
        ),
        (
            "\tbackground = clamp(\n"
            "\t\tbackground + cloud_sky_lift * cloud_colour_amount,\n"
            "\t\tvec3(0.0), vec3(1.0));",
            "\tbackground = clamp(\n"
            "\t\tmix(background, cloud_sky_lift, cloud_colour_amount),\n"
            "\t\tvec3(0.0), vec3(1.0));",
        ),
        (
            "\t\t* (FL4737_CLOUD_SKY_LIFT_LUMA\n"
            "\t\t\t/ max(rec709_luminance(cloud_sky_light), 0.0001));",
            "\t\t* FL4737_CLOUD_SKY_LIFT_LUMA;",
        ),
        (
            "const float WATER_BED_VISIBILITY_FALLOFF = 0.82;",
            "const float WATER_BED_VISIBILITY_FALLOFF = 1.00;",
        ),
        (
            "1.0 - s.depth_fade * bed_visibility_falloff",
            "1.0 - step(WATER_SHALLOW_TRANSITION_END, s.water_depth)",
        ),
        # --- F8 mutants (2026-08-13) -----------------------------------------
        # The day slope is the operator-facing knob. Collapsing it back to the
        # night slope restores the rejected 0.18 deep floor.
        (
            "const float WATER_BED_VISIBILITY_FALLOFF_DAY = 0.50;",
            "const float WATER_BED_VISIBILITY_FALLOFF_DAY = 0.82;",
        ),
        (
            "\tfloat bed_visibility_falloff = mix(\n"
            "\t\tWATER_BED_VISIBILITY_FALLOFF,\n"
            "\t\tWATER_BED_VISIBILITY_FALLOFF_DAY,\n"
            "\t\tdepth_band_daylight);",
            "\tfloat bed_visibility_falloff = WATER_BED_VISIBILITY_FALLOFF;",
        ),
        # Dropping the carried transmission re-arms the double attenuation.
        (
            "\ts.bed_transmission = bed_visibility;",
            "\ts.bed_transmission = 0.0;",
        ),
        (
            "\t\t* clamp(WATER_FOG_BG_PEAK, 0.0, 1.0)\n"
            "\t\t* (1.0 - clamp(s.bed_transmission, 0.0, 1.0) * water_daylight);",
            "\t\t* clamp(WATER_FOG_BG_PEAK, 0.0, 1.0);",
        ),
        # --- F8 revision 2 mutants (adversarial review AD1/AD2) ---------------
        # AD2 NIGHT REGRESSION. Deleting the bed substitution without a daylight
        # gate is exactly what revision 1 did: at full night the lake floor then
        # appears at 0.1476 in every submerged-source cell where canon is
        # 0.0000. This mutant is the night guard.
        (
            "\tvec3 bed = cell_facts.submerged_source_present\n"
            "\t\t? cell_facts.submerged_source_bg : cell_facts.bed;",
            "\tvec3 bed = cell_facts.bed;",
        ),
        # AD1 REFLECTION ERASURE. Dropping the mirror cap restores the state in
        # which reflected ink 0.78 / paper 0.56 painted over a source arriving at
        # 0.097 -- the operator-falsified missing wrist.
        (
            "\t\t\t* mass_gain\n"
            "\t\t\t* clamp(mirror_admission, 0.0, 1.0);",
            "\t\t\t* mass_gain;",
        ),
        (
            "\t\t\t\t* mass_gain\n"
            "\t\t\t\t* clamp(mirror_admission, 0.0, 1.0)),",
            "\t\t\t\t* mass_gain),",
        ),
        # FL-4216 shoreline admission must remain the sole reflection domain.
        # Replacing it with a submerged-visibility gate reintroduces the retired
        # geometry-family-specific owner.
        (
            "\t\tfloat shoreline_mirror_admission = smoothstep(\n"
            "\t\t\t0.75, 2.26, s.shore_dist_cells);",
            "\t\tfloat shoreline_mirror_admission = 1.0;",
        ),
        (
            "\t\tproduct_fl4216_apply_reflection_pair(\n"
            "\t\t\tvsb_product_selected_reflected_paint,",
            "\t\tbackground = mix(\n"
            "\t\t\tbackground, s.submerged_source_fg, s.submerged_visibility);\n"
            "\t\tproduct_fl4216_apply_reflection_pair(\n"
            "\t\t\tvsb_product_selected_reflected_paint,",
        ),
    ),
)
def test_fl4737_damaging_mutations_turn_red(old: str, new: str) -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    assert source.count(old) == 1, old
    mutant = source.replace(old, new, 1)
    with pytest.raises(AssertionError):
        _assert_contract(mutant)


def _bed_visibility(depth_fade: float, daylight: float) -> float:
    """Mirror of the shader: one continuous law, two slopes, one daylight fact."""
    falloff = 0.82 + (0.50 - 0.82) * daylight
    return max(0.0, min(1.0, 1.0 - depth_fade * falloff))


def test_fl4737_terrain_bed_transmission_endpoints() -> None:
    # NIGHT is unchanged: the accepted 1.00 -> 0.18 curve, byte for byte.
    assert _bed_visibility(0.0, 0.0) == 1.0
    assert _bed_visibility(1.0, 0.0) == pytest.approx(0.18)
    # DAY is the F8 repair: 1.00 -> 0.50, continuous, no threshold anywhere.
    assert _bed_visibility(0.0, 1.0) == 1.0
    assert _bed_visibility(1.0, 1.0) == pytest.approx(0.50)
    # Strictly decreasing in depth and strictly increasing in daylight, so no
    # cutoff can hide inside the blend (af58771a5 was rejected as opaque).
    depths = [i / 20.0 for i in range(21)]
    for daylight in (0.0, 0.25, 0.5, 0.75, 1.0):
        values = [_bed_visibility(d, daylight) for d in depths]
        assert all(b < a for a, b in zip(values, values[1:]))
    assert all(
        _bed_visibility(1.0, d) < _bed_visibility(1.0, d + 0.25)
        for d in (0.0, 0.25, 0.5, 0.75)
    )


def test_fl4737_deep_day_bed_delivery_beats_the_rejected_route() -> None:
    """The operator rejected BOTH prior shapes; arithmetic must show the gain.

    af58771a5 cut the deep bed off outright (rejected: opaque). 3785e14dd
    restored a continuous 1.00 -> 0.18 slope and was STILL rejected on
    2026-08-13, because the background fog stage then removed 46 % more of it:
    0.18 * (1 - 0.46) = 0.097 reached the pixel. F8 raises the day slope AND
    stops the second attenuation, so the same deep column delivers ~4x.
    """

    def delivered(depth_fade: float, daylight: float, exempt: bool) -> float:
        transmission = _bed_visibility(depth_fade, daylight)
        fog = depth_fade * 0.46
        if exempt:
            fog *= 1.0 - transmission * daylight
        return transmission * (1.0 - fog)

    # The rejected 3785e14dd route: the old 0.82 slope at full depth (0.18),
    # then the unexempted background fog stage (0.46).
    before = 0.18 * (1.0 - 0.46)
    after = delivered(1.0, 1.0, exempt=True)
    assert before == pytest.approx(0.0972)
    assert after == pytest.approx(0.385)
    assert after / before > 3.5
    # Mid depth keeps a genuinely intermediate value: the attenuation is
    # progressive across the three body bands, not a two-state cutoff.
    middle = delivered(0.5, 1.0, exempt=True)
    assert after < middle < delivered(0.0, 1.0, exempt=True)
    assert middle == pytest.approx(0.7069, abs=1e-4)
    # NIGHT delivery is untouched by both F8 factors.
    assert delivered(1.0, 0.0, exempt=True) == pytest.approx(0.0972)
    assert delivered(0.5, 0.0, exempt=True) == pytest.approx(
        delivered(0.5, 0.0, exempt=False)
    )


def test_fl4737_full_night_is_byte_equal_in_every_cell() -> None:
    """AD2 (2026-08-13). Revision 1 failed this and it is the reason for revision 2.

    Revision 1 deleted the submerged-source bed substitution WITHOUT a daylight
    gate. At full night the source's own coefficient was unchanged, which is how
    it slipped through, but the lake floor it uncovered appeared at 0.1476 where
    canon is 0.0000 and the body dropped 0.82 -> 0.6724. Revision 2 moves no
    composition site at all, so every term this slice adds is multiplied by zero
    at product_day_night_factor() == 1.
    """
    daylight = 0.0

    # 1. the transmission slope resolves to the night constant exactly
    assert _bed_visibility(1.0, daylight) == pytest.approx(0.18)
    assert _bed_visibility(0.5, daylight) == pytest.approx(0.59)

    # 2. the fog transmission exemption is exactly 1.0
    for depth_fade in (0.0, 0.25, 0.5, 0.75, 1.0):
        transmission = _bed_visibility(depth_fade, daylight)
        exemption = 1.0 - transmission * daylight
        assert exemption == 1.0
        assert depth_fade * 0.46 * exemption == pytest.approx(depth_fade * 0.46)

    # 3. the mirror cap sits inside a lane every write of which is already
    #    multiplied by clamp(daylight) == 0, so capping it cannot move a byte
    mirror_admission = 1.0 - _bed_visibility(1.0, daylight)
    assert 0.78 * daylight * 1.0 * mirror_admission == 0.0
    assert 0.56 * daylight * 1.0 * mirror_admission == 0.0

    # 4. THE REVISION-1 REGRESSION, as arithmetic. With the source removed from
    #    the bed, the night column re-splits and the lake floor becomes visible.
    transmission = _bed_visibility(1.0, daylight)
    canon_floor_coefficient = 0.0                     # source replaced the bed
    revision1_floor_coefficient = (1.0 - transmission) * transmission
    assert revision1_floor_coefficient == pytest.approx(0.1476, abs=1e-4)
    assert revision1_floor_coefficient != canon_floor_coefficient


def test_fl4737_mirror_cap_yields_only_where_water_is_see_through() -> None:
    """AD1: cap the ONE surface owner that erased the wrist, not all of them.

    The cap is `1 - T`. Where no direct source is transmitted T is 0, so the cap
    is exactly 1.0 and the reflection lane is arithmetically identical to its
    pre-F8 self. Where the column is thin enough to see a submerged solid the
    mirror yields to it, which is the physical statement as well as the fix.
    """
    def mirror(transmission: float) -> float:
        return 1.0 - transmission

    assert mirror(0.0) == 1.0                       # ordinary water: untouched
    assert mirror(_bed_visibility(1.0, 1.0)) == pytest.approx(0.50)
    assert mirror(_bed_visibility(0.1, 1.0)) == pytest.approx(0.05, abs=1e-9)

    # Delivered direct wrist through the WORST case (full reflection mass) at a
    # deep day cell, against the pre-slice route the operator falsified.
    transmission = _bed_visibility(1.0, 1.0)
    ink_before = 0.18 * (1.0 - 0.22) * (1.0 - 0.78)
    paper_before = 0.18 * (1.0 - 0.46) * (1.0 - 0.56)
    ink_after = transmission * (1.0 - 0.22) * (1.0 - 0.78 * mirror(transmission))
    paper_after = (
        transmission
        * (1.0 - 0.46 * (1.0 - transmission))
        * (1.0 - 0.56 * mirror(transmission))
    )
    assert ink_before == pytest.approx(0.0309, abs=1e-4)
    assert paper_before == pytest.approx(0.0428, abs=1e-4)
    assert ink_after == pytest.approx(0.2379, abs=1e-4)
    assert paper_after == pytest.approx(0.2772, abs=1e-4)
    assert ink_after / ink_before > 7.0
    assert paper_after / paper_before > 6.0
    # Where the reflection mass field has a gap the source is the full column
    # transmission, and near the waterline it is nearly the whole cell.
    assert transmission * (1.0 - 0.22) == pytest.approx(0.390, abs=1e-3)
    shallow = _bed_visibility(0.1, 1.0)
    assert shallow * (1.0 - 0.78 * mirror(shallow)) > 0.85

    # AD1 (b): the D3 cloud lift is not scaled by the mirror cap.
    cloud_delivery_in_source_cells = 1.0
    assert cloud_delivery_in_source_cells == 1.0
    # Revision 1's last-position composite delivered these instead:
    assert 1.0 - _bed_visibility(0.1, 1.0) == pytest.approx(0.05, abs=1e-9)
    assert 1.0 - _bed_visibility(1.0, 1.0) == pytest.approx(0.50)


def test_fl4737_direct_submerged_source_survives_the_surface_stages() -> None:
    """The wrist mechanism, as arithmetic.

    The source was never missing from the column -- it was overwritten by ONE
    surface owner. FL-4216 reflection ink (0.78) and reflected paper (0.56) at
    noon, against a source arriving at 0.18 transmission with 46 % of that fogged
    away, left 0.031 / 0.043 on the pixel. The later rejected F11 static band
    replaced both channels outright in its thin waterline interval.

    Revision 2 raises the transmission, stops the fog double-attenuation, and
    caps the mirror mix at (1 - T) in source-present cells only. It does not cap
    the cloud lift. F11 has since moved to the shared animated break state, so no
    direct-contact paint remains in the reflection helper. Delivered numbers are pinned in
    test_fl4737_mirror_cap_yields_only_where_water_is_see_through.
    """
    transmission_before = _bed_visibility(1.0, 0.0)   # 0.18, the old day slope
    ink_before = transmission_before * (1.0 - 0.22) * (1.0 - 0.78)
    paper_before = transmission_before * (1.0 - 0.46) * (1.0 - 0.56)
    assert ink_before == pytest.approx(0.0309, abs=1e-4)
    assert paper_before == pytest.approx(0.0428, abs=1e-4)
    transmission_after = _bed_visibility(1.0, 1.0)    # 0.50, the F8 day slope
    assert transmission_after == pytest.approx(0.50)
    # The weight is NOT the delivered share -- the surface stages still sit on
    # top of it, which is the whole point of revision 2. Delivery is strictly
    # less than the transmission and strictly more than the falsified route.
    delivered_ink = transmission_after * (1.0 - 0.22) * (
        1.0 - 0.78 * (1.0 - transmission_after)
    )
    assert ink_before < delivered_ink < transmission_after


def test_fl4737_depth_band_transition_is_continuous_across_daylight() -> None:
    """The normalized daytime band eases to the legacy raw-depth night band."""
    legacy = (0.25, 0.56, 0.60)
    normalized = (0.17, 0.46, 0.52)

    def blended(night_factor: float) -> tuple[float, float, float]:
        daylight = max(0.0, min(1.0, 1.0 - night_factor))
        return tuple(
            old + (new - old) * daylight
            for old, new in zip(legacy, normalized)
        )

    assert blended(0.0) == pytest.approx(normalized)
    assert blended(1.0) == pytest.approx(legacy)
    assert blended(0.5) == pytest.approx(
        tuple((old + new) * 0.5 for old, new in zip(legacy, normalized))
    )


def _rec709(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _cloud_sky_lift(sky, budget=0.11):
    """Mirror of the shader: the live sky colour rescaled to a fixed luma."""
    return tuple(channel * (budget / max(_rec709(sky), 0.0001)) for channel in sky)


def test_fl4737_cloud_lift_luma_budget_is_hue_independent() -> None:
    """rec709(lift) is EXACTLY the declared budget for any sky colour.

    The shader divides by the sky's own luminance, so the visible strength of the
    water cloud response is one number (FL4737_CLOUD_SKY_LIFT_LUMA) and does not
    drift as the sky warms toward dawn/dusk. Without that normalisation the noon
    sky (luma 0.838) and the low-sun sky (luma 0.706) would deliver lifts 19 %
    apart from the same constant.
    """
    noon = (0.792, 0.845, 0.907)      # RECEIPTED_DAY_SKY_COLOR
    low_sun = (0.82, 0.67, 0.59)      # product_day_water_sky_colour low-sun end
    for sky in (noon, low_sun):
        assert _rec709(_cloud_sky_lift(sky)) == pytest.approx(0.11)


def test_fl4737_cloud_lift_carries_sky_chromaticity_not_a_warm_offset() -> None:
    """The lift must read as sky light even on a fully transmitted bed.

    On shallow/contact cells bed_visibility reaches 1.0, so the resolved
    background IS the submerged terrain. The falsified form added
    ``(cloud_key - WATER_MID_BG)``, a DIFFERENCE of two colours: subtracting a
    teal leaves an R-dominant vector that tinted sand and rock warm. The
    replacement is the sky itself, so its chromaticity is the sky's by
    construction and a bed gains sky light rather than a red cast.
    """
    noon = (0.792, 0.845, 0.907)
    lift = _cloud_sky_lift(noon)
    # Same chromaticity as the sky: channel ratios preserved.
    for channel, sky_channel in zip(lift, noon):
        assert channel / lift[1] == pytest.approx(sky_channel / noon[1])
    # Blue-leaning, as a daytime sky is. The falsified difference vector was the
    # opposite: R-dominant by a wide margin.
    assert lift[2] > lift[0]
    falsified_key = tuple(
        0.65 * shallow + 0.35 * sky
        for shallow, sky in zip((0.34, 0.64, 0.66), noon)
    )
    falsified_delta = tuple(
        0.65 * (key - mid)
        for key, mid in zip(falsified_key, (0.25, 0.56, 0.60))
    )
    assert falsified_delta[0] > falsified_delta[2]


def test_fl4737_cloud_lift_is_depth_independent_after_fog() -> None:
    """Delivered lift is amount x budget at any depth WHERE NO CHANNEL CLIPS.

    XD3: the headline previously said "at EVERY depth", which the scope note and
    the model below both contradict -- neither models the clamp or the headroom.
    What this test covers is the pre-clamp term, which carries no depth weight.

    ``product_apply_day_water_pair`` fogs the background with
    ``mix(background, fog_target, 0.46 * depth_fade)``. ``mix`` is affine, so a
    term added BEFORE that stage survives scaled by ``1 - 0.46 * depth_fade``.
    This is what made the interim post-bed-mix placement still depth-dependent:
    it did not remove the depth law, it inverted it. Applying after fog is the
    only position that delivers a flat lift.

    sigma = 0.043 is the measured full-frame spread of water background luma
    (dump 1786570854_27480).

    PD2/VD1 SCOPE: these figures are the NON-CLIPPING delivery. The shader write
    is clamped to one and no no-clip bound is derivable, because the submerged
    bed has no ceiling in the file. A bright AUTHORED BED can therefore deliver
    less. Foam and wake do NOT clip on the witnessed rows -- an earlier revision
    of this sentence said they did, before the fog stage was included in the
    model. See ``test_fl4737_cloud_lift_delivery_on_the_live_chain``.
    """
    sigma = 0.043
    budget = 0.11
    common_band, dense_core = 0.42, 0.78

    def delivered_after_fog(amount):
        return amount * budget

    def delivered_before_fog(amount, depth_fade):
        return amount * budget * (1.0 - 0.46 * depth_fade)

    for depth_fade in (0.0, 0.25, 0.5, 0.75, 1.0):
        assert delivered_after_fog(common_band) == pytest.approx(0.0462)
        assert delivered_after_fog(dense_core) == pytest.approx(0.0858)
        # The acceptance bar the review set: >= 1 sigma common, ~2 sigma dense.
        assert delivered_after_fog(common_band) >= sigma
        assert delivered_after_fog(dense_core) / sigma == pytest.approx(2.0, abs=0.05)

    # The falsified placement fails that bar at depth, which is precisely the
    # defect the review caught.
    assert delivered_before_fog(common_band, 1.0) / sigma < 1.0


def test_fl4737_cloud_lift_preserves_depth_band_separation() -> None:
    """An additive term cannot collapse the three body bands into one colour.

    This is the invariant that separates the shipped shape from the rejected
    9cf571d7-predecessor blend, which replaced up to 78 % of every depth band
    with the same pale aqua. Here fog acts on the bands first and the identical
    lift is added to all of them afterwards, so every pairwise band difference
    survives the whole chain scaled only by the fog term they all share.

    PD2 SCOPE (Codex round 3): "exactly preserved" holds while no channel clips.
    The no-clipping assertion at the end of each case is what makes that precise
    -- these are the ordinary teal body bands, which cannot saturate. Under
    clipping the band ORDER survives only under the preconditions pinned in
    ``test_fl4737_cloud_lift_band_order_needs_its_preconditions``, and the
    differences COMPRESS. Delivery on the real chain is witnessed in
    ``test_fl4737_cloud_lift_delivery_on_the_live_chain``.
    """
    shallow = (0.34, 0.64, 0.66)
    mid = (0.25, 0.56, 0.60)
    deep = (0.17, 0.46, 0.52)
    fog_target = (0.18, 0.42, 0.50)
    lift = _cloud_sky_lift((0.792, 0.845, 0.907))

    def delivered(band, depth_fade, amount):
        w = 0.46 * depth_fade
        fogged = tuple(b + (f - b) * w for b, f in zip(band, fog_target))
        return tuple(
            min(1.0, max(0.0, c + l * amount)) for c, l in zip(fogged, lift)
        )

    for depth_fade in (0.0, 0.5, 1.0):
        w = 0.46 * depth_fade
        for amount in (0.0, 0.42, 0.78):
            a = delivered(shallow, depth_fade, amount)
            b = delivered(mid, depth_fade, amount)
            c = delivered(deep, depth_fade, amount)
            # Band differences are untouched by the lift: they carry only the
            # shared fog attenuation (1 - w), never the cloud amount.
            assert tuple(x - y for x, y in zip(a, b)) == pytest.approx(
                tuple((x - y) * (1.0 - w) for x, y in zip(shallow, mid))
            )
            assert tuple(x - y for x, y in zip(b, c)) == pytest.approx(
                tuple((x - y) * (1.0 - w) for x, y in zip(mid, deep))
            )
            # Nothing clips, so the separation is real and not a clamp artefact.
            for band in (a, b, c):
                assert all(0.0 < channel < 1.0 for channel in band)


def _live_water_chain(
    band,
    bed,
    depth_fade,
    shore_foam=0.0,
    wake=0.0,
    break_strength=0.0,
    amount=0.78,
):
    """The FULL live background chain, with every constant cited from the shader.

    WD1/WD2/WD3 (Codex round 5): earlier witnesses modelled a chain that the
    runtime never executes -- they omitted the fog stage between break and lift,
    and used a break weight of 0.16 that no live constructor can reach. This
    helper runs the real sequence end to end:

        water_body = band * motion_gain(<=1.045) * mottle_gain(<=1.10)   :3951
        bed mix    = mix(body, bed, 1 - depth_fade*mix(0.82,0.50,daylight))
        foam       = mix(bg, WATER_FOAM, shore_foam*0.18)
        wake       = mix(bg, wake_color, wake*0.72)      [gate wake>0.18]
        break      = mix(bg, break_wash, break_strength*0.10)
        fog        = mix(bg, WATER_FOG_COLOR,
                         depth_fade*0.46*(1 - transmission*daylight))
        lift       = min(bg_i + lift_i*amount, 1)

    ``depth_fade`` is not free: ``facts.depth_fade = clamp(water_depth/3, 0, 1)``
    (:3818), and shore foam needs ``water_depth > 0.04`` (:3907), so a foam cell
    ALWAYS carries nonzero fog. The break weight is 0.10 because every live
    constructor passes ``stable_cutover_paint = true`` (:3852, :11816); the 0.16
    arm is currently unreachable.
    """
    def mix(a, b, t):
        return tuple(x + (y - x) * t for x, y in zip(a, b))

    def clamp01(c):
        return tuple(min(max(x, 0.0), 1.0) for x in c)

    WATER_FOAM = (0.72, 0.94, 1.00)
    wake_color = mix(WATER_FOAM, (0.58, 0.88, 1.00), 0.35)
    break_wash = mix(WATER_FOAM, (0.58, 0.88, 1.00), 0.18)
    WATER_FOG_COLOR = (0.18, 0.42, 0.50)
    lift = _cloud_sky_lift((0.792, 0.845, 0.907))

    body = clamp01(tuple(c * 1.045 * 1.10 for c in band))
    # FL-4737 F8 (2026-08-13): these rows are DAYTIME rows (the cloud lift they
    # exist to measure is itself daylight-gated), so the transmission uses the
    # day slope and the fog stage exempts the transmitted fraction.
    bed_visibility = _bed_visibility(depth_fade, 1.0)
    bg = mix(body, bed, bed_visibility)
    bg = mix(bg, WATER_FOAM, shore_foam * 0.18)
    if wake > 0.18:
        bg = clamp01(mix(bg, wake_color, wake * 0.72))
    if break_strength > 0.0:
        bg = clamp01(mix(bg, break_wash, break_strength * 0.10))
    fog_amount = min(depth_fade, 1.0) * 0.46 * (1.0 - bed_visibility)
    bg = clamp01(mix(clamp01(bg), WATER_FOG_COLOR, fog_amount))
    after = tuple(min(b + l * amount, 1.0) for b, l in zip(bg, lift))
    return bg, after, _rec709(after) - _rec709(bg)


def test_fl4737_cloud_lift_delivery_on_the_live_chain() -> None:
    """WD1-WD3 / VD2: WITNESSED ROWS through the full live chain.

    These are specific input tuples, NOT a survey, and no universal delivery
    claim is made or implied. A universal claim is not available: the
    finite-water bed comes from water_submerged_bed_color (matlib terrain paint)
    or an authored submerged_source_bg, neither bounded in the file, and depth
    and foam each move the headroom. The bright-bed row below exists to
    exhibit a clipping case.

    What the rows DO establish: fog sits between foam/break and the lift and
    pulls the background toward (0.18, 0.42, 0.50), so on these inputs it leaves
    enough headroom that foam, break and depth do not themselves clip. That
    is why the earlier fog-less witnesses overstated the degradation.
    """
    SHALLOW, DEEP = (0.34, 0.64, 0.66), (0.17, 0.46, 0.52)
    # VD2: two DIFFERENT bed sources, previously conflated here.
    #   * the analytic backstop assigns facts.bed = WATER_DEEP_BG outright
    #     (:3855) -- a known constant;
    #   * the finite-water constructor takes the bed from
    #     water_submerged_bed_color (matlib terrain paint) or from an authored
    #     submerged_source_bg (:3832) -- NEITHER has a ceiling in the file.
    # For the finite-water rows we pick the one bed value the file names
    # outright, water_submerged_bed_color's fallback, and label it a CHOSEN
    # WITNESS INPUT rather than a typical or bounding bed.
    BACKSTOP_BED = DEEP                    # WATER_DEEP_BG, :3855
    WITNESS_BED = (0.06, 0.26, 0.40)       # chosen input, not a bound
    budget = 0.11

    # depth_fade is derived, not chosen: water_depth / 3.
    foam_df = 0.22 / 3.0                   # shallow_depth reaches 1.0 at 0.22
    deep_foam_df = 0.92 / 3.0              # ... and falls off after 0.92

    rows = (
        ("analytic backstop (bed=WATER_DEEP_BG)",
         DEEP, BACKSTOP_BED, 1.0, 0.0, 0.0, 0.0),
        ("foam cap @0.22", SHALLOW, WITNESS_BED, foam_df, 1.0, 0.0, 0.0),
        ("+ wake 1.0", SHALLOW, WITNESS_BED, foam_df, 1.0, 1.0, 0.0),
        ("+ break 1.0 (live 0.10)", SHALLOW, WITNESS_BED, foam_df, 1.0, 1.0, 1.0),
        ("foam+wake+break @0.92", SHALLOW, WITNESS_BED, deep_foam_df, 1.0, 1.0, 1.0),
    )
    for amount, sigma in ((0.78, 2.0), (0.42, 1.07)):
        for label, band, bed, df, foam, wake, brk in rows:
            bg, after, got = _live_water_chain(
                band, bed, df, foam, wake, brk, amount
            )
            assert got == pytest.approx(amount * budget), label
            assert all(c < 1.0 for c in after), label      # nothing clips
            assert got / 0.043 == pytest.approx(sigma, abs=0.01), label

    # The ONLY clipping case is a bright submerged bed. Its input is ASSUMED --
    # water_submerged_bed_color reads matlib terrain paint, which has no ceiling
    # in the shader -- so this row is labelled as such and not presented as a
    # derived bound.
    bright_bed = (0.95, 0.93, 0.90)
    bg, after, got = _live_water_chain(
        SHALLOW, bright_bed, foam_df, 1.0, 1.0, 1.0, 0.78
    )
    # FL-4737 F8 (2026-08-13) RECOMPUTED, and the change is in the honest
    # direction: the day transmission slope carries more of this bright bed
    # through, and the fog stage no longer pulls the composite down toward
    # (0.18, 0.42, 0.50) as hard, so the pre-lift background is BRIGHTER and the
    # clamp bites harder. Delivery on this assumed row drops from 95.4 % to
    # 87.7 % of the budget, and green now saturates alongside blue. That is the
    # documented cost of the transmission repair on a bright authored bed; the
    # rows above show it costs nothing where nothing clips.
    assert got == pytest.approx(0.075239, abs=1e-6)
    assert got / (0.78 * budget) == pytest.approx(0.8769, abs=0.002)
    assert got / 0.043 == pytest.approx(1.750, abs=0.01)
    assert after[2] == pytest.approx(1.0)          # blue tops out
    assert after[1] == pytest.approx(1.0)          # ... and now green too
    assert after[0] < 1.0
    bg42, after42, got42 = _live_water_chain(
        SHALLOW, bright_bed, foam_df, 1.0, 1.0, 1.0, 0.42
    )
    assert got42 / (0.42 * budget) == pytest.approx(0.9568, abs=0.002)

    # WD3: wake does NOT leave blue at 1.0 pre-lift and is NOT immune.
    # wake_color's blue is 1.0, but it is mixed FROM a background below 1 and
    # then fogged, so the pre-lift blue sits well short of saturation and the
    # full blue budget is delivered.
    bg, after, _ = _live_water_chain(
        SHALLOW, WITNESS_BED, foam_df, 1.0, 1.0, 0.0, 0.78
    )
    assert bg[2] == pytest.approx(0.8648, abs=1e-4)     # pre-lift blue, not 1.0
    assert bg[2] < 1.0
    blue_budget = _cloud_sky_lift((0.792, 0.845, 0.907))[2] * 0.78
    assert blue_budget == pytest.approx(0.09284, abs=1e-5)
    assert 1.0 - bg[2] > blue_budget                    # headroom exceeds budget
    assert after[2] - bg[2] == pytest.approx(blue_budget)   # 100 % delivered
    assert after[2] == pytest.approx(0.9576, abs=1e-4)
    # Only on the assumed bright bed does blue actually run out of headroom,
    # and there it delivers 41.5 % of the blue budget -- partial, not immune.
    bg_b, after_b, _ = _live_water_chain(
        SHALLOW, bright_bed, foam_df, 1.0, 1.0, 1.0, 0.78
    )
    assert bg_b[2] == pytest.approx(0.9777, abs=1e-4)
    assert (after_b[2] - bg_b[2]) / blue_budget == pytest.approx(0.240, abs=0.005)

    # --- invariants that hold on every row, clipping or not -------------------
    for amount in (0.0, 0.42, 0.78):
        for label, band, bed, df, foam, wake, brk in rows + (
            ("bright bed", SHALLOW, bright_bed, foam_df, 1.0, 1.0, 1.0),
        ):
            bg, after, got = _live_water_chain(
                band, bed, df, foam, wake, brk, amount
            )
            assert all(a >= b - 1e-12 for a, b in zip(after, bg)), label
            assert -1e-12 <= got <= amount * budget + 1e-12, label
            assert all(0.0 <= c <= 1.0 for c in after), label


def test_fl4737_cloud_lift_band_order_needs_its_preconditions() -> None:
    """QD3 (Codex round 4), re-stated on the live chain.

    ``min`` is monotone PER CHANNEL, so clipping preserves depth-band order only
    when the pre-lift backgrounds are ordered COMPONENTWISE -- not merely by luma
    -- and receive the SAME cloud amount. Arbitrary bed transmission, mottle,
    motion gain, foam and wake can all break componentwise ordering, and two
    cells under different cloud amounts are not comparable at all.

    This test asserts the preconditions explicitly wherever it relies on them,
    and exhibits a real pair that violates one and has its order INVERTED, so the
    invariant can never be restated as a general claim about neighbouring cells.
    """
    def delivered(background, amount):
        lift = _cloud_sky_lift((0.792, 0.845, 0.907))
        return tuple(min(b + l * amount, 1.0) for b, l in zip(background, lift))

    shallow = (0.34, 0.64, 0.66)
    mid = (0.25, 0.56, 0.60)
    deep = (0.17, 0.46, 0.52)
    # PRECONDITION 1: the three authored body bands are componentwise ordered.
    assert all(a > b for a, b in zip(shallow, mid))
    assert all(a > b for a, b in zip(mid, deep))

    bright = lambda band: tuple(min(c + 0.62, 1.0) for c in band)
    assert all(a >= b for a, b in zip(bright(shallow), bright(mid)))
    assert all(a >= b for a, b in zip(bright(mid), bright(deep)))

    # PRECONDITION 2: the SAME amount for all three cells.
    for amount in (0.42, 0.78):
        a = delivered(bright(shallow), amount)
        b = delivered(bright(mid), amount)
        c = delivered(bright(deep), amount)
        assert _rec709(a) >= _rec709(b) >= _rec709(c)      # order survives
        # ... while the DIFFERENCES compress under clipping.
        assert (_rec709(a) - _rec709(b)) <= (
            _rec709(shallow) - _rec709(mid)
        ) + 1e-12

    # The componentwise precondition is NECESSARY, not decorative: this real pair
    # is ordered by LUMA but not componentwise, and the clamp INVERTS its order.
    hi_luma = (0.642, 0.934, 0.823)
    lo_luma = (0.851, 0.850, 0.872)
    assert _rec709(hi_luma) > _rec709(lo_luma)                  # ordered by luma
    assert not all(x >= y for x, y in zip(hi_luma, lo_luma))    # not componentwise
    assert _rec709(delivered(hi_luma, 0.78)) < _rec709(
        delivered(lo_luma, 0.78)
    )                                                            # ORDER INVERTED
