#!/usr/bin/env python3
"""generate_wolfie_mounted_idle_rider_body.py

Generate the mounted idle rider-body visual layer for the wolf mount.

Owner: ActorVisualProfile pipeline (FL-4048/FL-4049).

Source truth:
- Rider pixels come from the upstream monolith: assets/sprites/wolfie-0000.xp layer 3.
  (180x96, 10x12 frames, angles=8, projs=2, anims=[1,8])
- Output topology must match mounted wrappers: 180x104, 10x13 frames, same metadata,
  same ref[2] plane. We take L0/L1 from the validated padded base:
  assets/sprites/wolfie-mount-base-padded.xp (L0+L1 authority).

This is a mechanical transform (no inference):
1. Copy L0 and L1 from wolfie-mount-base-padded.xp.
2. For each 10x12 frame in wolfie-0000 L3, paste into the 10x13 slot at y+1
   (top row padding remains transparent).
3. Rebase transparency keys in the visual layer from the source key (wolfie-0000 L0)
   to the output key (base padded L0) using xp_core.rebase_visual_layer_transparency_keys.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pipeline.xp_core import XPFile, XPLayer, rebase_visual_layer_transparency_keys  # type: ignore


def _load_xp_quiet(path: Path) -> XPFile:
    with contextlib.redirect_stdout(io.StringIO()):
        return XPFile(str(path))


def _blank_layer_like(layer: XPLayer) -> XPLayer:
    return XPLayer(layer.width, layer.height)


def _paste_frames_pad_top(
    src: XPLayer,
    dst: XPLayer,
    frame_w: int,
    frame_h_src: int,
    frame_h_dst: int,
    angles: int,
    projs: int,
    anims: list[int],
) -> None:
    anim_sum = sum(anims)
    cols = projs * anim_sum
    rows = angles
    if src.width != cols * frame_w or src.height != rows * frame_h_src:
        raise ValueError(f"src dims {src.width}x{src.height} do not match grid {cols}x{rows} * {frame_w}x{frame_h_src}")
    if dst.width != cols * frame_w or dst.height != rows * frame_h_dst:
        raise ValueError(f"dst dims {dst.width}x{dst.height} do not match grid {cols}x{rows} * {frame_w}x{frame_h_dst}")
    if frame_h_dst != frame_h_src + 1:
        raise ValueError("expected dst frame height to be src+1 (top padding)")

    for angle in range(angles):
        for col in range(cols):
            src_x0 = col * frame_w
            src_y0 = angle * frame_h_src
            dst_x0 = col * frame_w
            dst_y0 = angle * frame_h_dst
            # Top padding row (dst_y0 + 0) left as default empty cells.
            for y in range(frame_h_src):
                for x in range(frame_w):
                    dst.data[dst_y0 + 1 + y][dst_x0 + x] = src.data[src_y0 + y][src_x0 + x]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="assets/sprites/wolfie-0000.xp")
    ap.add_argument("--base", default="assets/sprites/wolfie-mount-base-padded.xp")
    ap.add_argument("--out", default="assets/sprites/wolfie-mounted-idle-rider-body.xp")
    args = ap.parse_args(argv)

    src_path = REPO_ROOT / args.source
    base_path = REPO_ROOT / args.base
    out_path = REPO_ROOT / args.out

    src = _load_xp_quiet(src_path)
    base = _load_xp_quiet(base_path)
    if len(src.layers) < 4:
        raise SystemExit(f"{src_path} expected >=4 layers, got {len(src.layers)}")
    if len(base.layers) < 2:
        raise SystemExit(f"{base_path} expected >=2 layers, got {len(base.layers)}")

    meta = src.get_metadata() or {}
    angles = int(meta.get("angles", 1))
    projs = int(meta.get("projs", 1))
    anims = [int(x) for x in (meta.get("anims", []) or [1])]

    # Source rider layer assumed to be layer 3.
    src_rider = src.layers[3]
    base_l0 = base.layers[0]
    base_l1 = base.layers[1]

    # Frame geometry from source and base.
    frame_w = 10
    frame_h_src = src_rider.height // angles
    frame_h_dst = base_l0.height // angles
    if frame_h_dst != frame_h_src + 1:
        raise SystemExit(f"unexpected frame heights: src={frame_h_src} dst={frame_h_dst} (want dst=src+1)")

    out = XPFile()
    out.layers = []
    out.layers.append(base_l0)
    out.layers.append(base_l1)
    out_vis = _blank_layer_like(base_l0)
    _paste_frames_pad_top(
        src_rider,
        out_vis,
        frame_w=frame_w,
        frame_h_src=frame_h_src,
        frame_h_dst=frame_h_dst,
        angles=angles,
        projs=projs,
        anims=anims,
    )

    # Rebase transparency keys from src L0 -> base L0.
    rebase_visual_layer_transparency_keys(out_vis, src.layers[0], base_l0)
    out.layers.append(out_vis)

    out.save(str(out_path))
    print(str(out_path.relative_to(REPO_ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
