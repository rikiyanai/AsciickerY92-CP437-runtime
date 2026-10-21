"""Normalize frozen route and calibrated-view manifests for vision stages."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROUTE_CAPTURE_SCHEMA = "y92.route_capture_manifest.v1"
CAPTURE_SOURCE_SCHEMA = "y92.capture_source_index.v1"
SUPPORTED_CAPTURE_SCHEMAS = frozenset((ROUTE_CAPTURE_SCHEMA, CAPTURE_SOURCE_SCHEMA))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_capture_corpus(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return the capture document and the frames eligible for machine vision.

    A route manifest contains accepted and rejected acquisition records, so its
    explicit ``selected_keyframe`` decision remains authoritative. A calibrated
    capture-source index is already a derived working corpus; every row in that
    index is eligible. This lets FL-4813 consume rectilinear views without
    pretending their source panoramas were pinhole cameras.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    schema = document.get("schema")
    if schema not in SUPPORTED_CAPTURE_SCHEMAS:
        raise ValueError(
            f"capture corpus schema {schema!r} is unsupported; expected one of "
            f"{sorted(SUPPORTED_CAPTURE_SCHEMAS)}"
        )
    rows = list(document.get("captures") or [])
    if schema == ROUTE_CAPTURE_SCHEMA:
        rows = [row for row in rows if row.get("selected_keyframe") is True]
    seen: set[str] = set()
    for row in rows:
        capture_id = str(row.get("capture_id") or "")
        if not capture_id:
            raise ValueError("capture corpus row lacks capture_id")
        if capture_id in seen:
            raise ValueError(f"duplicate capture_id in capture corpus: {capture_id}")
        seen.add(capture_id)
        for key in ("local_path", "sha256"):
            if not row.get(key):
                raise ValueError(f"capture {capture_id} lacks {key}")
    return document, rows


def capture_corpus_reference(path: Path, document: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "schema": document["schema"],
    }
