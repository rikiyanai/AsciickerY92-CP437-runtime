#!/usr/bin/env python3
"""Static ownership contract for the operator-selected biplane palette."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORLD = ROOT / "godot_project" / "scripts" / "World.gd"
BAKER = ROOT / "scripts" / "bake_airplane_airframe.py"


def test_biplane_uses_blue_body_and_yellow_authored_wingtips() -> None:
    world = WORLD.read_text(encoding="utf-8")
    baker = BAKER.read_text(encoding="utf-8")

    assert "var body := Color(0.22, 0.52, 0.95)" in world
    assert "var accent := Color(1.0, 0.78, 0.08)" in world
    assert "[AIRPLANE_MAT_BODY, body], [AIRPLANE_MAT_ACCENT, accent]" in world

    # The authored source assigns MAT_BODY to both wing slabs and MAT_ACCENT
    # to their outer cap slabs. The palette owner therefore recolors the
    # requested surfaces without introducing a second geometry classifier.
    assert baker.count("MAT_BODY, SH_TOP, SH_BELLY, SH_EDGE, cap_outer=False") == 4
    assert baker.count("MAT_ACCENT, 0.95, 0.45, 0.70") == 4
    assert "# accent wingtip caps" in baker
