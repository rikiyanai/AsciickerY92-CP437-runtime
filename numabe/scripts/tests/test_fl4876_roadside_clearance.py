"""FL-4876 ROADSIDE-CLEAR-01 and FACE-SPAN-01 regression tests.

A straight road runs along +x at N = 0 with painted carriageway |N| < 3 m.
A building occupies 40 <= E <= 60, 4 <= N <= 10, north of the road.
"""
import copy

from scripts.pipeline.compile_custom_asset_plan import _face_index, _fit_on_face
from scripts.pipeline.roadside_clearance import clear

CANONICAL = {"canonical_features": [
    {"feature_id": "feature:road:way/1", "semantic_class": "road",
     "geometry": {"kind": "polyline", "coordinates": [[0.0, 0.0], [100.0, 0.0]]}},
    {"feature_id": "feature:building_footprint:way/2", "semantic_class": "building_footprint",
     "geometry": {"kind": "polygon", "coordinates": [
         [40.0, 4.0], [60.0, 4.0], [60.0, 10.0], [40.0, 10.0], [40.0, 4.0]]}},
]}


class Surf:
    def __init__(self, half=3.0):
        self.half = half

    def point_surfaces(self, e, n):
        return {"carriageway"} if abs(n) < self.half else {"sidewalk"}


def _placement(pid, lateral, vertical, side="R"):
    return {"placement_id": pid, "module_id": "street_fixture", "E": 50.0, "N": lateral,
            "asset_physical_envelope": {"vertical_extent_m": vertical,
                                        "longitudinal_extent_m": 0.4,
                                        "lateral_extent_m": 0.4},
            "attachment": {"record_type": "attachment", "anchor_kind": "carrier_chainage",
                           "canonical_carrier_id": "feature:road:way/1", "chainage_m": 50.0,
                           "lateral_offset_m": lateral, "side": side}}


def _plan(*placements):
    return {"carriers": [{"placements": list(placements)}]}


def test_standing_fixture_on_carriageway_moves_to_nearest_kerbside_site():
    plan = _plan(_placement("lamp", 1.0, 5.0))
    receipt = clear(plan, CANONICAL, Surf(), {"modules": []})
    assert receipt["counts"] == {"checked": 1, "moved": 1, "withdrawn": 0}
    moved = plan["carriers"][0]["placements"][0]
    # Off the paint (and 0.3 m beyond), clear of the building at N >= 4 - 0.2.
    assert 3.0 <= abs(moved["N"]) < 3.8
    assert moved["evidence"]["roadside_clearance"]["kind"] == "moved_off_painted_carriageway"


def test_road_decal_is_left_on_the_asphalt():
    plan = _plan(_placement("crossing_marker", 0.5, 0.03))
    before = copy.deepcopy(plan)
    receipt = clear(plan, CANONICAL, Surf(), {"modules": []})
    assert receipt["counts"]["checked"] == 0
    assert plan == before


def test_fixture_already_off_the_paint_is_untouched():
    plan = _plan(_placement("sign", 3.5, 2.26))
    before = copy.deepcopy(plan)
    receipt = clear(plan, CANONICAL, Surf(), {"modules": []})
    assert receipt["counts"] == {"checked": 1, "moved": 0, "withdrawn": 0}
    assert plan == before


def test_no_legal_site_keeps_an_explicit_terminal_debt_not_a_road_object():
    plan = _plan(_placement("signal", 1.0, 3.0))
    receipt = clear(plan, CANONICAL, Surf(half=30.0), {"modules": []})
    assert receipt["counts"]["withdrawn"] == 1
    kept = plan["carriers"][0]["placements"][0]
    assert kept["population_disposition"]["category"] == "no_kerbside_site_off_carriageway"


def test_guardrail_run_on_the_paint_shifts_away_from_the_carrier():
    p = _placement("rail", 2.0, 1.5)
    p["module_id"] = "street_fixture"
    p["attachment"] = dict(p["attachment"], anchor_kind="carrier_span")
    p["run"] = {"E0": 10.0, "N0": -2.0, "E1": 20.0, "N1": -2.0, "length_m": 10.0}
    plan = _plan(p)
    receipt = clear(plan, CANONICAL, Surf(), {"modules": []})
    assert receipt["counts"]["moved"] == 1
    run = plan["carriers"][0]["placements"][0]["run"]
    # Same (south) side, now at or past the 3 m paint edge (nearest legal shift).
    assert run["N0"] == run["N1"] and -4.0 < run["N0"] <= -3.0


def test_kerb_line_is_the_paint_edge_and_is_not_moved():
    p = _placement("kerb", 2.9, 3.0)
    p["module_id"] = "curb_edge_line"
    p["run"] = {"E0": 10.0, "N0": 2.9, "E1": 20.0, "N1": 2.9, "length_m": 10.0}
    plan = _plan(p)
    before = copy.deepcopy(plan)
    catalog = {"modules": [{"module_id": "curb_edge_line", "owning_domain": "road_cross_section"}]}
    receipt = clear(plan, CANONICAL, Surf(), catalog)
    assert receipt["counts"]["checked"] == 0 and plan == before


def test_pole_on_the_rail_centreline_leaves_the_train_envelope():
    canonical = copy.deepcopy(CANONICAL)
    canonical["source_observations"] = [
        {"observation_id": "obs:rail", "attributes": {"tags": {"railway": "rail"}}}]
    canonical["canonical_features"].append(
        {"feature_id": "feature:railway_boundary:way/9", "semantic_class": "railway_boundary",
         "supporting_observation_ids": ["obs:rail"],
         "geometry": {"kind": "polyline", "coordinates": [[0.0, -20.0], [100.0, -20.0]]}})
    p = _placement("pole", 0.0, 10.0)
    p["attachment"] = dict(p["attachment"], canonical_carrier_id="feature:railway_boundary:way/9")
    plan = _plan(p)
    receipt = clear(plan, canonical, Surf(), {"modules": []})
    assert receipt["counts"]["moved"] == 1 and receipt["rail_lines"] == 1
    moved = plan["carriers"][0]["placements"][0]
    assert abs(moved["N"] - (-20.0)) >= 1.7


def test_level_crossing_deck_stays_on_the_track():
    canonical = copy.deepcopy(CANONICAL)
    canonical["source_observations"] = [
        {"observation_id": "obs:rail", "attributes": {"tags": {"railway": "rail"}}}]
    canonical["canonical_features"].append(
        {"feature_id": "feature:railway_boundary:way/9", "semantic_class": "railway_boundary",
         "supporting_observation_ids": ["obs:rail"],
         "geometry": {"kind": "polyline", "coordinates": [[0.0, -20.0], [100.0, -20.0]]}})
    p = _placement("deck", 0.0, 3.5)
    p.update(module_id="level_crossing_equipment", variant="roadway_over_double_track")
    p["attachment"] = dict(p["attachment"], canonical_carrier_id="feature:railway_boundary:way/9")
    plan = _plan(p)
    before = copy.deepcopy(plan)
    clear(plan, canonical, Surf(), {"modules": [{"module_id": "level_crossing_equipment",
                                                  "owning_domain": "rail_edge"}]})
    assert plan == before


def test_face_plane_wider_than_its_anchor_side_slides_onto_the_face(tmp_path):
    path = tmp_path / "canonical.json"
    import json
    path.write_text(json.dumps(CANONICAL), encoding="utf-8")
    index = _face_index(path)
    # Face 0 is the south face (40,4)->(60,4), 20 m long; anchor at its end.
    attachment = {"anchor_kind": "building_face",
                  "canonical_building_id": "feature:building_footprint:way/2",
                  "face_index": 0, "along_face_fraction": 1.0, "outward_offset_m": 0.0}
    # Anchor at the face end: the slide is capped at 1.4 m (inside coherence's
    # 1.5 m tolerance), so the 12 m plane shrinks to 2.8 m centred 1.4 m in.
    width, new_att, pose, record = _fit_on_face(attachment, 12.0, index)
    assert abs(width - 2.8) < 1e-6
    assert abs(new_att["along_face_fraction"] - 0.93) < 1e-6
    assert abs(pose["E"] - 58.6) < 1e-3 and abs(pose["N"] - 4.0) < 1e-3
    assert abs(record["slide_m"]) <= 1.4 + 1e-6
    # Wider than the face at fraction 0.2: centre slides 1.4 m toward the
    # middle (4 -> 5.4 m) and the plane is 10.8 m, both ends on the face.
    width, new_att, _, _ = _fit_on_face(dict(attachment, along_face_fraction=0.2), 30.0, index)
    assert abs(width - 10.8) < 1e-6 and abs(new_att["along_face_fraction"] - 0.27) < 1e-6
    # Fits already: no change.
    assert _fit_on_face(dict(attachment, along_face_fraction=0.5), 4.0, index) is None
