# Ad hoc script: Patch FL-4131 mesh landmarks into original game map A3D
# Created: 2026-06-07
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Patch FL-4131 mesh landmarks into assets/a3d/game_map_y8_original_game_map.a3d.

The original-map proof lane expects skull/sphere/pyramid meshes to exist in the
map used by the Shape Lab proof. This script preserves the terrain, material
palette, existing instances, player start, enemy generators, and minimap markers,
then appends three FL-4131 mesh instances if they are missing.
"""
from __future__ import annotations

import importlib.util
import shutil
import struct
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
A3D_PATH = REPO_ROOT / "assets/a3d/game_map_y8_original_game_map.a3d"
BACKUP_DIR = REPO_ROOT / "assets/a3d/backups"
MESH_DIR = REPO_ROOT / "assets/meshes"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {rel}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

fmt = _load("a3d_format", "addons/io_asciicker/scene/a3d_format.py")

MATERIAL_COUNT = 256
MATERIAL_SIZE = 512
INST_VISIBLE_USE_TREE = 3
FORMAT_VERSION_OUT = -4

MESH_SPECS = (
    ("fl4131_shape_lab_sphere.akm", "fl4131_original_game_sphere", (-58.0, 60.0, 65.0), (30.0, 30.0, 30.0)),
    ("fl4131_shape_lab_triangle_pyramid.akm", "fl4131_original_game_triangle_pyramid", (0.0, 60.0, 65.0), (40.0, 40.0, 40.0)),
    ("fl4131_shape_lab_skull_like.akm", "fl4131_original_game_skull_like", (58.0, 60.0, 65.0), (72.0, 72.0, 72.0)),
)


def _mesh_transform(pos, scale):
    return [
        float(scale[0]), 0.0, 0.0, 0.0,
        0.0, float(scale[1]), 0.0, 0.0,
        0.0, 0.0, float(scale[2]), 0.0,
        float(pos[0]), float(pos[1]), float(pos[2]), 1.0,
    ]


def read_i32(f):
    raw = f.read(4)
    if len(raw) != 4:
        raise EOFError("expected int32")
    return struct.unpack("<i", raw)[0]


def read_all(path: Path):
    with path.open("rb") as f:
        header = fmt.A3DHeader.from_file(f)
        patches = [fmt.A3DPatch.from_file(f) for _ in range(header.num_patches)]
        materials = f.read(MATERIAL_COUNT * MATERIAL_SIZE)
        if len(materials) != MATERIAL_COUNT * MATERIAL_SIZE:
            raise ValueError("truncated material palette")
        raw_fmt_version = read_i32(f)
        inst_format = -raw_fmt_version if raw_fmt_version < 0 else raw_fmt_version
        inst_count = read_i32(f)
        instances = []
        for _ in range(inst_count):
            instances.append(read_instance_upgrade_legacy_item(f, inst_format))
        player_start = None
        has_player_start = 0
        if inst_format >= 4:
            raw = f.read(4)
            if raw:
                if len(raw) != 4:
                    raise EOFError("truncated player-start flag")
                has_player_start = struct.unpack("<i", raw)[0]
                if has_player_start:
                    player_start = fmt.A3DPlayerStart.from_file(f)
        enemy_gens = []
        raw = f.read(4)
        if raw:
            if len(raw) != 4:
                raise EOFError("truncated enemy count")
            enemy_count = struct.unpack("<i", raw)[0]
            enemy_gens = [fmt.A3DEnemyGen.from_file(f) for _ in range(enemy_count)]
        markers = []
        raw = f.read(4)
        if raw:
            if len(raw) != 4:
                raise EOFError("truncated marker count")
            marker_count = struct.unpack("<i", raw)[0]
            markers = [fmt.A3DMinimapMarker.from_file(f) for _ in range(marker_count)]
        return header, patches, materials, raw_fmt_version, instances, has_player_start, player_start, enemy_gens, markers


def read_instance_upgrade_legacy_item(f, inst_format):
    start = f.tell()
    mesh_id_len = read_i32(f)
    f.seek(start)
    if mesh_id_len != -2:
        return fmt.A3DInstance.from_file(f, format_version=inst_format)
    if inst_format >= 3:
        return fmt.A3DInstance.from_file(f, format_version=inst_format)

    f.seek(start + 4)
    legacy_item_definition_id = read_i32(f)
    legacy_count = read_i32(f)
    inst = fmt.A3DInstance(variant="item")
    inst.item_definition_id = legacy_item_definition_id
    inst.visual_style_id = 1
    inst.presentation_kind_id = 0
    inst.item_count = max(1, legacy_count)
    inst.pos = list(struct.unpack("<fff", f.read(12)))
    inst.yaw = struct.unpack("<f", f.read(4))[0]
    inst.flags = read_i32(f)
    inst.story_id = read_i32(f) if inst_format > 0 else -1
    return inst


def write_all(path: Path, header, patches, materials, instances, has_player_start, player_start, enemy_gens, markers):
    with path.open("wb") as f:
        header.num_patches = len(patches)
        header.write(f)
        for patch in patches:
            patch.write(f)
        f.write(materials)
        f.write(struct.pack("<i", FORMAT_VERSION_OUT))
        f.write(struct.pack("<i", len(instances)))
        for inst in instances:
            inst.write(f)
        f.write(struct.pack("<i", 1 if has_player_start and player_start else 0))
        if has_player_start and player_start:
            player_start.write(f)
        f.write(struct.pack("<i", len(enemy_gens)))
        for gen in enemy_gens:
            gen.write(f)
        f.write(struct.pack("<i", len(markers)))
        for marker in markers:
            marker.write(f)


def main() -> int:
    for mesh_name, _, _, _ in MESH_SPECS:
        p = MESH_DIR / mesh_name
        if not p.is_file():
            raise SystemExit(f"missing mesh asset: {p}")
    header, patches, materials, raw_fmt, instances, has_ps, ps, enemy_gens, markers = read_all(A3D_PATH)
    existing_names = {getattr(i, "inst_name", "") for i in instances if getattr(i, "variant", "") == "mesh"}
    existing_meshes = {getattr(i, "mesh_name", "") for i in instances if getattr(i, "variant", "") == "mesh"}
    added = []
    for mesh_name, inst_name, pos, scale in MESH_SPECS:
        if inst_name in existing_names or mesh_name in existing_meshes:
            continue
        instances.append(fmt.A3DInstance(
            mesh_name=mesh_name,
            inst_name=inst_name,
            transform=_mesh_transform(pos, scale),
            flags=INST_VISIBLE_USE_TREE,
            story_id=-1,
            variant="mesh",
        ))
        added.append(inst_name)
    if not added:
        print("already patched: FL-4131 landmark mesh instances present")
        return 0
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / f"game_map_y8_original_game_map.pre_fl4131_mesh_patch.{int(time.time())}.a3d"
    shutil.copy2(A3D_PATH, backup)
    write_all(A3D_PATH, header, patches, materials, instances, has_ps, ps, enemy_gens, markers)
    print(f"patched {A3D_PATH}")
    print(f"input_format={raw_fmt} output_format={FORMAT_VERSION_OUT}")
    print(f"added={','.join(added)} total_instances={len(instances)} backup={backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
