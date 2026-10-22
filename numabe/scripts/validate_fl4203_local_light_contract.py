#!/usr/bin/env python3
"""Fail-closed FL-4203 local-light ownership contract gate.

This rejects live legacy shadow owners before local-light A/B captures can be
interpreted, then requires a current headed metrics artifact. Static deletion
alone is not proof that the replacement local-light path is rendering shadows.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AB_ROOT = ROOT / ".run" / "baseline_compositor_ab"


def read_rel(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def assert_absent(path: str, pattern: str, label: str) -> None:
    text = read_rel(path)
    if re.search(pattern, text, re.MULTILINE):
        fail(f"{label} still present in {path}")


def assert_present(path: str, pattern: str, label: str) -> None:
    text = read_rel(path)
    if not re.search(pattern, text, re.MULTILINE):
        fail(f"{label} missing from {path}")


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing local-light proof artifact: {path}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path}: {exc}")


def _ab_summaries_newest_first() -> list[Path]:
    summaries = sorted(
        AB_ROOT.glob("*/summary.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not summaries:
        fail(f"missing headed A/B summary under {AB_ROOT}")
    return summaries


def _candidate_metrics_paths(summary: dict) -> list[Path]:
    paths: list[Path] = []
    for run in summary.get("runs", []):
        if not isinstance(run, dict):
            continue
        candidate = run.get("candidate", {})
        if not isinstance(candidate, dict):
            continue
        for arg in candidate.get("cmd", []):
            if not isinstance(arg, str):
                continue
            prefix = "--local-light-metrics-json="
            if arg.startswith(prefix):
                paths.append((ROOT / arg[len(prefix):]).resolve())
    return paths


def assert_runtime_proof() -> None:
    failures: list[str] = []
    checked = 0
    for summary_path in _ab_summaries_newest_first():
        summary = _load_json(summary_path)
        if summary.get("ok") is not True:
            failures.append(f"{summary_path}: headed A/B proof is not green")
            continue
        metrics_paths = _candidate_metrics_paths(summary)
        if not metrics_paths:
            failures.append(f"{summary_path}: no candidate local-light metrics path")
            continue
        for metrics_path in metrics_paths:
            checked += 1
            if not metrics_path.exists():
                failures.append(f"{metrics_path}: missing local-light proof artifact")
                continue
            try:
                metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                failures.append(f"{metrics_path}: invalid JSON: {exc}")
                continue
            local = metrics.get("local_light", {})
            if not isinstance(local, dict):
                failures.append(f"{metrics_path}: missing local_light object")
                continue
            checks = {
                "enabled": local.get("enabled") is True,
                "record_count": int(local.get("record_count", 0) or 0) > 0,
                "glyph_record_count": int(local.get("glyph_record_count", 0) or 0) > 0,
                "receiver_samples": int(local.get("receiver_samples", 0) or 0) > 0,
                "shadow_samples": int(local.get("shadow_samples", 0) or 0) > 0,
                "depth_normal_sample_count": int(local.get("depth_normal_sample_count", 0) or 0) > 0,
                "ascii_only_final_output": int(local.get("scene_light_pixels", 0) or 0) == 0,
            }
            missing = [name for name, ok in checks.items() if not ok]
            if not missing:
                return
            failures.append(
                f"{metrics_path}: local-light proof fields missing/zero: "
                + ", ".join(missing)
            )
    if failures:
        fail(f"no headed local-light proof found after checking {checked} metrics file(s):\n" + "\n".join(failures))


def main() -> int:
    shader = "godot_project/render/ascii/raster/ascii_compositor.glsl"
    product_shader = "godot_project/render/product/cell_owner_product.glsl"
    compositor = "godot_project/render/api/ASCIICompositorEffect.gd"
    world = "godot_project/scripts/World.gd"

    assert_absent(shader, r"\bactor_shadow_for_cell\s*\(", "actor shadow shader owner")
    assert_absent(shader, r"\b_actor_shadow_", "actor shadow helper owner")
    assert_absent(shader, r"\bbaseline_feature_contact_shadow", "contact shadow feature bit")
    assert_absent(shader, r"\bbaseline_plus_contact_shadow\s*\(", "contact shadow shader owner")
    if Path("godot_project/shaders/texel_splat_full_terrain_ray.glsl").exists():
        fail("texel_splat_full_terrain_ray.glsl old owner exists")

    assert_absent(compositor, r"\bbaseline_feature_contact_shadow\b", "contact shadow runtime gate")
    assert_absent(compositor, r'"contact_shadow"', "contact shadow command parser entry")
    assert_absent(world, r'"contact_shadow"', "manual contact shadow feature entry")

    assert_absent(shader, r"\bLocalLightControl\b", "final compositor local-light control owner")
    assert_absent(shader, r"\bLocalLightRecord\b", "final compositor local-light record owner")
    assert_absent(shader, r"\bLocalLightGlyphRecord\b", "final compositor local-light glyph owner")
    assert_present(product_shader, r"binding\s*=\s*11[^\n]+LocalLightControl", "product LocalLightControl binding 11")
    assert_present(product_shader, r"binding\s*=\s*12[^\n]+LocalLightRecord", "product LocalLightRecord binding 12")
    assert_present(product_shader, r"binding\s*=\s*13[^\n]+LocalLightGlyphRecord", "product LocalLightGlyphRecord binding 13")
    assert_present(compositor, r"u_ll_ctrl\.binding\s*=\s*11", "product LocalLightControl CPU binding 11")
    assert_present(compositor, r"u_ll_record\.binding\s*=\s*12", "product LocalLightRecord CPU binding 12")
    assert_present(compositor, r"u_ll_glyph\.binding\s*=\s*13", "product LocalLightGlyphRecord CPU binding 13")
    assert_runtime_proof()

    print("FL4203_LOCAL_LIGHT_CONTRACT_AND_RUNTIME_PROOF_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
