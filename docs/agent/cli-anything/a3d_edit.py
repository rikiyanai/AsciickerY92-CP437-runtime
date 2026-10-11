#!/usr/bin/env python3
"""Direct .a3d instance editor — no asciiid process needed.

Reads and rewrites A3D binary files directly using the existing
io_asciicker/scene/a3d_format.py parser. No SDL window, no LOAD_MAP wait.

WARNING: io_scene_a3d/a3d_format.py is a write-only stub with no from_file().
         Import exclusively from io_asciicker/scene/a3d_format.py.

Usage:
    python3 docs/agent/cli-anything/a3d_edit.py list <file.a3d>
    python3 docs/agent/cli-anything/a3d_edit.py delete <file.a3d> --match <pattern>
    python3 docs/agent/cli-anything/a3d_edit.py append <file.a3d> --json <instances.json>
    python3 docs/agent/cli-anything/a3d_edit.py set-markers <file.a3d> --json <markers.json>
    python3 docs/agent/cli-anything/a3d_edit.py copy-markers <dst.a3d> --from <src.a3d>
    python3 docs/agent/cli-anything/a3d_edit.py set-player-start <file.a3d> --x <X> --y <Y> [--z <Z>] [--yaw <YAW>]

[FL-3690] set-player-start derives Z from terrain at (X, Y) and embeds the player-start
record in the v4+ format. Baked OSM pipeline appends instances post-Blender, so this command
must be called after building_specs are appended to reconstruct the player-start that
derive_player_start() would have written in a traditional one-shot Blender export.

Rollback:
    a3d/copy_game_map_y8.a3d is untracked in git.
    First delete creates <file>.bak (original, never overwritten).
    To rollback: cp <file>.bak <file>
"""
import sys
import os
import struct
import shutil
import json
import importlib.util
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _load_a3d_format():
    """Load a3d_format directly from the addon tree without importing bpy."""
    if "a3d_format" in sys.modules:
        return sys.modules["a3d_format"]
    mod_path = PROJECT_ROOT / "addons" / "io_asciicker" / "scene" / "a3d_format.py"
    spec = importlib.util.spec_from_file_location("a3d_format", mod_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot find a3d_format.py at {mod_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["a3d_format"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_a3d_import_core():
    """Load a3d_import_core directly from the addon tree without importing bpy."""
    if "a3d_import_core" in sys.modules:
        return sys.modules["a3d_import_core"]
    mod_path = PROJECT_ROOT / "addons" / "io_asciicker" / "scene" / "a3d_import_core.py"
    spec = importlib.util.spec_from_file_location("a3d_import_core", mod_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot find a3d_import_core.py at {mod_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["a3d_import_core"] = mod
    spec.loader.exec_module(mod)
    return mod


a3d_format = _load_a3d_format()
a3d_import_core = _load_a3d_import_core()
A3DInstance = a3d_format.A3DInstance
A3DEnemyGen = a3d_format.A3DEnemyGen
A3DMinimapMarker = a3d_format.A3DMinimapMarker
BASE_TERRAIN_HEIGHT = a3d_format.BASE_TERRAIN_HEIGHT
HEIGHT_SCALE = a3d_format.HEIGHT_SCALE
HEIGHT_CELLS = a3d_format.HEIGHT_CELLS
VISUAL_CELLS = a3d_format.VISUAL_CELLS
infer_mesh_instance_z_baseline = a3d_import_core.infer_mesh_instance_z_baseline
reverse_instance_transform = a3d_import_core.reverse_instance_transform

PATCH_SIZE = 188       # A3DPatch: 188 bytes each (from a3d_format.py docstring)
MATERIAL_SIZE = 512    # A3DMaterial: 512 bytes each
MATERIAL_COUNT = 256   # always 256 materials


def read_a3d_instances(path):
    """Parse instance section from .a3d file.

    Returns:
        (pre_bytes, fmt_version, instances, tail_bytes)
        pre_bytes  -- raw bytes for header + patches + materials (preserved verbatim)
        fmt_version -- int32 format version (currently -2 on newly written maps)
        instances  -- list of A3DInstance objects
        tail_bytes -- raw bytes for enemygens + any trailing data (preserved verbatim)
    """
    with open(path, 'rb') as f:
        sig = f.read(4)
        if sig != b'AS3D':
            raise ValueError(f"Not an A3D file (got {sig!r}): {path}")
        header_size = struct.unpack('<I', f.read(4))[0]
        num_patches = struct.unpack('<I', f.read(4))[0]
        # f.read(4) would be reserved; included in pre_bytes below

        # Read pre-instance section as raw bytes (header + patches + materials)
        pre_size = header_size + num_patches * PATCH_SIZE + MATERIAL_COUNT * MATERIAL_SIZE
        f.seek(0)
        pre_bytes = f.read(pre_size)
        assert f.tell() == pre_size, f"Pre-section read mismatch: {f.tell()} != {pre_size}"

        # Instance section
        raw_fmt_version = struct.unpack('<i', f.read(4))[0]
        fmt_version = -raw_fmt_version if raw_fmt_version < 0 else raw_fmt_version
        count = struct.unpack('<i', f.read(4))[0]
        instances = [A3DInstance.from_file(f, format_version=fmt_version) for _ in range(count)]

        # Tail (enemygens + anything else) — preserved verbatim
        tail_bytes = f.read()

    return pre_bytes, raw_fmt_version, instances, tail_bytes


def write_a3d_instances(path, pre_bytes, fmt_version, instances, tail_bytes):
    """Write modified A3D file atomically.

    - Creates <path>.bak on first invocation only (preserves original).
    - Writes to <path>.tmp, then os.replace() for atomic swap on POSIX.
    """
    path = Path(path)
    bak = path.with_suffix(path.suffix + '.bak')
    if not bak.exists():
        shutil.copy2(path, bak)

    tmp = path.with_suffix(path.suffix + '.tmp')
    with open(tmp, 'wb') as f:
        f.write(pre_bytes)
        f.write(struct.pack('<i', fmt_version))
        f.write(struct.pack('<i', len(instances)))
        for inst in instances:
            inst.write(f)
        f.write(tail_bytes)
    os.replace(tmp, path)


def read_a3d_sections(path, *, strict_markers=True):
    """Parse A3D into structured sections, including player-start and markers.

    Handles both v3 (no player-start) and v4 (has player-start) formats.
    For v3 files, returns player_start=None.
    """
    fmt = _load_a3d_format()
    with open(path, "rb") as f:
        sig = f.read(4)
        if sig != b"AS3D":
            raise ValueError(f"Not an A3D file (got {sig!r}): {path}")
        header_size = struct.unpack("<I", f.read(4))[0]
        num_patches = struct.unpack("<I", f.read(4))[0]
        pre_size = header_size + num_patches * PATCH_SIZE + MATERIAL_COUNT * MATERIAL_SIZE
        f.seek(0)
        pre_bytes = f.read(pre_size)

        raw_fmt_version = struct.unpack("<i", f.read(4))[0]
        fmt_version = -raw_fmt_version if raw_fmt_version < 0 else raw_fmt_version
        inst_count = struct.unpack("<i", f.read(4))[0]
        instances = [fmt.A3DInstance.from_file(f, format_version=fmt_version) for _ in range(inst_count)]

        # [FL-3690] v4+ files have a player-start slot between instances and
        # enemy_gens. raw_fmt_version is negative for the modern A3D layout;
        # fmt_version is normalized for A3DInstance parsing only.
        player_start = None
        if raw_fmt_version <= -4:
            has_ps_raw = f.read(4)
            if len(has_ps_raw) == 4:
                has_ps = struct.unpack("<i", has_ps_raw)[0]
                if has_ps:
                    player_start = fmt.A3DPlayerStart.from_file(f)

        enemy_gens = []
        enemy_raw = f.read(4)
        if len(enemy_raw) == 4:
            enemy_count = struct.unpack("<i", enemy_raw)[0]
            enemy_gens = [fmt.A3DEnemyGen.from_file(f) for _ in range(enemy_count)]

        markers = []
        marker_raw = f.read(4)
        if len(marker_raw) == 4:
            marker_count = struct.unpack("<i", marker_raw)[0]
            try:
                markers = [fmt.A3DMinimapMarker.from_file(f) for _ in range(marker_count)]
            except ValueError:
                if strict_markers:
                    raise
                markers = []

    return pre_bytes, raw_fmt_version, instances, player_start, enemy_gens, markers


def write_a3d_sections(path, pre_bytes, fmt_version, instances, player_start, enemy_gens, markers):
    """Write A3D sections atomically, preserving player-start and markers.

    [FL-3690] Handles both v3 (no player-start) and v4 (with player-start) formats.
    Pass player_start=None to preserve v3 format or omit the player-start section.
    """
    path = Path(path)
    bak = path.with_suffix(path.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(path, bak)

    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(pre_bytes)
        f.write(struct.pack("<i", fmt_version))
        f.write(struct.pack("<i", len(instances)))
        for inst in instances:
            inst.write(f)
        # [FL-3690] Write player-start section: v4+ always has a has_ps slot;
        # v3 has no slot. Omitting has_ps=0 in v4 misaligns enemy/marker reads.
        if fmt_version <= -4:
            f.write(struct.pack("<i", 1 if player_start is not None else 0))
            if player_start is not None:
                player_start.write(f)
        f.write(struct.pack("<i", len(enemy_gens)))
        for gen in enemy_gens:
            gen.write(f)
        f.write(struct.pack("<i", len(markers)))
        for marker in markers:
            marker.write(f)
    os.replace(tmp, path)


def get_pos(inst, z_baseline=None):
    """Extract world position from any instance variant.

    Z is returned as Blender-normalized elevation: 0 = ground level,
    positive = above ground.
    """
    if inst.variant == 'mesh':
        if z_baseline is None:
            z_baseline = infer_mesh_instance_z_baseline(inst.transform[14])
        t = reverse_instance_transform(inst.transform, z_baseline=z_baseline)
        return t[12], t[13], t[14]
    elif inst.variant in ('sprite', 'item'):
        return inst.pos[0], inst.pos[1], inst.pos[2]
    else:
        raise ValueError(f"Unknown instance variant: {inst.variant!r}")


def cmd_list(path):
    """Print all instances with idx, type, names, and position."""
    _, _, instances, _ = read_a3d_instances(path)
    hdr = f"{'IDX':>4}  {'TYPE':6}  {'MESH NAME':40}  {'INST NAME':30}  {'X':>10}  {'Y':>10}  {'Z(elev)':>10}"
    print(hdr)
    print('-' * len(hdr))
    for i, inst in enumerate(instances):
        x, y, z = get_pos(inst)
        mesh = getattr(inst, 'mesh_name', '') or ''
        name = inst.inst_name or ''
        print(f"{i:>4}  {inst.variant:6}  {mesh:40}  {name:30}  {x:>10.2f}  {y:>10.2f}  {z:>10.2f}")
    print(f"\nTotal: {len(instances)} instance(s)")


def cmd_delete(path, pattern):
    """Delete instances whose mesh_name or inst_name contains pattern (case-insensitive)."""
    pre, fv, instances, tail = read_a3d_instances(path)
    pat = pattern.lower()
    keep = []
    deleted = []
    for inst in instances:
        mesh = (getattr(inst, 'mesh_name', '') or '').lower()
        name = (inst.inst_name or '').lower()
        if pat in mesh or pat in name:
            deleted.append(inst)
        else:
            keep.append(inst)

    print(f"Matched {len(deleted)} instance(s) to delete:")
    for inst in deleted:
        x, y, z = get_pos(inst)
        mesh = getattr(inst, 'mesh_name', '') or ''
        print(f"  [{inst.variant}] mesh={mesh!r}  inst={inst.inst_name!r}  @ {x:.2f},{y:.2f},{z:.2f}")

    if not deleted:
        print("Nothing deleted.")
        return

    write_a3d_instances(path, pre, fv, keep, tail)
    bak = Path(str(path) + '.bak')
    print(f"\nSaved. {len(keep)} instance(s) remain. Backup: {bak}")
    print(f"Rollback: cp {bak} {path}")


def _instance_from_dict(raw):
    inst = A3DInstance(
        mesh_name=raw.get("mesh_name", ""),
        inst_name=raw.get("inst_name", ""),
        transform=list(raw.get("transform") or []),
        flags=int(raw.get("flags", 3)),
        story_id=int(raw.get("story_id", -1)),
        variant=raw.get("variant", "mesh"),
    )
    if inst.variant != "mesh":
        raise ValueError(f"append currently supports mesh instances only, got {inst.variant!r}")
    if len(inst.transform) != 16:
        raise ValueError(f"mesh instance requires 16 transform values, got {len(inst.transform)}")
    return inst


def cmd_append(path, json_path):
    """Append mesh instances from a JSON file."""
    pre, fv, instances, tail = read_a3d_instances(path)
    with open(json_path, "r", encoding="utf-8") as fh:
        raw_instances = json.load(fh)
    if not isinstance(raw_instances, list):
        raise ValueError(f"append payload must be a list, got {type(raw_instances).__name__}")

    appended = [_instance_from_dict(raw) for raw in raw_instances]
    write_a3d_instances(path, pre, fv, instances + appended, tail)
    print(f"Appended {len(appended)} instance(s) from {json_path}")
    print(f"Total instances: {len(instances) + len(appended)}")


def cmd_copy_markers(path, source_path):
    """Copy the embedded minimap-marker section from source_path into path.

    [FL-3690] Preserves the existing player-start record (if any) in the destination map.
    """
    pre, fv, instances, player_start, enemy_gens, _markers = read_a3d_sections(path, strict_markers=False)
    _src_pre, _src_fv, _src_instances, _src_player_start, _src_enemy_gens, src_markers = read_a3d_sections(source_path)
    write_a3d_sections(path, pre, fv, instances, player_start, enemy_gens, src_markers)
    print(f"Copied {len(src_markers)} marker(s) from {source_path}")
    print(f"Destination map: {path}")


def _marker_type_from_raw(raw_type):
    if isinstance(raw_type, str):
        token = raw_type.strip().lower()
        if token == "building":
            return A3DMinimapMarker.TYPE_BUILDING
        if token == "region":
            return A3DMinimapMarker.TYPE_REGION
        if token in ("custom", ""):
            return A3DMinimapMarker.TYPE_CUSTOM
        raise ValueError(f"Unknown marker_type string: {raw_type!r}")
    if raw_type is None:
        return A3DMinimapMarker.TYPE_CUSTOM
    value = int(raw_type)
    if value < 0 or value > 255:
        raise ValueError(f"Invalid marker_type value: {value}")
    return value


def _marker_from_dict(raw):
    if not isinstance(raw, dict):
        raise ValueError(f"marker payload must be an object, got {type(raw).__name__}")
    name = str(raw.get("name", "") or "")
    label = str(raw.get("label", "") or "")
    x = float(raw.get("x", 0.0))
    y = float(raw.get("y", 0.0))
    fg = int(raw.get("fg", 226))
    glyph = str(raw.get("glyph", "X") or "X")
    marker_type = _marker_type_from_raw(raw.get("marker_type", A3DMinimapMarker.TYPE_CUSTOM))
    return A3DMinimapMarker(
        name=name,
        label=label,
        x=x,
        y=y,
        fg=fg,
        glyph=glyph[0],
        marker_type=marker_type,
    )


def cmd_set_markers(path, json_path):
    """Replace the embedded minimap-marker section with markers from JSON.

    [FL-3690] Preserves the existing player-start record (if any) in the map.
    """
    pre, fv, instances, player_start, enemy_gens, _markers = read_a3d_sections(path)
    with open(json_path, "r", encoding="utf-8") as fh:
        raw_markers = json.load(fh)
    if not isinstance(raw_markers, list):
        raise ValueError(f"marker payload must be a list, got {type(raw_markers).__name__}")
    markers = [_marker_from_dict(raw) for raw in raw_markers]
    write_a3d_sections(path, pre, fv, instances, player_start, enemy_gens, markers)
    print(f"Wrote {len(markers)} marker(s) from {json_path}")
    print(f"Destination map: {path}")


def derive_player_start_from_terrain(map_path, world_x, world_y, spawn_z_elev=16.0):
    """Derive map player-start by probing terrain height at (world_x, world_y).

    [FL-3690] Mirrors export_a3d.py:derive_player_start() logic without Blender.
    Reads the terrain patch grid directly from the A3D binary and samples
    height at the given world position, then adds spawn_z_elev above it.

    Args:
        map_path:     Path to the terrain-containing A3D file.
        world_x:      World X coordinate (post-offset, patch-grid units).
        world_y:      World Y coordinate.
        spawn_z_elev: Height above terrain sample for spawn Z. Default 16.0
                     starts the runtime just above the baked surface without a
                     long fall through the camera-height band.

    Returns:
        A3DPlayerStart with derived pos and default yaw/dir, or None on failure.
    """
    fmt = _load_a3d_format()
    with open(map_path, "rb") as f:
        sig = f.read(4)
        if sig != b"AS3D":
            raise ValueError(f"Not an A3D file: {map_path}")
        header_size = struct.unpack("<I", f.read(4))[0]
        num_patches = struct.unpack("<I", f.read(4))[0]
        # pre_bytes = header(16) + num_patches * PATCH_SIZE + 256*512
        pre_size = header_size + num_patches * PATCH_SIZE + MATERIAL_COUNT * MATERIAL_SIZE

        # Read terrain patches to find the one covering (world_x, world_y)
        f.seek(header_size)
        patches = []
        for _ in range(num_patches):
            patch = fmt.A3DPatch.from_file(f)
            patches.append(patch)

    if not patches:
        return None

    # Find the patch containing (world_x, world_y)
    patch_world = float(VISUAL_CELLS)
    target_patch = None
    for patch in patches:
        px0 = float(patch.x) * patch_world
        py0 = float(patch.y) * patch_world
        if px0 <= world_x <= px0 + patch_world and py0 <= world_y <= py0 + patch_world:
            target_patch = patch
            break

    if target_patch is None:
        # Fall back to nearest patch
        target_patch = min(
            patches,
            key=lambda p: (
                (float(p.x) * patch_world + patch_world * 0.5 - world_x) ** 2 +
                (float(p.y) * patch_world + patch_world * 0.5 - world_y) ** 2
            ),
        )

    # Bilinear sample of the target patch
    local_x = max(0.0, min(patch_world, world_x - float(target_patch.x) * patch_world))
    local_y = max(0.0, min(patch_world, world_y - float(target_patch.y) * patch_world))
    vertex_step = patch_world / float(HEIGHT_CELLS)
    fx = local_x / vertex_step
    fy = local_y / vertex_step
    x0 = min(HEIGHT_CELLS - 1, max(0, int(fx)))
    y0 = min(HEIGHT_CELLS - 1, max(0, int(fy)))
    x1 = min(HEIGHT_CELLS, x0 + 1)
    y1 = min(HEIGHT_CELLS, y0 + 1)
    tx = max(0.0, min(1.0, fx - x0))
    ty = max(0.0, min(1.0, fy - y0))
    h00 = float(target_patch.height[y0][x0])
    h10 = float(target_patch.height[y0][x1])
    h01 = float(target_patch.height[y1][x0])
    h11 = float(target_patch.height[y1][x1])
    hx0 = h00 + (h10 - h00) * tx
    hx1 = h01 + (h11 - h01) * tx
    terrain_h = hx0 + (hx1 - hx0) * ty

    return fmt.A3DPlayerStart(
        pos=[float(world_x), float(world_y), terrain_h + float(spawn_z_elev)],
        yaw=0.0,
        dir=0.0,
    )


def cmd_set_player_start(path, world_x, world_y, spawn_z_elev=16.0):
    """Derive player-start from terrain height at (world_x, world_y) and embed in the A3D.

    [FL-3690] Required after baked OSM pipeline appends instances post-Blender —
    derive_player_start() runs only during Blender's export_a3d.py save_a3d(), which
    is skipped for terrain-only prebake output.

    Args:
        path:          Path to the A3D file to update.
        world_x:       World X coordinate for spawn.
        world_y:       World Y coordinate for spawn.
        spawn_z_elev:  Height above terrain surface for player Z. Default 16.0.
    """
    ps = derive_player_start_from_terrain(path, world_x, world_y, spawn_z_elev)
    if ps is None:
        raise RuntimeError(f"Could not derive player-start from terrain in {path}")

    pre, fv, instances, _old_ps, enemy_gens, markers = read_a3d_sections(path)
    out_fv = fv if fv <= -4 else -4
    write_a3d_sections(path, pre, out_fv, instances, ps, enemy_gens, markers)
    print(f"Embedded player-start at ({ps.pos[0]:.1f}, {ps.pos[1]:.1f}, {ps.pos[2]:.1f})")
    print(f"Destination map: {path}")


def main():
    if len(sys.argv) < 3 or sys.argv[1] not in ('list', 'delete', 'append', 'set-markers', 'copy-markers', 'set-player-start'):
        print(__doc__)
        sys.exit(1)

    cmd, path = sys.argv[1], sys.argv[2]

    if cmd == 'list':
        cmd_list(path)
    elif cmd == 'delete':
        if '--match' not in sys.argv:
            print("Error: delete requires --match PATTERN")
            sys.exit(1)
        idx = sys.argv.index('--match') + 1
        if idx >= len(sys.argv):
            print("Error: --match requires a pattern argument")
            sys.exit(1)
        cmd_delete(path, sys.argv[idx])
    elif cmd == 'append':
        if '--json' not in sys.argv:
            print("Error: append requires --json FILE")
            sys.exit(1)
        idx = sys.argv.index('--json') + 1
        if idx >= len(sys.argv):
            print("Error: --json requires a file path")
            sys.exit(1)
        cmd_append(path, sys.argv[idx])
    elif cmd == 'set-markers':
        if '--json' not in sys.argv:
            print("Error: set-markers requires --json FILE")
            sys.exit(1)
        idx = sys.argv.index('--json') + 1
        if idx >= len(sys.argv):
            print("Error: --json requires a file path")
            sys.exit(1)
        cmd_set_markers(path, sys.argv[idx])
    elif cmd == 'copy-markers':
        if '--from' not in sys.argv:
            print("Error: copy-markers requires --from FILE")
            sys.exit(1)
        idx = sys.argv.index('--from') + 1
        if idx >= len(sys.argv):
            print("Error: --from requires a file path")
            sys.exit(1)
        cmd_copy_markers(path, sys.argv[idx])
    elif cmd == 'set-player-start':
        # [FL-3690] Parse --x, --y (required), --z, --yaw (optional)
        x_idx = next((i for i, a in enumerate(sys.argv) if a == '--x'), None)
        y_idx = next((i for i, a in enumerate(sys.argv) if a == '--y'), None)
        z_idx = next((i for i, a in enumerate(sys.argv) if a == '--z'), None)
        yaw_idx = next((i for i, a in enumerate(sys.argv) if a == '--yaw'), None)
        if x_idx is None or y_idx is None:
            print("Error: set-player-start requires --x <X> --y <Y>")
            sys.exit(1)
        world_x = float(sys.argv[x_idx + 1])
        world_y = float(sys.argv[y_idx + 1])
        spawn_z = float(sys.argv[z_idx + 1]) if z_idx is not None else 16.0
        cmd_set_player_start(path, world_x, world_y, spawn_z)


if __name__ == '__main__':
    main()
