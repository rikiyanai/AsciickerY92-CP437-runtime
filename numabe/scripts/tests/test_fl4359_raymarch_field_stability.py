import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fl4359_raymarch_field_stability import ray_key, compare_samples, direct_product_masks


class RaySourceIdentityTests(unittest.TestCase):
    def row(self):
        row = {name: True for name in (
            "oracle_record_valid", "oracle_query_valid", "oracle_direct_hit",
            "oracle_production_hit", "oracle_identity_agrees_with_selected_surface")}
        row.update({"selected_surface_owner": 1, "selected_surface_object": 2,
                    "selected_surface_primitive": 3, "selected_surface_material": 4,
                    "selected_surface_world_position": [1.0, 2.0, 3.0],
                    "selected_surface_scene_revision": 8, "oracle_dynamic_revision": 9})
        return row

    def test_exact_world_and_epoch_changes_are_not_same_source(self):
        row = self.row()
        original = ray_key(row)
        row["selected_surface_world_position"][0] += 0.000001
        self.assertNotEqual(original, ray_key(row))
        row = self.row()
        row["selected_surface_owner"] = 4
        original = ray_key(row)
        row["oracle_dynamic_revision"] += 1
        self.assertNotEqual(original, ray_key(row))

    def test_static_source_ignores_unrelated_actor_revision(self):
        row = self.row()
        original = ray_key(row)
        row["oracle_dynamic_revision"] += 1
        self.assertEqual(original, ray_key(row))

    def test_oracle_disagreement_excluded(self):
        row = self.row()
        row["oracle_identity_agrees_with_selected_surface"] = False
        self.assertIsNone(ray_key(row))

    def test_no_comparisons_is_not_zero_shimmer(self):
        result = compare_samples({}, {})
        self.assertIsNone(result["channels"]["fg"]["changed_pct"])
        self.assertEqual(result["channels"]["fg"]["comparable_samples"], 0)

    def test_direct_mask_uses_unanimous_product_and_actual_pixels(self):
        rows = [{"screen_cell": {"x": 0, "y": 0},
                 "final_product_status": (1 << 6) | (7 << 8)} for _ in range(4)]
        pixels = np.zeros((2, 2))
        field = SimpleNamespace(metadata={"cell_size": [2, 2]}, width=2, height=2,
                                fields={"native_intensity": pixels})
        page = SimpleNamespace(spec={"glyph_min": 0, "glyph_max": 255},
                               mask=lambda glyph, h, w: np.zeros((h, w)))
        with patch("fl4359_raymarch_field_stability.load_observed_atlases", return_value={0: page}):
            self.assertEqual(direct_product_masks(rows, field)["checked_cells"], 1)
            pixels[0, 0] = 1
            self.assertEqual(direct_product_masks(rows, field)["mismatching_pixels"], 1)
            rows[0]["final_product_status"] = (1 << 6) | (8 << 8)
            self.assertEqual(direct_product_masks(rows, field)["disagreeing_product_cells"], 1)


if __name__ == "__main__":
    unittest.main()
