#!/usr/bin/env python3
"""Validate FL-4131 web/multiplayer receipts against current HEAD."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RECEIPT_DIR = (
    REPO_ROOT
    / "docs/research/ascii/verification/fl4131/phase_d/2026-05-30"
)
RECEIPTS = {
    "manifest_mismatch": (
        RECEIPT_DIR / "phase_d_multiplayer_manifest_mismatch_local_two_tab.json",
        "fl4131_multiplayer_manifest_mismatch_local_two_tab.v1",
    ),
    "fallback_agreement": (
        RECEIPT_DIR / "phase_d_multiplayer_fallback_agreement_local_two_tab.json",
        "fl4131_multiplayer_fallback_agreement_local_two_tab.v1",
    ),
    "admitted_extended": (
        RECEIPT_DIR / "phase_d_multiplayer_admitted_extended_local_two_tab.json",
        "fl4131_multiplayer_admitted_extended_local_two_tab.v1",
    ),
}


def _head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def _receipt_source_head() -> str:
    receipt_only_prefixes = (
        "docs/research/ascii/verification/fl4131/phase_d/2026-05-28/",
        "docs/research/ascii/verification/fl4131/phase_d/2026-05-30/",
        "docs/FAILURE_LOG.md",
        "scripts/test_fl4131_multiplayer_receipts_current.py",
    )
    commit = _head()
    for _ in range(16):
        changed = subprocess.check_output(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit],
            cwd=REPO_ROOT,
            text=True,
        ).splitlines()
        if not changed or not all(path.startswith(receipt_only_prefixes) for path in changed):
            return commit
        commit = subprocess.check_output(["git", "rev-parse", f"{commit}^"], cwd=REPO_ROOT, text=True).strip()
    raise RuntimeError("could not resolve receipt source commit within 16 parents")


def main() -> int:
    head = _receipt_source_head()
    errors: list[str] = []
    for label, (path, schema) in RECEIPTS.items():
        if not path.exists():
            errors.append(f"{label}: missing {path.relative_to(REPO_ROOT)}")
            continue
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if receipt.get("schema") != schema:
            errors.append(f"{label}: unexpected schema {receipt.get('schema')!r}")
        if receipt.get("verdict") != "PASS":
            errors.append(f"{label}: verdict is not PASS: {receipt.get('verdict')!r}")
        if receipt.get("commit") != head:
            errors.append(f"{label}: receipt commit {receipt.get('commit')} does not match HEAD {head}")
        if receipt.get("mode") != "local_two_tab":
            errors.append(f"{label}: mode is not local_two_tab")
        proof_points = receipt.get("proof_points")
        if not isinstance(proof_points, dict):
            errors.append(f"{label}: proof_points missing")
            continue
        if label == "manifest_mismatch":
            if proof_points.get("scenario_two_tab_join_handshake_reject_observed") is not True:
                errors.append(f"{label}: join mismatch rejection was not observed")
            if proof_points.get("evidence_recorder_captured_reject_reason_code") is not True:
                errors.append(f"{label}: recorder did not capture glyph_manifest_mismatch")
            if proof_points.get("accepted_tab_joined_without_reject") is not True:
                errors.append(f"{label}: accepted tab did not join cleanly")
        elif label == "fallback_agreement":
            if proof_points.get("evidence_recorder_captured_extended_fallback_render_event") is not True:
                errors.append(f"{label}: recorder did not capture extended fallback render event")
            if proof_points.get("evidence_browser_captured_extended_fallback_render_event") is not True:
                errors.append(f"{label}: browser did not capture extended fallback render event")
            if proof_points.get("gameplay_fallback_render_agreement_between_tabs") is not True:
                errors.append(f"{label}: two tabs did not agree on fallback rendering")
        elif label == "admitted_extended":
            if proof_points.get("evidence_browser_captured_admitted_extended_render_event") is not True:
                errors.append(f"{label}: browser did not capture admitted extended render event")
            if proof_points.get("evidence_manifest_bound_for_admitted_content") is not True:
                errors.append(f"{label}: admitted content did not bind a manifest")
            if proof_points.get("gameplay_admitted_extended_render_agreement_between_tabs") is not True:
                errors.append(f"{label}: two tabs did not agree on admitted extended rendering")

    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        return 1
    print("PASS: FL-4131 local web/multiplayer receipts match current HEAD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
