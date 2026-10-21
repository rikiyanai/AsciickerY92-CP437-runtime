from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTROLS = ROOT / "godot_project" / "scripts" / "ControlsRegistry.gd"
MAIN = ROOT / "godot_project" / "scripts" / "Main.gd"
WORLD = ROOT / "godot_project" / "scripts" / "World.gd"
HUD = ROOT / "godot_project" / "scripts" / "ui" / "StatusHUD.gd"
HUD_SCENE = ROOT / "godot_project" / "scenes" / "ui" / "HUD.tscn"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_desktop_manual_run_deletes_live_final_render_dump_triggers() -> None:
    controls = read(CONTROLS)
    main = read(MAIN)
    world = read(WORLD)

    assert '"Render-cell dump"' not in controls
    assert '"Record movement sequence"' in controls
    assert "KEY_X:" in world
    assert "_toggle_manual_sequence_recording()" in world
    assert 'Input.is_action_just_pressed("take_shot_capture")' in main
    assert "take_shot_capture(DEFAULT_SHOT_CAPTURE_PATH)" in main
    assert "hud.visible = true" in main
    assert "ASCIICKICKER_HUD" not in main
    assert 'world.call("_request_final_render_cell_dump", false)' not in main
    assert '"deleted_runtime_final_render_dump"' in world
    assert '"FL-4359 runtime final_render_cell_dump was deleted' in world
    assert '_ascii_effect.call("request_final_cell_paint_capture", 4)' not in world
    assert 'push_warning("FL-4359 final_render_cell_dump auto capture is deleted' in world
    assert "MANUAL_SEQUENCE_SAMPLE_HZ := 10.0" in world
    assert ".run/fl4359_manual_recordings" in world
    assert '"camera_world": _vec3_dict(camera.global_position)' in world
    assert '"keys": _manual_sequence_key_state()' in world
    assert "--fl4057-ab-x-dump is deleted" in main


def test_f3_hud_has_screen_record_state_fields() -> None:
    hud = read(HUD)
    scene = read(HUD_SCENE)

    required = [
        "F3 SCREEN STATE",
        "DisplayServer.window_get_position()",
        "DisplayServer.window_get_size()",
        "DisplayServer.window_get_current_screen()",
        "git branch=%s head=%s",
        "time=%s",
        "mode camera=%s arch_perspective=%s",
        "player x=%.3f y=%.3f z=%.3f yaw=%.3f",
        "camera x=%.3f y=%.3f z=%.3f pitch=%.6f yaw=%.6f",
        "RECORDING",
        'add_theme_color_override("font_color", Color(1.0, 0.08, 0.04, 1.0)',
    ]
    for needle in required:
        assert needle in hud, needle
    render_debug = scene[scene.index('[node name="RenderDebug"') :]
    assert "offset_top = 20.0" in render_debug
    assert "offset_bottom = 116.0" in render_debug
