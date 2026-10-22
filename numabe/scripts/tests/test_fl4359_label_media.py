import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fl4359_label_media import attributes, label_root, resolve_media_path, sha


class MediaNamesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def image(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (16, 16), "red").save(path)
        return path

    def test_unknown_does_not_claim_current_renderer(self):
        result = attributes(self.image("fl4359_modes_20260908/FF-r5/frame.png"), self.root)
        self.assertEqual(result["architecture"], "ARCH_UNKNOWN")
        self.assertEqual(result["mode_fg_bg"], "FF")
        self.assertEqual(result["cell_px"], "UNKNOWN")
        self.assertIsNone(result["source_commit"])

    def test_nested_raster_receipt_does_not_hide_identity(self):
        path = self.image("FF-r5/RASTER/frame.png")
        (path.parent / "receipt.json").write_text('{"pass": true}')
        (path.parent.parent / "receipt.json").write_text(json.dumps({"run_identity": {
            "build_head": "a" * 40, "build_dirty": True,
            "build_worktree_diff_sha256": "b" * 64, "glyph_matrix_cell_px": 32}}))
        result = attributes(path, self.root)
        self.assertEqual(result["source_commit"], "a" * 40)
        self.assertEqual(result["git_state"], "DIRTY")
        self.assertEqual(result["cell_px"], "32x32")

    def test_baseline_patch_and_atlas_are_distinct_from_runner(self):
        path = self.image("RAYMARCH_BASELINE/r2/atlas_sources/page_0.png")
        owner = self.root / "RAYMARCH_BASELINE"
        for name, patch in (("source_provenance.json", "c"), ("source_provenance_r2.json", "d")):
            (owner / name).write_text(json.dumps({"base_commit": "a" * 40, "patch_sha256": patch * 64}))
        (path.parent / "receipt.json").write_text(json.dumps({"run_identity": {
            "build_head": "b" * 40, "build_dirty": True, "build_worktree_diff_sha256": "e" * 64}}))
        result = attributes(path, self.root)
        self.assertEqual(result["source_commit"], "a" * 40)
        self.assertEqual(result["patch_sha256"], "d" * 64)
        self.assertIsNone(result["dirty_diff_sha256"])
        self.assertEqual(result["glyph_source_px"], "40x40")
        self.assertEqual(result["cell_px"], "32x32")

    def test_rename_is_lossless_idempotent_and_resolves_receipts(self):
        path = self.image("frame.png")
        original = sha(path)
        self.assertEqual(label_root(self.root, True)["new_renames"], 1)
        resolved = resolve_media_path(path)
        self.assertEqual(sha(resolved), original)
        self.assertEqual(label_root(self.root, True)["new_renames"], 0)
        resolved.rename(path)
        self.assertEqual(label_root(self.root, True)["new_renames"], 0)
        self.assertEqual(sha(resolve_media_path(path)), original)

    def test_existing_target_is_never_overwritten(self):
        path = self.image("frame.png")
        plan = label_root(self.root)["planned"][0]
        self.image(plan["new_path"])
        with self.assertRaises(RuntimeError):
            label_root(self.root, True)
        self.assertTrue(path.exists())

    def test_resolves_legacy_receipt_path_through_publication_alias(self):
        published = self.image("published/canonical-frame.png")
        (self.root / "godot_proof_publication.json").write_text(json.dumps({
            "schema": "godot-proof-publication.v1",
            "artifacts": [{
                "path": "published/canonical-frame.png",
                "aliases": ["legacy/frame.png"],
                "sha256": sha(published),
            }],
        }))
        resolved = resolve_media_path(self.root / "legacy/frame.png")
        self.assertEqual(resolved.resolve(), published.resolve())

    def test_publication_alias_rejects_changed_canonical_bytes(self):
        published = self.image("published/canonical-frame.png")
        (self.root / "godot_proof_publication.json").write_text(json.dumps({
            "schema": "godot-proof-publication.v1",
            "artifacts": [{
                "path": "published/canonical-frame.png",
                "aliases": ["legacy/frame.png"],
                "sha256": "0" * 64,
            }],
        }))
        with self.assertRaisesRegex(RuntimeError, "published media bytes changed"):
            resolve_media_path(self.root / "legacy/frame.png")

    def test_publication_alias_uses_relocated_artifact_root(self):
        root = self.root / "archive"
        published = self.image("archive/media/published/canonical-frame.png")
        (root / "godot_proof_publication.json").write_text(json.dumps({
            "schema": "godot-proof-publication.v1",
            "artifact_root": "media",
            "artifacts": [{
                "path": "published/canonical-frame.png",
                "aliases": ["legacy/frame.png"],
                "sha256": sha(published),
            }],
        }))
        self.assertEqual(
            resolve_media_path(root / "media/legacy/frame.png").resolve(),
            published.resolve(),
        )

    def test_publication_alias_rejects_path_escape(self):
        published = self.image("published/canonical-frame.png")
        (self.root / "godot_proof_publication.json").write_text(json.dumps({
            "schema": "godot-proof-publication.v1",
            "artifacts": [{
                "path": "../published/canonical-frame.png",
                "aliases": ["legacy/frame.png"],
                "sha256": sha(published),
            }],
        }))
        with self.assertRaisesRegex(RuntimeError, "escapes artifact_root"):
            resolve_media_path(self.root / "legacy/frame.png")


if __name__ == "__main__":
    unittest.main()
