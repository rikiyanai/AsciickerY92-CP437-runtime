"""FL-4831: approved construction binding, not runtime foreground acceptance."""
import json
from pathlib import Path

import pytest

from scripts import export_fl4831_approved_runtime_cars as export

ROOT = Path(__file__).resolve().parents[2]
OWNER = json.loads(export.OWNER.read_text())["authored_turntable_targets"]


@pytest.mark.parametrize("target", OWNER["targets"], ids=lambda t: t["id"])
def test_runtime_parts_and_selected_construction_come_from_approved_subject(target):
    construction = json.loads((ROOT/target["construction"]).read_text())
    actual = json.loads((export.OUTPUT/f'approved_car_{target["id"]}_faces.json').read_text())
    assert actual == export.convert(target, construction)
    assert actual["approved_target"]["gif_sha256"] == export.sha(ROOT/target["gif"])
    assert [p["name"] for p in actual["parts"]] == list(export.PARTS)
    assert len(actual["rows"]) == actual["face_count"]*16
    assert sum(p["face_count"] for p in actual["parts"]) == actual["face_count"]
    assert actual["forward_axis"] == "+X" and actual["wheel_spin_axis_baked"] == "+Z"
    assert actual["parts"][1]["offset_in_body_frame"][0] > actual["parts"][3]["offset_in_body_frame"][0]
    assert actual["wheel_radius_world"] == pytest.approx(actual["parts"][1]["radius"]*.9)
    authored = actual["authored_foreground"]
    assert len(authored["paths"]) == len(construction["paths"])
    assert len(authored["protected_openings"]) == len(construction["protected_openings"])
    assert len(authored["bounded_tone_sites"]) == len(construction["bounded_tone_sites"])
    assert "NOT_IMPLEMENTED" in authored["status"]
    for path in authored["paths"]:
        assert path["part"] == export.part_for_path(path["id"])
    assert all(export.part_for_path(f"contact.{side}.{axle}") == 0 for side in (-1,1) for axle in (0,1))


def test_approval_does_not_promote_runtime_acceptance():
    requirement = OWNER["godot_runtime_requirement"]
    assert set(requirement["roster"]) == {t["id"] for t in OWNER["targets"]}
    assert requirement["foreground_state"].startswith("OPEN:")
    assert OWNER["scope"].startswith("AUTHORED_TARGET_NOT_GAME_RENDER")
