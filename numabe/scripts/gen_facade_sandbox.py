#!/usr/bin/env python3
"""Generate a flat A3D street of PS1-style facades and open it in native Classic.

WHY THIS EXISTS
---------------
Every existing route from "a facade mesh" to "pixels in the native Classic
runtime" ran through ``scripts/sbu_e2e_run.py::_run_direct_world_ir_backend``,
which inlines ~26 reconstruction stages (OSM ingest, PLATEAU, JEV, rules,
representation, population, dispatch, lowering, stamping, four verifiers) into
one function with no partial exit.  A facade could therefore only be looked at
after a full source-backed world reconstruction succeeded end to end.

Nothing about looking at a facade needs any of that.  This generator uses the
SAME authored geometry owners the pipeline uses -- it adds no new facade art:

    scripts/pipeline/compile_facade_shell.py :: build_shell   (PS1 bay facades)
    scripts/pipeline/compile_facade_shell.py :: write_akm     (Classic ASCII PLY)
    addons/io_asciicker/scene/a3d_format.py  :: A3DInstance   (mesh placement)

and places them on a flat sandbox plate, so the authored-geometry lane can be
seen and iterated without the reconstruction lane being involved at all.

FRONT DOOR
----------
``scripts/launch_world_ir_classic.py`` requires ``--receipt`` carrying
``map_path`` and ``mesh_root``.  Rather than weaken that guard, this script
WRITES a conforming receipt, so the existing front door opens the sandbox
unmodified.  The receipt is explicitly marked as a sandbox with
``final_acceptance: false`` -- it is an authoring surface, never map acceptance
evidence, and must never be cited as reconstruction proof.

USAGE
-----
    python3 scripts/gen_facade_sandbox.py                      # build only
    python3 scripts/gen_facade_sandbox.py --launch             # build + open Classic
    python3 scripts/gen_facade_sandbox.py --profile konbini --profile tower
    python3 scripts/gen_facade_sandbox.py --list               # show profiles
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import struct
import subprocess
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
ADDONS_ROOT = REPO_ROOT / "addons"
if str(ADDONS_ROOT) not in sys.path:
    sys.path.insert(0, str(ADDONS_ROOT))


def _load(name: str, rel_path: str):
    """Load a module by path, mirroring scripts/gen_sandbox_20x20.py."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


a3d_format = _load("a3d_format", "addons/io_asciicker/scene/a3d_format.py")
path_utils = _load("io_asciicker.path_utils", "addons/io_asciicker/path_utils.py")
_pkg = types.ModuleType("io_asciicker")
_pkg.path_utils = path_utils
sys.modules.setdefault("io_asciicker", _pkg)
sys.modules["io_asciicker.path_utils"] = path_utils
default_materials = _load("default_materials",
                          "addons/io_asciicker/scene/default_materials.py")

from scripts.pipeline.compile_facade_shell import (  # noqa: E402
    HEIGHT_SCALE,
    build_shell,
    write_akm,
)
from scripts.pipeline import geo_frames as GF  # noqa: E402
from scripts.pipeline.classic_street_dressing import dress_frontage  # noqa: E402
from scripts.pipeline.classic_procedural_mesh import (  # noqa: E402
    ARCHETYPE_PHYSICAL,
    _build_archetype,
)

A3DHeader = a3d_format.A3DHeader
A3DPatch = a3d_format.A3DPatch
A3DInstance = a3d_format.A3DInstance
A3DPlayerStart = a3d_format.A3DPlayerStart
WORLD_FORMAT_VERSION = a3d_format.WORLD_FORMAT_VERSION

# Flat plate height, matching scripts/gen_sandbox_20x20.py so the two sandboxes
# read at the same datum and a player start clears the ground by 0.5.
SANDBOX_HEIGHT = 57
SANDBOX_MATERIAL_ID = 0
CONTENT_SCALE = GF.WORLD_UNITS_PER_METRE  # 5.132 wu/m = sprite 3.4213 x OPERATOR_MAP_SCALE 1.5

# ---------------------------------------------------------------------------
# PS1 facade profiles.
#
# These are the same keys build_shell()'s discrete-bay branch reads
# (compile_facade_shell.py:569-601): wall/glass/trim/roof rgb, storey_m,
# bay_width_m, window_fraction.  Colours stay on the terminal-safe 6x6x6 cube
# levels {0,51,102,153,204,255} so Classic needs no dithering.
# ---------------------------------------------------------------------------
SAFE = (0, 51, 102, 153, 204, 255)

PROFILES: dict[str, dict] = {
    "konbini": {
        "label": "convenience store, wide glazing, single storey",
        "width_m": 12.0, "depth_m": 9.0, "roof_m": 4.2,
        "wall_rgb": (204, 204, 204), "glass_rgb": (153, 204, 204),
        "trim_rgb": (51, 102, 153), "roof_rgb": (153, 153, 153),
        "storey_m": 4.2, "bay_width_m": 2.4, "window_fraction": 0.82,
        "dressing": {"awning","blade","roof"},
        "awning_rgb": (204, 51, 51), "fascia_rgb": (255, 255, 255),
        "sign_rgb": (204, 51, 51), "shutter_rgb": (153, 153, 153),
        "shopfront_head_m": 3.4,
    },
    "pharmacy": {
        "label": "two-storey drugstore, bright sign band",
        "width_m": 10.0, "depth_m": 8.0, "roof_m": 7.6,
        "wall_rgb": (255, 255, 204), "glass_rgb": (102, 153, 153),
        "trim_rgb": (204, 51, 51), "roof_rgb": (153, 153, 102),
        "storey_m": 3.8, "bay_width_m": 2.2, "window_fraction": 0.74,
        "dressing": {"awning","sign_tower","blade","roof"},
        "awning_rgb": (204, 153, 0), "fascia_rgb": (255, 255, 255),
        "sign_rgb": (204, 51, 51), "shutter_rgb": (153, 153, 153),
        "shopfront_head_m": 3.3,
    },
    "shophouse": {
        "label": "narrow mixed-use shophouse, tight bays",
        "width_m": 6.5, "depth_m": 10.0, "roof_m": 9.0,
        "wall_rgb": (204, 153, 102), "glass_rgb": (102, 102, 102),
        "trim_rgb": (102, 51, 51), "roof_rgb": (102, 102, 51),
        "storey_m": 3.0, "bay_width_m": 1.8, "window_fraction": 0.62,
        "dressing": {"awning","sign_tower","shutter","roof"},
        "awning_rgb": (102, 51, 51), "fascia_rgb": (255, 204, 102),
        "sign_rgb": (204, 102, 51), "shutter_rgb": (102, 102, 102),
        "shopfront_head_m": 3.0,
    },
    "apartment": {
        "label": "five-storey residential block, regular grid",
        "width_m": 16.0, "depth_m": 11.0, "roof_m": 15.0,
        "wall_rgb": (204, 204, 153), "glass_rgb": (102, 102, 153),
        "trim_rgb": (153, 102, 51), "roof_rgb": (102, 102, 102),
        "storey_m": 3.0, "bay_width_m": 2.6, "window_fraction": 0.55,
        "dressing": {"roof"},
        "awning_rgb": (153, 102, 51), "fascia_rgb": (255, 255, 255),
        "sign_rgb": (153, 102, 51), "shutter_rgb": (153, 153, 153),
        "shopfront_head_m": 3.0,
    },
    "office": {
        "label": "curtain-wall office, wide flush bays",
        "width_m": 18.0, "depth_m": 14.0, "roof_m": 21.0,
        "wall_rgb": (153, 153, 153), "glass_rgb": (51, 102, 153),
        "trim_rgb": (51, 51, 51), "roof_rgb": (102, 102, 102),
        "storey_m": 3.5, "bay_width_m": 3.0, "window_fraction": 0.86,
        "dressing": {"sign_tower","roof"},
        "awning_rgb": (51, 51, 51), "fascia_rgb": (204, 204, 204),
        "sign_rgb": (51, 102, 153), "shutter_rgb": (102, 102, 102),
        "shopfront_head_m": 3.6,
    },
    "tower": {
        "label": "slim tower, tall narrow bays",
        "width_m": 13.0, "depth_m": 13.0, "roof_m": 33.0,
        "wall_rgb": (153, 153, 204), "glass_rgb": (51, 51, 102),
        "trim_rgb": (102, 102, 153), "roof_rgb": (51, 51, 51),
        "storey_m": 3.3, "bay_width_m": 2.0, "window_fraction": 0.70,
        "dressing": {"sign_tower","blade","roof"},
        "awning_rgb": (51, 51, 102), "fascia_rgb": (204, 204, 255),
        "sign_rgb": (204, 51, 102), "shutter_rgb": (102, 102, 153),
        "shopfront_head_m": 3.4,
    },
    "warehouse": {
        "label": "low industrial shed, sparse openings",
        "width_m": 20.0, "depth_m": 15.0, "roof_m": 6.5,
        "wall_rgb": (153, 153, 102), "glass_rgb": (102, 153, 153),
        "trim_rgb": (102, 51, 0), "roof_rgb": (102, 102, 102),
        "storey_m": 6.5, "bay_width_m": 4.0, "window_fraction": 0.35,
        "dressing": {"shutter","roof"},
        "awning_rgb": (102, 102, 51), "fascia_rgb": (204, 204, 153),
        "sign_rgb": (102, 102, 51), "shutter_rgb": (102, 102, 102),
        "shopfront_head_m": 4.0,
    },
    "station": {
        "label": "station frontage, long colonnade of bays",
        "width_m": 26.0, "depth_m": 12.0, "roof_m": 11.0,
        "wall_rgb": (204, 204, 204), "glass_rgb": (102, 153, 204),
        "trim_rgb": (153, 51, 51), "roof_rgb": (153, 153, 153),
        "storey_m": 5.5, "bay_width_m": 2.8, "window_fraction": 0.78,
        "dressing": {"awning","blade","roof"},
        "awning_rgb": (153, 51, 51), "fascia_rgb": (255, 255, 255),
        "sign_rgb": (153, 51, 51), "shutter_rgb": (153, 153, 153),
        "shopfront_head_m": 4.2,
    },
}

DEFAULT_STREET = [
    "konbini", "pharmacy", "shophouse", "apartment",
    "office", "tower", "warehouse", "station",
]


def _validate_profiles() -> None:
    """Fail closed on any colour off the terminal-safe cube."""
    for name, spec in PROFILES.items():
        for key in ("wall_rgb", "glass_rgb", "trim_rgb", "roof_rgb"):
            for channel in spec[key]:
                if channel not in SAFE:
                    raise ValueError(
                        f"profile {name}.{key} channel {channel} is off the "
                        f"terminal-safe cube {SAFE}; Classic would dither it")


def build_patches(grid_size: int, height: int, mat_id: int) -> list:
    """Flat plate of grid_size x grid_size patches, as gen_sandbox_20x20 does."""
    half = grid_size // 2
    patches = []
    for py in range(-half, grid_size - half):
        for px in range(-half, grid_size - half):
            patch = A3DPatch(px, py)
            for hy in range(len(patch.height)):
                for hx in range(len(patch.height[hy])):
                    patch.height[hy][hx] = height
            for vy in range(len(patch.visual)):
                for vx in range(len(patch.visual[vy])):
                    patch.visual[vy][vx] = mat_id
            patches.append(patch)
    return patches


def _rect_world(cx: float, cy: float, width_m: float, depth_m: float,
                flip: bool) -> list[tuple[float, float]]:
    """Axis-aligned footprint ring in WORLD UNITS, centred on (cx, cy).

    build_shell() consumes world units and divides edge length by content_scale
    to recover metres for bay counting (compile_facade_shell.py:596).  Winding
    is reversed for the far side of the street so both rows present their
    entrance edge toward the walkway.
    """
    hw = width_m * CONTENT_SCALE / 2.0
    hd = depth_m * CONTENT_SCALE / 2.0
    ring = [(cx - hw, cy - hd), (cx + hw, cy - hd),
            (cx + hw, cy + hd), (cx - hw, cy + hd)]
    return list(reversed(ring)) if flip else ring


def build_street(names: list[str], mesh_dir: Path, street_half_width_m: float,
                 gap_m: float, lineup: bool = False) -> tuple[list, list[dict]]:
    """Author one AKM per facade and return (instances, manifest rows).

    Facades alternate between the two sides of a street running along +X, so
    the player spawns at one end and walks between two built frontages.
    """
    mesh_dir.mkdir(parents=True, exist_ok=True)
    instances: list = []
    manifest: list[dict] = []

    cursor = {False: 0.0, True: 0.0}  # near side, far side running X in metres
    for index, name in enumerate(names):
        spec = PROFILES[name]
        # build_shell puts the entrance bay on edge 0, the -Y face, so a
        # building must sit at +Y of the viewer for its shopfront to face them.
        # Lineup therefore uses one row at +Y; a street keeps two sides and the
        # near row shows its +Y wall instead.
        far = True if lineup else bool(index % 2)
        # Advance this side's cursor by half of this building plus the gap.
        cursor[far] += spec["width_m"] / 2.0
        x_m = cursor[far]
        cursor[far] += spec["width_m"] / 2.0 + gap_m

        y_m = street_half_width_m + spec["depth_m"] / 2.0
        if not far:
            y_m = -y_m

        cx = x_m * CONTENT_SCALE
        cy = y_m * CONTENT_SCALE
        poly = _rect_world(cx, cy, spec["width_m"], spec["depth_m"],
                           flip=False if lineup else far)

        profile = {
            "wall_rgb": spec["wall_rgb"], "glass_rgb": spec["glass_rgb"],
            "trim_rgb": spec["trim_rgb"], "roof_rgb": spec["roof_rgb"],
            "storey_m": spec["storey_m"], "bay_width_m": spec["bay_width_m"],
            "window_fraction": spec["window_fraction"],
        }
        built = build_shell(
            poly, spec["roof_m"], spec["wall_rgb"], spec["glass_rgb"],
            spec["roof_rgb"], CONTENT_SCALE,
            storey_m=spec["storey_m"], profile=profile,
        )
        if built is None:
            raise ValueError(f"build_shell returned no geometry for {name}")
        verts, faces, (ox, oy) = built
        shell_faces = len(faces)

        # Frontage the street sees, in the shell's own local metre frame.
        if lineup or far:
            front_y_m, out_sign = -spec["depth_m"] / 2.0, -1.0
        else:
            front_y_m, out_sign = spec["depth_m"] / 2.0, 1.0
        dress_frontage(verts, faces, spec=spec,
                       width_m=spec["width_m"], depth_m=spec["depth_m"],
                       roof_m=spec["roof_m"], front_y_m=front_y_m,
                       out_sign=out_sign, scale=CONTENT_SCALE)
        dressing_faces = len(faces) - shell_faces

        mesh_name = f"facade_{index:02d}_{name}.akm"
        write_akm(mesh_dir / mesh_name, verts, faces,
                  comment=f"gen_facade_sandbox {name} - presentation only, "
                          f"alpha=255 => no collision")

        # Same convention as compile_facade_shell.py:729-734: identity in XY,
        # HEIGHT_SCALE on row2, translation carrying the terrain datum.
        transform = [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, HEIGHT_SCALE, 0.0,
            float(ox), float(oy), float(SANDBOX_HEIGHT), 1.0,
        ]
        instances.append(A3DInstance(
            mesh_name=mesh_name, inst_name=f"sandbox_{index:02d}_{name}",
            transform=transform, flags=3, story_id=-1,
        ))
        manifest.append({
            "index": index, "profile": name, "label": spec["label"],
            "mesh_name": mesh_name, "side": "far" if far else "near",
            "centre_m": [round(x_m, 3), round(y_m, 3)],
            "footprint_m": [spec["width_m"], spec["depth_m"]],
            "roof_m": spec["roof_m"], "storeys": max(1, round(
                spec["roof_m"] / spec["storey_m"])),
            "vertices": len(verts), "faces": len(faces),
            "dressing": sorted(spec.get("dressing", ())),
            "dressing_faces": dressing_faces,
        })
    return instances, manifest



# Street clutter drawn from the archetypes the pipeline already declares in
# classic_procedural_mesh.ARCHETYPE_PHYSICAL.  The reference street is dense
# with these; nothing here authors a new prop, it only places existing ones.
STREET_CLUTTER = [
    "vending_machine", "utility_pole", "bicycle_parking", "bench",
    "vending_machine", "street_lamp", "post_box", "waste_basket",
    "vending_machine", "utility_pole", "information_board", "bench",
    "traffic_signal", "street_lamp", "vending_machine", "bicycle_parking",
]


def build_fixtures(mesh_dir: Path, x_from_m: float, x_to_m: float,
                   kerb_y_m: float) -> tuple[list, list[dict]]:
    """Place existing archetypes along the kerb line, one AKM per archetype.

    One mesh is authored per distinct archetype and then instanced repeatedly,
    which is how the reference kit ships too: a small prop library, reused.
    """
    mesh_dir.mkdir(parents=True, exist_ok=True)
    instances: list = []
    manifest: list[dict] = []
    authored: dict[str, str] = {}

    count = len(STREET_CLUTTER)
    span = max(1e-6, x_to_m - x_from_m)
    for index, key in enumerate(STREET_CLUTTER):
        if key not in ARCHETYPE_PHYSICAL:
            raise KeyError(f"unknown archetype {key}")
        subtype = ARCHETYPE_PHYSICAL[key][0]
        if key not in authored:
            verts, faces = _build_archetype(subtype, CONTENT_SCALE)
            mesh_name = f"fixture_{key}.akm"
            write_akm(mesh_dir / mesh_name, verts, faces,
                      comment=f"gen_facade_sandbox archetype {subtype} - "
                              f"presentation only, alpha=255 => no collision")
            authored[key] = mesh_name
            manifest.append({"archetype": key, "subtype": subtype,
                             "mesh_name": mesh_name,
                             "vertices": len(verts), "faces": len(faces)})
        # Stagger alternate props a little off the kerb so the row is not a
        # perfectly straight line of identical spacing.
        t = (index + 0.5) / count
        x_m = x_from_m + span * t
        y_m = kerb_y_m - (0.0 if index % 2 else 0.85)
        instances.append(A3DInstance(
            mesh_name=authored[key],
            inst_name=f"clutter_{index:02d}_{key}",
            transform=[
                1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, HEIGHT_SCALE, 0.0,
                x_m * CONTENT_SCALE, y_m * CONTENT_SCALE,
                float(SANDBOX_HEIGHT), 1.0,
            ],
            flags=3, story_id=-1,
        ))
    return instances, manifest


def write_a3d(output_path: Path, patches: list, instances: list,
              player_start) -> None:
    """Serialize the sandbox, mirroring gen_sandbox_20x20.py::write_a3d."""
    materials_binary = default_materials.get_default_materials_binary()
    with open(output_path, "wb") as f:
        header = A3DHeader(len(patches))
        header.write(f)
        for patch in patches:
            patch.write(f)
        f.write(materials_binary)
        f.write(struct.pack("<i", WORLD_FORMAT_VERSION))
        f.write(struct.pack("<i", len(instances)))
        for inst in instances:
            inst.write(f)
        f.write(struct.pack("<i", 1))  # has_player_start (v4+)
        player_start.write(f)
        f.write(struct.pack("<i", 0))  # enemy_gen_count
        f.write(struct.pack("<i", 0))  # marker_count


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path(".run/facade_sandbox"),
                    help="output directory (default .run/facade_sandbox)")
    ap.add_argument("--profile", action="append", dest="profiles",
                    choices=sorted(PROFILES), metavar="NAME",
                    help="facade profile to place; repeatable, ordered")
    ap.add_argument("--grid", type=int, default=20,
                    help="sandbox plate size in patches (default 20)")
    ap.add_argument("--street-half-width", type=float, default=3.0,
                    help="metres from centreline to each frontage (default 3). "
                         "Classic's isometric frame covers roughly 40 world "
                         "units, so a wide street pushes both frontages out of "
                         "shot; keep this tight or nothing reads.")
    ap.add_argument("--gap", type=float, default=1.5,
                    help="metres between neighbours on a side (default 1.5)")
    ap.add_argument("--no-clutter", action="store_true",
                    help="skip street furniture; facades only")
    ap.add_argument("--lineup", action="store_true",
                    help="place every facade in ONE row and stand the viewer "
                         "back from it, instead of a two-sided street. Use this "
                         "to see whole facades; a street frames you between "
                         "walls and Classic's camera cannot back out of it.")
    ap.add_argument("--spawn-y", type=float, default=None,
                    help="metres off the centreline to spawn at "
                         "(default: -34 in lineup mode, 0 in street mode)")
    ap.add_argument("--spawn-x", type=float, default=None,
                    help="metres along the street to spawn at "
                         "(default: in front of the second facade pair)")
    ap.add_argument("--launch", action="store_true",
                    help="open the sandbox in native Classic after writing")
    ap.add_argument("--capture-render", action="store_true",
                    help="with --launch, capture one resolved Classic frame")
    ap.add_argument("--list", action="store_true",
                    help="list available facade profiles and exit")
    args = ap.parse_args(argv)

    if args.list:
        print("Available PS1 facade profiles:\n")
        for name in sorted(PROFILES):
            spec = PROFILES[name]
            print(f"  {name:<11} {spec['label']}")
            print(f"  {'':<11} {spec['width_m']}x{spec['depth_m']} m footprint, "
                  f"{spec['roof_m']} m tall, {spec['bay_width_m']} m bays")
        return 0

    _validate_profiles()
    names = args.profiles or DEFAULT_STREET

    out_dir = args.out if args.out.is_absolute() else REPO_ROOT / args.out
    mesh_dir = out_dir / "meshes"
    out_dir.mkdir(parents=True, exist_ok=True)
    map_path = out_dir / "facade_sandbox.a3d"

    print("=== PS1 Facade Sandbox ===")
    print(f"  output:        {out_dir}")
    print(f"  facades:       {len(names)} ({', '.join(names)})")
    print(f"  content scale: {CONTENT_SCALE:.6f} world units / metre")
    print(f"  geometry:      compile_facade_shell.build_shell (authored owner)")
    print()

    instances, manifest = build_street(
        names, mesh_dir, args.street_half_width, args.gap, lineup=args.lineup)

    # Centre the row on the origin. build_street lays out from x=0 upward, but
    # the terrain plate is centred on the origin, so an uncentred row runs off
    # the plate and anything on bare space falls to the water datum.
    xs = [row["centre_m"][0] for row in manifest]
    shift_world = ((min(xs) + max(xs)) / 2.0) * CONTENT_SCALE
    for inst, row in zip(instances, manifest):
        inst.transform[12] -= shift_world
        row["centre_m"][0] -= shift_world / CONTENT_SCALE

    # Spawn on the centreline between the frontages. Classic's isometric frame
    # covers only a modest diamond around the player, so spawning behind the
    # street puts every facade out of shot; default to standing beside the
    # second pair with built frontage on both sides.
    # Street clutter along the kerb in front of the row.
    fixture_instances: list = []
    fixture_manifest: list[dict] = []
    if not args.no_clutter:
        row_xs = [row["centre_m"][0] for row in manifest]
        row_w = max(row["footprint_m"][0] for row in manifest)
        kerb_y = (min(row["centre_m"][1] - row["footprint_m"][1] / 2.0
                      for row in manifest) - 1.4)
        fixture_instances, fixture_manifest = build_fixtures(
            mesh_dir, min(row_xs) - row_w * 0.3, max(row_xs) + row_w * 0.3,
            kerb_y)
        instances = instances + fixture_instances

    # Plate must cover every facade footprint plus the viewer standing back.
    # A patch spans 8 world units and build_patches centres the grid, so the
    # half-extent is grid*4 world units (scripts/gen_sandbox_20x20.py).
    half_needed_w = max(
        max(abs(row["centre_m"][0]) + row["footprint_m"][0]
            for row in manifest),
        max(abs(row["centre_m"][1]) + row["footprint_m"][1]
            for row in manifest),
        abs(-34.0 if args.lineup else 0.0) + 8.0,
    ) * CONTENT_SCALE
    grid_needed = int(half_needed_w / 4.0) * 2 + 8
    grid = max(args.grid, grid_needed)
    if grid > args.grid:
        print(f"  plate:     auto-sized to {grid}x{grid} patches "
              f"(+-{grid*4} world units) to cover the layout")
    patches = build_patches(grid, SANDBOX_HEIGHT, SANDBOX_MATERIAL_ID)

    xs = [row["centre_m"][0] for row in manifest]
    if args.spawn_x is not None:
        spawn_x_m = args.spawn_x
    elif args.lineup:
        spawn_x_m = (min(xs) + max(xs)) / 2.0
    elif len(manifest) >= 3:
        spawn_x_m = manifest[2]["centre_m"][0]
    else:
        spawn_x_m = manifest[0]["centre_m"][0]
    if args.spawn_y is not None:
        spawn_y_m = args.spawn_y
    else:
        spawn_y_m = -34.0 if args.lineup else 0.0
    player_start = A3DPlayerStart(
        pos=[spawn_x_m * CONTENT_SCALE, spawn_y_m * CONTENT_SCALE,
             float(SANDBOX_HEIGHT) + 0.5],
        yaw=0.0, dir=0.0)
    write_a3d(map_path, patches, instances, player_start)

    # A receipt conforming to scripts/launch_world_ir_classic.py so the
    # existing Classic front door opens this without being weakened.
    receipt_path = out_dir / "sandbox_receipt.json"
    receipt = {
        "stage": "facade_authoring_sandbox",
        "final_acceptance": False,
        "runtime_product": "classic_cp437",
        "map_path": map_path.name,
        "mesh_root": "meshes",
        "reason": ("PS1 facade authoring surface; flat plate, no source data, "
                   "no reconstruction. Never valid as map acceptance evidence."),
        "facades": manifest,
        "street_clutter": fixture_manifest,
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n",
                            encoding="utf-8")

    total_v = sum(row["vertices"] for row in manifest)
    total_f = sum(row["faces"] for row in manifest)
    for row in manifest:
        print(f"  [{row['index']:02d}] {row['profile']:<11} {row['side']:<4} "
              f"x={row['centre_m'][0]:>7.2f}m y={row['centre_m'][1]:>6.2f}m  "
              f"{row['storeys']}F  {row['vertices']:>5}v {row['faces']:>5}f")
    print()
    print(f"  patches:   {len(patches)} ({grid}x{grid}, +-{grid*4} world units)")
    print(f"  facades:   {len(manifest)}  ({total_v} verts, {total_f} faces)")
    print(f"  clutter:   {len(fixture_instances)} instances from "
          f"{len(fixture_manifest)} archetypes")
    print(f"  instances: {len(instances)} total")
    print(f"  map:       {map_path}  ({map_path.stat().st_size} bytes)")
    print(f"  meshes:    {mesh_dir}")
    print(f"  receipt:   {receipt_path}")
    print(f"  spawn:     x={spawn_x_m:.2f}m y={spawn_y_m:.2f}m")
    print()

    if not args.launch:
        print("Open it in native Classic with:")
        print(f"  python3 scripts/gen_facade_sandbox.py --launch")
        print("or directly through the existing front door:")
        print(f"  python3 scripts/launch_world_ir_classic.py \\")
        print(f"      --receipt {receipt_path.relative_to(REPO_ROOT)} \\")
        print(f"      --intermediate-review")
        return 0

    game = REPO_ROOT / ".run/game_classic"
    if not game.exists():
        print("ERROR: .run/game_classic is not built. Run: make game-classic",
              file=sys.stderr)
        return 1

    cmd = [sys.executable, "scripts/launch_world_ir_classic.py",
           "--receipt", str(receipt_path), "--intermediate-review"]
    if args.capture_render:
        cmd.append("--capture-render")
    print(f"Launching native Classic: {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=REPO_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
