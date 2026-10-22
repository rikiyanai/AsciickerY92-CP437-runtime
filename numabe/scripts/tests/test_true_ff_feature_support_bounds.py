"""Offline support-bound regressions for the True FF feature-bank compiler.

These tests are rung-1 geometry evidence only.  They do not launch Godot or
claim that a live frame accepted the repaired hierarchy.
"""
from __future__ import annotations

import struct
from functools import lru_cache
from pathlib import Path
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
import sys

sys.path.insert(0, str(ROOT / "scripts"))
import true_ff_feature_bank as bank  # noqa: E402
import true_ff_feature_passes_ref as reference  # noqa: E402


PIETA = ROOT / "godot_project/assets/world_mesh/pieta_statue_mossy_faces.json"


@lru_cache(maxsize=1)
def _pieta_bytes():
    source = next(bank.faces_sources([PIETA]))
    built = bank.build_source(source)
    return bank.encode_file(built, [source["source"]])


def _decoded_geometry(data):
    decoded = bank.decode_file(data)
    features = bank._decoded_feature_records(decoded)
    faces = [tuple(int(value) for value in row[:3])
             for row in struct.iter_unpack("<4I", decoded["faces"])]
    # Independent oracle: walk each retained face, not the compiler's support
    # helper. A helper that forgets an incident vertex must fail this test.
    supports = [[feature.p0, feature.p1] for feature in features]
    for face in faces:
        points = [point for edge in face for point in (features[edge].p0, features[edge].p1)]
        for edge in face:
            if features[edge].cls == bank.ABI["CLASS_SMOOTH_CANDIDATE"]:
                supports[edge].extend(points)
    root, nodes, _rows = bank._decoded_node_tree(decoded)
    leaves = [node for node in nodes if not node.children]
    return decoded, features, faces, supports, root, nodes, leaves


def _contains(raw_node, point):
    distance = bank.norm(bank.sub(point, raw_node[0:3]))
    return distance <= raw_node[3] * (1.0 + 1e-6) + 1e-6


def test_pieta_decoded_smooth_incident_faces_are_inside_leaf_support_bounds():
    """Production Pieta bytes falsify endpoint-only leaf spheres."""
    data = _pieta_bytes()
    decoded, features, _faces, supports, _root, nodes, leaves = _decoded_geometry(data)
    raw_nodes = list(struct.iter_unpack("<4f4f4I4I", decoded["nodes"]))

    smooth = bank.ABI["CLASS_SMOOTH_CANDIDATE"]
    smooth_count = 0
    expanded_count = 0
    endpoint_only_violations = 0
    for node in nodes:
        for index in range(node.first, node.first + node.count):
            if features[index].cls == smooth:
                for point in supports[index]:
                    assert _contains(raw_nodes[node.index], point), (node.index, index, point)
    for leaf in leaves:
        endpoint_points = [point
                           for index in range(leaf.first, leaf.first + leaf.count)
                           for point in (features[index].p0, features[index].p1)]
        endpoint_node = bank.Node()
        endpoint_node.children = []
        legacy_sphere = bank._sphere_from_support(endpoint_node, lambda _node: endpoint_points)
        for index in range(leaf.first, leaf.first + leaf.count):
            if features[index].cls != smooth:
                continue
            smooth_count += 1
            if len(supports[index]) > 2:
                expanded_count += 1
            for point in supports[index]:
                assert _contains(raw_nodes[leaf.index], point), (leaf.index, index, point)
                if not _contains((legacy_sphere[0] + (legacy_sphere[1],)), point):
                    endpoint_only_violations += 1

    assert smooth_count > 0
    assert expanded_count > 0
    # Red-first witness: before the repair, real decoded Pieta geometry had
    # incident-face vertices outside endpoint-only leaf spheres.
    assert endpoint_only_violations > 0


def test_fixed_class_support_includes_incident_surface():
    # Closed-node representatives depict incident surface even for fixed
    # boundary classes, so their conservative bounds retain the whole face.
    corners = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)]
    face_rows = []
    for face in (corners, list(reversed(corners))):
        for index, point in enumerate(face + [face[-1]]):
            face_rows.extend([*point, 0.0 if index == 0 else 0.0])
    triangles = bank.triangles_from_rows(face_rows, 2)
    features, tri_edges, _normals = bank.mesh_features(triangles)
    supports = bank.feature_support_points(features, tri_edges)
    expanded_fixed = 0
    for feature, support in zip(features, supports):
        endpoint_keys = {bank._key(bank._stored_point(point)) for point in (feature.p0, feature.p1)}
        if feature.cls != bank.ABI["CLASS_SMOOTH_CANDIDATE"]:
            support_keys = {bank._key(point) for point in support}
            assert endpoint_keys <= support_keys
            expanded_fixed += support_keys != endpoint_keys
    assert expanded_fixed > 0


def test_pieta_incident_support_expands_at_least_one_node_bound():
    data = _pieta_bytes()
    decoded = bank.decode_file(data)
    stale = bank.decode_file(_endpoint_only_bytes(data))
    node_bytes = bank.ABI["NODE_BYTES"]
    count = len(decoded["nodes"]) // node_bytes
    current = [struct.unpack_from("<4f", decoded["nodes"], i * node_bytes) for i in range(count)]
    previous = [struct.unpack_from("<4f", stale["nodes"], i * node_bytes) for i in range(count)]
    assert any(a[3] > b[3] + 1e-6 for a, b in zip(current, previous))


def _endpoint_only_bytes(data):
    decoded = bank.decode_file(data)
    features = bank._decoded_feature_records(decoded)
    root, nodes, _rows = bank._decoded_node_tree(decoded)
    endpoints = {index: [bank._stored_point(feature.p0), bank._stored_point(feature.p1)]
                 for index, feature in enumerate(features)}

    def points_of(node):
        return [point for index in range(node.first, node.first + node.count)
                for point in endpoints[index]]

    bank._finish_spheres(root, points_of)
    stale = bytearray(data)
    node_offset = int(decoded["header"]["sections"]["nodes"]["offset"])
    for index, node in enumerate(nodes):
        centre, radius = node.sphere
        struct.pack_into("<4f", stale, node_offset + index * bank.ABI["NODE_BYTES"],
                         centre[0], centre[1], centre[2], radius)
    return bytes(stale)


def test_refit_bounds_is_equivalent_idempotent_and_sphere_only():
    fresh = _pieta_bytes()
    stale = _endpoint_only_bytes(fresh)
    assert stale != fresh

    repaired, facts = bank.refit_explicit_bounds(stale)
    assert facts["kind"] == "explicit"
    assert facts["changed"]
    assert repaired == fresh

    again, second_facts = bank.refit_explicit_bounds(repaired)
    assert again == repaired
    assert not second_facts["changed"]

    node_offset = int(bank.decode_file(fresh)["header"]["sections"]["nodes"]["offset"])
    for index in range(len(bank.decode_file(fresh)["nodes"]) // bank.ABI["NODE_BYTES"]):
        fresh_record = stale[node_offset + index * bank.ABI["NODE_BYTES"]:
                             node_offset + (index + 1) * bank.ABI["NODE_BYTES"]]
        repaired_record = repaired[node_offset + index * bank.ABI["NODE_BYTES"]:
                                   node_offset + (index + 1) * bank.ABI["NODE_BYTES"]]
        assert repaired_record[16:] == fresh_record[16:]
    before, after = bank.decode_file(stale), bank.decode_file(repaired)
    assert before["header"] == after["header"]
    assert before["features"] == after["features"]
    assert before["faces"] == after["faces"]
    assert len(stale) == len(repaired)


@pytest.mark.parametrize("fault", ["cycle", "orphan", "feature_range"])
def test_refit_rejects_malformed_node_tree(fault):
    data = bytearray(_pieta_bytes())
    offset = bank.decode_file(data)["header"]["sections"]["nodes"]["offset"]
    if fault == "cycle":
        struct.pack_into("<I", data, offset + 32, 0)
    elif fault == "orphan":
        struct.pack_into("<I", data, offset + 36, 0)
    else:
        struct.pack_into("<I", data, offset + 44, 0xffffffff)
    with pytest.raises(ValueError):
        bank.refit_explicit_bounds(bytes(data))


def test_refit_bounds_skips_implicit_terrain_byte_identically():
    heights = [0.0, 1.0, 2.0, 3.0]
    terrain, _root = bank.build_terrain_bank(heights, (1, 1), (0.0, 0.0), 1.0, 1.0)
    data = bank.encode_file(terrain, ["synthetic-terrain"])
    refit, facts = bank.refit_explicit_bounds(data)
    assert facts == {"kind": "terrain", "changed": False, "node_count": 0}
    assert refit == data


def test_refit_check_mode_reports_without_writing(tmp_path, capsys):
    fresh = _pieta_bytes()
    stale = _endpoint_only_bytes(fresh)
    path = tmp_path / "pieta.v1.bin"
    path.write_bytes(stale)
    assert bank.main(["refit-bounds", "--file", str(path), "--check"]) == 1
    assert path.read_bytes() == stale
    assert "mode=check" in capsys.readouterr().out
