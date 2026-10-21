#!/usr/bin/env python3
"""Source contract for the FL-4512 proof-capture HUD visibility owner."""

from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
HUD_SOURCE = REPO / "engine/render/render_hud_overlay.cpp"
GAME_BRIDGE = REPO / "engine/game_render_bridge.cpp"
RENDER_TARGET_MAKEFILES = (
    "apps/asciicker-presenter/makefile_asciiid",
    "apps/asciicker-presenter/makefile_asciiid_mac",
    "apps/game-fl4512/makefile_game",
    "apps/game-fl4512/makefile_game_mac",
    "makefile_game_term",
    "makefile_game_term_mac",
    "makefile_server",
)


def test_health_bar_visibility_has_one_shared_hud_owner():
    hud_source = HUD_SOURCE.read_text(encoding="utf-8")
    game_bridge = GAME_BRIDGE.read_text(encoding="utf-8")

    for definition in (
        "void RendererSetEditorHealthBarsHidden(int hidden)",
        "int RendererGetEditorHealthBarsHidden()",
    ):
        assert hud_source.count(definition) == 1
        assert definition not in game_bridge


def test_every_render_target_links_the_hud_owner():
    owner_source = "engine/render/render_hud_overlay.cpp"
    for makefile_name in RENDER_TARGET_MAKEFILES:
        makefile = (REPO / makefile_name).read_text(encoding="utf-8")
        assert owner_source in makefile, makefile_name
