#!/usr/bin/env python3
"""Apply a compiled source palette to an A3D map.

FL-4787 appearance lane. Companion to ``compile_source_palette.py``.

Two data-only edits, no engine or format change:

1.  Write the compiled MatCell recipes into the A3D 256-material table, using
    the free ids (6..255). Material ids 0..5 are left untouched so anything
    still emitting the legacy OSM terrain contract keeps working.

2.  Re-stamp terrain visual cells so each semantic role points at its OWN
    material id instead of sharing one of six. Role ownership per cell is read
    back from the run's existing ``*.carto_cells.bin`` sidecar, which the
    cartographic stamper already wrote:

        uint16 cx, uint16 cy, uint32 feat_index,
        uint8 mat_id, uint8 shade, uint8 role_id, uint8 flags    (12 bytes)

    so no reclassification happens here and no new semantic owner is created.

Only the LOW byte of each visual cell is rewritten. The high byte carries the
stored shade and the 0x8000 elevation flag and is preserved verbatim.

Buildings are deliberately NOT handled here. The building footprint walk lives
in ``bake_osm_building_footprints.py`` and that owner stamps the per-building
roof/wall materials, so the footprint geometry keeps exactly one owner.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import shutil
import struct
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
for p in (str(HERE), str(PROJECT_ROOT / "addons" / "io_asciicker" / "scene")):
    if p not in sys.path:
        sys.path.insert(0, p)

import a3d_format as fmt  # noqa: E402

MATERIAL_SIZE = 512
MATERIAL_COUNT = 256
CELL_RECORD = struct.Struct("<HH I 4B")

RESERVED_MATERIAL_IDS = frozenset((0, 1, 2, 3, 4, 5))

# The legacy terrain ids, measured in the shipped SBU map at the cell Classic
# actually samples -- matlib[mat].shade[1][0]:
#
#   mat 0/1 grass     bg=(0,51,0)      near-black green
#   mat 2   dirt      bg=(86,80,66)
#   mat 3   stone     bg=(96,82,56)
#   mat 4   road      bg=(51,51,51)    near-black, glyph 0 == NULL glyph
#   mat 5   building  bg=(102,106,106) flat mid grey
#
# Those were authored for a lighting-driven shade index, so sampling them at
# shade 0 yields the darkest step of every ramp and the map reads as a dark
# monochrome mush. Any cell the role re-stamp does not cover still lands on one
# of these, so rewrite them as constant, correctly-lit colours too.
BASE_PALETTE: dict[int, tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, ...]]] = {
    0: ((124, 168,  92), (168, 204, 128), (32, 46, 58, 35)),   # grass
    1: ((124, 168,  92), (168, 204, 128), (32, 46, 58, 35)),   # grass
    2: ((146, 132, 108), (182, 170, 146), (32, 46, 58, 35)),   # dirt
    3: ((158, 142, 106), (192, 178, 146), (32, 46, 58, 35)),   # stone
    4: (( 82,  80,  78), (128, 126, 122), (32, 46, 58, 35)),   # road (glyph 0 fixed)
    5: ((150, 146, 138), (186, 182, 174), (32, 46, 58, 35)),   # generic building
}


def material_bytes_constant(bg, fg, glyphs) -> bytes:
    """Build one 512-byte A3DMaterial with a constant colour across all shades.

    WHY CONSTANT: the Classic CP437 renderer resolves every terrain cell as
    ``matlib[mat].shade[1][0]`` (engine/render/render_resolve.cpp:3446-3452) --
    shade index is hardcoded to 0. A light-to-dark ramp would therefore always
    be sampled at its darkest step in Classic. Writing the true colour into all
    16 shade steps makes Classic correct and leaves the lit paths unchanged in
    hue.
    """
    out = io.BytesIO()
    for ramp in range(4):
        glyph = glyphs[ramp] if ramp < len(glyphs) else glyphs[-1]
        for _shade in range(16):
            fmt.MatCell(fg=tuple(fg), gl=int(glyph), bg=tuple(bg), flags=0).write(out)
    data = out.getvalue()
    assert len(data) == MATERIAL_SIZE, len(data)
    return data


def write_material_table(materials: bytes, palette: dict,
                         rewrite_base: bool = True) -> tuple[bytes, int]:
    """Return a new material table with the palette's ids written in."""
    if len(materials) != MATERIAL_COUNT * MATERIAL_SIZE:
        raise RuntimeError(
            f"material table must be {MATERIAL_COUNT * MATERIAL_SIZE} bytes, got {len(materials)}"
        )
    data = bytearray(materials)
    written = 0
    if rewrite_base:
        for mid, (bg, fg, glyphs) in BASE_PALETTE.items():
            blob = material_bytes_constant(bg, fg, glyphs)
            data[mid * MATERIAL_SIZE:(mid + 1) * MATERIAL_SIZE] = blob
            written += 1
    for sid, spec in palette["materials"].items():
        mid = int(sid)
        if mid in RESERVED_MATERIAL_IDS:
            raise RuntimeError(f"palette tried to overwrite reserved material id {mid}")
        if not (0 <= mid < MATERIAL_COUNT):
            raise RuntimeError(f"material id {mid} out of range")
        blob = material_bytes_constant(spec["bg"], spec["fg"], spec["glyphs"])
        data[mid * MATERIAL_SIZE:(mid + 1) * MATERIAL_SIZE] = blob
        written += 1
    return bytes(data), written


def _patch_for_cell(patches_by_xy, cx: int, cy: int):
    px = math.floor(cx / fmt.VISUAL_CELLS)
    py = math.floor(cy / fmt.VISUAL_CELLS)
    patch = patches_by_xy.get((px, py))
    if patch is None:
        return None, 0, 0
    return patch, cx - px * fmt.VISUAL_CELLS, cy - py * fmt.VISUAL_CELLS


def restamp_roles(patches, cells_bin: Path, role_names: dict[int, str],
                  role_material: dict[str, int]) -> dict:
    """Point every classified cell at its own role material id."""
    patches_by_xy = {(p.x, p.y): p for p in patches}
    raw = cells_bin.read_bytes()
    if len(raw) % CELL_RECORD.size:
        raise RuntimeError(
            f"{cells_bin} is not a whole number of {CELL_RECORD.size}-byte records"
        )
    n = len(raw) // CELL_RECORD.size

    changed = 0
    skipped_no_patch = 0
    skipped_unmapped = 0
    skipped_protected = 0
    per_role: Counter = Counter()

    for i in range(n):
        cx, cy, _feat, mat_id, _shade, role_id, _flags = CELL_RECORD.unpack_from(
            raw, i * CELL_RECORD.size
        )
        role = role_names.get(role_id)
        if role is None:
            skipped_unmapped += 1
            continue
        new_mat = role_material.get(role)
        if new_mat is None:
            skipped_unmapped += 1
            continue
        patch, lx, ly = _patch_for_cell(patches_by_xy, cx, cy)
        if patch is None:
            skipped_no_patch += 1
            continue
        old = int(patch.visual[ly][lx])
        # Only re-point cells the carto stamper still owns. If something later
        # in the pipeline (the building bake) took the cell, leave it alone.
        if (old & 0xFF) != (mat_id & 0xFF):
            skipped_protected += 1
            continue
        patch.visual[ly][lx] = (old & 0xFF00) | (new_mat & 0xFF)
        changed += 1
        per_role[role] += 1

    return {
        "records": n,
        "cells_restamped": changed,
        "skipped_no_patch": skipped_no_patch,
        "skipped_unmapped_role": skipped_unmapped,
        "skipped_owned_by_later_stage": skipped_protected,
        "per_role": dict(per_role.most_common()),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--map", required=True, help="input .a3d")
    ap.add_argument("--palette", required=True, help="source_palette.json")
    ap.add_argument("--out", required=True, help="output .a3d")
    ap.add_argument("--carto-cells", help="*.carto_cells.bin from the run")
    ap.add_argument("--carto-roles", help="*.carto_roles.json from the run")
    ap.add_argument("--summary", help="write a JSON receipt here")
    ap.add_argument("--no-base-rewrite", action="store_true",
                    help="leave legacy material ids 0..5 exactly as they are")
    ap.add_argument("--skip-roles", action="store_true",
                    help="write the material table only; do not re-stamp role cells")
    args = ap.parse_args(argv)

    import sbu_satellite_style_postprocess as post

    palette = json.loads(Path(args.palette).read_text(encoding="utf-8"))

    (header, patches, materials, raw_fmt_version, instances,
     player_start, enemy_gens, markers) = post._load_a3d(Path(args.map))

    materials, n_written = write_material_table(materials, palette,
                                                rewrite_base=not args.no_base_rewrite)

    role_summary = {"skipped": True}
    if not args.skip_roles:
        if not (args.carto_cells and args.carto_roles):
            raise SystemExit("--carto-cells and --carto-roles are required unless --skip-roles")
        role_names = {
            int(k): v
            for k, v in json.loads(Path(args.carto_roles).read_text(encoding="utf-8")).items()
        }
        role_summary = restamp_roles(
            patches, Path(args.carto_cells), role_names, palette["role_material"]
        )

    # a3d_edit.write_a3d_sections snapshots the destination before rewriting it,
    # so the destination has to exist first.
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not out_path.exists():
        shutil.copy2(args.map, out_path)

    post._write_a3d(out_path, header, patches, materials, raw_fmt_version,
                    instances, player_start, enemy_gens, markers)

    summary = {
        "map_in": args.map,
        "map_out": args.out,
        "palette": args.palette,
        "materials_written": n_written,
        "roles": role_summary,
        "instances_preserved": len(instances),
        "markers_preserved": len(markers),
        "player_start_preserved": bool(player_start),
    }
    if args.summary:
        Path(args.summary).write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"[palette-apply] materials_written={n_written}")
    if not args.skip_roles:
        print(f"[palette-apply] cells_restamped={role_summary['cells_restamped']} "
              f"of {role_summary['records']} records "
              f"(owned_by_later_stage={role_summary['skipped_owned_by_later_stage']})")
        top = list(role_summary["per_role"].items())[:8]
        print(f"[palette-apply] top roles: {top}")
    print(f"[palette-apply] instances={len(instances)} markers={len(markers)} "
          f"player_start={bool(player_start)}")
    print(f"[palette-apply] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
