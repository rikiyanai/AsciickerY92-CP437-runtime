#!/usr/bin/env python3
"""Compile stable actor-source IDs and per-profile atlas residency sets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILES = (
    REPO_ROOT
    / "colyseus_server/runtime/visual_catalog/current/actor_visual_profiles.compiled.json"
)
DEFAULT_SIDECARS = REPO_ROOT / "godot_project/assets/xp_sprites"
DEFAULT_OUTPUT = DEFAULT_SIDECARS / "fl4789_actor_atlas_catalog.v1.json"
SCHEMA = "fl4789_actor_atlas_catalog.v1"
COMPILER_VERSION = "1.1.0"

# This scope is a residency contract, not a runtime appearance selector. The
# normal-player profile compiler still owns layer selection. The Sekiro lane
# additionally publishes shield, fallback death, and pickup sources that are
# outside that profile matrix, so the offline catalog names them here once.
SEKIRO_SCOPE_NAME = "sekiro_combat_spike"
SEKIRO_PROFILE_SUFFIX = ".unmounted.normal_armour.normal_sword"
SEKIRO_SIDE_CAR_STEMS = (
    "player-shield-regular",
    "attack-0011",
    "plydie-0000",
    "item-armor",
    "item-helmet",
    "item-shield",
    "item-sword",
)


class CompileError(ValueError):
    """A source contract failed; no runtime catalog may be emitted."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _profile_residency_key(profile_id: str) -> str:
    parts = profile_id.split(".")
    if len(parts) < 3:
        raise CompileError(f"profile id has no presentation segment: {profile_id}")
    # Segment 1 is the compiler-owned presentation kind (idle_walk, attack,
    # death, ...). The remaining segments identify the same actor/loadout.
    return ".".join([parts[0], *parts[2:]])


def compile_catalog(profile_path: Path, sidecar_dir: Path) -> dict[str, Any]:
    profile_raw = profile_path.read_bytes()
    profile_doc = json.loads(profile_raw)
    profiles = profile_doc.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        raise CompileError("compiled actor profile catalog has no profiles")

    all_paths: set[str] = set()
    profile_paths: dict[str, set[str]] = {}
    paths_by_residency_key: dict[str, set[str]] = {}
    for profile in profiles:
        profile_id = str(profile.get("id", ""))
        layers = profile.get("layers")
        if not profile_id or not isinstance(layers, list):
            raise CompileError("compiled actor profile has invalid id or layers")
        paths = {
            str(layer.get("baked_atlas_path", ""))
            for layer in layers
            if isinstance(layer, dict) and str(layer.get("baked_atlas_path", ""))
        }
        if not paths:
            raise CompileError(f"profile has no baked atlas paths: {profile_id}")
        profile_paths[profile_id] = paths
        residency_key = _profile_residency_key(profile_id)
        paths_by_residency_key.setdefault(residency_key, set()).update(paths)
        all_paths.update(paths)

    # Sidecars cover authored NPC, spike, and world-item sources that are not
    # members of the normal-player profile matrix. They receive stable IDs too;
    # their current rows request residency explicitly.
    for sidecar_path in sorted(sidecar_dir.glob("*.json")):
        if sidecar_path.name == DEFAULT_OUTPUT.name:
            continue
        sidecar = json.loads(sidecar_path.read_text())
        atlas_path = str(sidecar.get("atlas_png", sidecar.get("atlas_path", "")))
        if atlas_path:
            all_paths.add(atlas_path)

    ordered_paths = sorted(all_paths)
    source_id_by_path = {path: index for index, path in enumerate(ordered_paths)}
    residency_by_profile: dict[str, list[int]] = {}
    for profile_id in sorted(profile_paths):
        residency_paths = paths_by_residency_key[_profile_residency_key(profile_id)]
        residency_by_profile[profile_id] = sorted(
            source_id_by_path[path] for path in residency_paths
        )

    sekiro_scope_paths = {
        path
        for profile_id, paths in profile_paths.items()
        if profile_id.startswith("normal_player.")
        and profile_id.endswith(SEKIRO_PROFILE_SUFFIX)
        for path in paths
    }
    sekiro_scope_paths.update(
        f"res://assets/xp_sprites/{stem}.png" for stem in SEKIRO_SIDE_CAR_STEMS
    )
    # FL-4407/FL-4798: combat rooms retain map-authored static sprites. Their
    # shipped manifests are compiler inputs, so sealing cannot reject a corpse,
    # statue, wolf or fire later when World publishes the map rows.
    map_sources = {}
    for manifest in sorted((REPO_ROOT / "godot_project/assets/world_spawns").glob("*.fl4798_map_spawns.v1.json")):
        raw = manifest.read_bytes()
        map_sources[manifest.relative_to(REPO_ROOT).as_posix()] = _sha256(raw)
        for sprite in json.loads(raw).get("sprites", []):
            stem = Path(str(sprite.get("sprite", ""))).stem
            if stem:
                sekiro_scope_paths.add(f"res://assets/xp_sprites/{stem}.png")
    missing_sekiro_paths = sorted(sekiro_scope_paths.difference(all_paths))
    if missing_sekiro_paths:
        raise CompileError(
            "Sekiro residency scope contains unknown sources: "
            + ", ".join(missing_sekiro_paths)
        )

    catalog: dict[str, Any] = {
        "$schema": SCHEMA,
        "header": {
            "schema_version": 1,
            "compiler_version": COMPILER_VERSION,
            "source_profiles_path": profile_path.relative_to(REPO_ROOT).as_posix(),
            "source_profiles_sha256": _sha256(profile_raw),
            "source_map_manifests_sha256": map_sources,
            "catalog_hash": "",
        },
        "sources": [
            {"source_id": source_id, "atlas_path": path}
            for source_id, path in enumerate(ordered_paths)
        ],
        "residency_by_profile": residency_by_profile,
        "residency_scopes": {
            SEKIRO_SCOPE_NAME: sorted(
                source_id_by_path[path] for path in sekiro_scope_paths
            )
        },
    }
    hash_input = json.loads(json.dumps(catalog))
    catalog["header"]["catalog_hash"] = _sha256(_canonical_bytes(hash_input))
    return catalog


def render_catalog(catalog: dict[str, Any]) -> str:
    return json.dumps(catalog, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--sidecars", type=Path, default=DEFAULT_SIDECARS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    catalog = compile_catalog(args.profiles.resolve(), args.sidecars.resolve())
    rendered = render_catalog(catalog)
    if args.check:
        if not args.output.is_file() or args.output.read_text() != rendered:
            print(f"FL4789_ACTOR_ATLAS_CATALOG_STALE output={args.output}")
            return 1
        print(
            "FL4789_ACTOR_ATLAS_CATALOG_OK "
            f"sources={len(catalog['sources'])} "
            f"profiles={len(catalog['residency_by_profile'])} "
            f"sekiro_scope={len(catalog['residency_scopes'][SEKIRO_SCOPE_NAME])} "
            f"catalog_hash={catalog['header']['catalog_hash']}"
        )
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered)
    print(f"FL4789_ACTOR_ATLAS_CATALOG_WRITTEN output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
