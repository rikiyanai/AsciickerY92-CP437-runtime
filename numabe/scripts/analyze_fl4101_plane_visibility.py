#!/usr/bin/env python3
"""Offline FL-4101 plane-visibility analysis.

This tool consumes a same-frame ``final_render_cell_dump`` package.  It does
not read, execute, or alter the renderer.  The required b62 extension is four
quarter records per ANSI cell.  Each quarter record must carry these fields:

``godray_lit_bits``
    A 16-bit mask.  Bit ``i`` is the Product observation
    ``cloud_lit && ridge_lit`` for plane ``i``.
``godray_sample_mask``
    A 16-bit mask of planes whose sample reached the cloud/terrain query.
``godray_sample_count``
    The population count of ``godray_sample_mask``.
``godray_accumulated_light``
    A finite non-negative ``{"x": r, "y": g, "z": b}`` object accumulated by
    the 16-plane query.

The current F14 dumps intentionally fail closed because this b62 extension is
not present yet.  The tool owns only offline interpretation and synthetic
counterfactuals.  It reports raw measurements for diagnosis and never emits a
visual-acceptance verdict; only direct target comparison in the headed runtime
can decide whether the rendered result contains acceptable shafts.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "fl4101.godray_plane_visibility.v1"
JOIN_SCHEMA = "fl4231.f13_terrain_edge_capture.v1"
JOIN_STATE = "same_request_join_readback_ok"
ROW_TYPE = "exact_final_cell"
PLANE_COUNT = 16
DEFAULT_GAP_WORLD = 6.0
NORMAL_DEGREES = 128.0
EQUIVALENT_WIDTH = 720
EQUIVALENT_HEIGHT = 405
HEAD_RE = re.compile(r"^[0-9a-fA-F]{40}$")
QUARTER_FIELDS = (
    "godray_lit_bits",
    "godray_sample_mask",
    "godray_sample_count",
    "godray_accumulated_light",
)


class SchemaError(ValueError):
    """Raised when evidence identity or the b62 extension is incomplete."""


def _expected_schema() -> dict[str, Any]:
    return {
        "metadata": {
            "git_head": "40 hexadecimal characters",
            "frame_index": "non-negative integer",
            "ansi_size": {"w": "positive integer", "h": "positive integer"},
            "pixel_camera_capture_contract_valid": True,
            "fl4231_terrain_edge_capture": {
                "schema": JOIN_SCHEMA,
                "state": JOIN_STATE,
                "frame_index": "same as metadata.frame_index",
            },
        },
        "row": {
            "row_type": ROW_TYPE,
            "screen_cell": {"x": "0..ansi_width-1", "y": "0..ansi_height-1"},
            "terrain_edge_b62_quarters": {
                "length": 4,
                "sampling_modes": {
                    "cell_scale_one_probe": "slot 0 retained at (x,y); slots 1..3 contain zero masks/count/light",
                    "quarter_subcells_2x": "all four quarters retained at (2*x+slot%2, 2*y+slot//2)",
                },
                "quarter_fields": {
                    "godray_lit_bits": "uint16, combined cloud_lit&&ridge_lit",
                    "godray_sample_mask": "uint16, effective plane mask",
                    "godray_sample_count": "popcount(godray_sample_mask)",
                    "godray_accumulated_light": "object {x,y,z}, finite non-negative components",
                },
            },
        },
    }


def _fail(reason: str, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "fail_closed",
        "reason": reason,
        "schema_expected": _expected_schema(),
    }
    result.update(extra)
    return result


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise SchemaError(reason)


def _as_int(value: Any, name: str) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), f"{name}_not_integer")
    return int(value)


def _as_finite(value: Any, name: str) -> float:
    _require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{name}_not_number")
    result = float(value)
    _require(math.isfinite(result), f"{name}_not_finite")
    return result


def _popcount(value: int) -> int:
    return value.bit_count()


def _luma(rgb: Iterable[float]) -> float:
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _parse_quarter(quarter: Any, row_index: int, slot: int) -> dict[str, Any]:
    _require(isinstance(quarter, dict), f"row_{row_index}_quarter_{slot}_not_object")
    for field in QUARTER_FIELDS:
        _require(field in quarter, f"row_{row_index}_quarter_{slot}_missing_{field}")
    lit_bits = _as_int(quarter["godray_lit_bits"], "godray_lit_bits")
    sample_mask = _as_int(quarter["godray_sample_mask"], "godray_sample_mask")
    sample_count = _as_int(quarter["godray_sample_count"], "godray_sample_count")
    _require(0 <= lit_bits <= 0xFFFF, f"row_{row_index}_quarter_{slot}_lit_bits_out_of_range")
    _require(0 <= sample_mask <= 0xFFFF, f"row_{row_index}_quarter_{slot}_sample_mask_out_of_range")
    _require(sample_count == _popcount(sample_mask), f"row_{row_index}_quarter_{slot}_sample_count_mismatch")
    _require((lit_bits & ~sample_mask) == 0, f"row_{row_index}_quarter_{slot}_lit_outside_sample")
    accumulated = quarter["godray_accumulated_light"]
    _require(isinstance(accumulated, dict) and all(axis in accumulated for axis in ("x", "y", "z")),
             f"row_{row_index}_quarter_{slot}_accumulated_light_shape")
    light = [_as_finite(accumulated[axis], f"godray_accumulated_light_{axis}")
             for axis in ("x", "y", "z")]
    _require(all(value >= 0.0 for value in light), f"row_{row_index}_quarter_{slot}_accumulated_light_negative")
    return {
        "lit_bits": lit_bits,
        "sample_mask": sample_mask,
        "sample_count": sample_count,
        "accumulated_light": light,
    }


def _is_zero_quarter(quarter: dict[str, Any]) -> bool:
    return (
        quarter["lit_bits"] == 0
        and quarter["sample_mask"] == 0
        and quarter["sample_count"] == 0
        and all(value == 0.0 for value in quarter["accumulated_light"])
    )


def _parse_dump(dump_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    metadata_path = dump_dir / "metadata.json"
    cells_path = dump_dir / "cells.jsonl"
    _require(metadata_path.is_file(), "metadata_missing")
    _require(cells_path.is_file(), "cells_jsonl_missing")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaError(f"metadata_unreadable:{type(exc).__name__}") from exc
    _require(isinstance(metadata, dict), "metadata_not_object")
    git_head = metadata.get("git_head")
    _require(isinstance(git_head, str) and HEAD_RE.fullmatch(git_head) is not None, "git_head_missing_or_invalid")
    frame_index = _as_int(metadata.get("frame_index"), "frame_index")
    _require(frame_index >= 0, "frame_index_negative")
    ansi_size = metadata.get("ansi_size")
    _require(isinstance(ansi_size, dict), "ansi_size_missing")
    width = _as_int(ansi_size.get("w"), "ansi_width")
    height = _as_int(ansi_size.get("h"), "ansi_height")
    _require(width > 0 and height > 0, "ansi_size_non_positive")
    _require(metadata.get("pixel_camera_capture_contract_valid") is True,
             "pixel_camera_capture_contract_invalid")
    join = metadata.get("fl4231_terrain_edge_capture")
    _require(isinstance(join, dict), "b62_join_receipt_missing")
    _require(join.get("schema") == JOIN_SCHEMA, "b62_join_schema_mismatch")
    _require(join.get("state") == JOIN_STATE, "b62_join_state_not_readback_ok")
    _require(join.get("frame_index") == frame_index, "b62_frame_identity_mismatch")

    parsed_rows: list[tuple[int, int, list[dict[str, Any]]]] = []
    seen: set[tuple[int, int]] = set()
    try:
        with cells_path.open(encoding="utf-8") as stream:
            for row_index, line in enumerate(stream):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SchemaError(f"row_{row_index}_invalid_json") from exc
                _require(isinstance(row, dict), f"row_{row_index}_not_object")
                _require(row.get("row_type") == ROW_TYPE, f"row_{row_index}_wrong_row_type")
                screen_cell = row.get("screen_cell")
                _require(isinstance(screen_cell, dict), f"row_{row_index}_screen_cell_missing")
                x = _as_int(screen_cell.get("x"), f"row_{row_index}_screen_x")
                y = _as_int(screen_cell.get("y"), f"row_{row_index}_screen_y")
                _require(0 <= x < width and 0 <= y < height, f"row_{row_index}_screen_cell_out_of_range")
                key = (x, y)
                _require(key not in seen, f"row_{row_index}_duplicate_screen_cell")
                seen.add(key)
                quarters = row.get("terrain_edge_b62_quarters")
                _require(isinstance(quarters, list) and len(quarters) == 4,
                         f"row_{row_index}_b62_quarter_count_mismatch")
                parsed = [_parse_quarter(value, row_index, slot) for slot, value in enumerate(quarters)]
                parsed_rows.append((x, y, parsed))
    except OSError as exc:
        raise SchemaError(f"cells_jsonl_unreadable:{type(exc).__name__}") from exc

    # Infer the lattice from the complete frame.  An all-zero legacy cell may
    # appear before a populated four-quarter cell, so a first-row decision
    # would misclassify the entire capture.
    lattice_mode = ("quarter_subcells_2x"
                    if any(not _is_zero_quarter(quarter)
                           for _, _, parsed in parsed_rows for quarter in parsed[1:])
                    else "cell_scale_one_probe")
    samples: list[dict[str, Any]] = []
    for x, y, parsed in parsed_rows:
        selected = parsed[:1] if lattice_mode == "cell_scale_one_probe" else parsed
        for slot, quarter in enumerate(selected):
            # The one-probe capture uses the upper-left quarter as a
            # cell-scale sample.  Populated four-quarter captures retain the
            # 2x2 b62 lattice; neither mode ORs quarters.
            samples.append({
                "cell_x": x,
                "cell_y": y,
                "slot": slot,
                "x": x if lattice_mode == "cell_scale_one_probe" else 2 * x + (slot & 1),
                "y": y if lattice_mode == "cell_scale_one_probe" else 2 * y + (slot >> 1),
                "effective_mask": quarter["sample_mask"],
                "effective_count": quarter["sample_count"],
                "lit_bits": quarter["lit_bits"],
                "lit_count": _popcount(quarter["lit_bits"]),
                "accumulated_light": quarter["accumulated_light"],
            })
    expected_samples = width * height if lattice_mode == "cell_scale_one_probe" else width * height * 4
    _require(len(samples) == expected_samples, "frame_sample_count_mismatch")
    _require(len(seen) == width * height, "frame_screen_cell_coverage_incomplete")
    sample_width = width if lattice_mode == "cell_scale_one_probe" else width * 2
    sample_height = height if lattice_mode == "cell_scale_one_probe" else height * 2
    metadata["_frame_identity"] = {
        "git_head": git_head,
        "frame_index": frame_index,
        "ansi_size": {"w": width, "h": height},
        "sample_lattice": {
            "mode": lattice_mode,
            "size": {"w": sample_width, "h": sample_height},
        },
        "sample_size": {"w": sample_width, "h": sample_height},
        "subcell_size": {"w": sample_width, "h": sample_height},
        "sample_count": len(samples),
        "quarter_samples": len(samples),
        "b62_sha256": join.get("b62_sha256"),
        "b64_sha256": join.get("b64_sha256"),
    }
    return metadata, samples


def _moving_average(values: list[float], window: int) -> list[float]:
    if not values:
        return []
    half = window // 2
    result: list[float] = []
    for index in range(len(values)):
        total = 0.0
        for offset in range(-half, half + 1):
            source = index + offset
            if 0 <= source < len(values):
                total += values[source]
        result.append(total / float(window))
    return result


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(math.floor(fraction * (len(ordered) - 1)))))
    return ordered[index]


def _otsu_threshold(values: list[float]) -> float:
    """Select a two-level boundary without encoding a target duty cycle."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if ordered[0] == ordered[-1]:
        return ordered[0]
    prefix = [0.0]
    for value in ordered:
        prefix.append(prefix[-1] + value)
    total = prefix[-1]
    best_score = -1.0
    best_threshold = ordered[0]
    count = len(ordered)
    for split in range(1, count):
        left_count = split
        right_count = count - split
        left_mean = prefix[split] / left_count
        right_mean = (total - prefix[split]) / right_count
        score = left_count * right_count * (left_mean - right_mean) ** 2
        if score > best_score:
            best_score = score
            best_threshold = (ordered[split - 1] + ordered[split]) * 0.5
    return best_threshold


def _runs(profile: list[float], support_span: dict[str, Any] | None = None) -> dict[str, Any]:
    if not profile:
        return {"run_count": 0, "all_widths_px": [], "widths_px": [], "duty": 0.0,
                "transition_widths_px": [], "step_luma": 0.0,
                "step_luma_signed": 0.0, "step_luma_abs": 0.0,
                "step_direction": "none", "threshold_method": "otsu", "threshold": 0.0,
                "supported_span": support_span}
    smooth = _moving_average(profile, 5)
    baseline = _moving_average(smooth, 61)
    residual = [value - baseline[index] for index, value in enumerate(smooth)]
    threshold = _otsu_threshold(residual)
    bright = [value > threshold for value in residual]
    intervals: list[tuple[int, int]] = []
    start: int | None = None
    for index, active in enumerate(bright + [False]):
        if active and start is None:
            start = index
        elif not active and start is not None:
            intervals.append((start, index))
            start = None
    all_widths = [end - begin for begin, end in intervals]
    derivative = [abs(profile[index + 1] - profile[index]) for index in range(len(profile) - 1)]
    transition_threshold = _percentile(derivative, 0.95)
    transition_active = [value > transition_threshold for value in derivative]
    transition_widths: list[int] = []
    start = None
    for index, active in enumerate(transition_active + [False]):
        if active and start is None:
            start = index
        elif not active and start is not None:
            transition_widths.append(index - start)
            start = None
    core = bright[30:-30] if len(bright) > 60 else bright
    on_residual = [residual[index] for index, active in enumerate(bright) if active]
    off_residual = [residual[index] for index, active in enumerate(bright) if not active]
    step = (statistics.fmean(on_residual) - statistics.fmean(off_residual)) \
        if on_residual and off_residual else 0.0
    return {
        "run_count": len(all_widths),
        "all_widths_px": all_widths[:64],
        "widths_px": all_widths[:64],
        "duty": statistics.fmean(core) if core else 0.0,
        "transition_widths_px": transition_widths[:64],
        "step_luma": step,
        "step_luma_signed": step,
        "step_luma_abs": abs(step),
        "step_direction": "bright_over_baseline" if step > 0.0 else (
            "dark_over_baseline" if step < 0.0 else "neutral"),
        "threshold_method": "otsu",
        "threshold": threshold,
        "supported_span": support_span,
    }


def _project_values(
    values: dict[tuple[int, int], float],
    eligible: dict[tuple[int, int], bool],
    width: int,
    height: int,
    equivalent_width: int,
    equivalent_height: int,
    normal_degrees: float,
) -> tuple[list[float], float, float, dict[str, Any] | None]:
    xmap = [min(width - 1, (x * width) // equivalent_width) for x in range(equivalent_width)]
    ymap = [min(height - 1, (y * height) // equivalent_height) for y in range(equivalent_height)]
    radians = math.radians(normal_degrees)
    coordinates: list[int] = []
    for y in range(equivalent_height):
        for x in range(equivalent_width):
            coordinates.append(int(math.floor(x * math.cos(radians) + y * math.sin(radians))))
    offset = min(coordinates)
    size = max(coordinates) - offset + 1
    histogram = [0.0] * size
    counts = [0] * size
    eligible_pixels = 0
    index = 0
    for y in range(equivalent_height):
        for x in range(equivalent_width):
            bucket = coordinates[index] - offset
            key = (xmap[x], ymap[y])
            if eligible.get(key, False):
                histogram[bucket] += values.get(key, 0.0)
                counts[bucket] += 1
                eligible_pixels += 1
            index += 1
    supported = [count > 0 for count in counts]
    best_start: int | None = None
    best_end: int | None = None
    current_start: int | None = None
    for index, is_supported in enumerate(supported + [False]):
        if is_supported and current_start is None:
            current_start = index
        elif not is_supported and current_start is not None:
            current_end = index - 1
            if best_start is None or current_end - current_start > best_end - best_start:
                best_start, best_end = current_start, current_end
            current_start = None
    support_span = None
    profile: list[float] = []
    if best_start is not None and best_end is not None:
        support_span = {
            "start": best_start,
            "end": best_end,
            "length": best_end - best_start + 1,
            "total_buckets": size,
            "coordinate_start": offset + best_start,
            "coordinate_end": offset + best_end,
        }
        profile = [histogram[index] / counts[index] for index in range(best_start, best_end + 1)]
    return profile, eligible_pixels / float(equivalent_width * equivalent_height), \
        sum(count > 0 for count in counts) / float(size), support_span


def _profile_bits(rows: list[dict[str, Any]], width: int, height: int, plane: int,
                 equivalent_width: int, equivalent_height: int, normal_degrees: float,
                 gap_world: float) -> dict[str, Any]:
    values = {(row["x"], row["y"]): float((row["lit_bits"] >> plane) & 1) for row in rows}
    eligible = {(row["x"], row["y"]): bool((row["effective_mask"] >> plane) & 1) for row in rows}
    profile, eligible_fraction, support_fraction, support_span = _project_values(
        values, eligible, width, height, equivalent_width, equivalent_height, normal_degrees)
    return {
        "plane": plane,
        "distance_world": plane * gap_world,
        "eligible_fraction": eligible_fraction,
        "support_fraction": support_fraction,
        **_runs(profile, support_span),
    }


def _phi(a: int, b: int, c: int, d: int) -> float:
    denominator = math.sqrt((a + b) * (c + d) * (a + c) * (b + d))
    return ((a * d) - (b * c)) / denominator if denominator else 0.0


def _correlations(rows: list[dict[str, Any]], gap_world: float) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for delta in range(1, PLANE_COUNT):
        groups: list[dict[str, Any]] = []
        for first in range(PLANE_COUNT - delta):
            second = first + delta
            a = b = c = d = 0
            for row in rows:
                mask = row["effective_mask"]
                if ((mask >> first) & 1) == 0 or ((mask >> second) & 1) == 0:
                    continue
                first_lit = (row["lit_bits"] >> first) & 1
                second_lit = (row["lit_bits"] >> second) & 1
                if first_lit and second_lit:
                    a += 1
                elif first_lit:
                    b += 1
                elif second_lit:
                    c += 1
                else:
                    d += 1
            pair_observations = a + b + c + d
            if pair_observations:
                groups.append({
                    "plane_a": first,
                    "plane_b": second,
                    "observations": pair_observations,
                    "phi": _phi(a, b, c, d),
                    "contingency": {"both": a, "a_only": b, "b_only": c, "neither": d},
                })
        if groups:
            correlations = [group["phi"] for group in groups]
            result.append({
                "plane_delta": delta,
                "distance_world": delta * gap_world,
                "pair_groups": len(groups),
                "observations": sum(group["observations"] for group in groups),
                "mean_phi": statistics.fmean(correlations),
                "min_phi": min(correlations),
                "max_phi": max(correlations),
                "groups": groups,
            })
    return result


def _binomial(rows: list[dict[str, Any]]) -> dict[str, Any]:
    full = [row for row in rows if row["effective_mask"] == 0xFFFF]
    if not full:
        return {"comparable": False, "reason": "no_full_16_plane_effective_cells", "cell_count": 0}
    counts = [row["lit_count"] for row in full]
    duty = statistics.fmean(counts) / PLANE_COUNT
    observed = statistics.fmean([(count - statistics.fmean(counts)) ** 2 for count in counts])
    expected = PLANE_COUNT * duty * (1.0 - duty)
    return {
        "comparable": True,
        "cell_count": len(full),
        "duty": duty,
        "observed_variance": observed,
        "binomial_expected_variance": expected,
        "variance_ratio": observed / expected if expected else None,
        "lit_count_histogram": dict(sorted(Counter(counts).items())),
    }


def _geometric_raw_opacity(lit_bits: int, alpha: float) -> float:
    """Return alpha*(1-alpha)^i for the actually lit plane indices i."""
    return sum(alpha * ((1.0 - alpha) ** index)
               for index in range(PLANE_COUNT) if (lit_bits >> index) & 1)


def _synthetic(rows: list[dict[str, Any]], width: int, height: int,
               equivalent_width: int, equivalent_height: int, normal_degrees: float) -> dict[str, Any]:
    per_plane_quantum = []
    for row in rows:
        if row["lit_count"] <= 0:
            continue
        value = _luma(row["accumulated_light"]) / row["lit_count"]
        if math.isfinite(value) and value > 0.0:
            per_plane_quantum.append(value)
    if not per_plane_quantum:
        return {"status": "not_comparable", "reason": "no_positive_accumulated_light_on_lit_cell"}
    quantum = statistics.median(per_plane_quantum)
    eligible = {(row["x"], row["y"]): bool(row["effective_mask"]) for row in rows}
    models: list[dict[str, Any]] = []

    def add_model(name: str, values: dict[tuple[int, int], float], alpha: float | None = None,
                  raw_opacity: dict[tuple[int, int], float] | None = None,
                  normalization: dict[str, Any] | None = None) -> None:
        profile, _, _, support_span = _project_values(
            values, eligible, width, height, equivalent_width, equivalent_height, normal_degrees)
        metrics = _runs(profile, support_span)
        model: dict[str, Any] = {"model": name, "alpha": alpha, **metrics}
        result_luma = list(values.values())
        model["result_luma"] = {
            "mean": statistics.fmean(result_luma) if result_luma else 0.0,
            "max": max(result_luma) if result_luma else 0.0,
        }
        if raw_opacity is not None:
            raw_values = list(raw_opacity.values())
            model["raw_geometric_opacity"] = {
                "mean": statistics.fmean(raw_values) if raw_values else 0.0,
                "max": max(raw_values) if raw_values else 0.0,
                "min": min(raw_values) if raw_values else 0.0,
            }
            model["amplitude_normalization"] = normalization
        models.append(model)

    equal_values = {(row["x"], row["y"]): quantum * row["lit_count"] for row in rows}
    add_model("equal", equal_values)
    for alpha in (0.15, 0.25, 0.35):
        raw_opacity = {}
        for row in rows:
            raw_opacity[(row["x"], row["y"])] = _geometric_raw_opacity(row["lit_bits"], alpha)
        full_raw_opacity = _geometric_raw_opacity(0xFFFF, alpha)
        reference_full_luma = quantum * PLANE_COUNT
        calibration_scale = reference_full_luma / full_raw_opacity if full_raw_opacity else 0.0
        values = {key: raw * calibration_scale for key, raw in raw_opacity.items()}
        add_model(
            "geometric_front_to_back", values, alpha, raw_opacity,
            {
                "formula": "raw_opacity=sum(alpha*(1-alpha)^i for actually lit plane bits i)",
                "reference_full_16_plane_luma": reference_full_luma,
                "full_16_plane_raw_opacity": full_raw_opacity,
                "calibration_scale_luma_per_opacity": calibration_scale,
                "purpose": "match full-16 equal-quantum amplitude while isolating weighting law",
            })
    nearest_mask = (1 << 4) - 1
    nearest_values = {
        (row["x"], row["y"]): quantum * _popcount(row["lit_bits"] & nearest_mask)
        for row in rows
    }
    add_model("nearest4_equal", nearest_values)
    return {
        "status": "ok",
        "quantum_luma_median": quantum,
        "quantum_source": "median(luma(accumulated_light)/lit_count)",
        "geometric_formula": "raw_opacity=sum(alpha*(1-alpha)^i for actually lit plane bits i)",
        "diagnostic_only": True,
        "models": models,
    }


def analyze_dump(dump_dir: Path, *, equivalent_width: int = EQUIVALENT_WIDTH,
                 equivalent_height: int = EQUIVALENT_HEIGHT,
                 normal_degrees: float = NORMAL_DEGREES,
                 gap_world: float = DEFAULT_GAP_WORLD) -> dict[str, Any]:
    try:
        metadata, rows = _parse_dump(dump_dir)
        frame = metadata["_frame_identity"]
        width, height = frame["ansi_size"]["w"], frame["ansi_size"]["h"]
        sample_width, sample_height = frame["sample_size"]["w"], frame["sample_size"]["h"]
        plane_profiles = []
        for plane in range(PLANE_COUNT):
            plane_profiles.append(_profile_bits(rows, sample_width, sample_height, plane,
                                                equivalent_width, equivalent_height, normal_degrees,
                                                gap_world))
        effective_histogram = dict(sorted(Counter(row["effective_count"] for row in rows).items()))
        lit_histogram = dict(sorted(Counter(row["lit_count"] for row in rows).items()))
        accumulated_luma = [_luma(row["accumulated_light"]) for row in rows]
        result: dict[str, Any] = {
            "schema": SCHEMA,
            "status": "ok",
            "frame": frame,
            "ansi_cells": width * height,
            "sample_count": len(rows),
            "quarter_samples": len(rows),
            "assumptions": {
                "normal_degrees": normal_degrees,
                "equivalent_resolution": [equivalent_width, equivalent_height],
                "plane_count": PLANE_COUNT,
                "plane_gap_world": gap_world,
                "sample_lattice": "cell_scale_one_probe uses upper-left quarter at (x,y); quarter_subcells_2x uses each populated quarter at (2*x+slot%2, 2*y+slot//2); no OR aggregation",
                "support_span": "each projected profile is restricted to its longest contiguous bucket span with count>0; unsupported buckets never become zero visibility",
                "visual_acceptance": "not evaluated; compare the headed runtime frame directly with the designated target",
                "d1_variance": "only retained samples with effective_sample_mask=0xffff are compared with Binomial(16,duty)",
                "d2_equal": "median observed per-lit-plane luma quantum times lit count",
                "d2_geometric": "raw opacity=sum(alpha*(1-alpha)^i) over actual lit plane indices; calibrate full-16 raw opacity to full-16 equal-quantum luma",
                "d2_nearest4": "equal quantum times lit bits whose actual plane indices are 0..3",
                "step_luma": "signed and absolute lit/off residual means use the same Otsu residual classification; these values are diagnostic only",
            },
            "effective_sample_count_histogram": effective_histogram,
            "lit_count_histogram": lit_histogram,
            "accumulated_result": {
                "mean_luma": statistics.fmean(accumulated_luma),
                "max_luma": max(accumulated_luma),
                "nonzero_cells": sum(value > 0.0 for value in accumulated_luma),
            },
            "d3_single_plane": {
                "diagnostic_only": True,
                "planes": plane_profiles,
            },
            "d1_cross_plane_correlation": _correlations(rows, gap_world),
            "d1_lit_count_variance": _binomial(rows),
            "d2_synthetic": _synthetic(rows, sample_width, sample_height,
                                        equivalent_width, equivalent_height, normal_degrees),
        }
        return result
    except (OSError, SchemaError, KeyError, TypeError, ValueError) as exc:
        return _fail(str(exc))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", type=Path, required=True, help="final_render_cell_dump directory")
    parser.add_argument("--equivalent-width", type=int, default=EQUIVALENT_WIDTH)
    parser.add_argument("--equivalent-height", type=int, default=EQUIVALENT_HEIGHT)
    parser.add_argument("--normal-degrees", type=float, default=NORMAL_DEGREES)
    parser.add_argument("--plane-gap-world", type=float, default=DEFAULT_GAP_WORLD)
    parser.add_argument("--output-json", type=Path,
                        help="write the bounded result to this explicit run-artifact path")
    parser.add_argument("--pretty", action="store_true", help="indent the bounded JSON output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.equivalent_width <= 0 or args.equivalent_height <= 0:
        result = _fail("equivalent_resolution_non_positive")
    elif not math.isfinite(args.normal_degrees) or not math.isfinite(args.plane_gap_world) or args.plane_gap_world <= 0:
        result = _fail("profile_parameters_invalid")
    else:
        result = analyze_dump(
            args.dump,
            equivalent_width=args.equivalent_width,
            equivalent_height=args.equivalent_height,
            normal_degrees=args.normal_degrees,
            gap_world=args.plane_gap_world,
        )
    rendered = json.dumps(
        result, indent=2 if args.pretty else None, sort_keys=True,
        separators=None if args.pretty else (",", ":"))
    if args.output_json is not None:
        try:
            # Only an explicitly requested artifact parent may be created.
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            args.output_json.write_text(rendered + "\n", encoding="utf-8")
        except OSError as exc:
            result = _fail(f"output_json_write_failed:{type(exc).__name__}")
            rendered = json.dumps(result, sort_keys=True, separators=(",", ":"))
        print(json.dumps({
            "schema": result.get("schema"),
            "status": result.get("status"),
            "output_json": str(args.output_json),
            **({"reason": result["reason"]} if result.get("status") == "fail_closed" else {}),
        }, sort_keys=True, separators=(",", ":")))
    else:
        print(rendered)
    return 2 if result.get("status") == "fail_closed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
