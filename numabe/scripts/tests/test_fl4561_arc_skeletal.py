#!/usr/bin/env python3
"""Focused contracts for the corrected FL-4561 arc-skeletal falsifier."""

from __future__ import annotations

import importlib.util
import argparse
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "scripts" / "fl4547_arc_skeletal.py"
SPEC = importlib.util.spec_from_file_location("fl4547_arc_skeletal", MODULE_PATH)
assert SPEC and SPEC.loader
arc = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(arc)


class ArcSkeletalContractTest(unittest.TestCase):
    def test_next_run_is_blocked_by_cell_owned_selection(self) -> None:
        readiness = arc.next_run_readiness()
        self.assertFalse(readiness["ready"])
        self.assertEqual(
            readiness["current_final_selection_owner"],
            "(frame_index, cell_index)",
        )
        self.assertIn("chain_sequence_candidate_owner", readiness["missing_owners"])
        self.assertIn("cell_is_raster_destination_only", readiness["missing_owners"])

    def test_default_run_fails_before_loading_source_geometry(self) -> None:
        args = argparse.Namespace(
            reproduce_rejected_cell_owner=False,
            grid_w=5,
            grid_h=7,
            frame_ms=90,
        )
        original = arc.rig.load_rig_proposals
        arc.rig.load_rig_proposals = lambda: self.fail("blocked run loaded source geometry")
        try:
            with self.assertRaisesRegex(SystemExit, "next run blocked"):
                arc.run(args)
        finally:
            arc.rig.load_rig_proposals = original

    def test_cyclic_hysteresis_rejects_boundary_noise(self) -> None:
        points = [(1.95, 2.5), (2.05, 2.5)] * 4
        cells = arc.solve_cyclic_occupancy(points, 5, 7)
        self.assertEqual(len(set(cells)), 1)

    def test_required_port_uses_same_boundary_pixel(self) -> None:
        targets = [0] * 4
        targets[0] = 1 << (8 * 3 + 7)
        targets[1] = 1 << (8 * 3)
        required, links = arc.required_anchor_masks(targets, 2, 2)
        self.assertEqual(required[0], 1 << (8 * 3 + 7))
        self.assertEqual(required[1], 1 << (8 * 3))
        self.assertEqual(len(links), 1)

    def test_glyph_component_gate_rejects_detached_ink(self) -> None:
        connected = (1 << 0) | (1 << 1) | (1 << 9)
        detached = connected | (1 << 63)
        self.assertEqual(arc.component_count(connected), 1)
        self.assertEqual(arc.component_count(detached), 2)

    def test_projection_freezes_midline_relative_to_pelvis(self) -> None:
        records = []
        for frame, root_x in enumerate((10.0, 13.0)):
            joints = {}
            for joint_id in arc.rig.JOINT_META:
                joints[joint_id] = {"source_xy": [root_x, 30.0]}
            joints["pelvis"] = {"source_xy": [root_x, 30.0]}
            joints["spine"] = {"source_xy": [root_x + frame, 20.0]}
            joints["neck"] = {"source_xy": [root_x - frame, 13.0]}
            joints["head"] = {"source_xy": [root_x + frame, 7.0]}
            records.append({"frame_index": frame, "joints": joints})
        projected, _root, _scales = arc.project_records({"S": records}, 5, 7)
        for joint_id in ("head", "neck", "spine"):
            self.assertEqual(
                projected["S"][0]["joints"][joint_id],
                projected["S"][1]["joints"][joint_id],
            )


if __name__ == "__main__":
    unittest.main()
