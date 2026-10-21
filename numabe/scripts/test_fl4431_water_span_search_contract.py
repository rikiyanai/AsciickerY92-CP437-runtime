#!/usr/bin/env python3
"""Prove FL-4431 water coverage survived the linear-to-binary search cut.

The live Metal frame showed that scanning 2,892 canonical water spans for every
subcell ray dominated frame time. This test compares the retired linear
coverage decision with the shipping predecessor binary search for every cell in
the real dumped heightfield whenever that same-run bank is available. A small
canonical fixture keeps the contract runnable on clean machines.

This is structural and CPU evidence only. It does not claim GPU timing, a
correct visible frame, or operator acceptance.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHADER_PATH = ROOT / "godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl"
DB_PATH = ROOT / "godot_project/render/scene/SceneGeometryDB.gd"
BUILDER_PATH = ROOT / "godot_project/render/core/contracts/visibility/VisibleSurfaceABI.gd"
REAL_DUMP_DIR = Path("/tmp/fl4431_bankdump")
REAL_SPAN_PATH = REAL_DUMP_DIR / "b75.bin"
REAL_HEIGHTFIELD_DESCRIPTOR_PATH = REAL_DUMP_DIR / "b72.bin"

# One decoded binding-75 record: row, inclusive run start, positive run length.
Span = tuple[int, int, int]


def function_body(source: str, name: str) -> str:
    """Return one GLSL function body including its braces."""
    marker = source.index(f"{name}(")
    opening = source.index("{", marker)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening : index + 1]
    raise AssertionError(f"unterminated GLSL function: {name}")


def decode_real_bank() -> tuple[tuple[int, int], list[Span]] | None:
    """Decode the same-run b72 dimensions plus b75 records when both exist."""
    if not REAL_SPAN_PATH.is_file() or not REAL_HEIGHTFIELD_DESCRIPTOR_PATH.is_file():
        return None
    descriptor = REAL_HEIGHTFIELD_DESCRIPTOR_PATH.read_bytes()
    words = REAL_SPAN_PATH.read_bytes()
    assert len(descriptor) == 64, "real b72 descriptor must retain its 64-byte ABI"
    assert len(words) % 8 == 0, "real b75 bank must contain whole 8-byte records"
    dims_x, dims_y = struct.unpack_from("<II", descriptor, 32)
    spans: list[Span] = []
    for offset in range(0, len(words), 8):
        packed_cell, run_length = struct.unpack_from("<II", words, offset)
        run_start_x = (packed_cell >> 16) & 0xFFFF
        cell_y = packed_cell & 0xFFFF
        spans.append((cell_y, run_start_x, run_length))
    return (dims_x, dims_y), spans


def synthetic_bank() -> tuple[tuple[int, int], list[Span]]:
    """Supply starts, ends, gaps, split rows, and completely empty rows."""
    return (17, 8), [
        (0, 0, 3),
        (0, 5, 2),
        (2, 1, 1),
        (2, 4, 5),
        (5, 0, 17),
        (7, 9, 3),
    ]


def validate_canonical(dims: tuple[int, int], spans: list[Span]) -> None:
    """Mirror the two host validators that make binary lookup sound."""
    dims_x, dims_y = dims
    assert 0 < dims_x <= 0x10000 and 0 < dims_y <= 0x10000
    previous_y = -1
    previous_end_x = -1
    for cell_y, run_start_x, run_length in spans:
        assert run_length > 0
        assert 0 <= run_start_x < dims_x
        assert 0 <= cell_y < dims_y
        assert run_length <= dims_x - run_start_x
        if cell_y == previous_y:
            assert run_start_x > previous_end_x
        else:
            assert cell_y > previous_y
        previous_y = cell_y
        previous_end_x = run_start_x + run_length


def linear_coverage(cell: tuple[int, int], spans: list[Span]) -> bool:
    """Reproduce the deleted full-bank half-open coverage loop."""
    cell_x, cell_y = cell
    covered = False
    for span_y, run_start_x, run_length in spans:
        run_end_x = run_start_x + run_length
        if (
            cell_y == span_y
            and cell_x >= run_start_x
            and cell_x < run_end_x
        ):
            covered = True
    return covered


def linear_coverage_map(dims: tuple[int, int], spans: list[Span]) -> bytearray:
    """Evaluate the old span loop once and retain its union for every cell.

    Re-running 2,892 Python iterations for each of 788,480 real cells would
    recreate the GPU defect inside the test and take many minutes. The deleted
    loop's only state is boolean union, so marking each half-open interval once
    produces the identical old result for the exhaustive comparison.
    """
    dims_x, dims_y = dims
    coverage = bytearray(dims_x * dims_y)
    for cell_y, run_start_x, run_length in spans:
        row_offset = cell_y * dims_x
        coverage[row_offset + run_start_x : row_offset + run_start_x + run_length] = (
            b"\x01" * run_length
        )
    return coverage


def binary_coverage(cell: tuple[int, int], spans: list[Span]) -> bool:
    """Reproduce the shader's upper-bound search plus predecessor test."""
    cell_x, cell_y = cell
    lower = 0
    upper = len(spans)
    while lower < upper:
        middle = lower + (upper - lower) // 2
        span_y, run_start_x, _run_length = spans[middle]
        key_after_selected = span_y > cell_y or (
            span_y == cell_y and run_start_x > cell_x
        )
        if key_after_selected:
            upper = middle
        else:
            lower = middle + 1
    if lower == 0:
        return False
    span_y, run_start_x, run_length = spans[lower - 1]
    return (
        cell_y == span_y
        and cell_x >= run_start_x
        and cell_x < run_start_x + run_length
    )


def validate_source_contract() -> None:
    """Pin the host authority and the logarithmic shader implementation."""
    shader = SHADER_PATH.read_text(encoding="utf-8")
    db = DB_PATH.read_text(encoding="utf-8")
    builder = BUILDER_PATH.read_text(encoding="utf-8")
    assert ".length()" not in shader, "Metal runtime-array capacity query reappeared"
    assert "cell_rect.position.y < previous_cell_y" in db
    assert "cell_rect.position.x <= previous_end_x" in db
    assert "cell_y < previous_cell_y" in builder
    assert "run_start_x <= previous_end_x" in builder

    query = function_body(shader, "scene_query_water")
    search = function_body(shader, "scene_water_cell_covered")
    record = function_body(shader, "scene_water_span_record")
    assert "scene_water_cell_covered(" in query
    assert query.index("if (!plane_candidate)") < query.index(
        "scene_water_cell_covered("
    ), "no-plane rays must return before touching the span bank"
    assert "while (lower < upper)" in search
    assert "middle = lower + (upper - lower) / 2u" in search
    assert "lower - 1u" in search
    assert "selected_cell.x < candidate_end_x" in search
    assert "for (uint span_index" not in query
    for token in (
        "run_length != 0u",
        "run_start_x < dims.x",
        "cell_y < dims.y",
        "run_length <= dims.x - run_start_x",
    ):
        assert token in record, f"touched-span safety gate drifted: {token}"


def compare_bank(
    dims: tuple[int, int],
    spans: list[Span],
    require_empty_row: bool,
) -> tuple[int, int, int]:
    """Compare every cell plus every explicit half-open run boundary."""
    validate_canonical(dims, spans)
    dims_x, dims_y = dims
    old_coverage = linear_coverage_map(dims, spans)
    occupied_rows = {cell_y for cell_y, _run_start_x, _run_length in spans}
    empty_row_count = dims_y - len(occupied_rows)
    if require_empty_row:
        assert empty_row_count > 0, "fixture must exercise at least one empty row"
    comparisons = 0
    for cell_y in range(dims_y):
        for cell_x in range(dims_x):
            old_value = old_coverage[cell_y * dims_x + cell_x] != 0
            new_value = binary_coverage((cell_x, cell_y), spans)
            assert old_value == new_value, (
                f"coverage mismatch at ({cell_x}, {cell_y}): "
                f"linear={old_value} binary={new_value}"
            )
            comparisons += 1

    # Explicitly name the half-open boundaries in addition to the exhaustive
    # grid. A run end must be dry unless a later separated span covers it.
    boundary_checks = 0
    for cell_y, run_start_x, run_length in spans:
        for cell_x in (run_start_x - 1, run_start_x, run_start_x + run_length - 1,
                       run_start_x + run_length):
            if 0 <= cell_x < dims_x:
                old_value = linear_coverage((cell_x, cell_y), spans)
                new_value = binary_coverage((cell_x, cell_y), spans)
                assert old_value == new_value
                assert old_value == (
                    old_coverage[cell_y * dims_x + cell_x] != 0
                )
                boundary_checks += 1
    return comparisons, boundary_checks, empty_row_count


def main() -> int:
    """Run source pins plus real-bank and explicit empty-row equivalence."""
    validate_source_contract()
    decoded = decode_real_bank()
    source = "real"
    if decoded is None:
        decoded = synthetic_bank()
        source = "synthetic"
    dims, spans = decoded
    comparisons, boundary_checks, empty_row_count = compare_bank(
        dims, spans, source == "synthetic"
    )

    # The captured 880x896 bank happens to own water on every row. Keep a second
    # small fixture so empty rows and their row-transition search behavior stay
    # covered even when the real artifact has no such row.
    synthetic_dims, synthetic_spans = synthetic_bank()
    synthetic_comparisons = 0
    if source == "real":
        synthetic_comparisons, _synthetic_boundaries, synthetic_empty_rows = (
            compare_bank(synthetic_dims, synthetic_spans, True)
        )
        assert synthetic_empty_rows > 0

    dims_x, dims_y = dims
    print(
        "FL4431_WATER_SPAN_SEARCH_CONTRACT_PASS "
        f"source={source} dims={dims_x}x{dims_y} spans={len(spans)} "
        f"exhaustive_cells={comparisons} boundary_checks={boundary_checks} "
        f"empty_rows={empty_row_count} synthetic_cells={synthetic_comparisons}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
