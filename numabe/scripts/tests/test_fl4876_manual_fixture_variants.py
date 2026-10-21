#!/usr/bin/env python3
"""Visible Numabe fixture variants retain their hand-authored construction."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.population_asset_geometry import (  # noqa: E402
    CUSTOM_METRIC_FAMILY,
    CUSTOM_MODULES,
    CUSTOM_VARIANTS,
    build_custom_geometry,
    build_point_mesh,
)


def test_every_custom_module_and_variant_has_a_metric_family():
    # The asset planner indexes CUSTOM_METRIC_FAMILY for every module in
    # CUSTOM_MODULES and every CUSTOM_VARIANTS pair; a missing entry stops
    # the build with a bare KeyError deep into the run.
    assert not [m for m in CUSTOM_MODULES if m not in CUSTOM_METRIC_FAMILY]
    assert not ({m for m, _v in CUSTOM_VARIANTS} - set(CUSTOM_METRIC_FAMILY))

CONTRACT = {
    "cross_section_m": {
        "carriageway_width_m": 4.0,
    },
}


def _colours(vertices):
    return {tuple(vertex[6:9]) for vertex in vertices}


def test_stone_faced_shrub_bed_is_not_lowered_as_a_flower_pot():
    raised_v, raised_f = build_point_mesh(
        "potted_frontage_plant", "stone_faced_raised_shrub_bed",
        1.0, CONTRACT)
    pot_v, pot_f = build_point_mesh(
        "potted_frontage_plant", "terracotta_pot_cluster",
        1.0, CONTRACT)
    raised_extent = (
        max(v[0] for v in raised_v) - min(v[0] for v in raised_v),
        max(v[1] for v in raised_v) - min(v[1] for v in raised_v),
        max(v[2] for v in raised_v) - min(v[2] for v in raised_v),
    )
    assert raised_extent == (2.4, 1.12, 1.35)
    assert len(raised_f) > len(pot_f)
    assert {(105, 99, 84), (77, 73, 64), (57, 91, 51)} <= _colours(raised_v)
    assert raised_v != pot_v


def test_red_blue_notice_board_uses_its_observed_rectangular_variant():
    key = ("notice_sign", "red_blue_hedge_notice_board")
    assert key in CUSTOM_VARIANTS
    vertices, faces = build_custom_geometry(*key, scale=1.0)
    assert vertices and faces
    assert {(226, 226, 218), (190, 48, 45), (45, 84, 151),
            (213, 181, 61)} <= _colours(vertices)
    assert max(v[2] for v in vertices) == 1.45


def test_mature_broadleaf_frontage_tree_keeps_observed_dense_crown():
    vertices, faces = build_point_mesh(
        "street_tree", "mature_broadleaf_frontage_tree", 1.0, CONTRACT)
    assert faces
    extent = (
        max(v[0] for v in vertices) - min(v[0] for v in vertices),
        max(v[1] for v in vertices) - min(v[1] for v in vertices),
        max(v[2] for v in vertices) - min(v[2] for v in vertices),
    )
    assert extent == (4.25, 2.8, 6.6)
    assert {(99, 77, 55), (58, 100, 54), (66, 111, 59),
            (51, 91, 48), (72, 117, 62)} <= _colours(vertices)


def test_three_bicycle_cluster_is_not_the_generic_empty_rack():
    key = ("parked_bicycles", "three_bicycle_cluster_at_apartment")
    assert key in CUSTOM_VARIANTS
    vertices, faces = build_custom_geometry(*key, scale=1.0)
    assert faces
    assert min(v[0] for v in vertices) == -2.29
    assert max(v[0] for v in vertices) == 2.29
    assert max(v[2] for v in vertices) == 0.94
    assert {(181, 184, 180), (55, 61, 62), (177, 76, 72),
            (111, 105, 91)} <= _colours(vertices)


def test_red_stop_board_is_a_freestanding_rectangular_notice():
    key = ("sidewalk_signboard", "red_stop_parking_notice_board")
    assert key in CUSTOM_VARIANTS
    vertices, faces = build_custom_geometry(*key, scale=1.0)
    assert faces
    assert {(64, 66, 64), (184, 43, 40), (231, 229, 219)} <= _colours(vertices)
    assert max(v[2] for v in vertices) == 1.28


def test_seven_unit_ac_ledge_does_not_collapse_to_one_condenser():
    vertices, faces = build_custom_geometry(
        "exterior_ac_condenser",
        "seven_unit_two_tier_ledge_with_white_railing", 1.0)
    assert faces
    assert min(v[0] for v in vertices) == -2.05
    assert max(v[0] for v in vertices) == 1.75
    assert max(v[2] for v in vertices) == 1.58
    assert {(199, 201, 194), (78, 84, 84), (224, 225, 219)} <= _colours(vertices)


def test_station_bank_is_four_cabinets_not_one_generic_machine():
    key = ("vending_white_red_blue",
           "four_unit_bank_red_end_with_recycle_bins")
    assert key in CUSTOM_VARIANTS
    vertices, faces = build_custom_geometry(*key, scale=1.0)
    assert faces
    assert min(v[0] for v in vertices) == -2.225
    assert max(v[0] for v in vertices) == 2.225
    assert max(v[2] for v in vertices) == 1.9
    assert {(196, 52, 48), (228, 228, 222), (38, 46, 52),
            (120, 124, 119)} <= _colours(vertices)


def test_shopfront_bank_is_two_cabinets_not_one_generic_machine():
    key = ("vending_white_red_blue", "two_unit_red_and_white_bank")
    assert key in CUSTOM_VARIANTS
    vertices, faces = build_custom_geometry(*key, scale=1.0)
    assert faces
    assert min(v[0] for v in vertices) == -1.075
    assert max(v[0] for v in vertices) == 1.075
    assert max(v[2] for v in vertices) == 1.9
    assert {(196, 52, 48), (228, 228, 222), (56, 96, 168),
            (38, 46, 52)} <= _colours(vertices)


def test_shopfront_bicycles_are_four_units_not_an_empty_rack():
    key = ("parked_bicycles", "four_bicycle_shopfront_row")
    assert key in CUSTOM_VARIANTS
    vertices, faces = build_custom_geometry(*key, scale=1.0)
    assert faces
    assert min(v[0] for v in vertices) == -4.09
    assert max(v[0] for v in vertices) == 4.09
    assert max(v[2] for v in vertices) == 0.94
    assert {(48, 51, 52), (181, 184, 180), (55, 61, 62),
            (177, 76, 72), (111, 105, 91)} <= _colours(vertices)


def test_forecourt_pair_is_scooter_and_bicycle_not_two_bicycles():
    key = ("parked_bicycles", "scooter_and_bicycle_pair_in_marked_bays")
    assert key in CUSTOM_VARIANTS
    vertices, faces = build_custom_geometry(*key, scale=1.0)
    assert faces
    assert min(v[0] for v in vertices) == -1.05
    assert max(v[2] for v in vertices) == 1.05
    assert {(45, 48, 50), (70, 74, 76), (30, 32, 33),
            (48, 51, 52), (111, 105, 91)} <= _colours(vertices)


def test_height_limit_gantry_is_pole_arm_and_board():
    key = ("regulatory_sign_family", "height_limit_gantry_3_1m")
    assert key in CUSTOM_VARIANTS
    vertices, faces = build_custom_geometry(*key, scale=1.0)
    assert faces
    assert min(v[0] for v in vertices) == -0.08
    assert max(v[0] for v in vertices) == 3.3
    assert max(v[2] for v in vertices) == 4.5
    assert {(120, 124, 128), (200, 40, 40), (232, 232, 226),
            (45, 84, 151)} <= _colours(vertices)


def test_speed_limit_30_pole_is_pole_and_round_board():
    key = ("regulatory_sign_family", "speed_limit_30_pole")
    assert key in CUSTOM_VARIANTS
    vertices, faces = build_custom_geometry(*key, scale=1.0)
    assert faces
    assert min(v[0] for v in vertices) == -0.3
    assert max(v[0] for v in vertices) == 0.3
    assert max(v[2] for v in vertices) == 3.5
    assert {(130, 132, 134), (200, 40, 40),
            (232, 232, 226)} <= _colours(vertices)


def test_ac_row_is_six_units_not_one_mass():
    key = ("exterior_ac_condenser",
           "single_row_units_on_steel_stand_with_guard_rail")
    assert key in CUSTOM_VARIANTS
    vertices, faces = build_custom_geometry(*key, scale=1.0)
    assert faces
    assert min(v[0] for v in vertices) == -3.6
    assert max(v[0] for v in vertices) == 3.6
    assert max(v[2] for v in vertices) == 2.1
    assert {(121, 118, 130), (40, 38, 42),
            (224, 225, 219)} <= _colours(vertices)


def test_tarp_stall_is_canopy_over_crates():
    key = ("frontage_merchandise_display", "tarp_covered_market_stall")
    assert key in CUSTOM_VARIANTS
    vertices, faces = build_custom_geometry(*key, scale=1.0)
    assert faces
    assert max(v[2] for v in vertices) == 1.6
    assert {(136, 136, 138), (132, 105, 76)} <= _colours(vertices)


def test_mesh_gate_is_a_leaf_with_posts():
    key = ("metal_fence", "black_mesh_sliding_gate")
    assert key in CUSTOM_VARIANTS
    vertices, faces = build_custom_geometry(*key, scale=1.0)
    assert faces
    assert min(v[0] for v in vertices) == -1.25
    assert max(v[0] for v in vertices) == 1.25
    assert max(v[2] for v in vertices) == 1.85
    assert {(28, 28, 30)} <= _colours(vertices)


def test_hamacho_wall_sign_is_red_panel_with_white_characters():
    key = ("storefront_signboard",
           "red_painted_wall_sign_white_characters")
    assert key in CUSTOM_VARIANTS
    vertices, faces = build_custom_geometry(*key, scale=1.0)
    assert faces
    assert min(v[0] for v in vertices) == -2.2
    assert max(v[0] for v in vertices) == 2.2
    assert max(v[2] for v in vertices) == 4.4
    assert {(140, 30, 30), (235, 235, 235)} <= _colours(vertices)


def test_trackside_fence_is_posts_and_two_rails():
    key = ("rail_corridor_fence",
           "low_wooden_post_and_rail_trackside_fence")
    assert key in CUSTOM_VARIANTS
    vertices, faces = build_custom_geometry(*key, scale=1.0)
    assert faces
    assert max(v[2] for v in vertices) == 1.05
    assert {(70, 90, 65)} <= _colours(vertices)


def test_flat_slab_footbridge_has_no_rails():
    from scripts.pipeline.population_asset_geometry import build_unit_mesh
    contract = {"cross_section_m": {
        "carriageway_width_m": 4.0, "curb_height_m": 0.15,
        "sidewalk_width_m": 2.0, "tactile_paving_width_m": 0.3,
        "drainage_gutter_width_m": 0.5,
        "rail_boundary_fence_height_m": 1.1}}
    vertices, faces = build_unit_mesh(
        "waterway_bridge_railing", "flat_slab_footbridge_no_rails",
        1.6, 1.0, contract)
    assert faces
    assert min(v[0] for v in vertices) == -1.1
    assert max(v[0] for v in vertices) == 1.1
    assert max(v[2] for v in vertices) == 0.55
    assert {(188, 188, 196), (143, 143, 145)} <= _colours(vertices)


def test_strict_module_fixtures_carry_an_authored_variant():
    # build_unit_mesh raises for unknown variants of the guardrail family
    # and bollard_curb_island (the two raise sites in
    # population_asset_geometry). An accepted fixture of those modules
    # without an authored variant stops the asset build ten minutes in.
    import json
    strict = {"pedestrian_guardrail_continuous",
              "pedestrian_guardrail_vertical_bar_continuous",
              "bollard_curb_island"}
    doc = json.loads((ROOT / "assets/source_bundles"
                      "/numabe_connected_neighborhood_scope_20260915"
                      / "authored_scene_fixtures.json").read_text())
    bad = [f["fixture_id"] for f in doc["fixtures"]
           if (f.get("manual_review") or {}).get("status") == "accepted"
           and f.get("module_id") in strict
           and f.get("variant") in (None, "default")]
    assert not bad
