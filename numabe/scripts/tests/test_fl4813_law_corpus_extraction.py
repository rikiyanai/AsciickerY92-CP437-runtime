"""FL-4813 law-corpus extraction contracts.

The corpus at assets/public_realm_corpora/jp/law_standards_corpus_index.json
is locator-first evidence: a rule may only claim extracted_text_verified when
its official source, exact locator, conditions, exceptions, and parameters
were inspected. These tests fail when an extracted rule lacks an exact
locator, a numeric parameter lacks a unit, a source hash is absent, a
predicate lacks machine-evaluable fields, a locator-only source is
represented as extracted, a legal rule asserts physical site presence, or a
long free-text predicate substitutes for structured applicability.
"""

import json
import re
from pathlib import Path

CORPUS_PATH = (
    Path(__file__).resolve().parents[2]
    / "assets"
    / "public_realm_corpora"
    / "jp"
    / "law_standards_corpus_index.json"
)

# The nine handoff target IDs. Split children keep the parent ID present as a
# superseded alias, so every target ID must resolve to at least one record.
TARGET_RULE_IDS = (
    "JP-ROAD-STRUCT-ORDER",
    "JP-SIGN-MARK-ORDER",
    "JP-MLIT-SIGN-PORTAL",
    "JP-ACCESSIBILITY-STD",
    "JP-RAILWAY-TECH-STD",
    "TYO-ROAD-STRUCT-ORD",
    "OTA-ROAD-STRUCT-ORD",
    "OTA-ACCESS-IMPL-REGS",
    "TYO-ROAD-OCCUPY-STD",
)

PREDICATE_FIELDS = (
    "management_classes",
    "administrators",
    "road_classes",
    "project_types",
)

# Unit suffixes every numeric parameter leaf key must carry. A bare number
# without a unit cannot be machine-evaluated.
UNIT_SUFFIXES = (
    "_m",
    "_cm",
    "_mm",
    "_km",
    "_m2",
    "_percent",
    "_kph",
    "_vehicles_per_day",
    "_count",
    "_ratio",
)

LOCATOR_PENDING_MARKERS = (
    "pending extraction",
    "not extracted",
    "not re-extracted",
)

# Phrases that would assert a feature physically exists at the site. Law is a
# constraint source only; presence needs observation evidence.
SITE_TRUTH_PHRASES = (
    "physically exists",
    "exists at numabe",
    "exists at the site",
    "proves existence",
    "proof of existence",
    "confirmed present",
    "observed at",
    "が存在する",
    "存在が確認",
)

# A free-text applies_when longer than this substitutes prose for the
# structured predicate fields.
MAX_APPLIES_WHEN_CHARS = 300

SHA_RE = re.compile(r"sha256:[0-9a-f]{64}")


def _load():
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def _rules():
    return _load()["rules"]


def _params_leaves(params, path=()):
    leaves = []
    if isinstance(params, dict):
        for key, value in params.items():
            leaves.extend(_params_leaves(value, path + (key,)))
    elif isinstance(params, list):
        for value in params:
            leaves.extend(_params_leaves(value, path))
    else:
        leaves.append((path, params))
    return leaves


def test_corpus_schema_is_v2():
    assert _load()["schema"] == "y92.law_standards_corpus_index.v2"


def test_nine_target_ids_resolve():
    ids = {rule["rule_id"] for rule in _rules()}
    for target in TARGET_RULE_IDS:
        assert target in ids, f"target rule {target} missing from corpus"


def test_every_rule_has_machine_evaluable_predicate():
    for rule in _rules():
        predicate = rule.get("applicability_predicate", {})
        for field in PREDICATE_FIELDS:
            assert field in predicate, (
                f"{rule['rule_id']} lacks machine-evaluable "
                f"predicate field {field!r}; free-text applies_when "
                "alone cannot scope a rule"
            )
            assert isinstance(predicate[field], list), (
                f"{rule['rule_id']} predicate field {field!r} "
                "must be a list"
            )
        assert predicate.get("management_classes"), (
            f"{rule['rule_id']} matches no management class"
        )


def test_no_rule_carries_route_span_assertions():
    for rule in _rules():
        assert not rule.get("span_assertions"), (
            f"corpus rule {rule['rule_id']} carries route-specific "
            "span_assertions; route facts belong in the designations "
            "input, not the generic corpus"
        )


def test_extracted_rule_has_exact_locator():
    for rule in _rules():
        if rule.get("extraction_state") != "extracted_text_verified":
            continue
        locator = rule.get("article_table_figure_or_page", "")
        assert locator.strip(), (
            f"extracted rule {rule['rule_id']} lacks an exact locator"
        )
        lowered = locator.lower()
        assert not any(
            marker in lowered for marker in LOCATOR_PENDING_MARKERS
        ), (
            f"extracted rule {rule['rule_id']} locator still pending: "
            f"{locator[:120]}"
        )


def test_locator_only_source_not_represented_as_extracted():
    for rule in _rules():
        receipt = rule.get("source_hash_or_archive_receipt", "")
        locator = rule.get("article_table_figure_or_page", "")
        pending = (
            "unfetched" in receipt
            or "no official content retrieved" in receipt
            or any(
                marker in locator.lower()
                for marker in LOCATOR_PENDING_MARKERS
            )
        )
        if pending:
            assert rule.get("extraction_state") != "extracted_text_verified", (
                f"locator-only source {rule['rule_id']} is "
                "represented as extracted"
            )
        if rule.get("extraction_state") == "extracted_text_verified":
            assert SHA_RE.search(receipt), (
                f"extracted rule {rule['rule_id']} has no "
                "raw-response SHA-256 receipt"
            )


def test_numeric_parameters_carry_units():
    for rule in _rules():
        for path, value in _params_leaves(
            rule.get("parameters_and_units", {})
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            dotted = ".".join(path)
            assert any(
                segment.endswith(suffix)
                for segment in path
                for suffix in UNIT_SUFFIXES
            ), (
                f"rule {rule['rule_id']} numeric parameter "
                f"{dotted!r} = {value} lacks a unit suffix"
            )


def test_official_urls_are_https():
    for rule in _rules():
        url = rule.get("official_https_url", "")
        assert url.startswith("https://"), (
            f"rule {rule['rule_id']} official URL is not HTTPS: {url!r}"
        )


def test_legal_rules_stay_constraint_only():
    for rule in _rules():
        assert rule.get("site_truth_effect") == "constraint_only", (
            f"rule {rule['rule_id']} must stay constraint_only; law "
            "never proves a feature physically exists at Numabe"
        )
        text = " ".join((
            rule.get("concise_interpretation", ""),
            rule.get("applicability_predicate", {}).get("applies_when", ""),
        )).lower()
        for phrase in SITE_TRUTH_PHRASES:
            assert phrase.lower() not in text, (
                f"rule {rule['rule_id']} asserts physical site "
                f"presence via {phrase!r}"
            )


def test_applies_when_is_bounded_not_a_substitute():
    for rule in _rules():
        applies = rule.get("applicability_predicate", {}).get(
            "applies_when", ""
        )
        assert applies.strip(), (
            f"rule {rule['rule_id']} has an empty applies_when"
        )
        assert len(applies) <= MAX_APPLIES_WHEN_CHARS, (
            f"rule {rule['rule_id']} applies_when is "
            f"{len(applies)} chars; long free text cannot substitute "
            "for structured applicability"
        )


def test_superseded_aliases_resolve_to_children():
    ids = {rule["rule_id"] for rule in _rules()}
    for rule in _rules():
        for child in rule.get("superseded_by", []):
            assert child in ids, (
                f"rule {rule['rule_id']} names unknown child {child}"
            )
    aliased_parents = [
        rule for rule in _rules() if rule.get("superseded_by")
    ]
    assert aliased_parents, "expected at least one superseded alias record"
    for rule in aliased_parents:
        assert rule.get("selection_role") == "alias_only", (
            f"alias parent {rule['rule_id']} must be excluded from selection"
        )
        assert rule.get("extraction_state") == "locator_only", (
            f"alias parent {rule['rule_id']} must stay locator_only; "
            "technical content lives on its children"
        )


def test_only_unextracted_ota_rules_remain_selectable_locator_records():
    locator_ids = {
        rule["rule_id"]
        for rule in _rules()
        if rule.get("extraction_state") != "extracted_text_verified"
        and rule.get("selection_role", "selectable") == "selectable"
    }
    assert locator_ids == {"OTA-ROAD-STRUCT-ORD", "OTA-ACCESS-IMPL-REGS"}


def test_locator_only_rules_expose_no_machine_parameters():
    for rule in _rules():
        if rule.get("extraction_state") != "extracted_text_verified":
            assert rule.get("parameters_and_units") == {}, rule["rule_id"]
