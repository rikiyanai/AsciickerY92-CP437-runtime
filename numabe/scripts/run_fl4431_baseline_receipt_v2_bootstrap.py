#!/usr/bin/env python3
"""Capture the evidence that an operator-designated Baseline A v2 receipt needs.

This is the only executable bridge from the historical Baseline A locator to
the closed v2 receipt builder.  The v1 file is read solely to locate and bind
its immutable predecessor checkout.  It never becomes active proof authority.

The bridge performs two real captures:

* the existing acceptance-package airplane arm records a moving perspective
  route from the Baseline renderer against the shared proof server;
* the existing PNG-grid command builder and capture adapter record the three
  night frames in one Godot process at route ticks 30, 150, and 270.

Both outputs are normalized into
``fl4431.baseline_capture.non_promotable.v1``.  That document is diagnostic
evidence only.  Promotion occurs exclusively through
``build_fl4431_baseline_receipt_v2.create_receipt`` with an explicit operator
designation record.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
LANE_MANIFEST_RELATIVE = (
    "docs/research/ascii/verification/fl4408/original-bug-lanes.manifest.json"
)
LANE_MANIFEST = ROOT / LANE_MANIFEST_RELATIVE
NIGHT_LANE_ID = "night_lighthouse_beam"
NIGHT_LABELS = ("beam_t0", "beam_t1", "beam_t2")
NIGHT_TICKS = (30, 150, 270)
NIGHT_OFFSETS = (0.0, 2.0 * math.pi / 3.0, 4.0 * math.pi / 3.0)
NIGHT_ROUTE_IDENTITY = "server_tick_constant_physical_flight_input"
NIGHT_TICK_ZERO_KIND = "server_first_changed_admitted_flight_intent"
CAPTURE_SCHEMA = "fl4431.baseline_capture.non_promotable.v1"
PREDECESSOR_SCHEMA = "asciicker.godot_baseline.v1"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Every path here can change what gets launched, what gets normalized, or what
# the final builder accepts.  Runtime bootstrap refuses any uncommitted byte so
# a capture cannot silently use a tool different from the committed identity
# recorded in the resulting receipt.
EXECUTABLE_TOOL_PATHS = (
    "scripts/run_fl4431_baseline_receipt_v2_bootstrap.py",
    "scripts/build_fl4431_baseline_receipt_v2.py",
    "scripts/run_fl4359_acceptance_package.py",
    "scripts/build_godot_canonical_png_grid.py",
    "scripts/fl4408_actual_godot_capture_adapter.py",
    LANE_MANIFEST_RELATIVE,
)


class BootstrapError(RuntimeError):
    """Raised when one capture fact cannot be derived from its real owner."""


@dataclass(frozen=True)
class BootstrapConfig:
    """Caller-owned paths and bounded capture duration for one bootstrap."""

    predecessor_receipt: Path
    designation_record: Path
    output_receipt: Path
    capture_receipt: Path
    capture_root: Path
    port: int = 2581
    moving_frames: int = 240
    moving_visual_stride: int = 30
    moving_visual_limit: int = 6
    godot_timeout: int = 900
    server_timeout: int = 45


@dataclass(frozen=True)
class Preflight:
    """Immutable source identities established before any expensive launch."""

    predecessor: dict[str, Any]
    baseline_root: Path
    baseline_commit: str
    baseline_tree: str
    proof_commit: str
    proof_tree: str
    manifest: dict[str, Any]
    night_lane: dict[str, Any]


ProcessRunner = Callable[..., subprocess.CompletedProcess[str]]


def _canonical_bytes(value: Any) -> bytes:
    """Serialize evidence deterministically before deriving an identity."""

    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    """Return the lowercase SHA-256 identity of exact bytes."""

    return hashlib.sha256(value).hexdigest()


def _file_sha256(path: Path) -> str:
    """Hash one evidence attachment without loading a large PNG at once."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _path_ref(path: Path) -> dict[str, Any]:
    """Bind one regular file to its absolute path, byte size, and SHA-256."""

    resolved = path.expanduser().resolve()
    if not resolved.is_file() or resolved.is_symlink():
        raise BootstrapError(f"evidence attachment is unavailable: {resolved}")
    return {
        "path": str(resolved),
        "sha256": _file_sha256(resolved),
        "size_bytes": resolved.stat().st_size,
    }


def _load_json(path: Path, label: str) -> dict[str, Any]:
    """Load one JSON object while keeping the owning artifact in the error."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BootstrapError(f"{label} is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BootstrapError(f"{label} is invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BootstrapError(f"{label} root must be an object")
    return value


def _write_json_exclusive(path: Path, value: Mapping[str, Any]) -> None:
    """Create one JSON identity atomically and refuse any overwrite."""

    output = path.expanduser().absolute()
    if output.exists():
        raise BootstrapError(f"refusing to overwrite existing artifact: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.link(temporary, output)
    except FileExistsError as exc:
        raise BootstrapError(f"refusing to overwrite existing artifact: {output}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _default_runner(
    command: Sequence[str],
    *,
    cwd: Path,
    **kwargs: Any,
) -> subprocess.CompletedProcess[str]:
    """Run one real subprocess with captured text for a precise failure."""

    return subprocess.run(
        list(command),
        cwd=str(cwd),
        text=True,
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        **kwargs,
    )


def _run_checked(
    command: Sequence[str],
    *,
    cwd: Path,
    runner: ProcessRunner,
) -> subprocess.CompletedProcess[str]:
    """Execute one owned command and stop before evidence normalization on failure."""

    result = runner(list(command), cwd=cwd)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise BootstrapError(
            f"subprocess failed with rc={result.returncode}: "
            f"{' '.join(command)}: {detail}"
        )
    return result


def _git(
    root: Path,
    *args: str,
    runner: ProcessRunner,
) -> str:
    """Run one read-only Git query through the injectable process boundary."""

    result = runner(
        ["git", "-C", str(root), *args],
        cwd=root,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise BootstrapError(f"Git query failed in {root}: {detail}")
    return result.stdout.strip()


def _full_hex(value: str, label: str, width: int) -> str:
    """Require one full lowercase Git or SHA identity without abbreviation."""

    if len(value) != width or any(ch not in "0123456789abcdef" for ch in value):
        raise BootstrapError(f"{label} must be {width} lowercase hexadecimal characters")
    return value


def _assert_clean_checkout(root: Path, label: str, runner: ProcessRunner) -> None:
    """Reject uncommitted and untracked bytes before they can own a proof run."""

    dirty = _git(
        root,
        "status",
        "--porcelain",
        "--untracked-files=all",
        runner=runner,
    )
    if dirty:
        raise BootstrapError(f"{label} checkout must be completely clean: {dirty}")


def _assert_tool_bytes_match_head(
    proof_root: Path,
    relative: str,
    runner: ProcessRunner,
) -> None:
    """Prove a capture tool exists in HEAD and its working bytes are unchanged."""

    absolute = (proof_root / relative).resolve()
    if not absolute.is_file():
        raise BootstrapError(f"capture tool is missing: {relative}")
    _git(proof_root, "ls-files", "--error-unmatch", relative, runner=runner)
    committed_blob = _git(
        proof_root,
        "rev-parse",
        f"HEAD:{relative}",
        runner=runner,
    )
    live_blob = _git(
        proof_root,
        "hash-object",
        relative,
        runner=runner,
    )
    if committed_blob != live_blob:
        raise BootstrapError(f"capture tool differs from HEAD: {relative}")


def _find_night_lane(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return the one tracked night lane and reject ambiguous ownership."""

    lanes = manifest.get("lanes")
    if not isinstance(lanes, list):
        raise BootstrapError("lane manifest does not contain a lanes array")
    matches = [
        lane
        for lane in lanes
        if isinstance(lane, dict) and lane.get("id") == NIGHT_LANE_ID
    ]
    if len(matches) != 1:
        raise BootstrapError(f"manifest must contain exactly one {NIGHT_LANE_ID} lane")
    return dict(matches[0])


def _scan_stale_phase_key(value: Any, path: str = "manifest") -> None:
    """Reject the retired absolute-phase key anywhere in the executable manifest."""

    if isinstance(value, dict):
        if "beam_phase_target" in value:
            raise BootstrapError(f"{path} contains retired beam_phase_target")
        for key, child in value.items():
            _scan_stale_phase_key(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_stale_phase_key(child, f"{path}[{index}]")


def _validate_night_lane(lane: Mapping[str, Any]) -> None:
    """Pin the three-frame temporal route before a Godot process can launch."""

    if lane.get("capture_sequence") != "static_temporal_sequence":
        raise BootstrapError("night lane must use static_temporal_sequence")
    frames = lane.get("frames")
    if not isinstance(frames, list) or len(frames) != 3:
        raise BootstrapError("night lane must contain exactly three frames")
    labels = tuple(frame.get("label") for frame in frames if isinstance(frame, dict))
    ticks = tuple(frame.get("route_tick") for frame in frames if isinstance(frame, dict))
    offsets = tuple(
        frame.get("beam_phase_offset_target")
        for frame in frames
        if isinstance(frame, dict)
    )
    if labels != NIGHT_LABELS:
        raise BootstrapError(f"night labels must be exactly {NIGHT_LABELS}")
    if ticks != NIGHT_TICKS:
        raise BootstrapError(f"night route ticks must be exactly {NIGHT_TICKS}")
    if len(offsets) != 3:
        raise BootstrapError("night lane phase offsets are incomplete")
    for index, observed in enumerate(offsets):
        if (
            isinstance(observed, bool)
            or not isinstance(observed, (int, float))
            or not math.isfinite(float(observed))
            or abs(float(observed) - NIGHT_OFFSETS[index]) > 1.0e-12
        ):
            raise BootstrapError(
                f"{NIGHT_LABELS[index]} phase offset must be {NIGHT_OFFSETS[index]}"
            )


def _validate_night_runtime_rows(receipt: Mapping[str, Any]) -> None:
    """Recheck ticks and serialized phase offsets from the real Godot receipt."""

    rows = receipt.get("visual_frame_samples")
    if not isinstance(rows, list) or len(rows) != 3:
        raise BootstrapError("night runtime receipt must contain exactly three rows")
    for index, raw_row in enumerate(rows):
        if not isinstance(raw_row, dict):
            raise BootstrapError(f"{NIGHT_LABELS[index]} runtime row is not an object")
        if raw_row.get("label") != NIGHT_LABELS[index]:
            raise BootstrapError(f"{NIGHT_LABELS[index]} runtime label is wrong")
        if raw_row.get("route_tick") != NIGHT_TICKS[index]:
            raise BootstrapError(f"{NIGHT_LABELS[index]} runtime route tick is wrong")
        for field in ("beam_phase_offset_target", "beam_phase_offset_measured"):
            observed = raw_row.get(field)
            if (
                isinstance(observed, bool)
                or not isinstance(observed, (int, float))
                or not math.isfinite(float(observed))
                or abs(float(observed) - NIGHT_OFFSETS[index]) > 1.0e-6
            ):
                raise BootstrapError(
                    f"{NIGHT_LABELS[index]} runtime {field} is wrong"
                )


def _preflight(config: BootstrapConfig, runner: ProcessRunner) -> Preflight:
    """Bind clean source, proof tools, manifest, and operator predecessor."""

    predecessor_path = config.predecessor_receipt.expanduser().resolve()
    predecessor = _load_json(predecessor_path, "Baseline predecessor receipt")
    if predecessor.get("schema") != PREDECESSOR_SCHEMA:
        raise BootstrapError(
            f"predecessor schema must be {PREDECESSOR_SCHEMA}; "
            "the predecessor is a locator, never active authority"
        )
    source_root_raw = predecessor.get("source_root")
    source_commit_raw = predecessor.get("source_commit")
    if not isinstance(source_root_raw, str) or not source_root_raw:
        raise BootstrapError("predecessor source_root is missing")
    if not isinstance(source_commit_raw, str):
        raise BootstrapError("predecessor source_commit is missing")
    baseline_root = Path(source_root_raw).expanduser().resolve()
    if not baseline_root.is_dir():
        raise BootstrapError(f"Baseline source root is missing: {baseline_root}")
    baseline_commit = _full_hex(source_commit_raw, "Baseline source commit", 40)
    observed_baseline_commit = _full_hex(
        _git(baseline_root, "rev-parse", "HEAD", runner=runner),
        "Baseline checkout HEAD",
        40,
    )
    if observed_baseline_commit != baseline_commit:
        raise BootstrapError(
            "Baseline checkout HEAD differs from immutable predecessor receipt"
        )
    baseline_tree = _full_hex(
        _git(baseline_root, "rev-parse", "HEAD^{tree}", runner=runner),
        "Baseline source tree",
        40,
    )
    _assert_clean_checkout(baseline_root, "Baseline source", runner)

    proof_root = ROOT.resolve()
    proof_commit = _full_hex(
        _git(proof_root, "rev-parse", "HEAD", runner=runner),
        "proof checkout HEAD",
        40,
    )
    proof_tree = _full_hex(
        _git(proof_root, "rev-parse", "HEAD^{tree}", runner=runner),
        "proof checkout tree",
        40,
    )
    _assert_clean_checkout(proof_root, "proof-tool", runner)
    for relative in EXECUTABLE_TOOL_PATHS:
        _assert_tool_bytes_match_head(proof_root, relative, runner)

    designation = _load_json(
        config.designation_record.expanduser().resolve(),
        "operator designation record",
    )
    predecessor_sha = _file_sha256(predecessor_path)
    if Path(str(designation.get("predecessor_receipt_path", ""))).expanduser().resolve() != predecessor_path:
        raise BootstrapError("designation names a different predecessor receipt")
    if designation.get("predecessor_receipt_sha256") != predecessor_sha:
        raise BootstrapError("designation predecessor SHA-256 differs from disk")
    if Path(str(designation.get("successor_receipt_path", ""))).expanduser().absolute() != config.output_receipt.expanduser().absolute():
        raise BootstrapError("designation names a different successor receipt path")

    manifest = _load_json(LANE_MANIFEST, "tracked original-bug lane manifest")
    _scan_stale_phase_key(manifest)
    night_lane = _find_night_lane(manifest)
    _validate_night_lane(night_lane)
    return Preflight(
        predecessor=predecessor,
        baseline_root=baseline_root,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_tree,
        proof_commit=proof_commit,
        proof_tree=proof_tree,
        manifest=manifest,
        night_lane=night_lane,
    )


def _load_module(relative: str, name: str) -> ModuleType:
    """Import one committed proof owner without changing process search paths."""

    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise BootstrapError(f"could not import proof owner: {relative}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _require_png(path: Path, label: str) -> Path:
    """Require a real PNG signature instead of accepting a named placeholder."""

    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise BootstrapError(f"{label} PNG is missing: {resolved}")
    if resolved.read_bytes()[: len(PNG_SIGNATURE)] != PNG_SIGNATURE:
        raise BootstrapError(f"{label} is not a PNG file: {resolved}")
    return resolved


def _vec3(value: Any, label: str) -> list[float]:
    """Decode one real x/y/z receipt vector without placeholder coercion."""

    if isinstance(value, dict):
        raw = [value.get(axis) for axis in ("x", "y", "z")]
    elif isinstance(value, list) and len(value) == 3:
        raw = value
    else:
        raise BootstrapError(f"{label} must contain three coordinates")
    result: list[float] = []
    for coordinate in raw:
        if (
            isinstance(coordinate, bool)
            or not isinstance(coordinate, (int, float))
            or not math.isfinite(float(coordinate))
        ):
            raise BootstrapError(f"{label} contains a non-finite coordinate")
        result.append(float(coordinate))
    return result


def _number(value: Any, label: str) -> float:
    """Require one finite receipt number."""

    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise BootstrapError(f"{label} must be a finite number")
    return float(value)


def _integer(value: Any, label: str) -> int:
    """Require one true JSON integer."""

    if type(value) is not int:
        raise BootstrapError(f"{label} must be an integer")
    return value


def _command_value(command: Sequence[str], option: str, label: str) -> str:
    """Read one exact command-line value from separate or equals syntax."""

    matches: list[str] = []
    prefix = option + "="
    for index, part in enumerate(command):
        if part.startswith(prefix):
            matches.append(part[len(prefix) :])
        elif part == option:
            if index + 1 >= len(command):
                raise BootstrapError(f"{label} option has no value")
            matches.append(command[index + 1])
    if len(matches) != 1 or not matches[0]:
        raise BootstrapError(f"{label} must appear exactly once")
    return matches[0]


def _moving_day_cycle(command: Sequence[str]) -> float:
    """Derive the day clock from the command that the package actually ran."""

    prefix = "--extra-user-arg=--fl4408-day-cycle-time="
    matches = [part[len(prefix) :] for part in command if part.startswith(prefix)]
    if len(matches) != 1:
        raise BootstrapError("moving command must carry one day-cycle argument")
    try:
        value = float(matches[0])
    except ValueError as exc:
        raise BootstrapError("moving day-cycle argument is not numeric") from exc
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise BootstrapError("moving day-cycle argument is outside [0, 1]")
    return value


def _command_int_value(command: Sequence[str], option: str, label: str) -> int:
    """Derive one integer from the exact command and preserve a closed error."""

    raw = _command_value(command, option, label)
    try:
        value = int(raw)
    except ValueError as exc:
        raise BootstrapError(f"{label} must be an integer") from exc
    if value <= 0:
        raise BootstrapError(f"{label} must be positive")
    return value


def _parse_dimensions(value: Any, label: str) -> tuple[int, int]:
    """Decode a receipt dimension written as WxH, [w,h], or {x,y}."""

    if isinstance(value, str) and "x" in value:
        left, right = value.lower().split("x", 1)
        try:
            width, height = int(left), int(right)
        except ValueError as exc:
            raise BootstrapError(f"{label} has invalid WxH text") from exc
    elif isinstance(value, list) and len(value) == 2:
        width, height = value
    elif isinstance(value, dict):
        width, height = value.get("x"), value.get("y")
    else:
        raise BootstrapError(f"{label} does not contain two dimensions")
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise BootstrapError(f"{label} dimensions must be positive integers")
    return width, height


def _capture_moving(
    config: BootstrapConfig,
    preflight: Preflight,
    package: ModuleType,
) -> tuple[dict[str, Any], list[str], Path]:
    """Run the real Baseline moving-airplane arm through the package owner."""

    output = config.capture_root / "moving_airplane"
    result = package.run_capture_arm(
        name="receipt_v2_baseline",
        project=preflight.baseline_root / "godot_project",
        colyseus_dir=ROOT / "colyseus_server",
        out_dir=output,
        port=config.port,
        frames=config.moving_frames,
        visual_stride=config.moving_visual_stride,
        visual_limit=config.moving_visual_limit,
        visual_start=0,
        frame_start=0,
        frame_limit=0,
        world_sample_stride=8,
        godot_timeout=config.godot_timeout,
        server_timeout=config.server_timeout,
        expected_red_gates=[],
        jitter_capture=False,
        shared_extra_user_args=[
            "--fl4408-day-cycle-time=0.5",
            "--fl4203-night-bonfire",
        ],
        arm_extra_user_args=[],
    )
    if not isinstance(result, dict):
        raise BootstrapError("moving capture owner returned a non-object")
    if result.get("returncode") != 0:
        raise BootstrapError(
            f"moving capture subprocess failed with rc={result.get('returncode')}"
        )
    receipt_path = output / "receipt.json"
    receipt = _load_json(receipt_path, "moving-airplane receipt")
    _scan_stale_phase_key(receipt, "moving receipt")
    if receipt.get("pass") is not True:
        raise BootstrapError("moving-airplane receipt pass is not true")
    if receipt.get("route_identity") != NIGHT_ROUTE_IDENTITY:
        raise BootstrapError("moving-airplane receipt has the wrong physical route")
    if receipt.get("perspective_forced") is not True:
        raise BootstrapError("moving-airplane receipt did not prove perspective mode")
    samples = receipt.get("visual_frame_samples")
    if not isinstance(samples, list) or len(samples) < 2:
        raise BootstrapError("moving-airplane receipt lacks time-separated frame truth")
    command = result.get("cmd")
    if not isinstance(command, list) or not command or not all(
        isinstance(part, str) and part for part in command
    ):
        raise BootstrapError("moving capture did not return its exact command")
    return receipt, list(command), receipt_path


def _capture_night(
    config: BootstrapConfig,
    preflight: Preflight,
    grid: ModuleType,
    runner: ProcessRunner,
) -> tuple[dict[str, Any], list[str], list[Path], list[Path], Path]:
    """Run the tracked three-frame night lane through grid and adapter owners."""

    output = config.capture_root / "night_lighthouse_beam"
    first_png = output / "beam_t0.png"
    first_metadata = first_png.with_suffix(".png.json")
    template = grid._default_actual_godot_capture_command(
        preflight.night_lane,
        NIGHT_LABELS[0],
    )
    values = {
        "current_project": str((ROOT / "godot_project").resolve()),
        "baseline_a_project": str(
            (preflight.baseline_root / "godot_project").resolve()
        ),
        "project": str((preflight.baseline_root / "godot_project").resolve()),
        "side": "baseline",
        "baseline": "baseline_a",
        "codebase": "baseline_a",
        "lane_id": NIGHT_LANE_ID,
        "frame": NIGHT_LABELS[0],
        "frame_label": NIGHT_LABELS[0],
        "out_png": str(first_png.resolve()),
        "out_metadata": str(first_metadata.resolve()),
        "out_dir": str(output.resolve()),
        "manifest": str(LANE_MANIFEST.resolve()),
        "required_current_commit": preflight.proof_commit,
        "shared_current_colyseus_dir": str((ROOT / "colyseus_server").resolve()),
    }
    command = grid._command_from_template(template, values)
    if not isinstance(command, list) or not command:
        raise BootstrapError("grid builder returned an empty night command")
    _run_checked(command, cwd=ROOT, runner=runner)

    pngs: list[Path] = []
    metadata_paths: list[Path] = []
    metadata_rows: list[dict[str, Any]] = []
    for label in NIGHT_LABELS:
        png = _require_png(output / f"{label}.png", label)
        metadata_path = png.with_suffix(".png.json")
        metadata = _load_json(metadata_path, f"{label} metadata")
        if metadata.get("receipt_pass") is not True:
            raise BootstrapError(f"{label} metadata says receipt_pass is not true")
        if metadata.get("temporal_continuous_run") is not True:
            raise BootstrapError(f"{label} is not from one continuous night process")
        if metadata.get("route_tick") != NIGHT_TICKS[len(metadata_rows)]:
            raise BootstrapError(f"{label} metadata has the wrong route tick")
        observed_offset = metadata.get("beam_phase_offset_target")
        if (
            isinstance(observed_offset, bool)
            or not isinstance(observed_offset, (int, float))
            or not math.isfinite(float(observed_offset))
            or abs(float(observed_offset) - NIGHT_OFFSETS[len(metadata_rows)]) > 1.0e-6
        ):
            raise BootstrapError(f"{label} metadata has the wrong phase offset")
        pngs.append(png)
        metadata_paths.append(metadata_path.resolve())
        metadata_rows.append(metadata)

    receipt_paths = {
        str(Path(str(row.get("receipt_path", ""))).expanduser().resolve())
        for row in metadata_rows
    }
    if len(receipt_paths) != 1:
        raise BootstrapError("night frames do not share one runtime receipt")
    night_receipt_path = Path(receipt_paths.pop())
    night_receipt = _load_json(night_receipt_path, "same-process night receipt")
    _scan_stale_phase_key(night_receipt, "night receipt")
    if night_receipt.get("pass") is not True:
        raise BootstrapError("same-process night receipt pass is not true")
    if night_receipt.get("route_identity") != NIGHT_ROUTE_IDENTITY:
        raise BootstrapError("same-process night receipt has the wrong route identity")
    if tuple(night_receipt.get("visual_route_target_ticks", [])) != NIGHT_TICKS:
        raise BootstrapError("same-process night receipt has the wrong route ticks")
    _validate_night_runtime_rows(night_receipt)
    temporal_id = night_receipt.get("temporal_capture_id")
    if not isinstance(temporal_id, str) or not temporal_id:
        raise BootstrapError("same-process night receipt lacks temporal_capture_id")
    for index, row in enumerate(metadata_rows):
        if row.get("temporal_capture_id") != temporal_id:
            raise BootstrapError(
                f"{NIGHT_LABELS[index]} metadata has a different temporal capture id"
            )
    return night_receipt, list(command), pngs, metadata_paths, night_receipt_path


def _find_moving_png(output: Path, sample: Mapping[str, Any]) -> Path:
    """Resolve one moving frame by its authoritative route tick."""

    tick = _integer(sample.get("route_tick"), "moving route_tick")
    expected = output / f"fl4359_perspective_frame_{tick:04d}.png"
    if expected.is_file():
        return _require_png(expected, f"moving route tick {tick}")
    matches = list(output.glob(f"fl4359_perspective_frame_*{tick}*.png"))
    if len(matches) != 1:
        raise BootstrapError(f"moving route tick {tick} does not own one PNG")
    return _require_png(matches[0], f"moving route tick {tick}")


def _normalized_attachment(
    *,
    path: Path,
    capture_id: str,
    temporal_capture_id: str,
    process_identity: str,
    label: str,
    source_path: Path,
    source_document: Mapping[str, Any],
    source_row: Mapping[str, Any],
) -> None:
    """Write one immutable metadata or runtime-truth wrapper from real facts."""

    _write_json_exclusive(
        path,
        {
            "schema": "fl4431.baseline_capture.attachment.v1",
            "capture_id": capture_id,
            "temporal_capture_id": temporal_capture_id,
            "process_identity": process_identity,
            "label": label,
            "source": _path_ref(source_path),
            "source_document": dict(source_document),
            "source_row": dict(source_row),
        },
    )


def _night_frame_map(receipt: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Index the exact three same-process night rows by operator-facing label."""

    raw = receipt.get("visual_frame_samples")
    if not isinstance(raw, list):
        raise BootstrapError("night receipt visual_frame_samples is missing")
    rows = {
        str(row.get("label")): dict(row)
        for row in raw
        if isinstance(row, dict) and isinstance(row.get("label"), str)
    }
    if tuple(rows) != NIGHT_LABELS:
        raise BootstrapError("night receipt rows are not ordered beam_t0/beam_t1/beam_t2")
    return rows


def _route_pose(
    label: str,
    tick: int,
    metadata: Mapping[str, Any],
    runtime_row: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one route pose entirely from adapter and same-process truth."""

    projection = metadata.get("view_mode")
    if projection not in {"ortho", "perspective"}:
        raise BootstrapError(f"{label} metadata has no supported projection")
    projection_params = metadata.get("projection_params")
    if not isinstance(projection_params, dict):
        raise BootstrapError(f"{label} metadata lacks projection_params")
    return {
        "label": label,
        "route_tick": tick,
        "player_position": _vec3(
            runtime_row.get("player_position"),
            f"{label} runtime player_position",
        ),
        "player_yaw": _number(
            runtime_row.get("player_yaw", metadata.get("player_yaw")),
            f"{label} player_yaw",
        ),
        "camera_position": _vec3(
            runtime_row.get("camera_position"),
            f"{label} runtime camera_position",
        ),
        "camera_yaw": _number(
            runtime_row.get("camera_yaw", metadata.get("camera_yaw")),
            f"{label} camera_yaw",
        ),
        "camera_pitch": _number(metadata.get("camera_pitch"), f"{label} camera_pitch"),
        "camera_roll": _number(metadata.get("camera_roll"), f"{label} camera_roll"),
        "airplane_position": _vec3(
            runtime_row.get("airplane_position"),
            f"{label} runtime airplane_position",
        ),
        "airplane_yaw": _number(
            runtime_row.get("airplane_yaw", metadata.get("airplane_yaw")),
            f"{label} airplane_yaw",
        ),
        "projection": projection,
        "fov_degrees": _number(metadata.get("camera_fov"), f"{label} camera_fov"),
        "near": _number(projection_params.get("near"), f"{label} near plane"),
        "far": _number(projection_params.get("far"), f"{label} far plane"),
    }


def _resolve_godot_identity(
    preflight: Preflight,
    runner: ProcessRunner,
) -> dict[str, Any]:
    """Bind the installed Godot bytes and Baseline import marker after capture."""

    requested = shutil.which("godot")
    if requested is None:
        raise BootstrapError("godot executable is not available on PATH")
    requested_path = Path(requested).resolve()
    version = _run_checked([str(requested_path), "--version"], cwd=ROOT, runner=runner)
    binary_format = _run_checked(
        ["file", "-b", str(requested_path)],
        cwd=ROOT,
        runner=runner,
    )
    marker = preflight.baseline_root / "godot_project" / ".godot" / "editor" / "project_metadata.cfg"
    if not marker.is_file():
        raise BootstrapError(f"Baseline Godot import marker is missing: {marker}")
    return {
        "execution_kind": "native-godot-baseline-capture",
        "godot_requested_path": str(requested_path),
        "godot_resolved_binary": _path_ref(requested_path),
        "godot_version": version.stdout.strip(),
        "godot_binary_format": binary_format.stdout.strip(),
        "imported_source_commit": preflight.baseline_commit,
        "imported_source_tree": preflight.baseline_tree,
        "project_import_marker": _path_ref(marker),
    }


def _normalise_capture(
    *,
    config: BootstrapConfig,
    preflight: Preflight,
    moving_receipt: Mapping[str, Any],
    moving_command: Sequence[str],
    moving_receipt_path: Path,
    night_receipt: Mapping[str, Any],
    night_command: Sequence[str],
    night_pngs: Sequence[Path],
    night_metadata_paths: Sequence[Path],
    night_receipt_path: Path,
    build_identity: Mapping[str, Any],
    created_at_utc: str,
) -> dict[str, Any]:
    """Build the exact non-promotable input consumed by the v2 builder."""

    manifest_sha = _file_sha256(LANE_MANIFEST)
    moving_sha = _file_sha256(moving_receipt_path)
    night_sha = _file_sha256(night_receipt_path)
    adapter_command_path = night_receipt_path.parent / "adapter_command.json"
    adapter_command_document = _load_json(
        adapter_command_path,
        "night inner adapter command receipt",
    )
    adapter_inner_command = adapter_command_document.get("command")
    if not isinstance(adapter_inner_command, list) or not adapter_inner_command or not all(
        isinstance(part, str) and part for part in adapter_inner_command
    ):
        raise BootstrapError("night inner adapter command receipt is incomplete")
    if adapter_command_document.get("returncode") != 0:
        raise BootstrapError("night inner adapter command receipt reports failure")
    moving_port = _command_int_value(moving_command, "--port", "moving command port")
    night_port = _command_int_value(
        adapter_inner_command,
        "--port",
        "night command port",
    )
    identity_seed = {
        "baseline_commit": preflight.baseline_commit,
        "proof_commit": preflight.proof_commit,
        "manifest_sha256": manifest_sha,
        "moving_receipt_sha256": moving_sha,
        "night_receipt_sha256": night_sha,
    }
    capture_id = f"fl4431-baseline-bootstrap-{_sha256_bytes(_canonical_bytes(identity_seed))[:24]}"
    night_temporal_id = night_receipt.get("temporal_capture_id")
    if not isinstance(night_temporal_id, str) or not night_temporal_id:
        raise BootstrapError("night receipt temporal_capture_id is missing")
    night_process_identity = f"godot-temporal-capture:{night_temporal_id}"
    moving_temporal_id = f"moving-receipt-sha256:{moving_sha}"
    moving_process_identity = moving_temporal_id

    night_rows = _night_frame_map(night_receipt)
    night_metadata = [
        _load_json(path, f"{NIGHT_LABELS[index]} adapter metadata")
        for index, path in enumerate(night_metadata_paths)
    ]
    poses = [
        _route_pose(
            label,
            NIGHT_TICKS[index],
            night_metadata[index],
            night_rows[label],
        )
        for index, label in enumerate(NIGHT_LABELS)
    ]

    source_frames: list[dict[str, Any]] = []
    moving_samples = moving_receipt.get("visual_frame_samples")
    if not isinstance(moving_samples, list) or len(moving_samples) < 2:
        raise BootstrapError("moving receipt lacks visual_frame_samples")
    moving_output = config.capture_root / "moving_airplane"
    for index, raw_sample in enumerate(moving_samples):
        if not isinstance(raw_sample, dict):
            raise BootstrapError(f"moving sample {index} is not an object")
        tick = _integer(raw_sample.get("route_tick"), f"moving sample {index} tick")
        label = f"moving_{index:02d}_tick_{tick}"
        png = _find_moving_png(moving_output, raw_sample)
        metadata_path = config.capture_root / "normalized" / f"{label}.metadata.json"
        truth_path = config.capture_root / "normalized" / f"{label}.runtime_truth.json"
        _normalized_attachment(
            path=metadata_path,
            capture_id=capture_id,
            temporal_capture_id=moving_temporal_id,
            process_identity=moving_process_identity,
            label=label,
            source_path=moving_receipt_path,
            source_document=moving_receipt,
            source_row=raw_sample,
        )
        _normalized_attachment(
            path=truth_path,
            capture_id=capture_id,
            temporal_capture_id=moving_temporal_id,
            process_identity=moving_process_identity,
            label=label,
            source_path=moving_receipt_path,
            source_document=moving_receipt,
            source_row=raw_sample,
        )
        source_frames.append(
            {
                "lane_id": "moving_perspective_airplane",
                "label": label,
                "arm": "baseline",
                "route_tick": tick,
                "capture_id": capture_id,
                "temporal_capture_id": moving_temporal_id,
                "process_identity": moving_process_identity,
                "png": _path_ref(png),
                "metadata": _path_ref(metadata_path),
                "runtime_truth": _path_ref(truth_path),
            }
        )

    for index, label in enumerate(NIGHT_LABELS):
        metadata_path = config.capture_root / "normalized" / f"{label}.metadata.json"
        truth_path = config.capture_root / "normalized" / f"{label}.runtime_truth.json"
        _normalized_attachment(
            path=metadata_path,
            capture_id=capture_id,
            temporal_capture_id=night_temporal_id,
            process_identity=night_process_identity,
            label=label,
            source_path=night_metadata_paths[index],
            source_document=night_metadata[index],
            source_row=night_rows[label],
        )
        _normalized_attachment(
            path=truth_path,
            capture_id=capture_id,
            temporal_capture_id=night_temporal_id,
            process_identity=night_process_identity,
            label=label,
            source_path=night_receipt_path,
            source_document=night_receipt,
            source_row=night_rows[label],
        )
        source_frames.append(
            {
                "lane_id": NIGHT_LANE_ID,
                "label": label,
                "arm": "baseline",
                "route_tick": NIGHT_TICKS[index],
                "capture_id": capture_id,
                "temporal_capture_id": night_temporal_id,
                "process_identity": night_process_identity,
                "png": _path_ref(night_pngs[index]),
                "metadata": _path_ref(metadata_path),
                "runtime_truth": _path_ref(truth_path),
            }
        )

    first_metadata = night_metadata[0]
    requested_width, requested_height = _parse_dimensions(
        first_metadata.get("requested_capture_resolution"),
        "night requested capture resolution",
    )
    actual_width, actual_height = _parse_dimensions(
        first_metadata.get("capture_resolution"),
        "night actual capture resolution",
    )
    ansi_width, ansi_height = _parse_dimensions(
        first_metadata.get("ansi_size"),
        "night ANSI dimensions",
    )
    for index, metadata in enumerate(night_metadata[1:], start=1):
        if _parse_dimensions(
            metadata.get("capture_resolution"),
            f"{NIGHT_LABELS[index]} actual capture resolution",
        ) != (actual_width, actual_height):
            raise BootstrapError("night frames have different actual dimensions")

    server_commit = first_metadata.get("server_commit")
    server_root = first_metadata.get("server_source_root")
    server_fingerprint = first_metadata.get("server_runtime_scope_fingerprint")
    if not isinstance(server_commit, str):
        raise BootstrapError("night metadata lacks server_commit")
    _full_hex(server_commit, "night server commit", 40)
    if not isinstance(server_root, str) or not Path(server_root).is_dir():
        raise BootstrapError("night metadata lacks an existing server_source_root")
    if not isinstance(server_fingerprint, str):
        raise BootstrapError("night metadata lacks server runtime fingerprint")
    _full_hex(server_fingerprint, "night server runtime fingerprint", 64)
    moving_server_fingerprint = moving_receipt.get("server_runtime_scope_fingerprint")
    if moving_server_fingerprint != server_fingerprint:
        raise BootstrapError("day and night captures used different server source bytes")
    moving_server_root = moving_receipt.get("server_source_dir")
    if Path(str(moving_server_root)).resolve() != Path(server_root).resolve():
        raise BootstrapError("day and night captures used different server roots")
    if moving_receipt.get("server_git_head") != server_commit:
        raise BootstrapError("day and night captures used different server commits")
    server_process_identity = (
        "capture-receipts-sha256:"
        + _sha256_bytes(
            _canonical_bytes(
                {
                    "moving": moving_sha,
                    "night": night_sha,
                    "night_temporal_capture_id": night_temporal_id,
                    "night_adapter_command_sha256": _file_sha256(
                        adapter_command_path
                    ),
                }
            )
        )
    )
    endpoint = json.dumps(
        {
            "moving": f"localhost:{moving_port}",
            "night": f"localhost:{night_port}",
        },
        sort_keys=True,
        separators=(",", ":"),
    )

    physical_input = moving_receipt.get("first_active_flight_intent")
    if not isinstance(physical_input, dict) or not physical_input:
        raise BootstrapError("moving receipt lacks first_active_flight_intent")
    if night_receipt.get("first_active_flight_intent") != physical_input:
        raise BootstrapError("day and night captures used different physical flight input")
    tick_zero_kind = moving_receipt.get("route_tick_zero_kind")
    if tick_zero_kind != NIGHT_TICK_ZERO_KIND:
        raise BootstrapError("moving receipt has the wrong route tick-zero owner")

    world_time = _number(first_metadata.get("world_time"), "night world_time")
    day_cycle = _moving_day_cycle(moving_command)
    features = preflight.predecessor.get("features")
    if not isinstance(features, list) or not features or not all(
        isinstance(item, str) and item for item in features
    ):
        raise BootstrapError("predecessor features are missing")
    lane_features = preflight.night_lane.get("feature_flags")
    if not isinstance(lane_features, list) or not all(
        isinstance(item, str) and item for item in lane_features
    ):
        raise BootstrapError("night lane feature flags are missing")
    merged_features = list(dict.fromkeys([*features, *lane_features]))

    return {
        "schema": CAPTURE_SCHEMA,
        "capture_classification": "non_promotable_diagnostic",
        "capture_id": capture_id,
        "temporal_capture_id": night_temporal_id,
        "process_identity": night_process_identity,
        "created_at_utc": created_at_utc,
        "baseline_source_commit": preflight.baseline_commit,
        "baseline_source_tree": preflight.baseline_tree,
        "proof_tool_commit": preflight.proof_commit,
        "lane_manifest_path": str(LANE_MANIFEST.resolve()),
        "lane_manifest_sha256": manifest_sha,
        "argument_vectors": {
            "day": list(moving_command),
            "night": list(night_command),
        },
        "route": {
            "lane_id": NIGHT_LANE_ID,
            "identity": NIGHT_ROUTE_IDENTITY,
            "physical_input_action": json.dumps(
                physical_input,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "tick_zero_kind": tick_zero_kind,
            "warmup_tick": NIGHT_TICKS[0],
            "target_ticks": list(NIGHT_TICKS),
            "tick_stride": NIGHT_TICKS[1] - NIGHT_TICKS[0],
            "frame_labels": list(NIGHT_LABELS),
            "poses": poses,
        },
        "dimensions": {
            "requested_width": requested_width,
            "requested_height": requested_height,
            "actual_width": actual_width,
            "actual_height": actual_height,
            "ansi_width": ansi_width,
            "ansi_height": ansi_height,
            "fullscreen": first_metadata.get("fullscreen") is True,
            "ascii": first_metadata.get("ascii_enabled") is True,
        },
        "time_profile": {
            "day_cycle_value": day_cycle,
            "night_cycle_value": world_time / 24.0,
            "day_world_hour": day_cycle * 24.0,
            "night_world_hour": world_time,
        },
        "server_identity": {
            "server_commit": server_commit,
            "server_source_root": str(Path(server_root).resolve()),
            "server_runtime_scope_fingerprint": server_fingerprint,
            "server_endpoint": endpoint,
            "server_process_identity": server_process_identity,
        },
        "baseline_build_identity": dict(build_identity),
        "features": merged_features,
        "source_frames": source_frames,
    }


def bootstrap(
    config: BootstrapConfig,
    *,
    runner: ProcessRunner = _default_runner,
    package_module: ModuleType | None = None,
    grid_module: ModuleType | None = None,
    builder_module: ModuleType | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    """Execute both capture owners, write capture evidence, then create v2."""

    for output in (config.output_receipt, config.capture_receipt):
        if output.expanduser().exists():
            raise BootstrapError(f"refusing to overwrite output: {output}")
    if config.capture_root.expanduser().exists():
        raise BootstrapError(
            f"capture root must not exist before bootstrap: {config.capture_root}"
        )
    if config.port <= 0 or config.port > 65535:
        raise BootstrapError("port must be between 1 and 65535")
    if config.moving_frames < 2:
        raise BootstrapError("moving capture must contain at least two frames")
    if config.moving_visual_stride <= 0 or config.moving_visual_limit < 2:
        raise BootstrapError("moving visual capture must contain time-separated frames")

    preflight = _preflight(config, runner)
    package = package_module or _load_module(
        "scripts/run_fl4359_acceptance_package.py",
        "fl4431_bootstrap_acceptance_package",
    )
    grid = grid_module or _load_module(
        "scripts/build_godot_canonical_png_grid.py",
        "fl4431_bootstrap_grid_builder",
    )
    builder = builder_module or _load_module(
        "scripts/build_fl4431_baseline_receipt_v2.py",
        "fl4431_bootstrap_receipt_builder",
    )

    config.capture_root.mkdir(parents=True, exist_ok=False)
    moving_receipt, moving_command, moving_receipt_path = _capture_moving(
        config,
        preflight,
        package,
    )
    (
        night_receipt,
        night_command,
        night_pngs,
        night_metadata_paths,
        night_receipt_path,
    ) = _capture_night(config, preflight, grid, runner)
    build_identity = _resolve_godot_identity(preflight, runner)
    timestamp = created_at_utc or datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    capture = _normalise_capture(
        config=config,
        preflight=preflight,
        moving_receipt=moving_receipt,
        moving_command=moving_command,
        moving_receipt_path=moving_receipt_path,
        night_receipt=night_receipt,
        night_command=night_command,
        night_pngs=night_pngs,
        night_metadata_paths=night_metadata_paths,
        night_receipt_path=night_receipt_path,
        build_identity=build_identity,
        created_at_utc=timestamp,
    )
    _write_json_exclusive(config.capture_receipt, capture)

    # Recheck both repositories after capture. A capture that changed source or
    # proof tools is evidence of a contaminated run and cannot reach promotion.
    _assert_clean_checkout(preflight.baseline_root, "Baseline source", runner)
    _assert_clean_checkout(ROOT, "proof-tool", runner)
    for relative in EXECUTABLE_TOOL_PATHS:
        _assert_tool_bytes_match_head(ROOT, relative, runner)

    receipt = builder.create_receipt(
        output_path=config.output_receipt.expanduser().absolute(),
        designation_record_path=config.designation_record.expanduser().resolve(),
        capture_receipt_path=config.capture_receipt.expanduser().resolve(),
        lane_manifest_path=LANE_MANIFEST.resolve(),
        source_root=preflight.baseline_root,
        proof_tool_root=ROOT.resolve(),
        created_at_utc=timestamp,
    )
    if not isinstance(receipt, dict) or receipt.get("schema") != builder.SCHEMA:
        raise BootstrapError("v2 receipt builder did not return its exact schema")
    return receipt


def _parser() -> argparse.ArgumentParser:
    """Create the one CLI that can bootstrap a designated successor receipt."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predecessor-receipt", type=Path, required=True)
    parser.add_argument("--designation-record", type=Path, required=True)
    parser.add_argument("--output-receipt", type=Path, required=True)
    parser.add_argument("--capture-receipt", type=Path, required=True)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=2581)
    parser.add_argument("--moving-frames", type=int, default=240)
    parser.add_argument("--moving-visual-stride", type=int, default=30)
    parser.add_argument("--moving-visual-limit", type=int, default=6)
    parser.add_argument("--godot-timeout", type=int, default=900)
    parser.add_argument("--server-timeout", type=int, default=45)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the bootstrap and print the immutable successor receipt path."""

    args = _parser().parse_args(argv)
    config = BootstrapConfig(
        predecessor_receipt=args.predecessor_receipt,
        designation_record=args.designation_record,
        output_receipt=args.output_receipt,
        capture_receipt=args.capture_receipt,
        capture_root=args.capture_root,
        port=args.port,
        moving_frames=args.moving_frames,
        moving_visual_stride=args.moving_visual_stride,
        moving_visual_limit=args.moving_visual_limit,
        godot_timeout=args.godot_timeout,
        server_timeout=args.server_timeout,
    )
    try:
        receipt = bootstrap(config)
    except BootstrapError as exc:
        print(f"FL4431_BASELINE_V2_BOOTSTRAP_FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "FL4431_BASELINE_V2_BOOTSTRAP_PASS "
        f"schema={receipt['schema']} output={config.output_receipt.expanduser().absolute()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
