#!/usr/bin/env python3
"""FL-4876 SUBDIVISION-01. Buildings do not interpenetrate.

The OSM document for this AOI has zero overlapping building pairs. Applying
the PLATEAU rewrite produces 25, totalling 423 square metres. Measured per
pair it is not a swapped ring: one building is untouched and its neighbour is
rewritten and grows over it, because PLATEAU models a block as one footprint
where OSM maps adjacent row houses separately.

Neither source is wrong. Applying one source to a building and the other to its
neighbour is what is wrong, and it yields geometry that is true in neither.
"""

from __future__ import annotations

import sys
from pathlib import Path

from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.compile_canonical_footprints import (  # noqa: E402
    OVERLAP_FRACTION,
    _swallows_other_building,
)

SOURCE = ROOT / "scripts" / "pipeline" / "compile_canonical_footprints.py"

SELF_ID = "feature:building_footprint:way/1507403771"
NEIGHBOUR_ID = "feature:building_footprint:way/1507403772"

# A 10x20 m house. Its neighbour is the 10x10 m house next door.
NEIGHBOUR = Polygon([(10, 0), (20, 0), (20, 10), (10, 10)])
# The PLATEAU ring models the whole block, covering both.
BLOCK = Polygon([(0, 0), (20, 0), (20, 10), (0, 10)])
# An honest rewrite of the house alone, touching but not covering.
OWN_ONLY = Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])


def test_a_rewrite_that_covers_the_house_next_door_is_refused():
    got = _swallows_other_building(BLOCK, SELF_ID, [(NEIGHBOUR_ID, NEIGHBOUR)])
    assert got is not None
    assert got["feature_id"] == NEIGHBOUR_ID
    assert got["fraction"] == 1.0


def test_a_rewrite_that_only_abuts_its_neighbour_is_allowed():
    """Shared party walls are normal. Touching is not covering."""
    assert _swallows_other_building(
        OWN_ONLY, SELF_ID, [(NEIGHBOUR_ID, NEIGHBOUR)]) is None


def test_the_building_never_swallows_itself():
    assert _swallows_other_building(BLOCK, SELF_ID, [(SELF_ID, BLOCK)]) is None


def test_the_threshold_is_the_modules_existing_one():
    """Reuse the declared constant. Do not introduce a second threshold."""
    assert OVERLAP_FRACTION == 0.10
    small = Polygon([(10, 0), (20, 0), (20, 10), (10, 10)])
    # Covers 5 percent of the neighbour: under the threshold.
    sliver = Polygon([(0, 0), (10.5, 0), (10.5, 10), (0, 10)])
    assert _swallows_other_building(
        sliver, SELF_ID, [(NEIGHBOUR_ID, small)]) is None


def test_the_worst_neighbour_is_the_one_reported():
    a = Polygon([(10, 0), (20, 0), (20, 10), (10, 10)])     # fully covered
    b = Polygon([(18, 0), (40, 0), (40, 10), (18, 10)])     # partly covered
    got = _swallows_other_building(
        BLOCK, SELF_ID, [("feature:b", b), ("feature:a", a)])
    assert got["feature_id"] == "feature:a"


def test_the_fraction_is_of_the_neighbour_not_of_the_candidate():
    """A large block covering a small house must read as covering ALL of it."""
    got = _swallows_other_building(BLOCK, SELF_ID, [(NEIGHBOUR_ID, NEIGHBOUR)])
    assert got["fraction"] == 1.0
    assert got["other_area_m2"] == 100.0


def test_the_rewrite_branch_actually_calls_the_guard():
    """Falsifier: a helper that exists is not a helper the branch uses."""
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines)
                 if 'counts["kept_osm_area_band"] += 1' in ln)
    end = next(i for i, ln in enumerate(lines[start:], start)
               if 'counts["rewritten_to_plateau"] += 1' in ln)
    branch = "\n".join(lines[start:end])
    assert "_swallows_other_building(" in branch, branch
    assert 'counts["kept_osm_subdivision_disagreement"] += 1' in branch


def test_the_refusal_keeps_both_buildings_and_records_it_unresolved():
    """Neither source wins, so the disagreement is recorded, not decided."""
    source = SOURCE.read_text(encoding="utf-8")
    start = source.index('counts["kept_osm_subdivision_disagreement"] += 1')
    block = source[start:start + 1600]
    assert '"osm"' in block
    assert '"subdivision_disagreement"' in block
    assert '"resolution": "unresolved",' in block
    assert '"covers_neighbour"' in block


def test_the_receipt_exposes_the_refusal():
    source = SOURCE.read_text(encoding="utf-8")
    assert '"kept_osm_subdivision_disagreement":' in source
    assert '"subdivision_reports": subdivision_reports,' in source


def test_the_two_guards_are_independent():
    """The swallow guard and this one answer different questions."""
    source = SOURCE.read_text(encoding="utf-8")
    assert "_newly_enclosed(" in source
    assert "_swallows_other_building(" in source
    assert '"kept_osm_swallows_observation":' in source
    assert '"kept_osm_subdivision_disagreement":' in source
