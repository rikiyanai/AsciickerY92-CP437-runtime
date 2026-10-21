#!/usr/bin/env python3
"""Run local Japanese OCR over machine-detected sign masks in bulk."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any

import numpy as np
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.bulk_mask_observations import decode_foreground_runs
from scripts.pipeline.capture_corpus import (
    capture_corpus_reference,
    load_capture_corpus,
)


SCHEMA = "y92.bulk_sign_ocr.v1"
DETECTION_SCHEMA = "y92.bulk_object_observations.v1"
MASK_SCHEMA = "y92.bulk_mask_observations.v1"
MODEL_SCHEMA = "y92.ocr_models.v1"
SIGN_CLASSES = {
    "station_identity_sign",
    "statutory_traffic_sign",
    "bicycle_parking_prohibited_notice",
    "storefront_sign",
    "notice_panel",
    "storefront_fascia",
}


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _ocr_tsv(
    image: Image.Image,
    *,
    tesseract_binary: Path,
    tessdata_dir: Path,
    language: str,
    page_segmentation_mode: int,
) -> dict[str, Any]:
    payload = io.BytesIO()
    image.save(payload, format="PNG")
    command = [
        str(tesseract_binary), "stdin", "stdout",
        "--tessdata-dir", str(tessdata_dir),
        "-l", language,
        "--psm", str(page_segmentation_mode),
        "--dpi", "300",
        "-c", "tessedit_create_tsv=1",
    ]
    result = subprocess.run(
        command, input=payload.getvalue(), stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"tesseract failed ({result.returncode}): "
            f"{result.stderr.decode(errors='replace').strip()}"
        )
    reader = csv.DictReader(
        result.stdout.decode("utf-8", errors="replace").splitlines(),
        delimiter="\t",
    )
    if not {"text", "conf", "left", "top", "width", "height"}.issubset(
        set(reader.fieldnames or [])
    ):
        raise RuntimeError("tesseract returned malformed TSV output: "
                           + result.stderr.decode(errors="replace").strip())
    words: list[dict[str, Any]] = []
    for row in reader:
        text = (row.get("text") or "").strip()
        try:
            confidence = float(row.get("conf") or -1)
        except ValueError:
            confidence = -1.0
        if text and confidence >= 0:
            words.append({
                "text": text,
                "confidence": round(confidence / 100.0, 6),
                "bbox_xywh_px": [
                    int(row["left"]), int(row["top"]),
                    int(row["width"]), int(row["height"]),
                ],
            })
    weighted = sum(row["confidence"] * len(row["text"]) for row in words)
    characters = sum(len(row["text"]) for row in words)
    return {
        "language": language,
        "page_segmentation_mode": page_segmentation_mode,
        "text": " ".join(row["text"] for row in words),
        "mean_confidence": round(weighted / characters, 6) if characters else 0.0,
        "words": words,
    }


def _masked_crop(
    image: Image.Image, mask_row: dict[str, Any], *, padding_fraction: float = 0.08
) -> tuple[Image.Image, list[int]]:
    width, height = mask_row["mask_size_px"]
    mask = decode_foreground_runs(
        mask_row["foreground_runs"], height=height, width=width
    )
    bbox = mask_row["mask_bbox_xyxy_px"]
    if bbox is None:
        raise ValueError("sign mask is empty")
    x0, y0, x1, y1 = bbox
    pad = round(max(x1 - x0, y1 - y0) * padding_fraction)
    crop_box = [max(0, x0 - pad), max(0, y0 - pad),
                min(width, x1 + pad), min(height, y1 + pad)]
    pixels = np.asarray(image.convert("RGB")).copy()
    pixels[~mask] = 255
    crop = Image.fromarray(pixels).crop(tuple(crop_box))
    # OCR is more stable on the retained low-resolution source pixels after a
    # deterministic scale and contrast pass.  This does not invent glyphs.
    scale = max(1, min(4, int(np.ceil(320 / max(1, min(crop.size))))))
    crop = crop.resize(
        (crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS
    )
    return ImageOps.autocontrast(ImageOps.grayscale(crop)), crop_box


def build_document(
    *,
    detections_path: Path,
    masks_path: Path,
    capture_manifest_path: Path,
    model_record_path: Path,
    tessdata_dir: Path,
    tesseract_binary: Path,
) -> dict[str, Any]:
    detections = json.loads(detections_path.read_text(encoding="utf-8"))
    masks = json.loads(masks_path.read_text(encoding="utf-8"))
    captures, capture_rows = load_capture_corpus(capture_manifest_path)
    models = json.loads(model_record_path.read_text(encoding="utf-8"))
    if detections.get("schema") != DETECTION_SCHEMA:
        raise ValueError(f"detections are not {DETECTION_SCHEMA}")
    if masks.get("schema") != MASK_SCHEMA:
        raise ValueError(f"masks are not {MASK_SCHEMA}")
    if models.get("schema") != MODEL_SCHEMA:
        raise ValueError(f"OCR model record is not {MODEL_SCHEMA}")
    for row in models["models"]:
        path = tessdata_dir / row["filename"]
        if not path.is_file() or _sha256(path) != row["sha256"]:
            raise ValueError(f"OCR model missing or changed: {row['filename']}")

    version = subprocess.run(
        [str(tesseract_binary), "--version"], capture_output=True,
        text=True, check=True,
    ).stdout.splitlines()[0]
    detections_by_id = {
        row["observation_id"]: row for row in detections["observations"]
    }
    captures_by_id = {row["capture_id"]: row for row in capture_rows}
    image_cache: dict[str, Image.Image] = {}
    rows: list[dict[str, Any]] = []
    for mask in masks["masks"]:
        detection = detections_by_id[mask["object_observation_id"]]
        if not SIGN_CLASSES.intersection(detection["semantic_candidates"]):
            continue
        capture_id = detection["capture_id"]
        if capture_id not in image_cache:
            capture = captures_by_id[capture_id]
            image_path = (capture_manifest_path.parent / capture["local_path"]).resolve()
            if not image_path.is_file() or _sha256(image_path) != capture["sha256"]:
                raise ValueError(f"capture pixels missing or changed: {capture_id}")
            image_cache[capture_id] = Image.open(image_path).convert("RGB")
        crop, crop_box = _masked_crop(image_cache[capture_id], mask)
        passes = [
            _ocr_tsv(
                crop, tesseract_binary=tesseract_binary,
                tessdata_dir=tessdata_dir, language="jpn",
                page_segmentation_mode=6,
            ),
            _ocr_tsv(
                crop, tesseract_binary=tesseract_binary,
                tessdata_dir=tessdata_dir, language="jpn_vert",
                page_segmentation_mode=5,
            ),
        ]
        rows.append({
            "ocr_observation_id": f"ocr:{detection['observation_id']}",
            "object_observation_id": detection["observation_id"],
            "mask_observation_id": mask["mask_observation_id"],
            "capture_id": capture_id,
            "semantic_candidates": detection["semantic_candidates"],
            "crop_bbox_xyxy_px": crop_box,
            "passes": passes,
            "evidence_state": "unreviewed_image_ocr",
            "text_identity_state": "unresolved",
            "canonical_feature_id": None,
        })
    rows.sort(key=lambda row: row["ocr_observation_id"])
    return {
        "schema": SCHEMA,
        "inputs": {
            "detections": {"path": str(detections_path), "sha256": _sha256(detections_path)},
            "masks": {"path": str(masks_path), "sha256": _sha256(masks_path)},
            "capture_manifest": capture_corpus_reference(
                capture_manifest_path, captures
            ),
        },
        "engine": {
            "binary": str(tesseract_binary),
            "version": version,
            "platform": platform.platform(),
        },
        "models": models,
        "counts": {
            "sign_candidates": len(rows),
            "ocr_passes": len(rows) * 2,
            "nonempty_passes": sum(
                bool(item["text"]) for row in rows for item in row["passes"]
            ),
            "reviewed_text_identities": 0,
            "canonical_features": 0,
        },
        "observations": rows,
        "stage": "unreviewed_image_ocr_only",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detections", type=Path, required=True)
    parser.add_argument("--masks", type=Path, required=True)
    parser.add_argument("--capture-manifest", type=Path, required=True)
    parser.add_argument("--model-record", type=Path, required=True)
    parser.add_argument("--tessdata-dir", type=Path, required=True)
    parser.add_argument("--tesseract-binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    document = build_document(
        detections_path=args.detections,
        masks_path=args.masks,
        capture_manifest_path=args.capture_manifest,
        model_record_path=args.model_record,
        tessdata_dir=args.tessdata_dir,
        tesseract_binary=args.tesseract_binary,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(_canonical_bytes(document))
    print(
        f"[bulk-sign-ocr] candidates={document['counts']['sign_candidates']} "
        f"passes={document['counts']['ocr_passes']} "
        f"nonempty={document['counts']['nonempty_passes']} reviewed=0 canonical=0"
    )
    print(f"[bulk-sign-ocr] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
