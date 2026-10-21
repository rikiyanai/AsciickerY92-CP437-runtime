#!/usr/bin/env python3
"""FL-4131 W3 — terrain shade inner-loop perf baseline.

This is a SYNTHETIC Python proxy benchmark that exercises the same kind of
work the C++ terrain renderer does on the per-cell inner loop:

  for each terminal cell:
      (slope_index, light_index) -> Material.shade[slope][light] -> glyph
      use GLYPH_COVERAGE[glyph] to compute per-quadrant transparency votes

It does NOT instrument the real C++ render path. Real timing requires C++
instrumentation, which is out of scope for W3. This baseline gives a
reproducible CPU-bound reference that future regressions (e.g. adding a
sidecar lookup into the hot path) can be measured against in the same
process so the relative cost is comparable.

Front door:
  python3 scripts/fl4131_terrain_shade_perf_baseline.py --baseline
      Run the benchmark and write the result to
      assets/glyphs/fixtures/terrain_shade_perf_baseline.json.
      Refuses to overwrite an existing baseline without --force.

  python3 scripts/fl4131_terrain_shade_perf_baseline.py --measure
      Run the benchmark and emit the result as JSON to stdout.

  python3 scripts/fl4131_terrain_shade_perf_baseline.py --verify
      Run repeated benchmark samples and compare the best observed mean
      against the recorded baseline. Exit 0 if that best sample stays within
      the comparison rule.

Comparison rule (FL-4131 future-sidecar-cost contract):
  Extended sidecar path mean ms/frame MUST be <= 1.20x baseline mean
  ms/frame, OR the deviation MUST be recorded as a reviewed exception
  in docs/FAILURE_LOG.md (overlay PerfExceptionRefs) before any
  FL-4131 Phase 2+ runtime admission.

Hard rules honored:
  - Does not add sidecar lookup to any real hot path.
  - Does not modify any runtime file.
  - Pure stdlib + GLYPH_COVERAGE table reused from
    scripts/compile_actor_visual_profiles.py.
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPO_ROOT / "assets" / "glyphs" / "fixtures" / "terrain_shade_perf_baseline.json"

# Reuse the canonical GLYPH_COVERAGE table from the existing profile compiler
# so the benchmark exercises the same data the renderer's MatCell blending uses.
sys.path.insert(0, str(REPO_ROOT / "scripts"))
try:
    from compile_actor_visual_profiles import GLYPH_COVERAGE  # type: ignore
except Exception as exc:  # pragma: no cover - import fail is fatal
    print(f"[FATAL] cannot import GLYPH_COVERAGE: {exc}", file=sys.stderr)
    raise


# Workload parameters chosen to give a stable ~10-50ms/frame on typical
# laptop CPUs so jitter does not dominate measurements.
WORKLOAD = {
    "grid_width": 160,    # typical terminal width
    "grid_height": 48,    # typical terminal height
    "frames": 30,         # one second-ish at 30 FPS
    "slopes": 4,          # Material.shade first dim
    "lights": 16,         # Material.shade second dim
    "seed": 0x4131,       # deterministic synthesis (FL-4131)
}

# Tolerance bound for --verify mode (1.20x rule from W3 contract).
PERF_REGRESSION_RATIO = 1.20


def _synth_material_shade(rng: random.Random) -> list[list[int]]:
    """Build a fake Material.shade[4][16] table of CP437 glyph indices."""
    return [
        [rng.randrange(0, 256) for _ in range(WORKLOAD["lights"])]
        for _ in range(WORKLOAD["slopes"])
    ]


def _synth_terrain_grid(rng: random.Random) -> list[tuple[int, int]]:
    """Build (slope_index, light_index) tuples for each cell, row-major."""
    slope_count = WORKLOAD["slopes"]
    light_count = WORKLOAD["lights"]
    n = WORKLOAD["grid_width"] * WORKLOAD["grid_height"]
    return [
        (rng.randrange(0, slope_count), rng.randrange(0, light_count))
        for _ in range(n)
    ]


def _one_frame(shade: list[list[int]], grid: list[tuple[int, int]]) -> int:
    """Simulate one render frame.

    Per cell: look up Material.shade[slope][light] -> glyph, then read
    GLYPH_COVERAGE[glyph] and accumulate the four 4-bit quadrant counters.
    Returns a checksum to prevent the optimizer (if any) from discarding work.
    """
    cov = GLYPH_COVERAGE
    checksum = 0
    for slope, light in grid:
        glyph = shade[slope][light] & 0xFF
        c = cov[glyph]
        # four 4-bit fields - mimic the renderer's per-quadrant accumulation
        q0 = c & 0xF
        q1 = (c >> 4) & 0xF
        q2 = (c >> 8) & 0xF
        q3 = (c >> 12) & 0xF
        checksum = (checksum + q0 + q1 + q2 + q3) & 0xFFFFFFFF
    return checksum


def run_benchmark(warmup_frames: int = 3) -> dict[str, Any]:
    rng = random.Random(WORKLOAD["seed"])
    shade = _synth_material_shade(rng)
    grid = _synth_terrain_grid(rng)

    # Warmup to settle CPU caches / Python interpreter state.
    for _ in range(warmup_frames):
        _one_frame(shade, grid)

    frame_times_ms: list[float] = []
    checksum = 0
    for _ in range(WORKLOAD["frames"]):
        t0 = time.perf_counter()
        checksum = _one_frame(shade, grid)
        t1 = time.perf_counter()
        frame_times_ms.append((t1 - t0) * 1000.0)

    cells_per_frame = WORKLOAD["grid_width"] * WORKLOAD["grid_height"]
    total_ops = cells_per_frame * WORKLOAD["frames"]
    total_seconds = sum(frame_times_ms) / 1000.0
    ops_per_sec = total_ops / total_seconds if total_seconds > 0 else 0.0

    return {
        "_comment": (
            "FL-4131 W3 terrain shade perf baseline. NOT a glyph manifest. "
            "The leading underscore key is the convention compile_glyph_manifest.py "
            "uses to skip non-manifest JSON files in assets/glyphs/fixtures/."
        ),
        "version": 1,
        "fl": "FL-4131",
        "gate_candidate": "future:extended_sidecar_perf_within_1_2x_baseline",
        "workload": WORKLOAD,
        "warmup_frames": warmup_frames,
        "system": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "machine": platform.machine(),
        },
        "frame_count": len(frame_times_ms),
        "checksum": checksum,
        "ms_per_frame": {
            "mean": statistics.fmean(frame_times_ms),
            "stdev": statistics.pstdev(frame_times_ms) if len(frame_times_ms) > 1 else 0.0,
            "min": min(frame_times_ms),
            "max": max(frame_times_ms),
            "median": statistics.median(frame_times_ms),
        },
        "ops_per_sec": ops_per_sec,
        "cells_per_frame": cells_per_frame,
        "comparison_rule": {
            "policy": "extended sidecar path mean ms/frame <= baseline * 1.20",
            "ratio_threshold": PERF_REGRESSION_RATIO,
            "exception_owner": "FL-4131 overlay PerfExceptionRefs",
        },
    }


def cmd_baseline(args: argparse.Namespace) -> int:
    if BASELINE_PATH.exists() and not args.force:
        print(
            f"[ERROR] baseline already exists at {BASELINE_PATH.relative_to(REPO_ROOT)}; "
            "pass --force to overwrite.",
            file=sys.stderr,
        )
        return 2
    result = run_benchmark()
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    BASELINE_PATH.write_text(payload, encoding="utf-8")
    mpf = result["ms_per_frame"]["mean"]
    print(
        f"[BASELINE] wrote terrain shade perf baseline to "
        f"{BASELINE_PATH.relative_to(REPO_ROOT)} "
        f"(mean {mpf:.3f} ms/frame, ops/sec {result['ops_per_sec']:.0f})"
    )
    return 0


def cmd_measure(_args: argparse.Namespace) -> int:
    result = run_benchmark()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    if not BASELINE_PATH.exists():
        print(
            f"[ERROR] baseline not found at {BASELINE_PATH.relative_to(REPO_ROOT)}; "
            "run --baseline once first.",
            file=sys.stderr,
        )
        return 3
    try:
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        # Distinct exit code (4) so an orchestrator can tell baseline-corruption
        # apart from regression-FAIL (exit 1).
        print(
            f"[ERROR] baseline at {BASELINE_PATH.relative_to(REPO_ROOT)} could not be read: {exc}",
            file=sys.stderr,
        )
        return 4
    sample_count = max(1, int(args.samples))
    samples = [run_benchmark() for _ in range(sample_count)]
    baseline_mpf = float(baseline["ms_per_frame"]["mean"])
    sample_means = [float(sample["ms_per_frame"]["mean"]) for sample in samples]
    current_mpf = min(sample_means)
    # Honor the baseline's recorded ratio_threshold (the baseline is the
    # contract; reading from the local constant would defeat per-baseline
    # versioning). Fall back to the constant only if the baseline lacks the
    # comparison_rule block.
    cmp_rule = baseline.get("comparison_rule") or {}
    threshold = float(cmp_rule.get("ratio_threshold", PERF_REGRESSION_RATIO))
    if baseline_mpf > 0:
        ratio: float = current_mpf / baseline_mpf
        ratio_repr: float | str = ratio
        ok = ratio <= threshold
    else:
        # Avoid serializing float('inf') (json.dumps emits "Infinity", which
        # is invalid per RFC 8259). Use a sentinel string for the report and
        # treat the verdict as FAIL.
        ratio = float("inf")
        ratio_repr = "inf"
        ok = False
    report = {
        "ok": ok,
        "baseline_ms_per_frame_mean": baseline_mpf,
        "current_ms_per_frame_mean": current_mpf,
        "current_statistic": "best_of_sample_means",
        "sample_count": sample_count,
        "sample_ms_per_frame_means": sample_means,
        "ratio": ratio_repr,
        "ratio_threshold": threshold,
        "policy": cmp_rule.get("policy", "extended sidecar path mean ms/frame <= baseline * 1.20"),
        "note": "synthetic Python benchmark; real C++ render-path timing is out of scope for W3",
    }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        ratio_str = f"{ratio:.3f}" if baseline_mpf > 0 else "inf"
        print(
            f"baseline mean ms/frame: {baseline_mpf:.3f}  "
            f"current best mean ms/frame: {current_mpf:.3f}  "
            f"samples: {sample_count}  "
            f"ratio: {ratio_str} (threshold {threshold:.2f})"
        )
        print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--baseline", action="store_true", help="run benchmark and write baseline")
    mode.add_argument("--measure", action="store_true", help="run benchmark and emit JSON")
    mode.add_argument("--verify", action="store_true", help="run benchmark and compare against baseline")
    parser.add_argument("--force", action="store_true", help="overwrite existing baseline")
    parser.add_argument("--json", action="store_true", help="machine-readable verify output")
    parser.add_argument("--samples", type=int, default=7, help="verify sample count (default: 7)")
    args = parser.parse_args()
    if args.baseline:
        return cmd_baseline(args)
    if args.measure:
        return cmd_measure(args)
    if args.verify:
        return cmd_verify(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
