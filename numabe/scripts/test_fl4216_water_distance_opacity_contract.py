#!/usr/bin/env python3
"""Check the retained Product owner preserves finite-water visibility.

The probe-resolve copy was physically deleted by FL-4431.  This test now reads
the sole surviving Product shader and pins the short F8 body-band transition,
continuous terrain-bed transmission, and retained analytic-horizon ownership.
"""
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"


def require(source: str, needles: tuple[str, ...], label: str) -> None:
    missing = [needle for needle in needles if needle not in source]
    if missing:
        raise AssertionError(f"{label} missing contract rows: {missing}")


def main() -> int:
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))

    require(
        product,
        (
            "const vec3 WATER_SHALLOW_BG = vec3(0.34, 0.64, 0.66);",
            "const vec3 WATER_MID_BG = vec3(0.25, 0.56, 0.60);",
            "const vec3 WATER_DEEP_BG = vec3(0.17, 0.46, 0.52);",
            "const float WATER_SHALLOW_TRANSITION_END = 0.82;",
            "const float WATER_BED_VISIBILITY_FALLOFF = 0.82;",
            # FL-4737 F8 (2026-08-13): the daytime half of the same continuous
            # transmission law. The night slope above is unchanged, so a full
            # night frame keeps the accepted night bytes; daylight eases the
            # slope to 0.50 so the deepest admitted column still transmits half
            # the submerged input instead of 0.18. Operator falsifier: the lake
            # read as one opaque pale-teal fill with submerged terrain absent.
            "const float WATER_BED_VISIBILITY_FALLOFF_DAY = 0.50;",
            "uint product_water_depth_band(float depth_coordinate)",
            "uint depth_band = product_water_depth_band(s.depth_fade);",
            "uint legacy_depth_band = product_water_depth_band(s.water_depth);",
            "float depth_band_daylight = clamp(",
            "1.0 - product_day_night_factor(), 0.0, 1.0);",
            "product_day_water_body_band(legacy_depth_band),",
            "product_day_water_body_band(depth_band),",
            "depth_band_daylight);",
            "float bed_visibility_falloff = mix(",
            "WATER_BED_VISIBILITY_FALLOFF,",
            "WATER_BED_VISIBILITY_FALLOFF_DAY,",
            "depth_band_daylight);",
            "1.0 - s.depth_fade * bed_visibility_falloff,",
            "0.0, 1.0);",
            "s.bed_transmission = bed_visibility;",
            "s.bg = mix(water_body, bed, bed_visibility);",
        ),
        "product water",
    )
    require(
        product,
        (
            "input_value.facts.analytic_water = true;",
            "input_value.facts.analytic_water_footprint =",
            "input_value.facts.water_horizon_mix = backstop",
            "VSB_PRODUCT_WATER_HORIZON_FADE_START = 6.0;",
            "VSB_PRODUCT_WATER_HORIZON_FADE_END = 28.0;",
            "ProductFiniteWaterCellFacts product_analytic_water_cell_facts(",
            "facts.analytic_backstop = true;",
            "ProductFiniteWaterCellFacts water_facts = context.payload_from_backstop",
            "if (!cell_facts.analytic_backstop) {",
            "ProductSkyPaint horizon_sky = product_sky_paint(horizon_ray);",
            "fg = mix(fg, horizon_sky.background, facts.water_horizon_mix);",
            "bg = mix(bg, horizon_sky.background, facts.water_horizon_mix);",
        ),
        "retained distant-water continuity",
    )

    night_wave_contract = (
        "void product_apply_night_water_pair(",
        "vec3 night_foreground = clamp(",
        "vec3 night_background = clamp(",
        "float ordinary_activity = clamp(",
        "max(abs(s.wave_height), s.crest), 0.0, 1.0);",
        "float shoreline_break_activity = clamp(",
        "max(s.break_strength, s.break_active ? 1.0 : 0.0),",
        "s.wake_source_id != 0u ? s.wake : 0.0), 0.0, 1.0);",
        "vec3 ordinary_wave_blue = vec3(0.18, 0.28, 0.46);",
        "ordinary_activity * 0.006);",
        "night_foreground = mix(",
        "shoreline_break_activity * 0.78);",
        "float moon_envelope = night_moon_reflection_mask(",
        "float moon_glitter = night_moon_water_glitter(",
        "night_foreground = mix(",
        "clamp(moon_glitter * 1.35, 0.0, 1.0));",
        "background = mix(",
        "day_background, night_background, water_night_blend);",
    )
    require(product, night_wave_contract, "product night wave hierarchy")

    forbidden = (
        "vec3 hz_fg = probe_payload_fallback_fg(OWNER_WATER, family);",
        "vec3 hz_bg = probe_payload_fallback_bg(OWNER_WATER, family);",
        "bed_visibility = clamp(1.0 - s.depth_fade * 1.35, 0.0, 1.0) * 0.62;",
        "s.depth_fade * 1.35",
        "const float WATER_SURFACE_FILM_MIN = 0.30;",
        "const float RESOLVE_WATER_SURFACE_FILM_MIN = 0.30;",
        "WATER_SHALLOW_BED_MAX_VISIBILITY",
        "smoothstep(0.08, WATER_SHALLOW_TRANSITION_END, s.water_depth)",
        "WATER_SURFACE_FILM_MIN",
        "mix(WATER_SHALLOW_BG, WATER_DEEP_BG, s.depth_fade)",
        "product_day_water_depth_band(",
        "resolve_distant_water_reflection",
        "resolve_distant_water_fg",
        "resolve_distant_water_bg",
        "analytic_water_backstop && resolve_night_enabled()",
        "RESOLVE_ANALYTIC_WATER_FADE_START",
        "RESOLVE_ANALYTIC_WATER_FOOTPRINT_MAX",
        "horizon_fade",
        "bool analytic_water_backstop = analytic_water",
        "night = mix(night, wave_white, activity * 0.78);",
        "float night_scale = is_fg ? 0.34 : 0.24;",
        "vec3 night_water_paint(",
        "distant_water_sky_paint",
        "VSB_PRODUCT_WATER_SKY_FOOTPRINT",
        "context.surface_kind == OWNER_WATER && facts.distant_water_sky_paint",
    )
    survivors = [needle for needle in forbidden if needle in product]
    if survivors:
        raise AssertionError(f"superseded water owners remain: {survivors}")

    # Continuous transmission, both ends of the day/night arc. The slope is
    # mix(0.82, 0.50, daylight); night keeps the accepted 1.00 -> 0.18 curve and
    # day carries 1.00 -> 0.50. Neither end has a threshold or a cutoff.
    def visibility(depth_fade: float, daylight: float) -> float:
        falloff = 0.82 + (0.50 - 0.82) * daylight
        return max(0.0, min(1.0, 1.0 - depth_fade * falloff))

    night_zero, night_deep = visibility(0.0, 0.0), visibility(1.0, 0.0)
    day_zero, day_deep = visibility(0.0, 1.0), visibility(1.0, 1.0)
    if night_zero != 1.0 or abs(night_deep - 0.18) > 1.0e-9:
        raise AssertionError("night terrain-bed transmission endpoints drifted")
    if day_zero != 1.0 or abs(day_deep - 0.50) > 1.0e-9:
        raise AssertionError("day terrain-bed transmission endpoints drifted")
    # The rejected shapes: af58771a5's hard deep cutoff and the 0.18 daytime
    # floor the operator still rejected on 2026-08-13.
    if day_deep <= 0.18 or day_deep >= 1.0:
        raise AssertionError("day transmission fell back to a rejected shape")

    print("PASS: Product owns body bands, terrain transmission, and horizon paint")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
