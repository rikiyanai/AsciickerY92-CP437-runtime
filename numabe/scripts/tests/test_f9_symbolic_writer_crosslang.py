#!/usr/bin/env python3
"""Cross-language fixture for the Godot F9 symbolic writer (TRUE-FF-940).

The byte string below is emitted by
godot_project/scripts/recording/F9SymbolicStreamWriter.gd (see the
F9_SYMBOLIC_STREAM_WRITER headless suite, golden case) and decoded here by
scripts/recording/f9_symbolic_codec.py. Agreement on these exact bytes is
the acceptance falsifier for NAMED UNKNOWN: any divergence means the
Godot encoder duplicated or drifted the tuple interpretation.

Run: python3 -m pytest scripts/tests/test_f9_symbolic_writer_crosslang.py -q
Nonzero exit or any failure is NOT-GREEN. No Godot, no headed run.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "recording"))

import f9_symbolic_codec as codec

# 2x1 grid, key seq0 (mode 3), delta seq1 (mode 3), drop seq2. Written here
# as the hex the Godot suite asserts with _hex(to_bytes()).
GOLDEN_HEX = (
    "463953594d76303102000100020000000300000047c6dcc4"
    "000000000003000000410000000000ff0000004200000000ff00"
    "000000010100000003000000010000000100000043000000ff00"
    "00111111020200000000000000"
)

# V2 contract-7 fixture emitted by test_f9_symbolic_stream_writer_v2.gd.
GOLDEN_V2_HEX = (
    "463953594d76303202000100020000000300000017df3598"
    "000000000003000000410000000000ff000000020111000000"
    "4200000000ff0000000005001200000001010000000300000001"
    "0000000100000043000000ff0000111111050113000000020200"
    "000000000000"
)


def test_golden_bytes_decode_and_reconstruct():
    raw = bytes.fromhex(GOLDEN_HEX)
    assert len(raw) == 89
    assert raw[:8] == codec.MAGIC_V1
    doc = codec.decode(raw)
    assert (doc["cols"], doc["rows"], doc["keyframe_every"]) == (2, 1, 2)
    assert codec.reconstruct(doc, 0, 0, 0) == (65, 0xFF0000, 0)
    assert codec.reconstruct(doc, 0, 1, 0) == (66, 0x00FF00, 0)
    assert codec.reconstruct(doc, 1, 1, 0) == (67, 0xFF, 0x111111)
    assert codec.reconstruct(doc, 1, 0, 0) == (65, 0xFF0000, 0)
    try:
        codec.reconstruct(doc, 2, 0, 0)
    except codec.ReconstructionGap as exc:
        assert exc.reason == "dropped"
    else:
        raise AssertionError("drop frame must not reconstruct")


def test_golden_join_reports_manifest_coverage():
    raw = bytes.fromhex(GOLDEN_HEX)
    doc = codec.decode(raw)
    covered, absent = codec.join_manifest(doc, [0, 1, 2, 7])
    assert covered == [0, 1, 2]
    assert absent == [7]


def test_v2_golden_decodes_contract7_lanes():
    raw = bytes.fromhex(GOLDEN_V2_HEX)
    assert len(raw) == 107
    assert raw[:8] == codec.MAGIC_V2
    doc = codec.decode(raw)
    assert doc["version"] == 2
    assert codec.reconstruct(doc, 0, 0, 0) == (65, 0xFF0000, 0, 1, 2, 17)
    assert codec.reconstruct(doc, 0, 1, 0) == (66, 0x00FF00, 0, 0, 5, 18)
    assert codec.reconstruct(doc, 1, 1, 0) == (67, 0xFF, 0x111111, 1, 5, 19)
    assert codec.contract7_stats(doc)["chain_turnovers"] == 1
