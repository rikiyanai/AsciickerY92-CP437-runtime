import hashlib
import json
from pathlib import Path

import pytest

from scripts.pipeline.select_applicable_law_standards import (
    select_applicable_law_standards,
)

SUBJECTS = (
    "road_structure",
    "sign_form",
    "marking_form",
    "accessibility",
    "setback",
    "article_42_category",
    "occupation",
    "railway_boundary",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _classification(spans):
    return {
        "schema": "y92.official_road_classification.v1",
        "adapter_id": "test-adapter",
        "management_spans": spans,
    }


def _base_spans():
    return [
        {
            "start_m": 0.0,
            "end_m": 10.0,
            "management_class": "ota_managed",
            "route_key": "TEST-1",
        },
        {
            "start_m": 10.0,
            "end_m": 20.0,
            "management_class": "not_ota_managed",
            "route_key": "TEST-2",
        },
    ]


def _designation_row(span, administrator="ota_ward", article_42="unknown",
                     accessibility="unknown", railway=None,
                     exclusions=None):
    return {
        "start_m": span["start_m"],
        "end_m": span["end_m"],
        "route_key": span.get("route_key"),
        "administrator": administrator,
        "administrator_evidence": "fixture",
        "road_class": None,
        "road_class_evidence": "fixture",
        "project_type": None,
        "project_type_evidence": "fixture",
        "designations": {
            "article_42": {
                "status": article_42,
                "evidence": "fixture",
                "authority": "ota_ward",
            },
            "accessibility": {
                "status": accessibility,
                "evidence": "fixture",
                "authority": "ota_ward",
            },
            "railway": {
                "adjacent": railway,
                "operator": None,
                "evidence": "fixture",
            },
        },
        "subject_exclusions": exclusions or {},
    }


def _rule(rule_id, subjects, management_classes=("ota_managed",
           "not_ota_managed"), administrators=None, road_classes=None,
           project_types=None, jurisdiction="ota_ward",
           extraction="extracted_text_verified"):
    return {
        "rule_id": rule_id,
        "subject_families": list(subjects),
        "jurisdiction": jurisdiction,
        "extraction_state": extraction,
        "applicability_predicate": {
            "applies_when": "fixture",
            "management_classes": list(management_classes),
            "administrators": list(administrators or []),
            "road_classes": list(road_classes or []),
            "project_types": list(project_types or []),
        },
    }


def _write_inputs(tmp_path: Path, *, spans=None, rules=None,
                  designations=None, route_bytes=b'{"type":"fixture"}',
                  step1_passed=True, step1_tamper=None):
    spans = spans if spans is not None else _base_spans()
    classification = _classification(spans)
    classification_path = tmp_path / "classification.json"
    classification_path.write_text(json.dumps(classification), encoding="utf-8")
    route_path = tmp_path / "route.geojson"
    route_path.write_bytes(route_bytes)
    classification_sha = _sha256(classification_path.read_bytes())
    route_sha = _sha256(route_path.read_bytes())
    step1_receipt = {
        "schema": "y92.official_road_source_receipt.v1",
        "adapter_id": "test-adapter",
        "phase1_step1_gate": {
            "passed": step1_passed,
            "failures": [] if step1_passed else ["fixture step1 failure"],
        },
        "classification": {
            "path": str(classification_path),
            "sha256": classification_sha,
        },
        "route": {"path": str(route_path), "sha256": route_sha},
    }
    if step1_tamper == "classification":
        step1_receipt["classification"]["sha256"] = "0" * 64
    elif step1_tamper == "route":
        step1_receipt["route"]["sha256"] = "0" * 64
    step1_path = tmp_path / "step1.json"
    step1_path.write_text(json.dumps(step1_receipt), encoding="utf-8")
    if rules is None:
        rules = [_rule(f"R-{s}", [s]) for s in SUBJECTS]
    corpus_path = tmp_path / "corpus.json"
    corpus_path.write_text(json.dumps({
        "schema": "y92.law_standards_corpus_index.v2",
        "rules": rules,
    }), encoding="utf-8")
    if designations is None:
        designations = {
            "schema": "y92.route_designation_facts.v1",
            "route_id": "fixture",
            "spans": [_designation_row(span) for span in spans],
        }
    designations.setdefault(
        "jurisdiction_chain",
        ["japan_national", "tokyo_metropolitan", "ota_ward"],
    )
    designations.setdefault(
        "conditional_authorities", {"railway_operator": "railway_adjacent"}
    )
    designations_path = tmp_path / "designations.json"
    designations_path.write_text(json.dumps(designations), encoding="utf-8")
    return {
        "step1_receipt_path": step1_path,
        "classification_path": classification_path,
        "route_path": route_path,
        "corpus_path": corpus_path,
        "designations_path": designations_path,
        "receipt_path": tmp_path / "receipt.json",
    }


def _run(tmp_path: Path, **overrides):
    return select_applicable_law_standards(**_write_inputs(tmp_path, **overrides))


def test_receipt_covers_every_classified_span(tmp_path):
    receipt = _run(tmp_path)
    assert receipt["span_count"] == 2
    assert [row["span_m"] for row in receipt["spans"]] == [[0.0, 10.0], [10.0, 20.0]]
    assert receipt["schema"] == "y92.applicable_law_selection_receipt.v2"


def test_step2_exposes_step_passed_not_generation_authorization(tmp_path):
    receipt = _run(tmp_path)
    assert "generation_authorized" not in receipt
    assert "step2_passed" in receipt


def test_gate_stays_closed_while_facts_are_unresolved(tmp_path):
    receipt = _run(tmp_path)
    assert receipt["total_unresolved_legal_facts"] > 0
    assert receipt["step2_passed"] is False
    assert receipt["law_selection_complete"] is False


def test_no_rule_for_one_management_class_stays_unresolved(tmp_path):
    rules = [_rule(f"R-{s}", [s], management_classes=("ota_managed",))
             for s in SUBJECTS]
    receipt = _run(tmp_path, rules=rules)
    foreign = [row for row in receipt["spans"]
               if row["management_class"] == "not_ota_managed"][0]
    assert foreign["subjects"]["sign_form"]["status"] == "unresolved"
    assert "zero_matched_rules" in foreign["subjects"]["sign_form"]["reason_codes"]
    assert receipt["step2_passed"] is False


def test_wrong_jurisdiction_rule_stays_unresolved(tmp_path):
    rules = [_rule("R-SIGN", ["sign_form"], jurisdiction="osaka_ward")]
    receipt = _run(tmp_path, rules=rules)
    for row in receipt["spans"]:
        verdict = row["subjects"]["sign_form"]
        assert verdict["status"] == "unresolved"
        assert "jurisdiction_mismatch" in verdict["reason_codes"]
    assert receipt["step2_passed"] is False


def test_wrong_authority_designation_stays_unresolved(tmp_path):
    spans = _base_spans()
    rows = []
    for span in spans:
        row = _designation_row(span, article_42="designated")
        row["designations"]["article_42"]["authority"] = "osaka_ward"
        rows.append(row)
    designations = {
        "schema": "y92.route_designation_facts.v1",
        "route_id": "fixture",
        "spans": rows,
    }
    receipt = _run(tmp_path, designations=designations)
    for row in receipt["spans"]:
        verdict = row["subjects"]["article_42_category"]
        assert verdict["status"] == "unresolved"
        assert "assertion_authority_mismatch" in verdict["reason_codes"]
    assert receipt["step2_passed"] is False


def test_locator_only_selected_rule_stays_unresolved(tmp_path):
    rules = [_rule("R-SIGN", ["sign_form"], extraction="locator_only")]
    receipt = _run(tmp_path, rules=rules)
    for row in receipt["spans"]:
        verdict = row["subjects"]["sign_form"]
        assert verdict["status"] == "unresolved"
        assert "locator_only_only" in verdict["reason_codes"]
        assert verdict["locator_only_rule_ids"] == ["R-SIGN"]
    assert receipt["step2_passed"] is False


def test_unproven_predicate_stays_unresolved(tmp_path):
    rules = [_rule("R-STRUCT", ["road_structure"],
                   project_types=("new_construction",))]
    receipt = _run(tmp_path, rules=rules)
    for row in receipt["spans"]:
        verdict = row["subjects"]["road_structure"]
        assert verdict["status"] == "unresolved"
        assert "predicate_unproven" in verdict["reason_codes"]
    assert receipt["step2_passed"] is False


def test_stale_step1_classification_hash_stays_unresolved(tmp_path):
    receipt = _run(tmp_path, step1_tamper="classification")
    assert receipt["step1_linkage"]["ok"] is False
    for row in receipt["spans"]:
        for subject in SUBJECTS:
            verdict = row["subjects"][subject]
            assert verdict["status"] == "unresolved"
            assert "step1_linkage_invalid" in verdict["reason_codes"]
    assert receipt["step2_passed"] is False


def test_route_hash_mismatch_stays_unresolved(tmp_path):
    receipt = _run(tmp_path, step1_tamper="route")
    assert receipt["step1_linkage"]["ok"] is False
    assert receipt["total_unresolved_legal_facts"] == 2 * len(SUBJECTS)
    assert receipt["step2_passed"] is False


def test_failed_step1_linkage_stays_unresolved(tmp_path):
    receipt = _run(tmp_path, step1_passed=False)
    assert receipt["step1_linkage"]["ok"] is False
    assert receipt["step2_passed"] is False


def test_missing_designation_span_fails_closed(tmp_path):
    spans = _base_spans()
    designations = {
        "schema": "y92.route_designation_facts.v1",
        "route_id": "fixture",
        "spans": [_designation_row(spans[0])],
    }
    with pytest.raises(ValueError, match="designations span coverage"):
        _run(tmp_path, designations=designations)


def test_extra_designation_span_fails_closed(tmp_path):
    spans = _base_spans()
    rows = [_designation_row(span) for span in spans]
    extra = dict(rows[0])
    extra["start_m"] = 20.0
    extra["end_m"] = 30.0
    rows.append(extra)
    designations = {
        "schema": "y92.route_designation_facts.v1",
        "route_id": "fixture",
        "spans": rows,
    }
    with pytest.raises(ValueError, match="designations span coverage"):
        _run(tmp_path, designations=designations)


def test_corpus_span_assertions_fail_closed(tmp_path):
    rules = [_rule("R-BAD", ["sign_form"])]
    rules[0]["span_assertions"] = [{"subject": "sign_form"}]
    with pytest.raises(ValueError, match="span_assertions"):
        _run(tmp_path, rules=rules)


def test_unnamed_spans_keep_owner_unresolved(tmp_path):
    spans = _base_spans()
    rows = [_designation_row(span, administrator=None) for span in spans]
    designations = {
        "schema": "y92.route_designation_facts.v1",
        "route_id": "fixture",
        "spans": rows,
    }
    receipt = _run(tmp_path, designations=designations)
    for row in receipt["spans"]:
        assert any(
            fact.startswith("owner:") for fact in row["span_level_unresolved"]
        )
    assert receipt["step2_passed"] is False


def test_proven_non_adjacent_railway_is_not_applicable(tmp_path):
    spans = _base_spans()
    rows = [_designation_row(span, railway=False) for span in spans]
    designations = {
        "schema": "y92.route_designation_facts.v1",
        "route_id": "fixture",
        "spans": rows,
    }
    receipt = _run(tmp_path, designations=designations)
    for row in receipt["spans"]:
        verdict = row["subjects"]["railway_boundary"]
        assert verdict["status"] == "not_applicable"


def test_span_root_structured_exclusion_is_consumed(tmp_path):
    spans = _base_spans()
    rows = [
        _designation_row(
            span,
            exclusions={
                "sign_form": {
                    "authority": "ota_ward",
                    "evidence": "official inventory proves no sign family applies",
                }
            },
        )
        for span in spans
    ]
    receipt = _run(
        tmp_path,
        designations={
            "schema": "y92.route_designation_facts.v1",
            "route_id": "fixture",
            "spans": rows,
        },
    )
    for row in receipt["spans"]:
        verdict = row["subjects"]["sign_form"]
        assert verdict["status"] == "not_applicable"
        assert verdict["reason_codes"] == ["affirmative_exclusion"]
        assert verdict["exclusion_evidence"]["authority"] == "ota_ward"


def test_bare_string_exclusion_fails_loudly(tmp_path):
    spans = _base_spans()
    rows = [
        _designation_row(span, exclusions={"sign_form": "not here"})
        for span in spans
    ]
    with pytest.raises(ValueError, match="structured record"):
        _run(
            tmp_path,
            designations={
                "schema": "y92.route_designation_facts.v1",
                "route_id": "fixture",
                "spans": rows,
            },
        )


def test_nested_exclusion_owner_fails_loudly(tmp_path):
    spans = _base_spans()
    rows = [_designation_row(span) for span in spans]
    for row in rows:
        row["designations"]["subject_exclusions"] = {
            "sign_form": {
                "authority": "ota_ward",
                "evidence": "wrong owner",
            }
        }
    with pytest.raises(ValueError, match="span root"):
        _run(
            tmp_path,
            designations={
                "schema": "y92.route_designation_facts.v1",
                "route_id": "fixture",
                "spans": rows,
            },
        )


def test_alias_only_rules_are_not_selector_candidates(tmp_path):
    rules = [_rule(f"R-{subject}", [subject]) for subject in SUBJECTS]
    rules.append({
        **_rule("alias", ("sign_form",), extraction="locator_only"),
        "selection_role": "alias_only",
        "superseded_by": ["R-sign_form"],
    })
    receipt = _run(tmp_path, rules=rules)
    assert receipt["corpus_state"]["alias_rule_ids"] == ["alias"]
    assert "alias" not in receipt["corpus_state"]["locator_only_rule_ids"]
    for row in receipt["spans"]:
        verdict = row["subjects"]["sign_form"]
        assert "alias" not in verdict["matched_rule_ids"]


def test_duplicate_designation_span_fails_loudly(tmp_path):
    spans = _base_spans()
    duplicate = _designation_row(spans[0])
    with pytest.raises(ValueError, match="duplicate span"):
        _run(
            tmp_path,
            designations={
                "schema": "y92.route_designation_facts.v1",
                "route_id": "fixture",
                "spans": [
                    _designation_row(spans[0]),
                    duplicate,
                    _designation_row(spans[1]),
                ],
            },
        )


def test_duplicate_corpus_rule_id_fails_loudly(tmp_path):
    rules = [_rule(f"R-{subject}", [subject]) for subject in SUBJECTS]
    rules.append(dict(rules[0]))
    with pytest.raises(ValueError, match="duplicate rule_id"):
        _run(tmp_path, rules=rules)


def test_alias_unknown_child_fails_loudly(tmp_path):
    rules = [_rule(f"R-{subject}", [subject]) for subject in SUBJECTS]
    rules.append({
        **_rule("alias", ("sign_form",), extraction="locator_only"),
        "selection_role": "alias_only",
        "superseded_by": ["missing-child"],
    })
    with pytest.raises(ValueError, match="unknown children"):
        _run(tmp_path, rules=rules)


def test_jurisdiction_chain_is_data_driven_for_another_adapter(tmp_path):
    spans = _base_spans()
    rows = [_designation_row(span, administrator="example_city") for span in spans]
    rules = [
        _rule(
            f"EX-{subject}",
            [subject],
            jurisdiction="example_national",
        )
        for subject in SUBJECTS
    ]
    receipt = _run(
        tmp_path,
        rules=rules,
        designations={
            "schema": "y92.route_designation_facts.v1",
            "route_id": "example-route",
            "jurisdiction_chain": ["example_national", "example_city"],
            "conditional_authorities": {},
            "spans": rows,
        },
    )
    assert receipt["authority_context"]["jurisdiction_chain"] == [
        "example_national", "example_city"
    ]
    assert all(
        row["subjects"]["sign_form"]["status"] == "applicable"
        for row in receipt["spans"]
    )


def test_gate_opens_only_with_matching_extraction_and_proof(tmp_path):
    spans = _base_spans()
    rows = [
        _designation_row(span, article_42="designated",
                         accessibility="designated", railway=True)
        for span in spans
    ]
    designations = {
        "schema": "y92.route_designation_facts.v1",
        "route_id": "fixture",
        "spans": rows,
    }
    receipt = _run(tmp_path, designations=designations)
    assert receipt["total_unresolved_legal_facts"] == 0
    assert receipt["step2_passed"] is True
    assert receipt["law_selection_complete"] is True
    assert "generation_authorized" not in receipt


def test_excluded_derivations_are_recorded(tmp_path):
    receipt = _run(tmp_path)
    excluded = receipt["excluded_derivations"]
    assert "certified legal width" in excluded
    assert "which signs or markings physically exist" in excluded
    assert "rail-fence presence" in excluded


def test_span_without_management_class_fails_closed(tmp_path):
    spans = [{"start_m": 0.0, "end_m": 10.0}]
    with pytest.raises(ValueError, match="no management_class"):
        _run(tmp_path, spans=spans)


def test_inputs_use_repo_relative_paths(tmp_path):
    root = Path.cwd()
    bundle = (
        root
        / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
    )
    classification_path = bundle / "official_road_classification.json"
    route_path = bundle / "connected_scope.geojson"
    corpus_path = (
        root / "assets/public_realm_corpora/jp/law_standards_corpus_index.json"
    )
    designations_path = bundle / "route_designation_facts.json"
    classification_sha = _sha256(classification_path.read_bytes())
    route_sha = _sha256(route_path.read_bytes())
    step1_path = tmp_path / "step1.json"
    step1_path.write_text(json.dumps({
        "schema": "y92.official_road_source_receipt.v1",
        "adapter_id": json.loads(
            classification_path.read_text(encoding="utf-8")
        )["adapter_id"],
        "phase1_step1_gate": {"passed": True, "failures": []},
        "classification": {
            "path": str(classification_path),
            "sha256": classification_sha,
        },
        "route": {"path": str(route_path), "sha256": route_sha},
    }), encoding="utf-8")
    receipt = select_applicable_law_standards(
        step1_receipt_path=step1_path,
        classification_path=classification_path,
        route_path=route_path,
        corpus_path=corpus_path,
        designations_path=designations_path,
        receipt_path=tmp_path / "receipt.json",
    )
    assert receipt["span_count"] == 13
    for key in ("classification", "route", "corpus", "designations"):
        assert not receipt["inputs"][key]["path"].startswith("/"), key
