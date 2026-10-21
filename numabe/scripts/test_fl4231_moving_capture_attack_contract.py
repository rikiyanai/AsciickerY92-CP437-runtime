#!/usr/bin/env python3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "godot_project" / "scripts" / "Main.gd"


def _function_body(src: str, name: str) -> str:
    marker = f"\nfunc {name}"
    start = src.index(marker) + 1
    next_func = src.find("\nfunc ", start + 1)
    if next_func == -1:
        return src[start:]
    return src[start:next_func]


def test_moving_capture_exercises_server_owned_attack_actor_rows() -> None:
    src = MAIN.read_text(encoding="utf-8")
    body = _function_body(src, "_run_probe_fl4231_moving_capture")

    required = [
        '"phase": "attack_sword"',
        'server.apply_intent(LOCAL_ACTOR_ID, InputIntent.from_dictionary({"attack": true}))',
        "refresh_local_world_state(LOCAL_ACTOR_ID)",
        'print("FL4231_MOVING_ATTACK phase=attack_sword server_visual_presentation=attack")',
        'step.get("server_attack", false)',
        "FL4231_MOVING_DONE dumps=%d",
    ]
    for needle in required:
        assert needle in body, needle

    forbidden = [
        "FL4231_MOVING_TODO attack_sword=needs_combat_api actor_row_toggle=needs_actorlayerstack_api",
        "TODO(FL-4231): attack/sword",
        'player.attack_anim_remaining =',
        'player.attack_active =',
        'visual_presentation_kind = "attack"',
    ]
    for needle in forbidden:
        assert needle not in body, needle


def test_wake_demo_pins_perspective_water_pose_for_screenshots() -> None:
    src = MAIN.read_text(encoding="utf-8")
    ready = _function_body(src, "_ready")
    body = _function_body(src, "_run_wake_demo_capture")
    seed_body = _function_body(src, "_apply_wake_demo_actor_state")

    required_ready = [
        'var wake_demo_requested := args.has("--wake-demo")',
        'if not args.has("--wake-demo"):',
        "_install_pause_menu()",
        "and not wake_demo_requested",
    ]
    for needle in required_ready:
        assert needle in ready, needle

    required_body = [
        "_fl4057_fixed_render_pose_active = true",
        "_fl4057_fixed_render_pose = spawn_pose",
        "_fl4057_fixed_render_pose = walked",
        'player.call("set_force_perspective", true)',
        'player.call("_aim_force_perspective_camera")',
        "projection=%s",
        "actor_rows=%d",
        "zoom_steps=%d",
        'world.call("_manual_controls_action", "zoom_in")',
        'world.call("_sync_actor_visuals_to_compositor")',
        "last_actor_frame_rect_row_count",
        "actor rows missing before capture",
        "RenderingServer.frame_post_draw",
        "_apply_wake_demo_actor_state(player, walked, yaw_rad, capture_idx)",
    ]
    for needle in required_body:
        assert needle in body, needle

    required_seed = [
        "ActorState.new()",
        'actor_state.visual_presentation_kind = "idle_walk"',
        'actor_state.visual_composite_key = "player-0000"',
        'actor_state.visual_frame_index = frame_idx % 16',
        '"baked_visual_key": "player-0000"',
        'layer_stack.apply_actor_state(actor_state)',
    ]
    for needle in required_seed:
        assert needle in seed_body, needle

    forbidden_body = [
        "player.set_process(false)",
    ]
    for needle in forbidden_body:
        assert needle not in body, needle
