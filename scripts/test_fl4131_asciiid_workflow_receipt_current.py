#!/usr/bin/env python3
"""Validate the FL-4131 headed ASCIIID workflow receipt against current source."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RECEIPT = (
    REPO_ROOT
    / "docs/research/ascii/verification/fl4131/phase_d/2026-05-30"
    / "phase_d_asciiid_cdp_preset_save_reopen.json"
)
MANIFEST = REPO_ROOT / "assets/glyphs/fixtures/extended_glyph_material_additive_v1.json"
GENERATED_PRESETS = REPO_ROOT / "assets/glyphs/generated/material_shape_presets.json"


def _head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def _source_head_for_commit(commit: str) -> str:
    receipt_only_prefixes = (
        "docs/research/ascii/verification/fl4131/phase_d/2026-05-28/",
        "docs/research/ascii/verification/fl4131/phase_d/2026-05-30/",
        "scripts/test_fl4131_asciiid_workflow_receipt_current.py",
        "scripts/test_fl4131_multiplayer_receipts_current.py",
    )
    for _ in range(16):
        changed = subprocess.check_output(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", commit],
            cwd=REPO_ROOT,
            text=True,
        ).splitlines()
        subject = subprocess.check_output(
            ["git", "show", "-s", "--format=%s", commit],
            cwd=REPO_ROOT,
            text=True,
        ).strip()
        receipt_only = all(
            path.startswith(receipt_only_prefixes)
            or (path == "docs/FAILURE_LOG.md" and "FL-4131" in subject)
            for path in changed
        )
        if not changed or not receipt_only:
            return commit
        commit = subprocess.check_output(["git", "rev-parse", f"{commit}^"], cwd=REPO_ROOT, text=True).strip()
    raise RuntimeError("could not resolve receipt source commit within 16 parents")


def _receipt_source_head() -> str:
    return _source_head_for_commit(_head())


def _manifest_entry_count() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return len(manifest["entries"])


def _generated_preset_count() -> int:
    generated = json.loads(GENERATED_PRESETS.read_text(encoding="utf-8"))
    return len(generated["presets"])


def main() -> int:
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    head = _receipt_source_head()
    errors: list[str] = []
    if receipt.get("schema") != "fl4131_asciiid_cdp_preset_save_reopen.v4":
        errors.append(f"unexpected schema: {receipt.get('schema')!r}")
    if receipt.get("verdict") != "PASS":
        errors.append(f"receipt verdict is not PASS: {receipt.get('verdict')!r}")
    receipt_head = receipt.get("commit_under_test")
    if not isinstance(receipt_head, str):
        errors.append(f"receipt commit_under_test is not a string: {receipt_head!r}")
    elif _source_head_for_commit(receipt_head) != head:
        errors.append(
            f"receipt commit_under_test {receipt_head} does not match source HEAD {head}"
        )
    generated_preset_count = _generated_preset_count()
    if receipt.get("listed_presets") != generated_preset_count or receipt.get("applied_preset_count") != generated_preset_count:
        errors.append(f"receipt does not apply all {generated_preset_count} generated shape presets")
    if receipt.get("all_listed_presets_admitted") is not True:
        errors.append("receipt has disabled/unadmitted listed presets")
    if receipt.get("all_preview_resolves_passed") is not True:
        errors.append("receipt preview resolve assertion is not true")
    if receipt.get("all_tooltips_show_glyph_fallback_pairs") is not True:
        errors.append("receipt tooltip assertion is not true")
    if receipt.get("sidecar_cells") != 64 or receipt.get("reopened_matches_saved") is not True:
        errors.append("receipt save/reopen sidecar assertion failed")
    screenshot = receipt.get("screenshot")
    if not isinstance(screenshot, str) or not screenshot:
        errors.append("receipt does not include a rendered screenshot artifact")
    elif not (REPO_ROOT / screenshot).is_file():
        errors.append(f"receipt screenshot artifact missing: {screenshot}")

    manifest_count = _manifest_entry_count()
    if manifest_count != 135:
        errors.append(f"current material manifest entry count is {manifest_count}, expected 135")

    applied = receipt.get("applied_presets")
    if not isinstance(applied, list):
        errors.append("receipt applied_presets missing")
    elif len(applied) != generated_preset_count:
        errors.append(f"receipt applied_presets length is {len(applied)}, expected {generated_preset_count}")
    elif any(p.get("extended_cells") != 64 for p in applied):
        errors.append("one or more presets did not fill 64 extended cells")

    if errors:
        for err in errors:
            print(f"FAIL: {err}")
        return 1
    print("PASS: FL-4131 headed ASCIIID workflow receipt matches current source")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
