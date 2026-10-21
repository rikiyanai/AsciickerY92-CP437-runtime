#!/usr/bin/env python3
"""Regression tests for honest failure-log compact-state classification.

The failure log stores attempts separately from authored status. These tests
protect that ownership boundary across both command-line front doors:
``analyze_runs.py`` and ``analyze_failure_log.py``. Historical attempt prose
must never promote an OPEN entry into an implementation claim.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest


# Resolve imports exactly as the command-line entry points do. The scripts
# directory contains their sibling helper modules, while the repository root
# gives the test a stable location regardless of the caller's working folder.
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _load_script_module(module_name: str, relative_path: str):
    """Load one CLI script as a module so its public classifier can be tested.

    Args:
        module_name: A unique import name used only inside this test process.
        relative_path: Repository-relative path to the Python entry point.

    Returns:
        The imported module object.

    Why this exists:
        Both front doors are executable scripts rather than an installed Python
        package. Loading them this way exercises their real wrapper functions
        while keeping the test independent from shell output formatting.
    """
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ANALYZE_RUNS = _load_script_module(
    "analyze_runs_fl_ledger_state_truth",
    "scripts/analyze_runs.py",
)
ANALYZE_FAILURE_LOG = _load_script_module(
    "analyze_failure_log_fl_ledger_state_truth",
    "scripts/analyze_failure_log.py",
)


def _entry(proof_state: str, *, status: str = "OPEN") -> dict:
    """Build a synthetic FL entry containing a historical fix-attempt block.

    Args:
        proof_state: Effective overlay proof state to classify.
        status: Canonical failure-log status, defaulting to OPEN.

    Returns:
        A parser-shaped dictionary accepted by both analyzer wrappers.

    Why this exists:
        The regression depends on attempt text being present. A compact fixture
        makes that fact explicit without coupling the test to a changing live FL
        entry such as FL-4512.
    """
    return {
        "fl": "FL-999999",
        "title": "Synthetic classifier regression",
        "status": status,
        "effective_status": status,
        "category": "failure_log/tooling",
        "overlay": {"ProofState": proof_state},
        "_lines": [
            "#### Fix attempt (2026-07-26, branch `main`)",
            "- **Approach:** Historical attempt only; no implementation claim.",
        ],
    }


@pytest.mark.parametrize("proof_state", ["", "OPEN", "UNPROVEN", "RAW_OPEN"])
def test_fix_attempt_never_promotes_open_entry(proof_state: str) -> None:
    """OPEN entries stay actionable even when their attempt ledger is non-empty."""
    entry = _entry(proof_state)
    assert ANALYZE_RUNS.fl_ledger_state(entry) == "raw-open-actionable"
    assert (
        ANALYZE_FAILURE_LOG._RunsCompat.fl_ledger_state(entry)
        == "raw-open-actionable"
    )


@pytest.mark.parametrize("proof_state", ["IMPLEMENTED", "PARTIAL"])
def test_explicit_implementation_proof_state_remains_distinct(
    proof_state: str,
) -> None:
    """Only an authored implementation proof state may use the compact token."""
    entry = _entry(proof_state)
    assert ANALYZE_RUNS.fl_ledger_state(entry) == "implemented-unproven"
    assert (
        ANALYZE_FAILURE_LOG._RunsCompat.fl_ledger_state(entry)
        == "implemented-unproven"
    )


def test_fl_card_does_not_render_terminal_status_as_proof(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An accounted defect can retain an explicitly unproven evidence state."""
    entry = _entry("IMPLEMENTED/UNPROVEN", status="ACCOUNTED")
    occurrence = {
        "entry": entry,
        "proof_state": "IMPLEMENTED/UNPROVEN",
        "ledger_state": "resolved-accounted",
        "gates": ["synthetic_cross_surface_gate"],
        "fix_attempts": [],
    }
    monkeypatch.setattr(
        ANALYZE_RUNS,
        "_fl_duplicate_occurrences",
        lambda _root, _fl_id: [occurrence],
    )
    monkeypatch.setattr(
        ANALYZE_RUNS,
        "_entry_family",
        lambda _root, _entry: [_entry],
    )

    ANALYZE_RUNS.cmd_fl_card(
        SimpleNamespace(fl_id="FL-999999", json=False),
        tmp_path,
    )
    output = capsys.readouterr().out

    assert "FL-999999  ACCOUNTED" in output
    assert "Proof state: IMPLEMENTED/UNPROVEN" in output
    assert "Ledger state: RESOLVED" in output
    assert "Proof state: RESOLVED" not in output


def test_fix_attempt_writer_default_does_not_fabricate_implementation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The append front door must not stamp every attempt as implemented."""
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    log_path = tmp_path / "docs" / "FAILURE_LOG.md"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "### FL-999999 — Synthetic writer regression\n\n"
        "**Status:** OPEN\n\n"
        "**Description:** Test entry.\n",
        encoding="utf-8",
    )
    args = SimpleNamespace(
        fl_id="FL-999999",
        description="Diagnosis only; no source modified.",
        session_id="fixture-session",
        branch="main",
        json=False,
        touched_file=[],
        code_ref=[],
        no_canon_spec_sync=True,
        no_lane_doc_refresh=True,
        auto_inline_comments=False,
        no_inline_comment_suggestions=True,
        no_auto_overlay_refs=True,
        stage=False,
    )

    ANALYZE_RUNS.cmd_fl_fix_attempt(args, tmp_path)
    _ = capsys.readouterr()
    text = log_path.read_text(encoding="utf-8")
    assert "- **Proof state:** attempt logged; status unchanged." in text
    assert "- **Proof state:** implemented, unproven." not in text


def test_fix_attempt_writer_preserves_explicit_evidence_state(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Callers can still record a stronger state when they actually earned it."""
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    log_path = tmp_path / "docs" / "FAILURE_LOG.md"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "### FL-999998 — Synthetic explicit-state regression\n\n"
        "**Status:** OPEN\n\n"
        "**Description:** Test entry.\n",
        encoding="utf-8",
    )
    args = SimpleNamespace(
        fl_id="FL-999998",
        description="Behavioral patch landed with focused tests.",
        session_id="fixture-session",
        proof_state="implemented, unproven",
        branch="main",
        json=False,
        touched_file=[],
        code_ref=[],
        no_canon_spec_sync=True,
        no_lane_doc_refresh=True,
        auto_inline_comments=False,
        no_inline_comment_suggestions=True,
        no_auto_overlay_refs=True,
        stage=False,
    )

    ANALYZE_RUNS.cmd_fl_fix_attempt(args, tmp_path)
    _ = capsys.readouterr()
    text = log_path.read_text(encoding="utf-8")
    assert "- **Proof state:** implemented, unproven." in text


def test_fix_attempt_mirror_uses_supplied_evidence_state(tmp_path: Path) -> None:
    """The navigation mirror must not reintroduce the old hardcoded state."""
    path = ANALYZE_RUNS._append_canon_spec_fix_attempt_mirror(
        tmp_path,
        "FL-999997",
        "Paper prose gate changed.",
        "main",
        "2026-07-31",
        "attempt logged; status unchanged",
    )
    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert "| 2026-07-31 | FL-999997 | `main` | attempt logged; status unchanged |" in text
    assert "implemented, unproven | Paper prose gate changed." not in text


def _writer_log(tmp_path: Path, fl_id: str = "FL-999996") -> Path:
    """Create the smallest native FL entry accepted by the writer."""
    log_path = tmp_path / "docs" / "FAILURE_LOG.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"### {fl_id} — Synthetic session writer regression\n\n"
        "**Status:** OPEN\n\n"
        "**Description:** Test entry.\n",
        encoding="utf-8",
    )
    return log_path


def _writer_args(
    fl_id: str,
    description: str,
    *,
    session_id: str | None,
    json_mode: bool = True,
) -> SimpleNamespace:
    """Build parser-shaped arguments for the actual FL writer function."""
    return SimpleNamespace(
        fl_id=fl_id,
        description=description,
        session_id=session_id,
        branch="main",
        json=json_mode,
        touched_file=[],
        code_ref=[],
        no_canon_spec_sync=True,
        no_lane_doc_refresh=True,
        auto_inline_comments=False,
        no_inline_comment_suggestions=True,
        no_auto_overlay_refs=True,
        stage=False,
    )


def test_fix_attempt_explicit_session_id_is_normalized_and_returned(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit identity is emitted once and included in JSON output."""
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    log_path = _writer_log(tmp_path)
    args = _writer_args(
        "FL-999996",
        "session_id=explicit-session Diagnosis only.",
        session_id="explicit-session",
    )

    ANALYZE_RUNS.cmd_fl_fix_attempt(args, tmp_path)

    payload = json.loads(capsys.readouterr().out)
    text = log_path.read_text(encoding="utf-8")
    assert payload["session_id"] == "explicit-session"
    assert text.count("session_id=explicit-session") == 1
    assert "- **Approach:** session_id=explicit-session Diagnosis only." in text


def test_fix_attempt_description_session_id_is_used_without_duplication(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A description identity is accepted when no explicit or env identity exists."""
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    log_path = _writer_log(tmp_path)
    args = _writer_args(
        "FL-999996",
        "Diagnosis only session_id=description-session",
        session_id=None,
    )

    ANALYZE_RUNS.cmd_fl_fix_attempt(args, tmp_path)

    payload = json.loads(capsys.readouterr().out)
    text = log_path.read_text(encoding="utf-8")
    assert payload["session_id"] == "description-session"
    assert text.count("session_id=description-session") == 1


def test_fix_attempt_uses_codex_thread_id_fallback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The environment identity is used when callers provide no other source."""
    monkeypatch.setenv("CODEX_THREAD_ID", "env-session")
    log_path = _writer_log(tmp_path)
    args = _writer_args("FL-999996", "Diagnosis only.", session_id=None)

    ANALYZE_RUNS.cmd_fl_fix_attempt(args, tmp_path)

    payload = json.loads(capsys.readouterr().out)
    text = log_path.read_text(encoding="utf-8")
    assert payload["session_id"] == "env-session"
    assert text.count("session_id=env-session") == 1


def test_fix_attempt_rejects_conflicting_identity_before_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Conflicting explicit and description identities fail closed."""
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    log_path = _writer_log(tmp_path)
    before = log_path.read_text(encoding="utf-8")
    args = _writer_args(
        "FL-999996",
        "session_id=description-session Diagnosis only.",
        session_id="explicit-session",
    )

    with pytest.raises(SystemExit) as exc_info:
        ANALYZE_RUNS.cmd_fl_fix_attempt(args, tmp_path)

    assert exc_info.value.code == 2
    assert log_path.read_text(encoding="utf-8") == before
    assert "conflicting session identities" in capsys.readouterr().err


def test_fix_attempt_missing_session_id_fails_before_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing identity cannot append an anonymous FL attempt."""
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    log_path = _writer_log(tmp_path)
    before = log_path.read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        ANALYZE_RUNS.cmd_fl_fix_attempt(
            _writer_args("FL-999996", "Diagnosis only.", session_id=None),
            tmp_path,
        )

    assert exc_info.value.code == 2
    assert log_path.read_text(encoding="utf-8") == before
    assert "missing session identity" in capsys.readouterr().err


def test_fix_attempt_rejects_malformed_description_session_id(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty description assignment cannot fall through to an env identity."""
    monkeypatch.setenv("CODEX_THREAD_ID", "env-session")
    log_path = _writer_log(tmp_path)
    before = log_path.read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        ANALYZE_RUNS.cmd_fl_fix_attempt(
            _writer_args("FL-999996", "Diagnosis only session_id=", session_id=None),
            tmp_path,
        )

    assert exc_info.value.code == 2
    assert log_path.read_text(encoding="utf-8") == before
    assert "invalid description session id" in capsys.readouterr().err


def test_fix_attempt_rejects_escaped_newline_session_marker(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A literal escape cannot hide a second session assignment from validation."""
    monkeypatch.setenv("CODEX_THREAD_ID", "env-session")
    log_path = _writer_log(tmp_path)
    before = log_path.read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        ANALYZE_RUNS.cmd_fl_fix_attempt(
            _writer_args(
                "FL-999996",
                r"foo\nsession_id=hidden-session\nrole=gpt-5.6-luna",
                session_id=None,
            ),
            tmp_path,
        )

    assert exc_info.value.code == 2
    assert log_path.read_text(encoding="utf-8") == before
    assert "invalid description session id marker" in capsys.readouterr().err


@pytest.mark.parametrize("session_id", ["has whitespace", "line\nbreak", "nul\x00byte"])
def test_fix_attempt_rejects_invalid_session_id_before_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    session_id: str,
) -> None:
    """Whitespace, line breaks, and NUL bytes are not session identities."""
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    log_path = _writer_log(tmp_path)
    before = log_path.read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        ANALYZE_RUNS.cmd_fl_fix_attempt(
            _writer_args("FL-999996", "Diagnosis only.", session_id=session_id),
            tmp_path,
        )

    assert exc_info.value.code == 2
    assert log_path.read_text(encoding="utf-8") == before
    assert "invalid --session-id session id" in capsys.readouterr().err
