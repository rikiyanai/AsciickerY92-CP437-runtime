"""FL-4934: measured road corridor producer and its surface-plan consumer.

Scope: the obstacle band, wall-to-wall ray measurement with per-side values,
footprint obstacles, eligibility and precedence, and the widen-only union.
Synthetic geometry only; nothing here measures a real road.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "pipeline"))

np = pytest.importorskip("numpy")
from shapely.geometry import LineString, Polygon  # noqa: E402

import compile_lidar_road_corridor as P  # noqa: E402
from scripts.pipeline import compile_surface_stamp_plan as S  # noqa: E402


def _street(wall_left_x=-3.0, wall_right_x=2.0, extra=()):
    """Flat ground (class 2) every 0.25 m plus 1.5 m walls at two x positions."""
    gx, gy = np.meshgrid(np.arange(-10, 10, 0.25), np.arange(0, 60, 0.25))
    X, Y = gx.ravel(), gy.ravel()
    Z = np.zeros_like(X); C = np.full(X.shape, 2)
    wy = np.arange(0, 60, 0.25)
    walls_x = np.concatenate([np.full(wy.shape, wall_left_x), np.full(wy.shape, wall_right_x)])
    walls_y = np.concatenate([wy, wy])
    X = np.concatenate([X, walls_x]); Y = np.concatenate([Y, walls_y])
    Z = np.concatenate([Z, np.full(walls_x.shape, 1.5)]); C = np.concatenate([C, np.ones(walls_x.shape, int)])
    for x, y, z in extra:
        X = np.append(X, x); Y = np.append(Y, y); Z = np.append(Z, z); C = np.append(C, 1)
    return X, Y, Z, C


def _measure(X, Y, Z, C):
    obstacle, origin = P.obstacle_grid(X, Y, Z, C, (-10, 0, 10, 60))
    return P.corridor_stations(LineString([(0, 0), (0, 60)]), obstacle, origin)


def test_walls_bound_each_side_and_left_is_the_positive_normal():
    sides = _measure(*_street())
    assert len(sides) >= 10
    lefts = [l for _, l, _ in sides]; rights = [r for _, _, r in sides]
    # line runs +y, so +normal (left) is -x: wall at -3 m; right wall at +2 m
    assert 2.25 <= np.median(lefts) <= 3.0
    assert 1.25 <= np.median(rights) <= 2.0


def test_overhead_wire_above_the_band_does_not_close_the_corridor():
    wire = [(x, 30.0, 6.0) for x in np.arange(-2.5, 1.75, 0.25)]
    with_wire = _measure(*_street(extra=wire))
    plain = _measure(*_street())
    assert [round(l + r, 2) for _, l, r in with_wire] == [round(l + r, 2) for _, l, r in plain]


def test_obstacle_in_band_on_the_centreline_skips_the_station():
    car = [(0.0, 30.0, 1.2), (0.1, 30.1, 1.2)]
    stations = {round(s) for s, _, _ in _measure(*_street(extra=car))}
    assert 30 not in stations


# --- consumer -----------------------------------------------------------------

RULES = {"road_class_defaults": {"carriageway_width_m_by_highway": {
    k: {"value": v, "source": "DEFAULT:test"} for k, v in
    {"primary": 11.0, "tertiary": 7.0, "residential": 4.5, "service": 3.0, "unclassified": 4.0}.items()}}}


def _corridor(left, right, stations=None):
    stations = stations or [[s, left, right] for s in range(2, 58, 4)]
    return {"feature_id": "feature:road:way/1", "stations": len(stations), "corridor_median_m": left + right,
            "left_median_m": left, "right_median_m": right, "stations_s_left_right_m": stations}


FEATURE = {"feature_id": "feature:road:way/1", "semantic_class": "road",
           "geometry": {"kind": "polyline", "coordinates": [[0, 0], [0, 60]]}}


def test_eligible_side_road_uses_the_measured_corridor():
    role, width, src = S._surface_role_width(FEATURE, {"highway": "residential"}, RULES,
                                             {"feature:road:way/1": _corridor(3.0, 2.0)})
    assert (role, width) == ("road_residential", 5.0) and "TMG 2023" in src


@pytest.mark.parametrize("tags", [{"highway": "primary"}, {"highway": "trunk"}, {"highway": "secondary"},
                                  {"highway": "residential", "width": "3.5"}])
def test_primary_class_and_tagged_width_ignore_the_corridor(tags):
    _, width, src = S._surface_role_width(FEATURE, tags, RULES, {"feature:road:way/1": _corridor(3.0, 2.0)})
    assert "TMG" not in src
    assert width in (11.0, 3.5)


def test_union_never_loses_default_paint_and_adds_the_measured_side():
    ring = S._road_corridor_polygon([[0, 0], [0, 60]], _corridor(4.0, 1.0), 4.5)
    painted = Polygon(ring)
    default = LineString([(0, 0), (0, 60)]).buffer(2.25, cap_style="flat")
    assert default.difference(painted).area < 1e-6
    assert painted.area > default.area                      # left side widened to 4 m
    assert painted.bounds[0] == pytest.approx(-4.0, abs=1e-6)


def test_local_narrowing_cannot_cut_below_the_default():
    stations = [[s, 0.5, 0.5] for s in range(2, 58, 4)]   # a car at every station
    ring = S._road_corridor_polygon([[0, 0], [0, 60]], _corridor(0.5, 0.5, stations), 4.5)
    painted = Polygon(ring)
    default = LineString([(0, 0), (0, 60)]).buffer(2.25, cap_style="flat")
    assert painted.symmetric_difference(default).area < 1e-6


def test_corridor_document_refusals(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"schema": "other", "tile_sha256": ["x"], "rows": []}))
    with pytest.raises(ValueError):
        S.load_road_corridors(p)
    p.write_text(json.dumps({"schema": S.ROAD_CORRIDOR_SCHEMA, "tile_sha256": [], "rows": []}))
    with pytest.raises(ValueError):
        S.load_road_corridors(p)
    assert S.load_road_corridors(None) == {}
