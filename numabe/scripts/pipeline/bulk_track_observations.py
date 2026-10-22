#!/usr/bin/env python3
"""Track a machine-selected object set through one ordered capture sequence."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path
import platform
import sys
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.bulk_mask_observations import (
    _canonical_bytes,
    _mask_bbox,
    decode_foreground_runs,
    encode_foreground_runs,
)
from scripts.pipeline.capture_corpus import (
    capture_corpus_reference,
    load_capture_corpus,
)


SCHEMA = "y92.bulk_object_tracks.v1"
DETECTION_SCHEMA = "y92.bulk_object_observations.v1"
MASK_SCHEMA = "y92.bulk_mask_observations.v1"
MODEL_SCHEMA = "y92.segmentation_model.v1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bbox_iou(left: list[float], right: list[float]) -> float:
    x0 = max(left[0], right[0])
    y0 = max(left[1], right[1])
    x1 = min(left[2], right[2])
    y1 = min(left[3], right[3])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union else 0.0


def select_seed_detections(
    rows: list[dict[str, Any]], *, confidence_threshold: float, nms_iou: float
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    ordered = sorted(
        (row for row in rows if row["detector_confidence"] >= confidence_threshold
         and row["semantic_candidates"]),
        key=lambda row: (-row["detector_confidence"], row["observation_id"]),
    )
    for candidate in ordered:
        semantics = set(candidate["semantic_candidates"])
        duplicate = any(
            semantics.intersection(existing["semantic_candidates"])
            and bbox_iou(candidate["bbox_xyxy_px"], existing["bbox_xyxy_px"]) >= nms_iou
            for existing in selected
        )
        if not duplicate:
            selected.append(candidate)
    return selected


def _associate_frame(
    tracks: list[dict[str, Any]],
    detections: list[dict[str, Any]],
    *,
    minimum_iou: float,
) -> dict[str, str]:
    pairs: list[tuple[float, str, str]] = []
    for track in tracks:
        if track["mask_bbox_xyxy_px"] is None:
            continue
        semantics = set(track["semantic_candidates"])
        for detection in detections:
            if not semantics.intersection(detection["semantic_candidates"]):
                continue
            score = bbox_iou(track["mask_bbox_xyxy_px"], detection["bbox_xyxy_px"])
            if score >= minimum_iou:
                pairs.append((score, track["track_id"], detection["observation_id"]))
    result: dict[str, str] = {}
    used_detections: set[str] = set()
    for _, track_id, observation_id in sorted(
        pairs, key=lambda row: (-row[0], row[1], row[2])
    ):
        if track_id in result or observation_id in used_detections:
            continue
        result[track_id] = observation_id
        used_detections.add(observation_id)
    return result


def track_sequence(
    capture_ids, detections_by_capture, masks_by_detection, *,
    propagate, prompt, seed_confidence, nms_iou, association_iou,
):
    """Propagate existing hypotheses, then seed newly encountered detections.

    Callbacks keep model execution separate from identity bookkeeping. A track
    remains a hypothesis: spatial mask agreement alone is not physical proof.
    """
    tracks = []
    for frame_index, capture_id in enumerate(capture_ids):
        frame_records = []
        if tracks:
            predicted_masks = propagate(frame_index)
            if len(predicted_masks) != len(tracks):
                raise ValueError("tracking output count differs from active object count")
            for track, mask in zip(tracks, predicted_masks):
                height, width = mask.shape
                frame_records.append({
                    "track_id": track["track_id"], "capture_id": capture_id,
                    "semantic_candidates": track["semantic_candidates"],
                    "mask_size_px": [width, height],
                    "mask_area_px": int(mask.sum()),
                    "mask_bbox_xyxy_px": _mask_bbox(mask),
                    "mask_encoding": "foreground_runs_row_major_v1",
                    "foreground_runs": encode_foreground_runs(mask),
                })
        candidates = select_seed_detections(
            detections_by_capture[capture_id],
            confidence_threshold=seed_confidence, nms_iou=nms_iou,
        )
        matches = _associate_frame(
            frame_records, candidates, minimum_iou=association_iou,
        )
        for track, record in zip(tracks, frame_records):
            record["matched_object_observation_id"] = matches.get(track["track_id"])
            track["frames"].append(record)
        matched = set(matches.values())
        new_masks, new_ids = [], []
        for seed in candidates:
            if seed["observation_id"] in matched:
                continue
            row = masks_by_detection.get(seed["observation_id"])
            if row is None:
                raise ValueError(f"seed detection lacks a mask: {seed['observation_id']}")
            width, height = row["mask_size_px"]
            mask = decode_foreground_runs(
                row["foreground_runs"], height=height, width=width,
            )
            if not mask.any():
                continue
            object_id = len(tracks) + 1
            track_id = f"machine-track:{capture_id}:{object_id:04d}"
            tracks.append({
                "track_id": track_id,
                "seed_object_observation_id": seed["observation_id"],
                "semantic_candidates": seed["semantic_candidates"],
                "frames": [{
                    "track_id": track_id, "capture_id": capture_id,
                    "semantic_candidates": seed["semantic_candidates"],
                    "mask_size_px": [width, height],
                    "mask_area_px": int(mask.sum()),
                    "mask_bbox_xyxy_px": _mask_bbox(mask),
                    "mask_encoding": "foreground_runs_row_major_v1",
                    "foreground_runs": encode_foreground_runs(mask),
                    "matched_object_observation_id": seed["observation_id"],
                }],
                "track_state": "machine_hypothesis",
                "physical_identity_state": "unresolved",
                "world_localization_state": "unresolved",
                "canonical_feature_id": None,
            })
            new_ids.append(object_id)
            new_masks.append(mask)
        if new_ids:
            prompt(frame_index, new_ids, new_masks)
            # Materialize all new prompts before advancing the video session.
            propagate(frame_index)
    return tracks


def sequence_windows(capture_ids, *, window_frames, overlap_frames):
    if window_frames < 2 or not 1 <= overlap_frames < window_frames:
        raise ValueError("require 1 <= overlap_frames < window_frames")
    start = 0
    while start < len(capture_ids):
        end = min(start + window_frames, len(capture_ids))
        yield start, capture_ids[start:end]
        if end == len(capture_ids):
            break
        start = end - overlap_frames


def merge_track_windows(windows, capture_ids):
    """FL-4813: join only unambiguous shared-detection hypotheses.

    The overlapping model sessions remain independently inspectable. Ambiguous
    many-to-one or one-to-many links are retained for review, never collapsed.
    """
    import copy
    tracks, conflicts = [], []
    order = {capture_id: index for index, capture_id in enumerate(capture_ids)}
    for window_index, incoming in enumerate(windows):
        def evidence(track):
            return {row["matched_object_observation_id"] for row in track["frames"]
                    if row.get("matched_object_observation_id")}
        old_evidence = [evidence(track) for track in tracks]
        links = [{i for i, previous in enumerate(old_evidence)
                  if previous.intersection(evidence(track))} for track in incoming]
        uses = {i: sum(i in candidates for candidates in links)
                for candidates in links for i in candidates}
        for local, candidates in zip(incoming, links):
            track = copy.deepcopy(local)
            local_id = f"window:{window_index}:{track['track_id']}"
            if len(candidates) == 1 and uses[next(iter(candidates))] == 1:
                target = tracks[next(iter(candidates))]
                frames = {row["capture_id"]: row for row in target["frames"]}
                for row in track["frames"]:
                    row["track_id"] = target["track_id"]
                    old = frames.get(row["capture_id"])
                    if old is None or (not old.get("matched_object_observation_id")
                                       and row.get("matched_object_observation_id")):
                        frames[row["capture_id"]] = row
                target["frames"] = sorted(frames.values(), key=lambda row: order[row["capture_id"]])
                target["window_track_ids"].append(local_id)
            else:
                if candidates:
                    conflicts.append({"window_track_id": local_id,
                                      "candidate_track_ids": [tracks[i]["track_id"] for i in sorted(candidates)],
                                      "state": "unresolved_overlap_identity"})
                track["track_id"] = local_id
                track["window_track_ids"] = [local_id]
                for row in track["frames"]:
                    row["track_id"] = local_id
                tracks.append(track)
    return tracks, conflicts


def build_document(
    *,
    detections_path: Path,
    masks_path: Path,
    capture_manifest_path: Path,
    model_record_path: Path,
    model_cache: Path,
    sequence_id: str,
    device: str,
    seed_confidence: float,
    nms_iou: float,
    association_iou: float,
    window_frames: int = 8,
    overlap_frames: int = 2,
) -> dict[str, Any]:
    try:
        import torch
        import transformers
        from PIL import Image
        from transformers import Sam2VideoModel, Sam2VideoProcessor
    except ImportError as exc:
        raise RuntimeError(
            "vision dependencies are absent; run the pinned FL-4813 vision setup"
        ) from exc

    detections = json.loads(detections_path.read_text(encoding="utf-8"))
    masks = json.loads(masks_path.read_text(encoding="utf-8"))
    captures, capture_rows = load_capture_corpus(capture_manifest_path)
    model_record = json.loads(model_record_path.read_text(encoding="utf-8"))
    if detections.get("schema") != DETECTION_SCHEMA:
        raise ValueError(f"detections are not {DETECTION_SCHEMA}")
    if masks.get("schema") != MASK_SCHEMA:
        raise ValueError(f"masks are not {MASK_SCHEMA}")
    if model_record.get("schema") != MODEL_SCHEMA:
        raise ValueError(f"model record is not {MODEL_SCHEMA}")
    model_path = model_cache / "model.safetensors"
    if _sha256(model_path) != model_record["weights_sha256"]:
        raise ValueError("tracking model digest disagrees with model record")

    sequence = sorted(
        (row for row in capture_rows if row.get("sequence_id") == sequence_id),
        key=lambda row: (int(row.get("sequence_index", 0)), row["capture_id"]),
    )
    if len(sequence) < 2:
        raise ValueError("tracking requires at least two selected sequence frames")
    capture_ids = [str(row["capture_id"]) for row in sequence]
    detections_by_capture = {
        capture_id: [
            row for row in detections["observations"]
            if row["capture_id"] == capture_id
        ]
        for capture_id in capture_ids
    }
    masks_by_detection = {
        row["object_observation_id"]: row for row in masks["masks"]
    }

    images = []
    for capture in sequence:
        path = (capture_manifest_path.parent / capture["local_path"]).resolve()
        if not path.is_file() or _sha256(path) != capture["sha256"]:
            raise ValueError(f"capture pixels missing or changed: {capture['capture_id']}")
        images.append(Image.open(path).convert("RGB"))

    processor = Sam2VideoProcessor.from_pretrained(model_cache, local_files_only=True)
    model = Sam2VideoModel.from_pretrained(model_cache, local_files_only=True)
    model.to(device).eval()
    windows = []
    for start, window_ids in sequence_windows(
        capture_ids, window_frames=window_frames, overlap_frames=overlap_frames,
    ):
        session = processor.init_video_session(
            video=images[start:start + len(window_ids)], inference_device=device, dtype=torch.float32,
            processing_device="cpu", video_storage_device="cpu",
            inference_state_device="cpu",
        )
        def propagate(frame_index):
            with torch.inference_mode():
                output = model(inference_session=session, frame_idx=frame_index)
            masks = processor.post_process_masks(
                [output.pred_masks.detach().cpu()],
                original_sizes=[[session.video_height, session.video_width]],
            )[0][:, 0].numpy()
            del output
            if device == "mps":
                torch.mps.empty_cache()
            return masks

        def prompt(frame_index, object_ids, seed_masks):
            processor.add_inputs_to_inference_session(
                inference_session=session, frame_idx=frame_index,
                obj_ids=object_ids, input_masks=seed_masks,
            )

        tracks = track_sequence(
            window_ids, detections_by_capture, masks_by_detection,
            propagate=propagate, prompt=prompt,
            seed_confidence=seed_confidence, nms_iou=nms_iou,
            association_iou=association_iou,
        )
        windows.append(tracks)
        del session
        gc.collect()
        if device == "mps":
            torch.mps.empty_cache()
        print(f"[bulk-track-observations] window={len(windows)} "
              f"frames={start}:{start + len(window_ids)} tracks={len(tracks)}", flush=True)
    tracks, overlap_conflicts = merge_track_windows(windows, capture_ids)

    for track in tracks:
        track["counts"] = {
            "frames": len(track["frames"]),
            "nonempty_masks": sum(row["mask_area_px"] > 0 for row in track["frames"]),
            "matched_detections": sum(
                row["matched_object_observation_id"] is not None
                for row in track["frames"]
            ),
        }

    return {
        "schema": SCHEMA,
        "inputs": {
            "detections": {"path": str(detections_path), "sha256": _sha256(detections_path)},
            "masks": {"path": str(masks_path), "sha256": _sha256(masks_path)},
            "capture_manifest": capture_corpus_reference(
                capture_manifest_path, captures
            ),
        },
        "sequence": {
            "sequence_id": sequence_id,
            "capture_ids": capture_ids,
            "seed_policy": "unmatched_detections_each_frame",
        },
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
        "parameters": {
            "seed_confidence": seed_confidence,
            "seed_nms_iou": nms_iou,
            "detection_association_bbox_iou": association_iou,
            "window_frames": window_frames,
            "overlap_frames": overlap_frames,
            "processing_device": "cpu",
            "video_storage_device": "cpu",
            "inference_state_device": "cpu",
        },
        "counts": {
            "frames": len(sequence),
            "seed_detections": len(tracks),
            "track_hypotheses": len(tracks),
            "track_frame_masks": sum(len(track["frames"]) for track in tracks),
            "matched_detections": sum(
                track["counts"]["matched_detections"] for track in tracks
            ),
            "physical_objects_confirmed": 0,
            "world_localized": 0,
            "canonical_features": 0,
        },
        "overlap_identity_conflicts": overlap_conflicts,
        "tracks": tracks,
        "stage": "machine_track_hypotheses_only",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detections", type=Path, required=True)
    parser.add_argument("--masks", type=Path, required=True)
    parser.add_argument("--capture-manifest", type=Path, required=True)
    parser.add_argument("--model-record", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, required=True)
    parser.add_argument("--sequence-id", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--seed-confidence", type=float, default=0.30)
    parser.add_argument("--nms-iou", type=float, default=0.60)
    parser.add_argument("--association-iou", type=float, default=0.15)
    parser.add_argument("--window-frames", type=int, default=8)
    parser.add_argument("--overlap-frames", type=int, default=2)
    args = parser.parse_args(argv)
    document = build_document(
        detections_path=args.detections,
        masks_path=args.masks,
        capture_manifest_path=args.capture_manifest,
        model_record_path=args.model_record,
        model_cache=args.model_cache,
        sequence_id=args.sequence_id,
        device=args.device,
        seed_confidence=args.seed_confidence,
        nms_iou=args.nms_iou,
        association_iou=args.association_iou,
        window_frames=args.window_frames, overlap_frames=args.overlap_frames,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(_canonical_bytes(document))
    print(
        f"[bulk-track-observations] frames={document['counts']['frames']} "
        f"tracks={document['counts']['track_hypotheses']} "
        f"matches={document['counts']['matched_detections']} "
        "physical=0 world_localized=0 canonical=0"
    )
    print(f"[bulk-track-observations] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
