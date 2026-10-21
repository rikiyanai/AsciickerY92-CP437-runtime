#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from run_receipt_preservation import (  # noqa: E402
    DeferredRunReceiptPreserver,
    preserve_run_receipt,
)
import run_fl4359_headed_movement as headed_runner  # noqa: E402


class RunReceiptPreservationTests(unittest.TestCase):
    def test_preserves_receipt_and_structured_companions_content_addressed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / ".run" / "measured-run"
            run_dir.mkdir(parents=True)
            receipt = run_dir / "receipt.json"
            receipt.write_text('{"gpu_update_or_rebuild": 137.844}\n')
            (run_dir / "analysis_summary.json").write_text('{"pass": true}\n')
            (run_dir / "frame.png").write_bytes(b"replaceable visual")

            manifest_path = preserve_run_receipt(
                repo_root=root,
                receipt_path=receipt,
                fl_id="FL-4359",
                runner="scripts/test_runner.py",
            )

            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(manifest["schema"], "asciicker.run-receipt-preservation.v1")
            self.assertEqual(manifest["fl_id"], "FL-4359")
            self.assertEqual(
                {row["name"] for row in manifest["files"]},
                {"receipt.json", "analysis_summary.json"},
            )
            self.assertEqual((manifest_path.parent / "receipt.json").read_bytes(), receipt.read_bytes())
            self.assertFalse((manifest_path.parent / "frame.png").exists())

    def test_repeat_is_idempotent_and_changed_receipt_gets_new_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_dir = root / ".run" / "measured-run"
            run_dir.mkdir(parents=True)
            receipt = run_dir / "receipt.json"
            receipt.write_text('{"attempt": 1}\n')
            first = preserve_run_receipt(
                repo_root=root,
                receipt_path=receipt,
                fl_id="FL-4359",
                runner="runner.py",
            )
            self.assertEqual(
                first,
                preserve_run_receipt(
                    repo_root=root,
                    receipt_path=receipt,
                    fl_id="FL-4359",
                    runner="runner.py",
                ),
            )

            receipt.write_text('{"attempt": 2}\n')
            second = preserve_run_receipt(
                repo_root=root,
                receipt_path=receipt,
                fl_id="FL-4359",
                runner="runner.py",
            )
            self.assertNotEqual(first.parent, second.parent)

    def test_deferred_preserver_is_safe_before_receipt_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            deferred = DeferredRunReceiptPreserver(
                repo_root=root,
                fl_id="FL-4359",
                runner="runner.py",
            )
            deferred.configure(root / ".run" / "missing" / "receipt.json")
            self.assertIsNone(deferred.preserve())

    def test_headed_runner_fails_closed_when_preservation_fails(self) -> None:
        class FailingPreserver:
            def __init__(self, **_kwargs: object) -> None:
                pass

            def add_finalizer(self, _finalizer: object) -> None:
                pass

            def preserve(self) -> Path:
                raise RuntimeError("durable store unavailable")

        with (
            mock.patch.object(headed_runner, "DeferredRunReceiptPreserver", FailingPreserver),
            mock.patch.object(headed_runner, "_run", return_value=0),
        ):
            self.assertEqual(headed_runner.main(), 1)


if __name__ == "__main__":
    unittest.main()
