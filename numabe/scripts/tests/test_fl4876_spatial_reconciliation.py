#!/usr/bin/env python3
"""FL-4876 SPATIAL-A1. The reconciliation sweep is centred on the observation.

``_reconcile_chainage_out_of_masses`` promises, in its own docstring, "the
closest mass-clear point within the observation's own sigma". It used to
enumerate candidates at absolute laterals ``0 .. sigma`` measured from the
*carrier centreline*, and then accept one only if it lay within sigma of the
*observed point*.

Those two constraints are disjoint by construction as soon as the object's
own lateral offset exceeds sigma: every enumerated candidate is then further
than sigma from the observation. The function returned
``beyond_geometry_uncertainty`` whatever the surrounding geometry looked
like. On the Numabe lane the shipped candidate set accepted nothing in 12 of
12 cases, at source laterals of 2.59-16.17 m against a 5.0 m sigma.

These tests pin the corrected enumeration and, just as importantly, pin the
budget it must never exceed. A repair that quietly widened sigma would clear
the same blockers and would be wrong.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.pipeline.canonical_attachment as attachment  # noqa: E402

SIGMA = 5.0


def _feature(px: float, py: float, sigma: float = SIGMA) -> dict:
    return {
        "geometry": {"coordinates": [px, py]},
        "properties": {"geometry": {
            "uncertainty": {"model": "isotropic_sigma", "sigma": sigma}}},
    }


def _carrier(qx: float = 0.0, qy: float = 0.0, bearing: float = 0.0) -> tuple:
    """A carrier at bearing 0, whose lateral normal is therefore +y.

    ``_reconcile_chainage_out_of_masses`` builds the normal at
    ``bearing + 90``, so side "R" is +y and side "L" is -y. Every geometry in
    this module is stated in that lateral coordinate.
    """
    return ("carrier:test", None, None, (qx, qy), bearing)


def _at_lateral(t: float, sigma: float = SIGMA) -> dict:
    """An observation t metres along the carrier's lateral normal."""
    return _feature(0.0, t, sigma)


def _mass(lo: float, hi: float):
    """A building mass covering the lateral band [lo, hi]."""
    return lambda x, y: lo <= y <= hi


def _record(lateral: float, side: str = "R") -> dict:
    return {"record_type": "attachment", "anchor_kind": "carrier_chainage",
            "lateral_offset_m": lateral, "side": side}


@pytest.fixture
def blocked(monkeypatch):
    """Declare which world points count as inside a building mass.

    The test owns the obstruction directly instead of building a canonical
    index, so each case states exactly the geometry it is about.
    """
    state: dict = {"predicate": lambda x, y: False}

    def _clear(px, py, clearance_m, index):
        return not state["predicate"](px, py)

    monkeypatch.setattr(attachment, "_clear_of_buildings", _clear)
    return state


def _reconcile(feature, record, carrier):
    return attachment._reconcile_chainage_out_of_masses(
        feature, record, carrier, index=None)


# --------------------------------------------------------------------------
# The defect itself.
# --------------------------------------------------------------------------

def test_an_object_beyond_sigma_from_the_centreline_can_still_reconcile(blocked):
    """The case the old enumeration could not express.

    The observation sits 12.0 m from the centreline inside a mass covering
    11.5 .. 12.5 m. A clear point lies 0.75 m away at 12.75 m. The old sweep
    only ever looked at -5.0 .. +5.0 m, every one of which is at least 7.0 m
    from the observation -- strictly beyond sigma, so not even the boundary
    candidate could be accepted. It returned debt.

    12.0 rather than 10.0 on purpose: at exactly twice sigma the old sweep's
    own +5.0 endpoint sits exactly sigma from the observation and is
    accepted, which made an earlier version of this test survive a mutation
    that restored the defect.
    """
    blocked["predicate"] = _mass(11.5, 12.5)
    resolved = _reconcile(_at_lateral(12.0), _record(12.0), _carrier())
    assert resolved["record_type"] == "attachment"
    assert resolved["spatial_reconciliation_m"] <= SIGMA
    assert resolved["source_lateral_offset_m"] == 12.0
    assert resolved["lateral_offset_m"] != 12.0


def test_the_centreline_sweep_is_empty_by_construction_beyond_sigma(blocked):
    """Guards the *reason* the old code failed, not just its symptom.

    Every candidate the old enumeration could produce -- laterals 0 .. sigma
    on either side -- is at least sigma from an observation whose own lateral
    is sigma or more. This asserts that arithmetic directly, so the test
    still states the defect even if the implementation is rewritten again.
    """
    source_lateral = 10.0
    for signed in [step * 0.25 for step in range(-20, 21)]:
        if abs(signed) > SIGMA:
            continue
        assert abs(signed - source_lateral) >= SIGMA


def test_a_reachable_clear_point_is_found_at_the_same_lattice(blocked):
    """The quarter-metre lattice stays anchored on the centreline.

    A candidate the old sweep could reach must still be enumerated at the
    same offset, so this repair cannot silently move a previously resolved
    object somewhere new.
    """
    blocked["predicate"] = _mass(1.9, 2.1)
    resolved = _reconcile(_at_lateral(2.0), _record(2.0), _carrier())
    assert resolved["record_type"] == "attachment"
    assert (resolved["lateral_offset_m"] * 4) % 1 == pytest.approx(0.0)


# --------------------------------------------------------------------------
# The budget the repair must not touch.
# --------------------------------------------------------------------------

def test_no_clear_point_within_sigma_is_still_debt(blocked):
    """A wide obstruction is still a refusal. This is the whole budget."""
    blocked["predicate"] = _mass(-40.0, 40.0)
    resolved = _reconcile(_at_lateral(10.0), _record(10.0), _carrier())
    assert resolved["record_type"] == "spatial_reconciliation_debt"
    assert resolved["reason"].endswith("beyond_geometry_uncertainty")
    assert resolved["residual_m"] == SIGMA


def _oracle(source_lateral: float, is_blocked, sigma: float = SIGMA):
    """The contract, computed independently of the implementation.

    Stated from the docstring rather than from the code: the closest
    mass-clear point on the quarter-metre lattice that lies within sigma of
    the observation, with the exact sigma endpoints reachable and the
    observed side preferred on a tie. Written this way so that a mutation to
    the implementation cannot also move the expectation -- the failure mode
    that let an earlier version of this suite pass while the defect was
    restored.
    """
    lattice = {round(step * 0.25, 3) for step in range(-400, 401)}
    lattice = {t for t in lattice
               if source_lateral - sigma - 1e-9 <= t <= source_lateral + sigma + 1e-9}
    lattice |= {round(source_lateral - sigma, 3), round(source_lateral + sigma, 3)}
    best = None
    for t in sorted(lattice):
        if is_blocked(0.0, t):
            continue
        movement = abs(t - source_lateral)
        if movement > sigma + 1e-9:
            continue
        key = (movement, (t < 0.0) != (source_lateral < 0.0), abs(t))
        if best is None or key < best[0]:
            best = (key, t, movement)
    return None if best is None else (best[1], best[2])


@pytest.mark.parametrize("source_lateral", [0.0, 1.0, 2.5, 5.0, 7.608, 12.0, 16.171])
@pytest.mark.parametrize("half_width", [0.25, 0.5, 1.0, 2.5, 4.9, 5.0, 6.0, 40.0])
def test_the_function_matches_the_contract_computed_independently(
        blocked, source_lateral, half_width):
    """Agreement on 56 geometries, including refusals.

    This is the test that discriminates. It fails if the sweep is re-anchored
    on the centreline (clear points within sigma get refused) and it fails if
    either the sweep or the acceptance budget is widened (points beyond sigma
    get accepted).
    """
    band = _mass(source_lateral - half_width, source_lateral + half_width)
    blocked["predicate"] = band
    resolved = _reconcile(_at_lateral(source_lateral),
                          _record(source_lateral,
                                  side="R" if source_lateral >= 0 else "L"),
                          _carrier())
    expected = _oracle(source_lateral, band)
    if expected is None:
        assert resolved["record_type"] == "spatial_reconciliation_debt", (
            "no clear point exists within sigma, so this must refuse")
        return
    assert resolved["record_type"] == "attachment", (
        f"a clear point exists at lateral {expected[0]} "
        f"({expected[1]:.3f} m from the observation, within sigma), "
        "so refusing is wrong")
    assert resolved["spatial_reconciliation_m"] == pytest.approx(
        expected[1], abs=1e-6), "accepted a different point than the closest"
    assert resolved["spatial_reconciliation_m"] <= SIGMA + 1e-9


def test_the_acceptance_test_still_bites_off_the_perpendicular(blocked):
    """Where the movement check is not redundant, it must still refuse.

    With the sweep anchored on the observation's own lateral, every
    enumerated candidate is within sigma of the observation *whenever the
    observation lies on the carrier perpendicular through q* -- there the
    movement check is arithmetically redundant, and doubling it changes
    nothing. That redundancy is why it is kept: it is the only guard left
    when the two disagree.

    Here the observation sits 3.0 m along-track from q, so a candidate at
    lateral t is hypot(3, t - 12) away, not |t - 12|. The mass covers
    8.0 .. 16.0; the nearest clear candidate inside the sweep is 16.25, at
    hypot(3, 4.25) = 5.20 m. Beyond sigma, so this must refuse -- and it is
    exactly what stops refusing if the movement budget is widened.
    """
    blocked["predicate"] = _mass(8.0, 16.0)
    resolved = _reconcile(_feature(3.0, 12.0), _record(12.0), _carrier())
    assert resolved["record_type"] == "spatial_reconciliation_debt"
    assert math.hypot(3.0, 4.25) > SIGMA


def test_off_perpendicular_still_accepts_what_is_genuinely_in_budget(blocked):
    """The companion case: the same geometry with a reachable clear point."""
    blocked["predicate"] = _mass(10.0, 13.0)
    resolved = _reconcile(_feature(3.0, 12.0), _record(12.0), _carrier())
    assert resolved["record_type"] == "attachment"
    assert resolved["spatial_reconciliation_m"] <= SIGMA + 1e-9


def test_an_absent_sigma_is_debt_not_a_default(blocked):
    """No declared uncertainty is no movement budget at all."""
    feature = {"geometry": {"coordinates": [0.0, 10.0]}, "properties": {}}
    blocked["predicate"] = _mass(9.5, 10.5)
    resolved = _reconcile(feature, _record(10.0), _carrier())
    assert resolved["record_type"] == "spatial_reconciliation_debt"
    assert resolved["reason"].endswith("no_geometry_uncertainty")


def test_an_already_clear_record_is_returned_untouched(blocked):
    blocked["predicate"] = lambda x, y: False
    record = _record(10.0)
    assert _reconcile(_at_lateral(10.0), record, _carrier()) is record


# --------------------------------------------------------------------------
# Side handling.
# --------------------------------------------------------------------------

def test_the_source_side_is_preferred_when_both_sides_are_clear(blocked):
    """A single frame does not establish road side, so both are eligible --
    but an equal-cost candidate on the observed side must win."""
    blocked["predicate"] = _mass(-0.4, 0.4)
    resolved = _reconcile(_at_lateral(0.0), _record(0.0, side="R"),
                          _carrier())
    assert resolved["record_type"] == "attachment"
    assert resolved["side"] == "R"


def test_crossing_to_the_other_side_stays_inside_the_budget(blocked):
    """The opposite side is reachable only when it is genuinely within sigma."""
    blocked["predicate"] = lambda x, y: y >= -1.0
    resolved = _reconcile(_at_lateral(1.0), _record(1.0, side="R"),
                          _carrier())
    assert resolved["record_type"] == "attachment"
    assert resolved["side"] == "L"
    assert resolved["spatial_reconciliation_m"] <= SIGMA
    assert math.isclose(resolved["spatial_reconciliation_m"],
                        1.0 + resolved["lateral_offset_m"], abs_tol=1e-6)


# --------------------------------------------------------------------------
# SPATIAL-A4: a door belongs to the wall it is in.
# --------------------------------------------------------------------------

LANE_CANONICAL = ROOT / ".run/numabe_connected/build_e2e_01/canonical.world_ir.json"
LANE_RULES = ROOT / ".run/numabe_connected/build_e2e_01/plan/rules_resolved.json"

requires_lane = pytest.mark.skipif(
    not (LANE_CANONICAL.is_file() and LANE_RULES.is_file()),
    reason="the lane canonical world is unavailable")


def test_a_station_entrance_resolves_against_a_face_not_a_carrier():
    """``face_first`` decides whether a class looks at a wall or a street.

    station_entrance was absent from it, so both entrance nodes took the
    carrier path and were then refused for sitting inside a building --
    which they do, because a door is in a wall. The correct face existed
    the whole time, 0.694 m and 0.968 m away against a 6.0 m tolerance.
    """
    source = (ROOT / "scripts" / "pipeline"
              / "canonical_attachment.py").read_text(encoding="utf-8")
    start = source.index("face_first = cls in {")
    assert '"station_entrance"' in source[start:start + 120]


@requires_lane
def test_both_lane_station_entrances_attach_to_their_host_face():
    """Measured end to end on the lane, not asserted from the source."""
    import json
    from scripts.pipeline.canonical_attachment import CanonicalIndex, attach
    canonical = json.loads(LANE_CANONICAL.read_text(encoding="utf-8"))
    rules = json.loads(LANE_RULES.read_text(encoding="utf-8"))
    index = CanonicalIndex(canonical)
    entrances = [f for f in canonical["canonical_features"]
                 if f.get("semantic_class") == "station_entrance"]
    assert len(entrances) == 2
    for feature in entrances:
        record = attach(feature, index, rules)
        assert record["record_type"] == "attachment", (
            feature["feature_id"], record.get("reason"))
        assert record["anchor_kind"] == "building_face"
        assert record["canonical_face_id"]
