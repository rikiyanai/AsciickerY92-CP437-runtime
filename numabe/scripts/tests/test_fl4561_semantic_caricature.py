#!/usr/bin/env python3
"""Focused contracts for the FL-4561 semantic-caricature experiment."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO / "scripts" / "fl4561_semantic_caricature.py"
SPEC = importlib.util.spec_from_file_location("fl4561_semantic_caricature", MODULE_PATH)
assert SPEC and SPEC.loader
caricature = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(caricature)


class SemanticCaricatureContractTest(unittest.TestCase):
    def test_readiness_declares_chain_cycle_as_only_selection_owner(self) -> None:
        contract = caricature.readiness_contract()
        self.assertTrue(contract["ready"])
        self.assertEqual(
            contract["final_selection_owner"],
            "(direction, semantic_chain, closed_cycle)",
        )
        self.assertEqual(contract["cell_is_raster_destination_only"], True)
        self.assertEqual(contract["sequence_level_counterpart_solver"], True)
        self.assertEqual(contract["runtime_glyph_writer"], False)

    def test_body_plan_uses_distinct_presentation_cells(self) -> None:
        cells = [cell for slots in caricature.CHAIN_SLOTS.values() for cell in slots]
        self.assertEqual(len(cells), 11)
        self.assertEqual(len(set(cells)), 11)
        self.assertEqual(caricature.LATENT_GRID, (3, 5))
        self.assertEqual(caricature.CHAIN_SLOTS["head"], ((2, 0),))
        self.assertEqual(caricature.CHAIN_SLOTS["torso"], ((2, 1), (2, 2)))

    def test_declared_joint_ports_are_eight_connected_in_global_raster(self) -> None:
        def global_point(chain: str, slot: int, x: int, y: int):
            col, row = caricature.CHAIN_SLOTS[chain][slot]
            return col * 8 + x, row * 8 + y

        pairs = [
            (global_point("head", 0, 3, 7), global_point("torso", 0, 3, 0)),
            (global_point("torso", 0, 3, 7), global_point("torso", 1, 3, 0)),
            (global_point("torso", 0, 0, 4), global_point("arm_left", 0, 7, 4)),
            (global_point("torso", 0, 7, 4), global_point("arm_right", 0, 0, 4)),
            (global_point("arm_left", 0, 0, 7), global_point("arm_left", 1, 7, 0)),
            (global_point("arm_right", 0, 7, 7), global_point("arm_right", 1, 0, 0)),
            (global_point("torso", 1, 0, 7), global_point("leg_left", 0, 7, 0)),
            (global_point("torso", 1, 7, 7), global_point("leg_right", 0, 0, 0)),
            (global_point("leg_left", 0, 4, 7), global_point("leg_left", 1, 4, 0)),
            (global_point("leg_right", 0, 3, 7), global_point("leg_right", 1, 3, 0)),
        ]
        for point_a, point_b in pairs:
            self.assertLessEqual(max(abs(point_a[0] - point_b[0]), abs(point_a[1] - point_b[1])), 1)

    def test_temporal_cost_prices_unexplained_curve_change(self) -> None:
        still = {"masks": (0b11,)}
        changed = {"masks": (0b1100,)}
        self.assertEqual(caricature.temporal_cost(still, still, [0b11], [0b11]), 0.0)
        self.assertGreater(
            caricature.temporal_cost(still, changed, [0b11], [0b11]),
            caricature.temporal_cost(still, changed, [0b11], [0b1100]),
        )

    def test_counterpart_cost_uses_reflected_glyph_geometry_without_id_lock(self) -> None:
        left = {"masks": (caricature._bit(1, 3) | caricature._bit(2, 3),)}
        reflected = {"masks": (caricature._bit(6, 3) | caricature._bit(5, 3),)}
        unrelated = {"masks": (caricature._bit(0, 0),)}
        self.assertEqual(caricature.counterpart_cost(reflected, left), 0.0)
        self.assertGreater(caricature.counterpart_cost(unrelated, left), 0.0)

    def test_cyclic_occlusion_fill_preserves_chain_identity(self) -> None:
        visible_a = [(0.0, 0.0), (1.0, 1.0)]
        visible_b = [(4.0, 0.0), (5.0, 1.0)]
        filled = caricature._cyclic_fill_samples([visible_a, None, visible_b, None])
        self.assertEqual(len(filled), 4)
        self.assertEqual(filled[1], [[2.0, 0.0], [3.0, 1.0]])
        self.assertEqual(filled[3], [[2.0, 0.0], [3.0, 1.0]])

    def test_accepted_source_retargets_with_frozen_midline_and_complete_roles(self) -> None:
        skeletons, _hashes, _bboxes = caricature.source.load_accepted_perpart_skeletons()
        for direction in caricature.source.DISPLAY_DIRS:
            retargeted = caricature.retarget_direction(skeletons[direction])
            self.assertEqual(set(retargeted["roles"].values()), set(caricature.CHAIN_ORDER))
            for chain in ("head", "torso"):
                self.assertTrue(all(
                    targets == retargeted["targets"][chain][0]
                    for targets in retargeted["targets"][chain]
                ))
            for chain in caricature.CHAIN_ORDER:
                self.assertEqual(len(retargeted["targets"][chain]), caricature.FRAME_COUNT)
                self.assertEqual(len(retargeted["visibility"][chain]), caricature.FRAME_COUNT)
                self.assertTrue(all(
                    len(frame_targets) == len(caricature.CHAIN_SLOTS[chain])
                    for frame_targets in retargeted["targets"][chain]
                ))


if __name__ == "__main__":
    unittest.main()
