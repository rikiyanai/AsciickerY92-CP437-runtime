"""Honest stage labels stay distinguishable from unsupported completion claims."""
from __future__ import annotations

from scripts.maintainer.claim_guard import check_message
from scripts.maintainer import claim_guard


def test_downgraded_stage_label_does_not_require_commit() -> None:
    report = check_message(
        "Highest directly evidenced stage: Implemented; visual proof is missing.",
        mode="block",
    )
    assert report.findings == ()


def test_open_executed_stage_with_runtime_gap_is_not_completion_claim() -> None:
    report = check_message(
        "Stage: Executed; runtime acceptance remains open and unverified.",
        mode="block",
    )
    assert report.findings == ()


def test_unqualified_completion_claim_still_requires_evidence() -> None:
    report = check_message("Implemented and fixed the issue.", mode="block")
    assert any(f.category == "unsupported_claim" for f in report.findings)


def test_stage_exemption_is_occurrence_local() -> None:
    report = check_message("Stage: Implemented; proof missing. All other work Implemented.", mode="block")
    assert any(f.category == "unsupported_claim" for f in report.findings)


def test_real_commit_reference_does_not_launder_acceptance(monkeypatch) -> None:
    monkeypatch.setattr(claim_guard, "_has_valid_commit_ref", lambda message: True)
    report = check_message("Everything fixed and accepted. Source identity: 0530ef962819fe62b161121b43085f63387c723f.", mode="block")
    assert any(f.category == "unsupported_claim" for f in report.findings)
