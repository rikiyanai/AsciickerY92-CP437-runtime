#!/usr/bin/env python3
"""FL-4265 receipt-side authority queue continuity diagnostics."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_godot_camera_turn_perf as runner  # noqa: E402


def _snapshot(
    *,
    dropped: int = 0,
    overflow_episodes: int | None = None,
    ingest_seq: int = 10,
    high_water: int = 1,
    pending: int = 1,
    limit: int = 32,
) -> dict:
    return {
        "status": "available",
        "limit": limit,
        "pending": pending,
        "high_water": high_water,
        "overflow_episodes": (
            dropped if overflow_episodes is None else overflow_episodes
        ),
        "dropped": dropped,
        "ingest_seq": ingest_seq,
    }


def _mode(before: dict, frame_snapshots: list[dict], after: dict) -> dict:
    return {
        "sample_count": len(frame_snapshots),
        "authority_queue_before_measurement": before,
        "authority_queue_after_measurement": after,
        "frame_timing_records": [
            {"authority_queue": snapshot} for snapshot in frame_snapshots
        ],
    }


def test_clean_window_proves_both_continuity_scopes() -> None:
    mode = _mode(
        _snapshot(ingest_seq=10),
        [_snapshot(ingest_seq=11), _snapshot(ingest_seq=12)],
        _snapshot(ingest_seq=12),
    )

    diagnostic = runner._authority_queue_diagnostic(mode)

    assert diagnostic["status"] == "measured"
    assert diagnostic["whole_run_continuity"] is True
    assert diagnostic["whole_run_continuity_status"] == "passed"
    assert diagnostic["measured_window_continuity"] is True
    assert diagnostic["measured_window_continuity_status"] == "passed"
    assert diagnostic["whole_run_dropped_count"] == 0
    assert diagnostic["measured_window_dropped_count"] == 0


def test_startup_drop_fails_whole_run_but_preserves_clean_window_result() -> None:
    mode = _mode(
        _snapshot(dropped=1, ingest_seq=10),
        [_snapshot(dropped=1, ingest_seq=11), _snapshot(dropped=1, ingest_seq=12)],
        _snapshot(dropped=1, ingest_seq=12),
    )

    diagnostic = runner._authority_queue_diagnostic(mode)

    assert diagnostic["status"] == "measured"
    assert diagnostic["startup_dropped_count"] == 1
    assert diagnostic["whole_run_dropped_count"] == 1
    assert diagnostic["measured_window_dropped_count"] == 0
    assert diagnostic["whole_run_continuity"] is False
    assert diagnostic["whole_run_continuity_status"] == "failed"
    assert diagnostic["measured_window_continuity"] is True
    assert diagnostic["measured_window_continuity_status"] == "passed"
    assert diagnostic["reason"] == "startup_drops_present"


def test_drop_during_measured_window_fails_both_scopes() -> None:
    mode = _mode(
        _snapshot(ingest_seq=10),
        [_snapshot(ingest_seq=11), _snapshot(dropped=1, ingest_seq=12)],
        _snapshot(dropped=1, ingest_seq=12),
    )

    diagnostic = runner._authority_queue_diagnostic(mode)

    assert diagnostic["status"] == "measured"
    assert diagnostic["startup_dropped_count"] == 0
    assert diagnostic["whole_run_dropped_count"] == 1
    assert diagnostic["measured_window_dropped_count"] == 1
    assert diagnostic["whole_run_continuity"] is False
    assert diagnostic["measured_window_continuity"] is False
    assert diagnostic["whole_run_continuity_status"] == "failed"
    assert diagnostic["measured_window_continuity_status"] == "failed"
    assert diagnostic["reason"] == "measured_window_drops_present"


def test_missing_witness_is_unknown_and_never_a_continuity_pass() -> None:
    mode = _mode(
        _snapshot(ingest_seq=10),
        [_snapshot(ingest_seq=11), _snapshot(ingest_seq=12)],
        _snapshot(ingest_seq=12),
    )
    del mode["frame_timing_records"][1]["authority_queue"]

    diagnostic = runner._authority_queue_diagnostic(mode)

    assert diagnostic["status"] == "unknown"
    assert diagnostic["whole_run_continuity"] is None
    assert diagnostic["whole_run_continuity_status"] == "unknown"
    assert diagnostic["measured_window_continuity"] is None
    assert diagnostic["measured_window_continuity_status"] == "unknown"
    assert any("frame_1:" in reason for reason in diagnostic["unknown_reasons"])


def test_counter_reset_is_invalid_instead_of_being_treated_as_no_drop() -> None:
    mode = _mode(
        _snapshot(ingest_seq=10),
        [_snapshot(ingest_seq=9), _snapshot(ingest_seq=12)],
        _snapshot(ingest_seq=12),
    )

    diagnostic = runner._authority_queue_diagnostic(mode)

    assert diagnostic["status"] == "invalid"
    assert diagnostic["whole_run_continuity"] is False
    assert diagnostic["measured_window_continuity"] is False
    assert diagnostic["whole_run_continuity_status"] == "invalid"
    assert diagnostic["measured_window_continuity_status"] == "invalid"
    assert "queue_counter_reset:ingest_seq" in diagnostic["invalid_reasons"]


def test_summary_persists_window_diagnostic_without_changing_receipt_verdict() -> None:
    mode = _mode(
        _snapshot(dropped=1, ingest_seq=10),
        [_snapshot(dropped=1, ingest_seq=11)],
        _snapshot(dropped=1, ingest_seq=11),
    )
    mode.update(
        {
            "projection_mode": "perspective",
            "avg_fps": 30.0,
            "min_fps": 30.0,
            "max_fps": 30.0,
            "avg_delta_ms": 33.0,
            "min_delta_ms": 33.0,
            "max_delta_ms": 33.0,
            "median_delta_ms": 33.0,
            "p95_delta_ms": 33.0,
            "p1_fps": 30.0,
            "hitch_count_33_333ms": 0,
            "hitch_rate": 0.0,
            "frame_deltas_ms": [33.0],
            "validity": {"ready": True},
        }
    )
    result = {"label": "default", "ok": True, "mode_results": [mode]}

    summary = runner.summarize([result])
    row = summary["results"][0]
    summarized_mode = row["mode_results"][0]

    assert row["receipt_valid"] is True
    assert summarized_mode["receipt_valid"] is True
    assert summarized_mode["authority_queue_diagnostic"]["reason"] == (
        "startup_drops_present"
    )
    assert row["authority_queue_summary"]["whole_run_continuity"] is False
    assert row["authority_queue_summary"]["whole_run_continuity_status"] == "failed"
    assert summary["authority_queue_summary"]["whole_run_continuity"] is False
    assert summary["authority_queue_summary"]["measured_window_continuity"] is True
