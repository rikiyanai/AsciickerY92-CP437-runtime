from __future__ import annotations

import json
import plistlib
from pathlib import Path

import pytest

from scripts.launcher_lib import health
from scripts.launcher_lib import option_tree
from scripts.launcher_lib import product_catalog


ROOT = Path(__file__).resolve().parents[2]


def test_catalog_is_the_single_launcher_product_owner() -> None:
    catalog = json.loads(
        (ROOT / "config" / "launcher_products.json").read_text(encoding="utf-8")
    )
    assert catalog["schema"] == product_catalog.EXPECTED_SCHEMA
    assert set(catalog["native_modes"]) == {
        "classic_euclidean",
        "spherical_classic",
        "fl4512_unicode_euclidean",
    }
    assert set(catalog["products"]) == {
        "classic_game",
        "fl4512_game",
        "fl4512_presenter",
        "classic_editor",
        "terminal",
        "godot",
    }

    launcher = (ROOT / "scripts" / "launcher.py").read_text(encoding="utf-8")
    health_source = (ROOT / "scripts" / "launcher_lib" / "health.py").read_text(
        encoding="utf-8"
    )
    status = (ROOT / "scripts" / "status.sh").read_text(encoding="utf-8")
    assert "NATIVE_PRODUCT_MODES = _product_catalog.NATIVE_PRODUCT_MODES" in launcher
    assert "_MAKE_TARGET_FOR_BINARY" not in health_source
    assert "product_health_signals" in status
    assert "Game-current" not in status


def test_option_tree_native_routes_match_catalog() -> None:
    leaves = {
        leaf.get("action"): leaf
        for leaf in option_tree.flatten_options()
        if leaf.get("action")
    }
    for mode_id, mode in product_catalog.NATIVE_PRODUCT_MODES.items():
        leaf = leaves[mode["action"]]
        assert leaf["product_mode"] == mode_id
        assert leaf["command"][0] == f".run/{mode['binary']}"
        assert mode["make_target"] == product_catalog.BINARY_BUILD_TARGETS[mode["binary"]]

    assert leaves["game-godot"]["handler"] == "_launch_godot"
    assert leaves["product-status"]["handler"] == "_show_product_status"
    assert leaves["build-game-classic"]["command"][-1] == "game-classic"
    assert leaves["build-game-fl4512"]["command"][-1] == "game-fl4512"


def test_godot_prerequisites_cover_the_runtime_the_launch_script_uses(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(product_catalog, "godot_engine_version", lambda *args, **kwargs: "4.7.2.stable.official.fixture")
    project = tmp_path / "godot_project" / "project.godot"
    launch_script = tmp_path / "scripts" / "launch_fl4359_manual_play.py"
    colyseus = tmp_path / "authority"
    runtime_module = colyseus / "node_modules" / "tsx"
    for directory in (project.parent, launch_script.parent, runtime_module):
        directory.mkdir(parents=True, exist_ok=True)
    project.write_text("[application]\n", encoding="utf-8")
    launch_script.write_text("# fixture\n", encoding="utf-8")
    (colyseus / "package.json").write_text("{}\n", encoding="utf-8")
    godot_binary = tmp_path / "godot"
    godot_binary.write_text("fixture\n", encoding="utf-8")

    env = {
        "GODOT_BIN": str(godot_binary),
        "ASCIICKER_COLYSEUS_DIR": str(colyseus),
    }
    which = lambda command: "/fixture/node" if command == "node" else None
    assert product_catalog.godot_missing_prerequisites(
        tmp_path,
        env=env,
        platform_name="linux",
        which=which,
    ) == []

    runtime_module.rmdir()
    missing = product_catalog.godot_missing_prerequisites(
        tmp_path,
        env=env,
        platform_name="linux",
        which=which,
    )
    assert str(runtime_module) in missing


@pytest.mark.parametrize("version,accepted", [
    ("4.7.2", True), ("4.7.2.stable.official.fixture", True),
    ("4.6.2", False), ("4.7.20", False), ("4.7.2.rc1", False), ("", False),
])
def test_launch_gate_rejects_wrong_engine_without_launching(
    tmp_path: Path, version: str, accepted: bool, monkeypatch,
) -> None:
    app = tmp_path / "Godot.app"
    binary = app / "Contents" / "MacOS" / "Godot"
    binary.parent.mkdir(parents=True)
    binary.write_text("fixture", encoding="utf-8")
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleShortVersionString": version,
    }))
    def forbid_launch(*args, **kwargs):
        raise AssertionError("macOS prerequisite check must not launch Godot")
    monkeypatch.setattr(product_catalog.subprocess, "run", forbid_launch)
    missing = product_catalog.godot_missing_prerequisites(
        tmp_path, env={"GODOT_APP": str(app)}, platform_name="darwin", which=lambda _: None,
    )
    version_errors = [error for error in missing if "Godot" in error]
    assert (not version_errors) == accepted


def test_missing_bundle_metadata_blocks_launch(tmp_path: Path) -> None:
    app = tmp_path / "Godot.app"
    binary = app / "Contents" / "MacOS" / "Godot"
    binary.parent.mkdir(parents=True)
    binary.write_text("fixture", encoding="utf-8")
    missing = product_catalog.godot_missing_prerequisites(
        tmp_path, env={"GODOT_APP": str(app)}, platform_name="darwin", which=lambda _: None,
    )
    assert any("Godot version unreadable" in error for error in missing)


def test_binary_version_gate_uses_selected_binary(tmp_path: Path, monkeypatch) -> None:
    binary = tmp_path / "legacy-godot"
    binary.write_text("fixture", encoding="utf-8")
    calls = []
    def version(executable, **kwargs):
        calls.append(executable)
        return "4.6.2.stable.official.fixture"
    monkeypatch.setattr(product_catalog, "godot_engine_version", version)
    missing = product_catalog.godot_missing_prerequisites(
        tmp_path, env={"GODOT_BIN": str(binary)}, platform_name="linux", which=lambda _: None,
    )
    assert calls == [str(binary)]
    assert any("4.6.2.stable.official.fixture" in error for error in missing)


def test_macos_status_and_launch_resolve_the_same_app_executable(tmp_path: Path) -> None:
    app = tmp_path / "Godot.app"
    executable = app / "Contents" / "MacOS" / "Godot"
    executable.parent.mkdir(parents=True)
    executable.write_text("fixture\n", encoding="utf-8")
    env = {"GODOT_APP": str(app)}

    assert product_catalog.resolve_godot_app(env) == app
    assert product_catalog.resolve_godot_executable(
        env,
        platform_name="darwin",
    ) == str(executable)


def test_product_health_is_derived_from_every_catalog_product(monkeypatch) -> None:
    observed: list[tuple[str, str]] = []

    def fake_binary(binary: str, label: str) -> health.HealthSignal:
        observed.append((binary, label))
        return health.HealthSignal(label, binary, "ok")

    monkeypatch.setattr(health, "_check_binary_signal", fake_binary)
    monkeypatch.setattr(
        health,
        "_check_godot_signal",
        lambda: health.HealthSignal("Godot", "fixture", "ok"),
    )

    signals = health.product_health_signals()
    binary_products = [
        product
        for product in product_catalog.PRODUCTS.values()
        if product["kind"] == "binary"
    ]
    assert len(signals) == len(product_catalog.PRODUCTS)
    assert observed == [
        (str(product["binary"]), str(product["label"]))
        for product in binary_products
    ]


def test_launcher_and_manual_route_use_catalog_prerequisite_gate() -> None:
    launcher = (ROOT / "scripts" / "launcher.py").read_text(encoding="utf-8")
    manual = (ROOT / "scripts" / "launch_fl4359_manual_play.py").read_text(
        encoding="utf-8"
    )
    assert "_product_catalog.godot_missing_prerequisites(REPO_ROOT)" in launcher
    assert "_product_catalog.godot_missing_prerequisites(ROOT)" in manual
    assert "_product_catalog.resolve_colyseus_dir(ROOT)" in manual
    assert "_product_catalog.resolve_godot_app()" in manual
