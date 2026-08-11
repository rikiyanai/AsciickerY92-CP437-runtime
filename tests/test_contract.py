from __future__ import annotations

import hashlib
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HistoricalRuntimeContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run([str(ROOT / "build.sh")], check=True)

    def test_clean_runtime_parses_full_snapshot(self) -> None:
        result = subprocess.run(
            [str(ROOT / "run-runtime.sh"), "--verify-corpus"],
            check=True,
            text=True,
            capture_output=True,
        )
        self.assertIn("115 normalized XP files / 573 raw layers", result.stdout)

    def test_once_screen_is_read_only(self) -> None:
        data = ROOT / "data"
        before = {path: path.stat().st_mtime_ns for path in data.rglob("*") if path.is_file()}
        result = subprocess.run(
            [str(ROOT / "run-runtime.sh"), "--once"],
            check=True,
            text=True,
            capture_output=True,
        )
        after = {path: path.stat().st_mtime_ns for path in data.rglob("*") if path.is_file()}
        self.assertEqual(before, after)
        self.assertIn("HISTORICAL ASCIICKER XP RUNTIME (READ-ONLY)", result.stdout)

    def test_snapshot_hashes_and_count(self) -> None:
        snapshot = json.loads((ROOT / "data/normalized-xp/SNAPSHOT.json").read_text())
        sprites = [row for row in snapshot["files"] if row["path"].startswith("sprites/")]
        self.assertEqual(len(sprites), 115)
        for row in snapshot["files"]:
            path = ROOT / "data/normalized-xp" / row["path"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), row["sha256"])

    def test_contract_hash_chain_is_internally_consistent(self) -> None:
        contract_root = ROOT / "data/normalized-xp/contracts/upstream_xp_cell_contract"
        manifest = contract_root / "manifest.json"
        freeze = contract_root / "family_contract_freeze.json"
        cutover = contract_root / "compiler_cutover.json"
        freeze_payload = json.loads(freeze.read_text())
        cutover_payload = json.loads(cutover.read_text())

        self.assertEqual(
            freeze_payload["source_hashes"]["cell_contract_manifest_sha256"],
            hashlib.sha256(manifest.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            cutover_payload["source_hashes"]["family_contract_freeze_sha256"],
            hashlib.sha256(freeze.read_bytes()).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
