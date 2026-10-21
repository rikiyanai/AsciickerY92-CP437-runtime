"""FL-4876 OBS-YAW-01: spatial coherence verifies the yaw lowering emitted.

Pins _lowered_yaw_reference's choice of reference per tile kind. resolve() is
stubbed so the test exercises the routing, not canonical geometry.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.pipeline import compile_spatial_coherence_receipt as C  # noqa: E402

RESOLVED = 129.0     # attachment carrier bearing (rail)
STORED = 15.3        # planner bearing of the carrier the placement is filed under


@pytest.fixture(autouse=True)
def _stub_resolve(monkeypatch):
    monkeypatch.setattr(C, "resolve", lambda attachment, index: {"yaw_deg": RESOLVED})


def _ref(kind, evidence, anchor, run=False, placement_yaw=STORED):
    inst = {"logical_instance_kind": kind, "evidence_kind": evidence, "run_record": run}
    placement = {"yaw_deg": placement_yaw} if placement_yaw is not None else {}
    return C._lowered_yaw_reference(inst, placement, {"anchor_kind": anchor}, anchor, None)


@pytest.mark.parametrize("kind", ["unit", "point", "hardware"])
def test_observation_chainage_tiles_use_the_resolved_attachment(kind):
    # fence 0152: filed under a footway, attached to the rail, emitted along the rail
    assert _ref(kind, "observation", "carrier_chainage") == RESOLVED


def test_observation_points_along_a_span_keep_the_run_chord():
    # obs-inst-0077: span-run points take the run chord, which is the stored yaw
    assert _ref("point", "observation", "carrier_span") == STORED


def test_rule_runs_use_the_resolved_attachment():
    assert _ref("segment", "rule", "carrier_chainage", run=True) == RESOLVED


def test_unattached_placements_keep_their_authored_yaw():
    assert _ref("point", "observation", "freestanding") == STORED


def test_no_reference_without_a_stored_yaw():
    assert _ref("point", "observation", "freestanding", placement_yaw=None) is None


def test_stored_yaw_disagreement_is_no_longer_reported_for_0152():
    built = 129.0
    assert C._yaw_diff_deg(built, _ref("unit", "observation", "carrier_chainage")) == 0.0
    assert C._yaw_diff_deg(built, STORED) == pytest.approx(113.7, abs=0.1)
