#!/usr/bin/env python3
"""Focused tests for the F9 binary symbolic sidecar (TRUE-FF-937 rework).

The rejected 936 JSON tuple stream is deleted: on-disk records are binary
bytes with magic/version/dimensions, bounded keyframe interval, exact
glyph/FG/BG integers, changed-cell delta indices, and gap/drop semantics.
Tests assert binary round-trip, out-of-order seeks, delta/keyframe
boundaries, drops/gaps, byte-size ceilings, malformed rejection, the
shipped JS decoder under node, and legacy viewer parity.

Run: python3 -m pytest scripts/tests/test_f9_symbolic_sidecar.py -q
Nonzero exit or any failure is NOT-GREEN. No Godot, no browser, no headed run:
the node check executes the shipped decoder logic without a browser, and the
receipt reports in-browser execution as unverified.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "recording"))

import f9_symbolic_codec as codec

BUILDER = ROOT / "scripts" / "build_f10_frames_html.py"

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

MANIFEST_LINES = [
    "frame: seq=0 engine=100 unix_ms=1000 rendered=99 f10=0:NORMAL"
    " camera=perspective pos=1.00,2.00,3.00 fov=58.00 cell=32x64:terminal"
    " matrix=[4,3] image=8x6 png=frame_00000.png capture_ms=0.05",
    "frame: seq=1 engine=101 unix_ms=1100 rendered=100 f10=0:NORMAL"
    " camera=perspective pos=1.00,2.00,3.00 fov=58.00 cell=32x64:terminal"
    " matrix=[4,3] image=8x6 png=frame_00001.png capture_ms=0.06",
    "frame: seq=2 engine=102 unix_ms=1200 rendered=101 f10=0:NORMAL"
    " camera=perspective pos=1.00,2.00,3.00 fov=58.00 cell=32x64:terminal"
    " matrix=[4,3] image=8x6 png=frame_00002.png capture_ms=0.05",
    "frame: seq=3 engine=103 unix_ms=1300 rendered=102 f10=1:FGONLY"
    " camera=perspective pos=1.00,2.00,3.00 fov=58.00 cell=32x64:terminal"
    " matrix=[4,3] image=8x6 png=frame_00003.png capture_ms=0.07",
]


def _synthetic_doc() -> tuple[dict, dict]:
    """Logical key/delta/drop stream plus expected per-frame grids."""
    base = [[(i % 90) + 33, 0x100000 + i, 0x000100 + i] for i in range(12)]
    model: dict[int, list] = {0: [tuple(c) for c in base]}
    grid1 = [tuple(c) for c in base]
    grid1[5] = (65, 0xFF0000, 0x000001)
    grid1[11] = (66, 0x00FF00, 0x000002)
    model[1] = grid1
    key3 = [[(i * 7) % 200 + 40, 0x0000FF - i, i] for i in range(12)]
    model[3] = [tuple(c) for c in key3]
    doc = {
        "cols": 4, "rows": 3, "keyframe_every": 2,
        "frames": [
            {"seq": 0, "kind": "key", "mode": 3, "f10": 0, "cells": base},
            {"seq": 1, "kind": "delta", "mode": 3, "f10": 0,
             "changes": [[5, 65, 0xFF0000, 0x000001],
                         [11, 66, 0x00FF00, 0x000002]]},
            {"seq": 2, "kind": "drop"},
            {"seq": 3, "kind": "key", "mode": 5, "f10": 1, "cells": key3},
        ],
    }
    return doc, model


def _frames_dir(tmp_path: Path, *, sidecar: bytes | None = None,
                stale_json: bool = False) -> Path:
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    for seq in range(4):
        (frames_dir / f"frame_{seq:05d}.png").write_bytes(PNG_1X1)
    (frames_dir / "manifest.txt").write_text(
        "# asciicker.screen_recording_manifest.v1\n"
        + "\n".join(MANIFEST_LINES) + "\n",
        encoding="utf-8",
    )
    if sidecar is not None:
        (frames_dir / codec.SIDECAR_FILENAME).write_bytes(sidecar)
    if stale_json:
        (frames_dir / "symbolic.v1.json").write_text('{"schema": "old"}',
                                                     encoding="utf-8")
    return frames_dir


def test_binary_round_trip_reconstructs_every_frame_and_cell():
    doc, model = _synthetic_doc()
    raw = codec.encode(doc)
    assert raw[:8] == codec.MAGIC_V1
    with pytest.raises(Exception):
        json.loads(raw)
    back = codec.decode(raw)
    for seq in (0, 1, 3):
        for y in range(3):
            for x in range(4):
                assert codec.reconstruct(back, seq, x, y) == model[seq][y * 4 + x]


def test_v2_contract7_round_trip_keeps_metadata_and_stats():
    doc = {
        "version": 2, "cols": 2, "rows": 1, "keyframe_every": 2,
        "frames": [
            {"seq": 0, "kind": "key", "cells": [
                [65, 1, 2, 1, 3, 41], [66, 3, 4, 0, 5, 42],
            ]},
            {"seq": 1, "kind": "delta", "changes": [
                [1, 67, 5, 6, 1, 5, 43],
            ]},
        ],
    }
    raw = codec.encode(doc)
    assert raw[:8] == codec.MAGIC_V2
    # 24 header + key(9 + 2*16) + delta(13 + 20).
    assert len(raw) == 98
    back = codec.decode(raw)
    assert back["version"] == back["format_version"] == 2
    assert codec.reconstruct(back, 0, 0, 0) == (65, 1, 2, 1, 3, 41)
    assert codec.reconstruct(back, 1, 1, 0) == (67, 5, 6, 1, 5, 43)
    assert codec.contract7_stats(back) == {
        "present": True, "version": 2, "frames": 2,
        "keyframe_cells": 2, "tween_cells": 1,
        "roles": {"3": 2, "5": 2}, "chain_turnovers": 1,
    }


def test_v2_rejects_reserved_role_bits_and_bad_metadata():
    base = {"version": 2, "cols": 1, "rows": 1, "keyframe_every": 2}
    for cell in ([65, 1, 2, 2, 3, 1], [65, 1, 2, 0, 256, 1],
                 [65, 1, 2, 0, 3, 0x100000000]):
        with pytest.raises(codec.SidecarError):
            codec.encode({**base, "frames": [
                {"seq": 0, "kind": "key", "cells": [cell]},
            ]})


def test_packed_byte_layout_is_exact():
    doc, _ = _synthetic_doc()
    raw = codec.encode(doc)
    # 24 header + key(9+120) + delta(9+4+28) + drop(9) + key(9+120) = 332.
    assert len(raw) == 332
    magic, cols, rows, kf, reserved, count, _crc = struct.unpack(
        "<8sHHHHII", raw[:24])
    assert magic == b"F9SYMv01"
    assert (cols, rows, kf, reserved, count) == (4, 3, 2, 0, 4)
    assert b"glyph" not in raw and b"cells" not in raw
    decoded = codec.decode(raw)
    assert decoded["count"] == count
    assert decoded["crc"] == struct.unpack("<I", raw[20:24])[0]


def test_seek_reconstructs_without_sequential_walk():
    doc, model = _synthetic_doc()
    back = codec.decode(codec.encode(doc))
    assert codec.reconstruct(back, 3, 2, 1) == model[3][1 * 4 + 2]
    assert codec.reconstruct(back, 1, 3, 2) == model[1][2 * 4 + 3]


def test_drop_and_gap_label_without_values():
    doc, _ = _synthetic_doc()
    back = codec.decode(codec.encode(doc))
    with pytest.raises(codec.ReconstructionGap) as exc:
        codec.reconstruct(back, 2, 0, 0)
    assert exc.value.reason == "dropped"
    gap_doc = {"cols": 2, "rows": 1, "keyframe_every": 5, "frames": [
        {"seq": 0, "kind": "key", "cells": [[65, 1, 2], [66, 3, 4]]},
        {"seq": 1, "kind": "gap", "through": 2},
        {"seq": 3, "kind": "key", "cells": [[67, 5, 6], [68, 7, 8]]},
    ]}
    gap_back = codec.decode(codec.encode(gap_doc))
    for seq in (1, 2):
        with pytest.raises(codec.ReconstructionGap) as exc:
            codec.reconstruct(gap_back, seq, 0, 0)
        assert exc.value.reason == "missing"
    assert codec.reconstruct(gap_back, 3, 1, 0) == (68, 7, 8)


def test_encode_rejects_gap_overlap_and_delta_after_loss():
    overlap = {"cols": 1, "rows": 1, "keyframe_every": 5, "frames": [
        {"seq": 0, "kind": "key", "cells": [[65, 1, 2]]},
        {"seq": 1, "kind": "gap", "through": 5},
        {"seq": 3, "kind": "key", "cells": [[66, 1, 2]]},
    ]}
    with pytest.raises(codec.SidecarError):
        codec.encode(overlap)
    after_drop = {"cols": 1, "rows": 1, "keyframe_every": 5, "frames": [
        {"seq": 0, "kind": "key", "cells": [[65, 1, 2]]},
        {"seq": 1, "kind": "drop"},
        {"seq": 2, "kind": "delta", "changes": [[0, 66, 1, 2]]},
    ]}
    with pytest.raises(codec.SidecarError):
        codec.encode(after_drop)
    after_gap = {"cols": 1, "rows": 1, "keyframe_every": 5, "frames": [
        {"seq": 0, "kind": "key", "cells": [[65, 1, 2]]},
        {"seq": 1, "kind": "gap", "through": 2},
        {"seq": 3, "kind": "delta", "changes": [[0, 66, 1, 2]]},
    ]}
    with pytest.raises(codec.SidecarError):
        codec.encode(after_gap)
    recovery = {"cols": 1, "rows": 1, "keyframe_every": 5, "frames": [
        {"seq": 0, "kind": "key", "cells": [[65, 1, 2]]},
        {"seq": 1, "kind": "drop"},
        {"seq": 2, "kind": "key", "cells": [[66, 3, 4]]},
    ]}
    back = codec.decode(codec.encode(recovery))
    assert codec.reconstruct(back, 2, 0, 0) == (66, 3, 4)


def test_cadence_boundary_accepts_limit_rejects_overflow():
    cells = [[65, 1, 2]]
    ok_doc = {"cols": 1, "rows": 1, "keyframe_every": 2, "frames": [
        {"seq": 0, "kind": "key", "cells": cells},
        {"seq": 1, "kind": "delta", "changes": []},
        {"seq": 2, "kind": "delta", "changes": []},
        {"seq": 3, "kind": "key", "cells": cells},
    ]}
    codec.decode(codec.encode(ok_doc))
    bad_doc = {"cols": 1, "rows": 1, "keyframe_every": 1, "frames": [
        {"seq": 0, "kind": "key", "cells": cells},
        {"seq": 1, "kind": "delta", "changes": []},
        {"seq": 2, "kind": "delta", "changes": []},
    ]}
    with pytest.raises(codec.SidecarError):
        codec.encode(bad_doc)


def _mutate(raw: bytes, offset: int, value: int) -> bytes:
    blown = bytearray(raw)
    blown[offset] = value
    return bytes(blown)


def test_malformed_and_truncated_streams_fail_closed():
    doc, _ = _synthetic_doc()
    raw = codec.encode(doc)
    cases = [
        _mutate(raw, 0, 0x58),            # bad magic
        _mutate(raw, 14, 0xFF),           # reserved nonzero
        _mutate(raw, 8, 0),               # cols zero
        _mutate(raw, 23, raw[23] ^ 0xFF),  # CRC break
        raw[:10],                          # truncated header
        raw[:30],                          # truncated record
        raw + b"\x00",                     # trailing bytes
        _mutate(raw, 24, 9),               # unknown kind id
        _mutate(raw, 36, 0x02),               # glyph out of range (key cell 0)
    ]
    for blown in cases:
        with pytest.raises(codec.SidecarError):
            codec.decode(blown)
    # seq regression: patch delta seq below key seq.
    reg = bytearray(raw)
    struct.pack_into("<I", reg, 24 + 129 + 1, 0)
    with pytest.raises(codec.SidecarError):
        codec.decode(bytes(reg))
    # first record not a key.
    drop_first = bytearray(raw)
    drop_first[24] = codec.KIND_DROP
    with pytest.raises(codec.SidecarError):
        codec.decode(bytes(drop_first))
    # delta index outside grid.
    oob = bytearray(raw)
    struct.pack_into("<I", oob, 24 + 129 + 9 + 4, 99)
    with pytest.raises(codec.SidecarError):
        codec.decode(bytes(oob))


def test_finite_ceilings_reject_before_allocating():
    doc, _ = _synthetic_doc()
    raw = codec.encode(doc)
    huge_count = bytearray(raw)
    struct.pack_into("<I", huge_count, 16, codec.MAX_FRAMES + 1)
    with pytest.raises(codec.SidecarError):
        codec.decode(bytes(huge_count))
    with pytest.raises(codec.SidecarError):
        codec.decode(b"\x00" * (codec.MAX_FILE_BYTES + 1))
    too_many = {"cols": 1, "rows": 1, "keyframe_every": 100000,
                "frames": [{"seq": 0, "kind": "key", "cells": [[65, 1, 2]]}]
                + [{"seq": s, "kind": "delta", "changes": []}
                   for s in range(1, codec.MAX_FRAMES + 1)]}
    with pytest.raises(codec.SidecarError):
        codec.encode(too_many)


def test_encode_size_ceiling_and_scalar_widths():
    doc, _ = _synthetic_doc()
    raw = codec.encode(doc)
    assert len(raw) == 332 < codec.MAX_FILE_BYTES
    old_max = codec.MAX_FILE_BYTES
    codec.MAX_FILE_BYTES = 100
    try:
        with pytest.raises(codec.SidecarError):
            codec.encode(doc)
        with pytest.raises(codec.SidecarError):
            codec.decode(raw)
    finally:
        codec.MAX_FILE_BYTES = old_max
    wide_seq = {"cols": 1, "rows": 1, "keyframe_every": 5, "frames": [
        {"seq": 0x100000000, "kind": "key", "cells": [[65, 1, 2]]}]}
    with pytest.raises(codec.SidecarError):
        codec.encode(wide_seq)
    wide_through = {"cols": 1, "rows": 1, "keyframe_every": 5, "frames": [
        {"seq": 0, "kind": "key", "cells": [[65, 1, 2]]},
        {"seq": 1, "kind": "gap", "through": 0x1FFFFFFFF}]}
    with pytest.raises(codec.SidecarError):
        codec.encode(wide_through)


def _rec(kind: int, seq: int, payload: bytes = b"") -> bytes:
    return struct.pack("<BIHH", kind, seq, 0, 0) + payload


def _assemble(records, *, cols: int = 1, rows: int = 1,
              kf: int = 5) -> bytes:
    import zlib
    body = b"".join(records)
    header = struct.pack("<8sHHHHII", codec.MAGIC_V1, cols, rows, kf, 0,
                         len(records), zlib.crc32(body) & 0xFFFFFFFF)
    return header + body


def _key_record(seq: int, glyph: int) -> bytes:
    return _rec(0, seq, struct.pack("<I3s3s", glyph, b"\x01\x00\x00",
                                    b"\x02\x00\x00"))


def test_decode_rejects_overlap_and_delta_after_loss():
    gap = _rec(3, 1, struct.pack("<I", 9))
    over = _assemble([_key_record(0, 65), gap, _key_record(3, 66)])
    with pytest.raises(codec.SidecarError):
        codec.decode(over)
    delta = _rec(1, 2, struct.pack("<I", 0))
    lost = _assemble([_key_record(0, 65), _rec(2, 1), delta])
    with pytest.raises(codec.SidecarError):
        codec.decode(lost)
    gap_delta = _assemble([_key_record(0, 65),
                           _rec(3, 1, struct.pack("<I", 2)),
                           _rec(1, 3, struct.pack("<I", 0))])
    with pytest.raises(codec.SidecarError):
        codec.decode(gap_delta)


def test_join_rejects_outside_seqs_and_reports_absence():
    doc, _ = _synthetic_doc()
    back = codec.decode(codec.encode(doc))
    covered, absent = codec.join_manifest(back, [0, 1, 2, 3, 9])
    assert covered == [0, 1, 2, 3]
    assert absent == [9]
    stray_raw = codec.encode({
        "cols": 4, "rows": 3, "keyframe_every": 5, "frames": [
            {"seq": 0, "kind": "key",
             "cells": [[65 + i, i, i] for i in range(12)]},
            {"seq": 77, "kind": "drop"},
        ]})
    with pytest.raises(codec.SidecarError):
        codec.join_manifest(codec.decode(stray_raw), [0, 1, 2, 3])


def test_join_rejects_unbounded_gap_before_iterating():
    raw = codec.encode({
        "cols": 1, "rows": 1, "keyframe_every": 5, "frames": [
            {"seq": 0, "kind": "key", "cells": [[65, 1, 2]]},
            {"seq": 1, "kind": "gap", "through": 0xFFFFFFFF},
        ],
    })
    with pytest.raises(codec.SidecarError, match="exceeds manifest"):
        codec.join_manifest(codec.decode(raw), [0, 1])


def _run_builder(frames_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(BUILDER), str(frames_dir)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120)


def _head_builder_html(frames_dir: Path, tmp_path: Path) -> str:
    old = tmp_path / "head_builder.py"
    proc = subprocess.run(
        ["git", "show", f"HEAD:scripts/build_f10_frames_html.py"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, "HEAD builder must be readable"
    old.write_text(proc.stdout, encoding="utf-8")
    # The HEAD builder imports its recording/ siblings (codec, session).
    (tmp_path / "recording").mkdir(exist_ok=True)
    for sibling in (ROOT / "scripts" / "recording").glob("f9_*.py"):
        shutil.copy(sibling, tmp_path / "recording" / sibling.name)
    out_dir = tmp_path / "head_frames"
    out_dir.mkdir()
    for child in frames_dir.iterdir():
        if child.name not in (codec.SIDECAR_FILENAME, "symbolic.v1.json"):
            (out_dir / child.name).write_bytes(child.read_bytes())
    proc = subprocess.run(
        [sys.executable, str(old), str(out_dir)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    return (out_dir / "frames.html").read_text(encoding="utf-8")


def _frames_payload(html: str) -> object:
    match = re.search(r"const FRAMES = (.*?);\n", html, re.DOTALL)
    assert match, "FRAMES payload must be embedded"
    return json.loads(match.group(1))


def test_legacy_viewer_output_preserved_without_sidecar(tmp_path):
    frames_dir = _frames_dir(tmp_path)
    proc = _run_builder(frames_dir)
    assert proc.returncode == 0, proc.stderr
    html = (frames_dir / "frames.html").read_text(encoding="utf-8")
    head_html = _head_builder_html(frames_dir, tmp_path)
    assert _frames_payload(html) == _frames_payload(head_html)
    for key in ("cellRect", "eventCell", "drawGrid", "drawZoom",
                "rectCells", "selBBox"):
        assert f"function {key}(" in html, key
    for key in ("seq", "engine_frame", "unix_ms", "rendered_frame",
                "f10_view", "camera", "camera_pos", "fov", "cell",
                "matrix", "image", "png", "capture_ms"):
        assert f"'{key}'" in html, key
    assert 'const SYMBOLIC_B64 = "";' in html
    primary = "box.textContent = 'primary (' + s[0]"
    assert primary in html and primary in head_html


def _decoder_js(html: str) -> str:
    start = html.index("// __SYM_DECODER_START__")
    end = html.index("// __SYM_DECODER_END__")
    return html[start:end]


def test_viewer_embeds_binary_and_labels_cells(tmp_path):
    doc, _ = _synthetic_doc()
    raw = codec.encode(doc)
    frames_dir = _frames_dir(tmp_path, sidecar=raw)
    proc = _run_builder(frames_dir)
    assert proc.returncode == 0, proc.stderr
    html = (frames_dir / "frames.html").read_text(encoding="utf-8")
    assert "function symDecode(b64)" in html
    assert "sym: dropped frame" in html
    assert "sym: no symbolic data" in html
    assert "sym: invalid sidecar" in html
    assert len(_frames_payload(html)) == 4
    match = re.search(r'const SYMBOLIC_B64 = "(.*?)";', html)
    assert match, "binary payload must be embedded"
    assert base64.b64decode(match.group(1)) == raw


def test_shipped_js_decoder_matches_python_model(tmp_path):
    doc, model = _synthetic_doc()
    raw = codec.encode(doc)
    frames_dir = _frames_dir(tmp_path, sidecar=raw)
    assert _run_builder(frames_dir).returncode == 0
    html = (frames_dir / "frames.html").read_text(encoding="utf-8")
    node = shutil.which("node")
    if node is None:
        pytest.skip("node unavailable; JS decoder execution unverified")
    driver = (
        f'const SYMBOLIC_B64 = "{base64.b64encode(raw).decode()}";\n'
        + _decoder_js(html)
        + "const out = [];\n"
        + "for (const seq of [0, 1, 3]) for (let y = 0; y < 3; y++)"
        + " for (let x = 0; x < 4; x++)"
        + " out.push(symbolicAt(seq, x, y));\n"
        + "out.push(symbolicAt(2, 0, 0));\n"
        + "console.log(JSON.stringify(out));\n"
    )
    proc = subprocess.run([node, "-e", driver], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    want = []
    for seq in (0, 1, 3):
        for y in range(3):
            for x in range(4):
                g, fg, bg = model[seq][y * 4 + x]
                want.append({"ok": True, "glyph": g, "fg": fg, "bg": bg,
                             "mode": 3 if seq < 3 else 5})
    want.append({"ok": False, "reason": "dropped"})
    assert got == want


def test_shipped_js_decoder_consumes_v2_contract7_lanes(tmp_path):
    doc = {
        "version": 2, "cols": 2, "rows": 1, "keyframe_every": 2,
        "frames": [
            {"seq": 0, "kind": "key", "mode": 3, "f10": 0,
             "cells": [[65, 0xFF0000, 0, 1, 2, 17],
                        [66, 0x00FF00, 0, 0, 5, 18]]},
            {"seq": 1, "kind": "delta", "mode": 3, "f10": 0,
             "changes": [[1, 67, 0x0000FF, 0x111111, 1, 5, 19]]},
        ],
    }
    raw = codec.encode(doc)
    frames_dir = _frames_dir(tmp_path, sidecar=raw)
    assert _run_builder(frames_dir).returncode == 0
    html = (frames_dir / "frames.html").read_text(encoding="utf-8")
    node = shutil.which("node")
    if node is None:
        pytest.skip("node unavailable; JS decoder execution unverified")
    driver = (
        f'const SYMBOLIC_B64 = "{base64.b64encode(raw).decode()}";\n'
        + _decoder_js(html)
        + "console.log(JSON.stringify([symbolicAt(0, 0, 0), "
          "symbolicAt(1, 0, 0), symbolicAt(1, 1, 0), "
          "symText(1, 1, 0)]));\n"
    )
    proc = subprocess.run([node, "-e", driver], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert got[:3] == [
        {"ok": True, "glyph": 65, "fg": 0xFF0000, "bg": 0,
         "mode": 3, "keyframe_tag": 1, "role": 2, "chain_id": 17},
        {"ok": True, "glyph": 65, "fg": 0xFF0000, "bg": 0,
         "mode": 3, "keyframe_tag": 1, "role": 2, "chain_id": 17},
        {"ok": True, "glyph": 67, "fg": 0x0000FF, "bg": 0x111111,
         "mode": 3, "keyframe_tag": 1, "role": 5, "chain_id": 19},
    ]
    assert "keyframe_tag=1 role=5 chain_id=19" in got[3]


def test_shipped_js_decoder_rejects_ambiguous_streams(tmp_path):
    doc, _ = _synthetic_doc()
    raw = codec.encode(doc)
    frames_dir = _frames_dir(tmp_path, sidecar=raw)
    assert _run_builder(frames_dir).returncode == 0
    html = (frames_dir / "frames.html").read_text(encoding="utf-8")
    node = shutil.which("node")
    if node is None:
        pytest.skip("node unavailable; JS decoder execution unverified")
    gap = _rec(3, 1, struct.pack("<I", 9))
    over = _assemble([_key_record(0, 65), gap, _key_record(3, 66)])
    delta = _rec(1, 2, struct.pack("<I", 0))
    lost = _assemble([_key_record(0, 65), _rec(2, 1), delta])
    cases = [base64.b64encode(over).decode(),
             base64.b64encode(lost).decode()]
    driver = (
        'const SYMBOLIC_B64 = "";\n'
        + _decoder_js(html)
        + "const out = [];\n"
        + f"const cases = {json.dumps(cases)};\n"
        + "for (const b64 of cases) {\n"
        + "  try { symDecode(b64); out.push('ok'); }\n"
        + "  catch (e) { out.push('err'); }\n"
        + "}\n"
        + "console.log(JSON.stringify(out));\n"
    )
    proc = subprocess.run([node, "-e", driver], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == ["err", "err"]


def test_builder_rejects_corrupt_binary(tmp_path):
    doc, _ = _synthetic_doc()
    raw = bytearray(codec.encode(doc))
    raw[0] = 0x58
    frames_dir = _frames_dir(tmp_path, sidecar=bytes(raw))
    proc = _run_builder(frames_dir)
    assert proc.returncode != 0
    assert "symbolic sidecar rejected" in proc.stderr


def test_builder_rejects_join_mismatch(tmp_path):
    raw = codec.encode({
        "cols": 4, "rows": 3, "keyframe_every": 5, "frames": [
            {"seq": 0, "kind": "key",
             "cells": [[65 + i, i, i] for i in range(12)]},
            {"seq": 77, "kind": "drop"},
        ]})
    frames_dir = _frames_dir(tmp_path, sidecar=raw)
    proc = _run_builder(frames_dir)
    assert proc.returncode != 0
    assert "join mismatch" in proc.stderr


def test_builder_ignores_stale_json_without_sidecar(tmp_path):
    frames_dir = _frames_dir(tmp_path, stale_json=True)
    proc = _run_builder(frames_dir)
    assert proc.returncode == 0, proc.stderr
    assert "ignored" in proc.stderr
    html = (frames_dir / "frames.html").read_text(encoding="utf-8")
    assert 'const SYMBOLIC_B64 = "";' in html
