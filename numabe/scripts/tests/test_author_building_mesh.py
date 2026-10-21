"""FL-4876: artifacts/experiments/fl4876_manual_world/author_building_mesh.py builds one hand-authored building mesh.

Geometry facts only (areas, extents, orientation, collision alpha, refusals). Whether a facade matches its street photo is checked
by looking (assess/live_fix_20261017/buildings_pilot). Synthetic footprint: no live build file is read.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "artifacts/experiments/fl4876_manual_world/author_building_mesh.py"
CS = 5.1320023927966725
META = {"content_scale": CS, "offset_x": 48.0, "offset_y": 48.0, "origin_easting": 379699.09478405997, "origin_northing": 3938417.876643479}


@pytest.fixture(scope="module")
def ab():
    spec = importlib.util.spec_from_file_location("fl4876_author_building_mesh", TOOL)
    assert spec is not None and spec.loader is not None
    m = importlib.util.module_from_spec(spec)
    sys.modules["fl4876_author_building_mesh"] = m
    spec.loader.exec_module(m)
    return m


def _row(w=8.0, d=6.0, h=9.0):
    """A w x d metre rectangle (world units), clockwise as the OSM footprints are: face 0 = the +n side."""
    cx, cy = 500.0, 500.0
    pts = [(-w / 2, -d / 2), (-w / 2, d / 2), (w / 2, d / 2), (w / 2, -d / 2)]      # clockwise seen from above
    return {"feature_id": "feature:building_footprint:way/1", "roof_height_m": h,
            "bake_footprint": [[cx + x * CS, cy + y * CS] for x, y in pts]}


def _spec(**kw):
    s = {"basis": "synthetic", "storey_heights_m": [4.0, 3.0], "height_m": 7.5,
         "wall": {"color": [200, 190, 170]}, "roof": {"type": "flat", "parapet_m": 0.5}, "faces": {"default": {}}}
    s.update(kw)
    return s


def test_flat_roof_extent_orientation_and_collision(ab):
    mesh, info = ab.build(_spec(), _row(), META)
    zs = [v[2] for v in mesh.v]
    assert min(zs) == pytest.approx(-0.6 * CS, abs=1e-2) and max(zs) == pytest.approx(7.5 * CS, abs=1e-2)
    xs = [v[0] for v in mesh.v]
    assert max(xs) - min(xs) == pytest.approx(8.0 * CS, abs=1e-2)
    assert info["storeys"] == 2 and len(info["faces"]) == 4
    for v in mesh.v:                                              # unit normals, wall alpha 0 or presentation 255
        assert math.sqrt(v[3] ** 2 + v[4] ** 2 + v[5] ** 2) == pytest.approx(1.0, abs=2e-3)
        assert v[9] in (0, 255)
    walls = [f for f in mesh.f if all(mesh.v[i][9] == 0 for i in f)]
    assert walls                                                  # the collider exists
    for f in walls:                                               # every collider quad faces away from the centre
        cx = sum(mesh.v[i][0] for i in f) / len(f)
        cy = sum(mesh.v[i][1] for i in f) / len(f)
        nx, ny = mesh.v[f[0]][3], mesh.v[f[0]][4]
        if abs(nx) + abs(ny) > 0.3 and abs(mesh.v[f[0]][5]) < 0.99:
            assert nx * cx + ny * cy > 0


def test_openings_split_the_wall_without_losing_or_inventing_area(ab):
    ops = [{"s": [1.0, 2.0], "z": [0.0, 2.0], "depth": 0.2, "fill": "void"},
           {"s": [4.0, 5.5], "z": [1.0, 2.5], "depth": 0.1, "fill": "window"}]
    _, info = ab.build(_spec(faces={"default": {}, "0": {"openings": ops}}), _row(), META)
    L = info["faces"][0]["len_m"]
    total_default_faces = sum(f["len_m"] * (7.5 + 0.6) for f in info["faces"])
    holes = 1.0 * 2.0 + 1.5 * 1.5
    assert info["stats"]["wall_area"] == pytest.approx(total_default_faces - holes, abs=1e-6)
    assert L == pytest.approx(6.0, abs=1e-3) or L == pytest.approx(8.0, abs=1e-3)


def test_overlapping_or_outside_openings_are_refused(ab):
    a = {"s": [1.0, 2.0], "z": [0.5, 2.0], "depth": 0.1, "fill": "window"}
    b = {"s": [1.5, 2.5], "z": [1.0, 2.5], "depth": 0.1, "fill": "window"}
    with pytest.raises(SystemExit):
        ab.build(_spec(faces={"0": {"openings": [a, b]}}), _row(), META)
    out = {"s": [0.5, 20.0], "z": [0.5, 2.0], "depth": 0.1, "fill": "window"}
    with pytest.raises(SystemExit):
        ab.build(_spec(faces={"0": {"openings": [out]}}), _row(), META)


def test_a_height_that_disagrees_with_the_storeys_is_refused(ab):
    with pytest.raises(SystemExit):
        ab.build(_spec(height_m=12.0), _row(), META)


def test_frames_and_glass_are_presentation_and_the_recess_back_plane_collides(ab):
    ops = [{"s": [1.0, 2.2], "z": [0.5, 2.2], "depth": 0.4, "fill": "window", "cols": 2}]
    mesh, _ = ab.build(_spec(faces={"0": {"openings": ops}}), _row(), META)
    alphas = {v[9] for v in mesh.v}
    assert alphas == {0, 255}
    n0 = sum(1 for f in mesh.f if mesh.v[f[0]][9] == 0)
    mesh2, _ = ab.build(_spec(), _row(), META)
    assert n0 > sum(1 for f in mesh2.f if mesh2.v[f[0]][9] == 0)    # reveals + back plane add colliders


@pytest.mark.parametrize("typ,extra", [("gable", {"ridge_m": 2.5, "overhang_m": 0.3}), ("hip", {"ridge_m": 2.5}),
                                       ("shed", {"ridge_m": 1.5, "high_edge": 0})])
def test_pitched_roofs_reach_the_ridge_height(ab, typ, extra):
    roof = {"type": typ, **extra}
    spec = _spec(roof=roof, storey_heights_m=[4.0, 3.0], height_m=7.0 + extra["ridge_m"])
    mesh, info = ab.build(spec, _row(), META)
    top = max(v[2] for v in mesh.v) / CS
    assert top == pytest.approx(7.0 + extra["ridge_m"], abs=0.35)
    assert info["stats"]["roof_pieces"] >= 1


def test_balconies_stairs_awnings_and_boxes_add_presentation_geometry_only(ab):
    plain, _ = ab.build(_spec(), _row(), META)
    fs = {"awnings": [{"s": [0.5, 3.0], "z": 2.6, "depth": 0.8, "color": [90, 60, 40]}],
          "balconies": [{"s": [3.5, 6.0], "z": 4.0, "depth": 0.8, "rail_h": 1.0}],
          "stairs": [{"s0": 6.5, "width": 0.9, "z": [0.0, 3.0], "dir": -1}],
          "boxes": [{"s": [1.0, 2.0], "z": [4.5, 6.5], "depth": 0.5, "color": [180, 170, 160], "glass": [80, 90, 100]}],
          "decals": [{"s": [1.0, 2.0], "z": [1.0, 2.0], "color": [250, 250, 250]}]}
    rich, info = ab.build(_spec(faces={"0": fs}), _row(), META)
    assert len(rich.f) > len(plain.f) + 40 and info["stats"]["bars"] > 5
    pcol = sum(1 for f in plain.f if plain.v[f[0]][9] == 0)
    rcol = sum(1 for f in rich.f if rich.v[f[0]][9] == 0)
    assert rcol == pcol                                           # nothing added collides


def test_a_decal_lift_tilts_its_normals_toward_up_and_the_default_is_unchanged(ab):
    """FL-4876 buildings wave 3: decal "lift" (default 0) gives a repainted wall part the shading of the wall; without it the mesh is as before."""
    base, _ = ab.build(_spec(faces={"0": {"decals": [{"s": [1.0, 2.0], "z": [1.0, 2.0], "color": [250, 250, 250]}]}}), _row(), META)
    same, _ = ab.build(_spec(faces={"0": {"decals": [{"s": [1.0, 2.0], "z": [1.0, 2.0], "color": [250, 250, 250], "lift": 0.0}]}}), _row(), META)
    lifted, _ = ab.build(_spec(faces={"0": {"decals": [{"s": [1.0, 2.0], "z": [1.0, 2.0], "color": [250, 250, 250], "lift": 0.75}]}}), _row(), META)
    assert base.v == same.v and base.f == same.f
    assert len(lifted.v) == len(base.v)
    nz0 = [v[5] for v in base.v if v[6:9] == (250, 250, 250)]
    nz1 = [v[5] for v in lifted.v if v[6:9] == (250, 250, 250)]
    assert nz0 and nz1 and max(abs(z) for z in nz0) < 1e-6 and min(nz1) > 0.5


def test_triangulation_of_a_concave_footprint_covers_its_area(ab):
    poly = [(0, 0), (4, 0), (4, 4), (2, 2), (0, 4)]               # an arrow-notched concave polygon
    tris = ab.triangulate(poly)
    area = sum(abs((poly[b][0] - poly[a][0]) * (poly[c][1] - poly[a][1]) - (poly[b][1] - poly[a][1]) * (poly[c][0] - poly[a][0])) / 2
               for a, b, c in tris)
    assert area == pytest.approx(abs(ab.signed_area(poly)), abs=1e-9)


# ---------------------------------------------------------------------------------------------------------------
# plan-fitted pitched roofs (roof.fit = "plan" / roof.parts), FL-4876 reroof 2026-10-17
# ---------------------------------------------------------------------------------------------------------------
import hashlib  # noqa: E402
import json  # noqa: E402

L_PLAN = [(-6, -5), (-6, 5), (-1, 5), (-1, 0), (6, 0), (6, -5)]                 # 12 x 10 with a 5 x 5 notch
T_PLAN = [(-7, 0), (-7, 4), (7, 4), (7, 0), (2, 0), (2, -6), (-2, -6), (-2, 0)]
U_PLAN = [(-7, -4), (-7, 5), (-3, 5), (-3, 0), (3, 0), (3, 5), (7, 5), (7, -4)]
STEP_PLAN = [(-8, -4), (-8, 4), (-2, 4), (-2, 1), (8, 1), (8, -4)]
TRAPEZOID = [(-6, -4), (-4, 4), (5, 4), (7, -4)]
ARROW = [(-6, -4), (-6, 4), (0, 1), (6, 4), (6, -4), (0, -1.5)]                  # neither convex nor rectilinear
ROOF_RGB = (96, 96, 100)
OV = 0.4


def _prow(pts, h=9.0):
    return {"feature_id": "feature:building_footprint:way/1", "roof_height_m": h,
            "bake_footprint": [[500.0 + x * CS, 500.0 + y * CS] for x, y in pts]}


def _roof_spec(typ="hip", **roof):
    r = {"type": typ, "fit": "plan", "ridge_m": 2.0, "overhang_m": OV, "color": list(ROOF_RGB)}
    r.update(roof)
    return _spec(roof=r, storey_heights_m=[4.0, 3.0], height_m=9.0)


def _roof_faces(mesh):
    """Roof faces as lists of (e, n, z) in metres."""
    return [[tuple(c / CS for c in mesh.v[i][:3]) for i in f] for f in mesh.f if tuple(mesh.v[f[0]][6:9]) == ROOF_RGB]


def _in_tri(p, a, b, c):
    def cr(o, q, r):
        return (q[0] - o[0]) * (r[1] - o[1]) - (q[1] - o[1]) * (r[0] - o[0])
    d = [cr(a, b, p), cr(b, c, p), cr(c, a, p)]
    return all(x >= 0 for x in d) or all(x <= 0 for x in d)


def _covered(faces, p):
    for f in faces:
        for k in range(1, len(f) - 1):
            if _in_tri(p, f[0], f[k], f[k + 1]):
                return True
    return False


def _inside(ab, p, poly):
    return ab._in_poly(p, poly)


@pytest.mark.parametrize("typ", ["hip", "gable"])
@pytest.mark.parametrize("name,pts,notch", [("L", L_PLAN, (3.0, 2.5)), ("T", T_PLAN, (-5.0, -3.0)), ("U", U_PLAN, (0.0, 2.5)),
                                            ("step", STEP_PLAN, (3.0, 2.5))])
def test_plan_roof_stays_inside_the_footprint_plus_eaves_and_leaves_notches_open(ab, typ, name, pts, notch):
    mesh, info = ab.build(_roof_spec(typ), _prow(pts), META)
    st = info["stats"]
    assert st["roof_fit"] == "plan" and "roof_fallback" not in st
    assert st["roof_audit"]["uncovered_m2"] == 0.0 and st["roof_audit"]["overhang_over_notch_m2"] == 0.0
    poly = info["polygon_m"]
    faces = _roof_faces(mesh)
    assert faces
    for f in faces:
        for e, n, z in f:
            assert _inside(ab, (e, n), poly) or ab._dist_poly((e, n), poly) <= OV * math.sqrt(2) + 0.3
            assert 7.0 - OV * 2 <= z <= 9.0 + 1e-6                     # eave level (minus the overhang drop) up to the ridge
    # the notch centre (translate: the build re-centres the footprint on its vertex mean)
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    np_ = (notch[0] - cx, notch[1] - cy)
    assert not _inside(ab, np_, poly)
    assert not _covered(faces, np_)                                     # no roof face over the empty notch
    for e, n in ((poly[0][0], poly[0][1]), ((poly[0][0] + poly[1][0]) / 2, (poly[0][1] + poly[1][1]) / 2)):
        assert _covered(faces, (e, n))                                  # a footprint boundary point is under the roof


@pytest.mark.parametrize("typ", ["hip", "gable"])
def test_plan_roof_pieces_tile_the_roof_outline_without_overlap_or_gap(ab, typ):
    for pts in (L_PLAN, T_PLAN, U_PLAN, STEP_PLAN):
        poly = [(float(x), float(y)) for x, y in pts]
        plan = ab.plan_roof(poly, {"type": typ, "ridge_m": 2.0, "overhang_m": OV}, typ)
        assert not plan.get("fallback")
        total = sum(ab._parea(p["poly"]) for p in plan["pieces"])
        # union of the wing outlines, sampled every 5 cm
        step, xs, ys, area = 0.05, [p[0] for p in plan["polygon_frame"]], [p[1] for p in plan["polygon_frame"]], 0.0
        for i in range(int((max(xs) - min(xs) + 3) / step)):
            for j in range(int((max(ys) - min(ys) + 3) / step)):
                x, y = min(xs) - 1.5 + (i + 0.5) * step, min(ys) - 1.5 + (j + 0.5) * step
                if any(ab._in_convex(w["domain"], (x, y)) for w in plan["wings"]):
                    area += step * step
        assert total == pytest.approx(area, rel=0.01)


def test_hip_wings_of_an_l_plan_meet_in_a_valley_and_the_ridges_meet(ab):
    mesh, info = ab.build(_roof_spec("hip"), _prow(L_PLAN), META)
    plan = ab.plan_roof([(float(x), float(y)) for x, y in L_PLAN], {"type": "hip", "ridge_m": 2.0, "overhang_m": OV}, "hip")
    assert len(plan["wings"]) == 2 and info["stats"]["roof_wings"] == 2
    top = max(v[2] for v in mesh.v) / CS
    assert top == pytest.approx(9.0, abs=1e-3)                          # ridge = eave 7.0 + ridge_m 2.0
    ridge = {(round(v[0] / CS, 3), round(v[1] / CS, 3)) for v in mesh.v if abs(v[2] / CS - 9.0) < 1e-3}
    cx = sum(p[0] for p in L_PLAN) / len(L_PLAN)
    cy = sum(p[1] for p in L_PLAN) / len(L_PLAN)
    col_x, bar_y = -3.5 - cx, -2.5 - cy                                 # ridge line of the 5 x 10 column and of the 12 x 5 bar
    for e, n in ridge:                                                  # every peak vertex lies on one of the two ridge lines
        assert abs(e - col_x) < 2e-3 or abs(n - bar_y) < 2e-3
    for want in ((col_x, bar_y), (col_x, 2.5 - cy), (3.5 - cx, bar_y)):  # the junction and the two ridge ends are all there
        assert any(abs(e - want[0]) < 2e-3 and abs(n - want[1]) < 2e-3 for e, n in ridge)
    zs = [z for f in _roof_faces(mesh) for _, _, z in f]
    assert min(zs) == pytest.approx(7.0 - OV * 2.0 / 2.5, abs=1e-2)    # eave 7.0 minus overhang * slope (2.0 m over 2.5 m)


def test_a_ridge_end_lies_on_the_other_wings_ridge_line(ab):
    poly = [(float(x), float(y)) for x, y in L_PLAN]
    plan = ab.plan_roof(poly, {"type": "hip", "ridge_m": 2.0, "overhang_m": OV}, "hip")
    fr, s = plan["frame"], plan["s"]
    pts = {(round(x, 3), round(y, 3)) for pc in plan["pieces"] for x, y in pc["poly"]
           if abs(s * (pc["plane"][0] * x + pc["plane"][1] * y + pc["plane"][2]) - plan["ridge_m"]) < 1e-3}
    # the two ridge lines are y = 2.5 (wing 12 x 5) and x = 3.5 (wing 5 x 10) in the plan frame; they cross at one vertex
    assert len(pts) >= 3
    xs = sorted({p[0] for p in pts})
    ys = sorted({p[1] for p in pts})
    # every ridge vertex lies on one of the two centre lines, and one vertex is on both
    wings = sorted(plan["wings"], key=lambda w: -w["peak"])
    lines = []
    for w in wings:
        x0, x1, y0, y1 = w["rect"]
        lines.append(("y", (y0 + y1) / 2) if w["axis"] == "x" else ("x", (x0 + x1) / 2))
    for p in pts:
        assert any(abs((p[1] if ax == "y" else p[0]) - c) < 1e-3 for ax, c in lines)
    both = [p for p in pts if all(abs((p[1] if ax == "y" else p[0]) - c) < 1e-3 for ax, c in lines)]
    assert len(both) == 1 and xs and ys


def test_trapezoid_gets_one_straight_skeleton_hip_and_stays_inside(ab):
    mesh, info = ab.build(_roof_spec("hip"), _prow(TRAPEZOID), META)
    st = info["stats"]
    assert st["roof_fit"] == "plan" and st["roof_wings"] == 1
    assert st["roof_audit"]["uncovered_m2"] == 0.0
    top = max(v[2] for v in mesh.v) / CS
    assert top == pytest.approx(9.0, abs=1e-2)


def test_pitch_deg_sets_the_ridge_height_from_the_widest_wing(ab):
    plan = ab.plan_roof([(float(x), float(y)) for x, y in L_PLAN], {"type": "hip", "pitch_deg": 30.0, "overhang_m": OV}, "hip")
    assert plan["ridge_m"] == pytest.approx(math.tan(math.radians(30.0)) * 2.5, abs=1e-6)
    spec = _roof_spec("hip", ridge_m=None, pitch_deg=30.0)
    spec["height_m"] = 7.0 + math.tan(math.radians(30.0)) * 2.5
    _, info = ab.build(spec, _prow(L_PLAN), META)
    assert info["stats"]["roof_pitch_deg"] == pytest.approx(30.0, abs=0.1)


def test_roof_parts_are_honoured_and_a_gable_infills_its_end_wall(ab):
    parts = [{"rect": [-6.0, 6.0, 0.0, 5.0], "type": "gable", "ridge": "x"}]
    mesh, info = ab.build(_roof_spec("gable", parts=parts), _prow(L_PLAN), META)
    assert info["stats"]["roof_wings"] == 1
    walls_above_eave = [f for f in mesh.f if mesh.v[f[0]][9] == 0 and tuple(mesh.v[f[0]][6:9]) == (200, 190, 170)
                        and max(mesh.v[i][2] for i in f) / CS > 7.1]
    assert walls_above_eave                                              # gable triangles close the end walls


def test_a_plan_that_cannot_be_split_falls_back_to_the_bounding_box_roof_and_says_so(ab, capsys):
    plain = _spec(roof={"type": "hip", "ridge_m": 2.0, "overhang_m": OV, "color": list(ROOF_RGB)}, height_m=9.0)
    old, _ = ab.build(plain, _prow(ARROW), META)
    new, info = ab.build(_roof_spec("hip"), _prow(ARROW), META)
    assert "roof_fallback" in info["stats"] and "ROOF FALLBACK" in capsys.readouterr().err
    assert ab.akm_text(new, "t") == ab.akm_text(old, "t")
    mesh2, info2 = ab.build(_roof_spec("shed", high_edge=0), _prow(L_PLAN), META)
    assert "roof_fallback" in info2["stats"]


GOLDEN = {   # sha256 of akm_text(mesh, "t") of specs WITHOUT roof.fit, measured on the tool before the plan roofs existed
    "flat": "a2ed546286ff981900b977de18963da495306f4468b0c1131165173722d48a8c",
    "gable": "27c0640a6d735df0755f11ed3e70f299bcf729c3e3d5ca31e3e822233768fff2",
    "hip": "e2e51ceb622351cc3d8f99dcacb0abcb849f0d2aede6533f2b19443bf1b5ff55",
    "shed": "1d62b3384db85b4ced766c0e18d652cec204396f4d1c357718b4acaceafe587b",
    "hip_L_bbox": "63a2778c64a0b601933b0cd7d3da8434c6ad70902150823839f5e6cbfaa2694a",
    "flat_L": "5193ba60642ed15fa450a394b25eb7537133040abe4578917c9cf9fb8531c1f7",
    "gable_ridge_short": "d6ed403b6663a81846e31a9454e8577a96ae44bd8f008c3fd8cf4f8930b47c69",
}


def _golden_cases():
    return {
        "flat": (_spec(), _row()),
        "gable": (_spec(roof={"type": "gable", "ridge_m": 2.5, "overhang_m": 0.3}, height_m=9.5), _row()),
        "hip": (_spec(roof={"type": "hip", "ridge_m": 2.5}, height_m=9.5), _row()),
        "shed": (_spec(roof={"type": "shed", "ridge_m": 1.5, "high_edge": 0}, height_m=8.5), _row()),
        "hip_L_bbox": (_spec(roof={"type": "hip", "ridge_m": 2.0}, height_m=9.0), _prow(L_PLAN)),
        "flat_L": (_spec(), _prow(L_PLAN)),
        "gable_ridge_short": (_spec(roof={"type": "gable", "ridge_m": 2.0, "ridge": "short"}, height_m=9.0), _row(10, 7)),
    }


@pytest.mark.parametrize("key", sorted(GOLDEN))
def test_existing_specs_still_build_byte_identical_meshes(ab, key):
    spec, row = _golden_cases()[key]
    mesh, _ = ab.build(spec, row, META)
    assert hashlib.sha256(ab.akm_text(mesh, "t").encode()).hexdigest() == GOLDEN[key]


PILOT_SHA = {    # sha256 of akm_text of each pilot spec, built by the tool before the plan roofs existed (2026-10-17 18:50)
    "225369226": "5a8cc705887b5c75753c17e8d369ef8f5d8285b589f81fdc145f067d98547996",
    "225369227": "2a92ad2c69c1c1bc13e217bc7aa5d8ac23a5fc6572fad39f9262de5257ac5baf",
    "225369228": "88afe5463c231500b5a8255842dc385a14d30c86b5a2893bf57fdee92844d43e",
    "225369231": "879daf7409186dbeee182a7c2f70bf296ebe613c86c835af9f1a889e0224e38f",
    "225369237": "c1eb51ae19256b381b6b4fc3159183b7d34a996fb9fa98c89fc626542c057df0",
}


def test_the_pilot_specs_build_the_same_bytes_as_before_the_plan_roofs(ab):
    pilot = ROOT / "artifacts/experiments/fl4876_manual_world/assess/live_fix_20261017/buildings_pilot/specs"
    build = ROOT / ".run/fl4876_builds/live_numabe"
    if not pilot.is_dir() or not (build / "building_instances.json").is_file():
        pytest.skip("live build files are not in this checkout")
    meta = ab.projection()
    for way, sha in PILOT_SHA.items():
        spec = json.loads((pilot / f"{way}.json").read_text())
        mesh, _ = ab.build(spec, ab.load_footprint(spec), meta)
        assert hashlib.sha256(ab.akm_text(mesh, "t").encode()).hexdigest() == sha, way


def test_akm_text_header_matches_the_body(ab):
    mesh, _ = ab.build(_spec(), _row(), META)
    text = ab.akm_text(mesh, "t").splitlines()
    nv = int(text[3].split()[-1])
    nf = int(text[text.index("end_header") - 2].split()[-1])
    assert nv == len(mesh.v) and nf == len(mesh.f)
    assert len(text) == text.index("end_header") + 1 + nv + nf
