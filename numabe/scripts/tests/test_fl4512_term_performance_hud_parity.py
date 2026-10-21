#!/usr/bin/env python3
"""Source contract for the product TERM++ performance-HUD action owner."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
EDITOR = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
GAME_RENDER = (ROOT / "engine/game_render_bridge.cpp").read_text(encoding="utf-8")
TERM = (ROOT / "platform/terminal_gl_present.cpp").read_text(encoding="utf-8")


class TermPerformanceHudParityTest(unittest.TestCase):
    def test_runtime_owner_defaults_on_and_gates_status_row(self) -> None:
        self.assertIn("g_term_performance_hud_visible = true", GAME_RENDER)
        self.assertIn(
            "if (RendererGetTermPerformanceHudVisible())", GAME_RENDER
        )

    def test_frozen_environment_deletes_autonomous_fps_cell_drift(self) -> None:
        fps_owner = GAME_RENDER.split("uint64_t fps_window_time", 1)[1]
        fps_owner = fps_owner.split("if (_stamp-stamp", 1)[0]
        self.assertIn(
            "!RendererGetEnvironmentalAnimationEnabled(renderer)", fps_owner
        )
        self.assertIn('FPSx10 = 1199; // "119.9 fps"', fps_owner)

    def test_retired_imgui_owner_is_physically_deleted(self) -> None:
        self.assertNotIn('ImGui::Checkbox("TERM++ PERFORMANCE HUD"', EDITOR)
        self.assertNotIn("##term_performance_hud_context", EDITOR)
        self.assertIn("TermGetPerformanceHudVisible,", EDITOR)
        self.assertIn("TermSetPerformanceHudVisible,", EDITOR)

    def test_retired_term_hotkey_cannot_bypass_typed_action(self) -> None:
        hotkey = TERM.split("else if (ki == A3D_F12)", 1)[1]
        hotkey = hotkey.split("else if", 1)[0]
        self.assertNotIn("TermSetPerformanceHudVisible", hotkey)
        self.assertIn("FL-4588", hotkey)


if __name__ == "__main__":
    unittest.main()
