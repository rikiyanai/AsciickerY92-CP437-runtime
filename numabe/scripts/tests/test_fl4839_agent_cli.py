"""FL-4839 low-context CLI coverage over the real A3D writer."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts/apply_map_edits.py"
MAP = ROOT / "godot_project/assets/maps/race_circuit_y8.a3d"


@pytest.fixture(scope="module")
def edits():
    spec = importlib.util.spec_from_file_location("fl4839_agent_cli", CLI)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_operation_manifest_covers_every_writer_operation(edits) -> None:
    manifest = edits.operation_manifest()
    assert manifest["operation_count"] == 27
    assert set(manifest["operations"]) == set(edits.OP_RECORD_KIND)
    assert all(row["record_kind"] == edits.OP_RECORD_KIND[name]
               for name, row in manifest["operations"].items())


def test_inspect_is_bounded_and_reports_total(edits) -> None:
    payload = edits.inspect_map(MAP, None, "marker", "race_", 2, 3)
    assert payload["returned"] == 3
    assert payload["total"] == 26
    assert payload["offset"] == 2
    assert all("race_" in row["name"] for row in payload["rows"])


def test_asset_catalog_is_bounded(edits) -> None:
    payload = edits.asset_catalog("item", "Shield", 0, 2)
    assert payload["total"] == 3
    assert payload["returned"] == 2
    assert all(row["kind"] == "item" for row in payload["rows"])


def test_asset_catalog_includes_map_owned_assets_with_map_precedence(
        edits, monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    maps = repo / "godot_project/assets/maps"
    maps.mkdir(parents=True)
    selected_map = maps / "author_map.a3d"
    selected_map.write_bytes(b"map")
    shared = repo / "assets/meshes"
    shared.mkdir(parents=True)
    (shared / "shared.akm").write_text("shared")
    (shared / "same.akm").write_text("shared version")
    owned = repo / "godot_project/assets/world_mesh/author_map.meshes"
    owned.mkdir(parents=True)
    (owned / "same.akm").write_text("map version")
    (owned / "owned.akm").write_text("owned")
    monkeypatch.setattr(edits, "REPO_ROOT", repo)
    payload = edits.asset_catalog("mesh", "", 0, 50, selected_map)
    by_name = {row["asset"]: row for row in payload["rows"]}
    assert set(by_name) == {"owned.akm", "same.akm", "shared.akm"}
    assert by_name["same.akm"]["origin"] == "map"


def test_import_asset_validates_and_installs_into_selected_map_bundle(
        edits, monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    maps = repo / "godot_project/assets/maps"
    maps.mkdir(parents=True)
    selected_map = maps / "author_map.a3d"
    selected_map.write_bytes(b"map")
    source = tmp_path / "outside.akm"
    source.write_text("valid fixture bytes")

    class Reader:
        @staticmethod
        def parse_akm_ply(_path):
            return ([{"pos": (0, 0, 0)}], [(0, 0, 0)])

    monkeypatch.setattr(edits, "REPO_ROOT", repo)
    monkeypatch.setattr(edits, "_load", lambda *_args: Reader)
    receipt = edits.import_asset(selected_map, source)
    destination = repo / "godot_project/assets/world_mesh/author_map.meshes/outside.akm"
    assert receipt["ok"] is True
    assert receipt["face_count"] == 1
    assert destination.read_text() == "valid fixture bytes"
    assert edits.import_asset(selected_map, source)["copied"] is False


def test_validate_executes_without_writing(edits) -> None:
    before = hashlib.sha256(MAP.read_bytes()).hexdigest()
    payload = edits.validate_delta(
        MAP, {"$schema": edits.DELTA_SCHEMA, "ops": [{"op": "clear_mat_elev"}]})
    assert payload == {
        "$schema": "fl4839_agent_validation.v1", "ok": True,
        "map_path": "godot_project/assets/maps/race_circuit_y8.a3d",
        "op_count": 1, "record_kinds": ["patch"], "writes": False,
    }
    assert hashlib.sha256(MAP.read_bytes()).hexdigest() == before


def test_inline_edit_cli_needs_no_delta_file() -> None:
    result = subprocess.run(
        [sys.executable, str(CLI), "edit", "--map", str(MAP),
         "--op", "add_marker", "--at", "1", "2", "--name", "agent_probe",
         "--set", 'label="Agent probe"', "--preview"],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["op_count"] == 1
    assert payload["record_kinds"] == ["marker"]


def test_inline_batch_cli_is_atomic_and_preview_only() -> None:
    before = hashlib.sha256(MAP.read_bytes()).hexdigest()
    ops = json.dumps([
        {"op": "add_marker", "name": "one", "world_xz": [1, 2]},
        {"op": "add_marker", "name": "two", "world_xz": [3, 4]},
    ])
    result = subprocess.run(
        [sys.executable, str(CLI), "batch", "--map", str(MAP),
         "--ops", ops, "--preview"], cwd=ROOT, text=True,
        capture_output=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["op_count"] == 2
    assert hashlib.sha256(MAP.read_bytes()).hexdigest() == before


def test_agent_mesh_shortcuts_preserve_yaw_and_per_axis_scale(edits, tmp_path) -> None:
    target = tmp_path / "mesh.a3d"
    shutil.copy2(MAP, target)
    delta = {"$schema": edits.DELTA_SCHEMA, "ops": [{
        "op": "add_mesh", "mesh": "tree-1.akm", "world_xz": [1.0, 2.0],
        "godot_y": 3.0, "yaw_degrees": 90.0, "scale_axes": [2.0, 3.0, 4.0],
    }]}
    edits.apply_delta(target, delta, runtime_terrain=MAP)
    row = edits.read_placements(target, MAP)["meshes"][0]
    assert row["yaw_degrees"] == pytest.approx(90.0)
    assert row["scale_axes"] == pytest.approx([2.0, 3.0, 4.0])
    assert row["world_xz"] == pytest.approx([1.0, 2.0])
    assert row["godot_y"] == pytest.approx(3.0)


def test_race_helpers_validate_and_derive_existing_course(edits, tmp_path) -> None:
    validation = edits.validate_race(MAP)
    assert validation["ok"] is True
    assert (validation["path_points"], validation["gates"], validation["grid_slots"]) == (11, 11, 4)
    delta = edits.race_build_delta(MAP)
    markers = delta["ops"][0]["markers"]
    assert len([row for row in markers if row["name"].startswith("race_gate_")]) == 11
    assert len([row for row in markers if row["name"].startswith("race_grid_")]) == 4
    target = tmp_path / "race.a3d"
    shutil.copy2(MAP, target)
    receipt = edits.apply_delta(target, delta, runtime_terrain=MAP)
    assert receipt["canonical_verdict"]["roundtrip_stable"] is True
    assert edits.validate_race(target, MAP)["ok"] is True


def test_launch_recipe_preserves_exact_map_and_mode(edits) -> None:
    payload = edits.launch_recipe("race_circuit_y8", "race", True, "FC")
    assert payload["executed"] is False
    assert "--map-id race_circuit_y8" in payload["shell"]
    assert "--extra-user-arg=--car-race" in payload["argv"]
    assert "--windowed" in payload["argv"]


def test_product_rebuild_uses_existing_product_owners(edits, monkeypatch) -> None:
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(edits.subprocess, "run", fake_run)
    payload = edits.rebuild_products(MAP)
    assert payload["ok"] is True
    assert payload["products"] == 4
    assert [Path(argv[1]).name for argv in calls] == [
        "compile_fl4708_y8_tree_placements.py", "compile_fl4798_map_spawns.py",
        "compile_fl4789_chunk_catalog.py", "bake_bridge_deck_slabs.py",
    ]


def test_pipeline_product_rebuild_uses_isolated_mesh_bundle(
        edits, monkeypatch, tmp_path) -> None:
    calls = []
    mesh_root = tmp_path / "race_circuit_y8.meshes"
    mesh_root.mkdir()
    real_paths = edits._map_product_paths(MAP)
    monkeypatch.setattr(edits, "_map_product_paths",
                        lambda _map: {**real_paths, "mesh_root": mesh_root})

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "ok", "")

    monkeypatch.setattr(edits.subprocess, "run", fake_run)
    payload = edits.rebuild_products(MAP)
    assert payload["pipeline_import"] is True
    assert "--allow-no-trees" in calls[0]
    assert "--mesh-root" in calls[2]
    assert calls[2][calls[2].index("--mesh-root") + 1] == str(mesh_root)


def test_pipeline_install_dry_run_is_low_context(edits, tmp_path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    shutil.copy2(MAP, run_root / "output.a3d")
    sprite_root = run_root / "output.sprites"
    sprite_root.mkdir()
    (sprite_root / "flat_sign.xp").write_text("fixture")
    payload = edits.install_build(run_root, "agent_cli_probe", None, False, True)
    assert payload["ok"] is True
    assert payload["executed"] is False
    assert payload["target_map"] == "godot_project/assets/maps/agent_cli_probe.a3d"
    assert payload["mesh_count"] == len(edits._referenced_asset_names(MAP)[0])
    assert payload["sprite_count"] == 1


def test_pipeline_install_commits_one_isolated_package(
        edits, monkeypatch, tmp_path) -> None:
    run_root = tmp_path / "run"
    run_root.mkdir()
    shutil.copy2(MAP, run_root / "output.a3d")
    fake_repo = tmp_path / "repo"
    monkeypatch.setattr(edits, "REPO_ROOT", fake_repo)
    monkeypatch.setattr(edits, "rebuild_products",
                        lambda map_path, runtime_terrain=None: {"ok": True, "map": str(map_path)})
    monkeypatch.setattr(edits, "verify_loadability",
                        lambda map_path: {"ok": True, "editor_ready": True,
                                          "map": str(map_path)})
    payload = edits.install_build(run_root, "agent_cli_probe", None, False, False)
    installed = fake_repo / "godot_project/assets/maps/agent_cli_probe.a3d"
    mesh_root = fake_repo / "godot_project/assets/world_mesh/agent_cli_probe.meshes"
    assert payload["executed"] is True
    assert installed.read_bytes() == MAP.read_bytes()
    assert mesh_root.is_dir()


def test_cli_help_names_the_complete_agent_route() -> None:
    result = subprocess.run([sys.executable, str(CLI), "--help"], cwd=ROOT,
                            text=True, capture_output=True, check=True)
    for command in ("operations", "inspect", "assets", "import-asset", "edit", "batch", "validate",
                    "race-validate", "race-build", "rebuild-products",
                    "verify-loadability", "install-build", "launch"):
        assert command in result.stdout
