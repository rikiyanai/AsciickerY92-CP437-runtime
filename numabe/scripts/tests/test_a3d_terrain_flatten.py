"""FL-4876: artifacts/experiments/fl4876_manual_world/a3d_terrain_flatten.py lowers a baked building's plateau to the ground.

Server facts only (bytes, guards, journal, lock). Nothing here claims how the ground looks; the look is checked in the map editor
(assess/live_fix_20261017/buildings_pilot). A synthetic map in tmp_path: no live map is read or written.
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
CS = 5.1320023927966725
META = {"content_scale": CS, "offset_x": 48.0, "offset_y": 48.0, "origin_easting": 379699.09478405997,
        "origin_northing": 3938417.876643479, "projection_kind": "epsg32654_affine", "schema": "y92.classic_base_projection.v1"}
TAIL = b"TAIL-after-instances"
N = 8                                              # 8 x 8 patches = 64 x 64 world units


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def fl():
    return _load("fl4876_a3d_terrain_flatten", TOOL_DIR / "a3d_terrain_flatten.py")


@pytest.fixture(scope="module")
def ext():
    return _load("fl4876_a3d_terrain_extend_f", TOOL_DIR / "a3d_terrain_extend.py")


def ground(gx, gy):
    return 2000 + 3 * gx + 5 * gy                   # a plane, so a plane fit must restore it exactly


def _map(ext, tmp_path: Path, plateau=(20, 20, 36, 36), plateau2=None, dz=800):
    recs = []
    for py in range(N):
        for px in range(N):
            r = np.zeros((), ext.REC)
            r["x"], r["y"] = px, py
            v = np.full((8, 8), 16, np.uint16)
            v[0, 0] = 25 | 0x0100                    # a cell with a non-zero high byte
            r["visual"] = v
            h = np.zeros((5, 5), np.uint16)
            for j in range(5):
                for i in range(5):
                    gx, gy = px * 4 + i, py * 4 + j
                    z = ground(gx, gy)
                    for box in (plateau, plateau2):
                        if box and box[0] <= gx * 2 <= box[2] and box[1] <= gy * 2 <= box[3]:
                            z += dz
                    h[j, i] = z
            r["height"] = h
            r["diag"] = 1
            recs.append(r)
    table = bytearray(256 * 512)
    for m in (12, 16, 40):
        table[m * 512:m * 512 + 8] = b"\x80\x40\x30\xdb\x90\x50\x40\x00"
    data = b"AS3D" + struct.pack("<III", 16, len(recs), 0) + b"".join(r.tobytes() for r in recs) + bytes(table) \
        + struct.pack("<ii", -1, 0) + TAIL
    mp = tmp_path / "output.a3d"
    mp.write_bytes(data)
    (tmp_path / "terrain_metadata.json").write_text(json.dumps(META))
    return mp, data


def _en(x, y):
    return (x - 48.0) / CS + META["origin_easting"], (y - 48.0) / CS + META["origin_northing"]


def _poly_text(x0, y0, x1, y1):
    return " ".join("%.6f,%.6f" % _en(x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)))


def _box(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _verts(ext, data):
    n, _, recs = ext.read_terrain(data)
    out = {}
    for r in recs:
        for j in range(5):
            for i in range(5):
                out[(int(r["x"]) * 4 + i, int(r["y"]) * 4 + j)] = int(r["height"][j, i])
    return out


def _proj(fl, tmp_path):
    return fl.Projection(tmp_path / "terrain_metadata.json")


def test_plateau_is_lowered_to_the_ground_plane_and_nothing_else_moves(fl, ext, tmp_path):
    mp, data = _map(ext, tmp_path)
    out, rep = fl.flatten(data, _proj(fl, tmp_path), _box(20, 20, 36, 36))
    before, after = _verts(ext, data), _verts(ext, out)
    inside = {k for k in before if 20 <= k[0] * 2 <= 36 and 20 <= k[1] * 2 <= 36}
    assert inside and rep["vertices_lowered"] == len(inside)
    for k in inside:
        assert abs(after[k] - ground(*k)) <= 1                         # plane + residual (0) = the plane
    for k in set(before) - inside:
        assert after[k] == before[k]                                   # nothing outside the polygon changes
    n, _, b = ext.read_terrain(data)
    _, _, a = ext.read_terrain(out)
    assert len(out) == len(data) and out[:16] == data[:16] and out[16 + n * 188:] == data[16 + n * 188:]
    for f in ("x", "y", "diag", "visual"):
        assert np.array_equal(b[f], a[f])


def test_edge_vertices_agree_in_both_patches_that_store_them(fl, ext, tmp_path):
    mp, data = _map(ext, tmp_path)
    out, _ = fl.flatten(data, _proj(fl, tmp_path), _box(14, 14, 34, 34))   # polygon edges run across patch seams
    _, _, a = ext.read_terrain(out)
    seen = {}
    for r in a:
        for j in range(5):
            for i in range(5):
                k = (int(r["x"]) * 4 + i, int(r["y"]) * 4 + j)
                assert seen.setdefault(k, int(r["height"][j, i])) == int(r["height"][j, i])


def test_neighbour_plateau_is_left_alone_and_does_not_bend_the_ground(fl, ext, tmp_path):
    mp, data = _map(ext, tmp_path, plateau=(20, 20, 36, 36), plateau2=(40, 20, 56, 36))
    out, _ = fl.flatten(data, _proj(fl, tmp_path), _box(20, 20, 36, 36), excl_world=[_box(40, 20, 56, 36)])
    before, after = _verts(ext, data), _verts(ext, out)
    for k in before:
        if 40 <= k[0] * 2 <= 56 and 20 <= k[1] * 2 <= 36:
            assert after[k] == before[k]                               # the neighbour's plateau survives
        if 20 <= k[0] * 2 <= 36 and 20 <= k[1] * 2 <= 36:
            assert abs(after[k] - ground(*k)) <= 1                     # and was not used as ground


def test_vertices_below_the_ground_or_within_tolerance_are_never_raised(fl, ext, tmp_path):
    mp, data = _map(ext, tmp_path, plateau=(0, 0, 0, 0), dz=0)
    out, rep = fl.flatten(data, _proj(fl, tmp_path), _box(20, 20, 36, 36))
    assert out == data and rep["vertices_lowered"] == 0                # already ground: byte for byte the same


def test_paint_touches_only_cells_that_touch_the_polygon_and_keeps_high_bytes(fl, ext, tmp_path):
    mp, data = _map(ext, tmp_path)
    # corners at half units: cells 20..35 are overlapped, cells 19 and 36 are not even touched
    out, rep = fl.flatten(data, _proj(fl, tmp_path), _box(20.5, 20.5, 35.5, 35.5), paint_from=["all"], paint_material=40)
    _, _, b = ext.read_terrain(data)
    _, _, a = ext.read_terrain(out)
    cells_b, cells_a = {}, {}
    for src, dst in ((b, cells_b), (a, cells_a)):
        for r in src:
            for j in range(8):
                for i in range(8):
                    dst[(int(r["x"]) * 8 + i, int(r["y"]) * 8 + j)] = int(r["visual"][j, i])
    changed = {k for k in cells_b if cells_a[k] != cells_b[k]}
    assert changed == {(x, y) for x in range(20, 36) for y in range(20, 36)}    # exactly the 16 x 16 covered cells
    assert all(cells_a[k] & 255 == 40 for k in changed)
    assert cells_a[(0, 0)] == 25 | 0x0100                                         # untouched cell keeps its high byte
    assert rep["paint_material"] == 40 and sum(rep["cells_painted_by_old_material"].values()) == 256


def test_paint_auto_takes_the_surrounding_material(fl, ext, tmp_path):
    mp, data = _map(ext, tmp_path)
    _, rep = fl.flatten(data, _proj(fl, tmp_path), _box(20, 20, 36, 36), paint_from=["all"], paint_material="auto")
    assert rep["paint_material"] == 16


def test_paint_with_an_undefined_material_is_refused(fl, ext, tmp_path):
    mp, data = _map(ext, tmp_path)
    with pytest.raises(SystemExit):
        fl.flatten(data, _proj(fl, tmp_path), _box(20, 20, 36, 36), paint_from=["all"], paint_material=99)


def test_polygon_without_a_ground_ring_is_refused(fl, ext, tmp_path):
    mp, data = _map(ext, tmp_path)
    with pytest.raises(SystemExit):
        fl.flatten(data, _proj(fl, tmp_path), _box(-50, -50, 120, 120))      # covers the whole map: no ring


def test_cli_saves_atomically_with_backup_journal_and_dry_run_writes_nothing(fl, ext, tmp_path):
    mp, data = _map(ext, tmp_path)
    jr = tmp_path / "journal.jsonl"
    args = ["flatten", str(mp), "--polygon", _poly_text(20, 20, 36, 36), "--paint-from", "all", "--paint-material", "40",
            "--by", "test", "--basis", "synthetic", "--journal", str(jr)]
    assert fl.main(args + ["--dry-run"]) == 0
    assert mp.read_bytes() == data and not jr.exists() and not list(tmp_path.glob("*.pre_edit_*"))
    assert fl.main(args) == 0
    new = mp.read_bytes()
    assert new != data and len(new) == len(data) and new[-len(TAIL):] == TAIL
    assert mp.with_name(mp.name + ".pre_edit_" + hashlib.sha256(data).hexdigest()[:8]).read_bytes() == data
    assert not list(tmp_path.glob("*.saving"))
    rec = json.loads(jr.read_text().splitlines()[-1])
    assert rec["op"] == "terrain_flatten" and rec["edits"] == [] and rec["by"] == "test"
    assert rec["sha256_before"] == hashlib.sha256(data).hexdigest() and rec["sha256_after"] == hashlib.sha256(new).hexdigest()
    assert rec["report"]["vertices_lowered"] > 0


def test_feature_mode_reads_the_footprint_and_excludes_every_other_one(fl, ext, tmp_path):
    mp, data = _map(ext, tmp_path, plateau=(20, 20, 36, 36), plateau2=(40, 20, 56, 36))
    rows = [{"feature_id": "feature:building_footprint:way/1", "bake_footprint": _box(20, 20, 36, 36)},
            {"feature_id": "feature:building_footprint:way/2", "bake_footprint": _box(40, 20, 56, 36)}]
    inst = tmp_path / "building_instances.json"
    inst.write_text(json.dumps(rows))
    assert fl.main(["flatten", str(mp), "--feature", "way/1", "--instances", str(inst), "--by", "t", "--basis", "b",
                    "--journal", str(tmp_path / "j.jsonl")]) == 0
    after = _verts(ext, mp.read_bytes())
    assert abs(after[(14, 14)] - ground(14, 14)) <= 1 and after[(24, 14)] == ground(24, 14) + 800
