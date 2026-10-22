"""Behavioral tests for visible proof labels and preserved source captures."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image, ImageChops

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from godot_proof_publication import deterministic_artifact_name, publish_artifacts, sha256_file, validate_manifest
from publish_godot_labeled_media import label_frame, label_lines, publish_labeled_media


class LabeledMediaTest(unittest.TestCase):
    def metadata(self, mode="FC"):
        return {"provenance_status": "INCOMPLETE_CURRENT_CAPTURE", "fl_id": "FL-4359",
                "run_id": "NATIVE-" + mode, "proof_kind": "RENDER", "architecture": "TEXEL_SPLAT",
                "foreground_mode": "FLAT" if mode[0] == "F" else "CONTINUOUS",
                "background_mode": "FLAT" if mode[1] == "F" else "CONTINUOUS",
                "capture_source_state": "DIRTY", "source_commit": "a" * 40,
                "effective_tree_fingerprint": "UNKNOWN", "cell_resolution": {"width": 16, "height": 32},
                "glyph_source_resolution": [{"width": 32, "height": 32}, {"width": 16, "height": 32}]}

    def test_label_mode_order_unknown_and_lossless_source_region(self):
        source = Image.new("RGB", (512, 100), (21, 42, 63))
        lines = label_lines(self.metadata(), "native", 2, 5, source.size)
        self.assertIn("FG=FLAT | BG=CONTINUOUS", lines[0])
        self.assertIn("frame 3/5", lines[0])
        self.assertIn("TREE=UNKNOWN", lines[2])
        self.assertIn("CELL=16x32 | GLYPHSRC=32x32+16x32", lines[3])
        result, band = label_frame(source, lines)
        self.assertIsNone(ImageChops.difference(source, result.crop((0, band, 512, band + 100))).getbbox())
        self.assertGreater(len(result.crop((0, 0, 512, band)).getcolors(100000)), 1)

    def test_all_gif_frames_labeled_and_source_hash_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = self.metadata()
            draft = {"role": "native", "frame_count": 5, "extension": ".gif",
                     "image_dimensions": {"width": 512, "height": 100}}
            source = root / deterministic_artifact_name(metadata, draft)
            frames = [Image.new("RGB", (512, 100), (i * 30, 0, 0)) for i in range(5)]
            frames[0].save(source, save_all=True, append_images=frames[1:], duration=60)
            original = sha256_file(source)
            manifest = root / "source.json"
            publish_artifacts(manifest, metadata, [{**draft, "path": source}], artifact_root=root)
            output = publish_labeled_media([manifest], root / "labeled", tags=False)
            self.assertEqual(validate_manifest(output, allow_incomplete=True), [])
            self.assertTrue(validate_manifest(output))  # Labeling cannot promote incomplete provenance.
            data = json.loads(output.read_text())
            image_path = output.parent / data["artifacts"][0]["path"]
            with Image.open(image_path) as image:
                self.assertEqual(image.n_frames, 5)
                self.assertEqual(image.width, 512)
                self.assertGreater(image.height, 100)
                bands = []
                for index in range(5):
                    image.seek(index)
                    band = image.convert("RGB").crop((0, 0, 512, image.height - 100))
                    self.assertGreater(len(band.getcolors(100000)), 1)
                    bands.append(band.tobytes())
                self.assertEqual(len(set(bands)), 5)
            receipt = json.loads((output.parent / "presentation_receipt.json").read_text())
            self.assertEqual(len(receipt["artifacts"][0]["labels"]), 5)
            self.assertEqual(sha256_file(source), original)

    def test_full_resolution_grid_keeps_per_pane_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifests = []
            for index, mode in enumerate(("FF", "FC", "CF", "CC")):
                metadata = self.metadata(mode)
                metadata["source_commit"] = str(index + 1) * 40
                draft = {"role": "native", "frame_count": 5, "extension": ".gif",
                         "image_dimensions": {"width": 512, "height": 100}}
                source = root / deterministic_artifact_name(metadata, draft)
                frames = [Image.new("RGB", (512, 100), (index * 40, frame * 30, 0)) for frame in range(5)]
                frames[0].save(source, save_all=True, append_images=frames[1:], duration=60)
                manifest = root / (mode + ".json")
                publish_artifacts(manifest, metadata, [{**draft, "path": source}], artifact_root=root)
                manifests.append(manifest)
            output = publish_labeled_media(manifests, root / "labeled", tags=False, grid=True)
            data = json.loads(output.read_text())
            grid = next(a for a in data["artifacts"] if a["role"] == "native-grid")
            self.assertEqual(grid["image_dimensions"]["width"], 1024)
            self.assertEqual(grid["frame_count"], 5)
            self.assertEqual(grid["capture_metadata"]["source_commit"], "MIXED")
            panes = grid["capture_metadata"]["panes"]
            self.assertEqual([p["pane_id"] for p in panes], ["FF", "FC", "CF", "CC"])
            self.assertEqual(len({p["source_commit"] for p in panes}), 4)
            self.assertEqual(validate_manifest(output, allow_incomplete=True), [])


if __name__ == "__main__":
    unittest.main()
