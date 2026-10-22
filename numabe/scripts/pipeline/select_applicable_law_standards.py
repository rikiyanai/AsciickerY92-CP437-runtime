#!/usr/bin/env python3
"""Select applicable law and standard references for classified route spans.

Generic owner. This selector contains no site-specific behavior. Every
site-specific value (spans, corpus rules, designation facts, same-run Step 1
receipt, output path) arrives through inputs. It joins each span to corpus
rules by management class and machine-evaluable predicate fields, accepts
route-specific designation facts only through the separate designations
input, records a per-span per-subject verdict of applicable, not_applicable,
or unresolved, and evaluates a fail-closed selection gate.

Fail-closed rules enforced here:

- The generic corpus must not carry route-specific span assertions. A rule
  with span_assertions fails the run loudly instead of silently scoping law.
- Assertions gathered without a jurisdiction or management-class check are
  rejected. Designation facts must name their authority.
- A zero-rule match, a locator-only selected source, an unproven predicate,
  a wrong jurisdiction, or a stale Step 1 linkage stays unresolved. None of
  these can open the gate.
- Coverage is evaluated per span and subject, never globally.
- Step 2 never authorizes generation. The receipt exposes step2_passed only.
  The Phase 1 generation gate owns generation_authorized.

Step 2 never creates site truth: no sign, fence, gutter, sidewalk, boundary,
width, or condition is asserted for any span here. Presence and position
require Step 3 observation evidence and Step 4 registration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

CORPUS_SCHEMA = "y92.law_standards_corpus_index.v2"
DESIGNATIONS_SCHEMA = "y92.route_designation_facts.v1"
STEP1_RECEIPT_SCHEMA = "y92.official_road_source_receipt.v1"
CLASSIFICATION_SCHEMA = "y92.official_road_classification.v1"
RECEIPT_SCHEMA = "y92.applicable_law_selection_receipt.v2"

SUBJECT_FAMILIES = (
    "road_structure",
    "sign_form",
    "marking_form",
    "accessibility",
    "setback",
    "article_42_category",
    "occupation",
    "railway_boundary",
)

# Subjects whose applicable verdict additionally requires a proven route
# designation fact: designation key -> designations document key.
DESIGNATION_GATED = {
    "article_42_category": "article_42",
    "accessibility": "accessibility",
    "railway_boundary": "railway",
    "setback": "article_42",
}

EXCLUDED_DERIVATIONS = (
    "certified legal width",
    "Building Standards Act Article 42 classification without designation-map evidence",
    "exact road or property boundary",
    "speed limits or traffic-control state",
    "which signs or markings physically exist",
    "universal gutter-to-road ratios",
    "universal sidewalk construction",
    "rail-fence presence",
    "current physical condition after the survey date",
    "actual owner of an unnamed or non-owner-managed span",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _repo_relative(path: Path) -> str:
    """Record run inputs as repository-relative paths when possible.

    Absolute /Users/... paths in receipts hide which bundle owns the input,
    so every input is stored relative to the current working directory
    (the repository root for pipeline runs) and falls back to absolute only
    for paths outside it.
    """
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def _load(path: Path, schema: str) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != schema:
        raise ValueError(
            f"{path} has schema {document.get('schema')!r}; expected {schema!r}"
        )
    return document


def _span_key(span: dict[str, Any]) -> str:
    return f"{span.get('start_m')}-{span.get('end_m')}"


def _validate_corpus(corpus: dict[str, Any]) -> None:
    """Reject a corpus that mixes route facts into generic law."""
    rules = corpus.get("rules", [])
    rule_ids = [rule.get("rule_id") for rule in rules]
    if any(not isinstance(rule_id, str) or not rule_id for rule_id in rule_ids):
        raise ValueError("every corpus rule requires a non-empty rule_id")
    if len(set(rule_ids)) != len(rule_ids):
        raise ValueError("corpus contains duplicate rule_id values")
    known_rule_ids = set(rule_ids)
    for rule in rules:
        unknown_subjects = sorted(
            set(rule.get("subject_families", [])) - set(SUBJECT_FAMILIES)
        )
        if unknown_subjects:
            raise ValueError(
                f"corpus rule {rule.get('rule_id')} has unknown subject families "
                f"{unknown_subjects}"
            )
        selection_role = rule.get("selection_role", "selectable")
        if selection_role not in ("selectable", "alias_only"):
            raise ValueError(
                f"corpus rule {rule.get('rule_id')} has unsupported "
                f"selection_role {selection_role!r}"
            )
        if selection_role == "alias_only":
            if not rule.get("superseded_by"):
                raise ValueError(
                    f"alias-only corpus rule {rule.get('rule_id')} names no children"
                )
            missing_children = sorted(
                set(rule["superseded_by"]) - known_rule_ids
            )
            if missing_children:
                raise ValueError(
                    f"alias-only corpus rule {rule.get('rule_id')} names unknown "
                    f"children {missing_children}"
                )
            if rule.get("extraction_state") != "locator_only":
                raise ValueError(
                    f"alias-only corpus rule {rule.get('rule_id')} must remain "
                    "locator_only"
                )
        if rule.get("span_assertions"):
            raise ValueError(
                f"corpus rule {rule.get('rule_id')} carries route-specific "
                "span_assertions; route facts belong in the designations "
                "input, not the generic corpus"
            )
        predicate = rule.get("applicability_predicate", {})
        for field in (
            "management_classes",
            "administrators",
            "road_classes",
            "project_types",
        ):
            if field not in predicate:
                raise ValueError(
                    f"corpus rule {rule.get('rule_id')} lacks machine-evaluable "
                    f"predicate field {field!r}; free-text applies_when alone "
                    "cannot scope a rule"
                )
        if not predicate.get("management_classes"):
            raise ValueError(
                f"corpus rule {rule.get('rule_id')} matches no management class"
            )


def _authority_context(
    designations: dict[str, Any],
) -> tuple[tuple[str, ...], dict[str, str]]:
    """Load jurisdiction compatibility from data, never locale-specific code."""
    chain = designations.get("jurisdiction_chain")
    if not isinstance(chain, list) or not chain or any(
        not isinstance(item, str) or not item for item in chain
    ):
        raise ValueError(
            "route designation facts require a non-empty jurisdiction_chain"
        )
    if len(set(chain)) != len(chain):
        raise ValueError("route designation jurisdiction_chain contains duplicates")
    conditional = designations.get("conditional_authorities", {})
    if not isinstance(conditional, dict) or any(
        not isinstance(authority, str)
        or not authority
        or not isinstance(context_key, str)
        or not context_key
        for authority, context_key in conditional.items()
    ):
        raise ValueError(
            "route designation conditional_authorities must map authority IDs "
            "to span-context predicate keys"
        )
    return tuple(chain), conditional


def _validate_subject_exclusions(
    row: dict[str, Any], *, allowed_authorities: frozenset[str]
) -> dict[str, Any]:
    """Validate the one route-fact owner for affirmative exclusions.

    ``subject_exclusions`` is a sibling of ``designations`` in the route-facts
    schema.  Accepting the same key below ``designations`` would create a
    second, silently different owner, so that shape fails loudly.
    """
    designations = row.get("designations", {})
    if not isinstance(designations, dict):
        raise ValueError(
            f"span {_span_key(row)} designations must be a mapping"
        )
    nested = designations.get("subject_exclusions")
    if nested is not None:
        raise ValueError(
            f"span {_span_key(row)} stores subject_exclusions inside "
            "designations; exclusions belong at the span root"
        )
    exclusions = row.get("subject_exclusions", {})
    if not isinstance(exclusions, dict):
        raise ValueError(
            f"span {_span_key(row)} subject_exclusions must be a mapping"
        )
    for subject, record in exclusions.items():
        if subject not in SUBJECT_FAMILIES:
            raise ValueError(
                f"span {_span_key(row)} excludes unknown subject {subject!r}"
            )
        if not isinstance(record, dict):
            raise ValueError(
                f"span {_span_key(row)} subject {subject!r} exclusion must be "
                "a structured record"
            )
        authority = record.get("authority")
        evidence = record.get("evidence")
        if authority not in allowed_authorities:
            raise ValueError(
                f"span {_span_key(row)} subject {subject!r} exclusion has "
                f"unsupported authority {authority!r}"
            )
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError(
                f"span {_span_key(row)} subject {subject!r} exclusion lacks "
                "evidence"
            )
    return exclusions


def _check_step1_linkage(
    *,
    step1_receipt: dict[str, Any],
    classification_path: Path,
    route_path: Path,
) -> tuple[bool, list[str], dict[str, Any]]:
    """Verify the same-run Step 1 receipt covers these exact inputs.

    Returns (linkage_ok, errors, evidence). A stale or mismatched linkage
    fails closed through unresolved verdicts, never through an exception,
    so the receipt itself records why the gate stayed shut.
    """
    errors: list[str] = []
    classification_bytes = classification_path.read_bytes()
    route_bytes = route_path.read_bytes()
    classification_sha = _sha256(classification_bytes)
    route_sha = _sha256(route_bytes)
    evidence = {
        "step1_passed": bool(
            step1_receipt.get("phase1_step1_gate", {}).get("passed", False)
        ),
        "classification_sha256": classification_sha,
        "route_sha256": route_sha,
    }
    if not evidence["step1_passed"]:
        errors.append("same-run Step 1 receipt did not pass")
    receipt_classification_sha = (
        step1_receipt.get("classification", {}).get("sha256")
    )
    if receipt_classification_sha != classification_sha:
        errors.append(
            "classification hash does not match the same-run Step 1 receipt: "
            f"receipt {receipt_classification_sha}, input {classification_sha}"
        )
    receipt_route_sha = step1_receipt.get("route", {}).get("sha256")
    if receipt_route_sha != route_sha:
        errors.append(
            "route hash does not match the same-run Step 1 receipt: "
            f"receipt {receipt_route_sha}, input {route_sha}"
        )
    receipt_adapter = step1_receipt.get("adapter_id")
    classification = json.loads(classification_bytes.decode("utf-8"))
    if classification.get("adapter_id") != receipt_adapter:
        errors.append(
            "classification adapter does not match the same-run Step 1 receipt"
        )
    return (not errors, errors, evidence)


def _match_rule(
    rule: dict[str, Any], *, span_context: dict[str, Any],
    jurisdiction_chain: frozenset[str],
    conditional_authorities: dict[str, str],
) -> tuple[bool, str | None]:
    """Evaluate one rule predicate against one span context.

    Returns (matched, exclusion_reason). An exclusion reason of
    predicate_unproven means the span fact a predicate field needs is still
    unknown; any other reason means the rule affirmatively does not cover
    the span.
    """
    predicate = rule.get("applicability_predicate", {})
    if span_context["management_class"] not in predicate.get(
        "management_classes", []
    ):
        return (False, "class_mismatch")
    jurisdiction = rule.get("jurisdiction")
    if jurisdiction in jurisdiction_chain:
        pass
    elif jurisdiction in conditional_authorities:
        condition = span_context.get(conditional_authorities[jurisdiction])
        if condition is not True:
            if condition is None:
                return (False, "predicate_unproven")
            return (False, "jurisdiction_mismatch")
    else:
        return (False, "jurisdiction_mismatch")
    for field, context_key in (
        ("administrators", "administrator"),
        ("road_classes", "road_class"),
        ("project_types", "project_type"),
    ):
        accepted = predicate.get(field, [])
        if not accepted:
            continue
        value = span_context.get(context_key)
        if value is None:
            return (False, "predicate_unproven")
        if value not in accepted:
            return (False, "predicate_mismatch")
    return (True, None)


def _designation_status(
    designations: dict[str, Any], designation_key: str,
    *, allowed_authorities: frozenset[str]
) -> tuple[str, str]:
    """Return (status, evidence) for one designation family.

    Railway uses an adjacency boolean; the other families use a
    designated/not_designated/unknown status. Anything unproven reports
    unknown with its reason.
    """
    entry = designations.get(designation_key, {})
    if not isinstance(entry, dict):
        return ("unknown", "designation record is not a structured mapping")
    if designation_key == "railway":
        adjacent = entry.get("adjacent")
        evidence = str(entry.get("evidence") or "no railway evidence joined")
        if adjacent is True:
            return ("adjacent", evidence)
        if adjacent is False:
            return ("not_adjacent", evidence)
        return ("unknown", evidence)
    status = entry.get("status", "unknown")
    evidence = str(entry.get("evidence") or "no designation evidence joined")
    if status not in ("designated", "not_designated"):
        return ("unknown", evidence)
    authority = entry.get("authority")
    if status in ("designated", "not_designated") and (
        authority not in allowed_authorities
    ):
        return ("authority_mismatch", evidence)
    return (status, evidence)


def _decide_subject(
    *,
    subject: str,
    candidates: list[dict[str, Any]],
    span_context: dict[str, Any],
    designations: dict[str, Any],
    subject_exclusions: dict[str, Any],
    jurisdiction_chain: frozenset[str],
    conditional_authorities: dict[str, str],
) -> dict[str, Any]:
    """Decide one span subject as applicable, not_applicable, or unresolved."""
    matched: list[dict[str, Any]] = []
    exclusion_reasons: list[str] = []
    for rule in candidates:
        ok, reason = _match_rule(
            rule,
            span_context=span_context,
            jurisdiction_chain=jurisdiction_chain,
            conditional_authorities=conditional_authorities,
        )
        if ok:
            matched.append(rule)
        elif reason:
            exclusion_reasons.append(f"{rule.get('rule_id')}:{reason}")
    extracted = [
        rule["rule_id"]
        for rule in matched
        if rule.get("extraction_state") == "extracted_text_verified"
    ]
    locator_only = [
        rule["rule_id"]
        for rule in matched
        if rule.get("extraction_state") != "extracted_text_verified"
    ]
    base: dict[str, Any] = {
        "matched_rule_ids": [rule["rule_id"] for rule in matched],
        "extracted_rule_ids": extracted,
        "locator_only_rule_ids": locator_only,
        "exclusion_detail": sorted(set(exclusion_reasons)),
    }

    exclusion_evidence = subject_exclusions.get(subject)
    if exclusion_evidence:
        return {
            "status": "not_applicable",
            "reason_codes": ["affirmative_exclusion"],
            "exclusion_evidence": exclusion_evidence,
            **base,
        }

    gate_key = DESIGNATION_GATED.get(subject)
    if gate_key is not None:
        status, evidence = _designation_status(
            designations,
            gate_key,
            allowed_authorities=(
                jurisdiction_chain | frozenset(conditional_authorities)
            ),
        )
        base["designation_status"] = status
        base["designation_evidence"] = evidence
        negative = (
            (gate_key == "railway" and status == "not_adjacent")
            or (
                gate_key in ("article_42", "accessibility")
                and status == "not_designated"
            )
        )
        if negative:
            return {
                "status": "not_applicable",
                "reason_codes": ["affirmative_exclusion"],
                **base,
            }
        if status in ("unknown", "authority_mismatch"):
            code = (
                "designation_unproven"
                if status == "unknown"
                else "assertion_authority_mismatch"
            )
            return {"status": "unresolved", "reason_codes": [code], **base}
        if not extracted:
            code = (
                "locator_only_only" if matched else "zero_matched_rules"
            )
            return {"status": "unresolved", "reason_codes": [code], **base}
        return {"status": "applicable", "reason_codes": [], **base}

    if not candidates:
        return {
            "status": "unresolved",
            "reason_codes": ["zero_candidate_rules"],
            **base,
        }
    if not matched:
        if any("predicate_unproven" in item for item in exclusion_reasons):
            code = "predicate_unproven"
        elif any("jurisdiction_mismatch" in item for item in exclusion_reasons):
            code = "jurisdiction_mismatch"
        else:
            code = "zero_matched_rules"
        return {"status": "unresolved", "reason_codes": [code], **base}
    if not extracted:
        return {
            "status": "unresolved",
            "reason_codes": ["locator_only_only"],
            **base,
        }
    return {"status": "applicable", "reason_codes": [], **base}


def select_applicable_law_standards(
    *,
    step1_receipt_path: Path,
    classification_path: Path,
    route_path: Path,
    corpus_path: Path,
    designations_path: Path,
    receipt_path: Path,
) -> dict[str, Any]:
    """Join spans to corpus rules under same-run Step 1 linkage."""
    step1_receipt = _load(step1_receipt_path, STEP1_RECEIPT_SCHEMA)
    classification = _load(classification_path, CLASSIFICATION_SCHEMA)
    corpus = _load(corpus_path, CORPUS_SCHEMA)
    designations = _load(designations_path, DESIGNATIONS_SCHEMA)
    _validate_corpus(corpus)
    jurisdiction_order, conditional_authorities = _authority_context(designations)
    jurisdiction_chain = frozenset(jurisdiction_order)
    allowed_authorities = (
        jurisdiction_chain | frozenset(conditional_authorities)
    )

    linkage_ok, linkage_errors, linkage_evidence = _check_step1_linkage(
        step1_receipt=step1_receipt,
        classification_path=classification_path,
        route_path=route_path,
    )

    spans = classification.get("management_spans", [])
    if not spans:
        raise ValueError("classification carries no management_spans")
    for span in spans:
        if not span.get("management_class"):
            raise ValueError(f"span {_span_key(span)} has no management_class")
    designation_list = designations.get("spans", [])
    designation_keys = [_span_key(row) for row in designation_list]
    if len(set(designation_keys)) != len(designation_keys):
        raise ValueError("designations contain duplicate span ranges")
    designation_rows = {
        _span_key(row): row for row in designation_list
    }
    span_keys = [_span_key(span) for span in spans]
    if len(set(span_keys)) != len(span_keys):
        raise ValueError("classification contains duplicate span ranges")
    if set(designation_rows) != set(span_keys):
        missing = sorted(set(span_keys) - set(designation_rows))
        extra = sorted(set(designation_rows) - set(span_keys))
        raise ValueError(
            "designations span coverage does not match classification: "
            f"missing {missing}, extra {extra}"
        )
    rules = corpus.get("rules", [])
    selectable_rules = [
        rule
        for rule in rules
        if rule.get("selection_role", "selectable") == "selectable"
    ]
    rules_by_subject: dict[str, list[dict[str, Any]]] = {
        subject: [
            rule
            for rule in selectable_rules
            if subject in rule.get("subject_families", [])
        ]
        for subject in SUBJECT_FAMILIES
    }

    span_rows = []
    for span in spans:
        key = _span_key(span)
        designation = designation_rows[key]
        subject_exclusions = _validate_subject_exclusions(
            designation, allowed_authorities=allowed_authorities
        )
        designation_facts = designation.get("designations", {})
        if not isinstance(designation_facts, dict):
            raise ValueError(f"span {key} designations must be a mapping")
        railway_entry = designation_facts.get("railway", {})
        if not isinstance(railway_entry, dict):
            railway_entry = {}
        span_context = {
            "management_class": str(span["management_class"]),
            "administrator": designation.get("administrator"),
            "road_class": designation.get("road_class"),
            "project_type": designation.get("project_type"),
            "railway_adjacent": railway_entry.get("adjacent"),
        }
        subjects: dict[str, Any] = {}
        span_level_unresolved: list[str] = []
        if span_context["administrator"] is None:
            span_level_unresolved.append(
                "owner: span administrator unresolved"
            )
        elif span_context["management_class"] == "not_ota_managed" and (
            not span.get("route_key")
        ):
            span_level_unresolved.append(
                "owner: unnamed span administrator and legal class unresolved"
            )
        for subject in SUBJECT_FAMILIES:
            if not linkage_ok:
                subjects[subject] = {
                    "status": "unresolved",
                    "reason_codes": ["step1_linkage_invalid"],
                    "matched_rule_ids": [],
                    "extracted_rule_ids": [],
                    "locator_only_rule_ids": [],
                    "exclusion_detail": [],
                }
                continue
            subjects[subject] = _decide_subject(
                subject=subject,
                candidates=rules_by_subject[subject],
                span_context=span_context,
                designations=designation_facts,
                subject_exclusions=subject_exclusions,
                jurisdiction_chain=jurisdiction_chain,
                conditional_authorities=conditional_authorities,
            )
        span_rows.append({
            "span_m": [span.get("start_m"), span.get("end_m")],
            "management_class": span_context["management_class"],
            "route_key": span.get("route_key"),
            "route_name_ja": span.get("route_name_ja"),
            "span_context": {
                key: value
                for key, value in span_context.items()
                if key != "management_class"
            },
            "subjects": subjects,
            "span_level_unresolved": span_level_unresolved,
        })

    total_unresolved = sum(
        len(row["span_level_unresolved"])
        + sum(
            1
            for subject in SUBJECT_FAMILIES
            if row["subjects"][subject]["status"] == "unresolved"
        )
        for row in span_rows
    )
    step2_passed = linkage_ok and total_unresolved == 0
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "stage": "law_selection_executed_generation_not_authorized",
        "phase1_step": "step2",
        "step2_passed": step2_passed,
        "pipeline_position": "after_step1_classification_before_observed_join",
        "inputs": {
            "step1_receipt": {
                "path": _repo_relative(step1_receipt_path),
                "sha256": _sha256(step1_receipt_path.read_bytes()),
            },
            "classification": {
                "path": _repo_relative(classification_path),
                "sha256": _sha256(classification_path.read_bytes()),
            },
            "route": {
                "path": _repo_relative(route_path),
                "sha256": _sha256(route_path.read_bytes()),
            },
            "corpus": {
                "path": _repo_relative(corpus_path),
                "sha256": _sha256(corpus_path.read_bytes()),
            },
            "designations": {
                "path": _repo_relative(designations_path),
                "sha256": _sha256(designations_path.read_bytes()),
            },
        },
        "step1_linkage": {
            "ok": linkage_ok,
            "errors": linkage_errors,
            **linkage_evidence,
        },
        "corpus_state": {
            "rule_count": len(rules),
            "selectable_rule_count": len(selectable_rules),
            "alias_rule_ids": sorted(
                rule["rule_id"]
                for rule in rules
                if rule.get("selection_role") == "alias_only"
            ),
            "extracted_rule_ids": sorted(
                rule["rule_id"]
                for rule in selectable_rules
                if rule.get("extraction_state") == "extracted_text_verified"
            ),
            "locator_only_rule_ids": sorted(
                rule["rule_id"]
                for rule in selectable_rules
                if rule.get("extraction_state") != "extracted_text_verified"
            ),
        },
        "authority_context": {
            "jurisdiction_chain": list(jurisdiction_order),
            "conditional_authorities": conditional_authorities,
        },
        "spans": span_rows,
        "span_count": len(span_rows),
        "total_unresolved_legal_facts": total_unresolved,
        "excluded_derivations": list(EXCLUDED_DERIVATIONS),
        "law_selection_complete": step2_passed,
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--step1-receipt", type=Path, required=True)
    parser.add_argument("--classification", type=Path, required=True)
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--designations", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    receipt = select_applicable_law_standards(
        step1_receipt_path=args.step1_receipt,
        classification_path=args.classification,
        route_path=args.route,
        corpus_path=args.corpus,
        designations_path=args.designations,
        receipt_path=args.out,
    )
    print(f"[law-selection] spans={receipt['span_count']} "
          f"unresolved={receipt['total_unresolved_legal_facts']} "
          f"step2_passed={receipt['step2_passed']}")
    print(f"[law-selection] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
