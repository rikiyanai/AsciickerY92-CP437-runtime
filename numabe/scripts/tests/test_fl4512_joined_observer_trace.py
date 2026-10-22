#!/usr/bin/env python3
"""Source contracts for the FL-4512 joined final-cell observer record."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRESENTER = (ROOT / "platform/terminal_gl_present.cpp").read_text(encoding="utf-8")
SCHEMA_PATH = (
    ROOT
    / "docs/research/ascii/verification/fl4260/fl4548/cell_trace.schema.json"
)


def test_bridge_record_joins_history_free_temporal_and_presented_identity() -> None:
    bridge = PRESENTER.split("int TermDumpBridgeCells", 1)[1]
    bridge = bridge.split("static void TermFl4548EmitCellTrace", 1)[0]
    assert '\\"schema\\":\\"fl4260.bridge_cells.v18\\"' in bridge
    assert '\\"schema\\":\\"fl4260.bridge_cells.v15\\"' not in bridge
    for token in (
        '\\"temporal\\":{',
        '\\"decision\\":\\"%s\\"',
        '\\"g_prev\\":%u',
        '\\"g_star\\":%u',
        '\\"g_selected\\":%u',
        "Fl4512TemporalDecisionNameV1",
        "final_result.sequence_hash",
    ):
        assert token in bridge


def test_cell_trace_copies_each_live_owner_decision() -> None:
    trace = PRESENTER.split("static void TermFl4548EmitCellTrace", 1)[1]
    trace = trace.split('extern "C" void TermSetRenderTargetOverride', 1)[0]
    for event in (
        "temporal_first",
        "temporal_keep",
        "temporal_replace",
        "absence_selected",
        "structural_sequence_change",
    ):
        assert event in trace
    assert "no temporal presentation owner" not in trace
    assert "Fl4512AbsenceReasonNameV1(trace_result.absence_reason)" in trace
    assert "disocclusion_reset" not in trace
    assert "g_fl4512_temporal_evictions" not in trace


def test_trace_schema_accepts_the_joined_owner_events() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    events = set(schema["properties"]["event"]["enum"])
    assert {
        "temporal_first",
        "temporal_keep",
        "temporal_replace",
        "disocclusion_reset",
        "absence_selected",
        "structural_sequence_change",
    } <= events
    assert "fl4512.primitive_field.v1" in schema["properties"]["descriptor_id"]["enum"]
    assert "S_pf.v1" in schema["properties"]["scorer_id"]["enum"]
