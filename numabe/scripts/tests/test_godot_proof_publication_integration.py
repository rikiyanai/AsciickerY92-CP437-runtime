"""Integration contract for the shared publication boundary on FL-4359 output."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import pytest

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import run_fl4359_headed_movement as headed
import run_fl4265_hunter_catch as hunter
from godot_proof_publication import PublicationError, load_manifest, validate_manifest


def _receipt(mode: str = "FC") -> dict:
    return {
        # This is deliberately present: the publisher must still refuse to
        # upgrade the run to ATTESTED without an executed-source inventory.
        "run_identity": {
            "build_head": "a" * 40,
            "build_dirty": True,
            "build_worktree_diff_sha256": "b" * 64,
        },
        "native_frame_capture": {
            "frames": [{
                "dispatch": {
                    "cell_size": [32, 32],
                    "matrix_identity": {"schema": "fl4359.matrix_configuration_identity.v1", "key": "synthetic-fixture-matrix"},
                    "policy": {
                        "status": "applied",
                        "mode": mode,
                    },
                },
            }],
        },
        "final_render_metadata": {"cell_dims": {"glyph_w": 40, "glyph_h": 32}},
    }


def test_heading_publication_uses_observed_policy_but_stays_unattested(tmp_path):
    capture = tmp_path / "jitter_capture.jsonl"
    capture.write_text("captured runtime rows\n", encoding="utf-8")
    metadata = headed._publication_render_metadata(_receipt("FC"), capture)

    assert metadata["provenance_status"] == "INCOMPLETE_CURRENT_CAPTURE"
    # FC and the cell lattice are observed, while glyph-source provenance stays
    # explicitly UNKNOWN rather than borrowing a display glyph dimension.
    assert metadata["proof_kind"] == "RENDER"
    assert metadata["foreground_mode"] == "FLAT"
    assert metadata["background_mode"] == "CONTINUOUS"
    assert metadata["glyph_source_resolution"] == "UNKNOWN"
    assert metadata["source_commit"] == "a" * 40
    assert metadata["effective_tree_fingerprint"] == "UNKNOWN"


def test_native_dispatch_cell_size_is_sibling_of_observed_policy(tmp_path):
    capture = tmp_path / "capture.jsonl"
    capture.write_text("observed\n")
    receipt = _receipt("FF")
    receipt["native_frame_capture"]["frames"][0]["dispatch"]["cell_size"] = [16, 32]
    metadata = headed._publication_render_metadata(receipt, capture)
    assert metadata["foreground_mode"] == metadata["background_mode"] == "FLAT"
    assert metadata["cell_resolution"] == {"width": 16, "height": 32}


def test_shared_ff_publication_labels_flat_channels_without_attesting_true_ff(tmp_path):
    capture = tmp_path / "capture.jsonl"
    capture.write_text("observed\n")
    receipt = _receipt("FF_SHARED")
    policy = receipt["native_frame_capture"]["frames"][0]["dispatch"]["policy"]
    policy.update(driver="shared_compositor", sampling_mode="FF", fg_sampling="flat",
                  bg_sampling="flat", bg_owner="family")
    assert headed._applied_policy_matches_mode(policy)
    metadata = headed._publication_render_metadata(receipt, capture)
    assert metadata["foreground_mode"] == metadata["background_mode"] == "FLAT"
    assert metadata["provenance_status"] == "INCOMPLETE_CURRENT_CAPTURE"
    assert metadata["architecture"] == "UNKNOWN"
    policy["driver"] = "standalone_true_ff"
    assert not headed._applied_policy_matches_mode(policy)


def test_conflicting_native_dispatch_sizes_cannot_receive_one_cell_label():
    receipt = _receipt()
    receipt["native_frame_capture"]["frames"].append({"dispatch": {
        "cell_size": [16, 32], "policy": {"status": "applied", "mode": "FC"}}})
    assert headed._publication_observed_policy(receipt) == {}


def test_missing_native_matrix_identity_cannot_receive_observed_labels():
    receipt = _receipt()
    receipt["native_frame_capture"]["frames"][0]["dispatch"].pop("matrix_identity")
    assert headed._publication_observed_policy(receipt) == {}


def test_heading_publisher_preserves_legacy_visual_as_hardlink_alias(tmp_path, monkeypatch):
    capture = tmp_path / "jitter_capture.jsonl"
    capture.write_text("captured runtime rows\n", encoding="utf-8")
    legacy = tmp_path / "movement_grid.png"
    Image.new("RGB", (16, 12), "red").save(legacy)
    # Finder tags are owned and tested by the core publisher. This test focuses
    # on the producer's finalized paths and compatibility alias contract.
    monkeypatch.setattr(headed, "apply_finder_tags", lambda path: {})

    manifest_path = headed._publish_visual_artifacts(tmp_path, _receipt(), capture)
    manifest = load_manifest(manifest_path)

    assert validate_manifest(manifest_path, allow_incomplete=True) == []
    assert "INCOMPLETE_CURRENT_CAPTURE is diagnostic only, not a publishable proof" in validate_manifest(
        manifest_path
    )
    artifact = next(
        row for row in manifest["artifacts"] if row["aliases"] == ["movement_grid.png"]
    )
    canonical = tmp_path / artifact["path"]
    assert canonical.name == artifact["expected_filename"]
    assert artifact["aliases"] == ["movement_grid.png"]
    assert hashlib.sha256(canonical.read_bytes()).hexdigest() == hashlib.sha256(legacy.read_bytes()).hexdigest()
    assert canonical.stat().st_ino == legacy.stat().st_ino


def test_missing_runtime_policy_never_uses_requested_mode_for_tags(tmp_path):
    capture = tmp_path / "jitter_capture.jsonl"
    capture.write_text(json.dumps({"requested_mode": "CC"}), encoding="utf-8")
    metadata = headed._publication_render_metadata({"run_identity": {}}, capture)

    assert metadata["proof_kind"] == "RENDER"
    assert metadata["foreground_mode"] == "UNKNOWN"
    assert metadata["background_mode"] == "UNKNOWN"


def test_heading_publication_keeps_matching_scoped_snapshot_as_diagnostic_fact(tmp_path):
    capture = tmp_path / "jitter_capture.jsonl"
    capture.write_text("captured runtime rows\n", encoding="utf-8")
    receipt = _receipt()
    receipt["publication_runtime_source_identity"] = {
        "scoped_snapshots_match": True,
        "launch": {
            "source_commit": "c" * 40,
            "effective_tree_fingerprint": "d" * 64,
        },
    }
    metadata = headed._publication_render_metadata(receipt, capture)

    assert metadata["source_commit"] == "c" * 40
    assert metadata["effective_tree_fingerprint"] == "d" * 64
    assert metadata["provenance_status"] == "INCOMPLETE_CURRENT_CAPTURE"


def _init_identity_repo(root: Path) -> Path:
    source = root / "source"
    source.mkdir()
    (source / "tracked.txt").write_text("first\n", encoding="utf-8")
    for args in (
        ("init", "-q"),
        ("config", "user.email", "proof@example.invalid"),
        ("config", "user.name", "Proof Fixture"),
        ("add", "."),
        ("commit", "-q", "-m", "initial"),
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True)
    return source


def test_runtime_source_identity_distinguishes_overlay_and_commit(tmp_path, monkeypatch):
    source = _init_identity_repo(tmp_path)
    monkeypatch.setattr(headed, "ROOT", tmp_path)

    original = headed._runtime_source_identity([source])
    assert original["valid"] is True
    assert original["fingerprint_schema"] == "fl4359-scoped-source-identity-v3"

    (source / "tracked.txt").write_text("dirty overlay\n", encoding="utf-8")
    dirty = headed._runtime_source_identity([source])
    assert dirty["effective_tree_fingerprint"] != original["effective_tree_fingerprint"]

    (source / "untracked.txt").write_text("untracked overlay\n", encoding="utf-8")
    untracked = headed._runtime_source_identity([source])
    assert untracked["effective_tree_fingerprint"] != dirty["effective_tree_fingerprint"]
    assert untracked["untracked_paths"] == ["source/untracked.txt"]

    subprocess.run(["git", "-C", str(tmp_path), "add", "source/tracked.txt", "source/untracked.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "next"], check=True)
    committed = headed._runtime_source_identity([source])
    assert committed["source_commit"] != original["source_commit"]
    assert committed["effective_tree_fingerprint"] != original["effective_tree_fingerprint"]


def test_runtime_source_identity_rejects_missing_and_outside_scope(tmp_path, monkeypatch):
    source = _init_identity_repo(tmp_path)
    monkeypatch.setattr(headed, "ROOT", tmp_path)

    missing = headed._runtime_source_identity([source / "missing"])
    assert missing == {"valid": False, "reason": f"scope_missing:{source / 'missing'}"}

    outside = headed._runtime_source_identity([tmp_path.parent])
    assert outside["valid"] is False
    assert outside["reason"].startswith("scope_outside_repo:")


def test_documentation_commit_does_not_change_scoped_runtime_identity(tmp_path, monkeypatch):
    source = _init_identity_repo(tmp_path)
    monkeypatch.setattr(headed, "ROOT", tmp_path)
    launch = headed._runtime_source_identity([source])
    # The fixture owns docs/; exercise a real documentation commit without
    # creating ownerless root Markdown rejected by the installed policy hook.
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "fixture.md").write_text("concurrent documentation\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "docs/fixture.md"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "docs"], check=True)
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"pass": true}')

    def observer_return():
        try:
            return "observer finished"
        finally:
            headed._persist_runtime_source_identity(receipt, launch, [source])

    assert observer_return() == "observer finished"
    result = json.loads(receipt.read_text())
    assert result["pass"] is True
    identity = result["publication_runtime_source_identity"]
    assert identity["source_commit_matches"] is False
    assert identity["scoped_snapshots_match"] is True
    assert json.loads((tmp_path / "runtime_source_identity.json").read_text()) == identity
    (source / "tracked.txt").write_text("changed runtime\n")
    headed._persist_runtime_source_identity(receipt, launch, [source])
    assert json.loads(receipt.read_text())["publication_runtime_source_identity"]["scoped_snapshots_match"] is False


def test_source_identity_survives_missing_runtime_receipt(tmp_path, monkeypatch):
    source = _init_identity_repo(tmp_path)
    monkeypatch.setattr(headed, "ROOT", tmp_path)
    launch = headed._runtime_source_identity([source])
    headed._persist_runtime_source_identity(tmp_path / "receipt.json", launch, [source])
    assert not (tmp_path / "receipt.json").exists()
    assert json.loads((tmp_path / "runtime_source_identity.json").read_text())["scoped_snapshots_match"] is True


def test_ignored_runtime_scope_hashes_actual_files(tmp_path, monkeypatch):
    _init_identity_repo(tmp_path)
    monkeypatch.setattr(headed, "ROOT", tmp_path)
    (tmp_path / ".gitignore").write_text(".run/\n*.bin\n")
    source = tmp_path / ".run" / "runtime"
    source.mkdir(parents=True)
    (source / "paint.gd").write_text("first")
    (source / "capture.bin").write_bytes(b"first")
    first = headed._runtime_source_identity([source])
    assert first["valid"]
    assert len(first["executed_source_files"]) == 2
    (source / "capture.bin").write_bytes(b"second")
    second = headed._runtime_source_identity([source])
    assert second["effective_tree_fingerprint"] != first["effective_tree_fingerprint"]
    cache = source / ".godot" / "shader_cache"
    cache.mkdir(parents=True)
    (cache / "generated.bin").write_bytes(b"cache")
    assert headed._runtime_source_identity([source])["effective_tree_fingerprint"] == second["effective_tree_fingerprint"]


def test_runtime_identity_streams_large_files_and_rejects_empty_scope(tmp_path, monkeypatch):
    source = _init_identity_repo(tmp_path)
    monkeypatch.setattr(headed, "ROOT", tmp_path)
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(AssertionError("unbounded read")))
    (source / "large.bin").write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    identity = headed._runtime_source_identity([source])
    assert identity["valid"]
    empty = tmp_path / "empty"
    empty.mkdir()
    assert headed._runtime_source_identity([empty])["reason"] == "source_scope_contains_no_files"


def test_runtime_identity_includes_imported_resources_and_class_cache(tmp_path, monkeypatch):
    source = _init_identity_repo(tmp_path)
    monkeypatch.setattr(headed, "ROOT", tmp_path)
    imported = source / ".godot" / "imported"
    imported.mkdir(parents=True)
    resource = imported / "atlas.ctex"
    resource.write_bytes(b"old texture")
    first = headed._runtime_source_identity([source])
    resource.write_bytes(b"changed texture")
    second = headed._runtime_source_identity([source])
    assert first["effective_tree_fingerprint"] != second["effective_tree_fingerprint"]
    (imported.parent / "global_script_class_cache.cfg").write_text("new class mapping")
    third = headed._runtime_source_identity([source])
    assert second["effective_tree_fingerprint"] != third["effective_tree_fingerprint"]


def _hunter_fixture(tmp_path, monkeypatch):
    """Synthetic publisher fixture, not live hunter or rendering evidence."""
    captures = tmp_path / "captures"
    atlas = captures / "atlas"
    atlas.mkdir(parents=True)
    page = Image.new("RGBA", (8, 16), "white")
    page.save(atlas / "page_0.png")
    frame = captures / "hunter_0.png"
    Image.new("RGB", (512, 320), "blue").save(frame)
    identity = {
        "schema": "fl4265.capture_identity.v1", "image_path": str(frame),
        "image_dimensions": [512, 320], "presentation_mode": "CC",
        "architecture": "PerspectiveProbePipeline", "architecture_script": "res://backend.gd",
        "pixel_camera": {"owner": "PixelCameraContext", "cell_size": [8, 16],
                         "frame_index": 50, "frame_time_seconds": 2.5},
        "engine": {"string": "4.7.2.stable.official.ed1daf0bf"},
        "target": "macOS", "scene": "res://scenes/Main.tscn", "map": "game_map_y8",
        "proof_capture_validity": {"owner": "Main shared occluder/blank guards",
            "occluder": "", "blank": False, "samples": 2304, "luma_spread": 0.7},
        "atlas_identity": {"sources": [{"dimensions": {"w": 8, "h": 16},
            "path": "res://atlas.png", "hash_kind": "decoded_rgba8",
            "sha256": hashlib.sha256(page.tobytes()).hexdigest()}]},
    }
    sidecar = frame.with_name(frame.name + ".identity.json")
    sidecar.write_text(json.dumps(identity))
    receipt = {"publication_runtime_source_identity": {
        "scoped_snapshots_match": True, "launch": {
            "valid": True, "source_commit": "a" * 40, "effective_tree_fingerprint": "b" * 64,
            "dirty_paths": [], "untracked_paths": ["godot_project/untracked.gd"],
            "executed_source_files": [{"path": "godot_project/untracked.gd", "sha256": "c" * 64}],
        },
    }}
    build = {"kind": "direct_source_engine", "path": "/engine/Godot", "sha256": "d" * 64}
    monkeypatch.setattr(hunter, "apply_finder_tags", lambda path: {})
    monkeypatch.setattr(hunter, "verify_finder_tags", lambda path: [])
    return frame, sidecar, identity, receipt, build


def test_hunter_publisher_attests_exact_aliases_and_untracked_source(tmp_path, monkeypatch):
    frame, _, _, receipt, build = _hunter_fixture(tmp_path, monkeypatch)
    manifest_path = hunter._publish_captures(tmp_path, "hunter-CC-fixture", receipt, build)
    assert validate_manifest(manifest_path) == []
    manifest = load_manifest(manifest_path)
    assert manifest["metadata"]["capture_source_state"] == "DIRTY"
    assert manifest["metadata"]["executed_sources"][0]["path"].endswith("untracked.gd")
    artifact = next(row for row in manifest["artifacts"] if row["aliases"] == ["captures/hunter_0.png"])
    assert (tmp_path / artifact["path"]).stat().st_ino == frame.stat().st_ino


@pytest.mark.parametrize("defect", ["missing", "wrong_path", "dimensions", "frame", "atlas", "source_change", "build", "guard_missing", "occluded", "blank", "spread", "samples"])
def test_hunter_publisher_refuses_missing_or_contradictory_identity(tmp_path, monkeypatch, defect):
    _, sidecar, identity, receipt, build = _hunter_fixture(tmp_path, monkeypatch)
    if defect == "missing":
        sidecar.unlink()
    elif defect == "wrong_path":
        identity["image_path"] = str(tmp_path / "other.png")
    elif defect == "dimensions":
        identity["image_dimensions"] = [1, 1]
    elif defect == "frame":
        identity["pixel_camera"].pop("frame_index")
    elif defect == "atlas":
        identity["atlas_identity"]["sources"][0]["sha256"] = "0" * 64
    elif defect == "source_change":
        receipt["publication_runtime_source_identity"]["scoped_snapshots_match"] = False
    elif defect == "build":
        build = {}
    elif defect == "guard_missing":
        identity.pop("proof_capture_validity")
    elif defect == "occluded":
        identity["proof_capture_validity"]["occluder"] = "pause_menu_open"
    elif defect == "blank":
        identity["proof_capture_validity"]["blank"] = True
    elif defect == "spread":
        identity["proof_capture_validity"]["luma_spread"] = 0.01
    elif defect == "samples":
        identity["proof_capture_validity"]["samples"] = 0
    if defect != "missing":
        sidecar.write_text(json.dumps(identity))
    with pytest.raises(PublicationError):
        hunter._publish_captures(tmp_path, "hunter-CC-fixture", receipt, build)


def test_hunter_publisher_refuses_mixed_capture_modes(tmp_path, monkeypatch):
    frame, _, identity, receipt, build = _hunter_fixture(tmp_path, monkeypatch)
    second = frame.with_name("hunter_1.png")
    second.write_bytes(frame.read_bytes())
    identity.update(image_path=str(second), presentation_mode="CF")
    second.with_name(second.name + ".identity.json").write_text(json.dumps(identity))
    with pytest.raises(PublicationError, match="identities changed"):
        hunter._publish_captures(tmp_path, "hunter-mixed-fixture", receipt, build)


def test_hunter_publisher_names_real_three_atlas_footprint(tmp_path, monkeypatch):
    _, sidecar, identity, receipt, build = _hunter_fixture(tmp_path, monkeypatch)
    sources = []
    for page, size in enumerate([(640, 640), (512, 320), (512, 2432)]):
        atlas = Image.new("RGBA", size, "white")
        atlas.save(tmp_path / "captures" / "atlas" / f"page_{page}.png")
        sources.append({"dimensions": {"w": size[0], "h": size[1]},
            "path": f"res://page_{page}.png", "hash_kind": "decoded_rgba8",
            "sha256": hashlib.sha256(atlas.tobytes()).hexdigest()})
    identity["atlas_identity"]["sources"] = sources
    sidecar.write_text(json.dumps(identity))
    manifest = hunter._publish_captures(tmp_path, "hunter-cc472-20261001", receipt, build)
    assert validate_manifest(manifest) == []
