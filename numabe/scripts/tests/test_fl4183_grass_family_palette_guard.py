"""Structural and numeric contracts for the FL-4183 grass gamut guard."""

from __future__ import annotations
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"


def _function(source: str, name: str) -> str:
    match = re.search(rf"\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{", source, re.S)
    assert match is not None, f"missing function: {name}"
    brace = source.find("{", match.start())
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
    raise AssertionError(f"unterminated function: {name}")


def _compact(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"//[^\n]*", "", source)
    return re.sub(r"\s+", "", source)


def _replace_in_function(source: str, name: str, old: str, new: str) -> str:
    function = _function(source, name)
    assert function.count(old) == 1, (name, old, function.count(old))
    return source.replace(function, function.replace(old, new, 1), 1)


def _mix(left: float, right: float, amount: float) -> float:
    return left * (1.0 - amount) + right * amount


def _luma(color: tuple[float, float, float]) -> float:
    return sum(
        channel * weight
        for channel, weight in zip(color, (0.2126, 0.7152, 0.0722))
    )


def _smoothstep(edge0: float, edge1: float, value: float) -> float:
    t = max(0.0, min(1.0, (value - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


def _guard(
    color: tuple[float, float, float], daylight: float = 1.0
) -> tuple[float, float, float]:
    day = max(0.0, min(1.0, daylight))
    if day <= 0.0:
        return color

    peak = max(color)
    floor_value = min(color)
    chroma = peak - floor_value
    shoulder = (
        _smoothstep(0.72, 0.90, peak)
        * _smoothstep(0.30, 0.62, chroma)
        * day
    )
    if shoulder <= 0.0:
        return color

    luma = _luma(color)
    softened = tuple(_mix(channel, luma, shoulder * 0.18) for channel in color)
    softened_peak = max(softened)
    displayed_peak = (
        softened_peak
        if softened_peak <= 0.78
        else 0.78 + (softened_peak - 0.78) * 0.18
    )
    scale = displayed_peak / max(softened_peak, 0.0001)
    return tuple(max(0.0, min(1.0, channel * scale)) for channel in softened)


def _guard_pair(
    foreground: tuple[float, float, float],
    background: tuple[float, float, float],
    *,
    flower: bool,
    daylight: float = 1.0,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    guarded_foreground = foreground if flower else _guard(foreground, daylight)
    return guarded_foreground, _guard(background, daylight)


def _assert_source_contract(source: str) -> None:
    guard_source = _function(
        source, "q3_probe_payload_apply_grass_family_palette_guard"
    )
    guard = _compact(guard_source)
    color_guard = _compact(
        _function(source, "product_grass_family_palette_guard_color")
    )
    compile_path = _compact(_function(source, "compile_probe_subcell_payload"))

    assert (
        "boolgrass_family_surface=context.surface_kind==OWNER_TERRAIN||"
        "(context.surface_kind==OWNER_STATIC_MESH&&"
        "context.material==STATIC_WORLD_MOSS_MAT);" in guard
    )
    assert "family!=TERRAIN_FAMILY_GRASS" in guard
    assert (
        "boolflower=foliage_product_present&&"
        "foliage_product_role==FOLIAGE_ROLE_FLOWER;" in guard
    )
    assert "if(!flower){fg=product_grass_family_palette_guard_color(fg,daylight);}" in guard
    assert "bg=product_grass_family_palette_guard_color(bg,daylight);" in guard
    assert "glyph" not in guard
    assert "OWNER_WATER" not in guard
    assert "OWNER_DYNAMIC_MESH" not in guard
    assert "foliage_root_ramp_band" not in guard
    assert "cast_shadow_attenuation" not in guard
    assert guard.count("product_grass_family_palette_guard_color(") == 2
    assert source.count("q3_probe_payload_apply_grass_family_palette_guard(") == 4

    foliage_at = compile_path.index("q3_probe_payload_apply_foliage(")
    flower_fact_at = compile_path.index(
        "boolterrain_flower=context.surface_kind==OWNER_TERRAIN&&"
        "foliage_product_present&&"
        "foliage_product_role==FOLIAGE_ROLE_FLOWER;"
    )
    terrain_guard_needle = (
        "if(context.surface_kind==OWNER_TERRAIN&&!terrain_flower){"
        "q3_probe_payload_apply_grass_family_palette_guard("
    )
    assert terrain_guard_needle in compile_path
    terrain_guard_at = compile_path.index(terrain_guard_needle)
    dither_at = compile_path.index("q3_probe_payload_apply_surface_stable_dither(")
    flower_guard_at = compile_path.index(
        "if(terrain_flower){q3_probe_payload_apply_grass_family_palette_guard("
    )
    water_at = compile_path.index("q3_probe_payload_apply_water(")
    shadow_at = compile_path.index("q3_probe_payload_apply_shadow_and_lit(")
    moss_guard_at = compile_path.index(
        "if(context.surface_kind==OWNER_STATIC_MESH&&"
        "context.material==STATIC_WORLD_MOSS_MAT){"
        "q3_probe_payload_apply_grass_family_palette_guard("
    )
    debug_at = compile_path.index("q3_probe_payload_apply_debug(")
    assert (
        foliage_at
        < flower_fact_at
        < terrain_guard_at
        < dither_at
        < flower_guard_at
        < water_at
        < shadow_at
        < moss_guard_at
        < debug_at
    )
    assert (
        "context,family,foliage_product_present,foliage_product_role,fg,bg);"
        in compile_path
    )

    assert "vec3color,floatdaylight" in color_guard
    assert "floatday=clamp(daylight,0.0,1.0);" in color_guard
    assert "if(day<=0.0)returncolor;" in color_guard
    assert "smoothstep(0.72,0.90,peak)" in color_guard
    assert "smoothstep(0.30,0.62,chroma)" in color_guard
    assert "vec3softened=mix(color,vec3(luma),shoulder*0.18);" in color_guard
    assert "softened_peak<=0.78" in color_guard
    assert "0.78+(softened_peak-0.78)*0.18" in color_guard

    # The rejected bb40cf919 replacement palette must stay retired. The guard
    # may compress a fluorescent shoulder, but cannot install a second value,
    # root-band, paper, or shadow-hue owner.
    for retired in (
        "root_ramp_band",
        "root_luma",
        "target_luma",
        "muted_axis",
        "cool_axis",
        "warm_axis",
        "cast_shadow",
        "paper",
        "mix(color,muted_axis,day)",
    ):
        assert retired not in color_guard


def test_guard_is_one_late_grass_and_moss_owner() -> None:
    _assert_source_contract(_expand_includes(PRODUCT.read_text(encoding="utf-8")))


def test_terminal_nonblack_endpoint_wiring_abi() -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    floor = _compact(_function(source, "terrain_apply_nonblack_floor"))
    contrast = _compact(
        _function(source, "product_apply_glyph_bg_contrast_for_family")
    )
    compile_path = _compact(_function(source, "compile_probe_subcell_payload"))
    endpoint_call = re.compile(
        r"terrain_nonblack_floor\((family,(?:true|false))\)"
    )
    apply_call = re.compile(
        r"terrain_apply_nonblack_floor\((family,fg,bg)\);"
    )

    assert endpoint_call.findall(floor) == ["family,true", "family,false"]
    assert apply_call.findall(contrast) == ["family,fg,bg"]
    assert apply_call.findall(compile_path) == ["family,fg,bg"]


def test_ordinary_authored_grass_is_byte_for_byte_unchanged() -> None:
    for color in (
        (0.24, 0.42, 0.18),
        (0.40, 0.58, 0.31),
        (0.52, 0.67, 0.48),
        (0.31, 0.48, 0.52),
    ):
        assert _guard(color) == color


def test_fluorescent_shoulder_is_bounded_without_replacing_hue() -> None:
    warm = (0.62, 0.96, 0.05)
    cool = (0.05, 0.94, 0.55)
    guarded_warm = _guard(warm)
    guarded_cool = _guard(cool)

    assert max(guarded_warm) < max(warm)
    assert max(guarded_cool) < max(cool)
    assert guarded_warm[1] > guarded_warm[0] > guarded_warm[2]
    assert guarded_cool[1] > guarded_cool[2] > guarded_cool[0]
    assert guarded_warm != guarded_cool


def test_bright_authored_rows_remain_ordered_after_monotonic_compression() -> None:
    low = _guard((0.46, 0.78, 0.08))
    middle = _guard((0.53, 0.86, 0.08))
    high = _guard((0.60, 0.95, 0.08))
    assert _luma(low) < _luma(middle) < _luma(high)


def test_night_bypasses_and_dusk_interpolates_continuously() -> None:
    fluorescent = (0.51, 0.94, 0.10)
    night = _guard(fluorescent, daylight=0.0)
    dusk = _guard(fluorescent, daylight=0.5)
    day = _guard(fluorescent, daylight=1.0)
    assert night == fluorescent
    assert max(day) < max(dusk) < max(night)


def test_flower_foreground_stays_authored_while_fluorescent_paper_is_guarded() -> None:
    foreground = (0.91, 0.24, 0.70)
    fluorescent_paper = (0.51, 0.94, 0.10)
    guarded_fg, guarded_bg = _guard_pair(
        foreground, fluorescent_paper, flower=True
    )
    assert guarded_fg == foreground
    assert guarded_bg == pytest.approx(_guard(fluorescent_paper))
    assert guarded_bg != fluorescent_paper


@pytest.mark.parametrize(
    ("function", "old", "new"),
    (
        (
            "q3_probe_payload_apply_grass_family_palette_guard",
            "context.material == STATIC_WORLD_MOSS_MAT",
            "context.material == STATIC_WORLD_ROCK_MAT",
        ),
        (
            "q3_probe_payload_apply_grass_family_palette_guard",
            "foliage_product_role == FOLIAGE_ROLE_FLOWER",
            "foliage_product_role == FOLIAGE_ROLE_STEM",
        ),
        (
            "q3_probe_payload_apply_grass_family_palette_guard",
            "if (!flower) {",
            "if (flower) {",
        ),
        (
            "compile_probe_subcell_payload",
            "context.surface_kind == OWNER_TERRAIN && !terrain_flower",
            "context.surface_kind == OWNER_TERRAIN",
        ),
        (
            "product_grass_family_palette_guard_color",
            "smoothstep(0.72, 0.90, peak)",
            "smoothstep(0.20, 0.90, peak)",
        ),
        (
            "product_grass_family_palette_guard_color",
            "shoulder * 0.18",
            "shoulder * 0.80",
        ),
        (
            "product_grass_family_palette_guard_color",
            "0.78 + (softened_peak - 0.78) * 0.18",
            "0.50",
        ),
    ),
)
def test_damaging_owner_mutations_turn_red(
    function: str, old: str, new: str
) -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    mutant = _replace_in_function(source, function, old, new)
    with pytest.raises(AssertionError):
        _assert_source_contract(mutant)
