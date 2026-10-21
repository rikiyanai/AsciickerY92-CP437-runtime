#!/usr/bin/env python3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PORT = ROOT / "godot_project" / "reference" / "texel_splat" / "reference_texel_splat_port.gd"
ASSET_ROOT = ROOT / "godot_project" / "reference" / "texel_splat"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    src = PORT.read_text(encoding="utf-8")
    parity_capture = ROOT / "scripts" / "capture_canxkoz_reference_frame.mjs"
    parity_compare = ROOT / "scripts" / "compare_png_exact.py"
    parity_runner = ROOT / "scripts" / "run_canxkoz_godot_pixel_parity.py"
    required_tokens = [
        "REFERENCE_SPLAT := \".scratch/canxkoz-texel-splatting/src/splat.ts\"",
        "REFERENCE_SCENE := \".scratch/canxkoz-texel-splatting/demo/scene.ts\"",
        "func _capture_probe(",
        "func _render_splat_layers(",
        "func _sample_probe_background(",
        "rec.color = _posterize_color(_sky_color(ray_dir, light_state))",
        "return rec.color",
        "func _probe_reconstruct(",
        "func _bayer4(",
        "func _splat_depth_bias(",
        "func _prev_texel_matches_eye(",
        "func _assign_edge_masks(",
        "func _edge_neighbor_breaks(",
        "func _face_normal(",
        "func _build_bvh(",
        "func _trace_scene(scene: Dictionary",
        "func _trace_any_shadow(scene: Dictionary",
        "func _raster_tri(img: Image",
        "return newly_filled",
        "\"valid_records\": valid_records",
        "for rec in grid_probe[\"valid_records\"]:",
        "eye_fill_pixels += _splat_probe_texel(img, depth, rec, eye_origin, cam, false, true, light_state, 0, fade_t, width, height)",
        "\"shadow_tris\": shadow_tris",
        "\"shadow_bvh\": shadow_bvh",
        "shadow_origin := pos + n * 0.02",
        "sun_shadow := lerpf(1.0, raw_shadow, shadow_fade)",
        "func _create_sphere(",
        "func _create_orb_tris(",
        "Tri.new(pos + p[idx[i]] * scale",
        "if int(hit[\"material\"]) == 4:",
        "CANXKOZ_PROBE_SIZE := 384",
        "FACE_RES := CANXKOZ_PROBE_SIZE",
        "RENDER_W := 888",
        "RENDER_H := 555",
        "BLEND_DURATION := 0.5",
        "func _final_canvas_image(",
        "REFERENCE_CAMERA_POS := Vector3(20.0, 1.6, 15.0)",
        "REFERENCE_CAPTURE_FRAME := 31",
        "var reference_frame := _get_user_int_arg(\"--reference-frame=\", REFERENCE_CAPTURE_FRAME)",
        "\"reference_frame\": reference_frame",
        "if z <= depth[idx]:",
        "func _splat_probe_texel(img: Image, depth: PackedFloat32Array, rec: ProbeTexel, probe_origin: Vector3, cam: CameraFrame, only_empty: bool, is_eye: bool",
        "var h := float(((tid * 2654435761) >> 24) & 0xFF)",
        "var stable_bias := h * 0.000000001",
        "return Color(minf(emissive.r, 1.0), minf(emissive.g, 1.0), minf(emissive.b, 1.0), 1.0)",
        "func _get_user_int_arg(",
        "var frame_count := _get_user_int_arg(\"--frames=\", FRAME_COUNT)",
        "var render_mode := _get_user_arg(\"--mode=\", \"splat\")",
        "var direct_internal := _get_user_arg(\"--direct-internal=\", \"canvas\")",
        "var splat_internal := _get_user_arg(\"--splat-internal=\", \"render\")",
        "var skip_direct_comparison := _has_user_arg(\"--skip-direct-comparison\")",
        "FACE_RES = _get_user_int_arg(\"--probe-size=\", CANXKOZ_PROBE_SIZE)",
        "func _has_user_arg(",
        "func _write_direct_only_package(",
        "func _render_direct_raster(",
        "func _raster_direct_tri(",
        "func _render_direct_at_size(",
        "func _apply_reference_postprocess_at_size(",
        "\"status\": \"direct_diagnostic\"",
        "\"postprocess\": \"internal_size_reference_math\"",
        "\"rasterized\": true",
        "\"direct_internal\": direct_internal",
        "REFERENCE_TEXEL_SPLAT_GODOT_DIRECT_DIAGNOSTIC",
        "func _create_transition_state(",
        "func _update_reference_transition(",
        "func _get_active_probe_mask(",
        "func _probe_is_active(",
        "active_probe_mask",
        "probe_frame",
        "var active_probes := _get_active_probe_mask(",
        "GRID_CULL_COS :=",
        "EYE_CULL_COS :=",
        "func _compute_face_mask(",
        "STONE_SUBDIVS := 8",
        "func _reference_frame_state(",
        "func _sample_gradient(",
        "func _simplex2(",
        "func _hash2sky(",
        "var mie := (1.0 - gg)",
        "var disk_blend := smoothstep(",
        "func _stone_color(",
        "func _path_ground_color(",
        "GRASS_LAYERS := 8",
        "GRASS_SUBDIVISIONS := 16",
        "func _append_reference_grass_shell_tris(",
        "var total_layers := GRASS_LAYERS + 1",
        "if float(hit[\"material_param\"]) == 0.0:",
        "func _raster_probe_tri(",
        "func _clip_probe_triangle_near(",
        "func _finalize_probe_lighting(",
        "func _raster_splat_world_tri(",
        "func _clip_splat_triangle_near(",
        "func _path_grass_discard(",
        "func _grass_shell_color(",
        "func _sample_clouds(",
        "func _apply_reference_postprocess(",
        "func _apply_reference_godrays(",
        "func _compute_reference_bloom(",
        "var splat_post := _apply_reference_postprocess_to_canvas(",
        "func _apply_reference_postprocess_to_canvas(",
        "stable probe texel stores radial depth and lit color",
        "EYE probe supplies the probe-derived background/backstop",
        "GRID and PREVIOUS stable probes use a dithered transition",
        "\"status\": \"mechanism_checkpoint\"",
        "\"artifact\": \"standalone_godot_canxkoz_texel_splat_port_checkpoint\"",
        "REFERENCE_TEXEL_SPLAT_GODOT_PORT_CHECKPOINT",
        "no ProductHit lane",
        "no current-ray repaint after splat selection",
    ]
    for token in required_tokens:
        require(token in src, f"missing reference-port token: {token}")

    for proof_tool in [parity_capture, parity_compare, parity_runner]:
        require(proof_tool.is_file(), f"missing pixel-parity proof tool: {proof_tool.relative_to(ROOT)}")

    banned_tokens = [
        "ASCIICompositorEffect",
        "cell_owner_product",
        "ProductHit(",
        "load_probe_eye_backstop_color",
        "probe_direct_cutover_owner",
        "func _texel_transition_hash(",
        "func _postprocess_color(",
        "func _count_depth_pixels(",
        "func _count_valid_probe_texels(",
        "func _trace_probe_scene(",
        "func _trace_probe_stones(",
        "func _trace_probe_grass(",
        "REFERENCE_CAPTURE_FRAME := 30",
        "REFERENCE_CAPTURE_FRAME := 41",
        "fade_t + 0.32",
        "eye_fill_pixels += _splat_probe_texel(img, depth, rec, eye_origin, cam, true, light_state, 0, fade_t, width, height)",
        "eye_fill_pixels += _splat_probe_texel(img, depth, rec, eye_origin, cam, false, light_state, 0, fade_t, width, height)",
        "var h := float((tid * 2654435761) & 0xFF) / 255.0",
        "Vector3(-half_area, y, -half_area)",
        "\"status\": \"complete\"",
        "REFERENCE_TEXEL_SPLAT_GODOT_PORT_COMPLETE",
        "func _apply_reference_canvas_transfer(",
        "0.8103629723752396",
        "31.324038769734877",
    ]
    for token in banned_tokens:
        require(token not in src, f"standalone reference port must not depend on app pipeline token: {token}")

    expected_assets = [
        "LICENSE.canxkoz-texel-splatting",
        "assets/hero.jpg",
        "assets/site-hero.jpg",
        "assets/icon.svg",
        "assets/public-icon.svg",
        "assets/pair-cubemap.jpg",
        "assets/pair-rasterization.jpg",
        "assets/pair-texel-splatting.jpg",
    ]
    for rel in expected_assets:
        require((ASSET_ROOT / rel).is_file(), f"missing copied canxkoz asset: {rel}")

    print("reference texel splat Godot structural contract passed; pixel parity is owned by run_canxkoz_godot_pixel_parity.py")


if __name__ == "__main__":
    main()
