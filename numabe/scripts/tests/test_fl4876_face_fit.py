#!/usr/bin/env python3
"""FL-4876 FACE-FIT-01. A wall panel stays inside the face it hangs on.

build_reg_75: roller_shutter_facade and rooftop_safety_railing are always 8 m wide and hung up to 4 m past the
corners of 3-7 m faces. resolve() now returns the (outset) face end points with a building_face pose, and the
fixture lowering cuts the panel to the face length and slides it along the same wall until it fits. The lowering
step is checked here as the same arithmetic it uses; the build-level check is facade_fit_list.py (overhang).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.canonical_attachment import resolve  # noqa: E402
from scripts.pipeline.lower_plan_fixtures import FACE_FIT_MAX_SLIDE_M  # noqa: E402

RING = [[0.0, 0.0], [3.0, 0.0], [3.0, 10.0], [0.0, 10.0], [0.0, 0.0]]  # 3 m x 10 m building


def _pose(face, frac, off=0.15):
    index = SimpleNamespace(buildings=[("b", RING)])
    return resolve({"anchor_kind": "building_face", "canonical_building_id": "b", "face_index": face,
                    "along_face_fraction": frac, "outward_offset_m": off}, index)


def _fit(pose, planned):
    """Same arithmetic as lower_plan_fixtures FACE-FIT-01."""
    (ax, ay), (bx, by) = pose["face_end_a"], pose["face_end_b"]
    fl = math.hypot(bx - ax, by - ay)
    ux, uy = (bx - ax) / fl, (by - ay) / fl
    t = min(max((pose["E"] - ax) * ux + (pose["N"] - ay) * uy, 0.0), fl)
    w = min(planned, fl)
    tc = min(max(t, w / 2.0), fl - w / 2.0)
    tc = t + max(-FACE_FIT_MAX_SLIDE_M, min(FACE_FIT_MAX_SLIDE_M, tc - t))
    w = min(w, 2.0 * tc, 2.0 * (fl - tc))
    return w, tc, fl


def test_face_ends_travel_with_the_pose_and_are_outset_like_it():
    p = _pose(0, 0.5)  # face 0 runs (0,0)-(3,0); outward is -N
    assert p["face_end_a"] == [0.0, -0.15] and p["face_end_b"] == [3.0, -0.15]
    assert (p["E"], p["N"]) == (1.5, -0.15)


def test_an_8_m_shutter_on_a_3_m_face_is_cut_to_the_face():
    w, tc, fl = _fit(_pose(0, 0.5), 8.0)
    assert w == 3.0 and tc == 1.5 and fl == 3.0


def test_a_panel_near_a_corner_slides_inside_and_never_grows():
    w, tc, fl = _fit(_pose(1, 0.95), 2.0)   # 10 m face, centre 9.5 m along, 2 m panel
    assert w == 2.0 and tc == 9.0           # slid back 0.5 m so its end meets the corner
    w, tc, _ = _fit(_pose(1, 0.5), 2.0)
    assert w == 2.0 and tc == 5.0           # already inside: unchanged


def test_the_slide_is_capped_and_the_rest_is_trimmed():
    # 8 m shutter centred 0.5 m from the end of a 10 m face: would need a 3.5 m slide
    w, tc, _ = _fit(_pose(1, 0.05), 8.0)
    assert abs(tc - 1.5) < 1e-9             # slid only 1.0 m (coherence tolerance is 1.5 m)
    assert abs(w - 3.0) < 1e-9              # trimmed to what fits around the new centre
