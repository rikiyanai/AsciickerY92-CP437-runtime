#!/usr/bin/env python3
"""Compile a debt-driven street-level observation assignment (FL-4809 OBSERVE-01).

Work set: the 17 B/C features of the triage table in
``.run/numabe_connected/triage01/osm_ingest_receipt.json`` (A features are
out of scope).  For each one this stage assigns:

- ``importance``: ``landmark`` when the feature name/id matches
  ``required_landmarks.json``, else ``route_visible`` when within 30 m of the
  Valhalla pedestrian route, else ``background``;
- up to 3 candidate frames whose camera is within 45 m and whose view cone
  (heading +- half FOV) contains the feature bearing, ranked by distance
  then bearing residual, with line of sight against building_footprint
  rings (the feature's own parent building excluded);
- ``observation_state``: ``observed_boxed`` when a registered instance or a
  facade view already references a kept frame for the feature,
  ``frame_available_unboxed`` when candidates exist but no annotation,
  ``observation_owed`` when none.

Frame pools (all licensed and recorded, nothing downloaded):

- survey pictures (1812 Mapillary records, remote asset_uri);
- route manifest captures (212 on-disk frames, local_path).

FOV: measured ``horizontal_fov_deg`` first, else derived from ``focal_35mm``
on the 36 mm full-frame width, else the ``observation_default_hfov_deg``
DEFAULT fact owned by ``compile_rules_resolved.py``.  Heading is compass,
clockwise from north, matching ``camera_solve/capture.py`` and
``register_observations.py`` (bearing vector ``(sin, cos)`` of the radian
angle).  Projection reuses ``geo_frames.utm_forward`` (EPSG:32654); no
second projection is written here.

Deterministic: every list is sorted, floats are rounded, JSON is dumped
with sorted keys, and there is no randomness, so two runs cmp equal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline import geo_frames as GF  # noqa: E402
from scripts.pipeline.compile_rules_resolved import (  # noqa: E402
    ROAD_CLASS_DEFAULTS,
)

SCHEMA = "y92.observation_debt.v1"

MAX_CAMERA_DISTANCE_M = 45.0
ROUTE_VISIBLE_DISTANCE_M = 30.0
KEEP_FRAMES = 3

RULE_TEXT = (
    "importance: landmark when a normalized feature name (triage label or "
    "IR name/name:en) equals a normalized required_landmarks entry "
    "(landmark_id, name, source_ref, lobe id); else route_visible when the "
    "feature point is within 30 m of the Valhalla pedestrian route "
    "polyline; else background. candidates: camera within 45 m of the "
    "feature point AND |wrap(bearing - heading)| <= hfov/2, ranked by "
    "(distance_m, |residual|, frame_id), line of sight required against "
    "every building_footprint ring except a ring containing the feature "
    "point (own parent) or the camera; keep the best 3. hfov: measured "
    "horizontal_fov_deg, else 2*atan(18/focal_35mm), else the "
    "observation_default_hfov_deg DEFAULT fact. observation_state: "
    "observed_boxed when a registered instance (custom_identity_subject "
    "match or shared capture_id with a kept frame) or a facade view "
    "(capture_id shared with a kept frame) references the feature; else "
    "frame_available_unboxed when kept frames exist; else "
    "observation_owed."
)


def _wrap_deg(angle: float) -> float:
    """Wrap to (-180, 180]."""
    wrapped = (angle + 180.0) % 360.0 - 180.0
    return 180.0 if wrapped == -180.0 else wrapped


def _bearing_deg(e0: float, n0: float, e1: float, n1: float) -> float:
    """Compass bearing from (e0, n0) to (e1, n1), clockwise from north."""
    return (math.degrees(math.atan2(e1 - e0, n1 - n0))) % 360.0


def _decode_polyline6(shape: str) -> list[tuple[float, float]]:
    """Valhalla shape (precision 6) -> [(lon, lat)]."""
    coords: list[tuple[float, float]] = []
    index = 0
    lat = 0
    lon = 0
    size = len(shape)
    while index < size:
        for delta in ("lat", "lon"):
            shift = 0
            result = 0
            while True:
                byte = ord(shape[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            value = ~(result >> 1) if (result & 1) else (result >> 1)
            if delta == "lat":
                lat += value
            else:
                lon += value
        coords.append((lon / 1e6, lat / 1e6))
    return coords


def _pt_seg_dist(px: float, py: float, ax: float, ay: float,
                 bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom == 0.0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _point_in_ring(pt: tuple[float, float],
                   ring: list[list[float]]) -> bool:
    x, y = pt
    inside = False
    for i in range(len(ring) - 1):
        x0, y0 = ring[i][0], ring[i][1]
        x1, y1 = ring[i + 1][0], ring[i + 1][1]
        if (y0 > y) != (y1 > y):
            if x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
                inside = not inside
    return inside


def _cross(o: tuple[float, float], a: tuple[float, float],
           b: tuple[float, float]) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _segments_cross(p1: tuple[float, float], p2: tuple[float, float],
                    p3: tuple[float, float], p4: tuple[float, float]) -> bool:
    d1, d2 = _cross(p3, p4, p1), _cross(p3, p4, p2)
    d3, d4 = _cross(p1, p2, p3), _cross(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _norm(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = text.strip().lower()
    return cleaned or None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hfov_deg(record: dict, default_hfov: float) -> tuple[float, str]:
    raw = record.get("horizontal_fov_deg")
    if isinstance(raw, (int, float)) and 0.0 < float(raw) < 180.0:
        return float(raw), "measured"
    focal = record.get("focal_35mm")
    if isinstance(focal, (int, float)) and float(focal) > 0.0:
        return math.degrees(2.0 * math.atan(18.0 / float(focal))), \
            "derived_focal_35mm"
    return default_hfov, "default"


def _load_frames(survey_path: Path, manifest_path: Path,
                 default_hfov: float) -> tuple[list[dict], dict]:
    survey = json.loads(survey_path.read_text(encoding="utf-8"))
    pictures = survey["providers"]["mapillary"]["pictures"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bundle_dir = manifest_path.parent

    frames: dict[str, dict] = {}
    skipped = 0
    for pic in pictures:
        lat, lon = pic.get("lat"), pic.get("lon")
        heading = pic.get("heading_deg")
        if lat is None or lon is None or heading is None:
            skipped += 1
            continue
        east, north = GF.utm_forward(float(lon), float(lat))
        hfov, basis = _hfov_deg(pic, default_hfov)
        frames[str(pic["picture_id"])] = {
            "frame_id": str(pic["picture_id"]),
            "source": "survey",
            "licence": pic.get("licence"),
            "capture_time": pic.get("capture_date"),
            "lat": float(lat),
            "lon": float(lon),
            "heading_deg": float(heading),
            "hfov_deg": hfov,
            "hfov_basis": basis,
            "on_disk_path": None,
            "asset_uri": pic.get("asset_uri"),
            "east": east,
            "north": north,
        }
    for cap in manifest["captures"]:
        lat, lon = cap.get("lat"), cap.get("lon")
        heading = cap.get("heading_deg")
        if lat is None or lon is None or heading is None:
            skipped += 1
            continue
        east, north = GF.utm_forward(float(lon), float(lat))
        hfov, basis = _hfov_deg(cap, default_hfov)
        local = cap.get("local_path")
        frames[str(cap["capture_id"])] = {
            "frame_id": str(cap["capture_id"]),
            "source": "route_capture",
            "licence": cap.get("license"),
            "capture_time": cap.get("timestamp"),
            "lat": float(lat),
            "lon": float(lon),
            "heading_deg": float(heading),
            "hfov_deg": hfov,
            "hfov_basis": basis,
            "on_disk_path": local,
            "asset_uri": cap.get("asset_uri"),
            "east": east,
            "north": north,
            "_local_exists": bool(local) and (bundle_dir / local).is_file(),
        }
    ordered = [frames[key] for key in sorted(frames)]
    stats = {
        "survey_pictures": len(pictures),
        "manifest_captures": len(manifest["captures"]),
        "frames_indexed": len(ordered),
        "frames_skipped_no_pose": skipped,
    }
    return ordered, stats


def _los_clear(cam: tuple[float, float], feat: tuple[float, float],
               rings: list[tuple[tuple[float, float, float, float],
                                 list[list[float]]]]) -> bool:
    for (min_e, min_n, max_e, max_n), ring in rings:
        if (max(min(cam[0], feat[0]), min_e) > min(max(cam[0], feat[0]), max_e)
                or max(min(cam[1], feat[1]), min_n)
                > min(max(cam[1], feat[1]), max_n)):
            continue
        if _point_in_ring(feat, ring) or _point_in_ring(cam, ring):
            continue
        for a, b in zip(ring, ring[1:]):
            if _segments_cross(cam, feat, (a[0], a[1]), (b[0], b[1])):
                return False
    return True


def build(triage_receipt: Path, world_ir: Path, survey: Path,
          manifest: Path, route: Path, landmarks: Path, instances: Path,
          facade_views: Path,
          generated_at: str) -> tuple[dict, dict]:
    default_entry = ROAD_CLASS_DEFAULTS["observation_default_hfov_deg"]
    default_hfov = float(default_entry[0])

    triage = json.loads(triage_receipt.read_text(encoding="utf-8"))
    work = [row for row in triage["triage"]["by_group"]
            if row["group"] in ("B", "C")]
    work.sort(key=lambda row: row["feature_id"])

    doc = json.loads(world_ir.read_text(encoding="utf-8"))
    points: dict[str, tuple[float, float]] = {}
    labels: dict[str, dict] = {}
    rings: list[tuple[tuple[float, float, float, float],
                      list[list[float]]]] = []
    for feat in doc["canonical_features"]:
        geom = feat["geometry"]
        if geom.get("kind") == "point":
            points[feat["feature_id"]] = (
                float(geom["coordinates"][0]),
                float(geom["coordinates"][1]),
            )
            labels[feat["feature_id"]] = dict(feat.get("labels") or {})
        elif (feat["semantic_class"] == "building_footprint"
                and geom.get("kind") == "polygon"):
            ring = [[float(p[0]), float(p[1])]
                    for p in geom["coordinates"]]
            eastings = [p[0] for p in ring]
            northings = [p[1] for p in ring]
            rings.append(((min(eastings), min(northings),
                           max(eastings), max(northings)), ring))

    landmark_doc = json.loads(landmarks.read_text(encoding="utf-8"))
    landmark_texts = set()
    for entry in landmark_doc.get("landmarks", []):
        for key in ("landmark_id", "name", "source_ref",
                    "aoi_lobe_feature_id"):
            normed = _norm(entry.get(key))
            if normed:
                landmark_texts.add(normed)

    route_doc = json.loads(route.read_text(encoding="utf-8"))
    shape = route_doc["trip"]["legs"][0]["shape"]
    route_pts = [GF.utm_forward(lon, lat)
                 for lon, lat in _decode_polyline6(shape)]

    frames, frame_stats = _load_frames(survey, manifest, default_hfov)

    registered = json.loads(instances.read_text(encoding="utf-8"))
    facade_ids: dict[str, list[str]] = {}
    for name in sorted(
            p.name for p in facade_views.iterdir()
            if p.is_file() and p.suffix.lower() == ".jpg"):
        if name.startswith("mapillary_") and "__" in name:
            capture = name.split("__", 1)[0]
            facade_ids.setdefault(capture, []).append(name)

    features_out = []
    for row in work:
        fid = row["feature_id"]
        feat_pt = points[fid]
        names = {_norm(row.get("label"))}
        for key in ("name", "name:en"):
            names.add(_norm(labels.get(fid, {}).get(key)))
        names.discard(None)

        importance = "background"
        if names & landmark_texts:
            importance = "landmark"
        else:
            near_route = min(
                _pt_seg_dist(feat_pt[0], feat_pt[1], ax, ay, bx, by)
                for (ax, ay), (bx, by)
                in zip(route_pts, route_pts[1:])
            )
            if near_route <= ROUTE_VISIBLE_DISTANCE_M:
                importance = "route_visible"

        scored = []
        for frame in frames:
            dist = math.hypot(frame["east"] - feat_pt[0],
                              frame["north"] - feat_pt[1])
            if dist > MAX_CAMERA_DISTANCE_M:
                continue
            bearing = _bearing_deg(frame["east"], frame["north"],
                                   feat_pt[0], feat_pt[1])
            residual = _wrap_deg(bearing - frame["heading_deg"])
            if abs(residual) > frame["hfov_deg"] / 2.0:
                continue
            scored.append((dist, abs(residual), frame["frame_id"],
                           frame, residual))
        scored.sort(key=lambda item: (item[0], item[1], item[2]))

        kept = []
        for dist, _abs_res, _key, frame, residual in scored:
            if len(kept) >= KEEP_FRAMES:
                break
            if not _los_clear((frame["east"], frame["north"]), feat_pt,
                              rings):
                continue
            kept.append({
                "source": frame["source"],
                "licence": frame["licence"],
                "capture_time": frame["capture_time"],
                "frame_id": frame["frame_id"],
                "lat": frame["lat"],
                "lon": frame["lon"],
                "heading_deg": frame["heading_deg"],
                "hfov_deg": round(frame["hfov_deg"], 3),
                "hfov_basis": frame["hfov_basis"],
                "distance_m": round(dist, 3),
                "bearing_residual_deg": round(residual, 3),
                "on_disk_path": frame["on_disk_path"],
                "asset_uri": frame["asset_uri"],
                "los_clear": True,
            })
        kept_ids = [item["frame_id"] for item in kept]

        box_refs = []
        for inst in registered:
            subject = _norm(inst.get("custom_identity_subject"))
            if subject and subject in names:
                box_refs.append({
                    "kind": "registered_instance",
                    "ref": inst.get("instance_id"),
                    "frame_id": None,
                    "via": "custom_identity_subject",
                    "annotation_ids": sorted(inst.get("annotation_ids")
                                             or []),
                })
            shared = sorted(set(inst.get("capture_ids") or [])
                            & set(kept_ids))
            for frame_id in shared:
                box_refs.append({
                    "kind": "registered_instance",
                    "ref": inst.get("instance_id"),
                    "frame_id": frame_id,
                    "via": "capture_id",
                    "annotation_ids": sorted(inst.get("annotation_ids")
                                             or []),
                })
        for frame_id in kept_ids:
            for name in facade_ids.get(frame_id, []):
                box_refs.append({
                    "kind": "facade_view",
                    "ref": name,
                    "frame_id": frame_id,
                    "via": "capture_id",
                })
        box_refs.sort(key=lambda ref: (ref["kind"], ref["ref"] or "",
                                       ref["frame_id"] or ""))

        if box_refs:
            state = "observed_boxed"
        elif kept:
            state = "frame_available_unboxed"
        else:
            state = "observation_owed"

        features_out.append({
            "feature_id": fid,
            "label": row.get("label"),
            "group": row["group"],
            "importance": importance,
            "observation_state": state,
            "frames": kept,
            "box_refs": box_refs,
            "waiver": None,
        })

    by_state: dict[str, int] = {}
    by_importance: dict[str, int] = {}
    for item in features_out:
        by_state[item["observation_state"]] = \
            by_state.get(item["observation_state"], 0) + 1
        by_importance[item["importance"]] = \
            by_importance.get(item["importance"], 0) + 1

    document = {
        "schema": SCHEMA,
        "generated_at": generated_at,
        "inputs": {
            "triage_receipt": str(triage_receipt),
            "world_ir": str(world_ir),
            "survey": str(survey),
            "manifest": str(manifest),
            "route": str(route),
            "landmarks": str(landmarks),
            "instances": str(instances),
            "facade_views": str(facade_views),
        },
        "defaults_used": {
            "observation_default_hfov_deg": {
                "value": float(default_entry[0]),
                "unit": default_entry[1],
                "source": default_entry[2],
            },
        },
        "frame_stats": frame_stats,
        "counts": {
            "features": len(features_out),
            "by_state": dict(sorted(by_state.items())),
            "by_importance": dict(sorted(by_importance.items())),
        },
        "features": features_out,
    }

    receipt = {
        "schema": "y92.observation_debt_receipt.v1",
        "generated_at": generated_at,
        "inputs": {
            str(path): {"sha256": _sha256(path),
                        "size_bytes": path.stat().st_size}
            for path in (triage_receipt, world_ir, survey, manifest,
                         route, landmarks, instances)
        },
        "facade_views": {"dir": str(facade_views),
                         "jpg_count": sum(1 for p in facade_views.iterdir()
                                          if p.is_file()
                                          and p.suffix.lower() == ".jpg")},
        "defaults_used": document["defaults_used"],
        "frame_stats": frame_stats,
        "counts": document["counts"],
        "rule": RULE_TEXT,
    }
    return document, receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--triage-receipt", type=Path, required=True)
    ap.add_argument("--world-ir", type=Path, required=True)
    ap.add_argument("--survey", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--route", type=Path, required=True)
    ap.add_argument("--landmarks", type=Path, required=True)
    ap.add_argument("--instances", type=Path, required=True)
    ap.add_argument("--facade-views", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--receipt-out", type=Path, required=True)
    ap.add_argument("--generated-at", default="2026-09-19T00:00:00Z")
    args = ap.parse_args(argv)

    document, receipt = build(
        args.triage_receipt, args.world_ir, args.survey, args.manifest,
        args.route, args.landmarks, args.instances, args.facade_views,
        args.generated_at,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n", encoding="utf-8",
    )
    args.receipt_out.parent.mkdir(parents=True, exist_ok=True)
    args.receipt_out.write_text(
        json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n", encoding="utf-8",
    )
    print(f"[observation-debt] features={document['counts']['features']} "
          f"by_state={document['counts']['by_state']} "
          f"by_importance={document['counts']['by_importance']}")
    print(f"[observation-debt] wrote {args.out} and {args.receipt_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
