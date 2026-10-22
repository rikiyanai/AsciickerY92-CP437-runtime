#!/usr/bin/env python3
"""Retain route-adjacent Mapillary frames as a generic capture source index."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any
import urllib.request

from PIL import Image

from scripts.pipeline.route_capture_manifest import (
    RouteMeasure,
    SOURCE_SCHEMA,
    _route_from_geojson,
)


SCHEMA = SOURCE_SCHEMA
SURVEY_SCHEMA = "street-imagery-survey/1"
PROVIDER = "mapillary"


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(row: dict[str, Any], output_dir: Path, user_agent: str) -> tuple[str, Path]:
    picture_id = str(row["picture_id"])
    destination = output_dir / f"mapillary_{picture_id}.jpg"
    if destination.is_file() and destination.stat().st_size:
        return picture_id, destination
    request = urllib.request.Request(
        row["asset_uri"], headers={"User-Agent": user_agent, "Accept": "image/*"}
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        payload = response.read()
        content_type = response.headers.get("Content-Type", "")
    if not payload or not content_type.startswith("image/"):
        raise ValueError(f"Mapillary frame {picture_id} did not return an image")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output_dir, delete=False) as stream:
        stream.write(payload)
        temporary = Path(stream.name)
    temporary.replace(destination)
    return picture_id, destination


def acquire(
    *,
    survey_path: Path,
    route_path: Path,
    output_dir: Path,
    output_index: Path,
    max_lateral_m: float = 25.0,
    min_chainage_m: float | None = None,
    max_chainage_m: float | None = None,
    workers: int = 8,
) -> dict[str, Any]:
    if (
        min_chainage_m is not None
        and max_chainage_m is not None
        and min_chainage_m > max_chainage_m
    ):
        raise ValueError("min_chainage_m cannot exceed max_chainage_m")
    survey = json.loads(survey_path.read_text(encoding="utf-8"))
    if survey.get("schema_version") != SURVEY_SCHEMA:
        raise ValueError(f"survey is not {SURVEY_SCHEMA}")
    provider = survey.get("providers", {}).get(PROVIDER)
    if not provider or provider.get("state") != "PRESENT":
        raise ValueError("Mapillary survey does not contain present coverage")
    coordinates, _ = _route_from_geojson(route_path)
    route = RouteMeasure(coordinates)

    eligible: list[tuple[dict[str, Any], float, float]] = []
    for row in provider.get("pictures", []):
        if not row.get("asset_uri") or not row.get("sequence_id"):
            continue
        projection = route.project(float(row["lon"]), float(row["lat"]))
        inside_chainage = (
            (min_chainage_m is None or projection.chainage_m >= min_chainage_m)
            and (max_chainage_m is None or projection.chainage_m <= max_chainage_m)
        )
        if projection.lateral_m <= max_lateral_m and inside_chainage:
            eligible.append((row, projection.chainage_m, projection.lateral_m))
    if not eligible:
        raise ValueError("Mapillary survey has no downloadable route-adjacent frames")

    user_agent = str(survey.get("user_agent") or "asciicker-Y9-2-mapillary-acquisition/1")
    downloaded: dict[str, Path] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_download, row, output_dir, user_agent): str(row["picture_id"])
            for row, _, _ in eligible
        }
        for future in as_completed(futures):
            picture_id, path = future.result()
            downloaded[picture_id] = path

    sequence_rows: dict[str, list[tuple[dict[str, Any], float, float]]] = {}
    for item in eligible:
        sequence_rows.setdefault(str(item[0]["sequence_id"]), []).append(item)
    sequence_index: dict[str, int] = {}
    for sequence_id, rows in sequence_rows.items():
        rows.sort(key=lambda item: (str(item[0].get("capture_date") or ""), str(item[0]["picture_id"])))
        for ordinal, (row, _, _) in enumerate(rows):
            sequence_index[str(row["picture_id"])] = ordinal

    captures: list[dict[str, Any]] = []
    for row, chainage_m, lateral_m in sorted(
        eligible, key=lambda item: (str(item[0]["sequence_id"]), sequence_index[str(item[0]["picture_id"])])
    ):
        picture_id = str(row["picture_id"])
        path = downloaded[picture_id]
        with Image.open(path) as image:
            width, height = image.size
        author = str(row.get("author") or "Mapillary contributor")
        page_uri = str(row.get("page_uri") or f"https://www.mapillary.com/app/?pKey={picture_id}")
        captures.append({
            "capture_id": f"mapillary_{picture_id}",
            "provider": "Mapillary",
            "source_uri": page_uri,
            "asset_uri": row["asset_uri"],
            "timestamp": row.get("capture_date"),
            "sequence_id": str(row["sequence_id"]),
            "sequence_index": sequence_index[picture_id],
            "lon": float(row["lon"]),
            "lat": float(row["lat"]),
            "altitude_m": None,
            "horizontal_accuracy_m": None,
            "vertical_accuracy_m": None,
            "heading_deg": row.get("heading_deg"),
            "pitch_deg": None,
            "roll_deg": None,
            "orientation_state": "heading_provider_pitch_roll_unavailable",
            "license": "CC-BY-SA-4.0",
            "license_uri": "https://creativecommons.org/licenses/by-sa/4.0/",
            "attribution": f"{author} / Mapillary, CC BY-SA 4.0",
            "width": width,
            "height": height,
            "camera_model": str(row.get("camera") or row.get("extra", {}).get("camera_type") or "Mapillary camera"),
            "projection_model": str(row.get("extra", {}).get("camera_type") or "unknown"),
            "source_dimensions_px": [
                int(row.get("width") or width), int(row.get("height") or height)
            ],
            "focal_length_mm": None,
            "focal_35mm": None,
            "horizontal_fov_deg": None,
            "intrinsics_state": "unknown_mapillary_thumbnail",
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
            "local_path": str(path.resolve().relative_to(output_index.parent.resolve())),
            "retention_permitted": True,
            "provider_detections": [],
            "provider_detection_state": "available_not_fetched",
            "acquisition_route_projection": {
                "chainage_m": round(chainage_m, 3),
                "lateral_m": round(lateral_m, 3),
            },
        })

    document = {
        "schema": SCHEMA,
        "source_index_id": "mapillary_route_capture",
        "source_survey": {"path": str(survey_path), "sha256": _sha256(survey_path)},
        "policy": {
            "max_lateral_m": max_lateral_m,
            "min_chainage_m": min_chainage_m,
            "max_chainage_m": max_chainage_m,
            "asset_variant": "thumb_1024_url",
            "license": "CC-BY-SA-4.0",
            "token_retained": False,
        },
        "counts": {
            "survey_pictures": len(provider.get("pictures", [])),
            "eligible_route_frames": len(captures),
            "sequences": len(sequence_rows),
        },
        "captures": captures,
    }
    output_index.parent.mkdir(parents=True, exist_ok=True)
    output_index.write_bytes(_canonical_bytes(document))
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--survey", type=Path, required=True)
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-lateral-m", type=float, default=25.0)
    parser.add_argument("--min-chainage-m", type=float)
    parser.add_argument("--max-chainage-m", type=float)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args(argv)
    document = acquire(
        survey_path=args.survey,
        route_path=args.route,
        output_dir=args.output_dir,
        output_index=args.out,
        max_lateral_m=args.max_lateral_m,
        min_chainage_m=args.min_chainage_m,
        max_chainage_m=args.max_chainage_m,
        workers=args.workers,
    )
    print(
        "[mapillary-capture-index] "
        f"frames={document['counts']['eligible_route_frames']} "
        f"sequences={document['counts']['sequences']}"
    )
    print(f"[mapillary-capture-index] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
