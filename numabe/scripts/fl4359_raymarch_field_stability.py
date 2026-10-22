#!/usr/bin/env python3
"""Measure direct-ray repeats without relabeling retained texels as ray hits."""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path

from fl4359_actual_field import ActualFieldError, ActualFieldReader, sha256_file
from fl4359_actual_field_stability import (
    COLUMNS, THRESHOLD, compare_samples, load_observed_atlases, mask_error,
    runtime_readback_health, samples,
)
from fl4359_raster_jitter_capture import stream_frames

RAY_COLUMNS = (
    "selected_surface_owner", "selected_surface_object",
    "selected_surface_primitive", "selected_surface_material",
    "selected_surface_world_position", "selected_surface_scene_revision",
    "oracle_dynamic_revision", "oracle_record_valid", "oracle_query_valid",
    "oracle_direct_hit", "oracle_production_hit",
    "oracle_identity_agrees_with_selected_surface",
    "final_product_status",
)


def ray_key(row):
    for flag in ("oracle_record_valid", "oracle_query_valid", "oracle_direct_hit",
                 "oracle_production_hit", "oracle_identity_agrees_with_selected_surface"):
        if row.get(flag) is not True:
            return None
    world = row.get("selected_surface_world_position", [])
    if len(world) != 3 or not all(math.isfinite(x) for x in world):
        return None
    identity = tuple(row["selected_surface_" + name]
                     for name in ("owner", "object", "primitive", "material"))
    # VSB owners 1/2 are static mesh/terrain. An unrelated actor publication
    # must not invalidate a static surface's exact-source comparison.
    dynamic_revision = row["oracle_dynamic_revision"] if identity[0] in (3, 4) else None
    return (identity, tuple(world), row["selected_surface_scene_revision"], dynamic_revision)


def ray_samples(rows, field):
    bank, counts = {}, Counter()
    cols, height = field.metadata["semantic_ansi_size"]
    width_px, height_px = field.metadata["logical_target_size"]
    for row in rows:
        counts["rows"] += 1
        key = ray_key(row)
        if key is None:
            counts["no_valid_agreeing_ray_hit"] += 1
            continue
        cell, slot = row["screen_cell"], row["subcell_slot"]
        if slot not in (0, 1, 2, 3):
            raise ActualFieldError("invalid subcell slot")
        x = int((cell["x"] * 2 + slot % 2 + 0.5) * width_px / (cols * 2))
        y = int((cell["y"] * 2 + slot // 2 + 0.5) * height_px / (height * 2))
        if not (0 <= x < field.width and 0 <= y < field.height):
            counts["outside_native_image"] += 1
            continue
        counts["admitted_ray_hits"] += 1
        probe_world = row["probe_prepack_world_position"]
        if tuple(probe_world[:3]) == key[1]:
            counts["exact_ray_probe_world_matches"] += 1
        bank.setdefault(key, []).append((
            field.fields["fg_rgb"][y, x].copy(),
            field.fields["bg_rgb"][y, x].copy(),
            float(field.fields["effective_coverage"][y, x]),
        ))
    counts["distinct_ray_sources"] = len(bank)
    return bank, dict(counts)


def direct_product_masks(rows, field):
    """Check the direct Product mask, not the retained-only presented_surface."""
    pages = load_observed_atlases(field.metadata)
    cell_statuses, masks, counts = {}, {}, Counter()
    for row in rows:
        xy = row["screen_cell"]["x"], row["screen_cell"]["y"]
        cell_statuses.setdefault(xy, []).append(row["final_product_status"])
    cw, ch = field.metadata["cell_size"]
    for (cx, cy), statuses in cell_statuses.items():
        counts["cells"] += 1
        if len(statuses) != 4 or not all(s & (1 << 6) for s in statuses):
            counts["invalid_or_incomplete_product_cells"] += 1
            continue
        glyphs = {(s & 0x000FFF00) >> 8 for s in statuses}
        if len(glyphs) != 1:
            counts["disagreeing_product_cells"] += 1
            continue
        glyph = glyphs.pop()
        page = next((p for p in pages.values()
                     if p.spec["glyph_min"] <= glyph <= p.spec["glyph_max"]), None)
        if page is None:
            counts["unmapped_glyph_cells"] += 1
            continue
        if glyph not in masks:
            masks[glyph] = page.mask(glyph, ch, cw)
        x, y = cx * cw, cy * ch
        w, h = min(cw, field.width - x), min(ch, field.height - y)
        if w > 0 and h > 0:
            error = mask_error(field.fields["native_intensity"][y:y+h, x:x+w], masks[glyph][:h, :w])
            counts["checked_cells"] += 1
            counts["mismatching_cells"] += error["different_pixels"] > 0
            counts["mismatching_pixels"] += error["different_pixels"]
    return dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    reader = ActualFieldReader(args.capture)
    frames, ray_pairs, transfer_pairs = [], [], []
    prior_ray = prior_probe = None
    health = runtime_readback_health(args.capture.parent / "godot_client.log")
    if health["status"] != "clean":
        raise ActualFieldError("runtime GPU readback is not clean")
    for sequence, (frame_id, trace, rows, count) in enumerate(stream_frames(
            args.capture, 5, columns=tuple(dict.fromkeys(COLUMNS + RAY_COLUMNS)))):
        if count != trace.get("oracle_expected_subcells_per_frame"):
            raise ActualFieldError("incomplete full-grid capture")
        field = reader.read(trace, frame_id=frame_id, sequence_index=sequence)
        if (field.policy.get("mode") != "FF" or field.projection != "perspective"
                or field.metadata.get("tilt_shift_enabled") is not False
                or field.metadata.get("tilt_shift_intensity") != 0):
            raise ActualFieldError("requires perspective FF with tilt shift off")
        ray, population = ray_samples(rows, field)
        probe, probe_population = samples(rows, field)
        frames.append({"frame_id": frame_id, "engine_frame": field.actual_frame_index,
                       "camera_world": trace.get("camera_world"),
                       "field_sha256": field.metadata["raw_sha256"],
                       "ray_population": population, "probe_population": probe_population,
                       "direct_product_mask_observation": direct_product_masks(rows, field)})
        if prior_ray is not None:
            ray_pairs.append(compare_samples(prior_ray, ray))
            transfer_pairs.append(compare_samples(prior_probe, probe))
        prior_ray, prior_probe = ray, probe
        del field
        print(f"frame {frame_id}: {population}", flush=True)
    if len(frames) != 5 or any(b["engine_frame"] != a["engine_frame"] + 1
                              for a, b in zip(frames, frames[1:])):
        raise ActualFieldError("five contiguous frames required")
    result = {
        "schema": "fl4359.direct_ray_field_stability.v1",
        "capture": str(args.capture.resolve()), "capture_sha256": sha256_file(args.capture),
        "threshold": THRESHOLD, "runtime_readback_health": health,
        "frames": frames, "direct_ray_pairs": ray_pairs,
        "retained_probe_registration_diagnostic": transfer_pairs,
        "direct_ray_source_join": "Exact selected owner/object/primitive/material, world xyz and scene revision; water/dynamic owners also require dynamic revision. Oracle must agree with production.",
        "sample_lattice": "Nearest native pixel to each query quarter centre, same lattice as the archived retained-source diagnostic.",
        "ray_pairs_all_have_comparisons": all(p["channels"]["fg"]["comparable_samples"] for p in ray_pairs),
        "archived_splat_ff": {"fg_changed": 13009, "bg_changed": 11496, "comparisons": 19798},
        "strict_archived_comparison_admitted": False,
        "claim_boundary": "Retained-probe registration values are NOT verified same-source ray shimmer. Exact ray hits use a different population from retained texels. Null rates mean no comparisons, not zero shimmer. No renderer acceptance or performance claim.",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(args.out, flush=True)


if __name__ == "__main__":
    main()
