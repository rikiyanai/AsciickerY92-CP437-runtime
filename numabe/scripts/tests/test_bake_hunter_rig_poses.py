#!/usr/bin/env python3
"""Contract checks for the immutable FL-4407 HunterRig offline product."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "godot_project" / "assets" / "actor_mesh" / "hunter_rig_poses.json"
RIG = ROOT / "godot_project" / "player" / "cat_soulslike" / "HunterRig.tscn"
EXPECTED_CLIPS = (
    "idle",
    "locomotion",
    "attack",
    "hurt_posture_break",
    "death_deathblow",
)


class HunterRigPoseBakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(PRODUCT.read_text(encoding="utf-8"))

    def test_schema_and_donor_provenance_are_pinned(self) -> None:
        self.assertEqual(self.payload["schema"], "asciicker.actor_mesh.hunter_rig_animation_rows.v2")
        provenance = self.payload["provenance"]
        self.assertEqual(provenance["donor_pin"], "d8bceffc5bf4afe585a3a926fd9aa60ebd26e001")
        self.assertEqual(provenance["rig_sha256"], hashlib.sha256(RIG.read_bytes()).hexdigest())
        self.assertEqual(provenance["license"], "MIT")
        self.assertTrue((ROOT / provenance["license_path"]).is_file())
        self.assertEqual(self.payload["compiler"]["generation"], "offline_rendering_context_only")

    def test_required_animation_clips_and_pinned_root_policy_exist(self) -> None:
        self.assertEqual(tuple(self.payload["animation_clips"]), EXPECTED_CLIPS)
        self.assertGreater(len(self.payload["poses"]), len(EXPECTED_CLIPS))
        self.assertIn("Server owns runtime actor transform", self.payload["geometry_contract"]["root_motion_policy"])
        # FL-4407: the silhouette budget is one elongated pyramid per anatomical
        # body (torso, hip, head, two arms, two legs) at 4 side walls + a
        # 2-triangle quad base. The predecessor emitted one double-capped frustum
        # per skeleton joint — 24 bodies x 16 triangles = 384 — where both end
        # fans of every body were interior at the joint and unreachable by any
        # camera ray.
        contract = self.payload["geometry_contract"]
        self.assertEqual(contract["construction"], "one_elongated_pyramid_per_anatomical_body")
        self.assertEqual(contract["segment_side_count"], 4)
        self.assertEqual(contract["triangles_per_body"], 6)
        self.assertEqual(contract["body_count"], 7)
        for pose in self.payload["poses"]:
            self.assertEqual(
                pose["face_count"], contract["body_count"] * contract["triangles_per_body"]
            )

    def test_presentation_scale_keeps_the_rig_on_the_gameplay_capsule(self) -> None:
        # The authoritative body is CapsuleShape3D height 1.8 in
        # godot_project/scenes/Player.tscn. A neutral standing pose must present
        # at that height: PRESENTATION_SCALE 2.0 previously produced a 3.34-tall
        # silhouette, which is what forced the 12 px glyph cell.
        idle = next(p for p in self.payload["poses"] if p["pose_id"] == "idle_frame_000")
        self.assertAlmostEqual(idle["local_aabb_min"][1], 0.0, places=4)
        self.assertAlmostEqual(idle["local_aabb_max"][1], 1.8, delta=0.05)

    def test_product_owns_clip_and_action_alias_selection(self) -> None:
        self.assertEqual(self.payload["clip_aliases"], {
            "idle": "idle", "locomotion": "locomotion", "attack": "attack",
            "block": "idle", "deflect": "idle",
            "hurt": "hurt_posture_break", "posture_break": "hurt_posture_break",
            "death": "death_deathblow", "deathblow": "death_deathblow",
        })
        self.assertEqual(self.payload["action_clip_aliases"], {
            "idle": "idle", "move": "locomotion", "dash": "locomotion",
            "dash_attack": "attack", "attack": "attack",
            "block": "idle", "deflected": "idle",
            # "hurt" is the server's hit reaction, held for the knockback window
            # (colyseus_server/src/sim/world.ts applyKnockback). It must resolve,
            # because ActorPoseGeometryDB rejects the WHOLE instance for a
            # selector the product does not carry -- an unaliased state does not
            # fall back to idle, it makes the actor disappear.
            "hurt": "hurt_posture_break",
            "guard_break": "hurt_posture_break", "deathblow": "death_deathblow",
            "dead": "death_deathblow",
        })
        self.assertEqual(self.payload["proxy_policy"]["block"], "idle")

    def test_every_server_action_state_resolves_to_a_baked_clip(self) -> None:
        # The server's ActionState union is the whole input domain of the action
        # alias family. Anything it can publish and this product cannot name is a
        # vanished actor at runtime, so the two are pinned together here rather
        # than discovered in a session.
        server_action_states = {
            "idle", "move", "attack", "dash", "dash_attack",
            "block", "deflected", "hurt", "guard_break", "dead",
        }
        aliases = self.payload["action_clip_aliases"]
        self.assertEqual(server_action_states - set(aliases), set())
        for state in sorted(server_action_states):
            self.assertIn(aliases[state], self.payload["animation_clips"])

    def test_each_pose_has_closed_articulated_compositor_rows(self) -> None:
        source_count = self.payload["geometry_contract"]["source_triangle_count_per_pose"]
        for pose in self.payload["poses"]:
            self.assertEqual(pose["source_triangle_count"], source_count)
            self.assertGreater(pose["face_count"], 0)
            self.assertLess(pose["face_count"], source_count)
            self.assertEqual(len(pose["face_rows"]), pose["face_count"] * 16)
            self.assertEqual(len(pose["local_aabb_min"]), 3)
            self.assertEqual(len(pose["local_aabb_max"]), 3)
            self.assertAlmostEqual(float(pose["local_aabb_min"][1]), 0.0, places=6)
            self.assertIn("donor_pin", pose["provenance"])
            self.assertEqual(len(pose["content_sha256"]), 64)
            for index, value in enumerate(pose["face_rows"]):
                self.assertIsInstance(value, (int, float))
                if index % 16 == 15:
                    # FL-4512 G15: lane 15 is the rig part word (bone * 8192 +
                    # part-local ordinal), not a coordinate.
                    self.assertEqual(value, int(value))
                    self.assertTrue(0 <= value < (1 << 20))
                else:
                    self.assertLess(abs(value), 1000.0)
            materials = pose["face_rows"][3::16]
            self.assertEqual(set(materials), {237})
            self.assertTrue(all(0 <= material <= 255 for material in materials))

    def test_every_clip_contains_multiple_distinct_animation_frames(self) -> None:
        poses = {pose["pose_id"]: pose for pose in self.payload["poses"]}

        def geometry_digest(pose: dict) -> str:
            canonical = f"{pose['face_count']}\n"
            canonical += "".join(f"{float(value):.6f}\n" for value in pose["face_rows"])
            return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        for clip_id, clip in self.payload["animation_clips"].items():
            frame_ids = clip["frame_pose_ids"]
            self.assertGreaterEqual(len(frame_ids), 2)
            self.assertEqual(clip["clip_id"], clip_id)
            self.assertEqual(float(clip["sample_fps"]), 10.0)
            self.assertGreaterEqual(
                len({geometry_digest(poses[pose_id]) for pose_id in frame_ids}), 2
            )
            for frame_index, pose_id in enumerate(frame_ids):
                self.assertEqual(poses[pose_id]["clip_id"], clip_id)
                self.assertEqual(poses[pose_id]["frame_index"], frame_index)

    def test_content_hash_matches_canonical_pose_rows(self) -> None:
        canonical = ""
        for alias, target in self.payload["clip_aliases"].items():
            canonical += f"clip_alias|{alias}|{target}\n"
        for action_state, target in self.payload["action_clip_aliases"].items():
            canonical += f"action_alias|{action_state}|{target}\n"
        for clip_id, clip in self.payload["animation_clips"].items():
            canonical += (
                f"clip|{clip_id}|{clip['source_animation']}|"
                f"{float(clip['sample_fps']):.6f}|"
                f"{float(clip['source_length_seconds']):.6f}|"
                f"{str(bool(clip['loop'])).lower()}\n"
            )
            for pose_id in clip["frame_pose_ids"]:
                canonical += f"frame|{pose_id}\n"
        for pose in self.payload["poses"]:
            canonical += f"{pose['pose_id']}|{pose['face_count']}\n"
            canonical += "".join(f"{float(value):.6f}\n" for value in pose["face_rows"])
        self.assertEqual(self.payload["content_sha256"], hashlib.sha256(canonical.encode("utf-8")).hexdigest())


if __name__ == "__main__":
    unittest.main()
