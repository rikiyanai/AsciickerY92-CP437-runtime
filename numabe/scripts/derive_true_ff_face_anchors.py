#!/usr/bin/env python3
"""FL-4902 lane FA (map FL-4900): derive True FF face-feature anchors from a mesh.

Why: True FF draws a statue or a head as sparse text. The archive draws a face
as isolated authored marks (a lid+pupil per eye, a one-glyph nose, a short
mouth; shingu-butsugu-mingeihin-zou/res424, TRAIN). The runtime needs the
object-local position and normal of each feature, so the marks move rigidly
with the object. No renderer fact carries them (one mesh, one material; no
face bones), so this script measures them from the mesh the game loads.

Method (bounded; no coordinate is typed by hand; PARAMETERS scale with the
asset height, so every window is a fixed fraction of the mesh):
1. Rasterise an orthographic depth map of the mesh along each horizontal axis.
2. Head region: rows above the lowest dip of the frontal depth profile inside
   the middle band of the height (the chin undercut / neck).
3. Relief: depth minus its box mean (`relief_radius`), NaN-aware. Pixels near
   a depth discontinuity (silhouette, veil edge) are excluded.
4. Face: every pair of concave relief minima (eye sockets) whose line is within
   30 degrees of level and whose separation d is in `eye_separation`; the
   strongest convex peak below their midpoint near the midline is the nose
   tip (`nose_drop`, `midline`, `max_asymmetry` in units of d); the pair must
   be bilaterally symmetric (`mirror_error` of the relief across the
   midline). The mouth is the most concave fine-scale line (`fine_radius`,
   averaged along the eye axis) below the nose (`mouth_drop`); it never
   chooses the face and is left out when it is too shallow. The view whose
   best face scores highest is the front; a view without one yields nothing.
5. Refinement: the face normal is the mean surface normal of the visible
   faces in a disc around the eyes and nose; the depth map is rendered again
   along it and the features are detected again. The largest anchor shift
   between the two passes is recorded (`checks.pass_shift`).
6. Every anchor is the surface point under its depth pixel: mesh face index,
   barycentric weights, local position and that face's unit normal.
7. Drapery folds: concave valleys of a fold-scale relief (`fold_radius`) in
   the first front view, away from depth discontinuities and outside the face
   disc, skeletonised, traced into polylines, simplified and lifted onto the
   surface; the longest `max_folds` are kept.

If any step fails, the feature is left out and the reason is recorded.

Usage:
  python3 scripts/derive_true_ff_face_anchors.py pieta_statue \
      --output godot_project/assets/world_mesh/pieta_statue_features.v1.json
  python3 scripts/derive_true_ff_face_anchors.py xbot_head \
      --output godot_project/assets/actor_mesh/xbot_head_features.v1.json
Add --check to compare against the existing file instead of writing it.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage
from skimage.morphology import skeletonize

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "godot_project"
LANDMARKS = PROJECT / "assets/world_mesh/authored_landmarks.json"
XBOT_POSES = PROJECT / "assets/actor_mesh/xbot_rig_poses.json"
SCHEMA = "true_ff.feature_anchors.v1"
DECIMALS = 4

# Parameters as fractions of the asset height, so they scale with the mesh.
PARAMETERS = {
    "view_step": 1.0 / 300.0,        # depth-map pixel, detection passes
    "relief_radius": 1.0 / 30.0,     # box-mean radius of the relief map
    "edge_jump": 1.0 / 60.0,         # depth step between neighbours = discontinuity
    "edge_margin": 1.0 / 30.0,       # candidates keep this far from a discontinuity
    "peak_separation": 1.0 / 60.0,   # local extrema window radius
    "fine_radius": 1.0 / 85.0,       # box-mean radius of the mouth relief map
    "eye_separation": [1.0 / 60.0, 1.0 / 5.0],  # socket to socket
    # Proportions in units of the eye separation d (bounded search windows):
    "nose_drop": [0.4, 1.3],         # nose tip below the eye midpoint
    "mouth_drop": [0.5, 1.5],        # mouth below the nose tip
    "mouth_half_width": 0.2,         # the mouth line is averaged over +-this
    "midline": 0.2,                  # nose and mouth within this of the midline
    "normal_disc": 0.75,             # face normal = mean surface normal in this disc
    "min_relief": 1.0 / 300.0,               # |relief| an eye or nose must reach
    "mouth_min_relief": 1.0 / 1000.0,        # |fine line relief| the mouth must reach
    "max_asymmetry": 0.2,                    # |dL - dR| / mean(dL, dR), nose to eyes
    "max_mirror_error": 0.8,                 # mirror_error of an accepted face
    "fold_radius": 1.0 / 60.0,               # box-mean radius of the fold relief map
    "fold_relief": 1.0 / 375.0,              # valley depth of a drapery fold
    "fold_min_length": 1.0 / 15.0,
    "fold_simplify": 1.0 / 150.0,
    "fold_face_clearance": 2.0,              # face disc radius, x eye distance
    "max_folds": 12,
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_static_asset(asset: str):
    placements = json.loads(LANDMARKS.read_text())["placements"]
    mesh_rel = placements[asset]["mesh"]
    path = PROJECT / mesh_rel
    doc = json.loads(path.read_text())
    rows = np.asarray(doc["rows"], dtype=np.float64).reshape(-1, 4, 4)
    if rows.shape[0] != int(doc["face_count"]):
        raise ValueError("face_count does not match rows")
    # Corner 3 repeats corner 2: every face is a triangle (bake format).
    return mesh_rel, path, rows[:, :3, :3].copy()


def basis_for(view: np.ndarray):
    """Orthonormal (right, up, toward-viewer) for a view direction `view`
    (the direction from the object toward the viewer)."""
    toward = view / np.linalg.norm(view)
    up = np.array([0.0, 1.0, 0.0]) - toward * toward[1]
    if np.linalg.norm(up) < 1e-6:
        up = np.array([0.0, 0.0, -1.0]) - toward * toward[2]
    up /= np.linalg.norm(up)
    right = np.cross(up, toward)
    return right, up, toward


def depth_map(tris: np.ndarray, view: np.ndarray, step: float):
    """Orthographic z-buffer along `view`. Returns depth (larger = nearer the
    viewer, NaN = miss), the face index per pixel, and the pixel frame."""
    right, up, toward = basis_for(view)
    local = np.stack([tris @ right, tris @ up, tris @ toward], axis=-1)
    lo = local[..., :2].reshape(-1, 2).min(0)
    hi = local[..., :2].reshape(-1, 2).max(0)
    width = int(np.ceil((hi[0] - lo[0]) / step)) + 1
    height = int(np.ceil((hi[1] - lo[1]) / step)) + 1
    depth = np.full((height, width), -np.inf)
    face = np.full((height, width), -1, dtype=np.int64)
    px = (local[..., 0] - lo[0]) / step
    py = (local[..., 1] - lo[1]) / step
    for t in range(local.shape[0]):
        x, y, z = px[t], py[t], local[t, :, 2]
        x0, x1 = max(int(np.floor(x.min())), 0), min(int(np.ceil(x.max())), width - 1)
        y0, y1 = max(int(np.floor(y.min())), 0), min(int(np.ceil(y.max())), height - 1)
        if x1 < x0 or y1 < y0:
            continue
        den = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
        if abs(den) < 1e-12:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        w0 = ((y[1] - y[2]) * (gx - x[2]) + (x[2] - x[1]) * (gy - y[2])) / den
        w1 = ((y[2] - y[0]) * (gx - x[2]) + (x[0] - x[2]) * (gy - y[2])) / den
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
        d = w0 * z[0] + w1 * z[1] + w2 * z[2]
        window = depth[y0:y1 + 1, x0:x1 + 1]
        take = inside & (d > window)
        window[take] = d[take]
        face[y0:y1 + 1, x0:x1 + 1][take] = t
    depth[~np.isfinite(depth)] = np.nan
    return depth, face, {"right": right, "up": up, "toward": toward, "lo": lo, "step": step}


def box_mean(values: np.ndarray, radius_px: int) -> np.ndarray:
    valid = np.isfinite(values).astype(np.float64)
    filled = np.where(valid > 0, values, 0.0)
    size = 2 * radius_px + 1
    total = ndimage.uniform_filter(filled, size, mode="constant")
    count = ndimage.uniform_filter(valid, size, mode="constant")
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(count > 1e-9, total / count, np.nan)


def relief_maps(depth: np.ndarray, height: float, step: float):
    radius = max(1, int(round(PARAMETERS["relief_radius"] * height / step)))
    relief = depth - box_mean(depth, radius)
    # A discontinuity: a miss next to a hit, or a depth jump between neighbours.
    hit = np.isfinite(depth)
    filled = np.where(hit, depth, -1e9)
    jump = np.zeros_like(hit)
    for axis in (0, 1):
        for shift in (1, -1):
            other = np.roll(filled, shift, axis=axis)
            other_hit = np.roll(hit, shift, axis=axis)
            jump |= hit & (~other_hit | (np.abs(filled - other) > PARAMETERS["edge_jump"] * height))
    margin = max(1, int(round(PARAMETERS["edge_margin"] * height / step)))
    near_edge = ndimage.binary_dilation(jump, iterations=margin) | ~hit
    return relief, near_edge, jump


def extrema(values: np.ndarray, allowed: np.ndarray, radius_px: int, sign: float):
    """Pixels that are the strict extremum of `sign * values` in their window."""
    data = np.where(allowed & np.isfinite(values), sign * values, -np.inf)
    peak = ndimage.maximum_filter(data, size=2 * radius_px + 1, mode="constant", cval=-np.inf)
    ys, xs = np.nonzero((data == peak) & np.isfinite(data))
    order = np.lexsort((xs, ys, -data[ys, xs]))
    return [(int(ys[i]), int(xs[i]), float(data[ys[i], xs[i]])) for i in order]


def detect_face(depth, relief, fine, near_edge, near_fine, head_rows, height, step):
    """Best (nose, eye a, eye b, mouth) configuration in pixel coordinates,
    or None. Row index grows upward (row 0 = lowest).

    Eyes are the anchor of the search: two coarse concave sockets whose line
    is within 30 degrees of level. The nose is the strongest coarse convex
    peak below their midpoint, near the midline; the mouth is the deepest
    fine-scale concave pixel below the nose, near the midline."""
    rows = np.arange(depth.shape[0])[:, None]
    allowed = ~near_edge & (rows >= head_rows)
    radius = max(1, int(round(PARAMETERS["peak_separation"] * height / step)))
    floor = PARAMETERS["min_relief"] * height
    peaks = [p for p in extrema(relief, allowed, radius, 1.0) if p[2] >= floor]
    pits = [p for p in extrema(relief, allowed, radius, -1.0) if p[2] >= floor]
    separation = [v * height / step for v in PARAMETERS["eye_separation"]]
    fine_allowed = np.where(~near_fine & (rows >= head_rows) & np.isfinite(fine), fine, np.inf)
    ys, xs = np.mgrid[0:depth.shape[0], 0:depth.shape[1]]
    best = None
    for i, first in enumerate(pits):
        for second in pits[i + 1:]:
            a, b = (first, second) if first[1] <= second[1] else (second, first)
            axis = np.array([b[1] - a[1], b[0] - a[0]], dtype=np.float64)
            d = float(np.linalg.norm(axis))
            if not separation[0] <= d <= separation[1] or abs(axis[1]) > 0.5 * d:
                continue
            axis /= d
            down = np.array([axis[1], -axis[0]])
            mid = np.array([0.5 * (a[1] + b[1]), 0.5 * (a[0] + b[0])])
            noses = []
            for p in peaks:
                delta = np.array([p[1], p[0]]) - mid
                if (PARAMETERS["nose_drop"][0] * d <= float(delta @ down) <= PARAMETERS["nose_drop"][1] * d
                        and abs(float(delta @ axis)) <= PARAMETERS["midline"] * d):
                    noses.append(p)
            if not noses:
                continue
            nose = max(noses, key=lambda p: (p[2], -p[0], -p[1]))
            da = np.hypot(a[0] - nose[0], a[1] - nose[1])
            db = np.hypot(b[0] - nose[0], b[1] - nose[1])
            asymmetry = abs(da - db) / (0.5 * (da + db))
            if asymmetry > PARAMETERS["max_asymmetry"]:
                continue
            rel_x = xs - nose[1]
            rel_y = ys - nose[0]
            along = rel_x * down[0] + rel_y * down[1]
            across = rel_x * axis[0] + rel_y * axis[1]
            region = ((along >= PARAMETERS["mouth_drop"][0] * d) & (along <= PARAMETERS["mouth_drop"][1] * d)
                      & (np.abs(across) <= PARAMETERS["midline"] * d))
            error = mirror_error(relief, mid, axis, down, d)
            if error > PARAMETERS["max_mirror_error"]:
                continue
            my, mx, mouth_relief = mouth_line(fine_allowed, region, axis, PARAMETERS["mouth_half_width"] * d)
            mouth = (my, mx) if mouth_relief >= PARAMETERS["mouth_min_relief"] * height else None
            # The mouth does not choose the face: the eyes, nose and mirror do.
            score = (a[2] + b[2] + nose[2]) * (1.0 - error)
            candidate = (score, nose[:2], a[:2], b[:2], mouth, asymmetry, error)
            if best is None or candidate[0] > best[0]:
                best = candidate
    return best


def mirror_error(relief, mid, axis, down, d):
    """Bilateral symmetry of a candidate face: mean |relief(p) - relief(p')|
    over the disc of radius d centred 0.5 d below the eye midpoint, where p'
    mirrors p across the midline, divided by the mean |relief| there.
    0 is a perfect mirror; 1 is no better than unrelated surfaces."""
    height, width = relief.shape
    centre = mid + down * (0.5 * d)
    r = int(np.ceil(d))
    gy, gx = np.mgrid[-r:r + 1, -r:r + 1]
    keep = np.hypot(gx, gy) <= d
    px = centre[0] + gx[keep]
    py = centre[1] + gy[keep]
    across = (px - mid[0]) * axis[0] + (py - mid[1]) * axis[1]
    mx = px - 2.0 * across * axis[0]
    my = py - 2.0 * across * axis[1]
    ix, iy = np.rint(px).astype(np.int64), np.rint(py).astype(np.int64)
    jx, jy = np.rint(mx).astype(np.int64), np.rint(my).astype(np.int64)
    inside = ((ix >= 0) & (ix < width) & (iy >= 0) & (iy < height)
              & (jx >= 0) & (jx < width) & (jy >= 0) & (jy < height))
    if inside.sum() < 0.8 * len(ix):
        return 1.0
    u = relief[iy[inside], ix[inside]]
    v = relief[jy[inside], jx[inside]]
    both = np.isfinite(u) & np.isfinite(v)
    if both.sum() < 0.8 * len(u):
        return 1.0
    scale = float(np.mean(np.abs(np.concatenate([u[both], v[both]]))))
    return float(np.mean(np.abs(u[both] - v[both])) / max(scale, 1e-9))


def mouth_line(fine_allowed, region, axis, half_width):
    """The region pixel whose fine relief, averaged along the eye axis over
    +-half_width, is the most concave: a mouth is a line, a nostril a point.
    Returns (row, column, relief depth); depth is -inf when nothing is valid."""
    ys, xs = np.nonzero(region)
    if len(ys) == 0:
        return 0, 0, -np.inf
    offsets = np.linspace(-half_width, half_width, 9)
    height, width = fine_allowed.shape
    samples = []
    for t in offsets:
        sy = np.clip(np.rint(ys + t * axis[1]).astype(np.int64), 0, height - 1)
        sx = np.clip(np.rint(xs + t * axis[0]).astype(np.int64), 0, width - 1)
        samples.append(fine_allowed[sy, sx])
    samples = np.array(samples)
    valid = np.isfinite(samples)
    # A line needs most of its samples on the smooth surface.
    enough = valid.sum(0) >= int(np.ceil(0.75 * len(offsets)))
    mean = np.where(enough, np.where(valid, samples, 0.0).sum(0) / np.maximum(valid.sum(0), 1), np.inf)
    order = np.lexsort((xs, ys, mean))
    k = int(order[0])
    return int(ys[k]), int(xs[k]), (-float(mean[k]) if np.isfinite(mean[k]) else -np.inf)


def head_start_row(depth: np.ndarray) -> int:
    """Lowest dip of the frontal depth profile inside the middle band: the
    chin undercut / neck. Rows above it are the head region."""
    profile = np.array([np.nanmax(r) if np.isfinite(r).any() else np.nan for r in depth])
    n = len(profile)
    band = range(int(0.2 * n), int(0.7 * n))
    candidates = [i for i in band if np.isfinite(profile[i])]
    return min(candidates, key=lambda i: (profile[i], i))


def surface_point(tris, depth, face, frame, pixel):
    """Mesh face, barycentric weights and local point under a depth pixel."""
    y, x = pixel
    index = int(face[y, x])
    if index < 0:
        return None
    u = frame["lo"][0] + (x + 0.5) * frame["step"]
    v = frame["lo"][1] + (y + 0.5) * frame["step"]
    w = float(depth[y, x])
    point = frame["right"] * u + frame["up"] * v + frame["toward"] * w
    a, b, c = tris[index]
    normal = np.cross(b - a, c - a)
    area2 = float(np.linalg.norm(normal))
    normal /= area2
    wa = float(np.dot(np.cross(c - b, point - b), normal)) / area2
    wb = float(np.dot(np.cross(a - c, point - c), normal)) / area2
    wc = 1.0 - wa - wb
    on_surface = a * wa + b * wb + c * wc
    if np.dot(normal, frame["toward"]) < 0:
        normal = -normal
    return {"face_index": index, "barycentric": [wa, wb, wc], "position": on_surface, "normal": normal}


def region_extent(relief, near_edge, pixel, axis_xy, step, sign):
    """World extent, along `axis_xy`, of the connected relief region of one
    sign that holds `pixel` (at half its relief)."""
    y, x = pixel
    level = relief[y, x] * 0.5
    mask = (sign * relief >= sign * level) & ~near_edge
    labels, _ = ndimage.label(mask)
    ys, xs = np.nonzero(labels == labels[y, x])
    along = xs * axis_xy[0] + ys * axis_xy[1]
    return float(along.max() - along.min() + 1) * step


def face_pass(tris, view, height, step):
    depth, face, frame = depth_map(tris, view, step)
    relief, near_edge, jump = relief_maps(depth, height, step)
    fine_px = max(1, int(round(PARAMETERS["fine_radius"] * height / step)))
    fine = depth - box_mean(depth, fine_px)
    near_fine = ndimage.binary_dilation(jump, iterations=fine_px) | ~np.isfinite(depth)
    head = head_start_row(depth)
    found = detect_face(depth, relief, fine, near_edge, near_fine, head, height, step)
    return found, depth, face, frame, relief, near_edge


def face_normal(tris, face, frame, pixels, radius_px):
    """Mean unit surface normal over the visible faces within `radius_px` of
    the landmark centroid (each pixel votes for the face it shows)."""
    cy = float(np.mean([p[0] for p in pixels]))
    cx = float(np.mean([p[1] for p in pixels]))
    ys, xs = np.mgrid[0:face.shape[0], 0:face.shape[1]]
    ids = face[(np.hypot(ys - cy, xs - cx) <= radius_px) & (face >= 0)]
    a, b, c = tris[ids, 0], tris[ids, 1], tris[ids, 2]
    n = np.cross(b - a, c - a)
    n /= np.linalg.norm(n, axis=1, keepdims=True)
    n *= np.sign(n @ frame["toward"])[:, None]
    mean = n.sum(0)
    return mean / np.linalg.norm(mean)


def derive_face(tris, height):
    step = PARAMETERS["view_step"] * height
    views = {"+z": np.array([0.0, 0.0, 1.0]), "-z": np.array([0.0, 0.0, -1.0]),
             "+x": np.array([1.0, 0.0, 0.0]), "-x": np.array([-1.0, 0.0, 0.0])}
    scored = []
    for name, view in views.items():
        found = face_pass(tris, view, height, step)[0]
        scored.append((found[0] if found else -np.inf, name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    if not np.isfinite(scored[0][0]):
        return None, {"front_view": None, "reason": "no view has two sockets above a peak above a pit"}
    front = scored[0][1]
    passes = []
    view = views[front]
    for _ in range(2):
        found, depth, face, frame, relief, near_edge = face_pass(tris, view, height, step)
        if found is None:
            return None, {"front_view": front, "reason": "the view along the face normal lost the configuration"}
        _, nose_px, a_px, b_px, mouth_px, asymmetry, mirror = found
        pixels = {"nose": nose_px, "eye_a": a_px, "eye_b": b_px}
        if mouth_px is not None:
            pixels["mouth"] = mouth_px
        points = {}
        for key, pixel in pixels.items():
            hit = surface_point(tris, depth, face, frame, pixel)
            if hit is None:
                return None, {"front_view": front, "reason": f"{key} pixel has no surface"}
            points[key] = hit
        separation = float(np.hypot(b_px[0] - a_px[0], b_px[1] - a_px[1]))
        normal = face_normal(tris, face, frame, (nose_px, a_px, b_px),
                             PARAMETERS["normal_disc"] * separation)
        passes.append({"points": points, "normal": normal, "frame": frame, "relief": relief,
                       "near_edge": near_edge, "pixels": pixels, "asymmetry": asymmetry,
                       "mirror_error": mirror, "view": view})
        view = normal
    final = passes[-1]
    shift = max(float(np.linalg.norm(final["points"][k]["position"] - passes[0]["points"][k]["position"]))
                for k in ("nose", "eye_a", "eye_b"))
    return final, {"front_view": front, "view_scores": {n: (round(float(s), DECIMALS) if np.isfinite(s) else None)
                                                         for s, n in scored},
                   "pass_shift": shift, "pass_one": passes[0]}


def trace_polylines(skeleton: np.ndarray):
    """Ordered pixel chains of a one-pixel skeleton, split at junctions."""
    neighbours = ndimage.convolve(skeleton.astype(np.int64), np.ones((3, 3), dtype=np.int64),
                                  mode="constant") - 1
    neighbours[~skeleton] = 0
    junction = skeleton & (neighbours > 2)
    body = skeleton & ~junction
    labels, count = ndimage.label(body, structure=np.ones((3, 3)))
    chains = []
    for label in range(1, count + 1):
        ys, xs = np.nonzero(labels == label)
        pixels = set(zip(ys.tolist(), xs.tolist()))
        degree = {p: sum((p[0] + dy, p[1] + dx) in pixels for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                         if dy or dx) for p in pixels}
        ends = sorted(p for p, d in degree.items() if d <= 1)
        start = ends[0] if ends else min(pixels)
        chain, seen = [start], {start}
        while True:
            y, x = chain[-1]
            nxt = sorted((y + dy, x + dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                         if (dy or dx) and (y + dy, x + dx) in pixels and (y + dy, x + dx) not in seen)
            if not nxt:
                break
            chain.append(nxt[0])
            seen.add(nxt[0])
        chains.append(chain)
    return chains


def simplify(points: np.ndarray, tolerance: float) -> list:
    """Ramer-Douglas-Peucker on a 2D chain; returns kept indices."""
    def rdp(lo, hi):
        if hi <= lo + 1:
            return [lo, hi]
        a, b = points[lo], points[hi]
        ab = b - a
        norm = np.linalg.norm(ab)
        seg = points[lo + 1:hi] - a
        if norm < 1e-12:
            dist = np.linalg.norm(seg, axis=1)
        else:
            dist = np.abs(ab[0] * seg[:, 1] - ab[1] * seg[:, 0]) / norm
        k = int(np.argmax(dist))
        if dist[k] <= tolerance:
            return [lo, hi]
        mid = lo + 1 + k
        return rdp(lo, mid)[:-1] + rdp(mid, hi)
    return rdp(0, len(points) - 1)


def fold_polylines(tris, depth, face, frame, jump, height, face_centre, face_radius):
    """Concave drapery valleys at fold scale: relief over `fold_radius`, away
    from depth discontinuities by the same radius (a discontinuity is an
    outline, drawn by the contour path, not a fold)."""
    step = frame["step"]
    radius = max(1, int(round(PARAMETERS["fold_radius"] * height / step)))
    relief = depth - box_mean(depth, radius)
    near_edge = ndimage.binary_dilation(jump, iterations=radius) | ~np.isfinite(depth)
    valley = (-relief >= PARAMETERS["fold_relief"] * height) & ~near_edge
    # Exclude the face disc: its pits are the features, not drapery.
    ys, xs = np.mgrid[0:depth.shape[0], 0:depth.shape[1]]
    u = frame["lo"][0] + (xs + 0.5) * step
    v = frame["lo"][1] + (ys + 0.5) * step
    cu = float(face_centre @ frame["right"])
    cv = float(face_centre @ frame["up"])
    valley &= np.hypot(u - cu, v - cv) > face_radius
    skeleton = skeletonize(valley)
    folds = []
    for chain in trace_polylines(skeleton):
        pts = np.array([[x, y] for y, x in chain], dtype=np.float64)
        length = float(np.sum(np.linalg.norm(np.diff(pts, axis=0), axis=1))) * step if len(pts) > 1 else 0.0
        if length < PARAMETERS["fold_min_length"] * height:
            continue
        keep = simplify(pts, PARAMETERS["fold_simplify"] * height / step)
        lifted, normals = [], []
        for index in keep:
            hit = surface_point(tris, depth, face, frame, chain[index])
            if hit is None:
                break
            lifted.append(hit)
        else:
            normal = np.sum([h["normal"] for h in lifted], axis=0)
            normal /= np.linalg.norm(normal)
            depth_mean = float(np.mean([-relief[chain[i]] for i in range(len(chain))]))
            folds.append({"length": length, "points": lifted, "normal": normal, "valley": depth_mean})
    folds.sort(key=lambda f: (-f["length"], f["points"][0]["face_index"]))
    return folds[:PARAMETERS["max_folds"]]


def r(values):
    return [round(float(v), DECIMALS) for v in values]


def anchor_record(kind, pair, side, hit, size):
    return {"kind": kind, "pair": pair, "side": side, "position": r(hit["position"]),
            "normal": r(hit["normal"]), "size": round(float(size), DECIMALS),
            "face_index": hit["face_index"], "barycentric": r(hit["barycentric"])}


def derive_static(asset: str) -> dict:
    mesh_rel, mesh_path, tris = load_static_asset(asset)
    points = tris.reshape(-1, 3)
    height = float(points[:, 1].max() - points[:, 1].min())
    result, info = derive_face(tris, height)
    out = {"schema": SCHEMA, "asset": asset, "mesh": mesh_rel, "mesh_sha256": sha256(mesh_path),
           "face_count": int(tris.shape[0]), "space": "object_local",
           "generated_by": "scripts/derive_true_ff_face_anchors.py",
           "parameters": {"asset_height": round(height, DECIMALS), **PARAMETERS},
           "face": None, "anchors": [], "folds": [], "omitted": []}
    if result is None:
        out["omitted"].append({"features": ["eye", "nose", "mouth", "drapery_fold"], "reason": info["reason"]})
        return out
    p = result["points"]
    frame = result["frame"]
    eye_a, eye_b = p["eye_a"]["position"], p["eye_b"]["position"]
    nose = p["nose"]["position"]
    normal = result["normal"]
    mid = 0.5 * (eye_a + eye_b)
    # Face up: from the nose tip to the eye midpoint, across the normal.
    up = mid - nose
    up -= normal * float(up @ normal)
    up /= np.linalg.norm(up)
    right = np.cross(up, normal)
    # Viewer-left eye: the smaller coordinate along the face's right axis.
    left_key, right_key = ("eye_a", "eye_b") if float(eye_a @ right) < float(eye_b @ right) else ("eye_b", "eye_a")
    pixels = result["pixels"]
    eye_axis_px = np.array([pixels["eye_b"][1] - pixels["eye_a"][1], pixels["eye_b"][0] - pixels["eye_a"][0]],
                           dtype=np.float64)
    eye_axis_px /= np.linalg.norm(eye_axis_px)
    down_px = np.array([eye_axis_px[1], -eye_axis_px[0]])
    sizes = {key: region_extent(result["relief"], result["near_edge"], pixels[key],
                                down_px if key == "nose" else eye_axis_px, frame["step"],
                                1.0 if key == "nose" else -1.0)
             for key in ("nose", "eye_a", "eye_b")}
    eye_distance = float(np.linalg.norm(eye_b - eye_a))
    rel = {k: np.array([float((p[k]["position"] - nose) @ right), float((p[k]["position"] - nose) @ up)])
           for k in p}
    features = [eye_a, eye_b, nose]
    out["anchors"] = [
        anchor_record("eye", "eyes", "viewer_left", p[left_key], sizes[left_key]),
        anchor_record("eye", "eyes", "viewer_right", p[right_key], sizes[right_key]),
        anchor_record("nose", "", "centre", p["nose"], sizes["nose"]),
    ]
    if "mouth" in p:
        # The mouth line was found on the fine relief map; its size is the
        # averaged line length the search used.
        out["anchors"].append(anchor_record("mouth", "", "centre", p["mouth"],
                                            2.0 * PARAMETERS["mouth_half_width"] * eye_distance))
        features.append(p["mouth"]["position"])
    else:
        out["omitted"].append({"features": ["mouth"],
                               "reason": "no fine concave line below the nose reached mouth_min_relief"})
    centre = np.mean(features, axis=0)
    out["face"] = {
        "front_view": info["front_view"], "view_scores": info["view_scores"],
        "centre": r(centre), "normal": r(normal), "up": r(up), "right": r(right),
        "eye_distance": round(eye_distance, DECIMALS)}
    face_radius = PARAMETERS["fold_face_clearance"] * eye_distance
    first = info["pass_one"]
    folds = fold_polylines(tris, *_frontal_maps(tris, first["view"], height), height, centre, face_radius)
    out["folds"] = [{"id": i, "kind": "valley", "length": round(f["length"], DECIMALS),
                     "normal": r(f["normal"]), "valley_depth": round(f["valley"], DECIMALS),
                     "points": [{"position": r(h["position"]), "face_index": h["face_index"],
                                 "barycentric": r(h["barycentric"])} for h in f["points"]]}
                    for i, f in enumerate(folds)]
    if not folds:
        out["omitted"].append({"features": ["drapery_fold"],
                               "reason": "no valley reached fold_relief and fold_min_length"})
    order = min(rel[left_key][1], rel[right_key][1]) > 0 and ("mouth" not in rel or rel["mouth"][1] < 0)
    out["checks"] = {
        "mouth_midline_offset": (round(float(abs(rel["mouth"][0]) / eye_distance), DECIMALS)
                                 if "mouth" in rel else None),
        "eye_height_difference": round(float(abs(rel[left_key][1] - rel[right_key][1]) / eye_distance), DECIMALS),
        "nose_distance_asymmetry": round(float(result["asymmetry"]), DECIMALS),
        "mirror_error": round(float(result["mirror_error"]), DECIMALS),
        "order_along_up": "eyes>nose>mouth" if order else "violated",
        "pass_shift": round(float(info["pass_shift"]), DECIMALS),
        "pass_shift_over_eye_distance": round(float(info["pass_shift"]) / eye_distance, DECIMALS),
    }
    return out


def _frontal_maps(tris, view, height):
    step = PARAMETERS["view_step"] * height
    depth, face, frame = depth_map(tris, view, step)
    _, _, jump = relief_maps(depth, height, step)
    return depth, face, frame, jump


def derive_xbot_head() -> dict:
    """The player head: the mixamorig:Head part of the 52-part octahedron rig
    (FL-4907), posed by idle frame 0's bone transform. Measured, not located:
    the X Bot head has no facial geometry, so every feature is left out with
    the evidence."""
    doc = json.loads(XBOT_POSES.read_text())
    bone = doc["part_identity"]["bones"].index("mixamorig:Head")
    pose = doc["poses"][0]
    blob = PROJECT / doc["mesh_resource"].replace("res://", "")
    mesh = json.loads(blob.read_text())
    rows = np.array(mesh["face_rows"], dtype=np.float32).astype(np.float64).reshape(-1, 16)
    part = np.array(mesh["part_indices"], dtype=np.int64)
    local = rows[part == bone][:, [0, 1, 2, 4, 5, 6, 8, 9, 10]].reshape(-1, 3, 3)
    transforms = PROJECT / doc["transform_resource"].replace("res://", "")
    matrix = np.fromfile(transforms, dtype="<f4", count=12,
                         offset=(pose["transform_offset"] + bone * 12) * 4).reshape(3, 4).astype(np.float64)
    tris = local @ matrix[:, :3].T + matrix[:, 3]
    vertices = np.unique(tris.reshape(-1, 3).round(6), axis=0)
    volume = float(np.sum(np.einsum("ij,ij->i", tris[:, 0], np.cross(tris[:, 1], tris[:, 2]))) / 6.0)
    from scipy.spatial import ConvexHull
    hull = ConvexHull(vertices)
    convexity = abs(volume) / hull.volume
    return {"schema": SCHEMA, "asset": "xbot_head", "mesh": str(blob.relative_to(PROJECT)),
            "mesh_sha256": sha256(blob), "pose": pose["pose_id"],
            "space": "head_part_rows_posed_actor_space", "bone": "mixamorig:Head", "bone_index": bone,
            "generated_by": "scripts/derive_true_ff_face_anchors.py",
            "head": {"faces": int(tris.shape[0]), "vertices": int(len(vertices)),
                     "volume_over_convex_hull": round(convexity, DECIMALS),
                     "aabb_min": r(vertices.min(0)), "aabb_max": r(vertices.max(0))},
            "face": None, "anchors": [], "folds": [],
            "omitted": [{"features": ["eye", "nose", "mouth"],
                         "reason": ("the head is %d triangles over %d vertices and %.1f%% of its convex hull: "
                                    "no concave socket, ridge or mouth line exists to locate"
                                    % (tris.shape[0], len(vertices), 100.0 * convexity))},
                        {"features": ["drapery_fold"], "reason": "the X Bot wears no drapery"}]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("asset", choices=["pieta_statue", "xbot_head"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    data = derive_xbot_head() if args.asset == "xbot_head" else derive_static(args.asset)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        same = args.output.exists() and args.output.read_text() == text
        print("up to date" if same else "stale: " + str(args.output))
        return 0 if same else 1
    args.output.write_text(text)
    print(f"wrote {args.output} anchors={len(data['anchors'])} folds={len(data['folds'])} omitted={len(data['omitted'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
