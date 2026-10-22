#!/usr/bin/env python3
"""Source contract for FL-4260 whole-map Auto Texture action parity."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")


class AutoTextureActionParityTest(unittest.TestCase):
    def test_single_deferred_mutation_owner(self) -> None:
        self.assertIn(
            'ConfirmApplyAutoTexture(void* cookie)\n{ StartDeferredOp("Auto Texture", AutoTexture::Apply',
            SOURCE,
        )
        helper = SOURCE.split("static bool Fl4260QueueAutoTextureAction", 1)[1]
        helper = helper.split("struct BakeConfirmParams", 1)[0]
        self.assertIn("RequestConfirm", helper)
        self.assertIn("ConfirmApplyAutoTexture(&ctx)", helper)

    def test_click_keyboard_and_context_share_helper(self) -> None:
        ui = SOURCE.split('ImGui::Button("Apply Auto Texture")', 1)[1]
        ui = ui.split("ImGui::Columns(1); // close the 2-column auto-tools block", 1)[0]
        self.assertIn("Fl4260QueueAutoTextureAction", ui)
        self.assertIn("ImGuiKey_Enter", ui)
        self.assertIn("ImGuiKey_Space", ui)
        self.assertIn('BeginPopupContextItem("##apply_auto_texture_context")', ui)

    def test_old_inline_ui_owner_is_deleted(self) -> None:
        ui = SOURCE.split('ImGui::Button("Apply Auto Texture")', 1)[1]
        ui = ui.split("ImGui::Columns(1); // close the 2-column auto-tools block", 1)[0]
        self.assertNotIn("AutoTexture ctx =", ui)
        self.assertNotIn("RequestConfirm", ui)


if __name__ == "__main__":
    unittest.main()
