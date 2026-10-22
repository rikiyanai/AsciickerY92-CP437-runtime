"""FL-4876: artifacts/experiments/fl4876_manual_world/a3d_terrain_extend.py appends terrain patches to a built map.

Server facts only (bytes, journal, locks, seams, material ids): nothing here claims how the river looks. The look is
checked in the map editor (assess/live_fix_20261016/tama_river). Builds a tiny synthetic map, synthetic DEM tiles and a
synthetic OSM file in tmp_path, so no download and no live map is read or written.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL_DIR = ROOT / "artifacts/experiments/fl4876_manual_world"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ext():
    return _load("fl4876_a3d_terrain_extend", TOOL_DIR / "a3d_terrain_extend.py")


@pytest.fixture(scope="module")
def edit():
    return _load("fl4876_a3d_edit", TOOL_DIR / "a3d_edit.py")


META = {"content_scale": 5.1320023927966725, "offset_x": 48.0, "offset_y": 48.0, "origin_easting": 379699.09478405997,
        "origin_northing": 3938417.876643479, "projection_kind": "epsg32654_affine", "schema": "y92.classic_base_projection.v1",
        "terrain_bounds": {"max_x": 32.0, "max_y": 32.0, "min_x": 0, "min_y": 0}}
TAIL = b"TAIL-after-instances"


def _existing_records(ext):
    recs = []
    for py in range(4):
        for px in range(4):
            r = np.zeros((), ext.REC)
            r["x"], r["y"] = px, py
            r["visual"] = 16 | (0x0100 if (px, py) == (1, 1) else 0)  # a non-zero high byte must survive untouched
            gi = 4 * px + np.arange(5)[None, :]   # a function of the global vertex, so shared patch edges agree (as in a real map)
            gj = 4 * py + np.arange(5)[:, None]
            r["height"] = (600 + 5 * gi + 11 * gj + (gi * gj) % 7).astype(np.uint16)
            r["diag"] = (px + py) & 1
            recs.append(r)
    return recs


def _write_map(ext, d: Path) -> bytes:
    recs = _existing_records(ext)
    body = np.array(recs, dtype=ext.REC).tobytes()
    mats = bytes((i * 31 + 7) & 0xFF for i in range(256 * 512))
    mesh, name = b"thing.akm", b"inst-0"
    inst = struct.pack("<ii", -4, 1) + struct.pack("<i", len(mesh)) + mesh + struct.pack("<i", len(name)) + name
    inst += struct.pack("<16d", *([1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 16.0, 0, 10.5, 12.5, 700.0, 1.0])) + struct.pack("<ii", 3, -1)
    data = b"AS3D" + struct.pack("<III", 16, len(recs), 0) + body + mats + inst + TAIL
    (d / "output.a3d").write_bytes(data)
    (d / "terrain_metadata.json").write_text(json.dumps(META))
    (d / "dem_lowering_receipt.json").write_text(json.dumps({"elevation": {"local_bake_datum_m": 6.5}}))
    return data


def _write_dem(ext, d: Path) -> Path:
    """3 x 3 z15 tiles around the map; elevation(global pixel) = 9 + 0.01 * column, so bilinear sampling is exact."""
    proj = ext.Projection(d / "terrain_metadata.json")
    lon, lat = proj.lonlat_of_world(np.array([0.0]), np.array([0.0]))
    n = 2 ** 15
    tx = int(math.floor((float(lon[0]) + 180) / 360 * n))
    ty = int(math.floor((1 - math.asinh(math.tan(math.radians(float(lat[0])))) / math.pi) / 2 * n))
    dem = d / "dem"
    dem.mkdir()
    for a in (tx - 1, tx, tx + 1):
        for b in (ty - 1, ty, ty + 1):
            cols = (a * 256 + np.arange(256)) * 0.01
            rows = np.tile(np.round(9 + cols % 40, 2), (256, 1))
            (dem / f"dem5a_15_{a}_{b}.txt").write_text("\n".join(",".join(f"{v:.2f}" for v in row) for row in rows) + "\n")
    return dem


def _write_osm(ext, d: Path) -> Path:
    proj = ext.Projection(d / "terrain_metadata.json")

    def node(i, x, y):
        lon, lat = proj.lonlat_of_world(np.array([x], float), np.array([y], float))
        return f'<node id="{i}" lat="{float(lat[0]):.9f}" lon="{float(lon[0]):.9f}"/>'
    pts = {1: (-40, 4), 2: (-20, 4), 3: (-20, 28), 4: (-40, 28),          # water polygon
           11: (-60, 0), 12: (-45, 0), 13: (-45, 10), 14: (-60, 10),      # park
           21: (-62, 30), 22: (-45, 30)}                                  # service road (half width 9 units)
    xml = ["<osm version='0.6'>"] + [node(i, *p) for i, p in pts.items()]
    xml.append('<way id="100"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/><tag k="natural" v="water"/></way>')
    xml.append('<way id="101"><nd ref="11"/><nd ref="12"/><nd ref="13"/><nd ref="14"/><nd ref="11"/><tag k="leisure" v="park"/></way>')
    xml.append('<way id="102"><nd ref="21"/><nd ref="22"/><tag k="highway" v="service"/></way></osm>')
    p = d / "tama.osm"
    p.write_text("\n".join(xml))
    return p


def _run(ext, d: Path, **kw):
    proj = ext.Projection(d / "terrain_metadata.json")
    (e0, e1), (n0, n1) = proj.utm(np.array([-64.0, 0.0]), np.array([0.0, 32.0]))
    kw.setdefault("blend_m", 2.0)
    return ext.extend_map(d / "output.a3d", [(float(e0), float(n0), float(e1), float(n1))], [d / "dem"], d / "tama.osm",
                          "pytest", "synthetic map, DEM and OSM", ext.parse_materials(None), journal=d / "journal.jsonl", **kw)


@pytest.fixture()
def world(ext, tmp_path):
    data = _write_map(ext, tmp_path)
    _write_dem(ext, tmp_path)
    _write_osm(ext, tmp_path)
    return tmp_path, data


def test_extension_adds_negative_patches_and_keeps_existing_bytes(ext, edit, world):
    d, before = world
    stats = _run(ext, d)
    after = (d / "output.a3d").read_bytes()
    n0, _, ex0 = ext.read_terrain(before)
    n1, _, ex1 = ext.read_terrain(after)
    assert stats["new_patches"] == 8 * 4 and n1 == n0 + 32
    assert int(ex1["x"].min()) == -8 and int(ex1["y"].min()) == 0
    assert stats["sha256_before"] == hashlib.sha256(before).hexdigest() and stats["sha256_after"] == hashlib.sha256(after).hexdigest()
    # every existing record is byte identical (a non-zero visual high byte and the diag bits included)
    old = {(int(r["x"]), int(r["y"])): r.tobytes() for r in ex0}
    got = {(int(r["x"]), int(r["y"])): r.tobytes() for r in ex1}
    assert all(got[k] == v for k, v in old.items())
    # canonical (y, x) order, header count, and everything after the terrain table copied verbatim
    keys = [(int(r["y"]), int(r["x"])) for r in ex1]
    assert keys == sorted(keys)
    assert after[:4] == b"AS3D" and struct.unpack_from("<II", after, 4) == (16, n1)
    assert after[16 + n1 * ext.PATCH_BYTES:] == before[16 + n0 * ext.PATCH_BYTES:]
    assert after.endswith(TAIL)
    # round trip through the instance editor's parser: same instance table
    _, v0, r0, t0 = edit.parse(before)
    _, v1, r1, t1 = edit.parse(after)
    assert (v1, t1) == (v0, t0) and [r["inst"] for r in r1] == [r["inst"] for r in r0] and r1[0]["tf"] == r0[0]["tf"]


def test_seam_is_exact_and_far_terrain_follows_the_dem(ext, world):
    d, before = world
    _run(ext, d)
    n, _, ex = ext.read_terrain((d / "output.a3d").read_bytes())
    by = {(int(r["x"]), int(r["y"])): r for r in ex}
    for y in range(4):
        assert (by[(-1, y)]["height"][:, 4] == by[(0, y)]["height"][:, 0]).all(), f"seam at y={y}"
    # far from the seam (blend 2 m = 5 vertices) the height is the DEM through the map's own bake, +-1 raw unit
    proj = ext.Projection(d / "terrain_metadata.json")
    dem = ext.Dem([d / "dem"])
    r = by[(-8, 2)]
    gx, gy = 8.0 * -8 + 2 * np.arange(5), 8.0 * 2 + 2 * np.arange(5)
    GX, GY = np.meshgrid(gx, gy)
    lon, lat = proj.lonlat_of_world(GX, GY)
    want = np.clip(np.rint(128 + (dem.sample(lon, lat) - 6.5) * proj.cs * 16), 0, 65535)
    assert np.abs(r["height"].astype(float) - want).max() <= 1.0


def test_materials_from_osm_and_flat_water(ext, world):
    d, _ = world
    _run(ext, d)
    n, _, ex = ext.read_terrain((d / "output.a3d").read_bytes())
    by = {(int(r["x"]), int(r["y"])): r for r in ex}

    def cell(u, v):  # world cell [u, u+1) x [v, v+1)
        r = by[(math.floor(u / 8), math.floor(v / 8))]
        return int(r["visual"][int(v) % 8, int(u) % 8])
    m = ext.parse_materials(None)
    assert cell(-30, 16) == m["water"]          # inside the natural=water polygon
    assert cell(-52, 4) == m["grass"]           # inside the leisure=park polygon
    assert cell(-52, 30) == m["road"]           # on the highway=service line (half width 1.75 m = 9 units)
    assert cell(-12, 30) == m["land"]           # nothing there
    # water vertices (all four neighbouring cells water) share one height per row: a flat surface
    for gy in range(3, 14):  # world y = 2 gy, polygon y 4..28
        row = []
        for gx in range(-19, -10):  # world x = 2 gx, polygon x -40..-20
            r = by[(math.floor(2 * gx / 8), math.floor(2 * gy / 8))]
            row.append(int(r["height"][(2 * gy % 8) // 2, (2 * gx % 8) // 2]))
        assert len(set(row)) == 1, (gy, row)
    # the high byte of every new cell is 0 like the rest of the map
    assert all(int((by[k]["visual"] >> 8).max()) == 0 for k in by if k[0] < 0)


def test_atomic_save_backup_lock_journal_and_bounds(ext, world):
    d, before = world
    _run(ext, d)
    sha8 = hashlib.sha256(before).hexdigest()[:8]
    keep = d / f"output.a3d.pre_edit_{sha8}"
    assert keep.read_bytes() == before
    assert (d / "output.a3d.lock").exists() and not (d / "output.a3d.saving").exists()
    rec = json.loads((d / "journal.jsonl").read_text().splitlines()[-1])
    assert rec["op"] == "terrain_extend" and rec["by"] == "pytest" and rec["basis"]
    assert rec["sha256_before"] == hashlib.sha256(before).hexdigest()
    assert rec["sha256_after"] == hashlib.sha256((d / "output.a3d").read_bytes()).hexdigest()
    assert len(rec["bbox_utm_EN"]) == 1 and rec["sources"]["dem"] and rec["sources"]["osm"]["sha256"]
    assert rec["edits"] == []  # a3d_edit.py replay iterates line["edits"]; this record must be inert there
    meta = json.loads((d / "terrain_metadata.json").read_text())
    assert meta["terrain_bounds"] == {"min_x": -64.0, "min_y": 0.0, "max_x": 32.0, "max_y": 32.0}
    for k in ("content_scale", "offset_x", "offset_y", "origin_easting", "origin_northing", "projection_kind"):
        assert meta[k] == META[k], f"projection field {k} moved"


def test_second_run_adds_nothing_and_dry_run_writes_nothing(ext, world):
    d, before = world
    _run(ext, d, dry_run=True)
    assert (d / "output.a3d").read_bytes() == before and not (d / "journal.jsonl").exists()
    _run(ext, d)
    once = (d / "output.a3d").read_bytes()
    _run(ext, d)
    assert (d / "output.a3d").read_bytes() == once
    assert len((d / "journal.jsonl").read_text().splitlines()) == 1


def test_editor_geometry_handles_negative_patch_coordinates(ext, world):
    d, _ = world
    _run(ext, d)
    ev = _load("fl4876_editor_views", TOOL_DIR / "editor_views.py")
    blob = ev.build_geometry(d)
    head = json.loads(blob[8:8 + struct.unpack_from("<I", blob, 4)[0]])
    assert head["origin_patch"] == [-8, 0] and head["patches"] == [12, 4]
    base = 8 + struct.unpack_from("<I", blob, 4)[0]
    s = head["sections"]["heights"]
    h = np.frombuffer(blob, np.uint16, s[1] // 2, base + s[0]).reshape(s[3])
    n, _, ex = ext.read_terrain((d / "output.a3d").read_bytes())
    r = next(r for r in ex if (int(r["x"]), int(r["y"])) == (-3, 2))
    # world vertex (2 i + 8 ox, 2 j + 8 oy): patch -3 starts at grid column 4 * (-3 - -8)
    assert (h[4 * 2:4 * 2 + 5, 4 * 5:4 * 5 + 5] == r["height"]).all()
