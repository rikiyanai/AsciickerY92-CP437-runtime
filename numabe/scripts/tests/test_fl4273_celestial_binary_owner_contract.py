from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPOSITOR = ROOT / "godot_project" / "render" / "scene" / "lighting" / "CelestialFrameState.gd"
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"


def _sample_body(source: str) -> str:
    start = source.index("func sample_frame() -> void:")
    end = source.index("\nfunc night_enabled() -> bool:", start)
    return source[start:end]


def test_day_night_arc_exclusively_owns_the_binary_night_register() -> None:
    body = _sample_body(COMPOSITOR.read_text())

    assert "frame_night_factor > 0.5" in body
    assert "if cycle_enabled" in body
    assert "else static_night" in body
    assert "or static_night" not in body


def test_legacy_static_night_keeps_its_endpoint_when_the_arc_is_disabled() -> None:
    body = _sample_body(COMPOSITOR.read_text())

    assert "frame_time = 0.0 if static_night else 0.5" in body
    assert "frame_night_factor = 1.0 if static_night else 0.0" in body


def test_terrain_ambient_uses_the_continuous_celestial_owner() -> None:
    source = _expand_includes(PRODUCT.read_text())
    start = source.rindex("void product_apply_ambient_base(")
    end = source.index("\n}\n", start) + 2
    body = source[start:end]

    assert "float night = product_day_night_factor();" in body
    assert "product_apply_day_ambient_base(" in body
    assert "product_apply_night_ambient_base(" in body
    assert "float moon_ambient_weight = smoothstep(0.55, 1.0, night);" in body
    assert "fg = mix(day_fg, night_fg, moon_ambient_weight);" in body
    assert "bg = mix(day_bg, night_bg, moon_ambient_weight);" in body
    assert "dot(nrm, normalize(product_sun_dir()))" in body
    assert "dot(nrm, normalize(product_moon_dir()))" in body
    assert "day_fg, day_bg, nrm, day_lambert_raw" in body
    assert "night_fg, night_bg, night_lambert_raw" in body
    assert "static_night_enabled()" not in body
