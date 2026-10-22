#!/usr/bin/env python3
"""Compile authored Y8 tree transforms for the FL-4708 retained-world owner.

The Godot map loader owns terrain decoding.  This compiler owns no runtime
geometry: it turns the surviving A3D mesh-instance facts into a checked,
generated sidecar that World.gd can later consume through its existing retained
static geometry bank.  In particular, it does not instantiate MeshInstance3D,
tree_varied.tscn, or any legacy falling-leaf owner.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import struct
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
# FL-4834 elected this richer map for both placement and terrain. The retired
# reduced twin is only a material-palette reference, never an authoring source.
DEFAULT_SOURCE = REPO_ROOT / "godot_project/assets/maps/game_map_y8.a3d"
RUNTIME_TERRAIN_MAP = REPO_ROOT / "godot_project/assets/maps/game_map_y8.a3d"
DEFAULT_OUTPUT = (
    REPO_ROOT / "godot_project/assets/world_mesh/fl4708_y8_tree_instances.v1.json"
)
A3D_IMPORT_CORE_PATH = REPO_ROOT / "addons/io_asciicker/scene/a3d_import_core.py"

# These are source mesh identifiers, not a presentation or geometry-owner
# choice.  The generated row keeps the original name so a later retained-world
# source selector can compare procedural and AKM geometry at one unchanged map
# placement.
CANONICAL_TREE_MESHES = frozenset({
    "tree-1.akm",
    "tree-2.akm",
    "tree-3.akm",
    "old-tree-1.akm",
    "old-tree-2.akm",
    "osm-tree-pole.akm",
})

# Historical Y8 asset-normalization scales recovered from the tracked map
# patcher. A3D instance bases already include these mesh-specific factors.
# Dividing the transformed local-up column by the corresponding factor recovers
# the authored per-instance scale used by the procedural retained replacement.
TREE_ASSET_NORMALIZATION = {
    "tree-1.akm": {"xy": 0.36, "z": 4.85},
    "tree-2.akm": {"xy": 0.30, "z": 7.00},
    "tree-3.akm": {"xy": 0.95, "z": 26.00},
    "old-tree-1.akm": {"xy": 0.28, "z": 7.55},
    "old-tree-2.akm": {"xy": 0.32, "z": 7.70},
}

PATCH_BYTES = 188
VISUAL_CELLS = 8
HEIGHT_CELLS = 4
HEIGHT_SCALE = 16.0


def _load_a3d_import_core() -> Any:
    spec = importlib.util.spec_from_file_location("fl4708_a3d_import_core", A3D_IMPORT_CORE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load canonical A3D parser: {A3D_IMPORT_CORE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _is_tree_mesh(mesh_name: str, inst_name: str) -> bool:
    # Some historical A3Ds use generic `tree`; the current Y8 map preserves
    # canonical AKM names.  Keep both forms data-only and reject unrelated
    # meshes containing a coincidental "tree" substring.
    return (
        mesh_name.lower() in CANONICAL_TREE_MESHES
        or mesh_name.lower() == "tree"
        or inst_name.lower() == "tree"
    )


def _godot_transform(tm: list[float]) -> dict[str, list[float]]:
    if len(tm) != 16:
        raise ValueError(f"expected 16 authored transform doubles, got {len(tm)}")
    # Mirrors import_a3d.gd::_read_transform exactly.  A3D stores the affine
    # basis by columns, which Godot Transform3D consumes as basis.x/y/z and
    # origin; no axis guess or terrain snap belongs in this placement compiler.
    return {
        "basis_x": tm[0:3],
        "basis_y": tm[4:7],
        "basis_z": tm[8:11],
        "origin": tm[12:15],
    }


def _decode_terrain(path: Path) -> dict[str, Any]:
    """Decode the terrain prefix independently of the legacy world section."""
    data = path.read_bytes()
    if data[:4] != b"AS3D":
        raise ValueError(f"A3D magic mismatch: {path}")
    header_size, patch_count = struct.unpack_from("<II", data, 4)
    if header_size != 16:
        raise ValueError(f"unsupported A3D header size {header_size}: {path}")
    patches: dict[tuple[int, int], tuple[int, ...]] = {}
    for patch_index in range(patch_count):
        offset = header_size + patch_index * PATCH_BYTES
        if offset + PATCH_BYTES > len(data):
            raise ValueError(f"truncated A3D patch {patch_index}: {path}")
        patch_x, patch_y = struct.unpack_from("<ii", data, offset)
        heights = struct.unpack_from("<25H", data, offset + 136)
        key = (patch_x, patch_y)
        if key in patches:
            raise ValueError(f"duplicate A3D terrain patch {key}: {path}")
        patches[key] = heights
    if not patches:
        raise ValueError(f"A3D contains no terrain patches: {path}")
    xs = [key[0] for key in patches]
    ys = [key[1] for key in patches]
    bounds = {
        "min_patch_x": min(xs),
        "max_patch_x": max(xs),
        "min_patch_y": min(ys),
        "max_patch_y": max(ys),
    }
    # Mirrors A3DMapLoader._center_offset(). The offset is expressed in A3D
    # visual-cell coordinates and is subtracted from authored horizontal origins.
    center_offset = (
        (bounds["min_patch_x"] + bounds["max_patch_x"] + 1) * VISUAL_CELLS / 2.0,
        (bounds["min_patch_y"] + bounds["max_patch_y"] + 1) * VISUAL_CELLS / 2.0,
    )
    return {
        "path": path,
        "patches": patches,
        "patch_count": patch_count,
        "bounds": bounds,
        "center_offset": center_offset,
    }


def _sample_terrain_height(
    terrain: dict[str, Any], source_x: float, source_y: float
) -> float | None:
    patch_x = math.floor(source_x / VISUAL_CELLS)
    patch_y = math.floor(source_y / VISUAL_CELLS)
    heights = terrain["patches"].get((patch_x, patch_y))
    if heights is None:
        return None
    local_x = source_x - patch_x * VISUAL_CELLS
    local_y = source_y - patch_y * VISUAL_CELLS
    hx = max(0.0, min(float(HEIGHT_CELLS), local_x / 2.0))
    hy = max(0.0, min(float(HEIGHT_CELLS), local_y / 2.0))
    x0 = int(math.floor(hx))
    y0 = int(math.floor(hy))
    x1 = min(x0 + 1, HEIGHT_CELLS)
    y1 = min(y0 + 1, HEIGHT_CELLS)
    tx = hx - x0
    ty = hy - y0
    h00 = heights[y0 * 5 + x0]
    h10 = heights[y0 * 5 + x1]
    h01 = heights[y1 * 5 + x0]
    h11 = heights[y1 * 5 + x1]
    top = h00 + (h10 - h00) * tx
    bottom = h01 + (h11 - h01) * tx
    return (top + (bottom - top) * ty) / HEIGHT_SCALE


def _runtime_tree_placement(
    tm: list[float], mesh_name: str, runtime_terrain: dict[str, Any]
) -> dict[str, Any]:
    source_x = tm[12]
    source_y = tm[13]
    authored_y = tm[14] / HEIGHT_SCALE
    center_x, center_y = runtime_terrain["center_offset"]
    ground_y = _sample_terrain_height(runtime_terrain, source_x, source_y)
    if ground_y is None:
        raise ValueError(
            f"authored tree origin ({source_x}, {source_y}) has no runtime terrain patch"
        )

    # A3D X/Y are the horizontal plane. The transformed local-X column therefore
    # supplies the exact authored yaw after the same axis mapping World uses.
    yaw = math.atan2(tm[1], tm[0])
    normalization = TREE_ASSET_NORMALIZATION.get(mesh_name.lower())
    if normalization is None:
        # Generic historical `tree` rows have no recoverable AKM normalization.
        # Preserve their placement and use neutral procedural scale explicitly.
        procedural_scale = 1.0
        scale_provenance = "RECONSTRUCTED_NEUTRAL"
    else:
        # A3D source Z is encoded in sixteenth-height units. Convert the local-up
        # column to Godot units before removing the source AKM's normalization.
        godot_up_length = math.sqrt(
            tm[8] ** 2 + tm[9] ** 2 + (tm[10] / HEIGHT_SCALE) ** 2
        )
        normalized_up = normalization["z"] / HEIGHT_SCALE
        procedural_scale = godot_up_length / normalized_up
        scale_provenance = "AUTHORED_BASIS_DIVIDED_BY_Y8_ASSET_NORMALIZATION"
    if not math.isfinite(procedural_scale) or procedural_scale <= 0.0:
        raise ValueError(f"invalid authored tree scale for {mesh_name}: {procedural_scale}")
    return {
        "world_xz": [source_x - center_x, source_y - center_y],
        "authored_godot_y": authored_y,
        "current_ground_y": ground_y,
        "authored_minus_current_ground_y": authored_y - ground_y,
        "yaw_radians": yaw,
        "procedural_scale": procedural_scale,
        "procedural_scale_provenance": scale_provenance,
        # Every height patch shared with the original Y8 source is byte-identical;
        # runtime-only additive patches (open-water expansion) add terrain where
        # the source had none. The complete authored Y is therefore compatible
        # and remains authoritative; ground_y is retained only as a diagnostic
        # for deliberate source sinks/offsets.
        "vertical_rule": "EXACT_AUTHORED_Y_ON_IDENTICAL_TERRAIN_HEIGHTFIELD",
    }


def _terrain_prefix_sha256(path: Path) -> str:
    data = path.read_bytes()
    if len(data) < 16:
        raise ValueError(f"truncated A3D terrain header: {path}")
    header_size = int.from_bytes(data[4:8], "little")
    patch_count = int.from_bytes(data[8:12], "little")
    terrain_end = header_size + patch_count * 188 + 256 * 512
    if terrain_end > len(data):
        raise ValueError(f"truncated A3D terrain/material section: {path}")
    return hashlib.sha256(data[:terrain_end]).hexdigest()


def _world_format_version(path: Path) -> int:
    data = path.read_bytes()
    header_size = int.from_bytes(data[4:8], "little")
    patch_count = int.from_bytes(data[8:12], "little")
    world_offset = header_size + patch_count * 188 + 256 * 512
    first_word = struct.unpack_from("<i", data, world_offset)[0]
    return -first_word if first_word < 0 else 0


def _mesh_record(
    index: int,
    instance: Any,
    is_tree: bool,
    runtime_terrain: dict[str, Any],
) -> dict[str, Any]:
    tm = [float(value) for value in instance.transform]
    row = {
        "provenance": "AUTHORED_Y8",
        "instance_identity": "y8-instance-%d" % index,
        "source_instance_index": index,
        "source_mesh_name": str(instance.mesh_name),
        "source_instance_name": str(instance.inst_name),
        "flags": int(instance.flags),
        "story_id": int(instance.story_id),
        "authored_transform_column_major_f64": tm,
        "godot_transform": _godot_transform(tm),
        "is_tree": is_tree,
    }
    if is_tree:
        row.update({
            "tree_identity": "y8-instance-%d" % index,
            "authored_geometry_variant": str(instance.mesh_name),
            "geometry_selection": "UNRESOLVED_FL4708_SOURCE",
            "runtime_placement": _runtime_tree_placement(
                tm, str(instance.mesh_name), runtime_terrain
            ),
        })
    return row


def compile_manifest(
    source: Path, runtime_terrain_map: Path = RUNTIME_TERRAIN_MAP,
    allow_no_trees: bool = False,
) -> dict[str, Any]:
    # FL-4798: the runtime terrain defaults to the Godot Y8 copy. A map the
    # OSM pipeline produced is its own runtime terrain, so the same compiler
    # serves it by naming that file here; nothing else about the rows changes.
    source = source.resolve()
    runtime_terrain_map = runtime_terrain_map.resolve()
    source_terrain = _decode_terrain(source)
    runtime_terrain = _decode_terrain(runtime_terrain_map)
    parser = _load_a3d_import_core()
    _header, _patches, _materials, instances, _enemy_gens, _markers = parser.load_a3d_file(source)
    records: list[dict[str, Any]] = []
    other_akm_records: list[dict[str, Any]] = []
    for index, instance in enumerate(instances):
        if instance.variant != "mesh":
            continue
        is_tree = _is_tree_mesh(str(instance.mesh_name), str(instance.inst_name))
        row = _mesh_record(index, instance, is_tree, runtime_terrain)
        if is_tree:
            records.append(row)
        else:
            other_akm_records.append(row)
    records.sort(key=lambda row: row["source_instance_index"])
    other_akm_records.sort(key=lambda row: row["source_instance_index"])
    # FL-4876 PUBLISH-01: an OSM-pipeline map (Numabe) carries its trees as
    # population meshes (linear_street_tree, linear_small_tree), not as the
    # canonical Y8 tree meshes this compiler gives canopy roles to. With
    # --allow-no-trees those meshes stay authored fixtures (other_akm_records)
    # and the chunk catalog compiles them from their own geometry.
    if not records and not allow_no_trees:
        raise ValueError("no authored Y8 tree mesh records found")

    runtime_terrain_sha256 = _terrain_prefix_sha256(runtime_terrain_map)
    source_terrain_sha256 = _terrain_prefix_sha256(source)
    terrain_matches = source_terrain_sha256 == runtime_terrain_sha256
    source_patch_keys = set(source_terrain["patches"])
    runtime_patch_keys = set(runtime_terrain["patches"])
    patch_topology_matches = source_patch_keys == runtime_patch_keys
    matching_height_patches = sum(
        1
        for key in source_patch_keys & runtime_patch_keys
        if source_terrain["patches"][key] == runtime_terrain["patches"][key]
    )
    placement_deltas = [
        float(row["runtime_placement"]["authored_minus_current_ground_y"])
        for row in records
    ]
    # FL-4821 map expansion: pure-additive runtime patches (new terrain where
    # the source had open water, no shared patch byte touched) keep every
    # authored tree placement exact, so they must not block consumption the
    # way a removed or re-heighted source patch does.
    missing_source_patches = sorted(source_patch_keys - runtime_patch_keys)
    changed_height_patches = sorted(
        key
        for key in source_patch_keys & runtime_patch_keys
        if source_terrain["patches"][key] != runtime_terrain["patches"][key]
    )
    added_runtime_patches = sorted(runtime_patch_keys - source_patch_keys)
    runtime_consumption_blocked = bool(missing_source_patches or changed_height_patches)
    return {
        "$schema": "fl4708_y8_tree_instances.v1",
        # FL-4876 PUBLISH-01: an explicit, checkable marker that zero tree
        # records is the declared state of this map, not a lost manifest.
        **({"tree_records_waived": "no_canonical_y8_tree_meshes:"
            "population_trees_compile_as_authored_fixtures"}
           if allow_no_trees and not records else {}),
        "source_a3d_path": source.relative_to(REPO_ROOT).as_posix(),
        "source_a3d_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_world_format_version": _world_format_version(source),
        "source_world_records": len(instances),
        "source_tree_records": len(records),
        "source_other_akm_records": len(other_akm_records),
        "source_terrain_sha256": source_terrain_sha256,
        "runtime_terrain_path": runtime_terrain_map.relative_to(REPO_ROOT).as_posix(),
        "runtime_terrain_sha256": runtime_terrain_sha256,
        "runtime_terrain_matches_source": terrain_matches,
        "terrain_bridge": {
            "source_patch_count": source_terrain["patch_count"],
            "runtime_patch_count": runtime_terrain["patch_count"],
            "patch_topology_matches": patch_topology_matches,
            "added_runtime_patch_count": len(added_runtime_patches),
            "missing_source_patch_count": len(missing_source_patches),
            "changed_height_patch_count": len(changed_height_patches),
            "matching_height_patch_count": matching_height_patches,
            "all_height_patches_match": (
                patch_topology_matches
                and matching_height_patches == source_terrain["patch_count"]
            ),
            "runtime_center_offset": list(runtime_terrain["center_offset"]),
            # FL-4876 PUBLISH-01: with --allow-no-trees there are no tree
            # placements to compare, so the deltas are null, not invented.
            "authored_minus_current_ground_y_min": (
                min(placement_deltas) if placement_deltas else None),
            "authored_minus_current_ground_y_max": (
                max(placement_deltas) if placement_deltas else None),
            "authored_minus_current_ground_y_mean": (
                sum(placement_deltas) / len(placement_deltas)
                if placement_deltas else None),
            "tree_anchor_coverage": len(records),
        },
        # A different terrain hash no longer blocks placement by itself. Exact
        # authored placement remains meaningful because every shared height
        # patch matches; visual/material bytes account for the prefix hash,
        # and pure-additive runtime patches (open-water expansion) move no tree.
        "runtime_consumption_blocked": runtime_consumption_blocked,
        "runtime_consumption_blocker": "" if not runtime_consumption_blocked else (
            "canonical and runtime A3D heightfields differ: "
            f"missing_source={len(missing_source_patches)} "
            f"changed_heights={len(changed_height_patches)}"
        ),
        "transform_contract": "import_a3d.gd::_read_transform column-major Transform3D",
        "runtime_coordinate_contract": (
            "A3D origin X/Y minus A3DMapLoader center offset -> Godot X/Z; "
            "A3D origin Z/16 -> exact authored Godot Y"
        ),
        "records": records,
        "other_akm_records": other_akm_records,
    }


def _write_or_check(manifest: dict[str, Any], output: Path, check: bool) -> int:
    rendered = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    if check:
        if not output.is_file() or output.read_text() != rendered:
            print(f"FL4708_Y8_TREE_MANIFEST_STALE output={output}")
            return 1
        print(f"FL4708_Y8_TREE_MANIFEST_OK records={len(manifest['records'])} output={output}")
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered)
    print(f"FL4708_Y8_TREE_MANIFEST_WRITTEN records={len(manifest['records'])} output={output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--runtime-terrain",
        type=Path,
        help="A3D the runtime loads (default: the same document as --source)",
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--allow-no-trees", action="store_true",
        help="accept a map with no canonical Y8 tree meshes (OSM-pipeline maps)",
    )
    args = parser.parse_args()
    return _write_or_check(
        compile_manifest(args.source, args.runtime_terrain or args.source,
                         allow_no_trees=args.allow_no_trees),
        args.output, args.check,
    )


if __name__ == "__main__":
    raise SystemExit(main())
