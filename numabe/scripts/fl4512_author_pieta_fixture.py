#!/usr/bin/env python3
# =============================================================================
# fl4512_author_pieta_fixture.py
# =============================================================================
# WHAT THIS SCRIPT DOES
# ---------------------
# Authors the committed 4x4 "pieta" capture fixture map for the FL-4512 paper lane:
#
#     assets/a3d/frog_pieta_4x4.a3d
#
# It starts from the validated flat 1x1 Pieta source, clones that flat patch
# into a centered 4x4 floor, and replaces its mesh-instance list with ONE
# instance of the revision-0 Pieta mesh carrying the mesh:200 MaterialKeyV1
# binding. It also writes a gameplay player start outside the mesh bounds.
#
# WHY A FILE PATCHER (and not the editor UI)
# ------------------------------------------
# The mesh material binding ("MaterialKey") is per-instance data serialized in
# the .a3d map file (engine/world_serialization_a3d.cpp, format version -5).
# The editor's CDP control surface can place meshes (PLACE_MESH) but has no
# scripted command that assigns a MaterialKey to a placed instance — that
# assignment exists only as an ImGui button ("Assign MaterialKey to Selected
# Meshes", editor/asciiid.cpp:30360). A committed fixture map with the binding
# already serialized is the reproducible route the proof plans expect: the
# skull fixture (minimal_1x1_TEST_WITH_SKULL.a3d) got its binding the same way.
#
# FILE FORMAT (verified against engine/world_serialization_a3d.cpp and
# scripts/debug_a3d_reader.py)
# ----------------------------
#   header   : 4-byte signature "AS3D" + u32 header_size + u32 num_patches
#              + u32 reserved                                     (16 bytes)
#   patches  : num_patches records of exactly 188 bytes each:
#                i32 px, i32 py            (patch grid coordinate)
#                64 x u16 visual           (per-cell material/visual bytes)
#                25 x u16 height           (5x5 heightfield corners)
#                u16 diag                  (diagonal split flags)
#   materials: fixed 131072-byte material table (256 slots x 512 bytes)
#   world    : i32 format_version (negative; -5 adds MaterialKeyV1,
#              -1 matches the pinned original upstream runtime)
#              i32 num_of_instances
#              per MESH instance:
#                i32 mesh_id_len, mesh_id bytes      (mesh file name, .akm)
#                i32 inst_name_len, inst_name bytes  (display name)
#                16 x f64 transform (column-major; translation in tm[12..14])
#                i32 flags, i32 story_id
#                i32 material_key, i32 material_key_override   (version -5)
#              (sprite/item records use mesh_id_len sentinels -1/-2; the skull
#               source map contains only mesh records, which we verify.)
#   trailer  : -4/-5 retain the player-start block and following bytes;
#              -1 emits the enemy-generator count expected immediately after
#              SaveWorld by the pinned original upstream runtime.
#
# USAGE
# -----
#   python3 scripts/fl4512_author_pieta_fixture.py
#
# The defaults place the statue on the north half of the flat floor, use a
# 60/60/960 basis fitted to the 160x90 TERM viewport, start from the visually
# measured runtime framing start, and bind mesh material 200 (the paper's key).
# =============================================================================

import argparse
import hashlib
import os
import struct
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

HEADER_BYTES = 16          # "AS3D" + header_size + num_patches + reserved
PATCH_BYTES = 188          # 8 (px,py) + 128 (visual) + 50 (heights) + 2 (diag)
MATERIALS_BYTES = 131072   # fixed 256 * 512 material table


def sha256(path_or_bytes):
    """Content hash so the authored fixture is pinnable in receipts."""
    h = hashlib.sha256()
    if isinstance(path_or_bytes, bytes):
        h.update(path_or_bytes)
    else:
        with open(path_or_bytes, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()


def parse_world_section(data, off):
    """Walk the instance list starting at byte offset `off`.

    Returns (format_version, [instance descriptions], end_offset) where
    end_offset points at the first byte AFTER the last instance record —
    the start of the verbatim trailer we preserve.
    """
    (fmt,) = struct.unpack_from("<i", data, off)
    off += 4
    if fmt >= 0:
        raise SystemExit(f"FAIL-CLOSED: expected negative format version, got {fmt}")
    (count,) = struct.unpack_from("<i", data, off)
    off += 4
    insts = []
    for k in range(count):
        (mesh_id_len,) = struct.unpack_from("<i", data, off)
        off += 4
        if mesh_id_len < 0:
            # Sprite (-1) / item (-2) records exist in the format, but the skull
            # source map must contain only meshes; refuse anything else so we
            # never mis-splice the byte stream.
            raise SystemExit(f"FAIL-CLOSED: non-mesh instance (sentinel {mesh_id_len}) at #{k}")
        mesh_id = data[off:off + mesh_id_len].decode()
        off += mesh_id_len
        (inst_name_len,) = struct.unpack_from("<i", data, off)
        off += 4
        inst_name = data[off:off + inst_name_len].decode()
        off += inst_name_len
        tm = struct.unpack_from("<16d", data, off)
        off += 16 * 8
        flags, story_id = struct.unpack_from("<ii", data, off)
        off += 8
        material_key, override = (0, 0)
        if -fmt >= 5:  # version -5 adds MaterialKeyV1 per mesh instance
            material_key, override = struct.unpack_from("<ii", data, off)
            off += 8
        insts.append({"mesh": mesh_id, "name": inst_name, "transform": tm,
                      "translate": tm[12:15], "flags": flags,
                      "story_id": story_id, "material_key": material_key,
                      "override": override})
    return fmt, insts, off


def build_mesh_record(mesh, name, x, y, z, scale_xy, scale_z, flags, story_id,
                      material_key, override, with_material_fields=True):
    """Serialize one mesh instance record (see format notes above).

    with_material_fields=True  -> version -5 shape (material_key + override)
    with_material_fields=False -> version -1/-4 shape (no MaterialKeyV1 bytes);
    the engine then resolves the key through the mesh asset default, which is
    200 for every mesh (engine/world_internal.h:601) — the same route the
    committed skull fixture's records use."""
    rec = b""
    rec += struct.pack("<i", len(mesh)) + mesh.encode()
    rec += struct.pack("<i", len(name)) + name.encode()
    # Column-major 4x4: uniform scale on the diagonal, translation in row 4.
    tm = [scale_xy, 0, 0, 0,
          0, scale_xy, 0, 0,
          0, 0, scale_z, 0,
          x, y, z, 1.0]
    rec += struct.pack("<16d", *tm)
    rec += struct.pack("<ii", flags, story_id)
    if with_material_fields:
        rec += struct.pack("<ii", material_key, override)
    return rec


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default="assets/a3d/frog_pieta_1x1.a3d")
    ap.add_argument("--mesh", default="pieta.akm",
                    help="mesh file name as the engine resolves it (meshes dir)")
    ap.add_argument("--out", default="assets/a3d/frog_pieta_4x4.a3d")
    ap.add_argument("--grid-patches", type=int, default=4, choices=(4,))
    ap.add_argument("--x", type=float, default=8.0)
    ap.add_argument("--y", type=float, default=18.0)
    ap.add_argument("--z", type=float, default=40960.0)
    ap.add_argument("--scale-xy", type=float, default=60.0,
					help="serialized Pieta XY basis fitted to the 160x90 TERM viewport")
    ap.add_argument("--scale-z", type=float, default=960.0,
					help="serialized Pieta Z basis paired with scale-xy=60")
    ap.add_argument("--camera-x", type=float, default=8.0)
    # FL-4731: the previous y=8 start was inside the transformed Pieta bounds
    # (2.46..31.56). Keep the tracked player outside the mesh for live play.
    ap.add_argument("--camera-y", type=float, default=-8.0)
    ap.add_argument("--camera-z", type=float, default=41090.0)
    ap.add_argument("--camera-yaw", type=float, default=45.0)
    ap.add_argument("--camera-dir", type=float, default=0.0)
    ap.add_argument("--material", type=int, default=200,
                    help="MaterialKeyV1 binding (mesh:200 is the paper key)")
    ap.add_argument("--inst-name", default="pieta",
                    help="serialized instance display name (may be empty)")
    ap.add_argument("--format-version", type=int, default=-5, choices=(-1, -4, -5),
                    help="-5 writes MaterialKeyV1 fields; -4 mirrors the "
                         "committed skull fixture's record shape; -1 emits "
                         "the original upstream runtime layout")
    ap.add_argument("--override", type=int, default=0, choices=(0, 1),
                    help="MaterialKeyV1 override flag; 0 = resolve through the "
                         "asset default (which is 200 for every mesh, "
                         "engine/world_internal.h:601)")
    args = ap.parse_args()

    src_path = os.path.join(REPO, args.src)
    out_path = os.path.join(REPO, args.out)
    data = open(src_path, "rb").read()

    # ── locate the world section by walking the fixed-size prefix ──
    sig = data[:4]
    if sig != b"AS3D":
        raise SystemExit(f"FAIL-CLOSED: bad signature {sig!r}")
    (_, num_patches, _) = struct.unpack_from("<III", data, 4)
    world_off = HEADER_BYTES + num_patches * PATCH_BYTES + MATERIALS_BYTES
    source_header = data[:HEADER_BYTES]
    template_patch = data[HEADER_BYTES:HEADER_BYTES + PATCH_BYTES]
    materials = data[HEADER_BYTES + num_patches * PATCH_BYTES:world_off]
    if len(template_patch) != PATCH_BYTES or len(materials) != MATERIALS_BYTES:
        raise SystemExit("FAIL-CLOSED: source terrain/material prefix has unexpected size")

    # The 1x1 source put the player start outside its only patch. Build a flat,
    # centered 4x4 floor by cloning the verified flat patch at coordinates
    # -1..2 on both axes. The material cells and heights remain byte-identical.
    grid_start = -(args.grid_patches // 2) + 1
    patch_records = []
    for py in range(grid_start, grid_start + args.grid_patches):
        for px in range(grid_start, grid_start + args.grid_patches):
            patch_records.append(struct.pack("<ii", px, py) + template_patch[8:])
    header = source_header[:8] + struct.pack("<I", len(patch_records)) + source_header[12:16]
    prefix = header + b"".join(patch_records) + materials

    fmt, insts, trailer_off = parse_world_section(data, world_off)
    trailer = data[trailer_off:]
    if args.format_version == -1:
        # Pinned upstream commit 8ff75d0c5 understands world format v1 only.
        # Its LoadGame() uses the fixed player pose (0,15,0), and the bytes
        # immediately following SaveWorld are the enemy-generator count.
        # Emitting the modern player-start block here would make upstream read
        # `has_start` as an enemy count and corrupt the comparison runtime.
        trailer = struct.pack("<i", 0)
    else:
        if len(trailer) < 24 or struct.unpack_from("<i", trailer, 0)[0] != 1:
            raise SystemExit("FAIL-CLOSED: expected serialized player-start record")
        trailer = (struct.pack("<i5f", 1, args.camera_x, args.camera_y,
                               args.camera_z, args.camera_yaw, args.camera_dir) +
                   trailer[24:])
    print(f"source: {args.src} fmt={fmt} instances={len(insts)}")
    for i in insts:
        print(f"  - {i['mesh']!r} name={i['name']!r} at "
              f"({i['translate'][0]:.2f},{i['translate'][1]:.2f},{i['translate'][2]:.2f}) "
              f"material_key={i['material_key']} override={i['override']}")

    # ── verify the replacement mesh exists where the engine will look ──
    mesh_path = os.path.join(REPO, "assets", "meshes", args.mesh)
    if not os.path.exists(mesh_path):
        raise SystemExit(f"FAIL-CLOSED: mesh not found: {mesh_path}")

    # ── splice: same prefix + ONE pieta record + same trailer ──
    # The requested format owns both the mesh-record shape and trailer shape.
    # Version -5 carries MaterialKeyV1 fields. Versions -1/-4 stop after the
    # legacy flags/story pair. Versions -4/-5 retain the modern player-start
    # trailer; version -1 uses the original enemy-generator trailer above.
    v5 = args.format_version == -5
    rec = build_mesh_record(args.mesh, args.inst_name, args.x, args.y, args.z,
                            args.scale_xy, args.scale_z, flags=3, story_id=-1,
                            material_key=args.material, override=args.override,
                            with_material_fields=v5)
    world = struct.pack("<ii", args.format_version, 1) + rec
    out = prefix + world + trailer
    with open(out_path, "wb") as fh:
        fh.write(out)
    print(f"wrote {args.out}  bytes={len(out)}  sha256={sha256(out)}")
    print(f"  floor patches={len(patch_records)} coords={grid_start}.."
          f"{grid_start + args.grid_patches - 1}")
    print(f"  pieta at ({args.x},{args.y},{args.z}) scale_xy={args.scale_xy} "
          f"scale_z={args.scale_z} material_key={args.material} override={args.override}")
    print(f"  camera at ({args.camera_x},{args.camera_y},{args.camera_z}) "
          f"yaw={args.camera_yaw}")
    print(f"  mesh sha256={sha256(mesh_path)}")


if __name__ == "__main__":
    main()
