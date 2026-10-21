#!/usr/bin/env python3
"""FL-4561 semantic-caricature Unicode walk experiment.

This offline compiler consumes the accepted T140724 colour-keyed skeleton
directly.  It retargets the six persistent parts to a three-lane semantic body
plan, expands that plan into a five-column presentation lattice, discovers
whole-chain Unicode tuple candidates, solves each closed walk cycle, then runs
a whole-pose block-coordinate refinement.  Cells are raster destinations only:
no independent cell variable selects a GlyphId.

The command writes an experimental, versioned 2x4 Desktop GIF plus a tracked
byte-identical copy and manifest.  It does not write runtime GlyphIds.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import deque
from datetime import datetime
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
TRACK_DIR = REPO / "docs/research/ascii/verification/fl4547/skeleton-versions"
REGISTRY = TRACK_DIR / "registry.json"
DESKTOP = Path.home() / "Desktop"

sys.path.insert(0, str(REPO / "scripts"))
import fl4547_arc_skeletal as arc
import fl4547_skeleton_walk as source


GRID_W = GRID_H = 5
LATENT_GRID = (3, 5)
SUBPIXELS = 8
FRAME_COUNT = 18
DEFAULT_TOP_K = 10
DEFAULT_BEAM = 18
GLOBAL_ROUNDS = 3
LAMBDA_TEMPORAL = 1.65
LAMBDA_GLOBAL = 0.85
LAMBDA_OFF_CORRIDOR = 1.35
LAMBDA_COUNTERPART = 0.72

# The three semantic columns are left / spine / right.  Distal arms expand to
# the outer presentation columns while legs stay one column from the spine.
CHAIN_SLOTS = {
    "head": ((2, 0),),
    "torso": ((2, 1), (2, 2)),
    "arm_left": ((1, 1), (0, 2)),
    "arm_right": ((3, 1), (4, 2)),
    "leg_left": ((1, 3), (1, 4)),
    "leg_right": ((3, 3), (3, 4)),
}
CHAIN_ORDER = tuple(CHAIN_SLOTS)
MIDLINE_CHAINS = {"head", "torso"}


def _bit(x: int, y: int) -> int:
    return 1 << (8 * y + x)


# Exact shared pixels make the complete 5x5 glyph figure one 8-connected
# component.  Every glyph must itself be a single ink component.
REQUIRED_PORTS = {
    "head": (_bit(3, 7),),
    "torso": (
        _bit(3, 0) | _bit(3, 7) | _bit(0, 4) | _bit(7, 4),
        _bit(3, 0) | _bit(0, 7) | _bit(7, 7),
    ),
    "arm_left": (
        _bit(7, 4) | _bit(0, 7),
        _bit(7, 0),
    ),
    "arm_right": (
        _bit(0, 4) | _bit(7, 7),
        _bit(0, 0),
    ),
    "leg_left": (
        _bit(7, 0) | _bit(4, 7),
        _bit(4, 0),
    ),
    "leg_right": (
        _bit(0, 0) | _bit(3, 7),
        _bit(3, 0),
    ),
}

# Canonical paths are expressed in the 40x40 presentation raster.  Limb motion
# is added as a bounded residual around these intentionally exaggerated paths.
BASE_PATHS = {
    "arm_left": ((15.0, 12.0), (8.0, 15.0), (1.5, 21.5)),
    "arm_right": ((24.0, 12.0), (31.0, 15.0), (38.0, 21.5)),
    "leg_left": ((16.0, 23.0), (12.0, 31.0), (11.0, 39.0)),
    "leg_right": ((23.0, 23.0), (28.0, 31.0), (29.0, 39.0)),
}


def readiness_contract() -> dict:
    return {
        "schema": "fl4561.semantic_caricature_readiness.v1",
        "ready": True,
        "accepted_source_owner": "T140724 colour-keyed per-part skeleton",
        "semantic_layout_owner": "5x3 latent body plan expanded into 5x5",
        "final_selection_owner": "(direction, semantic_chain, closed_cycle)",
        "chain_sequence_candidate_owner": True,
        "persistent_chain_slot_owner": True,
        "semantic_joint_compositor_owner": True,
        "explicit_collision_occlusion_owner": True,
        "off_corridor_ink_cost": True,
        "sequence_level_temporal_solver": True,
        "sequence_level_counterpart_solver": True,
        "cell_is_raster_destination_only": True,
        "runtime_glyph_writer": False,
    }


def _centroid_x(mask) -> float:
    import numpy as np

    _ys, xs = np.where(mask)
    return float(xs.mean()) if len(xs) else 0.0


def lateral_role_map(frames: list[dict]) -> dict[str, str]:
    """Assign persistent source colours to view-relative left/right lanes."""
    import numpy as np

    result = {"head": "head", "torso": "torso"}
    for family in ("arm", "leg"):
        parts = (f"{family}_a", f"{family}_b")
        means = {
            part: float(np.median([
                _centroid_x(frame[part]) for frame in frames if part in frame
            ]))
            for part in parts
        }
        ordered = sorted(parts, key=lambda part: (means[part], part))
        result[ordered[0]] = f"{family}_left"
        result[ordered[1]] = f"{family}_right"
    return result


def _sample_path(path: list[tuple[float, float]], count: int = 9) -> list[tuple[float, float]]:
    return [source.point_on_path(path, index / (count - 1)) for index in range(count)]


def _cyclic_fill_samples(sampled):
    """Interpolate fully occluded chain geometry between visible cycle frames."""
    import numpy as np

    visible = [index for index, value in enumerate(sampled) if value is not None]
    if not visible:
        raise ValueError("semantic chain is occluded for the complete cycle")
    filled = list(sampled)
    frame_count = len(sampled)
    for frame in range(frame_count):
        if filled[frame] is not None:
            continue
        previous = next(
            (candidate for step in range(1, frame_count + 1)
             if (candidate := (frame - step) % frame_count) in visible),
            None,
        )
        following = next(
            (candidate for step in range(1, frame_count + 1)
             if (candidate := (frame + step) % frame_count) in visible),
            None,
        )
        if previous is None or following is None:
            raise ValueError("failed to bracket an occluded semantic chain")
        span = (following - previous) % frame_count
        offset = (frame - previous) % frame_count
        alpha = offset / max(1, span)
        filled[frame] = (
            (1.0 - alpha) * np.asarray(filled[previous], dtype=float)
            + alpha * np.asarray(filled[following], dtype=float)
        ).tolist()
    return filled


def _limb_samples(frames: list[dict], part: str):
    sampled = []
    visibility = []
    for frame in frames:
        visible = part in frame and bool(frame[part].any())
        visibility.append(visible)
        if not visible:
            sampled.append(None)
            continue
        path = source.longest_skeleton_path(frame[part])
        path = source._path_proximal_first(path, frame["torso"])
        if not path:
            raise ValueError(f"accepted skeleton has empty {part} path")
        sampled.append(_sample_path(path))
    return _cyclic_fill_samples(sampled), visibility


def _base_point(chain: str, fraction: float) -> tuple[float, float]:
    points = BASE_PATHS[chain]
    if fraction <= 0.5:
        alpha = fraction * 2.0
        a, b = points[0], points[1]
    else:
        alpha = (fraction - 0.5) * 2.0
        a, b = points[1], points[2]
    return (a[0] + (b[0] - a[0]) * alpha,
            a[1] + (b[1] - a[1]) * alpha)


def _allowed_bounds(chain: str, fraction: float) -> tuple[float, float, float, float]:
    slot_index = 0 if fraction <= 0.5 else 1
    col, row = CHAIN_SLOTS[chain][slot_index]
    return (col * 8.0, row * 8.0, col * 8.0 + 7.0, row * 8.0 + 7.0)


def _retarget_limb_paths(frames: list[dict], part: str, chain: str):
    import numpy as np

    sampled, visibility = _limb_samples(frames, part)
    samples = np.asarray(sampled, dtype=float)
    mean = samples.mean(axis=0)
    span = np.maximum(samples.max(axis=(0, 1)) - samples.min(axis=(0, 1)), 1.0)
    paths = []
    for frame_index in range(len(frames)):
        points = []
        for sample_index in range(samples.shape[1]):
            fraction = sample_index / (samples.shape[1] - 1)
            base_x, base_y = _base_point(chain, fraction)
            residual = (samples[frame_index, sample_index] - mean[sample_index]) / span
            # The joint is anchored; distal motion is exaggerated gradually.
            motion_weight = min(1.0, fraction * 1.45)
            x = base_x + float(residual[0]) * 6.0 * motion_weight
            y = base_y + float(residual[1]) * 7.0 * motion_weight
            x0, y0, x1, y1 = _allowed_bounds(chain, fraction)
            x = min(x1, max(x0, x))
            y = min(y1, max(y0, y))
            points.append((x, y))
        # The two segments meet at one exact diagonal boundary port.
        points[0] = BASE_PATHS[chain][0]
        points[len(points) // 2] = BASE_PATHS[chain][1]
        paths.append(points)
    return paths, visibility


def _image_to_mask8(image) -> int:
    value = 0
    pixels = image.load()
    for y in range(8):
        for x in range(8):
            if pixels[x, y] > 0:
                value |= 1 << (8 * y + x)
    return value


def _fixed_midline_targets() -> dict[str, list[int]]:
    from PIL import Image, ImageDraw

    head = Image.new("L", (8, 8), 0)
    draw = ImageDraw.Draw(head)
    draw.line((3, 1, 3, 7), fill=255, width=1)
    draw.line((1, 2, 5, 2), fill=255, width=1)

    torso_upper = Image.new("L", (8, 8), 0)
    draw = ImageDraw.Draw(torso_upper)
    draw.line((3, 0, 3, 7), fill=255, width=1)
    draw.line((0, 4, 7, 4), fill=255, width=1)

    torso_lower = Image.new("L", (8, 8), 0)
    draw = ImageDraw.Draw(torso_lower)
    draw.line((3, 0, 3, 3), fill=255, width=1)
    draw.line((3, 3, 0, 7), fill=255, width=1)
    draw.line((3, 3, 7, 7), fill=255, width=1)
    return {
        "head": [_image_to_mask8(head) | REQUIRED_PORTS["head"][0]],
        "torso": [
            _image_to_mask8(torso_upper) | REQUIRED_PORTS["torso"][0],
            _image_to_mask8(torso_lower) | REQUIRED_PORTS["torso"][1],
        ],
    }


def _path_targets(chain: str, points: list[tuple[float, float]]) -> list[int]:
    from PIL import Image, ImageDraw

    canvas = Image.new("L", (GRID_W * 8, GRID_H * 8), 0)
    draw = ImageDraw.Draw(canvas)
    draw.line([(round(x), round(y)) for x, y in points], fill=255, width=1, joint="curve")
    targets = []
    for slot_index, (col, row) in enumerate(CHAIN_SLOTS[chain]):
        crop = canvas.crop((col * 8, row * 8, (col + 1) * 8, (row + 1) * 8))
        targets.append(_image_to_mask8(crop) | REQUIRED_PORTS[chain][slot_index])
    return targets


def retarget_direction(frames: list[dict]) -> dict:
    roles = lateral_role_map(frames)
    fixed = _fixed_midline_targets()
    by_chain = {chain: None for chain in CHAIN_ORDER}
    visibility = {
        "head": ["head" in frame for frame in frames],
        "torso": ["torso" in frame for frame in frames],
    }
    by_chain["head"] = [list(fixed["head"]) for _ in frames]
    by_chain["torso"] = [list(fixed["torso"]) for _ in frames]
    for part, chain in roles.items():
        if chain in MIDLINE_CHAINS:
            continue
        paths, chain_visibility = _retarget_limb_paths(frames, part, chain)
        by_chain[chain] = [_path_targets(chain, path) for path in paths]
        visibility[chain] = chain_visibility
    if any(value is None for value in by_chain.values()):
        raise ValueError(f"incomplete semantic role map: {roles}")
    return {"roles": roles, "targets": by_chain, "visibility": visibility}


def _candidate_cost(target: int, index: int, catalog: dict) -> float:
    mask = int(catalog["masks"][index])
    off_corridor = (mask & ~target).bit_count() / 64.0
    return source._glyph_index_cost(target, index, catalog) + LAMBDA_OFF_CORRIDOR * off_corridor


def chain_candidates(chain: str, targets: list[int], catalog: dict,
                     component_cache: dict, top_k: int, beam_size: int) -> list[dict]:
    local_sets = []
    for target, required in zip(targets, REQUIRED_PORTS[chain]):
        candidates = arc.local_candidates(
            target, required, catalog, component_cache, max(top_k * 3, 24)
        )
        scored = sorted(
            ((_candidate_cost(target, candidate["index"], catalog),
              int(catalog["scalars"][candidate["index"]]), candidate["index"])
             for candidate in candidates),
            key=lambda item: item[:2],
        )[:top_k]
        local_sets.append(scored)

    beam = [(0.0, tuple())]
    for slot_candidates in local_sets:
        expanded = []
        for cost, indices in beam:
            for slot_cost, scalar, index in slot_candidates:
                expanded.append((cost + slot_cost, indices + (index,), scalar))
        expanded.sort(key=lambda item: (item[0], item[2], item[1]))
        beam = [(cost, indices) for cost, indices, _scalar in expanded[:beam_size]]
    return [
        {
            "indices": indices,
            "masks": tuple(int(catalog["masks"][index]) for index in indices),
            "unary": cost,
        }
        for cost, indices in beam
    ]


def _tuple_distance(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    return sum((ma ^ mb).bit_count() for ma, mb in zip(a, b)) / (64.0 * len(a))


def flip_mask_horizontal(mask: int) -> int:
    flipped = 0
    for y in range(8):
        for x in range(8):
            if mask & _bit(x, y):
                flipped |= _bit(7 - x, y)
    return flipped


def counterpart_cost(candidate: dict, counterpart: dict) -> float:
    reflected = tuple(flip_mask_horizontal(mask) for mask in counterpart["masks"])
    return _tuple_distance(candidate["masks"], reflected)


def temporal_cost(candidate_a: dict, candidate_b: dict,
                  target_a: list[int], target_b: list[int]) -> float:
    target_change = _tuple_distance(tuple(target_a), tuple(target_b))
    glyph_change = _tuple_distance(candidate_a["masks"], candidate_b["masks"])
    unexplained = max(0.0, glyph_change - target_change)
    return abs(glyph_change - target_change) + 0.75 * unexplained


def solve_cyclic_chain(banks: list[list[dict]], targets: list[list[int]]) -> list[int]:
    if not banks or not banks[0]:
        raise ValueError("empty chain candidate lattice")
    best = None
    for start in range(len(banks[0])):
        costs = {start: banks[0][start]["unary"]}
        paths = {start: [start]}
        for frame in range(1, len(banks)):
            next_costs = {}
            next_paths = {}
            for current, candidate in enumerate(banks[frame]):
                choice = min(
                    (
                        cost + candidate["unary"]
                        + LAMBDA_TEMPORAL * temporal_cost(
                            banks[frame - 1][previous], candidate,
                            targets[frame - 1], targets[frame],
                        ),
                        previous,
                    )
                    for previous, cost in costs.items()
                )
                next_costs[current] = choice[0]
                next_paths[current] = paths[choice[1]] + [current]
            costs, paths = next_costs, next_paths
        for last, cost in costs.items():
            total = cost + LAMBDA_TEMPORAL * temporal_cost(
                banks[-1][last], banks[0][start], targets[-1], targets[0]
            )
            candidate = (total, paths[last])
            if best is None or candidate < best:
                best = candidate
    return best[1]


def _full_frame_masks(retargeted: dict, selected: dict, banks: dict,
                      frame: int) -> tuple[list[int], list[int]]:
    target = [0] * (GRID_W * GRID_H)
    glyph = [0] * (GRID_W * GRID_H)
    for chain in CHAIN_ORDER:
        candidate = banks[chain][frame][selected[chain][frame]]
        for slot_index, (col, row) in enumerate(CHAIN_SLOTS[chain]):
            cell = row * GRID_W + col
            target[cell] = retargeted["targets"][chain][frame][slot_index]
            glyph[cell] = candidate["masks"][slot_index]
    return target, glyph


def _component_count(frame_masks: list[int]) -> int:
    points = set()
    for cell, mask in enumerate(frame_masks):
        col, row = cell % GRID_W, cell // GRID_W
        for y in range(8):
            for x in range(8):
                if mask & _bit(x, y):
                    points.add((col * 8 + x, row * 8 + y))
    count = 0
    while points:
        count += 1
        queue = deque([points.pop()])
        while queue:
            x, y = queue.popleft()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    neighbor = (x + dx, y + dy)
                    if neighbor in points:
                        points.remove(neighbor)
                        queue.append(neighbor)
    return count


def _pose_cost(target: list[int], glyph: list[int]) -> float:
    xor = sum((a ^ b).bit_count() for a, b in zip(target, glyph)) / (64.0 * len(target))
    off = sum((b & ~a).bit_count() for a, b in zip(target, glyph)) / (64.0 * len(target))
    components = _component_count(glyph)
    return xor + 1.25 * off + 4.0 * max(0, components - 1)


def refine_whole_pose(retargeted: dict, banks: dict, selected: dict) -> dict:
    selected = {chain: list(indices) for chain, indices in selected.items()}
    for _round in range(GLOBAL_ROUNDS):
        changes = 0
        for frame in range(FRAME_COUNT):
            previous = (frame - 1) % FRAME_COUNT
            following = (frame + 1) % FRAME_COUNT
            for chain in CHAIN_ORDER:
                if chain in MIDLINE_CHAINS:
                    continue
                current = selected[chain][frame]
                scored = []
                for candidate_index, candidate in enumerate(banks[chain][frame]):
                    selected[chain][frame] = candidate_index
                    target, glyph = _full_frame_masks(retargeted, selected, banks, frame)
                    cost = candidate["unary"] + LAMBDA_GLOBAL * _pose_cost(target, glyph)
                    cost += LAMBDA_TEMPORAL * temporal_cost(
                        banks[chain][previous][selected[chain][previous]], candidate,
                        retargeted["targets"][chain][previous],
                        retargeted["targets"][chain][frame],
                    )
                    cost += LAMBDA_TEMPORAL * temporal_cost(
                        candidate, banks[chain][following][selected[chain][following]],
                        retargeted["targets"][chain][frame],
                        retargeted["targets"][chain][following],
                    )
                    scalar_tuple = tuple(
                        int(banks[chain][frame][candidate_index]["indices"][slot])
                        for slot in range(len(CHAIN_SLOTS[chain]))
                    )
                    scored.append((cost, scalar_tuple, candidate_index))
                chosen = min(scored)[2]
                changes += chosen != current
                selected[chain][frame] = chosen
        if changes == 0:
            break
    return selected


def _materialize_frames(retargeted: dict, result: dict, catalog: dict) -> None:
    banks = result["banks"]
    selected = result["selected"]
    frames = []
    for frame in range(FRAME_COUNT):
        indices = [catalog["space_index"]] * (GRID_W * GRID_H)
        owners = {}
        for chain in CHAIN_ORDER:
            candidate = banks[chain][frame][selected[chain][frame]]
            for slot_index, (col, row) in enumerate(CHAIN_SLOTS[chain]):
                cell = row * GRID_W + col
                indices[cell] = candidate["indices"][slot_index]
                owners[cell] = chain
        target, glyph = _full_frame_masks(retargeted, selected, banks, frame)
        frames.append({
            "indices": indices,
            "owners": owners,
            "targets": target,
            "glyph_masks": glyph,
            "components": _component_count(glyph),
        })
    result["frames"] = frames


def compile_direction(retargeted: dict, catalog: dict, component_cache: dict,
                      top_k: int, beam_size: int) -> dict:
    banks = {}
    selected = {}
    for chain in CHAIN_ORDER:
        targets = retargeted["targets"][chain]
        banks[chain] = [
            chain_candidates(chain, frame_targets, catalog, component_cache, top_k, beam_size)
            for frame_targets in targets
        ]
        if chain in MIDLINE_CHAINS:
            # Midline glyph tuples are explicitly frozen across the cycle.
            pool = banks[chain][0]
            chosen = min(
                (
                    sum(_candidate_cost(targets[frame][slot], candidate["indices"][slot], catalog)
                        for frame in range(FRAME_COUNT)
                        for slot in range(len(CHAIN_SLOTS[chain]))),
                    tuple(int(catalog["scalars"][index]) for index in candidate["indices"]),
                    candidate_index,
                )
                for candidate_index, candidate in enumerate(pool)
            )[2]
            selected[chain] = [chosen] * FRAME_COUNT
            banks[chain] = [pool for _ in range(FRAME_COUNT)]
        else:
            selected[chain] = solve_cyclic_chain(banks[chain], targets)
    selected = refine_whole_pose(retargeted, banks, selected)
    result = {"banks": banks, "selected": selected}
    _materialize_frames(retargeted, result, catalog)
    return result


def _refine_chain_against(retargeted_by_dir: dict, compiled: dict,
                          direction: str, chain: str,
                          counterpart_direction: str, counterpart_chain: str) -> int:
    result = compiled[direction]
    counterpart_result = compiled[counterpart_direction]
    changes = 0
    for frame in range(FRAME_COUNT):
        previous = (frame - 1) % FRAME_COUNT
        following = (frame + 1) % FRAME_COUNT
        target_candidate = counterpart_result["banks"][counterpart_chain][frame][
            counterpart_result["selected"][counterpart_chain][frame]
        ]
        scored = []
        for candidate_index, candidate in enumerate(result["banks"][chain][frame]):
            cost = candidate["unary"] + LAMBDA_COUNTERPART * counterpart_cost(
                candidate, target_candidate
            )
            cost += LAMBDA_TEMPORAL * temporal_cost(
                result["banks"][chain][previous][result["selected"][chain][previous]],
                candidate,
                retargeted_by_dir[direction]["targets"][chain][previous],
                retargeted_by_dir[direction]["targets"][chain][frame],
            )
            cost += LAMBDA_TEMPORAL * temporal_cost(
                candidate,
                result["banks"][chain][following][result["selected"][chain][following]],
                retargeted_by_dir[direction]["targets"][chain][frame],
                retargeted_by_dir[direction]["targets"][chain][following],
            )
            scored.append((cost, candidate["indices"], candidate_index))
        current = result["selected"][chain][frame]
        chosen = min(scored)[2]
        changes += chosen != current
        result["selected"][chain][frame] = chosen
    return changes


def refine_counterparts(retargeted_by_dir: dict, compiled: dict, catalog: dict) -> list[dict]:
    """Soft paired-aspect glyph refinement; equality is never imposed."""
    receipts = []
    chain_pairs = (
        ("arm_left", "arm_right"),
        ("arm_right", "arm_left"),
        ("leg_left", "leg_right"),
        ("leg_right", "leg_left"),
    )
    direction_pairs = (("W", "E"), ("NW", "NE"), ("SW", "SE"))
    for direction_a, direction_b in direction_pairs:
        changes = 0
        for _round in range(2):
            for chain_a, chain_b in chain_pairs:
                changes += _refine_chain_against(
                    retargeted_by_dir, compiled, direction_b, chain_b, direction_a, chain_a
                )
                changes += _refine_chain_against(
                    retargeted_by_dir, compiled, direction_a, chain_a, direction_b, chain_b
                )
        receipts.append({
            "directions": [direction_a, direction_b],
            "relation": "soft horizontal glyph-geometry reflection with independent unary evidence",
            "state_changes": changes,
        })
    for direction in ("S", "N"):
        changes = 0
        for _round in range(2):
            for chain_a, chain_b in (("arm_left", "arm_right"), ("leg_left", "leg_right")):
                changes += _refine_chain_against(
                    retargeted_by_dir, compiled, direction, chain_b, direction, chain_a
                )
                changes += _refine_chain_against(
                    retargeted_by_dir, compiled, direction, chain_a, direction, chain_b
                )
        receipts.append({
            "directions": [direction],
            "relation": "soft within-view anatomical reflection",
            "state_changes": changes,
        })
    for direction in source.DISPLAY_DIRS:
        _materialize_frames(retargeted_by_dir[direction], compiled[direction], catalog)
    return receipts


def metrics(compiled: dict, catalog: dict) -> dict:
    components = []
    unique = set()
    midline_changes = 0
    chain_changes = 0
    off_corridor = 0
    hard_port_violations = 0
    for result in compiled.values():
        components.extend(frame["components"] for frame in result["frames"])
        for frame in result["frames"]:
            for cell, index in enumerate(frame["indices"]):
                if index == catalog["space_index"]:
                    continue
                unique.add(index)
                off_corridor += (frame["glyph_masks"][cell] & ~frame["targets"][cell]).bit_count()
        for chain in CHAIN_ORDER:
            states = result["selected"][chain]
            changes = sum(states[index] != states[(index + 1) % FRAME_COUNT]
                          for index in range(FRAME_COUNT))
            chain_changes += changes
            if chain in MIDLINE_CHAINS:
                midline_changes += changes
        for frame in result["frames"]:
            for chain in CHAIN_ORDER:
                for slot_index, (col, row) in enumerate(CHAIN_SLOTS[chain]):
                    cell = row * GRID_W + col
                    if (frame["glyph_masks"][cell] & REQUIRED_PORTS[chain][slot_index]) != REQUIRED_PORTS[chain][slot_index]:
                        hard_port_violations += 1
    return {
        "output_component_min": min(components),
        "output_component_max": max(components),
        "hard_port_violation_count": hard_port_violations,
        "midline_chain_state_change_count": midline_changes,
        "all_chain_state_change_count": chain_changes,
        "unique_glyph_count": len(unique),
        "off_corridor_ink_pixels": off_corridor,
        "cell_selection_variables": 0,
        "chain_cycle_variables": len(source.DISPLAY_DIRS) * len(CHAIN_ORDER),
    }


def render_and_write(retargeted_by_dir: dict, compiled: dict, catalog: dict,
                     source_hashes: dict, bboxes: dict, frame_ms: int,
                     counterpart_receipts: list[dict]) -> dict:
    from PIL import Image, ImageDraw, ImageFont

    font_chain, font_receipt = source._canonical_glyph_renderer()
    tile_cache = {}

    def tile(index: int, cell_px: int):
        scalar = int(catalog["scalars"][index])
        if scalar not in tile_cache:
            rendered = font_chain.render(scalar)
            if rendered is None:
                raise ValueError(f"selected U+{scalar:04X} lacks a pinned font owner")
            rgba = Image.frombytes("RGBA", (font_chain.cell_px, font_chain.cell_px), rendered.rgba8)
            base = Image.new("RGB", rgba.size, (8, 8, 8))
            ink = Image.new("RGB", rgba.size, (120, 230, 120))
            base.paste(ink, mask=rgba.getchannel("A"))
            tile_cache[scalar] = base
        return tile_cache[scalar].resize((cell_px, cell_px), Image.Resampling.NEAREST)

    cell_px, label_h, pad = 24, 16, 6
    panel_w, panel_h = GRID_W * cell_px, GRID_H * cell_px
    canvas_w = pad + source.GRID_COLS * (panel_w + pad)
    canvas_h = pad + source.GRID_ROWS * (panel_h + label_h + pad)
    label_font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 12)
    gif_frames = []
    manifest_frames = {direction: [] for direction in source.DISPLAY_DIRS}
    for frame_index in range(FRAME_COUNT):
        canvas = Image.new("RGB", (canvas_w, canvas_h), (8, 8, 8))
        draw = ImageDraw.Draw(canvas)
        for direction_index, direction in enumerate(source.DISPLAY_DIRS):
            grid_col = direction_index % source.GRID_COLS
            grid_row = direction_index // source.GRID_COLS
            x0 = pad + grid_col * (panel_w + pad)
            y0 = pad + grid_row * (panel_h + label_h + pad)
            draw.text((x0, y0), f"{direction} f{frame_index}", fill=(150, 210, 150), font=label_font)
            frame = compiled[direction]["frames"][frame_index]
            rows = []
            cells = []
            for row in range(GRID_H):
                chars = []
                for col in range(GRID_W):
                    cell = row * GRID_W + col
                    index = frame["indices"][cell]
                    scalar = int(catalog["scalars"][index])
                    chars.append(chr(scalar))
                    if index != catalog["space_index"]:
                        canvas.paste(tile(index, cell_px),
                                     (x0 + col * cell_px, y0 + label_h + row * cell_px))
                    cells.append({
                        "cell": [col, row],
                        "semantic_owner": frame["owners"].get(cell),
                        "glyph_id": int(catalog["glyph_ids"][index]),
                        "unicode_scalar": scalar,
                        "character": chr(scalar),
                        "target_mask8_u64": f"0x{frame['targets'][cell]:016x}",
                        "glyph_mask8_u64": f"0x{frame['glyph_masks'][cell]:016x}",
                    })
                rows.append("".join(chars))
            manifest_frames[direction].append({
                "frame_index": frame_index,
                "rows": rows,
                "component_count": frame["components"],
                "cells": cells,
            })
        gif_frames.append(canvas)

    timestamp = datetime.now().strftime("v%Y%m%dT%H%M%S")
    name = f"{timestamp}__semantic-caricature-chain-5x5__walk_all_directions"
    TRACK_DIR.mkdir(parents=True, exist_ok=True)
    tracked_gif = TRACK_DIR / f"{name}.gif"
    tracked_manifest = TRACK_DIR / f"{name}.frames.json"
    desktop_gif = DESKTOP / f"{name}.gif"
    gif_frames[0].save(tracked_gif, save_all=True, append_images=gif_frames[1:],
                       duration=frame_ms, loop=0, disposal=2)
    desktop_gif.write_bytes(tracked_gif.read_bytes())

    measured = metrics(compiled, catalog)
    manifest = {
        "schema": "fl4561.semantic_caricature_chain.v1",
        "status": "experimental_operator_review_open",
        "primary_owner": "FL-4561",
        "lineage": ["FL-4512", "FL-4547"],
        "name": name,
        "timestamp": timestamp,
        "accepted_source": {
            "path": str(source.ACCEPTED_PERPART_CHECKPOINT.relative_to(REPO)),
            "sha256": source.ACCEPTED_PERPART_CHECKPOINT_SHA256,
            "aseprite_source_hashes": source_hashes,
            "stable_bboxes": bboxes,
        },
        "latent_grid": list(LATENT_GRID),
        "presentation_grid": [GRID_W, GRID_H],
        "frames_per_direction": FRAME_COUNT,
        "palette_mode": "canonical_full_unicode_non_emoji",
        "readiness": readiness_contract(),
        "chain_slots": {chain: [list(cell) for cell in slots]
                        for chain, slots in CHAIN_SLOTS.items()},
        "view_relative_roles": {
            direction: retargeted_by_dir[direction]["roles"]
            for direction in source.DISPLAY_DIRS
        },
        "objective": {
            "per_frame": "skeleton-curve fidelity plus off-corridor ink",
            "temporal": "glyph-curve change explained by target-curve change",
            "global": "whole-pose fidelity plus single-component connectivity",
            "counterpart": "soft reflected glyph-geometry distance with independent paired-view evidence",
            "loop_edge": True,
        },
        "counterpart_receipts": counterpart_receipts,
        "metrics": measured,
        "descriptor_census_sha256": catalog["descriptor_sha256"],
        "descriptor_manifest_sha256": catalog["manifest_sha256"],
        "font_chain": font_receipt,
        "frames": manifest_frames,
    }
    manifest["gif_sha256"] = hashlib.sha256(tracked_gif.read_bytes()).hexdigest()
    tracked_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    manifest_sha256 = hashlib.sha256(tracked_manifest.read_bytes()).hexdigest()
    registry = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else []
    registry.append({
        "name": name,
        "kind": "semantic-caricature-chain-5x5",
        "schema": manifest["schema"],
        "status": manifest["status"],
        "timestamp": timestamp,
        "grid": [GRID_W, GRID_H],
        "latent_grid": list(LATENT_GRID),
        "frames_per_dir": FRAME_COUNT,
        "palette_mode": manifest["palette_mode"],
        "accepted_source_sha256": source.ACCEPTED_PERPART_CHECKPOINT_SHA256,
        "tracked_gif": str(tracked_gif.relative_to(REPO)),
        "tracked_manifest": str(tracked_manifest.relative_to(REPO)),
        "desktop_gif": str(desktop_gif),
        "gif_sha256": manifest["gif_sha256"],
        "manifest_sha256": manifest_sha256,
        "metrics": measured,
    })
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=1) + "\n")
    return {
        "name": name,
        "desktop_gif": str(desktop_gif),
        "tracked_gif": str(tracked_gif),
        "tracked_manifest": str(tracked_manifest),
        "gif_sha256": manifest["gif_sha256"],
        "manifest_sha256": manifest_sha256,
        "metrics": measured,
    }


def run(args: argparse.Namespace) -> int:
    if not readiness_contract()["ready"]:
        raise SystemExit("semantic caricature compiler readiness contract is incomplete")
    skeletons, source_hashes, bboxes = source.load_accepted_perpart_skeletons(upscale=3)
    retargeted_by_dir = {
        direction: retarget_direction(skeletons[direction])
        for direction in source.DISPLAY_DIRS
    }
    catalog = source.load_descriptor_catalog()
    component_cache = {}
    compiled = {}
    for direction in source.DISPLAY_DIRS:
        print(f"compile {direction}: semantic caricature chains", flush=True)
        compiled[direction] = compile_direction(
            retargeted_by_dir[direction], catalog, component_cache,
            args.top_k, args.beam,
        )
    counterpart_receipts = refine_counterparts(retargeted_by_dir, compiled, catalog)
    receipt = render_and_write(
        retargeted_by_dir, compiled, catalog, source_hashes, bboxes, args.frame_ms,
        counterpart_receipts,
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--beam", type=int, default=DEFAULT_BEAM)
    parser.add_argument("--frame-ms", type=int, default=90)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
