#!/usr/bin/env python3
"""Compile exact cartographic serialization specs before A3D stamping.

The representation plan decides which canonical features are surface
representations.  This stage resolves their final raster role, metre width,
geometry, and terminal state.  The stamper receives no tags, attachments, or
semantic defaults and therefore cannot invent roads, shoulders, decals, or
placement while serializing A3D cells.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from scripts.pipeline.canonical_attachment import (
    CanonicalIndex,
    SURFACE_DECAL_DIAMETER_M,
    resolve,
)


SCHEMA = "y92.surface_stamp_plan.v1"


def _load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _tags(feature: dict, observations: dict[str, dict]) -> dict:
    merged = {}
    for observation_id in feature.get("supporting_observation_ids", []):
        observation = observations.get(observation_id) or {}
        merged.update((observation.get("attributes") or {}).get("tags") or {})
    return merged


def _positive_width_tag(tags: dict) -> float | None:
    try:
        value = float(str(tags.get("width", "")).removesuffix(" m"))
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0.0 else None


def _rule_value(rules: dict, path: tuple[str, ...]) -> tuple[float, str]:
    node = rules
    try:
        for part in path:
            node = node[part]
        return float(node["value"]), str(node["source"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("surface stamp rule absent: " + ".".join(path)) from exc


# FL-4934 ROAD-CORRIDOR-01. A measured clear corridor (compile_lidar_road_corridor.py)
# replaces the uncited class default only for road classes whose corridor is
# the carriageway: Tokyo side streets paved wall to wall. Primary, secondary,
# trunk and motorway are excluded because moving traffic and centre barriers
# fall inside the obstacle band (the probe read 6.25 m against an 11 m road).
CORRIDOR_ELIGIBLE_ROAD_CLASSES = ("residential", "tertiary", "service", "unclassified")
ROAD_CORRIDOR_SCHEMA = "y92.lidar_road_corridor.v1"


def load_road_corridors(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    doc = _load(path)
    if doc.get("schema") != ROAD_CORRIDOR_SCHEMA:
        raise ValueError(f"{path}: schema {doc.get('schema')!r} is not {ROAD_CORRIDOR_SCHEMA}")
    if not doc.get("tile_sha256"):
        raise ValueError(f"{path}: road corridors carry no tile hashes; refusing unprovenanced widths")
    return {str(r["feature_id"]): r for r in doc.get("rows", []) if "corridor_median_m" in r}


def _road_class(tags: dict) -> str:
    highway = str(tags.get("highway") or "residential")
    return {
        "motorway": "primary", "trunk": "primary", "primary": "primary",
        "secondary": "secondary", "tertiary": "tertiary",
        "service": "service", "unclassified": "unclassified",
    }.get(highway, "residential")


def _road_corridor_layout(feature: dict, tags: dict,
                          corridors: dict | None) -> tuple[float, float, str] | None:
    """(width_m, left_offset_m, width_source) from a measured corridor, or None.

    FL-4934 v2. v1 painted the total corridor centred on the OSM centreline
    and spilled into buildings wherever the centreline is off-centre (5.5 %
    of the gained area). The painted strip now spans exactly the measured
    left and right clear distances: width = left + right, and the centreline
    shifts by (left - right) / 2 toward the left (+normal) side.
    """
    if _positive_width_tag(tags) is not None:
        return None
    if _road_class(tags) not in CORRIDOR_ELIGIBLE_ROAD_CLASSES:
        return None
    corridor = (corridors or {}).get(str(feature.get("feature_id")))
    if corridor is None or "left_median_m" not in corridor:
        return None
    left, right = float(corridor["left_median_m"]), float(corridor["right_median_m"])
    return left + right, (left - right) / 2.0, (
        f"TMG 2023 LiDAR clear corridor: left {left:g} m + right {right:g} m, medians of "
        f"{corridor['stations']} stations (compile_lidar_road_corridor.py; FL-4934)")


def _road_default_width(tags: dict, rules: dict) -> float:
    road_class = _road_class(tags)
    width_class = "primary" if road_class == "secondary" else road_class
    return _rule_value(rules, ("road_class_defaults", "carriageway_width_m_by_highway", width_class))[0]


def _road_corridor_polygon(coordinates: list, corridor: dict,
                           default_width_m: float) -> list | None:
    """Exterior ring (EPSG:32654) of the measured strip, or None to fall back.

    FL-4934 v4. One width per road overshot where a road narrows locally
    (v3: 0.82 % of gained paint inside footprints). Each station is painted at
    min(local clearance, road median) per side: narrowings are followed, while
    junctions and open lots, where a ray runs to its 15 m limit, cannot bulge
    the strip. The two end caps reuse the nearest station. Left is the
    +normal side, matching compile_lidar_road_corridor.py.

    FL-4934 v5. v4 also removed 9,283 m2 of default paint, largely where a
    parked vehicle closed the ray at one station; a vehicle is no evidence
    that the asphalt ends. Each side is therefore floored at half the current
    class default: the measurement only widens, never narrows.
    """
    from shapely.geometry import LineString, Polygon

    stations = corridor.get("stations_s_left_right_m") or []
    if len(stations) < 2:
        return None
    line = LineString([p[:2] for p in coordinates])
    lmed, rmed = float(corridor["left_median_m"]), float(corridor["right_median_m"])
    samples = [(0.0, stations[0][1], stations[0][2])] + [tuple(s) for s in stations] + [
        (line.length, stations[-1][1], stations[-1][2])]
    left_pts, right_pts = [], []
    for s, left, right in samples:
        s = min(max(float(s), 0.0), line.length)
        p = line.interpolate(s)
        a = line.interpolate(max(0.0, s - 0.25)); b = line.interpolate(min(line.length, s + 0.25))
        tx, ty = b.x - a.x, b.y - a.y
        tn = math.hypot(tx, ty) or 1.0
        ux, uy = -ty / tn, tx / tn
        floor = default_width_m / 2.0
        lw = max(floor, min(float(left), lmed))
        rw = max(floor, min(float(right), rmed))
        left_pts.append((p.x + ux * lw, p.y + uy * lw))
        right_pts.append((p.x - ux * rw, p.y - uy * rw))
    # FL-4934 v6: station chords still cut the inside of bends and lost 4,524
    # m2 of default paint. Paint the union of the default strip and the
    # measured polygon, so the measurement can only add; anything that is not
    # one clean polygon keeps the unchanged default.
    measured = Polygon(left_pts + right_pts[::-1]).buffer(0)
    default = line.buffer(default_width_m / 2.0, cap_style="flat")
    union = measured.union(default)
    if union.geom_type != "Polygon" or not union.is_valid or union.is_empty:
        return None
    return [[float(x), float(y)] for x, y in union.exterior.coords]


def _surface_role_width(feature: dict, tags: dict,
                        rules: dict, corridors: dict | None = None) -> tuple[str, float, str]:
    semantic_class = str(feature.get("semantic_class") or "")
    geometry_kind = (feature.get("geometry") or {}).get("kind")
    polygon = geometry_kind == "polygon"
    measured = _positive_width_tag(tags)
    if semantic_class == "railway_boundary":
        value, source = _rule_value(rules, ("road_class_defaults", "rail_half_width_m"))
        return "railway", measured or value * 2.0, (
            "OSM width tag" if measured else source)
    if semantic_class == "crossing":
        value, source = _rule_value(
            rules, ("road_class_defaults", "footway_half_width_m_by_kind",
                    "footway"))
        return "crossing", measured or value * 2.0, (
            "OSM width tag" if measured else source)
    if semantic_class == "sidewalk":
        highway = str(tags.get("highway") or "footway")
        role = {
            "steps": "steps", "cycleway": "cycleway", "path": "path",
            "pedestrian": "pedestrian_area" if polygon else "footway",
        }.get(highway, "footway")
        key = "footway" if highway not in ("steps", "pedestrian") else highway
        value, source = _rule_value(
            rules, ("road_class_defaults", "footway_half_width_m_by_kind", key))
        return role, measured or value * 2.0, (
            "OSM width tag" if measured else source)
    if semantic_class == "road":
        road_class = _road_class(tags)
        role = {
            "primary": "road_primary", "secondary": "road_secondary",
            "tertiary": "road_tertiary",
        }.get(road_class, "road_residential")
        width_class = "primary" if road_class == "secondary" else road_class
        value, source = _rule_value(
            rules, ("road_class_defaults", "carriageway_width_m_by_highway",
                    width_class))
        if measured:
            return role, measured, "OSM width tag"
        layout = _road_corridor_layout(feature, tags, corridors)
        if layout is not None:
            return role, layout[0], layout[2]
        return role, value, source
    if semantic_class == "terrain":
        if tags.get("natural") == "water" or tags.get("waterway"):
            return "water", 1.0, "canonical polygon"
        if tags.get("amenity") in ("parking", "parking_space") or tags.get("parking"):
            return "parking", 1.0, "canonical polygon"
        if tags.get("landuse") in ("grass", "forest"):
            return ("wood" if tags.get("landuse") == "forest" else "grass",
                    1.0, "canonical polygon")
        if tags.get("landuse") == "brownfield":
            # FL-4876. Bare, disused ground. "land" is the authored landcover
            # role and is what this is; the pedestrian_area fallback below
            # would paint a derelict parcel as walkable public space.
            return "land", 1.0, "canonical polygon"
        return "pedestrian_area", 1.0, "canonical polygon"
    raise ValueError(
        f"no surface representation contract for {feature.get('feature_id')} "
        f"class {semantic_class!r}")


def build(canonical_path: Path, representation_plan_path: Path,
          rules_path: Path, road_corridors_path: Path | None = None) -> dict:
    corridors = load_road_corridors(road_corridors_path)
    canonical = _load(canonical_path)
    representation_plan = _load(representation_plan_path)
    rules = _load(rules_path)
    features = {str(row["feature_id"]): row
                for row in canonical.get("canonical_features", [])}
    observations = {str(row["observation_id"]): row
                    for row in canonical.get("source_observations", [])}
    index = CanonicalIndex(canonical)
    rows = []
    for decision in representation_plan.get("rows", []):
        if decision.get("owner_stage") != "stamp":
            continue
        feature_id = str(decision["feature_id"])
        feature = features.get(feature_id)
        if feature is None:
            raise ValueError(f"stamp-owned feature absent from canonical world: {feature_id}")
        if (decision.get("disposition") != "surface_decal"
                or decision.get("attachment_state") != "resolved"):
            rows.append({
                "feature_id": feature_id,
                "terminal_state": "skipped_with_reason",
                "reason": decision.get("reason") or "non-emitting representation",
                "stamp_layers": [],
            })
            continue
        geometry = feature.get("geometry") or {}
        semantic_class = str(feature.get("semantic_class") or "")
        layers = []
        if semantic_class in ("road_marking", "fixture"):
            if geometry.get("kind") != "point":
                rows.append({
                    "feature_id": feature_id,
                    "terminal_state": "new_asset_required",
                    "reason": "point surface representation requires point canonical geometry",
                    "stamp_layers": [],
                })
                continue
            attachment = decision.get("attachment") or {}
            if attachment.get("record_type") != "attachment":
                raise ValueError(f"resolved stamp row has no attachment: {feature_id}")
            pose = resolve(attachment, index)
            family = str(decision.get("decal_family") or (
                "fixture" if semantic_class == "fixture" else "other"))
            width = SURFACE_DECAL_DIAMETER_M.get(
                family, SURFACE_DECAL_DIAMETER_M["other"])
            layers.append({
                "role": "pedestrian_area" if semantic_class == "fixture" else "crossing",
                "geometry_kind": "point",
                "coordinates_epsg32654": [float(pose["E"]), float(pose["N"])],
                "width_m": float(width),
                "width_source": "representation:surface_decal_diameter",
                "z_offset": 0,
            })
        else:
            feature_tags = _tags(feature, observations)
            role, width, width_source = _surface_role_width(
                feature, feature_tags, rules, corridors)
            if geometry.get("kind") not in ("point", "polyline", "polygon"):
                rows.append({
                    "feature_id": feature_id,
                    "terminal_state": "new_asset_required",
                    "reason": "unsupported canonical surface geometry "
                              + str(geometry.get("kind")),
                    "stamp_layers": [],
                })
                continue
            coordinates = geometry.get("coordinates", [])
            geometry_kind = geometry["kind"]
            layout = (_road_corridor_layout(feature, feature_tags, corridors)
                      if semantic_class == "road" and geometry["kind"] == "polyline" else None)
            strip = (_road_corridor_polygon(coordinates, corridors[str(feature.get("feature_id"))],
                                            _road_default_width(feature_tags, rules))
                     if layout is not None else None)
            if strip is not None:
                coordinates, geometry_kind = strip, "polygon"
                width_source += ("; painted as default strip UNION the station polygon "
                                 "(each side max(default / 2, min(local clearance, road median)))")
            elif layout is not None:
                # FL-4934 v6: no clean polygon, so this road keeps its default
                # strip unchanged rather than any narrower fallback.
                role, width, width_source = _surface_role_width(feature, feature_tags, rules, None)
            layers.append({
                "role": role,
                "geometry_kind": geometry_kind,
                "coordinates_epsg32654": coordinates,
                "width_m": float(width),
                "width_source": width_source,
                "z_offset": 0,
            })
        rows.append({
            "feature_id": feature_id,
            "semantic_class": semantic_class,
            "representation_id": decision.get("representation_id"),
            "attachment_id": (decision.get("attachment") or {}).get("attachment_id"),
            "terminal_state": "painted",
            "reason": "exact stamp layers compiled before serialization",
            "stamp_layers": layers,
        })
    document = {
        "schema": SCHEMA,
        "inputs": {
            "canonical": str(Path(canonical_path).resolve()),
            "canonical_sha256": _sha256(canonical_path),
            "representation_plan": str(Path(representation_plan_path).resolve()),
            "representation_plan_sha256": _sha256(representation_plan_path),
            "rules": str(Path(rules_path).resolve()),
            "rules_sha256": _sha256(rules_path),
        },
        "rows": rows,
    }
    if road_corridors_path is not None:
        # FL-4934: present only with a corridor input, so the plan without one
        # stays byte-identical.
        document["inputs"]["road_corridors"] = str(Path(road_corridors_path).resolve())
        document["inputs"]["road_corridors_sha256"] = _sha256(road_corridors_path)
    return document


def write(canonical_path: Path, representation_plan_path: Path,
          rules_path: Path, out_path: Path,
          road_corridors_path: Path | None = None) -> dict:
    document = build(canonical_path, representation_plan_path, rules_path,
                     road_corridors_path)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return document


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--canonical", type=Path, required=True)
    parser.add_argument("--representation-plan", type=Path, required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--road-corridors", type=Path, default=None,
                        help="compile_lidar_road_corridor.py output (FL-4934)")
    args = parser.parse_args(argv)
    write(args.canonical, args.representation_plan, args.rules, args.out,
          args.road_corridors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
