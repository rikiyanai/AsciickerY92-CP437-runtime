#!/usr/bin/env python3
"""FL-4876 Repair 2. A facade is never demoted into a freestanding fixture.

Three facade-domain placements on this lane could not reach a canonical
building face. They were demoted to freestanding fixtures, which set
``attachment_state`` to ``"attached"`` -- so the failure was invisible to every
downstream gate -- and then stopped asset authoring anyway, because no
freestanding archetype exists for a facade module.

The tests here pin the three facts the repair rests on, and the routing that
makes an unfillable slot visible instead of fatal.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.dispatch_population_plan import (  # noqa: E402
    OWNERS,
    _owner_for,
)
from scripts.pipeline.population_asset_geometry import (  # noqa: E402
    ARCHETYPE_BY_MODULE,
)
from scripts.pipeline.population_representation_contract import (  # noqa: E402
    FIXTURE_DOMAINS,
    fixture_asset_contract,
)

PLANNER_SOURCE = ROOT / "scripts" / "pipeline" / "compile_population_plan.py"

RULES = {
    "road_class_defaults": {
        "storey_height_m": {"value": 3.2, "source": "test"},
    },
}

# The three facade-domain modules that failed on this lane, with the domain
# the module catalog gives each one.
LANE_FAILURES = [
    ("narrow_mixed_use", "facade_archetype"),
    ("roller_shutter_facade", "facade_archetype"),
    ("independent_storefront_awning", "generic_storefront"),
]

FACADE_DOMAINS = frozenset({"facade_archetype", "generic_storefront"})


# --------------------------------------------------------------------------
# Why the old condition was the wrong one.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("anchor_kind", [
    "carrier_chainage", "carrier_span", "freestanding", None])
def test_any_anchor_but_building_face_is_already_freestanding(anchor_kind):
    """mount_kind keys on building_face, not on the name "freestanding".

    All three lane failures carry ``carrier_chainage``. A first version of
    this repair tested ``anchor_kind == "freestanding"`` and therefore never
    fired -- the re-run produced a plan identical in every state count.
    """
    contract = fixture_asset_contract(
        "narrow_mixed_use",
        {"attachment": {"anchor_kind": anchor_kind}, "height_m": 0.0},
        RULES)
    assert contract["mount_kind"] == "freestanding_fixture"


def test_only_a_building_face_anchor_yields_a_facade_panel():
    contract = fixture_asset_contract(
        "narrow_mixed_use",
        {"attachment": {"anchor_kind": "building_face"}, "height_m": 0.0},
        RULES)
    assert contract["mount_kind"] == "building_face_panel"


def test_the_planner_tests_the_building_face_anchor_not_the_name():
    """Guards the exact predicate, since getting it wrong is silent."""
    source = PLANNER_SOURCE.read_text(encoding="utf-8")
    assert '_FACADE_OWNING_DOMAINS' in source
    assert 'and _rec.get("anchor_kind") != "building_face"' in source, (
        "the facade invariant must test anchor_kind != building_face; "
        'testing == "freestanding" silently matches nothing')


# --------------------------------------------------------------------------
# Why demoting cannot work even in principle.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("module_id,domain", LANE_FAILURES)
def test_a_demoted_facade_has_no_freestanding_archetype(module_id, domain):
    """Not a catalog gap. A facade has no freestanding form to fall back to."""
    assert domain in FACADE_DOMAINS
    assert domain in FIXTURE_DOMAINS
    assert module_id not in ARCHETYPE_BY_MODULE


def test_the_planner_states_the_invariant_against_the_domain():
    """independent_storefront_awning is why the test is on the domain.

    It is absent from _FACADE_ATTACH_MODULES, so it is never face-tested at
    all; a module-list invariant would have missed it.
    """
    source = PLANNER_SOURCE.read_text(encoding="utf-8")
    start = source.index("_FACADE_OWNING_DOMAINS = frozenset(")
    declaration = source[start:start + 200]
    assert '"facade_archetype"' in declaration
    assert '"generic_storefront"' in declaration


# --------------------------------------------------------------------------
# Where an unfillable slot goes instead.
# --------------------------------------------------------------------------

def test_an_unfilled_facade_slot_dispatches_to_terminal():
    """terminal authors no asset, so the slot is recorded rather than faked."""
    placement = {
        "placement_id": "obs-inst-0217",
        "module_id": "narrow_mixed_use",
        "population_disposition": {
            "state": "observation_owed",
            "category": "unfilled_facade_slot",
            "reason": "a facade module resolved to no canonical building face",
            "cause": "nearest_face_beyond_resolve_tolerance",
            "nearest_face_distance_m": 6.832,
            "face_resolve_tolerance_m": 6.0,
        },
    }
    owner, basis = _owner_for(placement, {})
    assert owner == "terminal"
    assert owner in OWNERS
    assert "unfilled_facade_slot" in basis


def test_a_facade_on_a_face_still_dispatches_to_fixtures():
    """The invariant must not swallow the 806 facades that do attach."""
    placement = {
        "placement_id": "rule-facade-0001",
        "module_id": "narrow_mixed_use",
        "attachment": {"anchor_kind": "building_face"},
    }
    owner, _basis = _owner_for(
        placement, {"narrow_mixed_use": {"owning_domain": "facade_archetype"}})
    assert owner == "fixtures"


def test_the_disposition_carries_the_measured_reason():
    """An unfillable slot that does not say why is a silent drop.

    The planner writes the nearest face, its distance, and the tolerance it
    exceeded, so the operator can tell "the wall is 30 m away" from "no wall
    was ever tested".
    """
    source = PLANNER_SOURCE.read_text(encoding="utf-8")
    start = source.index('"category": "unfilled_facade_slot"')
    block = source[start - 600:start + 900]
    for field in ("nearest_face_distance_m", "face_resolve_tolerance_m",
                  "nearest_canonical_face_id", "cause"):
        assert field in block, f"the unfilled slot does not record {field}"
    assert "no_building_face_candidate_was_tested" in block, (
        "the never-tested cause must be distinguishable from the "
        "beyond-tolerance cause")


# --------------------------------------------------------------------------
# FL-4876 Repair 2 completion: the invariant and the test share one owner.
#
# The invariant above is stated against owning_domain. The predicate that
# decides whether a facade is face-tested at all was stated against the
# module list. Two owners, one question -- so independent_storefront_awning
# reached the invariant with cause="no_building_face_candidate_was_tested".
# The report was correct. The measurement had never run.
# --------------------------------------------------------------------------

def test_the_face_test_predicate_consults_the_owning_domain():
    """Falsifier: restore the module-list-only predicate and this fails.

    _is_facade_mod gates the branch that builds building-face candidates.
    If it asks only _FACADE_ATTACH_MODULES, a facade-domain module that is
    absent from that list is never face-tested, falls through to a carrier
    or freestanding record, and is reported as untested rather than as
    measured-and-refused.
    """
    source = PLANNER_SOURCE.read_text(encoding="utf-8")
    start = source.index("_is_facade_mod = (")
    predicate = source[start:start + 260]
    assert "_FACADE_ATTACH_MODULES" in predicate, predicate
    assert "_FACADE_OWNING_DOMAINS" in predicate, predicate
    assert "owning_domain" in predicate, predicate


def test_the_face_test_predicate_gates_the_candidate_branch():
    """The predicate must still be what opens the face-candidate branches."""
    source = PLANNER_SOURCE.read_text(encoding="utf-8")
    assert (
        "if _rec is None and isinstance(_run, dict) and _is_facade_mod:"
        in source)
    assert "elif _rec is None and (_is_facade_mod or _is_face_point):" in source


def test_an_exhausted_face_list_is_distinguished_from_a_degenerate_ring():
    """Two different measurements must not collapse into one reason string.

    'every candidate face was occluded by a mass' and 'the ring produced no
    usable edge' are different findings. The first carries a nearest face and
    the conflicting mass ids; the second cannot.
    """
    source = PLANNER_SOURCE.read_text(encoding="utf-8")
    assert "no_face_candidate_survived_mass_conflict" in source
    start = source.index("no_face_candidate_survived_mass_conflict")
    block = source[start - 400:start + 900]
    assert "conflicting_mass_ids" in block, block
    assert "nearest_face_distance_m" in block, block
    # The degenerate case stays available; it is not replaced.
    assert "degenerate_geometry:no_usable_face" in block, block
