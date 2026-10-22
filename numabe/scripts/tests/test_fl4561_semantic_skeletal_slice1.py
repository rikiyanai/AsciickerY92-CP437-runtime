#!/usr/bin/env python3
"""Focused contracts for FL-4561 semantic-skeletal experiment slice 1."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import inspect
import io
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "scripts" / "fl4547_skeleton_walk.py"
SPEC = importlib.util.spec_from_file_location("fl4547_skeleton_walk", MODULE_PATH)
assert SPEC and SPEC.loader
rig = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rig)

VIEWER_PATH = REPO / "scripts" / "fl4547_walk_viewer.py"
VIEWER_SPEC = importlib.util.spec_from_file_location("fl4547_walk_viewer", VIEWER_PATH)
assert VIEWER_SPEC and VIEWER_SPEC.loader
viewer = importlib.util.module_from_spec(VIEWER_SPEC)
VIEWER_SPEC.loader.exec_module(viewer)


class SemanticSkeletalSlice1Test(unittest.TestCase):
    def test_semantic_segment_cells_obey_the_five_row_budget(self) -> None:
        self.assertEqual(rig.semantic_placement_cell(
            "head_axis", "head", "S", (2.4, 1.1), (2.5, 0.4)), [2, 0])
        self.assertEqual(rig.semantic_placement_cell(
            "torso_upper", "torso", "S", (2.5, 2.2), (2.5, 1.2)), [2, 1])
        self.assertEqual(rig.semantic_placement_cell(
            "torso_lower", "torso", "S", (2.5, 3.05), (2.5, 2.2)), [2, 2])
        self.assertEqual(rig.semantic_placement_cell(
            "upper_arm_a", "arm_a", "S", (2.35, 1.2), (2.15, 2.1)), [1, 1])
        self.assertEqual(rig.semantic_placement_cell(
            "shin_a", "leg_a", "S", (1.8, 3.5), (1.7, 4.4)), [1, 4])
        self.assertEqual(rig.semantic_placement_cell(
            "shin_a", "leg_a", "S", (1.8, 3.3), (1.7, 3.8)), [1, 3])

    def test_fixed_projection_pins_pelvis_and_retains_offset_sign(self) -> None:
        source_points = {
            "head": (0.0, -30.0), "neck": (0.0, -22.0),
            "spine": (0.0, -12.0), "pelvis": (0.0, 0.0),
            "shoulder_a": (-8.0, -20.0), "elbow_a": (-10.0, -10.0),
            "wrist_a": (-11.0, -1.0), "shoulder_b": (8.0, -18.0),
            "elbow_b": (10.0, -8.0), "wrist_b": (11.0, 1.0),
            "hip_a": (-5.0, 1.0), "knee_a": (-6.0, 18.0),
            "ankle_a": (-7.0, 35.0), "hip_b": (5.0, 2.0),
            "knee_b": (6.0, 16.0), "ankle_b": (7.0, 30.0),
        }
        record = {
            "frame_index": 0,
            "source_frame_sha256": "synthetic",
            "joints": {
                joint_id: rig._joint_record(joint_id, xy, "visible", "test")
                for joint_id, xy in source_points.items()
            },
            "topology_events": [],
        }
        scales = {"x": 0.1, "y_above_pelvis": 0.08, "y_below_pelvis": 0.04}
        projected = rig.project_rig_record(record, "SE", scales)
        self.assertEqual(projected["joints"]["pelvis"]["grid_xy"], list(rig.ROOT_TARGET))
        self.assertEqual(projected["pelvis_anchor_error"], 0.0)
        self.assertLess(projected["projected_offsets"]["shoulder_a_minus_b_y"], 0.0)
        self.assertLess(projected["projected_offsets"]["hip_a_minus_b_y"], 0.0)
        self.assertEqual(projected["occupancy_rows"]["head"], [0])
        self.assertEqual(projected["occupancy_rows"]["torso"], [1, 2])

    def test_fixture_produces_complete_persistent_tracks(self) -> None:
        if not rig.SOURCE_ROOT.exists():
            self.skipTest("local colour-keyed fixture is absent")
        proposals, _hashes = rig.load_rig_proposals()
        self.assertEqual(set(proposals), set(rig.DISPLAY_DIRS))
        self.assertTrue(all(len(records) == 18 for records in proposals.values()))
        self.assertTrue(all(
            set(record["joints"]) == set(rig.JOINT_META)
            for records in proposals.values() for record in records))

        scales = rig.compute_projection_scales(proposals)
        projected = {
            direction: [rig.project_rig_record(record, direction, scales)
                        for record in proposals[direction]]
            for direction in rig.DISPLAY_DIRS
        }
        summary = rig.summarize_rig(projected, scales)
        self.assertEqual(summary["source_frame_count"], 144)
        self.assertEqual(summary["joint_record_count"], 2304)
        self.assertEqual(summary["frame_dependent_scale_count"], 0)
        self.assertEqual(summary["pelvis_anchor_max_error"], 0.0)
        self.assertEqual(summary["clipped_joint_count"], 0)
        for joint_id in ("head", "neck", "spine"):
            metrics = summary["stabilization"][joint_id]
            self.assertLess(
                metrics["stabilized_acceleration_rms_mean"],
                metrics["raw_acceleration_rms_mean"],
            )
            self.assertLessEqual(
                metrics["stabilized_loop_jump_mean"],
                metrics["raw_loop_jump_mean"],
            )
        self.assertEqual(summary["occupancy_row_count_histogram"]["head"], {"1": 144})
        self.assertEqual(summary["occupancy_row_count_histogram"]["torso"], {"2": 144})
        for part in ("arm_a", "arm_b", "leg_a", "leg_b"):
            self.assertLessEqual(max(map(int, summary["occupancy_row_count_histogram"][part])), 2)

    def test_legacy_asciify_owner_fails_closed_without_explicit_gate(self) -> None:
        self.assertTrue(hasattr(rig, "cmd_asciify"))
        with self.assertRaisesRegex(SystemExit, "legacy asciify placement is gated"):
            rig.cmd_asciify(argparse.Namespace(allow_legacy_placement=False))
        main_source = inspect.getsource(rig.main)
        self.assertIn('"asciify"', main_source)
        self.assertIn("--allow-legacy-placement", main_source)
        self.assertIn('"unicode-skeletal"', main_source)

    def test_rejected_single_cell_unicode_owner_fails_closed(self) -> None:
        with self.assertRaisesRegex(SystemExit, "rejected unicode-skeletal compiler is gated"):
            rig.cmd_unicode_skeletal(argparse.Namespace(allow_rejected_rig_compiler=False))
        main_source = inspect.getsource(rig.main)
        self.assertIn("--allow-rejected-rig-compiler", main_source)
        self.assertIn('"unicode-perpart-skeleton"', main_source)

    def test_accepted_perpart_checkpoint_projects_all_crossed_cells(self) -> None:
        if not rig.SOURCE_ROOT.exists():
            self.skipTest("local colour-keyed fixture is absent")
        skeletons, _hashes, _bboxes = rig.load_accepted_perpart_skeletons()
        record = rig.project_perpart_frame(skeletons["S"][0])
        self.assertEqual(len(record["target_masks"]), 35)
        self.assertGreaterEqual(record["occupied_cell_count"], 8)
        observed_parts = {
            part for owners in record["cell_owners"] for part in owners
        }
        self.assertEqual(observed_parts, set(rig.PART_BY_RGB.values()))
        self.assertLessEqual(
            record["components_after_bridging"], record["components_before_bridging"]
        )

    def test_rejected_perpart_cell_compiler_fails_closed(self) -> None:
        with self.assertRaisesRegex(SystemExit, "rejected per-part cell compiler is gated"):
            rig.cmd_unicode_perpart_skeleton(
                argparse.Namespace(allow_rejected_cell_compiler=False)
            )
        self.assertIn("--allow-rejected-cell-compiler", inspect.getsource(rig.main))

    def test_combined_viewer_inventory_lists_the_rig_slice(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(viewer.cmd_list(argparse.Namespace(stage="rig")), 0)
        text = output.getvalue()
        self.assertIn("rig-bones-5x5", text)
        self.assertIn("v20260717T152157__rig-bones-5x5__walk_all_directions", text)
        self.assertIn("rig-bones-5x5-stabilized", text)
        self.assertIn("unicode-skeletal", text)

    def test_unicode_skeletal_manifest_is_terminal_playable_and_emoji_free(self) -> None:
        entries = [
            entry for entry in viewer.read_playable_registry()
            if entry.get("kind") == "unicode-skeletal"
        ]
        self.assertTrue(entries)
        latest = entries[-1]
        manifest = __import__("json").loads((REPO / latest["tracked_frames"]).read_text())
        self.assertEqual(manifest["schema"], "fl4561.unicode_skeletal_walk.v1")
        self.assertEqual(manifest["summary"]["emoji_selection_count"], 0)
        self.assertEqual(manifest["frames_per_direction"], 18)
        emoji = rig.load_emoji_codepoints()
        selected_scalars = {
            cell["unicode_scalar"]
            for frames in manifest["frames"].values()
            for frame in frames
            for cell in frame["cells"]
        }
        self.assertTrue(selected_scalars.isdisjoint(emoji))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(viewer.cmd_play(argparse.Namespace(
                version=latest["name"], montage=True, interval=0.01,
            )), 0)
        self.assertIn("109725 eligible; emoji excluded", output.getvalue())

    def test_perpart_retry_is_tracked_playable_and_emoji_free(self) -> None:
        entries = [
            entry for entry in viewer.read_playable_registry()
            if entry.get("kind") == "unicode-perpart-skeleton"
        ]
        self.assertTrue(entries)
        latest = entries[-1]
        manifest = __import__("json").loads((REPO / latest["tracked_frames"]).read_text())
        self.assertEqual(manifest["schema"], "fl4561.unicode_perpart_skeleton.v1")
        self.assertEqual(manifest["accepted_checkpoint"]["sha256"],
                         rig.ACCEPTED_PERPART_CHECKPOINT_SHA256)
        self.assertEqual(manifest["grid"], [5, 7])
        self.assertTrue(manifest["solver"]["all_skeleton_cell_intersections_preserved"])
        self.assertTrue(manifest["solver"]["display_uses_actual_pinned_font_raster"])
        emoji = rig.load_emoji_codepoints()
        selected_scalars = {
            cell["unicode_scalar"]
            for frames in manifest["frames"].values()
            for frame in frames
            for cell in frame["cells"]
        }
        self.assertTrue(selected_scalars.isdisjoint(emoji))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(viewer.cmd_play(argparse.Namespace(
                version=latest["name"], montage=True, interval=0.01,
            )), 0)
        self.assertIn("109725 eligible; emoji excluded", output.getvalue())

    def test_registry_inventory_reports_every_playback_gap(self) -> None:
        self.assertEqual(viewer.registry_inventory(), {
            "listed": 41,
            "playable": 29,
            "legacy_glyph_playable": 6,
            "skeleton_manifest_playable": 23,
            "list_only": 12,
            "legacy_glyph_missing": 0,
            "no_manifest_declared": 7,
            "declared_manifest_missing": 5,
        })
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(viewer.cmd_list(argparse.Namespace(stage="all")), 0)
        self.assertIn("41 listed / 29 playable", output.getvalue())
        self.assertIn("12 list-only", output.getvalue())

    def test_stonestory_banner_uses_manifest_candidate_supply_and_codebook(self) -> None:
        entry = next(
            entry for entry in viewer.read_playable_registry()
            if entry.get("kind") == "stonestory-walk"
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(viewer.cmd_play(argparse.Namespace(
                version=entry["name"], montage=True, interval=0.01,
            )), 0)
        text = output.getvalue()
        self.assertIn(
            "authored codebook + strokeness<=0.42 census, capped 14/track", text
        )
        self.assertIn("authored_walk_codebook.v2.stonestory.json", text)
        self.assertNotIn("canonical descriptor census", text)
        self.assertNotIn("montage (frame 0)  |  full-Unicode-minus-emoji", text)

    def test_latest_manifest_backed_registry_entry_is_the_default_playback(self) -> None:
        entries = viewer.read_playable_registry()
        latest = entries[-1]
        self.assertEqual(
            latest["name"],
            "v20260718T211044__stone-layer-keyframes-5x5__walk_all_directions",
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(viewer.cmd_play(argparse.Namespace(
                version=None, montage=True, interval=0.01,
            )), 0)
        text = output.getvalue()
        self.assertIn(latest["name"], text)
        self.assertIn("FL-4547/FL-4561 walk viewer", text)

    def test_manifest_playback_supports_top_level_and_nested_frame_schemas(self) -> None:
        entries = viewer.read_playable_registry()
        by_kind = {entry.get("kind"): entry for entry in entries}
        for kind in ("semantic-caricature-chain-5x5", "stone-layer-keyframes"):
            manifest = __import__("json").loads(
                (REPO / by_kind[kind]["tracked_frames"]).read_text()
            )
            frames, count, keys, cols, rows = viewer.manifest_playback(manifest)
            self.assertEqual(set(frames), set(viewer.DIRS))
            self.assertEqual(count, 18)
            self.assertEqual(keys[-1], "f17")
            self.assertGreater(cols, 0)
            self.assertGreater(rows, 0)


if __name__ == "__main__":
    unittest.main()
