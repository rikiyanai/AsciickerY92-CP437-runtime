#!/usr/bin/env python3
"""Compile the spatial-coherence receipt (FL-4809 SPATIAL-01, SPATIAL-04).

Proves surviving route-visible objects sit on their declared host: one row
per emitted route-visible object with planned vs emitted position,
host distance, attachment and orientation residuals, a collision state,
and a PASS/FAIL/UNRESOLVED verdict. Exit 1 on any FAIL.

Corridor, bins, class groups, and plan/emission joins reuse the route
streetscape inventory helpers (imported, never copied). Tolerances are
proposed DEFAULT facts in SPATIAL_DEFAULTS with rules provenance; no
numeric literal below is a tolerance. compile_rules_resolved.py is owned
by RULES-01 and left untouched; these rows are proposed for it.

SPATIAL-04: route binning is descriptive (out-of-corridor rows are kept
with an explicit route-scope state, never dropped); linear expected poses
resolve by placement attachment_id (population-plan placement, never the
single feature-level plan row); mass rings are canonical-closed in memory
and front face ownership is recorded; every emitted plan-owned instance
(fixtures, linear tiles and span records, population custom identities,
storefront fronts, station facade products, masses, decals) gets exactly one row or one explicit
contract-missing row.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline import geo_frames as GF  # noqa: E402
from scripts.pipeline.bake_osm_building_footprints import point_in_polygon  # noqa: E402
from scripts.pipeline.compile_route_streetscape_inventory import (  # noqa: E402
    DEFAULT_BIN_LENGTH_M,
    DEFAULT_ROUTE_VISIBLE_CORRIDOR_M,
    _decode_polyline6,
    _group_for,
    _plan_rows_by_feature,
    _sample_points,
)
from scripts.pipeline.canonical_attachment import (  # noqa: E402
    CanonicalIndex,
    _point_at_chainage,
    face_heading,
    resolve,
)

SCHEMA = "y92.spatial_coherence.v1"

# Proposed DEFAULT facts (FL-4809 SPATIAL-01). Each carries its provenance;
# none is a surveyed claim. Units are metres/degrees. Proposed for
# compile_rules_resolved.py, which is owned by RULES-01 and untouched here.
SPATIAL_DEFAULTS = {
    "freestanding_residual_m": (
        2.0, "proposed DEFAULT (FL-4809 SPATIAL-01; Mapillary-class fixity prior)"),
    "carrier_span_residual_m": (
        1.5, "proposed DEFAULT (FL-4809 SPATIAL-01; tile vs plan placement)"),
    "carrier_span_lateral_m": (
        2.0, "proposed DEFAULT (FL-4809 SPATIAL-01; tile off carrier centreline)"),
    "carrier_orientation_deg": (
        10.0, "proposed DEFAULT (FL-4809 SPATIAL-01; yaw vs plan run)"),
    "facade_edge_tolerance_m": (
        1.5, "proposed DEFAULT (FL-4809 SPATIAL-01; anchor vs declared face)"),
    "facade_orientation_deg": (
        10.0, "proposed DEFAULT (FL-4809 SPATIAL-01; front yaw vs face normal)"),
    "rail_half_width_m": (
        1.5, "proposed DEFAULT (FL-4809 SPATIAL-01; stamper railway width prior)"),
    "road_half_width_default_m": (
        1.75, "proposed DEFAULT (FL-4809 SPATIAL-01; narrow-street fallback)"),
    "dedup_chainage_step_m": (
        2.0, "proposed DEFAULT (FL-4809 SPATIAL-01; decal burial sample step)"),
    "freestanding_roadside_m": (
        5.0, "proposed DEFAULT (FL-4809 SPATIAL-01; roadside hardware offset bound)"),
    "sidewalk_half_width_m": (
        1.5, "proposed DEFAULT (FL-4809 SPATIAL-02; walkway centreline half-width)"),
}




def _yaw_diff_deg(have: float, want: float) -> float:
    return abs((have - want + 180.0) % 360.0 - 180.0)


def _lowered_yaw_reference(inst: dict, placement: dict | None, attachment: dict | None,
                           anchor: str, index) -> float | None:
    """The yaw lower_plan_linear actually emitted for a non-span linear tile.

    Orientation verifies the owner that lowering consumed, not a parallel copy.
    """
    if inst.get("run_record") and inst.get("evidence_kind") == "rule":
        # FL-4876 COHERENCE-ZERO-01 (Codex review debt on OBS-YAW-01):
        # lower_plan_linear emits logical pose["yaw_deg"] for run records,
        # and a run placement is one straight chord E0,N0 -> E1,N1, so the
        # emitted yaw is that chord (measured on build_reg_04: 57.15, -89.59,
        # -43.71 deg emitted = chord; resolve() gave 30.09, -76.37, -29.50).
        # resolve() remains the reference only when the run is absent.
        _run = (placement or {}).get("run") or {}
        if all(k in _run for k in ("E0", "N0", "E1", "N1")) and (
                _run["E0"], _run["N0"]) != (_run["E1"], _run["N1"]):
            return math.degrees(math.atan2(float(_run["N1"]) - float(_run["N0"]),
                                           float(_run["E1"]) - float(_run["E0"])))
        return float(resolve(attachment, index).get("yaw_deg") or 0.0)
    if (placement is not None and inst.get("evidence_kind") == "observation"
            and inst.get("logical_instance_kind") in ("unit", "point", "hardware")
            and anchor == "carrier_chainage"):
        # FL-4876 OBS-YAW-01. dispatch_population_plan resolves this
        # attachment (resolve_span -> resolve; the side flip there moves E/N
        # only and passes yaw through) into resolved_pose, the representation
        # contract copies it into the logical pose, and lower_plan_linear emits
        # that yaw. placement["yaw_deg"] is the planner's bearing of the
        # carrier the placement is FILED under, which can differ from the
        # attachment carrier (fence 0152: filed under footway way/1087430627
        # at 15.3 deg, attached to rail way/1038001322, emitted 129 deg).
        # Comparing against it reported 37 of 40 orientation failures in
        # build_road_01 that lowering never produced.
        return float(resolve(attachment, index).get("yaw_deg") or 0.0)
    if placement is not None and placement.get("yaw_deg") is not None:
        # Placements whose realisation keeps the planned run bearing:
        # points along a carrier_span run take the run chord yaw, which IS
        # placement["yaw_deg"] (OBS-YAW-01 regressed obs-inst-0077 when span
        # points were routed through resolve()), as do freestanding and other
        # unattached placements. Curved carriers and corner modules
        # legitimately differ from the local centreline tangent.
        return float(placement["yaw_deg"])
    return None


def _closed(ring: list) -> list:
    return ring if ring[0] == ring[-1] else [*ring, ring[0]]


def _dist_to_polyline(point: tuple[float, float], line: list) -> float:
    best = float("inf")
    for a, b in zip(line, line[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        denom = dx * dx + dy * dy
        if denom <= 1e-12:
            nearest = a
        else:
            t = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / denom))
            nearest = (a[0] + t * dx, a[1] + t * dy)
        best = min(best, math.dist(point, nearest))
    return best


def _dist_to_ring(point: tuple[float, float], ring: list) -> float:
    return _dist_to_polyline(point, _closed(ring))


def _inside_mass(point: tuple[float, float], rings: dict[str, list],
                 skip: set[str]) -> str | None:
    for fid, ring in rings.items():
        if fid in skip:
            continue
        if len(ring) < 3:
            continue
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        if not (min(xs) <= point[0] <= max(xs) and min(ys) <= point[1] <= max(ys)):
            continue
        if point_in_polygon(point[0], point[1], ring):
            return fid
    return None


def _segment_mass_hits(a: tuple[float, float], b: tuple[float, float],
                       rings: dict[str, list], skip: set[str]) -> list[str]:
    """Return every mass whose 2D footprint intersects segment ab."""
    def orient(p, q, r):
        return ((q[0] - p[0]) * (r[1] - p[1])
                - (q[1] - p[1]) * (r[0] - p[0]))

    def on_segment(p, q, r):
        return (min(p[0], q[0]) - 1e-9 <= r[0]
                <= max(p[0], q[0]) + 1e-9
                and min(p[1], q[1]) - 1e-9 <= r[1]
                <= max(p[1], q[1]) + 1e-9)

    def intersects(p, q, r, s):
        pqr, pqs = orient(p, q, r), orient(p, q, s)
        rsp, rsq = orient(r, s, p), orient(r, s, q)
        if ((pqr > 1e-9 and pqs < -1e-9
             or pqr < -1e-9 and pqs > 1e-9)
                and (rsp > 1e-9 and rsq < -1e-9
                     or rsp < -1e-9 and rsq > 1e-9)):
            return True
        return ((abs(pqr) <= 1e-9 and on_segment(p, q, r))
                or (abs(pqs) <= 1e-9 and on_segment(p, q, s))
                or (abs(rsp) <= 1e-9 and on_segment(r, s, p))
                or (abs(rsq) <= 1e-9 and on_segment(r, s, q)))

    hits = []
    for fid, ring in rings.items():
        if fid in skip or len(ring) < 3:
            continue
        closed = _closed(ring)
        if (point_in_polygon(a[0], a[1], closed)
                or point_in_polygon(b[0], b[1], closed)
                or any(intersects(a, b, p, q)
                       for p, q in zip(closed, closed[1:]))):
            hits.append(fid)
    return sorted(hits)


def _span_vertical_conflicts(
        mass_ids: list[str], span_min_height_m: float | None,
        mass_height_m: dict[str, float]) -> tuple[list[str], list[tuple[str, float]]]:
    """Partition crossed masses by actual vertical overlap, without tolerance."""
    conflicts = []
    clear = []
    for mass_id in mass_ids:
        roof_height = mass_height_m.get(mass_id)
        if (span_min_height_m is not None and roof_height is not None
                and float(span_min_height_m) > roof_height):
            clear.append((mass_id, roof_height))
        else:
            conflicts.append(mass_id)
    return conflicts, clear


def _disc_overlaps_mass(point: tuple[float, float], radius_world: float,
                        rings: dict[str, list]) -> str | None:
    """Exact-enough footprint test for the stamper's circular point dots."""
    hit = _inside_mass(point, rings, set())
    if hit is not None:
        return hit
    for fid, ring in rings.items():
        if len(ring) >= 3 and _dist_to_ring(point, ring) <= radius_world:
            return fid
    return None


def _attachment_host(attachment: dict | None) -> str | None:
    if not attachment:
        return None
    return (attachment.get("canonical_mass_id")
            or attachment.get("canonical_building_id")
            or attachment.get("canonical_carrier_id")
            or attachment.get("ground_owner")
            or attachment.get("nearest_carrier_id"))


def _placement_index(build_dir: Path):
    """Placement-unique attachments from the population plan.

    Returns (by_aid, by_pid, feature_aids, note): attachment_id ->
    placement record, placement_id -> placement record, referenced
    canonical feature id -> [attachment_ids]. Absent population plan ->
    empty maps with an explanatory note; linear rows then fall back
    through the recorded feature-level path, never silently.
    """
    pop_path = build_dir / "plan" / "population" / "population_plan.json"
    # FL-4876 ROADSIDE-SNAP-01: the representation-merged plan is the
    # emitted plan (population plus the representation fixture placements).
    # Read without it, every rep-fixture fell back to the feature-level
    # representation attachment, so a fixture moved off the carriageway
    # read as an attachment residual of exactly its move.
    _merged = pop_path.with_name("population_plan.representation_merged.json")
    if _merged.is_file():
        pop_path = _merged
    if not pop_path.is_file():
        return {}, {}, {}, f"population plan absent: {pop_path}"
    doc = json.loads(pop_path.read_text(encoding="utf-8"))
    by_aid: dict[str, dict] = {}
    by_pid: dict[str, dict] = {}
    feature_aids: dict[str, list] = {}
    n = 0
    for carrier in doc.get("carriers", []):
        for placement in carrier.get("placements", []):
            n += 1
            pid = placement.get("placement_id")
            if pid is not None:
                by_pid[pid] = placement
            att = placement.get("attachment") or {}
            aid = att.get("attachment_id") or placement.get("attachment_id")
            if aid:
                by_aid[aid] = placement
                host = _attachment_host(att)
                if host:
                    feature_aids.setdefault(host, []).append(aid)
    return by_aid, by_pid, feature_aids, f"{pop_path}: {n} placements"



def build(route_path: Path, world_ir_path: Path, terrain_metadata_path: Path,
          plan_path: Path, build_dir: Path,
          rules_path: Path | None,
          generated_at: str,
          authored_spatial_decisions_path: Path | None = None
          ) -> tuple[dict, dict]:
    from scripts.pipeline.spatial_coherence_review import (
        apply_authored_adjudications,
        load_decisions as load_spatial_decisions,
    )
    spatial_decisions = load_spatial_decisions(
        authored_spatial_decisions_path)
    meta = json.loads(terrain_metadata_path.read_text(encoding="utf-8"))
    proj = meta.get("projection", meta)
    scale = float(proj["content_scale"])
    origin_e = float(proj["origin_easting"])
    origin_n = float(proj["origin_northing"])
    offset_x = float(proj.get("offset_x", 0.0))
    offset_y = float(proj.get("offset_y", 0.0))

    def to_world(easting: float, northing: float) -> tuple[float, float]:
        return ((easting - origin_e) * scale + offset_x,
                (northing - origin_n) * scale + offset_y)
    def to_utm(world_x: float, world_y: float) -> tuple[float, float]:
        return ((world_x - offset_x) / scale + origin_e,
                (world_y - offset_y) / scale + origin_n)

    def metres(world_dist: float) -> float:
        # World units run scale-per-metre; every tolerance below is metres.
        return world_dist / scale

    route_doc = json.loads(route_path.read_text(encoding="utf-8"))
    route_xy = [GF.utm_forward(lon, lat)
                for lon, lat in _decode_polyline6(route_doc["trip"]["legs"][0]["shape"])]
    bin_length_m = DEFAULT_BIN_LENGTH_M
    corridor_m = DEFAULT_ROUTE_VISIBLE_CORRIDOR_M
    if rules_path is None or not rules_path.is_file():
        raise ValueError(
            "spatial coherence requires the generation's resolved rules; "
            "verifier-side default laws are forbidden"
        )
    _rdoc = json.loads(rules_path.read_text(encoding="utf-8"))
    inv = _rdoc.get("inventory") or {}
    if inv.get("bin_length_m") is None or inv.get("route_visible_corridor_m") is None:
        raise ValueError("resolved rules lack inventory bin/corridor facts")
    bin_length_m = float(inv["bin_length_m"])
    corridor_m = float(inv["route_visible_corridor_m"])
    rules_basis = "rules.inventory"
    _rb = ((_rdoc.get("roadside_lateral_band") or {}).get(
        "roadside_lateral_band_m") or {})
    if _rb.get("value") is None:
        raise ValueError("resolved rules lack roadside_lateral_band_m")
    roadside_band = float(_rb["value"])
    roadside_basis = "rules.roadside_lateral_band"
    stations = [route_xy[0]]
    acc = 0.0
    for a, b in zip(route_xy, route_xy[1:]):
        seg = math.dist(a, b)
        while acc + seg >= bin_length_m:
            take = bin_length_m - acc
            frac = take / seg if seg > 0 else 0.0
            nxt = (a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac)
            stations.append(nxt)
            a = nxt
            seg = math.dist(b, a)
            acc = 0.0
        acc += seg
    stations.append(route_xy[-1])
    total_m = sum(math.dist(b, a) for a, b in zip(route_xy, route_xy[1:]))
    n_bins = max(1, len(stations) - 1)

    def assign_bin(point_world: tuple[float, float]) -> tuple[int, float] | None:
        # Corridor width is metres: compare in UTM, not world units.
        point_utm = to_utm(*point_world)
        best = None
        for idx, station in enumerate(route_xy):
            dist = math.dist(point_utm, station)
            if best is None or dist < best[1]:
                best = (idx, dist)
        if best is None or best[1] > corridor_m:
            return None
        station = best[0]
        progress = min(station, n_bins - 1) * bin_length_m
        return (min(station, n_bins - 1), progress)

    world = json.loads(world_ir_path.read_text(encoding="utf-8"))
    observations = {str(o["observation_id"]): o for o in world.get("source_observations", [])}
    features = {f["feature_id"]: f for f in world.get("canonical_features", [])}

    def tags_of(feature: dict) -> dict:
        for oid in feature.get("supporting_observation_ids", []):
            obs = observations.get(oid) or {}
            tags = (obs.get("attributes") or {}).get("tags")
            if tags:
                return dict(tags)
        return {}

    plan_rows = _plan_rows_by_feature(json.loads(plan_path.read_text(encoding="utf-8")))
    entities_path = build_dir / "canonical_entities.json"

    buildings = json.loads((build_dir / "building_instances.json").read_text(encoding="utf-8"))
    # SPATIAL-04: canonical-close every mass ring once, in memory. Source
    # coordinates are never rewritten; closure only removes the
    # winding/open-ring ambiguity from containment and edge-distance tests.
    mass_rings = {row["feature_id"]: _closed(row["footprint"]) for row in buildings
                  if row.get("footprint") and len(row["footprint"]) >= 3}
    mass_height_m = {
        row["feature_id"]: float(row["roof_height_m"])
        for row in buildings if row.get("roof_height_m") is not None}
    buildings_degenerate = [row for row in buildings
                            if not (row.get("footprint") and len(row["footprint"]) >= 3)]
    # SPATIAL-04: placement-unique linear joins. The feature-level plan row
    # is one attachment per feature; each emitted tile carries the
    # placement attachment_id that sited it.
    place_by_aid, place_by_pid, feature_aids, placement_note = _placement_index(build_dir)

    polylines: dict[str, list] = {}
    for fid, feat in features.items():
        if feat["geometry"]["kind"] == "polyline":
            polylines[fid] = feat["geometry"]["coordinates"]

    surface = json.loads((build_dir / "surface_receipt.json").read_text(encoding="utf-8"))
    road_half: dict[str, float] = {}
    for link in surface.get("features", []):
        width = link.get("width_m")
        road_half[str(link.get("feature_id"))] = (
            float(width) / 2.0 if width else SPATIAL_DEFAULTS["road_half_width_default_m"][0])

    road_lines: dict[str, list] = {}
    for fid, line in polylines.items():
        if features[fid]["semantic_class"] == "road":
            road_lines[fid] = [to_world(x, y) for x, y in line]

    index = CanonicalIndex(world)
    # PLATEAU usage code per host mass for the inside_mass rows (d): OSM
    # way masses resolve through the entities dispositions, plateau masses
    # directly by gml id. POPPLAN-02 rules on these codes; no verdict change.
    entities_doc = json.loads(entities_path.read_text(encoding="utf-8")) \
        if entities_path.is_file() else {"entities": [], "dispositions": []}
    usage_by_canonical = {}
    for entity in entities_doc.get("entities", []):
        usage = (entity.get("claims") or {}).get("usage") or {}
        usage_by_canonical[entity["canonical_id"]] = usage.get("value")
    canonical_by_source: dict[tuple[str, str], str] = {}
    for disp in entities_doc.get("dispositions", []):
        canonical_by_source[(disp.get("source"), disp.get("source_object_id"))] = \
            disp.get("canonical_id")

    def host_usage_code(mass_fid: str) -> str | None:
        if mass_fid.startswith("feature:building_footprint:plateau/"):
            gml = mass_fid.split("/")[-1]
            cid = canonical_by_source.get(("plateau", gml))
        elif mass_fid.startswith("feature:building_footprint:"):
            ref = mass_fid.split("feature:building_footprint:")[-1]
            cid = canonical_by_source.get(("osm", ref))
        else:
            return None
        return usage_by_canonical.get(cid) if cid else None

    def plan_pose(fid: str) -> tuple[dict | None, list[str]]:
        """Attachment pose for a feature: (attachment, missing-fields)."""
        row = plan_rows.get(fid)
        if row is None:
            return None, ["plan_row"]
        attachment = row.get("attachment")
        if not attachment:
            return None, ["attachment"]
        return attachment, []

    _fixture_path = build_dir / "lower_fixtures_instances.json"
    _fixture_absent = not _fixture_path.is_file()
    fixtures = list(json.loads(_fixture_path.read_text(encoding="utf-8"))
                    if not _fixture_absent else [])
    _linear_path = build_dir / "lower_linear_instances.json"
    _linear_absent = not _linear_path.is_file()
    # SPATIAL-04: every emitted linear row is evaluated, including span
    # segment records (emitted wire/catenary geometry with transforms).
    linear = list(json.loads(_linear_path.read_text(encoding="utf-8")) if not _linear_absent else [])
    _custom_path = build_dir / "lower_custom_instances.json"
    _custom_absent = not _custom_path.is_file()
    custom = list(json.loads(_custom_path.read_text(encoding="utf-8"))
                  if not _custom_absent else [])
    _facade_path = build_dir / "station_facade_instances.json"
    _facade_absent = not _facade_path.is_file()
    facade_insts = json.loads(_facade_path.read_text(encoding="utf-8")) if not _facade_absent else []
    if isinstance(facade_insts, dict):
        facade_insts = facade_insts.get("instances", [])
    inputs_absent = []
    if _fixture_absent:
        inputs_absent.append({"path": str(_fixture_path),
                              "stage": "lower_fixtures",
                              "reason": "emission_inventory_missing"})
    if _linear_absent:
        inputs_absent.append({"path": str(_linear_path), "stage": "lower_linear",
                              "reason": "not_emitted_in_build: linear lowering did not run for this build"})
    if _custom_absent:
        inputs_absent.append({"path": str(_custom_path), "stage": "lower_custom",
                              "reason": "emission_inventory_missing: custom lowering did not persist its A3D denominator"})
    if _facade_absent:
        inputs_absent.append({"path": str(_facade_path), "stage": "station_facade",
                              "reason": "emission_inventory_missing: station facade did not persist its A3D denominator"})
    tol = {key: value[0] for key, value in SPATIAL_DEFAULTS.items()}
    rows: list[dict] = []
    contract_missing: list[dict] = []

    def emit(row: dict) -> None:
        # SPATIAL-04: binning is descriptive. An out-of-corridor object
        # stays in the receipt with an explicit route-scope state; the
        # corridor never suppresses a verdict row.
        assigned = assign_bin((row["emitted_position"][0], row["emitted_position"][1]))
        if assigned is None:
            row["bin"], row["route_progress_m"] = None, None
            row["route_scope"] = "out_of_corridor"
        else:
            row["bin"], row["route_progress_m"] = assigned[0], round(
                min(assigned[0], n_bins - 1) * bin_length_m, 3)
            row["route_scope"] = "in_corridor"
        rows.append(row)

    # -- fixtures: planned = resolve(plan attachment) -------------------------
    # The OSM source point is provenance only; chainage and face attachments
    # deliberately offset the hardware from it (NUMABE-SPATIAL-02 defect b).
    for inst in fixtures:
        pid = inst.get("placement_id")
        placement = place_by_pid.get(pid) if pid is not None else None
        if placement is not None:
            attachment = placement.get("attachment")
            missing = ([] if isinstance(attachment, dict)
                       and attachment.get("record_type") == "attachment"
                       else ["resolved_attachment"])
            basis = f"placement:{pid}"
        else:
            fallback_fid = str(inst.get("feature_id") or inst.get("parent"))
            attachment, missing = plan_pose(fallback_fid)
            basis = f"feature:{fallback_fid}"
        host = _attachment_host(attachment)
        fid = str(inst.get("feature_id") or inst.get("parent") or host
                  or f"population_placement:{pid}")
        emitted = (float(inst["transform"][12]), float(inst["transform"][13]))
        emitted_utm = to_utm(*emitted)
        feat = features.get(fid)
        group = (_group_for(feat["semantic_class"], tags_of(feat))
                 if feat else "street_furniture")
        if not isinstance(attachment, dict) \
                or attachment.get("record_type") != "attachment":
            contract_missing.append({"feature_id": fid, "inst_name": inst.get("inst_name"),
                                     "missing": missing})
            emit({"feature_id": fid, "inst_name": inst.get("inst_name"),
                  "representation_id": (plan_rows.get(fid) or {}).get("representation_id"),
                  "canonical_host_id": None, "anchor_kind": "freestanding",
                  "attachment_basis": basis,
                  "planned_position": None, "emitted_position": list(emitted),
                  "host_distance_m": None, "attachment_residual_m": None,
                  "orientation_residual_deg": None, "collision_state": "clear",
                  "verdict": "UNRESOLVED",
                  "reason": f"attachment contract missing: {','.join(missing)}",
                  "class_group": group})
            continue
        anchor = attachment["anchor_kind"]
        if anchor == "freestanding" and placement is not None:
            planned_utm = (float(placement["E"]), float(placement["N"]))
        else:
            pose = resolve(attachment, index)
            planned_utm = (float(pose["E"]), float(pose["N"]))
        planned = to_world(*planned_utm)
        residual = math.dist(emitted_utm, planned_utm)
        if anchor == "building_face":
            residual_tol = tol["facade_edge_tolerance_m"]
        elif anchor in ("carrier_chainage", "carrier_span"):
            residual_tol = tol["carrier_span_residual_m"]
        elif inst.get("road_centerline_clearance_m") is not None:
            residual_tol = tol["freestanding_roadside_m"]
        else:
            residual_tol = tol["freestanding_residual_m"]
        collision = "clear"
        verdict = "PASS"
        reason = f"{anchor} residual {residual:.3f}m within {residual_tol}m"
        mass_skip = {host} if host in mass_rings else set()
        mass_hit = _inside_mass(emitted, mass_rings, mass_skip)
        if mass_hit is not None:
            collision = f"inside_mass:{mass_hit}"
            verdict = "FAIL"
            reason = f"emitted position inside non-host mass {mass_hit}"
        elif residual > residual_tol:
            verdict = "FAIL"
            reason = (f"attachment residual {residual:.3f}m beyond "
                      f"{residual_tol}m")
        host_distance = None
        if group == "poles_and_lamps" and host not in road_lines:
            best_road = None
            for road_fid, line in road_lines.items():
                dist = metres(_dist_to_polyline(emitted, line))
                if best_road is None or dist < best_road[1]:
                    best_road = (road_fid, dist)
            if best_road is not None:
                host, host_distance = best_road[0], round(best_road[1], 3)
                half = road_half.get(host, tol["road_half_width_default_m"])
                if host_distance < half:
                    collision, verdict = "overlaps_carriageway", "FAIL"
                    reason = (f"pole/lamp {host_distance}m from {host} centreline, "
                              f"inside {half}m half-width carriageway")
        if host in road_lines:
            host_distance = round(metres(_dist_to_polyline(emitted, road_lines[host])), 3)
        usage = host_usage_code(mass_hit) if mass_hit else None
        emit({"feature_id": fid, "inst_name": inst.get("inst_name"),
              "representation_id": (plan_rows.get(fid) or {}).get("representation_id"),
              "canonical_host_id": host, "anchor_kind": anchor,
              "attachment_basis": basis,
              "planned_position": list(planned), "emitted_position": list(emitted),
              "host_distance_m": host_distance, "attachment_residual_m": round(residual, 3),
              "orientation_residual_deg": None, "collision_state": collision,
              "host_usage_code": usage,
              "verdict": verdict, "reason": reason,
              "class_group": group})

    # -- population custom identities: placement-owned attachment ------------
    # These instances are real A3D geometry, not viewer annotations.  Their
    # population placement is the only spatial owner; the lowerer may resolve
    # that attachment but may not independently select another face/standoff.
    for inst in custom:
        pid = str(inst.get("placement_id") or "")
        fid = f"population_placement:{pid}" if pid else "population_placement:missing"
        emitted = (float(inst["transform"][12]), float(inst["transform"][13]))
        emitted_utm = to_utm(*emitted)
        placement = place_by_pid.get(pid)
        attachment = (placement or {}).get("attachment")
        missing = []
        if placement is None:
            missing.append("population_placement")
        if not isinstance(attachment, dict):
            missing.append("attachment")
        elif attachment.get("record_type") != "attachment":
            missing.append("resolved_attachment")
        if missing:
            contract_missing.append({"feature_id": fid,
                                     "inst_name": inst.get("inst_name"),
                                     "missing": missing})
            emit({"feature_id": fid, "placement_id": pid,
                  "inst_name": inst.get("inst_name"),
                  "representation_id": None,
                  "canonical_host_id": None, "anchor_kind": "custom_identity",
                  "attachment_basis": f"placement:{pid}",
                  "emitted_parent_id": inst.get("parent"),
                  "plan_parent_id": None, "face_ownership": "unknown",
                  "planned_position": None, "emitted_position": list(emitted),
                  "host_distance_m": None, "attachment_residual_m": None,
                  "orientation_residual_deg": None, "collision_state": "clear",
                  "verdict": "UNRESOLVED",
                  "reason": f"attachment contract missing: {','.join(missing)}",
                  "class_group": "custom_identity"})
            continue

        anchor = attachment.get("anchor_kind")
        host = _attachment_host(attachment)
        if anchor == "freestanding":
            planned_utm = (float(placement["E"]), float(placement["N"]))
            pose = {"E": planned_utm[0], "N": planned_utm[1],
                    "yaw_deg": float(placement.get("yaw_deg") or 0.0)}
        else:
            pose = resolve(attachment, index)
            planned_utm = (float(pose["E"]), float(pose["N"]))
        planned = to_world(*planned_utm)
        residual = math.dist(emitted_utm, planned_utm)
        emitted_parent = inst.get("parent")
        ownership = "agree" if emitted_parent == host else "mismatch"
        residual_tol = (tol["facade_edge_tolerance_m"]
                        if anchor == "building_face"
                        else tol["carrier_span_residual_m"]
                        if anchor in ("carrier_chainage", "carrier_span")
                        else tol["freestanding_residual_m"])
        host_distance = None
        orientation = None
        collision, verdict = "clear", "PASS"
        reason = f"{anchor} residual {residual:.3f}m within {residual_tol}m"
        if host is None:
            verdict, reason = "UNRESOLVED", "attachment contract missing: canonical host"
            contract_missing.append({"feature_id": fid,
                                     "inst_name": inst.get("inst_name"),
                                     "missing": ["canonical_host"]})
        elif ownership == "mismatch":
            verdict = "FAIL"
            reason = (f"custom identity host mismatch: emitted parent {emitted_parent} "
                      f"vs plan host {host}")
        elif residual > residual_tol:
            verdict = "FAIL"
            reason = (f"attachment residual {residual:.3f}m beyond "
                      f"{residual_tol}m")

        if anchor == "building_face" and host in mass_rings:
            face_index = int(attachment.get("face_index", -1))
            edges = list(zip(mass_rings[host], mass_rings[host][1:]))
            if not 0 <= face_index < len(edges):
                verdict = "UNRESOLVED"
                reason = (f"face index {face_index} out of range on a "
                          f"{len(edges)}-edge ring")
                contract_missing.append({"feature_id": fid,
                                         "inst_name": inst.get("inst_name"),
                                         "missing": ["face_index_in_range"]})
            else:
                a, b = edges[face_index]
                host_distance = metres(_dist_to_polyline(emitted, [a, b]))
                expected_yaw = float(pose.get("yaw_deg") or 0.0)
                emitted_yaw = math.degrees(math.atan2(
                    float(inst["transform"][5]), float(inst["transform"][4])))
                orientation = _yaw_diff_deg(emitted_yaw, expected_yaw)
                if verdict == "PASS" and host_distance > tol["facade_edge_tolerance_m"]:
                    verdict = "FAIL"
                    reason = (f"custom identity {host_distance:.3f}m off declared "
                              f"face {face_index}")
                elif verdict == "PASS" and orientation > tol["facade_orientation_deg"]:
                    verdict = "FAIL"
                    reason = (f"orientation residual {orientation:.3f}deg beyond "
                              f"{tol['facade_orientation_deg']}deg")

        mass_hit = _inside_mass(
            emitted, mass_rings, {host} if host in mass_rings else set())
        if verdict == "PASS" and mass_hit is not None:
            collision, verdict = f"inside_mass:{mass_hit}", "FAIL"
            reason = f"custom identity inside non-host mass {mass_hit}"
        usage = host_usage_code(mass_hit) if mass_hit else None
        emit({"feature_id": fid, "placement_id": pid,
              "inst_name": inst.get("inst_name"),
              "representation_id": None,
              "canonical_host_id": host, "anchor_kind": anchor,
              "attachment_basis": f"placement:{pid}",
              "emitted_parent_id": emitted_parent, "plan_parent_id": host,
              "face_ownership": ownership,
              "planned_position": list(planned), "emitted_position": list(emitted),
              "host_distance_m": round(host_distance, 3)
              if host_distance is not None else None,
              "attachment_residual_m": round(residual, 3),
              "orientation_residual_deg": round(orientation, 3)
              if orientation is not None else None,
              "collision_state": collision, "host_usage_code": usage,
              "verdict": verdict, "reason": reason,
              "class_group": "custom_identity"})
    # -- linear tiles: planned = placement attachment span ----------------------
    # Chainage/span attachments resolve against current canonical geometry,
    # so stale-plan drift surfaces as residual, not as silence. SPATIAL-04:
    # the expected pose resolves by the instance's placement attachment_id
    # (population plan), never the single feature-level plan row: many
    # placements share one feature, each with its own span/chainage.
    def linear_pose(inst: dict, fid: str) -> tuple[dict | None, str, list[str], dict | None]:
        """Placement-unique attachment for a linear row.

        Returns (attachment, basis, missing, placement). Feature-level
        fallback occurs only when provably unambiguous (exactly one
        placement references the feature, or none does and the plan row
        is the only attachment on record) and the basis records it.
        """
        aid = inst.get("attachment_id")
        if aid and aid in place_by_aid:
            placement = place_by_aid[aid]
            return (placement.get("attachment") or {}), f"placement:{aid}", [], placement
        if inst.get("span_record"):
            first, second = inst.get("span_from"), inst.get("span_to")
            p_first = place_by_pid.get(first) if first else None
            p_second = place_by_pid.get(second) if second else None
            a_first = (p_first or {}).get("attachment") or {}
            a_second = (p_second or {}).get("attachment") or {}
            if p_first is None or p_second is None or not a_first or not a_second:
                return None, "placement_span_unresolved", ["span_endpoint_placement"], None
            host_first, host_second = _attachment_host(a_first), _attachment_host(a_second)
            if a_first.get("anchor_kind") == "freestanding" and \
                    a_second.get("anchor_kind") == "freestanding":
                synth = {"record_type": "attachment", "anchor_kind": "freestanding",
                         "attachment_id": f"span:{first}+{second}",
                         "canonical_point": f"span:{first}+{second}",
                         "nearest_carrier_id": a_second.get("nearest_carrier_id")}
                return synth, f"placement_span:{first}+{second}", [], p_second
            if host_first is None or host_first != host_second:
                return None, "placement_span_unresolved", ["span_endpoint_shared_host"], None
            if a_first.get("anchor_kind") == "carrier_span" and \
                    a_second.get("anchor_kind") == "carrier_span":
                lo = min(float(a_first["start_chainage_m"]), float(a_first["end_chainage_m"]),
                         float(a_second["start_chainage_m"]), float(a_second["end_chainage_m"]))
                hi = max(float(a_first["start_chainage_m"]), float(a_first["end_chainage_m"]),
                         float(a_second["start_chainage_m"]), float(a_second["end_chainage_m"]))
                synth = {"record_type": "attachment", "anchor_kind": "carrier_span",
                         "attachment_id": f"span:{first}+{second}",
                         "canonical_carrier_id": host_first,
                         "start_chainage_m": lo, "end_chainage_m": hi,
                         "lateral_offset_m": 0.0}
                return synth, f"placement_span:{first}+{second}", [], p_second
            if a_first.get("anchor_kind") == "carrier_chainage" and \
                    a_second.get("anchor_kind") == "carrier_chainage":
                lo = min(float(a_first["chainage_m"]), float(a_second["chainage_m"]))
                hi = max(float(a_first["chainage_m"]), float(a_second["chainage_m"]))
                synth = {"record_type": "attachment", "anchor_kind": "carrier_span",
                         "attachment_id": f"span:{first}+{second}",
                         "canonical_carrier_id": host_first,
                         "start_chainage_m": lo, "end_chainage_m": hi,
                         "lateral_offset_m": 0.0}
                return synth, f"placement_span:{first}+{second}", [], p_second
            return None, "placement_span_unresolved", ["span_endpoint_anchor_mismatch"], None
        pid = inst.get("placement_id") or inst.get("run_id")
        if pid and pid in place_by_pid:
            placement = place_by_pid[pid]
            return (placement.get("attachment") or {}), f"placement_pid:{pid}", [], placement
        refs = feature_aids.get(fid, [])
        if len(refs) == 1 and refs[0] in place_by_aid:
            placement = place_by_aid[refs[0]]
            return ((placement.get("attachment") or {}),
                    f"placement_singleton_for_feature:{refs[0]}", [], placement)
        if not refs:
            attachment, missing = plan_pose(fid)
            if attachment is not None:
                return attachment, f"feature_only_no_placements:{fid}", [], None
            return None, f"feature:{fid}", missing, None
        return None, "ambiguous_feature_multiple_placements", ["placement_unique_attachment"], None
    for inst in linear:
        fid = str(inst.get("feature_id"))
        emitted = (float(inst["transform"][12]), float(inst["transform"][13]))
        emitted_utm = to_utm(*emitted)
        feat = features.get(fid)
        group = _group_for(feat["semantic_class"], tags_of(feat)) if feat else "unmapped_class"
        attachment, basis, missing, placement = linear_pose(inst, fid)
        inst_yaw = float(inst.get("yaw_deg") or 0.0)
        if attachment is None:
            contract_missing.append({"feature_id": fid, "inst_name": inst.get("inst_name"),
                                     "missing": missing})
            emit({"feature_id": fid, "inst_name": inst.get("inst_name"),
                  "representation_id": (plan_rows.get(fid) or {}).get("representation_id"),
                  "canonical_host_id": None, "anchor_kind": "carrier_span",
                  "attachment_basis": basis,
                  "planned_position": None, "emitted_position": list(emitted),
                  "host_distance_m": None, "attachment_residual_m": None,
                  "orientation_residual_deg": None, "collision_state": "clear",
                  "verdict": "UNRESOLVED",
                  "reason": f"attachment contract missing: {','.join(missing)}",
                  "class_group": group})
            continue
        anchor = attachment["anchor_kind"]
        host = _attachment_host(attachment)
        if host is not None and host not in features and not host.startswith(("plan:", "span:")):
            emit({"feature_id": fid, "inst_name": inst.get("inst_name"),
                  "representation_id": (plan_rows.get(fid) or {}).get("representation_id"),
                  "canonical_host_id": None, "anchor_kind": anchor,
                  "attachment_basis": basis,
                  "planned_position": None, "emitted_position": list(emitted),
                  "host_distance_m": None, "attachment_residual_m": None,
                  "orientation_residual_deg": float(inst.get("yaw_deg") or 0.0),
                  "collision_state": f"missing_host:{host}",
                  "host_usage_code": None,
                  "verdict": "FAIL",
                  "reason": f"planned host id missing from canonical IR: {host}",
                  "class_group": group})
            continue
        if anchor == "freestanding":
            # SPATIAL-04: freestanding placements carry a plan: reference
            # point, not canonical geometry. The placement-unique expected
            # pose is the placement E,N (span records: the midpoint of the
            # two endpoint placements, matching the lowering construction);
            # burial is still tested. Tolerance is the existing
            # freestanding value, unchanged.
            if inst.get("span_record"):
                p_first = place_by_pid.get(inst.get("span_from") or "")
                p_second = place_by_pid.get(inst.get("span_to") or "")
                if p_first is not None and p_second is not None \
                        and p_first.get("E") is not None and p_second.get("E") is not None:
                    planned_utm = ((float(p_first["E"]) + float(p_second["E"])) / 2.0,
                                   (float(p_first["N"]) + float(p_second["N"])) / 2.0)
                    planned_from = "span_endpoint_midpoint"
                else:
                    planned_utm = (float(inst.get("E")), float(inst.get("N")))
                    planned_from = "instance"
            elif placement is not None and placement.get("E") is not None:
                planned_utm = (float(placement["E"]), float(placement["N"]))
                planned_from = "placement"
            else:
                planned_utm = (float(inst.get("E")), float(inst.get("N")))
                planned_from = "instance"
            planned = to_world(*planned_utm)
            residual = math.dist(emitted_utm, planned_utm)
            residual_tol = tol["freestanding_residual_m"]
            collision, verdict = "clear", "PASS"
            reason = (f"freestanding residual {residual:.3f}m within "
                      f"{residual_tol}m ({planned_from} E,N; {basis})")
            mass_hit = _inside_mass(emitted, mass_rings, set())
            if mass_hit is not None:
                collision, verdict = f"inside_mass:{mass_hit}", "FAIL"
                reason = f"emitted position inside non-host mass {mass_hit}"
            elif residual > residual_tol:
                verdict = "FAIL"
                reason = (f"attachment residual {residual:.3f}m beyond "
                          f"{residual_tol}m ({planned_from} E,N; {basis})")
            usage = host_usage_code(mass_hit) if mass_hit else None
            emit({"feature_id": fid, "inst_name": inst.get("inst_name"),
                  "representation_id": (plan_rows.get(fid) or {}).get("representation_id"),
                  "canonical_host_id": host, "anchor_kind": anchor,
                  "attachment_basis": basis,
                  "planned_position": list(planned), "emitted_position": list(emitted),
                  "host_distance_m": None, "attachment_residual_m": round(residual, 3),
                  "orientation_residual_deg": None, "collision_state": collision,
                  "host_usage_code": usage,
                  "verdict": verdict, "reason": reason, "class_group": group})
            continue
        span_lo = span_hi = tile_chainage = None
        span_endpoint_pose = False
        span_mass_hits: list[str] = []
        if anchor == "terrain_mass" and host in mass_rings:
            ring = mass_rings[host]
            closed = _closed(ring)
            best = None
            for a, b in zip(closed, closed[1:]):
                dx, dy = b[0] - a[0], b[1] - a[1]
                denom = dx * dx + dy * dy
                if denom <= 1e-12:
                    q = a
                else:
                    t = max(0.0, min(1.0, ((emitted[0] - a[0]) * dx
                        + (emitted[1] - a[1]) * dy) / denom))
                    q = (a[0] + t * dx, a[1] + t * dy)
                dist = math.dist(emitted, q)
                if best is None or dist < best[0]:
                    best = (dist, q, math.degrees(math.atan2(dy, dx)))
            residual = metres(best[0])
            planned = best[1]
            orientation = _yaw_diff_deg(inst_yaw, best[2])
            lateral = residual
        else:
            carrier_poly = []
            if host is not None:
                carrier_poly = (index.by_id.get(host, {}).get("geometry") or {}).get("coordinates", [])
            carrier_world = [to_world(x, y) for x, y in carrier_poly]
            # Nearest carrier vertex sets the local tangent and the tile
            # chainage: long curved spans must not be judged by chord bearing.
            nearest = None
            chainage = 0.0
            for a, b in zip(carrier_world, carrier_world[1:]):
                dx, dy = b[0] - a[0], b[1] - a[1]
                seg_len = math.hypot(dx, dy)
                if seg_len <= 1e-12:
                    continue
                t = max(0.0, min(1.0, ((emitted[0] - a[0]) * dx
                    + (emitted[1] - a[1]) * dy) / (seg_len * seg_len)))
                q = (a[0] + t * dx, a[1] + t * dy)
                dist = math.dist(emitted, q)
                if nearest is None or dist < nearest[0]:
                    nearest = (dist, math.degrees(math.atan2(dy, dx)),
                               chainage + t * seg_len, q)
                chainage += seg_len
            if nearest is None:
                pose = resolve(attachment, index)
                planned_utm = (float(pose["E"]), float(pose["N"]))
                planned = to_world(*planned_utm)
                residual = math.dist(emitted_utm, planned_utm)
                orientation = _yaw_diff_deg(inst_yaw, float(pose.get("yaw_deg") or 0.0))
                lateral = residual
                span_lo = span_hi = None
            else:
                lateral = metres(nearest[0])
                orientation = _yaw_diff_deg(inst_yaw, nearest[1])
                tile_chainage = nearest[2] / scale
                planned = nearest[3]
                residual = lateral
                if anchor == "carrier_span":
                    span_lo = min(float(attachment["start_chainage_m"]),
                                  float(attachment["end_chainage_m"]))
                    span_hi = max(float(attachment["start_chainage_m"]),
                                  float(attachment["end_chainage_m"]))
                else:
                    span_lo = span_hi = None
        if inst.get("span_record"):
            p_first = place_by_pid.get(inst.get("span_from") or "")
            p_second = place_by_pid.get(inst.get("span_to") or "")
            if p_first is not None and p_second is not None \
                    and p_first.get("E") is not None \
                    and p_second.get("E") is not None:
                first_utm = (float(p_first["E"]), float(p_first["N"]))
                second_utm = (float(p_second["E"]), float(p_second["N"]))
                first_world = to_world(*first_utm)
                second_world = to_world(*second_utm)
                span_mass_hits = _segment_mass_hits(
                    first_world, second_world, mass_rings,
                    {host} if host in mass_rings else set())
                planned_utm = ((first_utm[0] + second_utm[0]) / 2.0,
                               (first_utm[1] + second_utm[1]) / 2.0)
                planned = to_world(*planned_utm)
                residual = math.dist(emitted_utm, planned_utm)
                expected_yaw = math.degrees(math.atan2(
                    second_utm[1] - first_utm[1],
                    second_utm[0] - first_utm[0]))
                orientation = _yaw_diff_deg(inst_yaw, expected_yaw)
                span_endpoint_pose = True
        else:
            reference_yaw = _lowered_yaw_reference(inst, placement, attachment, anchor, index)
            if reference_yaw is not None:
                orientation = _yaw_diff_deg(inst_yaw, reference_yaw)
        collision, verdict, reason = "clear", "PASS", f"{anchor} within tolerances"
        if anchor == "terrain_mass":
            mass_hit = _inside_mass(emitted, mass_rings, {host} if host else set())
            if mass_hit is not None:
                collision, verdict = f"inside_mass:{mass_hit}", "FAIL"
                reason = f"tile inside neighbouring mass {mass_hit}"
            elif residual > tol["facade_edge_tolerance_m"]:
                verdict = "FAIL"
                reason = (f"attachment residual {residual:.3f}m beyond "
                          f"{tol['facade_edge_tolerance_m']}m")
        else:
            # SPATIAL-04: a tile on a building carrier sits on its own host
            # ring; only other masses are burial. Road/rail hosts are never
            # masses, so their skip set stays empty.
            mass_hit = _inside_mass(
                emitted, mass_rings, {host} if host in mass_rings else set())
            overhead_clear = []
            if inst.get("span_record") and span_mass_hits:
                conflicts, overhead_clear = _span_vertical_conflicts(
                    span_mass_hits, inst.get("span_min_height_m"),
                    mass_height_m)
                mass_hit = conflicts[0] if conflicts else None
            if mass_hit is not None:
                collision, verdict = f"inside_mass:{mass_hit}", "FAIL"
                reason = f"tile inside non-host mass {mass_hit}"
            elif span_endpoint_pose:
                if residual > tol["carrier_span_residual_m"]:
                    collision, verdict = "span_endpoint_drift", "FAIL"
                    reason = (f"span midpoint residual {residual:.3f}m beyond "
                              f"{tol['carrier_span_residual_m']}m")
                else:
                    reason = (f"span midpoint residual {residual:.3f}m within "
                              f"{tol['carrier_span_residual_m']}m")
                    if overhead_clear:
                        reason += "; overhead clearance " + ", ".join(
                            f"{fid}:wire_min={float(inst['span_min_height_m']):.3f}m"
                            f">roof={roof:.3f}m"
                            for fid, roof in overhead_clear)
                        collision = "overhead_clear"
            elif anchor == "carrier_span" and span_lo is not None:
                span_tol = tol["carrier_span_residual_m"]
                # SPATIAL-04: building carriers are closed rings. An authored
                # span with start > end traverses the ring origin (plan
                # frontage runs end at chainage 0.0); containment wraps
                # instead of misreading the plan as [end,start]. Open
                # carriers keep linear containment. Tolerance unchanged.
                authored_start = float(attachment.get("start_chainage_m", span_lo))
                authored_end = float(attachment.get("end_chainage_m", span_hi))
                if host in mass_rings and authored_start > authored_end:
                    in_span = (tile_chainage >= authored_start - span_tol or
                               tile_chainage <= authored_end + span_tol)
                    span_label = (f"[{authored_start:.3f},{authored_end:.3f}]m wrap on {host}")
                else:
                    in_span = (span_lo - span_tol <= tile_chainage <= span_hi + span_tol)
                    span_label = f"[{span_lo:.3f},{span_hi:.3f}]m on {host}"
                if not in_span:
                    collision, verdict = "span_out_of_range", "FAIL"
                    reason = (f"tile chainage {tile_chainage:.3f}m outside span {span_label}")
                else:
                    start_offset = attachment.get("start_lateral_offset_m")
                    end_offset = attachment.get("end_lateral_offset_m")
                    if (start_offset is not None and end_offset is not None
                            and abs(authored_end - authored_start) > 1e-9):
                        fraction = max(0.0, min(1.0,
                            (tile_chainage - authored_start)
                            / (authored_end - authored_start)))
                        start_signed = float(start_offset) * (
                            -1.0 if attachment.get("start_side") == "L" else 1.0)
                        end_signed = float(end_offset) * (
                            -1.0 if attachment.get("end_side") == "L" else 1.0)
                        planned_lat = abs(
                            start_signed + (end_signed - start_signed) * fraction)
                        planned_lat_basis = "endpoint_interpolation"
                    else:
                        planned_lat = abs(float(
                            attachment.get("lateral_offset_m") or 0.0))
                        planned_lat_basis = "shared_offset"
                    lat_residual = abs(lateral - planned_lat)
                    residual = lat_residual
                    if lat_residual > tol["carrier_span_lateral_m"]:
                        collision, verdict = "span_off_carrier", "FAIL"
                        reason = (f"tile lateral {lateral:.3f}m vs planned "
                                  f"offset {planned_lat:.3f}m differs by "
                                  f"{lat_residual:.3f}m (beyond "
                                  f"{tol['carrier_span_lateral_m']}m)")
                    else:
                        reason = (f"carrier_span lateral {lateral:.3f}m within "
                                  f"{tol['carrier_span_lateral_m']}m of planned "
                                  f"offset {planned_lat:.3f}m "
                                  f"({planned_lat_basis})")
            # FL-4876 COHERENCE-ZERO-01: a wire span carries its second pole's
            # chainage attachment (attachment_id), but its pose is judged
            # against BOTH poles above (span_endpoint_pose: midpoint residual,
            # pole-to-pole orientation, overhead mass clearance). The roadside
            # band is a point-object rule: a wire between two poles leaves the
            # poles' kerb offset wherever it crosses or follows a bending road.
            # The poles themselves still face the band as point objects.
            if anchor == "carrier_chainage" and span_lo is None and not span_endpoint_pose:
                planned_lat = abs(float((attachment or {}).get("lateral_offset_m") or 0.0))
                lat_residual = abs(lateral - planned_lat)
                if verdict == "PASS" and lat_residual > roadside_band:
                    verdict = "FAIL"
                    reason = (f"lateral {lateral:.3f}m vs planned offset "
                              f"{planned_lat:.3f}m outside roadside band "
                              f"{roadside_band:.3f}m ({roadside_basis})")
                elif verdict == "PASS":
                    reason = (f"carrier_chainage lateral {lateral:.3f}m within "
                              f"{roadside_band:.3f}m of planned offset "
                              f"{planned_lat:.3f}m ({roadside_basis})")
            elif verdict == "PASS" and span_lo is None and residual > tol["carrier_span_residual_m"]:
                verdict = "FAIL"
                reason = (f"attachment residual {residual:.3f}m beyond "
                          f"{tol['carrier_span_residual_m']}m")
            if verdict == "PASS" and orientation > tol["carrier_orientation_deg"]:
                verdict = "FAIL"
                reason = (f"orientation residual {orientation:.3f}deg beyond "
                          f"{tol['carrier_orientation_deg']}deg")
        usage = host_usage_code(mass_hit) if mass_hit else None
        emit({"feature_id": fid, "inst_name": inst.get("inst_name"),
              "representation_id": (plan_rows.get(fid) or {}).get("representation_id"),
              "canonical_host_id": host, "anchor_kind": anchor,
              "attachment_basis": basis,
              "planned_position": list(planned), "emitted_position": list(emitted),
              "host_distance_m": round(lateral, 3), "attachment_residual_m": round(residual, 3),
              "orientation_residual_deg": round(orientation, 3), "collision_state": collision,
              "host_usage_code": usage,
              "verdict": verdict, "reason": reason, "class_group": group})

    # -- station facade: planned = resolve(plan attachment) --------------------
    # SPATIAL-04: any emitted station-facade product present in the build
    # directory gets exactly one row. An empty product list yields zero
    # rows; evaluation mirrors the fixture lane (resolve + residual +
    # burial), with the existing tolerance selected by anchor kind.
    for inst in facade_insts:
        fid = str(inst.get("feature_id") or inst.get("canonical_id") or "unknown_facade")
        if isinstance(inst.get("transform"), (list, tuple)) and len(inst["transform"]) >= 14:
            emitted = (float(inst["transform"][12]), float(inst["transform"][13]))
        elif inst.get("E") is not None and inst.get("N") is not None:
            emitted = to_world(float(inst["E"]), float(inst["N"]))
        else:
            contract_missing.append({"feature_id": fid, "inst_name": inst.get("inst_name"),
                                     "missing": ["emitted_position"]})
            emit({"feature_id": fid, "inst_name": inst.get("inst_name"),
                  "representation_id": (plan_rows.get(fid) or {}).get("representation_id"),
                  "canonical_host_id": None, "anchor_kind": "station_facade",
                  "attachment_basis": f"feature:{fid}",
                  "planned_position": None, "emitted_position": None,
                  "host_distance_m": None, "attachment_residual_m": None,
                  "orientation_residual_deg": None, "collision_state": "clear",
                  "verdict": "UNRESOLVED",
                  "reason": "attachment contract missing: emitted_position",
                  "class_group": "station_facade"})
            continue
        emitted_utm = to_utm(*emitted)
        feat = features.get(fid)
        group = "station_facade"
        geom = (feat or {}).get("geometry") or {}
        coords = geom.get("coordinates")
        if (feat is None or geom.get("kind") != "point"
                or not isinstance(coords, (list, tuple)) or len(coords) < 2):
            contract_missing.append({"feature_id": fid, "inst_name": inst.get("inst_name"),
                                     "missing": ["canonical_station_anchor_point"]})
            emit({"feature_id": fid, "inst_name": inst.get("inst_name"),
                  "representation_id": (plan_rows.get(fid) or {}).get("representation_id"),
                  "canonical_host_id": fid, "anchor_kind": "station_facade_anchor",
                  "attachment_basis": "station_facade_reconstruction.anchor.osm_ref",
                  "planned_position": None, "emitted_position": list(emitted),
                  "host_distance_m": None, "attachment_residual_m": None,
                  "orientation_residual_deg": None, "collision_state": "clear",
                  "verdict": "UNRESOLVED",
                  "reason": "attachment contract missing: canonical_station_anchor_point",
                  "class_group": group})
            continue
        anchor = "station_facade_anchor"
        host = fid
        planned_utm = (float(coords[0]), float(coords[1]))
        # FL-4876 COHERENCE-ZERO-01: an authored offset_facade_normal decision
        # moves the plane along its planned normal by the distance the station
        # facade owner records in spatial_basis; the planned pose is that
        # offset anchor, so the decided offset is not reported as drift.
        _sad = inst.get("authored_spatial_decision") or {}
        _off = re.search(r"outward_normal_offset:([\d.]+)m", str(inst.get("spatial_basis") or ""))
        _pn = inst.get("planned_normal")
        if (_sad.get("action") == "offset_facade_normal" and _off
                and isinstance(_pn, (list, tuple)) and len(_pn) >= 2):
            planned_utm = (planned_utm[0] + float(_pn[0]) * float(_off.group(1)),
                           planned_utm[1] + float(_pn[1]) * float(_off.group(1)))
            # FL-4876 STATION-RAIL-01: the decided slide along the plane
            # tangent is part of the planned pose too.
            _lat = re.search(
                r"lateral_shift_along_tangent:(-?[\d.]+)m",
                str(inst.get("spatial_basis") or ""))
            _pt = inst.get("planned_tangent")
            if _lat and isinstance(_pt, (list, tuple)) and len(_pt) >= 2:
                planned_utm = (planned_utm[0] + float(_pt[0]) * float(_lat.group(1)),
                               planned_utm[1] + float(_pt[1]) * float(_lat.group(1)))
        planned = to_world(*planned_utm)
        residual = math.dist(emitted_utm, planned_utm)
        planned_tangent = inst.get("planned_tangent")
        orientation = None
        if (isinstance(planned_tangent, (list, tuple))
                and len(planned_tangent) >= 2
                and isinstance(inst.get("transform"), (list, tuple))
                and len(inst["transform"]) >= 2):
            expected_yaw = math.degrees(math.atan2(
                float(planned_tangent[1]), float(planned_tangent[0])))
            emitted_yaw = math.degrees(math.atan2(
                float(inst["transform"][1]), float(inst["transform"][0])))
            orientation = _yaw_diff_deg(emitted_yaw, expected_yaw)
        residual_tol = tol["freestanding_residual_m"]
        collision, verdict = "clear", "PASS"
        reason = f"{anchor} residual {residual:.3f}m within {residual_tol}m"
        mass_skip = {host} if host in mass_rings else set()
        mass_hit = _inside_mass(emitted, mass_rings, mass_skip)
        if mass_hit is not None:
            collision, verdict = f"inside_mass:{mass_hit}", "FAIL"
            reason = f"emitted position inside non-host mass {mass_hit}"
        elif residual > residual_tol:
            verdict = "FAIL"
            reason = (f"attachment residual {residual:.3f}m beyond {residual_tol}m")
        elif (orientation is not None
              and orientation > tol["facade_orientation_deg"]):
            verdict = "FAIL"
            reason = (f"station facade orientation residual {orientation:.3f}deg beyond "
                      f"{tol['facade_orientation_deg']}deg")
        usage = host_usage_code(mass_hit) if mass_hit else None
        emit({"feature_id": fid, "inst_name": inst.get("inst_name"),
              "representation_id": (plan_rows.get(fid) or {}).get("representation_id"),
              "canonical_host_id": host, "anchor_kind": anchor,
              "attachment_basis": "station_facade_reconstruction.anchor.osm_ref",
              "planned_position": list(planned), "emitted_position": list(emitted),
              "host_distance_m": None, "attachment_residual_m": round(residual, 3),
              "orientation_residual_deg": (round(orientation, 3)
                                           if orientation is not None else None),
              "collision_state": collision,
              "host_usage_code": usage,
              "verdict": verdict, "reason": reason, "class_group": group})

    # -- surface decals: planned = own centerline; burial is the test --------
    step = tol["dedup_chainage_step_m"]

    def _material_insets(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "material_inset":
                    yield v
                else:
                    yield from _material_insets(v)
        elif isinstance(node, list):
            for v in node:
                yield from _material_insets(v)

    _bake_receipt_path = build_dir / "building_mass_receipt.json"
    _insets = (list(_material_insets(json.loads(_bake_receipt_path.read_text(encoding="utf-8"))))
               if _bake_receipt_path.is_file() else [])
    _bake_overwrites_footprints = bool(_insets) and all(
        isinstance(v, (int, float)) and float(v) <= 0.0 for v in _insets)
    for link in surface.get("features", []):
        fid = str(link.get("feature_id"))
        plan_row = plan_rows.get(fid) or {}
        feat = features.get(fid)
        if feat is None:
            contract_missing.append({"feature_id": fid, "inst_name": None,
                                     "missing": ["canonical_feature"]})
            emit({"feature_id": fid, "inst_name": None,
                  "representation_id": plan_row.get("representation_id"),
                  "canonical_host_id": fid, "anchor_kind": "surface_decal",
                  "attachment_basis": f"feature:{fid}",
                  "planned_position": None, "emitted_position": None,
                  "host_distance_m": None, "attachment_residual_m": None,
                  "orientation_residual_deg": None, "collision_state": "clear",
                  "host_usage_code": None,
                  "verdict": "UNRESOLVED",
                  "reason": "attachment contract missing: canonical_feature",
                  "class_group": "surface_decal"})
            continue
        group = _group_for(feat["semantic_class"], tags_of(feat))
        components = link.get("generated_components") or []
        resolved_points = [
            component.get("resolved_position_epsg32654")
            for component in components
            if component.get("resolved_position_epsg32654") is not None
        ]
        if len(resolved_points) > 1:
            contract_missing.append({"feature_id": fid, "inst_name": None,
                                     "missing": ["single_resolved_decal_position"]})
        _decal_att = (plan_rows.get(fid) or {}).get("attachment") or {}
        if resolved_points:
            line = [to_world(float(resolved_points[0][0]),
                             float(resolved_points[0][1]))]
            decal_basis = "surface_receipt.resolved_position_epsg32654"
        elif ((feat.get("geometry") or {}).get("kind") == "point"
                and _decal_att.get("record_type") == "attachment"):
            # FL-4876 COHERENCE-ZERO-01: a point decal is painted at
            # resolve(plan attachment) (compile_surface_stamp_plan.py), the
            # position the attachment layer reconciled out of masses within
            # the observation's uncertainty. Testing the raw canonical point
            # instead judged a position nothing is painted at.
            _dpose = resolve(_decal_att, index)
            line = [to_world(float(_dpose["E"]), float(_dpose["N"]))]
            decal_basis = "plan_attachment_resolve"
        else:
            line = [to_world(x, y) for x, y in _sample_points(feat["geometry"])]
            decal_basis = f"feature:{fid}"
        if not line:
            contract_missing.append({"feature_id": fid, "inst_name": None,
                                     "missing": ["sampled_geometry"]})
            emit({"feature_id": fid, "inst_name": None,
                  "representation_id": plan_row.get("representation_id"),
                  "canonical_host_id": fid, "anchor_kind": "surface_decal",
                  "attachment_basis": f"feature:{fid}",
                  "planned_position": None, "emitted_position": None,
                  "host_distance_m": None, "attachment_residual_m": None,
                  "orientation_residual_deg": None, "collision_state": "clear",
                  "host_usage_code": None,
                  "verdict": "UNRESOLVED",
                  "reason": "attachment contract missing: sampled_geometry",
                  "class_group": "surface_decal"})
            continue
        samples = [line[0]]
        acc = 0.0
        for a, b in zip(line, line[1:]):
            seg = math.dist(a, b)
            while acc + seg >= step:
                take = step - acc
                frac = take / seg if seg > 0 else 0.0
                nxt = (a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac)
                samples.append(nxt)
                a = nxt
                seg = math.dist(b, a)
                acc = 0.0
            acc += seg
        dot_component = next((component for component in components
                              if component.get("kind") in {
                                  "road_marking_dot", "fixture_decal_dot"}), None)
        dot_radius_world = (float(link.get("width_m") or 0.0) * scale / 2.0
                            if dot_component is not None else 0.0)
        buried = None
        for sample in samples:
            hit = (_disc_overlaps_mass(sample, dot_radius_world, mass_rings)
                   if dot_radius_world > 0.0
                   else _inside_mass(sample, mass_rings, set()))
            if hit is not None:
                buried = hit
                break
        centroid = (sum(p[0] for p in line) / len(line), sum(p[1] for p in line) / len(line))
        collision, verdict, reason = "clear", "PASS", "decal follows own centerline"
        attachment, missing = plan_pose(fid)
        anchor = (attachment or {}).get("anchor_kind", "carrier_span")
        host = ((attachment or {}).get("canonical_mass_id")
                or (attachment or {}).get("canonical_building_id")
                or (attachment or {}).get("canonical_carrier_id") or fid)
        if attachment is None:
            contract_missing.append({"feature_id": fid, "inst_name": None,
                                     "missing": missing})
            verdict = "UNRESOLVED"
            reason = f"attachment contract missing: {','.join(missing)}"
        elif (buried is not None and _bake_overwrites_footprints
                and (feat.get("geometry") or {}).get("kind") in ("polyline", "polygon")):
            # FL-4876 COHERENCE-ZERO-01: the surface stamp runs before the
            # building bake (sbu_e2e_run.py), and the bake writes building
            # material into every visual cell touching the footprint
            # (bake_osm_building_footprints.py, material_inset 0.0, recorded
            # in building_mass_receipt.json). The part of a line or area decal
            # inside the footprint is therefore overwritten, not painted on
            # the mass. Point decals stay strict: a whole marking buried under
            # a building is lost.
            collision = f"under_baked_mass_overwritten:{buried}"
            reason = (f"decal cells inside {buried} are overwritten by the building "
                      "bake (material_inset 0.0, bake after surface stamp)")
        elif buried is not None:
            collision, verdict = f"inside_mass:{buried}", "FAIL"
            reason = f"decal painted over baked mass {buried}"
        usage = host_usage_code(buried) if buried else None
        emit({"feature_id": fid, "inst_name": None,
              "representation_id": plan_row.get("representation_id"),
              "canonical_host_id": host, "anchor_kind": anchor,
              "attachment_basis": decal_basis,
              "planned_position": list(centroid), "emitted_position": list(centroid),
              "host_distance_m": 0.0, "attachment_residual_m": 0.0,
              "orientation_residual_deg": None, "collision_state": collision,
              "host_usage_code": usage,
              "verdict": verdict, "reason": reason, "class_group": group})

    # -- terrain masses: planned = resolve(mass attachment) -------------------
    # SPATIAL-04: every baked mass is evaluated. Out-of-corridor masses
    # keep an explicit route-scope state via emit(); degenerate rings get
    # an explicit contract-missing row instead of silence.
    for row in buildings:
        fid = str(row.get("feature_id"))
        ring = row.get("footprint") or []
        if len(ring) < 3:
            contract_missing.append({"feature_id": fid, "inst_name": row.get("inst_name"),
                                     "missing": ["footprint_ring"]})
            emit({"feature_id": fid, "inst_name": row.get("inst_name"),
                  "representation_id": (plan_rows.get(fid) or {}).get("representation_id"),
                  "canonical_host_id": fid, "anchor_kind": "terrain_mass",
                  "attachment_basis": f"feature:{fid}",
                  "planned_position": None, "emitted_position": None,
                  "host_distance_m": None, "attachment_residual_m": None,
                  "orientation_residual_deg": None, "collision_state": "clear",
                  "verdict": "UNRESOLVED",
                  "reason": "attachment contract missing: footprint_ring",
                  "class_group": "buildings"})
            continue
        centroid = (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))
        attachment, missing = plan_pose(fid)
        if attachment is None:
            contract_missing.append({"feature_id": fid, "inst_name": row.get("inst_name"),
                                     "missing": missing})
            emit({"feature_id": fid, "inst_name": row.get("inst_name"),
                  "representation_id": (plan_rows.get(fid) or {}).get("representation_id"),
                  "canonical_host_id": fid, "anchor_kind": "terrain_mass",
                  "attachment_basis": f"feature:{fid}",
                  "planned_position": None, "emitted_position": list(centroid),
                  "host_distance_m": None, "attachment_residual_m": None,
                  "orientation_residual_deg": None, "collision_state": "clear",
                  "verdict": "UNRESOLVED",
                  "reason": f"attachment contract missing: {','.join(missing)}",
                  "class_group": "buildings"})
            continue
        host = attachment.get("canonical_mass_id") or fid
        pose = resolve(attachment, index)
        planned_utm = (float(pose["E"]), float(pose["N"]))
        planned = to_world(*planned_utm)
        residual = math.dist(to_utm(*centroid), planned_utm)
        verdict, reason = "PASS", "mass baked from own canonical ring"
        if residual > tol["facade_edge_tolerance_m"]:
            verdict, reason = "FAIL", (f"attachment residual {residual:.3f}m beyond "
                                       f"{tol['facade_edge_tolerance_m']}m")
        emit({"feature_id": fid, "inst_name": row.get("inst_name"),
              "representation_id": (plan_rows.get(fid) or {}).get("representation_id"),
              "canonical_host_id": host, "anchor_kind": "terrain_mass",
              "attachment_basis": f"feature:{fid}",
              "planned_position": list(planned), "emitted_position": list(centroid),
              "host_distance_m": 0.0, "attachment_residual_m": round(residual, 3),
              "orientation_residual_deg": None, "collision_state": "clear",
              "verdict": verdict, "reason": reason,
              "class_group": "buildings"})

    # FL-4876 COVERAGE-01: place fronts and infrastructure assemblies are
    # emitters too, but this receipt never verified them, so the acceptance
    # chain failed 8 place fronts and 4 grade-crossing rows as "emitted but no
    # spatial row (verifier coverage gap)".
    #
    # Place front: the anchor must lie on its parent building's selected
    # face (within the front stand-off), its outward normal must point out
    # of the parent ring, and it must not sit inside any other mass.
    _front_path = build_dir / "place_front_instances.json"
    _front_tol = tol["facade_edge_tolerance_m"]
    for inst in (json.loads(_front_path.read_text(encoding="utf-8"))
                 if _front_path.is_file() else []):
        fid = str(inst.get("feature_id"))
        anchor_xy = inst.get("front_anchor_world_xy")
        parent = str(inst.get("parent_building_feature_id") or "")
        ring = mass_rings.get(parent)
        if not (isinstance(anchor_xy, (list, tuple)) and len(anchor_xy) >= 2) or ring is None:
            emit({"feature_id": fid, "inst_name": inst.get("inst_name"),
                  "representation_id": inst.get("representation_id"),
                  "canonical_host_id": parent or None, "anchor_kind": "place_front_face",
                  "attachment_basis": "place_front.parent_building_face",
                  "planned_position": None,
                  "emitted_position": list(anchor_xy) if anchor_xy else [0.0, 0.0],
                  "host_distance_m": None, "attachment_residual_m": None,
                  "orientation_residual_deg": None, "collision_state": "clear",
                  "verdict": "UNRESOLVED",
                  "reason": "attachment contract missing: front anchor or parent ring",
                  "class_group": "place_front"})
            continue
        anchor_xy = (float(anchor_xy[0]), float(anchor_xy[1]))
        face_d = metres(_dist_to_ring(anchor_xy, ring))
        normal = inst.get("front_outward_normal_xy") or [0.0, 0.0]
        probe = (anchor_xy[0] + float(normal[0]) * scale,
                 anchor_xy[1] + float(normal[1]) * scale)
        outward = not point_in_polygon(probe[0], probe[1], ring)
        mass_hit = _inside_mass(anchor_xy, mass_rings, {parent})
        verdict, collision = "PASS", "clear"
        reason = f"place front on parent face, {face_d:.3f}m from it"
        if mass_hit is not None:
            verdict, collision = "FAIL", f"inside_mass:{mass_hit}"
            reason = f"place front anchor inside non-host mass {mass_hit}"
        elif face_d > _front_tol:
            verdict = "FAIL"
            reason = f"place front {face_d:.3f}m from its parent face, beyond {_front_tol}m"
        elif not outward:
            verdict = "FAIL"
            reason = "place front outward normal points into its parent building"
        emit({"feature_id": fid, "inst_name": inst.get("inst_name"),
              "representation_id": inst.get("representation_id"),
              "canonical_host_id": parent, "anchor_kind": "place_front_face",
              "attachment_basis": "place_front.parent_building_face",
              "planned_position": list(anchor_xy), "emitted_position": list(anchor_xy),
              "host_distance_m": round(face_d, 3), "attachment_residual_m": round(face_d, 3),
              "orientation_residual_deg": None, "collision_state": collision,
              "verdict": verdict, "reason": reason, "class_group": "place_front"})

    # Infrastructure grade crossing: one assembly stands for its grouped
    # source crossing points; its origin must lie within the crossing's own
    # extent (the farthest source point plus the barrier arm span) of their
    # centroid, and every grouped source feature gets the same row.
    _infra_path = build_dir / "infrastructure_receipt.json"
    _infra = (json.loads(_infra_path.read_text(encoding="utf-8"))
              if _infra_path.is_file() else {})
    for inst in _infra.get("instances") or []:
        if inst.get("semantic_class") != "railway_crossing_equipment_proposal":
            continue
        tr = inst.get("transform") or []
        if len(tr) < 14:
            continue
        emitted = (float(tr[12]), float(tr[13]))
        pts = []
        for efid in inst.get("evidence_feature_ids") or []:
            g = ((features.get(efid) or {}).get("geometry") or {}).get("coordinates")
            if isinstance(g, (list, tuple)) and len(g) >= 2 and isinstance(g[0], (int, float)):
                pts.append(to_world(float(g[0]), float(g[1])))
        for efid in inst.get("evidence_feature_ids") or []:
            if not pts:
                verdict, reason, residual = "UNRESOLVED", "no canonical crossing points", None
            else:
                cen = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
                residual = metres(math.dist(emitted, cen))
                spread = max(metres(math.dist(cen, p)) for p in pts)
                arm = float((inst.get("dimensions") or {}).get("barrier_arm_span_m") or 0.0)
                limit = spread + arm
                verdict = "PASS" if residual <= limit else "FAIL"
                reason = (f"crossing assembly {residual:.3f}m from its {len(pts)} source "
                          f"points' centroid, limit {limit:.3f}m")
            emit({"feature_id": str(efid), "inst_name": inst.get("inst_name"),
                  "representation_id": "proxy:crossing_marker:" + str(efid),
                  "canonical_host_id": inst.get("feature_id"),
                  "anchor_kind": "infrastructure_crossing",
                  "attachment_basis": "infrastructure.crossing_source_points",
                  "planned_position": list(emitted), "emitted_position": list(emitted),
                  "host_distance_m": None,
                  "attachment_residual_m": round(residual, 3) if residual is not None else None,
                  "orientation_residual_deg": None, "collision_state": "clear",
                  "verdict": verdict, "reason": reason, "class_group": "infrastructure"})

    # FL-4876 STATION-STOP-MAP-01: the station node's platforms and station rail fence are infrastructure
    # assemblies too. While the node also carried a generic sign plate (a fixtures row) that row stood in for it;
    # with the node drawn only as its platforms, the assemblies themselves are verified like a crossing: the
    # origin must lie within the assembly's own extent (half the platform length, or the fence's station radius)
    # of the centroid of its point source features (the station and stop-position nodes).
    for inst in _infra.get("instances") or []:
        sclass = inst.get("semantic_class")
        if sclass not in ("station_platform_proposal", "rail_corridor_fence_proposal"):
            continue
        tr = inst.get("transform") or []
        if len(tr) < 14:
            continue
        emitted = (float(tr[12]), float(tr[13]))
        pts = []
        for efid in inst.get("evidence_feature_ids") or []:
            g = ((features.get(efid) or {}).get("geometry") or {}).get("coordinates")
            if isinstance(g, (list, tuple)) and len(g) >= 2 and isinstance(g[0], (int, float)):
                pts.append(to_world(float(g[0]), float(g[1])))
        dims = inst.get("dimensions") or {}
        limit = (float(dims.get("platform_length_m") or 0.0) / 2.0
                 if sclass == "station_platform_proposal" else float(dims.get("station_radius_m") or 0.0))
        if not pts:
            verdict, reason, residual = "UNRESOLVED", "no point source features for the station assembly", None
        else:
            cen = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            residual = metres(math.dist(emitted, cen))
            verdict = "PASS" if residual <= limit else "FAIL"
            reason = (f"station assembly {residual:.3f}m from its {len(pts)} source points' centroid, "
                      f"limit {limit:.3f}m")
        emit({"feature_id": str(inst.get("feature_id")), "inst_name": inst.get("inst_name"),
              "representation_id": "proxy:station_platform:" + str(inst.get("feature_id")),
              "canonical_host_id": inst.get("feature_id"),
              "anchor_kind": "infrastructure_station",
              "attachment_basis": "infrastructure.station_source_points",
              "planned_position": list(emitted), "emitted_position": list(emitted),
              "host_distance_m": None,
              "attachment_residual_m": round(residual, 3) if residual is not None else None,
              "orientation_residual_deg": None, "collision_state": "clear",
              "verdict": verdict, "reason": reason, "class_group": "infrastructure"})

    # SPATIAL-04: every emitted row is kept, so the missing-contract list
    # needs no corridor scoping; it names rows already present below.
    emitted_keys = {(r["feature_id"], r.get("inst_name")) for r in rows}
    contract_missing = [r for r in contract_missing
                        if (r["feature_id"], r.get("inst_name")) in emitted_keys]
    # Exact operator adjudications may change only the named failure mode.
    # An accepted gantry rule cannot hide a later mass collision, and an
    # axial-orientation decision cannot hide a lateral-placement failure.
    manual_adjudications = apply_authored_adjudications(
        rows, spatial_decisions, tol["carrier_orientation_deg"])

    rows.sort(key=lambda r: (r["bin"] is None, r["bin"] or 0,
                             r["feature_id"], r.get("inst_name") or ""))
    by_verdict = Counter(r["verdict"] for r in rows)
    by_anchor = Counter(r["anchor_kind"] for r in rows)
    by_group = Counter(r.get("class_group", "other") for r in rows)
    by_bin = Counter("out_of_corridor" if r["bin"] is None else r["bin"] for r in rows)
    by_scope = Counter(r.get("route_scope", "in_corridor") for r in rows)
    by_basis = Counter(r.get("attachment_basis", "unknown").split(":")[0] for r in rows)
    fails = [r for r in rows if r["verdict"] == "FAIL"]
    document = {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "inputs": {
            "route": str(route_path), "world_ir": str(world_ir_path),
            "plan": str(plan_path),
            "build_dir": str(build_dir),
            "population_fixture_instances": str(_fixture_path),
            "population_plan": str(build_dir / "plan" / "population" / "population_plan.json"),
            "population_custom_instances": str(_custom_path),
            "station_facade_instances": str(_facade_path),
            "authored_spatial_decisions": spatial_decisions["path"],
            "authored_spatial_decisions_sha256": spatial_decisions["sha256"],
        },
        "inputs_absent": inputs_absent,
        "tolerances": {key: {"value": value[0], "provenance": value[1]}
                       for key, value in SPATIAL_DEFAULTS.items()},
        "tolerance_basis": rules_basis,
        "ruling_basis": {
            "roadside_band_m": {"value": roadside_band,
                                 "basis": roadside_basis},
            "inside_mass": {
                "basis": "planned host/attachment only",
                "verifier_side_semantic_exceptions": [],
            },
        },
        "corridor_m": corridor_m,
        "bin_length_m": bin_length_m,
        # SPATIAL-04 provenance: rings canonical-closed in memory (source
        # coordinates untouched); linear poses resolve by placement
        # attachment_id; fixture/custom rows record emitted-vs-plan ownership.
        "ring_closure": "mass rings canonical-closed in memory via _closed; source coordinates unchanged",
        "placement_join": {"note": placement_note,
                           "attachment_ids": len(place_by_aid),
                           "placements": len(place_by_pid)},
        "authored_spatial_adjudications": manual_adjudications,
        "emitted_denominator": {
            "fixtures": len(fixtures),
            "linear_instances": len(linear),
            "linear_span_records": sum(1 for r in linear if r.get("span_record")),
            "population_custom_identities": len(custom),
            "storefront_fronts": sum(
                1 for row in fixtures
                if (row.get("attachment") or {}).get("anchor_kind")
                == "building_face"
                or row.get("parent_building_feature_id") is not None),
            # Missing evidence is unknown, never a measured zero.  BUILD-14's
            # stale receipt turned an absent denominator sidecar into the
            # false claim that the station emitted nothing.
            "station_facade": (None if _facade_absent else len(facade_insts)),
            "building_masses": len(buildings),
            "building_masses_degenerate": len(buildings_degenerate),
            "surface_features": len(surface.get("features", [])),
        },
        "counts": {
            "rows": len(rows),
            "by_verdict": dict(sorted(by_verdict.items())),
            "by_anchor_kind": dict(sorted(by_anchor.items())),
            "by_class_group": dict(sorted(by_group.items())),
            "by_bin": {str(k): v for k, v in sorted(by_bin.items(), key=lambda kv: str(kv[0]))},
            "by_route_scope": dict(sorted(by_scope.items())),
            "by_attachment_basis": dict(sorted(by_basis.items())),
            "out_of_corridor": int(by_scope.get("out_of_corridor", 0)),
            "fail": len(fails),
            "unresolved": int(by_verdict.get("UNRESOLVED", 0)),
            "attachment_contract_missing": len(contract_missing),
            "input_contract_missing": len(inputs_absent),
        },
        "attachment_contract_missing": sorted(contract_missing, key=lambda r: r["feature_id"]),
        "fail_rows": fails,
        "rows": rows,
    }
    receipt = {
        "schema": "y92.spatial_coherence_receipt.v1",
        "generated_at": generated_at,
        "counts": document["counts"],
        "inputs_absent": inputs_absent,
        "ruling_basis": document["ruling_basis"],
        "fail_feature_ids": sorted({r["feature_id"] for r in fails}),
        "stage": ("failed_verdicts_present" if fails
                  else "input_contract_missing" if inputs_absent
                  else "unresolved_verdicts_present"
                  if by_verdict.get("UNRESOLVED") else "all_pass"),
    }
    return document, receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--route", type=Path, required=True)
    ap.add_argument("--world-ir", type=Path, required=True)
    ap.add_argument("--terrain-metadata", type=Path, required=True)
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--build-dir", type=Path, required=True)
    ap.add_argument("--rules", type=Path, default=None)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--receipt-out", type=Path, required=True)
    ap.add_argument("--authored-spatial-decisions", type=Path)
    ap.add_argument("--jev-input-out", type=Path,
                    help="write bounded JEV review jobs for every remaining FAIL")
    ap.add_argument("--jev-spatial-candidates", type=Path,
                    help="photo/LiDAR candidate evidence for concrete JEV choices")
    ap.add_argument("--generated-at", default="2026-09-19T00:00:00Z")
    args = ap.parse_args(argv)
    document, receipt = build(
        args.route, args.world_ir, args.terrain_metadata, args.plan,
        args.build_dir, args.rules, args.generated_at,
        args.authored_spatial_decisions)
    args.out.write_text(json.dumps(document, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    args.receipt_out.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if args.jev_input_out is not None:
        from scripts.pipeline.spatial_coherence_review import (
            load_decisions, write_jev_input,
        )
        population_plan_path = (
            args.build_dir / "plan" / "population" / "population_plan.json")
        write_jev_input(
            document, coherence_path=args.out,
            out_path=args.jev_input_out,
            decisions=load_decisions(args.authored_spatial_decisions),
            population_plan=(json.loads(population_plan_path.read_text(
                encoding="utf-8")) if population_plan_path.is_file() else None),
            population_plan_path=(
                population_plan_path if population_plan_path.is_file()
                else None),
            spatial_candidate_evidence=(
                json.loads(args.jev_spatial_candidates.read_text(
                    encoding="utf-8"))
                if args.jev_spatial_candidates is not None else None),
            spatial_candidate_evidence_path=args.jev_spatial_candidates)
    print(f"[spatial-coherence] rows={document['counts']['rows']} "
          f"fail={document['counts']['fail']} "
          f"unresolved={document['counts']['unresolved']}")
    for row in document["fail_rows"][:10]:
        print(f"  FAIL {row['feature_id']} {row.get('inst_name')} "
              f"{row['collision_state']} host={row['canonical_host_id']} {row['reason'][:100]}")
    return 1 if (document["counts"]["fail"]
                 or document["counts"]["unresolved"]
                 or document["counts"]["input_contract_missing"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
