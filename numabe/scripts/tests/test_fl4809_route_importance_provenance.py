#!/usr/bin/env python3
"""FL-4809 R1b: route importance is part of the spatial-generation tuple.

An override representation plan is accepted only when its receipt proves it
was compiled from this run's inputs.  Route importance changes the plan --
measurably, on lane data: 322 of 733 building facade rows stop being
background, and one street sighting stops being intentionally omitted -- so a
plan that consumed it and a plan that did not are different plans.  Until this
change the tuple had no member for it, and an override compiled WITH route
importance would have been accepted into a run WITHOUT it, silently.

Two directions are pinned, because the hole runs both ways:

* this run supplies the receipt, the override receipt pins nothing -> refuse;
* the override receipt pins it, this run supplies nothing -> refuse.

The second one is the one the required-field loop could not express: it skips
any field absent from the current run, so a pinned-but-unsupplied optional
used to pass.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.representation_plan_provenance import (  # noqa: E402
    OPTIONAL_INPUT_FIELDS,
    PlanWorldMismatch,
    current_hashes,
    file_sha256,
    verify,
)

TUPLE_FIELDS = ("canonical", "entities", "terrain_metadata", "rules",
                "module_catalog", "pack", "route", "fusion_reviews")


def _files(tmp_path: Path, names) -> dict:
    made = {}
    for name in names:
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"name": name}), encoding="utf-8")
        made[name] = path
    return made


def _run(tmp_path: Path, *, with_route_importance: bool):
    names = list(TUPLE_FIELDS)
    if with_route_importance:
        names.append("route_importance")
    made = _files(tmp_path, names)
    kwargs = {name: made[name] for name in TUPLE_FIELDS}
    if with_route_importance:
        kwargs["route_importance"] = made["route_importance"]
    return made, current_hashes(**kwargs)


def _receipt(hashes: dict) -> dict:
    return {"inputs": dict(hashes), "provenance": {}}


def test_route_importance_is_a_tuple_member():
    assert "route_importance" in OPTIONAL_INPUT_FIELDS


def test_hashes_omit_route_importance_when_the_planner_did_not_consume_it(
        tmp_path):
    _, hashes = _run(tmp_path, with_route_importance=False)
    assert "route_importance" not in hashes


def test_matching_route_importance_verifies(tmp_path):
    _, hashes = _run(tmp_path, with_route_importance=True)
    assert verify(_receipt(hashes), hashes)["route_importance"]


def test_unpinned_route_importance_is_refused(tmp_path):
    """This run consumed a receipt; the override plan proves nothing about it."""
    _, hashes = _run(tmp_path, with_route_importance=True)
    stale = _receipt(hashes)
    del stale["inputs"]["route_importance"]
    with pytest.raises(PlanWorldMismatch, match="route_importance"):
        verify(stale, hashes)


def test_different_route_importance_is_refused(tmp_path):
    made, hashes = _run(tmp_path, with_route_importance=True)
    stale = _receipt(hashes)
    stale["inputs"]["route_importance"] = "0" * 64
    with pytest.raises(PlanWorldMismatch, match="route_importance"):
        verify(stale, hashes)


def test_pinned_but_unsupplied_route_importance_is_refused(tmp_path):
    """The direction the required-field loop cannot express.

    The override plan was compiled from a route-importance receipt. This run
    supplies none, so its buildings would be uniformly background while the
    override's are not. Accepting it would replay a plan from a world this run
    cannot reproduce.
    """
    made, _ = _run(tmp_path, with_route_importance=True)
    _, current = _run(tmp_path, with_route_importance=False)
    pinned = dict(current)
    pinned["route_importance"] = file_sha256(made["route_importance"])
    with pytest.raises(PlanWorldMismatch, match="route_importance"):
        verify(_receipt(pinned), current)


def test_no_receipt_on_either_side_still_verifies(tmp_path):
    """The deliberate compatibility path stays open."""
    _, hashes = _run(tmp_path, with_route_importance=False)
    assert verify(_receipt(hashes), hashes) == hashes
