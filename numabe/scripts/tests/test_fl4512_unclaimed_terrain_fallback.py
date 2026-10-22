from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESOLVE = (ROOT / "engine/render/render_resolve.cpp").read_text(encoding="utf-8")


def test_unapplied_material_look_keeps_original_terrain_fallback() -> None:
    contract = "Unclaimed cells remain the\n\t\t\t\t\t// original CP437 terrain path"
    assert contract in RESOLVE

    start = RESOLVE.index("const bool material_look_applied = DispatchShape6ResolverHook")
    end = RESOLVE.index("// --- water-pass fixture", start)
    block = RESOLVE[start:end]

    assert "if (!material_look_applied)" in block
    assert "ApplyMaterialGlyphPlaneCell(" in block
    assert "Fl4260MaterialProfileLive" not in block


def test_web_fallback_is_not_replaced_by_profile_color_writer() -> None:
    assert "ApplyActiveProfileColor" not in RESOLVE
