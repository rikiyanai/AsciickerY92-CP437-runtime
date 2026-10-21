#!/usr/bin/env python3
# Generate a 20x20 patch flat sandbox A3D map with twin pickup-chain axes.
#
# Layout (top-down, lower-left spawn, upper-right NPC convergence):
#
#   X-axis player walks +X from spawn at y=-70:
#       helmet  ->  armour  ->  shield  ->  CROSSBOW  ->  BIGBEE landmark
#   Y-axis player walks +Y from spawn at x=-70:
#       helmet  ->  armour  ->  shield  ->  SWORD     ->  WOLFIE landmark
#   NPC sits at the opposite corner (+70, +70) for both players to converge on
#   after mounting.
#
# Auto-pickup radius = AUTHORITATIVE_WORLD_ITEM_PICKUP_RADIUS = 6.0f
# (engine/authoritative_world_item_appearance.cpp:9), so 15-unit item
# spacing gives clean sequential pickup as the player walks the axis.
#
# Item definition ids looked up from
# assets/appearance_bundle/phase2-fixtures/positive.bundle.json:
#   402 = Shield Item        (slot=shield)
#   409 = Normal Sword       (slot=weapon)   "MELEE"
#   410 = Normal Helmet      (slot=head)
#   411 = Normal Armour      (slot=armor)
#   412 = Wolf Mountable     (slot=mount, mount_definition_slug=wolf_mount)
#   413 = bee_mountable      DEFERRED entry per bundle catalog ("deferred
#                            2026-05-15 -- bee mount deferred"). The instance
#                            is included for layout parity; pickup may no-op
#                            until the catalog re-enables the slug. The
#                            decorative bigbee.xp sprite at the same coord
#                            keeps the landmark visible regardless.
#   417 = Crossbow           (slot=weapon)
#
# Run:
#   python3 scripts/gen_sandbox_20x20.py --force
#   python3 scripts/inspect_a3d.py assets/a3d/sandbox_20x20.a3d
#   .run/game assets/a3d/sandbox_20x20.a3d   # single-player launch

import argparse
import importlib.util
import os
import struct
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "addons"))


def _load(name, rel):
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
get_default_materials_binary = default_materials.get_default_materials_binary

SANDBOX_HEIGHT = 57
SANDBOX_MATERIAL_ID = 0

ITEM_DEF_HELMET = 410
ITEM_DEF_ARMOUR = 411
ITEM_DEF_SHIELD = 402
ITEM_DEF_SWORD = 409
ITEM_DEF_CROSSBOW = 417
ITEM_DEF_WOLF_MOUNTABLE = 412
ITEM_DEF_BEE_MOUNTABLE = 413
VISUAL_STYLE_DEFAULT = 500
PRESENTATION_KIND_WORLD = 603


def build_patches(grid_size, height, mat_id):
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


def _make_item(def_id, pos):
    inst = A3DInstance(variant="item")
    inst.item_definition_id = def_id
    inst.visual_style_id = VISUAL_STYLE_DEFAULT
    inst.presentation_kind_id = PRESENTATION_KIND_WORLD
    inst.item_count = 1
    inst.pos = [float(pos[0]), float(pos[1]), float(pos[2])]
    inst.yaw = 0.0
    inst.flags = 0
    inst.story_id = 0
    return inst


def _make_sprite(name, pos):
    inst = A3DInstance(variant="sprite")
    inst.inst_name = name
    inst.pos = [float(pos[0]), float(pos[1]), float(pos[2])]
    inst.yaw = 0.0
    inst.anim = 0
    inst.frame = 0
    inst.reps = [0, 0, 0, 0]
    inst.flags = 0
    inst.story_id = 0
    return inst


def build_instances(height):
    z = float(height)
    items = []
    sprites = []

    spacing = 30.0
    grid_xs = [-75.0, -45.0, -15.0, 15.0, 45.0, 75.0]
    grid_ys = [-75.0, -45.0, -15.0, 15.0, 45.0, 75.0]
    spawn_x, spawn_y = grid_xs[0], grid_ys[0]
    npc_x, npc_y = grid_xs[-1], grid_ys[-1]

    # Bottom row (y=spawn_y): X-axis pickup chain — one of each type
    # culminating in the bigbee mount + sprite landmark.
    x_chain_types = [
        ITEM_DEF_HELMET,
        ITEM_DEF_ARMOUR,
        ITEM_DEF_SHIELD,
        ITEM_DEF_CROSSBOW,
        ITEM_DEF_BEE_MOUNTABLE,
    ]
    for x, def_id in zip(grid_xs[1:], x_chain_types):
        items.append(_make_item(def_id, (x, spawn_y, z)))
    sprites.append(_make_sprite("bigbee.xp", (grid_xs[-1], spawn_y, z)))

    # Interior rows (y > spawn_y): each row is one item type, filled across
    # all 6 grid columns. Row order follows Y-axis pickup-chain order so the
    # leftmost cell of each row IS the Y player's next item.
    row_type_by_y = {
        grid_ys[1]: ITEM_DEF_HELMET,   # y=-45
        grid_ys[2]: ITEM_DEF_ARMOUR,   # y=-15
        grid_ys[3]: ITEM_DEF_SHIELD,   # y=+15
        grid_ys[4]: ITEM_DEF_SWORD,    # y=+45
        grid_ys[5]: ITEM_DEF_WOLF_MOUNTABLE,  # y=+75 (wolf row)
    }
    for y, def_id in row_type_by_y.items():
        for x in grid_xs:
            # Skip the opposite corner (reserved for NPC).
            if y == npc_y and x == npc_x:
                continue
            items.append(_make_item(def_id, (x, y, z)))
            # Wolf row gets a wolfie.xp landmark sprite at every cell so the
            # row reads as a visible band, not just invisible mount items.
            if def_id == ITEM_DEF_WOLF_MOUNTABLE:
                sprites.append(_make_sprite("wolfie.xp", (x, y, z)))

    return items, sprites


def build_enemy_gens(height):
    z = float(height)
    npc = A3DEnemyGen()
    npc.pos = [75.0, 75.0, z]
    npc.alive_max = 1
    npc.revive_min = 1000
    npc.revive_max = 2000
    # 0-10 probability scale (engine/enemygen.cpp:19-28).
    npc.armor = 10
    npc.helmet = 10
    npc.shield = 10
    npc.sword = 10
    npc.crossbow = 0
    return [npc]


def write_a3d(output_path, patches, items, sprites, enemy_gens, player_start):
    materials_binary = get_default_materials_binary()
    with open(output_path, "wb") as f:
        header = A3DHeader(len(patches))
        header.write(f)
        for p in patches:
            p.write(f)
        f.write(materials_binary)
        f.write(struct.pack("<i", WORLD_FORMAT_VERSION))
        instances = items + sprites
        f.write(struct.pack("<i", len(instances)))
        for inst in instances:
            inst.write(f)
        f.write(struct.pack("<i", 1))  # has_player_start (v4+)
        player_start.write(f)
        f.write(struct.pack("<i", len(enemy_gens)))
        for eg in enemy_gens:
            eg.write(f)
        f.write(struct.pack("<i", 0))  # marker_count


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", default="assets/a3d/sandbox_20x20.a3d")
    ap.add_argument("--grid", type=int, default=20)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    out = Path(args.output)
    if not out.is_absolute():
        out = REPO_ROOT / out
    if out.exists() and not args.force:
        print(f"ERROR: {out} exists. Pass --force to overwrite.", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)

    print("=== Sandbox 20x20 A3D Generator ===")
    print(f"  output:           {out}")
    print(f"  grid:             {args.grid}x{args.grid} patches ({args.grid*8}x{args.grid*8} cells)")
    print(f"  world extent:     [{-args.grid*4}, {args.grid*4}] each axis")
    print(f"  height:           {SANDBOX_HEIGHT} (flat)")
    print(f"  format_version:   {WORLD_FORMAT_VERSION} (v4)")

    patches = build_patches(args.grid, SANDBOX_HEIGHT, SANDBOX_MATERIAL_ID)
    items, sprites = build_instances(SANDBOX_HEIGHT)
    enemy_gens = build_enemy_gens(SANDBOX_HEIGHT)
    player_start = A3DPlayerStart(
        pos=[-75.0, -75.0, float(SANDBOX_HEIGHT) + 0.5],
        yaw=0.0,
        dir=0.0,
    )

    write_a3d(out, patches, items, sprites, enemy_gens, player_start)
    size = out.stat().st_size

    print(f"  patches:          {len(patches)}")
    print(f"  items:            {len(items)}")
    print(f"  sprites:          {len(sprites)}")
    print(f"  enemy_gens:       {len(enemy_gens)}")
    print(f"  player_start:     {player_start.pos}")
    print()
    print("Item layout (30-unit grid, 6 cols x 6 rows in [-75..+75]):")
    print("  Spawn corner:     (-75, -75, 57.5)")
    print("  X-axis chain  (y=-75):  helmet(-45) armour(-15) shield(+15) crossbow(+45) bigbee(+75)")
    print("  Helmet  row   (y=-45):  6 helmets across x in {-75,-45,-15,+15,+45,+75}")
    print("  Armour  row   (y=-15):  6 armours")
    print("  Shield  row   (y=+15):  6 shields")
    print("  Sword   row   (y=+45):  6 swords")
    print("  Wolf    row   (y=+75):  5 wolf mountables + wolfie.xp sprites (NPC at +75)")
    print("  NPC convergence:  (+75, +75) armor/helmet/shield/sword max")
    print()
    print(f"Wrote {out} ({size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
