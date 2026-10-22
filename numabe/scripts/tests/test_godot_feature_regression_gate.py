#!/usr/bin/env python3
"""Unit and CLI tests for the fail-closed Godot feature registry gate."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "godot_feature_regression_gate.py"
sys.path.insert(0, str(SCRIPT.parent))
import godot_feature_regression_gate as gate  # noqa: E402


def _provenance() -> dict[str, str]:
    return {
        "source": "fixture-history-census",
        "revision": "fixture-revision-1",
        "generated_at": "2026-08-28T00:00:00Z",
    }


def _command(*, code: str = "print('ok')", executable: str | None = None) -> dict[str, object]:
    return {
        "kind": "command",
        "argv": [executable or sys.executable, "-c", code],
        "cwd": str(ROOT),
        "timeout_seconds": 5,
        "max_output_bytes": 64,
    }


def _runtime_check(
    receipt_path: Path,
    *,
    feature_id: str = "feature.example",
    source_head: str | None = None,
    route_id: str = "fixture-route",
    behavior_observed: bool = True,
    payload_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    target_identity = {
        "target": "fixture-target",
        "build": "fixture-build",
        "map": "fixture-map",
        "scene": "fixture-scene",
        "pose": "fixture-pose",
        "camera": "fixture-camera",
        "mode": "fixture-mode",
        "baseline": "fixture-baseline",
        "timing_window": "fixture-window",
    }
    payload: dict[str, object] = {
        "schema": "fixture.runtime.v1",
        "feature_id": feature_id,
        "source_head": source_head or _git_head(ROOT),
        "route_id": route_id,
        "target_identity": target_identity,
        "behavior_observed": behavior_observed,
        "observation": "fresh fixture observation",
    }
    if payload_overrides:
        payload.update(payload_overrides)
    code = (
        "import json; from pathlib import Path; "
        f"Path({str(receipt_path)!r}).write_text(json.dumps({payload!r}))"
    )
    check = _command(code=code)
    check.update(
        {
            "receipt_path": str(receipt_path),
            "receipt_schema": "fixture.runtime.v1",
            "route_id": route_id,
            "target_identity": target_identity,
            "required_observation_keys": ["observation"],
        }
    )
    return check


def _git_head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _feature(
    evidence_id: str = "evidence.example",
    *,
    feature_id: str = "feature.example",
    state: str = "active",
    behavior_class: str = "static",
    check: dict[str, object] | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "id": feature_id,
        "name": "fixture feature",
        "category": "fixture",
        "state": state,
        "evidence_refs": [evidence_id],
        "source_owners": ["fixture/source.py"],
        "observable": "fixture observable behavior",
        "behavior_class": behavior_class,
        "check": check or _command(),
    }
    if state == "retired":
        row["operator_approval_ref"] = "approval.fixture"
        row["retirement_record"] = "Retired by explicit fixture operator record."
    return row


def _registry(
    feature: dict[str, object] | None = None,
    *,
    expected_path: Path,
    expected: list[str] | None = None,
    base_dir: Path = ROOT,
    operator_reference_commit: str | None = None,
    graph_baseline_commit: str | None = None,
    head_commit: str | None = None,
    history_paths: list[str] | None = None,
    classified_commits: list[str] | None = None,
) -> dict[str, object]:
    row = feature or _feature()
    expected_ids = expected if expected is not None else ["evidence.example"]
    current_head = head_commit or _git_head(base_dir)
    if classified_commits is None:
        classified_commits = [
            {
                "commit": current_head,
                "disposition": "feature-introducing",
                "feature_ids": [row.get("id", "")],
                "reason": "fixture baseline census record",
                "evidence_refs": expected_ids,
                "process_failure_category": None,
            }
        ]
    classified_commits = [
        {**record, "review_state": record.get("review_state", "resolved")}
        for record in classified_commits
    ]
    expected_document: object = {
        "version": 1,
        "feature_ids": [row.get("id", "")],
        "evidence_ids": expected_ids,
        "operator_reference_commit": operator_reference_commit or current_head,
        "graph_baseline_commit": graph_baseline_commit or current_head,
        "coverage_through_commit": current_head,
        "history_paths": history_paths if history_paths is not None else [],
        "classified_commits": classified_commits,
        "retirement_approvals": [],
    }
    if row.get("state") == "retired":
        expected_document["retirement_approvals"] = [
            {
                "ref": row.get("operator_approval_ref", ""),
                "feature_id": row.get("id", ""),
                "operator_approved": True,
                "fl_ref": "FL-1234",
                "record_marker": (
                    "OPERATOR_APPROVED_FEATURE_RETIREMENT "
                    f"feature={row.get('id', '')} ref={row.get('operator_approval_ref', '')}"
                ),
            }
        ]
    expected_path.write_text(
        json.dumps(expected_document),
        encoding="utf-8",
    )
    return {
        "version": 1,
        "census": {
            "provenance": _provenance(),
            "expected_census_path": str(expected_path),
        },
        "features": [row],
    }


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _git_commit_body(repo: Path, commit: str) -> str:
    raw = subprocess.run(
        ["git", "cat-file", "commit", commit],
        cwd=repo,
        capture_output=True,
        check=True,
    ).stdout
    return raw.split(b"\n\n", 1)[1].decode("utf-8")


def _make_history_repo(tmp_path: Path) -> tuple[Path, str, str, str, str]:
    repo = tmp_path / "history-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "baseline")
    baseline = _git_head(repo)
    (repo / "tracked.txt").write_text("relevant\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "relevant")
    relevant = _git_head(repo)
    (repo / "unrelated.txt").write_text("outside history path\n", encoding="utf-8")
    _git(repo, "add", "unrelated.txt")
    _git(repo, "commit", "-qm", "unrelated")
    head = _git_head(repo)
    return repo, baseline, relevant, head, _git(repo, "rev-list", f"{baseline}..{head}")


def _history_registry(
    repo: Path,
    baseline: str,
    head: str,
    expected_path: Path,
    *,
    history_paths: list[str],
    classified_commits: list[str],
    include_baseline: bool = True,
    operator_reference_commit: str | None = None,
) -> dict[str, object]:
    commit_ids = list(classified_commits)
    if include_baseline and baseline not in commit_ids:
        commit_ids.insert(0, baseline)
    records = [
        {
            "commit": commit,
            "disposition": "feature-introducing",
            "feature_ids": ["feature.example"],
            "reason": "fixture feature census record",
            "evidence_refs": ["evidence.example"],
            "process_failure_category": None,
        }
        for commit in commit_ids
    ]
    return _registry(
        expected_path=expected_path,
        base_dir=repo,
        operator_reference_commit=operator_reference_commit or baseline,
        graph_baseline_commit=baseline,
        head_commit=head,
        history_paths=history_paths,
        classified_commits=records,
    )


def _make_rewritten_equivalent_repo(
    tmp_path: Path,
    *,
    identity_mutation: str | None = None,
) -> tuple[Path, str, str]:
    """Create an unreachable reference and a rewritten equivalent graph commit."""

    repo = tmp_path / "rewritten-equivalent-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "seed.txt")
    _git(repo, "commit", "-qm", "seed")
    seed = _git_head(repo)

    (repo / "target.txt").write_text("target\n", encoding="utf-8")
    _git(repo, "add", "target.txt")
    _git(repo, "commit", "-qm", "equivalent")
    operator_reference = _git_head(repo)
    reference_tree = _git(repo, "show", "-s", "--format=%T", operator_reference)
    reference_message = _git_commit_body(repo, operator_reference)
    reference_author_name = _git(repo, "show", "-s", "--format=%an", operator_reference)
    reference_author_email = _git(repo, "show", "-s", "--format=%ae", operator_reference)
    reference_author_timestamp = _git(repo, "show", "-s", "--format=%aI", operator_reference)
    reference_committer_name = _git(repo, "show", "-s", "--format=%cn", operator_reference)
    reference_committer_email = _git(repo, "show", "-s", "--format=%ce", operator_reference)
    reference_committer_timestamp = _git(repo, "show", "-s", "--format=%cI", operator_reference)

    _git(repo, "checkout", "-qb", "rewritten", seed)
    (repo / "different-parent.txt").write_text("different parent\n", encoding="utf-8")
    _git(repo, "add", "different-parent.txt")
    _git(repo, "commit", "-qm", "different parent")
    different_parent = _git_head(repo)

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": reference_author_name,
            "GIT_AUTHOR_EMAIL": reference_author_email,
            "GIT_AUTHOR_DATE": reference_author_timestamp,
            "GIT_COMMITTER_NAME": reference_committer_name,
            "GIT_COMMITTER_EMAIL": reference_committer_email,
            "GIT_COMMITTER_DATE": reference_committer_timestamp,
        }
    )
    rewritten_tree = reference_tree
    rewritten_message = reference_message
    if identity_mutation == "tree":
        rewritten_tree = _git(repo, "show", "-s", "--format=%T", different_parent)
    elif identity_mutation == "subject":
        rewritten_message = "different subject\n"
    elif identity_mutation == "message_body":
        rewritten_message = reference_message.rstrip("\n") + "\n\nchanged body\n"
    elif identity_mutation == "author_name":
        env["GIT_AUTHOR_NAME"] = "different-author"
    elif identity_mutation == "author_email":
        env["GIT_AUTHOR_EMAIL"] = "different-author@example.invalid"
    elif identity_mutation == "author_timestamp":
        env["GIT_AUTHOR_DATE"] = "2000-01-01T00:00:00+00:00"
    elif identity_mutation == "committer_name":
        env["GIT_COMMITTER_NAME"] = "different-committer"
    elif identity_mutation == "committer_email":
        env["GIT_COMMITTER_EMAIL"] = "different-committer@example.invalid"
    elif identity_mutation == "committer_timestamp":
        env["GIT_COMMITTER_DATE"] = "2000-01-01T00:00:00+00:00"
    elif identity_mutation is not None:
        raise AssertionError(f"unknown identity mutation {identity_mutation!r}")

    rewritten = subprocess.run(
        ["git", "commit-tree", rewritten_tree, "-p", different_parent],
        cwd=repo,
        input=rewritten_message,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    ).stdout.strip()
    _git(repo, "reset", "--hard", "-q", rewritten)
    return repo, operator_reference, rewritten


def _make_continuity_fixture(
    tmp_path: Path,
    *,
    prior_feature: dict[str, object] | None = None,
) -> tuple[Path, str, dict[str, object], Path]:
    repo = tmp_path / "continuity-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "seed.txt")
    _git(repo, "commit", "-qm", "seed")
    seed = _git_head(repo)
    expected_path = repo / "expected.json"
    registry_path = repo / "registry.json"
    prior_feature = prior_feature or _feature(
        feature_id="feature.old", evidence_id="evidence.old", behavior_class="static"
    )
    prior_registry = _registry(
        prior_feature,
        expected_path=expected_path,
        expected=["evidence.old"],
        base_dir=repo,
        operator_reference_commit=seed,
        graph_baseline_commit=seed,
        head_commit=seed,
        history_paths=[],
        classified_commits=[
            {
                "commit": seed,
                "disposition": "feature-introducing",
                "feature_ids": ["feature.old"],
                "reason": "fixture baseline census record",
                "evidence_refs": ["evidence.old"],
                "process_failure_category": None,
            }
        ],
    )
    registry_path.write_text(json.dumps(prior_registry), encoding="utf-8")
    _git(repo, "add", "registry.json", "expected.json")
    _git(repo, "commit", "-qm", "registry baseline")
    snapshot = _git_head(repo)
    return repo, snapshot, prior_feature, registry_path


def _extend_continuity_census(
    repo: Path,
    snapshot: str,
    feature_id: str = "feature.old",
    evidence_id: str = "evidence.old",
) -> None:
    """Keep the prior anchor record while extending a current fixture census."""

    expected_path = repo / "expected.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    prior_seed = _git(repo, "rev-parse", f"{snapshot}^")
    expected["operator_reference_commit"] = prior_seed
    expected["graph_baseline_commit"] = prior_seed
    expected["coverage_through_commit"] = snapshot
    records = expected.setdefault("classified_commits", [])
    known_commits = {record.get("commit") for record in records if isinstance(record, dict)}
    if prior_seed not in known_commits:
        records.insert(
            0,
            {
                "commit": prior_seed,
                "disposition": "feature-introducing",
                "feature_ids": [feature_id],
                "reason": "fixture baseline census record",
                "evidence_refs": [evidence_id],
                "process_failure_category": None,
                "review_state": "resolved",
            },
        )
    if snapshot not in known_commits:
        records.append(
            {
                "commit": snapshot,
                "disposition": "feature-introducing",
                "feature_ids": [feature_id],
                "reason": "fixture registry baseline record",
                "evidence_refs": [evidence_id],
                "process_failure_category": None,
                "review_state": "resolved",
            }
        )
    expected_path.write_text(json.dumps(expected), encoding="utf-8")


def _make_classified_continuity_fixture(
    tmp_path: Path,
) -> tuple[Path, str, str, Path, dict[str, object]]:
    repo = tmp_path / "classified-continuity-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "seed.txt")
    _git(repo, "commit", "-qm", "seed")
    baseline = _git_head(repo)
    (repo / "history.txt").write_text("classified\n", encoding="utf-8")
    _git(repo, "add", "history.txt")
    _git(repo, "commit", "-qm", "classified")
    classified = _git_head(repo)
    feature = _feature(
        feature_id="feature.old",
        evidence_id="evidence.old",
        behavior_class="static",
    )
    record = {
        "commit": classified,
        "disposition": "feature-introducing",
        "feature_ids": ["feature.old"],
        "reason": "fixture classified feature commit",
        "evidence_refs": ["evidence.old"],
        "process_failure_category": None,
    }
    expected_path = repo / "expected.json"
    registry_path = repo / "registry.json"
    registry = _registry(
        feature,
        expected_path=expected_path,
        expected=["evidence.old"],
        base_dir=repo,
        operator_reference_commit=baseline,
        graph_baseline_commit=baseline,
        head_commit=classified,
        history_paths=[],
        classified_commits=[
            {
                "commit": baseline,
                "disposition": "no-feature",
                "feature_ids": [],
                "reason": "fixture seed commit",
                "evidence_refs": [],
                "process_failure_category": None,
            },
            record,
        ],
    )
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    _git(repo, "add", "registry.json", "expected.json")
    _git(
        repo,
        "commit",
        "-qm",
        "registry baseline\n\nGodot-Feature-Impact: feature.old",
    )
    snapshot = _git_head(repo)
    return repo, snapshot, classified, registry_path, record


def test_valid_registry_validates_and_runs(tmp_path: Path) -> None:
    registry = _registry(expected_path=tmp_path / "expected.json")
    validation = gate.validate_registry(registry, base_dir=ROOT)
    assert validation.ok, validation.errors
    result = gate.run_registry(registry, base_dir=ROOT)
    assert result.ok, result.to_dict()
    assert result.checks[0].status == "passed"
    assert result.checks[0].exit_code == 0


def test_valid_check_runs_when_unrelated_row_is_unresolved(tmp_path: Path) -> None:
    marker = tmp_path / "valid-check-runs.txt"
    valid_check = _command(
        code=f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"
    )
    valid = _feature(check=valid_check)
    unresolved = _feature(
        feature_id="feature.unresolved",
        evidence_id="evidence.unresolved",
        behavior_class="runtime",
    )
    unresolved["check"] = None
    registry = _registry(
        valid,
        expected_path=tmp_path / "expected.json",
        expected=["evidence.example", "evidence.unresolved"],
    )
    registry["features"].append(unresolved)
    expected = json.loads((tmp_path / "expected.json").read_text(encoding="utf-8"))
    expected["feature_ids"].append("feature.unresolved")
    (tmp_path / "expected.json").write_text(json.dumps(expected), encoding="utf-8")

    result = gate.run_registry(registry, base_dir=ROOT)

    assert marker.read_text(encoding="utf-8") == "ran"
    assert [check.feature_id for check in result.checks] == [
        "feature.example",
        "feature.unresolved",
    ]
    assert result.checks[0].status == "passed"
    assert result.checks[1].status == "skipped"
    assert not result.ok
    assert any(
        "feature.unresolved" in error and "check not executed" in error
        for error in result.errors
    )


def test_invalid_check_spec_is_skipped_without_execution(tmp_path: Path) -> None:
    marker = tmp_path / "invalid-check-ran.txt"
    invalid_check = _command(
        code=f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"
    )
    invalid_check["timeout_seconds"] = 0
    invalid = _feature(check=invalid_check)
    result = gate.run_registry(
        _registry(invalid, expected_path=tmp_path / "expected.json"), base_dir=ROOT
    )

    assert not result.ok
    assert not marker.exists()
    assert len(result.checks) == 1
    assert result.checks[0].status == "skipped"
    assert "check not executed" in (result.checks[0].error or "")
    assert "timeout_seconds" in (result.checks[0].error or "")


def test_runtime_job_runs_after_census_failure_and_keeps_census_dependency() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "godot-feature-regressions.yml").read_text(
            encoding="utf-8"
        )
    )
    runtime = workflow["jobs"]["registry-runtime"]
    assert runtime["needs"] == "registry-census"
    assert runtime["if"] in {"always()", "${{ always() }}"}
    assert "--run" in "\n".join(
        step.get("run", "")
        for step in runtime["steps"]
        if isinstance(step, dict)
    )
    workflow_text = (
        ROOT / ".github" / "workflows" / "godot-feature-regressions.yml"
    ).read_text(encoding="utf-8")
    assert "paths:" not in workflow_text


def test_nonempty_history_paths_are_rejected(
    tmp_path: Path,
) -> None:
    repo, baseline, relevant, head, _ = _make_history_repo(tmp_path)
    registry = _history_registry(
        repo,
        baseline,
        head,
        tmp_path / "expected.json",
        history_paths=["tracked.txt"],
        classified_commits=[relevant],
    )
    result = gate.validate_registry(registry, base_dir=repo)
    assert not result.ok
    assert any("history_paths: must be []" in error for error in result.errors)


def test_omitted_relevant_history_commit_fails_closed(tmp_path: Path) -> None:
    repo, baseline, _, head, _ = _make_history_repo(tmp_path)
    registry = _history_registry(
        repo,
        baseline,
        head,
        tmp_path / "expected.json",
        history_paths=[],
        classified_commits=[],
    )
    result = gate.validate_registry(registry, base_dir=repo)
    assert not result.ok
    assert any("unclassified reachable commit" in error for error in result.errors)


def test_extra_classified_history_commit_fails_closed(tmp_path: Path) -> None:
    repo, baseline, relevant, _, all_commits_text = _make_history_repo(tmp_path)
    extra = next(commit for commit in all_commits_text.splitlines() if commit != relevant)
    registry = _history_registry(
        repo,
        baseline,
        relevant,
        tmp_path / "expected.json",
        history_paths=[],
        classified_commits=[relevant, extra],
    )
    result = gate.validate_registry(registry, base_dir=repo, historical_audit=True)
    assert not result.ok
    assert any("classified commit not in reachable range" in error for error in result.errors)


def test_duplicate_classified_history_commit_fails_closed(tmp_path: Path) -> None:
    repo, baseline, relevant, _, _ = _make_history_repo(tmp_path)
    registry = _history_registry(
        repo,
        baseline,
        relevant,
        tmp_path / "expected.json",
        history_paths=[],
        classified_commits=[relevant],
    )
    expected_path = Path(registry["census"]["expected_census_path"])
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["classified_commits"].append(dict(expected["classified_commits"][-1]))
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    result = gate.validate_registry(registry, base_dir=repo, historical_audit=True)
    assert not result.ok
    assert any("duplicate classified commit" in error for error in result.errors)


def test_future_descendant_without_trailer_fails_normal_but_historical_audit_passes(
    tmp_path: Path,
) -> None:
    repo, baseline, relevant, _, _ = _make_history_repo(tmp_path)
    registry = _history_registry(
        repo,
        baseline,
        relevant,
        tmp_path / "expected.json",
        history_paths=[],
        classified_commits=[relevant],
    )
    normal = gate.validate_registry(registry, base_dir=repo)
    assert not normal.ok
    assert any("requires exactly one" in error for error in normal.errors)
    historical = gate.validate_registry(registry, base_dir=repo, historical_audit=True)
    assert historical.ok, historical.errors


def test_explicit_empty_history_paths_covers_full_reachable_graph(
    tmp_path: Path,
) -> None:
    repo, baseline, _, head, all_commits_text = _make_history_repo(tmp_path)
    all_commits = all_commits_text.splitlines()
    registry = _history_registry(
        repo,
        baseline,
        head,
        tmp_path / "expected.json",
        history_paths=[],
        classified_commits=all_commits,
    )
    result = gate.validate_registry(registry, base_dir=repo)
    assert result.ok, result.to_dict()
    assert result.history_commit_count == len(all_commits) + 1


def test_rewritten_graph_baseline_matches_unreachable_operator_reference(
    tmp_path: Path,
) -> None:
    repo, operator_reference, graph_baseline = _make_rewritten_equivalent_repo(tmp_path)
    registry = _history_registry(
        repo,
        graph_baseline,
        graph_baseline,
        tmp_path / "expected.json",
        history_paths=[],
        classified_commits=[graph_baseline],
        operator_reference_commit=operator_reference,
    )
    result = gate.validate_registry(registry, base_dir=repo)
    assert result.ok, result.to_dict()
    assert result.history_commit_count == 1
    assert (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", operator_reference, graph_baseline],
            cwd=repo,
            check=False,
        ).returncode
        == 1
    )


@pytest.mark.parametrize(
    "identity_field",
    [
        "tree",
        "author_name",
        "author_email",
        "author_timestamp",
        "committer_name",
        "committer_email",
        "committer_timestamp",
        "subject",
        "message_sha256",
    ],
)
def test_rewritten_reference_identity_mismatch_fails_closed(
    tmp_path: Path,
    identity_field: str,
) -> None:
    repo, operator_reference, graph_baseline = _make_rewritten_equivalent_repo(
        tmp_path,
        identity_mutation=(
            "message_body" if identity_field == "message_sha256" else identity_field
        ),
    )
    registry = _history_registry(
        repo,
        graph_baseline,
        graph_baseline,
        tmp_path / "expected.json",
        history_paths=[],
        classified_commits=[graph_baseline],
        operator_reference_commit=operator_reference,
    )
    result = gate.validate_registry(registry, base_dir=repo)
    assert not result.ok
    assert any(f"differ in {identity_field}" in error for error in result.errors)


@pytest.mark.parametrize("missing_field", ["operator_reference_commit", "graph_baseline_commit"])
def test_rewritten_census_requires_both_commit_identities(
    tmp_path: Path,
    missing_field: str,
) -> None:
    repo, operator_reference, graph_baseline = _make_rewritten_equivalent_repo(tmp_path)
    registry = _history_registry(
        repo,
        graph_baseline,
        graph_baseline,
        tmp_path / "expected.json",
        history_paths=[],
        classified_commits=[graph_baseline],
        operator_reference_commit=operator_reference,
    )
    expected_path = Path(registry["census"]["expected_census_path"])
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    del expected[missing_field]
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    result = gate.validate_registry(registry, base_dir=repo)
    assert not result.ok
    assert any(f"{missing_field}: required non-empty string" in error for error in result.errors)


def test_graph_baseline_must_be_ancestor_of_coverage_through(
    tmp_path: Path,
) -> None:
    repo, operator_reference, graph_baseline = _make_rewritten_equivalent_repo(tmp_path)
    registry = _history_registry(
        repo,
        operator_reference,
        operator_reference,
        tmp_path / "expected.json",
        history_paths=[],
        classified_commits=[operator_reference, graph_baseline],
        operator_reference_commit=operator_reference,
    )
    expected_path = Path(registry["census"]["expected_census_path"])
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["graph_baseline_commit"] = graph_baseline
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    result = gate.validate_registry(registry, base_dir=repo, historical_audit=True)
    assert not result.ok
    assert any(
        "graph_baseline_commit is not an ancestor of coverage_through_commit" in error
        for error in result.errors
    )


# This is no new route; it exercises the complete graph invariant.
def test_full_graph_requires_pathless_merge_commit(tmp_path: Path) -> None:
    repo = tmp_path / "merge-history-repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    (repo / "tracked.txt").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-qm", "baseline")
    baseline = _git_head(repo)
    main_branch = _git(repo, "branch", "--show-current")
    _git(repo, "checkout", "-qb", "side")
    (repo / "side.txt").write_text("side branch\n", encoding="utf-8")
    _git(repo, "add", "side.txt")
    _git(repo, "commit", "-qm", "side change")
    side = _git_head(repo)
    _git(repo, "checkout", "-q", main_branch)
    _git(repo, "merge", "--no-ff", "-s", "ours", "-q", "-m", "pathless merge", "side")
    head = _git_head(repo)
    all_commits = _git(repo, "rev-list", f"{baseline}..{head}").splitlines()
    assert side in all_commits
    assert len(_git(repo, "show", "-s", "--format=%P", head).split()) == 2

    complete = _history_registry(
        repo,
        baseline,
        head,
        tmp_path / "complete-expected.json",
        history_paths=[],
        classified_commits=all_commits,
    )
    complete_result = gate.validate_registry(complete, base_dir=repo)
    assert complete_result.ok, complete_result.to_dict()

    omitted_merge = [commit for commit in all_commits if commit != head]
    omitted = _history_registry(
        repo,
        baseline,
        head,
        tmp_path / "omitted-expected.json",
        history_paths=[],
        classified_commits=omitted_merge,
    )
    omitted_result = gate.validate_registry(omitted, base_dir=repo)
    assert not omitted_result.ok
    assert any("unclassified reachable commit" in error for error in omitted_result.errors)


def test_full_graph_requires_graph_baseline_commit_record(tmp_path: Path) -> None:
    repo, baseline, _, head, all_commits = _make_history_repo(tmp_path)
    registry = _history_registry(
        repo,
        baseline,
        head,
        tmp_path / "expected.json",
        history_paths=[],
        classified_commits=all_commits.splitlines(),
        include_baseline=False,
    )
    result = gate.validate_registry(registry, base_dir=repo)
    assert not result.ok
    assert any(
        f"unclassified reachable commit: {baseline}" in error
        for error in result.errors
    )


def test_future_descendant_unknown_feature_trailer_fails_closed(tmp_path: Path) -> None:
    repo, baseline, relevant, head, _ = _make_history_repo(tmp_path)
    _git(
        repo,
        "commit",
        "--amend",
        "-qm",
        "unrelated\n\nGodot-Feature-Impact: feature.unknown",
    )
    head = _git_head(repo)
    registry = _history_registry(
        repo,
        baseline,
        relevant,
        tmp_path / "expected.json",
        history_paths=[],
        classified_commits=[relevant],
    )
    result = gate.validate_registry(registry, base_dir=repo)
    assert not result.ok
    assert any("unknown feature" in error for error in result.errors)
    assert head != relevant


def test_future_descendant_explicit_no_impact_reason_passes(tmp_path: Path) -> None:
    repo, baseline, relevant, _, _ = _make_history_repo(tmp_path)
    _git(
        repo,
        "commit",
        "--amend",
        "-qm",
        "unrelated\n\nGodot-Feature-Impact: none\nGodot-Feature-Reason: outside history path",
    )
    registry = _history_registry(
        repo,
        baseline,
        relevant,
        tmp_path / "expected.json",
        history_paths=[],
        classified_commits=[relevant],
    )
    result = gate.validate_registry(registry, base_dir=repo)
    assert result.ok, result.to_dict()
    assert result.descendant_commit_count == 1


def test_missing_feature_census_evidence_fails_closed(tmp_path: Path) -> None:
    registry = _registry(
        expected_path=tmp_path / "expected.json",
        feature=None,
        expected=["evidence.example", "evidence.removed"],
    )
    result = gate.validate_registry(registry, base_dir=ROOT)
    assert not result.ok
    assert any("expected evidence id omitted" in error for error in result.errors)


def test_malformed_census_provenance_fails_closed(tmp_path: Path) -> None:
    registry = _registry(expected_path=tmp_path / "expected.json")
    registry["census"] = {
        "provenance": {"source": "fixture-history-census", "revision": ""},
        "expected_census_path": str(tmp_path / "expected.json"),
    }
    result = gate.validate_registry(registry, base_dir=ROOT)
    assert not result.ok
    assert any("generated_at" in error for error in result.errors)
    assert any("revision" in error for error in result.errors)


def test_deleting_feature_and_registry_expectation_cannot_weaken_external_census(
    tmp_path: Path,
) -> None:
    registry = _registry(expected_path=tmp_path / "expected.json")
    registry["features"] = []
    result = gate.validate_registry(registry, base_dir=ROOT)
    assert not result.ok
    assert any("features: must not be empty" in error for error in result.errors)
    assert any("expected evidence id omitted" in error for error in result.errors)


def test_forged_retirement_strings_without_external_approval_fail_closed(
    tmp_path: Path,
) -> None:
    feature = _feature(state="retired")
    registry = _registry(feature, expected_path=tmp_path / "expected.json")
    Path(registry["census"]["expected_census_path"]).write_text(
        json.dumps(
            {
                "version": 1,
                "evidence_ids": ["evidence.example"],
                "retirement_approvals": [],
            }
        ),
        encoding="utf-8",
    )
    result = gate.validate_registry(registry, base_dir=ROOT)
    assert not result.ok
    assert any("no independent approval record" in error for error in result.errors)


def test_active_feature_missing_check_fails_closed(tmp_path: Path) -> None:
    feature = _feature()
    del feature["check"]
    result = gate.validate_registry(
        _registry(feature, expected_path=tmp_path / "expected.json"), base_dir=ROOT
    )
    assert not result.ok
    assert any("check: must be an object" in error for error in result.errors)


def test_runtime_feature_cannot_use_static_contract(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    source.write_text("fixture marker\n", encoding="utf-8")
    feature = _feature(
        behavior_class="runtime",
        check={"kind": "static-contract", "path": str(source), "contains": "fixture marker"},
    )
    result = gate.validate_registry(
        _registry(feature, expected_path=tmp_path / "expected.json"), base_dir=ROOT
    )
    assert not result.ok
    assert any("static-contract is not valid" in error for error in result.errors)


def test_runtime_command_requires_receipt_contract(tmp_path: Path) -> None:
    feature = _feature(
        behavior_class="runtime",
        check=_command(executable="/usr/bin/true"),
    )
    result = gate.validate_registry(
        _registry(feature, expected_path=tmp_path / "expected.json"), base_dir=ROOT
    )
    assert not result.ok
    assert any("receipt_path: required" in error for error in result.errors)


def test_intake_only_presence_analyzer_cannot_own_temporal_behavior(
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "intake.json"
    check = _runtime_check(receipt_path)
    check["argv"] = [
        sys.executable,
        "scripts/analyze_feature_presence.py",
        "--capture",
        str(tmp_path),
    ]
    check["required_observation_keys"] = ["observation", "motion", "coherence"]
    feature = _feature(behavior_class="temporal", check=check)
    result = gate.validate_registry(
        _registry(feature, expected_path=tmp_path / "expected.json"), base_dir=ROOT
    )
    assert not result.ok
    assert any("intake-only analyzer" in error for error in result.errors)


def test_runtime_stale_preexisting_receipt_fails_closed(tmp_path: Path) -> None:
    receipt_path = tmp_path / "runtime.json"
    target_identity = {
        "target": "fixture-target",
        "build": "fixture-build",
        "map": "fixture-map",
        "scene": "fixture-scene",
        "pose": "fixture-pose",
        "camera": "fixture-camera",
        "mode": "fixture-mode",
        "baseline": "fixture-baseline",
        "timing_window": "fixture-window",
    }
    receipt_path.write_text(
        json.dumps(
            {
                "schema": "fixture.runtime.v1",
                "feature_id": "feature.example",
                "source_head": _git_head(ROOT),
                "route_id": "fixture-route",
                "target_identity": target_identity,
                "behavior_observed": True,
                "observation": "stale",
            }
        ),
        encoding="utf-8",
    )
    stale_check = _runtime_check(receipt_path)
    stale_check["argv"] = [sys.executable, "-c", "pass"]
    feature = _feature(behavior_class="runtime", check=stale_check)
    result = gate.run_registry(
        _registry(feature, expected_path=tmp_path / "expected.json"), base_dir=ROOT
    )
    assert not result.ok
    assert "not newly written" in (result.checks[0].error or "")


def test_runtime_fresh_matching_receipt_passes(tmp_path: Path) -> None:
    receipt_path = tmp_path / "runtime.json"
    feature = _feature(
        behavior_class="runtime",
        check=_runtime_check(receipt_path),
    )
    result = gate.run_registry(
        _registry(feature, expected_path=tmp_path / "expected.json"), base_dir=ROOT
    )
    assert result.ok, result.to_dict()
    assert result.checks[0].status == "passed"


def test_unauthorized_retirement_fails_closed(tmp_path: Path) -> None:
    feature = _feature(state="retired")
    del feature["operator_approval_ref"]
    result = gate.validate_registry(
        _registry(feature, expected_path=tmp_path / "expected.json"), base_dir=ROOT
    )
    assert not result.ok
    assert any("operator_approval_ref" in error for error in result.errors)


def test_explicit_external_retirement_approval_allows_retired_row(tmp_path: Path) -> None:
    repo, _, _, _, _ = _make_history_repo(tmp_path)
    (repo / "docs").mkdir()
    (repo / "docs" / "FAILURE_LOG.md").write_text(
        "### FL-1234: fixture operator record\n"
        "OPERATOR_APPROVED_FEATURE_RETIREMENT "
        "feature=feature.example ref=approval.fixture\n",
        encoding="utf-8",
    )
    feature = _feature(state="retired")
    result = gate.run_registry(
        _registry(feature, expected_path=tmp_path / "expected.json", base_dir=repo),
        base_dir=repo,
    )
    assert result.ok, result.to_dict()
    assert result.checks[0].status == "retired"


def test_duplicate_feature_id_fails_closed(tmp_path: Path) -> None:
    first = _feature()
    second = _feature(feature_id="feature.example", evidence_id="evidence.other")
    result = gate.validate_registry(
        {
            **_registry(
                first,
                expected_path=tmp_path / "expected.json",
                expected=["evidence.example", "evidence.other"],
            ),
            "features": [first, second],
        },
        base_dir=ROOT,
    )
    assert not result.ok
    assert any("duplicate feature id" in error for error in result.errors)


def test_nonexistent_executable_fails_closed(tmp_path: Path) -> None:
    feature = _feature(check=_command(executable="/definitely/missing/feature-check"))
    result = gate.validate_registry(
        _registry(feature, expected_path=tmp_path / "expected.json"), base_dir=ROOT
    )
    assert not result.ok
    assert any("executable not found" in error for error in result.errors)


def test_nonexistent_working_directory_fails_closed(tmp_path: Path) -> None:
    check = _command()
    check["cwd"] = "/definitely/missing/feature-cwd"
    result = gate.validate_registry(
        _registry(_feature(check=check), expected_path=tmp_path / "expected.json"), base_dir=ROOT
    )
    assert not result.ok
    assert any("directory does not exist" in error for error in result.errors)


def test_failing_command_is_reported_with_bounded_output(tmp_path: Path) -> None:
    check = _command(code="import sys; print('x' * 200); print('err', file=sys.stderr); sys.exit(7)")
    feature = _feature(check=check)
    result = gate.run_registry(
        _registry(feature, expected_path=tmp_path / "expected.json"), base_dir=ROOT
    )
    assert not result.ok
    check_result = result.checks[0]
    assert check_result.status == "failed"
    assert check_result.exit_code == 7
    assert check_result.stdout_truncated is True
    assert len(check_result.stdout.encode()) <= 64
    assert "err" in check_result.stderr


def test_identical_command_route_executes_once_and_maps_all_features(
    tmp_path: Path,
) -> None:
    counter = tmp_path / "counter.txt"
    code = (
        "from pathlib import Path; "
        f"p=Path({str(counter)!r}); "
        "p.write_text(str(int(p.read_text()) + 1) if p.exists() else '1')"
    )
    shared_check = _command(code=code)
    first = _feature(check=shared_check)
    second = _feature(
        feature_id="feature.other",
        evidence_id="evidence.other",
        check=shared_check,
    )
    registry = _registry(
        first,
        expected_path=tmp_path / "expected.json",
        expected=["evidence.example", "evidence.other"],
    )
    registry["features"] = [first, second]
    expected_document = json.loads(
        (tmp_path / "expected.json").read_text(encoding="utf-8")
    )
    expected_document["feature_ids"] = ["feature.example", "feature.other"]
    (tmp_path / "expected.json").write_text(
        json.dumps(expected_document), encoding="utf-8"
    )
    result = gate.run_registry(registry, base_dir=ROOT)
    assert result.ok, result.to_dict()
    assert counter.read_text(encoding="utf-8") == "1"
    assert [check.status for check in result.checks] == ["passed", "passed"]
    assert result.checks[0].deduplicated is False
    assert result.checks[1].deduplicated is True


def test_unknown_keys_status_and_check_kind_fail_closed(tmp_path: Path) -> None:
    feature = _feature()
    feature["unexpected"] = True
    feature["state"] = "unknown-state"
    feature["check"] = {"kind": "manual", "note": "not executable"}
    result = gate.validate_registry(
        _registry(feature, expected_path=tmp_path / "expected.json"), base_dir=ROOT
    )
    assert not result.ok
    assert any("unknown key" in error for error in result.errors)
    assert any("unknown status" in error for error in result.errors)
    assert any("unknown check kind" in error for error in result.errors)


def test_active_static_contract_is_rejected_as_source_only(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    source.write_text("STATIC_MARKER\n", encoding="utf-8")
    feature = _feature(
        behavior_class="static",
        check={"kind": "static-contract", "path": str(source), "contains": "STATIC_MARKER"},
    )
    result = gate.run_registry(
        _registry(feature, expected_path=tmp_path / "expected.json"), base_dir=ROOT
    )
    assert not result.ok
    assert any("active behavior" in error for error in result.validation.errors)


@pytest.mark.parametrize("status", ["skipped", "unknown", "unmeasured"])
def test_runtime_receipt_non_measured_status_fails_closed(
    tmp_path: Path, status: str
) -> None:
    receipt_path = tmp_path / f"{status}.json"
    feature = _feature(
        behavior_class="runtime",
        check=_runtime_check(receipt_path, payload_overrides={"status": status}),
    )
    result = gate.run_registry(
        _registry(feature, expected_path=tmp_path / f"expected-{status}.json"),
        base_dir=ROOT,
    )
    assert not result.ok, result.to_dict()
    assert "non-measured result" in (result.checks[0].error or "")


def test_cli_json_validate_and_run_exit_codes(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(_registry(expected_path=tmp_path / "expected.json")), encoding="utf-8"
    )
    validated = subprocess.run(
        [sys.executable, str(SCRIPT), "--registry", str(registry_path), "--validate", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert validated.returncode == 0, validated.stderr
    assert json.loads(validated.stdout)["ok"] is True
    ran = subprocess.run(
        [sys.executable, str(SCRIPT), str(registry_path), "--run", "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr
    assert json.loads(ran.stdout)["checks"][0]["status"] == "passed"


def test_cli_validation_failure_is_json_and_nonzero(tmp_path: Path) -> None:
    expected_path = tmp_path / "expected.json"
    registry_path = tmp_path / "invalid-registry.json"
    registry_path.write_text(
        json.dumps(_registry(expected_path=expected_path)), encoding="utf-8"
    )
    expected_path.write_text(
        json.dumps(
            {
                "version": 1,
                "evidence_ids": ["evidence.present", "evidence.omitted"],
                "operator_reference_commit": _git_head(ROOT),
                "graph_baseline_commit": _git_head(ROOT),
                "coverage_through_commit": _git_head(ROOT),
                "history_paths": [],
                "classified_commits": [],
                "retirement_approvals": [],
            }
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--validate", str(registry_path), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["ok"] is False
    assert any("expected evidence id omitted" in error for error in payload["errors"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("feature_id", "feature.wrong"),
        ("source_head", "0" * 40),
        ("route_id", "wrong-route"),
    ],
)
def test_runtime_receipt_identity_is_strict(
    tmp_path: Path, field: str, value: object
) -> None:
    receipt_path = tmp_path / f"runtime-{field}.json"
    feature = _feature(
        behavior_class="runtime",
        check=_runtime_check(receipt_path, payload_overrides={field: value}),
    )
    result = gate.run_registry(
        _registry(feature, expected_path=tmp_path / f"expected-{field}.json"),
        base_dir=ROOT,
    )
    assert not result.ok
    assert result.checks[0].error


def test_temporal_receipt_uses_two_time_separated_samples(tmp_path: Path) -> None:
    receipt_path = tmp_path / "temporal.json"
    check = _runtime_check(
        receipt_path,
        payload_overrides={
            "continuous_run_id": "run-1",
            "motion": {"delta": 1.0},
            "coherence": {"same_route": True},
            "frames": [
                {"frame_id": "frame-1", "timestamp_ns": 1, "continuous_run_id": "run-1"},
                {"frame_id": "frame-2", "timestamp_ns": 2, "continuous_run_id": "run-1"},
            ],
        },
    )
    check["required_observation_keys"] = ["observation", "motion", "coherence"]
    feature = _feature(
        behavior_class="temporal",
        check=check,
    )
    result = gate.run_registry(
        _registry(feature, expected_path=tmp_path / "expected.json"), base_dir=ROOT
    )
    assert result.ok, result.to_dict()


def test_temporal_check_requires_motion_and_coherence_obligations(tmp_path: Path) -> None:
    check = _runtime_check(
        tmp_path / "temporal-contract.json",
        payload_overrides={
            "continuous_run_id": "run-1",
            "frames": [
                {"frame_id": "frame-1", "timestamp_ns": 1, "continuous_run_id": "run-1"},
                {"frame_id": "frame-2", "timestamp_ns": 2, "continuous_run_id": "run-1"},
            ],
        },
    )
    feature = _feature(behavior_class="temporal", check=check)
    result = gate.validate_registry(
        _registry(feature, expected_path=tmp_path / "expected.json"), base_dir=ROOT
    )
    assert not result.ok
    assert any("motion and coherence" in error for error in result.errors)


def test_temporal_receipt_rejects_sample_from_different_run(tmp_path: Path) -> None:
    receipt_path = tmp_path / "temporal-mixed-run.json"
    check = _runtime_check(
        receipt_path,
        payload_overrides={
            "continuous_run_id": "run-1",
            "motion": {"delta": 1.0},
            "coherence": {"same_route": True},
            "frames": [
                {"frame_id": "frame-1", "timestamp_ns": 1, "continuous_run_id": "run-1"},
                {"frame_id": "frame-2", "timestamp_ns": 2, "continuous_run_id": "run-2"},
            ],
        },
    )
    check["required_observation_keys"] = ["observation", "motion", "coherence"]
    feature = _feature(behavior_class="temporal", check=check)
    result = gate.run_registry(
        _registry(feature, expected_path=tmp_path / "expected.json"), base_dir=ROOT
    )
    assert not result.ok
    assert "root continuous_run_id" in (result.checks[0].error or "")


def test_interactive_receipt_uses_event_sequence_and_runtime_truth(
    tmp_path: Path,
) -> None:
    receipt_path = tmp_path / "interactive.json"
    feature = _feature(
        behavior_class="interactive",
        check=_runtime_check(
            receipt_path,
            payload_overrides={
                "event_sequence": ["click"],
                "runtime_truth": {"clicked": True},
            },
        ),
    )
    result = gate.run_registry(
        _registry(feature, expected_path=tmp_path / "expected.json"), base_dir=ROOT
    )
    assert result.ok, result.to_dict()


@pytest.mark.parametrize(
    "omit_per_feature_observation", [False, True], ids=["all-observed", "missing-observed"]
)
def test_shared_runtime_route_executes_once_with_per_feature_receipts(
    tmp_path: Path, omit_per_feature_observation: bool
) -> None:
    counter = tmp_path / "counter.txt"
    receipt_path = tmp_path / "shared-runtime.json"
    identity_one = {
        "target": "fixture-target",
        "build": "fixture-build",
        "map": "fixture-map",
        "scene": "fixture-scene",
        "pose": "fixture-pose",
        "camera": "fixture-camera",
        "mode": "fixture-mode",
        "baseline": "fixture-baseline",
        "timing_window": "fixture-window",
    }
    identity_two = {**identity_one, "map": "fixture-map-two", "camera": "fixture-camera-two"}
    payload = {
        "schema": "fixture.runtime.v1",
        "source_head": _git_head(ROOT),
        "route_id": "fixture-route",
        "target_identity": identity_one,
        "behavior_observed": True,
        "features": {
            "feature.example": {
                "feature_id": "feature.example",
                "observation": "first",
                "behavior_observed": True,
                "target_identity": identity_one,
            },
            "feature.other": {
                "feature_id": "feature.other",
                "observation": "second",
                "behavior_observed": True,
                "target_identity": identity_two,
            },
        },
    }
    if omit_per_feature_observation:
        del payload["features"]["feature.other"]["behavior_observed"]
    code = (
        "import json; from pathlib import Path; "
        f"p=Path({str(counter)!r}); p.write_text(str(int(p.read_text()) + 1) if p.exists() else '1'); "
        f"Path({str(receipt_path)!r}).write_text(json.dumps({payload!r}))"
    )
    shared_check = _command(code=code)
    shared_check.update(
        {
            "receipt_path": str(receipt_path),
            "receipt_schema": "fixture.runtime.v1",
            "route_id": "fixture-route",
            "target_identity": payload["target_identity"],
            "required_observation_keys": ["observation"],
        }
    )
    first = _feature(behavior_class="runtime", check=shared_check)
    second_check = {**shared_check, "target_identity": identity_two}
    second = _feature(
        feature_id="feature.other",
        evidence_id="evidence.other",
        behavior_class="runtime",
        check=second_check,
    )
    registry = _registry(
        first,
        expected_path=tmp_path / "expected.json",
        expected=["evidence.example", "evidence.other"],
    )
    registry["features"] = [first, second]
    expected_document = json.loads(
        (tmp_path / "expected.json").read_text(encoding="utf-8")
    )
    expected_document["feature_ids"] = ["feature.example", "feature.other"]
    (tmp_path / "expected.json").write_text(
        json.dumps(expected_document), encoding="utf-8"
    )
    result = gate.run_registry(registry, base_dir=ROOT)
    if omit_per_feature_observation:
        assert not result.ok, result.to_dict()
        assert "per-feature receipt behavior_observed" in (result.checks[1].error or "")
        return
    assert result.ok, result.to_dict()
    assert counter.read_text(encoding="utf-8") == "1"
    assert [check.status for check in result.checks] == ["passed", "passed"]
    assert result.checks[1].deduplicated is True


def test_shared_runtime_route_rejects_stale_second_feature_receipt(
    tmp_path: Path,
) -> None:
    first_receipt = tmp_path / "first-runtime.json"
    second_receipt = tmp_path / "second-runtime.json"
    second_check = _runtime_check(second_receipt, feature_id="feature.other")
    second_receipt.write_text(
        json.dumps(
            {
                "schema": "fixture.runtime.v1",
                "feature_id": "feature.other",
                "source_head": _git_head(ROOT),
                "route_id": second_check["route_id"],
                "target_identity": second_check["target_identity"],
                "behavior_observed": True,
                "observation": "stale second receipt",
            }
        ),
        encoding="utf-8",
    )
    code = (
        "import json; from pathlib import Path; "
        f"Path({str(first_receipt)!r}).write_text(json.dumps({{"
        "'schema':'fixture.runtime.v1',"
        f"'feature_id':'feature.example','source_head':{_git_head(ROOT)!r},"
        "'route_id':'fixture-route',"
        f"'target_identity':{second_check['target_identity']!r},"
        "'behavior_observed':True,'observation':'fresh first receipt'"
        "}))"
    )
    first_check = _runtime_check(first_receipt)
    first_check["argv"] = [sys.executable, "-c", code]
    second_check["argv"] = list(first_check["argv"])
    first = _feature(behavior_class="runtime", check=first_check)
    second = _feature(
        feature_id="feature.other",
        evidence_id="evidence.other",
        behavior_class="runtime",
        check=second_check,
    )
    registry = _registry(
        first,
        expected_path=tmp_path / "expected.json",
        expected=["evidence.example", "evidence.other"],
    )
    registry["features"] = [first, second]
    expected_path = tmp_path / "expected.json"
    expected_document = json.loads(expected_path.read_text(encoding="utf-8"))
    expected_document["feature_ids"] = ["feature.example", "feature.other"]
    expected_path.write_text(json.dumps(expected_document), encoding="utf-8")

    result = gate.run_registry(registry, base_dir=ROOT)
    assert not result.ok, result.to_dict()
    assert result.checks[0].status == "passed"
    assert result.checks[1].deduplicated is True
    assert "not newly written" in (result.checks[1].error or "")


def test_timeout_boundary_accepts_900_second_route(tmp_path: Path) -> None:
    check = _command(code="pass")
    check["timeout_seconds"] = 900
    result = gate.validate_registry(
        _registry(_feature(check=check), expected_path=tmp_path / "expected.json"),
        base_dir=ROOT,
    )
    assert result.ok, result.errors
    check["timeout_seconds"] = 900.1
    result = gate.validate_registry(
        _registry(_feature(check=check), expected_path=tmp_path / "expected-too-long.json"),
        base_dir=ROOT,
    )
    assert not result.ok


def test_prior_feature_and_evidence_removal_fails_continuity(tmp_path: Path) -> None:
    repo, snapshot, _, registry_path = _make_continuity_fixture(tmp_path)
    candidate = _feature(
        feature_id="feature.candidate",
        evidence_id="evidence.candidate",
        behavior_class="static",
    )
    current_registry = _registry(
        candidate,
        expected_path=repo / "expected.json",
        base_dir=repo,
        operator_reference_commit=snapshot,
        graph_baseline_commit=snapshot,
        head_commit=snapshot,
        history_paths=[],
        classified_commits=[
            {
                "commit": snapshot,
                "disposition": "feature-introducing",
                "feature_ids": ["feature.old"],
                "reason": "fixture baseline census record",
                "evidence_refs": ["evidence.old"],
                "process_failure_category": None,
            }
        ],
    )
    registry_path.write_text(json.dumps(current_registry), encoding="utf-8")
    _git(
        repo,
        "commit",
        "-qam",
        "feature continuity\n\nGodot-Feature-Impact: feature.candidate",
    )
    prior_registry, prior_census = gate.load_prior_snapshot(repo, snapshot, registry_path)
    result = gate.validate_registry(
        current_registry,
        base_dir=repo,
        prior_registry=prior_registry,
        prior_census=prior_census,
    )
    assert not result.ok
    assert any("prior feature id removed" in error for error in result.errors)
    assert any("prior evidence id removed" in error for error in result.errors)


def test_retirement_approval_without_fl_reference_fails(tmp_path: Path) -> None:
    repo, _, _, _, _ = _make_history_repo(tmp_path)
    feature = _feature(state="retired")
    registry = _registry(
        feature,
        expected_path=tmp_path / "expected.json",
        base_dir=repo,
    )
    expected_path = Path(registry["census"]["expected_census_path"])
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    del expected["retirement_approvals"][0]["fl_ref"]
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    result = gate.validate_registry(registry, base_dir=repo)
    assert not result.ok
    assert any("fl_ref" in error for error in result.errors)


def test_one_row_cannot_cover_two_expected_atomic_feature_ids(tmp_path: Path) -> None:
    feature = _feature()
    feature["parent_family_id"] = "family.fixture"
    registry = _registry(
        feature,
        expected_path=tmp_path / "expected.json",
        expected=["evidence.example"],
    )
    expected_path = Path(registry["census"]["expected_census_path"])
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["feature_ids"] = ["feature.example", "feature.other"]
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    result = gate.validate_registry(registry, base_dir=ROOT)
    assert not result.ok
    assert any("expected feature id omitted" in error for error in result.errors)


def test_retirement_marker_must_be_inside_declared_fl_section(tmp_path: Path) -> None:
    repo, _, _, _, _ = _make_history_repo(tmp_path)
    (repo / "docs").mkdir()
    (repo / "docs" / "FAILURE_LOG.md").write_text(
        "### FL-1234: fixture operator record\n"
        "### FL-9999: another record\n"
        "OPERATOR_APPROVED_FEATURE_RETIREMENT "
        "feature=feature.example ref=approval.fixture\n",
        encoding="utf-8",
    )
    feature = _feature(state="retired")
    registry = _registry(
        feature,
        expected_path=tmp_path / "expected.json",
        base_dir=repo,
    )
    result = gate.validate_registry(registry, base_dir=repo)
    assert not result.ok
    assert any("in FL-1234 section" in error for error in result.errors)


def test_prior_classified_record_is_immutable(tmp_path: Path) -> None:
    repo, snapshot, classified, registry_path, _ = _make_classified_continuity_fixture(tmp_path)
    expected_path = repo / "expected.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    classified_record = next(
        record
        for record in expected["classified_commits"]
        if record["commit"] == classified
    )
    classified_record["disposition"] = "no-feature"
    classified_record["feature_ids"] = []
    classified_record["reason"] = "rewritten reason"
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    current_registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry_path.write_text(json.dumps(current_registry), encoding="utf-8")
    _git(
        repo,
        "commit",
        "-qam",
        "census revision\n\nGodot-Feature-Impact: feature.old",
    )
    prior_registry, prior_census = gate.load_prior_snapshot(repo, snapshot, registry_path)
    result = gate.validate_registry(
        current_registry,
        base_dir=repo,
        prior_registry=prior_registry,
        prior_census=prior_census,
    )
    assert not result.ok
    assert any("prior classified commit record changed" in error for error in result.errors)


def test_process_failure_classification_accepts_unscoped_record(
    tmp_path: Path,
) -> None:
    repo, baseline, relevant, _, _ = _make_history_repo(tmp_path)
    registry = _history_registry(
        repo,
        baseline,
        relevant,
        tmp_path / "expected.json",
        history_paths=[],
        classified_commits=[relevant],
    )
    expected_path = tmp_path / "expected.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["classified_commits"][0].update(
        {
            "disposition": "process-failure",
            "feature_ids": [],
            "reason": "fixture governance failure classification",
            "evidence_refs": [],
            "process_failure_category": "failure",
        }
    )
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    result = gate.validate_registry(registry, base_dir=repo, historical_audit=True)
    assert result.ok, result.to_dict()


def test_no_feature_classification_rejects_feature_ids(tmp_path: Path) -> None:
    repo, baseline, relevant, _, _ = _make_history_repo(tmp_path)
    registry = _history_registry(
        repo,
        baseline,
        relevant,
        tmp_path / "expected.json",
        history_paths=[],
        classified_commits=[relevant],
    )
    expected_path = tmp_path / "expected.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["classified_commits"][0].update(
        {
            "disposition": "no-feature",
            "feature_ids": ["feature.example"],
            "reason": "fixture commit has no feature impact",
            "evidence_refs": ["evidence.example"],
            "process_failure_category": None,
        }
    )
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    result = gate.validate_registry(registry, base_dir=repo, historical_audit=True)
    assert not result.ok
    assert any(
        "feature_ids: must be empty for 'no-feature' disposition" in error
        for error in result.errors
    )


def test_runtime_target_identity_requires_pose(tmp_path: Path) -> None:
    receipt_path = tmp_path / "missing-pose.json"
    check = _runtime_check(receipt_path)
    del check["target_identity"]["pose"]
    feature = _feature(behavior_class="runtime", check=check)
    result = gate.validate_registry(
        _registry(feature, expected_path=tmp_path / "expected.json"), base_dir=ROOT
    )
    assert not result.ok
    assert any("target_identity.pose" in error for error in result.errors)


@pytest.mark.parametrize("field", ["map", "camera", "mode"])
def test_runtime_receipt_rejects_target_identity_substitution(
    tmp_path: Path, field: str
) -> None:
    receipt_path = tmp_path / f"identity-{field}.json"
    identity = {
        "target": "fixture-target",
        "build": "fixture-build",
        "map": "fixture-map",
        "scene": "fixture-scene",
        "pose": "fixture-pose",
        "camera": "fixture-camera",
        "mode": "fixture-mode",
        "baseline": "fixture-baseline",
        "timing_window": "fixture-window",
    }
    bad_identity = {**identity, field: f"different-{field}"}
    feature = _feature(
        behavior_class="runtime",
        check=_runtime_check(
            receipt_path,
            payload_overrides={"target_identity": bad_identity},
        ),
    )
    result = gate.run_registry(
        _registry(feature, expected_path=tmp_path / f"expected-{field}.json"),
        base_dir=ROOT,
    )
    assert not result.ok
    assert "target_identity" in (result.checks[0].error or "")


def test_hash_only_classification_is_rejected(tmp_path: Path) -> None:
    registry = _registry(expected_path=tmp_path / "expected.json")
    expected_path = Path(registry["census"]["expected_census_path"])
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["classified_commits"] = [_git_head(ROOT)]
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    result = gate.validate_registry(registry, base_dir=ROOT)
    assert not result.ok
    assert any("bare commit id" in error for error in result.errors)


def test_semantic_classification_requires_all_fields(tmp_path: Path) -> None:
    registry = _registry(expected_path=tmp_path / "expected.json")
    expected_path = Path(registry["census"]["expected_census_path"])
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["classified_commits"] = [
        {"commit": _git_head(ROOT), "disposition": "feature-introducing"}
    ]
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    result = gate.validate_registry(registry, base_dir=ROOT)
    assert not result.ok
    assert any("feature_ids: required" in error for error in result.errors)
    assert any("reason: required" in error for error in result.errors)
    assert any("review_state: required" in error for error in result.errors)


def test_unresolved_semantic_record_blocks_census(tmp_path: Path) -> None:
    registry = _registry(expected_path=tmp_path / "expected.json")
    expected_path = Path(registry["census"]["expected_census_path"])
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["classified_commits"][0]["review_state"] = "unresolved"
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    result = gate.validate_registry(registry, base_dir=ROOT)
    assert not result.ok
    assert any(
        "unresolved semantic record blocks the census" in error
        for error in result.errors
    )


@pytest.mark.parametrize(
    "disposition",
    [
        "feature-introducing",
        "feature-modifying",
        "feature-restoring",
        "feature-removing",
    ],
)
def test_feature_history_dispositions_are_explicit(
    tmp_path: Path, disposition: str
) -> None:
    repo, baseline, relevant, _, _ = _make_history_repo(tmp_path)
    registry = _history_registry(
        repo,
        baseline,
        relevant,
        tmp_path / f"expected-{disposition}.json",
        history_paths=[],
        classified_commits=[relevant],
    )
    expected_path = tmp_path / f"expected-{disposition}.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["classified_commits"][-1]["disposition"] = disposition
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    result = gate.validate_registry(registry, base_dir=repo, historical_audit=True)
    assert result.ok, result.to_dict()


def test_generic_feature_disposition_cannot_hide_removal_or_retirement(
    tmp_path: Path,
) -> None:
    repo, baseline, relevant, _, _ = _make_history_repo(tmp_path)
    registry = _history_registry(
        repo,
        baseline,
        relevant,
        tmp_path / "expected.json",
        history_paths=[],
        classified_commits=[relevant],
    )
    expected_path = tmp_path / "expected.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["classified_commits"][-1]["disposition"] = "feature"
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    result = gate.validate_registry(registry, base_dir=repo, historical_audit=True)
    assert not result.ok
    assert any("unknown value 'feature'" in error for error in result.errors)


def test_semantic_classification_rejects_unknown_feature_and_reason(tmp_path: Path) -> None:
    registry = _registry(expected_path=tmp_path / "expected.json")
    expected_path = Path(registry["census"]["expected_census_path"])
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["classified_commits"] = [
        {
            "commit": _git_head(ROOT),
            "disposition": "feature-introducing",
            "feature_ids": ["feature.unknown"],
            "reason": "",
            "evidence_refs": [],
            "process_failure_category": None,
            "review_state": "resolved",
        }
    ]
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    result = gate.validate_registry(registry, base_dir=ROOT)
    assert not result.ok
    assert any("unknown feature id" in error for error in result.errors)
    assert any("reason: must be a non-empty" in error for error in result.errors)


def test_process_failure_classification_requires_controlled_category(tmp_path: Path) -> None:
    registry = _registry(expected_path=tmp_path / "expected.json")
    expected_path = Path(registry["census"]["expected_census_path"])
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["classified_commits"] = [
        {
            "commit": _git_head(ROOT),
            "disposition": "process-failure",
            "feature_ids": [],
            "reason": "fixture process record",
            "evidence_refs": [],
            "process_failure_category": None,
            "review_state": "resolved",
        }
    ]
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    result = gate.validate_registry(registry, base_dir=ROOT)
    assert not result.ok
    assert any("required for process-failure disposition" in error for error in result.errors)


def test_process_only_classification_does_not_invent_failure_category(
    tmp_path: Path,
) -> None:
    registry = _registry(expected_path=tmp_path / "expected.json")
    expected_path = Path(registry["census"]["expected_census_path"])
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["classified_commits"] = [
        {
            "commit": _git_head(ROOT),
            "disposition": "process-only",
            "feature_ids": [],
            "reason": "fixture process record",
            "evidence_refs": [],
            "process_failure_category": None,
            "review_state": "resolved",
        }
    ]
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    result = gate.validate_registry(registry, base_dir=ROOT)
    assert result.ok, result.to_dict()


@pytest.mark.parametrize("disposition", ["no-feature", "process-only", "process-failure"])
def test_non_feature_history_dispositions_reject_feature_ids(
    tmp_path: Path, disposition: str
) -> None:
    registry = _registry(expected_path=tmp_path / "expected.json")
    expected_path = Path(registry["census"]["expected_census_path"])
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["classified_commits"] = [
        {
            "commit": _git_head(ROOT),
            "disposition": disposition,
            "feature_ids": ["feature.example"],
            "reason": "fixture non-feature record",
            "evidence_refs": [],
            "process_failure_category": (
                "process" if disposition in {"process-only", "process-failure"} else None
            ),
            "review_state": "resolved",
        }
    ]
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    result = gate.validate_registry(registry, base_dir=ROOT)
    assert not result.ok
    assert any(
        f"must be empty for {disposition!r} disposition" in error
        for error in result.errors
    )


def test_active_to_retired_transition_without_approval_fails(tmp_path: Path) -> None:
    repo, snapshot, prior_feature, registry_path = _make_continuity_fixture(tmp_path)
    retired = dict(prior_feature)
    retired["state"] = "retired"
    retired["operator_approval_ref"] = "approval.fixture"
    retired["retirement_record"] = "fixture retirement text"
    current_registry = _registry(
        retired,
        expected_path=repo / "expected.json",
        expected=["evidence.old"],
        base_dir=repo,
        operator_reference_commit=snapshot,
        graph_baseline_commit=snapshot,
        head_commit=snapshot,
        history_paths=[],
        classified_commits=[
            {
                "commit": snapshot,
                "disposition": "feature-introducing",
                "feature_ids": ["feature.old"],
                "reason": "fixture baseline census record",
                "evidence_refs": ["evidence.old"],
                "process_failure_category": None,
            }
        ],
    )
    expected_path = repo / "expected.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    expected["retirement_approvals"] = []
    expected_path.write_text(json.dumps(expected), encoding="utf-8")
    _extend_continuity_census(repo, snapshot)
    registry_path.write_text(json.dumps(current_registry), encoding="utf-8")
    _git(
        repo,
        "commit",
        "-qam",
        "retire feature\n\nGodot-Feature-Retirement: feature.old approval.fixture",
    )
    prior_registry, prior_census = gate.load_prior_snapshot(repo, snapshot, registry_path)
    result = gate.validate_registry(
        current_registry,
        base_dir=repo,
        prior_registry=prior_registry,
        prior_census=prior_census,
    )
    assert not result.ok
    assert any("no independent approval record" in error for error in result.errors)


def test_approved_active_to_retired_transition_passes_with_fl_marker(
    tmp_path: Path,
) -> None:
    repo, snapshot, prior_feature, registry_path = _make_continuity_fixture(tmp_path)
    (repo / "docs").mkdir()
    (repo / "docs" / "FAILURE_LOG.md").write_text(
        "### FL-1234: fixture operator record\n"
        "OPERATOR_APPROVED_FEATURE_RETIREMENT "
        "feature=feature.old ref=approval.fixture\n",
        encoding="utf-8",
    )
    retired = dict(prior_feature)
    retired["state"] = "retired"
    retired["operator_approval_ref"] = "approval.fixture"
    retired["retirement_record"] = "fixture retirement text"
    current_registry = _registry(
        retired,
        expected_path=repo / "expected.json",
        expected=["evidence.old"],
        base_dir=repo,
        operator_reference_commit=snapshot,
        graph_baseline_commit=snapshot,
        head_commit=snapshot,
        history_paths=[],
        classified_commits=[
            {
                "commit": snapshot,
                "disposition": "feature-introducing",
                "feature_ids": ["feature.old"],
                "reason": "fixture baseline census record",
                "evidence_refs": ["evidence.old"],
                "process_failure_category": None,
            }
        ],
    )
    _extend_continuity_census(repo, snapshot)
    registry_path.write_text(json.dumps(current_registry), encoding="utf-8")
    _git(
        repo,
        "commit",
        "-qam",
        "retire feature\n\nGodot-Feature-Retirement: feature.old approval.fixture",
    )
    prior_registry, prior_census = gate.load_prior_snapshot(repo, snapshot, registry_path)
    result = gate.validate_registry(
        current_registry,
        base_dir=repo,
        prior_registry=prior_registry,
        prior_census=prior_census,
    )
    assert result.ok, result.to_dict()


def test_prior_observable_cannot_be_redefined(tmp_path: Path) -> None:
    repo, snapshot, _, registry_path = _make_continuity_fixture(tmp_path)
    current_registry = json.loads(registry_path.read_text(encoding="utf-8"))
    current_registry["features"][0]["observable"] = "a different observable claim"
    _extend_continuity_census(repo, snapshot)
    registry_path.write_text(json.dumps(current_registry), encoding="utf-8")
    _git(
        repo,
        "commit",
        "-qam",
        "observable revision\n\nGodot-Feature-Impact: feature.old",
    )
    prior_registry, prior_census = gate.load_prior_snapshot(repo, snapshot, registry_path)
    result = gate.validate_registry(
        current_registry,
        base_dir=repo,
        prior_registry=prior_registry,
        prior_census=prior_census,
    )
    assert not result.ok
    assert any("semantic field changed" in error and "observable" in error for error in result.errors)


def test_prior_runtime_behavior_class_cannot_be_downgraded_to_static(
    tmp_path: Path,
) -> None:
    prior_feature = _feature(
        feature_id="feature.old",
        evidence_id="evidence.old",
        behavior_class="runtime",
        check=_runtime_check(tmp_path / "prior-runtime.json", feature_id="feature.old"),
    )
    repo, snapshot, _, registry_path = _make_continuity_fixture(
        tmp_path, prior_feature=prior_feature
    )
    current_registry = json.loads(registry_path.read_text(encoding="utf-8"))
    current_registry["features"][0]["behavior_class"] = "static"
    _extend_continuity_census(repo, snapshot)
    registry_path.write_text(json.dumps(current_registry), encoding="utf-8")
    _git(
        repo,
        "commit",
        "-qam",
        "behavior revision\n\nGodot-Feature-Impact: feature.old",
    )
    prior_registry, prior_census = gate.load_prior_snapshot(repo, snapshot, registry_path)
    result = gate.validate_registry(
        current_registry,
        base_dir=repo,
        prior_registry=prior_registry,
        prior_census=prior_census,
    )
    assert not result.ok
    assert any(
        "semantic field changed" in error and "behavior_class" in error
        for error in result.errors
    )


# ---------------------------------------------------------------------------
# FL-4781 per-feature source fingerprints.
# ---------------------------------------------------------------------------

_FP_PRODUCT = """#[compute]
#version 450
#include "res://render/helpers.glslinc"

// Product entry.
float shade(vec2 p) {
\treturn helper(p) * SCALE;
}

float unrelated(float x) {
\treturn x + 1.0;
}
"""

_FP_HELPERS = """const float SCALE = 2.0;

float helper(vec2 p) {
\treturn p.x + p.y;
}
"""

_FP_GD = """extends Node

const LIMIT := 4

func run(x: int) -> int:
\tif x > LIMIT:
\t\treturn clamp_value(x)
\treturn x

func clamp_value(x: int) -> int:
\treturn LIMIT
"""


def _fp_registry(owners_by_id: dict[str, list[str]]) -> dict[str, object]:
    return {
        "features": [
            {"id": feature_id, "name": feature_id.lower(), "category": "fixture", "source_owners": owners}
            for feature_id, owners in owners_by_id.items()
        ]
    }


def _fp_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "fingerprint-repo"
    (repo / "godot_project" / "render").mkdir(parents=True)
    (repo / "hooks-off").mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    _git(repo, "config", "user.name", "fixture")
    _git(repo, "config", "core.hooksPath", str(repo / "hooks-off"))
    (repo / "godot_project" / "render" / "product.glsl").write_text(_FP_PRODUCT, encoding="utf-8")
    (repo / "godot_project" / "render" / "helpers.glslinc").write_text(_FP_HELPERS, encoding="utf-8")
    (repo / "godot_project" / "Thing.gd").write_text(_FP_GD, encoding="utf-8")
    (repo / "README.txt").write_text("fixture\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "baseline")
    return repo


def _fp_owner(repo: Path, owner: str, revision: str = "WORKTREE") -> dict[str, object]:
    with gate.FeatureFingerprinter(repo) as fingerprinter:
        return fingerprinter.owner(fingerprinter.reader(revision), owner)


def _fp_rewrite(repo: Path, relative: str, old: str, new: str) -> None:
    path = repo / relative
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


@pytest.mark.parametrize(
    ("language", "original", "reformatted"),
    [
        (
            "c",
            "float f(float x) {\n\treturn x * 2.0; // double\n}\n",
            "/* header */\nfloat   f( float x ){ return x*2.0;\n\n}\n",
        ),
        (
            "ts",
            "function f(a: number): number {\n  return a + 1; // one\n}\n",
            "function f(a:number):number{return a+1;}\n/* trailing */\n",
        ),
        (
            "gd",
            "func f(x):\n\tif x:\n\t\treturn 1 # one\n\treturn 2\n",
            "## doc\nfunc f( x ):\n    if x:\n        return 1\n\n    return 2   # two\n",
        ),
        (
            "py",
            "def f(x):\n    return [1,\n            2]  # pair\n",
            "# c\ndef f( x ):\n  return [ 1, 2 ]\n",
        ),
    ],
)
def test_fingerprint_normalisation_ignores_comments_and_whitespace(
    language: str, original: str, reformatted: str
) -> None:
    assert gate.normalize_source(original, language) == gate.normalize_source(reformatted, language)


@pytest.mark.parametrize(
    ("language", "original", "changed"),
    [
        ("c", "float f(float x) { return x * 2.0; }", "float f(float x) { return x * 3.0; }"),
        ("c", 'const char* s = "a // b";', 'const char* s = "a // c";'),
        ("gd", "func f(x):\n\tif x:\n\t\tg()\n\th()\n", "func f(x):\n\tif x:\n\t\tg()\n\t\th()\n"),
        ("gd", 'var s := "# not a comment"\n', 'var s := "# changed"\n'),
    ],
)
def test_fingerprint_normalisation_keeps_code_changes(language: str, original: str, changed: str) -> None:
    assert gate.normalize_source(original, language) != gate.normalize_source(changed, language)


def test_glsl_symbol_owner_expands_includes_and_follows_callees(tmp_path: Path) -> None:
    repo = _fp_repo(tmp_path)
    owner = "godot_project/render/product.glsl:shade"
    before = _fp_owner(repo, owner)
    assert before["status"] == gate.RESOLVED_STATUS
    assert before["resolution"] == "definition"
    assert set(before["closure"]) == {"helper", "SCALE"}
    assert before["files"] == [
        "godot_project/render/product.glsl",
        "godot_project/render/helpers.glslinc",
    ]

    # Comment and whitespace edits in the callee's include keep the fingerprint.
    _fp_rewrite(repo, "godot_project/render/helpers.glslinc", "\treturn p.x + p.y;", "  return p.x+p.y; // sum")
    assert _fp_owner(repo, owner)["fingerprint"] == before["fingerprint"]

    # An unrelated function in the same unit does not belong to this owner.
    _fp_rewrite(repo, "godot_project/render/product.glsl", "x + 1.0", "x + 9.0")
    assert _fp_owner(repo, owner)["fingerprint"] == before["fingerprint"]

    # A code change in a transitive callee inside an include changes it.
    _fp_rewrite(repo, "godot_project/render/helpers.glslinc", "p.x+p.y", "p.x-p.y")
    assert _fp_owner(repo, owner)["fingerprint"] != before["fingerprint"]


def test_whole_file_glsl_owner_includes_the_include_closure(tmp_path: Path) -> None:
    repo = _fp_repo(tmp_path)
    owner = "godot_project/render/product.glsl"
    before = _fp_owner(repo, owner)
    _fp_rewrite(repo, "godot_project/render/helpers.glslinc", "2.0", "3.0")
    assert _fp_owner(repo, owner)["fingerprint"] != before["fingerprint"]


def test_gdscript_symbol_owner_is_stable_and_detects_a_callee_change(tmp_path: Path) -> None:
    repo = _fp_repo(tmp_path)
    owner = "godot_project/Thing.gd:run"
    before = _fp_owner(repo, owner)
    assert before["status"] == gate.RESOLVED_STATUS
    assert set(before["closure"]) == {"LIMIT", "clamp_value"}
    _fp_rewrite(repo, "godot_project/Thing.gd", "\tif x > LIMIT:", "\t# guard\n\tif x>LIMIT:   ")
    assert _fp_owner(repo, owner)["fingerprint"] == before["fingerprint"]
    _fp_rewrite(repo, "godot_project/Thing.gd", "\treturn LIMIT\n", "\treturn LIMIT - 1\n")
    assert _fp_owner(repo, owner)["fingerprint"] != before["fingerprint"]


def test_unresolved_owners_are_reported_never_skipped(tmp_path: Path) -> None:
    repo = _fp_repo(tmp_path)
    registry = _fp_registry(
        {
            "GODOT-X01": ["godot_project/missing.gd"],
            "GODOT-X02": ["godot_project/Thing.gd:not_defined"],
            "GODOT-X03": [gate.NO_CURRENT_OWNER_PLACEHOLDER],
            "GODOT-X04": ["godot_project/Thing.gd:run", "godot_project/render/gone.glsl"],
            "GODOT-X05": ["README.txt:1-40"],
        }
    )
    snapshot = gate.fingerprint_snapshot(registry, "WORKTREE", base_dir=repo)
    features = snapshot["features"]
    assert features["GODOT-X01"]["status"] == gate.UNRESOLVED_STATUS
    assert "source file is missing" in features["GODOT-X01"]["owners"][0]["reason"]
    assert "has no definition" in features["GODOT-X02"]["owners"][0]["reason"]
    assert features["GODOT-X03"]["owners"][0]["reason"].startswith("NO_CURRENT_OWNER")
    assert features["GODOT-X04"]["status"] == "partial"
    assert "line range" in features["GODOT-X05"]["owners"][0]["reason"]
    counts = snapshot["unresolved"]
    assert counts["distinct_owners"] == 5
    assert counts["owner_references"] == 5
    assert counts["features_fully_unresolved"] == 4
    assert counts["features_partially_unresolved"] == 1


def test_fingerprint_diff_lists_changed_features_with_commits(tmp_path: Path) -> None:
    repo = _fp_repo(tmp_path)
    base = _git_head(repo)
    registry = _fp_registry(
        {
            "GODOT-X10": ["godot_project/render/product.glsl:shade"],
            "GODOT-X11": ["godot_project/render/product.glsl:unrelated"],
            "GODOT-X12": ["godot_project/Thing.gd"],
        }
    )
    _fp_rewrite(repo, "godot_project/render/helpers.glslinc", "p.x + p.y", "p.x * p.y")
    _git(repo, "commit", "-qam", "fix(FL-9999): True FF helper law")
    touching = _git_head(repo)
    _fp_rewrite(repo, "godot_project/Thing.gd", "extends Node", "extends Node # comment only")
    _git(repo, "commit", "-qam", "docs: comment only")
    diff = gate.fingerprint_diff(registry, base, "HEAD", base_dir=repo)
    changed = {feature["id"]: feature for feature in diff["features"] if feature["changed"]}
    assert set(changed) == {"GODOT-X10"}
    commits = changed["GODOT-X10"]["commits"]
    assert [touch["commit"] for touch in commits] == [touching]
    assert commits[0]["lane"] == "fix"
    assert commits[0]["label"] == "FL-9999"
    assert commits[0]["ff_lane"] is True
    assert commits[0]["owners_changed"] == ["godot_project/render/product.glsl:shade"]
    assert diff["summary"]["changed_non_ff_feature_count"] == 1


def test_fingerprint_diff_receipt_is_content_addressed(tmp_path: Path) -> None:
    repo = _fp_repo(tmp_path)
    registry = _fp_registry({"GODOT-X12": ["godot_project/Thing.gd"]})
    receipt = gate.fingerprint_diff_receipt(registry, [("HEAD", "HEAD")], base_dir=repo)
    kwargs = {
        "base_dir": repo,
        "label": "fixture",
        "working_root": tmp_path / "work",
        "destination_root": tmp_path / "durable",
    }
    first = gate.write_fingerprint_diff_receipt(receipt, **kwargs)
    second = gate.write_fingerprint_diff_receipt(receipt, **kwargs)
    assert first["preserved_manifest"] == second["preserved_manifest"]
    manifest = json.loads(Path(first["preserved_manifest"]).read_text(encoding="utf-8"))
    assert {row["name"] for row in manifest["files"]} == {"receipt.json", "summary.md"}


def test_staged_impact_refuses_an_unnamed_feature_change(tmp_path: Path) -> None:
    repo = _fp_repo(tmp_path)
    (repo / "config").mkdir()
    registry = _fp_registry(
        {
            "GODOT-X10": ["godot_project/render/product.glsl:shade"],
            "GODOT-X12": ["godot_project/Thing.gd"],
        }
    )
    (repo / "config" / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "registry")

    def errors(message: str) -> list[str]:
        return gate.staged_feature_impact_errors(
            message, base_dir=repo, registry_relative="config/registry.json"
        )

    # Comment-only change: fingerprint unchanged, no trailer required.
    _fp_rewrite(repo, "godot_project/render/helpers.glslinc", "const float", "// note\nconst float")
    _git(repo, "add", "-A")
    assert errors("docs: note") == []

    # Code change in an included callee: the feature must be named.
    _fp_rewrite(repo, "godot_project/render/helpers.glslinc", "2.0", "5.0")
    _git(repo, "add", "-A")
    refused = errors("fix: scale\n\nGodot-Feature-Impact: none\nGodot-Feature-Reason: x")
    assert len(refused) == 1 and "GODOT-X10" in refused[0] and "GODOT-X12" not in refused[0]
    assert errors("fix: scale\n\nGodot-Feature-Impact: GODOT-X10") == []

    # Unstaged worktree edits do not count; only the index is fingerprinted.
    _fp_rewrite(repo, "godot_project/Thing.gd", "LIMIT := 4", "LIMIT := 8")
    assert errors("fix: scale\n\nGodot-Feature-Impact: GODOT-X10") == []


def test_staged_impact_cli_exit_status(tmp_path: Path) -> None:
    repo = _fp_repo(tmp_path)
    (repo / "config").mkdir()
    registry = _fp_registry({"GODOT-X12": ["godot_project/Thing.gd"]})
    (repo / "config" / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "registry")
    _fp_rewrite(repo, "godot_project/Thing.gd", "LIMIT := 4", "LIMIT := 8")
    _git(repo, "add", "-A")
    message = tmp_path / "MSG"
    message.write_text("fix: limit\n", encoding="utf-8")
    command = [
        sys.executable,
        str(SCRIPT),
        "--registry",
        "config/registry.json",
        "--staged-impact",
        str(message),
    ]
    refused = subprocess.run(command, cwd=repo, capture_output=True, text=True)
    assert refused.returncode == 1
    assert "GODOT-X12" in refused.stderr
    message.write_text("fix: limit\n\nGodot-Feature-Impact: GODOT-X12\n", encoding="utf-8")
    accepted = subprocess.run(command, cwd=repo, capture_output=True, text=True)
    assert accepted.returncode == 0, accepted.stderr
