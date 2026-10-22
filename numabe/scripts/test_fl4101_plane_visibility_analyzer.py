#!/usr/bin/env python3
"""Pure offline contract tests for the FL-4101 b62 plane analyzer."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.analyze_fl4101_plane_visibility import (
    _geometric_raw_opacity,
    _parse_dump,
    _project_values,
    _runs,
    analyze_dump,
)


def _write_dump(root: Path, *, head: str = "a" * 40, with_godray: bool = True,
                quarter_bits: list[int] | None = None,
                one_probe: bool = False) -> None:
    width, height = 32, 24
    metadata = {
        "git_head": head,
        "dirty": True,
        "frame_index": 7,
        "ansi_size": {"w": width, "h": height},
        "pixel_camera_capture_contract_valid": True,
        "fl4231_terrain_edge_capture": {
            "schema": "fl4231.f13_terrain_edge_capture.v1",
            "state": "same_request_join_readback_ok",
            "frame_index": 7,
            "b62_sha256": "b62",
            "b64_sha256": "b64",
        },
    }
    root.mkdir()
    (root / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    with (root / "cells.jsonl").open("w", encoding="utf-8") as stream:
        for y in range(height):
            for x in range(width):
                quarters = []
                for slot in range(4):
                    if one_probe and slot > 0:
                        quarter = {
                            "state": "terrain_edge_diagnostic_readback_ok",
                            "godray_lit_bits": 0,
                            "godray_sample_mask": 0,
                            "godray_sample_count": 0,
                            "godray_accumulated_light": {"x": 0.0, "y": 0.0, "z": 0.0},
                        }
                        quarters.append(quarter)
                        continue
                    quarter = {
                        "state": "terrain_edge_diagnostic_readback_ok",
                        "godray_lit_bits": (quarter_bits[slot] if quarter_bits is not None
                                             else (0xFFFF if (x // 4) % 2 else 0)),
                        "godray_sample_mask": 0xFFFF,
                        "godray_sample_count": 16,
                        "godray_accumulated_light": {"x": 0.01, "y": 0.01, "z": 0.01},
                    }
                    if not with_godray:
                        quarter.pop("godray_lit_bits")
                        quarter.pop("godray_sample_mask")
                        quarter.pop("godray_sample_count")
                        quarter.pop("godray_accumulated_light")
                    quarters.append(quarter)
                stream.write(json.dumps({
                    "row_type": "exact_final_cell",
                    "screen_cell": {"x": x, "y": y},
                    "terrain_edge_b62_quarters": quarters,
                }) + "\n")


def test_missing_plane_extension_fails_closed(tmp_path: Path) -> None:
    dump = tmp_path / "missing"
    _write_dump(dump, with_godray=False)
    result = analyze_dump(dump)
    assert result["status"] == "fail_closed"
    assert "missing_godray_lit_bits" in result["reason"]


def test_identity_failure_fails_closed(tmp_path: Path) -> None:
    dump = tmp_path / "bad_identity"
    _write_dump(dump, head="")
    result = analyze_dump(dump)
    assert result["status"] == "fail_closed"
    assert result["reason"] == "git_head_missing_or_invalid"


def test_complete_quarter_schema_emits_bounded_d1_d2_d3(tmp_path: Path) -> None:
    dump = tmp_path / "complete"
    _write_dump(dump)
    result = analyze_dump(dump, equivalent_width=64, equivalent_height=48)
    assert result["status"] == "ok"
    assert result["frame"]["frame_index"] == 7
    assert len(result["d3_single_plane"]["planes"]) == 16
    assert result["d3_single_plane"]["diagnostic_only"] is True
    assert "pass" not in result["d3_single_plane"]
    assert "passing_plane_indices" not in result["d3_single_plane"]
    assert result["d1_lit_count_variance"]["comparable"] is True
    assert result["d2_synthetic"]["status"] == "ok"
    assert result["d2_synthetic"]["diagnostic_only"] is True
    assert "target" not in result["d2_synthetic"]
    assert len(result["d2_synthetic"]["models"]) == 5
    assert all("verdict" not in model for model in result["d2_synthetic"]["models"])


def test_quarters_remain_subcells_without_or_inflation(tmp_path: Path) -> None:
    dump = tmp_path / "independent_quarters"
    _write_dump(dump, quarter_bits=[1, 0, 0, 0])
    result = analyze_dump(dump, equivalent_width=64, equivalent_height=48)
    assert result["status"] == "ok"
    assert result["ansi_cells"] == 32 * 24
    assert result["quarter_samples"] == 32 * 24 * 4
    assert result["lit_count_histogram"] == {0: 32 * 24 * 3, 1: 32 * 24}
    assert result["d2_synthetic"]["models"][0]["result_luma"]["max"] == 0.01


def test_one_probe_capture_uses_cell_scale_upper_left_sample(tmp_path: Path) -> None:
    dump = tmp_path / "one_probe"
    _write_dump(dump, one_probe=True)
    metadata, rows = _parse_dump(dump)
    assert metadata["_frame_identity"]["sample_lattice"]["mode"] == "cell_scale_one_probe"
    assert metadata["_frame_identity"]["sample_size"] == {"w": 32, "h": 24}
    assert len(rows) == 32 * 24
    assert max(row["x"] for row in rows) == 31
    assert max(row["y"] for row in rows) == 23
    result = analyze_dump(dump, equivalent_width=64, equivalent_height=48)
    assert result["status"] == "ok"
    assert result["sample_count"] == 32 * 24
    assert result["frame"]["sample_lattice"]["mode"] == "cell_scale_one_probe"


def test_frame_wide_mode_keeps_legacy_after_initial_zero_cell(tmp_path: Path) -> None:
    dump = tmp_path / "legacy_initial_zero"
    _write_dump(dump, quarter_bits=[0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF])
    lines = (dump / "cells.jsonl").read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    for quarter in first["terrain_edge_b62_quarters"]:
        quarter["godray_lit_bits"] = 0
        quarter["godray_sample_mask"] = 0
        quarter["godray_sample_count"] = 0
        quarter["godray_accumulated_light"] = {"x": 0.0, "y": 0.0, "z": 0.0}
    lines[0] = json.dumps(first)
    (dump / "cells.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = analyze_dump(dump, equivalent_width=64, equivalent_height=48)
    assert result["status"] == "ok"
    assert result["frame"]["sample_lattice"]["mode"] == "quarter_subcells_2x"
    assert result["sample_count"] == 32 * 24 * 4


def test_geometric_weight_uses_sparse_actual_plane_indices() -> None:
    alpha = 0.25
    assert _geometric_raw_opacity(1 << 0, alpha) == alpha
    assert _geometric_raw_opacity(1 << 5, alpha) == alpha * (1.0 - alpha) ** 5
    assert _geometric_raw_opacity((1 << 0) | (1 << 5), alpha) == \
        alpha + alpha * (1.0 - alpha) ** 5


def test_runs_uses_data_derived_threshold_for_known_45_percent_duty() -> None:
    profile = []
    for _ in range(40):
        profile.extend([1.0] * 9)
        profile.extend([0.0] * 11)
    metrics = _runs(profile)
    assert metrics["threshold_method"] == "otsu"
    assert 0.42 <= metrics["duty"] <= 0.48
    assert metrics["step_luma_signed"] > 0.0
    assert metrics["step_luma_abs"] == metrics["step_luma_signed"]
    assert metrics["step_direction"] == "bright_over_baseline"


def test_unsupported_buckets_are_trimmed_not_shadow_runs() -> None:
    profile, _, support_fraction, support_span = _project_values(
        {(0, 0): 1.0, (7, 0): 1.0},
        {(0, 0): True, (7, 0): True},
        width=8,
        height=1,
        equivalent_width=8,
        equivalent_height=1,
        normal_degrees=0.0,
    )
    assert support_fraction == 2 / 8
    assert support_span is not None
    assert support_span["length"] == 1
    assert len(profile) == 1
    assert _runs(profile, support_span)["run_count"] == 0
