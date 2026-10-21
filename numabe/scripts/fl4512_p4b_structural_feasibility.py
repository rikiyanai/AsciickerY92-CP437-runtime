#!/usr/bin/env python3
"""FL-4512 / FL-4547: frozen P400 structural glyph feasibility slice.

This is an offline diagnostic. It does not write runtime GlyphIds and it does
not implement the U6 v2 cutover. It compares the existing independent full-
Unifont winner with a bounded structural palette, within-frame connectivity,
and deterministic cyclic dynamic programming on authored feature tracks.

Run with the CLIPasso virtualenv so svgpathtools and the pinned matcher are
available:

  .scratch/gradscii-art/.venv/bin/python \
    scripts/fl4512_p4b_structural_feasibility.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFont


REPO = Path(__file__).resolve().parents[1]
CLIPASSO = REPO / ".scratch" / "CLIPasso"
sys.path.insert(0, str(CLIPASSO))

import unifont_match as um  # noqa: E402  (loaded from the frozen CLIPasso tree)


SCHEMA = "fl4512.p4b_structural_feasibility.v1"
COLS = 5
ROWS = 7
FRAME_INDEX = [0, 2, 4, 7, 9, 12, 14, 17]
HORSE_DIRECTIONS = ["S", "SW", "W", "NW", "N", "NE", "E", "SE"]
TRANSFORMS = ("identity", "mirror_h", "mirror_v", "rotate_90", "rotate_270")

TOP_K = 10
LAMBDA_SPATIAL = 1.35
LAMBDA_TEMPORAL = 1.10
LAMBDA_TOPOLOGY = 0.22
LAMBDA_DECLARED_RELATION = 1.45
MAX_SPATIAL_SWEEPS = 10

BG = (12, 12, 12)
FG = (120, 230, 120)
LABEL = (220, 210, 90)
CELL_PX = 34
PANEL_PAD = 12


@dataclass(frozen=True)
class GlyphDescriptor:
    glyph_id: int
    char: str
    codepoint: int
    mask: np.ndarray
    ink: float
    components: int
    holes: int
    orientation: float
    ports: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]


@dataclass
class FrameData:
    key: str
    svg: Path
    cells: list[list[np.ndarray]]
    source_masks: dict[tuple[int, int], np.ndarray]
    candidates: dict[tuple[int, int], np.ndarray]
    unary: dict[tuple[int, int], np.ndarray]
    full_winner: dict[tuple[int, int], int]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def json_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def mask_feature(mask: np.ndarray) -> np.ndarray:
    return um.feature(mask)


def resize_mask(mask: np.ndarray, size: tuple[int, int] = (16, 16)) -> np.ndarray:
    image = Image.fromarray((mask.astype(np.uint8) * 255))
    return np.asarray(image.resize(size, Image.Resampling.BILINEAR)) > 96


def transform_mask(mask: np.ndarray, transform: str) -> np.ndarray:
    if transform == "identity":
        return mask
    if transform == "mirror_h":
        return np.fliplr(mask)
    if transform == "mirror_v":
        return np.flipud(mask)
    if transform == "rotate_90":
        return np.rot90(mask, 1)
    if transform == "rotate_270":
        return np.rot90(mask, 3)
    raise ValueError(f"unknown transform: {transform}")


def mask_distance(a: np.ndarray, b: np.ndarray, transform: str = "identity") -> float:
    ta = transform_mask(a, transform)
    if ta.shape != b.shape:
        ta = resize_mask(ta, (b.shape[1], b.shape[0]))
    return float(np.mean(np.abs(ta.astype(float) - b.astype(float))))


def connected_components(mask: np.ndarray, foreground: bool = True) -> int:
    target = mask if foreground else ~mask
    seen = np.zeros(target.shape, dtype=bool)
    count = 0
    h, w = target.shape
    for y in range(h):
        for x in range(w):
            if not target[y, x] or seen[y, x]:
                continue
            count += 1
            stack = [(y, x)]
            seen[y, x] = True
            while stack:
                cy, cx = stack.pop()
                for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    ny, nx = cy + dy, cx + dx
                    if 0 <= ny < h and 0 <= nx < w and target[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
    return count


def hole_count(mask: np.ndarray) -> int:
    background = ~mask
    padded = np.pad(background, 1, constant_values=True)
    return max(0, connected_components(padded, foreground=True) - 1)


def dominant_orientation(mask: np.ndarray) -> float:
    ys, xs = np.nonzero(mask)
    if len(xs) < 2:
        return 0.0
    pts = np.column_stack((xs - xs.mean(), ys - ys.mean()))
    cov = np.cov(pts, rowvar=False)
    vals, vecs = np.linalg.eigh(cov)
    vx, vy = vecs[:, int(np.argmax(vals))]
    angle = math.atan2(float(vy), float(vx))
    return angle % math.pi


def port_vector(mask: np.ndarray, side: int, bins: int = 8) -> np.ndarray:
    # Use a shallow strip, since Unifont strokes often stop one pixel before the
    # nominal cell boundary. The vector preserves multiple crossing positions.
    thickness = max(2, min(mask.shape) // 7)
    if side == 0:  # north
        values = np.max(mask[:thickness, :], axis=0)
    elif side == 1:  # east
        values = np.max(mask[:, -thickness:], axis=1)
    elif side == 2:  # south
        values = np.max(mask[-thickness:, :], axis=0)
    else:  # west
        values = np.max(mask[:, :thickness], axis=1)
    image = Image.fromarray((values.astype(np.uint8) * 255)[None, :])
    return np.asarray(image.resize((bins, 1), Image.Resampling.BILINEAR))[0] / 255.0


def describe_glyph(glyph_id: int, char: str, mask: np.ndarray) -> GlyphDescriptor:
    return GlyphDescriptor(
        glyph_id=glyph_id,
        char=char,
        codepoint=ord(char),
        mask=mask,
        ink=float(mask.mean()),
        components=connected_components(mask),
        holes=hole_count(mask),
        orientation=dominant_orientation(mask),
        ports=tuple(port_vector(mask, side) for side in range(4)),
    )


STRUCTURAL_ASCII = set("()[]{}<>/\\|_-~^`'.:,;+=*xXvVIlLrTtYy")
STRUCTURAL_RANGES = (
    (0x2190, 0x21FF),  # arrows
    (0x2300, 0x23FF),  # technical marks
    (0x2500, 0x257F),  # box drawing
    (0x2580, 0x259F),  # bounded block elements
    (0x25A0, 0x25FF),  # geometric shapes
    (0x27C0, 0x27EF),  # mathematical shapes
    (0x27F0, 0x27FF),  # supplemental arrows
)


def structural_glyph(codepoint: int, char: str) -> bool:
    if char in STRUCTURAL_ASCII:
        return True
    return any(lo <= codepoint <= hi for lo, hi in STRUCTURAL_RANGES)


def rasterize_chars(chars: list[str]) -> list[np.ndarray]:
    font = ImageFont.truetype(um.UNIFONT, 16)
    masks: list[np.ndarray] = []
    for char in chars:
        image = Image.new("L", (16, 16), 0)
        ImageDraw.Draw(image).text((0, 0), char, font=font, fill=255)
        masks.append(np.asarray(image) > 100)
    return masks


def build_structural_atlas() -> tuple[list[str], np.ndarray, list[GlyphDescriptor], dict]:
    chars, feats, provenance, stats = um.build_atlas()
    masks = rasterize_chars(chars)
    selected = [
        i for i, row in enumerate(provenance)
        if structural_glyph(int(row["cp"][2:], 16), chars[i])
        and 0.045 <= float(masks[i].mean()) <= 0.54
        and connected_components(masks[i]) <= 3
        and hole_count(masks[i]) <= 2
    ]
    if not selected:
        raise RuntimeError("structural atlas admission produced no glyphs")
    structural_chars = [chars[i] for i in selected]
    structural_feats = feats[np.asarray(selected, dtype=int)]
    descriptors = [describe_glyph(i, chars[i], masks[i]) for i in selected]
    return chars, feats, descriptors, {
        "full_atlas": stats,
        "structural_admitted": len(descriptors),
        "structural_codepoints": [f"U+{d.codepoint:04X}" for d in descriptors],
        "structural_chars": structural_chars,
    }


def frame_specs() -> dict[str, list[tuple[str, Path]]]:
    walk_root = CLIPASSO / "output_sketches" / "walk_p400"
    horse_root = CLIPASSO / "output_sketches" / "horse"
    return {
        "walk_e": [
            (f"E_f{index}", walk_root / f"E_f{index}" / "run0" / "best_iter.svg")
            for index in FRAME_INDEX
        ],
        "horse": [
            (direction, horse_root / direction / "run0" / "best_iter.svg")
            for direction in HORSE_DIRECTIONS
        ],
    }


def top_k_candidates(cell: np.ndarray, descriptors: list[GlyphDescriptor], k: int) -> tuple[np.ndarray, np.ndarray]:
    source_feature = mask_feature(cell)
    matrix = np.stack([mask_feature(d.mask) for d in descriptors])
    scores = matrix @ source_feature
    count = min(k, len(descriptors))
    local = np.argpartition(-scores, count - 1)[:count]
    local = local[np.lexsort((np.array([descriptors[i].glyph_id for i in local]), -scores[local]))]
    unary = 1.0 - scores[local]
    return local.astype(int), unary.astype(float)


def load_frame(key: str, svg: Path, full_feats: np.ndarray, descriptors: list[GlyphDescriptor]) -> FrameData:
    if not svg.exists():
        raise FileNotFoundError(svg)
    polygons = um.sample_polys(str(svg))
    if not polygons:
        raise ValueError(f"no sampleable paths: {svg}")
    cells = um.grid_cells(polygons, COLS, ROWS)
    source_masks: dict[tuple[int, int], np.ndarray] = {}
    candidates: dict[tuple[int, int], np.ndarray] = {}
    unary: dict[tuple[int, int], np.ndarray] = {}
    full_winner: dict[tuple[int, int], int] = {}
    for row in range(ROWS):
        for col in range(COLS):
            cell = cells[row][col]
            if cell.sum() < cell.size * 0.012:
                continue
            pos = (row, col)
            source_masks[pos] = resize_mask(cell)
            full_winner[pos] = int(np.argmax(full_feats @ mask_feature(cell)))
            cand, costs = top_k_candidates(cell, descriptors, TOP_K)
            candidates[pos] = cand
            unary[pos] = costs
    return FrameData(key, svg, cells, source_masks, candidates, unary, full_winner)


def neighbors(pos: tuple[int, int]) -> Iterable[tuple[tuple[int, int], int, int]]:
    row, col = pos
    for dr, dc, side, opposite in ((-1, 0, 0, 2), (0, 1, 1, 3), (1, 0, 2, 0), (0, -1, 3, 1)):
        other = (row + dr, col + dc)
        if 0 <= other[0] < ROWS and 0 <= other[1] < COLS:
            yield other, side, opposite


def spatial_pair_cost(
    frame: FrameData,
    pos_a: tuple[int, int],
    glyph_a: GlyphDescriptor,
    pos_b: tuple[int, int],
    glyph_b: GlyphDescriptor,
    side_a: int,
    side_b: int,
) -> float:
    source_a = port_vector(frame.source_masks[pos_a], side_a)
    source_b = port_vector(frame.source_masks[pos_b], side_b)
    target_join = np.sqrt(source_a * source_b)
    glyph_join = np.sqrt(glyph_a.ports[side_a] * glyph_b.ports[side_b])
    join_error = float(np.mean(np.abs(target_join - glyph_join)))
    boundary_error = 0.5 * float(np.mean(np.abs(source_a - glyph_a.ports[side_a])))
    boundary_error += 0.5 * float(np.mean(np.abs(source_b - glyph_b.ports[side_b])))
    return 0.72 * join_error + 0.28 * boundary_error


def independent_labels(frame: FrameData) -> dict[tuple[int, int], int]:
    return {pos: int(candidates[0]) for pos, candidates in frame.candidates.items()}


def spatial_refine(
    frame: FrameData,
    descriptors: list[GlyphDescriptor],
    initial: dict[tuple[int, int], int],
) -> dict[tuple[int, int], int]:
    labels = dict(initial)
    ordered = sorted(labels)
    for _sweep in range(MAX_SPATIAL_SWEEPS):
        changed = 0
        for pos in ordered:
            best = labels[pos]
            best_cost = math.inf
            for candidate_offset, local_index in enumerate(frame.candidates[pos]):
                glyph = descriptors[int(local_index)]
                cost = float(frame.unary[pos][candidate_offset])
                for other, side, opposite in neighbors(pos):
                    if other not in labels:
                        continue
                    cost += LAMBDA_SPATIAL * spatial_pair_cost(
                        frame, pos, glyph, other, descriptors[labels[other]], side, opposite
                    )
                tie = glyph.glyph_id
                if (cost, tie) < (best_cost, descriptors[best].glyph_id):
                    best_cost = cost
                    best = int(local_index)
            changed += int(best != labels[pos])
            labels[pos] = best
        if changed == 0:
            break
    return labels


def source_transform(a: np.ndarray, b: np.ndarray, declared: str) -> str:
    if declared != "source_best":
        return declared
    scored = [(mask_distance(a, b, transform), transform) for transform in TRANSFORMS]
    return min(scored, key=lambda item: (item[0], TRANSFORMS.index(item[1])))[1]


def topology_distance(a: GlyphDescriptor, b: GlyphDescriptor) -> float:
    component = min(1.0, abs(a.components - b.components) / 2.0)
    holes = min(1.0, abs(a.holes - b.holes) / 2.0)
    ink = min(1.0, abs(a.ink - b.ink) * 4.0)
    return (component + holes + ink) / 3.0


def temporal_pair_cost(
    descriptor_a: GlyphDescriptor,
    descriptor_b: GlyphDescriptor,
    expected_transform: str,
) -> float:
    relation = mask_distance(descriptor_a.mask, descriptor_b.mask, expected_transform)
    return LAMBDA_TEMPORAL * relation + LAMBDA_TOPOLOGY * topology_distance(descriptor_a, descriptor_b)


def local_unary_with_spatial(
    frame: FrameData,
    pos: tuple[int, int],
    candidate_local: int,
    labels: dict[tuple[int, int], int],
    descriptors: list[GlyphDescriptor],
) -> float:
    offsets = np.where(frame.candidates[pos] == candidate_local)[0]
    if len(offsets) != 1:
        raise RuntimeError(f"candidate missing from lattice at {frame.key}:{pos}")
    cost = float(frame.unary[pos][int(offsets[0])])
    glyph = descriptors[candidate_local]
    for other, side, opposite in neighbors(pos):
        if other not in labels:
            continue
        cost += LAMBDA_SPATIAL * spatial_pair_cost(
            frame, pos, glyph, other, descriptors[labels[other]], side, opposite
        )
    return cost


def cyclic_track_dp(
    frames: list[FrameData],
    points: list[tuple[int, int]],
    declared_relations: list[str],
    labels: list[dict[tuple[int, int], int]],
    descriptors: list[GlyphDescriptor],
) -> tuple[list[int], list[str], float]:
    if len(points) != len(frames):
        raise ValueError("track point count must match frame count")
    if len(declared_relations) != len(frames):
        raise ValueError("cyclic track needs one relation for every edge, including last-to-first")
    states = [frame.candidates[pos].tolist() for frame, pos in zip(frames, points)]
    unary = [
        [local_unary_with_spatial(frame, pos, state, label, descriptors) for state in frame_states]
        for frame, pos, frame_states, label in zip(frames, points, states, labels)
    ]
    expected = [
        source_transform(
            frames[i].source_masks[points[i]],
            frames[(i + 1) % len(frames)].source_masks[points[(i + 1) % len(frames)]],
            declared_relations[i],
        )
        for i in range(len(frames))
    ]

    best_total = math.inf
    best_path: list[int] | None = None
    for first in states[0]:
        costs = {first: unary[0][states[0].index(first)]}
        paths = {first: [first]}
        for frame_index in range(1, len(frames)):
            next_costs: dict[int, float] = {}
            next_paths: dict[int, list[int]] = {}
            for current in states[frame_index]:
                current_unary = unary[frame_index][states[frame_index].index(current)]
                choices = []
                for previous, prior_cost in costs.items():
                    pair = temporal_pair_cost(
                        descriptors[previous], descriptors[current], expected[frame_index - 1]
                    )
                    path_ids = tuple(descriptors[x].glyph_id for x in paths[previous] + [current])
                    choices.append((prior_cost + current_unary + pair, path_ids, previous))
                choice = min(choices)
                next_costs[current] = choice[0]
                next_paths[current] = paths[choice[2]] + [current]
            costs, paths = next_costs, next_paths
        for last, cost in costs.items():
            total = cost + temporal_pair_cost(descriptors[last], descriptors[first], expected[-1])
            path = paths[last]
            tie = tuple(descriptors[x].glyph_id for x in path)
            if best_path is None or (total, tie) < (
                best_total,
                tuple(descriptors[x].glyph_id for x in best_path),
            ):
                best_total = total
                best_path = path
    assert best_path is not None
    return best_path, expected, best_total


def refine_declared_relations(
    frames: list[FrameData],
    points: list[tuple[int, int]],
    path: list[int],
    chain_transforms: list[str],
    relation_edges: list[dict],
    labels: list[dict[tuple[int, int], int]],
    descriptors: list[GlyphDescriptor],
) -> list[int]:
    """Deterministically refine a cyclic path with declared non-chain edges.

    The cyclic DP is exact before these chords are introduced. The bounded
    coordinate pass is explicitly diagnostic and is never reported as a global
    U6 v2 optimum.
    """
    result = list(path)
    by_node: dict[int, list[dict]] = {index: [] for index in range(len(frames))}
    for edge in relation_edges:
        by_node[int(edge["a"])].append(edge)
        by_node[int(edge["b"])].append(edge)
    for _sweep in range(8):
        changed = 0
        for index, (frame, point) in enumerate(zip(frames, points)):
            previous = (index - 1) % len(frames)
            following = (index + 1) % len(frames)
            choices = []
            for candidate in frame.candidates[point].tolist():
                cost = local_unary_with_spatial(frame, point, candidate, labels[index], descriptors)
                cost += temporal_pair_cost(
                    descriptors[result[previous]], descriptors[candidate], chain_transforms[previous]
                )
                cost += temporal_pair_cost(
                    descriptors[candidate], descriptors[result[following]], chain_transforms[index]
                )
                for edge in by_node[index]:
                    a, b = int(edge["a"]), int(edge["b"])
                    other = b if index == a else a
                    relation = edge["relation"]
                    if index == b:
                        # Horizontal and vertical reflection are self-inverse.
                        # Quarter rotations are reversed when traversed backward.
                        relation = {
                            "rotate_90": "rotate_270",
                            "rotate_270": "rotate_90",
                        }.get(relation, relation)
                    relation_cost = mask_distance(
                        descriptors[candidate].mask, descriptors[result[other]].mask, relation
                    )
                    cost += LAMBDA_DECLARED_RELATION * relation_cost
                choices.append((cost, descriptors[candidate].glyph_id, candidate))
            best = min(choices)[2]
            changed += int(best != result[index])
            result[index] = best
        if changed == 0:
            break
    return result


def apply_tracks(
    dataset: str,
    frames: list[FrameData],
    labels: list[dict[tuple[int, int], int]],
    manifest: dict,
    descriptors: list[GlyphDescriptor],
) -> tuple[list[dict[tuple[int, int], int]], list[dict]]:
    result = [dict(frame_labels) for frame_labels in labels]
    receipts: list[dict] = []
    tracks = manifest["datasets"][dataset]["tracks"]
    dataset_relation_edges = manifest["datasets"][dataset].get("relation_edges", [])
    # Deterministic block-coordinate refinement: every track solve is exact for
    # its fixed-neighbour subproblem. This diagnostic does not claim the global
    # U6 v2 cutover proof.
    for _pass in range(2):
        receipts = []
        for track in tracks:
            points = [tuple(point) for point in track["points"]]
            missing = [
                f"{frame.key}:{point}"
                for frame, point in zip(frames, points)
                if point not in frame.candidates
            ]
            if missing:
                raise ValueError(f"track {track['id']} references empty cells: {missing}")
            relations = track.get("relations", ["source_best"] * len(frames))
            path, expected, energy = cyclic_track_dp(
                frames, points, relations, result, descriptors
            )
            relation_edges = [
                edge for edge in dataset_relation_edges if edge["track_id"] == track["id"]
            ]
            if relation_edges:
                path = refine_declared_relations(
                    frames, points, path, expected, relation_edges, result, descriptors
                )
            for index, (point, local) in enumerate(zip(points, path)):
                result[index][point] = local
            relation_receipts = []
            for edge in relation_edges:
                a, b = int(edge["a"]), int(edge["b"])
                distance = mask_distance(
                    descriptors[path[a]].mask, descriptors[path[b]].mask, edge["relation"]
                )
                relation_receipts.append({
                    **edge,
                    "glyph_id_a": descriptors[path[a]].glyph_id,
                    "glyph_id_b": descriptors[path[b]].glyph_id,
                    "distance": distance,
                    "passes_diagnostic_threshold_0_46": distance <= 0.46,
                })
            receipts.append({
                "track_id": track["id"],
                "semantic_part": track["semantic_part"],
                "points": [list(point) for point in points],
                "expected_transforms": expected,
                "glyph_ids": [descriptors[x].glyph_id for x in path],
                "codepoints": [f"U+{descriptors[x].codepoint:04X}" for x in path],
                "cyclic_dp_energy_before_declared_chords": energy,
                "declared_relation_edges": relation_receipts,
            })
    return result, receipts


def grid_from_labels(
    labels: dict[tuple[int, int], int], descriptors: list[GlyphDescriptor]
) -> list[str]:
    rows = []
    for row in range(ROWS):
        rows.append("".join(
            descriptors[labels[(row, col)]].char if (row, col) in labels else " "
            for col in range(COLS)
        ))
    return rows


def grid_from_full(frame: FrameData, chars: list[str]) -> list[str]:
    return ["".join(chars[frame.full_winner[(row, col)]] if (row, col) in frame.full_winner else " "
                    for col in range(COLS)) for row in range(ROWS)]


def render_grid(grid: list[str], font: ImageFont.FreeTypeFont) -> Image.Image:
    image = Image.new("RGB", (COLS * CELL_PX + 8, ROWS * CELL_PX + 8), BG)
    draw = ImageDraw.Draw(image)
    for row, line in enumerate(grid):
        for col, char in enumerate(line):
            if char != " ":
                draw.text((4 + col * CELL_PX, 4 + row * CELL_PX), char, font=font, fill=FG)
    return image


def render_source_grid(frame: FrameData) -> Image.Image:
    scale = CELL_PX
    image = Image.new("RGB", (COLS * scale + 1, ROWS * scale + 1), BG)
    for row in range(ROWS):
        for col in range(COLS):
            cell = frame.cells[row][col]
            tile = Image.fromarray((cell.astype(np.uint8) * 220)).resize((scale, scale))
            rgb = Image.merge("RGB", (Image.new("L", tile.size, 30), tile, Image.new("L", tile.size, 30)))
            image.paste(rgb, (col * scale, row * scale))
    draw = ImageDraw.Draw(image)
    for x in range(0, COLS * scale + 1, scale):
        draw.line((x, 0, x, ROWS * scale), fill=(80, 80, 80))
    for y in range(0, ROWS * scale + 1, scale):
        draw.line((0, y, COLS * scale, y), fill=(80, 80, 80))
    for row in range(ROWS):
        for col in range(COLS):
            draw.text((col * scale + 2, row * scale + 1), f"{row}{col}", fill=LABEL)
    return image


def write_source_sheet(datasets: dict[str, list[FrameData]], out: Path) -> Path:
    label_font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 13)
    tiles = {key: [render_source_grid(frame) for frame in frames] for key, frames in datasets.items()}
    tile_w, tile_h = next(iter(tiles.values()))[0].size
    width = PANEL_PAD + 8 * (tile_w + PANEL_PAD)
    height = PANEL_PAD + len(datasets) * (tile_h + 34 + PANEL_PAD)
    sheet = Image.new("RGB", (width, height), (5, 5, 5))
    draw = ImageDraw.Draw(sheet)
    for dataset_index, (dataset, frames) in enumerate(datasets.items()):
        y = PANEL_PAD + dataset_index * (tile_h + 34 + PANEL_PAD)
        for frame_index, frame in enumerate(frames):
            x = PANEL_PAD + frame_index * (tile_w + PANEL_PAD)
            draw.text((x, y), f"{dataset}:{frame.key}", fill=LABEL, font=label_font)
            sheet.paste(tiles[dataset][frame_index], (x, y + 22))
    path = out / "source_cell_coordinates.png"
    sheet.save(path)
    return path


def connection_breaks(
    frames: list[FrameData],
    labels: list[dict[tuple[int, int], int]],
    descriptors: list[GlyphDescriptor],
) -> dict:
    desired = 0
    broken = 0
    unwanted = 0
    for frame, frame_labels in zip(frames, labels):
        for pos, local in frame_labels.items():
            row, col = pos
            for other, side, opposite in neighbors(pos):
                if other not in frame_labels or other < pos:
                    continue
                source_a = port_vector(frame.source_masks[pos], side)
                source_b = port_vector(frame.source_masks[other], opposite)
                glyph_a = descriptors[local].ports[side]
                glyph_b = descriptors[frame_labels[other]].ports[opposite]
                source_join = float(np.mean(np.sqrt(source_a * source_b)))
                glyph_join = float(np.mean(np.sqrt(glyph_a * glyph_b)))
                wants = source_join >= 0.12
                has = glyph_join >= 0.12
                desired += int(wants)
                broken += int(wants and not has)
                unwanted += int(not wants and has)
    return {"desired_connections": desired, "broken_connections": broken, "unwanted_connections": unwanted}


def track_metrics(track_receipts: list[dict], descriptors_by_id: dict[int, GlyphDescriptor]) -> dict:
    edges = 0
    literal_switches = 0
    unexplained = 0
    loop_costs = []
    adjacent_costs = []
    declared_edges = 0
    declared_edges_passing = 0
    for track in track_receipts:
        ids = track["glyph_ids"]
        transforms = track["expected_transforms"]
        costs = []
        for index, glyph_id in enumerate(ids):
            next_id = ids[(index + 1) % len(ids)]
            edges += 1
            literal_switches += int(glyph_id != next_id)
            a = descriptors_by_id[glyph_id]
            b = descriptors_by_id[next_id]
            distance = mask_distance(a.mask, b.mask, transforms[index])
            costs.append(distance)
            unexplained += int(distance > 0.46)
        adjacent_costs.extend(costs[:-1])
        loop_costs.append(costs[-1])
        for edge in track.get("declared_relation_edges", []):
            declared_edges += 1
            declared_edges_passing += int(edge["passes_diagnostic_threshold_0_46"])
    median_adjacent = float(np.median(adjacent_costs)) if adjacent_costs else 0.0
    return {
        "track_edges": edges,
        "literal_glyph_switches": literal_switches,
        "unexplained_switches_threshold_0_46": unexplained,
        "median_adjacent_transform_cost": median_adjacent,
        "max_loop_transform_cost": max(loop_costs, default=0.0),
        "loop_cost_le_median_adjacent": bool(loop_costs and max(loop_costs) <= median_adjacent),
        "declared_relation_edges": declared_edges,
        "declared_relation_edges_passing_threshold": declared_edges_passing,
        "declared_relation_accuracy": (
            declared_edges_passing / declared_edges if declared_edges else None
        ),
    }


def write_variant_artifacts(
    dataset: str,
    frames: list[FrameData],
    variants: dict[str, list[list[str]]],
    out: Path,
) -> dict:
    font = ImageFont.truetype(um.UNIFONT, CELL_PX - 4)
    label_font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 13)
    receipt: dict[str, dict] = {}
    rendered: dict[str, list[Image.Image]] = {}
    for variant, grids in variants.items():
        variant_dir = out / dataset / variant
        variant_dir.mkdir(parents=True, exist_ok=True)
        rendered[variant] = []
        text_hashes = []
        for frame, grid in zip(frames, grids):
            text = "\n".join(grid) + "\n"
            text_path = variant_dir / f"{frame.key}.txt"
            text_path.write_text(text)
            image = render_grid(grid, font)
            image.save(variant_dir / f"{frame.key}.png")
            rendered[variant].append(image)
            text_hashes.append(sha256(text_path))
        gif_path = variant_dir / f"{dataset}_{variant}.gif"
        rendered[variant][0].save(
            gif_path,
            save_all=True,
            append_images=rendered[variant][1:],
            duration=220 if dataset == "walk_e" else 320,
            loop=0,
            disposal=2,
        )
        receipt[variant] = {
            "gif": str(gif_path.relative_to(REPO)),
            "gif_sha256": sha256(gif_path),
            "text_sha256": text_hashes,
        }
        if dataset == "horse":
            viewer_path = variant_dir / "horse_viewer.txt"
            viewer_lines = [
                f"# HORSE -- FL-4512 P4b diagnostic variant {variant}",
                "# one frozen P400 pose per direction",
                "",
            ]
            for frame, grid in zip(frames, grids):
                viewer_lines.append(f"# {frame.key}")
                viewer_lines.extend(grid)
                viewer_lines.append("")
            viewer_path.write_text("\n".join(viewer_lines))
            receipt[variant]["viewer"] = str(viewer_path.relative_to(REPO))
            receipt[variant]["viewer_sha256"] = sha256(viewer_path)

    tile_w, tile_h = next(iter(rendered.values()))[0].size
    width = PANEL_PAD + len(frames) * (tile_w + PANEL_PAD)
    height = PANEL_PAD + len(variants) * (tile_h + 30 + PANEL_PAD)
    sheet = Image.new("RGB", (width, height), (5, 5, 5))
    draw = ImageDraw.Draw(sheet)
    for variant_index, (variant, images) in enumerate(rendered.items()):
        y = PANEL_PAD + variant_index * (tile_h + 30 + PANEL_PAD)
        for frame_index, (frame, image) in enumerate(zip(frames, images)):
            x = PANEL_PAD + frame_index * (tile_w + PANEL_PAD)
            draw.text((x, y), f"{variant}:{frame.key}", fill=LABEL, font=label_font)
            sheet.paste(image, (x, y + 20))
    sheet_path = out / dataset / f"{dataset}_comparison.png"
    sheet.save(sheet_path)
    receipt["comparison"] = {
        "path": str(sheet_path.relative_to(REPO)),
        "sha256": sha256(sheet_path),
    }
    return receipt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=REPO / "scripts" / "fixtures" / "fl4512_p4b_tracks_v1.json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=CLIPASSO / "artifacts" / "fl4512-p4b-structural-feasibility-v1",
    )
    parser.add_argument("--inspect-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    full_chars, full_feats, descriptors, atlas_receipt = build_structural_atlas()
    datasets = {
        dataset: [load_frame(key, svg, full_feats, descriptors) for key, svg in specs]
        for dataset, specs in frame_specs().items()
    }
    source_sheet = write_source_sheet(datasets, args.out)
    if args.inspect_only:
        print(source_sheet)
        return 0

    manifest = json.loads(args.manifest.read_text())
    if manifest.get("schema") != "fl4512.p4b_authored_tracks.v1":
        raise ValueError("unexpected authored-track manifest schema")
    if manifest.get("source_geometry") != "clipasso-p400-frozen-20260716":
        raise ValueError("manifest is not pinned to the frozen P400 geometry")

    receipt: dict = {
        "schema": SCHEMA,
        "status": "DIAGNOSTIC_ONLY_NOT_P4B_PASS",
        "ownership": {
            "runtime_writes": False,
            "u6_v2_cutover": False,
            "final_owner_claim": False,
            "authored_track_manifest": str(args.manifest.relative_to(REPO)),
        },
        "parameters": {
            "grid": [COLS, ROWS],
            "top_k": TOP_K,
            "lambda_spatial": LAMBDA_SPATIAL,
            "lambda_temporal": LAMBDA_TEMPORAL,
            "lambda_topology": LAMBDA_TOPOLOGY,
            "lambda_declared_relation": LAMBDA_DECLARED_RELATION,
            "max_spatial_sweeps": MAX_SPATIAL_SWEEPS,
        },
        "atlas": atlas_receipt,
        "manifest_sha256": sha256(args.manifest),
        "source_cell_sheet": {
            "path": str(source_sheet.relative_to(REPO)),
            "sha256": sha256(source_sheet),
        },
        "datasets": {},
    }
    descriptor_by_id = {descriptor.glyph_id: descriptor for descriptor in descriptors}

    for dataset, frames in datasets.items():
        independent = [independent_labels(frame) for frame in frames]
        spatial = [spatial_refine(frame, descriptors, labels) for frame, labels in zip(frames, independent)]
        full, track_receipts = apply_tracks(dataset, frames, spatial, manifest, descriptors)

        variants = {
            "current_full_atlas": [grid_from_full(frame, full_chars) for frame in frames],
            "restricted_independent": [grid_from_labels(labels, descriptors) for labels in independent],
            "spatial_connectivity": [grid_from_labels(labels, descriptors) for labels in spatial],
            "structural_tracks": [grid_from_labels(labels, descriptors) for labels in full],
        }
        artifacts = write_variant_artifacts(dataset, frames, variants, args.out)
        receipt["datasets"][dataset] = {
            "frames": [frame.key for frame in frames],
            "source_svgs": [
                {"path": str(frame.svg.relative_to(REPO)), "sha256": sha256(frame.svg)}
                for frame in frames
            ],
            "occupied_cells": [len(frame.source_masks) for frame in frames],
            "tracks": track_receipts,
            "metrics": {
                "restricted_independent": connection_breaks(frames, independent, descriptors),
                "spatial_connectivity": connection_breaks(frames, spatial, descriptors),
                "structural_tracks": connection_breaks(frames, full, descriptors),
                "track_transitions": track_metrics(track_receipts, descriptor_by_id),
            },
            "artifacts": artifacts,
        }

    # The first serialization is included in the deterministic product hash.
    receipt["deterministic_product_sha256"] = json_sha256(receipt)
    receipt_path = args.out / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({
        "receipt": str(receipt_path),
        "deterministic_product_sha256": receipt["deterministic_product_sha256"],
        "walk_metrics": receipt["datasets"]["walk_e"]["metrics"],
        "horse_metrics": receipt["datasets"]["horse"]["metrics"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
