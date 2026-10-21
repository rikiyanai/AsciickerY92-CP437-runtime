#!/usr/bin/env python3
"""Associate a machine-detected entrance facade with an OSM entrance anchor.

The Numabe station entrance is a mapped point connected to a footway, not an
OSM building polygon.  A building-edge-only facade resolver therefore cannot
own it.  This owner derives a vertical entrance plane from that point and the
connected approach way, projects machine detector regions onto it through the
accepted cameras, fuses consistent regions, and emits canonical World IR.

No detector centroid is triangulated here.  The plane geometry comes from the
OSM entrance/footway and its appearance regions come from street imagery.  The
receipt keeps the back-projection error and refuses promotion when views do not
agree in facade coordinates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import cv2
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.camera_solve.geodesy import EnuFrame  # noqa: E402
from scripts.pipeline.world_ir import WorldIRStore, schema as S  # noqa: E402

MIN_PORTAL_VIEWS = 2
MIN_SIGN_VIEWS = 2
MAX_FUSED_CENTER_DEVIATION_M = 1.5
MIN_DETECTOR_CONFIDENCE = 0.25
MIN_MEDIAN_BACK_PROJECTION_IOU = 0.3


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tags(element: ET.Element) -> dict[str, str]:
    return {tag.get("k", ""): tag.get("v", "") for tag in element.findall("tag")}


def entrance_plane(osm_path: Path, osm_ref: str, frame: EnuFrame) -> dict[str, Any]:
    root = ET.parse(osm_path).getroot()
    node_id = osm_ref.removeprefix("node/")
    node_elements = [
        node for node in root.findall("node")
        if node.get("lat") and node.get("lon")
    ]
    # Overpass ``out body; >; out skel qt`` can retain a tagged body node and
    # its later skeleton copy.  Keep the tagged occurrence instead of letting
    # the skeleton silently erase its semantics in an id-keyed dictionary.
    nodes = {node.get("id"): node for node in node_elements}
    candidates = [node for node in node_elements if node.get("id") == node_id]
    node = max(candidates, key=lambda item: len(_tags(item))) if candidates else None
    if node is None:
        raise ValueError(f"entrance anchor {osm_ref} is absent from {osm_path}")
    tags = _tags(node)
    if tags.get("railway") != "train_station_entrance":
        raise ValueError(f"{osm_ref} is not a railway=train_station_entrance")

    choices = []
    for way in root.findall("way"):
        refs = [nd.get("ref") for nd in way.findall("nd")]
        if node_id not in refs:
            continue
        way_tags = _tags(way)
        if way_tags.get("highway") != "footway":
            continue
        index = refs.index(node_id)
        neighbours = []
        if index > 0:
            neighbours.append(refs[index - 1])
        if index + 1 < len(refs):
            neighbours.append(refs[index + 1])
        for neighbour_id in neighbours:
            neighbour = nodes.get(neighbour_id)
            if neighbour is not None:
                choices.append((way, way_tags, neighbour))
    if not choices:
        raise ValueError(f"{osm_ref} has no connected highway=footway direction")
    choices.sort(key=lambda item: int(item[0].get("id")))
    way, way_tags, neighbour = choices[0]

    lon = float(node.get("lon"))
    lat = float(node.get("lat"))
    origin = np.asarray(frame.to_enu(lon, lat, frame.origin_alt), dtype=float)
    neighbour_enu = np.asarray(
        frame.to_enu(float(neighbour.get("lon")), float(neighbour.get("lat")), frame.origin_alt),
        dtype=float,
    )
    normal = neighbour_enu - origin
    normal[2] = 0.0
    normal /= np.linalg.norm(normal)
    tangent = np.asarray([-normal[1], normal[0], 0.0])
    named_station_candidates = []
    for candidate in node_elements:
        candidate_tags = _tags(candidate)
        if not candidate_tags.get("name:ja"):
            continue
        if candidate_tags.get("railway") not in {"station", "stop"}:
            continue
        candidate_enu = np.asarray(frame.to_enu(
            float(candidate.get("lon")), float(candidate.get("lat")), frame.origin_alt
        ))
        named_station_candidates.append((
            float(np.linalg.norm(candidate_enu[:2] - origin[:2])), candidate,
            candidate_tags, candidate_enu,
        ))
    if not named_station_candidates:
        raise ValueError(f"{osm_ref} has no nearby named railway station identity")
    station_distance, station_node, station_tags, station_enu = min(
        named_station_candidates, key=lambda item: item[0]
    )
    if station_distance > 50.0:
        raise ValueError(
            f"nearest named railway station is {station_distance:.1f} m from {osm_ref}"
        )
    return {
        "origin": origin,
        "normal": normal,
        "tangent": tangent,
        "osm_ref": osm_ref,
        "osm_tags": tags,
        "approach_way_ref": f"way/{way.get('id')}",
        "approach_way_tags": way_tags,
        "approach_neighbour_ref": f"node/{neighbour.get('id')}",
        "station_name_ref": f"node/{station_node.get('id')}",
        "station_name_tags": station_tags,
        "station_name_enu": station_enu,
        "station_name_distance_m": station_distance,
    }


def _camera_maps(receipt: dict) -> tuple[dict, dict]:
    cameras = {camera["capture_id"]: camera for camera in receipt["cameras"]}
    intrinsics = {
        camera["capture_id"]: receipt["intrinsics"]["per_frame"][str(camera["frame_rank"])]
        for camera in receipt["cameras"]
    }
    return cameras, intrinsics


def ray_plane_hit(camera: dict, intrinsics: dict, pixel: tuple[float, float],
                  plane_origin: np.ndarray, plane_normal: np.ndarray) -> np.ndarray | None:
    focal = float(intrinsics["focal_px"])
    cx, cy = intrinsics["principal_point_px"]
    rotation = cv2.Rodrigues(np.asarray(camera["solved_rotation_rodrigues"], dtype=float))[0]
    ray_camera = np.asarray([(pixel[0] - cx) / focal, (pixel[1] - cy) / focal, 1.0])
    ray_world = rotation.T @ ray_camera
    center = np.asarray(camera["solved_center_enu_m"], dtype=float)
    denominator = float(ray_world @ plane_normal)
    if abs(denominator) < 1e-9:
        return None
    distance = float((plane_origin - center) @ plane_normal) / denominator
    if distance <= 0:
        return None
    return center + distance * ray_world


def project_point(camera: dict, intrinsics: dict, point: np.ndarray) -> list[float] | None:
    rotation = cv2.Rodrigues(np.asarray(camera["solved_rotation_rodrigues"], dtype=float))[0]
    center = np.asarray(camera["solved_center_enu_m"], dtype=float)
    local = rotation @ (point - center)
    if local[2] <= 0:
        return None
    focal = float(intrinsics["focal_px"])
    cx, cy = intrinsics["principal_point_px"]
    return [float(focal * local[0] / local[2] + cx), float(focal * local[1] / local[2] + cy)]


def _working_bbox(observation: dict, camera: dict) -> np.ndarray:
    source_width, source_height = observation["image_size_px"]
    working_width, working_height = camera["working_image"]["dimensions_px"]
    return np.asarray(observation["bbox_xyxy_px"], dtype=float) * np.asarray([
        working_width / source_width,
        working_height / source_height,
        working_width / source_width,
        working_height / source_height,
    ])


def _contains(box: np.ndarray, pixel: list[float]) -> bool:
    return bool(box[0] <= pixel[0] <= box[2] and box[1] <= pixel[1] <= box[3])


def choose_regions(observations: list[dict], controls: dict[str, list[float]],
                   cameras: dict) -> tuple[list[dict], list[dict]]:
    portals = []
    signs = []
    for capture_id, control in sorted(controls.items()):
        if capture_id not in cameras:
            continue
        rows = [row for row in observations if row["capture_id"] == capture_id]
        portal_choices = []
        sign_choices = []
        for row in rows:
            if float(row["detector_confidence"]) < MIN_DETECTOR_CONFIDENCE:
                continue
            box = _working_bbox(row, cameras[capture_id])
            if "facade_door" in row["semantic_candidates"] and _contains(box, control):
                area = float((box[2] - box[0]) * (box[3] - box[1]))
                portal_choices.append((area, float(row["detector_confidence"]), row, box))
            if "station_identity_sign" in row["semantic_candidates"]:
                width = float(box[2] - box[0])
                height = float(box[3] - box[1])
                center_x = float((box[0] + box[2]) / 2.0)
                center_y = float((box[1] + box[3]) / 2.0)
                image_height = cameras[capture_id]["working_image"]["dimensions_px"][1]
                if width >= 2.0 * height and center_y < control[1] + 0.1 * image_height and abs(center_x - control[0]) < 0.4 * cameras[capture_id]["working_image"]["dimensions_px"][0]:
                    # The station identity panel is the upper horizontal sign
                    # over the portal.  Confidence alone selected a second,
                    # lower notice in nmb01, so vertical placement is the
                    # primary deterministic discriminator.
                    sign_choices.append((-center_y, float(row["detector_confidence"]), row, box))
        if portal_choices:
            _, _, row, box = max(portal_choices, key=lambda value: (value[0], value[1]))
            portals.append({"observation": row, "working_bbox": box})
        if sign_choices:
            _, _, row, box = max(sign_choices, key=lambda value: (value[0], value[1]))
            signs.append({"observation": row, "working_bbox": box})
    return portals, signs


def _surface_observation(item: dict, cameras: dict, intrinsics: dict,
                         plane: dict) -> dict | None:
    row = item["observation"]
    capture_id = row["capture_id"]
    box = item["working_bbox"]
    corners_px = [
        (box[0], box[1]), (box[2], box[1]),
        (box[2], box[3]), (box[0], box[3]),
    ]
    corners = [
        ray_plane_hit(cameras[capture_id], intrinsics[capture_id], pixel,
                      plane["origin"], plane["normal"])
        for pixel in corners_px
    ]
    if any(corner is None for corner in corners):
        return None
    uv = [
        [float((corner - plane["origin"]) @ plane["tangent"]), float(corner[2])]
        for corner in corners
    ]
    return {
        "capture_id": capture_id,
        "observation_id": row["observation_id"],
        "detector_confidence": row["detector_confidence"],
        "detector_text_label": row["detector_text_label"],
        "working_bbox_xyxy_px": [float(value) for value in box],
        "corners_uv_m": uv,
        "extent_uv_m": {
            "u_min": min(point[0] for point in uv),
            "u_max": max(point[0] for point in uv),
            "z_min": min(point[1] for point in uv),
            "z_max": max(point[1] for point in uv),
        },
    }


def _fuse(rows: list[dict]) -> dict:
    keys = ("u_min", "u_max", "z_min", "z_max")
    extent = {key: float(np.median([row["extent_uv_m"][key] for row in rows])) for key in keys}
    centers = np.asarray([
        [(row["extent_uv_m"]["u_min"] + row["extent_uv_m"]["u_max"]) / 2.0,
         (row["extent_uv_m"]["z_min"] + row["extent_uv_m"]["z_max"]) / 2.0]
        for row in rows
    ])
    center = np.asarray([(extent["u_min"] + extent["u_max"]) / 2.0,
                         (extent["z_min"] + extent["z_max"]) / 2.0])
    return {
        "extent_uv_m": extent,
        "width_m": extent["u_max"] - extent["u_min"],
        "height_m": extent["z_max"] - extent["z_min"],
        "maximum_center_deviation_m": float(np.max(np.linalg.norm(centers - center, axis=1))),
    }


def _world_corners(plane: dict, extent: dict) -> list[list[float]]:
    # FL-4813: region z is absolute ENU elevation, not height above the plane.
    origin = np.asarray(plane["origin"], dtype=float).copy()
    origin[2] = 0.0
    tangent = plane["tangent"]
    return [
        [float(value) for value in origin + tangent * u + np.asarray([0.0, 0.0, z])]
        for u, z in [
            (extent["u_min"], extent["z_min"]),
            (extent["u_max"], extent["z_min"]),
            (extent["u_max"], extent["z_max"]),
            (extent["u_min"], extent["z_max"]),
        ]
    ]


def _bbox_iou(a: list[float], b: list[float]) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def _back_projection(rows: list[dict], corners: list[list[float]], cameras: dict,
                     intrinsics: dict) -> list[dict]:
    out = []
    for row in rows:
        capture_id = row["capture_id"]
        projected = [project_point(cameras[capture_id], intrinsics[capture_id], np.asarray(corner)) for corner in corners]
        if any(pixel is None for pixel in projected):
            continue
        bbox = [min(p[0] for p in projected), min(p[1] for p in projected),
                max(p[0] for p in projected), max(p[1] for p in projected)]
        out.append({
            "capture_id": capture_id,
            "detected_bbox_xyxy_px": row["working_bbox_xyxy_px"],
            "fused_back_projected_bbox_xyxy_px": bbox,
            "bbox_iou": _bbox_iou(row["working_bbox_xyxy_px"], bbox),
        })
    return out


def bounded_parent_plane(parent: dict) -> dict:
    """Read an explicit bounded parent proposal; never infer its acceptance.

    Endpoints are ENU metres on a horizontal OSM edge or fitted parent plane.
    Z bounds are absolute ENU elevations. The caller retains the parent source
    receipt, camera receipt and registration state; this helper grants no world
    authority to a geometrically plausible proposal.
    """
    edge = np.asarray(parent["edge_enu_m"], dtype=float)
    bounds = np.asarray(parent["z_bounds_enu_m"], dtype=float)
    if (edge.shape != (2, 3) or bounds.shape != (2,)
            or not np.isfinite(edge).all() or not np.isfinite(bounds).all()):
        raise ValueError("parent edge/bounds must be finite ENU metres")
    delta = edge[1] - edge[0]
    width = float(np.linalg.norm(delta[:2]))
    if width < 0.1 or abs(delta[2]) > 1e-6 or bounds[1] <= bounds[0]:
        raise ValueError("parent requires a horizontal nondegenerate edge and positive height")
    if not parent.get("parent_id") or not parent.get("source_refs"):
        raise ValueError("parent identity and source references are required")
    tangent = delta / width
    return {"origin": edge[0], "tangent": tangent,
            "normal": np.asarray([-tangent[1], tangent[0], 0.0]),
            "width_m": width, "z_bounds": bounds}


def project_child_to_parent(parent: dict, observations: list[dict],
                            camera_receipt: dict) -> dict:
    """FL-4813: test a tracked region against its parent, not a new sparse plane.

    Every view must fit the finite parent. Leave-one-view-out projection tests
    agreement without scoring each view against its own fitted region. This is
    geometric proposal evidence only: even a passing result needs accepted
    registration, parent association and occlusion review before promotion.
    """
    plane = bounded_parent_plane(parent)
    cameras, intrinsics = _camera_maps(camera_receipt)
    rows, failed = [], []
    seen = set()
    for observation in observations:
        cid = observation["capture_id"]
        if cid in seen:
            raise ValueError("a physical track may contribute only one region per capture")
        seen.add(cid)
        if cid not in cameras:
            failed.append("missing_camera:" + cid)
            continue
        camera = cameras[cid]
        center = np.asarray(camera["solved_center_enu_m"], dtype=float)
        rotation = np.asarray(camera["solved_rotation_rodrigues"], dtype=float)
        sizes = np.asarray([observation["image_size_px"],
                            camera["working_image"]["dimensions_px"]], dtype=float)
        principal = np.asarray(intrinsics[cid]["principal_point_px"], dtype=float)
        focal = float(intrinsics[cid]["focal_px"])
        if (center.shape != (3,) or rotation.shape != (3,) or sizes.shape != (2, 2)
                or principal.shape != (2,) or not np.isfinite(focal) or focal <= 0
                or not all(np.isfinite(a).all() for a in (center, rotation, sizes, principal))
                or np.any(sizes <= 0)):
            raise ValueError("invalid camera or image coordinate domain")
        box = _working_bbox(observation, cameras[cid])
        if (not np.isfinite(box).all() or box.shape != (4,)
                or box[2] <= box[0] or box[3] <= box[1]):
            raise ValueError("child bounding box must be finite and nonempty")
        row = _surface_observation({"observation": observation, "working_bbox": box},
                                   cameras, intrinsics, plane)
        if row is None:
            failed.append("ray_misses_parent:" + cid)
            continue
        uv = np.asarray(row["corners_uv_m"])
        if (not np.isfinite(uv).all() or np.any(uv[:, 0] < -1e-6)
                or np.any(uv[:, 0] > plane["width_m"] + 1e-6)
                or np.any(uv[:, 1] < plane["z_bounds"][0] - 1e-6)
                or np.any(uv[:, 1] > plane["z_bounds"][1] + 1e-6)):
            failed.append("outside_parent_bounds:" + cid)
            continue
        rows.append(row)
    if len(rows) < 2:
        failed.append("minimum_distinct_views")
    centers = [np.asarray(cameras[row["capture_id"]]["solved_center_enu_m"])
               for row in rows]
    baseline = max((float(np.linalg.norm(a - b)) for a in centers for b in centers), default=0.)
    if baseline < 0.25:
        failed.append("insufficient_camera_baseline")
    fused = _fuse(rows) if rows else None
    if fused and fused["maximum_center_deviation_m"] > MAX_FUSED_CENTER_DEVIATION_M:
        failed.append("child_view_agreement")
    held_out = []
    if len(rows) >= 2:
        for i, row in enumerate(rows):
            training = rows[:i] + rows[i + 1:]
            corners = _world_corners(plane, _fuse(training)["extent_uv_m"])
            check = _back_projection([row], corners, cameras, intrinsics)
            held_out.extend(check)
            if not check or check[0]["bbox_iou"] < MIN_MEDIAN_BACK_PROJECTION_IOU:
                failed.append("held_out_back_projection:" + row["capture_id"])
    return {
        "parent_id": parent["parent_id"], "parent_source_refs": parent["source_refs"],
        "parent_proposal": parent, "observations": rows,
        "fused_region": fused,
        "world_corners_enu_m": _world_corners(plane, fused["extent_uv_m"]) if fused else [],
        "held_out_back_projection": held_out, "camera_baseline_m": baseline,
        "gate": {"pass": not failed, "failed": sorted(set(failed)),
                 "minimum_views": 2, "minimum_baseline_m": 0.25,
                 "minimum_held_out_bbox_iou": MIN_MEDIAN_BACK_PROJECTION_IOU},
        "world_ir_promotion_ready": False,
        "remaining_checks": ["accepted_registration", "accepted_parent_association", "occlusion_review"],
    }


def build(camera_receipt_path: Path, observations_path: Path, controls_path: Path,
          osm_path: Path, anchor_ref: str, requirements_path: Path,
          locale_context_id: str = "numabe_station") -> tuple[WorldIRStore, dict]:
    camera_receipt = json.loads(camera_receipt_path.read_text())
    observations = json.loads(observations_path.read_text())["observations"]
    control_document = json.loads(controls_path.read_text())
    requirements = json.loads(requirements_path.read_text())
    locale_context = requirements.get("locale_contexts", {}).get(locale_context_id)
    if not locale_context:
        raise ValueError(f"locale context {locale_context_id!r} is absent from {requirements_path}")
    controls = {row["capture_id"]: row["pixel"] for row in control_document["control_points"]}
    origin = camera_receipt["corridor"]["enu_origin_wgs84"]
    frame = EnuFrame(origin["longitude"], origin["latitude"], origin["ellipsoidal_height_m"])
    plane = entrance_plane(osm_path, anchor_ref, frame)
    cameras, intrinsics = _camera_maps(camera_receipt)
    portal_items, sign_items = choose_regions(observations, controls, cameras)
    portal_rows = [row for item in portal_items if (row := _surface_observation(item, cameras, intrinsics, plane))]
    sign_rows = [row for item in sign_items if (row := _surface_observation(item, cameras, intrinsics, plane))]
    portal = _fuse(portal_rows) if portal_rows else None
    sign = _fuse(sign_rows) if sign_rows else None
    failed = []
    if len(portal_rows) < MIN_PORTAL_VIEWS:
        failed.append("minimum_portal_views")
    if len(sign_rows) < MIN_SIGN_VIEWS:
        failed.append("minimum_sign_views")
    if portal and portal["maximum_center_deviation_m"] > MAX_FUSED_CENTER_DEVIATION_M:
        failed.append("portal_view_agreement")
    if sign and sign["maximum_center_deviation_m"] > MAX_FUSED_CENTER_DEVIATION_M:
        failed.append("sign_view_agreement")
    gate_pass = not failed

    portal_corners = _world_corners(plane, portal["extent_uv_m"]) if portal else []
    sign_corners = _world_corners(plane, sign["extent_uv_m"]) if sign else []
    portal_back_projection = _back_projection(portal_rows, portal_corners, cameras, intrinsics)
    sign_back_projection = _back_projection(sign_rows, sign_corners, cameras, intrinsics)
    portal_iou_median = float(np.median([row["bbox_iou"] for row in portal_back_projection])) if portal_back_projection else 0.0
    sign_iou_median = float(np.median([row["bbox_iou"] for row in sign_back_projection])) if sign_back_projection else 0.0
    if portal_iou_median < MIN_MEDIAN_BACK_PROJECTION_IOU:
        failed.append("portal_back_projection")
    if sign_iou_median < MIN_MEDIAN_BACK_PROJECTION_IOU:
        failed.append("sign_back_projection")
    gate_pass = not failed

    receipt = {
        "schema": "y92.anchored_facade_reconstruction.v1",
        "stage": "osm_anchored_multi_view_facade_association",
        "anchor": {
            "osm_ref": plane["osm_ref"],
            "osm_tags": plane["osm_tags"],
            "approach_way_ref": plane["approach_way_ref"],
            "approach_way_tags": plane["approach_way_tags"],
            "approach_neighbour_ref": plane["approach_neighbour_ref"],
            "station_name_ref": plane["station_name_ref"],
            "station_name_tags": plane["station_name_tags"],
            "station_name_distance_m": plane["station_name_distance_m"],
        },
        "plane": {
            "origin_enu_m": [float(value) for value in plane["origin"]],
            "normal_enu": [float(value) for value in plane["normal"]],
            "horizontal_tangent_enu": [float(value) for value in plane["tangent"]],
            "derivation": "OSM station entrance point plus connected footway direction; vertical world Z",
        },
        "portal": {**portal, "observations": portal_rows, "world_corners_enu_m": portal_corners} if portal else None,
        "station_identity_sign": {**sign, "observations": sign_rows, "world_corners_enu_m": sign_corners} if sign else None,
        "back_projection": {
            "portal": portal_back_projection,
            "station_identity_sign": sign_back_projection,
            "median_bbox_iou": {
                "portal": portal_iou_median,
                "station_identity_sign": sign_iou_median,
            },
        },
        "gate": {
            "pass": gate_pass,
            "failed": failed,
            "minimum_portal_views": MIN_PORTAL_VIEWS,
            "minimum_sign_views": MIN_SIGN_VIEWS,
            "maximum_fused_center_deviation_m": MAX_FUSED_CENTER_DEVIATION_M,
            "minimum_median_back_projection_iou": MIN_MEDIAN_BACK_PROJECTION_IOU,
        },
        "inputs": {
            "camera_receipt": {"path": str(camera_receipt_path), "sha256": _digest(camera_receipt_path)},
            "object_observations": {"path": str(observations_path), "sha256": _digest(observations_path)},
            "control_points": {"path": str(controls_path), "sha256": _digest(controls_path)},
            "osm": {"path": str(osm_path), "sha256": _digest(osm_path)},
            "requirements": {"path": str(requirements_path), "sha256": _digest(requirements_path)},
        },
        "locale_context": locale_context,
        "world_ir_promotion_ready": gate_pass,
    }

    store = WorldIRStore.new(
        "numabe_station_entrance_facade_20260912",
        generated_at="2026-09-12T00:00:00Z",
        notes="Source-backed station entrance facade/sign slice; presentation authority only.",
    )
    store.add_frame(S.coordinate_frame(
        frame.frame_id, frame.crs, "m", ["east", "north", "up"],
        "wgs84_ellipsoid_offset_from_frame_origin", frame.origin_note,
    ))
    osm_observation = store.add_observation(S.source_observation(
        observation_id=f"obs:vector:{anchor_ref}#entrance_facade",
        source_id="osm_vector_extract", source_kind="vector_map",
        observed_at="2026-09-12T00:00:00Z",
        attributes={"source_ref": anchor_ref, "tags": plane["osm_tags"],
                    "approach_way_ref": plane["approach_way_ref"]},
        geometry=S.geometry_point(frame.frame_id, plane["origin"]),
        quality={"position_sigma_m": 1.5},
    ))
    station_name_observation = store.add_observation(S.source_observation(
        observation_id=f"obs:vector:{plane['station_name_ref']}#station_identity",
        source_id="osm_vector_extract", source_kind="vector_map",
        observed_at="2026-09-12T00:00:00Z",
        attributes={"source_ref": plane["station_name_ref"],
                    "tags": plane["station_name_tags"]},
        geometry=S.geometry_point(frame.frame_id, plane["station_name_enu"]),
        quality={"position_sigma_m": 1.5},
    ))
    image_observation_ids = []
    used = {row["observation_id"]: row for row in portal_rows + sign_rows}
    source_rows = {row["observation_id"]: row for row in observations}
    for observation_id in sorted(used):
        source = source_rows[observation_id]
        added = store.add_observation(S.source_observation(
            observation_id=f"obs:imagery:{observation_id}",
            source_id="wikimedia_commons_street_imagery", source_kind="imagery",
            observed_at="2024-05-30T16:00:00+09:00",
            attributes={
                "capture_id": source["capture_id"],
                "source_observation_id": observation_id,
                "detector_text_label": source["detector_text_label"],
                "detector_confidence": source["detector_confidence"],
                "bbox_xyxy_px": source["bbox_xyxy_px"],
                "capture_sha256": source["capture_sha256"],
            },
            quality={"detector_confidence": source["detector_confidence"]},
            digest=f"sha256:{source['capture_sha256']}",
        ))
        image_observation_ids.append(added["observation_id"])

    supporting = [
        osm_observation["observation_id"],
        station_name_observation["observation_id"],
        *image_observation_ids,
    ]
    if gate_pass:
        facade = store.add_feature(S.canonical_feature(
            feature_id="feature:facade_plane:numabe_station_entrance_1",
            semantic_class="facade_plane",
            geometry={"kind": "plane", "frame_id": frame.frame_id,
                      "origin": [float(value) for value in plane["origin"]],
                      "normal": [float(value) for value in plane["normal"]]},
            properties={
                "geometry": S.property_claim(None, S.PROVENANCE_DERIVED,
                    "OSM entrance anchor plus connected footway normal, corroborated by machine entrance regions",
                    supporting, uncertainty=S.uncertainty_isotropic(1.5, "m")),
                "portal_extent_uv_m": S.property_claim(portal["extent_uv_m"], S.PROVENANCE_DERIVED,
                    "median fusion of detector regions projected through accepted cameras", supporting),
                "association_gate": S.property_claim(receipt["gate"], S.PROVENANCE_DERIVED,
                    "scripts.pipeline.reconstruct_anchored_facade", supporting),
            },
            confidence=0.75, gameplay_authority=S.AUTHORITY_PRESENTATION_ONLY,
            presentation=S.presentation_requirements(True, True, True, "landmark",
                "Numabe entrance portal; building mass remains gameplay authority"),
            authority_supporting_properties=["geometry"],
        ))
        store.add_feature(S.canonical_feature(
            feature_id="feature:station_entrance:numabe_1",
            semantic_class="station_entrance",
            geometry=S.geometry_polygon(frame.frame_id, portal_corners),
            parent_feature_id=facade["feature_id"],
            properties={
                "geometry": S.property_claim(None, S.PROVENANCE_DERIVED,
                    "multi-view portal bounds on OSM-anchored plane", supporting,
                    uncertainty=S.uncertainty_isotropic(portal["maximum_center_deviation_m"], "m")),
            },
            confidence=0.75, gameplay_authority=S.AUTHORITY_PRESENTATION_ONLY,
        ))
        store.add_feature(S.canonical_feature(
            feature_id="feature:sign:numabe_station_identity_1",
            semantic_class="sign",
            geometry=S.geometry_polygon(frame.frame_id, sign_corners),
            parent_feature_id=facade["feature_id"],
            properties={
                "geometry": S.property_claim(None, S.PROVENANCE_DERIVED,
                    "multi-view station-sign detector bounds on OSM-anchored facade plane", supporting,
                    uncertainty=S.uncertainty_isotropic(sign["maximum_center_deviation_m"], "m")),
                "text": S.property_claim(plane["station_name_tags"]["name:ja"], S.PROVENANCE_MEASURED,
                    f"OSM {plane['station_name_ref']} name:ja identity corroborated by source-visible station sign",
                    [station_name_observation["observation_id"], *image_observation_ids]),
                "template_context": S.property_claim(
                    locale_context, S.PROVENANCE_DERIVED,
                    f"source-bundle locale context {locale_context_id}", supporting),
            },
            confidence=0.75, gameplay_authority=S.AUTHORITY_PRESENTATION_ONLY,
            presentation=S.presentation_requirements(True, True, True, "landmark",
                "Source-backed station identity sign; Classic representation chosen in FL-4814"),
            labels={
                "ja": plane["station_name_tags"]["name:ja"],
                "en": plane["station_name_tags"].get("name:en", "Numabe"),
            },
        ))
    return store, receipt


def write_preview(receipt: dict, capture_index_path: Path, out_path: Path) -> None:
    capture_index = json.loads(capture_index_path.read_text())
    paths = {row["capture_id"]: capture_index_path.parent / row["local_path"] for row in capture_index["captures"]}
    by_kind = {
        key: receipt["back_projection"][key]
        for key in ("portal", "station_identity_sign")
    }
    captures = sorted({row["capture_id"] for rows in by_kind.values() for row in rows})
    panels = []
    colours = {"portal": ((0, 180, 255), (255, 255, 0)),
               "station_identity_sign": ((0, 255, 255), (0, 255, 0))}
    for capture_id in captures:
        image = cv2.imread(str(paths[capture_id]))
        if image is None:
            continue
        image = cv2.resize(image, (1024, 768), interpolation=cv2.INTER_AREA)
        for kind, rows in by_kind.items():
            for row in rows:
                if row["capture_id"] != capture_id:
                    continue
                detected, fused = colours[kind]
                for box, colour in [(row["detected_bbox_xyxy_px"], detected),
                                    (row["fused_back_projected_bbox_xyxy_px"], fused)]:
                    scaled = [int(round(value * 0.5)) for value in box]
                    cv2.rectangle(image, (scaled[0], scaled[1]), (scaled[2], scaled[3]), colour, 3)
                cv2.putText(image, f"{kind} IoU={row['bbox_iou']:.2f}",
                            (20, 40 + 35 * list(by_kind).index(kind)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, fused, 2, cv2.LINE_AA)
        cv2.putText(image, capture_id, (20, 740), cv2.FONT_HERSHEY_SIMPLEX,
                    0.8, (255, 255, 255), 2, cv2.LINE_AA)
        panels.append(image)
    if panels:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out_path), np.vstack(panels), [cv2.IMWRITE_JPEG_QUALITY, 92])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--camera-receipt", type=Path, required=True)
    parser.add_argument("--object-observations", type=Path, required=True)
    parser.add_argument("--control-points", type=Path, required=True)
    parser.add_argument("--osm", type=Path, required=True)
    parser.add_argument("--anchor-ref", default="node/9865199050")
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--locale-context", default="numabe_station")
    parser.add_argument("--world-ir-out", type=Path, required=True)
    parser.add_argument("--receipt-out", type=Path, required=True)
    parser.add_argument("--capture-index", type=Path)
    parser.add_argument("--preview-out", type=Path)
    args = parser.parse_args(argv)
    store, receipt = build(args.camera_receipt, args.object_observations,
                           args.control_points, args.osm, args.anchor_ref,
                           args.requirements, args.locale_context)
    findings = store.validate()
    receipt["world_ir_validation_findings"] = [str(finding) for finding in findings]
    if findings:
        for finding in findings:
            print(f"FAIL {finding}", file=sys.stderr)
        return 1
    args.world_ir_out.parent.mkdir(parents=True, exist_ok=True)
    store.save(args.world_ir_out)
    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    args.receipt_out.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
    if args.preview_out:
        if not args.capture_index:
            parser.error("--preview-out requires --capture-index")
        write_preview(receipt, args.capture_index, args.preview_out)
    print(
        f"[anchored-facade] portal_views={len(receipt['portal']['observations']) if receipt['portal'] else 0} "
        f"sign_views={len(receipt['station_identity_sign']['observations']) if receipt['station_identity_sign'] else 0} "
        f"gate={'PASS' if receipt['gate']['pass'] else 'FAIL'} "
        f"features={len(store.features)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
