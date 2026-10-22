#!/usr/bin/env python3
"""Verify baseline corpus manifest checksums and metadata completeness.

Reads baseline_corpus_manifest.json, verifies each SHA256 matches the
file on disk, and checks for required metadata fields. Reports missing
files, checksum mismatches, and missing metadata.

Exit codes:
    0 = all checks pass (or all entries skipped due to missing files)
    1 = checksum mismatch or missing required metadata

Usage:
    python3 scripts/verify_corpus_checksums.py
    python3 scripts/verify_corpus_checksums.py --strict  # fail on missing files too

Phase 14, Plan 14-02, Task 1.
Requirement: OPS-14-02.

[FLOW:VALIDATION] [DATA-CONTRACT:CORPUS]
"""

import hashlib
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TESTS_DIR = Path(__file__).resolve().parent / "asset_gen" / "tests"
_MANIFEST_PATH = _TESTS_DIR / "baseline_corpus_manifest.json"

# Fields that must be non-null strings on every entry (schema v2+).
_REQUIRED_METADATA = ("license", "source_url", "author", "added_date", "sha256")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    """Compute SHA256 hex digest of a file.

    Args:
        path: Path to the file.

    Returns:
        Lowercase hex digest string.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest() -> dict:
    """Load and parse the baseline corpus manifest.

    Returns:
        Parsed manifest dict.

    Raises:
        FileNotFoundError: If manifest file is missing.
    """
    if not _MANIFEST_PATH.exists():
        print(f"ERROR: Manifest not found at {_MANIFEST_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def verify(strict: bool = False) -> int:
    """Run all corpus verification checks.

    Args:
        strict: If True, treat missing source files as failures.

    Returns:
        0 if all checks pass, 1 if any failures found.
    """
    manifest = _load_manifest()
    entries = manifest.get("entries", [])

    if not entries:
        print("WARNING: Manifest has no entries", file=sys.stderr)
        return 1

    failures = []
    skipped = 0
    verified = 0

    for entry in entries:
        fid = entry.get("fixture_id", "<unknown>")

        # --- Metadata completeness ---
        for field in _REQUIRED_METADATA:
            val = entry.get(field)
            if val is None or (isinstance(val, str) and not val.strip()):
                failures.append(f"{fid}: missing or null required field '{field}'")

        # --- SHA256 checksum verification ---
        source_path = Path(entry.get("source_path", ""))
        expected_sha = entry.get("sha256", "")

        if not source_path.exists():
            if strict:
                failures.append(f"{fid}: source file missing: {source_path}")
            else:
                skipped += 1
            continue

        if not expected_sha:
            failures.append(f"{fid}: no sha256 in manifest")
            continue

        actual_sha = _sha256_file(source_path)
        if actual_sha != expected_sha:
            failures.append(
                f"{fid}: SHA256 mismatch\n"
                f"  expected: {expected_sha}\n"
                f"  actual:   {actual_sha}"
            )
        else:
            verified += 1

    # --- Report ---
    total = len(entries)
    print(f"Corpus verification: {total} entries, "
          f"{verified} verified, {skipped} skipped (missing), "
          f"{len(failures)} failures")

    if failures:
        print("\nFAILURES:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    strict_mode = "--strict" in sys.argv
    sys.exit(verify(strict=strict_mode))
