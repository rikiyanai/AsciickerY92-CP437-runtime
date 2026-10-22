"""FL-4783 compiler and immutable GlyphProgram port-graph contract tests."""

from __future__ import annotations

import importlib.util
import shutil
import struct
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
COMPILER = ROOT / "scripts/compile_fl4683_glyph_program.py"
LOADER_SOURCE = ROOT / "platform/fl4683_glyph_program_loader.cpp"


def _load_compiler():
    spec = importlib.util.spec_from_file_location("fl4683_program_fl4783", COMPILER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PROGRAM = _load_compiler()


def _port(
    port_id: int,
    side: str,
    boundary: list[int],
    endpoint: list[int],
    tangent: tuple[int, int] = (23170, -23170),
) -> dict:
    return {
        "port_id": port_id,
        "component_id": 0,
        "side_or_corner": side,
        "boundary_t_q0_16": None,
        "position_xy": boundary,
        "endpoint_xy": endpoint,
        "virtual_boundary": True,
        "tangent_q1_15": list(tangent),
        "endpoint_or_junction_degree": 1,
        "terminal_kind": "endpoint",
    }


def _slash_record(glyph_record_id: int = 4711) -> dict:
    ports = [
        _port(0, "NE", [7, 0], [6, 1], (-23170, 23170)),
        _port(1, "SW", [0, 7], [1, 6], (23170, -23170)),
    ]
    return {
        "glyph_record_id": glyph_record_id,
        "structure": {"endpoint_count": 2, "junction_count": 0},
        "connectivity": {
            "stroke_port_graph": {
                "graph_schema": "u7.stroke_port_graph.v1",
                "graph_valid": True,
                "graph_rejection_reason": None,
                "boundary_port_count": 2,
                "port_connectivity_count": 1,
                "skeleton_component_graph": {
                    "components": 1,
                    "holes": 0,
                    "junctions": 0,
                    "skeleton_edges": 5,
                },
                "boundary_ports": ports,
                "port_connectivity": [
                    {
                        "component_id": 0,
                        "port_a": 0,
                        "port_b": 1,
                        "connected": True,
                        "arc_length_q8_8": 1810,
                        "turn_count": 0,
                        "curvature_class": "straight",
                    }
                ],
            }
        },
    }


def test_virtual_slash_graph_round_trips_without_mask_derivation():
    rows, ports, connectivity = PROGRAM.encode_glyph_port_graph_tables(
        [_slash_record()], {4711}, {4711: 0}
    )

    assert rows == [
        {
            "glyph_record_id": 4711,
            "component_count": 1,
            "boundary_port_offset": 0,
            "boundary_port_count": 2,
            "connectivity_offset": 0,
            "connectivity_count": 1,
            "hole_count": 0,
            "junction_count": 0,
            "skeleton_edge_count": 5,
        }
    ]
    assert len(ports) == PROGRAM.BOUNDARY_PORT_RECORD_SIZE * 2
    assert len(connectivity) == PROGRAM.PORT_CONNECTIVITY_RECORD_SIZE

    first = struct.unpack_from("<HBBHhhHBBBBBBH", ports, 0)
    second = struct.unpack_from("<HBBHhhHBBBBBBH", ports, PROGRAM.BOUNDARY_PORT_RECORD_SIZE)
    assert first == (0, 1, 1, 0, -23170, 23170, 0, 6, 1, 7, 0, 1, 0, 0)
    assert second == (0, 5, 1, 0, 23170, -23170, 1, 1, 6, 0, 7, 1, 0, 0)
    assert (first[1], second[1]) == (PROGRAM.PORT_SIDE_OR_CORNER["NE"], PROGRAM.PORT_SIDE_OR_CORNER["SW"])
    assert struct.unpack("<HHHHBBHI", connectivity) == (0, 0, 1, 1810, 0, 0, 0, 0)


def test_non_structural_invalid_graph_is_skipped_but_structural_graph_is_required():
    invalid = _slash_record(9)
    invalid["connectivity"]["stroke_port_graph"]["graph_valid"] = False
    invalid["connectivity"]["stroke_port_graph"]["graph_rejection_reason"] = (
        "capacity_exceeded:boundary_ports"
    )
    rows, ports, connectivity = PROGRAM.encode_glyph_port_graph_tables(
        [invalid, _slash_record(4711)], {4711}, {4711: 0}
    )
    assert [row["glyph_record_id"] for row in rows] == [4711]
    assert len(ports) == 40
    assert len(connectivity) == 16

    with pytest.raises(PROGRAM.CompileError, match="rejected stroke-port graph"):
        PROGRAM.encode_glyph_port_graph_tables([invalid], {9})

    mismatch = _slash_record(4711)
    mismatch["connectivity"]["stroke_port_graph"]["skeleton_component_graph"][
        "junctions"
    ] = 1
    with pytest.raises(PROGRAM.CompileError, match="disagree with U7 member descriptor"):
        PROGRAM.encode_glyph_port_graph_tables([mismatch], {4711}, {4711: 0})


def test_overflow_graph_is_rejected_without_truncation():
    record = _slash_record()
    graph = record["connectivity"]["stroke_port_graph"]
    graph["boundary_port_count"] = 9
    graph["boundary_ports"] = [
        _port(index, "N", [index % 8, 0], [index % 8, 1])
        for index in range(9)
    ]
    with pytest.raises(PROGRAM.CompileError, match="runtime graph ABI allows at most 8"):
        PROGRAM.encode_glyph_port_graph_tables([record], {4711})


def test_connectivity_overflow_is_rejected_without_truncation():
    record = _slash_record()
    graph = record["connectivity"]["stroke_port_graph"]
    graph["boundary_port_count"] = 8
    graph["boundary_ports"] = [
        _port(index, "N", [index, 0], [index, 1]) for index in range(8)
    ]
    graph["port_connectivity_count"] = 29
    graph["port_connectivity"] = [
        {
            "component_id": 0,
            "port_a": index % 8,
            "port_b": (index + 1) % 8,
            "connected": True,
            "arc_length_q8_8": 256,
            "turn_count": 0,
            "curvature_class": "straight",
        }
        for index in range(29)
    ]
    with pytest.raises(PROGRAM.CompileError, match="allows at most 28"):
        PROGRAM.encode_glyph_port_graph_tables([record], {4711})


def test_skeleton_edge_overflow_is_rejected_without_truncation():
    record = _slash_record()
    record["connectivity"]["stroke_port_graph"]["skeleton_component_graph"][
        "skeleton_edges"
    ] = 257
    with pytest.raises(PROGRAM.CompileError, match="allows at most 256"):
        PROGRAM.encode_glyph_port_graph_tables([record], {4711})


def test_enabled_loader_compiles_fixed_width_graph_view(tmp_path):
    compiler = shutil.which("c++") or shutil.which("clang++")
    if compiler is None:
        pytest.skip("no C++ compiler in the test environment")
    harness = tmp_path / "fl4783_graph_view.cpp"
    harness.write_text(
        r'''
#include <cstring>
#include <cstdint>
#include "platform/fl4683_glyph_program_loader.h"

int main() {
    alignas(8) unsigned char storage[128] = {};
    auto* graph = reinterpret_cast<Fl4683GlyphProgramGlyphPortGraphV1*>(storage);
    auto* ports = reinterpret_cast<Fl4683GlyphProgramBoundaryPortV1*>(storage + 32);
    auto* pairs = reinterpret_cast<Fl4683GlyphProgramPortConnectivityV1*>(storage + 72);
    graph->glyph_record_id = 4711;
    graph->boundary_port_offset = 32;
    graph->boundary_port_count = 2;
    graph->connectivity_offset = 72;
    graph->connectivity_count = 1;
    graph->component_count = 1;
    graph->skeleton_edge_count = 5;
    Fl4683GlyphProgramImageV1 image = {};
    image.glyph_port_graphs = graph;
    image.glyph_port_graph_count = 1;
    image.boundary_ports = ports;
    image.boundary_port_count = 2;
    image.port_connectivity = pairs;
    image.port_connectivity_count = 1;
    image.owned_bytes = storage;
    image.owned_byte_size = sizeof(storage);
    Fl4683GlyphProgramGlyphPortGraphViewV1 view = {};
    if (!Fl4683GlyphProgramGlyphPortGraphViewFindV1(&image, 4711, &view)) return 1;
    if (view.graph != graph || view.boundary_ports != ports || view.boundary_port_count != 2) return 2;
    if (view.port_connectivity != pairs || view.connectivity_count != 1) return 3;
    if (Fl4683GlyphProgramGlyphPortGraphViewFindV1(&image, 99, &view)) return 4;
    graph->component_count = 0;
    if (Fl4683GlyphProgramGlyphPortGraphViewFindV1(&image, 4711, &view)) return 5;
    graph->component_count = 1;
    graph->boundary_port_offset = 120;
    if (Fl4683GlyphProgramGlyphPortGraphViewFindV1(&image, 4711, &view)) return 6;
    graph->boundary_port_offset = 32;
    graph->skeleton_edge_count = 257;
    if (Fl4683GlyphProgramGlyphPortGraphViewFindV1(&image, 4711, &view)) return 7;
    return 0;
}
''',
        encoding="ascii",
    )
    binary = tmp_path / "fl4783_graph_view"
    subprocess.run(
        [
            compiler,
            "-std=c++17",
            "-DFL4683_GLYPH_PROGRAM_LOADER_ENABLED",
            "-I.",
            "-Iplatform",
            "-Iengine",
            "-Iengine/render",
            str(harness),
            str(LOADER_SOURCE),
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run([str(binary)], check=True)
