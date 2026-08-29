#!/usr/bin/env python3
"""Unicode glyph similarity audit — discover ramps, cycles, fill-pairs, neighbors.

Reads the shape-feature cache built by ``glyph_features.py`` and runs the
discovery operators described in the design:

    similar  nearest neighbours of one glyph under a chosen lens
    ramp     density/stroke-count ramps (same mark, stepping ink)  -> Ogham blades
    fill     outline -> filled pairs (containment + interior fill) -> WHITE/BLACK club
    cycle    near-identical clusters good for animating one cell, capped to
             short animatable chains instead of giant visual-neighbour blobs
    spin     C4 rotation orbits (spinners: ◐◑◒◓, ▲▶▼◀, ◜◝◞◟)
    morph    seed -> local shape-neighbours ordered along a morphology axis;
             --lens hu finds cross-script neighbours IoU misses, --lens topo
             ranks by skeleton structure; global topology family clustering
             (caching every fingerprint) is the still-gated next step
    anim     animate a glyph sequence in place — WATCH a spinner/ramp/cycle move
             (anim --spin N / --ramp N / --cycle N / --fill N pulls a family)
    families list ALL families across every mode (--json caches for the viewer)
    validate check the known hand-found examples fall out

The animated viewer over all families is scripts/glyph_families_viewer.py.

Lenses (for `similar`):
    iou_dil  IoU of dilated normalized grid (best for ramp-family neighbours)
    iou_norm IoU of normalized grid (shape, size/position invariant)
    iou      IoU of raw grid (near-identical, position-sensitive)
    hamming  raw pixel difference
    hu       Hu-moment distance (rotation/scale invariant)
    zoning   4x4 layout distance
    orient   orientation-histogram distance (flow/direction)
    topo     skeleton-topology fingerprint (crossing-number graph + traced-path
             curvature) — clusters structurally-similar glyphs across scripts
             (し レ √ J ∫ ʃ) where IoU/Hu cannot. Computed lazily over a
             Hu-prefiltered pool (--prefilter); see scripts/glyph_skeleton.py.
             morph adds --axis topo_soft (sharp->soft hook).

Examples:
    python3 scripts/glyph_audit.py similar U+1686 --lens iou_dil -k 12
    python3 scripts/glyph_audit.py ramp --min-len 3
    python3 scripts/glyph_audit.py fill
    python3 scripts/glyph_audit.py validate
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

import numpy as np

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import glyph_features as gf  # noqa: E402

N = gf.N


class Corpus:
    def __init__(self):
        data, meta = gf.load()
        self.cps = data["cps"]
        self.raw = data["raw"].astype(np.uint8)
        self.norm = data["norm"].astype(np.uint8)
        self.norm_dil = data["norm_dil"].astype(np.uint8)
        self.ink = data["ink"]
        self.ncomp = data["ncomp"]
        self.zoning = data["zoning"]
        self.hu = data["hu"]
        self.orient = data["orient"]
        self.d4 = data["d4"]
        self.names = meta["names"]
        self.fonts = meta["fonts"]
        self.blocks = meta["blocks"]
        self.count = meta["count"]
        self.idx_of = {int(cp): i for i, cp in enumerate(self.cps.tolist())}

    def i(self, cp: int) -> int | None:
        return self.idx_of.get(int(cp))

    def label(self, i: int) -> str:
        cp = int(self.cps[i])
        return f"U+{cp:04X} {chr(cp)}"


# ---------------------------------------------------------------------------
# Distance lenses (query row index -> distance array over the corpus)
# ---------------------------------------------------------------------------
def _iou_to(mat: np.ndarray, q: np.ndarray) -> np.ndarray:
    inter = (mat & q).sum(1).astype(np.float64)
    union = (mat | q).sum(1).astype(np.float64)
    union[union == 0] = 1.0
    return inter / union


def distances(c: Corpus, qi: int, lens: str) -> np.ndarray:
    if lens == "hamming":
        return (c.raw ^ c.raw[qi]).sum(1).astype(np.float64)
    if lens == "iou":
        return 1.0 - _iou_to(c.raw, c.raw[qi])
    if lens == "iou_norm":
        return 1.0 - _iou_to(c.norm, c.norm[qi])
    if lens == "iou_dil":
        return 1.0 - _iou_to(c.norm_dil, c.norm_dil[qi])
    if lens == "hu":
        return np.linalg.norm(c.hu - c.hu[qi], axis=1)
    if lens == "zoning":
        return np.linalg.norm(c.zoning - c.zoning[qi], axis=1)
    if lens == "orient":
        a = c.orient
        q = c.orient[qi]
        num = a @ q
        den = (np.linalg.norm(a, axis=1) * np.linalg.norm(q) + 1e-9)
        return 1.0 - num / den
    if lens == "topo":
        raise SystemExit("lens 'topo' is computed lazily — use cmd_similar/cmd_morph "
                         "(it cannot fill a full-corpus distance array without the "
                         "global fingerprint cache, which is gated Phase 3)")
    raise SystemExit(f"unknown lens: {lens}")


# ---------------------------------------------------------------------------
# topo lens (Phase 2): skeleton-topology fingerprint distance, computed lazily
# over a Hu-prefiltered candidate pool so no global fingerprint cache is needed.
# ---------------------------------------------------------------------------
class _Topo:
    """Lazy fingerprint provider. Imports the heavy skeleton/font stack only when
    the topo lens is actually used, and memoizes fingerprints by codepoint."""
    def __init__(self):
        import glyph_skeleton as gs  # heavy (PIL + font chain) — import on demand
        self.gs = gs
        self.r = gs.HiRenderer()
        self.cache: dict[int, dict] = {}

    def fp(self, cp: int) -> dict:
        f = self.cache.get(cp)
        if f is None:
            f = self.gs.fingerprint(self.r.grid(cp, self.gs.FP_SIZE))
            self.cache[cp] = f
        return f


def topo_rank(c: Corpus, qi: int, prefilter: int, topo: _Topo) -> list[tuple[int, float]]:
    """Rank the corpus against query qi by topology distance. Prefilter by Hu
    (cheap, full-corpus) to a bounded pool, then fingerprint only those and the
    query, and re-rank by topo_distance. Returns [(idx, dist)] best-first."""
    hu = distances(c, qi, "hu")
    cand = [i for i in np.argsort(hu) if i != qi][:prefilter]
    qfp = topo.fp(int(c.cps[qi]))
    scored: list[tuple[int, float]] = []
    if not qfp["ok"]:
        sys.stderr.write(f"  [topo] query U+{int(c.cps[qi]):04X} has no usable skeleton\n")
        return scored
    for i in cand:
        fp = topo.fp(int(c.cps[i]))
        if not fp["ok"]:
            continue
        scored.append((i, topo.gs.topo_distance(qfp, fp)))
    scored.sort(key=lambda t: t[1])
    return scored


# ---------------------------------------------------------------------------
# ASCII preview strips
# ---------------------------------------------------------------------------
def render_strip(c: Corpus, indices: list[int], per_row: int = 6, which: str = "raw") -> str:
    grids = getattr(c, which)
    out = []
    for start in range(0, len(indices), per_row):
        chunk = indices[start:start + per_row]
        heads = []
        for i in chunk:
            cp = int(c.cps[i])
            heads.append(f"U+{cp:04X} {chr(cp)}".ljust(N * 2))
        out.append("  ".join(heads))
        for row in range(N):
            parts = []
            for i in chunk:
                g = grids[i].reshape(N, N)[row]
                parts.append("".join("██" if v else "  " for v in g))
            out.append("  ".join(parts))
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# similar
# ---------------------------------------------------------------------------
def cmd_similar(c: Corpus, args) -> int:
    cp = parse_cp(args.query)
    qi = c.i(cp)
    if qi is None:
        raise SystemExit(f"U+{cp:04X} not in the cached corpus (rebuild glyph_features with its block)")
    if args.lens == "topo":
        scored = topo_rank(c, qi, args.prefilter, _Topo())
        order = [i for i, _ in scored[:args.k]]
        dmap = {i: dd for i, dd in scored}
        def dget(i): return dmap.get(i, float("nan"))
    else:
        d = distances(c, qi, args.lens)
        order = [i for i in np.argsort(d) if i != qi][:args.k]
        def dget(i): return d[i]
    print(f"\nQuery U+{cp:04X} {chr(cp)}  {c.names[qi]}   lens={args.lens}"
          + (f" (Hu-prefilter {args.prefilter})" if args.lens == "topo" else "") + "\n")
    print(render_strip(c, [qi], per_row=1))
    print(f"Top {len(order)} neighbours:")
    for rank, i in enumerate(order, 1):
        print(f"  {rank:2}. U+{int(c.cps[i]):04X} {chr(int(c.cps[i]))}  d={dget(i):.3f}  "
              f"ink={int(c.ink[i])} comp={int(c.ncomp[i])}  {c.names[i]}  [{c.blocks[i]}]")
    print()
    print(render_strip(c, order, per_row=args.cols))
    return 0


# ---------------------------------------------------------------------------
# ramp: same mark, stepping ink (within an orientation bin, monotone-ink chain)
# ---------------------------------------------------------------------------
def iou_matrix(B: np.ndarray) -> np.ndarray:
    Bf = B.astype(np.float64)
    inter = Bf @ Bf.T
    area = Bf.sum(1)
    union = area[:, None] + area[None, :] - inter
    union[union == 0] = 1.0
    return inter / union


def block_allow(c: Corpus, block: str | None) -> set[int] | None:
    if not block:
        return None
    up = block.upper()
    return {i for i in range(c.count) if up in c.blocks[i].upper()}


SCOPE_CAP = 4000        # max glyphs fed to one O(n^2) iou_matrix (bounds memory)
FILL_BUCKET_CAP = 600   # max glyphs in one silhouette bucket for O(n^2) fill pairing


def find_ramps(c: Corpus, min_len: int, iou_min: float, spread_min: float,
               allow: set[int] | None = None) -> list[list[int]]:
    """A ramp is a tight shape-family ordered by ink: complete-linkage cluster on
    dilated-normalized overlap, kept only if it spans a real density range.
    Scope with `allow` (a block) for crisp per-script ramps; global is coarse.

    On the full corpus, ramps are found per-block (the only density-invariant
    bucket is orientation, so global bins would be huge); blocks bigger than
    SCOPE_CAP are skipped with a logged note rather than risking an OOM."""
    dom = c.orient.argmax(1)
    scopes: dict[str, list[int]] = defaultdict(list)
    for i in range(c.count):
        if allow is not None and i not in allow:
            continue
        scopes[c.blocks[i] if allow is None else "_scope"].append(i)
    chains: list[list[int]] = []
    skipped: list[tuple[str, int]] = []
    for scope, members in scopes.items():
        if len(members) > SCOPE_CAP:
            skipped.append((scope, len(members)))
            continue
        bins: dict[int, list[int]] = defaultdict(list)
        for i in members:
            bins[int(dom[i])].append(i)
        chains.extend(_cluster_ramps(c, bins, min_len, iou_min, spread_min))
    if skipped:
        note = ", ".join(f"{s} ({n})" for s, n in sorted(skipped, key=lambda t: -t[1])[:5])
        sys.stderr.write(f"  [skipped {len(skipped)} oversized blocks for ramp: {note}…]\n")
    chains.sort(key=len, reverse=True)
    return chains


def _cluster_ramps(c: Corpus, bins: dict[int, list[int]], min_len: int,
                   iou_min: float, spread_min: float) -> list[list[int]]:
    chains: list[list[int]] = []
    for _b, idxs in bins.items():
        if len(idxs) < min_len:
            continue
        sub = np.array(idxs)
        iou = iou_matrix(c.norm_dil[sub])
        k = len(idxs)
        # Greedy COMPLETE-linkage clustering: a glyph joins a cluster only if it
        # is similar to EVERY current member. (Single-linkage / union-find chains
        # dissimilar glyphs into one mega-blob via transitive hops — avoid that.)
        order = sorted(range(k), key=lambda j: int(c.ink[sub[j]]))
        used = [False] * k
        for seed in order:
            if used[seed]:
                continue
            cluster = [seed]
            for j in sorted((x for x in range(k) if x != seed and not used[x]),
                            key=lambda x: -iou[seed, x]):
                if iou[seed, j] < iou_min:
                    break  # rest are even less similar to the seed
                if all(iou[j, m] >= iou_min for m in cluster):
                    cluster.append(j)
            for m in cluster:
                used[m] = True
            if len(cluster) < min_len:
                continue
            members = sorted((int(sub[m]) for m in cluster), key=lambda i: int(c.ink[i]))
            inks = [int(c.ink[i]) for i in members]
            if inks[-1] < inks[0] * spread_min:   # needs a real density range
                continue
            chains.append(members)
    chains.sort(key=len, reverse=True)
    return chains


def cmd_ramp(c: Corpus, args) -> int:
    chains = find_ramps(c, args.min_len, args.iou_min, args.spread_min,
                        allow=block_allow(c, args.block))
    print(f"\nfound {len(chains)} ramp chains (min-len {args.min_len}, "
          f"iou>={args.iou_min}, spread>={args.spread_min}"
          + (f", block~='{args.block}'" if args.block else "") + ")\n")
    for n, chain in enumerate(chains[:args.limit], 1):
        members = " ".join(f"U+{int(c.cps[i]):04X}{chr(int(c.cps[i]))}" for i in chain)
        inks = " ".join(str(int(c.ink[i])) for i in chain)
        print(f"#{n} len={len(chain)} [{c.blocks[chain[0]]}]  {members}   ink: {inks}")
        if args.preview:
            print(render_strip(c, chain, per_row=len(chain)))
    return 0


# ---------------------------------------------------------------------------
# fill: outline -> filled pairs
# ---------------------------------------------------------------------------
def interior_mask(g: np.ndarray) -> np.ndarray:
    """Enclosed-hole mask: background not reachable from the border."""
    bg = g == 0
    h, w = g.shape
    reach = np.zeros_like(bg)
    stack = []
    for x in range(w):
        for y in (0, h - 1):
            if bg[y, x] and not reach[y, x]:
                reach[y, x] = True
                stack.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if bg[y, x] and not reach[y, x]:
                reach[y, x] = True
                stack.append((y, x))
    while stack:
        y, x = stack.pop()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and bg[ny, nx] and not reach[ny, nx]:
                reach[ny, nx] = True
                stack.append((ny, nx))
    return bg & ~reach


def batch_interiors(norm: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Vectorized enclosed-hole masks for the whole corpus at once: flood the
    background inward from the border, anything unreached is an interior hole.
    Returns (grids MxNxN bool, interiors MxNxN bool, interior counts M)."""
    g = norm.reshape(-1, N, N).astype(bool)
    bg = ~g
    reach = np.zeros_like(bg)
    reach[:, 0, :] |= bg[:, 0, :]
    reach[:, -1, :] |= bg[:, -1, :]
    reach[:, :, 0] |= bg[:, :, 0]
    reach[:, :, -1] |= bg[:, :, -1]
    for _ in range(N * 2):
        new = reach.copy()
        new[:, 1:, :] |= reach[:, :-1, :]
        new[:, :-1, :] |= reach[:, 1:, :]
        new[:, :, 1:] |= reach[:, :, :-1]
        new[:, :, :-1] |= reach[:, :, 1:]
        new &= bg
        if np.array_equal(new, reach):
            break
        reach = new
    interior = bg & ~reach
    return g, interior, interior.reshape(len(g), -1).sum(1)


def find_fill_pairs(c: Corpus, cover_min: float, fill_min: float, ratio_min: float) -> list[tuple[int, int, float]]:
    # Bucket by SOLID silhouette (ink with holes filled): an outline glyph and its
    # filled twin share the same solid shape, even though their ink/zoning differ.
    grids, interiors, int_n = batch_interiors(c.norm)
    ink = grids.reshape(c.count, -1).sum(1)
    buckets: dict[int, list[int]] = defaultdict(list)
    for i in range(c.count):
        if int_n[i] == 0:
            # no holes -> can only be a fill target; still need a bucket key
            buckets[gf.d4_canonical_crc(grids[i])].append(i)
        else:
            buckets[gf.d4_canonical_crc(grids[i] | interiors[i])].append(i)
    pairs = []
    skipped_big = 0
    for idxs in buckets.values():
        if len(idxs) < 2:
            continue
        if len(idxs) > FILL_BUCKET_CAP:
            # degenerate silhouette (e.g. a full block) shared by many unrelated
            # glyphs -> O(n^2) pairing blows up and yields noise. Skip and log.
            skipped_big += 1
            continue
        for a in idxs:
            if ink[a] == 0 or int_n[a] == 0:      # outline must enclose something
                continue
            ga, inta = grids[a], interiors[a]
            for b in idxs:
                if a == b or ink[b] < ink[a] * ratio_min or int_n[b] >= int_n[a]:
                    continue
                cover = (ga & grids[b]).sum() / float(ink[a])          # A sits inside B
                fill = (grids[b] & inta).sum() / float(int_n[a])        # B fills A's holes
                if cover >= cover_min and fill >= fill_min:
                    pairs.append((a, b, float(cover * fill)))
    if skipped_big:
        sys.stderr.write(f"  [skipped {skipped_big} oversized fill buckets (>{FILL_BUCKET_CAP})]\n")
    pairs.sort(key=lambda t: t[2], reverse=True)
    return pairs


def cmd_fill(c: Corpus, args) -> int:
    pairs = find_fill_pairs(c, args.cover_min, args.fill_min, args.ratio_min)
    print(f"\nfound {len(pairs)} outline->fill pairs\n")
    for n, (a, b, score) in enumerate(pairs[:args.limit], 1):
        print(f"#{n} score={score:.2f}  "
              f"U+{int(c.cps[a]):04X}{chr(int(c.cps[a]))} (ink {int(c.ink[a])}) -> "
              f"U+{int(c.cps[b]):04X}{chr(int(c.cps[b]))} (ink {int(c.ink[b])})   "
              f"{c.names[a]} -> {c.names[b]}")
        if args.preview:
            print(render_strip(c, [a, b], per_row=2))
    return 0


# ---------------------------------------------------------------------------
# cycle: tight near-identical clusters (good to animate one cell)
# ---------------------------------------------------------------------------
def find_cycles(c: Corpus, iou_min: float, min_size: int,
                allow: set[int] | None = None) -> list[list[int]]:
    """Near-identical clusters (single-linkage on raw-norm IoU within zoning
    buckets) — glyphs close enough to cycle in one cell."""
    members_all = [i for i in range(c.count) if allow is None or i in allow]
    parent = {i: i for i in members_all}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    buckets: dict[tuple, list[int]] = defaultdict(list)
    qz = np.clip((c.zoning * 3).astype(int), 0, 2)
    for i in members_all:
        buckets[tuple(qz[i].tolist())].append(i)
    for idxs in buckets.values():
        if len(idxs) < 2 or len(idxs) > SCOPE_CAP:
            continue
        sub = np.array(idxs)
        iou = iou_matrix(c.norm[sub])
        for a in range(len(idxs)):
            for b in range(a + 1, len(idxs)):
                if iou[a, b] >= iou_min:
                    union(idxs[a], idxs[b])
    groups: dict[int, list[int]] = defaultdict(list)
    for i in members_all:
        groups[find(i)].append(i)
    clusters = [g for g in groups.values() if len(g) >= min_size]
    clusters.sort(key=len, reverse=True)
    return clusters


def cmd_cycle(c: Corpus, args) -> int:
    raw_clusters = find_cycles(c, args.iou_min, args.min_size, block_allow(c, getattr(args, "block", None)))
    clusters = [g for g in raw_clusters if len(g) <= MAX_FAMILY]
    skipped = len(raw_clusters) - len(clusters)
    print(f"\nfound {len(clusters)} animatable cycle clusters "
          f"(iou>={args.iou_min}, size>={args.min_size}, max_size<={MAX_FAMILY})\n")
    if skipped:
        print(f"skipped {skipped} oversized visual-neighbour clusters; use families/cycle chains, not blobs.\n")
    for n, g in enumerate(clusters[:args.limit], 1):
        members = " ".join(f"U+{int(c.cps[i]):04X}{chr(int(c.cps[i]))}" for i in g[:MAX_FAMILY])
        print(f"#{n} size={len(g)} [{c.blocks[g[0]]}]  {members}")
        if args.preview:
            print(render_strip(c, g[:args.cols], per_row=args.cols))
    return 0


# ---------------------------------------------------------------------------
# spin: C4 rotation orbits (spinners) — same shape at rotation phases
# ---------------------------------------------------------------------------
def _c4_key(g: np.ndarray) -> bytes:
    a = g
    best = None
    for _ in range(4):
        b = np.ascontiguousarray(a).tobytes()
        if best is None or b < best:
            best = b
        a = np.rot90(a)
    return best


def _rot_phase(g: np.ndarray, key: bytes) -> int:
    a = g
    for k in range(4):
        if np.ascontiguousarray(a).tobytes() == key:
            return k
        a = np.rot90(a)
    return 4


def find_spins(c: Corpus, min_size: int, allow: set[int] | None = None) -> list[list[int]]:
    """Spinner = a set of glyphs that are exact 90-degree rotations of one shape
    (C4 orbit). Cycling them animates rotation. Grouped by C4-canonical key of
    the normalized grid; only distinct rotation frames are kept."""
    groups: dict[bytes, list[int]] = defaultdict(list)
    for i in range(c.count):
        if allow is not None and i not in allow:
            continue
        groups[_c4_key(c.norm[i].reshape(N, N))].append(i)
    out: list[list[int]] = []
    for key, idxs in groups.items():
        seen: set[bytes] = set()
        frames: list[int] = []
        for i in idxs:
            b = c.norm[i].tobytes()
            if b in seen:        # identical frame (e.g. rotationally symmetric) -> skip dup
                continue
            seen.add(b)
            frames.append(i)
        if len(frames) < min_size:
            continue
        frames.sort(key=lambda i: _rot_phase(c.norm[i].reshape(N, N), key))
        out.append(frames)
    out.sort(key=len, reverse=True)
    return out


def cmd_spin(c: Corpus, args) -> int:
    spins = find_spins(c, args.min_size, allow=block_allow(c, args.block))
    print(f"\nfound {len(spins)} spinner orbits (C4 rotation, size>={args.min_size}"
          + (f", block~='{args.block}'" if args.block else "") + ")\n")
    for n, g in enumerate(spins[:args.limit], 1):
        members = " ".join(f"U+{int(c.cps[i]):04X}{chr(int(c.cps[i]))}" for i in g)
        print(f"#{n} size={len(g)} [{c.blocks[g[0]]}]  {members}")
        if args.preview:
            print(render_strip(c, g, per_row=min(len(g), args.cols)))
    return 0


# ---------------------------------------------------------------------------
# morph: seeded shape-neighbours ordered along a morphology axis (bendiness…)
# ---------------------------------------------------------------------------
def _axis_value(c: Corpus, i: int, axis: str) -> float:
    if axis == "ink":
        return float(c.ink[i])
    from generate_glyph_shape_catalog import analyze_grid
    m = analyze_grid(c.raw[i].reshape(N, N).tolist())
    if axis == "bend":            # curve_score - corner_score: sharp(-) -> soft(+)
        return m["curve_score"] - m["corner_score"]
    if axis == "curve":
        return m["curve_score"]
    if axis == "corner":
        return m["corner_score"]
    if axis == "density":
        return m["density"]
    raise SystemExit(f"unknown axis: {axis}")


def cmd_morph(c: Corpus, args) -> int:
    """Take a seed glyph, gather its shape-neighbours (default lens=hu, which
    can match shape across scripts where pixel-IoU does not), then order that
    local neighbourhood along a morphology axis. With --lens topo / --axis
    topo_soft this browses by skeleton structure; it is still a SEEDED tool —
    global topology family clustering (caching every fingerprint) is gated."""
    qi = c.i(parse_cp(args.query))
    if qi is None:
        raise SystemExit(f"{args.query} not in cache")
    topo = _Topo() if (args.lens == "topo" or args.axis == "topo_soft") else None
    if args.lens == "topo":
        scored = topo_rank(c, qi, args.prefilter, topo)
        neigh = [i for i, _ in scored[:args.k]]
        dmap = {i: dd for i, dd in scored}
        dmap[qi] = 0.0
        def dget(i): return dmap.get(i, float("nan"))
    else:
        d = distances(c, qi, args.lens)
        neigh = [i for i in np.argsort(d) if i != qi][:args.k]
        def dget(i): return d[i]
    members = [qi] + neigh
    if args.axis == "topo_soft":
        axis_values = {i: topo.gs.topo_bend(topo.fp(int(c.cps[i]))) for i in members}
    else:
        axis_values = {i: _axis_value(c, i, args.axis) for i in members}
    members.sort(key=lambda i: axis_values[i], reverse=args.desc)
    arrow = "high→low" if args.desc else "low→high"
    print(f"\nseed U+{int(c.cps[qi]):04X} {chr(int(c.cps[qi]))}  lens={args.lens}  "
          f"axis={args.axis} ({arrow})\n")
    for i in members:
        mark = " *" if i == qi else "  "
        print(f"{mark} {args.axis}={axis_values[i]:+.3f}  "
              f"U+{int(c.cps[i]):04X} {chr(int(c.cps[i]))}  d={dget(i):.3f}  {c.names[i]}")
    print("\n  animate it:  python3 scripts/glyph_audit.py anim "
          + " ".join(f"U+{int(c.cps[i]):04X}" for i in members))
    if args.preview:
        print()
        print(render_strip(c, members, per_row=args.cols))
    return 0


# ---------------------------------------------------------------------------
# topo families (Phase 3): global clustering over the cached topology
# fingerprints. Structurally-similar glyphs cluster ACROSS scripts (the whole
# point — し-like hooks, J-forms, …), ordered sharp->soft so animating a family
# morphs the hook. Reads the gated cache built by glyph_skeleton.py cache.
# ---------------------------------------------------------------------------
TOPO_BUCKET_CAP = 500   # max rows per signature bucket fed to O(n^2) linkage
TOPO_LINK = 1.5         # complete-linkage topo_distance threshold (tight families)


def find_topo_families(c: Corpus, link: float = TOPO_LINK, min_size: int = 2,
                       morph_only: bool = False, spread_min: float = 2.0,
                       allow: set[int] | None = None) -> list[list[int]]:
    """Cluster the global fingerprint cache into structural families, returned
    as corpus-index lists ordered sharp->soft, best-first (cross-script and
    wide softness-spread surface first). Coarse signature buckets bound the
    O(n^2) linkage; oversized buckets are skipped and logged (a known coverage
    limit, not silent truncation — in practice max bucket << cap, so none skip).

    morph_only keeps only families whose sharp->soft softness spans >= spread_min
    (animation-ready morph chains), ordered widest-spread first; this drops the
    many near-identical cross-script clusters that look static when animated."""
    import glyph_skeleton as gs
    d, _meta = gs.load_topo_cache()
    cps, struct, scal, turn, ok = d["cps"], d["struct"], d["scal"], d["turn"], d["ok"]
    rows = [r for r in range(len(cps)) if ok[r] and c.i(int(cps[r])) is not None
            and (allow is None or c.i(int(cps[r])) in allow)]
    fcache: dict[int, dict] = {}

    def fp(r: int) -> dict:
        f = fcache.get(r)
        if f is None:
            f = gs.fp_from_row(struct[r], scal[r], turn[r])
            fcache[r] = f
        return f

    # signature = structural counts + binned proportion/curvature -> small buckets
    buckets: dict[tuple, list[int]] = defaultdict(list)
    for r in rows:
        e, b, cmp = int(struct[r][0]), int(struct[r][1]), int(struct[r][2])
        straight, aspect, winding, peak = (float(scal[r][1]), float(scal[r][2]),
                                           float(scal[r][3]), float(scal[r][6]))
        sig = (e, b, cmp,
               min(3, int(aspect * 1.5)), min(4, int(straight * 5)),
               min(4, int(winding / 1.5)), min(3, int(peak * 4)))
        buckets[sig].append(r)

    fams: list[tuple[int, float, list[int]]] = []  # (-n_blocks, -spread, members)
    skipped = 0
    for rs in buckets.values():
        if len(rs) < min_size:
            continue
        if len(rs) > TOPO_BUCKET_CAP:
            skipped += 1
            continue
        rs_sorted = sorted(rs, key=lambda r: gs.topo_bend(fp(r)))
        used: set[int] = set()
        for seed in rs_sorted:
            if seed in used:
                continue
            cluster = [seed]
            for r in rs_sorted:
                if r == seed or r in used:
                    continue
                if all(gs.topo_distance(fp(r), fp(m)) <= link for m in cluster):
                    cluster.append(r)
            for m in cluster:
                used.add(m)
            if not (min_size <= len(cluster) <= MAX_FAMILY):
                continue
            members = sorted(cluster, key=lambda r: gs.topo_bend(fp(r)))
            ci = [c.i(int(cps[r])) for r in members]
            softs = [gs.topo_bend(fp(r)) for r in members]
            n_blocks = len({c.blocks[i] for i in ci})
            spread = max(softs) - min(softs)
            fams.append((n_blocks, spread, ci))
    if skipped:
        sys.stderr.write(f"  [skipped {skipped} oversized topo buckets (>{TOPO_BUCKET_CAP}); "
                         f"finer bins or per-block scope would recover them]\n")
    if morph_only:
        fams = [t for t in fams if t[1] >= spread_min]
        fams.sort(key=lambda t: t[1], reverse=True)      # widest sharp->soft first
    else:
        fams.sort(key=lambda t: (t[0], t[1]), reverse=True)  # cross-script, then spread
    return [ci for _nb, _sp, ci in fams]


# ---------------------------------------------------------------------------
# unified family collection (used by `families`, `anim --<mode> N`, the viewer)
# ---------------------------------------------------------------------------
MAX_FAMILY = 16  # an animatable ramp/cycle is small; bigger = degenerate cluster, drop it


def collect_families(c: Corpus, modes=("spin", "ramp", "fill", "cycle", "topo"),
                     per_mode: int = 300, block: str | None = None,
                     topo_morph_only: bool = False) -> list[dict]:
    """Aggregate every discovery mode into one uniform family list. Each family:
    {mode, members:[corpus indices, in display order], block, size}.

    Ramp/cycle families larger than MAX_FAMILY are degenerate clusters (e.g. a
    whole CJK block lumped together) — useless to animate — so they are dropped;
    a real spinner/ramp/cycle has a handful of frames. Each mode's families are
    surfaced smallest-first so the clean ones are reachable immediately."""
    allow = block_allow(c, block)
    fams: list[dict] = []

    def add(mode, members):
        members = list(members)
        if not (2 <= len(members) <= MAX_FAMILY):
            return
        # drop families with control/format/surrogate glyphs (render as ^Q junk)
        if any(unicodedata.category(chr(int(c.cps[i])))[0] == "C" for i in members):
            return
        fams.append({"mode": mode, "members": members,
                     "block": c.blocks[members[0]], "size": len(members)})

    if "spin" in modes:
        # find_spins already returns largest-first (full 4-frame rotations first)
        for g in find_spins(c, 3, allow)[:per_mode]:
            add("spin", g)
    if "ramp" in modes:
        # tightest (smallest) ramps first — a 4-step blade ramp reads cleaner than
        # a 16-member orientation cluster
        ramps = [g for g in find_ramps(c, 3, 0.60, 1.2, allow) if len(g) <= MAX_FAMILY]
        for g in sorted(ramps, key=len)[:per_mode]:
            add("ramp", g)
    if "fill" in modes:
        for a, b, _s in find_fill_pairs(c, 0.70, 0.45, 1.0)[:per_mode]:
            if allow is None or (a in allow and b in allow):
                add("fill", [a, b])
    if "cycle" in modes:
        # longest cycles first — more near-identical frames = smoother cell anim
        cycles = [g for g in find_cycles(c, 0.78, 3, allow) if len(g) <= MAX_FAMILY]
        for g in sorted(cycles, key=len, reverse=True)[:per_mode]:
            add("cycle", g)
    if "topo" in modes:
        # global skeleton-topology families (cross-script hooks/structure),
        # already best-first; fail soft if the gated cache is not built.
        try:
            for ci in find_topo_families(c, morph_only=topo_morph_only)[:per_mode]:
                if allow is None or all(i in allow for i in ci):
                    add("topo", ci)
        except SystemExit as e:
            sys.stderr.write(f"  [topo families unavailable: {e}]\n")
    return fams


def _validate_topo_families(c: Corpus) -> int:
    """Phase-3 gate: the GLOBAL topo pass must produce real cross-script
    families (a structural family spanning >=3 distinct Unicode blocks) — proof
    the clustering groups by SHAPE, not by script/codepoint. Returns nonzero if
    the cache is missing or block-scoped (it must be the full global pass)."""
    import glyph_skeleton as gs
    try:
        _d, meta = gs.load_topo_cache()
    except SystemExit as e:
        print(f"FAIL  topo Phase-3: {e}")
        return 1
    if meta.get("only_block"):
        print(f"FAIL  topo Phase-3: cache is block-scoped ({meta['only_block']!r}); "
              f"rebuild global with `glyph_skeleton.py cache`")
        return 1
    fams = find_topo_families(c)
    cross = [ci for ci in fams if len({c.blocks[i] for i in ci}) >= 3]
    print(f"global topo families: {len(fams)}   cross-script (>=3 blocks): {len(cross)}")
    if cross:
        ci = cross[0]
        blks = sorted({c.blocks[i] for i in ci})
        print(f"  e.g. {len(blks)} blocks: " + ", ".join(blks[:5])
              + (" …" if len(blks) > 5 else ""))
        print(f"       " + " ".join(f"{chr(int(c.cps[i]))}" for i in ci[:12]))
    ok = len(cross) >= 1
    print("\n" + ("PASS  global topology clustering is cross-script"
                  if ok else "FAIL  no cross-script families — clustering degenerated"))
    return 0 if ok else 1


def cmd_families(c: Corpus, args) -> int:
    modes = tuple(args.modes.split(",")) if args.modes else ("spin", "ramp", "fill", "cycle", "topo")
    if getattr(args, "validate", False):
        return _validate_topo_families(c)
    fams = collect_families(c, modes=modes, block=args.block,
                            per_mode=args.per_mode, topo_morph_only=args.morph_only)
    counts = defaultdict(int)
    for f in fams:
        counts[f["mode"]] += 1
    print(f"\n{len(fams)} families  (" + ", ".join(f"{m}:{counts[m]}" for m in modes) + ")"
          + (f"  block~='{args.block}'" if args.block else "") + "\n")
    if args.json:
        rows = [{"mode": f["mode"], "block": f["block"], "size": f["size"],
                 "cps": [int(c.cps[i]) for i in f["members"]],
                 "chars": "".join(chr(int(c.cps[i])) for i in f["members"])} for f in fams]
        out = gf.CACHE_DIR / "families.jsonl"
        out.write_text("\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""))
        meta = {
            "schema": 1,
            "modes": list(modes),
            "block": args.block,
            "count": len(rows),
            "max_family": MAX_FAMILY,
        }
        meta_out = gf.CACHE_DIR / "families.meta.json"
        meta_out.write_text(json.dumps(meta, sort_keys=True) + "\n")
        print(f"wrote {out} ({len(rows)} families); metadata {meta_out}")
        return 0
    for n, f in enumerate(fams[:args.limit], 1):
        members = " ".join(f"U+{int(c.cps[i]):04X}{chr(int(c.cps[i]))}" for i in f["members"][:14])
        print(f"#{n:4} [{f['mode']:5}] size={f['size']:<3} [{f['block']}]  {members}")
    return 0


# ---------------------------------------------------------------------------
# anim: cycle a sequence of glyphs in place so you can WATCH a spinner/ramp
# ---------------------------------------------------------------------------
def _family_pick(c: Corpus, mode: str, index: int, block: str | None) -> list[int] | None:
    fams = collect_families(c, modes=(mode,), block=block)
    if index < 1 or index > len(fams):
        sys.stderr.write(f"  {mode} #{index} out of range (found {len(fams)})\n")
        return None
    return fams[index - 1]["members"]


def cmd_anim(c: Corpus, args) -> int:
    picks = [(m, getattr(args, m)) for m in ("spin", "ramp", "cycle", "fill") if getattr(args, m)]
    if picks:
        mode, index = picks[0]
        idxs = _family_pick(c, mode, index, args.block)
        if not idxs:
            return 1
        print(f"  animating {mode} #{index}: "
              + " ".join(f"U+{int(c.cps[i]):04X}{chr(int(c.cps[i]))}" for i in idxs))
    else:
        idxs = []
        for token in args.cps:
            i = c.i(parse_cp(token))
            if i is None:
                sys.stderr.write(f"  {token} not in cache (build the block / --all first)\n")
            else:
                idxs.append(i)
    if not idxs:
        sys.stderr.write("  nothing to animate (give codepoints or --spin/--ramp/--cycle/--fill N)\n")
        return 1
    grids = getattr(c, args.which)
    frames = [grids[i].reshape(N, N) for i in idxs]
    height = N + 2
    delay = 1.0 / max(0.1, args.fps)
    sys.stdout.write("\033[?25l")  # hide cursor
    first = True
    loops = 0
    try:
        while args.loops == 0 or loops < args.loops:
            for k, g in enumerate(frames):
                if not first:
                    sys.stdout.write(f"\033[{height}A")
                first = False
                cp = int(c.cps[idxs[k]])
                sys.stdout.write(f"\033[2K  frame {k + 1}/{len(frames)}  U+{cp:04X} {chr(cp)}"
                                 f"  ({c.blocks[idxs[k]]})\n")
                for row in g:
                    sys.stdout.write("\033[2K  " + "".join("██" if v else "  " for v in row) + "\n")
                sys.stdout.write("\033[2K  [Ctrl+C to stop]\n")
                sys.stdout.flush()
                time.sleep(delay)
            loops += 1
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h\n")  # restore cursor
        sys.stdout.flush()
    return 0


# ---------------------------------------------------------------------------
# validate: known hand-found examples must fall out
# ---------------------------------------------------------------------------
def cmd_validate(c: Corpus, args) -> int:
    ok = True
    ogham = [0x1686, 0x1687, 0x1688, 0x1689]

    # 1. similar(ᚆ) should surface the other Ogham blades
    qi = c.i(0x1686)
    if qi is None:
        print("FAIL  ᚆ U+1686 not in corpus"); ok = False
    else:
        d = distances(c, qi, "iou_dil")
        order = [int(c.cps[i]) for i in np.argsort(d) if i != qi][:10]
        hit = [cp for cp in ogham[1:] if cp in order]
        print(f"{'PASS' if len(hit) >= 2 else 'FAIL'}  similar(ᚆ, iou_dil) top-10 contains "
              f"{len(hit)}/3 other Ogham blades: {[f'U+{x:04X}' for x in hit]}")
        ok &= len(hit) >= 2

    # 2. a ramp chain should contain >=3 Ogham blades
    chains = find_ramps(c, min_len=3, iou_min=args.iou_min, spread_min=args.spread_min)
    best = 0
    best_chain = None
    for ch in chains:
        cps = [int(c.cps[i]) for i in ch]
        hit = len(set(cps) & set(ogham))
        if hit > best:
            best, best_chain = hit, cps
    print(f"{'PASS' if best >= 3 else 'FAIL'}  ramp chain contains {best}/4 Ogham blades"
          + (f": {[f'U+{x:04X}' for x in best_chain if x in ogham]}" if best_chain else ""))
    ok &= best >= 3

    # 3. a fill pair WHITE CLUB (U+2667) -> BLACK CLUB (U+2663)
    pairs = find_fill_pairs(c, args.cover_min, args.fill_min, args.ratio_min)
    found = any(int(c.cps[a]) == 0x2667 and int(c.cps[b]) == 0x2663 for a, b, _s in pairs)
    print(f"{'PASS' if found else 'FAIL'}  fill pair ♧U+2667 -> ♣U+2663 detected")
    ok &= found

    # 4. a spinner orbit containing the four half-circles ◐◑◒◓
    half = {0x25D0, 0x25D1, 0x25D2, 0x25D3}
    spins = find_spins(c, min_size=3)
    sbest = 0
    for g in spins:
        sbest = max(sbest, len(half & {int(c.cps[i]) for i in g}))
    print(f"{'PASS' if sbest >= 3 else 'FAIL'}  spinner orbit contains {sbest}/4 half-circles ◐◑◒◓")
    ok &= sbest >= 3

    # 5. topo lens (Phase 2): exact topology twin + structural cleanliness +
    #    separation from a random baseline. Seed = first available glyph that has
    #    a guaranteed exact topology twin (Latin letters all have Mathematical
    #    Alphanumeric variants). If NONE are cached the check FAILS rather than
    #    skipping — it must never pass silently without exercising the lens.
    seed_cps = [0x004A, 0x0043, 0x0053, 0x004C, 0x004F]  # J C S L O -> math twins
    ti = next((c.i(s) for s in seed_cps if c.i(s) is not None), None)
    if ti is None:
        print(f"FAIL  topo: none of seeds {[f'U+{x:04X}' for x in seed_cps]} in corpus "
              f"(build --all to validate Phase 2)")
        ok = False
    else:
        seed_cp = int(c.cps[ti])
        topo = _Topo()
        scored = topo_rank(c, ti, 400, topo)
        if not scored:
            print("FAIL  topo: J produced no ranked neighbours"); ok = False
        else:
            top = scored[:5]
            twin_d = top[0][1]
            comp_ok = sum(1 for i, _ in top if int(c.ncomp[i]) == 1) >= 4
            import random
            rng = random.Random(7)
            pool = rng.sample(range(c.count), min(200, c.count))
            qfp = topo.fp(seed_cp)
            rdists = [topo.gs.topo_distance(qfp, topo.fp(int(c.cps[i])))
                      for i in pool if topo.fp(int(c.cps[i]))["ok"]]
            top_mean = sum(d for _, d in top) / len(top)
            rand_mean = sum(rdists) / len(rdists) if rdists else float("inf")
            tw, sep = twin_d < 0.5, rand_mean > top_mean * 1.5
            sd = f"U+{seed_cp:04X} {chr(seed_cp)}"
            print(f"{'PASS' if tw else 'FAIL'}  topo: {sd} top-1 exact-ish twin "
                  f"(d={twin_d:.3f} < 0.5)  [{c.names[top[0][0]]}]")
            print(f"{'PASS' if comp_ok else 'FAIL'}  topo: {sd} top-5 single-component "
                  f"(structurally clean strokes)")
            print(f"{'PASS' if sep else 'FAIL'}  topo: top-5 mean {top_mean:.3f} << "
                  f"random mean {rand_mean:.3f} (lens separates)")
            ok &= tw and comp_ok and sep

    print("\n" + ("ALL CHECKS PASS" if ok else "SOME CHECKS FAILED — tune thresholds"))
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# consume (Phase 1): restrict discovery to glyphs ALREADY admitted to the atlas,
# so the resulting families are directly renderable. Emits a dry-run proposal;
# it does NOT mutate the runtime material profiles (that is the gated Step 2 via
# seed_material_rendering_profiles.py).
# ---------------------------------------------------------------------------
FIXTURE_GLOB = "assets/glyphs/fixtures/extended_glyph_*.json"


def load_admitted() -> dict[int, int]:
    """Map admitted unicode scalar -> glyph_id, from the atlas fixture manifests
    (the admission source of truth fed to compile_glyph_manifest.py)."""
    import glob
    repo = SCRIPTS_DIR.parent
    gid_of: dict[int, int] = {}
    for f in sorted(glob.glob(str(repo / FIXTURE_GLOB))):
        try:
            d = json.loads(Path(f).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        for e in d.get("entries", []):
            if not isinstance(e, dict):
                continue
            cp, gid = e.get("unicode_scalar"), e.get("glyph_id")
            if isinstance(cp, int) and isinstance(gid, int):
                gid_of[cp] = gid
    return gid_of


def cmd_consume(c: Corpus, args) -> int:
    gid_of = load_admitted()
    allow = {c.i(cp) for cp in gid_of if c.i(cp) is not None}
    print(f"\nadmitted glyphs: {len(gid_of)}   in corpus: {len(allow)}   "
          f"(families below use ONLY renderable glyphs)\n")
    fams: list[dict] = []

    def emit(mode, members):
        members = [m for m in members if m is not None]
        if not (2 <= len(members) <= MAX_FAMILY):
            return
        fams.append({"mode": mode,
                     "cps": [int(c.cps[i]) for i in members],
                     "glyph_ids": [gid_of[int(c.cps[i])] for i in members],
                     "chars": "".join(chr(int(c.cps[i])) for i in members),
                     "blocks": sorted({c.blocks[i] for i in members})})

    for g in sorted([x for x in find_ramps(c, 3, 0.60, 1.2, allow) if len(x) <= MAX_FAMILY],
                    key=len)[:args.per_mode]:
        emit("ramp", g)
    for g in sorted([x for x in find_cycles(c, 0.78, 3, allow) if len(x) <= MAX_FAMILY],
                    key=len, reverse=True)[:args.per_mode]:
        emit("cycle", g)
    for g in find_spins(c, 3, allow)[:args.per_mode]:
        emit("spin", g)
    try:
        for ci in find_topo_families(c, allow=allow)[:args.per_mode]:
            emit("topo", ci)
    except SystemExit as e:
        sys.stderr.write(f"  [topo unavailable: {e}]\n")

    counts = defaultdict(int)
    for f in fams:
        counts[f["mode"]] += 1
    print("renderable families  (" + ", ".join(f"{m}:{counts[m]}" for m in
          ("ramp", "cycle", "spin", "topo")) + ")\n")
    for n, f in enumerate(fams[:args.limit], 1):
        gids = " ".join(str(g) for g in f["glyph_ids"][:10])
        print(f"#{n:3} [{f['mode']:5}] {f['chars'][:14]:14}  glyph_ids: {gids}")
    if args.json:
        out = gf.CACHE_DIR / "consume_proposal.json"
        out.write_text(json.dumps({"schema": 1, "admitted": len(gid_of),
                                   "families": fams}, indent=2) + "\n")
        print(f"\nwrote dry-run proposal: {out}")
        print("  (Step 2 — reseed material_rendering_profiles.v1.json — is gated; "
              "not performed by this command)")
    return 0


# ---------------------------------------------------------------------------
# export-candidates (Phase 1 seam): turn families into candidate data the
# EXISTING atlas/material pipeline ingests. A family discovered over full
# Unicode usually references glyphs not yet in the atlas — those MUST be
# admitted. This emits (a) a PROPOSED extended fixture = current morphology-v2
# fixture + new admittable glyphs (BMP & in the pack font), ready for
# compile_glyph_manifest.py, and (b) material candidate pools as glyph_ids.
# It writes only to .run/ — it is NOT a new runtime owner and mutates nothing
# the renderer reads until a human/gated step compiles the proposed fixture.
# ---------------------------------------------------------------------------
MORPH_V2_FIXTURE = "assets/glyphs/fixtures/extended_glyph_material_morphology_v2.json"


def _coverage_quadrants(grid16: np.ndarray) -> int:
    """4x4 occupancy bitmask (matches the fixture's coverage_quadrants field):
    bit qy*4+qx set if that 4x4 block of the 16x16 cell has any ink."""
    g = grid16.reshape(N, N)
    bits = 0
    for qy in range(4):
        for qx in range(4):
            if g[qy * 4:qy * 4 + 4, qx * 4:qx * 4 + 4].any():
                bits |= 1 << (qy * 4 + qx)
    return bits


def _entry_label(cp: int) -> str:
    nm = unicodedata.name(chr(cp), f"U+{cp:04X}").replace(" ", "_")[:28]
    return f"FAM_{nm}_U+{cp:04X}"


def _source_families(c: Corpus, args) -> list[list[int]]:
    """Source families as corpus-index lists, from saved viewer picks or topo."""
    if args.source == "saved":
        path = gf.CACHE_DIR / "saved_families.jsonl"
        if not path.exists():
            raise SystemExit(f"no saved families at {path} — save some in the viewer ('s')")
        out = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            cps = json.loads(line).get("cps", [])
            members = [c.i(int(cp)) for cp in cps]
            members = [m for m in members if m is not None]
            if len(members) >= 2:
                out.append(members)
        return out
    # source == topo
    return find_topo_families(c, morph_only=args.morph_only)[:args.per_mode]


def cmd_export_candidates(c: Corpus, args) -> int:
    import glyph_morphology_browser as gmb
    gid_of = load_admitted()
    chain = gmb.discover_font_chain()
    pack_cmap = gmb.GlyphScorer._load_cmap(chain[0]) if chain else None  # unifont (BMP)
    next_gid = (max(gid_of.values()) + 1) if gid_of else 512
    new_entries: dict[int, dict] = {}   # cp -> fixture entry
    new_assign: dict[int, int] = {}     # cp -> minted glyph_id

    def resolve(cp: int) -> tuple[int | None, str]:
        nonlocal next_gid
        if cp in gid_of:
            return gid_of[cp], "admitted"
        if cp in new_assign:
            return new_assign[cp], "new"
        if cp >= 0x10000 or (pack_cmap is not None and cp not in pack_cmap):
            return None, "blocked"      # not renderable by the pack font (needs 2nd atlas)
        gid = next_gid
        next_gid += 1
        new_assign[cp] = gid
        i = c.i(cp)
        new_entries[cp] = {
            "glyph_id": gid, "label": _entry_label(cp), "unicode_scalar": cp,
            "coverage_quadrants": _coverage_quadrants(c.raw[i]) if i is not None else 0,
            "coverage_hint": "partial", "cell_width_em": 1.0,
        }
        return gid, "new"

    fams = _source_families(c, args)
    out_fams = []
    n_full = n_partial = 0
    for members in fams:
        ids, statuses = [], []
        for i in members:
            cp = int(c.cps[i])
            gid, st = resolve(cp)
            ids.append(gid)
            statuses.append(st)
        blocked = statuses.count("blocked")
        out_fams.append({
            "chars": "".join(chr(int(c.cps[i])) for i in members),
            "cps": [int(c.cps[i]) for i in members],
            "glyph_ids": ids, "status": statuses,
            "realizable": blocked == 0,
        })
        n_full += blocked == 0
        n_partial += blocked > 0

    outdir = gf.CACHE_DIR / "export"
    outdir.mkdir(parents=True, exist_ok=True)
    # (a) proposed extended fixture = existing morphology-v2 + new admittable glyphs
    base = json.loads((SCRIPTS_DIR.parent / MORPH_V2_FIXTURE).read_text())
    added = sorted(new_entries.values(), key=lambda e: e["glyph_id"])
    base["entries"] = base["entries"] + added
    base["admission_set"] = base["admission_set"] + [e["glyph_id"] for e in added]
    base["_comment"] = (base.get("_comment", "")
                        + " | + FL-4208 candidate additions (PROPOSED, uncompiled)")
    prop = outdir / "extended_glyph_material_morphology_v2.PROPOSED.json"
    prop.write_text(json.dumps(base, indent=2, sort_keys=True) + "\n")
    # (b) material candidate pools (glyph_ids), for seed_material_rendering_profiles
    cand = outdir / "material_candidate_pools.json"
    cand.write_text(json.dumps({"schema": 1, "source": args.source,
                                "families": out_fams}, indent=2) + "\n")

    print(f"\nsource={args.source}  families={len(fams)}  "
          f"fully-realizable={n_full}  partial(needs 2nd atlas)={n_partial}")
    print(f"glyphs to admit (new, BMP & in pack font): {len(new_entries)}  "
          + (f"glyph_id {min(new_assign.values())}..{max(new_assign.values())}"
             if new_assign else "(none)"))
    blocked_cps = {int(c.cps[i]) for m in fams for i in m
                   if (int(c.cps[i]) >= 0x10000 or
                       (pack_cmap is not None and int(c.cps[i]) not in pack_cmap))
                   and int(c.cps[i]) not in gid_of}
    if blocked_cps:
        print(f"blocked (SMP / not in pack font, cannot admit here): {len(blocked_cps)} "
              "— need a 2nd atlas+binding")
    print(f"\nPROPOSED fixture: {prop}")
    print(f"material candidates: {cand}")
    print("  Import (gated): review, copy PROPOSED over the real fixture, then "
          "`compile_glyph_manifest.py --compile`; reseed profiles separately.")
    return 0


def parse_cp(s: str) -> int:
    s = s.strip()
    if s.lower().startswith("u+"):
        return int(s[2:], 16)
    if len(s) == 1:
        return ord(s)
    return int(s, 16)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("similar", help="nearest neighbours of a glyph")
    p.add_argument("query")
    p.add_argument("--lens", default="iou_dil",
                   help="iou_dil/iou_norm/iou/hamming/hu/zoning/orient/topo "
                        "(topo = skeleton-topology, cross-script structure)")
    p.add_argument("-k", type=int, default=12)
    p.add_argument("--cols", type=int, default=6)
    p.add_argument("--prefilter", type=int, default=400,
                   help="topo lens: Hu-prefilter pool size before fingerprinting")

    p = sub.add_parser("ramp", help="density / stroke-count ramps")
    p.add_argument("--min-len", type=int, default=3)
    p.add_argument("--iou-min", type=float, default=0.60)
    p.add_argument("--spread-min", type=float, default=1.2)
    p.add_argument("--block", type=str, default=None, help="scope to a block (crisper ramps)")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--preview", action="store_true")

    p = sub.add_parser("fill", help="outline -> filled pairs")
    p.add_argument("--cover-min", type=float, default=0.70)
    p.add_argument("--fill-min", type=float, default=0.45)
    p.add_argument("--ratio-min", type=float, default=1.0)
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--preview", action="store_true")

    p = sub.add_parser("cycle", help="near-identical clusters for cell animation")
    p.add_argument("--iou-min", type=float, default=0.78)
    p.add_argument("--min-size", type=int, default=2)
    p.add_argument("--block", type=str, default=None, help="scope to a block")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--cols", type=int, default=6)
    p.add_argument("--preview", action="store_true")

    p = sub.add_parser("spin", help="C4 rotation orbits (spinners)")
    p.add_argument("--min-size", type=int, default=3)
    p.add_argument("--block", type=str, default=None, help="scope to a block")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--cols", type=int, default=8)
    p.add_argument("--preview", action="store_true")

    p = sub.add_parser("anim", help="animate a glyph sequence in place (watch a spinner/ramp)")
    p.add_argument("cps", nargs="*", help="codepoints in cycle order, e.g. U+2199 U+2196 U+2197 U+2198")
    p.add_argument("--spin", type=int, default=0, metavar="N", help="animate spinner family #N")
    p.add_argument("--ramp", type=int, default=0, metavar="N", help="animate ramp family #N")
    p.add_argument("--cycle", type=int, default=0, metavar="N", help="animate cycle family #N")
    p.add_argument("--fill", type=int, default=0, metavar="N", help="animate fill pair #N")
    p.add_argument("--block", type=str, default=None, help="scope family selection to a block")
    p.add_argument("--fps", type=float, default=6.0)
    p.add_argument("--loops", type=int, default=0, help="0 = loop until Ctrl+C")
    p.add_argument("--which", choices=["raw", "norm"], default="raw",
                   help="raw = true rendered glyph; norm = centered shape")

    p = sub.add_parser("morph", help="seed -> shape-neighbours ordered by a morphology axis")
    p.add_argument("query")
    p.add_argument("--lens", default="hu",
                   help="neighbour lens (hu best for cross-script shape; topo = "
                        "skeleton-topology, the global hook/structure axis)")
    p.add_argument("--axis", default="bend",
                   choices=["bend", "curve", "corner", "ink", "density", "topo_soft"],
                   help="topo_soft = sharp->soft hook via skeleton curvature spread")
    p.add_argument("--desc", action="store_true", help="order high->low instead of low->high")
    p.add_argument("-k", type=int, default=12)
    p.add_argument("--cols", type=int, default=7)
    p.add_argument("--prefilter", type=int, default=400,
                   help="topo lens: Hu-prefilter pool size before fingerprinting")
    p.add_argument("--preview", action="store_true")

    p = sub.add_parser("families", help="list ALL families across every mode")
    p.add_argument("--modes", type=str, default=None, help="comma list e.g. spin,ramp (default all)")
    p.add_argument("--block", type=str, default=None)
    p.add_argument("--limit", type=int, default=80)
    p.add_argument("--per-mode", type=int, default=300, dest="per_mode",
                   help="max families kept per mode for the catalog (raise to view all)")
    p.add_argument("--morph-only", action="store_true", dest="morph_only",
                   help="topo: keep only wide sharp->soft morph chains (animation-ready)")
    p.add_argument("--json", action="store_true", help="export to .run/glyph_audit/families.jsonl")
    p.add_argument("--validate", action="store_true",
                   help="Phase-3 gate: assert global topo families are cross-script")

    p = sub.add_parser("consume", help="Phase 1: families restricted to ATLAS-ADMITTED "
                                       "glyphs (directly renderable) + dry-run proposal")
    p.add_argument("--per-mode", type=int, default=200, dest="per_mode")
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--json", action="store_true",
                   help="write .run/glyph_audit/consume_proposal.json (no runtime mutation)")

    p = sub.add_parser("export-candidates",
                       help="Phase 1 seam: families -> PROPOSED atlas fixture additions "
                            "+ material candidate pools (dry-run, .run/ only)")
    p.add_argument("--source", choices=["saved", "topo"], default="saved",
                   help="saved = families saved in the viewer ('s'); topo = global topo families")
    p.add_argument("--morph-only", action="store_true", dest="morph_only",
                   help="topo source: only wide sharp->soft chains")
    p.add_argument("--per-mode", type=int, default=80, dest="per_mode")

    p = sub.add_parser("validate", help="check known hand-found examples")
    p.add_argument("--iou-min", type=float, default=0.60)
    p.add_argument("--spread-min", type=float, default=1.2)
    p.add_argument("--cover-min", type=float, default=0.70)
    p.add_argument("--fill-min", type=float, default=0.45)
    p.add_argument("--ratio-min", type=float, default=1.0)

    args = ap.parse_args()
    c = Corpus()
    return {
        "similar": cmd_similar, "ramp": cmd_ramp, "fill": cmd_fill,
        "cycle": cmd_cycle, "spin": cmd_spin, "anim": cmd_anim, "morph": cmd_morph,
        "families": cmd_families, "validate": cmd_validate, "consume": cmd_consume,
        "export-candidates": cmd_export_candidates,
    }[args.cmd](c, args)


if __name__ == "__main__":
    raise SystemExit(main())
