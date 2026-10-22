"""Physical geometry implementations owned by the population asset planner.

No A3D lowerer imports or defines these builders. The asset-plan stage authors
and validates every selected AKM before preassembly; lowerers consume only the
resulting immutable mesh manifest and resolved transforms.
"""
from __future__ import annotations

import hashlib
import math
import re

from scripts.pipeline.classic_procedural_mesh import (
    ALPHA_NO_COLLISION,
    ARCHETYPE_PHYSICAL,
    _add_box,
    _add_polygon_prism_xz,
    _build_archetype,
)
from scripts.pipeline.population_representation_contract import (
    FIXTURE_ARCHETYPE_BY_MODULE,
    LINEAR_RUN_POINT_MODULES,
    POINT_REALIZABLE_MODULES,
)

# FL-4876.  The modules build_point_mesh() below actually branches on,
# checked at import against the contract that decides which modules can be
# realized as a point.  A module the contract can point-realize but this
# builder cannot author is the defect that hid 3869 bollards.
_COVERED_POINT_MODULES = frozenset({
    "small_tree", "street_tree", "pole_streetlight", "manhole_and_grate",
    "meter_and_service_cabinet", "transformer_drum_cluster",
    "potted_frontage_plant", "level_crossing_equipment",
    "water_control_structure",
}) | LINEAR_RUN_POINT_MODULES
if _COVERED_POINT_MODULES != POINT_REALIZABLE_MODULES:
    raise ImportError(
        "population point geometry does not cover the representation "
        "contract's point-realizable modules; uncovered="
        + repr(sorted(POINT_REALIZABLE_MODULES - _COVERED_POINT_MODULES))
        + " unknown-to-contract="
        + repr(sorted(_COVERED_POINT_MODULES - POINT_REALIZABLE_MODULES)))

# module_id -> _build_archetype subtype (existing mesh path, reused by module id).
ARCHETYPE_BY_MODULE = FIXTURE_ARCHETYPE_BY_MODULE

# Modules needing a small custom composition (no fitting archetype subtype).
CUSTOM_MODULES = {
    "traffic_mirror", "convex_mirror",
    "pedestrian_guardrail_continuous", "pedestrian_guardrail_vertical_bar_continuous",
    "hazard_strip_barrier", "covered_waste_storage_enclosure",
    "waste_collection_bin_pair", "decorative_waterwheel",
    "exterior_ac_condenser",
    "residential_carport_canopy",
}

# A catalog module can normally reuse its physical archetype while one
# manually observed variant needs a distinct construction. Keep that
# exception narrower than promoting every variant of the module to custom.
CUSTOM_VARIANTS = {
    ("exterior_ac_condenser", "single_row_units_on_steel_stand_with_guard_rail"),
    ("frontage_merchandise_display", "tarp_covered_market_stall"),
    ("metal_fence", "black_mesh_sliding_gate"),
    ("notice_sign", "red_blue_hedge_notice_board"),
    ("parked_bicycles", "three_bicycle_cluster_at_apartment"),
    ("parked_bicycles", "four_bicycle_shopfront_row"),
    ("parked_bicycles", "scooter_and_bicycle_pair_in_marked_bays"),
    ("rail_corridor_fence", "low_wooden_post_and_rail_trackside_fence"),
    ("regulatory_sign_family", "height_limit_gantry_3_1m"),
    ("regulatory_sign_family", "speed_limit_30_pole"),
    ("sidewalk_signboard", "red_stop_parking_notice_board"),
    ("stone_marker_bollard", "grave_terrace_row_block"),
    ("storefront_signboard", "red_painted_wall_sign_white_characters"),
    ("vending_white_red_blue", "four_unit_bank_red_end_with_recycle_bins"),
    ("vending_white_red_blue", "two_unit_red_and_white_bank"),
}

CUSTOM_METRIC_FAMILY = {
    "traffic_mirror": "information_board",
    "convex_mirror": "information_board",
    "pedestrian_guardrail_continuous": "guardrail",
    "pedestrian_guardrail_vertical_bar_continuous": "guardrail",
    "hazard_strip_barrier": "gate_barrier",
    "covered_waste_storage_enclosure": "waste_storage",
    "waste_collection_bin_pair": "waste_basket",
    "decorative_waterwheel": "water_structure",
    "exterior_ac_condenser": "building_service_equipment",
    "residential_carport_canopy": "residential_frontage_structure",
    "frontage_merchandise_display": "frontage_display",
    "metal_fence": "gate_barrier",
    "notice_sign": "information_board",
    "parked_bicycles": "bicycle_parking",
    "rail_corridor_fence": "trackside_fence",
    "regulatory_sign_family": "traffic_sign",
    "sidewalk_signboard": "information_board",
    # FL-4876 GRAVEYARD-01: a kerbed, stone-faced terrace with grave plots is nearest the raised stone-faced bed
    "stone_marker_bollard": "planter_bed",
    "storefront_signboard": "advertising_sign",
    "vending_white_red_blue": "vending_unit",
}

def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


VARIANT_COLOURS = {
    "red": (196, 52, 48), "blue": (56, 96, 168), "green": (64, 128, 72),
    "yellow": (212, 178, 44), "white": (228, 228, 222), "black": (48, 48, 48),
    "gray": (150, 150, 144), "grey": (150, 150, 144), "wooden": (132, 105, 76),
    "orange": (214, 128, 44), "pink": (214, 130, 160),
}


def variant_rgb(variant: str | None, fallback: tuple[int, int, int]):
    if variant:
        for word, rgb in VARIANT_COLOURS.items():
            if word in variant.lower():
                return rgb
    return fallback


def _vertex_extent_m(vertices, scale: float) -> tuple[float, float, float]:
    xs = [float(v[0]) for v in vertices]
    ys = [float(v[1]) for v in vertices]
    zs = [float(v[2]) for v in vertices]
    return ((max(xs) - min(xs)) / scale,
            (max(ys) - min(ys)) / scale,
            (max(zs) - min(zs)) / scale)

def _add_vending_cabinet(v, f, cx, body, face, band, scale):
    """One 1 m Japanese drink cabinet. Local -Y is the product/front face."""
    _add_box(v, f, (cx - 0.50, cx + 0.50, -0.40, 0.40,
                    0.0, 1.90), body, scale)
    _add_box(v, f, (cx - 0.44, cx + 0.44, -0.415, -0.40,
                    0.55, 1.55), face, scale)
    _add_box(v, f, (cx - 0.44, cx + 0.44, -0.42, -0.415,
                    1.58, 1.84), band, scale)
    _add_box(v, f, (cx - 0.50, cx + 0.50, -0.40, 0.40,
                    1.84, 1.90), body, scale)


def _add_bicycle_unit(v, f, centre, frame, scale):
    """One parked bicycle facing local -Y, matching the SF-4876-0130 unit."""
    wheel = (48, 51, 52)
    for wx in (centre - 0.48, centre + 0.48):
        _add_box(v, f, (wx - 0.31, wx + 0.31, -0.035, 0.035,
                        0.09, 0.15), wheel, scale)
        _add_box(v, f, (wx - 0.31, wx + 0.31, -0.035, 0.035,
                        0.57, 0.63), wheel, scale)
        _add_box(v, f, (wx - 0.31, wx - 0.25, -0.035, 0.035,
                        0.15, 0.57), wheel, scale)
        _add_box(v, f, (wx + 0.25, wx + 0.31, -0.035, 0.035,
                        0.15, 0.57), wheel, scale)
    _diagonal_gate_bar(v, f, centre - 0.48, 0.36,
                       centre, 0.72, 0.055, frame, scale)
    _diagonal_gate_bar(v, f, centre, 0.72,
                       centre + 0.48, 0.36, 0.055, frame, scale)
    _diagonal_gate_bar(v, f, centre - 0.48, 0.36,
                       centre + 0.48, 0.36, 0.055, frame, scale)
    _add_box(v, f, (centre - 0.13, centre + 0.13,
                    -0.08, 0.08, 0.70, 0.77), frame, scale)
    _add_box(v, f, (centre + 0.42, centre + 0.49,
                    -0.04, 0.04, 0.35, 0.92), frame, scale)
    _add_box(v, f, (centre + 0.31, centre + 0.61,
                    -0.05, 0.05, 0.88, 0.94), frame, scale)
    _add_box(v, f, (centre + 0.48, centre + 0.78,
                    -0.18, 0.18, 0.64, 0.91), (111, 105, 91), scale)


def build_custom_geometry(module_id: str, variant: str | None, scale: float):
    """Archetype-geometry boxes for modules without a fitting _build_archetype."""
    v, f = [], []
    if module_id in ("traffic_mirror", "convex_mirror"):
        # Deleted-module traffic-mirror vocabulary: post plus reflector head.
        _add_box(v, f, (-0.055, 0.055, -0.055, 0.055, 0, 2.05), (120, 124, 117), scale)
        _add_box(v, f, (-0.30, 0.30, -0.08, 0.08, 1.72, 2.30), (224, 132, 48), scale)
        _add_box(v, f, (-0.23, 0.23, -0.10, -0.08, 1.80, 2.22), (178, 196, 194), scale)
    elif (module_id == "parked_bicycles"
          and variant == "three_bicycle_cluster_at_apartment"):
        # SF-4876-0130: three actual bicycles along the apartment frontage,
        # not the generic three-loop bicycle rack archetype.
        wheel = (48, 51, 52)
        frames = ((181, 184, 180), (55, 61, 62), (177, 76, 72))
        for centre, frame in zip((-1.50, 0.0, 1.50), frames):
            for wx in (centre - 0.48, centre + 0.48):
                _add_box(v, f, (wx - 0.31, wx + 0.31, -0.035, 0.035,
                                0.09, 0.15), wheel, scale)
                _add_box(v, f, (wx - 0.31, wx + 0.31, -0.035, 0.035,
                                0.57, 0.63), wheel, scale)
                _add_box(v, f, (wx - 0.31, wx - 0.25, -0.035, 0.035,
                                0.15, 0.57), wheel, scale)
                _add_box(v, f, (wx + 0.25, wx + 0.31, -0.035, 0.035,
                                0.15, 0.57), wheel, scale)
            _diagonal_gate_bar(v, f, centre - 0.48, 0.36,
                               centre, 0.72, 0.055, frame, scale)
            _diagonal_gate_bar(v, f, centre, 0.72,
                               centre + 0.48, 0.36, 0.055, frame, scale)
            _diagonal_gate_bar(v, f, centre - 0.48, 0.36,
                               centre + 0.48, 0.36, 0.055, frame, scale)
            _add_box(v, f, (centre - 0.13, centre + 0.13,
                            -0.08, 0.08, 0.70, 0.77), frame, scale)
            _add_box(v, f, (centre + 0.42, centre + 0.49,
                            -0.04, 0.04, 0.35, 0.92), frame, scale)
            _add_box(v, f, (centre + 0.31, centre + 0.61,
                            -0.05, 0.05, 0.88, 0.94), frame, scale)
            _add_box(v, f, (centre + 0.48, centre + 0.78,
                            -0.18, 0.18, 0.64, 0.91), (111, 105, 91), scale)
    elif module_id in ("pedestrian_guardrail_continuous",
                       "pedestrian_guardrail_vertical_bar_continuous",
                       "hazard_strip_barrier"):
        # Short rail run; variant picks the tube colour. Length is archetype
        # geometry (one AKM per module/variant), never placement spacing.
        rail = variant_rgb(variant, (218, 221, 214))
        band = (210, 139, 41) if "hazard" in module_id or "chevron" in (variant or "") else rail
        for x in (-2.0, 0.0, 2.0):
            _add_box(v, f, (x - 0.055, x + 0.055, -0.055, 0.055, 0, 1.05), rail, scale)
            _add_box(v, f, (x - 0.075, x + 0.075, -0.075, -0.057, 0.56, 0.70), band, scale)
        for z in (0.50, 0.94):
            _add_box(v, f, (-2.10, 2.10, -0.055, 0.055, z, z + 0.095), rail, scale)
    elif module_id == "covered_waste_storage_enclosure":
        # Single pale-grey Japanese frontage refuse enclosure.  The source
        # views show a rectangular lower cabinet and a rounded/sloped lid,
        # not two household bins.  Local -Y is the opening/front face.
        shell = (178, 183, 178)
        shade = (115, 122, 120)
        dark = (65, 72, 72)
        _add_box(v, f, (-0.625, 0.625, -0.325, 0.325, 0.00, 0.66), shell, scale)
        _add_box(v, f, (-0.625, 0.625, -0.325, -0.245, 0.62, 0.73), shade, scale)
        # Four shallow tiers approximate the observed curved lid while
        # retaining a readable stepped silhouette in the Classic renderer.
        _add_box(v, f, (-0.625, 0.625, -0.245, 0.325, 0.66, 0.78), shell, scale)
        _add_box(v, f, (-0.625, 0.625, -0.115, 0.325, 0.76, 0.88), shell, scale)
        _add_box(v, f, (-0.625, 0.625, 0.015, 0.325, 0.86, 0.97), shell, scale)
        _add_box(v, f, (-0.625, 0.625, 0.145, 0.325, 0.95, 1.05), shell, scale)
        _add_box(v, f, (-0.16, 0.16, -0.325, -0.315, 0.55, 0.64), dark, scale)
    elif module_id == "waste_collection_bin_pair":
        grey = (120, 124, 119)
        dark = (55, 62, 65)
        for x in (-0.35, 0.35):
            _add_box(v, f, (x - 0.25, x + 0.25, -0.25, 0.25, 0, 0.8), grey, scale)
            _add_box(v, f, (x - 0.18, x + 0.18, -0.26, -0.25, 0.55, 0.7), dark, scale)
    elif module_id == "decorative_waterwheel":
        wood = (122, 94, 64)
        dark = (70, 54, 38)
        _add_box(v, f, (-0.7, 0.7, -0.12, 0.12, 0, 1.5), wood, scale)
        _add_box(v, f, (-0.12, 0.12, -0.12, 0.12, 0, 1.5), dark, scale)
        _add_box(v, f, (-0.55, 0.55, -0.16, 0.16, 0.55, 0.75), wood, scale)
        _add_box(v, f, (-0.55, 0.55, -0.16, 0.16, 1.05, 1.25), wood, scale)
    elif (module_id == "exterior_ac_condenser"
          and variant != "single_row_units_on_steel_stand_with_guard_rail"):
        # Compact wall-mounted Japanese split-system outdoor unit.  Local -Y
        # is the visible/front face, matching other building-front fixtures.
        shell = (199, 201, 194)
        shade = (142, 147, 143)
        grille = (78, 84, 84)
        bracket = (105, 110, 108)
        def add_condenser(cx, cz):
            _add_box(v, f, (cx - 0.41, cx + 0.41, -0.17, 0.17,
                            cz, cz + 0.62), shell, scale)
            _add_box(v, f, (cx - 0.285, cx + 0.285, -0.181, -0.17,
                            cz + 0.08, cz + 0.50), shade, scale)
            _add_box(v, f, (cx - 0.245, cx + 0.245, -0.190, -0.181,
                            cz + 0.12, cz + 0.47), grille, scale)
            _add_box(v, f, (cx - 0.075, cx + 0.075, -0.198, -0.190,
                            cz + 0.24, cz + 0.37), shell, scale)
        if variant == "seven_unit_two_tier_ledge_with_white_railing":
            _add_box(v, f, (-2.05, 1.75, -0.05, 0.48,
                            0.18, 0.32), bracket, scale)
            for x in (-1.62, -0.70, 0.22, 1.14):
                add_condenser(x, 0.34)
            for x in (-1.20, -0.28, 0.64):
                add_condenser(x, 0.96)
            rail = (224, 225, 219)
            for x in (-2.00, -1.00, 0.0, 1.00, 1.70):
                _add_box(v, f, (x - 0.035, x + 0.035,
                                -0.52, -0.45, 0.0, 0.72), rail, scale)
            for z in (0.28, 0.66):
                _add_box(v, f, (-2.03, 1.73, -0.52, -0.45,
                                z, z + 0.055), rail, scale)
        else:
            add_condenser(0.0, 0.0)
            for x in (-0.34, 0.34):
                _add_box(v, f, (x - 0.035, x + 0.035, -0.21, 0.08,
                                -0.10, 0.00), bracket, scale)
    elif module_id == "residential_carport_canopy":
        frame = (58, 62, 61)
        roof = (76, 80, 78)
        underside = (42, 45, 44)
        # Local X follows the building face; negative Y projects toward the
        # street. Four slim posts support a shallow opaque metal roof.
        _add_box(v, f, (-2.40, 2.40, -3.60, 0.00, 2.15, 2.35), roof, scale)
        _add_box(v, f, (-2.32, 2.32, -3.52, -0.08, 2.10, 2.15), underside, scale)
        for x in (-2.25, 2.25):
            for y in (-3.42, -0.18):
                _add_box(v, f, (x - 0.055, x + 0.055,
                                y - 0.055, y + 0.055,
                                0.00, 2.18), frame, scale)
        _add_box(v, f, (-2.34, 2.34, -3.54, -3.42, 2.02, 2.18), frame, scale)
        _add_box(v, f, (-2.34, 2.34, -0.18, -0.06, 2.02, 2.18), frame, scale)
    elif (module_id == "notice_sign"
          and variant == "red_blue_hedge_notice_board"):
        post = (66, 70, 69)
        white = (226, 226, 218)
        red = (190, 48, 45)
        blue = (45, 84, 151)
        yellow = (213, 181, 61)
        _add_box(v, f, (-0.035, 0.035, -0.035, 0.035,
                        0.0, 1.45), post, scale)
        _add_box(v, f, (-0.38, 0.38, -0.055, 0.055,
                        0.72, 1.43), white, scale)
        _add_box(v, f, (-0.35, 0.35, -0.066, -0.055,
                        1.20, 1.38), red, scale)
        _add_box(v, f, (-0.35, 0.35, -0.066, -0.055,
                        0.79, 1.17), blue, scale)
        _add_box(v, f, (-0.17, 0.17, -0.065, 0.065,
                        0.48, 0.68), yellow, scale)
    elif (module_id == "sidewalk_signboard"
          and variant == "red_stop_parking_notice_board"):
        frame = (64, 66, 64)
        red = (184, 43, 40)
        white = (231, 229, 219)
        _add_box(v, f, (-0.33, 0.33, -0.055, 0.055,
                        0.18, 1.28), frame, scale)
        _add_box(v, f, (-0.29, 0.29, -0.066, -0.055,
                        0.23, 1.23), red, scale)
        _add_box(v, f, (-0.25, 0.25, -0.073, -0.066,
                        0.91, 1.16), white, scale)
        for x in (-0.23, -0.08, 0.08, 0.23):
            _add_box(v, f, (x - 0.025, x + 0.025,
                            -0.079, -0.073, 0.94, 1.13), red, scale)
        for x in (-0.24, 0.24):
            _add_box(v, f, (x - 0.035, x + 0.035,
                            -0.04, 0.04, 0.0, 0.20), frame, scale)
    elif (module_id == "vending_white_red_blue"
          and variant == "four_unit_bank_red_end_with_recycle_bins"):
        # SF-4876-0033: one physical bank on the station-side lane, four
        # cabinets in a row against the beige wall (red Coca-Cola at the SE
        # end, white ITO EN, two more white) with two grey recycle bins in
        # front. Local -Y is the product/front face toward the lane.
        red = (196, 52, 48)
        white = (228, 228, 222)
        glass = (38, 46, 52)
        lit = (238, 238, 230)
        green = (56, 120, 70)
        _add_vending_cabinet(v, f, -1.725, red, glass, lit, scale)
        _add_vending_cabinet(v, f, -0.575, white, glass, green, scale)
        _add_vending_cabinet(v, f, 0.575, white, glass, lit, scale)
        _add_vending_cabinet(v, f, 1.725, white, glass, lit, scale)
        for x in (-1.15, 0.0):
            _add_box(v, f, (x - 0.28, x + 0.28, -1.25, -0.75,
                            0.0, 0.78), (120, 124, 119), scale)
            _add_box(v, f, (x - 0.20, x + 0.20, -1.26, -1.25,
                            0.45, 0.62), (55, 62, 65), scale)
    elif (module_id == "vending_white_red_blue"
          and variant == "two_unit_red_and_white_bank"):
        # SF-4876-0250: the striped-awning shopfront bank, a red Coca-Cola
        # cabinet beside a white cabinet with a blue ticket band.
        _add_vending_cabinet(v, f, -0.575, (196, 52, 48),
                             (38, 46, 52), (238, 238, 230), scale)
        _add_vending_cabinet(v, f, 0.575, (228, 228, 222),
                             (38, 46, 52), (56, 96, 168), scale)
    elif (module_id == "parked_bicycles"
          and variant == "four_bicycle_shopfront_row"):
        # SF-4876-0251: four bicycles spread along the 225369230 shopfront
        # (one at the My Basket curb, three in front of the red-sign shop).
        frames = ((181, 184, 180), (55, 61, 62),
                  (177, 76, 72), (181, 184, 180))
        for centre, frame in zip((-3.30, -1.10, 1.10, 3.30), frames):
            _add_bicycle_unit(v, f, centre, frame, scale)
    elif (module_id == "parked_bicycles"
          and variant == "scooter_and_bicycle_pair_in_marked_bays"):
        # SF-4876-0403: a motor scooter with a rear top box beside one
        # bicycle in the white-marked bays of the 225198151 forecourt. The
        # white diagonal bars in the train-window view are the train's own
        # window frame, not a canopy, so no roof is authored.
        dark = (45, 48, 50)
        shell = (70, 74, 76)
        seat = (30, 32, 33)
        _add_box(v, f, (-1.05, -0.55, -0.80, 0.80,
                        0.10, 0.55), shell, scale)
        _add_box(v, f, (-0.95, -0.65, -0.85, -0.55,
                        0.55, 1.05), shell, scale)
        _add_box(v, f, (-0.95, -0.65, -0.60, 0.30,
                        0.55, 0.66), seat, scale)
        _add_box(v, f, (-1.00, -0.60, 0.60, 1.00,
                        0.70, 1.05), dark, scale)
        for wy in (-0.80, 0.80):
            _add_box(v, f, (-0.90, -0.70, wy - 0.07, wy + 0.07,
                            0.09, 0.15), dark, scale)
            _add_box(v, f, (-0.90, -0.70, wy - 0.07, wy + 0.07,
                            0.38, 0.44), dark, scale)
        _add_bicycle_unit(v, f, 0.90, (55, 61, 62), scale)
    elif (module_id == "regulatory_sign_family"
          and variant == "height_limit_gantry_3_1m"):
        # SLICE-S0-026: round 3.1 m height-limit board (red rim, blue
        # legend) carried on a horizontal pole arm over the carriageway.
        steel = (120, 124, 128)
        rim = (200, 40, 40)
        face = (232, 232, 226)
        legend = (45, 84, 151)
        _add_box(v, f, (-0.08, 0.08, -0.08, 0.08,
                        0.0, 4.5), steel, scale)
        _add_box(v, f, (-0.06, 3.0, -0.06, 0.06,
                        4.2, 4.35), steel, scale)
        _add_box(v, f, (2.4, 3.3, -0.45, 0.45,
                        3.5, 4.4), rim, scale)
        _add_box(v, f, (2.48, 3.22, -0.37, 0.37,
                        3.58, 4.32), face, scale)
        _add_box(v, f, (2.62, 3.08, -0.39, -0.37,
                        3.72, 4.18), legend, scale)
    elif (module_id == "regulatory_sign_family"
          and variant == "speed_limit_30_pole"):
        # SLICE-S0-027: round speed-limit 30 board on a steel pole.
        steel = (130, 132, 134)
        rim = (200, 40, 40)
        face = (232, 232, 226)
        _add_box(v, f, (-0.06, 0.06, -0.06, 0.06,
                        0.0, 3.5), steel, scale)
        _add_box(v, f, (-0.3, 0.3, -0.35, -0.25,
                        2.6, 3.2), rim, scale)
        _add_box(v, f, (-0.22, 0.22, -0.36, -0.25,
                        2.68, 3.12), face, scale)
    elif (module_id == "exterior_ac_condenser"
          and variant == "single_row_units_on_steel_stand_with_guard_rail"):
        # SLICE-S3N-001: six individual commercial outdoor units in a row
        # on a dark steel stand with a white base frame bar, not one mass.
        shell = (121, 118, 130)
        dark = (40, 38, 42)
        rail = (224, 225, 219)
        for cx in (-3.0, -1.8, -0.6, 0.6, 1.8, 3.0):
            _add_box(v, f, (cx - 0.50, cx + 0.50, -0.30, 0.30,
                            0.50, 2.10), shell, scale)
            _add_box(v, f, (cx - 0.40, cx + 0.40, -0.31, -0.30,
                            0.70, 1.90), dark, scale)
        _add_box(v, f, (-3.60, 3.60, -0.30, 0.30,
                        0.00, 0.50), dark, scale)
        _add_box(v, f, (-3.60, 3.60, -0.34, -0.30,
                        0.10, 0.22), rail, scale)
    elif (module_id == "frontage_merchandise_display"
          and variant == "tarp_covered_market_stall"):
        # SLICE-S3N-007: tarp canopy over a stall table with crates beneath.
        _add_box(v, f, (-1.0, 1.0, -0.6, 0.6,
                        0.0, 1.6), (136, 136, 138), scale)
        _add_box(v, f, (-0.8, 0.8, -0.4, 0.4,
                        0.0, 0.8), (132, 105, 76), scale)
    elif (module_id == "metal_fence"
          and variant == "black_mesh_sliding_gate"):
        # SLICE-S3N-009: one 2.4 m black mesh sliding gate leaf, 1.8 high.
        leaf = (28, 28, 30)
        _add_box(v, f, (-1.2, 1.2, -0.04, 0.04,
                        0.0, 1.8), leaf, scale)
        _add_box(v, f, (-1.2, 1.2, -0.05, 0.05,
                        1.70, 1.80), leaf, scale)
        for x in (-1.2, 1.2):
            _add_box(v, f, (x - 0.05, x + 0.05, -0.05, 0.05,
                            0.0, 1.85), leaf, scale)
    elif (module_id == "storefront_signboard"
          and variant == "red_painted_wall_sign_white_characters"):
        # SLICE-S3N-012: red painted wall sign panel (hamacho) with white
        # raised characters, mounted high on the wall face.
        _add_box(v, f, (-2.2, 2.2, -0.05, 0.05,
                        2.6, 4.4), (140, 30, 30), scale)
        _add_box(v, f, (-2.0, 2.0, -0.06, 0.05,
                        2.8, 4.2), (235, 235, 235), scale)
    elif (module_id == "rail_corridor_fence"
          and variant == "low_wooden_post_and_rail_trackside_fence"):
        # SLICE-S3N-015: weathered 6 m post-and-rail run: pointed-cap
        # posts with two horizontal rails, grey-green as read.
        wood = (70, 90, 65)
        for x in (-3.0, -1.5, 0.0, 1.5, 3.0):
            _add_box(v, f, (x - 0.07, x + 0.07, -0.07, 0.07,
                            0.0, 0.95), wood, scale)
            _add_box(v, f, (x - 0.07, x + 0.07, -0.07, 0.07,
                            0.95, 1.05), wood, scale)
        for z in (0.35, 0.75):
            _add_box(v, f, (-3.0, 3.0, -0.05, 0.05,
                            z, z + 0.10), wood, scale)
    elif (module_id == "stone_marker_bollard"
          and variant == "grave_terrace_row_block"):
        _add_grave_terrace_block(v, f, scale)
    return v, f


# FL-4876 GRAVEYARD-01 (有慶山東光院 graveyard, OSM way/225102214).  Plan-view dimensions come from the operator's
# satellite (operator_ref_b): grave plots in rows on stepped stone terraces, rows running NE-SW; a plot is read as
# 1.6 m x 1.7 m (rows ~1.7 m apart, ~1.6 m between markers); the terrace stands 0.9 m above the next lower one
# (retaining wall in the isometric drawing operator_ref_a); stone and gravel colours read from the same pictures.
GRAVE_BLOCK_X_M = 9.6      # along the rows (local +x)
GRAVE_BLOCK_Y_M = 7.2      # across the terrace (local +y = the low side, where the retaining wall stands)
GRAVE_COLS = 6
GRAVE_ROWS = 4
GRAVE_WALL_TOP_M = 0.9


def _add_grave_marker(v, f, cx, cy, scale):
    """One Japanese grave plot: stone kerb, gravel, three-tier gravestone (芝台, 中台, 竿石 with a chamfered cap)."""
    kerb, gravel = (164, 160, 152), (184, 178, 164)
    base, mid, shaft, cap = (150, 148, 142), (140, 138, 132), (118, 116, 112), (132, 130, 124)
    z0 = GRAVE_WALL_TOP_M
    hx, hy, kw = 0.70, 0.76, 0.10
    _add_box(v, f, (cx - hx, cx + hx, cy - hy, cy + hy, z0, z0 + 0.02), gravel, scale)
    for x0, x1, y0, y1 in ((cx - hx, cx + hx, cy - hy, cy - hy + kw), (cx - hx, cx + hx, cy + hy - kw, cy + hy),
                           (cx - hx, cx - hx + kw, cy - hy, cy + hy), (cx + hx - kw, cx + hx, cy - hy, cy + hy)):
        _add_box(v, f, (x0, x1, y0, y1, z0, z0 + 0.16), kerb, scale)
    _add_box(v, f, (cx - 0.34, cx + 0.34, cy - 0.30, cy + 0.34, z0 + 0.02, z0 + 0.16), base, scale)
    _add_box(v, f, (cx - 0.26, cx + 0.26, cy - 0.22, cy + 0.26, z0 + 0.16, z0 + 0.30), mid, scale)
    _add_box(v, f, (cx - 0.15, cx + 0.15, cy - 0.12, cy + 0.12, z0 + 0.30, z0 + 1.00), shaft, scale)
    _add_box(v, f, (cx - 0.11, cx + 0.11, cy - 0.09, cy + 0.09, z0 + 1.00, z0 + 1.08), cap, scale)


def _add_grave_terrace_block(v, f, scale):
    """One stepped graveyard terrace: stone-faced fill with a retaining wall on its low (+y) side and 4 rows of
    6 grave plots.  Local +x runs along the rows; the block is centred on the placement point."""
    wall, joint, coping = (139, 137, 129), (108, 106, 100), (160, 156, 148)
    hx, hy = GRAVE_BLOCK_X_M / 2, GRAVE_BLOCK_Y_M / 2
    top = GRAVE_WALL_TOP_M
    _add_box(v, f, (-hx, hx, -hy, hy - 0.4, -0.6, top), (150, 146, 136), scale)       # terrace fill (earth/gravel)
    _add_box(v, f, (-hx, hx, hy - 0.4, hy, -0.6, top), wall, scale)                   # retaining wall, buried foot
    for z in (0.0, 0.3, 0.6):                                                         # ashlar course joints
        _add_box(v, f, (-hx, hx, hy, hy + 0.015, z, z + 0.025), joint, scale)
    _add_box(v, f, (-hx - 0.04, hx + 0.04, hy - 0.46, hy + 0.06, top, top + 0.08), coping, scale)
    for r in range(GRAVE_ROWS):
        cy = hy - 1.25 - 1.7 * r
        for c in range(GRAVE_COLS):
            _add_grave_marker(v, f, -hx + (c + 0.5) * GRAVE_BLOCK_X_M / GRAVE_COLS, cy, scale)



def _legacy_unit_panel(module_id: str, variant: str | None, scale: float,
                       asset_contract: dict):
    """Unit facade panel: 1 m wide in local X, thin in Y, per-module height.

    FL-4876 CARDFIX-01: only the true full-face modules in FULL_FACE_UNIT_MODULES
    still come through here (the transform scales local X to the face width).
    It used to draw EVERY building_face_panel fixture (419 in build_reg_88): AC
    condensers, bicycles, vending machines and sign plates all became a 1 m
    glazed shopfront or a plate with a dark 0.4-3.0 m strip, stretched to the
    face width. See build_unit_panel / build_wall_fixture for the real shapes.
    """
    v, f = [], []
    profile = asset_contract.get("facade_profile") or {}
    glass = tuple(profile.get("glass_rgb") or (150, 178, 184))
    wall = tuple(profile.get("wall_rgb") or
                 variant_rgb(variant, (186, 180, 166)))
    dark = tuple(max(20, c - 60) for c in wall)
    trim = tuple(profile.get("trim_rgb") or
                 tuple(min(255, c + 30) for c in wall))
    sign_mount = float(asset_contract["sign_mounting_height_m"])
    sign_plate = float(asset_contract["sign_plate_height_m"])
    storefront_h = float(asset_contract["storefront_panel_height_m"])
    if "awning" in module_id:
        h, colour = 0.5, wall
        _add_box(v, f, (-0.5, 0.5, 0.0, 1.0, 2.5, 2.5 + h), colour, scale)
        _add_box(v, f, (-0.5, 0.5, 0.92, 1.0, 1.9, 2.5), dark, scale)
    elif "banner" in module_id:
        h = sign_mount + sign_plate
        _add_box(v, f, (-0.5, 0.5, -0.03, 0.03, sign_mount, h), wall, scale)
    elif "sign" in module_id or "shutter" in module_id or "poster" in module_id:
        _add_box(v, f, (-0.5, 0.5, -0.04, 0.04, sign_mount,
                        sign_mount + sign_plate), wall, scale)
        _add_box(v, f, (-0.5, 0.5, -0.05, -0.04, 0.4, sign_mount), dark, scale)
    elif module_id == "lowrise_roof_edge":
        _add_box(v, f, (-0.5, 0.5, -0.05, 0.05, storefront_h - 1.0,
                        storefront_h), dark, scale)
    elif "railing" in module_id:
        _add_box(v, f, (-0.5, 0.5, -0.04, 0.04, storefront_h,
                        storefront_h + 1.1), wall, scale)
    elif "alley" in module_id or "column" in module_id:
        _add_box(v, f, (-0.5, 0.5, -0.06, 0.06, 0, storefront_h), dark, scale)
    else:  # storefront glazing / entry / produce / crates
        _add_box(v, f, (-0.5, 0.5, -0.05, 0.05, 0, 0.5), dark, scale)
        _add_box(v, f, (-0.5, 0.5, -0.04, 0.04, 0.5, 2.6), glass, scale)
        _add_box(v, f, (-0.5, 0.5, -0.05, 0.05, 2.6, storefront_h), wall, scale)
    if profile and "sign" in module_id:
        _add_box(v, f, (-0.48, 0.48, -0.055, -0.045,
                        sign_mount + 0.08,
                        min(sign_mount + sign_plate - 0.08,
                            sign_mount + 0.22)), trim, scale)
    return v, f


# ---------------------------------------------------------------------------------------------------------------------
# FL-4876 CARDFIX-01. Wall-mounted and frontage fixtures drawn as what they are.
#
# Wall frame of every mesh built below: local +X runs along the wall, local +Y is the outward normal, y = 0 is the BACK
# of the object (the lowering seats that plane on the wall the player sees), z is metres above the ground it stands on
# and is ABSOLUTE (the instance transform adds no height; before this the planner's 1.8 m sign height was added on top
# of heights already in the mesh). The mesh keeps its real width: the transform scales X only to fit a face that is
# shorter than the object. Only FULL_FACE_UNIT_MODULES are unit meshes stretched to the face width.
#
# Census, build_reg_88_temple, 419 building_face_panel instances that used the one generic panel (see
# unit_panel_shape for the decision per module): store/advertising/traffic street signs 125, sidewalk and storefront
# signboards 124, AC condensers 32, bicycles 31, vending 17, poster/notice carriers 31, awnings 28, displays 14, ...
FULL_FACE_UNIT_MODULES = frozenset({
    "roller_shutter_facade", "rooftop_safety_railing", "concrete_wall_column",
    "residential_facade_roofed_entry", "lowrise_roof_edge",
})

_STREET_SIGN_SUBTYPES = frozenset({
    "store_signboard", "advertising_sign", "banner_sign", "traffic_sign", "generic_sign_plate", "information_board",
    "pedestrian_gate"})


def unit_panel_shape(module_id: str, variant: str | None) -> str:
    """Shape key for one building_face_panel unit fixture; "legacy_full_face" for a stretched face-wide module and
    "unmodelled" for a module with no decided shape (it still gets the legacy panel)."""
    m, var = module_id, (variant or "")
    if m in FULL_FACE_UNIT_MODULES:
        return {"roller_shutter_facade": "roller_shutter",
                "residential_facade_roofed_entry": "roofed_entry"}.get(m, "legacy_full_face")
    if m in CUSTOM_MODULES or (m, variant) in CUSTOM_VARIANTS:
        return "custom"
    if m == "street_fixture" and var in _STREET_SIGN_SUBTYPES:
        return "street_" + var
    if m == "parked_bicycles":
        return "bicycles"
    if m == "vending_white_red_blue":
        return "vending"
    if m == "wall_mounted_mailbox":
        return "mailbox"
    if m == "storefront_signboard":
        return "window_poster" if var == "window_poster_panel" else "fascia_sign"
    if m == "storefront_pedestal_sign":
        return "pedestal_sign"
    if m == "sidewalk_signboard":
        return ("pole_banner_cluster" if var == "pole_mounted_banner_cluster"
                else "blade_sign" if "vertical_facade_shop_sign" in var else "a_frame_board")
    if m == "storefront_banner":
        return "wall_banner"
    if m == "notice_sign":
        return ("freestanding_notice_board" if var.startswith("freestanding_board")
                else "bulletin_board" if var.startswith("bulletin_board") else "notice_plate")
    if m == "poster_notice_carrier":
        return "poster_cluster"
    if m in ("small_commercial_awning", "independent_storefront_awning"):
        return "awning"
    if m == "glass_entry":
        return "glass_entry"
    if m == "noren_or_shutter_entry":
        return "noren_entry"
    if m == "frontage_merchandise_display":
        return "display_rack"
    if m == "frontage_merchandise_crates":
        return "crates"
    return "unmodelled"


def unit_panel_basis(module_id: str, variant: str | None) -> str:
    """Metric basis of the mesh build_unit_panel authors: a stretched unit panel or a real-size wall fixture."""
    shape = unit_panel_shape(module_id, variant)
    return ("generated_unit_facade_panel" if shape in ("legacy_full_face", "roller_shutter", "roofed_entry",
                                                       "unmodelled")
            else "generated_wall_fixture")


def _luma(rgb) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def _fascia_rgb(variant: str | None, fallback=(226, 222, 210)):
    """Board colour from the leading colour word of the variant ('dark_fascia_white_text': a DARK board)."""
    words = (variant or "").lower().split("_")
    if words and words[0] == "dark":
        return (52, 52, 56)
    if words and words[0] in VARIANT_COLOURS:
        return VARIANT_COLOURS[words[0]]
    return fallback


def _ink_for(face):
    return (30, 30, 32) if _luma(face) > 140 else (238, 236, 228)


def _text_lines(v, f, x0, x1, z0, z1, y, ink, n, scale):
    """n left-aligned bars standing in for lettering (text itself is unresolved for these fixtures)."""
    if n <= 0:
        return
    bar = min(0.05, (z1 - z0) / (2.0 * n + 2.0))
    for i in range(n):
        zc = z0 + (z1 - z0) * (i + 1) / (n + 1)
        w = (x1 - x0) * (0.85 if i % 2 == 0 else 0.6)
        _add_box(v, f, (x0, x0 + w, y, y + 0.008, zc - bar / 2, zc + bar / 2), ink, scale)


def _sign_plate(v, f, cx, w, z0, h, face, scale, depth=0.05, y0=0.0, rim=(58, 58, 60), lines=None):
    """A sign plate: dark rim, inset board and lettering bars. Back at y0, front at y0 + depth + 0.012."""
    _add_box(v, f, (cx - w / 2, cx + w / 2, y0, y0 + depth, z0, z0 + h), rim, scale)
    b = min(0.05, w / 6.0, h / 6.0)
    _add_box(v, f, (cx - w / 2 + b, cx + w / 2 - b, y0 + depth, y0 + depth + 0.012, z0 + b, z0 + h - b), face, scale)
    n = (2 if h < 0.5 else 3) if lines is None else lines
    _text_lines(v, f, cx - w / 2 + 2 * b, cx + w / 2 - 2 * b, z0 + b, z0 + h - b, y0 + depth + 0.012,
                _ink_for(face), n, scale)


def _add_prism_yz(v, f, x0, x1, pts_yz, rgb, scale):
    """A convex quad given as four (y, z) points in metres, extruded over x0..x1 (slopes: awnings, A-frame legs)."""
    cx = (x0 + x1) / 2.0
    cy = sum(p[0] for p in pts_yz) / 4.0
    cz = sum(p[1] for p in pts_yz) / 4.0

    def face(quad):
        ax, ay, az = (quad[1][i] - quad[0][i] for i in range(3))
        bx, by, bz = (quad[3][i] - quad[0][i] for i in range(3))
        n = (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)
        fc = tuple(sum(q[i] for q in quad) / 4.0 for i in range(3))
        if n[0] * (fc[0] - cx) + n[1] * (fc[1] - cy) + n[2] * (fc[2] - cz) < 0:
            quad, n = quad[::-1], (-n[0], -n[1], -n[2])
        ln = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2) or 1.0
        first = len(v)
        v.extend((q[0] * scale, q[1] * scale, q[2] * scale, n[0] / ln, n[1] / ln, n[2] / ln, *rgb,
                  ALPHA_NO_COLLISION) for q in quad)
        f.append([first, first + 1, first + 2, first + 3])

    for i in range(4):
        (ya, za), (yb, zb) = pts_yz[i], pts_yz[(i + 1) % 4]
        face([(x0, ya, za), (x1, ya, za), (x1, yb, zb), (x0, yb, zb)])
    face([(x0, y, z) for y, z in pts_yz])
    face([(x1, y, z) for y, z in pts_yz])


def _reframe_front_to_wall(v):
    """Turn a freestanding-convention mesh (front on -Y, centred on y = 0) into the wall frame: rotate 180 deg about Z
    (a rotation, so winding is kept) and slide so its back is y = 0."""
    if not v:
        return v
    top = max(p[1] for p in v)
    return [(-p[0], -p[1] + top, p[2], -p[3], -p[4], p[5], *p[6:]) for p in v]


def _bicycle_row(v, f, n, scale):
    frames = ((181, 184, 180), (55, 61, 62), (177, 76, 72))
    centres = {1: (0.0,), 2: (-0.85, 0.85), 3: (-1.7, 0.0, 1.7)}[n]
    for centre, frame in zip(centres, frames):
        _add_bicycle_unit(v, f, centre, frame, scale)


def _shelf_rack(v, f, w, d, h, shelves, frame, goods, scale):
    """Open display rack against the wall: uprights, shelf boards and rows of goods on each shelf."""
    for x in (-w / 2, w / 2 - 0.05):
        _add_box(v, f, (x, x + 0.05, 0.0, d, 0.0, h), frame, scale)
    _add_box(v, f, (-w / 2, w / 2, 0.0, 0.02, 0.0, h), tuple(max(0, c - 30) for c in frame), scale)
    for k, z in enumerate(shelves):
        _add_box(v, f, (-w / 2, w / 2, 0.0, d, z, z + 0.04), frame, scale)
        n = max(2, int(w / 0.28))
        for i in range(n):
            gx = -w / 2 + 0.1 + i * (w - 0.2) / n
            gh = 0.16 + 0.04 * ((i + k) % 3)
            _add_box(v, f, (gx, gx + (w - 0.2) / n - 0.04, 0.08, d - 0.06, z + 0.04, z + 0.04 + gh),
                     goods[(i + k) % len(goods)], scale)


def build_unit_panel(module_id: str, variant: str | None, scale: float,
                     asset_contract: dict):
    """Mesh of one building_face_panel unit fixture, in the wall frame (see the block comment above).

    Every kind gets its own shape and real size; only FULL_FACE_UNIT_MODULES (shutters, roof railings, wall columns,
    a roofed house entry) are 1 m wide unit meshes that the transform stretches to the face width. Sizes, heights
    and which module reaches here: unit_panel_shape and the shape table in the FL-4876 CARDFIX-01 notes."""
    shape = unit_panel_shape(module_id, variant)
    if shape in ("legacy_full_face", "unmodelled"):
        return _legacy_unit_panel(module_id, variant, scale, asset_contract)
    v, f = [], []
    var = variant or ""
    profile = asset_contract.get("facade_profile") or {}
    sign_mount = float(asset_contract["sign_mounting_height_m"])
    sign_plate = float(asset_contract["sign_plate_height_m"])
    band_top = sign_mount + sign_plate
    if shape == "custom":
        cv, cf = build_custom_geometry(module_id, variant, scale)
        return _reframe_front_to_wall(cv), cf
    if shape == "roller_shutter":
        slat, dark = (150, 154, 156), (92, 96, 98)
        _add_box(v, f, (-0.5, 0.5, 0.0, 0.06, 0.0, 2.6), slat, scale)
        z = 0.14
        while z < 2.55:
            _add_box(v, f, (-0.5, 0.5, 0.06, 0.066, z, z + 0.02), dark, scale)
            z += 0.14
        _add_box(v, f, (-0.5, 0.5, 0.0, 0.2, 2.6, 2.85), dark, scale)
        if "tiled_base" in var:
            _add_box(v, f, (-0.5, 0.5, 0.0, 0.09, 0.0, 0.5), (168, 140, 112), scale)
        return v, f
    if shape == "roofed_entry":
        wall = tuple(profile.get("wall_rgb") or (186, 180, 166))
        roof = tuple(profile.get("roof_rgb") or (110, 96, 92))
        _add_box(v, f, (-0.5, 0.5, 0.0, 0.05, 0.0, 3.0), wall, scale)
        _add_box(v, f, (-0.5, 0.5, 0.0, 0.40, 2.95, 3.12), roof, scale)
        return v, f
    if shape == "bicycles":
        n = 3 if "row" in var else 2
        _bicycle_row(v, f, n, scale)
        return _reframe_front_to_wall(v), f
    if shape == "vending":
        red, white, glass = (196, 52, 48), (228, 228, 222), (38, 46, 52)
        body = red if var.startswith("red") else white
        band = (56, 96, 168) if body == white else (238, 238, 230)
        _add_vending_cabinet(v, f, 0.0, body, glass, band, scale)
        return _reframe_front_to_wall(v), f
    if shape == "mailbox":
        if "louvered" in var:
            _add_box(v, f, (-0.25, 0.25, 0.0, 0.20, 1.0, 1.6), (150, 152, 148), scale)
            for i in range(6):
                z = 1.08 + 0.085 * i
                _add_box(v, f, (-0.21, 0.21, 0.20, 0.225, z, z + 0.035), (96, 100, 100), scale)
        else:
            _add_box(v, f, (-0.19, 0.19, 0.0, 0.16, 1.05, 1.45), (110, 114, 114), scale)
            _add_box(v, f, (-0.12, 0.12, 0.16, 0.175, 1.30, 1.34), (40, 42, 44), scale)
        return v, f
    if shape == "fascia_sign":
        face = _fascia_rgb(var)
        _sign_plate(v, f, 0.0, 2.0, band_top - 0.7, 0.7, face, scale, depth=0.07)
        return v, f
    if shape == "window_poster":
        _add_box(v, f, (-0.45, 0.45, 0.0, 0.02, 1.0, 2.1), (226, 220, 200), scale)
        _text_lines(v, f, -0.37, 0.37, 1.1, 2.0, 0.02, (60, 60, 64), 3, scale)
        return v, f
    if shape == "pedestal_sign":
        face = _fascia_rgb(var)
        _add_box(v, f, (-0.22, 0.22, 0.10, 0.34, 0.0, 0.05), (70, 70, 72), scale)
        _add_box(v, f, (-0.04, 0.04, 0.18, 0.26, 0.05, 0.95), (70, 70, 72), scale)
        _sign_plate(v, f, 0.0, 0.55, 0.9, 1.1, face, scale, depth=0.06, y0=0.15)
        return v, f
    if shape == "a_frame_board":
        board = _fascia_rgb(var, (46, 56, 50))
        wood = (120, 96, 70)
        _add_prism_yz(v, f, -0.28, 0.28, [(0.0, 0.0), (0.05, 0.0), (0.23, 0.9), (0.18, 0.9)], wood, scale)
        _add_prism_yz(v, f, -0.28, 0.28, [(0.45, 0.0), (0.40, 0.0), (0.17, 0.9), (0.22, 0.9)], board, scale)
        return v, f
    if shape == "blade_sign":
        face = _fascia_rgb(var)
        _sign_plate(v, f, 0.0, 0.5, 2.0, 1.6, face, scale, depth=0.08, lines=4)
        return v, f
    if shape == "pole_banner_cluster":
        _add_box(v, f, (-0.03, 0.03, 0.20, 0.26, 0.0, 3.2), (70, 72, 74), scale)
        for cx, rgb in ((-0.28, (190, 48, 44)), (0.28, (232, 230, 222))):
            _add_box(v, f, (cx - 0.18, cx + 0.18, 0.20, 0.235, 1.7, 2.8), rgb, scale)
            _text_lines(v, f, cx - 0.12, cx + 0.12, 1.8, 2.7, 0.235, _ink_for(rgb), 3, scale)
        return v, f
    if shape == "wall_banner":
        rgb = _fascia_rgb(var, (180, 40, 40))
        _add_box(v, f, (-0.33, 0.33, 0.0, 0.08, 3.2, 3.24), (60, 60, 62), scale)
        _add_box(v, f, (-0.28, 0.28, 0.04, 0.07, 1.5, 3.2), rgb, scale)
        _text_lines(v, f, -0.2, 0.2, 1.7, 3.0, 0.07, _ink_for(rgb), 4, scale)
        return v, f
    if shape == "notice_plate":
        _sign_plate(v, f, 0.0, 0.4, 1.4, 0.5, (236, 236, 230), scale, depth=0.03, lines=0)
        _add_box(v, f, (-0.10, 0.10, 0.042, 0.05, 1.5, 1.7), (190, 48, 45), scale)
        return v, f
    if shape == "freestanding_notice_board":
        _add_box(v, f, (-0.03, 0.03, 0.12, 0.18, 0.0, 1.4), (66, 70, 69), scale)
        _sign_plate(v, f, 0.0, 0.45, 1.0, 0.4, (236, 236, 230), scale, depth=0.05, y0=0.15, lines=0)
        _add_box(v, f, (-0.10, 0.10, 0.215, 0.225, 1.08, 1.30), (190, 48, 45), scale)
        return v, f
    if shape == "bulletin_board":
        _add_box(v, f, (-0.6, 0.6, 0.0, 0.05, 1.2, 2.1), (90, 70, 52), scale)
        _add_box(v, f, (-0.55, 0.55, 0.05, 0.062, 1.25, 2.05), (170, 130, 90), scale)
        for i, (px, rgb) in enumerate(((-0.36, (236, 232, 214)), (0.0, (196, 60, 56)), (0.34, (232, 226, 190)))):
            _add_box(v, f, (px - 0.17, px + 0.17, 0.062, 0.07, 1.4 + 0.05 * (i % 2), 1.95 - 0.05 * (i % 2)),
                     rgb, scale)
        _sign_plate(v, f, 0.0, 0.4, 2.2, 0.4, (236, 236, 230), scale, depth=0.04, lines=0)
        return v, f
    if shape == "poster_cluster":
        for px, w, z0, h, rgb in ((-0.55, 0.45, 1.25, 0.65, (232, 226, 206)), (0.0, 0.45, 1.20, 0.72, (196, 60, 56)),
                                  (0.55, 0.45, 1.30, 0.60, (60, 96, 150))):
            _add_box(v, f, (px - w / 2, px + w / 2, 0.0, 0.02, z0, z0 + h), rgb, scale)
            _text_lines(v, f, px - w / 2 + 0.05, px + w / 2 - 0.05, z0 + 0.05, z0 + h - 0.05, 0.02,
                        _ink_for(rgb), 3, scale)
        return v, f
    if shape == "awning":
        cloth = _fascia_rgb(var, (84, 104, 88)) if var != "default" else (84, 104, 88)
        if "white" in var:
            cloth = (232, 230, 222)
        if "metal" in var:
            cloth = (150, 154, 156)
        w, depth = 2.4, 0.95
        if "striped" in var:
            n = 8
            for i in range(n):
                x0 = -w / 2 + i * w / n
                _add_prism_yz(v, f, x0, x0 + w / n, [(0.0, 2.78), (depth, 2.48), (depth, 2.43), (0.0, 2.73)],
                              (232, 230, 222) if i % 2 else cloth, scale)
        else:
            _add_prism_yz(v, f, -w / 2, w / 2, [(0.0, 2.78), (depth, 2.48), (depth, 2.43), (0.0, 2.73)], cloth, scale)
        _add_box(v, f, (-w / 2, w / 2, depth - 0.02, depth + 0.02, 2.18, 2.45),
                 (190, 48, 44) if "signband" in var else cloth, scale)
        for x in (-w / 2 + 0.05, w / 2 - 0.05):
            _add_box(v, f, (x - 0.02, x + 0.02, 0.0, depth, 2.70, 2.73), (70, 72, 74), scale)
        return v, f
    if shape == "glass_entry":
        frame = (190, 48, 44) if "red" in var else (64, 128, 72) if "green" in var else (58, 60, 62)
        glass = (150, 178, 184)
        w = 1.8
        _add_box(v, f, (-w / 2, w / 2, 0.0, 0.08, 0.0, 2.2), frame, scale)
        for sx in (-1, 1):
            _add_box(v, f, (min(sx * 0.06, sx * 0.84), max(sx * 0.06, sx * 0.84), 0.08, 0.095, 0.1, 2.1),
                     glass, scale)
        if "planter" in var:
            for sx in (-1, 1):
                _add_box(v, f, (sx * 1.2 - 0.25, sx * 1.2 + 0.25, 0.0, 0.35, 0.0, 0.45), (120, 96, 72), scale)
                _add_box(v, f, (sx * 1.2 - 0.22, sx * 1.2 + 0.22, 0.03, 0.32, 0.45, 0.80), (64, 128, 72), scale)
        return v, f
    if shape == "noren_entry":
        _add_box(v, f, (-0.55, 0.55, 0.0, 0.07, 0.0, 2.05), (58, 52, 46), scale)
        _add_box(v, f, (-0.6, 0.6, 0.07, 0.10, 2.0, 2.06), (50, 50, 50), scale)
        cloth = _fascia_rgb(var, (36, 44, 92))
        for cx in (-0.36, 0.0, 0.36):
            _add_box(v, f, (cx - 0.16, cx + 0.16, 0.08, 0.095, 1.45, 2.0), cloth, scale)
        return v, f
    if shape == "display_rack":
        wood = (132, 105, 76)
        goods = ((196, 60, 56), (226, 190, 80), (80, 130, 84), (236, 232, 220))
        if "slatted" in var:
            _shelf_rack(v, f, 1.2, 0.42, 1.4, (0.25, 0.65, 1.05), wood, goods, scale)
        else:
            _shelf_rack(v, f, 1.6, 0.45, 1.2, (0.35, 0.75), wood, goods, scale)
        return v, f
    if shape == "crates":
        crate = (64, 128, 72) if "green" in var else (196, 100, 52)
        for row, count in ((0, 3), (1, 2)):
            for i in range(count):
                cx = (i - (count - 1) / 2.0) * 0.52
                _add_box(v, f, (cx - 0.25, cx + 0.25, 0.0, 0.38, 0.3 * row, 0.3 * row + 0.28), crate, scale)
                _add_box(v, f, (cx - 0.20, cx + 0.20, 0.38, 0.385, 0.3 * row + 0.06, 0.3 * row + 0.22),
                         tuple(max(0, c - 40) for c in crate), scale)
        return v, f
    if shape.startswith("street_"):
        sub = shape[len("street_"):]
        if sub == "store_signboard":
            _sign_plate(v, f, 0.0, 1.4, band_top - 0.7, 0.7, _fascia_rgb(None), scale, depth=0.07)
        elif sub == "advertising_sign":
            _sign_plate(v, f, 0.0, 1.0, sign_mount + 0.4, 0.8, (236, 236, 228), scale, depth=0.06)
        elif sub == "banner_sign":
            _add_box(v, f, (-0.33, 0.33, 0.0, 0.08, sign_mount + 1.8, sign_mount + 1.84), (60, 60, 62), scale)
            _add_box(v, f, (-0.28, 0.28, 0.04, 0.07, sign_mount, sign_mount + 1.8), (180, 40, 40), scale)
            _text_lines(v, f, -0.2, 0.2, sign_mount + 0.2, sign_mount + 1.6, 0.07, (238, 236, 228), 4, scale)
        elif sub == "traffic_sign":
            for sx in (-1, 1):
                _add_box(v, f, (sx * 0.2 - 0.02, sx * 0.2 + 0.02, 0.0, 0.07, 1.95, 2.45), (110, 114, 114), scale)
            _sign_plate(v, f, 0.0, 0.6, 1.9, 0.6, (236, 236, 230), scale, depth=0.04, y0=0.07, lines=0)
            _add_box(v, f, (-0.2, 0.2, 0.122, 0.13, 2.03, 2.37), (190, 48, 45), scale)
        elif sub == "generic_sign_plate":
            _sign_plate(v, f, 0.0, 0.5, 1.9, 0.35, (236, 236, 230), scale, depth=0.03)
        elif sub == "information_board":
            _add_box(v, f, (-0.6, 0.6, 0.0, 0.06, 1.2, 2.1), (58, 60, 62), scale)
            _add_box(v, f, (-0.55, 0.55, 0.06, 0.072, 1.25, 1.75), (226, 224, 214), scale)
            _add_box(v, f, (-0.55, 0.55, 0.06, 0.072, 1.8, 2.05), (56, 96, 168), scale)
            _text_lines(v, f, -0.45, 0.45, 1.3, 1.7, 0.072, (30, 30, 32), 3, scale)
        elif sub == "pedestrian_gate":
            gv, gf = _build_archetype("pedestrian_gate", scale)
            return _reframe_front_to_wall(gv), gf
        return v, f
    raise ValueError("no wall-fixture shape for %s/%s (%s)" % (module_id, variant, shape))


def build_wall_fixture_freestanding(module_id: str, variant: str | None, scale: float, asset_contract: dict):
    """The same fixture standing on its own (a face snap that was refused for distance leaves it where it was
    observed): front on -Y, centred on y = 0 like the other freestanding meshes, and a post from the ground up to
    any part that does not reach the ground."""
    v, f = build_unit_panel(module_id, variant, scale, asset_contract)
    zmin = min(p[2] for p in v) / scale
    ymax = max(p[1] for p in v)
    if zmin > 0.3:
        top = min(zmin + 0.3, max(p[2] for p in v) / scale)
        _add_box(v, f, (-0.04, 0.04, -0.10, 0.0, 0.0, top), (66, 70, 69), scale)
    ymin = min(p[1] for p in v)
    mid = (ymin + ymax) / 2.0
    v = [(-p[0], -(p[1] - mid), p[2], -p[3], -p[4], p[5], *p[6:]) for p in v]
    return v, f


def _run_panel_roof_slab(v, f, half, height_m, scale, trim, dark):
    """Projecting roof overhang band for roofed-entry variants."""
    _add_box(v, f, (-half - 0.15, half + 0.15, -0.12, 0.35,
                    max(0.4, height_m - 0.55), height_m), trim, scale)
    _add_box(v, f, (-half - 0.15, half + 0.15, -0.10, 0.33,
                    max(0.4, height_m - 0.62), max(0.4, height_m - 0.55)),
               dark, scale)


def _run_panel_signband(v, f, half, scale, wall, dark, z0=2.8, z1=3.5):
    """Projecting signband plate for signband variants."""
    _add_box(v, f, (-half, half, -0.05, 0.16, z0, z1), wall, scale)
    _add_box(v, f, (-half, half, -0.06, 0.17, z0 - 0.08, z0), dark, scale)


def _run_panel_stair_tower(v, f, half, height_m, scale, dark, glass,
                       tower=True, deck_depth=0.12):
    """Recessed stair-tower slot plus balcony rails for stair variants."""
    if tower:
        tx = -half + 0.9
        _add_box(v, f, (tx - 0.55, tx + 0.55, -0.10, 0.02, 0.1,
                        height_m - 0.3), dark, scale)
    else:
        tx = -half + 0.9
        _add_box(v, f, (tx - 0.75, tx + 0.75, -0.02, deck_depth, 0.1,
                        0.22), dark, scale)
    z = 3.4
    while z + 0.15 <= height_m:
        _add_box(v, f, (-half, half, 0.02, 0.10, z, z + 0.12),
                   glass, scale)
        z += 3.0


def _run_panel_shutter_courses(v, f, half, height_m, scale, wall, dark):
    """Horizontal shutter slats with tiled base for shutter archetype."""
    z = 0.5
    while z + 0.14 <= min(3.2, height_m):
        _add_box(v, f, (-half, half, 0.02, 0.08, z, z + 0.14),
                   dark if int(z * 4) % 2 else wall, scale)
        z += 0.28
    _add_box(v, f, (-half, half, -0.06, 0.10, 0, 0.5), wall, scale)


def build_run_panel(module_id: str, variant: str | None, width_m: float,
                      height_m: float, scale: float, storefront: bool,
                      asset_contract: dict):
    """Full-face run panel baked at exact width: local X spans the front,
    +Y faces outward, Z rises from grade. Window/door rhythm via _add_box
    strips; colours from the variant words. Storefront runs add a
    ground-floor glazing band plus a projecting awning strip."""
    v, f = [], []
    profile = asset_contract.get("facade_profile") or {}
    wall = tuple(profile.get("wall_rgb") or
                 variant_rgb(variant, (186, 180, 166)))
    dark = tuple(max(20, c - 60) for c in wall)
    trim = tuple(profile.get("trim_rgb") or
                 tuple(min(255, c + 30) for c in wall))
    glass = tuple(profile.get("glass_rgb") or (150, 178, 184))
    roof = tuple(profile.get("roof_rgb") or (150, 110, 88))
    sign_mount = float(asset_contract["sign_mounting_height_m"])
    sign_plate = float(asset_contract["sign_plate_height_m"])
    half = width_m / 2.0
    _add_box(v, f, (-half, half, -0.06, 0.06, 0, height_m), wall, scale)
    _add_box(v, f, (-half, half, -0.08, 0.08, 0, 0.5), dark, scale)
    variant_words = (variant or "").lower()
    # Per-archetype expression (FL-4809): the base wall stays shared, each
    # archetype family adds its own overhead/podium courses on top.
    if "shutter" in module_id:
        _run_panel_shutter_courses(v, f, half, height_m, scale, wall, dark)
    else:
        if variant_words == "curved_corner_facade":
            for ex in (-half + 0.15, half - 0.15):
                _add_box(v, f, (ex - 0.12, ex + 0.12, -0.08, 0.12, 0,
                                height_m), trim, scale)
        if "corner_commercial_with_signband" in variant_words:
            _run_panel_signband(v, f, half, scale, wall, dark,
                                z0=2.8, z1=3.5)
        if "lowrise_service_facade_with_signband" in variant_words:
            _run_panel_signband(v, f, half, scale, wall, dark,
                                z0=2.4, z1=3.0)
        if ("roof" in variant_words or "overhang" in variant_words
                or "tile" in variant_words):
            if "tile" in variant_words:
                _add_box(v, f, (-half - 0.15, half + 0.15, -0.12, 0.35,
                                max(0.4, height_m - 0.55), height_m),
                           roof, scale)
            else:
                _run_panel_roof_slab(v, f, half, height_m, scale, trim,
                                     dark)
        if "external_stair_stack" in variant_words:
            _run_panel_stair_tower(v, f, half, height_m, scale, dark,
                                   glass, tower=True)
        if "external_stair_and_balcony" in variant_words:
            _run_panel_stair_tower(v, f, half, height_m, scale, dark,
                                   glass, tower=False, deck_depth=0.12)
        if "stacked_balcony_facade" in variant_words:
            _run_panel_stair_tower(v, f, half, height_m, scale, dark,
                                   glass, tower=False, deck_depth=0.20)
        if "stacked_balcony_overhang" in variant_words:
            _run_panel_stair_tower(v, f, half, height_m, scale, dark,
                                   glass, tower=False)
            _run_panel_roof_slab(v, f, half, height_m, scale, trim, dark)
        if "boundary_sign" in module_id:
            _add_box(v, f, (-half, half, 0.02, 0.12,
                            sign_mount,
                            sign_mount + sign_plate), trim, scale)
    bay = 1.8
    n = max(1, int(round(width_m / bay)))
    pitch = width_m / n
    door_bay = n // 2
    for i in range(n):
        cx = -half + (i + 0.5) * pitch
        gw = min(0.62 * pitch, 1.4) / 2
        if i == door_bay:
            _add_box(v, f, (cx - gw - 0.08, cx + gw + 0.08, -0.07, 0.07,
                            0.1, 2.7), dark, scale)
            _add_box(v, f, (cx - gw, cx + gw, -0.05, 0.09, 0.15, 2.55),
                     glass, scale)
        else:
            sill, head = 0.7, min(2.6, height_m - 0.6)
            if head > sill + 0.3:
                _add_box(v, f, (cx - gw, cx + gw, -0.05, 0.09, sill, head),
                         glass, scale)
                _add_box(v, f, (cx - gw - 0.06, cx + gw + 0.06, -0.06, 0.10,
                                head, head + 0.12), trim, scale)
                _add_box(v, f, (cx - gw - 0.06, cx + gw + 0.06, -0.06, 0.10,
                                sill - 0.12, sill), trim, scale)
        mx = -half + (i + 1) * pitch
        if 0 < i + 1 < n:
            _add_box(v, f, (mx - 0.05, mx + 0.05, -0.07, 0.09, 0.5,
                            min(2.8, height_m)), dark, scale)
    z = 3.6
    while z + 1.6 <= height_m:
        for i in range(n):
            cx = -half + (i + 0.5) * pitch
            gw = min(0.55 * pitch, 1.2) / 2
            _add_box(v, f, (cx - gw, cx + gw, -0.05, 0.09, z + 0.6, z + 2.0),
                     glass, scale)
        z += 3.0
    _add_box(v, f, (-half, half, -0.09, 0.09,
                    max(0.4, height_m - 0.25), height_m), dark, scale)
    if storefront:
        _add_box(v, f, (-half, half, -0.05, 0.10, 0.3, 2.8), glass, scale)
        _add_box(v, f, (-half, half, -0.06, 0.11, 2.8, 3.4), wall, scale)
        _add_box(v, f, (-half, half, 0.0, 0.9, 2.5, 3.0), wall, scale)
        _add_box(v, f, (-half, half, 0.0, 0.9, 2.42, 2.5), dark, scale)
    return v, f



def variant_tint(base: tuple[int, int, int], variant: str) -> tuple[int, int, int]:
    """Deterministic per-variant lightness so distinct variants differ."""
    digest = hashlib.sha1(variant.encode("utf-8")).hexdigest()
    factor = 0.85 + 0.30 * (int(digest[:4], 16) % 100 / 100.0)
    return tuple(max(0, min(255, int(round(c * factor)))) for c in base)


def flat_panel(v, f, length, width, lift, rgb, scale):
    _add_box(v, f, (-length / 2, length / 2, -width / 2, width / 2, lift, lift + 0.02), rgb, scale)


def railing_run(v, f, length, height, rgb, scale, post_step_m=2.0):
    n_posts = max(2, int(round(length / post_step_m)) + 1)
    # Posts own volume, not just centrelines.  Keep their 5 cm half-width
    # inside the declared tile span so a short terminal mesh cannot protrude
    # beyond the placement it claims to represent.
    post_half = min(0.05, length / 2.0)
    post_lo = -length / 2.0 + post_half
    post_hi = length / 2.0 - post_half
    for i in range(n_posts):
        x = post_lo + i * ((post_hi - post_lo) / (n_posts - 1))
        _add_box(v, f, (x - post_half, x + post_half,
                        -0.05, 0.05, 0, height), rgb, scale)
    for z in (height * 0.55, height * 0.95):
        _add_box(v, f, (-length / 2, length / 2, -0.05, 0.05, z, z + 0.08), rgb, scale)


def fence_panel(v, f, length, height, rgb, scale, style):
    _add_box(v, f, (-length / 2, -length / 2 + 0.09, -0.06, 0.06, 0, height), rgb, scale)
    _add_box(v, f, (length / 2 - 0.09, length / 2, -0.06, 0.06, 0, height), rgb, scale)
    if style == "bars":
        n = max(2, int(round(length / 0.15)))
        # FL-4876.  The declared envelope is max_longitudinal_overhang_m 0.0,
        # so the end bars own volume inside the span rather than straddling
        # it.  railing_run() already insets this way; fence_panel did not, and
        # every bars/pickets run overhung its placement by a half-width at
        # each end.
        half = min(0.03, length / 2.0)
        lo, hi = -length / 2 + half, length / 2 - half
        for i in range(n + 1):
            x = lo + i * ((hi - lo) / n)
            _add_box(v, f, (x - half, x + half, -0.03, 0.03, 0, height), rgb, scale)
        _add_box(v, f, (-length / 2, length / 2, -0.04, 0.04, height - 0.12, height - 0.04), rgb, scale)
    elif style == "mesh":
        _add_box(v, f, (-length / 2, length / 2, -0.03, 0.03, 0.1, height - 0.05), rgb, scale)
        _add_box(v, f, (-length / 2, length / 2, -0.05, 0.05, height - 0.05, height), rgb, scale)
    elif style == "lattice":
        _add_box(v, f, (-length / 2, length / 2, -0.04, 0.04, 0.15, height), rgb, scale)
        _add_box(v, f, (-length / 2, length / 2, -0.02, 0.02, 0.15, height),
                 (min(255, rgb[0] + 30), min(255, rgb[1] + 30), min(255, rgb[2] + 30)), scale)
    elif style == "pickets":
        n = max(2, int(round(length / 0.25)))
        half = min(0.04, length / 2.0)
        lo, hi = -length / 2 + half, length / 2 - half
        for i in range(n + 1):
            x = lo + i * ((hi - lo) / n)
            _add_box(v, f, (x - half, x + half, -0.04, 0.04, 0, height + 0.08), rgb, scale)
        _add_box(v, f, (-length / 2, length / 2, -0.03, 0.03, height * 0.35, height * 0.45), rgb, scale)


def _diagonal_gate_bar(v, f, x0, z0, x1, z1, width, rgb, scale):
    """Facade-facing diagonal strip used by a collapsible lattice gate."""
    dx, dz = x1 - x0, z1 - z0
    length = math.hypot(dx, dz) or 1.0
    ox = -dz / length * width / 2.0
    oz = dx / length * width / 2.0
    _add_polygon_prism_xz(
        v, f,
        [(x0 + ox, z0 + oz), (x1 + ox, z1 + oz),
         (x1 - ox, z1 - oz), (x0 - ox, z0 - oz)],
        half_depth_m=0.025, rgb=rgb, scale=scale)


def accordion_gate(v, f, length, height, rgb, scale):
    """Observed black folding entrance gate: repeated crossing scissor bars."""
    post = min(0.07, length / 4.0)
    _add_box(v, f, (-length / 2, -length / 2 + 2 * post,
                    -0.055, 0.055, 0, height), rgb, scale)
    _add_box(v, f, (length / 2 - 2 * post, length / 2,
                    -0.055, 0.055, 0, height), rgb, scale)
    usable_lo = -length / 2 + 2 * post
    usable_hi = length / 2 - 2 * post
    bays = max(2, int(round((usable_hi - usable_lo) / 0.42)))
    bay_width = (usable_hi - usable_lo) / bays
    for index in range(bays):
        lo = usable_lo + index * bay_width
        hi = lo + bay_width
        # Endpoints are inset so the diagonal's finite width remains inside
        # the declared zero-overhang run envelope.
        inset = min(0.04, bay_width * 0.12)
        _diagonal_gate_bar(v, f, lo + inset, 0.10, hi - inset,
                           height - 0.10, 0.045, rgb, scale)
        _diagonal_gate_bar(v, f, lo + inset, height - 0.10,
                           hi - inset, 0.10, 0.045, rgb, scale)
    _add_box(v, f, (-length / 2, length / 2,
                    -0.035, 0.035, 0.02, 0.08), rgb, scale)


def corrugated_fence_with_gate(v, f, length, height, scale):
    """Opaque beige ribbed enclosure with the observed dark gate panels."""
    beige = (184, 176, 148)
    rib = (151, 145, 124)
    dark = (48, 48, 45)
    _add_box(v, f, (-length / 2, length / 2,
                    -0.055, 0.055, 0, height), beige, scale)
    # Narrow horizontal ribs are the feature that distinguishes this from a
    # blank wall in the source crop.
    z = 0.12
    while z < height - 0.05:
        _add_box(v, f, (-length / 2, length / 2,
                        -0.070, -0.055, z, min(height, z + 0.025)), rib, scale)
        z += 0.14
    gate_lo = length * 0.12
    gate_hi = length / 2 - 0.08
    _add_box(v, f, (gate_lo, gate_hi, -0.078, -0.055,
                    0, height * 0.78), dark, scale)
    _add_box(v, f, (gate_lo + 0.07, gate_hi - 0.07,
                    -0.088, -0.078, 0.08, height * 0.70), (62, 62, 57), scale)


def wall_run(v, f, length, height, thick, rgb, scale, cap=True):
    _add_box(v, f, (-length / 2, length / 2, -thick / 2, thick / 2, 0, height), rgb, scale)
    if cap:
        _add_box(v, f, (-length / 2, length / 2, -thick / 2 - 0.03, thick / 2 + 0.03,
                        height, height + 0.07), rgb, scale)


def catenary_boxes(v, f, x0, x1, z0_m, z1_m, rgb, scale, strands=(-0.14, 0.14)):
    """Straight multi-strand span with a 2%-of-span sagitta (span-derived)."""
    length = math.hypot(x1 - x0, z1_m - z0_m)
    sag = 0.02 * length
    segs = 5
    for strand in strands:
        for i in range(segs):
            t0, t1 = i / segs, (i + 1) / segs
            sag0 = sag * 4 * t0 * (1 - t0)
            sag1 = sag * 4 * t1 * (1 - t1)
            h0 = z0_m + (z1_m - z0_m) * t0 - sag0
            h1 = z0_m + (z1_m - z0_m) * t1 - sag1
            cx0, cx1 = x0 + (x1 - x0) * t0, x0 + (x1 - x0) * t1
            lo, hi = (cx0, cx1) if cx1 >= cx0 else (cx1, cx0)
            zmid = (h0 + h1) / 2
            _add_box(v, f, (lo, hi, strand - 0.025, strand + 0.025, zmid - 0.025, zmid + 0.025),
                     rgb, scale)


def _guardrail_variant_mesh(v, f, module_id, variant, L, scale, tint):
    """One guardrail tile mesh for (module_id, variant); geometry along X.
    Unknown variant strings are an error naming the variant."""
    if module_id == "pedestrian_guardrail_continuous" and variant == "gray_tube":
        railing_run(v, f, L, 1.0, tint((205, 208, 212)), scale)
    elif module_id == "pedestrian_guardrail_continuous" and variant == "white_tube":
        railing_run(v, f, L, 1.0, tint((232, 232, 226)), scale)
    elif (module_id == "pedestrian_guardrail_continuous"
            and variant == "white_tube_short_terminal"):
        railing_run(v, f, L, 1.0, tint((232, 232, 226)), scale)
        # The upstream physical-envelope contract owns the closed run span.
        # Terminal detail occupies the final 18 cm *inside* that span.
        _add_box(v, f, (L / 2 - 0.18, L / 2, -0.09, 0.09, 0, 1.15),
                 tint((232, 232, 226)), scale)
    elif (module_id == "pedestrian_guardrail_vertical_bar_continuous"
            and variant == "galvanized_silver"):
        railing_run(v, f, L, 1.0, tint((200, 205, 210)), scale)
    elif (module_id == "pedestrian_guardrail_vertical_bar_continuous"
            and variant == "white_continuous_vertical_bar_guardrail"):
        railing_run(v, f, L, 1.0, tint((232, 232, 226)), scale)
    elif (module_id == "pedestrian_guardrail_vertical_bar_continuous"
            and variant == "stair_run_with_tactile_edge"):
        railing_run(v, f, L, 1.0, tint((205, 208, 212)), scale)
        _add_box(v, f, (-L / 2, L / 2, -0.15, 0.15, 0.0, 0.035),
                 tint((210, 180, 60)), scale)
    else:
        raise ValueError(f"unknown {module_id} variant: {variant!r}")


def _bollard_variant_mesh(v, f, variant, scale, tint):
    """One bollard post mesh for a catalog variant, centred on the tile.
    Unknown variant strings are an error naming the variant."""
    if variant == "chevron_marked_bollard":
        _add_box(v, f, (-0.09, 0.09, -0.09, 0.09, 0, 0.86), tint((120, 124, 119)), scale)
        _add_box(v, f, (-0.11, 0.11, -0.11, 0.11, 0.70, 0.82), tint((210, 180, 54)), scale)
        _add_box(v, f, (-0.10, 0.10, -0.10, 0.10, 0.50, 0.62), tint((235, 235, 228)), scale)
    elif variant == "gray_stone_bollard":
        _add_box(v, f, (-0.13, 0.13, -0.13, 0.13, 0, 0.70), tint((145, 143, 135)), scale)
    else:
        raise ValueError(f"unknown bollard_curb_island variant: {variant!r}")


def build_unit_mesh(module_id, variant, unit_len, scale, asset_contract):
    """One unit segment mesh for (module_id, variant); geometry along local X."""
    v: list[tuple] = []
    f: list[list[int]] = []
    tint = lambda base: variant_tint(base, variant or "default")
    L = unit_len
    cross = asset_contract["cross_section_m"]
    curb_h = float(cross["curb_height_m"])
    carriageway_w = float(cross["carriageway_width_m"])
    sidewalk_w = float(cross["sidewalk_width_m"])
    tactile_w = float(cross["tactile_paving_width_m"])
    gutter_w = float(cross["drainage_gutter_width_m"])
    rail_fence_h = float(cross["rail_boundary_fence_height_m"])
    if module_id == "curb_and_open_gutter":
        _add_box(v, f, (-L / 2, L / 2, 0.0, 0.15, 0, curb_h), tint((150, 148, 140)), scale)
        _add_box(v, f, (-L / 2, L / 2, 0.15, 0.15 + gutter_w, -0.06, 0.0),
                 tint((120, 118, 110)), scale)
    elif module_id == "open_and_covered_drain":
        channel_w = max(gutter_w, 0.50 if variant == "open_stone_channel" else gutter_w)
        _add_box(v, f, (-L / 2, L / 2, -channel_w / 2, channel_w / 2,
                        -0.25, -0.18), tint((82, 91, 88)), scale)
        for side in (-1, 1):
            inner = side * channel_w / 2
            outer = side * (channel_w / 2 + 0.10)
            lo, hi = sorted((inner, outer))
            _add_box(v, f, (-L / 2, L / 2, lo, hi, -0.25, 0.03),
                     tint((140, 138, 130)), scale)
        if variant == "covered_edge_channel_with_drain_cover":
            _add_box(v, f, (-L / 2, L / 2, -channel_w / 2, channel_w / 2,
                            -0.03, 0.02), tint((160, 158, 150)), scale)
    elif module_id in ("curb_edge_line", "white_edge_line_shoulder"):
        flat_panel(v, f, L, 0.15, 0.02, tint((228, 228, 220)), scale)
    elif module_id == "road_edge_dashed_marking":
        flat_panel(v, f, L / 3, 0.15, 0.02, tint((228, 228, 220)), scale)
        _add_box(v, f, (L / 6, L / 2, -0.075, 0.075, 0.02, 0.04), tint((228, 228, 220)), scale)
    elif module_id == "green_cycle_or_walk_band":
        flat_panel(v, f, L, sidewalk_w, 0.02, tint((58, 120, 90)), scale)
    elif module_id == "sidewalk_paver_surface":
        flat_panel(v, f, L, sidewalk_w, 0.015, tint((150, 148, 142)), scale)
        if "tactile" in (variant or ""):
            _add_box(v, f, (-L / 2, L / 2, sidewalk_w / 2 - tactile_w,
                            sidewalk_w / 2, 0.015, 0.035), tint((210, 180, 60)), scale)
    elif module_id == "zebra_crossing":
        n = max(2, int(round(L / 1.0)))
        for i in range(n):
            x = -L / 2 + (i + 0.5) * (L / n)
            _add_box(v, f, (x - L / n * 0.3, x + L / n * 0.3,
                            -carriageway_w / 2, carriageway_w / 2,
                            0.02, 0.04), tint((228, 228, 220)), scale)
    elif module_id in ("road_surface_text_marking", "road_text_arrow_30"):
        flat_panel(v, f, L, carriageway_w * 0.6, 0.02, tint((60, 90, 70)), scale)
        digits = "".join(re.findall(r"\d", variant or ""))
        if digits:
            from scripts.pipeline.classic_procedural_mesh import _add_block_text
            _add_block_text(v, f, digits, max_width_m=L * 0.7, z_min_m=0.04, z_max_m=0.5,
                            rgb=tint((235, 235, 225)), scale=scale,
                            y_min_m=-carriageway_w * 0.3, y_max_m=-carriageway_w * 0.3 + 0.02)
        elif "arrow" in (variant or "") or "directional" in (variant or ""):
            _add_box(v, f, (-L * 0.3, L * 0.1, -0.1, 0.1, 0.04, 0.06), tint((235, 235, 225)), scale)
            _add_box(v, f, (L * 0.1, L * 0.3, -0.3, 0.3, 0.04, 0.06), tint((235, 235, 225)), scale)
    elif module_id == "crosswalk_and_edge_guardrail":
        railing_run(v, f, L, 0.75, tint((218, 221, 214)), scale)
        flat_panel(v, f, 0.6, carriageway_w, 0.02, tint((200, 60, 50)), scale)
    elif module_id == "manhole_and_grate":
        _add_box(v, f, (-0.3, 0.3, -0.3, 0.3, 0.015, 0.035), tint((90, 92, 88)), scale)
        _add_box(v, f, (-0.36, 0.36, -0.36, 0.36, 0.01, 0.02), tint((130, 132, 128)), scale)
    elif module_id == "trimmed_hedge":
        hedge_h = (1.60 if variant == "tall_dense_boundary_hedge" else
                   1.35 if variant == "dense_hedge_with_open_channel" else 1.15)
        _add_box(v, f, (-L / 2, L / 2, -0.25, 0.25, 0, hedge_h - 0.15),
                 tint((68, 102, 58)), scale)
        _add_box(v, f, (-L / 2, L / 2, -0.2, 0.2,
                        hedge_h - 0.15, hedge_h), tint((83, 119, 67)), scale)
    elif module_id == "concrete_block_wall":
        if variant == "cream_plaster_wall_with_blue_tile_coping":
            # FL-4876 temple precinct wall (有慶山東光院), panoramas 489113339072184 / 3921136788001424 /
            # 476931043387324: grey stone base, cream plaster body, overhanging blue glazed-tile coping
            # (sampled lit colours [228,220,203] plaster, [43,84,113] coping)
            _add_box(v, f, (-L / 2, L / 2, -0.16, 0.16, 0.0, 0.35), (132, 128, 120), scale)
            _add_box(v, f, (-L / 2, L / 2, -0.13, 0.13, 0.35, 1.52), (228, 220, 203), scale)
            _add_box(v, f, (-L / 2, L / 2, -0.24, 0.24, 1.52, 1.62), (43, 84, 113), scale)
            _add_box(v, f, (-L / 2, L / 2, -0.10, 0.10, 1.62, 1.70), (52, 96, 126), scale)
        elif variant == "gray_block_wall_with_tiled_coping_and_gate_piers":
            block = (151, 151, 145)
            joint = (105, 106, 103)
            tile = (117, 72, 64)
            _add_box(v, f, (-L / 2, L / 2, -0.14, 0.14,
                            0.0, 1.30), block, scale)
            for z in (0.32, 0.64, 0.96, 1.28):
                _add_box(v, f, (-L / 2, L / 2, -0.151, -0.14,
                                z, z + 0.025), joint, scale)
            _add_box(v, f, (-L / 2, L / 2, -0.22, 0.22,
                            1.30, 1.44), tile, scale)
            for x in (-L / 2 + 0.35, 0.0, L / 2 - 0.35):
                _add_box(v, f, (x - 0.22, x + 0.22, -0.25, 0.25,
                                0.0, 1.58), joint, scale)
                _add_box(v, f, (x - 0.29, x + 0.29, -0.31, 0.31,
                                1.58, 1.70), tile, scale)
        else:
            wall_run(v, f, L, 1.5, 0.2, tint((150, 148, 140)), scale)
    elif module_id == "stone_cobble_wall":
        wall_run(v, f, L, 1.2, 0.4, tint((110, 105, 95)), scale)
    elif module_id == "mesh_fence":
        fence_panel(v, f, L, 1.2, tint((90, 95, 100)), scale, "mesh")
    elif module_id == "metal_fence":
        if variant == "black_collapsible_accordion_gate":
            accordion_gate(v, f, L, 1.05, (35, 38, 38), scale)
        elif variant == "beige_corrugated_fence_with_black_gate":
            corrugated_fence_with_gate(v, f, L, 1.55, scale)
        elif variant == "black_vertical_picket_carport_fence":
            fence_panel(v, f, L, 1.10, (38, 41, 41), scale, "pickets")
        else:
            fence_panel(v, f, L, 1.2, tint((70, 75, 80)), scale, "bars")
    elif module_id == "lattice_boundary_fence":
        fence_panel(v, f, L, 1.2, tint((120, 95, 70)), scale, "lattice")
    elif module_id == "picket_fence":
        fence_panel(v, f, L, 1.0, tint((225, 222, 210)), scale, "pickets")
    elif module_id == "metal_boundary_fence":
        fence_panel(v, f, L, rail_fence_h, tint((60, 65, 70)), scale, "bars")
    elif module_id == "rail_corridor_fence":
        fence_panel(v, f, L, rail_fence_h, tint((65, 70, 75)), scale, "bars")
    elif module_id == "rail_corridor_mesh_fence":
        fence_panel(v, f, L, rail_fence_h, tint((85, 95, 85)), scale, "mesh")
    elif module_id == "ivy_wall":
        _add_box(v, f, (-L / 2, L / 2, -0.1, 0.1, 0, 1.5), tint((70, 95, 60)), scale)
        _add_box(v, f, (-L / 2, L / 2, -0.12, -0.1, 0.2, 1.3), tint((85, 115, 70)), scale)
    elif module_id == "stone_lined_water_channel":
        _add_box(v, f, (-L / 2, L / 2, -1.25, -0.75, 0, 0.6), tint((120, 115, 105)), scale)
        _add_box(v, f, (-L / 2, L / 2, 0.75, 1.25, 0, 0.6), tint((120, 115, 105)), scale)
        _add_box(v, f, (-L / 2, L / 2, -0.75, 0.75, -0.3, -0.05), tint((90, 130, 145)), scale)
    elif module_id == "concrete_retaining_edge":
        wall_run(v, f, L, 0.8, 0.3, tint((139, 137, 129)), scale)
    elif module_id == "white_railguard":
        railing_run(v, f, L, 1.0, tint((225, 225, 215)), scale)
    elif module_id in ("pedestrian_guardrail_continuous",
                        "pedestrian_guardrail_vertical_bar_continuous"):
        _guardrail_variant_mesh(v, f, module_id, variant, L, scale, tint)
    elif module_id == "bollard_curb_island":
        _bollard_variant_mesh(v, f, variant, scale, tint)
    elif module_id == "waterway_bridge_railing":
        if variant == "small_concrete_footbridge_with_dark_railings":
            # SF-4876-0384: the observed object is the entire small bridge,
            # not one generic rail floating over the channel.  x is the
            # measured crossing span; y is the photographed deck width.
            _add_box(v, f, (-L / 2, L / 2, -1.35, 1.35, 0.0, 0.22),
                     (151, 149, 140), scale)
            rail_rgb = (72, 68, 63)
            n_posts = max(2, int(round(L / 1.4)) + 1)
            post_half = min(0.045, L / 2.0)
            for side in (-1.24, 1.24):
                for i in range(n_posts):
                    x = (-L / 2 + post_half
                         + i * ((L - 2 * post_half) / (n_posts - 1)))
                    _add_box(v, f, (x - post_half, x + post_half,
                                    side - 0.045, side + 0.045,
                                    0.20, 1.12), rail_rgb, scale)
                for z in (0.70, 1.06):
                    _add_box(v, f, (-L / 2, L / 2,
                                    side - 0.045, side + 0.045,
                                    z, z + 0.07), rail_rgb, scale)
        elif variant == "flat_slab_footbridge_no_rails":
            # SLICE-S1CNEAR-001: flat pale slab over the channel, stone
            # abutments, no railings. Deck spans x -L/2..L/2; abutments
            # continue 0.3 m past each deck end (measured 1.6 m deck).
            _add_box(v, f, (-L / 2, L / 2, -0.75, 0.75,
                            0.35, 0.55), (188, 188, 196), scale)
            for end in (-1, 1):
                inner, outer = end * L / 2, end * (L / 2 + 0.3)
                _add_box(v, f, (min(inner, outer), max(inner, outer),
                                -0.75, 0.75, 0.0, 0.35),
                         (143, 143, 145), scale)
        else:
            railing_run(v, f, L, 1.0, tint((80, 75, 70)), scale)
    elif module_id == "rail_track_corridor":
        # FL-4876 RAIL-TRACK-01: the same track as the infrastructure owner
        # and untinted, so an observed run coincides with the modelled track
        # instead of showing as a uniform grey slab.
        _add_box(v, f, (-L / 2, L / 2, -1.5, 1.5, 0.0, 0.12), (153, 153, 153), scale)
        n_ties = max(1, int(L / 0.6))
        for i in range(n_ties):
            x = -L / 2 + (i + 0.5) * L / n_ties
            _add_box(v, f, (x - 0.12, x + 0.12, -0.9835, 0.9835, 0.12, 0.22),
                     (204, 204, 204), scale)
        for side in (-0.5335, 0.5335):
            _add_box(v, f, (-L / 2, L / 2, side - 0.08, side + 0.08, 0.22, 0.36),
                     (51, 51, 51), scale)
    elif module_id == "platform_railing":
        if variant == "white_vertical_bar_platform_rear_fence":
            fence_panel(v, f, L, 1.10, (220, 222, 218), scale, "pickets")
        else:
            railing_run(v, f, L, 1.1, tint((70, 75, 80)), scale)
    else:  # pragma: no cover - guarded by the dispatched representation contract
        raise ValueError(f"no unit builder for {module_id}")
    return v, f


def build_point_mesh(module_id, variant, scale, asset_contract):
    """One freestanding instance mesh for a point-kind module."""
    tint = lambda base: variant_tint(base, variant or "default")
    if (module_id == "street_tree"
            and variant == "mature_broadleaf_frontage_tree"):
        # SF-4876-0473: dense single frontage tree visible against the dark
        # gabled building.  A low trunk and overlapping asymmetric crown
        # preserve that silhouette instead of selecting the generic tree.
        v: list[tuple] = []
        f: list[list[int]] = []
        _add_box(v, f, (-0.18, 0.18, -0.18, 0.18, 0.0, 3.65),
                 (99, 77, 55), scale)
        _add_box(v, f, (-1.70, 1.70, -1.40, 1.40, 3.10, 5.90),
                 (58, 100, 54), scale)
        _add_box(v, f, (-2.20, -0.20, -1.10, 1.00, 3.60, 5.50),
                 (66, 111, 59), scale)
        _add_box(v, f, (0.15, 2.05, -1.00, 1.20, 3.40, 5.70),
                 (51, 91, 48), scale)
        _add_box(v, f, (-1.10, 1.10, -0.95, 0.95, 5.10, 6.60),
                 (72, 117, 62), scale)
        return v, f
    if module_id in ("small_tree", "street_tree"):
        return _build_archetype("tree", scale)
    if module_id == "pole_streetlight":
        if variant == "twin_opposed_path_lamp":
            v: list[tuple] = []
            f: list[list[int]] = []
            _add_box(v, f, (-0.06, 0.06, -0.06, 0.06, 0.0, 5.0),
                     (76, 78, 76), scale)
            _add_box(v, f, (-0.48, 0.48, -0.05, 0.05, 4.78, 4.90),
                     (76, 78, 76), scale)
            _add_box(v, f, (-0.68, -0.25, -0.15, 0.15, 4.72, 4.94),
                     (218, 212, 178), scale)
            _add_box(v, f, (0.25, 0.68, -0.15, 0.15, 4.72, 4.94),
                     (218, 212, 178), scale)
            return v, f
        return _build_archetype("street_lamp", scale)
    v: list[tuple] = []
    f: list[list[int]] = []
    carriageway_w = float(
        asset_contract["cross_section_m"]["carriageway_width_m"])
    if module_id == "manhole_and_grate":
        _add_box(v, f, (-0.3, 0.3, -0.3, 0.3, 0.015, 0.035),
                 tint((90, 92, 88)), scale)
        _add_box(v, f, (-0.36, 0.36, -0.36, 0.36, 0.01, 0.02),
                 tint((130, 132, 128)), scale)
    elif module_id == "meter_and_service_cabinet":
        _add_box(v, f, (-0.5, 0.5, -0.3, 0.3, 0, 1.5), tint((130, 135, 130)), scale)
        _add_box(v, f, (-0.4, 0.4, -0.32, -0.3, 0.6, 1.3), tint((70, 75, 70)), scale)
    elif module_id == "transformer_drum_cluster":
        _add_box(v, f, (-0.12, 0.12, -0.12, 0.12, 0, 8.0), tint((110, 112, 108)), scale)
        _add_box(v, f, (-0.35, 0.35, -0.35, 0.35, 5.5, 6.5), tint((150, 148, 140)), scale)
        _add_box(v, f, (-0.8, 0.8, -0.05, 0.05, 7.7, 7.85), tint((80, 82, 80)), scale)
    elif module_id == "potted_frontage_plant":
        if variant == "stone_faced_raised_shrub_bed":
            stone = (105, 99, 84)
            dark_stone = (77, 73, 64)
            leaf = (57, 91, 51)
            _add_box(v, f, (-1.20, 1.20, -0.55, 0.55,
                            0.0, 0.38), stone, scale)
            for x in (-0.78, -0.25, 0.28, 0.82):
                _add_box(v, f, (x - 0.20, x + 0.20, -0.57, -0.55,
                                0.08, 0.30), dark_stone, scale)
            for x, h in ((-0.75, 1.15), (0.0, 1.35), (0.75, 1.10)):
                _add_box(v, f, (x - 0.38, x + 0.38, -0.43, 0.43,
                                0.30, h), leaf, scale)
        else:
            _add_box(v, f, (-0.2, 0.2, -0.2, 0.2, 0, 0.4),
                     tint((150, 90, 70)), scale)
            _add_box(v, f, (-0.28, 0.28, -0.28, 0.28, 0.35, 0.8),
                     tint((70, 110, 60)), scale)
    elif module_id == "level_crossing_equipment":
        _add_box(v, f, (-0.1, 0.1, -0.1, 0.1, 0, 3.5), tint((200, 200, 195)), scale)
        _add_box(v, f, (-0.3, 0.3, -0.15, 0.15, 3.0, 3.5), tint((60, 60, 60)), scale)
        _add_box(v, f, (-0.15, 0.15, -0.12, 0.12, 3.1, 3.3), tint((220, 50, 40)), scale)
        _add_box(v, f, (0.1, carriageway_w, -0.06, 0.06, 1.0, 1.15),
                 tint((220, 220, 210)), scale)
        _add_box(v, f, (-0.25, 0.25, -0.25, 0.25, 0, 0.5), tint((90, 90, 90)), scale)
    elif module_id == "water_control_structure":
        _add_box(v, f, (-0.5, 0.5, -0.5, 0.5, -0.2, 0.8), tint((110, 95, 75)), scale)
        _add_box(v, f, (-0.3, 0.3, -0.06, 0.06, -0.2, 0.9), tint((80, 70, 60)), scale)
    elif module_id in LINEAR_RUN_POINT_MODULES:
        # Already point geometry -- one post centred on the tile, taking no
        # length.  It was reachable only through build_unit_mesh, which is the
        # path an isolated bollard takes; a bollard on a run arrives here
        # instead.  Same authored mesh, second door.
        _bollard_variant_mesh(v, f, variant, scale, tint)
    else:  # pragma: no cover - guarded by the dispatched representation contract
        raise ValueError(f"no point builder for {module_id}")
    return v, f


def akm_stem(module_id: str, variant: str | None) -> str:
    clean = lambda s: re.sub(r"[^A-Za-z0-9_.-]", "_", s or "default")
    return f"linear_{clean(module_id)}__{clean(variant)}.akm"
