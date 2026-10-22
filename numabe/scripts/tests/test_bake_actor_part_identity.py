#!/usr/bin/env python3
"""Actor rig part identity written by the actor bake scripts.

FL-4907 XBOT_52_BONE_OCTAHEDRON_RIG_20261002: the X Bot compiler emits one
octahedron per deforming bone (52 parts, 416 triangles) and per-frame bone
transforms; part identity is the deforming-bone index. Its pure geometry
helpers are loaded here with stub bpy/mathutils modules, and the compiled
products are read directly. The HunterRig compiler keeps the FL-4512 G15
row part word. No Blender, no Godot.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
XBOT = ROOT / "scripts" / "bake_xbot_rig_poses.py"
HUNTER = ROOT / "scripts" / "bake_hunter_rig_poses.gd"
PRODUCT = ROOT / "godot_project" / "assets" / "actor_mesh" / "xbot_rig_poses.json"
MESH = ROOT / "godot_project" / "assets" / "world_mesh" / "xbot_rig_rigid_mesh.json"


def _load_xbot():
    stubs = {"bpy": types.ModuleType("bpy"), "mathutils": types.ModuleType("mathutils")}
    for name in ("Vector", "Matrix", "Euler"):
        setattr(stubs["mathutils"], name, object)
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("bake_xbot_rig_poses_under_test", XBOT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


def _sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


class ActorPartIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.xbot = _load_xbot()
        cls.product = json.loads(PRODUCT.read_text(encoding="utf-8"))
        cls.mesh = json.loads(MESH.read_text(encoding="utf-8"))

    def test_standard_rig_full_fingers_is_52_deforming_bones(self) -> None:
        identity = self.product["part_identity"]
        self.assertEqual(len(identity["bones"]), 52)
        self.assertEqual(len(identity["leaf_markers_excluded"]), 13)
        self.assertTrue(all(self.xbot.LEAF_MARKER.search(name) for name in identity["leaf_markers_excluded"]))
        self.assertFalse(any(self.xbot.LEAF_MARKER.search(name) for name in identity["bones"]))

    def test_octahedron_is_closed_and_wound_outward(self) -> None:
        faces = self.xbot.OCTAHEDRON_FACES
        self.assertEqual(len(faces), 8)
        edges = {}
        for a, b, c in faces:
            for edge in ((a, b), (b, c), (c, a)):
                edges[edge] = edges.get(edge, 0) + 1
        # Every directed edge appears once and its reverse once: a closed,
        # consistently wound surface.
        self.assertTrue(all(count == 1 and edges.get((v, u)) == 1 for (u, v), count in edges.items()))
        vertices = self.xbot.octahedron(0.01, -0.02, -0.1, 0.4, 0.15, 0.06, 0.05)
        centre = (0.01, 0.15, -0.02)
        for a, b, c in faces:
            pa, pb, pc = vertices[a], vertices[b], vertices[c]
            normal = _cross(_sub(pb, pa), _sub(pc, pa))
            middle = tuple((x + y + z) / 3.0 for x, y, z in zip(pa, pb, pc))
            self.assertGreater(_dot(normal, _sub(middle, centre)), 0.0)

    def test_rig_mesh_is_416_triangles_eight_per_bone_part(self) -> None:
        rows, parts = self.mesh["face_rows"], self.mesh["part_indices"]
        self.assertEqual(self.mesh["face_count"], 416)
        self.assertEqual(len(rows), 416 * 16)
        self.assertEqual(sorted(set(parts)), list(range(52)))
        self.assertTrue(all(parts.count(part) == 8 for part in range(52)))
        for face, part in enumerate(parts):
            row = rows[face * 16:(face + 1) * 16]
            self.assertEqual(row[11], float(part))
            self.assertEqual(row[8:11], row[12:15])
            self.assertGreater(sum(v * v for v in _cross(_sub(row[4:7], row[0:3]), _sub(row[8:11], row[0:3]))), 0.0)

    def test_pose_frames_are_part_transforms_not_face_rows(self) -> None:
        self.assertEqual(self.product["part_count"], 52)
        self.assertEqual(self.product["face_count"], 416)
        stride = 52 * 12
        for pose in self.product["poses"]:
            self.assertNotIn("face_count", pose)
            self.assertEqual(pose["transform_offset"] % stride, 0)

    def test_hunter_keeps_its_row_part_word_lane(self) -> None:
        source = HUNTER.read_text(encoding="utf-8")
        lane = int(re.search(r"const PART_WORD_LANE := (\d+)", source).group(1))
        local = int(re.search(r"const PART_LOCAL_LIMIT := (\d+)", source).group(1))
        # Lane 15 is row 3 w: free in the triangle-as-quad row contract.
        self.assertEqual(lane, 15)
        self.assertEqual(local, 8192)


if __name__ == "__main__":
    unittest.main()
