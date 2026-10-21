#!/usr/bin/env python3
"""Audit static route composition before a headed Classic run.

Artifact existence does not prove that a shopfront faces the route, that a
station's parts form one readable assembly, or that repeated streetscape
features cover the walk.  This audit evaluates those static relationships in
the shared metric/Y9 frame.  It remains a prelaunch hypothesis: only headed
Classic evidence can prove actual visibility.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.pipeline.compile_route_walkability import _project_route


SCHEMA = "y92.route_view_composition.v1"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _nearest_route(
    point: tuple[float, float], route: list[tuple[float, float]], scale: float
) -> dict[str, Any]:
    best: tuple[float, float, tuple[float, float]] | None = None
    progress = 0.0
    for a, b in zip(route, route[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length <= 1e-12:
            continue
        t = max(0.0, min(1.0, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy)
                              / (length * length)))
        nearest = (a[0] + t * dx, a[1] + t * dy)
        candidate = (math.dist(point, nearest), progress + t * length, nearest)
        if best is None or candidate[0] < best[0]:
            best = candidate
        progress += length
    if best is None:
        raise ValueError("route has no nonzero segments")
    return {
        "offset_m": best[0] / scale,
        "progress_m": best[1] / scale,
        "nearest_world_xy": [best[2][0], best[2][1]],
    }


def _position(row: dict[str, Any]) -> tuple[float, float] | None:
    anchor = row.get("front_anchor_world_xy")
    if isinstance(anchor, list) and len(anchor) >= 2:
        return float(anchor[0]), float(anchor[1])
    transform = row.get("transform")
    if isinstance(transform, list) and len(transform) >= 14:
        return float(transform[12]), float(transform[13])
    return None


def _required_match(row: dict[str, Any], requirement: dict[str, Any]) -> bool:
    if requirement.get("feature_id") and row.get("feature_id") != requirement["feature_id"]:
        return False
    if requirement.get("label") and row.get("label") != requirement["label"]:
        return False
    if (requirement.get("representation_kind")
            and row.get("representation_kind") != requirement["representation_kind"]):
        return False
    return True


def audit(
    *, route_path: Path, terrain_metadata_path: Path,
    place_front_receipt_path: Path, infrastructure_receipt_path: Path,
    streetscape_receipt_path: Path, requirements_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    requirements_doc = _load(requirements_path)
    contract = requirements_doc.get("route_composition_requirements", {})
    route, _ = _project_route(route_path, terrain_metadata_path)
    metadata = _load(terrain_metadata_path)
    scale = float(metadata.get("projection", metadata)["content_scale"])
    route_length_m = sum(math.dist(a, b) for a, b in zip(route, route[1:])) / scale

    place_receipt = _load(place_front_receipt_path)
    infra_receipt = _load(infrastructure_receipt_path)
    streetscape_receipt = _load(streetscape_receipt_path)

    default_offset = float(contract.get("default_place_max_route_offset_m", 60.0))
    minimum_facing_dot = float(contract.get("minimum_place_facing_dot", 0.15))
    place_rows: list[dict[str, Any]] = []
    for row in place_receipt.get("instances", []):
        point = _position(row)
        if point is None:
            continue
        route_hit = _nearest_route(point, route, scale)
        normal = row.get("front_presentation_normal_xy")
        facing_dot = None
        if isinstance(normal, list) and len(normal) >= 2:
            toward_route = (
                route_hit["nearest_world_xy"][0] - point[0],
                route_hit["nearest_world_xy"][1] - point[1],
            )
            magnitude = math.hypot(*toward_route)
            if magnitude > 1e-9:
                facing_dot = (float(normal[0]) * toward_route[0]
                              + float(normal[1]) * toward_route[1]) / magnitude
        place_rows.append({
            "feature_id": row.get("feature_id"),
            "label": row.get("label"),
            "representation_kind": row.get("representation_kind"),
            "rendered_text": row.get("rendered_text"),
            "route_offset_m": round(route_hit["offset_m"], 3),
            "route_progress_m": round(route_hit["progress_m"], 3),
            "facing_dot_toward_route": (
                round(facing_dot, 6) if facing_dot is not None else None
            ),
            "static_route_view_candidate": (
                route_hit["offset_m"] <= default_offset
                and facing_dot is not None and facing_dot >= minimum_facing_dot
            ),
        })

    required_places = []
    required_places_pass = True
    for requirement in contract.get("required_places", []):
        matches = [row for row in place_rows if _required_match(row, requirement)]
        max_offset = float(requirement.get("max_route_offset_m", default_offset))
        must_face_route = bool(requirement.get("must_face_route", True))
        rendered_text_required = bool(requirement.get("rendered_text_required", False))
        match = matches[0] if matches else None
        passed = bool(
            match
            and match["route_offset_m"] <= max_offset
            and (not must_face_route or (
                match["facing_dot_toward_route"] is not None
                and match["facing_dot_toward_route"] >= minimum_facing_dot
            ))
            and (not rendered_text_required or match.get("rendered_text"))
        )
        required_places_pass &= passed
        required_places.append({
            **requirement,
            "passed": passed,
            "observed": match,
        })

    infra_counts: dict[str, int] = {}
    infra_positions: dict[str, list[tuple[float, float]]] = {}
    for row in infra_receipt.get("instances", []):
        semantic = row.get("semantic_class")
        if not semantic:
            continue
        infra_counts[semantic] = infra_counts.get(semantic, 0) + 1
        point = _position(row)
        if point is not None:
            infra_positions.setdefault(semantic, []).append(point)

    required_infrastructure = []
    required_infra_pass = True
    for semantic, minimum in contract.get("required_infrastructure_counts", {}).items():
        observed = infra_counts.get(semantic, 0)
        passed = observed >= int(minimum)
        required_infra_pass &= passed
        required_infrastructure.append({
            "semantic_class": semantic, "minimum": int(minimum),
            "observed": observed, "passed": passed,
        })

    station_semantics = list(contract.get("station_assembly_semantics", []))
    station_points = [point for semantic in station_semantics
                      for point in infra_positions.get(semantic, [])]
    station_max_span_m = 0.0
    for a in station_points:
        for b in station_points:
            station_max_span_m = max(station_max_span_m, math.dist(a, b) / scale)
    station_max_allowed = float(contract.get("station_assembly_max_span_m", 80.0))
    station_composed = bool(station_points) and station_max_span_m <= station_max_allowed
    station_identity_minimum = int(contract.get("station_identity_labels_minimum", 0))
    station_identity_labels = [
        row.get("dimensions", {}).get("rendered_identity_text")
        for row in infra_receipt.get("instances", [])
        if row.get("semantic_class") == "station_entrance_assembly_proposal"
        and row.get("dimensions", {}).get("rendered_identity_text")
    ]
    station_identity_pass = len(station_identity_labels) >= station_identity_minimum

    density_rows = []
    density_pass = True
    for semantic, rule in contract.get("streetscape_route_coverage", {}).items():
        bin_m = float(rule.get("bin_m", 60.0))
        max_offset = float(rule.get("max_route_offset_m", 25.0))
        minimum_fraction = float(rule.get("minimum_covered_fraction", 0.0))
        bin_count = max(1, math.ceil(route_length_m / bin_m))
        covered: set[int] = set()
        candidates = []
        for row in streetscape_receipt.get("instances", []):
            observed_kind = row.get("streetscape_kind")
            if observed_kind is None and str(row.get("feature_id", "")).startswith("style:"):
                observed_kind = str(row["feature_id"]).split(":", 2)[1]
            if observed_kind != semantic:
                continue
            point = _position(row)
            if point is None:
                continue
            hit = _nearest_route(point, route, scale)
            if hit["offset_m"] <= max_offset:
                covered.add(min(bin_count - 1, int(hit["progress_m"] // bin_m)))
                candidates.append(hit)
        fraction = len(covered) / bin_count
        passed = fraction >= minimum_fraction
        density_pass &= passed
        density_rows.append({
            "semantic_class": semantic,
            "route_bins": bin_count,
            "covered_bins": sorted(covered),
            "covered_fraction": round(fraction, 6),
            "minimum_covered_fraction": minimum_fraction,
            "candidate_count_near_route": len(candidates),
            "passed": passed,
        })

    static_pass = (
        required_places_pass and required_infra_pass and station_composed
        and station_identity_pass and density_pass
    )
    result = {
        "schema": SCHEMA,
        "stage": "static_route_composition_audit",
        "inputs": {
            "route": str(route_path.resolve()),
            "requirements": str(requirements_path.resolve()),
            "place_front_receipt": str(place_front_receipt_path.resolve()),
            "infrastructure_receipt": str(infrastructure_receipt_path.resolve()),
            "streetscape_receipt": str(streetscape_receipt_path.resolve()),
        },
        "route_length_m": round(route_length_m, 3),
        "place_fronts": place_rows,
        "required_places": required_places,
        "required_infrastructure": required_infrastructure,
        "station_assembly": {
            "required_semantics": station_semantics,
            "component_count": len(station_points),
            "maximum_component_span_m": round(station_max_span_m, 3),
            "maximum_allowed_span_m": station_max_allowed,
            "passed": station_composed,
        },
        "station_identity": {
            "minimum_labels": station_identity_minimum,
            "rendered_labels": station_identity_labels,
            "passed": station_identity_pass,
        },
        "streetscape_route_coverage": density_rows,
        "gates": {
            "required_place_fronts_are_route_facing": required_places_pass,
            "required_infrastructure_is_present": required_infra_pass,
            "station_components_form_one_static_assembly": station_composed,
            "station_identity_geometry_present": station_identity_pass,
            "streetscape_covers_route": density_pass,
            "static_route_composition_ready": static_pass,
            "headed_classic_visibility_proven": False,
        },
        "claim_limit": (
            "static geometry and route-facing checks are prelaunch evidence only; "
            "headed Classic traversal must still prove visibility, scale, occlusion, "
            "readability, collision, and place recognition"
        ),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--terrain-metadata", type=Path, required=True)
    parser.add_argument("--place-front-receipt", type=Path, required=True)
    parser.add_argument("--infrastructure-receipt", type=Path, required=True)
    parser.add_argument("--streetscape-receipt", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(
        route_path=args.route, terrain_metadata_path=args.terrain_metadata,
        place_front_receipt_path=args.place_front_receipt,
        infrastructure_receipt_path=args.infrastructure_receipt,
        streetscape_receipt_path=args.streetscape_receipt,
        requirements_path=args.requirements, output_path=args.output,
    )
    print(json.dumps(result["gates"], sort_keys=True))
    return 0 if result["gates"]["static_route_composition_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
