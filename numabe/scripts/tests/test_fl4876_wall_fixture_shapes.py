#!/usr/bin/env python3
"""FL-4876 CARDFIX-01. Wall fixtures are drawn as what they are, not as one generic panel.

build_reg_88_temple: 419 building_face_panel fixtures used one generic unit panel. AC condensers, bicycles and
vending machines became a 0-3 m glazed shopfront, every sign plate carried a dark 0.4-3.0 m strip, and every unit was
stretched to the face width. These tests pin the decided shape per module, the wall frame, the real sizes, and that
only the true full-face modules are still stretched unit panels.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.population_asset_geometry import (  # noqa: E402
    FULL_FACE_UNIT_MODULES,
    build_custom_geometry,
    build_unit_panel,
    build_wall_fixture_freestanding,
    unit_panel_basis,
    unit_panel_shape,
)

CONTRACT = {"sign_mounting_height_m": 1.8, "sign_plate_height_m": 1.2, "storefront_panel_height_m": 3.0}

# every (module, variant) that reached build_unit_panel in build_reg_88_temple (module, variant, count)
CENSUS = [
    ("street_fixture", "store_signboard", 52), ("street_fixture", "advertising_sign", 40),
    ("exterior_ac_condenser", "off_white_single_fan", 23), ("parked_bicycles", "default", 19),
    ("street_fixture", "traffic_sign", 16), ("sidewalk_signboard", "default", 15),
    ("poster_notice_carrier", "wall_mounted_poster_cluster", 12), ("storefront_signboard", "default", 12),
    ("sidewalk_signboard", "vertical_facade_shop_sign", 11), ("street_fixture", "banner_sign", 11),
    ("small_commercial_awning", "projecting_awning_generic", 10),
    ("storefront_signboard", "dark_fascia_white_text_unresolved", 9), ("vending_white_red_blue", "default", 9),
    ("rooftop_safety_railing", "painted_metal", 8), ("exterior_ac_condenser", "default", 7),
    ("waste_collection_bin_pair", "default", 7), ("storefront_signboard", "red_painted_wall_sign_white_characters", 6),
    ("roller_shutter_facade", "default", 6), ("notice_sign", "no_smoking_wall_notice_text_unresolved", 5),
    ("notice_sign", "default", 5), ("notice_sign", "freestanding_board_text_unresolved", 5),
    ("parked_bicycles", "parked_pair_with_basket", 5), ("storefront_signboard", "blue_fascia_sign_white_text", 5),
    ("frontage_merchandise_display", "default", 5), ("storefront_pedestal_sign", "narrow_vertical_shop_sign", 4),
    ("wall_mounted_mailbox", "gray_louvered_box", 4), ("glass_entry", "glazed_door_with_interior_display", 4),
    ("frontage_merchandise_display", "wooden_slatted_display_rack", 4),
    ("small_commercial_awning", "green_side_view_fabric", 4),
    ("roller_shutter_facade", "horizontal_shutter_with_tiled_base", 3),
    ("vending_white_red_blue", "white_red_blue_unit", 3), ("residential_carport_canopy", "dark_four_post_metal", 3),
    ("parked_bicycles", "three_bicycle_cluster_at_apartment", 3),
    ("small_commercial_awning", "dark_projecting_awning", 3),
    ("sidewalk_signboard", "pole_mounted_banner_cluster", 3),
    ("noren_or_shutter_entry", "restaurant_entry_with_sign_curtain", 3), ("wall_mounted_mailbox", "default", 3),
    ("hazard_strip_barrier", "red_white_horizontal_strip_barrier", 3),
    ("street_fixture", "information_board", 3), ("storefront_banner", "red_vertical_text_banner", 2),
    ("frontage_merchandise_crates", "default", 2), ("glass_entry", "red_framed_double_door", 2),
    ("poster_notice_carrier", "default", 2), ("concrete_wall_column", "roughcast_vertical_panel", 2),
    ("exterior_ac_condenser", "seven_unit_two_tier_ledge_with_white_railing", 1),
    ("independent_storefront_awning", "red_projecting_awning", 1),
    ("covered_waste_storage_enclosure", "pale_grey_single_curved_lid", 1),
    ("residential_facade_roofed_entry", "weathered_tile_roof", 1),
    ("storefront_signboard", "window_poster_panel", 1),
    ("small_commercial_awning", "curved_metal_awning_with_signband", 1),
    ("small_commercial_awning", "striped_fabric_awning", 1),
    ("exterior_ac_condenser", "single_row_units_on_steel_stand_with_guard_rail", 1),
    ("frontage_merchandise_display", "tarp_covered_market_stall", 1),
    ("notice_sign", "bulletin_board_with_regulatory_stack", 1), ("street_fixture", "generic_sign_plate", 5),
    ("street_fixture", "pedestrian_gate", 1), ("rooftop_safety_railing", "default", 1),
]


def _extent(vertices):
    return tuple(max(p[i] for p in vertices) - min(p[i] for p in vertices) for i in range(3))


def _build(module, variant):
    return build_unit_panel(module, variant, 1.0, CONTRACT)


@pytest.mark.parametrize("module,variant,_n", CENSUS)
def test_every_census_kind_has_a_decided_shape(module, variant, _n):
    shape = unit_panel_shape(module, variant)
    assert shape != "unmodelled", (module, variant)
    v, f = _build(module, variant)
    assert v and f
    if module in FULL_FACE_UNIT_MODULES:
        assert unit_panel_basis(module, variant) == "generated_unit_facade_panel"
    else:
        assert unit_panel_basis(module, variant) == "generated_wall_fixture"


@pytest.mark.parametrize("module,variant,_n", [r for r in CENSUS if r[0] not in FULL_FACE_UNIT_MODULES])
def test_a_wall_fixture_stands_in_the_wall_frame(module, variant, _n):
    """Back plane y = 0 (the lowering seats it on the wall), heights absolute from the ground, real size."""
    v, _ = _build(module, variant)
    standoff = unit_panel_shape(module, variant) in ("pedestal_sign", "pole_banner_cluster",
                                                      "freestanding_notice_board")
    if standoff:   # a post stands 0.12-0.2 m out from the wall
        assert 0.0 < min(p[1] for p in v) <= 0.2 + 1e-6
    else:
        assert min(p[1] for p in v) == pytest.approx(0.0, abs=1e-6)
    w, d, h = _extent(v)
    assert w < 9.0 and d < 4.0 and h < 5.0
    assert min(p[2] for p in v) > -0.15


@pytest.mark.parametrize("module,variant,_n", [r for r in CENSUS if r[0] not in FULL_FACE_UNIT_MODULES])
def test_no_wall_fixture_is_the_old_glazed_shopfront(module, variant, _n):
    """The default branch drew a 0-3 m shopfront (glass 0.5-2.6 m) for anything unnamed."""
    v, _ = _build(module, variant)
    glass = (150, 178, 184)
    full_height_glass = [p for p in v if tuple(p[6:9]) == glass and p[2] >= 2.5]
    tall = _extent(v)[2] >= 2.95 and min(p[2] for p in v) <= 0.01 and _extent(v)[0] >= 0.99 and _extent(v)[0] <= 1.01
    assert not (full_height_glass and tall), (module, variant)


def test_ac_condenser_is_a_small_box_at_the_ground():
    v, _ = _build("exterior_ac_condenser", "off_white_single_fan")
    w, d, h = _extent(v)
    assert 0.7 <= w <= 0.95 and d <= 0.45 and 0.6 <= h <= 0.8   # about 0.8 x 0.3 x 0.6 m
    assert min(p[2] for p in v) <= 0.0


def test_ac_condenser_front_faces_outward_after_reframing():
    """The freestanding mesh fronts -Y; in the wall frame the louvred face is the outward (+Y) one."""
    v, f = _build("exterior_ac_condenser", "off_white_single_fan")
    front = [p for p in v if p[4] > 0.9 and p[1] == max(q[1] for q in v)]
    assert front, "no +Y face at the outermost depth"
    grille = (78, 84, 84)
    assert any(tuple(p[6:9]) == grille for p in v if p[4] > 0.9)


def test_sign_plate_is_at_the_sign_band_with_no_strip():
    for module, variant in (("storefront_signboard", "default"),
                            ("storefront_signboard", "blue_fascia_sign_white_text"),
                            ("street_fixture", "store_signboard")):
        c = {**CONTRACT, "sign_mounting_height_m": 3.0} if module == "street_fixture" else CONTRACT
        v, _ = build_unit_panel(module, variant, 1.0, c)
        band_lo, band_hi = c["sign_mounting_height_m"], c["sign_mounting_height_m"] + c["sign_plate_height_m"]
        assert min(p[2] for p in v) >= band_lo - 1e-6
        assert max(p[2] for p in v) <= band_hi + 1e-6
        # the old dark strip ran 0.4 m to the band start under every plate
        assert not [p for p in v if p[2] < band_lo - 1e-6]


def test_a_sign_plate_is_not_stretched_to_a_face():
    v, _ = _build("storefront_signboard", "default")
    assert _extent(v)[0] == pytest.approx(2.0)
    assert unit_panel_basis("storefront_signboard", "default") == "generated_wall_fixture"


def test_posters_hang_between_1_2_and_2_0_m():
    for variant in ("wall_mounted_poster_cluster", "default"):
        v, _ = _build("poster_notice_carrier", variant)
        assert min(p[2] for p in v) >= 1.2 - 1e-6 and max(p[2] for p in v) <= 2.0 + 1e-6
    v, _ = _build("storefront_signboard", "window_poster_panel")
    assert min(p[2] for p in v) >= 1.0 and max(p[2] for p in v) <= 2.1


def test_a_display_rack_and_crates_stand_on_the_ground():
    for module, variant, top in (("frontage_merchandise_display", "default", 1.5),
                                 ("frontage_merchandise_display", "wooden_slatted_display_rack", 1.6),
                                 ("frontage_merchandise_crates", "default", 0.8)):
        v, _ = _build(module, variant)
        assert min(p[2] for p in v) == pytest.approx(0.0, abs=1e-6)
        assert max(p[2] for p in v) <= top


def test_bicycles_are_bicycles_not_a_panel():
    v, _ = _build("parked_bicycles", "default")
    w, d, h = _extent(v)
    assert h < 1.1 and d < 0.6 and w > 2.5            # two parked bikes, wheel height, not a 3 m shopfront
    assert min(p[2] for p in v) == pytest.approx(0.09, abs=0.01)


def test_the_full_face_modules_are_still_unit_panels_the_transform_stretches():
    for module in ("roller_shutter_facade", "rooftop_safety_railing", "concrete_wall_column",
                   "residential_facade_roofed_entry"):
        v, _ = _build(module, "default")
        assert _extent(v)[0] == pytest.approx(1.0, abs=0.01), module
        assert unit_panel_basis(module, "default") == "generated_unit_facade_panel"


def test_the_temple_agents_grave_block_variant_is_kept():
    v, f = build_custom_geometry("stone_marker_bollard", "grave_terrace_row_block", 1.0)
    assert v and f
    assert _extent(v)[0] == pytest.approx(9.6, abs=0.2)


@pytest.mark.parametrize("module,variant", [("storefront_signboard", "default"), ("poster_notice_carrier", "default"),
                                            ("exterior_ac_condenser", "default"),
                                            ("small_commercial_awning", "projecting_awning_generic")])
def test_a_refused_snap_fixture_stands_on_its_own(module, variant):
    """Freestanding form of a wall fixture: centred on y = 0, front on -Y, a post down to the ground."""
    v, f = build_wall_fixture_freestanding(module, variant, 1.0, CONTRACT)
    assert min(p[2] for p in v) <= 0.0 + 1e-6
    ys = [p[1] for p in v]
    assert min(ys) < 0 < max(ys)
    assert abs((min(ys) + max(ys)) / 2.0) < 0.01


def test_winding_is_kept_by_the_reframe():
    """A reframed mesh must keep outward-facing quads: every face normal agrees with its vertex order."""
    v, f = _build("exterior_ac_condenser", "default")
    for face in f:
        a, b, c = (v[i] for i in face[:3])
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        wx, wy, wz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        n = (uy * wz - uz * wy, uz * wx - ux * wz, ux * wy - uy * wx)
        dot = n[0] * a[3] + n[1] * a[4] + n[2] * a[5]
        assert dot >= -1e-9
