#!/usr/bin/env python3
"""FL-4876 UNPLACEABLE-01. An observation that cannot be placed is an owned
outcome, not an unowned debt.

obs-inst-0110, a street light, sits inside a canonical building mass, and no
clear position exists within its own sigma. It kept a
spatial_reconciliation_debt record and NO population_disposition, so dispatch
routed it to a geometry owner, which refused the entire build:

    ValueError: population placement obs-inst-0110 reaches dispatch with
    unresolved attachment debt: observed_point_inside_canonical_mass

One unplaceable street light stopped 3010 other resolved placements from being
authored. The repair gives the refusal an owner. It invents no position and
clears no obligation.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.pipeline.dispatch_population_plan import _owner_for  # noqa: E402

PLANNER = ROOT / "scripts" / "pipeline" / "compile_population_plan.py"
DISPATCH = ROOT / "scripts" / "pipeline" / "dispatch_population_plan.py"

DISPOSITION = {
    "state": "observation_owed",
    "category": "unresolved_attachment_debt",
    "reason": "the observed position lies inside a canonical building mass",
    "cause": "observed_point_inside_canonical_mass",
    "module_id": "pole_streetlight",
    "conflicting_mass_ids": ["feature:building_footprint:way/1"],
    "own_mass_id": None,
}


def test_the_disposition_routes_to_the_owner_that_authors_nothing():
    owner, reason = _owner_for(
        {"placement_id": "obs-inst-0110", "module_id": "pole_streetlight",
         "population_disposition": DISPOSITION}, {})
    assert owner == "terminal"
    assert "unresolved_attachment_debt" in reason


def test_without_a_disposition_the_same_placement_goes_to_geometry():
    """This is what made dispatch refuse. Pinned so the repair cannot be
    mistaken for a dispatch change."""
    owner, _ = _owner_for(
        {"placement_id": "obs-inst-0110", "module_id": "pole_streetlight"},
        {"pole_streetlight": {"owning_domain": "street_fixture",
                              "lowering_owner": "fixtures"}})
    assert owner != "terminal"


def test_the_repair_lives_at_the_single_choke_point():
    """_mass_debt is a shared helper called from several sites, so a
    site-specific fix misses producers. The first attempt did exactly that
    and obs-inst-0110 still reached dispatch. The repair belongs where every
    attachment outcome is finally written."""
    src = PLANNER.read_text(encoding="utf-8")
    i = src.index('"category": "unresolved_attachment_debt",')
    j = src.index('_apl["attachment"] = _rec', i)
    # the disposition is set immediately before the final assignment
    assert 0 < j - i < 900, (j - i)


def test_it_fires_for_any_debt_reason_not_one_producer():
    src = PLANNER.read_text(encoding="utf-8")
    i = src.index('"category": "unresolved_attachment_debt",')
    block = src[i - 700:i + 700]
    assert '"record_type") == "spatial_reconciliation_debt"' in block
    assert '"cause": _rec.get("reason", "unnamed")' in block


def test_an_existing_disposition_is_never_overwritten():
    src = PLANNER.read_text(encoding="utf-8")
    i = src.index('"category": "unresolved_attachment_debt",')
    block = src[i - 700:i + 200]
    assert 'not isinstance(' in block
    assert '_apl.get("population_disposition"), dict)' in block


def test_no_position_is_invented():
    src = PLANNER.read_text(encoding="utf-8")
    i = src.index('"category": "unresolved_attachment_debt",')
    block = src[i - 900:i + 900]
    assert '_apl["E"] =' not in block
    assert '_apl["N"] =' not in block
    assert '_state = "attached"' not in block


def test_the_dispatch_guard_is_untouched():
    """The repair gives the placement an owner. It does not relax dispatch."""
    src = DISPATCH.read_text(encoding="utf-8")
    assert "reaches dispatch " in src
    assert "with unresolved attachment debt" in src
    assert 'if owner != "terminal":' in src
