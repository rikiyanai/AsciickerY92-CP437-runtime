#!/usr/bin/env python3
"""FL-4907 RASTER lane: Python reference of the P5 native row layout.

This file exercises pure native-row helpers shared by the shaders. It is
not a model of the complete P1 admission/certificate pipeline and cannot
establish that the renderer admits or draws a complete world. It covers:

1. bin sites to native rows (17 px pitch) on the page lattice translation
   (continuous camera, no snap);
2. order each row by projected x (total order: x, chain key, seed, object id);
3. choose each site's idiom as a pure function of (role, analytic projected
   tangent, member index = finest-level ordinal mod run length) through the
   existing run table (outline_language.v1.json run_table, the same rows the
   GLSL selector tff_run_row_matches reads), with two admission guards:
   D7 (no alphanumerics without a sanctioned gaze anchor) and G10 (a crest draws a wave
   glyph only as the approved pair U+2248 U+2248);
4. split the row into conflict groups separated by gaps wide enough for free
   spacing, and solve each group alone with the row-transition costs of
   true_ff_row_transition.glslinc (hole 128 per px, capped; strict nearest
   depth-band order first);
5. fill every gap with a U+3000 and b U+0020 minimising |11a + 5b - gap|
   under b <= 2a (leading) or b <= 2a + 2 (interior); ties go to fewer
   glyphs, then smaller b.

The group solve here is the exact dynamic programme over the group. The GPU
solve (tff_row_* in true_ff_row_transition.glslinc) equals it unless it sets
its inexact flag, and except for the order in which exactly tied
predecessors are kept. No Godot, GPU or capture is involved.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
LANGUAGE = ROOT / "godot_project/assets/glyphs/true_ff/outline_language.v1.json"
CATALOG = ROOT / "godot_project/assets/glyphs/true_ff/saitamaar/catalog.json"

ROW_PITCH = 17
HALF_ADVANCE = 5
FULL_ADVANCE = 11
HALF_SPACE = 0x20
FULL_SPACE = 0x3000
ORIGINS = 7
CENTRE = 3
PLACE_COST = 16
HOLE_COST = 128  # TFF_ROW_HOLE_COST
HOLE_CAP = 8192  # TFF_ROW_HOLE_CAP
JOIN_COST = 64  # TFF_ROW_JOIN_COST (next-row joins; see JOIN note below)
ROW_BANDS = 8  # TFF_ROW_BANDS
SPACING_LIMIT = 1024  # TFF_GROUP_SPACING_LIMIT
INVALID = 0x3FFFFFFF  # TFF_ROW_INVALID

# Role word built by P5 per site (true_ff_feature_layout.glslinc
# tff_feature_role_word): role | language << 8 | class << 12 | bank << 16.
ROLE_MASK = 0xFF
LANGUAGE_SHIFT = 8
LANGUAGE_MASK = 0xF
CLASS_SHIFT = 12
CLASS_MASK = 0xF
BANK_SHIFT = 16
BANK_MASK = 0x3

# true_ff_roles.glslinc
ROLE_GENERIC = 0
ROLE_SHORELINE = 1
ROLE_CREST = 3
ROLE_RIDGE = 10
ROLE_BODY_OUTLINE = 21
LANGUAGE_WATER = 8
# true_ff_feature_abi.glslinc feature classes that name a depiction.
CLASS_TERRAIN_RELIEF = 7
CLASS_SHORELINE = 8

# Idiom flags (FeatureIdiom.flags.x).
IDIOM_ALNUM = 1
IDIOM_NOT_CREST_PAIR = 2
# G10: the wave glyphs. A crest idiom that contains one must be exactly the
# approved crest pair. The operator's latest decision (Codex session
# 2026-09-30T06:24:33Z, FL-4512 OPERATOR_WAVE_FORM_REVERSED_TO_APPROX_PAIR_
# 20260930_01A0EFFD) is two U+2248; a lone wave glyph and the superseded
# U+FF5E pair are not crests (FL-4907 SPEC_GAP_CLOSURE_20260930 G10).
WAVE_GLYPHS = ("～", "≈")
CREST_PAIR = "≈≈"

RUN_MEMBERS_SHIFT = 8
RUN_MEMBERS_MASK = 255
RUN_JOINT = 1
RUN_BOW_RATIO = 2
RUN_JUNCTION = 4
RUN_BANK_SHIFT = 16
RUN_BANK_MASK = 3
BANK_UNKNOWN = 0

_ALNUM = re.compile(r"[A-Za-z0-9０-９Ａ-Ｚａ-ｚ]")


def f32(value: float) -> float:
    """Round to IEEE single precision, as the GPU stores run-table bounds."""
    import struct

    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


# ---------------------------------------------------------------- spacing law


def space_counts(distance: int, leading: bool) -> Tuple[int, int]:
    """tff_space_counts (true_ff_spacing.glslinc) at 5/11 px: exact spelling."""
    if distance < 0:
        return (-1, -1)
    full = distance // FULL_ADVANCE
    full -= ((full - distance) % 5 + 5) % 5
    if full < 0:
        return (-1, -1)
    narrow = (distance - FULL_ADVANCE * full) // HALF_ADVANCE
    limit = 2 * full + (0 if leading else 2)
    return (full, narrow) if narrow <= limit else (-1, -1)


def gap_fill(gap: int, leading: bool) -> Tuple[int, int]:
    """a U+3000 + b U+0020 minimising |11a + 5b - gap|.

    Constraint: b <= 2a (leading) or b <= 2a + 2 (interior). Ties: fewer
    glyphs (a + b), then smaller b. gap < 0 is not a gap (overlap): (0, 0).
    """
    if gap <= 0:
        return (0, 0)
    best: Optional[Tuple[int, int, int, int]] = None
    for a in range(gap // FULL_ADVANCE + 2):
        cap = 2 * a + (0 if leading else 2)
        # b closest to (gap - 11a)/5 within [0, cap], and its neighbours.
        ideal = (gap - FULL_ADVANCE * a) / HALF_ADVANCE
        for b in {0, cap, max(0, min(cap, math.floor(ideal))), max(0, min(cap, math.ceil(ideal)))}:
            key = (abs(FULL_ADVANCE * a + HALF_ADVANCE * b - gap), a + b, b, a)
            if best is None or key < best:
                best = key
    assert best is not None
    return (best[3], best[2])


def last_illegal(leading: bool) -> int:
    last = -1
    for d in range(SPACING_LIMIT):
        if space_counts(d, leading)[0] < 0:
            last = d
    return last


def group_threshold(last: int) -> int:
    """tff_group_threshold."""
    return last + 1 if last < SPACING_LIMIT // 2 else INVALID


# ------------------------------------------------------------------ run table


@dataclass(frozen=True)
class Idiom:
    text: str
    advance: int
    ink_left: int
    ink_right: int
    members: Tuple[int, ...]  # codepoints
    offsets: Tuple[int, ...]  # pen x per member
    flags: int


@dataclass(frozen=True)
class RunRow:
    key: Tuple[int, int, int, int]
    lo: Tuple[float, float, float, float]
    hi: Tuple[float, float, float, float]
    primary: int
    substitute: int
    phase: int


def idiom_flags(text: str) -> int:
    flags = 0
    if _ALNUM.search(text):
        flags |= IDIOM_ALNUM
    if any(w in text for w in WAVE_GLYPHS) and text != CREST_PAIR:
        flags |= IDIOM_NOT_CREST_PAIR
    return flags


class Table:
    """The run table and idiom vocabulary exactly as the renderer packs them."""

    def __init__(self, language: Optional[dict] = None, catalog: Optional[dict] = None):
        self.language = language if language is not None else json.loads(LANGUAGE.read_text())
        catalog = catalog if catalog is not None else json.loads(CATALOG.read_text())
        self.glyphs: Dict[int, dict] = {g["codepoint"]: g for g in catalog["glyphs"]}
        languages = self.language["languages"]
        self.idioms: List[Idiom] = []
        self._index: Dict[str, int] = {}
        self.rows: List[RunRow] = []
        for row in self.language["run_table"]["rows"]:
            primary = self._idiom(row["sequence"])
            substitute = self._idiom(row["substitute"]) if row["substitute"] else -1
            self.rows.append(RunRow(
                key=(int(row["role_id"]), int(languages[row["language"]]), int(row["flags"]), int(row["detail"])),
                lo=tuple(f32(v) for v in row["lo"]), hi=tuple(f32(v) for v in row["hi"]),
                primary=primary, substitute=substitute, phase=int(row["phase_advance_px"])))

    def _idiom(self, text: str) -> int:
        if text in self._index:
            return self._index[text]
        pen = 0
        ink_left, ink_right = 0x3FFF, -0x3FFF
        members, offsets = [], []
        for char in text:
            glyph = self.glyphs[ord(char)]
            x0, _, x1, _ = glyph["ink_bounds"]
            if x1 > x0:
                ink_left = min(ink_left, pen + x0)
                ink_right = max(ink_right, pen + x1)
            members.append(ord(char))
            offsets.append(pen)
            pen += int(glyph["advance_px"])
        self._index[text] = len(self.idioms)
        self.idioms.append(Idiom(text, pen, ink_left, ink_right, tuple(members), tuple(offsets), idiom_flags(text)))
        return self._index[text]


def keyframe_bow(bow: float, extent: float, relative: bool) -> float:
    return (bow / extent if extent > 0.0000001 else 0.0) if relative else bow


def row_matches(row: RunRow, language: int, shape: Tuple[float, float, float, float], bank: int) -> bool:
    """tff_run_row_matches for a plain edge site (sectors 0)."""
    if (row.key[1] & language) == 0:
        return False
    need = (row.key[2] >> RUN_BANK_SHIFT) & RUN_BANK_MASK
    if need != BANK_UNKNOWN and need != bank:
        return False
    if (row.key[2] & RUN_JUNCTION) or (row.key[2] & RUN_JOINT):
        return False
    angle, extent, bow, height = shape
    b = keyframe_bow(bow, extent, (row.key[2] & RUN_BOW_RATIO) != 0)
    return (row.lo[0] <= angle < row.hi[0] and row.lo[1] <= extent < row.hi[1]
            and row.lo[2] <= b < row.hi[2] and row.lo[3] <= height < row.hi[3])


def scenery_role(role: int) -> bool:
    """D7: every role before the figure/vehicle block depicts scenery; the
    generic fallback may depict scenery, so it is scenery too."""
    return role < ROLE_BODY_OUTLINE


def admit(table: Table, role: int, idiom: int) -> int:
    if idiom < 0:
        return -1
    flags = table.idioms[idiom].flags
    # This entry point has no measured/approved eye-gaze anchor. No source
    # family is a blanket alphanumeric exemption.
    if flags & IDIOM_ALNUM:
        return -1
    if role == ROLE_CREST and (flags & IDIOM_NOT_CREST_PAIR):
        return -1
    return idiom


def select(table: Table, role: int, language: int, shape: Tuple[float, float, float, float],
           bank: int, seed: int) -> int:
    """Exact role-language owner; an absent recipe cannot borrow generic art."""
    index = 0
    rows = table.rows
    while index < len(rows):
        row = rows[index]
        members = max((row.key[2] >> RUN_MEMBERS_SHIFT) & RUN_MEMBERS_MASK, 1)
        if row.key[0] == role and row_matches(row, language, shape, bank):
            idiom = rows[index + (seed & 0xFFFFFFFF) % members].primary
            return admit(table, role, idiom)
        index += members
    return -1


# ----------------------------------------------------------------- site facts


@dataclass(frozen=True)
class Site:
    x: float  # screen x px (continuous camera)
    y: float  # screen y px
    depth: float  # view depth
    q: float  # arc ordinal q
    chain: int  # loop/chain key
    seed: int  # member-index seed = finest-level ordinal
    role_word: int  # the P5 role word (role_word()), see *_SHIFT above
    object_id: int
    tx: float  # analytic projected tangent (PASSES publishes a unit vector)
    ty: float
    source_namespace: int = 1  # retained source-part namespace
    chain_address: Optional[int] = None  # exact chain/address lane
    source_extent: float = 1.0  # projected source span, separate from unit tangent

    @property
    def contour_identity(self) -> Tuple[int, int, int]:
        feature_class = (self.role_word >> CLASS_SHIFT) & CLASS_MASK
        return (int(self.source_namespace),
                int(self.chain if self.chain_address is None else self.chain_address), feature_class)


def class_role(feature_class: int) -> int:
    """tff_feature_class_role: only shoreline and terrain relief name a role."""
    if feature_class == CLASS_SHORELINE:
        return ROLE_SHORELINE
    if feature_class == CLASS_TERRAIN_RELIEF:
        return ROLE_RIDGE
    return ROLE_GENERIC


def role_word(feature_class: int, material_language: int) -> int:
    """tff_feature_role_word: a shoreline draws in the water language."""
    language = LANGUAGE_WATER if feature_class == CLASS_SHORELINE else material_language
    return pack_role(class_role(feature_class), language, feature_class, 0)


def pack_role(role: int, language: int, feature_class: int = 0, bank: int = 0) -> int:
    return (role & ROLE_MASK) | ((language & LANGUAGE_MASK) << LANGUAGE_SHIFT) \
        | ((feature_class & CLASS_MASK) << CLASS_SHIFT) | ((bank & BANK_MASK) << BANK_SHIFT)


def f32_angle(dx: float, dy: float) -> float:
    """tff_feature_angle: tff_contour_geometry's convention (dx >= 0,
    degrees, screen y down)."""
    if dx < 0.0 or (dx == 0.0 and dy < 0.0):
        dx, dy = -dx, -dy
    return math.atan2(dy, dx) * 57.29577951308232


def shape_of(site: Site, row_top: float) -> Tuple[float, float, float, float]:
    """Source span is independent of direction; curvature is still absent."""
    return (f32_angle(site.tx, site.ty), site.source_extent, 0.0, site.y - row_top)


def row_of(y: float, top0: float) -> int:
    return math.floor((y - top0) / ROW_PITCH)


def depth_band(depth: float) -> int:
    """Packed production bands 1..8, nearest first; far depths saturate at 1."""
    d = max(depth, 1.0)
    return max(1, min(ROW_BANDS, ROW_BANDS - int(math.floor(math.log2(d)))))


def origin_of(target_x: float, advance: int) -> int:
    return math.floor(target_x - advance * 0.5 + 0.5) - CENTRE


def local_cost(c: int, origin: int, advance: int, width: int) -> int:
    start = origin + c
    if start < 0 or start + advance > width:
        return INVALID
    return abs(c - CENTRE) * PLACE_COST


def site_order(site: Site) -> Tuple:
    return (site.x, site.contour_identity, site.seed, site.object_id, site.y, site.depth, site.q,
            site.role_word, site.tx, site.ty)


# --------------------------------------------------------------- row solving


@dataclass
class Node:
    site: Site
    idiom: int
    origin: int
    advance: int
    ink_left: int
    ink_right: int
    edge: Tuple[int, int, int]
    band: int
    local: List[int] = field(default_factory=list)

    def fitted(self) -> List[int]:
        return [c for c in range(ORIGINS) if self.local[c] < INVALID]


def gain(band: int) -> Tuple[int, ...]:
    """One placed support in `band`, nearest band first."""
    out = [0] * ROW_BANDS
    out[ROW_BANDS - band] = 1
    return tuple(out)


def add(a: Tuple[int, ...], b: Tuple[int, ...]) -> Tuple[int, ...]:
    return tuple(x + y for x, y in zip(a, b))


def better(a, b) -> bool:
    """tff_row_better: more placed supports in the nearest differing band,
    else smaller placement cost. None is invalid."""
    if a is None:
        return False
    if b is None:
        return True
    if a[0] != b[0]:
        return a[0] > b[0]
    return a[1] < b[1]


def row_edge(end: int, ink_end: int, connected: bool, start: int, ink_left: int, leading: bool) -> bool:
    """tff_row_edge."""
    if start < end:
        return False
    if not connected and start + ink_left < ink_end:
        return False
    return space_counts(start - end, leading)[0] >= 0


def hole(gap: int) -> int:
    return min(gap * HOLE_COST, HOLE_CAP)


def solve_group(nodes: Sequence[Node]) -> List[Tuple[int, int]]:
    """Exact DP over one conflict group, from an empty row state.

    Returns the chosen (node index, origin choice) path in text order.
    Opening a group is judged against the row start (leading law), which the
    split rule makes legal for every non-first group."""
    zero = tuple([0] * ROW_BANDS)
    cost: List[List[Optional[Tuple[Tuple[int, ...], int]]]] = []
    parent: List[List[Optional[Tuple[int, int]]]] = []
    best: Tuple[Tuple[int, ...], int] = (zero, 0)
    best_at: Optional[Tuple[int, int]] = None
    for k, node in enumerate(nodes):
        row_cost: List[Optional[Tuple[Tuple[int, ...], int]]] = []
        row_parent: List[Optional[Tuple[int, int]]] = []
        g = gain(node.band)
        for c in range(ORIGINS):
            local = node.local[c]
            chosen = None
            chosen_parent = None
            if local < INVALID:
                start = node.origin + c
                left = node.ink_left
                if start + min(left, 0) >= 0 and row_edge(0, 0, True, start, left, True):
                    chosen = (g, local)
                for j in range(k):
                    prev = nodes[j]
                    for p in range(ORIGINS):
                        from_cost = cost[j][p]
                        if from_cost is None:
                            continue
                        origin = prev.origin + p
                        end = origin + prev.advance
                        ink_end = origin + prev.ink_right
                        connected = prev.edge == node.edge
                        if not row_edge(end, ink_end, connected, start, left, False):
                            continue
                        candidate = (add(from_cost[0], g),
                                     from_cost[1] + local + (hole(start - end) if connected else 0))
                        if better(candidate, chosen):
                            chosen = candidate
                            chosen_parent = (j, p)
            row_cost.append(chosen)
            row_parent.append(chosen_parent)
        cost.append(row_cost)
        parent.append(row_parent)
        for c in range(ORIGINS):
            if better(row_cost[c], best):
                best = row_cost[c]
                best_at = (k, c)
    path: List[Tuple[int, int]] = []
    at = best_at
    while at is not None:
        path.append(at)
        at = parent[at[0]][at[1]]
    path.reverse()
    return path


def reach(node: Node) -> Tuple[int, int, int, int]:
    """tff_group_reach over the fitted origins: start, ink start, end, ink end."""
    fits = node.fitted()
    return (min(node.origin + c for c in fits), min(node.origin + c + node.ink_left for c in fits),
            max(node.origin + c + node.advance for c in fits), max(node.origin + c + node.ink_right for c in fits))


def partition(nodes: Sequence[Node], interior_from: int, leading_from: int) -> List[List[int]]:
    """Split ready nodes (text order) into conflict groups. A group opens at
    i when every later fitted origin clears every earlier fitted end by a gap
    that is always legal interior text, no ink can collide, and the later
    side could open the row (leading law). Such a gap is wide enough for free
    spacing, so each group is solved alone."""
    if not nodes:
        return []
    reaches = [reach(n) for n in nodes]
    n = len(nodes)
    suf_start = [0] * n
    suf_ink = [0] * n
    s, si = INVALID, INVALID
    for i in range(n - 1, -1, -1):
        s = min(s, reaches[i][0])
        si = min(si, reaches[i][1])
        suf_start[i], suf_ink[i] = s, si
    groups: List[List[int]] = [[0]]
    pre_end, pre_ink = reaches[0][2], reaches[0][3]
    for i in range(1, n):
        opens = (interior_from < INVALID and leading_from < INVALID
                 and suf_start[i] - pre_end >= interior_from and suf_ink[i] >= pre_ink
                 and suf_start[i] >= leading_from and suf_ink[i] >= 0)
        if opens:
            groups.append([i])
        else:
            groups[-1].append(i)
        pre_end = max(pre_end, reaches[i][2])
        pre_ink = max(pre_ink, reaches[i][3])
    return groups


# ----------------------------------------------------------------- the layout


@dataclass(frozen=True)
class Instance:
    x: float  # page px of the glyph's pen origin
    y: float  # page px of the row top
    codepoint: int
    advance: int
    object_id: int
    row: int
    group: int  # first sorted slot of the group (row-local)
    member: int


@dataclass
class RowResult:
    row: int
    nodes: List[Node]
    groups: List[List[int]]
    paths: List[List[Tuple[int, int]]]
    gaps: List[Tuple[int, bool, Tuple[int, int]]]  # (gap px, leading, (a, b))


@dataclass
class Page:
    width: int
    height: int
    residual_x: float = 0.0  # presentation translation only; never a P5 row origin
    residual_y: float = 0.0

    @property
    def x0(self) -> float:
        return 0.0

    @property
    def top0(self) -> float:
        return 0.0

    @property
    def rows(self) -> int:
        return math.ceil((self.height - self.top0) / ROW_PITCH)


def build_nodes(table: Table, sites: Iterable[Site], row: int, page: Page) -> List[Node]:
    top = page.top0 + row * ROW_PITCH
    nodes: List[Node] = []
    for site in sorted(sites, key=site_order):
        role = site.role_word & ROLE_MASK
        language = (site.role_word >> LANGUAGE_SHIFT) & LANGUAGE_MASK
        bank = (site.role_word >> BANK_SHIFT) & BANK_MASK
        idiom = select(table, role, language, shape_of(site, top), bank, site.seed)
        if idiom < 0:
            continue
        spec = table.idioms[idiom]
        origin = origin_of(site.x - page.x0, spec.advance)
        node = Node(site, idiom, origin, spec.advance, spec.ink_left, spec.ink_right,
                    site.contour_identity, depth_band(site.depth))
        node.local = [local_cost(c, origin, spec.advance, page.width) for c in range(ORIGINS)]
        if node.fitted():
            nodes.append(node)
    return nodes


def layout_row(table: Table, sites: Iterable[Site], row: int, page: Page,
               thresholds: Optional[Tuple[int, int]] = None) -> RowResult:
    interior_from, leading_from = thresholds or (group_threshold(last_illegal(False)),
                                                 group_threshold(last_illegal(True)))
    nodes = build_nodes(table, sites, row, page)
    groups = partition(nodes, interior_from, leading_from)
    paths = [[(members[k], c) for k, c in solve_group([nodes[i] for i in members])] for members in groups]
    gaps: List[Tuple[int, bool, Tuple[int, int]]] = []
    pen = 0
    for path in paths:
        for i, c in path:
            start = nodes[i].origin + c
            leading = pen == 0 and not gaps
            gap = start - pen
            gaps.append((gap, leading, gap_fill(gap, leading)))
            pen = start + nodes[i].advance
    return RowResult(row, nodes, groups, paths, gaps)


def instances_of(table: Table, result: RowResult, page: Page) -> List[Instance]:
    out: List[Instance] = []
    top = page.top0 + result.row * ROW_PITCH
    for members, path in zip(result.groups, result.paths):
        for i, c in path:
            node = result.nodes[i]
            spec = table.idioms[node.idiom]
            for m, (cp, offset) in enumerate(zip(spec.members, spec.offsets)):
                out.append(Instance(page.x0 + node.origin + c + offset, top, cp,
                                    int(table.glyphs[cp]["advance_px"]), node.site.object_id,
                                    result.row, members[0], m))
    return out


def bin_rows(sites: Iterable[Site], page: Page) -> Dict[int, List[Site]]:
    rows: Dict[int, List[Site]] = {}
    for site in sites:
        row = row_of(site.y, page.top0)
        if 0 <= row < page.rows and 0.0 <= site.x < page.width:
            rows.setdefault(row, []).append(site)
    return rows


def layout(table: Table, sites: Iterable[Site], page: Page) -> List[Instance]:
    """The whole P5 frame: only non-empty rows are visited."""
    thresholds = (group_threshold(last_illegal(False)), group_threshold(last_illegal(True)))
    out: List[Instance] = []
    for row, row_sites in sorted(bin_rows(sites, page).items()):
        out.extend(instances_of(table, layout_row(table, row_sites, row, page, thresholds), page))
    return out


if __name__ == "__main__":
    table = Table()
    print(f"rows={len(table.rows)} idioms={len(table.idioms)} "
          f"interior_from={group_threshold(last_illegal(False))} leading_from={group_threshold(last_illegal(True))}")
