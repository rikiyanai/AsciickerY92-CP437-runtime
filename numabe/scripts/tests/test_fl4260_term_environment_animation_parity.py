#!/usr/bin/env python3
"""Source contract for TERM++ environment-animation action ownership."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
EDITOR = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
GAME_APP = (ROOT / "engine/game_app.cpp").read_text(encoding="utf-8")
SCENE = (ROOT / "engine/render/render_scene.cpp").read_text(encoding="utf-8")
INTERNAL = (ROOT / "engine/render/render_internal.h").read_text(encoding="utf-8")
TERM = (ROOT / "platform/terminal_gl_present.cpp").read_text(encoding="utf-8")
SPRITE_BLIT = (ROOT / "engine/render/render_sprite_blit.cpp").read_text(
    encoding="utf-8")


class TermEnvironmentAnimationParityTest(unittest.TestCase):
    def test_renderer_setting_is_default_on_and_pauses_without_reset(self) -> None:
        self.assertIn("environment_animation_enabled = 1", INTERNAL)
        phase = SCENE.split("if (ObserveRenderEnabled())", 1)[1]
        phase = phase.split("if (r->pn_time >=", 1)[0]
        self.assertIn("else if (r->environment_animation_enabled)", phase)
        self.assertEqual(1, phase.count("r->pn_time = 0.0"))

    def test_editor_surfaces_share_one_action_owner(self) -> None:
        helper = EDITOR.split(
            "static int Fl4260SetTermEnvironmentalAnimationEnabled", 1
        )[1].split("static void AsciiidEmbeddedTermApplyCamera", 1)[0]
        self.assertIn("TermSetEnvironmentalAnimationEnabled", helper)
        self.assertIn("RendererSetEnvironmentalAnimationEnabled", helper)

        ui = EDITOR.split('ImGui::Checkbox("TERM++ ANIMATE ENVIRONMENT"', 1)[1]
        ui = ui.split("\n\t\t}", 1)[0]
        self.assertIn("Fl4260SetTermEnvironmentalAnimationEnabled", ui)
        self.assertIn("ImGuiKey_Enter", ui)
        self.assertIn("ImGuiKey_Space", ui)
        self.assertIn("##term_environment_animation_context", ui)

    def test_term_window_hotkey_reaches_same_term_owner(self) -> None:
        hotkey = TERM.split("else if (ki == A3D_F10)", 1)[1]
        hotkey = hotkey.split("else if", 1)[0]
        self.assertIn("TermSetEnvironmentalAnimationEnabled", hotkey)

    def test_environment_phase_reset_is_a_complete_user_reachable_leaf(self) -> None:
        helper = EDITOR.split(
            "static int Fl4260ResetTermEnvironmentalPhase", 1
        )[1].split("static AnsiCell g_asciiid_embedded_term_cells", 1)[0]
        self.assertIn("TermResetEnvironmentalPhase", helper)
        self.assertIn("RendererResetObservePhase", helper)
        self.assertNotIn("CDP", helper)
        self.assertNotIn("RendererResetEnvironmentPhase", helper)

        ui = EDITOR.split('ImGui::Button("RESET ENVIRONMENT PHASE")', 1)[1]
        ui = ui.split('ImGui::Checkbox("TERM++ PERFORMANCE HUD"', 1)[0]
        self.assertIn('Fl4260KbRegister("view.termpp_environment_phase_reset")', ui)
        self.assertIn("ImGuiKey_Enter", ui)
        self.assertIn("ImGuiKey_Space", ui)
        self.assertIn("##term_environment_phase_reset_context", ui)
        self.assertIn('ImGui::MenuItem("Reset water and environment phase")', ui)
        self.assertGreaterEqual(ui.count("Fl4260ResetTermEnvironmentalPhase"), 2)

    def test_missing_materials_have_one_deterministic_visible_sentinel(self) -> None:
        missing = GAME_APP.split(
            "// FL-4512: materials absent from the loaded A3D", 1
        )[1].split("void* GetFontArr();", 1)[0]
        self.assertNotIn("fast_rand", missing)
        self.assertIn("mat[i].shade[r][s].bg[0] = 0;", missing)
        self.assertIn("mat[i].shade[r][s].fg[0] = 255;", missing)
        self.assertIn("mat[i].shade[r][s].fg[2] = 255;", missing)
        self.assertIn("mat[i].shade[r][s].gl = '?';", missing)
        self.assertNotIn('#include "fast_rand.h"', GAME_APP)

    def test_world_frame_is_the_only_sprite_frame_owner(self) -> None:
        assignment = SPRITE_BLIT.split("buf->sprite = s;", 1)[1].split(
            "buf->reps[0]", 1
        )[0]
        self.assertIn("buf->frame = frame;", assignment)
        self.assertNotIn("AnimateSpriteInst", assignment)

    def test_profile_colors_reach_termpp_without_a_selection_tint_owner(self) -> None:
        # U4's presented cell identity is (GlyphId, fg, bg).  A material
        # selection is editor state, never a second TERM++ palette owner: the
        # former highlight pass swapped fg/bk after the profile lookup and
        # made an authored foreground edit present as a reversed palette pair.
        presentation = TERM.split("void term_render(A3D_WND *wnd)", 1)[1]
        self.assertNotIn("Fl4260GetTraceHighlightState", presentation)
        self.assertNotIn("highlight_enabled", presentation)
        self.assertNotIn("term->buf[t_idx].bk = pfg", presentation)
        self.assertNotIn("term->buf[t_idx].fg = pbg", presentation)
        self.assertIn("term->buf[term_idx].fg = pfg;", presentation)
        self.assertIn("term->buf[term_idx].bk = pbg;", presentation)


if __name__ == "__main__":
    unittest.main()
