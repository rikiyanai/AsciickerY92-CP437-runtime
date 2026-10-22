"""Provenance liveness: repository-identity git calls are bounded and explicit.

A wedged `git status` once held runner preflight past 100 seconds with zero
output. Every git call on the proof path now carries a finite ceiling, and a
timeout or nonzero surfaces as a named provenance failure that fails the
preflight gate instead of reading as a clean tree.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import run_fl4359_headed_movement as runner


CEILING_SLACK_SECONDS = 10


def test_helper_timeout_has_ceiling(monkeypatch) -> None:
    monkeypatch.setattr(runner, "_REPO_PROVENANCE_TIMEOUT_SECONDS", 1)
    started = time.monotonic()
    rc, out, outcome = runner._bounded_git_run(["sleep", "30"], ROOT)
    elapsed = time.monotonic() - started
    assert rc == 124
    assert out == b""
    assert outcome == "timeout"
    assert elapsed < CEILING_SLACK_SECONDS


def test_helper_success_keeps_stdout() -> None:
    rc, out, outcome = runner._bounded_git_run(["git", "rev-parse", "HEAD"], ROOT)
    assert rc == 0
    assert outcome == "ok"
    assert re.fullmatch(r"[0-9a-f]{40}\n", out.decode("utf-8")) is not None


def test_helper_nonzero_is_failed() -> None:
    rc, out, outcome = runner._bounded_git_run(
        ["git", "rev-parse", "--verify", "definitely-not-a-ref-12345"], ROOT
    )
    assert rc != 0
    assert out == b""
    assert outcome == "failed"


def test_repo_provenance_timeout_mapping(monkeypatch) -> None:
    def hung(argv, cwd):
        return 124, b"", "timeout"

    monkeypatch.setattr(runner, "_bounded_git_run", hung)
    provenance = runner._repo_provenance(preflight_only=True)
    assert provenance["provenance_status"] == "git_head_timeout"
    assert provenance["git_head_rc"] == 124
    assert provenance["git_status_rc"] == 124
    assert provenance["git_head"] == ""
    assert provenance["git_dirty"] is False
    assert provenance["git_dirty_paths"] == []


def test_repo_provenance_quiet_fields_unchanged() -> None:
    provenance = runner._repo_provenance(preflight_only=True)
    assert provenance["provenance_status"] == "ok"
    assert provenance["git_head_outcome"] == "ok"
    assert provenance["git_status_outcome"] == "ok"
    assert provenance["git_head_rc"] == 0
    assert provenance["git_status_rc"] == 0
    assert re.fullmatch(r"[0-9a-f]{40}", provenance["git_head"]) is not None
    # rev-parse --short output verbatim (lane FF-DPR-20260920-25): a prefix of
    # the full head whose width git chooses, never a fixed 12-character cut.
    assert provenance["git_head_short"]
    assert provenance["git_head"].startswith(provenance["git_head_short"])
    assert isinstance(provenance["git_dirty"], bool)
    assert isinstance(provenance["git_dirty_paths"], list)
    assert provenance["worktree_diff_status"] == "not_requested_preflight"


def test_gate_fails_missing_provenance() -> None:
    preflight = {"ok": True, "failed_checks": []}
    gated = runner._apply_provenance_preflight_gate(
        preflight, {"provenance_status": "git_status_timeout"}
    )
    assert gated["ok"] is False
    assert "provenance_available" in gated["failed_checks"]
    assert gated["provenance_status"] == "git_status_timeout"


def test_gate_passes_ok_provenance() -> None:
    preflight = {"ok": True, "failed_checks": []}
    gated = runner._apply_provenance_preflight_gate(
        preflight, {"provenance_status": "ok"}
    )
    assert gated["ok"] is True
    assert gated["failed_checks"] == []
    assert gated["provenance_status"] == "ok"


def test_identity_timeout_reason(monkeypatch) -> None:
    def hung(argv, cwd):
        return 124, b"", "timeout"

    monkeypatch.setattr(runner, "_bounded_git_run", hung)
    identity = runner._runtime_source_identity([Path(runner.__file__)])
    assert identity["valid"] is False
    assert "timeout" in identity["reason"]
