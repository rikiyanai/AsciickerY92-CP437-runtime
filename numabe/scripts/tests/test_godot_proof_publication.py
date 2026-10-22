"""Focused contract tests for shared Godot proof publication."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

from PIL import Image


SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from godot_proof_publication import (
    ATTESTED,
    HISTORICAL_UNKNOWN,
    INCOMPLETE_CURRENT_CAPTURE,
    PublicationError,
    TAG_ATTRIBUTE,
    apply_finder_tags,
    deterministic_artifact_name,
    finder_tags,
    publish_artifacts,
    sha256_file,
    validate_manifest,
    verify_finder_tags,
)


def _metadata(status: str = INCOMPLETE_CURRENT_CAPTURE) -> dict:
    return {
        "provenance_status": status,
        "fl_id": "FL-4359",
        "run_id": "headed-movement-r1",
        "proof_kind": "RENDER",
        "architecture": "TEXEL_SPLAT",
        "foreground_mode": "FLAT",
        "background_mode": "FLAT",
        "capture_source_state": "UNKNOWN",
        "source_commit": "UNKNOWN",
        "effective_tree_fingerprint": "UNKNOWN",
        "cell_resolution": {"width": 32, "height": 32},
        "glyph_source_resolution": [{"width": 40, "height": 40}, {"width": 32, "height": 32}],
        "storage": {"status": "PENDING"},
    }


def _attested_metadata() -> dict:
    value = _metadata(ATTESTED)
    source = "a" * 40
    digest = "b" * 64
    value.update({
        "capture_source_state": "CLEAN",
        "source_commit": source,
        "effective_tree_fingerprint": digest,
        "executed_sources": [{"path": "godot_project/scripts/World.gd", "sha256": "c" * 64}],
        "build_identity": {"kind": "editor", "path": "godot_project/project.godot", "sha256": "d" * 64},
        "runtime_identity": {"target": "macos-editor", "engine_version": "4.6.1"},
        "workload_identity": {
            "runner": "scripts/run_fl4359_headed_movement.py", "scene": "World.tscn",
            "map": "main", "pose": "headed", "camera": "perspective", "timing": "five-60ms",
        },
    })
    return value


class GodotProofPublicationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _artifact(self, metadata: dict, *, role: str = "sweep", name: str | None = None,
                  aliases: list[Path] | None = None) -> dict:
        draft = {
            "role": role, "frame_count": 1, "image_dimensions": {"width": 7, "height": 5},
            "extension": ".png",
        }
        filename = name or deterministic_artifact_name(metadata, draft)
        path = self.root / filename
        Image.new("RGB", (7, 5), "red").save(path)
        draft["path"] = path
        if aliases:
            draft["aliases"] = aliases
        return draft

    def _publish(self, metadata: dict, artifact: dict) -> Path:
        manifest = self.root / "publication.json"
        publish_artifacts(manifest, metadata, [artifact], artifact_root=self.root)
        return manifest

    def test_incomplete_current_capture_is_explicit_and_not_publishable(self):
        metadata = _metadata()
        manifest = self._publish(metadata, self._artifact(metadata))
        self.assertIn("INCOMPLETE_CURRENT_CAPTURE", " ".join(validate_manifest(manifest)))
        self.assertEqual(validate_manifest(manifest, allow_incomplete=True), [])

    def test_attested_capture_requires_executed_build_runtime_and_workload_identity(self):
        metadata = _metadata(ATTESTED)
        metadata.update({
            "capture_source_state": "CLEAN", "source_commit": "a" * 40,
            "effective_tree_fingerprint": "b" * 64,
        })
        with self.assertRaisesRegex(PublicationError, "executed_sources"):
            self._artifact(metadata)
        metadata = _attested_metadata()
        manifest = self._publish(metadata, self._artifact(metadata))
        self.assertEqual(validate_manifest(manifest), [])

    def test_historical_unknown_preserves_unknown_fields_and_old_filename(self):
        metadata = _metadata(HISTORICAL_UNKNOWN)
        metadata.update({
            "architecture": "UNKNOWN", "foreground_mode": "UNKNOWN", "background_mode": "UNKNOWN",
            "capture_source_state": "MIXED", "cell_resolution": "UNKNOWN", "glyph_source_resolution": "UNKNOWN",
        })
        artifact = self._artifact(metadata, name="immutable-original.png")
        artifact["filename_policy"] = "HISTORICAL_PRESERVED"
        manifest = self._publish(metadata, artifact)
        self.assertEqual(validate_manifest(manifest), [])
        content = json.loads(manifest.read_text())
        self.assertIn("GODOT_PROOF:CELL-UNKNOWN", content["artifacts"][0]["expected_finder_tags"])
        self.assertIn("GODOT_PROOF:GLYPH-UNKNOWN", content["artifacts"][0]["expected_finder_tags"])

    def test_actual_visual_facts_and_recomputed_identity_are_verified(self):
        metadata = _metadata()
        manifest = self._publish(metadata, self._artifact(metadata))
        content = json.loads(manifest.read_text())
        content["artifacts"][0]["image_dimensions"] = {"width": 8, "height": 5}
        content["artifacts"][0]["expected_finder_tags"] = []
        manifest.write_text(json.dumps(content))
        errors = validate_manifest(manifest, allow_incomplete=True)
        self.assertTrue(any("image_dimensions" in error for error in errors))
        self.assertTrue(any("expected_finder_tags" in error for error in errors))

    def test_aliases_must_be_present_inside_root_and_byte_identical(self):
        metadata = _metadata()
        artifact = self._artifact(metadata)
        alias = self.root / "legacy-sweep.png"
        os.link(artifact["path"], alias)
        artifact["aliases"] = [alias]
        manifest = self._publish(metadata, artifact)
        self.assertEqual(validate_manifest(manifest, allow_incomplete=True), [])
        content = json.loads(manifest.read_text())
        content["artifacts"][0]["aliases"] = ["../../outside.png"]
        manifest.write_text(json.dumps(content))
        self.assertTrue(any("escapes artifact_root" in error for error in validate_manifest(manifest, allow_incomplete=True)))

    def test_fractional_numeric_fields_are_rejected(self):
        metadata = _metadata()
        with self.assertRaisesRegex(PublicationError, "positive integer"):
            deterministic_artifact_name(metadata, {
                "role": "sweep", "frame_count": 1.5,
                "image_dimensions": {"width": 7, "height": 5}, "extension": ".png",
            })

    def test_mixed_pane_cannot_inherit_source_identity(self):
        metadata = _metadata(HISTORICAL_UNKNOWN)
        metadata.update({
            "architecture": "MIXED", "foreground_mode": "MIXED", "background_mode": "MIXED",
            "capture_source_state": "MIXED", "panes": [{"pane_id": "top-left", "architecture": "TEXEL_SPLAT"}],
        })
        with self.assertRaisesRegex(PublicationError, "must state its own metadata"):
            self._artifact(metadata)

    def test_incomplete_artifact_override_cannot_hide_under_historical_manifest(self):
        base = _metadata(HISTORICAL_UNKNOWN)
        override = _metadata(INCOMPLETE_CURRENT_CAPTURE)
        artifact = self._artifact(override)
        artifact["metadata"] = override
        manifest = self._publish(base, artifact)
        errors = validate_manifest(manifest)
        self.assertTrue(any("capture_metadata is INCOMPLETE_CURRENT_CAPTURE" in error for error in errors))

    def test_filename_policy_and_recorded_filename_cannot_be_forged(self):
        metadata = _metadata()
        manifest = self._publish(metadata, self._artifact(metadata))
        content = json.loads(manifest.read_text())
        content["artifacts"][0]["filename_policy"] = "WHATEVER"
        content["artifacts"][0]["filename"] = "different.png"
        manifest.write_text(json.dumps(content))
        errors = validate_manifest(manifest, allow_incomplete=True)
        self.assertTrue(any("filename_policy is invalid" in error for error in errors))
        self.assertTrue(any("filename does not match actual path" in error for error in errors))

    def test_tracked_storage_requires_real_git_tracking_and_receipt(self):
        metadata = _metadata(HISTORICAL_UNKNOWN)
        receipt = self.root / "archive-receipt.json"
        receipt.write_text("archive evidence\n")
        metadata["storage"] = {
            "status": "TRACKED", "location": ".", "failure_log": "docs/FAILURE_LOG.md#FL-4359",
            "godot_goal": "docs/plans/GODOT_GOAL.MD", "receipt_path": receipt.name,
            "receipt_sha256": sha256_file(receipt),
        }
        artifact = self._artifact(metadata, name="immutable-original.png")
        artifact["filename_policy"] = "HISTORICAL_PRESERVED"
        manifest = self._publish(metadata, artifact)
        errors = validate_manifest(manifest, require_storage=True)
        self.assertTrue(any("outside repository" in error or "not tracked by Git" in error for error in errors))

    def test_archive_storage_requires_byte_verified_members(self):
        metadata = _metadata(HISTORICAL_UNKNOWN)
        receipt = self.root / "archive-receipt.json"
        receipt.write_text("archive evidence\n")
        archive = self.root / "archive"
        archive.mkdir()
        metadata["storage"] = {
            "status": "ARCHIVED", "location": archive.name, "failure_log": "docs/FAILURE_LOG.md#FL-4359",
            "godot_goal": "docs/plans/GODOT_GOAL.MD", "receipt_path": receipt.name,
            "receipt_sha256": sha256_file(receipt),
        }
        artifact = self._artifact(metadata, name="immutable-original.png")
        artifact["filename_policy"] = "HISTORICAL_PRESERVED"
        manifest = self._publish(metadata, artifact)
        errors = validate_manifest(manifest, require_storage=True)
        self.assertTrue(any("archive_members" in error for error in errors))

    @unittest.skipUnless(sys.platform == "darwin", "Finder tags require macOS")
    def test_real_finder_tags_preserve_unrelated_tags_and_file_bytes(self):
        metadata = _metadata()
        artifact = self._artifact(metadata)
        manifest = self._publish(metadata, artifact)
        original = sha256_file(artifact["path"])
        try:
            result = subprocess.run(
                ["xattr", "-wx", TAG_ATTRIBUTE,
                 plistlib.dumps(["USER:Keep\n0"], fmt=plistlib.FMT_BINARY).hex(), str(artifact["path"])],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
        except OSError as error:
            self.skipTest(f"volume does not support Finder tags: {error}")
        if result.returncode:
            self.skipTest(f"volume does not support Finder tags: {result.stderr}")
        apply_finder_tags(manifest)
        self.assertEqual(sha256_file(artifact["path"]), original)
        self.assertIn("USER:Keep", finder_tags(artifact["path"]))
        self.assertEqual(verify_finder_tags(manifest), [])
        subprocess.run(["xattr", "-d", TAG_ATTRIBUTE, str(artifact["path"])], check=True)
        self.assertTrue(verify_finder_tags(manifest))
        apply_finder_tags(manifest)
        self.assertEqual(verify_finder_tags(manifest), [])

    @unittest.skipUnless(sys.platform == "darwin", "Finder tags require macOS")
    def test_finder_tags_restore_independent_aliases_and_select_each_path(self):
        metadata = _metadata()
        artifact = self._artifact(metadata)
        alias = self.root / "legacy-sweep.png"
        # Deliberately do not hard-link: Git/archive restoration may recreate
        # aliases as independent files with identical proof bytes.
        alias.write_bytes(artifact["path"].read_bytes())
        artifact["aliases"] = [alias]
        manifest = self._publish(metadata, artifact)
        canonical_hash = sha256_file(artifact["path"])
        alias_hash = sha256_file(alias)
        try:
            for path, user_tag in ((artifact["path"], "USER:Canonical"), (alias, "USER:Alias")):
                result = subprocess.run(
                    ["xattr", "-wx", TAG_ATTRIBUTE,
                     plistlib.dumps([f"{user_tag}\n0"], fmt=plistlib.FMT_BINARY).hex(), str(path)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                if result.returncode:
                    self.skipTest(f"volume does not support Finder tags: {result.stderr}")
        except OSError as error:
            self.skipTest(f"volume does not support Finder tags: {error}")

        # No selection restores both physical files, preserving each file's own
        # unrelated tag and leaving PNG bytes unchanged.
        applied = apply_finder_tags(manifest)
        self.assertIn(str(artifact["path"].resolve()), applied)
        self.assertIn(str(alias.resolve()), applied)
        self.assertEqual(sha256_file(artifact["path"]), canonical_hash)
        self.assertEqual(sha256_file(alias), alias_hash)
        self.assertIn("USER:Canonical", finder_tags(artifact["path"]))
        self.assertIn("USER:Alias", finder_tags(alias))
        self.assertEqual(verify_finder_tags(manifest), [])

        # Alias-relative selection is valid and does not hide a canonical tag
        # problem.  It restores only the selected legacy physical file.
        subprocess.run(["xattr", "-d", TAG_ATTRIBUTE, str(alias)], check=True)
        alias_errors = verify_finder_tags(manifest, artifact_paths=[alias.name])
        self.assertTrue(any(str(alias.resolve()) in error for error in alias_errors))
        self.assertEqual(verify_finder_tags(manifest, artifact_paths=[artifact["path"].name]), [])
        alias_applied = apply_finder_tags(manifest, artifact_paths=[alias.name])
        self.assertEqual(set(alias_applied), {str(alias.resolve())})
        self.assertEqual(verify_finder_tags(manifest), [])

        # Canonical-relative selection is likewise precise.
        subprocess.run(["xattr", "-d", TAG_ATTRIBUTE, str(artifact["path"])], check=True)
        canonical_applied = apply_finder_tags(manifest, artifact_paths=[artifact["path"].name])
        self.assertEqual(set(canonical_applied), {str(artifact["path"].resolve())})
        self.assertEqual(verify_finder_tags(manifest), [])


if __name__ == "__main__":
    unittest.main()
