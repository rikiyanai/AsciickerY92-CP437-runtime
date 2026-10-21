from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROLS = ROOT / "godot_project/scripts/ControlsRegistry.gd"
WORLD = ROOT / "godot_project/scripts/World.gd"
PLAYER = ROOT / "godot_project/scripts/player.gd"
COMPOSITOR = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
PAUSE = ROOT / "godot_project/scripts/PauseMenu.gd"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def function_body(source: str, name: str) -> str:
    start = source.index(f"func {name}(")
    next_func = source.find("\nfunc ", start + 1)
    return source[start:] if next_func < 0 else source[start:next_func]


def test_dual_zoom_uses_one_modifier_aware_registry_owner() -> None:
    controls = read(CONTROLS)
    world = read(WORLD)

    assert '"id": "glyph_matrix_in"' in controls
    assert '"id": "glyph_matrix_out"' in controls
    # Operator falsifier 2026-08-18: Command chords are macOS menu-equivalents
    # and never reached the unbundled CLI game (Finder resized its desktop
    # icons instead), so the chord is Shift+Option and the registry matcher is
    # option-aware.
    assert '"shift": true, "option": true' in controls
    assert "option_pressed: bool" in controls
    assert "key_event.alt_pressed" in world
    assert "static func toggle_id_for_event(" in controls
    assert "key_event.shift_pressed" in world
    assert "key_event.meta_pressed" in world
    assert "KEY_EQUAL:" not in function_body(world, "_unhandled_input")
    assert "KEY_MINUS:" not in function_body(world, "_unhandled_input")
    assert '"glyph_matrix_in":' in function_body(world, "_manual_controls_action")
    assert '"glyph_matrix_out":' in function_body(world, "_manual_controls_action")


def test_ordinary_zoom_remains_camera_owned() -> None:
    controls = read(CONTROLS)
    world = read(WORLD)
    player = read(PLAYER)

    assert '"id": "zoom_in",      "key": KEY_EQUAL' in controls
    assert '"id": "zoom_out",     "key": KEY_MINUS' in controls
    assert '_call_player_camera_control("runtime_zoom_in")' in world
    assert '_call_player_camera_control("runtime_zoom_out")' in world
    assert "func runtime_zoom_in() -> void:" in player
    assert "func runtime_zoom_out() -> void:" in player
    assert "_runtime_zoom_scale * RUNTIME_ZOOM_STEP" in player
    assert "_runtime_zoom_scale / RUNTIME_ZOOM_STEP" in player


def test_matrix_zoom_changes_only_bounded_pixel_cell_owner() -> None:
    compositor = read(COMPOSITOR)
    setter = function_body(compositor, "_set_glyph_matrix_resolution_step")

    assert "GLYPH_MATRIX_CELL_SIZE_STEPS: Array[int] = [32, 24, 20, 16, 12]" in compositor
    assert "cell_w = cell_pixels" in setter
    assert "cell_h = cell_pixels" in setter
    for forbidden in (
        "camera.",
        "camera =",
        "projection =",
        "frustum =",
        "_runtime_zoom_scale",
        "transform =",
    ):
        assert forbidden not in setter.lower()
    assert '"glyph_matrix_cols"' in compositor
    assert '"glyph_matrix_rows"' in compositor


def test_visible_control_surfaces_render_registry_chords() -> None:
    controls = read(CONTROLS)
    pause = read(PAUSE)

    assert "static func control_binding_name(control: Dictionary)" in controls
    assert 'parts.append("Shift")' in controls
    assert 'parts.append("Command")' in controls
    assert "control_binding_name(c)" in pause
