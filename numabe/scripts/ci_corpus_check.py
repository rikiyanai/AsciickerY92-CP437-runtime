#!/usr/bin/env python3
"""CI-safe corpus integrity check.

Validates the baseline corpus manifest for metadata completeness and
structural integrity. Unlike verify_corpus_checksums.py, this script
does NOT require source PNG files to be present (they live outside
the repo). It validates:

    1. Manifest JSON is valid and has required top-level structure
    2. Every entry has all required metadata fields (non-null)
    3. SHA256 values are well-formed (64-char lowercase hex)
    4. Schema version is recognized
    5. Entries reference consistent variant buckets

This script is designed to run in CI where source PNGs are unavailable.
For full checksum verification (requires source files), use
verify_corpus_checksums.py instead.

Exit codes:
    0 = all checks pass
    1 = integrity failures found

Usage:
    python3 scripts/ci_corpus_check.py

Phase 14, Plan 14-02, Task 3.
Requirement: OPS-14-02.

[FLOW:VALIDATION] [DATA-CONTRACT:CORPUS]
"""

import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TESTS_DIR = Path(__file__).resolve().parent / "asset_gen" / "tests"
_MANIFEST_PATH = _TESTS_DIR / "baseline_corpus_manifest.json"

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# Required string fields that must be non-null and non-empty.
_REQUIRED_STRING_FIELDS = ("fixture_id", "source_path", "source_kind",
                           "license", "source_url", "author", "added_date")

# Recognized schema versions.
_SUPPORTED_VERSIONS = {1, 2}


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_manifest() -> list:
    """Run all corpus integrity checks.

    Returns:
        List of failure description strings. Empty list means all pass.
    """
    failures = []

    # --- Load manifest ---
    if not _MANIFEST_PATH.exists():
        return [f"Manifest not found: {_MANIFEST_PATH}"]

    try:
        with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except json.JSONDecodeError as exc:
        return [f"Manifest is not valid JSON: {exc}"]

    # --- Top-level structure ---
    if not isinstance(manifest, dict):
        return ["Manifest root must be a JSON object"]

    sv = manifest.get("schema_version")
    if sv not in _SUPPORTED_VERSIONS:
        failures.append(
            f"Unrecognized schema_version: {sv} "
            f"(supported: {sorted(_SUPPORTED_VERSIONS)})"
        )

    entries = manifest.get("entries")
    if not isinstance(entries, list):
        return ["Manifest missing 'entries' list"]

    if len(entries) == 0:
        failures.append("Manifest has zero entries")
        return failures

    # --- Variant bucket consistency ---
    variant_buckets = manifest.get("variant_buckets", {})
    all_fixture_ids = {e.get("fixture_id") for e in entries}
    for bucket_name, bucket_ids in variant_buckets.items():
        if not isinstance(bucket_ids, list):
            failures.append(
                f"variant_buckets.{bucket_name} must be a list"
            )
            continue
        for bid in bucket_ids:
            if bid not in all_fixture_ids:
                failures.append(
                    f"variant_buckets.{bucket_name} references "
                    f"unknown fixture_id: {bid}"
                )

    # --- Per-entry validation ---
    seen_ids = set()
    for i, entry in enumerate(entries):
        fid = entry.get("fixture_id", f"<entry[{i}]>")

        # Duplicate ID check
        if fid in seen_ids:
            failures.append(f"{fid}: duplicate fixture_id")
        seen_ids.add(fid)

        # Required string fields
        for field in _REQUIRED_STRING_FIELDS:
            val = entry.get(field)
            if val is None:
                failures.append(f"{fid}: required field '{field}' is null")
            elif not isinstance(val, str) or not val.strip():
                failures.append(
                    f"{fid}: required field '{field}' must be a "
                    f"non-empty string, got: {val!r}"
                )

        # SHA256 format
        sha = entry.get("sha256", "")
        if not isinstance(sha, str) or not _SHA256_PATTERN.match(sha):
            failures.append(
                f"{fid}: sha256 must be a 64-char lowercase hex string, "
                f"got: {sha!r}"
            )

    return failures


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """Run CI corpus check and print results.

    Returns:
        0 if all pass, 1 if failures found.
    """
    failures = check_manifest()

    if not failures:
        print(f"CI corpus check: PASS (manifest at {_MANIFEST_PATH})")
        return 0

    print("CI corpus check: FAIL", file=sys.stderr)
    for f in failures:
        print(f"  {f}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
