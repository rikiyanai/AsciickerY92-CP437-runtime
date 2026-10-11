#!/usr/bin/env python3
"""
FL-4359 MOVING-CAMERA jitter/shimmer analyzer.

Reads JSONL files produced by --fl4359-jitter-capture (the
fl4359_external_movement_driver.gd capture) and splits the visual defect into
its two independent owners, per docs/agent/ADR-0001:

  * CAMERA jitter   -> snap residual delta / sign-flip (gameplay_moving_camera_jitter_bounded)
  * CONTENT shimmer -> jitter_pct over world-anchor-matched cells (gameplay_content_shimmer_bounded)

FL-4359 accepts MOVING-camera evidence only: a capture whose camera did not move
is rejected by scenario_moving_camera_continuous, so a steady-pose capture can
never satisfy the jitter/shimmer gates. This supersedes the deleted
scripts/analyze_fl4359_jitter.py (which could pass on a steady pose and is banned
by scripts/git_commit_policy_guard.py); the metric math is the same, the
acceptance contract is motion-gated.

The content classifier names owner_instability as the splat winner-hysteresis
owner and terrain/water churn as the cell-owner product owner -- i.e. exactly the
FL-4359 Tier-B winner-history fix surface (terrain_cell_splat winner bias +
cell_owner_product water deadband).

Usage:
  python3 scripts/analyze_fl4359_moving_camera.py <capture.jsonl> --all-frames \\
      --summary-json out.json --fail-on-gate
"""

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path


def load_rows(path: str) -> list[dict]:
    rows = []
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def group_by_frame(rows: list[dict]) -> dict[int, list[dict]]:
    groups: dict[int, list[dict]] = {}
    for r in rows:
        fid = r.get("frame_id", 0)
        groups.setdefault(fid, []).append(r)
    return groups


def product_key(row: dict) -> tuple:
    return (
        row.get("selected_owner"),
        row.get("selected_product_id"),
        row.get("terrain_cell"),
        row.get("material_role"),
        row.get("actor_id"),
        row.get("water_phase"),
        row.get("terrain_product_detail"),
        row.get("cloud_shadow_phase"),
        foliage_product_key(row),
    )


def foliage_product_key(row: dict) -> tuple:
    if not row.get("foliage_product_present"):
        return (False,)
    return (
        True,
        row.get("foliage_role"),
        row.get("foliage_shape"),
        row.get("foliage_variant"),
        row.get("foliage_density"),
        row.get("foliage_source"),
        row.get("foliage_glyph"),
        row.get("foliage_clump_id"),
        row.get("foliage_coverage"),
        row.get("foliage_effective_y"),
        row.get("foliage_local_u"),
        row.get("foliage_local_v"),
    )


# FL-4359 same-key proof (Codex slice): terrain_detail_fact bit layout — must match
# cell_owner_product.glsl product_pack_terrain_detail_fact (struct variant ~:5467).
def unpack_terrain_detail(fact) -> dict:
    if fact is None:
        return {}
    f = int(fact)
    return {
        "slope_class": f & 0x3,
        "ridge": (f >> 2) & 0x1,
        "cliff": (f >> 3) & 0x1,
        "contact_shadow": (f >> 4) & 0x1,
        "ramp_band": (f >> 5) & 0x7,
        "snow": (f >> 8) & 0x1,
        "glyph_role": (f >> 9) & 0xF,
        "depth_class": (f >> 13) & 0x7,
        "material_tone_band": (f >> 16) & 0xF,
        "clone_peak_glyph_lane": (f >> 20) & 0x1,
        "convex_edge": (f >> 21) & 0x1,
        "concave_edge": (f >> 22) & 0x1,
        "heightfield_neighbor_state": (f >> 23) & 0xF,
        "sidewall_score_class": (f >> 27) & 0xF,
    }


# Semantic fields compared for a same-key terrain decision: the unpacked detail-fact
# sub-decisions plus the final glyph and material role. (NOT depth/owner/coverage/UV.)
SAME_KEY_SEMANTIC_FIELDS = [
    "slope_class", "ridge", "cliff", "contact_shadow", "ramp_band", "snow",
    "glyph_role", "depth_class", "material_tone_band", "clone_peak_glyph_lane",
    "convex_edge", "concave_edge", "heightfield_neighbor_state", "sidewall_score_class",
    "glyph_id", "material_role",
]


def _terrain_same_key(row: dict):
    # (source_key, terr_stable_cell.x, terr_stable_cell.y) for terrain rows only.
    if row.get("selected_owner") != "terrain":
        return None
    sk = row.get("source_key")
    sc = row.get("terr_stable_cell")
    if sk is None or not isinstance(sc, dict):
        return None
    return (int(sk), int(sc.get("x", 0)), int(sc.get("y", 0)))


def _terrain_semantic_signature(row: dict) -> dict:
    sig = unpack_terrain_detail(row.get("terrain_product_detail"))
    sig["glyph_id"] = row.get("glyph_id")
    sig["material_role"] = row.get("material_role")
    return sig


def summarize_same_key_flips(frames: dict) -> dict:
    """Codex same-key proof. For an UNCHANGED (source_key, terr_stable_cell):
      - intra-frame variant count: distinct semantic signatures colliding under one
        key in ONE frame (>1 = a world-key history writer WOULD race; Codex CRIT-1).
      - cross-frame flips: per-field changes for the same key between frames (names
        which semantic decision is unstable; the gate for whether the lane exists).
    Pure post-processing; tolerant of rows lacking the new fields (returns zeros)."""
    frame_ids = sorted(frames.keys())
    terrain_key_rows = 0
    per_frame_groups: dict = {}
    intra_variant_groups = 0
    frame_key_groups = 0
    max_intra_variant = 1
    for fid in frame_ids:
        groups: dict = {}
        for row in frames[fid]:
            key = _terrain_same_key(row)
            if key is None:
                continue
            terrain_key_rows += 1
            sig = _terrain_semantic_signature(row)
            g = groups.setdefault(key, {
                "field_values": {fld: set() for fld in SAME_KEY_SEMANTIC_FIELDS},
                "sig_tuples": set(),
            })
            g["sig_tuples"].add(tuple(sig.get(fld) for fld in SAME_KEY_SEMANTIC_FIELDS))
            for fld in SAME_KEY_SEMANTIC_FIELDS:
                g["field_values"][fld].add(sig.get(fld))
        for g in groups.values():
            frame_key_groups += 1
            vc = len(g["sig_tuples"])
            g["intra_frame_variant_count"] = vc
            if vc > 1:
                intra_variant_groups += 1
            if vc > max_intra_variant:
                max_intra_variant = vc
        per_frame_groups[fid] = groups
    per_field_flip = {fld: 0 for fld in SAME_KEY_SEMANTIC_FIELDS}
    same_key_pairs = 0
    same_key_flip_pairs = 0
    examples: list = []
    for i in range(len(frame_ids) - 1):
        a = per_frame_groups[frame_ids[i]]
        b = per_frame_groups[frame_ids[i + 1]]
        for key, ga in a.items():
            gb = b.get(key)
            if gb is None:
                continue
            same_key_pairs += 1
            flipped = [fld for fld in SAME_KEY_SEMANTIC_FIELDS
                       if ga["field_values"][fld] != gb["field_values"][fld]]
            for fld in flipped:
                per_field_flip[fld] += 1
            if flipped:
                same_key_flip_pairs += 1
                if len(examples) < 30:
                    examples.append({
                        "frame_a": frame_ids[i], "frame_b": frame_ids[i + 1],
                        "source_key": key[0], "stable_x": key[1], "stable_y": key[2],
                        "intra_frame_variant_count_a": ga.get("intra_frame_variant_count", 1),
                        "intra_frame_variant_count_b": gb.get("intra_frame_variant_count", 1),
                        "flipped_fields": flipped,
                    })
    flip_pct = (same_key_flip_pairs / same_key_pairs * 100.0) if same_key_pairs else 0.0
    intra_pct = (intra_variant_groups / frame_key_groups * 100.0) if frame_key_groups else 0.0
    return {
        "same_key_terrain_rows": terrain_key_rows,
        "same_key_frame_groups": frame_key_groups,
        "same_key_intra_frame_variant_groups": intra_variant_groups,
        "same_key_intra_frame_variant_pct": intra_pct,
        "same_key_max_intra_frame_variant_count": max_intra_variant,
        "same_key_cross_frame_pairs": same_key_pairs,
        "same_key_cross_frame_flip_pairs": same_key_flip_pairs,
        "same_key_semantic_flip_pct": flip_pct,
        "same_key_semantic_flip_observed": bool(same_key_flip_pairs > 0 or intra_variant_groups > 0),
        "same_key_per_field_flip_counts": per_field_flip,
        "same_key_flip_examples": examples,
    }


def distance3(a: dict, b: dict) -> float:
    return math.sqrt(
        (a["x"] - b["x"]) ** 2
        + (a["y"] - b["y"]) ** 2
        + (a["z"] - b["z"]) ** 2
    )


def comparable_anchor(a: dict, b: dict, threshold: float = 0.10) -> bool:
    aw = a.get("ansi_cell_world_anchor", {})
    bw = b.get("ansi_cell_world_anchor", {})
    if not aw or not bw:
        return False
    return distance3(aw, bw) <= threshold


def classify_pair(a: dict, b: dict) -> str:
    if not comparable_anchor(a, b):
        return "different_world_sample"
    if a.get("selected_owner") == "actor_sprite" and b.get("selected_owner") == "actor_sprite":
        if a.get("actor_rect_min_px") != b.get("actor_rect_min_px"):
            return "actor_projection_snap"
        if a.get("actor_rect_max_px") != b.get("actor_rect_max_px"):
            return "actor_projection_snap"
    if product_key(a) != product_key(b):
        if a.get("selected_owner") != b.get("selected_owner"):
            return "owner_instability"
        if a.get("terrain_cell") != b.get("terrain_cell"):
            return "terrain_sample_key_drift"
        if foliage_product_key(a) != foliage_product_key(b):
            if a.get("foliage_role") == 4 or b.get("foliage_role") == 4:
                return "flower_product_payload_instability"
            return "foliage_product_payload_instability"
        if a.get("water_phase") != b.get("water_phase"):
            return "water_phase_camera_drift"
        if a.get("terrain_product_detail") != b.get("terrain_product_detail"):
            return "terrain_product_detail_churn"
        return "product_payload_instability"
    secondary_a = (
        a.get("secondary_kind"),
        a.get("secondary_source_id"),
        a.get("secondary_glyph"),
        a.get("secondary_coverage"),
        a.get("secondary_alpha"),
        a.get("secondary_blend"),
        a.get("secondary_intensity"),
    )
    secondary_b = (
        b.get("secondary_kind"),
        b.get("secondary_source_id"),
        b.get("secondary_glyph"),
        b.get("secondary_coverage"),
        b.get("secondary_alpha"),
        b.get("secondary_blend"),
        b.get("secondary_intensity"),
    )
    if secondary_a != secondary_b:
        if a.get("secondary_kind") in (9, 11) or b.get("secondary_kind") in (9, 11):
            return "dynamic_secondary_animation"
        return "secondary_payload_instability"
    if (
        a.get("glyph_id") != b.get("glyph_id")
        or a.get("fg_rgb") != b.get("fg_rgb")
        or a.get("bg_rgb") != b.get("bg_rgb")
        or a.get("final_ink_pixels") != b.get("final_ink_pixels")
        or a.get("final_ink_fg") != b.get("final_ink_fg")
        or a.get("final_ink_bg") != b.get("final_ink_bg")
    ):
        if a.get("selected_owner") == "actor_sprite" and b.get("selected_owner") == "actor_sprite":
            return "dynamic_actor_animation"
        if a.get("selected_owner") == "water" and b.get("selected_owner") == "water":
            return "dynamic_water_animation"
        return "final_paint_or_glyph_instability"
    return "stable"


def compare_frames(frame_a: list[dict], frame_b: list[dict], details_limit: int = 50) -> dict:
    """Compare two frames by matching cells at the same world anchor.

    The handoff says: 'Same world/product anchors keep the same selected product
    id under movement.' So the comparison must match by world anchor proximity,
    NOT by screen cell position. Translation tests move the camera, so the same
    world point appears at different screen cells. Matching by screen cell would
    drop all changed-world-sample rows and make translation look clean.
    """
    # Build a spatial index of frame B by world anchor XZ. The first version
    # used a full nearest-neighbor scan and was too slow for headed movement
    # captures with hundreds of frames.
    b_rows: list[dict] = []
    bucket_size = 2.0
    b_index: dict[tuple[int, int], list[dict]] = {}
    for r in frame_b:
        aw = r.get("ansi_cell_world_anchor", {})
        if not aw or (aw.get("x", 0) == 0 and aw.get("z", 0) == 0):
            continue
        b_rows.append(r)
        bx = int(math.floor(float(aw.get("x", 0.0)) / bucket_size))
        bz = int(math.floor(float(aw.get("z", 0.0)) / bucket_size))
        b_index.setdefault((bx, bz), []).append(r)

    classifications: Counter = Counter()
    detail_rows: list[dict] = []
    total = 0

    for a in frame_a:
        aw = a.get("ansi_cell_world_anchor", {})
        if not aw or (aw.get("x", 0) == 0 and aw.get("z", 0) == 0):
            continue  # skip rows with no world anchor (non-terrain)
        total += 1

        # Find the closest frame B row by world anchor distance.
        best_b = None
        best_dist = float("inf")
        ax = int(math.floor(float(aw.get("x", 0.0)) / bucket_size))
        az = int(math.floor(float(aw.get("z", 0.0)) / bucket_size))
        candidates: list[dict] = []
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                candidates.extend(b_index.get((ax + dx, az + dz), []))
        if not candidates:
            candidates = b_rows
        for b in candidates:
            bw = b.get("ansi_cell_world_anchor", {})
            if not bw:
                continue
            d = distance3(aw, bw)
            if d < best_dist:
                best_dist = d
                best_b = b

        if best_b is None:
            classifications["no_match"] += 1
            continue

        # If the closest anchor is too far, the world point scrolled off-screen.
        if best_dist > 2.0:
            classifications["different_world_sample"] += 1
            continue

        # Use the closest match for classification.
        cls = classify_pair(a, best_b)
        classifications[cls] += 1
        if cls != "stable" and cls != "different_world_sample":
            detail_rows.append({
                "cell_a": a.get("screen_cell"),
                "cell_b": best_b.get("screen_cell"),
                "classification": cls,
                "a_owner": a.get("selected_owner"),
                "b_owner": best_b.get("selected_owner"),
                "a_product_id": a.get("selected_product_id"),
                "b_product_id": best_b.get("selected_product_id"),
                "a_glyph": a.get("glyph_id"),
                "b_glyph": best_b.get("glyph_id"),
                "a_water_phase": a.get("water_phase"),
                "b_water_phase": best_b.get("water_phase"),
                "a_terrain_product_detail": a.get("terrain_product_detail"),
                "b_terrain_product_detail": best_b.get("terrain_product_detail"),
                "a_secondary": {
                    "kind": a.get("secondary_kind"),
                    "source_id": a.get("secondary_source_id"),
                    "glyph": a.get("secondary_glyph"),
                    "coverage": a.get("secondary_coverage"),
                    "alpha": a.get("secondary_alpha"),
                    "blend": a.get("secondary_blend"),
                    "intensity": a.get("secondary_intensity"),
                },
                "b_secondary": {
                    "kind": best_b.get("secondary_kind"),
                    "source_id": best_b.get("secondary_source_id"),
                    "glyph": best_b.get("secondary_glyph"),
                    "coverage": best_b.get("secondary_coverage"),
                    "alpha": best_b.get("secondary_alpha"),
                    "blend": best_b.get("secondary_blend"),
                    "intensity": best_b.get("secondary_intensity"),
                },
                "a_foliage": {
                    "present": a.get("foliage_product_present"),
                    "role": a.get("foliage_role"),
                    "shape": a.get("foliage_shape"),
                    "variant": a.get("foliage_variant"),
                    "density": a.get("foliage_density"),
                    "source": a.get("foliage_source"),
                    "glyph": a.get("foliage_glyph"),
                    "coverage": a.get("foliage_coverage"),
                    "clump_id": a.get("foliage_clump_id"),
                },
                "b_foliage": {
                    "present": best_b.get("foliage_product_present"),
                    "role": best_b.get("foliage_role"),
                    "shape": best_b.get("foliage_shape"),
                    "variant": best_b.get("foliage_variant"),
                    "density": best_b.get("foliage_density"),
                    "source": best_b.get("foliage_source"),
                    "glyph": best_b.get("foliage_glyph"),
                    "coverage": best_b.get("foliage_coverage"),
                    "clump_id": best_b.get("foliage_clump_id"),
                },
                "a_final_ink_pixels": a.get("final_ink_pixels"),
                "b_final_ink_pixels": best_b.get("final_ink_pixels"),
                "a_anchor": a.get("ansi_cell_world_anchor"),
                "b_anchor": best_b.get("ansi_cell_world_anchor"),
                "anchor_dist": best_dist,
            })

    # FL-4359 false-green fix: jitter_pct must be the shimmer rate over
    # WORLD-ANCHOR-MATCHED *comparable* cells only. no_match + different_world_sample
    # (scrolled off / saw a different world point) are excluded from the DENOMINATOR
    # so fast camera motion cannot dilute the percentage into a false pass.
    # Animations stay in the denominator (they are comparable) but not the numerator
    # (a legitimate change, not shimmer).
    no_match = classifications.get("no_match", 0)
    scrolled = classifications.get("different_world_sample", 0)
    animation = (
        classifications.get("dynamic_secondary_animation", 0)
        + classifications.get("dynamic_actor_animation", 0)
        + classifications.get("dynamic_water_animation", 0)
        + classifications.get("water_phase_camera_drift", 0)
        + classifications.get("foliage_product_payload_instability", 0)
        + classifications.get("flower_product_payload_instability", 0)
    )
    comparable = total - no_match - scrolled
    shimmer_cells = comparable - classifications["stable"] - animation
    return {
        "total_matched_cells": total,
        "comparable_cells": comparable,
        "shimmer_cells": shimmer_cells,
        "classifications": dict(classifications),
        "stable_pct": (classifications["stable"] / comparable * 100) if comparable > 0 else 0,
        "jitter_pct": (shimmer_cells / comparable * 100) if comparable > 0 else 0.0,
        "jitter_pct_over_total_legacy": (
            (comparable - classifications["stable"] - animation) / total * 100
        ) if total > 0 else 0.0,
        "detail_rows": detail_rows[:details_limit],
    }


def _vec2_from_row(row: dict, key: str) -> tuple[float, float]:
    value = row.get(key, {})
    if not isinstance(value, dict):
        return (0.0, 0.0)
    return (float(value.get("x", 0.0)), float(value.get("y", 0.0)))


def _vec3_from_row(row: dict, key: str) -> dict:
    value = row.get(key, {})
    if not isinstance(value, dict):
        return {"x": 0.0, "y": 0.0, "z": 0.0}
    return {
        "x": float(value.get("x", 0.0)),
        "y": float(value.get("y", 0.0)),
        "z": float(value.get("z", 0.0)),
    }


def _value_from_row(row: dict, key: str, default=None):
    if key in row:
        return row.get(key, default)
    frame_trace = row.get("frame_trace", {})
    if isinstance(frame_trace, dict):
        return frame_trace.get(key, default)
    return default


def _has_value_from_row(row: dict, key: str) -> bool:
    if key in row:
        return row.get(key) is not None
    frame_trace = row.get("frame_trace", {})
    return isinstance(frame_trace, dict) and key in frame_trace and frame_trace.get(key) is not None


def _float_from_row(row: dict, key: str, default: float = 0.0) -> float:
    value = _value_from_row(row, key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_from_row(row: dict, key: str, default: int = 0) -> int:
    value = _value_from_row(row, key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def summarize_shimmer_metrics(frames: dict[int, list[dict]]) -> dict:
    frame_ids = sorted(frames.keys())
    frame_metrics: list[dict] = []
    previous_snap_px: tuple[float, float] | None = None
    previous_snap_delta: tuple[float, float] | None = None
    max_snap_px_delta = 0.0
    total_snap_px_delta = 0.0
    snap_delta_count = 0
    snap_sign_flip_count = 0
    snap_sign_flip_axis_samples = 0
    projection_modes = Counter()
    fov_values = set()
    near_values = set()
    far_values = set()
    projection_fields_present_frames = 0
    snap_world_values = set()
    snap_world_observed_frames = 0
    # FL-4359 moving-camera requirement: track real camera translation between
    # frames so a steady-pose capture is rejected (jitter/shimmer are only
    # meaningful under continuous sub-cell motion).
    camera_motion_eps = 1.0e-4
    previous_camera_world: dict | None = None
    camera_translation_total = 0.0
    camera_translation_max = 0.0
    camera_translation_pairs = 0
    moving_frame_pairs = 0

    for fid in frame_ids:
        rows = frames.get(fid, [])
        sample = rows[0] if rows else {}
        # FL-4359 defect-2 fix: detect motion on the UNSNAPPED presentation pose.
        # The snapped pose can hold still across a snap quantum while the camera is
        # genuinely translating, which would misclassify a moving run as steady.
        cam_world = _vec3_from_row(sample, "camera_world_unsnapped")
        if cam_world["x"] == 0.0 and cam_world["y"] == 0.0 and cam_world["z"] == 0.0:
            cam_world = _vec3_from_row(sample, "camera_world")  # legacy capture fallback
        if previous_camera_world is not None:
            tdx = cam_world["x"] - previous_camera_world["x"]
            tdy = cam_world["y"] - previous_camera_world["y"]
            tdz = cam_world["z"] - previous_camera_world["z"]
            translation = math.sqrt(tdx * tdx + tdy * tdy + tdz * tdz)
            camera_translation_total += translation
            camera_translation_max = max(camera_translation_max, translation)
            camera_translation_pairs += 1
            if translation > camera_motion_eps:
                moving_frame_pairs += 1
        previous_camera_world = cam_world
        snap_px = _vec2_from_row(sample, "camera_snap_error_px")
        snap_cells = _vec2_from_row(sample, "camera_snap_error_cells")
        style_shift_px = _vec2_from_row(sample, "style_post_shift_px")
        snap_px_delta = 0.0
        current_snap_delta: tuple[float, float] | None = None
        if previous_snap_px is not None:
            current_snap_delta = (
                snap_px[0] - previous_snap_px[0],
                snap_px[1] - previous_snap_px[1],
            )
            snap_px_delta = math.sqrt(
                current_snap_delta[0] ** 2
                + current_snap_delta[1] ** 2
            )
            max_snap_px_delta = max(max_snap_px_delta, snap_px_delta)
            total_snap_px_delta += snap_px_delta
            snap_delta_count += 1
            if previous_snap_delta is not None:
                for axis in range(2):
                    prev_axis = previous_snap_delta[axis]
                    cur_axis = current_snap_delta[axis]
                    if abs(prev_axis) > 1.0e-6 and abs(cur_axis) > 1.0e-6:
                        snap_sign_flip_axis_samples += 1
                        if (prev_axis < 0.0) != (cur_axis < 0.0):
                            snap_sign_flip_count += 1
            previous_snap_delta = current_snap_delta
        previous_snap_px = snap_px
        projection_mode_present = _has_value_from_row(sample, "camera_projection_mode")
        projection_mode = _int_from_row(sample, "camera_projection_mode", 0) if projection_mode_present else None
        camera_fov = _float_from_row(sample, "camera_fov", 0.0)
        camera_near = _float_from_row(sample, "camera_near", 0.0)
        camera_far = _float_from_row(sample, "camera_far", 0.0)
        snap_world_present = _has_value_from_row(sample, "pixel_camera_snap_world")
        snap_world = _float_from_row(sample, "pixel_camera_snap_world", 0.0)
        if projection_mode is not None:
            projection_modes.update([projection_mode])
        if camera_fov > 0.0:
            fov_values.add(round(camera_fov, 4))
        if camera_near > 0.0:
            near_values.add(round(camera_near, 6))
        if camera_far > 0.0:
            far_values.add(round(camera_far, 4))
        if snap_world_present:
            snap_world_observed_frames += 1
            snap_world_values.add(round(snap_world, 8))
        if projection_mode_present and camera_fov > 0.0 and camera_near > 0.0 and camera_far > 0.0:
            projection_fields_present_frames += 1
        frame_metrics.append({
            "frame_id": fid,
            "rows": len(rows),
            "camera_world": _vec3_from_row(sample, "camera_world"),
            "camera_world_unsnapped": _vec3_from_row(sample, "camera_world_unsnapped"),
            "camera_snap_error_cells": {"x": snap_cells[0], "y": snap_cells[1]},
            "camera_snap_error_px": {"x": snap_px[0], "y": snap_px[1]},
            "style_post_shift_px": {"x": style_shift_px[0], "y": style_shift_px[1]},
            "camera_projection_mode": projection_mode,
            "camera_fov": camera_fov,
            "camera_near": camera_near,
            "camera_far": camera_far,
            "pixel_camera_snap_world": snap_world,
            "snap_px_delta_from_previous": snap_px_delta,
        })

    return {
        "frame_metrics": frame_metrics,
        "max_snap_px_delta": max_snap_px_delta,
        "avg_snap_px_delta": (total_snap_px_delta / snap_delta_count) if snap_delta_count else 0.0,
        "snap_sign_flip_count": snap_sign_flip_count,
        "snap_sign_flip_axis_samples": snap_sign_flip_axis_samples,
        "snap_sign_flip_rate": (snap_sign_flip_count / snap_sign_flip_axis_samples) if snap_sign_flip_axis_samples else 0.0,
        "projection_fields_present_frames": projection_fields_present_frames,
        "camera_projection_modes": dict(sorted(projection_modes.items())),
        "camera_fov_values": sorted(fov_values),
        "camera_near_values": sorted(near_values),
        "camera_far_values": sorted(far_values),
        "pixel_camera_snap_world_values": sorted(snap_world_values),
        "pixel_camera_snap_world_observed_frames": snap_world_observed_frames,
        "camera_translation_total": camera_translation_total,
        "camera_translation_max_per_frame": camera_translation_max,
        "camera_translation_pairs": camera_translation_pairs,
        "moving_frame_pairs": moving_frame_pairs,
        "camera_motion_continuous_ratio": (
            moving_frame_pairs / camera_translation_pairs
        ) if camera_translation_pairs else 0.0,
    }


def summarize_foliage_metrics(frames: dict[int, list[dict]]) -> dict:
    role_counts: Counter = Counter()
    selected_owner_counts: Counter = Counter()
    frames_with_foliage_fields = 0
    frames_with_flower_rows = 0
    foliage_rows = 0
    flower_rows = 0
    flower_rows_with_glyph = 0
    flower_rows_with_final_ink = 0
    flower_rows_with_glyph_and_final_ink = 0
    for _fid, rows in frames.items():
        frame_has_fields = any("foliage_product_present" in row for row in rows)
        frame_has_flower = False
        if frame_has_fields:
            frames_with_foliage_fields += 1
        for row in rows:
            if not row.get("foliage_product_present"):
                continue
            foliage_rows += 1
            role = row.get("foliage_role")
            role_counts.update([str(role)])
            selected_owner_counts.update([str(row.get("selected_owner"))])
            if role == 4:
                frame_has_flower = True
                flower_rows += 1
                has_glyph = int(row.get("foliage_glyph") or 0) > 0
                has_ink = int(row.get("final_ink_pixels") or 0) > 0
                if has_glyph:
                    flower_rows_with_glyph += 1
                if has_ink:
                    flower_rows_with_final_ink += 1
                if has_glyph and has_ink:
                    flower_rows_with_glyph_and_final_ink += 1
        if frame_has_flower:
            frames_with_flower_rows += 1
    frame_count = len(frames)
    return {
        "foliage_fields_present_frames": frames_with_foliage_fields,
        "foliage_fields_present_all_frames": frame_count > 0 and frames_with_foliage_fields == frame_count,
        "foliage_product_rows": foliage_rows,
        "foliage_role_counts": dict(sorted(role_counts.items())),
        "foliage_selected_owner_counts": dict(sorted(selected_owner_counts.items())),
        "flower_frames": frames_with_flower_rows,
        "flower_product_rows": flower_rows,
        "flower_rows_with_glyph": flower_rows_with_glyph,
        "flower_rows_with_final_ink": flower_rows_with_final_ink,
        "flower_rows_with_glyph_and_final_ink": flower_rows_with_glyph_and_final_ink,
    }


def build_gate_summary(summary: dict, args: argparse.Namespace) -> dict:
    frame_count = int(summary.get("frame_count", 0))
    row_count = int(summary.get("row_count", 0))
    projection_frames = int(summary.get("projection_fields_present_frames", 0))
    snap_world_values = summary.get("pixel_camera_snap_world_values", [])
    snap_world_frames = int(summary.get("pixel_camera_snap_world_observed_frames", 0))
    # FL-4359 accepts moving-camera evidence only. The camera must actually move
    # across enough frame pairs; otherwise jitter/shimmer are not falsifiable and
    # the gameplay gates must not pass on a steady pose.
    moving_frame_pairs = int(summary.get("moving_frame_pairs", 0))
    camera_motion_ratio = float(summary.get("camera_motion_continuous_ratio", 0.0))
    camera_is_moving = (
        moving_frame_pairs >= args.min_moving_frame_pairs
        and camera_motion_ratio >= args.min_camera_motion_ratio
    )
    # FL-4359 false-green guard: the shimmer gate is only meaningful if enough
    # world-anchor-matched comparable cells exist in every pair. Too few comparable
    # cells (all scrolled/unmatched) must not let the shimmer gate pass vacuously.
    min_comparable_cells = int(summary.get("min_comparable_cells", 0))
    total_comparable_cells = int(summary.get("total_comparable_cells", 0))
    comparable_ok = min_comparable_cells >= args.min_comparable_cells
    gates = {
        "evidence_comparable_cells_present": {
            "pass": comparable_ok,
            "min_comparable_cells": min_comparable_cells,
            "min_comparable_cells_limit": args.min_comparable_cells,
            "total_comparable_cells": total_comparable_cells,
        },
        "scenario_moving_camera_continuous": {
            "pass": camera_is_moving,
            "moving_frame_pairs": moving_frame_pairs,
            "min_moving_frame_pairs": args.min_moving_frame_pairs,
            "camera_motion_continuous_ratio": camera_motion_ratio,
            "min_camera_motion_ratio": args.min_camera_motion_ratio,
            "camera_translation_total": summary.get("camera_translation_total", 0.0),
            "camera_translation_max_per_frame": summary.get("camera_translation_max_per_frame", 0.0),
        },
        "evidence_jitter_capture_present": {
            "pass": row_count > 0 and frame_count > 1 and snap_world_frames == frame_count,
            "row_count": row_count,
            "frame_count": frame_count,
            "pixel_camera_snap_world_observed_frames": snap_world_frames,
            "pixel_camera_snap_world_values": snap_world_values,
        },
        "evidence_projection_fields_present": {
            "pass": frame_count > 0 and projection_frames == frame_count,
            "projection_fields_present_frames": projection_frames,
            "frame_count": frame_count,
            "camera_projection_modes": summary.get("camera_projection_modes", {}),
            "camera_fov_values": summary.get("camera_fov_values", []),
            "camera_near_values": summary.get("camera_near_values", []),
            "camera_far_values": summary.get("camera_far_values", []),
        },
        "evidence_foliage_product_fields_present": {
            "pass": bool(summary.get("foliage_fields_present_all_frames", False)),
            "foliage_fields_present_frames": summary.get("foliage_fields_present_frames", 0),
            "frame_count": frame_count,
        },
        "gameplay_moving_camera_jitter_bounded": {
            "pass": (
                camera_is_moving
                and float(summary.get("max_snap_px_delta", 0.0)) <= args.max_snap_px_delta
                and float(summary.get("snap_sign_flip_rate", 0.0)) <= args.max_snap_sign_flip_rate
            ),
            "camera_is_moving": camera_is_moving,
            "max_snap_px_delta": summary.get("max_snap_px_delta", 0.0),
            "max_snap_px_delta_limit": args.max_snap_px_delta,
            "snap_sign_flip_rate": summary.get("snap_sign_flip_rate", 0.0),
            "snap_sign_flip_rate_limit": args.max_snap_sign_flip_rate,
        },
        "gameplay_content_shimmer_bounded": {
            "pass": (
                camera_is_moving
                and comparable_ok
                and float(summary.get("max_pair_jitter_pct", 0.0)) <= args.max_content_jitter_pct
            ),
            "camera_is_moving": camera_is_moving,
            "comparable_cells_present": comparable_ok,
            "min_comparable_cells": min_comparable_cells,
            "max_pair_jitter_pct": summary.get("max_pair_jitter_pct", 0.0),
            "max_content_jitter_pct_limit": args.max_content_jitter_pct,
        },
        # FL-4359 same-key companion diagnostic (Codex). Companion diagnostic only —
        # the labeled June-14 PNG grid stays the visual acceptance surface (FL-4377);
        # this is a JSONL-side fail-closed check that the capture carries terrain
        # source_key + terr_stable_cell samples the flip analysis needs (Law 6). The
        # flip statistics (same_key_semantic_flip_observed / per-field counts) are
        # measurements, not a pass/fail — an observed flip is data, not a regression.
        "evidence_same_key_terrain_present": {
            "pass": int(summary.get("same_key_terrain_rows", 0)) >= getattr(args, "min_same_key_terrain_rows", 50),
            "same_key_terrain_rows": int(summary.get("same_key_terrain_rows", 0)),
            "min_required": getattr(args, "min_same_key_terrain_rows", 50),
            "same_key_semantic_flip_observed": bool(summary.get("same_key_semantic_flip_observed", False)),
        },
    }
    gates["all_pass"] = all(bool(v.get("pass")) for v in gates.values() if isinstance(v, dict))
    return gates


def main():
    parser = argparse.ArgumentParser(description="FL-4359 moving-camera jitter/shimmer analyzer")
    parser.add_argument("path", help="Path to JSONL capture file")
    parser.add_argument("--frame-a", type=int, default=0, help="Frame ID for A (static)")
    parser.add_argument("--frame-b", type=int, default=1, help="Frame ID for B (moved)")
    parser.add_argument("--all-frames", action="store_true", help="Compare every consecutive frame pair")
    parser.add_argument("--summary-json", default="", help="Write machine-readable shimmer summary JSON")
    parser.add_argument("--max-snap-px-delta", type=float, default=1.0, help="Camera jitter gate limit in native px per frame")
    parser.add_argument("--max-snap-sign-flip-rate", type=float, default=0.05, help="Camera jitter gate limit for residual-axis sign flips")
    parser.add_argument("--max-content-jitter-pct", type=float, default=5.0, help="Content shimmer gate limit as percent of comparable cells")
    parser.add_argument("--min-moving-frame-pairs", type=int, default=4, help="Min frame pairs with real camera translation (moving-camera requirement)")
    parser.add_argument("--min-camera-motion-ratio", type=float, default=0.5, help="Min fraction of frame pairs that show camera translation")
    parser.add_argument("--min-comparable-cells", type=int, default=200, help="Min world-anchor-matched comparable cells per pair (false-green guard)")
    parser.add_argument("--fail-on-gate", action="store_true", help="Return non-zero when any named gate fails")
    parser.add_argument("--min-same-key-terrain-rows", type=int, default=50,
                        help="FL-4359 same-key proof: min terrain rows carrying source_key+terr_stable_cell "
                             "before the same-key flip diagnostic is trusted (fail-closed, Law 6)")
    args = parser.parse_args()

    if not Path(args.path).exists():
        print(f"Error: {args.path} not found", file=sys.stderr)
        sys.exit(1)

    rows = load_rows(args.path)
    if not rows:
        print("Error: no rows found in capture file", file=sys.stderr)
        sys.exit(1)

    frames = group_by_frame(rows)
    frame_ids = sorted(frames.keys())
    print(f"Loaded {len(rows)} rows across {len(frame_ids)} frames: {frame_ids}")
    print()
    pair_results: list[dict] = []

    if args.all_frames:
        for i in range(len(frame_ids) - 1):
            fa = frames[frame_ids[i]]
            fb = frames[frame_ids[i + 1]]
            result = compare_frames(fa, fb)
            pair_results.append({
                "frame_a": frame_ids[i],
                "frame_b": frame_ids[i + 1],
                **result,
            })
            print(f"=== Frame {frame_ids[i]} -> {frame_ids[i+1]} ===")
            print(f"  Matched cells: {result['total_matched_cells']}")
            print(f"  Stable: {result['stable_pct']:.1f}%")
            print(f"  Jitter: {result['jitter_pct']:.1f}%")
            print(f"  Classifications: {result['classifications']}")
            if result["detail_rows"]:
                print(f"  Top jitter examples (first 5):")
                for d in result["detail_rows"][:5]:
                    print(f"    {d['cell_a']}->{d['cell_b']} {d['classification']}: "
                          f"owner {d['a_owner']}->{d['b_owner']} "
                          f"glyph {d['a_glyph']}->{d['b_glyph']} "
                          f"product {d['a_product_id']}->{d['b_product_id']} "
                          f"dist={d['anchor_dist']:.3f}")
            print()
    else:
        if args.frame_a not in frames:
            print(f"Error: frame {args.frame_a} not found", file=sys.stderr)
            sys.exit(1)
        if args.frame_b not in frames:
            print(f"Error: frame {args.frame_b} not found", file=sys.stderr)
            sys.exit(1)

        result = compare_frames(frames[args.frame_a], frames[args.frame_b])
        pair_results.append({
            "frame_a": args.frame_a,
            "frame_b": args.frame_b,
            **result,
        })
        print(f"=== Frame {args.frame_a} (A) -> Frame {args.frame_b} (B) ===")
        print(f"  Matched cells: {result['total_matched_cells']}")
        print(f"  Stable: {result['stable_pct']:.1f}%")
        print(f"  Jitter: {result['jitter_pct']:.1f}%")
        print(f"  Classifications: {result['classifications']}")
        print()
        if result["detail_rows"]:
            print("Jitter detail (first 20):")
            for d in result["detail_rows"][:20]:
                print(f"  {d['cell_a']}->{d['cell_b']} {d['classification']}: "
                      f"owner {d['a_owner']}->{d['b_owner']} "
                      f"glyph {d['a_glyph']}->{d['b_glyph']} "
                      f"product {d['a_product_id']}->{d['b_product_id']} "
                      f"dist={d['anchor_dist']:.3f}")
                print(f"    anchor A={d['a_anchor']} B={d['b_anchor']}")

    # Summary
    print()
    print("Classification guide:")
    print("  stable                       - No change (good)")
    print("  different_world_sample       - Cell sees a different world point (expected during scroll)")
    print("  terrain_sample_key_drift     - Same world area, terrain cell changed (BUG: shader sample key)")
    print("  owner_instability            - Same world area, owner kind changed (BUG: splat winner hysteresis)")
    print("  product_payload_instability  - Same world area, same owner, product id changed (BUG: payload drift)")
    print("  water_phase_camera_drift     - Same world area, water phase changed (animated lane; not shimmer numerator)")
    print("  terrain_product_detail_churn - Same world area, terrain detail fact changed (BUG: terrain product)")
    print("  foliage_product_payload_instability - Same world area, foliage product facts changed (animated lane)")
    print("  flower_product_payload_instability - Same world area, flower product facts changed (animated lane)")
    print("  actor_projection_snap        - Same world area, actor rect jumped (BUG: CPU projection snap)")
    print("  dynamic_water_animation      - Same water product, animated final paint changed")
    print("  final_paint_or_glyph_instability - Same product, glyph/color/ink changed (BUG: final paint)")
    print()
    print("Coverage:")
    print("  terrain: world anchor from r15.yz (terr_sub_world_xz)")
    print("  water:   world anchor from r15.yz (receiver_world.xz); water_phase from r3.z")
    print("  actor:   world anchor from r15.yz (foot world XZ); actor_rect from actor_cell_tex")
    print("  sky/mesh: no world anchor (skipped — cannot match across frames)")

    shimmer = summarize_shimmer_metrics(frames)
    foliage = summarize_foliage_metrics(frames)
    same_key = summarize_same_key_flips(frames)
    max_pair_jitter_pct = max((float(r.get("jitter_pct", 0.0)) for r in pair_results), default=0.0)
    # FL-4359 false-green guard: track world-anchor-matched comparable cells so a
    # run with too few comparable cells (almost everything scrolled/unmatched)
    # cannot pass the shimmer gate vacuously.
    comparable_per_pair = [int(r.get("comparable_cells", 0)) for r in pair_results]
    total_comparable_cells = sum(comparable_per_pair)
    min_comparable_cells = min(comparable_per_pair) if comparable_per_pair else 0
    summary = {
        "path": str(args.path),
        "row_count": len(rows),
        "frame_count": len(frame_ids),
        "frame_ids": frame_ids,
        "max_pair_jitter_pct": max_pair_jitter_pct,
        "total_comparable_cells": total_comparable_cells,
        "min_comparable_cells": min_comparable_cells,
        "pair_count": len(pair_results),
        "pair_results": pair_results,
        **shimmer,
        **foliage,
        **same_key,
    }
    gates = build_gate_summary(summary, args)
    summary["gates"] = gates
    print()
    print("Shimmer metrics:")
    print(f"  Max pair jitter: {max_pair_jitter_pct:.2f}%")
    print(f"  Max snap residual delta: {summary['max_snap_px_delta']:.3f}px")
    print(f"  Avg snap residual delta: {summary['avg_snap_px_delta']:.3f}px")
    print(f"  Snap residual sign flips: {summary['snap_sign_flip_count']} / {summary['snap_sign_flip_axis_samples']} "
          f"({summary['snap_sign_flip_rate']:.3f})")
    print("Foliage product metrics:")
    print(f"  Product rows: {summary['foliage_product_rows']}")
    print(f"  Role counts: {summary['foliage_role_counts']}")
    print(f"  Flower rows: {summary['flower_product_rows']}")
    print(f"  Flower rows with glyph+ink: {summary['flower_rows_with_glyph_and_final_ink']}")
    print("Same-key terrain semantic flips (FL-4359 Codex proof slice):")
    print(f"  Terrain rows w/ source_key+stable_cell: {summary['same_key_terrain_rows']}")
    print(f"  (source_key, stable_cell) frame-groups: {summary['same_key_frame_groups']}")
    print(f"  Intra-frame variant groups (race exposure): {summary['same_key_intra_frame_variant_groups']} "
          f"({summary['same_key_intra_frame_variant_pct']:.2f}%), max variant count={summary['same_key_max_intra_frame_variant_count']}")
    print(f"  Cross-frame same-key pairs: {summary['same_key_cross_frame_pairs']}, "
          f"with a field flip: {summary['same_key_cross_frame_flip_pairs']} ({summary['same_key_semantic_flip_pct']:.2f}%)")
    print(f"  Same-key semantic flip observed: {summary['same_key_semantic_flip_observed']}")
    _nonzero_fields = {k: v for k, v in summary["same_key_per_field_flip_counts"].items() if v}
    print(f"  Per-field flip counts (nonzero): {_nonzero_fields if _nonzero_fields else 'none'}")
    print("  INTERPRETATION: variant>1 or cross-frame flips => a same-key world-history")
    print("    writer would be nondeterministic / the key maps to multiple products.")
    print("    Zero flips => same-key terrain is already stable; hysteresis there is a")
    print("    no-op (Codex HIGH-3) and the semantic-history lane is NOT justified.")
    print("Named gates:")
    for gate_name, gate in gates.items():
        if not isinstance(gate, dict):
            continue
        print(f"  {gate_name}: {'PASS' if gate.get('pass') else 'FAIL'}")
    if args.summary_json:
        Path(args.summary_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary_json).write_text(json.dumps(summary, indent=2) + "\n")
        print(f"Summary JSON: {args.summary_json}")
    if args.fail_on_gate and not gates["all_pass"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
