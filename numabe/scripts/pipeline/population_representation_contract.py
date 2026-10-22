#!/usr/bin/env python3
"""Single lowering-owner and physical-envelope contract for population modules.

This module is the boundary between representation planning and population
placement.  Catalog taxonomy describes what a module *is*; it does not select
which serializer happens to emit it.  The lowering owner and the asset
envelope are therefore declared here once and consumed before lowering.

Lowerers may realize this contract.  They may not reclassify modules, invent
longitudinal overhang, or silently redirect a placement to another lane.
"""

from __future__ import annotations

import math


LINEAR_DOMAINS = frozenset({
    "road_cross_section",
    "property_frontage",
    "rail_edge",
    "utility_network",
    "vegetation",
})
FIXTURE_DOMAINS = frozenset({
    "street_fixture",
    "generic_storefront",
    "facade_archetype",
})

# FL-4876.  Every logical-realization kind this module can emit, in one place.
# Two functions produce them: finalize_linear_realizations() covers isolated
# placements (run is None) and emits point/unit/hardware/span, while
# logical_realizations() covers placements that carry a run and emits
# point/segment.  The lowerer that authors geometry must cover exactly this
# set.  It previously enumerated only the first function's kinds, so every
# "segment" reached asset authoring as an unknown kind and stopped the build.
# A lowerer imports this set and fails at import when it drifts; it does not
# keep a second copy of the vocabulary.
LINEAR_REALIZATION_KINDS = frozenset({
    "point",
    "unit",
    "segment",
    "hardware",
    "span",
})

# A "segment" is one continuous run realized at its measured run length.  A
# "unit" is the same module's same cross-section realized at its fixed
# isolated-module extent because there is no run to measure.  They differ in
# where the length comes from, not in what the asset is.
LINEAR_RUN_LENGTH_KINDS = frozenset({"unit", "segment"})

# Shared representation selection for point fixtures.  Geometry authoring and
# placement import this one table; a lowerer does not maintain a second asset
# vocabulary.
FIXTURE_ARCHETYPE_BY_MODULE = {
    "horizontal_traffic_signal": "traffic_signal",
    "red_mailbox": "post_box",
    "wall_mounted_mailbox": "post_box",
    "parked_bicycles": "bicycle_parking",
    "vending_white_red_blue": "vending_machine",
    "notice_sign": "traffic_sign",
    "pole_poster_notice_carrier": "traffic_sign",
    "directional_road_sign": "traffic_sign",
    "pedestrian_warning_sign": "traffic_sign",
    "regulatory_sign_family": "traffic_sign",
    "street_name_sign": "traffic_sign",
    "street_sign_carrier": "traffic_sign",
    "traffic_sign_pole_cluster": "traffic_sign",
    "pole_notice_banner": "advertising_sign",
    "sidewalk_signboard": "advertising_sign",
    "bollard_curb_island": "bollard",
    "roadside_reflector_bollard": "bollard",
    "stone_marker_bollard": "bollard",
}

FIXTURE_CUSTOM_EXTENT_M = {
    "traffic_mirror": (0.6, 0.2, 2.3),
    "convex_mirror": (0.6, 0.2, 2.3),
    "hazard_strip_barrier": (4.2, 0.18, 1.15),
    "covered_waste_storage_enclosure": (1.25, 0.65, 1.05),
    "waste_collection_bin_pair": (1.2, 0.52, 0.8),
    "decorative_waterwheel": (1.4, 0.32, 1.5),
    "exterior_ac_condenser": (0.82, 0.34, 0.62),
    "residential_carport_canopy": (4.8, 3.6, 2.35),
}

# These modules are semantically street fixtures in the catalog but their
# selected Classic representation is a repeated linear assembly.  This is a
# representation fact, not a lowerer-side exception.
LINEAR_REPRESENTATION_MODULES = frozenset({
    "pedestrian_guardrail_continuous",
    "pedestrian_guardrail_vertical_bar_continuous",
    "bollard_curb_island",
})

LINEAR_POINT_MODULES = frozenset({
    "manhole_and_grate",
    "meter_and_service_cabinet",
    "pole_streetlight",
    "transformer_drum_cluster",
    "potted_frontage_plant",
    "small_tree",
    "street_tree",
    "level_crossing_equipment",
    "water_control_structure",
})

LINEAR_SPAN_MODULES = frozenset({
    "wire_span",
    "utility_pole_wire_cluster",
    "overhead_gantry_wire",
})

# FL-4876.  linear_kind() answers for an *isolated* placement, so it calls
# bollard_curb_island a "unit".  A bollard that carries a run is not a unit:
# logical_realizations() expands it into one "point" per bollard_spacing_m.
# It is therefore the one module that produces point realizations without
# being a LINEAR_POINT_MODULE, and the point geometry builder covered only
# that frozenset -- so all 3869 run-expanded bollards on this lane, 99 pct of
# every point realization, had no builder.  The builder covers this union.
LINEAR_RUN_POINT_MODULES = frozenset({"bollard_curb_island"})
POINT_REALIZABLE_MODULES = LINEAR_POINT_MODULES | LINEAR_RUN_POINT_MODULES

# These are representation-envelope facts for isolated authored modules, not
# placement-spacing rules. Modules absent from this table fail dispatch;
# population topology is never an asset-dimension authority.
LINEAR_FIXED_EXTENT_M = {
    "concrete_block_wall": 4.0,
    "curb_and_open_gutter": 4.0,
    "curb_edge_line": 6.0,
    "zebra_crossing": 3.0,
    "crosswalk_and_edge_guardrail": 4.0,
    "green_cycle_or_walk_band": 4.0,
    "ivy_wall": 4.0,
    "road_edge_dashed_marking": 3.0,
    "road_surface_text_marking": 4.0,
    "road_text_arrow_30": 4.0,
    "white_edge_line_shoulder": 6.0,
    "concrete_retaining_edge": 4.0,
    "metal_fence": 4.0,
    "metal_boundary_fence": 4.0,
    "lattice_boundary_fence": 4.0,
    "mesh_fence": 4.0,
    "picket_fence": 4.0,
    "open_and_covered_drain": 4.0,
    "platform_railing": 4.0,
    "rail_corridor_fence": 4.0,
    "rail_corridor_mesh_fence": 4.0,
    "rail_track_corridor": 4.0,
    "sidewalk_paver_surface": 4.0,
    "stone_cobble_wall": 4.0,
    "stone_lined_water_channel": 4.0,
    "trimmed_hedge": 4.0,
    "waterway_bridge_railing": 4.0,
    "white_railguard": 4.0,
    "pedestrian_guardrail_continuous": 4.0,
    "pedestrian_guardrail_vertical_bar_continuous": 4.0,
    # MANUAL-FIXTURE-02: an isolated hand-authored kerb bollard is one squat
    # concrete block (photos SF-4876-0076/0192/0285), not a run.
    "bollard_curb_island": 0.4,
}

LINEAR_SPAN_ASSET_CONTRACT = {
    "wire_span": {"hardware": None, "nominal_height_m": 6.0},
    "utility_pole_wire_cluster": {
        "hardware": "utility_pole",
        "hardware_height_m": 10.0,
        "nominal_height_m": 8.6,
    },
    "overhead_gantry_wire": {
        "hardware": "gantry_portal",
        "hardware_height_m": 5.5,
        "nominal_height_m": 5.4,
    },
}

LINEAR_SPAN_ENDPOINT_EXTENT_M = {
    "utility_pole_wire_cluster": (0.3, 1.8, 10.0),
    "overhead_gantry_wire": (0.24, 5.4, 5.6),
}


def finalize_wire_span_dispositions(carriers: list[dict]) -> dict[str, int]:
    """Terminate standalone wire sightings that cannot define a span.

    ``wire_span`` observations prove that overhead wire is present; a single
    sighting does not prove two endpoints.  Where the same road/rail carrier
    already has a pole network, that network owns the emitted wire spans and
    the sighting is retained as confirming evidence.  Otherwise the sighting
    remains an explicit non-emitting terminal instead of becoming an invented
    chord or reaching lowering as an invalid one-endpoint span.

    Two or more wire endpoints are usable only on road/rail carriers.  A
    building ring is not a utility-network carrier even when registration put
    two image observations near that footprint.
    """
    counts = {
        "consumed_by_pole_network": 0,
        "insufficient_endpoint_evidence": 0,
    }
    for carrier in carriers:
        placements = carrier.get("placements") or []
        wires = [
            row for row in placements
            if row.get("module_id") == "wire_span"
            and not isinstance(row.get("population_disposition"), dict)
        ]
        if not wires:
            continue
        carrier_class = str(carrier.get("carrier_class") or "")
        is_network_carrier = (
            carrier_class.startswith("road")
            or carrier_class.startswith("railway")
        )
        poles = [
            row for row in placements
            if row.get("module_id") == "utility_pole_wire_cluster"
            and not isinstance(row.get("population_disposition"), dict)
        ]
        if is_network_carrier and len(wires) >= 2:
            continue
        if is_network_carrier and len(poles) >= 2:
            category = "consumed_by_utility_pole_span_network"
            reason = (
                "overhead-wire sighting confirms this carrier's utility "
                "network; adjacent planned utility poles own the emitted "
                "wire spans and this row must not duplicate them"
            )
            count_key = "consumed_by_pole_network"
        else:
            category = "insufficient_wire_endpoint_evidence"
            reason = (
                "overhead-wire presence is retained as evidence, but this "
                "carrier does not provide two lawful utility-network "
                "endpoints; no chord is invented from a point sighting"
            )
            count_key = "insufficient_endpoint_evidence"
        for row in wires:
            row["population_disposition"] = {
                "state": "intentional_omission",
                "category": category,
                "reason": reason,
            }
            counts[count_key] += 1
    return counts

# Exact native bounds for point-kind linear assets, in the local
# (longitudinal, lateral, vertical) frame used by lowering.  These values are
# representation facts: population placement needs the selected asset's
# footprint before it can approve an attachment.
LINEAR_POINT_EXTENT_M = {
    "manhole_and_grate": (0.72, 0.72, 0.035),
    "meter_and_service_cabinet": (1.0, 0.64, 1.5),
    "pole_streetlight": (0.75, 0.4, 5.0),
    "transformer_drum_cluster": (1.6, 0.7, 8.0),
    "potted_frontage_plant": (0.56, 0.56, 0.8),
    "small_tree": (1.7, 1.7, 3.55),
    "street_tree": (1.7, 1.7, 3.55),
    # The crossing boom extends from the post across the resolved
    # carriageway.  Its longitudinal size is filled from the resolved rule
    # contract below rather than duplicated here.
    "level_crossing_equipment": (0.0, 0.5, 3.5),
    "water_control_structure": (1.0, 1.0, 1.1),
}

CUSTOM_CLASS_BANDS = {
    "vending": ("vending_unit", 0.9, 1.4, 1.6, 2.1),
    "figure": ("figure_statue", 0.4, 1.0, 0.6, 1.8),
    "poster": ("poster_notice", 0.4, 1.5, 0.5, 1.8),
    "nobori": ("nobori_flag", 0.5, 0.9, 1.5, 2.0),
    "sign": ("sign_cluster", 1.0, 12.0, 0.4, 3.0),
    "storefront": ("storefront_frontage", 2.0, 12.0, 2.4, 6.0),
    "temple": ("temple_cultural", 0.6, 12.0, 0.4, 6.0),
}

CUSTOM_FACE_TOKENS = frozenset({
    "storefront", "frontage", "shopfront", "awning", "entry", "shutter",
    "independent_shop", "signband",
    "mikasa", "respa", "miyoshiya", "familymart", "cocokara", "mufg",
    "restaurant", "produce", "market", "branch_frontage",
    # FL-4876 SIGN-MOUNT-01: the capture review describes these subjects as
    # attached / facade-mounted / projecting shop signs, wall posters and
    # frontage scaffold (observation_module_mapping carrier_type and
    # evidence_summary). Without them 26 shop signs fell through to
    # freestanding_fixture and were placed by road chainage, inside the
    # carriageway (obs-inst-0519 at 1.4 m from a road:primary centreline).
    "shop_sign", "shop_sig", "storefront_sign", "sign_cluster", "red_sign",
    "election_poster", "scaffold",
})


def custom_mount_kind(subject: str) -> str:
    """Resolve whether custom evidence belongs on a facade or in open space.

    A custom identity is not automatically a facade.  The former blanket rule
    snapped poles, local sign clusters, statues, and other freestanding objects
    to whichever building happened to be nearest.
    """
    value = str(subject or "").lower()
    if any(token in value for token in CUSTOM_FACE_TOKENS):
        return "building_face_panel"
    return "freestanding_fixture"

# Requirement vocabulary is intentionally mapped to representation modules
# here, beside lowering ownership.  Coverage gates must count the exact logical
# modules selected by representation; they may not infer density from an A3D
# filename, a viewer label, or a lowerer receipt family.
STREETSCAPE_REQUIREMENT_MODULES = {
    "street_handrail": frozenset({
        "crosswalk_and_edge_guardrail",
        "pedestrian_guardrail_continuous",
        "pedestrian_guardrail_vertical_bar_continuous",
        "platform_railing",
        "waterway_bridge_railing",
        "white_railguard",
    }),
    "street_lamp": frozenset({"pole_streetlight"}),
    "tree": frozenset({"potted_frontage_plant", "small_tree", "street_tree"}),
    "utility_pole": frozenset({
        "transformer_drum_cluster", "utility_pole_wire_cluster",
    }),
}

# FL-4876. The same streetscape requirement, expressed as the archetype a
# world-template template_id names. A placement generated from a
# representation row carries its asset choice as a resolved archetype rather
# than a module id, because the plan chose a template and never chose a
# module. Counting only module ids would have left a route with 40 newly
# placed street lamps still reporting one candidate.
STREETSCAPE_REQUIREMENT_ARCHETYPES = {
    "street_lamp": frozenset({"street_lamp"}),
    "tree": frozenset({"tree"}),
    "utility_pole": frozenset({"utility_pole"}),
    "street_handrail": frozenset(),
}

INFRASTRUCTURE_REQUIREMENT_MODULES = {
    "railway_crossing_equipment_proposal": frozenset({
        "level_crossing_equipment",
    }),
    "rail_corridor_fence_proposal": frozenset({
        "metal_boundary_fence", "platform_railing", "rail_corridor_fence",
        "rail_corridor_mesh_fence",
    }),
    # These must be explicit components of the station semantic plan. A
    # facade/sign plane is not a platform or a pair of entrances.
    "station_entrance_assembly_proposal": frozenset(),
    "station_platform_proposal": frozenset(),
}


def _resolved_default(rules: dict, key: str) -> tuple[float, str]:
    try:
        fact = rules["road_class_defaults"][key]
        return float(fact["value"]), str(fact["source"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"resolved rules lack road_class_defaults.{key}") from exc


def fixture_asset_contract(module_id: str, placement: dict,
                           rules: dict) -> dict:
    """Resolve generated fixture dimensions before mechanical lowering."""
    storey_height_m, storey_source = _resolved_default(
        rules, "storey_height_m")
    mounting_height_m = float(placement.get("height_m") or 0.0)
    if mounting_height_m <= 0.0:
        mounting_height_m = storey_height_m
        mounting_source = storey_source
    else:
        mounting_source = "population_plan.height_m"
    dimensions = placement.get("dims_m") or {}
    contract = {
        "module_id": module_id,
        "mount_kind": (
            "building_face_panel"
            if (placement.get("attachment") or {}).get("anchor_kind")
            == "building_face"
            else "freestanding_fixture"
        ),
        "storefront_panel_height_m": storey_height_m,
        "storefront_panel_height_source": storey_source,
        "sign_mounting_height_m": mounting_height_m,
        "sign_mounting_height_source": mounting_source,
        "sign_plate_height_m": 1.2,
        "selection_basis": "resolved_population_and_rules",
    }
    if dimensions.get("width_m") is not None:
        contract["planned_width_m"] = float(dimensions["width_m"])
    if dimensions.get("height_m") is not None:
        contract["planned_height_m"] = float(dimensions["height_m"])
    return contract


def linear_asset_contract(carrier: dict, rules: dict) -> dict:
    """Resolve the rule-owned cross-section consumed by asset realization."""
    span_index = carrier.get("span_index")
    spans = rules.get("spans") or []
    if span_index is None:
        span_index = 0
        span_basis = "default_span_for_non_route_carrier"
    else:
        span_index = int(span_index)
        span_basis = "carrier.span_index"
    try:
        subjects = spans[span_index]["subjects"]
        road = subjects["road_structure"]["parameters"]
        access = subjects["accessibility"]["parameters"]
        rail = subjects["railway_boundary"]["parameters"]
        values = {
            "curb_height_m": float(access["curb_height_cm"]["value"]) / 100.0,
            "carriageway_width_m": float(
                road["carriageway_width_m"]["value"]),
            "sidewalk_width_m": float(road["sidewalk_width_m"]["value"]),
            "tactile_paving_width_m": float(
                access["tactile_paving_width_m"]["value"]),
            "drainage_gutter_width_m": float(
                road["drainage_gutter_width_m"]["value"]),
            "rail_boundary_fence_height_m": float(
                rail["rail_boundary_fence_height_m"]["value"]),
        }
        sources = {
            "curb_height_m": access["curb_height_cm"]["source"],
            "carriageway_width_m": road["carriageway_width_m"]["source"],
            "sidewalk_width_m": road["sidewalk_width_m"]["source"],
            "tactile_paving_width_m": access[
                "tactile_paving_width_m"]["source"],
            "drainage_gutter_width_m": road[
                "drainage_gutter_width_m"]["source"],
            "rail_boundary_fence_height_m": rail[
                "rail_boundary_fence_height_m"]["source"],
        }
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"resolved rules lack cross-section facts for span {span_index}") \
            from exc
    return {
        "span_index": span_index,
        "span_selection_basis": span_basis,
        "cross_section_m": values,
        "sources": sources,
    }


def custom_plane_contract(subject: str) -> dict:
    """Choose the custom-plane semantic/metric contract before lowering."""
    value = str(subject or "").lower()
    if any(token in value for token in ("vending", "vendor", "machine")):
        key = "vending"
    elif any(token in value for token in ("statue", "figure")):
        key = "figure"
    elif any(token in value for token in ("poster", "notice")):
        key = "poster"
    elif any(token in value for token in ("nobori", "flag", "banner")):
        key = "nobori"
    elif any(token in value for token in (
            "storefront", "frontage", "shop", "store", "awning", "entry",
            "shutter", "mikasa", "respa", "miyoshiya", "familymart",
            "cocokara", "mufg", "restaurant", "crates", "display",
            "produce", "market")):
        key = "storefront"
    elif any(token in value for token in (
            "temple", "cultural", "hall", "roofline", "shrine", "pergola",
            "water_edge", "boundary", "gate")):
        key = "temple"
    else:
        key = "sign"
    family, min_w, max_w, min_h, max_h = CUSTOM_CLASS_BANDS[key]
    is_object = any(token in value for token in (
        "scaffold", "vending", "statue", "figure", "crate", "display",
        "construction", "waterwheel", "pergola"))
    return {
        "semantic_family": family,
        "mount_kind": custom_mount_kind(subject),
        "width_m": {"min": min_w, "max": max_w},
        "height_m": {"min": min_h, "max": max_h},
        "depth_m": 0.3 if is_object else 0.08,
        "selection_basis": "custom_identity_subject",
    }


def lowering_owner(module_id: str, catalog_row: dict) -> tuple[str, str]:
    """Return the sole mechanical owner selected by representation."""
    if module_id in LINEAR_REPRESENTATION_MODULES:
        return "linear", f"representation_contract:{module_id}"
    domain = str(catalog_row.get("owning_domain") or "")
    if domain in LINEAR_DOMAINS:
        return "linear", f"catalog_domain:{domain}"
    if domain in FIXTURE_DOMAINS:
        return "fixtures", f"catalog_domain:{domain}"
    raise ValueError(
        f"module {module_id!r} has no lowering owner for catalog domain "
        f"{domain!r}"
    )


def linear_kind(module_id: str) -> str:
    """Return the realization kind owned by the representation contract."""
    if module_id in LINEAR_POINT_MODULES:
        return "point"
    if module_id in LINEAR_SPAN_MODULES:
        return "span"
    return "unit"


def linear_module_ids(catalog_rows: list[dict], *, include_points: bool) -> set[str]:
    """Return catalog modules assigned to the linear serializer.

    This is deliberately derived from the same owner function used by the
    dispatcher.  Planning and lowering must never carry parallel module tables.
    """
    module_ids: set[str] = set()
    for row in catalog_rows:
        module_id = str(row.get("module_id") or "")
        if not module_id:
            raise ValueError("module catalog row has no module_id")
        owner, _basis = lowering_owner(module_id, row)
        if owner == "linear" and (include_points or linear_kind(module_id) != "point"):
            module_ids.add(module_id)
    return module_ids


def _span_building_conflicts(e0: float, n0: float, e1: float, n1: float,
                             wire_min_m: float, buildings) -> list[str]:
    """Building footprints a straight pole-to-pole wire would pass through.

    FL-4876 COHERENCE-ZERO-01. A distribution wire runs between adjacent
    poles along a street; it never passes through a building. A chord that
    crosses a footprint is clear only when that building's measured height
    is below the wire's minimum height (the spatial coherence rule
    _span_vertical_conflicts); an unknown height is a conflict.
    """
    if not buildings:
        return []
    from shapely.geometry import LineString
    chord = LineString([(e0, n0), (e1, n1)])
    out = []
    for fid, ring, height in buildings:
        if ring.bounds[0] > max(e0, e1) or ring.bounds[2] < min(e0, e1) \
                or ring.bounds[1] > max(n0, n1) or ring.bounds[3] < min(n0, n1):
            continue
        if chord.crosses(ring) or chord.within(ring):
            if height is None or float(height) >= wire_min_m:
                out.append(fid)
    return out


def span_building_index(features) -> list:
    """(feature_id, footprint polygon, measured height or None) per canonical building."""
    from shapely.geometry import Polygon
    out = []
    for feature in features:
        geometry = feature.get("geometry") or {}
        if (feature.get("semantic_class") != "building_footprint"
                or geometry.get("frame_id") != "epsg32654"):
            continue
        coords = [q[:2] for q in geometry.get("coordinates") or []]
        if len(coords) < 3:
            continue
        ring = Polygon(coords)
        if not ring.is_valid or ring.area <= 0.0:
            continue
        claim = (feature.get("properties") or {}).get("height_m")
        value = claim.get("value") if isinstance(claim, dict) else claim
        out.append((feature["feature_id"], ring,
                    float(value) if isinstance(value, (int, float)) else None))
    return out


def finalize_linear_realizations(carriers: list[dict], rules: dict,
                                 buildings: list | None = None) -> None:
    """Create every non-run logical instance before linear lowering."""
    unit_extent: dict[str, tuple[float, str]] = {}
    unit_modules = {
        str(placement.get("module_id") or "")
        for carrier in carriers
        for placement in carrier.get("placements", [])
        if placement.get("run") is None
        and linear_kind(str(placement.get("module_id") or "")) == "unit"
    }
    for module_id in unit_modules:
        if module_id not in LINEAR_FIXED_EXTENT_M:
            raise ValueError(
                f"linear module {module_id!r} has no explicit asset extent "
                "in the representation contract")
        unit_extent[module_id] = (
            float(LINEAR_FIXED_EXTENT_M[module_id]),
            "representation_contract:isolated_module_extent",
        )

    for carrier in carriers:
        span_groups: dict[str, list[dict]] = {}
        for placement in carrier.get("placements", []):
            if placement.get("run") is not None:
                continue
            module_id = str(placement.get("module_id") or "")
            kind = linear_kind(module_id)
            placement["logical_realizations"] = []
            if kind == "point":
                placement["logical_realizations"].append({
                    "logical_instance_id": placement["placement_id"] + ":point",
                    "kind": "point",
                    "pose": dict(placement["resolved_pose"]),
                    "source_placement_ids": [placement["placement_id"]],
                })
            elif kind == "unit":
                length_m, basis = unit_extent[module_id]
                placement["logical_realizations"].append({
                    "logical_instance_id": placement["placement_id"] + ":unit",
                    "kind": "unit",
                    "pose": dict(placement["resolved_pose"]),
                    "length_m": length_m,
                    "length_basis": basis,
                    "source_placement_ids": [placement["placement_id"]],
                    "physical_envelope": {
                        "longitudinal_extent_m": length_m,
                        "max_longitudinal_overhang_m": 0.0,
                        "fit_policy": "centred_on_resolved_pose",
                    },
                })
            else:
                span_groups.setdefault(module_id, []).append(placement)
                contract = LINEAR_SPAN_ASSET_CONTRACT[module_id]
                if contract["hardware"] is not None:
                    placement["logical_realizations"].append({
                        "logical_instance_id": placement["placement_id"]
                        + ":hardware",
                        "kind": "hardware",
                        "hardware": contract["hardware"],
                        "height_m": contract["hardware_height_m"],
                        "pose": dict(placement["resolved_pose"]),
                        "source_placement_ids": [placement["placement_id"]],
                    })

        for module_id, rows in span_groups.items():
            contract = LINEAR_SPAN_ASSET_CONTRACT[module_id]
            if len(rows) < 2:
                if contract["hardware"] is None:
                    raise ValueError(
                        f"span module {module_id!r} on carrier "
                        f"{carrier.get('carrier_id')!r} has fewer than two "
                        "endpoints")
                continue

            def chainage(row: dict) -> float:
                attachment = row.get("attachment") or {}
                for key in ("chainage_m", "start_chainage_m"):
                    if attachment.get(key) is not None:
                        return float(attachment[key])
                raise ValueError(
                    f"span endpoint {row.get('placement_id')} has no canonical "
                    "carrier chainage")

            # FL-4876 COHERENCE-ZERO-01: a freestanding endpoint (a pole the
            # planner could not express as carrier chainage, e.g. beyond the
            # carrier's end) has no position in the chain; it stays an
            # isolated pole with its own hardware and is not wired.
            def _has_chainage(row: dict) -> bool:
                attachment = row.get("attachment") or {}
                return any(attachment.get(key) is not None
                           for key in ("chainage_m", "start_chainage_m"))

            ordered = sorted((row for row in rows if _has_chainage(row)), key=chainage)
            for first, second in zip(ordered, ordered[1:]):
                first_pose = first["resolved_pose"]
                second_pose = second["resolved_pose"]
                e0, n0 = float(first_pose["E"]), float(first_pose["N"])
                e1, n1 = float(second_pose["E"]), float(second_pose["N"])
                length_m = ((e1 - e0) ** 2 + (n1 - n0) ** 2) ** 0.5
                if length_m <= 1e-9:
                    raise ValueError(
                        f"span endpoints coincide: {first['placement_id']} and "
                        f"{second['placement_id']}")
                nominal = float(contract["nominal_height_m"])
                logical_id = (first["placement_id"] + "__"
                              + second["placement_id"] + ":span")
                blocked = _span_building_conflicts(
                    e0, n0, e1, n1, nominal - 0.02 * length_m - 0.025, buildings)
                if blocked:
                    # Adjacent by chainage, but the straight wire would run
                    # through a building: these two poles are not wired to
                    # each other. Both poles are still emitted.
                    second.setdefault("span_omissions", []).append({
                        "logical_instance_id": logical_id,
                        "reason": "pole_to_pole_chord_through_building_footprint",
                        "buildings": blocked,
                        "length_m": round(length_m, 3),
                    })
                    continue
                second["logical_realizations"].append({
                    "logical_instance_id": logical_id,
                    "kind": "span",
                    "source_placement_ids": [first["placement_id"],
                                             second["placement_id"]],
                    "start": {"E": e0, "N": n0},
                    "end": {"E": e1, "N": n1},
                    "pose": {"E": (e0 + e1) / 2.0,
                             "N": (n0 + n1) / 2.0},
                    "length_m": length_m,
                    "yaw_deg": math.degrees(math.atan2(n1 - n0, e1 - e0)),
                    "nominal_height_m": nominal,
                    "minimum_height_m": nominal - 0.02 * length_m - 0.025,
                    "topology_basis": "canonical_carrier_chainage_adjacency",
                    "physical_envelope": {
                        "longitudinal_extent_m": length_m,
                        "max_longitudinal_overhang_m": 0.0,
                        "fit_policy": "between_resolved_endpoints",
                    },
                })


def physical_envelope(module_id: str, variant: str | None,
                      placement: dict, rules: dict) -> dict:
    """Declare the placement-space envelope the realized asset must obey.

    Runs own a closed longitudinal interval.  Asset detail, including terminal
    posts, must fit inside it.  Point objects own their declared anchor and do
    not get a fabricated run interval.
    """
    run = placement.get("run")
    if isinstance(run, dict):
        pose = placement.get("resolved_pose") or {}
        if all(pose.get(key) is not None for key in ("E0", "N0", "E1", "N1")):
            length_m = math.hypot(
                float(pose["E1"]) - float(pose["E0"]),
                float(pose["N1"]) - float(pose["N0"]))
            length_basis = "resolved_attachment_span"
        else:
            length_m = float(run.get("length_m") or 0.0)
            length_basis = "population_run_length"
        if length_m <= 0.0:
            raise ValueError(
                f"run placement {placement.get('placement_id')} has a "
                f"non-positive length {length_m}"
            )
        contract = {
            "anchor": "run_midpoint",
            "longitudinal_extent_m": length_m,
            "max_longitudinal_overhang_m": 0.0,
            "fit_policy": "inside_logical_run",
            "length_basis": length_basis,
        }
        height_m = float(placement.get("height_m") or 0.0)
        if height_m <= 0.0:
            height_m, _source = _resolved_default(rules, "storey_height_m")
        contract["vertical_extent_m"] = height_m
        if (module_id == "pedestrian_guardrail_continuous"
                and variant == "white_tube_short_terminal"):
            contract["terminal_detail_length_m"] = 0.18
            if length_m < 0.18:
                raise ValueError(
                    f"guardrail terminal {placement.get('placement_id')} is "
                    f"shorter than its 0.18 m physical detail"
                )
        return contract
    contract = {
        "anchor": "placement_anchor",
        "max_longitudinal_overhang_m": 0.0,
        "fit_policy": "declared_asset_bounds",
    }
    if module_id in LINEAR_POINT_EXTENT_M:
        longitudinal, lateral, vertical = LINEAR_POINT_EXTENT_M[module_id]
        if module_id == "level_crossing_equipment":
            try:
                longitudinal = max(
                    float(span["subjects"]["road_structure"]["parameters"]
                          ["carriageway_width_m"]["value"])
                    for span in rules["spans"])
                # Boom starts 0.1 m beyond the post centre and its post/base
                # reaches 0.25 m in the opposite direction.
                longitudinal += 0.35
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    "resolved rules lack carriageway width for the level "
                    "crossing asset envelope") from exc
        contract.update({
            "longitudinal_extent_m": longitudinal,
            "lateral_extent_m": lateral,
            "vertical_extent_m": vertical,
            "extent_basis": "population_representation_contract",
        })
    elif module_id in LINEAR_SPAN_ENDPOINT_EXTENT_M:
        longitudinal, lateral, vertical = \
            LINEAR_SPAN_ENDPOINT_EXTENT_M[module_id]
        contract.update({
            "longitudinal_extent_m": longitudinal,
            "lateral_extent_m": lateral,
            "vertical_extent_m": vertical,
            "extent_basis": "population_representation_contract",
        })
    elif module_id in LINEAR_FIXED_EXTENT_M:
        dimensions = placement.get("dims_m") or {}
        contract.update({
            "longitudinal_extent_m": LINEAR_FIXED_EXTENT_M[module_id],
            "lateral_extent_m": float(dimensions.get("width_m") or 0.5),
            "vertical_extent_m": float(dimensions.get("height_m") or 1.5),
            "extent_basis": "population_representation_contract",
        })
    elif module_id in FIXTURE_ARCHETYPE_BY_MODULE:
        from scripts.pipeline.classic_procedural_mesh import ARCHETYPE_PHYSICAL
        subtype = FIXTURE_ARCHETYPE_BY_MODULE[module_id]
        _family, width, depth, height, _scale_min, _scale_max = \
            ARCHETYPE_PHYSICAL[subtype]
        contract.update({
            "longitudinal_extent_m": float(width),
            "lateral_extent_m": float(depth),
            "vertical_extent_m": float(height),
            "extent_basis": f"ARCHETYPE_PHYSICAL:{subtype}",
        })
    elif module_id in FIXTURE_CUSTOM_EXTENT_M:
        longitudinal, lateral, vertical = FIXTURE_CUSTOM_EXTENT_M[module_id]
        contract.update({
            "longitudinal_extent_m": longitudinal,
            "lateral_extent_m": lateral,
            "vertical_extent_m": vertical,
            "extent_basis": "population_representation_contract",
        })
    else:
        dimensions = placement.get("dims_m") or {}
        if dimensions.get("width_m") is not None:
            contract["longitudinal_extent_m"] = float(
                dimensions["width_m"])
        if dimensions.get("depth_m") is not None:
            contract["lateral_extent_m"] = float(dimensions["depth_m"])
        if dimensions.get("height_m") is not None:
            contract["vertical_extent_m"] = float(
                dimensions["height_m"])
        if len(contract) > 3:
            contract["extent_basis"] = "population_plan.dims_m"
    return contract


def logical_realizations(module_id: str, placement: dict,
                         rules: dict) -> list[dict]:
    """Expand one logical run into exact pre-lowering realization records."""
    run = placement.get("run")
    if not isinstance(run, dict):
        return []
    envelope = placement["asset_physical_envelope"]
    length_m = float(envelope["longitudinal_extent_m"])
    placement_id = str(placement["placement_id"])
    pose = placement["resolved_pose"]
    if all(pose.get(key) is not None for key in ("E0", "N0", "E1", "N1")):
        e0, n0 = float(pose["E0"]), float(pose["N0"])
        e1, n1 = float(pose["E1"]), float(pose["N1"])
        yaw_deg = math.degrees(math.atan2(n1 - n0, e1 - e0))
    else:
        midpoint_e, midpoint_n = float(pose["E"]), float(pose["N"])
        yaw_deg = float(pose.get("yaw_deg", 0.0))
        yaw = math.radians(yaw_deg)
        e0 = midpoint_e - math.cos(yaw) * length_m / 2.0
        n0 = midpoint_n - math.sin(yaw) * length_m / 2.0
        e1 = midpoint_e + math.cos(yaw) * length_m / 2.0
        n1 = midpoint_n + math.sin(yaw) * length_m / 2.0
    dx, dy = (e1 - e0) / length_m, (n1 - n0) / length_m
    if module_id == "bollard_curb_island":
        try:
            spacing_m = float(
                rules["road_class_defaults"]["bollard_spacing_m"]["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "resolved rules lack bollard_spacing_m for population "
                "realization planning"
            ) from exc
        count = max(1, int(round(length_m / spacing_m)))
        step_m = length_m / count
        return [
            {
                "logical_instance_id": f"{placement_id}:point:{index:04d}",
                "kind": "point",
                "pose": {
                    "E": e0 + dx * (index + 0.5) * step_m,
                    "N": n0 + dy * (index + 0.5) * step_m,
                    "yaw_deg": yaw_deg,
                },
                "source_placement_ids": [placement_id],
                "physical_envelope": {
                    "longitudinal_extent_m": 0.26,
                    "max_longitudinal_overhang_m": 0.0,
                    "fit_policy": "inside_logical_run",
                },
                "spacing_basis_m": spacing_m,
            }
            for index in range(count)
        ]
    return [{
        "logical_instance_id": f"{placement_id}:segment:0000",
        "kind": "segment",
        "pose": {"E": (e0 + e1) / 2.0, "N": (n0 + n1) / 2.0,
                 "yaw_deg": yaw_deg},
        "source_placement_ids": [placement_id],
        "length_m": length_m,
        "physical_envelope": envelope,
    }]
