#!/usr/bin/env python3
# FL-4131 shape-lab fixture generator.
#
# Builds an isolated authoring/proof fixture that the FL-4131 Shape Lab UX
# uses to compare:
#   - normal CP437 default/origin baseline
#   - current generated checkpoint baseline (the seven preset names)
#   - shape6-scored extended GlyphId candidates from the admitted set
#
# The fixture is independent of assets/a3d/sandbox_20x20.a3d. It must NOT
# rewrite gameplay material identity, material IDs, collision, network state,
# the production renderer, or anything outside this isolated lab map.
#
# Zones (all glyph_ids drawn from the 512..647 admitted extended set):
#   - shoreline           [544, 545, 542, 543]   water + sand + dirt boundary
#   - grass_flowers       [616, 623, 528, 529]   grass field + sparse flower dots
#   - mountain_snow_strata[556, 557, 645, 521]   stepped stone strata, snow caps
#   - sphere              [633, 634, 635, 636]   curve / dense fills AKM mesh slot
#   - triangle_pyramid    [641, 642, 643, 515]   diagonal / ridges AKM mesh slot
#   - skull_like          [522, 523, 524, 525]   contour / dense-fill AKM mesh slot
#
# Heights and material IDs (Water=0, Grass=1, Dirt=2, Stone=3, Sand=4, Snow=5)
# vary across the 20x20 patch grid to give the lab non-flat shoreline curves,
# diagonals, ridges, dense fills, sparse dots, strata bands, and material
# boundaries. Real mesh instances mark the mesh-zone centers.
#
# Run:
#   python3 scripts/gen_fl4131_shape_lab_fixture.py --force
#   python3 scripts/inspect_a3d.py assets/a3d/fl4131_shape_lab_20x20.a3d
#   python3 scripts/test_fl4131_shape_lab_fixture.py

from __future__ import annotations

import argparse
import importlib.util
import json
import struct
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "addons"))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


a3d_format = _load("a3d_format", "addons/io_asciicker/scene/a3d_format.py")
path_utils = _load("io_asciicker.path_utils", "addons/io_asciicker/path_utils.py")
io_asciicker_pkg = types.ModuleType("io_asciicker")
io_asciicker_pkg.path_utils = path_utils
sys.modules.setdefault("io_asciicker", io_asciicker_pkg)
sys.modules["io_asciicker.path_utils"] = path_utils
default_materials = _load("default_materials", "addons/io_asciicker/scene/default_materials.py")

A3DHeader = a3d_format.A3DHeader
A3DPatch = a3d_format.A3DPatch
A3DInstance = a3d_format.A3DInstance
A3DPlayerStart = a3d_format.A3DPlayerStart
A3DEnemyGen = a3d_format.A3DEnemyGen
WORLD_FORMAT_VERSION = a3d_format.WORLD_FORMAT_VERSION
VISUAL_CELLS = a3d_format.VISUAL_CELLS
HEIGHT_CELLS = a3d_format.HEIGHT_CELLS
get_default_materials_binary = default_materials.get_default_materials_binary

DEFAULT_GRID = 20
MESH_ASSET_DIR = REPO_ROOT / "assets/meshes"

MAT_WATER = 0
MAT_GRASS = 1
MAT_DIRT = 2
MAT_STONE = 3
MAT_SAND = 4
MAT_SNOW = 5

# Vertical band layout (py runs from -half to +half). Picks were chosen so a
# 20x20 grid (half=10) lands six visible zones with non-flat boundaries:
#   shoreline       py in [-10..-6]   low + diagonal water/sand/dirt
#   grass_flowers   py in [-5..-1]    medium height + sparse dots
#   mountain_strata py in [ 0.. 4]    stepped strata + snow caps above threshold
#   mesh slots      py in [ 5.. 9]    flat plateau hosting the three mesh sprites
BAND_SHORELINE_MAX_PY = -6
BAND_GRASS_MAX_PY = -1
BAND_MOUNTAIN_MAX_PY = 4

ZONES = [
    {"zone_id": "shoreline",            "materials": ["WATER", "SAND", "DIRT"], "glyph_ids": [544, 545, 542, 543]},
    {"zone_id": "grass_flowers",        "materials": ["GRASS"],                  "glyph_ids": [616, 623, 528, 529]},
    {"zone_id": "mountain_snow_strata", "materials": ["STONE", "SNOW", "DIRT"], "glyph_ids": [556, 557, 645, 521]},
    {
        "zone_id": "sphere",
        "mesh": "sphere",
        "mesh_asset": "fl4131_shape_lab_sphere.akm",
        "glyph_ids": [633, 634, 635, 636],
    },
    {
        "zone_id": "triangle_pyramid",
        "mesh": "pyramid",
        "mesh_asset": "fl4131_shape_lab_triangle_pyramid.akm",
        "glyph_ids": [641, 642, 643, 515],
    },
    {
        "zone_id": "skull_like",
        "mesh": "skull",
        "mesh_asset": "fl4131_shape_lab_skull_like.akm",
        "glyph_ids": [522, 523, 524, 525],
    },
]

VISUAL_STYLE_DEFAULT = 500
PRESENTATION_KIND_WORLD = 603
INST_VISIBLE_USE_TREE = 3


def _zone_for_global_cell(gx: float, gy: float) -> str:
    """Return the textual zone id covering a global visual-cell coordinate."""
    py = gy / VISUAL_CELLS
    if py <= BAND_SHORELINE_MAX_PY:
        return "shoreline"
    if py <= BAND_GRASS_MAX_PY:
        return "grass_flowers"
    if py <= BAND_MOUNTAIN_MAX_PY:
        return "mountain_snow_strata"
    return "mesh_plateau"


def _shoreline_mat(gx: float, gy: float) -> int:
    """Diagonal shoreline: water in the south-west, dirt in the north-east,
    sand band in between. Produces real material boundaries plus a curved
    coastline (not a straight band) by adding a low-frequency cosine wobble.
    """
    import math
    diag = gx + gy
    wobble = 4.0 * math.cos((gx + gy) * 0.18)
    if diag + wobble < -40:
        return MAT_WATER
    if diag + wobble < -10:
        return MAT_SAND
    return MAT_DIRT


def _shoreline_height(gx: float, gy: float) -> int:
    import math
    diag = gx + gy
    wobble = 4.0 * math.cos((gx + gy) * 0.18)
    if diag + wobble < -40:
        return 20  # water level (well below sandbox 57)
    if diag + wobble < -10:
        return 40  # beach slope
    return 55      # dirt apron


def _grass_mat(gx: float, gy: float) -> int:
    """Mostly grass with sparse dirt 'flower' dots to give sparse-dot fixture
    coverage for the shape lab.
    """
    if (int(gx) * 31 + int(gy) * 17) % 23 == 0:
        return MAT_DIRT
    return MAT_GRASS


def _grass_height(gx: float, gy: float) -> int:
    import math
    return int(60 + 2.0 * math.sin(gx * 0.25) + 2.0 * math.cos(gy * 0.21))


def _mountain_mat(gx: float, gy: float, py_norm: float) -> int:
    """Stone strata band with snow caps above a height threshold. py_norm runs
    from 0 at the southern edge of the band to ~1 at the northern edge.
    """
    if py_norm > 0.65:
        return MAT_SNOW
    if py_norm > 0.40 and int(gx) % 7 == 0:
        return MAT_DIRT
    return MAT_STONE


def _mountain_height(gx: float, gy: float, py_norm: float) -> int:
    import math
    base = 70 + 50.0 * py_norm
    ridge = 8.0 * math.sin(gx * 0.30)
    strata = 4.0 * ((int(gy) % 3) - 1)
    return int(base + ridge + strata)


def _plateau_mat(gx: float, gy: float) -> int:
    return MAT_GRASS


def _plateau_height(gx: float, gy: float) -> int:
    return 65


def build_patches(grid_size: int) -> list:
    half = grid_size // 2
    patches = []
    for py in range(-half, grid_size - half):
        for px in range(-half, grid_size - half):
            patch = A3DPatch(px, py)
            for vy in range(VISUAL_CELLS):
                for vx in range(VISUAL_CELLS):
                    gx = px * VISUAL_CELLS + vx
                    gy = py * VISUAL_CELLS + vy
                    zone = _zone_for_global_cell(gx, gy)
                    if zone == "shoreline":
                        patch.visual[vy][vx] = _shoreline_mat(gx, gy)
                    elif zone == "grass_flowers":
                        patch.visual[vy][vx] = _grass_mat(gx, gy)
                    elif zone == "mountain_snow_strata":
                        py_span_min = BAND_GRASS_MAX_PY + 1
                        py_span_max = BAND_MOUNTAIN_MAX_PY
                        span = max(1, py_span_max - py_span_min + 1)
                        py_norm = ((gy / VISUAL_CELLS) - py_span_min) / span
                        patch.visual[vy][vx] = _mountain_mat(gx, gy, py_norm)
                    else:
                        patch.visual[vy][vx] = _plateau_mat(gx, gy)
            for hy in range(HEIGHT_CELLS + 1):
                for hx in range(HEIGHT_CELLS + 1):
                    gx = px * VISUAL_CELLS + hx * (VISUAL_CELLS / HEIGHT_CELLS)
                    gy = py * VISUAL_CELLS + hy * (VISUAL_CELLS / HEIGHT_CELLS)
                    zone = _zone_for_global_cell(gx, gy)
                    if zone == "shoreline":
                        h = _shoreline_height(gx, gy)
                    elif zone == "grass_flowers":
                        h = _grass_height(gx, gy)
                    elif zone == "mountain_snow_strata":
                        py_span_min = BAND_GRASS_MAX_PY + 1
                        py_span_max = BAND_MOUNTAIN_MAX_PY
                        span = max(1, py_span_max - py_span_min + 1)
                        py_norm = ((gy / VISUAL_CELLS) - py_span_min) / span
                        h = _mountain_height(gx, gy, py_norm)
                    else:
                        h = _plateau_height(gx, gy)
                    patch.height[hy][hx] = max(0, min(0xFFFF, int(h)))
            patches.append(patch)
    return patches


def _mesh_transform(pos: tuple[float, float, float], scale: tuple[float, float, float]) -> list[float]:
    return [
        float(scale[0]), 0.0, 0.0, 0.0,
        0.0, float(scale[1]), 0.0, 0.0,
        0.0, 0.0, float(scale[2]), 0.0,
        float(pos[0]), float(pos[1]), float(pos[2]), 1.0,
    ]


def _make_mesh_instance(zone_id: str, mesh_asset: str, pos: tuple[float, float, float], scale: tuple[float, float, float]) -> A3DInstance:
    return A3DInstance(
        mesh_name=mesh_asset,
        inst_name=f"fl4131_shape_lab_{zone_id}",
        transform=_mesh_transform(pos, scale),
        flags=INST_VISIBLE_USE_TREE,
        story_id=-1,
        variant="mesh",
    )


def build_instances() -> list[A3DInstance]:
    """Place one real AKM mesh instance per mesh-slot zone on the plateau.
    The meshes are fixture landmarks for shape-lab screenshots; glyph/profile
    ownership still lives in the sidecar.
    """
    z = float(_plateau_height(0, 60))
    instances = []
    mesh_specs = (
        (-58.0, "sphere", (30.0, 30.0, 30.0)),
        (0.0, "triangle_pyramid", (40.0, 40.0, 40.0)),
        (58.0, "skull_like", (72.0, 72.0, 72.0)),
    )
    for x, zone_id, scale in mesh_specs:
        zone = next(z for z in ZONES if z["zone_id"] == zone_id)
        instances.append(
            _make_mesh_instance(
                zone_id,
                str(zone["mesh_asset"]),
                (x, 60.0, z),
                scale,
            )
        )
    return instances


def _write_akm(path: Path, vertices: list[tuple[float, float, float, int, int, int, int]], faces: list[tuple[int, int, int]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("ply\n")
        handle.write("format ascii 1.0\n")
        handle.write("comment FL-4131 shape lab fixture mesh\n")
        handle.write(f"element vertex {len(vertices)}\n")
        handle.write("property float x\n")
        handle.write("property float y\n")
        handle.write("property float z\n")
        handle.write("property uchar red\n")
        handle.write("property uchar green\n")
        handle.write("property uchar blue\n")
        handle.write("property uchar alpha\n")
        handle.write(f"element face {len(faces)}\n")
        handle.write("property list uchar uint vertex_indices\n")
        handle.write("end_header\n")
        for x, y, z, r, g, b, a in vertices:
            handle.write(f"{x:.3f} {y:.3f} {z:.3f} {r:d} {g:d} {b:d} {a:d}\n")
        for a, b, c in faces:
            handle.write(f"3 {a:d} {b:d} {c:d}\n")


def write_lab_mesh_assets() -> list[Path]:
    """Write tiny local AKM meshes required by the fixture's mesh slots."""
    MESH_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    solid = 0
    mesh_specs = {
        "fl4131_shape_lab_sphere.akm": (
            [
                (0.0, 0.0, 1.0, 204, 204, 255, solid),
                (1.0, 0.0, 0.0, 153, 153, 255, solid),
                (0.0, 1.0, 0.0, 102, 102, 204, solid),
                (-1.0, 0.0, 0.0, 153, 153, 255, solid),
                (0.0, -1.0, 0.0, 102, 102, 204, solid),
                (0.0, 0.0, -1.0, 51, 51, 153, solid),
            ],
            [
                (0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 1),
                (5, 2, 1), (5, 3, 2), (5, 4, 3), (5, 1, 4),
            ],
        ),
        "fl4131_shape_lab_triangle_pyramid.akm": (
            [
                (-1.0, -1.0, 0.0, 204, 153, 51, solid),
                (1.0, -1.0, 0.0, 204, 153, 51, solid),
                (1.0, 1.0, 0.0, 153, 102, 51, solid),
                (-1.0, 1.0, 0.0, 153, 102, 51, solid),
                (0.0, 0.0, 1.4, 255, 204, 102, solid),
            ],
            [
                (0, 1, 2), (0, 2, 3),
                (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4),
            ],
        ),
        "fl4131_shape_lab_skull_like.akm": (
            [
                (-0.9, -0.7, -0.7, 204, 204, 204, solid),
                (0.9, -0.7, -0.7, 204, 204, 204, solid),
                (0.9, 0.7, -0.7, 153, 153, 153, solid),
                (-0.9, 0.7, -0.7, 153, 153, 153, solid),
                (-0.9, -0.7, 0.7, 255, 255, 255, solid),
                (0.9, -0.7, 0.7, 255, 255, 255, solid),
                (0.9, 0.7, 0.7, 204, 204, 204, solid),
                (-0.9, 0.7, 0.7, 204, 204, 204, solid),
                (-0.35, -0.95, -1.1, 153, 153, 153, solid),
                (0.35, -0.95, -1.1, 153, 153, 153, solid),
                (0.35, -0.25, -1.1, 204, 204, 204, solid),
                (-0.35, -0.25, -1.1, 204, 204, 204, solid),
            ],
            [
                (0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6),
                (0, 4, 5), (0, 5, 1), (1, 5, 6), (1, 6, 2),
                (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0),
                (8, 9, 10), (8, 10, 11), (8, 11, 3), (8, 3, 0),
                (9, 1, 2), (9, 2, 10), (10, 2, 3), (10, 3, 11),
            ],
        ),
    }
    written = []
    for name, (vertices, faces) in mesh_specs.items():
        path = MESH_ASSET_DIR / name
        _write_akm(path, vertices, faces)
        written.append(path)
    return written


def write_a3d(output_path: Path, patches, instances, player_start) -> None:
    materials_binary = get_default_materials_binary()
    with output_path.open("wb") as f:
        header = A3DHeader(len(patches))
        header.write(f)
        for p in patches:
            p.write(f)
        f.write(materials_binary)
        f.write(struct.pack("<i", WORLD_FORMAT_VERSION))
        f.write(struct.pack("<i", len(instances)))
        for inst in instances:
            inst.write(f)
        f.write(struct.pack("<i", 1))  # has_player_start (v4+)
        player_start.write(f)
        f.write(struct.pack("<i", 0))  # enemy generator count (none for the lab)
        f.write(struct.pack("<i", 0))  # minimap marker count


CANONICAL_GLYPH_MANIFEST_PATH = "assets/glyphs/fixtures/extended_glyph_material_additive_v1.json"


def compute_canonical_manifest_hash() -> str:
    """RFC8785-canonical sha256 of the admitted manifest content.

    The engine's material_sidecar parser requires `glyph_manifest_hash` to
    equal this value when it later validates the manifest file. Generating
    the hash from the file at runtime keeps the fixture in lockstep with
    whatever manifest is currently committed; we never hardcode a stale hex.
    """
    import sys as _sys
    scripts_dir = REPO_ROOT / "scripts"
    if str(scripts_dir) not in _sys.path:
        _sys.path.insert(0, str(scripts_dir))
    from compile_glyph_manifest import sha256_manifest  # type: ignore
    manifest = json.loads((REPO_ROOT / CANONICAL_GLYPH_MANIFEST_PATH).read_text())
    return sha256_manifest(manifest)


def _build_material_entries() -> list:
    """Map the zone GlyphId sets onto the 4×16 (elev,shade) material grid.

    Each entry distributes its zone's curated GlyphIds across one elev row so
    the engine loader sees a non-empty plane per material. The proof script
    repaints these immediately via FL4131_HARRI_PAINT_MATERIAL; the seed only
    has to pass material_sidecar_parse + material_sidecar_validate.
    """
    # mat_id -> ordered GlyphId seeds drawn from zones above.
    seed_by_mat = {
        MAT_WATER: [544, 545, 542, 543, 528, 529],
        MAT_GRASS: [616, 623, 528, 529, 522, 523, 524, 525],
        MAT_DIRT:  [542, 543, 645, 521, 633, 634, 635, 636],
        MAT_STONE: [556, 557, 645, 521, 641, 642, 643, 515],
        MAT_SAND:  [544, 545, 528, 529, 522, 523],
        MAT_SNOW:  [556, 557, 645, 521, 633, 634],
    }
    entries = []
    for mat_id in sorted(seed_by_mat):
        cells = []
        for shade, gid in enumerate(seed_by_mat[mat_id]):
            cells.append({"elev": 0, "shade": shade, "glyph_id": gid})
        entries.append({"material_id": mat_id, "cells": cells})
    return entries


def build_sidecar() -> dict:
    return {
        # ── engine/material_sidecar.cpp required fields ────────────────────
        "sidecar_version": 1,
        "profile_kind": "extended_material_glyph_v1",
        "content_pack_id": "material.additive.v1",
        "glyph_manifest_hash": compute_canonical_manifest_hash(),
        "glyph_manifest_path": CANONICAL_GLYPH_MANIFEST_PATH,
        "material_entries": _build_material_entries(),
        # ── FL-4131 authoring annotations (ignored by the engine loader) ───
        "_authoring": {
            "schema": "fl4131_shape_lab_glyph_profile_v0",
            "fixture": "assets/a3d/fl4131_shape_lab_20x20.a3d",
            "purpose": (
                "FL-4131 authoring/proof fixture. Compares the normal CP437 "
                "default/origin baseline, the current generated checkpoint "
                "baseline, and shape6-scored extended GlyphId candidates on "
                "an isolated 20x20 map. CP437 remains the default renderer "
                "path; extended GlyphIds remain opt-in candidate/comparison "
                "data."
            ),
            "admitted_glyph_id_min": 512,
            "admitted_glyph_id_max": 647,
            "zones": ZONES,
        },
        "zones": ZONES,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", default="assets/a3d/fl4131_shape_lab_20x20.a3d")
    ap.add_argument("--sidecar", default="assets/a3d/fl4131_shape_lab_20x20.a3d.glyph_profile.json")
    ap.add_argument("--grid", type=int, default=DEFAULT_GRID)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    out = Path(args.output)
    if not out.is_absolute():
        out = REPO_ROOT / out
    sidecar_path = Path(args.sidecar)
    if not sidecar_path.is_absolute():
        sidecar_path = REPO_ROOT / sidecar_path
    if (out.exists() or sidecar_path.exists()) and not args.force:
        print(
            f"ERROR: {out} or {sidecar_path} exists. Pass --force to overwrite.",
            file=sys.stderr,
        )
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)

    print("=== FL-4131 Shape Lab Fixture Generator ===")
    print(f"  output:           {out}")
    print(f"  sidecar:          {sidecar_path}")
    print(f"  grid:             {args.grid}x{args.grid} patches ({args.grid*VISUAL_CELLS}x{args.grid*VISUAL_CELLS} cells)")
    print(f"  format_version:   {WORLD_FORMAT_VERSION}")

    mesh_paths = write_lab_mesh_assets()
    patches = build_patches(args.grid)
    instances = build_instances()
    player_start = A3DPlayerStart(
        pos=[-70.0, -70.0, 55.5],
        yaw=0.0,
        dir=0.0,
    )
    write_a3d(out, patches, instances, player_start)

    sidecar = build_sidecar()
    sidecar_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    fixture_size = out.stat().st_size
    print(f"  patches:          {len(patches)}")
    print(f"  instances:        {len(instances)}")
    print(f"  player_start:     {player_start.pos}")
    print(f"  zones:            {[z['zone_id'] for z in ZONES]}")
    print(f"  mesh_assets:      {[p.name for p in mesh_paths]}")
    print(f"Wrote {out} ({fixture_size} bytes)")
    print(f"Wrote {sidecar_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
