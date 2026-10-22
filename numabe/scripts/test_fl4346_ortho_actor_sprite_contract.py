#!/usr/bin/env python3
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes

import json
import math
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
WORLD = ROOT / "godot_project" / "scripts" / "World.gd"
MAIN = ROOT / "godot_project" / "scripts" / "Main.gd"
STACK = ROOT / "godot_project" / "scripts" / "visual" / "ActorLayerStack.gd"
EFFECT = ROOT / "godot_project" / "render" / "api" / "ASCIICompositorEffect.gd"
SCENE_DB = ROOT / "godot_project" / "render" / "scene" / "SceneGeometryDB.gd"
PRODUCT = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"
COMPOSITOR = ROOT / "godot_project" / "render" / "ascii" / "raster" / "ascii_compositor.glsl"
BAKER = ROOT / "scripts" / "bake_xp_sprite_for_runtime.py"
ACTIVE_ACTOR_SIDECARS = [
    ROOT / "godot_project" / "assets" / "xp_sprites" / name
    for name in (
        "player-0000.json",
        "player-body.json",
        "player-armor-regular.json",
        "player-helmet-regular.json",
        "player-weapon-sword.json",
        "player-weapon-crossbow.json",
        "attack-0001.json",
        "attack-body.json",
        "attack-weapon-sword.json",
        "attack-armor-regular.json",
        "attack-helmet-regular.json",
    )
]
SCALE_REFERENCE_SIDECARS = [
    ROOT / "godot_project" / "assets" / "xp_sprites" / name
    for name in (
        "player-0000.json",
        "player-body.json",
        "plydie-0000.json",
        "plydie-body.json",
    )
]
ACTIVE_ATTACK_SIDECARS = [
    ROOT / "godot_project" / "assets" / "xp_sprites" / name
    for name in (
        "attack-0001.json",
        "attack-body.json",
        "attack-weapon-sword.json",
        "attack-armor-regular.json",
        "attack-helmet-regular.json",
    )
]


def _expected_anchor(sidecar: dict, frame: dict) -> tuple[float, float]:
    frame_rect = frame["frame_rect_px"]
    crop_x = int(frame_rect[0]) - int(frame["frame"]) * int(sidecar["frame_w_px"])
    crop_y = int(frame_rect[1]) - int(frame["yaw"]) * int(sidecar["frame_h_px"])
    return (
        float(sidecar["frame_w_px"]) * 0.5
        - crop_x,
        float(sidecar["frame_h_px"] - crop_y),
    )


def _anchor_contract_errors(sidecar: dict, sidecar_name: str) -> list[str]:
    errors: list[str] = []
    frames = sidecar["compositor_frames"]
    if any(tuple(row.get("anchor_ref_px", [])) != _expected_anchor(sidecar, row) for row in frames):
        errors.append("anchor_ref_px")
    if any("contact_anchor_px" in row for row in frames):
        errors.append("contact_anchor_px")
    return errors


def _body_depth_contract_errors(depth_source: str) -> list[str]:
    errors: list[str] = []
    if "product_vsb_frame.camera_forward_far.xyz" not in depth_source:
        errors.append("missing_vsb_camera_facing_plane")
    if "dot(row_foot.world - ray.near_world, normal) / denominator" not in depth_source:
        errors.append("missing_per_ray_plane_depth")
    if "actor_billboard_plane_hit_for_ray(row_foot, ray)" in depth_source:
        errors.append("actor_facing_standee_reowns_body_depth")
    if "depth_key_for_world(ray, row_foot.world)" in depth_source:
        errors.append("b262_foot_point_depth")
    if ".local_uv" in depth_source:
        errors.append("depth_reowns_body_uv")
    return errors


def test_local_actor_has_no_second_compositor_position_owner() -> None:
    source = WORLD.read_text(encoding="utf-8")
    assert "_local_actor_compositor_render_position" not in source
    assert "LOCAL_ACTOR_COMPOSITOR_PRESENTATION_RATE" not in source
    assert 'row["actor_world_position"] = body_render_position' in source
    assert 'row["actor_world_position"] = authority_position' not in source
    assert 'row["actor_root_world"] = body_render_position' in source
    assert 'row["actor_water_contact"] = actor_presentation_root_is_over_water(' in source
    assert "actor_wake_world_position" not in source
    assert 'row["actor_authority_world_position"] = authority_position' in source
    assert "_local_actor_published_position_from_rows" not in source
    assert "set_player_wake_presentation_xz" in source
    assert '_ascii_effect.set_player_wake_presentation_xz(Vector2(root.x, root.z))' in source


def test_runtime_uses_the_baker_billboard_axis_without_a_contact_override() -> None:
    source = STACK.read_text(encoding="utf-8")
    assert 'resolved_frame.get("anchor_ref_px", null)' in source
    assert "var frame_canvas_w" not in source
    assert "posmod(frame_rect.x" not in source
    assert "posmod(frame_rect.y" not in source
    assert '"legacy_frame_ref_half_cells": resolved_frame.get(' not in source
    assert "anchor_cell * cell_scale + root_offset_px" not in source

    baker = BAKER.read_text(encoding="utf-8")
    assert "def stable_occupied_bounds_px(" in baker
    assert "legacy_frame_ref_half_cells = [" in baker
    assert 'geom["y_proj"]' in baker
    assert 'geom["z_proj"]' in baker
    assert '"legacy_frame_ref_half_cells": [' in baker
    assert 'frame_w_px * 0.5 - occupied_x' in baker
    assert '"billboard_yaw_axis_offsets_px":' not in baker
    assert "def _normalized_yaw_axis_offsets(" not in baker
    assert "def set_billboard_yaw_axis(" not in baker
    assert 'float(frame_h_px - occupied_y)' in baker
    assert '"anchor_ref_px": billboard_anchor_px' in baker
    assert '"contact_anchor_px": contact_anchor_px' not in baker
    assert 'frame.pop("contact_anchor_px", None)' in baker
    assert 'resolved_frame.get("contact_anchor_px", null)' not in source
    assert "actor_foot_sink_world" not in baker


def test_attack_body_crop_is_positioned_on_the_full_frame_axis() -> None:
    sidecar = json.loads(
        (ROOT / "godot_project/assets/xp_sprites/attack-0001.json").read_text(
            encoding="utf-8"
        )
    )
    frames = sidecar["compositor_frames"]
    assert {tuple(row["frame_rect_px"][2:]) for row in frames} == {(144, 144)}
    assert {float(row["anchor_ref_px"][1]) for row in frames} == {160.0}
    assert {float(row["anchor_ref_px"][0]) for row in frames} == {72.0}
    assert all("contact_anchor_px" not in row for row in frames)


def test_every_attack_crop_uses_one_bottom_centre_axis() -> None:
    for sidecar_path in ACTIVE_ATTACK_SIDECARS:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        assert sidecar.get("family") == "attack", sidecar_path.name
        for row in sidecar["compositor_frames"]:
            assert "contact_anchor_px" not in row, sidecar_path.name
            assert tuple(row["anchor_ref_px"]) == _expected_anchor(sidecar, row), (
                sidecar_path.name,
                row.get("layer_role"),
            )


def test_active_actor_sheets_keep_tight_crops_on_one_registered_axis() -> None:
    for sidecar_path in ACTIVE_ACTOR_SIDECARS:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        frames = sidecar["compositor_frames"]
        frame_sizes = {(row["frame_rect_px"][2], row["frame_rect_px"][3]) for row in frames}
        assert len(frame_sizes) == 1, sidecar_path.name
        frame_w, frame_h = next(iter(frame_sizes))
        assert _anchor_contract_errors(sidecar, sidecar_path.name) == [], sidecar_path.name
        for row in frames:
            assert tuple(row["anchor_ref_px"]) == _expected_anchor(sidecar, row), sidecar_path.name
        assert frame_w <= sidecar["frame_w_px"], sidecar_path.name
        assert frame_h <= sidecar["frame_h_px"], sidecar_path.name
        # FL-4346 size bump (57f15af05): the shipping bake authors 0.126 world
        # units per XP cell, so 16-pixel cells give 0.007875 per raster pixel.
        assert sidecar["actor_pixel_size_world"] == 0.007875, sidecar_path.name
        assert "actor_foot_sink_world" not in sidecar, sidecar_path.name


def test_player_and_death_sheets_share_baseline_a_cell_scale() -> None:
    for sidecar_path in SCALE_REFERENCE_SIDECARS:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        assert sidecar["cell_scale_px"] == 16, sidecar_path.name
        # FL-4346 size bump (57f15af05): 0.126 world units per authored XP cell.
        assert sidecar["actor_pixel_size_world"] == 0.007875, sidecar_path.name
        assert (
            sidecar["actor_pixel_size_world"] * sidecar["cell_scale_px"] == 0.126
        ), sidecar_path.name


def test_player_and_attack_layers_share_one_full_frame_billboard_axis() -> None:
    for sidecar_path in ACTIVE_ACTOR_SIDECARS:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        assert "billboard_yaw_axis_offsets_px" not in sidecar, sidecar_path.name
        by_yaw = {
            int(row["yaw"]): {
                float(frame["anchor_ref_px"][0])
                - (
                    float(sidecar["frame_w_px"]) * 0.5
                    - (
                        int(frame["frame_rect_px"][0])
                        - int(frame["frame"]) * int(sidecar["frame_w_px"])
                    )
                )
                for frame in sidecar["compositor_frames"]
                if int(frame["yaw"]) == int(row["yaw"])
            }
            for row in sidecar["compositor_frames"]
        }
        assert all(len(offsets) == 1 for offsets in by_yaw.values()), sidecar_path.name
        assert tuple(next(iter(by_yaw[yaw])) for yaw in range(8)) == (0.0,) * 8, (
            sidecar_path.name
        )


def test_png_idle_body_ink_cannot_redefine_the_frame_axis() -> None:
    sidecar = json.loads(
        (ROOT / "godot_project/assets/xp_sprites/player-body.json").read_text(
            encoding="utf-8"
        )
    )
    atlas = Image.open(ROOT / "godot_project/assets/xp_sprites/player-body.png").convert(
        "RGBA"
    )
    frame_w = int(sidecar["frame_w_px"])
    frame_h = int(sidecar["frame_h_px"])
    measured = []
    for yaw in range(int(sidecar["angles"])):
        alpha = atlas.crop((0, yaw * frame_h, frame_w, (yaw + 1) * frame_h)).getchannel(
            "A"
        )
        bounds = alpha.getbbox()
        assert bounds is not None
        measured.append((float(bounds[0] + bounds[2]) * 0.5) - frame_w * 0.5)
    assert tuple(measured) == (0.0, -4.0, 0.0, -4.0, 0.0, 4.0, 0.0, 4.0)
    assert any(offset != 0.0 for offset in measured)


def test_body_candidate_crops_do_not_expand_to_transparent_canvas_bounds() -> None:
    root = ROOT / "godot_project/assets/xp_sprites"
    player = json.loads((root / "player-body.json").read_text(encoding="utf-8"))
    attack = json.loads((root / "attack-body.json").read_text(encoding="utf-8"))
    assert {tuple(row["frame_rect_px"][2:]) for row in player["compositor_frames"]} == {(72, 128)}
    assert {tuple(row["frame_rect_px"][2:]) for row in attack["compositor_frames"]} == {(144, 144)}


def test_off_axis_and_attack_override_payloads_are_rejected() -> None:
    for sidecar_path in ACTIVE_ACTOR_SIDECARS:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        for frame in sidecar["compositor_frames"]:
            frame["anchor_ref_px"][0] += 4.0
            frame["contact_anchor_px"] = list(frame["anchor_ref_px"])
        errors = _anchor_contract_errors(sidecar, sidecar_path.name)
        assert "anchor_ref_px" in errors, sidecar_path.name
        assert "contact_anchor_px" in errors, sidecar_path.name


def test_live_bee_layers_are_distinct_and_keep_compiled_roles() -> None:
    base = json.loads(
        (ROOT / "godot_project/assets/xp_sprites/bigbee-0000.json").read_text(
            encoding="utf-8"
        )
    )
    body = json.loads(
        (ROOT / "godot_project/assets/xp_sprites/bigbee-0000-L3.json").read_text(
            encoding="utf-8"
        )
    )
    assert base["source_layer_index"] == 2
    assert body["source_layer_index"] == 3
    assert {row["layer_role"] for row in base["compositor_frames"]} == {"mount_rear"}
    assert {row["layer_role"] for row in body["compositor_frames"]} == {"body"}
    assert base["atlas_png_sha256"] != body["atlas_png_sha256"]


def test_actor_body_normalizes_default_size_and_retains_depth_and_zoom() -> None:
    source = EFFECT.read_text(encoding="utf-8")
    world = WORLD.read_text(encoding="utf-8")
    player = (ROOT / "godot_project/scripts/player.gd").read_text(encoding="utf-8")
    projection = source.split(
        "func _project_actor_sprite_frame_rect_queue", 1
    )[1].split("func _pack_actor_sprite_frame_rect_queue", 1)[0]
    helper = source.split(
        "func _actor_billboard_pixels_per_world_from_screen", 1
    )[1].split("func _project_actor_sprite_frame_rect_queue", 1)[0]
    # FL-4346 (operator order, 2026-08-04): the scale is read from an
    # explicitly present row key, never a .get() default.
    assert (
        'var sprite_pixel_size_world := float(row["actor_sprite_pixel_size_world"])'
        in projection
    )
    assert "_actor_world_vertical_billboard_right(projection_ctx)" in helper
    assert "var right_step_projected" in helper
    assert "var up_step_projected" in helper
    assert "right_step_px.distance_to(body_screen)" in helper
    assert "up_step_px.distance_to(body_screen)" in helper
    assert "absf(right_step_px.x - body_screen.x)" not in projection
    assert "absf(up_step_px.y - body_screen.y)" not in projection
    assert "actor_presentation_camera_contract_valid" in projection
    assert "var canonical_pixels_per_world := target_to_presentation_scale" in projection
    assert "var presentation_world_scale := Vector2(" in projection
    assert "var source_pixel_world_size := Vector2(" in projection
    assert "var source_pixel_screen_size := pixels_per_world * source_pixel_world_size" not in projection
    assert "var pixels_per_world := reference_pixels_per_world" not in projection
    assert "float(frame_rect.z) * source_pixel_world_size.x" in projection
    assert "float(frame_rect.w) * source_pixel_world_size.y" in projection
    assert "var plane_offsets := [" in projection
    assert "top_left_world" not in projection
    assert "bottom_right_world" not in projection
    assert "func get_actor_presentation_world_per_presentation_pixel() -> float:" in player
    assert "zoom_ratio /= maxf(_perspective_default_zoom_scale(), 0.0001)" in player
    assert "zoom_ratio *= _pilot_zoom_factor" in player
    assert "CAMERA_SIZE_PER_VIEWPORT_PIXEL * maxf(zoom_ratio, 0.0001)" in player
    assert '"set_actor_presentation_camera_contract"' in world
    assert '(player_node as Node3D).global_position' in world

    # At the reference actor, mode-specific projected scale cancels exactly. The
    # remaining camera contract is inverse zoom, so zoom-in grows monotonically.
    sprite_pixel_size_world = 0.0075
    target_to_presentation = 113.0 / 2252.0
    for reference_ppw in (1.8, 2.3, 4.7):
        for zoom_ratio in (0.85, 1.0, 1.0 / 0.85):
            canonical_ppw = target_to_presentation / ((18.0 / 800.0) * zoom_ratio)
            world_scale = canonical_ppw / reference_ppw
            result = reference_ppw * sprite_pixel_size_world * world_scale
            assert abs(result - canonical_ppw * sprite_pixel_size_world) < 1e-12


def test_body_scheduler_projects_the_exact_geometry_derived_billboard_corners() -> None:
    source = EFFECT.read_text(encoding="utf-8")
    projection = source.split(
        "func _project_actor_sprite_frame_rect_queue", 1
    )[1].split("func _pack_actor_sprite_frame_rect_queue", 1)[0]
    assert "var body_projected := _project_actor_world_to_pixel(projection_ctx, world_pos)" in projection
    assert "var body_screen: Vector2 = body_projected.get(\"px\", Vector2.ZERO)" in projection
    assert 'var root_world: Vector3 = row.get("actor_root_world", world_pos)' in projection
    assert "var card_bottom_gap_world := maxf(anchor_world.y - sprite_world_size.y, 0.0)" in projection
    assert "var water_immersion_world := 0.5 * sprite_world_size.y" in projection
    assert 'if bool(row.get("actor_water_contact", false)) else 0.0' in projection
    assert "var billboard_vertical_offset_world := card_bottom_gap_world + water_immersion_world" in projection
    assert "Vector3.UP * billboard_vertical_offset_world" in projection
    assert "var anchor_screen_px := anchor_px * source_pixel_screen_size" not in projection
    assert "var min_px := body_screen - anchor_screen_px" not in projection
    assert "var foot_projected :=" not in projection
    assert "actor_foot_sink_screen_delta_px" not in projection
    assert "var corner_world := billboard_origin" in projection
    assert "billboard_right * plane_offset.x" in projection
    assert "Vector3.UP * plane_offset.y" in projection
    assert "screen_right_world" not in projection


def test_orthographic_camera_aims_at_the_same_actor_root_that_qe_orbits() -> None:
    source = (ROOT / "godot_project/scripts/player.gd").read_text(encoding="utf-8")
    assert "_aim_orthographic_camera_at_actor_root()" in source
    helper = source.split("func _aim_orthographic_camera_at_actor_root() -> void:", 1)[1].split(
        "func _record_springarm_slope_lift", 1
    )[0]
    assert "camera.look_at(global_position, Vector3.UP)" in helper


def test_f10_anchor_pole_is_analytic_actor_debug_only() -> None:
    source = EFFECT.read_text(encoding="utf-8")
    world = WORLD.read_text(encoding="utf-8")
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    assert "func is_fl4346_actor_reference_marker_enabled() -> bool:" in source
    assert 'OS.get_cmdline_user_args().has("--fl4346-actor-reference-marker")' in source
    assert 'OS.get_environment("FL4346_ACTOR_REFERENCE_MARKER") == "1"' in source
    assert "func _append_dynamic_actor_reference_marker(" not in source
    assert '"actor_reference_marker": true' not in world
    assert "actor_reference_marker_faces" not in world
    assert "vec4 compositor_actor_card_debug_ink(" in compositor
    assert "bool on_anchor_pole" in compositor
    assert "float pole_base_y = actor_water_surface_world_y() - hit.billboard_origin.y;" in compositor
    assert "float pole_top_y = anchor.y;" in compositor
    assert "p.y >= pole_base_y - half_w" in compositor
    assert "p.y >= -half_w" not in compositor
    assert "return vec4(0.0, 1.0, 1.0, 1.0);" in compositor


def test_pixel_camera_rotation_and_zoom_invalidate_actor_projection_cache() -> None:
    source = EFFECT.read_text(encoding="utf-8")
    snap = source.split("func _pixel_camera_snap_state_from_projection", 1)[1].split(
        "func _pixel_camera_live_true_perspective_projection", 1
    )[0]
    assert "elif not _pixel_camera_grid.basis.is_equal_approx(cam_transform.basis):" in snap
    assert snap.count("_pixel_camera_grid = cam_transform") == 1
    assert "_pixel_camera_grid.basis = cam_transform.basis" in snap
    assert "retain its world-space phase origin" in snap
    assert "PIXEL_CAMERA_GRID_BASIS_DOT_THRESHOLD" not in source
    assert "_pixel_camera_basis_changed" not in source
    context = source.split("func _build_pixel_camera_context", 1)[1].split(
        "func _pixel_camera_capture_contract", 1
    )[0]
    assert "str(ctx.world_to_clip)" in context
    assert "ctx.snap_world" in context
    assert "ctx.camera_fov" in context
    assert "ctx.camera_near" in context
    assert "ctx.camera_far" in context


def test_f10_has_no_product_owned_marker_style() -> None:
    shader = _expand_includes((ROOT / "godot_project/render/product/cell_owner_product.glsl").read_text(encoding="utf-8"))
    style = shader.split("void product_apply_bonfire_dynamic_style(", 1)[1].split(
        "const uint ACTOR_REJECT_RECT_OVERLAP", 1
    )[0]
    assert "(source_id & 0xFF0000u) == 0x7F0000u" not in style
    assert "uint marker_part = source_id & 3u;" not in style
    assert "marker_part == 3u" not in style


def test_f10_card_debug_draws_red_border_and_cyan_pole_before_alpha_gate() -> None:
    """FL-4346 F10 card debug (operator order 2026-08-04).

    While F10 is active the compositor outlines the billboard card in RED. The
    CYAN pole starts at the actor root's XZ on the descriptor-owned water surface
    and extends only along positive world Y. It is judged before the body-alpha
    gate, so sprite content cannot hide the geometry under test.
    """
    compositor = (ROOT / "godot_project/render/ascii/raster/ascii_compositor.glsl").read_text(
        encoding="utf-8"
    )
    ink = compositor.split("vec4 compositor_actor_card_debug_ink(", 1)[1].split(
        "CompositorActorFragment compositor_actor_fragment_for_cell(", 1
    )[0]
    # The crop can be asymmetric around the authored root. Red must use the
    # actual plane-space pivot column so it can overlap the independent blue
    # world-root line; the geometric crop center is a forbidden second owner.
    assert "float center_x = world_size.x * 0.5 - anchor.x;" not in ink
    assert "float pole_base_y = actor_water_surface_world_y() - hit.billboard_origin.y;" in ink
    assert "float pole_top_y = anchor.y;" in ink
    assert "bool on_anchor_pole = abs(p.x) <= half_w" in ink
    assert "p.y >= pole_base_y - half_w" in ink
    assert "p.y >= -half_w" not in ink
    assert "max(anchor.y, world_size.y)" not in ink
    assert "if (on_anchor_pole) return vec4(0.0, 1.0, 1.0, 1.0);" in ink
    assert "if (edge_distance <= half_w) return vec4(1.0, 0.0, 0.0, 0.78);" in ink
    loop = compositor.split(
        "CompositorActorFragment compositor_actor_fragment_for_cell(", 1
    )[1].split("#ifdef FL4431_FINAL_CELL_CAPTURE", 1)[0]
    # Bit 26 is evaluated before body alpha; the deleted receiver duel cannot
    # return as an earlier visibility authority.
    card_gate = loop.index(">> 26u) & 1u")
    body_alpha = loop.index("bool body_visible")
    assert card_gate < body_alpha
    assert "actor_hit_is_in_front_of_world(" not in loop
    # FL-4346: the world depth gate restored in this loop is NOT the deleted
    # veto. Positively stated law: it runs strictly AFTER the shared validity
    # boundary, so it can never become an earlier visibility authority, and it
    # sits after the F10 card-debug branch so diagnostic witness ink is never
    # hidden by occlusion.
    depth_gate = loop.index("fl4346_actor_survives_world_depth(")
    validity_boundary = loop.index("if (!hit.valid) continue;")
    assert card_gate < validity_boundary < depth_gate
    # Actor ink is never remapped from the receiver, so witness colors remain exact.
    card_branch = loop.split(">> 26u) & 1u", 1)[1].split(
        "if (!hit.valid", 1
    )[0]
    assert "fragment.valid = true;" in card_branch
    debug_ink = compositor.split(
        "vec4 compositor_actor_card_debug_ink(", 1
    )[1].split(
        "CompositorActorFragment compositor_actor_fragment_for_cell", 1
    )[0]
    assert "actor_water_surface_world_y() - hit.billboard_origin.y" in debug_ink
    assert "pole_top_y" in debug_ink
    assert "diagnostic_tint" not in compositor
    assert "receiver_luma" not in compositor
    assert "actor_luma" not in compositor
    effect = EFFECT.read_text(encoding="utf-8")
    pack = effect.split("func _pack_actor_source_key(", 1)[1].split(
        "func _pack_actor_slash_rig_params(", 1
    )[0]
    # The card bit rides col15 bit 26 and is gated by the F10 debug flag for
    # EVERY actor row (human debug mode, not the single-subject falsifier).
    assert "if fl4346_actor_reference_marker:" in pack
    assert "key |= 1 << 26" in pack


def test_f10_pole_does_not_publish_world_marker_geometry() -> None:
    shader = _expand_includes((ROOT / "godot_project/render/product/cell_owner_product.glsl").read_text(
        encoding="utf-8"
    ))
    compositor = (ROOT / "godot_project/render/ascii/raster/ascii_compositor.glsl").read_text(
        encoding="utf-8"
    )
    effect = EFFECT.read_text(encoding="utf-8")
    world = WORLD.read_text(encoding="utf-8")
    assert "func _append_dynamic_actor_reference_marker(" not in effect
    assert "actor_reference_marker_faces" not in world
    assert '"actor_reference_marker": true' not in world
    assert 'row["actor_reference_marker_enabled"]' not in world
    assert 'row.get("actor_reference_marker_enabled", false)' not in effect
    assert "ACTOR_SOURCE_REFERENCE_MARKER_BIT" not in shader
    assert "actor_reference_marker_cell_x01(" not in shader
    assert "actor_reference_marker_overlay" not in shader
    assert "actor_reference_marker_x01" not in shader
    assert "(product.r1.w & 0x80000000u)" not in compositor
    assert "actor_reference_marker_x01" not in compositor
    assert "marker_distance" not in compositor
    assert "actor_reference_marker_overlay ? (1u << 20u) : 0u" not in compositor


def test_live_wake_endpoint_stays_in_the_ascii_product_path() -> None:
    world = WORLD.read_text(encoding="utf-8")
    effect = EFFECT.read_text(encoding="utf-8")
    wake_field = (ROOT / "godot_project/render/effects/water/WakeTrailField.gd").read_text(encoding="utf-8")
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    assert "actor_wake_contact_enabled" not in world
    assert "actor_wake_contact_enabled" not in effect
    pack = effect.split("func _pack_actor_source_key(", 1)[1].split(
        "func _pack_actor_slash_rig_params(", 1
    )[0]
    assert 'if bool(row.get("actor_water_contact", false)):' in pack
    assert "key |= 1 << 24" in pack
    assert "actor_wake_contact" not in pack
    assert "func set_player_wake_presentation_xz(" in effect
    setter = effect.split("func set_player_wake_presentation_xz(", 1)[1]
    setter = setter.split("\nfunc ", 1)[0]
    assert "wake_trail.set_presentation_xz(xz)" in setter
    wake_setter = wake_field.split("func set_presentation_xz(", 1)[1].split("\nfunc ", 1)[0]
    assert "clock_data[2] = xz.x" in wake_setter
    assert "clock_data[3] = xz.y" in wake_setter
    wake_body = product.split("ProductWakeFact product_wake_fact", 1)[1]
    wake_body = wake_body.split("vec2 product_terrain_world_xz", 1)[0]
    assert "wake_clock.value.zw" in wake_body
    assert "point_source_id == WAKE_SOURCE_PLAYER && is_latest_sample" in wake_body
    assert "root_distance" not in wake_body
    assert "compositor_actor_wake_contact" not in compositor
    assert "wake_contact_rgba" not in compositor
    assert "wake_contact_valid" not in compositor
    scheduler = product.split("bool actor_attached_effect_schedules_cell(", 1)[1]
    scheduler = scheduler.split("float actor_candidate_view_depth", 1)[0]
    assert "wake_contact_active" not in scheduler
    assert "contact_radii" not in scheduler


def test_water_actor_uses_one_descriptor_derived_presentation_root() -> None:
    main = MAIN.read_text(encoding="utf-8")
    world = WORLD.read_text(encoding="utf-8")
    scene_db = SCENE_DB.read_text(encoding="utf-8")
    update = main.split("func _update_local_player_presentation", 1)[1].split(
        "func _emit_fl4359_lerp_receipt", 1
    )[0]
    assert "player.global_position = target_position" not in update
    # FL-4346: the local player no longer maps and writes its own position in
    # Main.gd. Both steps moved into World.place_presented_actor, which is now the
    # single owner that ALSO places remote players and NPCs - previously those two
    # were dropped at the raw server height and skipped the water rule entirely.
    # Main.gd must therefore call the owner and must not perform its own write.
    assert "var presentation_root := world.place_presented_actor(player, target_position)" in update
    assert "player.global_position" not in update
    assert '"presentation_surface_offset": presentation_position - interpolated_authority_position' in main
    assert "func water_surface_y_at_world_xz(world_xz: Vector2) -> float:" in scene_db
    assert "var low := 0" in scene_db
    assert "var high := spans.size() - 1" in scene_db
    assert "func actor_presentation_root_world(authority_position: Vector3) -> Vector3:" in world
    assert "water_surface_y_at_world_xz" in world
    assert "return Vector3(authority_position.x, water_surface_y, authority_position.z)" in world
    assert 'row["actor_water_contact"] = actor_presentation_root_is_over_water(' in world
    assert '_ascii_effect.set_player_wake_presentation_xz(Vector2(root.x, root.z))' in world
    # FL-4346 single placement owner: exactly one function writes an actor's
    # visible position, and all three appliers (local player, remote players,
    # NPCs) reach it. ReplicatedActor must no longer place its own node.
    assert "func place_presented_actor(node: Node3D, raw_authority_position: Vector3) -> Vector3:" in world
    assert "\tnode.global_position = presentation_root" in world
    assert world.count("node.global_position = presentation_root") == 1
    replicated = (WORLD.parent / "net" / "ReplicatedActor.gd").read_text(encoding="utf-8")
    assert "position = state.position" not in replicated
    # Both replicated appliers must route through the shared placement helper.
    assert "func _place_replicated_actor_from_state(node: Node) -> void:" in world
    assert world.count("_place_replicated_actor_from_state(node)") == 2


def test_card_offset_is_geometry_derived_and_not_an_authored_second_root() -> None:
    stack = STACK.read_text(encoding="utf-8")
    effect = EFFECT.read_text(encoding="utf-8")
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    assert "actor_foot_sink_world" not in stack
    assert "actor_sprite_foot_sink_world" not in stack
    projection = effect.split(
        "func _project_actor_sprite_frame_rect_queue", 1
    )[1].split("func _pack_actor_sprite_frame_rect_queue", 1)[0]
    assert "var card_bottom_gap_world := maxf(anchor_world.y - sprite_world_size.y, 0.0)" in projection
    assert "var water_immersion_world := 0.5 * sprite_world_size.y" in projection
    assert "var billboard_vertical_offset_world := card_bottom_gap_world + water_immersion_world" in projection
    assert "var corner_world := billboard_origin" in projection
    pack = effect.split("func _pack_actor_sprite_frame_rect_queue", 1)[1].split(
        "func _actor_presentation_kind_id", 1
    )[0]
    assert "col5 low16: geometry-derived root-to-card vertical offset" in pack
    assert 'float(row.get("actor_billboard_vertical_offset_world", 0.0)), 0.0' in pack
    assert "compositor_actor_vertical_offset_world(int row)" in compositor
    assert "compositor_actor_foot_sink_world" not in compositor
    assert "hit.billboard_origin = hit.root" in compositor
    assert "compositor_actor_water_contact(hit.row)" in compositor
    assert "bool actor_hit_survives_water_surface(CompositorActorHit hit)" in compositor
    assert "hit.world.y >= actor_water_surface_world_y()" in compositor
    assert "bool hit_above_water = actor_hit_survives_water_surface(hit);" in compositor
    assert "actor_body_survives_waterline" not in compositor
    assert "actor_effect_survives_waterline" not in compositor
    assert "ACTOR_SINK_FULL_DEPTH" not in compositor
    assert "ACTOR_SINK_WATERLINE_SHALLOW" not in compositor
    assert "ACTOR_SINK_WATERLINE_DEEP" not in compositor
    assert "actor_hit_is_in_front_of_world(" not in compositor
    assert "binding = 48" not in compositor
    # FL-4346: the world depth the gate reads rides the EXISTING 80-byte product
    # record's spare anchor lane. No quarter-cell receiver buffer comes back.
    assert "float world_ray_t = product.anchor.w;" in compositor


def test_review_surface_cannot_draw_a_counterfeit_f10_marker() -> None:
    runner = (ROOT / "scripts/run_fl4346_actor_presentation_proof.py").read_text(
        encoding="utf-8"
    )
    annotate = runner.split("def _annotate(", 1)[1].split(
        "def _panel", 1
    )[0]
    assert "marker_anchor" not in annotate
    assert "draw.ellipse" not in annotate
    assert "draw.rectangle" not in annotate
    assert "draw.line" not in annotate
    assert "def _actor_wake_crop(" in runner
    assert 'case_dir / "temporal_close_grid.png"' in runner
    assert 'case_dir / "motion_close_grid.png"' in runner
    assert "Actual gameplay frame: F10 off; no diagnostic overlay" in runner
    assert "return image.copy()" in annotate
    assert "F10 compositor diagnostic: red card border + cyan actor-anchor pole" not in runner
    assert "osascript" not in runner


def test_actor_lut_resolution_cannot_cache_whole_rows_behind_artwork_identity() -> None:
    source = EFFECT.read_text(encoding="utf-8")
    assert "_actor_resolved_source_rows_cache" not in source
    assert "_resolve_actor_sprite_rows_for_pixel_context_cached" not in source
    publish_body = source.split(
        "func _publish_actor_sprite_source_rows_for_scene(", 1
    )[1].split("\nfunc ", 1)[0]
    assert "_resolve_actor_sprite_rows_for_pixel_context(" in publish_body
    assert "_pending_actor_sprite_source_rows, pixel_ctx" in publish_body


def test_qe_f10_driver_requires_water_axis_trace_and_zoom() -> None:
    driver = (ROOT / "scripts/fl4346_actor_presentation_driver.gd").read_text(encoding="utf-8")
    runner = (ROOT / "scripts/run_fl4346_actor_presentation_proof.py").read_text(encoding="utf-8")
    assert "func _require_authoritative_surface_spawn(" in driver
    assert "func _move_into_authoritative_water(" not in driver
    assert '"authoritative_surface_entry_not_observed"' in driver
    assert "const POST_WATER_RENDER_SETTLE_FRAMES := 12" in driver
    assert "for _frame in range(POST_WATER_RENDER_SETTLE_FRAMES):" in driver
    assert "func _wait_for_stationary_actor(player: Node3D) -> Dictionary:" in driver
    assert '"surface_entry_did_not_settle"' in driver
    assert "POST_WATER_STATIONARY_FRAMES := 30" in driver
    assert "POST_WATER_STEP_MAX_DELTA := 0.01" in driver
    assert '"actor_dead_at_server_spawn"' in driver
    assert '"actor_dead_during_q"' in driver
    assert '"actor_dead_during_e"' in driver
    assert '"%s_%s_sequence_rejected"' not in driver
    assert "const EXPECTED_CAPTURE_IMAGE_COUNT := 16" in driver
    assert "_screenshots.size() == EXPECTED_CAPTURE_IMAGE_COUNT" in driver
    assert '_press_action(&"move_forward", KEY_W)' in driver
    assert '_release_action(&"move_forward", KEY_W)' in driver
    assert 'var wake_lay := await _lay_real_wake(world, player)' in driver
    assert 'var stationary_entry := await _wait_for_stationary_actor(player)' in driver
    assert driver.index('var wake_lay := await _lay_real_wake(world, player)') < driver.index(
        'var stationary_entry := await _wait_for_stationary_actor(player)'
    )
    assert "sample_mat_id_at_godot_xz" in driver
    assert "mat_id == 140 or height <= 3.0625" in driver
    assert "func _motion_trace_summary(" in driver
    assert "func _drive_turn_sweep_with_trace(" in driver
    assert '"rotate_right", KEY_E, "e", phase_prefix, q_turn_radians, false' in driver
    assert "_repeat_held_key(keycode)" in driver
    assert "event.echo = true" in driver
    assert "q_sweep_complete" in driver
    assert "e_return_complete" in driver
    assert "const SWING_MOTION_CAPTURE_COUNT := 2" in driver
    assert "swing_motion_count == SWING_MOTION_CAPTURE_COUNT" in driver
    assert "const SWING_TURN_MIN_RADIANS := 0.02" in driver
    assert "func _wait_for_attack_presentation(" not in driver
    assert "func _wait_for_attack_swoosh_presentation(" in driver
    assert '"server_attack_swoosh_presentation_missing"' in driver
    assert '_press_action(&"rotate_left", KEY_Q)' in driver
    assert '_release_action(&"rotate_left", KEY_Q)' in driver
    assert 'float(input_state.get("rotate_left", 0.0)) > 0.0' in driver
    assert "func _return_camera_to_yaw(player: Node3D, target_yaw: float)" in driver
    assert '"swing_camera_turn_complete": swing_camera_turn_complete' in driver
    assert '"slash_ribbon_active": bool(' in driver
    assert '"swing_swoosh_motion_capture_count": swing_swoosh_motion_count' in driver
    assert "swing_swoosh_motion_count >= 1" in driver
    assert "axis_aligned" in driver
    assert "STATIONARY_WORLD_MAX_DELTA" in driver
    assert '"actor_stationary"' in driver
    assert '"max_player_world_delta"' in driver
    assert "KEY_EQUAL" in driver
    assert "KEY_MINUS" in driver
    assert "func _zoom_summary(" in driver
    assert "world_scaled" in driver
    assert "ZOOM_MIN_SIZE_GROWTH_PX" in driver
    assert "ZOOM_RETURN_MAX_SIZE_DELTA_PX" in driver
    assert "func _actor_row_contract_summary(" in driver
    assert '"owner": "compositor_actor_row"' in driver
    assert '"presentation_diagnostics_enabled": false' in driver
    first_png = driver.index("image.save_png(image_path)")
    first_f10 = driver.index('_press_action(&"debug_actor_reference", KEY_F10)', first_png)
    assert first_f10 > first_png
    assert "final_actor_reference_marker_count" not in driver
    assert "func _final_marker_row_is_near_root" not in driver
    assert "selected_cell_product_payload" not in driver
    assert "MARKER_FINAL_ROW_MAX_DISTANCE_PX" in driver
    assert "MARKER_STEM_AXIS_MAX_ERROR_PX" in driver
    assert '"--uncapped"' in runner
    assert '"--projection-modes"' in runner
    assert '"orthographic,perspective"' in runner
    assert '"00_entry"' in runner
    assert '"01_q_45"' in runner
    assert '"02_q_90"' in runner
    assert '"03_e_return"' in runner
    assert '"04_zoom_in"' in runner
    assert '"05_zoom_return"' in runner
    assert '"06_swing_start"' in runner
    assert '"07_swing_return"' in runner
    assert 'PERF_BASELINES' in runner
    assert '"orthographic": {"avg_fps": 89.983, "p95_delta_ms": 11.408}' in runner
    assert '"perspective": {"avg_fps": 103.838, "p95_delta_ms": 10.434}' in runner
    assert "asciicker-Y9-2-codex-runnable-20260801" not in runner
    assert '"orthographic"' in driver
    assert '"perspective"' in driver
    assert "func _run_projection_sequence(" in driver
    assert '"surface": "ground"' not in runner
    assert '"surface": "water"' in runner
    assert "def _find_all_yaw_water_pose() -> dict:" in runner
    assert "WATER_CAMERA_ORBIT_RADIUS = 33.0" in runner
    assert "WATER_CAMERA_RAY_HALF_WIDTH = 1.0" in runner
    assert "237.5" not in runner
    assert "-255.5" not in runner
    assert 'env["PLAYER_SPAWN_X"]' in runner
    assert 'env["PLAYER_SPAWN_Z"]' in runner


def test_m_is_the_board_mount_key_and_e_only_turns() -> None:
    """M boards; E turns and does nothing else. One job per key.

    HISTORY, because this test has now asserted three different things:
      - commit 68358f70a moved boarding from E to M ("reserve E for camera
        orbit"), on the theory that a Q/E turn could accidentally attach the
        actor to a nearby vehicle;
      - commit 1231343eb put boarding BACK on E at the operator's request, and
        added a contextual split so E boarded only when a vehicle was in range
        and otherwise still turned the camera;
      - the operator ruling of 2026-08-06 settles it on M and SUPERSEDES
        1231343eb.

    Why the contextual split had to go: while the player is actually piloting an
    airplane the plane is permanently "in range", so the split resolved E to
    boarding on every single frame and steering with E stopped working. That is
    the operator's "E is broken on the airplane" report. The fix is not a better
    tie-breaker, it is removing the tie: E turns, M boards, no overlap.

    This test therefore guards BOTH halves - that M owns boarding, and that the
    E-suppression machinery is genuinely gone rather than dormant.
    """
    world = WORLD.read_text(encoding="utf-8")
    player = (ROOT / "godot_project/scripts/player.gd").read_text(encoding="utf-8")
    controls = (ROOT / "godot_project/scripts/ControlsRegistry.gd").read_text(encoding="utf-8")
    # Boarding dispatches from a match arm keyed on M alone.
    assert "KEY_M:\n\t\t\tif _mounted_on_airplane" in world
    assert "or _near_airplane_pickup" in world
    assert "or _near_boat_pickup" in world
    # E must no longer reach the boarding arm by any route.
    assert "KEY_E, KEY_M:" not in world
    assert "KEY_M, KEY_E:" not in world
    # The contextual-split helpers must be DELETED, not merely unused: a dormant
    # copy could be re-wired and would silently cancel E-turns near a vehicle.
    # Match on the DEFINITION and the CALL SITE, not on any mention of the name:
    # both files intentionally keep a tombstone comment naming these helpers so a
    # future reader learns why they went away, and that prose must stay legal.
    assert "func is_e_mount_interaction_available() -> bool:" not in world
    assert "func _e_key_is_mount_interaction() -> bool:" not in player
    assert "and _e_key_is_mount_interaction():" not in player
    assert 'world.call("is_e_mount_interaction_available")' not in world
    # E-turn handling itself is untouched: the rotate axis still subtracts the
    # rotate_right strength, and nothing zeroes that strength any more.
    assert "Input.get_action_strength(\"rotate_left\") - rotate_right_strength" in player
    assert "rotate_right_strength = 0.0" not in player
    # The printed keymap must advertise M, since that is what players now press.
    assert '{"key": KEY_M,      "label": "Board / mount"}' in controls
    assert '{"key": KEY_E,      "label": "Board / mount"}' not in controls


def test_colyseus_movement_sends_release_once_without_tick_flood() -> None:
    player = (ROOT / "godot_project/scripts/player.gd").read_text(encoding="utf-8")
    body = player.split("func _process_colyseus_intent_only() -> void:", 1)[1].split(
        "## FL-4265: airplane pilot intent.", 1
    )[0]
    assert "_last_sent_colyseus_move_intent" in player
    assert 'payload["move"] = move_intent' in body
    assert "payload := {\"move\": move_intent}" not in body
    assert "if not payload.is_empty():" in body


def test_movement_input_has_one_inputmap_owner() -> None:
    player = (ROOT / "godot_project/scripts/player.gd").read_text(encoding="utf-8")
    body = player.split("func _movement_input() -> Vector2:", 1)[1].split(
        "func _camera_relative_walk_direction", 1
    )[0]
    assert 'return Input.get_vector("move_left", "move_right", "move_forward", "move_back")' in body
    assert "Input.is_physical_key_pressed" not in body


def test_zoom_flushes_only_idle_colyseus_move_intent() -> None:
    player = (ROOT / "godot_project/scripts/player.gd").read_text(encoding="utf-8")
    world = (ROOT / "godot_project/scripts/World.gd").read_text(encoding="utf-8")
    flush = player.split("func flush_idle_colyseus_move_intent() -> void:", 1)[1].split(
        "func _camera_relative_walk_direction", 1
    )[0]
    assert "_movement_input() != Vector2.ZERO" in flush
    assert 'main.notify_local_intent({"move": Vector2.ZERO})' in flush
    controls = world.split('"zoom_in":', 1)[1].split('"render_near":', 1)[0]
    assert controls.count("_flush_idle_player_move_intent()") == 2


def test_delete_first_removes_vsb_actor_raster_owner() -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    effect = EFFECT.read_text(encoding="utf-8")
    assert "uniform sampler2DArray actor_atlas_tex" not in source
    assert "void vsb_product_select_actor_paint_quad(" not in source
    assert "struct VsbActorPaintSelection" not in source
    assert "actor_row_rect_overlaps_cell(" not in source
    assert "actor_standee_sample_for_ray(" not in source
    assert "actor_atlas_sample(" not in source
    assert "record.r0.y = OWNER_ACTOR_SPRITE;" not in source
    assert "u_actor_atlas.binding = 39" not in effect


def test_actor_footprint_fix_does_not_revive_whole_cell_actor_flood() -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    publication = source.split(
        "// FL-4330: an ANSI cell paints one foliage glyph", 1
    )[1].split("// Exactly one b55 store site", 1)[0]
    assert "FL-4346: actor paint is cell-uniform" not in publication
    assert "staged_final_records[slot] = actor_record" not in publication
    assert "same_actor_corner" not in publication


def test_deleted_body_depth_owner_stays_absent() -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    assert "float actor_body_depth_for_ray(" not in source
    assert "vec4 actor_atlas_sample_for_subcell_footprint(" not in source
    assert "bool actor_local_uv_for_screen_sample(" not in source


def test_actor_facing_standee_plane_cannot_reown_billboard_body_depth() -> None:
    failed_depth = """
	ActorBillboardPlaneHit plane_hit =
		actor_billboard_plane_hit_for_ray(row_foot, ray);
	return plane_hit.valid ? plane_hit.depth : -1.0;
"""
    errors = _body_depth_contract_errors(failed_depth)
    assert "missing_vsb_camera_facing_plane" in errors
    assert "actor_facing_standee_reowns_body_depth" in errors


def test_b262_failed_foot_point_depth_shape_is_absent() -> None:
    failed_depth = """
	uint packed_layer = actor_cell_word(6, row_foot.row);
	return max(
		depth_key_for_world(ray, row_foot.world)
			- float((packed_layer >> 16u) & 0xFFu) * 0.0005,
		0.0);
"""
    errors = _body_depth_contract_errors(failed_depth)
    assert "missing_per_ray_plane_depth" in errors
    assert "b262_foot_point_depth" in errors


def test_delete_checkpoint_preserves_world_receiver_product() -> None:
    source = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    finalizer = source.split("ProbeSubcellProductRecord vsb_product_finalize_record(", 1)[1].split(
        "// ===========================================================================\n"
        "// FL-4431 Q3 Slice 4", 1
    )[0]
    assert "VsbActorPaintSelection" not in finalizer
    assert "record.r0.y = OWNER_ACTOR_SPRITE;" not in finalizer
    assert "vec3 effect_receiver = input_value.context.trace_receiver;" in finalizer


def test_server_facing_yaw_remains_packed_but_has_no_vsb_slash_owner() -> None:
    effect = EFFECT.read_text(encoding="utf-8")
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    pack = effect.split("func _pack_actor_sprite_frame_rect_queue", 1)[1].split(
        "func _actor_presentation_kind_id", 1
    )[0]
    assert 'float(row.get("actor_facing_yaw", 0.0))' in pack
    assert "actor_facing_yaw_u16 << 16" in pack
    assert "uint actor_direction_id(" not in product
    assert "actor_attack_swoosh_ribbon_coverage(" not in product


def test_runtime_bake_uses_bdf_for_pixels_and_product_coverage() -> None:
    source = BAKER.read_text(encoding="utf-8")
    assert "render_cell_font(" in source
    assert "render_cell_geo(" not in source
    assert '"--font"' in source
    assert "ACTOR_CELL_SIZE_WORLD = 0.12" in source
    assert '"actor_pixel_size_world": ACTOR_CELL_SIZE_WORLD / float(scale)' in source
    assert "DEFAULT_ACTOR_PIXEL_SIZE_WORLD" not in source


def test_compositor_candidate_raster_has_no_permission_slip() -> None:
    """Candidates schedule exact raster work without deciding visibility."""
    source = COMPOSITOR.read_text(encoding="utf-8")
    effect = EFFECT.read_text(encoding="utf-8")
    assert "actor_direct_paint" not in source
    assert "sample_selected_actor_atlas_from_ray(" not in source
    assert "readonly buffer ActorCandidateProduct" in source
    assert "readonly buffer ActorCellRows" in source
    assert "uniform sampler2DArray actor_atlas_tex" in source
    assert "candidate_count == 0u" in source
    candidate_loop = source.split(
        "CompositorActorFragment compositor_actor_fragment_for_cell(", 1
    )[1].split("return fragment;\n}", 1)[0]
    assert "candidate < candidate_count" in candidate_loop
    assert "row < actor_count" not in candidate_loop
    assert "u_compositor_actor_candidates.binding = 45" in effect
    assert "u_compositor_actor_rows.binding = 46" in effect
    assert "u_compositor_actor_atlas.binding = 47" in effect
    assert "u_compositor_vsb_receivers" not in effect
    assert "binding = 48" not in source


def test_quarter_center_world_receiver_cannot_veto_native_actor_pixels() -> None:
    """The exact actor hit is not filtered by a different VSB quarter ray.

    FL-4346: the deleted quarter-cell RECEIVER remains banned by name. What is
    permitted instead is a conservative depth gate that reads the world depth
    already published in the product record and refuses to reject unless the
    overlap exceeds the measured local depth disagreement, so uncertain cells
    fail open and cannot flicker.
    """
    source = COMPOSITOR.read_text(encoding="utf-8")
    assert "actor_hit_is_in_front_of_world" not in source
    assert "CompositorVisibleSurfaceProduct" not in source
    assert "compositor_visible_surface_products" not in source
    assert "compositor_visible_receiver_index" not in source
    loop = source.split(
        "CompositorActorFragment compositor_actor_fragment_for_cell(", 1
    )[1].split("return fragment;\n}", 1)[0]
    assert "CompositorActorHit hit = compositor_actor_hit(row, ray);" in loop
    assert "if (!hit.valid) continue;" in loop

    gate = source.split(
        "bool fl4346_actor_survives_world_depth(", 1
    )[1].split("\n}\n", 1)[0]
    # One metric on both sides: the actor's own un-biased ray distance against
    # the ray distance published for the world. The actor-vs-actor ordering
    # helper carries a per-layer bias and must never enter this comparison.
    assert "hit.ray_t <= world_ray_t + slack" in gate
    assert "actor_candidate_view_depth" not in source
    assert "layer_bias" not in source
    # Conservative fail-open slack derived from the three sibling quarters.
    assert "(slot ^ neighbour)" in gate
    assert "disagreement = max(disagreement, abs(sibling_t - world_ray_t));" in gate
    assert "FL4346_WORLD_DEPTH_SLACK_GAIN" in gate
    assert "FL4346_WORLD_DEPTH_SLACK_FLOOR_WORLD" in gate
    # The copied float tie-break must not come back as the spatial tolerance.
    assert "world_ray_t + 0.001" not in gate
    # Sky and water can never occlude a character.
    assert "if (world_kind == CO_OWNER_SKY) return true;" in gate
    assert "if (world_kind == CO_OWNER_WATER) return true;" in gate
    # Ground-contact exception: strictly AFTER the duel, and keyed on the
    # presented ROOT. Keying on billboard_origin would exempt a band half a
    # sprite tall around every submerged actor and disable their occlusion.
    duel = gate.index("hit.ray_t <= world_ray_t + slack")
    ground = gate.index("FL4346_GROUND_CONTACT_BAND_WORLD")
    assert duel < ground
    assert "world_point.y <= hit.root.y + FL4346_GROUND_CONTACT_BAND_WORLD" in gate
    assert "billboard_origin" not in gate
    # The semantic anchor is never used as a geometric depth or height.
    assert "product.anchor.xyz" not in gate
    assert "product.anchor.y" not in gate


def test_world_depth_lane_fails_open_on_every_producer_and_host_path() -> None:
    """FL-4346: a missing world depth must never blank actors.

    anchor.w carries the world depth. Zero would read as "the world is at the
    camera" and reject every sprite, so all three writers of that lane publish
    the same enormous sentinel when no depth is known.
    """
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    effect = EFFECT.read_text(encoding="utf-8")
    assert "const float FL4346_WORLD_DEPTH_FAIL_OPEN = 1.0e20;" in product
    assert "const float FL4346_WORLD_DEPTH_FAIL_OPEN = 1.0e20;" in compositor
    assert "const FL4346_WORLD_DEPTH_FAIL_OPEN := 1.0e20" in effect
    # Producer: the sole record store publishes the sentinel for an invalid
    # receiver, and the shader-side failure record carries it too.
    assert (
        "frozen_receivers[slot].valid\n\t\t\t\t? frozen_receivers[slot].world_depth"
        "\n\t\t\t\t: FL4346_WORLD_DEPTH_FAIL_OPEN;" in product
    )
    assert (
        "record.anchor = vec4(0.0, 0.0, 0.0, FL4346_WORLD_DEPTH_FAIL_OPEN);"
        in product
    )
    # Host failure prefill must match the shader failure record byte for byte.
    assert (
        "_write_vec4(bytes, base + 64, 0.0, 0.0, 0.0, FL4346_WORLD_DEPTH_FAIL_OPEN)"
        in effect
    )
    # Consumer: an invalid record, an unknown neighbour, and the sentinel all
    # let the actor through.
    gate = compositor.split(
        "bool fl4346_actor_survives_world_depth(", 1
    )[1].split("\n}\n", 1)[0]
    assert "if (!final_product_status_is_valid(product.r0.x)) return true;" in gate
    assert "if (!final_product_status_is_valid(sibling.r0.x)) return true;" in gate
    assert gate.count("FL4346_WORLD_DEPTH_FAIL_OPEN) return true;") == 2


def test_broadphase_rect_is_conservative_for_the_actor_root_standee() -> None:
    """FL-4346: the CPU rect may only ADMIT cells, never crop the billboard.

    The scheduler must project the same four actor-root, world-vertical card
    corners consumed by the compositor. A one-cell margin covers integer
    partition rounding only; guessed centre/extent plus sink compensation is
    forbidden because it does not establish geometric containment.
    """
    source = EFFECT.read_text(encoding="utf-8")
    projection = source.split(
        "func _project_actor_sprite_frame_rect_queue", 1
    )[1].split("func _pack_actor_sprite_frame_rect_queue", 1)[0]
    assert "var rect_margin := 0.0" not in projection
    assert "var broadphase_cell_span := maxf(" in projection
    assert "var plane_offsets := [" in projection
    assert "Vector2(-anchor_world.x, anchor_world.y)" in projection
    assert "sprite_world_size.x - anchor_world.x" in projection
    assert "anchor_world.y - sprite_world_size.y" in projection
    assert "corner_world := billboard_origin" in projection
    assert "projected_corners.size() != 4" in projection
    assert "var rect_margin := broadphase_cell_span" in projection
    assert "foot_sink_margin_px" not in projection
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    # The packed rect now schedules candidate ids only. It cannot sample alpha,
    # intersect a ray, and publish an actor owner.
    assert "actor_schedule_rect_overlaps_cell(" in product
    scheduler = product.split(
        "void publish_actor_candidates_for_cell(", 1
    )[1].split("int product_material_family_for_visible_terrain", 1)[0]
    assert "actor_candidate_product.values" in scheduler
    assert "actor_atlas" not in scheduler
    assert "ray" not in scheduler
    assert "actor_atlas_sample(" not in product


def test_delete_first_removes_vsb_dash_and_attack_placement() -> None:
    """Body plus attached effects must return through one later raster path."""
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    assert "vec2 actor_local_uv_for_pixel(" not in product
    assert "actor_local_uv_for_pixel(row" not in product
    assert "vec2 actor_attack_effect_uv_for_pixel(" not in product
    assert "actor_attack_effect_uv_for_pixel(row" not in product
    assert "actor_dash_trail_ribbon_coverage_for_ray(" not in product
    assert "actor_dash_trail_cell_product(" not in product
    assert "actor_attack_swoosh_cell_product(" not in product
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    assert "float actor_dash_coverage(int row, vec2 uv)" in compositor
    assert "float actor_attack_coverage(int row, vec2 uv)" in compositor
    resolver = compositor.split(
        "CompositorActorFragment compositor_actor_fragment_for_cell(", 1
    )[1].split("return fragment;\n}", 1)[0]
    assert "CompositorActorHit hit = compositor_actor_hit(row, ray);" in resolver
    assert "actor_dash_coverage(row, hit.local_uv)" in resolver
    assert "actor_attack_coverage(row, hit.local_uv)" in resolver


def test_proof_runner_gates_on_rendered_framebuffer_registration() -> None:
    """FL-4346: package pass must be earned on rendered pixels.

    Every earlier gate compared two runtime fields produced by one projection
    call (actor anchor px vs pole px), a common-source self-comparison that
    reported 0.0 error while the operator watched the sprite orbit the wake.
    The runner must measure foot-vs-wake-anchor registration from the capture
    PNGs, fail closed on unmeasurable frames, and fold that verdict into the
    arm's pass bit. Behavioral coverage of the measurement itself lives in
    the synthetic-frame tests below; this test only pins the wiring.
    """
    runner = (ROOT / "scripts/run_fl4346_actor_presentation_proof.py").read_text(
        encoding="utf-8"
    )
    assert "def _measure_capture_registration(" in runner
    assert "def _framebuffer_registration_summary(" in runner
    assert "FRAMEBUFFER_REGISTRATION_TOLERANCE_PX = 20.0" in runner
    # Fail-closed: unmeasured gated captures fail the arm.
    assert "all_gated_captures_measured" in runner
    # The verdict is a hard gate on the arm's pass bit, not advisory metadata.
    assert 'receipt["framebuffer_registration"] = framebuffer' in runner
    assert 'and bool(framebuffer.get("pass"))' in runner
    # Zoom legitimately rescales the sprite around its root; only the zoom-in
    # phase may be excluded from the stability gate.
    assert 'FRAMEBUFFER_GATE_EXCLUDED_SUFFIXES = ("04_zoom_in",)' in runner
    # Visible wake endpoint identity is a hard runtime-truth gate.
    assert "def _wake_endpoint_identity(" in runner
    assert 'and bool(wake_endpoint.get("holds"))' in runner
    # Deleted Product/world-marker machinery must not return.
    assert "def _group_stems_by_x_overlap(" not in runner
    assert "def _stem_matches_receipt_rows(" not in runner
    assert "all_gated_markers_receipt_linked" not in runner
    # FL-4346: the measurement surface is the CONTENT-AGNOSTIC sentinel
    # footprint channel, and no clothing-band or palette segmentation may
    # return (billboarding holds for arbitrary sprite pixels; recognizing
    # one player skin tests nothing).
    assert "FRAMEBUFFER_SENTINEL_MIN_CHANNEL" in runner
    assert "def _actor_silhouette_bands(" not in runner
    assert "def _closest_component_seed(" not in runner
    assert '"gate_measures": "billboard_footprint_vs_actor_presentation_pole"' in runner
    assert "FRAMEBUFFER_MIN_PIVOT_PIXELS" in runner
    assert '"pivot_not_found"' in runner
    # FL-4346 (reviewer round 4, finding 3): the second-process presentation
    # pass is deleted. The DRIVER toggles the tint per phase, so the runner
    # command must not carry a launch-time tint flag, the gate must measure
    # the per-phase _mask.png captures, and every gated phase must be a
    # same-state pair of its presentation frame.
    assert '"--fl4346-actor-footprint-tint",' not in runner
    assert 'receipt["presentation_pass"]' not in runner
    assert "and presentation_complete" not in runner
    assert '_mask.png"' in runner
    assert "and all_gated_pairs_state_identical" in runner
    assert "and presentation_frames_complete" in runner


# ---------------------------------------------------------------------------
# FL-4346 falsifier BEHAVIORAL tests (synthetic rendered frames)
# ---------------------------------------------------------------------------
# The reviewer requirement: source-string threshold assertions are not
# sufficient for the falsifier — the measurement itself must be exercised
# against frames containing actor-red and unrelated bright-red contamination.
# These tests import the runner module and feed it synthetic frames whose
# ground truth is known exactly.

def _load_proof_runner():
    """FL-4346: import the proof runner (its main() is __main__-guarded)."""
    import importlib.util
    import sys

    # The runner imports its sibling module inspect_a3d, so the scripts
    # directory must be importable no matter which directory pytest was
    # launched from.
    scripts_dir = str(ROOT / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(
        "fl4346_proof_runner",
        ROOT / "scripts" / "run_fl4346_actor_presentation_proof.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _synthetic_actor_frame():
    """Build a synthetic diagnostic capture with exactly known truth.

    The billboard invariant is content-agnostic (FL-4346 operator
    correction 2026-08-04), so the actor here is an ARBITRARY sentinel
    blob — deliberately non-humanoid — exactly as the compositor's
    footprint tint channel would render any sprite's accepted alpha
    pixels. Layout (frame is 900 tall x 1200 wide, water background):
      - blob:   pure-magenta sentinel, an irregular splat spanning
                y 330-520 with its lowest run at y 490-520 x 575-625
                (TRUE FOOT bottom at y ~519)
      - pole:   5px-wide pure-cyan line x 598-602, y 340-540. Its bottom
                endpoint is the compositor's packed actor-presentation root.
    Returns (numpy_frame, truth_dict).
    """
    import numpy

    frame = numpy.zeros((900, 1200, 3), dtype=numpy.uint8)
    frame[...] = (117, 157, 172)                 # plain-water background
    sentinel = (255, 0, 255)
    # Irregular splat: wide head, offset torso lobe, narrow lowest run.
    frame[330:380, 560:640] = sentinel
    frame[380:450, 590:650] = sentinel
    frame[450:490, 565:605] = sentinel
    frame[490:520, 575:625] = sentinel           # lowest run -> foot at 519
    frame[340:541, 598:603] = (0, 255, 255)      # anchor pole, base 540
    truth = {
        "foot_y_bottom": 519.0,
        "anchor_x": 600.0,
        "anchor_y_bottom": 540.0,
    }
    return frame, truth


def _measure_synthetic(runner, frame, tmp_path, name="synthetic.png"):
    """Write a synthetic frame to disk and run the real measurement on it."""
    from PIL import Image as PILImage

    png_path = tmp_path / name
    PILImage.fromarray(frame).save(png_path)
    return runner._measure_capture_registration(png_path)


def test_falsifier_measures_an_arbitrary_blob_footprint(tmp_path) -> None:
    """FL-4346: measurement holds for a non-humanoid splat — no anatomy.

    The billboard invariant holds for arbitrary sprite content. The blob's
    foot (bottom-band centroid) is recorded as telemetry, but the GATED
    point is the bottom band of the rendered cyan anchor pole.
    """
    runner = _load_proof_runner()
    frame, truth = _synthetic_actor_frame()
    result = _measure_synthetic(runner, frame, tmp_path)
    assert result["measured"], result.get("reason")
    foot = result["footprint"]["foot_px"]
    assert foot["y"] > 500.0, foot
    assert abs(foot["y"] - truth["foot_y_bottom"]) <= runner.FRAMEBUFFER_FOOT_BAND_PX + 1
    # Foot X must exist and sit on the blob's lowest run.
    assert abs(foot["x"] - 600.0) < 30.0, foot
    anchor = result["pivot"]["anchor_base_px"]
    assert abs(anchor["x"] - truth["anchor_x"]) <= 1.0, anchor
    assert abs(anchor["y"] - truth["anchor_y_bottom"]) \
        <= runner.FRAMEBUFFER_FOOT_BAND_PX + 1, anchor
    offset = result["offset_px"]
    expected_dy = foot["y"] - anchor["y"]
    assert abs(offset["dy"] - expected_dy) <= runner.FRAMEBUFFER_FOOT_BAND_PX + 1
    assert abs(offset["dx"] - (foot["x"] - truth["anchor_x"])) <= 3.0


def test_falsifier_detects_blob_motion_against_fixed_anchor_pole(tmp_path) -> None:
    """The gate is content-agnostic and detects arbitrary footprint motion."""
    runner = _load_proof_runner()
    frame_a, _ = _synthetic_actor_frame()
    result_a = _measure_synthetic(runner, frame_a, tmp_path, "content_a.png")
    assert result_a["measured"], result_a.get("reason")

    frame_b, _ = _synthetic_actor_frame()
    # Move the blob's bottom while leaving the analytic pole fixed.
    lower = frame_b[450:529]
    lower_magenta = (
        (lower[..., 0] == 255) & (lower[..., 1] == 0) & (lower[..., 2] == 255)
    )
    lower[lower_magenta] = (117, 157, 172)
    frame_b[400:450, 540:594] = (255, 0, 255)
    result_b = _measure_synthetic(runner, frame_b, tmp_path, "content_b.png")
    assert result_b["measured"], result_b.get("reason")

    foot_shift = abs(
        result_b["footprint"]["foot_px"]["y"]
        - result_a["footprint"]["foot_px"]["y"]
    )
    assert foot_shift > 50.0, foot_shift
    offset_drift = abs(
        result_b["offset_px"]["dy"] - result_a["offset_px"]["dy"]
    )
    assert offset_drift > 50.0, offset_drift


def test_falsifier_fails_closed_when_pole_or_actor_is_missing(tmp_path) -> None:
    """FL-4346: an unmeasurable frame is a failed frame, never a skipped one.

    An untinted presentation frame has no sentinel at all, so this same
    rule is what keeps presentation captures out of the gate by design.
    """
    import numpy

    runner = _load_proof_runner()
    # No sentinel footprint at all -> fail closed.
    empty = numpy.zeros((900, 1200, 3), dtype=numpy.uint8)
    empty[...] = (117, 157, 172)
    result = _measure_synthetic(runner, empty, tmp_path, "no_actor.png")
    assert not result["measured"]
    assert result["reason"] == "footprint_not_found"
    # No rendered anchor pole -> fail closed.
    no_pivot, _ = _synthetic_actor_frame()
    cyan = (
        (no_pivot[..., 0] <= 5)
        & (no_pivot[..., 1] >= 250)
        & (no_pivot[..., 2] >= 250)
    )
    no_pivot[cyan] = (117, 157, 172)
    result = _measure_synthetic(runner, no_pivot, tmp_path, "no_pole.png")
    assert not result["measured"]
    assert result["reason"] == "pivot_not_found"
    # A second cyan source outside the subject window fails closed.
    stray_pivot, _ = _synthetic_actor_frame()
    stray_pivot[40:60, 1120:1140] = (0, 255, 255)
    result = _measure_synthetic(runner, stray_pivot, tmp_path, "two_pivots.png")
    assert not result["measured"]
    assert result["reason"] == "pivot_outside_window"


def test_falsifier_fails_closed_on_a_second_sentinel_blob(tmp_path) -> None:
    """FL-4346: sentinel ink outside the actor window is a broken channel.

    The footprint channel's premise is ONE tinted local actor. A second
    sentinel blob far from the first means that premise is false, and
    gating on either blob would be a guess — fail closed instead.
    """
    runner = _load_proof_runner()
    frame, _ = _synthetic_actor_frame()
    # Second sentinel blob in the far corner, > window half-size away.
    frame[60:120, 1100:1160] = (255, 0, 255)
    result = _measure_synthetic(runner, frame, tmp_path, "two_blobs.png")
    assert not result["measured"]
    assert result["reason"] == "footprint_split_across_window"


def test_falsifier_requires_visible_wake_endpoint_to_equal_actor_root() -> None:
    runner = _load_proof_runner()
    good_receipt = {
        "wake_endpoint_matches_root": True,
        "captures": [
            {
                "phase": "p0",
                "actor_root_world": {"x": 152.5, "y": 0.0, "z": -5.5},
                "wake_authority_body_xz": [152.5, -5.5],
                "wake_presentation_endpoint_xz": [152.5, -5.5],
            }
        ],
    }
    assert runner._wake_endpoint_identity(good_receipt)["holds"]
    diverged = json.loads(json.dumps(good_receipt))
    diverged["captures"][0]["wake_presentation_endpoint_xz"] = [153.9, -5.5]
    verdict = runner._wake_endpoint_identity(diverged)
    assert not verdict["holds"]
    assert verdict["reason"] == "wake_endpoint_not_actor_root"
    # Missing fields are absence of proof, not proof.
    stripped = json.loads(json.dumps(good_receipt))
    del stripped["captures"][0]["wake_presentation_endpoint_xz"]
    assert not runner._wake_endpoint_identity(stripped)["holds"]
    unmatched = json.loads(json.dumps(good_receipt))
    unmatched["wake_endpoint_matches_root"] = False
    assert not runner._wake_endpoint_identity(unmatched)["holds"]

def test_footprint_tint_is_per_actor_and_driver_toggled() -> None:
    """FL-4346 (reviewer round 4, findings 1+3): tint keys on ONE actor id
    and both surfaces of a phase come from one live process.

    A run-global tint boolean merged every actor row into one sentinel
    population, so a second actor inside the measurement window could shift
    the blob. The channel must be keyed to a single server actor id, and
    the proof driver must toggle it around each phase's mask capture with a
    receipt-level state-identity check between the presentation and mask
    snapshots.
    """
    effect = EFFECT.read_text(encoding="utf-8")
    # The boolean is deleted, not renamed alongside: only the id form exists.
    assert "var fl4346_actor_footprint_tint_actor_id: int = -1" in effect
    assert "var fl4346_actor_footprint_tint: bool" not in effect
    # Bit 25 is granted only to rows whose actor id matches the pinned id.
    assert "fl4346_actor_footprint_tint_actor_id >= 0" in effect
    assert (
        "actor_id == clampi(fl4346_actor_footprint_tint_actor_id, 0, 0xFFFF)"
        in effect
    )
    driver = (ROOT / "scripts" / "fl4346_actor_presentation_driver.gd").read_text(
        encoding="utf-8"
    )
    # Same-process pairing: tint on -> mask capture -> tint off, plus the
    # state-identity comparison between the two receipt snapshots.
    assert (
        'effect.set("fl4346_actor_footprint_tint_actor_id", local_actor_id)'
        in driver
    )
    assert 'effect.set("fl4346_actor_footprint_tint_actor_id", -1)' in driver
    assert '_mask.png" % phase' in driver
    assert "func _mask_pair_state_identity(" in driver
    assert '"footprint_pair": mask_pair,' in driver
    assert '"state_identical"' in driver


def test_pivot_diagnostic_consumes_the_compositor_billboard_hit() -> None:
    """The diagnostic pivot is paint from the same exact plane intersection."""
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    product = _expand_includes(PRODUCT.read_text(encoding="utf-8"))
    assert "FL4346_PIVOT_DISC_RADIUS_WORLD" in compositor
    assert "compositor_actor_pivot_plane_distance(" not in compositor
    assert "FL4346_PIVOT_ADMIT_RADIUS_WORLD" not in product
    assert "pivot_candidate" not in product
    assert "length(hit.plane_offset) <= FL4346_PIVOT_DISC_RADIUS_WORLD" in compositor


def test_billboard_is_one_vertical_camera_yaw_parallel_standee() -> None:
    """FL-4346: camera yaw turns one world-vertical standee in both modes.

    The deleted perspective branch aimed each actor's plane at the camera
    POSITION (point-facing billboarding), so a Q/E orbit changed the plane
    orientation per actor and re-projected the sprite around its pivot. Attempt
    95 then used the full pitched camera plane, which tilted the authored world-Y
    sprite axis. One frame-uniform yaw comes from the frozen camera; packed
    per-actor plane angles and tilted vertical axes may not return.
    """
    effect = EFFECT.read_text(encoding="utf-8")
    projection = effect.split(
        "func _project_actor_sprite_frame_rect_queue", 1
    )[1].split("func _pack_actor_sprite_frame_rect_queue", 1)[0]
    # CPU no longer publishes a cylindrical-plane angle. The rectangle only
    # schedules cells, using horizontal camera-right and world-up.
    assert "ortho_billboard_normal" not in projection
    assert "billboard_normal_xz_angle" not in projection
    right_helper = effect.split(
        "func _actor_world_vertical_billboard_right", 1
    )[1].split("func _actor_billboard_pixels_per_world_from_screen", 1)[0]
    assert "var billboard_right := projection_ctx.basis.x" in right_helper
    assert "billboard_right.y = 0.0" in right_helper
    helper = effect.split(
        "func _actor_billboard_pixels_per_world_from_screen", 1
    )[1].split("func _project_actor_sprite_frame_rect_queue", 1)[0]
    assert "_actor_world_vertical_billboard_right(projection_ctx)" in helper
    assert "var billboard_up := Vector3.UP" in helper
    assert "world_pos + billboard_up" in helper
    assert "to_camera_xz" not in projection
    assert "camera_world.x - foot_world.x" not in projection
    assert "billboard_normal_xz_angle = atan2(" not in projection
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    hit = compositor.split(
        "CompositorActorHit compositor_actor_hit(", 1
    )[1].split("uint actor_attack_phase_id", 1)[0]
    assert "normal = product_vsb_frame.camera_forward_far.xyz" in hit
    assert "normal.y = 0.0;" in hit
    assert "right = product_vsb_frame.camera_right_ortho_half_width.xyz" in hit
    assert "right.y = 0.0;" in hit
    assert "right = vec3(normal.z, 0.0, -normal.x)" not in hit
    assert "up = vec3(0.0, 1.0, 0.0)" in hit
    plane_basis = hit.split("float denominator", 1)[0]
    assert "actor_cell_word(6, row)" not in plane_basis
    assert "actor_direction" not in plane_basis

    # Representative Godot camera bases: positive frozen camera-right must stay
    # the authored U axis at every yaw. The deleted derived formula is exactly
    # its negative for these world-up cameras.
    for yaw in (0.0, math.pi * 0.25, math.pi * 0.5, math.pi, math.pi * 1.5):
        forward = (-math.sin(yaw), -0.45, -math.cos(yaw))
        forward_xz_len = math.hypot(forward[0], forward[2])
        normal = (forward[0] / forward_xz_len, 0.0, forward[2] / forward_xz_len)
        camera_right = (math.cos(yaw), 0.0, -math.sin(yaw))
        derived_wrong = (normal[2], 0.0, -normal[0])
        assert sum(a * b for a, b in zip(camera_right, derived_wrong)) < -0.999
        assert abs(sum(a * b for a, b in zip(camera_right, normal))) < 1.0e-9


def test_actor_hit_becomes_valid_only_after_uv_and_atlas_validation() -> None:
    compositor = COMPOSITOR.read_text(encoding="utf-8")
    hit = compositor.split(
        "CompositorActorHit compositor_actor_hit(", 1
    )[1].split("uint actor_attack_phase_id", 1)[0]
    valid_set = hit.index("hit.valid = true;")
    uv_reject = hit.index("any(lessThan(hit.local_uv")
    layer_reject = hit.index("if (layer < 0 || layer >= atlas_size.z)")
    atlas_reject = hit.index("any(greaterThanEqual(atlas_px, atlas_size.xy))")
    atlas_fetch = hit.index("hit.atlas_pixel = texelFetch(")
    assert uv_reject < layer_reject < atlas_reject < atlas_fetch < valid_set
    assert hit.count("hit.valid = true;") == 1


def test_direction_lut_cannot_change_billboard_geometry() -> None:
    stack = STACK.read_text(encoding="utf-8")
    effect = EFFECT.read_text(encoding="utf-8")
    assert "func _billboard_geometry_from_variants(" in stack
    assert '"billboard_geometry": billboard_geometry' in stack
    resolver = effect.split(
        "func _resolve_actor_sprite_rows_for_pixel_context", 1
    )[1].split("func _build_actor_atlas_source_rows", 1)[0]
    assert 'selected["anchor_px"] = billboard_geometry.get(' in resolver
    assert 'selected["actor_sprite_pixel_size_world"] = float(' in resolver
    assert "actor_sprite_foot_sink_world" not in resolver
    assert "foot_sink_world" not in stack
    assert 'selected["frame_rect_px"] = Vector4i(' in resolver

    for sidecar_path in ACTIVE_ACTOR_SIDECARS:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        groups: dict[tuple[str, str, int], list[dict]] = {}
        for frame in sidecar["compositor_frames"]:
            key = (
                frame["presentation_kind"],
                frame["layer_role"],
                int(frame["frame"]),
            )
            groups.setdefault(key, []).append(frame)
        for rows in groups.values():
            sizes = {(row["frame_rect_px"][2], row["frame_rect_px"][3]) for row in rows}
            anchors = {tuple(row["anchor_ref_px"]) for row in rows}
            assert len(sizes) == 1
            assert len(anchors) == 1


def test_sprite_scale_has_no_fallback_authority() -> None:
    """FL-4346 (operator order, 2026-08-04): NO scale fallback anywhere.

    The world-units-per-source-pixel scale is authored per sprite in its
    sidecar. The compositor's 0.0075 default was a silent second scale
    authority that no sidecar ever authored; it is deleted, and a row
    without an explicit positive scale must be REJECTED, never rendered
    at an invented size. ActorLayerStack must keep hard-indexing the
    sidecar key so a sidecar missing it fails loudly at publish time.
    """
    effect = EFFECT.read_text(encoding="utf-8")
    assert 'row.get("actor_sprite_pixel_size_world"' not in effect
    # The literal may survive in the tombstone comment; any CODE use ends
    # with a closing parenthesis (a .get default position).
    assert "0.0075)" not in effect
    projection = effect.split(
        "func _project_actor_sprite_frame_rect_queue", 1
    )[1].split("func _pack_actor_sprite_frame_rect_queue", 1)[0]
    assert 'if not row.has("actor_sprite_pixel_size_world"):' in projection
    assert (
        'var sprite_pixel_size_world := float(row["actor_sprite_pixel_size_world"])'
        in projection
    )
    assert "if sprite_pixel_size_world <= 0.0:" in projection
    stack = (
        ROOT / "godot_project" / "scripts" / "visual" / "ActorLayerStack.gd"
    ).read_text(encoding="utf-8")
    # Hard index, not .get(): a sidecar without the key must fail loudly.
    assert (
        '"actor_sprite_pixel_size_world": float(sidecar["actor_pixel_size_world"])'
        in stack
    )
    assert 'sidecar.get("actor_pixel_size_world"' not in stack
