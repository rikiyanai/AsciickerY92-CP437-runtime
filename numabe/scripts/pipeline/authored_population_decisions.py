"""Authored operator decisions on population placements that cannot resolve.

FL-4876 DECIDED-OMISSION-01.

The planner can say a placement is OWED and it can say a placement is an
INTENTIONAL OMISSION, and the emission manifest treats those two very
differently: an omission is a settled outcome, an owed observation blocks the
build. The rule the whole lane rests on is

    an owed observation is not a decided omission

and that rule is right. The gap is that nothing could ever turn the first into
the second. Every ``intentional_omission`` in the planner is set by a RULE.
There was no door an operator could walk through to decide one, so three
unresolvable observations blocked 7413 emitted instances with no lawful way to
settle them.

This module is that door, and it is deliberately narrow:

* A decision names ONE placement id. There is no pattern, glob or class-wide
  form, because a decision that can sweep is a tolerance change wearing a
  decision's clothes.
* A decision names the CAUSE it was made against. If the planner later computes
  a different cause for that placement, the decision does not apply and the
  placement goes back to owed. A decision about a street light standing inside
  a building must not silently settle that same light once it is standing in a
  road instead.
* A decision records WHO decided, WHEN, and the VERBATIM instruction. The
  authored-waiver owner beside this one was written after a decision was
  attributed to an operator who had never made it; this one carries the words
  so a reader can check.
* A decision NEVER places anything, moves anything, or asserts the feature is
  absent from the world. It settles an accounting outcome and nothing else.
  The object is not built, and the record says why.
"""

from __future__ import annotations

import json
from pathlib import Path

SCHEMA = "y92.authored_population_decisions.v1"

STATUSES = ("proposed", "decided", "superseded")

REQUIRED_FIELDS = (
    "decision_id",
    "placement_id",
    "disposition_decided",
    "against_cause",
    "status",
    "reason",
    "proposed_by",
    "proposed_on",
)

# Required when, and only when, status is "decided". Refused on a proposal,
# exactly as the authored-waiver owner refuses an approver on a proposal.
DECISION_FIELDS = ("decided_by", "decided_on", "decision_record")

# The only disposition an operator may decide here. Deciding a placement into
# "emitted" would be asserting geometry nobody built.
DECIDABLE = ("intentional_omission",)


class AuthoredDecisionInvalid(ValueError):
    """The decisions document cannot be trusted, so the run does not start."""


def load_authored_decisions(path) -> dict:
    """Return ``{placement_id: row}`` for DECIDED rows only.

    A proposed decision discharges nothing, the same way a proposed waiver
    discharges nothing.
    """
    if path is None:
        return {}
    p = Path(path)
    if not p.is_file():
        raise AuthoredDecisionInvalid(
            f"authored population decisions document is absent: {p}")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AuthoredDecisionInvalid(f"{p} is not JSON: {exc}") from exc
    if doc.get("schema") != SCHEMA:
        raise AuthoredDecisionInvalid(
            f"{p} schema {doc.get('schema')!r}; expected {SCHEMA!r}")
    rows = doc.get("decisions")
    if not isinstance(rows, list):
        raise AuthoredDecisionInvalid(f"{p} has no 'decisions' list")
    out: dict[str, dict] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise AuthoredDecisionInvalid(f"{p} row {index} is not an object")
        for field in REQUIRED_FIELDS:
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise AuthoredDecisionInvalid(
                    f"{p} row {index} field {field!r} must be non-empty text")
        status = row["status"]
        if status not in STATUSES:
            raise AuthoredDecisionInvalid(
                f"{p} row {index} status {status!r}; expected one of "
                f"{', '.join(STATUSES)}")
        if row["disposition_decided"] not in DECIDABLE:
            raise AuthoredDecisionInvalid(
                f"{p} row {index} disposition_decided "
                f"{row['disposition_decided']!r}; only "
                f"{', '.join(DECIDABLE)} may be decided here")
        if status == "decided":
            for field in DECISION_FIELDS:
                if not isinstance(row.get(field), str) or not row[field].strip():
                    raise AuthoredDecisionInvalid(
                        f"{p} row {index} is decided and must carry "
                        f"non-empty {field!r}")
            pid = row["placement_id"]
            if pid in out:
                raise AuthoredDecisionInvalid(
                    f"{p} decides {pid!r} more than once")
            out[pid] = row
        else:
            for field in DECISION_FIELDS:
                if field in row:
                    raise AuthoredDecisionInvalid(
                        f"{p} row {index} is {status!r} and must not carry "
                        f"{field!r}: a proposal that names a decider reads as "
                        "a decision nobody made")
    return out


def decision_for(decisions: dict, placement_id: str, cause: str) -> dict | None:
    """The decision that applies to this placement AND this cause, if any.

    A decision made against one cause never settles a different one.
    """
    row = decisions.get(str(placement_id))
    if row is None:
        return None
    if str(row.get("against_cause")) != str(cause):
        return None
    return row
