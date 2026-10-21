"""Proof that the baked edge table carries adjacency the face rows lack.

FF-ACQ-20260920-01 plus FF-ACQ-04C. The bake emits one row per unique
undirected edge over the baked (transformed) corner positions beside the
face rows (`fl4359.edge_rows.v2`), plus run columns chaining welded
tangent-continuous edges. These tests prove, on mossy_rock (3,862 faces):

(a) every face's three edges appear exactly once with correct adjacency;
(b) open edges carry role 3 and the face_b sentinel (synthetic fixture --
    mossy_rock is a closed manifold, so the open case is proved on one and
    two hand-built triangles through fl4359_edge_table directly);
(c) row roles equal stone_edge_roles for the same edges.

FL-4512: the retired standalone renderer's smooth-vertex silhouette count
is not an oracle for this face-adjacency table. A similar count does not
establish matching contours. These tests check the bake's actual contract.
"""

import importlib.util
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BAKER_PATH = ROOT / "scripts/bake_akm_to_static_world_faces.py"

CASE = {
    "asset": ROOT / "godot_project/assets/world_mesh/mossy_rock_faces.json",
    "source": ROOT / "assets/meshes/incoming/mossy_rock.akm",
    "mode": "rock",
    "target_size": 2.4,
    "moss_threshold": None,
    "up_axis": "+y",
    "shade": 0.82,
}

FACE_B_OPEN = 0xFFFFFFFF

def _load_baker():
    spec = importlib.util.spec_from_file_location("static_face_baker", BAKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bake_tmp(baker, tmp_path):
    output = tmp_path / CASE["asset"].name
    baker.bake(
        str(CASE["source"]), str(output), CASE["target_size"], CASE["shade"],
        CASE["mode"], moss_threshold=CASE["moss_threshold"],
        up_axis=CASE["up_axis"], stone_topology=True,
    )
    return json.loads(output.read_text())


def _table_maps(doc, baker):
    floats = doc["edge_rows"]["floats"]
    uints = doc["edge_rows"]["uints"]
    run_uints = doc["edge_rows"]["run_uints"]
    count = doc["edge_table"]["count"]
    assert len(floats) == 16 * count
    assert len(uints) == 4 * count
    assert len(run_uints) == 2 * count
    assert doc["edge_table"]["schema"] == baker.EDGE_TABLE_SCHEMA
    assert doc["edge_table"]["run_columns"] == ["run_id", "order_index"]
    return floats, uints, run_uints, count


def test_face_rows_byte_identical(tmp_path):
    baker = _load_baker()
    shipped = json.loads(CASE["asset"].read_text())
    assert shipped["edge_table"]["schema"] == baker.EDGE_TABLE_SCHEMA
    assert "run_uints" in shipped["edge_rows"]
    regenerated = _bake_tmp(baker, tmp_path)
    assert regenerated["rows"] == shipped["rows"]
    assert regenerated["face_count"] == shipped["face_count"]
    assert regenerated["mats"] == shipped["mats"]
    assert regenerated["dimensions_xyz"] == shipped["dimensions_xyz"]
    assert regenerated["edge_rows"]["floats"] == shipped["edge_rows"]["floats"]
    assert regenerated["edge_rows"]["uints"] == shipped["edge_rows"]["uints"]
    assert regenerated["edge_rows"]["run_uints"] == shipped["edge_rows"]["run_uints"]


def test_edge_runs_cover_and_chain(tmp_path):
    baker = _load_baker()
    doc = _bake_tmp(baker, tmp_path)
    floats, _uints, run_uints, count = _table_maps(doc, baker)
    run_ids = run_uints[0::2]
    orders = run_uints[1::2]
    # Dense ids, every edge exactly once, consecutive orders per run.
    assert sorted(set(run_ids)) == list(range(max(run_ids) + 1))
    assert len(run_ids) == count
    seen = {}
    for edge, (rid, order) in enumerate(zip(run_ids, orders)):
        seen.setdefault(rid, []).append((order, edge))
    limit = math.cos(math.radians(baker.RUN_MAX_TURN_DEG))
    for rid, members in seen.items():
        by_order = {o: e for o, e in members}
        for o in range(len(members) - 1):
            a, b = by_order[o], by_order[o + 1]
            va0 = tuple(round(floats[a * 16 + k], 9) for k in range(3))
            va1 = tuple(round(floats[a * 16 + 4 + k], 9) for k in range(3))
            vb0 = tuple(round(floats[b * 16 + k], 9) for k in range(3))
            vb1 = tuple(round(floats[b * 16 + 4 + k], 9) for k in range(3))
            shared = [p for p in (va0, va1) if p in (vb0, vb1)]
            assert len(shared) == 1, (rid, o)
            da = (va1[0] - va0[0], va1[1] - va0[1], va1[2] - va0[2])
            db = (vb1[0] - vb0[0], vb1[1] - vb0[1], vb1[2] - vb0[2])
            la = math.sqrt(sum(x * x for x in da))
            lb = sum(x * x for x in db) ** 0.5
            assert la > 1e-12 and lb > 1e-12, (rid, o)
            # Directed turn: orient both away from the shared vertex.
            s = shared[0]
            oa = -1.0 if va0 == s else 1.0
            ob = -1.0 if vb0 == s else 1.0
            cos_turn = abs(sum((va1[k] - va0[k]) * oa * (vb1[k] - vb0[k]) * ob
                               for k in range(3))) / (la * lb)
            assert cos_turn >= limit - 1e-9, (rid, o, cos_turn)


def test_edge_runs_reject_partial_rows():
    baker = _load_baker()
    try:
        baker.fl4359_edge_runs([0.0] * 15)
    except ValueError as exc:
        assert "partial float record" in str(exc)
    else:
        raise AssertionError("partial edge rows must fail closed")


def test_every_face_edge_in_table_once(tmp_path):
    baker = _load_baker()
    doc = _bake_tmp(baker, tmp_path)
    floats, uints, run_uints, count = _table_maps(doc, baker)
    rows = doc["rows"]
    face_count = doc["face_count"]
    assert doc["edge_table"]["non_manifold_edges"] == 0
    assert doc["edge_table"]["degenerate_edges"] == 0
    # Index rows by welded endpoint key; each row must own exactly its faces.
    by_key = {}
    for e in range(count):
        a = (floats[e * 16], floats[e * 16 + 1], floats[e * 16 + 2])
        b = (floats[e * 16 + 4], floats[e * 16 + 5], floats[e * 16 + 6])
        key = tuple(sorted(tuple(round(x, 9) for x in p) for p in (a, b)))
        by_key.setdefault(key, []).append(e)
    assert all(len(v) == 1 for v in by_key.values())
    seen = {}
    for face in range(face_count):
        corners = [(rows[face * 16 + c * 4], rows[face * 16 + c * 4 + 1],
                    rows[face * 16 + c * 4 + 2]) for c in range(3)]
        for local in range(3):
            a, b = corners[local], corners[(local + 1) % 3]
            key = tuple(sorted(tuple(round(x, 9) for x in p) for p in (a, b)))
            assert key in by_key, (face, local)
            row = by_key[key][0]
            face_a, face_b = uints[row * 4], uints[row * 4 + 1]
            assert face in (face_a, face_b), (face, local, row)
            seen[(face, local)] = row
    assert len(seen) == 3 * face_count
    # Adjacency is exact: each row's faces share its stored endpoints.
    for e in range(count):
        a = (floats[e * 16], floats[e * 16 + 1], floats[e * 16 + 2])
        b = (floats[e * 16 + 4], floats[e * 16 + 5], floats[e * 16 + 6])
        for f in (uints[e * 4], uints[e * 4 + 1]):
            if f == FACE_B_OPEN:
                continue
            corners = [(rows[f * 16 + c * 4], rows[f * 16 + c * 4 + 1],
                        rows[f * 16 + c * 4 + 2]) for c in range(3)]
            flat = [tuple(round(x, 9) for x in p) for p in corners]
            assert tuple(round(x, 9) for x in a) in flat
            assert tuple(round(x, 9) for x in b) in flat


def test_open_edges_carry_role3_and_sentinel():
    baker = _load_baker()
    # One free triangle: three open rows, role 3, face_b sentinel, zero n_b.
    corners = [((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))]
    floats, uints, stats = baker.fl4359_edge_table(corners, [210], [0])
    assert stats == {"edges": 3, "open": 3, "non_manifold": 0, "degenerate": 0}
    assert uints[1::4] == [FACE_B_OPEN] * 3
    assert uints[2::4] == [0] * 3  # roles arg was 0; sentinel + zero normal hold
    assert uints[0::4] == [0] * 3
    for e in range(3):
        assert (floats[e * 16 + 12], floats[e * 16 + 13], floats[e * 16 + 14]) == (0.0, 0.0, 0.0)
    # Two triangles sharing one edge: the shared row names both faces.
    corners = [((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
               ((1.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 0.0))]
    floats, uints, stats = baker.fl4359_edge_table(corners, [210, 211], [0, 0])
    assert stats["edges"] == 5
    assert stats["open"] == 4
    shared = [e for e in range(5) if uints[e * 4 + 1] != FACE_B_OPEN]
    assert len(shared) == 1
    assert (uints[shared[0] * 4], uints[shared[0] * 4 + 1]) == (0, 1)
    # Open edges of a lone triangle whose role word says boundary.
    floats, uints, stats = baker.fl4359_edge_table(
        [((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))], [210], [0b111111])
    assert uints[2::4] == [3] * 3


def test_roles_equal_stone_edge_roles(tmp_path):
    baker = _load_baker()
    doc = _bake_tmp(baker, tmp_path)
    floats, uints, run_uints, count = _table_maps(doc, baker)
    rows = doc["rows"]
    verts, faces = baker.parse_akm_ply(str(CASE["source"]))
    # Roles are computed on source verts; the bake transform is rigid plus
    # uniform scale, so (face, local-edge) identity is transform-invariant.
    expected = baker.stone_edge_roles(verts, faces)
    assert len(expected) == doc["face_count"]
    for e in range(count):
        face_a = uints[e * 4]
        a = (floats[e * 16], floats[e * 16 + 1], floats[e * 16 + 2])
        b = (floats[e * 16 + 4], floats[e * 16 + 5], floats[e * 16 + 6])
        corners = [(rows[face_a * 16 + c * 4], rows[face_a * 16 + c * 4 + 1],
                    rows[face_a * 16 + c * 4 + 2]) for c in range(3)]
        local = None
        for k in range(3):
            pair = {tuple(round(x, 9) for x in corners[k]),
                    tuple(round(x, 9) for x in corners[(k + 1) % 3])}
            if pair == {tuple(round(x, 9) for x in a), tuple(round(x, 9) for x in b)}:
                local = k
                break
        assert local is not None
        assert uints[e * 4 + 2] == (expected[face_a] >> (local * 2)) & 3
