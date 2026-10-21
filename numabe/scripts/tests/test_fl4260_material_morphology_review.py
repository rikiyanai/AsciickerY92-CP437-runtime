#!/usr/bin/env python3
"""Cover the FL-4260 morphology review refresh path.

The review refresh must never shrink candidate pools; receipts are evidence,
not a second runtime owner.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TABLES = REPO_ROOT / "assets/glyphs/generated/material.morphology.v2.profile_tables.json"
REVIEWS = REPO_ROOT / "assets/glyphs/generated/material.morphology.v2.manual_review_receipts.jsonl"
REVIEW = REPO_ROOT / "scripts/fl4260_material_morphology_review.py"


def _run_review(args: list[str]) -> dict:
    proc = subprocess.run(
        [sys.executable, str(REVIEW), *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)


def test_empty_receipts_keep_review_pending() -> None:
    old = REVIEWS.read_text(encoding="utf-8") if REVIEWS.exists() else ""
    try:
        REVIEWS.write_text("", encoding="utf-8")
        summary = _run_review(["--dry-run"])
    finally:
        REVIEWS.write_text(old, encoding="utf-8")
    assert summary["runtime_profile_live"] is True
    assert summary["review_ready"] is False
    assert summary["accepted_cells"] == 0
    assert summary["pending_cells"] == summary["total_cells"]
    assert summary["accepted_glyph_count"] == 0


def test_single_accept_marks_matching_primary_cells_without_shrinking_candidates() -> None:
    tables = json.loads(TABLES.read_text(encoding="utf-8"))
    cell = tables["profiles"]["WATER"]["N"]["D0"]
    primary = cell["primary_glyph_ids"][0]
    before_candidates = list(cell["candidate_glyph_ids"])
    old = REVIEWS.read_text(encoding="utf-8") if REVIEWS.exists() else ""
    try:
        REVIEWS.write_text(
            json.dumps(
                {
                    "glyph_id": int(primary),
                    "action": "accept",
                    "reason": "test single-accept smoke",
                    "reviewer_timestamp": "2026-06-07T20:00:00",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        summary = _run_review(["--dry-run"])
        assert summary["accepted_glyph_count"] == 1
        assert summary["accepted_cells"] > 0
        assert summary["pending_cells"] == summary["total_cells"] - summary["accepted_cells"]
        assert summary["runtime_profile_live"] is True
        after = json.loads(TABLES.read_text(encoding="utf-8"))["profiles"]["WATER"]["N"]["D0"]["candidate_glyph_ids"]
        assert after == before_candidates
    finally:
        REVIEWS.write_text(old, encoding="utf-8")


def test_reject_marks_blocker_without_stripping_candidate_glyph() -> None:
    tables = json.loads(TABLES.read_text(encoding="utf-8"))
    cell = tables["profiles"]["WATER"]["N"]["D0"]
    primary = cell["primary_glyph_ids"][0]
    before_candidates = list(cell["candidate_glyph_ids"])
    old = REVIEWS.read_text(encoding="utf-8") if REVIEWS.exists() else ""
    try:
        REVIEWS.write_text(
            json.dumps(
                {
                    "glyph_id": int(primary),
                    "action": "reject",
                    "reason": "test reject blocker",
                    "reviewer_timestamp": "2026-06-07T20:00:00",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        summary = _run_review(["--dry-run"])
        assert summary["rejected_glyph_count"] == 1
        assert summary["rejected_cells"] > 0
        assert summary["accepted_cells"] == 0
        assert summary["runtime_profile_live"] is True
        after = json.loads(TABLES.read_text(encoding="utf-8"))["profiles"]["WATER"]["N"]["D0"]["candidate_glyph_ids"]
        assert after == before_candidates
    finally:
        REVIEWS.write_text(old, encoding="utf-8")
