#!/usr/bin/env python3
"""Lower a measured World IR DEM field into A3D terrain heights.

This is the FL-4814 vertical-terrain owner.  It consumes the accepted DEM
observation artifact, projects it through the exact base-run coordinate frame,
and changes only ``A3DPatch.height``.  Materials and the complete post-patch
A3D payload remain byte-identical.
"""

from __future__ import annotations

import argparse
import heapq
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline import geo_frames as GF
from scripts.pipeline.osm_projection import (
    latlon_to_world as project_latlon_to_world,
    world_to_latlon as project_world_to_latlon,
)
from scripts.pipeline.world_ir import WorldIRStore, assert_valid


PATCH_WORLD = 8.0
HEIGHT_STEP_WORLD = 2.0
BUCKET_WORLD = 16.0
NEIGHBOURS = 4
MAX_BAKE_HEIGHT = 0xFFFF


def _load_run_params(run_root: Path) -> dict:
    metadata_path = run_root / "terrain_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("projection_kind") == "epsg32654_affine":
        return {
            "projection_kind": "epsg32654_affine",
            "origin_easting": float(metadata["origin_easting"]),
            "origin_northing": float(metadata["origin_northing"]),
            "content_scale": float(metadata["content_scale"]),
            "offset_x": float(metadata.get("offset_x", 0.0)),
            "offset_y": float(metadata.get("offset_y", 0.0)),
        }
    required = ("scene_lat", "scene_lon", "content_scale", "terrain_shift")
    missing = [key for key in required if key not in metadata]
    if missing:
        raise ValueError(f"{metadata_path} lacks required projection fields: {missing}")
    return {
        "projection_kind": "blosm_tm",
        "scene_lat": float(metadata["scene_lat"]),
        "scene_lon": float(metadata["scene_lon"]),
        "content_scale": float(metadata["content_scale"]),
        "shift_x": float(metadata["terrain_shift"]["x"]),
        "shift_y": float(metadata["terrain_shift"]["y"]),
        "engine_offset_x": float(metadata.get("engine_offset_x", -32.0)),
        "engine_offset_y": float(metadata.get("engine_offset_y", -32.0)),
        "calibration_offset_x": float(metadata.get("calibration_offset_x", 0.0)),
        "calibration_offset_y": float(metadata.get("calibration_offset_y", 0.0)),
    }


def _latlon_to_world(lat: float, lon: float, params: dict) -> tuple[float, float]:
    if params["projection_kind"] == "epsg32654_affine":
        easting, northing = GF.utm_forward(lon, lat)
        return (
            (easting - params["origin_easting"]) * params["content_scale"] + params["offset_x"],
            (northing - params["origin_northing"]) * params["content_scale"] + params["offset_y"],
        )
    wx, wy = project_latlon_to_world(
        lat, lon, params["scene_lat"], params["scene_lon"],
        params["content_scale"], params["shift_x"], params["shift_y"],
    )
    return (
        wx + params["engine_offset_x"] + params["calibration_offset_x"],
        wy + params["engine_offset_y"] + params["calibration_offset_y"],
    )


def _world_to_latlon(wx: float, wy: float, params: dict) -> tuple[float, float]:
    if params["projection_kind"] == "epsg32654_affine":
        easting = (wx - params["offset_x"]) / params["content_scale"] + params["origin_easting"]
        northing = (wy - params["offset_y"]) / params["content_scale"] + params["origin_northing"]
        lon, lat = GF.utm_inverse(easting, northing)
        return lat, lon
    return project_world_to_latlon(
        wx - params["engine_offset_x"] - params["calibration_offset_x"],
        wy - params["engine_offset_y"] - params["calibration_offset_y"],
        params["scene_lat"], params["scene_lon"], params["content_scale"],
        params["shift_x"], params["shift_y"],
    )


def _raster(samples: list[dict]) -> tuple[int, dict[tuple[int, int], float]]:
    zooms: set[int] = set()
    cells: dict[tuple[int, int], float] = {}
    for sample in samples:
        tile = str(sample.get("tile", "")).split("/")
        cell = sample.get("cell")
        if len(tile) != 3 or not isinstance(cell, list) or len(cell) != 2:
            return 0, {}
        zoom, tile_x, tile_y = map(int, tile)
        zooms.add(zoom)
        cells[(tile_x * 256 + int(cell[0]), tile_y * 256 + int(cell[1]))] = float(
            sample["elevation_m"]
        )
    if len(zooms) != 1:
        raise ValueError(f"DEM samples must use one zoom, got {sorted(zooms)}")
    return next(iter(zooms)), cells


def _bilinear(lon: float, lat: float, zoom: int,
              cells: dict[tuple[int, int], float]) -> float | None:
    n = 2 ** zoom
    gx = (lon + 180.0) / 360.0 * n * 256.0 - 0.5
    lat_rad = math.radians(lat)
    gy = (
        1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi
    ) / 2.0 * n * 256.0 - 0.5
    x0, y0 = math.floor(gx), math.floor(gy)
    corners = (
        cells.get((x0, y0)), cells.get((x0 + 1, y0)),
        cells.get((x0, y0 + 1)), cells.get((x0 + 1, y0 + 1)),
    )
    if any(value is None for value in corners):
        return None
    fx, fy = gx - x0, gy - y0
    z00, z10, z01, z11 = corners
    return (
        z00 * (1.0 - fx) * (1.0 - fy)
        + z10 * fx * (1.0 - fy)
        + z01 * (1.0 - fx) * fy
        + z11 * fx * fy
    )
def _load_a3d_format():
    path = REPO_ROOT / "addons" / "io_asciicker" / "scene" / "a3d_format.py"
    spec = importlib.util.spec_from_file_location("world_ir_dem_a3d_format", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load A3D format module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fmt = _load_a3d_format()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_a3d(path: Path):
    with path.open("rb") as fh:
        header = fmt.A3DHeader.from_file(fh)
        patches = [fmt.A3DPatch.from_file(fh) for _ in range(header.num_patches)]
        tail = fh.read()
    return header, patches, tail


def _write_a3d(path: Path, header, patches, tail: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as fh:
        header.write(fh)
        for patch in patches:
            patch.write(fh)
        fh.write(tail)
    tmp.replace(path)


def _terrain_samples(world_ir_path: Path) -> list[dict]:
    store = WorldIRStore.load(world_ir_path)
    assert_valid(store.to_dict())
    fields = [
        feature["geometry"]
        for feature in store.features
        if feature.get("semantic_class") == "terrain"
        and feature.get("geometry", {}).get("kind") == "field_samples"
    ]
    if len(fields) != 1:
        raise ValueError(
            f"expected exactly one canonical terrain field_samples feature, got {len(fields)}"
        )
    field = fields[0]
    if field.get("frame_id") != GF.FRAME_PROJECTED:
        raise ValueError(
            f"terrain field must use {GF.FRAME_PROJECTED}, got {field.get('frame_id')!r}"
        )
    samples = field.get("samples") or []
    if len(samples) < NEIGHBOURS:
        raise ValueError(f"terrain field needs at least {NEIGHBOURS} samples")
    return samples


class SampleIndex:
    """Deterministic exact nearest-neighbour search over a balanced 2-D tree."""

    def __init__(self, samples: list[tuple[float, float, float, float, float]]):
        self.samples = sorted(samples, key=lambda item: (item[0], item[1], item[2]))
        self.root = self._build(list(range(len(self.samples))), 0)

    def _build(self, indices: list[int], depth: int):
        if not indices:
            return None
        axis = depth & 1
        indices.sort(key=lambda i: (
            self.samples[i][axis], self.samples[i][1 - axis],
            self.samples[i][2], i,
        ))
        middle = len(indices) // 2
        return (
            indices[middle], axis,
            self._build(indices[:middle], depth + 1),
            self._build(indices[middle + 1:], depth + 1),
        )

    def nearest(self, x: float, y: float, count: int = NEIGHBOURS):
        heap: list[tuple[float, int]] = []

        def visit(node) -> None:
            if node is None:
                return
            index, axis, left, right = node
            sample = self.samples[index]
            distance2 = (sample[0] - x) ** 2 + (sample[1] - y) ** 2
            item = (-distance2, -index)
            if len(heap) < count:
                heapq.heappush(heap, item)
            elif item > heap[0]:
                heapq.heapreplace(heap, item)
            delta = (x, y)[axis] - sample[axis]
            near, far = (left, right) if delta <= 0 else (right, left)
            visit(near)
            worst_distance2 = -heap[0][0] if len(heap) == count else math.inf
            if delta * delta <= worst_distance2:
                visit(far)

        visit(self.root)
        ranked = sorted((-distance2, -index) for distance2, index in heap)
        return [self.samples[index] for _distance, index in ranked]

    def interpolate(self, x: float, y: float) -> float:
        neighbours = self.nearest(x, y)
        weighted = 0.0
        weights = 0.0
        for sx, sy, elevation, _easting, _northing in neighbours:
            distance2 = (sx - x) ** 2 + (sy - y) ** 2
            if distance2 <= 1e-18:
                return elevation
            weight = 1.0 / distance2
            weighted += elevation * weight
            weights += weight
        return weighted / weights


def lower(map_path: Path, world_ir_path: Path, run_root: Path, out_path: Path,
          receipt_path: Path) -> dict:
    map_path = Path(map_path)
    world_ir_path = Path(world_ir_path)
    run_root = Path(run_root)
    out_path = Path(out_path)
    receipt_path = Path(receipt_path)
    samples = _terrain_samples(world_ir_path)
    params = _load_run_params(run_root)
    if abs(params["content_scale"] - GF.WORLD_UNITS_PER_METRE) > 1e-9:
        raise ValueError(
            f"run content_scale {params['content_scale']} conflicts with World IR scale "
            f"{GF.WORLD_UNITS_PER_METRE}"
        )

    projected = []
    elevations = []
    for sample in samples:
        easting = float(sample["easting"])
        northing = float(sample["northing"])
        elevation = float(sample["elevation_m"])
        if params["projection_kind"] == "epsg32654_affine":
            wx = ((easting - params["origin_easting"]) * params["content_scale"]
                  + params["offset_x"])
            wy = ((northing - params["origin_northing"]) * params["content_scale"]
                  + params["offset_y"])
        else:
            lon, lat = GF.utm_inverse(easting, northing)
            wx, wy = _latlon_to_world(lat, lon, params)
        projected.append((wx, wy, elevation, easting, northing))
        elevations.append(elevation)
    datum_m = min(elevations)
    source_range_m = max(elevations) - datum_m
    if source_range_m < 1.0:
        raise ValueError(f"source DEM range is effectively flat ({source_range_m:.3f} m)")

    header, patches, tail = _load_a3d(map_path)
    if not patches:
        raise ValueError("A3D contains no terrain patches")
    source_tail_hash = hashlib.sha256(tail).hexdigest()
    index = SampleIndex(projected)
    raster_zoom, raster_cells = _raster(samples)
    interpolation_counts = {"bilinear": 0, "boundary_idw4": 0}
    vertex_values: dict[tuple[int, int], tuple[int, float]] = {}
    raw_min = MAX_BAKE_HEIGHT
    raw_max = 0
    for patch in patches:
        for hy in range(fmt.HEIGHT_CELLS + 1):
            for hx in range(fmt.HEIGHT_CELLS + 1):
                key = (patch.x * fmt.HEIGHT_CELLS + hx, patch.y * fmt.HEIGHT_CELLS + hy)
                cached = vertex_values.get(key)
                if cached is None:
                    wx = patch.x * PATCH_WORLD + hx * HEIGHT_STEP_WORLD
                    wy = patch.y * PATCH_WORLD + hy * HEIGHT_STEP_WORLD
                    lat, lon = _world_to_latlon(wx, wy, params)
                    elevation = (
                        _bilinear(lon, lat, raster_zoom, raster_cells)
                        if raster_cells else None
                    )
                    if elevation is None:
                        elevation = index.interpolate(wx, wy)
                        interpolation_counts["boundary_idw4"] += 1
                    else:
                        interpolation_counts["bilinear"] += 1
                    raw = int(round(GF.BAKE_HEIGHT_ZERO + (elevation - datum_m) * GF.BAKE_UNITS_PER_METRE))
                    if not 0 <= raw <= MAX_BAKE_HEIGHT:
                        raise ValueError(f"DEM height {elevation} m maps outside uint16: {raw}")
                    cached = (raw, elevation)
                    vertex_values[key] = cached
                patch.height[hy][hx] = cached[0]
                raw_min = min(raw_min, cached[0])
                raw_max = max(raw_max, cached[0])

    _write_a3d(out_path, header, patches, tail)
    _h2, output_patches, output_tail = _load_a3d(out_path)
    seam_values: dict[tuple[int, int], int] = {}
    seam_mismatches = 0
    for patch in output_patches:
        for hy in range(fmt.HEIGHT_CELLS + 1):
            for hx in range(fmt.HEIGHT_CELLS + 1):
                key = (patch.x * fmt.HEIGHT_CELLS + hx, patch.y * fmt.HEIGHT_CELLS + hy)
                value = patch.height[hy][hx]
                if key in seam_values and seam_values[key] != value:
                    seam_mismatches += 1
                seam_values[key] = value
    if seam_mismatches:
        raise ValueError(f"lowered A3D has {seam_mismatches} terrain seam mismatches")
    if hashlib.sha256(output_tail).hexdigest() != source_tail_hash:
        raise ValueError("A3D post-patch payload changed during DEM lowering")

    receipt = {
        "schema": "y92.world_ir_dem_a3d_lowering.v1",
        "input_map": str(map_path.resolve()),
        "input_map_sha256": _sha256(map_path),
        "world_ir": str(world_ir_path.resolve()),
        "world_ir_sha256": _sha256(world_ir_path),
        "run_root": str(run_root.resolve()),
        "output_map": str(out_path.resolve()),
        "output_map_sha256": _sha256(out_path),
        "counts": {
            "dem_samples": len(samples),
            "patches": len(patches),
            "unique_height_vertices": len(vertex_values),
            "seam_mismatches": seam_mismatches,
            "bilinear_vertices": interpolation_counts["bilinear"],
            "boundary_idw4_vertices": interpolation_counts["boundary_idw4"],
        },
        "elevation": {
            "vertical_datum": GF.VERTICAL_DATUM_GSI,
            "source_min_m": min(elevations),
            "source_max_m": max(elevations),
            "source_range_m": source_range_m,
            "local_bake_datum_m": datum_m,
            "compiled_raw_min": raw_min,
            "compiled_raw_max": raw_max,
            "compiled_relative_min_m": (raw_min - GF.BAKE_HEIGHT_ZERO) / GF.BAKE_UNITS_PER_METRE,
            "compiled_relative_max_m": (raw_max - GF.BAKE_HEIGHT_ZERO) / GF.BAKE_UNITS_PER_METRE,
        },
        "scale": {
            "world_units_per_metre": GF.WORLD_UNITS_PER_METRE,
            "bake_units_per_world_unit": GF.BAKE_UNITS_PER_WORLD_UNIT,
            "bake_units_per_metre": GF.BAKE_UNITS_PER_METRE,
            "baseline_raw": GF.BAKE_HEIGHT_ZERO,
        },
        "invariants": {
            "a3d_tail_preserved": True,
            "terrain_seams_equal": True,
            "source_dem_nonflat": True,
        },
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--map", required=True, type=Path)
    ap.add_argument("--world-ir", required=True, type=Path)
    ap.add_argument("--run-root", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--receipt", required=True, type=Path)
    args = ap.parse_args(argv)
    receipt = lower(args.map, args.world_ir, args.run_root, args.out, args.receipt)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
