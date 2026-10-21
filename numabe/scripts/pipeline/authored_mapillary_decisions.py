#!/usr/bin/env python3
"""Authored per-object decisions for Mapillary detections (FL-4876).

A Mapillary detection becomes a world fixture in compile_representation_plan's
R-STREET lane.  Before this document existed nothing could say "this detection
is the same junction box as that one", "this blade sign is already drawn by an
authored sign", or "this signal head sits on the platform edge": the only
exits were the fusion reviews (same object as an OSM candidate) and the Jev
routing, neither of which is a per-object human decision.  A reviewed wrong
detection stayed in the world until the detection itself was edited.

The owner file is ``authored_mapillary_object_decisions.json`` in the source
bundle, schema ``y92.authored_mapillary_object_decisions.v1``.  One row per
decided object::

    {"decision_id": "MLY-4876-001",
     "mapillary_object_id": "549495619815002",
     "action": "reject" | "minimap_only" | "move_to",
     "basis": "...what was looked at and why...",
     "reviewed_by": "...", "reviewed_on": "YYYY-MM-DD",
     "evidence": ["repo-relative picture or measurement paths"],
     "move_to": {"E": 379751.0, "N": 3938510.0}      # move_to only
    }

``reject``        the detection is not a world object (duplicate, wrong
                  object): the plan row is an intentional omission.
``minimap_only``  the detection is real but must not be built: the plan row is
                  a minimap-only disposition.
``move_to``       the detection is real but its position is wrong: the plan
                  uses the given EPSG:32654 E,N for attachment.

A non-Mapillary feature (an OSM entrance gate that the infrastructure stage
already draws) is decided with ``target_feature_id`` instead of
``mapillary_object_id``; every row names exactly one of the two.  A decision
that names a feature the run does not carry refuses the run, so an approval
written for another world cannot sit silently in this one.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SCHEMA = "y92.authored_mapillary_object_decisions.v1"
ACTIONS = ("reject", "minimap_only", "move_to")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class MapillaryDecisionInvalid(ValueError):
    """The authored Mapillary decision document cannot drive the plan."""


def _text(row: dict, field: str, source, index) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise MapillaryDecisionInvalid(
            f"{source} row {index} needs non-empty text in {field!r}")
    return value


def target_feature_id(row: dict) -> str:
    """The canonical feature id a decision row targets."""
    if row.get("mapillary_object_id"):
        return f"feature:mapillary_object/{row['mapillary_object_id']}"
    return str(row["target_feature_id"])


def load_mapillary_decisions(path, *, known_feature_ids=None) -> dict:
    """Load and validate the document; return ``{feature_id: row}``.

    ``known_feature_ids`` (this run's canonical ids) makes a decision that
    names a feature the run lacks a refusal."""
    if path is None:
        return {}
    source = Path(path)
    if not source.is_file():
        raise MapillaryDecisionInvalid(f"decision document absent: {source}")
    doc = json.loads(source.read_text(encoding="utf-8"))
    if doc.get("schema") != SCHEMA:
        raise MapillaryDecisionInvalid(
            f"{source} schema {doc.get('schema')!r}; expected {SCHEMA!r}")
    table: dict[str, dict] = {}
    for index, row in enumerate(doc.get("decisions") or []):
        _text(row, "decision_id", source, index)
        _text(row, "basis", source, index)
        _text(row, "reviewed_by", source, index)
        if not _DATE.match(str(row.get("reviewed_on") or "")):
            raise MapillaryDecisionInvalid(
                f"{source} row {index} reviewed_on must be YYYY-MM-DD")
        if row.get("action") not in ACTIONS:
            raise MapillaryDecisionInvalid(
                f"{source} row {index} action {row.get('action')!r} not in "
                f"{ACTIONS}")
        has_mly = bool(row.get("mapillary_object_id"))
        has_osm = bool(row.get("target_feature_id"))
        if has_mly == has_osm:
            raise MapillaryDecisionInvalid(
                f"{source} row {index} must name exactly one of "
                "mapillary_object_id or target_feature_id")
        if has_mly and not str(row["mapillary_object_id"]).isdigit():
            raise MapillaryDecisionInvalid(
                f"{source} row {index} mapillary_object_id must be digits")
        if not row.get("evidence"):
            raise MapillaryDecisionInvalid(
                f"{source} row {index} needs at least one evidence path")
        if row["action"] == "move_to":
            move = row.get("move_to") or {}
            if not all(isinstance(move.get(k), (int, float))
                       for k in ("E", "N")):
                raise MapillaryDecisionInvalid(
                    f"{source} row {index} move_to needs numeric E and N")
        fid = target_feature_id(row)
        if fid in table:
            raise MapillaryDecisionInvalid(
                f"{source} row {index} decides {fid} a second time")
        if known_feature_ids is not None and fid not in known_feature_ids:
            raise MapillaryDecisionInvalid(
                f"{source} row {index} names {fid}, which this run's "
                "canonical world does not carry")
        table[fid] = row
    return table
