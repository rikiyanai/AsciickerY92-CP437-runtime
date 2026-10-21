"""Offline checks of the FF bank allocation policy; no Godot/GPU execution."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "godot_project/render/ascii/structure/TrueFFSceneGBuffer.gd"
FRAME_SOURCE = ROOT / "godot_project/render/ascii/composition/TrueFFFrameRenderer.gd"


class FFBankGrowth(unittest.TestCase):
    def body(self):
        source = SOURCE.read_text()
        return source.split("func _ensure_bank(", 1)[1].split("\n\nfunc ", 1)[0]

    def test_growth_expression_is_amortized_and_never_shrinks(self):
        # Evaluate the production scalar expression, not a second policy.
        expression = re.search(r"var capacity := (.+)", self.body()).group(1)
        expression = expression.replace('int(_bank_sizes.get(name, 0))', 'held')
        self.assertEqual(expression, "maxi(want, held * 2)")
        held = 64
        allocations = 0
        for requested in range(65, 8193):
            if held >= requested:
                continue
            new = eval(expression, {"__builtins__": {}, "maxi": max},
                       {"want": requested, "held": held})
            self.assertGreaterEqual(new, requested)
            self.assertLess(new, requested * 2)
            held = new
            allocations += 1
        self.assertEqual(allocations, 7)

    def test_allocation_failure_preserves_old_owner(self):
        body = self.body()
        create = body.index("_rd.storage_buffer_create(capacity, zero)")
        failure = body.index("if not created.is_valid():\n\t\treturn false", create)
        for mutation in ("_free_cached_sets()", "_owned.erase(existing)",
                         "_rd.free_rid(existing)", "_banks[name] = created",
                         "_bank_sizes[name] = capacity"):
            self.assertGreater(body.index(mutation), failure)
        self.assertLess(body.index("zero.resize(capacity)"), create)

    def test_changing_bank_growth_is_prepared_before_any_view(self):
        source = SOURCE.read_text()
        prepare = source.split("func _prepare_scene(", 1)[1].split("\n\nfunc ", 1)[0]
        capacity = source.split("func dynamic_capacity_ready(", 1)[1].split("\n\nfunc ", 1)[0]
        for name in ("movers", "mover_rows", "actor_instances", "actor_rows"):
            self.assertIn(f'"{name}"', capacity)
        self.assertLess(prepare.index("_changing_bank_byte_sizes(scene)"),
                        prepare.index("var published: Dictionary = publish(scene)"))
        self.assertIn("_ensure_bank(name, int(changing_sizes.get(name, 0)))", prepare)

        frame = FRAME_SOURCE.read_text()
        ingress = frame.split("func begin_source_frame(", 1)[1].split("\n\nfunc ", 1)[0]
        growth = "not _gbuffer.dynamic_capacity_ready(scene)"
        self.assertIn(growth, ingress)
        self.assertLess(ingress.index(growth), ingress.index("_passes.prepare_views("))


if __name__ == "__main__":
    unittest.main()
