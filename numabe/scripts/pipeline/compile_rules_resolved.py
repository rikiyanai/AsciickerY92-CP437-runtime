#!/usr/bin/env python3
"""Compile resolved public-realm rules for the Numabe connected AOI.

Reads the FL-4813 Phase 1 applicable-law selection receipt (13 spans x 8
subjects, 81 unresolved legal facts) plus the on-disk JP law/standards corpus
index, and resolves every span x subject into a cited value or an explicit
DEFAULT naming the parent rule. Produces rules_resolved.json and a
rules_resolution_log.txt listing each of the 81 facts with its old
reason_code and new status/source.

No network access. Corpus bytes on disk only.
"""

import argparse
import json
import os
import sys

SUBJECTS = [
    "accessibility",
    "article_42_category",
    "marking_form",
    "occupation",
    "railway_boundary",
    "road_structure",
    "setback",
    "sign_form",
]

# Unnamed spans (index -> adjacent lower-m named span route_key to inherit).
ADJACENT_NAMED = {1: "5030310", 7: "4000460", 9: "5030250"}

# Parent rule per subject for DEFAULT rows (from receipt extracted/matched ids).
PARENT_RULE = {
    "accessibility": "JP-ACCESSIBILITY-STD",
    "article_42_category": "OTA-ART42-SETBACK",
    "occupation": "JP-ROAD-STRUCT-ORDER",
    "railway_boundary": "JP-RAILWAY-TECH-STD",
    "road_structure": "JP-ROAD-STRUCT-ORDER",
    "setback": "OTA-ART42-SETBACK",
}

# Base-standard (kubun_3 / base-class) parameter values per subject.
# Each entry: (param_name, value, unit).
BASE_PARAMS = {
    "road_structure": [
        ("carriageway_width_m", 4.0, "m"),
        ("sidewalk_width_m", 2.0, "m"),
        ("drainage_gutter_width_m", 0.3, "m"),
    ],
    "accessibility": [
        ("sidewalk_width_m", 2.0, "m"),
        ("curb_height_cm", 5.0, "cm"),
        ("tactile_paving_width_m", 0.3, "m"),
        ("tactile_paving_placement", "crosswalk_ends_and_guidance_path", "placement"),
        ("cross_slope_standard_percent", 1.0, "percent"),
    ],
    "article_42_category": [
        ("designation_category", "not_designated", "category"),
    ],
    "occupation": [
        ("permitted_occupation_objects", "none", "list"),
        ("utility_pole_spacing_basis", "base_class_reference_no_placed_poles", "basis"),
    ],
    "railway_boundary": [
        ("rail_boundary_fence_type", "trespass_prevention_equipment", "type"),
        ("rail_boundary_fence_height_m", 1.8, "m"),
    ],
    "setback": [
        ("setback_distance_from_centerline_m", 2.0, "m"),
        ("setback_distance_from_river_or_railway_side_m", 4.0, "m"),
    ],
}

# Cited (applicable) subject parameters: (param_name, value, unit, source).
CITED_PARAMS = {
    "sign_form": {
        "rule_ids": ["JP-SIGN-MARK-ORDER-SIGNS", "JP-MLIT-SIGN-CATALOG", "JP-MLIT-SIGN-INSTALL-STD"],
        "params": [
            ("sign_mounting_height_m", 1.8, "m", "JP-MLIT-SIGN-INSTALL-STD ch.3(2)"),
            ("sign_lateral_offset_cm", 25.0, "cm", "JP-MLIT-SIGN-INSTALL-STD ch.3(2)"),
        ],
    },
    "marking_form": {
        "rule_ids": ["JP-SIGN-MARK-ORDER-MARKINGS"],
        "params": [
            ("crosswalk_marking", "Appended-Table-4 crosswalk form", "form",
             "JP-SIGN-MARK-ORDER-MARKINGS Article 6"),
            ("stop_line", "Appended-Table-4 stop-line form", "form",
             "JP-SIGN-MARK-ORDER-MARKINGS Article 6"),
        ],
    },
}


# FL-4813 NUMABE-RULES-02: DEFAULT road cross-section facts, moved here from
# scripts/pipeline/world_surfaces.py so the consumer reads rules, not code.
# Values are authoring DEFAULTs in the ordinance base-class family (see the
# file governing_note: no official legal-width claim is made for any span).
_AUTH_BASIS_ROAD = ("DEFAULT:authoring (JP-ROAD-STRUCT-ORDER base-class "
                     "family; moved from world_surfaces.py; no article-level "
                     "citation)")
_AUTH_BASIS_RAIL = ("DEFAULT:authoring (JP-RAILWAY-TECH-STD family; moved "
                     "from world_surfaces.py; no article-level citation)")
_AUTH_BASIS_OTHER = ("DEFAULT:authoring (moved from world_surfaces.py; no "
                      "ordinance basis)")

ROAD_CLASS_DEFAULTS = {
    "carriageway_width_m_by_highway": {
        "primary": (11.0, "m", _AUTH_BASIS_ROAD),
        "tertiary": (7.0, "m", _AUTH_BASIS_ROAD),
        "residential": (4.5, "m", _AUTH_BASIS_ROAD),
        "unclassified": (4.5, "m", _AUTH_BASIS_ROAD),
        "service": (3.5, "m", _AUTH_BASIS_ROAD),
    },
    "footway_half_width_m_by_kind": {
        "footway": (0.9, "m", _AUTH_BASIS_ROAD),
        "steps": (0.75, "m", _AUTH_BASIS_ROAD),
        "pedestrian": (1.5, "m", _AUTH_BASIS_ROAD),
    },
    "sidewalk_default_width_m": (2.0, "m", _AUTH_BASIS_ROAD),
    "sidewalk_default_classes": ["primary", "tertiary"],
    "lane_width_m": (3.0, "m", _AUTH_BASIS_ROAD),
    "observation_run_extent_m": (8.0, "m", ("DEFAULT:authoring (one "
        "annotation fixes position, not extent)")),
    "bollard_spacing_m": (1.5, "m", ("DEFAULT:authoring (typical vehicle-stop "
        "post spacing 1.2-2.0 m family; no ordinance basis)")),
    "storey_height_m": (3.0, "m", ("DEFAULT:authoring (typical Japanese "
        "residential storey ~3 m family; no ordinance basis)")),
    "default_building_height_m": (9.0, "m", ("DEFAULT:authoring (three "
        "storeys at the storey default where no height evidence exists; "
        "no ordinance basis)")),
    "utility_pole_spacing_m": (30.0, "m", ("DEFAULT:authoring (typical urban "
        "distribution-pole spacing 25-40 m family; no ordinance basis)")),
    "utility_pole_merge_m": (5.0, "m", ("DEFAULT:authoring (same-pole "
        "observation scatter versus distinct poles; no ordinance basis)")),
    "tag_sidewalk_extra_m": (0.5, "m", _AUTH_BASIS_OTHER),
    "explicit_sidewalk_centreline_half_m": (1.0, "m", _AUTH_BASIS_OTHER),
    # FL-4809 NUMABE-REPRESENT-01: single outward offset for every mesh-placed
    # place front, temples included.  One full render cell is 1/1.5 world
    # units ~= 0.195 m at the Numabe content scale, and the deepest front
    # (place_entry_gate roof, half-depth 0.48 m) needs 0.195 + 0.48 = 0.675 m
    # to keep its rear corner outside the one-cell dilation of the baked
    # parent mass.  0.70 m covers that bound with margin; the commercial
    # fronts (half-depth 0.325 m) clear by more.  Replaces the .45/.08
    # literals that buried every non-temple front inside the rasterised block.
    "place_front_outward_offset_m": (0.70, "m", ("DEFAULT:authoring (FL-4809 "
        "REPRESENT-01: one full terrain cell plus half the deepest front "
        "depth, so mesh-placed fronts clear the baked parent mass; moved "
        "from compile_world_ir_fixtures.py literals; no ordinance basis)")),
    "rail_half_width_m": (5.0, "m", _AUTH_BASIS_RAIL),
    "waterway_half_width_m": (1.5, "m", _AUTH_BASIS_OTHER),
    # FL-4809 NUMABE-OBSERVE-01: fallback horizontal field of view for a
    # street-level perspective frame that carries no measured
    # horizontal_fov_deg and no focal_35mm to derive one from.  60 deg is the
    # perspective default already used by register_observations.py and
    # lower_plan_custom.py for the same frame class; Mapillary survey and
    # route captures without intrinsics are that class.
    "observation_default_hfov_deg": (60.0, "deg", ("DEFAULT:authoring (FL-4809 "
        "OBSERVE-01: repo perspective-frame fallback 60 deg per "
        "register_observations.py and lower_plan_custom.py; no measured "
        "intrinsics basis)")),
    "junction_rule": (
        "at every node shared by two or more road carriers (footway-only "
        "nodes excluded), a disc of radius the largest incident carriageway "
        "half-width",
        "m",
        _AUTH_BASIS_OTHER,
    ),
}


# FL-4809 NUMABE-METRIC-01: DEFAULT object-metric class facts. Every reusable
# visual asset carries a physical metre contract; the object-metric receipt
# checks emitted instance extents against these ranges (block intended values
# must fall inside them; emitted scale must stay inside the family allowed
# scale). Width/depth/height are axis ranges in metres for the
# asset at rest; linear families additionally bound the sorted tile axes
# (thickness, height, length). Values are authoring DEFAULTs: measured
# build_final4 mesh ranges lie inside every family below; no ordinance basis.
_AUTH_BASIS_METRIC = ("DEFAULT:authoring (FL-4809 METRIC-01: urban object "
    "class-size priors; measured build_final4 mesh ranges lie inside; no "
    "ordinance basis)")

# family: ((w_lo, w_hi, d_lo, d_hi, h_lo, h_hi, scale_min, scale_max), unit, source)
OBJECT_METRIC_DEFAULTS = {
    "vending_machine": ((0.8, 1.5, 0.5, 1.0, 1.6, 2.1, 0.85, 1.15), "m", _AUTH_BASIS_METRIC),
    "utility_pole": ((0.15, 0.5, 0.15, 2.0, 6.0, 12.0, 0.8, 1.25), "m", _AUTH_BASIS_METRIC),
    "street_lamp": ((0.15, 1.0, 0.15, 1.0, 4.0, 7.0, 0.8, 1.25), "m", _AUTH_BASIS_METRIC),
    "traffic_signal": ((0.3, 0.8, 0.2, 0.6, 2.5, 4.0, 0.85, 1.15), "m", _AUTH_BASIS_METRIC),
    "post_box": ((0.4, 0.9, 0.3, 0.8, 1.2, 1.8, 0.85, 1.15), "m", _AUTH_BASIS_METRIC),
    "public_telephone": ((0.5, 1.0, 0.4, 0.9, 1.5, 2.2, 0.85, 1.15), "m", _AUTH_BASIS_METRIC),
    "bicycle_parking": ((1.0, 2.5, 0.5, 1.2, 0.6, 1.2, 0.85, 1.15), "m", _AUTH_BASIS_METRIC),
    "bench": ((1.2, 2.4, 0.4, 0.8, 0.7, 1.1, 0.85, 1.15), "m", _AUTH_BASIS_METRIC),
    "waste_basket": ((0.4, 0.8, 0.4, 0.8, 0.6, 1.2, 0.85, 1.15), "m", _AUTH_BASIS_METRIC),
    "drinking_water": ((0.4, 1.0, 0.4, 1.0, 0.7, 1.4, 0.85, 1.15), "m", _AUTH_BASIS_METRIC),
    "shelter": ((2.0, 5.0, 1.0, 2.5, 2.2, 2.8, 0.85, 1.15), "m", _AUTH_BASIS_METRIC),
    "clock": ((0.3, 1.0, 0.15, 0.5, 2.0, 4.0, 0.8, 1.25), "m", _AUTH_BASIS_METRIC),
    "fountain": ((1.0, 3.0, 1.0, 3.0, 0.2, 1.5, 0.8, 1.25), "m", _AUTH_BASIS_METRIC),
    "bus_stop": ((0.3, 0.8, 0.1, 0.5, 1.8, 2.6, 0.85, 1.15), "m", _AUTH_BASIS_METRIC),
    "stop_sign_panel": ((0.5, 1.0, 0.1, 0.3, 1.8, 3.0, 0.85, 1.15), "m", _AUTH_BASIS_METRIC),
    "gate_barrier": ((1.0, 3.5, 0.1, 0.5, 0.9, 1.4, 0.8, 1.25), "m", _AUTH_BASIS_METRIC),
    "bollard": ((0.12, 0.35, 0.12, 0.35, 0.6, 1.1, 0.85, 1.15), "m", _AUTH_BASIS_METRIC),
    "railway_signal": ((0.3, 0.8, 0.2, 0.6, 2.5, 4.5, 0.85, 1.15), "m", _AUTH_BASIS_METRIC),
    "railway_ground_equipment": ((0.4, 1.2, 0.2, 0.8, 0.1, 0.8, 0.8, 1.25), "m", _AUTH_BASIS_METRIC),
    "railway_buffer_stop": ((1.0, 2.5, 0.3, 1.0, 0.5, 1.5, 0.8, 1.25), "m", _AUTH_BASIS_METRIC),
    "flagpole": ((0.1, 1.0, 0.1, 0.5, 4.0, 10.0, 0.8, 1.25), "m", _AUTH_BASIS_METRIC),
    "street_cabinet": ((0.6, 1.6, 0.4, 1.0, 1.0, 2.0, 0.85, 1.15), "m", _AUTH_BASIS_METRIC),
    "fire_hydrant": ((0.3, 0.7, 0.2, 0.6, 0.5, 1.0, 0.85, 1.15), "m", _AUTH_BASIS_METRIC),
    "information_board": ((1.0, 2.5, 0.2, 0.6, 1.6, 2.6, 0.85, 1.15), "m", _AUTH_BASIS_METRIC),
    "advertising_sign": ((0.5, 2.0, 0.1, 0.4, 1.0, 3.0, 0.8, 1.25), "m", _AUTH_BASIS_METRIC),
    "notice_panel": ((0.4, 1.2, 0.05, 0.3, 1.2, 2.6, 0.8, 1.25), "m", _AUTH_BASIS_METRIC),
    "curb": ((0.01, 4.5, 0.01, 4.5, 0.02, 1.0, 0.5, 2.0), "m", _AUTH_BASIS_METRIC),
    "drain": ((0.2, 2.5, 0.2, 2.5, 0.2, 1.0, 0.5, 2.0), "m", _AUTH_BASIS_METRIC),
    "wall": ((0.2, 0.5, 0.2, 0.5, 0.5, 2.0, 0.5, 2.0), "m", _AUTH_BASIS_METRIC),
    "fence": ((0.05, 0.3, 0.05, 0.3, 0.8, 2.2, 0.5, 2.0), "m", _AUTH_BASIS_METRIC),
    "vegetation": ((0.15, 2.0, 0.15, 2.0, 0.5, 6.0, 0.5, 2.0), "m", _AUTH_BASIS_METRIC),
    "guardrail": ((0.05, 0.35, 0.05, 0.35, 0.9, 1.3, 0.5, 2.0), "m", _AUTH_BASIS_METRIC),
    "marking": ((0.01, 4.5, 0.01, 4.5, 0.01, 0.5, 0.5, 2.0), "m", _AUTH_BASIS_METRIC),
    "paver": ((0.01, 3.0, 0.01, 3.0, 0.01, 0.1, 0.5, 2.0), "m", _AUTH_BASIS_METRIC),
    "cabinet_box": ((0.05, 1.7, 0.05, 1.7, 0.02, 8.5, 0.5, 2.0), "m", _AUTH_BASIS_METRIC),
    "pedestrian_crossing_marker": ((1.0, 2.0, 0.8, 1.6, 0.01, 0.1, 0.5, 2.0), "m", _AUTH_BASIS_METRIC),
    # Crown upper bound 6.0 m (was 4.0): the hand-reviewed mature broadleaf
    # frontage trees SF-4876-0473/0565 carry a measured 4.25 m crown.
    "tree": ((1.0, 6.0, 1.0, 6.0, 2.0, 8.0, 0.7, 1.5), "m", _AUTH_BASIS_METRIC),
    # MANUAL-FIXTURE-02 object classes photographed in Numabe (SF-4876-0384,
    # 0574 footbridges over the channel; 0187 raised shrub bed; 0289 block
    # wall with square gate piers); bounds bracket the photographed objects.
    "footbridge": ((0.8, 6.0, 0.8, 4.0, 0.2, 1.4, 0.5, 2.0), "m", _AUTH_BASIS_METRIC),
    "planter_bed": ((0.4, 4.0, 0.3, 2.0, 0.3, 2.0, 0.5, 2.0), "m", _AUTH_BASIS_METRIC),
    "wall_with_piers": ((0.2, 0.8, 0.2, 0.8, 0.5, 2.2, 0.5, 2.0), "m", _AUTH_BASIS_METRIC),
    "storefront_front": ((1.0, 8.0, 0.1, 1.5, 2.4, 3.4, 0.8, 1.25), "m", _AUTH_BASIS_METRIC),
    "place_entry_gate": ((1.5, 4.5, 0.2, 1.2, 2.4, 3.0, 0.8, 1.25), "m", _AUTH_BASIS_METRIC),
    "conventional_rail": ((0.5, 4.0, 0.5, 4.0, 0.0, 2.0, 0.8, 1.25), "m", _AUTH_BASIS_METRIC),
    "crossing_equipment": ((0.4, 4.5, 0.4, 4.5, 0.4, 4.5, 0.8, 1.25), "m", _AUTH_BASIS_METRIC),
    "railbed": ((0.1, 3.0, 0.1, 3.0, 0.3, 3.0, 0.5, 2.0), "m", _AUTH_BASIS_METRIC),
    "pole_wire": ((0.05, 6.0, 0.05, 6.0, 0.05, 10.5, 0.5, 2.0), "m", _AUTH_BASIS_METRIC),
    "platform_edge": ((0.05, 0.5, 0.05, 0.5, 0.5, 2.0, 0.5, 2.0), "m", _AUTH_BASIS_METRIC),
    "station_proposal": ((1.0, 6.0, 1.0, 6.0, 1.0, 4.0, 0.5, 2.0), "m", _AUTH_BASIS_METRIC),
    "terrain_patch": ((1.0, 30.0, 1.0, 30.0, 0.01, 1.0, 0.5, 2.0), "m", _AUTH_BASIS_METRIC),
    "water_structure": ((0.3, 3.0, 0.3, 3.0, 0.5, 8.0, 0.5, 2.0), "m", _AUTH_BASIS_METRIC),
}

# FL-4809 NUMABE-FRONT-01: DEFAULT facade archetype by building use, so the
# plan selects archetypes from rules, not from a single hard-coded default.
# Use classes come from OSM building=/shop=/amenity=/office= tags and place
# association; thresholds below are authoring DEFAULTs (see the file
# governing_note: no official claim is made for any span).
_AUTH_BASIS_FACADE = ("DEFAULT:authoring (facade use-class family; no "
                       "ordinance basis)")

FACADE_ARCHETYPE_DEFAULTS_BY_USE = {
    "commercial": ("narrow_mixed_use", "module_id", _AUTH_BASIS_FACADE),
    "mixed": ("narrow_mixed_use", "module_id", _AUTH_BASIS_FACADE),
    "residential": ("residential_facade_roofed_entry", "module_id",
                      _AUTH_BASIS_FACADE),
    "service": ("roller_shutter_facade", "module_id", _AUTH_BASIS_FACADE),
}

# Variant by measured front-run length tier (documented DEFAULT mapping;
# catalog order within each module).
FACADE_VARIANT_LENGTH_TIERS = {
    "residential_facade_roofed_entry": [
        (6.0, "deep_projecting_roof_overhang"),
        (8.0, "external_stair_and_balcony"),
        (10.0, "external_stair_stack"),
        (12.0, "stacked_balcony_facade"),
        (15.0, "stacked_balcony_overhang"),
        (float("inf"), "weathered_tile_roof"),
    ],
    "narrow_mixed_use": [
        (8.0, "corner_commercial_with_signband"),
        (10.0, "curved_corner_facade"),
        (13.0, "lowrise_service_facade_with_signband"),
        (float("inf"), "partial_multistorey_facade"),
    ],
    "residential_facade_with_boundary_sign": [
        (float("inf"), "lowrise_tile_roof"),
    ],
    "roller_shutter_facade": [
        (float("inf"), "horizontal_shutter_with_tiled_base"),
    ],
}


def facade_archetype_defaults_block():
    """Top-level facade_archetype_defaults_by_use block."""
    return {
        "by_use": {
            use: {"value": mod, "unit": unit, "source": source}
            for use, (mod, unit, source)
            in FACADE_ARCHETYPE_DEFAULTS_BY_USE.items()
        },
        "variant_length_tiers_m": {
            mod: [{"max_length_m": ("inf" if m == float("inf") else m),
                    "variant": v}
                   for m, v in tiers]
            for mod, tiers in FACADE_VARIANT_LENGTH_TIERS.items()
        },
    }

# FL-4809 NUMABE-PLAN-02: proxy semantics as a recorded DEFAULT fact. The
# representation planner sits above the lowerers; a semantic identity
# standing in for an unobserved physical thing is an authoring DEFAULT,
# never a measured claim. Match predicates read canonical claim VALUES
# only (never method text, never OSM tags).
_AUTH_BASIS_REPRESENTATION = ("DEFAULT:authoring (FL-4809 PLAN-02: proxy "
    "semantics for unobserved physical things; no ordinance basis)")

# One row per semantic invention the plan authorizes. All conditions in
# match must hold. claim_property is dotted (nested claim values allowed).
REPRESENTATION_SEMANTIC_IDENTITY_PROXY = [
    # FL-4876 STATION-STOP-MAP-01: the station node is drawn as the platforms the infrastructure owner builds from
    # it (compile_world_ir_fixtures station composition, jp.tokyo.small_station_side_platform), so its plan row
    # names that owner; listed first so it wins over station_stop below.
    {"id": "station_platform",
     "match": {"semantic_class": "place",
               "claim_property": "place_category",
               "claim_value": "public_transport=station"},
     "proxy_target": "station_platform", "disposition": "mesh_fixture",
     "representation_kind": "synthetic_identity_proxy",
     "asserts_physical_presence": False},
    # FL-4876 STATION-STOP-MAP-01: a public_transport station / stop_position node is a data point (where a
    # train stops), not a thing. As a mesh_fixture proxy it became a generic sign plate standing on the rail
    # centreline (build_reg_75 nodes 2145028048, 266085809, 7834193975; operator 2026-10-16 "card like things
    # overlapping on train tracks"). The physical station signs are hand-authored from photos and the
    # platforms are built from these same nodes by compile_world_ir_fixtures, so the identity stays on the map
    # only.
    {"id": "station_stop",
     "match": {"semantic_class": "place",
               "claim_property": "place_category",
               "claim_value_prefix": "public_transport="},
     "proxy_target": None, "disposition": "minimap_only",
     "representation_kind": "map_only",
     "asserts_physical_presence": False},
    {"id": "memorial",
     "match": {"semantic_class": "place",
               "claim_property": "place_category",
               "claim_value": "historic=memorial"},
     "proxy_target": "information_board", "disposition": "mesh_fixture",
     "representation_kind": "synthetic_identity_proxy",
     "asserts_physical_presence": False},
    {"id": "entrance_gate",
     "match": {"semantic_class": "station_entrance"},
     "proxy_target": "pedestrian_gate", "disposition": "mesh_fixture",
     "representation_kind": "synthetic_identity_proxy",
     "asserts_physical_presence": False},
    {"id": "rail_crossing",
     "match": {"semantic_class": "crossing", "geometry_kind": "point",
               "claim_property": "cross_section.highway",
               "claim_null": True},
     "proxy_target": "crossing_marker", "disposition": "mesh_fixture",
     "representation_kind": "synthetic_identity_proxy",
     "asserts_physical_presence": False},
    # FL-4876 ATTRACTION-MAP-01: an attraction node names a place, not a tenancy. Sakurazaka (node/4651760389,
    # tourism=attraction) is the 桜坂 slope; the rule-backed facade drew it as a "SAKURAZAKA" shop front on a
    # 0.59 m face of a nearby building (build_reg_75, sideways to its wall). Its own debt record says "a named
    # slope, not a tenancy; no storefront front owed: no building face". Map label only.
    {"id": "attraction_map_only",
     "match": {"semantic_class": "place",
               "claim_property": "place_category",
               "claim_value": "tourism=attraction"},
     "proxy_target": None, "disposition": "minimap_only",
     "representation_kind": "map_only",
     "asserts_physical_presence": False},
    # FL-4876 BIKESHARE-MAP-01: a bicycle rental port is docked bicycles on the pavement, not a tenancy; the rule-
    # backed facade drew one as a shop front with no wall behind it. The dock itself is a hand-authored fixture.
    {"id": "bicycle_rental_map_only",
     "match": {"semantic_class": "place",
               "claim_property": "place_category",
               "claim_value": "amenity=bicycle_rental"},
     "proxy_target": None, "disposition": "minimap_only",
     "representation_kind": "map_only",
     "asserts_physical_presence": False},
    {"id": "locality_map_only",
     "match": {"semantic_class": "place",
               "claim_property": "place_category", "claim_absent": True,
               "triage_group": "A"},
     "proxy_target": None, "disposition": "minimap_only",
     "representation_kind": "map_only",
     "asserts_physical_presence": False},
]

REPRESENTATION_DEBT_POLICY_BY_IMPORTANCE = {
    "background_A":
        "synthesis allowed under pack rule",
    "route_visible_B":
        "synthesis only as a marked archetype or proxy",
    "route_visible_C":
        "observation_owed blocks evidence_complete until observation, "
        "authored waiver, or explicit synthetic proxy",
}

REPRESENTATION_ELEVATED_SURFACE_OMISSION = {
    "claim_property": "elevation_relation",
    "omit_when": "bridge tag present and not a no/false/0 value, or "
                 "numeric layer above grade",
    "disposition": "intentional_omission",
    "reason": "bridge_deck",
    "owner_stage": "none",
}

# FL-4809 NUMABE-ATTACH-01: anchor-kind tolerances for the canonical
# attachment contract. Association radii are authoring DEFAULTs recorded
# here, never literals in the contract module. carrier_association_m bounds
# the nearest-carrier search for chainage anchors (same order as the
# historical front-search radius the anchors were validated against);
# face_resolve_m bounds the nearest-face search for face anchors (same
# order as the historical face-attach envelope).
_AUTH_BASIS_ATTACHMENT = ("DEFAULT:authoring (FL-4809 ATTACH-01: "
    "anchor-kind association tolerances; no ordinance basis)")

ATTACHMENT_TOLERANCES = {
    "carrier_association_m": (12.0, "m", _AUTH_BASIS_ATTACHMENT),
    "face_resolve_m": (6.0, "m", _AUTH_BASIS_ATTACHMENT),
}


def attachment_tolerances_block():
    """Top-level attachment_tolerances block: every value with source."""
    return {
        key: {"value": value, "unit": unit, "source": source}
        for key, (value, unit, source) in ATTACHMENT_TOLERANCES.items()
    }

# FL-4809 NUMABE-SPATIAL-03: roadside lateral band for carrier_chainage
# rows. Roadside hardware (poles, signs) legitimately sits at the sidewalk
# edge, so the expectation is the plan lateral offset, not the centreline:
# residual = |emitted lateral - planned lateral_offset_m| must lie within
# this band. Set from pole01 data: 12 exact placements + 0.70 m vs >=1.37 m
# deviations split the gap at 1.0 m. Authoring DEFAULT, recorded here.
_AUTH_BASIS_ROADSIDE = ("DEFAULT:authoring (FL-4809 SPATIAL-03: roadside "
    "hardware lateral placement band; no ordinance basis)")
ROADSIDE_LATERAL_BAND = {
    "roadside_lateral_band_m": (1.0, "m", _AUTH_BASIS_ROADSIDE),
}


# FL-4876 INVENTORY-FACTS-01. Route inventory binning and corridor width.
#
# compile_spatial_coherence_receipt refuses to run on its own constants --
# "spatial coherence requires the generation's resolved rules; verifier-side
# default laws are forbidden" -- and then reads rules.inventory, which no
# producer ever wrote. The verifier is right and the block was simply missing.
#
# The numbers are NOT invented here. They are the two documented DEFAULTs in
# compile_route_streetscape_inventory.py:35-36, whose own comment says they are
# "Recorded DEFAULT facts, proposed as rules facts in the receipt. Read from
# --rules (rules.inventory) when present, else these documented DEFAULTs."
# This is that promotion: the generation now owns the law, and both the
# inventory and the coherence verifier read it from one place instead of one
# tolerating a default the other forbids.
_AUTH_BASIS_INVENTORY = (
    "DEFAULT:authoring (FL-4876 INVENTORY-FACTS-01: route inventory binning "
    "and route-visible corridor width, promoted verbatim from the documented "
    "defaults in compile_route_streetscape_inventory.py; no ordinance basis)")
INVENTORY_FACTS = {
    "bin_length_m": (25.0, "m", _AUTH_BASIS_INVENTORY),
    "route_visible_corridor_m": (20.0, "m", _AUTH_BASIS_INVENTORY),
}


def inventory_block():
    """Top-level inventory block.

    SHAPE MATTERS HERE. Unlike attachment_tolerances and object_metric_defaults,
    both consumers of this block read a BARE NUMBER --
    compile_route_streetscape_inventory.py:198 and
    compile_spatial_coherence_receipt.py:278 each do
    ``float(inv["bin_length_m"])``. Wrapping the value in the usual
    {value, unit, source} envelope would raise TypeError in both. So the
    numbers sit at the top of the block and their provenance travels beside
    them, where an extra key is harmless to a reader that only takes the two
    it needs.
    """
    block = {key: value for key, (value, _u, _s) in INVENTORY_FACTS.items()}
    block["unit"] = "m"
    block["source"] = _AUTH_BASIS_INVENTORY
    return block


def roadside_lateral_band_block():
    """Top-level roadside_lateral_band block: band with source."""
    return {
        key: {"value": value, "unit": unit, "source": source}
        for key, (value, unit, source) in ROADSIDE_LATERAL_BAND.items()
    }

# FL-4809 NUMABE-JEV-01: typed-decision thresholds for the perception /
# reconstruction boundary. accept_confidence admits a decided row;
# review_floor parks weak evidence as observation debt; between them routes
# to opus review. Authoring DEFAULTs, recorded here, never literals in jev.
_AUTH_BASIS_JEV = ("DEFAULT:authoring (FL-4809 JEV-01: typed decision "
    "thresholds; no ordinance basis)")

JEV_DECISION_THRESHOLDS = {
    "accept_confidence": (0.7, "ratio", _AUTH_BASIS_JEV),
    "review_floor": (0.4, "ratio", _AUTH_BASIS_JEV),
    # FL-4809 NUMABE-OBSERVE-05: human_review rows get the same typed Opus
    # review but stay human_review below this stricter bar (no human is
    # available in-session). Authoring DEFAULT, recorded here with the rest.
    "human_review_accept_confidence": (0.85, "ratio", _AUTH_BASIS_JEV),
}

# FL-4809 NUMABE-JEV-03: code owns facts that do not require a semantic
# judgment. A measured Mapillary object token that resolves to an authored
# catalog template is a routine representation decision. Observed markings
# and flat driveway/apron tokens are routine surface decisions. Jev remains
# authoritative only where neither deterministic rule applies. This prevents
# absent OCR/VLM enrichment from vetoing authored generic furniture or an
# observed surface class that has no object asset by design.
_AUTH_BASIS_JEV_ROUTINE = ("DEFAULT:authoring (FL-4809 JEV-03: exact "
    "Mapillary class plus verified catalog coverage is routine; no ordinance "
    "or site-specific identity claim)")

JEV_ROUTINE_RESOLUTION = {
    "catalog_match": {
        "routing": "decided",
        "representation": "existing_asset",
        "source": _AUTH_BASIS_JEV_ROUTINE,
    },
    "observed_road_marking": {
        "semantic_class": "road_marking",
        "object_value_prefix": "marking--",
        "routing": "decided",
        "representation": "procedural_template",
        "source": _AUTH_BASIS_JEV_ROUTINE,
    },
    "observed_flat_surface": {
        "semantic_class": "fixture",
        "object_value_prefix": "construction--flat--",
        "routing": "decided",
        "representation": "procedural_template",
        "source": _AUTH_BASIS_JEV_ROUTINE,
    },
    "unmatched": {
        "routing": "model_decision",
        "representation": "unresolved",
        "source": _AUTH_BASIS_JEV_ROUTINE,
    },
}


def jev_thresholds_block():
    """Top-level jev_thresholds block: every value with source."""
    return {
        key: {"value": value, "unit": unit, "source": source}
        for key, (value, unit, source) in JEV_DECISION_THRESHOLDS.items()
    }


def jev_routine_resolution_block():
    """Deterministic routine-object law; Jev handles only the remainder."""
    return dict(JEV_ROUTINE_RESOLUTION)

# FL-4809 NUMABE-PLAN-04: street-sighting policy for fused Mapillary
# features. sighting_frames_min admits a sighting as observed: every
# retained Mapillary frame count is redacted to a single placeholder, so
# multiplicity is unknown and the gate is presence (>= 1), not corroboration.
# beyond_carrier_budget routes a sighting past the carrier association
# budget to freestanding ground placement, never to debt (the thing exists;
# only its carrier relation is unmeasured).
_AUTH_BASIS_STREET = ("DEFAULT:authoring (FL-4809 PLAN-04: street "
    "sighting policy; no ordinance basis)")

STREET_FUSION_POLICY = {
    "sighting_frames_min": (1, "count", _AUTH_BASIS_STREET),
    "beyond_carrier_budget": ("freestanding", "disposition",
                               _AUTH_BASIS_STREET),
}


def street_fusion_policy_block():
    """Top-level street_fusion_policy block: every value with source."""
    return {
        "sighting_frames_min": {
            "value": STREET_FUSION_POLICY["sighting_frames_min"][0],
            "unit": "count",
            "source": _AUTH_BASIS_STREET},
        "beyond_carrier_budget": {
            "value": STREET_FUSION_POLICY["beyond_carrier_budget"][0],
            "unit": "disposition",
            "source": _AUTH_BASIS_STREET},
    }


# FL-4809 NUMABE-PLAN-06: object_value token to authored template family, so
# asset matching reads policy instead of planner literals. Consumers collect
# all matching tokens and select the unique longest match; list order is not
# semantic authority. Traffic cones are transient by class:
# cone rows omit unless separately evidenced (cone_evidence_frames_min).
_AUTH_BASIS_STREET_TEMPLATE = ("DEFAULT:authoring (FL-4809 PLAN-06: "
    "Mapillary object_value to template family; no ordinance basis)")

STREET_TEMPLATE_FAMILIES = [
    ("utility-pole", "jp.urban.utility_pole_concrete_v1"),
    ("traffic-sign", "jp.urban.traffic_sign_v1"),
    ("regulatory--stop", "jp.urban.stop_sign_v1"),
    ("advertisement", "jp.urban.advertising_sign_v1"),
    ("information", "jp.urban.information_board_v1"),
    ("street-light", "jp.urban.street_lamp_v1"),
    ("traffic-light", "jp.urban.traffic_signal_v1"),
    ("junction-box", "jp.urban.street_cabinet_v1"),
    ("trash-can", "jp.urban.waste_basket_v1"),
    ("regulatory", "jp.traffic.regulatory_family_v1"),
    ("bench", "jp.urban.bench_v1"),
    ("bus-stop", "jp.urban.bus_stop_v1"),
    ("bus_stop", "jp.urban.bus_stop_v1"),
    ("bollard", "jp.urban.bollard_v1"),
    ("vending-machine", "jp.urban.vending_machine_rectangular_v1"),
    ("tree", "jp.urban.street_tree_v1"),
    ("vegetation", "jp.urban.street_tree_v1"),
    ("sign--store", "jp.urban.store_signboard_v1"),
    ("banner", "jp.urban.banner_sign_v1"),
    ("manhole", "jp.urban.manhole_cover_v1"),
    ("catch-basin", "jp.urban.catch_basin_grate_v1"),
    ("traffic-cone", "jp.urban.traffic_cone_v1"),
    ("driveway", "jp.urban.driveway_apron_v1"),
    ("sign--other", "jp.urban.generic_sign_plate_v1"),
    ("warning", "jp.urban.warning_sign_v1"),
    ("water-valve", "jp.urban.water_valve_cover_v1"),
    ("bike-rack", "jp.urban.bicycle_parking_rack_v1"),
    ("fire-hydrant", "jp.urban.fire_hydrant_v1"),
    ("sign--back", "jp.urban.generic_sign_plate_v1"),
]

CONE_EVIDENCE_FRAMES_MIN = (2, "count", _AUTH_BASIS_STREET_TEMPLATE)


def street_template_families_block():
    """Top-level street_template_families block with provenance."""
    return {
        "families": [
            {"object_token": sub, "match": "contains",
             "template_id": tid,
             "source": _AUTH_BASIS_STREET_TEMPLATE}
            for sub, tid in STREET_TEMPLATE_FAMILIES],
        "cone_evidence_frames_min": {
            "value": CONE_EVIDENCE_FRAMES_MIN[0],
            "unit": "count",
            "source": _AUTH_BASIS_STREET_TEMPLATE},
    }
# FL-4809 NUMABE-MERGE-01: Mapillary fusion budgets as DEFAULT facts.

# FL-4809 NUMABE-PLAN-07: fixture archetype pair to authored template, so
# asset matching reads policy instead of planner literals. Pairs are the
# fixture_archetype claim values the ingest emits; each row names the
# attested template that renders it. No row invents an asset.
_AUTH_BASIS_ARCHETYPE_TEMPLATE = ("DEFAULT:authoring (FL-4809 PLAN-07: "
    "fixture archetype to template; no ordinance basis)")

FIXTURE_ARCHETYPE_TEMPLATES = [
    ("amenity=bicycle_parking", "jp.urban.bicycle_parking_rack_v1"),
    ("highway=traffic_signals", "jp.urban.traffic_signal_v1"),
    ("amenity=post_box", "jp.urban.post_box_v1"),
    ("barrier=bollard", "jp.urban.bollard_v1"),
    ("amenity=telephone", "jp.urban.public_telephone_v1"),
    ("highway=bus_stop", "jp.urban.bus_stop_v1"),
    ("highway=stop", "jp.urban.stop_sign_v1"),
    # FL-4876. tourism=information is a mapped, surveyed fixture class and
    # jp.urban.information_board_v1 is authored in the japan_urban pack --
    # the proxy path at compile_representation_plan.py already names that
    # very template for its information_board target. Only this row and the
    # ingest claim key were missing, so a map board on the station wall
    # arrived as new_asset_required for an asset that exists.
    ("tourism=information", "jp.urban.information_board_v1"),
]


def fixture_archetype_templates_block():
    """Top-level fixture_archetype_templates block with provenance."""
    return {
        "rows": [
            {"archetype": pair, "template_id": tid,
             "source": _AUTH_BASIS_ARCHETYPE_TEMPLATE}
            for pair, tid in FIXTURE_ARCHETYPE_TEMPLATES],
    }
# Per-attribute authority needs a merge distance per class and a position
# sigma; Mapillary serves neither, so these are authoring DEFAULTs with
# provenance. Sign clusters need the tight budget (several faces share one
# post); poles are sparse and GPS-strong. Consumed by
# compile_canonical_entities street fusion; not wired into main() counts.
_AUTH_BASIS_MERGE = ("DEFAULT:authoring (FL-4809 MERGE-01: Mapillary fusion "
    "budgets; Mapillary serves no per-feature sigma or merge distance; no "
    "ordinance basis)")

MAPILLARY_POSITION_SIGMA_M = 5.0

# class: (budget_m, unit, source)
MERGE_BUDGET_M = {
    "utility_pole": (6.0, "m", _AUTH_BASIS_MERGE),
    "fixture": (4.0, "m", _AUTH_BASIS_MERGE),
    "sign": (2.5, "m", _AUTH_BASIS_MERGE),
    "road_marking": (3.0, "m", _AUTH_BASIS_MERGE),
}


def merge_budget_block():
    """Top-level merge_budget block: every value with unit + source."""
    return {
        "mapillary_position_sigma_m": {
            "value": MAPILLARY_POSITION_SIGMA_M, "unit": "m",
            "source": _AUTH_BASIS_MERGE},
        "merge_budget_m": {
            cls: {"value": val, "unit": unit, "source": src}
            for cls, (val, unit, src) in MERGE_BUDGET_M.items()},
    }

# FL-4809 NUMABE-SPATIAL-02: object-metric ratio tolerance. Sanity ratios
# compare an emitted median height against an intended height (family band
# midpoint or the person basis); the band is the intended ratio widened by
# this fraction on each side. Authoring DEFAULT, recorded here, never a
# literal in the metric receipt.
_AUTH_BASIS_RATIO = ("DEFAULT:authoring (FL-4809 SPATIAL-02: sanity-ratio "
    "band fraction; no ordinance basis)")

OBJECT_METRIC_RATIO_TOLERANCE_FRACTION = (0.3, "fraction", _AUTH_BASIS_RATIO)


def object_metric_defaults_block():
    """Top-level object_metric_defaults block: every range with unit + source."""
    block = {}
    for family, (values, unit, source) in OBJECT_METRIC_DEFAULTS.items():
        (w_lo, w_hi, d_lo, d_hi, h_lo, h_hi, scale_min, scale_max) = values
        block[family] = {
            "width_m": {"min": w_lo, "max": w_hi, "unit": unit, "source": source},
            "depth_m": {"min": d_lo, "max": d_hi, "unit": unit, "source": source},
            "height_m": {"min": h_lo, "max": h_hi, "unit": unit, "source": source},
            "allowed_scale": {"min": scale_min, "max": scale_max, "unit": "ratio",
                              "source": source},
        }
    return block


REPRESENTATION_BELOW_GRADE_OMISSION = {
    "claim_property": "elevation_relation",
    "omit_when": "numeric layer below grade, or tunnel tag present and "
                 "not a no/false/0 value without a layer above grade",
    "disposition": "intentional_omission",
    "reason": "below_grade",
    "owner_stage": "none",
}

REPRESENTATION_UNMAPPED_RULE = {
    "disposition": "intentional_omission",
    "reconstruction_class": "B",
    "owner_stage": "none",
}

REPRESENTATION_OBSERVED_DEFAULTS = {
    "ground_surface_classes":
        ["road", "sidewalk", "crossing", "terrain", "railway_boundary",
         "bridge_deck"],
    "physical_fixture_classes": ["fixture", "sign"],
    "zebra_crossing": {"claim_property": "cross_section.highway",
                       "claim_null": False},
    "mass_class": "building_footprint",
}

# FL-4809 NUMABE-PLAN-08: paint has no mesh asset. Classification uses the
# observation's verbatim object_value; this policy never supplies geometry.
_AUTH_BASIS_STREET_MARKING = (
    "DEFAULT:authoring (FL-4809 R-STREET-MARKING: resolved Mapillary road "
    "markings are surface paint; family follows the observed class; no "
    "ordinance, dimension, or licence claim)")
REPRESENTATION_STREET_MARKING = {
    "id": "R-STREET-MARKING",
    "semantic_class": "road_marking",
    "claim_property": "object_value",
    "attachment_state": "resolved",
    "anchor_kind": "carrier_chainage",
    "disposition": "surface_decal",
    "representation_kind": "surface",
    "owner_stage": "stamp",
    "family_by_class_token": {
        "crosswalk-zebra": "crosswalk",
        "crosswalk": "crosswalk",
        "stop-line": "stop_line",
        "lane-line": "lane_line",
        "arrow": "arrow",
    },
    "other_family": "other",
}

def representation_policy_block():
    """Top-level representation_policy block: every row carries its source."""
    def row(r):
        return {**r, "source": _AUTH_BASIS_REPRESENTATION}
    return {
        "semantic_identity_proxy":
            [row(r) for r in REPRESENTATION_SEMANTIC_IDENTITY_PROXY],
        "debt_policy_by_importance": {
            "value": REPRESENTATION_DEBT_POLICY_BY_IMPORTANCE,
            "source": _AUTH_BASIS_REPRESENTATION},
        "elevated_surface_omission": {
            "value": REPRESENTATION_ELEVATED_SURFACE_OMISSION,
            "source": _AUTH_BASIS_REPRESENTATION},
        "below_grade_omission": {
            "value": REPRESENTATION_BELOW_GRADE_OMISSION,
            "source": _AUTH_BASIS_REPRESENTATION},
        "unmapped_rule": {
            "value": REPRESENTATION_UNMAPPED_RULE,
            "source": _AUTH_BASIS_REPRESENTATION},
        "observed_defaults": {
            "value": REPRESENTATION_OBSERVED_DEFAULTS,
            "source": _AUTH_BASIS_REPRESENTATION},
        "street_marking": {
            "value": REPRESENTATION_STREET_MARKING,
            "source": _AUTH_BASIS_STREET_MARKING},
    }


def road_class_defaults_block():
    """Top-level road_class_defaults block: every value with unit + source."""
    block = {}
    for key, entry in ROAD_CLASS_DEFAULTS.items():
        if isinstance(entry, dict):
            block[key] = {
                name: {"value": value, "unit": unit, "source": source}
                for name, (value, unit, source) in entry.items()
            }
        elif isinstance(entry, tuple):
            value, unit, source = entry
            block[key] = {"value": value, "unit": unit, "source": source}
        else:
            block[key] = {"value": list(entry), "unit": "class_list",
                           "source": _AUTH_BASIS_ROAD}
    return block


def cited_subject(subject):
    spec = CITED_PARAMS[subject]
    return {
        "status": "cited",
        "rule_ids": list(spec["rule_ids"]),
        "parameters": {
            name: {"value": value, "unit": unit, "source": source}
            for name, value, unit, source in spec["params"]
        },
    }


def default_subject(subject, source, rule_ids):
    return {
        "status": "default",
        "rule_ids": list(rule_ids),
        "parameters": {
            name: {"value": value, "unit": unit, "source": source}
            for name, value, unit in BASE_PARAMS[subject]
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--receipt", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.receipt, encoding="utf-8") as f:
        receipt = json.load(f)
    with open(args.corpus, encoding="utf-8") as f:
        corpus = json.load(f)
    corpus_rule_ids = {r.get("rule_id") for r in corpus.get("rules", [])}

    receipt_spans = receipt["spans"]
    assert len(receipt_spans) == 13, f"expected 13 spans, got {len(receipt_spans)}"

    out_spans = []
    log_lines = []
    cited_rows = 0
    default_rows = 0

    for i, sp in enumerate(receipt_spans):
        route_key = sp.get("route_key")
        unnamed = route_key is None
        subjects_out = {}
        for subject in SUBJECTS:
            entry = sp["subjects"][subject]
            status = entry.get("status")
            reasons = entry.get("reason_codes", [])
            if status == "applicable":
                subjects_out[subject] = cited_subject(subject)
                cited_rows += 1
                continue
            # Unresolved fact -> DEFAULT per policy.
            reason = reasons[0] if reasons else "unknown_reason"
            matched = entry.get("matched_rule_ids") or []
            extracted = entry.get("extracted_rule_ids") or []
            parent = PARENT_RULE[subject]
            assert parent in corpus_rule_ids, f"parent {parent} missing from corpus"
            rule_ids = matched or extracted or [parent]
            if subject in ("road_structure", "occupation") and unnamed and \
                    reason == "predicate_unproven":
                adj = ADJACENT_NAMED[i]
                source = f"DEFAULT:{adj}"
                detail = (f"predicate_unproven -> copy adjacent named span values, "
                          f"source {source}")
            elif reason == "designation_unproven":
                source = "DEFAULT:not_designated"
                detail = (f"designation_unproven -> DEFAULT:not_designated with "
                          f"{parent} base-standard parameters")
            elif reason == "predicate_unproven":
                source = f"DEFAULT:{parent}"
                detail = (f"predicate_unproven (named span) -> {parent} base-class "
                          f"values, source {source}")
            elif reason == "zero_matched_rules":
                source = f"DEFAULT:{parent}"
                detail = (f"zero_matched_rules -> DEFAULT:{parent} base class "
                          f"(no occupation objects)")
            else:
                raise SystemExit(f"span {i} subject {subject}: unhandled reason {reason}")
            subjects_out[subject] = default_subject(subject, source, rule_ids)
            default_rows += 1
            log_lines.append(
                f"span {i} [{sp['span_m'][0]}-{sp['span_m'][1]}m] x {subject}: "
                f"old reason_code={reason} -> new status=default source={source} "
                f"rule_ids={','.join(rule_ids)} | {detail}"
            )
        if unnamed:
            log_lines.append(
                f"span {i} [{sp['span_m'][0]}-{sp['span_m'][1]}m] x owner(span-level): "
                f"old reason_code=owner_unproven -> new administrator=DEFAULT:ota_managed "
                f"legal_class=DEFAULT:kubun_3"
            )
        out_spans.append({
            "span_index": i,
            "route_key": route_key if route_key is not None else (
                f"DEFAULT:unnamed_{sp['span_m'][0]:g}_{sp['span_m'][1]:g}"),
            "route_name_ja": sp.get("route_name_ja") or "DEFAULT:unnamed_span",
            "management_class": sp.get("management_class")
            if not unnamed else "DEFAULT:ota_managed",
            "administrator": "ota_ward" if not unnamed else "DEFAULT:ota_managed",
            "legal_class": "kubun_3" if not unnamed else "DEFAULT:kubun_3",
            "span_m": [sp["span_m"][0], sp["span_m"][1]],
            "subjects": subjects_out,
        })

    # Falsifier gate: no holes allowed.
    assert len(out_spans) == 13
    total_subjects = 0
    for sp in out_spans:
        assert len(sp["subjects"]) == 8, f"span {sp['span_index']} subject count"
        for name, subj in sp["subjects"].items():
            total_subjects += 1
            assert subj["status"] in ("cited", "default"), name
            assert subj["rule_ids"], name
            assert subj["parameters"], name
            for pname, p in subj["parameters"].items():
                for field in ("value", "unit", "source"):
                    v = p[field]
                    assert v is not None, f"{name}.{pname}.{field} is null"
                    assert v != "unresolved" and v != "unknown", f"{name}.{pname}.{field}"
                    if isinstance(v, str):
                        assert v.strip() != "", f"{name}.{pname}.{field} empty"
    assert total_subjects == 104, total_subjects
    assert cited_rows == 26, cited_rows
    assert default_rows == 78, default_rows
    for proxy_row in REPRESENTATION_SEMANTIC_IDENTITY_PROXY:
        log_lines.append(
            f"representation_policy x semantic_identity_proxy[{proxy_row['id']}]: "
            f"new status=default source={_AUTH_BASIS_REPRESENTATION}")
    log_lines.append(
        "representation_policy x debt_policy_by_importance: new status=default "
        f"source={_AUTH_BASIS_REPRESENTATION}")
    log_lines.append(
        "representation_policy x elevated_surface_omission: new status=default "
        f"source={_AUTH_BASIS_REPRESENTATION}")
    log_lines.append(
        "representation_policy x below_grade_omission: new status=default "
        f"source={_AUTH_BASIS_REPRESENTATION}")
    log_lines.append(
        "representation_policy x unmapped_rule: new status=default "
        f"source={_AUTH_BASIS_REPRESENTATION}")
    log_lines.append(
        "representation_policy x observed_defaults: new status=default "
        f"source={_AUTH_BASIS_REPRESENTATION}")
    log_lines.append(
        "representation_policy x R-STREET-MARKING: new status=default "
        f"source={_AUTH_BASIS_STREET_MARKING}")
    for tolerance_key in ATTACHMENT_TOLERANCES:
        log_lines.append(
            "attachment_tolerances x %s: new status=default "
            "source=%s" % (tolerance_key, _AUTH_BASIS_ATTACHMENT))
    for inventory_key in INVENTORY_FACTS:
        log_lines.append(
            "inventory x %s: new status=default "
            "source=%s" % (inventory_key, _AUTH_BASIS_INVENTORY))
    for jev_key in JEV_DECISION_THRESHOLDS:
        log_lines.append(
            "jev_thresholds x %s: new status=default "
            "source=%s" % (jev_key, _AUTH_BASIS_JEV))
    for routine_key in JEV_ROUTINE_RESOLUTION:
        log_lines.append(
            "jev_routine_resolution x %s: new status=default "
            "source=%s" % (routine_key, _AUTH_BASIS_JEV_ROUTINE))
    for street_key in ("sighting_frames_min", "beyond_carrier_budget"):
        log_lines.append(
            "street_fusion_policy x %s: new status=default "
            "source=%s" % (street_key, _AUTH_BASIS_STREET))
    for roadside_key in ROADSIDE_LATERAL_BAND:
        log_lines.append(
            "roadside_lateral_band x %s: new status=default "
            "source=%s" % (roadside_key, _AUTH_BASIS_ROADSIDE))
    for sub, tid in STREET_TEMPLATE_FAMILIES:
        log_lines.append(
            "street_template_families x %s -> %s: new status=default "
            "source=%s" % (sub, tid, _AUTH_BASIS_STREET_TEMPLATE))
    log_lines.append(
        "street_template_families x cone_evidence_frames_min: "
        "new status=default source=%s" % (_AUTH_BASIS_STREET_TEMPLATE,))
    for pair, tid in FIXTURE_ARCHETYPE_TEMPLATES:
        log_lines.append(
            "fixture_archetype_templates x %s -> %s: new status=default "
            "source=%s" % (pair, tid, _AUTH_BASIS_ARCHETYPE_TEMPLATE))
    # FL-4809 POPPLAN-03: log both height priors with their source strings.
    for height_key in ("storey_height_m", "default_building_height_m"):
        log_lines.append(
            "road_class_defaults x %s: new status=default source=%s"
            % (height_key, ROAD_CLASS_DEFAULTS[height_key][2]))
    # Account for the facts actually emitted by each semantic owner.  A fixed
    # historical total made adding or removing an owned rule look like a
    # correctness failure even when every current fact was logged.
    unnamed_span_logs = sum(
        span["route_name_ja"] == "DEFAULT:unnamed_span"
        for span in out_spans)
    expected_log_lines = (
        default_rows
        + unnamed_span_logs
        + len(REPRESENTATION_SEMANTIC_IDENTITY_PROXY)
        + 6  # five representation policies plus R-STREET-MARKING
        + len(ATTACHMENT_TOLERANCES)
        + len(JEV_DECISION_THRESHOLDS)
        + len(JEV_ROUTINE_RESOLUTION)
        + 2  # street fusion policy facts
        + len(INVENTORY_FACTS)  # FL-4876 INVENTORY-FACTS-01
        + len(ROADSIDE_LATERAL_BAND)
        + len(STREET_TEMPLATE_FAMILIES)
        + 1  # cone evidence threshold
        + len(FIXTURE_ARCHETYPE_TEMPLATES)
        + 2  # building height priors
    )
    assert len(log_lines) == expected_log_lines, (
        f"rule log accounting mismatch: expected {expected_log_lines} "
        f"from emitted owners, got {len(log_lines)}")
    assert len(set(log_lines)) == len(log_lines), (
        "rule resolution log contains duplicate facts")
    assert all(line.strip() for line in log_lines), (
        "rule resolution log contains an empty fact")

    doc = {
        "schema": "y92.numabe_rules_resolved.v1",
        "task_id": "NUMABE-RULES-01",
        "governing_note": ("Authoring defaults only; no official legal-width, "
                            "Article 42, boundary, presence, or condition claim "
                            "is made for any span."),
        "defaults_policy": {
            "unnamed_spans": "administrator DEFAULT:ota_managed, legal class DEFAULT:kubun_3",
            "designation_unproven": "DEFAULT:not_designated with base-standard parameters",
            "predicate_unproven": "copy adjacent named span resolved values as DEFAULT:<adjacent route_key>",
            "zero_matched_rules": "DEFAULT:JP-ROAD-STRUCT-ORDER base class",
        },
        "inputs": {
            "receipt": args.receipt,
            "corpus": args.corpus,
        },
        "counts": {
            "spans": 13,
            "subjects_per_span": 8,
            "subject_rows": 104,
            "cited_rows": cited_rows,
            "default_rows": default_rows,
            "street_fusion_rows": 2,
            "street_template_rows": len(STREET_TEMPLATE_FAMILIES),
            "fixture_archetype_rows": 7,
            "jev_routine_resolution_rows": len(JEV_ROUTINE_RESOLUTION),
        },
        "road_class_defaults": road_class_defaults_block(),
        "facade_archetype_defaults_by_use": facade_archetype_defaults_block(),
        "representation_policy": representation_policy_block(),
        "attachment_tolerances": attachment_tolerances_block(),
        "jev_thresholds": jev_thresholds_block(),
        "jev_routine_resolution": jev_routine_resolution_block(),
        "street_fusion_policy": street_fusion_policy_block(),
        "street_template_families": street_template_families_block(),
        "fixture_archetype_templates": fixture_archetype_templates_block(),
        "inventory": inventory_block(),
        "roadside_lateral_band": roadside_lateral_band_block(),
        "object_metric_defaults": object_metric_defaults_block(),
        "spans": out_spans,
    }
    log_path = os.path.join(os.path.dirname(os.path.abspath(args.out)),
                            "rules_resolution_log.txt")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"# rules resolution log: {len(log_lines)} facts ({default_rows} subject rows + "
                f"3 span-level owner rows + 11 representation_policy rows + "
                f"2 attachment_tolerance rows + 3 jev_threshold rows + "
                f"4 jev_routine_resolution rows + "
                f"2 street_fusion rows + "
                f"1 roadside_lateral_band row + "
                f"{len(STREET_TEMPLATE_FAMILIES)} street_template rows + 7 fixture_archetype rows + "
                f"2 road_class height rows)\n")
        for line in log_lines:
            f.write(line + "\n")
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"spans=13 subjects=104 cited={cited_rows} default={default_rows} "
          f"facts_logged={len(log_lines)}")
    print(f"wrote {args.out}")
    print(f"wrote {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
