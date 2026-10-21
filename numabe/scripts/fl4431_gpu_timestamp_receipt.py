#!/usr/bin/env python3
# ============================================================================
# fl4431_gpu_timestamp_receipt.py
# ============================================================================
# FL-4431 GPU-timestamp acquisition — receipt builder bound to a real run.
#
# WHAT THIS IS
#   The renderer's per-pass GPU timing is captured by the SINGLE measurement
#   owner in godot_project/render/api/ASCIICompositorEffect.gd:
#       _pt_stamp(label) -> rd.capture_timestamp(label)
#   Those are Godot RenderingDevice timestamp queries. Godot 4.6.2's native
#   Metal driver implements timestamp writes as no-ops and returns zero query
#   results, so the canonical Metal route emits an explicit unsupported marker
#   and never pretends to have GPU durations. Backends with real timestamp-query
#   support measure the GPU window 00_start..99_end directly and, over 30
#   samples, print:
#
#     PERF_GPU_TRACE enabled=1 (per-dispatch GPU timestamps, %-of-frame, ~1/sec)
#     ...
#     PERF_GPU_TRACE internal=WxH (M.MMMpx) gpu_samples=30 avg_gpu_window_ms=X gpu_passes_seen=K \
#       | per-pass % of traced GPU window: key=P%, ... | per-pass GPU ms: key=Mms, ...
#     PERF_FRAME_TRACE frames=30 internal=WxH avg_wall_ms=F views=N key_ms=... ...   (CPU wall)
#
#   This module is a READ-ONLY CONSUMER of that owner: it turns a REAL run into
#   one canonical receipt of DIRECTLY-MEASURED GPU-window + per-pass milliseconds
#   with fail-closed provenance. It never captures timestamps itself, launches
#   Godot, or alters the renderer, so it is not a competing measurement owner.
#
# DIRECT-DURATION INVARIANT (no fabrication)
#   avg_gpu_window_ms and per_pass_gpu_ms are read verbatim from the GPU trace —
#   they are RenderingDevice timestamp measurements, never a GPU share multiplied
#   by the CPU wall. The per-pass GPU shares (% of the traced GPU window) and the
#   PERF_FRAME_TRACE CPU-callback wall figures are kept as SEPARATE, labelled
#   fields. The receipt is rejected unless the per-pass durations sum to
#   avg_gpu_window_ms within a rounding-aware tolerance.
#
#   occupancy / bandwidth counters remain UNAVAILABLE (RenderingDevice exposes
#   timestamps only; they need Metal performance counters via Instruments/xctrace
#   or a Metal GDExtension = full Xcode). They are never fabricated; that surface
#   is deferred to the external-tool contract.
#
# FAIL-CLOSED BINDING
#   Built from the ACTUAL per-arm receipt JSON emitted by
#   run_godot_camera_turn_perf.py and its REQUIRED AB summary. Provenance is owned
#   by the runtime-source FINGERPRINT: the summary must be ok / not aborted /
#   scoped_clean / runtime_source_stable with nonempty EQUAL baseline+final
#   fingerprints and nonempty baseline/final/arm HEADs; when head_drift_observed
#   is false the three HEADs must be equal, when true unrelated HEAD drift is
#   admitted but the equal fingerprint is still required. Caller pose/dimension
#   labels are only expected values compared against observed receipt fields.
#   Trace events are paired IN ORDER: the trailing window must be one parseable
#   GPU line immediately followed by one parseable FRAME line with matching
#   internal dimensions before the next GPU event.
#
# USAGE
#   python3 scripts/fl4431_gpu_timestamp_receipt.py <arm_receipt.json>
#           --summary <ab_summary.json> [--out receipt.json]
#           [--expect-pose x,y,z] [--expect-presentation WxH]
#           [--expect-target WxH] [--expect-ansi WxH]
#           [--min-sample-count N] [--min-gpu-samples N] [--stamp-utc ISO8601]
#   Exit 0 only when the receipt is VALID (fail-closed); non-zero otherwise.
# ============================================================================

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

_GPU_PREFIX = "PERF_GPU_TRACE "
_GPU_UNSUPPORTED_PREFIX = "PERF_GPU_TRACE_UNSUPPORTED "
_FRAME_PREFIX = "PERF_FRAME_TRACE "

_POSE_EPS = 1e-3
# Per-pass share total lands near 100 % after 1-decimal rounding of ~6 passes.
_SHARE_LO, _SHARE_HI = 99.0, 101.0
# The owned destination boundaries every legitimate compositor GPU window carries
# (interval keys begin AFTER the 00_start window start, so 00_start is not a key).
_CORE_PASSES = ("10_splat", "12_terrain_resolve", "20_cell_product",
                "30_compositor", "40_present", "99_end")


def _parse_size_token(value: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d+)x(\d+)", value.strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def _sole_observed_pair(node) -> tuple[int, int] | None:
    """observed_dimensions entries look like [[W, H]]. Return the single distinct
    (W, H); None if empty or not unanimous (a wobbled dimension fails closed)."""
    if not isinstance(node, list) or not node:
        return None
    pairs = {tuple(p) for p in node if isinstance(p, list) and len(p) == 2}
    if len(pairs) != 1:
        return None
    w, h = next(iter(pairs))
    return int(w), int(h)


def _parse_kv_map(blob: str, suffix: str) -> tuple[dict[str, float], bool]:
    """Parse 'key=<num><suffix>, ...' into ({key: value}, ok). ok is False if any
    fragment is malformed (so a truncated blob is rejected)."""
    out: dict[str, float] = {}
    malformed = False
    for frag in blob.split(","):
        frag = frag.strip()
        if not frag:
            continue
        m = re.fullmatch(r"([0-9A-Za-z_]+)=(-?[0-9.]+|nan|inf|-inf)" + re.escape(suffix), frag)
        if not m:
            malformed = True
            continue
        try:
            out[m.group(1)] = float(m.group(2))
        except ValueError:
            malformed = True
    return out, (bool(out) and not malformed)


def _parse_gpu_trace_line(seg: str) -> dict:
    """Parse a PERF_GPU_TRACE DATA line (not the enabled marker) in the direct-
    measurement format. Returns {ok, bad_reason, internal, gpu_samples,
    avg_gpu_window_ms, gpu_passes_seen, shares, durations}."""
    m = re.search(
        r"internal=(\d+x\d+).*?gpu_samples=(\d+)\s+avg_gpu_window_ms=([0-9.]+|nan|inf|-inf)\s+"
        r"gpu_passes_seen=(\d+)\s*\|\s*per-pass % of traced GPU window:\s*(.*?)\s*\|\s*"
        r"per-pass GPU ms:\s*(\S.*)$",
        seg,
    )
    if not m:
        # A recognizable GPU data line that is missing the duration block is a
        # distinct, nameable failure; anything else is generically malformed.
        if "gpu_passes_seen=" in seg and "per-pass GPU ms:" not in seg:
            return {"ok": False, "bad_reason": "duration_block_missing"}
        return {"ok": False, "bad_reason": "trailing_malformed_gpu_line"}
    shares, shares_ok = _parse_kv_map(m.group(5), "%")
    durations, dur_ok = _parse_kv_map(m.group(6), "ms")
    try:
        window_ms = float(m.group(3))
    except ValueError:
        window_ms = float("nan")
    ok = shares_ok and dur_ok
    return {
        "ok": ok,
        "bad_reason": (None if ok else "trailing_malformed_gpu_line"),
        "internal": _parse_size_token(m.group(1)),
        "gpu_samples": int(m.group(2)),
        "avg_gpu_window_ms": window_ms,
        "gpu_passes_seen": int(m.group(4)),
        "shares": shares,
        "durations": durations,
    }


def _parse_gpu_unsupported_line(seg: str) -> dict:
    """Parse the fail-closed marker for a backend with no usable timestamp
    query implementation."""
    m = re.search(r"enabled=1\s+backend=([0-9A-Za-z_]+)\s+reason=([0-9A-Za-z_]+)", seg)
    if not m:
        return {"ok": False, "backend": None, "unsupported_reason": None}
    return {"ok": True, "backend": m.group(1), "unsupported_reason": m.group(2)}


def _parse_frame_trace_line(seg: str) -> dict:
    """Parse a PERF_FRAME_TRACE (CPU wall) line."""
    m = re.search(r"internal=(\d+x\d+)\s+avg_wall_ms=([0-9.]+)\s+views=(\d+)\s+(\S.*)$", seg)
    if not m:
        return {"ok": False, "internal": None, "frame_avg_wall_ms_cpu": None,
                "views": None, "per_pass_cpu_ms": {}}
    cpu = {mm.group(1): float(mm.group(2)) for mm in re.finditer(r"([0-9A-Za-z_]+)_ms=([0-9.]+)", m.group(4))}
    return {"ok": True, "internal": _parse_size_token(m.group(1)),
            "frame_avg_wall_ms_cpu": float(m.group(2)), "views": int(m.group(3)),
            "per_pass_cpu_ms": cpu}


def _pair_trailing_window(log_text: str) -> dict:
    """Return the TRAILING trace window + the enabled-marker flag. A GPU data line
    opens a window; the FIRST following FRAME line (before the next GPU) closes it;
    only the last window is used, so the tail of the log must be well-formed."""
    enabled = False
    unsupported: dict | None = None
    last_gpu: dict | None = None
    frame_after: dict | None = None
    for line in log_text.splitlines():
        if line.find(_GPU_UNSUPPORTED_PREFIX) >= 0:
            seg = line[line.index(_GPU_UNSUPPORTED_PREFIX):]
            unsupported = _parse_gpu_unsupported_line(seg)
            enabled = True
        elif line.find(_GPU_PREFIX) >= 0:
            seg = line[line.index(_GPU_PREFIX):]
            if "enabled=1" in seg and "gpu_passes_seen=" not in seg:
                enabled = True
                continue
            last_gpu = _parse_gpu_trace_line(seg)
            frame_after = None
        elif line.find(_FRAME_PREFIX) >= 0:
            seg = line[line.index(_FRAME_PREFIX):]
            if last_gpu is not None and frame_after is None:
                frame_after = _parse_frame_trace_line(seg)

    if unsupported is not None:
        if not unsupported.get("ok"):
            return {"enabled": True, "gpu": None, "frame": None,
                    "unsupported": unsupported,
                    "reason": "gpu_timestamp_unsupported_marker_malformed"}
        return {
            "enabled": True,
            "gpu": None,
            "frame": None,
            "unsupported": unsupported,
            "reason": (
                "gpu_timestamp_surface_unsupported:"
                f"{unsupported['backend']}:{unsupported['unsupported_reason']}"
            ),
        }
    if not enabled:
        return {"enabled": False, "gpu": last_gpu, "frame": frame_after,
                "unsupported": None, "reason": "gpu_trace_marker_absent"}
    if last_gpu is None:
        return {"enabled": True, "gpu": None, "frame": None,
                "unsupported": None, "reason": "no_gpu_trace"}
    if not last_gpu.get("ok"):
        return {"enabled": True, "gpu": last_gpu, "frame": frame_after,
                "unsupported": None,
                "reason": last_gpu.get("bad_reason", "trailing_malformed_gpu_line")}
    if frame_after is None:
        return {"enabled": True, "gpu": last_gpu, "frame": None,
                "unsupported": None, "reason": "trailing_unmatched_gpu_line"}
    if not frame_after.get("ok"):
        return {"enabled": True, "gpu": last_gpu, "frame": frame_after,
                "unsupported": None, "reason": "malformed_final_frame_line"}
    if last_gpu.get("internal") is None or last_gpu["internal"] != frame_after.get("internal"):
        return {"enabled": True, "gpu": last_gpu, "frame": frame_after,
                "unsupported": None,
                "reason": "trace_dimension_mismatch_gpu_vs_frame"}
    return {"enabled": True, "gpu": last_gpu, "frame": frame_after,
            "unsupported": None, "reason": None}


def _validate_gpu_content(gpu: dict, min_gpu_samples: int) -> list[str]:
    """Direct-measurement content validity: sample floor, finite positive window,
    identical share/duration key sets, gpu_passes_seen equal to both counts, core
    boundaries present, finite non-negative shares+durations, shares near 100 %,
    and durations summing to avg_gpu_window_ms within a rounding-aware tolerance."""
    reasons: list[str] = []
    shares = gpu.get("shares") or {}
    durations = gpu.get("durations") or {}
    samples = gpu.get("gpu_samples")
    window = gpu.get("avg_gpu_window_ms")

    if not isinstance(samples, int) or samples < min_gpu_samples:
        reasons.append(f"gpu_samples_below_floor:{samples}<{min_gpu_samples}")
    if not isinstance(window, (int, float)) or not math.isfinite(window) or window <= 0.0:
        reasons.append(f"avg_gpu_window_ms_nonpositive:{window}")
    if set(shares.keys()) != set(durations.keys()):
        reasons.append("share_duration_key_mismatch")
    if gpu.get("gpu_passes_seen") != len(shares) or gpu.get("gpu_passes_seen") != len(durations):
        reasons.append(
            f"gpu_passes_seen_ne_map_counts:{gpu.get('gpu_passes_seen')}!=({len(shares)},{len(durations)})")
    missing = [p for p in _CORE_PASSES if p not in shares or p not in durations]
    if missing:
        reasons.append(f"core_pass_names_missing:{missing}")
    if any((not math.isfinite(v)) or v < 0.0 for v in shares.values()):
        reasons.append("gpu_share_negative_or_nonfinite")
    if any((not math.isfinite(v)) or v < 0.0 for v in durations.values()):
        reasons.append("gpu_duration_negative_or_nonfinite")
    stot = sum(shares.values())
    if not (_SHARE_LO <= stot <= _SHARE_HI):
        reasons.append(f"gpu_share_total_out_of_band:{stot:.3f}")
    if isinstance(window, (int, float)) and math.isfinite(window) and window > 0.0:
        dtot = sum(durations.values())
        # Rounding-aware: durations + window are 3-decimal; allow per-key rounding.
        tol = 0.0005 * (len(durations) + 1) + 0.001
        if abs(dtot - window) > tol:
            reasons.append(f"duration_sum_ne_window:sum={dtot:.4f}:window={window:.4f}:tol={tol:.4f}")
    return reasons


def _validate_draw_calls(mode0: dict) -> tuple[list[str], list | None]:
    """draw_call_band [lo,hi] (lo>0, hi>=lo) + samples[*].perf.draw_calls, all
    positive and inside the band. Returns (reasons, observed_band)."""
    reasons: list[str] = []
    band = mode0.get("draw_call_band")
    band_ok = (isinstance(band, list) and len(band) == 2
               and all(isinstance(x, (int, float)) for x in band)
               and band[0] > 0 and band[1] >= band[0])
    if not band_ok:
        reasons.append(f"draw_call_band_invalid:{band}")
    dc = [((s or {}).get("perf") or {}).get("draw_calls") for s in (mode0.get("samples") or [])]
    if not dc:
        reasons.append("draw_call_samples_absent")
    elif any((not isinstance(v, (int, float))) or v <= 0 for v in dc):
        reasons.append("draw_call_samples_nonpositive_or_nonnumeric")
    elif band_ok and any(not (band[0] <= v <= band[1]) for v in dc):
        reasons.append(f"draw_call_samples_outside_band:band={band}")
    return reasons, (band if band_ok else None)


def _validate_provenance(arm: dict, summary_path: str | None) -> tuple[list[str], dict | None]:
    """Provenance owned by the runtime-source FINGERPRINT (per the committed
    harness). The summary is REQUIRED. Returns (reasons, summary_or_None)."""
    reasons: list[str] = []
    if summary_path is None:
        return ["summary_required"], None
    sp = Path(summary_path)
    if not sp.exists():
        return ["requested_summary_missing"], None
    try:
        summary = json.loads(sp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ["requested_summary_unparseable"], None

    if summary.get("ok") is False or summary.get("aborted") is True:
        reasons.append("summary_not_ok_or_aborted")
    if summary.get("scoped_clean") is not True:
        reasons.append("scoped_source_not_clean")
    if summary.get("runtime_source_stable") is not True:
        reasons.append("runtime_source_not_stable")

    bfp = summary.get("baseline_runtime_source_sha256")
    ffp = summary.get("final_runtime_source_sha256")
    if not bfp or not ffp:
        reasons.append("runtime_fingerprint_empty")
    elif bfp != ffp:
        reasons.append("runtime_fingerprint_drift")  # equality is ALWAYS required

    bh, fh, ah = summary.get("baseline_head"), summary.get("final_head"), arm.get("git_head")
    if not bh or not fh or not ah:
        reasons.append("head_empty")
    elif summary.get("head_drift_observed") is not True:
        # No admitted drift: the three HEADs must be identical.
        if not (bh == fh == ah):
            reasons.append(f"head_mismatch_without_drift:baseline={bh}:final={fh}:arm={ah}")
        # With head_drift_observed True, unrelated HEAD drift is permitted; the
        # equal runtime fingerprint (enforced above) is what guarantees identical
        # runtime source across the window.
    return reasons, summary


def build_receipt(
    arm_receipt_path: str,
    *,
    summary_path: str | None = None,
    expect_pose: str | None = None,
    expect_presentation: str | None = None,
    expect_target: str | None = None,
    expect_ansi: str | None = None,
    min_sample_count: int = 120,
    min_gpu_samples: int = 30,
    stamp_utc: str | None = None,
) -> dict:
    """Build the FL-4431 GPU-timestamp receipt from a real run. `valid` is True
    only when every fail-closed gate passes."""
    reasons: list[str] = []

    arm_p = Path(arm_receipt_path)
    if not arm_p.exists():
        return {"schema": "fl4431_gpu_timestamp_receipt.v2", "valid": False,
                "reason": "arm_receipt_missing", "arm_receipt_path": arm_receipt_path}
    try:
        arm = json.loads(arm_p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"schema": "fl4431_gpu_timestamp_receipt.v2", "valid": False,
                "reason": "arm_receipt_unparseable", "arm_receipt_path": arm_receipt_path}

    mode0 = (arm.get("mode_results") or [{}])[0]
    validity = mode0.get("validity") or {}
    obs = arm.get("observed_dimensions") or {}
    obs_pres = _sole_observed_pair(obs.get("presentation_size"))
    obs_targ = _sole_observed_pair(obs.get("target_size"))
    obs_ansi = _sole_observed_pair(obs.get("semantic_ansi_size"))
    settled = validity.get("settled_origin")
    sample_count = mode0.get("sample_count")
    log_rel = arm.get("log")

    if arm.get("ok") is not True:
        reasons.append("arm_not_ok")
    if arm.get("dimension_valid") is not True:
        reasons.append("dimension_invalid")
    if obs_pres is None or obs_targ is None or obs_ansi is None:
        reasons.append("observed_dimensions_missing_or_wobbled")
    for label_name, want_str, observed in (
        ("presentation", expect_presentation, obs_pres),
        ("target", expect_target, obs_targ),
        ("ansi", expect_ansi, obs_ansi),
    ):
        if want_str is not None:
            want = _parse_size_token(want_str)
            if want is None or observed is None or want != observed:
                reasons.append(f"dimension_mismatch:{label_name}:expected={want}:observed={observed}")
    if settled is None or len(settled) != 3:
        reasons.append("settled_origin_missing")
    elif expect_pose is not None:
        try:
            want_xyz = [float(x) for x in expect_pose.split(",")]
        except ValueError:
            want_xyz = None
        if want_xyz is None or len(want_xyz) != 3 or any(
            abs(float(a) - b) > _POSE_EPS for a, b in zip(settled, want_xyz)
        ):
            reasons.append(f"pose_mismatch:expected={expect_pose}:observed={settled}")
    if not isinstance(sample_count, int) or sample_count < min_sample_count:
        reasons.append(f"sample_count_below_floor:{sample_count}<{min_sample_count}")

    dc_reasons, observed_band = _validate_draw_calls(mode0)
    reasons.extend(dc_reasons)

    prov_reasons, summary = _validate_provenance(arm, summary_path)
    reasons.extend(prov_reasons)

    # --- log identity + ordered trailing trace window ---
    log_abs = None
    if not isinstance(log_rel, str) or not log_rel:
        reasons.append("arm_log_path_absent")
    else:
        cand = arm_p.parent / Path(log_rel).name
        if cand.exists():
            log_abs = cand
        elif Path(log_rel).exists():
            log_abs = Path(log_rel)
        else:
            reasons.append(f"arm_log_not_found:{log_rel}")

    window = {"enabled": False, "gpu": None, "frame": None,
              "unsupported": None, "reason": "arm_log_not_found"}
    if log_abs is not None:
        window = _pair_trailing_window(log_abs.read_text(encoding="utf-8", errors="replace"))
        if window["reason"] is not None:
            reasons.append(window["reason"])
        else:
            reasons.extend(_validate_gpu_content(window["gpu"], min_gpu_samples))
            # The GPU/FRAME internal size is scene_buffers.get_internal_size() =
            # the PRESENTATION size (e.g. 3600x2252), NOT the PixelCamera target
            # (3600x2260). Bind the trace-internal gate to observed presentation;
            # the target-size gate stays separate (the expected/observed target
            # dimension check above).
            if obs_pres is not None and window["gpu"]["internal"] != obs_pres:
                reasons.append(
                    f"trace_dimension_mismatch_vs_presentation:trace={window['gpu']['internal']}:observed_presentation={obs_pres}")

    ok = not reasons
    gpu = window.get("gpu") or {}
    frame = window.get("frame") or {}
    complete = window["reason"] is None

    return {
        "schema": "fl4431_gpu_timestamp_receipt.v2",
        "timestamp_surface": "godot RenderingDevice rd.capture_timestamp (--perf-trace)",
        "timestamp_supported": complete,
        "timestamp_unsupported": window.get("unsupported"),
        "measurement_owner": "godot_project/render/api/ASCIICompositorEffect.gd:_pt_stamp/_pt_collect_and_report",
        # --- DIRECTLY-MEASURED GPU window + per-pass milliseconds (from timestamps) ---
        "internal_size": (gpu.get("internal") if complete else None),
        "gpu_samples": gpu.get("gpu_samples"),
        "gpu_passes_seen": gpu.get("gpu_passes_seen"),
        "avg_gpu_window_ms": gpu.get("avg_gpu_window_ms"),
        "per_pass_gpu_ms": gpu.get("durations") or {},
        # --- per-pass share of the traced GPU window (also a direct measurement) ---
        "per_pass_gpu_frac_pct_of_window": gpu.get("shares") or {},
        # --- CPU render-callback wall (SEPARATE; never used to derive GPU ms) ---
        "frame_avg_wall_ms_cpu": frame.get("frame_avg_wall_ms_cpu"),
        "views": frame.get("views"),
        "per_pass_cpu_ms": frame.get("per_pass_cpu_ms") or {},
        # --- occupancy / bandwidth (unavailable in the current source) ---
        "occupancy_bandwidth": {
            "supported": False,
            "reason": "godot RenderingDevice exposes timestamps only; occupancy/bandwidth "
            "require Metal performance counters (MTLCounterSampleBuffer via "
            "Instruments/xctrace or a Metal GDExtension, full Xcode). Deferred to the "
            "external-tool contract.",
        },
        # --- OBSERVED provenance (from the real run receipt + summary fingerprint) ---
        "observed": {
            "arm_ok": arm.get("ok"),
            "git_head": arm.get("git_head"),
            "git_dirty": arm.get("git_dirty"),
            "dimension_valid": arm.get("dimension_valid"),
            "observed_presentation_size": obs_pres,
            "observed_target_size": obs_targ,
            "observed_ansi_size": obs_ansi,
            "settled_origin": settled,
            "sample_count": sample_count,
            "draw_call_band": observed_band,
            "gpu_trace_enabled_marker": window.get("enabled"),
            "log_path": (str(log_abs) if log_abs is not None else None),
            "summary_ok": (summary or {}).get("ok"),
            "summary_aborted": (summary or {}).get("aborted"),
            "summary_scoped_clean": (summary or {}).get("scoped_clean"),
            "summary_runtime_source_stable": (summary or {}).get("runtime_source_stable"),
            "summary_head_drift_observed": (summary or {}).get("head_drift_observed"),
            "summary_baseline_head": (summary or {}).get("baseline_head"),
            "summary_final_head": (summary or {}).get("final_head"),
            "summary_baseline_runtime_source_sha256": (summary or {}).get("baseline_runtime_source_sha256"),
            "summary_final_runtime_source_sha256": (summary or {}).get("final_runtime_source_sha256"),
        },
        "expected": {
            "pose": expect_pose,
            "presentation_size": expect_presentation,
            "target_size": expect_target,
            "ansi_size": expect_ansi,
            "min_sample_count": min_sample_count,
            "min_gpu_samples": min_gpu_samples,
        },
        "stamp_utc": stamp_utc,
        "arm_receipt_path": arm_receipt_path,
        "summary_path": summary_path,
        "valid": ok,
        "reason": "ok" if ok else ";".join(reasons),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="FL-4431 GPU-timestamp receipt from a real --perf-trace run")
    ap.add_argument("arm_receipt", help="per-arm receipt JSON from run_godot_camera_turn_perf.py")
    ap.add_argument("--summary", default=None, help="ab_summary.json (REQUIRED for provenance)")
    ap.add_argument("--out", default="", help="write the JSON receipt here (default: stdout)")
    ap.add_argument("--expect-pose", default=None, help="expected effective camera origin x,y,z")
    ap.add_argument("--expect-presentation", default=None, help="expected presentation WxH")
    ap.add_argument("--expect-target", default=None, help="expected target WxH")
    ap.add_argument("--expect-ansi", default=None, help="expected ANSI WxH")
    ap.add_argument("--min-sample-count", type=int, default=120, help="minimum settled sample_count")
    ap.add_argument("--min-gpu-samples", type=int, default=30, help="minimum GPU trace sample count")
    ap.add_argument("--stamp-utc", default=None, help="ISO-8601 UTC timestamp (caller-supplied)")
    args = ap.parse_args(argv)

    receipt = build_receipt(
        args.arm_receipt,
        summary_path=args.summary,
        expect_pose=args.expect_pose,
        expect_presentation=args.expect_presentation,
        expect_target=args.expect_target,
        expect_ansi=args.expect_ansi,
        min_sample_count=args.min_sample_count,
        min_gpu_samples=args.min_gpu_samples,
        stamp_utc=args.stamp_utc,
    )
    text = json.dumps(receipt, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out} (valid={receipt['valid']} reason={receipt['reason']})")
    else:
        print(text)
    return 0 if receipt["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
