#!/usr/bin/env python3
"""Freeze a capture-review receipt and its verified crops as a portable snapshot."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != "y92.capture_review.v1":
        raise ValueError(f"unsupported capture-review schema: {document.get('schema')!r}")
    if not isinstance(document.get("annotations"), list):
        raise ValueError("capture-review receipt has no annotations array")
    return document


def _source_crop(source_receipt: Path, crop_path: str) -> Path:
    candidate = Path(crop_path)
    if not candidate.is_absolute():
        candidate = source_receipt.parent / candidate
    candidate = candidate.resolve()
    crop_root = (source_receipt.parent / "crops").resolve()
    if not candidate.is_relative_to(crop_root):
        raise ValueError(f"crop escapes source crop root: {candidate}")
    return candidate


def freeze_capture_review(
    source_receipt: Path,
    snapshot_dir: Path,
    *,
    frozen_utc: str,
) -> dict[str, Any]:
    source_receipt_ref = source_receipt.as_posix()
    source_receipt = source_receipt.resolve()
    snapshot_dir = snapshot_dir.resolve()
    source = _read(source_receipt)
    frozen = copy.deepcopy(source)
    crop_dir = snapshot_dir / "crops"
    crop_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    verified = 0
    seen_names: set[str] = set()
    for row in frozen["annotations"]:
        annotation_id = row.get("annotation_id")
        crop = row.get("crop") or {}
        if not isinstance(annotation_id, str) or not annotation_id:
            raise ValueError("annotation missing annotation_id")
        if not isinstance(crop.get("path"), str) or not isinstance(crop.get("sha256"), str):
            raise ValueError(f"annotation {annotation_id} has no verified crop record")
        source_crop = _source_crop(source_receipt, crop["path"])
        if not source_crop.is_file():
            raise ValueError(f"annotation {annotation_id} crop is missing: {source_crop}")
        digest = _sha256(source_crop)
        if digest != crop["sha256"]:
            raise ValueError(f"annotation {annotation_id} crop digest mismatch")
        name = source_crop.name
        if name in seen_names:
            raise ValueError(f"duplicate crop filename: {name}")
        seen_names.add(name)
        target = crop_dir / name
        if not target.is_file() or _sha256(target) != digest:
            temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            shutil.copyfile(source_crop, temporary)
            temporary.replace(target)
            copied += 1
        if _sha256(target) != digest:
            raise ValueError(f"snapshot crop verification failed: {target}")
        crop["path"] = f"crops/{name}"
        verified += 1

    frozen["snapshot"] = {
        "schema": "y92.capture_review_snapshot.v1",
        "frozen_utc": frozen_utc,
        "source_receipt_sha256": _sha256(source_receipt),
        "path_policy": "crop paths are relative to this snapshot directory",
        "annotation_count": len(frozen["annotations"]),
        "verified_crop_count": verified,
    }
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    annotations_path = snapshot_dir / "annotations.json"
    temporary = annotations_path.with_name(f".{annotations_path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(frozen, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(annotations_path)

    receipt = {
        "schema": "y92.capture_review_snapshot_receipt.v1",
        "frozen_utc": frozen_utc,
        "source_receipt": source_receipt_ref,
        "source_receipt_sha256": _sha256(source_receipt),
        "annotations_path": "annotations.json",
        "annotations_sha256": _sha256(annotations_path),
        "annotation_count": len(frozen["annotations"]),
        "verified_crop_count": verified,
        "copied_crop_count": copied,
        "missing_crop_count": 0,
        "digest_mismatch_count": 0,
        "passed": verified == len(frozen["annotations"]),
    }
    receipt_path = snapshot_dir / "snapshot_receipt.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--frozen-utc", required=True)
    args = parser.parse_args()
    receipt = freeze_capture_review(
        args.source, args.snapshot_dir, frozen_utc=args.frozen_utc
    )
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
