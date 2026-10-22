"""Contract tests for importing immutable FL-4359 naming maps."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from godot_proof_publication import PublicationError, load_manifest, validate_manifest
from publish_fl4359_historical_media import historical_metadata, prepare_map, publish_map


class HistoricalPublicationTests(unittest.TestCase):
    def test_dual_atlas_is_not_rectangular_and_diff_is_not_effective_tree(self):
        result = historical_metadata({"mode_fg_bg": "FC", "cell_px": "32x32",
                                      "glyph_source_px": "40-32", "dirty_diff_sha256": "a" * 64})
        self.assertEqual(result["foreground_mode"], "FLAT")
        self.assertEqual(result["background_mode"], "CONTINUOUS")
        self.assertEqual(result["glyph_source_resolution"],
                         [{"width": 40, "height": 40}, {"width": 32, "height": 32}])
        self.assertEqual(result["effective_tree_fingerprint"], "UNKNOWN")
        self.assertEqual(result["run_id"], "UNKNOWN")

    def test_unknown_is_not_not_applicable(self):
        result = historical_metadata({})
        for key in ("foreground_mode", "background_mode", "cell_resolution", "glyph_source_resolution"):
            self.assertEqual(result[key], "UNKNOWN")

    def make_map(self, root):
        image = root / "immutable.png"
        Image.new("RGB", (16, 8), "green").save(image)
        row = {"old_path": "old.png", "new_path": image.name,
               "bytes": image.stat().st_size,
               "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
               "attributes": {"architecture": "UNKNOWN", "mode_fg_bg": "FF",
                              "git_state": "UNKNOWN", "cell_px": "32x32",
                              "glyph_source_px": "UNKNOWN", "image_px": [16, 8],
                              "frame_count": 1}}
        path = root / "media_name_map.json"
        path.write_text(json.dumps({"schema": "fl4359.media_names.v1", "renames": [row]}))
        return path, image

    def test_publication_preserves_map_name_bytes_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, image = self.make_map(root)
            before = (path.read_bytes(), image.read_bytes())
            manifests = publish_map(path)
            self.assertEqual(len(manifests), 1)
            self.assertEqual(validate_manifest(manifests[0]), [])
            manifest = load_manifest(manifests[0])
            self.assertEqual(manifest["artifacts"][0]["filename_policy"], "HISTORICAL_PRESERVED")
            self.assertEqual((path.read_bytes(), image.read_bytes()), before)
            image.write_bytes(b"tampered")
            with self.assertRaisesRegex(PublicationError, "bytes changed"):
                prepare_map(path)

    def test_target_cannot_escape_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = self.make_map(root)
            data = json.loads(path.read_text())
            data["renames"][0]["new_path"] = "../outside.png"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(PublicationError, "escapes collection"):
                prepare_map(path)

    def test_grid_keeps_each_pane_and_resolves_relative_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "grid.gif"
            Image.new("RGB", (16, 8), "green").save(image)
            receipt = root / "pane.json"
            receipt.write_text("{}")
            modes = ["FF", "FC", "CF", "CC"]
            data = {
                "schema": "fl4359.grid_media_name_map.v1",
                "architecture": "TEXEL_SPLAT", "cell_px": [32, 32],
                "glyph_source": "40-32",
                "pane_positions": dict(zip(modes, ["top-left", "top-right", "bottom-left", "bottom-right"])),
                "panes": {mode: {"receipt_path": receipt.name,
                                  "receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
                                  "source_dirty": True, "source_commit": str(index) * 40}
                          for index, mode in enumerate(modes, 1)},
                "mappings": [{"new_relative": image.name, "bytes": image.stat().st_size,
                              "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
                              "dimensions": [16, 8], "role": "native", "frame_count": 1}],
            }
            path = root / "media_name_map.json"
            path.write_text(json.dumps(data))
            manifest = load_manifest(publish_map(path)[0])
            artifact = manifest["artifacts"][0]
            self.assertIn("GODOT_PROOF:MODE-MIXED", artifact["expected_finder_tags"])
            panes = artifact["capture_metadata"]["panes"]
            self.assertEqual([pane["pane_id"] for pane in panes], modes)
            self.assertEqual([pane["source_commit"] for pane in panes], [str(n) * 40 for n in range(1, 5)])
            self.assertTrue(all(pane["effective_tree_fingerprint"] == "UNKNOWN" for pane in panes))
            receipt.write_text("changed")
            with self.assertRaisesRegex(PublicationError, "receipt bytes changed"):
                prepare_map(path)


if __name__ == "__main__":
    unittest.main()
