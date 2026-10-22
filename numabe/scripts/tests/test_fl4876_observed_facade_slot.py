#!/usr/bin/env python3
"""FL-4876 FACADE-C1. An observed storefront on a face IS that building's facade.

Five buildings reached the pre-assembly gate reported as having no facade at
all, while a measured storefront panel stood on their named canonical face:
Cocokara Fine, FamilyMart, and three storefront sign clusters, each with
``anchor_kind: building_face`` and a real ``canonical_face_id``.

The planner already knew. ``obs_facade_filled`` marks a building whose facade
slot an observation has filled and suppresses the archetype run there -- but
it kept that to itself. Downstream, ``_owner_for`` routes every
``custom_identity_subject`` to owner ``custom`` before module domain is even
consulted, and ``wants_facade`` required ``owner == "fixtures"``. So the
placement bound to the building's *primary* representation row
(``terrain_mass``) instead of its ``mesh_facade`` row, and the facade looked
missing.

The planner now stamps the placement it has already identified; dispatch
reads the stamp instead of re-deriving the fact from owner and module domain,
which is what got it wrong.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# plan_spatial_provenance resolves canonical_attachment as a sibling module,
# the same convention the production runner sets up.
_PIPELINE = ROOT / "scripts" / "pipeline"
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))

from scripts.pipeline.dispatch_population_plan import (  # noqa: E402
    _owner_for,
    partition,
)

PLANNER_SOURCE = ROOT / "scripts" / "pipeline" / "compile_population_plan.py"
ASSET_SOURCE = ROOT / "scripts" / "pipeline" / "compile_population_asset_plan.py"

BUILDING = "feature:building_footprint:way/1507398033"
FACE = BUILDING + "#f0"

CATALOG = {"modules": [
    {"module_id": "narrow_mixed_use", "owning_domain": "facade_archetype"},
]}

REPRESENTATION = {"rows": [
    {"feature_id": BUILDING, "primary": True, "disposition": "terrain_mass",
     "representation_id": "rep:mass", "owner_stage": "bake"},
    {"feature_id": BUILDING, "primary": False, "disposition": "mesh_facade",
     "representation_id": "rep:facade", "owner_stage": "population_fixtures"},
]}

RULES = {"road_class_defaults": {
    "storey_height_m": {"value": 3.2, "source": "test"},
}}

CARRIER = "feature:road:way/9001"

CANONICAL = {"canonical_features": [
    {"feature_id": BUILDING, "semantic_class": "building_footprint",
     "geometry": {"kind": "polygon", "coordinates": [
         [0.0, 0.0], [10.0, 0.0], [10.0, 8.0], [0.0, 8.0], [0.0, 0.0]]}},
    {"feature_id": CARRIER, "semantic_class": "road",
     "geometry": {"kind": "polyline",
                  "coordinates": [[0.0, -5.0], [20.0, -5.0]]}},
]}


def _placement(*, custom: bool, stamped: bool, anchor="building_face") -> dict:
    placement = {
        "placement_id": "obs-inst-0111",
        "attachment": {
            "record_type": "attachment",
            "anchor_kind": anchor,
            "canonical_building_id": BUILDING,
            "canonical_face_id": FACE,
            "face_index": 0,
            "along_face_fraction": 0.5,
            "outward_offset_m": 0.2,
        },
        "canonical_feature_id": BUILDING,
        "evidence": {"kind": "observation", "instance_id": "obs-0111"},
        # Selected upstream by the planner; dispatch refuses a placement that
        # arrives without one, so the fixture states it explicitly.
        "asset_physical_envelope": {
            "selection_stage": "population_before_dispatch",
            "width_m": 3.0, "height_m": 2.4, "depth_m": 0.3,
        },
    }
    if custom:
        placement["custom_identity_subject"] = "cocokara_fine_storefront"
    else:
        placement["module_id"] = "narrow_mixed_use"
    if anchor == "carrier_chainage":
        placement["attachment"].update({
            "canonical_carrier_id": CARRIER,
            "chainage_m": 4.0, "lateral_offset_m": 1.5, "side": "R",
        })
    if stamped:
        placement["facade_slot"] = {
            "role": "observed_facade", "host_id": BUILDING,
            "basis": "observation fills the building facade slot",
        }
    return placement


def _decision(placement: dict) -> dict:
    plan = {"carriers": [{"carrier_id": "carrier:test",
                          "placements": [placement]}]}
    slices = partition(
        plan, CATALOG, REPRESENTATION, RULES, CANONICAL,
        source_plan_sha256="0" * 64, representation_plan_sha256="1" * 64)
    for owner_slice in slices.values():
        for carrier in owner_slice.get("carriers", []):
            for row in carrier.get("placements", []):
                if row["placement_id"] == placement["placement_id"]:
                    return row.get("representation_decision") or {}
    raise AssertionError("placement vanished during dispatch")


# --------------------------------------------------------------------------
# The binding.
# --------------------------------------------------------------------------

def test_a_stamped_observed_facade_binds_to_the_mesh_facade_row():
    decision = _decision(_placement(custom=True, stamped=True))
    assert decision.get("disposition") == "mesh_facade"
    assert decision.get("representation_id") == "rep:facade"


def test_an_unstamped_custom_placement_still_binds_to_the_primary_row():
    """The stamp is load-bearing, not decoration.

    Without it the old behaviour is reproduced exactly: the storefront binds
    to the building mass and the facade slot reads as empty.
    """
    decision = _decision(_placement(custom=True, stamped=False))
    assert decision.get("disposition") == "terrain_mass"


def test_a_stamp_cannot_turn_a_freestanding_object_into_a_facade():
    """A vending machine beside the wall is not the building's front."""
    decision = _decision(
        _placement(custom=True, stamped=True, anchor="carrier_chainage"))
    assert decision.get("disposition") != "mesh_facade"


def test_the_fixtures_facade_path_is_unchanged():
    decision = _decision(_placement(custom=False, stamped=False))
    assert decision.get("disposition") == "mesh_facade"


def test_a_custom_subject_still_dispatches_to_the_custom_owner():
    """Binding to the facade row must not move the placement's owner.

    The custom lowerer builds this asset from measured dimensions; the stamp
    is about which representation the placement answers for, not who builds
    it.
    """
    owner, basis = _owner_for(_placement(custom=True, stamped=True), {})
    assert owner == "custom"
    assert basis == "custom_identity_subject"


# --------------------------------------------------------------------------
# The stamp's producer.
# --------------------------------------------------------------------------

def test_the_planner_stamps_the_observation_that_fills_the_slot():
    """The stamp records WHICH placement filled the slot, and defers.

    It cannot test the anchor: ``obs_facade_filled`` is computed roughly
    1900 lines before any attachment is resolved, so at that line every
    placement's attachment is still absent. The first version of this repair
    did test it there, stamped zero placements, and produced a plan identical
    to the one before -- the same silent no-op as the first facade-demotion
    predicate earlier in this work. The anchor requirement lives in dispatch,
    where the anchor exists, and the test below pins it there.
    """
    source = PLANNER_SOURCE.read_text(encoding="utf-8")
    start = source.index('"role": "observed_facade"')
    block = source[start - 900:start + 400]
    assert '"custom_identity_subject" in p' in block
    assert 'anchor_checked_by' in block, (
        "the stamp must name the stage that enforces the building_face "
        "requirement, so deferring it is explicit rather than forgotten")
    # The record has to land ON the placement. Asserting the literal's shape
    # alone let a mutation that renamed the assignment target pass, which is
    # the same "the test reads the code rather than its effect" trap that bit
    # the unmapped-row contract earlier in this work.
    assert 'p["facade_slot"] = {' in block, (
        "the observed_facade record is built but never written to the "
        "placement, so dispatch can never see it")


def test_dispatch_reads_the_stamp_rather_than_the_owner():
    source = (ROOT / "scripts" / "pipeline"
              / "dispatch_population_plan.py").read_text(encoding="utf-8")
    assert 'facade_slot.get("role") == "observed_facade"' in source
    start = source.index("wants_facade = (")
    block = source[start:start + 400]
    assert "observed_facade" in block
    assert 'attachment.get("anchor_kind") == "building_face"' in block


# --------------------------------------------------------------------------
# Appearance provenance, stated rather than invented.
# --------------------------------------------------------------------------

def test_a_custom_asset_reports_the_colour_it_is_actually_built_from():
    """The box is coloured from background_rgb; that must reach the row.

    Without this the repair above would only move these five from
    building_facade_population to building_facade_appearance -- a renamed
    blocker, not a repair.
    """
    source = ASSET_SOURCE.read_text(encoding="utf-8")
    start = source.index('colour_basis = str(spec.get("colour_basis")')
    block = source[start:start + 1100]
    assert '"appearance_contract"' in block
    assert 'spec.get("background_rgb")' in block
    assert 'spec.get("text_rgb")' in block


def test_a_fallback_colour_is_never_reported_as_a_measurement():
    """provenance_class separates the two, because on this lane all 61
    custom assets report crop_unreadable -- a neutral grey default, not
    source colour."""
    source = ASSET_SOURCE.read_text(encoding="utf-8")
    start = source.index('measured = colour_basis ==')
    block = source[start:start + 600]
    assert '"source_crop_dominant"' in block
    assert '"synthesized_fallback"' in block
    assert '"source_measured"' in block
