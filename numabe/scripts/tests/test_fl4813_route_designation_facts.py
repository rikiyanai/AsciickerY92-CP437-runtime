"""FL-4813: fail-closed checks for connected-route designation facts.

Every test here guards a must-never-silently-pass property of
route_designation_facts.json against official_road_classification.json:

- span coverage is exactly the 13 classified spans (no missing,
  duplicated, overlapping, or extra span)
- route keys repeat the Step 1 classification (no drift)
- an administrator value is never asserted without official evidence,
  and not_ota_managed is never promoted to another administrator
  without evidence
- designated / not_designated never appears without authority and
  provenance (unknown stays explicit)
- affirmative subject exclusions are structured records, never bare
  truthy strings
- route-specific facts never leak into the generic law corpus
- the bundle source manifest verifies against disk bytes
"""

import json
from pathlib import Path

BUNDLE = Path(
    "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
)
CLASSIFICATION_PATH = BUNDLE / "official_road_classification.json"
DESIGNATIONS_PATH = BUNDLE / "route_designation_facts.json"
MANIFEST_PATH = BUNDLE / "source_manifest.json"
CORPUS_PATH = Path(
    "assets/public_realm_corpora/jp/law_standards_corpus_index.json"
)

NATIONAL_JURISDICTIONS = frozenset(
    {"japan_national", "tokyo_metropolitan", "ota_ward"}
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _span_key(span: dict) -> str:
    return f"{span.get('start_m')}-{span.get('end_m')}"


def test_designation_spans_match_classification_exactly():
    classification = _load(CLASSIFICATION_PATH)
    designations = _load(DESIGNATIONS_PATH)
    assert (
        designations.get("schema") == "y92.route_designation_facts.v1"
    )
    class_keys = [_span_key(span) for span in classification["management_spans"]]
    designation_keys = [
        _span_key(span) for span in designations["spans"]
    ]
    assert len(designation_keys) == len(set(designation_keys)), (
        f"duplicated designation spans: {designation_keys}"
    )
    missing = sorted(set(class_keys) - set(designation_keys))
    extra = sorted(set(designation_keys) - set(class_keys))
    assert not missing, f"missing designation spans: {missing}"
    assert not extra, f"extra designation spans: {extra}"


def test_designation_spans_are_contiguous_without_overlap():
    designations = _load(DESIGNATIONS_PATH)
    spans = sorted(
        designations["spans"], key=lambda span: float(span["start_m"])
    )
    cursor = 0.0
    for span in spans:
        start = float(span["start_m"])
        end = float(span["end_m"])
        assert abs(start - cursor) < 0.001, (
            f"span {_span_key(span)} starts at {start}, expected {cursor} "
            "(gap or overlap)"
        )
        assert end > start, f"span {_span_key(span)} is non-positive"
        cursor = end
    assert abs(cursor - 1016.882) < 0.001, (
        f"designation spans end at {cursor}, expected 1016.882"
    )


def test_route_keys_agree_with_step1_classification():
    classification = _load(CLASSIFICATION_PATH)
    designations = _load(DESIGNATIONS_PATH)
    class_keys = {
        _span_key(span): span.get("route_key")
        for span in classification["management_spans"]
    }
    for span in designations["spans"]:
        key = _span_key(span)
        assert span.get("route_key") == class_keys[key], (
            f"span {key}: designation route_key {span.get('route_key')!r} "
            f"disagrees with Step 1 classification {class_keys[key]!r}"
        )


def test_administrator_claims_carry_official_evidence():
    designations = _load(DESIGNATIONS_PATH)
    for span in designations["spans"]:
        key = _span_key(span)
        administrator = span.get("administrator")
        evidence = span.get("administrator_evidence") or ""
        if administrator is None:
            assert "unresolved" in evidence.lower(), (
                f"span {key}: null administrator without an explicit "
                "unresolved evidence statement"
            )
            continue
        assert len(evidence) >= 20, (
            f"span {key}: administrator {administrator!r} lacks evidence"
        )
        cites_official = (
            "Step 1" in evidence
            or "doro-map.city.ota.tokyo.jp" in evidence
            or "city.ota.tokyo.jp" in evidence
            or "kensetsu.metro.tokyo.lg.jp" in evidence
            or "mlit.go.jp" in evidence
        )
        assert cites_official, (
            f"span {key}: administrator {administrator!r} cites no "
            "official source"
        )


def test_not_ota_managed_is_never_promoted_without_evidence():
    classification = _load(CLASSIFICATION_PATH)
    designations = _load(DESIGNATIONS_PATH)
    management = {
        _span_key(span): span.get("management_class")
        for span in classification["management_spans"]
    }
    for span in designations["spans"]:
        key = _span_key(span)
        if management[key] != "not_ota_managed":
            continue
        administrator = span.get("administrator")
        evidence = span.get("administrator_evidence") or ""
        assert administrator is None or (
            "competent-office" in evidence.lower()
            or "competent office" in evidence.lower()
            or "confirmation required" in evidence.lower()
        ), (
            f"span {key}: not_ota_managed promoted to "
            f"{administrator!r} without competent-office evidence"
        )
        assert "not_ota_managed" in evidence or administrator is None or (
            "only" in evidence.lower()
        ), (
            f"span {key}: RM absence must not be stretched beyond "
            "not_ota_managed"
        )


def test_designated_status_requires_authority_and_provenance():
    designations = _load(DESIGNATIONS_PATH)
    for span in designations["spans"]:
        key = _span_key(span)
        families = span.get("designations", {})
        for family in ("article_42", "accessibility"):
            entry = families.get(family, {})
            status = entry.get("status", "unknown")
            evidence = entry.get("evidence") or ""
            assert status in (
                "designated",
                "not_designated",
                "unknown",
            ), f"span {key} {family}: unexpected status {status!r}"
            assert evidence.strip(), (
                f"span {key} {family}: status {status!r} without evidence"
            )
            if status in ("designated", "not_designated"):
                assert entry.get("authority") in NATIONAL_JURISDICTIONS, (
                    f"span {key} {family}: {status} without a national "
                    "jurisdiction authority"
                )
                assert "http" in evidence and "2026-" in evidence, (
                    f"span {key} {family}: {status} without URL and "
                    "retrieval-timestamp provenance"
                )


def test_railway_facts_require_official_evidence():
    designations = _load(DESIGNATIONS_PATH)
    for span in designations["spans"]:
        key = _span_key(span)
        railway = span.get("designations", {}).get("railway", {})
        adjacent = railway.get("adjacent")
        operator = railway.get("operator")
        evidence = railway.get("evidence") or ""
        assert adjacent in (True, False, None), (
            f"span {key}: railway adjacent must be true/false/null"
        )
        assert evidence.strip(), (
            f"span {key}: railway fact without evidence"
        )
        if adjacent is not None:
            assert "http" in evidence or "Step 1" in evidence, (
                f"span {key}: railway adjacent={adjacent} without "
                "official provenance"
            )
        if operator is not None:
            assert adjacent is True, (
                f"span {key}: railway operator {operator!r} asserted "
                "without adjacency"
            )
            assert "http" in evidence, (
                f"span {key}: railway operator without official URL "
                "provenance"
            )


def test_subject_exclusions_are_structured_records():
    designations = _load(DESIGNATIONS_PATH)
    for span in designations["spans"]:
        key = _span_key(span)
        exclusions = span.get("subject_exclusions")
        assert isinstance(exclusions, dict), (
            f"span {key}: subject_exclusions must be a mapping"
        )
        for subject, record in exclusions.items():
            assert isinstance(record, dict), (
                f"span {key} subject {subject}: affirmative exclusion "
                f"must be a structured record, got {record!r}"
            )
            assert record.get("authority") and record.get("evidence"), (
                f"span {key} subject {subject}: exclusion record lacks "
                "authority and evidence"
            )


def test_per_span_predicates_carry_evidence():
    designations = _load(DESIGNATIONS_PATH)
    for span in designations["spans"]:
        key = _span_key(span)
        for field in (
            "road_class_evidence",
            "project_type_evidence",
        ):
            evidence = span.get(field) or ""
            assert evidence.strip(), (
                f"span {key}: {field} is empty"
            )


def test_generic_law_corpus_carries_no_route_specific_facts():
    corpus = _load(CORPUS_PATH)
    assert corpus.get("schema") == "y92.law_standards_corpus_index.v2"
    classification = _load(CLASSIFICATION_PATH)
    route_keys = {
        str(span.get("route_key"))
        for span in classification["management_spans"]
        if span.get("route_key")
    }
    blob = json.dumps(corpus, ensure_ascii=False)
    for rule in corpus.get("rules", []):
        assert not rule.get("span_assertions"), (
            f"corpus rule {rule.get('rule_id')} carries route-specific "
            "span_assertions; route facts belong in the designations input"
        )
    for route_key in route_keys:
        assert route_key not in blob, (
            f"route-specific key {route_key} appears in the generic law "
            "corpus"
        )


def test_source_manifest_verifies_against_disk():
    from scripts.pipeline.source_manifest import (
        load_manifest,
        verify,
    )

    manifest = load_manifest(str(MANIFEST_PATH))
    rows = verify(manifest, MANIFEST_PATH.parent)
    bad = [
        row
        for row in rows
        if row["status"] in ("MISSING", "MISMATCH")
    ]
    assert not bad, f"manifest verification failures: {bad}"
    record_ids = {row["record_id"] for row in rows}
    assert "official_road_management_classification" in record_ids
    assert "connected_route_designation_facts" in record_ids


def test_provenance_log_is_structured():
    designations = _load(DESIGNATIONS_PATH)
    predicate_evidence = designations.get("predicate_evidence", {})
    consultations = predicate_evidence.get("source_consultations", [])
    assert consultations, "no structured source consultations recorded"
    required = {
        "authority",
        "url",
        "retrieved_at",
        "finding",
        "limit",
    }
    for entry in consultations:
        missing = required - set(entry)
        assert not missing, (
            f"consultation {entry.get('authority')}: missing {missing}"
        )


def test_route_facts_do_not_own_derived_step2_totals():
    designations = _load(DESIGNATIONS_PATH)
    predicate_evidence = designations.get("predicate_evidence", {})
    verdict_effect = predicate_evidence.get("verdict_effect", "")
    assert "unresolved with identical distribution" not in verdict_effect
    assert "run-local applicable-law receipt" in verdict_effect


def test_route_facts_own_adapter_jurisdiction_context():
    designations = _load(DESIGNATIONS_PATH)
    assert designations["jurisdiction_chain"] == [
        "japan_national", "tokyo_metropolitan", "ota_ward"
    ]
    assert designations["conditional_authorities"] == {
        "railway_operator": "railway_adjacent"
    }
