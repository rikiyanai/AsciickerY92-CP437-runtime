#!/usr/bin/env python3
"""Regression checks for FL-4131 actor visual compile admission."""

from __future__ import annotations

import importlib.util
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MATERIAL_MANIFEST_HASH = "077de379be107288555c7162ad737f0545fc637d43a2cf085051aed578b4aa8e"
MATERIAL_CONTENT_PACK_ID = "material.additive.v1"


def load_compiler():
    path = REPO_ROOT / "scripts" / "compile_actor_visual_profiles.py"
    spec = importlib.util.spec_from_file_location("compile_actor_visual_profiles", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_valid_fixture_sidecar_manifest_is_admitted() -> None:
    compiler = load_compiler()
    xp = REPO_ROOT / "assets/glyphs/fixtures/fl4131_phase2_valid.xp"
    assert (
        compiler._admitted_glyph_manifest_hash_for(xp)
        == "1ff4e22faf91a79fde8ae38c59d0736982a53aafef100d708651dd3f95c9d9cd"
    )


def test_unadmitted_fixture_still_fails_closed() -> None:
    compiler = load_compiler()
    xp = REPO_ROOT / "assets/glyphs/fixtures/fl4131_phase2_unadmitted.xp"
    assert compiler._admitted_glyph_manifest_hash_for(xp) is None


def test_combat_sprite_prefix_overrides_any_admission() -> None:
    compiler = load_compiler()
    xp = REPO_ROOT / "assets/sprites/player-0000.xp"
    assert compiler._admitted_glyph_manifest_hash_for(xp) is None


def test_runtime_table_advertises_material_manifest_hash() -> None:
    generated = (REPO_ROOT / "engine" / "actor_visual_profile_table.generated.h").read_text(
        encoding="utf-8"
    )
    assert f'ACTOR_VISUAL_PROFILE_GLYPH_MANIFEST_SHA256 = "{MATERIAL_MANIFEST_HASH}"' in generated
    assert f'ACTOR_VISUAL_PROFILE_CONTENT_PACK_ID = "{MATERIAL_CONTENT_PACK_ID}"' in generated


def test_valid_fixture_sidecar_content_pack_is_admitted() -> None:
    compiler = load_compiler()
    xp = REPO_ROOT / "assets/glyphs/fixtures/fl4131_phase2_valid.xp"
    assert compiler._admitted_glyph_content_pack_id_for(xp) == "terrain.extended.v1"


if __name__ == "__main__":
    test_valid_fixture_sidecar_manifest_is_admitted()
    test_unadmitted_fixture_still_fails_closed()
    test_combat_sprite_prefix_overrides_any_admission()
    test_runtime_table_advertises_material_manifest_hash()
    test_valid_fixture_sidecar_content_pack_is_admitted()
    print("FL-4131 actor visual compile admission tests passed")
