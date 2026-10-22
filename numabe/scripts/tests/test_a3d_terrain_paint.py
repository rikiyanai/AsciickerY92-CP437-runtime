"""FL-4876: artifacts/experiments/fl4876_manual_world/a3d_terrain_paint.py paints terrain visual cells inside a polygon.

Server facts only (bytes, journal, lock, guards). Nothing here claims how the paving looks; the look is checked in the map
editor (assess/live_fix_20261017/walkway). A tiny synthetic map in tmp_path: no live map is read or written.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL_DIR = ROOT / "artifacts/experiments/fl4876_manual_world"
META = {"content_scale": 5.1320023927966725, "offset_x": 48.0, "offset_y": 48.0, "origin_easting": 379699.09478405997,
        "origin_northing": 3938417.876643479, "projection_kind": "epsg32654_affine", "schema": "y92.classic_base_projection.v1"}
TAIL = b"TAIL-after-instances"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def paint():
    return _load("fl4876_a3d_terrain_paint", TOOL_DIR / "a3d_terrain_paint.py")


@pytest.fixture(scope="module")
def ext():
    return _load("fl4876_a3d_terrain_extend_p", TOOL_DIR / "a3d_terrain_extend.py")


def _map(ext, tmp_path: Path):
    recs = []
    for py in range(4):
        for px in range(4):
            r = np.zeros((), ext.REC)
            r["x"], r["y"] = px, py
            v = np.full((8, 8), 16, np.uint16)
            v[:, :4] = 26                                   # left half of every patch is "footpath"
            v[0, 0] = 28 | 0x0100                            # a road cell with a non-zero high byte
            r["visual"] = v
            r["height"] = (600 + 3 * px + 5 * py + np.arange(25).reshape(5, 5)).astype(np.uint16)
            r["diag"] = 1
            recs.append(r)
    table = bytearray(256 * 512)
    table[18 * 512:18 * 512 + 8] = b"\x80\x40\x30\xdb\x90\x50\x40\x00"   # material 18 defined, 17 left empty
    data = b"AS3D" + struct.pack("<III", 16, len(recs), 0) + b"".join(r.tobytes() for r in recs) + bytes(table) \
        + struct.pack("<ii", -1, 0) + TAIL
    mp = tmp_path / "output.a3d"
    mp.write_bytes(data)
    (tmp_path / "terrain_metadata.json").write_text(json.dumps(META))
    return mp, data


def _cell_world_to_en(x, y):
    return (x - 48.0) / 5.1320023927966725 + META["origin_easting"], (y - 48.0) / 5.1320023927966725 + META["origin_northing"]


def _poly(x0, y0, x1, y1):
    return " ".join("%.6f,%.6f" % _cell_world_to_en(x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)))


def test_paints_only_listed_materials_inside_polygon_and_keeps_all_other_bytes(paint, ext, tmp_path):
    mp, data = _map(ext, tmp_path)
    jr = tmp_path / "journal.jsonl"
    # world box x 2..10, y 2..6 covers cells 2..9 x 2..5: patches (0,0) and (1,0); footpath = columns 0-3 of each patch
    rc = paint.main(["paint", str(mp), "--polygon", _poly(2, 2, 10, 6), "--from", "26", "--material", "18",
                     "--by", "test", "--basis", "synthetic", "--journal", str(jr)])
    assert rc == 0
    new = mp.read_bytes()
    n, _, before = ext.read_terrain(data)
    _, _, after = ext.read_terrain(new)
    assert len(new) == len(data) and new[:16] == data[:16] and new[16 + n * 188:] == data[16 + n * 188:]
    for f in ("x", "y", "height", "diag"):
        assert np.array_equal(before[f], after[f])
    exp = np.array(before["visual"])
    # patch 0: cells x 2,3 (low 26) and y 2..5 -> 18; patch 1 (world x 8..15): box covers x 8,9 which are columns 0,1 (26) -> 18
    exp[0][2:6, 2:4] = 18
    exp[1][2:6, 0:2] = 18
    assert np.array_equal(after["visual"], exp)
    assert after["visual"][0][0, 0] == 28 | 0x0100                 # untouched, high byte intact
    keep = mp.with_name(mp.name + ".pre_edit_" + hashlib.sha256(data).hexdigest()[:8])
    assert keep.read_bytes() == data
    rec = json.loads(jr.read_text().splitlines()[-1])
    assert rec["op"] == "terrain_paint" and rec["edits"] == [] and rec["cells_painted_by_old_material"] == {"26": 16}
    assert rec["sha256_after"] == hashlib.sha256(new).hexdigest() and rec["by"] == "test"


def test_cells_of_other_materials_in_polygon_are_left_alone(paint, ext, tmp_path):
    mp, data = _map(ext, tmp_path)
    out, painted, _ = paint.paint(data, paint.Projection(tmp_path / "terrain_metadata.json"),
                                  paint.parse_polygon(_poly(4, 0, 8, 8)), {26}, 18)
    # x 4..7 of patch 0 is land (16): nothing to paint
    assert painted == {} and out == data


def test_undefined_material_is_refused_and_nothing_written(paint, ext, tmp_path):
    mp, data = _map(ext, tmp_path)
    with pytest.raises(SystemExit):
        paint.main(["paint", str(mp), "--polygon", _poly(0, 0, 8, 8), "--from", "26", "--material", "17",
                    "--by", "t", "--basis", "b", "--journal", str(tmp_path / "j.jsonl")])
    assert mp.read_bytes() == data and not (tmp_path / "j.jsonl").exists()


def test_dry_run_writes_nothing(paint, ext, tmp_path):
    mp, data = _map(ext, tmp_path)
    paint.main(["paint", str(mp), "--polygon", _poly(0, 0, 8, 8), "--from", "26", "--material", "18", "--by", "t",
                "--basis", "b", "--journal", str(tmp_path / "j.jsonl"), "--dry-run"])
    assert mp.read_bytes() == data and not (tmp_path / "j.jsonl").exists()
    assert not list(tmp_path.glob("*.pre_edit_*"))


def test_polygon_with_two_vertices_is_refused(paint):
    with pytest.raises(SystemExit):
        paint.parse_polygon("1,2 3,4")
