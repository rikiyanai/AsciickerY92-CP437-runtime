#!/usr/bin/env python3
"""Build the canonical Godot visual PNG grid from a fail-closed lane manifest.

The operator-designated immutable receipt is the sole Baseline A owner. This
tool deliberately has no date-named project switch and cannot promote a
historical checkpoint through a command-line pathname.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from godot_proof_publication import (
    PublicationError,
    apply_finder_tags,
    deterministic_artifact_name,
    inspect_visual,
    publish_artifacts,
)


BASELINE_RECEIPT_V2_VALIDATOR = ROOT / "scripts" / "build_fl4431_baseline_receipt_v2.py"
BASELINE_RECEIPT_V2_SCHEMA = "asciicker.godot_baseline.v2"
BASELINE_NAMES = {"baseline_a"}
VIEW_MODES = {"ortho", "perspective"}
STATIC_TEMPORAL_SEQUENCE = "static_temporal_sequence"
CAPTURE_SEQUENCES = {"single_frame", "movement_sequence", STATIC_TEMPORAL_SEQUENCE}
BATCHED_CAPTURE_SEQUENCES = {"movement_sequence", STATIC_TEMPORAL_SEQUENCE}
NIGHT_AIRBORNE_FRAME_LABELS = ("beam_t0", "beam_t1", "beam_t2")
NIGHT_AIRBORNE_ROUTE_TICKS = (30, 150, 270)
NIGHT_AIRBORNE_PHASE_OFFSETS = (
    0.0,
    2.0943951023931953,
    4.1887902047863905,
)
NIGHT_CAPTURE_OWNER = "run_fl4265_airplane_dogfight_proof.gd"
GODOT_PROOF_PUBLICATION_MANIFEST = "godot_proof_publication.json"
GODOT_PROOF_PUBLICATION_DIR = "published"
NIGHT_PRODUCT_COUNT_FIELDS = (
    "lighthouse_beam_contributed_subcell_count",
    "bonfire_flame_contributed_subcell_count",
    "bonfire_light_contributed_subcell_count",
    "bonfire_smoke_contributed_subcell_count",
    "lighthouse_tower_selected_subcell_count",
    "terrain_selected_subcell_count",
    "water_selected_subcell_count",
)
# These legacy names made an intermediate Product marker sound like proof that
# the compositor painted a visible pixel. New captures must never emit them.
FORBIDDEN_AUTOMATED_PIXEL_CLAIM_FIELDS = (
    "beam_shaft_in_screen",
    "beam_pixel_evidence_source",
    "bonfire_pixels_in_screen",
    "bonfire_pixel_evidence_source",
    "final_effect_pixel_evidence_state",
)
RUNTIME_PREFIXES = ("godot_project/scripts/", "godot_project/render/", "godot_project/visual/")
# FL-4431 changes one retained-scene chain across host publication, VSB
# selection, Product authoring, and final paint. A visual package that names
# only the final painter cannot identify the source generation that produced
# the pixels, so every closure-grade lane binds this complete runtime set.
FL4431_RUNTIME_SOURCE_FILES = (
    "godot_project/scripts/World.gd",
    "godot_project/scripts/player.gd",
    "godot_project/render/api/ASCIICompositorEffect.gd",
    "godot_project/render/scene/SceneGeometryDB.gd",
    "godot_project/render/core/contracts/visibility/VisibleSurfaceABI.gd",
    "godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl",
    "godot_project/render/product/cell_owner_product.glsl",
    "godot_project/render/ascii/raster/ascii_compositor.glsl",
)
SHARED_SERVER_IDENTITY_FIELDS = (
    "server_commit",
    "server_requested_root",
    "server_source_root",
    "server_dirty_paths",
    "server_runtime_scope_fingerprint",
    "proof_tool_sha256",
)
RENDERER_ARM_LABELS = {
    "baseline": "Baseline A renderer + shared current server",
    "current": "current renderer + shared current server",
}
MANDATORY_PANEL_LABEL_FIELDS = (
    "timestamp",
    "failure_log_refs",
    "code_refs",
    "fix_attempt_number",
    "fix_attempt_description",
    "rationale",
    "commit",
    "dirty_state",
    "baseline_panel_identity",
    "current_panel_identity",
    "panel_identity",
)
POSE_TOLERANCE = 0.0001
RUNTIME_POSE_TOLERANCE = 0.25
REQUIRED_CANONICAL_LANE_IDS = {
    "perspective_terrain_water_static",
    "ortho_movement_jitter",
    "perspective_movement_jitter",
    "sprite_npc_worldspace_alignment",
    "sprite_shadow_alignment",
    "blood_decal_alignment",
    "water_wake_path_alignment",
    "falling_leaves_foliage_alignment",
    "airplane_air_movement_perspective",
    "airplane_air_movement_ortho",
    "night_lighthouse_beam",
}


class GridInputError(RuntimeError):
    pass


@dataclass(frozen=True)
class FrameEvidence:
    label: str
    path: Path
    metadata: dict[str, Any]
    sha256: str
    size: tuple[int, int]


def _bool_arg(value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"invalid boolean: {value}")


def _wrapped_positive_angle_delta(first: float, current: float) -> float:
    """Return the observed angle offset from `first` in the 0..2pi range.

    The lighthouse naturally crosses the 2pi-to-zero seam. Modulo converts the
    directed subtraction into the positive one-turn vocabulary used in panel
    receipts. This helper interprets captured facts only; it never supplies a
    renderer phase.
    """
    return (current - first) % math.tau


def _repo_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _project_root(path: Path) -> Path:
    return path.parent if path.name == "godot_project" else path


def _resolve_path(value: str, base: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base / path).resolve()


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GridInputError(f"missing JSON: {path}") from exc
    except json.JSONDecodeError as exc:
        raise GridInputError(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise GridInputError(f"JSON root must be an object: {path}")
    return data


def _load_baseline_v2_validator() -> Any:
    """Load the one receipt authority without creating a second parser."""

    spec = importlib.util.spec_from_file_location(
        "_fl4431_baseline_receipt_v2_grid_validator",
        BASELINE_RECEIPT_V2_VALIDATOR,
    )
    if spec is None or spec.loader is None:
        raise GridInputError(
            f"Baseline A v2 validator could not be loaded: {BASELINE_RECEIPT_V2_VALIDATOR}"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_operator_baseline_receipt(receipt_path: Path) -> tuple[dict[str, Any], Path]:
    """Resolve Baseline A only through the closed v2 receipt validator."""

    module = _load_baseline_v2_validator()
    try:
        receipt = module.validate_receipt(receipt_path)
    except module.ReceiptValidationError as exc:
        raise GridInputError(str(exc)) from exc
    baseline_source = receipt["baseline_source"]
    proof_source = receipt["proof_tool_source"]
    proof_root = Path(proof_source["root"]).expanduser().resolve()
    if proof_root != ROOT.resolve():
        raise GridInputError(
            "Baseline receipt proof_tool_source.root differs from the executing grid checkout"
        )
    source_root = Path(baseline_source["root"]).expanduser().resolve()
    project_path = source_root / str(baseline_source["project_subdir"])
    if not project_path.is_dir():
        raise GridInputError(f"Baseline receipt project is missing: {project_path}")
    return receipt, project_path


def _baseline_receipt_identity(
    receipt_path: Path,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Project the immutable v2 identity into every downstream receipt."""

    return {
        "path": str(receipt_path.expanduser().resolve()),
        "schema": receipt["schema"],
        "self_hash": receipt["self_hash"]["digest"],
        "source_commit": receipt["baseline_source"]["commit"],
        "source_tree": receipt["baseline_source"]["tree"],
        "capture_id": receipt["capture_contract"]["capture_id"],
        "temporal_capture_id": receipt["capture_contract"]["temporal_capture_id"],
        "lane_manifest_sha256": receipt["lane_manifest"]["sha256"],
        "lane_manifest_git_blob": receipt["lane_manifest"]["git_blob"],
        "proof_tool_commit": receipt["proof_tool_source"]["commit"],
        "proof_tool_tree": receipt["proof_tool_source"]["tree"],
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _grid_pane_metadata(
    panel: dict[str, Any],
    *,
    run_id: str,
    current_commit: str,
    baseline_commit: str,
) -> dict[str, Any]:
    """Record only panel origin facts the grid receipt directly establishes."""
    source_commit = baseline_commit if panel["side"] == "baseline" else current_commit
    return {
        "pane_id": f"{panel['lane']}:{panel['side']}:{panel['frame']}",
        "fl_id": "FL-4431",
        "run_id": run_id,
        "proof_kind": "RENDER",
        "architecture": "UNKNOWN",
        "foreground_mode": "UNKNOWN",
        "background_mode": "UNKNOWN",
        "capture_source_state": "UNKNOWN",
        "source_commit": source_commit if source_commit else "UNKNOWN",
        "effective_tree_fingerprint": "UNKNOWN",
        "cell_resolution": "UNKNOWN",
        "glyph_source_resolution": "UNKNOWN",
    }


def _grid_publication_metadata(receipt: dict[str, Any]) -> dict[str, Any]:
    run_id = "GRID-" + str(receipt["sha256"])[:16]
    return {
        "provenance_status": "INCOMPLETE_CURRENT_CAPTURE",
        "fl_id": "FL-4431",
        "run_id": run_id,
        "proof_kind": "RENDER",
        "architecture": "MIXED",
        "foreground_mode": "MIXED",
        "background_mode": "MIXED",
        "capture_source_state": "UNKNOWN",
        "source_commit": "UNKNOWN",
        "effective_tree_fingerprint": "UNKNOWN",
        "cell_resolution": "UNKNOWN",
        "glyph_source_resolution": "UNKNOWN",
        "storage": {"status": "PENDING"},
        "provenance_note": (
            "The composite contains baseline and current panels. Pane labels and the "
            "legacy receipt retain their exact origin facts; renderer, mode, and source "
            "tree fields not recorded by the inputs remain UNKNOWN. Diagnostic only."
        ),
        "panes": [
            _grid_pane_metadata(
                panel,
                run_id=run_id,
                current_commit=str(receipt.get("required_current_commit", "")),
                baseline_commit=str(receipt.get("baseline_source_commit", "")),
            )
            for panel in receipt["panels"]
        ],
    }


def _canonical_hardlink(legacy_path: Path, canonical_path: Path) -> None:
    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    if canonical_path.exists():
        if _sha256(canonical_path) != _sha256(legacy_path):
            raise PublicationError(
                f"canonical name collision has different bytes: {canonical_path}"
            )
        return
    try:
        canonical_path.hardlink_to(legacy_path)
    except OSError as error:
        raise PublicationError(
            f"cannot create no-copy canonical alias for {legacy_path}: {error}"
        ) from error


def _publish_grid_artifacts(out_dir: Path, receipt_path: Path, receipt: dict[str, Any]) -> Path:
    """Publish the immutable grid and receipt without replacing reader paths."""
    metadata = _grid_publication_metadata(receipt)
    image_path = Path(receipt["artifact"])
    if not image_path.is_absolute():
        image_path = (ROOT / image_path).resolve()
    width, height, frames = inspect_visual(image_path)
    drafts = [
        {
            "path": image_path,
            "role": "canonical-current-baseline-grid",
            "frame_count": frames,
            "visual": True,
            "image_dimensions": {"width": width, "height": height},
        },
        {
            "path": receipt_path,
            "role": "canonical-current-baseline-grid-receipt",
            "frame_count": 1,
            "visual": False,
        },
    ]
    artifacts: list[dict[str, Any]] = []
    for draft in drafts:
        canonical_path = out_dir / GODOT_PROOF_PUBLICATION_DIR / deterministic_artifact_name(metadata, draft)
        _canonical_hardlink(Path(draft["path"]), canonical_path)
        artifacts.append({**draft, "path": canonical_path, "aliases": [draft["path"]]})
    publication_path = out_dir / GODOT_PROOF_PUBLICATION_MANIFEST
    publish_artifacts(
        publication_path,
        metadata,
        artifacts,
        artifact_root=out_dir,
        enforce_names=True,
    )
    if sys.platform == "darwin":
        apply_finder_tags(publication_path)
    return publication_path


def _git_text(*args: str, source_root: Path = ROOT) -> str:
    result = subprocess.run(
        ["git", "-C", str(source_root), *args],
        text=True,
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise GridInputError(result.stderr.strip() if result.stderr.strip() else "git command failed")
    return result.stdout.strip()


def _commit_exists(commit: str, source_root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(source_root), "cat-file", "-e", f"{commit}^{{commit}}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _normalize_required_files(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _validate_shared_current_colyseus_dir(
    value: str,
    current_project: str = "",
) -> list[str]:
    """Require one explicit absolute current-server directory before capture.

    The adapter performs the deeper Git and runtime-scope fingerprint checks.
    This early builder gate prevents a relative path plus a renderer-specific
    default from entering either arm's command line.
    """
    if not value:
        return ["--shared-current-colyseus-dir is required"]
    path = Path(value).expanduser()
    if not path.is_absolute():
        return ["--shared-current-colyseus-dir must be an absolute path"]
    if not path.exists():
        return [f"--shared-current-colyseus-dir does not exist: {path}"]
    if not path.is_dir():
        return [f"--shared-current-colyseus-dir is not a directory: {path}"]
    if current_project:
        current_root = _project_root(Path(current_project).resolve())
        expected = (current_root / "colyseus_server").resolve()
        if path.resolve() != expected:
            return [
                "--shared-current-colyseus-dir must be the current renderer "
                f"checkout's server root: expected {expected}"
            ]
    return []


def _normalize_code_refs(value: str) -> list[str]:
    """Parse mandatory `file:line` review references from one CLI value."""
    return [item.strip() for item in value.split(",") if item.strip()]


def _validate_review_label_inputs(args: argparse.Namespace) -> list[str]:
    """Reject incomplete review-label inputs before any capture can start."""
    errors: list[str] = []
    if args.fix_attempt_number <= 0:
        errors.append("--fix-attempt-number must be positive")
    if not args.fix_attempt_description.strip():
        errors.append("--fix-attempt-description must be non-empty")
    code_refs = _normalize_code_refs(args.code_refs)
    if not code_refs:
        errors.append("--code-refs must contain at least one file:line reference")
    for code_ref in code_refs:
        path, separator, line = code_ref.rpartition(":")
        if (
            not separator
            or not path
            or not line.isdigit()
            or int(line) <= 0
        ):
            errors.append(f"--code-refs entry must be file:line: {code_ref}")
    return errors


def _validate_required_commit(
    commit: str,
    required_files: list[str],
    current_project: Path,
) -> list[str]:
    """Require one exact, clean current checkout for every captured pixel.

    Ancestor reachability is insufficient: a later dirty checkout can render
    bytes that the requested commit never contained. The current project's Git
    root therefore has to be clean, its full HEAD has to equal the requested
    object id, and every member of the retained-scene runtime chain has to exist
    in that exact commit.
    """
    errors: list[str] = []
    if (
        len(commit) != 40
        or any(char not in "0123456789abcdef" for char in commit)
    ):
        return ["--required-current-commit must be one full lowercase 40-character Git commit"]
    source_root = _project_root(current_project)
    non_runtime_required = [path for path in required_files if not path.startswith(RUNTIME_PREFIXES)]
    if non_runtime_required:
        errors.append("--required-runtime-files contains non-runtime paths: " + ", ".join(non_runtime_required))
    required_set = set(required_files)
    expected_set = set(FL4431_RUNTIME_SOURCE_FILES)
    missing_required = sorted(expected_set - required_set)
    extra_required = sorted(required_set - expected_set)
    if missing_required:
        errors.append(
            "--required-runtime-files omits FL-4431 runtime sources: "
            + ", ".join(missing_required)
        )
    if extra_required:
        errors.append(
            "--required-runtime-files contains files outside the FL-4431 runtime source set: "
            + ", ".join(extra_required)
        )
    if len(required_files) != len(required_set):
        errors.append("--required-runtime-files contains duplicate paths")
    try:
        observed_head = _git_text("rev-parse", "HEAD", source_root=source_root)
        dirty_text = _git_text(
            "status",
            "--porcelain",
            "--untracked-files=all",
            source_root=source_root,
        )
    except GridInputError as exc:
        errors.append(f"current project is not a readable Git checkout: {exc}")
        return errors
    if observed_head != commit:
        errors.append(
            f"required current commit must equal current HEAD exactly: expected {commit}, observed {observed_head}"
        )
    if dirty_text:
        errors.append(
            "current source tree must be fully clean; dirty paths: "
            + ", ".join(dirty_text.splitlines())
        )
    if not _commit_exists(commit, source_root):
        errors.append(f"required current commit is missing from current checkout: {commit}")
        return errors
    for path in FL4431_RUNTIME_SOURCE_FILES:
        result = subprocess.run(
            ["git", "-C", str(source_root), "cat-file", "-e", f"{commit}:{path}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0:
            errors.append(f"required FL-4431 runtime source is absent from {commit}: {path}")
    return errors


def _number_equal(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= POSE_TOLERANCE
    return a == b


def _value_equal(a: Any, b: Any) -> bool:
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        return all(_value_equal(left, right) for left, right in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        keys = set(a) | set(b)
        return all(_value_equal(a.get(key), b.get(key)) for key in keys)
    return _number_equal(a, b)


def _metadata_value(meta: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        cur: Any = meta
        ok = True
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok:
            return cur
    return None


def _lane_expected_values(lane: dict[str, Any]) -> dict[str, Any]:
    camera_pose = lane.get("camera_pose") if isinstance(lane.get("camera_pose"), dict) else {}
    instance_pose = lane.get("instance_pose") if isinstance(lane.get("instance_pose"), dict) else {}
    expected = {
        "view_mode": lane.get("view_mode") or camera_pose.get("projection"),
        "camera_position": lane.get("camera_position") if "camera_position" in lane else camera_pose.get("position"),
        "camera_yaw": lane.get("camera_yaw") if "camera_yaw" in lane else camera_pose.get("yaw"),
        "camera_pitch": lane.get("camera_pitch") if "camera_pitch" in lane else camera_pose.get("pitch"),
        "camera_roll": lane.get("camera_roll") if "camera_roll" in lane else camera_pose.get("roll"),
        "camera_fov": lane.get("camera_fov") if "camera_fov" in lane else camera_pose.get("fov"),
        "projection_params": lane.get("projection_params") if "projection_params" in lane else camera_pose.get("projection_params"),
        "player_position": lane.get("player_position") if "player_position" in lane else instance_pose.get("player_position"),
        "player_yaw": lane.get("player_yaw") if "player_yaw" in lane else instance_pose.get("player_yaw"),
        "npc_positions": lane.get("npc_positions") if "npc_positions" in lane else instance_pose.get("npc_positions"),
        "airplane_position": lane.get("airplane_position") if "airplane_position" in lane else instance_pose.get("airplane_position"),
        "airplane_yaw": lane.get("airplane_yaw") if "airplane_yaw" in lane else instance_pose.get("airplane_yaw"),
        "world_time": lane.get("world_time"),
        "night_state": lane.get("night_state"),
        "feature_flags": lane.get("feature_flags") if "feature_flags" in lane else lane.get("required_features"),
        "capture_mode": lane.get("capture_mode"),
        # The requested beam value is a relative analyzer interval. Preserve it
        # through the frame-normalization layer so validation never falls back
        # to a lane-level value and never mistakes a requested offset for the
        # runtime's absolute lighthouse orientation.
        "beam_phase_offset_target": lane.get("beam_phase_offset_target"),
    }
    return {key: value for key, value in expected.items() if value is not None}


def _merge_nested_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_nested_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _frame_spec_map(lane: dict[str, Any]) -> dict[str, dict[str, Any]]:
    frames_value = lane.get("frames")
    if not isinstance(frames_value, list) or not frames_value:
        raise GridInputError(f"{lane.get('id', '<missing>')}: frames must be non-empty")
    specs: dict[str, dict[str, Any]] = {}
    for item in frames_value:
        if isinstance(item, str) and item:
            label = item
            spec: dict[str, Any] = {"label": label}
        elif isinstance(item, dict) and isinstance(item.get("label"), str) and item.get("label"):
            label = str(item["label"])
            spec = dict(item)
        else:
            raise GridInputError(f"{lane.get('id', '<missing>')}: frame needs non-empty label")
        if label in specs:
            raise GridInputError(f"{lane.get('id', '<missing>')}: duplicate frame label {label}")
        specs[label] = spec
    return specs


def _frame_expected_values(lane: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    combined = dict(lane)
    for key, value in spec.items():
        if key in {"label", "current_project", "baseline_project", "codebases"}:
            continue
        if key in {"camera_pose", "instance_pose"} and isinstance(value, dict) and isinstance(combined.get(key), dict):
            combined[key] = _merge_nested_dicts(combined[key], value)
        else:
            combined[key] = value
    camera_pose = combined.get("camera_pose") if isinstance(combined.get("camera_pose"), dict) else {}
    camera_aliases = {
        "position": "camera_position",
        "yaw": "camera_yaw",
        "pitch": "camera_pitch",
        "roll": "camera_roll",
        "fov": "camera_fov",
        "projection_params": "projection_params",
        "projection": "view_mode",
    }
    for nested_key, flat_key in camera_aliases.items():
        if nested_key in camera_pose:
            combined[flat_key] = camera_pose[nested_key]
    instance_pose = combined.get("instance_pose") if isinstance(combined.get("instance_pose"), dict) else {}
    instance_aliases = {
        "player_position": "player_position",
        "player_yaw": "player_yaw",
        "npc_positions": "npc_positions",
        "airplane_position": "airplane_position",
        "airplane_yaw": "airplane_yaw",
    }
    for nested_key, flat_key in instance_aliases.items():
        if nested_key in instance_pose:
            combined[flat_key] = instance_pose[nested_key]
    return _lane_expected_values(combined)


def _frame_baseline_name(lane: dict[str, Any], spec: dict[str, Any]) -> str:
    codebases = spec.get("codebases") if isinstance(spec.get("codebases"), dict) else {}
    value = spec.get("baseline_project", codebases.get("baseline", lane.get("baseline")))
    return str(value)


def _frame_current_name(lane: dict[str, Any], spec: dict[str, Any]) -> str:
    codebases = spec.get("codebases") if isinstance(spec.get("codebases"), dict) else {}
    return str(spec.get("current_project", codebases.get("current", lane.get("current_project", "current"))))


def _frame_meta_value(meta: dict[str, Any], key: str) -> Any:
    aliases = {
        "view_mode": ("view_mode", "camera_pose.projection", "projection"),
        "camera_position": ("camera_position", "camera_pose.position", "camera.position"),
        "camera_yaw": ("camera_yaw", "camera_pose.yaw", "camera.yaw"),
        "camera_pitch": ("camera_pitch", "camera_pose.pitch", "camera.pitch"),
        "camera_roll": ("camera_roll", "camera_pose.roll", "camera.roll"),
        "camera_fov": ("camera_fov", "camera_pose.fov", "camera.fov"),
        "projection_params": ("projection_params", "camera_pose.projection_params", "camera.projection_params"),
        "player_position": ("player_position", "instance_pose.player_position", "player.position"),
        "player_yaw": ("player_yaw", "instance_pose.player_yaw", "player.yaw"),
        "npc_positions": ("npc_positions", "instance_pose.npc_positions", "npcs"),
        "airplane_position": ("airplane_position", "instance_pose.airplane_position", "airplane.position"),
        "airplane_yaw": ("airplane_yaw", "instance_pose.airplane_yaw", "airplane.yaw"),
        "world_time": ("world_time", "time.world_time"),
        "night_state": ("night_state", "time.night_state", "night"),
        "feature_flags": ("feature_flags", "required_features", "features"),
        "capture_mode": ("capture_mode", "camera_mode", "capture.camera_mode"),
    }
    return _metadata_value(meta, *aliases.get(key, (key,)))


def _runtime_path_value(meta: dict[str, Any], key: str) -> Path | None:
    value = _metadata_value(meta, key)
    if not isinstance(value, str) or not value:
        return None
    return Path(value).expanduser().resolve()


def _entry_path_and_metadata(entry: Any, base: Path) -> tuple[Path, dict[str, Any]]:
    if isinstance(entry, str):
        path = _resolve_path(entry, base)
        metadata_path = path.with_suffix(path.suffix + ".json")
        return path, _load_json(metadata_path) if metadata_path.exists() else {}
    if not isinstance(entry, dict):
        raise GridInputError(f"frame entry must be a path string plus metadata object: {entry!r}")
    path_value = entry.get("path")
    if not isinstance(path_value, str) or not path_value:
        raise GridInputError(f"frame entry missing path: {entry!r}")
    path = _resolve_path(path_value, base)
    metadata: dict[str, Any] = {}
    metadata_path_value = entry.get("metadata_path")
    if isinstance(metadata_path_value, str) and metadata_path_value:
        metadata = _load_json(_resolve_path(metadata_path_value, base))
    else:
        default_metadata_path = path.with_suffix(path.suffix + ".json")
        if default_metadata_path.exists():
            metadata = _load_json(default_metadata_path)
    inline = entry.get("metadata")
    if isinstance(inline, dict):
        metadata = {**metadata, **inline}
    return path, metadata


def _frame_entry_path(entry: Any, base: Path) -> Path:
    if isinstance(entry, str):
        return _resolve_path(entry, base)
    if not isinstance(entry, dict):
        raise GridInputError(f"frame entry must be a path string plus metadata object: {entry!r}")
    path_value = entry.get("path")
    if not isinstance(path_value, str) or not path_value:
        raise GridInputError(f"frame entry missing path: {entry!r}")
    return _resolve_path(path_value, base)


def _frame_entry_metadata_path(entry: Any, frame_path: Path, base: Path) -> Path:
    if isinstance(entry, dict):
        metadata_path_value = entry.get("metadata_path")
        if isinstance(metadata_path_value, str) and metadata_path_value:
            return _resolve_path(metadata_path_value, base)
    return frame_path.with_suffix(frame_path.suffix + ".json")


def _path_ready_for_validation(entry: Any, base: Path) -> bool:
    frame_path = _frame_entry_path(entry, base)
    if not frame_path.exists():
        return False
    if isinstance(entry, dict) and isinstance(entry.get("metadata"), dict) and entry.get("metadata"):
        return True
    return _frame_entry_metadata_path(entry, frame_path, base).exists()


def _command_from_template(command: Any, values: dict[str, str]) -> list[str]:
    if not isinstance(command, list) or not command:
        raise GridInputError("capture_command must be a non-empty argv list")
    rendered: list[str] = []
    for part in command:
        if not isinstance(part, str):
            raise GridInputError(f"capture_command argv entries must be strings: {command!r}")
        text = part
        for key, value in values.items():
            text = text.replace("{" + key + "}", value)
        rendered.append(text)
    return rendered


def _template_values(args: argparse.Namespace, manifest_path: Path) -> dict[str, str]:
    out_dir = Path(args.out_dir).resolve()
    return {
        "current_project": str(Path(args.current_project).resolve()),
        "baseline_a_project": str(Path(args.baseline_a_project).resolve()),
        "out_dir": str(out_dir),
        "manifest": str(manifest_path),
        "required_current_commit": str(args.required_current_commit),
        # Keep the caller's absolute spelling unchanged. The adapter validates
        # and fingerprints it; both renderer arms must receive this exact value.
        "shared_current_colyseus_dir": args.shared_current_colyseus_dir,
    }


def _expand_template_text(value: str, values: dict[str, str]) -> str:
    text = value
    for key, replacement in values.items():
        text = text.replace("{" + key + "}", replacement)
    return text


def _expand_frame_path_templates(manifest: dict[str, Any], args: argparse.Namespace, manifest_path: Path) -> None:
    values = _template_values(args, manifest_path)
    lanes = manifest.get("lanes")
    if not isinstance(lanes, list):
        return
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        for field_name in ("required_current_frame_paths", "required_baseline_frame_paths"):
            entries = lane.get(field_name)
            if isinstance(entries, dict):
                iterable = entries.values()
            elif isinstance(entries, list):
                iterable = entries
            else:
                continue
            for entry in iterable:
                if not isinstance(entry, dict):
                    continue
                for key in ("path", "metadata_path"):
                    if isinstance(entry.get(key), str):
                        entry[key] = _expand_template_text(entry[key], values)


def _receipt_route_manifest_errors(
    manifest: dict[str, Any],
    args: argparse.Namespace,
) -> list[str]:
    """Bind the effective night manifest to the validated v2 capture contract."""

    receipt = getattr(args, "baseline_receipt_context", None)
    if not isinstance(receipt, dict):
        return ["validated Baseline A v2 receipt context is missing"]
    contract = receipt["capture_contract"]
    route = contract["route"]
    lanes = manifest.get("lanes")
    if not isinstance(lanes, list):
        return ["capture manifest lanes are missing"]
    matches = [
        lane
        for lane in lanes
        if isinstance(lane, dict) and lane.get("id") == route["lane_id"]
    ]
    if len(matches) != 1:
        return [
            "capture manifest must contain exactly one lane named by "
            "receipt.capture_contract.route.lane_id"
        ]
    lane = matches[0]
    try:
        specs = _frame_spec_map(lane)
    except GridInputError as exc:
        return [str(exc)]
    expected_labels = list(route["frame_labels"])
    errors: list[str] = []
    if list(specs) != expected_labels:
        errors.append("manifest frame labels differ from Baseline receipt route")
        return errors
    if [specs[label].get("route_tick") for label in expected_labels] != list(
        route["target_ticks"]
    ):
        errors.append("manifest route ticks differ from Baseline receipt route")
    dimensions = contract["dimensions"]
    if (
        int(dimensions["requested_width"]) != 3840
        or int(dimensions["requested_height"]) != 2160
    ):
        errors.append("Baseline receipt requested dimensions differ from grid capture command")
    if bool(dimensions["fullscreen"]) != bool(args.fullscreen_required):
        errors.append("Baseline receipt fullscreen admission differs from grid request")
    if bool(dimensions["ascii"]) != bool(args.ascii_required):
        errors.append("Baseline receipt ASCII admission differs from grid request")
    night_hour = float(contract["time_profile"]["night_world_hour"])
    lane_world_time = lane.get("world_time")
    if not isinstance(lane_world_time, (int, float)) or abs(
        float(lane_world_time) - night_hour
    ) > POSE_TOLERANCE:
        errors.append("manifest night world time differs from Baseline receipt")
    receipt_features = set(str(value) for value in contract["features"])
    lane_features = lane.get("feature_flags")
    if (
        not isinstance(lane_features, list)
        or not set(str(value) for value in lane_features).issubset(receipt_features)
    ):
        errors.append("manifest feature flags are not admitted by Baseline receipt")

    poses = route["poses"]
    if not isinstance(poses, list) or len(poses) != len(expected_labels):
        errors.append("Baseline receipt route poses are incomplete")
        return errors

    vector_fields = (
        ("camera_position", "camera_position"),
        ("player_position", "player_position"),
        ("airplane_position", "airplane_position"),
    )
    scalar_fields = (
        ("camera_yaw", "camera_yaw"),
        ("camera_pitch", "camera_pitch"),
        ("camera_roll", "camera_roll"),
        ("player_yaw", "player_yaw"),
        ("airplane_yaw", "airplane_yaw"),
        ("camera_fov", "fov_degrees"),
    )
    for index, label in enumerate(expected_labels):
        expected = _frame_expected_values(lane, specs[label])
        pose = poses[index]
        if pose.get("label") != label or pose.get("route_tick") != route["target_ticks"][index]:
            errors.append(f"{label}: Baseline receipt pose identity differs from route")
            continue
        if expected.get("view_mode") != pose.get("projection"):
            errors.append(f"{label}: projection differs from Baseline receipt pose")
        for expected_key, receipt_key in vector_fields:
            expected_value = expected.get(expected_key)
            receipt_value = pose.get(receipt_key)
            if (
                not isinstance(expected_value, list)
                or not isinstance(receipt_value, list)
                or len(expected_value) != 3
                or len(receipt_value) != 3
                or max(
                    abs(float(expected_value[axis]) - float(receipt_value[axis]))
                    for axis in range(3)
                )
                > RUNTIME_POSE_TOLERANCE
            ):
                errors.append(
                    f"{label}: {expected_key} differs from Baseline receipt pose"
                )
        for expected_key, receipt_key in scalar_fields:
            try:
                delta = abs(float(expected[expected_key]) - float(pose[receipt_key]))
            except (KeyError, TypeError, ValueError):
                delta = math.inf
            if delta > RUNTIME_POSE_TOLERANCE:
                errors.append(
                    f"{label}: {expected_key} differs from Baseline receipt pose"
                )
        projection_params = expected.get("projection_params")
        if not isinstance(projection_params, dict):
            errors.append(f"{label}: projection parameters are missing")
        else:
            for key in ("near", "far"):
                try:
                    delta = abs(float(projection_params[key]) - float(pose[key]))
                except (KeyError, TypeError, ValueError):
                    delta = math.inf
                if delta > POSE_TOLERANCE:
                    errors.append(
                        f"{label}: projection {key} differs from Baseline receipt pose"
                    )
    return errors


def _capture_command_for_frame(lane: dict[str, Any], side: str, frame_label: str, entry: Any) -> Any:
    if isinstance(entry, dict) and "capture_command" in entry:
        return entry["capture_command"]
    frame_commands = lane.get("capture_commands")
    if isinstance(frame_commands, dict):
        side_commands = frame_commands.get(side)
        if isinstance(side_commands, dict) and frame_label in side_commands:
            return side_commands[frame_label]
        if side in frame_commands and isinstance(frame_commands[side], list):
            return frame_commands[side]
    side_key = f"{side}_capture_command"
    if side_key in lane:
        return lane[side_key]
    if "capture_command" in lane:
        return lane["capture_command"]
    return _default_actual_godot_capture_command(lane, frame_label)


def _default_actual_godot_capture_command(lane: dict[str, Any], frame_label: str) -> list[str]:
    expected = _frame_expected_values(lane, _frame_spec_map(lane)[frame_label])
    camera_mode = str(expected.get("capture_mode") or lane.get("id") or expected.get("view_mode"))
    capture_sequence = str(lane.get("capture_sequence") or "single_frame")
    night_state = expected.get("night_state") is True
    beam_expected_in_png = bool(lane.get("beam_expected_in_png"))
    frame_specs = _frame_spec_map(lane)
    frame_spec = frame_specs[frame_label]
    if isinstance(frame_spec.get("beam_expected_in_png"), bool):
        beam_expected_in_png = bool(frame_spec["beam_expected_in_png"])
    command = [
        sys.executable,
        str(ROOT / "scripts" / "fl4408_actual_godot_capture_adapter.py"),
        "--out-png",
        "{out_png}",
        "--out-metadata",
        "{out_metadata}",
        "--lane-id",
        "{lane_id}",
        "--side",
        "{side}",
        "--frame",
        "{frame}",
        "--project",
        "{project}",
        "--colyseus-dir",
        "{shared_current_colyseus_dir}",
        "--capture-root",
        "{out_dir}/captures",
        "--capture-sequence",
        capture_sequence,
        "--camera-mode",
        camera_mode,
        "--view-mode",
        str(expected["view_mode"]),
        "--camera-position",
        json.dumps(expected["camera_position"]),
        "--camera-yaw",
        str(expected["camera_yaw"]),
        "--camera-pitch",
        str(expected["camera_pitch"]),
        "--camera-roll",
        str(expected["camera_roll"]),
        "--camera-fov",
        str(expected["camera_fov"]),
        "--projection-params",
        json.dumps(expected["projection_params"]),
        "--player-position",
        json.dumps(expected["player_position"]),
        "--player-yaw",
        str(expected["player_yaw"]),
        "--npc-positions",
        json.dumps(expected["npc_positions"]),
        "--airplane-position",
        json.dumps(expected["airplane_position"]),
        "--airplane-yaw",
        str(expected["airplane_yaw"]),
        "--world-time",
        str(expected["world_time"]),
        "--feature-flags",
        json.dumps(expected["feature_flags"]),
        "--resolution",
        "3840x2160",
        "--godot-timeout",
        "900.0",
    ]
    if night_state:
        command.append("--night-state")
    if beam_expected_in_png:
        command.append("--beam-expected-in-png")
    command.append("--fullscreen")
    return command


def _capture_missing_frames(manifest: dict[str, Any], args: argparse.Namespace, manifest_path: Path) -> list[str]:
    if not args.run_captures:
        return []
    lanes = manifest.get("lanes")
    if not isinstance(lanes, list):
        return ["manifest lanes missing before capture"]
    errors: list[str] = []
    current_project_path = Path(args.current_project).resolve()
    current_root = _project_root(current_project_path)
    baseline_roots = {
        "baseline_a": _project_root(Path(args.baseline_a_project).resolve()),
    }
    baseline_projects = {
        "baseline_a": str((baseline_roots["baseline_a"] / "godot_project").resolve()),
    }
    out_dir = Path(args.out_dir).resolve()
    for lane in lanes:
        if not isinstance(lane, dict):
            continue
        lane_id = str(lane.get("id", "<missing>"))
        try:
            frame_specs = _frame_spec_map(lane)
        except GridInputError as exc:
            errors.append(str(exc))
            continue
        current_entries = lane.get("required_current_frame_paths")
        baseline_entries = lane.get("required_baseline_frame_paths")
        if not isinstance(current_entries, dict) or not isinstance(baseline_entries, dict):
            continue
        capture_sequence = str(lane.get("capture_sequence") or "single_frame")
        side_work: list[tuple[str, str, dict[str, Any], Path, str, str]] = []
        for side in ("current", "baseline"):
            entries = current_entries if side == "current" else baseline_entries
            pending_labels: list[str] = []
            side_baselines: dict[str, str] = {}
            for frame_label, spec in frame_specs.items():
                if frame_label not in entries:
                    continue
                baseline_name = _frame_baseline_name(lane, spec)
                if baseline_name not in baseline_roots:
                    continue
                side_baselines[frame_label] = baseline_name
                try:
                    if not _path_ready_for_validation(entries[frame_label], current_root if side == "current" else baseline_roots[baseline_name]):
                        pending_labels.append(frame_label)
                except GridInputError as exc:
                    errors.append(f"{lane_id}:{side}:{frame_label}: {exc}")
                    return errors
            if not pending_labels:
                continue
            if capture_sequence in BATCHED_CAPTURE_SEQUENCES:
                representative_label = pending_labels[0]
                baseline_name = side_baselines[representative_label]
                side_work.append((side, representative_label, entries, current_root if side == "current" else baseline_roots[baseline_name], str(current_project_path) if side == "current" else baseline_projects[baseline_name], "current" if side == "current" else baseline_name))
            else:
                for frame_label in pending_labels:
                    baseline_name = side_baselines[frame_label]
                    side_work.append((side, frame_label, entries, current_root if side == "current" else baseline_roots[baseline_name], str(current_project_path) if side == "current" else baseline_projects[baseline_name], "current" if side == "current" else baseline_name))
        for side, frame_label, entries, base, project, codebase in side_work:
            baseline_name = codebase if side == "baseline" else _frame_baseline_name(lane, frame_specs[frame_label])
            entry = entries[frame_label]
            try:
                frame_path = _frame_entry_path(entry, base)
                metadata_path = _frame_entry_metadata_path(entry, frame_path, base)
                command_template = _capture_command_for_frame(lane, side, frame_label, entry)
                if command_template is None:
                    errors.append(f"{lane_id}:{side}:{frame_label}: missing capture_command for automatic capture")
                    continue
                values = {
                    "current_project": str(Path(args.current_project).resolve()),
                    "baseline_a_project": str(Path(args.baseline_a_project).resolve()),
                    "project": project,
                    "side": side,
                    "baseline": baseline_name,
                    "codebase": codebase,
                    "lane_id": lane_id,
                    "frame": frame_label,
                    "frame_label": frame_label,
                    "out_png": str(frame_path),
                    "out_metadata": str(metadata_path),
                    "out_dir": str(out_dir),
                    "manifest": str(manifest_path),
                    "required_current_commit": str(args.required_current_commit),
                    "shared_current_colyseus_dir": args.shared_current_colyseus_dir,
                }
                command = _command_from_template(command_template, values)
                if args.require_actual_godot_capture:
                    forwarded_server = _command_arg(command, "--colyseus-dir")
                    if forwarded_server != args.shared_current_colyseus_dir:
                        errors.append(
                            f"{lane_id}:{side}:{frame_label}: capture command must "
                            "forward --shared-current-colyseus-dir unchanged"
                        )
                        return errors
                print("GODOT_CANONICAL_GRID_CAPTURE " + json.dumps({"lane": lane_id, "side": side, "frame": frame_label, "sequence": capture_sequence, "cmd": command}), flush=True)
                result = subprocess.run(command, cwd=str(ROOT), text=True, capture_output=True, check=False)
                if result.returncode != 0:
                    errors.append(
                        f"{lane_id}:{side}:{frame_label}: capture command failed rc={result.returncode}: "
                        f"{result.stderr.strip() or result.stdout.strip()}"
                    )
                    return errors
                labels_to_check = list(frame_specs) if capture_sequence in BATCHED_CAPTURE_SEQUENCES else [frame_label]
                for required_label in labels_to_check:
                    required_entry = entries[required_label]
                    required_baseline = _frame_baseline_name(lane, frame_specs[required_label])
                    required_base = current_root if side == "current" else baseline_roots[required_baseline]
                    required_path = _frame_entry_path(required_entry, required_base)
                    required_metadata = _frame_entry_metadata_path(required_entry, required_path, required_base)
                    if not required_path.exists():
                        errors.append(f"{lane_id}:{side}:{required_label}: capture command did not write frame {required_path}")
                        return errors
                    if not required_metadata.exists() and not (isinstance(required_entry, dict) and isinstance(required_entry.get("metadata"), dict) and required_entry.get("metadata")):
                        errors.append(f"{lane_id}:{side}:{required_label}: capture command did not write metadata {required_metadata}")
                        return errors
            except GridInputError as exc:
                errors.append(f"{lane_id}:{side}:{frame_label}: {exc}")
                return errors
    return errors


def _normalize_frame_entries(value: Any, labels: list[str], base: Path | dict[str, Path], field_name: str, paths_are_capture_destinations: bool) -> dict[str, FrameEvidence]:
    if not isinstance(value, (dict, list)):
        raise GridInputError(f"{field_name} must be object keyed by frame label")
    raw_by_label: dict[str, Any] = {}
    if isinstance(value, dict):
        raw_by_label = dict(value)
    else:
        for item in value:
            if not isinstance(item, dict) or "label" not in item:
                raise GridInputError(f"{field_name} list entries need label: {item!r}")
            raw_by_label[str(item["label"])] = item
    missing = [label for label in labels if label not in raw_by_label]
    if missing:
        raise GridInputError(f"{field_name} missing frame labels: {', '.join(missing)}")
    extras = sorted(set(raw_by_label) - set(labels))
    if extras:
        raise GridInputError(f"{field_name} has undeclared frame labels: {', '.join(extras)}")
    frames: dict[str, FrameEvidence] = {}
    for label in labels:
        entry_base = base[label] if isinstance(base, dict) else base
        path, metadata = _entry_path_and_metadata(raw_by_label[label], entry_base)
        if not path.exists():
            if paths_are_capture_destinations:
                raise GridInputError(f"{field_name} capture destination was not written: {path}")
            raise GridInputError(f"{field_name} pre-existing frame input missing: {path}")
        try:
            with Image.open(path) as img:
                size = img.size
        except Exception as exc:
            raise GridInputError(f"{field_name} frame is not readable image: {path}: {exc}") from exc
        frames[label] = FrameEvidence(label=label, path=path, metadata=metadata, sha256=_sha256(path), size=size)
    return frames


def _validate_frame_metadata(
    lane_id: str,
    side: str,
    frame: FrameEvidence,
    expected: dict[str, Any],
    ascii_required: bool,
    fullscreen_required: bool,
    actual_godot_required: bool,
    min_fullscreen_size: tuple[int, int],
    capture_sequence: str,
) -> list[str]:
    errors: list[str] = []
    meta = frame.metadata
    if not meta:
        errors.append(f"{lane_id}:{side}:{frame.label}: missing frame metadata")
        return errors
    if ascii_required:
        ascii_value = _metadata_value(meta, "ascii_enabled", "ascii", "features.ascii")
        if ascii_value is not True:
            errors.append(f"{lane_id}:{side}:{frame.label}: ASCII is off or unproven")
    if fullscreen_required:
        fullscreen_value = _metadata_value(meta, "fullscreen", "window.fullscreen", "capture.fullscreen")
        if fullscreen_value is not True:
            errors.append(f"{lane_id}:{side}:{frame.label}: fullscreen is false or unproven")
        if frame.size[0] < min_fullscreen_size[0] or frame.size[1] < min_fullscreen_size[1]:
            errors.append(
                f"{lane_id}:{side}:{frame.label}: image size {frame.size[0]}x{frame.size[1]} below fullscreen minimum "
                f"{min_fullscreen_size[0]}x{min_fullscreen_size[1]}"
            )
    if actual_godot_required:
        if _metadata_value(meta, "actual_godot_capture", "capture.actual_godot_capture") is not True:
            errors.append(f"{lane_id}:{side}:{frame.label}: actual Godot runtime capture is missing")
        receipt_path = _runtime_path_value(meta, "receipt_path")
        source_frame = _runtime_path_value(meta, "source_frame")
        complete_path = _runtime_path_value(meta, "complete_capture_path")
        if receipt_path is None or not receipt_path.exists():
            errors.append(f"{lane_id}:{side}:{frame.label}: actual Godot receipt is missing")
        if source_frame is None or not source_frame.exists():
            errors.append(f"{lane_id}:{side}:{frame.label}: actual Godot source frame is missing")
        elif (
            capture_sequence != STATIC_TEMPORAL_SEQUENCE
            and source_frame.name != f"{frame.label}.png"
        ):
            errors.append(f"{lane_id}:{side}:{frame.label}: source frame label does not match requested frame")
        if source_frame is not None and source_frame.exists() and _sha256(source_frame) != frame.sha256:
            errors.append(f"{lane_id}:{side}:{frame.label}: output PNG is not the recorded runtime source frame")
        if complete_path is None or not complete_path.exists():
            errors.append(f"{lane_id}:{side}:{frame.label}: complete actual capture receipt is missing")
        if _runtime_vector(meta, "runtime_player_position", "runtime.player_position") is None:
            errors.append(f"{lane_id}:{side}:{frame.label}: runtime player position from actual receipt is missing")
        if _runtime_vector(meta, "runtime_camera_position", "runtime.camera_position") is None:
            errors.append(f"{lane_id}:{side}:{frame.label}: runtime camera position from actual receipt is missing")
        # A valid RenderingDevice handle is not a readback of the compositor's
        # final pixels. The actual source PNG and its matching hash below are
        # the machine-checkable capture surface; visual correctness still
        # requires direct review and operator signoff.
        ansi_size = _metadata_value(meta, "ansi_size", "final_render_metadata.ansi_size")
        if not _dict_has_positive_keys(ansi_size, ("w", "h")):
            errors.append(f"{lane_id}:{side}:{frame.label}: ansi_size readback metadata is missing")
        cell_dims = _metadata_value(meta, "cell_dims", "final_render_metadata.cell_dims")
        if not _dict_has_positive_keys(cell_dims, ("cell_w", "cell_h", "glyph_w", "glyph_h")):
            errors.append(f"{lane_id}:{side}:{frame.label}: cell_dims readback metadata is missing")
        viewport_size = _metadata_value(meta, "viewport_size", "final_render_metadata.viewport_size")
        if not _dict_has_positive_keys(viewport_size, ("w", "h")):
            errors.append(f"{lane_id}:{side}:{frame.label}: viewport_size readback metadata is missing")
        display = _metadata_value(meta, "display", "final_render_metadata.display")
        if not isinstance(display, dict) or not isinstance(display.get("window_size"), dict):
            errors.append(f"{lane_id}:{side}:{frame.label}: display readback metadata is missing")
        if "foliage" in lane_id:
            foliage_state = _metadata_value(meta, "foliage_mask_state", "final_render_metadata.foliage_mask_state")
            if foliage_state != "sampled":
                errors.append(f"{lane_id}:{side}:{frame.label}: foliage_mask_state is not sampled")
        if capture_sequence == STATIC_TEMPORAL_SEQUENCE:
            if meta.get("canonical_review_capture") is not True:
                errors.append(f"{lane_id}:{side}:{frame.label}: temporal lane is not a canonical review capture")
            if meta.get("temporal_continuous_run") is not True:
                errors.append(f"{lane_id}:{side}:{frame.label}: temporal lane is not one continuous run")
            if not isinstance(meta.get("temporal_capture_id"), str) or not meta.get("temporal_capture_id"):
                errors.append(f"{lane_id}:{side}:{frame.label}: temporal capture id is missing")
            if not isinstance(meta.get("temporal_frame_index"), int):
                errors.append(f"{lane_id}:{side}:{frame.label}: temporal frame index is missing")
            if not _positive_number(meta.get("temporal_elapsed_seconds")):
                errors.append(f"{lane_id}:{side}:{frame.label}: temporal elapsed time is missing")
            route_tick = meta.get("route_tick")
            authority_tick = meta.get("authority_server_tick")
            if not isinstance(route_tick, int) or isinstance(route_tick, bool) or route_tick < 0:
                errors.append(f"{lane_id}:{side}:{frame.label}: exact route tick is missing")
            if (
                not isinstance(authority_tick, int)
                or isinstance(authority_tick, bool)
            ):
                errors.append(f"{lane_id}:{side}:{frame.label}: authority server tick is missing")
            if meta.get("png_capture_owner") != NIGHT_CAPTURE_OWNER:
                errors.append(f"{lane_id}:{side}:{frame.label}: PNG owner is not the dogfight process")
            if meta.get("png_captured_in_same_process") is not True:
                errors.append(f"{lane_id}:{side}:{frame.label}: same-process PNG truth is unproven")
            airplane_position = _runtime_vector(
                meta,
                "runtime_airplane_position",
                "runtime.airplane_position",
                "airplane_position",
            )
            if airplane_position is None or airplane_position[1] <= 0.0:
                errors.append(f"{lane_id}:{side}:{frame.label}: airborne airplane position is unproven")
    sequence_runtime_owned_keys = {"camera_position", "player_position", "airplane_position"}
    for key, value in expected.items():
        if capture_sequence == "movement_sequence" and key in sequence_runtime_owned_keys:
            continue
        actual = _frame_meta_value(meta, key)
        if actual is None:
            errors.append(f"{lane_id}:{side}:{frame.label}: missing {key}")
        elif not _value_equal(actual, value):
            errors.append(f"{lane_id}:{side}:{frame.label}: {key} differs from lane expected")
    return errors


def _full_lower_hex(value: Any, length: int) -> bool:
    """Return true only for a complete lowercase hexadecimal identity."""
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _shared_server_identity_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Extract the complete server facts plus exact requested-root spelling."""
    value = metadata.get("shared_current_server_identity")
    return dict(value) if isinstance(value, dict) else {}


def _validate_shared_server_frame(
    lane_id: str,
    side: str,
    frame: FrameEvidence,
    shared_current_colyseus_dir: str,
    required_current_commit: str,
) -> list[str]:
    """Validate one frame's explicit shared-current-server provenance."""
    errors: list[str] = []
    metadata = frame.metadata
    identity = _shared_server_identity_from_metadata(metadata)
    if not identity:
        return [
            f"{lane_id}:{side}:{frame.label}: shared current server identity is absent"
        ]
    for field in SHARED_SERVER_IDENTITY_FIELDS:
        if field not in identity:
            errors.append(
                f"{lane_id}:{side}:{frame.label}: shared server identity missing {field}"
            )
        elif metadata.get(field) != identity.get(field):
            errors.append(
                f"{lane_id}:{side}:{frame.label}: top-level {field} differs from nested identity"
            )
    expected_label = RENDERER_ARM_LABELS[side]
    if metadata.get("renderer_arm_label") != expected_label:
        errors.append(
            f"{lane_id}:{side}:{frame.label}: renderer arm label must be exactly {expected_label}"
        )
    if identity.get("server_requested_root") != shared_current_colyseus_dir:
        errors.append(
            f"{lane_id}:{side}:{frame.label}: shared server argument was not forwarded unchanged"
        )
    expected_root = str(Path(shared_current_colyseus_dir).resolve())
    if identity.get("server_source_root") != expected_root:
        errors.append(
            f"{lane_id}:{side}:{frame.label}: shared server source root differs from the builder input"
        )
    if not _full_lower_hex(identity.get("server_commit"), 40):
        errors.append(
            f"{lane_id}:{side}:{frame.label}: full shared server commit is absent"
        )
    elif identity.get("server_commit") != required_current_commit:
        errors.append(
            f"{lane_id}:{side}:{frame.label}: shared server commit differs from current renderer commit"
        )
    dirty_paths = identity.get("server_dirty_paths")
    if not isinstance(dirty_paths, list) or not all(
        isinstance(path, str) and path for path in dirty_paths
    ):
        errors.append(
            f"{lane_id}:{side}:{frame.label}: shared server dirty paths are absent"
        )
    for field in ("server_runtime_scope_fingerprint", "proof_tool_sha256"):
        if not _full_lower_hex(identity.get(field), 64):
            errors.append(
                f"{lane_id}:{side}:{frame.label}: {field} is absent"
            )
    return errors


def _validate_shared_server_pairing(
    lane: dict[str, Any],
    current: dict[str, FrameEvidence],
    baseline: dict[str, FrameEvidence],
) -> list[str]:
    """Require every grid cell to name one identical shared server identity."""
    lane_id = str(lane.get("id", "<missing>"))
    errors: list[str] = []
    canonical_identity: dict[str, Any] | None = None
    canonical_location = ""
    for side, frames in (("baseline", baseline), ("current", current)):
        for label, frame in frames.items():
            identity = _shared_server_identity_from_metadata(frame.metadata)
            if not identity:
                continue
            if canonical_identity is None:
                canonical_identity = identity
                canonical_location = f"{side}:{label}"
            elif identity != canonical_identity:
                errors.append(
                    f"{lane_id}:{side}:{label}: shared server identity differs "
                    f"from {canonical_location}"
                )
    return errors


def _runtime_vector(meta: dict[str, Any], *keys: str) -> list[float] | None:
    value = _metadata_value(meta, *keys)
    if not isinstance(value, list) or len(value) != 3:
        return None
    if not all(isinstance(item, (int, float)) for item in value):
        return None
    return [float(item) for item in value]


def _dict_has_positive_keys(value: Any, keys: tuple[str, ...]) -> bool:
    if not isinstance(value, dict):
        return False
    for key in keys:
        item = value.get(key)
        if not isinstance(item, (int, float)) or float(item) <= 0.0:
            return False
    return True


def _runtime_vectors_close(left: list[float], right: list[float]) -> bool:
    return all(abs(a - b) <= RUNTIME_POSE_TOLERANCE for a, b in zip(left, right))


def _positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and float(value) > 0.0


def _sequence_runtime_reuse_valid(frames: dict[str, FrameEvidence], previous_label: str, label: str) -> bool:
    previous = frames[previous_label]
    current = frames[label]
    previous_source = _runtime_path_value(previous.metadata, "source_frame")
    current_source = _runtime_path_value(current.metadata, "source_frame")
    if previous_source is None or current_source is None or previous_source == current_source:
        return False
    previous_delta = _metadata_value(previous.metadata, "movement_delta", "camera_delta", "airplane_dynamic_face_count")
    current_delta = _metadata_value(current.metadata, "movement_delta", "camera_delta", "airplane_dynamic_face_count")
    return _positive_number(previous_delta) and _positive_number(current_delta)


def _temporal_runtime_reuse_valid(frames: dict[str, FrameEvidence], previous_label: str, label: str) -> bool:
    """Prove two receipt references came from distinct airborne route ticks."""
    previous = frames[previous_label]
    current = frames[label]
    previous_source = _runtime_path_value(previous.metadata, "source_frame")
    current_source = _runtime_path_value(current.metadata, "source_frame")
    if previous_source is None or current_source is None or previous_source == current_source:
        return False
    previous_capture_id = previous.metadata.get("temporal_capture_id")
    current_capture_id = current.metadata.get("temporal_capture_id")
    if not isinstance(previous_capture_id, str) or previous_capture_id != current_capture_id:
        return False
    previous_index = previous.metadata.get("temporal_frame_index")
    current_index = current.metadata.get("temporal_frame_index")
    if not isinstance(previous_index, int) or not isinstance(current_index, int) or current_index <= previous_index:
        return False
    previous_elapsed = previous.metadata.get("temporal_elapsed_seconds")
    current_elapsed = current.metadata.get("temporal_elapsed_seconds")
    if not isinstance(previous_elapsed, (int, float)) or not isinstance(current_elapsed, (int, float)):
        return False
    if float(current_elapsed) <= float(previous_elapsed):
        return False
    previous_tick = previous.metadata.get("route_tick")
    current_tick = current.metadata.get("route_tick")
    if (
        not isinstance(previous_tick, int)
        or isinstance(previous_tick, bool)
        or not isinstance(current_tick, int)
        or isinstance(current_tick, bool)
        or current_tick <= previous_tick
    ):
        return False
    return True


def _shared_capture_reuse_valid(
    capture_sequence: str,
    frames: dict[str, FrameEvidence],
    previous_label: str,
    label: str,
) -> bool:
    if capture_sequence == "movement_sequence":
        return _sequence_runtime_reuse_valid(frames, previous_label, label)
    if capture_sequence == STATIC_TEMPORAL_SEQUENCE:
        return _temporal_runtime_reuse_valid(frames, previous_label, label)
    return False


def _validate_lane_pairing(
    lane: dict[str, Any],
    current: dict[str, FrameEvidence],
    baseline: dict[str, FrameEvidence],
    expected_by_label: dict[str, dict[str, Any]],
) -> list[str]:
    lane_id = str(lane.get("id", "<missing>"))
    capture_sequence = str(lane.get("capture_sequence") or "single_frame")
    errors: list[str] = []
    for label, expected in expected_by_label.items():
        cur = current[label]
        base = baseline[label]
        for key in expected:
            if capture_sequence == "movement_sequence" and key in {"camera_position", "player_position", "airplane_position"}:
                continue
            cur_value = _frame_meta_value(cur.metadata, key)
            base_value = _frame_meta_value(base.metadata, key)
            if cur_value is None or base_value is None:
                continue
            if not _value_equal(cur_value, base_value):
                errors.append(f"{lane_id}:{label}: current and baseline {key} differ")
        if capture_sequence != "movement_sequence":
            runtime_pairs = (
                ("runtime_player_position", ("runtime_player_position", "runtime.player_position")),
                ("runtime_camera_position", ("runtime_camera_position", "runtime.camera_position")),
                ("runtime_airplane_position", ("runtime_airplane_position", "runtime.airplane_position")),
            )
            for field_name, keys in runtime_pairs:
                cur_runtime = _runtime_vector(cur.metadata, *keys)
                base_runtime = _runtime_vector(base.metadata, *keys)
                if cur_runtime is None or base_runtime is None:
                    continue
                if not _runtime_vectors_close(cur_runtime, base_runtime):
                    errors.append(f"{lane_id}:{label}: current and baseline {field_name} differ")
    for side, frames in (("current", current), ("baseline", baseline)):
        seen: dict[str, str] = {}
        seen_runtime_captures: dict[str, str] = {}
        for label, frame in frames.items():
            previous = seen.get(frame.sha256)
            if previous is not None:
                if (
                    capture_sequence == STATIC_TEMPORAL_SEQUENCE
                    or not _shared_capture_reuse_valid(capture_sequence, frames, previous, label)
                ):
                    errors.append(f"{lane_id}:{side}: frame reused as placeholder: {previous} and {label}")
            seen[frame.sha256] = label
            complete_path = _runtime_path_value(frame.metadata, "complete_capture_path")
            signature_hash = _metadata_value(frame.metadata, "capture_signature_hash")
            if complete_path is not None:
                runtime_key = str(complete_path)
                if isinstance(signature_hash, str) and signature_hash:
                    runtime_key = f"{runtime_key}#{signature_hash}"
                previous_runtime = seen_runtime_captures.get(runtime_key)
                if previous_runtime is not None:
                    if not _shared_capture_reuse_valid(capture_sequence, frames, previous_runtime, label):
                        errors.append(f"{lane_id}:{side}: runtime capture reused across frame labels: {previous_runtime} and {label}")
                seen_runtime_captures[runtime_key] = label
    return errors


def _lane_pose_signature(record: dict[str, Any]) -> str:
    expected = record["expected"]
    signature = {
        key: expected.get(key)
        for key in (
            "view_mode",
            "capture_mode",
            "camera_position",
            "camera_yaw",
            "camera_pitch",
            "camera_roll",
            "camera_fov",
            "projection_params",
            "player_position",
            "player_yaw",
            "npc_positions",
            "airplane_position",
            "airplane_yaw",
            "world_time",
            "night_state",
            "feature_flags",
        )
    }
    return json.dumps(signature, sort_keys=True, separators=(",", ":"))


def _validate_canonical_matrix(normalized: list[dict[str, Any]], require_canonical_lanes: bool) -> list[str]:
    errors: list[str] = []
    if require_canonical_lanes:
        pose_owner: dict[str, str] = {}
        for record in normalized:
            lane_id = str(record["lane"].get("id", "<missing>"))
            capture_sequence = str(record["lane"].get("capture_sequence") or "single_frame")
            for label, expected in record["expected_by_label"].items():
                signature = _lane_pose_signature({"expected": expected})
                previous = pose_owner.get(signature)
                owner = f"{lane_id}:{label}"
                if previous is not None:
                    previous_lane = previous.split(":", 1)[0]
                    same_temporal_lane = (
                        capture_sequence == STATIC_TEMPORAL_SEQUENCE
                        and previous_lane == lane_id
                    )
                    if not same_temporal_lane:
                        errors.append(f"canonical frames share the same pose package: {previous} and {owner}")
                pose_owner[signature] = owner
    return errors


def _validate_beam_lane(
    lane: dict[str, Any],
    current: dict[str, FrameEvidence],
    baseline: dict[str, FrameEvidence],
    expected_by_label: dict[str, dict[str, Any]],
) -> list[str]:
    bug_refs = [str(item) for item in lane.get("bug_refs", [])]
    lane_id = str(lane.get("id", ""))
    if "FL-4360" not in bug_refs and "beam" not in lane_id:
        return []
    errors: list[str] = []
    capture_sequence = str(lane.get("capture_sequence") or "single_frame")
    if capture_sequence != STATIC_TEMPORAL_SEQUENCE:
        errors.append(f"{lane_id}: night lane must use one airborne temporal sequence")
    expected_labels = list(NIGHT_AIRBORNE_FRAME_LABELS)
    if (
        list(expected_by_label) != expected_labels
        or list(current) != expected_labels
        or list(baseline) != expected_labels
    ):
        errors.append(
            f"{lane_id}: night lane must declare exactly beam_t0, beam_t1, beam_t2"
        )
    for label, expected in expected_by_label.items():
        if expected.get("night_state") is not True:
            errors.append(f"{lane_id}:{label}: night beam frame is not declared as night")
        world_time = expected.get("world_time")
        if isinstance(world_time, (int, float)) and not (float(world_time) >= 18.0 or float(world_time) <= 5.5):
            errors.append(f"{lane_id}:{label}: night beam frame world_time is not night")
        if label not in expected_labels:
            continue
        expected_offset = expected.get("beam_phase_offset_target")
        offset_index = expected_labels.index(label)
        if (
            not isinstance(expected_offset, (int, float))
            or not math.isfinite(float(expected_offset))
            or abs(
                float(expected_offset)
                - NIGHT_AIRBORNE_PHASE_OFFSETS[offset_index]
            )
            > 0.000001
        ):
            errors.append(
                f"{lane_id}:{label}: requested relative beam offset is wrong"
            )
    for side, frames in (("current", current), ("baseline", baseline)):
        observed_azimuths: list[float] = []
        observed_offsets: list[float] = []
        requested_offsets: list[float] = []
        capture_ids: set[str] = set()
        route_ticks: list[int] = []
        frame_indices: list[int] = []
        elapsed_times: list[float] = []
        for label in expected_labels:
            frame = frames.get(label)
            if frame is None:
                continue
            if (
                frame.path.suffix.lower() != ".png"
                or not frame.path.is_file()
                or frame.size[0] <= 0
                or frame.size[1] <= 0
            ):
                errors.append(f"{lane_id}:{side}:{label}: direct capture PNG is absent")
            if frame.metadata.get("lighthouse_record_published") is not True:
                errors.append(f"{lane_id}:{side}:{label}: lighthouse kind-4 host record is absent")
            if frame.metadata.get("bonfire_record_published") is not True:
                errors.append(f"{lane_id}:{side}:{label}: bonfire kind-1 host record is absent")
            capture_id = frame.metadata.get("temporal_capture_id")
            if isinstance(capture_id, str) and capture_id:
                capture_ids.add(capture_id)
            route_tick = frame.metadata.get("route_tick")
            if isinstance(route_tick, int) and not isinstance(route_tick, bool) and route_tick >= 0:
                route_ticks.append(route_tick)
            else:
                errors.append(f"{lane_id}:{side}:{label}: route tick is absent")
            frame_index = frame.metadata.get("temporal_frame_index")
            if isinstance(frame_index, int) and not isinstance(frame_index, bool):
                frame_indices.append(frame_index)
            else:
                errors.append(f"{lane_id}:{side}:{label}: temporal frame index is absent")
            elapsed = frame.metadata.get("temporal_elapsed_seconds")
            if isinstance(elapsed, (int, float)) and math.isfinite(float(elapsed)):
                elapsed_times.append(float(elapsed))
            else:
                errors.append(f"{lane_id}:{side}:{label}: temporal elapsed time is absent")
            if frame.metadata.get("png_capture_owner") != NIGHT_CAPTURE_OWNER:
                errors.append(f"{lane_id}:{side}:{label}: PNG owner is not the dogfight process")
            if frame.metadata.get("png_captured_in_same_process") is not True:
                errors.append(f"{lane_id}:{side}:{label}: same-process PNG truth is unproven")
            source_frame = _runtime_path_value(frame.metadata, "source_frame")
            if (
                source_frame is None
                or not source_frame.is_file()
                or _sha256(source_frame) != frame.sha256
            ):
                errors.append(f"{lane_id}:{side}:{label}: same-process PNG bytes do not match the grid frame")
            airplane_position = _runtime_vector(
                frame.metadata,
                "runtime_airplane_position",
                "runtime.airplane_position",
                "airplane_position",
            )
            if airplane_position is None or airplane_position[1] <= 0.0:
                errors.append(f"{lane_id}:{side}:{label}: airborne airplane position is absent")
            azimuth = frame.metadata.get("lighthouse_beam_azimuth")
            target = frame.metadata.get("beam_phase_offset_target")
            measured_offset = frame.metadata.get("beam_phase_offset_measured")
            if not isinstance(azimuth, (int, float)) or not math.isfinite(float(azimuth)):
                errors.append(f"{lane_id}:{side}:{label}: observed lighthouse azimuth is absent")
            else:
                observed_azimuths.append(float(azimuth))
            if not isinstance(target, (int, float)) or not math.isfinite(float(target)):
                errors.append(
                    f"{lane_id}:{side}:{label}: requested phase offset is absent"
                )
            else:
                requested_offsets.append(float(target))
            if (
                not isinstance(measured_offset, (int, float))
                or not math.isfinite(float(measured_offset))
            ):
                errors.append(
                    f"{lane_id}:{side}:{label}: measured phase offset is absent"
                )
            else:
                observed_offsets.append(float(measured_offset))
            if side == "current":
                for forbidden_field in FORBIDDEN_AUTOMATED_PIXEL_CLAIM_FIELDS:
                    if frame.metadata.get(forbidden_field) is not None:
                        errors.append(
                            f"{lane_id}:{side}:{label}: forbidden automated pixel claim "
                            f"{forbidden_field}; structural counts cannot establish visible pixels"
                        )
                for count_field in NIGHT_PRODUCT_COUNT_FIELDS:
                    count = frame.metadata.get(count_field)
                    if (
                        not isinstance(count, int)
                        or isinstance(count, bool)
                        or count <= 0
                    ):
                        errors.append(
                            f"{lane_id}:{side}:{label}: {count_field} is absent or zero"
                        )
            else:
                for count_field in NIGHT_PRODUCT_COUNT_FIELDS:
                    if count_field not in frame.metadata or frame.metadata.get(count_field) is not None:
                        errors.append(
                            f"{lane_id}:{side}:{label}: Baseline A product ABI absence must be explicit null: {count_field}"
                        )
        if len(capture_ids) != 1:
            errors.append(f"{lane_id}:{side}: frames are not from one continuous airborne capture")
        if tuple(route_ticks) != NIGHT_AIRBORNE_ROUTE_TICKS:
            errors.append(
                f"{lane_id}:{side}: route ticks are not exactly 30, 150, 270"
            )
        if frame_indices != list(range(len(expected_labels))):
            errors.append(f"{lane_id}:{side}: temporal frame indices are not exact and increasing")
        if (
            len(elapsed_times) != len(expected_labels)
            or any(
                elapsed_times[index] <= elapsed_times[index - 1]
                for index in range(1, len(elapsed_times))
            )
        ):
            errors.append(f"{lane_id}:{side}: temporal elapsed times are not increasing")
        if (
            len(observed_azimuths) == len(expected_labels)
            and len(observed_offsets) == len(expected_labels)
            and len(requested_offsets) == len(expected_labels)
        ):
            first_azimuth = observed_azimuths[0]
            for index, label in enumerate(expected_labels):
                recomputed_offset = _wrapped_positive_angle_delta(
                    first_azimuth,
                    observed_azimuths[index],
                )
                if abs(recomputed_offset - observed_offsets[index]) > 0.000001:
                    errors.append(
                        f"{lane_id}:{side}:{label}: measured offset does not "
                        "match the observed azimuths"
                    )
                requested_error = abs(
                    math.atan2(
                        math.sin(
                            observed_offsets[index] - requested_offsets[index]
                        ),
                        math.cos(
                            observed_offsets[index] - requested_offsets[index]
                        ),
                    )
                )
                if requested_error > 0.08:
                    errors.append(
                        f"{lane_id}:{side}:{label}: naturally observed phase "
                        "offset missed the requested interval"
                    )
    for label in expected_labels:
        if label not in current or label not in baseline:
            continue
        current_tick = current[label].metadata.get("route_tick")
        baseline_tick = baseline[label].metadata.get("route_tick")
        if current_tick != baseline_tick:
            errors.append(f"{lane_id}:{label}: baseline and current route ticks are not matched")
        current_phase = current[label].metadata.get(
            "beam_phase_offset_measured"
        )
        baseline_phase = baseline[label].metadata.get(
            "beam_phase_offset_measured"
        )
        if isinstance(current_phase, (int, float)) and isinstance(baseline_phase, (int, float)):
            phase_delta = abs(
                math.atan2(
                    math.sin(float(current_phase) - float(baseline_phase)),
                    math.cos(float(current_phase) - float(baseline_phase)),
                )
            )
            if phase_delta > 0.12:
                errors.append(
                    f"{lane_id}:{label}: baseline and current measured beam "
                    "offsets are not matched"
                )
    return errors


def _validate_lane_availability(lane: dict[str, Any], baseline: dict[str, FrameEvidence]) -> list[str]:
    lane_id = str(lane.get("id", ""))
    errors: list[str] = []
    if lane_id in {"airplane_air_movement_perspective", "airplane_air_movement_ortho"}:
        for label, frame in baseline.items():
            face_count = _metadata_value(frame.metadata, "airplane_dynamic_face_count", "airplane.dynamic_face_count")
            if not isinstance(face_count, (int, float)) or int(face_count) <= 0:
                errors.append(f"{lane_id}:baseline:{label}: checkpoint baseline cannot produce the lane")
    return errors


def _validate_lane_schema(lane: dict[str, Any], expected_by_label: dict[str, dict[str, Any]], required_files: list[str]) -> list[str]:
    lane_id = str(lane.get("id", "<missing>"))
    errors: list[str] = []
    if not isinstance(lane.get("bug_refs"), list) or not all(isinstance(item, str) and item for item in lane.get("bug_refs", [])):
        errors.append(f"{lane_id}: bug_refs must be a non-empty string list")
    lane_baseline = lane.get("baseline")
    if lane_baseline not in BASELINE_NAMES:
        errors.append(f"{lane_id}: baseline must be baseline_a")
    capture_sequence = lane.get("capture_sequence", "single_frame")
    if capture_sequence not in CAPTURE_SEQUENCES:
        errors.append(
            f"{lane_id}: capture_sequence must be single_frame, movement_sequence, "
            f"or {STATIC_TEMPORAL_SEQUENCE}"
        )
    for label, expected in expected_by_label.items():
        view_mode = expected.get("view_mode")
        if view_mode not in VIEW_MODES:
            errors.append(f"{lane_id}:{label}: view_mode must be ortho or perspective")
        features = expected.get("feature_flags")
        if not isinstance(features, list) or not all(isinstance(item, str) and item for item in features):
            errors.append(f"{lane_id}:{label}: feature_flags must be a non-empty string list")
        if "ascii" not in features:
            errors.append(f"{lane_id}:{label}: feature_flags must include ascii")
    lane_files = lane.get("expected_runtime_source_files")
    if not isinstance(lane_files, list) or not all(isinstance(item, str) and item for item in lane_files):
        errors.append(f"{lane_id}: expected_runtime_source_files must be a non-empty string list")
    else:
        non_runtime_lane_files = [path for path in lane_files if not path.startswith(RUNTIME_PREFIXES)]
        if non_runtime_lane_files:
            errors.append(f"{lane_id}: expected_runtime_source_files contains non-runtime paths: {', '.join(non_runtime_lane_files)}")
        missing_from_lane = [path for path in required_files if path not in lane_files]
        extra_in_lane = [path for path in lane_files if path not in required_files]
        if missing_from_lane:
            errors.append(f"{lane_id}: expected_runtime_source_files missing required files: {', '.join(missing_from_lane)}")
        if extra_in_lane:
            errors.append(f"{lane_id}: expected_runtime_source_files has files not declared by --required-runtime-files: {', '.join(extra_in_lane)}")
    return errors


def _validate_manifest(manifest: dict[str, Any], args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[str]]:
    lanes = manifest.get("lanes")
    if not isinstance(lanes, list) or not lanes:
        raise GridInputError("manifest must contain non-empty lanes array")
    errors: list[str] = []
    normalized: list[dict[str, Any]] = []
    required_files = _normalize_required_files(args.required_runtime_files)
    current_root = _project_root(Path(args.current_project).resolve())
    baseline_roots = {
        "baseline_a": _project_root(Path(args.baseline_a_project).resolve()),
    }
    required_lane_fields = {
        "id",
        "bug_refs",
        "baseline",
		"frames",
		"required_current_frame_paths",
		"required_baseline_frame_paths",
		"expected_runtime_source_files",
	}
    required_expected_fields = {
        "view_mode",
        "camera_position",
        "camera_yaw",
        "camera_pitch",
        "camera_roll",
        "camera_fov",
        "projection_params",
        "player_position",
        "player_yaw",
        "npc_positions",
        "airplane_position",
        "airplane_yaw",
        "world_time",
        "night_state",
        "feature_flags",
    }
    lane_ids = {str(lane.get("id", "")) for lane in lanes if isinstance(lane, dict)}
    if args.require_canonical_lanes:
        missing_lanes = sorted(REQUIRED_CANONICAL_LANE_IDS - lane_ids)
        if missing_lanes:
            errors.append("manifest missing canonical lanes: " + ", ".join(missing_lanes))
        extra_lanes = sorted(lane_ids - REQUIRED_CANONICAL_LANE_IDS)
        if extra_lanes:
            errors.append("manifest has non-canonical lanes: " + ", ".join(extra_lanes))
    for lane_value in lanes:
        if not isinstance(lane_value, dict):
            errors.append(f"lane must be object: {lane_value!r}")
            continue
        lane_id = str(lane_value.get("id", "<missing>"))
        missing_fields = sorted(required_lane_fields - set(lane_value))
        if missing_fields:
            errors.append(f"{lane_id}: missing lane fields: {', '.join(missing_fields)}")
            continue
        try:
            frame_specs = _frame_spec_map(lane_value)
        except GridInputError as exc:
            errors.append(str(exc))
            continue
        labels = list(frame_specs)
        expected_by_label: dict[str, dict[str, Any]] = {}
        baseline_name_by_label: dict[str, str] = {}
        baseline_base_by_label: dict[str, Path] = {}
        frame_errors = False
        for label, spec in frame_specs.items():
            current_name = _frame_current_name(lane_value, spec)
            if current_name != "current":
                errors.append(f"{lane_id}:{label}: invalid current codebase {current_name}")
                frame_errors = True
            baseline_name = _frame_baseline_name(lane_value, spec)
            if baseline_name not in BASELINE_NAMES:
                errors.append(f"{lane_id}:{label}: invalid baseline codebase {baseline_name}")
                frame_errors = True
                continue
            expected = _frame_expected_values(lane_value, spec)
            missing_expected = sorted(required_expected_fields - set(expected))
            if missing_expected:
                errors.append(f"{lane_id}:{label}: missing frame pose fields: {', '.join(missing_expected)}")
                frame_errors = True
                continue
            expected_by_label[label] = expected
            baseline_name_by_label[label] = baseline_name
            baseline_base_by_label[label] = baseline_roots[baseline_name]
        if frame_errors:
            continue
        errors.extend(_validate_lane_schema(lane_value, expected_by_label, required_files))
        try:
            current = _normalize_frame_entries(
                lane_value.get("required_current_frame_paths"),
                labels,
                current_root,
                f"{lane_id}:required_current_frame_paths",
                args.run_captures,
            )
            baseline = _normalize_frame_entries(
                lane_value.get("required_baseline_frame_paths"),
                labels,
                baseline_base_by_label,
                f"{lane_id}:required_baseline_frame_paths",
                args.run_captures,
            )
        except GridInputError as exc:
            errors.append(str(exc))
            continue
        min_size = (int(args.min_fullscreen_width), int(args.min_fullscreen_height))
        for label, frame in current.items():
            errors.extend(
                _validate_frame_metadata(
                    lane_id,
                    "current",
                    frame,
                    expected_by_label[label],
                    args.ascii_required,
                    args.fullscreen_required,
                    args.require_actual_godot_capture,
                    min_size,
                    str(lane_value.get("capture_sequence") or "single_frame"),
                )
            )
            if args.require_actual_godot_capture:
                errors.extend(
                    _validate_shared_server_frame(
                        lane_id,
                        "current",
                        frame,
                        args.shared_current_colyseus_dir,
                        args.required_current_commit,
                    )
                )
        for label, frame in baseline.items():
            errors.extend(
                _validate_frame_metadata(
                    lane_id,
                    "baseline",
                    frame,
                    expected_by_label[label],
                    args.ascii_required,
                    args.fullscreen_required,
                    args.require_actual_godot_capture,
                    min_size,
                    str(lane_value.get("capture_sequence") or "single_frame"),
                )
            )
            if args.require_actual_godot_capture:
                errors.extend(
                    _validate_shared_server_frame(
                        lane_id,
                        "baseline",
                        frame,
                        args.shared_current_colyseus_dir,
                        args.required_current_commit,
                    )
                )
        errors.extend(_validate_lane_pairing(lane_value, current, baseline, expected_by_label))
        if args.require_actual_godot_capture:
            errors.extend(
                _validate_shared_server_pairing(lane_value, current, baseline)
            )
        errors.extend(_validate_beam_lane(lane_value, current, baseline, expected_by_label))
        errors.extend(_validate_lane_availability(lane_value, baseline))
        normalized.append(
            {
                "lane": lane_value,
                "frame_specs": frame_specs,
                "baseline_by_label": baseline_name_by_label,
                "current": current,
                "baseline": baseline,
                "expected_by_label": expected_by_label,
                "expected": next(iter(expected_by_label.values())),
            }
        )
    errors.extend(_validate_canonical_matrix(normalized, args.require_canonical_lanes))
    return normalized, errors


def _font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrapped_lines(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont, width: int) -> list[str]:
    result: list[str] = []
    for raw in text.splitlines():
        current = ""
        for word in raw.split(" "):
            candidate = word if not current else f"{current} {word}"
            if draw.textbbox((0, 0), candidate, font=fnt)[2] <= width:
                current = candidate
                continue
            if current:
                result.append(current)
            if draw.textbbox((0, 0), word, font=fnt)[2] <= width:
                current = word
            else:
                chunks = wrap(word, max(8, width // 9))
                result.extend(chunks[:-1])
                current = chunks[-1] if chunks else word
        if current:
            result.append(current)
    return result


def _draw_text_box(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, fnt: ImageFont.ImageFont, fill: tuple[int, int, int]) -> None:
    draw.rectangle(box, fill=fill)
    x0, y0, x1, y1 = box
    y = y0 + 7
    step = max(14, int(getattr(fnt, "size", 13) * 1.2))
    for line in _wrapped_lines(draw, text, fnt, x1 - x0 - 14):
        if y + step > y1 - 2:
            break
        draw.text((x0 + 7, y), line, font=fnt, fill=(244, 244, 238))
        y += step


def _fit_image(path: Path, width: int, height: int) -> Image.Image:
    img = Image.open(path).convert("RGB")
    scale = min(width / float(img.width), height / float(img.height))
    out_w = max(1, int(round(img.width * scale)))
    out_h = max(1, int(round(img.height * scale)))
    resized = img.resize((out_w, out_h), Image.Resampling.LANCZOS)
    out = Image.new("RGB", (width, height), (7, 8, 11))
    out.paste(resized, ((width - out_w) // 2, (height - out_h) // 2))
    return out


def _command_arg(command: list[Any], flag: str) -> str:
    """Return one flag value without treating a command string as evidence."""
    if flag not in command:
        return ""
    index = command.index(flag) + 1
    if index >= len(command):
        return ""
    value = command[index]
    return value if isinstance(value, str) else ""


def _operator_signoff_contract(
    *,
    artifact: Path,
    commit: str,
    timestamp: str,
) -> dict[str, Any]:
    """Describe the human decision still required after the grid is built."""
    artifact_path = _repo_path(artifact)
    return {
        "required": True,
        "gate": "visual_receipted_baseline_grid_operator_review",
        "status": "missing_until_analyze_failure_log_signoff_verify_passes",
        "verify_command": [
            "python3",
            "scripts/analyze_failure_log.py",
            "signoff",
            "verify",
            "FL-4431",
        ],
        "query_command": [
            "python3",
            "scripts/analyze_failure_log.py",
            "signoff",
            "query",
            "FL-4431",
        ],
        "record_pass_command": [
            "python3",
            "scripts/analyze_failure_log.py",
            "signoff",
            "record",
            "FL-4431",
            "--operator",
            "<operator-name>",
            "--date",
            timestamp[:10],
            "--branch",
            "main",
            "--commit",
            commit,
            "--environment",
            "local-headed-godot-fullscreen-receipted-baseline-grid",
            "--path",
            artifact_path,
            "--verdict",
            "pass",
            "--note",
            "Receipted Baseline A/current canonical PNG grid visually accepted",
        ],
        "note": (
            "The grid is review material only. Running the record command after "
            "direct inspection creates the separate operator decision."
        ),
    }


def _operator_signoff_errors(
    signoff: Any,
    *,
    artifact: Path,
    commit: str,
) -> list[str]:
    """Reject a manifest that cannot record and later verify human review."""
    if not isinstance(signoff, dict):
        return ["operator_signoff must be an object"]
    errors: list[str] = []
    verify = signoff.get("verify_command")
    query = signoff.get("query_command")
    record = signoff.get("record_pass_command")
    expected_verify = [
        "python3",
        "scripts/analyze_failure_log.py",
        "signoff",
        "verify",
        "FL-4431",
    ]
    expected_query = [
        "python3",
        "scripts/analyze_failure_log.py",
        "signoff",
        "query",
        "FL-4431",
    ]
    if signoff.get("required") is not True:
        errors.append("operator_signoff.required must be true")
    if signoff.get("gate") != "visual_receipted_baseline_grid_operator_review":
        errors.append("operator_signoff gate is incorrect")
    if signoff.get("status") != "missing_until_analyze_failure_log_signoff_verify_passes":
        errors.append("operator_signoff status must remain pending")
    if verify != expected_verify:
        errors.append("operator_signoff verify command is missing or incoherent")
    if query != expected_query:
        errors.append("operator_signoff query command is missing or incoherent")
    if not isinstance(record, list):
        errors.append("operator_signoff record command is missing")
        return errors
    expected_prefix = [
        "python3",
        "scripts/analyze_failure_log.py",
        "signoff",
        "record",
        "FL-4431",
    ]
    if record[:5] != expected_prefix:
        errors.append("operator_signoff record command targets the wrong owner")
    if _command_arg(record, "--commit") != commit:
        errors.append("operator_signoff record command commit differs from captured source")
    if _command_arg(record, "--branch") != "main":
        errors.append("operator_signoff record command branch is not main")
    if _command_arg(record, "--verdict") != "pass":
        errors.append("operator_signoff record command verdict is not pass")
    if _command_arg(record, "--path") != _repo_path(artifact):
        errors.append("operator_signoff record command path differs from the grid artifact")
    if not _command_arg(record, "--operator"):
        errors.append("operator_signoff record command has no operator field")
    if not _command_arg(record, "--date"):
        errors.append("operator_signoff record command has no date")
    if not _command_arg(record, "--environment"):
        errors.append("operator_signoff record command has no environment")
    if not _command_arg(record, "--note"):
        errors.append("operator_signoff record command has no note")
    return errors


def _night_panel_runtime_label(metadata: dict[str, Any]) -> str:
    requested_offset = metadata.get("beam_phase_offset_target")
    requested_text = (
        f"{float(requested_offset):.6f}"
        if (
            isinstance(requested_offset, (int, float))
            and math.isfinite(float(requested_offset))
        )
        else "null"
    )
    measured_azimuth = metadata.get("lighthouse_beam_azimuth")
    measured_text = (
        f"{float(measured_azimuth):.6f}"
        if (
            isinstance(measured_azimuth, (int, float))
            and math.isfinite(float(measured_azimuth))
        )
        else "null"
    )
    return (
        f"route_tick={metadata.get('route_tick')} | "
        f"requested_phase_offset={requested_text} | "
        f"measured_azimuth={measured_text}"
    )


def _panel_label_text(values: dict[str, str]) -> str:
    """Validate and serialize every mandatory operator-visible label field.

    Keeping one explicit schema prevents a future layout refactor from silently
    dropping provenance. Tests delete each field in turn and require this
    function to reject the incomplete label before the PNG is written.
    """
    missing = [
        field
        for field in MANDATORY_PANEL_LABEL_FIELDS
        if not isinstance(values.get(field), str) or not values[field].strip()
    ]
    if missing:
        raise GridInputError(
            "panel label is missing mandatory fields: " + ", ".join(missing)
        )
    return "\n".join(
        [
            f"timestamp={values['timestamp']} | FL refs={values['failure_log_refs']}",
            f"code refs={values['code_refs']} | fix attempt={values['fix_attempt_number']}",
            f"exact attempt={values['fix_attempt_description']}",
            f"rationale={values['rationale']}",
            f"commit={values['commit']} | dirty state={values['dirty_state']}",
            f"Baseline A panel={values['baseline_panel_identity']}",
            f"current panel={values['current_panel_identity']}",
            f"panel identity={values['panel_identity']}",
        ]
    )


def _panel_label_values(
    *,
    args: argparse.Namespace,
    lane: dict[str, Any],
    side: str,
    timestamp: str,
) -> dict[str, str]:
    """Build the review-label schema from authoritative CLI plus lane facts."""
    return {
        "timestamp": timestamp,
        "failure_log_refs": ",".join(str(value) for value in lane["bug_refs"]),
        "code_refs": ",".join(_normalize_code_refs(args.code_refs)),
        "fix_attempt_number": str(args.fix_attempt_number),
        "fix_attempt_description": args.fix_attempt_description.strip(),
        "rationale": str(
            lane.get(
                "why_fix_should_work",
                "source architecture fix must be described in manifest",
            )
        ).strip(),
        "commit": args.required_current_commit,
        # `_validate_required_commit` rejects every dirty current checkout.
        # Exposing the resulting clean state makes that gate visible instead of
        # leaving reviewers to infer it from a hidden validator.
        "dirty_state": "clean",
        "baseline_panel_identity": RENDERER_ARM_LABELS["baseline"],
        "current_panel_identity": RENDERER_ARM_LABELS["current"],
        "panel_identity": RENDERER_ARM_LABELS[side],
    }


def _build_grid(normalized: list[dict[str, Any]], args: argparse.Namespace, manifest_path: Path) -> tuple[Path, Path]:
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    current_source_root = _project_root(Path(args.current_project).resolve())
    head = _git_text("rev-parse", "HEAD", source_root=current_source_root)
    timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    panel_w = int(args.panel_width)
    panel_h = int(args.panel_height)
    # Eight mandatory review rows plus lane/runtime context need more vertical
    # room than the historical two-line label. Cropping any row would make the
    # field structurally present yet invisible, which is still a false green.
    label_h = 300
    header_h = 260
    gutter = 12
    margin = 18
    frame_labels: list[tuple[int, str]] = []
    for lane_idx, record in enumerate(normalized):
        for frame_label in record["frame_specs"]:
            frame_labels.append((lane_idx, frame_label))
    cols = 2
    rows = len(frame_labels)
    width = margin * 2 + cols * panel_w + gutter
    tile_h = label_h + panel_h
    height = header_h + margin + rows * tile_h + max(0, rows - 1) * gutter + margin
    canvas = Image.new("RGB", (width, height), (12, 13, 17))
    draw = ImageDraw.Draw(canvas)
    label_font = _font(13)
    header_font = _font(18)
    header = "\n".join(
        [
            f"Canonical Godot PNG grid | {timestamp} | HEAD {head[:12]} | required_runtime_commit={args.required_current_commit}",
            f"manifest={_repo_path(manifest_path)} | current={args.current_project}",
            f"Baseline A receipt={args.baseline_receipt} | source_commit={args.baseline_source_commit}",
            f"shared current Colyseus={args.shared_current_colyseus_dir}",
            "Every row pairs current pixels with Baseline A pixels at one frame label. Runtime labels establish provenance only; they never establish visible correctness. Operator inspection of the PNG cells remains mandatory.",
        ]
    )
    _draw_text_box(draw, (0, 0, width, header_h), header, header_font, (27, 31, 42))
    panels: list[dict[str, Any]] = []
    y = header_h + margin
    for lane_idx, frame_label in frame_labels:
        record = normalized[lane_idx]
        lane = record["lane"]
        for col, side in enumerate(("baseline", "current")):
            evidence: FrameEvidence = record[side][frame_label]
            x = margin + col * (panel_w + gutter)
            temporal_label = ""
            if lane["id"] == "night_lighthouse_beam":
                temporal_label = "\n" + _night_panel_runtime_label(evidence.metadata)
            visible_label_values = _panel_label_values(
                args=args,
                lane=lane,
                side=side,
                timestamp=timestamp,
            )
            mandatory_label = _panel_label_text(visible_label_values)
            title = (
                f"{mandatory_label}\n"
                f"lane={lane['id']} | baseline={record['baseline_by_label'][frame_label]} | "
                f"frame={frame_label} | path={_repo_path(evidence.path)}{temporal_label}"
            )
            label_lines = _wrapped_lines(
                draw,
                title,
                label_font,
                panel_w - 14,
            )
            label_step = max(
                14,
                int(getattr(label_font, "size", 13) * 1.2),
            )
            if 7 + len(label_lines) * label_step > label_h - 2:
                raise GridInputError(
                    f"{lane['id']}:{side}:{frame_label}: mandatory panel label "
                    "does not fit visibly inside the label box"
                )
            _draw_text_box(draw, (x, y, x + panel_w, y + label_h), title, label_font, (24, 29, 39))
            canvas.paste(_fit_image(evidence.path, panel_w, panel_h), (x, y + label_h))
            panels.append(
                {
                    "lane": lane["id"],
                    "side": side,
                    "frame": frame_label,
                    "baseline": record["baseline_by_label"][frame_label],
                    "expected_state": record["expected_by_label"][frame_label],
                    "path": _repo_path(evidence.path),
                    "sha256": evidence.sha256,
                    "size": list(evidence.size),
                    "visible_label_fields": visible_label_values,
                    "visible_label_text": mandatory_label,
                    "shared_current_server_identity": (
                        evidence.metadata.get("shared_current_server_identity")
                    ),
                    "runtime_truth": {
                        "route_tick": evidence.metadata.get("route_tick"),
                        "requested_phase_offset": evidence.metadata.get(
                            "beam_phase_offset_target"
                        ),
                        "measured_phase_offset": evidence.metadata.get(
                            "beam_phase_offset_measured"
                        ),
                        "measured_azimuth": evidence.metadata.get(
                            "lighthouse_beam_azimuth"
                        ),
                    }
                    if lane["id"] == "night_lighthouse_beam"
                    else None,
                }
            )
        y += tile_h + gutter
    out_png = out_dir / "GODOT_CANONICAL_CURRENT_BASELINE_GRID.png"
    canvas.save(out_png)
    receipt = {
        "artifact": _repo_path(out_png),
        "sha256": _sha256(out_png),
        "timestamp": timestamp,
        "head": head,
        "required_current_commit": args.required_current_commit,
        "required_runtime_files": _normalize_required_files(args.required_runtime_files),
        "current_project": str(Path(args.current_project).resolve()),
        "shared_current_colyseus_dir": args.shared_current_colyseus_dir,
        "code_refs": _normalize_code_refs(args.code_refs),
        "fix_attempt_number": args.fix_attempt_number,
        "fix_attempt_description": args.fix_attempt_description.strip(),
        "dirty_state": "clean",
        "baseline_receipt": str(Path(args.baseline_receipt).resolve()),
        "baseline_receipt_identity": dict(args.baseline_receipt_identity),
        "baseline_receipt_schema": args.baseline_receipt_identity["schema"],
        "baseline_receipt_self_hash": args.baseline_receipt_identity["self_hash"],
        "baseline_source_commit": args.baseline_receipt_identity["source_commit"],
        "baseline_source_tree": args.baseline_receipt_identity["source_tree"],
        "baseline_capture_id": args.baseline_receipt_identity["capture_id"],
        "baseline_temporal_capture_id": args.baseline_receipt_identity[
            "temporal_capture_id"
        ],
        "baseline_lane_manifest_sha256": args.baseline_receipt_identity[
            "lane_manifest_sha256"
        ],
        "baseline_proof_tool_commit": args.baseline_receipt_identity[
            "proof_tool_commit"
        ],
        "baseline_a_project": str(Path(args.baseline_a_project).resolve()),
        "manifest": _repo_path(manifest_path),
        "panels": panels,
        "proof_state": "grid built by fail-closed canonical runner; operator visual signoff still required",
        "operator_signoff": _operator_signoff_contract(
            artifact=out_png,
            commit=args.required_current_commit,
            timestamp=timestamp,
        ),
    }
    signoff_errors = _operator_signoff_errors(
        receipt["operator_signoff"],
        artifact=out_png,
        commit=args.required_current_commit,
    )
    if signoff_errors:
        raise GridInputError("; ".join(signoff_errors))
    receipt_path = out_png.with_suffix(".manifest.json")
    publication_path = out_dir / GODOT_PROOF_PUBLICATION_MANIFEST
    receipt["godot_proof_publication_manifest"] = _repo_path(publication_path)
    receipt["godot_proof_publication_status"] = "diagnostic_only_incomplete_current_capture"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    try:
        _publish_grid_artifacts(out_dir, receipt_path, receipt)
    except PublicationError as error:
        raise GridInputError(f"shared Godot proof publication failed: {error}") from error
    return out_png, receipt_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-project", required=True)
    parser.add_argument("--baseline-receipt", required=True)
    parser.add_argument(
        "--shared-current-colyseus-dir",
        required=True,
        help=(
            "One absolute current-checkout colyseus_server directory forwarded "
            "unchanged to the Baseline A and current renderer arms."
        ),
    )
    parser.add_argument("--required-current-commit", required=True)
    parser.add_argument("--required-runtime-files", required=True)
    parser.add_argument("--code-refs", required=True)
    parser.add_argument("--fix-attempt-number", required=True, type=int)
    parser.add_argument("--fix-attempt-description", required=True)
    parser.add_argument("--ascii-required", type=_bool_arg, default=True)
    parser.add_argument("--fullscreen-required", type=_bool_arg, default=True)
    parser.add_argument("--lanes", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--min-fullscreen-width", type=int, default=1600)
    parser.add_argument("--min-fullscreen-height", type=int, default=900)
    parser.add_argument("--panel-width", type=int, default=620)
    parser.add_argument("--panel-height", type=int, default=350)
    parser.add_argument("--run-captures", type=_bool_arg, default=True)
    parser.add_argument("--require-canonical-lanes", type=_bool_arg, default=True)
    parser.add_argument("--require-actual-godot-capture", type=_bool_arg, default=True)
    args = parser.parse_args(argv)

    errors: list[str] = []
    errors.extend(
        _validate_shared_current_colyseus_dir(
            args.shared_current_colyseus_dir,
            args.current_project,
        )
    )
    errors.extend(_validate_review_label_inputs(args))
    if errors:
        print("GODOT_CANONICAL_GRID_REJECTED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 2
    try:
        baseline_receipt, baseline_project = _load_operator_baseline_receipt(
            Path(args.baseline_receipt).resolve()
        )
        args.baseline_receipt_context = baseline_receipt
        args.baseline_receipt_identity = _baseline_receipt_identity(
            Path(args.baseline_receipt).resolve(),
            baseline_receipt,
        )
        args.baseline_a_project = str(baseline_project)
        args.baseline_source_commit = str(
            baseline_receipt["baseline_source"]["commit"]
        )
    except GridInputError as exc:
        print("GODOT_CANONICAL_GRID_REJECTED", file=sys.stderr)
        print(f"- {exc}", file=sys.stderr)
        return 2
    required_files = _normalize_required_files(args.required_runtime_files)
    errors.extend(
        _validate_required_commit(
            args.required_current_commit,
            required_files,
            Path(args.current_project).resolve(),
        )
    )
    manifest_path = Path(args.lanes).resolve()
    try:
        manifest = _load_json(manifest_path)
        _expand_frame_path_templates(manifest, args, manifest_path)
        errors.extend(_receipt_route_manifest_errors(manifest, args))
        if not errors:
            errors.extend(_capture_missing_frames(manifest, args, manifest_path))
        normalized, manifest_errors = _validate_manifest(manifest, args)
        errors.extend(manifest_errors)
    except GridInputError as exc:
        normalized = []
        errors.append(str(exc))
    if errors:
        print("GODOT_CANONICAL_GRID_REJECTED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 2
    try:
        out_png, receipt_path = _build_grid(normalized, args, manifest_path)
    except GridInputError as exc:
        print("GODOT_CANONICAL_GRID_REJECTED", file=sys.stderr)
        print(f"- {exc}", file=sys.stderr)
        return 2
    print(out_png)
    print(receipt_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
