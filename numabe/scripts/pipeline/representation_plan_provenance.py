#!/usr/bin/env python3
"""Representation-plan provenance: the complete spatial-generation tuple.

FL-4809 PLANORDER-04. The representation plan is generated and verified from
one complete tuple; a stale member (entities, terrain, rules, scale, catalog,
route, fusion decisions, planner context) must fail closed naming the field.

The tuple definition lives here. ``compile_representation_plan.build()``
pins it into the receipt; ``compile_representation_plan.verify()`` compares
an override receipt against this run's inputs. Only pinned (non-null)
optional fields are compared; a missing optional pin means the planner did
not consume that input, never a silent pass over a consumed one.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

SCHEMA = "y92.representation_plan_provenance.v1"

# Every member the planner consumes when generating geometry. An override
# receipt must pin each of these or verify() fails closed naming it.
REQUIRED_INPUT_FIELDS = (
    "canonical",
    "entities",
    "terrain_metadata",
    "rules",
    "module_catalog",
    "pack",
    "route",
    "fusion_reviews",
)

# Pinned only when the planner consumes the input (receipt carries the key).
#
# route_importance is optional because the planner's no-receipt path is
# deliberate: absent the receipt, importance falls back to the observation-debt
# row and the plan is byte-for-byte what it was before FL-4809 R1. But once a
# plan WAS compiled with route importance, an override of it is only valid
# against a run carrying the same receipt -- so verify() treats a pinned
# optional whose field this run does not supply as a mismatch, not a pass.
#
# authored_waivers is optional for the same reason and pinned for a sharper
# one: it is the only planner input carrying a human's approval rather than a
# measurement. A plan compiled with a waiver omits a row that a run without
# that document would refuse to finish. Replaying such a plan into a run whose
# waiver set differs would let one operator's approval silently authorise a
# world they never saw.
# aoi is optional and pinned for the same reason as authored_waivers: it is
# the declared product scope, so it decides which features the plan promises
# at all. Replaying a plan compiled against one AOI into a run with a
# different scope would promise a world nobody asked for, or silently drop
# one they did.
OPTIONAL_INPUT_FIELDS = ("jev_decisions", "route_importance",
                         "authored_waivers", "aoi")

# Lattice inputs the planner also consumes; pinned for audit, compared too.
LEGACY_INPUT_FIELDS = ("debt", "triage_receipt", "osm_ir")

# Scalars compared by value, not by hash.
REQUIRED_SCALAR_FIELDS = (
    "world_units_per_metre",
    "planner_version",
    "planner_context",
)


class PlanWorldMismatch(ValueError):
    """Raised when an override plan's pin disagrees with this run's inputs."""


def file_sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def current_hashes(*, canonical, entities, terrain_metadata, rules,
                   module_catalog, pack, route, fusion_reviews,
                   jev_decisions=None, route_importance=None,
                   authored_waivers=None, aoi=None, debt=None,
                   triage_receipt=None, osm_ir=None) -> dict:
    """Hash this run's tuple members. Missing optionals stay absent."""
    hashes = {
        "canonical": file_sha256(canonical),
        "entities": file_sha256(entities),
        "terrain_metadata": file_sha256(terrain_metadata),
        "rules": file_sha256(rules),
        "module_catalog": file_sha256(module_catalog),
        "pack": file_sha256(pack),
        "route": file_sha256(route),
        "fusion_reviews": file_sha256(fusion_reviews),
    }
    if jev_decisions is not None:
        hashes["jev_decisions"] = file_sha256(jev_decisions)
    if route_importance is not None:
        hashes["route_importance"] = file_sha256(route_importance)
    if authored_waivers is not None:
        hashes["authored_waivers"] = file_sha256(authored_waivers)
    if aoi is not None:
        hashes["aoi"] = file_sha256(aoi)
    if debt is not None:
        hashes["debt"] = file_sha256(debt)
    if triage_receipt is not None:
        hashes["triage_receipt"] = file_sha256(triage_receipt)
    if osm_ir is not None:
        hashes["osm_ir"] = file_sha256(osm_ir)
    return hashes


def verify(receipt, current: dict, *, current_scalars: dict | None = None) -> dict:
    """Fail closed when the override receipt's tuple differs from this run.

    ``receipt`` is a loaded receipt doc (or a path to one); ``current`` maps
    tuple field -> this run's sha256; ``current_scalars`` maps scalar field
    -> this run's value. Every required field absent from the receipt pins
    is a mismatch naming that field. Returns the receipt inputs on success.
    """
    doc = receipt
    if isinstance(receipt, (str, Path)):
        doc = json.loads(Path(receipt).read_text(encoding="utf-8"))
    pinned = (doc or {}).get("inputs", {})
    if not isinstance(pinned, dict):
        raise PlanWorldMismatch(
            "PLAN_WORLD_MISMATCH: override receipt has no inputs tuple")
    provenance = (doc or {}).get("provenance", {})
    if not isinstance(provenance, dict):
        provenance = {}
    diffs = []
    for field in REQUIRED_INPUT_FIELDS + LEGACY_INPUT_FIELDS:
        if field not in current:
            continue
        want = pinned.get(field)
        if want is None:
            diffs.append((field, None, current[field]))
        elif want != current[field]:
            diffs.append((field, want, current[field]))
    for field in OPTIONAL_INPUT_FIELDS:
        want = pinned.get(field)
        actual = current.get(field)
        if want is None and actual is None:
            continue
        if actual is None:
            # The override plan was compiled from an input this run does not
            # supply. Skipping here -- which is what the required/legacy loop
            # above does for an absent member -- would accept a plan built
            # from a world this run cannot reproduce. Name it instead.
            diffs.append((field, want, None))
        elif want is None or want != actual:
            diffs.append((field, want, actual))
    for field, actual in (current_scalars or {}).items():
        want = provenance.get(field)
        if want is None or actual is None:
            if field in REQUIRED_SCALAR_FIELDS and want is None \
                    and actual is not None:
                diffs.append((field, None, actual))
            continue
        if json.dumps(want, sort_keys=True) != json.dumps(actual, sort_keys=True):
            diffs.append((field, want, actual))
    if diffs:
        lines = ["PLAN_WORLD_MISMATCH: override representation plan "
                 "differs from this run's spatial-generation tuple:"]
        for field, want, actual in diffs:
            lines.append(f"  {field}: pinned={want} actual={actual}")
        raise PlanWorldMismatch("\n".join(lines))
    return pinned
