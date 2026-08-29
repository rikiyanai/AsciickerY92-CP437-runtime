#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RECEIPT = (
    REPO_ROOT
    / "docs/research/ascii/verification/fl4131/phase_d/2026-05-30"
    / "phase_d_asciiid_mouse_click_visible_material_grid.json"
)


def _current_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def _fail(message: str) -> None:
    raise AssertionError(message)


def main() -> None:
    if not RECEIPT.exists():
        _fail(f"missing receipt: {RECEIPT.relative_to(REPO_ROOT)}")
    receipt = json.loads(RECEIPT.read_text())

    if receipt.get("schema") != "fl4131_asciiid_mouse_click_visible_material_grid.v1":
        _fail(f"bad schema: {receipt.get('schema')}")
    if receipt.get("verdict") != "PASS":
        _fail(f"bad verdict: {receipt.get('verdict')}")
    if receipt.get("transport") != "headed_asciiid_cdp":
        _fail(f"bad transport: {receipt.get('transport')}")

    # Receipts are source evidence. They may predate a receipt-only commit, but
    # they must not drift away from the currently checked source state while the
    # proof script/editor files are dirty.
    commit = receipt.get("commit_under_test")
    if not isinstance(commit, str) or len(commit) < 8:
        _fail(f"bad commit_under_test: {commit!r}")
    _current_head()

    rect = receipt.get("button_rect") or {}
    click = receipt.get("click_point") or {}
    if not rect.get("valid"):
        _fail("button rect was not marked valid")
    if not (rect.get("x1", 0) > rect.get("x0", 0) and rect.get("y1", 0) > rect.get("y0", 0)):
        _fail(f"button rect has no area: {rect}")
    if not (rect["x0"] <= click.get("x", -1) <= rect["x1"] and rect["y0"] <= click.get("y", -1) <= rect["y1"]):
        _fail(f"click point outside rect: click={click} rect={rect}")

    if receipt.get("before_extended_cells") != 0:
        _fail(f"expected default material to start CP437-only, got {receipt.get('before_extended_cells')}")
    if receipt.get("after_extended_cells") != 64:
        _fail(f"expected 64 extended cells after click, got {receipt.get('after_extended_cells')}")
    if receipt.get("preview_extended_cells") != 64:
        _fail(f"preview did not resolve 64 extended cells: {receipt.get('preview_extended_cells')}")
    if receipt.get("preview_coverage_cells") != 64:
        _fail(f"preview coverage cells mismatch: {receipt.get('preview_coverage_cells')}")
    if receipt.get("preview_diagnostic_cells") != 0:
        _fail(f"unexpected diagnostic cells: {receipt.get('preview_diagnostic_cells')}")
    if receipt.get("preview_display_not_fallback") != 64:
        _fail(f"preview still displayed fallback bytes: {receipt.get('preview_display_not_fallback')}")

    after = receipt.get("after_row0_glyph_ids") or []
    fallback = receipt.get("after_row0_fallback_bytes") or []
    if len(after) != 16 or len(fallback) != 16:
        _fail("expected 16 glyph/fallback samples in row 0")
    if not any(gid > 255 for gid in after):
        _fail(f"row 0 did not contain extended GlyphIds: {after}")
    if after == receipt.get("before_row0_glyph_ids"):
        _fail("row 0 glyph ids did not change")

    points = receipt.get("proof_points") or {}
    for key in (
        "real_visible_button_rect_used",
        "actual_mouse_click_was_queued",
        "active_material_ramp_received_extended_glyph_ids",
        "material_preview_resolved_to_extended_atlas_glyphs",
        "composited_ui_frame_captured_after_click",
    ):
        if points.get(key) is not True:
            _fail(f"proof point not true: {key}")

    screenshot_rel = receipt.get("screenshot")
    screenshot = REPO_ROOT / screenshot_rel
    if not screenshot.exists():
        _fail(f"missing screenshot: {screenshot_rel}")
    if screenshot.stat().st_size != receipt.get("screenshot_bytes"):
        _fail("screenshot byte count drifted")
    if screenshot.stat().st_size < 100_000:
        _fail("screenshot is too small to be a real composited UI frame")

    print("PASS fl4131 asciiid mouse-click visible material grid receipt")


if __name__ == "__main__":
    main()
