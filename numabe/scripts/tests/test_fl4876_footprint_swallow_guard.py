#!/usr/bin/env python3
"""FL-4876 FOOTPRINT-GUARD-01. A rewrite may not grow a building over an
observed street object.

Of 17 placements measured 2 m or deeper inside a building on build_e2e_09, 11
were not placement errors. The wall was correctly outside the building as
mapped, and the PLATEAU footprint rewrite came out over it. The barrier was
never at fault; the building moved underneath it.

The added_plateau_only branch has carried an overlap guard all along, firing
71 times on this lane. The rewritten_to_plateau branch had none.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.compile_canonical_footprints import (  # noqa: E402
    _newly_enclosed,
    _observed_points,
)

SOURCE = ROOT / "scripts" / "pipeline" / "compile_canonical_footprints.py"

# A 10x10 m building. The rewrite grows it 4 m east.
OLD_RING = [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0], [0.0, 0.0]]
NEW_RING = [[0.0, 0.0], [14.0, 0.0], [14.0, 10.0], [0.0, 10.0], [0.0, 0.0]]

WALL_OUTSIDE_OLD = ("feature:mapillary_object/1", "fixture", 12.0, 5.0)
SIGN_INSIDE_BOTH = ("feature:mapillary_object/2", "sign", 5.0, 5.0)
POLE_CLEAR = ("feature:mapillary_object/3", "utility_pole", 40.0, 40.0)


def test_a_rewrite_that_grows_over_an_observed_object_is_caught():
    got = _newly_enclosed(Polygon(NEW_RING), OLD_RING, [WALL_OUTSIDE_OLD])
    assert len(got) == 1
    assert got[0]["feature_id"] == "feature:mapillary_object/1"
    assert got[0]["semantic_class"] == "fixture"


def test_an_object_already_inside_the_old_ring_is_not_this_guards_business():
    """Source overlap. The rewrite did not cause it, so the guard leaves it.

    97 observations on this lane are in exactly this position. A guard that
    claimed them would refuse rewrites for damage it did not cause.
    """
    assert _newly_enclosed(Polygon(NEW_RING), OLD_RING, [SIGN_INSIDE_BOTH]) == []


def test_an_object_outside_both_rings_is_untouched():
    assert _newly_enclosed(Polygon(NEW_RING), OLD_RING, [POLE_CLEAR]) == []


def test_a_rewrite_that_shrinks_away_from_an_object_is_allowed():
    """The rewrite fixed 23 penetrations on this lane. Do not refuse those."""
    assert _newly_enclosed(Polygon(OLD_RING), NEW_RING, [WALL_OUTSIDE_OLD]) == []


def test_the_guard_reports_every_swallowed_object_not_just_the_first():
    observed = [WALL_OUTSIDE_OLD, SIGN_INSIDE_BOTH, POLE_CLEAR,
                ("feature:mapillary_object/4", "sign", 13.0, 2.0)]
    got = _newly_enclosed(Polygon(NEW_RING), OLD_RING, observed)
    assert {g["feature_id"] for g in got} == {
        "feature:mapillary_object/1", "feature:mapillary_object/4"}


def test_no_observations_means_the_guard_cannot_fire():
    assert _newly_enclosed(Polygon(NEW_RING), OLD_RING, []) == []


def test_a_missing_observation_document_yields_no_points():
    """Absent input must not be mistaken for a clean result.

    The receipt carries swallow_guard_ran so an empty list is distinguishable
    from a guard that passed.
    """
    assert _observed_points(None) == []
    source = SOURCE.read_text(encoding="utf-8")
    assert '"swallow_guard_ran": bool(observed_points),' in source


def test_the_rewrite_branch_actually_calls_the_guard():
    """Falsifier: remove the call and this fails.

    The helper existing is not the same as the branch using it -- that is the
    defect class this whole lane keeps producing.
    """
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines)
                 if 'counts["kept_osm_area_band"] += 1' in ln)
    end = next(i for i, ln in enumerate(lines[start:], start)
               if 'counts["rewritten_to_plateau"] += 1' in ln)
    branch = "\n".join(lines[start:end])
    assert "_newly_enclosed(" in branch, branch
    assert 'counts["kept_osm_swallows_observation"] += 1' in branch
    assert "continue" in branch


def test_the_guard_keeps_the_osm_ring_rather_than_dropping_the_building():
    """A refused rewrite keeps a building. It never deletes one."""
    source = SOURCE.read_text(encoding="utf-8")
    start = source.index('counts["kept_osm_swallows_observation"] += 1')
    block = source[start:start + 1400]
    assert '"footprint_source"' in block
    assert '"osm"' in block
    assert '"swallows_observation"' in block


def test_the_receipt_exposes_the_refusal():
    source = SOURCE.read_text(encoding="utf-8")
    assert '"kept_osm_swallows_observation":' in source
    assert '"swallow_reports": swallow_reports,' in source


@pytest.mark.parametrize("band", ["AREA_BAND_LO = 0.5", "AREA_BAND_HI = 2.0",
                                  "OVERLAP_FRACTION = 0.10"])
def test_no_existing_tolerance_was_touched(band):
    assert band in SOURCE.read_text(encoding="utf-8")
