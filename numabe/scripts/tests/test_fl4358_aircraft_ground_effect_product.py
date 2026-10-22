"""Focused source contract for FL-4358's bounded aircraft ground-effect Product lane."""
from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPOSITOR = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
PRODUCT = ROOT / "godot_project/render/product/cell_owner_product.glsl"
WORLD = ROOT / "godot_project/scripts/World.gd"


def _law(throttle: float, gap: float, grounded: bool, surface_valid: bool) -> float:
    """Mirror the documented cosmetic admission law, not gameplay movement."""
    if grounded or not surface_valid or not 0.15 <= throttle <= 1.0:
        return 0.0
    if not 0.0 <= gap <= 6.0:
        return 0.0
    return throttle * max(0.0, 1.0 - gap / 6.0)


def test_authoritative_surface_contract_reaches_clean_compositor_seam() -> None:
    world = WORLD.read_text()
    compositor = COMPOSITOR.read_text()
    for field in (
        '"velocity": authority_velocity',
        '"engine_throttle": engine_throttle',
        '"ground_effect_surface_valid"',
        '"ground_effect_surface_kind"',
        '"ground_effect_surface_y"',
        '"ground_effect_surface_gap"',
        '"ground_effect_surface_position"',
    ):
        assert field in world
    for consumer in (
        'fact.get("velocity", null)',
        'fact.get("engine_throttle", -1.0)',
        'fact.get("ground_effect_surface_valid", false)',
        'fact.get("ground_effect_surface_kind", "")',
        'fact.get("ground_effect_surface_y", NAN)',
        'fact.get("ground_effect_surface_gap", INF)',
        'fact.get("ground_effect_surface_position", null)',
    ):
        assert consumer in compositor
    assert "_aircraft_ground_effect_fact_is_admitted" in compositor
    assert 'bool(fact.get("ground_effect_surface_valid", false))' in compositor
    assert 'surface_kind != "water" and surface_kind != "land"' in compositor
    assert 'bool(fact.get("grounded", true))' in compositor


def test_hover_and_decay_law_are_power_gap_based_not_velocity_based() -> None:
    assert _law(0.8, 0.5, False, True) > 0.0  # hover: horizontal velocity is absent
    assert _law(0.8, 6.0, False, True) == 0.0
    assert _law(0.0, 0.5, False, True) == 0.0
    assert _law(0.8, 0.5, True, True) == 0.0
    assert _law(0.8, 0.5, False, False) == 0.0
    compositor = COMPOSITOR.read_text()
    assert "AIRCRAFT_GROUND_EFFECT_MAX_GAP_WORLD := 6.0" in compositor
    assert "strength := clampf(throttle, 0.0, 1.0) * proximity" in compositor
    assert "velocity.length()" in compositor
    assert "Velocity is consumed only for a stable phase key" in compositor


def test_multi_aircraft_records_are_bounded_and_source_sorted() -> None:
    compositor = COMPOSITOR.read_text()
    assert "AIRCRAFT_GROUND_EFFECT_MAX_RECORDS := 4" in compositor
    assert "if insert_at >= AIRCRAFT_GROUND_EFFECT_MAX_RECORDS" in compositor
    assert "selected.insert(insert_at, fact)" in compositor
    assert "selected.pop_back()" in compositor
    ground_effect = compositor.split("func _add_aircraft_ground_effect_records", 1)[1].split(
        "func _aircraft_ground_effect_fact_is_admitted", 1
    )[0]
    assert "pulse_downwash" not in ground_effect
    assert "push_wake_source_sample" not in ground_effect
    assert "rec.source_id = source_id" in ground_effect


def test_product_has_distinct_selected_water_and_land_consumers() -> None:
    product = _expand_includes(PRODUCT.read_text())
    compositor = COMPOSITOR.read_text()
    assert "LOCAL_LIGHT_KIND_AIRCRAFT_GROUND_EFFECT = 8" in product
    assert "aircraft_ground_effect_water_strength" in product
    assert "spray_color" in product
    assert "Product paints only this already-selected water receiver" in product
    assert 'glyph.glyph_id = 0x2A if surface_kind == "water" else 0xB0' in compositor
    assert 'glyph.glyph_flags = 128 if surface_kind == "water" else 64' in compositor
    assert 'glyph.color = Vector3(0.86, 0.97, 1.0) if surface_kind == "water" else rec.color' in compositor
    assert "LOCAL_LIGHT_FLAG_AIRCRAFT_GROUND_EFFECT_WATER" in product
    assert "kind != LOCAL_LIGHT_KIND_AIRCRAFT_GROUND_EFFECT" in product
    assert "kind == LOCAL_LIGHT_KIND_AIRCRAFT_GROUND_EFFECT" in product
    assert "|| kind == LOCAL_LIGHT_KIND_AIRCRAFT_GROUND_EFFECT" in product
