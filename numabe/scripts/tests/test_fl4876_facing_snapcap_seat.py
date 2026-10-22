#!/usr/bin/env python3
"""FL-4876 FACING-01, SNAP-CAP-01, WALL-SEAT-01, POST-GROUND-01.

build_reg_87/88 card review: about 20 freestanding platform and track signs stood 90-180 deg off their authored
facing (resolve() answered the carrier bearing); a frontage point snapped to the nearest face of its declared host from
29.5 m; 57 terrain-box wall fixtures floated 0.19-0.95 m off the wall they hang on; freestanding sign posts floated
1.8 m (the planner's sign mounting height was added to a mesh whose post already starts at the ground).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline import compile_population_plan as PLAN  # noqa: E402
from scripts.pipeline.canonical_attachment import resolve  # noqa: E402
from scripts.pipeline.classic_procedural_mesh import _add_box  # noqa: E402
from scripts.pipeline.compile_facade_shell import write_akm  # noqa: E402
from scripts.pipeline import lower_plan_fixtures as FIX  # noqa: E402

PLAN_SRC = (ROOT / "scripts" / "pipeline" / "compile_population_plan.py").read_text(encoding="utf-8")
FIX_SRC = (ROOT / "scripts" / "pipeline" / "lower_plan_fixtures.py").read_text(encoding="utf-8")

RAIL = [[0.0, 0.0], [100.0, 0.0]]                 # a rail running due east
INDEX = SimpleNamespace(
    by_id={"rail": {"feature_id": "rail", "geometry": {"kind": "polyline", "coordinates": RAIL}}},
    buildings=[], nearest_carrier=lambda x, y: (0.0, "rail", 0.0, (x, 0.0), 0.0))


def _chainage(**extra):
    return {"record_type": "attachment", "anchor_kind": "carrier_chainage", "canonical_carrier_id": "rail",
            "chainage_m": 30.0, "side": "R", "lateral_offset_m": 2.0, **extra}


def test_chainage_pose_is_the_carrier_bearing_without_an_authored_facing():
    assert resolve(_chainage(), INDEX)["yaw_deg"] == pytest.approx(0.0)


def test_chainage_pose_keeps_the_authored_facing():
    pose = resolve(_chainage(authored_yaw_deg=322.0), INDEX)
    assert pose["yaw_deg"] == pytest.approx(322.0)
    assert pose["carrier_bearing_deg"] == pytest.approx(0.0)        # still available to callers that want it
    assert (pose["E"], pose["N"]) == pytest.approx((30.0, 2.0))     # the position is unchanged


def test_freestanding_pose_keeps_the_authored_yaw():
    rec = {"record_type": "attachment", "anchor_kind": "freestanding", "E": 12.0, "N": 3.0,
           "nearest_carrier_id": "rail", "authored_yaw_deg": 139.0}
    assert resolve(rec, INDEX)["yaw_deg"] == pytest.approx(139.0)
    rec.pop("authored_yaw_deg")
    assert resolve(rec, INDEX)["yaw_deg"] == pytest.approx(0.0)       # carrier bearing, as before


def test_the_face_snap_cap_is_one_and_a_half_metres():
    assert PLAN.FACE_POINT_SNAP_MAX_M == pytest.approx(1.5)


def test_the_snap_limit_widens_to_twice_the_observation_uncertainty():
    assert PLAN._face_snap_limit_m({}) == pytest.approx(1.5)
    assert PLAN._face_snap_limit_m({"evidence": {"position_uncertainty_m": 0.5}}) == pytest.approx(1.5)
    # build_reg_89 SF-4876-S401-080: a +-3 m camera-ray point 4.7 m off its host face keeps the snap
    assert PLAN._face_snap_limit_m({"evidence": {"position_uncertainty_m": 3.0}}) == pytest.approx(6.0)
    assert PLAN._face_snap_limit_m({"evidence": {"position_uncertainty_m": "n/a"}}) == pytest.approx(1.5)


def test_a_point_inside_another_building_is_never_kept_by_the_refusal():
    i = PLAN_SRC.index("_bd > _face_snap_limit_m(_apl)")
    assert "not _mass_conflicts(_fx, _fy, _bfid3)" in PLAN_SRC[i:i + 200]


def test_the_snap_branch_refuses_beyond_the_cap_and_records_why():
    i = PLAN_SRC.index("_bd > _face_snap_limit_m(_apl)")
    block = PLAN_SRC[i:i + 2600]
    assert "face_snap_refused_beyond_limit" in block
    assert '"kept": "authored_point_and_yaw"' in block
    assert "snap_limit_m" in block and "nearest_face_distance_m" in block
    # a face-wide module (shutter...) exists only on a face: its snap stands
    assert "FULL_FACE_UNIT_MODULES" in PLAN_SRC[PLAN_SRC.index("def _snap_refusal_site"):][:900]


def test_a_refused_snap_is_not_demoted_to_an_omitted_facade_slot():
    i = PLAN_SRC.index("FL-4876 Repair 2. A facade is geometry on a building face.")
    assert "not _face_snap_refused" in PLAN_SRC[i:i + 3600]


def test_obsyaw02_and_refile_keep_an_authored_facing():
    i = PLAN_SRC.index("# OBSYAW-02:")
    assert '"authored_orientation_basis" not in placement["evidence"]' in PLAN_SRC[i:i + 1400]
    j = PLAN_SRC.index('_fpl["yaw_deg"] = _fyaw')
    assert '"authored_orientation_basis" not in (_fpl.get("evidence") or {})' in PLAN_SRC[j - 700:j]


def test_authored_facing_is_stamped_on_the_chainage_attachment():
    i = PLAN_SRC.index('"authored_yaw_deg": round(float(_apl["yaw_deg"]), 3)')
    block = PLAN_SRC[i - 900:i + 500]
    assert '"carrier_chainage"' in block and '"freestanding"' in block
    assert "not isinstance(_apl.get(\"run\"), dict)" in block        # runs keep their chord


def test_the_orientation_audit_counts_an_authored_facing_instead_of_judging_it():
    carriers = {"k": {"carrier_class": "railway:rail", "polyline": [(0.0, 0.0), (100.0, 0.0)]}}
    catalog = {"m": {"module_id": "m", "owning_domain": "street_fixture"}}     # lateral "hardware": carrier-aligned
    base = {"placement_id": "p", "module_id": "m", "E": 50.0, "N": 2.0, "yaw_deg": 135.0,
            "evidence": {"kind": "observation"}}
    plain = PLAN.audit_orientation({"k": [dict(base)]}, carriers, catalog)
    authored = PLAN.audit_orientation(
        {"k": [dict(base, evidence={"kind": "observation", "authored_orientation_basis": "faces the train"})]},
        carriers, catalog)
    assert plain["violations"] == 1                                     # 135 deg to a due-east rail is judged
    assert authored["violations"] == 0 and authored["exempt_authored_facing"] == 1


# --- lowering ---------------------------------------------------------------------------------------------------

def test_wall_fixtures_are_seated_with_the_sign_plane_wall_rule_and_carry_no_planned_height():
    assert "from scripts.pipeline.compile_sign_planes import FACE_PANEL_MODULES, wall_distance_m" in FIX_SRC
    assert "wall_distance_m(wall_terrain, rings_world, mass_ids, wx0, wy0, nx, ny, scale)" in FIX_SRC
    i = FIX_SRC.index("# FL-4876 WALL-SEAT-01: stand the back")
    block = FIX_SRC[i:i + 1800]
    assert "ground_bake(p, 0.0)" in block                    # heights are in the mesh; no 1.8 m added again
    assert "(length / _native_w) if _real_size else length" in block


def test_seat_move_is_capped_inside_the_coherence_tolerance():
    assert FIX.WALL_SEAT_BUDGET_M < 1.5 and FIX.WALL_SEAT_GAP_M < 0.05


def test_mesh_zmin_reads_a_post_that_starts_at_the_ground(tmp_path):
    v, f = [], []
    _add_box(v, f, (-0.03, 0.03, -0.03, 0.03, 0.0, 1.5), (60, 60, 60), 2.0)      # post, mesh scale 2
    _add_box(v, f, (-0.3, 0.3, -0.03, 0.03, 1.0, 1.4), (230, 230, 230), 2.0)     # plate
    grounded = tmp_path / "post.akm"
    write_akm(grounded, v, f, comment="t")
    assert FIX._mesh_zmin_m(grounded, 2.0) == pytest.approx(0.0, abs=1e-6)
    v, f = [], []
    _add_box(v, f, (-0.3, 0.3, -0.03, 0.03, 2.3, 2.7), (230, 230, 230), 2.0)
    floating = tmp_path / "plate.akm"
    write_akm(floating, v, f, comment="t")
    assert FIX._mesh_zmin_m(floating, 2.0) == pytest.approx(2.3, abs=1e-6)
    assert FIX._mesh_zmin_m(tmp_path / "absent.akm", 2.0) is None


def test_a_grounded_freestanding_mesh_drops_the_planned_height():
    i = FIX_SRC.index("# FL-4876 POST-GROUND-01.")
    block = FIX_SRC[i:i + 1800]
    assert "GROUNDED_MESH_MAX_ZMIN_M" in block
    assert "_lift_m = 0.0 if _grounded else height_m" in block
