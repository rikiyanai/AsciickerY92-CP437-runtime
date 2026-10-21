#!/usr/bin/env python3
"""Build an FL-4359 receipt-verified Baseline-A/current visual review grid.

This script packages existing moving-frame captures into a labeled PNG and
manifest. It does not capture, mutate, analyze, accept, close, or prove the
runtime lane by itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import shutil
from datetime import datetime, timezone
from pathlib import Path
from textwrap import wrap
from typing import Any

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
POSE_TOLERANCE_WORLD = 0.5
MIN_STRUCTURAL_REVIEW_SECONDS = 2.0
MIN_STRUCTURAL_REVIEW_FPS = 24.0
STRUCTURAL_LOCAL_SHIFT_P95_LIMIT_PX = 2.5
STRUCTURAL_REGION_BLOCK_P95_LIMIT_PCT = 12.0
STRUCTURAL_SETTLING_PULSE_LIMIT_PCT = 8.0
STRUCTURAL_SHORELINE_DELTA_LIMIT_PCT = 6.0


def repo_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (ROOT / path).resolve()


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_text(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        text=True,
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    ).stdout.strip()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def font(size: int) -> ImageFont.ImageFont:
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


def text_lines(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont, width: int) -> list[str]:
    result: list[str] = []
    for raw in text.splitlines():
        if not raw:
            result.append("")
            continue
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
                pieces = wrap(word, max(8, width // 9))
                result.extend(pieces[:-1])
                current = pieces[-1] if pieces else word
        if current:
            result.append(current)
    return result


def draw_box(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    fnt: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    text_fill: tuple[int, int, int] = (242, 242, 236),
) -> None:
    draw.rectangle(box, fill=fill)
    x0, y0, x1, y1 = box
    y = y0 + 8
    line_h = max(14, int(getattr(fnt, "size", 12) * 1.22))
    for line in text_lines(draw, text, fnt, x1 - x0 - 16):
        if y + line_h > y1 - 2:
            break
        draw.text((x0 + 8, y), line, font=fnt, fill=text_fill)
        y += line_h


def fit_image(path: Path, width: int, height: int) -> Image.Image:
    img = Image.open(path).convert("RGB")
    scale = min(width / img.width, height / img.height)
    scaled = img.resize(
        (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
        Image.Resampling.LANCZOS,
    )
    out = Image.new("RGB", (width, height), (8, 9, 12))
    out.paste(scaled, ((width - scaled.width) // 2, (height - scaled.height) // 2))
    return out


def difference_image(sunday_frame: Path, current_frame: Path) -> tuple[Image.Image, dict[str, Any]]:
    sunday = Image.open(sunday_frame).convert("RGB")
    current = Image.open(current_frame).convert("RGB")
    size_mismatch = sunday.size != current.size
    if size_mismatch:
        raise ValueError(
            "exact pixel comparison requires equal dimensions: "
            f"baseline_a={sunday.size} current={current.size}"
        )
    diff = ImageChops.difference(sunday, current)
    hist = diff.histogram()
    total_px = sunday.width * sunday.height
    total_abs = 0
    changed_channels = 0
    for channel in range(3):
        offset = channel * 256
        for value in range(256):
            count = hist[offset + value]
            total_abs += value * count
            if value > 12:
                changed_channels += count
    mean_abs = total_abs / (total_px * 3) if total_px > 0 else 0.0
    pct_changed = 100.0 * changed_channels / (total_px * 3) if total_px > 0 else 0.0
    amplified = diff.point(lambda value: min(255, value * 4))
    metrics = {
        "frame": parse_frame_id(sunday_frame),
        "size_mismatch": size_mismatch,
        "source_size": list(sunday.size),
        "mean_abs_diff_0_255": round(mean_abs, 4),
        "pct_channels_changed_gt12": round(pct_changed, 4),
    }
    return amplified, metrics


def make_gif_frame(
    sunday_frame: Path,
    current_frame: Path,
    frame_id: int,
    width: int,
    height: int,
    label_h: int,
    head: str,
    current_jitter: float | None,
    baseline_label: str,
    current_label: str,
) -> Image.Image:
    canvas = Image.new("RGB", (width * 2, height + label_h), (10, 12, 16))
    draw = ImageDraw.Draw(canvas)
    fnt = font(14)
    jitter_text = "n/a" if current_jitter is None else f"{current_jitter:.3f}%"
    left_label = f"{baseline_label} | frame {frame_id:03d} | FL-4359 FL-4513 | HEAD {head}"
    right_label = f"{current_label} | frame {frame_id:03d} | exact-anchor max jitter {jitter_text}"
    draw_box(draw, (0, 0, width, label_h), left_label, fnt, (23, 28, 39))
    draw_box(draw, (width, 0, width * 2, label_h), right_label, fnt, (23, 28, 39))
    canvas.paste(fit_image(sunday_frame, width, height), (0, label_h))
    canvas.paste(fit_image(current_frame, width, height), (width, label_h))
    return canvas


def make_difference_gif_frame(
    sunday_frame: Path,
    current_frame: Path,
    frame_id: int,
    width: int,
    height: int,
    label_h: int,
    head: str,
    current_jitter: float | None,
    baseline_label: str,
    current_label: str,
) -> tuple[Image.Image, dict[str, Any]]:
    diff, metrics = difference_image(sunday_frame, current_frame)
    canvas = Image.new("RGB", (width * 3, height + label_h), (10, 12, 16))
    draw = ImageDraw.Draw(canvas)
    fnt = font(14)
    jitter_text = "n/a" if current_jitter is None else f"{current_jitter:.3f}%"
    labels = [
        f"{baseline_label} | frame {frame_id:03d} | FL-4359 FL-4513 | HEAD {head}",
        f"{current_label} | frame {frame_id:03d} | exact-anchor max jitter {jitter_text}",
        f"Diff heatmap x4 | mean abs {metrics['mean_abs_diff_0_255']:.4f}/255",
    ]
    for col_idx, label in enumerate(labels):
        draw_box(draw, (width * col_idx, 0, width * (col_idx + 1), label_h), label, fnt, (23, 28, 39))
    canvas.paste(fit_image(sunday_frame, width, height), (0, label_h))
    canvas.paste(fit_image(current_frame, width, height), (width, label_h))
    scaled_diff = diff.resize((width, height), Image.Resampling.LANCZOS)
    canvas.paste(scaled_diff, (width * 2, label_h))
    return canvas, metrics


def damage_frame_paths(directory: str) -> list[Path]:
    """List one renderer's material-211 damage captures, oldest frame first.

    `directory` is a package path such as
    `.run/<package>/damage_pair_comparison/baseline`. An empty string means the
    caller supplied no damage evidence for that side, which returns an empty
    list rather than raising — the grid must still build for older packages that
    predate the paired damage comparison.

    Returns at most two paths, because the damage proof is defined as exactly
    two time-separated frames: a still cannot show a flickering flame.
    """
    if not directory:
        return []
    root = repo_path(directory)
    if not root.is_dir():
        return []
    return sorted(root.glob("fl4265_damage_frame_*.png"))[:2]


def damage_row_label(
    side: str,
    frame: Path,
    record: dict[str, Any],
    head: str,
    dirty: str,
) -> str:
    """Compose the caption printed above one damage panel.

    Every number here comes from the capture record written beside the PNG by
    the proof harness, so the caption states the server-owned state that was
    true when the shutter opened rather than anything inferred from the image.

    `record` may be empty when a package carries frames without records; the
    caption then says so instead of inventing values.
    """
    if not record:
        return "\n".join([
            f"{side} | material-211 damage frame | {frame.name}",
            f"HEAD {head} | {dirty}",
            "capture record MISSING — no server-owned state can be shown",
        ])
    ink = record.get("ink_sample", {})
    ink = ink if isinstance(ink, dict) else {}
    return "\n".join([
        f"{side} | material-211 damage frame | {frame.name}",
        (
            f"server tick {record.get('authoritative_tick', '?')} | "
            f"plane HP {record.get('plane_hp', '?')}/"
            f"{record.get('plane_max_hp', '?')} | "
            f"airborne {record.get('plane_airborne', '?')} | "
            f"pilot mounted {record.get('pilot_mounted', '?')} "
            f"alive {record.get('pilot_alive', '?')}"
        ),
        (
            f"host smoke samples {record.get('host_damage_smoke_sample_count', '?')}"
            f" -> retained mat211 faces "
            f"{record.get('retained_mat211_face_count', '?')}"
            f" -> Product bit-26 subcells "
            f"{record.get('product_mat211_contributed_subcell_count', '?')}"
            f" -> blue pixels {ink.get('blue_ramp_pixel_count', '?')}"
        ),
        (
            f"flame phase step {record.get('damage_flame_phase_step', '?')} | "
            f"FPS-eligible {record.get('fps_sample_eligible', '?')} | "
            f"HEAD {head} | {dirty}"
        ),
    ])


def damage_capture_records(receipt_path: Path) -> list[dict[str, Any]]:
    """Read the per-frame damage records from one arm's receipt.

    Returns an empty list when the receipt is absent or malformed, so a missing
    receipt degrades to a caption that says the record is missing rather than
    to a crash or, worse, to a confident-looking caption full of defaults.
    """
    receipt = load_json(receipt_path)
    records = receipt.get("damage_capture_records", [])
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def parse_frame_id(path: Path) -> int:
    stem = path.stem
    raw = stem.rsplit("_", 1)[-1]
    return int(raw)


def frame_paths(root: Path) -> list[Path]:
    paths = sorted(root.glob("fl4359_perspective_frame_*.png"), key=parse_frame_id)
    if not paths:
        raise FileNotFoundError(f"no moving frame PNGs under {root}")
    return paths


def frame_path_map(paths: list[Path]) -> dict[int, Path]:
    """Map exact route-tick filenames and reject duplicate route ownership."""
    mapped: dict[int, Path] = {}
    for path in paths:
        route_tick = parse_frame_id(path)
        if route_tick in mapped:
            raise ValueError(f"duplicate route tick {route_tick}: {mapped[route_tick]} and {path}")
        mapped[route_tick] = path
    return mapped


def _vec3_distance(a: dict[str, Any], b: dict[str, Any]) -> float:
    return (
        (float(a.get("x", 0.0)) - float(b.get("x", 0.0))) ** 2
        + (float(a.get("y", 0.0)) - float(b.get("y", 0.0))) ** 2
        + (float(a.get("z", 0.0)) - float(b.get("z", 0.0))) ** 2
    ) ** 0.5


def _sample_map(
    samples: Any,
) -> tuple[dict[int, dict[str, Any]], list[int], int]:
    if not isinstance(samples, list):
        return {}, [], 1
    mapped: dict[int, dict[str, Any]] = {}
    duplicates: list[int] = []
    invalid = 0
    for sample in samples:
        if not isinstance(sample, dict):
            invalid += 1
            continue
        try:
            route_tick = int(sample["route_tick"])
        except (KeyError, TypeError, ValueError):
            invalid += 1
            continue
        if route_tick in mapped:
            duplicates.append(route_tick)
            continue
        mapped[route_tick] = sample
    return mapped, duplicates, invalid


def pose_parity_metrics(
    sunday_receipt: dict[str, Any],
    current_receipt: dict[str, Any],
    route_ticks: list[int],
) -> dict[str, Any]:
    sunday_samples, sunday_duplicates, sunday_invalid = _sample_map(
        sunday_receipt.get("visual_frame_samples")
    )
    current_samples, current_duplicates, current_invalid = _sample_map(
        current_receipt.get("visual_frame_samples")
    )
    shared_server_source_match = (
        sunday_receipt.get("server_identity_valid") is True
        and current_receipt.get("server_identity_valid") is True
        and all(
            sunday_receipt.get(key) == current_receipt.get(key)
            for key in (
                "server_git_head",
                "server_source_root",
                "server_source_scope",
                "server_source_dir",
                "server_runtime_scope_fingerprint",
            )
        )
    )
    rows: list[dict[str, Any]] = []
    missing: list[int] = []
    max_player_delta = 0.0
    max_camera_delta = 0.0
    max_airplane_delta = 0.0
    for route_tick in route_ticks:
        sunday_sample = sunday_samples.get(route_tick)
        current_sample = current_samples.get(route_tick)
        if sunday_sample is None or current_sample is None:
            missing.append(route_tick)
            continue
        player_delta = _vec3_distance(sunday_sample.get("player_position", {}), current_sample.get("player_position", {}))
        camera_delta = _vec3_distance(sunday_sample.get("camera_position", {}), current_sample.get("camera_position", {}))
        airplane_delta = _vec3_distance(sunday_sample.get("airplane_position", {}), current_sample.get("airplane_position", {}))
        max_player_delta = max(max_player_delta, player_delta)
        max_camera_delta = max(max_camera_delta, camera_delta)
        max_airplane_delta = max(max_airplane_delta, airplane_delta)
        rows.append(
            {
                "route_tick": route_tick,
                "player_delta": round(player_delta, 6),
                "camera_delta": round(camera_delta, 6),
                "airplane_delta": round(airplane_delta, 6),
            }
        )
    ok = (
        not missing
        and not sunday_duplicates
        and not current_duplicates
        and sunday_invalid == 0
        and current_invalid == 0
        and len(rows) == len(route_ticks)
        and shared_server_source_match
        and max_player_delta <= POSE_TOLERANCE_WORLD
        and max_camera_delta <= POSE_TOLERANCE_WORLD
        and max_airplane_delta <= POSE_TOLERANCE_WORLD
    )
    return {
        "ok": ok,
        "tolerance_world": POSE_TOLERANCE_WORLD,
        "missing_frames": missing,
        "baseline_duplicate_ticks": sunday_duplicates,
        "current_duplicate_ticks": current_duplicates,
        "baseline_invalid_samples": sunday_invalid,
        "current_invalid_samples": current_invalid,
        "shared_server_source_match": shared_server_source_match,
        "max_player_delta": round(max_player_delta, 6),
        "max_camera_delta": round(max_camera_delta, 6),
        "max_airplane_delta": round(max_airplane_delta, 6),
        "rows": rows,
    }


def receipt_summary(path: Path) -> dict[str, Any]:
    receipt = load_json(path)
    return {
        "path": rel(path),
        "pass": bool(receipt.get("pass", False)),
        "moving_frames": receipt.get("moving_frames"),
        "visual_frame_count": receipt.get("visual_frame_count"),
        "visual_recorded_fps": receipt.get("visual_recorded_fps"),
        "visual_recorded_frame_time_ms": receipt.get("visual_recorded_frame_time_ms"),
        "visual_media_frame_zero_moving": receipt.get("visual_media_frame_zero_moving"),
        "first_visual_active_media_frame_index": receipt.get("first_visual_active_media_frame_index"),
        "last_visual_active_media_frame_index": receipt.get("last_visual_active_media_frame_index"),
        "visual_capture_stride": receipt.get("visual_capture_stride"),
        "visual_route_target_ticks": receipt.get("visual_route_target_ticks", []),
        "missing_visual_route_ticks": receipt.get("missing_visual_route_ticks", []),
        "visual_route_ticks_exact": receipt.get("visual_route_ticks_exact"),
        "route_identity": receipt.get("route_identity"),
        "server_tick_rate_hz": receipt.get("server_tick_rate_hz"),
        "server_git_head": receipt.get("server_git_head"),
        "server_source_root": receipt.get("server_source_root"),
        "server_source_scope": receipt.get("server_source_scope"),
        "server_source_dir": receipt.get("server_source_dir"),
        "server_runtime_scope_fingerprint": receipt.get("server_runtime_scope_fingerprint"),
        "server_identity_valid": receipt.get("server_identity_valid"),
        "perspective_forced": receipt.get("perspective_forced"),
        "warmup_frames_discarded": receipt.get("warmup_frames_discarded"),
        "capture_commit": receipt.get("git_head") or receipt.get("commit") or "not recorded in receipt",
        "git_head": receipt.get("git_head") or receipt.get("commit") or "",
        "git_dirty_count": receipt.get("git_dirty_count"),
        "git_dirty_paths": receipt.get("git_dirty_paths", []),
        "godot_rc": receipt.get("godot_rc"),
        "analyzer_rc": receipt.get("analyzer_rc"),
        "visual_frame_samples": receipt.get("visual_frame_samples"),
    }


def _receipt_float(receipt: dict[str, Any], key: str) -> float:
    try:
        return float(receipt.get(key, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _receipt_int(receipt: dict[str, Any], key: str) -> int:
    try:
        return int(receipt.get(key, 0))
    except (TypeError, ValueError):
        return 0


def review_media_fps(sunday_receipt: dict[str, Any], current_receipt: dict[str, Any]) -> dict[str, Any]:
    sunday_tick_rate = _receipt_float(sunday_receipt, "server_tick_rate_hz")
    current_tick_rate = _receipt_float(current_receipt, "server_tick_rate_hz")
    sunday_stride = _receipt_int(sunday_receipt, "visual_capture_stride")
    current_stride = _receipt_int(current_receipt, "visual_capture_stride")
    frame_zero_ok = (
        sunday_receipt.get("visual_media_frame_zero_moving") is True
        and current_receipt.get("visual_media_frame_zero_moving") is True
        and _receipt_int(sunday_receipt, "first_visual_active_media_frame_index") == 0
        and _receipt_int(current_receipt, "first_visual_active_media_frame_index") == 0
    )
    same_stride = sunday_stride > 0 and sunday_stride == current_stride
    same_tick_rate = (
        sunday_tick_rate > 0.0
        and current_tick_rate > 0.0
        and abs(sunday_tick_rate - current_tick_rate) <= 1.0e-6
    )
    fps = sunday_tick_rate / sunday_stride if same_stride and same_tick_rate else 0.0
    ok = fps > 0.0 and frame_zero_ok
    frame_duration_ms = int(round(1000.0 / fps)) if fps > 0.0 else 260
    return {
        "ok": ok,
        "source": "shared_server_tick_rate_divided_by_exact_route_tick_stride",
        "fps": round(fps, 3),
        "frame_duration_ms": frame_duration_ms,
        "sunday_server_tick_rate_hz": sunday_tick_rate,
        "current_server_tick_rate_hz": current_tick_rate,
        "sunday_visual_capture_stride": sunday_stride,
        "current_visual_capture_stride": current_stride,
        "frame_zero_motion_visible": frame_zero_ok,
    }


def summary_metrics(path: Path) -> dict[str, Any]:
    data = load_json(path)
    gates = data.get("gates", {})
    original_shimmer = data.get("original_shimmer_attribution", {})
    post_probe_bounce = data.get("post_probe_bounce_attribution", {})
    presentation_distribution_admissible = (
        data.get("terrain_product_homogeneity_source") == "final_product_b55"
        and
        int(data.get("terrain_product_homogeneity_rows", 0)) > 0
        and int(data.get("terrain_product_homogeneity_invalid_rows", 0)) == 0
        and float(data.get("terrain_product_dominant_material_ratio", 1.0)) <= 0.65
        and data.get("terrain_product_homogeneity_baseline_calibrated") is True
        and data.get("presentation_observer_population_accounting_valid") is True
    )
    return {
        "path": rel(path),
        "max_pair_jitter_pct": data.get("max_pair_jitter_pct"),
        "screen_position_residual_world_max": data.get("screen_position_residual_world_max"),
        "screen_position_residual_world_avg": data.get("screen_position_residual_world_avg"),
        "max_snap_px_delta": data.get("max_snap_px_delta"),
        "avg_snap_px_delta": data.get("avg_snap_px_delta"),
        "snap_sign_flip_rate": data.get("snap_sign_flip_rate"),
        "comparable_pair_count": data.get("comparable_pair_count"),
        "pair_count": data.get("pair_count"),
        "motion_compensated_final_cell_instability_pct": data.get("motion_compensated_final_cell_instability_pct"),
        "motion_compensated_final_cell_instability_cells": data.get("motion_compensated_final_cell_instability_cells"),
        "max_original_shimmer_pct": original_shimmer.get("max_original_shimmer_pct"),
        "total_original_shimmer_cells": original_shimmer.get("total_original_shimmer_cells"),
        "original_shimmer_class_counts": original_shimmer.get("class_counts", {}),
        "original_shimmer_surface_counts": original_shimmer.get("surface_counts", {}),
        "max_probe_record_churn_pct": data.get("max_probe_record_churn_pct"),
        "max_post_probe_surface_bounce_pct": data.get("max_post_probe_surface_bounce_pct"),
        "post_probe_bounce_dominant_owner": post_probe_bounce.get("dominant_owner"),
        "post_probe_bounce_owner_counts": post_probe_bounce.get("owner_counts", {}),
        "post_probe_split_totals": data.get("post_probe_split_totals", {}),
        "post_probe_split_surface_totals": data.get("post_probe_split_surface_totals", {}),
        "gameplay_moving_camera_jitter_bounded": gates.get("gameplay_moving_camera_jitter_bounded", {}).get("pass"),
        "terrain_presentation_distribution_admissible": presentation_distribution_admissible,
        "gameplay_content_shimmer_bounded": (
            gates.get("gameplay_content_shimmer_bounded", {}).get("pass") is True
            and presentation_distribution_admissible
        ),
        "gameplay_post_probe_surface_bounce_bounded": gates.get("gameplay_post_probe_surface_bounce_bounded", {}).get("pass"),
        "failed_gates": gates.get("failed_gates", []),
        "blocking_failed_gates": gates.get("blocking_failed_gates", []),
    }


def _frame_array(path: Path, size: tuple[int, int] = (320, 180)) -> np.ndarray:
    img = Image.open(path).convert("RGB").resize(size, Image.Resampling.BILINEAR)
    return np.asarray(img, dtype=np.float32) / 255.0


def _gray(rgb: np.ndarray) -> np.ndarray:
    return rgb[:, :, 0] * 0.299 + rgb[:, :, 1] * 0.587 + rgb[:, :, 2] * 0.114


def _edge(gray: np.ndarray) -> np.ndarray:
    dx = np.abs(np.diff(gray, axis=1, prepend=gray[:, :1]))
    dy = np.abs(np.diff(gray, axis=0, prepend=gray[:1, :]))
    return np.maximum(dx, dy)


def _shift_image(arr: np.ndarray, dx: int, dy: int) -> np.ndarray:
    shifted = np.roll(arr, shift=(dy, dx), axis=(0, 1))
    if dy > 0:
        shifted[:dy, :] = shifted[dy:dy + 1, :]
    elif dy < 0:
        shifted[dy:, :] = shifted[dy - 1:dy, :]
    if dx > 0:
        shifted[:, :dx] = shifted[:, dx:dx + 1]
    elif dx < 0:
        shifted[:, dx:] = shifted[:, dx - 1:dx]
    return shifted


def _best_translation(a: np.ndarray, b: np.ndarray, max_shift: int = 10) -> tuple[int, int, float]:
    ga = _gray(a)
    gb = _gray(b)
    best = (0, 0, float("inf"))
    for dy in range(-max_shift, max_shift + 1):
        for dx in range(-max_shift, max_shift + 1):
            shifted = _shift_image(gb, dx, dy)
            score = float(np.mean(np.abs(ga - shifted)))
            if score < best[2]:
                best = (dx, dy, score)
    return best


def _water_mask(rgb: np.ndarray) -> np.ndarray:
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    return (
        (blue > red + 0.035)
        & (green > red + 0.01)
        & (blue > 0.18)
        & (green > 0.16)
    ).astype(np.float32)


def _island_mask(rgb: np.ndarray) -> np.ndarray:
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    water = _water_mask(rgb) > 0.5
    terrain = ((green > blue + 0.015) | (red > blue + 0.025) | (green > red + 0.02))
    return (terrain & (~water)).astype(np.float32)


def _edge_from_gray(gray: np.ndarray) -> np.ndarray:
    dx = np.abs(np.diff(gray, axis=1, prepend=gray[:, :1]))
    dy = np.abs(np.diff(gray, axis=0, prepend=gray[:1, :]))
    return np.maximum(dx, dy)


def _local_shift_incoherence_from_edges(edge_a: np.ndarray, edge_b: np.ndarray) -> dict[str, Any]:
    tile_w = max(16, edge_a.shape[1] // 8)
    tile_h = max(16, edge_a.shape[0] // 6)
    local_shifts: list[tuple[int, int]] = []
    tile_rows: list[dict[str, Any]] = []
    raw_tile_count = 0
    excluded_low_texture_count = 0
    excluded_small_tile_count = 0
    for y0 in range(0, edge_a.shape[0], tile_h):
        y1 = min(edge_a.shape[0], y0 + tile_h)
        for x0 in range(0, edge_a.shape[1], tile_w):
            x1 = min(edge_a.shape[1], x0 + tile_w)
            raw_tile_count += 1
            if y1 - y0 < 10 or x1 - x0 < 10:
                excluded_small_tile_count += 1
                continue
            tile_a = edge_a[y0:y1, x0:x1]
            texture = float(np.percentile(tile_a, 90))
            if texture < 0.025:
                excluded_low_texture_count += 1
                continue
            best = (0, 0, float("inf"))
            for dy in range(-6, 7):
                for dx in range(-6, 7):
                    shifted_b = _shift_image(edge_b, dx, dy)
                    tile_b = shifted_b[y0:y1, x0:x1]
                    score = float(np.mean(np.abs(tile_a - tile_b)))
                    if score < best[2]:
                        best = (dx, dy, score)
            local_shifts.append((best[0], best[1]))
            tile_rows.append(
                {
                    "x": x0,
                    "y": y0,
                    "dx": best[0],
                    "dy": best[1],
                    "score_pct": best[2] * 100.0,
                    "edge_texture": texture,
                }
            )
    if len(local_shifts) < 4:
        return {
            "measured": False,
            "reason": "not_enough_textured_tiles",
            "raw_tile_count": raw_tile_count,
            "textured_tile_count": len(local_shifts),
            "excluded_tile_count": excluded_low_texture_count + excluded_small_tile_count,
            "excluded_low_texture_count": excluded_low_texture_count,
            "excluded_small_tile_count": excluded_small_tile_count,
            "p95_px": 0.0,
            "max_px": 0.0,
            "tiles": tile_rows,
        }
    shift_arr = np.asarray(local_shifts, dtype=np.float32)
    median = np.median(shift_arr, axis=0)
    distances = np.sqrt(np.sum((shift_arr - median) ** 2, axis=1))
    return {
        "measured": True,
        "raw_tile_count": raw_tile_count,
        "textured_tile_count": len(local_shifts),
        "excluded_tile_count": excluded_low_texture_count + excluded_small_tile_count,
        "excluded_low_texture_count": excluded_low_texture_count,
        "excluded_small_tile_count": excluded_small_tile_count,
        "median_local_shift": [float(median[0]), float(median[1])],
        "p95_px": float(np.percentile(distances, 95)),
        "max_px": float(np.max(distances)),
        "tiles": tile_rows,
    }


def _local_motion_incoherence_px(a: np.ndarray, aligned_b: np.ndarray) -> dict[str, Any]:
    edge_a = _edge(_gray(a))
    edge_b = _edge(_gray(aligned_b))
    return _local_shift_incoherence_from_edges(edge_a, edge_b)


def _mask_block_delta_pct(mask_a: np.ndarray, mask_b: np.ndarray) -> float:
    img_a = Image.fromarray(np.uint8(np.clip(mask_a * 255.0, 0, 255)))
    img_b = Image.fromarray(np.uint8(np.clip(mask_b * 255.0, 0, 255)))
    small_a = np.asarray(img_a.resize((32, 18), Image.Resampling.BOX), dtype=np.float32) / 255.0
    small_b = np.asarray(img_b.resize((32, 18), Image.Resampling.BOX), dtype=np.float32) / 255.0
    return float(np.percentile(np.abs(small_a - small_b), 95) * 100.0)


def _structural_region_metrics(a: np.ndarray, aligned_b: np.ndarray) -> dict[str, Any]:
    water_a = _water_mask(a)
    water_b = _water_mask(aligned_b)
    island_a = _island_mask(a)
    island_b = _island_mask(aligned_b)
    water_edge = _local_shift_incoherence_from_edges(_edge_from_gray(water_a), _edge_from_gray(water_b))
    island_edge = _local_shift_incoherence_from_edges(_edge_from_gray(island_a), _edge_from_gray(island_b))
    water_block = _mask_block_delta_pct(water_a, water_b)
    island_block = _mask_block_delta_pct(island_a, island_b)
    shoreline_a = _edge_from_gray(water_a)
    shoreline_b = _edge_from_gray(water_b)
    shoreline_delta = float(np.mean(np.abs(shoreline_a - shoreline_b) > 0.2) * 100.0)
    raw_region_pixels = int(water_a.size)
    water_pixels = int(np.sum(water_a > 0.5))
    island_pixels = int(np.sum(island_a > 0.5))
    excluded_pixels = raw_region_pixels - water_pixels - island_pixels
    return {
        "raw_region_pixels": raw_region_pixels,
        "water_pixels": water_pixels,
        "island_pixels": island_pixels,
        "excluded_region_pixels": max(0, excluded_pixels),
        "water_block_p95_delta_pct": water_block,
        "island_block_p95_delta_pct": island_block,
        "max_region_block_p95_delta_pct": max(water_block, island_block),
        "shoreline_delta_pct": shoreline_delta,
        "water_edge_shift_p95_px": water_edge.get("p95_px", 0.0),
        "island_edge_shift_p95_px": island_edge.get("p95_px", 0.0),
        "max_edge_shift_p95_px": max(float(water_edge.get("p95_px", 0.0)), float(island_edge.get("p95_px", 0.0))),
        "water_edge": water_edge,
        "island_edge": island_edge,
    }


def _receipt_visual_fps(receipt: dict[str, Any] | None) -> float:
    if not isinstance(receipt, dict):
        return 0.0
    try:
        tick_rate = float(receipt.get("server_tick_rate_hz", 0.0))
        stride = int(receipt.get("visual_capture_stride", 0))
        return tick_rate / float(stride) if tick_rate > 0.0 and stride > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def visual_temporal_metrics(frames: list[Path], receipt: dict[str, Any] | None = None) -> dict[str, Any]:
    if len(frames) < 2:
        return {
            "measured": False,
            "reason": "needs_at_least_two_frames",
            "failed_gates": ["visual_metrics_missing"],
        }
    arrays = [_frame_array(path) for path in frames]
    shimmer_values: list[float] = []
    changed_values: list[float] = []
    jitter_values: list[float] = []
    bounce_values: list[float] = []
    structural_values: list[float] = []
    region_block_values: list[float] = []
    shoreline_values: list[float] = []
    structural_rows: list[dict[str, Any]] = []
    region_rows: list[dict[str, Any]] = []
    shifts: list[dict[str, Any]] = []
    raw_pair_count = max(0, len(arrays) - 1)
    measured_pair_count = 0
    excluded_pair_count = 0
    raw_tile_count = 0
    measured_tile_count = 0
    excluded_tile_count = 0
    raw_region_pixels = 0
    excluded_region_pixels = 0
    for idx in range(len(arrays) - 1):
        a = arrays[idx]
        b = arrays[idx + 1]
        dx, dy, score = _best_translation(a, b)
        aligned_b = _shift_image(b, dx, dy)
        diff = np.abs(a - aligned_b)
        gray_diff = np.mean(diff, axis=2)
        shimmer_values.append(float(np.mean(gray_diff) * 100.0))
        changed_values.append(float(np.mean(gray_diff > 0.07) * 100.0))
        edge_diff = np.abs(_edge(_gray(a)) - _edge(_gray(aligned_b)))
        jitter_values.append(float(np.mean(edge_diff > 0.08) * 100.0))
        small_a = np.asarray(Image.fromarray(np.uint8(np.clip(a * 255.0, 0, 255))).resize((32, 18), Image.Resampling.BOX), dtype=np.float32) / 255.0
        small_b = np.asarray(Image.fromarray(np.uint8(np.clip(aligned_b * 255.0, 0, 255))).resize((32, 18), Image.Resampling.BOX), dtype=np.float32) / 255.0
        block_delta = np.mean(np.abs(small_a - small_b), axis=2)
        bounce_values.append(float(np.percentile(block_delta, 95) * 100.0))
        structural = _local_motion_incoherence_px(a, aligned_b)
        structural_values.append(float(structural.get("p95_px", 0.0)))
        structural_rows.append({"pair": idx, **structural})
        region = _structural_region_metrics(a, aligned_b)
        region_block_values.append(float(region.get("max_region_block_p95_delta_pct", 0.0)))
        shoreline_values.append(float(region.get("shoreline_delta_pct", 0.0)))
        region_rows.append({"pair": idx, **region})
        measured_pair_count += 1
        raw_tile_count += int(structural.get("raw_tile_count", 0))
        measured_tile_count += int(structural.get("textured_tile_count", 0))
        excluded_tile_count += int(structural.get("excluded_tile_count", 0))
        raw_region_pixels += int(region.get("raw_region_pixels", 0))
        excluded_region_pixels += int(region.get("excluded_region_pixels", 0))
        if structural.get("measured") is not True:
            excluded_pair_count += 1
        shifts.append({"pair": idx, "dx": dx, "dy": dy, "align_score_pct": score * 100.0})
    jitter_pct = max(jitter_values, default=0.0)
    shimmer_pct = max(changed_values, default=0.0)
    bounce_pct = max(bounce_values, default=0.0)
    structural_px = max(structural_values, default=0.0)
    region_block_pct = max(region_block_values, default=0.0)
    shoreline_delta_pct = max(shoreline_values, default=0.0)
    settling_pulse_pct = max(region_block_values, default=0.0) - min(region_block_values, default=0.0)
    structural_fps = _receipt_visual_fps(receipt)
    structural_seconds = (len(frames) / structural_fps) if structural_fps > 0.0 else 0.0
    structural_window_ok = (
        structural_fps >= MIN_STRUCTURAL_REVIEW_FPS
        and structural_seconds >= MIN_STRUCTURAL_REVIEW_SECONDS
    )
    failed_gates: list[str] = []
    if jitter_pct > 3.0:
        failed_gates.append("gameplay_moving_camera_jitter_bounded")
    if shimmer_pct > 8.0:
        failed_gates.append("gameplay_content_shimmer_bounded")
    if bounce_pct > 18.0:
        failed_gates.append("gameplay_post_probe_surface_bounce_bounded")
    if (
        structural_px > STRUCTURAL_LOCAL_SHIFT_P95_LIMIT_PX
        or region_block_pct > STRUCTURAL_REGION_BLOCK_P95_LIMIT_PCT
        or settling_pulse_pct > STRUCTURAL_SETTLING_PULSE_LIMIT_PCT
        or shoreline_delta_pct > STRUCTURAL_SHORELINE_DELTA_LIMIT_PCT
    ):
        failed_gates.append("gameplay_structural_projection_incoherence_bounded")
    if receipt is not None and not structural_window_ok:
        failed_gates.append("evidence_structural_screen_motion_window_present")
    return {
        "measured": True,
        "source": "visual_frames_no_runtime_cell_readback",
        "frame_count": len(frames),
        "raw_pair_count": raw_pair_count,
        "measured_pair_count": measured_pair_count,
        "excluded_pair_count": excluded_pair_count,
        "raw_tile_count": raw_tile_count,
        "measured_tile_count": measured_tile_count,
        "excluded_tile_count": excluded_tile_count,
        "raw_region_pixels": raw_region_pixels,
        "excluded_region_pixels": excluded_region_pixels,
        "structural_review_fps": structural_fps,
        "structural_review_seconds": structural_seconds,
        "structural_review_window_ok": structural_window_ok,
        "frame_paths": [rel(path) for path in frames],
        "max_visual_jitter_edge_churn_pct": jitter_pct,
        "max_visual_shimmer_changed_pixel_pct": shimmer_pct,
        "max_visual_shimmer_mean_delta_pct": max(shimmer_values, default=0.0),
        "max_visual_bounce_block_p95_delta_pct": bounce_pct,
        "max_structural_projection_local_shift_incoherence_px": structural_px,
        "max_structural_region_block_p95_delta_pct": region_block_pct,
        "max_structural_shoreline_delta_pct": shoreline_delta_pct,
        "max_structural_settling_pulse_pct": settling_pulse_pct,
        "translation_alignment": shifts,
        "structural_projection_local_motion": structural_rows,
        "structural_projection_region_motion": region_rows,
        "failed_gates": failed_gates,
        "gates": {
            "gameplay_moving_camera_jitter_bounded": {"pass": "gameplay_moving_camera_jitter_bounded" not in failed_gates},
            "gameplay_content_shimmer_bounded": {"pass": "gameplay_content_shimmer_bounded" not in failed_gates},
            "gameplay_post_probe_surface_bounce_bounded": {"pass": "gameplay_post_probe_surface_bounce_bounded" not in failed_gates},
            "gameplay_structural_projection_incoherence_bounded": {"pass": "gameplay_structural_projection_incoherence_bounded" not in failed_gates},
            "evidence_structural_screen_motion_window_present": {"pass": "evidence_structural_screen_motion_window_present" not in failed_gates},
            "all_pass": not failed_gates,
            "failed_gates": failed_gates,
        },
    }


def apply_presentation_evidence_admission(
    visual: dict[str, Any], metrics: dict[str, Any]
) -> dict[str, Any]:
    """Keep visual equality diagnostic until Product diversity has observer power."""
    gates = visual.get("gates", {})
    shimmer_gate = gates.get("gameplay_content_shimmer_bounded", {})
    raw_pass = shimmer_gate.get("pass") is True
    admissible = metrics.get("terrain_presentation_distribution_admissible") is True
    shimmer_gate["raw_diagnostic_pass"] = raw_pass
    shimmer_gate["presentation_distribution_admissible"] = admissible
    shimmer_gate["pass"] = raw_pass and admissible
    shimmer_gate["diagnostic_only_reason"] = (
        None
        if admissible
        else "terrain_presentation_degenerate_uncalibrated_or_population_unaccounted"
    )
    if not shimmer_gate["pass"]:
        failed = visual.setdefault("failed_gates", [])
        if "gameplay_content_shimmer_bounded" not in failed:
            failed.append("gameplay_content_shimmer_bounded")
    gates["gameplay_content_shimmer_bounded"] = shimmer_gate
    gates["failed_gates"] = visual.get("failed_gates", [])
    gates["all_pass"] = not visual.get("failed_gates", [])
    visual["gates"] = gates
    return visual


def acceptance_metric_summary(side: str, metrics: dict[str, Any]) -> dict[str, Any]:
    visual = metrics.get("visual_frame_metrics", {})
    return {
        "side": side,
        "jitter": {
            "definition": "positional instability after camera motion compensation",
            "visual_metric": visual.get("max_visual_jitter_edge_churn_pct"),
            "max_pair_jitter_pct_legacy": metrics.get("max_pair_jitter_pct"),
            "screen_position_residual_world_max": metrics.get("screen_position_residual_world_max"),
            "screen_position_residual_world_avg": metrics.get("screen_position_residual_world_avg"),
            "gate_pass": visual.get("gates", {}).get("gameplay_moving_camera_jitter_bounded", {}).get("pass", metrics.get("gameplay_moving_camera_jitter_bounded")),
        },
        "shimmer": {
            "definition": "visible cell signature instability for the same motion-compensated world anchor",
            "visual_metric": visual.get("max_visual_shimmer_changed_pixel_pct"),
            "visual_mean_delta_pct": visual.get("max_visual_shimmer_mean_delta_pct"),
            "motion_compensated_final_cell_instability_pct": metrics.get("motion_compensated_final_cell_instability_pct"),
            "motion_compensated_final_cell_instability_cells": metrics.get("motion_compensated_final_cell_instability_cells"),
            "max_original_shimmer_pct": metrics.get("max_original_shimmer_pct"),
            "total_original_shimmer_cells": metrics.get("total_original_shimmer_cells"),
            "class_counts": metrics.get("original_shimmer_class_counts", {}),
            "surface_counts": metrics.get("original_shimmer_surface_counts", {}),
            "gate_pass": visual.get("gates", {}).get("gameplay_content_shimmer_bounded", {}).get("pass", metrics.get("gameplay_content_shimmer_bounded")),
        },
        "bounce": {
            "definition": "region-scale post-probe instability from selected record, texel, phase, and final-paint churn",
            "visual_metric": visual.get("max_visual_bounce_block_p95_delta_pct"),
            "max_probe_record_churn_pct": metrics.get("max_probe_record_churn_pct"),
            "max_post_probe_surface_bounce_pct": metrics.get("max_post_probe_surface_bounce_pct"),
            "dominant_owner": metrics.get("post_probe_bounce_dominant_owner"),
            "owner_counts": metrics.get("post_probe_bounce_owner_counts", {}),
            "split_totals": metrics.get("post_probe_split_totals", {}),
            "surface_totals": metrics.get("post_probe_split_surface_totals", {}),
            "gate_pass": visual.get("gates", {}).get("gameplay_post_probe_surface_bounce_bounded", {}).get("pass", metrics.get("gameplay_post_probe_surface_bounce_bounded")),
        },
        "structural_projection": {
            "definition": "large-scale perspective projection incoherence after global camera-motion compensation",
            "visual_metric": visual.get("max_structural_projection_local_shift_incoherence_px"),
            "region_block_p95_delta_pct": visual.get("max_structural_region_block_p95_delta_pct"),
            "shoreline_delta_pct": visual.get("max_structural_shoreline_delta_pct"),
            "settling_pulse_pct": visual.get("max_structural_settling_pulse_pct"),
            "raw_pair_count": visual.get("raw_pair_count"),
            "measured_pair_count": visual.get("measured_pair_count"),
            "excluded_pair_count": visual.get("excluded_pair_count"),
            "raw_tile_count": visual.get("raw_tile_count"),
            "measured_tile_count": visual.get("measured_tile_count"),
            "excluded_tile_count": visual.get("excluded_tile_count"),
            "raw_region_pixels": visual.get("raw_region_pixels"),
            "excluded_region_pixels": visual.get("excluded_region_pixels"),
            "review_seconds": visual.get("structural_review_seconds"),
            "review_window_ok": visual.get("structural_review_window_ok"),
            "gate_pass": visual.get("gates", {}).get("gameplay_structural_projection_incoherence_bounded", {}).get("pass"),
        },
        "visual_frame_metrics": visual,
        "blocking_failed_gates": metrics.get("blocking_failed_gates", []),
        "failed_gates": metrics.get("failed_gates", []),
    }


def runtime_failed_gates(metrics: dict[str, Any]) -> list[str]:
    visual = metrics.get("visual_frame_metrics", {})
    failed = visual.get("failed_gates", []) if isinstance(visual, dict) else []
    if not failed:
        failed = metrics.get("failed_gates", [])
    if not isinstance(failed, list):
        return []
    return [str(name) for name in failed if name]


def run_video_encode(frame_dir: Path, pattern: str, out_path: Path, fps: float = 4.0) -> dict[str, Any]:
    ffmpeg = shutil.which("ffmpeg")
    fps_arg = f"{fps:.3f}".rstrip("0").rstrip(".")
    result: dict[str, Any] = {
        "path": rel(out_path),
        "codec": "h264",
        "fps": fps,
        "ffmpeg": ffmpeg or "",
        "returncode": None,
        "ok": False,
    }
    if not ffmpeg:
        result["error"] = "ffmpeg_missing"
        return result
    cmd = [
        ffmpeg,
        "-y",
        "-framerate",
        fps_arg,
        "-i",
        str(frame_dir / pattern),
        "-vf",
        "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    result["cmd"] = cmd
    result["returncode"] = proc.returncode
    result["stdout_tail"] = proc.stdout[-2000:]
    result["ok"] = proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0
    if result["ok"]:
        result["sha256"] = sha256(out_path)
        result["bytes"] = out_path.stat().st_size
    return result


def prune_video_frame_dir(frame_dir: Path, encode_result: dict[str, Any]) -> dict[str, Any]:
    result = {
        "path": rel(frame_dir),
        "pruned": False,
        "reason": "",
    }
    if encode_result.get("ok") is not True:
        result["reason"] = "encode_failed_frames_retained_for_diagnosis"
        return result
    if not frame_dir.exists():
        result["pruned"] = True
        result["reason"] = "already_absent"
        return result
    shutil.rmtree(frame_dir)
    result["pruned"] = True
    result["reason"] = "encode_succeeded"
    return result


def row_label(
    side: str,
    frame: Path,
    receipt: dict[str, Any],
    metrics: dict[str, Any],
    head: str,
    dirty: str,
    attempt: str,
    code_refs: str,
) -> str:
    max_jitter = metrics.get("max_pair_jitter_pct")
    jitter_text = "n/a" if max_jitter is None else f"{float(max_jitter):.3f}%"
    return "\n".join(
        [
            f"{side} frame {parse_frame_id(frame):03d} | FL-4359 FL-4513 | review package HEAD {head}",
            f"capture commit: {receipt['capture_commit']} | dirty state: {dirty}",
            f"attempt: {attempt}",
            f"why: probe texels are candidate coverage/depth only; CellOwnerProduct owns role/glyph/color",
            "visual: screen-motion video is the primary evidence",
            f"diagnostics: exact-anchor legacy jitter {jitter_text}; snap px delta {metrics.get('max_snap_px_delta')}",
            f"code refs: {code_refs}",
        ]
    )


def diff_row_label(
    frame_id: int,
    head: str,
    dirty: str,
    attempt: str,
    metrics: dict[str, Any],
) -> str:
    return "\n".join(
        [
            f"Diff frame {frame_id:03d} | FL-4359 FL-4513 | review package HEAD {head}",
            f"dirty state: {dirty}",
            f"attempt: {attempt}",
            "meaning: absolute RGB difference, amplified x4 for by-eye mismatch review",
            f"metrics: mean abs {metrics['mean_abs_diff_0_255']:.4f}/255; changed channels >12: {metrics['pct_channels_changed_gt12']:.4f}%",
            f"size mismatch: {metrics['size_mismatch']}",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", default=".run/fl4359_moving_visual_long_c587/review")
    parser.add_argument("--sunday-dir", required=True)
    parser.add_argument("--current-dir", required=True)
    parser.add_argument("--sunday-receipt", required=True)
    parser.add_argument("--current-receipt", required=True)
    parser.add_argument("--sunday-summary", required=True)
    parser.add_argument("--current-summary", required=True)
    parser.add_argument("--baseline-label", default="Receipt-verified Baseline A")
    parser.add_argument(
        "--baseline-description",
        default="Baseline A faa36357e — operator-designated pre-cutover authority",
    )
    parser.add_argument("--current-label", default="Current")
    # FL-4265 / FL-4431 paired damage evidence. These two directories hold the
    # material-211 blue-damage-flame frames captured by the Baseline A renderer
    # and by the current renderer on the SAME damage flight. Supplying both puts
    # a labelled damage section at the bottom of the review grid, so the human
    # reviewer can answer the two paired checklist questions by looking at the
    # grid instead of hunting through the package directory. Supplying neither
    # keeps the older damage-free grid working unchanged.
    parser.add_argument(
        "--damage-baseline-dir",
        default="",
        help=(
            "Directory of Baseline A material-211 damage frames "
            "(fl4265_damage_frame_*.png)."
        ),
    )
    parser.add_argument(
        "--damage-current-dir",
        default="",
        help=(
            "Directory of current-renderer material-211 damage frames "
            "(fl4265_damage_frame_*.png)."
        ),
    )
    parser.add_argument("--attempt", default="exact-anchor all-window diagnostics plus moving visual review")
    parser.add_argument(
        "--code-refs",
        default="godot_project/render/product/cell_owner_product.glsl:6171, scripts/analyze_fl4359_moving_camera.py:816",
    )
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument(
        "--metric-limit",
        type=int,
        default=0,
        help="Number of moving frames used for visual gates; 0 means all captured frames.",
    )
    args = parser.parse_args()

    out_dir = repo_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sunday_dir = repo_path(args.sunday_dir)
    current_dir = repo_path(args.current_dir)
    sunday_receipt = receipt_summary(repo_path(args.sunday_receipt))
    current_receipt = receipt_summary(repo_path(args.current_receipt))
    try:
        sunday_targets = [
            int(value) for value in sunday_receipt.get("visual_route_target_ticks", [])
        ]
        current_targets = [
            int(value) for value in current_receipt.get("visual_route_target_ticks", [])
        ]
    except (TypeError, ValueError):
        raise SystemExit("invalid visual route target tick list")
    if (
        not sunday_targets
        or sunday_targets != current_targets
        or len(sunday_targets) != len(set(sunday_targets))
    ):
        raise SystemExit(
            "Baseline A and current must declare one identical unique route-tick set"
        )
    sunday_all_frames = frame_paths(sunday_dir)
    current_all_frames = frame_paths(current_dir)
    try:
        sunday_frame_map = frame_path_map(sunday_all_frames)
        current_frame_map = frame_path_map(current_all_frames)
    except ValueError as exc:
        raise SystemExit(str(exc))
    if set(sunday_frame_map) != set(sunday_targets):
        raise SystemExit("Baseline A frame files do not equal the declared route-tick set")
    if set(current_frame_map) != set(current_targets):
        raise SystemExit("current frame files do not equal the declared route-tick set")
    review_ticks = sunday_targets[: args.limit]
    sunday_frames = [sunday_frame_map[tick] for tick in review_ticks]
    current_frames = [current_frame_map[tick] for tick in review_ticks]
    metric_limit = int(args.metric_limit)
    metric_ticks = sunday_targets[:metric_limit] if metric_limit > 0 else sunday_targets
    sunday_metric_frames = [sunday_frame_map[tick] for tick in metric_ticks]
    current_metric_frames = [current_frame_map[tick] for tick in metric_ticks]

    media_fps = review_media_fps(sunday_receipt, current_receipt)
    # FL-4359 Phase B: unequal runtime capture rates are contradictory evidence,
    # not permission to suppress the mandatory visual grid. Encode both arms at
    # the slower measured cadence and carry the failed comparability gate into
    # the failure-confirming manifest.
    media_rate_red = not bool(media_fps["ok"])
    animated_frame_duration_ms = int(media_fps["frame_duration_ms"])
    encoded_fps = float(media_fps["fps"])
    pose_parity = pose_parity_metrics(
        sunday_receipt,
        current_receipt,
        sunday_targets,
    )
    sunday_metrics = summary_metrics(repo_path(args.sunday_summary))
    current_metrics = summary_metrics(repo_path(args.current_summary))
    sunday_metrics["visual_frame_metrics"] = apply_presentation_evidence_admission(
        visual_temporal_metrics(sunday_metric_frames, sunday_receipt),
        sunday_metrics,
    )
    current_metrics["visual_frame_metrics"] = apply_presentation_evidence_admission(
        visual_temporal_metrics(current_metric_frames, current_receipt),
        current_metrics,
    )
    current_failed_gates = runtime_failed_gates(current_metrics)
    if media_rate_red:
        current_failed_gates.append("evidence_runtime_media_fps_comparable")
    pose_visual_red = not bool(pose_parity.get("ok"))
    failure_confirming = bool(current_failed_gates)
    if failure_confirming:
        proof_state = "failure-confirming review package; current runtime gates failed; no acceptance claimed"
        review_verdict = "failure_confirming_current_runtime_gates_failed"
    elif pose_visual_red:
        proof_state = "visual-red evidence package; same-pose review gate failed; no acceptance claimed"
        review_verdict = "visual_red_same_pose_review_gate_failed"
    else:
        proof_state = "visual review package only; operator signoff required"
        review_verdict = "visual_review_pending_operator_signoff"
    head = git_text("rev-parse", "--short", "HEAD")
    dirty_lines = [line for line in git_text("status", "--porcelain").splitlines() if line.strip()]
    dirty = "clean" if not dirty_lines else f"dirty {len(dirty_lines)} paths"
    timestamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    panel_w = 640
    panel_h = 400
    label_h = 148
    gutter = 14
    margin = 18
    header_h = 210
    cols = 2
    rows = min(args.limit, len(sunday_frames))

    # ---------------------------------------------------------------------
    # FL-4265 / FL-4431 paired damage frames
    # ---------------------------------------------------------------------
    # Collect the material-211 blue-flame captures from each renderer. Only the
    # frames present on BOTH sides are drawn, because a damage row exists to let
    # a reviewer compare one renderer against the other; a lone frame with an
    # empty slot beside it would invite exactly the current-only reading this
    # pairing was built to prevent.
    damage_baseline_frames = damage_frame_paths(args.damage_baseline_dir)
    damage_current_frames = damage_frame_paths(args.damage_current_dir)
    damage_rows = min(len(damage_baseline_frames), len(damage_current_frames))
    # A short banner separates the damage section from the moving-route rows
    # above it, so nobody mistakes a damage frame for a route frame.
    damage_header_h = 64 if damage_rows else 0

    width = margin * 2 + cols * panel_w + (cols - 1) * gutter
    height = header_h + margin + rows * (label_h + panel_h) + gutter + margin
    # Grow the canvas for the damage section rather than overlaying it, so the
    # existing moving-route layout keeps its exact geometry.
    height += damage_header_h + damage_rows * (label_h + panel_h + gutter)
    canvas = Image.new("RGB", (width, height), (11, 12, 16))
    draw = ImageDraw.Draw(canvas)
    label_font = font(14)
    header_font = font(20)

    header = "\n".join(
        [
            f"FL-4359 / FL-4513 Baseline-A/current moving perspective review grid | {timestamp}",
            f"Baseline: {args.baseline_description}. Review package HEAD {head}. Dirty state: {dirty}.",
            f"Review vocabulary: {proof_state}.",
            "Primary evidence: real runtime screen-motion video. Metrics are optional diagnostic attachment.",
            f"Media cadence: {encoded_fps:.3f} FPS from runtime receipts; frame-zero motion visible: {media_fps['frame_zero_motion_visible']}.",
            "Selected architecture: probe texels may own candidate coverage/depth; CellOwnerProduct and ascii_compositor own semantic product and paint.",
            f"Baseline A diagnostic jitter: {sunday_metrics['max_pair_jitter_pct']}. Current diagnostic jitter: {current_metrics['max_pair_jitter_pct']}.",
            f"Same-pose gate: max camera delta {pose_parity['max_camera_delta']}u, max airplane delta {pose_parity['max_airplane_delta']}u.",
        ]
    )
    draw_box(draw, (0, 0, width, header_h), header, header_font, (30, 34, 45))

    manifest_panels: list[dict[str, Any]] = []
    animation_frames: list[Image.Image] = []
    difference_panels: list[dict[str, Any]] = []
    difference_animation_frames: list[Image.Image] = []
    video_frame_dir = out_dir / "_video_frames"
    diff_video_frame_dir = out_dir / "_diff_video_frames"
    if video_frame_dir.exists():
        shutil.rmtree(video_frame_dir)
    if diff_video_frame_dir.exists():
        shutil.rmtree(diff_video_frame_dir)
    video_frame_dir.mkdir(parents=True, exist_ok=True)
    diff_video_frame_dir.mkdir(parents=True, exist_ok=True)
    current_jitter = current_metrics.get("max_pair_jitter_pct")
    current_jitter_value = float(current_jitter) if current_jitter is not None else None
    for row_idx, (sunday_frame, current_frame) in enumerate(zip(sunday_frames, current_frames)):
        frame_id = parse_frame_id(sunday_frame)
        video_frame = make_gif_frame(
            sunday_frame,
            current_frame,
            frame_id,
            panel_w,
            panel_h,
            74,
            head,
            current_jitter_value,
            args.baseline_label,
            args.current_label,
        )
        animation_frames.append(video_frame)
        video_frame.save(video_frame_dir / f"ab_{row_idx:03d}.png")
        for col_idx, (side, frame, receipt, metrics) in enumerate(
            [
                (args.baseline_label, sunday_frame, sunday_receipt, sunday_metrics),
                (args.current_label, current_frame, current_receipt, current_metrics),
            ]
        ):
            x = margin + col_idx * (panel_w + gutter)
            y = header_h + margin + row_idx * (label_h + panel_h + gutter)
            label = row_label(side, frame, receipt, metrics, head, dirty, args.attempt, args.code_refs)
            draw_box(draw, (x, y, x + panel_w, y + label_h), label, label_font, (23, 28, 39))
            canvas.paste(fit_image(frame, panel_w, panel_h), (x, y + label_h))
            manifest_panels.append(
                {
                    "side": side,
                    "frame": parse_frame_id(frame),
                    "path": rel(frame),
                    "sha256": sha256(frame),
                    "source_size": list(Image.open(frame).size),
                }
            )
        diff_frame, diff_metrics = make_difference_gif_frame(
            sunday_frame,
            current_frame,
            frame_id,
            panel_w,
            panel_h,
            74,
            head,
            current_jitter_value,
            args.baseline_label,
            args.current_label,
        )
        difference_animation_frames.append(diff_frame)
        diff_frame.save(diff_video_frame_dir / f"diff_{row_idx:03d}.png")
        difference_panels.append(
            {
                "frame": frame_id,
                "sunday_path": rel(sunday_frame),
                "current_path": rel(current_frame),
                "metrics": diff_metrics,
            }
        )

    # -----------------------------------------------------------------------
    # FL-4265 / FL-4431 paired damage section
    # -----------------------------------------------------------------------
    # Drawn below the moving-route rows, with its own banner and its own manifest
    # list. Each row is one time-separated damage frame shown twice: once as the
    # Baseline A renderer drew it, once as the current renderer drew it.
    damage_panels: list[dict[str, Any]] = []
    if damage_rows:
        damage_baseline_records = damage_capture_records(
            repo_path(args.damage_baseline_dir) / "receipt.json"
        )
        damage_current_records = damage_capture_records(
            repo_path(args.damage_current_dir) / "receipt.json"
        )
        # Y coordinate just under the last moving-route row.
        damage_top = header_h + margin + rows * (label_h + panel_h + gutter)
        draw_box(
            draw,
            (0, damage_top, width, damage_top + damage_header_h),
            "\n".join([
                "[server-damage route] paired material-211 blue damage flame — "
                "Baseline A renderer (left) vs current renderer (right)",
                "Two time-separated frames per renderer. These frames are "
                "damage evidence only and are never FPS samples.",
            ]),
            header_font,
            (36, 28, 46),
        )
        damage_side_columns = [
            (
                f"{args.baseline_label} [server-damage route]",
                damage_baseline_frames,
                damage_baseline_records,
            ),
            (
                f"{args.current_label} [server-damage route]",
                damage_current_frames,
                damage_current_records,
            ),
        ]
        for damage_row_idx in range(damage_rows):
            row_y = (
                damage_top
                + damage_header_h
                + damage_row_idx * (label_h + panel_h + gutter)
            )
            for col_idx, (side, frames, records) in enumerate(
                damage_side_columns
            ):
                frame = frames[damage_row_idx]
                record = (
                    records[damage_row_idx]
                    if damage_row_idx < len(records)
                    else {}
                )
                x = margin + col_idx * (panel_w + gutter)
                draw_box(
                    draw,
                    (x, row_y, x + panel_w, row_y + label_h),
                    damage_row_label(side, frame, record, head, dirty),
                    label_font,
                    (33, 25, 42),
                )
                canvas.paste(
                    fit_image(frame, panel_w, panel_h), (x, row_y + label_h)
                )
                damage_panels.append({
                    "side": side,
                    "renderer": "baseline" if col_idx == 0 else "current",
                    "damage_frame_index": damage_row_idx,
                    "path": rel(frame),
                    "sha256": sha256(frame),
                    "source_size": list(Image.open(frame).size),
                    "authoritative_tick": record.get("authoritative_tick"),
                    "plane_hp": record.get("plane_hp"),
                    "damage_flame_phase_step": record.get(
                        "damage_flame_phase_step"
                    ),
                    "fps_sample_eligible": False,
                })

    out_png = out_dir / "FL4359_FL4513_SUNDAY_CURRENT_MOVING_REVIEW_GRID.png"
    canvas.save(out_png)
    out_gif = out_dir / "FL4359_FL4513_SUNDAY_CURRENT_MOVING_REVIEW.gif"
    if animation_frames:
        animation_frames[0].save(
            out_gif,
            save_all=True,
            append_images=animation_frames[1:],
            duration=animated_frame_duration_ms,
            loop=0,
            optimize=False,
        )
    diff_cols = 3
    diff_width = margin * 2 + diff_cols * panel_w + (diff_cols - 1) * gutter
    diff_height = header_h + margin + rows * (label_h + panel_h) + gutter + margin
    diff_canvas = Image.new("RGB", (diff_width, diff_height), (11, 12, 16))
    diff_draw = ImageDraw.Draw(diff_canvas)
    diff_header = "\n".join(
        [
            f"FL-4359 / FL-4513 Baseline-A/current visual difference grid | {timestamp}",
            f"Baseline: {args.baseline_description}. Review package HEAD {head}. Dirty state: {dirty}.",
            f"Review vocabulary: {proof_state}.",
            "Selected architecture: probe texels may own candidate coverage/depth; CellOwnerProduct and ascii_compositor own semantic product and paint.",
            "Diff panels are amplified absolute RGB differences; they help find mismatches but do not accept the lane.",
            "Primary evidence: real runtime screen-motion video. Metrics are optional diagnostic attachment.",
            f"Media cadence: {encoded_fps:.3f} FPS from runtime receipts; frame-zero motion visible: {media_fps['frame_zero_motion_visible']}.",
            f"Same-pose gate: max camera delta {pose_parity['max_camera_delta']}u, max airplane delta {pose_parity['max_airplane_delta']}u.",
        ]
    )
    draw_box(diff_draw, (0, 0, diff_width, header_h), diff_header, header_font, (30, 34, 45))
    for row_idx, (sunday_frame, current_frame) in enumerate(zip(sunday_frames, current_frames)):
        frame_id = parse_frame_id(sunday_frame)
        diff_image, diff_metrics = difference_image(sunday_frame, current_frame)
        panels = [
            (args.baseline_label, fit_image(sunday_frame, panel_w, panel_h)),
            (args.current_label, fit_image(current_frame, panel_w, panel_h)),
            ("Diff heatmap x4", diff_image.resize((panel_w, panel_h), Image.Resampling.LANCZOS)),
        ]
        for col_idx, (side, panel) in enumerate(panels):
            x = margin + col_idx * (panel_w + gutter)
            y = header_h + margin + row_idx * (label_h + panel_h + gutter)
            if side == "Diff heatmap x4":
                label = diff_row_label(frame_id, head, dirty, args.attempt, diff_metrics)
            else:
                receipt = sunday_receipt if side == args.baseline_label else current_receipt
                metrics = sunday_metrics if side == args.baseline_label else current_metrics
                frame = sunday_frame if side == args.baseline_label else current_frame
                label = row_label(side, frame, receipt, metrics, head, dirty, args.attempt, args.code_refs)
            draw_box(diff_draw, (x, y, x + panel_w, y + label_h), label, label_font, (23, 28, 39))
            diff_canvas.paste(panel, (x, y + label_h))
    diff_png = out_dir / "FL4359_FL4513_SUNDAY_CURRENT_MOVING_DIFF_GRID.png"
    diff_canvas.save(diff_png)
    diff_gif = out_dir / "FL4359_FL4513_SUNDAY_CURRENT_MOVING_DIFF.gif"
    if difference_animation_frames:
        difference_animation_frames[0].save(
            diff_gif,
            save_all=True,
            append_images=difference_animation_frames[1:],
            duration=animated_frame_duration_ms,
            loop=0,
            optimize=False,
        )
    out_mp4 = out_dir / "FL4359_FL4513_SUNDAY_CURRENT_MOVING_REVIEW.mp4"
    diff_mp4 = out_dir / "FL4359_FL4513_SUNDAY_CURRENT_MOVING_DIFF.mp4"
    video_encode = run_video_encode(video_frame_dir, "ab_%03d.png", out_mp4, fps=encoded_fps)
    diff_video_encode = run_video_encode(diff_video_frame_dir, "diff_%03d.png", diff_mp4, fps=encoded_fps)
    video_frame_prune = prune_video_frame_dir(video_frame_dir, video_encode)
    diff_video_frame_prune = prune_video_frame_dir(diff_video_frame_dir, diff_video_encode)
    signoff_paths = [rel(out_png), rel(out_gif), rel(diff_png), rel(diff_gif)]
    if video_encode.get("ok"):
        signoff_paths.append(rel(out_mp4))
    if diff_video_encode.get("ok"):
        signoff_paths.append(rel(diff_mp4))
    signoff_path = "; ".join(signoff_paths)
    signoff_record_command = [
        "python3",
        "scripts/analyze_failure_log.py",
        "signoff",
        "record",
        "FL-4359",
        "--operator",
        "<operator-name>",
        "--date",
        timestamp[:10],
        "--branch",
        "main",
        "--commit",
        head,
        "--environment",
        "local-headed-godot-fullscreen-default-probe-cutover",
        "--path",
        signoff_path,
        "--verdict",
        "pass",
        "--note",
        "Receipt-verified Baseline A/current moving perspective visual review accepted",
    ]
    operator_signoff: dict[str, Any]
    if failure_confirming:
        operator_signoff = {
            "required": False,
            "gate": "visual_sunday_grid_operator_review",
            "status": "blocked_by_current_runtime_gate_failures",
            "verify_command": ["python3", "scripts/analyze_failure_log.py", "signoff", "verify", "FL-4359"],
            "query_command": ["python3", "scripts/analyze_failure_log.py", "signoff", "query", "FL-4359"],
            "blocked_failed_gates": current_failed_gates,
            "record_pass_command": [],
            "note": "Current runtime gates failed; this package is failure-confirming evidence only.",
        }
    elif pose_visual_red:
        operator_signoff = {
            "required": False,
            "gate": "visual_sunday_grid_operator_review",
            "status": "blocked_by_visual_red_same_pose_review_gate",
            "verify_command": ["python3", "scripts/analyze_failure_log.py", "signoff", "verify", "FL-4359"],
            "query_command": ["python3", "scripts/analyze_failure_log.py", "signoff", "query", "FL-4359"],
            "blocked_failed_gates": ["visual_sunday_current_same_pose_world_delta"],
            "record_pass_command": [],
            "note": "Same-pose review gate failed after media encoding; this package is visual-red evidence only.",
        }
    else:
        operator_signoff = {
            "required": True,
            "gate": "visual_sunday_grid_operator_review",
            "status": "missing_until_analyze_failure_log_signoff_verify_passes",
            "verify_command": ["python3", "scripts/analyze_failure_log.py", "signoff", "verify", "FL-4359"],
            "query_command": ["python3", "scripts/analyze_failure_log.py", "signoff", "query", "FL-4359"],
            "record_pass_command": signoff_record_command,
            "checklist": [
                "[day moving route] current terrain matches receipt-verified Baseline A pixels",
                "[day moving route] current water matches receipt-verified Baseline A pixels",
                "[day moving route] current trees match receipt-verified Baseline A pixels",
                # Receipts prove that retained airplane and vapor facts reached
                # Product. Only the moving frames prove that later paint and
                # presentation kept those contributions visible.
                "[day moving route] airplane body remains visible and stable across exact route ticks",
                "[day moving route] contrail remains attached behind the moving airplane across multiple ticks",
                # Paired, not singular. A current-only flame question would let
                # the reviewer confirm the current renderer alone and have that
                # read as parity; both sides get their own question.
                "[server-damage route] Baseline A material-211 blue damage flame is visibly emitted from the airplane",
                "[server-damage route] current material-211 blue damage flame is visibly emitted from the airplane",
                "[night temporal route] lighthouse beam is present and rotates across time-separated frames",
                "[night temporal route] current always-on bonfire is visibly present",
                "[night temporal route] tower lights are visibly present",
                "no grey static collapse is visible",
                "no sky pixels leak through terrain",
                "motion is stable without visible shimmer",
            ],
        }
    manifest = {
        "artifact": rel(out_png),
        "sha256": sha256(out_png),
        "animated_artifact": rel(out_gif),
        "animated_sha256": sha256(out_gif),
        "video_artifact": rel(out_mp4),
        "video_sha256": sha256(out_mp4) if out_mp4.exists() else "",
        "video_encode": video_encode,
        "video_frame_intermediates": video_frame_prune,
        "difference_artifact": rel(diff_png),
        "difference_sha256": sha256(diff_png),
        "difference_animated_artifact": rel(diff_gif),
        "difference_animated_sha256": sha256(diff_gif),
        "difference_video_artifact": rel(diff_mp4),
        "difference_video_sha256": sha256(diff_mp4) if diff_mp4.exists() else "",
        "difference_video_encode": diff_video_encode,
        "difference_video_frame_intermediates": diff_video_frame_prune,
        "animated_frame_duration_ms": animated_frame_duration_ms,
        "review_media_fps": encoded_fps,
        "review_media_fps_source": media_fps["source"],
        "review_media_fps_gate": media_fps,
        "primary_evidence": "real_runtime_screen_motion_video",
        "metrics_attachment": "optional_diagnostic_attachment",
        "video_container": "mp4_h264",
        "timestamp": timestamp,
        "head": head,
        "dirty_state": dirty,
        "dirty_paths": dirty_lines,
        "fl_refs": ["FL-4359", "FL-4513"],
        "code_refs": args.code_refs.split(", "),
        "attempt": args.attempt,
        "baseline_label": args.baseline_label,
        "baseline_description": args.baseline_description,
        "current_label": args.current_label,
        "proof_state": proof_state,
        "review_verdict": review_verdict,
        "current_runtime_failed_gates": current_failed_gates,
        "operator_signoff": operator_signoff,
        "architecture": "probe texels may own candidate coverage/depth; CellOwnerProduct and ascii_compositor own semantic product and paint",
        "layout": "paired_rows",
        "animated_layout": "paired_side_by_side",
        "difference_layout": "baseline_a_current_diff_rows",
        "panel_size": [panel_w, panel_h],
        "label_height": label_h,
        "sunday_receipt": sunday_receipt,
        "current_receipt": current_receipt,
        "sunday_summary": sunday_metrics,
        "current_summary": current_metrics,
        "metric_definitions": {
            "jitter": "apparent positional instability after camera motion compensation",
            "shimmer": "visible cell signature instability for the same motion-compensated world anchor",
            "bounce": "region-scale post-probe instability from selected record, texel, phase, and final-paint churn",
            "structural_projection": "large-scale perspective projection incoherence: local terrain/water regions need inconsistent motion after global camera-motion compensation",
        },
        "metric_summary": {
            "sunday": acceptance_metric_summary("Receipt-verified Baseline A", sunday_metrics),
            "current": acceptance_metric_summary("Current", current_metrics),
        },
        "same_pose_review_gate": pose_parity,
        "panels": manifest_panels,
        # FL-4265 / FL-4431: the paired damage evidence drawn into this same
        # grid. `damage_pair_included` is a plain boolean so a gate can reject a
        # package whose grid omitted the damage frames entirely, without having
        # to reason about why the list is short.
        "damage_panels": damage_panels,
        "damage_pair_included": bool(damage_panels),
        "damage_pair_renderers": sorted(
            {str(panel["renderer"]) for panel in damage_panels}
        ),
        "damage_frames_are_fps_samples": False,
        "difference_panels": difference_panels,
    }
    manifest_path = out_png.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(out_png)
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
