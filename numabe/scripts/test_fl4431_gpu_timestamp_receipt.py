#!/usr/bin/env python3
# ============================================================================
# test_fl4431_gpu_timestamp_receipt.py
# ============================================================================
# Fixture-backed contract tests for the FL-4431 GPU-timestamp receipt builder,
# bound to the DIRECT-MEASUREMENT owner (avg_gpu_window_ms + per-pass GPU ms from
# RenderingDevice microsecond timestamps) and the committed harness provenance
# (runtime-source fingerprint). Fixtures mirror the real arm schema
# (draw_call_band + samples[*].perf.draw_calls) and the real trace format:
# the trace internal size is the PRESENTATION size (3600x2252), while the receipt
# target is 3600x2260. No headed Godot run is needed.
#
# Run:  python3 scripts/test_fl4431_gpu_timestamp_receipt.py
# Exit: 0 = all pass, 1 = any failure.
# ============================================================================

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fl4431_gpu_timestamp_receipt as gt  # noqa: E402

_failures: list[str] = []

POSE = [78.0999984741211, 17.9365615844727, 144.399993896484]
POSE_STR = "78.0999984741211,17.9365615844727,144.399993896484"
HEAD = "94fd80629c0c34a49ca9e7d193fb8ae3aa11f705"
FP = "c72c891dec7b28e899236b33ad96ce5698925fa4b85af564c6d635ed94c4ec8f"

ENABLED = "PERF_GPU_TRACE enabled=1 (per-dispatch GPU timestamps, %-of-frame, ~1/sec)"
UNSUPPORTED = (
    "PERF_GPU_TRACE_UNSUPPORTED enabled=1 backend=metal "
    "reason=godot_4_6_native_metal_timestamp_queries_zero_by_design"
)
# Trace internal = presentation 3600x2252. Durations sum to avg_gpu_window_ms=12.000.
GOOD_GPU = (
    "PERF_GPU_TRACE internal=3600x2252 (7.75Mpx) gpu_samples=30 avg_gpu_window_ms=12.000 "
    "gpu_passes_seen=6 | per-pass % of traced GPU window: 10_splat=8.3%, "
    "12_terrain_resolve=41.7%, 20_cell_product=25.0%, 30_compositor=20.8%, "
    "40_present=3.3%, 99_end=0.8% | per-pass GPU ms: 10_splat=1.000ms, "
    "12_terrain_resolve=5.000ms, 20_cell_product=3.000ms, 30_compositor=2.500ms, "
    "40_present=0.400ms, 99_end=0.100ms"
)
GOOD_FRAME = (
    "PERF_FRAME_TRACE frames=30 internal=3600x2252 avg_wall_ms=20.000 views=1 "
    "10_splat_ms=1.6 12_terrain_resolve_ms=8.0 20_cell_product_ms=5.0 "
    "30_compositor_ms=5.3 40_present_ms=0.1"
)
GOOD_LOG = "\n".join(["boot", ENABLED, GOOD_GPU, GOOD_FRAME])


def check(ok: bool, name: str, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}" + (f"  {detail}" if detail and not ok else ""))
    if not ok:
        _failures.append(name)


def _mode0(**over):
    m = {
        "projection_mode": "perspective",
        "sample_count": 240,
        "draw_call_band": [29, 29],
        "samples": [{"perf": {"draw_calls": 29}} for _ in range(16)],
        "validity": {"ready": True, "settled_origin": list(POSE)},
    }
    m.update(over)
    return m


def _make_run(tmp, *, arm_over=None, mode_over=None, log_body=None, summary_over=None, with_summary=True):
    arm = {
        "ok": True,
        "git_head": HEAD,
        "git_dirty": True,
        "dimension_valid": True,
        "observed_dimensions": {
            "presentation_size": [[3600, 2252]],
            "target_size": [[3600, 2260]],
            "semantic_ansi_size": [[180, 113]],
        },
        "log": "arm.log",
        "mode_results": [_mode0(**(mode_over or {}))],
    }
    if arm_over:
        arm.update(arm_over)
    arm_p = tmp / "arm.json"
    arm_p.write_text(json.dumps(arm), encoding="utf-8")
    (tmp / "arm.log").write_text((log_body if log_body is not None else GOOD_LOG) + "\n", encoding="utf-8")
    summary_p = None
    if with_summary:
        summary = {
            "ok": True, "aborted": False, "abort_reason": "",
            "head_drift_observed": False,
            "runtime_source_stable": True, "scoped_clean": True,
            "baseline_head": HEAD, "final_head": HEAD,
            "baseline_runtime_source_sha256": FP, "final_runtime_source_sha256": FP,
        }
        if summary_over:
            summary.update(summary_over)
        summary_p = tmp / "summary.json"
        summary_p.write_text(json.dumps(summary), encoding="utf-8")
    return str(arm_p), (str(summary_p) if summary_p else None)


def _build(tmp, *, summary_path_override="__made__", **kw):
    mk = {k: kw.pop(k) for k in list(kw) if k in
          ("arm_over", "mode_over", "log_body", "summary_over", "with_summary")}
    arm, summ = _make_run(tmp, **mk)
    if summary_path_override != "__made__":
        summ = summary_path_override
    return gt.build_receipt(arm, summary_path=summ, **kw)


# --- positive + direct-duration invariant -----------------------------------
def test_valid_direct_measurement():
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), expect_pose=POSE_STR, expect_presentation="3600x2252",
                   expect_target="3600x2260", expect_ansi="180x113")
    check(r["valid"] and r["reason"] == "ok", "valid_direct_ok", str(r["reason"]))
    check(r["schema"] == "fl4431_gpu_timestamp_receipt.v2", "schema_v2")
    check(r["avg_gpu_window_ms"] == 12.0, "avg_gpu_window_ms_direct")
    # per-pass GPU ms is the DIRECT timestamp value, NOT share/100 * cpu_wall.
    check(r["per_pass_gpu_ms"]["12_terrain_resolve"] == 5.0, "per_pass_gpu_ms_direct")
    check(abs(0.417 * 20.0 - 8.34) < 1e-6 and r["per_pass_gpu_ms"]["12_terrain_resolve"] != 8.34,
          "gpu_ms_not_share_times_cpu_wall")
    check(r["frame_avg_wall_ms_cpu"] == 20.0, "cpu_wall_separate")
    check(r["internal_size"] == [3600, 2252] or r["internal_size"] == (3600, 2252),
          "trace_internal_is_presentation")
    check(r["observed"]["draw_call_band"] == [29, 29], "observed_draw_call_band")


def test_valid_2252_trace_with_2260_target():
    # Explicit: trace internal = presentation 3600x2252 while the receipt target is
    # 3600x2260. This must be VALID (the correction fixture).
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), expect_presentation="3600x2252", expect_target="3600x2260")
    check(r["valid"], "valid_2252_trace_2260_target", str(r["reason"]))


# --- correction: trace must match PRESENTATION, not target ------------------
def test_trace_vs_presentation_mismatch():
    # Trace internal 3600x2260 (== target) but presentation is 3600x2252 -> reject.
    gpu = GOOD_GPU.replace("internal=3600x2252", "internal=3600x2260")
    frame = GOOD_FRAME.replace("internal=3600x2252", "internal=3600x2260")
    log = "\n".join(["boot", ENABLED, gpu, frame])
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), log_body=log)
    check(not r["valid"] and "trace_dimension_mismatch_vs_presentation" in r["reason"],
          "trace_vs_presentation_mismatch", r["reason"])


# --- item 5: direct-duration negatives --------------------------------------
def test_missing_duration_block():
    gpu = (GOOD_GPU.split(" | per-pass GPU ms:")[0])  # drop the duration block
    log = "\n".join(["boot", ENABLED, gpu, GOOD_FRAME])
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), log_body=log)
    check(not r["valid"] and "duration_block_missing" in r["reason"], "missing_duration_block", r["reason"])


def test_share_duration_key_mismatch():
    # Drop 99_end from the duration map only.
    gpu = GOOD_GPU.replace(", 99_end=0.100ms", "")
    log = "\n".join(["boot", ENABLED, gpu, GOOD_FRAME])
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), log_body=log)
    check(not r["valid"] and "share_duration_key_mismatch" in r["reason"],
          "share_duration_key_mismatch", r["reason"])


def test_gpu_samples_floor():
    gpu = GOOD_GPU.replace("gpu_samples=30", "gpu_samples=10")
    log = "\n".join(["boot", ENABLED, gpu, GOOD_FRAME])
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), log_body=log)
    check(not r["valid"] and "gpu_samples_below_floor" in r["reason"], "gpu_samples_floor", r["reason"])


def test_invalid_window_ms():
    gpu = GOOD_GPU.replace("avg_gpu_window_ms=12.000", "avg_gpu_window_ms=0.000")
    log = "\n".join(["boot", ENABLED, gpu, GOOD_FRAME])
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), log_body=log)
    check(not r["valid"] and "avg_gpu_window_ms_nonpositive" in r["reason"], "invalid_window_ms", r["reason"])


def test_duration_sum_mismatch():
    # Bump 12_terrain_resolve duration to 8.000 so durations sum to 15 != window 12.
    gpu = GOOD_GPU.replace("12_terrain_resolve=5.000ms", "12_terrain_resolve=8.000ms")
    log = "\n".join(["boot", ENABLED, gpu, GOOD_FRAME])
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), log_body=log)
    check(not r["valid"] and "duration_sum_ne_window" in r["reason"], "duration_sum_mismatch", r["reason"])


# --- item 4: provenance (fingerprint owner) ---------------------------------
def test_summary_required():
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), with_summary=False, summary_path_override=None)
    check(not r["valid"] and "summary_required" in r["reason"], "summary_required", r["reason"])


def test_admitted_unrelated_head_drift():
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), summary_over={"head_drift_observed": True, "final_head": "b" * 40})
    check(r["valid"], "admitted_unrelated_head_drift", str(r["reason"]))


def test_invalid_fingerprint_drift():
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), summary_over={"final_runtime_source_sha256": "d" * 64})
    check(not r["valid"] and "runtime_fingerprint_drift" in r["reason"],
          "invalid_fingerprint_drift", r["reason"])


def test_head_mismatch_without_drift():
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), summary_over={"head_drift_observed": False, "final_head": "e" * 40})
    check(not r["valid"] and "head_mismatch_without_drift" in r["reason"],
          "head_mismatch_without_drift", r["reason"])


def test_summary_not_ok_or_aborted():
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), summary_over={"aborted": True})
    check(not r["valid"] and "summary_not_ok_or_aborted" in r["reason"],
          "summary_not_ok_or_aborted", r["reason"])


def test_summary_unstable_and_dirty():
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), summary_over={"runtime_source_stable": False, "scoped_clean": False})
    ok = (not r["valid"] and "runtime_source_not_stable" in r["reason"]
          and "scoped_source_not_clean" in r["reason"])
    check(ok, "summary_unstable_and_dirty", r["reason"])


def test_requested_summary_missing():
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), with_summary=False, summary_path_override=str(Path(td) / "nope.json"))
    check(not r["valid"] and "requested_summary_missing" in r["reason"], "requested_summary_missing", r["reason"])


# --- GPU content --------------------------------------------------------------
def test_marker_absent():
    log = "\n".join(["boot", GOOD_GPU, GOOD_FRAME])
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), log_body=log)
    check(not r["valid"] and "gpu_trace_marker_absent" in r["reason"], "marker_absent", r["reason"])


def test_native_metal_unsupported_marker():
    log = "\n".join(["boot", UNSUPPORTED, GOOD_FRAME])
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), log_body=log)
    expected = "gpu_timestamp_surface_unsupported:metal:godot_4_6_native_metal_timestamp_queries_zero_by_design"
    check(not r["valid"] and expected in r["reason"], "native_metal_unsupported", r["reason"])
    check(r["timestamp_supported"] is False, "native_metal_timestamp_supported_false")
    check(r["timestamp_unsupported"] == {
        "ok": True,
        "backend": "metal",
        "unsupported_reason": "godot_4_6_native_metal_timestamp_queries_zero_by_design",
    }, "native_metal_unsupported_fields", str(r["timestamp_unsupported"]))


def test_malformed_unsupported_marker():
    log = "\n".join(["boot", "PERF_GPU_TRACE_UNSUPPORTED enabled=1 backend=metal", GOOD_FRAME])
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), log_body=log)
    check(not r["valid"] and "gpu_timestamp_unsupported_marker_malformed" in r["reason"],
          "malformed_unsupported_marker", r["reason"])


def test_core_pass_missing():
    gpu = GOOD_GPU.replace("30_compositor=20.8%", "31_other=20.8%").replace("30_compositor=2.500ms", "31_other=2.500ms")
    log = "\n".join(["boot", ENABLED, gpu, GOOD_FRAME])
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), log_body=log)
    check(not r["valid"] and "core_pass_names_missing" in r["reason"], "core_pass_missing", r["reason"])


def test_passes_seen_ne_count():
    gpu = GOOD_GPU.replace("gpu_passes_seen=6", "gpu_passes_seen=9")
    log = "\n".join(["boot", ENABLED, gpu, GOOD_FRAME])
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), log_body=log)
    check(not r["valid"] and "gpu_passes_seen_ne_map_counts" in r["reason"], "passes_seen_ne_count", r["reason"])


def test_share_total_out_of_band():
    # Shares halved (sum ~50) but durations unchanged (sum 12 == window).
    gpu = (
        "PERF_GPU_TRACE internal=3600x2252 (7.75Mpx) gpu_samples=30 avg_gpu_window_ms=12.000 "
        "gpu_passes_seen=6 | per-pass % of traced GPU window: 10_splat=4.15%, "
        "12_terrain_resolve=20.85%, 20_cell_product=12.5%, 30_compositor=10.4%, "
        "40_present=1.65%, 99_end=0.4% | per-pass GPU ms: 10_splat=1.000ms, "
        "12_terrain_resolve=5.000ms, 20_cell_product=3.000ms, 30_compositor=2.500ms, "
        "40_present=0.400ms, 99_end=0.100ms"
    )
    log = "\n".join(["boot", ENABLED, gpu, GOOD_FRAME])
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), log_body=log)
    check(not r["valid"] and "gpu_share_total_out_of_band" in r["reason"], "share_total_out_of_band", r["reason"])


# --- ordered trailing window --------------------------------------------------
def test_trailing_unmatched_gpu_no_scavenge():
    log = "\n".join(["boot", ENABLED, GOOD_GPU, GOOD_FRAME, GOOD_GPU])
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), log_body=log)
    check(not r["valid"] and "trailing_unmatched_gpu_line" in r["reason"],
          "trailing_unmatched_no_scavenge", r["reason"])


def test_malformed_final_frame():
    log = "\n".join(["boot", ENABLED, GOOD_GPU, "PERF_FRAME_TRACE frames=30 internal=3600x2252"])
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), log_body=log)
    check(not r["valid"] and "malformed_final_frame_line" in r["reason"], "malformed_final_frame", r["reason"])


def test_gpu_frame_dim_mismatch():
    frame = GOOD_FRAME.replace("internal=3600x2252", "internal=1280x800")
    log = "\n".join(["boot", ENABLED, GOOD_GPU, frame])
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), log_body=log)
    check(not r["valid"] and "trace_dimension_mismatch_gpu_vs_frame" in r["reason"],
          "gpu_frame_dim_mismatch", r["reason"])


# --- draw-call + core arm gates ---------------------------------------------
def test_draw_call_band_invalid():
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), mode_over={"draw_call_band": []})
    check(not r["valid"] and "draw_call_band_invalid" in r["reason"], "draw_call_band_invalid", r["reason"])


def test_draw_call_sample_outside_band():
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), mode_over={"draw_call_band": [29, 29], "samples": [{"perf": {"draw_calls": 40}}] * 16})
    check(not r["valid"] and "draw_call_samples_outside_band" in r["reason"],
          "draw_call_outside_band", r["reason"])


def test_missing_arm_receipt():
    with tempfile.TemporaryDirectory() as td:
        r = gt.build_receipt(str(Path(td) / "nope.json"))
    check(not r["valid"] and r["reason"] == "arm_receipt_missing", "missing_arm_receipt", r["reason"])


def test_dimension_mismatch_target():
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), expect_target="3840x2160")
    check(not r["valid"] and "dimension_mismatch:target" in r["reason"], "dimension_mismatch_target", r["reason"])


def test_pose_mismatch():
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), expect_pose="1.0,2.0,3.0")
    check(not r["valid"] and "pose_mismatch" in r["reason"], "pose_mismatch", r["reason"])


def test_sample_count_floor():
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), mode_over={"sample_count": 10})
    check(not r["valid"] and "sample_count_below_floor" in r["reason"], "sample_count_floor", r["reason"])


def test_arm_not_ok():
    with tempfile.TemporaryDirectory() as td:
        r = _build(Path(td), arm_over={"ok": False})
    check(not r["valid"] and "arm_not_ok" in r["reason"], "arm_not_ok", r["reason"])


def main() -> int:
    print("FL-4431 GPU-timestamp receipt v2 — direct-measurement contracts")
    for fn in (
        test_valid_direct_measurement, test_valid_2252_trace_with_2260_target,
        test_trace_vs_presentation_mismatch,
        test_missing_duration_block, test_share_duration_key_mismatch,
        test_gpu_samples_floor, test_invalid_window_ms, test_duration_sum_mismatch,
        test_summary_required, test_admitted_unrelated_head_drift,
        test_invalid_fingerprint_drift, test_head_mismatch_without_drift,
        test_summary_not_ok_or_aborted, test_summary_unstable_and_dirty,
        test_requested_summary_missing,
        test_marker_absent, test_native_metal_unsupported_marker,
        test_malformed_unsupported_marker, test_core_pass_missing, test_passes_seen_ne_count,
        test_share_total_out_of_band,
        test_trailing_unmatched_gpu_no_scavenge, test_malformed_final_frame,
        test_gpu_frame_dim_mismatch,
        test_draw_call_band_invalid, test_draw_call_sample_outside_band,
        test_missing_arm_receipt, test_dimension_mismatch_target, test_pose_mismatch,
        test_sample_count_floor, test_arm_not_ok,
    ):
        fn()
    print()
    print(f"  {len(_failures)} failure(s)")
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
