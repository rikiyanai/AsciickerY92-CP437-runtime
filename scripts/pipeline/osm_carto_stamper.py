#!/usr/bin/env python3
"""Stamp an OSM-Carto-like raster into A3D terrain visual cells.

Metadata outputs (compact sidecar split, not giant per-cell JSON):
  <out>.carto_stamp.json       — run summary + index (backward-compatible)
  <out>.carto_features.jsonl   — one JSON object per extracted OSM feature
  <out>.carto_cells.bin        — compact binary per painted cell
  <out>.carto_roles.json       — role id lookup table
  <out>.carto_topology.json    — topology provenance summary
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import shutil
import struct
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter, OrderedDict
from pathlib import Path

from PIL import Image

PIPELINE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PIPELINE_DIR.parents[1]
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

from osm_carto_classify import MAT_BUILDING, MAT_GRASS, MAT_LAND, MAT_PAVEMENT, MAT_ROAD, MAT_WATER, classify_carto_pixel, material_debug_rgb
from osm_carto_render import CARTO_STYLE, CartoFeature, render_osm_carto, style_for
from osm_to_cell import latlon_to_world, load_run_params


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


post = _load_module("sbu_satellite_style_postprocess_for_carto", PIPELINE_DIR / "sbu_satellite_style_postprocess.py")
fmt = post.fmt
PATCH_WORLD = post.PATCH_WORLD
VERTEX_STEP = post.VERTEX_STEP


ROAD_TYPES = {
    "motorway": "road_primary",
    "trunk": "road_primary",
    "primary": "road_primary",
    "secondary": "road_secondary",
    "tertiary": "road_tertiary",
    "unclassified": "road_residential",
    "residential": "road_residential",
    "service": "road_residential",
}

NATURAL_HEIGHT_ROLES = {"grass", "wood", "garden", "hedge", "tree"}
FLAT_HEIGHT_MATERIALS = {MAT_WATER, MAT_LAND, MAT_PAVEMENT, MAT_ROAD, MAT_BUILDING}

# ---- Role ID table ----------------------------------------------------------
# Populated in stamp() and written to carto_roles.json.

_ROLE_IDS: dict[str, int] = {}
_ROLE_NAMES: list[str] = []


def _role_id(role: str) -> int:
    if role not in _ROLE_IDS:
        _ROLE_IDS[role] = len(_ROLE_NAMES)
        _ROLE_NAMES.append(role)
    return _ROLE_IDS[role]


# ---------------------------------------------------------------------------


def _feature_type(tags: dict[str, str], is_polygon: bool) -> tuple[str | None, float]:
    highway = tags.get("highway")
    if tags.get("natural") == "water" or tags.get("amenity") == "fountain" or tags.get("waterway"):
        return "water", 0.0
    if tags.get("building") or tags.get("building:part"):
        return "building", 0.0
    if tags.get("amenity") in {"parking", "parking_space"} or tags.get("parking"):
        return "parking", 0.0
    if tags.get("place") == "square" or tags.get("man_made") == "courtyard":
        return "pedestrian_area", 0.0
    if tags.get("leisure") in {"pitch", "sports_centre"}:
        return "sport_court", 0.0
    if tags.get("man_made") == "planter" or tags.get("garden:type") in {"flowerbed", "rock_garden"} or tags.get("landuse") == "flowerbed":
        return "garden", 0.0
    if tags.get("barrier") == "hedge":
        return "hedge", 2.0
    if tags.get("natural") == "wood" or tags.get("landuse") == "forest":
        return "wood", 0.0
    if tags.get("landuse") == "grass" or tags.get("leisure") in {"garden", "park", "outdoor_seating"}:
        return "grass", 0.0
    if tags.get("surface") in {"asphalt", "concrete", "concrete:plates", "paved", "paving_stones", "brick"} and is_polygon:
        return "pedestrian_area", 0.0
    if highway == "pedestrian":
        return ("pedestrian_area" if is_polygon else "footway"), 8.0
    if highway == "footway":
        return "footway", 5.0 if tags.get("footway") == "crossing" else 3.25
    if highway == "steps":
        return "steps", 4.0
    if highway == "cycleway":
        return "cycleway", 3.0
    if highway == "path":
        return "path", 2.0
    if highway:
        return ROAD_TYPES.get(highway, "road_residential"), post._road_width(tags)
    return None, 0.0


_NODE_POINT_FEATURES: list[tuple[str, str, float, str]] = [
    # (feature_type, tag_key, tag_value_or_none, render_type)
    # Render types: "dot", "dot_small"
    ("tree", "natural", "tree", "dot"),
    ("monument_artwork", "tourism", "artwork", "dot"),
    ("memorial", "historic", "memorial", "dot"),
    ("bicycle_parking", "amenity", "bicycle_parking", "dot"),
    ("bus_stop", "highway", "bus_stop", "dot"),
]


def _extract_node_tags(root: ET.Element) -> dict[str, dict[str, str]]:
    """Extract all tagged nodes with their OSM tags keyed by node id."""
    result: dict[str, dict[str, str]] = {}
    for node in root.findall("node"):
        tags = {tag.attrib.get("k", ""): tag.attrib.get("v", "") for tag in node.findall("tag")}
        if tags:
            result[node.attrib["id"]] = tags
    return result


def extract_carto_features(osm_path: Path, run_root: Path) -> list[CartoFeature]:
    params = load_run_params(run_root)
    root = ET.parse(osm_path).getroot()
    nodes: dict[str, tuple[float, float]] = {}
    for node in root.findall("node"):
        nodes[node.attrib["id"]] = latlon_to_world(float(node.attrib["lat"]), float(node.attrib["lon"]), params)

    features: list[CartoFeature] = []

    # --- Ways ----------------------------------------------------------------
    for way in root.findall("way"):
        refs = [nd.attrib.get("ref") for nd in way.findall("nd")]
        points = [nodes[r] for r in refs if r in nodes]
        if len(points) < 2:
            continue
        tags = {tag.attrib.get("k", ""): tag.attrib.get("v", "") for tag in way.findall("tag")}
        is_polygon = len(points) >= 4 and refs[0] == refs[-1]
        feature_type, width = _feature_type(tags, is_polygon)
        if not feature_type:
            continue
        style = style_for(feature_type)
        features.append(CartoFeature(
            feature_type=feature_type,
            geometry=points,
            is_polygon=is_polygon,
            tags=tags,
            osm_id=way.attrib.get("id", ""),
            osm_type="way",
            z_order=int(style["z"]),
            width_cells=width or float(style.get("width", 0.0) or 0.0),
        ))

    # --- Node point features -------------------------------------------------
    for feature_type, tag_key, tag_value, _render in _NODE_POINT_FEATURES:
        for node in root.findall("node"):
            tags = {tag.attrib.get("k", ""): tag.attrib.get("v", "") for tag in node.findall("tag")}
            if tags.get(tag_key) != tag_value:
                continue
            xy = nodes.get(node.attrib["id"])
            if xy is None:
                continue
            style = style_for(feature_type)
            width = float(style.get("width", 1.0))
            features.append(CartoFeature(
                feature_type=feature_type,
                geometry=[xy],
                is_polygon=False,
                tags=tags,
                osm_id=node.attrib["id"],
                osm_type="node",
                z_order=int(style["z"]),
                width_cells=width,
            ))

    return features


def _terrain_bounds(patches) -> tuple[int, int, int, int]:
    min_x = min(p.x for p in patches) * fmt.VISUAL_CELLS
    min_y = min(p.y for p in patches) * fmt.VISUAL_CELLS
    max_x = (max(p.x for p in patches) + 1) * fmt.VISUAL_CELLS
    max_y = (max(p.y for p in patches) + 1) * fmt.VISUAL_CELLS
    return int(min_x), int(min_y), int(max_x), int(max_y)


def _patch_for_cell(patches_by_xy, cx: int, cy: int):
    px = math.floor(cx / fmt.VISUAL_CELLS)
    py = math.floor(cy / fmt.VISUAL_CELLS)
    patch = patches_by_xy.get((px, py))
    if patch is None:
        return None, 0, 0
    return patch, cx - px * fmt.VISUAL_CELLS, cy - py * fmt.VISUAL_CELLS


def _carto_paintable_ground_cell(patch, vy: int, vx: int) -> bool:
    """Protect only already-baked building cells.

    The Carto stamper runs after the OSM postprocessor, whose intended terrain
    variation raises ordinary ground well above the old baseline+80 heuristic.
    Building bakes are the true protected range in this stage.
    """
    hx = vx * fmt.HEIGHT_CELLS // fmt.VISUAL_CELLS
    hy = vy * fmt.HEIGHT_CELLS // fmt.VISUAL_CELLS
    return not any(
        patch.height[min(fmt.HEIGHT_CELLS, hy + dy)][min(fmt.HEIGHT_CELLS, hx + dx)] > 640
        for dy in range(2)
        for dx in range(2)
    )


def _height_vertex_for_visual_cell(vx: int, vy: int) -> tuple[int, int]:
    return (
        vx * fmt.HEIGHT_CELLS // fmt.VISUAL_CELLS,
        vy * fmt.HEIGHT_CELLS // fmt.VISUAL_CELLS,
    )


def _natural_height_offset(wx: float, wy: float, seed: int = 42) -> int:
    """Return a small deterministic natural-terrain height offset.

    This is intentionally hash-like instead of random state. Re-stamping the
    same map produces the same vertex heights rather than accumulating bumps.
    """
    value = math.sin((wx + seed * 13.0) * 12.9898 + (wy - seed * 7.0) * 78.233)
    hashed = value - math.floor(value)
    return int(round((hashed * 2.0 - 1.0) * 3.0))


def _is_flat_height_cell(patch, vx: int, vy: int) -> bool:
    return (int(patch.visual[vy][vx]) & 0xFF) in FLAT_HEIGHT_MATERIALS


def _apply_natural_height_variation(patches_by_xy, natural_cells: set[tuple[int, int]], seed: int = 42) -> dict[str, int]:
    """Apply deterministic height relief only to interior natural Carto cells.

    Height vertices are shared by neighboring visual cells. To keep roads,
    plazas, parking, water, and buildings flat, this only changes a vertex when
    every visual cell touching that vertex is natural/non-flat terrain.
    """
    changed = 0
    protected_flat_neighbors = 0
    protected_high_vertices = 0
    visited_vertices: set[tuple[int, int, int, int]] = set()

    for cx, cy in natural_cells:
        patch, vx, vy = _patch_for_cell(patches_by_xy, cx, cy)
        if patch is None:
            continue
        hx0, hy0 = _height_vertex_for_visual_cell(vx, vy)
        for dy in (0, 1):
            for dx in (0, 1):
                hx = min(fmt.HEIGHT_CELLS, hx0 + dx)
                hy = min(fmt.HEIGHT_CELLS, hy0 + dy)
                vertex_key = (patch.x, patch.y, hx, hy)
                if vertex_key in visited_vertices:
                    continue
                visited_vertices.add(vertex_key)
                if int(patch.height[hy][hx]) > 640:
                    protected_high_vertices += 1
                    continue

                # A height vertex can influence up to four visual cells. If
                # any of them are flat materials, leave the shared vertex alone
                # so roads/plazas/buildings do not inherit green terrain relief.
                flat_neighbor = False
                for nvy in range(max(0, hy * 2 - 1), min(fmt.VISUAL_CELLS - 1, hy * 2) + 1):
                    for nvx in range(max(0, hx * 2 - 1), min(fmt.VISUAL_CELLS - 1, hx * 2) + 1):
                        if _is_flat_height_cell(patch, nvx, nvy):
                            flat_neighbor = True
                            break
                    if flat_neighbor:
                        break
                if flat_neighbor:
                    protected_flat_neighbors += 1
                    continue

                wx = patch.x * PATCH_WORLD + hx * VERTEX_STEP
                wy = patch.y * PATCH_WORLD + hy * VERTEX_STEP
                target = max(128, min(576, int(post._topology_height(wx, wy)) + _natural_height_offset(wx, wy, seed)))
                if int(patch.height[hy][hx]) != target:
                    patch.height[hy][hx] = target
                    changed += 1

    return {
        "changed_natural_height_vertices": changed,
        "protected_flat_neighbor_vertices": protected_flat_neighbors,
        "protected_high_vertices": protected_high_vertices,
    }


def _flatten_flat_height_cells(patches_by_xy, flat_cells: set[tuple[int, int]]) -> dict[str, int]:
    """Remove postprocessor noise from Carto-painted flat surfaces.

    The material postprocessor runs before Carto stamping and deliberately adds
    height noise for visual texture. After Carto takes over the semantic owner
    map, roads, plazas, parking, land, water, and building footprints must stop
    inheriting that noise or asciiid renders them as diagonal lighting bands.
    """
    changed = 0
    protected_high_vertices = 0
    visited_vertices: set[tuple[int, int, int, int]] = set()

    for cx, cy in flat_cells:
        patch, vx, vy = _patch_for_cell(patches_by_xy, cx, cy)
        if patch is None:
            continue
        hx0, hy0 = _height_vertex_for_visual_cell(vx, vy)
        for dy in (0, 1):
            for dx in (0, 1):
                hx = min(fmt.HEIGHT_CELLS, hx0 + dx)
                hy = min(fmt.HEIGHT_CELLS, hy0 + dy)
                vertex_key = (patch.x, patch.y, hx, hy)
                if vertex_key in visited_vertices:
                    continue
                visited_vertices.add(vertex_key)
                if int(patch.height[hy][hx]) > 640:
                    protected_high_vertices += 1
                    continue

                wx = patch.x * PATCH_WORLD + hx * VERTEX_STEP
                wy = patch.y * PATCH_WORLD + hy * VERTEX_STEP
                target = max(128, min(576, int(post._topology_height(wx, wy))))
                if int(patch.height[hy][hx]) != target:
                    patch.height[hy][hx] = target
                    changed += 1

    return {
        "flattened_flat_height_vertices": changed,
        "protected_high_vertices": protected_high_vertices,
    }


def _owner_index(rgb: tuple[int, int, int]) -> int | None:
    raw = (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]
    return raw - 1 if raw > 0 else None


def _load_topology_metadata(terrain_metadata_path: Path) -> dict:
    """Extract topology provenance from terrain_metadata.json if available."""
    if not terrain_metadata_path.exists():
        return {"_note": "terrain_metadata.json not found"}
    try:
        md = json.loads(terrain_metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return {"_error": "could not parse terrain_metadata.json"}

    result: dict = {}
    for key in ("topology_mesh", "topology_instance_path", "topology_source"):
        if key in md:
            result[key] = md[key]

    result["scene_lat"] = md.get("scene_lat")
    result["scene_lon"] = md.get("scene_lon")
    result["content_scale"] = md.get("content_scale")
    result["carto_paints_materials_not_topology"] = True
    return result


def _build_feature_jsonl(features: list[CartoFeature], run_root: Path, painted_counts: Counter) -> list[dict]:
    """Build one JSON object per extracted OSM feature for carto_features.jsonl."""
    params = load_run_params(run_root)
    records: list[dict] = []
    for fi, f in enumerate(features):
        style = style_for(f.feature_type)
        has_world = bool(f.geometry)
        world_pts = [[round(v, 2) for v in pt] for pt in f.geometry] if has_world else []

        # World bounds
        if has_world:
            xs = [pt[0] for pt in f.geometry]
            ys = [pt[1] for pt in f.geometry]
            world_bounds = {
                "min_x": round(min(xs), 2),
                "min_y": round(min(ys), 2),
                "max_x": round(max(xs), 2),
                "max_y": round(max(ys), 2),
            }
            world_centroid = (round(sum(xs) / len(xs), 2), round(sum(ys) / len(ys), 2))
        else:
            world_bounds = None
            world_centroid = None

        # Lat/lon bounds (reverse project world coords)
        # Store the first vertex's lat/lon as a reference point for point features
        latlon_vertices: list[list[float]] = []
        latlon_point: list[float] | None = None
        if has_world and len(f.geometry) >= 1:
            # We store world coords; lat/lon projection requires the inverse
            # which is parameterized. Store world coords in record.
            # For point features, use the single vertex.
            if len(f.geometry) == 1:
                latlon_point = None  # inverse projection requires osm_params; skip for now
            latlon_vertices = []  # skip inverse for now; use world coords

        rec = {
            "feature_id": fi,
            "osm_type": f.osm_type,
            "osm_id": f.osm_id,
            "feature_kind": f.feature_type,
            "carto_style_rule": f.feature_type,
            "material_role": f.feature_type,
            "z_order": f.z_order,
            "paint_width_cells": round(f.width_cells, 2),
            "painted_cell_count": painted_counts.get(fi, 0),
            "source_tags": dict(sorted(f.tags.items())),
            "world_vertices": world_pts if len(world_pts) <= 40 else world_pts[:40] + ["...truncated"],
            "world_point": world_pts[0] if len(world_pts) == 1 else None,
            "world_bounds": world_bounds,
            "world_centroid": list(world_centroid) if world_centroid else None,
            "is_polygon": f.is_polygon,
        }
        # Strip empty/null fields
        rec = {k: v for k, v in rec.items() if v is not None}
        records.append(rec)
    return records


def stamp(run_root: Path, map_path: Path, osm_path: Path, metadata_path: Path, out_path: Path, *, dry_run: bool, osm_only: bool, force: bool, labels: bool = False) -> dict:
    t0 = time.time()

    # Reset role-id table for this run
    global _ROLE_IDS, _ROLE_NAMES
    _ROLE_IDS.clear()
    _ROLE_NAMES.clear()

    header, patches, materials, raw_fmt_version, instances, player_start, enemy_gens, markers = post._load_a3d(map_path)
    materials = post._normalize_osm_material_palette(materials)
    bounds = _terrain_bounds(patches)
    features = extract_carto_features(osm_path, run_root)
    source, owners = render_osm_carto(features, bounds)

    classified = Image.new("RGB", source.size, (0, 0, 0))
    classified_px = classified.load()
    source_px = source.load()
    owner_px = owners.load()
    patches_by_xy = {(p.x, p.y): p for p in patches}
    min_x, min_y, _, _ = bounds
    painted = Counter()
    feature_painted = Counter()
    feature_painted_counts: Counter = Counter()  # per feature index
    skipped_non_ground = 0
    skipped_existing = 0
    unclassified = 0
    sample_records: list[dict] = []
    natural_height_cells: set[tuple[int, int]] = set()
    flat_height_cells: set[tuple[int, int]] = set()

    # Pre-allocate role IDs for all feature types
    for f in features:
        _role_id(f.feature_type)
    # Also register roles that may only appear in classification
    for role_name in sorted(CARTO_STYLE.keys()):
        _role_id(role_name)

    # ---- Collect per-cell binary records ------------------------------------
    cell_binary_records: list[bytes] = []

    for y in range(source.height):
        cy = min_y + y
        for x in range(source.width):
            cx = min_x + x
            rgb = source_px[x, y]
            material, confidence = classify_carto_pixel(rgb)
            if confidence <= 0.0:
                unclassified += 1
                continue
            classified_px[x, y] = material_debug_rgb(material)
            patch, lx, ly = _patch_for_cell(patches_by_xy, cx, cy)
            if patch is None:
                continue
            if not _carto_paintable_ground_cell(patch, ly, lx):
                skipped_non_ground += 1
                continue
            current = int(patch.visual[ly][lx]) & 0xFF
            if not osm_only and current != post.MAT_GRASS:
                skipped_existing += 1
                continue

            patch.visual[ly][lx] = material.visual
            painted[material.role] += 1
            if material.role in NATURAL_HEIGHT_ROLES:
                natural_height_cells.add((cx, cy))
            elif material.mat_id in FLAT_HEIGHT_MATERIALS:
                flat_height_cells.add((cx, cy))

            owner = _owner_index(owner_px[x, y])
            role_id = _role_id(material.role) if material.role else 0
            flags = 0
            if material.elevation:
                flags |= 1

            if owner is not None and 0 <= owner < len(features):
                feature_painted[features[owner].feature_type] += 1
                feature_painted_counts[owner] += 1
                feat_index = owner
                if len(sample_records) < 120:
                    tags = features[owner].tags
                    sample_records.append({
                        "cell": [cx, cy],
                        "carto_rgb": list(rgb),
                        "rule": features[owner].feature_type,
                        "material_id": material.mat_id,
                        "role": material.role,
                        "confidence": round(confidence, 3),
                        "source_osm": {
                            "type": features[owner].osm_type,
                            "id": features[owner].osm_id,
                            "tags": {k: tags[k] for k in sorted(tags)[:10]},
                        },
                    })
            else:
                feat_index = 0xFFFFFFFF

            # Binary record: uint16 cx, uint16 cy, uint32 feat_index, uint8 mat_id, uint8 shade, uint8 role_id, uint8 flags
            cell_binary_records.append(struct.pack(
                "<HH I 4B",
                cx & 0xFFFF,
                cy & 0xFFFF,
                feat_index,
                material.mat_id & 0xFF,
                material.shade & 0xFF,
                role_id & 0xFF,
                flags & 0xFF,
            ))

    flat_height_summary = _flatten_flat_height_cells(patches_by_xy, flat_height_cells)
    natural_height_summary = _apply_natural_height_variation(patches_by_xy, natural_height_cells)

    # ---- Write output PNGs --------------------------------------------------
    source_path = out_path.with_suffix(out_path.suffix + ".carto_source.png")
    classified_path = out_path.with_suffix(out_path.suffix + ".carto_classified.png")
    stamp_path = out_path.with_suffix(out_path.suffix + ".carto_stamp.json")
    source.save(source_path)
    classified.save(classified_path)

    # ---- Sidecar: carto_features.jsonl --------------------------------------
    features_path = out_path.with_suffix(out_path.suffix + ".carto_features.jsonl")
    feature_records = _build_feature_jsonl(features, run_root, feature_painted_counts)
    with open(features_path, "w", encoding="utf-8") as fh:
        for rec in feature_records:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    # ---- Sidecar: carto_cells.bin -------------------------------------------
    cells_path = out_path.with_suffix(out_path.suffix + ".carto_cells.bin")
    with open(cells_path, "wb") as fh:
        for rec in cell_binary_records:
            fh.write(rec)

    # ---- Sidecar: carto_roles.json ------------------------------------------
    roles_path = out_path.with_suffix(out_path.suffix + ".carto_roles.json")
    roles_data = {str(rid): name for name, rid in sorted(_ROLE_IDS.items(), key=lambda x: x[1])}
    roles_path.write_text(json.dumps(roles_data, indent=2, sort_keys=True), encoding="utf-8")

    # ---- Sidecar: carto_topology.json ---------------------------------------
    topology_path = out_path.with_suffix(out_path.suffix + ".carto_topology.json")
    topology_data = _load_topology_metadata(metadata_path)
    topology_data["cell_bounds"] = list(bounds)
    topology_data["feature_count"] = len(features)
    topology_data["flat_height_normalization"] = flat_height_summary
    topology_data["natural_height_variation"] = natural_height_summary
    topology_path.write_text(json.dumps(topology_data, indent=2, sort_keys=True), encoding="utf-8")

    labels_path = None
    if labels:
        from osm_carto_label_proof import generate_label_proof
        labels_path = out_path.with_suffix(out_path.suffix + ".carto_labels.png")
        generate_label_proof(features_path, topology_path).save(labels_path)

    # ---- Summary (carto_stamp.json, backward-compatible) --------------------
    summary = {
        "map": str(map_path),
        "out": str(out_path),
        "osm": str(osm_path),
        "metadata": str(metadata_path),
        "osm_only": osm_only,
        "dry_run": dry_run,
        "cell_bounds": bounds,
        "feature_count": len(features),
        "feature_types": dict(Counter(f.feature_type for f in features)),
        "painted_roles": dict(painted),
        "painted_feature_types": dict(feature_painted),
        "painted_cells_total": len(cell_binary_records),
        "unclassified_pixels": unclassified,
        "skipped_non_ground": skipped_non_ground,
        "skipped_existing": skipped_existing,
        "carto_source_png": str(source_path),
        "carto_classified_png": str(classified_path),
        "carto_features_jsonl": str(features_path),
        "carto_cells_bin": str(cells_path),
        "carto_roles_json": str(roles_path),
        "carto_topology_json": str(topology_path),
        "carto_labels_png": str(labels_path) if labels_path else None,
        "flat_height_normalization": flat_height_summary,
        "natural_height_variation": natural_height_summary,
        "role_count": len(_ROLE_IDS),
        "samples": sample_records,
        "elapsed_s": round(time.time() - t0, 3),
    }
    stamp_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    if not dry_run:
        backup = out_path.with_suffix(out_path.suffix + ".pre-carto")
        tmp = out_path.with_suffix(out_path.suffix + ".carto-tmp")
        if out_path.exists() and not backup.exists():
            shutil.copy2(out_path, backup)
        shutil.copy2(map_path, tmp)
        post._write_a3d(tmp, header, patches, materials, raw_fmt_version, instances, player_start, enemy_gens, markers)
        os.replace(tmp, out_path)
        summary["backup"] = str(backup)
    return summary


def _resolve(run_root: Path, path: str | None, default_name: str) -> Path:
    if path is None:
        return run_root / default_name
    p = Path(path)
    return p if p.is_absolute() else run_root / p


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--map", required=True)
    parser.add_argument("--osm", default=None)
    parser.add_argument("--metadata", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--osm-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--labels", action="store_true", help="Generate <out>.carto_labels.png proof overlay from sidecar metadata")
    args = parser.parse_args()

    run_root = args.run_root
    map_path = _resolve(run_root, args.map, "output_terrain_only.a3d")
    osm_path = _resolve(run_root, args.osm, "osm_blosm_input.osm")
    metadata_path = _resolve(run_root, args.metadata, "terrain_metadata.json")
    out_path = _resolve(run_root, args.out, args.map)
    features = extract_carto_features(osm_path, run_root)
    if args.stats:
        print(json.dumps({
            "feature_count": len(features),
            "feature_types": dict(Counter(f.feature_type for f in features)),
            "styles": sorted(CARTO_STYLE.keys()),
        }, indent=2, sort_keys=True))
        return 0

    result = stamp(run_root, map_path, osm_path, metadata_path, out_path, dry_run=args.dry_run, osm_only=args.osm_only, force=args.force, labels=args.labels)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
