"""UI scene-path ownership: HUD/InventoryHud/MainMenu live under scenes/ui.

Delete-first move: godot_project/scenes/{HUD,InventoryHud,MainMenu}.tscn
-> godot_project/scenes/ui/*.tscn. No aliases or forwarding scenes remain at
the old addresses. Allowed consumers reference only the new addresses.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GODOT = ROOT / "godot_project"
NEW_DIR = GODOT / "scenes" / "ui"

EXPECTED_UIDS = {
    "HUD.tscn": "uid://asciicker_hud_scene",
    "InventoryHud.tscn": "uid://asciicker_inventory_hud_scene",
    "MainMenu.tscn": "uid://fl4367_mainmenu",
}

OLD_RES_ADDRESSES = [
    "res://scenes/HUD.tscn",
    "res://scenes/InventoryHud.tscn",
    "res://scenes/MainMenu.tscn",
]

OLD_FS_ADDRESSES = [
    "godot_project/scenes/HUD.tscn",
    "godot_project/scenes/InventoryHud.tscn",
    "godot_project/scenes/MainMenu.tscn",
]

# Allowlisted live callers: path -> new-address tokens that must be present.
# Old res:// and filesystem addresses must be absent.
CALLERS: dict[Path, list[str]] = {
    ROOT / "godot_project" / "scenes" / "Main.tscn": [
        "res://scenes/ui/HUD.tscn",
    ],
    ROOT / "godot_project" / "scripts" / "Main.gd": [
        "res://scenes/ui/MainMenu.tscn",
    ],
    ROOT / "godot_project" / "scripts" / "boot" / "ConsumerBoot.gd": [
        "res://scenes/ui/MainMenu.tscn",
    ],
    ROOT / "godot_project" / "scripts" / "ui" / "InventoryHud.gd": [
        "res://scenes/ui/InventoryHud.tscn",
    ],
    ROOT / "godot_project" / "scripts" / "ui" / "RunResultsConsumer.gd": [
        "res://scenes/ui/MainMenu.tscn",
    ],
    ROOT / "scripts" / "launch_fl4359_manual_play.py": [
        "res://scenes/ui/MainMenu.tscn",
    ],
    ROOT / "scripts" / "test_fl4359_manual_play_launcher.py": [
        "res://scenes/ui/MainMenu.tscn",
    ],
    ROOT / "scripts" / "test_fl4359_desktop_manual_hud_contract.py": [
        '"scenes" / "ui" / "HUD.tscn"',
    ],
    ROOT / "config" / "godot_proof_pose_registry.json": [
        "godot_project/scenes/ui/MainMenu.tscn",
    ],
}


def test_new_canonical_scenes_exist_with_identity() -> None:
    assert NEW_DIR.is_dir(), f"missing {NEW_DIR}"
    for name, expected_uid in EXPECTED_UIDS.items():
        scene = NEW_DIR / name
        assert scene.is_file(), f"missing {scene}"
        first_line = scene.read_text(encoding="utf-8").splitlines()[0]
        assert expected_uid in first_line, f"resource identity drift in {scene}: {first_line!r}"


def test_old_scene_addresses_deleted() -> None:
    leftovers = [old for old in OLD_FS_ADDRESSES if (ROOT / old).exists()]
    assert leftovers == [], f"old UI scene addresses remain: {leftovers}"


def test_moved_bodies_carry_no_old_self_addresses() -> None:
    for name in EXPECTED_UIDS:
        source = (NEW_DIR / name).read_text(encoding="utf-8")
        for old in OLD_RES_ADDRESSES:
            assert old not in source, f"{name} still references old address {old}"


def test_moved_scene_references_resolve() -> None:
    missing: list[str] = []
    for name in EXPECTED_UIDS:
        for line in (NEW_DIR / name).read_text(encoding="utf-8").splitlines():
            if line.startswith("[ext_resource") and 'path="res://' in line:
                res_path = line.split('path="res://', 1)[1].split('"', 1)[0]
                if not (GODOT / res_path).is_file():
                    missing.append(f"{name}: {line.strip()}")
    assert missing == [], f"unresolved moved scene references: {missing}"


def test_known_callers_resolve_to_new_address() -> None:
    for caller, tokens in CALLERS.items():
        assert caller.is_file(), f"caller missing: {caller}"
        text = caller.read_text(encoding="utf-8")
        for token in tokens:
            assert token in text, f"{caller} missing new address {token}"
        for old in OLD_RES_ADDRESSES:
            assert old not in text, f"{caller} still references old address {old}"
        for old_fs in OLD_FS_ADDRESSES:
            assert old_fs not in text, f"{caller} still references old filesystem path {old_fs}"


def test_pose_registry_menu_scene_resolves_to_file() -> None:
    registry = json.loads((ROOT / "config" / "godot_proof_pose_registry.json").read_text(encoding="utf-8"))
    menu_scene = registry["defaults"]["main_menu_scene"]
    assert menu_scene == "godot_project/scenes/ui/MainMenu.tscn", menu_scene
    assert (ROOT / menu_scene).is_file(), f"registry menu scene missing: {menu_scene}"
