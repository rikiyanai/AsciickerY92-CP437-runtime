import hashlib
import json
from pathlib import Path

import pytest

from scripts.pipeline.freeze_capture_review import freeze_capture_review


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    source_dir = tmp_path / "live"
    crop_dir = source_dir / "crops"
    crop_dir.mkdir(parents=True)
    crop = crop_dir / "ann-1.png"
    crop.write_bytes(b"portable crop")
    digest = hashlib.sha256(crop.read_bytes()).hexdigest()
    receipt = source_dir / "annotations.json"
    receipt.write_text(json.dumps({
        "schema": "y92.capture_review.v1",
        "annotations": [{
            "annotation_id": "ann-1",
            "crop": {"path": str(crop), "sha256": digest},
        }],
    }))
    return receipt, crop


def test_freeze_rewrites_crop_path_and_verifies_portable_snapshot(tmp_path):
    source, _ = _fixture(tmp_path)
    destination = tmp_path / "snapshot"
    receipt = freeze_capture_review(source, destination, frozen_utc="2026-09-15T00:00:00Z")

    frozen = json.loads((destination / "annotations.json").read_text())
    assert frozen["annotations"][0]["crop"]["path"] == "crops/ann-1.png"
    assert (destination / "crops/ann-1.png").read_bytes() == b"portable crop"
    assert receipt["passed"] is True
    assert receipt["verified_crop_count"] == 1


def test_freeze_refuses_a_tampered_source_crop(tmp_path):
    source, crop = _fixture(tmp_path)
    crop.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="crop digest mismatch"):
        freeze_capture_review(source, tmp_path / "snapshot", frozen_utc="2026-09-15T00:00:00Z")
