#!/usr/bin/env python3
"""Fail-closed FL-4743 SITE-3 waterfall runtime behavior gate.

This checker is deliberately narrower than the broad feature-presence intake.
It consumes a headed capture from the real Colyseus/Godot route and measures
the behavior the player is meant to see: a localized broken ribbon, downward
travel, and receiver-preserving variation.  A PNG is not treated as proof of
water merely because it contains a pale vertical streak.  The expected site
footprint is projected from the archived, provenance-matching SITE-3 receipt;
all current frames must match the same route identity.

The checker can also launch the existing manual-play launcher.  That launcher
loads the current working tree at process start, so this route records both the
commit and the runtime source digest in its receipt.  No renderer or compositor
code is imported or modified here.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    from PIL import Image
except ImportError:  # pragma: no cover - reported as a fail-closed setup error
    Image = None  # type: ignore[assignment]

try:
    import numpy as np
except ImportError:  # pragma: no cover - reported as a fail-closed setup error
    np = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / ".run" / "fl4743_site3_waterfall_accept_20260828T035447Z"
DEFAULT_OUT = ROOT / ".run" / "fl4781_waterfall_w17_current_site3"
DEFAULT_PORT = 2767
EXPECTED_PLAYER = (165.4, 18.84375, -73.0)
EXPECTED_CAMERA = (186.612, 35.34375, -47.7205429077148)
EXPECTED_YAW_DEG = 40.0
EXPECTED_PITCH = -0.463640006066219
EXPECTED_REQUEST = (3600, 2252)
EXPECTED_PRESENTED = (3600, 2194)
EXPECTED_FRAMES_PER_WINDOW = 4
WINDOWS = ("day", "night")
SCHEMA = "fl4743.waterfall_behavior_gate.v1"


class SetupFailure(RuntimeError):
    """The route or input could not be established; no behavior verdict."""


class MeasurementFailure(RuntimeError):
    """The route ran but did not provide the required behavior."""


@dataclass(frozen=True)
class Frame:
    path: Path
    sidecar: dict[str, Any]
    image: Any
    window: str
    index: int


def _close(a: float, b: float, eps: float = 1.0e-3) -> bool:
    return abs(float(a) - float(b)) <= eps


def _vec_close(actual: Any, expected: tuple[float, ...], eps: float = 1.0e-3) -> bool:
    return isinstance(actual, (list, tuple)) and len(actual) >= len(expected) and all(
        _close(float(actual[i]), expected[i], eps) for i in range(len(expected))
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SetupFailure(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SetupFailure(f"JSON object required: {path}")
    return value


def _matrix_from_sidecar(sidecar: dict[str, Any]) -> list[float]:
    matrix = sidecar.get("world_to_clip_matrix", sidecar.get("world_to_clip"))
    if not isinstance(matrix, list) or len(matrix) != 16:
        raise SetupFailure("world_to_clip 4x4 matrix missing from provenance sidecar")
    return [float(v) for v in matrix]


def validate_route_sidecar(sidecar: dict[str, Any], *, image_size: tuple[int, int]) -> None:
    """Reject a frame unless it is the exact operator-designated SITE-3 route."""

    if not _vec_close(sidecar.get("actor_presentation_reference_world"), EXPECTED_PLAYER, 2.0e-3):
        raise SetupFailure("player reference world does not match SITE-3")
    if not _vec_close(sidecar.get("camera_origin"), EXPECTED_CAMERA, 2.0e-3):
        raise SetupFailure("camera origin does not match SITE-3")
    if not _close(float(sidecar.get("camera_yaw", math.nan)), math.radians(EXPECTED_YAW_DEG), 2.0e-3):
        raise SetupFailure("camera yaw does not match SITE-3")
    if not _close(float(sidecar.get("camera_pitch", math.nan)), EXPECTED_PITCH, 2.0e-3):
        raise SetupFailure("camera pitch does not match SITE-3")
    if tuple(int(v) for v in sidecar.get("saved_image_size", ())) != EXPECTED_PRESENTED:
        raise SetupFailure("presented image size is not 3600x2194")
    if tuple(int(v) for v in image_size) != EXPECTED_PRESENTED:
        raise SetupFailure("PNG image is not 3600x2194")
    if not bool(sidecar.get("fl4359_proof_valid")):
        raise SetupFailure("frame lacks proof-valid capture marker")
    if sidecar.get("camera_transform_source") != "live_camera_world_true_perspective":
        raise SetupFailure("camera transform is not live true perspective")
    if int(float(sidecar.get("projection_mode", -1))) != 2:
        raise SetupFailure("projection mode is not true perspective")
    if not bool(sidecar.get("world_to_clip_valid")):
        raise SetupFailure("world_to_clip provenance is not valid")
    _matrix_from_sidecar(sidecar)


def _project(matrix: list[float], point: tuple[float, float, float], size: tuple[int, int]) -> tuple[float, float]:
    """Project a world point through the sidecar's column-major clip matrix."""

    x, y, z = point
    q = (
        matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12],
        matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13],
        matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14],
        matrix[3] * x + matrix[7] * y + matrix[11] * z + matrix[15],
    )
    if abs(q[3]) < 1.0e-8:
        raise SetupFailure("waterfall site projection has zero clip w")
    ndc_x, ndc_y = q[0] / q[3], q[1] / q[3]
    return ((ndc_x + 1.0) * size[0] * 0.5, (1.0 - ndc_y) * size[1] * 0.5)


def site_footprint(sidecar: dict[str, Any], size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Return a localized union box around both active operator waterfall rows."""

    matrix = _matrix_from_sidecar(sidecar)
    points = []
    for x, z, top, bottom in (
        (132.60, -60.78, 23.21, 10.90),
        (134.80, -61.60, 22.81, 10.90),
    ):
        points.extend((_project(matrix, (x, top, z), size), _project(matrix, (x, bottom, z), size)))
    arr = np.asarray(points, dtype=float)
    # 80 px margin covers the authored radius and one cell of camera/grid
    # quantization while staying far smaller than the whole viewport.
    x0, y0 = np.floor(arr.min(axis=0) - 80.0).astype(int)
    x1, y1 = np.ceil(arr.max(axis=0) + 80.0).astype(int)
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(size[0], int(x1)), min(size[1], int(y1))
    if x1 - x0 < 40 or y1 - y0 < 80:
        raise SetupFailure(f"projected SITE-3 footprint is invalid: {(x0, y0, x1, y1)}")
    return x0, y0, x1, y1


def _load_archive() -> tuple[list[dict[str, Any]], tuple[int, int, int, int]]:
    if Image is None or np is None:
        raise SetupFailure("Pillow and numpy are required for the behavior gate")
    if not ARCHIVE.is_dir():
        raise SetupFailure(f"immutable SITE-3 calibration archive missing: {ARCHIVE}")
    sidecars: list[dict[str, Any]] = []
    images = []
    for stem in ("d0", "d1", "d2", "d3", "n0", "n1", "n2", "n3"):
        image_path = ARCHIVE / f"{stem}.png"
        sidecar_path = ARCHIVE / f"{stem}.png.pixel_camera.json"
        if not image_path.is_file() or not sidecar_path.is_file():
            raise SetupFailure(f"calibration pair missing for {stem}")
        image = Image.open(image_path).convert("RGB")
        validate_route_sidecar(_read_json(sidecar_path), image_size=image.size)
        sidecars.append(_read_json(sidecar_path))
        images.append(np.asarray(image, dtype=np.float32) / 255.0)
    first = sidecars[0]
    footprint = site_footprint(first, images[0].shape[1::-1])
    # Require all calibration matrices and footprints to agree. This prevents
    # an old image from silently becoming a generic colour template.
    for sidecar, image in zip(sidecars[1:], images[1:]):
        if site_footprint(sidecar, image.shape[1::-1]) != footprint:
            raise SetupFailure("calibration SITE-3 projection footprint is inconsistent")
    return images, footprint


def _water_score(rgb: Any) -> Any:
    """Return pale-cyan water likelihood, not brightness alone."""

    # Water ink is blue/cyan in the authored family. Requiring chroma rejects
    # grey sky and grey statue values that a bright-column detector would admit.
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    chroma = (b - r) + 0.45 * (g - r)
    return np.clip((chroma - 0.055) / 0.18, 0.0, 1.0)


def _crop(image: Any, box: tuple[int, int, int, int]) -> Any:
    x0, y0, x1, y1 = box
    return image[y0:y1, x0:x1]


def _calibration_template(images: list[Any], box: tuple[int, int, int, int]) -> Any:
    masks = [(_water_score(_crop(img, box)) >= 0.42) for img in images]
    # Two independent calibration windows must agree before a pixel is used as
    # a site support. This is a structural footprint, not a current verdict.
    return np.sum(np.asarray(masks, dtype=np.uint8), axis=0) >= 2


def _frame_measurement(frame: Any, support: Any) -> dict[str, float]:
    score = _water_score(frame)
    candidate = score >= 0.42
    # Structural support permits small current raster drift but keeps the gate
    # local to the eye-ribbon projection obtained from the immutable receipt.
    support_dilated = support.copy()
    support_dilated[:-4, :] |= support[4:, :]
    support_dilated[4:, :] |= support[:-4, :]
    support_dilated[:, :-4] |= support[:, 4:]
    support_dilated[:, 4:] |= support[:, :-4]
    localized = candidate & support_dilated
    row_occ = localized.mean(axis=1)
    col_occ = localized.mean(axis=0)
    active = row_occ >= 0.025
    if active.any():
        lo, hi = np.where(active)[0][[0, -1]]
        core = localized[lo : hi + 1]
    else:
        lo, hi, core = 0, 0, localized[:0]
    core_support = support_dilated[lo : hi + 1]
    gap_fraction = (
        float(1.0 - core[core_support].mean())
        if core.size and bool(core_support.any())
        else 1.0
    )
    # A uniform filled column has low within-ribbon contrast. Include both
    # candidate ink and preserved support pixels in the local variation sample.
    local_rgb = frame[localized | support_dilated]
    luma = local_rgb.mean(axis=1) if local_rgb.size else np.asarray([], dtype=float)
    luma_std = float(np.std(luma)) if luma.size else 0.0
    luma_p90_p10 = float(np.percentile(luma, 90) - np.percentile(luma, 10)) if luma.size else 0.0
    candidate_rgb = frame[localized]
    candidate_luma = candidate_rgb.mean(axis=1) if candidate_rgb.size else np.asarray([], dtype=float)
    candidate_luma_std = float(np.std(candidate_luma)) if candidate_luma.size else 0.0
    candidate_luma_p90_p10 = (
        float(np.percentile(candidate_luma, 90) - np.percentile(candidate_luma, 10))
        if candidate_luma.size
        else 0.0
    )
    return {
        "candidate_pixels": float(localized.sum()),
        "candidate_fraction": float(localized.mean()),
        "active_row_count": float(active.sum()),
        "vertical_span_px": float(max(0, hi - lo + 1)),
        "gap_fraction": gap_fraction,
        "luma_std": luma_std,
        "luma_p90_p10": luma_p90_p10,
        "candidate_luma_std": candidate_luma_std,
        "candidate_luma_p90_p10": candidate_luma_p90_p10,
        "row_signal_mean": float(row_occ.mean()),
    }


def _downward_motion(previous: Any, current: Any) -> dict[str, float]:
    """Cross-correlate site row signals; positive image-y shift is downward."""

    if previous.ndim == 3:
        a = previous.mean(axis=(1, 2)).astype(float)
    else:
        a = previous.mean(axis=1).astype(float)
    if current.ndim == 3:
        b = current.mean(axis=(1, 2)).astype(float)
    else:
        b = current.mean(axis=1).astype(float)
    a -= a.mean()
    b -= b.mean()
    norm = float(np.linalg.norm(a) * np.linalg.norm(b))
    if norm <= 1.0e-9:
        return {"best_shift_px": 0.0, "best_corr": 0.0, "corr_at_zero": 0.0}
    correlations: dict[int, float] = {}
    for shift in range(-80, 81):
        if shift >= 0:
            aa, bb = a[: max(1, len(a) - shift)], b[shift:]
        else:
            aa, bb = a[-shift:], b[: len(b) + shift]
        correlations[shift] = float(np.dot(aa, bb) / norm)
    best_shift = max(correlations, key=correlations.get)
    return {
        "best_shift_px": float(best_shift),
        "best_corr": float(correlations[best_shift]),
        "corr_at_zero": float(correlations[0]),
    }


def _source_identity(out_dir: Path) -> dict[str, Any]:
    receipt = out_dir / "manual_route_receipt.json"
    result: dict[str, Any] = {}
    if receipt.is_file():
        result["manual_route_receipt"] = _read_json(receipt)
    try:
        result["head"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        result["head"] = None
    return result


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _write_command(path: Path, request_id: int, command: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(f"{request_id}\n{command}\n", encoding="utf-8")
    tmp.replace(path)


def _command(channel: Path, request_id: int, command: str, timeout: float = 20.0) -> str:
    ack = channel.with_suffix(".ack")
    _write_command(channel.with_suffix(".cmd"), request_id, command)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ack.is_file():
            lines = ack.read_text(encoding="utf-8", errors="replace").splitlines()
            if lines and lines[0].startswith(f"{request_id} "):
                response = lines[0]
                if " ERR " in response or response.endswith(" ERR"):
                    raise SetupFailure(f"Godot command failed: {response}")
                return response
        time.sleep(0.15)
    raise SetupFailure(f"timed out waiting for command acknowledgement: {command}")


def _run_current(out_dir: Path, port: int) -> dict[str, Any]:
    if not _port_free(port):
        raise SetupFailure(f"port {port} is occupied immediately before launch")
    # Godot resolves FileAccess command-channel paths relative to the project,
    # while the driver runs from the repository root. Absolute paths are part of
    # the route identity; a relative channel can report readiness yet never
    # receive a command.
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    launcher_log = out_dir / "waterfall_gate_launcher.log"
    channel = out_dir / "waterfall_gate_channel"
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "launch_fl4359_manual_play.py"),
        "--port",
        str(port),
        "--out",
        str(out_dir),
        "--windowed",
        "--resolution",
        "3600x2252",
        "--perspective",
        "--no-window-activation",
        f"--extra-user-arg=--fl4359-command-channel={channel}",
    ]
    launch_env = dict(os.environ)
    # The launcher forwards these values to the real Colyseus authority. The
    # authority resolves the player Y from the map, producing the recorded
    # [165.4, 18.84375, -73.0] presentation reference without a client warp.
    launch_env["PLAYER_SPAWN_X"] = "165.4"
    launch_env["PLAYER_SPAWN_Z"] = "-73.0"
    with launcher_log.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            cmd, cwd=ROOT, env=launch_env, stdout=log, stderr=subprocess.STDOUT, text=True
        )
    godot_log = out_dir / "godot_client.log"
    try:
        ready_deadline = time.monotonic() + 180.0
        while time.monotonic() < ready_deadline:
            if proc.poll() is not None:
                raise SetupFailure(f"manual launcher exited {proc.returncode}; see {launcher_log}")
            if godot_log.is_file() and "FL4359_COMMAND_CHANNEL_READY" in godot_log.read_text(encoding="utf-8", errors="replace"):
                break
            time.sleep(0.25)
        else:
            raise SetupFailure(f"command channel readiness timeout; see {launcher_log} and {godot_log}")
        request_id = 1
        _command(channel, request_id, "pose 165.4 18.84375 -73.0 40.0")
        request_id += 1
        _command(channel, request_id, "night 0")
        request_id += 1
        for i in range(EXPECTED_FRAMES_PER_WINDOW):
            # Each no-op marker advances the actual game loop by at least one
            # frame, while preserving the exact player/camera pose.
            _command(channel, request_id, f"say waterfall_day_{i}")
            request_id += 1
            _command(channel, request_id, f"shot {out_dir / f'day{i}.png'}")
            request_id += 1
        _command(channel, request_id, "night 1")
        request_id += 1
        for i in range(EXPECTED_FRAMES_PER_WINDOW):
            _command(channel, request_id, f"say waterfall_night_{i}")
            request_id += 1
            _command(channel, request_id, f"shot {out_dir / f'night{i}.png'}")
            request_id += 1
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=20.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10.0)
    return {
        "argv": cmd,
        "port": port,
        "launcher_pid": proc.pid,
        "godot_pid": (_read_json(out_dir / "window_activation.json").get("engine_pid")
                       if (out_dir / "window_activation.json").is_file() else None),
        "launcher_log": str(launcher_log),
        "godot_log": str(godot_log),
        "completion_signal": "launcher_exit_after_SIGINT_and_capture_files",
    }


def _load_current_frames(out_dir: Path) -> list[Frame]:
    if Image is None or np is None:
        raise SetupFailure("Pillow and numpy are required for the behavior gate")
    route_receipt_path = out_dir / "manual_route_receipt.json"
    route = _read_json(route_receipt_path)
    command = route.get("command")
    if route.get("schema") != "fl4359.manual_route_receipt.v1":
        raise SetupFailure("current route receipt schema is not the manual-play schema")
    if route.get("map") != "asciicker_spike" or route.get("scene") != "res://scenes/Main.tscn":
        raise SetupFailure("current route receipt map/scene is not SITE-3")
    if route.get("mode") != "perspective" or route.get("input_authority") != "real_user_input_only":
        raise SetupFailure("current route is not the headed real-input perspective route")
    if not isinstance(command, list) or "--resolution" not in command or "3600x2252" not in command or "--perspective" not in command:
        raise SetupFailure("current route receipt does not prove the 3600x2252 perspective request")
    if not route.get("start_head") or not isinstance(route.get("start_source_state"), dict):
        raise SetupFailure("current route source identity receipt is incomplete")
    frames: list[Frame] = []
    for window in WINDOWS:
        for index in range(EXPECTED_FRAMES_PER_WINDOW):
            path = out_dir / f"{window}{index}.png"
            sidecar_path = out_dir / f"{window}{index}.png.pixel_camera.json"
            if not path.is_file() or not sidecar_path.is_file():
                raise SetupFailure(f"current {window}{index} frame or sidecar missing")
            image = Image.open(path).convert("RGB")
            validate_route_sidecar(_read_json(sidecar_path), image_size=image.size)
            frames.append(Frame(path, _read_json(sidecar_path), np.asarray(image, dtype=np.float32) / 255.0, window, index))
    return frames


def analyze_capture(out_dir: Path) -> dict[str, Any]:
    calibration, footprint = _load_archive()
    current = _load_current_frames(out_dir)
    x0, y0, x1, y1 = footprint
    support = _calibration_template(calibration, footprint)
    calibration_measurements = [_frame_measurement(_crop(image, footprint), support) for image in calibration]
    measurements = [_frame_measurement(_crop(frame.image, footprint), support) for frame in current]
    by_window = {window: [m for m, f in zip(measurements, current) if f.window == window] for window in WINDOWS}
    motion: dict[str, list[dict[str, float]]] = {}
    for window in WINDOWS:
        fs = [f for f in current if f.window == window]
        motion[window] = [_downward_motion(_water_score(_crop(fs[i].image, footprint)), _water_score(_crop(fs[i + 1].image, footprint))) for i in range(len(fs) - 1)]
    avg = lambda values, key: float(np.mean([float(v[key]) for v in values])) if values else 0.0
    # Calibration establishes the accepted site's shape/variation scale. The
    # current run must retain a meaningful portion of that localized structure.
    cal_gap = avg(calibration_measurements, "gap_fraction")
    cur_gap = avg(measurements, "gap_fraction")
    cur_candidate = avg(measurements, "candidate_fraction")
    cur_std = avg(measurements, "luma_std")
    cur_span = avg(measurements, "vertical_span_px")
    all_motion = motion["day"] + motion["night"]
    positive_motion = [m for m in all_motion if m["best_corr"] >= 0.18 and m["best_shift_px"] >= 2.0]
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "target_identity": {
            "site": "SITE-3",
            "player_reference_world": EXPECTED_PLAYER,
            "camera_origin": EXPECTED_CAMERA,
            "camera_yaw_deg": EXPECTED_YAW_DEG,
            "camera_pitch": EXPECTED_PITCH,
            "request_resolution": EXPECTED_REQUEST,
            "presented_resolution": EXPECTED_PRESENTED,
            "windows": list(WINDOWS),
            "frames_per_window": EXPECTED_FRAMES_PER_WINDOW,
        },
        "source_identity": _source_identity(out_dir),
        "calibration": {
            "archive": str(ARCHIVE),
            "footprint_xyxy": [x0, y0, x1, y1],
            "frames": [f"{w}{i}" for w in ("d", "n") for i in range(4)],
            "mean_gap_fraction": cal_gap,
            "mean_candidate_fraction": avg(calibration_measurements, "candidate_fraction"),
            "mean_luma_std": avg(calibration_measurements, "luma_std"),
            "mean_vertical_span_px": avg(calibration_measurements, "vertical_span_px"),
        },
        "current_frames": [
            {"window": f.window, "index": f.index, "path": str(f.path), "frame_index": f.sidecar.get("frame_index"), "time": f.sidecar.get("frame_time_seconds")}
            for f in current
        ],
        "measurements": measurements,
        "motion": motion,
        "summary": {
            "current_mean_gap_fraction": cur_gap,
            "current_mean_candidate_fraction": cur_candidate,
            "current_mean_luma_std": cur_std,
            "current_mean_vertical_span_px": cur_span,
            "positive_downward_pairs": len(positive_motion),
            "motion_pairs": len(all_motion),
        },
    }
    # Fail-closed behavior criteria. Thresholds are relative to the immutable
    # accepted structural calibration except for the explicit minimum geometry.
    checks = {
        "localized_ribbon": cur_span >= 100.0 and cur_candidate >= 0.005,
        "calibrated_site_coverage": cur_candidate >= avg(calibration_measurements, "candidate_fraction") * 0.20,
        "real_spatial_gaps": cur_gap >= max(0.08, cal_gap * 0.35),
        "downward_temporal_travel": len(all_motion) >= 4 and len(positive_motion) >= max(3, math.ceil(len(all_motion) * 0.45)),
        "receiver_preserving_variation": cur_std >= max(0.012, avg(calibration_measurements, "luma_std") * 0.30) and cur_gap >= 0.08,
    }
    result["checks"] = checks
    result["verdict"] = "PASS" if all(checks.values()) else "RED"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--analyze-only", action="store_true")
    args = parser.parse_args(argv)
    receipt: dict[str, Any] = {"schema": SCHEMA, "out": str(args.out), "port": args.port}
    try:
        if not args.analyze_only:
            receipt["run"] = _run_current(args.out, args.port)
        result = analyze_capture(args.out)
        receipt.update(result)
        (args.out / "waterfall_behavior_receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0 if result["verdict"] == "PASS" else 1
    except SetupFailure as exc:
        receipt.update({"verdict": "SETUP_FAILURE", "reason": str(exc)})
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "waterfall_behavior_receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 2
    except Exception as exc:  # fail closed on malformed/unknown capture conditions
        receipt.update({"verdict": "SETUP_FAILURE", "reason": f"unexpected gate error: {exc}"})
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "waterfall_behavior_receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
