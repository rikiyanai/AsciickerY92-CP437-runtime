#!/usr/bin/env python3
"""FL-4876 DENSITY-SYNTH-01. Operator-authorized surrounding synthesis.

The northern route has one panorama total, so poles, lamps and trees can
never be observed there. The operator authorized pattern synthesis from
surrounding spacing, OSM road geometry and point-cloud premise validation.
These tests pin the stationing geometry: chainage math, side alternation,
route scoping, and the observed-streetscape exclusion that keeps evidenced
carriers pure.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.compile_population_plan import (  # noqa: E402
    SYNTH_OBSERVED_CLEAR_M,
    SYNTH_ROUTE_OFFSET_M,
    chainage_point,
    select_synth_stations,
)


def test_chainage_point_walks_segments():
    poly = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    e, n, b = chainage_point(poly, 5.0)
    assert (e, n) == (5.0, 0.0) and b == 0.0
    e, n, b = chainage_point(poly, 15.0)
    assert (e, n) == (10.0, 5.0) and b == 90.0
    assert chainage_point([(1.0, 2.0)], 0.0) is None


def test_stations_space_and_alternate_sides():
    carriers = {"c1": {"polyline": [(0.0, 0.0), (100.0, 0.0)]}}
    route = [(float(x), 0.0) for x in range(0, 101, 10)]
    out = select_synth_stations(carriers, route, [], 30.0)
    assert [round(o[1], 1) for o in out] == [0.0, 30.0, 60.0, 90.0]
    assert [o[4] for o in out] == ["L", "R", "L", "R"]
    assert all(o[0] == "c1" for o in out)


def test_stations_skip_observed_streetscape():
    carriers = {"c1": {"polyline": [(0.0, 0.0), (100.0, 0.0)]}}
    route = [(float(x), 0.0) for x in range(0, 101, 10)]
    out = select_synth_stations(carriers, route, [(30.0, 0.0)], 30.0)
    assert [round(o[1], 1) for o in out] == [0.0, 60.0, 90.0]


def test_far_carriers_and_short_carriers_excluded():
    carriers = {
        "far": {"polyline": [(0.0, 500.0), (100.0, 500.0)]},
        "short": {"polyline": [(0.0, 0.0), (10.0, 0.0)]},
        "near": {"polyline": [(0.0, 5.0), (100.0, 5.0)]},
    }
    route = [(float(x), 0.0) for x in range(0, 101, 10)]
    out = select_synth_stations(carriers, route, [], 30.0)
    assert out and all(o[0] == "near" for o in out)


def test_station_offsets_respect_maximum():
    carriers = {"c1": {"polyline": [(0.0, 0.0), (200.0, 0.0)]}}
    route = [(float(x), 0.0) for x in range(0, 51, 10)]
    out = select_synth_stations(carriers, route, [], 30.0,
                                max_offset_m=SYNTH_ROUTE_OFFSET_M)
    assert [round(o[1], 1) for o in out] == [0.0, 30.0, 60.0]


def test_clear_radius_uses_module_constant():
    assert SYNTH_OBSERVED_CLEAR_M == 25.0
