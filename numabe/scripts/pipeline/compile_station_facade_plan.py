#!/usr/bin/env python3
"""Compile the station facade's semantic placement before lowering.

The accepted photogrammetric reconstruction owns this facade plane. Sightings
that resolve_identity_entities.py linked to the station-assembly entity are
evidence for that one assembly, not duplicate custom panels.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


SCHEMA = "y92.station_facade_plan.v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# FL-4876 STATION-RAIL-01. Least distance a station facade plane may keep from
# any rail route centreline: half the 1.067 m gauge plus the platform-edge
# strip.  The 7.15 m reconstruction plane lay across the rails at 0.02 m.
RAIL_CLEARANCE_M = 1.9


class FacadeCrossesRail(ValueError):
    """The placed facade plane crosses, or lies too close to, a rail route."""


def rail_routes_from_canonical(canonical_ir_path: Path,
                               line_name_en: str = "Tokyu Tamagawa Line",
                               ) -> list[list[tuple[float, float]]]:
    """Rail route polylines (EPSG:32654 E,N) the way compile_classic_train_routes
    chains them: canonical railway centrelines of the configured line, joined
    by end point, the two longest kept.  Empty when the line is absent."""
    from scripts.pipeline.compile_classic_train_routes import (
        _chains, _length, _tags)
    canon = json.loads(Path(canonical_ir_path).read_text(encoding="utf-8"))
    obs = {o["observation_id"]: o for o in canon.get("source_observations", [])}
    lines = []
    for feature in canon.get("canonical_features", []):
        geometry = feature.get("geometry") or {}
        if (feature.get("semantic_class") != "railway_boundary"
                or geometry.get("kind") != "polyline"):
            continue
        tags = _tags(feature, obs)
        if tags.get("name:en") == line_name_en and tags.get("railway") == "rail":
            lines.append((feature["feature_id"], geometry["coordinates"]))
    chains = sorted(_chains(lines), key=lambda c: -_length(c[1]))
    return [[(float(p[0]), float(p[1])) for p in pl] for _, pl in chains[:2]]


def rail_routes_from_trains(trains_path: Path, terrain_metadata_path: Path,
                            ) -> list[list[tuple[float, float]]]:
    """Rail route polylines (E,N) from a compiled output.trains.json (map
    units) using the build's terrain projection."""
    trains = json.loads(Path(trains_path).read_text(encoding="utf-8"))
    meta = json.loads(Path(terrain_metadata_path).read_text(encoding="utf-8"))
    scale = float(meta["content_scale"])
    return [[((float(p[0]) - float(meta["offset_x"])) / scale
              + float(meta["origin_easting"]),
              (float(p[1]) - float(meta["offset_y"])) / scale
              + float(meta["origin_northing"])) for p in route["polyline"]]
            for route in trains.get("routes", [])]


def _segment_distance(a, b, c, d) -> float:
    """Least distance between segments ab and cd (planar)."""
    def point_seg(p, s, e):
        dx, dy = e[0] - s[0], e[1] - s[1]
        length2 = dx * dx + dy * dy
        t = 0.0 if length2 == 0 else max(0.0, min(
            1.0, ((p[0] - s[0]) * dx + (p[1] - s[1]) * dy) / length2))
        return math.hypot(p[0] - s[0] - t * dx, p[1] - s[1] - t * dy)

    def ccw(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    if (ccw(a, b, c) * ccw(a, b, d) < 0) and (ccw(c, d, a) * ccw(c, d, b) < 0):
        return 0.0
    return min(point_seg(a, c, d), point_seg(b, c, d),
               point_seg(c, a, b), point_seg(d, a, b))


def assert_plane_clear_of_rails(plane_ends_en, routes_en,
                                clearance_m: float = RAIL_CLEARANCE_M) -> float:
    """Refuse a facade plane (two EPSG:32654 end points) whose least distance
    to any rail route centreline is under clearance_m.  Returns that distance."""
    a, b = plane_ends_en
    least = float("inf")
    for pl in routes_en:
        for s, e in zip(pl, pl[1:]):
            least = min(least, _segment_distance(a, b, s, e))
    if least < clearance_m:
        raise FacadeCrossesRail(
            f"station facade plane lies {least:.2f} m from a rail route "
            f"centreline (minimum {clearance_m} m): it crosses the rails; "
            "re-author outward_offset_m / lateral_shift_m in the authored "
            "spatial decision")
    return least


def _plane_ends_utm(reconstruction: dict, outward_offset_m: float,
                    lateral_shift_m: float):
    """EPSG:32654 end points of the placed plane (portal u extent along the
    plane tangent, after the authored outward offset and lateral shift)."""
    from scripts.pipeline import geo_frames as GF
    from scripts.pipeline.camera_solve.geodesy import EnuFrame
    root = Path(__file__).resolve().parents[2]
    declared = reconstruction["inputs"]["camera_receipt"]
    receipt_path = root / declared["path"]
    if _sha256(receipt_path) != declared["sha256"]:
        raise ValueError("station camera receipt differs from reconstruction")
    origin = json.loads(receipt_path.read_text(encoding="utf-8"))[
        "corridor"]["enu_origin_wgs84"]
    frame = EnuFrame(origin["longitude"], origin["latitude"],
                     origin["ellipsoidal_height_m"])
    plane = reconstruction["plane"]
    extent = reconstruction["portal"]["extent_uv_m"]
    o, t, n = plane["origin_enu_m"], plane["horizontal_tangent_enu"], plane[
        "normal_enu"]
    ends = []
    for u in (float(extent["u_min"]), float(extent["u_max"])):
        e = (float(o[0]) + float(n[0]) * outward_offset_m
             + float(t[0]) * (lateral_shift_m + u))
        nn = (float(o[1]) + float(n[1]) * outward_offset_m
              + float(t[1]) * (lateral_shift_m + u))
        lon, lat, _ = frame.to_geodetic(e, nn, 0.0)
        ends.append(GF.utm_forward(lon, lat))
    return ends[0], ends[1]


def build(world_ir_path: Path, reconstruction_path: Path,
          requirements_path: Path, template_root: Path,
          registered_instances_path: Path,
          object_observations_path: Path, capture_index_path: Path,
          meshes_out: Path, content_scale: float,
          authored_spatial_decisions_path: Path | None = None,
          rail_routes_en: list | None = None) -> dict:
    world = json.loads(world_ir_path.read_text(encoding="utf-8"))
    reconstruction = json.loads(
        reconstruction_path.read_text(encoding="utf-8"))
    if not reconstruction.get("gate", {}).get("pass"):
        raise ValueError("station facade reconstruction is not accepted")
    facades = [row for row in world.get("canonical_features", [])
               if row.get("semantic_class") == "facade_plane"]
    signs = [row for row in world.get("canonical_features", [])
             if row.get("semantic_class") == "sign"]
    if len(facades) != 1 or len(signs) != 1:
        raise ValueError(
            "station facade plan requires one facade plane and one sign")
    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    context = signs[0]["properties"]["template_context"]["value"]
    if context not in requirements.get("locale_contexts", {}).values():
        raise ValueError(
            "station sign template context is not declared by requirements")
    from scripts.pipeline.template_resolver import (
        discover_pack_chain, resolve_template_candidates)
    packs = discover_pack_chain(template_root, context)
    candidates = resolve_template_candidates(
        semantic_candidates=["station_identity_sign"], context=context,
        packs=packs,
        ocr_text=str(signs[0]["properties"]["text"]["value"]))
    if not candidates:
        raise ValueError("no location-compatible station identity template")
    selected_template = candidates[0]
    anchor_ref = str((reconstruction.get("anchor") or {}).get("osm_ref") or "")
    if not anchor_ref:
        raise ValueError("station facade reconstruction has no OSM anchor")
    portal = reconstruction["portal"]["extent_uv_m"]
    placement_id = "station-facade:" + facades[0]["feature_id"]
    source_instances = []
    registered = json.loads(
        Path(registered_instances_path).read_text(encoding="utf-8"))
    # FL-4876 ENTITY-01: registered_instances_path is identity_entities.json;
    # the station's sightings are the observations consumed by the mapped
    # transit entity that the station assembly owns, not a subject name.
    source_instances = sorted({
        str(iid)
        for ent in registered.get("entities", [])
        if ent.get("owner") == "station_assembly"
        for iid in (ent.get("observations") or {}).get("supporting", [])})
    sightings_basis = "registered_station_entity_observations"
    if not source_instances:
        # NUMABE-REG-01 withholds identity annotations from registration
        # (delegated to the mapped-place resolver), so the station entity may
        # carry no registered sightings.  The accepted reconstruction's own
        # portal and sign observations are the station's photographed
        # sightings; without either the station is unevidenced.
        source_instances = sorted({
            str(row["observation_id"])
            for part in ("portal", "station_identity_sign")
            for row in (reconstruction.get(part) or {}).get("observations", [])})
        sightings_basis = "accepted_reconstruction_observations"
    if not source_instances:
        raise ValueError(
            "station facade plan has no registered station sightings")

    # The station asset is authored before preassembly. The lowerer receives
    # this immutable mesh contract and may only ground and serialize it.
    from scripts.pipeline.compile_facade_shell import (
        ALPHA_NO_COLLISION,
        STATION_PORTAL_RASTER,
        STATION_SIGN_RASTER,
        _fused_crop,
        _station_ps1_front,
        write_akm,
    )
    object_observations = json.loads(
        Path(object_observations_path).read_text(encoding="utf-8"))
    capture_index = json.loads(
        Path(capture_index_path).read_text(encoding="utf-8"))
    portal_pixels, portal_sources = _fused_crop(
        [row["observation_id"]
         for row in reconstruction["portal"]["observations"]],
        object_observations, capture_index, Path(capture_index_path).parent,
        STATION_PORTAL_RASTER)
    sign_pixels, sign_sources = _fused_crop(
        [row["observation_id"]
         for row in reconstruction["station_identity_sign"]["observations"]],
        object_observations, capture_index, Path(capture_index_path).parent,
        STATION_SIGN_RASTER)
    verts, faces, authored_palette = _station_ps1_front(
        reconstruction["portal"]["extent_uv_m"], portal_pixels,
        reconstruction["station_identity_sign"]["extent_uv_m"],
        sign_pixels, content_scale=float(content_scale),
        ground_z_m=float(reconstruction["portal"]["extent_uv_m"]["z_min"]))
    facade_slug = facades[0]["feature_id"].replace(":", "_").replace("/", "_")
    mesh_name = f"facade_{facade_slug}.akm"
    instance_name = f"facade_{facade_slug}"
    from scripts.pipeline.spatial_coherence_review import (
        decision_for as spatial_decision_for,
        load_decisions as load_spatial_decisions,
    )
    spatial_decisions = load_spatial_decisions(
        authored_spatial_decisions_path)
    spatial_decision = spatial_decision_for(
        spatial_decisions, "instance", instance_name)
    outward_offset_m = 0.0
    lateral_shift_m = 0.0
    if spatial_decision is not None:
        if spatial_decision["action"] != "offset_facade_normal":
            raise ValueError(
                "station facade authored spatial decision must use "
                "offset_facade_normal")
        outward_offset_m = float(
            spatial_decision["parameters"]["outward_offset_m"])
        # FL-4876 COHERENCE-ZERO-01: same 6.0 m bound as the decision loader
        # (spatial_coherence_review.py); the entrance needs a measured 4.4 m to
        # clear the PLATEAU canopy footprint its OSM node sits inside.
        if not 0.0 < outward_offset_m <= 6.0:
            raise ValueError(
                "station facade outward offset must be within (0, 6] metres")
        lateral_shift_m = float(
            spatial_decision["parameters"].get("lateral_shift_m") or 0.0)
        if not -8.0 <= lateral_shift_m <= 8.0:
            raise ValueError(
                "station facade lateral shift must be within [-8, 8] metres")
    # FL-4876 STATION-RAIL-01: the plane is a solid card; it may not cross a
    # rail route.  The route set is supplied by the runner (canonical rail
    # centrelines or a compiled output.trains.json).
    rail_clearance_m = None
    if rail_routes_en:
        rail_clearance_m = assert_plane_clear_of_rails(
            _plane_ends_utm(reconstruction, outward_offset_m,
                            lateral_shift_m), rail_routes_en)
    meshes_out = Path(meshes_out)
    meshes_out.mkdir(parents=True, exist_ok=True)
    mesh_path = meshes_out / mesh_name
    write_akm(mesh_path, verts, faces)
    if not mesh_path.is_file() or mesh_path.stat().st_size == 0:
        raise ValueError("station facade asset authoring produced no mesh")
    return {
        "schema": SCHEMA,
        "lowering_owner": "station_facade",
        "inputs": {
            "world_ir": str(world_ir_path),
            "world_ir_sha256": _sha256(world_ir_path),
            "reconstruction": str(reconstruction_path),
            "reconstruction_sha256": _sha256(reconstruction_path),
            "requirements": str(requirements_path),
            "requirements_sha256": _sha256(requirements_path),
            "registered_instances": str(registered_instances_path),
            "registered_instances_sha256": _sha256(
                Path(registered_instances_path)),
            "object_observations_sha256": _sha256(
                Path(object_observations_path)),
            "capture_index_sha256": _sha256(Path(capture_index_path)),
            "authored_spatial_decisions": spatial_decisions["path"],
            "authored_spatial_decisions_sha256": spatial_decisions["sha256"],
        },
        "representation": {
            "selected_template": selected_template,
            "locale_pack_chain": [pack["pack_id"] for pack in packs],
            "selection_context": context,
        },
        "asset": {
            "mesh_name": mesh_name,
            "mesh_path": str(mesh_path.resolve()),
            "mesh_sha256": _sha256(mesh_path),
            "mesh_bytes": mesh_path.stat().st_size,
            "vertices": len(verts),
            "faces": len(faces),
            "alpha": ALPHA_NO_COLLISION,
            "facade_raster": list(STATION_PORTAL_RASTER),
            "sign_raster": list(STATION_SIGN_RASTER),
            "authored_palette": authored_palette,
            "source_observations": {
                "facade": portal_sources, "sign": sign_sources},
        },
        "placements": [{
            "placement_id": placement_id,
            "feature_id": facades[0]["feature_id"],
            "sign_feature_id": signs[0]["feature_id"],
            "canonical_anchor_id": f"feature:station_entrance:{anchor_ref}",
            "source_observation_instance_ids": source_instances,
            "source_observation_basis": sightings_basis,
            "attachment": {
                "record_type": "accepted_reconstruction_plane",
                "attachment_id": "attachment:" + placement_id,
                "source": "station_facade_reconstruction.plane",
                "plane_origin_enu_m": reconstruction["plane"]["origin_enu_m"],
                "horizontal_tangent_enu": reconstruction["plane"][
                    "horizontal_tangent_enu"],
                "normal_enu": reconstruction["plane"]["normal_enu"],
                "outward_offset_m": outward_offset_m,
                # signed metres along horizontal_tangent_enu, applied after
                # the outward offset; zero when the decision has no slide
                "lateral_shift_m": lateral_shift_m,
                "min_rail_route_distance_m": (
                    round(rail_clearance_m, 3)
                    if rail_clearance_m is not None else None),
                "rail_clearance_required_m": RAIL_CLEARANCE_M,
            },
            "authored_spatial_decision": ({
                "decision_id": spatial_decision["decision_id"],
                "action": spatial_decision["action"],
                "rationale": spatial_decision["rationale"],
            } if spatial_decision is not None else None),
            "asset_physical_envelope": {
                "width_m": float(portal["u_max"]) - float(portal["u_min"]),
                "depth_m": 0.045,
                "height_m": float(portal["z_max"]) - float(portal["z_min"]),
                "max_longitudinal_overhang_m": 0.0,
            },
        }],
    }


def write(world_ir_path: Path, reconstruction_path: Path,
          requirements_path: Path, template_root: Path,
          out_path: Path, registered_instances_path: Path,
          object_observations_path: Path, capture_index_path: Path,
          meshes_out: Path, content_scale: float,
          authored_spatial_decisions_path: Path | None = None,
          rail_routes_en: list | None = None) -> Path:
    document = build(
        world_ir_path, reconstruction_path, requirements_path, template_root,
        registered_instances_path, object_observations_path,
        capture_index_path, meshes_out, content_scale,
        authored_spatial_decisions_path, rail_routes_en)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
        + "\n", encoding="utf-8")
    return out_path


def require_station_plan(document: dict) -> dict:
    if (document.get("schema") != SCHEMA
            or document.get("lowering_owner") != "station_facade"):
        raise ValueError("station facade lowerer requires its typed plan")
    placements = document.get("placements") or []
    if len(placements) != 1:
        raise ValueError("station facade plan must contain exactly one placement")
    return placements[0]
