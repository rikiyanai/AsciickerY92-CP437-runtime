#!/usr/bin/env python3
"""Run Godot camera-turn FPS isolation variants.

This is a runtime diagnostic harness, not a proof gate. It launches the real
Main.tscn through a small Godot SceneTree runner, rotates the camera pivot, and
records achieved yaw/sec plus FPS for each compositor isolation switch.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import argparse
import re
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

from maintainer.lib.run_budget_guard import check_run_budget

FL4691_STAGE_TIMING_SCHEMA = "fl4691.stage_timing.v1"
FL4691_REQUIRED_STAGE_MS = (
    "frame_setup_cpu_ms",
    "dynamic_mover_publish_cpu_ms",
    "surface_facts_compile_upload_cpu_ms",
    "vsb_view_build_cpu_ms",
    "product_prepare_cpu_ms",
    "vsb_product_submit_cpu_ms",
    "final_paint_submit_cpu_ms",
)
FL4691_SUSPECTED_OWNER_SCHEMA = "fl4691.suspected_owner_matrix.v2"
FL4691_MEASURED_CPU_STAGE_SPECS = (
    {
        "owner": "dynamic_mover_publication",
        "stage": "dynamic_mover_publish_cpu_ms",
        "source": "ASCIICompositorEffect.gd:_publish_dynamic_mover_source_facts_for_pixel_context",
        "original_suspect": True,
    },
    {
        "owner": "surface_facts_compile_upload",
        "stage": "surface_facts_compile_upload_cpu_ms",
        "source": "ASCIICompositorEffect.gd:_ensure_water_interaction_field_synced",
        "original_suspect": False,
    },
    {
        "owner": "visible_surface_builder_view_build",
        "stage": "vsb_view_build_cpu_ms",
        "source": "ASCIICompositorEffect.gd:_build_vsb_view_dispatch",
        "original_suspect": True,
    },
    {
        "owner": "cell_owner_product_prepare",
        "stage": "product_prepare_cpu_ms",
        "source": "ASCIICompositorEffect.gd:_prepare_cell_owner_product_dispatch",
        "original_suspect": True,
    },
    {
        "owner": "vsb_product_dispatch_submit",
        "stage": "vsb_product_submit_cpu_ms",
        "source": "ASCIICompositorEffect.gd VSB plus Product compute-list submit",
        "original_suspect": True,
    },
    {
        "owner": "final_painter_submit",
        "stage": "final_paint_submit_cpu_ms",
        "source": "ASCIICompositorEffect.gd final compositor compute-list submit",
        "original_suspect": True,
    },
    {
        "owner": "frame_setup_residual",
        "stage": "frame_setup_cpu_ms",
        "source": "ASCIICompositorEffect.gd render-callback setup outside named suspect stages",
        "original_suspect": False,
    },
)
FL4691_UNMEASURED_HYPOTHESES = (
    {
        "hypothesis": "actor_foliage_product_gpu_work",
        "source": "cell_owner_product.glsl actor cells plus foliage leaf bank",
        "reason": "CPU Product preparation does not isolate GPU shader execution; a Product GPU substage probe is required",
        "original_suspect": True,
    },
)
FL4691_DYNAMIC_BREAKDOWN_KEYS = (
    "build_ms",
    "validate_ms",
    "publish_ms",
    "total_ms",
)
FL4691_DYNAMIC_BREAKDOWN_V2_KEYS = FL4691_DYNAMIC_BREAKDOWN_KEYS[:-1] + (
    "publication_wrapper_ms", "rigid_sync_ms", "total_ms",
)

# FL-60FPS diagnostic contract. This is deliberately a fixed diagnostic
# deadline: the harness reports whether every measured interval met the
# requested 60 FPS budget, while the existing ``ok`` field continues to mean
# that the receipt and its provenance are valid. The two answers must remain
# independently readable when a valid run is slower than the target.
PERFORMANCE_DEADLINE_MS = 16.667
PERFORMANCE_DIAGNOSTIC_SCHEMA = "fl4431.performance_diagnostic.v1"

# FL-4265 authority-transport witness. Queue counters are whole-run state, so a
# drop that happened while the renderer was starting must remain visible even
# when the measured window itself drained cleanly. The Python receipt layer
# classifies that distinction; it never treats missing counters or silence in
# the log as continuity evidence.
FL4265_AUTHORITY_QUEUE_SCHEMA = "fl4265.authority_queue_window.v1"
FL4265_QUEUE_COUNTERS = (
    "limit",
    "pending",
    "high_water",
    "overflow_episodes",
    "dropped",
    "ingest_seq",
)
FL4265_MONOTONIC_COUNTERS = (
    "high_water",
    "overflow_episodes",
    "dropped",
    "ingest_seq",
)

# FL-4431 distribution metrics (FL4431-DISTRIBUTION-*-20260717): every mode
# receipt carries median_delta_ms, p95_delta_ms, p1_fps, hitch_count_33_333ms,
# hitch_rate, sample_count, and the raw frame_deltas_ms array alongside the
# canonical avg_delta_ms mean. Each is validated fail-shut per-field in
# summarize() (frame-time percentiles strictly positive, hitch count an int in
# [0, sample_count], hitch rate in [0, 1]).


def _finite_number(value) -> bool:
    """True only for a real, finite int/float (bool excluded — it is not a
    measurement). NaN and +/-inf are rejected so a poisoned arm can never read
    as valid distribution data."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _finite_positive(value) -> bool:
    """True only for a finite, strictly-positive number — a frame-time
    percentile or FPS of zero or below is physically impossible."""
    return _finite_number(value) and float(value) > 0.0


def _valid_frame_deltas(frame_deltas, sample_count) -> bool:
    """The raw per-frame array must be present, non-empty, exactly sample_count
    long, and every element numeric, finite, and strictly positive. Any
    violation fails closed (a zero or negative frame time is impossible and a
    length mismatch means the distribution stats describe a different sample
    set than the receipt claims)."""
    if not isinstance(frame_deltas, list) or not frame_deltas:
        return False
    if not isinstance(sample_count, int) or isinstance(sample_count, bool):
        return False
    if len(frame_deltas) != sample_count:
        return False
    return all(_finite_number(x) and float(x) > 0.0 for x in frame_deltas)


def _authority_queue_snapshot_state(snapshot: object) -> tuple[str, str, dict | None]:
    """Validate one FL-4265 queue snapshot without interpreting log silence.

    ``unknown`` is reserved for absent/explicitly unavailable evidence. A
    present counter with the wrong type, a negative value, or an invalid limit
    is ``invalid`` because it cannot support a continuity claim.
    """
    if not isinstance(snapshot, dict):
        return "unknown", "queue_snapshot_missing", None
    if snapshot.get("status") != "available":
        reason = str(snapshot.get("reason", "queue_snapshot_unavailable"))
        return "unknown", reason, None
    missing = [key for key in FL4265_QUEUE_COUNTERS if key not in snapshot]
    if missing:
        return "unknown", "queue_counters_missing:" + ",".join(missing), None
    normalized: dict[str, int] = {}
    for key in FL4265_QUEUE_COUNTERS:
        value = snapshot.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return "invalid", f"queue_counter_invalid:{key}", None
        normalized[key] = int(value)
    if normalized["limit"] <= 0 or normalized["pending"] > normalized["limit"]:
        return "invalid", "queue_limit_or_pending_invalid", None
    # Optional timing metadata is validated when supplied, but its absence does
    # not make the queue counters unknown: older client receipts did not expose
    # the server tick fields.
    for key in ("server_tick",):
        if key in snapshot and (
            isinstance(snapshot[key], bool)
            or not isinstance(snapshot[key], int)
            or snapshot[key] < 0
        ):
            return "invalid", f"queue_counter_invalid:{key}", None
    if "server_tick_rate_hz" in snapshot and not _finite_number(
        snapshot["server_tick_rate_hz"]
    ):
        return "invalid", "queue_counter_invalid:server_tick_rate_hz", None
    return "measured", "queue_snapshot_valid", normalized


def _authority_queue_diagnostic(mode_result: dict) -> dict:
    """Classify the measured FL-4265 queue window for one projection mode.

    The before/after values describe the entire process lifetime observed by the
    client. Per-frame values prove whether the counters increased during the
    measured interval. A startup drop therefore fails whole-run continuity while
    still allowing the separate measured-window result to be ``passed`` when no
    additional drops occurred. Missing witnesses stay ``unknown``; counter
    regressions are ``invalid``.
    """
    unknown_reasons: list[str] = []
    invalid_reasons: list[str] = []
    before_state, before_reason, before = _authority_queue_snapshot_state(
        mode_result.get("authority_queue_before_measurement")
    )
    after_state, after_reason, after = _authority_queue_snapshot_state(
        mode_result.get("authority_queue_after_measurement")
    )
    for state, reason, label in (
        (before_state, before_reason, "before_measurement"),
        (after_state, after_reason, "after_measurement"),
    ):
        if state == "unknown":
            unknown_reasons.append(f"{label}:{reason}")
        elif state == "invalid":
            invalid_reasons.append(f"{label}:{reason}")

    frame_records = mode_result.get("frame_timing_records")
    if not isinstance(frame_records, list) or not frame_records:
        unknown_reasons.append("frame_queue_witness_missing")
        frame_snapshots: list[dict] = []
    else:
        frame_snapshots = []
        for index, record in enumerate(frame_records):
            if not isinstance(record, dict):
                unknown_reasons.append(f"frame_{index}:record_missing")
                continue
            state, reason, snapshot = _authority_queue_snapshot_state(
                record.get("authority_queue")
            )
            if state == "unknown":
                unknown_reasons.append(f"frame_{index}:{reason}")
            elif state == "invalid":
                invalid_reasons.append(f"frame_{index}:{reason}")
            elif snapshot is not None:
                frame_snapshots.append(snapshot)
        sample_count = mode_result.get("sample_count")
        if isinstance(sample_count, int) and not isinstance(sample_count, bool):
            if sample_count <= 0 or len(frame_records) != sample_count:
                invalid_reasons.append("frame_record_count_mismatch")
        else:
            unknown_reasons.append("sample_count_missing")

    snapshots = ([before] if before is not None else []) + frame_snapshots
    if after is not None:
        snapshots.append(after)
    if not invalid_reasons and snapshots:
        for previous, current in zip(snapshots, snapshots[1:]):
            if previous["limit"] != current["limit"]:
                invalid_reasons.append("queue_limit_changed")
                break
            for key in FL4265_MONOTONIC_COUNTERS:
                if current[key] < previous[key]:
                    invalid_reasons.append(f"queue_counter_reset:{key}")
                    break
            if invalid_reasons:
                break

    common = {
        "schema": FL4265_AUTHORITY_QUEUE_SCHEMA,
        "before_measurement": mode_result.get(
            "authority_queue_before_measurement"
        ),
        "after_measurement": mode_result.get("authority_queue_after_measurement"),
        "frame_record_count": len(frame_records) if isinstance(frame_records, list) else None,
        "whole_run_dropped_count": None,
        "startup_dropped_count": None,
        "measured_window_dropped_count": None,
        "whole_run_overflow_episodes": None,
        "startup_overflow_episodes": None,
        "measured_window_overflow_episodes": None,
        "whole_run_continuity": None,
        "whole_run_continuity_status": "unknown",
        "measured_window_continuity": None,
        "measured_window_continuity_status": "unknown",
        "status": "unknown",
        "reason": "authority_queue_witness_unknown",
        "unknown_reasons": unknown_reasons,
        "invalid_reasons": invalid_reasons,
    }
    if invalid_reasons:
        common["status"] = "invalid"
        common["reason"] = "authority_queue_counter_contract_invalid"
        common["whole_run_continuity_status"] = "invalid"
        common["measured_window_continuity_status"] = "invalid"
        common["whole_run_continuity"] = False
        common["measured_window_continuity"] = False
        return common
    if unknown_reasons or before is None or after is None or not frame_snapshots:
        return common

    startup_dropped = before["dropped"]
    whole_run_dropped = after["dropped"]
    measured_window_dropped = whole_run_dropped - startup_dropped
    startup_overflows = before["overflow_episodes"]
    whole_run_overflows = after["overflow_episodes"]
    measured_window_overflows = whole_run_overflows - startup_overflows
    if measured_window_dropped < 0 or measured_window_overflows < 0:
        common["status"] = "invalid"
        common["reason"] = "authority_queue_counter_contract_invalid"
        common["invalid_reasons"].append("queue_counter_reset:dropped_or_overflow")
        common["whole_run_continuity_status"] = "invalid"
        common["measured_window_continuity_status"] = "invalid"
        common["whole_run_continuity"] = False
        common["measured_window_continuity"] = False
        return common

    common.update({
        "status": "measured",
        "whole_run_dropped_count": whole_run_dropped,
        "startup_dropped_count": startup_dropped,
        "measured_window_dropped_count": measured_window_dropped,
        "whole_run_overflow_episodes": whole_run_overflows,
        "startup_overflow_episodes": startup_overflows,
        "measured_window_overflow_episodes": measured_window_overflows,
        "whole_run_continuity": whole_run_dropped == 0,
        "whole_run_continuity_status": "passed" if whole_run_dropped == 0 else "failed",
        "measured_window_continuity": measured_window_dropped == 0,
        "measured_window_continuity_status": (
            "passed" if measured_window_dropped == 0 else "failed"
        ),
        "reason": (
            "startup_drops_present"
            if startup_dropped > 0 and measured_window_dropped == 0
            else "measured_window_drops_present"
            if measured_window_dropped > 0
            else "authority_queue_continuity_clean"
        ),
    })
    return common


def _authority_queue_summary(diagnostics: list[dict]) -> dict:
    """Aggregate mode diagnostics without turning unknown evidence into pass."""
    if not diagnostics:
        return {
            "schema": FL4265_AUTHORITY_QUEUE_SCHEMA,
            "status": "unknown",
            "mode_count": 0,
            "whole_run_continuity": None,
            "whole_run_continuity_status": "unknown",
            "measured_window_continuity": None,
            "measured_window_continuity_status": "unknown",
            "reason": "authority_queue_witness_missing",
            "modes": [],
        }
    statuses = [str(item.get("status", "unknown")) for item in diagnostics]
    status = "invalid" if "invalid" in statuses else "unknown" if "unknown" in statuses else "measured"
    whole_values = [item.get("whole_run_continuity") for item in diagnostics]
    measured_values = [item.get("measured_window_continuity") for item in diagnostics]
    whole_pass = all(value is True for value in whole_values) if status == "measured" else None
    measured_pass = all(value is True for value in measured_values) if status == "measured" else None
    return {
        "schema": FL4265_AUTHORITY_QUEUE_SCHEMA,
        "status": status,
        "mode_count": len(diagnostics),
        "whole_run_continuity": whole_pass,
        "whole_run_continuity_status": (
            "passed" if whole_pass is True else "failed" if whole_pass is False else status
        ),
        "measured_window_continuity": measured_pass,
        "measured_window_continuity_status": (
            "passed" if measured_pass is True else "failed" if measured_pass is False else status
        ),
        "startup_drop_mode_count": sum(
            int(item.get("startup_dropped_count", 0) or 0) > 0
            for item in diagnostics
            if item.get("status") == "measured"
        ),
        "measured_drop_mode_count": sum(
            int(item.get("measured_window_dropped_count", 0) or 0) > 0
            for item in diagnostics
            if item.get("status") == "measured"
        ),
        "reason": (
            "authority_queue_counter_contract_invalid"
            if status == "invalid"
            else "authority_queue_witness_unknown"
            if status == "unknown"
            else "authority_queue_continuity_clean"
            if whole_pass is True and measured_pass is True
            else "authority_queue_drops_observed"
        ),
        "modes": diagnostics,
    }


def _nearest_rank_percentile(values: list[float], percentile: float) -> float | None:
    """Return a deterministic nearest-rank percentile from raw intervals.

    The Godot runner already stores the raw per-frame intervals. Percentiles in
    this diagnostic are recomputed from that array so the reported tail cannot
    drift away from the samples or be reconstructed from an average. Nearest
    rank matches the existing GDScript distribution contract.
    """
    if not values or not _finite_number(percentile):
        return None
    if float(percentile) <= 0.0 or float(percentile) > 100.0:
        return None
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(float(percentile) * len(ordered) / 100.0))
    return ordered[rank - 1]


def _longest_deadline_miss_streak(
    frame_deltas: list[float], deadline_ms: float = PERFORMANCE_DEADLINE_MS
) -> int:
    """Count the longest consecutive run of intervals above the deadline."""
    longest = 0
    current = 0
    for value in frame_deltas:
        if float(value) > deadline_ms:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _gpu_diagnostic(mode_result: dict) -> dict:
    """Describe direct GPU timer availability without inventing a value.

    ``render_gpu_ms`` comes directly from Godot's RenderingServer monitor. A
    missing or zero-only series means that this backend does not expose a GPU
    timer for the run; it is reported as the explicit string ``unknown``.
    CPU-minus-wall subtraction is intentionally absent because it would turn
    queueing and synchronization into a fake GPU measurement.
    """
    values: list[float] = []
    samples = mode_result.get("samples", [])
    if isinstance(samples, list):
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            perf = sample.get("perf", {})
            if not isinstance(perf, dict):
                continue
            value = perf.get("render_gpu_ms")
            if _finite_number(value) and float(value) > 0.0:
                values.append(float(value))
    if not values:
        return {
            "schema": PERFORMANCE_DIAGNOSTIC_SCHEMA,
            "status": "unknown",
            "gpu_time_status": "unknown",
            "gpu_time_ms": "unknown",
            "measured_sample_count": 0,
            "reason": "gpu_timer_unavailable_or_unsupported",
        }
    return {
        "schema": PERFORMANCE_DIAGNOSTIC_SCHEMA,
        "status": "measured",
        "gpu_time_status": "measured",
        "gpu_time_ms": _nearest_rank_percentile(values, 50.0),
        "measured_sample_count": len(values),
        "reason": "direct_rendering_server_timer",
    }


def _frame_performance_diagnostic(mode_result: dict) -> dict:
    """Compute the 60 FPS diagnostic from one mode's raw frame intervals.

    This function never changes receipt validity. An invalid or incomplete raw
    interval array yields an explicit unknown diagnostic and a false machine
    pass, while a valid receipt can independently report a missed deadline.
    """
    frame_deltas = mode_result.get("frame_deltas_ms")
    sample_count = mode_result.get("sample_count")
    raw_valid = _valid_frame_deltas(frame_deltas, sample_count)
    if not raw_valid:
        return {
            "schema": PERFORMANCE_DIAGNOSTIC_SCHEMA,
            "status": "unknown",
            "performance_target_ms": PERFORMANCE_DEADLINE_MS,
            "frame_deadline_ms": PERFORMANCE_DEADLINE_MS,
            "sample_count": sample_count if isinstance(sample_count, int) else None,
            "frames_meeting_deadline": None,
            "frames_missing_deadline": None,
            "frames_meeting": None,
            "frames_missing": None,
            "p95_frame_delta_ms": None,
            "p99_frame_delta_ms": None,
            "longest_miss_streak": None,
            "deadline_pass": False,
            "reason": "raw_frame_intervals_invalid_or_missing",
        }
    values = [float(value) for value in frame_deltas]
    meeting = sum(value <= PERFORMANCE_DEADLINE_MS for value in values)
    missing = len(values) - meeting
    deadline_pass = all(value <= PERFORMANCE_DEADLINE_MS for value in values)
    return {
        "schema": PERFORMANCE_DIAGNOSTIC_SCHEMA,
        "status": "measured",
        "performance_target_ms": PERFORMANCE_DEADLINE_MS,
        "frame_deadline_ms": PERFORMANCE_DEADLINE_MS,
        "sample_count": len(values),
        "frames_meeting_deadline": meeting,
        "frames_missing_deadline": missing,
        # Short aliases keep the summary readable to CLI consumers while the
        # explicit *_deadline names remain the canonical receipt fields.
        "frames_meeting": meeting,
        "frames_missing": missing,
        "p95_frame_delta_ms": _nearest_rank_percentile(values, 95.0),
        "p99_frame_delta_ms": _nearest_rank_percentile(values, 99.0),
        "longest_miss_streak": _longest_deadline_miss_streak(values),
        "deadline_pass": deadline_pass,
        "reason": "raw_frame_intervals",
    }


def _annotate_performance_diagnostics(result: dict) -> dict:
    """Attach per-mode and overall performance diagnostics to an arm receipt."""
    mode_results = result.get("mode_results", [])
    if not isinstance(mode_results, list):
        mode_results = []
    statuses: list[str] = []
    mode_passes: list[bool] = []
    gpu_values: list[float] = []
    overall_frame_deltas: list[float] = []
    overall_diagnostics: list[dict] = []
    authority_diagnostics: list[dict] = []
    for mode_result in mode_results:
        if not isinstance(mode_result, dict):
            continue
        authority = _authority_queue_diagnostic(mode_result)
        mode_result["authority_queue_diagnostic"] = authority
        authority_diagnostics.append(authority)
        diagnostic = _frame_performance_diagnostic(mode_result)
        mode_result["performance_diagnostic"] = diagnostic
        mode_result.update(diagnostic)
        mode_result["receipt_valid"] = (
            not bool(mode_result.get("invalid", False))
            and bool(mode_result.get("validity", {}).get("ready", False))
        )
        gpu = _gpu_diagnostic(mode_result)
        mode_result["gpu_diagnostic"] = gpu
        mode_result["gpu_time_status"] = gpu["gpu_time_status"]
        mode_result["gpu_time_ms"] = gpu["gpu_time_ms"]
        statuses.append(gpu["status"])
        if _finite_number(gpu.get("gpu_time_ms")):
            gpu_values.append(float(gpu["gpu_time_ms"]))
        mode_passes.append(bool(diagnostic["deadline_pass"]))
        overall_diagnostics.append(diagnostic)
        if diagnostic.get("status") == "measured":
            overall_frame_deltas.extend(
                float(value) for value in mode_result.get("frame_deltas_ms", [])
            )
    if statuses and all(status == "measured" for status in statuses):
        gpu_status = "measured"
    elif statuses and all(status == "unknown" for status in statuses):
        gpu_status = "unknown"
    elif statuses:
        gpu_status = "mixed"
    else:
        gpu_status = "unknown"
    all_raw_valid = bool(overall_diagnostics) and all(
        diagnostic.get("status") == "measured"
        for diagnostic in overall_diagnostics
    )
    if all_raw_valid and overall_frame_deltas:
        overall_meeting = sum(
            value <= PERFORMANCE_DEADLINE_MS for value in overall_frame_deltas
        )
        overall_missing = len(overall_frame_deltas) - overall_meeting
        performance_summary = {
            "schema": PERFORMANCE_DIAGNOSTIC_SCHEMA,
            "status": "measured",
            "performance_target_ms": PERFORMANCE_DEADLINE_MS,
            "frame_deadline_ms": PERFORMANCE_DEADLINE_MS,
            "sample_count": len(overall_frame_deltas),
            "frames_meeting_deadline": overall_meeting,
            "frames_missing_deadline": overall_missing,
            "frames_meeting": overall_meeting,
            "frames_missing": overall_missing,
            "p95_frame_delta_ms": _nearest_rank_percentile(
                overall_frame_deltas, 95.0
            ),
            "p99_frame_delta_ms": _nearest_rank_percentile(
                overall_frame_deltas, 99.0
            ),
            "longest_miss_streak": max(
                int(diagnostic.get("longest_miss_streak", 0))
                for diagnostic in overall_diagnostics
            ),
            "deadline_pass": all(
                bool(diagnostic.get("deadline_pass", False))
                for diagnostic in overall_diagnostics
            ),
            "mode_count": len(overall_diagnostics),
            "reason": "raw_frame_intervals",
        }
    else:
        performance_summary = {
            "schema": PERFORMANCE_DIAGNOSTIC_SCHEMA,
            "status": "unknown",
            "performance_target_ms": PERFORMANCE_DEADLINE_MS,
            "frame_deadline_ms": PERFORMANCE_DEADLINE_MS,
            "sample_count": None,
            "frames_meeting_deadline": None,
            "frames_missing_deadline": None,
            "frames_meeting": None,
            "frames_missing": None,
            "p95_frame_delta_ms": None,
            "p99_frame_delta_ms": None,
            "longest_miss_streak": None,
            "deadline_pass": False,
            "mode_count": len(overall_diagnostics),
            "reason": "raw_frame_intervals_invalid_or_missing",
        }
    result["performance_target_ms"] = PERFORMANCE_DEADLINE_MS
    result["frame_deadline_ms"] = PERFORMANCE_DEADLINE_MS
    result["performance_summary"] = performance_summary
    for field in (
        "frames_meeting_deadline", "frames_missing_deadline", "frames_meeting",
        "frames_missing", "p95_frame_delta_ms", "p99_frame_delta_ms",
        "longest_miss_streak", "deadline_pass",
    ):
        result[field] = performance_summary.get(field)
    result["gpu_diagnostic"] = {
        "schema": PERFORMANCE_DIAGNOSTIC_SCHEMA,
        "status": gpu_status,
        "gpu_time_status": gpu_status if gpu_status == "measured" else "unknown",
        "gpu_time_ms": (
            _nearest_rank_percentile(gpu_values, 50.0)
            if gpu_status == "measured" and gpu_values
            else "unknown"
        ),
        "measured_sample_count": sum(
            int(mode.get("measured_sample_count", 0))
            for mode in mode_results
            if isinstance(mode, dict)
        ),
        "mode_statuses": statuses,
        "reason": (
            "direct_rendering_server_timer"
            if gpu_status == "measured"
            else "gpu_timer_unavailable_or_unsupported"
        ),
    }
    result["performance_pass"] = bool(mode_passes) and all(mode_passes)
    # This is a transport-continuity diagnostic, independent from receipt
    # validity and the 60 FPS result. In particular, startup drops must remain a
    # failed whole-run continuity result even when the measured window is clean.
    result["authority_queue_summary"] = _authority_queue_summary(authority_diagnostics)
    return result


def _aggregate_gpu_diagnostic(mode_rows: list[dict]) -> dict:
    """Summarize direct GPU timer status across the reported modes."""
    diagnostics = [
        row.get("gpu_diagnostic", {})
        for row in mode_rows
        if isinstance(row, dict) and isinstance(row.get("gpu_diagnostic", {}), dict)
    ]
    statuses = [str(item.get("status", "unknown")) for item in diagnostics]
    if statuses and all(status == "measured" for status in statuses):
        status = "measured"
    elif statuses and all(status == "unknown" for status in statuses):
        status = "unknown"
    elif statuses:
        status = "mixed"
    else:
        status = "unknown"
    values = [
        float(item["gpu_time_ms"])
        for item in diagnostics
        if _finite_number(item.get("gpu_time_ms"))
    ]
    return {
        "schema": PERFORMANCE_DIAGNOSTIC_SCHEMA,
        "status": status,
        "gpu_time_status": status if status == "measured" else "unknown",
        "gpu_time_ms": (
            _nearest_rank_percentile(values, 50.0)
            if status == "measured" and values
            else "unknown"
        ),
        "mode_statuses": statuses,
        "reason": (
            "direct_rendering_server_timer"
            if status == "measured"
            else "gpu_timer_unavailable_or_unsupported"
        ),
    }


def _valid_stage_timing(mode_result: dict) -> bool:
    """Fail-closed FL-4691 stage-timing receipt contract."""
    summary = mode_result.get("stage_timing_summary", {})
    if not isinstance(summary, dict):
        return False
    if not bool(summary.get("valid", False)):
        return False
    if summary.get("dominant_stage") not in FL4691_REQUIRED_STAGE_MS:
        return False
    if not _finite_number(summary.get("dominant_stage_avg_ms")):
        return False
    summary_sample_count = summary.get("sample_count")
    if (
        not isinstance(summary_sample_count, int)
        or isinstance(summary_sample_count, bool)
        or summary_sample_count <= 0
    ):
        return False
    averages = summary.get("avg_stage_ms", {})
    maxes = summary.get("max_stage_ms", {})
    if not isinstance(averages, dict) or not isinstance(maxes, dict):
        return False
    for name in FL4691_REQUIRED_STAGE_MS:
        if not _finite_number(averages.get(name)):
            return False
        if not _finite_number(maxes.get(name)):
            return False
    samples = mode_result.get("stage_timing_samples", [])
    if not isinstance(samples, list) or not samples:
        return False
    if summary_sample_count != len(samples):
        return False
    sample_values = {name: [] for name in FL4691_REQUIRED_STAGE_MS}
    for sample in samples:
        if not isinstance(sample, dict):
            return False
        if sample.get("schema") != FL4691_STAGE_TIMING_SCHEMA:
            return False
        stage_ms = sample.get("stage_ms", {})
        if not isinstance(stage_ms, dict):
            return False
        for name in FL4691_REQUIRED_STAGE_MS:
            if not _finite_number(stage_ms.get(name)):
                return False
            sample_values[name].append(float(stage_ms[name]))
    recomputed_averages = {
        name: sum(values) / float(len(values))
        for name, values in sample_values.items()
    }
    recomputed_maxes = {
        name: max(values)
        for name, values in sample_values.items()
    }
    for name in FL4691_REQUIRED_STAGE_MS:
        if not math.isclose(
            float(averages[name]),
            recomputed_averages[name],
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            return False
        if not math.isclose(
            float(maxes[name]),
            recomputed_maxes[name],
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            return False
    recomputed_dominant_stage = max(
        recomputed_averages,
        key=recomputed_averages.get,
    )
    if summary.get("dominant_stage") != recomputed_dominant_stage:
        return False
    if not math.isclose(
        float(summary["dominant_stage_avg_ms"]),
        recomputed_averages[recomputed_dominant_stage],
        rel_tol=1e-9,
        abs_tol=1e-9,
    ):
        return False
    return True


def _stage_value(summary: dict, field: str, stage: str):
    values = summary.get(field, {})
    if not isinstance(values, dict):
        return None
    value = values.get(stage)
    return float(value) if _finite_number(value) else None


def _dynamic_mover_breakdown(mode_result: dict) -> dict:
    samples = mode_result.get("stage_timing_samples", [])
    if not isinstance(samples, list):
        samples = []
    totals: dict[str, float] = {}
    maxes: dict[str, float] = {}
    errors: list[str] = []
    valid_sample_count = 0
    for sample_index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            errors.append(f"sample[{sample_index}] is not an object")
            continue
        dyn = sample.get("dynamic_mover_publication", {})
        if not isinstance(dyn, dict):
            errors.append(f"sample[{sample_index}] dynamic mover block is not an object")
            continue
        schema = dyn.get("schema")
        if schema not in ("fl4691.dynamic_mover_publication_timing.v1",
                           "fl4691.dynamic_mover_publication_timing.v2"):
            errors.append(f"sample[{sample_index}] dynamic mover schema mismatch")
            continue
        if not bool(dyn.get("valid", False)):
            errors.append(f"sample[{sample_index}] dynamic mover block is invalid")
            continue
        if dyn.get("published") is not True:
            errors.append(f"sample[{sample_index}] dynamic mover block was not published")
            continue
        if dyn.get("frame_index") != sample.get("frame_index"):
            errors.append(f"sample[{sample_index}] dynamic mover frame mismatch")
            continue
        keys = (FL4691_DYNAMIC_BREAKDOWN_V2_KEYS if schema.endswith(".v2")
                else FL4691_DYNAMIC_BREAKDOWN_KEYS)
        values = {key: dyn.get(key) for key in keys}
        invalid_keys = [
            key
            for key, value in values.items()
            if not _finite_number(value) or float(value) < 0.0
        ]
        if invalid_keys:
            errors.append(
                f"sample[{sample_index}] invalid dynamic mover fields: {','.join(invalid_keys)}"
            )
            continue
        component_total = sum(float(values[key]) for key in keys[:-1])
        if not math.isclose(
            float(values["total_ms"]),
            component_total,
            rel_tol=0.002,
            abs_tol=0.002,
        ):
            errors.append(f"sample[{sample_index}] dynamic mover total mismatch")
            continue
        stage_ms = sample.get("stage_ms", {})
        stage_total = stage_ms.get("dynamic_mover_publish_cpu_ms")
        if not _finite_number(stage_total) or not math.isclose(
            float(values["total_ms"]),
            float(stage_total),
            rel_tol=0.002,
            abs_tol=0.002,
        ):
            errors.append(f"sample[{sample_index}] dynamic mover stage mismatch")
            continue
        valid_sample_count += 1
        for key, value in values.items():
            totals[key] = totals.get(key, 0.0) + float(value)
            current_max = maxes.get(key)
            if current_max is None:
                maxes[key] = float(value)
            elif float(value) > current_max:
                maxes[key] = float(value)
    averages = {
        key: totals[key] / float(valid_sample_count)
        for key in totals
        if valid_sample_count > 0
    }
    component_averages = {
        key: averages[key]
        for key in FL4691_DYNAMIC_BREAKDOWN_V2_KEYS[:-1]
        if key in averages
    }
    dominant_component = (
        max(component_averages, key=component_averages.get)
        if component_averages
        else ""
    )
    total_avg = averages.get("total_ms")
    dominant_avg = component_averages.get(dominant_component)
    return {
        "valid": bool(samples) and valid_sample_count == len(samples) and not errors,
        "sample_count": len(samples),
        "valid_sample_count": valid_sample_count,
        "avg_ms": averages,
        "max_ms": maxes,
        "dominant_component": dominant_component,
        "dominant_component_avg_ms": dominant_avg,
        "dominant_component_share": (
            dominant_avg / total_avg
            if _finite_number(dominant_avg) and _finite_positive(total_avg)
            else None
        ),
        "errors": errors,
    }


def _suspected_owner_matrix(mode_result: dict) -> dict:
    summary = mode_result.get("stage_timing_summary", {})
    if not isinstance(summary, dict):
        summary = {}
    rows = []
    for spec in FL4691_MEASURED_CPU_STAGE_SPECS:
        stage = str(spec["stage"])
        row = {
            "owner": spec["owner"],
            "stage": stage,
            "source": spec["source"],
            "original_suspect": bool(spec["original_suspect"]),
            "avg_ms": _stage_value(summary, "avg_stage_ms", stage),
            "max_ms": _stage_value(summary, "max_stage_ms", stage),
        }
        rows.append(row)
    rows.sort(
        key=lambda row: row["avg_ms"] if _finite_number(row.get("avg_ms")) else -1.0,
        reverse=True,
    )
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    dynamic_breakdown = _dynamic_mover_breakdown(mode_result)
    measured_original_suspects = [
        spec["owner"]
        for spec in FL4691_MEASURED_CPU_STAGE_SPECS
        if bool(spec["original_suspect"])
    ]
    unmeasured_original_suspects = [
        spec["hypothesis"]
        for spec in FL4691_UNMEASURED_HYPOTHESES
        if bool(spec["original_suspect"])
    ]
    matrix_valid = (
        _valid_stage_timing(mode_result)
        and bool(rows)
        and all(_finite_number(row.get("avg_ms")) for row in rows)
        and bool(dynamic_breakdown.get("valid", False))
    )
    return {
        "schema": FL4691_SUSPECTED_OWNER_SCHEMA,
        "valid": matrix_valid,
        "measurement_scope": "cpu_callback_stage",
        "attribution_scope": "stage_bucket_not_regression_owner",
        "diagnostic_only": True,
        "gpu_execution_measured": False,
        "owners": rows,
        "dominant_measured_cpu_stage": rows[0]["owner"] if matrix_valid else "",
        "dynamic_mover_breakdown": dynamic_breakdown,
        "measured_original_suspects": measured_original_suspects,
        "unmeasured_hypotheses": [dict(spec) for spec in FL4691_UNMEASURED_HYPOTHESES],
        "unmeasured_original_suspects": unmeasured_original_suspects,
        "original_suspects": measured_original_suspects + unmeasured_original_suspects,
    }


def _num_or_none(value):
    """Propagate a finite number as float for the summary row, or None when it
    is absent/invalid — keeps the row JSON-clean without inventing a value."""
    return float(value) if _finite_number(value) else None


_GODOT_GPU_PROFILE_HEADER_RE = re.compile(
    r"^GPU PROFILE \(total ([0-9]+(?:\.[0-9]+)?)ms\):\s*$"
)
_GODOT_GPU_PROFILE_TASK_RE = re.compile(
    r"^\s*-(.+): ([0-9]+(?:\.[0-9]+)?)ms\s*$"
)


def _godot_gpu_profile_engine_args(enabled: bool) -> list[str]:
    """Return the engine-side profiler argument.

    This list is inserted before Godot's ``--`` project-argument delimiter;
    placing ``--gpu-profile`` after that delimiter silently passes it to the
    SceneTree runner instead of enabling RenderingServer profiling.
    """
    return ["--gpu-profile"] if enabled else []


def _uncapped_engine_args(enabled: bool) -> list[str]:
    """Return the engine-side arguments that remove the frame-rate ceiling.

    WHY THIS EXISTS
    ---------------
    By default the engine waits for the monitor to finish drawing before it
    starts the next frame ("vertical sync"). On a 60 Hz display that pins every
    measured frame at roughly 16.6 ms no matter how little work the frame
    actually cost. A harness run in that state reports the CEILING, not the
    cost, so two arms that differ by a fraction of a millisecond both read
    "16.6 ms" and the difference is invisible.

    That limitation is why the 2026-07-27 FL-4431 headroom audit could not use
    this harness and drove the engine by hand instead (see the audit entry in
    ``docs/plans/GODOT_GOAL.MD``). This flag folds that capability back into the
    canonical harness so the next measurement does not have to be hand-rolled
    again.

    ``--disable-vsync`` stops the wait-for-monitor behaviour and ``--max-fps 0``
    removes the engine's own optional software limiter, which would otherwise
    reimpose a ceiling. Both are stock Godot engine flags: the program being
    measured is byte-identical to the shipping one, only its pacing changes.

    These MUST be placed before Godot's ``--`` project-argument delimiter, for
    the same reason ``--gpu-profile`` must: anything after ``--`` is handed to
    the SceneTree script as a project argument and never reaches the engine.

    Parameters
    ----------
    enabled:
        True when the caller asked for an uncapped run.

    Returns
    -------
    The two engine flags when enabled, otherwise an empty list so the default
    vsync-paced behaviour is completely unchanged.
    """
    return ["--disable-vsync", "--max-fps", "0"] if enabled else []


def _canonical_presentation_user_args(
    headless: bool,
    expected_presentation_size: tuple[int, int] | None,
) -> list[str]:
    """Pass locked benchmark geometry to World's perf-surface mutator."""
    if headless:
        return []
    if expected_presentation_size is None:
        raise ValueError("headed perf run requires expected presentation size")
    width, height = expected_presentation_size
    return [f"--fl4431-canonical-presentation={width}x{height}"]


def _parse_godot_gpu_profiles(
    log_text: str,
) -> tuple[list[dict], list[str], list[float]]:
    """Parse Godot 4.6.2 native-Metal ``--gpu-profile`` stdout blocks.

    Godot emits one header plus a map of task rows roughly once each second.
    The result remains contextual attribution: it is persisted with an arm,
    never used to compute the candidate/control keep verdict.
    """
    profiles: list[dict] = []
    errors: list[str] = []
    header_totals_ms: list[float] = []
    current: dict | None = None
    for line_number, line in enumerate(log_text.splitlines(), start=1):
        header = _GODOT_GPU_PROFILE_HEADER_RE.match(line)
        if header:
            header_totals_ms.append(float(header.group(1)))
            if current is not None:
                if current["tasks"]:
                    profiles.append(current)
                else:
                    errors.append(
                        f"line_{current['line']}:gpu_profile_block_has_no_tasks"
                    )
            current = {
                "line": line_number,
                "total_gpu_ms": float(header.group(1)),
                "tasks": {},
            }
            continue
        if current is None:
            continue
        task = _GODOT_GPU_PROFILE_TASK_RE.match(line)
        if task:
            name = task.group(1).strip()
            if name in current["tasks"]:
                errors.append(f"line_{line_number}:duplicate_gpu_profile_task:{name}")
            else:
                current["tasks"][name] = float(task.group(2))
            continue
        if current["tasks"]:
            profiles.append(current)
        else:
            errors.append(f"line_{current['line']}:gpu_profile_block_has_no_tasks")
        current = None
    if current is not None:
        if current["tasks"]:
            profiles.append(current)
        else:
            errors.append(f"line_{current['line']}:gpu_profile_block_has_no_tasks")
    for profile in profiles:
        if not _finite_positive(profile.get("total_gpu_ms")):
            errors.append(f"line_{profile['line']}:invalid_gpu_profile_total")
        if any(not _finite_positive(value) for value in profile["tasks"].values()):
            errors.append(f"line_{profile['line']}:invalid_gpu_profile_task_time")
    return profiles, errors, header_totals_ms


ROOT = Path(__file__).resolve().parents[1]
RUN_DIR = ROOT / ".run" / "godot_camera_turn_perf"
GODOT_PROJECT = ROOT / "godot_project"
GODOT = os.environ.get("GODOT_BIN", "/opt/homebrew/bin/godot")
# The Colyseus multiplayer server this harness must boot for every variant.
# Since the local in-Godot GameServer was deleted (Colyseus-only authority
# mandate), Main.tscn FAIL-CLOSES at Main.gd:2648 unless launched with
# --colyseus-client against a listening server — without this, every variant
# exits receipt-less and the whole harness reads as "missing_receipt"
# (FL-4431 bit-rot found 2026-07-14 while baselining the face-scan cost).
COLYSEUS_DIR = ROOT / "colyseus_server"
# Fixed port distinct from the FL-4359 gate harnesses (2589/2596) so a perf
# run can never collide with a concurrently running proof gate.
COLYSEUS_PORT = int(os.environ.get("CAMERA_TURN_PERF_COLYSEUS_PORT", "2597"))
# The server prints this exact marker once its WebSocket transport is bound;
# waiting for it (rather than sleeping blindly) is the same readiness contract
# scripts/run_fl4359_headed_movement.py uses.
SERVER_READY_MARKER = "ASCIICKER_COLYSEUS_SPIKE_LISTENING"
SERVER_BOOT_TIMEOUT_S = 60.0
GATE_PREFIXES = ("FL4431_", "FL4408_", "FL4359_")


def _port_open(port: int) -> bool:
    """True when something is already listening on 127.0.0.1:<port>."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _git(args: list[str]) -> str:
    """Best-effort git query for receipt provenance. Returns empty string on
    failure — a provenance lookup must never abort a perf measurement run."""
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except OSError:
        return ""


def _inherited_gates() -> dict[str, str]:
    """Return gate-family values that could contaminate the baseline arm."""
    return {k: v for k, v in os.environ.items() if k.startswith(GATE_PREFIXES)}


# FL4431-SCOPED-SOURCE-GATE-20260717-01: the runtime source an AB arm actually
# reloads from disk. Only dirt UNDER these roots can make two arms execute
# different code; unrelated lanes' dirt (docs, other scripts, assets) cannot,
# so the whole-tree gate was replaced by this scoped owner after the operator
# released the whole-tree-clean precondition (unrelated lanes keep the shared
# tree perpetually dirty).
_SCOPED_SOURCE_PREFIXES = ("godot_project/", "colyseus_server/")
_SCOPED_SOURCE_FILES = ("scripts/run_godot_camera_turn_perf.py",)


def _is_scoped_source(path: str) -> bool:
    """True when a repo-relative path is renderer/harness runtime source."""
    return path.startswith(_SCOPED_SOURCE_PREFIXES) or path in _SCOPED_SOURCE_FILES


class _SourceIntegrity(NamedTuple):
    """Typed result of the scoped runtime-source integrity check.

    ok is True only when git status ran successfully (a failure/timeout/nonzero
    leaves cleanliness UNKNOWN, which must refuse just like scoped dirt).
    scoped_clean is True only when scoped working files match the active index
    and no scoped untracked path exists. The index-versus-HEAD X column is not
    contamination because matrix start independently proves the active index
    equals the selected committed runtime tree. global_dirty is informational: unrelated
    dirt exists but is ADMITTED. scoped_contamination lists refusing paths.
    """
    ok: bool
    scoped_clean: bool
    global_dirty: bool
    scoped_contamination: list[str]


def _scoped_source_integrity() -> _SourceIntegrity:
    """SINGLE owner of the AB scoped-source eligibility decision. Classifies
    a path-limited `git status --porcelain` result: scoped-source dirt refuses.
    Global dirt is informational and is sampled separately without controlling
    launch eligibility. This avoids making unrelated generated trees a timeout
    owner for the bounded runtime-source gate. Owns its own git calls (not
    `_git`, whose aggregate .strip() corrupts the first porcelain line's path)."""
    try:
        out = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                *_SCOPED_SOURCE_PREFIXES,
                *_SCOPED_SOURCE_FILES,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return _SourceIntegrity(
            ok=False, scoped_clean=False, global_dirty=False, scoped_contamination=[]
        )
    if out.returncode != 0:
        return _SourceIntegrity(
            ok=False, scoped_clean=False, global_dirty=False, scoped_contamination=[]
        )
    scoped: list[str] = []
    for line in out.stdout.splitlines():
        # "XY <path>" (path at column 3) or "XY <old> -> <new>" for a rename;
        # a rename is scoped if EITHER side lands under a scoped root.
        if len(line) <= 3:
            continue
        xy = line[:2]
        # The Y column owns worktree-vs-active-index equality. X-only changes
        # are expected when a read-only isolated index pins the selected commit while
        # the shared local branch points at a foreign lane. `??` is a real
        # scoped worktree input and remains contamination.
        if xy != "??" and xy[1] == " ":
            continue
        path = line[3:].strip()
        sides = path.split(" -> ") if " -> " in path else [path]
        if any(_is_scoped_source(side) for side in sides):
            scoped.append(path)

    # Global dirt never decides eligibility. Query tracked dirt without walking
    # untracked output trees, then inspect untracked names only when the tracked
    # tree is clean. Failure of either informational probe is conservatively
    # recorded as global_dirty=True while the scoped gate remains authoritative.
    global_dirty = False
    try:
        tracked = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        global_dirty = tracked.returncode != 0 or bool(tracked.stdout.strip())
    except (OSError, subprocess.TimeoutExpired):
        global_dirty = True
    if not global_dirty:
        try:
            untracked = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard", "--directory"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=10,
            )
            global_dirty = untracked.returncode != 0 or bool(untracked.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            global_dirty = True
    return _SourceIntegrity(
        ok=True,
        scoped_clean=(not scoped),
        global_dirty=global_dirty,
        scoped_contamination=scoped,
    )


def _current_head() -> str | None:
    """Current HEAD commit sha, or None on any git failure/timeout. Used for
    matrix-start provenance and informational branch-movement receipts."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


DEFAULT_RUNTIME_SOURCE_REF = "origin/main"


def _commit_at_ref(ref: str) -> str | None:
    """Resolve one explicit ref to an immutable commit SHA.

    The ref is passed as a subprocess argument, never through a shell. The
    ``^{commit}`` peel rejects trees, blobs, missing refs, and malformed input.
    """
    if not isinstance(ref, str) or not ref.strip():
        return None
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--verify", f"{ref.strip()}^{{commit}}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    commit = out.stdout.strip()
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        return None
    return commit


def _runtime_source_fingerprint_from_git(args: list[str]) -> str | None:
    """Normalize index/tree records into one deterministic runtime-source hash.

    `git ls-files -s` emits `<mode> <blob> <stage>\\t<path>` while `git ls-tree`
    emits `<mode> <type> <blob>\\t<path>`. Normalizing both shapes lets matrix
    start prove that the active isolated index carries exactly the selected
    committed runtime tree. Returns None on malformed,
    conflicted, empty, failed, and timed-out input.
    """
    try:
        out = subprocess.run(
            args,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    normalized: list[str] = []
    for line in out.stdout.splitlines():
        metadata, separator, path = line.partition("\t")
        fields = metadata.split()
        if not separator or not path or len(fields) != 3:
            return None
        if fields[1] in ("blob", "commit"):
            mode, _object_type, blob_sha = fields
        else:
            mode, blob_sha, stage = fields
            if stage != "0":
                return None
        normalized.append(f"{mode} {blob_sha}\t{path}")
    if not normalized:
        return None
    return hashlib.sha256("\n".join(sorted(normalized)).encode("utf-8")).hexdigest()


def _runtime_source_fingerprint() -> str | None:
    """Hash the active index runtime source an arm reloads.

    The separate scoped-clean gate establishes that working files loaded by
    Godot match these index blobs. An isolated `GIT_INDEX_FILE` therefore gives
    the matrix a reproducible source owner without touching the shared index.

    FL4431-SCOPED-FINGERPRINT-20260717-03: this replaces per-arm HEAD equality.
    In this permanently active shared repo other lanes rewrite local HEAD
    constantly; a global-HEAD abort owner wrongly killed a valid run whose
    runtime source never changed (scoped_clean=true, HEAD drifted). The
    fingerprint aborts ONLY on an actual runtime-source content change.
    """
    return _runtime_source_fingerprint_from_git(
        [
            "git", "ls-files", "-s", "--",
            "godot_project", "colyseus_server",
            "scripts/run_godot_camera_turn_perf.py",
        ]
    )


def _runtime_source_fingerprint_at_ref(ref: str) -> str | None:
    """Hash the runtime source committed at one resolved commit ref."""
    if not ref:
        return None
    return _runtime_source_fingerprint_from_git(
        [
            "git", "ls-tree", "-r", ref, "--",
            "godot_project", "colyseus_server",
            "scripts/run_godot_camera_turn_perf.py",
        ]
    )


def _runtime_worktree_fingerprint() -> str | None:
    """Hash every Git-visible runtime input exactly as the arms load it.

    This is the explicit dirty-tree diagnostic owner. Tracked files plus
    non-ignored untracked files are included; ignored import caches and runtime
    outputs are excluded by Git. A conflict, Git failure, unreadable file, and
    a file changing while it is hashed all fail closed. The result establishes
    byte stability across adjacent arms without claiming the bytes equal a
    commit.
    """
    scope = [
        "godot_project",
        "colyseus_server",
        "scripts/run_godot_camera_turn_perf.py",
    ]
    try:
        conflicts = subprocess.run(
            ["git", "ls-files", "-u", "-z", "--", *scope],
            cwd=ROOT,
            capture_output=True,
            timeout=15,
        )
        listed = subprocess.run(
            [
                "git", "ls-files", "-z", "--cached", "--others",
                "--exclude-standard", "--", *scope,
            ],
            cwd=ROOT,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if conflicts.returncode != 0 or conflicts.stdout:
        return None
    if listed.returncode != 0:
        return None
    raw_paths = [raw for raw in listed.stdout.split(b"\0") if raw]
    if not raw_paths:
        return None
    digest = hashlib.sha256()

    def _hash_path(path: Path, receipt_path: bytes) -> bool:
        digest.update(receipt_path)
        digest.update(b"\0")
        try:
            before = path.lstat()
        except FileNotFoundError:
            digest.update(b"missing\0")
            return True
        except OSError:
            return False
        digest.update(f"{before.st_mode:o}:{before.st_size}".encode("ascii"))
        digest.update(b"\0")
        file_digest = hashlib.sha256()
        try:
            if path.is_symlink():
                file_digest.update(os.fsencode(os.readlink(path)))
            elif path.is_file():
                with path.open("rb") as handle:
                    while True:
                        chunk = handle.read(1024 * 1024)
                        if not chunk:
                            break
                        file_digest.update(chunk)
            elif path.is_dir():
                # A mode-160000 gitlink appears as a directory in the parent
                file_digest.update(b"gitlink\0")
                if (path / ".git").exists():
                    # Initialized submodule: hash its own Git-visible bytes.
                    nested_conflicts = subprocess.run(
                        ["git", "ls-files", "-u", "-z"],
                        cwd=path,
                        capture_output=True,
                        timeout=15,
                    )
                    nested_list = subprocess.run(
                        [
                            "git", "ls-files", "-z", "--cached", "--others",
                            "--exclude-standard",
                        ],
                        cwd=path,
                        capture_output=True,
                        timeout=30,
                    )
                    if (
                        nested_conflicts.returncode != 0
                        or nested_conflicts.stdout
                        or nested_list.returncode != 0
                    ):
                        return False
                    nested_paths = [
                        raw for raw in nested_list.stdout.split(b"\0") if raw
                    ]
                    for nested_raw in sorted(set(nested_paths)):
                        if not _hash_path(
                            path / os.fsdecode(nested_raw),
                            receipt_path + b"/" + nested_raw,
                        ):
                            return False
                else:
                    # Uninitialized gitlink: the checkout can be an empty
                    # directory. Hash any actual files directly; do not let
                    # Git climb into the parent repository.
                    for root_dir, dir_names, file_names in os.walk(path):
                        dir_names[:] = sorted(
                            name for name in dir_names if name != ".git"
                        )
                        root_path = Path(root_dir)
                        for file_name in sorted(file_names):
                            child = root_path / file_name
                            nested_rel = child.relative_to(path)
                            nested_raw = os.fsencode(str(nested_rel))
                            if not _hash_path(
                                child,
                                receipt_path + b"/" + nested_raw,
                            ):
                                return False
            else:
                return False
            after = path.lstat()
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            return False
        if (
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ino,
        ) != (
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ):
            return False
        digest.update(file_digest.digest())
        digest.update(b"\0")
        return True

    for raw_path in sorted(set(raw_paths)):
        rel = os.fsdecode(raw_path)
        if not _is_scoped_source(rel):
            return None
        if not _hash_path(ROOT / rel, raw_path):
            return None
    return digest.hexdigest()


class _MatrixGate(NamedTuple):
    """Per-arm gate result. head is the CURRENT HEAD (for drift observation,
    not abort); fingerprint is the current runtime-source sha256."""
    ok: bool
    reason: str
    head: str | None
    fingerprint: str | None


def _matrix_source_gate(
    baseline_fingerprint: str,
    allow_stable_dirty_runtime: bool = False,
) -> _MatrixGate:
    """Per-arm gate (FL4431-SCOPED-FINGERPRINT-20260717-03): scoped source must
    be clean AND the runtime-source fingerprint must exactly equal the pinned
    baseline. HEAD equality is NO LONGER an abort condition — an unrelated HEAD
    move with an identical fingerprint is admitted (the caller records it as
    observed drift). A git failure, scoped drift, fingerprint failure, or a
    changed runtime fingerprint returns ok=False so the caller aborts with zero
    further launches. global_dirty stays admitted (informational only)."""
    integ = _scoped_source_integrity()
    if not integ.ok:
        return _MatrixGate(False, "git_failure", None, None)
    if not integ.scoped_clean and not allow_stable_dirty_runtime:
        return _MatrixGate(
            False, "scoped_drift:" + ",".join(integ.scoped_contamination), None, None
        )
    fingerprint = (
        _runtime_worktree_fingerprint()
        if allow_stable_dirty_runtime
        else _runtime_source_fingerprint()
    )
    if fingerprint is None:
        return _MatrixGate(False, "fingerprint_failure", None, None)
    head = _current_head()
    if head is None:
        return _MatrixGate(False, "git_failure_head", None, fingerprint)
    if fingerprint != baseline_fingerprint:
        return _MatrixGate(False, "runtime_source_changed", head, fingerprint)
    # Scoped source is stable. Report current HEAD for drift OBSERVATION only.
    return _MatrixGate(True, "", head, fingerprint)


def _arm_local_env(arm_env: dict[str, str]) -> dict[str, str]:
    """Scrub every runtime gate family, then apply this arm's exact values."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(GATE_PREFIXES)}
    env.update(arm_env)
    return env




def _parse_camera_origin(value: str) -> tuple[float, float, float]:
    """Parse the complete effective pixel-camera origin used by every arm."""
    parts = value.split(",")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("camera origin must be x,y,z")
    try:
        return (float(parts[0]), float(parts[1]), float(parts[2]))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("camera origin must contain three numbers") from exc


def _parse_size(value: str) -> tuple[int, int]:
    """Parse a positive WIDTHxHEIGHT receipt dimension."""
    parts = value.lower().split("x")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("size must be WIDTHxHEIGHT")
    try:
        parsed = (int(parts[0]), int(parts[1]))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("size must contain two integers") from exc
    if parsed[0] <= 0 or parsed[1] <= 0:
        raise argparse.ArgumentTypeError("size dimensions must be positive")
    return parsed


def _dimension_pair(value) -> tuple[int, int] | None:
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 2
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
        or any(item <= 0 for item in value)
    ):
        return None
    return (int(value[0]), int(value[1]))


def _apply_expected_dimension_contract(
    result: dict,
    *,
    expected_presentation_size: tuple[int, int] | None,
    expected_target_size: tuple[int, int] | None,
    expected_ansi_size: tuple[int, int] | None,
) -> None:
    """Validate every sampled compositor contract against the headed AB size.

    The top-level fields are copied into the persisted arm receipt and later
    propagated into the AB summary. Missing contracts, malformed sizes, empty
    sample sets, and any per-sample mismatch fail closed.
    """
    expected = {
        "presentation_size": expected_presentation_size,
        "target_size": expected_target_size,
        "semantic_ansi_size": expected_ansi_size,
    }
    required = any(value is not None for value in expected.values())
    result["expected_dimensions"] = {
        key: list(value) if value is not None else None
        for key, value in expected.items()
    }
    observed: dict[str, list[list[int]]] = {key: [] for key in expected}
    errors: list[str] = []
    sample_contract_count = 0
    if required and any(value is None for value in expected.values()):
        errors.append("incomplete_expected_dimensions")
    for mode_index, mode_result in enumerate(result.get("mode_results", [])):
        samples = mode_result.get("samples", [])
        if not isinstance(samples, list) or not samples:
            errors.append(f"mode_{mode_index}:missing_samples")
            continue
        for sample_index, sample in enumerate(samples):
            sample_contract_count += 1
            contract = sample.get("pixel_camera_contract", {}) if isinstance(sample, dict) else {}
            if not isinstance(contract, dict):
                errors.append(f"mode_{mode_index}:sample_{sample_index}:missing_contract")
                continue
            for key, expected_value in expected.items():
                actual = _dimension_pair(contract.get(key))
                if actual is not None and list(actual) not in observed[key]:
                    observed[key].append(list(actual))
                if required and actual is None:
                    errors.append(f"mode_{mode_index}:sample_{sample_index}:{key}:missing")
                elif required and expected_value is not None and actual != expected_value:
                    errors.append(
                        f"mode_{mode_index}:sample_{sample_index}:{key}:"
                        f"observed={actual}:expected={expected_value}"
                    )
    if required and not result.get("mode_results"):
        errors.append("missing_mode_results")
    dimension_valid = (not errors) if required else None
    result["observed_dimensions"] = observed
    result["dimension_sample_contract_count"] = sample_contract_count
    result["dimension_valid"] = dimension_valid
    result["dimension_errors"] = errors
    if dimension_valid is False:
        result["ok"] = False
        result["reason"] = "dimension_contract_mismatch:" + errors[0]


def _settled_camera_origin(result: dict) -> tuple[float, float, float] | None:
    """Return the sole valid compositor origin from a one-mode control arm."""
    if not bool(result.get("ok", False)):
        return None
    mode_results = result.get("mode_results", [])
    if len(mode_results) != 1:
        return None
    validity = mode_results[0].get("validity", {})
    origin = validity.get("settled_origin", [])
    if not bool(validity.get("ready", False)) or len(origin) != 3:
        return None
    try:
        return (float(origin[0]), float(origin[1]), float(origin[2]))
    except (TypeError, ValueError):
        return None

VARIANTS = [
    ("default", []),
    ("no_ascii", ["--no-ascii"]),
    ("no_gpu_cell_splat", ["--no-gpu-cell-splat"]),
    ("no_holland_style_post", ["--no-holland-style-post"]),
    ("no_terrain3d", ["--no-terrain3d"]),
    ("mobile_renderer", []),
]


def _start_colyseus_server(server_log: Path) -> subprocess.Popen | None:
    """Boot the Colyseus authority server and wait until it is listening.

    Returns the live server process, or None when boot failed (caller then
    reports the variant as failed instead of letting Godot fail-close with a
    confusing missing_receipt). Pattern mirrors run_fl4359_headed_movement.py.
    """
    env = dict(os.environ)
    env["COLYSEUS_PORT"] = str(COLYSEUS_PORT)
    slog = server_log.open("w")
    server = subprocess.Popen(
        # tsx runs the TypeScript server entry directly, no build step needed.
        ["node", "--import", "tsx", "src/index.ts"],
        cwd=str(COLYSEUS_DIR),
        env=env,
        stdout=slog,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + SERVER_BOOT_TIMEOUT_S
    while time.monotonic() < deadline:
        if server.poll() is not None:
            # Server crashed during boot: surface the log tail for triage.
            print(server_log.read_text()[-2000:], flush=True)
            return None
        try:
            if SERVER_READY_MARKER in server_log.read_text():
                return server
        except OSError:
            pass
        time.sleep(0.25)
    server.terminate()
    return None


def _render_failure_lines(log_text: str) -> list[str]:
    """A running SceneTree cannot certify a renderer that failed to compile."""
    patterns = (
        "compute shader compile error", "shader compilation failed",
        "SCRIPT ERROR:", "Failed to create compute pipeline",
        "VK_ERROR_INVALID_SHADER", "VK_ERROR_INITIALIZATION_FAILED",
    )
    return [line.strip()[:1000] for line in log_text.splitlines()
            if any(pattern.lower() in line.lower() for pattern in patterns)][:32]


def run_variant(
    label: str,
    extra_args: list[str],
    *,
    headless: bool,
    sample_frames: int,
    warmup_frames: int,
    perf_trace: bool = False,
    godot_gpu_profile: bool = False,
    # When true the arm runs without the monitor-refresh frame ceiling, so the
    # recorded frame time reflects actual render cost rather than the display
    # cap. See _uncapped_engine_args for why this matters (FL-4431).
    uncapped: bool = False,
    projection_modes: str = "",
    expect_camera_origin: tuple[float, float, float] | None = None,
    arm_env: dict[str, str] | None = None,
    day_cycle_time: float = 0.5,
    stationary: bool = False,
    expected_presentation_size: tuple[int, int] | None = None,
    expected_target_size: tuple[int, int] | None = None,
    expected_ansi_size: tuple[int, int] | None = None,
    stage_timing_gate: bool = False,
    keep_forever: bool = False,
) -> dict:
    try:
        run_budget = check_run_budget(ROOT, RUN_DIR, keep_forever=keep_forever)
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "label": label,
            "reason": "run_budget_check_failed",
            "run_budget_error": str(exc),
        }
    if not run_budget.allowed:
        print(
            f"[camera-turn-perf] refusing {label}: {run_budget.reason}; "
            f"usage={run_budget.usage_bytes} hard={run_budget.hard_bytes}. "
            "Prune disposable raw artifacts or pass --keep-forever explicitly.",
            file=sys.stderr,
        )
        return {
            "ok": False,
            "label": label,
            "reason": "run_budget_exceeded",
            "run_budget": run_budget.as_dict(),
        }
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    receipt = RUN_DIR / f"{label}.json"
    receipt.unlink(missing_ok=True)
    log = RUN_DIR / f"{label}.log"
    engine_args: list[str] = []
    if label == "mobile_renderer":
        engine_args = ["--rendering-method", "mobile"]
    cmd = [
        GODOT,
        *(["--headless"] if headless else []),
        *engine_args,
        *_godot_gpu_profile_engine_args(godot_gpu_profile),
        # Frame-pacing control. Empty unless --uncapped was requested, so the
        # default behaviour of every existing caller is bit-for-bit unchanged.
        *_uncapped_engine_args(uncapped),
        "--log-file",
        str(RUN_DIR / f"{label}.godot.log"),
        "--path",
        str(GODOT_PROJECT),
        "--script",
        "res://tools/profiling/run_camera_turn_perf.gd",
        "--",
        "--camera-turn-perf",
        # FL-4431: World is the single presentation-geometry mutator. The
        # runner supplies the explicit benchmark contract instead of allowing
        # current OS status-bar geometry to redefine the measured surface.
        *_canonical_presentation_user_args(headless, expected_presentation_size),
        f"--camera-turn-perf-label={label}",
        f"--camera-turn-perf-output={receipt}",
        f"--camera-turn-perf-sample-frames={sample_frames}",
        f"--camera-turn-perf-warmup-frames={warmup_frames}",
        *(["--camera-turn-perf-stage-timing-gate"] if stage_timing_gate else []),
        f"--fl4408-day-cycle-time={day_cycle_time}",
        # Colyseus-only gameplay authority: Main.tscn fail-closes without a
        # live client connection (Main.gd:2648), so every perf variant runs
        # as a real client of the server this harness booted.
        "--colyseus-client",
        f"--port={COLYSEUS_PORT}",
        "--colyseus-skip-room-leave",
        # Per-dispatch GPU profiler (FL-4431 task #24): when requested, the
        # compositor prints PERF_GPU_TRACE lines every 30 frames for the
        # bounded compositor timestamp window. The lines land in this
        # variant's .log file; nothing else in the run changes.
        *(["--perf-trace"] if perf_trace else []),
        # Optional projection filter (e.g. "perspective") so trace lines are
        # attributable to ONE mode instead of interleaving ortho+perspective.
        *([f"--camera-turn-perf-projection-modes={projection_modes}"] if projection_modes else []),
        *(["--camera-turn-perf-stationary"] if stationary else []),
        # FL-4431 pose pin: the runner refuses to sample outside the control
        # arm's complete effective pixel-camera origin envelope.
        *(
            ["--camera-turn-perf-expect-camera-origin=" + ",".join(str(v) for v in expect_camera_origin)]
            if expect_camera_origin is not None
            else []
        ),
        *extra_args,
    ]
    exact_arm_env = dict(arm_env or {})
    env = _arm_local_env(exact_arm_env)
    env.setdefault("ASCIICKER_HUD", "1")
    if _port_open(COLYSEUS_PORT):
        # A stale server (crashed prior run) would silently absorb the client
        # and poison the measurement — refuse rather than reuse.
        return {
            "ok": False,
            "label": label,
            "reason": f"port_{COLYSEUS_PORT}_already_in_use",
            "log": str(log.relative_to(ROOT)),
        }
    server = _start_colyseus_server(RUN_DIR / f"{label}.server.log")
    if server is None:
        return {
            "ok": False,
            "label": label,
            "reason": "colyseus_server_boot_failed",
            "log": str(log.relative_to(ROOT)),
        }
    timed_out = False
    returncode = 0
    try:
        with log.open("w", encoding="utf-8") as log_file:
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=ROOT,
                    env=env,
                    text=True,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    # 90s was calibrated for the serverless launch; server
                    # handshake + warmup + 240 sampled frames needs headroom.
                    timeout=240,
                )
                returncode = proc.returncode
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                returncode = -1
                log_file.write(f"\nTIMEOUT after {exc.timeout} seconds\n")
    finally:
        # Always tear the server down so the next variant boots fresh on the
        # same port (and so no orphan node process outlives the harness).
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
    if not receipt.exists():
        return {
            "ok": False,
            "label": label,
            "reason": "timeout_missing_receipt" if timed_out else "missing_receipt",
            "returncode": returncode,
            "log": str(log.relative_to(ROOT)),
        }
    data = json.loads(receipt.read_text(encoding="utf-8"))
    data["returncode"] = returncode
    data["log"] = str(log.relative_to(ROOT))
    data["run_budget"] = run_budget.as_dict()
    data["receipt"] = str(receipt.relative_to(ROOT))
    # Provenance (FL-4431 review 2026-07-15): receipts were not self-contained
    # — the A/B identity depended on renamed files and adjacent logs. Every
    # receipt now records the exact code state and the active FL4431 switches
    # so a number can be traced to a binary + configuration without archaeology.
    data["git_head"] = _git(["rev-parse", "HEAD"])
    receipt_integrity = _scoped_source_integrity()
    data["git_dirty"] = (
        not receipt_integrity.ok
        or not receipt_integrity.scoped_clean
        or receipt_integrity.global_dirty
    )
    data["env_switches"] = exact_arm_env
    # FL-4431 receipt validity (review 2026-07-15): the startup markers the
    # renderer prints for every FL4431 switch state are copied INTO the
    # receipt, so an A/B script verifies arm identity from the receipt alone
    # (marker archaeology in renamed .log files is what let an unarmed run
    # masquerade as a control arm). Workload counters get a compact digest
    # for the same reason: cross-arm draw-call-band comparison from receipts.
    log_text = ""  # guaranteed defined for the product-family route contract below
    try:
        log_text = log.read_text(encoding="utf-8", errors="replace")
        data["fl4431_log_markers"] = sorted(
            {ln.strip() for ln in log_text.splitlines() if ln.startswith("FL4431_")}
        )
    except OSError as exc:
        # FL-4691 evidence integrity: an unreadable log used to leave log_text
        # empty, which made _render_failure_lines() return [] and let the
        # renderer-failure gate below PASS. That is fail-open on the only
        # SCRIPT ERROR / shader-compile detector this arm has, and it is the
        # same class of defect the comment above records ("marker archaeology
        # in renamed .log files is what let an unarmed run masquerade as a
        # control arm"). Absence of evidence is not evidence of a clean run:
        # fail closed and say why.
        data["fl4431_log_markers"] = []
        data["arm_log_unreadable_reason"] = f"{type(exc).__name__}: {exc}"
        data["ok"] = False
        data.setdefault("reason", "arm_log_unreadable")
    data["render_failures"] = _render_failure_lines(log_text)
    if data["render_failures"]:
        data["ok"] = False
        data.setdefault("reason", "renderer_failed")
    gpu_profiles, gpu_profile_errors, gpu_profile_header_totals = (
        _parse_godot_gpu_profiles(log_text)
    )
    gpu_profile_unsupported = (
        godot_gpu_profile
        and bool(gpu_profile_header_totals)
        and not gpu_profiles
        and all(total == 0.0 for total in gpu_profile_header_totals)
    )
    gpu_profile_valid = (
        bool(gpu_profiles) and not gpu_profile_errors
        if godot_gpu_profile
        else None
    )
    # Record the frame-pacing state in the receipt itself. Without this a
    # reader cannot tell whether a ~16.6 ms figure is the real render cost or
    # simply the monitor refresh ceiling, and those two readings must never be
    # compared with one another.
    data["frame_pacing"] = {
        "uncapped": uncapped,
        "engine_args": _uncapped_engine_args(uncapped),
        "note": (
            "vsync-capped: frame time is bounded below by the display refresh"
            if not uncapped
            else "uncapped: frame time reflects render cost, not display refresh"
        ),
    }
    data["stage_timing_gate"] = stage_timing_gate
    data["godot_gpu_profile"] = {
        "enabled": godot_gpu_profile,
        "valid": gpu_profile_valid,
        "profile_count": len(gpu_profiles),
        "header_count": len(gpu_profile_header_totals),
        "header_totals_ms": gpu_profile_header_totals,
        "unsupported": gpu_profile_unsupported,
        "profiles": gpu_profiles,
        "errors": gpu_profile_errors,
    }
    if godot_gpu_profile and not gpu_profile_valid:
        data["ok"] = False
        data.setdefault(
            "reason",
            "godot_gpu_profile_unsupported"
            if gpu_profile_unsupported
            else "godot_gpu_profile_invalid",
        )
    for mode_result in data.get("mode_results", []):
        dcs = [
            s.get("perf", {}).get("draw_calls")
            for s in mode_result.get("samples", [])
            if s.get("perf", {}).get("draw_calls") is not None
        ]
        if dcs:
            mode_result["draw_call_band"] = [min(dcs), max(dcs)]
    if timed_out:
        data["ok"] = False
        data.setdefault("reason", "timeout")
    elif returncode != 0:
        data["ok"] = False
        data.setdefault("reason", f"godot_exit_{returncode}")
    # FL-4431 fail-closed backstop: even if the runner exited 0, a mode result
    # without a ready validity block is not a measurement — refuse it here so
    # older receipts (pre-hardening) can never be mixed into a new bracket.
    for mode_result in data.get("mode_results", []):
        validity = mode_result.get("validity", {})
        if not bool(validity.get("ready", False)):
            data["ok"] = False
            data.setdefault(
                "reason",
                "validity_not_ready:" + str(validity.get("reason", "missing_validity_block")),
            )
            break
        if stage_timing_gate and not _valid_stage_timing(mode_result):
            data["ok"] = False
            data.setdefault("reason", "stage_timing_gate_invalid")
            break
    _apply_expected_dimension_contract(
        data,
        expected_presentation_size=expected_presentation_size,
        expected_target_size=expected_target_size,
        expected_ansi_size=expected_ansi_size,
    )
    # Persist the same raw-interval diagnostic on each arm receipt that the
    # matrix summary exposes. This does not alter the existing ``ok`` verdict;
    # it makes receipt validity and the independent 60 FPS result explicit for
    # downstream readers that only retain the per-arm JSON.
    _annotate_performance_diagnostics(data)
    mode_receipt_valid = [
        isinstance(mode_result, dict)
        and not bool(mode_result.get("invalid", False))
        and bool(mode_result.get("validity", {}).get("ready", False))
        for mode_result in data.get("mode_results", [])
    ]
    data["receipt_valid"] = bool(data.get("ok", False)) and bool(
        mode_receipt_valid
    ) and all(mode_receipt_valid)
    data["performance_pass"] = bool(data["receipt_valid"]) and bool(
        data.get("performance_pass", False)
    )
    # Persist the augmented receipt back to disk AFTER all validation so the
    # on-disk JSON is self-contained AND carries the final ok/reason verdict —
    # provenance in the summary alone dies when the receipt file is copied or
    # renamed for archiving.
    receipt.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def summarize(results: list[dict]) -> dict:
    rows = []
    overall_frame_deltas: list[float] = []
    overall_mode_diagnostics: list[dict] = []
    overall_authority_diagnostics: list[dict] = []
    for result in results:
        mode_rows = []
        for mode_result in result.get("mode_results", []):
            validity = mode_result.get("validity", {})
            avg_fps = float(mode_result.get("avg_fps", 0.0) or 0.0)
            avg_delta_ms = float(mode_result.get("avg_delta_ms", 0.0) or 0.0)
            # FL-4431 distribution validity, tightened (FL4431-SCOPED-SOURCE-
            # GATE-20260717-01 follow-up): frame-time percentiles must be
            # strictly positive (a zero/negative median/p95 is impossible);
            # hitch_count must be an int within [0, sample_count]; hitch_rate a
            # finite fraction in [0, 1]; sample_count a positive int; and the
            # raw frame_deltas_ms array present/well-formed/length-matched.
            # Fail shut so a malformed distribution never passes as a valid arm.
            sample_count = mode_result.get("sample_count")
            sample_count_ok = (
                isinstance(sample_count, int)
                and not isinstance(sample_count, bool)
                and sample_count > 0
            )
            hitch_count = mode_result.get("hitch_count_33_333ms")
            hitch_count_ok = (
                isinstance(hitch_count, int)
                and not isinstance(hitch_count, bool)
                and hitch_count >= 0
                and sample_count_ok
                and hitch_count <= sample_count
            )
            hitch_rate = mode_result.get("hitch_rate")
            hitch_rate_ok = _finite_number(hitch_rate) and 0.0 <= float(hitch_rate) <= 1.0
            distribution_ok = (
                sample_count_ok
                and _finite_positive(mode_result.get("median_delta_ms"))
                and _finite_positive(mode_result.get("p95_delta_ms"))
                and _finite_positive(mode_result.get("p1_fps"))
                and hitch_count_ok
                and hitch_rate_ok
                and _valid_frame_deltas(
                    mode_result.get("frame_deltas_ms"), sample_count
                )
            )
            stage_timing_requested = bool(mode_result.get("stage_timing_gate", False))
            stage_timing_ok = (
                _valid_stage_timing(mode_result)
                if stage_timing_requested
                else True
            )
            stage_timing_summary = mode_result.get("stage_timing_summary", {})
            if not isinstance(stage_timing_summary, dict):
                stage_timing_summary = {}
            suspected_owner_matrix = (
                _suspected_owner_matrix(mode_result)
                if stage_timing_requested
                else {}
            )
            suspected_owner_matrix_ok = (
                bool(suspected_owner_matrix.get("valid", False))
                if stage_timing_requested
                else True
            )
            mode_ok = (
                not bool(mode_result.get("invalid", False))
                and bool(validity.get("ready", False))
                and avg_fps > 0.0
                and avg_delta_ms > 0.0
                and distribution_ok
                and stage_timing_ok
                and suspected_owner_matrix_ok
            )
            authority_diagnostic = mode_result.get("authority_queue_diagnostic")
            if not isinstance(authority_diagnostic, dict):
                authority_diagnostic = _authority_queue_diagnostic(mode_result)
            overall_authority_diagnostics.append(authority_diagnostic)
            performance_diagnostic = _frame_performance_diagnostic(mode_result)
            gpu_diagnostic = _gpu_diagnostic(mode_result)
            if performance_diagnostic.get("status") == "measured":
                overall_frame_deltas.extend(
                    float(value) for value in mode_result.get("frame_deltas_ms", [])
                )
            overall_mode_diagnostics.append(performance_diagnostic)
            # ``receipt_valid`` is the old mode ``ok`` contract under an
            # explicit name. ``performance_pass`` is deliberately separate:
            # a valid receipt may document a run that misses the 60 FPS target.
            performance_pass = bool(
                mode_ok and performance_diagnostic.get("deadline_pass", False)
            )
            mode_rows.append({
                "projection_mode": mode_result.get("projection_mode"),
                "ok": mode_ok,
                "receipt_valid": mode_ok,
                "performance_pass": performance_pass,
                "avg_fps": avg_fps,
                "min_fps": float(mode_result.get("min_fps", 0.0) or 0.0),
                "max_fps": float(mode_result.get("max_fps", 0.0) or 0.0),
                "avg_delta_ms": avg_delta_ms,
                "min_delta_ms": float(mode_result.get("min_delta_ms", 0.0) or 0.0),
                "max_delta_ms": float(mode_result.get("max_delta_ms", 0.0) or 0.0),
                # FL-4431 distribution fields (avg_delta_ms remains the mean).
                "median_delta_ms": _num_or_none(mode_result.get("median_delta_ms")),
                "p95_delta_ms": _num_or_none(mode_result.get("p95_delta_ms")),
                "p1_fps": _num_or_none(mode_result.get("p1_fps")),
                # hitch count stays a genuine int (never floated) when valid.
                "hitch_count_33_333ms": hitch_count if hitch_count_ok else None,
                "hitch_rate": _num_or_none(mode_result.get("hitch_rate")),
                "sample_count": sample_count if sample_count_ok else None,
                "stage_timing_gate": stage_timing_requested,
                "stage_timing_valid": _valid_stage_timing(mode_result)
                if stage_timing_requested
                else None,
                "stage_timing_summary": stage_timing_summary,
                "suspected_owner_matrix": suspected_owner_matrix,
                "dominant_stage": stage_timing_summary.get("dominant_stage", "")
                if stage_timing_requested
                else "",
                "dominant_stage_avg_ms": _num_or_none(
                    stage_timing_summary.get("dominant_stage_avg_ms")
                )
                if stage_timing_requested
                else None,
                "achieved_yaw_deg_per_sec": float(
                    mode_result.get("achieved_yaw_deg_per_sec", 0.0) or 0.0
                ),
                "pixel_camera_peak_excursion": float(
                    mode_result.get("pixel_camera_peak_excursion", 0.0) or 0.0
                ),
                "performance_diagnostic": performance_diagnostic,
                "authority_queue_diagnostic": authority_diagnostic,
                "performance_target_ms": PERFORMANCE_DEADLINE_MS,
                "frame_deadline_ms": PERFORMANCE_DEADLINE_MS,
                "frames_meeting_deadline": performance_diagnostic.get(
                    "frames_meeting_deadline"
                ),
                "frames_missing_deadline": performance_diagnostic.get(
                    "frames_missing_deadline"
                ),
                "frames_meeting": performance_diagnostic.get("frames_meeting"),
                "frames_missing": performance_diagnostic.get("frames_missing"),
                "p95_frame_delta_ms": performance_diagnostic.get(
                    "p95_frame_delta_ms"
                ),
                "p99_frame_delta_ms": performance_diagnostic.get(
                    "p99_frame_delta_ms"
                ),
                "longest_miss_streak": performance_diagnostic.get(
                    "longest_miss_streak"
                ),
                "deadline_pass": performance_diagnostic.get("deadline_pass", False),
                "gpu_diagnostic": gpu_diagnostic,
                "gpu_time_status": gpu_diagnostic.get("gpu_time_status", "unknown"),
                "gpu_time_ms": gpu_diagnostic.get("gpu_time_ms", "unknown"),
                "validity": validity,
            })

        overall_gpu_diagnostic = _aggregate_gpu_diagnostic(mode_rows)
        authority_summary = _authority_queue_summary(
            [
                mode.get("authority_queue_diagnostic", {})
                for mode in mode_rows
                if isinstance(mode.get("authority_queue_diagnostic", {}), dict)
            ]
        )
        row = {
            "label": result.get("label"),
            "ok": (
                bool(result.get("ok"))
                and bool(mode_rows)
                and all(mode["ok"] for mode in mode_rows)
            ),
            "receipt_valid": (
                bool(result.get("ok"))
                and bool(mode_rows)
                and all(mode["receipt_valid"] for mode in mode_rows)
            ),
            "performance_pass": (
                bool(result.get("ok"))
                and bool(mode_rows)
                and all(mode["performance_pass"] for mode in mode_rows)
            ),
            "performance_target_ms": PERFORMANCE_DEADLINE_MS,
            "frame_deadline_ms": PERFORMANCE_DEADLINE_MS,
            "gpu_diagnostic": overall_gpu_diagnostic,
            "authority_queue_summary": authority_summary,
            "mode_results": mode_rows,
            "git_head": result.get("git_head", ""),
            "git_dirty": bool(result.get("git_dirty", False)),
            "env_switches": result.get("env_switches", {}),
            "receipt": result.get("receipt"),
            "log": result.get("log"),
            "reason": result.get("reason", ""),
            "expected_dimensions": result.get("expected_dimensions", {}),
            "observed_dimensions": result.get("observed_dimensions", {}),
            "dimension_sample_contract_count": result.get(
                "dimension_sample_contract_count", 0
            ),
            "dimension_valid": result.get("dimension_valid"),
            "dimension_errors": result.get("dimension_errors", []),
            "godot_gpu_profile": result.get(
                "godot_gpu_profile",
                {
                    "enabled": False,
                    "valid": None,
                    "profile_count": 0,
                    "header_count": 0,
                    "header_totals_ms": [],
                    "unsupported": False,
                    "profiles": [],
                    "errors": [],
                },
            ),
        }
        # A one-mode invocation is the decision-grade baseline path. Mirror its
        # metrics for compact CLI output without inventing an average across
        # projection modes with different workloads.
        if len(mode_rows) == 1:
            for key in (
                "projection_mode", "avg_fps", "min_fps", "max_fps",
                "avg_delta_ms", "min_delta_ms", "max_delta_ms",
                "median_delta_ms", "p95_delta_ms", "p1_fps",
                "hitch_count_33_333ms", "hitch_rate", "sample_count",
                "stage_timing_gate", "stage_timing_valid", "dominant_stage",
                "dominant_stage_avg_ms", "stage_timing_summary",
                "suspected_owner_matrix",
                "achieved_yaw_deg_per_sec", "pixel_camera_peak_excursion",
                "receipt_valid", "performance_pass", "performance_target_ms",
                "frame_deadline_ms", "frames_meeting_deadline",
                "frames_missing_deadline", "frames_meeting", "frames_missing",
                "p95_frame_delta_ms", "p99_frame_delta_ms", "longest_miss_streak",
                "deadline_pass", "gpu_diagnostic", "gpu_time_status", "gpu_time_ms",
                "authority_queue_diagnostic",
            ):
                row[key] = mode_rows[0][key]
        rows.append(row)

    baselines = {
        mode["projection_mode"]: mode["avg_fps"]
        for row in rows if row.get("label") == "default"
        for mode in row["mode_results"] if mode["ok"]
    }
    for row in rows:
        for mode in row["mode_results"]:
            baseline_fps = baselines.get(mode["projection_mode"], 0.0)
            mode["fps_vs_default"] = (
                mode["avg_fps"] / baseline_fps if baseline_fps > 0.0 else None
            )
        if len(row["mode_results"]) == 1:
            row["fps_vs_default"] = row["mode_results"][0]["fps_vs_default"]
    receipt_valid = bool(rows) and all(row["receipt_valid"] for row in rows)
    performance_pass = bool(rows) and all(row["performance_pass"] for row in rows)
    overall_raw_valid = bool(overall_mode_diagnostics) and all(
        diagnostic.get("status") == "measured"
        for diagnostic in overall_mode_diagnostics
    )
    if overall_raw_valid and overall_frame_deltas:
        overall_meeting = sum(
            value <= PERFORMANCE_DEADLINE_MS for value in overall_frame_deltas
        )
        overall_missing = len(overall_frame_deltas) - overall_meeting
        overall_performance = {
            "schema": PERFORMANCE_DIAGNOSTIC_SCHEMA,
            "status": "measured",
            "performance_target_ms": PERFORMANCE_DEADLINE_MS,
            "frame_deadline_ms": PERFORMANCE_DEADLINE_MS,
            "sample_count": len(overall_frame_deltas),
            "frames_meeting_deadline": overall_meeting,
            "frames_missing_deadline": overall_missing,
            "frames_meeting": overall_meeting,
            "frames_missing": overall_missing,
            "p95_frame_delta_ms": _nearest_rank_percentile(
                overall_frame_deltas, 95.0
            ),
            "p99_frame_delta_ms": _nearest_rank_percentile(
                overall_frame_deltas, 99.0
            ),
            # Mode boundaries are separate experiments; do not manufacture a
            # miss streak by joining the last frame of one mode to the first of
            # another. The overall streak is the worst mode streak.
            "longest_miss_streak": max(
                int(diagnostic.get("longest_miss_streak", 0))
                for diagnostic in overall_mode_diagnostics
            ),
            "deadline_pass": all(
                bool(diagnostic.get("deadline_pass", False))
                for diagnostic in overall_mode_diagnostics
            ),
            "mode_count": len(overall_mode_diagnostics),
            "reason": "raw_frame_intervals",
        }
    else:
        overall_performance = {
            "schema": PERFORMANCE_DIAGNOSTIC_SCHEMA,
            "status": "unknown",
            "performance_target_ms": PERFORMANCE_DEADLINE_MS,
            "frame_deadline_ms": PERFORMANCE_DEADLINE_MS,
            "sample_count": None,
            "frames_meeting_deadline": None,
            "frames_missing_deadline": None,
            "frames_meeting": None,
            "frames_missing": None,
            "p95_frame_delta_ms": None,
            "p99_frame_delta_ms": None,
            "longest_miss_streak": None,
            "deadline_pass": False,
            "mode_count": len(overall_mode_diagnostics),
            "reason": "raw_frame_intervals_invalid_or_missing",
        }
    return {
        # Existing ``ok`` remains the receipt-integrity verdict. Consumers that
        # need the 60 FPS decision must use the explicit performance_pass key.
        "ok": bool(rows) and all(row["ok"] for row in rows),
        "receipt_valid": receipt_valid,
        "performance_pass": performance_pass,
        "performance_target_ms": PERFORMANCE_DEADLINE_MS,
        "frame_deadline_ms": PERFORMANCE_DEADLINE_MS,
        "performance_summary": overall_performance,
        "frames_meeting_deadline": overall_performance.get(
            "frames_meeting_deadline"
        ),
        "frames_missing_deadline": overall_performance.get(
            "frames_missing_deadline"
        ),
        "frames_meeting": overall_performance.get("frames_meeting"),
        "frames_missing": overall_performance.get("frames_missing"),
        "p95_frame_delta_ms": overall_performance.get("p95_frame_delta_ms"),
        "p99_frame_delta_ms": overall_performance.get("p99_frame_delta_ms"),
        "longest_miss_streak": overall_performance.get("longest_miss_streak"),
        "deadline_pass": overall_performance.get("deadline_pass", False),
        "authority_queue_summary": _authority_queue_summary(
            overall_authority_diagnostics
        ),
        "gpu_diagnostic": _aggregate_gpu_diagnostic(
            [
                mode
                for row in rows
                for mode in row.get("mode_results", [])
                if isinstance(mode, dict)
            ]
        ),
        "results": rows,
    }


def _arm_launch_environment_failure(result: dict) -> bool:
    """True when an arm died to the LAUNCH ENVIRONMENT, not the renderer.

    FL-4431 measurement learnings (2026-07-17): on macOS, launching a
    fullscreen arm while a previous fullscreen window is still animating out
    yields a never-drawn window (frame_index frozen -> readiness_timeout), and
    an arm that settles at a transient drop-in pose fails the origin pin
    (pose_mismatch_timeout). Both failure modes carry ZERO samples, so a
    single relaunch is bias-free — nothing was measured that could be
    selectively retried. A fullscreen dimension mismatch is also a launch-
    environment failure: macOS can place a nominally fullscreen window at the
    smaller 1280x800 surface. It receives the same single relaunch; a repeated
    mismatch remains invalid and never contributes timing."""
    if result.get("dimension_valid") is False:
        return True
    for mode_result in result.get("mode_results", []):
        reason = str(mode_result.get("validity", {}).get("reason", ""))
        if reason.startswith(("readiness_timeout", "pose_mismatch_timeout")):
            return True
    return False


def _ab_arm_measurement_ms(result: dict) -> float | None:
    """Return an arm's frame-time measurement ONLY when the arm is fully valid.

    Fail-closed rule (review FL4431-PRODUCT-AB-RESTART-20260717-03): a failed
    arm's receipt carries numeric 0.0 in avg_delta_ms, and 0.0 entering a
    paired delta silently manufactures a huge fake gain/loss. An arm counts
    ONLY when: the arm-level ok flag is true, there is exactly ONE mode result
    (the ABAB contract pins one projection mode), that mode is not marked
    invalid, its validity row is ready, and the measurement is a real number.
    Anything else returns None and the pair is discarded, never averaged."""
    if not bool(result.get("ok", False)):
        return None
    mode_results = result.get("mode_results", [])
    if len(mode_results) != 1:
        return None
    mode = mode_results[0]
    if bool(mode.get("invalid", False)):
        return None
    if not bool(mode.get("validity", {}).get("ready", False)):
        return None
    avg = mode.get("avg_delta_ms")
    if not isinstance(avg, (int, float)) or isinstance(avg, bool):
        return None
    return float(avg)


def _ab_paired_deltas(
    results: list, brackets: int, slots: list | None = None
) -> list:
    """Control-minus-candidate delta per bracket; None unless BOTH arms valid.

    ``slots`` maps each bracket to the positions of its candidate and control
    arms. Counterbalanced runs can launch control first, so positional pairing
    would otherwise invert the sign of every second bracket.
    """
    paired = []
    for i in range(brackets):
        cand_index, ctrl_index = (
            slots[i] if slots is not None else (i * 2, i * 2 + 1)
        )
        cand_ms = (
            _ab_arm_measurement_ms(results[cand_index])
            if cand_index < len(results)
            else None
        )
        ctrl_ms = (
            _ab_arm_measurement_ms(results[ctrl_index])
            if ctrl_index < len(results)
            else None
        )
        paired.append(ctrl_ms - cand_ms if cand_ms is not None and ctrl_ms is not None else None)
    return paired


# FL-4712 A=A admissibility ceiling, in milliseconds of mean paired delta.
# An A=A bracket launches the SAME arm twice, so the true difference is zero by
# construction and every observed paired delta is machine noise plus launch-
# ORDER bias. A mean whose magnitude stays under this ceiling is treated as "the
# harness did not move"; anything larger is a systematic offset the paired
# protocol failed to cancel. The value sits well below the 0.3-1.4 ms single-arm
# noise band recorded for this machine (see run_ab_brackets), so a genuine
# one-directional lever cannot hide underneath it.
AA_NOISE_MEAN_TOLERANCE_MS = 0.25


def _aa_arms_identical(arms: list, slots: list) -> bool:
    """True only when BOTH arms of every bracket carry identical env AND args.

    This is the DEFINING property of the A=A control: the two arms must be the
    same experiment run twice. It is checked on the fully built arm list rather
    than trusted from the construction site, so a future edit that reintroduces
    a per-arm switch is caught before any process is launched.
    """
    for first_index, second_index in slots:
        if first_index >= len(arms) or second_index >= len(arms):
            return False
        _first_label, first_env, first_args = arms[first_index]
        _second_label, second_env, second_args = arms[second_index]
        if dict(first_env) != dict(second_env):
            return False
        if list(first_args) != list(second_args):
            return False
    return True


def _aa_noise_control_verdict(
    paired: list, tolerance_ms: float = AA_NOISE_MEAN_TOLERANCE_MS
) -> str:
    """Name the A=A outcome explicitly instead of leaving it to the reader.

    Returns exactly one of:

    ``insufficient_pairs`` — fewer than two valid pairs survived, so neither
    sign mixing nor a mean is judgeable. Invalid pairs arrive here as None from
    ``_ab_paired_deltas`` and are never counted.

    ``noise_only`` — the deltas are NOT all one direction AND the mean magnitude
    is within ``tolerance_ms``. Both halves are required: mixed signs alone can
    still hide a biased mean, and a small mean alone can still be a consistent
    one-directional offset.

    ``directional_bias`` — everything else. This is the fail-closed answer: a
    real A/B-shaped effect (all deltas the same sign, or a mean past the
    ceiling) must never be reported as A=A noise.
    """
    valid = [
        value
        for value in paired
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if len(valid) < 2:
        return "insufficient_pairs"
    one_directional = all(value > 0 for value in valid) or all(
        value < 0 for value in valid
    )
    mean = sum(valid) / len(valid)
    if not one_directional and abs(mean) <= tolerance_ms:
        return "noise_only"
    return "directional_bias"


def run_ab_brackets(args: argparse.Namespace) -> int:
    """Same-binary alternating candidate/control brackets for one env switch.

    The candidate arm runs env-clean (the default build path); the control arm
    runs with the single NAME=VALUE from --ab-control-env. Arms alternate
    candidate/control N times (ABAB...) so slow thermal/order drift cancels in
    the PAIRED deltas instead of biasing one side, which is the only way a
    sub-noise-floor lever can be judged honestly (single-arm noise is
    0.3-1.4 ms on this machine, plus ~2 ms slow drift across a sweep).

    FL-4712 A=A: with --aa-control the SAME arm is launched on both sides of
    every bracket (identical env, identical arguments), so the true difference
    is zero and the surviving paired deltas are the harness's own noise plus any
    launch-order bias the counterbalancing did not cancel. Every gate below —
    scoped cleanliness, the runtime-source fingerprint around each arm, the
    single relaunch for zero-sample launch failures, and the fail-closed
    per-arm validity rule — applies unchanged; only the arm inputs, labels, and
    summary filename differ.

    Eligibility defaults to committed-source mode: scoped working bytes must
    match the selected commit. The explicit stable-dirty mode instead hashes
    every Git-visible runtime input as loaded from the working tree, then
    requires that fingerprint to remain identical around every arm. It is a
    diagnostic provenance class and never claims commit identity.
    """
    expected_dimensions = (
        args.expect_presentation_size,
        args.expect_target_size,
        args.expect_ansi_size,
    )
    if not args.headless and any(value is None for value in expected_dimensions):
        print(
            "headed --ab-brackets requires --expect-presentation-size, "
            "--expect-target-size, and --expect-ansi-size",
            file=sys.stderr,
        )
        return 2
    start = _scoped_source_integrity()
    if not start.ok:
        print(
            "--ab-brackets refuses: could not read git status (failed, timed "
            "out, or exited nonzero). Repair git, then retry.",
            file=sys.stderr,
        )
        return 2
    allow_stable_dirty_runtime = bool(
        getattr(args, "allow_stable_dirty_runtime", False)
    )
    if not start.scoped_clean and not allow_stable_dirty_runtime:
        print(
            "--ab-brackets refuses: scoped runtime source is dirty (each arm "
            "reloads it from disk, so uncommitted changes contaminate the "
            "comparison). Commit these paths, then retry:",
            file=sys.stderr,
        )
        for path in start.scoped_contamination:
            print(f"  {path}", file=sys.stderr)
        return 2
    if not start.scoped_clean:
        print(
            "--ab-brackets: scoped dirt admitted under explicit stable-"
            "worktree mode; every Git-visible runtime byte is fingerprinted "
            "around every arm.",
            flush=True,
        )
    if start.global_dirty:
        # Admitted, not a refusal: unrelated lanes cannot change what an arm
        # loads. Reported so the receipt records the tree was not globally clean.
        print(
            "--ab-brackets: unrelated (non-scoped) global dirt present and "
            "ADMITTED; scoped runtime source is clean.",
            flush=True,
        )
    baseline_head = _current_head()
    if baseline_head is None:
        print("--ab-brackets refuses: could not resolve HEAD to pin.", file=sys.stderr)
        return 2
    runtime_source_ref = ""
    runtime_source_commit: str | None = None
    committed_fingerprint: str | None = None
    if allow_stable_dirty_runtime:
        baseline_fingerprint = _runtime_worktree_fingerprint()
    else:
        # Matrix-start provenance: one explicit committed ref owns the runtime
        # bytes. Local HEAD belongs to concurrent lanes and remains receipt
        # metadata only.
        runtime_source_ref = str(args.runtime_source_ref).strip()
        runtime_source_commit = _commit_at_ref(runtime_source_ref)
        if runtime_source_commit is None:
            print(
                "--ab-brackets refuses: --runtime-source-ref did not resolve "
                "to an immutable commit.",
                file=sys.stderr,
            )
            return 2
        baseline_fingerprint = _runtime_source_fingerprint()
    if baseline_fingerprint is None:
        print(
            "--ab-brackets refuses: could not fingerprint the selected "
            "runtime source.",
            file=sys.stderr,
        )
        return 2
    if not allow_stable_dirty_runtime:
        committed_fingerprint = _runtime_source_fingerprint_at_ref(
            runtime_source_commit or ""
        )
        if committed_fingerprint is None:
            print(
                "--ab-brackets refuses: could not fingerprint the committed "
                "runtime source ref.",
                file=sys.stderr,
            )
            return 2
        if baseline_fingerprint != committed_fingerprint:
            print(
                "--ab-brackets refuses: active-index runtime source does not "
                "match --runtime-source-ref.",
                file=sys.stderr,
            )
            return 2
    # FL-4712 A=A mode. Read defensively: several sibling contract tests build a
    # partial args namespace, and an absent flag must mean "ordinary A/B".
    aa_control = bool(getattr(args, "aa_control", False))
    name, sep, value = args.ab_control_env.partition("=")
    if aa_control:
        # In A=A there is no switch to express. A per-arm env or a per-arm
        # argument would make the two arms different experiments and destroy the
        # only property this mode exists to hold, so the request is REFUSED
        # rather than silently ignored.
        if args.ab_control_env or str(getattr(args, "ab_control_args", "") or ""):
            print(
                "--aa-control refuses: --ab-control-env/--ab-control-args would "
                "make the two arms differ; an A=A control requires identical arms",
                file=sys.stderr,
            )
            return 2
    elif not sep or not name:
        print("--ab-control-env must be NAME=VALUE", file=sys.stderr)
        return 2
    if args.expect_camera_origin is None:
        # An unpinned first arm settles at a transient drop-in pose and then
        # poisons every later arm's pin — the pin is mandatory here.
        print("--ab-brackets requires --expect-camera-origin (canonical pose pin)", file=sys.stderr)
        return 2
    shared_env: dict[str, str] = {}
    if args.ab_shared_env:
        s_name, s_sep, s_value = args.ab_shared_env.partition("=")
        if not s_sep or not s_name:
            print("--ab-shared-env must be NAME=VALUE", file=sys.stderr)
            return 2
        shared_env[s_name] = s_value
    control_extra_args: list[str] = shlex.split(
        str(getattr(args, "ab_control_args", "") or "")
    )
    alternate_order = bool(getattr(args, "ab_alternate_order", False))
    arms: list[tuple[str, dict[str, str], list[str]]] = []
    bracket_slots: list[tuple[int, int]] = []
    for i in range(args.ab_brackets):
        if aa_control:
            # A=A: ONE env dict and ONE argument list are built per bracket and
            # copied into both arms, so no code path exists that could hand them
            # different launch inputs. The aa_ label prefix also keeps the
            # per-label receipt filename (RUN_DIR/<label>.json, which is derived
            # from the label alone) disjoint from the ab_candidate_/ab_control_
            # receipts a feature matrix writes, so an A=A run cannot overwrite a
            # feature matrix's evidence in the same .run directory.
            aa_arm_env = dict(shared_env)
            aa_arm_args: list[str] = []
            candidate_arm = (
                f"aa_arm_a_b{i + 1:02d}", dict(aa_arm_env), list(aa_arm_args)
            )
            control_arm = (
                f"aa_arm_b_b{i + 1:02d}", dict(aa_arm_env), list(aa_arm_args)
            )
        else:
            candidate_arm = (f"ab_candidate_b{i + 1:02d}", dict(shared_env), [])
            control_arm = (
                f"ab_control_b{i + 1:02d}",
                {**shared_env, name: value},
                list(control_extra_args),
            )
        first_index = len(arms)
        if alternate_order and i % 2 == 1:
            arms.append(control_arm)
            arms.append(candidate_arm)
            bracket_slots.append((first_index + 1, first_index))
        else:
            arms.append(candidate_arm)
            arms.append(control_arm)
            bracket_slots.append((first_index, first_index + 1))
    if aa_control and not _aa_arms_identical(arms, bracket_slots):
        # Fail before launching anything: an A=A run whose arms differ is not an
        # A=A run, and its deltas would silently be read as a noise floor.
        print(
            "--aa-control refuses: the two arms of a bracket did not receive "
            "identical environment and identical arguments",
            file=sys.stderr,
        )
        return 2
    results = []
    aborted = False
    abort_reason = ""
    head_drift_observed = False
    final_head = baseline_head
    final_fingerprint = baseline_fingerprint

    def _observe(gate: _MatrixGate) -> None:
        # An admitted arm may sit on a drifted HEAD (unrelated lane commit) so
        # long as the runtime fingerprint held. Record the drift; never abort.
        nonlocal head_drift_observed, final_head, final_fingerprint
        if gate.head is not None:
            final_head = gate.head
            if gate.head != baseline_head:
                head_drift_observed = True
        if gate.fingerprint is not None:
            final_fingerprint = gate.fingerprint

    for label, arm_env, arm_extra_args in arms:
        if aborted:
            break
        for attempt in (1, 2):
            # Recheck scoped cleanliness + runtime fingerprint immediately
            # BEFORE every launch (relaunches too). HEAD equality is NOT checked
            # — only an actual runtime-source change aborts, with zero further
            # launches.
            gate = _matrix_source_gate(
                baseline_fingerprint,
                allow_stable_dirty_runtime=allow_stable_dirty_runtime,
            )
            _observe(gate)
            if not gate.ok:
                aborted = True
                abort_reason = f"pre_arm:{label}:attempt{attempt}:{gate.reason}"
                print(f"ABORT before {label} (attempt {attempt}): {gate.reason}", file=sys.stderr)
                break
            # Settle before EVERY fullscreen launch: macOS needs the previous
            # fullscreen space to finish animating out or the new window is
            # never drawn (see _arm_launch_environment_failure).
            if not args.headless:
                time.sleep(8.0)
            result = run_variant(
                label,
                arm_extra_args,
                headless=args.headless,
                sample_frames=args.sample_frames,
                warmup_frames=args.warmup_frames,
                perf_trace=args.perf_trace,
                godot_gpu_profile=args.godot_gpu_profile,
                # Read defensively: several contract tests build a partial
                # args namespace, and an absent flag must mean "keep the
                # default vsync-paced behaviour", never an AttributeError.
                uncapped=getattr(args, "uncapped", False),
                projection_modes=args.projection_modes,
                expect_camera_origin=args.expect_camera_origin,
                arm_env=arm_env,
                day_cycle_time=args.day_cycle_time,
                stationary=args.stationary,
                expected_presentation_size=args.expect_presentation_size,
                expected_target_size=args.expect_target_size,
                expected_ansi_size=args.expect_ansi_size,
                stage_timing_gate=args.stage_timing_gate,
                keep_forever=getattr(args, "keep_forever", False),
            )
            # Recheck immediately AFTER the arm: the runtime source must not have
            # changed DURING the arm, or the just-collected sample is suspect.
            gate = _matrix_source_gate(
                baseline_fingerprint,
                allow_stable_dirty_runtime=allow_stable_dirty_runtime,
            )
            _observe(gate)
            if not gate.ok:
                aborted = True
                abort_reason = f"post_arm:{label}:attempt{attempt}:{gate.reason}"
                print(f"ABORT after {label} (attempt {attempt}): {gate.reason}", file=sys.stderr)
                break
            if attempt == 1 and _arm_launch_environment_failure(result):
                print(f"{label}: launch-environment failure (zero samples) — one relaunch", flush=True)
                continue
            break
        if aborted:
            break
        results.append(result)
    summary = summarize(results)
    # Paired per-bracket deltas: control minus candidate, so a POSITIVE delta
    # means the candidate (default build path) is FASTER than the control.
    # Fail-closed: a pair contributes only when BOTH arms are fully valid
    # (see _ab_arm_measurement_ms) — failed arms report numeric 0.0 which must
    # never reach the mean.
    paired = _ab_paired_deltas(results, args.ab_brackets, bracket_slots)
    valid_pairs = [p for p in paired if isinstance(p, (int, float))]
    summary["ab_control_env"] = args.ab_control_env
    summary["ab_shared_env"] = args.ab_shared_env
    summary["ab_control_args"] = str(
        getattr(args, "ab_control_args", "") or ""
    )
    summary["ab_alternate_order"] = alternate_order
    summary["ab_arm_order"] = [label for label, _env, _extra in arms]
    # Pair slots are LAUNCH-POSITION indices into ab_arm_order, written as
    # (first-role, second-role). Counterbalancing swaps which role launches
    # first on alternating brackets, so it is the slots — not the positions —
    # that keep every persisted delta oriented second-role-minus-first-role.
    summary["ab_pair_slots"] = [list(slot) for slot in bracket_slots]
    summary["ab_valid_pair_count"] = len(valid_pairs)
    # FL-4712 A=A control. The verdict is an explicit named field so a reader
    # never has to infer a noise result from the delta list; it is None outside
    # A=A mode because an A/B matrix has a real switch and no noise claim.
    summary["aa_control"] = aa_control
    summary["aa_noise_mean_tolerance_ms"] = AA_NOISE_MEAN_TOLERANCE_MS
    summary["aa_noise_control_verdict"] = (
        _aa_noise_control_verdict(paired) if aa_control else None
    )
    # Persist the bracket count so a downstream HUD consumer can require the
    # summary to carry exactly 2*ab_brackets results in the exact candidate/
    # control order (the anti-truncation / anti-reorder gate).
    summary["ab_brackets"] = args.ab_brackets
    summary["ab_paired_control_minus_candidate_ms"] = paired
    summary["ab_mean_control_minus_candidate_ms"] = (
        sum(valid_pairs) / len(valid_pairs) if valid_pairs else None
    )
    summary["headless"] = args.headless
    summary["sample_frames"] = args.sample_frames
    summary["warmup_frames"] = args.warmup_frames
    summary["timing_verdict_eligible"] = not (
        args.perf_trace or args.godot_gpu_profile
    )
    summary["stage_timing_gate"] = args.stage_timing_gate
    summary["day_cycle_time"] = args.day_cycle_time
    summary["motion_mode"] = "stationary" if args.stationary else "camera_turn"
    summary["control_camera_origin"] = args.expect_camera_origin
    summary["expected_dimensions"] = {
        "presentation_size": (
            list(args.expect_presentation_size)
            if args.expect_presentation_size is not None else None
        ),
        "target_size": (
            list(args.expect_target_size)
            if args.expect_target_size is not None else None
        ),
        "semantic_ansi_size": (
            list(args.expect_ansi_size)
            if args.expect_ansi_size is not None else None
        ),
    }
    summary["observed_dimensions_by_arm"] = {
        str(result.get("label", f"arm_{index}")): result.get("observed_dimensions", {})
        for index, result in enumerate(results)
    }
    summary["dimension_valid"] = (
        bool(results)
        and all(result.get("dimension_valid") is True for result in results)
        if any(value is not None for value in expected_dimensions)
        else None
    )
    if summary["dimension_valid"] is False:
        summary["ok"] = False
    # FL4431-SCOPED-SOURCE-GATE provenance: pinned baseline HEAD, whether
    # unrelated global dirt was admitted, the scoped-source clean state and its
    # contamination paths at matrix start, and any mid-matrix abort. An aborted
    # matrix is never ok, regardless of how many arms happened to run first.
    summary["baseline_head"] = baseline_head
    summary["final_head"] = final_head
    summary["runtime_source_ref"] = runtime_source_ref
    summary["pinned_runtime_source_commit"] = runtime_source_commit
    summary["runtime_source_ref_kind"] = (
        "stable_worktree"
        if allow_stable_dirty_runtime
        else (
            "remote_tracking"
            if runtime_source_ref == DEFAULT_RUNTIME_SOURCE_REF
            else "local_committed"
        )
    )
    # Unrelated HEAD movement is NOT contamination when the runtime fingerprint
    # held — record it so a reader knows the branch pointer drifted harmlessly.
    summary["head_drift_observed"] = head_drift_observed
    summary["baseline_runtime_source_sha256"] = baseline_fingerprint
    summary["final_runtime_source_sha256"] = final_fingerprint
    summary["committed_runtime_source_sha256"] = committed_fingerprint
    summary["stable_dirty_runtime"] = allow_stable_dirty_runtime
    # runtime_source_stable is True only when the fingerprint held for every arm
    # (i.e. no runtime-source-change abort). A scoped-dirt/git/fingerprint abort
    # leaves it False.
    summary["runtime_source_stable"] = (
        not aborted and final_fingerprint == baseline_fingerprint
    )
    summary["global_dirty"] = bool(start.global_dirty)
    summary["scoped_clean"] = bool(start.scoped_clean)
    summary["scoped_contamination"] = start.scoped_contamination
    summary["aborted"] = aborted
    summary["abort_reason"] = abort_reason
    if aborted:
        summary["ok"] = False
    # Separate summary owner per mode: an A=A run must not overwrite the
    # ab_summary.json a feature matrix left in the same .run directory.
    summary_path = RUN_DIR / ("aa_summary.json" if aa_control else "ab_summary.json")
    # Write the (possibly contaminated) summary to disk BEFORE returning, so an
    # aborted matrix still leaves a durable receipt for triage.
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"summary={summary_path.relative_to(ROOT)}")
    # Exit-code contract: a mid-matrix source-integrity/fingerprint abort is a
    # CONTAMINATION signal -> exit 2 (same class as the start-gate refusals). A
    # fully executed matrix whose measurement validity merely fails -> exit 1.
    if aborted:
        return 2
    return 0 if summary["ok"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Godot with --headless. Useful for CI/sandbox smoke, but less faithful for GPU compositor performance.",
    )
    parser.add_argument("--sample-frames", type=int, default=240)
    parser.add_argument("--warmup-frames", type=int, default=90)
    parser.add_argument(
        "--variants",
        default="all",
        help="Comma-separated variant labels to run (default: all). "
        "Example: --variants default  — one headed run for a quick baseline "
        "instead of the full six-variant isolation sweep.",
    )
    parser.add_argument(
        "--perf-trace",
        action="store_true",
        help="Enable the compositor's per-dispatch GPU profiler (PERF_GPU_TRACE "
        "lines in the variant log) — FL-4431 per-pass cost ranking.",
    )
    parser.add_argument(
        "--godot-gpu-profile",
        action="store_true",
        help="Enable Godot's native whole-frame --gpu-profile task report and "
        "persist every parsed block in each arm receipt. Contextual attribution "
        "only; both AB arms receive the same profiler state.",
    )
    parser.add_argument(
        "--uncapped",
        action="store_true",
        help="Remove the frame-rate ceiling for every arm via the stock engine "
        "flags --disable-vsync --max-fps 0. Without this the harness is paced "
        "by the display refresh and every headed arm reports roughly 16.6 ms, "
        "which is the CEILING and not the render cost, so sub-millisecond "
        "differences between arms are invisible. Required whenever an A/B is "
        "meant to resolve a cost change (FL-4431). Never compare a capped "
        "reading with an uncapped one; each receipt records which it was under "
        "the 'frame_pacing' key.",
    )
    parser.add_argument(
        "--stage-timing-gate",
        action="store_true",
        help="Require FL-4691 compositor stage timing receipts in every sampled "
        "mode and expose the dominant CPU stage in the summary.",
    )
    parser.add_argument(
        "--projection-modes",
        default="",
        help="Comma-separated projection modes for the in-Godot runner "
        "(e.g. 'perspective'); empty = runner default (ortho+perspective).",
    )
    parser.add_argument(
        "--expect-camera-origin",
        type=_parse_camera_origin,
        default=None,
        help="FL-4431 receipt validity: pin the complete effective pixel-camera "
        "origin as x,y,z. Without an explicit pin, the first control arm owns "
        "the pin for every later arm in this invocation.",
    )
    parser.add_argument(
        "--expect-presentation-size",
        type=_parse_size,
        default=None,
        help="Required for headed AB matrices; canonical value is 3600x2252.",
    )
    parser.add_argument(
        "--expect-target-size",
        type=_parse_size,
        default=None,
        help="Required for headed AB matrices; canonical value is 3600x2260.",
    )
    parser.add_argument(
        "--expect-ansi-size",
        type=_parse_size,
        default=None,
        help="Required for headed AB matrices; canonical value is 180x113.",
    )
    parser.add_argument(
        "--stationary",
        action="store_true",
        help="Measure a settled fixed camera instead of the camera-turn workload.",
    )
    parser.add_argument(
        "--disable-eye-reuse-control",
        action="store_true",
        help="Run the same-binary stationary control with EYE capture reuse disabled.",
    )
    parser.add_argument(
        "--ab-control-env",
        default="",
        help="NAME=VALUE applied to the CONTROL arms of --ab-brackets "
        "(candidate arms run env-clean). Example: FL4431_DISABLE_POSRECON=1",
    )
    parser.add_argument(
        "--ab-shared-env",
        default="",
        help="NAME=VALUE applied to BOTH candidate and control arms of "
        "--ab-brackets (e.g. a diagnostic receipt gate like "
        "FL4431_CULL_COUNT_RECEIPT=1 whose cost must cancel in paired deltas).",
    )
    parser.add_argument(
        "--ab-brackets",
        type=int,
        default=0,
        help="Run N alternating candidate/control bracket pairs for one "
        "same-binary env switch (FL-4431 ABAB protocol: requires "
        "--ab-control-env, or --aa-control for the FL-4712 A=A noise control, "
        "plus --expect-camera-origin; ~8s settle precedes "
        "every fullscreen arm; a zero-sample readiness/pose-timeout arm is "
        "relaunched once).",
    )
    parser.add_argument(
        "--ab-control-args",
        default="",
        help="Extra command-line user arguments applied to control arms as "
        "one shell-quoted string. Candidate arms receive none.",
    )
    parser.add_argument(
        "--aa-control",
        action="store_true",
        help="FL-4712 noise control: run --ab-brackets as A=A. Both arms of "
        "every bracket receive IDENTICAL environment and IDENTICAL arguments, "
        "so the true difference is zero and the paired deltas measure only "
        "machine noise plus launch-order bias. Refuses --ab-control-env and "
        "--ab-control-args (they would make the arms differ). Writes aa_arm_a_/"
        "aa_arm_b_ receipts and aa_summary.json, and reports an explicit "
        "aa_noise_control_verdict.",
    )
    parser.add_argument(
        "--ab-alternate-order",
        action="store_true",
        help="Counterbalance launch order so every second bracket starts with "
        "the control arm.",
    )
    parser.add_argument(
        "--runtime-source-ref",
        default=DEFAULT_RUNTIME_SOURCE_REF,
        help="Committed source owner for an AB matrix. The active isolated "
        "index and scoped working files must match this ref exactly. Defaults "
        "to origin/main; a local tag or full commit is accepted when push is "
        "unavailable.",
    )
    parser.add_argument(
        "--allow-stable-dirty-runtime",
        action="store_true",
        help="Diagnostic-only AB mode: admit scoped dirt, fingerprint every "
        "Git-visible runtime input around every arm, and abort on byte drift. "
        "Does not claim commit identity.",
    )
    parser.add_argument(
        "--day-cycle-time",
        type=float,
        default=0.5,
        help="Deterministic celestial phase in [0,1), applied to every arm.",
    )
    parser.add_argument(
        "--allow-inherited-env",
        action="store_true",
        help="Proceed after reporting inherited gate-family variables; all arms still scrub them.",
    )
    parser.add_argument(
        "--keep-forever",
        "--pin-run",
        dest="keep_forever",
        action="store_true",
        help=(
            "Explicitly pin this run's raw artifacts and bypass a hard .run "
            "budget. The override is recorded in each receipt."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    inherited = _inherited_gates()
    if inherited and not args.allow_inherited_env:
        print(
            "refusing inherited runtime gates: " + ", ".join(sorted(inherited)),
            file=sys.stderr,
        )
        return 2
    if not (0.0 <= args.day_cycle_time < 1.0):
        print("--day-cycle-time must be in [0,1)", file=sys.stderr)
        return 2
    if args.ab_brackets > 0:
        return run_ab_brackets(args)
    if getattr(args, "aa_control", False):
        # A=A only has meaning inside the paired-bracket protocol; without
        # brackets there is nothing to pair and no noise verdict to report.
        print("--aa-control requires --ab-brackets", file=sys.stderr)
        return 2
    if args.ab_control_env:
        print("--ab-control-env requires --ab-brackets", file=sys.stderr)
        return 2
    expected_dimensions = (
        args.expect_presentation_size,
        args.expect_target_size,
        args.expect_ansi_size,
    )
    if not args.headless and any(value is None for value in expected_dimensions):
        print(
            "headed perf runs require --expect-presentation-size, "
            "--expect-target-size, and --expect-ansi-size",
            file=sys.stderr,
        )
        return 2

    wanted = None if args.variants == "all" else {
        value.strip() for value in args.variants.split(",")
    }
    selected = [
        (label, variant_args, {})
        for label, variant_args in VARIANTS
        if wanted is None or label in wanted
    ]
    if args.disable_eye_reuse_control:
        selected = [
            (label, variant_args, {**arm_env, "FL4431_DISABLE_EYE_REUSE": "1"})
            for label, variant_args, arm_env in selected
        ]
    if not selected:
        print(f"no variants match --variants={args.variants}", file=sys.stderr)
        return 2
    results = []
    expected_camera_origin = args.expect_camera_origin
    control_camera_origin = expected_camera_origin
    for label, variant_args, arm_env in selected:
        result = run_variant(
            label,
            variant_args,
            headless=args.headless,
            sample_frames=args.sample_frames,
            warmup_frames=args.warmup_frames,
            perf_trace=args.perf_trace,
            godot_gpu_profile=args.godot_gpu_profile,
            uncapped=getattr(args, "uncapped", False),
            projection_modes=args.projection_modes,
            expect_camera_origin=expected_camera_origin,
            arm_env=arm_env,
            day_cycle_time=args.day_cycle_time,
            stationary=args.stationary,
            expected_presentation_size=args.expect_presentation_size,
            expected_target_size=args.expect_target_size,
            expected_ansi_size=args.expect_ansi_size,
            stage_timing_gate=args.stage_timing_gate,
            keep_forever=args.keep_forever,
        )
        results.append(result)
        if expected_camera_origin is None:
            expected_camera_origin = _settled_camera_origin(result)
            control_camera_origin = expected_camera_origin
            if expected_camera_origin is None:
                break
    summary = summarize(results)
    summary["headless"] = args.headless
    summary["sample_frames"] = args.sample_frames
    summary["warmup_frames"] = args.warmup_frames
    summary["timing_verdict_eligible"] = not (
        args.perf_trace or args.godot_gpu_profile
    )
    summary["stage_timing_gate"] = args.stage_timing_gate
    summary["day_cycle_time"] = args.day_cycle_time
    summary["motion_mode"] = "stationary" if args.stationary else "camera_turn"
    summary["eye_reuse_control"] = args.disable_eye_reuse_control
    summary["control_camera_origin"] = control_camera_origin
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = RUN_DIR / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"summary={summary_path.relative_to(ROOT)}")
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
