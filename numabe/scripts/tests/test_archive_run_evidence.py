import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("archive_run_evidence", Path(__file__).parents[1] / "archive_run_evidence.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ArchiveTests(unittest.TestCase):
    def test_verified_round_trip_before_prune(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "capture.jsonl"
            content = b'{"source":42}\n' * 10000
            source.write_bytes(content)
            manifest = root / "manifest.jsonl"
            row = module.archive_file(source, Path(source.name), root / "archive", root / "proofs", manifest, True)
            self.assertFalse(source.exists())
            self.assertEqual(gzip.decompress(Path(row["archive"]).read_bytes()), content)
            self.assertEqual(json.loads(manifest.read_text())["sha256"], row["sha256"])

    def test_corrupt_existing_archive_preserves_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "capture.jsonl"
            source.write_bytes(b"original")
            archive = root / "archive"
            archive.mkdir()
            (archive / source.name).write_bytes(b"corrupt")
            with self.assertRaises(RuntimeError):
                module.archive_file(source, Path(source.name), archive, root / "proofs", root / "manifest.jsonl", True)
            self.assertEqual(source.read_bytes(), b"original")
            self.assertFalse((root / "manifest.jsonl").exists())

    def test_gif_extracted_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "five.gif"
            source.write_bytes(b"GIF89a")
            row = module.archive_file(source, Path(source.name), root / "archive", root / "proofs", root / "manifest.jsonl", True)
            self.assertEqual(Path(row["extracted_proof"]).read_bytes(), b"GIF89a")


if __name__ == "__main__":
    unittest.main()
