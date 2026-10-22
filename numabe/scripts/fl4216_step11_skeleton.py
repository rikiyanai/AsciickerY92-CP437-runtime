#!/usr/bin/env python3
"""FL-4216 operator skeleton (Steps 11, 13, 14, 15).

Authority: docs/FAILURE_LOG.md entries FL-4216, FL-4129, and FL-4208.
This script is a non-authoritative scaffold. It must not define water-lane
requirements, status, closure, acceptance, queue order, nor proof success.
Update the FL entries first; update this scaffold only as an implementation
mirror.

Emits the GDScript + GLSL change skeleton the operator drops into
ASCIICompositorEffect.gd and ascii_compositor.glsl to wire the
water-specific GPU readback path. Also surfaces the Step 13 scripted
proof checklist, the Step 14 headed manual checklist, and the
Step 15 closure audit sequence.

Use:
  python3 scripts/fl4216_step11_skeleton.py                 # full
  python3 scripts/fl4216_step11_skeleton.py --section shader
  python3 scripts/fl4216_step11_skeleton.py --section effect
  python3 scripts/fl4216_step11_skeleton.py --section schema
  python3 scripts/fl4216_step11_skeleton.py --section step13
  python3 scripts/fl4216_step11_skeleton.py --section step14
  python3 scripts/fl4216_step11_skeleton.py --section step15
"""
from __future__ import annotations

import argparse
import sys


SHADER_SKELETON = """\
# === ascii_compositor.glsl additions ===
# Add a water-specific GPU fact image at the next free binding slot.
# Current high-water-mark is binding 21 (fl4129_reflected_terrain_depth_image).
# Suggest binding 22-25 for the water fact images. Pick a format that holds
# 4 float channels per ANSI cell so the per-cell payload covers depth_fade_raw,
# fog_factor, reflection_alpha, x13_shade255 + one packed enum word for
# pixelart_depth_path / x13_matched_phase / actor_sink_pass_index, etc.
#
# Replicate the FL-4184 foliage_fact image pattern:
#
#     layout(rgba32f, set = 0, binding = 22) uniform restrict writeonly image2D
#         fl4129_water_fact_color;        // depth_fade_raw, depth_fade_displaced, fog_factor, water_thickness
#     layout(rgba32f, set = 0, binding = 23) uniform restrict writeonly image2D
#         fl4129_water_fact_reflection;   // reflection_alpha, reflection_fresnel, reflection_product_depth, reflection_fallback_reason
#     layout(rgba32f, set = 0, binding = 24) uniform restrict writeonly image2D
#         fl4129_water_fact_wave;         // x13_matched_phase, x13_shade255, glyph_bucket_before, glyph_bucket_after
#     layout(rgba32f, set = 0, binding = 25) uniform restrict writeonly image2D
#         fl4129_water_fact_actor_sink;   // actor_id, actor_row, water_glyph_id, sink_alpha
#
# In apply_fl4129_water_surface_owner, after the sample is computed:
#
#     ivec2 ansi_xy = pos / int(ANSI_CELL_SIZE);
#     imageStore(fl4129_water_fact_color, ansi_xy,
#         vec4(s.depth_fade_raw, s.depth_fade_displaced, s.fog_factor, s.water_thickness));
#     imageStore(fl4129_water_fact_reflection, ansi_xy,
#         vec4(s.reflection_alpha, s.reflection_fresnel,
#              s.reflection_product_depth, float(s.reflection_fallback_reason)));
#     imageStore(fl4129_water_fact_wave, ansi_xy,
#         vec4(float(s.x13_matched_phase), s.x13_shade255,
#              float(s.x13_glyph_bucket_before), float(s.x13_glyph_bucket_after)));
#     imageStore(fl4129_water_fact_actor_sink, ansi_xy,
#         vec4(float(s.actor_sink_actor_id), float(s.actor_sink_actor_row),
#              float(s.actor_sink_water_glyph_id), s.actor_sink_sink_alpha));
#
# Each cell ends up with 4*4 = 16 floats of water proof. ANSI grid size at
# 1280x800 / 16 = 80x50 cells = 4000 cells per image; 4 images * 64 bytes =
# 256 bytes per cell or about 1 MB per frame of readback. Reuse the foliage
# readback bracket so the Step 12 fact_readback budget stays under 500us.
"""


EFFECT_SKELETON = """\
# === ASCIICompositorEffect.gd additions ===
# Mirror the FL-4184 foliage fact image allocation and readback. Suggested
# placement: right next to the foliage_fact_color_rd / _albedo_rd / etc.
#
# State vars:
#
#     var fl4129_water_fact_color_rd: RID
#     var fl4129_water_fact_reflection_rd: RID
#     var fl4129_water_fact_wave_rd: RID
#     var fl4129_water_fact_actor_sink_rd: RID
#     var _fl4129_water_fact_state: String = "missing_gpu_fact_surface"
#     var _fl4129_water_fact_cells: Array = []
#
# Allocate in _initialize_compute(), free in the cleanup pair. Reuse the
# foliage_fact_format helper if it already creates RGBA32F storage images.
#
# Bind in _render_callback() right after the main compositor uniform set,
# at the same call site that today binds the foliage_fact_* images.
#
# Readback decode (extend _readback_foliage_facts or add a sibling). Keep
# the same Step 2.5 timing bracket — wrap both readbacks in the existing
# _fl4216_fact_readback_us_ring sample so the budget tracks the combined
# cost:
#
#     var _fl4216_fact_readback_start_us: int = Time.get_ticks_usec()
#     _readback_foliage_facts(size)
#     _readback_water_facts(size)
#     _fl4216_record_perf_sample(_fl4216_fact_readback_us_ring,
#         float(Time.get_ticks_usec() - _fl4216_fact_readback_start_us))
#
# Decode per-cell fl4129.* fields and append them to the cells.jsonl row
# that the X dump writer already constructs in World.gd. The row shape:
#
#     row["water_state"] = {
#         "inferred_from": "shader_fact_readback",
#         "package": "fl4129",
#     }
#     row["fl4129"] = {
#         "depth_fade_raw":              color_pixel[0],
#         "depth_fade_displaced":        color_pixel[1],
#         "fog_factor":                  color_pixel[2],
#         "water_thickness":             color_pixel[3],
#         "reflection_alpha":            reflection_pixel[0],
#         "reflection_fresnel":          reflection_pixel[1],
#         "reflection_product_depth":    reflection_pixel[2],
#         "reflection_fallback_reason":  int(reflection_pixel[3]),
#         "reflection_product_present":  reflection_pixel[2] > 0.0,
#         "reflection_fallback_used":    int(reflection_pixel[3]) != 0,
#         "x13_matched_phase":           int(wave_pixel[0]),
#         "x13_shade255":                wave_pixel[1],
#         "x13_glyph_bucket_before":     int(wave_pixel[2]),
#         "x13_glyph_bucket_after":      int(wave_pixel[3]),
#         "x13_marker_removed":          int(wave_pixel[0]) >= 0,
#         "actor_sink": {
#             "actor_id":             int(actor_sink_pixel[0]),
#             "actor_row":            int(actor_sink_pixel[1]),
#             "water_glyph_id":       int(actor_sink_pixel[2]),
#             "sink_alpha":           actor_sink_pixel[3],
#         }
#     }
#
# After this lands, the fresh headed X dump must carry shader-owned water
# rows and the Step 10 takeover call site can be added to the actor pixel
# path. Deleted validator scripts must not be used as proof.
"""


SCHEMA_SKELETON = """\
# === Per-cell schema operator must satisfy ===
# Required (FL-4216 frozen plan + FL-4208 child contract):
#   water_state.inferred_from == "shader_fact_readback"
#   fl4129.depth_fade_raw, depth_fade_displaced, fog_factor,
#       water_thickness
#   fl4129.reflection.{alpha, fresnel, product_depth,
#       fallback_reason, product_present, fallback_used}
#   fl4129.x13.{matched_phase, shade255, glyph_bucket_before,
#       glyph_bucket_after, marker_removed}
#   fl4129.actor_sink.{actor_id, actor_row, water_glyph_id,
#       sink_alpha}
#   fl4129.perf.{water_package_gpu_us_median, fact_readback_us_median,
#       fl4208_frame_cost_us_median, headed_visible_fps_min,
#       sample_count, budget.*}
#
# Optional follow-ups (Step 5/6/7 supplementary fields the runtime
# already computes; the X dump can carry them when there is room):
#   fl4129.refraction.{displaced_uv, displaced_uv_reverted,
#       pixelart_depth_path}
#   fl4129.fog.{fog_uv, pixelart_depth_path}
#   fl4129.detail.{enabled, direction, scale, speed, amount,
#       sharpness, color, strength, mask}
#   fl4129.reflection.{reflect_uv, tint_applied}
#   fl4129.lighting.{shadow_atten, main_light_contribution,
#       additional_light_contribution}
#   fl4129.x13.{wave_anim, wave_pos, wave_siz, ws} (each a 4-tuple)
#   fl4129.actor_sink.{actor_glyph_id, actor_glyph_id_above,
#       water_palette_entry_id, reflected_y_shift, pass_index}
#
# Mirror the X dump cells.jsonl rows between manual and scripted runs.
# The only allowed schema diff is input_owner_kind ("manual" vs
# "scripted"); any other diff fails the FL-4208 dump-schema gate.
"""


STEP13_CHECKLIST = """\
# === Step 13 scripted X proof checklist ===
# Run after Step 11 lands (the scripted runner depends on the
# per-cell fl4129.* readback being live in cells.jsonl).
#
# For each pose:
#   python3 scripts/proofs/proof_fl4216_water_xdump.py \\
#       --pose-id shore_flat_line \\
#       --input-owner-kind scripted \\
#       --output .run/fl4216_water/scripted/<stamp>/
#
# Poses (from the frozen plan pose registry):
#   shore_flat_line        flat level body, no sloped ramp
#   actor_sink_deep        lower-row takeover and wake separation
#   shore_grass_context    shoreline body next to non-water
#   open_reflection_band   diagnostic-only (off-map historical)
#   wave_band_sweep        X13 wave bands and matched-phase
#
# Receipt write target (per pose):
#   .run/fl4216_water/scripted/<pose>/<stamp>/receipt.json
#   docs/research/ascii/verification/fl4216/water/scripted/<pose>/<stamp>/receipt.json
#
# Acceptance:
#   - cells.jsonl per pose carries the full fl4129.* schema.
#   - water_state.inferred_from == "shader_fact_readback" everywhere.
#   - manual vs scripted schemas identical except input_owner_kind.
#   - Step 12 perf budgets pass under the warmed-up sample window.
"""


STEP14_CHECKLIST = """\
# === Step 14 headed manual proof checklist ===
# Operator drives the Godot scene by hand.
#
# Command (from a fresh terminal in the repo root):
#   /opt/homebrew/bin/godot --path godot_project -- \\
#     --baseline-feature=water_current_package \\
#     --baseline-feature=foliage_cards
#
# At each pose:
#   1. Drive the player camera into the pose using mouse/keyboard.
#   2. Let the engine warm up at least 30 frames (>= half a second).
#   3. Press X to drop an X dump.
#   4. Visual review per the pose-specific check below.
#   5. Copy the .run/final_render_cell_dump/<stamp>/ payload into
#      docs/research/ascii/verification/fl4216/water/manual/<pose>/<stamp>/.
#
# Pose-specific visual checks (frozen plan):
#   shore_flat_line: flat continuous water body, no sloped white ramp,
#                    no shoreline body split.
#   actor_sink_deep: lower actor rows are water glyph/colour;
#                    upper rows remain actor glyph/colour;
#                    reflected Y shift is visible.
#   shore_grass_context: shoreline water with adjacent non-water
#                        terrain shares the same baseline body.
#   open_reflection_band: reflection respects depth/refraction
#                          (diagnostic-only).
#   wave_band_sweep: moving wave bands tied to shade field;
#                    matched_phase causes glyph_bucket transition.
#
# Acceptance:
#   - Visible flat body, continuous shoreline/body baseline.
#   - PixelWater refraction/depth/fog/detail/reflection/lighting
#     present in headed output.
#   - X13 wave bands visible.
#   - Actor sink row takeover visible.
#   - No `:` shimmer glyph anywhere.
#   - FPS reads >= 24 after warm-up at the proof viewport.
"""


STEP15_CLOSURE_SEQUENCE = """\
# === Step 15 closure audit sequence ===
# Run after Step 13 + Step 14 are complete.
#
#   1. Fresh headed operator X dump from the locked proof pose.
#      Expect: shader-owned water rows, actor sink rows, wake rows,
#      reflection rows, surface bucket provenance, and performance facts.
#
#   2. python3 scripts/fl4216_completion_status.py
#      Expect: completion_proof_available remains False until replacement
#      evidence gates exist.
#
#   8. Operator visual sign-off per Law 16 (gate pass is not closure).
#
#   9. Write the final audit receipt:
#      docs/research/ascii/verification/fl4216/water/final_audit/<stamp>/receipt.json
#      with verdict=closed_human_signoff.
#
#  10. python3 scripts/analyze_runs.py fl note FL-4216 \\
#          "Closure: <commit-hash>; receipt at <path>"
#
#  11. Record the operator verdict in FL-4216. Do not mark resolved from this scaffold.
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FL-4216 operator skeleton (Steps 11, 13, 14, 15)")
    parser.add_argument(
        "--section",
        choices=["shader", "effect", "schema", "step13", "step14", "step15", "all"],
        default="all",
    )
    args = parser.parse_args(argv)
    if args.section in ("shader", "all"):
        print(SHADER_SKELETON)
    if args.section in ("effect", "all"):
        print(EFFECT_SKELETON)
    if args.section in ("schema", "all"):
        print(SCHEMA_SKELETON)
    if args.section in ("step13", "all"):
        print(STEP13_CHECKLIST)
    if args.section in ("step14", "all"):
        print(STEP14_CHECKLIST)
    if args.section in ("step15", "all"):
        print(STEP15_CLOSURE_SEQUENCE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
