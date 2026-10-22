"""FL-4876 FACADE-CAPTURE-01 guards (operator 2026-10-13 facade capture in the a3d editor).

Pure-function checks of the shared owner scripts/pipeline/compile_sign_sprites.py
that both the editor's live preview (map_editor.py) and the build use:
face orientation as seen from the street, the engine's per-cell height digits
(engine/sprite.cpp: '0'-'9' then 'A'-'Z'), XP layer-1 heights for sprites taller
than 10 rows, and that only accepted captures reach the build. The renderer-level
evidence (preview frame byte-identical to the rebuilt map; anchor sweep) is in
docs/FAILURE_LOG.md FL-4876.
"""

import gzip
import json
import struct
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO), str(REPO / "docs" / "agent" / "cli-anything")]

from scripts.pipeline import compile_sign_sprites as C  # noqa: E402


def test_face_frame_left_right_and_outward_normal():
    # CCW unit square; edge 0 runs (0,0)->(10,0) along the south side
    ring = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    f = C.face_frame(ring, 0)
    assert f["normal"] == pytest.approx([0.0, -1.0])      # faces south
    assert f["bearing_deg"] == pytest.approx(180.0)
    # a viewer south of the wall looks north: their left is west, their right is east
    assert f["left"] == [0.0, 0.0]
    assert f["right"] == pytest.approx([1.0, 0.0])
    assert f["length_m"] == pytest.approx(10.0)
    # clockwise ring gives the same outward normal
    g = C.face_frame(list(reversed(ring)), 2)             # (10,0)->(0,0)
    assert g["normal"] == pytest.approx([0.0, -1.0])
    assert g["left"] == [0.0, 0.0]


def test_height_digits_follow_engine_layer1_encoding():
    assert [chr(C._height_digit(k)) for k in (0, 9, 10, 35, 99, -3)] == ["0", "9", "A", "Z", "Z", "0"]


def _layers(raw: bytes):
    data = gzip.decompress(raw)
    n = struct.unpack_from("<I", data, 4)[0]
    o, out = 8, []
    for _ in range(n):
        w, h = struct.unpack_from("<ii", data, o)
        o += 8
        cells = {}
        for x in range(w):
            for y in range(h):
                g = struct.unpack_from("<I", data, o)[0]
                cells[(x, y)] = (g, tuple(data[o + 4:o + 7]), tuple(data[o + 7:o + 10]))
                o += 10
        out.append((w, h, cells))
    return out


def test_tall_sprite_rows_keep_rising_past_nine():
    rows = 17
    visual = [[(223, (255, 255, 255), (0, 0, 51))] * 3 for _ in range(rows)]
    w, h, l1 = _layers(C.xp_bytes(visual))[1]
    assert (w, h) == (3, rows)
    heights = [chr(l1[(0, y)][0]) for y in range(rows)]
    # top row (y=0) is 16 rows above the bottom: 'G'; the old min(9, ...) cap made rows 10+ lose the depth test
    assert heights[0] == "G" and heights[-1] == "0" and heights[rows - 1 - 10] == "A"


def test_only_accepted_captures_reach_the_build(tmp_path):
    caps = tmp_path / "operator_facade_captures.v1.json"
    caps.write_text(json.dumps({"captures": [
        {"capture_id": "FCAP-T-0001", "status": "retracted", "building_id": "b", "face_index": 0, "bands": []},
        {"capture_id": "FCAP-T-0002", "status": "superseded", "building_id": "b", "face_index": 0, "bands": []}]}))
    run = tmp_path / "run"
    run.mkdir()
    (run / "canonical.world_ir.json").write_text(json.dumps({"canonical_features": []}))
    signs, skipped, faces = C.operator_capture_signs(run, tmp_path, None, {}, caps)
    assert (signs, skipped, faces) == ([], [], [])


def test_editor_rejects_rows_the_engine_cannot_height():
    sys.path.insert(0, str(REPO / "artifacts" / "experiments" / "fl4876_manual_world"))
    import map_editor as ME
    ok = [[[223, [255, 0, 0], [0, 0, 0]]] * 2] * 36
    assert len(ME.clean_cells(ok, C)) == 36
    with pytest.raises(ValueError):
        ME.clean_cells([[[223, [0, 0, 0], [0, 0, 0]]]] * 37, C)
    snapped = ME.clean_cells([[[0, [254, 86, 1], [255, 255, 85]]] * 2], C)[0][0]
    assert snapped[0] == 1                      # glyph 0 is never written
    assert snapped[1] == (255, 102, 0)          # 6-level cube
    assert snapped[2] != C.KEY                  # never the XP transparency key


def test_one_column_grid_is_refused_before_encoding():
    # FACADE-CAPTURE-REVIEW-20261014 P2: the .xp header cell is layer 0 column 1
    sys.path.insert(0, str(REPO / "artifacts" / "experiments" / "fl4876_manual_world"))
    import map_editor as ME
    with pytest.raises(ValueError):
        ME.clean_cells([[[223, [255, 0, 0], [0, 0, 0]]]] * 3, C)
    with pytest.raises(ValueError):
        C.xp_bytes([[(223, (255, 0, 0), (0, 0, 0))]] * 3)
    assert C.xp_bytes([[(223, (255, 0, 0), (0, 0, 0))] * 2] * 3)


def _capture_run(tmp_path, bands, **extra):
    caps = tmp_path / "operator_facade_captures.v1.json"
    cap = {"capture_id": "FCAP-T-0003", "status": "accepted", "building_id": "b", "face_index": 0, "bands": bands}
    cap.update(extra)
    caps.write_text(json.dumps({"captures": [cap]}))
    return caps


def test_colour_only_capture_retires_no_signs(tmp_path, monkeypatch):
    # FACADE-CAPTURE-REVIEW-20261014 P1: a wall-colour capture must not delete the face's signs
    monkeypatch.setattr(C, "_rings", lambda run_root: {"b": [(0, 0), (10, 0), (10, 10)]})
    monkeypatch.setattr(C, "face_metrics", lambda *a: {"left_world": [0, 0], "right_world": [10, 0]})
    caps = _capture_run(tmp_path, [{"kind": "wall", "mode": "colour", "rgb": [51, 51, 51]}])
    signs, skipped, faces = C.operator_capture_signs(tmp_path, tmp_path, None, {}, caps)
    assert (signs, faces) == ([], [])


def test_sprite_capture_retires_face_signs_unless_opted_out(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "_rings", lambda run_root: {"b": [(0, 0), (10, 0), (10, 10)]})
    monkeypatch.setattr(C, "face_metrics", lambda *a: {"left_world": [0, 0], "right_world": [10, 0]})
    monkeypatch.setattr(C, "capture_anchor", lambda *a, **k: ([1.0, 2.0, 3.0], {"anchor_from_face_m": 0.3}))
    (tmp_path / "s.xp").write_bytes(C.xp_bytes([[(223, (255, 0, 0), (0, 0, 0))] * 2] * 2))
    band = {"kind": "sign", "mode": "sprite", "sprite": "s.xp", "x0_m": 0, "x1_m": 2, "y0_m": 2}
    out = tmp_path / "out"
    out.mkdir()
    signs, _, faces = C.operator_capture_signs(tmp_path, out, None, {}, _capture_run(tmp_path, [band]))
    assert len(signs) == 1 and [f[0] for f in faces] == ["FCAP-T-0003"]
    signs, _, faces = C.operator_capture_signs(tmp_path, out, None, {},
                                               _capture_run(tmp_path, [band], replaces_signs=False))
    assert len(signs) == 1 and faces == []


NODE_LIFECYCLE = r"""
const fs = require('fs'), vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8').replace('return {open, close, onSelect,', 'return {update, accept, open, close, onSelect,');
let resolve, added = 0; const removed = [];
const el = {value: '', checked: false, style: {}, classList: {add() {}, remove() {}, toggle() {}}, addEventListener() {}};
const ctx = {document: {getElementById: () => null, createElement: () => ({getContext: () => ({putImageData() {}}), toDataURL: () => 'data:,'})},
  S: {sel: []}, RT: {f: {pvAdd: () => ++added, pvRemove: s => removed.push(s)}}, Module: {FS: {mkdirTree() {}, writeFile() {}}},
  atob: s => Buffer.from(s, 'base64').toString('binary'), status() {}, api: () => new Promise(r => { resolve = r; }), setTimeout, clearTimeout};
vm.createContext(ctx); vm.runInContext(src + ';globalThis.testFC=FC;', ctx);
(async () => {
  // the reviewer's reproduction (panel DOM absent, so drawPanel stays closed): before the fix added=1
  const f = ctx.testFC; f.state.bid = 'b'; f.state.face = {face_index: 0};
  f.state.bands = [{mode: 'sprite', cells: [[[223, [255, 0, 0], [0, 0, 0]], [223, [255, 0, 0], [0, 0, 0]]]], x0_m: 0, x1_m: 1, y0_m: 0}];
  const p = f.update(); f.close(); resolve({bands: [{pos: [0, 0, 0], xp: 'AA==', wall_snap: {}}]}); await p;
  console.log(JSON.stringify({open: f.state.open, added, previewSlots: f.state.preview}));
})().catch(e => { console.log(JSON.stringify({error: String(e)})); });
"""


def test_cancel_voids_an_in_flight_preview(tmp_path):
    # FACADE-CAPTURE-REVIEW-20261014 P2: a preview response after Cancel adds nothing
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    js = REPO / "artifacts" / "experiments" / "fl4876_manual_world" / "map_editor_ui" / "facade_capture.js"
    script = tmp_path / "lifecycle.js"
    script.write_text(NODE_LIFECYCLE)
    out = subprocess.run([node, str(script), str(js)], capture_output=True, text=True, timeout=30)
    assert out.stdout.strip(), out.stderr[-2000:]
    res = json.loads(out.stdout.strip().splitlines()[-1])
    assert res.get("error") is None, res
    assert res["added"] == 0 and res["previewSlots"] == [] and res["open"] is False
