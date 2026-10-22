"""FL-4359: attribution must distinguish cell election from changing source paint."""
import gzip
import json
from pathlib import Path
import sys

import pytest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fl4359_actual_field import ActualFieldError
from fl4359_flat_colour_attribution import (
    compare, restored_field, replay_samples, summarize_pairs, digest, projected_extent,
    extent_summary, capture_texel_clip_corners, review_samples,
)


def mark(owner, flat=0.2, observed=0.2, probe=1, glyph=46):
    return {"elected": owner, "flat_fg": [flat] * 3, "flat_bg": [flat] * 3,
            "fg": [observed] * 3, "bg": [observed] * 3, "probe": probe, "glyph": glyph}


def test_constant_texel_can_receive_a_different_cells_colour():
    result = compare({"same-world": [mark("A")]}, {"same-world": [mark("B", flat=0.8)]})
    group = result["strata"]["different_elected_source/grid_probes_only"]
    assert group["flat_fg_changes"] == group["flat_fg_changes_with_observed_stable"] == 1
    assert group["observed_fg_changes"] == 0


def test_changed_paint_on_same_source_is_not_called_election_change():
    result = compare({1: [mark("A", probe=0)]}, {1: [mark("A", flat=0.8, observed=0.8, glyph=47)]})
    group = result["strata"]["same_elected_source/eye_in_pair"]
    assert group["flat_fg_changes"] == group["observed_fg_changes"] == group["elected_glyph_changes"] == 1
    assert group["flat_fg_changes_with_observed_stable"] == 0


def test_ambiguous_and_unanchored_sources_are_not_silently_classified_stable():
    result = compare({1: [mark("A"), mark("A")], 2: [mark(None)]},
                     {1: [mark("A")], 2: [mark("B", flat=0.8)]})
    assert result["counts"]["ambiguous_excluded"] == 1
    assert result["strata"]["unanchored_elected_source/grid_probes_only"]["matched"] == 1
    assert result["coverage"] == {
        "before_admitted_rows": 3, "after_admitted_rows": 2, "matched_rows_each_frame": 1,
        "before_unmeasured_rows": 2, "after_unmeasured_rows": 1,
    }


def test_coverage_counts_unmatched_rows_and_duplicates_in_each_frame():
    pair = compare({1: [mark("A")], 2: [mark("B")] * 3},
                   {1: [mark("A")], 3: [mark("C")]})
    assert pair["coverage"]["before_unmeasured_rows"] == 3
    assert pair["coverage"]["after_unmeasured_rows"] == 1
    assert pair["coverage"]["matched_rows_each_frame"] == 1


def test_projected_extent_uses_homogeneous_coordinates_and_actual_cell_axes():
    # 0.2 NDC units across each axis of a 1000x500 target = 100x50 pixels.
    corners = [[0, 0, 0, 1], [.4, 0, 0, 2], [0, .6, 0, 3], [.8, .8, 0, 4]]
    assert projected_extent(corners, [1000, 500], [25, 50]).tolist() == pytest.approx([4, 1])
    summary = extent_summary([[.5, 2], [2, .5], [1, 1], [.5, .5]])
    assert summary["bbox_smaller_than_cell_on_either_axis"] == 3
    assert summary["bbox_smaller_than_cell_on_both_axes"] == 1


def test_projected_extent_refuses_clipped_or_invalid_geometry():
    with pytest.raises(ActualFieldError):
        projected_extent([[0, 0, 0, 0]] * 4, [1000, 500], [16, 32])
    with pytest.raises(ActualFieldError):
        projected_extent([[0, 0, 0, 1]] * 4, [1000, 500], [0, 32])


def test_unexpanded_texel_uses_cubemap_face_and_column_major_camera():
    matrix = np.eye(4)
    matrix[3, :3] = [10, 20, 30]  # Flat Godot columns: translation is last column.
    for face in range(6):
        corners = capture_texel_clip_corners(face, face * 4, 2, [1, 2, 3], 4, matrix.ravel())
        axis, sign = face // 2, 1 if face % 2 == 0 else -1
        assert corners[:, axis].tolist() == pytest.approx([sign * 4 + [11, 22, 33][axis]] * 4)
        assert corners[:, 3].tolist() == [1] * 4
    with pytest.raises(ActualFieldError):
        capture_texel_clip_corners(1, 0, 2, [0, 0, 0], 4, matrix.ravel())


def test_restoration_removes_only_its_own_temporary_file(tmp_path):
    target = tmp_path / "field.bin"
    with gzip.open(str(target) + ".gz", "wb") as out:
        out.write(b"original bytes")
    metadata = {"raw_path": str(target)}
    with pytest.raises(RuntimeError), restored_field(tmp_path / "capture.jsonl", metadata):
        assert target.read_bytes() == b"original bytes"
        raise RuntimeError("consumer failed")
    assert not target.exists()
    target.write_bytes(b"pre-existing")
    with restored_field(tmp_path / "capture.jsonl", metadata):
        assert target.read_bytes() == b"pre-existing"
    assert target.read_bytes() == b"pre-existing"


def test_restoration_rejects_external_path(tmp_path):
    with pytest.raises(ActualFieldError, match="within its capture"):
        with restored_field(tmp_path / "capture.jsonl", {"raw_path": str(tmp_path.parent / "outside.bin")}):
            pass


def test_compact_sample_replay_reproduces_counts_and_rejects_tampering(tmp_path):
    samples = tmp_path / "samples.jsonl.gz"
    banks = [{("world", 1): [mark(("owner", i), flat=0.1 * i)]} for i in range(5)]
    with gzip.open(samples, "wt") as output:
        for bank in banks:
            output.write(json.dumps({"bank": list(bank.items())}) + "\n")
    expected = summarize_pairs([compare(a, b) for a, b in zip(banks, banks[1:])])
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"mode": "CC", "samples_file": samples.name,
                                 "samples_sha256": digest(samples), **expected}))
    assert replay_samples(result) == expected
    review = review_samples(result)
    assert review["acceptance_claimed"] is False
    assert len(review["pairs"]) == 4
    for pair in review["pairs"]:
        assert pair["rates"]["source_switch"] == 1
        assert pair["coverage"]["before_unmeasured_rows"] == 0
    with samples.open("ab") as output:
        output.write(b"changed")
    with pytest.raises(ActualFieldError, match="hash mismatch"):
        replay_samples(result)


def test_wide_continuation_paint_churn_is_distinct_from_cell_candidate_churn():
    before = dict(mark("A", glyph=65), painted_glyph=764)
    after = dict(mark("A", glyph=65), painted_glyph=765)
    result = compare({"same-world": [before]}, {"same-world": [after]})
    assert result["counts"]["elected_glyph_changes"] == 0
    assert result["counts"]["painted_glyph_changes"] == 1
