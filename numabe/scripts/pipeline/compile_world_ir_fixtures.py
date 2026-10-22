#!/usr/bin/env python3
"""Lower OSM-backed World IR fixtures into Classic presentation instances."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from scripts.pipeline.classic_procedural_mesh import (
    _add_block_text,
    _add_box,
    _block_text_boxes,
    _build_archetype,
)
from scripts.pipeline.compile_facade_shell import HEIGHT_SCALE, _load_a3d_edit, write_akm
from scripts.pipeline.compile_rules_resolved import ROAD_CLASS_DEFAULTS
from scripts.pipeline.probe_instance_terrain_z import load_height_grid, terrain_z
from scripts.pipeline.template_resolver import (
    discover_pack_chain,
    resolve_data_profile,
    resolve_template_candidates,
)
from scripts.pipeline.world_ir import WorldIRStore


# FL-4876 SIGN-WALL-01: largest inward move of a place front onto its extruded
# wall; spatial coherence holds a front within 1.5 m of its canonical face.
FRONT_WALL_SHIFT_MAX_M = 1.45


FIXTURE_SUBTYPES = {
    ("highway", "traffic_signals"): "traffic_signal",
    ("amenity", "post_box"): "post_box",
    ("amenity", "telephone"): "public_telephone",
    ("amenity", "bicycle_parking"): "bicycle_parking",
    ("amenity", "bench"): "bench",
    ("amenity", "vending_machine"): "vending_machine",
    ("amenity", "waste_basket"): "waste_basket",
    ("amenity", "drinking_water"): "drinking_water",
    ("amenity", "shelter"): "shelter",
    ("amenity", "clock"): "clock",
    ("amenity", "fountain"): "fountain",
    ("highway", "street_lamp"): "street_lamp",
    ("highway", "bus_stop"): "bus_stop",
    ("highway", "stop"): "stop_sign",
    ("power", "pole"): "utility_pole",
    ("barrier", "gate"): "pedestrian_gate",
    ("barrier", "lift_gate"): "lift_gate",
    ("barrier", "swing_gate"): "pedestrian_gate",
    ("barrier", "cycle_barrier"): "cycle_barrier",
    ("barrier", "bollard"): "bollard",
    ("barrier", "block"): "bollard",
    ("barrier", "kissing_gate"): "pedestrian_gate",
    ("barrier", "turnstile"): "turnstile",
    ("railway", "signal"): "railway_signal",
    ("railway", "switch"): "railway_switch",
    ("railway", "buffer_stop"): "railway_buffer_stop",
    ("railway", "derail"): "railway_derailer",
    ("man_made", "flagpole"): "flagpole",
    ("man_made", "street_cabinet"): "street_cabinet",
    ("emergency", "fire_hydrant"): "fire_hydrant",
    ("natural", "tree"): "tree",
    ("railway", "station"): "station_stop",
    ("railway", "stop"): "station_stop",
    ("railway", "level_crossing"): "railway_crossing_marker",
    ("railway", "crossing"): "railway_crossing_marker",
    ("railway", "train_station_entrance"): "station_entrance_gate",
    ("historic", "memorial"): "information_board",
}




def _source_tags(feature: dict, observations: dict[str, dict]) -> dict:
    for observation_id in feature.get("supporting_observation_ids", []):
        observation = observations.get(observation_id) or {}
        tags = (observation.get("attributes") or {}).get("tags")
        if tags:
            return dict(tags)
    raise ValueError(f"{feature.get('feature_id')} has no source tags")


def _plan_source_tags(feature: dict, observations: dict[str, dict]) -> tuple[dict, str]:
    """Plan-path tag source (FL-4809 NUMABE-FIXTURE-01): OSM tags when present, else fused.

    Returns (tags, source_kind). Fused street features (Mapillary observations
    carrying object_value, no OSM tags) yield ({}, "mapillary"); the plan row --
    not tags -- types and sites them. OSM-backed features yield (tags, "osm").
    Anything else raises like the legacy _source_tags (untouched).
    """
    for observation_id in feature.get("supporting_observation_ids", []):
        observation = observations.get(observation_id) or {}
        tags = (observation.get("attributes") or {}).get("tags")
        if tags:
            return dict(tags), "osm"
    for observation_id in feature.get("supporting_observation_ids", []):
        observation = observations.get(observation_id) or {}
        attrs = observation.get("attributes") or {}
        if attrs.get("object_value") or observation.get("source_id") == "mapillary":
            return {}, "mapillary"
    raise ValueError(f"{feature.get('feature_id')} has no source tags")


def _street_subtype_from_plan(row: dict, feature: dict) -> str | None:
    """Subtype for a fused tagless row from plan authority (NUMABE-FIXTURE-01).

    Only crossings resolve (railway_crossing_marker -> family template via
    ROUTED_SUBTYPE_MODULE); anything else returns None and the caller skips
    the row with new_asset_required listed.
    """
    if feature.get("semantic_class") == "crossing" or row.get("semantic_class") == "crossing":
        return "railway_crossing_marker"
    return None


def _nearest_polyline_frame(
    point: tuple[float, float],
    polylines: list[list[tuple[float, float]]],
) -> tuple[tuple[float, float], float, float] | None:
    """Return nearest point, segment yaw and distance in world units."""
    best = None
    px, py = point
    for polyline in polylines:
        for a, b in zip(polyline, polyline[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            denom = dx * dx + dy * dy
            if denom <= 1e-12:
                continue
            t = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / denom))
            nearest = (a[0] + t * dx, a[1] + t * dy)
            distance = math.dist(point, nearest)
            candidate = (nearest, math.atan2(dy, dx), distance)
            if best is None or candidate[2] < best[2]:
                best = candidate
    return best


def _cluster_crossing_features(
    crossings: list[tuple[dict, dict, tuple[float, float]]],
    *,
    radius_world: float,
) -> list[list[tuple[dict, dict, tuple[float, float]]]]:
    """Collapse per-track crossing nodes into physical crossing candidates."""
    groups: list[list[tuple[dict, dict, tuple[float, float]]]] = []
    for candidate in crossings:
        name = candidate[1].get("name")
        chosen = None
        for group in groups:
            group_names = {row[1].get("name") for row in group if row[1].get("name")}
            centre = (
                sum(row[2][0] for row in group) / len(group),
                sum(row[2][1] for row in group) / len(group),
            )
            if name and group_names and name not in group_names:
                continue
            if math.dist(candidate[2], centre) <= radius_world:
                chosen = group
                break
        if chosen is None:
            chosen = []
            groups.append(chosen)
        chosen.append(candidate)
    return groups


def _subtype(tags: dict) -> str:
    matches = {
        subtype for (key, value), subtype in FIXTURE_SUBTYPES.items()
        if tags.get(key) == value
    }
    if not matches and tags.get("tourism") == "information":
        matches.add("information_board")
    if not matches and tags.get("traffic_sign"):
        matches.add("traffic_sign")
    if not matches and tags.get("advertising"):
        matches.add("advertising_sign")
    if not matches and tags.get("highway") == "crossing":
        matches.add("pedestrian_crossing_marker")
    if len(matches) != 1:
        raise ValueError(f"fixture tags resolve to {sorted(matches)}: {tags}")
    return matches.pop()


def _centroid(geometry: dict) -> tuple[float, float]:
    kind = geometry.get("kind")
    if kind == "point":
        x, y = geometry["coordinates"][:2]
        return float(x), float(y)
    if kind == "polygon":
        points = list(geometry["coordinates"])
        if len(points) > 1 and points[0] == points[-1]:
            points.pop()
        if not points:
            raise ValueError("empty fixture polygon")
        return (
            sum(float(point[0]) for point in points) / len(points),
            sum(float(point[1]) for point in points) / len(points),
        )
    raise ValueError(f"unsupported fixture geometry: {kind}")

# FL-4809 NUMABE-REPRESENT-01: mesh-place representation.  A mesh-placed
# instance whose parent is a terrain-owned (baked) mass must clear the
# rasterised block: the mass footprint is painted into the terrain grid, so
# any part of the instance inside the parent footprint dilated by one cell is
# buried (painted over).  One terrain cell is one render cell: 1 world unit
# = 1.5 cells, so cell_world = 1/1.5 world units.
MESH_PLACE_CELLS_PER_WORLD = 1.5
# FL-4876 RAIL-TRACK-01: exact terminal-cube colours (write_akm snaps to 51
# steps) and a realistic concrete-sleeper pitch.
RAIL_BALLAST_RGB = (153, 153, 153)
RAIL_SLEEPER_RGB = (204, 204, 204)
RAIL_STEEL_RGB = (51, 51, 51)
RAIL_SLEEPER_PITCH_M = 0.6
MESH_PLACE_REPRESENTATION = "mesh-place"

# Routed-subtype to locale module: station signs, memorial boards, entrance
# gates and crossing markers reuse these existing locale modules (REPRESENT-02
# owner; shared by the IR-driven and plan-driven paths).
ROUTED_SUBTYPE_MODULE = {
    "station_stop": "traffic_sign",
    "railway_crossing_marker": "railway_signal",
    "station_entrance_gate": "pedestrian_gate",
}


def _point_to_ring_signed_distance(
    point: tuple[float, float], ring: list[tuple[float, float]],
) -> float:
    """Signed world-unit distance from point to a polygon ring."""
    from scripts.pipeline.bake_osm_building_footprints import point_in_polygon
    inside = point_in_polygon(point[0], point[1], ring)
    best = float("inf")
    closed = ring if ring[0] == ring[-1] else [*ring, ring[0]]
    for edge_a, edge_b in zip(closed, closed[1:]):
        dx, dy = edge_b[0] - edge_a[0], edge_b[1] - edge_a[1]
        denominator = dx * dx + dy * dy
        if denominator <= 1e-12:
            nearest = edge_a
        else:
            t = max(0.0, min(1.0, ((point[0] - edge_a[0]) * dx
                                   + (point[1] - edge_a[1]) * dy) / denominator))
            nearest = (edge_a[0] + t * dx, edge_a[1] + t * dy)
        best = min(best, math.dist(point, nearest))
    return -best if inside else best


def _cell_row_span(min_v: float, max_v: float, cell_world: float) -> int:
    """Number of cell rows intersected by a world-unit interval."""
    import math as _math
    return max(1, int(_math.floor(max_v / cell_world)
                      - _math.floor(min_v / cell_world)) + 1)


def annotate_mesh_place_instances(
    place_rows: list[dict[str, Any]],
    *,
    fixture_rows: list[dict[str, Any]] | tuple = (),
    facade_rows: list[dict[str, Any]] | tuple = (),
    parent_footprints: dict[str, list],
    parent_heights_m: dict[str, float] | None = None,
    raised_at=None,
) -> tuple[list[dict[str, Any]], int, list[dict[str, Any]]]:
    """Tag mesh-placed instances with burial clearance against baked masses.

    Each row needs feature_id, label, parent_id, world_polygon (world-unit
    [x, y] corners of the instance bbox), front_height_m, and optionally
    resolved_profile_id.  parent_footprints maps parent feature id to its
    world-unit mass ring.  fixture_rows and anchored-facade rows share the
    contract so later lanes reuse this function.  Returns (per_instance,
    buried_count, debt).  Rows are mutated in place with representation and
    clearance_cells.  clearance_cells is the signed distance of the instance
    bbox from the parent footprint dilated by one cell, in cells; negative
    means buried.  The distance is the minimum over the bbox corners and
    edge midpoints: corners alone miss edge-centred burial when the front
    overhangs a short parent edge laterally, while the rear-edge midpoint
    sits 0.03 world units outside the mass under the old 0.08 m offset.
    Debt holds fronts under 1/3 of their parent mass height.
    """
    cell_world = 1.0 / MESH_PLACE_CELLS_PER_WORLD
    per_instance: list[dict[str, Any]] = []
    debt: list[dict[str, Any]] = []
    buried = 0
    for row in [*place_rows, *fixture_rows, *facade_rows]:
        parent_id = row["parent_id"]
        ring = parent_footprints[parent_id]
        poly = [(corner[0], corner[1]) for corner in row["world_polygon"]]
        samples = list(poly)
        for (ax, ay), (bx, by) in zip(poly, poly[1:] + poly[:1]):
            samples.append(((ax + bx) / 2.0, (ay + by) / 2.0))
        if raised_at is not None:
            # FL-4876 SIGN-WALL-01: judged against the mass as baked. The
            # bake leaves the wall 0.8-1.8 m inside the canonical ring
            # (FL-4814), so the ring-dilated model called every front that
            # stands on the real wall buried. Clearance is 0 when no sample
            # lies on raised terrain, else minus the raised sample count.
            raised = sum(1 for point in samples if raised_at(point[0], point[1]))
            clearance_cells = -float(raised) if raised else 0.0
        else:
            signed_world = min(
                _point_to_ring_signed_distance(point, ring) for point in samples
            )
            clearance_cells = round(signed_world / cell_world - 1.0, 3)
        is_buried = clearance_cells < 0
        buried += 1 if is_buried else 0
        row["representation"] = MESH_PLACE_REPRESENTATION
        row["clearance_cells"] = clearance_cells
        xs = [corner[0] for corner in row["world_polygon"]]
        ys = [corner[1] for corner in row["world_polygon"]]
        front_rows = _cell_row_span(min(ys), max(ys), cell_world)
        parent_xs = [point[0] for point in ring]
        parent_ys = [point[1] for point in ring]
        mass_rows = _cell_row_span(min(parent_ys), max(parent_ys), cell_world)
        per_instance.append({
            "feature_id": row["feature_id"],
            "label": row["label"],
            "parent": parent_id,
            "clearance_cells": clearance_cells,
            "front_rows": front_rows,
            "mass_rows": mass_rows,
            "buried": is_buried,
        })
        parent_height = (parent_heights_m or {}).get(parent_id)
        if (parent_height and row.get("front_height_m") is not None
                and float(row["front_height_m"]) < float(parent_height) / 3.0):
            debt.append({
                "feature_id": row["feature_id"],
                "label": row["label"],
                "parent": parent_id,
                "front_height_m": round(float(row["front_height_m"]), 3),
                "parent_height_m": round(float(parent_height), 3),
                "resolved_profile_id": row.get("resolved_profile_id"),
            })
    per_instance.sort(key=lambda item: item["feature_id"])
    debt.sort(key=lambda item: item["feature_id"])
    return per_instance, buried, debt


def _compile_fixtures_from_plan(
    *, world_ir_path: Path, requirements_path: Path, template_root: Path,
    terrain_metadata_path: Path, map_path: Path, meshes_out: Path,
    instances_out: Path, receipt_out: Path,
    plan_rows: list[dict[str, Any]], canonical_doc: dict[str, Any],
    embed_map: bool = True,
) -> dict[str, Any]:
    """Build exactly the plan fixtures rows, sited by canonical attachment.

    FL-4809 NUMABE-REPRESENT-03: every instance pose comes from
    canonical_attachment.resolve(row["attachment"]) against the same
    canonical document the plan was built on. Rows whose attachment_state
    is not resolved are never built; they are echoed with the plan state.
    No centroid, road-frame, or nearest-segment siting lives on this path
    (removed lookups: _centroid site, _nearest_polyline_frame roadside
    search, REPRESENT-02 nearest-face/segment anchors).
    """
    from scripts.pipeline.canonical_attachment import CanonicalIndex, resolve
    from scripts.pipeline.classic_procedural_mesh import ARCHETYPE_PHYSICAL
    store = WorldIRStore.load(world_ir_path)
    store.assert_valid()
    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    context = requirements["locale_contexts"]["route_default"]
    packs = discover_pack_chain(template_root, context)
    declared_chain = context.get("template_pack_chain", [])
    actual_chain = [pack["pack_id"] for pack in packs]
    if actual_chain != declared_chain:
        raise ValueError(f"locale pack drift: {actual_chain} != {declared_chain}")
    metadata = json.loads(terrain_metadata_path.read_text(encoding="utf-8"))
    projection = metadata.get("projection", metadata)
    origin_e = float(projection["origin_easting"])
    origin_n = float(projection["origin_northing"])
    content_scale = float(projection["content_scale"])
    offset_x = float(projection["offset_x"])
    offset_y = float(projection["offset_y"])
    grid = load_height_grid(map_path)
    observations = {row["observation_id"]: row for row in store.observations}
    features = {row["feature_id"]: row for row in store.features}
    index = CanonicalIndex(canonical_doc)
    template_by_id: dict[str, dict[str, Any]] = {}
    for pack in packs:
        for template in pack.get("templates", []):
            template_by_id[str(template.get("template_id"))] = template

    def project_metric(point: list[float] | tuple[float, ...]) -> tuple[float, float]:
        return (
            (float(point[0]) - origin_e) * content_scale + offset_x,
            (float(point[1]) - origin_n) * content_scale + offset_y,
        )

    meshes_out.mkdir(parents=True, exist_ok=True)
    mesh_cache: dict[str, str] = {}
    instances: list[dict[str, Any]] = []
    per_subtype: dict[str, int] = {}
    selected_templates: dict[str, int] = {}
    skipped_by_state: list[dict[str, Any]] = []
    proxy_instances = 0
    consumed = 0

    def plan_subtype(feature: dict, tags: dict) -> str:
        try:
            return _subtype(tags)
        except ValueError:
            if (feature.get("semantic_class") == "crossing"
                    and tags.get("railway") in {"level_crossing", "crossing"}):
                return "railway_crossing_marker"
            raise

    for row in sorted(plan_rows, key=lambda r: str(r.get("feature_id"))):
        fid = str(row.get("feature_id"))
        if row.get("owner_stage") != "fixtures":
            raise ValueError(f"fixtures owner file holds {row.get('owner_stage')}: {fid}")
        if row.get("attachment_state") != "resolved" or not row.get("attachment"):
            skipped_by_state.append({
                "feature_id": fid,
                "representation_id": row.get("representation_id"),
                "attachment_state": row.get("attachment_state"),
                "reason": row.get("reason") or "plan row not resolved",
            })
            continue
        # NUMABE-FIXTURE-01 amendment: the plan row is the authority — only
        # disposition mesh_fixture emits; owed/debt rows are skipped and listed.
        if row.get("disposition") != "mesh_fixture":
            skipped_by_state.append({
                "feature_id": fid,
                "representation_id": row.get("representation_id"),
                "attachment_state": row.get("attachment_state"),
                "reason": "plan disposition %s is not fixtures-emitted (owed/debt)" % (
                    row.get("disposition")),
            })
            continue
        feature = features.get(fid)
        if feature is None:
            raise ValueError(f"plan fixtures row has no canonical feature: {fid}")
        tags, source_kind = _plan_source_tags(feature, observations)
        match = str(row.get("asset_match") or "")
        if match == "new_asset_required" and not tags:
            # NUMABE-FIXTURE-01: fused street rows name a missing asset; the plan
            # row is the authority but there is no template to emit, and no
            # placeholder may assert presence. Skip, listed.
            skipped_by_state.append({
                "feature_id": fid,
                "representation_id": row.get("representation_id"),
                "attachment_state": row.get("attachment_state"),
                "reason": "new_asset_required: %s" % (
                    row.get("asset_missing_claim") or "no template in pack chain"),
                "new_asset": True,
            })
            continue
        # Amendment: mesh rows naming no asset (null/none) with no tags to
        # type them are owed in effect — skip, listed. Tag-bearing rows keep
        # the legacy tag-typed path below.
        if match in ("", "none") and not tags:
            skipped_by_state.append({
                "feature_id": fid,
                "representation_id": row.get("representation_id"),
                "attachment_state": row.get("attachment_state"),
                "reason": "new_asset_required: no asset_match on plan row",
                "new_asset": True,
            })
            continue
        else:
            routed_note = None
        if match.startswith("catalog:"):
            tid = match[len("catalog:"):]
            template = template_by_id.get(tid)
            if template is None and tags:
                # Named asset is not a fixture template (e.g. an
                # infrastructure cluster profile): the marker aspect still
                # builds from tags; the cluster is infra-owned. Record it.
                routed_note = ("named asset %s is infra-owned; fixtures lane "
                               "builds the marker aspect from tags" % tid)
                tagged = plan_subtype(feature, tags)
                module = ROUTED_SUBTYPE_MODULE.get(tagged, tagged)
                candidates = resolve_template_candidates(
                    semantic_candidates=[module],
                    context=context, packs=packs,
                )
                if not candidates:
                    raise ValueError(f"locale pack chain has no template: {fid}")
                template = candidates[0]
                subtype = module
            elif template is None:
                # NUMABE-FIXTURE-01: fused row names a template absent from the
                # pack; emit the asset_match's family template when present,
                # else skip with new_asset_required listed.
                street_subtype = _street_subtype_from_plan(row, feature)
                if street_subtype is None:
                    skipped_by_state.append({
                        "feature_id": fid,
                        "representation_id": row.get("representation_id"),
                        "attachment_state": row.get("attachment_state"),
                        "reason": "new_asset_required: %s" % (
                            row.get("asset_missing_claim") or
                            ("named asset %s absent from pack chain" % tid)),
                        "new_asset": True,
                    })
                    continue
                module = ROUTED_SUBTYPE_MODULE.get(street_subtype, street_subtype)
                candidates = resolve_template_candidates(
                    semantic_candidates=[module],
                    context=context, packs=packs,
                )
                if not candidates:
                    skipped_by_state.append({
                        "feature_id": fid,
                        "representation_id": row.get("representation_id"),
                        "attachment_state": row.get("attachment_state"),
                        "reason": "new_asset_required: no family template for %s" % tid,
                        "new_asset": True,
                    })
                    continue
                template = candidates[0]
                subtype = module
                routed_note = ("named asset %s absent from pack; fixtures lane "
                               "builds family template %s" % (
                                   tid, template.get("template_id")))
            else:
                family = str(template.get("geometry_family") or "")
                short = family[3:] if family.startswith("jp_") else family
                if short not in ARCHETYPE_PHYSICAL:
                    # NUMABE-FIXTURE-01: named template's family has no builder;
                    # never emit a placeholder that asserts presence: skip listed.
                    skipped_by_state.append({
                        "feature_id": fid,
                        "representation_id": row.get("representation_id"),
                        "attachment_state": row.get("attachment_state"),
                        "reason": "new_asset_required: family %s of %s has no builder" % (
                            family, tid),
                        "new_asset": True,
                    })
                    continue
                subtype = short
        else:
            tagged = plan_subtype(feature, tags)
            module = ROUTED_SUBTYPE_MODULE.get(tagged, tagged)
            candidates = resolve_template_candidates(
                semantic_candidates=[module],
                context=context, packs=packs,
            )
            if not candidates:
                raise ValueError(f"locale pack chain has no template: {fid}")
            template = candidates[0]
            subtype = module
        mesh_key = str(template.get("template_id"))
        mesh_name = mesh_cache.get(mesh_key)
        if mesh_name is None:
            mesh_name = f"fixture_{template.get('geometry_family')}.akm"
            vertices, faces = _build_archetype(
                subtype, content_scale, template.get("parameters"),
            )
            physical = template.get("physical") or {}
            native = physical.get("native_bbox_m")
            if native is not None:
                xs = [v[0] for v in vertices]
                ys = [v[1] for v in vertices]
                zs = [v[2] for v in vertices]
                built = ((max(xs) - min(xs)) / content_scale,
                         (max(ys) - min(ys)) / content_scale,
                         (max(zs) - min(zs)) / content_scale)
                for got, want, axis in zip(built, native, "xyz"):
                    if want and abs(got - want) / want > 0.05:
                        raise ValueError(
                            f"builder subtype {subtype} does not match template "
                            f"{mesh_key}: axis {axis} {got:.3f} vs {want:.3f}")
            write_akm(
                meshes_out / mesh_name, vertices, faces,
                comment="FL-4809 plan-driven fixture archetype; alpha=255 => no collision",
            )
            mesh_cache[mesh_key] = mesh_name
        pose = resolve(row["attachment"], index)
        world_x, world_y = project_metric((pose["E"], pose["N"]))
        yaw = math.radians(float(pose["yaw_deg"]))
        ground = terrain_z(grid, world_x, world_y)
        if ground is None:
            raise ValueError(f"plan-sited fixture lies outside compiled terrain: {fid}")
        asserts = bool(row.get("asserts_physical_presence", True))
        if not asserts:
            proxy_instances += 1
        control = _centroid(feature["geometry"])
        source_world = project_metric(control)
        instance_name = "fixture_" + fid.replace(":", "_").replace("/", "_")
        c, s = math.cos(yaw), math.sin(yaw)
        instances.append({
            "feature_id": fid,
            "mesh_name": mesh_name,
            "inst_name": instance_name,
            "transform": [
                c, s, 0.0, 0.0,
                -s, c, 0.0, 0.0,
                0.0, 0.0, HEIGHT_SCALE, 0.0,
                world_x, world_y, float(round(ground, 3)), 1.0,
            ],
            "flags": 3,
            "story_id": -1,
            "semantic_subtype": subtype,
            "template_id": str(template.get("template_id")),
            "resolved_parameters": template.get("parameters", {}),
            "source_control_world_xy": list(source_world),
            "physical_offset_m": round(
                math.dist(source_world, (world_x, world_y)) / content_scale, 3),
            "road_centerline_clearance_m": None,
            "representation_id": row.get("representation_id"),
            "representation_kind": row.get("representation_kind"),
            "asserts_physical_presence": asserts,
            "asset_match": match,
            "source_kind": source_kind,
            "provenance": {
                "identity": "plan asset_match " + (match or "tag-selected"),
                "position": "plan attachment %s resolved against canonical %s" % (
                    row["attachment"].get("anchor_kind"), row.get("source_bridge")),
                "rendering_geometry": "synthesized locale archetype",
                "orientation": "plan attachment yaw",
                "collision": "none; alpha=255" + ("" if asserts else "; proxy presentation-only"),
                **({"routing": routed_note} if routed_note else {}),
            },
        })
        consumed += 1
        per_subtype[subtype] = per_subtype.get(subtype, 0) + 1
        selected_templates[str(template.get("template_id"))] = (
            selected_templates.get(str(template.get("template_id")), 0) + 1)
    instances_out.parent.mkdir(parents=True, exist_ok=True)
    instances_out.write_text(
        json.dumps(instances, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    embedded = False
    if embed_map:
        editor = _load_a3d_edit()
        pre, version, existing, player_start, enemy_gens, markers = editor.read_a3d_sections(map_path)
        existing_names = {row.inst_name for row in existing}
        duplicate = sorted(row["inst_name"] for row in instances if row["inst_name"] in existing_names)
        if duplicate:
            raise ValueError(f"fixture instances already embedded: {duplicate}")
        additions = [editor.a3d_format.A3DInstance(
            mesh_name=row["mesh_name"], inst_name=row["inst_name"],
            transform=row["transform"], flags=row["flags"], story_id=row["story_id"],
        ) for row in instances]
        editor.write_a3d_sections(
            map_path, pre, version, [*existing, *additions], player_start, enemy_gens, markers,
        )
        embedded = True
    missing_instances: list[str] = []
    if embed_map:
        _pre, _version, actual, _start, _gens, _markers = editor.read_a3d_sections(map_path)
        actual_names = {row.inst_name for row in actual}
        missing_instances = sorted(
            row["inst_name"] for row in instances
            if row["inst_name"] not in actual_names
        )
        if missing_instances:
            raise ValueError(f"fixture instances missing after embed: {missing_instances}")
    missing_meshes = sorted(
        mesh for mesh in set(mesh_cache.values())
        if not (meshes_out / mesh).is_file() or (meshes_out / mesh).stat().st_size == 0
    )
    if missing_meshes:
        raise ValueError(f"fixture meshes missing: {missing_meshes}")
    receipt = {
        "schema": "y92.world_ir_fixture_lowering.v1",
        "stage": "executed_artifact_attested",
        "locale_pack_chain": actual_chain,
        "counts": {
            "plan_rows": len(plan_rows),
            "plan_consumed": consumed,
            "fixtures_emitted": len(instances),
            "mesh_instances_embedded": len(instances) if embedded else 0,
            "map_embedded": embedded,
            "archetype_meshes_nonempty": len(mesh_cache),
            "proxy_instances": proxy_instances,
            "skipped_by_state": len(skipped_by_state),
            "skipped_new_asset_required": sum(
                1 for row in skipped_by_state if row.get("new_asset")),
        },
        "fixtures_by_subtype": dict(sorted(per_subtype.items())),
        "selected_templates": dict(sorted(selected_templates.items())),
        "plan": {
            "consumed": consumed,
            "built": len(instances),
            "skipped_by_state": skipped_by_state,
        },
        "instances": str(instances_out.resolve()),
        "mesh_root": str(meshes_out.resolve()),
    }
    receipt_out.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt

def compile_world_ir_fixtures(
    *, world_ir_path: Path, requirements_path: Path, template_root: Path,
    terrain_metadata_path: Path, map_path: Path, meshes_out: Path,
    instances_out: Path, receipt_out: Path,
    plan_rows: list[dict[str, Any]] | None = None,
    canonical_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile, embed, and attest every mapped fixture in one direct map."""
    if plan_rows is not None:
        if canonical_doc is None:
            raise ValueError("plan-driven fixtures need the canonical document")
        return _compile_fixtures_from_plan(
            world_ir_path=world_ir_path, requirements_path=requirements_path,
            template_root=template_root, terrain_metadata_path=terrain_metadata_path,
            map_path=map_path, meshes_out=meshes_out, instances_out=instances_out,
            receipt_out=receipt_out, plan_rows=plan_rows, canonical_doc=canonical_doc,
        )
    store = WorldIRStore.load(world_ir_path)
    store.assert_valid()
    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    context = requirements["locale_contexts"]["route_default"]
    packs = discover_pack_chain(template_root, context)
    declared_chain = context.get("template_pack_chain", [])
    actual_chain = [pack["pack_id"] for pack in packs]
    if actual_chain != declared_chain:
        raise ValueError(f"locale pack drift: {actual_chain} != {declared_chain}")

    metadata = json.loads(terrain_metadata_path.read_text(encoding="utf-8"))
    projection = metadata.get("projection", metadata)
    origin_e = float(projection["origin_easting"])
    origin_n = float(projection["origin_northing"])
    content_scale = float(projection["content_scale"])
    offset_x = float(projection["offset_x"])
    offset_y = float(projection["offset_y"])
    grid = load_height_grid(map_path)
    observations = {row["observation_id"]: row for row in store.observations}

    def project_metric(point: list[float] | tuple[float, ...]) -> tuple[float, float]:
        return (
            (float(point[0]) - origin_e) * content_scale + offset_x,
            (float(point[1]) - origin_n) * content_scale + offset_y,
        )

    road_polylines = [
        [project_metric(point) for point in feature["geometry"]["coordinates"]]
        for feature in store.features
        if feature.get("semantic_class") == "road"
        and feature.get("geometry", {}).get("kind") == "polyline"
        and len(feature["geometry"].get("coordinates", [])) >= 2
    ]

    fixtures = sorted(
        (feature for feature in store.features
         if feature.get("semantic_class") in {"fixture", "sign", "utility_pole"}
         or (feature.get("semantic_class") == "crossing"
             and feature.get("geometry", {}).get("kind") == "point"
             and _source_tags(feature, observations).get("highway") == "crossing")
         or (feature.get("semantic_class") == "tree"
             and feature.get("geometry", {}).get("kind") == "point")),
        key=lambda feature: feature["feature_id"],
    )
    meshes_out.mkdir(parents=True, exist_ok=True)
    mesh_by_subtype: dict[str, str] = {}
    instances: list[dict[str, Any]] = []
    per_subtype: dict[str, int] = {}
    selected_templates: dict[str, int] = {}
    for feature in fixtures:
        tags = _source_tags(feature, observations)
        subtype = _subtype(tags)
        candidates = resolve_template_candidates(
            semantic_candidates=[subtype], context=context, packs=packs,
        )
        if not candidates:
            raise ValueError(f"locale pack chain has no template for {subtype}")
        selected = candidates[0]
        mesh_name = mesh_by_subtype.get(subtype)
        if mesh_name is None:
            mesh_name = f"fixture_{selected['geometry_family']}.akm"
            vertices, faces = _build_archetype(
                subtype, content_scale, selected.get("parameters"),
            )
            write_akm(
                meshes_out / mesh_name, vertices, faces,
                comment="FL-4814 synthesized fixture archetype; alpha=255 => no collision",
            )
            mesh_by_subtype[subtype] = mesh_name

        easting, northing = _centroid(feature["geometry"])
        source_world_x, source_world_y = project_metric((easting, northing))
        world_x, world_y = source_world_x, source_world_y
        yaw = 0.0
        clearance_m = None
        placement_method = "measured OSM geometry"
        parameters = selected.get("parameters", {})
        if parameters.get("placement_mode") == "roadside_from_logical_control":
            frame = _nearest_polyline_frame((source_world_x, source_world_y), road_polylines)
            if frame is None:
                raise ValueError(
                    f"{subtype} requires a road frame: {feature['feature_id']}"
                )
            _road_point, road_yaw, road_distance = frame
            direction = str(tags.get("traffic_signals:direction") or tags.get("direction") or "forward")
            travel_yaw = road_yaw + (math.pi if direction == "backward" else 0.0)
            traffic_side = str(parameters.get("traffic_side", "left"))
            side = 1.0 if traffic_side == "left" else -1.0
            offset_world = float(parameters["roadside_offset_m"]) * content_scale
            preferred_angle = travel_yaw + side * math.pi / 2.0
            placement_candidates = [
                (
                    source_world_x + math.cos(preferred_angle) * offset_world,
                    source_world_y + math.sin(preferred_angle) * offset_world,
                )
            ]
            corner_offset_m = parameters.get("intersection_corner_offset_m")
            if corner_offset_m is not None:
                corner_offset = float(corner_offset_m) * content_scale
                for degrees in range(-80, 81, 10):
                    angle_delta = math.radians(degrees)
                    angle = preferred_angle + angle_delta
                    placement_candidates.append((
                        source_world_x + math.cos(angle) * corner_offset,
                        source_world_y + math.sin(angle) * corner_offset,
                    ))
            # A control node can sit at the crossing of two roads.  A simple
            # perpendicular offset can therefore leave the physical pole in
            # the other carriageway.  Prefer the same traffic side while
            # maximizing clearance from every mapped road centreline.
            def road_clearance(candidate: tuple[float, float]) -> float:
                candidate_frame = _nearest_polyline_frame(candidate, road_polylines)
                return candidate_frame[2] if candidate_frame is not None else -1.0

            world_x, world_y = max(placement_candidates, key=road_clearance)
            clearance_m = road_clearance((world_x, world_y)) / content_scale
            minimum_clearance_m = float(parameters.get("minimum_road_clearance_m", 0.0))
            if clearance_m < minimum_clearance_m:
                raise ValueError(
                    f"{subtype} cannot reach required road clearance: "
                    f"{feature['feature_id']} {clearance_m:.3f}m < {minimum_clearance_m:.3f}m"
                )
            # The archetype's visible face points toward local -Y.
            yaw = travel_yaw - math.pi / 2.0
            placement_method = (
                "derived roadside position from logical OSM control point; "
                f"nearest-road residual={road_distance / content_scale:.3f}m"
            )
        ground = terrain_z(grid, world_x, world_y)
        if ground is None:
            raise ValueError(f"fixture lies outside compiled terrain: {feature['feature_id']}")
        instance_name = "fixture_" + feature["feature_id"].replace(":", "_").replace("/", "_")
        c, s = math.cos(yaw), math.sin(yaw)
        instances.append({
            "feature_id": feature["feature_id"],
            "mesh_name": mesh_name,
            "inst_name": instance_name,
            "transform": [
                c, s, 0.0, 0.0,
                -s, c, 0.0, 0.0,
                0.0, 0.0, HEIGHT_SCALE, 0.0,
                world_x, world_y, float(round(ground, 3)), 1.0,
            ],
            "flags": 3,
            "story_id": -1,
            "semantic_subtype": subtype,
            "template_id": selected["template_id"],
            "resolved_parameters": parameters,
            "source_control_world_xy": [source_world_x, source_world_y],
            "physical_offset_m": round(
                math.dist((source_world_x, source_world_y), (world_x, world_y))
                / content_scale,
                3,
            ),
            "road_centerline_clearance_m": (
                round(clearance_m, 3) if clearance_m is not None else None
            ),
            "provenance": {
                "identity": "derived from OSM tags",
                "position": placement_method,
                "rendering_geometry": "synthesized locale archetype",
                "orientation": (
                    "derived from nearest road frame"
                    if yaw else "synthesized zero yaw; source carries no usable bearing"
                ),
                "collision": "none; alpha=255",
            },
        })
        per_subtype[subtype] = per_subtype.get(subtype, 0) + 1
        selected_templates[selected["template_id"]] = (
            selected_templates.get(selected["template_id"], 0) + 1
        )

    # FL-4809 NUMABE-REPRESENT-02: every World IR feature that reached the
    # built world with no visual owner gets an explicit representation
    # decision in this compiler. Station/stop places get a station_stop sign
    # (reused traffic_sign module), memorial places get an information_board,
    # railway crossing points get a marker post (reused railway_signal
    # module; approach gates stay owned by the infrastructure cluster
    # equipment), and station entrances get a gate (reused pedestrian_gate
    # module) hosted on the parent building edge facing the road under the
    # REPRESENT-01 burial gate. No procedural facades: every archetype reuses
    # an existing locale module. Locality places stay owned by the
    # place-front lowerer (minimap-landmark exclusion).
    from scripts.pipeline.bake_osm_building_footprints import point_in_polygon

    represent02_reasons = {
        "station_stop": "station/stop identity sign; reused traffic_sign module",
        "information_board": "historic memorial marker",
        "station_entrance_gate": "station entrance gate on the parent building edge facing the road",
        "railway_crossing_marker": "level-crossing marker post; approach gates owned by infrastructure cluster equipment",
    }
    represent02_front_height = {
        "station_stop": 2.26,
        "information_board": 2.12,
        "station_entrance_gate": 1.1,
        "railway_crossing_marker": 3.15,
    }
    # Authored-bbox half extents in metres: (half_x, half_y) per subtype.
    represent02_half_extents = {
        "station_stop": (0.35, 0.07),
        "information_board": (0.88, 0.19),
        "station_entrance_gate": (1.0, 0.10),
        "railway_crossing_marker": (0.20, 0.13),
    }

    def _represent02_nearest_on_segment(point, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        denominator = dx * dx + dy * dy
        if denominator <= 1e-12:
            return a
        t = max(0.0, min(1.0, ((point[0] - a[0]) * dx
            + (point[1] - a[1]) * dy) / denominator))
        return (a[0] + t * dx, a[1] + t * dy)

    represent02_buildings: list[tuple[dict, list]] = []
    represent02_roads: list[tuple] = []
    for _feature in store.features:
        if (_feature.get("semantic_class") == "building_footprint"
                and _feature.get("geometry", {}).get("kind") == "polygon"):
            _ring = [project_metric(point) for point in _feature["geometry"]["coordinates"]]
            if len(_ring) > 2:
                represent02_buildings.append((_feature, _ring))
        elif (_feature.get("semantic_class") == "road"
                and _feature.get("geometry", {}).get("kind") == "polyline"):
            _line = [project_metric(point) for point in _feature["geometry"]["coordinates"]]
            represent02_roads.extend(zip(_line, _line[1:]))
    represent02_parent_rings = {
        _building["feature_id"]: _ring for _building, _ring in represent02_buildings
    }
    represent02_emitted_ids = {row["feature_id"] for row in instances}
    represent02_candidates: list[tuple[dict, dict, str]] = []
    for _feature in sorted(store.features, key=lambda row: row["feature_id"]):
        _kind = _feature.get("semantic_class")
        if _kind in {"fixture", "sign", "utility_pole", "tree"}:
            continue  # main-loop owned; never double-emit
        if _feature["feature_id"] in represent02_emitted_ids:
            continue  # already represented above
        _tags: dict[str, Any] = {}
        for _observation_id in _feature.get("supporting_observation_ids", []):
            _row = observations.get(_observation_id) or {}
            _row_tags = (_row.get("attributes") or {}).get("tags")
            if _row_tags:
                _tags = dict(_row_tags)
                break
        _subtype_routed = None
        if _kind == "place" and _tags.get("railway") in {"station", "stop"}:
            _subtype_routed = "station_stop"
        elif _kind == "place" and _tags.get("historic") == "memorial":
            _subtype_routed = "information_board"
        elif _kind == "station_entrance" and _tags.get("railway") == "train_station_entrance":
            _subtype_routed = "station_entrance_gate"
        elif (_kind == "crossing" and _feature.get("geometry", {}).get("kind") == "point"
                and _tags.get("railway") in {"level_crossing", "crossing"}):
            _subtype_routed = "railway_crossing_marker"
        else:
            continue
        if _feature.get("geometry", {}).get("kind") != "point":
            raise ValueError(
                f"represent02 needs point geometry: {_feature['feature_id']}"
            )
        if _subtype(_tags) != _subtype_routed:
            raise ValueError(
                f"represent02 routing drift for {_feature['feature_id']}: "
                f"table says {_subtype(_tags)}, router says {_subtype_routed}"
            )
        represent02_candidates.append((_feature, _tags, _subtype_routed))
    represent02_rows: list[dict[str, Any]] = []
    represent02_placements: list[dict[str, Any]] = []
    for _feature, _tags, _subtype_new in represent02_candidates:
        _module = ROUTED_SUBTYPE_MODULE.get(_subtype_new, _subtype_new)
        _candidates = resolve_template_candidates(
            semantic_candidates=[_module], context=context, packs=packs,
        )
        if not _candidates:
            raise ValueError(f"locale pack chain has no template for {_module}")
        _selected = _candidates[0]
        _mesh_name = mesh_by_subtype.get(_subtype_new)
        if _mesh_name is None:
            _mesh_name = f"fixture_{_selected['geometry_family']}.akm"
            _vertices, _faces = _build_archetype(
                _module, content_scale, _selected.get("parameters"),
            )
            write_akm(
                meshes_out / _mesh_name, _vertices, _faces,
                comment="FL-4809 NUMABE-REPRESENT-02 fixture archetype; alpha=255 => no collision",
            )
            mesh_by_subtype[_subtype_new] = _mesh_name
        _easting, _northing = _centroid(_feature["geometry"])
        _source_x, _source_y = project_metric((_easting, _northing))
        _world_x, _world_y = _source_x, _source_y
        _yaw = 0.0
        _parent_id: str | None = None
        _label = _tags.get("name:en") or _tags.get("name")
        if _subtype_new == "station_stop":
            _label = _label or "station sign"
        elif _subtype_new == "information_board":
            _label = _label or "information board"
        elif _subtype_new == "railway_crossing_marker":
            _label = _label or "level-crossing marker"
        if _subtype_new == "station_entrance_gate":
            _label = f"station entrance {(_tags.get('ref') or '').strip() or '?'}"
            _gate_point = (_source_x, _source_y)
            _best = None
            for _building, _ring in represent02_buildings:
                _inside = point_in_polygon(_gate_point[0], _gate_point[1], _ring)
                _stripped = _ring[:-1] if _ring[0] == _ring[-1] else _ring
                _centroid_pt = (
                    sum(point[0] for point in _stripped) / len(_stripped),
                    sum(point[1] for point in _stripped) / len(_stripped),
                )
                _closed = _ring if _ring[0] == _ring[-1] else [*_ring, _ring[0]]
                for _ea, _eb in zip(_closed, _closed[1:]):
                    _near = _represent02_nearest_on_segment(_gate_point, _ea, _eb)
                    _dist = math.dist(_gate_point, _near)
                    _candidate = (0 if _inside else 1, _dist,
                                  _building["feature_id"], _ring, _centroid_pt,
                                  _ea, _eb, _near)
                    if _best is None or _candidate[:2] < _best[:2]:
                        _best = _candidate
            if _best is None or (_best[0] and _best[1] > 12.0 * content_scale):
                raise ValueError(
                    f"represent02 gate has no parent edge: {_feature['feature_id']}"
                )
            _parent_id = _best[2]
            _ring = _best[3]
            _centroid_pt = _best[4]
            _closed = _ring if _ring[0] == _ring[-1] else [*_ring, _ring[0]]
            _street_edges = []
            for _ea, _eb in zip(_closed, _closed[1:]):
                _edge_length = math.dist(_ea, _eb)
                if _edge_length < 0.8 * content_scale:
                    continue
                _midpoint = ((_ea[0] + _eb[0]) / 2, (_ea[1] + _eb[1]) / 2)
                _road_distance = min(
                    (math.dist(_midpoint, _represent02_nearest_on_segment(_midpoint, _ra, _rb))
                     for _ra, _rb in represent02_roads),
                    default=float("inf"),
                )
                _poi_distance = math.dist(_gate_point, _midpoint)
                _street_edges.append((_road_distance, _poi_distance, _ea, _eb, _midpoint))
            if not _street_edges:
                raise ValueError(
                    f"represent02 gate parent has no usable edge: {_feature['feature_id']}"
                )
            _hosted = None
            for _rank_road, _rank_poi, _ea, _eb, _near in sorted(
                    _street_edges, key=lambda row: row[:2]):
                _edge_yaw = math.atan2(_eb[1] - _ea[1], _eb[0] - _ea[0])
                _outward = (_near[0] - _centroid_pt[0], _near[1] - _centroid_pt[1])
                _norm = math.hypot(*_outward)
                if _norm <= 1e-9:
                    _outward = (-math.sin(_edge_yaw), math.cos(_edge_yaw))
                else:
                    _outward = (_outward[0] / _norm, _outward[1] / _norm)
                _yaw = _edge_yaw
                if math.sin(_yaw) * _outward[0] + (-math.cos(_yaw)) * _outward[1] < 0.0:
                    _yaw += math.pi
                _anchor = (_near[0] + _outward[0] * 0.70 * content_scale,
                           _near[1] + _outward[1] * 0.70 * content_scale)
                _gate_ground = terrain_z(grid, *_anchor)
                if _gate_ground is None:
                    continue
                _probe_hx, _probe_ly0, _probe_ly1 = (
                    1.05 * content_scale, -0.20 * content_scale, 0.20 * content_scale)
                _pc, _ps = math.cos(_yaw), math.sin(_yaw)
                _probe = [
                    [_anchor[0] + _pc * _lx - _ps * _ly,
                     _anchor[1] + _ps * _lx + _pc * _ly]
                    for _lx, _ly in ((-_probe_hx, _probe_ly0), (_probe_hx, _probe_ly0),
                                     (_probe_hx, _probe_ly1), (-_probe_hx, _probe_ly1))]
                _samples = list(_probe)
                for (_pax, _pay), (_pbx, _pby) in zip(_probe, _probe[1:] + _probe[:1]):
                    _samples.append(((_pax + _pbx) / 2.0, (_pay + _pby) / 2.0))
                _probe_clearance = round(
                    min(_point_to_ring_signed_distance(_point, _ring) for _point in _samples)
                    / (1.0 / MESH_PLACE_CELLS_PER_WORLD) - 1.0, 3)
                if _probe_clearance < 0:
                    continue
                _hosted = (_yaw, _anchor, _gate_ground)
                break
            if _hosted is None:
                raise ValueError(
                    f"represent02 gate buries in parent mass: {_feature['feature_id']}"
                )
            _yaw, (_world_x, _world_y), _ground = _hosted
        else:
            _ground = terrain_z(grid, _world_x, _world_y)
            if _ground is None:
                raise ValueError(f"fixture lies outside compiled terrain: {_feature['feature_id']}")
            if _parent_id is None:
                _nearest_ring = None
                for _fid, _ring in represent02_parent_rings.items():
                    _closed = _ring if _ring[0] == _ring[-1] else [*_ring, _ring[0]]
                    _best_dist = min(
                        math.dist((_world_x, _world_y),
                                  _represent02_nearest_on_segment((_world_x, _world_y), _ea, _eb))
                        for _ea, _eb in zip(_closed, _closed[1:]))
                    if _nearest_ring is None or _best_dist < _nearest_ring[1]:
                        _nearest_ring = (_fid, _best_dist)
                if _nearest_ring is None:
                    raise ValueError(
                        f"represent02 freestanding fixture has no parent ring: {_feature['feature_id']}"
                    )
                _parent_id = _nearest_ring[0]
        _hx_m, _hy_m = represent02_half_extents[_subtype_new]
        _hx, _hy = _hx_m * content_scale, _hy_m * content_scale
        _cc, _ss = math.cos(_yaw), math.sin(_yaw)
        _poly = [
            [_world_x + _cc * _lx - _ss * _ly, _world_y + _ss * _lx + _cc * _ly]
            for _lx, _ly in ((-_hx, -_hy), (_hx, -_hy), (_hx, _hy), (-_hx, _hy))]
        represent02_rows.append({
            "feature_id": _feature["feature_id"],
            "label": _label,
            "parent_id": _parent_id,
            "world_polygon": _poly,
            "front_height_m": represent02_front_height[_subtype_new],
        })
        represent02_placements.append({
            "feature": _feature,
            "tags": _tags,
            "subtype": _subtype_new,
            "label": _label,
            "selected": _selected,
            "parameters": _selected.get("parameters", {}),
            "mesh_name": _mesh_name,
            "world_x": _world_x,
            "world_y": _world_y,
            "ground": _ground,
            "yaw": _yaw,
            "source_x": _source_x,
            "source_y": _source_y,
            "parent_id": _parent_id,
        })
    if represent02_rows:
        represent02_per_instance, represent02_buried, _represent02_debt = (
            annotate_mesh_place_instances(
                [],
                fixture_rows=represent02_rows,
                parent_footprints=represent02_parent_rings,
            )
        )
        if represent02_buried > 0:
            _buried_ids = [item["feature_id"] for item in represent02_per_instance if item["buried"]]
            raise ValueError(f"represent02 burial: {sorted(_buried_ids)}")
    else:
        represent02_per_instance = []
    represent02_clearance = {
        row["feature_id"]: (row["representation"], row["clearance_cells"])
        for row in represent02_rows
    }
    represent02_claims: list[dict[str, Any]] = []
    for _placement in represent02_placements:
        _feature = _placement["feature"]
        _subtype_new = _placement["subtype"]
        _selected = _placement["selected"]
        _representation, _clearance_cells = represent02_clearance[_feature["feature_id"]]
        _c, _s = math.cos(_placement["yaw"]), math.sin(_placement["yaw"])
        _instance_name = "fixture_" + _feature["feature_id"].replace(":", "_").replace("/", "_")
        instances.append({
            "feature_id": _feature["feature_id"],
            "mesh_name": _placement["mesh_name"],
            "inst_name": _instance_name,
            "transform": [
                _c, _s, 0.0, 0.0,
                -_s, _c, 0.0, 0.0,
                0.0, 0.0, HEIGHT_SCALE, 0.0,
                _placement["world_x"], _placement["world_y"],
                float(round(_placement["ground"], 3)), 1.0,
            ],
            "flags": 3,
            "story_id": -1,
            "semantic_subtype": _subtype_new,
            "template_id": _selected["template_id"],
            "resolved_parameters": _placement["parameters"],
            "source_control_world_xy": [_placement["source_x"], _placement["source_y"]],
            "physical_offset_m": round(
                math.dist((_placement["source_x"], _placement["source_y"]),
                          (_placement["world_x"], _placement["world_y"]))
                / content_scale,
                3,
            ),
            "road_centerline_clearance_m": None,
            "representation": _representation,
            "clearance_cells": _clearance_cells,
            "provenance": {
                "identity": "derived from OSM tags",
                "position": ("derived parent-edge position from OSM entrance node"
                             if _subtype_new == "station_entrance_gate"
                             else "measured OSM geometry"),
                "rendering_geometry": (
                    f"reused locale {ROUTED_SUBTYPE_MODULE.get(_subtype_new, _subtype_new)} "
                    f"archetype as {_subtype_new}; no procedural facade"),
                "orientation": ("derived from parent building edge facing the road"
                                if _subtype_new == "station_entrance_gate"
                                else "synthesized zero yaw; source carries no usable bearing"),
                "collision": "none; alpha=255",
            },
        })
        per_subtype[_subtype_new] = per_subtype.get(_subtype_new, 0) + 1
        selected_templates[_selected["template_id"]] = (
            selected_templates.get(_selected["template_id"], 0) + 1
        )
        represent02_claims.append({
            "feature_id": _feature["feature_id"],
            "label": _placement["label"],
            "visual_owner": "mesh",
            "collision_owner": "none",
            "navigation_owner": "none",
            "reason": represent02_reasons[_subtype_new],
            "instance": _instance_name,
            "parent": _placement["parent_id"],
            "clearance_cells": _clearance_cells,
        })

    instances_out.parent.mkdir(parents=True, exist_ok=True)
    instances_out.write_text(
        json.dumps(instances, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    editor = _load_a3d_edit()
    pre, version, existing, player_start, enemy_gens, markers = editor.read_a3d_sections(map_path)
    existing_names = {row.inst_name for row in existing}
    duplicate = sorted(row["inst_name"] for row in instances if row["inst_name"] in existing_names)
    if duplicate:
        raise ValueError(f"fixture instances already embedded: {duplicate}")
    additions = [editor.a3d_format.A3DInstance(
        mesh_name=row["mesh_name"], inst_name=row["inst_name"],
        transform=row["transform"], flags=row["flags"], story_id=row["story_id"],
    ) for row in instances]
    editor.write_a3d_sections(
        map_path, pre, version, [*existing, *additions], player_start, enemy_gens, markers,
    )
    _pre, _version, actual, _start, _gens, _markers = editor.read_a3d_sections(map_path)
    actual_names = {row.inst_name for row in actual}
    missing_instances = sorted(
        row["inst_name"] for row in instances
        if row["inst_name"] not in actual_names
    )
    missing_meshes = sorted(
        mesh for mesh in set(mesh_by_subtype.values())
        if not (meshes_out / mesh).is_file() or (meshes_out / mesh).stat().st_size == 0
    )
    if missing_instances or missing_meshes:
        raise ValueError(
            "fixture artifact attestation failed: "
            f"instances={missing_instances}, meshes={missing_meshes}"
        )
    required_rows = requirements.get("required_resolved_archetypes", {}).get("fixtures", [])
    required_audit = []
    for required in required_rows:
        matches = [row for row in instances
                   if row["semantic_subtype"] == required["semantic_class"]]
        if not matches:
            raise ValueError(
                f"required fixture archetype absent: {required['semantic_class']}"
            )
        expected_template = required["template_id"]
        if any(row["template_id"] != expected_template for row in matches):
            raise ValueError(
                f"required fixture template drift for {required['semantic_class']}"
            )
        expected_traits = set(required.get("visual_traits", []))
        for row in matches:
            actual_traits = set(row["resolved_parameters"].get("visual_traits", []))
            if not expected_traits.issubset(actual_traits):
                raise ValueError(
                    f"required visual traits missing for {required['semantic_class']}: "
                    f"{sorted(expected_traits - actual_traits)}"
                )
        required_audit.append({
            "semantic_class": required["semantic_class"],
            "template_id": expected_template,
            "visual_traits": sorted(expected_traits),
            "instances_attested": len(matches),
            "passed": True,
        })

    receipt = {
        "schema": "y92.world_ir_fixture_lowering.v1",
        "stage": "executed_artifact_attested",
        "locale_pack_chain": actual_chain,
        "counts": {
            "world_ir_point_features": len(fixtures),
            "world_ir_fixtures": sum(
                feature.get("semantic_class") == "fixture" for feature in fixtures
            ),
            "world_ir_signs": sum(
                feature.get("semantic_class") == "sign" for feature in fixtures
            ),
            "world_ir_utility_poles": sum(
                feature.get("semantic_class") == "utility_pole" for feature in fixtures
            ),
            "world_ir_crossings": sum(
                feature.get("semantic_class") == "crossing" for feature in fixtures
            ),
            "fixtures_emitted": len(instances),
            "mesh_instances_embedded": len(instances),
            "archetype_meshes_nonempty": len(mesh_by_subtype),
        },
        "fixtures_by_subtype": dict(sorted(per_subtype.items())),
        "selected_templates": dict(sorted(selected_templates.items())),
        "required_resolved_archetype_audit": required_audit,
        "representation": {
            "rule_source": "FL-4809 NUMABE-REPRESENT-02: every routed feature gets an explicit per-id decision row",
            "routed_subtypes": sorted(ROUTED_SUBTYPE_MODULE),
            "per_instance": represent02_claims,
        },
        "instances": str(instances_out.resolve()),
        "mesh_root": str(meshes_out.resolve()),
    }
    receipt_out.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def _commercial_front_ground_floor(
    local_vertices: list[tuple], faces: list[list[int]], *, width_m: float,
    front_profile: dict, palette: dict, scale: float, fallback_text: str,
) -> dict[str, Any]:
    """Author the body, glazing, door, fascia, awning and label of a shop front.

    FL-4876 BRAND-FRONT-01: every dimension that used to be a constant is read
    from the profile's optional ``ground_floor`` object. A profile without it
    gets the previous constants, box for box, so other stores keep their bytes.
    Keys (metres, a = along the viewer's right from the front centre):
      glazing: z_min_m, z_max_m, side_margin_m, rgb, mullion_z_min_m,
        mullion_z_max_m, mullion_width_m, mullion_fractions (legacy layout),
        mullion_spacing_m and bays (bay layout), frame_rgb.
      door: width_m, z_min_m, z_max_m, side (center|left|right), edge_margin_m.
      piers: width_m (brick piers beside the door and between bays).
      awning: false drops the awning slab (default true).
      fascia: face_o_m, the distance of the flat band face in front of the
        anchor (default 0.125).
    Fascia z range, stripe colours and label come from identity_sign and
    fascia_bands as before. Returns the numbers the receipt and the logo need.
    """
    gf = front_profile.get('ground_floor') or {}
    glz = gf.get('glazing') or {}
    door = gf.get('door') or {}
    piers = gf.get('piers') or {}
    fas = gf.get('fascia') or {}
    identity_sign = front_profile.get('identity_sign') or {}
    fascia_z_min = float(identity_sign.get('fascia_z_min_m', 2.12))
    fascia_z_max = float(identity_sign.get('fascia_z_max_m', 2.78))
    label_z_min = float(identity_sign.get('label_z_min_m', 2.24))
    label_z_max = float(identity_sign.get('label_z_max_m', 2.68))
    if not (0.0 < fascia_z_min < fascia_z_max):
        raise ValueError(f"invalid identity fascia in {front_profile['id']}")
    if not (fascia_z_min <= label_z_min < label_z_max <= fascia_z_max):
        raise ValueError(f"identity label leaves fascia in {front_profile['id']}")
    front_top_m = max(2.94, fascia_z_max + .12)
    face_o = float(fas.get('face_o_m', .125))
    face_shift = face_o - .125  # 0.0 for every profile without the key
    _add_box(local_vertices, faces,
             (-width_m / 2, width_m / 2, -.07, .07, .05, front_top_m),
             palette['body'], scale)

    glass_rgb = tuple(glz['rgb']) if glz.get('rgb') else palette['window']
    frame_rgb = tuple(glz['frame_rgb']) if glz.get('frame_rgb') else palette['body']
    gz0 = float(glz.get('z_min_m', .22))
    gz1 = float(glz.get('z_max_m', 1.94))
    margin = float(glz.get('side_margin_m', .18))
    mz0 = float(glz.get('mullion_z_min_m', .18))
    mz1 = float(glz.get('mullion_z_max_m', 2.02))
    mhalf = float(glz.get('mullion_width_m', .07)) / 2
    door_w = float(door.get('width_m', .96))
    door_side = str(door.get('side', 'center'))
    if door_side == 'right':
        door_cx = width_m / 2 - float(door.get('edge_margin_m', .3)) - door_w / 2
    elif door_side == 'left':
        door_cx = -width_m / 2 + float(door.get('edge_margin_m', .3)) + door_w / 2
    elif door_side == 'center':
        door_cx = 0.0
    else:
        raise ValueError(f"invalid door side {door_side!r} in {front_profile['id']}")
    d0, d1 = door_cx - door_w / 2, door_cx + door_w / 2
    if d0 < -width_m / 2 or d1 > width_m / 2:
        raise ValueError(f"door leaves the front in {front_profile['id']}")
    layout = (door_side != 'center' or 'mullion_spacing_m' in glz
              or 'bays' in glz or bool(piers))
    gl0, gl1 = -width_m / 2 + margin, width_m / 2 - margin
    if not layout:
        _add_box(local_vertices, faces, (gl0, gl1, -.10, -.07, gz0, gz1),
                 glass_rgb, scale)
        for fraction in glz.get('mullion_fractions', (-.25, 0.0, .25)):
            mullion_x = fraction * width_m
            _add_box(local_vertices, faces,
                     (mullion_x - mhalf, mullion_x + mhalf, -.115, -.07, mz0, mz1),
                     frame_rgb, scale)
    else:
        pier_w = float(piers.get('width_m', 0.0))
        regions = [r for r in ((gl0, d0 - pier_w), (d1 + pier_w, gl1))
                   if r[1] - r[0] >= .4]
        widest = max(regions, key=lambda r: r[1] - r[0]) if regions else None
        for r0, r1 in regions:
            n_bays = int(glz.get('bays', 1)) if (r0, r1) == widest else 1
            bay_w = (r1 - r0 - pier_w * (n_bays - 1)) / n_bays
            for k in range(n_bays):
                b0 = r0 + k * (bay_w + pier_w)
                b1 = b0 + bay_w
                _add_box(local_vertices, faces, (b0, b1, -.10, -.07, gz0, gz1),
                         glass_rgb, scale)
                spacing = float(glz.get('mullion_spacing_m', 0.0))
                n_panes = max(1, int(round(bay_w / spacing))) if spacing > 0 else 1
                for j in range(1, n_panes):
                    mx = b0 + bay_w * j / n_panes
                    _add_box(local_vertices, faces,
                             (mx - mhalf, mx + mhalf, -.115, -.07, mz0, mz1),
                             frame_rgb, scale)
                if k:
                    _add_box(local_vertices, faces,
                             (b0 - pier_w, b0, -.115, -.07, mz0, mz1),
                             palette['body'], scale)
            if pier_w > 0:
                edge = d0 if r1 <= d0 else d1
                lo, hi = (r1, edge) if r1 <= d0 else (edge, r0)
                _add_box(local_vertices, faces, (lo, hi, -.115, -.07, mz0, mz1),
                         palette['body'], scale)
    _add_box(local_vertices, faces,
             (d0, d1, -.125, -.07,
              float(door.get('z_min_m', .06)), float(door.get('z_max_m', 2.04))),
             palette['door'], scale)
    fascia_bands = front_profile.get('fascia_bands')
    if fascia_bands:
        top = fascia_z_max
        total_height = fascia_z_max - fascia_z_min
        total_fraction = sum(float(band['fraction']) for band in fascia_bands)
        if total_fraction <= 0.0:
            raise ValueError(f"invalid fascia bands in {front_profile['id']}")
        for band in fascia_bands:
            height = total_height * float(band['fraction']) / total_fraction
            _add_box(
                local_vertices, faces,
                (-width_m / 2 + .08, width_m / 2 - .08,
                 -.125 - face_shift, -.07, top - height, top),
                tuple(band['rgb']), scale,
            )
            top -= height
    else:
        _add_box(local_vertices, faces,
                 (-width_m / 2 + .08, width_m / 2 - .08, -.12 - face_shift, -.07,
                  fascia_z_min, fascia_z_max),
                 palette['primary'], scale)
        _add_box(local_vertices, faces,
                 (-width_m / 2 + .08, width_m / 2 - .08,
                  -.125 - face_shift, -.12 - face_shift,
                  fascia_z_min - .04, fascia_z_min + .04),
                 palette['accent'], scale)
    if gf.get('awning', True):
        _add_box(local_vertices, faces,
                 (-width_m / 2, width_m / 2, -.58, .05, 1.94, 2.08),
                 palette['awning'], scale)
    rendered_text = _add_block_text(
        local_vertices,
        faces,
        str(identity_sign.get('display_text') or fallback_text or ''),
        max_width_m=width_m * float(identity_sign.get('label_width_fraction', .96)),
        z_min_m=label_z_min,
        z_max_m=label_z_max,
        rgb=palette['label'],
        scale=scale,
        y_min_m=float(identity_sign.get('label_y_min_m', -.205)),
        y_max_m=float(identity_sign.get('label_y_max_m', -.165)),
    )
    return {
        'rendered_text': rendered_text,
        'fascia_z_min': fascia_z_min, 'fascia_z_max': fascia_z_max,
        'label_z_min': label_z_min, 'label_z_max': label_z_max,
        'fascia_face_o_m': face_o,
        'identity_sign': identity_sign,
    }


def compile_world_ir_place_fronts(
    *, world_ir_path: Path, terrain_metadata_path: Path, ground_map_path: Path,
    map_path: Path, meshes_out: Path, receipt_out: Path,
    template_root: Path, locale_context: dict[str, Any],
    route_path: Path | None = None,
    requirements_path: Path | None = None,
    building_instances_path: Path | None = None,
    plan_rows: list[dict[str, Any]] | None = None,
    canonical_doc: dict[str, Any] | None = None,
    embed_map: bool = True,
    instances_out: Path | None = None,
) -> dict[str, Any]:
    """Attach cheap source-named storefront identity to parent buildings.

    OSM place points identify tenancies, not measured signs.  This lowerer
    associates each named commercial place with its containing/nearest
    building edge and emits a coarse fascia/awning proposal.  Brand-aware
    colours are useful PS1 identity; text and exact layout remain unresolved
    until a source-derived sign observation replaces the proposal.
    """
    from scripts.pipeline.bake_osm_building_footprints import point_in_polygon

    store = WorldIRStore.load(world_ir_path)
    store.assert_valid()
    observations = {row['observation_id']: row for row in store.observations}
    packs = discover_pack_chain(template_root, locale_context)
    metadata = json.loads(terrain_metadata_path.read_text())
    projection = metadata.get('projection', metadata)
    scale = float(projection['content_scale'])
    grid = load_height_grid(ground_map_path)
    # SIGN-WALL-01: the baked map (buildings extruded) locates each front's wall.
    from scripts.pipeline.extruded_wall import Terrain, wall_foot
    wall_terrain = Terrain(map_path) if Path(map_path).is_file() else None

    def _raised_at(x: float, y: float) -> bool:
        """Baked terrain stands > 1 m above the unbaked ground: building mass."""
        baked = wall_terrain.height(x, y)
        return baked is not None and baked - terrain_z(grid, x, y) > scale * HEIGHT_SCALE * 1.0
    route = None
    if route_path is not None:
        from scripts.pipeline.compile_route_walkability import _project_route
        route, _route_doc = _project_route(route_path, terrain_metadata_path)

    def project(point):
        return ((point[0] - projection['origin_easting']) * scale + projection['offset_x'],
                (point[1] - projection['origin_northing']) * scale + projection['offset_y'])

    def nearest_on_segment(point, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        denominator = dx * dx + dy * dy
        if denominator <= 1e-12:
            return a, 0.0
        t = max(0.0, min(1.0,
            ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / denominator))
        return (a[0] + t * dx, a[1] + t * dy), t

    def route_location(point):
        if not route or len(route) < 2:
            return None, None
        best = None
        progress = 0.0
        for a, b in zip(route, route[1:]):
            nearest, t = nearest_on_segment(point, a, b)
            segment = math.dist(a, b)
            candidate = (math.dist(point, nearest), progress + t * segment)
            if best is None or candidate[0] < best[0]:
                best = candidate
            progress += segment
        return best[1] / scale, best[0] / scale

    buildings = []
    road_segments = []
    for feature in store.features:
        if (feature['semantic_class'] == 'building_footprint'
                and feature['geometry']['kind'] == 'polygon'):
            ring = [project(point) for point in feature['geometry']['coordinates']]
            if len(ring) > 2:
                buildings.append((feature, ring))
        elif (feature['semantic_class'] == 'road'
              and feature['geometry']['kind'] == 'polyline'):
            line = [project(point) for point in feature['geometry']['coordinates']]
            road_segments.extend(zip(line, line[1:]))
    instances = []
    excluded = []
    # FL-4809 NUMABE-REPRESENT-01: rows for the shared mesh-place burial
    # check; parent rings are the same world-unit projections the anchor uses.
    mesh_place_rows: list[dict[str, Any]] = []
    parent_footprints: dict[str, list] = {}
    def emit_front(place, tags, label, building, ring, hosted, front_profile,
                   route_progress_m, route_offset_m, association,
                   association_distance_m, front_edge_selection, brow,
                   asserts, provenance_parent, fitted_width_m=None):
        (front_road_distance, _front_poi_distance, front_edge_index, a, b,
         nearest, yaw, outward, anchor, ground, edge_length_m) = hosted
        # Presentation normal follows the hosted yaw (already flipped above
        # so local -Y faces the street).
        local_front_normal = (math.sin(yaw), -math.cos(yaw))

        palette = {
            'body': tuple(front_profile['body_rgb']),
            'primary': tuple(front_profile['primary_rgb']),
            'accent': tuple(front_profile['accent_rgb']),
            'awning': tuple(front_profile['awning_rgb']),
            'window': tuple(front_profile.get('window_rgb', (45, 58, 62))),
            'door': tuple(front_profile.get('door_rgb', (37, 48, 52))),
            'label': tuple(front_profile.get('label_rgb', (239, 238, 224))),
        }
        palette_basis = front_profile['provenance']

        local_vertices: list[tuple] = []
        faces: list[list[int]] = []
        rendered_text = None
        if front_profile['geometry_family'] == 'place_entry_gate':
            width_m = fitted_width_m if fitted_width_m is not None else min(
                float(front_profile['max_width_m']),
                max(float(front_profile['min_width_m']),
                    edge_length_m * float(front_profile['edge_width_fraction'])))
            timber = tuple(front_profile['post_rgb'])
            roof = tuple(front_profile['roof_rgb'])
            stone = tuple(front_profile['base_rgb'])
            height_m = float(front_profile['height_m'])
            for side in (-1.0, 1.0):
                x = side * width_m * .42
                _add_box(local_vertices, faces,
                         (x - .14, x + .14, -.20, .20, 0, height_m), timber, scale)
                _add_box(local_vertices, faces,
                         (x - .25, x + .25, -.28, .28, 0, .22), stone, scale)
            _add_box(local_vertices, faces,
                     (-width_m / 2, width_m / 2, -.18, .18,
                      height_m - .45, height_m - .20), timber, scale)
            _add_box(local_vertices, faces,
                     (-width_m * .62, width_m * .62, -.48, .48,
                      height_m - .13, height_m + .08), roof, scale)
            _add_box(local_vertices, faces,
                     (-.48, .48, -.22, -.18,
                      height_m - 1.17, height_m - .67),
                     tuple(front_profile['panel_rgb']), scale)
            representation_kind = 'place_entry_gate_proposal'
        else:
            width_m = fitted_width_m if fitted_width_m is not None else min(
                float(front_profile['max_width_m']),
                max(2.6, edge_length_m * .68))
            # Author a complete readable PS1 shopfront rather than floating a
            # thin fascia over the generic fallback facade.  The identity and
            # category come from OSM; dimensions remain an explicit proposal.
            _ground = _commercial_front_ground_floor(
                local_vertices, faces, width_m=width_m, front_profile=front_profile,
                palette=palette, scale=scale, fallback_text=tags.get('name:en'))
            identity_sign = _ground['identity_sign']
            fascia_z_min, fascia_z_max = _ground['fascia_z_min'], _ground['fascia_z_max']
            label_z_min, label_z_max = _ground['label_z_min'], _ground['label_z_max']
            rendered_text = _ground['rendered_text']
            representation_kind = 'commercial_place_front_proposal'
        c, s = math.cos(yaw), math.sin(yaw)
        vertices = []
        for x, y, z, nx, ny, nz, r, g, blue, alpha in local_vertices:
            vertices.append((c * x - s * y, s * x + c * y, z,
                             c * nx - s * ny, s * nx + c * ny, nz,
                             r, g, blue, alpha))
        # FL-4809 NUMABE-REPRESENT-01: oriented world bbox of the authored
        # mesh (local x/y are world units about the anchor; z is metres).
        bbox_xs = [vertex[0] for vertex in local_vertices]
        bbox_ys = [vertex[1] for vertex in local_vertices]
        min_lx, max_lx = min(bbox_xs), max(bbox_xs)
        min_ly, max_ly = min(bbox_ys), max(bbox_ys)
        # _add_box scales every bound (including z) by the content scale, so
        # local z is metres * scale; divide back to report front_height_m.
        front_height_m = max(vertex[2] for vertex in local_vertices) / scale
        # FL-4876 SIGN-WALL-01: stand the front on the wall it faces. The
        # extruded wall is inset 0.8-1.8 m from the canonical face (FL-4814
        # bake rule) and every front floated that far out (build_reg_29,
        # 13/13). The shift is the deepest move toward the measured wall foot
        # at which the front's whole footprint (corners and edge midpoints,
        # the burial gate's own samples) is still off the baked mass, capped
        # inside spatial coherence's 1.5 m facade tolerance to the face.
        wall_shift_m = 0.0
        # FL-4876 FRONT-MASS-01: a building drawn by a mass mesh (meshes/mass_<id>.akm, baked before this stage)
        # shows its wall on the canonical face, not at the terrain foot. Shifting toward the foot buried a front
        # inside the mesh (Shiba Shinkin Bank on way/289194874: shift -1.45 m, front 0.75 m inside the wall), and
        # not shifting left every front 0.70 m out (build_reg_85). The front's back goes onto the face itself.
        _src = str(building['feature_id']).split(':', 2)[-1].replace('/', '_')
        _mass_drawn = (Path(meshes_out) / ('mass_%s.akm' % _src)).is_file()
        if _mass_drawn:
            _face_d = ((anchor[0] - a[0]) * outward[0] + (anchor[1] - a[1]) * outward[1]) / scale
            if _face_d > 0.0:
                wall_shift_m = -min(_face_d, FRONT_WALL_SHIFT_MAX_M)
                anchor = (anchor[0] + outward[0] * wall_shift_m * scale,
                          anchor[1] + outward[1] * wall_shift_m * scale)
                ground = terrain_z(grid, anchor[0], anchor[1])
        if wall_terrain is not None and not _mass_drawn:
            _hit = wall_foot(wall_terrain, anchor[0], anchor[1], outward, scale)
            if _hit is not None and _hit['foot_m'] < 0.0:
                _local = [(min_lx, min_ly), (max_lx, min_ly), (max_lx, max_ly), (min_lx, max_ly)]
                _local += [((ax + bx) / 2.0, (ay + by) / 2.0) for (ax, ay), (bx, by)
                           in zip(_local, _local[1:] + _local[:1])]
                _target = max(_hit['foot_m'], -FRONT_WALL_SHIFT_MAX_M)
                _k = int(round(-_target / 0.05))
                while _k > 0:
                    _d = -_k * 0.05
                    _ax = anchor[0] + outward[0] * _d * scale
                    _ay = anchor[1] + outward[1] * _d * scale
                    if not any(_raised_at(_ax + c * lx - s * ly, _ay + s * lx + c * ly)
                               for lx, ly in _local):
                        wall_shift_m = _d
                        break
                    _k -= 1
                if wall_shift_m:
                    anchor = (anchor[0] + outward[0] * wall_shift_m * scale,
                              anchor[1] + outward[1] * wall_shift_m * scale)
                    ground = terrain_z(grid, anchor[0], anchor[1])
        world_polygon = [
            [anchor[0] + c * min_lx - s * min_ly,
             anchor[1] + s * min_lx + c * min_ly],
            [anchor[0] + c * max_lx - s * min_ly,
             anchor[1] + s * max_lx + c * min_ly],
            [anchor[0] + c * max_lx - s * max_ly,
             anchor[1] + s * max_lx + c * max_ly],
            [anchor[0] + c * min_lx - s * max_ly,
             anchor[1] + s * min_lx + c * max_ly],
        ]
        parent_footprints[building['feature_id']] = ring
        mesh_place_rows.append({
            'feature_id': place['feature_id'],
            'label': label,
            'parent_id': building['feature_id'],
            'world_polygon': world_polygon,
            'front_height_m': front_height_m,
            'resolved_profile_id': front_profile['id'],
        })
        name = 'place_front_' + place['feature_id'].replace(':', '_').replace('/', '_')
        mesh = name + '.akm'
        meshes_out.mkdir(parents=True, exist_ok=True)
        write_akm(meshes_out / mesh, vertices, faces,
                  comment='FL-4814 semantic place front proposal; presentation only')
        instances.append({
            'feature_id': place['feature_id'],
            'parent_building_feature_id': building['feature_id'],
            'inst_name': name,
            'mesh_name': mesh,
            'transform': [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, HEIGHT_SCALE, 0,
                          anchor[0], anchor[1], float(round(ground, 3)), 1],
            'flags': 3,
            'story_id': -1,
            'label': label,
            'representation_kind': representation_kind,
            'resolved_profile_id': front_profile['id'],
            'resolved_profile_pack': front_profile['pack_id'],
            'resolved_visual_traits': list(front_profile.get('visual_traits', [])),
            **({} if brow is None else {
                'representation_id': brow.get('representation_id'),
                'representation_kind': brow.get('representation_kind'),
                'asserts_physical_presence': asserts,
                'asset_match': brow.get('asset_match'),
            }),
            'category': next((f'{key}={tags[key]}' for key in
                              ('shop', 'amenity', 'healthcare', 'office', 'craft')
                              if tags.get(key)), 'place'),
            'association': association,
            'association_distance_m': association_distance_m,
            'front_edge_index': front_edge_index,
            'front_edge_selection': front_edge_selection,
            'fitted_width_m': (round(fitted_width_m, 3) if fitted_width_m is not None else None),
            'front_road_distance_m': round(front_road_distance / scale, 3),
            'front_anchor_world_xy': [round(anchor[0], 6), round(anchor[1], 6)],
            'front_wall_shift_m': round(wall_shift_m, 3),
            'front_outward_normal_xy': [round(outward[0], 9), round(outward[1], 9)],
            'front_presentation_normal_xy': [
                round(local_front_normal[0], 9), round(local_front_normal[1], 9)
            ],
            'front_yaw_rad': round(yaw, 9),
            'route_progress_m':(round(route_progress_m,3) if route_progress_m is not None else None),
            'route_offset_m':(round(route_offset_m,3) if route_offset_m is not None else None),
            'rendered_text':rendered_text,
            'identity_sign': ({
                'fascia_height_m': round(fascia_z_max - fascia_z_min, 3),
                'label_height_m': round(label_z_max - label_z_min, 3),
                'label_width_fraction': float(identity_sign.get('label_width_fraction', .96)),
                # Flat band face, metres in front of the anchor; a logo plane sits 0.06 m beyond it.
                **({'fascia_face_o_m': _ground['fascia_face_o_m']}
                   if (front_profile.get('ground_floor') or {}).get('fascia') else {}),
            } if front_profile['geometry_family'] != 'place_entry_gate' else None),
            'proposal_status': 'synthesized_candidate_pending_source_sign_review',
            'provenance': {
                'identity': 'measured OSM place name/category',
                'parent': provenance_parent,
                'geometry': front_profile['provenance'],
                'appearance': palette_basis,
                'text': ('OSM name:en rendered with synthesized 5x7 block glyphs'
                         if rendered_text else 'not rendered; no Latin source label'),
                'ground': 'compiled DEM',
                'collision': 'none; presentation only',
            },
        })

    plan_skipped: list[dict[str, Any]] = []
    plan_consumed = 0
    plan_fitted: list[dict[str, Any]] = []
    if plan_rows is not None:
        from scripts.pipeline.canonical_attachment import CanonicalIndex, resolve
        if canonical_doc is None:
            raise ValueError("plan-driven fronts need the canonical document")
        plan_index = CanonicalIndex(canonical_doc)
        plan_features = {f["feature_id"]: f for f in store.features}
        # FRONT-01: every baked building mass as a world-frame ring for the
        # panel fit test (host included: corners must clear the host ring or
        # the burial gate fails the emission). Rings come from the canonical
        # features through the same project() as the anchor.
        mass_rings = []
        for _bf in store.features:
            if _bf.get("semantic_class") == "building_footprint" and _bf.get(
                    "geometry", {}).get("kind") == "polygon":
                _pts = [project(point) for point in _bf["geometry"]["coordinates"]]
                mass_rings.append((str(_bf["feature_id"]),
                                   _pts if _pts[0] == _pts[-1] else [*_pts, _pts[0]]))
        by_feature: dict[str, list[dict[str, Any]]] = {}
        for prow in plan_rows:
            if prow.get("owner_stage") not in ("place_fronts", None):
                raise ValueError(f"fronts owner file holds {prow.get('owner_stage')}: {prow.get('feature_id')}")
            by_feature.setdefault(str(prow.get("feature_id")), []).append(prow)
        # FL-4876 SLOT-OWNER-01. Two places mapped to one building are two
        # tenancies, not two street shopfronts on one slot: a face's street
        # slot has exactly one owner. A ground-floor storefront is emitted
        # only for a tenancy whose OSM level is 0 or absent and whose slot is
        # free; shop= tenancies claim slots first. A tenancy with an upper OSM
        # level, or whose slot is already owned, emits no storefront: its
        # storey is not resolved here, so it is recorded as debt rather than
        # guessed.
        def _osm_level(fid):
            raw = str(_source_tags(plan_features.get(fid) or {}, observations).get("level") or "")
            try:
                return max(0, int(float(raw.split(";")[0])))
            except ValueError:
                return None

        def _tenancy_order(fid):
            t = _source_tags(plan_features.get(fid) or {}, observations)
            lvl = _osm_level(fid)
            return (lvl if lvl is not None else 0, 0 if t.get("shop") else 1, fid)
        ground_slots: dict[tuple, list[tuple]] = {}
        for fid in sorted(by_feature, key=_tenancy_order):
            rows = by_feature[fid]
            emitting = [r for r in rows if r.get("attachment_state") == "resolved"
                        and (r.get("evidence_complete", True)
                             or r.get("representation_kind") == "synthetic_identity_proxy")
                        and r.get("disposition") == "mesh_facade" and r.get("attachment")]
            if not emitting:
                for r in rows:
                    plan_skipped.append({
                        "feature_id": fid,
                        "representation_id": r.get("representation_id"),
                        "attachment_state": r.get("attachment_state"),
                        "evidence_complete": r.get("evidence_complete", True),
                        "disposition": r.get("disposition"),
                        "reason": r.get("reason") or "plan row not emittable",
                    })
                continue
            for r in rows:
                if r.get("disposition") == "observation_owed":
                    plan_skipped.append({
                        "feature_id": fid,
                        "representation_id": r.get("representation_id"),
                        "attachment_state": r.get("attachment_state"),
                        "evidence_complete": r.get("evidence_complete", True),
                        "disposition": r.get("disposition"),
                        "reason": (r.get("reason") or "observation owed")
                                  + "; superseded by explicit synthetic proxy",
                    })
            brow = next((r for r in sorted(emitting, key=lambda r: str(r.get("representation_id")))
                         if not r.get("primary", True)), emitting[0])
            if brow.get("owner_stage") not in ("place_fronts", None):
                raise ValueError(f"front row has owner {brow.get('owner_stage')}: {fid}")
            place = plan_features.get(fid)
            if place is None:
                raise ValueError(f"plan fronts row has no canonical feature: {fid}")
            tags = _source_tags(place, observations)
            label = tags.get("name:en") or tags.get("name")
            if not label:
                plan_skipped.append({"feature_id": fid,
                                     "representation_id": brow.get("representation_id"),
                                     "reason": "unnamed place"})
                continue
            attachment = brow["attachment"]
            if attachment.get("anchor_kind") != "building_face":
                raise ValueError(f"front attachment is not a building face: {fid}")
            pose = resolve(attachment, plan_index)
            anchor = project((pose["E"], pose["N"]))
            yaw = math.radians(float(pose["yaw_deg"]))
            bfid = attachment["canonical_building_id"]
            building = plan_features.get(bfid)
            if building is None or building.get("geometry", {}).get("kind") != "polygon":
                raise ValueError(f"plan front parent is not a canonical ring: {bfid}")
            ring = [project(point) for point in building["geometry"]["coordinates"]]
            closed = ring if ring[0] == ring[-1] else [*ring, ring[0]]
            fi = int(attachment["face_index"])
            if not 0 <= fi < len(closed) - 1:
                raise ValueError(f"plan face index out of range: {fid} f{fi}")
            a, b = closed[fi], closed[fi + 1]
            edge_length_m = math.dist(a, b) / scale
            # FRONT-01: the anchor sits on its host face by definition; burial
            # is anchor-inside-host (the Tamagawa gate), never panel overlap
            # with the host. No minimum face length: the fit test arbitrates.
            if _point_to_ring_signed_distance(anchor, closed) < -1e-9:
                raise ValueError(f"plan front anchor buried in host mass: {fid} {bfid}")
            nearest = anchor
            # Mesh convention needs edge-parallel yaw (width along local X,
            # glazing on local -Y); the plan pose yaw is the outward normal.
            # Keep the plan outward, fit the mesh frame to it.
            pose_outward = (math.cos(yaw), math.sin(yaw))
            yaw = math.atan2(b[1] - a[1], b[0] - a[0])
            if (math.sin(yaw) * pose_outward[0]
                    - math.cos(yaw) * pose_outward[1]) < 0.0:
                yaw += math.pi
            outward = pose_outward
            ground = terrain_z(grid, *anchor)
            if ground is None:
                raise ValueError(f"plan-sited front lies outside compiled terrain: {fid}")
            front_road_distance = min(
                (math.dist(anchor, nearest_on_segment(anchor, ra, rb)[0])
                 for ra, rb in road_segments),
                default=float("inf"),
            )
            route_progress_m, route_offset_m = route_location(anchor)
            host_face = brow.get("host_face") or {}
            brand_identity = ' '.join(filter(None, (
                tags.get('brand'), tags.get('name:en'), tags.get('name'), tags.get('shop'),
            )))
            commercial_category = next((f'{key}={tags[key]}' for key in
                                        ('shop', 'amenity', 'healthcare', 'office', 'craft')
                                        if tags.get(key)), 'unclassified')
            front_profile = resolve_data_profile(
                collection='place_front_profiles',
                attributes={'brand_identity': brand_identity,
                            'commercial_category': commercial_category,
                            'amenity': tags.get('amenity'),
                            'religion': tags.get('religion')},
                packs=packs,
            )
            if front_profile is None:
                raise ValueError('locale pack chain supplies no place_front_profiles fallback')
            # FL-4809 FRONT-01: the plan owns the anchor and yaw. Fit the
            # authored width without moving that pose or weakening burial.
            gate_family = front_profile['geometry_family'] == 'place_entry_gate'
            place_cell_world = 1.0 / MESH_PLACE_CELLS_PER_WORLD
            min_width_m = float(front_profile.get('min_width_m', 1.0))
            if gate_family:
                full_width_m = min(float(front_profile['max_width_m']),
                                   max(float(front_profile['min_width_m']),
                                       edge_length_m * float(front_profile['edge_width_fraction'])))
            else:
                # FRONT-01: width from the profile's min..max band.
                _min_w = front_profile.get('min_width_m') or 2.6
                full_width_m = min(float(front_profile['max_width_m']),
                                   max(float(_min_w), edge_length_m * .68))
            # Host contact is valid, but a panel spanning a concave corner
            # still has to satisfy the existing mesh burial check.
            front_conflict = {"fid": None, "dist": float("inf")}
            def probe_clears(width_m: float) -> bool:
                if gate_family:
                    hx = max(0.62 * width_m, 0.42 * width_m + 0.25, 0.48) * scale
                    ly0, ly1 = -0.48 * scale, 0.48 * scale
                else:
                    hx = width_m / 2 * scale
                    ly0, ly1 = -0.58 * scale, 0.07 * scale
                pc, ps = math.cos(yaw), math.sin(yaw)
                corners = [
                    [anchor[0] + pc * lx - ps * ly, anchor[1] + ps * lx + pc * ly]
                    for lx, ly in ((-hx, ly0), (hx, ly0), (hx, ly1), (-hx, ly1))]
                samples = list(corners)
                for (pax, pay), (pbx, pby) in zip(corners, corners[1:] + corners[:1]):
                    samples.append(((pax + pbx) / 2.0, (pay + pby) / 2.0))
                # FRONT-01: the fit tests every baked mass INCLUDING the host:
                # a straight panel across a turning corner must shrink until
                # its corners clear the host ring too, else the burial gate
                # fails it after emission. Anchor-inside-host stays a raise.
                worst = float("inf")
                worst_fid = None
                for mid, mring in mass_rings:
                    for point in samples:
                        d = _point_to_ring_signed_distance(point, mring)
                        if d < worst:
                            worst = d
                            worst_fid = mid
                if worst < front_conflict["dist"]:
                    front_conflict.update(fid=worst_fid, dist=worst)
                return worst / place_cell_world - 1.0 >= 0
            fitted_width_m = full_width_m
            while not probe_clears(fitted_width_m):
                if fitted_width_m <= min_width_m:
                    break
                fitted_width_m = max(min_width_m, round(fitted_width_m - 0.2, 3))
            else:
                front_conflict["fitted"] = True
            if not front_conflict.get("fitted"):
                # FRONT-01: truly unfittable is a named state with the
                # conflicting mass id, never a silent skip.
                plan_skipped.append({"feature_id": fid,
                                     "representation_id": brow.get("representation_id"),
                                     "state": "front_unfittable",
                                     "conflicting_mass_id": front_conflict["fid"],
                                     "host_id": bfid, "face_index": fi,
                                     "route_distance_m": route_offset_m,
                                     "outward_offset_m": attachment.get("outward_offset_m"),
                                     "minimum_width_m": min_width_m,
                                     "reason": "front_unfittable: no width clears baked masses"})
                continue
            if fitted_width_m != full_width_m:
                plan_fitted.append({"feature_id": fid,
                                    "representation_id": brow.get("representation_id"),
                                    "conflicting_mass_id": front_conflict["fid"],
                                    "host_id": bfid, "face_index": fi,
                                    "route_distance_m": route_offset_m,
                                    "outward_offset_m": attachment.get("outward_offset_m"),
                                    "full_width_m": round(full_width_m, 3),
                                    "fitted_width_m": round(fitted_width_m, 3)})
            _flen = math.dist(a, b) or 1e-9
            _along = (((anchor[0] - a[0]) * (b[0] - a[0]) + (anchor[1] - a[1]) * (b[1] - a[1]))
                      / _flen) / scale
            _lvl = _osm_level(fid)
            _owner = next((o for c0, w0, o in ground_slots.get((bfid, fi), [])
                           if abs(_along - c0) < (fitted_width_m + w0) / 2.0), None)
            if (_lvl or 0) > 0 or _owner is not None:
                plan_skipped.append({
                    "feature_id": fid, "representation_id": brow.get("representation_id"),
                    "state": "storefront_slot_not_owned",
                    "osm_level": _lvl, "slot_owner": _owner, "host_id": bfid, "face_index": fi,
                    "reason": ("OSM level %s is not the street storey" % _lvl if (_lvl or 0) > 0
                               else "street slot on this face is owned by %s; this tenancy's "
                                    "storey is unresolved, so no storefront is guessed" % _owner)})
                continue
            ground_slots.setdefault((bfid, fi), []).append((_along, fitted_width_m, fid))
            emit_front(place, tags, label, building, ring,
                       (front_road_distance, 0.0, fi, a, b, nearest, yaw,
                        outward, anchor, ground, edge_length_m), front_profile,
                       route_progress_m, route_offset_m, "plan_building_face",
                       host_face.get("distance_m"),
                       "plan_face_index", brow,
                       bool(brow.get("asserts_physical_presence", True)),
                       "plan attachment building_face resolved against canonical "
                       + str(brow.get("source_bridge")), fitted_width_m)
            plan_consumed += 1
        for place in sorted(store.features, key=lambda row: row["feature_id"]):
            if place.get("semantic_class") != "place" or place.get("geometry", {}).get("kind") != "point":
                continue
            if place["feature_id"] not in by_feature:
                excluded.append({"feature_id": place["feature_id"],
                                 "reason": "no plan row for owner_stage place_fronts"})
    for place in ([] if plan_rows is not None else sorted(store.features, key=lambda row: row['feature_id'])):
        if place['semantic_class'] != 'place' or place['geometry']['kind'] != 'point':
            continue
        tags = _source_tags(place, observations)
        if tags.get('public_transport'):
            # FL-4809 NUMABE-REPRESENT-02: station/stop places are owned by
            # the fixture station-sign lowerer, not a storefront. Record the
            # routing explicitly instead of dropping them silently.
            excluded.append({
                'feature_id': place['feature_id'],
                'reason': 'station/stop place is owned by the fixture station-sign lowerer, not a storefront',
            })
            continue
        if tags.get('place') or tags.get('historic') == 'memorial':
            excluded.append({
                'feature_id': place['feature_id'],
                'reason': 'named locality/memorial is a minimap landmark, not a storefront',
            })
            continue
        label = tags.get('name:en') or tags.get('name')
        if not label:
            excluded.append({'feature_id': place['feature_id'], 'reason': 'unnamed place'})
            continue
        place_point = project(place['geometry']['coordinates'])
        route_progress_m, route_offset_m = route_location(place_point)
        best = None
        for building, ring in buildings:
            inside = point_in_polygon(place_point[0], place_point[1], ring)
            centroid = (sum(point[0] for point in ring[:-1] or ring) / len(ring[:-1] or ring),
                        sum(point[1] for point in ring[:-1] or ring) / len(ring[:-1] or ring))
            closed = ring if ring[0] == ring[-1] else [*ring, ring[0]]
            for a, b in zip(closed, closed[1:]):
                nearest, _t = nearest_on_segment(place_point, a, b)
                distance = math.dist(place_point, nearest)
                candidate = (0 if inside else 1, distance, building, a, b, nearest, centroid)
                if best is None or candidate[:2] < best[:2]:
                    best = candidate
        if best is None or (best[0] and best[1] > 12.0 * scale):
            excluded.append({'feature_id': place['feature_id'],
                             'reason': 'no containing or nearby parent building edge'})
            continue
        outside_rank, distance, building, _nearest_a, _nearest_b, _nearest, centroid = best
        # A POI node identifies the tenancy, but its position inside a large or
        # irregular building does not identify the public storefront edge.
        # The previous nearest-POI-edge rule can put corner tenants on a side
        # or rear wall. Prefer the parent edge facing the nearest mapped
        # street, then use POI distance only as a tie-breaker.  Source-derived
        # facade evidence may replace this proposal later.
        ring = next(ring for candidate, ring in buildings
                    if candidate['feature_id'] == building['feature_id'])
        closed = ring if ring[0] == ring[-1] else [*ring, ring[0]]
        street_edges = []
        for edge_index, (edge_a, edge_b) in enumerate(zip(closed, closed[1:])):
            edge_length = math.dist(edge_a, edge_b)
            if edge_length < .8 * scale:
                continue
            midpoint = ((edge_a[0] + edge_b[0]) / 2,
                        (edge_a[1] + edge_b[1]) / 2)
            road_distance = min(
                (math.dist(midpoint, nearest_on_segment(midpoint, ra, rb)[0])
                 for ra, rb in road_segments),
                default=float('inf'),
            )
            poi_distance = math.dist(place_point, midpoint)
            street_edges.append((road_distance, poi_distance, edge_index,
                                 edge_a, edge_b, midpoint))
        if not street_edges:
            excluded.append({'feature_id': place['feature_id'],
                             'reason': 'parent has no usable storefront edge'})
            continue
        brand_identity = ' '.join(filter(None, (
            tags.get('brand'), tags.get('name:en'), tags.get('name'), tags.get('shop'),
        )))
        commercial_category = next((f'{key}={tags[key]}' for key in
                                    ('shop', 'amenity', 'healthcare', 'office', 'craft')
                                    if tags.get(key)), 'unclassified')
        front_profile = resolve_data_profile(
            collection='place_front_profiles',
            attributes={'brand_identity': brand_identity,
                        'commercial_category': commercial_category,
                        'amenity': tags.get('amenity'),
                        'religion': tags.get('religion')},
            packs=packs,
        )
        if front_profile is None:
            raise ValueError('locale pack chain supplies no place_front_profiles fallback')
        # FL-4809 BUILD06FIX-01: the burying consumer is not a stale ring.
        # Bake, anchor, and clearance all read the same canonical ring; the
        # PLATEAU densification splits the OSM host edge into sub-metre
        # fragments and the first-ranked fragment can sit in a concave jag
        # where the fixed-width front overhangs laterally into the mass
        # (parent way/1507403794: OSM edge 2 hosts at +2.123 cells while the
        # PLATEAU edge 3 buries at -1.155). Try the ranked parent edges in
        # order and host on the first whose authored-size polygon clears the
        # ring by the same one-cell measure the burial gate enforces below.
        # Ranking is unchanged, so fronts that clear keep their edge; only
        # burying fronts move. The gate still fails closed on disagreement.
        gate_family = front_profile['geometry_family'] == 'place_entry_gate'
        place_cell_world = 1.0 / MESH_PLACE_CELLS_PER_WORLD
        outward_offset_m = float(ROAD_CLASS_DEFAULTS["place_front_outward_offset_m"][0])
        hosted = None
        for (_rank_road, _rank_poi, edge_index, a, b, nearest) in sorted(
                street_edges, key=lambda row: row[:3]):
            edge_length_m = math.dist(a, b) / scale
            if edge_length_m < .8:
                continue
            yaw = math.atan2(b[1] - a[1], b[0] - a[0])
            outward = (nearest[0] - centroid[0], nearest[1] - centroid[1])
            norm = math.hypot(*outward)
            if norm <= 1e-9:
                outward = (-math.sin(yaw), math.cos(yaw))
            else:
                outward = (outward[0] / norm, outward[1] / norm)
            # The shopfront geometry presents its sign/glazing on local -Y.
            # An OSM polygon may use either winding, so orient that face
            # toward the exterior explicitly instead of assuming the edge
            # direction. The rejected FL-4809 Classic review exposed fronts
            # that were present in A3D but edge-on or facing through their
            # parent building.
            if ((math.sin(yaw)) * outward[0]
                    + (-math.cos(yaw)) * outward[1]) < 0.0:
                yaw += math.pi
            anchor = (nearest[0] + outward[0] * outward_offset_m * scale,
                      nearest[1] + outward[1] * outward_offset_m * scale)
            ground = terrain_z(grid, *anchor)
            if ground is None:
                continue
            # Probe polygon mirrors the authored mesh bbox below (gate roof
            # overhang, commercial awning/rear); the burial gate re-checks
            # the authored polygon, so any drift here fails closed there.
            if gate_family:
                probe_width_m = min(float(front_profile['max_width_m']),
                                    max(float(front_profile['min_width_m']),
                                        edge_length_m * float(front_profile['edge_width_fraction'])))
                probe_hx = max(0.62 * probe_width_m, 0.42 * probe_width_m + 0.25,
                               0.48) * scale
                probe_ly0, probe_ly1 = -0.48 * scale, 0.48 * scale
            else:
                probe_width_m = min(float(front_profile['max_width_m']),
                                    max(2.6, edge_length_m * .68))
                probe_hx = probe_width_m / 2 * scale
                probe_ly0, probe_ly1 = -0.58 * scale, 0.07 * scale
            pc, ps = math.cos(yaw), math.sin(yaw)
            probe_corners = [
                [anchor[0] + pc * lx - ps * ly, anchor[1] + ps * lx + pc * ly]
                for lx, ly in ((-probe_hx, probe_ly0), (probe_hx, probe_ly0),
                               (probe_hx, probe_ly1), (-probe_hx, probe_ly1))]
            probe_samples = list(probe_corners)
            for (pax, pay), (pbx, pby) in zip(probe_corners, probe_corners[1:] + probe_corners[:1]):
                probe_samples.append(((pax + pbx) / 2.0, (pay + pby) / 2.0))
            probe_clearance = round(
                min(_point_to_ring_signed_distance(point, ring) for point in probe_samples)
                / place_cell_world - 1.0, 3)
            if probe_clearance < 0:
                continue
            hosted = (_rank_road, _rank_poi, edge_index, a, b, nearest,
                      yaw, outward, anchor, ground, edge_length_m)
            break
        if hosted is None:
            excluded.append({'feature_id': place['feature_id'],
                             'reason': 'no parent edge hosts the front clear of the baked mass'})
            continue
        association = ('containing_building' if outside_rank == 0
                         else 'nearest_building')
        association_distance_m = round(distance / scale, 3)
        emit_front(place, tags, label, building, ring, hosted, front_profile,
                   route_progress_m, route_offset_m, association,
                   association_distance_m,
                   'nearest_mapped_road_then_poi_tiebreak', None, True,
                   'derived nearest parent building facade edge')

    # FL-4809 NUMABE-REPRESENT-01: burial gate.  Every mesh-placed front
    # whose parent is a terrain-owned mass gets representation "mesh-place"
    # and a signed clearance in cells against the parent footprint dilated by
    # one cell.  Parent heights come from the building-mass instances when
    # the caller passes them; without them the debt list stays empty.
    parent_heights_m = None
    if building_instances_path is not None:
        parent_heights_m = {
            row['feature_id']: float(row['roof_height_m'])
            for row in json.loads(Path(building_instances_path).read_text())
            if row.get('roof_height_m') is not None
        }
    offset_entry = ROAD_CLASS_DEFAULTS["place_front_outward_offset_m"]
    representation_rows, buried_count, representation_debt = (
        annotate_mesh_place_instances(
            mesh_place_rows,
            parent_footprints=parent_footprints,
            parent_heights_m=parent_heights_m,
            raised_at=_raised_at if wall_terrain is not None else None,
        )
    )
    clearance_by_id = {
        row['feature_id']: (row['representation'], row['clearance_cells'])
        for row in mesh_place_rows
    }
    for row in instances:
        row['representation'], row['clearance_cells'] = clearance_by_id[row['feature_id']]
    if buried_count > 0:
        buried_ids = [item['feature_id'] for item in representation_rows if item['buried']]
        raise ValueError(
            f'place-front burial: {buried_count} mesh-placed fronts lie inside '
            f'the baked parent mass (clearance_cells < 0): {sorted(buried_ids)}'
        )

    embedded = False
    if embed_map:
        editor = _load_a3d_edit()
        pre, version, existing, start, gens, markers = editor.read_a3d_sections(map_path)
        existing_names = {row.inst_name for row in existing}
        duplicates = [row['inst_name'] for row in instances if row['inst_name'] in existing_names]
        if duplicates:
            raise ValueError(f'place-front instances already embedded: {duplicates}')
        additions = [editor.a3d_format.A3DInstance(
            mesh_name=row['mesh_name'], inst_name=row['inst_name'], transform=row['transform'],
            flags=row['flags'], story_id=row['story_id'],
        ) for row in instances]
        editor.write_a3d_sections(map_path, pre, version, [*existing, *additions], start, gens, markers)
        embedded = True
    if embed_map:
        actual_names = {row.inst_name for row in editor.read_a3d_sections(map_path)[2]}
        if any(row['inst_name'] not in actual_names for row in instances):
            raise ValueError('place-front instances missing after embed')
    if any(not (meshes_out / row['mesh_name']).is_file()
           or not (meshes_out / row['mesh_name']).stat().st_size for row in instances):
        raise ValueError('place-front mesh artifact attestation failed')
    route_place_sequence = [
        {'feature_id':row['feature_id'],'label':row['label'],
         'representation_kind':row['representation_kind'],
         'route_progress_m':row['route_progress_m'],'route_offset_m':row['route_offset_m']}
        for row in sorted(instances,key=lambda item:(
            item['route_progress_m'] is None,
            item['route_progress_m'] if item['route_progress_m'] is not None else 0,
            item['feature_id']))
        if row['route_progress_m'] is not None
    ]
    required_audit = []
    unmet = []
    if requirements_path is not None:
        requirements = json.loads(requirements_path.read_text(encoding='utf-8'))
        required_rows = requirements.get('required_resolved_archetypes', {}).get(
            'place_fronts', []
        )
        # FRONT-01: evaluate every required place, list each with its state,
        # and fail closed at the end naming every unmet one (no short-circuit:
        # a later required place must still be evaluated and reported).
        skip_by_fid = {}
        for _sk in plan_skipped:
            skip_by_fid.setdefault(str(_sk.get("feature_id")), []).append(_sk)
        for required in required_rows:
            _rfid = str(required['feature_id'])
            matches = [row for row in instances
                       if row['feature_id'] == _rfid]
            if len(matches) != 1:
                _why = "; ".join(
                    str(_sk.get("reason", _sk.get("state", "skipped")))
                    for _sk in skip_by_fid.get(_rfid, [])) or (
                    "no plan row for required place" if plan_rows is not None
                    and _rfid not in by_feature else "no instance emitted")
                required_audit.append({
                    'feature_id': required['feature_id'],
                    'profile_id': required.get('profile_id'),
                    'passed': False,
                    'state': _why,
                })
                unmet.append(_rfid)
                continue
            row = matches[0]
            if row['resolved_profile_id'] != required['profile_id']:
                required_audit.append({
                    'feature_id': required['feature_id'],
                    'profile_id': required.get('profile_id'),
                    'passed': False,
                    'state': "profile drift: %s" % row['resolved_profile_id'],
                })
                unmet.append(_rfid)
                continue
            expected_traits = set(required.get('visual_traits', []))
            actual_traits = set(row['resolved_visual_traits'])
            if not expected_traits.issubset(actual_traits):
                required_audit.append({
                    'feature_id': required['feature_id'],
                    'profile_id': required.get('profile_id'),
                    'passed': False,
                    'state': "traits missing: %s" % sorted(expected_traits - actual_traits),
                })
                unmet.append(_rfid)
                continue
            required_audit.append({
                'feature_id': required['feature_id'],
                'profile_id': required['profile_id'],
                'visual_traits': sorted(expected_traits),
                'passed': True,
                'state': 'emitted',
            })
    receipt = {
        'schema': 'y92.place_front_lowering.v1',
        'stage': 'executed_artifact_attested',
        'places_considered': sum(
            feature['semantic_class'] == 'place' for feature in store.features),
        'place_fronts_emitted': len(instances),
        'commercial_place_fronts_emitted':sum(
            row['representation_kind']=='commercial_place_front_proposal' for row in instances),
        'place_entry_gate_proposals':sum(
            row['representation_kind']=='place_entry_gate_proposal' for row in instances),
        'place_identity_labels_emitted':sum(bool(row['rendered_text']) for row in instances),
        'counts': {
            'place_fronts_emitted': len(instances),
            'buried': buried_count,
            'map_embedded': embedded,
        },
        'plan': {
            'consumed': plan_consumed if plan_rows is not None else None,
            'built': len(instances) if plan_rows is not None else None,
            'skipped_by_state': plan_skipped if plan_rows is not None else None,
            'width_fitted': plan_fitted if plan_rows is not None else None,
        },
        'representation': {
            'rule_source': ('scripts/pipeline/compile_rules_resolved.py:'
                            'ROAD_CLASS_DEFAULTS[place_front_outward_offset_m]'),
            'offset_fact': {'value': float(offset_entry[0]),
                            'unit': offset_entry[1], 'source': offset_entry[2]},
            'per_instance': representation_rows,
            'debt': representation_debt,
        },
        'route_place_sequence':route_place_sequence,
        'required_resolved_archetype_audit': required_audit,
        'instances': instances,
        'excluded': excluded,
        'not_claimed': ('source-derived sign layout/typeface, exact storefront dimensions, '
                        'Japanese text where no supported source label exists, collision authority'),
    }
    receipt_out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    # FL-4876. The producer writes the emission-join file itself, in the same
    # execution that emits the instances, so the emission manifest never has
    # to reconstruct this owner's output from a receipt a later stage might
    # not read.  Written before the unmet raise: a refused run still leaves
    # the instances it did emit on disk as evidence.
    if instances_out is not None:
        instances_out = Path(instances_out)
        instances_out.parent.mkdir(parents=True, exist_ok=True)
        instances_out.write_text(
            json.dumps(instances, ensure_ascii=False, indent=2,
                       sort_keys=True) + '\n',
            encoding='utf-8')
    if unmet:
        raise ValueError(
            "required place-front archetypes unmet: %s" % ", ".join(sorted(unmet)))
    return receipt




def compile_world_ir_infrastructure(*, world_ir_path: Path, terrain_metadata_path: Path,
        ground_map_path: Path, map_path: Path, meshes_out: Path, receipt_out: Path,
        locale_context: dict, template_root: Path,
        plan_crossing_rows: list[dict[str, Any]] | None = None,
        canonical_doc: dict[str, Any] | None = None,
        embed_map: bool = True,
        rail_profiles_path: Path | None = None) -> dict:
    """FL-4814: lower mapped linear infrastructure through the shared AKM owner.

    Track gauge comes from source tags. Ballast/tie dimensions are explicit
    presentation approximations. Elevated/tunnel ways remain unresolved until
    a vertical alignment exists; ground-level rails must not stand in for them.
    """
    store = WorldIRStore.load(world_ir_path)
    store.assert_valid()
    observations = {r['observation_id']:r for r in store.observations}
    packs = discover_pack_chain(template_root, locale_context)
    rail_profile = resolve_data_profile(
        collection='rail_infrastructure_profiles', attributes={}, packs=packs,
    )
    crossing_profile = resolve_data_profile(
        collection='railway_crossing_profiles', attributes={}, packs=packs,
    )
    station_profile = resolve_data_profile(
        collection='station_composition_profiles', attributes={}, packs=packs,
    )
    station_identity_candidates = resolve_template_candidates(
        semantic_candidates=['station_identity_sign'], context=locale_context, packs=packs,
    )
    station_identity_template = (
        station_identity_candidates[0] if station_identity_candidates else None
    )
    metadata=json.loads(terrain_metadata_path.read_text())
    projection=metadata.get('projection',metadata)
    scale=float(projection['content_scale'])
    grid=load_height_grid(ground_map_path)
    def project(point):
        return ((point[0]-projection['origin_easting'])*scale+projection['offset_x'],
                (point[1]-projection['origin_northing'])*scale+projection['offset_y'])
    # FL-4876 RAIL-VERTICAL-01: measured track-bed elevations (LiDAR heights
    # only; OSM keeps the centreline) for structure-tagged rail. Orthometric
    # metres map to terrain units through the DEM lowering's own datum.
    rail_profiles = {}
    raw_of_m = None
    if rail_profiles_path is not None and Path(rail_profiles_path).is_file():
        rail_profiles = json.loads(Path(rail_profiles_path).read_text(encoding='utf-8')).get('ways', {})
        _dem = Path(ground_map_path).parent / 'dem_lowering_receipt.json'
        if _dem.is_file():
            _el = json.loads(_dem.read_text(encoding='utf-8'))['elevation']
            _raw0, _dat = float(_el['compiled_raw_min']), float(_el['local_bake_datum_m'])
            raw_of_m = lambda z_m: _raw0 + (z_m - _dat) * scale * HEIGHT_SCALE
    def _is_structure(tags):
        return (tags.get('bridge') not in (None, 'no') or tags.get('embankment') == 'yes'
                or str(tags.get('layer') or '0') not in ('0', ''))
    _structure_ends = set()
    for _f in store.features:
        if _f.get('semantic_class') == 'railway_boundary' and _f['geometry']['kind'] == 'polyline':
            if _is_structure(_source_tags(_f, observations)):
                for _p in (_f['geometry']['coordinates'][0], _f['geometry']['coordinates'][-1]):
                    _structure_ends.add((round(_p[0]), round(_p[1])))
    def bed_profile(feature_id, tags, coords=None):
        """chainage(m) -> terrain-unit track height, or None (DEM rules).

        Structure-tagged rail follows the measured bed throughout. Untagged
        rail that shares an end with a structure follows it only along the
        contiguous elevated run (bed >= 0.5 m above LiDAR ground) from that
        shared end -- the ramp down off the structure -- and the DEM beyond.
        """
        prof = rail_profiles.get(str(feature_id).rsplit('/', 1)[-1])
        if prof is None or raw_of_m is None:
            return None
        lo, hi = None, None
        if not _is_structure(tags):
            if not coords:
                return None
            st = [s for s in prof['stations'] if s[3] is not None and s[4] is not None]
            start_hit = (round(coords[0][0]), round(coords[0][1])) in _structure_ends
            end_hit = (round(coords[-1][0]), round(coords[-1][1])) in _structure_ends
            if not (start_hit or end_hit) or not st:
                return None
            if start_hit:
                run = [s for s in st]
                k = next((i for i, s in enumerate(run) if s[3] - s[4] < 0.5), len(run))
                lo, hi = 0.0, run[k - 1][0] if k else 0.0
            else:
                run = list(reversed(st))
                k = next((i for i, s in enumerate(run) if s[3] - s[4] < 0.5), len(run))
                lo, hi = (run[k - 1][0] if k else prof['length_m']), prof['length_m']
            if hi - lo < 2.0:
                return None
        meas = [(s[0], s[3]) for s in prof['stations'] if s[3] is not None
                and (lo is None or lo <= s[0] <= hi)]
        if len(meas) < 2:
            return None
        zs = [z for _, z in meas]
        sm = [sorted(zs[max(0, i - 2):i + 3])[len(zs[max(0, i - 2):i + 3]) // 2] for i in range(len(zs))]
        ss = [s for s, _ in meas]
        def at(ch_m):
            if lo is not None and not (lo <= ch_m <= hi):
                return None
            if ch_m <= ss[0]: return raw_of_m(sm[0])
            if ch_m >= ss[-1]: return raw_of_m(sm[-1])
            for k in range(1, len(ss)):
                if ch_m <= ss[k]:
                    t = (ch_m - ss[k - 1]) / ((ss[k] - ss[k - 1]) or 1.0)
                    return raw_of_m(sm[k - 1] + (sm[k] - sm[k - 1]) * t)
        return at
    instances=[]; excluded=[]
    for feature in sorted(store.features,key=lambda r:r['feature_id']):
        kind=feature['semantic_class']
        # FL-4809 NUMABE-INFRA-01: kind filter before the tag lookup. The 83
        # plateau-only building_footprint features carry no OSM source tags;
        # resolving tags first killed the stage on canonical input.
        if kind not in {'railway_boundary','wall','fence','retaining_wall','guardrail','hedge','tree','terrain'}:
            continue
        if not any(((observations.get(oid) or {}).get('attributes') or {}).get('tags')
                   for oid in feature.get('supporting_observation_ids', [])):
            excluded.append({'feature_id':feature['feature_id'],'semantic_class':kind,
                             'reason':'consumed infrastructure kind carries no source tags; intentionally not rendered'})
            continue
        tags=_source_tags(feature,observations)
        is_watercourse = kind == 'terrain' and bool(tags.get('waterway'))
        if (kind not in {'railway_boundary','wall','fence','retaining_wall','guardrail','hedge','tree'}
                and not is_watercourse):
            continue
        geom=feature['geometry']
        if kind=='railway_boundary' and (tags.get('railway')!='rail' or geom['kind']!='polyline'):
            excluded.append({'feature_id':feature['feature_id'],'reason':'rail land polygon is not track centreline'})
            continue
        is_bridge = tags.get('bridge') not in (None, 'no')
        is_tunnel = tags.get('tunnel') not in (None, 'no')
        if is_tunnel:
            excluded.append({'feature_id':feature['feature_id'],
                             'semantic_class':kind,
                             'reason':'source marks tunnel; intentionally not rendered above ground'})
            continue
        if is_bridge and kind != 'railway_boundary':
            excluded.append({'feature_id':feature['feature_id'],
                             'reason':'unsupported non-rail bridge presentation'})
            continue
        if geom['kind'] not in {'polyline','polygon'}:
            excluded.append({'feature_id':feature['feature_id'],'reason':'unsupported linear geometry'})
            continue
        points=[project(p) for p in geom['coordinates']]
        if geom['kind']=='polygon' and points[0]!=points[-1]: points.append(points[0])
        ox,oy=points[0]
        vertices=[];faces=[];segments=0;ties=0;bridge_piers=0
        gauge=None
        if kind=='railway_boundary':
            fallback=(rail_profile or {}).get('default_gauge_mm')
            raw_gauge=tags.get('gauge',fallback)
            if raw_gauge is None:
                excluded.append({'feature_id':feature['feature_id'],'reason':'unknown source and locale gauge'})
                continue
            gauge=float(raw_gauge)/1000
        if gauge is not None and not 0.5<=gauge<=2: raise ValueError('unsupported rail gauge')
        def box_at(x,y,yaw,bounds,rgb,ground):
            v=[];f=[];_add_box(v,f,bounds,rgb,scale)
            start=len(vertices);c,s=math.cos(yaw),math.sin(yaw)
            for vx,vy,vz,nx,ny,nz,r,g,b,a in v:
                vertices.append((x-ox+c*vx-s*vy,y-oy+s*vx+c*vy,
                    ground/HEIGHT_SCALE+vz,c*nx-s*ny,s*nx+c*ny,nz,r,g,b,a))
            faces.extend([[start+i for i in face] for face in f])
        # FL-4876 RAIL-TRACK-01. A bridge deck used to sit at the DEM under
        # each sample plus a flat 6 m clearance prior. The DEM already holds
        # the embankment the bridge joins (Shinkansen way/796878138 ends at
        # 5.94 / 5.96 m), so every bridge stood about 6 m above the track on
        # either side. The deck now runs straight between the DEM heights of
        # its own two end points (the abutments), so the track is continuous.
        bed = (bed_profile(feature['feature_id'], tags, geom.get('coordinates'))
               if kind == 'railway_boundary' else None)
        is_embankment = tags.get('embankment') == 'yes'
        deck_ends = None
        if is_bridge and kind == 'railway_boundary':
            _z0 = terrain_z(grid, *points[0]); _z1 = terrain_z(grid, *points[-1])
            if _z0 is not None and _z1 is not None:
                deck_ends = (_z0, _z1)
        total_len = sum(math.dist(p, q) for p, q in zip(points, points[1:])) or 1.0
        run_s = 0.0
        for a,b in zip(points,points[1:]):
            length=math.dist(a,b)/scale
            if length<=1e-6: continue
            yaw=math.atan2(b[1]-a[1],b[0]-a[0])
            steps=max(1,math.ceil(length/RAIL_SLEEPER_PITCH_M))
            for i in range(steps):
                t=(i+.5)/steps;x=a[0]+t*(b[0]-a[0]);y=a[1]+t*(b[1]-a[1])
                ground=terrain_z(grid,x,y)
                if ground is None: continue
                base_ground = ground
                _bz = bed((run_s + t * math.dist(a, b)) / scale) if bed is not None else None
                if _bz is not None:
                    # RAIL-VERTICAL-01: measured track bed (never below DEM).
                    ground = max(ground, _bz)
                elif is_bridge and deck_ends is not None:
                    u = (run_s + t * math.dist(a, b)) / total_len
                    ground = max(ground, deck_ends[0] + (deck_ends[1] - deck_ends[0]) * u)
                elif is_bridge:
                    # One abutment outside the compiled terrain: keep the
                    # locale clearance prior (presentation only).
                    bridge_clearance_m = float((rail_profile or {}).get(
                        'bridge_clearance_m', 0.0))
                    if bridge_clearance_m <= 0:
                        raise ValueError('bridge rail requires a resolved positive clearance prior')
                    ground += bridge_clearance_m * scale * HEIGHT_SCALE
                half=length/steps/2
                if kind=='railway_boundary':
                    # RAIL-TRACK-01 colours are exact terminal-cube values
                    # (write_akm snaps to 51 steps): the old sleeper brown
                    # (91,78,63) snapped to olive (102,102,51) and the old
                    # ballast and rails both to (102,102,102). Concrete (PC)
                    # sleepers on grey ballast with dark rails; the rails are
                    # widened to 0.16 m so a terminal cell can show them.
                    box_at(x,y,yaw,(-half,half,-1.5,1.5,0,.12),RAIL_BALLAST_RGB,ground)
                    box_at(x,y,yaw,(-.12,.12,-gauge/2-.45,gauge/2+.45,.12,.22),RAIL_SLEEPER_RGB,ground)
                    for side in (-1,1):
                        centre=side*gauge/2
                        box_at(x,y,yaw,(-half,half,centre-.08,centre+.08,.22,.36),RAIL_STEEL_RGB,ground)
                    ties+=1
                    if is_embankment and ground - base_ground > scale * HEIGHT_SCALE:
                        # RAIL-VERTICAL-01: an embankment is solid under the bed.
                        box_at(x,y,yaw,(-half,half,-2.6,2.6,0,(ground-base_ground)/(scale*HEIGHT_SCALE)),
                               (112,104,88),base_ground)
                    if (is_bridge and ground - base_ground > scale * HEIGHT_SCALE
                            and i % max(1, round(12.0 / (length / steps))) == 0):
                        pier_top_m = (ground - base_ground) / (scale * HEIGHT_SCALE)
                        box_at(x,y,yaw,(-.34,.34,-1.25,1.25,0,pier_top_m),
                               (153,153,153),base_ground)
                        bridge_piers += 1
                elif is_watercourse:
                    # Surface paint already owns the visible water.  Add only
                    # shallow stone/concrete edge structure, leaving channel
                    # depth and collision untouched until width evidence is
                    # reviewed. Culverts were excluded above.
                    for side in (-1.0, 1.0):
                        bank = side * .62
                        box_at(x,y,yaw,(-half,half,bank-.11,bank+.11,0,.34),
                               (122,120,111),ground)
                elif kind == 'tree':
                    # A tree-row centreline is measured.  Crown form and
                    # spacing are a replaceable presentation proposal.
                    if i % max(1, round(4.5 / (length / steps))) == 0:
                        box_at(x,y,yaw,(-.11,.11,-.11,.11,0,2.1),(91,70,52),ground)
                        box_at(x,y,yaw,(-.82,.82,-.82,.82,1.55,3.45),(67,105,58),ground)
                else:
                    height=1.2;thick=.12
                    rgb=(138,135,121)
                    if kind=='hedge':height=1;thick=.4;rgb=(73,103,58)
                    if kind in {'fence','guardrail'}:
                        # JP roadside boundary fallback: light tubular rails
                        # with strong readable posts.  Source tags own the
                        # boundary location; exact manufacturer remains a
                        # locale-specific presentation approximation.
                        rgb=(218,221,214)
                        box_at(x,y,yaw,(-.055,.055,-.055,.055,0,height),rgb,ground)
                        for z in (.45,.95):
                            box_at(x,y,yaw,(-half,half,-.055,.055,z,z+.095),rgb,ground)
                        box_at(x,y,yaw,(-.075,.075,-.075,-.057,.56,.70),
                               (210,139,41),ground)
                    else:
                        box_at(x,y,yaw,(-half,half,-thick,thick,0,height),rgb,ground)
                segments+=1
            run_s += math.dist(a, b)
        if not faces:
            excluded.append({'feature_id':feature['feature_id'],'reason':'no in-map linear segments'})
            continue
        name='infrastructure_'+feature['feature_id'].replace(':','_').replace('/','_')
        mesh=name+'.akm';meshes_out.mkdir(parents=True,exist_ok=True)
        write_akm(meshes_out/mesh,vertices,faces,comment='FL-4814 mapped infrastructure; synthesized presentation; alpha=255')
        instances.append({'feature_id':feature['feature_id'],'inst_name':name,'mesh_name':mesh,
            'transform':[1,0,0,0,0,1,0,0,0,0,HEIGHT_SCALE,0,ox,oy,0,1],
            'flags':3,'story_id':-1,'semantic_class':kind,'segments':segments,'sleepers':ties,
            'gauge_m':gauge,'gauge_provenance':('source tag' if tags.get('gauge') else 'synthesized locale fallback'),'provenance':{'path':'canonical World IR','geometry':'synthesized PS1 dimensions',
            'ground':'compiled DEM','vertical_alignment':(
                ('measured LiDAR track bed (rail_vertical_profiles.v1, '
                 'RAIL-VERTICAL-01); OSM structure tag %s'
                 % ('bridge' if is_bridge else 'embankment' if is_embankment
                    else 'layer' if _is_structure(tags)
                    else 'none; elevated ramp from the adjacent structure end only'))
                if bed is not None else
                ('OSM bridge=yes; deck straight between the DEM heights of '
                 'its two end points (RAIL-TRACK-01)')
                if is_bridge and deck_ends is not None else
                'OSM bridge=yes + resolved locale rail-clearance prior'
                if is_bridge else 'ground-level DEM'),
            'collision':'none; presentation only'},
            'proposal_status':(
                'synthesized_bridge_height_pending_source_review' if is_bridge
                else 'synthesized_watercourse_width_pending_source_review' if is_watercourse
                else None),
            'watercourse':is_watercourse,
            'bridge_clearance_m':(
                float((rail_profile or {}).get('bridge_clearance_m'))
                if is_bridge and rail_profile else None),
            'resolved_profile_id': (rail_profile or {}).get('id'),
            'resolved_profile_pack': (rail_profile or {}).get('pack_id'),
            'bridge_piers':bridge_piers})

    # OSM carries station, stop-position, entrance and crossing observations,
    # points, but no platform polygon or crossing-apparatus geometry.  Emit a
    # deliberately cheap station grammar from those semantic priors.  These
    # products remain presentation-only proposals until image evidence reviews
    # their dimensions and placement; they must never masquerade as measured
    # platform or barrier geometry.
    feature_rows = []
    rail_polylines: list[list[tuple[float, float]]] = []
    station_rail_rows: list[tuple[dict, list[tuple[float, float]]]] = []
    for feature in sorted(store.features, key=lambda row: row['feature_id']):
        # FL-4809 NUMABE-INFRA-01: tags resolve only for the kinds the station
        # grammar consumes; tagless plateau buildings flow through with empty
        # tags instead of raising.
        if feature['semantic_class'] in {'place', 'station_entrance', 'crossing', 'railway_boundary'}:
            if not any(((observations.get(oid) or {}).get('attributes') or {}).get('tags')
                       for oid in feature.get('supporting_observation_ids', [])):
                excluded.append({'feature_id': feature['feature_id'],
                                 'semantic_class': feature['semantic_class'],
                                 'reason': 'consumed infrastructure kind carries no source tags; intentionally not rendered'})
                continue
            tags = _source_tags(feature, observations)
        else:
            tags = {}
        geom = feature['geometry']
        projected = None
        if geom['kind'] == 'point':
            projected = project(geom['coordinates'])
        feature_rows.append((feature, tags, projected))
        if (feature['semantic_class'] == 'railway_boundary'
                and tags.get('railway') == 'rail'
                and geom['kind'] == 'polyline'
                and tags.get('bridge') in (None, 'no')
                and tags.get('tunnel') in (None, 'no')):
            rail_line = [project(p) for p in geom['coordinates']]
            rail_polylines.append(rail_line)
            station_rail_rows.append((feature, rail_line))

    def write_proposal(*, feature_id, semantic_class, name, anchor,
                       parts, evidence, dimensions):
        vertices: list[tuple] = []
        faces: list[list[int]] = []
        ax, ay = anchor
        for part in parts:
            x, y, yaw, bounds, rgb = part
            local_vertices: list[tuple] = []
            local_faces: list[list[int]] = []
            _add_box(local_vertices, local_faces, bounds, rgb, scale)
            ground = terrain_z(grid, x, y)
            if ground is None:
                continue
            c, s = math.cos(yaw), math.sin(yaw)
            start = len(vertices)
            for vx, vy, vz, nx, ny, nz, r, g, b, a in local_vertices:
                vertices.append((
                    x - ax + c * vx - s * vy,
                    y - ay + s * vx + c * vy,
                    ground / HEIGHT_SCALE + vz,
                    c * nx - s * ny,
                    s * nx + c * ny,
                    nz, r, g, b, a,
                ))
            faces.extend([[start + index for index in face] for face in local_faces])
        if not faces:
            excluded.append({'feature_id': feature_id, 'reason': f'{semantic_class} is outside terrain'})
            return
        mesh = name + '.akm'
        meshes_out.mkdir(parents=True, exist_ok=True)
        write_akm(
            meshes_out / mesh,
            vertices,
            faces,
            comment='FL-4814 station grammar proposal; synthesized presentation; alpha=255',
        )
        instances.append({
            'feature_id': feature_id,
            'inst_name': name,
            'mesh_name': mesh,
            'transform': [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, HEIGHT_SCALE, 0, ax, ay, 0, 1],
            'flags': 3,
            'story_id': -1,
            'semantic_class': semantic_class,
            'segments': len(parts),
            'sleepers': 0,
            'gauge_m': None,
            'gauge_provenance': None,
            'proposal_status': 'synthesized_candidate_pending_source_review',
            'evidence_feature_ids': evidence,
            'dimensions': dimensions,
            'provenance': {
                'path': 'canonical World IR semantic place/rail/crossing priors',
                'geometry': 'resolved locale station-composition proposal',
                'ground': 'compiled DEM',
                'collision': 'none; presentation only',
            },
        })

    station_places = [row for row in feature_rows
                      if row[0]['semantic_class'] == 'place'
                      and row[1].get('public_transport') == 'station'
                      and row[2] is not None]
    if station_places and station_profile is None:
        excluded.append({
            'feature_id': station_places[0][0]['feature_id'],
            'reason': 'compatible locale packs supply no station composition profile',
        })
        station_places = []
    stop_positions = [row for row in feature_rows
                      if row[0]['semantic_class'] == 'place'
                      and row[1].get('public_transport') == 'stop_position'
                      and row[2] is not None]
    for station, station_tags, station_point in station_places:
        same_station_stops = [row for row in stop_positions
                              if (row[1].get('name') == station_tags.get('name')
                                  or row[1].get('ref') == station_tags.get('ref'))]
        if not same_station_stops or not rail_polylines:
            excluded.append({'feature_id': station['feature_id'],
                             'reason': 'station platform proposal lacks stop positions or ground rail'})
            continue
        stop_centroid = (
            sum(row[2][0] for row in same_station_stops) / len(same_station_stops),
            sum(row[2][1] for row in same_station_stops) / len(same_station_stops),
        )
        parts = []
        accepted_stop_ids = []
        platform_length_m = float(station_profile['platform_length_m'])
        platform_width_m = float(station_profile['platform_width_m'])
        platform_height_m = float(station_profile['platform_height_m'])
        canopy_height_m = float(station_profile['canopy_height_m'])
        for stop, _stop_tags, stop_point in same_station_stops:
            frame = _nearest_polyline_frame(stop_point, rail_polylines)
            if frame is None or frame[2] > 4.0 * scale:
                continue
            rail_point, yaw, _distance = frame
            normal = (-math.sin(yaw), math.cos(yaw))
            side = 1.0 if ((stop_point[0] - stop_centroid[0]) * normal[0]
                           + (stop_point[1] - stop_centroid[1]) * normal[1]) >= 0 else -1.0
            # Two visibly raised side-platform strips, split into short
            # sections so their DEM grounding follows the local grade.  The
            # prior .04-.22 m slab read as ordinary road paint in Classic;
            # a small-station platform must present a clear retaining face and
            # tactile edge above the rail bed.
            for along_m in range(-int(platform_length_m // 2) + 1,
                                 int(platform_length_m // 2), 3):
                centre = (
                    rail_point[0] + math.cos(yaw) * along_m * scale + normal[0] * side * 1.85 * scale,
                    rail_point[1] + math.sin(yaw) * along_m * scale + normal[1] * side * 1.85 * scale,
                )
                half_width = platform_width_m / 2.0
                parts.append((*centre, yaw, (-1.55, 1.55, -half_width, half_width,
                                             .04, platform_height_m-.12), (142, 139, 132)))
                parts.append((*centre, yaw, (-1.55, 1.55, -half_width, half_width,
                                             platform_height_m-.12, platform_height_m), (177, 174, 163)))
                tactile_centre = (
                    rail_point[0] + math.cos(yaw) * along_m * scale + normal[0] * side * .74 * scale,
                    rail_point[1] + math.sin(yaw) * along_m * scale + normal[1] * side * .74 * scale,
                )
                parts.append((*tactile_centre, yaw, (-1.55, 1.55, -.14, .14,
                                                     platform_height_m,
                                                     platform_height_m+.045), (211, 173, 42)))
            # Cheap canopy rhythm makes the platform read as station
            # infrastructure rather than a long raised curb.
            for along_m in range(-18, 19, 9):
                centre = (
                    rail_point[0] + math.cos(yaw) * along_m * scale + normal[0] * side * 2.25 * scale,
                    rail_point[1] + math.sin(yaw) * along_m * scale + normal[1] * side * 2.25 * scale,
                )
                parts.append((*centre, yaw, (-4.35, 4.35, -.92, .92,
                                             canopy_height_m-.17,
                                             canopy_height_m), (104, 108, 108)))
                for post_x in (-3.7, 3.7):
                    parts.append((*centre, yaw, (post_x-.07, post_x+.07, -.60, -.46,
                                                 platform_height_m-.12,
                                                 canopy_height_m-.14), (119, 123, 121)))
            accepted_stop_ids.append(stop['feature_id'])
        if parts:
            write_proposal(
                feature_id=station['feature_id'],
                semantic_class='station_platform_proposal',
                name='infrastructure_platform_' + station['feature_id'].replace(':', '_').replace('/', '_'),
                anchor=station_point,
                parts=parts,
                evidence=[station['feature_id'], *accepted_stop_ids],
                dimensions={'platform_length_m': platform_length_m,
                            'platform_width_m': platform_width_m,
                            'platform_height_m': platform_height_m,
                            'canopy_height_m': canopy_height_m,
                            'platform_count': len(accepted_stop_ids),
                            'resolved_profile_id': station_profile['id'],
                            'resolved_profile_pack': station_profile['pack_id'],
                            'basis': station_profile['provenance']},
            )

        # Give each mapped entrance a coherent portal/canopy/stair proposal.
        # The accepted source-derived panel remains a child appearance layer;
        # this macro assembly supplies the missing station silhouette.
        station_entrances = [row for row in feature_rows
                             if row[0]['semantic_class'] == 'station_entrance'
                             and row[2] is not None
                             and math.dist(row[2], station_point) <= 30.0 * scale]
        for entrance, entrance_tags, entrance_point in station_entrances:
            frame = _nearest_polyline_frame(entrance_point, rail_polylines)
            if frame is None:
                continue
            rail_point, yaw, _distance = frame
            portal_parts = []
            portal_width_m = float(station_profile['entrance_portal_width_m'])
            portal_height_m = float(station_profile['entrance_portal_height_m'])
            for x_offset in (-portal_width_m*.418, portal_width_m*.418):
                portal_parts.append((*entrance_point, yaw,
                    (x_offset-.20, x_offset+.20, -.34, .34, 0, portal_height_m-.04), (195, 196, 188)))
            identity_parameters = dict(
                (station_identity_template or {}).get('parameters', {})
            )
            panel_rgb = tuple(identity_parameters.get('panel_rgb', [91, 96, 98]))
            trim_rgb = tuple(identity_parameters.get('trim_rgb', [220, 219, 208]))
            text_rgb = tuple(identity_parameters.get('text_rgb', [239, 238, 224]))
            station_identity = str(station_tags.get('name:en') or '').strip()
            if station_identity:
                station_identity += str(identity_parameters.get('label_suffix', ''))
            rendered_station_identity, identity_boxes = _block_text_boxes(
                station_identity,
                max_width_m=portal_width_m - .40,
                z_min_m=2.34,
                z_max_m=2.60,
                y_min_m=-.405,
                y_max_m=-.385,
            )
            portal_parts.extend([
                (*entrance_point, yaw, (-portal_width_m/2, portal_width_m/2, -.62, .70,
                                        portal_height_m-.34, portal_height_m), (91, 96, 98)),
                (*entrance_point, yaw, (-1.55, 1.55, .18, .28, .10, 2.72), (39, 52, 58)),
                (*entrance_point, yaw, (-2.05, 2.05, -.36, -.25, 2.28, 2.68), trim_rgb),
                (*entrance_point, yaw, (-1.95, 1.95, -.38, -.36, 2.34, 2.60), panel_rgb),
            ])
            portal_parts.extend(
                (*entrance_point, yaw, bounds, text_rgb) for bounds in identity_boxes
            )
            # Four broad steps rise toward the platform side.  They remain
            # presentation-only until the headed collision route is authored.
            for step in range(4):
                depth0 = .45 + step * .42
                portal_parts.append((*entrance_point, yaw,
                    (-1.55, 1.55, depth0, depth0+.44, .04, .18+.16*step),
                    (156, 154, 147)))
            write_proposal(
                feature_id=entrance['feature_id'],
                semantic_class='station_entrance_assembly_proposal',
                name='infrastructure_station_entrance_' + entrance['feature_id'].replace(':', '_').replace('/', '_'),
                anchor=entrance_point,
                parts=portal_parts,
                evidence=[station['feature_id'], entrance['feature_id']],
                dimensions={'entrance_ref': entrance_tags.get('ref'),
                            'portal_width_m': portal_width_m,
                            'portal_height_m': portal_height_m,
                            'rendered_identity_text': rendered_station_identity,
                            'identity_template_id': (
                                (station_identity_template or {}).get('template_id')
                            ),
                            'identity_template_pack': (
                                (station_identity_template or {}).get('pack_id')
                            ),
                            'resolved_profile_id': station_profile['id'],
                            'resolved_profile_pack': station_profile['pack_id'],
                            'basis': station_profile['provenance']},
            )

        # Build the visually dominant outer boundary along the station corridor.
        # Parallel track centrelines identify which side is outside; isolated
        # segments get both sides. The 110 m station radius is an art-direction
        # bound, not a claim that OSM mapped the real fence.
        fence_parts = []
        fence_source_ids = []
        fence_posts = 0
        seen_fence_cells = set()
        station_radius_m = float(station_profile['station_radius_m'])
        station_radius = station_radius_m * scale
        rail_boundary_offset_m = float(station_profile['rail_boundary_offset_m'])
        rail_boundary_height_m = float(station_profile['rail_boundary_height_m'])
        for rail_feature, line in station_rail_rows:
            other_lines = [candidate for candidate_feature, candidate in station_rail_rows
                           if candidate_feature['feature_id'] != rail_feature['feature_id']]
            contributed = False
            for a, b in zip(line, line[1:]):
                length_world = math.dist(a, b)
                if length_world <= 1e-6:
                    continue
                yaw = math.atan2(b[1] - a[1], b[0] - a[0])
                tangent = (math.cos(yaw), math.sin(yaw))
                normal = (-tangent[1], tangent[0])
                steps = max(1, math.ceil(length_world / (1.8 * scale)))
                for step in range(steps):
                    t = (step + .5) / steps
                    midpoint = (a[0] + (b[0] - a[0]) * t,
                                a[1] + (b[1] - a[1]) * t)
                    if math.dist(midpoint, station_point) > station_radius:
                        continue
                    nearest_other = _nearest_polyline_frame(midpoint, other_lines)
                    if nearest_other is not None and nearest_other[2] <= 10.0 * scale:
                        toward_other = ((nearest_other[0][0] - midpoint[0]) * normal[0]
                                        + (nearest_other[0][1] - midpoint[1]) * normal[1])
                        sides = (-1.0,) if toward_other >= 0 else (1.0,)
                    else:
                        sides = (-1.0, 1.0)
                    half_m = length_world / steps / scale / 2.0
                    for side in sides:
                        centre = (midpoint[0] + normal[0] * side * rail_boundary_offset_m * scale,
                                  midpoint[1] + normal[1] * side * rail_boundary_offset_m * scale)
                        key = (round(centre[0] / scale, 1), round(centre[1] / scale, 1))
                        if key in seen_fence_cells:
                            continue
                        seen_fence_cells.add(key)
                        fence_parts.append((*centre, yaw, (-.045, .045, -.045, .045,
                                                           0, rail_boundary_height_m),
                                            (103, 108, 103)))
                        for height in (.42, .92):
                            fence_parts.append((*centre, yaw,
                                                (-half_m, half_m, -.035, .035,
                                                 height, height + .075),
                                                (103, 108, 103)))
                        fence_posts += 1
                        contributed = True
            if contributed:
                fence_source_ids.append(rail_feature['feature_id'])
        if fence_parts:
            write_proposal(
                feature_id=station['feature_id'],
                semantic_class='rail_corridor_fence_proposal',
                name='infrastructure_rail_fence_' + station['feature_id'].replace(':', '_').replace('/', '_'),
                anchor=station_point,
                parts=fence_parts,
                evidence=[station['feature_id'], *fence_source_ids],
                dimensions={'station_radius_m': station_radius_m,
                            'fence_offset_from_track_m': rail_boundary_offset_m,
                            'fence_height_m': rail_boundary_height_m,
                            'post_count': fence_posts,
                            'resolved_profile_id': station_profile['id'],
                            'resolved_profile_pack': station_profile['pack_id'],
                            'basis': station_profile['provenance']},
            )

    railway_crossings = [row for row in feature_rows
                         if row[0]['semantic_class'] == 'crossing'
                         and row[1].get('railway') in {'level_crossing', 'crossing'}
                         and row[2] is not None]
    # FL-4809 NUMABE-REPRESENT-03: plan-driven clusters resolve each
    # grade-crossing row attachment (carrier_chainage on the rail carrier)
    # instead of the rail-frame lookup below (removed lookup:
    # _nearest_polyline_frame ground-rail search for cluster siting).
    plan_poses: dict[str, tuple[float, float, float]] = {}
    plan_built_fids: set[str] = set()
    plan_crossing_skipped: list[dict[str, Any]] = []
    plan_crossing_rows_used: list = []
    if plan_crossing_rows is not None:
        from scripts.pipeline.canonical_attachment import CanonicalIndex, resolve as resolve_attachment
        if canonical_doc is None:
            raise ValueError("plan-driven clusters need the canonical document")
        plan_index = CanonicalIndex(canonical_doc)
        plan_features = {f["feature_id"]: f for f in store.features}
        plan_project = project
        rebuilt = []
        for prow in sorted(plan_crossing_rows, key=lambda r: str(r.get("feature_id"))):
            pfid = str(prow.get("feature_id"))
            if not str(prow.get("asset_match") or "").endswith("railway_grade_crossing"):
                continue
            if prow.get("attachment_state") != "resolved" or not prow.get("attachment"):
                plan_crossing_skipped.append({
                    "feature_id": pfid,
                    "representation_id": prow.get("representation_id"),
                    "attachment_state": prow.get("attachment_state"),
                    "reason": prow.get("reason") or "plan row not resolved",
                })
                continue
            pfeature = plan_features.get(pfid)
            if pfeature is None:
                raise ValueError(f"plan cluster row has no canonical feature: {pfid}")
            ptags = _source_tags(pfeature, observations)
            ppose = resolve_attachment(prow["attachment"], plan_index)
            wx, wy = plan_project((ppose["E"], ppose["N"]))
            plan_poses[pfid] = (wx, wy, math.radians(float(ppose["yaw_deg"])))
            rebuilt.append((pfeature, ptags, (wx, wy), prow))
        railway_crossings = [(feat, tags, point) for feat, tags, point, _prow in rebuilt]
        plan_crossing_rows_used = rebuilt
    if railway_crossings and crossing_profile is None:
        for feature, _tags, _point in railway_crossings:
            excluded.append({
                'feature_id': feature['feature_id'],
                'reason': 'compatible locale packs supply no railway crossing profile',
            })
        railway_crossings = []
    crossing_radius_m = float((crossing_profile or {}).get('cluster_radius_m', 12.0))
    for group in _cluster_crossing_features(
            railway_crossings, radius_world=crossing_radius_m * scale):
        group_from_plan = bool(plan_poses) and all(row[0]["feature_id"] in plan_poses for row in group)
        if group_from_plan:
            for row in group:
                plan_built_fids.add(row[0]["feature_id"])
            pts = [plan_poses[row[0]["feature_id"]][:2] for row in group]
            yaws = [plan_poses[row[0]["feature_id"]][2] for row in group]
            centre = (sum(p[0] for p in pts) / len(pts),
                      sum(p[1] for p in pts) / len(pts))
            rail_yaw = math.atan2(sum(math.sin(y) for y in yaws) / len(yaws),
                                  sum(math.cos(y) for y in yaws) / len(yaws))
        else:
            source_points = [row[2] for row in group]
            centre = (
                sum(point[0] for point in source_points) / len(source_points),
                sum(point[1] for point in source_points) / len(source_points),
            )
            frame = _nearest_polyline_frame(centre, rail_polylines)
            if frame is None:
                excluded.append({'feature_id': group[0][0]['feature_id'],
                                 'reason': 'railway crossing proposal lacks nearby ground rail'})
                continue
            _rail_point, rail_yaw, distance = frame
            max_rail_distance_m = float(crossing_profile['max_rail_distance_m'])
            if distance > max_rail_distance_m * scale:
                excluded.append({'feature_id': group[0][0]['feature_id'],
                                 'reason': 'railway crossing proposal is too far from ground rail'})
                continue
        road_yaw = rail_yaw + math.pi / 2.0
        road_dir = (math.cos(road_yaw), math.sin(road_yaw))
        rail_dir = (math.cos(rail_yaw), math.sin(rail_yaw))
        parts = []
        for approach_side in (-1.0, 1.0):
            base = (
                centre[0] + road_dir[0] * approach_side
                * float(crossing_profile['approach_offset_m']) * scale,
                centre[1] + road_dir[1] * approach_side
                * float(crossing_profile['approach_offset_m']) * scale,
            )
            post_offset_m = float(crossing_profile['post_offset_m'])
            post = (base[0] - rail_dir[0] * post_offset_m * scale,
                    base[1] - rail_dir[1] * post_offset_m * scale)
            post_height_m = float(crossing_profile['post_height_m'])
            parts.append((*post, rail_yaw,
                          (-.09, .09, -.09, .09, 0, post_height_m),
                          tuple(crossing_profile['warning_rgb'])))
            parts.append((*post, rail_yaw,
                          (-.28, .28, -.16, .16,
                           float(crossing_profile['signal_box_min_m']),
                           float(crossing_profile['signal_box_max_m'])),
                          tuple(crossing_profile['dark_rgb'])))
            # Alternating black/yellow barrier-arm blocks preserve the crossing
            # silhouette without inventing source-specific machinery.
            arm_blocks = int(crossing_profile['barrier_arm_blocks'])
            arm_step_m = float(crossing_profile['barrier_arm_step_m'])
            arm_start_m = float(crossing_profile['barrier_arm_start_m'])
            for arm_index in range(arm_blocks):
                arm_centre = (
                    post[0] + rail_dir[0] * (arm_start_m + arm_index * arm_step_m) * scale,
                    post[1] + rail_dir[1] * (arm_start_m + arm_index * arm_step_m) * scale,
                )
                colour = (tuple(crossing_profile['warning_rgb']) if arm_index % 2 == 0
                          else tuple(crossing_profile['dark_rgb']))
                parts.append((*arm_centre, rail_yaw, (-.25, .25, -.055, .055, 1.02, 1.13), colour))
            for cross_angle in (-math.pi / 4.0, math.pi / 4.0):
                parts.append((*post, rail_yaw + cross_angle,
                              (-.46, .46, -.045, .045,
                               post_height_m - .22, post_height_m - .12),
                              tuple(crossing_profile['warning_rgb'])))
        parent = next((row[0] for row in group if row[1].get('railway') == 'level_crossing'), group[0][0])
        write_proposal(
            feature_id=parent['feature_id'],
            semantic_class='railway_crossing_equipment_proposal',
            name='infrastructure_crossing_' + parent['feature_id'].replace(':', '_').replace('/', '_'),
            anchor=centre,
            parts=parts,
            evidence=[row[0]['feature_id'] for row in group],
            dimensions={'approach_count': 2,
                        'barrier_arm_span_m': arm_blocks * arm_step_m,
                        'source_crossing_points': len(group),
                        'resolved_profile_id': crossing_profile['id'],
                        'resolved_profile_pack': crossing_profile['pack_id'],
                        'basis': crossing_profile['provenance']},
        )
    if plan_poses:
        plan_row_by_fid = {str(r.get("feature_id")): r for r in (plan_crossing_rows or [])}
        for inst in instances:
            if inst.get("feature_id") in plan_built_fids:
                prow = plan_row_by_fid.get(inst["feature_id"], {})
                inst["representation_id"] = prow.get("representation_id")
                inst["representation_kind"] = prow.get("representation_kind")
                inst["asserts_physical_presence"] = bool(prow.get("asserts_physical_presence", True))
                inst["asset_match"] = prow.get("asset_match")
    embedded = False
    if embed_map:
        editor=_load_a3d_edit()
        pre,version,existing,start,gens,markers=editor.read_a3d_sections(map_path)
        existing_names={r.inst_name for r in existing}
        if any(r['inst_name'] in existing_names for r in instances):raise ValueError('infrastructure already embedded')
        additions=[editor.a3d_format.A3DInstance(mesh_name=r['mesh_name'],inst_name=r['inst_name'],
            transform=r['transform'],flags=r['flags'],story_id=r['story_id']) for r in instances]
        editor.write_a3d_sections(map_path,pre,version,[*existing,*additions],start,gens,markers)
        embedded = True
    if embed_map:
        actual_names={r.inst_name for r in editor.read_a3d_sections(map_path)[2]}
        if any(r['inst_name'] not in actual_names or not (meshes_out/r['mesh_name']).stat().st_size for r in instances):
            raise ValueError('infrastructure artifact attestation failed')
    else:
        missing_meshes = sorted(
            mesh for mesh in set(r['mesh_name'] for r in instances)
            if not (meshes_out / mesh).is_file() or (meshes_out / mesh).stat().st_size == 0)
        if missing_meshes:
            raise ValueError(f'infrastructure meshes missing: {missing_meshes}')
    receipt={'schema':'y92.infrastructure_lowering.v1','instances':instances,'excluded':excluded,
        'mesh_instances_embedded':len(instances) if embedded else 0,'map_embedded':embedded,
        'plan':({'consumed':len(plan_crossing_rows_used) if plan_crossing_rows is not None else None,
                 'built_clusters':len(plan_built_fids) if plan_crossing_rows is not None else None,
                 'skipped_by_state':plan_crossing_skipped if plan_crossing_rows is not None else None}
                if plan_crossing_rows is not None else None),
        'platform_proposals':sum(r['semantic_class']=='station_platform_proposal' for r in instances),
        'station_entrance_proposals':sum(
            r['semantic_class']=='station_entrance_assembly_proposal' for r in instances),
        'station_identity_labels_emitted':sum(
            bool(r.get('dimensions', {}).get('rendered_identity_text'))
            for r in instances
            if r['semantic_class']=='station_entrance_assembly_proposal'),
        'crossing_equipment_proposals':sum(r['semantic_class']=='railway_crossing_equipment_proposal' for r in instances),
        'rail_fence_proposals':sum(r['semantic_class']=='rail_corridor_fence_proposal' for r in instances),
        'rail_fence_posts':sum(
            r.get('dimensions', {}).get('post_count', 0)
            for r in instances if r['semantic_class']=='rail_corridor_fence_proposal'),
        'bridge_track_proposals':sum(
            r.get('proposal_status')=='synthesized_bridge_height_pending_source_review'
            for r in instances),
        'bridge_piers':sum(r.get('bridge_piers',0) for r in instances),
        'tunnel_tracks_withheld':sum(
            row['reason']=='source marks tunnel; intentionally not rendered above ground'
            and row.get('semantic_class')=='railway_boundary'
            for row in excluded),
        'watercourse_edge_proposals':sum(r.get('watercourse') is True for r in instances),
        'watercourse_tunnels_withheld':sum(
            row['reason']=='source marks tunnel; intentionally not rendered above ground'
            and row.get('semantic_class')=='terrain'
            for row in excluded),
        'crossing_source_points_grouped':sum(
            len(r.get('evidence_feature_ids', []))
            for r in instances if r['semantic_class']=='railway_crossing_equipment_proposal'),
        'stage':'executed_artifact_attested','collision_accepted':False}
    receipt_out.write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
    return receipt
