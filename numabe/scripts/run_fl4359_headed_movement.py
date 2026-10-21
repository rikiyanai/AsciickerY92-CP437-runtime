#!/usr/bin/env python3
"""FL-4359 headed movement and visual-capture runner.

Starts the real Colyseus server, launches a headed Godot client through the
normal --colyseus-client runtime, and drives movement through Godot input.

The retired in-game jitter JSONL producer no longer belongs to this runner.
Calls that still request that evidence fail before either process starts. The
external recorder now owns temporal evidence collection for the active VSB
rendering route.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import signal
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

from godot_proof_publication import (
    PublicationError,
    apply_finder_tags,
    deterministic_artifact_name,
    inspect_visual,
    publish_artifacts,
)
from maintainer.lib.run_budget_guard import check_run_budget
sys.path.insert(0, str(Path(__file__).resolve().parent / "recording"))
import f9_replay  # noqa: E402  FL-4873 replay orchestration module
from launch_fl4359_manual_play import (
    _activate_macos_window,
    _frontmost_process_name,
    _frontmost_process_pid,
    _launch_macos_godot_bundle,
)
from run_receipt_preservation import DeferredRunReceiptPreserver
import true_ff_launch_guard as _launch_guard  # noqa: E402  FL-4516 launch admission

ROOT = Path(__file__).resolve().parents[1]
COLYSEUS_DIR = ROOT / "colyseus_server"
GODOT_PROJECT = ROOT / "godot_project"


def _selected_godot_project_dir(args: argparse.Namespace | None = None) -> Path:
    """The Godot project directory this run executes (FL-4873 defect 1).

    --godot-project-dir, which f9_replay.plan() rewrites to the materialized
    snapshot for --replay-from-snapshot/--materialize-dir. Every consumer of
    the project path (the --path argv, PID discovery, executed-file and
    compile-site checks, presentation atlases) must use this one directory;
    GODOT_PROJECT is only the default. Resolved, because PID discovery matches
    the resolved path against the engine command line.
    """
    raw = getattr(args, "godot_project_dir", None) if args is not None else None
    path = Path(raw).expanduser() if raw else GODOT_PROJECT
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def _f9_finalizer_path(replay_plan: dict | None) -> Path:
    """The F9 bundle finalizer of the executed source tree (FL-4896).

    ScreenRecordingController.gd reads it from ASCIICKER_F9_FINALIZER. A
    materialized snapshot (--replay-from-snapshot) runs its own copy; every
    other run executes this checkout and uses this checkout's finalizer.
    """
    materialized = (replay_plan or {}).get("materialized_source") or {}
    source_root = Path(materialized["root"]) if materialized.get("root") else ROOT
    return source_root / "scripts" / "recording" / "f9_finalize_bundle.py"


def _unregistered_project_tree(godot_project_dir: Path, replay_plan: dict | None) -> str:
    """Why a run may not execute godot_project_dir, or "" when it may (FL-4896).

    RunSourceProvenance snapshots this checkout's runtime pathspecs, or the
    verified materialized snapshot of --replay-from-snapshot. Godot running any
    other tree (a hand-made git archive under .run/, say) compiles bytes that
    neither snapshot holds, so the run can only end in
    compiled_variant_not_reproducible_from_snapshot. Refuse it before launch.
    """
    resolved = godot_project_dir.resolve()
    if resolved == GODOT_PROJECT.resolve():
        return ""
    materialized = (replay_plan or {}).get("materialized_source") or {}
    if materialized.get("root") and resolved == (Path(materialized["root"]) / "godot_project").resolve():
        return ""
    return (f"unregistered_project_tree: --godot-project-dir {resolved} is neither this checkout's "
            f"{GODOT_PROJECT} nor a --replay-from-snapshot materialized tree; its source provenance "
            "cannot be proven. Run the live worktree (the default) or a registered snapshot.")


GODOT_BIN = os.environ.get("GODOT_BIN", "/opt/homebrew/bin/godot")
# A cold True FF launch creates the complete Metal pipeline set before the
# first drawable frame.  The shared-program fan-out can legitimately take
# roughly thirty minutes on the supported Apple backend, so the old three-
# minute default killed that one-time compile and made a cold runtime capture
# impossible.  Keep the watchdog finite, but give the cold path enough room to
# finish; callers may still request a shorter deadline explicitly.
DEFAULT_GODOT_TIMEOUT_SECONDS = 2400.0
DEFAULT_JITTER_CAPTURE_FRAME_LIMIT = 5
DEFAULT_JITTER_CAPTURE_WORLD_SAMPLE_STRIDE = 1
DEFAULT_AIRPLANE_METADATA_FRAME_CAP = 12
DEFAULT_INITIAL_TURN_FRAMES = 8
DEFAULT_AIRPLANE_MOVEMENT_FRAMES = 12
DEFAULT_FL4216_TEMPORAL_CAPTURE_COUNT = 2
DEFAULT_FL4216_TEMPORAL_CAPTURE_INTERVAL_SECONDS = 1.5
MIN_FL4216_TEMPORAL_CAPTURE_INTERVAL_SECONDS = 1.5
MIN_FL4216_TEMPORAL_GODOT_TIMEOUT_SECONDS = 600.0
MAX_COMPLETION_JITTER_CAPTURE_FRAME_LIMIT = 5
MIN_COMPLETION_JITTER_CAPTURE_WORLD_SAMPLE_STRIDE = 1
FULL_GRID_CONTRACT_MODE_STATIONARY = "stationary"
FULL_GRID_CONTRACT_MODE_EXACT_TRANSLATION = "exact_translation"
FULL_GRID_CONTRACT_MODE_CAMERA_TURN = "camera_turn"
FULL_GRID_CONTRACT_MODES = (
    FULL_GRID_CONTRACT_MODE_STATIONARY,
    FULL_GRID_CONTRACT_MODE_EXACT_TRANSLATION,
    FULL_GRID_CONTRACT_MODE_CAMERA_TURN,
)
FULL_GRID_REQUIRED_CAPTURE_FRAMES = 5
FULL_GRID_REQUIRED_WORLD_STRIDE = 1
FULL_GRID_REQUIRED_DAY_CYCLE_TIME = 0.5
FULL_GRID_REQUIRED_GLYPH_MATRIX_CELL_PX = 32
FULL_GRID_REQUIRED_GIF_FAMILY_COUNT = 4
# The full-grid profile is a short attribution microscope. The selected route
# uses twelve active frames plus release and settle; even an explicitly large
# capture may never exceed this absolute temporal bound.
MAX_FULL_GRID_TEMPORAL_CAPTURE_FRAMES = 20
DEFAULT_AIRPLANE_PROOF_SPAWN_X = "-80.8"
DEFAULT_AIRPLANE_PROOF_SPAWN_Z = "307.2"
# First F3 sample from the operator's 2026-07-13 tower-strafe recording.
# This is the replay precondition, not a synthetic landmark approximation.
GHOST_TOWER_SPAWN_X = "66.131"
GHOST_TOWER_SPAWN_Z = "44.123"
DRIVER_PLANE_TURN_AXIS = 0.65
NOISY_COMPLETION_GUARD_ARGS = (
    "--fl4057-probe-diagnostics",
    "--fl4057-probe-diagnostics-every-frame",
)
RECEIPT_SUMMARY_FIELDS = (
    "pass",
    "reason",
    "camera_mode",
    "expected_projection",
    "proof",
    "route_identity",
    "route_kind",
    "active_move_frames",
    "plane_circle_frames",
    "movement_delta",
    "camera_delta",
    "first_intent_frame",
    "first_turn_intent_frame",
    "first_measured_motion_frame",
    "first_active_camera_turn_pressed",
    "first_active_flight_intent",
    "first_active_throttle_command",
    "first_active_pitch_command",
    "first_active_roll_command",
    "first_active_yaw_command",
    "active_turn_frames",
    "turn_intent_frames",
    "camera_yaw_before",
    "camera_yaw_after_turn",
    "camera_yaw_after",
    "camera_yaw_turn_delta",
    "camera_yaw_total_delta",
    "camera_yaw_turn_measured",
    "camera_yaw_release_to_settle_catchup",
    "camera_yaw_post_turn_drift",
    "camera_yaw_post_turn_settle_ok",
    "airplane_yaw_before",
    "airplane_yaw_after_turn",
    "airplane_yaw_after",
    "airplane_yaw_turn_delta",
    "airplane_yaw_total_delta",
    "airplane_yaw_turn_measured",
    "active_route_start_position",
    "first_active_frame_delta",
    "movement_fail_fast_frame",
    "fail_fast_reason",
    "prearmed_motion_frames",
    "route_segment_frames",
    "airplane_dynamic_face_count",
    "actor_frame_rect_row_count",
    "capture_window",
    "capture_window_mode_requested",
    "screenshots",
    "screen_recording_requested",
    "screen_recording_started",
    "screen_recording_stopped",
    "screen_recording_path",
    "screen_recording_bytes",
    "capture_path",
    "final_cell_paint_state",
    "foliage_mask_state",
    "ansi_size",
    "cell_dims",
    "viewport_size",
    "display",
    "fps_frame_time_ms",
    "measured_fps",
    "proof_profile",
    "projection_contract_valid",
    "projection_contract_mismatch_reason",
    "product_time_pin_seconds",
    "product_time_constant",
    "product_time_observed",
    "product_time_pin_valid",
    "capture_schedule",
    "sparse_lifecycle_facts_valid",
    "sparse_capture_drain_pass",
    "sparse_capture_pending_after_drain",
    "sparse_capture_ready_after_final_drain",
    "sparse_capture_completed_request_count",
    "sparse_capture_failed_request_count",
    "sparse_capture_written_row_count",
    "sparse_capture_completed_phase_counts",
    "sparse_capture_duplicate_sequence_frames",
    "sparse_capture_join_invalid_rows",
    "sparse_capture_max_outstanding_buffers",
    "sparse_capture_output_byte_formula",
    "sparse_capture_peak_output_capacity_bytes",
)
FRAME_TIME_GRID_CROSSING_FIELDS = (
    "grid_crossing_frame_time_ms",
    "grid_crossing_frame_times_ms",
    "snap_grid_crossing_frame_time_ms",
    "snap_grid_crossing_frame_times_ms",
)
INADMISSIBLE_TIMEOUT_RULE = (
    "Timed-out FL-4359 headed movement captures are raw debris, not analyzer evidence; "
    "a clean Godot exit and completion receipt are required."
)
NO_GODOT_TIMEOUT_HELP = (
    "Finite Godot subprocess timeout in seconds; nonpositive values are normalized "
    "to the default so the proof front door cannot hang indefinitely."
)
REQUIRED_ANALYZER_PROOF_GATES = (
    "scenario_moving_camera_continuous",
    "evidence_required_fields_present",
    "evidence_presentation_observer_population_accounted",
    "evidence_presentation_observer_sensitivity_valid",
    "gameplay_moving_camera_jitter_bounded",
    "gameplay_content_shimmer_bounded",
    "gameplay_surface_identity_bounce_bounded",
)
CURRENT_METRIC_DEFINITIONS = {
    "jitter": "positional instability",
    "shimmer": "final visible signature instability",
    "bounce": "region-scale oscillation",
}
# This stable reason is shared with the activation contract. It makes a stale
# caller visibly fail instead of launching a plausible-looking run with no
# jitter rows behind it.
EXTERNAL_RECORDER_JITTER_REASON = "external_recorder_owns_fl4359_jitter_capture"
SERVER_TICK_AIRPLANE_ROUTE_IDENTITY = "server_tick_constant_physical_flight_input"
SCRIPTED_AIRBORNE_AIRPLANE_ROUTE_IDENTITY = "scripted_airborne_airplane_movement"
SCRIPTED_GROUNDED_ROUTE_IDENTITY = "scripted_ground_movement"
# FL-4431 owns the one locked presentation surface used by the historical
# 32px closure identity.  The runner selects this owner for the mounted
# airplane route; the driver only observes the resulting viewport and never
# sets DisplayServer geometry for this arm.
CANONICAL_AIRPLANE_PRESENTATION_SIZE = (3600, 2252)
CANONICAL_AIRPLANE_ANSI_SIZE = (113, 71)
CANONICAL_AIRPLANE_PRESENTATION_ARG = (
    "--fl4431-canonical-presentation=3600x2252"
)
CANONICAL_AIRPLANE_CAPTURE_MODE = "canonical_locked"
HISTORICAL_R6_PRESENTATION_PROFILE = "fl4359_historical_r6_v1"
HISTORICAL_R6_PRESENTATION_SIZE = (3840, 2160)
FL4359_OPERATOR_SLAB_REQUESTED_RESOLUTION = (3600, 2206)
FL4359_OPERATOR_SLAB_WINDOW_SIZE = (3560, 2154)
FL4359_OPERATOR_SLAB_SCREEN_SIZE = (3600, 2338)
FL4359_OPERATOR_SLAB_VIEWPORT_SIZE = (1004, 608)
FL4359_OPERATOR_SLAB_ANSI_SIZE = (178, 108)
HISTORICAL_R6_ANSI_SIZE = (120, 68)

# Presentation identity is deliberately separate from the analyzer profile.  A
# temporal oracle says how a capture is sampled; this tuple says which native
# cell/presentation surface the runtime actually applied.  Keep the registry
# small until the owning Godot surface emits matching runtime facts.  Adding an
# unverified aspect here would let the runner label a 32px perspective capture
# as an orthographic or tall-cell comparison.
PRESENTATION_PROFILE_SCHEMA = "fl4359.presentation_profile.v1"
CANONICAL_NATIVE_PRESENTATION_PROFILE = "fl4359_native_square32_perspective_v1"
TERMINAL_NATIVE_PRESENTATION_PROFILE = "fl4359_native_terminal16x32_perspective_v1"
GROUND_TURN_NATIVE_PRESENTATION_PROFILE = (
    "fl4359_ground_turn_terminal16x32_3840x2160_perspective_v1"
)
GROUND_TURN_PRESENTATION_SIZE = (3840, 2160)
PRESENTATION_FIELD_RECORD_BYTES = 96
POSTPROCESS_TRACE_PEAK_BYTES = 2252 * 22 * 4 * 6
NATIVE_SOURCE_CONTENT_RECORD_BYTES = 144
NATIVE_SOURCE_CONTENT_REQUIRED_PEAK_BYTES = 1195639152
REFLECTION_LINEAGE_CAPACITY = 262144
REFLECTION_LINEAGE_TABLE_BYTES = 16 + REFLECTION_LINEAGE_CAPACITY * 64
REFLECTION_LINEAGE_REQUIRED_PEAK_BYTES = REFLECTION_LINEAGE_TABLE_BYTES + 2 * 384 * 384 * 18 * 4
NATIVE_SKY_CONTEXT_HISTORY_REQUIRED_PEAK_BYTES = 1024 * 1024
GENERATION_OBSERVER_REQUIRED_PEAK_BYTES = 160 * 1024 * 1024
# Include every retained observer lane, not just the native field. These are
# capacity reservations; the runtime receipt reports the actual pool extent.
SOURCE_GENERATION_PEAK_BYTES = 160 * 1024 * 1024
SPARSE_CAPTURE_POOL_PEAK_BYTES = 160 * 1024 * 1024 * 9
VERTEX_WITNESS_PEAK_BYTES = 360_972_296
RASTER_WITNESS_PEAK_BYTES = 2 * (4 + 69_442 * (1 + 64 * 16)) * 4
CANVAS_TRACE_PEAK_BYTES = 1280 * 1024 * 1024
PRESENTATION_FIELD_REQUIRED_PEAK_BYTES = (
    3840 * 2160 * PRESENTATION_FIELD_RECORD_BYTES * 6 + POSTPROCESS_TRACE_PEAK_BYTES
    + SOURCE_GENERATION_PEAK_BYTES + SPARSE_CAPTURE_POOL_PEAK_BYTES
    + VERTEX_WITNESS_PEAK_BYTES + RASTER_WITNESS_PEAK_BYTES + CANVAS_TRACE_PEAK_BYTES
    + NATIVE_SOURCE_CONTENT_REQUIRED_PEAK_BYTES
    + REFLECTION_LINEAGE_REQUIRED_PEAK_BYTES
    + NATIVE_SKY_CONTEXT_HISTORY_REQUIRED_PEAK_BYTES
)
COMPACT_PRESENTATION_FIELD_SCHEMA = "fl4359.final_paint_field.v3.compact_subcell"
COMPACT_PRESENTATION_FIELD_REQUIRED_PEAK_BYTES = (
    PRESENTATION_FIELD_REQUIRED_PEAK_BYTES - 3840 * 2160 * PRESENTATION_FIELD_RECORD_BYTES * 6
    + 2_791_497_984
)
SAME_RUN_TEMPORAL_PERFORMANCE_TIMING_SCHEMA = (
    "fl4359.same_run_temporal_performance_timing.v1"
)
SAME_RUN_TEMPORAL_PERFORMANCE_RECORD_SCHEMA = (
    "fl4359.same_run_temporal_performance_frame.v1"
)
SAME_RUN_TEMPORAL_PERFORMANCE_GATE_SCHEMA = (
    "fl4359.same_run_temporal_performance_gate.v1"
)
SAME_RUN_TEMPORAL_PERFORMANCE_CLASSIFICATION = (
    "instrumented_full_grid_temporal_capture_cadence"
)
SAME_RUN_TEMPORAL_PERFORMANCE_DEADLINE_MS = 16.667


CANONICAL_NATIVE_ATLAS_SOURCES = (
    ("res://assets/font.png", "godot_project/assets/font.png"),
    (
        "res://assets/glyphs/atlases/material.additive.v1.page32.png",
        "godot_project/assets/glyphs/atlases/material.additive.v1.page32.png",
    ),
    (
        "res://assets/glyphs/atlases/material.morphology.v2.page32.png",
        "godot_project/assets/glyphs/atlases/material.morphology.v2.page32.png",
    ),
)


class PresentationProfileSpec(NamedTuple):
    """One strict expected native presentation identity for an exact-pose arm."""

    profile_id: str
    projection: str
    native_cell_size: tuple[int, int]
    presentation_size: tuple[int, int]
    ansi_size: tuple[int, int]
    atlas_sources: tuple[tuple[str, str, tuple[int, int]], ...]
    ortho_scale: float | None = None
    applied_policy: dict | None = None

    @classmethod
    def from_mapping(cls, value: object) -> "PresentationProfileSpec":
        if not isinstance(value, dict):
            raise ValueError("presentation_profile_manifest_must_be_object")
        if value.get("schema") != PRESENTATION_PROFILE_SCHEMA:
            raise ValueError("presentation_profile_manifest_schema_mismatch")
        required = {
            "schema",
            "profile_id",
            "projection",
            "native_cell_size",
            "presentation_size",
            "ansi_size",
            "atlas_identity",
        }
        if not required.issubset(value):
            missing = sorted(required.difference(value))
            raise ValueError("presentation_profile_manifest_missing:" + ",".join(missing))
        profile_id = value.get("profile_id")
        projection = value.get("projection")
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise ValueError("presentation_profile_id_must_be_nonempty_string")
        if projection not in {"perspective", "ortho"}:
            raise ValueError("presentation_profile_projection_invalid")

        def dimensions(name: str) -> tuple[int, int]:
            parsed = _size_tuple(value.get(name))
            if parsed is None or any(component <= 0 for component in parsed):
                raise ValueError(f"presentation_profile_{name}_invalid")
            return parsed

        native_cell_size = dimensions("native_cell_size")
        presentation_size = dimensions("presentation_size")
        ansi_size = dimensions("ansi_size")
        atlas = value.get("atlas_identity")
        if not isinstance(atlas, dict) or atlas.get("schema") != "fl4359.native_atlas_identity.v1":
            raise ValueError("presentation_profile_atlas_identity_invalid")
        raw_sources = atlas.get("sources")
        if not isinstance(raw_sources, list) or not raw_sources:
            raise ValueError("presentation_profile_atlas_sources_missing")
        sources: list[tuple[str, str, tuple[int, int]]] = []
        seen_paths: set[str] = set()
        for source in raw_sources:
            if not isinstance(source, dict):
                raise ValueError("presentation_profile_atlas_source_invalid")
            logical_path = source.get("path")
            content_hash = source.get("sha256")
            hash_kind = source.get("hash_kind")
            dimensions_value = _size_tuple(source.get("dimensions"))
            if (
                not isinstance(logical_path, str)
                or not logical_path.strip()
                or logical_path in seen_paths
                or not isinstance(content_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None
                or hash_kind != "decoded_rgba8"
                or dimensions_value is None
                or any(component <= 0 for component in dimensions_value)
            ):
                raise ValueError("presentation_profile_atlas_source_hash_invalid")
            seen_paths.add(logical_path)
            sources.append((logical_path, content_hash, dimensions_value))
        raw_ortho_scale = value.get("ortho_scale")
        if projection == "ortho":
            if not _finite_number(raw_ortho_scale) or float(raw_ortho_scale) <= 0.0:
                raise ValueError("presentation_profile_ortho_scale_required")
            ortho_scale = float(raw_ortho_scale)
        else:
            if raw_ortho_scale is not None:
                raise ValueError("presentation_profile_perspective_ortho_scale_forbidden")
            ortho_scale = None
        applied_policy = value.get("applied_policy")
        if applied_policy is not None and not isinstance(applied_policy, dict):
            raise ValueError("presentation_profile_applied_policy_invalid")
        return cls(
            profile_id=profile_id.strip(),
            projection=projection,
            native_cell_size=native_cell_size,
            presentation_size=presentation_size,
            ansi_size=ansi_size,
            atlas_sources=tuple(sources),
            ortho_scale=ortho_scale,
            applied_policy=applied_policy,
        )

    def atlas_identity(self) -> dict:
        return {
            "schema": "fl4359.native_atlas_identity.v1",
            "sources": [
                {
                    "path": path,
                    "hash_kind": "decoded_rgba8",
                    "dimensions": {"w": dimensions[0], "h": dimensions[1]},
                    "sha256": content_hash,
                }
                for path, content_hash, dimensions in self.atlas_sources
            ],
        }

    def as_dict(self) -> dict:
        return {
            "schema": PRESENTATION_PROFILE_SCHEMA,
            "profile_id": self.profile_id,
            "projection": self.projection,
            "native_cell_size": list(self.native_cell_size),
            "presentation_size": list(self.presentation_size),
            "ansi_size": list(self.ansi_size),
            "atlas_identity": self.atlas_identity(),
            "ortho_scale": self.ortho_scale,
            "applied_policy": self.applied_policy,
        }
RUN_CLASSIFICATION_MOVEMENT_PROOF = "movement-proof"
RUN_CLASSIFICATION_DIAGNOSTIC_ONLY = "diagnostic-only"
DIAGNOSTIC_PROOF_CLASSIFICATION = "diagnostic_only"
STATIONARY_TERMINAL_REPROJECTION_CALIBRATION = (
    "stationary_terminal_reprojection_calibration"
)
FULL_GRID_ORACLE_PROFILE = "full_grid_temporal_oracle_v1"
# FL-4359 pinned exact-pose sweep. A named route on the DEFAULT driver path (the
# only path that arms the full-grid capture writer) whose per-frame camera pose is
# an exact CLI identity, so an OFF arm and a retained-owner ON arm replay the same
# choreography. It is diagnostic machinery for an A/B, never a movement proof.
EXACT_POSE_SWEEP_ROUTE_IDENTITY = "fl4359_pinned_exact_pose_sweep_v1"
# FL-4359 frame-PNG route. `--frame-pngs` hands the driver this directory under
# the run's own output root and stitches the result into this GIF name. The
# stitch owner is a separate script so the runner never grows image code.
FRAME_PNG_DIR_NAME = "frames"
FRAME_PNG_SEQUENCE_FRAMES = 5
FRAME_PNG_NAME = "frame_%02d.png"
SWEEP_GIF_NAME = "sweep_5frames.gif"
SWEEP_GIF_FRAME_MS = 60
STITCH_SWEEP_GIF_SCRIPT = Path(__file__).resolve().parent / "fl4359_stitch_sweep_gif.py"
# FL-4896 True FF temporal route. Strict True FF draws native text rows, not a
# fixed cell grid, and produces none of the VSB sparse-capture records the
# full-grid oracle counts. Its per-frame capture was the true_ff.text_frame.v1
# export, measured by the text flip instrument. FL-4907
# DEL_LANE_SCREEN_PIPELINE_DELETED_20260930 deleted the exporter and its
# World.gd flag, so the runner passes no export flag and the instrument finds
# no export (true_ff_text_export_missing) until a feature-route exporter exists.
TRUE_FF_TEXT_EXPORT_NAME = "text_frames.jsonl"
TRUE_FF_TEXT_FLIPS_DIR_NAME = "textflips"
MEASURE_TRUE_FF_TEXT_FLIPS_SCRIPT = Path(__file__).resolve().parent / "measure_true_ff_text_flips.py"
GODOT_PROOF_PUBLICATION_MANIFEST = "godot_proof_publication.json"
GODOT_PROOF_PUBLICATION_DIR = "published"
DEFAULT_EXACT_POSE_SWEEP_DELTA = "0.05,0,0"

PRESENTATION_ARTIFACT_MANIFEST_SCHEMA = "fl4359.presentation_artifact_manifest.v1"
PRESENTATION_ARTIFACT_ROLES = (
    "native",
    "full",
    "bg_only",
    "fg_only",
    "fg_highlight",
    "glyph_change_highlight",
    "fg_glyph_change_highlight",
)
PRESENTATION_COMPARISON_ROLES = ("ab_sidebyside", "ab_fg_highlight")
PRESENTATION_CAPTURE_FRAME_IDS = (0, 1, 2, 3, 4)
# The oracle owns both presentation controls needed for comparable frames:
# `day_night_cycle` makes the 0.5 noon pin observable and `tilt_shift_off`
# prevents a screen-space post-process from changing the captured cell field.
FULL_GRID_ORACLE_REQUIRED_FEATURE = "day_night_cycle"
FULL_GRID_ORACLE_REQUIRED_DISABLED_FEATURE = "tilt_shift_off"


def _stationary_calibration_enabled(args: argparse.Namespace) -> bool:
    """Return whether this invocation is the proof-only stationary calibration arm."""
    return bool(
        getattr(args, "fl4359_terminal_reprojection_stationary_calibration", False)
    )


def _moving_full_grid_capture_enabled(args: argparse.Namespace) -> bool:
    """Return whether the legacy moving flag requested the full-grid contract."""
    return bool(getattr(args, "fl4359_moving_full_grid_capture", False))


def _presentation_density_diagnostic_enabled(args: argparse.Namespace) -> bool:
    """Permit a density experiment in the unchanged full-scene five-pose route."""
    return bool(
        getattr(args, "presentation_observer_only", False)
        and getattr(args, "presentation_field_capture", False)
        and getattr(args, "run_classification", "") == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
        and getattr(args, "analyzer_proof_profile", "") == "full_grid_temporal_oracle_v1"
        and getattr(args, "full_grid_contract_mode", "") == "exact_translation"
        and getattr(args, "exact_pose_sweep", False)
        and getattr(args, "exact_camera_projection", "") == "perspective"
        and getattr(args, "move_frames", 0) == 5
        and getattr(args, "jitter_capture_frame_limit", 0) == 5
    )


# The full-grid contract always pins the brightest midday phase.
FULL_GRID_DAY_CYCLE_TIME_STATIONARY = FULL_GRID_REQUIRED_DAY_CYCLE_TIME
FULL_GRID_DAY_CYCLE_TIME_MOVING_MIDDAY = FULL_GRID_REQUIRED_DAY_CYCLE_TIME


def _full_grid_day_cycle_time_valid(args: argparse.Namespace) -> bool:
    """Require a pinned daylight phase for every full-grid temporal proof arm."""
    if getattr(args, "analyzer_proof_profile", "") != "full_grid_temporal_oracle_v1":
        return True
    value = getattr(args, "day_cycle_time", None)
    try:
        return value is not None and math.isfinite(float(value)) and math.isclose(
            float(value),
            FULL_GRID_REQUIRED_DAY_CYCLE_TIME,
            rel_tol=0.0,
            abs_tol=1e-9,
        )
    except (TypeError, ValueError):
        return False


def _full_grid_contract_mode(args: argparse.Namespace) -> str:
    """Return the explicit full-grid proof contract mode selected by the CLI."""
    requested = str(getattr(args, "full_grid_contract_mode", "") or "")
    if requested in FULL_GRID_CONTRACT_MODES:
        return requested
    # FL-4896: True FF measures its own sweep with the text flip instrument;
    # the full-grid contract belongs to the fixed-cell oracle FF refuses.
    if _strict_true_ff(args):
        return ""
    if _stationary_calibration_enabled(args):
        return FULL_GRID_CONTRACT_MODE_STATIONARY
    if bool(getattr(args, "exact_pose_sweep", False)):
        return FULL_GRID_CONTRACT_MODE_EXACT_TRANSLATION
    if _moving_full_grid_capture_enabled(args):
        return FULL_GRID_CONTRACT_MODE_EXACT_TRANSLATION
    return ""


def _positions_all_equal(positions: list[list[float]]) -> bool:
    if not positions:
        return False
    first = positions[0]
    return all(
        len(position) == 3
        and all(
            math.isclose(float(position[axis]), float(first[axis]), rel_tol=0.0, abs_tol=1e-9)
            for axis in range(3)
        )
        for position in positions
    )


def _positions_all_distinct(positions: list[list[float]]) -> bool:
    if not positions:
        return False
    seen: set[tuple[float, float, float]] = set()
    for position in positions:
        key = tuple(round(float(component), 9) for component in position)
        if key in seen:
            return False
        seen.add(key)
    return len(seen) == len(positions)


def _full_grid_pose_contract(args: argparse.Namespace) -> dict:
    """Validate the declared five-frame camera path before any full-grid launch."""
    mode = _full_grid_contract_mode(args)
    supplied = _parse_world_vec3_list_arg(
        str(getattr(args, "exact_pose_sweep_positions", "") or "")
    )
    positions = supplied or _exact_pose_sweep_positions(
        args, FULL_GRID_REQUIRED_CAPTURE_FRAMES
    )
    exact_five_positions = len(positions) == FULL_GRID_REQUIRED_CAPTURE_FRAMES
    yaw_present = bool(str(getattr(args, "exact_camera_yaw", "") or "").strip())
    pitch_present = bool(str(getattr(args, "exact_camera_pitch", "") or "").strip())
    stationary_ok = (
        mode == FULL_GRID_CONTRACT_MODE_STATIONARY
        and exact_five_positions
        and _positions_all_equal(positions)
    )
    translation_ok = (
        mode == FULL_GRID_CONTRACT_MODE_EXACT_TRANSLATION
        and supplied is not None
        and exact_five_positions
        and _positions_all_distinct(positions)
        and yaw_present
        and pitch_present
        and int(getattr(args, "turn_frames", -1)) == 0
    )
    camera_turn_ok = (
        mode == FULL_GRID_CONTRACT_MODE_CAMERA_TURN
        and supplied is None
        and not bool(getattr(args, "exact_pose_sweep", False))
        and int(getattr(args, "move_frames", 0)) >= FULL_GRID_REQUIRED_CAPTURE_FRAMES
        and int(getattr(args, "turn_frames", 0)) >= FULL_GRID_REQUIRED_CAPTURE_FRAMES
        and str(getattr(args, "turn_action", "")) in {"rotate_left", "rotate_right"}
    )
    return {
        "mode": mode,
        "positions": positions,
        "supplied_position_count": len(supplied or []),
        "exact_five_positions": exact_five_positions,
        "positions_all_equal": _positions_all_equal(positions),
        "positions_all_distinct": _positions_all_distinct(positions),
        "yaw_present": yaw_present,
        "pitch_present": pitch_present,
        "turn_frames_zero": int(getattr(args, "turn_frames", -1)) == 0,
        "camera_turn_input_window_contains_capture": camera_turn_ok,
        "pass": stationary_ok or translation_ok or camera_turn_ok,
        "reason": (
            "full_grid_pose_contract_ok"
            if stationary_ok or translation_ok or camera_turn_ok
            else "full_grid_pose_contract_invalid"
        ),
    }


def _parse_world_vec3_arg(value: str) -> tuple[float, float, float] | None:
    """Return one finite `x,y,z` world triple, or None when it is not usable."""
    parts = [part.strip() for part in str(value).split(",")]
    if len(parts) != 3 or any(not part for part in parts):
        return None
    try:
        components = [float(part) for part in parts]
    except ValueError:
        return None
    if not all(math.isfinite(component) for component in components):
        return None
    return components[0], components[1], components[2]


def _parse_world_vec3_list_arg(value: str) -> list[list[float]] | None:
    """Return an explicit `x,y,z;x,y,z;...` camera path, or None when unusable.

    The FL-4359 operator-designated airplane pose advances by NON-UNIFORM
    per-frame steps, which `origin + delta * i` cannot express. This parser owns
    that explicit route. It rejects (returns None) an empty list, a triple that is
    not exactly three components, and a non-finite component, so the preflight
    names the failure instead of silently replaying a different choreography.
    """
    text = str(value or "").strip()
    if not text:
        return None
    entries = [part.strip() for part in text.split(";")]
    if any(not entry for entry in entries):
        return None
    positions: list[list[float]] = []
    for entry in entries:
        triple = _parse_world_vec3_arg(entry)
        if triple is None:
            return None
        positions.append([triple[0], triple[1], triple[2]])
    return positions or None


def _exact_pose_sweep_positions(
    args: argparse.Namespace, frame_count: int
) -> list[list[float]]:
    """Return the declared camera position for every captured sequence frame.

    This is the choreography identity the A/B arms must both replay. Two routes
    own it and the explicit one wins:

    * `--exact-pose-sweep-positions` — a literal per-frame pose list. It OVERRIDES
      the delta because a non-uniform operator-designated path is not expressible
      as `origin + delta * i`. The list is clipped to the captured window; the
      driver holds the final entry for any sequence frame past its end.
    * otherwise the historical constant-delta rule `origin + delta * i`.

    It is published in the preflight so the requested pose list is durable
    evidence next to the observed capture poses.
    """
    if frame_count <= 0:
        return []
    explicit = _parse_world_vec3_list_arg(
        getattr(args, "exact_pose_sweep_positions", "")
    )
    if explicit is not None:
        return [list(position) for position in explicit[:frame_count]]
    origin = _parse_world_vec3_arg(getattr(args, "exact_camera_position", ""))
    delta = _parse_world_vec3_arg(getattr(args, "exact_pose_sweep_delta", ""))
    if origin is None or delta is None:
        return []
    return [
        [origin[axis] + delta[axis] * index for axis in range(3)]
        for index in range(frame_count)
    ]


# Per-axis world-unit slack when grading a captured eye against its declared pose.
# The declared list and the captured trace both carry float world coordinates, so
# only float round-tripping is tolerated here, never a different pose.
EXACT_POSE_SWEEP_FRAME_POSE_TOLERANCE = 1e-4
EXACT_POSE_SWEEP_FRAME_ANGLE_TOLERANCE = 0.01


def _angle_match(observed: object, expected: object) -> bool:
    try:
        actual = float(observed)
        target = float(expected)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(actual) or not math.isfinite(target):
        return False
    return abs(math.atan2(math.sin(actual - target), math.cos(actual - target))) <= (
        EXACT_POSE_SWEEP_FRAME_ANGLE_TOLERANCE
    )


def _exact_pose_sweep_frame_pose_rows(
    capture_path: Path,
    declared_positions: list[list[float]],
    tolerance: float = EXACT_POSE_SWEEP_FRAME_POSE_TOLERANCE,
    requested_yaw: object = None,
    requested_pitch: object = None,
) -> list[dict]:
    """Grade each captured frame's rendered eye against its declared sweep pose.

    Owner of the post-run sweep-pin observation. The driver pins the pixel-camera
    transform per captured sequence frame; an unexpired pin makes the compositor
    keep the PREVIOUS pose and ignore plain pose writes, so a capture can record
    the declared list with one entry duplicated and the next one dropped while the
    receipt still reports the requested path. Frame `i` is graded against declared
    entry `min(i, len - 1)` because the driver clamps at the end of the list
    instead of extrapolating.

    Captures reach multiple GB, so this streams the file and parses only the
    frame rows.
    """
    rows: list[dict] = []
    if not declared_positions or not capture_path.exists():
        return rows
    last_index = len(declared_positions) - 1
    with capture_path.open("r") as capture:
        for line in capture:
            if not line.startswith('{"frame_id"'):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            frame_id = record.get("frame_id")
            if not isinstance(frame_id, int) or isinstance(frame_id, bool):
                continue
            frame_trace = _dict_value(record.get("frame_trace"))
            camera_world = _dict_value(frame_trace.get("camera_world"))
            pose = _dict_value(frame_trace.get("pose"))
            camera_yaw = frame_trace.get("camera_yaw", pose.get("camera_yaw"))
            camera_pitch = frame_trace.get("camera_pitch", pose.get("camera_pitch"))
            observed = [camera_world.get(axis) for axis in ("x", "y", "z")]
            expected = declared_positions[min(max(frame_id, 0), last_index)]
            match = all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and abs(float(value) - float(expected[axis])) <= tolerance
                for axis, value in enumerate(observed)
            )
            rows.append(
                {
                    "frame_id": frame_id,
                    "expected": [float(component) for component in expected],
                    "observed": observed,
                    "match": match,
                    "camera_yaw": camera_yaw,
                    "camera_pitch": camera_pitch,
                    "camera_yaw_match": (
                        True
                        if requested_yaw is None
                        else _angle_match(camera_yaw, requested_yaw)
                    ),
                    "camera_pitch_match": (
                        True
                        if requested_pitch is None
                        else _angle_match(camera_pitch, requested_pitch)
                    ),
                }
            )
    return rows


def _frame_png_pose_rows(
    receipt: dict,
    declared_positions: list[list[float]],
    tolerance: float = EXACT_POSE_SWEEP_FRAME_POSE_TOLERANCE,
    requested_yaw: object = None,
    requested_pitch: object = None,
) -> list[dict]:
    """Grade the frame-PNG route's applied poses against the declared sweep.

    The frame-PNG route runs without the full-grid oracle, so there is no
    per-frame `camera_world` trace to grade. The driver instead records the
    declared pose it applied for each saved sequence index as `frame_png_poses`.
    Rows keep the same shape `_exact_pose_sweep_frame_pose_rows` produces, so the
    mismatch reporting below has exactly one code path.
    """
    rows: list[dict] = []
    pose_rows = receipt.get("frame_png_poses")
    if not declared_positions or not isinstance(pose_rows, list):
        return rows
    last_index = len(declared_positions) - 1
    for pose_row in pose_rows:
        if not isinstance(pose_row, dict):
            continue
        frame_id = pose_row.get("sequence_frame")
        if not isinstance(frame_id, int) or isinstance(frame_id, bool):
            continue
        camera_position = _dict_value(pose_row.get("camera_position"))
        observed = [camera_position.get(axis) for axis in ("x", "y", "z")]
        expected = declared_positions[min(max(frame_id, 0), last_index)]
        match = all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and abs(float(value) - float(expected[axis])) <= tolerance
            for axis, value in enumerate(observed)
        )
        rows.append(
            {
                "frame_id": frame_id,
                "expected": [float(component) for component in expected],
                "observed": observed,
                "match": match,
                "camera_yaw": pose_row.get("camera_yaw"),
                "camera_pitch": pose_row.get("camera_pitch"),
                "camera_yaw_match": (
                    True
                    if requested_yaw is None
                    else _angle_match(pose_row.get("camera_yaw"), requested_yaw)
                ),
                "camera_pitch_match": (
                    True
                    if requested_pitch is None
                    else _angle_match(pose_row.get("camera_pitch"), requested_pitch)
                ),
                "source": "driver_receipt_frame_png_poses",
            }
        )
    return rows


def _strict_true_ff(args: argparse.Namespace) -> bool:
    """True FF is the default presentation; every FF run measures its text (FL-4896)."""
    return getattr(args, "presentation_mode", "FF") == "FF"


def _true_ff_temporal_route_error(args: argparse.Namespace) -> str:
    """Why this invocation cannot measure True FF over time, or "" (FL-4896)."""
    if not _strict_true_ff(args):
        return ""
    if getattr(args, "analyzer_proof_profile", "") == FULL_GRID_ORACLE_PROFILE:
        return ("--presentation-mode FF cannot run --analyzer-proof-profile "
                "full_grid_temporal_oracle_v1: the oracle counts VSB sparse-capture records on a "
                "fixed cell grid, and strict True FF draws native text rows and produces none. "
                "Every FF run measures its glyph flips with the text flip instrument; drop the "
                "profile (five-frame sweep: --exact-pose-sweep --frame-pngs).")
    if str(getattr(args, "full_grid_contract_mode", "") or ""):
        return ("--presentation-mode FF cannot take --full-grid-contract-mode: that contract "
                "belongs to the fixed-cell full-grid oracle.")
    return ""


def _true_ff_text_flips_stem(args: argparse.Namespace, out_dir: Path, git_head: str,
                             window: str = "run") -> str:
    """FL-4894 stem: run, view, route, projection, source and selection identity,
    with no separator the instrument refuses."""
    def token(value: object) -> str:
        return "".join(c if c.isalnum() or c in "-." else "-" for c in str(value)) or "none"

    return "_".join([
        "FL4896", token(out_dir.name), "FF-" + token(getattr(args, "camera_mode", "")),
        _run_projection_token(args), token(git_head[:9] or "nohead"), "textflips-" + token(window)])


def _run_projection_token(args: argparse.Namespace) -> str:
    """FL-4512 naming: the projection the run renders. An explicit exact-camera
    projection wins; otherwise the camera mode decides (plane_air and
    perspective render perspective). The old rule named every run without
    --exact-camera-projection 'ortho', so perspective airplane runs were
    published as FF-plane-air_ortho."""
    explicit = str(getattr(args, "exact_camera_projection", "") or "")
    if explicit:
        return "persp" if explicit == "perspective" else "ortho"
    mode = str(getattr(args, "camera_mode", "") or "")
    return "ortho" if mode in ("ortho", "plane_air_ortho") else "persp"


def _run_text_flip_instrument(frames: Path, *, stem: str, out: Path,
                              f9_bundle: Path | None = None, metadata: Path | None = None) -> dict:
    """One scripts/measure_true_ff_text_flips.py run. The instrument preserves its
    own receipt under artifacts/run_receipts/ for FL-4896 before it reports
    success, and publishes any media it draws through godot_proof_publication."""
    cmd = [sys.executable, str(MEASURE_TRUE_FF_TEXT_FLIPS_SCRIPT), "run", str(frames),
           "--stem", stem, "--fl", "FL-4896", "--out", str(out)]
    if f9_bundle is not None:
        cmd += ["--f9-bundle", str(f9_bundle)]
    if metadata is not None:
        cmd += ["--publication-metadata", str(metadata)]
    result: dict = {"frames_file": str(frames), "stem": stem, "command": cmd, "rc": 1,
                    "reason": "", "summary": None, "durable_receipt": "", "media": []}
    completed = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    result["rc"] = completed.returncode
    result["stderr_tail"] = completed.stderr[-2000:]
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    try:
        summary = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:
        summary = None
    if isinstance(summary, dict):
        result["summary"] = summary
        result["durable_receipt"] = str(summary.get("durable_receipt", ""))
        result["media"] = [str(out / name) for name in summary.get("media") or []]
    if completed.returncode == 5:
        # FL-4907 WAVE_C_CAPTURE: the instrument preserved a summary that counts
        # invalid (error-record) frames; the run is measured but never a pass.
        result["reason"] = "true_ff_text_flips_invalid_frames"
    elif completed.returncode != 0:
        result["reason"] = f"true_ff_text_flips_rc_{completed.returncode}"
    elif not isinstance(summary, dict) or not result["durable_receipt"]:
        result["rc"] = 1
        result["reason"] = "true_ff_text_flips_summary_unreadable"
    else:
        result["reason"] = "true_ff_text_flips_measured"
    return result


def _f9_png_engine_frames(bundle: Path) -> set[int]:
    """Engine frames whose F9 PNG exists on disk (manifest.json rows)."""
    doc = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    return {int(row["engine_frame"]) for row in doc.get("frames") or []
            if isinstance(row, dict) and "engine_frame" in row and row.get("png")
            and (bundle / str(row["png"])).is_file()}


def _write_f9_window_export(export: Path, engine_frames: set[int], dest: Path) -> int:
    """Copy the export lines whose engine_frame has an F9 PNG; returns the count."""
    kept = []
    for line in export.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        engine_frame = json.loads(line).get("engine_frame")
        if isinstance(engine_frame, int) and engine_frame in engine_frames:
            kept.append(line)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("".join(line + "\n" for line in kept), encoding="utf-8")
    return len(kept)


def _true_ff_text_flip_publication_metadata(receipt_path: Path, export: Path) -> tuple[dict | None, str]:
    """Capture identity for the flip media, from this run's own receipt (FL-4896).

    The same diagnostic envelope the runner publishes its own visuals with
    (_publication_render_metadata: INCOMPLETE_CURRENT_CAPTURE, identity fields
    from the receipt or UNKNOWN), keyed to the text export as the capture. The
    modes are FLAT/FLAT because the capture is a true_ff.text_frame.v1 export,
    which only the strict True FF raster writes; no fixed cell exists, so
    cell_resolution stays UNKNOWN. Returns (None, reason) when the receipt or
    the export cannot support that.
    """
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "publication_identity_unavailable:receipt_missing_or_unreadable"
    if not isinstance(receipt, dict):
        return None, "publication_identity_unavailable:receipt_not_an_object"
    try:
        first = json.loads(export.read_text(encoding="utf-8").splitlines()[0])
    except (OSError, IndexError, json.JSONDecodeError):
        return None, "publication_identity_unavailable:text_export_unreadable"
    if first.get("schema") != "true_ff.text_frame.v1":
        return None, "publication_identity_unavailable:text_export_schema_mismatch"
    metadata = _publication_render_metadata(receipt, export)
    metadata.update({"fl_id": "FL-4896", "foreground_mode": "FLAT", "background_mode": "FLAT",
                     "cell_resolution": "UNKNOWN"})
    metadata["provenance_note"] = (
        "True FF text flip media: capture = the run's true_ff.text_frame.v1 export; identity "
        "fields come from the headed receipt; " + str(metadata.get("provenance_note", "")))
    return metadata, ""


def _true_ff_text_flips_after_run(args: argparse.Namespace, *, out_dir: Path, receipt_path: Path,
                                  godot_log: Path, repo_provenance: dict) -> dict:
    """Default glyph flip detection for every FF run (FL-4896).

    1. The whole-run export is measured (RED/YELLOW/kept counts, receipt).
    2. For each F9 bundle this run recorded, after its detached finalizer
       completes, the export lines whose engine_frame has a PNG are measured
       again with --f9-bundle, so the instrument draws the RED/YELLOW GIF and
       HTML, and publishes them with capture identity from this run's receipt.
    Hand inspection stays optional: the media are there to open.
    """
    export = out_dir / TRUE_FF_TEXT_EXPORT_NAME
    flips_dir = out_dir / TRUE_FF_TEXT_FLIPS_DIR_NAME
    head = str(repo_provenance.get("git_head", ""))
    report: dict = {"export": str(export), "rc": 1, "reason": "", "run": None, "f9_windows": []}
    if not export.is_file() or export.stat().st_size == 0:
        report["reason"] = "true_ff_text_export_missing"
        return report
    run = _run_text_flip_instrument(export, stem=_true_ff_text_flips_stem(args, out_dir, head),
                                    out=flips_dir)
    report["run"] = run
    report["rc"], report["reason"] = run["rc"], run["reason"]
    for bundle in _f9_bundles_from_log(godot_log):
        window: dict = {"bundle": str(bundle), "rc": 1, "reason": ""}
        report["f9_windows"].append(window)
        status = _wait_for_f9_finalize(bundle, F9_BUNDLE_FINALIZE_WAIT_SECONDS)
        window["completion_status"] = status
        if status != "complete":
            window["reason"] = f"f9_bundle_not_finalized:{status}"
            continue
        try:
            engine_frames = _f9_png_engine_frames(bundle)
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as error:
            window["reason"] = f"f9_manifest_unreadable:{error}"
            continue
        window_export = flips_dir / f"{bundle.name}.f9_window.text_frames.jsonl"
        window["frames"] = _write_f9_window_export(export, engine_frames, window_export)
        if window["frames"] < 2:
            window["reason"] = f"f9_window_too_short:{window['frames']}_frames_with_png"
            continue
        metadata, reason = _true_ff_text_flip_publication_metadata(receipt_path, window_export)
        if metadata is None:
            window["reason"] = reason
            continue
        metadata_path = flips_dir / f"{bundle.name}.publication_metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        window.update(_run_text_flip_instrument(
            window_export, stem=_true_ff_text_flips_stem(args, out_dir, head, "f9-" + bundle.name),
            out=flips_dir, f9_bundle=bundle, metadata=metadata_path))
        window["bundle"] = str(bundle)
        if window["rc"] == 0 and not window["media"]:
            window["rc"], window["reason"] = 1, "f9_window_media_not_drawn"
    failed = [w["reason"] for w in report["f9_windows"] if w["rc"] != 0]
    if report["rc"] == 0 and failed:
        report["rc"], report["reason"] = 1, "f9_window_failed:" + ",".join(failed)
    return report


def _print_true_ff_text_flips(report: dict) -> None:
    """End-of-run lines: durable receipts, media paths and RED/YELLOW/kept counts."""
    def counts(result: dict | None) -> str:
        summary = (result or {}).get("summary") or {}
        return (f"frames={summary.get('frames', 0)} red={summary.get('red', 0)} "
                f"yellow={summary.get('yellow', 0)} kept={summary.get('carried', 0)} "
                f"returned={summary.get('returned', 0)} repeat_sites={summary.get('repeat_sites', 0)}")

    run = report.get("run") or {}
    print(f"[fl4359-headed] TRUE_FF_TEXT_FLIPS rc={report.get('rc')} reason={report.get('reason')} "
          f"{counts(run)} durable_receipt={run.get('durable_receipt') or '<none>'}", flush=True)
    for window in report.get("f9_windows") or []:
        print(f"[fl4359-headed] TRUE_FF_TEXT_FLIPS_F9 bundle={window.get('bundle')} rc={window.get('rc')} "
              f"reason={window.get('reason')} {counts(window)} "
              f"durable_receipt={window.get('durable_receipt') or '<none>'}", flush=True)
        for media in window.get("media") or []:
            print(f"[fl4359-headed] TRUE_FF_TEXT_FLIPS_MEDIA {media}", flush=True)


def _true_ff_text_flips_summary(text_flips: dict, *, args: argparse.Namespace, godot_rc: int,
                                analyzer_rc: int, receipt_path: Path, repo_provenance: dict) -> dict:
    """analysis_summary.json for an FF run: a measurement, never a pass."""
    return {
        "frame_count": int(((text_flips.get("run") or {}).get("summary") or {}).get("frames", 0) or 0),
        "analysis_skipped": False,
        "analyzer": "scripts/measure_true_ff_text_flips.py",
        "reason": text_flips.get("reason", ""),
        "true_ff_text_flips": text_flips,
        "run_classification": args.run_classification,
        "proof_classification": DIAGNOSTIC_PROOF_CLASSIFICATION,
        "diagnostic_only": True,
        "proof": "true_ff_text_row_temporal_measurement",
        "admissible": False,
        "pass": False,
        "proof_pass": False,
        "proof_reason": "true_ff_text_flips_measurement_has_no_acceptance_threshold",
        "presentation_profile": repo_provenance.get("presentation_profile", ""),
        "wrapper_git_head": repo_provenance.get("git_head", ""),
        "godot_rc": godot_rc,
        "analyzer_rc": analyzer_rc,
        "git_head": repo_provenance.get("git_head", ""),
        "git_dirty": bool(repo_provenance.get("git_dirty", False)),
        "provenance": {"source": "scripts/run_fl4359_headed_movement.py", "wrapper": repo_provenance,
                       "runtime": {"godot_rc": godot_rc, "analyzer_rc": analyzer_rc,
                                   "receipt_path": str(receipt_path)}},
    }


def _stitch_sweep_gif(out_dir: Path) -> tuple[str, str]:
    """Stitch <out>/frames/frame_00..04.png into <out>/sweep_5frames.gif.

    Returns `(gif_path, reason)`. `gif_path` is empty when nothing was written,
    and `reason` then names the missing frames or the stitch failure. This owner
    never fabricates a GIF path for a run that did not produce five frames.
    """
    frames_dir = out_dir / FRAME_PNG_DIR_NAME
    missing = [
        FRAME_PNG_NAME % index
        for index in range(FRAME_PNG_SEQUENCE_FRAMES)
        if not (frames_dir / (FRAME_PNG_NAME % index)).is_file()
    ]
    if missing:
        return "", "missing_frame_pngs:" + ",".join(missing)
    gif_path = out_dir / SWEEP_GIF_NAME
    stitch = subprocess.run(
        [
            sys.executable,
            str(STITCH_SWEEP_GIF_SCRIPT),
            "--arm",
            f"sweep={frames_dir}",
            "--out",
            str(gif_path),
            "--frame-ms",
            str(SWEEP_GIF_FRAME_MS),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    for line in (stitch.stdout + stitch.stderr).splitlines():
        if line.strip():
            print(f"[fl4359-headed] {line}", flush=True)
    if stitch.returncode != 0 or not gif_path.is_file():
        return "", f"stitch_failed_rc={stitch.returncode}"
    return str(gif_path), ""


def _forced_baseline_features(args: argparse.Namespace) -> str:
    """Return the baseline feature list actually sent to the Godot client.

    A pinned day-cycle phase is valid only when the renderer reads it. The
    effect reads the pin when `day_night_cycle` is forced, so every pinned
    invocation appends that feature at the same boundary that forwards the pin.
    """
    requested = str(getattr(args, "force_baseline_features", "") or "")
    day_cycle_time = getattr(args, "day_cycle_time", None)
    try:
        pinned_day_cycle = day_cycle_time is not None and float(day_cycle_time) >= 0.0
    except (TypeError, ValueError):
        pinned_day_cycle = False
    names = [name for name in (part.strip() for part in requested.split(",")) if name]
    # FL-4359: all proof routes, including menu and strafe, keep blur off.
    if FULL_GRID_ORACLE_REQUIRED_DISABLED_FEATURE not in names:
        names.append(FULL_GRID_ORACLE_REQUIRED_DISABLED_FEATURE)
    if pinned_day_cycle and FULL_GRID_ORACLE_REQUIRED_FEATURE not in names:
        names.append(FULL_GRID_ORACLE_REQUIRED_FEATURE)
    return ",".join(names)


# The obsolete FL-4359 A/B arms were physically deleted. Keep the tuple as the
# receipt ABI's explicit empty set so a stale caller cannot silently resurrect
# a second paint, geometry, or transition owner.
FL4359_ARM_OPT_IN_FLAGS: tuple[str, ...] = ()

# Arm flags that CANNOT share one process. Each entry is one mutually exclusive
# group; at most one member of a group may appear in a single argv. The b61
# producer is the single splat route (FL-4359 texel splatting), so no producer
# identity flags remain; the table is kept for future exclusive arms.
FL4359_MUTUALLY_EXCLUSIVE_ARM_GROUPS: tuple = ()


def _fl4359_arm_flag_conflicts(flags: list[str]) -> list[dict]:
    """Return every mutually exclusive arm group this argv arms more than once."""
    conflicts: list[dict] = []
    for group, reason in FL4359_MUTUALLY_EXCLUSIVE_ARM_GROUPS:
        armed = [flag for flag in group if flag in flags]
        if len(armed) > 1:
            conflicts.append({"armed": armed, "reason": reason})
    return conflicts


def _fl4359_arm_flags(args: argparse.Namespace) -> list[str]:
    """Return the FL-4359 arm opt-ins this invocation forwards, in table order.

    An empty list is the baseline control identity and is meaningful evidence in
    its own right: it says this run armed nothing.
    """
    forwarded = {
        str(value).split("=", 1)[0]
        for value in getattr(args, "extra_user_arg", []) or []
    }
    return [flag for flag in FL4359_ARM_OPT_IN_FLAGS if flag in forwarded]


def _uses_canonical_airplane_presentation(args: argparse.Namespace) -> bool:
    """Select World.gd's locked surface for the exact 32px airplane route."""
    return (
        getattr(args, "camera_mode", "") in ("plane_air", "plane_air_ortho")
        and int(getattr(args, "glyph_matrix_cell_px", 32)) == 32
        and not bool(getattr(args, "fl4359_historical_r6_replay", False))
    )


def _requested_presentation_profile_id(args: argparse.Namespace) -> str:
    """Return the runner-declared native presentation identity.

    Existing canonical exact-pose callers predate the profile option.  Their
    already-locked 3600x2252/32px route gets the one reviewed profile ID as a
    compatibility default; every other route must name its profile explicitly
    before it can enter the exact-pose contract.
    """
    requested = str(getattr(args, "presentation_profile", "") or "").strip()
    if requested:
        return requested
    if _full_grid_contract_mode(args) == FULL_GRID_CONTRACT_MODE_CAMERA_TURN:
        return GROUND_TURN_NATIVE_PRESENTATION_PROFILE
    raw_manifest = _profile_manifest_value(args)
    if isinstance(raw_manifest, dict):
        manifest_profile_id = raw_manifest.get("profile_id")
        if isinstance(manifest_profile_id, str) and manifest_profile_id.strip():
            return manifest_profile_id.strip()
    if (
        bool(getattr(args, "exact_pose_sweep", False))
        and _uses_canonical_airplane_presentation(args)
    ):
        return (TERMINAL_NATIVE_PRESENTATION_PROFILE if getattr(args, "cell_aspect", "square") == "terminal"
                else CANONICAL_NATIVE_PRESENTATION_PROFILE)
    return ""


def _project_file(relative_path: str, godot_project_dir: Path | None) -> Path:
    """"godot_project/<rel>" inside the selected project directory."""
    prefix = "godot_project/"
    if godot_project_dir is None or not relative_path.startswith(prefix):
        return ROOT / relative_path
    return godot_project_dir / relative_path[len(prefix):]


def _canonical_profile_spec(terminal: bool = False,
                            godot_project_dir: Path | None = None) -> PresentationProfileSpec:
    """Build the reviewed square profile from the bytes selected by the build.

    godot_project_dir: the executed project (snapshot replay reads the
    snapshot's atlases, not the checkout's); None keeps the checkout.
    """
    atlas_sources = []
    sources = (tuple((f"res://assets/glyphs/terminal/page_{i}.png",
                      f"godot_project/assets/glyphs/terminal/page_{i}.png") for i in range(3))
               if terminal else CANONICAL_NATIVE_ATLAS_SOURCES)
    for logical_path, relative_path in sources:
        source_path = _project_file(relative_path, godot_project_dir)
        digest, dimensions = _decoded_rgba8_identity(source_path)
        atlas_sources.append((logical_path, digest, dimensions))
    return PresentationProfileSpec(
        profile_id=TERMINAL_NATIVE_PRESENTATION_PROFILE if terminal else CANONICAL_NATIVE_PRESENTATION_PROFILE,
        projection="perspective",
        native_cell_size=(16, 32) if terminal else (32, 32),
        presentation_size=CANONICAL_AIRPLANE_PRESENTATION_SIZE,
        ansi_size=(225, 71) if terminal else CANONICAL_AIRPLANE_ANSI_SIZE,
        atlas_sources=tuple(atlas_sources),
    )


def _ground_turn_profile_spec(args: argparse.Namespace) -> PresentationProfileSpec:
    """Build the recovered R27 3840x2160 terminal presentation identity."""
    atlas_sources = []
    for i in range(3):
        logical_path = f"res://assets/glyphs/terminal/page_{i}.png"
        source_path = _selected_godot_project_dir(args) / f"assets/glyphs/terminal/page_{i}.png"
        digest, dimensions = _decoded_rgba8_identity(source_path)
        atlas_sources.append((logical_path, digest, dimensions))
    cell_w, cell_h = _requested_native_cell_size(args)
    width, height = GROUND_TURN_PRESENTATION_SIZE
    return PresentationProfileSpec(
        profile_id=GROUND_TURN_NATIVE_PRESENTATION_PROFILE,
        projection="perspective",
        native_cell_size=(cell_w, cell_h),
        presentation_size=GROUND_TURN_PRESENTATION_SIZE,
        ansi_size=(math.ceil(width / cell_w), math.ceil(height / cell_h)),
        atlas_sources=tuple(atlas_sources),
    )


def _requested_native_cell_size(args):
    height = int(getattr(args, "glyph_matrix_cell_px", 0))
    return (height // 2 if getattr(args, "cell_aspect", "square") == "terminal" else height, height)


def _profile_application_supported(args, profile):
    if _full_grid_contract_mode(args) == FULL_GRID_CONTRACT_MODE_CAMERA_TURN:
        return bool(
            profile
            and profile == _ground_turn_profile_spec(args)
            and args.camera_mode == "perspective"
            and args.exact_camera_projection == "perspective"
            and tuple(args.resolution) == GROUND_TURN_PRESENTATION_SIZE
            and getattr(args, "cell_aspect", "square") == "terminal"
            and int(getattr(args, "glyph_matrix_cell_px", 0)) == 32
        )
    expected = _canonical_profile_spec(getattr(args, "cell_aspect", "square") == "terminal",
                                       _selected_godot_project_dir(args))
    return bool(profile and profile == expected and args.camera_mode == "plane_air"
                and args.exact_camera_projection == "perspective"
                and int(getattr(args, "glyph_matrix_cell_px", 0)) == 32)


def _profile_manifest_value(args: argparse.Namespace) -> object:
    value = getattr(args, "presentation_profile_manifest", None)
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(Path(value).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
    return None


def _presentation_profile_parse(args: argparse.Namespace) -> tuple[PresentationProfileSpec | None, str]:
    """Parse exactly one expected profile, preserving a named parse failure."""
    raw = _profile_manifest_value(args)
    if raw is None:
        if getattr(args, "presentation_profile_manifest", None):
            return None, "presentation_profile_manifest_unreadable"
        if _full_grid_contract_mode(args) == FULL_GRID_CONTRACT_MODE_CAMERA_TURN:
            spec = _ground_turn_profile_spec(args)
            return spec, "" if _requested_presentation_profile_id(args) in {
                "",
                spec.profile_id,
            } else "presentation_profile_id_mismatch"
        if (
            bool(getattr(args, "exact_pose_sweep", False))
            and _uses_canonical_airplane_presentation(args)
        ):
            spec = _canonical_profile_spec(getattr(args, "cell_aspect", "square") == "terminal",
                                           _selected_godot_project_dir(args))
            return spec, "" if _requested_presentation_profile_id(args) in {
                "",
                spec.profile_id,
            } else "presentation_profile_id_mismatch"
        return None, "presentation_profile_manifest_required"
    try:
        spec = PresentationProfileSpec.from_mapping(raw)
    except ValueError as exc:
        return None, str(exc)
    requested = str(getattr(args, "presentation_profile", "") or "").strip()
    if requested and requested != spec.profile_id:
        return None, "presentation_profile_id_mismatch"
    return spec, ""


def _presentation_profile_spec(args: argparse.Namespace) -> PresentationProfileSpec | None:
    spec, _reason = _presentation_profile_parse(args)
    return spec


def _presentation_profile(args: argparse.Namespace) -> str:
    """Return the explicit presentation identity carried by durable artifacts."""
    if bool(getattr(args, "fl4359_historical_r6_replay", False)):
        return HISTORICAL_R6_PRESENTATION_PROFILE
    return _requested_presentation_profile_id(args)


def _size_tuple(value: object, *, keys: tuple[str, str] = ("w", "h")) -> tuple[int, int] | None:
    """Read exactly two integer dimensions from one producer-owned value."""
    def integer_pair(first: object, second: object) -> tuple[int, int] | None:
        if (
            isinstance(first, bool)
            or not isinstance(first, int)
            or isinstance(second, bool)
            or not isinstance(second, int)
        ):
            return None
        return int(first), int(second)

    if isinstance(value, dict):
        if keys[0] in value and keys[1] in value:
            return integer_pair(value[keys[0]], value[keys[1]])
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return integer_pair(value[0], value[1])
    return None


def _actual_presentation_facts_from_frame_trace(frame_trace: object) -> tuple[dict | None, str]:
    """Read one strict applied tuple from ``frame_trace``.

    The final field producer owns the field object; the surrounding jitter trace
    owns the camera projection enums. No wrapper, requested profile, expected
    projection, or alternate metadata alias can satisfy this reader. Keeping
    the contract narrow prevents contradictory receipts from being resolved by
    whichever value happens to appear first.
    """
    if not isinstance(frame_trace, dict):
        return None, "missing_frame_trace"
    final_paint_field = frame_trace.get("final_paint_field")
    if not isinstance(final_paint_field, dict):
        return None, "missing_frame_trace_final_paint_field"
    if final_paint_field.get("schema") not in ("fl4359.final_paint_field.v1", "fl4359.final_paint_field.v2", COMPACT_PRESENTATION_FIELD_SCHEMA):
        return None, "frame_trace_final_paint_field_schema_mismatch"
    if final_paint_field.get("status") != "valid":
        return None, "frame_trace_final_paint_field_status_invalid"
    raw_path = final_paint_field.get("raw_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None, "frame_trace_final_paint_raw_path_missing_or_invalid"
    frame_index = final_paint_field.get("frame_index")
    trace_frame_index = frame_trace.get("frame_index")
    if (
        isinstance(frame_index, bool)
        or not isinstance(frame_index, int)
        or isinstance(trace_frame_index, bool)
        or not isinstance(trace_frame_index, int)
        or frame_index != trace_frame_index
    ):
        return None, "frame_trace_final_paint_frame_index_mismatch"
    sequence_frame = final_paint_field.get("sequence_frame")
    trace_sequence_frame = frame_trace.get("sequence_frame")
    if (
        isinstance(sequence_frame, bool)
        or not isinstance(sequence_frame, int)
        or isinstance(trace_sequence_frame, bool)
        or not isinstance(trace_sequence_frame, int)
        or sequence_frame != trace_sequence_frame
    ):
        return None, "frame_trace_final_paint_sequence_frame_mismatch"
    camera_projection_enum = frame_trace.get("camera_projection_mode")
    camera_projection_mode = frame_trace.get("projection_mode")
    if isinstance(camera_projection_enum, bool) or camera_projection_enum not in {0, 1}:
        return None, "frame_trace_camera_projection_enum_missing_or_invalid"
    if isinstance(camera_projection_mode, bool) or camera_projection_mode not in {1, 2}:
        return None, "frame_trace_projection_mode_missing_or_invalid"
    # These are the producer's concrete enum identities. `projection_kind` is
    # owned by the final-paint producer (0=ortho, 1=perspective); the camera
    # contract uses Godot's enum (0=perspective, 1=ortho), while its local
    # projection mode is (1=ortho, 2=perspective). All three must agree.
    projection_kind = final_paint_field.get("projection_kind")
    field_projection_mode = final_paint_field.get("projection_mode")
    if isinstance(projection_kind, bool) or projection_kind not in {0, 1}:
        return None, "frame_trace_final_paint_projection_kind_missing_or_invalid"
    if isinstance(field_projection_mode, bool) or field_projection_mode not in {1, 2}:
        return None, "frame_trace_final_paint_projection_mode_missing_or_invalid"
    projection = "perspective" if camera_projection_enum == 0 else "ortho"
    expected_kind = 1 if projection == "perspective" else 0
    expected_mode = 2 if projection == "perspective" else 1
    if projection_kind != expected_kind or field_projection_mode != expected_mode:
        return None, "frame_trace_projection_identity_mismatch"
    if camera_projection_mode != expected_mode:
        return None, "frame_trace_camera_projection_mode_mismatch"
    native_cell_size = _size_tuple(final_paint_field.get("cell_size"))
    actual_image_size = _size_tuple(final_paint_field.get("actual_image_size"))
    presentation_size = _size_tuple(final_paint_field.get("presentation_size"))
    ansi_size = _size_tuple(final_paint_field.get("semantic_ansi_size"))
    if native_cell_size is None or any(value <= 0 for value in native_cell_size):
        return None, "frame_trace_cell_size_missing_or_invalid"
    if actual_image_size is None or any(value <= 0 for value in actual_image_size):
        return None, "frame_trace_actual_image_size_missing_or_invalid"
    if presentation_size is None or any(value <= 0 for value in presentation_size):
        return None, "frame_trace_presentation_size_missing_or_invalid"
    if ansi_size is None or any(value <= 0 for value in ansi_size):
        return None, "frame_trace_semantic_ansi_size_missing_or_invalid"
    atlas_identity = final_paint_field.get("atlas_identity")
    if atlas_identity is not None:
        if not isinstance(atlas_identity, dict) or atlas_identity.get(
            "schema"
        ) != "fl4359.native_atlas_identity.v1":
            return None, "frame_trace_atlas_identity_invalid"
        atlas_sources = atlas_identity.get("sources")
        if not isinstance(atlas_sources, list) or not atlas_sources:
            return None, "frame_trace_atlas_sources_missing"
        atlas_paths: set[str] = set()
        for source in atlas_sources:
            if not isinstance(source, dict):
                return None, "frame_trace_atlas_source_invalid"
            path = source.get("path")
            content_hash = source.get("sha256")
            hash_kind = source.get("hash_kind")
            dimensions = _size_tuple(source.get("dimensions"))
            if (
                not isinstance(path, str)
                or not path.strip()
                or path in atlas_paths
                or not isinstance(content_hash, str)
                or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None
                or hash_kind != "decoded_rgba8"
                or dimensions is None
                or any(value <= 0 for value in dimensions)
            ):
                return None, "frame_trace_atlas_source_hash_invalid"
            atlas_paths.add(path)
    field_sha256 = final_paint_field.get("raw_sha256")
    if not isinstance(field_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", field_sha256) is None:
        return None, "frame_trace_raw_sha256_missing_or_invalid"
    # The current producer has no applied orthographic scale witness. Preserve
    # that absence as an explicit fact; an orthographic expected profile will
    # therefore fail its scale check until the owning camera route publishes it.
    ortho_scale = frame_trace.get("ortho_scale")
    if projection == "ortho":
        if not _finite_number(ortho_scale) or float(ortho_scale) <= 0.0:
            return None, "frame_trace_ortho_scale_missing_or_invalid"
        ortho_scale = float(ortho_scale)
    elif ortho_scale is not None:
        return None, "frame_trace_perspective_ortho_scale_unexpected"
    return {
        "projection": projection,
        "camera_projection_mode": camera_projection_enum,
        "projection_mode": camera_projection_mode,
        "projection_kind": projection_kind,
        "raw_path": raw_path,
        "raw_sha256": field_sha256,
        "actual_image_size": actual_image_size,
        "frame_index": frame_index,
        "sequence_frame": sequence_frame,
        "native_cell_size": native_cell_size,
        "presentation_size": presentation_size,
        "ansi_size": ansi_size,
        "atlas_identity": atlas_identity,
        "field_sha256": field_sha256,
        "valid": True,
        "ortho_scale": ortho_scale,
    }, ""


def _actual_presentation_facts(receipt: dict) -> dict:
    """Read the one applied tuple exposed in a public runtime receipt."""
    facts, reason = _actual_presentation_facts_from_frame_trace(
        receipt.get("frame_trace") if isinstance(receipt, dict) else None
    )
    return facts or {"valid": False, "reason": reason}


def _presentation_profile_receipt_contract(
    receipt: dict,
    args: argparse.Namespace,
    *,
    capture_path: Path | None = None,
) -> dict:
    """Require a profile to match actual runtime dimensions and projection."""
    profile_id = _requested_presentation_profile_id(args)
    if not profile_id:
        return {
            "pass": True,
            "required": False,
            "reason": "presentation_profile_not_requested",
            "checks": {},
        }
    spec, parse_reason = _presentation_profile_parse(args)
    if spec is None:
        return {
            "pass": False,
            "required": True,
            "reason": parse_reason or "presentation_profile_manifest_invalid",
            "profile_id": profile_id,
            "checks": {"known_profile": False},
        }
    actual_rows: list[dict] = []
    actual_errors: list[str] = []
    if capture_path is not None:
        if not capture_path.is_file():
            actual_errors.append("capture_path_missing")
        else:
            with capture_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict) or row.get("row_type") != "fl4359_jitter_frame":
                        continue
                    sequence_frame = row.get("frame_id", row.get("sequence_frame"))
                    if sequence_frame not in range(FULL_GRID_REQUIRED_CAPTURE_FRAMES):
                        continue
                    facts, reason = _actual_presentation_facts_from_frame_trace(
                        row.get("frame_trace")
                    )
                    if facts is None:
                        actual_errors.append(f"frame_{sequence_frame}:{reason}")
                    else:
                        if facts.get("sequence_frame") != int(sequence_frame):
                            actual_errors.append(
                                f"frame_{sequence_frame}:actual_profile_sequence_frame_mismatch"
                            )
                        actual_rows.append({"frame_id": int(sequence_frame), **facts})
        frame_ids = [row["frame_id"] for row in actual_rows]
        if sorted(frame_ids) != list(range(FULL_GRID_REQUIRED_CAPTURE_FRAMES)):
            actual_errors.append("actual_profile_frames_must_be_exactly_0_to_4")
    else:
        facts = _actual_presentation_facts(receipt)
        if facts.get("valid") is not True:
            actual_errors.append(str(facts.get("reason", "invalid_frame_trace")))
        else:
            actual_rows.append(facts)
    expected_atlas_identity = spec.atlas_identity()
    expected_atlas_sources_available = all(
        isinstance(source, dict)
        and isinstance(source.get("sha256"), str)
        and bool(source.get("sha256"))
        for source in expected_atlas_identity.get("sources", [])
    )
    actual = actual_rows[0] if actual_rows else {}
    if actual_rows:
        baseline = actual_rows[0]
        for row in actual_rows[1:]:
            for key in (
                "projection",
                "camera_projection_mode",
                "native_cell_size",
                "presentation_size",
                "ansi_size",
                "atlas_identity",
                "ortho_scale",
            ):
                if row.get(key) != baseline.get(key):
                    actual_errors.append(f"actual_profile_drift:{key}")
    checks = {
        "known_profile": True,
        "expected_atlas_sources_available": expected_atlas_sources_available,
        "actual_frame_metadata": not actual_errors and bool(actual_rows),
        "runtime_projection": actual.get("projection") == spec.projection,
        "runtime_native_cell_size": actual.get("native_cell_size") == spec.native_cell_size,
        "runtime_presentation_size": actual.get("presentation_size") == spec.presentation_size,
        "runtime_ansi_size": actual.get("ansi_size") == spec.ansi_size,
        "runtime_atlas_identity": actual.get("atlas_identity") == expected_atlas_identity,
    }
    if spec.projection == "ortho":
        checks["runtime_ortho_scale"] = (
            spec.ortho_scale is not None
            and actual.get("ortho_scale") is not None
            and math.isclose(
                actual["ortho_scale"],
                spec.ortho_scale,
                rel_tol=0.0,
                abs_tol=1e-6,
            )
        )
    if actual_errors:
        checks["actual_frame_metadata"] = False
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "pass": not failed,
        "required": True,
        "reason": "presentation_profile_receipt_ok" if not failed else "presentation_profile_receipt_failed",
        "profile_id": profile_id,
        "expected": spec.as_dict(),
        "actual": actual,
        "actual_rows": actual_rows,
        "actual_errors": actual_errors,
        "checks": checks,
        "failed_checks": failed,
    }


def _presentation_field_capture_budget_valid(args: argparse.Namespace) -> bool:
    """Require the producer's full five-frame peak budget when armed."""
    return (
        not bool(getattr(args, "presentation_field_capture", False))
        or int(getattr(args, "presentation_field_capture_budget_bytes", 0))
        >= PRESENTATION_FIELD_REQUIRED_PEAK_BYTES
    )


def _presentation_field_capture_receipt_accepts(receipt: dict) -> bool:
    """Validate complete presentation transport, including native source joins."""
    if receipt.get("presentation_field_capture_requested") is not True:
        return False
    if receipt.get("presentation_field_capture_ok") is not True:
        return False
    field_sidecars = receipt.get("presentation_field_sidecars", [])
    compact = isinstance(field_sidecars, list) and bool(field_sidecars) and all(
        isinstance(sidecar, dict) and sidecar.get("schema") == COMPACT_PRESENTATION_FIELD_SCHEMA
        for sidecar in field_sidecars
    )
    expected_peak = COMPACT_PRESENTATION_FIELD_REQUIRED_PEAK_BYTES if compact else PRESENTATION_FIELD_REQUIRED_PEAK_BYTES
    if receipt.get("presentation_field_required_peak_bytes") != expected_peak:
        return False
    if receipt.get("presentation_field_native_source_content_required_peak_bytes") != NATIVE_SOURCE_CONTENT_REQUIRED_PEAK_BYTES:
        return False
    if receipt.get("presentation_field_reflection_lineage_required_peak_bytes") != REFLECTION_LINEAGE_REQUIRED_PEAK_BYTES:
        return False
    if receipt.get("presentation_field_native_sky_context_history_required_peak_bytes") != NATIVE_SKY_CONTEXT_HISTORY_REQUIRED_PEAK_BYTES:
        return False
    if receipt.get("presentation_field_generation_observer_required_peak_bytes") != GENERATION_OBSERVER_REQUIRED_PEAK_BYTES:
        return False
    if receipt.get("presentation_field_postprocess_capture_required_peak_bytes") != POSTPROCESS_TRACE_PEAK_BYTES:
        return False
    sidecars = receipt.get("presentation_field_sidecars")
    if not isinstance(sidecars, list) or len(sidecars) != len(PRESENTATION_CAPTURE_FRAME_IDS):
        return False
    frame_ids: list[int] = []
    for sidecar in sidecars:
        if not isinstance(sidecar, dict) or sidecar.get("status") != "valid":
            return False
        sequence_frame = sidecar.get("sequence_frame")
        if isinstance(sequence_frame, bool) or not isinstance(sequence_frame, int):
            return False
        if not _valid_sha256(sidecar.get("raw_sha256")):
            return False
        if sidecar.get("schema") == COMPACT_PRESENTATION_FIELD_SCHEMA:
            policy = sidecar.get("applied_policy", {})
            if not compact or not isinstance(policy, dict) or (
                policy.get("status"), policy.get("mode"), policy.get("projection")
            ) != ("applied", "FF", "perspective"):
                return False
            if not _valid_sha256(sidecar.get("witness_sha256")) or not sidecar.get("witness_path"):
                return False
            if sidecar.get("fixed_mask") != 238136 or sidecar.get("compact_pixel_stride_words") != 14:
                return False
            if sidecar.get("record_layout") != "v3_header_slot_table_prefix_dynamic_fields":
                return False
        frame_ids.append(sequence_frame)
    if sorted(frame_ids) != list(PRESENTATION_CAPTURE_FRAME_IDS):
        return False
    native_sidecars = receipt.get("native_source_content_sidecars")
    if not isinstance(native_sidecars, list) or len(native_sidecars) != len(PRESENTATION_CAPTURE_FRAME_IDS):
        return False
    native_ids: list[int] = []
    perspective = receipt.get("camera_mode") in ("plane_air", "perspective")
    for sidecar in native_sidecars:
        if not isinstance(sidecar, dict):
            return False
        sequence_frame = sidecar.get("sequence_frame")
        if isinstance(sequence_frame, bool) or not isinstance(sequence_frame, int):
            return False
        native_ids.append(sequence_frame)
        if not perspective:
            if sidecar.get("status") not in ("unsupported_orthographic_raw_legacy_only", "not_requested_presentation_field_disabled"):
                return False
            continue
        if sidecar.get("status") != "valid":
            return False
        if sidecar.get("schema") != "fl4359.native_source_content.v1":
            return False
        if sidecar.get("record_bytes") != NATIVE_SOURCE_CONTENT_RECORD_BYTES:
            return False
        record_count = sidecar.get("record_count")
        byte_count = sidecar.get("bytes")
        if (isinstance(record_count, bool) or not isinstance(record_count, int)
                or record_count <= 0 or isinstance(byte_count, bool)
                or not isinstance(byte_count, int)
                or byte_count != record_count * NATIVE_SOURCE_CONTENT_RECORD_BYTES):
            return False
        if isinstance(sidecar.get("probe_size"), bool) or not isinstance(sidecar.get("probe_size"), int) or sidecar.get("probe_size") <= 0:
            return False
        if isinstance(sidecar.get("layer_count"), bool) or not isinstance(sidecar.get("layer_count"), int) or sidecar.get("layer_count") <= 0:
            return False
        if isinstance(sidecar.get("epoch"), bool) or not isinstance(sidecar.get("epoch"), int) or sidecar.get("epoch") <= 0:
            return False
        if sidecar.get("payload_binding") != "post_effect_display_payloads":
            return False
        if sidecar.get("record_layout") != [
                "key", "identity", "world", "source_revisions", "anchor_source",
                "payload", "colour", "normal_depth", "surface_meta"]:
            return False
        if sidecar.get("colour_field") != "effective_geometry_bank_colour_vec4":
            return False
        if sidecar.get("colour_semantics") != "actor_uv_coverage_subkind_or_meadow_vegetation_pose_vec4":
            return False
        if sidecar.get("reflection_status") != "captured_selected_water_reflection_facts":
            return False
        if sidecar.get("reflection_plan") != (
                "water_v3_requested_bank_address_resolves_product_owned_append_only_fact_"
                "without_native_attachment_reload"):
            return False
        if sidecar.get("reflection_facts_schema") != "fl4359.native_reflection_facts.v3.product_lineage":
            return False
        if sidecar.get("reflection_facts_status") != "captured_selected_water_reflection_facts":
            return False
        if sidecar.get("reflection_binding_source") != "PerspectiveProbePipeline.reflection_lineage_receipt":
            return False
        if sidecar.get("reflection_address_law") != (
                "product_capture_adapter.fl4359_captured_reflected_material(address)"):
            return False
        if sidecar.get("reflection_generation_lifetime") != "product_append_only_flight_fact":
            return False
        if sidecar.get("reflection_record_bytes") != 64:
            return False
        if sidecar.get("reflection_record_layout") != [
                "key", "identity_106", "normal_bits_107", "meta_108"]:
            return False
        if (not isinstance(sidecar.get("reflection_record_count"), int)
                or sidecar.get("reflection_record_count") < 0
                or sidecar.get("reflection_record_count") > 262144
                or sidecar.get("reflection_bytes") != sidecar.get("reflection_record_count") * 64):
            return False
        if (type(sidecar.get("reflection_lineage_epoch")) is not int
                or sidecar.get("reflection_lineage_epoch") <= 0
                or type(sidecar.get("reflection_lineage_capacity")) is not int
                or sidecar.get("reflection_lineage_capacity") != REFLECTION_LINEAGE_CAPACITY
                or type(sidecar.get("reflection_lineage_table_bytes")) is not int
                or sidecar.get("reflection_lineage_table_bytes") != REFLECTION_LINEAGE_TABLE_BYTES
                or sidecar.get("probe_size") > 384 or sidecar.get("layer_count") != 18
                or type(sidecar.get("reflection_lineage_map_bytes")) is not int
                or sidecar.get("reflection_lineage_map_bytes") != sidecar["probe_size"] ** 2 * 18 * 4):
            return False
        if not isinstance(sidecar.get("reflection_path"), str) or not sidecar.get("reflection_path"):
            return False
        if not _valid_sha256(sidecar.get("reflection_sha256")):
            return False
        sky_context = sidecar.get("sky_context")
        if (not isinstance(sky_context, dict)
                or sky_context.get("schema") != "fl4359.native_sky_context.v1"
                or sky_context.get("status") != "valid"
                or sky_context.get("capture_boundary") != "same_frame_paint_completion"
                or sky_context.get("source_production_owner") != "product_capture_adapter.fl4359_texel_shade"
                or sky_context.get("source_context_join") != "same_frame_frozen_source_paint_state"
                or sky_context.get("render_frame_index") != sidecar.get("render_frame_index")
                or sky_context.get("frame_index") != sidecar.get("frame_index")
                or sky_context.get("sequence_frame") != sidecar.get("sequence_frame")):
            return False
        if not _valid_sha256(sidecar.get("raw_sha256")):
            return False
        join = sidecar.get("join")
        if not isinstance(join, dict):
            return False
        if any(join.get(key) != sidecar.get(source_key) for key, source_key in (
            ("sequence_frame", "sequence_frame"),
            ("frame_index", "frame_index"),
            ("request_id", "request_id"),
            ("render_frame_index", "render_frame_index"),
            ("epoch", "epoch"),
        )):
            return False
    return sorted(native_ids) == list(PRESENTATION_CAPTURE_FRAME_IDS)


def _historical_r6_replay_identity_reason(args: argparse.Namespace) -> str:
    """Fail closed unless the opt-in replay names the complete native R6 tuple."""
    if not bool(getattr(args, "fl4359_historical_r6_replay", False)):
        return ""
    exact_values = (
        ("camera_mode", getattr(args, "camera_mode", ""), "plane_air"),
        ("godot_window_mode", getattr(args, "godot_window_mode", ""), "fullscreen"),
        ("glyph_matrix_cell_px", getattr(args, "glyph_matrix_cell_px", None), 32),
        ("move_frames", getattr(args, "move_frames", None), 120),
        ("settle_frames", getattr(args, "settle_frames", None), 2),
        ("after_frames", getattr(args, "after_frames", None), 2),
        ("plane_circle_frames", getattr(args, "plane_circle_frames", None), 12),
        ("airplane_metadata_frame_cap", getattr(args, "airplane_metadata_frame_cap", None), 12),
        ("plane_circle_rate", getattr(args, "plane_circle_rate", None), 0.26),
        ("plane_throttle", getattr(args, "plane_throttle", None), 0.37),
        ("plane_settle_throttle", getattr(args, "plane_settle_throttle", None), 0.37),
        ("plane_pitch_base", getattr(args, "plane_pitch_base", None), 0.02),
        ("plane_pitch_amp", getattr(args, "plane_pitch_amp", None), 0.03),
        ("plane_roll_base", getattr(args, "plane_roll_base", None), -0.12),
        ("plane_roll_amp", getattr(args, "plane_roll_amp", None), 0.04),
        ("plane_fire_frames", getattr(args, "plane_fire_frames", None), 0),
        ("turn_frames", getattr(args, "turn_frames", None), 8),
        ("turn_action", getattr(args, "turn_action", None), "rotate_left"),
        ("jitter_capture_frame_limit", getattr(args, "jitter_capture_frame_limit", None), 5),
        ("jitter_capture_world_sample_stride", getattr(args, "jitter_capture_world_sample_stride", None), 4),
        ("jitter_capture_drain_deadline_frames", getattr(args, "jitter_capture_drain_deadline_frames", None), 120),
    )
    for name, actual, expected in exact_values:
        if actual != expected:
            return f"{name}_must_equal_{expected!r}"
    resolution = getattr(args, "resolution", None)
    if tuple(resolution or ()) != HISTORICAL_R6_PRESENTATION_SIZE:
        return "resolution_must_equal_3840x2160"
    projection = getattr(args, "exact_camera_projection", "")
    if projection != "perspective":
        return "exact_camera_projection_must_equal_perspective"
    product_time = getattr(args, "product_time", None)
    if product_time is None or not math.isclose(float(product_time), 17.0, rel_tol=0.0, abs_tol=1e-9):
        return "product_time_must_equal_17"
    day_cycle_time = getattr(args, "day_cycle_time", None)
    if day_cycle_time is None or not math.isclose(float(day_cycle_time), 0.75, rel_tol=0.0, abs_tol=1e-9):
        return "day_cycle_time_must_equal_0.75"
    if not bool(getattr(args, "allow_large_jitter_capture", False)):
        return "allow_large_jitter_capture_required"
    if bool(getattr(args, "skip_jitter_analysis", False)):
        return "skip_jitter_analysis_forbidden"
    if getattr(args, "run_classification", RUN_CLASSIFICATION_MOVEMENT_PROOF) != RUN_CLASSIFICATION_MOVEMENT_PROOF:
        return "run_classification_must_equal_movement-proof"
    if getattr(args, "capture_side", "current") != "current":
        return "capture_side_must_equal_current"
    return ""


def _historical_r6_replay_receipt_accepts(receipt: dict) -> bool:
    """Admit the native R6 surface without weakening the canonical receipt gate."""
    capture_window = receipt.get("capture_window")
    if not isinstance(capture_window, dict):
        return False
    viewport = receipt.get("viewport_size")
    ansi = receipt.get("ansi_size")
    display = receipt.get("display")
    if not isinstance(viewport, dict) or not isinstance(ansi, dict) or not isinstance(display, dict):
        return False
    screen_size = capture_window.get("screen_size")
    window_size = capture_window.get("window_size")
    run_identity = receipt.get("run_identity")
    run_identity = run_identity if isinstance(run_identity, dict) else {}
    final_metadata = receipt.get("final_render_metadata")
    final_metadata = final_metadata if isinstance(final_metadata, dict) else {}
    cell_dims = final_metadata.get("cell_dims")
    cell_dims = cell_dims if isinstance(cell_dims, dict) else {}
    glyph_matrix_cell_px = receipt.get("glyph_matrix_cell_px")
    if glyph_matrix_cell_px is None:
        glyph_matrix_cell_px = run_identity.get("glyph_matrix_cell_px")
    if glyph_matrix_cell_px is None:
        glyph_matrix_cell_px = cell_dims.get("cell_w")
    expected_size = {"w": HISTORICAL_R6_PRESENTATION_SIZE[0], "h": HISTORICAL_R6_PRESENTATION_SIZE[1]}
    expected_screen_size = {"x": HISTORICAL_R6_PRESENTATION_SIZE[0], "y": HISTORICAL_R6_PRESENTATION_SIZE[1]}
    return (
        receipt.get("camera_mode") == "plane_air"
        and receipt.get("canonical_presentation_requested") is False
        and receipt.get("capture_window_mode_requested") == "fullscreen"
        and receipt.get("presentation_surface") == "fullscreen"
        and capture_window.get("actual_fullscreen") is True
        and capture_window.get("actual_mode") == "exclusive_fullscreen"
        and screen_size == expected_screen_size
        and window_size == expected_screen_size
        and viewport == expected_size
        and display.get("window_size") == expected_size
        and ansi == {"w": HISTORICAL_R6_ANSI_SIZE[0], "h": HISTORICAL_R6_ANSI_SIZE[1]}
        and glyph_matrix_cell_px == 32
        and math.isclose(_float_value(receipt.get("product_time_pin_seconds"), -1.0), 17.0, rel_tol=0.0, abs_tol=1e-6)
        and receipt.get("product_time_pin_valid") is True
    )


def _reconcile_receipt_with_analyzer(receipt: dict, analyzer_rc: int) -> dict:
    """Reconcile runtime movement truth with the external analyzer outcome."""
    runtime_movement_pass = bool(receipt.get("pass", False))
    runtime_movement_reason = receipt.get("reason")
    analyzer_rc = int(analyzer_rc)
    analyzer_failure_reason = (
        None if analyzer_rc == 0 else f"analyzer_failed_rc_{analyzer_rc}"
    )
    receipt["runtime_movement_pass"] = runtime_movement_pass
    receipt["runtime_movement_reason"] = runtime_movement_reason
    receipt["analyzer_rc"] = analyzer_rc
    receipt["analyzer_pass"] = analyzer_rc == 0
    receipt["analyzer_failure_reason"] = analyzer_failure_reason
    if analyzer_rc != 0:
        receipt["pass"] = False
        if runtime_movement_pass is True or not runtime_movement_reason:
            receipt["reason"] = analyzer_failure_reason
    return receipt


def _run_classification_contract_reason(
    run_classification: str,
    skip_jitter_analysis: bool,
    fl4216_temporal_capture: bool = False,
) -> str:
    """Keep classification authority separate from evidence collection."""
    if fl4216_temporal_capture and run_classification != RUN_CLASSIFICATION_DIAGNOSTIC_ONLY:
        return (
            "FL-4216 temporal capture requires explicit diagnostic-only classification; "
            "it cannot be represented as FL-4359 movement proof"
        )
    if run_classification == RUN_CLASSIFICATION_MOVEMENT_PROOF:
        if skip_jitter_analysis:
            return (
                "movement-proof classification requires actual jitter/shimmer analysis; "
                "use --run-classification diagnostic-only for bounded static evidence"
            )
        return ""
    if run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY:
        # FL-4359/FL-4775: a diagnostic may collect the same Product, jitter,
        # and shimmer observations needed to falsify a candidate. Classification
        # below forces every such result to non-proof regardless of gate values.
        return ""
    return f"unsupported run classification: {run_classification}"


def _summary_classification_is_movement_proof(summary: dict) -> bool:
    """Require explicit proof identity instead of inferring it from a route."""
    return (
        summary.get("run_classification") == RUN_CLASSIFICATION_MOVEMENT_PROOF
        and summary.get("proof_classification") == RUN_CLASSIFICATION_MOVEMENT_PROOF
        and summary.get("analysis_skipped") is not True
        and summary.get("admissible") is True
        and summary.get("proof_pass") is True
        and summary.get("pass") is True
    )


def _summary_is_diagnostic_only(summary: dict) -> bool:
    """Make diagnostic artifacts non-proof whether analysis ran or was skipped."""
    return (
        summary.get("run_classification") == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
        and summary.get("proof_classification") == DIAGNOSTIC_PROOF_CLASSIFICATION
        and summary.get("diagnostic_only") is True
        and summary.get("admissible") is False
        and summary.get("proof_pass") is False
        and summary.get("pass") is False
    )


def _analyzed_diagnostic_is_nonproof(
    summary: dict,
    receipt: dict,
    *,
    skip_jitter_analysis: bool,
    run_classification: str,
) -> bool:
    """Admit analyzed evidence only as an explicitly non-proof diagnostic."""
    return (
        run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
        and not skip_jitter_analysis
        and summary.get("analysis_skipped") is not True
        and int(summary.get("frame_count", 0) or 0) > 0
        and receipt.get("pass") is True
        and _summary_is_diagnostic_only(summary)
    )


def _bounded_static_diagnostic_accepts_analysis_skip(
    summary: dict,
    receipt: dict,
    *,
    skip_jitter_analysis: bool,
    run_classification: str,
    exact_pose_capture: bool,
) -> bool:
    """Admit only an explicitly classified, exact-pose static capture."""
    return (
        run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
        and exact_pose_capture
        and skip_jitter_analysis
        and summary.get("analysis_skipped") is True
        and summary.get("reason") == "canonical_png_grid_capture_only"
        and receipt.get("pass") is True
    )


def _context_preflight(args: argparse.Namespace) -> tuple[bool, str]:
    """Require current native FL context before any headed process launch."""
    if bool(getattr(args, "preflight_only", False)):
        return True, "preflight_only"
    if bool(getattr(args, "fl4216_temporal_capture", False)):
        fl_id = "FL-4216"
        task = (
            "selected execution path=diagnostic-only FL-4216 same-process temporal capture. "
            "Require exact pose identity, same-process temporal frames, timing, and runtime "
            "truth. Exclude FL-4359 movement proof, skipped analysis as jitter proof, "
            "canonical static captures as movement evidence, and performance-only receipts."
        )
    else:
        fl_id = "FL-4359"
        run_classification = getattr(
            args,
            "run_classification",
            RUN_CLASSIFICATION_MOVEMENT_PROOF,
        )
        if _stationary_calibration_enabled(args):
            task = (
                "selected execution path=stationary_terminal_reprojection_calibration. "
                "Require the canonical plane_air perspective route, airborne server "
                "identity, five contiguous full-grid frames, authority continuity, "
                "and fail-closed pose/matrix/revision/Product-time stability. "
                "Exclude movement closure, renderer acceptance, skipped analysis, "
                "and alternate exact-pose routes."
            )
        else:
            task = (
                f"selected execution path={run_classification}. "
                "Require actual user-reachable movement, jitter, shimmer, and bounce analysis "
                "with admissible required artifacts. Exclude skipped analysis, "
                "canonical_png_grid_capture_only, static exact-pose captures, and "
                "performance-only receipts."
            )
    command = [
        sys.executable,
        str(ROOT / "scripts" / "analyze_runs.py"),
        "fl",
        "context",
        fl_id,
        "--task",
        task,
        "--json",
    ]
    result = subprocess.run(
        command,
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        errors="replace",
    )
    output = f"{result.stdout}\n{result.stderr}".strip()
    if result.returncode != 0:
        return False, (
            f"native context preflight blocked for {fl_id} rc={result.returncode}: "
            f"{output[-1200:]}"
        )
    try:
        receipt = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        return False, f"native context preflight returned invalid JSON for {fl_id}: {exc}"
    native = receipt.get("native") if isinstance(receipt, dict) else None
    if (
        receipt.get("ok") is not True
        or not isinstance(native, dict)
        or native.get("authority") != "native_failure_log"
        or native.get("card", {}).get("fl") != fl_id
    ):
        return False, f"native context preflight omitted current {fl_id} authority"
    external = receipt.get("external_context_channel")
    external_state = (
        f"; external={external.get('status')}:{external.get('error')}"
        if isinstance(external, dict)
        else ""
    )
    return True, f"native context preflight passed for {fl_id}{external_state}"


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _launcher_log_hand_facts(godot_log: Path) -> dict:
    """Extract installed-hand facts from the headed launcher log only.

    The external driver observes pose/camera and Product publication. It does
    not claim that an actor row is the retained hand. The hand identity and
    SceneGeometryDB parity remain launcher-log facts, fail-closed when absent.
    """
    text = godot_log.read_text() if godot_log.exists() else ""
    female_match = re.search(r"FEMALE_HAND_STATUE_LOADED\s+faces=(\d+)", text)
    geometry_match = re.search(r"SCENE_GEOMETRY_DB\s+[^\n]*parity_ok=(true|false)", text)
    faces = int(female_match.group(1)) if female_match else None
    parity_ok = geometry_match.group(1) == "true" if geometry_match else False
    return {
        "source": "godot_client.log",
        "female_hand_statue_loaded": female_match is not None,
        "female_hand_faces": faces,
        "female_hand_faces_expected": 8082,
        "scene_geometry_db_line_present": geometry_match is not None,
        "scene_geometry_db_parity_ok": parity_ok,
        "pass": faces == 8082 and parity_ok,
    }


def _launcher_log_authority_tick_facts(
    godot_log: Path,
    runtime_queue: object = None,
    runtime_window: object = None,
) -> dict:
    """Extract FL-4265 authority continuity from runtime truth plus the log.

    Overflow log lines prove a failure, but their absence never proved zero.
    The driver now snapshots ColyseusGameClient's counters at run completion;
    that owned counter is the positive zero-drop witness.
    """
    text = godot_log.read_text() if godot_log.exists() else ""
    matches = re.findall(
        r"FL-4265 authority snapshot queue overflow:\s*"
        r"limit=(\d+) dropped_server_tick=(\d+) total_dropped=(\d+)",
        text,
    )
    dropped_ticks = [int(match[1]) for match in matches]
    total_dropped = max((int(match[2]) for match in matches), default=0)
    queue_limit = int(matches[-1][0]) if matches else None
    queue = runtime_queue if isinstance(runtime_queue, dict) else {}
    queue_dropped = queue.get("dropped")
    queue_present = isinstance(queue_dropped, (int, float))
    final_dropped = int(queue_dropped) if queue_present else total_dropped
    window = runtime_window if isinstance(runtime_window, dict) else {}
    window_dropped = window.get("dropped_delta")
    window_present = bool(window.get("complete")) and isinstance(
        window_dropped, (int, float)
    )
    effective_dropped = int(window_dropped) if window_present else final_dropped
    temporal_claim_valid = bool(
        window_present
        and window.get("temporal_claim_valid") is True
        and isinstance(window.get("ingest_seq_delta"), (int, float))
        and int(window["ingest_seq_delta"]) > 0
        and queue_present
        and final_dropped == 0
    )
    return {
        "schema": "fl4359.authority_tick_continuity.v1",
        "source": "runtime_measurement_window_and_final_queue",
        "queue_overflow_lines": len(matches),
        "queue_limit": queue_limit,
        "dropped_server_ticks": dropped_ticks,
        "total_dropped": final_dropped,
        "measurement_window_dropped": effective_dropped,
        "final_total_dropped": final_dropped,
        "post_window_dropped": max(0, final_dropped - effective_dropped),
        "runtime_queue_summary": queue,
        "runtime_measurement_window": window,
        "temporal_claim_valid": temporal_claim_valid,
        "present": window_present and queue_present,
        "reason": (
            "authority_tick_continuity_zero_drop_window"
            if temporal_claim_valid
            else "authority_snapshot_queue_overflow"
            if final_dropped > 0
            else "authority_tick_continuity_measurement_window_missing_or_unclean"
        ),
    }


def _effective_analyzer_proof_profile(args: argparse.Namespace) -> str:
    requested = str(getattr(args, "analyzer_proof_profile", "auto"))
    if requested in {"same_frame_oracle_v1", "full_grid_temporal_oracle_v1"}:
        return requested
    if args.camera_mode in ("plane_air", "plane_air_ortho"):
        return "airplane_temporal_v1"
    return "general"


def _prepare_runtime_receipt(
    receipt: dict,
    *,
    args: argparse.Namespace,
    godot_log: Path,
    repo_provenance: dict,
) -> dict:
    """Normalize proof identity before the analyzer reads the receipt."""
    runtime_profile = str(receipt.get("proof_profile", ""))
    effective_profile = _effective_analyzer_proof_profile(args)
    receipt["runtime_route_profile"] = runtime_profile
    # FL-4873: the F10 view requested for the recorded frames (--f10-view).
    receipt["f10_view_requested"] = getattr(args, "f10_view", "") or "NORMAL (default, not set)"
    receipt["runner_presentation_profile_id"] = _requested_presentation_profile_id(args)
    requested_presentation_spec = _presentation_profile_spec(args)
    receipt["runner_presentation_profile_spec"] = (
        requested_presentation_spec.as_dict()
        if requested_presentation_spec is not None
        else None
    )
    receipt["requested_analyzer_proof_profile"] = str(
        getattr(args, "analyzer_proof_profile", "auto")
    )
    receipt["effective_proof_profile"] = effective_profile
    receipt["proof_profile"] = effective_profile
    receipt["proof_profile_identity_valid"] = bool(runtime_profile)
    if effective_profile == "full_grid_temporal_oracle_v1":
        # The analyzer's celestial-time gate reads the runtime receipt, not
        # only the wrapper command. Preserve the fixed phase at that boundary.
        receipt["requested_day_cycle_time"] = getattr(args, "day_cycle_time", None)
        if "day_cycle_time" not in receipt:
            receipt["day_cycle_time"] = getattr(args, "day_cycle_time", None)
    receipt["authority_tick_continuity"] = _launcher_log_authority_tick_facts(
        godot_log,
        receipt.get("authority_snapshot_queue"),
        receipt.get("authority_snapshot_queue_window"),
    )
    receipt["wrapper_git_head"] = repo_provenance.get("git_head", "")
    receipt["worktree_diff_sha256"] = repo_provenance.get(
        "worktree_diff_sha256", ""
    )
    run_identity = receipt.get("run_identity")
    if not isinstance(run_identity, dict):
        run_identity = {}
    run_identity["build_head"] = run_identity.get("build_head") or repo_provenance.get(
        "git_head"
    )
    run_identity["build_dirty"] = bool(
        run_identity.get("build_dirty", repo_provenance.get("git_dirty", False))
    )
    run_identity["build_worktree_diff_sha256"] = (
        run_identity.get("build_worktree_diff_sha256")
        or repo_provenance.get("worktree_diff_sha256", "")
    )
    run_identity["source_identity_complete"] = bool(
        run_identity.get("build_head")
        and run_identity.get("build_worktree_diff_sha256")
    )
    receipt["run_identity"] = run_identity
    return receipt


def _same_run_temporal_performance_gate(
    receipt: object,
    summary: object,
    *,
    temporal_gate_pass: bool | None = None,
    source_content_capture_pass: bool | None = None,
    positive_comparable_population_pass: bool | None = None,
) -> dict[str, object]:
    """Join temporal verdict, post-draw timing, and final Canvas by exact IDs.

    The measured interval includes the full-grid recorder, native-frame observer,
    ToneMap snapshots, and Canvas provenance. It can reject a slow instrumented
    proof run, but it is never promoted to an uninstrumented 60 FPS result.
    """
    errors: list[str] = []
    receipt_dict = receipt if isinstance(receipt, dict) else {}
    summary_dict = summary if isinstance(summary, dict) else {}
    timing = receipt_dict.get("same_run_temporal_performance_timing")
    timing = timing if isinstance(timing, dict) else {}
    if timing.get("schema") != SAME_RUN_TEMPORAL_PERFORMANCE_TIMING_SCHEMA:
        errors.append("timing_schema_mismatch")
    if (
        timing.get("measurement_classification")
        != SAME_RUN_TEMPORAL_PERFORMANCE_CLASSIFICATION
    ):
        errors.append("measurement_classification_mismatch")
    if timing.get("includes_sparse_capture_overhead") is not True:
        errors.append("sparse_capture_overhead_not_declared")
    if timing.get("includes_native_frame_observer_overhead") is not True:
        errors.append("native_observer_overhead_not_declared")
    if timing.get("includes_canvas_and_tonemap_capture_overhead") is not True:
        errors.append("canvas_tonemap_overhead_not_declared")
    if timing.get("per_present_frame_timing_complete") is not False or timing.get(
        "wall_interval_semantics"
    ) != "selected_capture_cadence_including_intervening_post_draws":
        errors.append("capture_cadence_misclassified_as_per_present_timing")
    if timing.get("continuous_post_draw_timing_status") != "open" or timing.get(
        "continuous_post_draw_timing_reason"
    ) != "selected_capture_rows_do_not_cover_intervening_draws":
        errors.append("continuous_post_draw_timing_not_left_open")
    if timing.get("clean_60fps_acceptance_eligible") is not False:
        errors.append("instrumented_timing_misclassified_as_clean_60fps")

    if source_content_capture_pass is None:
        native_source_sidecars = receipt_dict.get("native_source_content_sidecars")
        source_content_capture_pass = (
            _presentation_field_capture_receipt_accepts(receipt_dict)
            and isinstance(native_source_sidecars, list)
            and len(native_source_sidecars) == 5
            and all(
                isinstance(sidecar, dict)
                and sidecar.get("status") == "valid"
                and isinstance(sidecar.get("record_count"), int)
                and not isinstance(sidecar.get("record_count"), bool)
                and sidecar["record_count"] > 0
                for sidecar in native_source_sidecars
            )
        )
    if source_content_capture_pass is not True:
        errors.append("actual_native_source_content_capture_missing")
    continuous_colour = _dict_value(
        summary_dict.get("continuous_product_colour_stability")
    )
    if positive_comparable_population_pass is None:
        positive_comparable_population_pass = (
            summary_dict.get("presentation_observer_population_accounting_valid")
            is True
            and isinstance(summary_dict.get("total_comparable_cells"), int)
            and not isinstance(summary_dict.get("total_comparable_cells"), bool)
            and summary_dict["total_comparable_cells"] > 0
            and isinstance(summary_dict.get("comparable_pair_count"), int)
            and not isinstance(summary_dict.get("comparable_pair_count"), bool)
            and summary_dict["comparable_pair_count"] > 0
            and continuous_colour.get("population_accounting_valid") is True
            and isinstance(continuous_colour.get("comparable_pair_count"), int)
            and not isinstance(continuous_colour.get("comparable_pair_count"), bool)
            and continuous_colour["comparable_pair_count"] > 0
            and isinstance(
                continuous_colour.get("min_comparable_samples_per_pair"), int
            )
            and not isinstance(
                continuous_colour.get("min_comparable_samples_per_pair"), bool
            )
            and continuous_colour["min_comparable_samples_per_pair"] > 0
        )
    if positive_comparable_population_pass is not True:
        errors.append("positive_comparable_population_missing")

    schedule = receipt_dict.get("capture_schedule")
    schedule = schedule if isinstance(schedule, dict) else {}
    expected_count = schedule.get("effective_total_frames")
    if isinstance(expected_count, bool) or not isinstance(expected_count, int):
        expected_count = -1
    if expected_count <= 0:
        errors.append("expected_record_count_missing")
    if timing.get("expected_record_count") != expected_count:
        errors.append("timing_expected_record_count_mismatch")

    records_value = timing.get("records")
    records = records_value if isinstance(records_value, list) else []
    if not records:
        errors.append("timing_records_empty")
    if len(records) != expected_count:
        errors.append("timing_record_count_mismatch")
    if timing.get("record_count") != len(records):
        errors.append("timing_record_count_summary_mismatch")

    active_count = schedule.get("effective_active_frames")
    if isinstance(active_count, bool) or not isinstance(active_count, int):
        active_count = -1
    durations: list[float] = []
    record_by_sequence: dict[int, dict] = {}
    matrix_keys: set[str] = set()
    previous_end: int | None = None
    post_draw_frames: set[int] = set()
    pixel_render_frames: set[int] = set()
    request_ids: set[int] = set()
    for index, record_value in enumerate(records):
        if not isinstance(record_value, dict):
            errors.append("timing_record_not_dictionary")
            continue
        record = record_value
        if record.get("schema") != SAME_RUN_TEMPORAL_PERFORMANCE_RECORD_SCHEMA:
            errors.append("timing_record_schema_mismatch")
        sequence = record.get("sequence_frame")
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            errors.append("sequence_frame_untyped")
            continue
        if sequence != index or sequence in record_by_sequence:
            errors.append("sequence_frame_gap_duplicate_or_reorder")
        record_by_sequence[sequence] = record
        expected_phase = (
            "active" if sequence < active_count
            else "release" if sequence == active_count
            else "settle"
        )
        if record.get("movement_phase") != expected_phase:
            errors.append("movement_phase_sequence_mismatch")

        interval = record.get("wall_interval")
        interval = interval if isinstance(interval, dict) else {}
        start_usec, end_usec = interval.get("start_usec"), interval.get("end_usec")
        duration_ms = interval.get("duration_ms")
        if (
            isinstance(start_usec, bool)
            or not isinstance(start_usec, int)
            or isinstance(end_usec, bool)
            or not isinstance(end_usec, int)
            or isinstance(duration_ms, bool)
            or not isinstance(duration_ms, (int, float))
            or not math.isfinite(float(duration_ms))
            or end_usec <= start_usec
            or round(float(duration_ms) * 1000.0) != end_usec - start_usec
        ):
            errors.append("wall_interval_invalid")
        else:
            if previous_end is not None and start_usec != previous_end:
                errors.append("wall_interval_gap_or_overlap")
            previous_end = end_usec
            duration = float(duration_ms)
            durations.append(duration)
            observed_deadline = duration <= SAME_RUN_TEMPORAL_PERFORMANCE_DEADLINE_MS
            if record.get("deadline_met") is not observed_deadline:
                errors.append("deadline_flag_mismatch")
        if not math.isclose(
            _float_value(record.get("deadline_ms"), math.nan),
            SAME_RUN_TEMPORAL_PERFORMANCE_DEADLINE_MS,
            abs_tol=1e-9,
        ):
            errors.append("record_deadline_mismatch")

        post_draw = record.get("post_draw_join")
        post_draw = post_draw if isinstance(post_draw, dict) else {}
        post_draw_frame = post_draw.get("post_draw_frame")
        dispatch_frame = post_draw.get("dispatch_frame_index")
        if (
            post_draw.get("status") != "matched"
            or isinstance(post_draw_frame, bool)
            or not isinstance(post_draw_frame, int)
            or post_draw_frame < 0
            or dispatch_frame != post_draw_frame
            or post_draw_frame in post_draw_frames
        ):
            errors.append("post_draw_dispatch_join_invalid")
        else:
            post_draw_frames.add(post_draw_frame)
        pixel_render_frame = record.get("render_frame_index")
        if (
            isinstance(pixel_render_frame, bool)
            or not isinstance(pixel_render_frame, int)
            or pixel_render_frame < 0
            or pixel_render_frame in pixel_render_frames
        ):
            errors.append("pixel_render_frame_invalid_or_duplicate")
        else:
            pixel_render_frames.add(pixel_render_frame)
        request_id = record.get("request_id")
        if (
            isinstance(request_id, bool)
            or not isinstance(request_id, int)
            or request_id <= 0
            or request_id in request_ids
        ):
            errors.append("capture_request_id_invalid_or_duplicate")
        else:
            request_ids.add(request_id)
        matrix = record.get("matrix_identity")
        matrix = matrix if isinstance(matrix, dict) else {}
        matrix_key = matrix.get("key")
        if (
            matrix.get("schema") != "fl4359.matrix_configuration_identity.v1"
            or not isinstance(matrix_key, str)
            or not matrix_key
        ):
            errors.append("matrix_identity_missing")
        else:
            matrix_keys.add(matrix_key)
    if len(matrix_keys) != 1:
        errors.append("matrix_identity_changed")

    counter_fields = (
        "post_draw_observation_count",
        "unselected_post_draw_count",
        "repeated_sequence_post_draw_count",
        "skipped_post_draw_frame_count",
        "repeated_or_reversed_post_draw_frame_count",
    )
    post_draw_counts: dict[str, int] = {}
    for field in counter_fields:
        value = timing.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            errors.append("post_draw_observation_accounting_invalid")
            value = -1
        post_draw_counts[field] = value
    if post_draw_counts["post_draw_observation_count"] != (
        len(records)
        + post_draw_counts["unselected_post_draw_count"]
        + post_draw_counts["repeated_sequence_post_draw_count"]
    ):
        errors.append("post_draw_observation_accounting_mismatch")

    authority_progress = timing.get("authority_progress")
    authority_progress = authority_progress if isinstance(authority_progress, dict) else {}
    if authority_progress.get("valid") is not True or len(records) < 2:
        errors.append("authority_progress_invalid")
    else:
        first_authority = records[0].get("authority", {})
        last_authority = records[-1].get("authority", {})
        first_authority = first_authority if isinstance(first_authority, dict) else {}
        last_authority = last_authority if isinstance(last_authority, dict) else {}
        if (
            first_authority.get("status") != "available"
            or last_authority.get("status") != "available"
            or not all(
                isinstance(snapshot.get(key), int)
                and not isinstance(snapshot.get(key), bool)
                and snapshot[key] >= 0
                for snapshot in (first_authority, last_authority)
                for key in ("ingest_seq", "server_tick")
            )
            or last_authority["ingest_seq"] <= first_authority["ingest_seq"]
            or last_authority["server_tick"] <= first_authority["server_tick"]
            or authority_progress.get("ingested_count")
            != last_authority["ingest_seq"] - first_authority["ingest_seq"]
            or authority_progress.get("server_tick_delta")
            != last_authority["server_tick"] - first_authority["server_tick"]
        ):
            errors.append("authority_progress_not_reproducible_from_frames")

    native_capture = receipt_dict.get("native_frame_capture")
    native_capture = native_capture if isinstance(native_capture, dict) else {}
    native_frames_value = native_capture.get("frames")
    native_frames = native_frames_value if isinstance(native_frames_value, list) else []
    if native_capture.get("status") != "readback_complete" or len(native_frames) != 5:
        errors.append("final_canvas_five_frame_capture_missing")
    canvas_sequences: set[int] = set()
    for native_value in native_frames:
        native = native_value if isinstance(native_value, dict) else {}
        sequence = native.get("sequence_frame")
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            errors.append("final_canvas_sequence_untyped")
            continue
        if sequence in canvas_sequences or sequence not in record_by_sequence:
            errors.append("final_canvas_sequence_unmatched")
            continue
        canvas_sequences.add(sequence)
        record = record_by_sequence[sequence]
        dispatch = native.get("dispatch")
        dispatch = dispatch if isinstance(dispatch, dict) else {}
        provenance = native.get("canvas_provenance")
        provenance = provenance if isinstance(provenance, dict) else {}
        record_post_draw = _dict_value(record.get("post_draw_join"))
        record_output = _dict_value(record.get("custom_presentation_output"))
        record_matrix = _dict_value(record.get("matrix_identity"))
        dispatch_matrix = _dict_value(dispatch.get("matrix_identity"))
        dispatch_output = _dict_value(dispatch.get("custom_presentation_output"))
        if (
            provenance.get("status") != "ok"
            or native.get("post_draw_frame") != record_post_draw.get("post_draw_frame")
            or dispatch.get("frame_index") != record_post_draw.get("dispatch_frame_index")
            or dispatch.get("request_id") != record.get("request_id")
            or dispatch.get("render_frame_index")
            != record.get("render_frame_index")
            or native.get("dispatch_canvas_render_frame_id")
            != record.get("canvas_render_frame_id")
            or dispatch.get("canvas_render_frame_id")
            != record.get("canvas_render_frame_id")
            or provenance.get("render_frame_id") != record.get("canvas_render_frame_id")
            or provenance.get("frame_id") != record.get("canvas_render_frame_id")
            or native.get("canvas_request_token") != record.get("canvas_request_token")
            or dispatch.get("canvas_request_token") != record.get("canvas_request_token")
            or native.get("canvas_provenance_target")
            != record.get("canvas_render_target_id")
            or dispatch.get("canvas_render_target_id")
            != record.get("canvas_render_target_id")
            or dispatch_matrix.get("key") != record_matrix.get("key")
            or record_output.get("schema") != "fl4691.custom_presentation_output.v1"
            or record_output.get("render_frame_id")
            != record.get("canvas_render_frame_id")
            or record_output.get("canvas_request_token")
            != record.get("canvas_request_token")
            or record_output.get("owner")
            != "ascii_compositor_post_present_scene_color"
            or dispatch_output.get("output_rid") != record_output.get("output_rid")
            or dispatch_output.get("render_frame_id")
            != record_output.get("render_frame_id")
            or dispatch_output.get("canvas_request_token")
            != record_output.get("canvas_request_token")
            or isinstance(record_output.get("output_rid"), bool)
            or not isinstance(record_output.get("output_rid"), int)
            or record_output.get("output_rid") <= 0
        ):
            errors.append("final_canvas_identity_join_mismatch")
    if canvas_sequences != set(range(5)):
        errors.append("final_canvas_sequence_set_incomplete")

    presentation_sidecars = receipt_dict.get("presentation_field_sidecars")
    native_sidecars = receipt_dict.get("native_source_content_sidecars")
    presentation_sidecars = (
        presentation_sidecars if isinstance(presentation_sidecars, list) else []
    )
    native_sidecars = native_sidecars if isinstance(native_sidecars, list) else []
    presentation_by_sequence = {
        sidecar.get("sequence_frame"): sidecar
        for sidecar in presentation_sidecars
        if isinstance(sidecar, dict)
    }
    native_by_sequence = {
        sidecar.get("sequence_frame"): sidecar
        for sidecar in native_sidecars
        if isinstance(sidecar, dict)
    }
    if (
        len(presentation_by_sequence) != 5
        or len(native_by_sequence) != 5
        or set(presentation_by_sequence) != set(range(5))
        or set(native_by_sequence) != set(range(5))
    ):
        errors.append("source_sidecar_sequence_set_incomplete")
    else:
        for sequence in range(5):
            record = record_by_sequence.get(sequence, {})
            presentation_sidecar = presentation_by_sequence[sequence]
            native_sidecar = native_by_sequence[sequence]
            if (
                presentation_sidecar.get("request_id") != record.get("request_id")
                or native_sidecar.get("request_id") != record.get("request_id")
                or presentation_sidecar.get("frame_index")
                != record.get("render_frame_index")
                or native_sidecar.get("frame_index")
                != record.get("render_frame_index")
                or native_sidecar.get("render_frame_index")
                != record.get("render_frame_index")
            ):
                errors.append("source_sidecar_timing_identity_join_mismatch")

    sorted_durations = sorted(durations)
    def nearest_rank(percent: float) -> float | None:
        if not sorted_durations:
            return None
        rank = max(1, math.ceil(percent / 100.0 * len(sorted_durations)))
        return sorted_durations[min(rank - 1, len(sorted_durations) - 1)]

    longest_miss_streak = 0
    current_miss_streak = 0
    for duration in durations:
        if duration > SAME_RUN_TEMPORAL_PERFORMANCE_DEADLINE_MS:
            current_miss_streak += 1
            longest_miss_streak = max(longest_miss_streak, current_miss_streak)
        else:
            current_miss_streak = 0
    deadline_met_count = sum(
        duration <= SAME_RUN_TEMPORAL_PERFORMANCE_DEADLINE_MS for duration in durations
    )
    capture_mode_deadline_pass = (
        bool(durations)
        and len(durations) == expected_count
        and deadline_met_count == expected_count
        and all(value == 0 for value in (
            post_draw_counts["unselected_post_draw_count"],
            post_draw_counts["repeated_sequence_post_draw_count"],
            post_draw_counts["skipped_post_draw_frame_count"],
            post_draw_counts["repeated_or_reversed_post_draw_frame_count"],
        ))
        and timing.get("capture_mode_deadline_pass") is True
        and timing.get("deadline_met_count") == deadline_met_count
        and math.isclose(
            _float_value(timing.get("deadline_ms"), math.nan),
            SAME_RUN_TEMPORAL_PERFORMANCE_DEADLINE_MS,
            abs_tol=1e-9,
        )
    )
    if not capture_mode_deadline_pass:
        errors.append("instrumented_capture_deadline_failed")
    if temporal_gate_pass is None:
        temporal_gate_pass = (
            summary_dict.get("admissible") is not False
            and _analysis_gates_accept_proof(summary_dict)
        )
    if temporal_gate_pass is not True:
        errors.append("temporal_analyzer_gate_failed")

    unique_errors = list(dict.fromkeys(errors))
    passed = not unique_errors
    identity_join_pass = not any(
        error not in {
            "instrumented_capture_deadline_failed",
            "temporal_analyzer_gate_failed",
        }
        for error in unique_errors
    )
    temporal_identity_join_pass = temporal_gate_pass is True and identity_join_pass
    return {
        "schema": SAME_RUN_TEMPORAL_PERFORMANCE_GATE_SCHEMA,
        "pass": passed,
        "reason": (
            "instrumented_temporal_and_deadline_gates_passed"
            if passed else unique_errors[0]
        ),
        "errors": unique_errors,
        "measurement_classification": SAME_RUN_TEMPORAL_PERFORMANCE_CLASSIFICATION,
        "temporal_gate_pass": temporal_gate_pass is True,
        "source_content_capture_pass": source_content_capture_pass is True,
        "positive_comparable_population_pass": (
            positive_comparable_population_pass is True
        ),
        "identity_join_pass": identity_join_pass,
        "temporal_identity_join_pass": temporal_identity_join_pass,
        "capture_mode_deadline_pass": capture_mode_deadline_pass,
        "per_present_frame_timing_complete": False,
        "continuous_post_draw_timing_status": "open",
        "continuous_post_draw_timing_reason": (
            "selected_capture_rows_do_not_cover_intervening_draws"
        ),
        "clean_60fps_acceptance_eligible": False,
        "clean_60fps_absence_reason": "proof_capture_and_provenance_observers_armed",
        "deadline_ms": SAME_RUN_TEMPORAL_PERFORMANCE_DEADLINE_MS,
        "record_count": len(records),
        "deadline_met_count": deadline_met_count,
        "median_delta_ms": (
            None if not sorted_durations
            else (
                sorted_durations[len(sorted_durations) // 2]
                if len(sorted_durations) % 2
                else sum(sorted_durations[len(sorted_durations) // 2 - 1:len(sorted_durations) // 2 + 1]) / 2.0
            )
        ),
        "p95_delta_ms": nearest_rank(95.0),
        "p99_delta_ms": nearest_rank(99.0),
        "longest_deadline_miss_streak": longest_miss_streak,
        "matrix_identity_key": next(iter(matrix_keys)) if len(matrix_keys) == 1 else None,
        "canvas_joined_sequence_frames": sorted(canvas_sequences),
        "post_draw_observation_accounting": post_draw_counts,
        "claim_scope": "instrumented_same_run_temporal_capture_cadence_only",
    }


def _enforce_same_run_temporal_identity_gate(
    receipt: dict,
    summary: dict,
    full_grid_runtime_contract: dict,
    gate: dict,
) -> bool:
    """Make exact same-run identity a full-grid temporal proof prerequisite."""
    passed = gate.get("temporal_identity_join_pass") is True
    non_deadline_errors = [
        error
        for error in gate.get("errors", [])
        if error != "instrumented_capture_deadline_failed"
    ]
    full_grid_runtime_contract["same_run_temporal_identity_join_pass"] = passed
    full_grid_runtime_contract["same_run_temporal_identity_join_reason"] = (
        "temporal_analyzer_and_exact_identity_join_passed"
        if passed
        else (
            non_deadline_errors[0]
            if non_deadline_errors
            else "same_run_temporal_identity_join_missing"
        )
    )
    if passed:
        return True
    reason = "same_run_temporal_identity_join_failed"
    full_grid_runtime_contract["pass"] = False
    full_grid_runtime_contract["reason"] = reason
    summary["admissible"] = False
    summary["reason"] = reason
    receipt["pass"] = False
    receipt["reason"] = reason
    return False


def _canvas_frame_join_rows(observer_receipt: object) -> list[dict[str, object]]:
    """Keep only bounded identity rows in the runner-side Canvas summary."""
    if not isinstance(observer_receipt, dict):
        return []
    rows = observer_receipt.get("frames", [])
    if not isinstance(rows, list):
        return []
    result: list[dict[str, object]] = []
    for frame in rows:
        if not isinstance(frame, dict):
            continue
        dispatch = frame.get("dispatch", {})
        provenance = frame.get("canvas_provenance", {})
        target_info = provenance.get("target_info", {}) if isinstance(provenance, dict) else {}
        if not isinstance(dispatch, dict):
            dispatch = {}
        if not isinstance(provenance, dict):
            provenance = {}
        if not isinstance(target_info, dict):
            target_info = {}
        result.append(
            {
                "sequence_frame": frame.get("sequence_frame"),
                "post_draw_frame": frame.get("post_draw_frame"),
                "canvas_request_token": frame.get("canvas_request_token"),
                "canvas_render_target_id": frame.get("canvas_provenance_target"),
                "dispatch_canvas_render_frame_id": frame.get(
                    "dispatch_canvas_render_frame_id"
                ),
                "dispatch_frame_index": dispatch.get("frame_index"),
                "matrix_identity": dispatch.get("matrix_identity"),
                "provenance_frame_id": provenance.get("frame_id"),
                "record_count": provenance.get("record_count"),
                "source_count": provenance.get("source_count"),
                "target_load_action": target_info.get("load_action"),
            }
        )
    return result


def _run_canvas_provenance_analysis(
    out_dir: Path,
    runtime_receipt: object,
) -> tuple[int, Path]:
    """Replay the exported Canvas receipt after the child has exited.

    The driver calls NativeFrameObserver.export_pngs(<out>/native_frames) and
    embeds its returned receipt under ``native_frame_capture`` in receipt.json.
    The file written here is a normalized copy at the stable sidecar root used
    by the offline reader; it contains metadata only, while pixels and Canvas
    records remain in the existing PNG/binary sidecars.
    """
    analysis_path = out_dir / "canvas_analysis.json"
    observer_path = out_dir / "native_frames" / "observer_receipt.json"
    summary: dict[str, object]
    native_capture = (
        runtime_receipt.get("native_frame_capture")
        if isinstance(runtime_receipt, dict)
        else None
    )
    try:
        if not isinstance(native_capture, dict):
            raise ValueError("native_frame_capture_missing_from_runtime_receipt")
        observer_path.parent.mkdir(parents=True, exist_ok=True)
        observer_path.write_text(
            json.dumps(native_capture, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        # Lazy import keeps ordinary headed movement runs free of reader import
        # and NumPy/Pillow startup work. The module is delivered alongside this
        # runner patch when Canvas provenance capture is enabled.
        from fl4359_canvas_trace import _summary, read_capture

        result = read_capture(observer_path, replay=True, row_chunk=128)
        summary = _summary(result)
        summary["frame_joins"] = _canvas_frame_join_rows(native_capture)
        summary["native_observer_receipt"] = str(observer_path)
        summary["native_export_owner"] = (
            "NativeFrameObserver.export_pngs -> driver abs_out/native_frames"
        )
        summary["surface_boundary"] = "viewport_after_canvas_layers"
        summary["input_boundary"] = "captured_pre_canvas_load_action_target"
        summary["post_compositor_causality"] = "not_claimed"
    except Exception as exc:  # Fail closed and retain a bounded diagnosis.
        summary = {
            "status": "failed",
            "reason": str(exc),
            "exact": False,
            "frame_count": 0,
            "frames": [],
            "frame_joins": [],
            "native_observer_receipt": str(observer_path),
            "native_export_owner": (
                "NativeFrameObserver.export_pngs -> driver abs_out/native_frames"
            ),
            "surface_boundary": "viewport_after_canvas_layers",
            "input_boundary": "captured_pre_canvas_load_action_target",
            "post_compositor_causality": "not_claimed",
        }
    analysis_path.write_text(
        json.dumps(
            {"schema": "fl4359.canvas_analysis.v1", **summary},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    passed = summary.get("status") == "ok" and summary.get("exact") is True
    return (0 if passed else 1), analysis_path


def _classify_fl4216_selected_surface_dump(receipt: dict) -> dict:
    """Stream existing selected-surface rows after Godot exits.

    This stays outside the game process: the driver records the final-cell dump
    path, then the runner observes its selected_surface_quarters JSONL. Python's
    JSON decoder accepts the dump's Infinity/large-exponent absence depths
    without turning them into repeated Godot `Exponent too high` warnings.
    """
    counts = {
        "source": "final_render_cell_dump.cells_jsonl.selected_surface_quarters",
        "cells_read": 0,
        "selected_surface_quarters_read": 0,
        "selected_surface_valid_quarters": 0,
        "selected_surface_contact_quarters": 0,
        "selected_surface_contact_static_source_quarters": 0,
        "selected_surface_contact_dynamic_source_quarters": 0,
        "selected_surface_static_quarters": 0,
        "selected_surface_dynamic_quarters": 0,
        "selected_surface_water_quarters": 0,
        "selected_surface_reflection_quarters": 0,
        "selected_surface_submerged_source_quarters": 0,
        "json_parse_errors": 0,
    }
    dump = receipt.get("final_render_cell_dump", {})
    paths = dump.get("artifact_paths", {}) if isinstance(dump, dict) else {}
    cells_value = paths.get("cells_jsonl", "") if isinstance(paths, dict) else ""
    cells_path = Path(str(cells_value)) if cells_value else None
    if cells_path is None or not cells_path.exists():
        counts["readback_state"] = "missing_cells_jsonl"
        return counts
    counts["cells_bytes"] = cells_path.stat().st_size
    max_bytes = 512 * 1024 * 1024
    if counts["cells_bytes"] > max_bytes:
        counts["readback_state"] = "cells_jsonl_too_large"
        counts["max_cells_bytes"] = max_bytes
        return counts
    with cells_path.open("r", encoding="utf-8") as cells_file:
        for line in cells_file:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                counts["json_parse_errors"] += 1
                continue
            if not isinstance(row, dict):
                continue
            counts["cells_read"] += 1
            quarters = row.get("selected_surface_quarters", [])
            if not isinstance(quarters, list):
                continue
            for quarter in quarters:
                if not isinstance(quarter, dict):
                    continue
                counts["selected_surface_quarters_read"] += 1
                if not bool(quarter.get("valid", False)):
                    continue
                counts["selected_surface_valid_quarters"] += 1
                owner_kind = int(quarter.get("owner_kind", 0) or 0)
                if owner_kind == 1:
                    counts["selected_surface_static_quarters"] += 1
                elif owner_kind == 4:
                    counts["selected_surface_dynamic_quarters"] += 1
                elif owner_kind == 3:
                    counts["selected_surface_water_quarters"] += 1
                if bool(quarter.get("direct_water_solid_contact", False)):
                    counts["selected_surface_contact_quarters"] += 1
                    source_owner_kind = int(quarter.get("submerged_source_owner_kind", 0) or 0)
                    if source_owner_kind == 1:
                        counts["selected_surface_contact_static_source_quarters"] += 1
                    elif source_owner_kind == 4:
                        counts["selected_surface_contact_dynamic_source_quarters"] += 1
                if bool(quarter.get("submerged_source_present", False)):
                    counts["selected_surface_submerged_source_quarters"] += 1
                if int(quarter.get("reflection_word", 0) or 0) != 0 or int(quarter.get("reflection_authored_shade_q", 0) or 0) > 0:
                    counts["selected_surface_reflection_quarters"] += 1
    counts["readback_state"] = (
        "selected_surface_json_invalid"
        if counts["json_parse_errors"]
        else "selected_surface_classification_ok"
    )
    return counts


FL4216_TEMPORAL_TOP_LEVEL_FIELDS = (
    "selected_owner",
    "source_class",
    "glyph_id",
    "glyph_char",
    "fg_rgb",
    "bg_rgb",
)
FL4216_TEMPORAL_QUARTER_FIELDS = (
    "stable_world_anchor",
    "owner_kind",
    "submerged_source_owner_kind",
    "direct_water_solid_contact",
)
FL4216_TEMPORAL_ARTIFACT_KEYS = (
    "cells_jsonl",
    "final_png",
    "index_html",
    "metadata_json",
)
FL4216_TEMPORAL_METADATA_KEYS = (
    "camera_world",
    "player_world",
    "pixel_camera_capture_contract",
)
FL4216_TEMPORAL_SOURCE_CLASSES = ("terrain", "static_mesh", "dynamic_mesh")
FL4216_P1_REQUIRED_SOURCE_CLASSES = frozenset(("terrain", "static_mesh"))
FL4216_TEMPORAL_SOURCE_OWNER_KINDS = {2: "terrain", 1: "static_mesh", 4: "dynamic_mesh"}
# Exact FL-4216 shoreline-break atlas table from
# cell_owner_product.glsl:FL4216_GLYPH_ID (8 directions x 4 stages). Keep the
# tuple, including repeated stage values, as the shader contract; a broad
# 512..671 range would incorrectly classify unrelated extended glyphs such as
# retained-hand glyph 670 as shoreline morphology.
FL4216_SHORELINE_BREAK_GLYPH_IDS = (
    664, 546, 661, 661,
    668, 649, 662, 662,
    517, 648, 652, 657,
    654, 556, 647, 659,
    665, 656, 660, 660,
    669, 653, 651, 657,
    666, 663, 663, 650,
    667, 655, 658, 658,
)
FL4216_SHORELINE_BREAK_GLYPH_ID_SET = frozenset(FL4216_SHORELINE_BREAK_GLYPH_IDS)
FL4216_TEMPORAL_PRODUCT_DELTA_MIN_SECONDS = 1.5
FL4216_TEMPORAL_PRODUCT_DELTA_MAX_SECONDS = 2.0


def _fl4216_temporal_snapshot_payload(dump: dict) -> tuple[dict | None, str | None]:
    """Load deferred raw b43/b64 bytes; never associate a later classic dump."""
    paths = dump.get("artifact_paths", {}) if isinstance(dump, dict) else {}
    snapshot_path = Path(paths.get("temporal_snapshot_json", "")) if isinstance(paths, dict) else Path("")
    if not snapshot_path.is_file():
        return None, "missing_temporal_snapshot_json"
    try:
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "invalid_temporal_snapshot_json"
    if not isinstance(payload, dict) or payload.get("schema") != "fl4216_temporal_snapshot.v1":
        return None, "temporal_snapshot_schema_mismatch"
    try:
        final_bytes = base64.b64decode(payload.get("final_cell_paint_b64", ""), validate=True)
        selected_bytes = base64.b64decode(payload.get("selected_surface_b64", ""), validate=True)
    except (TypeError, ValueError):
        return None, "temporal_snapshot_base64_invalid"
    final_stride = int(payload.get("final_cell_record_stride_bytes", 0) or 0)
    selected_stride = int(payload.get("selected_surface_record_stride_bytes", 0) or 0)
    selected_count = int(payload.get("selected_surface_record_count", 0) or 0)
    metadata = payload.get("metadata")
    contract = metadata.get("pixel_camera_capture_contract") if isinstance(metadata, dict) else None
    ansi = contract.get("semantic_ansi_size") if isinstance(contract, dict) else None
    if (
        not isinstance(contract, dict)
        or not isinstance(ansi, list)
        or len(ansi) < 2
        # b43's FL-4359 capture variants append proof-only mask and Product-key
        # words. FL-4216 consumes only the stable 28-word prefix, so legacy,
        # intermediate mask-only, and current mask-plus-key rows remain valid.
        or final_stride not in (112, 148, 168)
        or selected_stride != 96
        or int(ansi[0]) <= 0
        or int(ansi[1]) <= 0
        or len(final_bytes) != int(ansi[0]) * int(ansi[1]) * final_stride
        or selected_count != int(ansi[0]) * int(ansi[1]) * 4
        or len(selected_bytes) != selected_count * selected_stride
    ):
        return None, "temporal_snapshot_raw_buffer_shape_mismatch"
    payload["final_cell_paint_bytes"] = final_bytes
    payload["selected_surface_bytes"] = selected_bytes
    payload["semantic_ansi_size"] = [int(ansi[0]), int(ansi[1])]
    return payload, None


def _fl4216_temporal_snapshot_rows(dump: dict) -> tuple[list[dict], dict]:
    """Decode same-frame raw b43+b64 bytes after cadence capture is complete."""
    payload, error = _fl4216_temporal_snapshot_payload(dump)
    diagnostics = {
        "cells_read": 0,
        "contact_quarters_read": 0,
        "json_parse_errors": 0,
        "missing_identity_rows": 0,
        "readback_state": "temporal_snapshot_decode_ok" if payload is not None else error,
    }
    if payload is None:
        return [], diagnostics
    final_bytes: bytes = payload["final_cell_paint_bytes"]
    selected_bytes: bytes = payload["selected_surface_bytes"]
    width, height = payload["semantic_ansi_size"]
    final_stride = int(payload["final_cell_record_stride_bytes"])
    selected_stride = int(payload["selected_surface_record_stride_bytes"])
    rows: list[dict] = []
    for y in range(height):
        for x in range(width):
            final_base = (y * width + x) * final_stride
            if final_base + 112 > len(final_bytes):
                continue
            magic, _owner_kind, glyph_id = struct.unpack_from("<III", final_bytes, final_base)
            if magic != 0xF14231:
                continue
            mask_presence = struct.unpack_from("<I", final_bytes, final_base + 76)[0]
            mask_glyphs = []
            for slot in range(4):
                encoded = struct.unpack_from("<I", final_bytes, final_base + 84 + slot * 4)[0]
                mask_glyphs.append(encoded - 1 if encoded else None)
            quarters: list[dict] = []
            for slot in range(4):
                sx = x * 2 + (slot & 1)
                sy = y * 2 + (slot >> 1)
                selected_index = sy * (width * 2) + sx
                selected_base = selected_index * selected_stride
                if selected_base + selected_stride > len(selected_bytes):
                    continue
                owner, object_id, primitive_id, abi = struct.unpack_from("<IIII", selected_bytes, selected_base)
                status = struct.unpack_from("<I", selected_bytes, selected_base + 60)[0]
                anchor = struct.unpack_from("<4f", selected_bytes, selected_base + 64)
                valid = bool(status & 1) and abi == 3 and owner in (1, 2, 3, 4)
                submerged_code = (status & 0x180) >> 7
                submerged_owner = {1: 2, 2: 1, 3: 4}.get(submerged_code, 0)
                contact = bool(status & (1 << 29))
                quarters.append({
                    "valid": valid,
                    "owner_kind": owner,
                    "object_id": object_id,
                    "primitive_id": primitive_id,
                    "stable_world_anchor": list(anchor),
                    "direct_water_solid_contact": contact,
                    "submerged_source_owner_kind": submerged_owner,
                    "submerged_source_present": submerged_owner != 0,
                })
            rows.append({
                "screen_cell": {"x": x, "y": y},
                "glyph_id": glyph_id,
                "final_cell_paint_buffer": {
                    "glyph_id": glyph_id,
                    "final_mask_glyph_ids": mask_glyphs if mask_presence else [],
                },
                "selected_surface_quarters": quarters,
            })
            diagnostics["cells_read"] += 1
            diagnostics["contact_quarters_read"] += sum(
                1 for quarter in quarters if quarter["valid"] and quarter["direct_water_solid_contact"]
            )
    if diagnostics["cells_read"] != width * height:
        diagnostics["readback_state"] = "final_cell_paint_magic_mismatch"
        diagnostics["reason"] = "final_cell_paint_magic_mismatch"
        return [], diagnostics
    return rows, diagnostics


def _validate_fl4216_temporal_dump(dump: dict) -> dict:
    """Validate one same-process final-render dump without inventing a writer.

    The Godot driver owns dump creation. The wrapper only checks that every
    declared artifact exists and that the JSONL retains the identity/contact
    and final-paint fields needed by the temporal gate.
    """
    result = {
        "pass": False,
        "artifact_paths_present": False,
        "artifact_files_present": False,
        "cells_read": 0,
        "required_top_level_fields_present": [],
        "required_quarter_fields_present": [],
        "json_parse_errors": 0,
        "reason": "missing_final_render_cell_dump",
    }
    if not isinstance(dump, dict) or dump.get("ok") is not True:
        return result
    paths = dump.get("artifact_paths")
    if not isinstance(paths, dict):
        result["reason"] = "missing_final_render_artifact_paths"
        return result
    if dump.get("capture_schema") == "fl4216_temporal_snapshot.v1":
        snapshot, error = _fl4216_temporal_snapshot_payload(dump)
        metadata_path = Path(paths.get("metadata_json", ""))
        png_path = Path(paths.get("final_png", ""))
        result.update({
            "artifact_paths_present": bool(
                paths.get("temporal_snapshot_json")
                and paths.get("metadata_json")
                and paths.get("final_png")
            ),
            "artifact_files_present": bool(
                snapshot is not None and metadata_path.is_file() and png_path.is_file()
            ),
            "snapshot_schema": "fl4216_temporal_snapshot.v1" if snapshot is not None else None,
            "cells_read": 1 if snapshot is not None else 0,
            "required_top_level_fields_present": list(FL4216_TEMPORAL_TOP_LEVEL_FIELDS) if snapshot is not None else [],
            "required_quarter_fields_present": list(FL4216_TEMPORAL_QUARTER_FIELDS) if snapshot is not None else [],
            "metadata_json_valid": metadata_path.is_file(),
            "metadata_fields_present": list(FL4216_TEMPORAL_METADATA_KEYS) if metadata_path.is_file() else [],
        })
        result["pass"] = bool(snapshot is not None and metadata_path.is_file() and png_path.is_file())
        result["reason"] = "temporal_snapshot_contract_ok" if result["pass"] else (error or "temporal_snapshot_contract_failed")
        return result
    result["artifact_paths_present"] = all(
        isinstance(paths.get(key), str) and bool(paths.get(key))
        for key in FL4216_TEMPORAL_ARTIFACT_KEYS
    )
    if not result["artifact_paths_present"]:
        result["reason"] = "missing_final_render_artifact_path_key"
        return result
    artifact_paths = {key: Path(paths[key]) for key in FL4216_TEMPORAL_ARTIFACT_KEYS}
    result["artifact_files_present"] = all(path.is_file() for path in artifact_paths.values())
    if not result["artifact_files_present"]:
        result["reason"] = "missing_final_render_artifact_file"
        return result
    result["metadata_json_valid"] = False
    result["metadata_fields_present"] = []
    try:
        metadata = json.loads(artifact_paths["metadata_json"].read_text(encoding="utf-8"))
        result["metadata_json_valid"] = isinstance(metadata, dict)
        if result["metadata_json_valid"]:
            result["metadata_fields_present"] = [
                field for field in FL4216_TEMPORAL_METADATA_KEYS if field in metadata
            ]
    except (OSError, json.JSONDecodeError):
        result["reason"] = "invalid_final_render_metadata_json"
        return result
    top_level_fields: set[str] = set()
    quarter_fields: set[str] = set()
    try:
        with artifact_paths["cells_jsonl"].open("r", encoding="utf-8") as cells_file:
            for line in cells_file:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    result["json_parse_errors"] += 1
                    continue
                if not isinstance(row, dict):
                    continue
                result["cells_read"] += 1
                top_level_fields.update(
                    field for field in FL4216_TEMPORAL_TOP_LEVEL_FIELDS if field in row
                )
                quarters = row.get("selected_surface_quarters")
                if isinstance(quarters, list):
                    for quarter in quarters:
                        if isinstance(quarter, dict):
                            quarter_fields.update(
                                field for field in FL4216_TEMPORAL_QUARTER_FIELDS if field in quarter
                            )
                if result["cells_read"] >= 1 and (
                    len(top_level_fields) == len(FL4216_TEMPORAL_TOP_LEVEL_FIELDS)
                    and len(quarter_fields) == len(FL4216_TEMPORAL_QUARTER_FIELDS)
                ):
                    break
    except OSError:
        result["reason"] = "unreadable_final_render_cells_jsonl"
        return result
    result["required_top_level_fields_present"] = [
        field for field in FL4216_TEMPORAL_TOP_LEVEL_FIELDS if field in top_level_fields
    ]
    result["required_quarter_fields_present"] = [
        field for field in FL4216_TEMPORAL_QUARTER_FIELDS if field in quarter_fields
    ]
    required_fields_ok = (
        result["cells_read"] > 0
        and result["json_parse_errors"] == 0
        and len(result["required_top_level_fields_present"]) == len(FL4216_TEMPORAL_TOP_LEVEL_FIELDS)
        and len(result["required_quarter_fields_present"]) == len(FL4216_TEMPORAL_QUARTER_FIELDS)
    )
    result["pass"] = bool(
        result["artifact_files_present"]
        and result["metadata_json_valid"]
        and len(result["metadata_fields_present"]) == len(FL4216_TEMPORAL_METADATA_KEYS)
        and required_fields_ok
    )
    result["reason"] = "temporal_dump_contract_ok" if result["pass"] else "temporal_dump_field_contract_failed"
    return result


def _reuse_pruned_fl4216_contract(
    current: dict,
    previous: dict,
    *,
    fallback_reasons: set[str],
    contract_kind: str,
) -> dict:
    """Preserve an extracted contract when only the raw artifact went away.

    This is intentionally narrow. We reuse a previous PASS result only when the
    current re-check failed because raw files are missing. Corrupt JSON, shape
    mismatches, and other content defects must still fail closed.
    """
    if not isinstance(current, dict) or current.get("pass") is True:
        return current
    if str(current.get("reason", "")) not in fallback_reasons:
        return current
    if not isinstance(previous, dict) or previous.get("pass") is not True:
        return current
    reused = dict(previous)
    reused["reason"] = "durable_extracted_contract_reused_after_raw_artifact_prune"
    reused["raw_artifact_pruned"] = True
    reused["raw_artifact_missing_reason"] = current.get("reason")
    reused["raw_artifact_fallback_contract_kind"] = contract_kind
    reused["blocking"] = False
    return reused


def _reuse_pruned_fl4216_comparison(current: dict, previous: dict) -> dict:
    """Reuse an extracted temporal comparison only for missing-raw cases."""
    if not isinstance(current, dict) or current.get("pass") is True:
        return current
    if current.get("reason") != "temporal_contact_rows_unreadable":
        return current
    diagnostics = current.get("frame_diagnostics")
    if not isinstance(diagnostics, list) or not diagnostics:
        return current
    missing_only = all(
        isinstance(item, dict)
        and int(item.get("json_parse_errors", 0) or 0) == 0
        and int(item.get("missing_identity_rows", 0) or 0) == 0
        and str(item.get("reason", "")).startswith("missing")
        for item in diagnostics
    )
    if not missing_only:
        return current
    return _reuse_pruned_fl4216_contract(
        current,
        previous,
        fallback_reasons={"temporal_contact_rows_unreadable"},
        contract_kind="temporal_comparison",
    )


def _fl4216_metadata_vec3(value) -> tuple[float, float, float] | None:
    if isinstance(value, dict):
        values = (value.get("x"), value.get("y"), value.get("z"))
    elif isinstance(value, list) and len(value) >= 3:
        values = (value[0], value[1], value[2])
    else:
        return None
    try:
        coordinates = tuple(float(component) for component in values)
    except (TypeError, ValueError):
        return None
    return coordinates if all(math.isfinite(component) for component in coordinates) else None


def _validate_fl4216_temporal_pose_metadata(
    dump: dict,
    requested_player: dict,
    requested_camera: dict,
    projection: str,
    requested_yaw=None,
    requested_pitch=None,
    grounding_contract: dict | None = None,
) -> dict:
    result = {
        "pass": False,
        "reason": "missing_temporal_metadata_pose",
        "camera_error": None,
        "actor_xz_error": None,
        "actor_y_error": None,
    }
    paths = dump.get("artifact_paths", {}) if isinstance(dump, dict) else {}
    metadata_path = Path(paths.get("metadata_json", "")) if isinstance(paths, dict) else Path("")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except OSError:
        result["reason"] = "missing_temporal_metadata_json"
        return result
    except json.JSONDecodeError:
        result["reason"] = "invalid_temporal_metadata_json"
        return result
    contract = metadata.get("pixel_camera_capture_contract") if isinstance(metadata, dict) else None
    if not isinstance(contract, dict):
        return result
    rendered_camera = _fl4216_metadata_vec3(contract.get("scene_camera_origin"))
    rendered_actor = _fl4216_metadata_vec3(contract.get("actor_presentation_reference_world"))
    expected_camera = _fl4216_metadata_vec3(requested_camera)
    expected_player = _fl4216_metadata_vec3(requested_player)
    if rendered_camera is None or rendered_actor is None or expected_camera is None or expected_player is None:
        return result
    if not isinstance(grounding_contract, dict) or grounding_contract.get("pass") is not True:
        result["reason"] = "missing_or_invalid_authoritative_grounding_contract"
        return result
    grounded = _fl4216_metadata_vec3(
        grounding_contract.get("authoritative_grounded_player_position")
    )
    stable_grounded = _fl4216_metadata_vec3(
        grounding_contract.get("stable_publication_player_position")
    )
    try:
        ground_y_adjustment = float(grounding_contract.get("ground_y_adjustment"))
        publication_consistency_error = float(
            grounding_contract.get("publication_consistency_error")
        )
    except (TypeError, ValueError):
        grounded = None
        stable_grounded = None
        ground_y_adjustment = math.inf
        publication_consistency_error = math.inf
    if grounded is None or stable_grounded is None:
        result["reason"] = "missing_authoritative_grounded_player_fact"
        return result
    expected_grounded_xz_error = math.dist(
        (grounded[0], grounded[2]), (expected_player[0], expected_player[2])
    )
    stable_grounded_error = math.dist(grounded, stable_grounded)
    adjustment_error = abs(
        ground_y_adjustment - (grounded[1] - expected_player[1])
    )
    if (
        expected_grounded_xz_error > 0.01
        or stable_grounded_error > 0.01
        or publication_consistency_error > 0.01
        or adjustment_error > 0.01
    ):
        result.update(
            {
                "reason": "authoritative_grounding_fact_inconsistent",
                "authoritative_grounded_player_position": grounded,
                "stable_publication_player_position": stable_grounded,
                "ground_y_adjustment": ground_y_adjustment,
                "expected_grounded_xz_error": expected_grounded_xz_error,
                "stable_grounded_error": stable_grounded_error,
                "publication_consistency_error": publication_consistency_error,
                "ground_y_adjustment_error": adjustment_error,
            }
        )
        return result
    camera_error = math.dist(rendered_camera, expected_camera)
    actor_xz_error = math.dist(
        (rendered_actor[0], rendered_actor[2]), (expected_player[0], expected_player[2])
    )
    actor_y_error = abs(rendered_actor[1] - grounded[1])
    expected_projection = 0 if projection == "perspective" else 1
    projection_match = int(contract.get("camera_projection_mode", -1)) == expected_projection
    contract_valid = contract.get("actor_presentation_camera_contract_valid") is True
    rendered_yaw = contract.get("camera_yaw")
    rendered_pitch = contract.get("camera_pitch")
    yaw_error = None
    pitch_error = None
    if requested_yaw is not None:
        try:
            rendered_yaw = float(rendered_yaw)
            expected_yaw = float(requested_yaw)
            yaw_error = abs(math.atan2(math.sin(rendered_yaw - expected_yaw), math.cos(rendered_yaw - expected_yaw)))
        except (TypeError, ValueError):
            yaw_error = math.inf
    if requested_pitch is not None:
        try:
            rendered_pitch = float(rendered_pitch)
            expected_pitch = float(requested_pitch)
            pitch_error = abs(
                math.atan2(
                    math.sin(rendered_pitch - expected_pitch),
                    math.cos(rendered_pitch - expected_pitch),
                )
            )
        except (TypeError, ValueError):
            pitch_error = math.inf
    yaw_match = yaw_error is None or yaw_error <= 0.01
    pitch_match = pitch_error is None or pitch_error <= 0.01
    pose_match = camera_error <= 0.01 and actor_xz_error <= 0.01 and actor_y_error <= 0.01 and yaw_match and pitch_match
    result.update(
        {
            "camera_error": camera_error,
            "actor_xz_error": actor_xz_error,
            "actor_y_error": actor_y_error,
            "authoritative_grounded_player_position": grounded,
            "stable_publication_player_position": stable_grounded,
            "ground_y_adjustment": ground_y_adjustment,
            "expected_grounded_xz_error": expected_grounded_xz_error,
            "stable_grounded_error": stable_grounded_error,
            "publication_consistency_error": publication_consistency_error,
            "ground_y_adjustment_error": adjustment_error,
            "yaw_error": yaw_error,
            "pitch_error": pitch_error,
            "projection_match": projection_match,
            "actor_presentation_camera_contract_valid": contract_valid,
            "top_level_player_world_is_diagnostic_only": True,
            "grounding_source": grounding_contract.get("source"),
            "pass": pose_match and projection_match and contract_valid,
            "reason": "temporal_metadata_pose_ok"
            if pose_match and projection_match and contract_valid
            else "temporal_metadata_pose_mismatch",
        }
    )
    return result


def _validate_fl4216_water_interaction_field(dump: dict) -> dict:
    """Validate same-frame compiler/build/upload provenance from metadata."""
    result = {"pass": False, "reason": "missing_water_interaction_field_metrics"}
    paths = dump.get("artifact_paths", {}) if isinstance(dump, dict) else {}
    metadata_path = Path(paths.get("metadata_json", "")) if isinstance(paths, dict) else Path("")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except OSError:
        return result
    except json.JSONDecodeError:
        result["reason"] = "invalid_water_interaction_field_metadata"
        return result
    contract = metadata.get("pixel_camera_capture_contract") if isinstance(metadata, dict) else None
    metrics = contract.get("water_interaction_field") if isinstance(contract, dict) else None
    if not isinstance(metrics, dict):
        return result
    revisions = metrics.get("source_revisions")
    # FL-4216 lane 0: "static_content" is the deterministic content identity that
    # now keys field invalidation; "static_geometry" is the build counter and is
    # kept because it answers a different question ("has a build happened?").
    # Requiring BOTH is what makes this gate able to see the repair: a run that
    # reports a moving static_geometry with a fixed static_content is exactly the
    # steady state the invalidation change was made to produce.
    required_revisions = (
        "static_geometry",
        "static_content",
        "heightfield",
        "water",
        "dynamic_mover",
    )
    result.update({"metrics": metrics, "required_source_revisions": list(required_revisions)})
    counters_valid = all(
        isinstance(metrics.get(field), int)
        for field in ("generation", "full_build_count", "upload_count", "uploaded_bytes", "gpu_generation")
    )
    result["pass"] = bool(
        counters_valid
        and int(metrics["generation"]) >= 1
        and int(metrics["full_build_count"]) >= 1
        and int(metrics["upload_count"]) >= 1
        and int(metrics["uploaded_bytes"]) > 0
        and int(metrics["gpu_generation"]) == int(metrics["generation"])
        and int(metrics.get("last_upload_bytes", -1)) == 0
        and isinstance(revisions, dict)
        and all(isinstance(revisions.get(field), int) for field in required_revisions)
    )
    result["reason"] = (
        "water_interaction_field_steady_state_ok"
        if result["pass"]
        else "water_interaction_field_metrics_invalid_or_uploaded_during_capture"
    )
    return result


def _fl4216_temporal_identity(value) -> str:
    if isinstance(value, dict):
        value = {str(key): value[key] for key in sorted(value)}
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _fl4216_temporal_contact_rows(dump: dict) -> tuple[dict[str, dict[str, tuple]], dict]:
    by_class = {source_class: {} for source_class in FL4216_TEMPORAL_SOURCE_CLASSES}
    diagnostics = {
        "cells_read": 0,
        "contact_quarters_read": 0,
        "json_parse_errors": 0,
        "missing_identity_rows": 0,
    }
    if isinstance(dump, dict) and dump.get("capture_schema") == "fl4216_temporal_snapshot.v1":
        decoded_rows, snapshot_diagnostics = _fl4216_temporal_snapshot_rows(dump)
        diagnostics.update(snapshot_diagnostics)
        for row in decoded_rows:
            screen = _fl4216_temporal_identity(row.get("screen_cell"))
            final_paint = row.get("final_cell_paint_buffer", {})
            final_glyph = final_paint.get("glyph_id") if isinstance(final_paint, dict) else None
            break_glyph_id = (
                int(final_glyph)
                if isinstance(final_glyph, (int, float))
                and not isinstance(final_glyph, bool)
                and int(final_glyph) in FL4216_SHORELINE_BREAK_GLYPH_ID_SET
                else None
            )
            final_mask = final_paint.get("final_mask_glyph_ids", []) if isinstance(final_paint, dict) else []
            break_mask = tuple(
                int(glyph)
                for glyph in final_mask
                if isinstance(glyph, (int, float))
                and not isinstance(glyph, bool)
                and int(glyph) in FL4216_SHORELINE_BREAK_GLYPH_ID_SET
            )
            for quarter in row.get("selected_surface_quarters", []):
                if not isinstance(quarter, dict) or not quarter.get("valid") or not quarter.get("direct_water_solid_contact"):
                    continue
                source_class = FL4216_TEMPORAL_SOURCE_OWNER_KINDS.get(
                    int(quarter.get("submerged_source_owner_kind", 0) or 0)
                )
                if source_class is None or quarter.get("stable_world_anchor") is None:
                    diagnostics["missing_identity_rows"] += 1
                    continue
                identity = f"{screen}|{_fl4216_temporal_identity(quarter.get('stable_world_anchor'))}"
                by_class[source_class][identity] = {
                    "final_paint_glyph_id": int(final_glyph) if isinstance(final_glyph, (int, float)) and not isinstance(final_glyph, bool) else None,
                    "break_glyph_id": break_glyph_id,
                    "break_state": (break_glyph_id, break_mask),
                }
        diagnostics["reason"] = "contact_rows_ok" if diagnostics.get("readback_state") == "temporal_snapshot_decode_ok" else diagnostics.get("readback_state")
        return by_class, diagnostics
    paths = dump.get("artifact_paths", {}) if isinstance(dump, dict) else {}
    cells_path = Path(paths.get("cells_jsonl", "")) if isinstance(paths, dict) else Path("")
    if not cells_path.is_file():
        diagnostics["reason"] = "missing_cells_jsonl"
        return by_class, diagnostics
    try:
        with cells_path.open("r", encoding="utf-8") as cells_file:
            for line in cells_file:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    diagnostics["json_parse_errors"] += 1
                    continue
                if not isinstance(row, dict):
                    continue
                diagnostics["cells_read"] += 1
                screen = _fl4216_temporal_identity(row.get("screen_cell"))
                for quarter in row.get("selected_surface_quarters", []):
                    if not isinstance(quarter, dict) or not quarter.get("valid"):
                        continue
                    if not quarter.get("direct_water_solid_contact"):
                        continue
                    source_class = FL4216_TEMPORAL_SOURCE_OWNER_KINDS.get(
                        int(quarter.get("submerged_source_owner_kind", 0) or 0)
                    )
                    if source_class is None:
                        continue
                    diagnostics["contact_quarters_read"] += 1
                    if row.get("screen_cell") is None or quarter.get("stable_world_anchor") is None:
                        diagnostics["missing_identity_rows"] += 1
                        continue
                    anchor = _fl4216_temporal_identity(quarter.get("stable_world_anchor"))
                    identity = f"{screen}|{anchor}"
                    final_paint = row.get("final_cell_paint_buffer")
                    final_paint = final_paint if isinstance(final_paint, dict) else {}
                    final_glyph = final_paint.get("glyph_id")
                    if isinstance(final_glyph, bool) or not isinstance(final_glyph, (int, float)):
                        final_glyph = None
                    else:
                        final_glyph = int(final_glyph)
                    break_glyph_id = (
                        final_glyph
                        if final_glyph in FL4216_SHORELINE_BREAK_GLYPH_ID_SET
                        else None
                    )
                    final_mask = final_paint.get("final_mask_glyph_ids", [])
                    if not isinstance(final_mask, list):
                        final_mask = []
                    break_mask = tuple(
                        int(glyph)
                        for glyph in final_mask
                        if isinstance(glyph, (int, float))
                        and not isinstance(glyph, bool)
                        and int(glyph) in FL4216_SHORELINE_BREAK_GLYPH_ID_SET
                    )
                    # Deliberately exclude top-level glyph/fg/bg helpers. The
                    # final-paint SSBO glyph and its break mask are the only
                    # morphology identity consumed by this temporal gate.
                    payload = {
                        "final_paint_glyph_id": final_glyph,
                        "break_glyph_id": break_glyph_id,
                        "break_state": (break_glyph_id, break_mask),
                    }
                    by_class[source_class][identity] = payload
    except OSError:
        diagnostics["reason"] = "unreadable_cells_jsonl"
        return by_class, diagnostics
    diagnostics["reason"] = (
        "contact_rows_ok" if diagnostics["json_parse_errors"] == 0 else "contact_rows_json_invalid"
    )
    return by_class, diagnostics


def _compare_fl4216_temporal_frames(frames: list[dict]) -> dict:
    """Compare consecutive contact products; absent classes stay explicitly unproven."""
    empty_class = {
        source_class: {"status": "unproven_absent", "contact_count": 0}
        for source_class in FL4216_TEMPORAL_SOURCE_CLASSES
    }
    result = {
        "pass": False,
        "frame_count": len(frames),
        "source_classes": empty_class,
        "consecutive_pairs": [],
        "exact_coverage": {},
        "reason": "fewer_than_two_temporal_frames",
    }
    if len(frames) < 2:
        return result
    per_frame = []
    all_diagnostics = []
    for frame in frames:
        rows, diagnostics = _fl4216_temporal_contact_rows(
            frame.get("final_render_cell_dump", {}) if isinstance(frame, dict) else {}
        )
        per_frame.append(rows)
        all_diagnostics.append(diagnostics)
    result["frame_diagnostics"] = all_diagnostics
    if any(
        item.get("json_parse_errors", 0)
        or item.get("missing_identity_rows", 0)
        or item.get("reason", "").startswith("missing")
        for item in all_diagnostics
    ):
        result["reason"] = "temporal_contact_rows_unreadable"
        return result
    claimed = {
        source_class
        for rows in per_frame
        for source_class, identities in rows.items()
        if identities
    }
    aggregate = {}
    for source_class in FL4216_TEMPORAL_SOURCE_CLASSES:
        contact_counts = [len(rows[source_class]) for rows in per_frame]
        break_counts = [
            sum(1 for payload in rows[source_class].values() if payload["break_glyph_id"] is not None)
            for rows in per_frame
        ]
        aggregate[source_class] = {
            "status": "unproven_absent" if source_class not in claimed else "claimed",
            "contact_count": sum(contact_counts),
            "contact_count_by_frame": contact_counts,
            "break_glyph_count_by_frame": break_counts,
            "break_coverage_pct_by_frame": [
                100.0 * break_count / contact_count if contact_count else None
                for break_count, contact_count in zip(break_counts, contact_counts)
            ],
            "missing_break_frames": [
                index for index, (break_count, contact_count)
                in enumerate(zip(break_counts, contact_counts))
                if contact_count > 0 and break_count == 0
            ],
            "comparable_count": 0,
            "break_state_changed_count": 0,
            "changed_count": 0,
            "changed_pct": None,
            "exact_coverage": [],
        }
    for frame_index in range(1, len(per_frame)):
        pair = {"frame_a": frame_index - 1, "frame_b": frame_index, "source_classes": {}}
        for source_class in FL4216_TEMPORAL_SOURCE_CLASSES:
            left = per_frame[frame_index - 1][source_class]
            right = per_frame[frame_index][source_class]
            matched = set(left) & set(right)
            break_state_changed = sum(
                1
                for identity in matched
                if left[identity]["break_state"] != right[identity]["break_state"]
            )
            denominator = max(len(left), len(right))
            coverage = {
                "matched_count": len(matched),
                "denominator": denominator,
                "exact_coverage_pct": (100.0 * len(matched) / denominator) if denominator else None,
            }
            pair["source_classes"][source_class] = {
                "contact_count_a": len(left),
                "contact_count_b": len(right),
                "comparable_count": len(matched),
                "break_glyph_count_a": sum(
                    1 for payload in left.values() if payload["break_glyph_id"] is not None
                ),
                "break_glyph_count_b": sum(
                    1 for payload in right.values() if payload["break_glyph_id"] is not None
                ),
                "break_state_changed_count": break_state_changed,
                "changed_count": break_state_changed,
                "changed_pct": (100.0 * break_state_changed / len(matched)) if matched else None,
                "exact_coverage": coverage,
            }
            aggregate[source_class]["comparable_count"] += len(matched)
            aggregate[source_class]["break_state_changed_count"] += break_state_changed
            aggregate[source_class]["changed_count"] += break_state_changed
            aggregate[source_class]["exact_coverage"].append(coverage)
        result["consecutive_pairs"].append(pair)
    for source_class, summary in aggregate.items():
        comparable = summary["comparable_count"]
        summary["changed_pct"] = (100.0 * summary["changed_count"] / comparable) if comparable else None
        if source_class in claimed:
            if summary["missing_break_frames"]:
                summary["status"] = "unproven_missing_break_glyph"
            elif comparable == 0:
                summary["status"] = "unproven_no_comparable_identity"
            elif summary["changed_count"] == 0:
                summary["status"] = "static_no_changed_break_morphology"
            else:
                summary["status"] = "observed_changed"
    result["source_classes"] = aggregate
    result["exact_coverage"] = {
        source_class: summary["exact_coverage"] for source_class, summary in aggregate.items()
    }
    valid_claims = [aggregate[source_class] for source_class in claimed]
    required_classes_present = FL4216_P1_REQUIRED_SOURCE_CLASSES.issubset(claimed)
    result["required_source_classes"] = sorted(FL4216_P1_REQUIRED_SOURCE_CLASSES)
    result["required_source_classes_present"] = sorted(
        FL4216_P1_REQUIRED_SOURCE_CLASSES.intersection(claimed)
    )
    if required_classes_present and valid_claims and all(
        not summary["missing_break_frames"]
        and summary["comparable_count"] > 0
        and summary["break_state_changed_count"] > 0
        for summary in valid_claims
    ):
        result["pass"] = True
        result["reason"] = "temporal_shoreline_morphology_comparison_ok"
    elif not required_classes_present:
        result["reason"] = "required_p1_source_class_absent"
    elif any(summary["missing_break_frames"] for summary in valid_claims):
        result["reason"] = "claimed_class_missing_break_glyph_in_frame"
    elif any(summary["comparable_count"] == 0 for summary in valid_claims):
        result["reason"] = "claimed_class_has_zero_comparable_cells"
    else:
        result["reason"] = "claimed_class_has_no_changed_break_morphology"
    return result


def _fl4216_temporal_product_frame_time(frame: dict) -> float | None:
    snapshot = frame.get("runtime_truth_snapshot") if isinstance(frame, dict) else None
    if not isinstance(snapshot, dict):
        return None
    try:
        value = float(snapshot.get("frame_time_seconds"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _validate_fl4216_temporal_frame_timing(
    frames: list[dict], requested_interval_seconds: float,
) -> dict:
    product_times = [_fl4216_temporal_product_frame_time(frame) for frame in frames]
    product_deltas = [
        product_times[index] - product_times[index - 1]
        for index in range(1, len(product_times))
        if product_times[index] is not None and product_times[index - 1] is not None
    ]
    missing = len(product_deltas) != max(0, len(frames) - 1)
    in_window = bool(product_deltas) and all(
        FL4216_TEMPORAL_PRODUCT_DELTA_MIN_SECONDS <= delta <= FL4216_TEMPORAL_PRODUCT_DELTA_MAX_SECONDS
        for delta in product_deltas
    )
    return {
        "pass": bool(len(frames) >= 2 and not missing and in_window),
        "reason": "temporal_product_frame_delta_ok"
        if len(frames) >= 2 and not missing and in_window
        else "missing_temporal_product_frame_time" if missing
        else "temporal_product_frame_delta_out_of_window",
        "requested_interval_seconds": float(requested_interval_seconds),
        "product_frame_time_seconds": product_times,
        "product_frame_delta_seconds": product_deltas,
        "product_delta_window_seconds": [
            FL4216_TEMPORAL_PRODUCT_DELTA_MIN_SECONDS,
            FL4216_TEMPORAL_PRODUCT_DELTA_MAX_SECONDS,
        ],
        "requested_sleep_is_not_product_delta": True,
    }


def _fl4216_temporal_capture_accepts_analysis_skip(
    summary: dict,
    receipt: dict,
    skip_jitter_analysis: bool,
) -> bool:
    """Allow the visual analyzer skip only for the bounded FL-4216 arm."""
    frames = receipt.get("fl4216_temporal_frames")
    if not isinstance(frames, list) or len(frames) < DEFAULT_FL4216_TEMPORAL_CAPTURE_COUNT:
        return False
    if receipt.get("fl4216_temporal_capture") is not True:
        return False
    if receipt.get("fl4216_temporal_same_process") is not True:
        return False
    if receipt.get("camera_mode") not in ("perspective", "ortho"):
        return False
    comparison = receipt.get("fl4216_temporal_comparison")
    if not isinstance(comparison, dict) or comparison.get("pass") is not True:
        return False
    pose_contract = receipt.get("fl4216_temporal_pose_contract")
    if not isinstance(pose_contract, dict) or pose_contract.get("pass") is not True:
        return False
    timing_contract = receipt.get("fl4216_temporal_timing_contract")
    if not isinstance(timing_contract, dict) or timing_contract.get("pass") is not True:
        return False
    field_contract = receipt.get("fl4216_water_interaction_field_contract")
    if not isinstance(field_contract, dict) or field_contract.get("pass") is not True:
        return False
    interval = _float_value(receipt.get("fl4216_temporal_interval_seconds"), 0.0)
    if interval < MIN_FL4216_TEMPORAL_CAPTURE_INTERVAL_SECONDS:
        return False
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict) or int(frame.get("capture_index", -1)) != index:
            return False
        if frame.get("same_godot_process") is not True:
            return False
        validation = frame.get("runtime_field_contract")
        if not isinstance(validation, dict) or validation.get("pass") is not True:
            return False
        pose_validation = frame.get("artifact_pose_contract")
        if not isinstance(pose_validation, dict) or pose_validation.get("pass") is not True:
            return False
        if not isinstance(frame.get("final_render_cell_dump"), dict):
            return False
        if not isinstance(frame.get("screenshot"), str) or not Path(frame["screenshot"]).is_file():
            return False
        if index > 0 and _float_value(frame.get("elapsed_since_previous_seconds"), 0.0) < interval:
            return False
    return bool(
        skip_jitter_analysis
        and summary.get("analysis_skipped") is True
        and summary.get("reason") == "canonical_png_grid_capture_only"
    )


def _wait_for(predicate, timeout: float, interval: float = 0.25) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _godot_subprocess_timeout(raw_timeout: float) -> float:
    timeout = float(raw_timeout)
    if not math.isfinite(timeout):
        return timeout
    if timeout <= 0.0:
        return DEFAULT_GODOT_TIMEOUT_SECONDS
    return timeout


def _timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _default_output_dir(args: argparse.Namespace, *, timestamp: str | None = None) -> Path:
    stamp = timestamp if timestamp is not None else _timestamp()
    if args.camera_mode in ("plane_air", "plane_air_ortho"):
        return ROOT / ".run" / f"fl4359_airplane_movement_{stamp}"
    return ROOT / ".run" / f"fl4359_headed_movement_{stamp}"


def _parse_resolution(value: str) -> tuple[int, int]:
    raw = value.lower().replace("x", " ").replace(",", " ").split()
    if len(raw) != 2:
        raise argparse.ArgumentTypeError("resolution must be WIDTHxHEIGHT")
    width = int(raw[0])
    height = int(raw[1])
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("resolution dimensions must be positive")
    return width, height


def _stitch_movement_grid(receipt: dict, out_dir: Path) -> Path | None:
    screenshot_paths = [Path(p) for p in receipt.get("screenshots", [])]
    screenshot_paths = [p for p in screenshot_paths if p.exists()]
    if not screenshot_paths:
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    images = [Image.open(path).convert("RGB") for path in screenshot_paths]
    max_panel_w = 640
    panel_w = min(max(image.width for image in images), max_panel_w)
    label_h = 58
    panels = []
    font = ImageFont.load_default()
    for idx, image in enumerate(images):
        scale = panel_w / float(image.width)
        panel_h = max(1, int(round(float(image.height) * scale)))
        resized = image.resize((panel_w, panel_h), Image.Resampling.LANCZOS)
        panel = Image.new("RGB", (panel_w, panel_h + label_h), (18, 18, 18))
        panel.paste(resized, (0, label_h))
        draw = ImageDraw.Draw(panel)
        path = screenshot_paths[idx]
        label = "%s | %s | ASCII ON | mode=%s" % (
            path.name,
            "FL-4359 FL-4388 FL-4391",
            receipt.get("camera_mode", "unknown"),
        )
        detail = "movement_delta=%.3f camera_delta=%.3f airplane_faces=%d pass=%s" % (
            float(receipt.get("movement_delta", 0.0)),
            float(receipt.get("camera_delta", 0.0)),
            int(receipt.get("airplane_dynamic_face_count", 0)),
            str(bool(receipt.get("pass", False))).lower(),
        )
        draw.text((8, 8), label, fill=(245, 245, 245), font=font)
        draw.text((8, 30), detail, fill=(210, 230, 255), font=font)
        panels.append(panel)

    cols = 2
    rows = (len(panels) + cols - 1) // cols
    panel_h = max(panel.height for panel in panels)
    grid = Image.new("RGB", (cols * panel_w, rows * panel_h), (28, 28, 28))
    for idx, panel in enumerate(panels):
        x = (idx % cols) * panel_w
        y = (idx // cols) * panel_h
        grid.paste(panel, (x, y))
    out_path = out_dir / "movement_grid.png"
    grid.save(out_path)
    return out_path


def _write_invalid_summary(
    summary_path: Path,
    *,
    reason: str,
    godot_rc: int,
    receipt_path: Path,
    capture_path: Path,
    repo_provenance: dict | None = None,
    analyzer_rc: int = 1,
    run_classification: str = RUN_CLASSIFICATION_MOVEMENT_PROOF,
) -> None:
    wrapper_provenance = dict(repo_provenance or {})
    summary_path.write_text(
        json.dumps(
            {
                "admissible": False,
                "analysis_skipped": True,
                "run_classification": run_classification,
                "proof_classification": (
                    DIAGNOSTIC_PROOF_CLASSIFICATION
                    if run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
                    else RUN_CLASSIFICATION_MOVEMENT_PROOF
                ),
                "diagnostic_only": run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY,
                "proof": (
                    "bounded_static_visual_diagnostic"
                    if run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
                    else "movement_proof_inadmissible"
                ),
                "pass": False,
                "proof_pass": False,
                "frame_count": 0,
                "max_pair_jitter_pct": 100.0,
                "max_snap_px_delta": 0.0,
                "avg_snap_px_delta": 0.0,
                "reason": reason,
                "process_timed_out": godot_rc == 124,
                "godot_rc": godot_rc,
                "analyzer_rc": analyzer_rc,
                "presentation_profile": wrapper_provenance.get("presentation_profile", ""),
                "wrapper_git_head": wrapper_provenance.get("git_head", ""),
                "git_head": wrapper_provenance.get("git_head", ""),
                "git_dirty": bool(wrapper_provenance.get("git_dirty", False)),
                "git_dirty_count": int(wrapper_provenance.get("git_dirty_count", 0)),
                "git_dirty_paths": list(wrapper_provenance.get("git_dirty_paths", [])),
                "receipt_present": receipt_path.exists(),
                "capture_present": capture_path.exists(),
                "receipt_path": str(receipt_path),
                "capture_path": str(capture_path),
                "provenance": {
                    "source": "scripts/run_fl4359_headed_movement.py",
                    "wrapper": wrapper_provenance,
                    "runtime": {
                        "godot_rc": godot_rc,
                        "analyzer_rc": analyzer_rc,
                        "receipt_path": str(receipt_path),
                        "capture_path": str(capture_path),
                    },
                },
                "rule": INADMISSIBLE_TIMEOUT_RULE,
            },
            indent=2,
        )
        + "\n"
    )


def _invalid_run_reason(godot_rc: int, receipt_path: Path, capture_path: Path) -> str:
    if godot_rc == 124:
        return "timed_out_before_completion_receipt"
    if not receipt_path.exists():
        return "missing_completion_receipt"
    if not capture_path.exists():
        return "missing_jitter_capture_jsonl"
    return ""


def _has_noisy_completion_arg(extra_args: list[str]) -> str:
    for extra_arg in extra_args:
        if extra_arg in NOISY_COMPLETION_GUARD_ARGS:
            return extra_arg
    return ""


def _shell_command(parts: list[object]) -> str:
    return shlex.join([str(part) for part in parts])


def _runner_command() -> str:
    return _shell_command([sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])


def _worktree_diff_digest() -> tuple[int, str]:
    # FL-4691: dirty binary assets can produce a multi-gigabyte diff. Hash the
    # exact stream without retaining it in the runner's address space.
    digest = hashlib.sha256()
    with subprocess.Popen(
        ["git", "diff", "HEAD", "--binary"], cwd=str(ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    ) as process:
        assert process.stdout is not None
        for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
            digest.update(chunk)
        return_code = process.wait()
    return return_code, digest.hexdigest() if return_code == 0 else ""


# FL-4359: every repository-identity git call on the proof path is bounded.
# A wedged status once held preflight past 100s with zero output; the gate
# must fail explicitly instead. No shell, no retry, no broad cleanup.
_REPO_PROVENANCE_TIMEOUT_SECONDS = 30


def _bounded_git_run(argv: list[str], cwd: Path) -> tuple[int, bytes, str]:
    """Run one git identity command with a finite ceiling.

    Returns (returncode, stdout_bytes, outcome) where outcome is "ok",
    "timeout", or "failed". Timeout uses the conventional 124 marker.
    """
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_REPO_PROVENANCE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return 124, b"", "timeout"
    if proc.returncode != 0:
        return proc.returncode, b"", "failed"
    return 0, proc.stdout, "ok"


def _repo_provenance(*, preflight_only: bool = False) -> dict:
    head_rc, head_out, head_outcome = _bounded_git_run(["git", "rev-parse", "HEAD"], ROOT)
    short_rc, short_out, short_outcome = _bounded_git_run(["git", "rev-parse", "--short", "HEAD"], ROOT)
    status_rc, status_out, status_outcome = _bounded_git_run(["git", "status", "--short"], ROOT)
    git_head = head_out.decode("utf-8", errors="surrogateescape").strip() if head_outcome == "ok" else ""
    # Recording identity uses the rev-parse --short output verbatim, never a
    # fixed-width truncation of the full head (lane FF-DPR-20260920-25).
    git_head_short = short_out.decode("utf-8", errors="surrogateescape").strip() if short_outcome == "ok" else ""
    # An argv/preflight check executes no renderer source. Its receipt must
    # state that this capture identity was not collected, rather than spending
    # minutes encoding unrelated dirty binary assets before returning a check.
    diff_rc, worktree_diff_sha256 = (None, "") if preflight_only else _worktree_diff_digest()
    dirty_paths: list[str] = []
    if status_outcome == "ok":
        dirty_paths = [line.strip() for line in status_out.decode("utf-8", errors="surrogateescape").splitlines() if line.strip()]
    if head_outcome == "ok" and status_outcome == "ok":
        provenance_status = "ok"
    elif head_outcome != "ok":
        provenance_status = "git_head_" + head_outcome
    else:
        provenance_status = "git_status_" + status_outcome
    return {
        "git_head": git_head,
        "git_head_short": git_head_short,
        "git_dirty": bool(dirty_paths),
        "git_dirty_count": len(dirty_paths),
        "git_dirty_paths": dirty_paths,
        "git_head_rc": int(head_rc),
        "git_status_rc": int(status_rc),
        "git_head_outcome": head_outcome,
        "git_status_outcome": status_outcome,
        "provenance_status": provenance_status,
        "worktree_diff_sha256": worktree_diff_sha256,
        "worktree_diff_rc": diff_rc,
        "worktree_diff_status": "not_requested_preflight" if preflight_only else (
            "captured" if diff_rc == 0 else "failed"),
    }


def _recording_git_identity(repo_provenance: dict) -> str:
    """Short-head plus dirty/clean identity for F9 recording names.

    The head is the `git rev-parse --short HEAD` output used verbatim,
    never truncated to a fixed width (lane FF-DPR-20260920-25). Computed
    once at launch from `git status --porcelain` state the runner already
    collected. A missing head never reads as clean: it reports unknown,
    and the provenance preflight gate already fails such launches.
    """
    short = str((repo_provenance or {}).get("git_head_short", "") or "")
    if not short:
        return "unknown"
    dirty = bool((repo_provenance or {}).get("git_dirty", True))
    return "%s-%s" % (short, "dirty" if dirty else "clean")

def _launch_source_identity(repo_provenance: dict, run_identity: dict | None = None,
                            source_root: Path | None = None) -> str:
    """JSON for ASCIICKER_LAUNCH_SOURCE_IDENTITY (FL-4873).

    Collected once at launch from the provenance the runner already gathered;
    git_dirty is omitted (not guessed) when git status did not succeed.
    run_identity (FL-4888, RunSourceProvenance) names the preserved start
    snapshot; World and every F9 manifest of this run read it from here.
    """
    prov = repo_provenance or {}
    sys.path.insert(0, str(ROOT / "scripts" / "recording"))
    import f9_source_identity  # noqa: E402

    executed_files, executed_status = f9_source_identity.executed_source_files(source_root)
    executed_sha = f9_source_identity.inventory_sha256(executed_files) if executed_status == "captured" else ""
    doc = {
        "git_head": str(prov.get("git_head", "") or ""),
        "git_head_short": str(prov.get("git_head_short", "") or ""),
        "executed_source_sha256": executed_sha,
        "executed_source_status": executed_status,
        "executed_source_paths": list(f9_source_identity.TRUE_FF_SCOPE),
        "executed_source_files": executed_files,
        "worktree_diff_sha256": str(prov.get("worktree_diff_sha256", "") or ""),
        "worktree_diff_status": str(prov.get("worktree_diff_status", "") or ""),
        "provenance_status": str(prov.get("provenance_status", "unknown")),
        "git_dirty_count": prov.get("git_dirty_count"),
        "captured_by": "scripts/run_fl4359_headed_movement.py",
        "captured_unix_s": round(time.time(), 3),
    }
    if prov.get("git_status_outcome") == "ok":
        doc["git_dirty"] = bool(prov.get("git_dirty", True))
    if run_identity:
        doc["run_identity"] = run_identity
    return json.dumps(doc, sort_keys=True)


def _apply_provenance_preflight_gate(preflight: dict, repo_provenance: dict) -> dict:
    """Fail preflight explicitly when repository provenance is unavailable.

    A missing or timed-out git status must never read as a clean tree, so a
    non-ok provenance status appends the named provenance_available failure
    and clears the preflight ok flag.
    """
    provenance_status = str(repo_provenance.get("provenance_status", "unknown"))
    preflight["provenance_status"] = provenance_status
    if provenance_status != "ok":
        preflight["ok"] = False
        preflight.setdefault("failed_checks", []).append("provenance_available")
    return preflight


def _runtime_source_identity(scopes: list[Path]) -> dict:
    """Hash actual launch files, including ignored isolated runtime copies.

    Git establishes the revision and overlay labels. Direct, bounded file reads
    establish executed input identity; an ignored scope cannot hash as empty.
    """
    root_rc, root_out, root_outcome = _bounded_git_run(
        ["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"], ROOT,
    )
    if root_outcome == "timeout":
        return {"valid": False, "reason": "git_root_timeout"}
    if root_rc != 0:
        return {"valid": False, "reason": "git_root_unavailable"}
    source_root = Path(root_out.decode("utf-8", errors="surrogateescape").strip()).resolve()
    relative_scopes: list[str] = []
    for scope in scopes:
        if not scope.exists():
            return {"valid": False, "reason": f"scope_missing:{scope}"}
        try:
            relative = str(scope.resolve().relative_to(source_root))
        except ValueError:
            return {"valid": False, "reason": f"scope_outside_repo:{scope}"}
        if relative not in relative_scopes:
            relative_scopes.append(relative)
    head_rc, head_out, head_outcome = _bounded_git_run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"], ROOT,
    )
    if head_outcome == "timeout":
        return {"valid": False, "reason": "git_head_timeout"}
    head = head_out.decode("utf-8", errors="surrogateescape").strip()
    if head_rc != 0 or re.fullmatch(r"[0-9a-f]{40}", head) is None:
        return {"valid": False, "reason": "git_head_unavailable"}
    status_rc, status_out, status_outcome = _bounded_git_run(
        ["git", "-C", str(source_root), "status", "--porcelain=v1", "-z",
         "--untracked-files=all", "--", *relative_scopes], ROOT,
    )
    untracked_rc, untracked_out, untracked_outcome = _bounded_git_run(
        ["git", "-C", str(source_root), "ls-files", "--others",
         "--exclude-standard", "-z", "--", *relative_scopes], ROOT,
    )
    if "timeout" in (status_outcome, untracked_outcome):
        return {"valid": False, "reason": "scoped_git_identity_timeout"}
    if status_rc != 0 or untracked_rc != 0:
        return {"valid": False, "reason": "scoped_git_identity_command_failed"}
    excluded_directories = {".git", ".godot", ".run", "__pycache__", ".pytest_cache", ".cache"}
    inputs: dict[str, Path] = {}
    excluded_paths: list[str] = []
    try:
        for relative in relative_scopes:
            scope = source_root / relative
            if scope.is_file():
                inputs[relative] = scope
                continue
            if not scope.is_dir():
                raise ValueError(f"unsupported_source_scope:{relative}")
            # Exclusions apply below each explicit scope. A requested .run
            # runtime itself is included; only nested generated caches are not.
            for directory, dirs, files in os.walk(scope, followlinks=False):
                kept = []
                for name in sorted(dirs):
                    child = Path(directory) / name
                    label = str(child.relative_to(source_root))
                    if name in excluded_directories:
                        excluded_paths.append(label)
                        # Imported textures/resources and the global class map
                        # are runtime inputs, unlike shader/editor caches. A
                        # stale imported resource can alter output while its
                        # source PNG and all tracked scripts remain unchanged.
                        if name == ".godot":
                            imported = child / "imported"
                            if imported.is_dir():
                                for imported_directory, imported_dirs, imported_files in os.walk(imported, followlinks=False):
                                    if any((Path(imported_directory) / item).is_symlink() for item in imported_dirs):
                                        raise ValueError(f"source_directory_symlink_requires_explicit_scope:{label}/imported")
                                    for imported_name in sorted(imported_files):
                                        imported_path = Path(imported_directory) / imported_name
                                        inputs[str(imported_path.relative_to(source_root))] = imported_path
                            class_cache = child / "global_script_class_cache.cfg"
                            if class_cache.is_file():
                                inputs[str(class_cache.relative_to(source_root))] = class_cache
                    elif child.is_symlink():
                        raise ValueError(f"source_directory_symlink_requires_explicit_scope:{label}")
                    else:
                        kept.append(name)
                dirs[:] = kept
                for name in sorted(files):
                    child = Path(directory) / name
                    if name == ".DS_Store":
                        continue
                    inputs[str(child.relative_to(source_root))] = child
        if not inputs:
            raise ValueError("source_scope_contains_no_files")
        inventory = []
        for label, path in sorted(inputs.items()):
            before = path.stat()
            if not path.is_file():
                raise ValueError(f"unsupported_source_input:{label}")
            file_digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    file_digest.update(chunk)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (
                after.st_size, after.st_mtime_ns, after.st_ino
            ):
                raise ValueError(f"source_input_changed_during_hash:{label}")
            record = {"path": label, "bytes": after.st_size, "sha256": file_digest.hexdigest()}
            if path.is_symlink():
                record["symlink_target"] = os.readlink(path)
            inventory.append(record)
    except (OSError, ValueError) as exc:
        return {"valid": False, "reason": str(exc)}
    fingerprint_schema = "fl4359-scoped-source-identity-v3"
    digest = hashlib.sha256(json.dumps(
        {"schema": fingerprint_schema, "scopes": relative_scopes, "files": inventory},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()).hexdigest()
    return {
        "valid": True, "source_root": str(source_root), "scopes": relative_scopes,
        "source_commit": head, "effective_tree_fingerprint": digest,
        "dirty_paths": [item.decode("utf-8", errors="surrogateescape")
                        for item in status_out.split(b"\0") if item],
        "untracked_paths": [item.decode("utf-8", errors="surrogateescape")
                            for item in untracked_out.split(b"\0") if item],
        "excluded_untracked_paths": excluded_paths,
        "fingerprint_schema": fingerprint_schema,
        "executed_source_files": inventory,
        "execution_kind": "direct_source_launch", "exported_build_identity": "UNKNOWN",
    }


def _persist_runtime_source_identity(receipt_path: Path, launch: dict, scopes: list[Path]) -> None:
    """Run in the launcher finally block, including observer/menu early returns."""
    if launch.get("execution_kind") == "materialized_snapshot":
        completion = _materialized_source_identity(
            {"root": launch["source_root"], "snapshot": launch["snapshot"], "head": launch.get("source_commit"),
             "digest": launch["effective_tree_fingerprint"]})
    else:
        completion = _runtime_source_identity(scopes)
    identity = {
        "launch": launch, "completion": completion,
        "source_commit_matches": launch.get("source_commit") == completion.get("source_commit"),
        "scoped_snapshots_match": (
            launch.get("valid") is True and completion.get("valid") is True
            and launch.get("fingerprint_schema") == completion.get("fingerprint_schema")
            and launch.get("effective_tree_fingerprint") == completion.get("effective_tree_fingerprint")
        ),
        "limitations": [
            "two endpoint snapshots do not prove that scoped inputs never changed during capture",
            "direct source launch identity; no exported build was launched",
            "runtime architecture and glyph-source identity must be recorded separately",
        ],
    }
    # Preserve source evidence even if Godot failed before writing its receipt.
    receipt_path.with_name("runtime_source_identity.json").write_text(json.dumps(identity, indent=2) + "\n")
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        receipt["publication_runtime_source_identity"] = identity
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")


# FL-4873/FL-4888: run source provenance. The launcher
# (scripts/launch_fl4359_manual_play.py) owns the runtime source snapshot; this
# runner reuses it at run start and run end, widened to the files this runner
# executes itself. The record goes into receipt.json and
# run_source_provenance.json and leaves .run through the receipt preserver.
RUN_SOURCE_PROVENANCE_NAME = "run_source_provenance.json"
RUN_SOURCE_PROVENANCE_SCHEMA = "asciicker.run_source_provenance.v1"
RUNNER_EXTRA_SOURCE_PATHSPECS = (
    "scripts/run_fl4359_headed_movement.py",
    "scripts/run_receipt_preservation.py",
)
F9_BUNDLE_FINALIZE_WAIT_SECONDS = 600.0
_GODOT_WHITESPACE = "".join(chr(code) for code in range(33))
_F9_NAMED_RE = re.compile(r"FL4359_SCREEN_RECORDING named key=F9 bundle=(.+?) mov=")
_F9_RENAMED_RE = re.compile(r"FL4359_SCREEN_RECORDING renamed key=F9 from=(.+?) to=(.+?) reason=")


# The recorder (godot_project/scripts/recording/ScreenRecordingController.gd,
# BUNDLE_FINALIZER_RES) starts this detached finalizer from the tree Godot
# executes. A replay on a materialized snapshot runs it from the snapshot, so
# every script the finalizer chain executes must be inside the snapshot
# pathspecs (FL-4873 replay A: html failed because
# scripts/build_f10_frames_html.py was outside the snapshot).
F9_FINALIZER_ENTRY = "scripts/recording/f9_finalize_bundle.py"
_F9_CHAIN_SEARCH_DIRS = ("scripts/recording", "scripts")


def f9_finalizer_chain_sources(root: Path | None = None) -> tuple[str, ...]:
    """Repository-relative Python files the F9 finalizer chain executes.

    Static walk from F9_FINALIZER_ENTRY: each file's top-level-or-nested
    `import X` / `from X import` (resolved in its own directory, then
    scripts/recording, then scripts, the sys.path entries the chain inserts)
    and each "*.py" string constant (subprocess targets such as
    BUILDER = ROOT / "scripts" / "build_f10_frames_html.py"), recursively.
    Names that resolve to no repository file (stdlib, third party) are skipped.
    """
    import ast

    base = root or ROOT
    seen: set[str] = set()
    queue = [F9_FINALIZER_ENTRY]
    while queue:
        rel = queue.pop()
        if rel in seen or not (base / rel).is_file():
            continue
        seen.add(rel)
        try:
            tree = ast.parse((base / rel).read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        own_dir = Path(rel).parent.as_posix()
        dirs = (own_dir,) + tuple(d for d in _F9_CHAIN_SEARCH_DIRS if d != own_dir)
        modules: set[str] = set()
        files: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module.split(".")[0])
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and node.value.endswith(".py") and "\n" not in node.value and " " not in node.value:
                files.add(node.value)
        candidates = [[f"{d}/{m}.py" for d in dirs] for m in sorted(modules)]
        candidates += [[f] if "/" in f else [f"{d}/{f}" for d in dirs] for f in sorted(files)]
        for options in candidates:
            hit = next((c for c in options if (base / c).is_file()), None)
            if hit:
                queue.append(hit)
    return tuple(sorted(seen))


def _covered_by(path: str, specs) -> bool:
    return any(path == s or path.startswith(s.rstrip("/") + "/") for s in specs)


def _runner_source_pathspecs(driver_script: Path | None) -> tuple[str, ...]:
    """The launcher's runtime pathspecs, the files only this runner executes,
    and every script the F9 finalizer chain executes."""
    import launch_fl4359_manual_play as manual_launcher

    specs = list(manual_launcher.RUNTIME_SOURCE_PATHSPECS) + list(RUNNER_EXTRA_SOURCE_PATHSPECS)
    specs += [p for p in f9_finalizer_chain_sources() if not _covered_by(p, specs)]
    if driver_script is not None:
        try:
            relative = driver_script.resolve().relative_to(ROOT.resolve()).as_posix()
        except ValueError:
            relative = ""
        if relative and not _covered_by(relative, specs):
            specs.append(relative)
    return tuple(specs)


def _f9_bundles_from_log(godot_log: Path) -> list[Path]:
    """F9 bundle folders this run recorded, from the recorder's own log lines
    (ScreenRecordingController.gd prints 'named' and, once, 'renamed')."""
    try:
        text = godot_log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    bundles: list[str] = []
    for line in text.splitlines():
        named = _F9_NAMED_RE.search(line)
        if named:
            if named.group(1) not in bundles:
                bundles.append(named.group(1))
            continue
        renamed = _F9_RENAMED_RE.search(line)
        if renamed:
            old, new = renamed.group(1), renamed.group(2)
            bundles = [new if b == old else b for b in bundles]
            if new not in bundles:
                bundles.append(new)
    return [Path(b) for b in bundles]


def _wait_for_f9_finalize(bundle: Path, timeout: float) -> str:
    """Bounded wait for the detached F9 finalizer (PNG conversion, MOV, HTML).

    Called only after Godot has exited. The recorder writes the "finalizing"
    completion.json synchronously before it launches the detached finalizer
    (ScreenRecordingController.gd _write_completion("finalizing")), so an
    absent completion.json at this point means no finalizer was ever started
    (e.g. Godot was killed on timeout mid-recording): return at once instead
    of waiting the full timeout for a file nothing will write (FL-4873 run 1d
    waited 600 s here).
    """
    deadline = time.monotonic() + max(0.0, timeout)
    status = "absent"
    while True:
        try:
            status = str(json.loads((bundle / "completion.json").read_text(encoding="utf-8")).get("status", ""))
        except (OSError, json.JSONDecodeError):
            status = "absent" if not (bundle / "completion.json").exists() else "unreadable"
        if status not in ("finalizing", "unreadable") or time.monotonic() >= deadline:
            return status
        time.sleep(1.0)


def _find_key(doc: object, key: str) -> object:
    """First value stored under key anywhere in a JSON document."""
    if isinstance(doc, dict):
        if key in doc:
            return doc[key]
        values = list(doc.values())
    elif isinstance(doc, list):
        values = list(doc)
    else:
        return None
    for value in values:
        found = _find_key(value, key)
        if found is not None:
            return found
    return None


def _res_to_repo_path(res_path: str) -> str:
    """res://X -> <godot project, repository-relative>/X."""
    try:
        godot_relative = GODOT_PROJECT.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        godot_relative = "godot_project"
    return godot_relative + "/" + res_path[len("res://"):]


def _shader_include_targets(text: str) -> list[str]:
    targets = []
    for line in text.split("\n"):
        stripped = line.strip(_GODOT_WHITESPACE)
        if not stripped.startswith("#include"):
            continue
        target = stripped[len("#include"):].strip(_GODOT_WHITESPACE)
        if target.startswith('"res://') and target.endswith('"'):
            targets.append(target[1:-1])
    return targets


def _decode_godot_text(data: bytes) -> str:
    # FileAccess.get_file_as_string parses UTF-8 and drops a leading BOM.
    return data.decode("utf-8", errors="replace").removeprefix("﻿")


def _expand_shader_source(res_path: str, files: dict[str, bytes | None], ancestors: tuple[str, ...] = ()) -> str:
    """Python mirror of RenderShaderSource.load_source (includes expanded)."""
    if not res_path.startswith("res://") or res_path in ancestors:
        return ""
    data = files.get(_res_to_repo_path(res_path))
    if not data:
        return ""
    lines = _decode_godot_text(data).split("\n")
    for index, line in enumerate(lines):
        stripped = line.strip(_GODOT_WHITESPACE)
        if not stripped.startswith("#include"):
            continue
        target = stripped[len("#include"):].strip(_GODOT_WHITESPACE)
        if not target.startswith('"res://') or not target.endswith('"'):
            return ""
        included = _expand_shader_source(target[1:-1], files, ancestors + (res_path,))
        if not included:
            return ""
        lines[index] = included
    return "\n".join(lines)


def _inject_defines(source: str, defines: list[str]) -> str:
    """Python mirror of RenderPipelineFactory.inject_defines."""
    if not defines:
        return source
    newline = source.find("\n")
    if newline < 0 or not source.startswith("#version"):
        return source
    return source[: newline + 1] + "".join(f"#define {d}\n" for d in defines) + source[newline + 1:]


def _snapshot_shader_files(snapshot_manifest: Path, res_paths: list[str]) -> dict[str, bytes | None]:
    """Every shader file and transitive include, read from the preserved snapshot."""
    import launch_fl4359_manual_play as manual_launcher

    files: dict[str, bytes | None] = {}
    pending = {_res_to_repo_path(p) for p in res_paths if p.startswith("res://")}
    while pending:
        fetched = manual_launcher.snapshot_file_bytes(snapshot_manifest, sorted(pending))
        files.update(fetched)
        pending = set()
        for data in fetched.values():
            if data:
                for target in _shader_include_targets(_decode_godot_text(data)):
                    repo_path = _res_to_repo_path(target)
                    if repo_path not in files:
                        pending.add(repo_path)
    return files


PROBE_RASTER_WITNESS = "FL4359_RASTER_WITNESS"
PROBE_VERTEX_WITNESS = "FL4359_VERTEX_WITNESS"


def _godot_strip(text: str) -> str:
    """String.strip_edges(): removes every char with code <= 32 at both ends."""
    return text.strip(_GODOT_WHITESPACE)


def _probe_capture_stages(expanded: str, defines: list[str]) -> dict[str, str] | None:
    """Mirror of ProbeCaptureStage.compile_graphics_shader (the stage text it
    hands the compiler and records). Recorded defines are
    [specialization?, FL4359_RASTER_WITNESS?, FL4359_VERTEX_WITNESS?]; the
    specialization is the define that is neither witness name.

      vertex_pos = find("#[vertex]"); fragment_pos = find("#[fragment]")
      none when vertex_pos < 0 or fragment_pos <= vertex_pos
      vertex   = substr(vertex_pos + 9, fragment_pos - vertex_pos - 9).strip_edges()
      fragment = substr(fragment_pos + 11).strip_edges()
      specialization: fragment.replace("#version 450", "#version 450\\n#define <S> 1")
      raster marks:   both stages .replace("#version 450", "#version 450\\n#define FL4359_RASTER_WITNESS 1")
      vertex marks:   both stages .replace("#version 450", "#version 450\\n#define FL4359_VERTEX_WITNESS 1")
    (String.replace replaces every occurrence, as str.replace does.)
    """
    vertex_pos, fragment_pos = expanded.find("#[vertex]"), expanded.find("#[fragment]")
    if vertex_pos < 0 or fragment_pos <= vertex_pos:
        return None
    vertex = _godot_strip(expanded[vertex_pos + len("#[vertex]"):fragment_pos])
    fragment = _godot_strip(expanded[fragment_pos + len("#[fragment]"):])
    specializations = [d for d in defines if d not in (PROBE_RASTER_WITNESS, PROBE_VERTEX_WITNESS)]
    if len(specializations) > 1:
        return None
    if specializations:
        fragment = fragment.replace("#version 450", f"#version 450\n#define {specializations[0]} 1")
    for mark in (PROBE_RASTER_WITNESS, PROBE_VERTEX_WITNESS):
        if mark in defines:
            vertex = vertex.replace("#version 450", f"#version 450\n#define {mark} 1")
            fragment = fragment.replace("#version 450", f"#version 450\n#define {mark} 1")
    return {"vertex": vertex, "fragment": fragment}


def _variant_stage_candidates(expanded: str, defines: list[str], raw: str = "") -> list[tuple[str, dict[str, str]]]:
    """(rule, {stage: text}) for every recording compile site (FL-4888).

    A variant counts as re-derived when ANY rule reproduces the recorded
    variant digest: the sha256 match is the evidence, the rule names the site.
    `expanded` is RenderShaderSource.load_source(path) (includes expanded);
    `raw` is FileAccess.get_file_as_string(path) (no include expansion).

      compute_factory        RenderPipelineFactory.compile_compute:
                             expanded.replace("#[compute]\\n","").replace("#[compute]","")
                             .strip_edges() -> inject_defines(defines)
      compute_after_marker   PerspectiveProbeBackend / ProbeSplatStage._compile_compute_shader:
                             expanded.substr(find("#[compute]") + 10).strip_edges()
      compute_replace        ProbeWorklistOrder.initialize: expanded.replace("#[compute]", "")
      compute_raw_replace    NativeSourceCapture: raw.replace("#[compute]", "")
      compute_raw_first_line WaterFlowFieldProducer: raw, minus its first line when it begins "#["
      graphics_compositor    ASCIICompositorEffect._compile_graphics_shader: split at the
                             markers, strip_edges, inject_defines into both stages
      graphics_probe_capture ProbeCaptureStage.compile_graphics_shader (_probe_capture_stages)
      graphics_true_ff_gbuffer TrueFFSceneGBuffer._compile: split at find("#[fragment]");
                             vertex = before.replace("#[vertex]","").strip_edges(),
                             fragment = after.replace("#[fragment]","").strip_edges()
    """
    out: list[tuple[str, dict[str, str]]] = []
    factory = expanded.replace("#[compute]\n", "").replace("#[compute]", "")
    out.append(("compute_factory", {"compute": _inject_defines(_godot_strip(factory), defines)}))
    if not defines:
        marker_at = expanded.find("#[compute]")
        if marker_at >= 0:
            out.append(("compute_after_marker", {"compute": _godot_strip(expanded[marker_at + len("#[compute]"):])}))
        out.append(("compute_replace", {"compute": expanded.replace("#[compute]", "")}))
        if raw:
            out.append(("compute_raw_replace", {"compute": raw.replace("#[compute]", "")}))
            out.append(("compute_raw_first_line",
                        {"compute": raw[raw.find("\n") + 1:] if raw.startswith("#[") else raw}))
    vertex_pos, fragment_pos = expanded.find("#[vertex]"), expanded.find("#[fragment]")
    if vertex_pos >= 0 and fragment_pos >= 0 and fragment_pos > vertex_pos:
        vertex = _godot_strip(expanded[vertex_pos + len("#[vertex]"):fragment_pos])
        fragment = _godot_strip(expanded[fragment_pos + len("#[fragment]"):])
        out.append(("graphics_compositor", {"vertex": _inject_defines(vertex, defines),
                                            "fragment": _inject_defines(fragment, defines)}))
    probe = _probe_capture_stages(expanded, defines)
    if probe is not None:
        out.append(("graphics_probe_capture", probe))
    if not defines and fragment_pos >= 0:
        out.append(("graphics_true_ff_gbuffer", {
            "vertex": _godot_strip(expanded[:fragment_pos].replace("#[vertex]", "")),
            "fragment": _godot_strip(expanded[fragment_pos:].replace("#[fragment]", ""))}))
    return out


def _variant_digest(stage_texts: dict[str, str]) -> tuple[dict[str, str], str]:
    """RenderShaderSource.record_compiled_variant digests of stage texts:
    stage sha256 = String.sha256_text() (UTF-8 bytes); variant sha256 =
    sha256_text of "<stage>:<sha>\\n" for stage names in Array.sort() order."""
    stages = {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in stage_texts.items()}
    combined = "".join(f"{name}:{stages[name]}\n" for name in sorted(stages))
    return stages, hashlib.sha256(combined.encode("utf-8")).hexdigest()


def _derivable_digests(path: str, defines: list[str], files: dict[str, bytes | None]) -> dict[str, str]:
    """{variant sha256: rule} derivable from one snapshot's bytes."""
    expanded = _expand_shader_source(path, files)
    data = files.get(_res_to_repo_path(path)) if path.startswith("res://") else None
    raw = _decode_godot_text(data) if data else ""
    if not expanded and not raw:
        return {}
    return {_variant_digest(texts)[1]: rule for rule, texts in _variant_stage_candidates(expanded, defines, raw)}


def _verify_compiled_variants(variants: dict, snapshot_manifest: Path,
                              other_snapshots: list[Path] | None = None) -> dict:
    """Re-derive every variant of the FINAL registry from preserved snapshot bytes.

    The final registry (INTERFACE: receipt "compiled_variants", manifest
    "compiled_variants_final") holds late-compiled variants and, per variant,
    superseded_sha256: digests the same key compiled earlier with different
    text. The current digest must re-derive from the run's start snapshot;
    every superseded digest must re-derive from a preserved snapshot of this
    run (start or end), or the executed source changed mid-run to bytes no
    snapshot holds, which fails the run.
    """
    rows = []
    snapshots = [snapshot_manifest] + list(other_snapshots or [])
    per_snapshot = [_snapshot_shader_files(s, [str(v.get("path", "")) for v in variants.values()])
                    for s in snapshots]
    files = per_snapshot[0]
    for key in sorted(variants):
        variant = variants[key] or {}
        path = str(variant.get("path", ""))
        defines = [str(d) for d in variant.get("defines", [])]
        stages = variant.get("stages") or {}
        superseded = [str(s) for s in variant.get("superseded_sha256", []) or []]
        row = {"key": key, "path": path, "defines": defines, "sha256": variant.get("sha256", ""),
               "stages": stages, "compile_count": variant.get("compile_count"),
               "first_compile_frame": variant.get("first_compile_frame"),
               "last_compile_frame": variant.get("last_compile_frame"),
               "superseded_sha256": superseded}
        start_digests = _derivable_digests(path, defines, files)
        any_digests: dict[str, str] = {}
        for snapshot_files in per_snapshot:
            any_digests.update(_derivable_digests(path, defines, snapshot_files))
        row["superseded_unreproducible"] = [s for s in superseded if s not in any_digests]
        row["source_changed_mid_run"] = bool(superseded)
        expanded = _expand_shader_source(path, files)
        involved: set[str] = set()
        stack = [path] if path.startswith("res://") else []
        while stack:
            repo_path = _res_to_repo_path(stack.pop())
            if repo_path in involved:
                continue
            involved.add(repo_path)
            if files.get(repo_path):
                stack.extend(_shader_include_targets(_decode_godot_text(files[repo_path])))
        involved = sorted(involved)
        row["files"] = [{"path": p, "snapshot_sha256": hashlib.sha256(files[p]).hexdigest()
                         if files.get(p) is not None else None} for p in involved]
        raw_bytes = files.get(_res_to_repo_path(path)) if path.startswith("res://") else None
        applicable = sorted({",".join(sorted(texts)) for _rule, texts in _variant_stage_candidates(
            expanded, defines, _decode_godot_text(raw_bytes) if raw_bytes else "")}) if (expanded or raw_bytes) else []
        if not expanded and not raw_bytes:
            row.update(reproduced=False, reason="source_missing_from_snapshot")
        elif variant.get("sha256") in start_digests:
            row.update(reproduced=True, rule=start_digests[variant["sha256"]], reason="")
        elif ",".join(sorted(stages)) not in applicable:
            # No site rule builds this stage set from this file (unknown stage
            # names, missing markers, ambiguous specialization): unverified.
            row.update(reproduced=None, reason="no_site_rule_for_stages:" + ",".join(sorted(stages)))
        else:
            # A site rule applies and no rule reproduces the digest: the
            # executed text is not what the snapshot bytes build.
            row.update(reproduced=False, reason="compiled_text_differs_from_snapshot")
        if row["superseded_unreproducible"]:
            row["reason"] = (row["reason"] + ";" if row["reason"] else "") + "superseded_digest_not_reproducible"
        rows.append(row)
    mismatched = [r["key"] for r in rows if r["reproduced"] is False]
    unmodelled = [r["key"] for r in rows if r["reproduced"] is None]
    superseded_bad = [r["key"] for r in rows if r["superseded_unreproducible"]]
    status = "mismatch" if mismatched or superseded_bad else "unverified" if unmodelled else "verified"
    return {"status": status, "variants": rows, "mismatched": mismatched, "unmodelled": unmodelled,
            "superseded_unreproducible": superseded_bad,
            "changed_mid_run": [r["key"] for r in rows if r["source_changed_mid_run"]]}


def _unrecorded_shader_compile_sites(godot_dir: Path) -> list[str]:
    """RenderingDevice compile calls whose function never records the variant
    (RenderShaderSource.record_compiled_variant). These are GDScript gaps: the
    shader they compile is absent from compiled_shader_variants."""
    sites: list[str] = []
    func_re = re.compile(r"^(static\s+)?func\s")
    for path in sorted(godot_dir.rglob("*.gd")):
        parts = set(path.relative_to(godot_dir).parts)
        if parts & {".godot", "tests", "tools", "addons"}:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        starts = [i for i, line in enumerate(lines) if func_re.match(line)]
        for index, line in enumerate(lines):
            if "shader_compile_spirv_from_source(" not in line or line.lstrip().startswith("#"):
                continue
            begin = max([s for s in starts if s <= index], default=0)
            end = min([s for s in starts if s > index], default=len(lines))
            if not any("record_compiled_variant(" in body for body in lines[begin:end]):
                try:
                    label = path.resolve().relative_to(ROOT.resolve()).as_posix()
                except ValueError:
                    label = str(path)
                sites.append(f"{label}:{index + 1}")
    return sites


VENDORED_SHADER_ROOTS = ("canxkoz_gpu_port",)
_GAME_REFERENCE_SUFFIXES = {".gd", ".tscn", ".tres", ".godot", ".gdshader", ".gdshaderinc", ".glsl", ".glslinc"}


def _vendored_root_referenced(root: str, godot_dir: Path) -> bool:
    """True when any first-party game file outside the vendored root can load
    it: a res:// path into the root (preload, load, ext_resource, include).
    A bare mention (RenderShaderSource.UNRECORDED_COMPILE_SITES) loads nothing."""
    loader = re.compile(
        r'(?:\bpreload\s*\(|\bload\s*\(|ResourceLoader\.load\s*\(|\bpath\s*=|#include\s*)\s*"res://'
        + re.escape(root) + r'/')
    for path in godot_dir.rglob("*"):
        if path.suffix not in _GAME_REFERENCE_SUFFIXES or not path.is_file():
            continue
        parts = path.relative_to(godot_dir).parts
        if parts[0] in {root, ".godot", "artifacts"}:
            continue
        try:
            if loader.search(path.read_text(encoding="utf-8", errors="replace")):
                return True
        except OSError:
            continue
    return False


def _unreachable_vendored_site(site: str, godot_dir: Path) -> bool:
    """A 'path:line' compile site under a vendored root that the game never loads."""
    rel = site.rsplit(":", 1)[0]
    for root in VENDORED_SHADER_ROOTS:
        if f"/{root}/" in f"/{rel}" and not _vendored_root_referenced(root, godot_dir):
            return True
    return False


RECORDER_TOO_OLD = "recorder_too_old"
# The run never wrote its final Godot movement receipt (timeout, crash, launch
# failure). Distinct from RECORDER_TOO_OLD: absence is not an old recorder.
FINAL_RECEIPT_MISSING = "final_receipt_missing"


def _recording_ineligibility(bundle: Path) -> tuple[list[str], dict]:
    """Why an F9 recording is not usable capture evidence ([] = eligible).

    Every reason is named; none is silently tolerated (FL-4873 review P5):
      recording_completion_missing        no completion.json
      recording_unfinished:<status>       completion status is not "complete"
      recording_dropped_frames:<n>        the recorder dropped engine frames
      recording_missing_png:<n>           listed frames without a PNG on disk
      recorder_too_old:<field>            manifest.json lacks the final variant
                                          registry or per-frame variant_revision
      variant_revision_inconsistent       a frame revision exceeds the final one
    """
    reasons: list[str] = []
    facts: dict = {}
    try:
        completion = json.loads((bundle / "completion.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        completion = None
    if completion is None:
        reasons.append("recording_completion_missing")
    else:
        facts["completion_status"] = completion.get("status")
        if completion.get("status") != "complete":
            reasons.append(f"recording_unfinished:{completion.get('status')}")
    try:
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = {}
        reasons.append("recording_manifest_json_missing")
    counts = manifest.get("counts") or {}
    drops = int((completion or {}).get("capture_drops") or counts.get("dropped") or 0)
    facts["dropped"] = drops
    if drops:
        reasons.append(f"recording_dropped_frames:{drops}")
    rows = manifest.get("frames") or []
    missing = [str(r.get("png")) for r in rows if r.get("png") and not (bundle / str(r["png"])).is_file()]
    missing_count = len(missing) + int(counts.get("missing_png_files", 0) or 0)
    facts["missing_png"] = missing_count
    if missing_count:
        reasons.append(f"recording_missing_png:{missing_count}")
    if manifest:
        if "compiled_variants_final" not in manifest:
            reasons.append(f"{RECORDER_TOO_OLD}:compiled_variants_final_missing")
        if "compiled_variant_registry_revision_final" not in manifest:
            reasons.append(f"{RECORDER_TOO_OLD}:compiled_variant_registry_revision_final_missing")
        if rows and any("variant_revision" not in r for r in rows):
            reasons.append(f"{RECORDER_TOO_OLD}:frame_variant_revision_missing")
        final_revision = manifest.get("compiled_variant_registry_revision_final")
        if isinstance(final_revision, int) and any(
                isinstance(r.get("variant_revision"), int) and r["variant_revision"] > final_revision for r in rows):
            reasons.append("variant_revision_inconsistent")
        facts["variant_revision_final"] = final_revision
    return reasons, facts


def _final_registry_owner(receipt: dict, recorded: list[Path]) -> dict:
    """Which document owns this run's final compiled-variant registry, and
    why it is too old when it lacks the registry fields.

    Movement run: receipt.json ("compiled_variants" +
    "compiled_variant_registry_revision"), written by the Godot driver.
    Replay run (receipt schema f9_replay.RECEIPT_SCHEMA): receipt.json is the
    replay receipt; it carries the registry only when the driver also wrote a
    runtime receipt (f9_replay.post_run copies "compiled_variants" into it).
    Otherwise the registry owner is the replay recording's manifest.json
    ("compiled_variants_final" + "compiled_variant_registry_revision_final").
    recorder_too_old fires only when that owner lacks the fields.
    """
    if receipt.get("schema") != f9_replay.RECEIPT_SCHEMA or "compiled_variants" in receipt:
        ineligible = [] if "compiled_variant_registry_revision" in receipt else [
            f"{RECORDER_TOO_OLD}:receipt_registry_revision_missing"]
        return {"kind": "receipt", "documents": ["receipt.json"], "ineligible": ineligible}
    manifests: list[str] = []
    lacking: list[str] = []
    for bundle in recorded:
        path = bundle / "manifest.json"
        manifests.append(str(path))
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
        if not isinstance(manifest, dict) or "compiled_variants_final" not in manifest \
                or "compiled_variant_registry_revision_final" not in manifest:
            lacking.append(str(path))
    if not manifests:
        ineligible = ["replay_recording_missing"]
    elif lacking:
        ineligible = [f"{RECORDER_TOO_OLD}:replay_recording_registry_revision_missing"]
    else:
        ineligible = []
    return {"kind": "replay_recording_manifest", "documents": manifests, "lacking_fields": lacking,
            "ineligible": ineligible}


def _materialized_source_identity(materialized: dict) -> dict:
    """_runtime_source_identity() shape for a baseline replay on a materialized
    snapshot (outside the repository, so the git-scoped reader cannot run)."""
    import launch_fl4359_manual_play as manual_launcher

    snapshot = Path(materialized["snapshot"])
    try:
        check = manual_launcher.verify_materialized_tree(snapshot, Path(materialized["root"]))
    except manual_launcher.SourceSnapshotError as error:
        return {"valid": False, "reason": f"materialized_tree_unverifiable: {error}"}
    return {"valid": check["status"] == "verified", "reason": "" if check["status"] == "verified"
            else "materialized_tree_mismatch", "execution_kind": "materialized_snapshot",
            "source_root": str(materialized["root"]), "source_commit": materialized.get("head", ""),
            "effective_tree_fingerprint": materialized["digest"], "fingerprint_schema": "materialized-snapshot-v2",
            "snapshot": str(snapshot), "verification": check, "exported_build_identity": "UNKNOWN"}


def _register_replay_input(provenance: "RunSourceProvenance", replay_plan: dict) -> None:
    """Hand the replay's immutable inputs to end-of-run preservation.

    The selection is the ARCHIVED copy f9_replay.plan wrote from the bytes it
    used (with its sha256), never the live session.json the viewer may have
    rewritten since. A materialized snapshot becomes the executed tree.
    """
    bundle = Path(replay_plan["bundle"])
    provenance.input_bundles.append(bundle)
    sys.path.insert(0, str(ROOT / "scripts" / "recording"))
    import f9_session  # noqa: E402

    archived = f9_session.archived_selection_path(Path(replay_plan["selection_archive"]))
    provenance.input_selections[str(bundle)] = {
        "archive": str(archived) if archived else "", "sha256": replay_plan["selection_sha256"],
        "path": replay_plan["selection_path"]}
    if replay_plan.get("materialized_source"):
        provenance.materialized = dict(replay_plan["materialized_source"])


class RunSourceProvenance:
    """Start/end runtime-source snapshots, executed-source checks and the
    capture-eligibility verdict for one run.

    start() runs before any process launches; finalize() is a
    DeferredRunReceiptPreserver finalizer, so the end snapshot, the checks and
    the F9 bundle preservation happen after Godot exits and before the receipt
    leaves .run. Nothing here raises; everything is recorded.

      failures    evidence integrity broke: a snapshot not preserved, source
                  drift, an executed file or compiled variant (current or
                  superseded) not reproducible, a bundle not preserved
      ineligible  the run's capture cannot support a claim: the final Godot
                  movement receipt was never written (final_receipt_missing:
                  timeout / exit code), a receipt without the final variant
                  registry (recorder too old), unverified variants, unrecorded
                  compile sites, unfinished/dropped/missing-PNG recordings
    verdict: "failed" | "ineligible" | "eligible"; only "eligible" may exit 0.
    """

    def __init__(self, *, snapshot_root: Path | None = None, fl_id: str = "FL-4359",
                 finalize_wait_seconds: float = F9_BUNDLE_FINALIZE_WAIT_SECONDS) -> None:
        import launch_fl4359_manual_play as manual_launcher

        self.launcher = manual_launcher
        self.snapshot_root = snapshot_root or (
            ROOT / "artifacts" / "run_receipts" / manual_launcher.SOURCE_SNAPSHOT_DIRNAME)
        self.fl_id = fl_id
        self.finalize_wait_seconds = finalize_wait_seconds
        self.pathspecs: tuple[str, ...] = tuple(manual_launcher.RUNTIME_SOURCE_PATHSPECS)
        self.started = False
        self.start_state: dict = {}
        self.start_executed_files: dict[str, str] = {}
        self.start_executed_status = "not_captured"
        self.run_identity: dict = {}
        self.godot_log: Path | None = None
        self.input_bundles: list[Path] = []
        # str(bundle) -> {"archive", "sha256", "path"} of the selection a replay used.
        self.input_selections: dict[str, dict] = {}
        # A baseline replay on a materialized snapshot (f9_replay --replay-from-snapshot).
        self.materialized: dict | None = None
        # The --path directory Godot executes (FL-4873 defect 1); set by start().
        self.godot_project_dir: Path | None = None
        # How the Godot process ended, set by the runner after it exits:
        # {"exit_code": int, "reason": "timeout"|"launch_failed"|"exit_code"}.
        # None: the runner never reached or never returned from the launch.
        self.godot_outcome: dict | None = None
        self.record: dict = {}
        self.failures: list[str] = []
        self.ineligible: list[str] = []

    @property
    def ok(self) -> bool:
        return self.started and not self.failures

    @property
    def verdict(self) -> str:
        if not self.ok:
            return "failed"
        return "ineligible" if self.ineligible else "eligible"

    def _godot_dir(self) -> Path:
        if self.godot_project_dir is not None:
            return self.godot_project_dir
        if self.materialized:
            return Path(self.materialized["root"]) / "godot_project"
        return GODOT_PROJECT

    def note_godot_exit(self, godot_rc: int) -> None:
        """Record how Godot ended so a missing receipt names its cause."""
        reason = {124: "timeout", 125: "launch_failed_or_exit_status_unknown"}.get(godot_rc, "exit_code")
        self.godot_outcome = {"exit_code": int(godot_rc), "reason": reason}

    def _final_receipt_missing_reason(self) -> str:
        outcome = self.godot_outcome
        if outcome is None:
            detail = "godot_outcome_unknown"
        elif outcome["reason"] == "exit_code":
            detail = f"exit_code_{outcome['exit_code']}"
        else:
            detail = str(outcome["reason"])
        return detail

    def start(self, *, receipt_path: Path, pathspecs: tuple[str, ...], godot_log: Path,
              godot_project_dir: Path | None = None) -> bool:
        self.started = True
        self.godot_project_dir = godot_project_dir
        self.pathspecs = tuple(pathspecs)
        self.godot_log = godot_log
        sys.path.insert(0, str(ROOT / "scripts" / "recording"))
        import f9_source_identity  # noqa: E402

        if self.materialized:
            snapshot = Path(self.materialized["snapshot"])
            identity = _materialized_source_identity(self.materialized)
            if identity.get("valid") is not True:
                self.start_state = {"available": False, "reason": identity.get("reason")}
                self.failures.append("start_snapshot_not_preserved")
                return False
            manifest = json.loads(snapshot.read_text(encoding="utf-8"))
            self.pathspecs = tuple(manifest.get("pathspecs") or ())
            self.start_state = {"available": True, "kind": "materialized_snapshot",
                                "head": manifest.get("head", ""), "dirty": True,
                                "sha256": self.materialized["digest"],
                                "snapshot": self.launcher._repo_relative(snapshot),
                                "materialized_root": self.materialized["root"]}
            self.start_executed_files, self.start_executed_status = f9_source_identity.executed_source_files(
                Path(self.materialized["root"]))
        else:
            try:
                self.start_state = self.launcher._runtime_source_state(self.snapshot_root, self.pathspecs)
            except (OSError, self.launcher.SourceSnapshotError) as error:
                self.start_state = {"available": False, "reason": str(error)}
                self.failures.append("start_snapshot_not_preserved")
                return False
            self.start_executed_files, self.start_executed_status = f9_source_identity.executed_source_files()
        self.run_identity = dict(
            self.launcher._run_identity(self.start_state, receipt_path),
            owner="scripts/run_fl4359_headed_movement.py",
            runtime_source_pathspecs=list(self.pathspecs),
        )
        return True

    def _end_state(self) -> dict:
        if self.materialized:
            identity = _materialized_source_identity(self.materialized)
            return {"available": True, "kind": "materialized_snapshot",
                    "sha256": self.materialized["digest"] if identity.get("valid") else "materialized_tree_changed",
                    "snapshot": self.start_state.get("snapshot"), "verification": identity.get("verification")}
        return self.launcher._runtime_source_state(self.snapshot_root, self.pathspecs)

    def _check_executed_files(self, snapshot_manifest: Path) -> dict:
        if self.start_executed_status != "captured":
            return {"status": "not_captured", "reason": self.start_executed_status}
        unreadable = sorted(p for p, sha in self.start_executed_files.items() if sha == "unreadable")
        paths = sorted(p for p in self.start_executed_files if p not in unreadable)
        rebuilt = self.launcher.snapshot_file_bytes(snapshot_manifest, paths)
        mismatched = sorted(
            p for p in paths
            if rebuilt.get(p) is None or hashlib.sha256(rebuilt[p]).hexdigest() != self.start_executed_files[p]
        ) + unreadable
        return {"status": "verified" if not mismatched else "mismatch",
                "scope": "f9_source_identity.TRUE_FF_SCOPE", "files_checked": len(paths),
                "files": [{"path": p, "sha256": self.start_executed_files[p]} for p in sorted(self.start_executed_files)],
                "mismatched": mismatched}

    def _final_registry(self, receipt: dict, bundles: list[Path]) -> tuple[dict, list[str], dict]:
        """Merge the FINAL variant registries of this run (INTERFACE fields).

        receipt.json "compiled_variants" (+ "compiled_variant_registry_revision")
        and every recorded manifest.json "compiled_variants_final"
        (+ "compiled_variant_registry_revision_final"). The first-frame
        registry (settings.first_frame_view_state) is NOT used: it misses
        late-compiled variants. A key recorded with different digests is a
        conflict unless one registry's superseded list holds the other's digest.
        """
        variants: dict = {}
        sources: list[str] = []
        revisions: dict = {}
        docs: list[tuple[str, object, object]] = [
            ("receipt.json", receipt.get("compiled_variants"), receipt.get("compiled_variant_registry_revision"))]
        for bundle in bundles:
            try:
                manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            docs.append((str(bundle / "manifest.json"), manifest.get("compiled_variants_final"),
                         manifest.get("compiled_variant_registry_revision_final")))
        for source, registry, revision in docs:
            if not isinstance(registry, dict):
                continue
            sources.append(source)
            revisions[source] = revision
            for key, value in registry.items():
                current = variants.get(key)
                if current is None:
                    variants[key] = value
                    continue
                a, b = str(current.get("sha256")), str(value.get("sha256"))
                if a == b:
                    continue
                if a in (value.get("superseded_sha256") or []):
                    variants[key] = value  # the later registry recompiled it
                elif b not in (current.get("superseded_sha256") or []):
                    self.failures.append("compiled_variant_conflict")
        return variants, sources, revisions

    def _preserve_bundles(self, bundles: list[tuple[Path, str]]) -> list[dict]:
        sys.path.insert(0, str(ROOT / "scripts" / "recording"))
        import f9_session  # noqa: E402

        rows = []
        for bundle, role in bundles:
            row: dict = {"bundle": str(bundle), "role": role}
            if role == "recorded":
                row["completion_status"] = _wait_for_f9_finalize(bundle, self.finalize_wait_seconds)
                reasons, facts = _recording_ineligibility(bundle)
                row["ineligible_reasons"] = reasons
                row["capture_facts"] = facts
                self.ineligible += [f"{bundle.name}:{r}" for r in reasons]
            selection_input = None
            handoff = self.input_selections.get(str(bundle))
            try:
                if handoff:
                    # The bytes the replay used, from its immutable archive.
                    data = Path(handoff["archive"]).read_bytes()
                    if hashlib.sha256(data).hexdigest() != handoff["sha256"]:
                        raise RuntimeError(f"archived selection {handoff['archive']} no longer has "
                                           f"sha256 {handoff['sha256']}")
                    selection_input = f9_session.selection_from_bytes(bundle, Path(handoff["path"]), data)
                    row["selection_sha256_used"] = handoff["sha256"]
                manifest = f9_session.preserve_bundle(
                    bundle, repo_root=ROOT, fl_id=self.fl_id, runner="scripts/run_fl4359_headed_movement.py",
                    selection_input=selection_input)
                row["durable_manifest"] = self.launcher._repo_relative(manifest)
                doc = json.loads(manifest.read_text(encoding="utf-8"))
                row["copied_files"] = len(doc["files"])
                row["referenced_not_copied"] = len(doc["referenced_not_copied"])
                row["selection_status"] = doc["metadata"]["selection"].get("status")
                row["selection_sha256_archived"] = f9_session.archived_selection_sha256(manifest)
                if handoff and row["selection_sha256_archived"] != handoff["sha256"]:
                    raise RuntimeError("end-of-run archive holds a different selection than the replay used")
            except (OSError, RuntimeError, ValueError) as error:
                row["error"] = str(error)
                self.failures.append("f9_bundle_not_preserved")
            rows.append(row)
        return rows

    def finalize(self, preserver) -> None:
        if not self.started or preserver.receipt_path is None:
            return
        receipt_path = Path(preserver.receipt_path)
        out_dir = receipt_path.parent
        record: dict = {"schema": RUN_SOURCE_PROVENANCE_SCHEMA, "runner": "scripts/run_fl4359_headed_movement.py",
                        "pathspecs": list(self.pathspecs), "start": self.start_state,
                        "run_identity": self.run_identity}
        start_ok = self.start_state.get("available") is True
        end_snapshots: list[Path] = []
        if start_ok:
            try:
                end_state = self._end_state()
            except (OSError, self.launcher.SourceSnapshotError) as error:
                end_state = {"available": False, "reason": str(error)}
                self.failures.append("end_snapshot_not_preserved")
            record["end"] = end_state
            record["source_state_stable"] = end_state.get("sha256") == self.start_state.get("sha256")
            if end_state.get("available") is True and not record["source_state_stable"]:
                self.failures.append("source_drift_between_start_and_end")
            if end_state.get("available") is True and end_state.get("snapshot") and not self.materialized:
                end_snapshots.append(ROOT / str(end_state["snapshot"]))
        receipt: dict = {}
        receipt_loaded = False
        receipt_exists = receipt_path.is_file()
        if start_ok and receipt_exists:
            try:
                loaded = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                loaded = None
            if isinstance(loaded, dict):
                receipt, receipt_loaded = loaded, True
        # FL-4873 defect 4: a run that never wrote its final Godot movement
        # receipt did not complete. That is not evidence of an old recorder;
        # recorder_too_old is reserved for a receipt that exists without the
        # registry fields. Name exactly which artifacts exist.
        final_receipt_missing = start_ok and not receipt_loaded
        record["godot_run"] = dict(self.godot_outcome) if self.godot_outcome is not None else {
            "exit_code": None, "reason": "godot_outcome_unknown"}
        record["artifacts"] = {
            "run_source_provenance": {"path": RUN_SOURCE_PROVENANCE_NAME, "exists": True},
            "godot_movement_receipt": {
                "path": receipt_path.name, "exists": receipt_exists, "parsed": receipt_loaded,
                "status": "present" if receipt_loaded else (
                    "unreadable" if receipt_exists else "missing")},
            "godot_log": {"path": self.godot_log.name if self.godot_log is not None else None,
                          "exists": bool(self.godot_log is not None and self.godot_log.is_file())},
        }
        bundles: list[tuple[Path, str]] = [(b, "replay_input") for b in self.input_bundles]
        if self.godot_log is not None:
            bundles += [(b, "recorded") for b in _f9_bundles_from_log(self.godot_log) if b.is_dir()]
        recorded = [b for b, role in bundles if role == "recorded"]
        if final_receipt_missing:
            detail = "receipt_unreadable" if receipt_exists else self._final_receipt_missing_reason()
            record["final_receipt_missing"] = {
                "reason": detail, "godot_run": record["godot_run"],
                "meaning": "the run did not complete: the Godot movement receipt that carries the final "
                           "variant registry was never written; this is not a recorder_too_old verdict"}
            self.ineligible.append(f"{FINAL_RECEIPT_MISSING}:{detail}")
        sites = _unrecorded_shader_compile_sites(self._godot_dir())
        runtime_sites = _find_key(receipt, "unrecorded_compile_sites")
        if isinstance(runtime_sites, list):
            # RenderShaderSource.unrecorded_compile_sites(): [{path: res://..., line, reason}].
            sites = sorted(set(sites) | {
                f"{_res_to_repo_path(str(s['path'])) if str(s.get('path', '')).startswith('res://') else s.get('path')}"
                f":{s.get('line')}" if isinstance(s, dict) else str(s) for s in runtime_sites})
        # A vendored compile site can only disqualify a run it can execute in.
        # It is exempt while no first-party game file references its vendored
        # root (the game never loads it); exempt sites stay reported.
        exempt = [s for s in sites if _unreachable_vendored_site(s, self._godot_dir())]
        sites = [s for s in sites if s not in exempt]
        executed: dict = {"unrecorded_compile_sites": sites,
                          "unreachable_vendored_compile_sites": exempt}
        if sites:
            self.ineligible.append("unrecorded_compile_sites")
        if start_ok:
            snapshot_manifest = ROOT / str(self.start_state["snapshot"])
            try:
                executed["true_ff_scope"] = self._check_executed_files(snapshot_manifest)
                if executed["true_ff_scope"]["status"] == "mismatch":
                    self.failures.append("executed_file_not_reproducible_from_snapshot")
                elif executed["true_ff_scope"]["status"] != "verified":
                    self.ineligible.append("executed_files_not_captured")
                variants, sources, revisions = self._final_registry(receipt, recorded)
                if variants:
                    compiled = dict(_verify_compiled_variants(variants, snapshot_manifest, end_snapshots),
                                    sources=sources, registry_revisions=revisions, registry="final")
                    executed["compiled_shader_variants"] = compiled
                    if compiled["mismatched"]:
                        self.failures.append("compiled_variant_not_reproducible_from_snapshot")
                    if compiled["superseded_unreproducible"]:
                        self.failures.append("superseded_variant_not_reproducible:executed_source_changed_mid_run")
                    if compiled["unmodelled"]:
                        self.ineligible.append("compiled_variants_unverified")
                elif final_receipt_missing:
                    executed["compiled_shader_variants"] = {
                        "status": "registry_unavailable_final_receipt_missing", "registry": "final",
                        "reason": "no final variant registry: the Godot movement receipt was not written "
                                  "(run did not complete) and no F9 manifest of this run has "
                                  "compiled_variants_final"}
                else:
                    executed["compiled_shader_variants"] = {
                        "status": "registry_missing", "registry": "final",
                        "reason": "no final variant registry: receipt.json has no compiled_variants and no F9 "
                                  "manifest of this run has compiled_variants_final"}
                    self.ineligible.append(f"{RECORDER_TOO_OLD}:final_variant_registry_missing")
                if receipt_loaded:
                    owner = _final_registry_owner(receipt, recorded)
                    if isinstance(executed.get("compiled_shader_variants"), dict):
                        executed["compiled_shader_variants"]["registry_owner"] = owner
                    self.ineligible += owner["ineligible"]
            except (OSError, self.launcher.SourceSnapshotError) as error:
                executed["error"] = str(error)
                self.failures.append("executed_source_check_failed")
        record["executed_sources"] = executed
        record["f9_bundles"] = self._preserve_bundles(bundles)
        record["failures"] = sorted(set(self.failures))
        record["ineligible_reasons"] = sorted(set(self.ineligible))
        record["ok"] = self.ok
        record["verdict"] = self.verdict
        self.record = record
        encoded = json.dumps(record, indent=2, sort_keys=True) + "\n"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / RUN_SOURCE_PROVENANCE_NAME).write_text(encoded, encoding="utf-8")
        if receipt_loaded:
            receipt["run_source_provenance"] = record
            receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            preserver.add_companions((RUN_SOURCE_PROVENANCE_NAME,))
        else:
            # No receipt from this run (early failure): the provenance record is the receipt.
            preserver.configure(out_dir / RUN_SOURCE_PROVENANCE_NAME)


def _artifact_tree_state(root: Path) -> dict[Path, tuple[int, int, int]]:
    """Snapshot artifact inode/size/mtime before a run, without reading image bytes."""
    state: dict[Path, tuple[int, int, int]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == GODOT_PROOF_PUBLICATION_DIR:
            continue
        stat = path.stat()
        state[relative] = (int(stat.st_ino), int(stat.st_size), int(stat.st_mtime_ns))
    return state


def _launch_recorded_macos_godot(
    cmd: list[str],
    env: dict[str, str],
    godot_log: Path,
    godot_project_dir: Path,
    port: int,
    godot_timeout: float,
) -> tuple[int, dict]:
    """Launch through LaunchServices, resolve the exact PID, focus, wait.

    FL-4873 defect 1: godot_project_dir is the directory the argv's --path
    names (the materialized snapshot on --replay-from-snapshot). PID
    discovery matches that path plus the port, so passing the checkout's
    GODOT_PROJECT here lost every snapshot-replay engine.
    """
    godot_child, launch_receipt = _launch_macos_godot_bundle(
        cmd[1:],
        env,
        godot_log,
        godot_project_dir,
        port,
        False,
    )
    activation = dict(launch_receipt)
    activation["project_path"] = str(godot_project_dir)
    if godot_child is None:
        activation.update(
            {
                "attempted": False,
                "activated": False,
                "reason": str(launch_receipt.get("launch_reason", "launch_failed")),
            }
        )
        return 125, activation
    # FL-4359: terrain startup can outlast the default ten-second
    # window. Keep the exact-PID focus check, allow startup to finish.
    activation.update(_activate_macos_window(godot_child.pid, timeout=45.0))
    print(
        "[fl4359-headed] window activation: "
        f"activated={activation.get('activated')} "
        f"reason={activation.get('reason')}",
        flush=True,
    )
    if not bool(activation.get("activated", False)):
        print("[fl4359-headed] video focus gate FAILED; preserving bounded GPU capture and completion receipt", flush=True)
    # FL-4359: focus failure invalidates the video claim, not the
    # already captured GPU fields. Never abort their export midway.
    godot_rc, keeper = _keep_recorded_window_frontmost(godot_child, godot_timeout)
    activation["focus_keeper"] = keeper
    return godot_rc, activation


# FL-4907 WAVE_C_CAPTURE focus keeper. Run
# .run/fl4359_headed_movement_20261002-204436 activated Godot once at launch
# (window_activation.json: registered_godot_process_is_frontmost) and then
# only waited. Godot enters exclusive fullscreen, a separate macOS Space; when
# another app was activated later, macOS switched Spaces and reported the
# Godot window occluded. Godot 4.7 draws nothing while no window can draw, so
# the proof drew 16 frames and then idled (FL4873_DRAW_WATCHDOG
# can_draw=false focused=false mode=4, 2901 process frames in 20005 ms). The
# keeper polls the frontmost process for the whole run, reactivates the exact
# Godot PID through the same System Events setter as the launch activation,
# and records every loss (time, the app that took focus, reactivation
# result). A loss is never hidden: _video_focus_gate fails on any loss.
FOCUS_KEEPER_POLL_S = 1.0
FOCUS_KEEPER_REACTIVATE_TIMEOUT_S = 3.0
FOCUS_KEEPER_MAX_RECORDED_LOSSES = 50


def _keep_recorded_window_frontmost(godot_child, godot_timeout: float) -> tuple[int, dict]:
    """Wait for the detached engine while keeping its window frontmost."""
    keeper: dict = {"poll_s": FOCUS_KEEPER_POLL_S, "polls": 0, "query_failures": 0,
                    "loss_count": 0, "reactivated": 0, "reactivation_failed": 0,
                    "losses": []}
    started = time.monotonic()
    deadline = started + godot_timeout
    while True:
        rc = godot_child.poll()
        if rc is not None:
            return rc, keeper
        if time.monotonic() >= deadline:
            print("[fl4359-headed] ERROR: Godot timed out", flush=True)
            godot_child.kill()
            return 124, keeper
        frontmost = _frontmost_process_pid()
        keeper["polls"] += 1
        if frontmost is None:
            keeper["query_failures"] += 1
        elif frontmost != godot_child.pid and godot_child.poll() is None:
            keeper["loss_count"] += 1
            loss = {"t_s": round(time.monotonic() - started, 3), "frontmost_pid": frontmost,
                    "frontmost_name": _frontmost_process_name()}
            again = _activate_macos_window(godot_child.pid, timeout=FOCUS_KEEPER_REACTIVATE_TIMEOUT_S)
            loss["reactivated"] = bool(again.get("activated", False))
            loss["reason"] = str(again.get("reason", ""))
            keeper["reactivated" if loss["reactivated"] else "reactivation_failed"] += 1
            if len(keeper["losses"]) < FOCUS_KEEPER_MAX_RECORDED_LOSSES:
                keeper["losses"].append(loss)
            print(
                "[fl4359-headed] focus keeper: Godot lost frontmost to "
                f"{loss['frontmost_name'] or loss['frontmost_pid']} at {loss['t_s']}s; "
                f"reactivated={loss['reactivated']} reason={loss['reason']}",
                flush=True,
            )
        time.sleep(FOCUS_KEEPER_POLL_S)


def _video_focus_gate(activation: dict) -> tuple[bool, str]:
    """Launch activation held for the whole recorded run (FL-4907 WAVE_C_CAPTURE)."""
    if not bool(activation.get("activated", False)):
        return False, str(activation.get("reason", "missing_activation_receipt"))
    keeper = activation.get("focus_keeper")
    if not isinstance(keeper, dict):
        return False, "focus_keeper_receipt_missing"
    losses = int(keeper.get("loss_count", 0) or 0)
    if losses:
        return False, f"focus_lost_during_proof:{losses}"
    return True, str(activation.get("reason", ""))


def _run_direct_godot(
    cmd: list[str],
    env: dict[str, str],
    godot_log: Path,
    godot_timeout: float,
) -> int:
    """Run Godot as a direct child. Timeout: kill, reap, return 124."""
    with godot_log.open("w") as glog:
        process = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=glog, stderr=subprocess.STDOUT)
        try:
            try:
                return process.wait(timeout=godot_timeout)
            except subprocess.TimeoutExpired:
                print("[fl4359-headed] ERROR: Godot timed out", flush=True)
                process.kill()
                process.wait()
                return 124
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def _build_godot_command(
    args: argparse.Namespace,
    *,
    godot_project_dir: Path,
    driver_script: Path,
    out_dir: Path,
    receipt_path: Path,
    capture_path: Path,
    godot_log: Path | None = None,
    effective_plane_circle_frames: int,
    effective_turn_frames: int,
    effective_jitter_capture_frame_limit: int,
    build_head: str = "",
    build_dirty: bool = False,
    build_worktree_diff_sha256: str = "",
) -> list[str]:
    cmd = [GODOT_BIN]
    canonical_airplane_presentation = _uses_canonical_airplane_presentation(args)
    if not canonical_airplane_presentation:
        if args.godot_window_mode == "fullscreen":
            cmd.append("--fullscreen")
        else:
            cmd.append("--windowed")
        cmd.extend([
            "--resolution",
            "%dx%d" % (args.resolution[0], args.resolution[1]),
        ])
    # World.gd is the sole presentation owner for the default canonical airplane
    # arm. The explicit historical R6 profile intentionally leaves this owner
    # unselected so the native fullscreen request reaches the existing route.
    cmd.extend(["--path", str(godot_project_dir)])
    cmd.extend(["--script", str(driver_script)])
    cmd.append("--")
    cmd.extend([
        "--colyseus-client",
        f"--port={args.port}",
        f"--fl4359-headed-out-dir={out_dir}",
        f"--fl4359-headed-receipt={receipt_path}",
        f"--fl4359-headed-log-path={godot_log}" if godot_log is not None else "",
        f"--fl4359-headed-move-frames={args.move_frames}",
        f"--fl4359-headed-settle-frames={args.settle_frames}",
        f"--fl4359-headed-after-frames={args.after_frames}",
        f"--fl4359-headed-camera-mode={args.camera_mode}",
        f"--fl4359-plane-circle-frames={effective_plane_circle_frames}",
        f"--fl4359-plane-circle-rate={args.plane_circle_rate}",
        f"--fl4359-plane-throttle={args.plane_throttle}",
        f"--fl4359-plane-settle-throttle={args.plane_settle_throttle}",
        f"--fl4359-plane-pitch-base={args.plane_pitch_base}",
        f"--fl4359-plane-pitch-amp={args.plane_pitch_amp}",
        f"--fl4359-plane-roll-base={args.plane_roll_base}",
        f"--fl4359-plane-roll-amp={args.plane_roll_amp}",
        f"--fl4359-plane-fire-frames={args.plane_fire_frames}",
        f"--fl4359-turn-frames={effective_turn_frames}",
        f"--fl4359-turn-action={args.turn_action}",
        f"--fl4359-glyph-matrix-cell-px={args.glyph_matrix_cell_px}",
        f"--fl4359-cell-aspect={getattr(args, 'cell_aspect', 'square')}",
        f"--force-weather={getattr(args, 'weather', 'clear')}",
        "--colyseus-skip-room-leave",
    ])
    if not getattr(args, "clean_play_no_capture", False):
        capture_mode = (
            CANONICAL_AIRPLANE_CAPTURE_MODE
            if canonical_airplane_presentation
            else args.godot_window_mode
        )
        capture_resolution = (
            CANONICAL_AIRPLANE_PRESENTATION_SIZE
            if canonical_airplane_presentation
            else args.resolution
        )
        cmd.extend([
            f"--fl4359-request-final-dump={1 if args.request_final_dump else 0}",
            f"--fl4359-jitter-capture-path={capture_path}",
            f"--fl4359-jitter-capture-frame-limit={effective_jitter_capture_frame_limit}",
            f"--fl4359-jitter-capture-world-stride={1 if _effective_analyzer_proof_profile(args) == 'full_grid_temporal_oracle_v1' else max(1, int(args.jitter_capture_world_sample_stride))}",
            f"--fl4359-jitter-capture-screen-stride={1 if _effective_analyzer_proof_profile(args) == 'full_grid_temporal_oracle_v1' else 64}",
            f"--fl4359-jitter-capture-drain-deadline-frames={max(1, int(getattr(args, 'jitter_capture_drain_deadline_frames', 120)))}",
        ])
        if _effective_analyzer_proof_profile(args) == "full_grid_temporal_oracle_v1":
            cmd.append("--fl4359-full-grid-temporal-oracle=1")
            capture_start = (
                0
                if _full_grid_contract_mode(args) == FULL_GRID_CONTRACT_MODE_CAMERA_TURN
                else -1
            )
            cmd.append(f"--fl4359-full-grid-capture-start-frame={capture_start}")
            # The exhaustive oracle must observe actual splat fragments that
            # lost hardware depth, not only the already-resolved MRT winner.
            # This flag allocates and clears the proof-only contender bank;
            # ordinary play keeps the four-word disabled placeholder.
            cmd.append("--fl4359-probe-candidate-diagnostics")
        if not canonical_airplane_presentation:
            cmd.append(f"--fl4359-capture-window-mode={capture_mode}")
            cmd.append(
                f"--fl4359-capture-resolution={capture_resolution[0]}x{capture_resolution[1]}"
            )
    if canonical_airplane_presentation:
        cmd.append(CANONICAL_AIRPLANE_PRESENTATION_ARG)
    if getattr(args, "keep_forever", False):
        # The outer .run budget override must reach World's own final-dump
        # retention owner; otherwise a pinned run can pass preflight and still
        # delete the requested dump at the producer boundary.
        cmd.append("--final-render-dump-keep-forever")
    product_time = getattr(args, "product_time", None)
    if product_time is not None:
        cmd.append(f"--fl4431-product-time={float(product_time):.6f}")
    probe_owner_isolation = getattr(args, "probe_owner_isolation", "")
    if probe_owner_isolation:
        cmd.append(f"--fl4359-probe-owner-isolation={probe_owner_isolation}")
        cmd.append("--fl4431-exact-pose-diagnostic")
    probe_size = int(getattr(args, "probe_size", 0))
    if probe_size > 0:
        cmd.append(f"--fl4359-probe-size={probe_size}")
    cmd = [part for part in cmd if part != ""]
    if args.ghost_replay_segment:
        cmd.append(f"--fl4359-ghost-replay-segment={args.ghost_replay_segment}")
        if args.onset_provenance:
            cmd.append("--fl4359-onset-provenance")
        if args.ghost_replay_validate_only:
            cmd.append("--fl4359-ghost-replay-validate-only")
        else:
            # The replay driver arms only when this explicit contract flag is
            # present. Minimal mode retains the diagnostic planes consumed by the
            # pulse gate, then omits the multi-hundred-megabyte atlas readbacks.
            cmd.append("--fl4359-moving-cell-dump")
            cmd.append("--fl4359-moving-cell-dump-minimal")
            if args.epoch_depth_sign_trace:
                cmd.append("--fl4359-epoch-depth-sign-trace")
    # `_forced_baseline_features` owns the oracle-profile celestial feature; the
    # raw argument is never appended directly.
    forced_baseline_features = _forced_baseline_features(args)
    if forced_baseline_features:
        cmd.append(f"--force-baseline-feature={forced_baseline_features}")
    if getattr(args, "clean_play_no_capture", False):
        # This user argument selects the dogfight driver's measured timing arm.
        # The isolation guard in main rejects every capture/readback argument
        # before either Colyseus or Godot can start.
        cmd.append("--fl4359-clean-play-no-capture")
    if getattr(args, "record_flight_video", False):
        cmd.append("--fl4359-record-f9-test")
    if _stationary_calibration_enabled(args):
        cmd.append("--fl4359-terminal-reprojection-stationary-calibration")
    if build_head:
        cmd.append(f"--fl4359-build-head={build_head}")
    cmd.append(f"--fl4359-build-dirty={'true' if build_dirty else 'false'}")
    if build_worktree_diff_sha256:
        cmd.append(f"--fl4359-build-diff-sha256={build_worktree_diff_sha256}")
    for extra_arg in args.extra_user_arg:
        if extra_arg:
            cmd.append(extra_arg)
    if args.exact_player_position:
        cmd.append(f"--fl4408-exact-player-position={args.exact_player_position}")
    if args.exact_camera_position:
        cmd.append(f"--fl4408-exact-camera-position={args.exact_camera_position}")
    if args.exact_camera_yaw:
        cmd.append(f"--fl4408-exact-camera-yaw={args.exact_camera_yaw}")
    if args.exact_camera_pitch:
        cmd.append(f"--fl4408-exact-camera-pitch={args.exact_camera_pitch}")
    if args.exact_camera_projection:
        cmd.append(f"--fl4408-exact-camera-projection={args.exact_camera_projection}")
    if args.exact_camera_fov:
        cmd.append(f"--fl4408-exact-camera-fov={args.exact_camera_fov}")
    if args.exact_camera_size:
        cmd.append(f"--fl4408-exact-camera-size={args.exact_camera_size}")
    presentation_profile_id = _requested_presentation_profile_id(args)
    if presentation_profile_id:
        # The driver/observer owns the applied receipt. This argument carries
        # the runner's declaration across the existing exact-pose transport;
        # it does not select a renderer mode or alter the camera route.
        cmd.append(f"--fl4359-presentation-profile={presentation_profile_id}")
    if getattr(args, "canvas_provenance_capture", False):
        cmd.append("--fl4359-canvas-provenance-capture=true")
    if getattr(args, "presentation_field_capture", False):
        cmd.append("--fl4359-presentation-field-capture=true")
        cmd.append(
            "--fl4359-presentation-field-capture-budget-bytes="
            f"{int(getattr(args, 'presentation_field_capture_budget_bytes', 0))}"
        )
    # FL-4359 pinned exact-pose sweep: hand the driver the declared origin delta so
    # the captured sequence frame index, not physics, selects the rendered eye.
    if getattr(args, "exact_pose_sweep", False):
        cmd.append("--fl4408-exact-pose-sweep=true")
        cmd.append(
            f"--fl4408-exact-pose-sweep-delta={args.exact_pose_sweep_delta}"
        )
        # Explicit pose list: forwarded verbatim (not re-serialized) so the
        # driver's per-frame pose is the same literal CLI identity the preflight
        # published and the receipt records.
        exact_pose_sweep_positions_arg = str(
            getattr(args, "exact_pose_sweep_positions", "") or ""
        ).strip()
        if exact_pose_sweep_positions_arg:
            cmd.append(
                "--fl4408-exact-pose-sweep-positions="
                f"{exact_pose_sweep_positions_arg}"
            )
    # FL-4359 frame-PNG route. The driver's own gate refuses this directory under
    # the full-grid oracle; `main` refuses the combination before launch, so the
    # argument is only ever emitted for an off-oracle run.
    if getattr(args, "frame_pngs", False):
        cmd.append(f"--fl4408-frame-png-dir={out_dir / FRAME_PNG_DIR_NAME}")
    # FL-4216/4273 DAY-NIGHT CYCLE: pin the effect's day-cycle time-of-day for a
    # deterministic single-frame capture (0=midnight..0.5=noon..0.75=sunset).
    if args.day_cycle_time is not None and args.day_cycle_time >= 0.0:
        cmd.append(f"--fl4408-day-cycle-time={args.day_cycle_time}")
    if args.single_frame:
        cmd.append("--fl4408-single-frame=true")
        cmd.append(f"--fl4408-single-frame-label={args.single_frame_label}")
    if getattr(args, "fl4216_temporal_capture", False):
        cmd.append("--fl4216-temporal-capture=true")
        cmd.append(
            "--fl4216-temporal-capture-interval-seconds=%.3f"
            % float(args.fl4216_temporal_capture_interval_seconds)
        )
        cmd.append(
            "--fl4216-temporal-capture-count=%d"
            % int(args.fl4216_temporal_capture_count)
        )
    cmd.append(f"--fl4408-capture-side={args.capture_side}")
    # The former compositor JSONL switches are intentionally absent. Their
    # producer was deleted with the probe route, and the external recorder now
    # owns matching temporal evidence.
    return cmd


def _airplane_route_preflight(
    args: argparse.Namespace,
    *,
    effective_plane_circle_frames: int,
    effective_turn_frames: int,
    effective_jitter_capture_frame_limit: int,
    godot_timeout_seconds: float,
) -> dict:
    canonical_airplane_presentation = _uses_canonical_airplane_presentation(args)
    historical_r6_replay = bool(getattr(args, "fl4359_historical_r6_replay", False))
    extra_user_args = [str(value) for value in getattr(args, "extra_user_arg", [])]
    canonical_extra_args = [
        value for value in extra_user_args
        if value.startswith("--fl4431-canonical-presentation")
    ]
    run_classification = getattr(
        args,
        "run_classification",
        RUN_CLASSIFICATION_MOVEMENT_PROOF,
    )
    diagnostic_only = run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
    stationary_calibration = _stationary_calibration_enabled(args)
    continuous_video_diagnostic = bool(getattr(args, "record_flight_video", False)) and not _full_grid_contract_mode(args)
    turn_sign = 1.0 if args.turn_action == "rotate_left" else -1.0
    first_frame_intent = {
        "flight": {
            "throttle": float(args.plane_throttle),
            "pitch": float(args.plane_pitch_base) + float(args.plane_pitch_amp),
            "roll": turn_sign * DRIVER_PLANE_TURN_AXIS if effective_turn_frames > 0 else float(args.plane_roll_base),
            "yaw": turn_sign * DRIVER_PLANE_TURN_AXIS if effective_turn_frames > 0 else 0.0,
        },
        "fire": False,
    }
    if stationary_calibration:
        checks = {
            "airplane_mode": args.camera_mode == "plane_air",
            "perspective_projection": getattr(args, "exact_camera_projection", "") == "perspective",
            "canonical_presentation_selected": canonical_airplane_presentation,
            "fullscreen_requested": canonical_airplane_presentation or args.godot_window_mode == "fullscreen",
            "stationary_calibration_flag": True,
            "full_grid_temporal_oracle_profile": getattr(args, "analyzer_proof_profile", "") == "full_grid_temporal_oracle_v1",
            "exact_five_capture_frames": int(args.jitter_capture_frame_limit) == 5,
            "exhaustive_world_stride": int(args.jitter_capture_world_sample_stride) == 1,
            "no_camera_turn_frames": int(args.turn_frames) == 0,
            "jitter_analysis_enabled": not bool(args.skip_jitter_analysis),
            "fixed_day_cycle_time": _full_grid_day_cycle_time_valid(args),
            "product_time_pin_present": (
                getattr(args, "product_time", None) is not None
                and math.isfinite(float(getattr(args, "product_time", 0.0)))
            ),
            "finite_godot_timeout": math.isfinite(godot_timeout_seconds) and godot_timeout_seconds > 0.0,
            "run_classification_valid": run_classification == RUN_CLASSIFICATION_MOVEMENT_PROOF,
        }
        failed_checks = [name for name, ok in checks.items() if not ok]
        return {
            "ok": not failed_checks,
            "failed_checks": failed_checks,
            "proof": STATIONARY_TERMINAL_REPROJECTION_CALIBRATION,
            "run_classification": run_classification,
            "proof_classification": STATIONARY_TERMINAL_REPROJECTION_CALIBRATION,
            "camera_mode": args.camera_mode,
            "route_identity": SCRIPTED_AIRBORNE_AIRPLANE_ROUTE_IDENTITY,
            "calibration_of_route": SCRIPTED_AIRBORNE_AIRPLANE_ROUTE_IDENTITY,
            "calibration_only": True,
            "manual_run": False,
            "checks": checks,
            "effective_capture_frames": 5,
        }
    checks = {
        "airplane_mode": args.camera_mode in ("plane_air", "plane_air_ortho"),
        "projection_matches_camera_mode": (
            getattr(args, "exact_camera_projection", "")
            == ("perspective" if args.camera_mode == "plane_air" else "ortho")
        ),
        # The default 32px airplane route is locked by World.gd. The explicit
        # historical R6 profile is the only opt-in that selects native fullscreen.
        "canonical_presentation_selected": canonical_airplane_presentation,
        "historical_r6_native_presentation_selected": (
            not canonical_airplane_presentation if historical_r6_replay else True
        ),
        "canonical_presentation_arg_not_duplicated": (
            not canonical_airplane_presentation or not canonical_extra_args
        ),
        "canonical_no_native_fullscreen_owner": (
            not canonical_airplane_presentation
            or not any(value in {"--fullscreen", "--fl4359-capture-window-mode=fullscreen"}
                       for value in extra_user_args)
        ),
        "fullscreen_requested": (
            canonical_airplane_presentation
            or args.godot_window_mode == "fullscreen"
        ),
        "bounded_movement_frames": effective_plane_circle_frames > 0,
        "frame_zero_movement_intent": effective_plane_circle_frames > 0,
        "bounded_turn_frames": effective_turn_frames > 0,
        "frame_zero_camera_turn_intent": effective_turn_frames > 0,
        "first_frame_throttle_nonzero": float(args.plane_throttle) > 0.0,
        "first_frame_roll_nonzero": abs(float(first_frame_intent["flight"]["roll"])) > 0.01,
        "first_frame_yaw_nonzero": abs(float(first_frame_intent["flight"]["yaw"])) > 0.01,
        "jitter_metadata_enabled": not bool(args.skip_jitter_analysis),
        "bounded_jitter_frame_limit": (
            not bool(args.skip_jitter_analysis)
            and effective_jitter_capture_frame_limit > 0
            and effective_jitter_capture_frame_limit <= effective_plane_circle_frames
        ),
        "full_grid_release_and_settle_exact": (
            getattr(args, "analyzer_proof_profile", "")
            != "full_grid_temporal_oracle_v1"
            or int(args.after_frames) == 2
        ),
        "full_grid_total_capture_frames_bounded": (
            getattr(args, "analyzer_proof_profile", "")
            != "full_grid_temporal_oracle_v1"
            or effective_jitter_capture_frame_limit + 2
            <= MAX_FULL_GRID_TEMPORAL_CAPTURE_FRAMES
        ),
        "finite_godot_timeout": math.isfinite(godot_timeout_seconds) and godot_timeout_seconds > 0.0,
        "product_time_pin_present": (
            getattr(args, "product_time", 0.0) is not None
            and math.isfinite(float(getattr(args, "product_time", 0.0)))
        ),
        "fixed_day_cycle_time": _full_grid_day_cycle_time_valid(args),
        "run_classification_valid": run_classification in {
            RUN_CLASSIFICATION_MOVEMENT_PROOF,
            RUN_CLASSIFICATION_DIAGNOSTIC_ONLY,
        },
    }
    fl4671_receipt_diagnostic = bool(getattr(args, "fl4671_lerp_receipt_diagnostic", False))
    if diagnostic_only:
        # Diagnostic authority comes from the explicit classification and the
        # forced non-proof summary fields. It does not require discarding the
        # temporal observations needed to falsify a renderer candidate.
        checks["diagnostic_nonproof_classification"] = (
            run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
        )
    if continuous_video_diagnostic:
        # This arm proves the live temporal viewport. Sparse GPU readback is a
        # separate arm because it starves authority-tick consumption badly
        # enough to invalidate a smooth-motion observation.
        checks["jitter_metadata_enabled"] = bool(args.skip_jitter_analysis)
        checks["bounded_jitter_frame_limit"] = (
            bool(args.skip_jitter_analysis)
            and effective_jitter_capture_frame_limit == 0
        )
        checks["continuous_video_requested"] = True
        # FL-4359 / FL-4333: continuous video must be able to observe the
        # production weather/gait clock. A required time pin freezes both.
        # Static/full-grid proof arms above retain their mandatory pin.
        clock_pinned = checks.pop("product_time_pin_present")
        checks["product_clock_valid"] = args.product_time is None or clock_pinned
    if fl4671_receipt_diagnostic:
        # FL-4671: this diagnostic observes presentation interpolation receipts.
        # It is not an FL-4359 jitter proof, so the retired jitter recorder stays
        # skipped while the mounted airplane route contract remains enforced.
        checks["jitter_metadata_enabled"] = bool(args.skip_jitter_analysis)
        checks["bounded_jitter_frame_limit"] = bool(args.skip_jitter_analysis)
        checks["phase2a_receipt_enabled"] = "--fl4359-phase2a-receipts" in args.extra_user_arg
    failed_checks = [
        name
        for name, ok in checks.items()
        if not ok
        and not (diagnostic_only and name in {
            "jitter_metadata_enabled",
            "bounded_jitter_frame_limit",
        })
        and not (historical_r6_replay and name == "canonical_presentation_selected")
    ]
    return {
        "ok": not failed_checks,
        "failed_checks": failed_checks,
        "proof": (
            "FL-4359 bounded visual capture diagnostic"
            if diagnostic_only
            else (
                "FL-4671 mounted airplane presentation interpolation receipt diagnostic"
                if fl4671_receipt_diagnostic
                else "FL-4359 headed airplane movement metadata preflight"
            )
        ),
        "run_classification": run_classification,
        "proof_classification": (
            DIAGNOSTIC_PROOF_CLASSIFICATION
            if diagnostic_only
            else RUN_CLASSIFICATION_MOVEMENT_PROOF
        ),
        "camera_mode": args.camera_mode,
        "route_identity": SCRIPTED_AIRBORNE_AIRPLANE_ROUTE_IDENTITY,
        "presentation_surface": (
            "canonical_locked"
            if canonical_airplane_presentation
            else "native_exclusive" if args.godot_window_mode == "fullscreen" else "windowed"
        ),
        "presentation_profile": (
            HISTORICAL_R6_PRESENTATION_PROFILE if historical_r6_replay else ""
        ),
        "presentation_size": list(
            CANONICAL_AIRPLANE_PRESENTATION_SIZE
            if canonical_airplane_presentation
            else getattr(args, "resolution", (0, 0))
        ),
        "semantic_ansi_size": list(
            ((225, 71) if getattr(args, "cell_aspect", "square") == "terminal" else CANONICAL_AIRPLANE_ANSI_SIZE)
            if canonical_airplane_presentation
            else (0, 0)
        ),
        "manual_run": False,
        "diagnostic_substitute": diagnostic_only or fl4671_receipt_diagnostic,
        "expected_first_intent_frame": 0,
        "expected_first_turn_intent_frame": 0,
        "effective_plane_circle_frames": effective_plane_circle_frames,
        "effective_turn_frames": effective_turn_frames,
        "effective_jitter_capture_frame_limit": effective_jitter_capture_frame_limit,
        "effective_total_capture_frames": (
            effective_jitter_capture_frame_limit + 2
            if getattr(args, "analyzer_proof_profile", "")
            == "full_grid_temporal_oracle_v1"
            else effective_jitter_capture_frame_limit
        ),
        "jitter_capture_world_sample_stride": (
            1
            if _effective_analyzer_proof_profile(args)
            == "full_grid_temporal_oracle_v1"
            else max(1, int(args.jitter_capture_world_sample_stride))
        ),
        "godot_timeout_seconds": godot_timeout_seconds,
        "first_frame_intent": first_frame_intent,
        "checks": checks,
    }


def _grounded_route_preflight(
    args: argparse.Namespace,
    *,
    effective_turn_frames: int,
    effective_jitter_capture_frame_limit: int,
    godot_timeout_seconds: float,
) -> dict:
    """Validate the physical-key grounded movement proof before launch."""
    run_classification = getattr(
        args,
        "run_classification",
        RUN_CLASSIFICATION_MOVEMENT_PROOF,
    )
    diagnostic_only = run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
    expected_projection = str(args.camera_mode)
    requested_projection = str(getattr(args, "exact_camera_projection", ""))
    move_frames = int(args.move_frames)
    checks = {
        "grounded_camera_mode": args.camera_mode in ("perspective", "ortho"),
        "projection_matches_camera_mode": (
            requested_projection in ("", expected_projection)
        ),
        "fullscreen_requested": args.godot_window_mode == "fullscreen",
        # A reduced window is useful for bounded per-cell diagnostics. It is
        # never a movement proof and requires the explicit diagnostic opt-in.
        "window_policy_valid": args.godot_window_mode == "fullscreen" or (
            diagnostic_only
            and args.godot_window_mode == "windowed"
            and bool(getattr(args, "allow_windowed_diagnostic", False))
        ),
        "bounded_movement_frames": move_frames > 0,
        "frame_zero_movement_intent": move_frames > 0,
        "bounded_turn_frames": effective_turn_frames > 0,
        "frame_zero_camera_turn_intent": effective_turn_frames > 0,
        "post_turn_settle_frames": int(args.settle_frames) > 0,
        "release_and_settle_frames": int(args.after_frames) >= 2,
        "jitter_metadata_enabled": not bool(args.skip_jitter_analysis),
        "bounded_jitter_frame_limit": (
            not bool(args.skip_jitter_analysis)
            and effective_jitter_capture_frame_limit >= 5
            and effective_jitter_capture_frame_limit <= move_frames
        ),
        "finite_godot_timeout": math.isfinite(godot_timeout_seconds) and godot_timeout_seconds > 0.0,
        "product_time_pin_present": (
            getattr(args, "product_time", None) is not None
            and math.isfinite(float(getattr(args, "product_time", 0.0)))
        ),
        "fixed_day_cycle_time": _full_grid_day_cycle_time_valid(args),
        "run_classification_valid": run_classification in {
            RUN_CLASSIFICATION_MOVEMENT_PROOF,
            RUN_CLASSIFICATION_DIAGNOSTIC_ONLY,
        },
    }
    if diagnostic_only:
        checks["diagnostic_nonproof_classification"] = (
            run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
        )
    failed_checks = [
        name
        for name, ok in checks.items()
        if not ok and not (diagnostic_only and name in {
            "jitter_metadata_enabled",
            "bounded_jitter_frame_limit",
        }) and not (diagnostic_only and name == "fullscreen_requested" and checks["window_policy_valid"])
    ]
    return {
        "ok": not failed_checks,
        "failed_checks": failed_checks,
        "proof": (
            "FL-4359 bounded grounded movement diagnostic"
            if diagnostic_only
            else "FL-4359 headed grounded movement metadata preflight"
        ),
        "run_classification": run_classification,
        "proof_classification": (
            DIAGNOSTIC_PROOF_CLASSIFICATION
            if diagnostic_only
            else RUN_CLASSIFICATION_MOVEMENT_PROOF
        ),
        "camera_mode": args.camera_mode,
        "route_identity": SCRIPTED_GROUNDED_ROUTE_IDENTITY,
        "manual_run": False,
        "diagnostic_substitute": diagnostic_only,
        "expected_first_intent_frame": 0,
        "expected_first_turn_intent_frame": 0,
        "effective_move_frames": move_frames,
        "effective_turn_frames": effective_turn_frames,
        "effective_jitter_capture_frame_limit": effective_jitter_capture_frame_limit,
        "jitter_capture_world_sample_stride": max(1, int(args.jitter_capture_world_sample_stride)),
        "godot_timeout_seconds": godot_timeout_seconds,
        "checks": checks,
    }


def _has_exact_pose_arguments(args: argparse.Namespace) -> bool:
    """Return whether this invocation is trying to use the FL-4408 pose seam."""
    if _full_grid_contract_mode(args) == FULL_GRID_CONTRACT_MODE_CAMERA_TURN:
        # R27 pins projection identity but deliberately leaves the live camera
        # under physical turn-input ownership. A projection assertion alone is
        # not an FL-4408 exact-pose request.
        return any(
            (
                args.exact_player_position,
                args.exact_camera_position,
                args.exact_camera_yaw,
                args.exact_camera_pitch,
                args.exact_camera_fov,
                args.exact_camera_size,
            )
        )
    # Airplane proof modes use their scripted airborne camera owner.  A
    # projection assertion is provenance for that route, not an exact-pose
    # request that would incorrectly enter grounded preflight.
    if args.camera_mode in ("plane_air", "plane_air_ortho"):
        return any(
            (
                args.exact_player_position,
                args.exact_camera_position,
                args.exact_camera_yaw,
                args.exact_camera_pitch,
                args.exact_camera_fov,
                args.exact_camera_size,
            )
        )
    return any(
        (
            args.exact_player_position,
            args.exact_camera_position,
            args.exact_camera_yaw,
            args.exact_camera_pitch,
            args.exact_camera_projection,
            args.exact_camera_fov,
            args.exact_camera_size,
        )
    )


def _exact_player_spawn_xz(value: str) -> tuple[str, str] | None:
    """Return the server-owned XZ spawn encoded by one finite exact pose."""
    parts = [part.strip() for part in str(value).split(",")]
    if len(parts) != 3 or any(not part for part in parts):
        return None
    try:
        coordinates = [float(part) for part in parts]
    except ValueError:
        return None
    if not all(math.isfinite(coordinate) for coordinate in coordinates):
        return None
    return parts[0], parts[2]


def _player_spawn_environment(
    args: argparse.Namespace,
    *,
    exact_pose_capture: bool,
) -> dict[str, str]:
    """Select explicit spawn flags first, then the exact-pose server truth."""
    exact_spawn = (
        _exact_player_spawn_xz(args.exact_player_position)
        if exact_pose_capture
        else None
    )
    selected: dict[str, str] = {}
    if args.player_spawn_x:
        selected["PLAYER_SPAWN_X"] = str(args.player_spawn_x)
    elif exact_spawn is not None:
        selected["PLAYER_SPAWN_X"] = exact_spawn[0]
    if args.player_spawn_z:
        selected["PLAYER_SPAWN_Z"] = str(args.player_spawn_z)
    elif exact_spawn is not None:
        selected["PLAYER_SPAWN_Z"] = exact_spawn[1]
    return selected


def _exact_pose_capture_preflight(
    args: argparse.Namespace,
    *,
    godot_timeout_seconds: float,
    temporal_capture: bool = False,
) -> dict:
    """Fail closed on grounded exact-pose captures without airplane duties."""
    run_classification = getattr(
        args,
        "run_classification",
        RUN_CLASSIFICATION_MOVEMENT_PROOF,
    )
    projection_parameter_present = (
        bool(args.exact_camera_fov)
        if args.exact_camera_projection == "perspective"
        else bool(args.exact_camera_size)
    )
    probe_owner_isolation = bool(getattr(args, "probe_owner_isolation", ""))
    presentation_identity_valid = (
        args.godot_window_mode == "windowed"
        and args.resolution == FL4359_OPERATOR_SLAB_REQUESTED_RESOLUTION
        and int(args.glyph_matrix_cell_px) == 20
    ) if probe_owner_isolation else args.godot_window_mode == "fullscreen"
    observation_surface_valid = (
        "--single-frame-cell-dump" not in args.extra_user_arg
    ) if probe_owner_isolation else (
        "--single-frame-cell-dump" in args.extra_user_arg or temporal_capture
    )
    checks = {
        "single_frame_or_temporal_capture": bool(
            getattr(args, "single_frame", False) or temporal_capture
        ),
        "jitter_analysis_retired": bool(args.skip_jitter_analysis),
        "grounded_camera_mode": args.camera_mode in ("perspective", "ortho"),
        "projection_matches_camera_mode": (
            args.exact_camera_projection == args.camera_mode
        ),
        "exact_player_position": (
            _exact_player_spawn_xz(args.exact_player_position) is not None
        ),
        "exact_camera_position": bool(args.exact_camera_position),
        "exact_camera_yaw": bool(args.exact_camera_yaw),
        "exact_camera_pitch": bool(args.exact_camera_pitch),
        "projection_parameter_present": projection_parameter_present,
        "fixed_day_cycle_time": (
            _full_grid_day_cycle_time_valid(args)
            and (
                args.day_cycle_time is None
                or 0.0 <= float(args.day_cycle_time) < 1.0
            )
        ),
        "presentation_identity_valid": presentation_identity_valid,
        "observation_surface_valid": observation_surface_valid,
        "exact_pose_diagnostic_requested": (
            "--fl4431-exact-pose-diagnostic" in args.extra_user_arg
            or probe_owner_isolation
            or temporal_capture
        ),
        "temporal_capture_interval_valid": (
            not temporal_capture
            or _float_value(
                getattr(args, "fl4216_temporal_capture_interval_seconds", 0.0), 0.0
            ) >= MIN_FL4216_TEMPORAL_CAPTURE_INTERVAL_SECONDS
        ),
        "temporal_capture_count_valid": (
            not temporal_capture
            or _int_value(getattr(args, "fl4216_temporal_capture_count", 0), 0)
            >= DEFAULT_FL4216_TEMPORAL_CAPTURE_COUNT
        ),
        "temporal_capture_projection_supported": (
            not temporal_capture or args.camera_mode in ("perspective", "ortho")
        ),
        "temporal_capture_timeout_valid": (
            not temporal_capture
            or godot_timeout_seconds >= MIN_FL4216_TEMPORAL_GODOT_TIMEOUT_SECONDS
        ),
        "finite_godot_timeout": math.isfinite(godot_timeout_seconds) and godot_timeout_seconds > 0.0,
    }
    failed_checks = [name for name, ok in checks.items() if not ok]
    return {
        "ok": not failed_checks,
        "failed_checks": failed_checks,
        "proof": (
            "FL-4216 same-process temporal exact-pose visual/runtime diagnostic"
            if temporal_capture
            else "FL-4359 frozen-pose exact-provenance owner isolation"
            if probe_owner_isolation
            else "FL-4737 grounded exact-pose visual/runtime diagnostic"
        ),
        "run_classification": run_classification,
        "proof_classification": (
            DIAGNOSTIC_PROOF_CLASSIFICATION
            if run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
            else RUN_CLASSIFICATION_MOVEMENT_PROOF
        ),
        "route_identity": (
            "fl4216_same_process_temporal_v1"
            if temporal_capture
            else "fl4359_frozen_probe_owner_isolation_v1"
            if probe_owner_isolation
            else "fl4737_grounded_exact_pose_v1"
        ),
        "temporal_capture": temporal_capture,
        "manual_run": False,
        "diagnostic_substitute": True,
        "camera_mode": args.camera_mode,
        "exact_camera_projection": args.exact_camera_projection,
        "probe_owner_isolation": getattr(args, "probe_owner_isolation", ""),
        # Zero selects the production pipeline default, which FL-4359 promotes
        # to 384 after the controlled same-pose 128/384 visual comparison.
        "probe_size": int(getattr(args, "probe_size", 0) or 384),
        "day_cycle_time": args.day_cycle_time,
        "godot_timeout_seconds": godot_timeout_seconds,
        "checks": checks,
    }


def _exact_pose_sweep_preflight(
    args: argparse.Namespace,
    *,
    effective_jitter_capture_frame_limit: int,
    godot_timeout_seconds: float,
) -> dict:
    """Validate the pinned exact-pose sweep before launch.

    This route deliberately combines exact-pose pinning with the DEFAULT capture
    route, which no existing check-set covers: `_exact_pose_capture_preflight`
    requires a single-frame or FL-4216 temporal capture with analysis skipped, and
    `_grounded_route_preflight` requires physical movement and turn input. The
    checks below are the equal-rigor replacement for BOTH, and they apply only when
    `--exact-pose-sweep` is requested, so no existing route loses a check.
    """
    run_classification = getattr(
        args,
        "run_classification",
        RUN_CLASSIFICATION_MOVEMENT_PROOF,
    )
    diagnostic_only = run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
    sweep_delta = _parse_world_vec3_arg(
        getattr(args, "exact_pose_sweep_delta", "")
    )
    # FL-4359 explicit pose-list route. `sweep_positions_supplied` is the switch:
    # when it is False every position check below is vacuously True and the
    # constant-delta contract is unchanged, so no existing invocation loses or
    # gains a check.
    sweep_positions_arg = str(
        getattr(args, "exact_pose_sweep_positions", "") or ""
    ).strip()
    sweep_positions_supplied = bool(sweep_positions_arg)
    sweep_positions = _parse_world_vec3_list_arg(sweep_positions_arg)
    projection_parameter_present = (
        bool(args.exact_camera_fov)
        if args.exact_camera_projection == "perspective"
        else bool(args.exact_camera_size)
    )
    forced_features = [
        name
        for name in (part.strip() for part in _forced_baseline_features(args).split(","))
        if name
    ]
    day_cycle_time = getattr(args, "day_cycle_time", None)
    # FL-4896: the True FF text route replaces the fixed-cell oracle as this
    # sweep's temporal capture. Its camera path is held to the same
    # exact-translation pose contract (five distinct declared poses, yaw,
    # pitch, no turn); only the capture owner differs.
    true_ff_text_route = _strict_true_ff(args)
    # FL-4512: a True FF sweep whose five declared poses are all equal is a
    # stationary baseline (the instability floor with nothing moving).
    true_ff_contract_mode = (
        FULL_GRID_CONTRACT_MODE_STATIONARY
        if _positions_all_equal(_parse_world_vec3_list_arg(
            str(getattr(args, "exact_pose_sweep_positions", "") or "")) or [])
        else FULL_GRID_CONTRACT_MODE_EXACT_TRANSLATION)
    full_grid_pose_contract = _full_grid_pose_contract(
        argparse.Namespace(**{**vars(args), "full_grid_contract_mode": true_ff_contract_mode})
        if true_ff_text_route else args)
    presentation_profile_id = _requested_presentation_profile_id(args)
    presentation_profile = _presentation_profile_spec(args)
    presentation_profile_atlas_identity = (
        presentation_profile.atlas_identity()
        if presentation_profile is not None
        else None
    )
    presentation_profile_atlas_available = bool(
        isinstance(presentation_profile_atlas_identity, dict)
        and all(
            isinstance(source, dict)
            and isinstance(source.get("sha256"), str)
            and bool(source.get("sha256"))
            for source in presentation_profile_atlas_identity.get("sources", [])
        )
    )
    checks = {
        "exact_pose_sweep_requested": bool(getattr(args, "exact_pose_sweep", False)),
        "airplane_mode": args.camera_mode == "plane_air",
        "projection_matches_camera_mode": (
            args.exact_camera_projection == "perspective"
        ),
        # The capture writer only arms on the DEFAULT driver route, so every route
        # that early-returns before it is rejected here rather than silently
        # producing no jitter_capture.jsonl.
        "default_capture_route_selected": not any(
            (
                bool(getattr(args, "single_frame", False)),
                bool(getattr(args, "fl4216_temporal_capture", False)),
                bool(getattr(args, "ghost_replay_segment", "")),
                _stationary_calibration_enabled(args),
                bool(getattr(args, "clean_play_no_capture", False)),
            )
        ),
        "presentation_identity_valid": args.godot_window_mode == "fullscreen",
        # The identity is separate from the temporal oracle. A missing or
        # unknown profile fails before launch; receipt validation later still
        # requires the runtime to echo actual applied facts.
        "presentation_profile_known": bool(presentation_profile_id and presentation_profile),
        "presentation_profile_atlas_sources_available": presentation_profile_atlas_available,
        "presentation_profile_projection_matches": bool(
            presentation_profile
            and args.exact_camera_projection == presentation_profile.projection
        ),
        "presentation_profile_native_cell_size_matches": bool(
            presentation_profile
            and presentation_profile.native_cell_size
            == _requested_native_cell_size(args)
        ),
        "presentation_profile_application_supported": _profile_application_supported(args, presentation_profile),
        "presentation_field_capture_budget_valid": (
            _presentation_field_capture_budget_valid(args)
        ),
        "exact_camera_position": bool(args.exact_camera_position),
        "exact_camera_yaw": bool(args.exact_camera_yaw),
        "exact_camera_pitch": bool(args.exact_camera_pitch),
        "projection_parameter_present": projection_parameter_present,
        # An explicit pose list OVERRIDES the delta, so the delta is not an input
        # to the rendered choreography on that route and is not required to be a
        # usable advance. Without the list these two collapse to their original
        # expressions exactly.
        "sweep_delta_finite": sweep_delta is not None or sweep_positions_supplied,
        "sweep_delta_nonzero": sweep_positions_supplied or (
            sweep_delta is not None and any(
                component != 0.0 for component in sweep_delta
            )
        ),
        # Explicit pose-list validity. A supplied list that does not parse into
        # finite `x,y,z` triples, or that declares more poses than the run's own
        # movement window can consume, stops the run in preflight rather than
        # replaying an undeclared camera path.
        "sweep_positions_parse_valid": (
            not sweep_positions_supplied or sweep_positions is not None
        ),
        "sweep_positions_within_move_frames": (
            not sweep_positions_supplied
            or (
                sweep_positions is not None
                and 0 < len(sweep_positions) <= int(args.move_frames)
            )
        ),
        # The pinned sweep normally rides the full-grid oracle. The frame-PNG
        # route is the declared alternative: the oracle owns every render
        # callback in its bounded request window, so a per-frame PNG cannot be
        # saved there at all. `frame_pngs` is the only thing that satisfies this
        # check without the oracle, and `main` refuses the two together, so no
        # existing invocation loses or gains a check.
        "full_grid_oracle_profile_selected": (
            _effective_analyzer_proof_profile(args) == FULL_GRID_ORACLE_PROFILE
            or true_ff_text_route
        ),
        "full_grid_contract_mode_selected": (
            full_grid_pose_contract["mode"] in FULL_GRID_CONTRACT_MODES
        ),
        "full_grid_pose_contract": full_grid_pose_contract["pass"],
        "five_active_capture_frames": (
            effective_jitter_capture_frame_limit == FULL_GRID_REQUIRED_CAPTURE_FRAMES
        ),
        "world_stride_one": (
            int(getattr(args, "jitter_capture_world_sample_stride", -1))
            == FULL_GRID_REQUIRED_WORLD_STRIDE
        ),
        "full_cell_capture": True,
        "maximum_glyph_grid": (
            int(getattr(args, "glyph_matrix_cell_px", 0))
            == FULL_GRID_REQUIRED_GLYPH_MATRIX_CELL_PX
        ),
        "turn_frames_zero": int(getattr(args, "turn_frames", -1)) == 0,
        "tilt_shift_off": (
            FULL_GRID_ORACLE_REQUIRED_DISABLED_FEATURE in forced_features
            and not any(
                "tilt-shift" in value or "tilt_shift" in value
                for value in getattr(args, "extra_user_arg", [])
            )
        ),
        "release_and_settle_frames": int(getattr(args, "after_frames", 0)) == 2,
        "bounded_movement_frames": (
            int(args.move_frames) >= effective_jitter_capture_frame_limit
        ),
        "jitter_metadata_enabled": not bool(args.skip_jitter_analysis),
        "large_capture_acknowledged": bool(
            getattr(args, "allow_large_jitter_capture", False)
        ),
        "fixed_day_cycle_time": (
            _full_grid_day_cycle_time_valid(args)
            and day_cycle_time is not None
            and 0.0 <= float(day_cycle_time) < 1.0
        ),
        "day_night_cycle_feature_forced": (
            FULL_GRID_ORACLE_REQUIRED_FEATURE in forced_features
        ),
        "product_time_pin_present": (
            getattr(args, "product_time", None) is not None
            and math.isfinite(float(getattr(args, "product_time", 0.0)))
        ),
        "finite_godot_timeout": (
            math.isfinite(godot_timeout_seconds) and godot_timeout_seconds > 0.0
        ),
        "run_classification_valid": run_classification in {
            RUN_CLASSIFICATION_MOVEMENT_PROOF,
            RUN_CLASSIFICATION_DIAGNOSTIC_ONLY,
        },
    }
    failed_checks = [name for name, ok in checks.items() if not ok]
    return {
        "ok": not failed_checks,
        "failed_checks": failed_checks,
        "proof": (
            "FL-4359 pinned exact-pose sweep temporal-oracle capture "
            "(choreography determinism diagnostic, not a renderer claim)"
        ),
        "run_classification": run_classification,
        "proof_classification": (
            DIAGNOSTIC_PROOF_CLASSIFICATION
            if diagnostic_only
            else RUN_CLASSIFICATION_MOVEMENT_PROOF
        ),
        "route_identity": EXACT_POSE_SWEEP_ROUTE_IDENTITY,
        "camera_mode": args.camera_mode,
        "exact_camera_projection": args.exact_camera_projection,
        "presentation_profile": presentation_profile_id,
        "presentation_profile_spec": (
            presentation_profile.as_dict() if presentation_profile is not None else None
        ),
        "manual_run": False,
        "diagnostic_substitute": True,
        "exact_camera_position": args.exact_camera_position,
        "exact_camera_yaw": args.exact_camera_yaw,
        "exact_camera_pitch": args.exact_camera_pitch,
        "exact_pose_sweep_delta": getattr(args, "exact_pose_sweep_delta", ""),
        "full_grid_contract": full_grid_pose_contract,
        # Which rule produced `exact_pose_sweep_positions`, plus the literal CLI
        # string forwarded to the driver, so a durable receipt can be replayed
        # without re-deriving the choreography.
        "exact_pose_sweep_route": (
            "explicit_position_list" if sweep_positions_supplied else "constant_delta"
        ),
        "exact_pose_sweep_positions_arg": sweep_positions_arg,
        "exact_pose_sweep_positions": _exact_pose_sweep_positions(
            args, effective_jitter_capture_frame_limit
        ),
        "forced_baseline_features": _forced_baseline_features(args),
        # Launch-side arm witness: which FL-4359 default-off opt-ins this argv
        # carries. Empty means the baseline control identity.
        "fl4359_arm_flags": _fl4359_arm_flags(args),
        "day_cycle_time": day_cycle_time,
        "effective_jitter_capture_frame_limit": effective_jitter_capture_frame_limit,
        "godot_timeout_seconds": godot_timeout_seconds,
        "checks": checks,
    }


def _dict_value(value) -> dict:
    return value if isinstance(value, dict) else {}


def _sha256_file(path: Path) -> str:
    """Hash one finalized proof artifact without loading the raw dump twice."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decoded_rgba8_identity(path: Path) -> tuple[str, tuple[int, int]]:
    """Hash the RGBA8 pixels actually uploaded by the Godot texture owner."""
    if not path.is_file():
        return "", (0, 0)
    try:
        from PIL import Image

        with Image.open(path) as image:
            rgba = image.convert("RGBA")
            dimensions = (int(rgba.width), int(rgba.height))
            digest = hashlib.sha256(rgba.tobytes()).hexdigest()
    except (ImportError, OSError, ValueError):
        return "", (0, 0)
    return digest, dimensions


def _write_artifact_manifest(out_dir: Path, paths: list[Path]) -> Path:
    """Preserve auditable identities for disposable and durable run outputs."""
    rows = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        rows.append({
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        })
    manifest_path = out_dir / "artifact_manifest.json"
    manifest_path.write_text(
        json.dumps({"schema": "fl4359.artifact_manifest.v1", "artifacts": rows}, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _publication_observed_policy(receipt: dict) -> dict:
    """Use only a captured compositor policy, never CLI intent, for mode tags."""
    frames = _dict_value(receipt.get("native_frame_capture")).get("frames")
    if not isinstance(frames, list) or not frames:
        return {}
    policies: list[dict] = []
    cell_sizes: set[tuple[int, int]] = set()
    matrix_keys: set[str] = set()
    for frame in frames:
        dispatch = _dict_value(_dict_value(frame).get("dispatch"))
        policy = _dict_value(dispatch.get("policy"))
        if policy.get("status") != "applied" or not isinstance(policy.get("mode"), str):
            return {}
        size = dispatch.get("cell_size")
        if not isinstance(size, list) or len(size) != 2 or not all(
            isinstance(value, int) and value > 0 for value in size
        ):
            return {}
        cell_sizes.add(tuple(size))
        matrix_identity = _dict_value(dispatch.get("matrix_identity"))
        matrix_key = matrix_identity.get("key")
        if (
            matrix_identity.get("schema")
            != "fl4359.matrix_configuration_identity.v1"
            or not isinstance(matrix_key, str)
            or not matrix_key
        ):
            return {}
        matrix_keys.add(matrix_key)
        policies.append(policy)
    modes = {policy["mode"].upper() for policy in policies}
    if len(modes) != 1 or len(cell_sizes) != 1 or len(matrix_keys) != 1:
        return {}
    return {
        **policies[0],
        "cell_size": list(next(iter(cell_sizes))),
        "matrix_identity_key": next(iter(matrix_keys)),
    }


def _publication_glyph_source_resolution(receipt: dict) -> list[dict[str, int]] | str:
    """Return only the exact atlas source sizes shared by every valid sidecar."""
    sidecars = receipt.get("presentation_field_sidecars")
    if not isinstance(sidecars, list) or not sidecars:
        return "UNKNOWN"
    observed: set[tuple[tuple[int, int], ...]] = set()
    for sidecar in sidecars:
        atlas = _dict_value(_dict_value(sidecar).get("atlas_identity"))
        sources = atlas.get("sources")
        if _dict_value(sidecar).get("status") != "valid" or not isinstance(sources, list) or not sources:
            return "UNKNOWN"
        dimensions: list[tuple[int, int]] = []
        for source in sources:
            size = _dict_value(_dict_value(source).get("dimensions"))
            width, height = size.get("w"), size.get("h")
            if not all(isinstance(value, int) and value > 0 for value in (width, height)):
                return "UNKNOWN"
            dimensions.append((width, height))
        observed.add(tuple(sorted(set(dimensions), reverse=True)))
    if len(observed) != 1:
        return "UNKNOWN"
    return [
        {"width": width, "height": height}
        for width, height in next(iter(observed))
    ]


def _publication_render_metadata(receipt: dict, capture_path: Path) -> dict:
    """Build a diagnostic publication envelope from observed runtime receipt facts.

    The headed receipt currently lacks the complete executable-source inventory
    and exported-build identity required for ATTESTED publication.  Do not fill
    that gap from the launcher's current checkout or a requested CLI mode.
    """
    policy = _publication_observed_policy(receipt)
    mode_map = {
        "FF": ("FLAT", "FLAT"),
        "FC": ("FLAT", "CONTINUOUS"),
        "CF": ("CONTINUOUS", "FLAT"),
        "CC": ("CONTINUOUS", "CONTINUOUS"),
        "FF_SHARED": ("FLAT", "FLAT"),
    }
    run_identity = _dict_value(receipt.get("run_identity"))
    source_identity = _dict_value(receipt.get("publication_runtime_source_identity"))
    launch_identity = _dict_value(source_identity.get("launch"))
    capture_source_state = "UNKNOWN"
    if isinstance(run_identity.get("build_dirty"), bool):
        capture_source_state = "DIRTY" if run_identity["build_dirty"] else "CLEAN"
    capture_digest = _sha256_file(capture_path)[:16] if capture_path.is_file() else "UNKNOWN"
    recorded_commit = launch_identity.get("source_commit") or run_identity.get("build_head")
    if not isinstance(recorded_commit, str) or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", recorded_commit) is None:
        recorded_commit = "UNKNOWN"
    metadata = {
        "provenance_status": "INCOMPLETE_CURRENT_CAPTURE",
        "fl_id": "FL-4359",
        "run_id": f"CAPTURE-{capture_digest}",
        "proof_kind": "RENDER",
        "architecture": "UNKNOWN",
        "foreground_mode": "UNKNOWN",
        "background_mode": "UNKNOWN",
        "capture_source_state": capture_source_state,
        "source_commit": recorded_commit,
        "effective_tree_fingerprint": (
            launch_identity.get("effective_tree_fingerprint")
            if source_identity.get("scoped_snapshots_match") is True
            and isinstance(launch_identity.get("effective_tree_fingerprint"), str)
            and re.fullmatch(r"[0-9a-f]{64}", launch_identity["effective_tree_fingerprint"])
            else "UNKNOWN"
        ),
        "cell_resolution": "UNKNOWN",
        "glyph_source_resolution": _publication_glyph_source_resolution(receipt),
        "storage": {"status": "PENDING"},
        "provenance_note": (
            "scoped direct-source identity is retained when stable, but no exported build "
            "or runtime architecture identity is recorded; this manifest is diagnostic only"
        ),
    }
    mode = str(policy.get("mode", "")).upper()
    cell_size = policy.get("cell_size")
    if (
        mode in mode_map
        and isinstance(cell_size, list)
        and len(cell_size) == 2
        and all(isinstance(value, int) and value > 0 for value in cell_size)
    ):
        foreground, background = mode_map[mode]
        metadata.update({
            "foreground_mode": foreground,
            "background_mode": background,
            "cell_resolution": {"width": cell_size[0], "height": cell_size[1]},
        })
    return metadata


def _publish_visual_artifacts(
    out_dir: Path,
    receipt: dict,
    capture_path: Path,
    *,
    start_state: dict[Path, tuple[int, int, int]] | None = None,
    apply_tags: bool = True,
) -> Path:
    """Hard-link all finalized run outputs and tag every visual publication."""
    metadata = _publication_render_metadata(receipt, capture_path)
    published_dir = out_dir / GODOT_PROOF_PUBLICATION_DIR
    outputs = []
    for path in sorted(out_dir.rglob("*")):
        if (
            not path.is_file()
            or path.is_relative_to(published_dir)
            or path.name in {GODOT_PROOF_PUBLICATION_MANIFEST, "artifact_manifest.json"}
        ):
            continue
        relative = path.relative_to(out_dir)
        stat = path.stat()
        current_state = (int(stat.st_ino), int(stat.st_size), int(stat.st_mtime_ns))
        if start_state is not None and start_state.get(relative) == current_state:
            continue
        outputs.append(path)
    if not outputs:
        raise PublicationError("headed proof produced no finalized artifacts to publish")
    published_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[dict] = []
    visual_suffixes = {".gif", ".jpeg", ".jpg", ".mov", ".mp4", ".png", ".webm", ".webp"}
    for legacy_path in outputs:
        visual = legacy_path.suffix.lower() in visual_suffixes
        width = height = None
        frames = 1
        if visual:
            width, height, frames = inspect_visual(legacy_path)
        role = "headed-" + "-".join(legacy_path.relative_to(out_dir).with_suffix("").parts)
        draft = {
            "path": legacy_path,
            "role": role,
            "frame_count": frames,
            "visual": visual,
        }
        if visual:
            draft["image_dimensions"] = {"width": width, "height": height}
        canonical_path = published_dir / deterministic_artifact_name(metadata, draft)
        if canonical_path.exists():
            if _sha256_file(canonical_path) != _sha256_file(legacy_path):
                raise PublicationError(f"canonical name collision has different bytes: {canonical_path}")
        else:
            try:
                canonical_path.hardlink_to(legacy_path)
            except OSError as error:
                raise PublicationError(
                    f"cannot create no-copy canonical alias for {legacy_path}: {error}"
                ) from error
        artifacts.append({**draft, "path": canonical_path, "aliases": [legacy_path]})
    manifest_path = out_dir / GODOT_PROOF_PUBLICATION_MANIFEST
    publish_artifacts(manifest_path, metadata, artifacts, artifact_root=out_dir, enforce_names=True)
    if apply_tags and sys.platform == "darwin":
        apply_finder_tags(manifest_path)
    return manifest_path


def _int_value(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _float_value(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _present_fields(*sources: dict, keys: tuple[str, ...]) -> dict:
    fields: dict = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            if key in source:
                fields[key] = source.get(key)
    return fields


def _analysis_gates_accept_proof(summary: dict) -> bool:
    gates = _dict_value(summary.get("gates"))
    if not gates:
        return False
    if not bool(gates.get("all_pass", False)):
        return False
    oracle_profile = summary.get("proof_profile")
    if oracle_profile in {"same_frame_oracle_v1", "full_grid_temporal_oracle_v1"}:
        required_gates = [
            "evidence_same_frame_oracle_spatial_rows_valid",
            "evidence_same_frame_oracle_query_complete",
            "gameplay_same_frame_geometry_agrees",
            "gameplay_same_frame_product_and_paint_presence_agree",
        ]
        if oracle_profile == "full_grid_temporal_oracle_v1":
            required_gates.extend(
                [
                    "evidence_same_frame_oracle_exhaustive_frame_coverage",
                    "evidence_same_frame_oracle_temporal_continuity",
                    "evidence_full_grid_temporal_oracle_valid",
                    "evidence_full_grid_identity_normalized",
                    "evidence_full_grid_rows_classified",
                    "evidence_full_grid_raster_witness_valid",
                    "evidence_full_grid_downstream_exact_identity",
                    "evidence_full_grid_exact_current_ray_differences_reported",
                    "gameplay_full_grid_zero_structural_geometry_errors",
                    "gameplay_full_grid_zero_changing_reference_state",
                    "gameplay_full_grid_exact_product_translation",
                    "gameplay_full_grid_zero_glyph_mask_splits",
                    "gameplay_cell_mask_owner_stability_bounded",
                    "gameplay_full_grid_zero_duplicate_out_of_place",
                    "evidence_full_grid_first_divergence_known",
                    "evidence_presentation_observer_population_accounted",
                    "evidence_presentation_observer_sensitivity_valid",
                    # A structurally correct oracle run is not a temporal
                    # presentation pass.  The completion front door must keep
                    # the three user-visible motion symptoms independently
                    # blocking even when every geometry row agrees.
                    "gameplay_moving_camera_jitter_bounded",
                    "gameplay_content_shimmer_bounded",
                    "gameplay_continuous_product_colour_stability_bounded",
                    # Terminal world-point reprojection is a distinct
                    # positional-jitter proof.  Whole-cell presentation uses
                    # exact expected-address equality.  The pixel-centroid
                    # residual remains diagnostic because canon defines no
                    # calibration statistic or threshold law for it.
                    "evidence_terminal_expected_reprojection_valid",
                    "gameplay_terminal_expected_cell_address_exact",
                    "evidence_celestial_time_fixed",
                    "evidence_product_owner_name_transport_valid",
                    "evidence_terrain_product_homogeneity_present",
                    "evidence_terrain_product_final_paint_join_complete",
                    "gameplay_terrain_product_distribution_non_degenerate",
                    "evidence_terrain_product_homogeneity_baseline_calibrated",
                    "evidence_product_admission_contingency_valid",
                    "gameplay_product_admission_chain_exact",
                ]
            )
    else:
        required_gates = list(REQUIRED_ANALYZER_PROOF_GATES)
    if summary.get("proof_profile") == "airplane_temporal_v1":
        required_gates.append("evidence_sparse_capture_schedule_complete")
        required_gates.append("evidence_temporal_authority_tick_continuity")
    for gate_name in required_gates:
        gate = _dict_value(gates.get(gate_name))
        if not bool(gate.get("pass", False)):
            return False
    return True


def _night_airborne_temporal_capture_accepts_analysis_skip(
    summary: dict,
    receipt: dict,
    skip_jitter_analysis: bool,
) -> bool:
    """Accept analyzer absence only for the canonical airborne PNG sequence.

    The adapter performs the detailed product-fact validation after this
    wrapper returns. This predicate only proves that the wrapper received three
    exact route-tick frames from the authoritative dogfight process. Grounded
    receipts cannot enter through this boundary.
    """
    frames = receipt.get("visual_frame_samples")
    ticks = receipt.get("visual_route_target_ticks")
    if not isinstance(frames, list) or not isinstance(ticks, list):
        return False
    if len(frames) != 3 or len(ticks) != 3 or ticks != sorted(set(ticks)):
        return False
    capture_id = receipt.get("temporal_capture_id")
    if not isinstance(capture_id, str) or not capture_id:
        return False
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            return False
        airplane = frame.get("airplane_position")
        if not isinstance(airplane, dict):
            return False
        airplane_y = airplane.get("y")
        if not isinstance(airplane_y, (int, float)) or float(airplane_y) <= 0.0:
            return False
        if frame.get("route_tick") != ticks[index]:
            return False
        if frame.get("temporal_capture_id") != capture_id:
            return False
        if frame.get("png_captured_in_same_process") is not True:
            return False
    return (
        skip_jitter_analysis
        and receipt.get("night_airborne_temporal_sequence") is True
        and receipt.get("route_identity") == SERVER_TICK_AIRPLANE_ROUTE_IDENTITY
        and receipt.get("airborne_airplane_ready") is True
        and receipt.get("local_airplane_pilot") is True
        and receipt.get("visual_route_ticks_exact") is True
        and receipt.get("missing_visual_route_ticks") == []
        and summary.get("analysis_skipped") is True
        and summary.get("reason") == "canonical_png_grid_capture_only"
    )


def _clean_play_no_capture_args_are_isolated(arguments: list[str]) -> bool:
    """Reject capture plus readback switches from the clean timing arm.

    The argument list is inspected before launch because a driver-side flag is
    too late: merely enabling a GPU readback path can alter the measured frame
    times even when the resulting artifact is later ignored.
    """
    if "--fl4359-clean-play-no-capture" not in arguments:
        return False
    forbidden_fragments = (
        "capture",
        "temporal",
        "secondary-effect",
        "readback",
        "moving-cell-dump",
        "epoch-depth-sign-trace",
    )
    for argument in arguments:
        # The arm selector states the absence of capture. It is the one token
        # containing the word "capture" that is required instead of forbidden.
        if argument == "--fl4359-clean-play-no-capture":
            continue
        if any(fragment in argument for fragment in forbidden_fragments):
            return False
    return True


def _clean_play_no_capture_accepts_analysis_skip(
    summary: dict,
    receipt: dict,
    skip_jitter_analysis: bool,
) -> bool:
    """Accept timing receipts only when the driver proves zero capture work."""
    frame_times = receipt.get("clean_play_frame_times_ms")
    return (
        skip_jitter_analysis
        and receipt.get("route_identity") == SERVER_TICK_AIRPLANE_ROUTE_IDENTITY
        and receipt.get("clean_play_no_capture") is True
        and receipt.get("clean_play_timing_uncontaminated") is True
        and receipt.get("jitter_capture_requested") is False
        and receipt.get("frame_captured") is False
        and receipt.get("capture_path", "") == ""
        and int(receipt.get("visual_frame_count", 0) or 0) == 0
        and isinstance(frame_times, list)
        and len(frame_times) > 0
        and summary.get("analysis_skipped") is True
        and summary.get("reason") == "canonical_png_grid_capture_only"
    )


def _parse_prefixed_json_receipts(log_text: str, prefix: str) -> list[dict]:
    receipts: list[dict] = []
    for line in log_text.splitlines():
        if prefix not in line:
            continue
        payload = line.split(prefix, 1)[1].strip()
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(decoded, dict):
            receipts.append(decoded)
    return receipts


def _parse_fl4359_lerp_receipts(log_text: str) -> list[dict]:
    return _parse_prefixed_json_receipts(log_text, "FL4359_LERP_RECEIPT ")


def _position_string_is_nonzero(value: object) -> bool:
    if not isinstance(value, str):
        return False
    cleaned = value.strip().strip("()")
    try:
        components = [float(part.strip()) for part in cleaned.split(",")]
    except ValueError:
        return False
    return len(components) == 3 and any(abs(component) > 1e-6 for component in components)


def _fl4671_lerp_receipt_summary(
    *,
    godot_log: Path,
    receipt: dict,
    repo_provenance: dict,
) -> dict:
    log_text = godot_log.read_text() if godot_log.exists() else ""
    rows = _parse_fl4359_lerp_receipts(log_text)
    world_rows = _parse_prefixed_json_receipts(log_text, "FL4359_PHASE2A_WORLD_RECEIPT ")
    camera_rows = _parse_prefixed_json_receipts(log_text, "FL4359_ONFOOT_CAMERA_RECEIPT ")
    correction_errors = [
        float(row.get("correction_error", 0.0))
        for row in rows
        if _finite_number(row.get("correction_error", 0.0))
    ]
    extrapolation_ages = [
        float(row.get("extrapolation_age_ticks", 0.0))
        for row in rows
        if _finite_number(row.get("extrapolation_age_ticks", 0.0))
    ]
    newest_ticks = [
        float(row.get("newest_tick", -1.0))
        for row in rows
        if _finite_number(row.get("newest_tick", -1.0))
    ]
    mounted_world_frames = sum(1 for row in world_rows if row.get("is_pilot") is True)
    mounted_camera_frames = sum(1 for row in camera_rows if row.get("is_pilot") is True)
    nonzero_airplane_position_frames = sum(
        1 for row in world_rows if _position_string_is_nonzero(row.get("airplane_position"))
    )
    movement_delta = receipt.get("movement_delta")
    airplane_faces = _int_value(receipt.get("airplane_dynamic_face_count", 0), 0)
    route_mounted_witness_present = (
        len(rows) > 0
        and mounted_world_frames > 0
        and mounted_camera_frames > 0
        and nonzero_airplane_position_frames > 0
        and _finite_number(movement_delta)
        and float(movement_delta) > 0.0
        and airplane_faces > 0
    )
    return {
        "fl": "FL-4671",
        "diagnostic": "mounted_airplane_presentation_interpolation_receipts",
        "acceptance_claimed": False,
        "closure_claimed": False,
        "receipt_count": len(rows),
        "phase2a_world_receipt_count": len(world_rows),
        "onfoot_camera_receipt_count": len(camera_rows),
        "receipt_rows_present": len(rows) > 0,
        "mounted_world_frames": mounted_world_frames,
        "mounted_camera_frames": mounted_camera_frames,
        "nonzero_airplane_position_frames": nonzero_airplane_position_frames,
        "airplane_dynamic_face_count": airplane_faces,
        "route_mounted_witness_present": route_mounted_witness_present,
        "all_correction_errors_zero": bool(correction_errors) and max(correction_errors) <= 1e-6,
        "max_correction_error": max(correction_errors) if correction_errors else None,
        "max_extrapolation_age_ticks": max(extrapolation_ages) if extrapolation_ages else None,
        "interp_modes": sorted({str(row.get("interp_mode", "")) for row in rows if row.get("interp_mode", "")}),
        "actor_ids": sorted({str(row.get("actor_id", "")) for row in rows if row.get("actor_id", "")}),
        "newest_tick_first": newest_ticks[0] if newest_ticks else None,
        "newest_tick_last": newest_ticks[-1] if newest_ticks else None,
        "route_identity": receipt.get("route_identity"),
        "airborne_airplane_ready": receipt.get("airborne_airplane_ready"),
        "local_airplane_pilot": receipt.get("local_airplane_pilot"),
        "airplane_world_item_count_exact": receipt.get("airplane_world_item_count_exact"),
        "first_active_flight_intent": receipt.get("first_active_flight_intent"),
        "first_active_throttle_command": receipt.get("first_active_throttle_command"),
        "airplane_yaw_turn_measured": receipt.get("airplane_yaw_turn_measured"),
        "airplane_yaw_turn_delta": receipt.get("airplane_yaw_turn_delta"),
        "movement_delta": movement_delta,
        "camera_delta": receipt.get("camera_delta"),
        "git_head": repo_provenance.get("git_head", ""),
        "git_dirty": bool(repo_provenance.get("git_dirty", False)),
        "source": "scripts/run_fl4359_headed_movement.py",
    }


def _fl4671_lerp_receipt_diagnostic_accepts_analysis_skip(
    summary: dict,
    receipt: dict,
    skip_jitter_analysis: bool,
    fl4671_summary: dict,
) -> bool:
    return (
        skip_jitter_analysis
        and summary.get("analysis_skipped") is True
        and summary.get("reason") == "fl4671_lerp_receipt_diagnostic"
        and receipt.get("route_identity") == SCRIPTED_AIRBORNE_AIRPLANE_ROUTE_IDENTITY
        and fl4671_summary.get("route_mounted_witness_present") is True
        and fl4671_summary.get("receipt_rows_present") is True
        and fl4671_summary.get("all_correction_errors_zero") is True
    )


def _has_numeric_field(summary: dict, key: str) -> bool:
    if key not in summary:
        return False
    value = summary.get(key)
    if value is None:
        return False
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _has_valid_consecutive_movement_metric(summary: dict) -> bool:
    moving_pairs = _int_value(summary.get("moving_frame_pairs"), 0)
    if moving_pairs <= 0:
        return False
    if not (
        _has_numeric_field(summary, "camera_motion_continuous_ratio")
        or _has_numeric_field(summary, "camera_translation_total")
        or _has_numeric_field(summary, "camera_translation_max_per_frame")
    ):
        return False
    gates = _dict_value(summary.get("gates"))
    scenario_gate = _dict_value(gates.get("scenario_moving_camera_continuous"))
    if scenario_gate and not bool(scenario_gate.get("pass", False)):
        return False
    return True


def _current_metric_unmeasured_line(metric: str, reason: str) -> str:
    definition = CURRENT_METRIC_DEFINITIONS[metric]
    return f"current_{metric}({definition})=UNMEASURED reason={reason}"


def _current_metric_report_lines(summary: dict) -> list[str]:
    lines = [
        "current_metric_definitions: "
        "jitter positional instability; "
        "shimmer final visible signature instability; "
        "bounce region-scale oscillation; "
        "measurement_requires=valid_consecutive_frame_movement",
    ]
    has_valid_motion = _has_valid_consecutive_movement_metric(summary)
    if not has_valid_motion:
        reason = "missing_valid_consecutive_frame_movement_metric"
        lines.extend([
            _current_metric_unmeasured_line("jitter", reason),
            _current_metric_unmeasured_line("shimmer", reason),
            _current_metric_unmeasured_line("bounce", reason),
        ])
    else:
        if _has_numeric_field(summary, "max_snap_px_delta"):
            lines.append(
                "current_jitter(%s)=MEASURED "
                "max_snap_delta=%.3fpx avg_snap_delta=%.3fpx snap_sign_flip_rate=%.3f%% "
                "moving_frame_pairs=%d"
                % (
                    CURRENT_METRIC_DEFINITIONS["jitter"],
                    float(summary.get("max_snap_px_delta", 0.0)),
                    float(summary.get("avg_snap_px_delta", 0.0)),
                    float(summary.get("snap_sign_flip_rate", 0.0)),
                    _int_value(summary.get("moving_frame_pairs"), 0),
                )
            )
        else:
            lines.append(_current_metric_unmeasured_line("jitter", "missing_positional_instability_metric"))
        if _has_numeric_field(summary, "motion_compensated_final_cell_instability_pct"):
            lines.append(
                "current_shimmer(%s)=MEASURED "
                "motion_compensated_final_cell_instability=%.2f%% "
                "final_cell_signature_churn=%.2f%%"
                % (
                    CURRENT_METRIC_DEFINITIONS["shimmer"],
                    float(summary.get("motion_compensated_final_cell_instability_pct", 0.0)),
                    float(summary.get("final_cell_signature_churn_pct", 0.0)),
                )
            )
        else:
            lines.append(
                _current_metric_unmeasured_line(
                    "shimmer",
                    "missing_motion_compensated_final_cell_instability_pct",
                )
            )
        if _has_numeric_field(summary, "max_post_surface_bounce_pct"):
            lines.append(
                "current_bounce(%s)=MEASURED "
                "post_surface_bounce=%.2f%% selected_surface_identity_churn=%.2f%% "
                "final_product_identity_churn=%.2f%%"
                % (
                    CURRENT_METRIC_DEFINITIONS["bounce"],
                    float(summary.get("max_post_surface_bounce_pct", 0.0)),
                    float(summary.get("max_selected_surface_identity_churn_pct", 0.0)),
                    float(summary.get("max_final_product_identity_churn_pct", 0.0)),
                )
            )
        else:
            lines.append(
                _current_metric_unmeasured_line(
                    "bounce",
                    "missing_post_surface_bounce_metric",
                )
            )
    if _has_numeric_field(summary, "max_pair_jitter_pct"):
        lines.append(
            "legacy_narrow_pair_metric: max_pair_jitter_pct=%.2f%% "
            "label=legacy_not_current_shimmer_bounce"
            % float(summary.get("max_pair_jitter_pct", 0.0))
        )
    return lines


def _airplane_metadata_frame_window(args: argparse.Namespace) -> tuple[int, int]:
    requested_frames = max(1, int(args.plane_circle_frames))
    requested_capture_limit = max(0, int(args.jitter_capture_frame_limit))
    if args.camera_mode not in ("plane_air", "plane_air_ortho"):
        return requested_frames, requested_capture_limit
    if args.skip_jitter_analysis or requested_capture_limit == 0:
        return requested_frames, requested_capture_limit
    cap = max(0, int(args.airplane_metadata_frame_cap))
    if cap == 0:
        return requested_frames, requested_capture_limit
    effective_frames = min(requested_frames, cap)
    effective_capture_limit = min(requested_capture_limit, effective_frames)
    return max(1, effective_frames), max(1, effective_capture_limit)


def _effective_airplane_turn_frames(args: argparse.Namespace, effective_plane_circle_frames: int) -> int:
    requested_turn_frames = max(0, int(args.turn_frames))
    if args.camera_mode not in ("plane_air", "plane_air_ortho"):
        return requested_turn_frames
    return min(requested_turn_frames, max(1, int(effective_plane_circle_frames)))


def _invalid_airplane_proof_bounds_reason(args: argparse.Namespace) -> str:
    if args.camera_mode not in ("plane_air", "plane_air_ortho"):
        return ""
    if _stationary_calibration_enabled(args):
        return ""
    if args.skip_jitter_analysis:
        return ""
    if int(args.airplane_metadata_frame_cap) <= 0:
        return "airplane_metadata_frame_cap_must_be_positive"
    if int(args.jitter_capture_frame_limit) <= 0:
        return "jitter_capture_frame_limit_must_be_positive"
    # FL-4359: a large-output allowance cannot make an invalid stride meaningful.
    if int(args.jitter_capture_world_sample_stride) <= 0:
        return "jitter_capture_world_sample_stride_must_be_positive"
    if int(args.jitter_capture_frame_limit) < 5:
        return "jitter_capture_frame_limit_must_include_five_active_frames"
    if int(getattr(args, "after_frames", 2)) < 2:
        return "after_frames_must_include_release_and_settle"
    if getattr(args, "analyzer_proof_profile", "") == "full_grid_temporal_oracle_v1":
        if int(getattr(args, "after_frames", 2)) != 2:
            return "full_grid_after_frames_must_equal_two"
        full_grid_total_frames = (
            int(args.jitter_capture_frame_limit)
            + int(getattr(args, "after_frames", 2))
        )
        if full_grid_total_frames > MAX_FULL_GRID_TEMPORAL_CAPTURE_FRAMES:
            return "full_grid_temporal_capture_total_exceeds_20"
    if not bool(args.allow_large_jitter_capture):
        if int(args.jitter_capture_frame_limit) > MAX_COMPLETION_JITTER_CAPTURE_FRAME_LIMIT:
            return "jitter_capture_frame_limit_requires_large_capture_flag"
        if int(args.jitter_capture_world_sample_stride) < MIN_COMPLETION_JITTER_CAPTURE_WORLD_SAMPLE_STRIDE:
            return "jitter_capture_world_sample_stride_requires_large_capture_flag"
    return ""


def _invalid_airplane_route_contract_reason(args: argparse.Namespace, effective_plane_circle_frames: int) -> str:
    if args.camera_mode not in ("plane_air", "plane_air_ortho"):
        return ""
    if _stationary_calibration_enabled(args):
        return ""
    if args.skip_jitter_analysis:
        return ""
    if int(args.plane_circle_frames) <= 0:
        return "plane_circle_frames_must_be_positive"
    if int(args.turn_frames) <= 0:
        return "turn_frames_must_be_positive"
    if _effective_airplane_turn_frames(args, effective_plane_circle_frames) <= 0:
        return "effective_turn_frames_must_be_positive"
    if effective_plane_circle_frames < 5:
        return "effective_plane_circle_frames_must_include_five_active_frames"
    expected_projection = "perspective" if args.camera_mode == "plane_air" else "ortho"
    if getattr(args, "exact_camera_projection", expected_projection) != expected_projection:
        return "airplane_camera_projection_must_match_camera_mode"
    product_time = getattr(args, "product_time", 0.0)
    if product_time is None or not math.isfinite(float(product_time)):
        return "product_time_pin_required_for_movement_proof"
    return ""


def _airplane_route_receipt_accepts_proof(receipt: dict, camera_mode: str) -> bool:
    """Validate one physical-flight capture without retired route receipts.

    The server still owns airplane movement. The deleted proof-only intent
    epoch and applied-tick fields no longer participate in admission.
    """
    if camera_mode not in ("plane_air", "plane_air_ortho"):
        return True
    common_route_ok = (
        receipt.get("route_identity") == SCRIPTED_AIRBORNE_AIRPLANE_ROUTE_IDENTITY
        and receipt.get("airborne_airplane_ready") is True
        and receipt.get("local_airplane_pilot") is True
        and receipt.get("airplane_world_item_count_exact") is True
        and _int_value(receipt.get("max_airplane_world_item_count"), 0) > 0
        and _float_value(receipt.get("first_active_throttle_command"), 0.0) > 0.0
        and abs(_float_value(receipt.get("first_active_roll_command"), 0.0)) > 0.01
        and abs(_float_value(receipt.get("first_active_yaw_command"), 0.0)) > 0.01
        and bool(receipt.get("camera_yaw_held", False))
        and bool(receipt.get("airplane_yaw_turn_measured", False))
        and receipt.get("sparse_lifecycle_facts_valid") is True
    )
    if not common_route_ok:
        return False
    if receipt.get("clean_play_no_capture") is True:
        return (
            receipt.get("clean_play_timing_uncontaminated") is True
            and receipt.get("frame_captured") is False
            and _int_value(receipt.get("visual_frame_count"), 0) == 0
        )
    return (
        receipt.get("visual_route_ticks_exact") is True
        and receipt.get("missing_visual_route_ticks") == []
        and _int_value(receipt.get("visual_frame_count"), 0) > 0
    )


def _full_grid_runtime_celestial_receipt_accepts(
    receipt: dict, args: argparse.Namespace
) -> bool:
    """Require frozen runtime celestial facts, not only a CLI day-cycle pin."""
    if not _full_grid_day_cycle_time_valid(args):
        return False
    if receipt.get("day_cycle_time_pin_valid") is not True:
        return False
    expected_day_cycle = float(getattr(args, "day_cycle_time", FULL_GRID_REQUIRED_DAY_CYCLE_TIME))
    if not _finite_number(receipt.get("day_cycle_time")) or not math.isclose(
        float(receipt.get("day_cycle_time")),
        expected_day_cycle,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        return False
    if receipt.get("celestial_runtime_witness_valid") is not True:
        return False
    if _int_value(receipt.get("celestial_witness_invalid_count"), 1) != 0:
        return False
    if _int_value(receipt.get("celestial_witness_change_count"), 1) != 0:
        return False
    frames = receipt.get("celestial_frame_witnesses")
    if not isinstance(frames, list):
        return False
    schedule = _dict_value(receipt.get("capture_schedule"))
    # Movement capture observes the complete active/release/settle lifecycle.
    # The exact-pose GIF uses the five active frames, but every additional
    # lifecycle frame must carry the same frozen celestial witness.
    expected_frames = _int_value(schedule.get("effective_total_frames"), 0)
    if expected_frames <= 0:
        expected_frames = _int_value(schedule.get("effective_active_frames"), 0)
    if expected_frames < 5 or len(frames) != expected_frames:
        return False
    baseline = None
    for frame in frames:
        if not isinstance(frame, dict) or frame.get("celestial_witness_valid") is not True:
            return False
        celestial_time = frame.get("celestial_time")
        night_factor = frame.get("celestial_night_factor")
        sun_direction = frame.get("celestial_sun_direction")
        if not _finite_number(celestial_time) or not _finite_number(night_factor):
            return False
        if (
            not isinstance(sun_direction, list)
            or len(sun_direction) != 3
            or any(not _finite_number(component) for component in sun_direction)
        ):
            return False
        witness = (celestial_time, night_factor, tuple(sun_direction))
        if baseline is None:
            baseline = witness
        elif witness != baseline:
            return False
    return baseline is not None


def _stationary_calibration_receipt_accepts(
    receipt: dict, args: argparse.Namespace
) -> bool:
    """Admit only a complete stationary calibration receipt, never movement proof."""
    if not _stationary_calibration_enabled(args):
        return False
    schedule = receipt.get("capture_schedule", {})
    pose_contract = receipt.get("stationary_pose_contract", {})
    samples = receipt.get("stationary_frame_samples", [])
    expected_day_cycle = getattr(args, "day_cycle_time", None)
    observed_day_cycle = receipt.get("day_cycle_time", None)
    day_cycle_matches = (
        _full_grid_day_cycle_time_valid(args)
        and _finite_number(observed_day_cycle)
        and math.isclose(
            float(observed_day_cycle), float(expected_day_cycle), rel_tol=0.0, abs_tol=1e-9
        )
    )
    return bool(
        receipt.get("calibration_only") is True
        and receipt.get("proof") == STATIONARY_TERMINAL_REPROJECTION_CALIBRATION
        and receipt.get("route_identity") == SCRIPTED_AIRBORNE_AIRPLANE_ROUTE_IDENTITY
        and receipt.get("calibration_of_route") == SCRIPTED_AIRBORNE_AIRPLANE_ROUTE_IDENTITY
        and receipt.get("camera_mode") == "plane_air"
        and receipt.get("expected_projection") == "perspective"
        and receipt.get("day_cycle_time_pin_valid") is True
        and day_cycle_matches
        and _full_grid_runtime_celestial_receipt_accepts(receipt, args)
        and receipt.get("product_time_pin_valid") is True
        and receipt.get("sparse_lifecycle_facts_valid") is True
        and receipt.get("sparse_capture_drain_pass") is True
        and receipt.get("sparse_capture_pending_after_drain") is False
        and _int_value(receipt.get("sparse_capture_failed_request_count"), 1) == 0
        and _int_value(receipt.get("sparse_capture_join_invalid_rows"), 1) == 0
        and int(receipt.get("sparse_capture_written_row_count", 0) or 0) > 0
        and int(schedule.get("effective_active_frames", 0) or 0) == 5
        and int(schedule.get("observed_active_frames", 0) or 0) == 5
        and schedule.get("schedule_complete") is True
        and isinstance(samples, list)
        and len(samples) == 5
        and pose_contract.get("pass") is True
    )


def _stationary_calibration_summary_accepts(summary: dict) -> bool:
    """Validate calibration transport without turning it into renderer acceptance."""
    if summary.get("proof_profile") != "full_grid_temporal_oracle_v1":
        return False
    if summary.get("analysis_skipped") is True:
        return False
    required = summary.get("required_field_coverage", {})
    terminal = summary.get("terminal_expected_reprojection", {})
    return bool(
        int(summary.get("frame_count", 0) or 0) == 5
        and summary.get("full_grid_temporal_oracle_valid") is True
        and summary.get("same_frame_oracle_temporal_continuity_valid") is True
        and summary.get("same_frame_oracle_fixed_day_cycle_time_valid") is True
        and isinstance(required, dict)
        and required.get("all_present") is True
        and summary.get("product_anchor_provenance_complete") is True
        and isinstance(terminal, dict)
        and terminal.get("valid") is True
        and terminal.get("expected_cell_address_pass") is True
        and int(
            terminal.get("counts", {}).get(
                "comparable_terminal_displacements", 0
            )
            or 0
        ) > 0
        and int(
            terminal.get("counts", {}).get("comparable_expected_cells", 0)
            or 0
        ) > 0
        and int(
            terminal.get("counts", {}).get("expected_cell_mismatches", 0)
            or 0
        ) == 0
        and terminal.get("invalid", {}) == {}
    )


def _gif_family_artifact_paths(value: object) -> list[str]:
    """Return existing path strings from a receipt or summary GIF-family field."""
    paths: list[str] = []
    if isinstance(value, dict):
        for item in value.values():
            paths.extend(_gif_family_artifact_paths(item))
    elif isinstance(value, list):
        for item in value:
            paths.extend(_gif_family_artifact_paths(item))
    elif isinstance(value, str) and value.strip():
        paths.append(value.strip())
    return paths


def _full_grid_gif_families_present(summary: dict, receipt: dict) -> dict:
    """Require the four requested GIF-family artifacts as real files."""
    paths = []
    for key in ("gif_families", "required_gif_families", "four_gif_families"):
        paths.extend(_gif_family_artifact_paths(summary.get(key)))
        paths.extend(_gif_family_artifact_paths(receipt.get(key)))
    paths.extend(_gif_family_artifact_paths(summary.get("sweep_gif")))
    unique_paths = []
    for path in paths:
        if path not in unique_paths:
            unique_paths.append(path)
    existing = [path for path in unique_paths if Path(path).is_file()]
    return {
        "pass": len(existing) >= FULL_GRID_REQUIRED_GIF_FAMILY_COUNT,
        "required_count": FULL_GRID_REQUIRED_GIF_FAMILY_COUNT,
        "present_count": len(existing),
        "paths": existing,
        "reason": (
            "full_grid_gif_families_present"
            if len(existing) >= FULL_GRID_REQUIRED_GIF_FAMILY_COUNT
            else "missing_required_gif_families"
        ),
    }


def _presentation_artifact_manifest_value(summary: dict, receipt: dict) -> tuple[dict | None, str]:
    """Read one named manifest and reject contradictory summary/receipt copies."""
    summary_value = summary.get("presentation_artifact_manifest")
    receipt_value = receipt.get("presentation_artifact_manifest")
    if summary_value is not None and receipt_value is not None and summary_value != receipt_value:
        return None, "presentation_artifact_manifest_conflict"
    value = summary_value if summary_value is not None else receipt_value
    if value is None:
        return None, "presentation_artifact_manifest_missing"
    if not isinstance(value, dict):
        return None, "presentation_artifact_manifest_must_be_object"
    return value, ""


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _native_artifact_ready(
    arm: dict,
    role_rows: dict[str, dict],
    native_binding: dict | None = None,
) -> bool:
    """Require a producer-owned same-frame viewport witness for the native GIF.

    The cell-field rasterizer can create every derived family, including a GIF
    named ``native`` if a caller merely renames one.  That is not a native view.
    A future viewport producer must publish this small witness beside the arm;
    until then the arm remains useful field evidence while four-mode visual
    comparison stays ineligible.
    """
    native = role_rows.get("native")
    if not isinstance(native, dict):
        return False
    witness = arm.get("native_viewport_source")
    if not isinstance(witness, dict):
        witness = native_binding
        if isinstance(witness, dict):
            return bool(
                witness.get("admitted") is True
                and witness.get("native_path") == native.get("path")
            )
    return bool(
        isinstance(native, dict)
        and isinstance(witness, dict)
        and witness.get("status") == "available"
        and witness.get("owner") == "same_frame_viewport_capture"
        and witness.get("frame_ids") == list(PRESENTATION_CAPTURE_FRAME_IDS)
        and witness.get("artifact_path") == native.get("path")
    )


def _presentation_user_args(mode: str, user_args: list[str]) -> list[str]:
    """FL-4907: admission, World startup and proof selection must name one path."""
    values = list(user_args)
    for prefix in ("--presentation-mode=", "--fl4359-presentation-mode="):
        matching = [value for value in values if value.startswith(prefix)]
        if any(value != prefix + mode for value in matching):
            raise ValueError(f"{prefix} conflicts with --presentation-mode {mode}")
        if not matching:
            values.append(prefix + mode)
    return values


def _applied_policy_matches_mode(policy: object) -> bool:
    """Check the declared mode against both sampling bits it names."""
    if not isinstance(policy, dict) or policy.get("status") != "applied":
        return False
    expected_sampling = {
        "FF": ("flat", "flat"),
        "FC": ("flat", "continuous"),
        "CF": ("continuous", "flat"),
        "CC": ("continuous", "continuous"),
        "FF_SHARED": ("flat", "flat"),
    }.get(policy.get("mode"))
    if policy.get("mode") == "FF_SHARED" and (
        policy.get("driver") != "shared_compositor" or policy.get("sampling_mode") != "FF"
    ):
        return False
    return expected_sampling == (
        policy.get("fg_sampling"),
        policy.get("bg_sampling"),
    ) and policy.get("bg_owner") in {
        "water",
        "family",
        "inherited",
        "unowned",
        "world",
        "terrain",
        "shared",
    }


def _presentation_gif_facts(path: Path) -> tuple[dict | None, str]:
    """Verify that an artifact is a real five-frame GIF, not a renamed blob."""
    try:
        from PIL import Image
    except ImportError:
        return None, "pillow_unavailable"
    try:
        with Image.open(path) as image:
            if image.format != "GIF":
                return None, "artifact_format_must_be_gif"
            frame_count = int(getattr(image, "n_frames", 1))
            dimensions = (int(image.width), int(image.height))
    except (OSError, ValueError):
        return None, "artifact_gif_decode_failed"
    if frame_count != len(PRESENTATION_CAPTURE_FRAME_IDS):
        return None, "artifact_gif_frame_count_must_equal_5"
    return {"frame_count": frame_count, "dimensions": dimensions}, ""


def _presentation_artifact_arm_contract(
    arm_name: str,
    arm: object,
    *,
    native_binding: dict | None = None,
) -> dict:
    """Validate one arm's seven actual-field artifacts and shared metadata."""
    failed: list[str] = []
    if not isinstance(arm, dict):
        return {"pass": False, "arm": arm_name, "failed_checks": ["arm_must_be_object"]}
    required = ("capture_id", "frame_ids", "poses", "profile_id", "mode_id", "field_sha256", "artifacts")
    for key in required:
        if key not in arm:
            failed.append(f"{arm_name}_missing_{key}")
    frame_ids = arm.get("frame_ids")
    if frame_ids != list(PRESENTATION_CAPTURE_FRAME_IDS):
        failed.append(f"{arm_name}_frame_ids_must_be_0_to_4")
    poses = arm.get("poses")
    if not isinstance(poses, list) or len(poses) != len(PRESENTATION_CAPTURE_FRAME_IDS):
        failed.append(f"{arm_name}_poses_must_contain_five")
    profile_id = arm.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id.strip():
        failed.append(f"{arm_name}_profile_id_invalid")
    mode_id = arm.get("mode_id")
    if not isinstance(mode_id, str) or not mode_id.strip():
        failed.append(f"{arm_name}_mode_id_invalid")
    field_sha256 = arm.get("field_sha256")
    if (
        not isinstance(field_sha256, list)
        or len(field_sha256) != len(PRESENTATION_CAPTURE_FRAME_IDS)
        or not all(_valid_sha256(value) for value in field_sha256)
    ):
        failed.append(f"{arm_name}_field_sha256_invalid")
    artifacts = arm.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
        failed.append(f"{arm_name}_artifacts_must_be_list")
    role_rows: dict[str, dict] = {}
    paths: list[str] = []
    for index, artifact in enumerate(artifacts):
        prefix = f"{arm_name}_artifact_{index}"
        if not isinstance(artifact, dict):
            failed.append(f"{prefix}_must_be_object")
            continue
        role = artifact.get("role")
        if role not in PRESENTATION_ARTIFACT_ROLES:
            failed.append(f"{prefix}_unrelated_role")
            continue
        if role in role_rows:
            failed.append(f"{arm_name}_duplicate_role_{role}")
        role_rows[role] = artifact
        path_value = artifact.get("path")
        if not isinstance(path_value, str) or not path_value.strip():
            failed.append(f"{prefix}_path_missing")
            continue
        path = Path(path_value).expanduser()
        if not path.is_file():
            failed.append(f"{prefix}_path_not_real_file")
        else:
            paths.append(str(path.resolve()))
            if not _valid_sha256(artifact.get("sha256")):
                failed.append(f"{prefix}_sha256_invalid")
            elif _sha256_file(path) != artifact.get("sha256"):
                failed.append(f"{prefix}_sha256_mismatch")
            gif_facts, gif_reason = _presentation_gif_facts(path)
            if gif_facts is None:
                failed.append(f"{prefix}_{gif_reason}")
            else:
                if artifact.get("frame_count") != gif_facts["frame_count"]:
                    failed.append(f"{prefix}_frame_count_mismatch")
                if _size_tuple(artifact.get("dimensions")) != gif_facts["dimensions"]:
                    failed.append(f"{prefix}_dimensions_mismatch")
        if artifact.get("frame_ids") != frame_ids:
            failed.append(f"{prefix}_frame_ids_mismatch")
        if artifact.get("poses") != poses:
            failed.append(f"{prefix}_poses_mismatch")
        if artifact.get("capture_id") != arm.get("capture_id"):
            failed.append(f"{prefix}_capture_id_mismatch")
        if artifact.get("profile_id") != profile_id:
            failed.append(f"{prefix}_profile_id_mismatch")
        if artifact.get("mode_id") != mode_id:
            failed.append(f"{prefix}_mode_id_mismatch")
        if artifact.get("field_sha256") != field_sha256:
            failed.append(f"{prefix}_field_sha256_mismatch")
    for role in PRESENTATION_ARTIFACT_ROLES:
        if role not in role_rows:
            failed.append(f"{arm_name}_missing_role_{role}")
    duplicate_paths = sorted({path for path in paths if paths.count(path) > 1})
    if duplicate_paths:
        failed.append(f"{arm_name}_duplicate_real_paths")
    return {
        "pass": not failed,
        "arm": arm_name,
        "failed_checks": failed,
        "paths": paths,
        "role_rows": role_rows,
        "actual_field_eligible": not failed and bool(field_sha256),
        "native_artifact_ready": _native_artifact_ready(
            arm, role_rows, native_binding
        ),
        "mode_comparison_eligible": bool(
            not failed
            and _native_artifact_ready(arm, role_rows, native_binding)
            and arm.get("field_observations_mode_eligible") is True
            and _applied_policy_matches_mode(arm.get("applied_policy"))
        ),
    }


def _presentation_artifact_manifest_gate(summary: dict, receipt: dict) -> dict:
    """Validate named five-frame artifacts without reviving the old count gate."""
    manifest, manifest_reason = _presentation_artifact_manifest_value(summary, receipt)
    if manifest is None:
        return {
            "pass": False,
            "required": False,
            "reason": manifest_reason,
            "actual_field_eligible": False,
            "native_artifact_ready": False,
            "mode_comparison_eligible": False,
            "legacy_diagnostic": True,
            "failed_checks": [manifest_reason],
        }
    failed: list[str] = []
    if manifest.get("schema") != PRESENTATION_ARTIFACT_MANIFEST_SCHEMA:
        failed.append("manifest_schema_mismatch")
    eligibility = manifest.get("eligibility")
    if eligibility not in {"actual_field", "legacy_pair_diagnostic"}:
        failed.append("manifest_eligibility_invalid")
    if eligibility == "legacy_pair_diagnostic":
        # Legacy GIF receipts remain inspectable diagnostics. They deliberately
        # do not enter the actual-field role or four-mode comparison gate.
        return {
            "pass": not failed,
            "required": True,
            "reason": "legacy_pair_diagnostic",
            "eligibility": eligibility,
            "legacy_diagnostic": True,
            "actual_field_eligible": False,
            "native_artifact_ready": False,
            "mode_comparison_eligible": False,
            "failed_checks": failed,
        }
    arm_kind = manifest.get("arm_kind")
    if arm_kind not in {"single_arm", "ab"}:
        failed.append("manifest_arm_kind_invalid")
    arms = manifest.get("arms")
    if not isinstance(arms, dict):
        arms = {}
        failed.append("manifest_arms_must_be_object")
    expected_arm_names = ["single"] if arm_kind == "single_arm" else ["A", "B"]
    if set(arms) != set(expected_arm_names):
        failed.append("manifest_arm_set_mismatch")
    source = manifest.get("source")
    native_binding = (
        {
            **source.get("native_binding"),
            "native_path": source.get("native_path"),
        }
        if isinstance(source, dict)
        and isinstance(source.get("native_binding"), dict)
        else None
    )
    arm_contracts: dict[str, dict] = {}
    all_paths: list[str] = []
    for arm_name in expected_arm_names:
        contract = _presentation_artifact_arm_contract(
            arm_name,
            arms.get(arm_name),
            native_binding=native_binding,
        )
        arm_contracts[arm_name] = contract
        failed.extend(contract.get("failed_checks", []))
        all_paths.extend(contract.get("paths", []))
    if arm_kind == "ab" and all(name in arms and isinstance(arms[name], dict) for name in ("A", "B")):
        for key in ("frame_ids", "poses", "profile_id"):
            if arms["A"].get(key) != arms["B"].get(key):
                failed.append(f"ab_{key}_mismatch")
    comparisons = manifest.get("comparisons", [])
    if not isinstance(comparisons, list):
        comparisons = []
        failed.append("manifest_comparisons_must_be_list")
    comparison_rows: dict[str, dict] = {}
    for index, comparison in enumerate(comparisons):
        prefix = f"comparison_{index}"
        if not isinstance(comparison, dict):
            failed.append(f"{prefix}_must_be_object")
            continue
        role = comparison.get("role")
        if role not in PRESENTATION_COMPARISON_ROLES:
            failed.append(f"{prefix}_unrelated_role")
            continue
        if role in comparison_rows:
            failed.append(f"duplicate_comparison_role_{role}")
        comparison_rows[role] = comparison
        path_value = comparison.get("path")
        if not isinstance(path_value, str) or not Path(path_value).is_file():
            failed.append(f"{prefix}_path_not_real_file")
        else:
            path = Path(path_value)
            all_paths.append(str(path.resolve()))
            if not _valid_sha256(comparison.get("sha256")):
                failed.append(f"{prefix}_sha256_invalid")
            elif _sha256_file(path) != comparison.get("sha256"):
                failed.append(f"{prefix}_sha256_mismatch")
            gif_facts, gif_reason = _presentation_gif_facts(path)
            if gif_facts is None:
                failed.append(f"{prefix}_{gif_reason}")
            else:
                if comparison.get("frame_count") != gif_facts["frame_count"]:
                    failed.append(f"{prefix}_frame_count_mismatch")
                if _size_tuple(comparison.get("dimensions")) != gif_facts["dimensions"]:
                    failed.append(f"{prefix}_dimensions_mismatch")
        if comparison.get("frame_ids") != arms.get("A", {}).get("frame_ids"):
            failed.append(f"{prefix}_frame_ids_mismatch")
        if comparison.get("poses") != arms.get("A", {}).get("poses"):
            failed.append(f"{prefix}_poses_mismatch")
        if comparison.get("source_field_sha256") != [
            arms.get("A", {}).get("field_sha256"),
            arms.get("B", {}).get("field_sha256"),
        ]:
            failed.append(f"{prefix}_source_field_sha256_mismatch")
        if comparison.get("profile_id") != arms.get("A", {}).get("profile_id"):
            failed.append(f"{prefix}_profile_id_mismatch")
        if comparison.get("source_arms") != ["A", "B"]:
            failed.append(f"{prefix}_source_arms_mismatch")
        if comparison.get("source_capture_ids") != [
            arms.get("A", {}).get("capture_id"),
            arms.get("B", {}).get("capture_id"),
        ]:
            failed.append(f"{prefix}_source_capture_ids_mismatch")
        if comparison.get("source_modes") != [
            arms.get("A", {}).get("mode_id"),
            arms.get("B", {}).get("mode_id"),
        ]:
            failed.append(f"{prefix}_source_modes_mismatch")
    if arm_kind == "single_arm":
        if comparison_rows:
            failed.append("single_arm_comparisons_forbidden")
    elif set(comparison_rows) != set(PRESENTATION_COMPARISON_ROLES):
        failed.append("ab_comparison_roles_incomplete")
    if len(all_paths) != len(set(all_paths)):
        failed.append("manifest_duplicate_real_paths")
    actual_field_eligible = eligibility == "actual_field" and not failed
    mode_comparison_eligible = actual_field_eligible and all(
        contract.get("mode_comparison_eligible") is True for contract in arm_contracts.values()
    )
    native_artifact_ready = all(
        contract.get("native_artifact_ready") is True
        for contract in arm_contracts.values()
    )
    return {
        "pass": not failed,
        "required": True,
        "reason": "presentation_artifact_manifest_ok" if not failed else "presentation_artifact_manifest_failed",
        "eligibility": eligibility,
        "legacy_diagnostic": eligibility == "legacy_pair_diagnostic",
        "actual_field_eligible": actual_field_eligible,
        "native_artifact_ready": native_artifact_ready,
        "mode_comparison_eligible": mode_comparison_eligible,
        "arm_contracts": arm_contracts,
        "comparison_roles": sorted(comparison_rows),
        "failed_checks": failed,
    }


def _full_grid_receipt_contract(
    summary: dict,
    receipt: dict,
    args: argparse.Namespace,
    *,
    require_gif_families: bool = False,
    require_presentation_artifacts: bool = False,
    capture_path: Path | None = None,
) -> dict:
    """Validate runtime evidence and the named post-run artifact contract."""
    if _effective_analyzer_proof_profile(args) != FULL_GRID_ORACLE_PROFILE:
        return {"pass": True, "reason": "not_full_grid_oracle"}
    pose_contract = _full_grid_pose_contract(args)
    pose_rows = summary.get("exact_pose_sweep_frame_poses")
    capture_schedule = _dict_value(receipt.get("capture_schedule"))
    expected_pose_rows = _int_value(capture_schedule.get("effective_total_frames"), 0)
    if expected_pose_rows <= 0:
        expected_pose_rows = FULL_GRID_REQUIRED_CAPTURE_FRAMES
    pose_rows_valid = (
        isinstance(pose_rows, list)
        and len(pose_rows) == expected_pose_rows
        and len(pose_rows) >= FULL_GRID_REQUIRED_CAPTURE_FRAMES
        and all(
            isinstance(row, dict)
            and row.get("match") is True
            and row.get("camera_yaw_match") is True
            and row.get("camera_pitch_match") is True
            for row in pose_rows
        )
    )
    gif_contract = _full_grid_gif_families_present(summary, receipt)
    presentation_profile_contract = (
        _presentation_profile_receipt_contract(
            receipt,
            args,
            capture_path=capture_path,
        )
        if bool(getattr(args, "presentation_field_capture", False))
        else {
            "pass": True,
            "required": False,
            "reason": "presentation_field_capture_not_requested",
            "failed_checks": [],
        }
    )
    presentation_artifact_contract = _presentation_artifact_manifest_gate(
        summary, receipt
    )
    tilt_shift = receipt.get("tilt_shift_enabled")
    if tilt_shift is None:
        tilt_shift = receipt.get("tilt_shift_on")
    if tilt_shift is None:
        tilt_shift = _dict_value(
            _dict_value(receipt.get("final_render_metadata")).get("feature_state")
        ).get("tilt_shift")
    runtime_checks = {
        "actual_airplane": (
            receipt.get("route_identity") == SCRIPTED_AIRBORNE_AIRPLANE_ROUTE_IDENTITY
            and receipt.get("camera_mode") == "plane_air"
            and _int_value(receipt.get("local_actor_id"), 0) > 0
            and _int_value(receipt.get("airplane_dynamic_face_count"), 0) > 0
            and bool(_dict_value(receipt.get("airplane_before")))
            and bool(_dict_value(receipt.get("airplane_after")))
            and receipt.get("airplane_yaw_turn_measured") is True
        ),
        "declared_positions": pose_contract["pass"],
        "rendered_positions_and_orientation": pose_rows_valid,
        "turn_frames_zero": int(getattr(args, "turn_frames", -1)) == 0,
        "day_cycle_time": _full_grid_runtime_celestial_receipt_accepts(receipt, args),
        "tilt_shift_off": tilt_shift is False,
        "glyph_level": int(getattr(args, "glyph_matrix_cell_px", 0)) == FULL_GRID_REQUIRED_GLYPH_MATRIX_CELL_PX,
        "continuous_recorder_interval": (
            int(capture_schedule.get("effective_active_frames", 0) or 0)
            == FULL_GRID_REQUIRED_CAPTURE_FRAMES
            and int(capture_schedule.get("observed_active_frames", 0) or 0)
            == FULL_GRID_REQUIRED_CAPTURE_FRAMES
            and capture_schedule.get("schedule_complete") is True
            and receipt.get("sparse_capture_drain_pass") is True
            and receipt.get("sparse_capture_pending_after_drain") is False
        ),
        "canvas_provenance_capture_transport": (
            not bool(getattr(args, "canvas_provenance_capture", False))
            or receipt.get("canvas_provenance_capture_ok") is True
        ),
        "presentation_field_capture_transport": (
            not bool(getattr(args, "presentation_field_capture", False))
            or _presentation_field_capture_receipt_accepts(receipt)
        ),
    }
    artifact_checks = {
        "presentation_profile_receipt": presentation_profile_contract["pass"],
        # Kept as a historical diagnostic field for old receipts. It is not the
        # admission gate; four arbitrary paths may be duplicates or unrelated.
        "all_four_gif_families": gif_contract["pass"],
        "named_presentation_manifest": presentation_artifact_contract["pass"],
        "actual_field_artifacts_eligible": presentation_artifact_contract[
            "actual_field_eligible"
        ],
        "native_artifact_ready": presentation_artifact_contract[
            "native_artifact_ready"
        ],
        "mode_comparison_eligible": presentation_artifact_contract[
            "mode_comparison_eligible"
        ],
    }
    failed = [name for name, ok in runtime_checks.items() if not ok]
    if require_gif_families and not artifact_checks["all_four_gif_families"]:
        failed.append("all_four_gif_families")
    if require_presentation_artifacts and not (
        artifact_checks["actual_field_artifacts_eligible"]
        and artifact_checks["presentation_profile_receipt"]
    ):
        failed.append("presentation_artifacts_incomplete")
    # The field sidecars are runtime transport. Named GIF roles are a separate
    # post-processing deliverable and may legitimately be absent immediately
    # after capture; keep their pending status visible without making the
    # capture itself fail or inventing a rasterizer invocation here.
    checks = {**runtime_checks, **artifact_checks}
    return {
        "pass": not failed,
        "reason": "full_grid_receipt_contract_ok" if not failed else "full_grid_receipt_contract_failed",
        "failed_checks": failed,
        "checks": checks,
        "runtime_checks": runtime_checks,
        "artifact_checks": artifact_checks,
        "post_run_artifacts_complete": gif_contract["pass"],
        "post_run_artifact_status": (
            "complete" if gif_contract["pass"] else "pending_gif_generation"
        ),
        "mode": pose_contract["mode"],
        "pose_contract": pose_contract,
        "gif_contract": gif_contract,
        "presentation_profile_contract": presentation_profile_contract,
        "presentation_artifact_contract": presentation_artifact_contract,
        "presentation_artifact_status": (
            "mode_comparison_eligible"
            if artifact_checks["mode_comparison_eligible"]
            else "actual_field_only"
            if artifact_checks["actual_field_artifacts_eligible"]
            else "legacy_or_pending"
        ),
    }


def _canonical_airplane_presentation_receipt_accepts(
    receipt: dict,
    camera_mode: str,
) -> bool:
    """Admit only the actual World-owned 3600x2252 / 113x71 surface."""
    if camera_mode not in ("plane_air", "plane_air_ortho"):
        return True
    if receipt.get("canonical_presentation_requested") is not True:
        return False
    if receipt.get("presentation_surface") != CANONICAL_AIRPLANE_CAPTURE_MODE:
        return False
    if receipt.get("capture_window_mode_requested") != CANONICAL_AIRPLANE_CAPTURE_MODE:
        return False
    contract = receipt.get("canonical_presentation_contract")
    if not isinstance(contract, dict) or contract.get("pass") is not True:
        return False
    expected = contract.get("expected")
    observed = contract.get("observed")
    if not isinstance(expected, dict) or not isinstance(observed, dict):
        return False
    expected_presentation = {
        "w": CANONICAL_AIRPLANE_PRESENTATION_SIZE[0],
        "h": CANONICAL_AIRPLANE_PRESENTATION_SIZE[1],
    }
    expected_ansi = {
        "w": 225 if contract.get("cell_aspect") == "terminal" else CANONICAL_AIRPLANE_ANSI_SIZE[0],
        "h": CANONICAL_AIRPLANE_ANSI_SIZE[1],
    }
    return (
        expected.get("presentation") == expected_presentation
        and expected.get("ansi") == expected_ansi
        and observed.get("viewport_size") == expected_presentation
        and observed.get("pixel_camera_presentation_size") == expected_presentation
        and observed.get("display_window_size") == expected_presentation
        and observed.get("semantic_ansi_size") == expected_ansi
    )


def _capture_window_receipt_accepts(
    receipt: dict,
    args: argparse.Namespace,
) -> bool:
    """Require native fullscreen only for routes that selected that owner."""
    if getattr(args, "probe_owner_isolation", ""):
        capture_window = receipt.get("capture_window", {})
        display = receipt.get("display", {})
        viewport = receipt.get("viewport_size", {})
        ansi = receipt.get("ansi_size", {})
        expected_window = {
            "x": FL4359_OPERATOR_SLAB_WINDOW_SIZE[0],
            "y": FL4359_OPERATOR_SLAB_WINDOW_SIZE[1],
        }
        expected_screen = {
            "x": FL4359_OPERATOR_SLAB_SCREEN_SIZE[0],
            "y": FL4359_OPERATOR_SLAB_SCREEN_SIZE[1],
        }
        expected_ansi = {
            "w": FL4359_OPERATOR_SLAB_ANSI_SIZE[0],
            "h": FL4359_OPERATOR_SLAB_ANSI_SIZE[1],
        }
        expected_viewport = {
            "w": FL4359_OPERATOR_SLAB_VIEWPORT_SIZE[0],
            "h": FL4359_OPERATOR_SLAB_VIEWPORT_SIZE[1],
        }
        return (
            args.godot_window_mode == "windowed"
            and isinstance(capture_window, dict)
            and capture_window.get("actual_fullscreen") is False
            and capture_window.get("actual_mode") == "windowed"
            and capture_window.get("requested_mode") == "windowed"
            and capture_window.get("screen_size") == expected_screen
            and capture_window.get("window_size") == expected_window
            and viewport == expected_viewport
            and isinstance(display, dict)
            and display.get("window_size") == {
                "w": FL4359_OPERATOR_SLAB_WINDOW_SIZE[0],
                "h": FL4359_OPERATOR_SLAB_WINDOW_SIZE[1],
            }
            and ansi == expected_ansi
        )
    if _uses_canonical_airplane_presentation(args):
        # World.gd owns the canonical locked window. The caller's historical
        # fullscreen default must not become a second receipt admission gate.
        return True
    if args.godot_window_mode != "fullscreen":
        return True
    capture_window = receipt.get("capture_window", {})
    return (
        isinstance(capture_window, dict)
        and bool(capture_window.get("actual_fullscreen", False))
    )


def _augment_summary_with_receipt(
    summary_path: Path,
    *,
    receipt: dict,
    godot_rc: int,
    analyzer_rc: int,
    receipt_path: Path,
    capture_path: Path,
    out_dir: Path,
    requested_plane_circle_frames: int,
    effective_plane_circle_frames: int,
    requested_jitter_capture_frame_limit: int,
    effective_jitter_capture_frame_limit: int,
    requested_turn_frames: int,
    effective_turn_frames: int,
    jitter_capture_world_sample_stride: int,
    requested_godot_timeout_seconds: float,
    godot_timeout_seconds: float,
    repo_provenance: dict | None = None,
    run_classification: str = RUN_CLASSIFICATION_MOVEMENT_PROOF,
) -> dict:
    if not summary_path.exists():
        return {}
    summary = json.loads(summary_path.read_text())
    wrapper_provenance = dict(repo_provenance or {})
    receipt_subset = {key: receipt.get(key) for key in RECEIPT_SUMMARY_FIELDS if key in receipt}
    final_render_metadata = _dict_value(receipt.get("final_render_metadata"))
    final_signature_attr = _dict_value(summary.get("final_signature_churn_attribution"))
    surface_identity_split_totals = _dict_value(summary.get("surface_identity_split_totals"))
    surface_identity_split_surface_totals = _dict_value(summary.get("surface_identity_split_surface_totals"))
    selected_surface_identity_field_counts = _dict_value(final_signature_attr.get("selected_surface_identity_field_counts"))
    selected_surface_identity_field_surface_counts = _dict_value(final_signature_attr.get("selected_surface_identity_field_surface_counts"))
    final_signature_field_counts = _dict_value(final_signature_attr.get("field_counts"))
    final_signature_field_surface_counts = _dict_value(final_signature_attr.get("field_surface_counts"))
    terrain_detail_field_counts = _dict_value(final_signature_attr.get("terrain_detail_field_counts"))
    terrain_detail_field_surface_counts = _dict_value(final_signature_attr.get("terrain_detail_field_surface_counts"))
    snap_phase_surface_counts = _dict_value(final_signature_attr.get("snap_phase_surface_counts"))
    final_cell_signature_churn_cells = _int_value(summary.get("final_cell_signature_churn_cells"))
    snap_phase_changed_cells = _int_value(final_signature_attr.get("snap_phase_changed_cells"))
    between_snap_instability_cells = max(0, final_cell_signature_churn_cells - snap_phase_changed_cells)
    frame_time_grid_crossing_fields = _present_fields(
        summary,
        receipt,
        final_render_metadata,
        keys=FRAME_TIME_GRID_CROSSING_FIELDS,
    )
    record_churn_buckets = {
        "max_selected_surface_identity_churn_pct": summary.get("max_selected_surface_identity_churn_pct"),
        "selected_surface_identity_churn_cells": surface_identity_split_totals.get("selected_surface_identity_churn_cells"),
        "selected_surface_identity_surface_counts": _dict_value(surface_identity_split_surface_totals.get("selected_surface_identity_churn")),
        "selected_surface_identity_field_counts": selected_surface_identity_field_counts,
        "selected_surface_identity_field_surface_counts": selected_surface_identity_field_surface_counts,
    }
    final_product_churn_buckets = {
        "final_product_identity_churn_cells": surface_identity_split_totals.get("final_product_identity_churn_cells"),
        "final_product_identity_surface_counts": _dict_value(surface_identity_split_surface_totals.get("final_product_identity_churn")),
        "final_product_cell_churn_cells": surface_identity_split_totals.get("final_product_cell_churn_cells"),
        "final_product_cell_surface_counts": _dict_value(surface_identity_split_surface_totals.get("final_product_cell_churn")),
    }
    final_cell_signature_churn_buckets = {
        "final_cell_signature_churn_cells": final_cell_signature_churn_cells,
        "final_cell_signature_churn_pct": summary.get("final_cell_signature_churn_pct"),
        "field_counts": final_signature_field_counts,
        "field_surface_counts": final_signature_field_surface_counts,
        "terrain_detail_field_counts": terrain_detail_field_counts,
        "terrain_detail_field_surface_counts": terrain_detail_field_surface_counts,
        "snap_phase_changed_cells": snap_phase_changed_cells,
        "snap_phase_surface_counts": snap_phase_surface_counts,
    }
    snap_window_instability = {
        "available": "snap_phase_changed_cells" in final_signature_attr,
        "in_snap_window_cells": snap_phase_changed_cells,
        "between_snap_window_cells": between_snap_instability_cells,
        "in_snap_window_surface_counts": snap_phase_surface_counts,
        "final_cell_signature_churn_cells": final_cell_signature_churn_cells,
    }
    frame_time_grid_crossing_metadata = {
        "available": bool(frame_time_grid_crossing_fields),
        "fields": frame_time_grid_crossing_fields,
    }
    surface_buckets = {
        "surface_identity_split_surface_totals": surface_identity_split_surface_totals,
        "final_signature_field_surface_counts": final_signature_field_surface_counts,
        "selected_surface_identity_field_surface_counts": selected_surface_identity_field_surface_counts,
        "snap_phase_surface_counts": snap_phase_surface_counts,
    }
    original_shimmer_attribution = _dict_value(summary.get("original_shimmer_attribution"))
    raw_excluded_lane_counts = {
        **_dict_value(summary.get("raw_excluded_counts")),
        **_dict_value(original_shimmer_attribution.get("raw_excluded_counts")),
        "authored_animation_excluded_cells": original_shimmer_attribution.get(
            "authored_animation_excluded_cells"
        ),
        "authored_animation_excluded_class_counts": _dict_value(
            original_shimmer_attribution.get("authored_animation_excluded_class_counts")
        ),
    }
    gates = _dict_value(summary.get("gates"))
    if summary.get("proof_profile") == "airplane_temporal_v1":
        capture_schedule = _dict_value(receipt.get("capture_schedule"))
        schedule_pass = bool(
            receipt.get("pass", False)
            and capture_schedule.get("schedule_complete", False)
            and receipt.get("sparse_capture_drain_pass", False)
            and not bool(receipt.get("sparse_capture_pending_after_drain", False))
            and int(receipt.get("sparse_capture_failed_request_count", 0) or 0) == 0
            and int(receipt.get("sparse_capture_join_invalid_rows", 0) or 0) == 0
            and bool(receipt.get("product_time_pin_valid", False))
            and receipt.get("sparse_lifecycle_facts_valid") is True
        )
        gates["evidence_sparse_capture_schedule_complete"] = {
            "pass": schedule_pass,
            "capture_schedule": capture_schedule,
            "drain_pass": receipt.get("sparse_capture_drain_pass"),
            "pending_after_drain": receipt.get("sparse_capture_pending_after_drain"),
            "failed_request_count": receipt.get("sparse_capture_failed_request_count"),
            "join_invalid_rows": receipt.get("sparse_capture_join_invalid_rows"),
            "product_time_pin_valid": receipt.get("product_time_pin_valid"),
            "sparse_lifecycle_facts_valid": receipt.get("sparse_lifecycle_facts_valid"),
        }
        if not schedule_pass:
            gates.setdefault("failed_gates", []).append("evidence_sparse_capture_schedule_complete")
            gates.setdefault("profile_failed_gates", []).append("evidence_sparse_capture_schedule_complete")
            gates.setdefault("blocking_failed_gates", []).append("evidence_sparse_capture_schedule_complete")
            gates["all_pass"] = False
            gates["all_non_expected_pass"] = False
    stationary_calibration = bool(receipt.get("calibration_only", False))
    proof_pass = (
        _stationary_calibration_summary_accepts(summary)
        if stationary_calibration
        else _analysis_gates_accept_proof(summary)
    )
    proof_failed_gates = gates.get("blocking_failed_gates", gates.get("failed_gates", []))
    diagnostic_only = run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
    route_contract_metadata = {
        "kind": (
            "stationary_terminal_reprojection_calibration"
            if stationary_calibration
            else "bounded_static_visual_diagnostic"
            if diagnostic_only
            else "scripted_airborne_airplane_movement"
        ),
        "run_classification": run_classification,
        "proof_classification": (
            STATIONARY_TERMINAL_REPROJECTION_CALIBRATION
            if stationary_calibration
            else DIAGNOSTIC_PROOF_CLASSIFICATION
            if diagnostic_only
            else RUN_CLASSIFICATION_MOVEMENT_PROOF
        ),
        "proof_eligible": not diagnostic_only and not stationary_calibration,
        "route_identity": receipt.get("route_identity"),
        "route_kind": receipt.get("route_kind"),
        "movement_starts_on_active_frame_zero": receipt.get("first_intent_frame") == 0,
        "first_intent_frame": receipt.get("first_intent_frame"),
        "first_measured_motion_frame": receipt.get("first_measured_motion_frame"),
        "measured_motion_immediate": bool(receipt.get("measured_motion_immediate", False)),
        "camera_turn_starts_on_active_frame_zero": receipt.get("first_turn_intent_frame") == 0,
        "first_turn_intent_frame": receipt.get("first_turn_intent_frame"),
        "first_active_camera_turn_pressed": receipt.get("first_active_camera_turn_pressed"),
        "first_active_flight_intent": receipt.get("first_active_flight_intent"),
        "first_active_throttle_command": receipt.get("first_active_throttle_command"),
        "first_active_pitch_command": receipt.get("first_active_pitch_command"),
        "first_active_roll_command": receipt.get("first_active_roll_command"),
        "first_active_yaw_command": receipt.get("first_active_yaw_command"),
        "requested_turn_frames": requested_turn_frames,
        "effective_turn_frames": effective_turn_frames,
        "active_turn_frames": receipt.get("active_turn_frames"),
        "turn_intent_frames": receipt.get("turn_intent_frames"),
        "camera_yaw_turn_measured": receipt.get("camera_yaw_turn_measured"),
        "camera_yaw_turn_delta": receipt.get("camera_yaw_turn_delta"),
        "airplane_yaw_turn_measured": receipt.get("airplane_yaw_turn_measured"),
        "airplane_yaw_turn_delta": receipt.get("airplane_yaw_turn_delta"),
        "first_active_frame_delta": receipt.get("first_active_frame_delta"),
        "movement_fail_fast_frame": receipt.get("movement_fail_fast_frame"),
        "fail_fast_reason": receipt.get("fail_fast_reason"),
        "prearmed_motion_frames": receipt.get("prearmed_motion_frames"),
        "airplane_route_receipt_ok": (
            bool(receipt.get("stationary_pose_contract", {}).get("pass", False))
            if stationary_calibration
            else _airplane_route_receipt_accepts_proof(
                receipt, str(receipt.get("camera_mode", ""))
            )
        ),
        "diagnostic_only": diagnostic_only,
        "calibration_only": stationary_calibration,
    }
    summary["admissible"] = (
        godot_rc == 0
        and bool(receipt.get("pass", False))
        and receipt_path.exists()
        and capture_path.exists()
        and int(summary.get("frame_count", 0) or 0) > 0
        and bool(summary.get("surface_identity_split_diagnostics_present", False))
        and bool(summary.get("required_field_coverage", {}).get("all_present", False))
        and bool(summary.get("product_anchor_provenance_complete", False))
        and (
            summary.get("proof_profile") != "airplane_temporal_v1"
            or receipt.get("sparse_lifecycle_facts_valid") is True
        )
        and "motion_compensated_final_cell_instability_pct" in summary
    )
    summary["run_classification"] = run_classification
    summary["proof_classification"] = (
        STATIONARY_TERMINAL_REPROJECTION_CALIBRATION
        if stationary_calibration
        else DIAGNOSTIC_PROOF_CLASSIFICATION
        if run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
        else RUN_CLASSIFICATION_MOVEMENT_PROOF
    )
    summary["calibration_only"] = stationary_calibration
    summary["diagnostic_only"] = (
        run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
        and not stationary_calibration
    )
    summary["pass"] = proof_pass
    summary["proof_pass"] = proof_pass
    summary["proof_reason"] = "analyzer_gates_passed" if proof_pass else "analyzer_gates_failed"
    summary["proof_failed_gates"] = proof_failed_gates
    summary["diagnostic_status"] = "diagnostic_gates_passed" if proof_pass else "diagnostic_gates_failed"
    summary["acceptance_claimed"] = False
    summary["closure_claimed"] = False
    if stationary_calibration:
        summary["proof"] = STATIONARY_TERMINAL_REPROJECTION_CALIBRATION
        summary["acceptance_claimed"] = False
        summary["closure_claimed"] = False
        summary["proof_reason"] = (
            "stationary_calibration_transport_valid"
            if proof_pass
            else "stationary_calibration_transport_invalid"
        )
        summary["diagnostic_status"] = (
            "stationary_calibration_valid"
            if proof_pass
            else "stationary_calibration_invalid"
        )
    elif run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY:
        # A diagnostic may collect the same observations as a proof run, but
        # classification—not observed gate values—keeps it inadmissible.
        analysis_skipped = summary.get("analysis_skipped") is True
        summary["proof"] = (
            "bounded_static_visual_diagnostic"
            if analysis_skipped
            else "bounded_movement_diagnostic"
        )
        summary["admissible"] = False
        summary["pass"] = False
        summary["proof_pass"] = False
        summary["proof_reason"] = (
            "diagnostic_only_analysis_skipped"
            if analysis_skipped
            else "diagnostic_only_analysis_observed_non_proof"
        )
        summary["diagnostic_status"] = (
            "diagnostic_gates_observed_without_blocking_failure"
            if proof_pass
            else "diagnostic_gates_observed_with_blocking_failure"
        )
    summary["process_timed_out"] = godot_rc == 124
    summary["receipt_pass"] = bool(receipt.get("pass", False))
    summary["receipt_reason"] = receipt.get("reason")
    summary["runtime_movement_pass"] = receipt.get("runtime_movement_pass")
    summary["runtime_movement_reason"] = receipt.get("runtime_movement_reason")
    summary["analyzer_rc"] = receipt.get("analyzer_rc", analyzer_rc)
    summary["analyzer_pass"] = receipt.get("analyzer_pass", analyzer_rc == 0)
    summary["analyzer_failure_reason"] = receipt.get("analyzer_failure_reason")
    summary["presentation_profile"] = wrapper_provenance.get("presentation_profile", "")
    summary["wrapper_git_head"] = wrapper_provenance.get("git_head", "")
    summary["git_head"] = wrapper_provenance.get("git_head", "")
    summary["git_dirty"] = bool(wrapper_provenance.get("git_dirty", False))
    summary["git_dirty_count"] = int(wrapper_provenance.get("git_dirty_count", 0))
    summary["git_dirty_paths"] = list(wrapper_provenance.get("git_dirty_paths", []))
    summary["provenance"] = {
        "source": "scripts/run_fl4359_headed_movement.py",
        "wrapper": wrapper_provenance,
        "runtime": {
            "godot_rc": godot_rc,
            "analyzer_rc": analyzer_rc,
            "receipt_path": str(receipt_path),
            "capture_path": str(capture_path),
        },
    }
    summary["completion"] = {
        "godot_rc": godot_rc,
        "analyzer_rc": analyzer_rc,
        "git_head": wrapper_provenance.get("git_head", ""),
        "git_dirty": bool(wrapper_provenance.get("git_dirty", False)),
        "git_dirty_count": int(wrapper_provenance.get("git_dirty_count", 0)),
        "git_dirty_paths": list(wrapper_provenance.get("git_dirty_paths", [])),
        "process_timed_out": godot_rc == 124,
        "receipt_present": receipt_path.exists(),
        "capture_present": capture_path.exists(),
        "receipt_path": str(receipt_path),
        "capture_path": str(capture_path),
        "artifacts_dir": str(out_dir),
        "requested_plane_circle_frames": requested_plane_circle_frames,
        "effective_plane_circle_frames": effective_plane_circle_frames,
        "requested_jitter_capture_frame_limit": requested_jitter_capture_frame_limit,
        "effective_jitter_capture_frame_limit": effective_jitter_capture_frame_limit,
        "requested_turn_frames": requested_turn_frames,
        "effective_turn_frames": effective_turn_frames,
        "jitter_capture_world_sample_stride": jitter_capture_world_sample_stride,
        "requested_godot_timeout_seconds": requested_godot_timeout_seconds,
        "effective_godot_timeout_seconds": godot_timeout_seconds,
    }
    summary["bounded_capture_metadata"] = {
        "route_mode": "bounded_consecutive_frame_window",
        "process_timed_out": godot_rc == 124,
        "timeout_policy": "finite_subprocess_timeout",
        "requested_godot_timeout_seconds": requested_godot_timeout_seconds,
        "effective_godot_timeout_seconds": godot_timeout_seconds,
        "godot_timeout_seconds": godot_timeout_seconds,
        "godot_timeout_disabled": False,
        "requested_plane_circle_frames": requested_plane_circle_frames,
        "effective_plane_circle_frames": effective_plane_circle_frames,
        "requested_jitter_capture_frame_limit": requested_jitter_capture_frame_limit,
        "effective_jitter_capture_frame_limit": effective_jitter_capture_frame_limit,
        "requested_turn_frames": requested_turn_frames,
        "effective_turn_frames": effective_turn_frames,
        "jitter_capture_world_sample_stride": jitter_capture_world_sample_stride,
    }
    summary["route_contract_metadata"] = route_contract_metadata
    summary["jitter_metadata"] = {
        "definition": CURRENT_METRIC_DEFINITIONS["jitter"],
        "camera_delta": receipt.get("camera_delta"),
        "movement_delta": receipt.get("movement_delta"),
        "max_snap_px_delta": summary.get("max_snap_px_delta"),
        "avg_snap_px_delta": summary.get("avg_snap_px_delta"),
        "snap_sign_flip_rate": summary.get("snap_sign_flip_rate"),
        "moving_frame_pairs": summary.get("moving_frame_pairs"),
        "first_measured_motion_frame": receipt.get("first_measured_motion_frame"),
        "frame_time_grid_crossing_metadata": frame_time_grid_crossing_metadata,
    }
    summary["turn_metadata"] = {
        "active_turn_frames": receipt.get("active_turn_frames"),
        "turn_intent_frames": receipt.get("turn_intent_frames"),
        "first_turn_intent_frame": receipt.get("first_turn_intent_frame"),
        "camera_yaw_before": receipt.get("camera_yaw_before"),
        "camera_yaw_after_turn": receipt.get("camera_yaw_after_turn"),
        "camera_yaw_after": receipt.get("camera_yaw_after"),
        "camera_yaw_turn_delta": receipt.get("camera_yaw_turn_delta"),
        "camera_yaw_total_delta": receipt.get("camera_yaw_total_delta"),
        "camera_yaw_turn_measured": receipt.get("camera_yaw_turn_measured"),
        "camera_yaw_post_turn_drift": receipt.get("camera_yaw_post_turn_drift"),
        "camera_yaw_post_turn_settle_ok": receipt.get("camera_yaw_post_turn_settle_ok"),
        "airplane_yaw_before": receipt.get("airplane_yaw_before"),
        "airplane_yaw_after_turn": receipt.get("airplane_yaw_after_turn"),
        "airplane_yaw_after": receipt.get("airplane_yaw_after"),
        "airplane_yaw_turn_delta": receipt.get("airplane_yaw_turn_delta"),
        "airplane_yaw_total_delta": receipt.get("airplane_yaw_total_delta"),
        "airplane_yaw_turn_measured": receipt.get("airplane_yaw_turn_measured"),
    }
    summary["shimmer_metadata"] = {
        "definition": CURRENT_METRIC_DEFINITIONS["shimmer"],
        "max_pair_jitter_pct": summary.get("max_pair_jitter_pct"),
        "legacy_max_pair_jitter_pct": summary.get("max_pair_jitter_pct"),
        "motion_compensated_final_cell_instability_pct": summary.get(
            "motion_compensated_final_cell_instability_pct"
        ),
        "final_cell_signature_churn_buckets": final_cell_signature_churn_buckets,
        "snap_window_instability": snap_window_instability,
        "original_shimmer_attribution": original_shimmer_attribution,
        "raw_excluded_lane_counts": raw_excluded_lane_counts,
        "required_field_coverage": summary.get("required_field_coverage", {}),
        "product_anchor_provenance": summary.get("product_anchor_provenance", {}),
        "product_anchor_provenance_complete": bool(summary.get("product_anchor_provenance_complete", False)),
    }
    summary["bounce_metadata"] = {
        "definition": CURRENT_METRIC_DEFINITIONS["bounce"],
        "max_selected_surface_identity_churn_pct": summary.get("max_selected_surface_identity_churn_pct"),
        "max_post_surface_bounce_pct": summary.get("max_post_surface_bounce_pct"),
        "max_final_product_identity_churn_pct": summary.get("max_final_product_identity_churn_pct"),
        "record_churn_buckets": record_churn_buckets,
        "final_product_churn_buckets": final_product_churn_buckets,
        "surface_buckets": surface_buckets,
        "surface_identity_bounce_attribution": summary.get("surface_identity_bounce_attribution", {}),
        "surface_identity_split_totals": summary.get("surface_identity_split_totals", {}),
    }
    summary["measured_metadata"] = {
        "current_metric_definitions": dict(CURRENT_METRIC_DEFINITIONS),
        "jitter_evidence": summary["jitter_metadata"],
        "shimmer_evidence": summary["shimmer_metadata"],
        "bounce_evidence": summary["bounce_metadata"],
        "active_turn_frames": receipt.get("active_turn_frames"),
        "camera_yaw_turn_delta": receipt.get("camera_yaw_turn_delta"),
        "camera_yaw_turn_measured": receipt.get("camera_yaw_turn_measured"),
        "camera_yaw_post_turn_drift": receipt.get("camera_yaw_post_turn_drift"),
        "camera_yaw_post_turn_settle_ok": receipt.get("camera_yaw_post_turn_settle_ok"),
        "airplane_yaw_turn_delta": receipt.get("airplane_yaw_turn_delta"),
        "airplane_yaw_turn_measured": receipt.get("airplane_yaw_turn_measured"),
        "first_active_frame_delta": receipt.get("first_active_frame_delta"),
        "first_active_camera_turn_pressed": receipt.get("first_active_camera_turn_pressed"),
        "first_active_flight_intent": receipt.get("first_active_flight_intent"),
        "first_active_throttle_command": receipt.get("first_active_throttle_command"),
        "first_active_pitch_command": receipt.get("first_active_pitch_command"),
        "first_active_roll_command": receipt.get("first_active_roll_command"),
        "first_active_yaw_command": receipt.get("first_active_yaw_command"),
        "route_identity": receipt.get("route_identity"),
        "movement_delta": receipt.get("movement_delta"),
        "camera_delta": receipt.get("camera_delta"),
        "record_churn_buckets": record_churn_buckets,
        "final_product_churn_buckets": final_product_churn_buckets,
        "final_cell_signature_churn_buckets": final_cell_signature_churn_buckets,
        "surface_buckets": surface_buckets,
        "raw_excluded_lane_counts": raw_excluded_lane_counts,
        "snap_window_instability": snap_window_instability,
        "frame_time_grid_crossing_metadata": frame_time_grid_crossing_metadata,
        "artifact_path": str(out_dir),
        "process_timed_out": godot_rc == 124,
        "timeout_policy": "finite_subprocess_timeout",
        "requested_godot_timeout_seconds": requested_godot_timeout_seconds,
        "effective_godot_timeout_seconds": godot_timeout_seconds,
        "route_contract_metadata": route_contract_metadata,
    }
    summary["receipt_summary"] = receipt_subset
    for key, value in receipt_subset.items():
        if key in ("pass", "reason"):
            continue
        summary.setdefault(key, value)
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def _full_grid_capture_prerequisites(args: argparse.Namespace) -> dict:
    required = (
        getattr(args, "analyzer_proof_profile", "") == "full_grid_temporal_oracle_v1"
        and getattr(args, "run_classification", RUN_CLASSIFICATION_MOVEMENT_PROOF)
        == RUN_CLASSIFICATION_MOVEMENT_PROOF
    )
    checks = {
        "presentation_field_capture_requested": bool(getattr(args, "presentation_field_capture", False)),
        "canvas_provenance_capture_requested": bool(getattr(args, "canvas_provenance_capture", False)),
        "capture_peak_budget_declared": getattr(args, "presentation_field_capture_budget_bytes", 0)
        >= PRESENTATION_FIELD_REQUIRED_PEAK_BYTES,
    }
    return {
        "required": required,
        "ok": not required or all(checks.values()),
        "checks": checks,
        "failed_checks": [key for key, passed in checks.items() if required and not passed],
        "engine_capabilities": "not_checked_configuration_only",
    }


def _canvas_engine_capabilities(
    godot_binary: str,
    project: Path,
    driver: Path,
    requested_resolution: tuple[int, int] | list[int] | None = None,
) -> dict:
    """Reject an incompatible binary before starting the game or server."""
    normalized_resolution = None
    if requested_resolution is not None:
        if (
            not isinstance(requested_resolution, (tuple, list))
            or len(requested_resolution) != 2
            or any(type(value) is not int or value <= 0 for value in requested_resolution)
        ):
            return {
                "ok": False,
                "reason": "canvas_requested_resolution_invalid",
                "game_started": False,
                "requested_resolution": requested_resolution,
            }
        normalized_resolution = [int(requested_resolution[0]), int(requested_resolution[1])]
    command = [godot_binary, "--headless", "--path", str(project), "--script", str(driver),
               "--", "--fl4359-engine-capabilities-only"]
    result = {"ok": False, "command": command, "game_started": False}
    if normalized_resolution is not None:
        result["requested_resolution"] = normalized_resolution
    try:
        executable = Path(shutil.which(godot_binary) or godot_binary).resolve()
        result["executable"] = {"path": str(executable), "sha256": _sha256_file(executable)}
        process = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        result["reason"] = "engine_capability_probe_failed"
        result["error"] = str(error)
        return result
    output = process.stdout or ""
    result.update(returncode=process.returncode, output=output)
    try:
        if _sha256_file(executable) != result["executable"]["sha256"]:
            result["reason"] = "engine_binary_changed_during_capability_probe"
            return result
    except OSError:
        result["reason"] = "engine_binary_missing_after_capability_probe"
        return result
    prefix = "FL4359_ENGINE_CAPABILITIES "
    lines = [line[len(prefix):] for line in output.splitlines() if line.startswith(prefix)]
    if process.returncode != 0 or len(lines) != 1 or any(
        marker in output for marker in ("SCRIPT ERROR:", "Parse Error:", "ERROR:")
    ):
        result["reason"] = "engine_capability_probe_invalid"
        return result
    try:
        witness = json.loads(lines[0])
    except (TypeError, ValueError):
        result["reason"] = "engine_capability_probe_invalid_json"
        return result
    if not isinstance(witness, dict) or witness.get("schema") != "fl4359.engine_capabilities.v1" \
            or witness.get("game_started") is not False:
        result["reason"] = "engine_capability_probe_identity_invalid"
        return result
    methods = witness.get("methods")
    methods = methods if isinstance(methods, dict) else {}
    missing = [method for method in (
        "canvas_provenance_arm", "canvas_provenance_readback",
        "canvas_provenance_capture_bounds", "get_render_frame_number",
    ) if methods.get(method) is not True]
    result.update(witness=witness, missing_methods=missing)
    if missing:
        result.update(ok=False, reason="canvas_engine_api_missing")
        return result

    bounds = witness.get("canvas_provenance_bounds")
    result["canvas_provenance_bounds"] = bounds
    if not isinstance(bounds, dict) or bounds.get("schema") != "fl4691.canvas_provenance_bounds.v1":
        result.update(ok=False, reason="canvas_engine_bounds_invalid")
        return result
    max_width = bounds.get("max_target_width")
    max_height = bounds.get("max_target_height")
    max_pixels = bounds.get("max_target_pixels")
    if (
        type(max_width) is not int
        or type(max_height) is not int
        or max_width <= 0
        or max_height <= 0
        or ("max_target_pixels" in bounds and (type(max_pixels) is not int or max_pixels <= 0))
    ):
        result.update(ok=False, reason="canvas_engine_bounds_invalid")
        return result
    if normalized_resolution is not None:
        exceeded = []
        if normalized_resolution[0] > max_width:
            exceeded.append("width")
        if normalized_resolution[1] > max_height:
            exceeded.append("height")
        requested_pixels = normalized_resolution[0] * normalized_resolution[1]
        if max_pixels is not None and requested_pixels > max_pixels:
            exceeded.append("pixels")
        resolution_check = {
            "requested": normalized_resolution,
            "maximum": [max_width, max_height],
            "exceeded": exceeded,
            "pass": not exceeded,
        }
        if max_pixels is not None:
            resolution_check.update(
                requested_pixels=requested_pixels, maximum_pixels=max_pixels
            )
        result["resolution_check"] = resolution_check
        if exceeded:
            result.update(ok=False, reason="canvas_engine_requested_resolution_exceeds_bounds")
            return result
    result.update(ok=True, reason="canvas_engine_capabilities_present")
    return result


def _run(preserver: DeferredRunReceiptPreserver, provenance: "RunSourceProvenance | None" = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=2589)
    parser.add_argument("--out", default="")
    parser.add_argument("--godot-timeout", type=float, default=DEFAULT_GODOT_TIMEOUT_SECONDS, help=NO_GODOT_TIMEOUT_HELP)
    parser.add_argument("--server-boot-timeout", type=float, default=30.0)
    parser.add_argument("--move-frames", type=int, default=120)
    parser.add_argument("--settle-frames", type=int, default=2)
    parser.add_argument("--after-frames", type=int, default=2)
    parser.add_argument("--plane-circle-frames", type=int, default=DEFAULT_AIRPLANE_MOVEMENT_FRAMES)
    parser.add_argument(
        "--airplane-metadata-frame-cap",
        type=int,
        default=DEFAULT_AIRPLANE_METADATA_FRAME_CAP,
        help=(
            "Positive cap for plane_air completion-grade bounded metadata captures."
        ),
    )
    parser.add_argument("--plane-circle-rate", type=float, default=0.26)
    parser.add_argument("--plane-throttle", type=float, default=0.37)
    parser.add_argument("--plane-settle-throttle", type=float, default=0.37)
    parser.add_argument("--plane-pitch-base", type=float, default=0.02)
    parser.add_argument("--plane-pitch-amp", type=float, default=0.03)
    parser.add_argument("--plane-roll-base", type=float, default=-0.12)
    parser.add_argument("--plane-roll-amp", type=float, default=0.04)
    parser.add_argument(
        "--plane-fire-frames",
        type=int,
        default=0,
        help=(
            "Leading flight frames that hold the gun-fire intent, for night tracer-light "
            "captures. Default 0 leaves every existing route unarmed; the driver owns the "
            "on-window and no gate or analyzer reads this value."
        ),
    )
    parser.add_argument(
        "--request-final-dump",
        action="store_true",
        help=(
            "Have the driver issue one final_render_cell_dump from its live per-frame "
            "loop after settling, instead of an operator pressing X in the game window. "
            "Default off; no gate, analyzer, or preflight reads this value."
        ),
    )
    parser.add_argument("--turn-frames", type=int, default=DEFAULT_INITIAL_TURN_FRAMES)
    parser.add_argument("--turn-action", choices=["rotate_left", "rotate_right"], default="rotate_left")
    parser.add_argument("--airborne-spawn-throttle", type=float, default=0.37)
    parser.add_argument("--max-jitter-pct", type=float, default=5.0)
    parser.add_argument(
        "--analyzer-proof-profile",
        choices=("auto", "same_frame_oracle_v1", "full_grid_temporal_oracle_v1"),
        default="auto",
        help=(
            "Select the analyzer gate surface without changing the headed route. "
            "auto preserves the canonical movement profile; same_frame_oracle_v1 "
            "requires the same-current-build b86 direct-ray join; "
            "full_grid_temporal_oracle_v1 additionally requires every subcell "
            "on contiguous frames from one moving process."
        ),
    )
    parser.add_argument(
        "--fl4359-moving-full-grid-capture",
        action="store_true",
        help=(
            "Legacy alias for --full-grid-contract-mode exact_translation. The "
            "contract forbids camera rotation and requires five supplied camera "
            "positions with one fixed yaw and pitch."
        ),
    )
    parser.add_argument(
        "--full-grid-contract-mode",
        choices=FULL_GRID_CONTRACT_MODES,
        default="",
        help=(
            "Full-grid proof contract mode. stationary requires five equal "
            "positions. exact_translation requires five supplied distinct "
            "positions, fixed yaw/pitch, and turn_frames=0. camera_turn records "
            "the first five frames of the declared physical turn-input window."
        ),
    )
    parser.add_argument(
        "--fl4359-terminal-reprojection-stationary-calibration",
        action="store_true",
        help=(
            "Run the existing plane_air/full-grid route without any input or "
            "movement and emit a five-frame terminal reprojection calibration "
            "receipt. This arm cannot claim movement or renderer acceptance."
        ),
    )
    parser.add_argument("--camera-mode", choices=["ortho", "perspective", "plane_air", "plane_air_ortho"], default="plane_air")
    parser.add_argument(
        "--godot-project-dir", default=str(GODOT_PROJECT),
        help=(
            "The Godot project Godot executes. Default: this checkout's live "
            "godot_project, the same state a manual launch runs (FL-4896). "
            "f9_replay rewrites it for --replay-from-snapshot; any other tree "
            "is refused, because the run's source provenance snapshots this "
            "checkout and could not prove what an unregistered copy executed."
        ),
    )
    parser.add_argument("--colyseus-dir", default=str(COLYSEUS_DIR))
    parser.add_argument(
        "--driver-script",
        default=str(ROOT / "scripts" / "adhoc" / "fl4359_external_movement_driver.gd"),
    )
    parser.add_argument("--godot-window-mode", choices=["windowed", "fullscreen"], default="fullscreen")
    parser.add_argument(
        "--allow-windowed-diagnostic",
        action="store_true",
        help="Allow a windowed airplane run for local diagnostics; never use this for operator signoff evidence.",
    )
    parser.add_argument("--resolution", type=_parse_resolution, default=(1280, 800))
    parser.add_argument(
        "--glyph-matrix-cell-px",
        type=int,
        choices=(12, 16, 20, 24, 32, 48, 64, 96),
        default=32,
        help=(
            "PixelCamera cell size for FL-4359 continuous-colour, shimmer, and jitter "
            "diagnostics. The operator-designated 32px canonical capture surface is "
            "the diagnostic default; pass 20 explicitly for the canonical gameplay grid."
        ),
    )
    parser.add_argument("--presentation-mode", choices=("FF", "FC", "CF", "CC", "FF_SHARED"), default="FF")
    parser.add_argument("--cell-aspect", choices=("terminal", "square"), default="terminal",
                        help="Native cell aspect. Terminal uses half-width columns and two-column full-width glyphs.")
    parser.add_argument(
        "--presentation-profile",
        default="",
        help=(
            "Native presentation identity for an exact-pose arm. The current "
            "terminal route derives fl4359_native_terminal16x32_perspective_v1 when "
            "this is omitted; square cells select the square32 profile. Unknown profiles fail before launch. This selects "
            "identity metadata only and does not toggle a Godot menu setting."
        ),
    )
    parser.add_argument(
        "--presentation-profile-manifest",
        default="",
        help=(
            "Path to one strict fl4359.presentation_profile.v1 expected-profile "
            "manifest. It declares dimensions, projection, atlas content hashes, "
            "and orthographic scale; it never selects a Godot menu setting."
        ),
    )
    parser.add_argument("--canvas-provenance-capture", action="store_true",
                        help="Capture ordered Canvas fragments on the instrumented engine; requires native field capture.")
    parser.add_argument(
        "--presentation-field-capture",
        action="store_true",
        help=(
            "Explicitly request the producer's actual final-paint FG/BG field "
            "sidecar for this run. The producer must also receive a declared "
            "byte budget; this is never implicit."
        ),
    )
    parser.add_argument("--presentation-observer-only", action="store_true",
                        help="Run the current native-field/mask/fragment measurements after capture; diagnostic-only, no legacy b64 acceptance claim.")
    parser.add_argument(
        "--presentation-field-capture-budget-bytes",
        type=int,
        default=0,
        help=(
            "Maximum bytes reserved for --presentation-field-capture. The current "
            f"producer requires at least {PRESENTATION_FIELD_REQUIRED_PEAK_BYTES} "
            "bytes for its bounded five-frame peak."
        ),
    )
    parser.add_argument("--exact-player-position", default="")
    parser.add_argument("--exact-camera-position", default="")
    parser.add_argument("--exact-camera-yaw", default="")
    parser.add_argument("--exact-camera-pitch", default="")
    parser.add_argument("--exact-camera-projection", choices=["ortho", "perspective", ""], default="")
    parser.add_argument("--exact-camera-fov", default="")
    parser.add_argument("--exact-camera-size", default="")
    parser.add_argument(
        "--exact-pose-sweep",
        action="store_true",
        help=(
            "Run the DEFAULT full-grid capture route with a pinned per-frame camera "
            "pose instead of physical movement input. Captured sequence frame i "
            "renders from --exact-camera-position + --exact-pose-sweep-delta * i, so "
            "two arms of an A/B replay identical camera choreography. Diagnostic "
            "machinery: it makes no renderer claim."
        ),
    )
    parser.add_argument(
        "--exact-pose-sweep-delta",
        default=DEFAULT_EXACT_POSE_SWEEP_DELTA,
        help=(
            "World-space `x,y,z` advance added to the exact camera position for each "
            "captured sequence frame of --exact-pose-sweep. Ignored when "
            "--exact-pose-sweep-positions declares an explicit path."
        ),
    )
    parser.add_argument(
        "--exact-pose-sweep-positions",
        default="",
        help=(
            "Explicit semicolon-separated `x,y,z` world camera positions, one per "
            'captured sequence frame (e.g. "447,48,-336;448,48,-337;448,48,-338"). '
            "When present this OVERRIDES --exact-pose-sweep-delta, because an "
            "operator-designated pose may advance by non-uniform per-frame steps that "
            "no constant delta can replay. A captured sequence frame past the last "
            "entry holds that final pose. A malformed, non-finite, or over-long list "
            "is rejected in preflight."
        ),
    )
    parser.add_argument(
        "--frame-pngs",
        dest="frame_pngs",
        action="store_true",
        help=(
            "Save one presented-resolution PNG per declared sweep sequence index "
            "into <out>/frames (frame_00.png..frame_04.png) and stitch them into "
            "<out>/sweep_5frames.gif. The full-grid temporal oracle owns every "
            "render callback inside its bounded request window, so this route "
            "refuses --analyzer-proof-profile full_grid_temporal_oracle_v1. "
            "Diagnostic machinery: it makes no renderer claim."
        ),
    )
    parser.add_argument("--ghost-replay-segment", choices=["", "tower_strafe", "close_breakdown"], default="")
    parser.add_argument(
        "--ghost-replay-validate-only",
        action="store_true",
        help="Replay the recorded F3 pose envelope without any compositor readback.",
    )
    parser.add_argument(
        "--onset-provenance",
        action="store_true",
        help="Enable bounded, dump-only cyan-blob onset provenance for a ghost replay.",
    )
    parser.add_argument(
        "--epoch-depth-sign-trace",
        action="store_true",
        help="Record only same-identity EYE-minus-GRID biased-depth signs during the exact ghost replay.",
    )
    parser.add_argument("--player-spawn-x", default="")
    parser.add_argument("--player-spawn-z", default="")
    parser.add_argument("--single-frame", action="store_true")
    parser.add_argument("--single-frame-label", default="00_before")
    parser.add_argument(
        "--probe-owner-isolation",
        choices=[
            "",
            "current_only",
            "current_previous",
            "current_terrain",
            "current_water",
            "current_water_finite",
            "current_static",
        ],
        default="",
        help=(
            "Proof-only frozen-pose probe matrix arm. It removes PREVIOUS for "
            "CURRENT-only modes and optionally submits one retained capture family."
        ),
    )
    parser.add_argument(
        "--fl4216-temporal-capture",
        action="store_true",
        help=(
            "Capture bounded same-process FL-4216 final-render/runtime-truth "
            "frames from an exact fullscreen P1 pose."
        ),
    )
    parser.add_argument(
        "--fl4216-temporal-capture-interval-seconds",
        type=float,
        default=DEFAULT_FL4216_TEMPORAL_CAPTURE_INTERVAL_SECONDS,
        help="Minimum monotonic interval between temporal captures; must be >= 1.5s.",
    )
    parser.add_argument(
        "--fl4216-temporal-capture-count",
        type=int,
        default=DEFAULT_FL4216_TEMPORAL_CAPTURE_COUNT,
        help="Number of same-process temporal captures; must be >= 2.",
    )
    # FL-4216/4273 DAY-NIGHT CYCLE: pin the day-cycle time-of-day for a
    # deterministic single-frame capture. Float in [0,1): 0=midnight, 0.25=sunrise,
    # 0.5=noon, 0.75=sunset. Omit / negative => effect auto-advances from wall clock.
    parser.add_argument("--day-cycle-time", type=float, default=None)
    parser.add_argument("--weather", choices=("clear", "light_rain", "rain", "heavy_rain", "snow"), default="clear",
                        help="Pin the existing runtime weather controller for the selected capture.")
    parser.add_argument(
        "--fl4216-stability-wait-seconds",
        type=float,
        default=None,
        help=(
            "Settled FL-4216 exact-pose proof wait in monotonic seconds; "
            "must be between 1.5 and 2.0 and is republished on every frame."
        ),
    )
    parser.add_argument("--capture-side", choices=["current", "baseline"], default="current")
    parser.add_argument("--skip-jitter-analysis", action="store_true")
    parser.add_argument(
        "--run-classification",
        choices=[RUN_CLASSIFICATION_MOVEMENT_PROOF, RUN_CLASSIFICATION_DIAGNOSTIC_ONLY],
        default=RUN_CLASSIFICATION_MOVEMENT_PROOF,
        help=(
            "Classify this invocation as movement proof or an explicitly "
            "diagnostic-only capture. Skipped analysis is never proof."
        ),
    )
    parser.add_argument(
        "--clean-play-no-capture",
        action="store_true",
        help=(
            "Measure the server-tick airplane route without PNG capture, GPU "
            "readback, jitter rows, and secondary-effect diagnostics."
        ),
    )
    parser.add_argument(
        "--record-flight-video",
        action="store_true",
        help=(
            "Record the existing bounded airplane movement and camera-turn route "
            "through World's supervised F9 recorder. This is a diagnostic-only "
            "continuous visual arm and requires skipped sparse analysis."
        ),
    )
    parser.add_argument(
        "--proof-id",
        default="",
        help=(
            "FL-4895: config/godot_proof_pose_registry.json id of the proof this "
            "run executes. Refused when unknown. Forwarded as ASCIICKER_PROOF_ID; "
            "the recorder writes it to identity.proof_id and the driver to its receipt."
        ),
    )
    parser.add_argument(
        "--recording-title",
        default="",
        help=(
            "Operator title embedded in the F9 recording identity: it names "
            "the .mov and frames directory and rides the container title. "
            "Forwarded to the game through ASCIICKER_SCREEN_RECORDING_TITLE, "
            "the same environment door as the other recorder options."
        ),
    )
    parser.add_argument(
        "--fl4671-lerp-receipt-diagnostic",
        action="store_true",
        help=(
            "Run the mounted airplane route as an FL-4671 diagnostic that "
            "collects FL4359_LERP_RECEIPT rows while retired jitter analysis stays skipped."
        ),
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Validate and print the selected headed movement proof command without starting Colyseus or Godot.",
    )
    parser.add_argument(
        "--jitter-capture-frame-start",
        type=int,
        default=0,
        help="First compositor source frame to record when jitter capture is enabled.",
    )
    parser.add_argument(
        "--jitter-capture-frame-limit",
        type=int,
        default=DEFAULT_JITTER_CAPTURE_FRAME_LIMIT,
        help="Maximum jitter-capture frames for completion-grade headed runs; 0 means unbounded.",
    )
    parser.add_argument(
        "--jitter-capture-world-sample-stride",
        type=int,
        default=DEFAULT_JITTER_CAPTURE_WORLD_SAMPLE_STRIDE,
        help="World-anchor hash stride for completion-grade jitter rows; 1 records every cell.",
    )
    parser.add_argument(
        "--jitter-capture-drain-deadline-frames",
        type=int,
        default=120,
        help="Bounded asynchronous sparse-recorder drain deadline after the final settle frame.",
    )
    parser.add_argument(
        "--product-time",
        type=float,
        default=None,
        help="Pinned FL-4431 Product clock used for movement-proof joins.",
    )
    parser.add_argument(
        "--allow-large-jitter-capture",
        action="store_true",
        help=(
            "Allow dense airplane JSONL capture for a named missing-marker diagnostic. "
            "Completion-grade proof defaults stay capped to avoid disk bloat."
        ),
    )
    parser.add_argument(
        "--keep-forever",
        "--pin-run",
        dest="keep_forever",
        action="store_true",
        help=(
            "Explicitly pin this run's raw artifacts and bypass a hard .run "
            "budget. The override is recorded in the receipt provenance."
        ),
    )
    parser.add_argument(
        "--expected-red-gate",
        action="append",
        default=[],
        help="Named analyzer gate allowed to fail for diagnostic runs; repeated flag allowed.",
    )
    parser.add_argument(
        "--sunday-summary-json",
        default="",
        help="Sunday-path analyzer summary for relative final-cell instability gating.",
    )
    parser.add_argument(
        "--extra-user-arg",
        action="append",
        default=[],
        help="Additional Godot user argument appended after -- for visual proof wrappers.",
    )
    parser.add_argument(
        "--force-baseline-features",
        default="",
    )
    parser.add_argument(
        "--probe-size",
        type=int,
        choices=[0, 128, 256, 384],
        default=0,
        help="Proof-only cubemap face size for owner isolation or a diagnostic full-scene five-pose presentation observer.",
    )
    parser.add_argument(
        "--fl4359-historical-r6-replay",
        action="store_true",
        help=(
            "Opt into the exact historical R6 native fullscreen presentation tuple. "
            "All replay identity fields are required to match; the default canonical "
            "32px airplane route remains unchanged."
        ),
    )
    f9_replay.add_arguments(parser)
    f9_replay.add_recording_arguments(parser)
    parser.add_argument(
        "--replicate-reason",
        default="",
        help=(
            "FL-4516: required to relaunch unchanged runtime source after a launch "
            "that did not pass (log rejection, or missing/nonzero RED-YELLOW crawl "
            "proof). Recorded in .run/true_ff_launch_guard/launches.jsonl."
        ),
    )
    args = parser.parse_args()
    if args.proof_id:
        sys.path.insert(0, str(ROOT / "scripts" / "recording"))
        from f9_before_after import registry_ids  # noqa: E402
        if args.proof_id not in registry_ids():
            parser.error(f"--proof-id {args.proof_id!r} is not an id in config/godot_proof_pose_registry.json")
    # FL-4873: --replay-bundle replays one F9 recording through this same
    # runner, server, driver, recorder and renderer. The bundle supplies the
    # conditions; plan() refuses mismatched selections and source.
    replay_plan = None
    if args.replay_bundle:
        if not args.out:
            args.out = str(ROOT / ".run" / ("fl4873_replay_" + time.strftime("%Y%m%d-%H%M%S")))
        replay_out = Path(args.out) if Path(args.out).is_absolute() else (ROOT / args.out).resolve()
        try:
            replay_plan = f9_replay.plan(args, replay_out)
        except f9_replay.ReplayRefused as exc:
            print(f"[fl4873-replay] REFUSED {exc}", flush=True)
            return 2
        print("[fl4873-replay] plan: " + json.dumps({k: v for k, v in replay_plan.items() if k != "session"}), flush=True)
    else:
        # FL-4873: --f10-view selects the F10 view before F9 recording starts
        # (the driver applies --fl4359-debug-view before its recording toggle).
        args.extra_user_arg = list(args.extra_user_arg or []) + f9_replay.f10_view_user_args(args)
    if args.presentation_observer_only and (not args.presentation_field_capture
            or args.run_classification != RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
            or args.analyzer_proof_profile != "full_grid_temporal_oracle_v1"):
        parser.error("--presentation-observer-only requires diagnostic-only full-grid native-field capture")
    # FL-4907: select before World installs a driver, not only after startup
    # in the proof driver. An explicit shared run must not boot standalone FF.
    try:
        args.extra_user_arg = _presentation_user_args(args.presentation_mode, args.extra_user_arg)
    except ValueError as exc:
        parser.error(str(exc))
    if args.canvas_provenance_capture and args.clean_play_no_capture:
        parser.error("--canvas-provenance-capture cannot use --clean-play-no-capture")
    if args.canvas_provenance_capture and not args.presentation_field_capture:
        parser.error("--canvas-provenance-capture requires --presentation-field-capture")
    if args.presentation_field_capture_budget_bytes < 0:
        parser.error("presentation_field_capture_budget_bytes_must_be_nonnegative")
    if args.presentation_field_capture_budget_bytes > 0 and not args.presentation_field_capture:
        parser.error(
            "presentation_field_capture_budget_requires_presentation_field_capture"
        )
    if (
        args.presentation_field_capture
        and args.presentation_field_capture_budget_bytes
        < PRESENTATION_FIELD_REQUIRED_PEAK_BYTES
    ):
        parser.error(
            "presentation_field_capture_budget_below_producer_peak:"
            f"{PRESENTATION_FIELD_REQUIRED_PEAK_BYTES}"
        )
    true_ff_route_error = _true_ff_temporal_route_error(args)
    if true_ff_route_error:
        print(f"[fl4359-headed] ERROR: {true_ff_route_error}", flush=True)
        return 2
    full_grid_contract_mode = _full_grid_contract_mode(args)
    if _moving_full_grid_capture_enabled(args) and _stationary_calibration_enabled(args):
        parser.error(
            "fl4359_moving_full_grid_capture_conflicts_with_stationary_calibration"
        )
    if args.analyzer_proof_profile == "full_grid_temporal_oracle_v1" and not full_grid_contract_mode:
        print(
            "[fl4359-headed] ERROR: full-grid contract requires "
            "--full-grid-contract-mode stationary|exact_translation|camera_turn",
            flush=True,
        )
        return 2
    if full_grid_contract_mode:
        full_grid_pose_contract = _full_grid_pose_contract(args)
        full_grid_errors: list[str] = []
        camera_turn_contract = (
            full_grid_contract_mode == FULL_GRID_CONTRACT_MODE_CAMERA_TURN
        )
        expected_camera_mode = "perspective" if camera_turn_contract else "plane_air"
        if args.camera_mode != expected_camera_mode:
            full_grid_errors.append(
                "camera_mode_must_be_perspective_for_camera_turn"
                if camera_turn_contract
                else "camera_mode_must_be_plane_air"
            )
        if args.exact_camera_projection != "perspective":
            full_grid_errors.append("exact_camera_projection_must_be_perspective")
        if not camera_turn_contract and not _uses_canonical_airplane_presentation(args):
            full_grid_errors.append("canonical_32px_presentation_required")
        if camera_turn_contract and tuple(args.resolution) != GROUND_TURN_PRESENTATION_SIZE:
            full_grid_errors.append("camera_turn_presentation_must_equal_3840x2160")
        profile_id = _requested_presentation_profile_id(args)
        profile_spec, profile_parse_reason = _presentation_profile_parse(args)
        if not profile_id or profile_spec is None:
            full_grid_errors.append(
                profile_parse_reason or "presentation_profile_manifest_invalid"
            )
        else:
            expected_atlas = profile_spec.atlas_identity()
            if not all(
                isinstance(source, dict)
                and isinstance(source.get("sha256"), str)
                and bool(source.get("sha256"))
                for source in expected_atlas.get("sources", [])
            ):
                full_grid_errors.append("presentation_profile_atlas_sources_unavailable")
            if profile_spec.projection != args.exact_camera_projection:
                full_grid_errors.append("presentation_profile_projection_mismatch")
            if profile_spec.native_cell_size != _requested_native_cell_size(args):
                full_grid_errors.append("presentation_profile_native_cell_size_mismatch")
            if not _profile_application_supported(args, profile_spec):
                full_grid_errors.append("presentation_profile_application_not_implemented")
        if args.analyzer_proof_profile != "full_grid_temporal_oracle_v1":
            full_grid_errors.append("full_grid_temporal_oracle_v1_required")
        if int(args.jitter_capture_frame_limit) != FULL_GRID_REQUIRED_CAPTURE_FRAMES:
            full_grid_errors.append("jitter_capture_frame_limit_must_equal_5")
        if int(args.jitter_capture_world_sample_stride) != FULL_GRID_REQUIRED_WORLD_STRIDE:
            full_grid_errors.append("jitter_capture_world_sample_stride_must_equal_1")
        if int(args.glyph_matrix_cell_px) != FULL_GRID_REQUIRED_GLYPH_MATRIX_CELL_PX:
            full_grid_errors.append("glyph_matrix_cell_px_must_equal_32")
        if not full_grid_pose_contract["pass"]:
            full_grid_errors.append(full_grid_pose_contract["reason"])
        if full_grid_contract_mode == FULL_GRID_CONTRACT_MODE_EXACT_TRANSLATION and int(args.turn_frames) != 0:
            full_grid_errors.append("turn_frames_must_equal_0_for_exact_translation")
        if args.skip_jitter_analysis:
            full_grid_errors.append("skip_jitter_analysis_forbidden")
        if args.clean_play_no_capture:
            full_grid_errors.append("clean_play_no_capture_forbidden")
        if any(
            value == "--fl4359-press-b-birds"
            or value.startswith("--fl4359-press-b-birds=")
            for value in args.extra_user_arg
        ):
            full_grid_errors.append("secondary_birds_input_forbidden")
        if any("tilt-shift" in value or "tilt_shift" in value for value in args.extra_user_arg):
            full_grid_errors.append("tilt_shift_must_be_off")
        if args.product_time is None or not math.isfinite(float(args.product_time)):
            full_grid_errors.append("product_time_pin_required")
        if not _full_grid_day_cycle_time_valid(args):
            full_grid_errors.append("day_cycle_time_pin_required_equal_0.5")
        if FULL_GRID_ORACLE_REQUIRED_FEATURE not in [
            name
            for name in (part.strip() for part in _forced_baseline_features(args).split(","))
            if name
        ]:
            full_grid_errors.append("day_night_cycle_feature_must_be_forced")
        if FULL_GRID_ORACLE_REQUIRED_DISABLED_FEATURE not in [
            name
            for name in (part.strip() for part in _forced_baseline_features(args).split(","))
            if name
        ]:
            full_grid_errors.append("tilt_shift_off_feature_must_be_forced")
        if full_grid_errors:
            print(
                "[fl4359-headed] ERROR: full-grid contract identity rejected: "
                + ",".join(full_grid_errors),
                flush=True,
            )
            return 2
    elif not _full_grid_day_cycle_time_valid(args):
        print(
            "[fl4359-headed] ERROR: full_grid_temporal_oracle_v1 requires "
            "--day-cycle-time 0.5",
            flush=True,
        )
        return 2
    if args.probe_owner_isolation:
        isolation_identity_valid = (
            args.single_frame
            and args.run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
            and args.skip_jitter_analysis
            and bool(args.exact_player_position)
            and bool(args.exact_camera_position)
            and args.exact_camera_projection == "perspective"
            and args.product_time is not None
            and args.day_cycle_time is not None
            and 0.0 <= float(args.day_cycle_time) < 1.0
            and not args.clean_play_no_capture
        )
        if not isolation_identity_valid:
            print(
                "[fl4359-headed] ERROR: --probe-owner-isolation requires one "
                "diagnostic-only single-frame perspective run with skipped movement "
                "analysis, exact player/camera positions, pinned Product time, and "
                "pinned day-cycle time",
                flush=True,
            )
            return 2
    elif args.probe_size != 0 and not _presentation_density_diagnostic_enabled(args):
        print(
            "[fl4359-headed] ERROR: --probe-size requires --probe-owner-isolation "
            "or a diagnostic-only five-pose full-grid presentation observer",
            flush=True,
        )
        return 2
    historical_r6_reason = _historical_r6_replay_identity_reason(args)
    if historical_r6_reason:
        print(
            "[fl4359-headed] ERROR: historical R6 replay identity rejected: "
            f"{historical_r6_reason}",
            flush=True,
        )
        return 2
    if not math.isfinite(float(args.godot_timeout)):
        print(
            "[fl4359-headed] ERROR: --godot-timeout must be finite",
            flush=True,
        )
        return 2
    # FL-4359: the canonical full-grid route drains CPU data after its video
    # window. It may record that window, but cannot claim clean-play timing.
    if args.record_flight_video and not full_grid_contract_mode and not (
        args.run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
        and args.skip_jitter_analysis
        and int(args.jitter_capture_frame_limit) == 0
    ):
        print(
            "[fl4359-headed] ERROR: --record-flight-video requires "
            "--run-classification diagnostic-only --skip-jitter-analysis "
            "--jitter-capture-frame-limit 0",
            flush=True,
        )
        return 2
    classification_reason = _run_classification_contract_reason(
        args.run_classification,
        bool(args.skip_jitter_analysis),
        bool(args.fl4216_temporal_capture),
    )
    if classification_reason:
        print(f"[fl4359-headed] ERROR: {classification_reason}", flush=True)
        return 2
    if args.fl4216_temporal_capture:
        if args.single_frame:
            print(
                "[fl4359-headed] ERROR: FL-4216 temporal capture cannot be combined with --single-frame",
                flush=True,
            )
            return 2
        if args.fl4216_temporal_capture_interval_seconds < MIN_FL4216_TEMPORAL_CAPTURE_INTERVAL_SECONDS:
            print(
                "[fl4359-headed] ERROR: --fl4216-temporal-capture-interval-seconds must be >= 1.5",
                flush=True,
            )
            return 2
        if args.fl4216_temporal_capture_count < DEFAULT_FL4216_TEMPORAL_CAPTURE_COUNT:
            print(
                "[fl4359-headed] ERROR: --fl4216-temporal-capture-count must be >= 2",
                flush=True,
            )
            return 2
        if args.godot_timeout < MIN_FL4216_TEMPORAL_GODOT_TIMEOUT_SECONDS:
            print(
                "[fl4359-headed] ERROR: FL-4216 temporal capture requires "
                f"--godot-timeout >= {MIN_FL4216_TEMPORAL_GODOT_TIMEOUT_SECONDS:.0f}s",
                flush=True,
            )
            return 2
    if args.fl4216_stability_wait_seconds is not None:
        if not 1.5 <= args.fl4216_stability_wait_seconds <= 2.0:
            print(
                "[fl4359-headed] ERROR: --fl4216-stability-wait-seconds must be between 1.5 and 2.0",
                flush=True,
            )
            return 2
        args.extra_user_arg.append(
            "--fl4216-stability-wait-seconds=%.3f" % args.fl4216_stability_wait_seconds
        )
    exact_single_frame_proof = (
        args.single_frame
        and bool(args.exact_player_position or args.exact_camera_position)
        and "--single-frame-cell-dump" in args.extra_user_arg
    )
    exact_temporal_proof = bool(args.fl4216_temporal_capture)
    if (exact_single_frame_proof or exact_temporal_proof) and args.fl4216_stability_wait_seconds is None:
        print(
            "[fl4359-headed] ERROR: exact FL-4216 capture requires "
            "--fl4216-stability-wait-seconds=1.5..2.0",
            flush=True,
        )
        return 2
    if getattr(args, "frame_pngs", False):
        # The driver comment at fl4359_external_movement_driver.gd owns the
        # reason: saving a PNG inside the full-grid oracle's bounded request
        # window creates an unowned sequence -1 request and severs the dispatch
        # stream. The two routes are therefore mutually exclusive, and refusing
        # here is cheaper than discovering it after a headed launch.
        if args.analyzer_proof_profile == FULL_GRID_ORACLE_PROFILE:
            print(
                "[fl4359-headed] ERROR: --frame-pngs cannot run with "
                "--analyzer-proof-profile full_grid_temporal_oracle_v1: the "
                "full-grid oracle owns every render callback in its capture "
                "window, so no per-frame PNG can be saved there. Drop the "
                "profile (or drop --frame-pngs).",
                flush=True,
            )
            return 2
        if not getattr(args, "exact_pose_sweep", False):
            print(
                "[fl4359-headed] ERROR: --frame-pngs requires "
                "--exact-pose-sweep; the per-frame PNGs are indexed by the "
                "declared sweep sequence, which no other route advances.",
                flush=True,
            )
            return 2
        # `--fl4359-clean-play-no-capture` is deliberately NOT forwarded here. It
        # is the "disable every driver-side capture owner" switch: it clears
        # `_capture_path` AND suppresses the very `_save_screenshot` owner this
        # route reuses, and the sweep preflight's `default_capture_route_selected`
        # check rejects it outright. The supported alternative is this route's own
        # off-oracle gate: the exhaustive full-grid capture is never armed, so the
        # bounded default capture writer stays on its historical stride.
    if args.clean_play_no_capture:
        clean_arguments = ["--fl4359-clean-play-no-capture", *args.extra_user_arg]
        if args.single_frame:
            clean_arguments.append("--fl4408-single-frame=true")
        if (
            args.ghost_replay_segment
            or args.onset_provenance
            or args.epoch_depth_sign_trace
        ):
            clean_arguments.append("--capture-owned-ghost-replay-diagnostic")
        if not _clean_play_no_capture_args_are_isolated(clean_arguments):
            print(
                "[fl4359-headed] ERROR: clean-play timing rejects every "
                "capture, readback, dump, and secondary-effect argument",
                flush=True,
            )
            return 2
    if args.ghost_replay_segment == "tower_strafe":
        # The operator recording is a grounded true-perspective route.  Do not
        # let the airplane default replace its spawn before the driver starts.
        args.camera_mode = "perspective"
        args.player_spawn_x = GHOST_TOWER_SPAWN_X
        args.player_spawn_z = GHOST_TOWER_SPAWN_Z
    exact_pose_capture = _has_exact_pose_arguments(args)
    if args.fl4216_temporal_capture and not exact_pose_capture:
        print(
            "[fl4359-headed] ERROR: FL-4216 temporal capture requires exact P1 pose arguments",
            flush=True,
        )
        return 2
    bounds_reason = None if exact_pose_capture else _invalid_airplane_proof_bounds_reason(args)
    if bounds_reason:
        print(
            "[fl4359-headed] ERROR: airplane proof captures must be bounded for completion evidence: "
            f"{bounds_reason}; use a separate diagnostic artifact for long visual investigation",
            flush=True,
        )
        return 2
    effective_plane_circle_frames, effective_jitter_capture_frame_limit = _airplane_metadata_frame_window(args)
    effective_turn_frames = _effective_airplane_turn_frames(args, effective_plane_circle_frames)
    route_contract_reason = None if exact_pose_capture else _invalid_airplane_route_contract_reason(args, effective_plane_circle_frames)
    if route_contract_reason:
        print(
            "[fl4359-headed] ERROR: airplane proof route must start active movement on frame 0 "
            f"and include a camera-turn segment: {route_contract_reason}",
            flush=True,
        )
        return 2
    noisy_arg = _has_noisy_completion_arg(args.extra_user_arg)
    if noisy_arg and not args.allow_windowed_diagnostic:
        print(
            "[fl4359-headed] ERROR: noisy probe diagnostics are disabled for completion runs: "
            f"{noisy_arg}; use --allow-windowed-diagnostic only for local diagnostics",
            flush=True,
        )
        return 2
    godot_project_dir = _selected_godot_project_dir(args)
    true_ff_text_flips: dict | None = None
    unregistered_tree = _unregistered_project_tree(godot_project_dir, replay_plan)
    if unregistered_tree:
        print(f"[fl4359-headed] ERROR: {unregistered_tree}", flush=True)
        return 2
    colyseus_dir = Path(args.colyseus_dir)
    if not colyseus_dir.is_absolute():
        colyseus_dir = (ROOT / colyseus_dir).resolve()
    driver_script = Path(args.driver_script) if args.driver_script else None
    if driver_script is not None and not driver_script.is_absolute():
        driver_script = (ROOT / driver_script).resolve()
    canonical_airplane_presentation = _uses_canonical_airplane_presentation(args)
    if (
        args.camera_mode in ("plane_air", "plane_air_ortho")
        and not canonical_airplane_presentation
        and args.godot_window_mode != "fullscreen"
        and not args.allow_windowed_diagnostic
    ):
        print(
            "[fl4359-headed] ERROR: airplane proof-front capture must be fullscreen; "
            "pass --allow-windowed-diagnostic only for non-signoff local diagnostics",
            flush=True,
        )
        return 2

    out_dir = Path(args.out) if args.out else _default_output_dir(args)
    if not out_dir.is_absolute():
        out_dir = (ROOT / out_dir).resolve()
    # FL-4516: Metal gate plus no relaunch of unchanged source after a launch
    # without a passing RED/YELLOW crawl proof. Enforced here, not in prose.
    receipt_path = out_dir / "receipt.json"
    preserver.configure(receipt_path)
    capture_path = out_dir / "jitter_capture.jsonl"
    analysis_path = out_dir / "analysis.txt"
    summary_path = out_dir / "analysis_summary.json"
    defect_map_path = out_dir / "full_grid_defect_map.jsonl"
    server_log = out_dir / "colyseus_server.log"
    godot_log = out_dir / "godot_client.log"
    try:
        run_budget = check_run_budget(ROOT, out_dir, keep_forever=args.keep_forever)
    except (OSError, ValueError) as exc:
        print(f"[fl4359-headed] ERROR: run budget check failed: {exc}", flush=True)
        return 2
    if not run_budget.allowed:
        print(
            "[fl4359-headed] ERROR: refusing new raw-artifact run: "
            f"{run_budget.reason}; usage={run_budget.usage_bytes} "
            f"hard={run_budget.hard_bytes}. Prune disposable artifacts or "
            "pass --keep-forever explicitly.",
            flush=True,
        )
        return 2
    godot_timeout = _godot_subprocess_timeout(args.godot_timeout)
    repo_provenance = _repo_provenance(preflight_only=args.preflight_only)
    repo_provenance["presentation_profile"] = _presentation_profile(args)
    repo_provenance["run_budget"] = run_budget.as_dict()
    source_identity_scopes = [godot_project_dir, colyseus_dir, Path(__file__)]
    if driver_script is not None:
        source_identity_scopes.append(driver_script)
    if replay_plan is not None and replay_plan.get("materialized_source"):
        # FL-4873 review P7: Godot runs a snapshot materialized outside the
        # repository; its identity is the verified snapshot, not this checkout.
        launch_source_identity = _materialized_source_identity(replay_plan["materialized_source"])
    else:
        launch_source_identity = _runtime_source_identity(source_identity_scopes)
    if launch_source_identity.get("valid") is not True:
        print("[fl4359-headed] ERROR: executed source identity unavailable: "
              + str(launch_source_identity.get("reason", "unknown")), flush=True)
        return 2
    cmd = _build_godot_command(
        args,
        godot_project_dir=godot_project_dir,
        driver_script=driver_script,
        out_dir=out_dir,
        receipt_path=receipt_path,
        godot_log=godot_log,
        capture_path=capture_path,
        effective_plane_circle_frames=effective_plane_circle_frames,
        effective_turn_frames=effective_turn_frames,
        effective_jitter_capture_frame_limit=effective_jitter_capture_frame_limit,
        build_head=str(repo_provenance.get("git_head", "")),
        build_dirty=bool(repo_provenance.get("git_dirty", False)),
        build_worktree_diff_sha256=str(
            repo_provenance.get("worktree_diff_sha256", "")
        ),
    )
    if getattr(args, "exact_pose_sweep", False):
        # The pinned sweep is exact-pose AND default-route, which neither existing
        # check-set describes. It gets its own equal-rigor check-set; the branches
        # below stay exactly as they were for every other route.
        preflight = _exact_pose_sweep_preflight(
            args,
            effective_jitter_capture_frame_limit=effective_jitter_capture_frame_limit,
            godot_timeout_seconds=godot_timeout,
        )
    elif exact_pose_capture:
        preflight = _exact_pose_capture_preflight(
            args,
            godot_timeout_seconds=godot_timeout,
            temporal_capture=bool(args.fl4216_temporal_capture),
        )
    elif args.camera_mode in ("perspective", "ortho"):
        preflight = _grounded_route_preflight(
            args,
            effective_turn_frames=effective_turn_frames,
            effective_jitter_capture_frame_limit=effective_jitter_capture_frame_limit,
            godot_timeout_seconds=godot_timeout,
        )
    else:
        preflight = _airplane_route_preflight(
            args,
            effective_plane_circle_frames=effective_plane_circle_frames,
            effective_turn_frames=effective_turn_frames,
            effective_jitter_capture_frame_limit=effective_jitter_capture_frame_limit,
            godot_timeout_seconds=godot_timeout,
        )
    capture_prerequisites = _full_grid_capture_prerequisites(args)
    preflight["capture_prerequisites"] = capture_prerequisites
    if not capture_prerequisites["ok"]:
        preflight["ok"] = False
        preflight.setdefault("failed_checks", []).extend(capture_prerequisites["failed_checks"])
    preflight = _apply_provenance_preflight_gate(preflight, repo_provenance)
    print(f"[fl4359-headed] runner_command: {_runner_command()}", flush=True)
    print(f"[fl4359-headed] godot_command: {_shell_command(cmd)}", flush=True)
    # FL-4359 arm propagation proof, evaluated against the argv actually built
    # rather than against the parsed arguments. The A/B is defined by exactly one
    # flag differing between two runs, so a flag silently dropped between the CLI
    # and the Godot argv would produce two identical runs reported as an A/B.
    # Fail closed: a requested arm that is missing from the argv aborts before
    # any launch instead of yielding an unlabelled duplicate of the baseline.
    requested_arm_flags = _fl4359_arm_flags(args)
    missing_arm_flags = [flag for flag in requested_arm_flags if flag not in cmd]
    arm_flag_conflicts = _fl4359_arm_flag_conflicts(
        [flag for flag in FL4359_ARM_OPT_IN_FLAGS if flag in cmd]
    )
    print(
        "[fl4359-headed] fl4359_arm_flags: "
        + json.dumps(
            {
                "requested": requested_arm_flags,
                "present_in_godot_argv": [
                    flag for flag in requested_arm_flags if flag in cmd
                ],
                "missing_from_godot_argv": missing_arm_flags,
                "mutually_exclusive_conflicts": arm_flag_conflicts,
                "witness_scope": (
                    "launch-side only: proves the flag reached the Godot argv this "
                    "runner built, NOT that the client bound the arm or that the "
                    "shader specialization constant moved"
                ),
            }
        ),
        flush=True,
    )
    if missing_arm_flags:
        print(
            "[fl4359-headed] ERROR: FL-4359 arm flags requested but absent from the "
            "Godot argv: " + ",".join(missing_arm_flags),
            flush=True,
        )
        return 2
    if arm_flag_conflicts:
        # Fail closed BEFORE launch. The client would refuse this argv anyway;
        # aborting here keeps the failure readable and costs no GPU time.
        for conflict in arm_flag_conflicts:
            print(
                "[fl4359-headed] ERROR: FL-4359 mutually exclusive arm flags in one "
                "argv: " + ",".join(conflict["armed"]) + " -- " + conflict["reason"],
                flush=True,
            )
        return 2
    print("[fl4359-headed] route_preflight:", json.dumps(preflight, indent=2), flush=True)
    if args.fl4671_lerp_receipt_diagnostic and not preflight.get("ok"):
        print(
            "[fl4671] ERROR: mounted interpolation diagnostic preflight failed; launch skipped",
            flush=True,
        )
        return 2
    if args.preflight_only:
        status = "SELF_CHECK_PASS" if preflight.get("ok") else "SELF_CHECK_FAIL"
        print(f"[fl4359-headed] {status} launch_skipped=true artifacts={out_dir}", flush=True)
        return 0 if preflight.get("ok") else 2

    if not capture_prerequisites["ok"]:
        print("[fl4359-headed] ERROR: full-grid proof capture prerequisites missing: "
              + ",".join(capture_prerequisites["failed_checks"]), flush=True)
        return 2
    if args.canvas_provenance_capture:
        engine_capabilities = _canvas_engine_capabilities(
            GODOT_BIN,
            godot_project_dir,
            driver_script,
            tuple(args.resolution),
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "engine_capabilities.json").write_text(json.dumps(engine_capabilities, indent=2) + "\n")
        if not engine_capabilities["ok"]:
            print("[fl4359-headed] ERROR: " + engine_capabilities["reason"]
                  + "; game and server launch skipped", flush=True)
            return 2
        repo_provenance["engine_capabilities"] = engine_capabilities

    # FL-4359 pinned sweep: refuse to launch when the declared choreography is not
    # fully specified, because such a run cannot produce the A/B identity it exists
    # to produce. Placed after --preflight-only so that reporting contract is intact.
    if getattr(args, "exact_pose_sweep", False) and not preflight.get("ok"):
        print(
            "[fl4359-headed] ERROR: pinned exact-pose sweep identity rejected: "
            + ",".join(preflight.get("failed_checks", [])),
            flush=True,
        )
        return 2

    context_ok, context_message = _context_preflight(args)
    print(f"[fl4359-headed] {context_message}", flush=True)
    if not context_ok:
        return 2

    admission = _launch_guard.admit("scripts/run_fl4359_headed_movement.py", out_dir,
                                    args.replicate_reason, project_dir=godot_project_dir,
                                    server_dir=colyseus_dir,
                                    presentation_mode=args.presentation_mode)
    if admission != 0:
        return admission
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in (receipt_path, capture_path, analysis_path, summary_path):
        if path.exists():
            path.unlink()
    output_start_state = _artifact_tree_state(out_dir)
    # FL-4873/FL-4888: preserve the executed source before any process starts.
    # A run whose source cannot be preserved cannot be replayed, so it does
    # not start (the launcher's exit 7 contract).
    if provenance is not None:
        if replay_plan is not None:
            # f9_replay.plan archived the selection from the bytes it used;
            # end-of-run preservation re-uses THAT archive, never the live
            # session.json the viewer may have rewritten since (review P1).
            _register_replay_input(provenance, replay_plan)
        if not provenance.start(receipt_path=receipt_path, pathspecs=_runner_source_pathspecs(driver_script),
                                godot_log=godot_log, godot_project_dir=godot_project_dir):
            print("[fl4359-headed] ERROR: runtime source snapshot not preserved: "
                  + str(provenance.start_state.get("reason", "unknown")), flush=True)
            return 7
        print(f"[fl4359-headed] run identity {provenance.run_identity.get('label')} "
              f"snapshot={provenance.start_state.get('snapshot')}", flush=True)

    if _port_open(args.port):
        print(f"[fl4359-headed] ERROR: port {args.port} already in use", flush=True)
        return 2

    env = dict(os.environ)
    env["COLYSEUS_PORT"] = str(args.port)
    # FL-4873: the executed-source identity observed before Godot starts. The
    # F9 recorder names every recording (runner-requested or a manual F9 in
    # this session) from it, so a later HEAD never relabels the capture.
    env["ASCIICKER_LAUNCH_SOURCE_IDENTITY"] = _launch_source_identity(
        repo_provenance, provenance.run_identity if provenance is not None else None,
        source_root=Path(replay_plan["materialized_source"]["root"])
        if replay_plan is not None and replay_plan.get("materialized_source") else None)
    if args.record_flight_video:
        env["ASCIICKER_SCREEN_RECORDING_ALLOWED"] = "1"
        # The ordinary F9 path keeps World's 20 GiB reserve. This bounded proof
        # keeps 8 GiB free plus the supervisor's independent 1 GiB output cap;
        # it must not delete unrelated raw evidence merely to launch a video.
        env["ASCIICKER_SCREEN_RECORDING_MIN_FREE_BYTES"] = str(8 * 1024**3)
        # Operator title plus short-head/dirty identity ride the same
        # environment door as the other recorder options. The game sanitizes
        # the title for filenames and reads the identity for .mov/HUD naming.
        env["ASCIICKER_SCREEN_RECORDING_TITLE"] = str(getattr(args, "recording_title", "") or "")
        env["ASCIICKER_SCREEN_RECORDING_GIT_IDENTITY"] = _recording_git_identity(repo_provenance)
    else:
        env.pop("ASCIICKER_SCREEN_RECORDING_ALLOWED", None)
        env.pop("ASCIICKER_SCREEN_RECORDING_MIN_FREE_BYTES", None)
        env.pop("ASCIICKER_SCREEN_RECORDING_TITLE", None)
        env.pop("ASCIICKER_SCREEN_RECORDING_GIT_IDENTITY", None)
    # FL-4896: the F9 recorder resolves its finalizer as res://../scripts/..., which
    # only exists beside a project that sits in a full source tree. Hand it the
    # finalizer of the executed source tree explicitly: the materialized
    # snapshot's own copy on --replay-from-snapshot, else this checkout's.
    env["ASCIICKER_F9_FINALIZER"] = str(_f9_finalizer_path(replay_plan))
    if getattr(args, "proof_id", ""):
        env["ASCIICKER_PROOF_ID"] = str(args.proof_id)
    else:
        env.pop("ASCIICKER_PROOF_ID", None)
    env.update(_player_spawn_environment(args, exact_pose_capture=exact_pose_capture))
    if replay_plan is not None:
        env.update(replay_plan["env"])
    if args.camera_mode in ("plane_air", "plane_air_ortho"):
        # FL-4359 PROCESS FAILURE GUARD: the deleted
        # godot_project/scripts/run_fl4359_airplane_capture.gd harness used client
        # pickup + takeoff choreography and was wrongly treated as an airborne
        # airplane shimmer proof. The active movement runner starts the first player
        # already mounted in a server-owned airborne airplane, but only for the
        # airplane proof modes. Static perspective parity runs must keep the
        # Sunday-clone ground-actor state.
        env.pop("ASCIICKER_ENABLE_DOGFIGHT_BOT", None)
        env["ASCIICKER_DISABLE_HUNTER_BOT"] = "1"  # NO mount-triggered hunter either, same reason
        env["AIRPLANE_SPAWN_AIRBORNE"] = "1"
        env.setdefault("AIRPLANE_SPAWN_ALTITUDE", "45")
        env["AIRPLANE_SPAWN_THROTTLE"] = str(max(0.0, min(1.0, args.airborne_spawn_throttle)))
        env.setdefault("AIRPLANE_SPAWN_X", DEFAULT_AIRPLANE_PROOF_SPAWN_X)
        env.setdefault("AIRPLANE_SPAWN_Z", DEFAULT_AIRPLANE_PROOF_SPAWN_Z)
    else:
        for key in (
            "AIRPLANE_SPAWN_AIRBORNE",
            "AIRPLANE_SPAWN_ALTITUDE",
            "AIRPLANE_SPAWN_THROTTLE",
        ):
            env.pop(key, None)
    print(f"[fl4359-headed] starting colyseus server on :{args.port}", flush=True)
    slog = server_log.open("w")
    server = subprocess.Popen(
        ["node", "--import", "tsx", "src/index.ts"],
        cwd=str(colyseus_dir),
        env=env,
        stdout=slog,
        stderr=subprocess.STDOUT,
    )

    def listening() -> bool:
        if server.poll() is not None:
            return False
        try:
            return "ASCIICKER_COLYSEUS_SPIKE_LISTENING" in server_log.read_text()
        except OSError:
            return False

    godot_rc = 1
    analyzer_rc = 1
    try:
        if not _wait_for(listening, args.server_boot_timeout):
            print("[fl4359-headed] ERROR: server did not report LISTENING", flush=True)
            if server_log.exists():
                print(server_log.read_text()[-2000:], flush=True)
            return 3
        print("[fl4359-headed] server listening; launching headed Godot", flush=True)
        if effective_plane_circle_frames != max(1, int(args.plane_circle_frames)):
            print(
                "[fl4359-headed] plane_air bounded metadata window: "
                f"requested_frames={args.plane_circle_frames} effective_frames={effective_plane_circle_frames} "
                f"requested_jitter_limit={args.jitter_capture_frame_limit} "
                f"effective_jitter_limit={effective_jitter_capture_frame_limit}",
                flush=True,
            )
        if godot_timeout != float(args.godot_timeout):
            print(
                "[fl4359-headed] finite Godot timeout policy: "
                f"requested={args.godot_timeout:.1f}s effective={godot_timeout:.1f}s",
                flush=True,
            )
        # FL-4516: Main.gd refuses a windowed launch without this admission;
        # cmd already ends with the user arguments after "--".
        if "--" not in cmd:
            print("[fl4359-headed] ERROR: Godot command has no user-argument separator", flush=True)
            return _launch_guard.REFUSED
        cmd.append(f"--true-ff-admission={out_dir.resolve() / 'guard_admission.json'}")
        if _launch_guard.before_spawn(out_dir, godot_project_dir, colyseus_dir) != 0:
            return _launch_guard.REFUSED
        if args.record_flight_video and sys.platform == "darwin":
            # Use the same registered LaunchServices route as manual play. A
            # direct terminal child can render screenshots while Ghostty stays
            # frontmost, which makes a screen recording capture the terminal
            # instead of the user-reachable Godot viewport.
            godot_rc, activation = _launch_recorded_macos_godot(
                cmd, env, godot_log, godot_project_dir, args.port, godot_timeout)
            (out_dir / "window_activation.json").write_text(
                json.dumps(activation, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        else:
            godot_rc = _run_direct_godot(cmd, env, godot_log, godot_timeout)
        _launch_guard.record_completion(out_dir, godot_rc)
        if provenance is not None:
            provenance.note_godot_exit(godot_rc)
        runtime_receipt = {}
        if receipt_path.exists():
            try:
                runtime_receipt = json.loads(receipt_path.read_text())
            except (OSError, json.JSONDecodeError):
                runtime_receipt = {}
        if replay_plan is not None:
            # FL-4873: the replay is judged by replay validation and the
            # causal analysis, not by the movement-route gates below.
            return f9_replay.post_run(replay_plan, godot_rc, godot_log, preserver=preserver)
        if "--fl4359-menu-proof" in args.extra_user_arg:
            menu_pass = godot_rc == 0 and runtime_receipt.get("pass") is True
            summary_path.write_text(json.dumps({
                "schema": "fl4359.menu_result.v1", "pass": menu_pass,
                "runtime_receipt": str(receipt_path), "godot_rc": godot_rc,
                "scope": "stationary live menu input and compositor dispatch; no flight verdict",
                "provenance": repo_provenance,
            }, indent=2) + "\n")
            print(f"[fl4359-headed] MENU {'PASS' if menu_pass else 'FAIL'} receipt={receipt_path}", flush=True)
            return 0 if menu_pass else 1
        if isinstance(runtime_receipt, dict) and runtime_receipt:
            _prepare_runtime_receipt(
                runtime_receipt,
                args=args,
                godot_log=godot_log,
                repo_provenance=repo_provenance,
            )
            receipt_path.write_text(
                json.dumps(runtime_receipt, indent=2, sort_keys=True) + "\n"
            )
        canvas_analysis_rc = 0
        canvas_analysis_path = out_dir / "canvas_analysis.json"
        if args.canvas_provenance_capture:
            canvas_analysis_rc, canvas_analysis_path = _run_canvas_provenance_analysis(
                out_dir,
                runtime_receipt,
            )
            if isinstance(runtime_receipt, dict) and runtime_receipt:
                runtime_receipt["canvas_provenance_offline_analysis"] = {
                    "status": "ok" if canvas_analysis_rc == 0 else "failed",
                    "exact": canvas_analysis_rc == 0,
                    "path": str(canvas_analysis_path),
                    "surface_boundary": "viewport_after_canvas_layers",
                    "input_boundary": "captured_pre_canvas_load_action_target",
                    "post_compositor_causality": "not_claimed",
                }
                receipt_path.write_text(
                    json.dumps(runtime_receipt, indent=2, sort_keys=True) + "\n"
                )
            print(
                "[fl4359-headed] canvas_analysis_rc="
                f"{canvas_analysis_rc} path={canvas_analysis_path}",
                flush=True,
            )
        invalid_reason = _invalid_run_reason(godot_rc, receipt_path, capture_path)
        # FL-4896: default glyph flip detection. Every FF run measures its text
        # export (and draws media for any recorded F9 bundle) before analysis.
        if _strict_true_ff(args):
            true_ff_text_flips = _true_ff_text_flips_after_run(
                args, out_dir=out_dir, receipt_path=receipt_path, godot_log=godot_log,
                repo_provenance=repo_provenance)
        if args.presentation_field_capture and capture_path.is_file():
            with (out_dir / "presentation_analysis.log").open("w") as presentation_log:
                presentation_rc = subprocess.run([
                    sys.executable, str(ROOT / "scripts/fl4359_actual_field_stability.py"),
                    str(capture_path), "--out", str(out_dir / "presentation_stability.json"),
                    "--presentation-verdict",
                ], cwd=str(ROOT), stdout=presentation_log, stderr=subprocess.STDOUT).returncode
            print(f"[fl4359-headed] presentation_analysis_rc={presentation_rc}", flush=True)
            if args.presentation_observer_only:
                result = {"schema": "fl4359.presentation_observer_run.v1",
                          "classification": "diagnostic_only", "acceptance_claimed": False,
                          "godot_rc": godot_rc, "analysis_rc": presentation_rc,
                          "runtime_pass": runtime_receipt.get("pass"),
                          "runtime_reason": runtime_receipt.get("reason"),
                          "invalid_run_reason": invalid_reason,
                          "provenance": repo_provenance,
                          "measurement": str(out_dir / "presentation_stability.json")}
                (out_dir / "presentation_observer_result.json").write_text(json.dumps(result, indent=2) + "\n")
                return 0 if (
                    godot_rc == 0
                    and presentation_rc == 0
                    and runtime_receipt.get("pass") is True
                    and canvas_analysis_rc == 0
                ) else 1
        if args.skip_jitter_analysis:
            analyzer_rc = 0
            skipped_reason = (
                "fl4671_lerp_receipt_diagnostic"
                if args.fl4671_lerp_receipt_diagnostic
                else "canonical_png_grid_capture_only"
            )
            summary_path.write_text(
                json.dumps(
                    {
                        "frame_count": 0,
                        "max_pair_jitter_pct": 0.0,
                        "max_snap_px_delta": 0.0,
                        "avg_snap_px_delta": 0.0,
                        "analysis_skipped": True,
                        "reason": skipped_reason,
                        "run_classification": args.run_classification,
                        "proof_classification": DIAGNOSTIC_PROOF_CLASSIFICATION,
                        "diagnostic_only": True,
                        "proof": "bounded_static_visual_diagnostic",
                        "admissible": False,
                        "pass": False,
                        "proof_pass": False,
                        "proof_reason": "diagnostic_only_analysis_skipped",
                        "presentation_profile": repo_provenance.get("presentation_profile", ""),
                        "wrapper_git_head": repo_provenance.get("git_head", ""),
                        "godot_rc": godot_rc,
                        "analyzer_rc": analyzer_rc,
                        "git_head": repo_provenance.get("git_head", ""),
                        "git_dirty": bool(repo_provenance.get("git_dirty", False)),
                        "git_dirty_count": int(repo_provenance.get("git_dirty_count", 0)),
                        "git_dirty_paths": list(repo_provenance.get("git_dirty_paths", [])),
                        "provenance": {
                            "source": "scripts/run_fl4359_headed_movement.py",
                            "wrapper": repo_provenance,
                            "runtime": {
                                "godot_rc": godot_rc,
                                "analyzer_rc": analyzer_rc,
                                "receipt_path": str(receipt_path),
                                "capture_path": str(capture_path),
                            },
                        },
                    },
                    indent=2,
                )
                + "\n"
            )
        elif _strict_true_ff(args) and invalid_reason in ("", "missing_jitter_capture_jsonl"):
            # FL-4896: strict True FF writes no jitter capture; its text export
            # is the per-frame capture, so the text flip instrument replaces the
            # fixed-cell analyzer. A timeout or a missing completion receipt
            # still takes the invalid branch below.
            analyzer_rc = int(true_ff_text_flips["rc"])
            summary_path.write_text(json.dumps(_true_ff_text_flips_summary(
                true_ff_text_flips, args=args, godot_rc=godot_rc, analyzer_rc=analyzer_rc,
                receipt_path=receipt_path, repo_provenance=repo_provenance), indent=2) + "\n")
        elif invalid_reason:
            analyzer_rc = 1
            _write_invalid_summary(
                summary_path,
                reason=invalid_reason,
                godot_rc=godot_rc,
                receipt_path=receipt_path,
                capture_path=capture_path,
                repo_provenance=repo_provenance,
                analyzer_rc=analyzer_rc,
            )
            print(
                f"[fl4359-headed] INVALID: {invalid_reason}; partial capture not analyzed",
                flush=True,
            )
        else:
            with analysis_path.open("w") as out:
                analyzer_cmd = [
                    sys.executable,
                    str(ROOT / "scripts/analyze_fl4359_moving_camera.py"),
                    str(capture_path),
                    "--all-frames",
                    "--summary-json",
                    str(summary_path),
                    "--runtime-receipt",
                    str(receipt_path),
                    "--max-content-jitter-pct",
                    str(args.max_jitter_pct),
                ]
                if (
                    not _stationary_calibration_enabled(args)
                    and args.run_classification
                    != RUN_CLASSIFICATION_DIAGNOSTIC_ONLY
                ):
                    analyzer_cmd.append("--fail-on-gate")
                if args.analyzer_proof_profile in {
                    "same_frame_oracle_v1",
                    "full_grid_temporal_oracle_v1",
                }:
                    analyzer_cmd.extend(
                        ["--proof-profile", args.analyzer_proof_profile]
                    )
                    if args.analyzer_proof_profile == "full_grid_temporal_oracle_v1":
                        analyzer_cmd.extend(
                            ["--defect-map-jsonl", str(defect_map_path)]
                        )
                elif args.camera_mode in ("plane_air", "plane_air_ortho"):
                    analyzer_cmd.extend(["--proof-profile", "airplane_temporal_v1"])
                if args.sunday_summary_json:
                    analyzer_cmd.extend(["--sunday-summary-json", args.sunday_summary_json])
                for gate_name in args.expected_red_gate:
                    analyzer_cmd.extend(["--expected-red-gate", gate_name])
                analyzer_rc = subprocess.run(
                    analyzer_cmd,
                    cwd=str(ROOT),
                    stdout=out,
                    stderr=subprocess.STDOUT,
                ).returncode
    finally:
        if server.poll() is None:
            server.send_signal(signal.SIGINT)
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
        slog.close()
        _persist_runtime_source_identity(receipt_path, launch_source_identity, source_identity_scopes)

    for line in godot_log.read_text().splitlines() if godot_log.exists() else []:
        if "FL4359" in line or "GODOT_COLYSEUS" in line or "ERROR:" in line:
            print(f"[godot] {line}", flush=True)

    receipt = {}
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        _reconcile_receipt_with_analyzer(receipt, analyzer_rc)
        receipt["run_budget"] = run_budget.as_dict()
        receipt["presentation_profile"] = _presentation_profile(args)
        receipt["wrapper_git_head"] = repo_provenance.get("git_head", "")
        if args.fl4359_historical_r6_replay and not _historical_r6_replay_receipt_accepts(receipt):
            receipt["historical_r6_failure_reason"] = "historical_r6_native_presentation_receipt_failed"
            if receipt.get("pass") is True:
                receipt["pass"] = False
                receipt["reason"] = receipt["historical_r6_failure_reason"]
        receipt["launcher_log_hand_facts"] = _launcher_log_hand_facts(godot_log)
        receipt["fl4216_selected_surface"] = _classify_fl4216_selected_surface_dump(receipt)
        temporal_frames = receipt.get("fl4216_temporal_frames")
        if isinstance(temporal_frames, list):
            temporal_contracts = []
            temporal_pose_contracts = []
            water_interaction_contracts = []
            for frame in temporal_frames:
                if not isinstance(frame, dict):
                    temporal_contracts.append({"pass": False, "reason": "invalid_temporal_frame"})
                    continue
                existing_validation = frame.get("runtime_field_contract", {})
                validation = _reuse_pruned_fl4216_contract(
                    _validate_fl4216_temporal_dump(
                        frame.get("final_render_cell_dump", {})
                    ),
                    existing_validation,
                    fallback_reasons={
                        "missing_final_render_artifact_paths",
                        "missing_final_render_artifact_path_key",
                        "missing_final_render_artifact_file",
                        "missing_temporal_snapshot_json",
                    },
                    contract_kind="runtime_field_contract",
                )
                frame["runtime_field_contract"] = validation
                temporal_contracts.append(validation)
                existing_pose = frame.get("artifact_pose_contract", {})
                pose_validation = _reuse_pruned_fl4216_contract(
                    _validate_fl4216_temporal_pose_metadata(
                        frame.get("final_render_cell_dump", {}),
                        receipt.get("exact_player_position", {}),
                        receipt.get("exact_camera_position", {}),
                        str(receipt.get("camera_mode", "")),
                        receipt.get("exact_camera_yaw"),
                        receipt.get("exact_camera_pitch"),
                        frame.get("fl4216_grounding_contract")
                        or receipt.get("fl4216_grounding_contract"),
                    ),
                    existing_pose,
                    fallback_reasons={"missing_temporal_metadata_json"},
                    contract_kind="artifact_pose_contract",
                )
                frame["artifact_pose_contract"] = pose_validation
                temporal_pose_contracts.append(pose_validation)
                water_interaction_validation = _validate_fl4216_water_interaction_field(
                    frame.get("final_render_cell_dump", {})
                )
                frame["water_interaction_field_contract"] = water_interaction_validation
                water_interaction_contracts.append(water_interaction_validation)
            receipt["fl4216_temporal_field_contract"] = {
                "pass": bool(temporal_contracts)
                and all(item.get("pass") is True for item in temporal_contracts),
                "frames": temporal_contracts,
                "required_top_level_fields": list(FL4216_TEMPORAL_TOP_LEVEL_FIELDS),
                "required_quarter_fields": list(FL4216_TEMPORAL_QUARTER_FIELDS),
            }
            receipt["fl4216_temporal_pose_contract"] = {
                "pass": bool(temporal_pose_contracts)
                and all(item.get("pass") is True for item in temporal_pose_contracts),
                "frames": temporal_pose_contracts,
                "epsilon": 0.01,
                "actor_reference_source": "World._sync_pixel_camera_to_compositor -> Player.global_position",
                "grounding_source": "World.actor_presentation_root_world cross-checked with stable publication",
            }
            receipt["fl4216_water_interaction_field_contract"] = {
                "pass": bool(water_interaction_contracts)
                and all(item.get("pass") is True for item in water_interaction_contracts),
                "frames": water_interaction_contracts,
                "required_steady_state_last_upload_bytes": 0,
            }
            receipt["fl4216_temporal_timing_contract"] = _validate_fl4216_temporal_frame_timing(
                temporal_frames,
                _float_value(receipt.get("fl4216_temporal_interval_seconds"), 0.0),
            )
            receipt["fl4216_temporal_comparison"] = _reuse_pruned_fl4216_comparison(
                _compare_fl4216_temporal_frames(temporal_frames),
                receipt.get("fl4216_temporal_comparison", {}),
            )
            if receipt.get("pass") is True and not receipt["fl4216_temporal_field_contract"]["pass"]:
                receipt["pass"] = False
                receipt["reason"] = "fl4216_temporal_runtime_field_contract_failed"
            if receipt.get("pass") is True and not receipt["fl4216_temporal_pose_contract"]["pass"]:
                receipt["pass"] = False
                receipt["reason"] = "fl4216_temporal_metadata_pose_mismatch"
            if receipt.get("pass") is True and not receipt["fl4216_water_interaction_field_contract"]["pass"]:
                receipt["pass"] = False
                receipt["reason"] = "fl4216_water_interaction_field_contract_failed"
            if receipt.get("pass") is True and not receipt["fl4216_temporal_timing_contract"]["pass"]:
                receipt["pass"] = False
                receipt["reason"] = receipt["fl4216_temporal_timing_contract"]["reason"]
            if receipt.get("pass") is True and not receipt["fl4216_temporal_comparison"]["pass"]:
                receipt["pass"] = False
                receipt["reason"] = receipt["fl4216_temporal_comparison"]["reason"]
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        print("[fl4359-headed] receipt:", json.dumps(receipt, indent=2), flush=True)
    else:
        print(f"[fl4359-headed] FAIL: no receipt. godot rc={godot_rc}", flush=True)
        if godot_log.exists():
            print(godot_log.read_text()[-2000:], flush=True)
        return 1

    capture_window = receipt.get("capture_window", {})
    if not _capture_window_receipt_accepts(receipt, args):
        print(
            "[fl4359-headed] FAIL: requested fullscreen but runtime receipt did not report actual fullscreen "
            f"capture_window={json.dumps(capture_window, sort_keys=True)}",
            flush=True,
        )
        return 1

    grid_path = _stitch_movement_grid(receipt, out_dir)
    if grid_path is not None:
        print(f"[fl4359-headed] movement_grid={grid_path}", flush=True)
    else:
        print("[fl4359-headed] movement_grid=not_written", flush=True)

    fl4671_summary = {}
    if args.fl4671_lerp_receipt_diagnostic:
        fl4671_summary = _fl4671_lerp_receipt_summary(
            godot_log=godot_log,
            receipt=receipt,
            repo_provenance=repo_provenance,
        )
        (out_dir / "fl4671_lerp_summary.json").write_text(
            json.dumps(fl4671_summary, indent=2) + "\n"
        )
        print("[fl4671] lerp_summary:", json.dumps(fl4671_summary, indent=2), flush=True)

    summary = {}
    full_grid_runtime_contract = {"pass": True, "reason": "not_full_grid_oracle"}
    if summary_path.exists():
        summary = _augment_summary_with_receipt(
            summary_path,
            receipt=receipt,
            godot_rc=godot_rc,
            analyzer_rc=analyzer_rc,
            receipt_path=receipt_path,
            capture_path=capture_path,
            out_dir=out_dir,
            requested_plane_circle_frames=max(1, int(args.plane_circle_frames)),
            effective_plane_circle_frames=effective_plane_circle_frames,
            requested_jitter_capture_frame_limit=max(0, int(args.jitter_capture_frame_limit)),
            effective_jitter_capture_frame_limit=effective_jitter_capture_frame_limit,
            requested_turn_frames=max(0, int(args.turn_frames)),
            effective_turn_frames=effective_turn_frames,
            jitter_capture_world_sample_stride=max(1, int(args.jitter_capture_world_sample_stride)),
            requested_godot_timeout_seconds=float(args.godot_timeout),
            godot_timeout_seconds=_godot_subprocess_timeout(args.godot_timeout),
            repo_provenance=repo_provenance,
            run_classification=args.run_classification,
        )
        # FL-4359 sweep-pin admissibility. An explicit `--exact-pose-sweep-positions`
        # list is a claim about what each captured frame rendered from, so grade the
        # captured eyes against it before any downstream reader trusts the receipt.
        declared_sweep_positions = _parse_world_vec3_list_arg(
            getattr(args, "exact_pose_sweep_positions", "")
        )
        if declared_sweep_positions:
            sweep_frame_poses = _exact_pose_sweep_frame_pose_rows(
                capture_path,
                declared_sweep_positions,
                requested_yaw=getattr(args, "exact_camera_yaw", None),
                requested_pitch=getattr(args, "exact_camera_pitch", None),
            )
            if not sweep_frame_poses:
                # Frame-PNG route: no per-frame capture trace exists, so the
                # driver receipt's applied poses are the grading source. Same
                # row shape, same tolerance, same mismatch line below.
                sweep_frame_poses = _frame_png_pose_rows(
                    receipt,
                    declared_sweep_positions,
                    requested_yaw=getattr(args, "exact_camera_yaw", None),
                    requested_pitch=getattr(args, "exact_camera_pitch", None),
                )
            summary["exact_pose_sweep_frame_poses"] = sweep_frame_poses
            mismatched_sweep_frames = [
                row["frame_id"]
                for row in sweep_frame_poses
                if not (
                    row["match"]
                    and row.get("camera_yaw_match") is True
                    and row.get("camera_pitch_match") is True
                )
            ]
            if mismatched_sweep_frames:
                summary["admissible"] = False
                summary["reason"] = "exact_pose_sweep_frame_pose_mismatch"
                print(
                    "[fl4359-headed] exact_pose_sweep_frame_pose_mismatch "
                    f"frames={mismatched_sweep_frames}",
                    flush=True,
                )
            # `_augment_summary_with_receipt` already wrote the summary, so this
            # owner republishes it rather than leaving the durable file behind.
            summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        # FL-4359 frame-PNG route: stitch the five declared-sequence PNGs into the
        # operator's 5-frame / 60 ms GIF shape. Stitching only; it grades nothing
        # and it never invents a path for a run that produced fewer than five.
        if getattr(args, "frame_pngs", False):
            sweep_gif, sweep_gif_reason = _stitch_sweep_gif(out_dir)
            summary["sweep_gif"] = sweep_gif
            summary["sweep_gif_reason"] = sweep_gif_reason
            summary["frame_png_dir"] = str(out_dir / FRAME_PNG_DIR_NAME)
            summary["frame_pngs"] = list(receipt.get("frame_pngs") or [])
            summary["frame_png_poses"] = list(receipt.get("frame_png_poses") or [])
            print(
                "[fl4359-headed] sweep_gif="
                + (sweep_gif or f"<none:{sweep_gif_reason}>"),
                flush=True,
            )
            summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        full_grid_runtime_contract = _full_grid_receipt_contract(
            summary,
            receipt,
            args,
            # GIF generation and comparison run after capture.
            require_presentation_artifacts=False,
            capture_path=capture_path,
        )
        if full_grid_runtime_contract.get("pass") is not True:
            summary["admissible"] = False
            summary["reason"] = full_grid_runtime_contract["reason"]
            if receipt.get("pass") is True:
                receipt["pass"] = False
                receipt["reason"] = full_grid_runtime_contract["reason"]
                receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        summary["full_grid_receipt_contract"] = full_grid_runtime_contract
        if _full_grid_contract_mode(args):
            temporal_performance_gate = _same_run_temporal_performance_gate(
                receipt,
                summary,
            )
            receipt["same_run_temporal_performance_gate"] = temporal_performance_gate
            summary["same_run_temporal_performance_gate"] = temporal_performance_gate
            _enforce_same_run_temporal_identity_gate(
                receipt,
                summary,
                full_grid_runtime_contract,
                temporal_performance_gate,
            )
        if args.record_flight_video and sys.platform == "darwin":
            focus_pass, focus_reason = _video_focus_gate(activation)
            summary["video_focus_gate"] = {
                "pass": focus_pass,
                "reason": focus_reason,
                "focus_losses": int(_dict_value(activation.get("focus_keeper")).get("loss_count", 0) or 0),
                "receipt": str(out_dir / "window_activation.json"),
                "gpu_field_export_preserved": True,
            }
            if not summary["video_focus_gate"]["pass"]:
                summary["admissible"] = False
        summary_path.write_text(json.dumps(summary, indent=2) + "\n")
        if summary.get("admissible") is False:
            print(
                "[fl4359-headed] invalid_evidence: "
                f"reason={summary.get('reason', 'unknown')} godot_rc={summary.get('godot_rc')}",
                flush=True,
            )
        else:
            for report_line in _current_metric_report_lines(summary):
                print(f"[fl4359-headed] {report_line}", flush=True)
    else:
        print("[fl4359-headed] FAIL: no analyzer summary", flush=True)
        if analysis_path.exists():
            print(analysis_path.read_text()[-2000:], flush=True)
        return 1

    publication_manifest_path = out_dir / GODOT_PROOF_PUBLICATION_MANIFEST
    receipt["godot_proof_publication_manifest"] = str(publication_manifest_path)
    summary["godot_proof_publication_manifest"] = str(publication_manifest_path)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    try:
        # Receipts point to this deterministic path before publication so the
        # one manifest pass hashes their final bytes rather than a stale copy.
        publication_manifest_path = _publish_visual_artifacts(
            out_dir,
            receipt,
            capture_path,
            start_state=output_start_state,
        )
    except PublicationError as error:
        # The runtime result remains in its original receipt, but this output
        # cannot be delivered as a published proof without an auditable package
        # and Finder-tag readback.
        print(f"[fl4359-headed] PUBLICATION_FAIL: {error}", flush=True)
        return 1
    artifact_manifest_path = _write_artifact_manifest(
        out_dir,
        [
            receipt_path,
            summary_path,
            capture_path,
            analysis_path,
            godot_log,
            server_log,
            defect_map_path,
            grid_path if grid_path is not None else out_dir / "movement_grid.png",
            publication_manifest_path,
        ],
    )
    print(
        f"[fl4359-headed] artifact_manifest={artifact_manifest_path} "
        f"sha256={_sha256_file(artifact_manifest_path)}",
        flush=True,
    )
    print(f"[fl4359-headed] publication_manifest={publication_manifest_path}", flush=True)

    max_jitter = float(summary.get("max_pair_jitter_pct", 100.0))
    actor_owner_rows = 0
    if capture_path.exists():
        with capture_path.open("r") as capture:
            for line in capture:
                if '"selected_owner":"actor_sprite"' in line or '"selected_owner": "actor_sprite"' in line:
                    actor_owner_rows += 1
    receipt_ok = bool(receipt.get("pass", False))
    fullscreen_ok = _capture_window_receipt_accepts(receipt, args)
    airplane_faces = int(receipt.get("airplane_dynamic_face_count", 0) or 0)
    actor_frame_rect_rows = int(receipt.get("actor_frame_rect_row_count", 0) or 0)
    actor_surface_ok = actor_owner_rows > 0 or actor_frame_rect_rows > 0
    if args.camera_mode in ("plane_air", "plane_air_ortho"):
        # The dogfight driver observes the authoritative world-item population
        # and airborne pilot state. A stale local geometry counter can be absent
        # from the clean arm without making the server-owned airplane vanish.
        actor_surface_ok = (
            receipt.get("airplane_world_item_count_exact") is True
            and int(receipt.get("max_airplane_world_item_count", 0) or 0) > 0
            and receipt.get("airborne_airplane_ready") is True
            and receipt.get("local_airplane_pilot") is True
        )
    if args.fl4671_lerp_receipt_diagnostic:
        actor_surface_ok = fl4671_summary.get("route_mounted_witness_present") is True
        route_receipt_ok = actor_surface_ok
    elif _stationary_calibration_enabled(args):
        route_receipt_ok = _stationary_calibration_receipt_accepts(receipt, args)
    elif _full_grid_contract_mode(args):
        route_receipt_ok = full_grid_runtime_contract.get("pass") is True
    else:
        route_receipt_ok = _airplane_route_receipt_accepts_proof(receipt, args.camera_mode)
        if args.analyzer_proof_profile == "full_grid_temporal_oracle_v1":
            route_receipt_ok = route_receipt_ok and _full_grid_runtime_celestial_receipt_accepts(
                receipt, args
            )
    historical_r6_presentation_ok = (
        _historical_r6_replay_receipt_accepts(receipt)
        if args.fl4359_historical_r6_replay
        else True
    )
    canonical_presentation_ok = (
        _canonical_airplane_presentation_receipt_accepts(
            receipt,
            args.camera_mode,
        )
        if not args.fl4359_historical_r6_replay
        else True
    )
    skip_contract_ok = (
        _night_airborne_temporal_capture_accepts_analysis_skip(
            summary,
            receipt,
            bool(args.skip_jitter_analysis),
        )
        or _fl4216_temporal_capture_accepts_analysis_skip(
            summary,
            receipt,
            bool(args.skip_jitter_analysis),
        )
        or _clean_play_no_capture_accepts_analysis_skip(
            summary,
            receipt,
            bool(args.skip_jitter_analysis),
        )
        or _fl4671_lerp_receipt_diagnostic_accepts_analysis_skip(
            summary,
            receipt,
            bool(args.skip_jitter_analysis),
            fl4671_summary,
        )
        or _bounded_static_diagnostic_accepts_analysis_skip(
            summary,
            receipt,
            skip_jitter_analysis=bool(args.skip_jitter_analysis),
            run_classification=args.run_classification,
            exact_pose_capture=exact_pose_capture,
        )
    )
    if _stationary_calibration_enabled(args):
        analysis_contract_ok = _stationary_calibration_summary_accepts(summary)
        classification_summary_ok = (
            summary.get("calibration_only") is True
            and summary.get("proof_classification")
            == STATIONARY_TERMINAL_REPROJECTION_CALIBRATION
            and summary.get("acceptance_claimed") is False
            and summary.get("closure_claimed") is False
        )
    elif args.run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY:
        analysis_contract_ok = (
            skip_contract_ok
            or _analyzed_diagnostic_is_nonproof(
                summary,
                receipt,
                skip_jitter_analysis=bool(args.skip_jitter_analysis),
                run_classification=args.run_classification,
            )
        )
        classification_summary_ok = _summary_is_diagnostic_only(summary)
    else:
        analysis_contract_ok = (
            not bool(summary.get("analysis_skipped", False))
            and _analysis_gates_accept_proof(summary)
            and max_jitter <= float(args.max_jitter_pct)
        )
        classification_summary_ok = _summary_classification_is_movement_proof(summary)
    if full_grid_runtime_contract.get("pass") is not True:
        route_receipt_ok = False
        analysis_contract_ok = False
    video_focus_ok = not (args.record_flight_video and sys.platform == "darwin") or _video_focus_gate(activation)[0]
    ok = (
        godot_rc == 0
        and video_focus_ok
        and analyzer_rc == 0
        and receipt_ok
        and fullscreen_ok
        and route_receipt_ok
        and canonical_presentation_ok
        and historical_r6_presentation_ok
        and analysis_contract_ok
        and classification_summary_ok
        and actor_surface_ok
        and canvas_analysis_rc == 0
    )
    if not ok:
        gates = _dict_value(summary.get("gates"))
        print(
            "[fl4359-headed] FAIL details: "
            f"godot_rc={godot_rc} analyzer_rc={analyzer_rc} "
            f"receipt_pass={receipt_ok} legacy_max_pair_jitter={max_jitter:.2f}% "
            f"fullscreen_ok={fullscreen_ok} "
            f"video_focus_ok={video_focus_ok} "
            f"canonical_presentation_ok={canonical_presentation_ok} "
            f"historical_r6_presentation_ok={historical_r6_presentation_ok} "
            f"legacy_threshold={args.max_jitter_pct:.2f}% actor_owner_rows={actor_owner_rows} "
            f"actor_frame_rect_rows={actor_frame_rect_rows} airplane_faces={airplane_faces} "
            f"route_receipt_ok={route_receipt_ok} "
            f"analysis_contract_ok={analysis_contract_ok} "
            f"classification_summary_ok={classification_summary_ok} "
            f"analyzer_gates_all_pass={bool(gates.get('all_pass', False))} "
            f"blocking_failed_gates={gates.get('blocking_failed_gates', [])} "
            f"failed_gates={gates.get('failed_gates', [])}",
            flush=True,
        )
    if true_ff_text_flips is not None:
        # FL-4896: every FF summary carries the glyph flip measurement, whichever
        # analysis branch wrote it; the counts and paths close the run log.
        try:
            final_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            final_summary = None
        if isinstance(final_summary, dict) and "true_ff_text_flips" not in final_summary:
            final_summary["true_ff_text_flips"] = true_ff_text_flips
            summary_path.write_text(json.dumps(final_summary, indent=2) + "\n")
        _print_true_ff_text_flips(true_ff_text_flips)
    if _stationary_calibration_enabled(args):
        status = "STATIONARY_CALIBRATION_PASS" if ok else "STATIONARY_CALIBRATION_FAIL"
    elif args.run_classification == RUN_CLASSIFICATION_DIAGNOSTIC_ONLY:
        status = "DIAGNOSTIC_OBSERVED" if ok else "DIAGNOSTIC_INVALID"
    else:
        status = "MOVEMENT_PROOF_PASS" if ok else "MOVEMENT_PROOF_FAIL"
    print(
        f"[fl4359-headed] {status} artifacts={out_dir} "
        f"classification={args.run_classification} "
        "acceptance_claimed=false closure_claimed=false",
        flush=True,
    )
    return 0 if ok else 1


def main() -> int:
    preserver = DeferredRunReceiptPreserver(
        repo_root=ROOT,
        fl_id="FL-4359",
        runner="scripts/run_fl4359_headed_movement.py",
    )
    provenance = RunSourceProvenance()
    preserver.add_finalizer(provenance.finalize)
    run_error: BaseException | None = None
    result = 1
    try:
        result = _run(preserver, provenance)
    except BaseException as error:
        run_error = error
    try:
        manifest_path = preserver.preserve()
        if manifest_path is not None:
            print(
                f"[fl4359-headed] preserved_run_receipt={manifest_path}",
                flush=True,
            )
    except (OSError, RuntimeError, ValueError) as error:
        print(
            "[fl4359-headed] RECEIPT_PRESERVATION_FAIL: " + str(error),
            file=sys.stderr,
            flush=True,
        )
        if run_error is None:
            return 1
    result = _apply_source_provenance_verdict(provenance, result)
    if run_error is not None:
        raise run_error
    return result


def _apply_source_provenance_verdict(provenance: RunSourceProvenance, result: int) -> int:
    """Report the durable source record; a provenance failure fails the run."""
    if not provenance.started:
        return result
    record = provenance.record
    print(
        "[fl4359-headed] source provenance: "
        f"start={provenance.start_state.get('snapshot', '')} "
        f"end={(record.get('end') or {}).get('snapshot', '')} "
        f"stable={record.get('source_state_stable')} "
        f"bundles={[row.get('durable_manifest', row.get('error')) for row in record.get('f9_bundles', [])]}",
        flush=True,
    )
    if provenance.verdict == "eligible":
        return result
    if not provenance.ok:
        print(
            "[fl4359-headed] SOURCE_PROVENANCE_FAIL: " + ",".join(sorted(set(provenance.failures))),
            flush=True,
        )
    if provenance.ineligible:
        # FL-4873 review P5: an ineligible capture never exits as success; its
        # evidence (receipt, logs, bundle parts) was preserved with the reasons.
        print(
            "[fl4359-headed] CAPTURE_INELIGIBLE: " + ",".join(sorted(set(provenance.ineligible))),
            flush=True,
        )
    return result if result not in (0, None) else 1


if __name__ == "__main__":
    sys.exit(main())
