"""FL-4876 MAP-EDIT-01: a3d_edit.py edits single instances of a built map and saves it without a rebuild."""
import importlib.util
import hashlib
import json
import math
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("a3d_edit", ROOT / "artifacts/experiments/fl4876_manual_world/a3d_edit.py")
ED = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ED)
META = {"cs": 5.0, "E0": 1000.0, "N0": 2000.0, "ox": 48.0, "oy": 48.0}


def _map(version=3):
    header = b"AS3D" + struct.pack("<III", 16, 0, 0)
    body = bytes(ED.MATERIALS)
    recs = [{"kind": "mesh", "mesh": "a.akm", "inst": "one",
             "tf": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 16, 0, 100.0, 200.0, 300.0, 1], "flags": 3, "story": -1},
            {"kind": "mesh", "mesh": "b.akm", "inst": "two",
             "tf": [0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 16, 0, 50.0, 60.0, 70.0, 1], "flags": 3, "story": -1}]
    return ED.serialise(header + body, version, recs, b"TAIL-BYTES")


def test_round_trip_is_byte_identical():
    data = _map()
    assert ED.serialise(*ED.parse(data)) == data


def test_move_face_and_delete_change_only_their_instance():
    prefix, v, recs, tail = ED.parse(_map())
    ED.apply_edit(recs[0], {"op": "move", "dE_m": 2.0, "dN_m": -1.0}, META, Path("."))
    assert recs[0]["tf"][12:14] == pytest.approx([110.0, 195.0])
    ED.apply_edit(recs[0], {"op": "face", "bearing_deg": 90.0}, META, Path("."))
    assert ED.facing_bearing(recs[0]["tf"]) == pytest.approx(90.0)
    assert recs[0]["tf"][10] == pytest.approx(16.0)  # height scale untouched by a yaw
    out = ED.parse(ED.serialise(prefix, v, [recs[0]], tail))
    assert [r["inst"] for r in out[2]] == ["one"] and out[3] == b"TAIL-BYTES"


def test_to_puts_the_origin_at_metres():
    _, _, recs, _ = ED.parse(_map())
    ED.apply_edit(recs[1], {"op": "to", "E": 1004.0, "N": 2002.0}, META, Path("."))
    assert ED.to_en(META, recs[1]["tf"][12], recs[1]["tf"][13]) == pytest.approx((1004.0, 2002.0))


def test_scale_multiplies_only_the_named_axis():
    _, _, recs, _ = ED.parse(_map())
    ED.apply_edit(recs[0], {"op": "scale", "z": 0.5}, META, Path("."))
    assert recs[0]["tf"][10] == pytest.approx(8.0) and recs[0]["tf"][0] == pytest.approx(1.0)


def test_add_and_delete_through_apply(tmp_path, monkeypatch):
    (tmp_path / "meshes").mkdir()
    (tmp_path / "meshes" / "c.akm").write_text("ply\n")
    (tmp_path / "terrain_metadata.json").write_text(
        '{"content_scale": 5.0, "origin_easting": 1000.0, "origin_northing": 2000.0, "offset_x": 48.0, "offset_y": 48.0}')
    m = tmp_path / "output.a3d"
    m.write_bytes(_map())
    edits = tmp_path / "e.json"
    edits.write_text('[{"inst": "new", "op": "add", "mesh": "c.akm", "E": 1002, "N": 2001, "z_world": 10,'
                     ' "bearing_deg": 90, "basis": "test", "by": "test"},'
                     ' {"inst": "two", "op": "delete", "basis": "test", "by": "test"}]')
    monkeypatch.setattr(ED, "JOURNAL", tmp_path / "journal.jsonl")
    ED.main(["apply", str(m), str(edits)])
    recs = {r["inst"]: r for r in ED.parse(m.read_bytes())[2]}
    assert set(recs) == {"one", "new"}
    assert ED.facing_bearing(recs["new"]["tf"]) == pytest.approx(90.0)
    assert ED.to_en(META, recs["new"]["tf"][12], recs["new"]["tf"][13]) == pytest.approx((1002.0, 2001.0))
    receipt = json.loads(ED.JOURNAL.read_text())
    assert receipt["sha256_before"] == hashlib.sha256(_map()).hexdigest()
    assert receipt["sha256_after"] == hashlib.sha256(m.read_bytes()).hexdigest()
    assert Path(receipt["kept"]).read_bytes() == _map()


def test_pre_edit_returns_the_retained_path(tmp_path):
    m = tmp_path / "output.a3d"
    original = _map()
    sha = hashlib.sha256(original).hexdigest()
    backup = ED.keep_pre_edit(m, original, sha)
    assert backup == m.with_name(m.name + ".pre_edit_" + sha[:8])
    assert backup.read_bytes() == original


def test_clear_span_saves_and_journals_the_real_backup(tmp_path, monkeypatch):
    """Exercise the fifth save caller, not only its shared backup helper."""
    import numpy as np

    spec = importlib.util.spec_from_file_location(
        "fl4876_clear_span_test", ROOT / "artifacts/experiments/fl4876_manual_world/a3d_terrain_clear_span.py")
    clear = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(clear)
    patch = np.zeros(1, dtype=clear.REC)
    patch["visual"] = clear.WATER
    patch["visual"][0, 3, :] = clear.ROAD
    patch["height"] = 100
    tail = bytes(ED.MATERIALS) + b"preserved-instance-tail"
    before = b"AS3D" + struct.pack("<III", 16, 1, 0) + patch.tobytes() + tail
    path = tmp_path / "output.a3d"
    path.write_bytes(before)
    (tmp_path / "terrain_metadata.json").write_text(json.dumps({
        "content_scale": 1, "origin_easting": 1000, "origin_northing": 2000,
        "offset_x": 0, "offset_y": 0}))
    monkeypatch.setattr(clear, "JOURNAL", tmp_path / "journal.jsonl")
    monkeypatch.setattr(clear, "way_axis", lambda *args: ((1000, 2000), (1008, 2000)))
    args = ["clear", str(path), "--way", "fixture", "--nodes", "0", "1",
            "--s0", "0", "--s1", "8", "--lat0", "0", "--lat1", "8",
            "--by", "pytest", "--basis", "synthetic road strip bordered by water"]
    assert clear.main(args + ["--dry-run"]) == 0
    assert path.read_bytes() == before and not clear.JOURNAL.exists()
    assert clear.main(args) == 0
    after = path.read_bytes()
    assert after != before and after[16 + clear.PATCH_BYTES:] == tail
    receipt = json.loads(clear.JOURNAL.read_text())
    assert receipt["cells_to_water"] == 8
    assert receipt["sha256_before"] == hashlib.sha256(before).hexdigest()
    assert receipt["sha256_after"] == hashlib.sha256(after).hexdigest()
    assert Path(receipt["kept"]).read_bytes() == before


def test_current_owned_map_round_trips():
    m = ROOT / ".run/fl4876_builds/live_numabe/output.a3d"
    assert m.is_file(), "The migrated workspace must retain its current edited map"
    data = m.read_bytes()
    assert ED.serialise(*ED.parse(data)) == data
