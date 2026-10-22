"""Validator tool-path ownership: standalone SceneTree validators live under tools/validation."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GODOT = ROOT / "godot_project"
NEW_DIR = GODOT / "tools" / "validation"
OLD_DIR = GODOT / "scripts"

BASENAMES = [
    "validate_actor_layer_missing_fallback.gd",
    "validate_clip_planes.gd",
    "validate_colyseus_adapter.gd",
    "validate_colyseus_scene_quarantine.gd",
    "validate_combat_authority.gd",
    "validate_coordinate_parity.gd",
    "validate_godot_project.gd",
    "validate_light_tuple.gd",
    "validate_live_movement.gd",
    "validate_playable_scene.gd",
    "validate_reflection_pass.gd",
    "validate_resolved_cell_resolver.gd",
    "validate_runtime_slice.gd",
    "validate_sample_buffer.gd",
    "validate_shadow_pass.gd",
    "validate_source_frame_facts.gd",
    "validate_sprite_hud_blit.gd",
    "validate_terrain_query.gd",
    "validate_terrain_sample_pass.gd",
    "validate_water_pass.gd",
    "validate_world_query.gd",
    "validate_world_section_parser.gd",
]

_PRELOAD_RE = re.compile(r'preload\("res://([^"]+)"\)')
_LOAD_RE = re.compile(r'load\("res://([^"]+)"\)')


def test_new_canonical_paths_exist_with_uid_siblings() -> None:
    assert NEW_DIR.is_dir(), f"missing {NEW_DIR}"
    for name in BASENAMES:
        script = NEW_DIR / name
        uid = NEW_DIR / f"{name}.uid"
        assert script.is_file(), f"missing {script}"
        assert uid.is_file(), f"missing {uid}"
        text = uid.read_text(encoding="utf-8").strip()
        assert text.startswith("uid://") and len(text) > len("uid://"), f"bad uid in {uid}"


def test_old_runtime_addresses_absent() -> None:
    leftovers = sorted(OLD_DIR.glob("validate_*.gd")) + sorted(OLD_DIR.glob("validate_*.gd.uid"))
    assert leftovers == [], f"old validator addresses remain: {leftovers}"


def test_validator_bodies_carry_no_old_self_addresses() -> None:
    for name in BASENAMES:
        source = (NEW_DIR / name).read_text(encoding="utf-8")
        assert "res://scripts/validate_" not in source, f"{name} still references old address"


def test_validator_preloads_and_loads_resolve() -> None:
    missing: list[str] = []
    for name in BASENAMES:
        source = (NEW_DIR / name).read_text(encoding="utf-8")
        for match in list(_PRELOAD_RE.finditer(source)) + list(_LOAD_RE.finditer(source)):
            target = GODOT / match.group(1)
            if not target.is_file():
                missing.append(f"{name} -> res://{match.group(1)}")
    assert missing == [], f"unresolved validator references: {missing}"
