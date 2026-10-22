#!/usr/bin/env python3
"""Lay out EVERY authored Classic asset on one flat plate, labelled and walkable.

WHY THIS EXISTS
---------------
``scripts/gen_facade_sandbox.py`` showed 8 facades and 9 of the fixture
archetypes.  That is not an inspection surface -- it is a sample.  The
repository actually declares:

    42 point archetypes   classic_procedural_mesh.ARCHETYPE_PHYSICAL
    25 linear modules     population_representation_contract.LINEAR_FIXED_EXTENT_M

including every road sign, fence, hedge, plant, drain, railing and signboard
the operator asked after.  None of them had a surface that shows all of them at
once, so "what assets exist?" could only be answered by reading source.

This script answers it by construction: one instance of every archetype and one
run of every linear module, on a flat plate, in labelled rows.  Nothing here
authors new art.  Every mesh comes from the existing owners:

    classic_procedural_mesh._build_archetype      point archetypes
    lower_plan_linear.build_unit_mesh             linear module runs
    lower_plan_linear.build_point_mesh            point-kind modules

CROSS-SECTION IS REAL, NOT INVENTED
-----------------------------------
Linear modules need a rule-owned cross-section (curb height, carriageway and
sidewalk widths, tactile paving, gutter, rail fence height).  This script reads
it through ``population_representation_contract.linear_asset_contract`` from a
real ``rules_resolved.json``, so the catalogue shows the dimensions the pipeline
would actually emit.  It never substitutes a made-up number; with no rules file
it fails closed and says so.

USAGE
-----
    python3 scripts/gen_asset_catalog.py                 # build
    python3 scripts/gen_asset_catalog.py --editor        # build + open ASCIIID
    python3 scripts/gen_asset_catalog.py --list          # inventory, no build
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
for _p in (REPO_ROOT, REPO_ROOT / "addons"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


a3d_format = _load("a3d_format", "addons/io_asciicker/scene/a3d_format.py")
path_utils = _load("io_asciicker.path_utils", "addons/io_asciicker/path_utils.py")
_pkg = types.ModuleType("io_asciicker")
_pkg.path_utils = path_utils
sys.modules.setdefault("io_asciicker", _pkg)
sys.modules["io_asciicker.path_utils"] = path_utils
default_materials = _load("default_materials",
                          "addons/io_asciicker/scene/default_materials.py")

from scripts.pipeline.compile_facade_shell import HEIGHT_SCALE, write_akm  # noqa: E402
from scripts.pipeline import geo_frames as GF  # noqa: E402
from scripts.pipeline.classic_procedural_mesh import (  # noqa: E402
    ARCHETYPE_PHYSICAL,
    _build_archetype,
)
from scripts.pipeline.population_representation_contract import (  # noqa: E402
    LINEAR_FIXED_EXTENT_M,
    linear_asset_contract,
)
from scripts.pipeline.lower_plan_linear import (  # noqa: E402
    build_point_mesh,
    build_unit_mesh,
)

A3DHeader = a3d_format.A3DHeader
A3DPatch = a3d_format.A3DPatch
A3DInstance = a3d_format.A3DInstance
A3DPlayerStart = a3d_format.A3DPlayerStart
WORLD_FORMAT_VERSION = a3d_format.WORLD_FORMAT_VERSION

SANDBOX_HEIGHT = 57
SANDBOX_MATERIAL_ID = 0
CONTENT_SCALE = GF.WORLD_UNITS_PER_METRE

# Rule-owned cross-section is read from a real resolved-rules file. This is the
# newest build's rules; it is evidence, not a default baked into this script.
DEFAULT_RULES = (REPO_ROOT / ".run/numabe_connected/build_final14"
                 / "plan/rules_resolved.json")

# Row pitch in metres. Archetypes are small, linear runs are long, so they get
# their own bands with generous separation: the catalogue is for reading one
# asset at a time, not for looking like a street.
ARCHETYPE_PITCH_M = 6.0
ARCHETYPE_PER_ROW = 11
ARCHETYPE_ROW_GAP_M = 7.0
LINEAR_RUN_LEN_M = 8.0
LINEAR_ROW_GAP_M = 5.0
BAND_GAP_M = 18.0


def build_patches(grid: int, height: int, mat: int) -> list:
    half = grid // 2
    out = []
    for py in range(-half, grid - half):
        for px in range(-half, grid - half):
            patch = A3DPatch(px, py)
            for hy in range(len(patch.height)):
                for hx in range(len(patch.height[hy])):
                    patch.height[hy][hx] = height
            for vy in range(len(patch.visual)):
                for vx in range(len(patch.visual[vy])):
                    patch.visual[vy][vx] = mat
            out.append(patch)
    return out


def _instance(mesh_name: str, inst_name: str, x_m: float, y_m: float):
    """Same transform law the pipeline uses (bake_osm_building_footprints:577)."""
    return A3DInstance(
        mesh_name=mesh_name, inst_name=inst_name,
        transform=[
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, HEIGHT_SCALE, 0.0,
            x_m * CONTENT_SCALE, y_m * CONTENT_SCALE,
            float(SANDBOX_HEIGHT), 1.0,
        ],
        flags=3, story_id=-1,
    )


def build_archetype_band(mesh_dir: Path, y0_m: float):
    """One instance of all 42 point archetypes, in labelled rows."""
    instances, manifest = [], []
    keys = list(ARCHETYPE_PHYSICAL)
    for index, key in enumerate(keys):
        semantic_family, w, d, h = ARCHETYPE_PHYSICAL[key][:4]
        try:
            # ARCHETYPE_PHYSICAL is keyed by the builder subtype.  The first
            # tuple member is the metric semantic family, not another builder
            # selector (for example ``traffic_sign`` maps to the
            # ``stop_sign_panel`` family).  Passing that family here made
            # valid builders look absent from the catalogue.
            verts, faces = _build_archetype(key, CONTENT_SCALE)
        except Exception as exc:
            # A key declared in ARCHETYPE_PHYSICAL with no builder in
            # _build_archetype is a real inventory hole, not a catalogue bug.
            # Report it rather than hiding it or inventing a placeholder mesh.
            manifest.append({"kind": "archetype_missing_builder", "key": key,
                             "semantic_family": semantic_family,
                             "size_m": [w, d, h],
                             "reason": f"{type(exc).__name__}: {exc}"})
            continue
        mesh_name = f"arch_{key}.akm"
        write_akm(mesh_dir / mesh_name, verts, faces,
                  comment=(f"asset catalog archetype {key} "
                          f"({semantic_family}) - "
                          f"presentation only, alpha=255 => no collision"))
        row, col = divmod(len(instances), ARCHETYPE_PER_ROW)
        x_m = (col - (ARCHETYPE_PER_ROW - 1) / 2.0) * ARCHETYPE_PITCH_M
        y_m = y0_m + row * ARCHETYPE_ROW_GAP_M
        instances.append(_instance(mesh_name, f"arch_{index:02d}_{key}",
                                   x_m, y_m))
        manifest.append({
            "kind": "archetype", "key": key,
            "semantic_family": semantic_family,
            "mesh_name": mesh_name, "size_m": [w, d, h],
            "centre_m": [round(x_m, 2), round(y_m, 2)],
            "vertices": len(verts), "faces": len(faces),
        })
    rows = (len(instances) + ARCHETYPE_PER_ROW - 1) // ARCHETYPE_PER_ROW
    return instances, manifest, y0_m + rows * ARCHETYPE_ROW_GAP_M


def build_linear_band(mesh_dir: Path, y0_m: float, contract: dict):
    """One run of every linear module, each on its own line.

    build_unit_mesh lays geometry along local X, so a run is several unit
    instances placed end to end -- exactly how the linear lowerer realises a
    span, just without a carrier to follow.
    """
    instances, manifest = [], []
    y_m = y0_m
    for index, (module_id, unit_len) in enumerate(sorted(
            LINEAR_FIXED_EXTENT_M.items())):
        try:
            verts, faces = build_unit_mesh(module_id, None, float(unit_len),
                                           CONTENT_SCALE, contract)
            kind = "linear_unit"
        except Exception:
            # Some ids are point-kind modules; the linear lowerer resolves
            # those through build_point_mesh instead. Try that before giving up.
            try:
                verts, faces = build_point_mesh(module_id, None,
                                                CONTENT_SCALE, contract)
                kind = "point_module"
            except Exception as exc:
                manifest.append({"kind": "linear_skipped", "key": module_id,
                                 "reason": f"{type(exc).__name__}: {exc}"})
                continue
        if not verts or not faces:
            manifest.append({"kind": "linear_skipped", "key": module_id,
                             "reason": "builder returned empty geometry"})
            continue

        mesh_name = f"lin_{module_id}.akm"
        write_akm(mesh_dir / mesh_name, verts, faces,
                  comment=f"asset catalog linear module {module_id} - "
                          f"presentation only, alpha=255 => no collision")
        if kind == "linear_unit":
            units = max(1, int(round(LINEAR_RUN_LEN_M / float(unit_len))))
            for u in range(units):
                x_m = (u - (units - 1) / 2.0) * float(unit_len)
                instances.append(_instance(
                    mesh_name, f"lin_{index:02d}_{module_id}_{u}", x_m, y_m))
        else:
            units = 1
            instances.append(_instance(mesh_name,
                                       f"lin_{index:02d}_{module_id}", 0.0, y_m))
        manifest.append({
            "kind": kind, "key": module_id, "mesh_name": mesh_name,
            "unit_len_m": float(unit_len), "units_placed": units,
            "centre_m": [0.0, round(y_m, 2)],
            "vertices": len(verts), "faces": len(faces),
        })
        y_m += LINEAR_ROW_GAP_M
    return instances, manifest, y_m


def write_a3d(path: Path, patches: list, instances: list, player_start) -> None:
    mats = default_materials.get_default_materials_binary()
    with open(path, "wb") as f:
        A3DHeader(len(patches)).write(f)
        for p in patches:
            p.write(f)
        f.write(mats)
        f.write(struct.pack("<i", WORLD_FORMAT_VERSION))
        f.write(struct.pack("<i", len(instances)))
        for inst in instances:
            inst.write(f)
        f.write(struct.pack("<i", 1))
        player_start.write(f)
        f.write(struct.pack("<i", 0))
        f.write(struct.pack("<i", 0))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=Path(".run/asset_catalog"))
    ap.add_argument("--rules", type=Path, default=DEFAULT_RULES,
                    help="resolved-rules JSON supplying the cross-section")
    ap.add_argument("--list", action="store_true",
                    help="print the inventory and exit")
    ap.add_argument("--editor", action="store_true",
                    help="open the result in ASCIIID Classic")
    args = ap.parse_args(argv)

    if args.list:
        print(f"Point archetypes ({len(ARCHETYPE_PHYSICAL)}):")
        for k, v in ARCHETYPE_PHYSICAL.items():
            print(f"  {k:<28} {v[0]:<28} {v[1]}x{v[2]}x{v[3]} m")
        print(f"\nLinear modules ({len(LINEAR_FIXED_EXTENT_M)}):")
        for k, v in sorted(LINEAR_FIXED_EXTENT_M.items()):
            print(f"  {k:<44} unit {v} m")
        return 0

    if not args.rules.exists():
        print(f"ERROR: resolved-rules file not found: {args.rules}\n"
              "The linear cross-section is rule-owned; this script will not "
              "substitute invented dimensions.", file=sys.stderr)
        return 1
    rules = json.loads(args.rules.read_text(encoding="utf-8"))
    contract = linear_asset_contract({}, rules)

    out_dir = args.out if args.out.is_absolute() else REPO_ROOT / args.out
    mesh_dir = out_dir / "meshes"
    mesh_dir.mkdir(parents=True, exist_ok=True)

    print("=== Classic Asset Catalog ===")
    print(f"  archetypes:    {len(ARCHETYPE_PHYSICAL)}")
    print(f"  linear modules:{len(LINEAR_FIXED_EXTENT_M)}")
    print(f"  cross-section: {contract['cross_section_m']}")
    print(f"  rules source:  {args.rules}")
    print()

    arch_i, arch_m, y_after = build_archetype_band(mesh_dir, 0.0)
    lin_i, lin_m, y_end = build_linear_band(
        mesh_dir, y_after + BAND_GAP_M, contract)
    instances = arch_i + lin_i
    manifest = arch_m + lin_m

    # Plate sized from actual content, centred on the origin.
    half_m = max(
        ARCHETYPE_PER_ROW * ARCHETYPE_PITCH_M / 2.0 + 6.0,
        y_end + 10.0, LINEAR_RUN_LEN_M + 6.0)
    half_w = half_m * CONTENT_SCALE
    grid = int(half_w / 4.0) * 2 + 8
    patches = build_patches(grid, SANDBOX_HEIGHT, SANDBOX_MATERIAL_ID)

    map_path = out_dir / "asset_catalog.a3d"
    player_start = A3DPlayerStart(
        pos=[0.0, -8.0 * CONTENT_SCALE, float(SANDBOX_HEIGHT) + 0.5],
        yaw=0.0, dir=0.0)
    write_a3d(map_path, patches, instances, player_start)

    receipt = out_dir / "catalog_receipt.json"
    skipped = [r for r in manifest
               if r["kind"] in ("linear_skipped", "archetype_missing_builder")]
    receipt.write_text(json.dumps({
        "stage": "asset_catalog",
        "final_acceptance": False,
        "runtime_product": "classic_cp437",
        "map_path": map_path.name,
        "mesh_root": "meshes",
        "reason": ("Every authored Classic asset on one plate for inspection. "
                   "Flat plate, no source data, no reconstruction. Never valid "
                   "as map acceptance evidence."),
        "cross_section_m": contract["cross_section_m"],
        "cross_section_sources": contract["sources"],
        "rules_file": str(args.rules),
        "entries": manifest,
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    placed = [r for r in manifest if r["kind"] != "linear_skipped"]
    print(f"  placed:    {len(placed)} distinct assets "
          f"({len(instances)} instances)")
    print(f"  archetypes:{len([r for r in manifest if r['kind']=='archetype'])}")
    print(f"  linear:    {len([r for r in manifest if r['kind']=='linear_unit'])}"
          f" runs + {len([r for r in manifest if r['kind']=='point_module'])}"
          f" point modules")
    if skipped:
        print(f"  SKIPPED:   {len(skipped)} (reported, not hidden)")
        for r in skipped:
            print(f"     {r['key']}: {r['reason']}")
    print(f"  plate:     {grid}x{grid} patches (+-{grid*4} world units)")
    print(f"  map:       {map_path} ({map_path.stat().st_size} bytes)")
    print(f"  receipt:   {receipt}")

    if not args.editor:
        print("\nOpen in ASCIIID Classic:")
        print(f"  ASCIICKER_ACTIVE_MESH_ROOT={mesh_dir} \\")
        print(f"    python3 scripts/asciid.py launch-classic -- "
              f"--map {map_path}")
        return 0

    env = dict(os.environ)
    env["ASCIICKER_ACTIVE_MESH_ROOT"] = str(mesh_dir)
    return subprocess.call(
        [sys.executable, "scripts/asciid.py", "launch-classic", "--no-wait",
         "--", "--map", str(map_path)], cwd=str(REPO_ROOT), env=env)


if __name__ == "__main__":
    raise SystemExit(main())
