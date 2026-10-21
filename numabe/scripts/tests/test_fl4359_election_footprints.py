from copy import deepcopy
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fl4359_election_footprints import classify, key, triangle_depth


def quad(identity, lo=40, hi=50, z=.7):
    xy = np.asarray([[lo, lo], [hi, lo], [lo, hi], [hi, hi]]) / 50 - 1
    clip = np.column_stack((xy, [z]*4, [1]*4)).tolist()
    return {"identity": identity, "clip": clip, "world": clip, "centre": [0, 0, z]}


def setup():
    a = {"source": [1, 10, 1, 1], "origin": [0, 0, 0], "elected": [[2, 0, 10, 0]], "cell": [4, 4]}
    b = {**a, "source": [1, 11, 1, 1], "elected": [[2, 0, 11, 0]]}
    row = {"before": a, "after": b}
    ga = {key(a): quad(a["elected"][0]), key(b): quad(b["elected"][0], z=.8)}
    return row, ga, deepcopy(ga), {"cell_size": [10, 10], "target_size": [100, 100]}


def test_overlap_preserves_depth_deltas_without_acceptance_claim():
    row, ga, gb, view = setup()
    result = classify(row, ga, gb, view)
    assert result["kind"] == "overlap_at_both_poses"
    assert np.allclose(result["depth_a_minus_b"], [-.1, -.1])
    assert result["world_centres_changed"] is False


def test_boundary_crossing_is_distinct_from_depth_competition():
    row, ga, gb, view = setup()
    gb[key(row["before"])] = quad(row["before"]["elected"][0], lo=30, hi=40)
    assert classify(row, ga, gb, view)["kind"] == "boundary_entry_or_exit"


def test_missing_and_changed_capture_identity_stay_unresolved():
    row, ga, gb, view = setup()
    gb[key(row["before"])] = None
    assert classify(row, ga, gb, view)["kind"] == "missing_or_ambiguous_quad"
    gb[key(row["before"])] = quad([2, 0, 99, 0])
    assert classify(row, ga, gb, view)["kind"] == "capture_identity_changed"


def test_projected_depth_is_linear_in_screen_barycentric_coordinates():
    q = quad([1])
    q["clip"][1][2] = .9
    q["clip"][3][2] = .9
    assert np.isclose(triangle_depth(q["clip"], np.array([45., 45.]), [100, 100]), .8)
