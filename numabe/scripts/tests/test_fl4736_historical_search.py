#!/usr/bin/env python3
"""Recall, precision, and provenance gates for FL-4736 historical search."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# Deletion of scripts/launcher_helper_bot/retriever.py -- the operator memory the
# recall gate replays. Only reachable in checkouts that still hold pre-force-push
# history.
_DELETION_COMMIT = "ef4c90ec03d61bda8a235675a188c136a19a940b"

from maintainer.lib import historical_search  # noqa: E402
from maintainer.lib.failure_log import FailureLogSnapshot  # noqa: E402
from maintainer.lib.historical_search import (  # noqa: E402
    HistoricalSearchIndex,
    evaluate_recall_cases,
)


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        env={**os.environ, **(env or {})},
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _entry(path: str) -> dict:
    return {
        "fl": "FL-9000",
        "num": 9000,
        "title": "Old local LLM helper search",
        "status": "IMPLEMENTED-UNPROVEN",
        "effective_status": "IMPLEMENTED-UNPROVEN",
        "date": "2026-01-01",
        "related": ["FL-8999"],
        "overlay": {
            "ProofState": "IMPLEMENTED",
            "CodeRefs": [f"{path}:1"],
            "AnchorFunctions": ["HybridRetriever"],
        },
        "_lines": [
            "#### Fix attempt — historical",
            f"- **Code:** `{path}:1`",
            "Hybrid transcript and code retrieval used the old helper terminology.",
        ],
    }


def test_temp_git_deletion_has_timestamped_old_blob_provenance(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    source = repo / "scripts/launcher_helper_bot/retriever.py"
    source.parent.mkdir(parents=True)
    source.write_text("class HybridRetriever:\n    # Hybrid search across old transcripts.\n    pass\n", encoding="utf-8")
    _git(repo, "add", str(source.relative_to(repo)))
    _git(
        repo,
        "commit",
        "-q",
        "-m",
        "add old helper",
        env={
            "GIT_AUTHOR_DATE": "2025-01-02T03:04:05+09:00",
            "GIT_COMMITTER_DATE": "2025-01-03T04:05:06+09:00",
        },
    )
    source.unlink()
    _git(repo, "add", "-u")
    _git(
        repo,
        "commit",
        "-q",
        "-m",
        "delete stale helper",
        env={
            "GIT_AUTHOR_DATE": "2025-02-02T03:04:05-04:00",
            "GIT_COMMITTER_DATE": "2025-02-03T04:05:06-04:00",
        },
    )

    report = HistoricalSearchIndex(repo, [_entry(str(source.relative_to(repo)))]).search(
        "old local llm stale",
        limit=10,
    )
    deletion = next(result for result in report.results if result.provenance_role == "deleted")

    assert deletion.source_path == "scripts/launcher_helper_bot/retriever.py"
    assert deletion.excerpt.startswith("# Hybrid search")
    assert deletion.authored_at == "2025-02-02T03:04:05-04:00"
    assert deletion.committed_at == "2025-02-03T04:05:06-04:00"
    assert deletion.deletion_commit == deletion.commit
    assert deletion.parents
    assert deletion.head_state == "absent"
    assert deletion.why_ranked


def test_identifier_and_symbol_channel_searches_current_source() -> None:
    report = HistoricalSearchIndex(REPO_ROOT, []).search(
        "FailureLogSnapshot",
        include_git=False,
        limit=5,
    )
    source = next(result for result in report.results if result.source_kind == "current_source")
    assert source.source_path == "scripts/maintainer/lib/failure_log.py"
    assert "identifier" in source.match_modes
    assert source.commit
    assert source.why_ranked


def test_missing_ripgrep_is_reported_instead_of_silently_dropping_source_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_run = subprocess.run

    def fake_run(args, *rest, **kwargs):
        if args and args[0] == "rg":
            raise OSError("rg not installed")
        return real_run(args, *rest, **kwargs)

    monkeypatch.setattr(historical_search.subprocess, "run", fake_run)

    report = HistoricalSearchIndex(REPO_ROOT, []).search(
        "FailureLogSnapshot",
        include_git=False,
        limit=5,
    )

    assert not [result for result in report.results if result.source_kind == "current_source"]
    # Query terms are normalized to lower case before the channel runs.
    assert "current_source:failurelogsnapshot:rg_unavailable" in report.history_scope["stop_reasons"]
    assert report.history_scope["truncated"] is True


def test_real_operator_memory_recall_and_precision_gate() -> None:
    # This gate replays operator memories that only exist in pre-force-push
    # history. origin/main was force-rewritten on 2026-06-25, so a fresh clone
    # cannot reach the deletion commit below and the recall numbers would be
    # measuring a different repository. Skip rather than report a failure that
    # says nothing about the search code.
    archival = subprocess.run(
        ["git", "cat-file", "-e", f"{_DELETION_COMMIT}^{{commit}}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if archival.returncode != 0:
        pytest.skip("archival refs absent on this clone; recall gate requires the historical checkout")

    fixture_path = Path(__file__).with_name("fixtures") / "fl4736_remembered_cases.json"
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))
    entries = FailureLogSnapshot(REPO_ROOT / "docs/FAILURE_LOG.md").rows()
    reports = {
        case["case_id"]: HistoricalSearchIndex(REPO_ROOT, entries).search(
            case["query"],
            limit=10,
        )
        for case in cases
    }

    metrics = evaluate_recall_cases(reports, cases, k=20)

    assert metrics["passed"], metrics
    assert metrics["recall_at_k"] == 1.0
    assert metrics["precision_at_k"] >= 0.25
    report = reports["operator_old_local_llm_helper"]
    assert len(report.results) <= 50
    deletion = next(
        result
        for result in report.results
        if result.source_path == "scripts/launcher_helper_bot/retriever.py"
        and result.provenance_role == "deleted"
    )
    assert deletion.commit == _DELETION_COMMIT
    assert deletion.committed_at == "2026-05-19T15:25:50-04:00"
    # The report must describe this clone accurately. Pinning the flag to one
    # value would only assert which machine ran the test.
    assert report.history_scope["shallow"] == (
        _git(REPO_ROOT, "rev-parse", "--is-shallow-repository").lower() == "true"
    )


def test_canonical_fl_front_door_emits_grouped_explainable_contract() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/fl"),
            "search",
            "old local llm stale",
            "--no-git",
            "--limit",
            "5",
            "--json",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["schema"] == "fl.historical_search.v1"
    assert [row["fl"] for row in payload["groups"]["prior_attempt"]] == [
        "FL-2794",
        "FL-2931",
    ]
    assert all(
        row["excerpt"]
        and row["source_path"]
        and row["date"]
        and row["commit"]
        and row["why_ranked"]
        for rows in payload["groups"].values()
        for row in rows
    )


def test_legacy_direct_json_shape_remains_available() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/analyze_runs.py"),
            "fl",
            "search",
            "launcher helper bot",
            "--legacy-direct",
            "--json",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert isinstance(payload, list)
    assert payload
    assert set(payload[0]) == {"fl", "title", "status", "date"}
