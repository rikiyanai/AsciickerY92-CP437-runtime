#!/usr/bin/env python3
"""Score locale-aware templates for unconfirmed machine track hypotheses."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.template_resolver import (
    discover_pack_chain,
    resolve_template_candidates,
    unresolved_source_fallback,
)


SCHEMA = "y92.bulk_template_candidates.v1"
TRACK_SCHEMA = "y92.bulk_object_tracks.v1"
OCR_SCHEMA = "y92.bulk_sign_ocr.v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def build_document(
    *,
    tracks_path: Path,
    ocr_path: Path,
    requirements_path: Path,
    template_root: Path,
    locale_context_id: str,
) -> dict[str, Any]:
    tracks = json.loads(tracks_path.read_text(encoding="utf-8"))
    ocr = json.loads(ocr_path.read_text(encoding="utf-8"))
    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    if tracks.get("schema") != TRACK_SCHEMA:
        raise ValueError(f"tracks are not {TRACK_SCHEMA}")
    if ocr.get("schema") != OCR_SCHEMA:
        raise ValueError(f"OCR observations are not {OCR_SCHEMA}")
    contexts = requirements.get("locale_contexts", {})
    if locale_context_id not in contexts:
        raise ValueError(f"unknown locale context: {locale_context_id}")
    context = contexts[locale_context_id]
    packs = discover_pack_chain(template_root, context)
    derived_chain = [pack["pack_id"] for pack in packs]
    declared_chain = context.get("template_pack_chain")
    if declared_chain is not None and declared_chain != derived_chain:
        raise ValueError(
            "declared locale pack chain disagrees with location-derived chain: "
            f"declared={declared_chain} derived={derived_chain}"
        )
    country_pack = next((
        pack for pack in packs
        if pack.get("jurisdiction", {}).get("country_code") == context.get("country_code")
        and pack.get("support_tier") == "full"
    ), None)

    ocr_by_detection: dict[str, list[str]] = {}
    for row in ocr["observations"]:
        texts = [item["text"] for item in row["passes"] if item["text"]]
        ocr_by_detection[row["object_observation_id"]] = texts

    rows = []
    for track in tracks["tracks"]:
        detection_ids = {track["seed_object_observation_id"]}
        detection_ids.update(
            frame["matched_object_observation_id"]
            for frame in track["frames"]
            if frame["matched_object_observation_id"] is not None
        )
        ocr_text = " ".join(
            text for detection_id in sorted(detection_ids)
            for text in ocr_by_detection.get(detection_id, [])
        )
        candidates = resolve_template_candidates(
            semantic_candidates=track["semantic_candidates"],
            context=context,
            packs=packs,
            ocr_text=ocr_text,
        )
        rows.append({
            "track_id": track["track_id"],
            "semantic_candidates": track["semantic_candidates"],
            "candidate_templates": candidates,
            "fallback": unresolved_source_fallback(track["semantic_candidates"]),
            "resolution_state": "candidate_only_unconfirmed_physical_track",
            "selected_template_id": None,
            "canonical_feature_id": None,
        })
    return {
        "schema": SCHEMA,
        "inputs": {
            "tracks": {"path": str(tracks_path), "sha256": _sha256(tracks_path)},
            "ocr": {"path": str(ocr_path), "sha256": _sha256(ocr_path)},
            "requirements": {
                "path": str(requirements_path), "sha256": _sha256(requirements_path)
            },
        },
        "locale_context_id": locale_context_id,
        "locale_context": context,
        "loaded_packs": derived_chain,
        "locale_resolution": {
            "method": "pack_metadata_parent_and_context_match",
            "country_support": (
                "full_first_country_pack"
                if country_pack is not None
                else "generic_source_derived_fallback"
            ),
            "country_pack": country_pack["pack_id"] if country_pack else None,
            "derived_pack_chain": derived_chain,
        },
        "counts": {
            "track_hypotheses": len(rows),
            "tracks_with_candidates": sum(bool(row["candidate_templates"]) for row in rows),
            "selected_templates": 0,
            "canonical_features": 0,
        },
        "tracks": rows,
        "stage": "template_candidates_only",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracks", type=Path, required=True)
    parser.add_argument("--ocr", type=Path, required=True)
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--template-root", type=Path, required=True)
    parser.add_argument("--locale-context", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    document = build_document(
        tracks_path=args.tracks,
        ocr_path=args.ocr,
        requirements_path=args.requirements,
        template_root=args.template_root,
        locale_context_id=args.locale_context,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(_canonical_bytes(document))
    print(
        f"[bulk-template-candidates] tracks={document['counts']['track_hypotheses']} "
        f"with_candidates={document['counts']['tracks_with_candidates']} "
        "selected=0 canonical=0"
    )
    print(f"[bulk-template-candidates] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
