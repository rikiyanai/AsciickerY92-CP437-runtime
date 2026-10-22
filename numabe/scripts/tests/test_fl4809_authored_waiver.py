#!/usr/bin/env python3
"""FL-4809 R2: the third lawful exit from route_visible_C, schema v2.

``debt_policy_by_importance`` has named three ways out of a blocking
observation debt since PLAN-02 -- observation, authored waiver, explicit
synthetic proxy -- and only two were implemented.  The waiver lived in a
policy string.  So a feature that owed an observation the world could not
supply had two outcomes: block the run, or fall into R-STREET-OMIT, which
drops an unresolved sighting only while it is ``background``.

Measuring importance against the route removed that default from exactly one
feature (``feature:mapillary_object/937664923692529``, a Mapillary
``object--cctv-camera``: the single row of 569 that JEV routed to human review,
with ``asset_choice: none`` at confidence 1.0 and no camera in the 84-module
catalog).  The blocker it produced is correct.  The fix is not to give the
default back.

What is pinned here:

* a PROPOSAL discharges nothing.  Planning the lane with the committed
  document gives the same incomplete rows as planning with no document at
  all, so writing a proposal can never be a way to make a build pass;
* the waived row is NOT the background-omission row -- the regression that
  matters, because the two share a disposition and differ only in whether a
  human decided;
* the waiver is checked AFTER the background branch, so it applies only where
  the row would otherwise block.  With the route receipt absent the camera is
  ``background``, R-STREET-OMIT handles it, the approval is never exercised,
  and the run is refused.  An approval is valid only while the configuration
  that created the debt is in force;
* the approval is bound to ``conditions_sha256``, and the blocking row
  publishes the digest an approver would be signing, so an approval nobody
  can compute cannot be demanded;
* every refusal that keeps an approval attributable: a non-string field, a
  date that is not a date, a role-noun approver, a bare lane id as the
  approval record, two live rows for one feature, a supersession pointing
  nowhere;
* the approval joins the provenance tuple, so one operator's decision cannot
  be replayed into a run they never saw.
"""

from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.authored_waivers import (  # noqa: E402
    SCHEMA,
    WAIVABLE_DISPOSITIONS,
    AuthoredWaiverInvalid,
    assert_all_exercised,
    condition_digest,
    load_authored_waivers,
    load_waiver_document,
)
from scripts.pipeline.representation_plan_provenance import (  # noqa: E402
    OPTIONAL_INPUT_FIELDS,
    PlanWorldMismatch,
    current_hashes,
    file_sha256,
    verify as verify_tuple,
)

CAMERA = "feature:mapillary_object/937664923692529"

# A principal that resolves to a person, and the three approval-record forms
# that resolve to one retrievable decision.
PRINCIPAL = "Riki Yanai Hernandez <info@yuuz.co>"
COMMIT_RECORD = "commit:" + "b3" * 20
TRANSCRIPT_RECORD = ("transcript:01234567-89ab-cdef-0123-456789abcdef"
                     ":fedcba98-7654-3210-fedc-ba9876543210")
FL_RECORD = "fl:FL-4809:" + "c7" * 20

# The digest the committed proposal was argued against. The planner
# recomputes it from the world; the lane tests below check the two agree.
PROPOSED_DIGEST = (
    "266ef3f8fa3f339b0d642ea77c6100361db98207c4a41a84afaa3eedb1ef9cf2")


def _row(fid=CAMERA, *, status="approved", waiver_id="WVR-TEST-001", **over):
    """One schema-v2 row, complete for its status before ``over`` is applied."""
    row = {"waiver_id": waiver_id, "feature_id": fid,
           "disposition_waived": "observation_owed",
           "reason": "no honest representation exists today",
           "status": status,
           "proposed_by": "agent:test session_fl4809",
           "proposed_on": "2026-09-23",
           "conditions_sha256": PROPOSED_DIGEST}
    if status == "approved":
        row.update({"approved_by": PRINCIPAL, "approved_on": "2026-09-23",
                    "approval_record": COMMIT_RECORD})
    elif status == "superseded":
        row.update({"superseded_by": "WVR-TEST-002",
                    "superseded_on": "2026-09-23",
                    "supersession_reason": "re-reviewed after new frames"})
    row.update(over)
    return row


def _doc(tmp_path: Path, rows, schema=SCHEMA) -> Path:
    path = tmp_path / "authored_representation_waivers.json"
    path.write_text(json.dumps({"schema": schema, "waivers": rows}),
                    encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# the document is refused rather than guessed at
# --------------------------------------------------------------------------

def test_wrong_schema_is_refused(tmp_path):
    """v1 could not say 'proposed', so a v1 document may not be read as v2.

    Every v1 row asserted an approval. Accepting one here would re-import the
    laundering the status axis exists to stop.
    """
    path = _doc(tmp_path, [_row()],
                schema="y92.authored_representation_waivers.v1")
    with pytest.raises(AuthoredWaiverInvalid, match="schema"):
        load_authored_waivers(path)


@pytest.mark.parametrize("field", ["waiver_id", "reason", "proposed_by",
                                   "approved_by", "approved_on",
                                   "approval_record"])
def test_an_unattributable_waiver_is_refused(tmp_path, field):
    """An approval missing one of its fields is not a weaker approval.

    Blank means the same thing as absent: nobody can be asked what they
    agreed to, so there is nothing to honour.
    """
    path = _doc(tmp_path, [_row(**{field: ""})])
    with pytest.raises(AuthoredWaiverInvalid, match=field):
        load_authored_waivers(path)


@pytest.mark.parametrize("field,value", [
    ("approved_by", ["Riki Yanai Hernandez <info@yuuz.co>"]),
    ("approved_by", {"name": "Riki", "email": "info@yuuz.co"}),
    ("approved_by", 4809),
    ("approved_by", True),
    ("reason", ["no honest representation exists today"]),
    ("proposed_by", 1),
    ("conditions_sha256", {"sha256": PROPOSED_DIGEST}),
    ("status", False),
])
def test_a_field_that_is_not_a_string_is_refused(tmp_path, field, value):
    """A waiver field is read by a person, not rendered by one.

    A list of one approver, or a dict that happens to str() into something
    plausible, would put an unreadable structure where the record of a human
    decision belongs. The refusal names the type so the author can see what
    they actually wrote.
    """
    path = _doc(tmp_path, [_row(**{field: value})])
    with pytest.raises(AuthoredWaiverInvalid) as err:
        load_authored_waivers(path)
    assert field in str(err.value)
    assert type(value).__name__ in str(err.value)


@pytest.mark.parametrize("field", ["proposed_on", "approved_on"])
@pytest.mark.parametrize("value", ["yesterday", "2026-13-45"])
def test_a_date_that_is_not_a_date_is_refused(tmp_path, field, value):
    """An approval that cannot be placed in time cannot be aged or reviewed.

    'yesterday' is relative to a reading that already happened, and
    '2026-13-45' is a date-shaped string that names no day.
    """
    path = _doc(tmp_path, [_row(**{field: value})])
    with pytest.raises(AuthoredWaiverInvalid, match=field):
        load_authored_waivers(path)


def test_an_unrecognised_field_is_refused(tmp_path):
    """The usual way to lose an approver is to misspell the field.

    A loader that ignores extras would report that as a complete waiver.
    """
    row = _row()
    row["aproved_by"] = PRINCIPAL
    path = _doc(tmp_path, [row])
    with pytest.raises(AuthoredWaiverInvalid, match="aproved_by"):
        load_authored_waivers(path)


def test_an_unknown_status_is_refused(tmp_path):
    """Three statuses, and no fourth invented at the document.

    A status the loader does not know would be validated against nothing and
    then withheld or honoured by accident.
    """
    path = _doc(tmp_path, [_row(status="pending")])
    with pytest.raises(AuthoredWaiverInvalid, match="status"):
        load_authored_waivers(path)


def test_waiving_a_represented_disposition_is_refused(tmp_path):
    """Only a blocking debt is waivable.

    Waiving mesh_facade would delete geometry the plan meant to emit, which
    is a different operation wearing this one's name.
    """
    path = _doc(tmp_path, [_row(disposition_waived="mesh_facade")])
    with pytest.raises(AuthoredWaiverInvalid, match="only"):
        load_authored_waivers(path)


def test_a_duplicate_waiver_id_is_refused(tmp_path):
    """An approval that cannot be named uniquely cannot be cited or retired."""
    path = _doc(tmp_path, [_row(), _row(fid="feature:other/1")])
    with pytest.raises(AuthoredWaiverInvalid, match="twice"):
        load_authored_waivers(path)


@pytest.mark.parametrize("value", ["", "abc", PROPOSED_DIGEST.upper(),
                                   "z" * 64])
def test_a_malformed_conditions_digest_is_refused(tmp_path, value):
    """Without a well-formed digest the approval is bound to no world.

    An approval that cannot be compared against the run it is applied to is
    an approval that can never expire.
    """
    path = _doc(tmp_path, [_row(conditions_sha256=value)])
    with pytest.raises(AuthoredWaiverInvalid, match="conditions_sha256"):
        load_authored_waivers(path)


def test_a_live_waiver_against_another_world_is_refused(tmp_path):
    """Written against a canonical set this run does not carry."""
    path = _doc(tmp_path, [_row(fid="feature:gone/1")])
    with pytest.raises(AuthoredWaiverInvalid, match="does not carry"):
        load_authored_waivers(path, known_feature_ids=[CAMERA])


def test_a_valid_document_loads(tmp_path):
    """The approved row reaches the planner with its approval record intact."""
    path = _doc(tmp_path, [_row()])
    table = load_authored_waivers(path, known_feature_ids=[CAMERA])
    assert table[CAMERA]["approval_record"] == COMMIT_RECORD


def test_an_unexercised_waiver_is_refused():
    """The feature stopped owing anything; the approval outlived its condition.

    The refusal says to retire the row to 'superseded' rather than delete it,
    because deleting is how the record of who approved what disappears.
    """
    with pytest.raises(AuthoredWaiverInvalid, match="never exercised") as err:
        assert_all_exercised({CAMERA: _row()}, set())
    assert "superseded" in str(err.value)


def test_an_exercised_waiver_passes():
    assert assert_all_exercised({CAMERA: _row()}, {CAMERA}) is None


# --------------------------------------------------------------------------
# a proposal is an argument, not a decision
# --------------------------------------------------------------------------

@pytest.mark.parametrize("field,value", [
    ("approved_by", PRINCIPAL),
    ("approved_on", "2026-09-23"),
    ("approval_record", COMMIT_RECORD),
])
def test_a_proposal_that_names_an_approver_is_refused(tmp_path, field, value):
    """This is the v1 laundering, refused at the schema.

    v1 could express only an approval, so the first document written against
    it recorded approved_by 'operator' for a decision no operator had made.
    A proposal has no approver by definition; naming one records a decision
    that did not happen.
    """
    path = _doc(tmp_path, [_row(status="proposed", **{field: value})])
    with pytest.raises(AuthoredWaiverInvalid, match=field):
        load_authored_waivers(path)


def test_a_proposal_is_withheld_from_the_planner(tmp_path):
    """A proposal discharges nothing.

    It validates, it is kept in the document, and it does not enter the table
    the planner consumes. Writing a proposal must never be a way to make a
    build pass.
    """
    path = _doc(tmp_path, [_row(status="proposed")])
    assert load_authored_waivers(path, known_feature_ids=[CAMERA]) == {}
    assert [r["status"] for r in load_waiver_document(path)] == ["proposed"]


def test_the_table_holds_only_approved_rows_and_the_document_holds_all(
        tmp_path):
    """Two readers, two jobs.

    The planner must see only what a human decided. An audit must see the
    argument and the retired approvals too, or the record is only the part
    that currently happens to be in force.
    """
    rows = [
        _row(waiver_id="WVR-A", status="approved"),
        _row(waiver_id="WVR-B", status="proposed", fid="feature:other/1"),
        _row(waiver_id="WVR-C", status="superseded", fid="feature/third",
             superseded_by="WVR-A"),
    ]
    path = _doc(tmp_path, rows)
    table = load_authored_waivers(path)
    assert set(table) == {CAMERA}
    assert table[CAMERA]["waiver_id"] == "WVR-A"
    assert [r["waiver_id"] for r in load_waiver_document(path)] == [
        "WVR-A", "WVR-B", "WVR-C"]


# --------------------------------------------------------------------------
# an approver has to be a person, and a record has to be one decision
# --------------------------------------------------------------------------

@pytest.mark.parametrize("principal", ["operator", "agent", "user", "Claude",
                                       " maintainer ", "info@yuuz.co",
                                       "Riki Yanai Hernandez"])
def test_an_approver_that_does_not_resolve_to_a_person_is_refused(
        tmp_path, principal):
    """Two people writing 'operator' are indistinguishable on the record.

    That defeats the only thing an approval is for, which is being able to
    ask the person what they agreed to.
    """
    path = _doc(tmp_path, [_row(approved_by=principal)])
    with pytest.raises(AuthoredWaiverInvalid,
                       match="does not resolve to a person"):
        load_authored_waivers(path)


def test_a_named_principal_is_accepted(tmp_path):
    """'Name <email>' resolves to someone who can be asked."""
    path = _doc(tmp_path, [_row(approved_by=PRINCIPAL)])
    assert load_authored_waivers(path)[CAMERA]["approved_by"] == PRINCIPAL


@pytest.mark.parametrize("record", ["FL-4809", "fl:FL-4809", "the operator",
                                    "commit:abc123", "transcript:session"])
def test_an_approval_record_that_is_not_one_decision_is_refused(
        tmp_path, record):
    """A bare lane id names the project, not the decision.

    FL-4809 spans many commits and many attempts. Citing it answers 'which
    project', which is not a thing anybody can retrieve and read.
    """
    path = _doc(tmp_path, [_row(approval_record=record)])
    with pytest.raises(AuthoredWaiverInvalid,
                       match="single retrievable decision"):
        load_authored_waivers(path)


@pytest.mark.parametrize("record", [TRANSCRIPT_RECORD, COMMIT_RECORD,
                                    FL_RECORD])
def test_each_approval_record_form_is_accepted(tmp_path, record):
    """Three ways to point at one retrievable decision, and only three.

    The operator's own decision turn, the commit that carried it in, or a
    failure-log attempt pinned to the commit that appended it.
    """
    path = _doc(tmp_path, [_row(approval_record=record)])
    assert load_authored_waivers(path)[CAMERA]["approval_record"] == record


# --------------------------------------------------------------------------
# supersession: the audit trail v1 could not hold
# --------------------------------------------------------------------------

def test_a_superseded_row_is_kept_and_only_the_live_one_is_returned(tmp_path):
    """Revising an approval must not erase who approved what.

    v1 refused a second row per feature, so the only way to change a decision
    was to overwrite the record of the previous one.
    """
    rows = [_row(waiver_id="WVR-OLD", status="superseded",
                 superseded_by="WVR-NEW", approved_by=PRINCIPAL,
                 approved_on="2026-09-01", approval_record=FL_RECORD),
            _row(waiver_id="WVR-NEW", status="approved")]
    path = _doc(tmp_path, rows)
    table = load_authored_waivers(path, known_feature_ids=[CAMERA])
    assert set(table) == {CAMERA}
    assert table[CAMERA]["waiver_id"] == "WVR-NEW"
    assert [r["waiver_id"] for r in load_waiver_document(path)] == [
        "WVR-OLD", "WVR-NEW"]


def test_a_supersession_pointing_at_nothing_is_refused(tmp_path):
    """A retired approval must point at the decision that replaced it.

    Otherwise 'superseded' is just a way to make a row stop being checked.
    """
    rows = [_row(waiver_id="WVR-OLD", status="superseded",
                 superseded_by="WVR-NEVER-WRITTEN"),
            _row(waiver_id="WVR-NEW", status="approved")]
    path = _doc(tmp_path, rows)
    with pytest.raises(AuthoredWaiverInvalid, match="WVR-NEVER-WRITTEN"):
        load_authored_waivers(path)


def test_a_row_superseded_by_itself_is_refused(tmp_path):
    """A row that replaced itself names no successor and retires no decision."""
    path = _doc(tmp_path, [_row(waiver_id="WVR-OLD", status="superseded",
                                superseded_by="WVR-OLD")])
    with pytest.raises(AuthoredWaiverInvalid, match="superseded by itself"):
        load_authored_waivers(path)


def test_two_live_rows_for_one_feature_are_refused(tmp_path):
    """One feature has one current disposition.

    Two live rows means the planner would pick one by document order, which
    is not a decision anybody made.
    """
    rows = [_row(waiver_id="WVR-A"),
            _row(waiver_id="WVR-B", reason="a different reason")]
    path = _doc(tmp_path, rows)
    with pytest.raises(AuthoredWaiverInvalid, match="two live rows"):
        load_authored_waivers(path)


def test_a_superseded_row_may_name_a_feature_the_run_dropped(tmp_path):
    """History is exempt from the known-feature check on purpose.

    A retired approval describes a world that has already moved. Refusing it
    would force the audit trail to be deleted, which is the thing this schema
    exists to stop.
    """
    rows = [_row(waiver_id="WVR-OLD", fid="feature:gone/1",
                 status="superseded", superseded_by="WVR-NEW"),
            _row(waiver_id="WVR-NEW", status="approved")]
    path = _doc(tmp_path, rows)
    table = load_authored_waivers(path, known_feature_ids=[CAMERA])
    assert set(table) == {CAMERA}


# --------------------------------------------------------------------------
# the hashing law
# --------------------------------------------------------------------------

def _conditions():
    return {
        "feature": {"feature_id": CAMERA, "semantic_class": "street_object",
                    "confidence": 0.7},
        "observations": [{"observation_id": "obs:mapillary:mf/1"}],
        "requirements": {"presentation_requirements":
                         {"legibility_class": "secondary"},
                         "gameplay_authority": "presentation_only"},
        "debt_decision": {"lane": "street", "route": "human_review"},
    }


def test_the_digest_is_stable_for_the_same_conditions():
    """One hashing law, used by the producer and by the planner.

    If the two could disagree, an approval written correctly today would be
    refused tomorrow for no reason a reader could see.
    """
    assert condition_digest(**_conditions()) == condition_digest(
        **_conditions())
    assert re.fullmatch(r"[0-9a-f]{64}", condition_digest(**_conditions()))


@pytest.mark.parametrize("member", ["feature", "observations", "requirements",
                                    "debt_decision"])
def test_the_digest_moves_when_any_condition_moves(member):
    """Each of the four members is load-bearing.

    The approver agreed to omit THIS feature, with THESE observations, under
    THESE presentation requirements, given THIS debt verdict. A member that
    did not change the digest would be one an approval silently ignored.
    """
    base = _conditions()
    moved = _conditions()
    if member == "observations":
        moved[member] = moved[member] + [{"observation_id": "obs:new"}]
    else:
        moved[member] = dict(moved[member])
        moved[member]["injected"] = "changed"
    assert condition_digest(**base) != condition_digest(**moved)


# --------------------------------------------------------------------------
# the approval joins the provenance tuple
# --------------------------------------------------------------------------

TUPLE_FIELDS = ("canonical", "entities", "terrain_metadata", "rules",
                "module_catalog", "pack", "route", "fusion_reviews")


def _hashes(tmp_path: Path, *, with_waivers: bool):
    made = {}
    names = list(TUPLE_FIELDS) + (["authored_waivers"] if with_waivers else [])
    for name in names:
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps({"name": name}), encoding="utf-8")
        made[name] = path
    kwargs = {n: made[n] for n in TUPLE_FIELDS}
    if with_waivers:
        kwargs["authored_waivers"] = made["authored_waivers"]
    return made, current_hashes(**kwargs)


def test_authored_waivers_is_a_tuple_member():
    assert "authored_waivers" in OPTIONAL_INPUT_FIELDS


def test_an_override_compiled_with_a_waiver_needs_the_same_waiver(tmp_path):
    """One operator's approval must not authorise a world they never saw."""
    made, _ = _hashes(tmp_path, with_waivers=True)
    _, current = _hashes(tmp_path, with_waivers=False)
    pinned = dict(current)
    pinned["authored_waivers"] = file_sha256(made["authored_waivers"])
    with pytest.raises(PlanWorldMismatch, match="authored_waivers"):
        verify_tuple({"inputs": pinned, "provenance": {}}, current)


def test_an_override_that_waived_nothing_is_refused_here(tmp_path):
    _, hashes = _hashes(tmp_path, with_waivers=True)
    stale = {"inputs": dict(hashes), "provenance": {}}
    del stale["inputs"]["authored_waivers"]
    with pytest.raises(PlanWorldMismatch, match="authored_waivers"):
        verify_tuple(stale, hashes)


# --------------------------------------------------------------------------
# the real planner, on the real lane inputs
# --------------------------------------------------------------------------

LANE = REPO_ROOT / ".run/numabe_connected/build_planning_arch09"
BUNDLE = (REPO_ROOT
          / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915")
CATALOG = (REPO_ROOT / "artifacts/experiments/fl4813_capture_review_20260915"
           / "preserved/numabe_full_727_mapping_20260915"
           / "generic_module_catalog.json")
PACK = REPO_ROOT / "assets/world_templates/japan_urban/pack.json"
FUSION = REPO_ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915/fusion_reviews.json"
ROUTE_IMPORTANCE = (REPO_ROOT / "artifacts/experiments/fl4809_route_importance"
                    / "preserved/5a1feab7a462a28d.route_importance.json")
WAIVERS = BUNDLE / "authored_representation_waivers.json"

_LANE_INPUTS = (
    LANE / "canonical.world_ir.json", LANE / "canonical_entities.json",
    LANE / "observation_debt.json", LANE / "osm_ingest_receipt.json",
    LANE / "plan/rules_resolved.json", LANE / "osm.world_ir.json",
    LANE / "plan/jev_decisions.json", LANE / "terrain_metadata.json",
    BUNDLE / "valhalla_pedestrian_route.json", CATALOG, PACK, FUSION,
    ROUTE_IMPORTANCE, WAIVERS)

requires_lane = pytest.mark.skipif(
    not all(p.is_file() for p in _LANE_INPUTS),
    reason="lane planning artifacts absent (.run is not tracked)")


def _plan(route_importance, waivers, canonical=None):
    from scripts.pipeline.compile_representation_plan import build
    return build(
        canonical or (LANE / "canonical.world_ir.json"),
        LANE / "canonical_entities.json",
        LANE / "observation_debt.json", LANE / "osm_ingest_receipt.json",
        LANE / "plan/rules_resolved.json", LANE / "osm.world_ir.json",
        CATALOG, PACK,
        jev_path=LANE / "plan/jev_decisions.json",
        route_path=BUNDLE / "valhalla_pedestrian_route.json",
        fusion_path=FUSION,
        terrain_metadata_path=LANE / "terrain_metadata.json",
        route_importance_path=route_importance,
        authored_waivers_path=waivers)


def _approve(into: Path, digest, *, name="approved_waivers.json", **over):
    """A copy of the committed proposal, moved to 'approved'.

    The committed document is never modified. An approval is manufactured
    here the way a real one would be written: same waiver_id, same reason,
    same conditions, plus the three fields a human adds when they decide.
    """
    doc = copy.deepcopy(json.loads(WAIVERS.read_text(encoding="utf-8")))
    row = doc["waivers"][0]
    row["status"] = "approved"
    row["approved_by"] = PRINCIPAL
    row["approved_on"] = "2026-09-23"
    row["approval_record"] = COMMIT_RECORD
    row["conditions_sha256"] = digest
    row.update(over)
    path = into / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _camera(plan):
    return next(r for r in plan["rows"] if r["feature_id"] == CAMERA)


def _incomplete(plan):
    return {r["feature_id"] for r in plan["rows"] if not r["evidence_complete"]}


@pytest.fixture(scope="module")
def unwaived():
    """The lane with no waiver document at all."""
    return _plan(ROUTE_IMPORTANCE, None)


@pytest.fixture(scope="module")
def published_digest(unwaived):
    """The digest the blocking row publishes for an approver to sign."""
    return _camera(unwaived[0])["waiver_conditions_sha256"]


@pytest.fixture(scope="module")
def approved_dir():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture(scope="module")
def approved_doc(approved_dir, published_digest):
    return _approve(approved_dir, published_digest)


@pytest.fixture(scope="module")
def waived(approved_doc):
    """The lane with that approval in force."""
    return _plan(ROUTE_IMPORTANCE, approved_doc)


@pytest.fixture(scope="module")
def proposed():
    """The lane with the committed document, which is still a proposal."""
    return _plan(ROUTE_IMPORTANCE, WAIVERS)


@requires_lane
def test_exactly_one_row_owes_an_observation(unwaived):
    """The camera is the only blocker a waiver could ever touch.

    The other 19 incomplete rows are mesh_fixture and surface_decal, which
    are dispositions no waiver may waive. So the approval under discussion
    decides exactly one row, and there is no second row it could reach.
    """
    plan = unwaived[0]
    owed = [r for r in plan["rows"] if r["disposition"] == "observation_owed"]
    assert [r["feature_id"] for r in owed] == [CAMERA]
    assert owed[0]["waiver_conditions_sha256"]
    others = sorted(r["disposition"] for r in plan["rows"]
                    if not r["evidence_complete"]
                    and r["disposition"] != "observation_owed")
    # FL-4876. The claim is that no OTHER incomplete row is reachable by a
    # waiver, so assert that directly against the waiver module's own rule
    # rather than against a count.
    #
    # This previously pinned 17 mesh_fixture and 2 surface_decal. That count
    # is a lane snapshot, not the invariant: repairing the mass-conflict
    # reconciliation sweep (SPATIAL-A1) resolved seven mapillary_object
    # attachments, so seven of those rows stopped being incomplete at all.
    # Measured, and 0 rows became incomplete in exchange. A count that moves
    # when unrelated evidence improves is a test that fails on good news.
    assert others, "the lane is expected to still carry incomplete rows"
    assert not set(others) & set(WAIVABLE_DISPOSITIONS), (
        "an incomplete row other than the camera has a waivable disposition, "
        "so the approval under discussion would decide more than one row: "
        + repr(sorted(set(others))))


@requires_lane
def test_the_committed_proposal_changes_nothing(proposed, unwaived):
    """The regression that stops a proposal from making a build pass.

    Planning with the committed document and planning with no document give
    the same rows and the same blockers. The camera still owes its
    observation, because nobody has decided anything yet.
    """
    on, off = proposed[0], unwaived[0]
    assert len(on["rows"]) == len(off["rows"])
    assert _incomplete(on) == _incomplete(off)
    row = _camera(on)
    assert row["disposition"] == "observation_owed"
    assert row["evidence_complete"] is False
    assert row["policy_rule"] == (
        "street_fusion_policy:jev_decision:human_review:observation_debt")


@requires_lane
def test_the_waiver_clears_the_blocker_it_was_written_for(waived, unwaived):
    """20 back to 19, and only because of the row the approval names."""
    on, off = waived[0], unwaived[0]
    assert _incomplete(off) - _incomplete(on) == {CAMERA}
    assert not _incomplete(on) - _incomplete(off), \
        "a waiver must not create debt elsewhere"


@requires_lane
def test_the_waived_row_is_not_the_background_omission_row(waived):
    """The regression that matters.

    R-STREET-OMIT and R-WAIVER produce the same disposition. The difference
    is whether anybody decided. Restoring the background default would make
    this row look identical from the outside, so the policy_rule is pinned.
    """
    row = _camera(waived[0])
    assert row["disposition"] == "intentional_omission"
    assert row["evidence_complete"] is True
    assert row["policy_rule"] == "authored_waiver:" + COMMIT_RECORD
    assert "background_unresolved_omission" not in row["policy_rule"]
    assert row["reason"].startswith("R-WAIVER:")
    assert PRINCIPAL in row["reason"] and COMMIT_RECORD in row["reason"]


@requires_lane
def test_the_waived_row_keeps_its_observation_and_asserts_nothing(waived):
    """A waiver approves an omission; it does not delete evidence."""
    row = _camera(waived[0])
    assert row["evidence_state"] == "directly_observed"
    assert row["representation_kind"] == "omitted"
    assert row["owner_stage"] == "none"


@requires_lane
def test_an_approval_is_not_exercised_where_the_row_never_blocked(
        approved_doc):
    """RETRACTED precedence, pinned in its corrected form.

    The waiver check used to run BEFORE the background branch, so an approval
    could overwrite a row that was never blocking. It now runs after it.
    Without the route receipt the camera reads 'background' and R-STREET-OMIT
    omits it, so there is no debt left for the approval to discharge, it is
    never exercised, and the run is refused. That is the honest consequence:
    the approval is valid only while the configuration that created the debt
    is in force.
    """
    with pytest.raises(AuthoredWaiverInvalid, match="never exercised") as err:
        _plan(None, approved_doc)
    assert re.search(re.escape(CAMERA), str(err.value))


@requires_lane
def test_the_receipt_pins_the_approval(waived, unwaived):
    """A plan compiled under an approval cannot be replayed without it."""
    assert waived[1]["inputs"]["authored_waivers"]
    assert "authored_waivers" not in unwaived[1]["inputs"]


@requires_lane
def test_a_waiver_the_run_does_not_need_refuses_the_run(
        approved_dir, published_digest):
    """A building owes no observation, so approving one carries a decision
    past the condition it was made under."""
    canonical = json.loads(
        (LANE / "canonical.world_ir.json").read_text(encoding="utf-8"))
    building = next(f["feature_id"] for f in canonical["canonical_features"]
                    if f["semantic_class"] == "building_footprint")
    path = _approve(approved_dir, published_digest, name="spare.json")
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["waivers"].append(_row(fid=building, waiver_id="WVR-4809-002"))
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(AuthoredWaiverInvalid, match="never exercised") as err:
        _plan(ROUTE_IMPORTANCE, path)
    assert re.search(re.escape(building), str(err.value))


# --------------------------------------------------------------------------
# the approval is bound to the world it was given for
# --------------------------------------------------------------------------

@requires_lane
def test_the_blocking_row_publishes_the_digest_an_approval_must_carry(
        unwaived, published_digest, waived):
    """The round trip that makes an approval computable.

    Without this an approver would have to guess the digest, and an approval
    nobody can compute is an approval nobody can check. The blocking row
    publishes it, an approval built from exactly that value is honoured by
    the planner, and the committed proposal carries the same value, so the
    document was argued against the world this lane presents.
    """
    assert re.fullmatch(r"[0-9a-f]{64}", published_digest)
    assert published_digest == PROPOSED_DIGEST
    committed = json.loads(WAIVERS.read_text(encoding="utf-8"))
    assert committed["waivers"][0]["conditions_sha256"] == published_digest
    assert _camera(waived[0])["waiver_conditions_sha256"] == published_digest


@requires_lane
def test_an_approval_bound_to_other_conditions_refuses_the_run(approved_dir):
    """An approval whose conditions do not match the world is not applied.

    Applying it would mean honouring a judgement about a situation that is
    not the one in front of the planner. The refusal names the waiver so the
    row can be reviewed again rather than re-applied.
    """
    path = _approve(approved_dir, "0" * 64, name="mismatched.json")
    with pytest.raises(AuthoredWaiverInvalid, match="WVR-4809-001") as err:
        _plan(ROUTE_IMPORTANCE, path)
    assert "reviewed again" in str(err.value)


@requires_lane
def test_an_approval_does_not_survive_a_change_to_its_conditions(
        approved_doc, published_digest):
    """Staleness, measured through the real planner.

    The approval is valid for this lane. Change one member of the conditions
    -- here the camera's presentation_requirements -- and the same approval,
    unchanged, is refused. An approval that outlives its justification is
    worse than no approval, because it reads as considered.
    """
    canonical = json.loads(
        (LANE / "canonical.world_ir.json").read_text(encoding="utf-8"))
    moved = copy.deepcopy(canonical)
    feature = next(f for f in moved["canonical_features"]
                   if f["feature_id"] == CAMERA)
    feature["presentation_requirements"]["notes"] = (
        "Mapillary object--cctv-camera, re-reviewed")
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "canonical.world_ir.json"
        path.write_text(json.dumps(moved), encoding="utf-8")
        with pytest.raises(AuthoredWaiverInvalid,
                           match="WVR-4809-001") as err:
            _plan(ROUTE_IMPORTANCE, approved_doc, canonical=path)
    assert published_digest in str(err.value)
