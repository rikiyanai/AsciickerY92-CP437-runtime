#!/usr/bin/env python3
"""Run machine-first open-vocabulary detection over a frozen route corpus.

The output contains image observations only.  A detection remains one bounding
box in one capture until a later tracking/localization stage proves that several
boxes belong to one physical object.  This prevents detector confidence from
silently becoming World IR position or identity truth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.capture_corpus import (
    capture_corpus_reference,
    load_capture_corpus,
)

SCHEMA = "y92.bulk_object_observations.v1"
VOCABULARY_SCHEMA = "y92.object_detection_vocabulary.v1"


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_inputs(capture_manifest: Path, vocabulary_path: Path):
    captures, capture_rows = load_capture_corpus(capture_manifest)
    vocabulary = json.loads(vocabulary_path.read_text(encoding="utf-8"))
    if vocabulary.get("schema") != VOCABULARY_SCHEMA:
        raise ValueError(f"vocabulary is not {VOCABULARY_SCHEMA}")
    classes = vocabulary.get("classes") or []
    if not classes:
        raise ValueError("object vocabulary has no classes")
    phrases: list[str] = []
    phrase_owner: dict[str, str] = {}
    for row in classes:
        semantic_class = str(row.get("semantic_class", "")).strip()
        if not semantic_class:
            raise ValueError("object vocabulary class lacks semantic_class")
        for phrase in row.get("prompts") or []:
            phrase = str(phrase).strip()
            if not phrase:
                continue
            if phrase in phrase_owner and phrase_owner[phrase] != semantic_class:
                raise ValueError(f"prompt {phrase!r} belongs to two semantic classes")
            phrase_owner[phrase] = semantic_class
            phrases.append(phrase)
    return captures, capture_rows, vocabulary, phrases, phrase_owner


def _semantic_candidates(text_label: str, phrase_owner: dict[str, str]) -> list[str]:
    label = text_label.casefold().strip()
    matches = {
        semantic
        for phrase, semantic in phrase_owner.items()
        if _contains_phrase(label, phrase.casefold())
        or _contains_phrase(phrase.casefold(), label)
    }
    return sorted(matches)


def _contains_phrase(text: str, phrase: str) -> bool:
    """Match detector phrases on word boundaries, including punctuation gaps.

    Grounding DINO can return joined prompt fragments.  A plain substring match
    classified ``street bollard`` as a tree because ``tree`` occurs inside
    ``street``.  Treat whitespace in a prompt as one or more non-word
    characters and require both ends to be token boundaries.
    """
    phrase = phrase.strip()
    if not phrase:
        return False
    words = [re.escape(word) for word in re.split(r"\W+", phrase) if word]
    if not words:
        return False
    pattern = r"(?<!\w)" + r"\W+".join(words) + r"(?!\w)"
    return re.search(pattern, text) is not None


def build_document(
    *,
    capture_manifest_path: Path,
    vocabulary_path: Path,
    model_id: str,
    model_revision: str,
    model_path: Path,
    box_threshold: float,
    text_threshold: float,
    device: str,
    selected_capture_ids: set[str] | None = None,
) -> dict[str, Any]:
    try:
        import torch
        import transformers
        from PIL import Image
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor
    except ImportError as exc:
        raise RuntimeError(
            "vision dependencies are absent; run the pinned FL-4813 vision setup"
        ) from exc

    captures, capture_rows, vocabulary, phrases, phrase_owner = _load_inputs(
        capture_manifest_path, vocabulary_path
    )
    if not 0.0 < box_threshold < 1.0 or not 0.0 < text_threshold < 1.0:
        raise ValueError("detection thresholds must lie between zero and one")
    if _sha256(model_path) != vocabulary["model"]["weights_sha256"]:
        raise ValueError("model weight digest disagrees with the frozen vocabulary")

    processor = AutoProcessor.from_pretrained(
        model_id, revision=model_revision, local_files_only=True
    )
    model = AutoModelForZeroShotObjectDetection.from_pretrained(
        model_id, revision=model_revision, local_files_only=True
    )
    model.to(device).eval()

    rows = [
        row for row in capture_rows
        if selected_capture_ids is None or row.get("capture_id") in selected_capture_ids
    ]
    observations: list[dict[str, Any]] = []
    per_capture: dict[str, int] = {}
    for capture in rows:
        capture_id = str(capture["capture_id"])
        local_path = (capture_manifest_path.parent / capture["local_path"]).resolve()
        if not local_path.is_file() or _sha256(local_path) != capture["sha256"]:
            raise ValueError(f"capture pixels missing or changed: {capture_id}")
        image = Image.open(local_path).convert("RGB")
        inputs = processor(images=image, text=[phrases], return_tensors="pt")
        inputs = {key: value.to(device) for key, value in inputs.items()}
        with torch.no_grad():
            outputs = model(**inputs)
        result = processor.post_process_grounded_object_detection(
            outputs,
            inputs["input_ids"],
            threshold=box_threshold,
            text_threshold=text_threshold,
            target_sizes=[image.size[::-1]],
        )[0]
        text_labels = result.get("text_labels", result.get("labels", []))
        count = 0
        for ordinal, (score, text_label, box) in enumerate(zip(
            result["scores"].detach().cpu().tolist(),
            text_labels,
            result["boxes"].detach().cpu().tolist(),
        )):
            x0, y0, x1, y1 = (float(value) for value in box)
            width, height = image.size
            observations.append({
                "observation_id": f"object-observation:{capture_id}:{ordinal:04d}",
                "capture_id": capture_id,
                "capture_sha256": capture["sha256"],
                "detector_text_label": str(text_label),
                "semantic_candidates": _semantic_candidates(str(text_label), phrase_owner),
                "detector_confidence": round(float(score), 8),
                "bbox_xyxy_px": [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)],
                "bbox_xyxy_normalized": [
                    round(x0 / width, 8), round(y0 / height, 8),
                    round(x1 / width, 8), round(y1 / height, 8),
                ],
                "image_size_px": [width, height],
                "evidence_state": "untracked_image_detection",
                "world_localization_state": "unresolved",
                "canonical_feature_id": None,
            })
            count += 1
        per_capture[capture_id] = count

    observations.sort(key=lambda row: row["observation_id"])
    return {
        "schema": SCHEMA,
        "capture_manifest": capture_corpus_reference(capture_manifest_path, captures),
        "vocabulary": {
            "path": str(vocabulary_path),
            "sha256": _sha256(vocabulary_path),
            "classes": len(vocabulary["classes"]),
            "prompts": len(phrases),
        },
        "model": {
            "id": model_id,
            "revision": model_revision,
            "weights_sha256": _sha256(model_path),
            "license": vocabulary["model"]["license"],
            "library": "transformers",
            "transformers_version": transformers.__version__,
            "torch_version": torch.__version__,
            "device": device,
            "python": platform.python_version(),
        },
        "parameters": {
            "box_threshold": box_threshold,
            "text_threshold": text_threshold,
        },
        "counts": {
            "captures_processed": len(rows),
            "observations": len(observations),
            "per_capture": per_capture,
            "world_localized": 0,
            "canonical_features": 0,
        },
        "observations": observations,
        "stage": "image_detection_only",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--capture-manifest", type=Path, required=True)
    parser.add_argument("--vocabulary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path, required=True,
                        help="local frozen model snapshot directory")
    parser.add_argument("--device", default="mps")
    parser.add_argument("--box-threshold", type=float, default=0.25)
    parser.add_argument("--text-threshold", type=float, default=0.20)
    parser.add_argument("--capture-id", action="append", default=[])
    args = parser.parse_args(argv)

    vocabulary = json.loads(args.vocabulary.read_text(encoding="utf-8"))
    model_record = vocabulary["model"]
    model_path = args.model_cache / "model.safetensors"
    document = build_document(
        capture_manifest_path=args.capture_manifest,
        vocabulary_path=args.vocabulary,
        model_id=model_record["id"],
        model_revision=model_record["revision"],
        model_path=model_path,
        box_threshold=args.box_threshold,
        text_threshold=args.text_threshold,
        device=args.device,
        selected_capture_ids=set(args.capture_id) or None,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(_canonical_bytes(document))
    print(
        f"[bulk-object-observations] captures={document['counts']['captures_processed']} "
        f"detections={document['counts']['observations']} "
        "world_localized=0 canonical_features=0"
    )
    print(f"[bulk-object-observations] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
