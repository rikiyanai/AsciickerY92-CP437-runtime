#!/usr/bin/env python3
"""Source contract for FL-4512 empty-frame dispatch ownership."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "engine/render/render_scene.cpp").read_text(encoding="utf-8")


class EmptyFrameOwnershipTest(unittest.TestCase):
    def test_clear_templates_fail_closed(self) -> None:
        template_blocks = re.findall(
            r"for \(int cl = dw \* dh; cl < 2\s*\*?\s*dw\*dh; cl\+\+\)\s*\{(.*?)\n\t\t\}",
            SOURCE,
            flags=re.DOTALL,
        )
        self.assertEqual(2, len(template_blocks))
        for block in template_blocks:
            self.assertIn(
                "glyph_dispatch_surface = GLYPH_DISPATCH_UNKNOWN_VISUAL",
                block,
            )
            self.assertNotIn(
                "glyph_dispatch_surface = GLYPH_DISPATCH_AUTOMAP_MESH_RGB",
                block,
            )
            self.assertIn("material_id = -1", block)

    def test_real_mesh_raster_still_owns_mesh_dispatch(self) -> None:
        mesh_source = (
            ROOT / "engine/render/render_world_pass.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "s->glyph_dispatch_surface = GLYPH_DISPATCH_AUTOMAP_MESH_RGB",
            mesh_source,
        )
        self.assertIn("s->material_id = face_material_id", mesh_source)


if __name__ == "__main__":
    unittest.main()
