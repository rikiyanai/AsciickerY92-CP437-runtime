from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fl4359_lattice_comparison import compare_frames


def fixture():
    return [{"grid": [2, 1], "cell_size": [16, 32], "matrix": [1] * 16,
             "probe_size": 384, "transition": 1,
             "cells": [{"class": [2, 0, 4], "elected": [i], "glyph": i % 2,
                        "fg": [.1 * i] * 3, "bg": [.2] * 3} for _ in range(2)]}
            for i in range(5)]


def test_fixed_lattice_reports_raw_changes_without_acceptance():
    a = fixture()
    b = deepcopy(a)
    for frame in b:
        frame["probe_size"] = 256
        for cell in frame["cells"]:
            cell.update(glyph=0, fg=[.2] * 3)
    result = compare_frames(a, b)
    assert result["acceptance_claimed"] is False
    assert sum(p["before"]["painted_glyph_changes"] for p in result["temporal"]) == 8
    assert sum(p["after"]["painted_glyph_changes"] for p in result["temporal"]) == 0
    assert result["transition_sequences_match"] is True


def test_lost_surface_is_reported_and_excluded_from_both_temporal_sides():
    a = fixture()
    b = deepcopy(a)
    b[2]["cells"][1]["class"] = None
    b[2]["transition"] = 0
    result = compare_frames(a, b)
    assert result["fidelity"][2]["counts"]["world_to_unanchored"] == 1
    assert sum(p["excluded_cells"] for p in result["temporal"]) == 2
    assert all(p["before"]["eligible"] == p["after"]["eligible"] for p in result["temporal"])
    assert result["transition_sequences_match"] is False


def test_changed_camera_cannot_masquerade_as_density_effect():
    a = fixture()
    b = deepcopy(a)
    b[2]["matrix"][3] = 2
    with pytest.raises(ValueError, match="camera or cell lattice differs"):
        compare_frames(a, b)


def test_exact_source_subset_excludes_either_pose_mismatch_but_keeps_broad_counts():
    a = fixture()
    b = deepcopy(a)
    for frame in b:
        for cell in frame["cells"]:
            cell["glyph"] = 0
    b[2]["cells"][1]["elected"] = [99]
    result = compare_frames(a, b)
    assert result["fidelity"][2]["counts"]["source_disagreements"] == 1
    assert sum(p["before"]["eligible"] for p in result["temporal"]) == 8
    exact = [p["same_elected_sources"] for p in result["temporal"]]
    assert sum(p["eligible"] for p in exact) == 6
    assert sum(p["excluded_cells"] for p in exact) == 2
    groups = [p["by_owner_object_material"]["[2, 0, 4]"] for p in exact]
    assert sum(g["before"]["painted_glyph_changes"] for g in groups) == 6
    assert sum(g["after"]["painted_glyph_changes"] for g in groups) == 0


def test_exact_source_subset_never_matches_two_missing_sources():
    a = fixture()
    for frame in a:
        for cell in frame["cells"]:
            cell["elected"] = None
    result = compare_frames(a, deepcopy(a))
    assert all(p["same_elected_sources"]["eligible"] == 0 for p in result["temporal"])


def test_exact_colour_check_does_not_hide_subthreshold_changes():
    a = fixture()
    b = deepcopy(a)
    b[2]["cells"][0]["fg"][0] += .0001
    result = compare_frames(a, b)
    counts = result["fidelity"][2]["counts"]
    assert counts["fg_disagreements"] == 0
    assert counts["exact_source_cells"] == 2
    assert counts["exact_source_fg_inequalities"] == 1
    assert counts["exact_source_bg_inequalities"] == 0
