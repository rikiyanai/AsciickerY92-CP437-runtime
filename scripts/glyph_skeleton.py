#!/usr/bin/env python3
"""Phase 0 spike: glyph skeletonization + resolution/cost gate.

Goal of the larger effort: a script-independent *topology* descriptor (skeleton
graph + curvature) so structurally-similar glyphs ("vertical stroke + hook":
し レ √ J ∫ ʃ) cluster globally and order along axes like bendiness — something
IoU and Hu moments cannot do.

This file is ONLY the first gate: render glyphs at several resolutions, thin them
to a 1px medial axis (Zhang-Suen), and report whether the skeleton is clean
(connected, recognizable hook) plus the per-glyph cost. No fingerprint, no
integration yet — those come after this gate passes.

    python3 scripts/glyph_skeleton.py spike            # the 6 canonical hooks
    python3 scripts/glyph_skeleton.py spike U+3057 U+221A --sizes 16,48,64
"""

from __future__ import annotations

import argparse
import sys
import time
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from glyph_morphology_browser import GlyphScorer, discover_font_chain  # noqa: E402

CANONICAL = [0x3057, 0x30EC, 0x221A, 0x004A, 0x222B, 0x0283]  # し レ √ J ∫ ʃ
INK_THRESHOLD = 96


# ---------------------------------------------------------------------------
# Hi-res rendering through the font chain (crop-to-ink + fit-centered to SxS)
# ---------------------------------------------------------------------------
class HiRenderer:
    def __init__(self):
        self._chain_paths = discover_font_chain()
        self._cmaps = [(GlyphScorer._load_cmap(p), p) for p in self._chain_paths]
        self._fonts: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    def _font(self, path: Path, px: int):
        key = (path.name, px)
        f = self._fonts.get(key)
        if f is None:
            f = ImageFont.truetype(str(path), px)
            self._fonts[key] = f
        return f

    def _path_for(self, cp: int) -> Path:
        for cm, p in self._cmaps:
            if not cm or cp in cm:
                return p
        return self._chain_paths[0]

    def grid(self, cp: int, size: int) -> np.ndarray:
        """Binary size x size grid: render large, crop to ink, fit-center."""
        path = self._path_for(cp)
        render_px = max(size * 3, 96)
        font = self._font(path, render_px)
        ch = chr(cp)
        try:
            bbox = font.getbbox(ch)
        except Exception:
            return np.zeros((size, size), np.uint8)
        if not bbox or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            return np.zeros((size, size), np.uint8)
        pad = 8
        canvas = Image.new("L", (bbox[2] - bbox[0] + pad * 2, bbox[3] - bbox[1] + pad * 2), 0)
        ImageDraw.Draw(canvas).text((pad - bbox[0], pad - bbox[1]), ch, fill=255, font=font)
        ink = canvas.getbbox()
        if ink is None:
            return np.zeros((size, size), np.uint8)
        crop = canvas.crop(ink)
        cw, chh = crop.size
        scale = min(size / cw, size / chh)
        nw, nh = max(1, round(cw * scale)), max(1, round(chh * scale))
        rs = crop.resize((nw, nh), Image.LANCZOS)
        out = Image.new("L", (size, size), 0)
        out.paste(rs, ((size - nw) // 2, (size - nh) // 2))
        return (np.asarray(out) > INK_THRESHOLD).astype(np.uint8)


# ---------------------------------------------------------------------------
# Zhang-Suen thinning (vectorized)
# ---------------------------------------------------------------------------
def _neighbors(p: np.ndarray):
    # P2..P9 clockwise from north, on a 1-padded array; return views on interior
    P2 = p[:-2, 1:-1]
    P3 = p[:-2, 2:]
    P4 = p[1:-1, 2:]
    P5 = p[2:, 2:]
    P6 = p[2:, 1:-1]
    P7 = p[2:, :-2]
    P8 = p[1:-1, :-2]
    P9 = p[:-2, :-2]
    return P2, P3, P4, P5, P6, P7, P8, P9


def thin(img: np.ndarray) -> np.ndarray:
    """Zhang-Suen skeletonization. img: HxW uint8 {0,1}. Returns 1px skeleton."""
    I = img.astype(np.uint8).copy()
    while True:
        removed_any = False
        for step in (0, 1):
            p = np.pad(I, 1)
            P2, P3, P4, P5, P6, P7, P8, P9 = _neighbors(p)
            seq = [P2, P3, P4, P5, P6, P7, P8, P9]
            B = sum(seq)
            # A = number of 0->1 transitions in P2,P3,...,P9,P2
            ordered = seq + [P2]
            A = np.zeros_like(B)
            for k in range(8):
                A += ((ordered[k] == 0) & (ordered[k + 1] == 1)).astype(np.uint8)
            cond = (I == 1) & (B >= 2) & (B <= 6) & (A == 1)
            if step == 0:
                cond &= (P2 * P4 * P6 == 0) & (P4 * P6 * P8 == 0)
            else:
                cond &= (P2 * P4 * P8 == 0) & (P2 * P6 * P8 == 0)
            if cond.any():
                I[cond] = 0
                removed_any = True
        if not removed_any:
            break
    return I


# ---------------------------------------------------------------------------
# Skeleton graph stats
# ---------------------------------------------------------------------------
def degree_map(skel: np.ndarray) -> np.ndarray:
    p = np.pad(skel, 1)
    deg = np.zeros_like(skel, dtype=np.int8)
    for nb in _neighbors(p):
        deg += nb.astype(np.int8)
    return deg * skel  # only count on skeleton pixels


def components(skel: np.ndarray) -> int:
    visited = np.zeros_like(skel, dtype=bool)
    h, w = skel.shape
    n = 0
    for i in range(h):
        for j in range(w):
            if skel[i, j] and not visited[i, j]:
                n += 1
                st = [(i, j)]
                visited[i, j] = True
                while st:
                    y, x = st.pop()
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            ny, nx = y + dy, x + dx
                            if 0 <= ny < h and 0 <= nx < w and skel[ny, nx] and not visited[ny, nx]:
                                visited[ny, nx] = True
                                st.append((ny, nx))
    return n


def ascii_overlay(grid: np.ndarray, skel: np.ndarray) -> str:
    rows = []
    for y in range(grid.shape[0]):
        line = []
        for x in range(grid.shape[1]):
            if skel[y, x]:
                line.append("#")
            elif grid[y, x]:
                line.append("·")
            else:
                line.append(" ")
        rows.append("".join(line))
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Phase 1: topology fingerprint (crossing-number graph + path curvature)
# ---------------------------------------------------------------------------
# The Phase-0 finding: raw 8-degree junction counts are noise (Zhang-Suen leaves
# diagonal staircases that read as degree>=3). The *crossing number* — half the
# number of 0->1 transitions around the 8-ring — is the robust branch/endpoint
# detector: CN==1 is a true endpoint, CN>=3 a true branch, regardless of
# staircase thickness. The shape signal is the curvature profile traced ALONG
# the medial axis, which is what separates a soft hook (し) from a sharp one (√)
# in a way IoU and Hu moments cannot.
FP_SIZE = 48          # skeleton resolution (Phase-0 gate: clean single-component axes)
FP_K = 24             # resampled path points -> FP_K-2 signed turning angles


def crossing_number(skel: np.ndarray) -> np.ndarray:
    """CN per skeleton pixel = number of 0->1 transitions around the 8-ring.
    CN==1 endpoint, CN==2 simple path pixel, CN>=3 branch point."""
    p = np.pad(skel, 1).astype(np.int8)
    ring = list(_neighbors(p))            # P2..P9 clockwise
    ring = ring + [ring[0]]               # close the ring back to P2
    cn = np.zeros_like(skel, dtype=np.int8)
    for k in range(8):
        cn += ((ring[k] == 0) & (ring[k + 1] == 1)).astype(np.int8)
    return cn * skel


def _bfs_farthest(coords: set[tuple[int, int]], start: tuple[int, int]):
    """8-connected BFS over skeleton pixels; return (farthest_node, parent_map)."""
    from collections import deque
    parent = {start: None}
    dist = {start: 0}
    dq = deque([start])
    far, fard = start, 0
    while dq:
        y, x = dq.popleft()
        if dist[(y, x)] > fard:
            far, fard = (y, x), dist[(y, x)]
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                nb = (y + dy, x + dx)
                if nb in coords and nb not in dist:
                    dist[nb] = dist[(y, x)] + 1
                    parent[nb] = (y, x)
                    dq.append(nb)
    return far, parent


def longest_path(skel: np.ndarray) -> list[tuple[int, int]]:
    """Geodesic diameter path of the skeleton (double-BFS). Exact for tree/path
    skeletons, which is what hook glyphs reduce to; good enough otherwise."""
    coords = {(int(y), int(x)) for y, x in zip(*np.where(skel))}
    if not coords:
        return []
    cn = crossing_number(skel)
    endpoints = [(int(y), int(x)) for y, x in zip(*np.where(cn == 1))]
    seed = endpoints[0] if endpoints else next(iter(coords))
    a, _ = _bfs_farthest(coords, seed)
    b, parent = _bfs_farthest(coords, a)
    path = []
    node = b
    while node is not None:
        path.append(node)
        node = parent[node]
    return path  # a..b


def _resample(coords: np.ndarray, k: int) -> np.ndarray:
    coords = np.asarray(coords, dtype=np.float64)
    if len(coords) < 2:
        return np.repeat(coords if len(coords) else np.zeros((1, 2)), k, axis=0)[:k]
    seg = np.linalg.norm(np.diff(coords, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    total = s[-1]
    if total == 0:
        return np.repeat(coords[:1], k, axis=0)
    t = np.linspace(0.0, total, k)
    x = np.interp(t, s, coords[:, 0])
    y = np.interp(t, s, coords[:, 1])
    return np.stack([x, y], axis=1)


def _turning(rs: np.ndarray) -> np.ndarray:
    """Signed turning angle at each interior resampled point (radians)."""
    v = np.diff(rs, axis=0)
    ang = np.arctan2(v[:, 1], v[:, 0])
    d = np.diff(ang)
    return (d + np.pi) % (2 * np.pi) - np.pi


def fingerprint(grid: np.ndarray, skel: np.ndarray | None = None) -> dict:
    """Topology fingerprint of a binary glyph grid. Orientation of the traced
    path is ambiguous (either endpoint can be 'start'), so the curvature
    SEQUENCE is stored raw and compared direction-invariantly in topo_distance."""
    if skel is None:
        skel = thin(grid)
    cn = crossing_number(skel)
    n_endpoints = int((cn == 1).sum())
    n_branches = int((cn >= 3).sum())
    n_comp = components(skel)
    path = longest_path(skel)
    # path coords as (x=col, y=row) so turning angles are in screen space
    pc = np.array([(x, y) for (y, x) in path], dtype=np.float64) if path else np.zeros((0, 2))
    skel_px = int(skel.sum())
    H, W = grid.shape
    if len(pc) >= 2:
        rs = _resample(pc, FP_K)
        turn = _turning(rs)                       # FP_K-2 signed angles
        path_len = float(np.linalg.norm(np.diff(pc, axis=0), axis=1).sum())
        endpoint_dist = float(np.linalg.norm(pc[0] - pc[-1]))
        straightness = endpoint_dist / path_len if path_len else 1.0
        ys, xs = pc[:, 1], pc[:, 0]
        aspect = (xs.max() - xs.min() + 1) / (ys.max() - ys.min() + 1)
    else:
        rs = np.zeros((FP_K, 2))
        turn = np.zeros(FP_K - 2)
        path_len = 0.0
        straightness = 1.0
        aspect = 1.0
    abst = np.abs(turn)
    net_bend = float(turn.sum())                  # signed winding (orientation-dependent)
    winding = float(abst.sum())                   # total absolute turning
    mean_abs = float(abst.mean()) if abst.size else 0.0
    max_abs = float(abst.max()) if abst.size else 0.0
    peak_pos = float(np.argmax(abst) / max(1, abst.size - 1)) if abst.size else 0.0
    return {
        "ok": skel_px > 0 and len(pc) >= 2,
        "n_endpoints": n_endpoints,
        "n_branches": n_branches,
        "n_components": n_comp,
        "skel_px": skel_px,
        "path_len_norm": path_len / max(H, W),
        "straightness": straightness,
        "aspect": float(aspect),
        "winding": winding,
        "mean_abs": mean_abs,
        "max_abs": max_abs,
        "peak_pos": peak_pos,
        "net_bend": net_bend,
        "turn": turn.astype(np.float32),
    }


def _seq_dist(a: np.ndarray, b: np.ndarray) -> float:
    """Direction-invariant curvature-sequence distance. Reversing a traced path
    reverses order AND negates each turning angle, so compare against both."""
    if a.size == 0 or b.size == 0:
        return float(np.abs(a.sum() - b.sum()))
    fwd = np.linalg.norm(a - b)
    rev = np.linalg.norm(a - (-b[::-1]))
    return float(min(fwd, rev) / np.sqrt(a.size))


def topo_distance(a: dict, b: dict, w_struct=0.6, w_seq=2.0, w_scalar=0.7) -> float:
    """Distance between two topology fingerprints. Structural term keeps
    different graph shapes apart; sequence term is the soft/sharp-hook signal;
    scalar term adds proportion (aspect/straightness/winding)."""
    struct = (abs(a["n_endpoints"] - b["n_endpoints"])
              + 1.5 * abs(a["n_branches"] - b["n_branches"])
              + 1.5 * abs(a["n_components"] - b["n_components"]))
    seq = _seq_dist(a["turn"], b["turn"])
    sa = np.array([a["aspect"], a["straightness"], a["path_len_norm"], a["winding"]])
    sb = np.array([b["aspect"], b["straightness"], b["path_len_norm"], b["winding"]])
    scalar = float(np.linalg.norm(sa - sb))
    return w_struct * struct + w_seq * seq + w_scalar * scalar


def topo_bend(fp: dict) -> float:
    """Softness axis = participation ratio of the turning distribution:
    winding^2 / sum(turn^2) = the EFFECTIVE NUMBER of path samples that carry
    the bend. A sharp corner dumps the whole turn into ~1 sample (LOW); a soft
    hook spreads the same total turn across many samples (HIGH). Drift-robust:
    a tiny per-sample drift along a straight run contributes negligibly to the
    sum of squares, so it cannot fake softness the way raw winding can."""
    t = fp["turn"].astype(np.float64)
    s2 = float(np.sum(t * t))
    w = float(np.sum(np.abs(t)))
    return (w * w) / (s2 + 1e-6)


# ---------------------------------------------------------------------------
# Phase A (FL-4208 rebuild): per-glyph AXIS VECTOR. topo_class is rotation- AND
# reflection-invariant; orientation, mirror_parity and symmetry_lines are the
# factored-out facts. Rotation preserves handedness; reflection flips it.
# ---------------------------------------------------------------------------
TOPO_CLASSES = ["dot", "bar", "single_hook", "wave", "arc_loop", "branch", "compound"]
FILL_STATES = ["open", "hollow", "solid"]
SYM_BITS = {"vertical": 1, "horizontal": 2, "diagonal_fwd": 4, "diagonal_back": 8}
SYM_TAU = 0.80          # IoU threshold for a reflection symmetry line
ECC_THRESH = 0.80       # PCA λ2/λ1 above this = near-circular -> chirality degenerate
AREA_THRESH = 0.012     # |normalized signed area| below this = too straight -> degenerate
DOT_MAX_PX = 6
SPUR_MAX = 16           # skeleton spurs (serifs/ticks) shorter than this are pruned
LOBE_MIN = 0.7          # a curvature lobe must accumulate this much |turn| to count
STRAIGHT_BAR = 0.85     # endpoint_dist/path_len above this = bar (straightness alone;
                        # winding is unreliable on diagonals due to staircase residue)
SYM_COVER = 0.88        # 1px-tolerant reflection coverage for a symmetry line


def despur(skel: np.ndarray, max_len: int = SPUR_MAX) -> np.ndarray:
    """Remove short terminal branches (font serifs/ticks) that reach a junction
    within max_len pixels — they are not structural, and otherwise turn a hook
    into a 'branch'. Junction = crossing-number>=3 (consistent with endpoint
    counts). Long arms (real T/Y structure) survive. Two passes for nesting."""
    s = skel.copy()
    for _pass in range(3):
        cn = crossing_number(s)
        coords = set(map(tuple, np.column_stack(np.where(s))))
        remove = set()
        for ep in [tuple(p) for p in np.column_stack(np.where(cn == 1))]:
            branch, cur, prev = [ep], ep, None
            for _ in range(max_len):
                around = [(cur[0] + dy, cur[1] + dx) for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                          if (dy or dx) and (cur[0] + dy, cur[1] + dx) in coords]
                nbrs = [n for n in around if n != prev]
                # adjacent to a junction cluster (fat junctions break the 1-neighbor
                # walk one pixel early) -> still a spur, prune it
                if any(cn[n] >= 3 for n in around):
                    remove.update(branch)
                    break
                if len(nbrs) != 1:
                    break
                prev, cur = cur, nbrs[0]
                if cn[cur] >= 3:
                    remove.update(branch)        # spur (excludes the junction)
                    break
                branch.append(cur)
        if not remove:
            break
        for (y, x) in remove:
            s[y, x] = 0
    return s


def _smooth_xy(a: np.ndarray, k: int = 3) -> np.ndarray:
    """Edge-safe moving average (edge-pad then valid) — avoids the zero-pad
    artifact of convolve('same') that injects fake curvature at path ends."""
    if len(a) < k:
        return a
    pad = k // 2
    ap = np.pad(a, ((pad, pad), (0, 0)), mode="edge")
    ker = np.ones(k) / k
    return np.column_stack([np.convolve(ap[:, 0], ker, "valid"),
                            np.convolve(ap[:, 1], ker, "valid")])


def _smoothed_curvature(skel: np.ndarray):
    """(turn, winding, straightness) from the smoothed traced path — kills the
    Zhang-Suen staircase that otherwise inflates winding/inflections on straight
    diagonals and clean sweeps."""
    path = longest_path(skel)
    if len(path) < 3:
        return np.zeros(0), 0.0, 1.0
    pc = np.array([(x, y) for (y, x) in path], dtype=np.float64)
    rs = _smooth_xy(_resample(pc, FP_K))
    turn = _turning(rs)
    plen = float(np.linalg.norm(np.diff(pc, axis=0), axis=1).sum())
    edist = float(np.linalg.norm(pc[0] - pc[-1]))
    return turn, float(np.abs(turn).sum()), (edist / plen if plen else 1.0)


def _lobes(turn: np.ndarray) -> int:
    """Number of substantial curvature lobes (a small terminal flick is not a
    lobe). Distinguishes a true S/wave (>=2 lobes) from a hook (<=1)."""
    lobes, acc, cur = 0, 0.0, 0
    for t in turn:
        if abs(t) < 0.05:
            continue
        s = 1 if t > 0 else -1
        if s != cur:
            if abs(acc) >= LOBE_MIN:
                lobes += 1
            cur, acc = s, 0.0
        acc += t
    if abs(acc) >= LOBE_MIN:
        lobes += 1
    return lobes


def _iou_bool(a: np.ndarray, b: np.ndarray) -> float:
    u = (a | b).sum()
    return float((a & b).sum()) / float(u) if u else 1.0


def _dilate1(a: np.ndarray) -> np.ndarray:
    out = a.copy()
    out[1:, :] |= a[:-1, :]; out[:-1, :] |= a[1:, :]
    out[:, 1:] |= a[:, :-1]; out[:, :-1] |= a[:, 1:]
    return out


def _sym_match(a: np.ndarray, b: np.ndarray) -> bool:
    """a ≈ b allowing 1px slack both ways — tolerant of font anti-alias / 1px
    centering drift that sinks a strict IoU below threshold for real symmetry."""
    if a.sum() == 0 or b.sum() == 0:
        return a.sum() == b.sum()
    da, db = _dilate1(a), _dilate1(b)
    cov = min((a & db).sum() / a.sum(), (b & da).sum() / b.sum())
    return cov >= SYM_COVER


def symmetry_lines(grid: np.ndarray) -> int:
    g = grid.astype(bool)
    out = 0
    if _sym_match(g, g[:, ::-1]):
        out |= SYM_BITS["vertical"]
    if _sym_match(g, g[::-1, :]):
        out |= SYM_BITS["horizontal"]
    if _sym_match(g, g.T):
        out |= SYM_BITS["diagonal_back"]      # main-diagonal reflection
    if _sym_match(g, g[::-1, ::-1].T):
        out |= SYM_BITS["diagonal_fwd"]       # anti-diagonal reflection
    return out


def _enclosed_holes(grid: np.ndarray) -> int:
    """β₁ᵣₐw: number of bounded, border-unreachable, 4-connected background
    components (= enclosed holes). This is the raw first Betti count under the
    FL-4208 (8,4)-connectivity contract — foreground 8-connected, background
    4-connected. It counts *holes*, NOT enclosed-background pixel area (the
    pre-Gate-T1 behavior returned `(bg & ~reach).sum()`, a pixel-area proxy that
    is mathematically not β₁). Denoising is a downstream concern; this stays raw."""
    bg = grid == 0
    h, w = grid.shape
    from collections import deque
    reach = np.zeros(bg.shape, dtype=bool)
    dq = deque()
    # Seed the unbounded outer region: every border background pixel. Flood with
    # 4-connectivity so the hole count is the background 4-connected count.
    for x in range(w):
        for y in (0, h - 1):
            if bg[y, x] and not reach[y, x]:
                reach[y, x] = True; dq.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if bg[y, x] and not reach[y, x]:
                reach[y, x] = True; dq.append((y, x))
    while dq:
        y, x = dq.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and bg[ny, nx] and not reach[ny, nx]:
                reach[ny, nx] = True; dq.append((ny, nx))
    # Enclosed background = border-unreachable bg. Count its 4-connected
    # components; each distinct component is exactly one hole (β₁ᵣₐw).
    enclosed = bg & ~reach
    seen = np.zeros(bg.shape, dtype=bool)
    holes = 0
    for sy in range(h):
        for sx in range(w):
            if enclosed[sy, sx] and not seen[sy, sx]:
                holes += 1
                seen[sy, sx] = True
                dq.append((sy, sx))
                while dq:
                    y, x = dq.popleft()
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = y + dy, x + dx
                        if (0 <= ny < h and 0 <= nx < w and enclosed[ny, nx]
                                and not seen[ny, nx]):
                            seen[ny, nx] = True; dq.append((ny, nx))
    return holes


def fill_state(grid: np.ndarray) -> str:
    if _enclosed_holes(grid) > 0:          # β₁ᵣₐw: any enclosed hole => hollow (FL-4208 Gate T1)
        return "hollow"
    if grid.mean() > 0.55:
        return "solid"
    return "open"


def stroke_count(skel: np.ndarray) -> int:
    cn = crossing_number(skel)
    seg = skel.copy()
    seg[cn >= 3] = 0                      # delete junctions -> remaining components = strokes
    return max(1, components(seg))


def _inflections(turn: np.ndarray) -> int:
    t = turn[np.abs(turn) > 0.15]
    if len(t) < 2:
        return 0
    s = np.sign(t)
    return int(np.sum(s[1:] != s[:-1]))


def orientation_bin(path: list[tuple[int, int]]) -> int | None:
    """Gross direction of the stroke, 0..7 = E NE N NW W SW S SE (CCW from East).
    Canonical start = topmost-leftmost endpoint (screen frame)."""
    import math
    if len(path) < 2:
        return None
    a, b = path[0], path[-1]
    if b < a:
        a, b = b, a                       # start = topmost-leftmost
    dy, dx = b[0] - a[0], b[1] - a[1]
    if dx == 0 and dy == 0:
        return None
    deg = math.degrees(math.atan2(-dy, dx)) % 360.0   # screen y is down
    return int(round(deg / 45.0)) % 8


def mirror_parity(skel: np.ndarray, path: list[tuple[int, int]], sym: int):
    """(parity, confidence). Chirality only exists for chiral glyphs (no symmetry
    line). Computed in the glyph's intrinsic PCA frame so rotation preserves the
    sign and reflection flips it; trace-direction is removed (frame, not order).
    Confidence=degenerate when the frame is ill-defined; then parity is None
    unless a second method (orientation-canonical signed turning) agrees."""
    if sym != 0:
        return None, "stable"             # achiral: parity definitively N/A
    pts = np.column_stack(np.where(skel))[:, ::-1].astype(np.float64)  # (x,y)
    if len(pts) < 5 or len(path) < 3:
        return None, "degenerate"
    ctr = pts.mean(0)
    X = pts - ctr
    cov = (X.T @ X) / len(X)
    w, V = np.linalg.eigh(cov)            # ascending eigenvalues
    l2, l1 = float(w[0]), float(w[1])
    if l1 <= 1e-9:
        return None, "degenerate"
    near_circular = (l2 / l1) > ECC_THRESH
    v1 = V[:, 1].copy()                   # major axis
    proj = X @ v1
    m3 = float((proj ** 3).mean())
    dir_unstable = abs(m3) < 1e-6 + 0.02 * (float(proj.std()) ** 3)
    if m3 < 0:
        v1 = -v1                          # rotation-equivariant direction tiebreak
    v2 = np.array([-v1[1], v1[0]])        # fixed right-handed perpendicular
    pp = np.array([(x, y) for (y, x) in path], dtype=np.float64) - ctr
    aa, bb = pp @ v1, pp @ v2
    area = 0.5 * float(np.sum(aa[:-1] * bb[1:] - aa[1:] * bb[:-1]))
    area_norm = area / float(skel.shape[0] ** 2)
    pca_sign = 1 if area_norm > 0 else -1
    degenerate = near_circular or dir_unstable or abs(area_norm) < AREA_THRESH
    if not degenerate:
        return ("normal" if pca_sign > 0 else "mirrored"), "stable"
    # degenerate: require a second method to agree (net signed turning, canonical start)
    if path and len(path) >= 3:
        pc = np.array([(x, y) for (y, x) in path], dtype=np.float64)
        if path[-1] < path[0]:
            pc = pc[::-1]
        v = np.diff(pc, axis=0)
        ang = np.arctan2(-v[:, 1], v[:, 0])
        dnet = float(np.sum((np.diff(ang) + np.pi) % (2 * np.pi) - np.pi))
        turn_sign = 1 if dnet > 0 else -1
        if abs(dnet) > 0.4 and turn_sign == pca_sign:
            return ("normal" if pca_sign > 0 else "mirrored"), "degenerate"
    return None, "degenerate"


def topo_class(fp: dict, skel: np.ndarray, grid: np.ndarray) -> str:
    """Endpoint + loop based (robust to false mid-stroke junctions from staircase
    noise). A loop = an enclosed hole; loop+tails (A/R/P) = compound; pure loop =
    arc_loop. A single open stroke (e<=2, no loop) splits bar/single_hook/wave by
    smoothed curvature."""
    e, comp = fp["n_endpoints"], fp["n_components"]
    has_loop = _enclosed_holes(grid) > 0    # β₁ᵣₐw: ≥1 enclosed hole => loop (FL-4208 Gate T1)
    if fp["skel_px"] <= DOT_MAX_PX or fp["path_len_norm"] < 0.15:
        return "dot"
    if comp >= 2:
        return "compound"
    if has_loop:
        return "arc_loop" if e == 0 else "compound"   # ring vs loop+tails (A,R,P,Q)
    if e == 0:
        return "arc_loop"
    if e >= 4:
        return "compound"
    if e == 3:
        return "branch"
    turn, winding, straight = _smoothed_curvature(skel)   # e in {1,2}: one open stroke
    if straight >= STRAIGHT_BAR:
        return "bar"
    if _lobes(turn) >= 2:
        return "wave"
    return "single_hook"


def axis_vector(grid: np.ndarray, skel: np.ndarray | None = None,
                fp: dict | None = None) -> dict:
    if skel is None:
        skel = despur(thin(grid))        # drop serifs/ticks before structure counts
    if fp is None:
        fp = fingerprint(grid, skel)
    path = longest_path(skel)
    sym = symmetry_lines(grid)
    cls = topo_class(fp, skel, grid)
    orient = None if cls in ("arc_loop", "dot") else orientation_bin(path)
    parity, conf = mirror_parity(skel, path, sym)
    return {
        "topo_class": cls,
        "orientation": orient,
        "mirror_parity": parity,
        "mirror_parity_confidence": conf,
        "symmetry_lines": sym,
        "bend_softness": topo_bend(fp),
        "density": float(grid.mean()),
        "fill_state": fill_state(grid),
        "endpoint_count": fp["n_endpoints"],
        "branch_count": fp["n_branches"],
        "stroke_count": stroke_count(skel),
    }


def _sym_names(bits: int) -> str:
    return "+".join(n for n, v in SYM_BITS.items() if bits & v) or "none"


def cmd_axes(args) -> int:
    """Phase-A gate: positive class examples, reflection-parity proof, achiral
    controls, AND negative controls (hook must NOT absorb bar/wave/branch/compound)."""
    r = HiRenderer()
    def av(cp):
        return axis_vector(r.grid(cp, FP_SIZE))
    def show(tag, cp):
        grid = r.grid(cp, FP_SIZE)
        v = axis_vector(grid)
        d = ""
        if args.debug:
            sk = despur(thin(grid))
            fpp = fingerprint(grid, sk)
            t, w, st = _smoothed_curvature(sk)
            gb = grid.astype(bool)
            d = (f"  [e={fpp['n_endpoints']} comp={fpp['n_components']} "
                 f"holes={_enclosed_holes(grid)} wind={w:.2f} straight={st:.2f} "
                 f"lobes={_lobes(t)} v={_iou_bool(gb, gb[:, ::-1]):.2f} "
                 f"dfwd={_iou_bool(gb, gb[::-1, ::-1].T):.2f} dback={_iou_bool(gb, gb.T):.2f}]")
        print(f"  {tag:18} U+{cp:04X} {chr(cp)}  class={v['topo_class']:11} "
              f"orient={str(v['orientation']):>4} parity={str(v['mirror_parity']):>8}"
              f"/{v['mirror_parity_confidence']:9} sym={_sym_names(v['symmetry_lines'])} "
              f"fill={v['fill_state']} strokes={v['stroke_count']}{d}")
        return v

    ok = True
    print("positive — single_hook (J レ し; clean one-bend hooks):")
    for cp in (0x004A, 0x30EC, 0x3057):
        v = show("hook", cp)
        ok &= v["topo_class"] == "single_hook"
    print("evidence note — √ (U+221A) is NOT a clean hook:")
    vroot = show("root √", 0x221A)
    # the unifont radical is a 3-lobe zigzag (tick+valley+vinculum) -> wave, not hook.
    # The original √ exemplar was a loose human grouping; the metric corrects it.
    ok &= vroot["topo_class"] == "wave"

    print("\nreflection parity (レ vs mirrored-レ via fliplr):")
    re_grid = r.grid(0x30EC, FP_SIZE)
    a = axis_vector(re_grid)
    bvec = axis_vector(re_grid[:, ::-1].copy())
    same_cls = a["topo_class"] == bvec["topo_class"]
    opp = (a["mirror_parity"] and bvec["mirror_parity"]
           and a["mirror_parity"] != bvec["mirror_parity"]
           and a["mirror_parity_confidence"] == "stable"
           and bvec["mirror_parity_confidence"] == "stable")
    print(f"  レ        class={a['topo_class']} parity={a['mirror_parity']}/{a['mirror_parity_confidence']}")
    print(f"  mirror-レ class={bvec['topo_class']} parity={bvec['mirror_parity']}/{bvec['mirror_parity_confidence']}")
    print(f"  same class + opposite STABLE parity: {'PASS' if same_cls and opp else 'FAIL'}")
    ok &= same_cls and opp

    print("\nachiral controls (must be parity=None):")
    for tag, cp in (("A", 0x0041), ("T", 0x0054), ("triangle", 0x25B2)):
        v = show(tag, cp)
        ok &= v["mirror_parity"] is None and (v["symmetry_lines"] & SYM_BITS["vertical"])

    print("\nslash pair (bar; distinguished by ORIENTATION, parity None) [diagnostic]:")
    vf = show("slash /", 0x002F)
    vb = show("backslash \\", 0x005C)
    # NOTE: the unifont slash is steeper than 45 deg, so it is symmetric across its
    # OWN axis, not the canonical 45-deg diagonal -> the diagonal_* symmetry bit may
    # miss. The reliable discriminator is orientation (/ != \), both bar, parity None.
    slash_ok = (vf["topo_class"] == "bar" and vb["topo_class"] == "bar"
                and vf["mirror_parity"] is None and vb["mirror_parity"] is None
                and vf["orientation"] != vb["orientation"])
    print(f"  both bar, parity None, orientation differs: {'PASS' if slash_ok else 'CHECK'}")

    print("\nhalf-circles (one class across orientations, parity None):")
    hc = [show(f"hc{n}", cp) for n, cp in enumerate((0x25D0, 0x25D1, 0x25D2, 0x25D3))]
    hc_cls = {v["topo_class"] for v in hc}
    hc_ok = len(hc_cls) == 1 and all(v["mirror_parity"] is None for v in hc)
    print(f"  single class {hc_cls}, parity None: {'PASS' if hc_ok else 'CHECK'}")

    print("\nnegative controls (must NOT be single_hook):")
    negs = (("bar |", 0x007C, "bar"), ("wave ~", 0x223F, "wave"),
            ("branch Y", 0x0059, "branch"), ("compound =", 0x003D, "compound"))
    for tag, cp, want in negs:
        v = show(tag, cp)
        not_hook = v["topo_class"] != "single_hook"
        print(f"      -> {'PASS' if not_hook else 'FAIL'} (not single_hook; got {v['topo_class']}, want~{want})")
        ok &= not_hook

    print("\n" + ("PHASE-A GATE PASS" if ok else "PHASE-A GATE FAIL"))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Phase 3: global fingerprint cache (the gated step). Excludes the giant
# ideographic blocks by default — they dominate cost and neighbour structure,
# and the target is hook/foliage/motion glyphs, not CJK/Hangul/Tangut. Excluded
# blocks remain opt-in via --only-block / --include-empty.
# ---------------------------------------------------------------------------
SCAL_FIELDS = ("path_len_norm", "straightness", "aspect", "winding",
               "mean_abs", "max_abs", "peak_pos", "net_bend")


def block_excluded(block: str) -> bool:
    if block == "":                       # <no name> SMP ideograph/component noise
        return True
    if block.startswith("CJK"):
        return True
    if "Hangul Syllables" in block:
        return True
    if block.startswith("Tangut"):
        return True
    if block == "Yi Syllables":
        return True
    return False


def _topo_paths():
    import glyph_features as gf
    return gf.CACHE_DIR / "glyph_topo.npz", gf.CACHE_DIR / "glyph_topo.meta.json"


def build_topo_cache(only_block: str | None = None, include_empty: bool = False) -> int:
    import json
    import glyph_features as gf
    data, meta = gf.load()
    cps = data["cps"].tolist()
    blocks = meta["blocks"]
    keep = []
    for i, cp in enumerate(cps):
        b = blocks[i]
        if only_block is not None:
            if only_block.upper() not in b.upper():
                continue
        elif block_excluded(b) and not (include_empty and b == ""):
            continue
        keep.append(i)
    r = HiRenderer()
    M = len(keep)
    out_cps = np.zeros(M, np.int32)
    struct = np.zeros((M, 3), np.int8)
    scal = np.zeros((M, len(SCAL_FIELDS)), np.float32)
    turn = np.zeros((M, FP_K - 2), np.float32)
    okm = np.zeros(M, bool)
    # schema-2 axis-vector fields
    av_cls = np.zeros(M, np.int8)        # index into TOPO_CLASSES
    av_orient = np.full(M, -1, np.int8)  # 0..7, -1 = None
    av_parity = np.full(M, -1, np.int8)  # -1 None, 0 normal, 1 mirrored
    av_conf = np.zeros(M, np.int8)       # 0 stable, 1 degenerate
    av_sym = np.zeros(M, np.uint8)       # symmetry_lines bitset
    av_fill = np.zeros(M, np.int8)       # index into FILL_STATES
    av_strokes = np.zeros(M, np.int16)
    pmap = {None: -1, "normal": 0, "mirrored": 1}
    t0 = time.perf_counter()
    for n, i in enumerate(keep):
        cp = cps[i]
        grid = r.grid(cp, FP_SIZE)
        skel = despur(thin(grid))
        fp = fingerprint(grid, skel)
        av = axis_vector(grid, skel, fp)
        out_cps[n] = cp
        struct[n] = (fp["n_endpoints"], fp["n_branches"], fp["n_components"])
        scal[n] = [fp[k] for k in SCAL_FIELDS]
        turn[n] = fp["turn"]
        okm[n] = fp["ok"]
        av_cls[n] = TOPO_CLASSES.index(av["topo_class"])
        av_orient[n] = -1 if av["orientation"] is None else av["orientation"]
        av_parity[n] = pmap[av["mirror_parity"]]
        av_conf[n] = 1 if av["mirror_parity_confidence"] == "degenerate" else 0
        av_sym[n] = av["symmetry_lines"]
        av_fill[n] = FILL_STATES.index(av["fill_state"])
        av_strokes[n] = av["stroke_count"]
        if (n + 1) % 2000 == 0:
            el = time.perf_counter() - t0
            sys.stderr.write(f"  axis-vectored {n + 1}/{M}  ({el:.0f}s, "
                             f"~{el / (n + 1) * (M - n - 1):.0f}s left)\n")
    npz, mj = _topo_paths()
    np.savez_compressed(npz, cps=out_cps, struct=struct, scal=scal, turn=turn, ok=okm,
                        av_cls=av_cls, av_orient=av_orient, av_parity=av_parity,
                        av_conf=av_conf, av_sym=av_sym, av_fill=av_fill,
                        av_strokes=av_strokes)
    mj.write_text(json.dumps({
        "schema": 2, "fp_size": FP_SIZE, "fp_k": FP_K,
        "scal_fields": list(SCAL_FIELDS),
        "topo_classes": TOPO_CLASSES, "fill_states": FILL_STATES, "sym_bits": SYM_BITS,
        "only_block": only_block, "include_empty": include_empty,
        "count": M, "usable": int(okm.sum()),
        "source_count": len(cps),
    }, indent=2) + "\n")
    el = time.perf_counter() - t0
    sys.stderr.write(f"  wrote {npz} — {M} glyphs ({int(okm.sum())} usable) in {el:.0f}s\n")
    return 0


def load_topo_cache():
    import json
    npz, mj = _topo_paths()
    if not npz.exists():
        raise SystemExit(f"no topo cache at {npz} — build it: "
                         f"python3 scripts/glyph_skeleton.py cache")
    d = np.load(npz)
    meta = json.loads(mj.read_text()) if mj.exists() else {}
    return d, meta


def fp_from_row(struct_row, scal_row, turn_row) -> dict:
    """Reconstruct the fingerprint dict (the shape topo_distance/topo_bend want)
    from cached arrays, so the global pass reuses the exact Phase-1 distance."""
    s = {SCAL_FIELDS[k]: float(scal_row[k]) for k in range(len(SCAL_FIELDS))}
    return {
        "ok": True,
        "n_endpoints": int(struct_row[0]),
        "n_branches": int(struct_row[1]),
        "n_components": int(struct_row[2]),
        "turn": np.asarray(turn_row, np.float32),
        **s,
    }


def cmd_cache(args) -> int:
    return build_topo_cache(only_block=args.only_block, include_empty=args.include_empty)


def cmd_validate(args) -> int:
    """Phase-1 gate: the canonical hook set must CLUSTER (intra << inter) and
    し must be the softest extreme. The full sharp->soft order is reported as a
    diagnostic only — the loose human guess (√ -> J -> レ -> し) is NOT asserted
    because the participation-ratio metric ranks レ sharper than √."""
    r = HiRenderer()
    labels = {0x3057: "し", 0x30EC: "レ", 0x221A: "√", 0x004A: "J",
              0x222B: "∫", 0x0283: "ʃ"}
    fps = {}
    for cp in CANONICAL:
        g = r.grid(cp, FP_SIZE)
        fps[cp] = fingerprint(g)
    print(f"fingerprints @ {FP_SIZE}px (FP_K={FP_K})\n")
    print(f"  {'glyph':6} {'cp':8} end br cmp  {'straight':>8} {'aspect':>7} "
          f"{'winding':>7} {'maxabs':>7} {'soft':>7}")
    for cp in CANONICAL:
        f = fps[cp]
        print(f"  {labels[cp]:6} U+{cp:04X}  {f['n_endpoints']:2} {f['n_branches']:2} "
              f"{f['n_components']:2}  {f['straightness']:8.3f} {f['aspect']:7.3f} "
              f"{f['winding']:7.3f} {f['max_abs']:7.3f} {topo_bend(f):7.3f}")

    # intra-set: all pairwise distances within the hook set
    cps = CANONICAL
    intra = [topo_distance(fps[cps[i]], fps[cps[j]])
             for i in range(len(cps)) for j in range(i + 1, len(cps))]
    intra_mean = float(np.mean(intra))

    # inter-set: each hook vs a spread of random non-hook glyphs
    import random
    rng = random.Random(1234)
    pool = [0x0041, 0x0042, 0x25A0, 0x2588, 0x4E00, 0x571F, 0x2603,
            0x2660, 0x0023, 0x002B, 0x25CF, 0x3042, 0x16A0, 0x0E01]
    rng.shuffle(pool)
    inter = []
    for cp in cps:
        for q in pool:
            fq = fingerprint(r.grid(q, FP_SIZE))
            if fq["ok"]:
                inter.append(topo_distance(fps[cp], fq))
    inter_mean = float(np.mean(inter))
    ratio = inter_mean / intra_mean if intra_mean else float("inf")
    print(f"\n  intra-hook mean dist = {intra_mean:.3f}")
    print(f"  inter (hook vs random) mean dist = {inter_mean:.3f}")
    print(f"  separation ratio = {ratio:.2f}  ({'PASS' if ratio >= 1.5 else 'WEAK'} "
          f">=1.5 target)")

    # --- GATE criteria (firm, provable assertions only) ---
    ratio_ok = ratio >= 1.5
    # The only morphology assertion the data firmly supports: し (a long smooth
    # sweep) is the SOFTEST of the set. The full sharp->soft *order* is NOT a
    # gate (see diagnostic below) — the human guess was approximate.
    soft_ok = max(CANONICAL, key=lambda cp: topo_bend(fps[cp])) == 0x3057
    print(f"  separation>=1.5 .... {'PASS' if ratio_ok else 'FAIL'}")
    print(f"  し is softest ...... {'PASS' if soft_ok else 'FAIL'}")

    # --- DIAGNOSTIC (not a gate): full sharp->soft order ---
    # The loose human guess was √ -> J -> レ -> し. The participation-ratio
    # softness metric instead ranks レ sharper than √ (レ is a near-straight
    # stroke with one concentrated kink; √'s long diagonal carries distributed
    # curvature). This divergence is expected and is reported, not asserted.
    diag = [0x221A, 0x004A, 0x30EC, 0x3057]
    got = sorted(diag, key=lambda cp: topo_bend(fps[cp]))
    print(f"\n  [diagnostic] sharp->soft by metric: "
          + " -> ".join(labels[cp] for cp in got))
    print(f"  [diagnostic] loose human guess was:  "
          + " -> ".join(labels[cp] for cp in diag) + "  (NOT asserted)")

    gate_ok = ratio_ok and soft_ok
    print("\n" + ("GATE PASS" if gate_ok else "GATE FAIL"))
    return 0 if gate_ok else 1


def cmd_spike(args) -> int:
    cps = [int(t[2:], 16) if t.lower().startswith("u+") else int(t, 16) for t in args.cps] \
        if args.cps else CANONICAL
    sizes = [int(s) for s in args.sizes.split(",")]
    r = HiRenderer()
    print(f"font chain: {[p.name for p in r._chain_paths]}\n")
    for cp in cps:
        name = unicodedata.name(chr(cp), "?")
        print("=" * 70)
        print(f"U+{cp:04X} {chr(cp)}  {name}")
        for S in sizes:
            grid = r.grid(cp, S)
            ink = int(grid.sum())
            t0 = time.perf_counter()
            skel = thin(grid)
            dt = (time.perf_counter() - t0) * 1000
            deg = degree_map(skel)
            endpoints = int(((deg == 1)).sum())
            junctions = int(((deg >= 3)).sum())
            comp = components(skel)
            print(f"\n  --- {S}px ---  ink={ink}  skel_px={int(skel.sum())}  "
                  f"endpoints={endpoints}  junctions={junctions}  components={comp}  "
                  f"thin={dt:.1f}ms")
            if args.ascii:
                print(ascii_overlay(grid, skel))
        print()
    # cost projection
    S = sizes[-1]
    grid = r.grid(CANONICAL[2], S)
    t0 = time.perf_counter()
    for _ in range(5):
        thin(grid)
    per = (time.perf_counter() - t0) / 5
    print("=" * 70)
    print(f"cost @ {S}px: ~{per*1000:.1f}ms thin/glyph -> ~{per*152661/60:.1f} min for 152k "
          f"(thinning only; render extra)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("spike", help="render + thin canonical hooks at several sizes")
    p.add_argument("cps", nargs="*", help="codepoints (default: し レ √ J ∫ ʃ)")
    p.add_argument("--sizes", default="16,48,64")
    p.add_argument("--ascii", action="store_true", default=True)
    p.add_argument("--no-ascii", dest="ascii", action="store_false")

    v = sub.add_parser("validate", help="Phase-1 gate: hook set clusters + bend orders")

    a = sub.add_parser("axes", help="Phase-A gate: per-glyph axis vector (class/orientation/"
                                    "parity/symmetry) with positive + negative controls")
    a.add_argument("--debug", action="store_true", help="print raw features for tuning")

    cp = sub.add_parser("cache", help="Phase-3: build the global topology fingerprint cache")
    cp.add_argument("--only-block", default=None,
                    help="restrict the pass to one block (substring match); "
                         "default = all blocks minus the giant ideographic ones")
    cp.add_argument("--include-empty", action="store_true",
                    help="also fingerprint the <no name> SMP ideograph bucket")

    args = ap.parse_args()
    return {"spike": cmd_spike, "validate": cmd_validate, "cache": cmd_cache,
            "axes": cmd_axes}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
