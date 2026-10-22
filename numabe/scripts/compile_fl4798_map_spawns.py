#!/usr/bin/env python3
"""FL-4798: compile a map's authored spawn facts (items, sprites, enemies, start, markers).

The A3D world section carries more than meshes: placed items (`variant='item'`,
an index into asciicker's item prototype table), placed XP sprites
(campfires, wolves, corpses, statues), enemy generators (spawn point, alive
cap, revive window, equipment) plus the player start and minimap markers.
The Colyseus authority consumes item/generator/start facts. Godot World consumes
sprite facts, and the canonical viewer consumes authored minimap/race markers.
These are source connections; runtime acceptance belongs to the lane receipts.

This compiler is data-only: it turns those records into one checked JSON
sidecar per runtime map, in the runtime's coordinate contract (A3D X/Y minus
the map loader's centre offset -> Godot X/Z; A3D Z / 16 -> Godot Y), with the
sampled ground height beside every authored height and the item prototype
resolved by name/kind.  It instantiates nothing.  Consumers (server spawn
seeding, client world-sprite rows) read this product; they must not re-parse
the A3D.

FL-4834, ELECTION: the canonical Y8 map is
``godot_project/assets/maps/game_map_y8.a3d`` (see the evidence restated in
scripts/apply_map_edits.py).  The migration to world format v4 landed through
the existing writer front door, preserving all 154 item identities and the
absence of an authored player start. The shipped Y8 product therefore passes
one file to both flags, just like the SBU map:

  --source godot_project/assets/maps/game_map_y8.a3d \
  --runtime-terrain godot_project/assets/maps/game_map_y8.a3d

and `source_a3d_sha256 == runtime_terrain_sha256` is the check that there is
exactly one map.

Usage (current):
  python3 scripts/compile_fl4798_map_spawns.py \
      --source godot_project/assets/maps/game_map_y8.a3d \
      --runtime-terrain godot_project/assets/maps/game_map_y8.a3d \
      --output godot_project/assets/world_spawns/game_map_y8.fl4798_map_spawns.v1.json
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
A3D_IMPORT_CORE_PATH = REPO_ROOT / "addons/io_asciicker/scene/a3d_import_core.py"
# FL-4834: the capability model has ONE owner.  This compiler reports it, it
# does not restate it.
CAPABILITY_OWNER_PATH = REPO_ROOT / "scripts/apply_map_edits.py"
A3D_CODEC_PATH = REPO_ROOT / "scripts/fl4588/a3d_document_codec.py"
PLACEMENT_COMPILER_PATH = REPO_ROOT / "scripts/compile_fl4708_y8_tree_placements.py"
SCHEMA = "fl4798_map_spawns.v1"
A3D_HEIGHT_SCALE = 16.0

# asciicker's static item prototype table (upstream game.cpp `item_proto[]`,
# index == A3D `item_definition_id`).  kind: W weapon, R ring, H helmet,
# S shield, A armor, F food, D drink, P potion.  Restated here because the
# engine builds it in code, not from a data file.
ITEM_PROTOTYPES = [
    ("W", "mace", "Giant's Mace"), ("W", "hammer", "Giant's Hammer"), ("W", "axe", "Giant's Axe"),
    ("W", "sword", "Alpha Sword"), ("W", "sword", "Plus Sword"), ("W", "mace", "Small Mace"),
    ("W", "sword", "Small Sword"), ("W", "sword", "Small Saber"), ("W", "axe", "Lumber Axe"),
    ("W", "crossbow", "Crossbow"), ("W", "flail", "Flail"),
    ("R", "white", "Unidentified White Ring"), ("R", "cyan", "Unidentified Cyan Ring"),
    ("R", "gold", "Unidentified Gold Ring"), ("R", "pink", "Unidentified Pink Ring"),
    ("H", "normal", "Light Helmet"), ("H", "normal", "Heavy Helmet"),
    ("S", "normal", "Light Shield"), ("S", "normal", "Heavy Shield"),
    ("A", "normal", "Light Armor"), ("A", "normal", "Heavy Armor"),
    ("F", "meat", "Meat"), ("F", "egg", "Egg"), ("F", "cheese", "Cheese"), ("F", "bread", "Bread"),
    ("F", "beet", "Beet"), ("F", "cucumber", "Cucumber"), ("F", "carrot", "Carrot"), ("F", "apple", "Apple"),
    ("F", "cherry", "Cherry"), ("F", "plum", "Plum"),
    ("D", "milk", "Milk"), ("D", "water", "Water"), ("D", "wine", "Wine"),
    ("P", "red", "Healing Potion"), ("P", "blue", "Mana Potion"), ("P", "green", "Unidentified Green Potion"),
    ("P", "pink", "Unidentified Pink Potion"), ("P", "cyan", "Unidentified Cyan Potion"),
    ("P", "gold", "Unidentified Gold Potion"), ("P", "grey", "Unidentified Grey Potion"),
]
KIND_NAMES = {"W": "weapon", "R": "ring", "H": "helmet", "S": "shield", "A": "armor",
              "F": "food", "D": "drink", "P": "potion"}


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(path: Path) -> str:
    """Repository-relative when possible, absolute otherwise.

    FL-4834: a migration proof compiles this product from a map staged outside
    the worktree.  ``Path.relative_to`` raised there, so the compiler could not
    be pointed at a candidate map at all and the migration had no way to show
    its manifest before the bytes landed.
    """
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _world(pos: list[float], center: tuple[float, float]) -> dict[str, Any]:
    return {
        "world_xz": [float(pos[0]) - center[0], float(pos[1]) - center[1]],
        "authored_godot_y": float(pos[2]) / A3D_HEIGHT_SCALE,
    }


def _player_start(source: Path) -> dict[str, Any] | None:
    codec = _load("fl4798_a3d_codec", A3D_CODEC_PATH)
    try:
        document = codec.parse_document(source.read_bytes())
    except Exception as error:
        # FL-4834: this used to be `except Exception: return None`, which made a
        # REFUSED document indistinguishable from a document that simply has no
        # player start.  That is the silent drop this entry exists to remove: a
        # pre-v3 map would compile a manifest declaring `player_start: null` and
        # nothing anywhere would say the start had never been looked for.  The
        # compile now fails, loudly, with the codec's own words.
        raise SystemExit(
            f"FL4798_MAP_SPAWNS_SOURCE_REFUSED source={source} "
            f"reason={type(error).__name__}: {error} "
            "-- upgrade the map first: python3 scripts/apply_map_edits.py upgrade "
            "--map <a3d> --to-version 4"
        ) from error
    raw = document.player_start
    if not raw or len(raw) < 12:
        return None
    x, y, z = struct.unpack("<3f", raw[:12])
    extra = list(struct.unpack(f"<{(len(raw) - 12) // 4}f", raw[12:12 + ((len(raw) - 12) // 4) * 4]))
    return {"a3d_pos": [x, y, z], "extra": extra}


def compile_spawns(source: Path, runtime_terrain: Path) -> dict[str, Any]:
    source = source.resolve()
    runtime_terrain = runtime_terrain.resolve()
    placement = _load("fl4798_placement_compiler", PLACEMENT_COMPILER_PATH)
    core = _load("fl4798_a3d_core", A3D_IMPORT_CORE_PATH)
    terrain = placement._decode_terrain(runtime_terrain)
    center = tuple(terrain["center_offset"])
    _header, _patches, _materials, instances, enemy_gens, markers = core.load_a3d_file(source)

    def ground(pos: list[float]) -> float | None:
        return placement._sample_terrain_height(terrain, float(pos[0]), float(pos[1]))

    items: list[dict[str, Any]] = []
    sprites: list[dict[str, Any]] = []
    race_barriers: list[dict[str, Any]] = []
    for index, inst in enumerate(instances):
        variant = getattr(inst, "variant", "")
        if variant == "mesh" and str(getattr(inst, "inst_name", "")).startswith("fl4880_barrier_"):
            transform = [float(value) for value in getattr(inst, "transform", [])]
            if len(transform) != 16:
                raise SystemExit(f"FL4798_MAP_SPAWNS_INVALID_BARRIER_TRANSFORM index={index}")
            pos = [transform[12], transform[13], transform[14]]
            race_barriers.append({
                "source_instance_index": index,
                "name": str(inst.inst_name),
                "mesh": str(inst.mesh_name),
                "a3d_transform": transform,
                "ground_y": ground(pos),
                **_world(pos, center),
            })
        elif variant == "item":
            definition = int(inst.item_definition_id)
            proto = ITEM_PROTOTYPES[definition] if 0 <= definition < len(ITEM_PROTOTYPES) else None
            row = {
                "source_instance_index": index,
                "item_definition_id": definition,
                "prototype": None if proto is None else {
                    "kind": KIND_NAMES[proto[0]], "kind_code": proto[0], "sub_kind": proto[1], "name": proto[2],
                },
                "visual_style_id": int(inst.visual_style_id),
                "presentation_kind_id": int(inst.presentation_kind_id),
                "item_count": int(inst.item_count),
                # SpritePrefs.yaw is native degrees (asciiid.cpp). Keep the
                # publication contract named in the unit it actually stores.
                "yaw_degrees": float(inst.yaw),
                "flags": int(inst.flags),
                "story_id": int(inst.story_id),
                "ground_y": ground(inst.pos),
                **_world(inst.pos, center),
            }
            items.append(row)
        elif variant == "sprite":
            sprites.append({
                "source_instance_index": index,
                "sprite": str(inst.inst_name),
                "yaw_degrees": float(inst.yaw),
                "anim": int(inst.anim),
                "frame": int(inst.frame),
                "reps": [int(v) for v in inst.reps],
                "flags": int(inst.flags),
                "story_id": int(inst.story_id),
                "ground_y": ground(inst.pos),
                **_world(inst.pos, center),
            })
    enemies = []
    for index, gen in enumerate(enemy_gens):
        enemies.append({
            "source_generator_index": index,
            "alive_max": int(gen.alive_max),
            "revive_min": int(gen.revive_min),
            "revive_max": int(gen.revive_max),
            "equipment": {
                "armor": int(gen.armor), "helmet": int(gen.helmet), "shield": int(gen.shield),
                "sword": int(gen.sword), "crossbow": int(gen.crossbow),
            },
            "ground_y": ground(gen.pos),
            **_world(gen.pos, center),
        })
    marker_rows = []
    for index, mk in enumerate(markers):
        pos = [float(mk.x), float(mk.y), 0.0]
        marker_rows.append({
            "index": index,
            "name": str(getattr(mk, "name", "")),
            "label": str(getattr(mk, "label", "")),
            "glyph": str(getattr(mk, "glyph", "")),
            "fg": int(getattr(mk, "fg", 0)),
            "marker_type": int(getattr(mk, "marker_type", 0)),
            "ground_y": ground(pos),
            "world_xz": [pos[0] - center[0], pos[1] - center[1]],
        })
    start = _player_start(source)
    if start is not None:
        start.update(_world(start["a3d_pos"], center))
        start["ground_y"] = ground(start["a3d_pos"])
    prototype_counts: dict[str, int] = {}
    for row in items:
        key = row["prototype"]["name"] if row["prototype"] else f"unknown:{row['item_definition_id']}"
        prototype_counts[key] = prototype_counts.get(key, 0) + 1
    result = {
        "$schema": SCHEMA,
        "source_a3d_path": _rel(source),
        "source_a3d_sha256": _sha256(source),
        "runtime_terrain_path": _rel(runtime_terrain),
        "runtime_terrain_sha256": _sha256(runtime_terrain),
        "runtime_center_offset": list(center),
        "coordinate_contract": (
            "A3D X/Y minus the A3DMapLoader centre offset -> Godot X/Z; A3D Z / 16 -> Godot Y; "
            "ground_y is the runtime terrain sampled at the authored X/Y (null off the patch union)"
        ),
        "item_prototype_table": [
            {"item_definition_id": i, "kind": KIND_NAMES[k], "kind_code": k, "sub_kind": s, "name": n}
            for i, (k, s, n) in enumerate(ITEM_PROTOTYPES)
        ],
        "counts": {
            "items": len(items), "sprites": len(sprites), "enemy_generators": len(enemies),
            "minimap_markers": len(marker_rows), "player_start": start is not None,
            "items_by_prototype": dict(sorted(prototype_counts.items())),
            "sprites_by_name": dict(sorted({s["sprite"]: sum(1 for t in sprites if t["sprite"] == s["sprite"]) for s in sprites}.items())),
        },
        "items": items,
        "sprites": sprites,
        "enemy_generators": enemies,
        "player_start": start,
        "minimap_markers": marker_rows,
        # FL-4837: the server half of this block used to say "NOT YET WIRED",
        # and that string was cited as evidence that authored spawns never
        # reached the game. It was a stale docstring and it was FALSE. Verified
        # by direct source read: AuthoritativeRoom.ts:255 gates on
        # mapSpawnsEnabled(), :256 loads this product, :258 seeds from it and
        # keeps the receipt, and :601-603 resolves the spawn base through
        # authoredPlayerStart(). sim/map_spawns.ts:105 seedMapSpawns seeds world
        # items through PROTOTYPE_TO_ITEM_ID and alive_max NPCs per generator.
        # FL-4839: World now consumes the map-stem spawn sidecar's sprite rows.
        # Keep the missing minimap-marker consumer separate from that source
        # connection; neither a status string nor wiring proves visible output.
        "consumers": {
            "server": (
                "colyseus_server: WIRED — AuthoritativeRoom.ts:255-258 seeds worldItems from "
                "`items` and NPCs from `enemy_generators` through sim/map_spawns.ts:105 "
                "seedMapSpawns; AuthoritativeRoom.ts:601-603 resolves the spawn base through "
                "authoredPlayerStart(`player_start`)"
            ),
            "client": (
                "Godot: sprite source CONNECTED — World._publish_map_sprite_rows reads the "
                "map-stem spawn sidecar through build_map_sprite_rows and the compositor's "
                "set_map_sprite_rows. Unsupported sprite mappings still report refusal. "
                "Y8IsoViewer consumes `minimap_markers` from the exact loaded map-stem "
                "product for read-only navigation and race overlays. Source wiring "
                "does not establish visible/runtime acceptance."
            ),
        },
        # FL-4834 measured that the companion FL-4708 manifest carried
        # `runtime_consumption_blocked: false` with an EMPTY blocker string, so
        # NOTHING declared the format gap at runtime: no consumer reported that
        # the map it loaded cannot express a spawn point or an item. This block
        # is that declaration. It is derived, never hand-maintained.
        "runtime_terrain_authoring": _authoring_capability(runtime_terrain),
    }
    # Keep the existing manifest byte shape unchanged for maps without the
    # editor's explicit barrier names.  Authored barriers remain ordinary T2
    # mesh records; this optional projection merely lets the race consumer
    # identify those records without inventing a second geometry file.
    if race_barriers:
        result["race_barriers"] = race_barriers
        result["counts"]["race_barriers"] = len(race_barriers)
    return result


def _authoring_capability(runtime_terrain: Path) -> dict[str, Any]:
    """Declare, per record kind, what the RUNTIME map can legally hold.

    FL-4833/FL-4834.  This block used to carry ONE table,
    ``min_world_format_version``, and it was read as four different claims at
    once.  For ``item`` all four readings disagreed with the file on disk: the
    elected canonical runtime map is now world format v4 after the FL-4834
    migration and physically holds the same 154 item records with their
    identities preserved. Legacy v1 inputs remain refused by the same owner.

    So the four properties are now recorded SEPARATELY, and they are not
    re-derived here: ``scripts/apply_map_edits.py`` is the single owner of the
    capability model and of the version tables behind it.  Duplicating the
    table here is exactly how the two statements drifted apart in the first
    place.
    """
    caps = _load("fl4798_capability_owner", CAPABILITY_OWNER_PATH).capabilities(runtime_terrain)
    block = {
        # The four separately-recorded properties live per kind inside
        # `record_kinds`: format_can_represent, writer_can_author,
        # reader_can_consume; `canonical_codec_accepts` is document level.
        "world_format_version": caps["world_format_version"],
        "canonical_codec_accepts": caps["canonical_codec_accepts"],
        "canonical_codec_error": caps["canonical_codec_error"],
        "record_kinds": caps["record_kinds"],
        "format_unrepresentable_record_kinds": caps["format_unrepresentable_record_kinds"],
        "writer_unsupported_record_kinds": caps["writer_unsupported_record_kinds"],
        "reader_defect_record_kinds": caps["reader_defect_record_kinds"],
        "authoring_blocked": caps["authoring_blocked"],
        "authoring_blocker": caps["authoring_blocker"],
        "authoring_front_door": "scripts/apply_map_edits.py",
        "capability_model_owner": "scripts/apply_map_edits.py capabilities()",
        "capability_model_schema": caps["$schema"],
    }
    # Retained under its old name so no consumer breaks, but it now means ONLY
    # what it says: the format has no slot.  Reader defects and writer gaps have
    # their own lists above and are no longer smuggled into this one.
    block["unrepresentable_record_kinds"] = caps["format_unrepresentable_record_kinds"]
    return block


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--runtime-terrain", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = json.dumps(compile_spawns(args.source, args.runtime_terrain), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not args.output.is_file() or args.output.read_text() != rendered:
            print(f"FL4798_MAP_SPAWNS_STALE output={args.output}")
            return 1
        print(f"FL4798_MAP_SPAWNS_OK output={args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered)
    print(f"FL4798_MAP_SPAWNS_WRITTEN output={args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
