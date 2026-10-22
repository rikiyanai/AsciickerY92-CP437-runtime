#!/usr/bin/env python3
"""FL-4896: strict True FF publishes the PixelCamera capture contract.

Before this, only the non-FF branch recorded it, so every FF capture logged
"PixelCamera capture image normalization failed: missing PixelCameraContext",
the final render dump was skipped and exact-pose pins never ticked.
FL-4907: the True FF frame is driven by TrueFFCompositorEffect, which records
the contract through the shared world-source interface before presenting.
Source-level check only; no Godot process starts.

Run: python3 -m pytest scripts/tests/test_fl4896_true_ff_capture_contract.py -q
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EFFECT = ROOT / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd"
DRIVER = ROOT / "godot_project" / "render" / "ascii" / "composition" / "TrueFFCompositorEffect.gd"


def _driver() -> str:
    return DRIVER.read_text(encoding="utf-8")


def test_true_ff_driver_records_the_capture_contract_before_presenting() -> None:
    src = _driver()
    record = src.index('world.call("record_capture_contract", ctx)')
    present = src.index("_renderer.render_frame(", record)
    assert record < present
    effect = EFFECT.read_text(encoding="utf-8")
    start = effect.index("func record_capture_contract(")
    body = effect[start:effect.index("\nfunc ", start + 10)]
    assert "_record_pixel_camera_capture_contract(ctx)" in body


def test_true_ff_context_carries_the_actor_presentation_contract() -> None:
    src = _driver()
    start = src.index("func _scene_context(")
    body = src[start:src.index("\nstatic func ", start + 10)]
    for field in ("actor_presentation_reference_world",
                  "actor_presentation_world_per_presentation_pixel",
                  "actor_presentation_camera_contract_valid"):
        assert f"ctx.{field} = " in body, field
