"""Exercise the camera convention, joins and origin attribution independently."""
from copy import deepcopy
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fl4359_election_motion_diagnostic import address_relation, compare_motion, elected_origin, election_detail, project_cells


def test_column_major_translation_and_tall_grid():
    matrix = np.eye(4)
    matrix[0, 3] = .25
    matrix[1, 3] = -.5
    assert np.allclose(project_cells([0, 0, 0], matrix.flatten(order="F"), [20, 10]), [12.5, 2.5])
    matrix[3, 3] = -1
    with pytest.raises(ValueError):
        project_cells([0, 0, 0], matrix.flatten(order="F"), [20, 10])


def test_elected_origin_uses_actual_selected_role():
    p = {"state": [1, 1], "world": [1, 2, 3, 1], "identity": [1], "revisions": [0, 0, 0], "source": [2, 100, 7, 12]}
    state = {"captured_origin_valid_mask": 7, "captured_origins_after_capture": [[1, 0, 0], [2, 0, 0], [3, 0, 0]]}
    assert elected_origin(p, state) == (3, 0, 0)
    state["captured_origin_valid_mask"] = 3
    with pytest.raises(ValueError):
        elected_origin(p, state)


def test_motion_split_retains_ambiguous_and_unmatched_exclusions():
    trace = {"world_to_clip_matrix": np.eye(4).flatten(order="F"), "viewport": {"semantic_ansi_size": [20, 10]}}
    moved = deepcopy(trace)
    matrix = np.eye(4)
    matrix[0, 3] = .02  # 0.2 cell, below one quarter cell.
    moved["world_to_clip_matrix"] = matrix.flatten(order="F")
    key = ((), (0, 0, 0), ())
    ambiguous = ((), (1, 0, 0), ())
    missing = ((), (2, 0, 0), ())
    item = {"elected": (1,), "glyph": 5, "origin": (0, 0, 0), "cell": (10, 5)}
    changed = {**item, "elected": (2,), "glyph": 6, "origin": (1, 0, 0)}
    result = compare_motion({key: [item], ambiguous: [item, item], missing: [item]},
                            {key: [changed], ambiguous: [changed]}, trace, moved)
    assert result["counts"]["matched"] == 1
    assert result["counts"]["ambiguous_shared_keys_excluded"] == 1
    assert result["counts"]["before_unmeasured_rows"] == 3
    assert result["strata"]["lt_0.25/changed_origin"]["glyph_changes"] == 1
    assert result["counts"]["projected_centre_cell_changes"] == 0


def test_cube_address_adjacency_does_not_wrap_rows_or_faces():
    assert address_relation(10, 11, 8) == "adjacent_same_face"
    assert address_relation(10, 19, 8) == "adjacent_same_face"
    assert address_relation(7, 8, 8) == "nonadjacent_same_face"
    assert address_relation(63, 64, 8) == "different_face"
    with pytest.raises(ValueError):
        address_relation(0, 384, 8)


def test_same_address_can_change_geometry_and_is_not_assumed_stable():
    a = {"elected": ((2, 0, 10, 0), (1., 2., 3.), (1, 1, 1)),
         "source": [1, 10, 7, 1], "glyph": 4, "fg": [.2]*3, "bg": [.1]*3, "cell": (2, 3)}
    b = {**a, "elected": ((2, 0, 10, 0), (1., 2.1, 3.), (1, 1, 1)), "glyph": 5}
    d = election_detail(a, b, 8, .1, [1, 2, 3])
    assert d["address_relation"] == "same_address"
    assert d["source_changed"] and d["glyph_changed"]
    assert not d["recorded_cell_changed"]
    assert d["surface_relation"] == "same_primitive"
