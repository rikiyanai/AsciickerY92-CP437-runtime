#!/usr/bin/env python3
"""Segment frozen image detections without promoting them to world objects."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
from typing import Any
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.capture_corpus import (
    capture_corpus_reference,
    load_capture_corpus,
)


SCHEMA = "y92.bulk_mask_observations.v1"
DETECTION_SCHEMA = "y92.bulk_object_observations.v1"
MODEL_SCHEMA = "y92.segmentation_model.v1"


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def encode_foreground_runs(mask: np.ndarray) -> list[list[int]]:
    """Encode a 2-D boolean mask as [flat_start, length] foreground runs."""
    if mask.ndim != 2:
        raise ValueError("mask must have exactly two dimensions")
    values = mask.astype(np.int8, copy=False).reshape(-1)
    transitions = np.diff(np.pad(values, (1, 1)))
    starts = np.flatnonzero(transitions == 1)
    ends = np.flatnonzero(transitions == -1)
    return [[int(start), int(end - start)] for start, end in zip(starts, ends)]


def decode_foreground_runs(
    runs: list[list[int]], *, height: int, width: int
) -> np.ndarray:
    size = height * width
    flat = np.zeros(size, dtype=bool)
    previous_end = 0
    for start, length in runs:
        if start < previous_end or length <= 0 or start + length > size:
            raise ValueError("invalid or overlapping foreground run")
        flat[start:start + length] = True
        previous_end = start + length
    return flat.reshape(height, width)


def _mask_bbox(mask: np.ndarray) -> list[int] | None:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return [int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)]


def masked_source_palette(rgb: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
    """FL-4813: retain robust image appearance, never claim measured albedo.

    Luminance trimming suppresses extremes; it does not prove that shadows,
    reflections or occluders have been removed. Multi-view facade fusion and
    review still own that decision.
    """
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.shape[:2] != mask.shape:
        raise ValueError("RGB image and region mask dimensions disagree")
    pixels = rgb[mask.astype(bool)].astype(np.float64)
    if not len(pixels):
        return {"state": "empty_region", "masked_pixels": 0, "sampled_pixels": 0,
                "median_rgb": None}
    luminance = pixels @ np.array([0.2126, 0.7152, 0.0722])
    low, high = np.percentile(luminance, [15, 85])
    samples = pixels[(luminance >= low) & (luminance <= high)]
    return {
        "state": "source_image_appearance_only",
        "method": "masked_luminance_p15_p85_channel_median",
        "masked_pixels": len(pixels), "sampled_pixels": len(samples),
        "median_rgb": np.rint(np.median(samples, axis=0)).astype(int).tolist(),
        "channel_p10_rgb": np.rint(np.percentile(samples, 10, axis=0)).astype(int).tolist(),
        "channel_p90_rgb": np.rint(np.percentile(samples, 90, axis=0)).astype(int).tolist(),
        "illumination_corrected": False,
        "occlusion_reviewed": False,
    }


def build_document(
    *,
    detections_path: Path,
    capture_manifest_path: Path,
    model_record_path: Path,
    model_cache: Path,
    device: str,
) -> dict[str, Any]:
    try:
        import torch
        import transformers
        from PIL import Image
        from transformers import Sam2Model, Sam2Processor
    except ImportError as exc:
        raise RuntimeError(
            "vision dependencies are absent; run the pinned FL-4813 vision setup"
        ) from exc

    detections = json.loads(detections_path.read_text(encoding="utf-8"))
    captures, capture_rows = load_capture_corpus(capture_manifest_path)
    model_record = json.loads(model_record_path.read_text(encoding="utf-8"))
    if detections.get("schema") != DETECTION_SCHEMA:
        raise ValueError(f"detections are not {DETECTION_SCHEMA}")
    if model_record.get("schema") != MODEL_SCHEMA:
        raise ValueError(f"model record is not {MODEL_SCHEMA}")
    model_path = model_cache / "model.safetensors"
    if _sha256(model_path) != model_record["weights_sha256"]:
        raise ValueError("segmentation model digest disagrees with model record")

    captures_by_id = {str(row["capture_id"]): row for row in capture_rows}
    detections_by_capture: dict[str, list[dict[str, Any]]] = {}
    for row in detections["observations"]:
        detections_by_capture.setdefault(str(row["capture_id"]), []).append(row)

    processor = Sam2Processor.from_pretrained(model_cache, local_files_only=True)
    model = Sam2Model.from_pretrained(model_cache, local_files_only=True)
    model.to(device).eval()

    mask_rows: list[dict[str, Any]] = []
    per_capture: dict[str, int] = {}
    for capture_id in sorted(detections_by_capture):
        capture = captures_by_id.get(capture_id)
        if capture is None:
            raise ValueError(f"detection refers to unknown capture: {capture_id}")
        image_path = (capture_manifest_path.parent / capture["local_path"]).resolve()
        if not image_path.is_file() or _sha256(image_path) != capture["sha256"]:
            raise ValueError(f"capture pixels missing or changed: {capture_id}")
        image = Image.open(image_path).convert("RGB")
        rows = sorted(
            detections_by_capture[capture_id], key=lambda row: row["observation_id"]
        )
        boxes = [row["bbox_xyxy_px"] for row in rows]
        inputs = processor(images=image, input_boxes=[boxes], return_tensors="pt")
        inputs = inputs.to(device)
        with torch.inference_mode():
            output = model(**inputs, multimask_output=False)
        masks = processor.post_process_masks(
            output.pred_masks.detach().cpu(), inputs["original_sizes"].detach().cpu()
        )[0][:, 0].numpy()
        scores = output.iou_scores.detach().cpu()[0, :, 0].tolist()
        if len(masks) != len(rows):
            raise RuntimeError("segmentation output count disagrees with input boxes")

        image_rgb = np.asarray(image)
        for detection, mask, score in zip(rows, masks, scores):
            height, width = mask.shape
            runs = encode_foreground_runs(mask)
            decoded = decode_foreground_runs(runs, height=height, width=width)
            if not np.array_equal(mask, decoded):
                raise RuntimeError("mask run encoding failed round-trip validation")
            mask_rows.append({
                "mask_observation_id": f"mask:{detection['observation_id']}",
                "object_observation_id": detection["observation_id"],
                "capture_id": capture_id,
                "semantic_candidates": detection["semantic_candidates"],
                "segmentation_score": round(float(score), 8),
                "mask_size_px": [width, height],
                "mask_area_px": int(mask.sum()),
                "mask_bbox_xyxy_px": _mask_bbox(mask),
                "mask_encoding": "foreground_runs_row_major_v1",
                "foreground_runs": runs,
                "capture_sha256": capture["sha256"],
                "source_palette": masked_source_palette(image_rgb, mask),
                "evidence_state": "untracked_image_mask",
                "world_localization_state": "unresolved",
                "canonical_feature_id": None,
            })
        per_capture[capture_id] = len(rows)

    mask_rows.sort(key=lambda row: row["mask_observation_id"])
    return {
        "schema": SCHEMA,
        "detections": {"path": str(detections_path), "sha256": _sha256(detections_path)},
        "capture_manifest": capture_corpus_reference(capture_manifest_path, captures),
        "model": {
            **{key: model_record[key] for key in (
                "id", "revision", "weights_sha256", "license", "source_uri"
            )},
            "library": "transformers",
            "transformers_version": transformers.__version__,
            "torch_version": torch.__version__,
            "device": device,
            "python": platform.python_version(),
        },
        "counts": {
            "captures_processed": len(detections_by_capture),
            "input_detections": len(detections["observations"]),
            "masks": len(mask_rows),
            "nonempty_masks": sum(row["mask_area_px"] > 0 for row in mask_rows),
            "per_capture": per_capture,
            "tracked_objects": 0,
            "world_localized": 0,
            "canonical_features": 0,
        },
        "masks": mask_rows,
        "stage": "image_segmentation_only",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detections", type=Path, required=True)
    parser.add_argument("--capture-manifest", type=Path, required=True)
    parser.add_argument("--model-record", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--device", default="mps")
    args = parser.parse_args(argv)

    document = build_document(
        detections_path=args.detections,
        capture_manifest_path=args.capture_manifest,
        model_record_path=args.model_record,
        model_cache=args.model_cache,
        device=args.device,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(_canonical_bytes(document))
    print(
        f"[bulk-mask-observations] captures={document['counts']['captures_processed']} "
        f"masks={document['counts']['masks']} "
        f"nonempty={document['counts']['nonempty_masks']} tracked=0 world_localized=0"
    )
    print(f"[bulk-mask-observations] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
