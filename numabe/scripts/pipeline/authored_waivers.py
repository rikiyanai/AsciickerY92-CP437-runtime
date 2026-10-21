#!/usr/bin/env python3
"""Authored representation waivers: a proposed or approved omission, on the record.

WHY THIS EXISTS
===============
``representation_policy.debt_policy_by_importance`` has named three lawful
exits from ``route_visible_C`` since FL-4809 PLAN-02::

    observation_owed blocks evidence_complete until observation,
    authored waiver, or explicit synthetic proxy

Two of the three were implemented.  ``compile_representation_plan`` emits an
observed representation when the evidence supports one, and R-PROXY reads the
``semantic_identity_proxy`` table for an explicit stand-in.  The authored
waiver existed only inside that policy string: no document declared one, no
loader read one, no rule consumed one.  A feature that owed an observation the
world could not supply had exactly two outcomes -- block the run, or be
silently absorbed by a default.

That gap is what made R-STREET-OMIT load-bearing.  It drops an unresolved
street sighting with no defensible authored representation, but only while the
sighting is ``background``, and ``background`` was the answer every feature
outside the 17 B/C triage rows fell to.  So the omission was real and the
approval was implicit: nobody decided, the default decided.

WHAT v1 GOT WRONG, AND WHY IT IS THE WHOLE POINT
================================================
RETRACTED -- v1 of this schema could express exactly one kind of row: an
approval.  ``REQUIRED_ROW_FIELDS`` demanded ``approved_by``, ``approved_on``
and ``approval_record`` on every row, so writing down a *proposed* omission was
not representable.  The first document written against it duly recorded
``approved_by: operator`` for a decision no operator had made: the operator had
selected "Build the authored-waiver mechanism" from a menu whose own preview
wrote the approver as the placeholder ``<operator>``, and who was never shown
the justification that ended up in the ``reason`` field.

A format that cannot say "proposed" converts every proposal into an approval at
the moment it is written down.  That is not a documentation slip, it is the
schema doing the only thing it could.  So the status axis below is not extra
metadata; it is the repair.

THREE STATUSES
==============
``proposed``
    An omission someone is arguing for.  It carries no approver -- naming one
    is a hard refusal, because that is exactly the laundering v1 performed.
    **A proposed waiver discharges nothing.**  It does not enter the table the
    planner consumes, so the feature keeps owing its observation and the run
    keeps blocking.  Writing a proposal must never be a way to make a build
    pass.

``approved``
    A human decided.  It carries a resolvable principal, an ISO date, and an
    approval record that resolves to one retrievable decision -- not to a lane,
    a role noun, or a free string.

``superseded``
    History.  It keeps its original approver and reason, names the waiver that
    replaced it, and is inert.  v1 refused a second row per feature outright,
    which meant the only way to revise an approval was to overwrite it and
    erase who had approved what.

WHAT A WAIVER IS NOT
====================
It is not a way to make a build pass.  It removes no evidence and lowers no
threshold.  The waived row keeps its observation, keeps ``asserts_physical_
presence`` false, and is emitted as ``intentional_omission`` -- the same
disposition the background default produced -- with one difference that is the
entire point: ``policy_rule`` names the approval record instead of naming a
default.  A reader can ask who decided, when, and why, and get an answer.

It is also not a proxy.  A proxy puts a marked stand-in in the world; a waiver
puts nothing there and says so.  Choosing a waiver over a proxy is a statement
that no honest stand-in exists, not that one was too much work.

REFUSALS THAT KEEP IT HONEST
============================
An approval that outlives its justification is worse than no approval, because
it reads as considered.  So:

* a waiver naming a feature this run does not carry is refused.  It was
  written against a different world and cannot be known to still apply.
* a waiver this run never exercises is refused.  The feature stopped owing
  anything -- it was observed, proxied, or dropped -- and the approval is now
  being carried past the condition it approved.
* a waiver whose ``conditions_sha256`` no longer matches the world is refused.
  The approver agreed to omit *this* feature, with *these* observations, under
  *these* presentation requirements, given *this* debt decision.  Change any of
  them and the thing they agreed to is not the thing in front of the planner.
* a proposal that names an approver is refused, and so is an approval whose
  approver is a role noun or whose record is a bare lane id.

Every refusal is a refusal to guess.  A waiver is the one input in this
pipeline carrying a human's judgement rather than a measurement, so a malformed
one cannot be repaired by inference without inventing the judgement it was
supposed to record.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from pathlib import Path

SCHEMA = "y92.authored_representation_waivers.v2"

STATUSES = ("proposed", "approved", "superseded")

# Carried by every row, whatever its status. ``conditions_sha256`` is required
# on a proposal too: a proposal that does not say what world it was argued
# against cannot later be approved for that world.
COMMON_ROW_FIELDS = (
    "waiver_id",
    "feature_id",
    "disposition_waived",
    "reason",
    "status",
    "proposed_by",
    "proposed_on",
    "conditions_sha256",
)

# Required when, and only when, status is ``approved``. Refused on a proposal.
APPROVAL_ROW_FIELDS = ("approved_by", "approved_on", "approval_record")

# Required when status is ``superseded``. Such a row MAY also keep its
# approval fields -- that is the audit trail the v1 duplicate rule destroyed.
SUPERSESSION_ROW_FIELDS = ("superseded_by", "superseded_on",
                           "supersession_reason")

ALL_ROW_FIELDS = (COMMON_ROW_FIELDS + APPROVAL_ROW_FIELDS
                  + SUPERSESSION_ROW_FIELDS)

# Only a blocking debt can be waived. Waiving a disposition that already has
# a representation would delete geometry the plan meant to emit, which is a
# different operation wearing this one's name.
WAIVABLE_DISPOSITIONS = ("observation_owed",)

# A principal has to resolve to someone. "operator", "agent" and "user" name a
# role, and two different people writing "operator" are indistinguishable on
# the record, which defeats the only thing an approval is for.
PRINCIPAL_PATTERN = re.compile(r"^[^<>@]+ <[^<>@\s]+@[^<>@\s]+>$")
ROLE_NOUNS = frozenset({"operator", "agent", "user", "admin", "me", "human",
                        "claude", "assistant", "owner", "maintainer"})

# An approval record must resolve to one retrievable decision. "FL-4809" names
# a lane spanning many commits and many attempts; it answers "which project",
# not "which decision".
APPROVAL_RECORD_FORMS = (
    # the operator's own decision turn, in a session transcript
    ("transcript", re.compile(
        r"^transcript:[0-9a-f-]{36}:[0-9a-f-]{36}$")),
    # the commit that carried the approval into the repository
    ("commit", re.compile(r"^commit:[0-9a-f]{40}$")),
    # a failure-log attempt, pinned to the commit that appended it
    ("fl", re.compile(r"^fl:FL-\d+:[0-9a-f]{40}$")),
)

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class AuthoredWaiverInvalid(ValueError):
    """The waiver document cannot be trusted, so the run does not start."""


def condition_digest(*, feature, observations, requirements,
                     debt_decision) -> str:
    """Hash exactly what an approver is agreeing to.

    One hashing law, used by the producer that writes a row and by the planner
    that honours it. The four members are the four things that, if they moved,
    would make the approval describe a different situation:

    * ``feature`` -- the canonical row: its class, geometry, labels, evidence;
    * ``observations`` -- what was actually seen, and by whom;
    * ``requirements`` -- what the presentation law demands of it;
    * ``debt_decision`` -- the verdict that made it owe anything at all.

    Deliberately NOT included: anything about the rest of the world. An
    approval to omit one camera must not expire because an unrelated building
    gained a facade.
    """
    payload = {
        "feature": feature,
        "observations": observations,
        "requirements": requirements,
        "debt_decision": debt_decision,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _require_text(row, field, source, index):
    value = row.get(field)
    if not isinstance(value, str):
        raise AuthoredWaiverInvalid(
            f"authored waiver document {source} row {index} field {field!r} "
            f"is {type(value).__name__}, expected a string; a waiver field is "
            "read by a person, so it may not be a structure that only happens "
            "to render")
    if not value.strip():
        raise AuthoredWaiverInvalid(
            f"authored waiver document {source} row {index} leaves {field!r} "
            "blank; an approval without it cannot be attributed")
    return value


def _require_date(row, field, source, index):
    value = _require_text(row, field, source, index)
    try:
        datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise AuthoredWaiverInvalid(
            f"authored waiver document {source} row {index} field {field!r} "
            f"is {value!r}, which is not an ISO calendar date (YYYY-MM-DD); "
            "an approval that cannot be placed in time cannot be aged, "
            "ordered, or reviewed") from exc
    return value


def _require_absent(row, fields, source, index, why):
    present = sorted(f for f in fields if f in row)
    if present:
        raise AuthoredWaiverInvalid(
            f"authored waiver document {source} row {index} carries "
            f"{', '.join(present)}; {why}")


def load_authored_waivers(path, *, known_feature_ids=None) -> dict:
    """Read the document and return ``{feature_id: row}`` for APPROVED rows.

    Proposed and superseded rows are validated and then withheld. They are
    history and argument respectively; neither discharges a debt, so neither
    reaches the planner.
    """
    source = Path(path)
    try:
        doc = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AuthoredWaiverInvalid(
            f"authored waiver document unreadable: {source}: {exc}") from exc
    if not isinstance(doc, dict):
        raise AuthoredWaiverInvalid(
            f"authored waiver document is not an object: {source}")
    schema = doc.get("schema")
    if schema != SCHEMA:
        raise AuthoredWaiverInvalid(
            f"authored waiver document {source} declares schema "
            f"{schema!r}, expected {SCHEMA!r}")
    rows = doc.get("waivers")
    if not isinstance(rows, list):
        raise AuthoredWaiverInvalid(
            f"authored waiver document {source} carries no waivers list")

    approved: dict[str, dict] = {}
    active_by_fid: dict[str, str] = {}
    by_waiver_id: dict[str, dict] = {}
    validated: list[dict] = []

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise AuthoredWaiverInvalid(
                f"authored waiver document {source} row {index} "
                "is not an object")
        # An unknown key is refused rather than ignored. The common way to
        # lose an approver is to misspell the field that carries one, and a
        # loader that ignores extras reports that as a complete waiver.
        unknown = sorted(set(row) - set(ALL_ROW_FIELDS))
        if unknown:
            raise AuthoredWaiverInvalid(
                f"authored waiver document {source} row {index} carries "
                f"unrecognised field(s) {', '.join(unknown)}; expected from "
                f"{', '.join(ALL_ROW_FIELDS)}")

        for field in COMMON_ROW_FIELDS:
            if field == "proposed_on":
                _require_date(row, field, source, index)
            else:
                _require_text(row, field, source, index)

        status = row["status"]
        if status not in STATUSES:
            raise AuthoredWaiverInvalid(
                f"authored waiver document {source} row {index} declares "
                f"status {status!r}; expected one of {', '.join(STATUSES)}")

        digest = row["conditions_sha256"]
        if not SHA256_PATTERN.match(digest):
            raise AuthoredWaiverInvalid(
                f"authored waiver document {source} row {index} field "
                f"'conditions_sha256' is {digest!r}, expected 64 lowercase "
                "hex characters; without it the approval is not bound to the "
                "world it was given for")

        disposition = row["disposition_waived"]
        if disposition not in WAIVABLE_DISPOSITIONS:
            raise AuthoredWaiverInvalid(
                f"authored waiver document {source} row {index} waives "
                f"{disposition!r}; only {WAIVABLE_DISPOSITIONS} may be waived")

        waiver_id = row["waiver_id"]
        if waiver_id in by_waiver_id:
            raise AuthoredWaiverInvalid(
                f"authored waiver document {source} uses waiver_id "
                f"{waiver_id!r} twice; an approval that cannot be named "
                "uniquely cannot be cited or superseded")
        by_waiver_id[waiver_id] = row

        if status == "proposed":
            # The refusal that v1 needed and did not have.
            _require_absent(
                row, APPROVAL_ROW_FIELDS, source, index,
                "a proposed omission has no approver by definition, and "
                "naming one records a decision that was not made")
            _require_absent(
                row, SUPERSESSION_ROW_FIELDS, source, index,
                "a proposal cannot be superseded before it is decided")
        elif status == "approved":
            for field in APPROVAL_ROW_FIELDS:
                if field == "approved_on":
                    _require_date(row, field, source, index)
                else:
                    _require_text(row, field, source, index)
            _require_absent(
                row, SUPERSESSION_ROW_FIELDS, source, index,
                "an approval that has been superseded must say so in its "
                "status, not only in its fields")
            principal = row["approved_by"]
            if (principal.strip().lower() in ROLE_NOUNS
                    or not PRINCIPAL_PATTERN.match(principal)):
                raise AuthoredWaiverInvalid(
                    f"authored waiver document {source} row {index} is "
                    f"approved by {principal!r}, which does not resolve to a "
                    "person; use 'Name <email>'. A role noun cannot be asked "
                    "what they agreed to")
            record = row["approval_record"]
            if not any(form.match(record) for _, form in
                       APPROVAL_RECORD_FORMS):
                shapes = ", ".join(name for name, _ in APPROVAL_RECORD_FORMS)
                raise AuthoredWaiverInvalid(
                    f"authored waiver document {source} row {index} cites "
                    f"approval_record {record!r}, which does not resolve to a "
                    f"single retrievable decision; expected one of: {shapes}. "
                    "A bare lane id names the project, not the decision")
        else:  # superseded
            for field in SUPERSESSION_ROW_FIELDS:
                if field == "superseded_on":
                    _require_date(row, field, source, index)
                else:
                    _require_text(row, field, source, index)
            if row["superseded_by"] == waiver_id:
                raise AuthoredWaiverInvalid(
                    f"authored waiver document {source} row {index} is "
                    "superseded by itself")

        fid = row["feature_id"]
        if status != "superseded":
            if fid in active_by_fid:
                raise AuthoredWaiverInvalid(
                    f"authored waiver document {source} carries two live rows "
                    f"for {fid} ({active_by_fid[fid]}, {waiver_id}); one "
                    "feature has one current disposition, so retire the older "
                    "row to status 'superseded' instead of leaving both")
            active_by_fid[fid] = waiver_id
            if status == "approved":
                approved[fid] = dict(row)
        validated.append(row)

    # Resolve supersession pointers only once every waiver_id is known, so
    # order inside the document does not matter.
    for index, row in enumerate(validated):
        if row["status"] != "superseded":
            continue
        target = row["superseded_by"]
        if target not in by_waiver_id:
            raise AuthoredWaiverInvalid(
                f"authored waiver document {source} row {index} is superseded "
                f"by {target!r}, which is not a waiver in this document; a "
                "retired approval must point at the decision that replaced it")

    if known_feature_ids is not None:
        # Superseded rows are exempt on purpose: history is allowed to mention
        # a feature the world has since dropped. Refusing it would force the
        # audit trail to be deleted, which is what this schema exists to stop.
        live = {row["feature_id"] for row in validated
                if row["status"] != "superseded"}
        unknown_fids = sorted(live - set(known_feature_ids))
        if unknown_fids:
            shown = ", ".join(unknown_fids[:5])
            raise AuthoredWaiverInvalid(
                f"authored waiver document {source} carries "
                f"{len(unknown_fids)} live row(s) for feature(s) this run "
                f"does not carry, first: {shown}; the waiver was written "
                "against a different world and cannot be known to still apply")
    return approved


def load_waiver_document(path) -> list[dict]:
    """Every validated row, whatever its status, in document order.

    For receipts, audits and tests that must see the proposals and the retired
    approvals, not only the live ones.
    """
    source = Path(path)
    load_authored_waivers(source)
    doc = json.loads(source.read_text(encoding="utf-8"))
    return list(doc["waivers"])


def assert_all_exercised(table: dict, exercised) -> None:
    """Refuse an approval the run never needed.

    Called after planning, when the set of rows that actually took the waiver
    branch is known. An unexercised approval means the feature stopped owing
    an observation, so the approval now describes a condition that no longer
    exists. Carrying it silently is how a considered decision decays into an
    unexamined one.

    ``table`` holds approved rows only, so a proposal never trips this.
    """
    unused = sorted(set(table) - set(exercised))
    if unused:
        shown = ", ".join(unused[:5])
        raise AuthoredWaiverInvalid(
            f"{len(unused)} authored waiver(s) were never exercised, first: "
            f"{shown}; the feature no longer owes an observation, so retire "
            "the approval to status 'superseded' rather than carrying it past "
            "its condition")
