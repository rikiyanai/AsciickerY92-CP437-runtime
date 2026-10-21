#!/usr/bin/env python3
"""Structural tests for the FL-4561 held-layer gait-key compiler."""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import fl4561_stone_layer_walk as subject


class StoneLayerWalkTest(unittest.TestCase):
    def test_gait_keys_are_unique_and_cyclically_ordered(self):
        a = np.asarray([
            (math.sin(index * 2.0 * math.pi / subject.FRAMES), 0.0)
            for index in range(subject.FRAMES)
        ])
        b = -a
        keys = subject.detect_gait_keys(a, b)
        self.assertEqual(set(keys), set(subject.PHASE_NAMES))
        self.assertEqual(len(set(keys.values())), 4)
        contact = keys["contact"]
        distances = [
            (keys[name] - contact) % subject.FRAMES
            for name in subject.PHASE_NAMES
        ]
        self.assertEqual(distances, sorted(distances))

    def test_limb_cells_are_pivot_connected_and_bounded(self):
        pivot = (2, 2)
        cells = subject.limb_cells([(10.0, 10.0), (30.0, 52.0)], pivot, (64, 64))
        self.assertGreaterEqual(len(cells), 1)
        self.assertLessEqual(len(cells), 2)
        chain = [pivot] + cells
        for a, b in zip(chain, chain[1:]):
            self.assertEqual(max(abs(a[0] - b[0]), abs(a[1] - b[1])), 1)
        self.assertTrue(all(0 <= x < subject.GRID_W and 0 <= y < subject.GRID_H
                            for x, y in cells))

    def test_target_mask_exposes_every_required_port(self):
        ports = {"tl", "r", "b"}
        mask = subject.target_mask(55.0, ports)
        self.assertTrue(all(subject.has_port(mask, port) for port in ports))

    def test_caricature_layout_separates_counterpart_lanes(self):
        masks = {}
        for role, points in {
            "torso": [(8, y) for y in range(4, 13)],
            "arm_a": [(8 - step, 5 + step) for step in range(5)],
            "arm_b": [(8 + step, 5 + step) for step in range(5)],
            "leg_a": [(8 - step // 2, 12 + step) for step in range(8)],
            "leg_b": [(8 + step // 2, 12 + step) for step in range(8)],
        }.items():
            mask = np.zeros((24, 18), dtype=bool)
            for x, y in points:
                mask[y, x] = True
            masks[role] = mask
        pivots = {"arm_a": (2, 1), "arm_b": (2, 1),
                  "leg_a": (2, 2), "leg_b": (2, 2)}
        layout = subject.caricature_limb_cells(masks, pivots)
        self.assertEqual(layout["arm_a"][-1][0], 0)
        self.assertEqual(layout["arm_b"][-1][0], 4)
        self.assertEqual(layout["leg_a"][-1][0], 1)
        self.assertEqual(layout["leg_b"][-1][0], 3)

    def test_ownership_contract_names_approved_boundaries(self):
        expected = {
            "held_head_spine_layer_owner",
            "explicit_shoulder_hip_pivot_owner",
            "four_symbolic_gait_key_owner",
            "full_non_emoji_unicode_discovery_owner",
            "non_joint_occlusion_suppression_owner",
        }
        self.assertTrue(expected.issubset(subject.OWNERSHIP_CONTRACT))
        self.assertTrue(all(subject.OWNERSHIP_CONTRACT[key] for key in expected))


if __name__ == "__main__":
    unittest.main()
