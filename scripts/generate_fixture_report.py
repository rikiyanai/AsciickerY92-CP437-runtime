#!/usr/bin/env python3
"""Generate a fixture verification report and save to .planning/artifacts/.

Produces a JSON report with:
    - Run timestamp, Python version, platform
    - Per-fixture: pass/fail, SHA256 verification, metadata completeness
    - Aggregate: total pass/fail/skip counts

Reports are saved as .planning/artifacts/fixture-report-{date}.json.
Old reports beyond MAX_REPORTS (default 10) are moved to archive/.

Usage:
    python3 scripts/generate_fixture_report.py
    python3 scripts/generate_fixture_report.py --max-reports 5

Phase 14, Plan 14-02, Task 2.
Requirement: OPS-14-03.

[FLOW:VALIDATION] [DATA-CONTRACT:CORPUS]
"""

import hashlib
import json
import os
import platform
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TESTS_DIR = _PROJECT_ROOT / "scripts" / "asset_gen" / "tests"
_MANIFEST_PATH = _TESTS_DIR / "baseline_corpus_manifest.json"
_ARTIFACTS_DIR = _PROJECT_ROOT / ".planning" / "artifacts"
_ARCHIVE_DIR = _ARTIFACTS_DIR / "archive"

MAX_REPORTS = int(os.environ.get("FIXTURE_MAX_REPORTS", "10"))

_REQUIRED_METADATA = ("license", "source_url", "author", "added_date", "sha256")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    """Compute SHA256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest() -> dict:
    """Load the baseline corpus manifest."""
    if not _MANIFEST_PATH.exists():
        print(f"ERROR: Manifest not found at {_MANIFEST_PATH}", file=sys.stderr)
        sys.exit(1)
    with open(_MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _probe_fixture(entry: dict) -> dict:
    """Probe a single fixture entry and return results.

    Args:
        entry: A manifest entry dict.

    Returns:
        Dict with pass/fail status and probe details.
    """
    fid = entry.get("fixture_id", "<unknown>")
    result = {
        "fixture_id": fid,
        "status": "pass",
        "issues": [],
        "sha256_verified": False,
        "file_exists": False,
        "metadata_complete": True,
    }

    # Check metadata completeness
    for field in _REQUIRED_METADATA:
        val = entry.get(field)
        if val is None or (isinstance(val, str) and not val.strip()):
            result["issues"].append(f"missing or null field: {field}")
            result["metadata_complete"] = False
            result["status"] = "fail"

    # Check file existence and SHA256
    source_path = Path(entry.get("source_path", ""))
    expected_sha = entry.get("sha256", "")

    if not source_path.exists():
        result["status"] = "skip"
        result["issues"].append(f"source file not found: {source_path}")
        return result

    result["file_exists"] = True

    if expected_sha:
        start = time.monotonic()
        actual_sha = _sha256_file(source_path)
        elapsed = time.monotonic() - start
        result["sha256_time_s"] = round(elapsed, 3)

        if actual_sha == expected_sha:
            result["sha256_verified"] = True
        else:
            result["status"] = "fail"
            result["issues"].append(
                f"SHA256 mismatch: expected {expected_sha[:16]}..., "
                f"got {actual_sha[:16]}..."
            )
    else:
        result["status"] = "fail"
        result["issues"].append("no sha256 in manifest entry")

    return result


def _rotate_reports(artifacts_dir: Path, archive_dir: Path, max_reports: int) -> int:
    """Rotate old reports beyond max_reports threshold.

    Args:
        artifacts_dir: Directory containing fixture-report-*.json files.
        archive_dir: Directory to move old reports to.
        max_reports: Maximum number of reports to keep.

    Returns:
        Number of reports archived.
    """
    reports = sorted(
        artifacts_dir.glob("fixture-report-*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if len(reports) <= max_reports:
        return 0

    archive_dir.mkdir(parents=True, exist_ok=True)
    archived = 0
    for old_report in reports[max_reports:]:
        dest = archive_dir / old_report.name
        shutil.move(str(old_report), str(dest))
        archived += 1

    return archived


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_report(max_reports: int = MAX_REPORTS) -> Path:
    """Generate a fixture verification report.

    Args:
        max_reports: Maximum reports to retain before archiving.

    Returns:
        Path to the generated report file.
    """
    manifest = _load_manifest()
    entries = manifest.get("entries", [])

    now = datetime.now(timezone.utc)
    report = {
        "report_version": 1,
        "timestamp": now.isoformat(),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "manifest_schema_version": manifest.get("schema_version"),
        "total_entries": len(entries),
        "results": [],
        "aggregate": {
            "pass": 0,
            "fail": 0,
            "skip": 0,
        },
    }

    for entry in entries:
        probe = _probe_fixture(entry)
        report["results"].append(probe)
        status = probe["status"]
        if status in report["aggregate"]:
            report["aggregate"][status] += 1

    # Write report
    _ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = now.strftime("%Y%m%d-%H%M%S")
    report_path = _ARTIFACTS_DIR / f"fixture-report-{date_str}.json"

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
        f.write("\n")

    # Rotate old reports
    archived = _rotate_reports(_ARTIFACTS_DIR, _ARCHIVE_DIR, max_reports)

    # Summary
    agg = report["aggregate"]
    print(f"Fixture report: {report_path.name}")
    print(f"  {agg['pass']} pass, {agg['fail']} fail, {agg['skip']} skip")
    if archived:
        print(f"  Archived {archived} old report(s)")

    return report_path


if __name__ == "__main__":
    mr = MAX_REPORTS
    if "--max-reports" in sys.argv:
        idx = sys.argv.index("--max-reports")
        if idx + 1 < len(sys.argv):
            mr = int(sys.argv[idx + 1])

    path = generate_report(max_reports=mr)

    # Exit 1 if any failures
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if data["aggregate"]["fail"] > 0:
        sys.exit(1)
