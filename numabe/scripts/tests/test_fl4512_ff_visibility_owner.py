"""Static FF visibility ownership checks, not runtime/render acceptance."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


class FFVisibilityOwner(unittest.TestCase):
    def test_callback_needs_output_surface_not_native_depth(self):
        source = (ROOT / "godot_project/render/api/ASCIICompositorEffect.gd").read_text()
        self.assertNotIn("direct_ff_scene_depth_missing", source)
        self.assertNotIn("scene_buffers.get_depth_texture()", source)
        # The engine still owns the output surface. Removing the native-depth
        # dependency must not delete its size, colour-layer or presentation seam.
        # FL-4907: the True FF frame has its own driver; that driver owns the
        # output surface seam for FF frames.
        driver = (ROOT / "godot_project/render/ascii/composition/TrueFFCompositorEffect.gd").read_text()
        self.assertNotIn("get_depth_texture", driver)
        callback = driver[driver.index("func _render_callback("):]
        self.assertIn("scene_buffers.get_internal_size()", callback)
        self.assertIn("scene_buffers.get_color_layer(view)", callback)
        self.assertIn("_renderer.render_frame(rd,", callback)
        legacy = source[source.index("func _render_callback("):]
        self.assertIn("if not pipeline.is_valid() or not _initialized:", legacy)

    def test_ff_consumers_receive_the_same_owned_visibility_depth(self):
        source = (ROOT / "godot_project/render/ascii/composition/TrueFFFrameRenderer.gd").read_text()
        self.assertEqual(source.count("_gbuffer.draw("), 1)
        # FL-4907: the one P0 depth reaches the feature route through the page.
        self.assertIn('"prepass_depth": _gbuffer.depth_rid(),', source)
        self.assertIn("_render_features(camera_facts, page_facts)", source)
        self.assertNotIn("get_depth_texture", source)


if __name__ == "__main__":
    unittest.main()
