#!/usr/bin/env python3
"""Refresh official road-map metadata before any map population bake.

This owner deliberately does not download or retain municipal map drawings.
It refreshes the official update feed and terms, verifies a reviewed factual
classification against the live layer versions, and emits a run-local receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


POLICY_SCHEMA = "y92.official_road_source_policy.v1"
CLASSIFICATION_SCHEMA = "y92.official_road_classification.v1"
RECEIPT_SCHEMA = "y92.official_road_source_receipt.v1"
MANAGEMENT_CLASSES = frozenset({"ota_managed", "not_ota_managed"})
_REIWA = re.compile(r"令和(?P<year>\d+)年(?P<month>\d+)月(?P<day>\d+)日")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _headers(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in raw.replace("\r\n", "\n").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip().lower()] = value.strip()
    return result


def _curl_fetch(url: str) -> tuple[bytes, dict[str, str]]:
    """Fetch one official endpoint with system curl and strict TLS handling."""
    common = [
        "curl", "--fail", "--silent", "--show-error", "--location",
        "--connect-timeout", "10", "--max-time", "30",
    ]
    head = subprocess.run(
        [*common, "--head", url], check=True, capture_output=True,
    )
    body = subprocess.run(
        [*common, "--compressed", url], check=True, capture_output=True,
    )
    return body.stdout, _headers(head.stdout.decode("iso-8859-1", errors="replace"))


def _official_date(value: str) -> str:
    match = _REIWA.fullmatch(value.strip())
    if not match:
        raise ValueError(f"unsupported official update date: {value!r}")
    return "{:04d}-{:02d}-{:02d}".format(
        2018 + int(match.group("year")),
        int(match.group("month")),
        int(match.group("day")),
    )


def _parse_update_feed(data: bytes, labels: dict[str, str]) -> dict[str, str]:
    root = ET.fromstring(data.decode("utf-8-sig"))
    updates: dict[str, str] = {}
    for item in root.findall("./item"):
        label = (item.findtext("label") or "").strip()
        raw_date = (item.findtext("time") or "").strip()
        if label in labels:
            updates[labels[label]] = _official_date(raw_date)
    return updates


def _load(path: Path, schema: str) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != schema:
        raise ValueError(f"{path} has unsupported schema {document.get('schema')!r}")
    return document


def _validate_classification(
    classification: dict[str, Any], route_path: Path, live_updates: dict[str, str]
) -> dict[str, Any]:
    expected_hash = classification.get("route", {}).get("sha256")
    actual_hash = _sha256(route_path.read_bytes())
    if actual_hash != expected_hash:
        raise ValueError(
            "official road classification route hash is stale: "
            f"expected {expected_hash}, got {actual_hash}"
        )

    reviewed_updates = classification.get("source_layer_updates", {})
    changed_layers = {
        layer: {"reviewed": reviewed_updates.get(layer), "live": live_updates.get(layer)}
        for layer in sorted(set(reviewed_updates) | set(live_updates))
        if reviewed_updates.get(layer) != live_updates.get(layer)
    }

    length_m = float(classification.get("route", {}).get("length_m", 0.0))
    spans = classification.get("management_spans", [])
    failures: list[str] = []
    cursor = 0.0
    for index, span in enumerate(spans):
        start = float(span.get("start_m", -1.0))
        end = float(span.get("end_m", -1.0))
        if abs(start - cursor) > 0.001:
            failures.append(f"span {index} starts at {start}, expected {cursor}")
        if end <= start:
            failures.append(f"span {index} has non-positive length")
        if span.get("management_class") not in MANAGEMENT_CLASSES:
            failures.append(f"span {index} has unresolved management_class")
        if span.get("management_class") == "ota_managed" and not span.get("route_key"):
            failures.append(f"span {index} lacks an official Ota route key")
        cursor = end
    if abs(cursor - length_m) > 0.001:
        failures.append(f"management spans end at {cursor}, expected {length_m}")
    if not spans:
        failures.append("management_spans is empty")
    if changed_layers:
        failures.append("official layer versions changed after classification review")

    return {
        "route_sha256": actual_hash,
        "route_length_m": length_m,
        "management_span_count": len(spans),
        "coverage_complete": not any("span" in item for item in failures),
        "changed_layers": changed_layers,
        "failures": failures,
        "passed": not failures,
    }


def refresh_official_road_source(
    *,
    policy_path: Path,
    classification_path: Path,
    route_path: Path,
    receipt_path: Path,
    fetcher: Callable[[str], tuple[bytes, dict[str, str]]] = _curl_fetch,
    fetched_at: str | None = None,
) -> dict[str, Any]:
    """Refresh official metadata and validate Step 1 before generation."""
    policy = _load(policy_path, POLICY_SCHEMA)
    classification = _load(classification_path, CLASSIFICATION_SCHEMA)
    if classification.get("adapter_id") != policy.get("adapter_id"):
        raise ValueError(
            "official road classification adapter does not match policy adapter: "
            f"{classification.get('adapter_id')!r} != {policy.get('adapter_id')!r}"
        )
    labels = policy.get("update_labels", {})
    required_layers = set(policy.get("phase1_required_layers", []))
    if not required_layers:
        raise ValueError("official road source policy has no phase1_required_layers")

    endpoints = policy.get("refresh_endpoints", [])
    if not endpoints:
        raise ValueError("official road source policy has no refresh_endpoints")
    fetched: dict[str, dict[str, Any]] = {}
    payloads: dict[str, bytes] = {}
    for endpoint in endpoints:
        endpoint_id = str(endpoint["id"])
        data, headers = fetcher(str(endpoint["url"]))
        payloads[endpoint_id] = data
        fetched[endpoint_id] = {
            "url": endpoint["url"],
            "sha256": _sha256(data),
            "size_bytes": len(data),
            "last_modified": headers.get("last-modified"),
            "etag": headers.get("etag"),
        }

    update_endpoint = str(policy["update_feed_endpoint_id"])
    if update_endpoint not in payloads:
        raise ValueError("update feed endpoint was not refreshed")
    live_updates = _parse_update_feed(payloads[update_endpoint], labels)
    missing_layers = sorted(required_layers - set(live_updates))
    classification_check = _validate_classification(
        classification, route_path, live_updates
    )
    failures = list(classification_check["failures"])
    if missing_layers:
        failures.append("live update feed lacks required layers: " + ", ".join(missing_layers))

    receipt = {
        "schema": RECEIPT_SCHEMA,
        "adapter_id": policy["adapter_id"],
        "fetched_at": fetched_at or datetime.now(timezone.utc).isoformat(),
        "pipeline_position": (
            "official_road_map_classification_before_law_selection_before_"
            "observed_feature_join_before_physical_registration_before_generation"
        ),
        "policy": {
            "path": str(policy_path.resolve()),
            "sha256": _sha256(policy_path.read_bytes()),
        },
        "classification": {
            "path": str(classification_path.resolve()),
            "sha256": _sha256(classification_path.read_bytes()),
        },
        "route": {
            "path": str(route_path.resolve()),
            "sha256": classification_check["route_sha256"],
        },
        "live_layer_updates": live_updates,
        "fetched_endpoints": fetched,
        "classification_check": classification_check,
        # FL-4813: stage receipts expose only their own stepN_passed state.
        # The final Phase 1 generation gate is the sole owner of
        # generation_authorized; Step 1 must not emit it or any alias.
        "claim_boundary": {
            "site_truth_effect": "classification_constraint_only",
            "drawing_or_tile_retained": False,
            "legal_width_certified": False,
            "building_standards_act_road_status_certified": False,
            "current_physical_condition_certified": False,
        },
        "phase1_step1_gate": {
            "passed": not failures,
            "failures": failures,
        },
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--classification", type=Path, required=True)
    parser.add_argument("--route", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    receipt = refresh_official_road_source(
        policy_path=args.policy,
        classification_path=args.classification,
        route_path=args.route,
        receipt_path=args.out,
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if receipt["phase1_step1_gate"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
