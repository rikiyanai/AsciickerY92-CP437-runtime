#!/usr/bin/env python3
"""Materialize the complete FL-4781 feature and history registry.

The failure log is the canonical input for this builder.  Its row and
``commit_semantic_v3`` records are parsed literally, then emitted in a stable
JSON order.  The builder deliberately does not infer a retirement, behavior
checker, or source ownership from a path.  Every active feature therefore
retains an explicit missing checker until an authoritative executable route is
recorded.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FAILURE_LOG = REPO_ROOT / "docs" / "FAILURE_LOG.md"
DEFAULT_REGISTRY = REPO_ROOT / "config" / "godot_feature_regressions.json"
DEFAULT_CENSUS = REPO_ROOT / "config" / "godot_feature_history_census.json"

EXPECTED_ROW_COUNT = 237
EXPECTED_COMMIT_COUNT = 5_945
EXPECTED_V4_CORRECTION_COUNT = 2_445
EXPECTED_ROUTE_COUNT = 237
EXPECTED_BEHAVIOR_CLASS_COUNTS = {
    "static": 21,
    "runtime": 84,
    "temporal": 55,
    "interactive": 77,
}
ROUTE_RECORD_PREFIX = "record_type=feature_route_census_v1|"
ROUTE_REQUIRED_FIELDS = (
    "feature_id",
    "human_name",
    "behavior_class",
    "route_state",
    "route_argv",
    "target",
    "build",
    "map",
    "scene",
    "pose",
    "camera",
    "mode",
    "baseline",
    "timing_window",
    "receipt_path",
    "receipt_schema",
    "required_observation_keys",
    "missing_reason",
    "evidence_refs",
    "reviewer_status",
)
VALID_BEHAVIOR_CLASSES = frozenset(EXPECTED_BEHAVIOR_CLASS_COUNTS)
VALID_ROUTE_STATES = frozenset({"MISSING", "EXACT_EXISTING"})
EXPECTED_OPERATOR_REFERENCE = "cad59385041f75c22738372ef31d2efa68bd6592"
EXPECTED_GRAPH_BASELINE = "e780ea800b77a75bd395414c25c79039abf8ff95"
EXPECTED_COVERAGE_ENDPOINT = "69f2729561158cb840a6a1e9cb6ed1d9e71441c8"
EXPECTED_HISTORY_CHECKSUM = "5f716c2fed46a302d5f0abb2781d67ad62e99b122368fa6d34ad777792d14ec6"
PROVENANCE_TIMESTAMP = "2026-08-28T00:00:00Z"
ROW_EVIDENCE_PREFIX = "row_adjudication_v2:"


class RegistryBuildError(RuntimeError):
    """Raised when the canonical population cannot be materialized exactly."""


def _parse_record(line: str, prefix: str) -> dict[str, str]:
    """Parse one canonical pipe-delimited record without lossy heuristics."""

    if not line.startswith(prefix):
        raise RegistryBuildError(f"record does not start with {prefix!r}")
    fields: dict[str, str] = {}
    fields_source = line[len(prefix) :].split("|")
    # Canonical v4 rows use a terminal pipe as a record terminator; it is not
    # an empty field and must not be treated as a schema violation.
    if fields_source and fields_source[-1] == "":
        fields_source.pop()
    for field in fields_source:
        if "=" not in field:
            raise RegistryBuildError(f"malformed field without '=': {field!r}")
        key, value = field.split("=", 1)
        if not key or key in fields:
            raise RegistryBuildError(f"duplicate or empty field key: {key!r}")
        fields[key] = value
    return fields


def _route_records_from_line(line: str, line_number: int) -> list[dict[str, str]]:
    """Extract route records joined by ``||`` from one FL front-door line."""

    if ROUTE_RECORD_PREFIX not in line:
        return []
    records: list[dict[str, str]] = []
    for segment in line.split("||"):
        marker = segment.find(ROUTE_RECORD_PREFIX)
        if marker < 0:
            continue
        payload = segment[marker:]
        try:
            record = _parse_record(payload, ROUTE_RECORD_PREFIX)
        except RegistryBuildError as exc:
            raise RegistryBuildError(f"failure-log:{line_number}: {exc}") from exc
        record["_line_number"] = str(line_number)
        records.append(record)
    return records


V5_RECORD_PREFIX = "record_type=commit_semantic_v5|"
V5_RECORD_END = re.compile(r"\|semantic_review=(?:RESOLVED|RED)(?=\s|\||$)")
V5_REQUIRED_FIELDS = (
    "record_type",
    "slice",
    "commit_sha",
    "order_index",
    "prior_v4_ref",
    "disposition",
    "feature_ids",
    "intent_from_commit",
    "outcome_from_exact_FL_or_commit",
    "failure_reason_or_NONE",
    "evidence_refs",
    "process_failure_category",
    "reviewer_status",
    "semantic_review",
)
V5_OUTCOME_FIELD = "outcome_from_exact_FL_or_commit"


def _v5_records_from_line(line: str, line_number: int) -> list[dict[str, str]]:
    """Extract wrapped v5 records without parsing surrounding FL prose."""

    records: list[dict[str, str]] = []
    search_from = 0
    while True:
        marker = line.find(V5_RECORD_PREFIX, search_from)
        if marker < 0:
            break
        tail = line[marker:]
        match = V5_RECORD_END.search(tail)
        if match is None:
            raise RegistryBuildError(
                f"failure-log:{line_number}: v5 record has no semantic_review terminator"
            )
        payload = tail[: match.end()]
        record = _parse_record(payload, V5_RECORD_PREFIX)
        record["record_type"] = "commit_semantic_v5"
        record["_line_number"] = str(line_number)
        records.append(record)
        search_from = marker + match.end()
    return records


def parse_failure_log(
    path: Path,
) -> tuple[
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str]],
]:
    """Return the exact FL row, v3, v4, v5, and route streams from ``path``."""

    rows: list[dict[str, str]] = []
    commits: list[dict[str, str]] = []
    corrections: list[dict[str, str]] = []
    v5_corrections: list[dict[str, str]] = []
    routes: list[dict[str, str]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RegistryBuildError(f"cannot read canonical failure log {path}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        try:
            if line.startswith("row_adjudication_v2|"):
                record = _parse_record(line, "row_adjudication_v2|")
                record["_line_number"] = str(line_number)
                rows.append(record)
            elif line.startswith("record_type=commit_semantic_v3|"):
                record = _parse_record(line, "record_type=commit_semantic_v3|")
                record["_line_number"] = str(line_number)
                commits.append(record)
            elif line.startswith("record_type=commit_semantic_v4|"):
                record = _parse_record(line, "record_type=commit_semantic_v4|")
                record["_line_number"] = str(line_number)
                corrections.append(record)
            v5_corrections.extend(_v5_records_from_line(line, line_number))
            routes.extend(_route_records_from_line(line, line_number))
        except RegistryBuildError as exc:
            raise RegistryBuildError(f"{path}:{line_number}: {exc}") from exc
    return rows, commits, corrections, v5_corrections, routes


def _require_fields(record: dict[str, str], fields: Iterable[str], context: str) -> None:
    missing = sorted(field for field in fields if not record.get(field))
    if missing:
        raise RegistryBuildError(f"{context}: missing required fields {missing}")


def _git_rev_list(
    repo: Path,
    baseline: str,
    endpoint: str,
    *,
    include_baseline: bool,
    reverse: bool = False,
) -> list[str]:
    range_spec = f"{baseline}^..{endpoint}" if include_baseline else f"{baseline}..{endpoint}"
    try:
        completed = subprocess.run(
            ["git", "rev-list", *(('--reverse',) if reverse else ()), range_spec],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except OSError as exc:
        raise RegistryBuildError(f"git rev-list failed: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RegistryBuildError(f"git rev-list failed: {detail}")
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _validate_population(
    rows: list[dict[str, str]], commits: list[dict[str, str]], repo: Path
) -> tuple[dict[str, dict[str, str]], list[str], str]:
    if len(rows) != EXPECTED_ROW_COUNT:
        raise RegistryBuildError(f"expected {EXPECTED_ROW_COUNT} row records, found {len(rows)}")
    row_ids = [row.get("feature_id", "") for row in rows]
    if any(not value for value in row_ids):
        raise RegistryBuildError("every row must have a feature_id")
    if len(set(row_ids)) != len(row_ids):
        raise RegistryBuildError("duplicate atomic feature_id in row_adjudication_v2 stream")
    for index, row in enumerate(rows):
        _require_fields(
            row,
            (
                "feature_id",
                "disposition",
                "human_name",
                "category",
                "parent_family",
                "admission_source",
                "current_owner",
                "row_state",
                "current_state",
                "machine_coverage",
                "retirement_record",
                "reason",
            ),
            f"row {index}",
        )
        if row["machine_coverage"] != "RED":
            raise RegistryBuildError(
                f"row {row['feature_id']} is not RED; builder may not promote coverage"
            )
        if row["retirement_record"] != "NONE":
            raise RegistryBuildError(
                f"row {row['feature_id']} contains retirement evidence; operator approval is required"
            )

    if len(commits) != EXPECTED_COMMIT_COUNT:
        raise RegistryBuildError(
            f"expected {EXPECTED_COMMIT_COUNT} commit_semantic_v3 records, found {len(commits)}"
        )
    commit_ids = [commit.get("commit_sha", "") for commit in commits]
    if any(len(value) != 40 or value.lower() != value for value in commit_ids):
        raise RegistryBuildError("every v3 commit record must contain a lowercase full SHA")
    if len(set(commit_ids)) != len(commit_ids):
        raise RegistryBuildError("duplicate commit_sha in commit_semantic_v3 stream")
    order_indices: list[int] = []
    for index, commit in enumerate(commits):
        _require_fields(
            commit,
            (
                "commit_sha",
                "order_index",
                "disposition",
                "feature_ids",
                "reason",
                "evidence_refs",
                "process_failure_category",
            ),
            f"commit {index}",
        )
        try:
            order_indices.append(int(commit["order_index"]))
        except ValueError as exc:
            raise RegistryBuildError(f"commit {index}: order_index is not an integer") from exc
    if order_indices != list(range(EXPECTED_COMMIT_COUNT)):
        raise RegistryBuildError("v3 order_index must be exactly 0..5944")

    graph = _git_rev_list(
        repo, EXPECTED_GRAPH_BASELINE, EXPECTED_COVERAGE_ENDPOINT, include_baseline=True
    )
    if len(graph) != EXPECTED_COMMIT_COUNT:
        raise RegistryBuildError(f"git graph contains {len(graph)} commits, expected {EXPECTED_COMMIT_COUNT}")
    if set(graph) != set(commit_ids):
        missing = sorted(set(graph) - set(commit_ids))
        extra = sorted(set(commit_ids) - set(graph))
        raise RegistryBuildError(f"v3/git graph mismatch: missing={missing[:3]} extra={extra[:3]}")
    baseline_first_default = [EXPECTED_GRAPH_BASELINE] + _git_rev_list(
        repo, EXPECTED_GRAPH_BASELINE, EXPECTED_COVERAGE_ENDPOINT, include_baseline=False
    )
    checksum = hashlib.sha256(("\n".join(baseline_first_default) + "\n").encode("utf-8")).hexdigest()
    if checksum != EXPECTED_HISTORY_CHECKSUM:
        raise RegistryBuildError(
            f"canonical history checksum mismatch: {checksum} != {EXPECTED_HISTORY_CHECKSUM}"
        )
    return {row["feature_id"]: row for row in rows}, commit_ids, checksum


def _validate_route_stream(
    routes: list[dict[str, str]], rows_by_id: dict[str, dict[str, str]]
) -> dict[str, dict[str, str]]:
    """Validate the exact atomic route stream and index it by feature ID."""

    if len(routes) != EXPECTED_ROUTE_COUNT:
        raise RegistryBuildError(
            f"expected {EXPECTED_ROUTE_COUNT} route records, found {len(routes)}"
        )
    route_ids = [route.get("feature_id", "") for route in routes]
    if any(not feature_id for feature_id in route_ids):
        raise RegistryBuildError("every route record must have a feature_id")
    if len(set(route_ids)) != len(route_ids):
        raise RegistryBuildError("duplicate feature_id in route census")
    row_ids = set(rows_by_id)
    route_id_set = set(route_ids)
    if route_id_set != row_ids:
        missing = sorted(row_ids - route_id_set)
        extra = sorted(route_id_set - row_ids)
        raise RegistryBuildError(
            f"route census feature-id set mismatch: missing={missing[:3]} extra={extra[:3]}"
        )

    classes: dict[str, int] = {name: 0 for name in EXPECTED_BEHAVIOR_CLASS_COUNTS}
    indexed: dict[str, dict[str, str]] = {}
    exact_identity_fields = (
        "route_argv",
        "target",
        "build",
        "map",
        "scene",
        "pose",
        "camera",
        "mode",
        "baseline",
        "timing_window",
        "receipt_path",
        "receipt_schema",
        "required_observation_keys",
    )
    for index, route in enumerate(routes):
        context = f"route {index}"
        _require_fields(route, ROUTE_REQUIRED_FIELDS, context)
        feature_id = route["feature_id"]
        behavior_class = route["behavior_class"]
        route_state = route["route_state"]
        if behavior_class not in VALID_BEHAVIOR_CLASSES:
            raise RegistryBuildError(
                f"{context} {feature_id}: unsupported behavior_class {behavior_class!r}"
            )
        if route_state not in VALID_ROUTE_STATES:
            raise RegistryBuildError(
                f"{context} {feature_id}: unsupported route_state {route_state!r}"
            )
        classes[behavior_class] += 1
        if route_state == "MISSING":
            if route["reviewer_status"] != "UNRESOLVED_RED":
                raise RegistryBuildError(
                    f"{context} {feature_id}: MISSING route must remain UNRESOLVED_RED"
                )
            if not route["missing_reason"]:
                raise RegistryBuildError(
                    f"{context} {feature_id}: MISSING route requires missing_reason"
                )
        else:
            if route["reviewer_status"] != "RESOLVED":
                raise RegistryBuildError(
                    f"{context} {feature_id}: EXACT_EXISTING route must be RESOLVED"
                )
            missing_identity = [
                field for field in exact_identity_fields if route[field] == "NONE"
            ]
            if missing_identity:
                raise RegistryBuildError(
                    f"{context} {feature_id}: exact route missing identity fields {missing_identity}"
                )
        indexed[feature_id] = route
    if classes != EXPECTED_BEHAVIOR_CLASS_COUNTS:
        raise RegistryBuildError(
            f"route behavior-class counts mismatch: {classes} != {EXPECTED_BEHAVIOR_CLASS_COUNTS}"
        )
    return indexed


def _feature_evidence_id(feature_id: str) -> str:
    return f"{ROW_EVIDENCE_PREFIX}{feature_id}"


_V4_DISPOSITIONS = {
    "feature-introducing",
    "feature-modifying",
    "feature-restoring",
    "feature-removing",
    "retirement",
    "no-feature",
    "process-only",
    "process-failure",
}
_V4_EMPTY_FEATURE_DISPOSITIONS = {"no-feature", "process-only", "process-failure"}
_V4_PROCESS_FAILURE_CATEGORIES = {
    "process",
    "failure",
    "governance",
    "documentation",
    "infrastructure",
    "tooling",
}


def _v3_red_records(commits: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        commit
        for commit in commits
        if commit.get("disposition") == "no-feature"
        and "semantic_review=RED" in set(commit.get("evidence_refs", "").split(";"))
    ]


def _v4_feature_ids(record: dict[str, str], context: str) -> list[str]:
    raw = record.get("feature_ids")
    if raw is None:
        raise RegistryBuildError(f"{context}: feature_ids is required")
    if raw == "NONE":
        return []
    values = raw.split(",")
    if not values or any(not value for value in values) or len(values) != len(set(values)):
        raise RegistryBuildError(f"{context}: feature_ids must be NONE or unique IDs")
    return values


def _validate_v4_overlay(
    v3_commits: list[dict[str, str]],
    corrections: list[dict[str, str]],
    rows_by_id: dict[str, dict[str, str]],
    *,
    expected_count: int | None = EXPECTED_V4_CORRECTION_COUNT,
) -> dict[tuple[str, int], dict[str, Any]]:
    """Validate and normalize canonical v4 corrections over v3 RED rows.

    ``expected_count=None`` supports small adversarial fixtures while the
    production builder retains the exact 2445-row population requirement.
    """

    red_records = _v3_red_records(v3_commits)
    if expected_count is not None and len(corrections) != expected_count:
        raise RegistryBuildError(
            f"expected {expected_count} v4 correction records, found {len(corrections)}"
        )
    expected_pairs = {
        (record["commit_sha"], int(record["order_index"])) for record in red_records
    }
    red_by_pair = {
        (record["commit_sha"], int(record["order_index"])): record
        for record in red_records
    }
    correction_pairs: set[tuple[str, int]] = set()
    correction_shas: set[str] = set()
    correction_indices: set[int] = set()
    normalized: dict[tuple[str, int], dict[str, Any]] = {}
    for index, correction in enumerate(corrections):
        context = f"v4 correction {index}"
        _require_fields(
            correction,
            (
                "commit_sha",
                "order_index",
                "feature_ids",
                "reason",
                "evidence_refs",
                "process_failure_category",
                "semantic_review",
                "reviewer_status",
            ),
            context,
        )
        commit_sha = correction["commit_sha"]
        if len(commit_sha) != 40 or commit_sha.lower() != commit_sha:
            raise RegistryBuildError(f"{context}: commit_sha must be a lowercase full SHA")
        try:
            order_index = int(correction["order_index"])
        except ValueError as exc:
            raise RegistryBuildError(f"{context}: order_index must be an integer") from exc
        pair = (commit_sha, order_index)
        if pair in correction_pairs:
            raise RegistryBuildError(f"{context}: duplicate SHA/index pair {pair}")
        if commit_sha in correction_shas:
            raise RegistryBuildError(f"{context}: duplicate commit_sha {commit_sha}")
        if order_index in correction_indices:
            raise RegistryBuildError(f"{context}: duplicate order_index {order_index}")
        correction_pairs.add(pair)
        correction_shas.add(commit_sha)
        correction_indices.add(order_index)
        v3_record = red_by_pair.get(pair)
        if v3_record is None:
            raise RegistryBuildError(f"{context}: correction does not match a v3 RED no-feature pair {pair}")
        prior_disposition = correction.get("prior_disposition")
        if prior_disposition is not None and prior_disposition != v3_record["disposition"]:
            raise RegistryBuildError(
                f"{context}: prior_disposition {prior_disposition!r} does not match v3 disposition"
            )
        disposition = correction.get("reviewed_disposition") or correction.get("disposition")
        if not disposition:
            raise RegistryBuildError(f"{context}: disposition or reviewed_disposition is required")
        if correction.get("disposition") and correction.get("reviewed_disposition"):
            if correction["disposition"] != correction["reviewed_disposition"]:
                raise RegistryBuildError(f"{context}: dialect disposition fields disagree")
        if disposition not in _V4_DISPOSITIONS:
            raise RegistryBuildError(f"{context}: unknown disposition {disposition!r}")
        feature_ids = _v4_feature_ids(correction, context)
        if disposition in _V4_EMPTY_FEATURE_DISPOSITIONS:
            if feature_ids:
                raise RegistryBuildError(f"{context}: {disposition} requires empty feature_ids")
        elif not feature_ids:
            raise RegistryBuildError(f"{context}: feature disposition requires nonempty feature_ids")
        elif any(feature_id not in rows_by_id for feature_id in feature_ids):
            raise RegistryBuildError(f"{context}: feature_ids include unknown atomic feature")
        category = correction["process_failure_category"]
        if category != "NONE" and category not in _V4_PROCESS_FAILURE_CATEGORIES:
            raise RegistryBuildError(f"{context}: unknown process_failure_category {category!r}")
        semantic_review = correction["semantic_review"]
        reviewer_status = correction["reviewer_status"]
        unresolved = semantic_review == "RED" or reviewer_status == "UNRESOLVED_RED"
        resolved = semantic_review in {"GREEN", "RESOLVED"} and reviewer_status == "RESOLVED"
        if not unresolved and not resolved:
            raise RegistryBuildError(
                f"{context}: semantic_review/reviewer_status is not a supported resolved or unresolved pair"
            )
        if unresolved and (semantic_review != "RED" or reviewer_status != "UNRESOLVED_RED"):
            raise RegistryBuildError(f"{context}: unresolved corrections require RED/UNRESOLVED_RED")
        if unresolved and disposition != "no-feature":
            raise RegistryBuildError(f"{context}: unresolved correction must remain no-feature")
        if resolved and disposition in _V4_EMPTY_FEATURE_DISPOSITIONS:
            raise RegistryBuildError(f"{context}: resolved correction requires exact feature disposition")
        normalized[pair] = {
            "commit": commit_sha,
            "disposition": disposition,
            "feature_ids": feature_ids,
            "reason": correction["reason"],
            "evidence_refs": (
                [_feature_evidence_id(feature_id) for feature_id in feature_ids]
                if feature_ids
                else []
            ),
            "process_failure_category": None if category == "NONE" else category,
            "review_state": "unresolved" if unresolved else "resolved",
        }
    if correction_pairs != expected_pairs:
        missing = sorted(expected_pairs - correction_pairs)
        extra = sorted(correction_pairs - expected_pairs)
        raise RegistryBuildError(
            f"v4 correction set mismatch: missing={missing[:3]} extra={extra[:3]}"
        )
    return normalized


def _validate_v5_overlay(
    corrections: list[dict[str, Any]],
    v4_by_pair: dict[tuple[str, int], dict[str, Any]],
    rows_by_id: dict[str, dict[str, str]],
    *,
    expected_count: int | None = None,
) -> dict[tuple[str, int], dict[str, Any]]:
    """Validate v5 rows and normalize them as exact replacements for v4."""

    if expected_count is not None and len(corrections) != expected_count:
        raise RegistryBuildError(
            f"expected {expected_count} v5 correction records, found {len(corrections)}"
        )
    pairs: set[tuple[str, int]] = set()
    shas: set[str] = set()
    indices: set[int] = set()
    normalized: dict[tuple[str, int], dict[str, Any]] = {}
    for index, correction in enumerate(corrections):
        context = f"v5 correction {index}"
        _require_fields(correction, V5_REQUIRED_FIELDS, context)
        if correction["record_type"] != "commit_semantic_v5":
            raise RegistryBuildError(f"{context}: unsupported record_type")
        commit_sha = correction["commit_sha"]
        if len(commit_sha) != 40 or commit_sha.lower() != commit_sha:
            raise RegistryBuildError(f"{context}: commit_sha must be a lowercase full SHA")
        try:
            order_index = int(correction["order_index"])
        except ValueError as exc:
            raise RegistryBuildError(f"{context}: order_index must be an integer") from exc
        pair = (commit_sha, order_index)
        if pair in pairs:
            raise RegistryBuildError(f"{context}: duplicate SHA/order pair {pair}")
        if commit_sha in shas:
            raise RegistryBuildError(f"{context}: duplicate commit_sha {commit_sha}")
        if order_index in indices:
            raise RegistryBuildError(f"{context}: duplicate order_index {order_index}")
        pairs.add(pair)
        shas.add(commit_sha)
        indices.add(order_index)

        prior = v4_by_pair.get(pair)
        if prior is None:
            raise RegistryBuildError(f"{context}: no exact prior v4 pair {pair}")
        expected_prior_ref = f"commit_semantic_v4_{commit_sha}_order_index_{order_index}"
        if correction["prior_v4_ref"] != expected_prior_ref:
            raise RegistryBuildError(
                f"{context}: prior_v4_ref does not identify exact pair {pair}"
            )
        if prior["review_state"] != "unresolved" or prior["disposition"] != "no-feature":
            raise RegistryBuildError(
                f"{context}: v5 may replace only an unresolved v4 no-feature pair"
            )

        disposition = correction["disposition"]
        if disposition not in _V4_DISPOSITIONS:
            raise RegistryBuildError(f"{context}: unknown disposition {disposition!r}")
        feature_ids = _v4_feature_ids(correction, context)
        if disposition in _V4_EMPTY_FEATURE_DISPOSITIONS:
            if feature_ids:
                raise RegistryBuildError(f"{context}: {disposition} requires empty feature_ids")
        elif not feature_ids:
            raise RegistryBuildError(f"{context}: feature disposition requires nonempty feature_ids")
        elif any(feature_id not in rows_by_id for feature_id in feature_ids):
            raise RegistryBuildError(f"{context}: feature_ids include unknown atomic feature")
        category = correction["process_failure_category"]
        if category != "NONE" and category not in _V4_PROCESS_FAILURE_CATEGORIES:
            raise RegistryBuildError(f"{context}: unknown process_failure_category {category!r}")

        status_pair = (correction["semantic_review"], correction["reviewer_status"])
        if status_pair == ("RESOLVED", "RESOLVED"):
            if disposition == "process-failure":
                raise RegistryBuildError(f"{context}: resolved process-failure is not admissible")
            review_state = "resolved"
        elif status_pair == ("RED", "UNRESOLVED_RED"):
            if disposition not in {"no-feature", "process-failure"} or feature_ids:
                raise RegistryBuildError(
                    f"{context}: unresolved v5 row must remain featureless no-feature/process-failure"
                )
            review_state = "unresolved"
        else:
            raise RegistryBuildError(
                f"{context}: semantic_review/reviewer_status must be RESOLVED/RESOLVED or RED/UNRESOLVED_RED"
            )
        normalized[pair] = {
            "commit": commit_sha,
            "disposition": disposition,
            "feature_ids": feature_ids,
            "reason": correction["failure_reason_or_NONE"]
            if correction["failure_reason_or_NONE"] != "NONE"
            else correction["intent_from_commit"],
            "evidence_refs": (
                [_feature_evidence_id(feature_id) for feature_id in feature_ids]
                if feature_ids
                else []
            ),
            "process_failure_category": None if category == "NONE" else category,
            "review_state": review_state,
        }
    return normalized


def _v5_outcome_counts(corrections: list[dict[str, str]]) -> tuple[int, int]:
    """Count exact v5 outcomes without treating semantic review as outcome proof."""

    unlocated = sum(
        correction[V5_OUTCOME_FIELD] == "outcome_unlocated" for correction in corrections
    )
    return len(corrections) - unlocated, unlocated


def _feature_row(
    row: dict[str, str], route_by_id: dict[str, dict[str, str]]
) -> dict[str, Any]:
    feature_id = row["feature_id"]
    route = route_by_id[feature_id]
    behavior_class = route["behavior_class"]
    owner = row["current_owner"]
    source_owners = [owner] if owner != "NONE" else ["UNRESOLVED_NO_CURRENT_SOURCE_OWNER"]
    observable = (
        f"UNRESOLVED machine behavior obligation for {feature_id} ({row['human_name']}); "
        f"admission={row['disposition']}; row_state={row['row_state']}; "
        f"current_state={row['current_state']}; machine_coverage=RED; "
        f"behavior_class={behavior_class}; route_state={route['route_state']}; "
        f"route_evidence_line={route['_line_number']}; check=null"
    )
    return {
        "id": feature_id,
        "name": row["human_name"],
        "category": row["category"],
        "state": "active",
        "evidence_refs": [_feature_evidence_id(feature_id)],
        "source_owners": source_owners,
        "observable": observable,
        "parent_family_id": row["parent_family"],
        "behavior_class": behavior_class,
        "check": None,
    }


def _commit_record(
    commit: dict[str, str],
    rows_by_id: dict[str, dict[str, str]],
    correction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if correction is not None:
        return correction
    disposition = commit["disposition"]
    raw_feature_ids = commit["feature_ids"]
    feature_ids = [] if raw_feature_ids == "NONE" else raw_feature_ids.split(",")
    if disposition in {"no-feature", "process-only", "process-failure"}:
        if feature_ids:
            raise RegistryBuildError(
                f"{commit['commit_sha']}: {disposition} must have empty feature_ids"
            )
        evidence_refs: list[str] = []
    else:
        if not feature_ids or any(feature_id not in rows_by_id for feature_id in feature_ids):
            raise RegistryBuildError(
                f"{commit['commit_sha']}: feature disposition must reference exact known feature IDs"
            )
        evidence_refs = [_feature_evidence_id(feature_id) for feature_id in feature_ids]
    evidence_tokens = set(commit["evidence_refs"].split(";"))
    if disposition == "no-feature":
        review_state = "unresolved" if "semantic_review=RED" in evidence_tokens else "resolved"
        if review_state != "unresolved":
            raise RegistryBuildError(
                f"{commit['commit_sha']}: no-feature v3 row lacks required semantic_review=RED"
            )
    else:
        if "semantic_review=RED" in evidence_tokens:
            raise RegistryBuildError(
                f"{commit['commit_sha']}: feature/process v3 row unexpectedly carries semantic_review=RED"
            )
        review_state = "resolved"
    category = commit["process_failure_category"]
    process_failure_category = None if category == "NONE" else category
    return {
        "commit": commit["commit_sha"],
        "disposition": disposition,
        "feature_ids": feature_ids,
        "reason": commit["reason"],
        "evidence_refs": evidence_refs,
        "process_failure_category": process_failure_category,
        "review_state": review_state,
    }


def build_documents(
    *, failure_log: Path = DEFAULT_FAILURE_LOG, repo: Path = REPO_ROOT
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build registry and external census documents from canonical source."""

    rows, commits, corrections, v5_corrections, routes = parse_failure_log(failure_log)
    rows_by_id, commit_ids, checksum = _validate_population(rows, commits, repo)
    correction_by_pair = _validate_v4_overlay(
        commits,
        corrections,
        rows_by_id,
    )
    v5_by_pair = _validate_v5_overlay(v5_corrections, correction_by_pair, rows_by_id)
    correction_by_pair.update(v5_by_pair)
    route_by_id = _validate_route_stream(routes, rows_by_id)
    feature_rows = [_feature_row(row, route_by_id) for row in rows]
    classified = [
        _commit_record(
            commit,
            rows_by_id,
            correction=correction_by_pair.get(
                (commit["commit_sha"], int(commit["order_index"]))
            ),
        )
        for commit in commits
    ]
    evidence_ids = [_feature_evidence_id(row["feature_id"]) for row in rows]
    remaining_semantic_reviews = EXPECTED_V4_CORRECTION_COUNT - len(v5_corrections)
    if remaining_semantic_reviews < 0:
        raise RegistryBuildError(
            f"v5 correction stream exceeds v4 population: {len(v5_corrections)}"
        )
    v5_outcome_reviewed_count, v5_outcome_unlocated_count = _v5_outcome_counts(v5_corrections)
    census = {
        "version": 1,
        "feature_ids": [row["feature_id"] for row in rows],
        "evidence_ids": evidence_ids,
        "operator_reference_commit": EXPECTED_OPERATOR_REFERENCE,
        "graph_baseline_commit": EXPECTED_GRAPH_BASELINE,
        "coverage_through_commit": EXPECTED_COVERAGE_ENDPOINT,
        "history_paths": [],
        "classified_commits": classified,
        "retirement_approvals": [],
        "v5_correction_count": len(v5_corrections),
        "v5_resolved_count": sum(record["review_state"] == "resolved" for record in v5_by_pair.values()),
        "v5_unresolved_count": sum(record["review_state"] == "unresolved" for record in v5_by_pair.values()),
        "remaining_semantic_reviews": remaining_semantic_reviews,
        "v5_outcome_reviewed_count": v5_outcome_reviewed_count,
        "v5_outcome_unlocated_count": v5_outcome_unlocated_count,
        "remaining_outcome_reviews": v5_outcome_unlocated_count,
    }
    registry = {
        "version": 1,
        "census": {
            "provenance": {
                "source": "docs/FAILURE_LOG.md#FL-4781",
                "revision": (
                    f"commit_semantic_v3:{checksum};"
                    f"commit_semantic_v4:{len(corrections)};"
                    f"commit_semantic_v5:{len(v5_corrections)};"
                    f"remaining_semantic_reviews:{remaining_semantic_reviews};"
                    f"remaining_outcome_reviews:{v5_outcome_unlocated_count}"
                ),
                "generated_at": PROVENANCE_TIMESTAMP,
            },
            "expected_census_path": "config/godot_feature_history_census.json",
        },
        "features": feature_rows,
    }
    if len(feature_rows) != EXPECTED_ROW_COUNT or len(classified) != EXPECTED_COMMIT_COUNT:
        raise RegistryBuildError("internal materialization count mismatch")
    if set(commit_ids) != {record["commit"] for record in classified}:
        raise RegistryBuildError("internal classified commit set mismatch")
    return registry, census


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.write_text(encoded, encoding="utf-8")


def write_outputs(
    *, failure_log: Path = DEFAULT_FAILURE_LOG, repo: Path = REPO_ROOT,
    registry_path: Path = DEFAULT_REGISTRY, census_path: Path = DEFAULT_CENSUS
) -> tuple[dict[str, Any], dict[str, Any]]:
    registry, census = build_documents(failure_log=failure_log, repo=repo)
    # FL-4512: these individually authored meanings live in the registry, not
    # the historical census stream. Regeneration must never erase them.
    if registry_path.exists():
        try:
            previous = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RegistryBuildError(f"cannot preserve authored True FF mappings: {exc}") from exc
        preserve_true_ff_mappings(registry, previous)
    _write_json(census_path, census)
    _write_json(registry_path, registry)
    return registry, census


def preserve_true_ff_mappings(registry: dict[str, Any], previous: object) -> None:
    """Retain human-authored mapping fields by exact feature identity."""
    if not isinstance(previous, dict) or not isinstance(previous.get("features"), list):
        raise RegistryBuildError("cannot preserve mappings from malformed registry")
    mapped: dict[str, dict[str, Any]] = {}
    for row in previous["features"]:
        if not isinstance(row, dict) or "true_ff" not in row:
            continue
        feature_id = row.get("id")
        if not isinstance(feature_id, str) or feature_id in mapped:
            raise RegistryBuildError("duplicate or invalid authored mapping identity")
        mapped[feature_id] = row
    current = {row["id"]: row for row in registry["features"]}
    for feature_id, old in mapped.items():
        row = current.get(feature_id)
        if row is None or row["name"] != old.get("name"):
            raise RegistryBuildError(f"authored mapping identity changed: {feature_id}")
        row["true_ff"] = copy.deepcopy(old["true_ff"])
    if mapped and set(current) != set(mapped):
        missing = ", ".join(sorted(set(current) - set(mapped)))
        raise RegistryBuildError(f"new or previously unmapped features need explicit True FF meanings: {missing}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--failure-log", type=Path, default=DEFAULT_FAILURE_LOG)
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--census", type=Path, default=DEFAULT_CENSUS)
    args = parser.parse_args(argv)
    try:
        registry, census = write_outputs(
            failure_log=args.failure_log,
            repo=args.repo,
            registry_path=args.registry,
            census_path=args.census,
        )
    except RegistryBuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        "materialized "
        f"features={len(registry['features'])} "
        f"classified_commits={len(census['classified_commits'])} "
        f"unresolved_semantics={sum(record['review_state'] == 'unresolved' for record in census['classified_commits'])} "
        f"missing_behavior_checks={sum(feature['check'] is None for feature in registry['features'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
