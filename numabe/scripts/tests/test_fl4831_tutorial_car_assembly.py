"""FL-4831: real car-bank geometry, placement and material regressions.

Source/data diagnostics only. No renderer launch or visual acceptance.
"""
import copy
import json
from pathlib import Path

import pytest

from scripts.merge_car_faces import merge_faces, RACE_PIVOTS

ROOT = Path(__file__).resolve().parents[2]
SOURCES = json.loads((ROOT / "assets/meshes/incoming/tutorial_cars.source.json").read_text())


def part_bounds(doc, part):
    rows = doc["rows"][part["face_start"] * 16:(part["face_start"] + part["face_count"]) * 16]
    return [(min(rows[i+c+a] for i in range(0, len(rows), 16) for c in (0, 4, 8, 12)),
             max(rows[i+c+a] for i in range(0, len(rows), 16) for c in (0, 4, 8, 12))) for a in range(3)]


def assert_placed_wheels(doc):
    pivots = []
    for part in doc["parts"][1:]:
        bounds = part_bounds(doc, part)
        centre = [(lo+hi)/2 for lo, hi in bounds]
        assert centre == pytest.approx(part["offset_in_body_frame"], abs=1e-6)
        pivots.append(centre)
    assert pivots[0][0] > 0 and pivots[1][0] > 0  # front axle, +X nose
    assert pivots[2][0] < 0 and pivots[3][0] < 0
    assert pivots[0][2] < 0 and pivots[2][2] < 0  # left side
    assert pivots[1][2] > 0 and pivots[3][2] > 0


@pytest.mark.parametrize("name", SOURCES["models"])
def test_shipped_variant_wheels_are_in_body_frame(name):
    model = SOURCES["models"][name]
    doc = json.loads((ROOT / model["faces"]).read_text())
    assert doc["model_space"] == "body_frame"
    assert_placed_wheels(doc)
    # Match the existing race-v1 chassis-to-wheel bottom relationship, not
    # body-AABB height (which differs by model and includes bumpers/roof).
    bottom = min(part_bounds(doc, p)[1][0] for p in doc["parts"])
    assert bottom * 0.9 + doc["ride_lift"] == pytest.approx(-0.285703125, abs=1e-6)
    materials = set(doc["rows"][3::16])
    assert materials == {model["assembly"]["body_material"], 217, 219}
    assert materials.isdisjoint({200, 201, 203, 213, 220})
    for part in doc["parts"]:
        start, count = part["edge_start"], part["edge_count"]
        eu = doc["edge_rows"]["uints"][start*4:(start+count)*4]
        lo, hi = part["face_start"], part["face_start"] + part["face_count"]
        assert all(lo <= f < hi for f in eu[0::4])
        assert all(f == 0xFFFFFFFF or lo <= f < hi for f in eu[1::4])
        ef = doc["edge_rows"]["floats"][start*16:(start+count)*16]
        bounds = part_bounds(doc, part)
        assert all(bounds[a][0]-1e-6 <= ef[i+c+a] <= bounds[a][1]+1e-6
                   for i in range(0, len(ef), 16) for c in (0, 4) for a in range(3))


def test_checker_rejects_the_old_origin_centred_wheel_failure():
    model = SOURCES["models"]["mustang67"]
    doc = json.loads((ROOT / model["faces"]).read_text())
    for part in doc["parts"][1:]:
        for face in range(part["face_start"], part["face_start"]+part["face_count"]):
            for corner in (0, 4, 8, 12):
                for a in range(3):
                    doc["rows"][face*16+corner+a] -= part["offset_in_body_frame"][a]
    with pytest.raises(AssertionError):
        assert_placed_wheels(doc)


def test_merge_places_local_rows_and_rebases_edges_without_mutating_inputs():
    # Small independent fixture exercises the production assembler, including
    # the open-face sentinel and nonuniform normal transform.
    doc = {"face_count": 1, "dimensions_xyz": [2, 2, 2], "mats": {"body": 218},
           "rows": [-1, -1, 0, 218, 1, -1, 0, .8, 0, 1, 0, 0, 0, 1, 0, 0],
           "edge_rows": {"floats": [-1,-1,0,0, 1,-1,0,0, 0,0,1,0, 0,0,0,0],
                         "uints": [0, 0xFFFFFFFF, 3, 218], "run_uints": [0, 0]},
           "edge_table": {"count": 1, "runs": 1, "run_max_length": 1}}
    before = copy.deepcopy(doc)
    result = merge_faces(doc, [doc]*4, "fixture", wheel_scale=(2, 2, .5))
    assert doc == before
    assert result["parts"][1]["edge_count"] == 1
    assert result["edge_rows"]["uints"][4:8] == [1, 0xFFFFFFFF, 3, 218]
    assert result["edge_rows"]["run_uints"] == [0,0, 1,0, 2,0, 3,0, 4,0]
    assert result["rows"][16:19] == pytest.approx([RACE_PIVOTS[0][0]-2, RACE_PIVOTS[0][1]-2, RACE_PIVOTS[0][2]])


def test_source_forward_axes_preserve_tutorial_body_instance_rotation():
    assert SOURCES["models"]["basic_car"]["assembly"]["forward_axis"] == "-z"
    for name in ("mustang67", "nissan35"):
        assert SOURCES["models"][name]["assembly"]["forward_axis"] == "+z"
