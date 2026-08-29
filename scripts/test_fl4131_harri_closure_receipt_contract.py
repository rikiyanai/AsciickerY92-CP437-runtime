#!/usr/bin/env python3
"""FL-4131 Harri closure receipt contract.

This is the closure-grade gate. Source contracts and builds may pass while the
lane is still visibly wrong; this test reads the headed proof receipts and
fails until the current HEAD has a passing, operator-accepted Harri run with
runtime counters matching the GPU bridge and sprite dispatch proved.
"""

from pathlib import Path
import json
import subprocess


ROOT = Path(__file__).resolve().parents[1]
HARRI_RECEIPT = ROOT / "docs/research/ascii/verification/fl4131/termpp_harri_visual/termpp_harri_visual_receipt.json"
CP437_RECEIPT = ROOT / "docs/research/ascii/verification/fl4131/termpp_harri_visual_cp437/termpp_harri_visual_receipt.json"


def current_head() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def load(path: Path) -> dict:
    if not path.exists():
        raise AssertionError(f"missing receipt: {path.relative_to(ROOT)}")
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition: bool, label: str, detail: str) -> None:
    if not condition:
        raise AssertionError(f"{label}: {detail}")


def main() -> int:
    head = current_head()
    harri = load(HARRI_RECEIPT)
    cp437 = load(CP437_RECEIPT)
    failures = []

    def check(condition: bool, label: str, detail: str) -> None:
        if not condition:
            failures.append(f"{label}: {detail}")

    check(harri.get("commit_under_test") == head,
          "harri receipt stale",
          f"commit_under_test={harri.get('commit_under_test')} head={head}")
    check(cp437.get("commit_under_test") == head,
          "cp437 receipt stale",
          f"commit_under_test={cp437.get('commit_under_test')} head={head}")
    check(harri.get("verdict") == "PASS",
          "harri headed verdict not PASS",
          f"verdict={harri.get('verdict')}")
    check(cp437.get("verdict") == "PASS",
          "cp437 identity verdict not PASS",
          f"verdict={cp437.get('verdict')}")

    hb = harri.get("pass_breakdown") if isinstance(harri.get("pass_breakdown"), dict) else {}
    cb = cp437.get("pass_breakdown") if isinstance(cp437.get("pass_breakdown"), dict) else {}
    for key in (
        "operator_visual_inspection_ack",
        "runtime_shape6_hook_ok",
        "runtime_shape6_gpu_arabic_gt_0",
        "runtime_shape6_gpu_kana_gt_0",
        "runtime_shape6_gpu_distinct_extended_ge_4",
    ):
        check(hb.get(key) is True, f"harri gate {key} not true", f"value={hb.get(key)}")
    check(cb.get("operator_visual_inspection_ack") is True,
          "cp437 operator inspection ack not true",
          f"value={cb.get('operator_visual_inspection_ack')}")

    runtime = harri.get("runtime_hook_response") if isinstance(harri.get("runtime_hook_response"), dict) else {}
    bridge = harri.get("gpu_bridge_response") if isinstance(harri.get("gpu_bridge_response"), dict) else {}
    check((runtime.get("sprite_actor_calls") or 0) > 0,
          "sprite actor dispatch not proved",
          f"sprite_actor_calls={runtime.get('sprite_actor_calls')}")
    check((runtime.get("gpu_extended_applied") or 0) >= (bridge.get("extended_winners") or 1),
          "runtime GPU extended counter not fed by bridge winners",
          f"runtime={runtime.get('gpu_extended_applied')} bridge={bridge.get('extended_winners')}")
    check((runtime.get("gpu_arabic") or 0) >= (bridge.get("arabic_winners") or 1),
          "runtime GPU Arabic counter not fed by bridge winners",
          f"runtime={runtime.get('gpu_arabic')} bridge={bridge.get('arabic_winners')}")
    check((runtime.get("gpu_kana") or 0) >= (bridge.get("kana_winners") or 1),
          "runtime GPU Kana counter not fed by bridge winners",
          f"runtime={runtime.get('gpu_kana')} bridge={bridge.get('kana_winners')}")
    check((runtime.get("gpu_distinct_extended") or 0) >= (bridge.get("distinct_extended") or 4),
          "runtime GPU distinct counter not fed by bridge winners",
          f"runtime={runtime.get('gpu_distinct_extended')} bridge={bridge.get('distinct_extended')}")

    captures = harri.get("captures") if isinstance(harri.get("captures"), list) else []
    required_names = {
        "shape_lab_skull_front",
        "shape_lab_skull_side",
        "shape_lab_skull_diag",
        "shape_lab_sphere_front",
        "shape_lab_sphere_diag",
        "shape_lab_pyramid_front",
        "shape_lab_pyramid_diag",
    }
    present_names = {c.get("name") for c in captures if isinstance(c, dict) and c.get("written") is True}
    check(required_names.issubset(present_names),
          "seven required mesh captures not freshly written",
          f"missing={sorted(required_names - present_names)}")
    check(bool(harri.get("operator_visual_inspection")),
          "operator visual inspection record missing",
          "skull/pyramid framing cannot be accepted by source grep")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("PASS: FL-4131 headed Harri closure receipt is current and accepted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
