#!/usr/bin/env python3
"""FL-4876 SIGN-PLANE-01. Logos become flat wall meshes, not camera-facing sprites.

Pins: the logo is not mirrored (cell column 0 is the viewer's left), equal colours merge into one quad per run,
the transparency key leaves a hole, every quad is two-sided, and the transform's local y is the outward normal.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.compile_sign_planes import KEY, plane_akm, plane_transform  # noqa: E402

R, G, B = (255, 0, 0), (0, 255, 0), (0, 0, 255)


def _parse(text):
    lines = text.splitlines()
    nv = int(next(l for l in lines if l.startswith("element vertex")).split()[-1])
    start = lines.index("end_header") + 1
    verts = [tuple(float(x) for x in l.split()) for l in lines[start:start + nv]]
    faces = [tuple(int(x) for x in l.split()[1:]) for l in lines[start + nv:]]
    return verts, faces


def test_runs_merge_and_key_is_a_hole():
    cells = [[(223, R, B), (223, R, B), (223, KEY, B), (223, G, B)]]  # 1 row, 4 columns
    verts, faces = _parse(plane_akm(cells, 4.0, 2.0, "t"))
    # upper half: R run (cols 0-1), hole, G (col 3) -> 2 quads; lower half: B run of 4 -> 1 quad; x2 sides x2 tris
    assert len(faces) == (2 + 1) * 2 * 2
    red = [v for v in verts if v[6:9] == R]
    assert min(v[0] for v in red) == -2.0 and max(v[0] for v in red) == 0.0  # columns 0-1: the viewer's left half
    assert not [v for v in verts if v[6:9] == KEY]
    assert {v[4] for v in verts} == {1.0, -1.0}  # front (+y, outward) and back faces


def test_upper_half_is_fg_lower_half_is_bg():
    verts, _ = _parse(plane_akm([[(223, R, G)]], 1.0, 2.0, "t"))
    assert min(v[2] for v in verts if v[6:9] == R) == 1.0  # fg on the top half
    assert max(v[2] for v in verts if v[6:9] == G) == 1.0  # bg on the bottom half


def test_xp_round_trip(tmp_path):
    from scripts.pipeline.compile_sign_planes import xp_visual
    from scripts.pipeline.compile_sign_sprites import xp_bytes
    cells = [[(223, R, G), (223, B, R), (223, G, B)], [(223, G, G), (223, R, R), (223, B, B)]]
    p = tmp_path / "s.xp"
    p.write_bytes(xp_bytes(cells))
    assert xp_visual(p) == cells


def test_transform_local_y_is_the_outward_normal_and_x_the_viewers_right():
    t = plane_transform(10.0, 20.0, 5.0, 0.0, -1.0, 16.0)  # wall faces south (normal -N)
    assert t[4:6] == [0.0, -1.0]
    assert t[0:2] == [1.0, 0.0]  # facing north at a south wall, the viewer's right is east
    assert t[10] == 16.0 and t[12:15] == [10.0, 20.0, 5.0]
