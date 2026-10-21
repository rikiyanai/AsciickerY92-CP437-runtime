from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launcher
from scripts.launcher_lib import option_tree

PRODUCT_ACTIONS = {
    "game-classic-euclidean",
    "game-spherical-classic",
    "game-fl4512-unicode-euclidean",
    "game-godot",
    "map-asciicker-presenter",
    "map-asciicker-presenter-html",
    "map-asciicker-presenter-native",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _install_fake_native_build(
    run_dir: Path,
    binary_name: str,
    *,
    source_commit: str,
    build_hash: str = "build-hash-test",
) -> Path:
    binary = run_dir / binary_name
    binary.write_bytes(f"fake-current-{binary_name}".encode("utf-8"))
    binary.chmod(0o755)
    (run_dir / f"{binary_name}.build_identity.json").write_text(
        json.dumps(
            {
                "schema": launcher.NATIVE_BUILD_IDENTITY_SCHEMA,
                "source_commit": source_commit,
                "build_hash": build_hash,
                "binary_sha256": _sha256(binary),
            }
        ),
        encoding="utf-8",
    )
    return binary


def test_cli_action_registry_and_option_tree_expose_product_modes_once() -> None:
    raw = subprocess.check_output(
        ["python3", "scripts/launcher.py", "--list-actions", "--json"],
        cwd=ROOT,
        text=True,
    )
    actions = json.loads(raw)

    for action in PRODUCT_ACTIONS:
        assert actions.count(action) == 1, action

    leaves = option_tree.flatten_options()
    by_action = {leaf.get("action"): leaf for leaf in leaves if leaf.get("action")}
    for action in PRODUCT_ACTIONS:
        assert action in by_action, action

    tree = option_tree.option_tree()
    menus = {menu["id"]: menu for menu in tree["menus"]}
    game_single_player = menus["game"]["items"][0]
    assert game_single_player["kind"] == "submenu"
    assert game_single_player["submenu_id"] == "single_player"
    assert [
        item["key"]
        for item in menus["single_player"]["items"]
        if item["key"] != "legacy"
    ] == [
        "1",
        "2",
        "3",
        "m",
        "q",
    ]

    assert by_action["game-classic-euclidean"]["product_mode"] == "classic_euclidean"
    assert by_action["game-spherical-classic"]["product_mode"] == "spherical_classic"
    assert by_action["game-fl4512-unicode-euclidean"]["product_mode"] == "fl4512_unicode_euclidean"
    assert by_action["game-godot"]["handler"] == "_launch_godot"
    assert by_action["map-asciicker-presenter-html"]["handler"] == "_run_presenter_html_alternate"
    assert by_action["map-asciicker-presenter-native"]["handler"] == "_run_presenter_default"


def test_native_product_mode_writes_receipt_before_subprocess_launch(tmp_path, monkeypatch) -> None:
    run_dir = tmp_path / ".run"
    run_dir.mkdir()
    binary = run_dir / "game"
    binary.write_bytes(b"fake-current-game")
    binary.chmod(0o755)
    source_commit = launcher._source_commit()
    build_identity = {
        "schema": launcher.NATIVE_BUILD_IDENTITY_SCHEMA,
        "source_commit": source_commit,
        "build_hash": "build-hash-test",
        "binary_sha256": _sha256(binary),
    }
    (run_dir / "game.build_identity.json").write_text(json.dumps(build_identity), encoding="utf-8")
    test_map = tmp_path / "fixture.a3d"
    test_map.write_bytes(b"fixture-map")

    observed: dict[str, object] = {}

    def fake_run(cmd, env=None, **_kwargs):
        receipt_path = run_dir / "launcher-native-game-fl4512-unicode-euclidean.json"
        assert receipt_path.is_file(), "receipt must exist before subprocess launch"
        observed["cmd"] = cmd
        observed["env"] = env

        return launcher._child_run.ChildOutcome("", "", 0)

    monkeypatch.setattr(launcher, "RUN_DIR", run_dir)
    monkeypatch.setattr(launcher, "_source_commit", lambda: source_commit)
    monkeypatch.setattr(launcher, "_read_map_selection", lambda _target: test_map)
    monkeypatch.setattr(launcher, "_single_player_spawn_env_for_map", lambda _map_path: None)
    monkeypatch.setattr(launcher, "_stop_repo_single_player_servers", lambda: None)
    monkeypatch.setattr(launcher._health, "_binary_fresh", lambda _name: True)
    monkeypatch.setattr(launcher._child_run, "run_child", fake_run)  # FL-4759 child owner

    assert launcher._run_native_game_mode(
        "fl4512_unicode_euclidean",
        action="game-fl4512-unicode-euclidean",
    ) == 0

    receipt = json.loads((run_dir / "launcher-native-game-fl4512-unicode-euclidean.json").read_text())
    assert receipt["schema"] == launcher.NATIVE_PRELAUNCH_RECEIPT_SCHEMA
    assert receipt["status"] == "PRELAUNCH"
    assert receipt["game_mode"] == "fl4512_unicode_euclidean"
    assert receipt["world_geometry"] == "euclidean"
    assert receipt["renderer"] == "fl4512_unicode"
    assert receipt["map_hash"] == _sha256(test_map)
    assert receipt["build_hash"] == "build-hash-test"
    assert receipt["source_commit"] == source_commit
    assert receipt["glyph_program_hash"] != "declared_absent"
    assert receipt["actor_profile_contract_hash"] != "declared_absent"
    assert receipt["server_state_schema_hash"] != "declared_absent"
    assert receipt["classic_baseline_receipt_id"] == "declared_absent"
    assert receipt["fl4512_runtime_owner_count"] == 1
    assert receipt["watchdog_run_id"] is None
    assert receipt["pre_glyph_scene_fact_hash"] is None
    assert receipt["gameplay_transition_hash"] is None
    assert receipt["binary_path"] == str(binary.resolve())
    assert receipt["binary_sha256"] == _sha256(binary)
    assert observed["cmd"] == [
        str(binary),
        "--world-geometry", "euclidean",
        "--renderer", "fl4512_unicode",
        "--auto-start-map", str(test_map),
    ]
    env = observed["env"]
    assert isinstance(env, dict)
    assert env["ASCIICKER_LAUNCHER_GAME_MODE"] == "fl4512_unicode_euclidean"
    assert env["ASCIICKER_WORLD_GEOMETRY"] == "euclidean"
    assert env["ASCIICKER_RENDERER"] == "fl4512_unicode"
    assert env["ASCIICKER_GEOMETRY_MODE_SOURCE"] == "launcher"


def test_spherical_product_mode_uses_s3_default_map_when_no_saved_selection(tmp_path, monkeypatch) -> None:
    run_dir = tmp_path / ".run"
    run_dir.mkdir()
    source_commit = "current-head"
    binary = _install_fake_native_build(run_dir, "game_classic", source_commit=source_commit)
    selection_dir = tmp_path / "map-selection"
    s3_default = (ROOT / "assets/a3d/game_map_y8_s3_local.a3d").resolve()

    observed: dict[str, object] = {}

    def fake_run(cmd, env=None, **_kwargs):
        observed["cmd"] = cmd
        observed["env"] = env

        return launcher._child_run.ChildOutcome("", "", 0)

    monkeypatch.setattr(launcher, "RUN_DIR", run_dir)
    monkeypatch.setattr(launcher, "MAP_SELECTION_DIR", selection_dir)
    monkeypatch.setattr(launcher, "MAP_SELECTION_FILES", {"single-player": selection_dir / "single-player.json"})
    monkeypatch.setattr(launcher, "_source_commit", lambda: source_commit)
    monkeypatch.setattr(launcher, "_single_player_spawn_env_for_map", lambda _map_path: None)
    monkeypatch.setattr(launcher, "_merge_map_mesh_root_env", lambda _map_path, env=None: env or {})
    monkeypatch.setattr(launcher, "_stop_repo_single_player_servers", lambda: None)
    monkeypatch.setattr(launcher._health, "_binary_fresh", lambda _name: True)
    monkeypatch.setattr(launcher._child_run, "run_child", fake_run)  # FL-4759 child owner

    assert launcher._run_native_game_mode("spherical_classic", action="game-spherical-classic") == 0

    assert observed["cmd"] == [
        str(binary),
        "--world-geometry", "spherical",
        "--renderer", "classic_cp437",
        "--auto-start-map", str(s3_default),
    ]
    receipt = json.loads((run_dir / "launcher-native-game-spherical-classic.json").read_text())
    assert receipt["game_mode"] == "spherical_classic"
    assert receipt["world_geometry"] == "spherical"
    assert receipt["renderer"] == "classic_cp437"
    assert receipt["map"] == "assets/a3d/game_map_y8_s3_local.a3d"
    assert receipt["map_hash"] == _sha256(s3_default)


def test_spherical_product_mode_preserves_saved_incompatible_map_for_server_rejection(tmp_path, monkeypatch) -> None:
    run_dir = tmp_path / ".run"
    run_dir.mkdir()
    source_commit = "current-head"
    binary = _install_fake_native_build(run_dir, "game_classic", source_commit=source_commit)
    selection_dir = tmp_path / "map-selection"
    selected_euclidean_map = launcher.DEFAULT_MAP.resolve()
    s3_default = (ROOT / "assets/a3d/game_map_y8_s3_local.a3d").resolve()
    observed: dict[str, object] = {}

    def fake_run(cmd, env=None, **_kwargs):
        observed["cmd"] = cmd

        return launcher._child_run.ChildOutcome("", "", 0)

    monkeypatch.setattr(launcher, "RUN_DIR", run_dir)
    monkeypatch.setattr(launcher, "MAP_SELECTION_DIR", selection_dir)
    monkeypatch.setattr(launcher, "MAP_SELECTION_FILES", {"single-player": selection_dir / "single-player.json"})
    launcher._write_map_selection("single-player", selected_euclidean_map)
    monkeypatch.setattr(launcher, "_source_commit", lambda: source_commit)
    monkeypatch.setattr(launcher, "_single_player_spawn_env_for_map", lambda _map_path: None)
    monkeypatch.setattr(launcher, "_merge_map_mesh_root_env", lambda _map_path, env=None: env or {})
    monkeypatch.setattr(launcher, "_stop_repo_single_player_servers", lambda: None)
    monkeypatch.setattr(launcher._health, "_binary_fresh", lambda _name: True)
    monkeypatch.setattr(launcher._child_run, "run_child", fake_run)  # FL-4759 child owner

    assert launcher._run_native_game_mode("spherical_classic", action="game-spherical-classic") == 0

    assert observed["cmd"] == [
        str(binary),
        "--world-geometry", "spherical",
        "--renderer", "classic_cp437",
        "--auto-start-map", str(selected_euclidean_map),
    ]
    assert selected_euclidean_map != s3_default


def test_native_product_mode_rebuilds_missing_build_identity_before_launch(tmp_path, monkeypatch) -> None:
    run_dir = tmp_path / ".run"
    run_dir.mkdir()
    binary = run_dir / "game_classic"
    binary.write_bytes(b"fake-current-classic")
    binary.chmod(0o755)

    builds: list[tuple[str, str]] = []

    def fake_build(make_target: str, label: str) -> bool:
        builds.append((make_target, label))
        _install_fake_native_build(run_dir, "game_classic", source_commit="current-head")
        return True

    def fake_run(*_args, **_kwargs):
        return launcher._child_run.ChildOutcome("", "", 0)

    monkeypatch.setattr(launcher, "RUN_DIR", run_dir)
    monkeypatch.setattr(launcher, "_source_commit", lambda: "current-head")
    monkeypatch.setattr(launcher, "_stop_repo_single_player_servers", lambda: None)
    monkeypatch.setattr(launcher, "_build_native_target", fake_build)
    monkeypatch.setattr(launcher._health, "_binary_fresh", lambda _name: True)
    monkeypatch.setattr(launcher._child_run, "run_child", fake_run)  # FL-4759 child owner

    assert launcher._run_native_game_mode(
        "classic_euclidean",
        action="game-classic-euclidean",
    ) == 0
    assert builds == [("game-classic", "Classic CP437 / Euclidean")]
    assert (run_dir / "launcher-native-game-classic-euclidean.json").is_file()


def test_native_product_mode_rebuilds_stale_build_identity_before_launch(tmp_path, monkeypatch) -> None:
    run_dir = tmp_path / ".run"
    run_dir.mkdir()
    binary = run_dir / "game_classic"
    binary.write_bytes(b"fake-current-classic")
    binary.chmod(0o755)
    (run_dir / "game_classic.build_identity.json").write_text(
        json.dumps(
            {
                "schema": launcher.NATIVE_BUILD_IDENTITY_SCHEMA,
                "source_commit": "stale-head",
                "build_hash": "build-hash-test",
                "binary_sha256": _sha256(binary),
            }
        ),
        encoding="utf-8",
    )

    builds: list[tuple[str, str]] = []

    def fake_build(make_target: str, label: str) -> bool:
        builds.append((make_target, label))
        _install_fake_native_build(run_dir, "game_classic", source_commit="current-head")
        return True

    def fake_run(*_args, **_kwargs):
        return launcher._child_run.ChildOutcome("", "", 0)

    monkeypatch.setattr(launcher, "RUN_DIR", run_dir)
    monkeypatch.setattr(launcher, "_source_commit", lambda: "current-head")
    monkeypatch.setattr(launcher, "_stop_repo_single_player_servers", lambda: None)
    monkeypatch.setattr(launcher, "_build_native_target", fake_build)
    monkeypatch.setattr(launcher._health, "_binary_fresh", lambda _name: True)
    monkeypatch.setattr(launcher._child_run, "run_child", fake_run)  # FL-4759 child owner

    assert launcher._run_native_game_mode(
        "classic_euclidean",
        action="game-classic-euclidean",
    ) == 0
    assert builds == [("game-classic", "Classic CP437 / Euclidean")]
    assert (run_dir / "launcher-native-game-classic-euclidean.json").is_file()


def test_action_registry_dispatches_product_metadata(monkeypatch) -> None:
    calls: list[tuple[str, str | None]] = []

    def fake_run_native_game_mode(product_mode: str, *, action: str | None = None) -> int:
        calls.append((product_mode, action))
        return 0

    monkeypatch.setattr(launcher, "_run_native_game_mode", fake_run_native_game_mode)
    from scripts.launcher_actions import ActionRegistry
    from scripts.launcher_agent_bridge import register_all_actions

    registry = ActionRegistry()
    register_all_actions(registry, launcher.__dict__)

    args = argparse.Namespace()
    assert registry.dispatch("game-spherical-classic", args) == 0
    assert calls == [("spherical_classic", "game-spherical-classic")]


def test_single_player_menu_selects_each_explicit_product_mode(monkeypatch) -> None:
    choices = iter(("1", "2", "3", "q"))
    calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr(launcher, "_prompt_char", lambda _prompt: next(choices))
    monkeypatch.setattr(launcher, "_draw_submenu_header", lambda _title: None)
    monkeypatch.setattr(launcher, "_menu_line", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        launcher,
        "_single_player_map_for_launch",
        lambda: ROOT / "assets/a3d/game_map_y8.a3d",
    )
    monkeypatch.setattr(
        launcher,
        "_run_native_game_mode",
        lambda product_mode, *, action=None: calls.append((product_mode, action)) or 0,
    )

    launcher._menu_single_player()

    assert calls == [
        ("classic_euclidean", "game-classic-euclidean"),
        ("spherical_classic", "game-spherical-classic"),
        ("fl4512_unicode_euclidean", "game-fl4512-unicode-euclidean"),
    ]


def test_single_player_menu_selects_map_before_launch(monkeypatch) -> None:
    choices = iter(("m", "q"))
    selected = ROOT / "assets/a3d/game_map_y8.a3d"
    swaps: list[str] = []

    monkeypatch.setattr(launcher, "_prompt_char", lambda _prompt: next(choices))
    monkeypatch.setattr(launcher, "_draw_submenu_header", lambda _title: None)
    monkeypatch.setattr(launcher, "_menu_line", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher, "_single_player_map_for_launch", lambda: selected)
    monkeypatch.setattr(launcher, "_file_picker", lambda *_args, **_kwargs: selected)
    monkeypatch.setattr(
        launcher,
        "_swap_map_for_single_player",
        lambda path: swaps.append(path) or 0,
    )

    launcher._menu_single_player()

    assert swaps == [str(selected)]


def test_presenter_default_and_html_alternate_dispatch_to_distinct_front_doors(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_presenter_html_alternate() -> int:
        calls.append(("html-authoring", "ASCIID FL4512 Presenter HTML Authoring"))
        return 0

    def fake_presenter_default() -> int:
        calls.append(("presenter", "ASCIID FL4512 Presenter"))
        return 0

    monkeypatch.setattr(
        launcher, "_run_presenter_html_alternate", fake_presenter_html_alternate
    )
    monkeypatch.setattr(launcher, "_run_presenter_default", fake_presenter_default)
    from scripts.launcher_actions import ActionRegistry
    from scripts.launcher_agent_bridge import register_all_actions

    registry = ActionRegistry()
    register_all_actions(registry, launcher.__dict__)

    args = argparse.Namespace()
    assert registry.dispatch("map-asciicker-presenter-html", args) == 0
    assert registry.dispatch("map-asciicker-presenter-native", args) == 0
    assert calls == [
        ("html-authoring", "ASCIID FL4512 Presenter HTML Authoring"),
        ("presenter", "ASCIID FL4512 Presenter"),
    ]
