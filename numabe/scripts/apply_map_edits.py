#!/usr/bin/env python3
"""FL-4839 / FL-4833: the one write front door for the Godot map authoring surface.

This file INVENTS NO FORMAT AND NO SERIALIZER.  Every byte that leaves here is
produced by code that already existed:

  * ``docs/agent/cli-anything/a3d_edit.py`` ``read_a3d_sections`` /
    ``write_a3d_sections`` own the section split and the atomic
    temp-then-``os.replace`` commit, and preserve the document's own world
    format version rather than upgrading it.
  * ``addons/io_asciicker/scene/a3d_format.py`` owns every record struct
    (``A3DInstance``, ``A3DPlayerStart``, ``A3DEnemyGen``, ``A3DMinimapMarker``).
  * ``scripts/fl4588/a3d_document_codec.py`` owns the canonical-byte verdict
    (``parse_document`` / ``serialize_canonical`` / ``roundtrip_verdict``).
  * ``scripts/compile_fl4708_y8_tree_placements.py`` owns the terrain decode and
    the A3D<->runtime coordinate contract.

What this module adds is the part none of them had: a DECLARED CAPABILITY GATE,
and (FL-4834) the world-format migration that lets the gate ever open.

THE ELECTED CANONICAL Y8 MAP (FL-4834)
--------------------------------------
``godot_project/assets/maps/game_map_y8.a3d``.  Not by preference — by
measurement, restated so the next reader does not re-litigate it:

  * it is the file both consumers LOAD: colyseus_server/src/sim/terrain.ts:67
    and godot_project/scripts/map/A3DMapLoader.gd:15 / PlayableMap.gd:13 /
    Y8IsoViewer.gd:73, plus every gate, fixture and proof runner in the tree;
  * it is the RICHER document.  Against
    ``assets/a3d/game_map_y8_original_game_map.a3d`` it holds the SAME 154 item
    identities, the SAME 48 sprites and the SAME 26 enemy generators, and 1083
    mesh placements that include all 322 of the other file's — zero mesh
    placements exist only in the other file;
  * it is the one with upstream provenance: same length, patch count and
    instance count as msokalski's shipped y8 map, and byte-identical item
    records.

So ``assets/a3d/game_map_y8_original_game_map.a3d`` is a REDUCED DERIVATIVE, not
a second source of truth.  It keeps exactly one job — the material-palette
reference at addons/io_asciicker/scene/default_materials.py:104 — and stops being
an authoring source.  scripts/tests/test_fl4834_canonical_map_identity_chain.py
is the executable form of this election and fails if any of it stops being true.

THE GATE, AND WHY IT EXISTS
---------------------------
FL-4833: ``godot_project/assets/maps/game_map_y8.a3d`` — the map the game loads
— carries world format version 1.  A player start has no slot below v4, so
placing one in that file is UNREPRESENTABLE, not merely unimplemented.  An ITEM
is a different failure wearing the same word: the shipped v1 document already
holds 154 of them, and it is this repository's own PRODUCT READER that refuses
the layout ("item record before v3").  The CAPABILITY MODEL below keeps those
two apart, because migrating a document and repairing a reader are different
fixes owned by different files; do not re-merge them into one "v1 cannot hold
X" sentence.  FL-4834 recorded the worse half: the generated manifest declared
``runtime_consumption_blocked: false`` with an empty blocker string, so nothing
announced either gap at runtime.  The gap was silent.

``capabilities`` below makes it loud.  It reports, per record kind, which of the
four questions fails, and names FL-4833 in the refusal.  ``apply``
refuses the whole delta — it does not partially apply and it does not warn and
continue — when any op needs a version the target document does not have.  The
Godot edit surface reads the same JSON and greys the tool out before the
operator spends effort on a placement that would be silently dropped.

COORDINATE CONTRACT (verbatim from the FL-4798/FL-4708 products)
----------------------------------------------------------------
    A3D X/Y minus the A3DMapLoader centre offset -> Godot X/Z
    A3D Z / 16                                   -> Godot Y

Both directions are implemented here from that one statement, so a value read
out and written straight back is a no-op.

Usage
-----
    python3 scripts/apply_map_edits.py capabilities --map <a3d>
    python3 scripts/apply_map_edits.py read --map <a3d> [--output <json>]
    python3 scripts/apply_map_edits.py apply --map <a3d> --delta <json> \
        [--out <a3d>] [--verify-canonical]
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import math
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
A3D_EDIT_PATH = REPO_ROOT / "docs/agent/cli-anything/a3d_edit.py"
A3D_FORMAT_PATH = REPO_ROOT / "addons/io_asciicker/scene/a3d_format.py"
A3D_CODEC_PATH = REPO_ROOT / "scripts/fl4588/a3d_document_codec.py"
A3D_COMPAT_PATH = REPO_ROOT / "scripts/a3d/compatibility.py"
PLACEMENT_COMPILER_PATH = REPO_ROOT / "scripts/compile_fl4708_y8_tree_placements.py"
SPAWN_COMPILER_PATH = REPO_ROOT / "scripts/compile_fl4798_map_spawns.py"

TREE_COMPILER_PATH = REPO_ROOT / "scripts/compile_fl4708_y8_tree_placements.py"
CHUNK_COMPILER_PATH = REPO_ROOT / "scripts/compile_fl4789_chunk_catalog.py"
DECK_BAKER_PATH = REPO_ROOT / "scripts/bake_bridge_deck_slabs.py"
ACTOR_CATALOG_COMPILER_PATH = REPO_ROOT / "scripts/compile_fl4789_actor_atlas_catalog.py"
PLAY_LAUNCHER_PATH = REPO_ROOT / "scripts/launch_fl4359_manual_play.py"
XP_BAKER_PATH = REPO_ROOT / "scripts/bake_xp_sprite_for_runtime.py"
APPEARANCE_BUNDLE_PATH = (
    REPO_ROOT / "colyseus_server/runtime/visual_catalog/current/appearance_bundle.json"
)

DELTA_SCHEMA = "fl4839_map_edits.v1"
READ_SCHEMA = "fl4839_map_placements.v1"
CAPABILITY_SCHEMA = "fl4839_map_capabilities.v2"
HEIGHT_SCALE = 16.0
RACE_GATE_RADIUS_DEFAULT = 5.0
RACE_GATE_RADIUS_MIN = 0.5
RACE_GATE_RADIUS_MAX = 128.0
RACE_GATE_LABEL_RE = re.compile(
    r"^gate\.v1\|radius=([-+0-9.]+)\|label=(.*)$"
)

# FL-4833/FL-4834 CAPABILITY MODEL.  ONE table used to answer four different
# questions at once, and it answered all four wrong for `item`.  The four are
# now SEPARATE, because they have separate owners, separate evidence and
# separate fixes:
#
#   format_can_represent  Does a byte layout for this record exist at this
#                         world format version?  Owner: the FORMAT.  Evidence:
#                         a document at that version that physically holds one.
#   writer_can_author     Can this repository EMIT that layout and read the
#                         same identity back?  Owner:
#                         addons/io_asciicker/scene/a3d_format.py
#                         A3DInstance.write via
#                         docs/agent/cli-anything/a3d_edit.py
#                         write_a3d_sections.
#   reader_can_consume    Does this repository's PRODUCT reader accept a
#                         document containing that layout?  Owner:
#                         scripts/fl4588/a3d_document_codec.py:203, the twin of
#                         engine/world_serialization_a3d.cpp:783.
#   canonical_codec_accepts  Document level, not per kind: does the product
#                         reader accept THIS document as it stands?
#
# MEASURED 2026-10-01 after the FL-4834 migration on the elected canonical
# runtime map godot_project/assets/maps/game_map_y8.a3d (world format v4,
# sha256 14dc40f7409692b9a0b29cd2c4ecb42db834e57a56dd58a160443c2210428cbc).
# The pre-migration v1 document remains an explicit legacy negative control in
# the FL-4834/FL-4839 suites; the shipped Y8 product no longer depends on it.
#
#   item: format_can_represent, writer_can_author and reader_can_consume TRUE —
#         the 154 legacy identities were upgraded to the v3 bundle without
#         changing their definition, count, position or yaw.
#   player_start: format_can_represent TRUE at v4, but the canonical document
#         intentionally carries none; the migration did not invent a spawn.
FORMAT_MIN_VERSION = {
    "patch": 0,          # the fixed A3DPatch terrain section
    "mesh": 0,          # tag >= 0, legal in every version
    "sprite": 0,        # tag -1, legal in every version
    "item": 0,          # tag -2 with the pre-v3 two-int layout; v3+ adds two
                        # identity fields, so the KIND is representable at every
                        # version and only the LAYOUT changes at v3
    "player_start": 4,  # no slot below v4: genuinely unrepresentable
    "enemy_generator": 0,   # its own trailing section, version independent
    "marker": 0,            # its own trailing section, version independent
}
# The version at or above which the CURRENT (non-legacy) layout is written.
# Below it the writer must emit the legacy form or every following record
# desyncs; a3d_edit.write_a3d_sections passes the document's own version down
# for exactly this reason.
CURRENT_LAYOUT_MIN_VERSION = {
    "patch": 0,
    "mesh": 0, "sprite": 0, "item": 3, "player_start": 4,
    "enemy_generator": 0, "marker": 0,
}
WRITER_MIN_VERSION = dict(FORMAT_MIN_VERSION)
# What this repository's PRODUCT reader will accept.  `item: 3` here is the
# DEFECT, recorded as a reader fact rather than smuggled in as a format fact.
READER_MIN_VERSION = {
    "patch": 0,
    "mesh": 0,
    "sprite": 0,
    "item": 3,          # a3d_document_codec.py:203 "item record before v3"
    "player_start": 4,  # a3d_document_codec.py: `if version >= 4` gate
    "enemy_generator": 0,
    "marker": 0,
}
READER_DEFECT = {
    "item": (
        "FL-4834 READER DEFECT (not a format limit): scripts/fl4588/"
        "a3d_document_codec.py:203 and engine/world_serialization_a3d.cpp:783 "
        "reject tag -2 below v3 and fail the WHOLE document, so this repository "
        "cannot read back item records its own shipped map already contains. "
        "The fix is either the reader learning the legacy layout or the map "
        "being upgraded; it is NOT a statement about what v1 can hold."
    ),
}
OP_RECORD_KIND = {
    "sculpt_terrain": "patch",
    "paint_material": "patch",
    "set_material_ramp": "patch",
    "auto_mat_elev": "patch",
    "clear_mat_elev": "patch",
    "auto_texture": "patch",
    "add_mesh": "mesh",
    "add_item": "item",
    "add_sprite": "sprite",
    "set_player_start": "player_start",
    "add_enemy_generator": "enemy_generator",
    "add_marker": "marker",
    "delete_marker": "marker",
    "replace_race_markers": "marker",
    "replace_generic_markers": "marker",
    "delete_instance": "mesh",
    "delete_all_meshes": "mesh",
    "update_instance": "mesh",
    "update_enemy_generator": "enemy_generator",
    "delete_enemy_generator": "enemy_generator",
    "delete_all_enemy_generators": "enemy_generator",
    "reset_items": "item",
    "set_instance_story": "mesh",
    "patch_tile": "patch",
    "patch_diagonal": "patch",
    "bake_meshes": "patch",
    "translate_map": "patch",
}

# Frozen ranges, cited from scripts/fl4588/a3d_document_codec.py:238-250.  The
# editor must clamp at input time; this is the last line of defence, and it
# refuses rather than saturating so a bad delta is visible.
ENEMY_ALIVE_RANGE = (1, 7)
ENEMY_REVIVE_MAX = 30
ENEMY_EQUIPMENT_MAX = 15
MARKER_TYPE_MAX = 2
# The historical ASCIIID editor's placement commits create persistent instances with both
# INST_VISIBLE (0x1) and INST_USE_TREE (0x2). Keep that default for newly
# authored mesh, item and sprite records; an explicit flags payload remains
# the only way to author a different visibility/tree policy.
PERSISTENT_INSTANCE_FLAGS = 0x1 | 0x2
RACE_MARKER_RE = re.compile(r"^race_(gate|grid|path)_(\d+)$")

# FL-4839: this is the low-context, machine-readable contract for every
# operation accepted by apply_delta(). Keep it beside OP_RECORD_KIND so agents
# can discover the writer without reading this module or reverse-engineering
# MapEditSession.gd. The writer remains the final validator.
OPERATION_SPECS: dict[str, dict[str, Any]] = {
    "sculpt_terrain": {"required": {"world_xz": "number[2]", "delta_y": "number", "radius": "number"},
        "optional": {"mode": "raise|lower|flatten|blur|sharpen", "shape": "circle|gaussian|square|noise", "strength": "number", "height_min": "int", "height_max": "int", "probe_enabled": "bool", "probe_height": "int"}},
    "patch_tile": {"required": {"action": "create|delete", "patch_x": "int", "patch_y": "int"},
        "optional": {"initial_height": "int 0..65535"}},
    "translate_map": {"required": {"delta_z": "nonzero int -65535..65535"},
        "optional": {"water_limit": "bool", "water_height": "int 0..65535"}},
    "patch_diagonal": {"required": {"world_xz": "number[2]"},
        "optional": {"radius": "number", "shape": "circle|square"}},
    "paint_material": {"required": {"world_xz": "number[2]", "material_id": "int 0..255", "radius": "number"},
        "optional": {"shape": "circle|square", "elevation": "bool", "elevation_value": "0|1", "height_limit": "number|null", "below_limit": "bool", "slope_auto": "bool", "slope_threshold": "number"}},
    "set_material_ramp": {"required": {"material_id": "int 0..255", "elevation": "int 0..3", "fg_start": "rgb[3]", "fg_end": "rgb[3]", "bg_start": "rgb[3]", "bg_end": "rgb[3]"},
        "optional": {"glyph_start": "int 0..255", "glyph_end": "int 0..255"}},
    "auto_mat_elev": {"required": {}, "optional": {"mode": "0|1", "slope_threshold": "number", "height_threshold": "int 0..65535", "overwrite": "bool"}},
    "clear_mat_elev": {"required": {}, "optional": {}},
    "auto_texture": {"required": {}, "optional": {"mode": "0|1", "slope_threshold": "number", "height_min": "int 0..65535", "height_max": "int 0..65535", "material_id": "int 0..255", "overwrite": "bool"}},
    "bake_meshes": {"required": {}, "optional": {"bake_height": "bool", "bake_material": "bool", "material_id": "int 0..255"}},
    "add_mesh": {"required": {"mesh": ".akm basename", "world_xz": "number[2]"},
        "optional": {"godot_y": "number", "name": "string", "yaw_degrees": "number", "scale": "number", "scale_axes": "number[3]", "scale_log2": "number[3]", "scale_random": "number[3]", "rotate_loc_z_degrees": "number", "rotate_loc_z_random": "number", "rotate_xy_degrees": "number[2]", "rotate_xy_random": "number[2]", "rotate_align": "number 0..1", "terrain_normal": "number[3]", "height_offset": "number", "random_seed": "int", "authored_transform_column_major_f64": "number[16]", "flags": "int", "story_id": "int", "staged_instance_id": "negative int"}},
    "add_item": {"required": {"item_definition_id": "int 0..65535", "world_xz": "number[2]"},
        "optional": {"godot_y": "number", "visual_style_id": "int 0..65535", "presentation_kind_id": "int 0..65535", "item_count": "positive int", "yaw_degrees": "number", "flags": "int", "story_id": "int", "staged_instance_id": "negative int"}},
    "add_sprite": {"required": {"sprite": ".xp basename", "world_xz": "number[2]"},
        "optional": {"godot_y": "number", "height_offset": "number", "yaw_degrees": "number", "anim": "int", "frame": "int", "reps": "int[4]", "rand_yaw": "bool", "rand_anim": "bool", "rand_frame": "bool", "flags": "int", "story_id": "int", "staged_instance_id": "negative int"}},
    "add_enemy_generator": {"required": {"world_xz": "number[2]"},
        "optional": {"godot_y": "number", "alive_max": "int 1..7", "revive_min": "int 0..30", "revive_max": "int 0..30", "armor": "int 0..15", "helmet": "int 0..15", "shield": "int 0..15", "sword": "int 0..15", "crossbow": "int 0..15"}},
    "set_player_start": {"required": {"world_xz": "number[2]"}, "optional": {"godot_y": "number", "yaw": "number", "dir": "number"}},
    "add_marker": {"required": {"world_xz": "number[2]"}, "optional": {"name": "string", "label": "string", "marker_type": "int 0..2", "glyph": "1..8 chars", "fg": "int 0..255"}},
    "delete_marker": {"required": {"name": "race_gate_N|race_grid_N|race_path_N"}, "optional": {}},
    "replace_generic_markers": {"required": {"markers": "marker[]"}, "optional": {}},
    "replace_race_markers": {"required": {"markers": "race marker[]"}, "optional": {}},
    "delete_instance": {"required": {"source_instance_index": "int"}, "optional": {"staged_instance_id": "negative int"}},
    "delete_all_meshes": {"required": {}, "optional": {}},
    "update_instance": {"required": {"source_instance_index": "int", "world_xz": "number[2]"},
        "optional": {"staged_instance_id": "negative int", "godot_y": "number", "mesh": ".akm basename", "sprite": ".xp basename", "name": "string", "yaw_degrees": "number", "scale": "number", "scale_axes": "number[3]", "height_offset": "number", "anim": "int", "frame": "int", "reps": "int[4]", "item_definition_id": "int", "item_count": "positive int", "visual_style_id": "int", "presentation_kind_id": "int", "flags": "int", "story_id": "int"}},
    "reset_items": {"required": {}, "optional": {}},
    "set_instance_story": {"required": {"source_instance_index": "int", "story_id": "int"}, "optional": {"staged_instance_id": "negative int"}},
    "update_enemy_generator": {"required": {"source_generator_index": "int"},
        "optional": {"world_xz": "number[2]", "godot_y": "number", "alive_max": "int 1..7", "revive_min": "int 0..30", "revive_max": "int 0..30", "armor": "int 0..15", "helmet": "int 0..15", "shield": "int 0..15", "sword": "int 0..15", "crossbow": "int 0..15"}},
    "delete_enemy_generator": {"required": {"source_generator_index": "int"}, "optional": {}},
    "delete_all_enemy_generators": {"required": {}, "optional": {}},
}


def _race_marker_name(name: str) -> bool:
    """Return whether *name* is one of the bounded race marker identities."""
    return RACE_MARKER_RE.fullmatch(name) is not None


def _decode_race_gate_label(label: str) -> tuple[str, float | None]:
    """Return the visible label and optional authored gate radius."""
    match = RACE_GATE_LABEL_RE.fullmatch(label)
    if match is None:
        return label, None
    try:
        radius = float(match.group(1))
    except ValueError:
        return label, None
    return urllib.parse.unquote(match.group(2)), radius


def _encode_race_gate_label(label: str, radius: float) -> str:
    visible, _prior = _decode_race_gate_label(label)
    return "gate.v1|radius=%.3f|label=%s" % (
        radius, urllib.parse.quote(visible, safe="-_.~ "),
    )


def _race_marker_payload(op: dict[str, Any]) -> dict[str, Any]:
    """Validate one race marker payload without inventing a second format."""
    name = str(op.get("name", ""))
    _require(_race_marker_name(name),
             "race marker name must match race_gate_N, race_grid_N or race_path_N")
    world_xz = op.get("world_xz")
    _require(isinstance(world_xz, (list, tuple)) and len(world_xz) == 2,
             "race marker world_xz must contain exactly two coordinates")
    try:
        x, z = float(world_xz[0]), float(world_xz[1])
        marker_type = int(op.get("marker_type", 0))
        fg = int(op.get("fg", 226))
    except (TypeError, ValueError, OverflowError) as exc:
        raise MapEditError("race marker numeric fields must be finite numbers") from exc
    _require(math.isfinite(x) and math.isfinite(z),
             "race marker coordinates must be finite")
    _require(0 <= marker_type <= MARKER_TYPE_MAX,
             f"marker_type must be 0..{MARKER_TYPE_MAX}")
    label = str(op.get("label", name))
    visible_label, encoded_radius = _decode_race_gate_label(label)
    radius: float | None = encoded_radius
    if name.startswith("race_gate_") and "radius" in op:
        try:
            radius = float(op["radius"])
        except (TypeError, ValueError, OverflowError) as exc:
            raise MapEditError("race gate radius must be finite") from exc
    if radius is not None:
        _require(math.isfinite(radius)
                 and RACE_GATE_RADIUS_MIN <= radius <= RACE_GATE_RADIUS_MAX,
                 f"race gate radius must be within {RACE_GATE_RADIUS_MIN}..{RACE_GATE_RADIUS_MAX}")
        label = _encode_race_gate_label(visible_label, radius)
    return {
        "name": name,
        "label": label,
        "world_xz": [x, z],
        "marker_type": marker_type,
        "glyph": str(op.get("glyph", "X")),
        "fg": fg,
        **({"radius": radius} if radius is not None else {}),
    }


def _sculpt_terrain_payload(op: dict[str, Any]) -> dict[str, Any]:
    """Validate one terrain brush stroke in the existing A3D heightfield.

    The operation carries runtime X/Z plus a Godot-height delta and brush
    radius; the writer converts those through the established 8-unit patch /
    2-unit vertex grid.  No terrain sidecar or second heightfield is created.
    """
    world_xz = op.get("world_xz")
    _require(isinstance(world_xz, (list, tuple)) and len(world_xz) == 2,
             "terrain sculpt world_xz must contain exactly two coordinates")
    try:
        x, z = float(world_xz[0]), float(world_xz[1])
        delta_y = float(op.get("delta_y", 0.0))
        radius = float(op["radius"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise MapEditError("terrain sculpt numeric fields must be finite numbers") from exc
    _require(math.isfinite(x) and math.isfinite(z)
             and math.isfinite(delta_y) and math.isfinite(radius),
             "terrain sculpt numeric fields must be finite numbers")
    _require(radius > 0.0 and radius <= 128.0,
             "terrain sculpt radius must be > 0 and <= 128 world units")
    mode_value = op.get("mode")
    mode = ("raise" if delta_y >= 0.0 else "lower") if mode_value is None \
        else str(mode_value).lower()
    if mode == "smooth":
        mode = "blur"
    _require(mode in {"raise", "lower", "flatten", "blur", "sharpen"},
             "terrain sculpt mode must be raise, lower, flatten or smooth")
    # The historical ASCIIID editor carries ascent/descent in brush alpha's sign. The
    # reviewed delta schema also exposes an explicit mode for editor callers;
    # normalize that mode here so direct CLI deltas and the Godot viewer share
    # exactly the same raise/lower semantics before quantization is applied.
    steps = 0
    if mode in {"raise", "lower"}:
        try:
            steps = int(round(delta_y * HEIGHT_SCALE))
        except (OverflowError, ValueError) as exc:
            raise MapEditError("terrain sculpt delta_y is outside the signed 16-bit brush range") from exc
        if mode == "raise":
            steps = abs(steps)
        else:
            steps = -abs(steps)
        delta_y = steps / HEIGHT_SCALE
        _require(steps != 0, "terrain sculpt delta_y must move at least one 1/16 height step")
    else:
        _require(math.isfinite(float(op.get("strength", 1.0)))
                 and float(op.get("strength", 1.0)) > 0.0
                 and float(op.get("strength", 1.0)) <= 1.0,
                 "flatten/smooth strength must be > 0 and <= 1")
        delta_y = 0.0
    if mode == "flatten":
        _require(bool(op.get("probe_enabled", False)),
                 "flatten requires a sampled height target (probe_enabled)")
    _require(abs(steps) <= 32767,
             "terrain sculpt delta_y is outside the signed 16-bit brush range")
    # `circle` is the pre-tool-shape delta contract; retain it for old
    # sessions while exposing the native Gaussian/Square/Noise choices.
    shape = str(op.get("shape", "circle")).lower()
    _require(shape in {"circle", "gaussian", "square", "noise"},
             "terrain sculpt shape must be circle, gaussian, square or noise")
    height_min = int(op.get("height_min", 0))
    height_max = int(op.get("height_max", 65535))
    _require(0 <= height_min <= height_max <= 65535,
             "terrain height limits must be within 0..65535")
    probe_height = int(op.get("probe_height", 0))
    _require(0 <= probe_height <= 65535,
             "terrain sampled height threshold must be within 0..65535")
    return {"world_xz": [x, z], "delta_y": delta_y, "radius": radius,
            "height_delta_steps": steps, "shape": shape, "mode": mode,
            "strength": float(op.get("strength", 1.0)),
            "height_min": height_min, "height_max": height_max,
            "probe_enabled": bool(op.get("probe_enabled", False)),
            "probe_height": probe_height,
            "diagonal": bool(op.get("diagonal", False))}


def _translate_map_payload(op: dict[str, Any]) -> dict[str, Any]:
    """Validate the historical ASCIIID editor's TranslateMap integer contract."""
    delta_value = op.get("delta_z")
    water_value = op.get("water_height", 0)
    if isinstance(delta_value, bool) or not isinstance(delta_value, int):
        raise MapEditError("translate_map delta_z must be an integer")
    if isinstance(water_value, bool) or not isinstance(water_value, int):
        raise MapEditError("translate_map water_height must be an integer")
    delta_z = int(delta_value)
    water_height = int(water_value)
    _require(-65535 <= delta_z <= 65535,
             "translate_map delta_z must be within the signed height-bank span")
    _require(0 <= water_height <= 65535,
             "translate_map water_height must be 0..65535 native steps")
    water_limit = op.get("water_limit", False)
    if not isinstance(water_limit, bool):
        raise MapEditError("translate_map water_limit must be boolean")
    return {"delta_z": delta_z, "water_limit": water_limit,
            "water_height": water_height}


def _auto_mat_elev_payload(op: dict[str, Any]) -> dict[str, Any]:
    """Validate the native whole-map AutoMatElev batch parameters."""
    try:
        mode = int(op.get("mode", 0))
        slope_threshold = float(op.get("slope_threshold", 64.0))
        height_threshold = int(op.get("height_threshold", 0xA000))
    except (TypeError, ValueError, OverflowError) as exc:
        raise MapEditError("auto_mat_elev numeric fields are invalid") from exc
    _require(mode in {0, 1}, "auto_mat_elev mode must be 0 (slope) or 1 (height)")
    _require(math.isfinite(slope_threshold) and slope_threshold >= 0.0,
             "auto_mat_elev slope_threshold must be finite and non-negative")
    _require(0 <= height_threshold <= 65535,
             "auto_mat_elev height_threshold must be 0..65535")
    overwrite = op.get("overwrite", True)
    if not isinstance(overwrite, bool):
        raise MapEditError("auto_mat_elev overwrite must be boolean")
    return {"mode": mode, "slope_threshold": slope_threshold,
            "height_threshold": height_threshold, "overwrite": overwrite}


def _auto_texture_payload(op: dict[str, Any]) -> dict[str, Any]:
    """Validate the native whole-map AutoTexture batch parameters."""
    try:
        mode = int(op.get("mode", 0))
        slope_threshold = float(op.get("slope_threshold", 64.0))
        height_min = int(op.get("height_min", 0))
        height_max = int(op.get("height_max", 0xA000))
        material_id = int(op.get("material_id", 0))
    except (TypeError, ValueError, OverflowError) as exc:
        raise MapEditError("auto_texture numeric fields are invalid") from exc
    _require(mode in {0, 1}, "auto_texture mode must be 0 (slope) or 1 (height)")
    _require(math.isfinite(slope_threshold) and slope_threshold >= 0.0,
             "auto_texture slope_threshold must be finite and non-negative")
    _require(0 <= height_min <= height_max <= 65535,
             "auto_texture height range must be within 0..65535")
    _require(0 <= material_id <= 255, "auto_texture material_id must be 0..255")
    overwrite = op.get("overwrite", True)
    if not isinstance(overwrite, bool):
        raise MapEditError("auto_texture overwrite must be boolean")
    # ASCIIID's current AutoTexture implementation retains this flag in its
    # command context but does not gate the subsequent write when false. Keep
    # the field for command parity and intentionally preserve that behavior.
    return {"mode": mode, "slope_threshold": slope_threshold,
            "height_min": height_min, "height_max": height_max,
            "material_id": material_id, "overwrite": overwrite}


def _paint_material_payload(op: dict[str, Any]) -> dict[str, Any]:
    """Validate a native MAT-id brush stroke over patch visual cells.

    The C++ editor writes the low byte of each uint16 visual cell as the
    material id and preserves shade/elevation bits.  This operation follows
    that wire contract; it never creates a Godot-side material table.
    """
    world_xz = op.get("world_xz")
    _require(isinstance(world_xz, (list, tuple)) and len(world_xz) == 2,
             "material brush world_xz must contain exactly two coordinates")
    try:
        x, z = float(world_xz[0]), float(world_xz[1])
        material_id = int(op["material_id"])
        radius = float(op["radius"])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise MapEditError("material brush numeric fields must be finite numbers") from exc
    _require(math.isfinite(x) and math.isfinite(z) and math.isfinite(radius),
             "material brush numeric fields must be finite numbers")
    _require(0 <= material_id <= 255, "material_id must be 0..255")
    _require(radius > 0.0 and radius <= 128.0,
             "material brush radius must be > 0 and <= 128 world units")
    shape = str(op.get("shape", "circle")).lower()
    _require(shape in {"circle", "square"},
             "material brush shape must be circle or square")
    height_limit = op.get("height_limit")
    if height_limit is not None:
        height_limit = float(height_limit)
        _require(math.isfinite(height_limit), "material height_limit must be finite")
    slope_threshold = float(op.get("slope_threshold", 1.0))
    _require(math.isfinite(slope_threshold) and slope_threshold >= 0.0,
             "material slope_threshold must be finite and non-negative")
    return {"world_xz": [x, z], "material_id": material_id,
            "radius": radius, "shape": shape,
            "elevation": bool(op.get("elevation", False)),
            "height_limit": height_limit,
            "below_limit": bool(op.get("below_limit", False)),
            "slope_auto": bool(op.get("slope_auto", False)),
            "slope_threshold": slope_threshold}


# FL-4839: native material-grid appearance ramp payload, not a new serializer.
def _material_ramp_payload(op: dict[str, Any]) -> dict[str, Any]:
    """Validate one native 16-shade MatCell row interpolation.

    ASCIIID's material-grid drag interpolates only foreground/background and
    chooses the nearer endpoint glyph.  This operation carries those two
    endpoints; existing flags and glyphs remain untouched when glyph endpoints
    are omitted.
    """
    def _bounded_int(value: Any, minimum: int, maximum: int, label: str) -> int:
        if isinstance(value, bool):
            raise MapEditError(f"{label} must be an integer")
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise MapEditError(f"{label} must be an integer") from exc
        _require((isinstance(value, int) or isinstance(value, str)
                  or (isinstance(value, float) and math.isfinite(value)
                      and value == parsed)),
                 f"{label} must be an integer")
        _require(minimum <= parsed <= maximum,
                 f"{label} must be {minimum}..{maximum}")
        return parsed

    def _rgb(value: Any, label: str) -> list[int]:
        _require(isinstance(value, (list, tuple)) and len(value) == 3,
                 f"{label} must contain exactly three channels")
        return [_bounded_int(channel, 0, 255, f"{label}[{index}]")
                for index, channel in enumerate(value)]

    material_id = _bounded_int(op.get("material_id", -1), 0, 255,
                               "material ramp material_id")
    elevation = op.get("elevation", op.get("ramp", 0))
    elevation = _bounded_int(elevation, 0, 3, "material ramp elevation")
    fg_start = _rgb(op.get("fg_start"), "material ramp fg_start")
    fg_end = _rgb(op.get("fg_end"), "material ramp fg_end")
    bg_start = _rgb(op.get("bg_start"), "material ramp bg_start")
    bg_end = _rgb(op.get("bg_end"), "material ramp bg_end")
    glyph_start_value = op.get("glyph_start")
    glyph_end_value = op.get("glyph_end")
    _require((glyph_start_value is None) == (glyph_end_value is None),
             "material ramp glyph_start and glyph_end must be supplied together")
    glyph_start = None if glyph_start_value is None else _bounded_int(
        glyph_start_value, 0, 255, "material ramp glyph_start")
    glyph_end = None if glyph_end_value is None else _bounded_int(
        glyph_end_value, 0, 255, "material ramp glyph_end")
    return {"material_id": material_id, "elevation": elevation,
            "fg_start": fg_start, "fg_end": fg_end,
            "bg_start": bg_start, "bg_end": bg_end,
            "glyph_start": glyph_start, "glyph_end": glyph_end}


class MapEditError(Exception):
    """A refusal.  Nothing is written when this is raised."""


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _a3d_edit() -> Any:
    return _load("fl4839_a3d_edit", A3D_EDIT_PATH)


def _a3d_format() -> Any:
    return _load("a3d_format", A3D_FORMAT_PATH)


def _codec() -> Any:
    return _load("fl4839_a3d_codec", A3D_CODEC_PATH)


def _compat() -> Any:
    return _load("fl4512_a3d_compat", A3D_COMPAT_PATH)


def _placement_compiler() -> Any:
    return _load("fl4839_placement_compiler", PLACEMENT_COMPILER_PATH)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(path: Path) -> str:
    """Repository-relative when possible, absolute otherwise (proof scratch)."""
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _world_format_version(path: Path) -> int:
    """Positive world format version of one document, read from the file only."""
    data = path.read_bytes()
    num_patches = struct.unpack_from("<I", data, 8)[0]
    offset = 16 + num_patches * 188 + 256 * 512
    (first,) = struct.unpack_from("<i", data, offset)
    return -first if first < 0 else 0


# ---------------------------------------------------------------------------
# Coordinate contract, both directions.
# ---------------------------------------------------------------------------

def _centre(runtime_terrain: Path) -> tuple[float, float]:
    terrain = _placement_compiler()._decode_terrain(runtime_terrain)
    return tuple(terrain["center_offset"])  # type: ignore[return-value]


def a3d_to_runtime(pos: list[float], centre: tuple[float, float]) -> dict[str, Any]:
    return {
        "world_xz": [float(pos[0]) - centre[0], float(pos[1]) - centre[1]],
        "godot_y": float(pos[2]) / HEIGHT_SCALE,
    }


def runtime_to_a3d(world_xz: list[float], godot_y: float,
                   centre: tuple[float, float]) -> list[float]:
    return [
        float(world_xz[0]) + centre[0],
        float(world_xz[1]) + centre[1],
        float(godot_y) * HEIGHT_SCALE,
    ]


def _mat3_mul(a: list[float], b: list[float]) -> list[float]:
    """Multiply two column-major 3x3 matrices, as engine::Product does."""
    out = [0.0] * 9
    for column in range(3):
        bx, by, bz = b[column * 3:column * 3 + 3]
        out[column * 3 + 0] = a[0] * bx + a[3] * by + a[6] * bz
        out[column * 3 + 1] = a[1] * bx + a[4] * by + a[7] * bz
        out[column * 3 + 2] = a[2] * bx + a[5] * by + a[8] * bz
    return out


def _axis_angle(axis: tuple[float, float, float], angle: float) -> list[float]:
    length = math.sqrt(sum(value * value for value in axis))
    if length <= 1.0e-12 or abs(angle) <= 1.0e-12:
        return [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    x, y, z = (value / length for value in axis)
    co, si, one = math.cos(angle), math.sin(angle), 1.0 - math.cos(angle)
    return [
        co + x * x * one, y * x * one + z * si, z * x * one - y * si,
        x * y * one - z * si, co + y * y * one, z * y * one + x * si,
        x * z * one + y * si, y * z * one - x * si, co + z * z * one,
    ]


def _native_random(seed: int, slot: int) -> float:
    digest = hashlib.sha256(f"asciiid-native-random:{seed}:{slot}".encode("ascii")).digest()
    return int.from_bytes(digest[:8], "little") / float(1 << 64)


def _mesh_transform(a3d_pos: list[float], yaw: float = 0.0, scale: float = 1.0,
                    rotate_xy: list[float] | tuple[float, float] | None = None,
                    rotate_align: float = 0.0,
                    terrain_normal: list[float] | tuple[float, float, float] | None = None,
                    height_offset: float = 0.0,
                    *, scale_log2: list[float] | tuple[float, float, float] | None = None,
                    scale_random: list[float] | tuple[float, float, float] | None = None,
                    rotate_loc_z_degrees: float | None = None,
                    rotate_loc_z_random: float = 0.0,
                    rotate_xy_degrees: list[float] | tuple[float, float] | None = None,
                    rotate_xy_random: list[float] | tuple[float, float] | None = None,
                    random_seed: int = 0) -> list[float]:
    """Build the native MeshPrefs matrix, including per-axis randomness.

    This is the placement math in asciiid.cpp: three log2 scale channels,
    local-Z degrees, an axis-angle RotateXY vector in 1/180 turns, then the
    optional terrain-normal alignment.  The final Z row is multiplied by 16
    because A3D stores its native up axis in sixteenths.
    """
    if scale_log2 is None:
        legacy_scale = float(scale)
        _require(math.isfinite(legacy_scale) and 0.0 < legacy_scale <= 100.0,
                 "mesh transform scale must be finite and in 0..100")
        log_scale = [math.log(legacy_scale, 2.0)] * 3
    else:
        log_scale = [float(value) for value in scale_log2]
        _require(len(log_scale) == 3 and all(math.isfinite(value) for value in log_scale),
                 "mesh scale_log2 must contain three finite values")
    random_scale = [0.0, 0.0, 0.0] if scale_random is None else [float(value) for value in scale_random]
    _require(len(random_scale) == 3 and all(math.isfinite(value) and value >= 0.0 for value in random_scale),
             "mesh scale_random must contain three finite non-negative values")
    axes = [2.0 ** (log_scale[index] + 2.0 * random_scale[index]
                    * (_native_random(random_seed, index) - 0.5)) for index in range(3)]
    _require(all(math.isfinite(value) and value > 0.0 and value <= 10000.0 for value in axes),
             "mesh native scale channels must be finite and positive")

    if rotate_loc_z_degrees is None:
        z_degrees = math.degrees(float(yaw))
    else:
        z_degrees = float(rotate_loc_z_degrees)
    z_random = float(rotate_loc_z_random)
    _require(math.isfinite(z_degrees) and math.isfinite(z_random) and z_random >= 0.0,
             "mesh local-Z rotation must be finite")
    z_angle = math.radians(z_degrees + 360.0 * z_random * (_native_random(random_seed, 3) - 0.5))
    ztm = _axis_angle((0.0, 0.0, 1.0), z_angle)

    if rotate_xy_degrees is None:
        # Legacy callers supplied radians around X/Y.  Keep that input only as
        # a migration path; newly authored payloads use native degree fields.
        legacy = [0.0, 0.0] if rotate_xy is None else [float(value) for value in rotate_xy]
        xy = [value / math.pi for value in legacy]
    else:
        xy = [float(value) / 180.0 for value in rotate_xy_degrees]
    xy_random = [0.0, 0.0] if rotate_xy_random is None else [float(value) for value in rotate_xy_random]
    _require(len(xy) == 2 and len(xy_random) == 2
             and all(math.isfinite(value) for value in xy + xy_random)
             and all(value >= 0.0 for value in xy_random),
             "mesh RotateXY channels must be finite")
    xy = [xy[index] + 2.0 * xy_random[index] * (_native_random(random_seed, 4 + index) - 0.5)
          for index in range(2)]
    xy_angle = min(1.0, math.hypot(xy[0], xy[1])) * math.pi
    rot_xy = _axis_angle((xy[0], xy[1], 0.0), xy_angle)

    align = float(rotate_align)
    _require(math.isfinite(align) and 0.0 <= align <= 1.0,
             "mesh alignment must be in 0..1")
    if terrain_normal is None:
        normal = (0.0, 0.0, 1.0)
    else:
        # Godot preview normals are X/Y/Z; native is X/Y/Z with Z-up.
        normal = (float(terrain_normal[0]), float(terrain_normal[2]), float(terrain_normal[1]))
    n_length = math.sqrt(sum(value * value for value in normal))
    normal = tuple(value / n_length for value in normal) if n_length > 1.0e-12 else (0.0, 0.0, 1.0)
    rot = rot_xy
    direction = (rot[6], rot[7], rot[8])
    cross = (direction[1] * normal[2] - direction[2] * normal[1],
             direction[2] * normal[0] - direction[0] * normal[2],
             direction[0] * normal[1] - direction[1] * normal[0])
    cross_len = math.sqrt(sum(value * value for value in cross))
    align_angle = math.asin(max(-1.0, min(1.0, cross_len)))
    if cross_len > 1.0e-12 and align > 0.0:
        rot = _mat3_mul(_axis_angle(cross, align_angle * align), rot)
    rot = _mat3_mul(rot, ztm)
    basis = [rot[column * 3 + row] * axes[column] for column in range(3) for row in range(3)]
    for column in range(3):
        basis[column * 3 + 2] *= HEIGHT_SCALE
    return [
        basis[0], basis[1], basis[2], 0.0,
        basis[3], basis[4], basis[5], 0.0,
        basis[6], basis[7], basis[8], 0.0,
        float(a3d_pos[0]), float(a3d_pos[1]),
        float(a3d_pos[2]) + float(height_offset) * HEIGHT_SCALE, 1.0,
    ]


def _mesh_scale(tm: list[float]) -> float:
    return math.sqrt(float(tm[0]) ** 2 + float(tm[1]) ** 2
                     + (float(tm[2]) / HEIGHT_SCALE) ** 2)


def _mesh_scale_axes(tm: list[float]) -> list[float]:
    return [math.sqrt(float(tm[column]) ** 2 + float(tm[column + 1]) ** 2
                      + (float(tm[column + 2]) / HEIGHT_SCALE) ** 2)
            for column in (0, 4, 8)]


def _placement_mesh_transform(op: dict[str, Any], pos: list[float],
                              previous: list[float] | None = None) -> list[float]:
    """Keep authored tilt, reflection and shear when moving/duplicating a mesh.

    Yaw and scale edits are deltas on the existing basis, not a replacement
    with a yaw-only matrix. The full A3D matrix stays the placement authority.
    """
    values = op.get("authored_transform_column_major_f64", previous)
    if isinstance(values, (list, tuple)) and not values:
        values = previous
    if values is None:
        scale_log2 = op.get("scale_log2")
        if scale_log2 is None and "scale_axes" in op:
            scale_axes = [float(value) for value in op["scale_axes"]]
            _require(len(scale_axes) == 3 and all(math.isfinite(value) and value > 0.0
                                                  for value in scale_axes),
                     "mesh scale_axes must contain three finite positive values")
            scale_log2 = [math.log(value, 2.0) for value in scale_axes]
        return _mesh_transform(
            pos, float(op.get("yaw", 0.0)), float(op.get("scale", 1.0)),
            op.get("rotate_xy"), float(op.get("rotate_align", 0.0)),
            op.get("terrain_normal"), float(op.get("height_offset", 0.0)),
            scale_log2=scale_log2, scale_random=op.get("scale_random"),
            rotate_loc_z_degrees=op.get("rotate_loc_z_degrees", op.get("yaw_degrees")),
            rotate_loc_z_random=float(op.get("rotate_loc_z_random", 0.0)),
            rotate_xy_degrees=op.get("rotate_xy_degrees"),
            rotate_xy_random=op.get("rotate_xy_random"),
            random_seed=int(op.get("random_seed", 0)),
        )
    _require(isinstance(values, (list, tuple)) and len(values) == 16,
             "authored mesh transform must contain 16 finite values")
    tm = [float(value) for value in values]
    _require(all(math.isfinite(value) for value in tm), "authored mesh transform must be finite")
    _require(all(math.isfinite(float(value)) for value in pos), "mesh position must be finite")
    yaw = (math.radians(float(op["yaw_degrees"])) if "yaw_degrees" in op
           else float(op.get("yaw", math.atan2(tm[1], tm[0]))))
    original_scale = _mesh_scale(tm)
    scale = float(op.get("scale", original_scale))
    _require(math.isfinite(yaw) and math.isfinite(scale) and 0.0 < scale <= 100.0
             and original_scale > 1e-12, "mesh transform must have finite yaw and positive scale <=100")
    angle = yaw - math.atan2(tm[1], tm[0])
    co, si = math.cos(angle), math.sin(angle)
    ratio = scale / original_scale
    if angle != 0.0 or ratio != 1.0:
        for column in (0, 4, 8):
            x, y = tm[column], tm[column + 1]
            tm[column] = (co * x - si * y) * ratio
            tm[column + 1] = (si * x + co * y) * ratio
            tm[column + 2] *= ratio
    tm[12:15] = [float(value) for value in pos]
    # Staged editor rows materialize their preview basis as a matrix but keep
    # height_offset as an explicit authoring field.  Apply that translation
    # in the same native 1/16-height units as the non-matrix path.
    tm[14] += float(op.get("height_offset", 0.0)) * HEIGHT_SCALE
    return tm


def _sprite_animation_lengths(sprite: str) -> list[int]:
    # Share the production XP metadata reader; never guess animation/frame
    # counts from another sprite or impose arbitrary 4x32 atlas dimensions.
    _require(sprite.lower().endswith(".xp") and "/" not in sprite and "\\" not in sprite,
             "sprite must be an .xp basename")
    path = next((root / sprite for root in (REPO_ROOT / "godot_project/assets/sprites",
                                           REPO_ROOT / "assets/sprites")
                 if (root / sprite).is_file()), None)
    _require(path is not None, f"sprite asset does not exist: {sprite}")
    xp_module = _load("map_editor_xp_core", REPO_ROOT / "scripts/pipeline/xp_core.py")
    with contextlib.redirect_stdout(io.StringIO()):
        metadata = xp_module.XPFile(str(path)).get_metadata()
    _require(isinstance(metadata, dict), f"sprite has no metadata layer: {sprite}")
    return [int(value) for value in metadata.get("anims", [])] or [1]


def _sprite_animation_frame(sprite: str, anim: int, frame: int,
                            seed: int | None = None, random_anim: bool = False,
                            random_frame: bool = False) -> tuple[int, int]:
    lengths = _sprite_animation_lengths(sprite)
    if random_anim:
        anim = int(seed or 0) % len(lengths)
    _require(0 <= anim < len(lengths), f"sprite animation index outside authored atlas: {sprite}")
    frame = (int(seed or 0) // 17) % lengths[anim] if random_frame else frame % lengths[anim]
    return anim, frame


def _sprite_repetitions(value: Any) -> list[int]:
    """Validate SpritePrefs.t with the native editor's bounded integer range."""
    _require(isinstance(value, (list, tuple)) and len(value) == 4,
             "sprite reps must contain four integers")
    result: list[int] = []
    for entry in value:
        try:
            number = float(entry)
        except (TypeError, ValueError):
            raise MapEditError("sprite reps must be integers in 0..50") from None
        _require(math.isfinite(number) and number.is_integer() and 0 <= int(number) <= 50,
                 "sprite reps must be integers in 0..50")
        result.append(int(number))
    return result


# ---------------------------------------------------------------------------
# capabilities — the declaration that FL-4834 found missing.
# ---------------------------------------------------------------------------

def capabilities(map_path: Path) -> dict[str, Any]:
    map_path = map_path.resolve()
    version = _world_format_version(map_path)
    codec = _codec()
    parse_ok, parse_error = True, ""
    try:
        codec.parse_document(map_path.read_bytes())
    except Exception as exc:  # the canonical codec is the oracle, not us
        parse_ok, parse_error = False, f"{type(exc).__name__}: {exc}"

    # Counted from the document, never assumed.  A kind with records present is
    # PROOF that the format can represent it at this version, which is the
    # evidence the old single-table answer threw away.
    present = record_counts(map_path)

    kinds: dict[str, Any] = {}
    for kind in sorted(FORMAT_MIN_VERSION):
        fmt_min = FORMAT_MIN_VERSION[kind]
        wr_min = WRITER_MIN_VERSION[kind]
        rd_min = READER_MIN_VERSION[kind]
        count = int(present.get(kind, 0))

        format_can_represent = version >= fmt_min
        format_evidence = (
            f"measured: {count} {kind} record(s) physically present in this "
            f"v{version} document"
            if count > 0 else
            f"derived: the format defines a {kind} layout from v{fmt_min}"
        )
        # A kind already in the file cannot be unrepresentable in it.  If the
        # table ever says otherwise the table is wrong, and saying so here is
        # cheaper than shipping the contradiction.
        if count > 0 and not format_can_represent:
            raise MapEditError(
                f"CAPABILITY_TABLE_CONTRADICTS_DOCUMENT: {count} {kind} records "
                f"exist in a v{version} document that FORMAT_MIN_VERSION calls "
                f"impossible below v{fmt_min}"
            )

        writer_can_author = version >= wr_min
        reader_can_consume = version >= rd_min

        blockers: list[str] = []
        if not format_can_represent:
            blockers.append(
                f"FL-4833 FORMAT: {map_path.name} declares world format version "
                f"{version} and the format has no {kind} slot below v{fmt_min}. "
                f"The record is UNREPRESENTABLE in this document."
            )
        if not writer_can_author:
            blockers.append(
                f"FL-4834 WRITER: this repository cannot emit a {kind} record "
                f"into a v{version} document."
            )
        if not reader_can_consume and rd_min > fmt_min:
            blockers.append(READER_DEFECT.get(kind, (
                f"FL-4834 READER: this repository's product reader refuses a "
                f"{kind} record below v{rd_min}."
            )))
        if not parse_ok and not blockers:
            blockers.append(
                f"FL-4833: {map_path.name} is refused by the canonical codec "
                f"({parse_error}), so no record may be written to it."
            )

        kinds[kind] = {
            # FOUR SEPARATE PROPERTIES.  They are recorded separately because
            # they are separately true, separately owned and separately fixed.
            "format_can_represent": format_can_represent,
            "format_min_version": fmt_min,
            "format_layout": ("legacy_pre_v3" if version < CURRENT_LAYOUT_MIN_VERSION[kind]
                              else "current"),
            "format_current_layout_min_version": CURRENT_LAYOUT_MIN_VERSION[kind],
            "format_evidence": format_evidence,
            "records_present_in_document": count,
            "writer_can_author": writer_can_author,
            "writer_min_version": wr_min,
            "writer_owner": (
                "addons/io_asciicker/scene/a3d_format.py A3DInstance.write via "
                "docs/agent/cli-anything/a3d_edit.py write_a3d_sections"
            ),
            "reader_can_consume": reader_can_consume,
            "reader_min_version": rd_min,
            "reader_owner": (
                "scripts/fl4588/a3d_document_codec.py parse_document "
                "(twin of engine/world_serialization_a3d.cpp:783)"
            ),
            # A reader that is STRICTER than the format is a defect of ours.
            # A reader that merely matches the format is not: `player_start`
            # below v4 has no slot to read, so refusing it is correct.
            "reader_stricter_than_format": rd_min > fmt_min,
            "reader_defect": (READER_DEFECT.get(kind, "")
                              if (not reader_can_consume and rd_min > fmt_min) else ""),
            # The conjunction the authoring gate acts on.  It is DERIVED, so a
            # reader can always see WHICH of the four is the actual obstacle.
            "authorable": bool(format_can_represent and writer_can_author
                               and reader_can_consume and parse_ok),
            "blocker": " | ".join(blockers),
        }

    blocked = [k for k, v in kinds.items() if not v["authorable"]]
    unrepresentable = [k for k, v in kinds.items() if not v["format_can_represent"]]
    reader_defects = [k for k, v in kinds.items()
                      if not v["reader_can_consume"] and v["reader_stricter_than_format"]]
    writer_gaps = [k for k, v in kinds.items() if not v["writer_can_author"]]
    return {
        "$schema": CAPABILITY_SCHEMA,
        "map_path": _rel(map_path),
        "map_sha256": _sha256(map_path),
        "world_format_version": version,
        "canonical_codec_accepts": parse_ok,
        "canonical_codec_error": parse_error,
        "record_kinds": kinds,
        # The four roll-ups, kept apart for the same reason as the per-kind
        # fields: "unrepresentable" used to absorb reader defects and writer
        # gaps, which is how "v1 cannot contain an item" survived next to a
        # shipped v1 map holding 154 of them.
        "format_unrepresentable_record_kinds": sorted(unrepresentable),
        "writer_unsupported_record_kinds": sorted(writer_gaps),
        "reader_defect_record_kinds": sorted(reader_defects),
        # FL-4834 recorded that the generated manifest carried
        # runtime_consumption_blocked=false with an EMPTY blocker string, so
        # nothing declared the format gap at runtime.  These two fields are the
        # declaration; a consumer that reads them cannot stay silent.
        "authoring_blocked": bool(blocked),
        "authoring_blocker": (
            "" if not blocked else
            "FL-4833/FL-4834: world format v%d — unrepresentable: [%s]; "
            "writer cannot emit: [%s]; reader defect: [%s]"
            % (version, ", ".join(sorted(unrepresentable)),
               ", ".join(sorted(writer_gaps)), ", ".join(sorted(reader_defects)))
        ),
    }


def record_counts(map_path: Path) -> dict[str, int]:
    """Count each record kind actually present in one document.

    This is the measurement that makes `format_can_represent` evidence rather
    than assertion: a kind counted in a document at version V is proof the
    format can hold it at V, whatever any table says.
    """
    edit = _a3d_edit()
    (_pre, _raw, instances, start, enemy_gens,
     markers) = edit.read_a3d_sections(map_path, strict_markers=False)
    with map_path.open("rb") as stream:
        signature = stream.read(4)
        if signature != b"AS3D":
            raise MapEditError(f"invalid A3D signature: {map_path}")
        stream.read(4)
        patch_count = struct.unpack("<I", stream.read(4))[0]
    counts = {"patch": patch_count, "mesh": 0, "sprite": 0, "item": 0,
              "player_start": 1 if start is not None else 0,
              "enemy_generator": len(enemy_gens), "marker": len(markers)}
    for inst in instances:
        variant = getattr(inst, "variant", "mesh")
        counts[variant] = counts.get(variant, 0) + 1
    return counts


def _construct_native_patch(fmt: Any, patches_by_coord: dict[tuple[int, int], Any],
                            patch_x: int, patch_y: int,
                            initial_height: int) -> Any:
    """Construct one patch with the same edge grammar as engine AddTerrainPatch.

    Native patch creation does not leave a flat isolated tile.  Existing
    neighbours own shared edges; free edges interpolate between their corners,
    and free interior vertices use the weighted perimeter blend from
    ``engine/terrain.cpp``.  The visual bank starts at native ``0x0101``.
    """
    patch = fmt.A3DPatch(patch_x, patch_y)
    patch.height = [[initial_height] * 5 for _ in range(5)]
    neighbours = [
        patches_by_coord.get((patch_x - 1, patch_y - 1)),
        patches_by_coord.get((patch_x, patch_y - 1)),
        patches_by_coord.get((patch_x + 1, patch_y - 1)),
        patches_by_coord.get((patch_x + 1, patch_y)),
        patches_by_coord.get((patch_x + 1, patch_y + 1)),
        patches_by_coord.get((patch_x, patch_y + 1)),
        patches_by_coord.get((patch_x - 1, patch_y + 1)),
        patches_by_coord.get((patch_x - 1, patch_y)),
    ]
    flags = 0
    for index, neighbour in enumerate(neighbours):
        if neighbour is None:
            continue
        flags |= 1 << index
        if index == 0:
            patch.height[0][0] = neighbour.height[4][4]
        elif index == 1:
            patch.height[0] = list(neighbour.height[4])
        elif index == 2:
            patch.height[0][4] = neighbour.height[4][0]
        elif index == 3:
            for row in range(5):
                patch.height[row][4] = neighbour.height[row][0]
        elif index == 4:
            patch.height[4][4] = neighbour.height[0][0]
        elif index == 5:
            patch.height[4] = list(neighbour.height[0])
        elif index == 6:
            patch.height[4][0] = neighbour.height[0][4]
        elif index == 7:
            for row in range(5):
                patch.height[row][0] = neighbour.height[row][4]

    if not (flags & 0x83):
        patch.height[0][0] = initial_height
    if not (flags & 0x0E):
        patch.height[0][4] = initial_height
    if not (flags & 0x38):
        patch.height[4][4] = initial_height
    if not (flags & 0xE0):
        patch.height[4][0] = initial_height

    if not (flags & 0x02):
        h0, h1 = patch.height[0][0], patch.height[0][4]
        for col in range(1, 4):
            patch.height[0][col] = (h0 * (4 - col) + h1 * col + 2) // 4
    if not (flags & 0x08):
        h0, h1 = patch.height[0][4], patch.height[4][4]
        for row in range(1, 4):
            patch.height[row][4] = (h0 * (4 - row) + h1 * row + 2) // 4
    if not (flags & 0x20):
        h0, h1 = patch.height[4][0], patch.height[4][4]
        for col in range(1, 4):
            patch.height[4][col] = (h0 * (4 - col) + h1 * col + 2) // 4
    if not (flags & 0x80):
        h0, h1 = patch.height[0][0], patch.height[4][0]
        for row in range(1, 4):
            patch.height[row][0] = (h0 * (4 - row) + h1 * row + 2) // 4

    for row in range(1, 4):
        for col in range(1, 4):
            average = 0.0
            normalizer = 0.0
            for edge in range(4):
                weight = 1.0 / math.sqrt((col - edge) ** 2 + row ** 2)
                normalizer += weight
                average += patch.height[0][edge] * weight

                weight = 1.0 / math.sqrt((col - 4) ** 2 + (row - edge) ** 2)
                normalizer += weight
                average += patch.height[edge][4] * weight

                weight = 1.0 / math.sqrt((col - 4 + edge) ** 2 + (row - 4) ** 2)
                normalizer += weight
                average += patch.height[4][4 - edge] * weight

                weight = 1.0 / math.sqrt(col ** 2 + (row - 4 + edge) ** 2)
                normalizer += weight
                average += patch.height[4 - edge][0] * weight
            patch.height[row][col] = int(math.floor(average / normalizer + 0.5))

    patch.visual = [[0x0101] * 8 for _ in range(8)]
    return patch


def _update_native_patch_diagonals(patches_by_coord: dict[tuple[int, int], Any],
                                   coord: tuple[int, int]) -> tuple[set[tuple[int, int]], int]:
    """Apply engine/terrain.cpp Tap3x3.Update, including neighbour split bits.

    Height sampling keeps the centre patch's vertex at index four (native's
    strict ``> HEIGHT_CELLS`` boundary). Missing neighbours clamp back to the
    centre patch; they do not contribute fabricated zero heights.
    """
    centre_patch = patches_by_coord[coord]

    def sample(x: int, y: int) -> int:
        px = -1 if x < 0 else (1 if x > 4 else 0)
        py = -1 if y < 0 else (1 if y > 4 else 0)
        x -= px * 4
        y -= py * 4
        neighbour = patches_by_coord.get((coord[0] + px, coord[1] + py))
        if neighbour is None:
            if px:
                x = 0 if px < 0 else 4
            if py:
                y = 0 if py < 0 else 4
            neighbour = centre_patch
        return int(neighbour.height[y][x])

    changed: set[tuple[int, int]] = set()
    flips = 0
    for y in range(-1, 5):
        for x in range(-1, 5):
            c0 = (sample(x + 2, y + 2) + sample(x + 1, y + 2)
                  + sample(x + 2, y + 1) + sample(x - 1, y - 1)
                  + sample(x - 1, y) + sample(x, y - 1)
                  - sample(x, y) - sample(x + 1, y + 1)
                  - sample(x + 1, y) * 2 - sample(x, y + 1) * 2)
            c1 = (sample(x - 1, y + 2) + sample(x - 1, y + 1)
                  + sample(x, y + 2) + sample(x + 2, y - 1)
                  + sample(x + 1, y - 1) + sample(x + 2, y)
                  - sample(x, y + 1) - sample(x + 1, y)
                  - sample(x, y) * 2 - sample(x + 1, y + 1) * 2)
            px = -1 if x < 0 else (1 if x >= 4 else 0)
            py = -1 if y < 0 else (1 if y >= 4 else 0)
            target_coord = (coord[0] + px, coord[1] + py)
            target = patches_by_coord.get(target_coord)
            if target is None:
                continue
            bit = 1 << ((x - px * 4) + (y - py * 4) * 4)
            old = int(target.diag)
            new = old | bit if abs(c0) > abs(c1) else old & ~bit
            if new != old:
                target.diag = new
                changed.add(target_coord)
                flips += 1
    return changed, flips


def _apply_terrain_sculpt(pre_bytes: bytes, ops: list[dict[str, Any]],
                          centre: tuple[float, float], fmt: Any,
                          patch_ops: list[dict[str, Any]] | None = None) -> tuple[bytes, dict[str, Any]]:
    """Apply native sculpt/flatten/blur/sharpen/diagonal operations to A3DPatch rows.

    ``patch_ops`` is the native Alt patch/tile contract: patch coordinates are
    the on-disk 8-unit grid, and create/delete changes the header count.  The
    material palette remains byte-for-byte after the patch bank.
    """
    patch_ops = patch_ops or []
    if not ops and not patch_ops:
        return pre_bytes, {"strokes": 0, "vertices_changed": 0, "patches_changed": [],
                           "patches_created": 0, "patches_deleted": 0, "diagonals_changed": 0}
    stream = io.BytesIO(pre_bytes)
    signature = stream.read(4)
    _require(signature == b"AS3D", "terrain sculpt target is not an A3D document")
    header_size_raw = stream.read(4)
    patch_count_raw = stream.read(4)
    _require(len(header_size_raw) == 4 and len(patch_count_raw) == 4,
             "terrain sculpt target has a truncated A3D header")
    header_size = struct.unpack("<I", header_size_raw)[0]
    patch_count = struct.unpack("<I", patch_count_raw)[0]
    _require(header_size + patch_count * 188 <= len(pre_bytes),
             "terrain sculpt target has a truncated patch section")
    stream.seek(header_size)
    patches = [fmt.A3DPatch.from_file(stream) for _ in range(patch_count)]
    by_coord = {(int(p.x), int(p.y)): p for p in patches}
    patches_created = 0
    patches_deleted = 0
    changed: set[tuple[int, int]] = set()
    diagonals_changed = 0
    for raw_patch_op in patch_ops:
        action = str(raw_patch_op.get("action", "")).lower()
        try:
            coord = (int(raw_patch_op["patch_x"]), int(raw_patch_op["patch_y"]))
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise MapEditError("patch_tile requires integer patch_x and patch_y") from exc
        if action == "create":
            _require(coord not in by_coord, "patch_tile create target already exists")
            try:
                initial_height = int(raw_patch_op.get("initial_height", 0))
            except (TypeError, ValueError, OverflowError) as exc:
                raise MapEditError("patch_tile initial height must be integer native steps") from exc
            _require(0 <= initial_height <= 65535, "patch_tile initial height must be 0..65535")
            patch = _construct_native_patch(fmt, by_coord, coord[0], coord[1], initial_height)
            patches.append(patch)
            by_coord[coord] = patch
            diagonal_patches, flips = _update_native_patch_diagonals(by_coord, coord)
            changed.update(diagonal_patches)
            changed.add(coord)
            diagonals_changed += flips
            patches_created += 1
        elif action == "delete":
            _require(coord in by_coord, "patch_tile delete target does not exist")
            patches.remove(by_coord.pop(coord))
            patches_deleted += 1
        else:
            raise MapEditError("patch_tile action must be create or delete")
    vertices_changed = 0
    sampled_vertices = 0
    for raw_op in ops:
        stroke = _sculpt_terrain_payload(raw_op)
        cx = float(stroke["world_xz"][0]) + centre[0]
        cz = float(stroke["world_xz"][1]) + centre[1]
        radius = float(stroke["radius"])
        delta_steps = int(stroke["height_delta_steps"])
        shape = str(stroke.get("shape", "circle"))
        mode = str(stroke.get("mode", "raise"))
        strength = float(stroke.get("strength", 1.0))
        height_min = int(stroke.get("height_min", 0))
        height_max = int(stroke.get("height_max", 65535))
        for patch in patches:
            # FL-4839: a diagonal transaction changes only the split bit.
            # Its zero-delta blur carrier must never smooth vertex heights.
            if bool(stroke.get("diagonal", False)):
                continue
            original = [row[:] for row in patch.height]
            patch_height_changed = False
            for row in range(5):
                vz = float(patch.y * 8 + row * 2)
                for col in range(5):
                    vx = float(patch.x * 8 + col * 2)
                    dx = vx - cx
                    dz = vz - cz
                    distance = math.hypot(dx, dz)
                    if shape == "square":
                        inside = max(abs(dx), abs(dz)) <= radius
                    else:
                        inside = distance <= radius
                    if not inside:
                        continue
                    sampled_vertices += 1
                    if shape == "gaussian":
                        weight = math.exp(-4.0 * (distance / radius) ** 2)
                    elif shape == "noise":
                        # A stable hash keeps a stroke reproducible while
                        # retaining the native editor's intentionally rough
                        # noise-brush character.
                        weight = 0.45 + 0.55 * ((
                            math.sin((vx + cx * 0.37) * 12.9898
                                     + (vz + cz * 0.19) * 78.233) * 43758.5453
                        ) % 1.0)
                    else:
                        weight = 1.0
                    old = int(patch.height[row][col])
                    if mode in {"blur", "sharpen"}:
                        neighbours = []
                        for nr in range(max(0, row - 1), min(5, row + 2)):
                            for nc in range(max(0, col - 1), min(5, col + 2)):
                                if nr != row or nc != col:
                                    neighbours.append(float(original[nr][nc]))
                        average = sum(neighbours) / max(1, len(neighbours))
                        delta = (average - old) if mode == "blur" else (old - average)
                        applied_steps = int(round(delta * strength * weight))
                    elif mode == "flatten":
                        # ASCIIID's sampled-height target is a weighted
                        # interpolation, not an abrupt overwrite: the native
                        # brush alpha/strength moves each cell toward probe_z.
                        applied_steps = int(round(
                            (int(stroke["probe_height"]) - old) * strength * weight))
                    else:
                        applied_steps = int(round(delta_steps * weight))
                    if applied_steps == 0:
                        continue
                    new = old + applied_steps
                    if bool(stroke.get("probe_enabled", False)):
                        probe_height = int(stroke["probe_height"])
                        if applied_steps > 0:
                            new = old if old > probe_height else min(new, probe_height)
                        elif applied_steps < 0:
                            new = old if old < probe_height else max(new, probe_height)
                    # Native sculpt saturates the uint16 height bank instead
                    # of refusing the whole stroke when one summit/edge reaches
                    # its limit. Respect the session's narrower bounds too.
                    new = min(height_max, max(height_min, new))
                    if new != old:
                        patch.height[row][col] = new
                        patch_height_changed = True
                        vertices_changed += 1
                        changed.add((int(patch.x), int(patch.y)))
            if patch_height_changed:
                diagonal_patches, flips = _update_native_patch_diagonals(
                    by_coord, (int(patch.x), int(patch.y)))
                changed.update(diagonal_patches)
                diagonals_changed += flips
        if bool(stroke.get("diagonal", False)):
            for patch in patches:
                for row in range(8):
                    vz = float(patch.y * 8 + row + 0.5)
                    for col in range(8):
                        vx = float(patch.x * 8 + col + 0.5)
                        distance = math.hypot(vx - cx, vz - cz)
                        inside = max(abs(vx - cx), abs(vz - cz)) <= radius if shape == "square" else distance <= radius
                        if not inside:
                            continue
                        if row % 2 != 0 or col % 2 != 0:
                            continue
                        bit = (row // 2) * 4 + (col // 2)
                        patch.diag ^= 1 << bit
                        diagonals_changed += 1
                        changed.add((int(patch.x), int(patch.y)))
    _require(sampled_vertices > 0 or diagonals_changed > 0 or patches_created > 0 or patches_deleted > 0,
             "terrain operation did not touch an existing height sample or patch")
    patch_stream = io.BytesIO()
    for patch in patches:
        patch.write(patch_stream)
    patch_bytes = patch_stream.getvalue()
    old_patch_start = header_size
    old_patch_end = old_patch_start + patch_count * 188
    out = bytearray(pre_bytes[:header_size] + patch_bytes + pre_bytes[old_patch_end:])
    struct.pack_into("<I", out, 8, len(patches))
    return bytes(out), {
        "strokes": len(ops),
        "vertices_changed": vertices_changed,
        "patches_changed": [[x, y] for x, y in sorted(changed)],
        "patches_created": patches_created,
        "patches_deleted": patches_deleted,
        "diagonals_changed": diagonals_changed,
    }


def _apply_terrain_translate(pre_bytes: bytes, ops: list[dict[str, Any]],
                             fmt: Any) -> tuple[bytes, dict[str, Any]]:
    """Apply ASCIIID TranslateMap to the A3D height bank.

    Native TranslateMap raises/lowers every terrain vertex, or only the
    selected water side, and saturates at uint16 bounds.  World mesh matrices
    are translated in the operation loop below at the same command position;
    this function owns only the patch preamble.
    """
    if not ops:
        return pre_bytes, {"operations": 0, "vertices_changed": 0,
                           "patches_changed": [], "delta_z_total": 0}
    stream = io.BytesIO(pre_bytes)
    _require(stream.read(4) == b"AS3D", "translate_map target is not an A3D document")
    header_size_raw = stream.read(4)
    patch_count_raw = stream.read(4)
    _require(len(header_size_raw) == 4 and len(patch_count_raw) == 4,
             "translate_map target has a truncated A3D header")
    header_size = struct.unpack("<I", header_size_raw)[0]
    patch_count = struct.unpack("<I", patch_count_raw)[0]
    _require(header_size + patch_count * 188 <= len(pre_bytes),
             "translate_map target has a truncated patch section")
    stream.seek(header_size)
    patches = [fmt.A3DPatch.from_file(stream) for _ in range(patch_count)]
    changed: set[tuple[int, int]] = set()
    vertices_changed = 0
    delta_z_total = 0
    for raw_op in ops:
        payload = _translate_map_payload(raw_op)
        delta_z = int(payload["delta_z"])
        water_limit = bool(payload["water_limit"])
        water_height = int(payload["water_height"])
        delta_z_total += delta_z
        for patch in patches:
            patch_changed = False
            for row in range(5):
                for col in range(5):
                    old = int(patch.height[row][col])
                    if water_limit:
                        eligible = old >= water_height if delta_z > 0 else old < water_height
                        if not eligible:
                            continue
                    new = min(65535, old + delta_z) if delta_z > 0 else max(0, old + delta_z)
                    if new != old:
                        patch.height[row][col] = new
                        vertices_changed += 1
                        patch_changed = True
            if patch_changed:
                changed.add((int(patch.x), int(patch.y)))
    patch_stream = io.BytesIO()
    for patch in patches:
        patch.write(patch_stream)
    patch_bytes = patch_stream.getvalue()
    patch_start = header_size
    patch_end = patch_start + patch_count * 188
    out = bytearray(pre_bytes)
    out[patch_start:patch_end] = patch_bytes
    return bytes(out), {
        "operations": len(ops),
        "vertices_changed": vertices_changed,
        "patches_changed": [[x, y] for x, y in sorted(changed)],
        "delta_z_total": delta_z_total,
    }


def _patch_height_bilinear(height: list[list[int]], fx: float, fy: float) -> float:
    """Match asciiid.cpp SampleHeightBilinear on one 5x5 patch bank."""
    fx = min(4.0, max(0.0, fx))
    fy = min(4.0, max(0.0, fy))
    x0, y0 = math.floor(fx), math.floor(fy)
    x1, y1 = min(4, x0 + 1), min(4, y0 + 1)
    tx, ty = fx - x0, fy - y0
    h00 = float(height[y0][x0])
    h10 = float(height[y0][x1])
    h01 = float(height[y1][x0])
    h11 = float(height[y1][x1])
    h0 = h00 * (1.0 - tx) + h10 * tx
    h1 = h01 * (1.0 - tx) + h11 * tx
    return h0 * (1.0 - ty) + h1 * ty


def _patch_slope_magnitude(height: list[list[int]], fx: float, fy: float,
                           step: float = 0.25) -> float:
    """Match asciiid.cpp SampleSlopeMagnitude's finite-difference law."""
    h_l = _patch_height_bilinear(height, fx - step, fy)
    h_r = _patch_height_bilinear(height, fx + step, fy)
    h_d = _patch_height_bilinear(height, fx, fy - step)
    h_u = _patch_height_bilinear(height, fx, fy + step)
    dx = (h_r - h_l) / (2.0 * step)
    dy = (h_u - h_d) / (2.0 * step)
    return math.sqrt(dx * dx + dy * dy)


def _apply_terrain_auto_mat_elev(pre_bytes: bytes, ops: list[dict[str, Any]],
                                 fmt: Any) -> tuple[bytes, dict[str, Any]]:
    """Apply native AutoMatElev to every visual cell in each A3D patch."""
    if not ops:
        return pre_bytes, {"operations": 0, "cells_touched": 0,
                           "cells_changed": 0, "patches_changed": []}
    stream = io.BytesIO(pre_bytes)
    _require(stream.read(4) == b"AS3D", "auto_mat_elev target is not an A3D document")
    header_size_raw, patch_count_raw = stream.read(4), stream.read(4)
    _require(len(header_size_raw) == 4 and len(patch_count_raw) == 4,
             "auto_mat_elev target has a truncated A3D header")
    header_size = struct.unpack("<I", header_size_raw)[0]
    patch_count = struct.unpack("<I", patch_count_raw)[0]
    _require(header_size + patch_count * 188 <= len(pre_bytes),
             "auto_mat_elev target has a truncated patch section")
    stream.seek(header_size)
    patches = [fmt.A3DPatch.from_file(stream) for _ in range(patch_count)]
    changed: set[tuple[int, int]] = set()
    cells_touched = 0
    cells_changed = 0
    for raw_op in ops:
        payload = _auto_mat_elev_payload(raw_op)
        for patch in patches:
            patch_changed = False
            for v in range(8):
                for u in range(8):
                    fx = (u + 0.5) * 5.0 / 8.0
                    fy = (v + 0.5) * 5.0 / 8.0
                    auto_bit = 0
                    if payload["mode"] == 0:
                        auto_bit = int(_patch_slope_magnitude(patch.height, fx, fy)
                                       >= payload["slope_threshold"])
                    else:
                        auto_bit = int(_patch_height_bilinear(patch.height, fx, fy)
                                       >= payload["height_threshold"])
                    cells_touched += 1
                    old = int(patch.visual[v][u])
                    if payload["overwrite"]:
                        new = (old & ~0x8000) | (auto_bit << 15)
                    elif auto_bit:
                        new = old | 0x8000
                    else:
                        new = old
                    if new != old:
                        patch.visual[v][u] = new
                        cells_changed += 1
                        patch_changed = True
            if patch_changed:
                changed.add((int(patch.x), int(patch.y)))
    patch_stream = io.BytesIO()
    for patch in patches:
        patch.write(patch_stream)
    patch_start = header_size
    patch_end = patch_start + patch_count * 188
    out = bytearray(pre_bytes)
    out[patch_start:patch_end] = patch_stream.getvalue()
    return bytes(out), {"operations": len(ops), "cells_touched": cells_touched,
                        "cells_changed": cells_changed,
                        "patches_changed": [[x, y] for x, y in sorted(changed)]}


def _apply_clear_mat_elev(pre_bytes: bytes, ops: list[dict[str, Any]],
                          fmt: Any) -> tuple[bytes, dict[str, Any]]:
    """Clear the native 0x8000 elevation bit from every visual cell."""
    if not ops:
        return pre_bytes, {"operations": 0, "cells_touched": 0,
                           "cells_changed": 0, "patches_changed": []}
    stream = io.BytesIO(pre_bytes)
    _require(stream.read(4) == b"AS3D", "clear_mat_elev target is not an A3D document")
    header_size_raw, patch_count_raw = stream.read(4), stream.read(4)
    _require(len(header_size_raw) == 4 and len(patch_count_raw) == 4,
             "clear_mat_elev target has a truncated A3D header")
    header_size = struct.unpack("<I", header_size_raw)[0]
    patch_count = struct.unpack("<I", patch_count_raw)[0]
    _require(header_size + patch_count * 188 <= len(pre_bytes),
             "clear_mat_elev target has a truncated patch section")
    stream.seek(header_size)
    patches = [fmt.A3DPatch.from_file(stream) for _ in range(patch_count)]
    changed: set[tuple[int, int]] = set()
    cells_touched = 0
    cells_changed = 0
    for _raw_op in ops:
        for patch in patches:
            patch_changed = False
            for v in range(8):
                for u in range(8):
                    cells_touched += 1
                    old = int(patch.visual[v][u])
                    new = old & ~0x8000
                    if new != old:
                        patch.visual[v][u] = new
                        cells_changed += 1
                        patch_changed = True
            if patch_changed:
                changed.add((int(patch.x), int(patch.y)))
    patch_stream = io.BytesIO()
    for patch in patches:
        patch.write(patch_stream)
    patch_start = header_size
    patch_end = patch_start + patch_count * 188
    out = bytearray(pre_bytes)
    out[patch_start:patch_end] = patch_stream.getvalue()
    return bytes(out), {"operations": len(ops), "cells_touched": cells_touched,
                        "cells_changed": cells_changed,
                        "patches_changed": [[x, y] for x, y in sorted(changed)]}


def _apply_terrain_auto_texture(pre_bytes: bytes, ops: list[dict[str, Any]],
                                fmt: Any) -> tuple[bytes, dict[str, Any]]:
    """Apply native AutoTexture's slope/height material selection."""
    if not ops:
        return pre_bytes, {"operations": 0, "cells_matched": 0,
                           "cells_changed": 0, "patches_changed": []}
    stream = io.BytesIO(pre_bytes)
    _require(stream.read(4) == b"AS3D", "auto_texture target is not an A3D document")
    header_size_raw, patch_count_raw = stream.read(4), stream.read(4)
    _require(len(header_size_raw) == 4 and len(patch_count_raw) == 4,
             "auto_texture target has a truncated A3D header")
    header_size = struct.unpack("<I", header_size_raw)[0]
    patch_count = struct.unpack("<I", patch_count_raw)[0]
    _require(header_size + patch_count * 188 <= len(pre_bytes),
             "auto_texture target has a truncated patch section")
    stream.seek(header_size)
    patches = [fmt.A3DPatch.from_file(stream) for _ in range(patch_count)]
    changed: set[tuple[int, int]] = set()
    cells_matched = 0
    cells_changed = 0
    for raw_op in ops:
        payload = _auto_texture_payload(raw_op)
        for patch in patches:
            patch_changed = False
            for v in range(8):
                for u in range(8):
                    fx = (u + 0.5) * 5.0 / 8.0
                    fy = (v + 0.5) * 5.0 / 8.0
                    if payload["mode"] == 0:
                        match = (_patch_slope_magnitude(patch.height, fx, fy)
                                 >= payload["slope_threshold"])
                    else:
                        height = _patch_height_bilinear(patch.height, fx, fy)
                        match = payload["height_min"] <= height <= payload["height_max"]
                    if not match:
                        continue
                    cells_matched += 1
                    old = int(patch.visual[v][u])
                    # This is deliberately unconditional after a match. The
                    # native implementation mutates its local `match` flag
                    # for overwrite=false but still computes/writes `next`.
                    new = (old & 0xFF00) | (payload["material_id"] & 0xFF)
                    if new != old:
                        patch.visual[v][u] = new
                        cells_changed += 1
                        patch_changed = True
            if patch_changed:
                changed.add((int(patch.x), int(patch.y)))
    patch_stream = io.BytesIO()
    for patch in patches:
        patch.write(patch_stream)
    patch_start = header_size
    patch_end = patch_start + patch_count * 188
    out = bytearray(pre_bytes)
    out[patch_start:patch_end] = patch_stream.getvalue()
    return bytes(out), {"operations": len(ops), "cells_matched": cells_matched,
                        "cells_changed": cells_changed,
                        "patches_changed": [[x, y] for x, y in sorted(changed)]}


def _apply_terrain_material(pre_bytes: bytes, ops: list[dict[str, Any]],
                            centre: tuple[float, float], fmt: Any) -> tuple[bytes, dict[str, Any]]:
    """Apply native MAT-id paint to the fixed A3DPatch visual-cell bank."""
    if not ops:
        return pre_bytes, {"strokes": 0, "cells_touched": 0, "cells_changed": 0,
                           "patches_changed": []}
    stream = io.BytesIO(pre_bytes)
    signature = stream.read(4)
    _require(signature == b"AS3D", "material brush target is not an A3D document")
    header_size_raw = stream.read(4)
    patch_count_raw = stream.read(4)
    _require(len(header_size_raw) == 4 and len(patch_count_raw) == 4,
             "material brush target has a truncated A3D header")
    header_size = struct.unpack("<I", header_size_raw)[0]
    patch_count = struct.unpack("<I", patch_count_raw)[0]
    _require(header_size + patch_count * 188 <= len(pre_bytes),
             "material brush target has a truncated patch section")
    stream.seek(header_size)
    patches = [fmt.A3DPatch.from_file(stream) for _ in range(patch_count)]
    changed: set[tuple[int, int]] = set()
    cells_touched = 0
    cells_changed = 0
    for raw_op in ops:
        stroke = _paint_material_payload(raw_op)
        cx = float(stroke["world_xz"][0]) + centre[0]
        cz = float(stroke["world_xz"][1]) + centre[1]
        radius = float(stroke["radius"])
        material_id = int(stroke["material_id"])
        shape = str(stroke["shape"])
        height_limit = stroke.get("height_limit")
        below_limit = bool(stroke.get("below_limit", False))
        elevation = bool(stroke.get("elevation", False))
        elevation_value = int(raw_op.get("elevation_value", 1 if elevation else 0)) & 1
        slope_auto = bool(stroke.get("slope_auto", False))
        slope_threshold = max(0.0, float(stroke.get("slope_threshold", 1.0)))
        for patch in patches:
            for row in range(8):
                vz = float(patch.y * 8 + row + 0.5)
                for col in range(8):
                    vx = float(patch.x * 8 + col + 0.5)
                    dx = vx - cx
                    dz = vz - cz
                    if (max(abs(dx), abs(dz)) > radius if shape == "square"
                            else math.hypot(dx, dz) > radius):
                        continue
                    if height_limit is not None:
                        # Native HitTerrain compares the sampled terrain
                        # height against probe_z; nearest vertex is the same
                        # deterministic cell-space decision for A3D editing.
                        hx = min(4, max(0, int(round((col + 0.5) * 4.0 / 8.0))))
                        hy = min(4, max(0, int(round((row + 0.5) * 4.0 / 8.0))))
                        sample_height = float(patch.height[hy][hx]) / HEIGHT_SCALE
                        is_below = sample_height < float(height_limit)
                        if is_below != below_limit:
                            continue
                    cells_touched += 1
                    old = int(patch.visual[row][col])
                    high = old & 0xFF00
                    if elevation or slope_auto:
                        bit = elevation_value
                        if slope_auto:
                            h_left = float(patch.height[hy if height_limit is not None else min(4, row // 2)][max(0, min(4, col // 2 - 1))]) / HEIGHT_SCALE
                            h_right = float(patch.height[hy if height_limit is not None else min(4, row // 2)][max(0, min(4, col // 2 + 1))]) / HEIGHT_SCALE
                            h_back = float(patch.height[max(0, min(4, row // 2 - 1))][hx if height_limit is not None else min(4, col // 2)]) / HEIGHT_SCALE
                            h_front = float(patch.height[max(0, min(4, row // 2 + 1))][hx if height_limit is not None else min(4, col // 2)]) / HEIGHT_SCALE
                            bit = 1 if abs(h_right - h_left) + abs(h_front - h_back) >= slope_threshold else 0
                        high = (high & ~0x8000) | (bit << 15)
                    new = high | material_id
                    if new != old:
                        patch.visual[row][col] = new
                        cells_changed += 1
                        changed.add((int(patch.x), int(patch.y)))
    out = bytearray(pre_bytes)
    stream = io.BytesIO()
    for patch in patches:
        patch.write(stream)
    patch_bytes = stream.getvalue()
    patch_start = header_size
    patch_end = patch_start + patch_count * 188
    out[patch_start:patch_end] = patch_bytes
    return bytes(out), {
        "strokes": len(ops),
        "cells_touched": cells_touched,
        "cells_changed": cells_changed,
        "patches_changed": [[x, y] for x, y in sorted(changed)],
    }


# FL-4839: mutate only the selected A3D MatCell row and preserve every flag.
def _apply_material_ramp(pre_bytes: bytes, ops: list[dict[str, Any]],
                         fmt: Any) -> tuple[bytes, dict[str, Any]]:
    """Apply native row interpolation to the existing A3D material bank.

    Only MatCell foreground/background/glyph bytes are written.  The flags
    byte and every material/elevation row outside the selected ramp remain
    byte-for-byte untouched, matching ASCIIID's drag interpolation owner.
    """
    if not ops:
        return pre_bytes, {"operations": 0, "cells_touched": 0,
                           "cells_changed": 0, "materials_changed": []}
    stream = io.BytesIO(pre_bytes)
    _require(stream.read(4) == b"AS3D", "material ramp target is not an A3D document")
    header_size_raw, patch_count_raw = stream.read(4), stream.read(4)
    _require(len(header_size_raw) == 4 and len(patch_count_raw) == 4,
             "material ramp target has a truncated A3D header")
    header_size = struct.unpack("<I", header_size_raw)[0]
    patch_count = struct.unpack("<I", patch_count_raw)[0]
    material_start = header_size + patch_count * 188
    material_end = material_start + 256 * 512
    _require(material_end <= len(pre_bytes),
             "material ramp target has a truncated A3D material bank")
    out = bytearray(pre_bytes)
    cells_touched = 0
    cells_changed = 0
    materials_changed: set[int] = set()

    def _native_round(value: float) -> int:
        # Native roundf is applied only to non-negative RGB bytes here.
        return max(0, min(255, int(math.floor(value + 0.5))))

    for raw_op in ops:
        payload = _material_ramp_payload(raw_op)
        material_id = int(payload["material_id"])
        elevation = int(payload["elevation"])
        material_offset = material_start + material_id * 512
        row_offset = material_offset + elevation * 16 * 8
        for shade in range(16):
            weight = float(shade) / 15.0
            cell_offset = row_offset + shade * 8
            old = bytes(out[cell_offset:cell_offset + 8])
            fg = [
                _native_round(float(payload["fg_start"][channel]) * (1.0 - weight)
                              + float(payload["fg_end"][channel]) * weight)
                for channel in range(3)
            ]
            bg = [
                _native_round(float(payload["bg_start"][channel]) * (1.0 - weight)
                              + float(payload["bg_end"][channel]) * weight)
                for channel in range(3)
            ]
            next_cell = bytearray(old)
            next_cell[0:3] = bytes(fg)
            next_cell[4:7] = bytes(bg)
            if payload["glyph_start"] is not None:
                next_cell[3] = int(payload["glyph_start"] if shade <= 7
                                    else payload["glyph_end"])
            cells_touched += 1
            if bytes(next_cell) != old:
                out[cell_offset:cell_offset + 8] = next_cell
                cells_changed += 1
                materials_changed.add(material_id)
    return bytes(out), {"operations": len(ops), "cells_touched": cells_touched,
                        "cells_changed": cells_changed,
                        "materials_changed": sorted(materials_changed)}


def _apply_mesh_bake(pre_bytes: bytes, ops: list[dict[str, Any]],
                     instances: list[Any], fmt: Any,
                     map_path: Path) -> tuple[bytes, dict[str, Any]]:
    """Rasterize actual transformed AKM triangles, never placement footprints.

    Port of ASCIIID MeshBake::RasterizeHeightFace and selected-material baking:
    highest barycentric height, native 16-unit quantization, visible/nonvolatile
    instance filtering, and nine jittered rays per visual cell. The retained
    AKM reader also serves the production world-face compiler.
    """
    if not ops:
        return pre_bytes, {"operations": 0, "vertices_changed": 0, "cells_changed": 0}
    stream = io.BytesIO(pre_bytes)
    _require(stream.read(4) == b"AS3D", "mesh bake target is not an A3D document")
    header_size = struct.unpack("<I", stream.read(4))[0]
    patch_count = struct.unpack("<I", stream.read(4))[0]
    stream.seek(header_size)
    patches = [fmt.A3DPatch.from_file(stream) for _ in range(patch_count)]
    reader = _load("fl4839_akm_reader", REPO_ROOT / "scripts/bake_akm_to_static_world_faces.py")
    by_coord = {(int(p.x), int(p.y)): p for p in patches}
    mesh_cache: dict[str, Any] = {}
    patch_triangles: dict[tuple[int, int], list[Any]] = {}
    patch_keys = {(int(p.x), int(p.y)) for p in patches}
    triangle_count = 0
    for inst in instances:
        if getattr(inst, "variant", "mesh") != "mesh":
            continue
        flags = int(getattr(inst, "flags", 0))
        if flags & 4 or not flags & 1:  # INST_VOLATILE / INST_VISIBLE
            continue
        name = str(getattr(inst, "mesh_name", ""))
        _require(name.endswith(".akm") and Path(name).name == name,
                 "mesh bake requires an authored AKM basename")
        if name not in mesh_cache:
            path = _map_product_paths(map_path)["mesh_root"] / name
            if not path.is_file():
                path = REPO_ROOT / "assets/meshes" / name
            if not path.is_file():
                path = REPO_ROOT / "godot_project/assets/meshes" / name
            _require(path.is_file(), f"mesh bake asset missing: {name}")
            mesh_cache[name] = reader.parse_akm_ply(str(path))
        verts, faces = mesh_cache[name]
        tm = list(inst.transform)
        _require(len(tm) == 16 and all(math.isfinite(float(v)) for v in tm),
                 f"mesh bake transform is invalid: {name}")
        transformed = [tuple(sum(float(tm[column * 4 + axis]) * float(v["pos"][column])
                                 for column in range(3)) + float(tm[12 + axis])
                             for axis in range(3)) for v in verts]
        for face in faces:
            _require(all(0 <= index < len(verts) for index in face), f"invalid AKM face: {name}")
            points = tuple(transformed[index] for index in face)
            solid = any(int(verts[index].get("alpha", 255)) & 0x80 for index in face)
            low_x, high_x = min(p[0] for p in points), max(p[0] for p in points)
            low_y, high_y = min(p[1] for p in points), max(p[1] for p in points)
            colors = tuple(tuple(int(v) for v in verts[index]["col"]) for index in face)
            triangle = (points, solid, colors)
            for py in range(math.floor((low_y - 1e-6) / 8), math.floor(high_y / 8) + 1):
                for px in range(math.floor((low_x - 1e-6) / 8), math.floor(high_x / 8) + 1):
                    if (px, py) in patch_keys:
                        patch_triangles.setdefault((px, py), []).append(triangle)
            triangle_count += 1

    def surface_height(points: Any, x: float, y: float) -> float | None:
        a, b, c = points
        denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(denominator) < 1e-9:
            return None
        u = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / denominator
        v = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / denominator
        w = 1.0 - u - v
        return u * a[2] + v * b[2] + w * c[2] if min(u, v, w) >= -1e-6 else None

    def ray_hit(points: Any, origin: tuple[float, float, float],
                direction: tuple[float, float, float], colors: Any) -> tuple[float, bool, tuple[int, ...]] | None:
        a, b, c = points
        e1 = tuple(b[i] - a[i] for i in range(3))
        e2 = tuple(c[i] - a[i] for i in range(3))
        def cross(v: Any, w: Any) -> tuple[float, float, float]:
            return (v[1]*w[2] - v[2]*w[1], v[2]*w[0] - v[0]*w[2], v[0]*w[1] - v[1]*w[0])
        def dot(v: Any, w: Any) -> float:
            return sum(v[i] * w[i] for i in range(3))
        h = cross(direction, e2)
        determinant = dot(e1, h)
        if abs(determinant) < 1e-9:
            return None
        s = tuple(origin[i] - a[i] for i in range(3))
        u = dot(s, h) / determinant
        q = cross(s, e1)
        v = dot(direction, q) / determinant
        distance = dot(e2, q) / determinant
        if u < -1e-6 or v < -1e-6 or u + v > 1.0 + 1e-6 or distance < 0:
            return None
        normal = cross(e1, e2)
        norm = math.sqrt(dot(normal, normal))
        color = tuple(min(255, max(0, int(colors[0][i]*(1-u-v) + colors[1][i]*u + colors[2][i]*v)))
                      for i in range(3))
        return origin[2] - distance, norm > 1e-6 and abs(normal[2]) / norm < 0.7, color

    def height_at(height: Any, x: float, y: float) -> float:
        x, y = min(4.0, max(0.0, x)), min(4.0, max(0.0, y))
        x0, y0 = math.floor(x), math.floor(y)
        x1, y1 = min(4, x0 + 1), min(4, y0 + 1)
        fx, fy = x - x0, y - y0
        return ((height[y0][x0] * (1-fx) + height[y0][x1] * fx) * (1-fy)
                + (height[y1][x0] * (1-fx) + height[y1][x1] * fx) * fy)

    material_start = header_size + patch_count * 188
    material_bytes = bytearray(pre_bytes[material_start:material_start + 256 * 512])
    _require(len(material_bytes) == 256 * 512, "mesh bake material bank is truncated")
    used_materials = {0} | {int(cell) & 0xFF for p in patches for row in p.visual for cell in row}
    allocated_materials: list[int] = []

    def allocate_color(rgb: tuple[int, ...]) -> int:
        # Native GetOrAllocateMaterialID: strict 5-RGB-unit match, then the
        # first unused empty slot, then first unused nonempty slot. Never
        # overwrite an ID already referenced by terrain.
        best_id, best_distance = 1, float("inf")
        empty, fallback = [], []
        for material in range(1, 256):
            offset = material * 512
            background = tuple(material_bytes[offset + 4:offset + 7])
            if material not in used_materials:
                (empty if background == (0, 0, 0) else fallback).append(material)
            if background == (0, 0, 0):
                continue
            distance = sum((rgb[i] - background[i]) ** 2 for i in range(3))
            if distance < best_distance:
                best_id, best_distance = material, distance
        free = (empty or fallback)
        if best_distance <= 25 or not free:
            return best_id
        material = free[0]
        glyphs = [ord("."), ord(":"), ord(","), ord("'")]
        for elevation in range(4):
            for shade in range(16):
                factor = 1.0 - shade / 16.0 * 0.6
                background = [int(channel * factor) for channel in rgb]
                foreground = [int(min(255.0, channel * factor * 1.5)) for channel in rgb]
                offset = material * 512 + (elevation * 16 + shade) * 8
                material_bytes[offset:offset + 8] = bytes([*foreground, glyphs[elevation], *background, 0])
        used_materials.add(material)
        allocated_materials.append(material)
        return material

    vertices_changed = 0
    cells_changed = 0
    for op in ops:
        bake_height = bool(op.get("bake_height", True))
        bake_material = bool(op.get("bake_material", False))
        bake_vertex_colors = bool(op.get("bake_vertex_colors", False))
        material_id = int(op.get("material_id", 0)) & 0xFF
        overwrite_height = bool(op.get("overwrite_height", False))
        overwrite_material = bool(op.get("overwrite_material", False))
        solid_only = bool(op.get("solid_only", False))
        ray_top = float(op.get("ray_top", 70000.0))
        _require(math.isfinite(ray_top), "mesh bake ray top must be finite")
        for patch in patches:
            candidates = [(points, colors) for points, solid, colors in patch_triangles.get((int(patch.x), int(patch.y)), [])
                          if solid or not solid_only]
            if not candidates:
                continue
            patch_height_changed = False
            if bake_height:
                for row in range(5):
                    for col in range(5):
                        heights = [h for points, _ in candidates
                                   if (h := surface_height(points, patch.x * 8 + col * 2,
                                                           patch.y * 8 + row * 2)) is not None]
                        if not heights:
                            continue
                        old_height = int(patch.height[row][col])
                        # C++ round is halfway away from zero, not Python's bankers' round.
                        scaled = max(heights) / 16.0
                        rounded = math.floor(scaled + 0.5) if scaled >= 0 else math.ceil(scaled - 0.5)
                        target = min(65535, max(0, int(rounded * 16)))
                        next_height = target if overwrite_height else max(old_height, target)
                        if next_height != old_height:
                            patch.height[row][col] = next_height
                            patch_height_changed = True
                            vertices_changed += 1
            if patch_height_changed:
                _update_native_patch_diagonals(by_coord, (int(patch.x), int(patch.y)))
            if bake_material:
                wall_mask = [[False] * 8 for _ in range(8)]
                for row in range(8):
                    for col in range(8):
                        hits = []
                        samples = 1 if bake_vertex_colors else 3
                        for sy in range(samples):
                            for sx in range(samples):
                                origin = (patch.x * 8 + col + (0.5 if samples == 1 else (sx + 1) / 4.0),
                                          patch.y * 8 + row + (0.5 if samples == 1 else (sy + 1) / 4.0), ray_top)
                                direction = (0.0, 0.0, -1.0) if samples == 1 else (0.15 * (sx - 1), 0.15 * (sy - 1), -1.0)
                                ray_hits = [hit for points, colors in candidates
                                            if (hit := ray_hit(points, origin, direction, colors)) is not None]
                                if ray_hits:
                                    hits.append(max(ray_hits, key=lambda hit: hit[0]))
                        if not hits:
                            continue
                        wall_mask[row][col] = any(hit[1] for hit in hits)
                        old = int(patch.visual[row][col])
                        if not overwrite_material and old & 0xFF:
                            continue
                        fx, fy = (col + 0.5) / 2, (row + 0.5) / 2
                        center = height_at(patch.height, fx, fy)
                        nearby = min(height_at(patch.height, fx + dx, fy + dy)
                                     for dx, dy in [(0, 0), (-1, 0), (1, 0), (0, -1), (0, 1)])
                        elevation = 0x8000 if center - nearby > 64 else 0
                        walls = [hit for hit in hits if hit[1]]
                        color = max(walls or hits, key=lambda hit: hit[0])[2]
                        cell_material = allocate_color(color) if bake_vertex_colors else material_id
                        new = (old & 0x7F00) | elevation | cell_material
                        if new != old:
                            patch.visual[row][col] = new
                            cells_changed += 1
                # Native MeshBake fills elevated wall-neighbor gaps from the
                # post-ray bank, not recursively from newly filled cells.
                if bake_height:
                    base = [list(row) for row in patch.visual]
                    for row in range(8):
                        for col in range(8):
                            old = int(base[row][col])
                            if not overwrite_material and old & 0xFF:
                                continue
                            if not any(wall_mask[y][x] for y in range(max(0, row-2), min(8, row+3))
                                       for x in range(max(0, col-2), min(8, col+3))):
                                continue
                            neighbors = [(abs(x-col)+abs(y-row), y, x) for y in range(max(0, row-3), min(8, row+4))
                                         for x in range(max(0, col-3), min(8, col+4)) if int(base[y][x]) & 0xFF]
                            preferred = [entry for entry in neighbors if wall_mask[entry[1]][entry[2]]]
                            if not neighbors:
                                continue
                            _, y, x = min(preferred or neighbors)
                            new = (old & 0xFF00) | (int(base[y][x]) & 0xFF)
                            if new != int(patch.visual[row][col]):
                                patch.visual[row][col] = new
                                cells_changed += 1
    out_stream = io.BytesIO()
    for patch in patches:
        patch.write(out_stream)
    patch_start = header_size
    patch_end = patch_start + patch_count * 188
    out = bytearray(pre_bytes)
    out[patch_start:patch_end] = out_stream.getvalue()
    out[material_start:material_start + 256 * 512] = material_bytes
    return bytes(out), {"operations": len(ops), "vertices_changed": vertices_changed,
                        "cells_changed": cells_changed, "authored_triangles": triangle_count,
                        "allocated_materials": allocated_materials}


# ---------------------------------------------------------------------------
# read — placements in the runtime coordinate contract.
# ---------------------------------------------------------------------------

def read_placements(map_path: Path, runtime_terrain: Path | None = None) -> dict[str, Any]:
    map_path = map_path.resolve()
    terrain_path = (runtime_terrain or map_path).resolve()
    centre = _centre(terrain_path)
    edit = _a3d_edit()
    (_pre, raw_version, instances, player_start, enemy_gens,
     markers) = edit.read_a3d_sections(map_path, strict_markers=False)

    meshes: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    sprites: list[dict[str, Any]] = []
    for index, inst in enumerate(instances):
        variant = getattr(inst, "variant", "mesh")
        if variant == "mesh":
            tm = inst.transform
            meshes.append({
                "source_instance_index": index,
                "instance_identity": f"source:{index}",
                "mesh": str(inst.mesh_name),
                "name": str(inst.inst_name),
                "flags": int(inst.flags),
                "story_id": int(inst.story_id),
                # Editable yaw/scale are supplementary. Never discard the
                # authored tilt/reflection/shear when editing this placement.
                "yaw_degrees": math.degrees(math.atan2(float(tm[1]), float(tm[0]))),
                "scale": _mesh_scale(tm),
                "scale_axes": _mesh_scale_axes(tm),
                "authored_transform_column_major_f64": list(tm),
                **a3d_to_runtime([tm[12], tm[13], tm[14]], centre),
            })
        elif variant == "item":
            items.append({
                "source_instance_index": index,
                "instance_identity": f"source:{index}",
                "item_definition_id": int(inst.item_definition_id),
                "item_count": int(inst.item_count),
                "visual_style_id": int(inst.visual_style_id),
                "presentation_kind_id": int(inst.presentation_kind_id),
                "flags": int(inst.flags),
                "story_id": int(inst.story_id),
                "yaw_degrees": float(inst.yaw),
                **a3d_to_runtime(list(inst.pos), centre),
            })
        else:
            sprites.append({
                "source_instance_index": index,
                "instance_identity": f"source:{index}",
                "sprite": str(inst.inst_name),
                "anim": int(inst.anim),
                "frame": int(inst.frame),
                "reps": [int(v) for v in inst.reps],
                "flags": int(inst.flags),
                "story_id": int(inst.story_id),
                "yaw_degrees": float(inst.yaw),
                **a3d_to_runtime(list(inst.pos), centre),
            })

    enemies = [{
        "source_generator_index": index,
        "alive_max": int(gen.alive_max),
        "revive_min": int(gen.revive_min),
        "revive_max": int(gen.revive_max),
        "equipment": {
            "armor": int(gen.armor), "helmet": int(gen.helmet),
            "shield": int(gen.shield), "sword": int(gen.sword),
            "crossbow": int(gen.crossbow),
        },
        **a3d_to_runtime(list(gen.pos), centre),
    } for index, gen in enumerate(enemy_gens)]

    marker_rows = []
    for index, mk in enumerate(markers):
        name = str(getattr(mk, "name", ""))
        stored_label = str(getattr(mk, "label", ""))
        visible_label, radius = _decode_race_gate_label(stored_label)
        row = {
            "index": index,
            "name": name,
            "label": visible_label,
            "world_xz": [float(mk.x) - centre[0], float(mk.y) - centre[1]],
            "marker_type": int(getattr(mk, "marker_type", 0)),
            "glyph": str(getattr(mk, "glyph", "X")),
            "fg": int(getattr(mk, "fg", 226)),
        }
        if name.startswith("race_gate_") and radius is not None:
            row["radius"] = radius
        marker_rows.append(row)

    start = None
    if player_start is not None:
        start = a3d_to_runtime(list(player_start.pos), centre)
        start["yaw"] = float(player_start.yaw)

    return {
        "$schema": READ_SCHEMA,
        "map_path": _rel(map_path),
        "map_sha256": _sha256(map_path),
        "world_format_version": -raw_version if raw_version < 0 else 0,
        "runtime_center_offset": list(centre),
        "coordinate_contract": (
            "A3D X/Y minus the A3DMapLoader centre offset -> Godot X/Z; A3D Z / 16 -> Godot Y"
        ),
        "capabilities": capabilities(map_path),
        "counts": {
            "meshes": len(meshes), "items": len(items), "sprites": len(sprites),
            "enemy_generators": len(enemies), "minimap_markers": len(marker_rows),
            "player_start": start is not None,
        },
        "meshes": meshes,
        "items": items,
        "sprites": sprites,
        "enemy_generators": enemies,
        "minimap_markers": marker_rows,
        "player_start": start,
    }


# ---------------------------------------------------------------------------
# apply — the write half.
# ---------------------------------------------------------------------------

def _require(condition: bool, message: str) -> None:
    if not condition:
        raise MapEditError(message)


def apply_delta(map_path: Path, delta: dict[str, Any], out_path: Path | None = None,
                runtime_terrain: Path | None = None,
                verify_canonical: bool = True,
                preview_only: bool = False) -> dict[str, Any]:
    """Apply every op or none of them, then verify and commit.

    Refusal is total.  A partially applied delta would leave the canonical map
    in a state no session authored, which is worse than no save at all.
    """
    map_path = map_path.resolve()
    _require(str(delta.get("$schema", "")) == DELTA_SCHEMA,
             f"delta schema must be {DELTA_SCHEMA}")
    ops = delta.get("ops", [])
    _require(isinstance(ops, list), "delta ops must be a list")

    caps = capabilities(map_path)
    needed = sorted({OP_RECORD_KIND.get(str(op.get("op", "")), "") for op in ops} - {""})
    refusals = [caps["record_kinds"][kind]["blocker"] for kind in needed
                if not caps["record_kinds"].get(kind, {}).get("authorable", False)]
    if refusals:
        raise MapEditError("MAP_EDIT_REFUSED_UNREPRESENTABLE " + " | ".join(refusals))

    fmt = _a3d_format()
    edit = _a3d_edit()
    terrain_path = (runtime_terrain or map_path).resolve()
    centre = _centre(terrain_path)
    (pre, raw_version, instances, player_start, enemy_gens,
     markers) = edit.read_a3d_sections(map_path, strict_markers=False)
    instances_before_count = len(instances)
    version = -raw_version if raw_version < 0 else 0

    applied: list[dict[str, Any]] = []
    delete_indices: set[int] = set()
    delete_generators: set[int] = set()
    # Preserve the author's terrain operation sequence. Sorting topology,
    # translate, sculpt, baking and material into separate phases silently changes a
    # delete/recreate stroke or a height-limited paint made before a sculpt.
    # Consecutive operations of one kind still share one patch-bank rewrite.
    terrain_sequence: list[tuple[str, dict[str, Any]]] = []
    # Native bake consumes the world at the command's position, not the final
    # saved world. Copy mutable records so a later transform/story/flags edit
    # cannot rewrite an earlier bake, and omit only deletions already authored.
    # Keep source indices stable until final serialization as before.
    bake_instance_banks: dict[int, list[Any]] = {}
    staged_instance_indices: dict[int, int] = {}

    def instance_index(op: dict[str, Any]) -> int:
        if "staged_instance_id" in op:
            staged_id = int(op["staged_instance_id"])
            _require(staged_id in staged_instance_indices,
                     f"staged instance has no preceding placement: {staged_id}")
            return staged_instance_indices[staged_id]
        return int(op["source_instance_index"])

    for op in ops:
        kind = str(op.get("op", ""))
        if kind in {"add_mesh", "add_item", "add_sprite"} and "staged_instance_id" in op:
            staged_id = int(op["staged_instance_id"])
            _require(staged_id < 0 and staged_id not in staged_instance_indices,
                     f"staged instance identity must be unique and negative: {staged_id}")
            staged_instance_indices[staged_id] = len(instances)
        if kind == "sculpt_terrain":
            # Terrain is the fixed A3DPatch preamble, not a world-section
            # instance.  Collect strokes and rewrite that section once so the
            # existing section writer can preserve every unrelated byte.
            terrain_sequence.append(("sculpt", op))
            applied.append({"op": kind})
        elif kind == "patch_tile":
            action = str(op.get("action", "")).lower()
            _require(action in {"create", "delete"}, "patch_tile action must be create or delete")
            terrain_sequence.append(("patch", op))
            applied.append({"op": kind, "action": action,
                            "patch_x": int(op.get("patch_x", 0)),
                            "patch_y": int(op.get("patch_y", 0))})
        elif kind == "translate_map":
            payload = _translate_map_payload(op)
            translated_meshes = 0
            # TranslateMap's native QueryWorld callback owns mesh matrices only;
            # its sprite callback is an assertion and items/generators/spawn
            # records are not part of this command. Preserve that exact scope.
            for inst in instances:
                if getattr(inst, "variant", "mesh") != "mesh":
                    continue
                transform = list(inst.transform)
                _require(len(transform) == 16,
                         "translate_map requires 16-value mesh transforms")
                transform[14] += float(payload["delta_z"])
                inst.transform = transform
                translated_meshes += 1
            terrain_sequence.append(("translate", op))
            applied.append({"op": kind, "delta_z": int(payload["delta_z"]),
                            "water_limit": bool(payload["water_limit"]),
                            "water_height": int(payload["water_height"]),
                            "meshes_translated": translated_meshes})
        elif kind == "patch_diagonal":
            # Diagonal editing uses the exact same brush contract as native
            # Ctrl-click, but never changes heights.
            diagonal_op = dict(op)
            diagonal_op["op"] = "sculpt_terrain"
            diagonal_op["delta_y"] = 0.0
            diagonal_op["mode"] = "blur"
            diagonal_op["strength"] = 1.0
            diagonal_op["diagonal"] = True
            _sculpt_terrain_payload(diagonal_op)
            terrain_sequence.append(("sculpt", diagonal_op))
            applied.append({"op": kind})
        elif kind == "paint_material":
            # Material ids live in the same fixed A3DPatch visual bank as
            # terrain heights.  Collect them so all patch bytes are rewritten
            # once, preserving the native section layout.
            _paint_material_payload(op)
            terrain_sequence.append(("material", op))
            applied.append({"op": kind, "material_id": int(op["material_id"])})
        elif kind == "set_material_ramp":
            # The historical ASCIIID editor's row interpolation owns the 4x16 MatCell table,
            # not the patch visual-id bank. Keep it in the ordered terrain
            # sequence so a bake before/after the ramp observes command order.
            ramp = _material_ramp_payload(op)
            terrain_sequence.append(("material_ramp", op))
            applied.append({"op": kind, "material_id": int(ramp["material_id"]),
                            "elevation": int(ramp["elevation"])})
        elif kind == "auto_mat_elev":
            payload = _auto_mat_elev_payload(op)
            terrain_sequence.append(("auto_mat_elev", op))
            applied.append({"op": kind, "mode": int(payload["mode"]),
                            "slope_threshold": float(payload["slope_threshold"]),
                            "height_threshold": int(payload["height_threshold"]),
                            "overwrite": bool(payload["overwrite"])})
        elif kind == "clear_mat_elev":
            terrain_sequence.append(("clear_mat_elev", op))
            applied.append({"op": kind})
        elif kind == "auto_texture":
            payload = _auto_texture_payload(op)
            terrain_sequence.append(("auto_texture", op))
            applied.append({"op": kind, "mode": int(payload["mode"]),
                            "slope_threshold": float(payload["slope_threshold"]),
                            "height_min": int(payload["height_min"]),
                            "height_max": int(payload["height_max"]),
                            "material_id": int(payload["material_id"]),
                            "overwrite": bool(payload["overwrite"])})
        elif kind == "bake_meshes":
            _require(bool(op.get("bake_height", True)) or bool(op.get("bake_material", False)),
                     "mesh bake must select height or material")
            bake_instance_banks[len(terrain_sequence)] = copy.deepcopy(
                [inst for index, inst in enumerate(instances) if index not in delete_indices])
            terrain_sequence.append(("bake", op))
            applied.append({"op": kind})
        elif kind == "add_mesh":
            mesh = str(op["mesh"])
            _require(mesh.endswith(".akm"), f"mesh asset must be a .akm basename: {mesh}")
            _require("/" not in mesh and "\\" not in mesh, f"mesh asset must be a basename: {mesh}")
            pos = runtime_to_a3d(op["world_xz"], float(op.get("godot_y", 0.0)), centre)
            inst = fmt.A3DInstance(
                mesh_name=mesh,
                inst_name=str(op.get("name", "")),
                transform=_placement_mesh_transform(op, pos),
                flags=int(op.get("flags", PERSISTENT_INSTANCE_FLAGS)),
                story_id=int(op.get("story_id", -1)),
                variant="mesh",
            )
            instances.append(inst)
            applied.append({"op": kind, "mesh": mesh, "a3d_pos": pos})
        elif kind == "add_item":
            definition = int(op["item_definition_id"])
            _require(0 <= definition <= 65535, "item_definition_id out of range")
            style = int(op.get("visual_style_id", 0))
            presentation = int(op.get("presentation_kind_id", 0))
            count = int(op.get("item_count", 1))
            _require(0 <= style <= 65535, "visual_style_id out of range")
            _require(0 <= presentation <= 65535, "presentation_kind_id out of range")
            _require(1 <= count <= 2147483647, "item_count must be positive")
            pos = runtime_to_a3d(op["world_xz"], float(op.get("godot_y", 0.0)), centre)
            inst = fmt.A3DInstance(variant="item")
            inst.item_definition_id = definition
            inst.visual_style_id = style
            inst.presentation_kind_id = presentation
            inst.item_count = count
            inst.pos = pos
            inst.yaw = float(op.get("yaw_degrees", op.get("yaw", 0.0)))
            _require(math.isfinite(inst.yaw), "item yaw degrees must be finite")
            inst.flags = int(op.get("flags", PERSISTENT_INSTANCE_FLAGS))
            inst.story_id = int(op.get("story_id", -1))
            instances.append(inst)
            applied.append({"op": kind, "item_definition_id": definition, "a3d_pos": pos})
        elif kind == "add_sprite":
            sprite = str(op.get("sprite", op.get("name", ""))).strip()
            _require(sprite and sprite.lower().endswith(".xp") and "/" not in sprite
                     and "\\" not in sprite and len(sprite.encode("utf-8")) <= 255,
                     "sprite must be an .xp basename <=255 UTF-8 bytes")
            sprite_height = float(op.get("height_offset", 0.0))
            _require(math.isfinite(sprite_height), "sprite height offset must be finite")
            pos = runtime_to_a3d(op["world_xz"], float(op.get("godot_y", 0.0)) + sprite_height, centre)
            inst = fmt.A3DInstance(variant="sprite")
            inst.inst_name = sprite
            inst.pos = pos
            # Native SpritePrefs randomizes placement-time values.  A3D stores
            # the concrete result, so use a stable per-operation seed rather
            # than leaking process-global randomness into a saved map.
            seed = int.from_bytes(hashlib.sha256(
                f"{sprite}:{len(instances)}".encode("utf-8")).digest()[:8], "little")
            inst.yaw = float(op.get("yaw_degrees", op.get("yaw", 0.0)))
            if bool(op.get("rand_yaw", False)):
                # Native SpritePrefs uses fast_rand() % 360: the persisted
                # placement yaw is a whole degree, never a radian or a
                # fractional-degree surrogate.
                inst.yaw += seed % 360
            inst.anim, inst.frame = _sprite_animation_frame(
                sprite, int(op.get("anim", 0)), int(op.get("frame", 0)), seed,
                bool(op.get("rand_anim", False)), bool(op.get("rand_frame", False)))
            inst.reps = _sprite_repetitions(op.get("reps", [0, 0, 0, 0]))
            inst.flags = int(op.get("flags", PERSISTENT_INSTANCE_FLAGS))
            inst.story_id = int(op.get("story_id", -1))
            instances.append(inst)
            applied.append({"op": kind, "sprite": sprite, "a3d_pos": pos})
        elif kind == "add_enemy_generator":
            alive = int(op.get("alive_max", 1))
            rmin = int(op.get("revive_min", 0))
            rmax = int(op.get("revive_max", 0))
            _require(ENEMY_ALIVE_RANGE[0] <= alive <= ENEMY_ALIVE_RANGE[1],
                     f"alive_max must be {ENEMY_ALIVE_RANGE[0]}..{ENEMY_ALIVE_RANGE[1]}")
            _require(0 <= rmin <= ENEMY_REVIVE_MAX and 0 <= rmax <= ENEMY_REVIVE_MAX
                     and rmin <= rmax, "revive window outside the frozen range")
            equipment = dict(op.get("equipment", {}))
            for slot in ("armor", "helmet", "shield", "sword", "crossbow"):
                if slot in op:
                    equipment[slot] = op[slot]
            gen = fmt.A3DEnemyGen()
            gen.pos = runtime_to_a3d(op["world_xz"], float(op.get("godot_y", 0.0)), centre)
            gen.alive_max, gen.revive_min, gen.revive_max = alive, rmin, rmax
            for slot in ("armor", "helmet", "shield", "sword", "crossbow"):
                value = int(equipment.get(slot, 0))
                _require(0 <= value <= ENEMY_EQUIPMENT_MAX,
                         f"equipment {slot} must be 0..{ENEMY_EQUIPMENT_MAX}")
                setattr(gen, slot, value)
            enemy_gens.append(gen)
            applied.append({"op": kind, "a3d_pos": gen.pos, "alive_max": alive})
        elif kind == "set_player_start":
            pos = runtime_to_a3d(op["world_xz"], float(op.get("godot_y", 0.0)), centre)
            player_start = fmt.A3DPlayerStart(pos=pos, yaw=float(op.get("yaw", 0.0)),
                                              dir=float(op.get("dir", 0.0)))
            applied.append({"op": kind, "a3d_pos": pos})
        elif kind == "add_marker":
            marker_type = int(op.get("marker_type", 0))
            _require(0 <= marker_type <= MARKER_TYPE_MAX,
                     f"marker_type must be 0..{MARKER_TYPE_MAX}")
            glyph = str(op.get("glyph", "X"))
            marker = fmt.A3DMinimapMarker(
                name=str(op.get("name", "")),
                label=str(op.get("label", "")),
                x=float(op["world_xz"][0]) + centre[0],
                y=float(op["world_xz"][1]) + centre[1],
                fg=int(op.get("fg", 226)),
                glyph=glyph,
                marker_type=marker_type,
            )
            markers.append(marker)
            applied.append({"op": kind, "name": marker.name})
        elif kind == "delete_marker":
            name = str(op.get("name", ""))
            _require(_race_marker_name(name),
                     "delete_marker only admits race_gate_N/race_grid_N/race_path_N")
            matches = [index for index, marker in enumerate(markers)
                       if str(getattr(marker, "name", "")) == name]
            _require(len(matches) == 1,
                     f"race marker must exist exactly once before deletion: {name}")
            markers = [marker for marker in markers
                       if str(getattr(marker, "name", "")) != name]
            applied.append({"op": kind, "name": name})
        elif kind == "replace_generic_markers":
            # FL-4839: the session's record identities are editor-only. Persist
            # precisely the existing A3D marker fields; duplicate/empty authored
            # names remain legal and never act as mutation identities.
            rows = op.get("markers")
            _require(isinstance(rows, list) and all(isinstance(row, dict) for row in rows),
                     "replace_generic_markers requires marker objects")
            replacements = []
            for row in rows:
                name = str(row.get("name", ""))
                _require(not _race_marker_name(name),
                         "generic marker editing cannot author reserved race identities")
                coords = row.get("world_xz")
                _require(isinstance(coords, (list, tuple)) and len(coords) == 2,
                         "marker world_xz requires two coordinates")
                try:
                    x, z = float(coords[0]), float(coords[1])
                    marker_type = int(row.get("marker_type", 0))
                    fg = int(row.get("fg", 226))
                except (TypeError, ValueError, OverflowError) as exc:
                    raise MapEditError("marker fields require finite numbers") from exc
                _require(math.isfinite(x) and math.isfinite(z), "marker coordinates must be finite")
                _require(0 <= marker_type <= MARKER_TYPE_MAX, "marker type outside native range")
                _require(0 <= fg <= 255, "marker palette index outside native range")
                glyph = str(row.get("glyph", "X"))
                _require(1 <= len(glyph) <= 8 and "\n" not in glyph and "\r" not in glyph,
                         "marker glyph requires 1..8 single-line characters")
                replacements.append(fmt.A3DMinimapMarker(
                    name=name, label=str(row.get("label", "")),
                    x=x + centre[0], y=z + centre[1], fg=fg,
                    glyph=glyph, marker_type=marker_type))
            # Preserve race records at their current positions when possible,
            # replacing only generic slots, then append additional generic rows.
            rebuilt = []
            cursor = 0
            for marker in markers:
                if _race_marker_name(str(getattr(marker, "name", ""))):
                    rebuilt.append(marker)
                elif cursor < len(replacements):
                    rebuilt.append(replacements[cursor])
                    cursor += 1
            rebuilt.extend(replacements[cursor:])
            markers = rebuilt
            applied.append({"op": kind, "generic_marker_count": len(replacements)})
        elif kind == "replace_race_markers":
            rows = op.get("markers")
            _require(isinstance(rows, list),
                     "replace_race_markers markers must be a list")
            _require(all(isinstance(row, dict) for row in rows),
                     "replace_race_markers rows must be objects")
            race_rows = [_race_marker_payload(row) for row in rows]
            names = [row["name"] for row in race_rows]
            _require(len(names) == len(set(names)),
                     "replace_race_markers names must be unique")
            preserved = [marker for marker in markers
                         if not _race_marker_name(str(getattr(marker, "name", "")))]
            for row in race_rows:
                marker = fmt.A3DMinimapMarker(
                    name=row["name"],
                    label=row["label"],
                    x=float(row["world_xz"][0]) + centre[0],
                    y=float(row["world_xz"][1]) + centre[1],
                    fg=row["fg"],
                    glyph=row["glyph"],
                    marker_type=row["marker_type"],
                )
                preserved.append(marker)
            markers = preserved
            applied.append({"op": kind, "race_marker_count": len(race_rows)})
        elif kind == "delete_instance":
            index = instance_index(op)
            _require(0 <= index < len(instances), f"instance index out of range: {index}")
            delete_indices.add(index)
            applied.append({"op": kind, "source_instance_index": index})
        elif kind == "delete_all_meshes":
            deleted = 0
            for index, inst in enumerate(instances):
                if getattr(inst, "variant", "mesh") != "mesh":
                    continue
                if index not in delete_indices:
                    delete_indices.add(index)
                    deleted += 1
            applied.append({"op": kind, "deleted": deleted})
        elif kind == "update_instance":
            index = instance_index(op)
            _require(0 <= index < len(instances), f"instance index out of range: {index}")
            _require(index not in delete_indices,
                     f"instance {index} cannot be updated after deletion")
            inst = instances[index]
            world_xz = op.get("world_xz")
            _require(isinstance(world_xz, (list, tuple)) and len(world_xz) == 2,
                     "update_instance world_xz must contain exactly two coordinates")
            godot_y = float(op.get("godot_y", 0.0))
            if getattr(instances[index], "variant", "mesh") == "sprite":
                godot_y += float(op.get("height_offset", 0.0))
            pos = runtime_to_a3d([float(world_xz[0]), float(world_xz[1])], godot_y, centre)
            variant = getattr(inst, "variant", "mesh")
            if variant == "mesh":
                mesh = str(op.get("mesh", inst.mesh_name))
                _require(mesh.endswith(".akm") and "/" not in mesh and "\\" not in mesh,
                         f"mesh asset must be a .akm basename: {mesh}")
                inst.mesh_name = mesh
                inst.transform = _placement_mesh_transform(op, pos, inst.transform)
                if "name" in op:
                    inst.inst_name = str(op["name"])
            else:
                _require(math.isfinite(float(op.get("yaw_degrees", op.get("yaw", getattr(inst, "yaw", 0.0)))))
                         and math.isfinite(godot_y),
                         "instance transform must be finite")
                inst.pos = pos
                inst.yaw = float(op.get("yaw_degrees", op.get("yaw", inst.yaw)))
                if variant == "sprite":
                    if "sprite" in op:
                        inst.inst_name = str(op["sprite"])
                    if "sprite" in op or "anim" in op or "frame" in op:
                        inst.anim, inst.frame = _sprite_animation_frame(
                            inst.inst_name, int(op.get("anim", inst.anim)), int(op.get("frame", inst.frame)))
                    if "reps" in op:
                        inst.reps = _sprite_repetitions(op["reps"])
                elif variant == "item":
                    if "item_definition_id" in op:
                        definition = int(op["item_definition_id"])
                        _require(0 <= definition <= 65535,
                                 "item_definition_id out of range")
                        inst.item_definition_id = definition
                    if "item_count" in op:
                        count = int(op["item_count"])
                        _require(1 <= count <= 2147483647,
                                 "item_count must be positive")
                        inst.item_count = count
                    if "visual_style_id" in op:
                        style = int(op["visual_style_id"])
                        _require(0 <= style <= 65535, "visual_style_id out of range")
                        inst.visual_style_id = style
                    if "presentation_kind_id" in op:
                        presentation = int(op["presentation_kind_id"])
                        _require(0 <= presentation <= 65535,
                                 "presentation_kind_id out of range")
                        inst.presentation_kind_id = presentation
            if "flags" in op:
                inst.flags = int(op["flags"])
            if "story_id" in op:
                inst.story_id = int(op["story_id"])
            applied.append({"op": kind, "source_instance_index": index,
                            "variant": variant, "a3d_pos": pos})
        elif kind == "reset_items":
            # Native ResetItemInsts rebuilds volatile WORLD clones from the
            # persistent EDIT items; the A3D document stores only those EDIT
            # records, so the serialized operation intentionally preserves
            # every item while refreshing the runtime-facing view.
            applied.append({"op": kind, "reset_world_clones": True})
        elif kind == "set_instance_story":
            index = instance_index(op)
            _require(0 <= index < len(instances), f"instance index out of range: {index}")
            _require(index not in delete_indices,
                     f"instance {index} cannot receive a story id after deletion")
            inst = instances[index]
            variant = getattr(inst, "variant", "mesh")
            _require(variant in {"mesh", "item", "sprite"},
                     "story assignment requires a placeable instance")
            inst.story_id = int(op.get("story_id", -1))
            applied.append({"op": kind, "source_instance_index": index,
                            "story_id": int(inst.story_id), "variant": variant})
        elif kind == "delete_all_enemy_generators":
            delete_generators.update(range(len(enemy_gens)))
            applied.append({"op": kind, "deleted": len(enemy_gens)})
        elif kind in {"update_enemy_generator", "delete_enemy_generator"}:
            index = int(op["source_generator_index"])
            _require(0 <= index < len(enemy_gens),
                     f"enemy generator index out of range: {index}")
            if kind == "delete_enemy_generator":
                delete_generators.add(index)
                applied.append({"op": kind, "source_generator_index": index})
            else:
                gen = enemy_gens[index]
                if "world_xz" in op:
                    gen.pos = runtime_to_a3d(op["world_xz"], float(op.get("godot_y", 0.0)), centre)
                for field in ("alive_max", "revive_min", "revive_max", "armor", "helmet", "shield", "sword", "crossbow"):
                    if field in op:
                        setattr(gen, field, int(op[field]))
                _require(ENEMY_ALIVE_RANGE[0] <= int(gen.alive_max) <= ENEMY_ALIVE_RANGE[1],
                         "enemy alive_max outside 1..7")
                _require(0 <= int(gen.revive_min) <= int(gen.revive_max) <= ENEMY_REVIVE_MAX,
                         "enemy revive window outside 0..30")
                for field in ("armor", "helmet", "shield", "sword", "crossbow"):
                    _require(0 <= int(getattr(gen, field)) <= ENEMY_EQUIPMENT_MAX,
                             f"equipment {field} must be 0..{ENEMY_EQUIPMENT_MAX}")
                applied.append({"op": kind, "source_generator_index": index})
        else:
            raise MapEditError(f"unknown op: {kind!r}")

    if delete_indices:
        instances = [inst for i, inst in enumerate(instances) if i not in delete_indices]
    if delete_generators:
        enemy_gens = [gen for i, gen in enumerate(enemy_gens) if i not in delete_generators]

    terrain_receipt = {"strokes": 0, "vertices_changed": 0, "patches_changed": [],
                       "patches_created": 0, "patches_deleted": 0, "diagonals_changed": 0,
                       "operations": 0, "delta_z_total": 0}
    material_receipt = {"strokes": 0, "cells_touched": 0, "cells_changed": 0,
                        "operations": 0, "cells_matched": 0,
                        "patches_changed": [], "materials_changed": []}
    bake_receipt = {"operations": 0, "vertices_changed": 0, "cells_changed": 0}

    def merge_receipt(destination: dict[str, Any], source: dict[str, Any]) -> None:
        for key, value in source.items():
            if key == "patches_changed":
                coordinates = {tuple(row) for row in destination.get(key, [])}
                coordinates.update(tuple(row) for row in value)
                destination[key] = [list(row) for row in sorted(coordinates)]
            elif key == "allocated_materials":
                destination[key] = sorted(set(destination.get(key, [])) | set(value))
            elif key == "materials_changed":
                destination[key] = sorted(set(destination.get(key, [])) | set(value))
            elif isinstance(value, int):
                destination[key] = int(destination.get(key, 0)) + value

    cursor = 0
    while cursor < len(terrain_sequence):
        operation_kind = terrain_sequence[cursor][0]
        end = cursor + 1
        # Each bake owns a distinct command-time world, even if no terrain op
        # separates it from another bake (placement edits are not terrain ops).
        while (operation_kind != "bake" and end < len(terrain_sequence)
               and terrain_sequence[end][0] == operation_kind):
            end += 1
        batch = [row[1] for row in terrain_sequence[cursor:end]]
        if operation_kind == "patch":
            pre, receipt = _apply_terrain_sculpt(pre, [], centre, fmt, batch)
            merge_receipt(terrain_receipt, receipt)
        elif operation_kind == "sculpt":
            pre, receipt = _apply_terrain_sculpt(pre, batch, centre, fmt)
            merge_receipt(terrain_receipt, receipt)
        elif operation_kind == "translate":
            pre, receipt = _apply_terrain_translate(pre, batch, fmt)
            merge_receipt(terrain_receipt, receipt)
        elif operation_kind == "auto_mat_elev":
            pre, receipt = _apply_terrain_auto_mat_elev(pre, batch, fmt)
            merge_receipt(material_receipt, receipt)
        elif operation_kind == "clear_mat_elev":
            pre, receipt = _apply_clear_mat_elev(pre, batch, fmt)
            merge_receipt(material_receipt, receipt)
        elif operation_kind == "auto_texture":
            pre, receipt = _apply_terrain_auto_texture(pre, batch, fmt)
            merge_receipt(material_receipt, receipt)
        elif operation_kind == "material":
            pre, receipt = _apply_terrain_material(pre, batch, centre, fmt)
            merge_receipt(material_receipt, receipt)
        elif operation_kind == "material_ramp":
            pre, receipt = _apply_material_ramp(pre, batch, fmt)
            merge_receipt(material_receipt, receipt)
        else:
            pre, receipt = _apply_mesh_bake(
                pre, batch, bake_instance_banks[cursor], fmt, map_path)
            merge_receipt(bake_receipt, receipt)
        cursor = end

    if preview_only:
        # Ephemeral editor projection of this exact writer's result. No A3D
        # candidate, backup or product is written; Save remains the sole commit
        # route. The viewer consumes the same decoded patch shape as its loader.
        stream = io.BytesIO(pre)
        _require(stream.read(4) == b"AS3D", "terrain preview requires an A3D document")
        header_size, patch_count = struct.unpack("<II", stream.read(8))
        stream.seek(header_size)
        preview_patches = []
        for _ in range(patch_count):
            patch = fmt.A3DPatch.from_file(stream)
            heights = [int(value) for row in patch.height for value in row]
            preview_patches.append({
                "x": int(patch.x), "y": int(patch.y),
                "heights": heights, "visual": [int(value) for row in patch.visual for value in row],
                "lo": min(heights), "hi": max(heights), "diag": int(patch.diag),
            })
        material_bytes = stream.read(256 * 512)
        _require(len(material_bytes) == 256 * 512, "terrain preview material bank is truncated")
        return {"ok": True, "patches": preview_patches,
                "material_block_base64": base64.b64encode(material_bytes).decode("ascii"),
                "op_count": len(ops), "bake": bake_receipt}

    target = (out_path or map_path).resolve()
    canonical_verdict: dict[str, Any] = {}

    def validate_candidate(candidate: Path) -> None:
        try:
            verdict = _codec().roundtrip_verdict(candidate.read_bytes())
        except Exception as exc:
            raise MapEditError(
                f"MAP_EDIT_REJECTED_BY_CANONICAL_CODEC {type(exc).__name__}: {exc}"
            ) from exc
        if not verdict.get("roundtrip_stable", False):
            raise MapEditError("MAP_EDIT_REJECTED_UNSTABLE_CANONICAL_ROUNDTRIP")
        canonical_verdict.update(verdict)

    edit.write_a3d_sections(target, pre, raw_version, instances, player_start,
                            enemy_gens, markers,
                            validate_candidate=validate_candidate if verify_canonical else None,
                            backup_source=map_path)

    receipt = {
        "$schema": "fl4839_map_edit_receipt.v1",
        "map_path": _rel(map_path),
        "output_path": str(target),
        "world_format_version": version,
        "ops_applied": applied,
        "op_count": len(applied),
        "instances_before": instances_before_count,
        "instances_after": len(instances),
        "enemy_generators_after": len(enemy_gens),
        "markers_after": len(markers),
        "player_start_present": player_start is not None,
        "terrain": terrain_receipt,
        "materials": material_receipt,
        "mesh_bake": bake_receipt,
        "output_sha256": _sha256(target),
    }

    if verify_canonical:
        receipt["canonical_verdict"] = canonical_verdict
    return receipt


# ---------------------------------------------------------------------------
# Native map lifecycle — New, Merge and Save-As share the A3D section writer.
# ---------------------------------------------------------------------------

def _read_patches(pre: bytes, fmt: Any) -> tuple[int, list[Any]]:
    stream = io.BytesIO(pre)
    _require(stream.read(4) == b"AS3D", "map preamble is not an A3D document")
    header_size = struct.unpack("<I", stream.read(4))[0]
    count = struct.unpack("<I", stream.read(4))[0]
    _require(header_size + count * PATCH_SIZE <= len(pre), "truncated A3D patch section")
    stream.seek(header_size)
    return header_size, [fmt.A3DPatch.from_file(stream) for _ in range(count)]


def _replace_patches(pre: bytes, header_size: int, old_count: int,
                     patches: list[Any]) -> bytes:
    stream = io.BytesIO()
    for patch in patches:
        patch.write(stream)
    old_end = header_size + old_count * PATCH_SIZE
    out = bytearray(pre[:header_size] + stream.getvalue() + pre[old_end:])
    struct.pack_into("<I", out, 8, len(patches))
    return bytes(out)


def create_new_map(template_path: Path, out_path: Path) -> dict[str, Any]:
    """Create the same deterministic blank terrain as native ``New()``.

    Native source ``CreateBlankNewMapTerrain`` uses a 16x16 patch grid,
    height ``0xA000`` and material id ``1``.  The template contributes only
    the destination document's material table/header format; all world records
    are cleared and the output must be a new path.
    """
    template_path = template_path.resolve()
    out_path = out_path.resolve()
    _require(template_path.suffix.lower() == ".a3d" and out_path.suffix.lower() == ".a3d",
             "new map paths must end in .a3d")
    _require(not out_path.exists(), f"new map output already exists: {out_path}")
    edit = _a3d_edit()
    fmt = _a3d_format()
    pre, raw_version, _instances, _start, _enemies, markers = edit.read_a3d_sections(template_path, strict_markers=False)
    header_size, _old_patches = _read_patches(pre, fmt)
    patches = []
    for py in range(16):
        for px in range(16):
            patch = fmt.A3DPatch(px, py)
            patch.height = [[0xA000] * 5 for _ in range(5)]
            # engine/terrain.cpp memset(p->visual, 0x01, ...) initializes
            # each uint16 visual cell to native 0x0101, not integer 1.
            patch.visual = [[0x0101] * 8 for _ in range(8)]
            patches.append(patch)
    new_pre = _replace_patches(pre, header_size, len(_old_patches), patches)
    # A new native document has no authored world, spawns or markers. Keep the
    # marker section presence bit only when the format requires a trailing slot.
    empty_markers = edit.MarkerSection([], present=getattr(markers, "present", True))
    target = out_path
    edit.write_a3d_sections(target, new_pre, raw_version, [], None, [], empty_markers,
                            backup_source=template_path)
    return {
        "$schema": "fl4839_map_new_receipt.v1",
        "template_path": _rel(template_path), "output_path": str(target),
        "patches": len(patches), "base_height": 0xA000, "base_material": 1,
        "world_format_version": _world_format_version(target),
        "output_sha256": _sha256(target),
    }


def merge_map(target_path: Path, source_path: Path, runtime_terrain: Path | None = None,
              source_runtime_terrain: Path | None = None,
              offset_world_xz: tuple[float, float] = (0.0, 0.0)) -> dict[str, Any]:
    """Merge source terrain/mesh/sprite records using native ``MergeCommit`` rules."""
    target_path = target_path.resolve()
    source_path = source_path.resolve()
    _require(target_path.suffix.lower() == ".a3d" and source_path.suffix.lower() == ".a3d",
             "merge paths must end in .a3d")
    _require(target_path != source_path, "merge source and destination must differ")
    edit = _a3d_edit()
    fmt = _a3d_format()
    target_pre, target_raw, target_instances, target_start, target_enemies, target_markers = edit.read_a3d_sections(target_path, strict_markers=False)
    source_pre, _source_raw, source_instances, _source_start, _source_enemies, _source_markers = edit.read_a3d_sections(source_path, strict_markers=False)
    target_center = _centre((runtime_terrain or target_path).resolve())
    source_center = _centre((source_runtime_terrain or source_path).resolve())
    shift_x = float(offset_world_xz[0]) + target_center[0] - source_center[0]
    shift_y = float(offset_world_xz[1]) + target_center[1] - source_center[1]
    target_header, target_patches = _read_patches(target_pre, fmt)
    _source_header, source_patches = _read_patches(source_pre, fmt)
    patch_by_coord = {(int(p.x), int(p.y)): p for p in target_patches}
    created = 0
    merged = 0
    for source_patch in source_patches:
        tx = int(math.floor((float(source_patch.x * 8) + shift_x) / 8.0 + 0.5))
        ty = int(math.floor((float(source_patch.y * 8) + shift_y) / 8.0 + 0.5))
        coord = (tx, ty)
        dest = patch_by_coord.get(coord)
        new_patch = dest is None
        if dest is None:
            dest = _construct_native_patch(fmt, patch_by_coord, tx, ty, 0)
            dest.visual = [row[:] for row in source_patch.visual]
            patch_by_coord[coord] = dest
            target_patches.append(dest)
            _update_native_patch_diagonals(patch_by_coord, coord)
            created += 1
        for row in range(5):
            for col in range(5):
                dest.height[row][col] = max(int(dest.height[row][col]), int(source_patch.height[row][col]))
        _update_native_patch_diagonals(patch_by_coord, coord)
        if new_patch:
            # Native MergeCommit restores only the newly created patch's
            # authored source diagonals after the height/neighbor update.
            dest.diag = int(source_patch.diag)
        merged += 1
    merged_pre = _replace_patches(target_pre, target_header,
                                  len(_read_patches(target_pre, fmt)[1]), target_patches)
    # ASCIIID's typed merge transaction admits meshes and sprites, but rejects
    # item instances before opening URDO: the native world/editor clone pair
    # has no safe ownership boundary for imported items. Refuse here instead
    # of silently dropping an item's authored identity while reporting success.
    source_items = [inst for inst in source_instances
                    if getattr(inst, "variant", "mesh") == "item"]
    _require(not source_items, "document_merge_items_not_supported")
    mesh_added = 0
    sprite_added = 0
    for source_inst in source_instances:
        variant = getattr(source_inst, "variant", "mesh")
        if variant == "sprite":
            # CommitSprite uses the same persistent visible/tree flags and
            # intentionally drops source story linkage, matching the native
            # URDO_Create(sprite, ..., -1) contract.
            inst = fmt.A3DInstance(variant="sprite")
            inst.inst_name = str(source_inst.inst_name)
            inst.pos = [float(source_inst.pos[0]) + shift_x,
                        float(source_inst.pos[1]) + shift_y,
                        float(source_inst.pos[2])]
            inst.yaw = float(source_inst.yaw)
            inst.anim = int(source_inst.anim)
            inst.frame = int(source_inst.frame)
            inst.reps = [int(value) for value in source_inst.reps]
            inst.flags = PERSISTENT_INSTANCE_FLAGS
            inst.story_id = -1
            target_instances.append(inst)
            sprite_added += 1
            continue
        if variant != "mesh":
            # Refuse future decoder variants rather than fabricating a second
            # merge format; the native callback has no owner for them either.
            continue
        inst = fmt.A3DInstance(mesh_name=str(source_inst.mesh_name),
                               inst_name=str(source_inst.inst_name),
                               transform=list(source_inst.transform),
                               flags=PERSISTENT_INSTANCE_FLAGS, story_id=-1, variant="mesh")
        inst.transform[12] += shift_x
        inst.transform[13] += shift_y
        if hasattr(source_inst, "material_key"):
            inst.material_key = int(source_inst.material_key)
            inst.material_override = int(source_inst.material_override)
            inst.uid = 0
        target_instances.append(inst)
        mesh_added += 1
    target = target_path
    edit.write_a3d_sections(target, merged_pre, target_raw, target_instances,
                            target_start, target_enemies, target_markers,
                            backup_source=target_path)
    return {
        "$schema": "fl4839_map_merge_receipt.v1",
        "map_path": _rel(target_path), "source_path": _rel(source_path),
        "offset_world_xz": [float(offset_world_xz[0]), float(offset_world_xz[1])],
        "patches_merged": merged, "patches_created": created,
        "mesh_instances_added": mesh_added,
        "sprite_instances_added": sprite_added,
        "sprites_skipped": 0,
        "output_sha256": _sha256(target),
    }


def save_as_map(source_path: Path, out_path: Path) -> dict[str, Any]:
    """Serialize the current map sections to a distinct Save-As path."""
    source_path = source_path.resolve()
    out_path = out_path.resolve()
    _require(source_path.suffix.lower() == ".a3d" and out_path.suffix.lower() == ".a3d",
             "save-as paths must end in .a3d")
    _require(source_path != out_path, "save-as output must differ from source")
    edit = _a3d_edit()
    pre, raw, instances, player_start, enemy_gens, markers = edit.read_a3d_sections(source_path, strict_markers=False)
    edit.write_a3d_sections(out_path, pre, raw, instances, player_start, enemy_gens, markers,
                            backup_source=source_path)
    return {"$schema": "fl4839_map_save_as_receipt.v1",
            "map_path": _rel(source_path), "output_path": str(out_path),
            "output_sha256": _sha256(out_path)}


# ---------------------------------------------------------------------------
# upgrade — the FL-4834 world-format migration, in the SAME front door.
# ---------------------------------------------------------------------------

UPGRADE_SCHEMA = "fl4834_map_upgrade_receipt.v1"
UPGRADE_SUPPORTED_TARGETS = (3, 4)


def _instance_identity(inst: Any) -> tuple:
    """Everything about one record that must survive a version change.

    Positions and yaws are compared as raw IEEE-754 bits, not as decimals: a
    migration that quietly re-rounded a coordinate would still look equal at
    printed precision and would move an item by a fraction of a world unit.
    """
    variant = getattr(inst, "variant", "mesh")
    if variant == "mesh":
        return ("mesh", str(inst.mesh_name), str(inst.inst_name),
                tuple(struct.pack("<d", float(v)) for v in inst.transform),
                int(inst.flags), int(inst.story_id))
    if variant == "item":
        return ("item", int(inst.item_definition_id), int(inst.item_count),
                struct.pack("<3f", *[float(v) for v in inst.pos]),
                struct.pack("<f", float(inst.yaw)),
                int(inst.flags), int(inst.story_id))
    return ("sprite", str(inst.inst_name),
            struct.pack("<3f", *[float(v) for v in inst.pos]),
            struct.pack("<f", float(inst.yaw)),
            int(inst.anim), int(inst.frame), tuple(int(v) for v in inst.reps),
            int(inst.flags), int(inst.story_id))


def _enemy_identity(gen: Any) -> tuple:
    return (struct.pack("<3f", *[float(v) for v in gen.pos]),
            int(gen.alive_max), int(gen.revive_min), int(gen.revive_max),
            int(gen.armor), int(gen.helmet), int(gen.shield),
            int(gen.sword), int(gen.crossbow))


def _player_start_identity(start: Any) -> tuple | None:
    if start is None:
        return None
    return (struct.pack("<3f", *[float(v) for v in start.pos]),
            struct.pack("<f", float(start.yaw)), struct.pack("<f", float(start.dir)))


def upgrade_world_format(map_path: Path, to_version: int, out_path: Path | None = None,
                         player_start: dict[str, Any] | None = None,
                         runtime_terrain: Path | None = None) -> dict[str, Any]:
    """Raise one document's world format version without losing an identity.

    FL-4834.  The elected canonical runtime map
    The elected canonical runtime map was migrated from world format v1 to v4
    through this existing write front door. Its 154 legacy item identities were
    upgraded to the v3 bundle without changing identity, and no player start was
    invented. The pre-migration v1 path remains covered by explicit legacy
    negative controls in the tests.

    It is a WORLD-SECTION-ONLY change.  The header, every terrain patch and the
    material table are copied through unmodified, so terrain sampling, the water
    predicate and every per-patch digest are bit-identical afterwards.

    The upgrade REFUSES rather than proceeding if any mesh, sprite, item, enemy
    generator, marker or player start fails to reappear with the same identity
    in the re-read output.
    """
    map_path = map_path.resolve()
    compat = _compat()
    _require(to_version in UPGRADE_SUPPORTED_TARGETS,
             f"unsupported target world format version {to_version}; "
             f"supported: {', '.join(str(v) for v in UPGRADE_SUPPORTED_TARGETS)}. "
             "Newer versions are owned by scripts/a3d/compatibility.py, not by this tool.")
    fmt = _a3d_format()
    edit = _a3d_edit()
    before_version = _world_format_version(map_path)
    _require(before_version <= compat.CURRENT_MAX_WORLD_VERSION,
             f"{compat.WORLD_REJECT_REASON}: {map_path.name} is world format "
             f"v{before_version}, newer than v{compat.CURRENT_MAX_WORLD_VERSION}; refusing")
    _require(before_version <= to_version,
             f"{map_path.name} is already world format v{before_version}; "
             f"this tool never downgrades to v{to_version}. A v5/v6 source "
             "reaches Classic versions only through scripts/asciid.py downgrade-to-classic "
             "downgrade_to_classic with its explicit loss contract "
             "(allow_lossy=True opts into dropping v5/v6-only fields).")

    (pre, raw_version, instances, start, enemy_gens,
     markers) = edit.read_a3d_sections(map_path, strict_markers=False)

    before_instances = [_instance_identity(inst) for inst in instances]
    before_enemies = [_enemy_identity(gen) for gen in enemy_gens]
    before_markers = [(str(m.name), str(m.label), struct.pack("<ff", float(m.x), float(m.y)),
                       int(m.fg), str(m.glyph), int(m.marker_type)) for m in markers]

    # The only record mutation: a pre-v3 item gains the two identity fields the
    # v3 bundle added.  The values are not invented here -- see
    # a3d_format.LEGACY_ITEM_* for the measurement that fixes them.
    upgraded_items = 0
    if to_version >= fmt.LEGACY_ITEM_MIN_BUNDLE_VERSION:
        for inst in instances:
            if getattr(inst, "variant", "") == "item" and getattr(inst, "legacy_item_layout", False):
                inst.legacy_item_layout = False
                inst.visual_style_id = fmt.LEGACY_ITEM_VISUAL_STYLE_ID
                inst.presentation_kind_id = fmt.LEGACY_ITEM_PRESENTATION_KIND_ID
                upgraded_items += 1

    start_added = False
    if player_start is not None:
        _require(to_version >= FORMAT_MIN_VERSION["player_start"],
                 f"a player start requires v{FORMAT_MIN_VERSION['player_start']}; "
                 f"target is v{to_version}")
        centre = _centre((runtime_terrain or map_path).resolve())
        pos = runtime_to_a3d(player_start["world_xz"], float(player_start.get("godot_y", 0.0)), centre)
        start = fmt.A3DPlayerStart(pos=pos, yaw=float(player_start.get("yaw", 0.0)),
                                   dir=float(player_start.get("dir", 0.0)))
        start_added = True

    target = (out_path or map_path).resolve()
    source_sha256 = _sha256(map_path)

    def validate_upgrade(candidate: Path) -> None:
        # Inspect the writer's complete on-disk candidate BEFORE replacement.
        # A lost identity must not corrupt the elected map on its refusal path.
        (after_pre, after_raw, after_instances, after_start, after_enemies,
         after_markers) = edit.read_a3d_sections(candidate, strict_markers=False)
        _require(after_pre == pre, "UPGRADE_REJECTED_TERRAIN_CHANGED")
        _require(-after_raw == to_version,
                 f"UPGRADE_REJECTED_VERSION_NOT_WRITTEN got {after_raw}")
        _require([_instance_identity(inst) for inst in after_instances] == before_instances,
                 "UPGRADE_REJECTED_INSTANCE_IDENTITY_LOST")
        _require([_enemy_identity(g) for g in after_enemies] == before_enemies,
                 "UPGRADE_REJECTED_ENEMY_IDENTITY_LOST")
        _require([(str(m.name), str(m.label), struct.pack("<ff", float(m.x), float(m.y)),
                   int(m.fg), str(m.glyph), int(m.marker_type)) for m in after_markers] == before_markers,
                 "UPGRADE_REJECTED_MARKER_IDENTITY_LOST")
        _require(_player_start_identity(after_start) == _player_start_identity(start),
                 "UPGRADE_REJECTED_PLAYER_START_IDENTITY_LOST")
        verdict = _codec().roundtrip_verdict(candidate.read_bytes())
        _require(bool(verdict.get("roundtrip_stable", False)),
                 "UPGRADE_REJECTED_UNSTABLE_CANONICAL_ROUNDTRIP")

    edit.write_a3d_sections(target, pre, -to_version, instances, start, enemy_gens, markers,
                            validate_candidate=validate_upgrade, backup_source=map_path)
    (_after_pre, _after_raw, after_instances, after_start, after_enemies,
     after_markers) = edit.read_a3d_sections(target, strict_markers=False)

    kinds: dict[str, int] = {}
    for inst in after_instances:
        variant = getattr(inst, "variant", "mesh")
        kinds[variant] = kinds.get(variant, 0) + 1

    return {
        "$schema": UPGRADE_SCHEMA,
        "map_path": _rel(map_path),
        "map_sha256_before": source_sha256,
        "output_path": str(target),
        "output_sha256": _sha256(target),
        "world_format_version_before": before_version,
        "world_format_version_after": to_version,
        "terrain_bytes_unchanged": True,
        "legacy_item_records_upgraded": upgraded_items,
        "legacy_item_identity_fill": {
            "visual_style_id": fmt.LEGACY_ITEM_VISUAL_STYLE_ID,
            "presentation_kind_id": fmt.LEGACY_ITEM_PRESENTATION_KIND_ID,
            "provenance": (
                "measured from assets/a3d/game_map_y8_original_game_map.a3d, the "
                "already-shipped v4 twin that carries the SAME 154 item identities"
            ),
        },
        "player_start_added": start_added,
        "player_start_present": after_start is not None,
        "counts_after": {
            "meshes": kinds.get("mesh", 0),
            "sprites": kinds.get("sprite", 0),
            "items": kinds.get("item", 0),
            "enemy_generators": len(after_enemies),
            "minimap_markers": len(after_markers),
        },
        "capabilities_after": capabilities(target),
    }


# ---------------------------------------------------------------------------
# Low-context agent CLI — discovery, focused reads and complete writer access.
# ---------------------------------------------------------------------------

ITEM_BUNDLES = [
    ("Gold Hat / Default", 400, 500, 603), ("Gold Hat / Gold", 400, 501, 603),
    ("Gold Hat / Dark", 400, 502, 603), ("Cyan Suit / Default", 401, 500, 603),
    ("Cyan Suit / Gold", 401, 501, 603), ("Cyan Suit / Dark", 401, 502, 603),
    ("Shield / Default", 402, 500, 603), ("Shield / Gold", 402, 501, 603),
    ("Shield / Dark", 402, 502, 603), ("Sword", 403, 500, 603),
    ("Heavy Weapon", 404, 500, 603), ("Heal", 405, 500, 603),
    ("Big Heal", 406, 500, 603), ("Bone Loot", 407, 500, 603),
    ("Gem Loot", 408, 500, 603),
]


def operation_manifest(operation: str = "") -> dict[str, Any]:
    """Return the complete apply_delta operation contract in bounded JSON."""
    if operation:
        _require(operation in OPERATION_SPECS, f"unknown operation: {operation}")
        selected = {operation: OPERATION_SPECS[operation]}
    else:
        selected = OPERATION_SPECS
    operations = {}
    for name, spec in selected.items():
        operations[name] = {**spec, "record_kind": OP_RECORD_KIND[name]}
    return {
        "$schema": "fl4839_agent_operation_manifest.v1",
        "delta_schema": DELTA_SCHEMA,
        "operation_count": len(operations),
        "operations": operations,
        "inline_example": (
            "python3 scripts/apply_map_edits.py edit --map MAP --op add_mesh "
            "--at 10 20 --asset fence.akm --set yaw_degrees=90 --preview"
        ),
        "batch_example": (
            "python3 scripts/apply_map_edits.py batch --map MAP "
            "--ops '[{\"op\":\"clear_mat_elev\"}]' --preview"
        ),
    }


def _bounded_rows(rows: list[Any], query: str, offset: int, limit: int) -> dict[str, Any]:
    if query:
        needle = query.casefold()
        rows = [row for row in rows if needle in json.dumps(row, sort_keys=True).casefold()]
    total = len(rows)
    selected = rows[offset:offset + limit]
    return {"total": total, "offset": offset, "limit": limit, "returned": len(selected),
            "rows": selected}


def inspect_map(map_path: Path, runtime_terrain: Path | None, kind: str,
                query: str, offset: int, limit: int) -> dict[str, Any]:
    data = read_placements(map_path, runtime_terrain)
    summary = {
        "$schema": "fl4839_agent_inspection.v1",
        "map_path": data["map_path"], "map_sha256": data["map_sha256"],
        "world_format_version": data["world_format_version"], "counts": data["counts"],
    }
    if kind == "summary":
        return summary
    key = {
        "mesh": "meshes", "item": "items", "sprite": "sprites",
        "enemy": "enemy_generators", "marker": "minimap_markers",
    }.get(kind, kind)
    if key == "player_start":
        summary["kind"] = key
        summary["row"] = data["player_start"]
        return summary
    _require(key in {"meshes", "items", "sprites", "enemy_generators", "minimap_markers"},
             f"unknown inspection kind: {kind}")
    summary["kind"] = key
    summary.update(_bounded_rows(list(data[key]), query, offset, limit))
    return summary


def _installed_authoring_map(map_path: Path) -> Path:
    resolved = map_path.resolve()
    maps_root = (REPO_ROOT / "godot_project/assets/maps").resolve()
    _require(resolved.is_file(), f"authoring map does not exist: {resolved}")
    _require(resolved.parent == maps_root,
             "authoring map must be directly under godot_project/assets/maps")
    return resolved


def asset_catalog(kind: str, query: str, offset: int, limit: int,
                  map_path: Path | None = None) -> dict[str, Any]:
    """Return the bounded shared plus map-owned placement library.

    A basename is one A3D identity.  When a map bundle owns that basename it
    shadows the shared library, matching the Godot/runtime resolver instead of
    showing two entries that serialize to the same record.
    """
    rows_by_identity: dict[tuple[str, str], dict[str, Any]] = {}
    if kind in {"all", "mesh"}:
        for path in sorted((REPO_ROOT / "assets/meshes").glob("*.akm")):
            rows_by_identity[("mesh", path.name.lower())] = {
                "kind": "mesh", "asset": path.name, "origin": "shared"}
    if kind in {"all", "sprite"}:
        for path in sorted((REPO_ROOT / "assets/sprites").glob("*.xp")):
            rows_by_identity[("sprite", path.name.lower())] = {
                "kind": "sprite", "asset": path.name, "origin": "shared"}
    if map_path is not None:
        selected_map = _installed_authoring_map(map_path)
        product_paths = _map_product_paths(selected_map)
        if kind in {"all", "mesh"}:
            for path in sorted(product_paths["mesh_root"].glob("*.akm")):
                rows_by_identity[("mesh", path.name.lower())] = {
                    "kind": "mesh", "asset": path.name, "origin": "map"}
        if kind in {"all", "sprite"}:
            for path in sorted(product_paths["sprite_root"].glob("*.xp")):
                rows_by_identity[("sprite", path.name.lower())] = {
                    "kind": "sprite", "asset": path.name, "origin": "map"}
    rows = sorted(rows_by_identity.values(),
                  key=lambda row: (str(row["kind"]), str(row["asset"]).lower()))
    if kind in {"all", "item"}:
        for label, definition, style, presentation in ITEM_BUNDLES:
            rows.append({"kind": "item", "label": label,
                         "item_definition_id": definition, "visual_style_id": style,
                         "presentation_kind_id": presentation})
    _require(kind in {"all", "mesh", "sprite", "item"}, f"unknown asset kind: {kind}")
    return {"$schema": "fl4839_agent_asset_catalog.v2", "kind": kind,
            "map": _rel(map_path.resolve()) if map_path is not None else "",
            **_bounded_rows(rows, query, offset, limit)}


def import_asset(map_path: Path, source: Path) -> dict[str, Any]:
    """Import one external AKM/XP into the selected map's isolated bundle."""
    selected_map = _installed_authoring_map(map_path)
    source = source.expanduser().resolve()
    _require(source.is_file(), f"asset source does not exist: {source}")
    _require(source.name == Path(source.name).name and not source.name.startswith("."),
             "asset source must have a visible basename")
    suffix = source.suffix.lower()
    _require(suffix in {".akm", ".xp"}, "asset import accepts .akm or .xp")
    paths = _map_product_paths(selected_map)
    root = paths["mesh_root"] if suffix == ".akm" else paths["sprite_root"]

    details: dict[str, Any] = {}
    if suffix == ".akm":
        reader = _load("fl4839_import_akm_reader",
                       REPO_ROOT / "scripts/bake_akm_to_static_world_faces.py")
        try:
            vertices, faces = reader.parse_akm_ply(str(source))
        except Exception as exc:  # parser exceptions are part of admission
            raise MapEditError(f"AKM import validation failed: {exc}") from exc
        _require(vertices and faces, "AKM import has no renderable geometry")
        details = {"vertex_count": len(vertices), "face_count": len(faces)}
    else:
        xp_reader = _load("fl4839_import_xp_reader", REPO_ROOT / "scripts/xp_to_png.py")
        try:
            _version, layers = xp_reader.read_xp(str(source))
        except Exception as exc:
            raise MapEditError(f"XP import validation failed: {exc}") from exc
        _require(layers, "XP import has no layers")
        details = {"layer_count": len(layers)}

    root.mkdir(parents=True, exist_ok=True)
    destination = root / source.name
    if destination.exists():
        _require(destination.is_file(), f"asset destination is not a file: {destination}")
        _require(_sha256_path(destination) == _sha256_path(source),
                 f"asset basename collision in map bundle: {source.name}")
        copied = False
    else:
        with tempfile.NamedTemporaryFile(dir=root, prefix=f".{source.name}.",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(source.read_bytes())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        copied = True
    if suffix == ".xp":
        baked = subprocess.run(
            [sys.executable, str(XP_BAKER_PATH), str(destination)], cwd=REPO_ROOT,
            text=True, capture_output=True, check=False,
        )
        if baked.returncode != 0:
            if copied:
                destination.unlink(missing_ok=True)
            raise MapEditError(
                "XP runtime bake failed: " + (baked.stderr.strip() or baked.stdout.strip()))
        details["runtime_baked"] = True
    return {
        "$schema": "fl4839_asset_import.v1", "ok": True,
        "map": _rel(selected_map), "kind": "mesh" if suffix == ".akm" else "sprite",
        "asset": source.name, "destination": _rel(destination), "copied": copied,
        "sha256": _sha256_path(destination), **details,
    }


def content_catalog(kind: str, query: str, offset: int, limit: int) -> dict[str, Any]:
    """Expose every placeable item row from the two existing content owners.

    The classic A3D prototype table owns ids 0..40. The packaged appearance
    bundle owns the newer item/style/mount rows. The editor presents their
    union and records the source; it does not create a third registry.
    """
    _require(APPEARANCE_BUNDLE_PATH.is_file(),
             f"packaged appearance bundle is missing: {APPEARANCE_BUNDLE_PATH}")
    bundle = json.loads(APPEARANCE_BUNDLE_PATH.read_text())
    catalog = bundle.get("catalog", {})
    presentation_rows = list(catalog.get("presentation_kinds", []))
    presentation_by_slug = {str(row.get("slug", "")): int(row.get("id", 0))
                            for row in presentation_rows}
    world_item_kind = presentation_by_slug.get("world_item", 603)
    rows: list[dict[str, Any]] = []
    authority_keys: set[tuple[int, int, int]] = set()
    for item in catalog.get("item_definitions", []):
        visuals = item.get("world_visuals", []) or [{}]
        for visual in visuals:
            asset = visual.get("asset", {}) if isinstance(visual, dict) else {}
            row = {
                "source": "appearance_bundle",
                "item_definition_id": int(item.get("id", 0)),
                "slug": str(item.get("slug", "")),
                "label": str(item.get("label", item.get("slug", ""))),
                "gameplay_kind": str(item.get("gameplay_kind", "")),
                "visual_style_id": int(visual.get("visual_style_id", 500)),
                "visual_style_slug": str(visual.get("visual_style_slug", "default")),
                "presentation_kind_id": world_item_kind,
                "mount_definition_id": int(item.get("mount_definition_id", 0)),
                "mount_definition_slug": str(item.get("mount_definition_slug", "")),
                "world_sprite": Path(str(asset.get("path", ""))).name,
            }
            authority_keys.add((row["item_definition_id"], row["visual_style_id"],
                                row["presentation_kind_id"]))
            rows.append(row)
    spawn_compiler = _load("fl4839_content_table", SPAWN_COMPILER_PATH)
    kind_names = dict(getattr(spawn_compiler, "KIND_NAMES", {}))
    for definition, (code, slug, label) in enumerate(
            getattr(spawn_compiler, "ITEM_PROTOTYPES", [])):
        rows.append({
            "source": "classic_a3d_table", "item_definition_id": definition,
            "slug": str(slug), "label": str(label),
            "gameplay_kind": str(kind_names.get(code, code)),
            "visual_style_id": 500, "visual_style_slug": "default",
            "presentation_kind_id": world_item_kind,
            "mount_definition_id": 0, "mount_definition_slug": "",
            "world_sprite": "",
        })
    for label, definition, style, presentation in ITEM_BUNDLES:
        key = (definition, style, presentation)
        if key in authority_keys:
            continue
        rows.append({
            "source": "native_editor_bundle", "item_definition_id": definition,
            "slug": "", "label": label, "gameplay_kind": "bundle",
            "visual_style_id": style, "visual_style_slug": "",
            "presentation_kind_id": presentation,
            "mount_definition_id": 0, "mount_definition_slug": "",
            "world_sprite": "",
        })
    if kind == "mountable":
        rows = [row for row in rows if row["gameplay_kind"] == "mountable"]
    elif kind == "authority":
        rows = [row for row in rows if row["source"] == "appearance_bundle"]
    elif kind == "classic":
        rows = [row for row in rows if row["source"] == "classic_a3d_table"]
    _require(kind in {"all", "mountable", "authority", "classic"},
             f"unknown content kind: {kind}")
    rows.sort(key=lambda row: (str(row["source"]), int(row["item_definition_id"]),
                               int(row["visual_style_id"])))
    return {
        "$schema": "fl4839_content_catalog.v1", "kind": kind,
        "bundle_path": _rel(APPEARANCE_BUNDLE_PATH),
        "bundle_hash": str(bundle.get("bundle_hash", "")),
        "mount_definitions": catalog.get("mount_definitions", []),
        "visual_styles": catalog.get("visual_styles", []),
        "presentation_kinds": presentation_rows,
        **_bounded_rows(rows, query, offset, limit),
    }


def template_gallery(query: str, offset: int, limit: int) -> dict[str, Any]:
    """List installed A3D documents that can seed the existing New workflow."""
    rows: list[dict[str, Any]] = []
    for path in sorted((REPO_ROOT / "godot_project/assets/maps").glob("*.a3d")):
        try:
            data = read_placements(path)
        except Exception as exc:
            rows.append({"map_id": path.stem, "path": _rel(path), "usable": False,
                         "reason": f"{type(exc).__name__}: {exc}"})
            continue
        rows.append({"map_id": path.stem, "path": _rel(path), "usable": True,
                     "world_format_version": data["world_format_version"],
                     "counts": data["counts"], "sha256": data["map_sha256"]})
    return {"$schema": "fl4839_template_gallery.v1",
            **_bounded_rows(rows, query, offset, limit)}


def barrier_plan(map_path: Path, mesh: str, half_width: float, spacing: float,
                 runtime_terrain: Path | None = None) -> dict[str, Any]:
    """Plan ordinary A3D mesh operations along the authored closed course."""
    _require(mesh.endswith(".akm") and Path(mesh).name == mesh,
             "barrier mesh must be an .akm basename")
    _require(math.isfinite(half_width) and 0 < half_width <= 128,
             "barrier half-width must be within 0..128")
    _require(math.isfinite(spacing) and 0.5 <= spacing <= 128,
             "barrier spacing must be within 0.5..128")
    data = read_placements(map_path, runtime_terrain)
    points = _race_group(data["minimap_markers"], "race_path_")
    if len(points) < 3:
        points = _race_group(data["minimap_markers"], "race_gate_")
    _require(len(points) >= 2, "barrier planning requires at least two gates or three path points")
    terrain_path = (runtime_terrain or map_path).resolve()
    placement = _load("fl4839_barrier_terrain", PLACEMENT_COMPILER_PATH)
    terrain = placement._decode_terrain(terrain_path)
    centre = tuple(terrain["center_offset"])
    existing = [str(row.get("name", "")) for row in data["meshes"]]
    next_index = 0
    for name in existing:
        match = re.fullmatch(r"fl4880_barrier_(\d+)", name)
        if match:
            next_index = max(next_index, int(match.group(1)) + 1)
    ops: list[dict[str, Any]] = []
    for edge_index, point in enumerate(points):
        start = point["world_xz"]
        end = points[(edge_index + 1) % len(points)]["world_xz"]
        dx, dz = float(end[0]) - float(start[0]), float(end[1]) - float(start[1])
        length = math.hypot(dx, dz)
        _require(length > 1e-6, f"barrier leg {edge_index} is degenerate")
        tx, tz = dx / length, dz / length
        nx, nz = -tz, tx
        samples = max(1, int(math.ceil(length / spacing)))
        for sample in range(samples):
            along = (sample + 0.5) / samples
            cx, cz = float(start[0]) + dx * along, float(start[1]) + dz * along
            for side in (-1.0, 1.0):
                x, z = cx + nx * half_width * side, cz + nz * half_width * side
                ground = placement._sample_terrain_height(
                    terrain, x + centre[0], z + centre[1])
                _require(ground is not None,
                         f"barrier lies outside authored terrain at ({x:.3f}, {z:.3f})")
                ops.append({
                    "op": "add_mesh", "mesh": mesh,
                    "name": f"fl4880_barrier_{next_index + len(ops)}",
                    "world_xz": [x, z], "godot_y": float(ground),
                    "yaw_degrees": math.degrees(math.atan2(tx, tz)), "scale": 1.0,
                })
    mesh_path = _map_product_paths(map_path.resolve())["mesh_root"] / mesh
    if not mesh_path.is_file():
        mesh_path = REPO_ROOT / "assets/meshes" / mesh
    _require(mesh_path.is_file(), f"barrier mesh is not installed: {mesh}")
    reader = _load("fl4839_barrier_akm_reader",
                   REPO_ROOT / "scripts/bake_akm_to_static_world_faces.py")
    _vertices, faces = reader.parse_akm_ply(str(mesh_path))
    return {
        "$schema": "fl4839_barrier_plan.v1", "map": _rel(map_path.resolve()),
        "mesh": mesh, "half_width": half_width, "spacing": spacing,
        "segment_count": len(points), "barrier_count": len(ops),
        "face_cost_each": len(faces), "face_cost_total": len(faces) * len(ops),
        "delta": _delta_from_ops(ops),
    }


def _json_argument(raw: str, label: str) -> Any:
    if raw.startswith("@"):
        path = Path(raw[1:])
        _require(path.is_file(), f"{label} file does not exist: {path}")
        raw = path.read_text()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MapEditError(f"{label} must be JSON or @path: {exc}") from exc


def _inline_operation(args: argparse.Namespace) -> dict[str, Any]:
    _require(args.op in OPERATION_SPECS, f"unknown operation: {args.op}")
    params: dict[str, Any] = {}
    if args.params:
        decoded = _json_argument(args.params, "params")
        _require(isinstance(decoded, dict), "params must decode to an object")
        params.update(decoded)
    for assignment in args.set_values:
        _require("=" in assignment, f"--set requires KEY=JSON: {assignment}")
        key, raw = assignment.split("=", 1)
        _require(bool(key), "--set key cannot be empty")
        params[key] = _json_argument(raw, f"--set {key}")
    if args.at is not None:
        params["world_xz"] = list(args.at)
    if args.height is not None:
        params["godot_y"] = args.height
    if args.asset is not None:
        field = "sprite" if args.op == "add_sprite" else "mesh"
        params[field] = args.asset
    if args.index is not None:
        field = "source_generator_index" if "enemy_generator" in args.op else "source_instance_index"
        params[field] = args.index
    if args.name is not None:
        params["name"] = args.name
    params["op"] = args.op
    return params


def _delta_from_ops(ops: Any) -> dict[str, Any]:
    _require(isinstance(ops, list) and all(isinstance(row, dict) for row in ops),
             "ops must be a JSON array of objects")
    return {"$schema": DELTA_SCHEMA, "ops": ops}


def validate_delta(map_path: Path, delta: dict[str, Any],
                   runtime_terrain: Path | None = None) -> dict[str, Any]:
    # preview_only executes the exact ordered writer in memory and therefore
    # validates placement as well as terrain operations without writing bytes.
    apply_delta(map_path, delta, runtime_terrain=runtime_terrain, preview_only=True)
    kinds = sorted({OP_RECORD_KIND[str(row["op"])] for row in delta["ops"]})
    return {"$schema": "fl4839_agent_validation.v1", "ok": True,
            "map_path": _rel(map_path.resolve()), "op_count": len(delta["ops"]),
            "record_kinds": kinds, "writes": False}


def _race_group(rows: list[dict[str, Any]], prefix: str) -> list[dict[str, Any]]:
    selected = [dict(row) for row in rows if str(row.get("name", "")).startswith(prefix)]
    selected.sort(key=lambda row: int(str(row["name"])[len(prefix):]))
    return selected


def validate_race(map_path: Path, runtime_terrain: Path | None = None) -> dict[str, Any]:
    markers = read_placements(map_path, runtime_terrain)["minimap_markers"]
    gates = _race_group(markers, "race_gate_")
    grid = _race_group(markers, "race_grid_")
    path = _race_group(markers, "race_path_")
    errors: list[str] = []
    if len(gates) < 2:
        errors.append("at least two race gates are required")
    if not grid:
        errors.append("at least one race grid slot is required")
    if len(grid) > 8:
        errors.append("race grid cannot exceed 8 slots")
    for prefix, group in (("race_gate_", gates), ("race_grid_", grid), ("race_path_", path)):
        if any(row["name"] != f"{prefix}{index}" for index, row in enumerate(group)):
            errors.append(f"{prefix} indices must be contiguous from zero")
    for index, row in enumerate(gates):
        nxt = gates[(index + 1) % len(gates)] if gates else row
        if math.dist(row["world_xz"], nxt["world_xz"]) <= 0.000001:
            errors.append(f"race gate leg {index} is degenerate")
    if gates:
        first = gates[0]["world_xz"]
        tangent = None
        if len(gates) >= 2:
            dx, dz = gates[1]["world_xz"][0] - first[0], gates[1]["world_xz"][1] - first[1]
            length = math.hypot(dx, dz)
            tangent = (dx / length, dz / length) if length > 0.000001 else None
        for row in grid:
            dx, dz = first[0] - row["world_xz"][0], first[1] - row["world_xz"][1]
            length = math.hypot(dx, dz)
            if length <= 0.000001:
                errors.append("race grid slot faces an undefined gate zero")
            elif tangent and (dx / length) * tangent[0] + (dz / length) * tangent[1] <= 0:
                errors.append(f"race grid slot {row['name']} faces away from the first race leg")
    return {"$schema": "fl4839_race_validation.v1", "ok": not errors, "errors": errors,
            "gates": len(gates), "grid_slots": len(grid), "path_points": len(path),
            "radius": 5.0}


def race_build_delta(map_path: Path, runtime_terrain: Path | None = None) -> dict[str, Any]:
    markers = read_placements(map_path, runtime_terrain)["minimap_markers"]
    path = _race_group(markers, "race_path_")
    _require(len(path) >= 3, "track requires at least three race_path control points")
    rows = [dict(row) for row in markers
            if _race_marker_name(str(row.get("name", "")))
            and not str(row["name"]).startswith(("race_gate_", "race_grid_"))]
    for index, point in enumerate(path):
        rows.append({"name": f"race_gate_{index}", "label": f"Gate {index}",
                     "world_xz": point["world_xz"], "marker_type": 0, "glyph": "G", "fg": 226})
    first, second = path[0]["world_xz"], path[1]["world_xz"]
    dx, dz = second[0] - first[0], second[1] - first[1]
    length = math.hypot(dx, dz)
    _require(length > 0.000001, "first track leg is degenerate")
    tx, tz = dx / length, dz / length
    lx, lz = -tz, tx
    properties = _race_path_payload(path[0].get("label", ""))
    width = float(properties["width"])
    for slot in range(4):
        row_index, side = slot // 2, (-1.0 if slot % 2 == 0 else 1.0)
        back = max(6.0, width) + row_index * 3.0
        rows.append({"name": f"race_grid_{slot}", "label": f"Start grid {slot + 1}",
                     "world_xz": [first[0] - tx * back + lx * side * 1.25,
                                  first[1] - tz * back + lz * side * 1.25],
                     "marker_type": 0, "glyph": "S", "fg": 226})
    return _delta_from_ops([{"op": "replace_race_markers", "markers": rows}])


def _race_path_payload(label: str) -> dict[str, Any]:
    result: dict[str, Any] = {"profile": "road", "width": 10.0,
                              "elevation": 0.0, "bank": 0.0}
    if not label.startswith("route.v1|"):
        return result
    for field in label.split("|")[1:]:
        key, separator, value = field.partition("=")
        if not separator:
            continue
        if key == "profile" and value in {"road", "rail"}:
            result[key] = value
        elif key == "width":
            result[key] = min(64.0, max(2.0, float(value)))
        elif key == "elevation":
            result[key] = min(128.0, max(-32.0, float(value)))
        elif key == "bank":
            result[key] = min(60.0, max(-60.0, float(value)))
    return result


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _valid_runtime_map_id(map_id: str) -> bool:
    return re.fullmatch(r"[a-z0-9_]+", map_id) is not None


def _map_product_paths(map_path: Path) -> dict[str, Path]:
    """Return the shipped product names selected by the runtime map catalog."""
    map_path = map_path.resolve()
    canonical = map_path.name == "game_map_y8.a3d"
    stem = map_path.stem
    return {
        "tree": REPO_ROOT / ("godot_project/assets/world_mesh/fl4708_y8_tree_instances.v1.json"
                              if canonical else
                              f"godot_project/assets/world_mesh/{stem}.fl4708_tree_instances.v1.json"),
        "spawn": REPO_ROOT / f"godot_project/assets/world_spawns/{stem}.fl4798_map_spawns.v1.json",
        "catalog": REPO_ROOT / ("godot_project/assets/world_partition/fl4789_chunk_catalog.v1.json"
                                 if canonical else
                                 f"godot_project/assets/world_partition/{stem}.fl4789_chunk_catalog.v1.json"),
        "payload": REPO_ROOT / ("godot_project/assets/world_partition/fl4789_chunk_assets.v1.bin"
                                 if canonical else
                                 f"godot_project/assets/world_partition/{stem}.fl4789_chunk_assets.v1.bin"),
        "deck": REPO_ROOT / ("godot_project/assets/world_mesh/bridge_deck_slabs.v1.json"
                              if canonical else
                              f"godot_project/assets/world_mesh/{stem}.bridge_deck_slabs.v1.json"),
        # FL-4839/FL-4876: generated AKMs are map-owned. Flattening them into
        # assets/meshes would let equal basenames with different bytes silently
        # change another map (observed: bollard, bus_stop and street_lamp).
        "mesh_root": REPO_ROOT / f"godot_project/assets/world_mesh/{stem}.meshes",
        "sprite_root": map_path.with_suffix(".sprites"),
        "trains": map_path.with_suffix(".trains.json"),
        "signs": map_path.with_suffix(".signs.json"),
    }


def rebuild_products(map_path: Path, runtime_terrain: Path | None = None) -> dict[str, Any]:
    """Run the same existing product owners as MapEditSession Save."""
    map_path = map_path.resolve()
    runtime = (runtime_terrain or map_path).resolve()
    maps_root = (REPO_ROOT / "godot_project/assets/maps").resolve()
    _require(map_path.parent == maps_root, "product rebuild map must be directly under godot_project/assets/maps")
    canonical = map_path.name == "game_map_y8.a3d"
    paths = _map_product_paths(map_path)
    map_owned_meshes = paths["mesh_root"].is_dir()
    tree_command = [sys.executable, str(TREE_COMPILER_PATH), "--source", str(map_path),
                    "--runtime-terrain", str(runtime), "--output", str(paths["tree"])]
    if map_owned_meshes:
        # OSM/pipeline maps may intentionally contain no canonical Y8 trees.
        # The explicit map-owned mesh root is the admission fact; ordinary maps
        # keep the stricter historical behavior.
        tree_command.append("--allow-no-trees")
    chunk_command = [sys.executable, str(CHUNK_COMPILER_PATH), "--map", str(map_path),
                     "--tree-manifest", str(paths["tree"]),
                     "--output", str(paths["catalog"]),
                     "--payload", str(paths["payload"]), "--fixtures",
                     *([] if canonical else ["--no-lab-grove", "--no-meadow"])]
    if map_owned_meshes:
        chunk_command.extend(["--mesh-root", str(paths["mesh_root"])])
    commands = [
        tree_command,
        [sys.executable, str(SPAWN_COMPILER_PATH), "--source", str(map_path),
         "--runtime-terrain", str(runtime), "--output", str(paths["spawn"])],
        chunk_command,
        [sys.executable, str(DECK_BAKER_PATH), "--catalog", str(paths["catalog"]),
         "--payload", str(paths["payload"]), "--output", str(paths["deck"])],
    ]
    if canonical:
        commands.append([sys.executable, str(ACTOR_CATALOG_COMPILER_PATH)])
    receipts = []
    for command in commands:
        completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True)
        receipt = {"argv": command, "exit": completed.returncode,
                   "stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:]}
        receipts.append(receipt)
        if completed.returncode != 0:
            raise MapEditError(f"product rebuild failed: {Path(command[1]).name}: {completed.stderr or completed.stdout}")
    return {"$schema": "fl4839_product_rebuild.v1", "ok": True,
            "map": _rel(map_path), "products": len(receipts), "receipts": receipts,
            "map_owned_mesh_root": _rel(paths["mesh_root"]) if map_owned_meshes else "",
            "pipeline_import": map_owned_meshes}


def _bundle_sources(run_root: Path, receipt_path: Path | None = None) -> dict[str, Path]:
    """Resolve the conventional pipeline bundle, with optional receipt overrides."""
    run_root = run_root.resolve()
    values: dict[str, Any] = {}
    if receipt_path is not None:
        _require(receipt_path.is_file(), f"build receipt does not exist: {receipt_path}")
        try:
            document = json.loads(receipt_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise MapEditError(f"build receipt is unreadable: {exc}") from exc
        _require(isinstance(document, dict), "build receipt must be a JSON object")
        products = document.get("products", {})
        if isinstance(products, dict):
            values.update(products)
        values.update({key: value for key, value in document.items()
                       if key in {"map", "map_path", "output_a3d", "mesh_root", "sprite_root"}})

    def selected(keys: tuple[str, ...], fallback: Path) -> Path:
        for key in keys:
            value = values.get(key)
            if isinstance(value, str) and value:
                candidate = Path(value)
                return candidate.resolve() if candidate.is_absolute() else (run_root / candidate).resolve()
        return fallback.resolve()

    map_source = selected(("map", "map_path", "output_a3d"), run_root / "output.a3d")
    return {
        "map": map_source,
        "mesh_root": selected(("mesh_root",), run_root / "meshes"),
        "sprite_root": selected(("sprite_root",), map_source.with_suffix(".sprites")),
        "trains": map_source.with_suffix(".trains.json"),
        "signs": map_source.with_suffix(".signs.json"),
    }


def _referenced_asset_names(map_path: Path) -> tuple[list[str], list[str]]:
    rows = read_placements(map_path)
    meshes = sorted({Path(str(row["mesh"])).name for row in rows["meshes"]
                     if str(row.get("mesh", ""))})
    sprites = sorted({Path(str(row["sprite"])).stem for row in rows["sprites"]
                      if str(row.get("sprite", ""))})
    _require(all(name == Path(name).name and name.lower().endswith(".akm") for name in meshes),
             "map contains an unsafe or non-AKM mesh reference")
    return meshes, sprites


def _bundle_sprite_names(sprite_root: Path) -> list[str]:
    if not sprite_root.is_dir():
        return []
    return sorted({path.stem for path in sprite_root.rglob("*.xp") if path.is_file()})


def _sign_record_count(signs_path: Path) -> int:
    if not signs_path.is_file():
        return 0
    try:
        document = json.loads(signs_path.read_text())
    except (OSError, json.JSONDecodeError):
        return 0
    rows = document.get("signs", []) if isinstance(document, dict) else []
    return len(rows) if isinstance(rows, list) else 0


def verify_loadability(map_path: Path) -> dict[str, Any]:
    """Audit the exact installed package consumed by Godot Play and the editor."""
    map_path = map_path.resolve()
    maps_root = (REPO_ROOT / "godot_project/assets/maps").resolve()
    _require(map_path.is_file(), f"installed map does not exist: {map_path}")
    _require(map_path.parent == maps_root, "loadability map must be directly under godot_project/assets/maps")
    paths = _map_product_paths(map_path)
    map_sha = _sha256_path(map_path)
    mesh_names, embedded_sprite_names = _referenced_asset_names(map_path)
    shared_mesh_root = REPO_ROOT / "assets/meshes"
    mesh_owned = [name for name in mesh_names if (paths["mesh_root"] / name).is_file()]
    mesh_shared = [name for name in mesh_names
                   if name not in mesh_owned and (shared_mesh_root / name).is_file()]
    mesh_missing = [name for name in mesh_names
                    if name not in mesh_owned and name not in mesh_shared]

    product_rows: dict[str, Any] = {}
    products_ok = True
    for name in ("tree", "spawn", "catalog", "payload", "deck"):
        path = paths[name]
        row: dict[str, Any] = {"path": _rel(path), "exists": path.is_file()}
        if path.is_file():
            row["bytes"] = path.stat().st_size
            row["sha256"] = _sha256_path(path)
        products_ok = products_ok and bool(row["exists"])
        product_rows[name] = row

    product_identity_ok = True
    stable_mesh_provenance = True
    for name in ("tree", "spawn"):
        path = paths[name]
        if not path.is_file():
            product_identity_ok = False
            continue
        try:
            source_sha = str(json.loads(path.read_text()).get("source_a3d_sha256", ""))
        except (OSError, json.JSONDecodeError):
            source_sha = ""
        product_rows[name]["source_map_matches"] = source_sha == map_sha
        product_identity_ok = product_identity_ok and source_sha == map_sha
    if paths["catalog"].is_file():
        try:
            catalog = json.loads(paths["catalog"].read_text())
            header = catalog.get("header", {}) if isinstance(catalog, dict) else {}
        except (OSError, json.JSONDecodeError):
            header = {}
        catalog_matches = str(header.get("source_map_sha256", "")) == map_sha
        payload_declared = header.get("asset_payload", {})
        payload_matches = (paths["payload"].is_file()
                           and isinstance(payload_declared, dict)
                           and str(payload_declared.get("sha256", "")) == _sha256_path(paths["payload"]))
        stable_root = str(paths["mesh_root"].resolve())
        source_rows = list(header.get("fixture_mesh_sources", [])) + list(header.get("tree_mesh_sources", []))
        stable_sources = all(str(row.get("path", "")).startswith(stable_root + os.sep)
                             for row in source_rows if isinstance(row, dict))
        product_rows["catalog"].update({"source_map_matches": catalog_matches,
                                        "payload_matches": payload_matches,
                                        "stable_mesh_provenance": stable_sources})
        product_identity_ok = product_identity_ok and catalog_matches and payload_matches
        stable_mesh_provenance = stable_sources
    else:
        product_identity_ok = False
        stable_mesh_provenance = False

    source_sprite_root = paths["sprite_root"]
    bundle_sprite_names = _bundle_sprite_names(source_sprite_root)
    sprite_names = sorted(set(embedded_sprite_names) | set(bundle_sprite_names))
    sign_records = _sign_record_count(paths["signs"])
    sign_sprite_bundle_missing = sign_records > 0 and not bundle_sprite_names
    shared_sprite_root = REPO_ROOT / "assets/sprites"
    baked_sprite_root = REPO_ROOT / "godot_project/assets/xp_sprites"
    sprite_source_missing = [name for name in sprite_names
                             if not (source_sprite_root / f"{name}.xp").is_file()
                             and not (shared_sprite_root / f"{name}.xp").is_file()]
    sprite_baked_missing = [name for name in sprite_names
                            if not (baked_sprite_root / f"{name}.json").is_file()
                            or not (baked_sprite_root / f"{name}.png").is_file()]
    # The runtime consumes the compiled payload and needs matching product
    # hashes. The editor additionally needs source AKMs and stable provenance so
    # a Save can rebuild those products after the pipeline scratch tree is gone.
    runtime_ready = products_ok and product_identity_ok
    editor_ready = runtime_ready and not mesh_missing and stable_mesh_provenance
    return {
        "$schema": "fl4839_godot_loadability.v1", "ok": editor_ready,
        "map": _rel(map_path), "map_sha256": map_sha,
        "map_id": map_path.stem, "auto_discovered_by_catalog": True,
        "runtime_ready": runtime_ready, "editor_ready": editor_ready,
        "product_rebuild_ready": editor_ready,
        "sprite_visuals_ready": (not sprite_source_missing and not sprite_baked_missing
                                 and not sign_sprite_bundle_missing),
        "meshes": {"referenced": len(mesh_names), "map_owned": len(mesh_owned),
                   "shared_fallback": len(mesh_shared), "missing_count": len(mesh_missing),
                   "missing_sample": mesh_missing[:20]},
        "sprites": {"referenced_in_a3d": len(embedded_sprite_names),
                    "bundle_sources": len(bundle_sprite_names),
                    "sign_records": sign_records,
                    "sign_sprite_bundle_missing": sign_sprite_bundle_missing,
                    "source_missing_count": len(sprite_source_missing),
                    "source_missing_sample": sprite_source_missing[:20],
                    "godot_bake_missing_count": len(sprite_baked_missing),
                    "godot_bake_missing_sample": sprite_baked_missing[:20]},
        "products": product_rows,
    }


def install_build(run_root: Path, map_id: str, receipt_path: Path | None,
                  replace: bool, dry_run: bool) -> dict[str, Any]:
    """Install one pipeline output as an isolated, editable Godot map package."""
    _require(_valid_runtime_map_id(map_id), "map id must match [a-z0-9_]+")
    _require(map_id != "game_map_y8", "pipeline install cannot replace the canonical Y8 map")
    sources = _bundle_sources(run_root, receipt_path)
    _require(sources["map"].is_file(), f"pipeline map is missing: {sources['map']}")
    mesh_names, embedded_sprite_names = _referenced_asset_names(sources["map"])
    bundle_sprite_names = _bundle_sprite_names(sources["sprite_root"])
    sprite_names = sorted(set(embedded_sprite_names) | set(bundle_sprite_names))
    shared_mesh_root = REPO_ROOT / "assets/meshes"
    mesh_inputs: dict[str, Path] = {}
    for name in mesh_names:
        preferred = sources["mesh_root"] / name
        fallback = shared_mesh_root / name
        if preferred.is_file():
            mesh_inputs[name] = preferred
        elif fallback.is_file():
            mesh_inputs[name] = fallback
    missing_meshes = [name for name in mesh_names if name not in mesh_inputs]
    _require(not missing_meshes,
             f"pipeline bundle is missing {len(missing_meshes)} referenced AKMs; first: {missing_meshes[:5]}")

    target_map = REPO_ROOT / f"godot_project/assets/maps/{map_id}.a3d"
    targets = _map_product_paths(target_map)
    occupied = [target_map, targets["mesh_root"], targets["sprite_root"],
                targets["trains"], targets["signs"], targets["tree"], targets["spawn"],
                targets["catalog"], targets["payload"], targets["deck"]]
    existing = [path for path in occupied if path.exists()]
    _require(replace or not existing,
             f"map package already exists; pass --replace: {[ _rel(path) for path in existing[:5] ]}")
    plan = {"$schema": "fl4839_pipeline_install.v1", "ok": True,
            "executed": not dry_run, "map_id": map_id, "source_map": str(sources["map"]),
            "target_map": _rel(target_map), "mesh_count": len(mesh_inputs),
            "sprite_count": len(sprite_names), "replace": replace}
    if dry_run:
        return plan

    run_scratch = REPO_ROOT / ".run"
    run_scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fl4839-install-", dir=run_scratch) as temporary:
        stage = Path(temporary) / "stage"
        backup = Path(temporary) / "backup"
        staged_map = stage / target_map.name
        staged_meshes = stage / targets["mesh_root"].name
        staged_map.parent.mkdir(parents=True, exist_ok=True)
        staged_meshes.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sources["map"], staged_map)
        for name, source in mesh_inputs.items():
            shutil.copy2(source, staged_meshes / name)
        staged_optional: dict[Path, Path] = {}
        for key in ("sprite_root", "trains", "signs"):
            source = sources[key]
            if not source.exists():
                continue
            staged = stage / ("optional_" + key)
            shutil.copytree(source, staged) if source.is_dir() else shutil.copy2(source, staged)
            staged_optional[targets[key]] = staged

        backups: dict[Path, Path] = {}
        installed: list[Path] = []
        try:
            backup.mkdir(parents=True, exist_ok=True)
            for index, target in enumerate(occupied):
                if target.exists():
                    saved = backup / str(index)
                    os.replace(target, saved)
                    backups[target] = saved
            target_map.parent.mkdir(parents=True, exist_ok=True)
            targets["mesh_root"].parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged_map, target_map)
            installed.append(target_map)
            os.replace(staged_meshes, targets["mesh_root"])
            installed.append(targets["mesh_root"])
            for target, staged in staged_optional.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, target)
                installed.append(target)
            products = rebuild_products(target_map)
            installed.extend([targets[key] for key in ("tree", "spawn", "catalog", "payload", "deck")])
            audit = verify_loadability(target_map)
            _require(bool(audit["editor_ready"]), "installed package failed Godot editor loadability audit")
        except Exception:
            for target in reversed(occupied):
                if target.exists():
                    shutil.rmtree(target) if target.is_dir() else target.unlink()
            for target, saved in backups.items():
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(saved, target)
            raise
    plan["products"] = products
    plan["loadability"] = audit
    return plan


def launch_recipe(map_id: str, mode: str, windowed: bool,
                  presentation_mode: str) -> dict[str, Any]:
    _require(re.fullmatch(r"[A-Za-z0-9_.-]+", map_id) is not None, "map id contains unsafe characters")
    _require((REPO_ROOT / f"godot_project/assets/maps/{map_id}.a3d").is_file(),
             f"map id does not exist: {map_id}")
    _require(mode in {"play", "race"}, "mode must be play or race")
    argv = [sys.executable, str(PLAY_LAUNCHER_PATH), "--map-id", map_id,
            f"--extra-user-arg=--presentation-mode={presentation_mode}"]
    if windowed:
        argv.append("--windowed")
    if mode == "race":
        argv.append("--extra-user-arg=--car-race")
    return {"$schema": "fl4839_launch_recipe.v1", "map_id": map_id, "mode": mode,
            "argv": argv, "shell": shlex.join(argv), "executed": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    ops = sub.add_parser("operations", help="list every accepted edit operation and field")
    ops.add_argument("--op", choices=sorted(OPERATION_SPECS))

    ins = sub.add_parser("inspect", help="bounded map summary or filtered record rows")
    ins.add_argument("--map", type=Path, required=True)
    ins.add_argument("--runtime-terrain", type=Path)
    ins.add_argument("--kind", default="summary",
                     choices=["summary", "mesh", "item", "sprite", "enemy", "marker", "player_start"])
    ins.add_argument("--query", default="")
    ins.add_argument("--offset", type=int, default=0)
    ins.add_argument("--limit", type=int, default=50)

    assets = sub.add_parser("assets", help="bounded AKM, XP or item-preset discovery")
    assets.add_argument("--kind", default="all", choices=["all", "mesh", "sprite", "item"])
    assets.add_argument("--query", default="")
    assets.add_argument("--offset", type=int, default=0)
    assets.add_argument("--limit", type=int, default=50)
    assets.add_argument("--map", type=Path,
                        help="include assets owned by this installed map bundle")

    asset_import = sub.add_parser(
        "import-asset", help="validate and copy one AKM/XP into an installed map bundle")
    asset_import.add_argument("--map", type=Path, required=True)
    asset_import.add_argument("--source", type=Path, required=True)

    def add_inline_edit_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--map", type=Path, required=True)
        command.add_argument("--runtime-terrain", type=Path)
        command.add_argument("--op", required=True, choices=sorted(OPERATION_SPECS))
        command.add_argument("--params", default="", help="JSON object or @path")
        command.add_argument("--set", dest="set_values", action="append", default=[], metavar="KEY=JSON")
        command.add_argument("--at", type=float, nargs=2, metavar=("X", "Z"))
        command.add_argument("--height", type=float)
        command.add_argument("--asset", help="mesh/sprite basename shortcut")
        command.add_argument("--index", type=int, help="source instance/generator index shortcut")
        command.add_argument("--name")
        command.add_argument("--out", type=Path)
        command.add_argument("--preview", action="store_true", help="validate exact operation without writing")
        command.add_argument("--rebuild-products", action="store_true")

    edit = sub.add_parser("edit", help="apply one inline operation; no delta file required")
    add_inline_edit_arguments(edit)

    batch = sub.add_parser("batch", help="apply an inline JSON operation array as one transaction")
    batch.add_argument("--map", type=Path, required=True)
    batch.add_argument("--runtime-terrain", type=Path)
    batch.add_argument("--ops", required=True, help="JSON array or @path")
    batch.add_argument("--out", type=Path)
    batch.add_argument("--preview", action="store_true")
    batch.add_argument("--rebuild-products", action="store_true")

    val = sub.add_parser("validate", help="execute an inline operation array in memory without writes")
    val.add_argument("--map", type=Path, required=True)
    val.add_argument("--runtime-terrain", type=Path)
    val.add_argument("--ops", required=True, help="JSON array or @path")

    race_val = sub.add_parser("race-validate", help="validate the saved path/gate/grid course")
    race_val.add_argument("--map", type=Path, required=True)
    race_val.add_argument("--runtime-terrain", type=Path)

    race_build = sub.add_parser("race-build", help="derive gates and four grid slots from race_path_N")
    race_build.add_argument("--map", type=Path, required=True)
    race_build.add_argument("--runtime-terrain", type=Path)
    race_build.add_argument("--out", type=Path)
    race_build.add_argument("--preview", action="store_true")
    race_build.add_argument("--rebuild-products", action="store_true")

    products = sub.add_parser("rebuild-products", help="run the same product builders as editor Save")
    products.add_argument("--map", type=Path, required=True)
    products.add_argument("--runtime-terrain", type=Path)

    loadability = sub.add_parser(
        "verify-loadability", help="audit one installed map package for Godot Play and editor loading")
    loadability.add_argument("--map", type=Path, required=True)

    install = sub.add_parser(
        "install-build", help="install pipeline output.a3d plus assets as an isolated Godot map")
    install.add_argument("--run-root", type=Path,
                         help="pipeline run containing output.a3d and meshes/")
    install.add_argument("--receipt", type=Path,
                         help="optional build receipt; its directory is the default run root")
    install.add_argument("--map-id", required=True)
    install.add_argument("--replace", action="store_true")
    install.add_argument("--dry-run", action="store_true")

    launch = sub.add_parser("launch", help="print or execute the exact-map Play/Race launch recipe")
    launch.add_argument("--map-id", required=True)
    launch.add_argument("--mode", choices=["play", "race"], default="play")
    launch.add_argument("--presentation-mode", choices=["FF", "FC", "CF", "CC"], default="FC")
    launch.add_argument("--windowed", action="store_true")
    launch.add_argument("--execute", action="store_true")

    cap = sub.add_parser("capabilities")
    cap.add_argument("--map", type=Path, required=True)
    cap.add_argument("--output", type=Path)

    rd = sub.add_parser("read")
    rd.add_argument("--map", type=Path, required=True)
    rd.add_argument("--runtime-terrain", type=Path)
    rd.add_argument("--output", type=Path)

    ap = sub.add_parser("apply")
    ap.add_argument("--map", type=Path, required=True)
    ap.add_argument("--delta", type=Path, required=True)
    ap.add_argument("--runtime-terrain", type=Path)
    ap.add_argument("--out", type=Path,
                    help="write here instead of the canonical map (proof runs)")
    ap.add_argument("--no-verify-canonical", action="store_true")

    pv = sub.add_parser("preview-terrain", help="project pending edits without writing map or products")
    pv.add_argument("--map", type=Path, required=True)
    pv.add_argument("--runtime-terrain", type=Path)
    pv.add_argument("--delta", type=Path, required=True)

    nw = sub.add_parser("new", help="create native blank 16x16 terrain at a new path")
    nw.add_argument("--template", type=Path, required=True)
    nw.add_argument("--out", type=Path, required=True)

    mg = sub.add_parser("merge", help="merge native terrain and mesh records into a map")
    mg.add_argument("--map", type=Path, required=True)
    mg.add_argument("--source", type=Path, required=True)
    mg.add_argument("--runtime-terrain", type=Path)
    mg.add_argument("--source-runtime-terrain", type=Path)
    mg.add_argument("--offset-world-xz", type=float, nargs=2, default=(0.0, 0.0), metavar=("X", "Z"))

    sa = sub.add_parser("save-as", help="serialize a map to a distinct output path")
    sa.add_argument("--map", type=Path, required=True)
    sa.add_argument("--out", type=Path, required=True)

    up = sub.add_parser("upgrade", help="FL-4834: raise a map's world format version")
    up.add_argument("--map", type=Path, required=True)
    up.add_argument("--to-version", type=int, default=4,
                    choices=list(UPGRADE_SUPPORTED_TARGETS))
    up.add_argument("--runtime-terrain", type=Path)
    up.add_argument("--out", type=Path,
                    help="write here instead of the canonical map (proof runs)")
    up.add_argument("--player-start-world-xz", type=float, nargs=2, metavar=("X", "Z"))
    up.add_argument("--player-start-godot-y", type=float, default=0.0)
    up.add_argument("--player-start-yaw", type=float, default=0.0)
    up.add_argument("--output", type=Path, help="write the receipt JSON here")

    args = parser.parse_args()
    try:
        if args.command == "operations":
            payload = operation_manifest(args.op or "")
        elif args.command == "inspect":
            _require(args.offset >= 0 and 1 <= args.limit <= 1000,
                     "inspect offset must be >=0 and limit must be 1..1000")
            payload = inspect_map(args.map, args.runtime_terrain, args.kind,
                                  args.query, args.offset, args.limit)
        elif args.command == "assets":
            _require(args.offset >= 0 and 1 <= args.limit <= 1000,
                     "asset offset must be >=0 and limit must be 1..1000")
            payload = asset_catalog(args.kind, args.query, args.offset, args.limit, args.map)
        elif args.command == "import-asset":
            payload = import_asset(args.map, args.source)
        elif args.command == "edit":
            delta = _delta_from_ops([_inline_operation(args)])
            if args.preview:
                payload = validate_delta(args.map, delta, args.runtime_terrain)
            else:
                payload = apply_delta(args.map, delta, args.out, args.runtime_terrain)
                if args.rebuild_products:
                    payload["products"] = rebuild_products(args.out or args.map,
                                                           args.runtime_terrain or args.out or args.map)
        elif args.command in {"batch", "validate"}:
            delta = _delta_from_ops(_json_argument(args.ops, "ops"))
            if args.command == "validate" or args.preview:
                payload = validate_delta(args.map, delta, args.runtime_terrain)
            else:
                payload = apply_delta(args.map, delta, args.out, args.runtime_terrain)
                if args.rebuild_products:
                    payload["products"] = rebuild_products(args.out or args.map,
                                                           args.runtime_terrain or args.out or args.map)
        elif args.command == "race-validate":
            payload = validate_race(args.map, args.runtime_terrain)
            if not payload["ok"]:
                raise MapEditError("; ".join(payload["errors"]))
        elif args.command == "race-build":
            delta = race_build_delta(args.map, args.runtime_terrain)
            if args.preview:
                payload = validate_delta(args.map, delta, args.runtime_terrain)
                payload["derived"] = {"gates": len(read_placements(args.map, args.runtime_terrain)["minimap_markers"]),
                                      "note": "exact replace_race_markers transaction validated"}
            else:
                payload = apply_delta(args.map, delta, args.out, args.runtime_terrain)
                if args.rebuild_products:
                    payload["products"] = rebuild_products(args.out or args.map,
                                                           args.runtime_terrain or args.out or args.map)
        elif args.command == "rebuild-products":
            payload = rebuild_products(args.map, args.runtime_terrain)
        elif args.command == "verify-loadability":
            payload = verify_loadability(args.map)
        elif args.command == "install-build":
            _require(args.run_root is not None or args.receipt is not None,
                     "install-build requires --run-root or --receipt")
            run_root = args.run_root or args.receipt.parent
            payload = install_build(run_root, args.map_id, args.receipt,
                                    args.replace, args.dry_run)
        elif args.command == "launch":
            payload = launch_recipe(args.map_id, args.mode, args.windowed, args.presentation_mode)
            if args.execute:
                completed = subprocess.run(payload["argv"], cwd=REPO_ROOT)
                payload["executed"] = True
                payload["exit"] = completed.returncode
        elif args.command == "capabilities":
            payload = capabilities(args.map)
        elif args.command == "read":
            payload = read_placements(args.map, args.runtime_terrain)
        elif args.command == "upgrade":
            start = None
            if args.player_start_world_xz is not None:
                start = {
                    "world_xz": list(args.player_start_world_xz),
                    "godot_y": args.player_start_godot_y,
                    "yaw": args.player_start_yaw,
                }
            payload = upgrade_world_format(args.map, args.to_version, args.out,
                                           start, args.runtime_terrain)
        elif args.command == "new":
            payload = create_new_map(args.template, args.out)
        elif args.command == "merge":
            payload = merge_map(args.map, args.source, args.runtime_terrain,
                                args.source_runtime_terrain,
                                tuple(args.offset_world_xz))
        elif args.command == "save-as":
            payload = save_as_map(args.map, args.out)
        elif args.command == "preview-terrain":
            delta = json.loads(args.delta.read_text())
            payload = apply_delta(args.map, delta, runtime_terrain=args.runtime_terrain,
                                  preview_only=True)
        else:
            delta = json.loads(args.delta.read_text())
            payload = apply_delta(args.map, delta, args.out, args.runtime_terrain,
                                  not args.no_verify_canonical)
    except MapEditError as exc:
        print(f"MAP_EDIT_REFUSED {exc}", file=sys.stderr)
        return 2

    # Agent-facing commands default to compact JSON to keep context and tool
    # transport bounded. Retain the historical pretty output for the older
    # lifecycle commands so existing human workflows do not churn.
    agent_commands = {"operations", "inspect", "assets", "import-asset", "edit", "batch", "validate",
                      "race-validate", "race-build", "rebuild-products", "verify-loadability",
                      "install-build", "launch"}
    if args.command in agent_commands:
        rendered = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    else:
        rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    output = getattr(args, "output", None)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered)
        print(f"MAP_EDIT_OK output={output}")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
