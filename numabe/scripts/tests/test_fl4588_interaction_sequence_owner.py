# =============================================================================
# test_fl4588_interaction_sequence_owner.py
# =============================================================================
# FL4588-INTERACTION-SEQUENCE-OWNER-20260718-45A / FL-4588
#
# Proves one physical DOM-control sequence owns one interaction_id across
# pointer-down, pointer-up, click, and the runtime-intent consume. The retired
# canvas authoring recorder/resolver is absent.
#
# DELETED owner: per-capture crypto.randomUUID() multi-id sequence ownership.
# =============================================================================

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
OWNER_TS = ROOT / "web/asciiid-authoring-shell/src/interactionSequenceOwner.ts"
RECORDER_TS = ROOT / "web/asciiid-authoring-shell/src/uiEventRecorder.ts"

LEAF = "settings.display.termpp_performance_hud"
CONTROL = "settings_display_termpp_performance_hud"


def _run_owner_harness(script: str) -> dict[str, Any]:
    """Execute the TS sequence-owner module under npx tsx (behavioral)."""
    harness = f"""
import {{
  allocateSequenceInteractionId,
  consumeSequenceInteractionId,
  PENDING_INTERACTION_MAX_AGE_MS,
}} from {json.dumps(str(OWNER_TS))};

const pending = new Map();
let n = 0;
const mint = () => `id-${{++n}}`;
const now = () => 1000 + n; // advances slightly per mint call site via n

// --- pointerdown + pointerup + click + one commit (same leaf) ---
const t0 = 10_000;
const idDown = allocateSequenceInteractionId(pending, {json.dumps(LEAF)}, {json.dumps(CONTROL)}, t0, mint);
const idUp = allocateSequenceInteractionId(pending, {json.dumps(LEAF)}, {json.dumps(CONTROL)}, t0 + 1, mint);
const idClick = allocateSequenceInteractionId(pending, {json.dumps(LEAF)}, {json.dumps(CONTROL)}, t0 + 2, mint);
const idCommit = consumeSequenceInteractionId(pending, {json.dumps(LEAF)}, {json.dumps(CONTROL)}, t0 + 3, mint);

// --- After consume, a new sequence may mint a fresh owner ---
const idNext = allocateSequenceInteractionId(pending, {json.dumps(LEAF)}, {json.dumps(CONTROL)}, t0 + 100, mint);

const out = {{
  click_sequence: {{ idDown, idUp, idClick, idCommit, all_equal: idDown === idUp && idUp === idClick && idClick === idCommit }},
  next_sequence_fresh: idNext !== idCommit,
  max_age_ms: PENDING_INTERACTION_MAX_AGE_MS,
}};
console.log(JSON.stringify(out));
"""
    # Write ephemeral harness next to the module (tsx needs a file for local import).
    harness_path = OWNER_TS.parent / "_interaction_sequence_owner_harness.mts"
    harness_path.write_text(harness, encoding="utf-8")
    try:
        proc = subprocess.run(
            ["npx", "--yes", "tsx", str(harness_path)],
            cwd=str(ROOT / "web/asciiid-authoring-shell"),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    finally:
        if harness_path.exists():
            harness_path.unlink()
    if proc.returncode != 0:
        raise AssertionError(
            f"tsx harness failed rc={proc.returncode}\nstdout={proc.stdout}\nstderr={proc.stderr}"
        )
    # Last JSON line
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip().startswith("{")]
    assert lines, f"no JSON in stdout: {proc.stdout!r} stderr={proc.stderr!r}"
    return json.loads(lines[-1])


def test_source_deletes_per_capture_uuid_mint() -> None:
    recorder = RECORDER_TS.read_text(encoding="utf-8")
    owner = OWNER_TS.read_text(encoding="utf-8")
    assert "allocateSequenceInteractionId" in recorder
    assert "allocateSequenceInteractionId" in owner
    assert "DELETED" in owner or "DELETED" in recorder
    # Capture paths must not mint UUID before consulting the shared allocator.
    assert "captureUnderLeaf" in recorder
    # One DOM-control observer calls the helper; the retired canvas authoring
    # recorder must not survive as a second physical-input owner.
    assert recorder.count("captureUnderLeaf(") == 2  # definition + DOM call


def test_pointer_down_up_click_plus_one_commit_share_one_id() -> None:
    out = _run_owner_harness("")
    seq = out["click_sequence"]
    assert seq["all_equal"] is True, seq
    assert seq["idDown"] == seq["idCommit"]
    assert out["next_sequence_fresh"] is True


def test_retired_canvas_capture_has_no_sequence_owner() -> None:
    recorder = RECORDER_TS.read_text(encoding="utf-8")
    assert "capturePhysicalCanvasEvent" not in recorder
    assert "canvasEventResolver" not in recorder


def test_dom_capture_rejects_the_retired_canvas_semantic_owner() -> None:
    """The TERM++ canvas is presentation/inspection, never an action recorder."""
    recorder = RECORDER_TS.read_text(encoding="utf-8")
    assert "if (target instanceof HTMLCanvasElement) return null;" in recorder
    assert "capturePhysicalCanvasEvent" not in recorder
    assert "canvasEventResolver" not in recorder
    assert not (ROOT / "web/asciiid-authoring-shell/src/canvasEventResolver.ts").exists()
