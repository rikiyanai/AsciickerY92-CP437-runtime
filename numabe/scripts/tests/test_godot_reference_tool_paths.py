"""Reference/tool-path ownership: non-production entrypoints live outside generic runtime locations."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GODOT = ROOT / "godot_project"

PORT = GODOT / "reference" / "texel_splat" / "reference_texel_splat_port.gd"
PROBE = GODOT / "tools" / "probes" / "probe_camera_terrain_rays.gd"
ORACLE = GODOT / "tools" / "compile_oracles" / "fl4431_metal_compile_oracle.gd"

SCRIPT_PAIRS = [
    (PORT, "uid://bx70og0awfirs"),
    (PROBE, "uid://dd0f44tuh34j1"),
    (ORACLE, "uid://k7g02eope8sf"),
]

OLD_SCRIPT_PATHS = [
    GODOT / "scripts" / "reference_texel_splat_port.gd",
    GODOT / "scripts" / "reference_texel_splat_port.gd.uid",
    GODOT / "scripts" / "probe_camera_terrain_rays.gd",
    GODOT / "scripts" / "probe_camera_terrain_rays.gd.uid",
    GODOT / "scripts" / "fl4431_metal_compile_oracle.gd",
    GODOT / "scripts" / "fl4431_metal_compile_oracle.gd.uid",
]
OLD_REFERENCE_DIR = GODOT / "reference_texel_splat"

REFERENCE_REL_FILES = [
    "LICENSE.canxkoz-texel-splatting",
    "assets/hero.jpg",
    "assets/hero.jpg.import",
    "assets/icon.svg",
    "assets/icon.svg.import",
    "assets/pair-cubemap.jpg",
    "assets/pair-cubemap.jpg.import",
    "assets/pair-rasterization.jpg",
    "assets/pair-rasterization.jpg.import",
    "assets/pair-texel-splatting.jpg",
    "assets/pair-texel-splatting.jpg.import",
    "assets/public-icon.svg",
    "assets/public-icon.svg.import",
    "assets/site-hero.jpg",
    "assets/site-hero.jpg.import",
]
NEW_REFERENCE_DIR = GODOT / "reference" / "texel_splat"

IMPORT_NAMES = [
    "hero.jpg",
    "icon.svg",
    "pair-cubemap.jpg",
    "pair-rasterization.jpg",
    "pair-texel-splatting.jpg",
    "public-icon.svg",
    "site-hero.jpg",
]

_PRELOAD_RE = re.compile(r'preload\("res://([^"]+)"\)')
_LOAD_RE = re.compile(r'load\("res://([^"]+)"\)')


def test_move_map_destinations_exist_with_uid_siblings() -> None:
    for script, expected_uid in SCRIPT_PAIRS:
        uid_path = script.with_name(f"{script.name}.uid")
        assert script.is_file(), f"missing {script}"
        assert uid_path.is_file(), f"missing {uid_path}"
        text = uid_path.read_text(encoding="utf-8").strip()
        assert text == expected_uid, f"UID drift in {uid_path}: {text!r}"


def test_reference_assets_moved_intact() -> None:
    assert NEW_REFERENCE_DIR.is_dir(), f"missing {NEW_REFERENCE_DIR}"
    for rel in REFERENCE_REL_FILES:
        target = NEW_REFERENCE_DIR / rel
        assert target.is_file(), f"missing {target}"


def test_moved_bodies_carry_no_old_self_addresses() -> None:
    port_src = PORT.read_text(encoding="utf-8")
    assert "godot_project/reference_texel_splat/" not in port_src, "port still references old asset root"
    assert "res://reference_texel_splat/" not in port_src, "port still references old res address"
    assert "godot_project/reference/texel_splat/assets/hero.jpg" in port_src
    assert "res://../.run/reference_texel_splat_godot_port/latest" in port_src, "generated output destination moved"
    oracle_src = ORACLE.read_text(encoding="utf-8")
    assert "res://scripts/fl4431_metal_compile_oracle.gd" not in oracle_src
    assert "res://tools/compile_oracles/fl4431_metal_compile_oracle.gd" in oracle_src
    probe_src = PROBE.read_text(encoding="utf-8")
    assert "res://scripts/probe_camera_terrain_rays" not in probe_src


def test_import_metadata_preserves_uid_and_names_new_source() -> None:
    for name in IMPORT_NAMES:
        import_path = NEW_REFERENCE_DIR / "assets" / f"{name}.import"
        text = import_path.read_text(encoding="utf-8")
        assert 'uid="uid://' in text, f"UID identity lost in {import_path}"
        assert f'source_file="res://reference/texel_splat/assets/{name}"' in text, (
            f"stale source path in {import_path}"
        )
        assert "res://reference_texel_splat/" not in text, f"old source path in {import_path}"


def test_moved_script_references_resolve() -> None:
    missing: list[str] = []
    for script, _ in SCRIPT_PAIRS:
        source = script.read_text(encoding="utf-8")
        for match in list(_PRELOAD_RE.finditer(source)) + list(_LOAD_RE.finditer(source)):
            target = GODOT / match.group(1)
            if not target.is_file():
                missing.append(f"{script.name} -> res://{match.group(1)}")
    assert missing == [], f"unresolved moved-script references: {missing}"
