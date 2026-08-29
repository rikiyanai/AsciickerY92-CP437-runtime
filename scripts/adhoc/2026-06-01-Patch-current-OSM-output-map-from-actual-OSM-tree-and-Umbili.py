# Ad hoc script: Patch current OSM output map from actual OSM tree and Umbilic Torus coordinates
# Created: 2026-06-01
# Canonical gap: OSM fixture enrichment should be an owned sbu_e2e_run.py post-bake step.

#!/usr/bin/env python3
"""Patch the current SBU OSM output map using actual OSM-derived feature positions.

This is intentionally map-local: it edits the retained output.a3d artifact for
manual ASCIIID review. It does not change the canonical OSM pipeline defaults.
"""
from __future__ import annotations

import importlib.util
import json
import math
import shutil
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIR = PROJECT_ROOT / "scripts/pipeline"
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))
RUN_DIR = PROJECT_ROOT / "assets/meshes/osm_runs/sbu_carto_scale225_20260601_canonical_ramps"
MAP_PATH = RUN_DIR / "output.a3d"
CANON_MAP = PROJECT_ROOT / "assets/a3d/game_map_y8.a3d"
FEATURES_JSONL = RUN_DIR / "output_terrain_only.a3d.carto_features.jsonl"
BUILDING_INSTANCES_JSON = RUN_DIR / "building_instances.json"
OSM_PATH = RUN_DIR / "osm_blosm_input.osm"
BACKUP_PATH = PROJECT_ROOT / ".run/osm_backups/sbu_carto_scale225_20260526_output_pre_canonical_cp437_ramps_20260601.a3d"

PATCH_SIZE = 188
MATERIAL_SIZE = 512

TREE_MESH_TRANSFORMS = {
    # Local AKM bounds differ wildly:
    #   street_lamp.akm height ~= 0.375 with z scale 864 -> ~=324 world units.
    #   tree-1.akm height ~= 44.646, tree-2 ~= 30.883, tree-3 ~= 8.314.
    # These per-mesh scales keep every tree well below lamp-post height,
    # with thin footprints and one branchless cypress/pole variant.
    "osm-tree-pole.akm": {"xy": 4.00, "z": 35.40},
    "tree-1.akm": {"xy": 0.36, "z": 4.85},
    "tree-2.akm": {"xy": 0.30, "z": 7.00},
    "tree-3.akm": {"xy": 0.95, "z": 26.00},
    "old-tree-1.akm": {"xy": 0.28, "z": 7.55},
    "old-tree-2.akm": {"xy": 0.32, "z": 7.70},
}

DARK_SANDY_MATERIALS = {
    2: {
        "name": "pavement-dark-sand",
        "bg": [(86, 80, 66), (112, 102, 78), (136, 124, 94), (158, 142, 108)],
        "fg": [(48, 43, 34), (74, 66, 50), (96, 86, 64), (120, 106, 78)],
    },
    3: {
        "name": "open-sand-dirt",
        "bg": [(96, 82, 56), (122, 104, 70), (148, 126, 84), (174, 146, 96)],
        "fg": [(58, 48, 32), (84, 70, 44), (108, 90, 58), (134, 110, 72)],
    },
    5: {
        "name": "building-light-grey",
        "bg": [(102, 106, 106), (132, 136, 136), (164, 168, 168), (196, 200, 200)],
        "fg": [(56, 60, 60), (78, 82, 82), (102, 106, 106), (128, 132, 132)],
    },
}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


a3d_edit = _load_module("a3d_edit_for_osm_patch", PROJECT_ROOT / "docs/agent/cli-anything/a3d_edit.py")
osm_to_cell = _load_module("osm_to_cell_for_osm_patch", PROJECT_ROOT / "scripts/pipeline/osm_to_cell.py")
building_bake = _load_module("building_bake_for_osm_patch", PROJECT_ROOT / "scripts/pipeline/bake_osm_building_footprints.py")


def _header_info(path: Path) -> tuple[int, int, int]:
    with path.open("rb") as f:
        sig = f.read(4)
        if sig != b"AS3D":
            raise ValueError(f"not an A3D file: {path}")
        header_size = struct.unpack("<I", f.read(4))[0]
        num_patches = struct.unpack("<I", f.read(4))[0]
    material_offset = header_size + num_patches * PATCH_SIZE
    return header_size, num_patches, material_offset


def _material_bytes(path: Path, mat_id: int) -> bytes:
    _header, _patches, material_offset = _header_info(path)
    with path.open("rb") as f:
        f.seek(material_offset + mat_id * MATERIAL_SIZE)
        data = f.read(MATERIAL_SIZE)
    if len(data) != MATERIAL_SIZE:
        raise ValueError(f"short material {mat_id} in {path}")
    return data


def _patch_material(path: Path, mat_id: int, data: bytes) -> None:
    if len(data) != MATERIAL_SIZE:
        raise ValueError("material data must be exactly 512 bytes")
    _header, _patches, material_offset = _header_info(path)
    with path.open("r+b") as f:
        f.seek(material_offset + mat_id * MATERIAL_SIZE)
        f.write(data)


def _make_material_bytes(*, bg: list[tuple[int, int, int]], fg: list[tuple[int, int, int]]) -> bytes:
    glyphs = [32, 46, 58, 35]
    out = bytearray()
    for ramp in range(4):
        glyph = glyphs[ramp]
        for shade in range(16):
            band = min(3, shade // 4)
            bg_rgb = bg[band]
            fg_rgb = fg[band]
            out.extend((fg_rgb[0], fg_rgb[1], fg_rgb[2], glyph, bg_rgb[0], bg_rgb[1], bg_rgb[2], 0))
    if len(out) != MATERIAL_SIZE:
        raise AssertionError(f"material size mismatch: {len(out)}")
    return bytes(out)


def _raise_baked_buildings_to_tallest(map_path: Path) -> dict:
    specs = json.loads(BUILDING_INSTANCES_JSON.read_text(encoding="utf-8"))
    target_height = max(int(spec.get("bake_height") or 0) for spec in specs)
    if target_height <= 0:
        raise RuntimeError(f"no positive bake_height values in {BUILDING_INSTANCES_JSON}")

    header, patches, tail = building_bake.load_a3d(map_path)
    patches_by_xy = {(patch.x, patch.y): patch for patch in patches}
    per_building = []
    for spec in specs:
        patched = dict(spec)
        old_height = int(patched.get("bake_height") or 0)
        patched["bake_height"] = max(target_height, old_height)
        result = building_bake._bake_one(
            patches_by_xy,
            patched,
            material_id=5,
            footprint_inset=1.0,
            material_inset=0.0,
        )
        result["old_bake_height"] = old_height
        result["target_bake_height"] = target_height
        per_building.append(result)

    building_bake.write_a3d(map_path, header, patches, tail)
    return {
        "target_bake_height": target_height,
        "buildings": len(specs),
        "height_vertices_written": sum(int(row.get("height_vertices") or 0) for row in per_building),
        "visual_cells_written": sum(int(row.get("visual_cells") or 0) for row in per_building),
        "min_old_bake_height": min(int(spec.get("bake_height") or 0) for spec in specs),
        "max_old_bake_height": max(int(spec.get("bake_height") or 0) for spec in specs),
    }


def _sample_terrain_raw_z(map_path: Path, wx: float, wy: float) -> float:
    ps = a3d_edit.derive_player_start_from_terrain(map_path, wx, wy, spawn_z_elev=0.0)
    if ps is None:
        raise RuntimeError(f"cannot sample terrain at {wx:.2f},{wy:.2f}")
    return float(ps.pos[2])


def _mesh_transform(wx: float, wy: float, wz: float, *, scale_xy: float, scale_z: float, angle_rad: float) -> list[float]:
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    return [
        scale_xy * c, -scale_xy * s, 0.0, 0.0,
        scale_xy * s,  scale_xy * c, 0.0, 0.0,
        0.0,           0.0,          scale_z, 0.0,
        float(wx),     float(wy),    float(wz), 1.0,
    ]


def _y8_reference_torus_transform(wx: float, wy: float, terrain_z: float) -> list[float]:
    """Place torus with the same upright scale/orientation as normal Y8.

    Y8's torus.akm transform is:
      [0,0,-64], [0,4,0], [4,0,0]
    in column-major basis terms. The large 64 factor is the vertical ring
    diameter; the 4 factors keep the sculpture thin instead of a giant slab.
    """
    return [
        0.0, 0.0, -64.0, 0.0,
        0.0, 4.0, 0.0, 0.0,
        4.0, 0.0, 0.0, 0.0,
        float(wx), float(wy), float(terrain_z + 210.0), 1.0,
    ]


def _load_tree_features() -> list[dict]:
    out = []
    for line in FEATURES_JSONL.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("feature_kind") == "tree" and rec.get("world_centroid"):
            out.append(rec)
    out.sort(key=lambda r: int(r.get("feature_id", 0)))
    return out


def _umbilic_torus_world() -> tuple[float, float, dict]:
    root = ET.parse(OSM_PATH).getroot()
    nodes = {
        n.attrib["id"]: (float(n.attrib["lat"]), float(n.attrib["lon"]))
        for n in root.findall("node")
    }
    params = osm_to_cell.load_run_params(RUN_DIR)
    for way in root.findall("way"):
        tags = {t.attrib.get("k"): t.attrib.get("v") for t in way.findall("tag")}
        if tags.get("name") != "Umbilic Torus":
            continue
        pts = [nodes[nd.attrib["ref"]] for nd in way.findall("nd") if nd.attrib["ref"] in nodes]
        if not pts:
            raise RuntimeError("Umbilic Torus way has no resolvable nodes")
        lat = sum(p[0] for p in pts) / len(pts)
        lon = sum(p[1] for p in pts) / len(pts)
        wx, wy = osm_to_cell.latlon_to_world(lat, lon, params)
        return float(wx), float(wy), {"osm_id": way.attrib.get("id"), "tags": tags, "lat": lat, "lon": lon, "node_count": len(pts)}
    raise RuntimeError("Umbilic Torus OSM way not found")


def _append_instances(map_path: Path, trees: list[dict], torus_xy: tuple[float, float]) -> tuple[int, int]:
    pre, fv, instances, tail = a3d_edit.read_a3d_instances(map_path)
    kept = []
    removed = 0
    for inst in instances:
        name = (getattr(inst, "inst_name", "") or "").lower()
        mesh = (getattr(inst, "mesh_name", "") or "").lower()
        if name.startswith("osm_tree_") or name == "osm_umbilic_torus" or mesh in ("umbilic_torus_with_a_twist.akm", "torus.akm"):
            removed += 1
            continue
        kept.append(inst)

    added = []
    tree_meshes = [
        "osm-tree-pole.akm",
        "tree-1.akm",
        "old-tree-1.akm",
        "tree-2.akm",
        "tree-3.akm",
        "old-tree-2.akm",
    ]
    for i, rec in enumerate(trees, start=1):
        wx, wy = [float(v) for v in rec["world_centroid"]]
        wz = _sample_terrain_raw_z(map_path, wx, wy)
        mesh_name = tree_meshes[(i - 1) % len(tree_meshes)]
        # Deterministic size/rotation variation by OSM id; placement stays at source coordinate.
        osm_id = int(str(rec.get("osm_id") or i).split("/")[-1])
        scales = TREE_MESH_TRANSFORMS[mesh_name]
        size_jitter = 0.90 + float(osm_id % 5) * 0.04
        angle = (osm_id % 360) * math.pi / 180.0
        added.append(a3d_edit.A3DInstance(
            mesh_name=mesh_name,
            inst_name=f"osm_tree_{i:03d}_node_{rec.get('osm_id')}",
            transform=_mesh_transform(
                wx,
                wy,
                wz,
                scale_xy=scales["xy"] * size_jitter,
                scale_z=scales["z"] * size_jitter,
                angle_rad=angle,
            ),
            flags=3,
            story_id=-1,
            variant="mesh",
        ))

    tx, ty = torus_xy
    tz = _sample_terrain_raw_z(map_path, tx, ty)
    added.append(a3d_edit.A3DInstance(
        mesh_name="torus.akm",
        inst_name="osm_umbilic_torus",
        transform=_y8_reference_torus_transform(tx, ty, tz),
        flags=3,
        story_id=-1,
        variant="mesh",
    ))

    a3d_edit.write_a3d_instances(map_path, pre, fv, kept + added, tail)
    return removed, len(added)


def main() -> int:
    if not MAP_PATH.exists():
        raise FileNotFoundError(MAP_PATH)
    if not CANON_MAP.exists():
        raise FileNotFoundError(CANON_MAP)
    if not FEATURES_JSONL.exists():
        raise FileNotFoundError(FEATURES_JSONL)
    if not OSM_PATH.exists():
        raise FileNotFoundError(OSM_PATH)

    BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MAP_PATH, BACKUP_PATH)

    # Map-local color fix requested by operator: grass matches canonical map grass;
    # roads become the canonical darker grey/asphalt material.
    _patch_material(MAP_PATH, 1, _material_bytes(CANON_MAP, 1))
    _patch_material(MAP_PATH, 4, _material_bytes(CANON_MAP, 4))
    for mat_id, spec in DARK_SANDY_MATERIALS.items():
        _patch_material(MAP_PATH, mat_id, _make_material_bytes(bg=spec["bg"], fg=spec["fg"]))
    building_patch = _raise_baked_buildings_to_tallest(MAP_PATH)

    trees = _load_tree_features()
    torus_x, torus_y, torus_meta = _umbilic_torus_world()
    removed, added = _append_instances(MAP_PATH, trees, (torus_x, torus_y))

    receipt = {
        "map": str(MAP_PATH),
        "backup_replaced": str(BACKUP_PATH),
        "grass_material_source": str(CANON_MAP) + ":mat1",
        "road_material_source": str(CANON_MAP) + ":mat4",
        "building_patch": building_patch,
        "tree_source": str(FEATURES_JSONL),
        "tree_count": len(trees),
        "torus_source": str(OSM_PATH),
        "torus_world": [round(torus_x, 2), round(torus_y, 2)],
        "torus_osm": torus_meta,
        "removed_prior_patch_instances": removed,
        "added_instances": added,
    }
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
