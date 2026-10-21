#!/usr/bin/env python3
# =============================================================================
# fl4547_skeleton_walk.py
# =============================================================================
# FL-4547 — SKELETON-first walking pipeline, PER AUTHORED BODY PART.
# -----------------------------------------------------------------------------
# THE CORRECTION THAT MAKES THIS WORK (plain language)
# ----------------------------------------------------
# Earlier Xu-style attempts skeletonized the FLATTENED figure — one silhouette —
# and at every junction (hip, shoulder) the medial axis merged, so the thinned
# line no longer knew which branch was a leg, an arm, or the torso. Semantic
# ownership was lost exactly where it mattered.
#
# The source art already solves this: the 8-direction pixel walk sprite is a
# COLOUR-KEYED RIG. Every body part is painted a distinct pure colour, and the
# colour is constant across all frames and directions. That colour IS the
# authored, persistent limb identity — available BEFORE we thin anything. So we
# split each frame into six colour masks first, then skeletonize EACH PART ON
# ITS OWN. The hip junction never merges leg into torso, because the leg is
# thinned in isolation. Identity survives thinning.
#
# THE SIX PARTS (decoded from the rig):
#   cyan   #00fff5  head       (self-mirror)
#   orange #ff8400  torso      (self-mirror)
#   purple #b000ff  arm_a  <-> yellow (arm_b)     mirror pair
#   yellow #fff700  arm_b  <-> purple (arm_a)
#   green  #3bff00  leg_a  <-> red    (leg_b)     mirror pair
#   red    #ff0f00  leg_b  <-> green  (leg_a)
#
# STEP 1 (this file): render the per-part skeleton as a colour-coded 8-direction
# stick-figure walk GIF, in the walk_all_directions 2x4 grid format. Seeing each
# limb as its own clean coloured centreline confirms identity is preserved. The
# ASCIIFY step (map each part's skeleton to a Unicode glyph, with mirror-partner
# symmetry per the hand-authored sprite_frames example) builds on this.
#
# SOURCE (per-direction, higher-res than the combined gif): 384x216, 18 frames
#   ~/Downloads/8-Direction Pixel Character Base/Walk_(Standard)/<DIR>/<DIR>.aseprite
#
# RUN (needs the CLIPasso venv: numpy / Pillow / scikit-image)
#   .scratch/gradscii-art/.venv/bin/python scripts/fl4547_skeleton_walk.py skeleton
# =============================================================================
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import struct
import zlib
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TRACK_DIR = REPO / "docs" / "research" / "ascii" / "verification" / "fl4547" / "skeleton-versions"
REGISTRY = TRACK_DIR / "registry.json"
DESKTOP = Path.home() / "Desktop"

SOURCE_ROOT = Path.home() / "Downloads" / "8-Direction Pixel Character Base" / "Walk_(Standard)"

# FL-4561: the operator rejected the reconstructed named-joint rigs because
# their motion differs from the accepted per-part skeleton.  The only active
# source checkpoint is ACCEPTED_PERPART_CHECKPOINT; rig generation has no CLI.
CLIPASSO = REPO / ".scratch" / "CLIPasso"
EMOJI_DATA = REPO / "assets" / "unicode" / "16.0.0" / "emoji" / "emoji-data.txt"
UNIVERSAL_SHAPE_CATALOG = (
    REPO / "assets" / "glyphs" / "generated" / "unicode16.onecell.v1.shape_catalog.json"
)
DESCRIPTOR_CENSUS = REPO / "assets" / "glyphs" / "generated" / "fl4512.descriptor_census.v1.jsonl"
DESCRIPTOR_MANIFEST = REPO / "assets" / "glyphs" / "generated" / "fl4512.descriptor_census.v1.manifest.json"
ACCEPTED_PERPART_CHECKPOINT = (
    TRACK_DIR / "v20260717T140724__skeleton-perpart__walk_all_directions.gif"
)
ACCEPTED_PERPART_CHECKPOINT_SHA256 = (
    "38149fd8f926e5bb62c6223f96003caa47bbd7cb914331f8a2b553ab65e8a455"
)

# Display order for the 2x4 grid (matches walk_all_directions.gif).
DISPLAY_DIRS = ["S", "SW", "W", "NW", "N", "NE", "E", "SE"]
GRID_COLS, GRID_ROWS = 4, 2

# The colour-keyed rig: exact RGB -> (part name, mirror partner, display colour).
# The display colour is the rig colour itself, so the stick figure is legible.
PART_BY_RGB = {
    (0, 255, 245): "head",
    (255, 132, 0): "torso",
    (176, 0, 255): "arm_a",
    (255, 247, 0): "arm_b",
    (59, 255, 0): "leg_a",
    (255, 15, 0): "leg_b",
}
MIRROR_PART = {"head": "head", "torso": "torso",
               "arm_a": "arm_b", "arm_b": "arm_a",
               "leg_a": "leg_b", "leg_b": "leg_a"}
MIRROR_DIR = {"E": "W", "W": "E", "NE": "NW", "NW": "NE", "SE": "SW", "SW": "SE",
              "N": "N", "S": "S"}
RIG_SCHEMA = "fl4561.semantic_skeletal_rig_stabilized.v2"
GRID_W = GRID_H = 5
PERPART_GRID_W, PERPART_GRID_H = 5, 7
PERPART_SUBPIXELS = 8
ROOT_TARGET = (2.5, 3.05)
PART_RGB_BY_NAME = {part: rgb for rgb, part in PART_BY_RGB.items()}

JOINT_META = {
    "head": ("head", "midline"),
    "neck": ("torso", "midline"),
    "spine": ("torso", "midline"),
    "pelvis": ("torso", "midline"),
    "shoulder_a": ("arm_a", "a"),
    "elbow_a": ("arm_a", "a"),
    "wrist_a": ("arm_a", "a"),
    "shoulder_b": ("arm_b", "b"),
    "elbow_b": ("arm_b", "b"),
    "wrist_b": ("arm_b", "b"),
    "hip_a": ("leg_a", "a"),
    "knee_a": ("leg_a", "a"),
    "ankle_a": ("leg_a", "a"),
    "hip_b": ("leg_b", "b"),
    "knee_b": ("leg_b", "b"),
    "ankle_b": ("leg_b", "b"),
}

# These are the semantic segments whose cell occupancy is measured. Shoulder
# and hip attachment chords are drawn separately and never inflate the two-
# segment limb budget.
BONE_SPECS = [
    ("torso_lower", "pelvis", "spine", "torso"),
    ("torso_upper", "spine", "neck", "torso"),
    ("head_axis", "neck", "head", "head"),
    ("upper_arm_a", "shoulder_a", "elbow_a", "arm_a"),
    ("forearm_a", "elbow_a", "wrist_a", "arm_a"),
    ("upper_arm_b", "shoulder_b", "elbow_b", "arm_b"),
    ("forearm_b", "elbow_b", "wrist_b", "arm_b"),
    ("thigh_a", "hip_a", "knee_a", "leg_a"),
    ("shin_a", "knee_a", "ankle_a", "leg_a"),
    ("thigh_b", "hip_b", "knee_b", "leg_b"),
    ("shin_b", "knee_b", "ankle_b", "leg_b"),
]
ATTACHMENT_SPECS = [
    ("shoulder_attach_a", "neck", "shoulder_a", "arm_a"),
    ("shoulder_attach_b", "neck", "shoulder_b", "arm_b"),
    ("hip_attach_a", "pelvis", "hip_a", "leg_a"),
    ("hip_attach_b", "pelvis", "hip_b", "leg_b"),
]


# -----------------------------------------------------------------------------
# Minimal Aseprite decoder (no external deps; the format is a documented series
# of chunks, and cel pixel data is raw RGBA compressed with zlib).
# -----------------------------------------------------------------------------
def decode_aseprite(path: Path):
    """Return a list of HxWx4 uint8 RGBA frames from a (single-layer) .aseprite.

    We only need the composited image per frame, so we read each frame's cel
    chunk (type 0x2005), inflate its zlib pixel data, and paste it at the cel's
    (x, y) onto a transparent canvas of the file's declared size."""
    import numpy as np

    data = path.read_bytes()
    frames, width, height, depth = struct.unpack_from("<HHHH", data, 6)
    if depth != 32:
        raise SystemExit(f"expected 32bpp RGBA aseprite, got {depth}bpp: {path}")
    out = []
    off = 128                                   # skip the 128-byte file header
    for _ in range(frames):
        frame_size = struct.unpack_from("<I", data, off)[0]
        # chunk count: new 32-bit field at +12, else old 16-bit at +6
        nch = struct.unpack_from("<I", data, off + 12)[0] or struct.unpack_from("<H", data, off + 6)[0]
        co = off + 16
        canvas = np.zeros((height, width, 4), np.uint8)
        for _ in range(nch):
            csize, ctype = struct.unpack_from("<IH", data, co)
            body = data[co + 6:co + csize]
            if ctype == 0x2005:                 # cel chunk
                cx, cy = struct.unpack_from("<hh", body, 2)      # signed cel origin
                cw, ch = struct.unpack_from("<HH", body, 16)     # cel pixel size
                pix = np.frombuffer(zlib.decompress(body[20:]), np.uint8).reshape(ch, cw, 4)
                canvas[cy:cy + ch, cx:cx + cw] = pix
            co += csize
        out.append(canvas)
        off += frame_size
    return out


# -----------------------------------------------------------------------------
# Per-part skeletonization.
# -----------------------------------------------------------------------------
def part_masks(frame_rgba):
    """Split one RGBA frame into {part_name: boolean mask} using the rig colours.
    Each returned mask is that one body part only — so it can be thinned alone."""
    import numpy as np
    rgb = frame_rgba[:, :, :3]
    alpha = frame_rgba[:, :, 3] > 0
    masks = {}
    for (r, g, b), part in PART_BY_RGB.items():
        m = alpha & (rgb[:, :, 0] == r) & (rgb[:, :, 1] == g) & (rgb[:, :, 2] == b)
        if m.any():
            masks[part] = m
    return masks


def union_bbox(frames):
    """A single crop box covering every opaque pixel across all frames of one
    direction, so the figure stays put in the panel instead of jittering."""
    import numpy as np
    ys_all, xs_all = [], []
    for fr in frames:
        ys, xs = np.where(fr[:, :, 3] > 0)
        if len(xs):
            ys_all.append((ys.min(), ys.max()))
            xs_all.append((xs.min(), xs.max()))
    y0 = min(a for a, _ in ys_all); y1 = max(b for _, b in ys_all)
    x0 = min(a for a, _ in xs_all); x1 = max(b for _, b in xs_all)
    pad = 2
    return (max(0, y0 - pad), y1 + pad + 1, max(0, x0 - pad), x1 + pad + 1)


def part_skeletons(frame_rgba, bbox, upscale: int):
    """Return {part: boolean skeleton} for one frame, cropped to `bbox` and
    upscaled. Each part is skeletonized independently (identity preserved)."""
    import numpy as np
    from PIL import Image
    from skimage import morphology

    y0, y1, x0, x1 = bbox
    result = {}
    for part, mask in part_masks(frame_rgba).items():
        sub = mask[y0:y1, x0:x1]
        if upscale > 1:
            big = Image.fromarray(sub.astype(np.uint8) * 255).resize(
                (sub.shape[1] * upscale, sub.shape[0] * upscale), Image.NEAREST)
            sub = np.asarray(big) > 127
        result[part] = morphology.skeletonize(sub)   # thin THIS part alone
    return result


def render_panel(skeletons, panel_w, panel_h):
    """Draw all six part skeletons in their rig colours on one dark panel,
    aspect-preserved and centred."""
    import numpy as np
    from PIL import Image
    from skimage import morphology

    # every part shares the same cropped canvas size -> take it from any part
    any_sk = next(iter(skeletons.values()))
    h, w = any_sk.shape
    rgb = np.full((h, w, 3), 12, np.uint8)
    inv = {v: k for k, v in PART_BY_RGB.items()}
    for part, sk in skeletons.items():
        thick = morphology.dilation(sk)              # 1px -> visible (3x3 footprint)
        rgb[thick] = inv[part]                        # paint in the rig colour
    img = Image.fromarray(rgb, "RGB")
    scale = min(panel_w / w, panel_h / h)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    img = img.resize((nw, nh), Image.NEAREST)
    panel = Image.new("RGB", (panel_w, panel_h), (12, 12, 12))
    panel.paste(img, ((panel_w - nw) // 2, (panel_h - nh) // 2))
    return panel


def cmd_skeleton(args: argparse.Namespace) -> int:
    from PIL import Image, ImageDraw, ImageFont

    # 1. decode every direction's frames + a stable per-direction crop box ------
    dir_frames, dir_bbox = {}, {}
    n_frames = None
    for d in DISPLAY_DIRS:
        src = SOURCE_ROOT / d / f"{d}.aseprite"
        if not src.exists():
            raise SystemExit(f"missing rig source: {src}")
        frames = decode_aseprite(src)
        dir_frames[d] = frames
        dir_bbox[d] = union_bbox(frames)
        n_frames = len(frames) if n_frames is None else min(n_frames, len(frames))
    print(f"decoded {len(DISPLAY_DIRS)} directions x {n_frames} frames (6-colour rig)")

    # 2. per-part skeleton for every direction/frame ----------------------------
    skels = {d: [part_skeletons(dir_frames[d][fi], dir_bbox[d], args.upscale)
                 for fi in range(n_frames)] for d in DISPLAY_DIRS}

    # 3. compose the animated colour-coded 2x4 stick-figure grid ----------------
    panel_w, panel_h = 120, 150
    label_h, pad = 16, 6
    canvas_w = pad + GRID_COLS * (panel_w + pad)
    canvas_h = pad + GRID_ROWS * (panel_h + label_h + pad)
    label_font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 12)

    gif_frames = []
    for fi in range(n_frames):
        canvas = Image.new("RGB", (canvas_w, canvas_h), (8, 8, 8))
        draw = ImageDraw.Draw(canvas)
        for idx, d in enumerate(DISPLAY_DIRS):
            gc, gr = idx % GRID_COLS, idx // GRID_COLS
            x = pad + gc * (panel_w + pad)
            y = pad + gr * (panel_h + label_h + pad)
            draw.text((x, y), f"{d} f{fi}", fill=(150, 210, 150), font=label_font)
            canvas.paste(render_panel(skels[d][fi], panel_w, panel_h), (x, y + label_h))
        gif_frames.append(canvas)

    # 4. versioned Desktop + tracked output -------------------------------------
    timestamp = datetime.now().strftime("v%Y%m%dT%H%M%S")
    name = f"{timestamp}__skeleton-perpart__walk_all_directions"
    TRACK_DIR.mkdir(parents=True, exist_ok=True)
    tracked_gif = TRACK_DIR / f"{name}.gif"
    desktop_gif = DESKTOP / f"{name}.gif"
    gif_frames[0].save(tracked_gif, save_all=True, append_images=gif_frames[1:],
                       duration=args.frame_ms, loop=0, disposal=2)
    desktop_gif.write_bytes(tracked_gif.read_bytes())

    registry = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else []
    registry.append({
        "name": name,
        "kind": "skeleton-perpart-colorcoded",
        "timestamp": timestamp,
        "source": str(SOURCE_ROOT),
        "parts": list(PART_BY_RGB.values()),
        "mirror_pairs": {"arm_a": "arm_b", "leg_a": "leg_b"},
        "directions": DISPLAY_DIRS,
        "frames_per_dir": n_frames,
        "upscale": args.upscale,
        "note": args.note,
        "tracked_gif": str(tracked_gif.relative_to(REPO)),
        "desktop_gif": str(desktop_gif),
        "gif_sha256": hashlib.sha256(tracked_gif.read_bytes()).hexdigest(),
    })
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=1) + "\n")

    print(f"\nwrote per-part skeleton @ {timestamp}")
    print(f"  desktop : {desktop_gif}")
    print(f"  tracked : {tracked_gif.relative_to(REPO)}")
    print(f"  parts   : {', '.join(PART_BY_RGB.values())} (mirror: arm_a<->arm_b, leg_a<->leg_b)")
    return 0


# =============================================================================
# FL-4561 EXPERIMENT SLICE 1 — NAMED RIG + FIXED-ROOT 5x5 PROJECTION
# =============================================================================
# This command owns normal 5x5 placement. It proposes named joints from each
# authored colour mask, fills fully occluded tracks cyclically, applies one
# fixed anatomy-aware transform, and translates every pelvis to ROOT_TARGET. The proposal stays
# review_state=proposed; colour identity is persistent evidence, not accepted
# anatomical left/right review.

def _dijkstra_farthest(nodes: set[tuple[int, int]], start: tuple[int, int]):
    """Return the farthest skeleton node plus parent links using 8-connectivity."""
    dist = {start: 0.0}
    parent: dict[tuple[int, int], tuple[int, int]] = {}
    queue = [(0.0, start)]
    while queue:
        d0, node = heapq.heappop(queue)
        if d0 != dist[node]:
            continue
        y, x = node
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nxt = (y + dy, x + dx)
                if nxt not in nodes:
                    continue
                nd = d0 + math.hypot(dx, dy)
                if nd < dist.get(nxt, float("inf")):
                    dist[nxt] = nd
                    parent[nxt] = node
                    heapq.heappush(queue, (nd, nxt))
    farthest = max(dist, key=lambda n: (dist[n], n))
    return farthest, parent, dist


def longest_skeleton_path(mask) -> list[tuple[float, float]]:
    """Return a deterministic approximate geodesic diameter as `(x, y)` points."""
    import numpy as np
    from skimage import morphology

    skeleton = morphology.skeletonize(mask)
    nodes = {(int(y), int(x)) for y, x in np.argwhere(skeleton)}
    if not nodes:
        return []
    if len(nodes) == 1:
        y, x = next(iter(nodes))
        return [(float(x), float(y))]
    first, _p0, _d0 = _dijkstra_farthest(nodes, min(nodes))
    last, parent, _d1 = _dijkstra_farthest(nodes, first)
    rev = [last]
    while rev[-1] != first:
        rev.append(parent[rev[-1]])
    rev.reverse()
    return [(float(x), float(y)) for y, x in rev]


def point_on_path(path: list[tuple[float, float]], fraction: float) -> tuple[float, float]:
    """Interpolate a point by geodesic arclength along a polyline."""
    if not path:
        raise ValueError("cannot sample an empty path")
    if len(path) == 1:
        return path[0]
    lengths = [math.dist(path[i - 1], path[i]) for i in range(1, len(path))]
    total = sum(lengths)
    if total <= 0.0:
        return path[0]
    target = min(1.0, max(0.0, fraction)) * total
    walked = 0.0
    for i, seg in enumerate(lengths, start=1):
        if walked + seg >= target:
            t = (target - walked) / seg if seg else 0.0
            a, b = path[i - 1], path[i]
            return (a[0] + (b[0] - a[0]) * t,
                    a[1] + (b[1] - a[1]) * t)
        walked += seg
    return path[-1]


def _mask_centroid(mask) -> tuple[float, float] | None:
    import numpy as np
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    return (float(xs.mean()), float(ys.mean()))


def _path_proximal_first(path, reference_mask):
    """Order a limb path from the endpoint nearest the torso to the distal tip."""
    import numpy as np
    if len(path) < 2:
        return path
    ys, xs = np.where(reference_mask)
    if len(xs) == 0:
        return path

    def distance2(p):
        return float(np.min((xs - p[0]) ** 2 + (ys - p[1]) ** 2))

    return path if distance2(path[0]) <= distance2(path[-1]) else list(reversed(path))


def _joint_record(joint_id: str, xy, visibility: str, initialization: str):
    part, side = JOINT_META[joint_id]
    return {
        "track_id": joint_id,
        "part": part,
        "anatomical_side": side,
        "source_xy": [round(float(xy[0]), 6), round(float(xy[1]), 6)],
        "visibility": visibility,
        "occlusion": visibility != "visible",
        "confidence": 0.5 if visibility == "visible" else 0.0,
        "review_state": "proposed",
        "initialization": initialization,
    }


def propose_frame_joints(frame_rgba, frame_index: int):
    """Propose persistent named joints from one colour-keyed source frame."""
    masks = part_masks(frame_rgba)
    joints = {}
    torso_mask = masks.get("torso")
    if torso_mask is None:
        raise ValueError(f"frame {frame_index}: torso colour is absent")

    torso_path = longest_skeleton_path(torso_mask)
    if not torso_path:
        raise ValueError(f"frame {frame_index}: torso skeleton is empty")
    if torso_path[0][1] > torso_path[-1][1]:
        torso_path.reverse()
    joints["neck"] = _joint_record("neck", point_on_path(torso_path, 0.0),
                                     "visible", "torso_geodesic_top")
    joints["spine"] = _joint_record("spine", point_on_path(torso_path, 0.52),
                                      "visible", "torso_geodesic_mid")
    joints["pelvis"] = _joint_record("pelvis", point_on_path(torso_path, 1.0),
                                       "visible", "torso_geodesic_bottom")

    head_mask = masks.get("head")
    head_xy = _mask_centroid(head_mask) if head_mask is not None else None
    if head_xy is not None:
        joints["head"] = _joint_record("head", head_xy, "visible", "head_mask_centroid")

    limb_specs = [
        ("arm_a", "shoulder_a", "elbow_a", "wrist_a"),
        ("arm_b", "shoulder_b", "elbow_b", "wrist_b"),
        ("leg_a", "hip_a", "knee_a", "ankle_a"),
        ("leg_b", "hip_b", "knee_b", "ankle_b"),
    ]
    for part, proximal_id, middle_id, distal_id in limb_specs:
        mask = masks.get(part)
        if mask is None:
            continue
        path = _path_proximal_first(longest_skeleton_path(mask), torso_mask)
        if not path:
            continue
        joints[proximal_id] = _joint_record(
            proximal_id, point_on_path(path, 0.0), "visible", f"{part}_geodesic_proximal")
        joints[middle_id] = _joint_record(
            middle_id, point_on_path(path, 0.5), "visible", f"{part}_geodesic_mid")
        joints[distal_id] = _joint_record(
            distal_id, point_on_path(path, 1.0), "visible", f"{part}_geodesic_distal")

    return {
        "frame_index": frame_index,
        "source_frame_sha256": hashlib.sha256(frame_rgba.tobytes()).hexdigest(),
        "joints": joints,
        "topology_events": [],
    }


def fill_occluded_joint_tracks(records: list[dict]) -> None:
    """Retain identity through fully hidden colour parts using cyclic interpolation."""
    n = len(records)
    for joint_id in JOINT_META:
        available = [i for i, rec in enumerate(records) if joint_id in rec["joints"]]
        if not available:
            raise ValueError(f"joint track {joint_id} has no visible source frame")
        for fi, rec in enumerate(records):
            if joint_id in rec["joints"]:
                continue
            prev = next((fi - step) % n for step in range(1, n + 1)
                        if (fi - step) % n in available)
            nxt = next((fi + step) % n for step in range(1, n + 1)
                       if (fi + step) % n in available)
            dp, dn = (fi - prev) % n, (nxt - fi) % n
            p0 = records[prev]["joints"][joint_id]["source_xy"]
            p1 = records[nxt]["joints"][joint_id]["source_xy"]
            denom = dp + dn
            t = (dp / denom) if denom else 0.0
            xy = (p0[0] + (p1[0] - p0[0]) * t,
                  p0[1] + (p1[1] - p0[1]) * t)
            rec["joints"][joint_id] = _joint_record(
                joint_id, xy, "occluded", "cyclic_track_interpolation")


# FL-4561: visible raster proposals are observations, not temporal truth. The
# cyclic solve below is the sole owner of stabilized joint coordinates consumed
# by projection and glyph compilation.
STABILIZATION_LAMBDA = {
    "head": 32.0,
    "neck": 24.0,
    "spine": 24.0,
    "pelvis": 0.0,
    "shoulder_a": 5.0,
    "shoulder_b": 5.0,
    "elbow_a": 2.0,
    "elbow_b": 2.0,
    "wrist_a": 1.0,
    "wrist_b": 1.0,
    "hip_a": 5.0,
    "hip_b": 5.0,
    "knee_a": 2.0,
    "knee_b": 2.0,
    "ankle_a": 1.0,
    "ankle_b": 1.0,
}


def _cyclic_second_difference_matrix(n: int):
    import numpy as np

    matrix = np.zeros((n, n), dtype=float)
    for i in range(n):
        matrix[i, (i - 1) % n] = 1.0
        matrix[i, i] = -2.0
        matrix[i, (i + 1) % n] = 1.0
    return matrix


def _cyclic_smooth(points, weights, strength: float):
    """Weighted closed-loop least squares with a cyclic acceleration penalty."""
    import numpy as np

    points = np.asarray(points, dtype=float)
    weights = np.asarray(weights, dtype=float)
    if strength <= 0.0:
        return points.copy()
    d2 = _cyclic_second_difference_matrix(len(points))
    lhs = np.diag(weights) + strength * (d2.T @ d2)
    rhs = weights[:, None] * points
    return np.linalg.solve(lhs, rhs)


def _unit(vector, fallback=(0.0, -1.0)):
    import numpy as np

    vector = np.asarray(vector, dtype=float)
    length = float(np.linalg.norm(vector))
    if length <= 1e-9:
        return np.asarray(fallback, dtype=float)
    return vector / length


def stabilize_joint_tracks(records: list[dict]) -> None:
    """Stabilize one 18-frame direction in pelvis-relative anatomical space.

    Head, neck, and spine receive the strongest cyclic regularization. Their
    three anatomical segment lengths are fixed to cycle medians after smoothing,
    which removes raster-centroid breathing without flattening torso direction.
    Every raw observation remains serialized beside the stabilized coordinate.
    """
    import numpy as np

    n = len(records)
    raw = {
        joint_id: np.asarray(
            [record["joints"][joint_id]["source_xy"] for record in records],
            dtype=float,
        )
        for joint_id in JOINT_META
    }
    root = raw["pelvis"]
    relative = {joint_id: points - root for joint_id, points in raw.items()}
    stable = {}
    for joint_id in JOINT_META:
        weights = [
            1.0 if record["joints"][joint_id]["visibility"] == "visible" else 0.15
            for record in records
        ]
        stable[joint_id] = _cyclic_smooth(
            relative[joint_id], weights, STABILIZATION_LAMBDA[joint_id]
        )

    # Rebuild the midline as a fixed-length articulated chain. Orientation may
    # move smoothly; anatomical length cannot pulse with raster skeleton noise.
    median_lengths = {}
    for child, parent in (("spine", "pelvis"), ("neck", "spine"), ("head", "neck")):
        lengths = np.linalg.norm(relative[child] - relative[parent], axis=1)
        median_lengths[f"{parent}_to_{child}"] = float(np.median(lengths))
    for fi in range(n):
        spine_dir = _unit(stable["spine"][fi])
        stable["spine"][fi] = spine_dir * median_lengths["pelvis_to_spine"]
        neck_dir = _unit(stable["neck"][fi] - stable["spine"][fi], spine_dir)
        stable["neck"][fi] = (
            stable["spine"][fi] + neck_dir * median_lengths["spine_to_neck"]
        )
        head_dir = _unit(stable["head"][fi] - stable["neck"][fi], neck_dir)
        stable["head"][fi] = (
            stable["neck"][fi] + head_dir * median_lengths["neck_to_head"]
        )

    for fi, record in enumerate(records):
        record["stabilization"] = {
            "schema": "fl4561.cyclic_joint_stabilization.v1",
            "cycle_length": n,
            "midline_fixed_lengths": {
                key: round(value, 6) for key, value in median_lengths.items()
            },
        }
        for joint_id, joint in record["joints"].items():
            stabilized = stable[joint_id][fi] + root[fi]
            joint["raw_source_xy"] = [round(float(value), 6) for value in raw[joint_id][fi]]
            joint["stabilized_source_xy"] = [
                round(float(value), 6) for value in stabilized
            ]
            joint["source_xy"] = list(joint["stabilized_source_xy"])
            joint["stabilization_displacement"] = round(
                float(np.linalg.norm(stabilized - raw[joint_id][fi])), 6
            )
            joint["stabilization_strength"] = STABILIZATION_LAMBDA[joint_id]


def load_rig_proposals():
    """Decode all 144 frames and return stabilized joint tracks plus source hashes."""
    records_by_dir = {}
    source_hashes = {}
    for direction in DISPLAY_DIRS:
        src = SOURCE_ROOT / direction / f"{direction}.aseprite"
        if not src.exists():
            raise SystemExit(f"missing rig source: {src}")
        frames = decode_aseprite(src)
        if len(frames) != 18:
            raise ValueError(f"{direction}: expected 18 frames, found {len(frames)}")
        records = [propose_frame_joints(frame, fi) for fi, frame in enumerate(frames)]
        fill_occluded_joint_tracks(records)
        stabilize_joint_tracks(records)
        records_by_dir[direction] = records
        source_hashes[direction] = hashlib.sha256(src.read_bytes()).hexdigest()
    return records_by_dir, source_hashes


def compute_projection_scales(records_by_dir: dict[str, list[dict]]) -> dict[str, float]:
    """Freeze anatomy-aware coefficients that fit the upper and lower row budgets.

    A single Euclidean scale cannot fit this source: its maximum pelvis-to-head
    extent is shorter than its maximum pelvis-to-foot extent, while the target
    reserves three rows above the pelvis and two below it. These three
    coefficients remain constant across every direction and frame.
    """
    max_up = max_down = max_side = 0.0
    for records in records_by_dir.values():
        for rec in records:
            root = rec["joints"]["pelvis"]["source_xy"]
            for joint in rec["joints"].values():
                x, y = joint["source_xy"]
                max_up = max(max_up, root[1] - y)
                max_down = max(max_down, y - root[1])
                max_side = max(max_side, abs(x - root[0]))
    scales = {
        "x": 2.20 / max(max_side, 1e-9),
        "y_above_pelvis": 2.75 / max(max_up, 1e-9),
        "y_below_pelvis": 1.70 / max(max_down, 1e-9),
    }
    if any(not math.isfinite(value) or value <= 0.0 for value in scales.values()):
        raise ValueError(f"invalid projection scales: {scales}")
    return scales


def segment_cells(a, b) -> list[list[int]]:
    """Return ordered grid cells touched by a segment for diagnostic occupancy."""
    length = math.dist(a, b)
    steps = max(2, int(math.ceil(length * 256.0)))
    cells = []
    seen = set()
    for i in range(steps + 1):
        t = i / steps
        x = a[0] + (b[0] - a[0]) * t
        y = a[1] + (b[1] - a[1]) * t
        cell = (int(math.floor(x)), int(math.floor(y)))
        if 0 <= cell[0] < GRID_W and 0 <= cell[1] < GRID_H and cell not in seen:
            seen.add(cell)
            cells.append([cell[0], cell[1]])
    return cells


def semantic_placement_cell(bone_id: str, part: str, direction: str, a, b) -> list[int]:
    """Choose the single glyph cell owned by one semantic body segment."""
    midpoint = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)
    column = min(GRID_W - 1, max(0, int(math.floor(midpoint[0]))))
    if bone_id == "head_axis":
        return [column, 0]
    if bone_id == "torso_upper":
        return [2, 1]
    if bone_id == "torso_lower":
        return [2, 2]
    # Front/back limbs occupy side columns when their projected midpoint is
    # already on one side of the spine. Diagonal views use the same rule only
    # when the offset is material. Side views retain the source collapse.
    dx = midpoint[0] - ROOT_TARGET[0]
    if part.startswith(("arm_", "leg_")) and column == 2:
        push_side = direction in ("N", "S") or (direction in ("NE", "NW", "SE", "SW")
                                                   and abs(dx) >= 0.2)
        if push_side and abs(dx) > 1e-9:
            column = 1 if dx < 0.0 else 3
    row = min(GRID_H - 1, max(0, int(math.floor(midpoint[1]))))
    # The lower-leg segment owns the foot row. A lifted foot therefore moves
    # the shin placement into row 3 without a hard-coded foreshortening flag.
    if bone_id.startswith("shin_"):
        row = min(GRID_H - 1, max(0, int(math.floor(b[1]))))
    return [column, row]


def project_rig_record(record: dict, direction: str, scales: dict[str, float]) -> dict:
    """Project one proposal with fixed anatomy-aware coefficients and pelvis target."""
    root = record["joints"]["pelvis"]["source_xy"]
    raw_root = record["joints"]["pelvis"].get("raw_source_xy", root)
    joints = {}
    clipped = []
    for joint_id in JOINT_META:
        src = record["joints"][joint_id]
        dx = src["source_xy"][0] - root[0]
        dy = src["source_xy"][1] - root[1]
        y_scale = scales["y_above_pelvis"] if dy < 0.0 else scales["y_below_pelvis"]
        x = ROOT_TARGET[0] + dx * scales["x"]
        y = ROOT_TARGET[1] + dy * y_scale
        raw_xy = src.get("raw_source_xy", src["source_xy"])
        raw_dx = raw_xy[0] - raw_root[0]
        raw_dy = raw_xy[1] - raw_root[1]
        raw_y_scale = scales["y_above_pelvis"] if raw_dy < 0.0 else scales["y_below_pelvis"]
        raw_grid_xy = [
            round(ROOT_TARGET[0] + raw_dx * scales["x"], 6),
            round(ROOT_TARGET[1] + raw_dy * raw_y_scale, 6),
        ]
        cell = [int(math.floor(x)), int(math.floor(y))] if 0 <= x < 5 and 0 <= y < 5 else None
        if cell is None:
            clipped.append(joint_id)
        joints[joint_id] = dict(
            src,
            raw_grid_xy=raw_grid_xy,
            grid_xy=[round(x, 6), round(y, 6)],
            cell=cell,
        )

    bones = {}
    for bone_id, ja, jb, part in BONE_SPECS:
        a, b = joints[ja]["grid_xy"], joints[jb]["grid_xy"]
        bones[bone_id] = {
            "joint_a": ja,
            "joint_b": jb,
            "part": part,
            "placement_cell": semantic_placement_cell(bone_id, part, direction, a, b),
            "intersected_cells": segment_cells(a, b),
        }
    attachments = {}
    for bone_id, ja, jb, part in ATTACHMENT_SPECS:
        a, b = joints[ja]["grid_xy"], joints[jb]["grid_xy"]
        attachments[bone_id] = {
            "joint_a": ja,
            "joint_b": jb,
            "part": part,
            "cells": segment_cells(a, b),
        }

    occupancy_rows = {}
    for part in ("head", "torso", "arm_a", "arm_b", "leg_a", "leg_b"):
        rows = {bone["placement_cell"][1] for bone in bones.values() if bone["part"] == part}
        occupancy_rows[part] = sorted(rows)

    placement_owners = {}
    for bone_id, bone in bones.items():
        key = f"{bone['placement_cell'][0]},{bone['placement_cell'][1]}"
        placement_owners.setdefault(key, []).append(bone_id)

    shoulder_delta = joints["shoulder_a"]["grid_xy"][1] - joints["shoulder_b"]["grid_xy"][1]
    hip_delta = joints["hip_a"]["grid_xy"][1] - joints["hip_b"]["grid_xy"][1]
    ankle_delta = joints["ankle_a"]["grid_xy"][1] - joints["ankle_b"]["grid_xy"][1]
    pelvis_error = math.dist(joints["pelvis"]["grid_xy"], ROOT_TARGET)
    return {
        "direction": direction,
        "frame_index": record["frame_index"],
        "source_frame_sha256": record["source_frame_sha256"],
        "review_state": "proposed",
        "joints": joints,
        "bones": bones,
        "attachments": attachments,
        "occupancy_rows": occupancy_rows,
        "placement_owners": placement_owners,
        "projected_offsets": {
            "shoulder_a_minus_b_y": round(shoulder_delta, 6),
            "hip_a_minus_b_y": round(hip_delta, 6),
            "ankle_a_minus_b_y": round(ankle_delta, 6),
        },
        "pelvis_anchor_error": round(pelvis_error, 12),
        "clipped_joints": clipped,
        "topology_events": record["topology_events"],
        "stabilization": record.get("stabilization"),
    }


def _cyclic_track_metrics(points) -> dict[str, float]:
    import numpy as np

    points = np.asarray(points, dtype=float)
    velocity = np.roll(points, -1, axis=0) - points
    acceleration = np.roll(points, -1, axis=0) - 2.0 * points + np.roll(points, 1, axis=0)
    return {
        "velocity_rms": round(float(np.sqrt(np.mean(np.sum(velocity * velocity, axis=1)))), 9),
        "acceleration_rms": round(
            float(np.sqrt(np.mean(np.sum(acceleration * acceleration, axis=1)))), 9
        ),
        "loop_jump": round(float(np.linalg.norm(points[0] - points[-1])), 9),
    }


def summarize_rig(records_by_dir: dict[str, list[dict]], scales: dict[str, float]) -> dict:
    records = [rec for direction in DISPLAY_DIRS for rec in records_by_dir[direction]]
    occupancy_hist = {}
    for part in ("head", "torso", "arm_a", "arm_b", "leg_a", "leg_b"):
        hist = {}
        for rec in records:
            count = len(rec["occupancy_rows"][part])
            hist[str(count)] = hist.get(str(count), 0) + 1
        occupancy_hist[part] = hist
    occupied_cell_hist = {}
    for rec in records:
        count = len(rec["placement_owners"])
        occupied_cell_hist[str(count)] = occupied_cell_hist.get(str(count), 0) + 1

    diagonal = [rec for direction in ("SW", "NW", "NE", "SE")
                for rec in records_by_dir[direction]]
    nonzero_shoulders = sum(abs(rec["projected_offsets"]["shoulder_a_minus_b_y"]) > 1e-6
                            for rec in diagonal)
    nonzero_hips = sum(abs(rec["projected_offsets"]["hip_a_minus_b_y"]) > 1e-6
                       for rec in diagonal)
    stabilization = {}
    for joint_id in ("head", "neck", "spine"):
        raw_metrics = []
        stable_metrics = []
        displacements = []
        for direction in DISPLAY_DIRS:
            direction_records = records_by_dir[direction]
            raw_metrics.append(_cyclic_track_metrics([
                rec["joints"][joint_id]["raw_grid_xy"] for rec in direction_records
            ]))
            stable_metrics.append(_cyclic_track_metrics([
                rec["joints"][joint_id]["grid_xy"] for rec in direction_records
            ]))
            displacements.extend(
                rec["joints"][joint_id].get("stabilization_displacement", 0.0)
                for rec in direction_records
            )
        stabilization[joint_id] = {
            "raw_acceleration_rms_mean": round(
                sum(item["acceleration_rms"] for item in raw_metrics) / len(raw_metrics), 9
            ),
            "stabilized_acceleration_rms_mean": round(
                sum(item["acceleration_rms"] for item in stable_metrics) / len(stable_metrics), 9
            ),
            "raw_loop_jump_mean": round(
                sum(item["loop_jump"] for item in raw_metrics) / len(raw_metrics), 9
            ),
            "stabilized_loop_jump_mean": round(
                sum(item["loop_jump"] for item in stable_metrics) / len(stable_metrics), 9
            ),
            "source_displacement_rms": round(
                math.sqrt(sum(value * value for value in displacements) / len(displacements)), 9
            ),
            "source_displacement_max": round(max(displacements), 9),
        }
    return {
        "source_frame_count": len(records),
        "required_joint_count_per_frame": len(JOINT_META),
        "joint_record_count": len(records) * len(JOINT_META),
        "occluded_interpolated_joint_count": sum(
            joint["visibility"] == "occluded" for rec in records for joint in rec["joints"].values()),
        "fixed_projection_scales": {key: round(value, 12) for key, value in scales.items()},
        "frame_dependent_scale_count": 0,
        "pelvis_anchor_max_error": max(rec["pelvis_anchor_error"] for rec in records),
        "clipped_joint_count": sum(len(rec["clipped_joints"]) for rec in records),
        "diagonal_frame_count": len(diagonal),
        "diagonal_nonzero_shoulder_offset_frames": nonzero_shoulders,
        "diagonal_nonzero_hip_offset_frames": nonzero_hips,
        "occupancy_row_count_histogram": occupancy_hist,
        "placement_cell_collision_count": sum(
            max(0, len(owners) - 1)
            for rec in records for owners in rec["placement_owners"].values()),
        "occupied_cell_count_histogram": occupied_cell_hist,
        "stabilization": stabilization,
        "anatomical_side_review": "open; colour tracks a/b are persistent proposal identities",
        "operator_visual_signoff": "open",
    }


def render_rig_panel(record: dict, cell_px: int = 28):
    """Render named bones and joints directly in the fixed 5x5 coordinate frame."""
    from PIL import Image, ImageDraw

    size = GRID_W * cell_px + 1
    panel = Image.new("RGB", (size, size), (10, 10, 12))
    draw = ImageDraw.Draw(panel)
    for i in range(GRID_W + 1):
        x = i * cell_px
        draw.line((x, 0, x, size), fill=(38, 38, 43), width=1)
    for i in range(GRID_H + 1):
        y = i * cell_px
        draw.line((0, y, size, y), fill=(38, 38, 43), width=1)
    axis_x = int(round(ROOT_TARGET[0] * cell_px))
    draw.line((axis_x, 0, axis_x, size), fill=(45, 70, 55), width=1)

    def pixel(xy):
        return (int(round(xy[0] * cell_px)), int(round(xy[1] * cell_px)))

    for attachment in record["attachments"].values():
        colour = PART_RGB_BY_NAME[attachment["part"]]
        muted = tuple(max(35, channel // 2) for channel in colour)
        draw.line((pixel(record["joints"][attachment["joint_a"]]["grid_xy"]),
                   pixel(record["joints"][attachment["joint_b"]]["grid_xy"])),
                  fill=muted, width=2)
    for bone in record["bones"].values():
        colour = PART_RGB_BY_NAME[bone["part"]]
        draw.line((pixel(record["joints"][bone["joint_a"]]["grid_xy"]),
                   pixel(record["joints"][bone["joint_b"]]["grid_xy"])),
                  fill=colour, width=4)
        cell_x, cell_y = bone["placement_cell"]
        mark_x = int((cell_x + 0.5) * cell_px)
        mark_y = int((cell_y + 0.5) * cell_px)
        draw.rectangle((mark_x - 2, mark_y - 2, mark_x + 2, mark_y + 2),
                       outline=colour, width=1)
    for joint_id, joint in record["joints"].items():
        x, y = pixel(joint["grid_xy"])
        colour = PART_RGB_BY_NAME[joint["part"]]
        radius = 3 if joint["visibility"] == "visible" else 2
        draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                     fill=colour, outline=(230, 230, 230))
        if joint_id == "pelvis":
            draw.rectangle((x - 4, y - 4, x + 4, y + 4), outline=(255, 255, 255), width=1)
    return panel


def cmd_rig5x5(args: argparse.Namespace) -> int:
    """Generate experiment slice 1: proposed named rig in the fixed 5x5 frame."""
    from PIL import Image, ImageDraw, ImageFont

    proposals, source_hashes = load_rig_proposals()
    scales = compute_projection_scales(proposals)
    projected = {
        direction: [project_rig_record(rec, direction, scales) for rec in proposals[direction]]
        for direction in DISPLAY_DIRS
    }
    summary = summarize_rig(projected, scales)
    if summary["pelvis_anchor_max_error"] != 0.0:
        raise ValueError(f"pelvis anchor drifted: {summary['pelvis_anchor_max_error']}")
    if summary["clipped_joint_count"] != 0:
        raise ValueError(f"projection clipped {summary['clipped_joint_count']} named joints")

    cell_px, label_h, pad = args.cell_px, 16, 6
    sample_panel = render_rig_panel(projected[DISPLAY_DIRS[0]][0], cell_px)
    panel_w, panel_h = sample_panel.size
    canvas_w = pad + GRID_COLS * (panel_w + pad)
    canvas_h = pad + GRID_ROWS * (panel_h + label_h + pad)
    label_font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 12)
    gif_frames = []
    for fi in range(18):
        canvas = Image.new("RGB", (canvas_w, canvas_h), (8, 8, 8))
        draw = ImageDraw.Draw(canvas)
        for idx, direction in enumerate(DISPLAY_DIRS):
            gc, gr = idx % GRID_COLS, idx // GRID_COLS
            x = pad + gc * (panel_w + pad)
            y = pad + gr * (panel_h + label_h + pad)
            draw.text((x, y), f"{direction} f{fi}", fill=(150, 210, 150), font=label_font)
            canvas.paste(render_rig_panel(projected[direction][fi], cell_px), (x, y + label_h))
        gif_frames.append(canvas)

    timestamp = datetime.now().strftime("v%Y%m%dT%H%M%S")
    name = f"{timestamp}__rig-bones-5x5-stabilized__walk_all_directions"
    TRACK_DIR.mkdir(parents=True, exist_ok=True)
    tracked_gif = TRACK_DIR / f"{name}.gif"
    desktop_gif = DESKTOP / f"{name}.gif"
    tracked_manifest = TRACK_DIR / f"{name}.frames.json"
    gif_frames[0].save(tracked_gif, save_all=True, append_images=gif_frames[1:],
                       duration=args.frame_ms, loop=0, disposal=2)
    desktop_gif.write_bytes(tracked_gif.read_bytes())

    manifest = {
        "schema": RIG_SCHEMA,
        "status": "stabilized_rig_proposed_review_open",
        "primary_owner": "FL-4561",
        "lineage": ["FL-4512", "FL-4547"],
        "name": name,
        "timestamp": timestamp,
        "source_root": str(SOURCE_ROOT),
        "source_hashes": source_hashes,
        "directions": DISPLAY_DIRS,
        "frames_per_direction": 18,
        "grid": [GRID_W, GRID_H],
        "vertical_budget": {
            "0": "head",
            "1": "shoulders_upper_torso_upper_arm",
            "2": "chest_waist_forearm_hand",
            "3": "pelvis_hips_thighs",
            "4": "shins_feet",
            "occupancy_policy": "one placement cell per semantic segment; coincident cells remain legal",
        },
        "transform": {
            "type": "cyclic_stabilized_joint_tracks_plus_fixed_anatomy_projection",
            "fixed_scales": {key: round(value, 12) for key, value in scales.items()},
            "root_target": list(ROOT_TARGET),
            "frame_dependent_scale": False,
            "ink_centroid_recentering": False,
            "spine_policy": "pelvis pinned to center column; torso tilt retained",
        },
        "joint_contract": [
            {"track_id": joint_id, "part": part, "anatomical_side": side,
             "review_state": "proposed"}
            for joint_id, (part, side) in JOINT_META.items()
        ],
        "bone_contract": [
            {"bone_id": bone_id, "joint_a": ja, "joint_b": jb, "part": part}
            for bone_id, ja, jb, part in BONE_SPECS
        ],
        "summary": summary,
        "gif_sha256": hashlib.sha256(tracked_gif.read_bytes()).hexdigest(),
        "frames": projected,
    }
    tracked_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    manifest_sha256 = hashlib.sha256(tracked_manifest.read_bytes()).hexdigest()

    registry = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else []
    registry.append({
        "name": name,
        "kind": "rig-bones-5x5-stabilized",
        "schema": RIG_SCHEMA,
        "status": "proposed_review_open",
        "timestamp": timestamp,
        "grid": [GRID_W, GRID_H],
        "frames_per_dir": 18,
        "root_target": list(ROOT_TARGET),
        "fixed_scales": {key: round(value, 12) for key, value in scales.items()},
        "spine_centered": True,
        "mirror_locked": False,
        "ink_centroid_recentering": False,
        "note": args.note,
        "tracked_gif": str(tracked_gif.relative_to(REPO)),
        "tracked_manifest": str(tracked_manifest.relative_to(REPO)),
        "desktop_gif": str(desktop_gif),
        "gif_sha256": manifest["gif_sha256"],
        "manifest_sha256": manifest_sha256,
    })
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=1) + "\n")

    print(f"decoded 8 directions x 18 frames; proposed {summary['joint_record_count']} named joints")
    print(f"fixed scales: {scales}; pelvis max error: {summary['pelvis_anchor_max_error']}")
    print(f"\nwrote FL-4561 stabilized rig @ {timestamp}")
    print(f"  desktop : {desktop_gif}")
    print(f"  tracked : {tracked_gif.relative_to(REPO)}")
    print(f"  manifest: {tracked_manifest.relative_to(REPO)}")
    return 0


# =============================================================================
# FL-4561 STABILIZED UNICODE COMPILATION
# =============================================================================

def _image_to_mask8(image) -> int:
    value = 0
    pixels = image.load()
    for y in range(8):
        for x in range(8):
            if pixels[x, y] > 0:
                value |= 1 << (8 * y + x)
    return value


def _mask8_topology(mask: int) -> tuple[int, int]:
    """Use the canonical FL-4482 topology owner for target measurements."""
    import sys

    scripts_dir = str(REPO / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from fl4482_coverage_census import topology

    return topology(mask)


def render_record_target_masks(record: dict) -> list[int]:
    """Rasterize each semantic segment only into its owned 5x5 cell."""
    from PIL import Image, ImageChops, ImageDraw

    scale = 8
    masks = [Image.new("L", (8, 8), 0) for _ in range(GRID_W * GRID_H)]

    def segment_crop(a, b):
        global_image = Image.new("L", (GRID_W * scale, GRID_H * scale), 0)
        draw = ImageDraw.Draw(global_image)
        draw.line(
            (
                int(round(a[0] * scale)),
                int(round(a[1] * scale)),
                int(round(b[0] * scale)),
                int(round(b[1] * scale)),
            ),
            fill=255,
            width=1,
        )
        return global_image

    for bone_id, bone in record["bones"].items():
        a = record["joints"][bone["joint_a"]]["grid_xy"]
        b = record["joints"][bone["joint_b"]]["grid_xy"]
        rendered = segment_crop(a, b)
        cell_x, cell_y = bone["placement_cell"]
        crop = rendered.crop(
            (cell_x * scale, cell_y * scale, (cell_x + 1) * scale, (cell_y + 1) * scale)
        )
        index = cell_y * GRID_W + cell_x
        masks[index] = ImageChops.lighter(masks[index], crop)
        if bone_id == "head_axis":
            head = record["joints"]["head"]["grid_xy"]
            hx = min(7, max(0, int(round((head[0] - cell_x) * scale))))
            hy = min(7, max(0, int(round((head[1] - cell_y) * scale))))
            ImageDraw.Draw(masks[index]).ellipse(
                (max(0, hx - 1), max(0, hy - 1), min(7, hx + 1), min(7, hy + 1)),
                fill=255,
            )

    occupied = {key for key in record["placement_owners"]}
    for attachment in record["attachments"].values():
        a = record["joints"][attachment["joint_a"]]["grid_xy"]
        b = record["joints"][attachment["joint_b"]]["grid_xy"]
        rendered = segment_crop(a, b)
        for cell_x, cell_y in attachment["cells"]:
            if f"{cell_x},{cell_y}" not in occupied:
                continue
            crop = rendered.crop(
                (cell_x * scale, cell_y * scale, (cell_x + 1) * scale, (cell_y + 1) * scale)
            )
            index = cell_y * GRID_W + cell_x
            masks[index] = ImageChops.lighter(masks[index], crop)
    return [_image_to_mask8(image) for image in masks]


def load_descriptor_catalog() -> dict:
    """Load the canonical compiler-admitted full-Unicode descriptor supply."""
    import numpy as np

    emoji = load_emoji_codepoints()
    rows = []
    with DESCRIPTOR_CENSUS.open() as handle:
        for line in handle:
            row = json.loads(line)
            scalar = int(row["unicode_scalar"])
            if scalar in emoji:
                continue
            rows.append(row)
    rows.sort(key=lambda row: (int(row["unicode_scalar"]), int(row["glyph_id"])))
    masks = np.asarray([int(row["mask8_u64"], 16) for row in rows], dtype=np.uint64)
    scalars = np.asarray([int(row["unicode_scalar"]) for row in rows], dtype=np.int64)
    glyph_ids = np.asarray([int(row["glyph_id"]) for row in rows], dtype=np.int64)
    endpoints = np.asarray([int(row["endpoint_count"]) for row in rows], dtype=np.int16)
    junctions = np.asarray([int(row["junction_cluster_count"]) for row in rows], dtype=np.int16)
    space_matches = np.where(scalars == 0x20)[0]
    if len(space_matches) != 1:
        raise ValueError(f"canonical descriptor supply has {len(space_matches)} spaces")
    return {
        "rows": rows,
        "masks": masks,
        "scalars": scalars,
        "glyph_ids": glyph_ids,
        "endpoints": endpoints,
        "junctions": junctions,
        "space_index": int(space_matches[0]),
        "descriptor_sha256": hashlib.sha256(DESCRIPTOR_CENSUS.read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(DESCRIPTOR_MANIFEST.read_bytes()).hexdigest(),
    }


def glyph_candidates(target_mask: int, catalog: dict, top_k: int) -> list[dict]:
    import numpy as np

    if target_mask == 0:
        index = catalog["space_index"]
        return [{"index": index, "base_cost": 0.0, "cost": 0.0}]
    target_endpoints, target_junctions = _mask8_topology(target_mask)
    xor = np.bitwise_xor(catalog["masks"], np.uint64(target_mask))
    hamming = np.bitwise_count(xor).astype(float)
    score = hamming / 64.0
    score += 0.055 * np.minimum(6, np.abs(catalog["endpoints"] - target_endpoints))
    score += 0.075 * np.minimum(4, np.abs(catalog["junctions"] - target_junctions))
    score += np.where(catalog["masks"] == 0, 0.75, 0.0)
    probe_count = min(len(score), max(top_k * 12, 96))
    probe = np.argpartition(score, probe_count - 1)[:probe_count]
    ordered = sorted(
        (int(index) for index in probe),
        key=lambda index: (
            float(score[index]),
            int(catalog["scalars"][index]),
            int(catalog["glyph_ids"][index]),
        ),
    )
    candidates = []
    seen_masks = set()
    for index in ordered:
        mask = int(catalog["masks"][index])
        if mask in seen_masks:
            continue
        seen_masks.add(mask)
        cost = float(score[index])
        candidates.append({"index": index, "base_cost": cost, "cost": cost})
        if len(candidates) == top_k:
            break
    return candidates


def _mask_port(mask: int, side: str) -> bool:
    if side == "left":
        return any(mask & (1 << (8 * y)) for y in range(8))
    if side == "right":
        return any(mask & (1 << (8 * y + 7)) for y in range(8))
    if side == "top":
        return bool(mask & 0xFF)
    if side == "bottom":
        return bool(mask & (0xFF << 56))
    raise ValueError(side)


def solve_cyclic_glyph_track(
    candidate_frames: list[list[dict]], target_masks: list[int], catalog: dict, lambda_temporal: float
) -> list[int]:
    """Solve one screen-cell track across the closed eighteen-frame cycle."""
    best_total = float("inf")
    best_path = None
    first_candidates = candidate_frames[0]
    for start_state in range(len(first_candidates)):
        costs = {start_state: first_candidates[start_state]["cost"]}
        paths = {start_state: [start_state]}
        for frame_index in range(1, len(candidate_frames)):
            next_costs = {}
            next_paths = {}
            target_motion = (
                (target_masks[frame_index - 1] ^ target_masks[frame_index]).bit_count() / 64.0
            )
            stability = max(0.08, 1.0 - target_motion)
            for state, candidate in enumerate(candidate_frames[frame_index]):
                candidate_mask = int(catalog["masks"][candidate["index"]])
                chosen = None
                for prev_state, prev_cost in costs.items():
                    previous = candidate_frames[frame_index - 1][prev_state]
                    previous_mask = int(catalog["masks"][previous["index"]])
                    transition = (
                        lambda_temporal
                        * stability
                        * ((previous_mask ^ candidate_mask).bit_count() / 64.0)
                    )
                    if previous["index"] != candidate["index"]:
                        transition += 0.012 * stability
                    total = prev_cost + candidate["cost"] + transition
                    if chosen is None or total < chosen[0]:
                        chosen = (total, prev_state)
                next_costs[state] = chosen[0]
                next_paths[state] = paths[chosen[1]] + [state]
            costs, paths = next_costs, next_paths
        for state, path_cost in costs.items():
            previous = candidate_frames[-1][state]
            first = first_candidates[start_state]
            loop_motion = (target_masks[-1] ^ target_masks[0]).bit_count() / 64.0
            stability = max(0.08, 1.0 - loop_motion)
            loop_cost = (
                lambda_temporal
                * stability
                * (
                    (
                        int(catalog["masks"][previous["index"]])
                        ^ int(catalog["masks"][first["index"]])
                    ).bit_count()
                    / 64.0
                )
            )
            total = path_cost + loop_cost
            if total < best_total:
                best_total = total
                best_path = paths[state]
    return [candidate_frames[fi][state]["index"] for fi, state in enumerate(best_path)]


def compile_unicode_direction(records: list[dict], catalog: dict, top_k: int, lambda_temporal: float):
    targets = [render_record_target_masks(record) for record in records]
    cache = {}
    candidates = [[None for _ in range(GRID_W * GRID_H)] for _ in records]
    for fi in range(len(records)):
        for cell in range(GRID_W * GRID_H):
            target = targets[fi][cell]
            if target not in cache:
                cache[target] = glyph_candidates(target, catalog, top_k)
            candidates[fi][cell] = [dict(candidate) for candidate in cache[target]]

    selected = [[catalog["space_index"] for _ in range(GRID_W * GRID_H)] for _ in records]
    for _round in range(3):
        for cell in range(GRID_W * GRID_H):
            track = solve_cyclic_glyph_track(
                [frame[cell] for frame in candidates],
                [frame[cell] for frame in targets],
                catalog,
                lambda_temporal,
            )
            for fi, index in enumerate(track):
                selected[fi][cell] = index

        # Spatial port consistency is an observation cost. The temporal solver
        # remains the single final owner after each deterministic update.
        neighbors = [(-1, 0, "left", "right"), (1, 0, "right", "left"),
                     (0, -1, "top", "bottom"), (0, 1, "bottom", "top")]
        for fi in range(len(records)):
            for cell in range(GRID_W * GRID_H):
                x, y = cell % GRID_W, cell // GRID_W
                for candidate in candidates[fi][cell]:
                    penalty = 0.0
                    candidate_mask = int(catalog["masks"][candidate["index"]])
                    for dx, dy, side, opposite in neighbors:
                        nx, ny = x + dx, y + dy
                        if not (0 <= nx < GRID_W and 0 <= ny < GRID_H):
                            continue
                        neighbor_cell = ny * GRID_W + nx
                        desired = _mask_port(targets[fi][cell], side) and _mask_port(
                            targets[fi][neighbor_cell], opposite
                        )
                        neighbor_mask = int(catalog["masks"][selected[fi][neighbor_cell]])
                        actual = _mask_port(candidate_mask, side) and _mask_port(
                            neighbor_mask, opposite
                        )
                        if desired != actual:
                            penalty += 0.28
                    candidate["cost"] = candidate["base_cost"] + penalty
    return targets, selected, candidates


def fit_view_counterpart_relations(records_by_dir: dict[str, list[dict]]) -> dict:
    """Fit 2D counterpart transports; no inverse of a 3D camera is claimed."""
    import numpy as np

    pairs = [
        ("shoulder_a", "shoulder_b"), ("elbow_a", "elbow_b"),
        ("wrist_a", "wrist_b"), ("hip_a", "hip_b"),
        ("knee_a", "knee_b"), ("ankle_a", "ankle_b"),
    ]
    output = {}
    for direction in DISPLAY_DIRS:
        x_rows, y_rows = [], []
        for record in records_by_dir[direction]:
            for joint_a, joint_b in pairs:
                a = record["joints"][joint_a]["grid_xy"]
                b = record["joints"][joint_b]["grid_xy"]
                x_rows.extend([[a[0], a[1], 1.0], [b[0], b[1], 1.0]])
                y_rows.extend([b, a])
        x = np.asarray(x_rows, dtype=float)
        y = np.asarray(y_rows, dtype=float)
        matrix, _residuals, _rank, _singular = np.linalg.lstsq(x, y, rcond=None)
        affine = matrix.T
        predicted = x @ matrix
        rms = float(np.sqrt(np.mean(np.sum((predicted - y) ** 2, axis=1))))
        homogeneous = np.eye(3)
        homogeneous[:2, :] = affine
        involution_error = float(np.linalg.norm(homogeneous @ homogeneous - np.eye(3)))
        output[direction] = {
            "model": "data_fitted_2d_counterpart_transport",
            "affine_2x3": [[round(float(value), 9) for value in row] for row in affine],
            "determinant": round(float(np.linalg.det(affine[:, :2])), 9),
            "fit_rms_grid_units": round(rms, 9),
            "involution_error_frobenius": round(involution_error, 9),
            "camera_inverse_used": False,
            "consumer": "view-transition cost only; not active inside a same-aspect walk cycle",
        }
    return output


def render_mask_glyph(mask: int, cell_px: int):
    from PIL import Image

    tile = Image.new("RGB", (8, 8), (0, 0, 0))
    pixels = tile.load()
    for y in range(8):
        for x in range(8):
            if mask & (1 << (8 * y + x)):
                pixels[x, y] = (235, 235, 225)
    return tile.resize((cell_px, cell_px), Image.Resampling.NEAREST)


def cmd_unicode_skeletal(args: argparse.Namespace) -> int:
    """Compile stabilized semantic tracks to canonical non-emoji Unicode glyphs."""
    if not args.allow_rejected_rig_compiler:
        raise SystemExit(
            "rejected unicode-skeletal compiler is gated: its single-cell bone placement "
            "produced disconnected, oscillating, low-resolution output; pass "
            "--allow-rejected-rig-compiler only to reproduce that historical experiment"
        )
    from PIL import Image, ImageDraw, ImageFont

    proposals, source_hashes = load_rig_proposals()
    scales = compute_projection_scales(proposals)
    projected = {
        direction: [project_rig_record(record, direction, scales) for record in proposals[direction]]
        for direction in DISPLAY_DIRS
    }
    catalog = load_descriptor_catalog()
    compiled = {}
    for direction in DISPLAY_DIRS:
        targets, selected, candidates = compile_unicode_direction(
            projected[direction], catalog, args.top_k, args.lambda_temporal
        )
        compiled[direction] = {"targets": targets, "selected": selected, "candidates": candidates}

    cell_px, label_h, pad = args.cell_px, 16, 6
    panel_w = GRID_W * cell_px
    panel_h = GRID_H * cell_px
    canvas_w = pad + GRID_COLS * (panel_w + pad)
    canvas_h = pad + GRID_ROWS * (panel_h + label_h + pad)
    label_font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 12)
    gif_frames = []
    manifest_frames = {direction: [] for direction in DISPLAY_DIRS}
    for fi in range(18):
        canvas = Image.new("RGB", (canvas_w, canvas_h), (8, 8, 8))
        draw = ImageDraw.Draw(canvas)
        for direction_index, direction in enumerate(DISPLAY_DIRS):
            gc, gr = direction_index % GRID_COLS, direction_index // GRID_COLS
            x0 = pad + gc * (panel_w + pad)
            y0 = pad + gr * (panel_h + label_h + pad)
            draw.text((x0, y0), f"{direction} f{fi}", fill=(150, 210, 150), font=label_font)
            rows = []
            cells = []
            for row in range(GRID_H):
                chars = []
                for col in range(GRID_W):
                    cell = row * GRID_W + col
                    catalog_index = compiled[direction]["selected"][fi][cell]
                    mask = int(catalog["masks"][catalog_index])
                    scalar = int(catalog["scalars"][catalog_index])
                    glyph_id = int(catalog["glyph_ids"][catalog_index])
                    chars.append(chr(scalar))
                    canvas.paste(
                        render_mask_glyph(mask, cell_px),
                        (x0 + col * cell_px, y0 + label_h + row * cell_px),
                    )
                    owner_key = f"{col},{row}"
                    cells.append({
                        "cell": [col, row],
                        "owners": projected[direction][fi]["placement_owners"].get(owner_key, []),
                        "target_mask8_u64": f"0x{compiled[direction]['targets'][fi][cell]:016x}",
                        "glyph_id": glyph_id,
                        "unicode_scalar": scalar,
                        "character": chr(scalar),
                        "glyph_mask8_u64": f"0x{mask:016x}",
                    })
                rows.append("".join(chars))
            manifest_frames[direction].append({"frame_index": fi, "rows": rows, "cells": cells})
        gif_frames.append(canvas)

    timestamp = datetime.now().strftime("v%Y%m%dT%H%M%S")
    name = f"{timestamp}__unicode-skeletal__walk_all_directions"
    tracked_gif = TRACK_DIR / f"{name}.gif"
    desktop_gif = DESKTOP / f"{name}.gif"
    tracked_manifest = TRACK_DIR / f"{name}.frames.json"
    gif_frames[0].save(
        tracked_gif, save_all=True, append_images=gif_frames[1:],
        duration=args.frame_ms, loop=0, disposal=2,
    )
    desktop_gif.write_bytes(tracked_gif.read_bytes())

    all_selected = [
        index
        for direction in DISPLAY_DIRS
        for frame in compiled[direction]["selected"]
        for index in frame
    ]
    glyph_changes = 0
    for direction in DISPLAY_DIRS:
        selected = compiled[direction]["selected"]
        for cell in range(GRID_W * GRID_H):
            glyph_changes += sum(
                selected[fi][cell] != selected[(fi + 1) % 18][cell]
                for fi in range(18)
            )
    manifest = {
        "schema": "fl4561.unicode_skeletal_walk.v1",
        "status": "experimental_operator_review_open",
        "primary_owner": "FL-4561",
        "rq_owner": "RQ-201g",
        "transition_consumer": "RQ-201h",
        "name": name,
        "source_hashes": source_hashes,
        "grid": [GRID_W, GRID_H],
        "frames_per_direction": 18,
        "directions": DISPLAY_DIRS,
        "catalog": {
            "path": str(DESCRIPTOR_CENSUS.relative_to(REPO)),
            "eligible_non_emoji_count": len(catalog["rows"]),
            "descriptor_sha256": catalog["descriptor_sha256"],
            "manifest_sha256": catalog["manifest_sha256"],
            "emoji_excluded": True,
        },
        "solver": {
            "top_k": args.top_k,
            "lambda_temporal": args.lambda_temporal,
            "cycle_closed": True,
            "spatial_port_rounds": 3,
            "legacy_centroid_placement_used": False,
            "stabilized_joint_owner": RIG_SCHEMA,
        },
        "rig_summary": summarize_rig(projected, scales),
        "view_counterpart_relations": fit_view_counterpart_relations(projected),
        "summary": {
            "unique_glyph_count": len(set(all_selected)),
            "cyclic_glyph_change_count": glyph_changes,
            "emoji_selection_count": 0,
        },
        "gif_sha256": hashlib.sha256(tracked_gif.read_bytes()).hexdigest(),
        "frames": manifest_frames,
    }
    tracked_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    manifest_sha256 = hashlib.sha256(tracked_manifest.read_bytes()).hexdigest()
    registry = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else []
    registry.append({
        "name": name,
        "kind": "unicode-skeletal",
        "schema": manifest["schema"],
        "status": "experimental_operator_review_open",
        "timestamp": timestamp,
        "grid": [GRID_W, GRID_H],
        "frames_per_dir": 18,
        "palette_mode": "canonical_full_unicode_non_emoji",
        "tracked_gif": str(tracked_gif.relative_to(REPO)),
        "tracked_manifest": str(tracked_manifest.relative_to(REPO)),
        "desktop_gif": str(desktop_gif),
        "gif_sha256": manifest["gif_sha256"],
        "manifest_sha256": manifest_sha256,
    })
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=1) + "\n")
    print(f"compiled {len(catalog['rows'])} canonical non-emoji Unicode candidates")
    print(f"selected {manifest['summary']['unique_glyph_count']} unique glyphs; "
          f"cyclic changes={glyph_changes}")
    print(f"\nwrote FL-4561 stabilized Unicode walk @ {timestamp}")
    print(f"  desktop : {desktop_gif}")
    print(f"  tracked : {tracked_gif.relative_to(REPO)}")
    print(f"  manifest: {tracked_manifest.relative_to(REPO)}")
    return 0


# =============================================================================
# FL-4561 PER-PART CHECKPOINT COMPILATION — FULL CENTERLINES, 5x7 DIAGNOSTIC
# =============================================================================

def load_accepted_perpart_skeletons(upscale: int = 3):
    """Reconstruct the accepted per-part checkpoint from its authored sources.

    The GIF is a review surface, not a recoverable semantic source. This function
    validates that review surface's frozen hash, then repeats the exact stable
    crop plus independent per-colour skeletonization used by ``cmd_skeleton``.
    """
    if not ACCEPTED_PERPART_CHECKPOINT.is_file():
        raise SystemExit(f"accepted per-part checkpoint is missing: {ACCEPTED_PERPART_CHECKPOINT}")
    observed = hashlib.sha256(ACCEPTED_PERPART_CHECKPOINT.read_bytes()).hexdigest()
    if observed != ACCEPTED_PERPART_CHECKPOINT_SHA256:
        raise SystemExit(
            "accepted per-part checkpoint hash mismatch: "
            f"{observed} != {ACCEPTED_PERPART_CHECKPOINT_SHA256}"
        )
    directions = {}
    source_hashes = {}
    bboxes = {}
    for direction in DISPLAY_DIRS:
        source = SOURCE_ROOT / direction / f"{direction}.aseprite"
        if not source.is_file():
            raise SystemExit(f"missing rig source: {source}")
        frames = decode_aseprite(source)
        bbox = union_bbox(frames)
        bboxes[direction] = [int(value) for value in bbox]
        source_hashes[direction] = hashlib.sha256(source.read_bytes()).hexdigest()
        directions[direction] = [
            part_skeletons(frame, bbox, upscale) for frame in frames
        ]
    return directions, source_hashes, bboxes


def _transform_perpart_skeletons(parts: dict, grid_w: int, grid_h: int) -> dict:
    """Map every source centerline pixel into one fixed aspect-preserved grid."""
    import numpy as np
    from PIL import Image, ImageDraw

    canvas_w = grid_w * PERPART_SUBPIXELS
    canvas_h = grid_h * PERPART_SUBPIXELS
    any_mask = next(iter(parts.values()))
    source_h, source_w = any_mask.shape
    margin = 2.0
    scale = min(
        (canvas_w - 1.0 - margin * 2.0) / max(1, source_w - 1),
        (canvas_h - 1.0 - margin * 2.0) / max(1, source_h - 1),
    )
    offset_x = (canvas_w - (source_w - 1) * scale) / 2.0
    offset_y = (canvas_h - (source_h - 1) * scale) / 2.0
    output = {}
    for part, source_mask in parts.items():
        image = Image.new("1", (canvas_w, canvas_h), 0)
        draw = ImageDraw.Draw(image)
        nodes = {(int(y), int(x)) for y, x in np.argwhere(source_mask)}

        def mapped(node):
            y, x = node
            return (round(offset_x + x * scale), round(offset_y + y * scale))

        for node in sorted(nodes):
            point = mapped(node)
            draw.point(point, fill=1)
            y, x = node
            for dy, dx in ((0, 1), (1, -1), (1, 0), (1, 1)):
                neighbor = (y + dy, x + dx)
                if neighbor in nodes:
                    draw.line((point, mapped(neighbor)), fill=1, width=1)
        output[part] = np.asarray(image, dtype=bool).copy()
    return output


def _nearest_mask_points(mask_a, mask_b):
    import numpy as np

    points_a = np.argwhere(mask_a)
    points_b = np.argwhere(mask_b)
    if len(points_a) == 0 or len(points_b) == 0:
        return None
    delta = points_a[:, None, :] - points_b[None, :, :]
    distance2 = np.sum(delta * delta, axis=2)
    index = int(np.argmin(distance2))
    ia, ib = np.unravel_index(index, distance2.shape)
    return points_a[ia], points_b[ib], float(distance2[ia, ib]) ** 0.5


def _bridge_semantic_attachments(parts: dict, max_distance: float = 12.0) -> list[dict]:
    """Join only authored anatomical neighbors; retain the bridge receipt."""
    import numpy as np
    from PIL import Image, ImageDraw

    attachments = [
        ("head", "torso"),
        ("torso", "arm_a"),
        ("torso", "arm_b"),
        ("torso", "leg_a"),
        ("torso", "leg_b"),
    ]
    receipts = []
    for part_a, part_b in attachments:
        if part_a not in parts or part_b not in parts:
            continue
        nearest = _nearest_mask_points(parts[part_a], parts[part_b])
        if nearest is None:
            continue
        point_a, point_b, distance = nearest
        if distance > max_distance:
            continue
        bridge = Image.new("1", (parts[part_a].shape[1], parts[part_a].shape[0]), 0)
        ImageDraw.Draw(bridge).line(
            (int(point_a[1]), int(point_a[0]), int(point_b[1]), int(point_b[0])),
            fill=1,
            width=1,
        )
        bridge_mask = np.asarray(bridge, dtype=bool)
        parts[part_a] |= bridge_mask
        parts[part_b] |= bridge_mask
        receipts.append({
            "parts": [part_a, part_b],
            "distance_subpixels": round(distance, 6),
            "endpoints_yx": [point_a.tolist(), point_b.tolist()],
        })
    return receipts


def _component_count(mask) -> int:
    from skimage import measure

    return int(measure.label(mask, connectivity=2).max())


def _semantic_fractions_by_cell(parts: dict, grid_w: int, grid_h: int) -> list[dict]:
    """Assign proximal/distal arc positions before attachment bridges are added."""
    import numpy as np

    def existing_centerline_path(mask):
        nodes = {(int(y), int(x)) for y, x in np.argwhere(mask)}
        if not nodes:
            return []
        if len(nodes) == 1:
            y, x = next(iter(nodes))
            return [(float(x), float(y))]
        first, _parents, _distances = _dijkstra_farthest(nodes, min(nodes))
        last, parents, _distances = _dijkstra_farthest(nodes, first)
        reversed_nodes = [last]
        while reversed_nodes[-1] != first:
            reversed_nodes.append(parents[reversed_nodes[-1]])
        reversed_nodes.reverse()
        return [(float(x), float(y)) for y, x in reversed_nodes]

    fractions = [dict() for _ in range(grid_w * grid_h)]
    torso = parts.get("torso")
    for part, mask in parts.items():
        path = existing_centerline_path(mask)
        if not path:
            continue
        if part in {"head", "torso"}:
            if path[0][1] > path[-1][1]:
                path.reverse()
        elif torso is not None:
            path = _path_proximal_first(path, torso)
        denominator = max(1, len(path) - 1)
        per_cell = {}
        for index, point in enumerate(path):
            x, y = int(round(point[0])), int(round(point[1]))
            col = min(grid_w - 1, max(0, x // PERPART_SUBPIXELS))
            row = min(grid_h - 1, max(0, y // PERPART_SUBPIXELS))
            per_cell.setdefault(row * grid_w + col, []).append(index / denominator)
        for cell, values in per_cell.items():
            fractions[cell][part] = round(sum(values) / len(values), 6)
    return fractions


def project_perpart_frame(parts: dict, grid_w: int = PERPART_GRID_W,
                          grid_h: int = PERPART_GRID_H) -> dict:
    """Preserve every intersected cell plus its persistent colour-part owner."""
    import numpy as np
    from PIL import Image

    transformed = _transform_perpart_skeletons(parts, grid_w, grid_h)
    semantic_fractions = _semantic_fractions_by_cell(transformed, grid_w, grid_h)
    components_before = _component_count(np.logical_or.reduce(list(transformed.values())))
    bridges = _bridge_semantic_attachments(transformed)
    combined = np.logical_or.reduce(list(transformed.values()))
    targets = []
    owners = []
    semantic_keys = []
    centroids = []
    for row in range(grid_h):
        for col in range(grid_w):
            y0, y1 = row * PERPART_SUBPIXELS, (row + 1) * PERPART_SUBPIXELS
            x0, x1 = col * PERPART_SUBPIXELS, (col + 1) * PERPART_SUBPIXELS
            patch = combined[y0:y1, x0:x1]
            targets.append(_image_to_mask8(Image.fromarray(patch.astype("uint8") * 255)))
            counts = {
                part: int(mask[y0:y1, x0:x1].sum())
                for part, mask in transformed.items()
                if mask[y0:y1, x0:x1].any()
            }
            owners.append([
                part for part, _count in sorted(
                    counts.items(), key=lambda item: (-item[1], item[0])
                )
            ])
            keys = []
            for part in owners[-1]:
                fraction = semantic_fractions[len(targets) - 1].get(part)
                if fraction is None:
                    keys.append(f"{part}:attachment")
                elif part == "head":
                    keys.append("head:0")
                else:
                    keys.append(f"{part}:{min(1, int(fraction * 2.0))}")
            semantic_keys.append(keys)
            points = np.argwhere(patch)
            if len(points):
                centroids.append([
                    round(col + float(points[:, 1].mean()) / PERPART_SUBPIXELS, 6),
                    round(row + float(points[:, 0].mean()) / PERPART_SUBPIXELS, 6),
                ])
            else:
                centroids.append(None)
    return {
        "target_masks": targets,
        "cell_owners": owners,
        "cell_semantic_keys": semantic_keys,
        "cell_centroids": centroids,
        "bridges": bridges,
        "components_before_bridging": components_before,
        "components_after_bridging": _component_count(combined),
        "occupied_cell_count": sum(mask != 0 for mask in targets),
    }


def _semantic_cell_match(source: dict, target: dict) -> dict[int, int]:
    """Match moving cells by shared persistent part identity and ink position."""
    mapping = {}
    for source_cell, source_keys in enumerate(source["cell_semantic_keys"]):
        if not source_keys:
            continue
        sx, sy = source["cell_centroids"][source_cell]
        choices = []
        for target_cell, target_keys in enumerate(target["cell_semantic_keys"]):
            shared = set(source_keys).intersection(target_keys)
            if not shared:
                continue
            tx, ty = target["cell_centroids"][target_cell]
            motion = math.hypot(tx - sx, ty - sy)
            shape = (
                source["target_masks"][source_cell] ^ target["target_masks"][target_cell]
            ).bit_count() / 64.0
            choices.append((motion + 0.25 * shape, -len(shared), target_cell))
        if choices:
            mapping[source_cell] = min(choices)[2]
    return mapping


def _edge_signature(mask: int, side: str) -> int:
    if side == "left":
        return sum(((mask >> (8 * y)) & 1) << y for y in range(8))
    if side == "right":
        return sum(((mask >> (8 * y + 7)) & 1) << y for y in range(8))
    if side == "top":
        return mask & 0xFF
    if side == "bottom":
        return (mask >> 56) & 0xFF
    raise ValueError(side)


def _glyph_index_cost(target_mask: int, index: int, catalog: dict) -> float:
    if target_mask == 0:
        return 0.0 if index == catalog["space_index"] else 1.0
    target_endpoints, target_junctions = _mask8_topology(target_mask)
    mask = int(catalog["masks"][index])
    cost = (mask ^ target_mask).bit_count() / 64.0
    cost += 0.055 * min(6, abs(int(catalog["endpoints"][index]) - target_endpoints))
    cost += 0.075 * min(4, abs(int(catalog["junctions"][index]) - target_junctions))
    if mask == 0:
        cost += 0.75
    return cost


def compile_perpart_direction(records: list[dict], catalog: dict, top_k: int,
                              lambda_temporal: float, lambda_spatial: float,
                              rounds: int = 5):
    """Jointly label full centerline cells with semantic motion correspondence."""
    cell_count = PERPART_GRID_W * PERPART_GRID_H
    cache = {}
    candidates = []
    for record in records:
        frame_candidates = []
        for target in record["target_masks"]:
            if target not in cache:
                cache[target] = glyph_candidates(target, catalog, top_k)
            frame_candidates.append([dict(candidate) for candidate in cache[target]])
        candidates.append(frame_candidates)

    # The supply remains the full non-emoji Unicode census. This derived
    # per-track codebook only makes candidates that recur across the same
    # authored proximal/distal identity available to every frame in that track.
    # It fixes top-K lattice disjointness without imposing a curated alphabet.
    codebook_stats = {}
    for fi, record in enumerate(records):
        for cell, keys in enumerate(record["cell_semantic_keys"]):
            for key in keys:
                stats = codebook_stats.setdefault(key, {})
                for candidate in candidates[fi][cell]:
                    count, cost = stats.get(candidate["index"], (0, 0.0))
                    stats[candidate["index"]] = (
                        count + 1, cost + candidate["base_cost"]
                    )
    codebooks = {}
    for key, stats in codebook_stats.items():
        ranked = sorted(
            stats,
            key=lambda index: (
                -stats[index][0],
                stats[index][1] / stats[index][0],
                int(catalog["scalars"][index]),
            ),
        )
        codebooks[key] = ranked[:8]
    for fi, record in enumerate(records):
        for cell, keys in enumerate(record["cell_semantic_keys"]):
            candidates[fi][cell] = candidates[fi][cell][:2]
            admitted = {candidate["index"] for candidate in candidates[fi][cell]}
            for key in keys:
                for index in codebooks.get(key, []):
                    if index in admitted:
                        continue
                    cost = _glyph_index_cost(record["target_masks"][cell], index, catalog)
                    candidates[fi][cell].append({
                        "index": index, "base_cost": cost, "cost": cost,
                    })
                    admitted.add(index)
    selected = [
        [frame_candidates[cell][0]["index"] for cell in range(cell_count)]
        for frame_candidates in candidates
    ]
    next_matches = [
        _semantic_cell_match(records[fi], records[(fi + 1) % len(records)])
        for fi in range(len(records))
    ]
    prev_matches = [
        _semantic_cell_match(records[fi], records[(fi - 1) % len(records)])
        for fi in range(len(records))
    ]
    neighbors = [
        (-1, 0, "left", "right"), (1, 0, "right", "left"),
        (0, -1, "top", "bottom"), (0, 1, "bottom", "top"),
    ]
    for _round in range(rounds):
        previous_selection = [list(frame) for frame in selected]
        for fi, record in enumerate(records):
            for cell in range(cell_count):
                if record["target_masks"][cell] == 0:
                    selected[fi][cell] = catalog["space_index"]
                    continue
                x, y = cell % PERPART_GRID_W, cell // PERPART_GRID_W
                scored = []
                for candidate in candidates[fi][cell]:
                    candidate_index = candidate["index"]
                    candidate_mask = int(catalog["masks"][candidate_index])
                    cost = candidate["base_cost"]
                    for adjacent_frame, mapping in (
                        ((fi + 1) % len(records), next_matches[fi]),
                        ((fi - 1) % len(records), prev_matches[fi]),
                    ):
                        linked_cell = mapping.get(cell)
                        if linked_cell is None:
                            continue
                        linked_index = previous_selection[adjacent_frame][linked_cell]
                        linked_mask = int(catalog["masks"][linked_index])
                        cost += lambda_temporal * (
                            (candidate_mask ^ linked_mask).bit_count() / 64.0
                            + (0.12 if candidate_index != linked_index else 0.0)
                        )
                    for dx, dy, side, opposite in neighbors:
                        nx, ny = x + dx, y + dy
                        if not (0 <= nx < PERPART_GRID_W and 0 <= ny < PERPART_GRID_H):
                            continue
                        neighbor_cell = ny * PERPART_GRID_W + nx
                        neighbor_index = previous_selection[fi][neighbor_cell]
                        neighbor_mask = int(catalog["masks"][neighbor_index])
                        target_a = _edge_signature(record["target_masks"][cell], side)
                        target_b = _edge_signature(record["target_masks"][neighbor_cell], opposite)
                        actual_a = _edge_signature(candidate_mask, side)
                        actual_b = _edge_signature(neighbor_mask, opposite)
                        boundary_cost = (
                            (actual_a ^ target_a).bit_count()
                            + (actual_b ^ target_b).bit_count()
                        ) / 16.0
                        desired_join = target_a & target_b
                        if desired_join:
                            boundary_cost += (actual_a ^ actual_b).bit_count() / 8.0
                        cost += lambda_spatial * boundary_cost
                    scored.append((cost, int(catalog["scalars"][candidate_index]), candidate_index))
                selected[fi][cell] = min(scored)[2]
    return selected, candidates, next_matches


def _canonical_glyph_renderer():
    import sys

    scripts_dir = str(REPO / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from fl4482_font_chain import CanonicalFontChain, font_chain_receipt

    return CanonicalFontChain(), font_chain_receipt()


def cmd_unicode_perpart_skeleton(args: argparse.Namespace) -> int:
    """Compile the accepted full per-part centerlines into a 5x7 Unicode walk."""
    if not args.allow_rejected_cell_compiler:
        raise SystemExit(
            "rejected per-part cell compiler is gated: it omitted counterpart "
            "geometry, hard shared-port connectivity, and occupancy hysteresis; "
            "pass --allow-rejected-cell-compiler only to reproduce its evidence"
        )
    from PIL import Image, ImageDraw, ImageFont

    skeletons, source_hashes, bboxes = load_accepted_perpart_skeletons(upscale=3)
    projected = {
        direction: [project_perpart_frame(parts) for parts in skeletons[direction]]
        for direction in DISPLAY_DIRS
    }
    catalog = load_descriptor_catalog()
    compiled = {}
    for direction in DISPLAY_DIRS:
        selected, candidates, next_matches = compile_perpart_direction(
            projected[direction], catalog, args.top_k,
            args.lambda_temporal, args.lambda_spatial, args.rounds,
        )
        compiled[direction] = {
            "selected": selected,
            "candidates": candidates,
            "next_matches": next_matches,
        }
        print(
            f"  {direction}: occupied cells "
            f"{min(r['occupied_cell_count'] for r in projected[direction])}-"
            f"{max(r['occupied_cell_count'] for r in projected[direction])}"
        )

    font_chain, font_receipt = _canonical_glyph_renderer()
    tile_cache = {}

    def rendered_tile(scalar: int):
        if scalar not in tile_cache:
            result = font_chain.render(scalar)
            if result is None:
                raise SystemExit(f"selected scalar U+{scalar:04X} has no pinned font owner")
            rgba = Image.frombytes("RGBA", (font_chain.cell_px, font_chain.cell_px), result.rgba8)
            tile = Image.new("RGB", rgba.size, (0, 0, 0))
            tile.paste(rgba.convert("RGB"), mask=rgba.getchannel("A"))
            tile_cache[scalar] = (tile, result.font_index, result.font_name)
        return tile_cache[scalar]

    cell_px, label_h, pad = args.cell_px, 16, 6
    panel_w = PERPART_GRID_W * cell_px
    panel_h = PERPART_GRID_H * cell_px
    canvas_w = pad + GRID_COLS * (panel_w + pad)
    canvas_h = pad + GRID_ROWS * (panel_h + label_h + pad)
    label_font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 12)
    gif_frames = []
    manifest_frames = {direction: [] for direction in DISPLAY_DIRS}
    for fi in range(18):
        canvas = Image.new("RGB", (canvas_w, canvas_h), (8, 8, 8))
        draw = ImageDraw.Draw(canvas)
        for direction_index, direction in enumerate(DISPLAY_DIRS):
            gc, gr = direction_index % GRID_COLS, direction_index // GRID_COLS
            x0 = pad + gc * (panel_w + pad)
            y0 = pad + gr * (panel_h + label_h + pad)
            draw.text((x0, y0), f"{direction} f{fi}", fill=(150, 210, 150), font=label_font)
            rows, cells = [], []
            for row in range(PERPART_GRID_H):
                chars = []
                for col in range(PERPART_GRID_W):
                    cell = row * PERPART_GRID_W + col
                    catalog_index = compiled[direction]["selected"][fi][cell]
                    mask = int(catalog["masks"][catalog_index])
                    scalar = int(catalog["scalars"][catalog_index])
                    glyph_id = int(catalog["glyph_ids"][catalog_index])
                    chars.append(chr(scalar))
                    tile, font_index, font_name = rendered_tile(scalar)
                    canvas.paste(
                        tile.resize((cell_px, cell_px), Image.Resampling.NEAREST),
                        (x0 + col * cell_px, y0 + label_h + row * cell_px),
                    )
                    cells.append({
                        "cell": [col, row],
                        "owners": projected[direction][fi]["cell_owners"][cell],
                        "semantic_next_cell": compiled[direction]["next_matches"][fi].get(cell),
                        "target_mask8_u64": f"0x{projected[direction][fi]['target_masks'][cell]:016x}",
                        "glyph_id": glyph_id,
                        "unicode_scalar": scalar,
                        "character": chr(scalar),
                        "glyph_mask8_u64": f"0x{mask:016x}",
                        "font_index": font_index,
                        "font_name": font_name,
                    })
                rows.append("".join(chars))
            manifest_frames[direction].append({"frame_index": fi, "rows": rows, "cells": cells})
        gif_frames.append(canvas)

    timestamp = datetime.now().strftime("v%Y%m%dT%H%M%S")
    name = f"{timestamp}__unicode-perpart-skeleton-5x7__walk_all_directions"
    tracked_gif = TRACK_DIR / f"{name}.gif"
    desktop_gif = DESKTOP / f"{name}.gif"
    tracked_manifest = TRACK_DIR / f"{name}.frames.json"
    gif_frames[0].save(
        tracked_gif, save_all=True, append_images=gif_frames[1:],
        duration=args.frame_ms, loop=0, disposal=2,
    )
    desktop_gif.write_bytes(tracked_gif.read_bytes())

    selected_indices = [
        index for direction in DISPLAY_DIRS
        for frame in compiled[direction]["selected"] for index in frame
    ]
    semantic_changes = 0
    semantic_edges = 0
    for direction in DISPLAY_DIRS:
        selected = compiled[direction]["selected"]
        for fi, mapping in enumerate(compiled[direction]["next_matches"]):
            for cell, next_cell in mapping.items():
                semantic_edges += 1
                semantic_changes += selected[fi][cell] != selected[(fi + 1) % 18][next_cell]
    manifest = {
        "schema": "fl4561.unicode_perpart_skeleton.v1",
        "status": "experimental_operator_review_open",
        "primary_owner": "FL-4561",
        "rq_owner": "RQ-201g",
        "transition_consumer": "RQ-201h",
        "name": name,
        "accepted_checkpoint": {
            "path": str(ACCEPTED_PERPART_CHECKPOINT.relative_to(REPO)),
            "sha256": ACCEPTED_PERPART_CHECKPOINT_SHA256,
            "reconstruction": "stable union bbox + per-colour skeletonize at 3x",
        },
        "source_hashes": source_hashes,
        "source_bboxes": bboxes,
        "grid": [PERPART_GRID_W, PERPART_GRID_H],
        "frames_per_direction": 18,
        "directions": DISPLAY_DIRS,
        "catalog": {
            "path": str(DESCRIPTOR_CENSUS.relative_to(REPO)),
            "eligible_non_emoji_count": len(catalog["rows"]),
            "descriptor_sha256": catalog["descriptor_sha256"],
            "manifest_sha256": catalog["manifest_sha256"],
            "emoji_excluded": True,
        },
        "font_chain": font_receipt,
        "solver": {
            "top_k": args.top_k,
            "lambda_temporal": args.lambda_temporal,
            "lambda_spatial": args.lambda_spatial,
            "coordinate_rounds": args.rounds,
            "cycle_closed": True,
            "correspondence": "nearest moving cell sharing authored colour-part identity",
            "single_placement_cell_used": False,
            "all_skeleton_cell_intersections_preserved": True,
            "display_uses_actual_pinned_font_raster": True,
            "mirror_relation_cost_active": False,
            "connectivity_enforcement": "soft_boundary_penalty_not_constraint",
            "occupancy_hysteresis_active": False,
        },
        "projection_summary": {
            "occupied_cell_range": [
                min(record["occupied_cell_count"] for records in projected.values() for record in records),
                max(record["occupied_cell_count"] for records in projected.values() for record in records),
            ],
            "component_count_before_range": [
                min(record["components_before_bridging"] for records in projected.values() for record in records),
                max(record["components_before_bridging"] for records in projected.values() for record in records),
            ],
            "component_count_after_range": [
                min(record["components_after_bridging"] for records in projected.values() for record in records),
                max(record["components_after_bridging"] for records in projected.values() for record in records),
            ],
            "semantic_bridge_count": sum(
                len(record["bridges"]) for records in projected.values() for record in records
            ),
        },
        "summary": {
            "unique_glyph_count": len(set(selected_indices)),
            "semantic_correspondence_edge_count": semantic_edges,
            "semantic_glyph_change_count": semantic_changes,
            "semantic_glyph_change_rate": round(semantic_changes / max(1, semantic_edges), 9),
            "emoji_selection_count": 0,
        },
        "gif_sha256": hashlib.sha256(tracked_gif.read_bytes()).hexdigest(),
        "frames": manifest_frames,
    }
    tracked_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    manifest_sha256 = hashlib.sha256(tracked_manifest.read_bytes()).hexdigest()
    registry = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else []
    registry.append({
        "name": name,
        "kind": "unicode-perpart-skeleton",
        "schema": manifest["schema"],
        "status": "experimental_operator_review_open",
        "timestamp": timestamp,
        "grid": [PERPART_GRID_W, PERPART_GRID_H],
        "frames_per_dir": 18,
        "palette_mode": "canonical_full_unicode_non_emoji",
        "source_checkpoint": str(ACCEPTED_PERPART_CHECKPOINT.relative_to(REPO)),
        "tracked_gif": str(tracked_gif.relative_to(REPO)),
        "tracked_manifest": str(tracked_manifest.relative_to(REPO)),
        "desktop_gif": str(desktop_gif),
        "gif_sha256": manifest["gif_sha256"],
        "manifest_sha256": manifest_sha256,
    })
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=1) + "\n")
    print(f"compiled {len(catalog['rows'])} canonical non-emoji Unicode candidates")
    print(
        f"selected {manifest['summary']['unique_glyph_count']} unique glyphs; "
        f"semantic changes={semantic_changes}/{semantic_edges}"
    )
    print(f"\nwrote FL-4561 per-part Unicode walk @ {timestamp}")
    print(f"  desktop : {desktop_gif}")
    print(f"  tracked : {tracked_gif.relative_to(REPO)}")
    print(f"  manifest: {tracked_manifest.relative_to(REPO)}")
    return 0


# -----------------------------------------------------------------------------
# LEGACY FL-4561 GLYPH PLACEMENT — PRESERVED, EXPLICIT OPT-IN ONLY
# -----------------------------------------------------------------------------
def load_emoji_codepoints() -> set[int]:
    """Codepoints carrying any Emoji property (>= U+0080), to subtract from the
    palette. ASCII keycap bases are kept — they are ordinary readable glyphs."""
    emoji: set[int] = set()
    for line in EMOJI_DATA.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        field = line.split(";", 1)[0].strip()
        if not field:
            continue
        if ".." in field:
            lo, hi = [int(x, 16) for x in field.split("..", 1)]
        else:
            lo = hi = int(field, 16)
        for cp in range(lo, hi + 1):
            if cp >= 0x80:
                emoji.add(cp)
    return emoji


def parse_authored_vocab(path: Path, rows: int):
    """Parse a hand-authored sprite_frames file into a per-ROW glyph vocabulary.

    Poses are separated by blank lines or '#' headers. Within a pose the line
    index is anatomy height (row 0 = head, ... last = feet), which lines up with
    our spine-centred grid rows. Returns `rows` sets of characters — the glyphs a
    human actually used at each height. This is a PREFERENCE, never a cage: the
    solver always keeps full-palette fallback candidates alongside these."""
    if not path.exists():
        return [set() for _ in range(rows)]
    vocab = [set() for _ in range(rows)]
    pose: list[str] = []

    def flush(p):
        for r, line in enumerate(p[:rows]):
            for ch in line:
                if ch not in (" ", "\t"):
                    vocab[r].add(ch)

    for line in path.read_text().splitlines():
        if line.startswith("#") or line.strip() == "":
            if pose:
                flush(pose)
                pose = []
            continue
        pose.append(line)
    if pose:
        flush(pose)
    return vocab


def cyclic_viterbi(order, cand, feats, lambda_t, np):
    """Pick one glyph per occupied frame for ONE grid cell, minimising
        sum_frame unary(fidelity) + lambda_t * sum_edge (1 - cos(glyph_i, glyph_j))
    around the walk cycle (last occupied frame wraps to the first). `order` is the
    list of occupied frame indices; `cand[fi]=(idx_array, unary_array)`. Returns
    {frame_index: chosen_palette_index}. Exact over the bounded candidate set."""
    m = len(order)
    if m == 1:
        idx, un = cand[order[0]]
        return {order[0]: int(idx[int(np.argmin(un))])}
    best_total, best_path = float("inf"), None
    idx0, un0 = cand[order[0]]
    for s0 in range(len(idx0)):
        cost = {s0: float(un0[s0])}
        path = {s0: [s0]}
        prev = order[0]
        for k in range(1, m):
            fi = order[k]
            idx, un = cand[fi]
            nc, npth = {}, {}
            for si in range(len(idx)):
                fcur = feats[idx[si]]
                best = None
                for sp, pc in cost.items():
                    trans = 1.0 - float(feats[cand[prev][0][sp]] @ fcur)
                    v = pc + float(un[si]) + lambda_t * trans
                    if best is None or v < best[0]:
                        best = (v, sp)
                nc[si] = best[0]
                npth[si] = path[best[1]] + [si]
            cost, path, prev = nc, npth, fi
        idxL = cand[order[-1]][0]
        idxF = cand[order[0]][0]
        for sl, pc in cost.items():
            trans = 1.0 - float(feats[idxL[sl]] @ feats[idxF[s0]])
            total = pc + lambda_t * trans
            if total < best_total:
                best_total, best_path = total, path[sl]
    return {order[k]: int(cand[order[k]][0][best_path[k]]) for k in range(m)}


def cmd_asciify(args: argparse.Namespace) -> int:
    if not args.allow_legacy_placement:
        raise SystemExit(
            "legacy asciify placement is gated: pass --allow-legacy-placement "
            "only to reproduce historical experimental output; use the accepted per-part checkpoint"
        )
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    import sys as _sys
    _sys.path.insert(0, str(CLIPASSO))
    import unifont_match as um

    GW, GH = args.grid_w, args.grid_h       # 5x5 by default (spine on centre col)
    K = args.top_k
    W_ORIENT = args.lambda_orient
    import fl4512_p4b_structural_feasibility as p4b
    p4b.LAMBDA_SPATIAL = args.lambda_spatial      # within-frame connectivity weight
    p4b.LAMBDA_TEMPORAL = args.lambda_temporal    # frame-to-frame coherence weight

    # 1. palette = full BMP Unifont atlas minus emoji (CJK strokes welcome) ------
    emoji = load_emoji_codepoints()
    chars, feats, _prov, _stats = um.build_atlas()
    keep = [i for i, c in enumerate(chars) if ord(c) not in emoji]
    chars = [chars[i] for i in keep]
    feats = feats[np.asarray(keep, dtype=int)]
    print(f"palette: {len(chars)} glyphs (Unifont BMP minus {len(emoji)} emoji cps)")

    def extend_palette(extra_chars):
        """Add any authored glyphs the font can draw to the palette, returning a
        {char: palette_index} map. Authored glyphs are often astral (e.g. 𞤧) and
        absent from the BMP atlas, so we rasterise + feature them here."""
        nonlocal chars, feats
        have = {c: i for i, c in enumerate(chars)}
        font = ImageFont.truetype(um.UNIFONT, um.TH)
        new_feats = list(feats)
        idxmap = {}
        for ch in extra_chars:
            if ch in have:
                idxmap[ch] = have[ch]
                continue
            tile = Image.new("L", (um.TW, um.TH), 0)
            try:
                ImageDraw.Draw(tile).text((0, 0), ch, font=font, fill=255)
            except Exception:
                continue
            m = np.asarray(tile) > 100
            if int(m.sum()) == 0:                 # font cannot draw it -> unusable
                continue
            idxmap[ch] = len(chars)
            chars.append(ch)
            new_feats.append(um.feature(m))
        feats = np.asarray(new_feats, dtype=float)
        return idxmap

    # AUTHORED VOCABULARY = soft prior + FALLBACK (per the operator's constraint).
    # A hard authored dictionary starves high-motion limbs/legs (their orientation
    # sweeps past the ~few authored glyphs) and breaks temporal coherence. So the
    # authored per-row glyphs are only PREFERRED (a unary discount); every cell
    # still carries full-palette top-K candidates, so a leg mid-stride falls back
    # to the closest palette shape instead of snapping to a wrong dictionary glyph.
    vocab_authored = (args.vocab == "authored")
    dictset = [set() for _ in range(GH)]
    if vocab_authored:
        row_chars = parse_authored_vocab(Path(args.authored_file), GH)
        idxmap = extend_palette(set().union(*row_chars) if any(row_chars) else set())
        for r in range(GH):
            dictset[r] = {idxmap[ch] for ch in row_chars[r] if ch in idxmap}
        print(f"  authored vocab per row: {[len(s) for s in dictset]} font-drawable glyphs; FALLBACK on")
    DICT_BONUS = args.dict_bonus

    def ang_dist(a, b):
        # angular distance between two orientations in [0, pi), normalised to [0,1]
        d = abs(a - b)
        return min(d, math.pi - d) / (math.pi / 2.0)

    # 2. PASS 1 — sample every direction/frame into a spine-centred grid; per cell
    #    keep the 16x16 skeleton patch, its dominant orientation (limb tangent),
    #    and top-K glyph candidates by shape. Collect the candidate union.
    raw: dict[str, list] = {}
    union: set[int] = set()
    for d in DISPLAY_DIRS:
        frames = decode_aseprite(SOURCE_ROOT / d / f"{d}.aseprite")
        y0, y1, x0, x1 = union_bbox(frames)
        bh, bw = y1 - y0, x1 - x0
        cellw, cellh = bw / GW, bh / GH
        seq = []
        for fr in frames:
            parts = part_skeletons(fr, (y0, y1, x0, x1), upscale=1)
            combined = np.zeros((bh, bw), bool)
            for sk in parts.values():
                combined |= sk
            # spine centering: shift the column grid so the torso centroid is centred
            tmask = part_masks(fr).get("torso")
            if tmask is not None and tmask[y0:y1, x0:x1].any():
                tcx = float(np.where(tmask[y0:y1, x0:x1])[1].mean())
            else:
                tcx = bw / 2.0
            grid_x0 = tcx - (GW / 2.0) * cellw
            cells = {}
            for r in range(GH):
                ry0, ry1 = int(round(r * cellh)), int(round((r + 1) * cellh))
                for c in range(GW):
                    cx0 = int(round(grid_x0 + c * cellw))
                    cx1 = int(round(grid_x0 + (c + 1) * cellw))
                    a, b = max(0, cx0), min(bw, cx1)
                    e, f = max(0, ry0), min(bh, ry1)
                    if b <= a or f <= e:
                        continue
                    # PART-TEMPLATE: the cell is owned by whichever body part has
                    # the most skeleton pixels here, and we match ONLY that part's
                    # fragment -- so an arm cell is never confused by a stray torso
                    # pixel, and the glyph follows that one limb's shape. PER-CELL
                    # mode instead matches the combined skeleton (all parts mixed).
                    if args.mode == "part-template":
                        part, sub, dom = None, None, 0
                        for pname, sk in parts.items():
                            ssub = sk[e:f, a:b]
                            cnt = int(ssub.sum())
                            if cnt > dom:
                                dom, part, sub = cnt, pname, ssub
                        if dom == 0:
                            continue
                    else:
                        sub = combined[e:f, a:b]
                        part = None
                        if not sub.any():
                            continue
                    patch = p4b.resize_mask(sub)                 # 16x16 source mask
                    orient = p4b.dominant_orientation(patch)     # limb tangent angle
                    scores = feats @ um.feature(sub)
                    kk = min(K, len(scores))
                    top = np.argpartition(-scores, kk - 1)[:kk]  # full-palette fallback
                    # combine fallback top-K with this row's authored glyphs, so
                    # both compete; the authored ones get a discount in PASS 2.
                    drow = np.array(sorted(dictset[r]), dtype=int)
                    idx = np.unique(np.concatenate([top, drow])).astype(int) if len(drow) else top.astype(int)
                    fid = 1.0 - scores[idx]
                    order = np.argsort(fid)                       # best shape-fit first
                    idx, fid = idx[order], fid[order]
                    is_dict = np.array([int(i) in dictset[r] for i in idx], dtype=bool)
                    cells[(r, c)] = (patch, orient, idx, fid, is_dict, part)
                    union.update(int(x) for x in idx)
            seq.append(cells)
        raw[d] = seq

    union_sorted = sorted(union)
    palmap = {p: l for l, p in enumerate(union_sorted)}
    umasks = p4b.rasterize_chars([chars[p] for p in union_sorted])
    descriptors = [p4b.describe_glyph(l, chars[union_sorted[l]], umasks[l])
                   for l in range(len(union_sorted))]
    print(f"  described {len(descriptors)} candidate glyphs")

    # 3. PASS 2 — per direction: build the P4b frame lattice with unary =
    #    shape-fidelity + orientation penalty; run within-frame connectivity
    #    (spatial port-join so adjacent glyphs JOIN at their shared border), then
    #    a per-cell cyclic temporal solve on cells present in every frame.
    #    Symmetry stays EMERGENT — nothing is mirror-locked.
    dir_frames_grids: dict[str, list[list[str]]] = {}
    for d in DISPLAY_DIRS:
        fds = []
        for cells in raw[d]:
            source_masks, cand, unary = {}, {}, {}
            for (r, c), (patch, orient, idx, fid, is_dict, _part) in cells.items():
                source_masks[(r, c)] = patch
                locals_ = np.array([palmap[int(p)] for p in idx], dtype=int)
                cand[(r, c)] = locals_
                # unary = shape-fidelity + orientation penalty - authored-vocab
                # discount (soft prior). The discount only tips near-ties toward the
                # authored glyph; a much-better fallback still wins (limbs/legs).
                unary[(r, c)] = np.array(
                    [fid[j] + W_ORIENT * ang_dist(orient, descriptors[int(locals_[j])].orientation)
                     - (DICT_BONUS if is_dict[j] else 0.0)
                     for j in range(len(locals_))], dtype=float)
            fds.append(p4b.FrameData(d, SOURCE_ROOT, None, source_masks, cand, unary, {}))
        # within-frame connectivity (glyphs join across cell borders)
        result = [p4b.spatial_refine(fd, descriptors, p4b.independent_labels(fd)) for fd in fds]
        n = len(fds)
        # temporal coherence for cells that persist across the whole walk cycle
        common = sorted(set.intersection(*[set(fd.candidates) for fd in fds])) if fds else []
        for pos in common:
            path, _e, _en = p4b.cyclic_track_dp(
                fds, [pos] * n, ["source_best"] * n, result, descriptors)
            for fi, loc in enumerate(path):
                result[fi][pos] = loc
        dir_frames_grids[d] = [
            ["".join(descriptors[result[fi][(r, c)]].char if (r, c) in result[fi] else " "
                     for c in range(GW)) for r in range(GH)]
            for fi in range(n)
        ]
        print(f"  {d}: connectivity+orientation+temporal (stable cells: {len(common)})")

    # 4. render the 2x4 grid GIF of Unicode glyphs ------------------------------
    n = len(next(iter(dir_frames_grids.values())))
    cell_px, label_h, pad = 26, 16, 6
    sprite_w, sprite_h = GW * cell_px, GH * cell_px
    canvas_w = pad + GRID_COLS * (sprite_w + pad)
    canvas_h = pad + GRID_ROWS * (sprite_h + label_h + pad)
    glyph_font = ImageFont.truetype(um.UNIFONT, cell_px - 4)
    label_font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 12)
    gif_frames = []
    for fi in range(n):
        canvas = Image.new("RGB", (canvas_w, canvas_h), (8, 8, 8))
        draw = ImageDraw.Draw(canvas)
        for idx, d in enumerate(DISPLAY_DIRS):
            gc, gr = idx % GRID_COLS, idx // GRID_COLS
            x = pad + gc * (sprite_w + pad)
            y = pad + gr * (sprite_h + label_h + pad)
            draw.text((x, y), f"{d} f{fi}", fill=(150, 210, 150), font=label_font)
            for r, row in enumerate(dir_frames_grids[d][fi]):
                for c, ch in enumerate(row):
                    if ch != " ":
                        draw.text((x + c * cell_px, y + label_h + r * cell_px),
                                  ch, font=glyph_font, fill=(120, 230, 120))
        gif_frames.append(canvas)

    # 5. versioned Desktop + tracked output + frames.json -----------------------
    timestamp = datetime.now().strftime("v%Y%m%dT%H%M%S")
    name = f"{timestamp}__asciify-{args.mode}-{args.vocab}__walk_all_directions"
    TRACK_DIR.mkdir(parents=True, exist_ok=True)
    tracked_gif = TRACK_DIR / f"{name}.gif"
    desktop_gif = DESKTOP / f"{name}.gif"
    gif_frames[0].save(tracked_gif, save_all=True, append_images=gif_frames[1:],
                       duration=args.frame_ms, loop=0, disposal=2)
    desktop_gif.write_bytes(tracked_gif.read_bytes())
    (TRACK_DIR / f"{name}.frames.json").write_text(json.dumps({
        "kind": "asciify-skeleton", "timestamp": timestamp, "grid": [GW, GH],
        "palette_size": len(chars), "lambda_temporal": args.lambda_temporal,
        "spine_centered": True, "mirror_locked": False,
        "frames": dir_frames_grids,
    }, ensure_ascii=False, indent=1) + "\n")

    registry = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else []
    registry.append({
        "name": name, "kind": "asciify-skeleton", "timestamp": timestamp,
        "grid": [GW, GH], "palette_size": len(chars),
        "lambda_temporal": args.lambda_temporal, "mirror_locked": False,
        "tracked_gif": str(tracked_gif.relative_to(REPO)), "desktop_gif": str(desktop_gif),
        "gif_sha256": hashlib.sha256(tracked_gif.read_bytes()).hexdigest(),
    })
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=1) + "\n")
    print(f"\nwrote asciify @ {timestamp}\n  desktop : {desktop_gif}\n  tracked : {tracked_gif.relative_to(REPO)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_sk = sub.add_parser("skeleton", help="per-part colour-coded 8-direction stick-figure walk GIF")
    p_sk.add_argument("--upscale", type=int, default=3, help="per-part mask upscale before thinning")
    p_sk.add_argument("--frame-ms", type=int, default=90, help="per-frame duration (ms)")
    p_sk.add_argument("--note", default="step 1: per-part color-coded skeleton (identity retained)")
    p_sk.set_defaults(func=cmd_skeleton)

    p_unicode = sub.add_parser(
        "unicode-skeletal",
        help="reproduce the rejected single-cell stabilized-rig Unicode experiment",
    )
    p_unicode.add_argument(
        "--allow-rejected-rig-compiler",
        action="store_true",
        help="explicitly reproduce the rejected disconnected 5x5 experiment",
    )
    p_unicode.add_argument("--cell-px", type=int, default=28, help="pixels per glyph cell")
    p_unicode.add_argument("--frame-ms", type=int, default=90, help="per-frame duration (ms)")
    p_unicode.add_argument("--top-k", type=int, default=12, help="shape candidates per cell/frame")
    p_unicode.add_argument(
        "--lambda-temporal",
        type=float,
        default=0.45,
        help="cyclic glyph-change cost after motion compensation",
    )
    p_unicode.set_defaults(func=cmd_unicode_skeletal)

    p_perpart = sub.add_parser(
        "unicode-perpart-skeleton",
        help="reproduce the rejected per-part 5x7 cell compiler",
    )
    p_perpart.add_argument(
        "--allow-rejected-cell-compiler",
        action="store_true",
        help="explicitly reproduce the rejected 5x7 cell-first experiment",
    )
    p_perpart.add_argument("--cell-px", type=int, default=30, help="pixels per glyph cell")
    p_perpart.add_argument("--frame-ms", type=int, default=90, help="per-frame duration (ms)")
    p_perpart.add_argument("--top-k", type=int, default=16, help="shape candidates per cell/frame")
    p_perpart.add_argument("--lambda-temporal", type=float, default=2.4)
    p_perpart.add_argument("--lambda-spatial", type=float, default=1.3)
    p_perpart.add_argument("--rounds", type=int, default=6)
    p_perpart.set_defaults(func=cmd_unicode_perpart_skeleton)

    p_as = sub.add_parser(
        "asciify",
        help="legacy centroid-placement glyph experiment (explicit opt-in only)",
    )
    p_as.add_argument(
        "--allow-legacy-placement",
        action="store_true",
        help="run the preserved historical centroid-placement experiment",
    )
    p_as.add_argument("--mode", choices=["part-template", "per-cell"], default="part-template")
    p_as.add_argument("--vocab", choices=["authored", "full"], default="authored")
    p_as.add_argument(
        "--authored-file",
        default=str(Path.home() / "Downloads" / "sprite_framesdirty.txt"),
    )
    p_as.add_argument("--dict-bonus", type=float, default=0.35)
    p_as.add_argument("--grid-w", type=int, default=5)
    p_as.add_argument("--grid-h", type=int, default=5)
    p_as.add_argument("--top-k", type=int, default=8)
    p_as.add_argument("--lambda-spatial", type=float, default=2.0)
    p_as.add_argument("--lambda-orient", type=float, default=0.6)
    p_as.add_argument("--lambda-temporal", type=float, default=1.0)
    p_as.add_argument("--frame-ms", type=int, default=90)
    p_as.set_defaults(func=cmd_asciify)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
