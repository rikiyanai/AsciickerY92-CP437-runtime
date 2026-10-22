"""FL-4831: exhaustive reference-accounting gate, NOT a visual-quality judge.

This checks that every archived art piece has a source-pinned qualitative
record. It cannot establish whether the judgments are good or whether a 3D
car resembles the art. Root must inspect those judgments and the real FG-only
viewport separately. Pending analysis intentionally fails the completeness
gate; inventory is not promoted to analysis.
"""
import copy
import hashlib
import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
OWNER = ROOT / "assets/meshes/incoming/tutorial_cars.source.json"
FIELDS = {
    "id", "text_sha256", "png_sha256", "depicted_subject", "viewpoint",
    "proportions_and_gesture", "structural_strokes", "protected_negative_shapes",
    "occlusion_and_contacts", "detail_and_tone", "retain", "do_not_transfer",
    "three_d_inference_limits", "vehicle_family", "related_piece_ids",
    "review_basis", "status",
}


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        assert key not in result, ("duplicate source-owner key", key)
        result[key] = value
    return result


def assert_complete_reviews(analysis, expected_ids):
    assigned = []
    reviews = []
    for batch in analysis["batches"].values():
        assigned.extend(batch["assigned_ids"])
        rows = batch["reviews"]
        assert {r["id"] for r in rows} == set(batch["assigned_ids"]), (
            "missing or foreign piece reviews", batch["assigned_ids"], [r["id"] for r in rows])
        reviews.extend(rows)
    assert len(assigned) == len(set(assigned)) == len(expected_ids)
    assert set(assigned) == set(expected_ids)
    assert len(reviews) == len(expected_ids)
    assert len({r["id"] for r in reviews}) == len(reviews)
    for row in reviews:
        assert FIELDS <= row.keys(), (row["id"], FIELDS - row.keys())
        for field in FIELDS - {"related_piece_ids"}:
            assert isinstance(row[field], str) and row[field].strip(), (
                row["id"], "empty or non-text qualitative field", field)
            assert not row[field].rstrip().endswith(("…", "...")), (
                row["id"], "truncated qualitative judgment", field)
        assert isinstance(row["related_piece_ids"], list)
        assert all(piece in expected_ids and piece != row["id"]
                   for piece in row["related_piece_ids"]), row["id"]
        assert "pending" not in str(row["status"]).lower(), row["id"]
        for field in ("text_sha256", "png_sha256"):
            assert len(row[field]) == 64 and all(c in "0123456789abcdef" for c in row[field])
    return reviews


def _fixture():
    review = {field: "piece-specific fixture observation" for field in FIELDS}
    review.update(id="resK-06", text_sha256="a" * 64, png_sha256="b" * 64,
                  status="reviewed_provisional", related_piece_ids=[])
    return {"batches": {"one": {"assigned_ids": ["resK-06"], "reviews": [review]}}}


def test_coverage_checker_accepts_complete_record_and_rejects_false_completion():
    good = _fixture()
    assert len(assert_complete_reviews(good, {"resK-06"})) == 1
    for corrupt in ("inventory_only", "blank_judgment", "whitespace_judgment",
                    "truncated_judgment", "wrong_piece", "pending", "duplicate",
                    "unknown_related_piece"):
        bad = copy.deepcopy(good)
        batch = bad["batches"]["one"]
        row = batch["reviews"][0]
        if corrupt == "inventory_only":
            batch["reviews"] = []
        elif corrupt == "blank_judgment":
            row["protected_negative_shapes"] = ""
        elif corrupt == "whitespace_judgment":
            row["protected_negative_shapes"] = "  "
        elif corrupt == "truncated_judgment":
            row["occlusion_and_contacts"] = "The near wheel meets…"
        elif corrupt == "wrong_piece":
            row["id"] = "resK-203"
        elif corrupt == "pending":
            row["status"] = "pending_direct_review"
        elif corrupt == "unknown_related_piece":
            row["related_piece_ids"] = ["resK-99999"]
        else:
            batch["reviews"].append(copy.deepcopy(row))
        with pytest.raises(AssertionError):
            assert_complete_reviews(bad, {"resK-06"})


def test_source_owner_loader_rejects_duplicate_keys():
    with pytest.raises(AssertionError, match="duplicate source-owner key"):
        json.loads('{"reviews": [], "reviews": ["silently overwrites"]}',
                   object_pairs_hook=reject_duplicate_keys)


def test_archive_section_context_preserves_every_piece_without_model_count_claim():
    analysis = json.loads(OWNER.read_text(), object_pairs_hook=reject_duplicate_keys)["qualitative_reference_analysis"]
    context = analysis["source_section_context"]
    archive = Path(analysis["archive"])
    headers = [(int(number), label) for number, label in re.findall(
        r"res(\d+)=\[([^\]]+)\]", (archive / "manifest.txt").read_text())]
    assert len(headers) == analysis["non_art_headers"] == 112
    groups = context["groups"]
    assert context["section_count"] == len(groups) == 108
    pieces = [piece for group in groups for piece in group["piece_ids"]]
    assert len(pieces) == len(set(pieces)) == context["retained_piece_count"] == 479
    assert set(pieces) == {path.stem for path in archive.glob("resK-*.txt")}
    # These are source-header contexts, deliberately NOT a semantic roster.
    # Individual titles/raster can contradict a containing header (190/191).
    for group in groups:
        for header in group["section_headers"]:
            assert (header["frame"], header["label"]) in headers
        for piece in group["piece_ids"]:
            number = int(piece.split("-")[1])
            major = category = leaf = None
            for frame, label in headers:
                if frame >= number:
                    break
                record = {"frame": frame, "label": label}
                if label.startswith("【"):
                    major, category, leaf = record, None, None
                elif label.startswith("《"):
                    category, leaf = record, None
                else:
                    leaf = record
            assert group["section_headers"] == [h for h in (major, category, leaf) if h]


def test_full_jidousha_qualitative_coverage_is_source_pinned():
    analysis = json.loads(OWNER.read_text(), object_pairs_hook=reject_duplicate_keys)["qualitative_reference_analysis"]
    archive = Path(analysis["archive"])
    assert archive.is_dir(), "Operator-named local archive is required for this local-source gate"
    ids = {path.stem for path in archive.glob("resK-*.txt")}
    assert len(ids) == analysis["inventory_count"] == 479
    assert {path.stem for path in archive.glob("resK-*.png")} == ids
    assert hashlib.sha256((archive / "manifest.txt").read_bytes()).hexdigest() == analysis["manifest_sha256"]
    reviews = assert_complete_reviews(analysis, ids)
    for row in reviews:
        for extension, field in (("txt", "text_sha256"), ("png", "png_sha256")):
            assert hashlib.sha256((archive / f"{row['id']}.{extension}").read_bytes()).hexdigest() == row[field], (
                row["id"], "source changed", extension)
