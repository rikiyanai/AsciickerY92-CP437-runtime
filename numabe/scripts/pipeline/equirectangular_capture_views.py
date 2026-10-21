#!/usr/bin/env python3
"""Derive calibrated rectilinear capture sequences from retained 360 panoramas."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np


SOURCE_SCHEMA = "y92.capture_source_index.v1"


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rectilinear_view(
    panorama: np.ndarray,
    *,
    yaw_offset_deg: float,
    pitch_deg: float = 0.0,
    horizontal_fov_deg: float = 90.0,
    width_px: int = 512,
    height_px: int = 512,
) -> np.ndarray:
    """Project an equirectangular panorama into one pinhole camera image.

    The panorama centre is yaw zero. Positive yaw turns right and positive
    pitch looks up. The returned image has a known rectilinear model, centred
    principal point, and the exact horizontal FOV supplied here.
    """
    if panorama.ndim not in (2, 3):
        raise ValueError("panorama must be a grayscale or color image")
    if panorama.shape[1] < 2 or panorama.shape[0] < 2:
        raise ValueError("panorama is too small")
    if not 1.0 < horizontal_fov_deg < 179.0:
        raise ValueError("horizontal_fov_deg must be between 1 and 179")
    if width_px < 2 or height_px < 2:
        raise ValueError("output dimensions must be at least 2x2")

    tan_half_x = math.tan(math.radians(horizontal_fov_deg) / 2.0)
    focal_px = width_px / (2.0 * tan_half_x)
    # Sample pixel centres using the same principal-point and focal convention
    # consumed by camera_solve.capture. Using linspace endpoints here would
    # instead describe a (width-1)/2 focal length while declaring width/2.
    xs = (np.arange(width_px, dtype=np.float64) + 0.5 - width_px / 2.0) / focal_px
    ys = (np.arange(height_px, dtype=np.float64) + 0.5 - height_px / 2.0) / focal_px
    ray_x, ray_y = np.meshgrid(xs, ys)
    ray_z = np.ones_like(ray_x)

    pitch = math.radians(pitch_deg)
    cp, sp = math.cos(pitch), math.sin(pitch)
    pitched_y = cp * ray_y - sp * ray_z
    pitched_z = sp * ray_y + cp * ray_z

    yaw = math.radians(yaw_offset_deg)
    cy, sy = math.cos(yaw), math.sin(yaw)
    world_x = cy * ray_x + sy * pitched_z
    world_y = pitched_y
    world_z = -sy * ray_x + cy * pitched_z
    norm = np.sqrt(world_x * world_x + world_y * world_y + world_z * world_z)
    longitude = np.arctan2(world_x, world_z)
    latitude = np.arcsin(np.clip(-world_y / norm, -1.0, 1.0))

    source_h, source_w = panorama.shape[:2]
    map_x = ((longitude / (2.0 * math.pi) + 0.5) * source_w).astype(np.float32)
    map_y = ((0.5 - latitude / math.pi) * source_h).astype(np.float32)
    map_y = np.clip(map_y, 0.0, float(source_h - 1))
    return cv2.remap(
        panorama, map_x, map_y, interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_WRAP,
    )


def _offset_token(value: float) -> str:
    sign = "p" if value >= 0 else "m"
    magnitude = abs(value)
    if float(magnitude).is_integer():
        number = f"{int(magnitude):03d}"
    else:
        number = f"{magnitude:06.2f}".replace(".", "p")
    return f"{sign}{number}"


def derive_views(
    *,
    source_index_path: Path,
    output_dir: Path,
    output_index_path: Path,
    yaw_offsets_deg: tuple[float, ...] = (0.0, 90.0, 180.0, 270.0),
    pitch_deg: float = 0.0,
    horizontal_fov_deg: float = 90.0,
    size_px: int = 512,
    selected_capture_manifest_path: Path | None = None,
    supplemental_survey_path: Path | None = None,
    captures_dir: Path | None = None,
) -> dict[str, Any]:
    source = json.loads(source_index_path.read_text(encoding="utf-8"))
    if source.get("schema") != SOURCE_SCHEMA:
        raise ValueError(f"source index is not {SOURCE_SCHEMA}")
    selected_ids: set[str] | None = None
    selection_digest = None
    if selected_capture_manifest_path is not None:
        manifest = json.loads(selected_capture_manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema") != "y92.route_capture_manifest.v1":
            raise ValueError("selected capture manifest has an unsupported schema")
        selected_ids = {
            str(row["capture_id"])
            for row in manifest.get("captures", [])
            if row.get("selected_keyframe")
        }
        selection_digest = _sha256(selected_capture_manifest_path)

    output_dir.mkdir(parents=True, exist_ok=True)
    source_rows = list(source.get("captures", []))
    known = {str(row["capture_id"]) for row in source_rows}
    if supplemental_survey_path is not None:
        if captures_dir is None:
            raise ValueError("captures_dir is required with supplemental_survey_path")
        survey = json.loads(supplemental_survey_path.read_text(encoding="utf-8"))
        pictures = (((survey.get("providers") or {}).get("mapillary") or {})
                    .get("pictures", []))
        for picture in pictures:
            capture_id = "mapillary_" + str(picture["picture_id"])
            local = captures_dir / f"{capture_id}.jpg"
            if capture_id in known or not local.is_file():
                continue
            camera_type = str((picture.get("extra") or {}).get("camera_type") or "").lower()
            source_rows.append({
                "capture_id": capture_id,
                "local_path": str(local.resolve()),
                "sequence_id": str(picture.get("sequence_id") or "unknown"),
                "lat": picture.get("lat"), "lon": picture.get("lon"),
                "heading_deg": picture.get("heading_deg"),
                "timestamp": picture.get("capture_date"),
                "projection_model": camera_type,
                "sha256": None,
            })
            known.add(capture_id)

    records: list[dict[str, Any]] = []
    component_ids: dict[str, list[str]] = {}
    for record in source_rows:
        capture_id = str(record["capture_id"])
        if selected_ids is not None and capture_id not in selected_ids:
            continue
        if str(record.get("projection_model") or "").lower() != "equirectangular":
            continue
        local_path = Path(str(record["local_path"]))
        source_path = local_path if local_path.is_absolute() else source_index_path.parent / local_path
        panorama = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
        if panorama is None:
            raise ValueError(f"cannot read panorama {source_path}")
        sequence_id = str(record["sequence_id"])
        for yaw_offset in yaw_offsets_deg:
            token = _offset_token(yaw_offset)
            derived_id = f"{capture_id}__yaw_{token}"
            destination = output_dir / f"{derived_id}.jpg"
            view = rectilinear_view(
                panorama,
                yaw_offset_deg=yaw_offset,
                pitch_deg=pitch_deg,
                horizontal_fov_deg=horizontal_fov_deg,
                width_px=size_px,
                height_px=size_px,
            )
            ok, payload = cv2.imencode(
                ".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 95]
            )
            if not ok:
                raise RuntimeError(f"failed to encode {derived_id}")
            destination.write_bytes(payload.tobytes())
            component_id = f"{sequence_id}__yaw_{token}"
            component_ids.setdefault(component_id, []).append(derived_id)
            base_heading = record.get("heading_deg")
            heading = (
                (float(base_heading) + yaw_offset) % 360.0
                if base_heading is not None else None
            )
            derived = dict(record)
            derived.update({
                "capture_id": derived_id,
                "sequence_id": component_id,
                "local_path": str(destination.resolve().relative_to(output_index_path.parent.resolve())),
                "width": size_px,
                "height": size_px,
                "heading_deg": heading,
                "pitch_deg": pitch_deg,
                "projection_model": "rectilinear",
                "horizontal_fov_deg": horizontal_fov_deg,
                "intrinsics_state": "derived_exact_equirectangular_projection",
                "orientation_state": "derived_from_equirectangular_heading_and_view_offset",
                "sha256": _sha256(destination),
                "size_bytes": destination.stat().st_size,
                "derived_from": {
                    "capture_id": capture_id,
                    "source_sha256": record.get("sha256"),
                    "source_projection_model": "equirectangular",
                    "yaw_offset_deg": yaw_offset,
                    "pitch_deg": pitch_deg,
                    "horizontal_fov_deg": horizontal_fov_deg,
                    "resampler": "opencv_remap_linear_wrap",
                    "jpeg_quality": 95,
                },
            })
            records.append(derived)

    if not records:
        raise ValueError("source index contains no selected equirectangular captures")
    components = [
        {
            "component_id": component_id,
            "capture_ids": ids,
            "source_sequence_id": component_id.rsplit("__yaw_", 1)[0],
            "view_yaw_token": component_id.rsplit("__yaw_", 1)[1],
            "evidence_state": "calibrated_virtual_pinhole_sequence_requires_sfm",
        }
        for component_id, ids in sorted(component_ids.items())
    ]
    default = max(components, key=lambda row: (len(row["capture_ids"]), row["component_id"]))
    document = {
        "schema": SOURCE_SCHEMA,
        "source_index_id": "derived_equirectangular_rectilinear_views",
        "source_index": {"path": str(source_index_path), "sha256": _sha256(source_index_path)},
        "selected_capture_manifest": (
            {"path": str(selected_capture_manifest_path), "sha256": selection_digest}
            if selected_capture_manifest_path is not None else None
        ),
        "working_image_policy": {
            "max_edge_px": size_px,
            "projection_resampler": "opencv_remap_linear_wrap",
            "encoding": "jpeg",
            "jpeg_quality": 95,
        },
        "default_reconstruction_component": default["component_id"],
        "reconstruction_components": components,
        "policy": {
            "yaw_offsets_deg": list(yaw_offsets_deg),
            "pitch_deg": pitch_deg,
            "horizontal_fov_deg": horizontal_fov_deg,
            "size_px": size_px,
            "projection": "equirectangular_to_rectilinear",
        },
        "counts": {
            "source_panoramas": len({row["derived_from"]["capture_id"] for row in records}),
            "derived_views": len(records),
            "reconstruction_components": len(components),
        },
        "captures": records,
    }
    output_index_path.parent.mkdir(parents=True, exist_ok=True)
    output_index_path.write_bytes(_canonical_bytes(document))
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-index", type=Path, required=True)
    parser.add_argument("--selected-capture-manifest", type=Path)
    parser.add_argument("--supplemental-survey", type=Path,
                        help="Mapillary survey containing retained captures absent from source-index")
    parser.add_argument("--captures-dir", type=Path,
                        help="local retained capture directory used with --supplemental-survey")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--yaw-offset", type=float, action="append", dest="yaw_offsets")
    parser.add_argument("--pitch-deg", type=float, default=0.0)
    parser.add_argument("--horizontal-fov-deg", type=float, default=90.0)
    parser.add_argument("--size-px", type=int, default=512)
    args = parser.parse_args(argv)
    document = derive_views(
        source_index_path=args.source_index,
        selected_capture_manifest_path=args.selected_capture_manifest,
        supplemental_survey_path=args.supplemental_survey,
        captures_dir=args.captures_dir,
        output_dir=args.output_dir,
        output_index_path=args.out,
        yaw_offsets_deg=tuple(args.yaw_offsets or (0.0, 90.0, 180.0, 270.0)),
        pitch_deg=args.pitch_deg,
        horizontal_fov_deg=args.horizontal_fov_deg,
        size_px=args.size_px,
    )
    print(
        "[equirectangular-capture-views] "
        f"panoramas={document['counts']['source_panoramas']} "
        f"views={document['counts']['derived_views']} "
        f"components={document['counts']['reconstruction_components']}"
    )
    print(f"[equirectangular-capture-views] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
