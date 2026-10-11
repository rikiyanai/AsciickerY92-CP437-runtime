# Ad hoc script: Filter huge FL-4359 jitter JSONL to selected frames without frame_trace for bounded analyzer runs
# Created: 2026-06-25
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

KEEP_KEYS = {
    "actor_candidate_depth",
    "actor_candidate_reject_bits",
    "actor_candidate_world_depth",
    "actor_foot_world",
    "actor_id",
    "actor_rect_min_px",
    "actor_rect_size_px",
    "ansi_cell_world_anchor",
    "bg_rgb",
    "blood_trace",
    "camera_far",
    "camera_fov",
    "camera_near",
    "camera_pitch",
    "camera_projection_mode",
    "camera_snap_error_cells",
    "camera_snap_error_px",
    "camera_snap_offset_world",
    "camera_world",
    "camera_yaw",
    "cloud_shadow_phase",
    "fg_rgb",
    "final_ink_bg",
    "final_ink_fg",
    "final_ink_pixels",
    "final_owner_cell",
    "foliage_clump_id",
    "foliage_coverage",
    "foliage_density",
    "foliage_effective_y",
    "foliage_glyph",
    "foliage_local_u",
    "foliage_local_v",
    "foliage_product_present",
    "foliage_role",
    "foliage_shape",
    "foliage_source",
    "foliage_variant",
    "foliage_wind_sample",
    "frame_id",
    "glyph_id",
    "material_role",
    "phase",
    "pixel_camera_hash",
    "pixel_camera_snap_world",
    "projection_matrix_hash",
    "row_type",
    "screen_cell",
    "secondary_alpha",
    "secondary_blend",
    "secondary_coverage",
    "secondary_glyph",
    "secondary_intensity",
    "secondary_kind",
    "secondary_source_id",
    "selected_owner",
    "selected_product_id",
    "shadow_trace",
    "style_post_shift_px",
    "terrain_cell",
    "terrain_product_detail",
    "view_projection_hash",
    "water_phase",
}


def parse_frames(raw: str) -> set[int]:
    frames: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        frames.add(int(part))
    return frames


def main() -> int:
    parser = argparse.ArgumentParser(description="Filter FL-4359 jitter JSONL to selected frame ids.")
    parser.add_argument("capture", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--frames", required=True, help="Comma-separated frame ids to keep, e.g. 0,60")
    parser.add_argument("--keep-frame-trace", action="store_true")
    args = parser.parse_args()

    wanted = parse_frames(args.frames)
    seen: dict[int, int] = {fid: 0 for fid in wanted}
    total = 0
    written = 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.capture.open("r", encoding="utf-8") as src, args.out.open("w", encoding="utf-8") as dst:
        for line in src:
            total += 1
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            fid = int(row.get("frame_id", -1))
            if fid not in wanted:
                continue
            seen[fid] = seen.get(fid, 0) + 1
            if not args.keep_frame_trace:
                frame_trace = row.get("frame_trace")
                if isinstance(frame_trace, dict) and "pixel_camera_snap_world" in frame_trace:
                    row["pixel_camera_snap_world"] = frame_trace.get("pixel_camera_snap_world")
                row = {key: row.get(key) for key in KEEP_KEYS if key in row}
            dst.write(json.dumps(row, separators=(",", ":")) + "\n")
            written += 1
    print(json.dumps({"input": str(args.capture), "output": str(args.out), "total_rows": total, "written_rows": written, "frames": seen}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
