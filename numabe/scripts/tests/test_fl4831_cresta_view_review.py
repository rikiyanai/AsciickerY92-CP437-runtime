"""Review choreography and evidence scope, not rendered artwork acceptance."""
import json
import math

import pytest

from scripts import review_fl4831_cresta_views as review


def poses():
    return review.build_poses(json.loads(review.BANK.read_text()), json.loads(review.SPAWNS.read_text()))


def test_eight_distinct_native_directions_keep_subject_centered():
    rows = poses()
    assert [p["sample"] for p in rows] == list(range(0, 96, 12))
    assert len({tuple(round(x, 3) for x in p["eye"]) for p in rows}) == 8
    for row in rows:
        forward = [-math.sin(row["yaw"])*math.cos(row["pitch"]), math.sin(row["pitch"]),
                   -math.cos(row["yaw"])*math.cos(row["pitch"])]
        delta = [b-a for a, b in zip(row["eye"], row["target"])]
        length = math.sqrt(sum(x*x for x in delta))
        assert forward == pytest.approx([x/length for x in delta])
    assert rows[0]["eye"] == pytest.approx(review.ANCHOR_WORLD, abs=.00001)


def test_calibrated_root_fails_if_authored_grid_changes():
    spawns = json.loads(review.SPAWNS.read_text())
    for marker in spawns["minimap_markers"]:
        if marker["name"] == "race_grid_1":
            marker["world_xz"][0] += 1
    with pytest.raises(ValueError, match="no longer matches"):
        review.build_poses(json.loads(review.BANK.read_text()), spawns)


def test_each_view_uses_existing_canonical_publisher_and_explicit_diagnostic_scope(tmp_path):
    for index, pose in enumerate(poses()):
        cmd = review.command(tmp_path/str(index), 2600+index, pose)
        assert cmd[1] == "scripts/run_fl4359_headed_movement.py"
        assert cmd[cmd.index("--presentation-mode")+1] == "FF_SHARED"
        assert cmd[cmd.index("--run-classification")+1] == "diagnostic-only"
        assert cmd[cmd.index("--proof-id")+1] == "proof.fl4359_headed_movement"
        assert "--extra-user-arg=--fl4831-cresta-native-audit-after-frame=30" in cmd


def test_missing_view_is_not_counted_as_observed_or_accepted(tmp_path):
    result = review.inspect_capture(tmp_path, poses()[0], 1)
    assert not result["observed"] and not result["accepted"]
    assert result["reason"] == "missing_receipt_or_source_provenance"
