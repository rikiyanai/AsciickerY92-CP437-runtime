#!/usr/bin/env python3
"""Focused contracts for the FL-4561 Active-Strokes parity slice."""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / "scripts" / "fl4547_arc_stroke_walk.py"
SPEC = importlib.util.spec_from_file_location("fl4547_arc_stroke_walk", MODULE)
assert SPEC and SPEC.loader
walk = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(walk)


class ActiveStrokesParityContractTest(unittest.TestCase):
    def test_all_eight_directions_are_direct_sources(self) -> None:
        self.assertEqual(tuple(walk.rig.DISPLAY_DIRS), walk.GENERATED_DIRECTIONS)
        self.assertTrue(walk.OWNERSHIP_CONTRACT["all_eight_source_directions_compiled_directly"])

    def test_persistent_state_is_chain_arc_slot(self) -> None:
        expected = sum(spec["count"] for spec in walk.TRACK_SPECS.values())
        ids = {
            f"{role}@{slot}"
            for role, spec in walk.TRACK_SPECS.items()
            for slot in range(spec["count"])
        }
        self.assertEqual(expected, 22)
        self.assertEqual(len(ids), expected)
        self.assertTrue(walk.OWNERSHIP_CONTRACT["cell_is_raster_destination_only"])

    def test_matching_horizontal_boundary_bits(self) -> None:
        bit_a, bit_b = walk._boundary_bits((1, 3), (2, 3), (1.8, 3.25), (2.2, 3.25))
        self.assertEqual(bit_a, 1 << (8 * 2 + 7))
        self.assertEqual(bit_b, 1 << (8 * 2))

    def test_matching_diagonal_corner_bits(self) -> None:
        bit_a, bit_b = walk._boundary_bits((1, 2), (2, 3), (1.8, 2.8), (2.2, 3.2))
        self.assertEqual(bit_a, 1 << 63)
        self.assertEqual(bit_b, 1)

    def test_long_connection_is_derived_as_adjacent_bridge_cells(self) -> None:
        cells = walk._connection_cells((1, 1), (3, 5))
        self.assertEqual(cells[0], (1, 1))
        self.assertEqual(cells[-1], (3, 5))
        self.assertTrue(all(max(abs(b[0] - a[0]), abs(b[1] - a[1])) == 1
                            for a, b in zip(cells, cells[1:])))

    def test_transport_is_cyclic_and_stabilizes_midline(self) -> None:
        raw = np.asarray([(2.0 + (0.3 if index % 2 else -0.3), float(index % 3))
                          for index in range(walk.FRAMES)], dtype=float)
        smooth = walk.transport_and_relax(raw, "torso")
        self.assertEqual(smooth.shape, raw.shape)
        self.assertLess(float(np.var(smooth[:, 0])), float(np.var(raw[:, 0])))
        self.assertLess(abs(smooth[0, 0] - smooth[-1, 0]), 0.10)

    def test_presence_hysteresis_heals_one_frame_gap(self) -> None:
        values = [True] * walk.FRAMES
        values[7] = False
        self.assertTrue(all(walk._presence_hysteresis(values)))


if __name__ == "__main__":
    unittest.main()
