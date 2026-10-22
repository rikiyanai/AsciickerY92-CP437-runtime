"""Ownership and exact native-map regressions for the migrated Numabe editor."""
import hashlib
import importlib.util
import json
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "numabe"
MANUAL = WORKSPACE / "artifacts/experiments/fl4876_manual_world"
BUILD = WORKSPACE / ".run/fl4876_builds/live_numabe"
sys.path.insert(0, str(MANUAL))
import a3d_edit as ED


def sha(path):
    """Bind assertions to bytes actually present, not a historical receipt."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_only_obsolete_station_facade_changed_in_native_map():
    """The operation may remove one old facade, never tracks, terrain or signs."""
    before = BUILD / "output.a3d.pre_edit_d40d35d4"
    assert sha(before) == "d40d35d440b00493ce646710dc5b07b3486fa5fab3c65fa4690c80fbe751bdf5"
    prefix0, version0, records0, tail0 = ED.parse(before.read_bytes())
    prefix1, version1, records1, tail1 = ED.parse((BUILD / "output.a3d").read_bytes())
    assert (prefix0, version0, tail0) == (prefix1, version1, tail1)
    obsolete = "facade_feature_facade_plane_numabe_station_entrance_1"
    assert len(records0) - len(records1) == 1
    assert records1 == [r for r in records0 if r.get("inst") != obsolete]
    assert (BUILD / "meshes" / (obsolete + ".akm")).is_file()
    assert any("infrastructure_station_entrance" in r.get("inst", "") for r in records1)


def test_train_routes_and_sign_sidecars_preserved():
    """The post-edit world keeps the original dynamic routes and native XP files."""
    manifest = json.loads((WORKSPACE / "transfer_manifest.json").read_text())
    for path in [BUILD / "output.trains.json", *sorted((BUILD / "output.sprites").glob("*"))]:
        assert sha(path) == manifest["files"][str(path.relative_to(WORKSPACE))]["sha256"]
    assert len(list((BUILD / "output.sprites").glob("*.xp"))) == 19
    assert len(json.loads((BUILD / "output.trains.json").read_text())["routes"]) == 2


def test_runtime_and_panorama_paths_belong_to_destination():
    """The public check resolves all runtime assets and every local panorama."""
    spec = importlib.util.spec_from_file_location("numabe_run", ROOT / "tools/numabe/run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    result = mod.check()
    assert result["mesh_instances"] == 3646
    assert not result["missing_meshes"] and not result["missing_panoramas"]
    assert not result["retired_track_obstruction"]
    assert result["panoramas"] > 300


def test_editor_does_not_promise_false_first_person_gameplay():
    """Do not regress by relabelling a trailing camera or mesh preview as a game."""
    source = (MANUAL / "map_editor_ui/editor_views.js").read_text()
    assert "'bFP', 'First person'" not in source
    assert "'bFP', 'Walk (Classic)'" in source
    assert "Classic first-person gameplay is not implemented" in source
    assert "EYE-LEVEL GEOMETRY PREVIEW · NOT GAMEPLAY" in source
    assert "$('evEye').disabled = !!st.fp.game" in source


def test_active_python_helpers_do_not_point_back_to_y9():
    """Historical journals may cite Y9; active tools must not write there."""
    for name in ("train_capture.py", "capture_lock.py", "pano_crop.py", "face_proj.py", "face_gap.py", "ray_plan.py"):
        assert "/Users/r/Projects/asciicker-Y9-2" not in (MANUAL / name).read_text()


def test_legacy_js_helpers_use_destination_and_are_syntactically_valid():
    """Syntax checks only; agent browser interactions must use computer use."""
    for folder in ("map_editor_ui", "facade_capture_checks"):
        for path in (MANUAL / folder).glob("*.mjs"):
            assert "/Users/r/Projects/asciicker-Y9-2" not in path.read_text(), path
            assert "8791" not in path.read_text(), path
            subprocess.run(["node", "--check", str(path)], check=True, capture_output=True)


def test_launcher_rejects_resurrected_obsolete_slab(monkeypatch):
    """A negative control rejects the original map without mutating either map."""
    spec = importlib.util.spec_from_file_location("numabe_guard", ROOT / "tools/numabe/run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    import pytest
    before = ED.parse((BUILD / "output.a3d.pre_edit_d40d35d4").read_bytes())
    monkeypatch.setattr(ED, "parse", lambda data: before)
    with pytest.raises(SystemExit, match="retired_track_obstruction"):
        mod.check()


def test_removal_has_destination_journal_and_matching_recovery_copy():
    """Saving the map without its durable receipt was a prior editor defect."""
    journal = [json.loads(line) for line in (MANUAL / "map_edits/journal.jsonl").read_text().splitlines()]
    row = journal[-1]
    # Historical journal paths record where the edit happened. Recovery uses
    # the retained local basename, never the old computer's absolute path.
    assert Path(row["map"]).name == "output.a3d"
    assert sha(BUILD / "output.a3d") == row["sha256_after"]
    assert sha(BUILD / Path(row["kept"]).name) == row["sha256_before"]
    assert row["edits"][0]["op"] == "delete"
