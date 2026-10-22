#!/usr/bin/env python3
"""Source contract for the FL-4431 RenderingDevice timestamp owner."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"


def main() -> int:
    text = SOURCE.read_text(encoding="utf-8")
    checks = {
        "native_metal_query_path_deleted": (
            '_pt_backend = RenderingServer.get_current_rendering_driver_name().to_lower()' in text
            and '_pt_gpu_supported = _pt_backend != "metal"' in text
            and 'PERF_GPU_TRACE_UNSUPPORTED enabled=1 backend=metal' in text
            and 'if _pt_on and _pt_gpu_supported and rd != null:' in text
        ),
        "cpu_trace_survives_metal_falsifier": (
            "func _cpu_trace_begin() -> Dictionary:" in text
            and "if not _pt_on:" in text
            and "if not _pt_gpu_supported:\n\t\treturn\n\tvar cnt: int = rd.get_captured_timestamps_count()" in text
        ),
        "captured_frame_dedup": (
            "get_captured_timestamps_frame()" in text
            and "captured_frame == _pt_last_captured_frame" in text
        ),
        "owned_window": (
            'boundary_names[0] != "00_start"' in text
            and 'boundary_names[-1] != "99_end"' in text
        ),
        "single_frame_denominator": (
            "_pt_window_total_us += float(frame_total_us)" in text
            and "_pt_total += float(prev_t - first_t)" not in text
        ),
        "real_gpu_window_duration": "avg_gpu_window_ms=%.3f" in text,
        "real_per_pass_gpu_duration": "per-pass GPU ms: %s" in text,
        "sample_window_reset": (
            "_pt_samples += 1" in text and "_pt_samples = 0" in text
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    for name, passed in checks.items():
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
    print(f"hard_failures={len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
