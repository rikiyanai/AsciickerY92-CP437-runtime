#!/usr/bin/env python3
"""FL-4561 Active-Strokes parity slice over the accepted per-part walk.

This is increment 2 of the Bénard-informed experiment.  The rejected increment
1 assigned every direction to one fixed 5x9 strip layout.  This replacement
hard-deletes that authority.  It consumes the accepted T140724 semantic
skeleton, compiles all eight source directions directly, and gives persistent
``(semantic_chain_id, normalized_arc_position)`` tracks sole temporal identity.

The terminal grid is a projection surface.  It never discovers correspondence.
Each track is transported through the closed walk cycle, relaxed toward the
current skeleton, assigned a hysteretic destination cell, and compiled against
the complete pinned non-emoji Unicode census.  Exact shared boundary pixels are
hard requirements.  A deterministic same-cell compositor handles anatomical
joint collisions without replacing the persistent track state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import fl4547_arc_skeletal as arc
import fl4547_hemisphere_mirror_walk as hm
import fl4547_skeleton_walk as rig

TRACK_DIR = REPO / "docs/research/ascii/verification/fl4547/skeleton-versions"
REGISTRY = TRACK_DIR / "registry.json"
DESKTOP = Path.home() / "Desktop"

GRID_W = 5
GRID_H = 9
FRAMES = 18
GENERATED_DIRECTIONS = tuple(rig.DISPLAY_DIRS)

# Glyph slots per semantic chain.  Samples include proximal and distal ends so
# joints can share one cell while the deterministic compositor owns the union.
TRACK_SPECS = {
    "head": {"part": "head", "count": 1, "kind": "head", "codebook": "head"},
    "torso": {"part": "torso", "count": 4, "kind": "stroke", "codebook": "torso"},
    "arm_a": {"part": "arm_a", "count": 3, "kind": "stroke", "codebook": "arm"},
    "arm_b": {"part": "arm_b", "count": 3, "kind": "stroke", "codebook": "arm"},
    "hip": {"part": None, "count": 1, "kind": "stroke", "codebook": "hip"},
    "leg_a": {"part": "leg_a", "count": 5, "kind": "stroke", "codebook": "leg_upper"},
    "leg_b": {"part": "leg_b", "count": 5, "kind": "stroke", "codebook": "leg_upper"},
}

MIDLINE_ROLES = {"head", "torso", "hip"}
MAX_POOL = 42
LOCAL_K = 16
CODEBOOK_BONUS = 0.28
LAMBDA_SWITCH = 0.08
LAMBDA_EXPLAINED_MOTION = 0.018
LAMBDA_MASK_CHANGE = 0.20
MAX_UNEXPLAINED_ANGLE_STEP = 52.0
TOPOLOGY_TRANSITION_COST = 7.5

OWNERSHIP_CONTRACT = {
    "chain_sequence_candidate_owner": True,
    "persistent_chain_slot_owner": True,
    "semantic_joint_compositor_owner": True,
    "explicit_collision_occlusion_owner": True,
    "off_corridor_ink_cost": True,
    "sequence_level_temporal_solver": True,
    "cell_is_raster_destination_only": True,
    "all_eight_source_directions_compiled_directly": True,
}


def _sample_polyline(path: list[tuple[float, float]], count: int) -> list[tuple[float, float]]:
    if not path:
        return []
    if count == 1:
        return [rig.point_on_path(path, 0.5)]
    return [rig.point_on_path(path, index / (count - 1)) for index in range(count)]


def _centroid(mask) -> tuple[float, float] | None:
    ys, xs = np.where(mask)
    if not len(xs):
        return None
    return float(xs.mean()), float(ys.mean())


def _ordered_part_path(parts: dict, role: str) -> list[tuple[float, float]]:
    mask = parts.get(TRACK_SPECS[role]["part"])
    if mask is None:
        return []
    path = rig.longest_skeleton_path(mask)
    if role == "torso" and path and path[0][1] > path[-1][1]:
        path.reverse()
    if role.startswith(("arm_", "leg_")):
        path = rig._path_proximal_first(path, parts["torso"])
    return path


def raw_direction_tracks(frames: list[dict]) -> tuple[dict, dict]:
    """Extract persistent arc samples from the accepted colour-part skeleton."""
    points = {
        f"{role}@{slot}": [None] * len(frames)
        for role, spec in TRACK_SPECS.items()
        for slot in range(spec["count"])
    }
    visible = {track_id: [False] * len(frames) for track_id in points}

    for fi, parts in enumerate(frames):
        paths = {}
        for role in ("torso", "arm_a", "arm_b", "leg_a", "leg_b"):
            paths[role] = _ordered_part_path(parts, role)
            samples = _sample_polyline(paths[role], TRACK_SPECS[role]["count"])
            for slot, point in enumerate(samples):
                track_id = f"{role}@{slot}"
                points[track_id][fi] = point
                visible[track_id][fi] = True

        head_mask = parts.get("head")
        head = _centroid(head_mask) if head_mask is not None else None
        if head is not None:
            points["head@0"][fi] = head
            visible["head@0"][fi] = True

        leg_a = paths["leg_a"]
        leg_b = paths["leg_b"]
        if leg_a and leg_b:
            a, b = leg_a[0], leg_b[0]
            points["hip@0"][fi] = ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)
            visible["hip@0"][fi] = True
        elif paths["torso"]:
            points["hip@0"][fi] = paths["torso"][-1]
    return points, visible


def _cyclic_fill(values: list[tuple[float, float] | None]) -> np.ndarray:
    n = len(values)
    known = [index for index, value in enumerate(values) if value is not None]
    if not known:
        raise ValueError("semantic track is absent for the complete cycle")
    result = np.zeros((n, 2), dtype=float)
    for fi, value in enumerate(values):
        if value is not None:
            result[fi] = value
            continue
        prev = next((fi - step) % n for step in range(1, n + 1)
                    if values[(fi - step) % n] is not None)
        nxt = next((fi + step) % n for step in range(1, n + 1)
                   if values[(fi + step) % n] is not None)
        dp, dn = (fi - prev) % n, (nxt - fi) % n
        t = dp / max(1, dp + dn)
        p0, p1 = np.asarray(values[prev]), np.asarray(values[nxt])
        result[fi] = p0 + (p1 - p0) * t
    return result


def _cyclic_matrix(n: int, velocity: float, acceleration: float) -> np.ndarray:
    identity = np.eye(n)
    d1 = np.zeros((n, n))
    d2 = np.zeros((n, n))
    for index in range(n):
        d1[index, index] = 1.0
        d1[index, (index - 1) % n] = -1.0
        d2[index, (index - 1) % n] = 1.0
        d2[index, index] = -2.0
        d2[index, (index + 1) % n] = 1.0
    return identity + velocity * (d1.T @ d1) + acceleration * (d2.T @ d2)


def transport_and_relax(raw: np.ndarray, role: str) -> np.ndarray:
    """Offline closed-cycle equivalent of Active Strokes advect then relax."""
    if role in MIDLINE_ROLES:
        velocity, acceleration = 2.6, 9.0
    else:
        velocity, acceleration = 0.34, 1.25
    matrix = _cyclic_matrix(len(raw), velocity, acceleration)
    smooth = np.column_stack([
        np.linalg.solve(matrix, raw[:, axis]) for axis in range(2)
    ])
    return smooth


def _presence_hysteresis(values: list[bool]) -> list[bool]:
    """Heal one-frame visibility holes and reject one-frame births cyclically."""
    result = list(values)
    for _ in range(2):
        updated = list(result)
        for index in range(len(result)):
            prev = result[(index - 1) % len(result)]
            nxt = result[(index + 1) % len(result)]
            if prev == nxt and result[index] != prev:
                updated[index] = prev
        result = updated
    return result


def normalize_tracks(raw_by_direction: dict, shapes: dict) -> tuple[dict, dict]:
    """Preserve direction-specific width while fitting every source into 5x9."""
    bounds = {}
    for direction, tracks in raw_by_direction.items():
        cloud = np.concatenate([_cyclic_fill(values) for values in tracks.values()], axis=0)
        bounds[direction] = (
            float(cloud[:, 0].min()), float(cloud[:, 0].max()),
            float(cloud[:, 1].min()), float(cloud[:, 1].max()),
        )
    max_width = max(value[1] - value[0] for value in bounds.values())
    max_height = max(value[3] - value[2] for value in bounds.values())
    sx = (GRID_W - 0.70) / max(max_width, 1e-9)
    sy = (GRID_H - 0.70) / max(max_height, 1e-9)

    normalized = {}
    receipts = {}
    for direction, tracks in raw_by_direction.items():
        x0, x1, y0, _y1 = bounds[direction]
        source_center = (x0 + x1) * 0.5
        output = {}
        for track_id, values in tracks.items():
            role = track_id.split("@", 1)[0]
            raw = _cyclic_fill(values)
            mapped = np.column_stack((
                GRID_W * 0.5 + (raw[:, 0] - source_center) * sx,
                0.35 + (raw[:, 1] - y0) * sy,
            ))
            mapped = transport_and_relax(mapped, role)
            mapped[:, 0] = np.clip(mapped[:, 0], 0.04, GRID_W - 0.04)
            mapped[:, 1] = np.clip(mapped[:, 1], 0.04, GRID_H - 0.04)
            output[track_id] = mapped
        normalized[direction] = output
        receipts[direction] = {
            "source_shape": list(shapes[direction]),
            "source_bounds": [round(value, 6) for value in bounds[direction]],
            "common_scale": [round(sx, 8), round(sy, 8)],
            "source_center_x": round(source_center, 6),
        }
    return normalized, receipts


def track_role(track_id: str) -> str:
    return track_id.split("@", 1)[0]


def track_slot(track_id: str) -> int:
    return int(track_id.split("@", 1)[1])


def track_links() -> list[tuple[str, str]]:
    links = []
    for role, spec in TRACK_SPECS.items():
        for slot in range(spec["count"] - 1):
            links.append((f"{role}@{slot}", f"{role}@{slot + 1}"))
    links.extend([
        ("head@0", "torso@0"),
        ("torso@0", "arm_a@0"),
        ("torso@0", "arm_b@0"),
        ("torso@3", "hip@0"),
        ("hip@0", "leg_a@0"),
        ("hip@0", "leg_b@0"),
    ])
    return links


def _track_angles(tracks: dict) -> dict:
    result = {}
    for track_id, positions in tracks.items():
        role, slot = track_role(track_id), track_slot(track_id)
        count = TRACK_SPECS[role]["count"]
        angles = []
        for fi in range(FRAMES):
            if role == "head":
                angles.append(0.0)
                continue
            if count == 1:
                if role == "hip":
                    a, b = tracks["leg_a@0"][fi], tracks["leg_b@0"][fi]
                else:
                    angles.append(90.0)
                    continue
            elif slot == 0:
                a, b = positions[fi], tracks[f"{role}@1"][fi]
            elif slot == count - 1:
                a, b = tracks[f"{role}@{slot - 1}"][fi], positions[fi]
            else:
                a = tracks[f"{role}@{slot - 1}"][fi]
                b = tracks[f"{role}@{slot + 1}"][fi]
            angles.append(math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0)
        result[track_id] = angles
    return result


def _boundary_bits(cell_a, cell_b, point_a, point_b) -> tuple[int, int]:
    """Exact matching 8x8 boundary pixels for one adjacent-cell connection."""
    ax, ay = cell_a
    bx, by = cell_b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return 0, 0
    if abs(dx) > 1 or abs(dy) > 1:
        raise ValueError(f"non-adjacent arc samples: {cell_a} -> {cell_b}")
    if dx and dy:
        if dx > 0 and dy > 0:
            return 1 << 63, 1
        if dx > 0 and dy < 0:
            return 1 << 7, 1 << 56
        if dx < 0 and dy > 0:
            return 1 << 56, 1 << 7
        return 1, 1 << 63

    pa, pb = np.asarray(point_a, dtype=float), np.asarray(point_b, dtype=float)
    if dx:
        boundary = float(max(ax, bx))
        t = (boundary - pa[0]) / (pb[0] - pa[0]) if pb[0] != pa[0] else 0.5
        y = float(pa[1] + (pb[1] - pa[1]) * t)
        bit = max(0, min(7, int(round((y - math.floor(y)) * 7.0))))
        if dx > 0:
            return 1 << (8 * bit + 7), 1 << (8 * bit)
        return 1 << (8 * bit), 1 << (8 * bit + 7)

    boundary = float(max(ay, by))
    t = (boundary - pa[1]) / (pb[1] - pa[1]) if pb[1] != pa[1] else 0.5
    x = float(pa[0] + (pb[0] - pa[0]) * t)
    bit = max(0, min(7, int(round((x - math.floor(x)) * 7.0))))
    if dy > 0:
        return 1 << (56 + bit), 1 << bit
    return 1 << bit, 1 << (56 + bit)


def _connection_cells(cell_a, cell_b) -> list[tuple[int, int]]:
    """Deterministic eight-connected Bresenham cells between two arc slots."""
    x0, y0 = cell_a
    x1, y1 = cell_b
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    error = dx - dy
    cells = []
    while True:
        cells.append((x0, y0))
        if x0 == x1 and y0 == y1:
            break
        twice = 2 * error
        if twice > -dy:
            error -= dy
            x0 += sx
        if twice < dx:
            error += dx
            y0 += sy
    return cells


def _folded_angle_distance(a: float, b: float) -> float:
    delta = abs(a - b) % 180.0
    return min(delta, 180.0 - delta)


def _signed_angle_delta(a: float, b: float) -> float:
    return ((b - a + 90.0) % 180.0) - 90.0


def _strokeness(mask: int) -> float:
    return hm.strokeness(mask)


def _target_mask(kind: str, angle: float) -> int:
    return hm.head_mask() if kind == "head" else hm.stroke_mask(angle)


def _unary(index: int, target: int, angle: float, kind: str, required: int,
           catalog, component_cache, authored: set[int]) -> float:
    if not arc.compatible(index, required, catalog, component_cache):
        return float("inf")
    mask = int(catalog["masks"][index])
    if kind == "head":
        value = rig._glyph_index_cost(target, index, catalog)
    else:
        glyph_angle = hm.glyph_angle(mask, {})
        value = 0.020 * _folded_angle_distance(glyph_angle, angle)
        value += 0.85 * _strokeness(mask)
    if index in authored:
        value -= CODEBOOK_BONUS
    return value


def _build_track_pool(track_id: str, angles: list[float], required: list[int],
                      catalog, component_cache, codebook_pools) -> tuple[list[int], list[int]]:
    role = track_role(track_id)
    spec = TRACK_SPECS[role]
    codebook_role = spec["codebook"]
    if role.startswith("leg_"):
        fraction = track_slot(track_id) / max(1, spec["count"] - 1)
        codebook_role = "leg_upper" if fraction < 0.5 else "leg_lower"
    authored = set(codebook_pools.get(codebook_role, []))
    local = []
    union = set(authored)
    for fi in range(FRAMES):
        target = _target_mask(spec["kind"], angles[fi])
        candidates = arc.local_candidates(target, required[fi], catalog, component_cache, LOCAL_K)
        indices = [candidate["index"] for candidate in candidates]
        local.append(indices)
        union.update(indices[:4])

    scores = []
    for index in union:
        costs = [
            _unary(index, _target_mask(spec["kind"], angles[fi]), angles[fi],
                   spec["kind"], required[fi], catalog, component_cache, authored)
            for fi in range(FRAMES)
        ]
        finite = [value for value in costs if math.isfinite(value)]
        if finite:
            scores.append((len(costs) - len(finite), sum(finite) / len(finite),
                           int(catalog["scalars"][index]), index))
    pool = [item[3] for item in sorted(scores)[:MAX_POOL]]
    # Guarantee each frame retains its locally best legal candidate.
    for indices in local:
        if indices[0] not in pool:
            pool.append(indices[0])
    return pool, sorted(authored)


def _solve_track(track_id: str, angles: list[float], required: list[int], pool: list[int],
                 authored: list[int], catalog, component_cache) -> list[int]:
    role = track_role(track_id)
    kind = TRACK_SPECS[role]["kind"]
    authored_set = set(authored)
    angle_cache = {
        index: hm.glyph_angle(int(catalog["masks"][index]), {}) for index in pool
    }
    allowed = []
    unary = []
    for fi in range(FRAMES):
        target = _target_mask(kind, angles[fi])
        frame_cost = {
            index: _unary(index, target, angles[fi], kind, required[fi], catalog,
                          component_cache, authored_set)
            for index in pool
        }
        frame_allowed = [index for index, value in frame_cost.items() if math.isfinite(value)]
        if not frame_allowed:
            raise ValueError(f"{track_id} f{fi}: no candidate satisfies hard ports")
        allowed.append(frame_allowed)
        unary.append(frame_cost)

    if kind == "head":
        common = set(allowed[0])
        for values in allowed[1:]:
            common.intersection_update(values)
        if common:
            best = min((sum(unary[fi][index] for fi in range(FRAMES)),
                        int(catalog["scalars"][index]), index) for index in common)[2]
            return [best] * FRAMES

    def transition(fi: int, previous: int, current: int) -> float:
        mask_a = int(catalog["masks"][previous])
        mask_b = int(catalog["masks"][current])
        glyph_delta = _signed_angle_delta(angle_cache[previous], angle_cache[current])
        target_delta = _signed_angle_delta(angles[(fi - 1) % FRAMES], angles[fi])
        unexplained = abs(glyph_delta - target_delta)
        topology_cost = 0.0
        if abs(glyph_delta) > MAX_UNEXPLAINED_ANGLE_STEP and abs(target_delta) < 30.0:
            topology_cost = TOPOLOGY_TRANSITION_COST + 0.10 * (
                abs(glyph_delta) - MAX_UNEXPLAINED_ANGLE_STEP
            )
        return (topology_cost
                + (LAMBDA_SWITCH if previous != current else 0.0)
                + LAMBDA_EXPLAINED_MOTION * unexplained
                + LAMBDA_MASK_CHANGE * ((mask_a ^ mask_b).bit_count() / 64.0))

    best_cycle = None
    for start in allowed[0]:
        costs = {start: unary[0][start]}
        paths = {start: [start]}
        for fi in range(1, FRAMES):
            next_costs, next_paths = {}, {}
            for current in allowed[fi]:
                choices = []
                for previous, base in costs.items():
                    edge = transition(fi, previous, current)
                    if math.isfinite(edge):
                        choices.append((base + edge, int(catalog["scalars"][previous]), previous))
                if not choices:
                    continue
                choice = min(choices)
                next_costs[current] = choice[0] + unary[fi][current]
                next_paths[current] = paths[choice[2]] + [current]
            costs, paths = next_costs, next_paths
        for last, base in costs.items():
            closure = transition(0, last, start)
            if not math.isfinite(closure):
                continue
            candidate = (base + closure, paths[last])
            if best_cycle is None or candidate[0] < best_cycle[0]:
                best_cycle = candidate
    if best_cycle is None:
        raise ValueError(f"{track_id}: cyclic track solve has no legal path")
    return best_cycle[1]


def compile_direction(direction: str, tracks: dict, visibility: dict, catalog,
                      codebook_pools, component_cache) -> dict:
    angles = _track_angles(tracks)
    cells = {
        track_id: arc.solve_cyclic_occupancy(
            [tuple(value) for value in positions], GRID_W, GRID_H
        )
        for track_id, positions in tracks.items()
    }
    active = {track_id: _presence_hysteresis(values) for track_id, values in visibility.items()}
    required = {track_id: [0] * FRAMES for track_id in tracks}
    port_links_by_frame = [[] for _ in range(FRAMES)]
    bridge_cells = [defaultdict(lambda: {"required": 0, "target": 0, "contributors": []})
                    for _ in range(FRAMES)]

    for track_a, track_b in track_links():
        for fi in range(FRAMES):
            if not active[track_a][fi] or not active[track_b][fi]:
                continue
            cell_a, cell_b = cells[track_a][fi], cells[track_b][fi]
            connection = _connection_cells(cell_a, cell_b)
            angle = math.degrees(math.atan2(
                tracks[track_b][fi][1] - tracks[track_a][fi][1],
                tracks[track_b][fi][0] - tracks[track_a][fi][0],
            )) % 180.0
            target = hm.stroke_mask(angle)
            for step, (from_cell, to_cell) in enumerate(zip(connection, connection[1:])):
                bit_a, bit_b = _boundary_bits(
                    from_cell, to_cell, tracks[track_a][fi], tracks[track_b][fi]
                )
                if step == 0:
                    required[track_a][fi] |= bit_a
                else:
                    bridge = bridge_cells[fi][from_cell]
                    bridge["required"] |= bit_a
                    bridge["target"] |= target
                    bridge["contributors"].append(f"{track_a}>{track_b}")
                if step + 1 == len(connection) - 1:
                    required[track_b][fi] |= bit_b
                else:
                    bridge = bridge_cells[fi][to_cell]
                    bridge["required"] |= bit_b
                    bridge["target"] |= target
                    bridge["contributors"].append(f"{track_a}>{track_b}")
                port_links_by_frame[fi].append((from_cell, to_cell, bit_a, bit_b))

    sequences = {}
    pools = {}
    for track_id in sorted(tracks):
        pool, authored = _build_track_pool(
            track_id, angles[track_id], required[track_id], catalog,
            component_cache, codebook_pools,
        )
        pools[track_id] = pool
        sequences[track_id] = _solve_track(
            track_id, angles[track_id], required[track_id], pool, authored,
            catalog, component_cache,
        )

    selected = []
    contributors_manifest = []
    collision_count = 0
    for fi in range(FRAMES):
        contributors = defaultdict(list)
        for track_id, sequence in sequences.items():
            if not active[track_id][fi]:
                continue
            cell_xy = cells[track_id][fi]
            cell = cell_xy[1] * GRID_W + cell_xy[0]
            contributors[cell].append(track_id)

        frame = [catalog["space_index"]] * (GRID_W * GRID_H)
        frame_contributors = {}
        occupied_cells = set(contributors)
        occupied_cells.update(row * GRID_W + col for col, row in bridge_cells[fi])
        for cell in sorted(occupied_cells):
            track_ids = contributors.get(cell, [])
            col, row = cell % GRID_W, cell // GRID_W
            bridge = bridge_cells[fi].get((col, row))
            bridge_names = [] if bridge is None else sorted(set(bridge["contributors"]))
            frame_contributors[str(cell)] = list(track_ids) + [f"bridge:{name}" for name in bridge_names]
            if len(track_ids) == 1 and bridge is None:
                frame[cell] = sequences[track_ids[0]][fi]
                continue
            collision_count += 1
            target = 0 if bridge is None else int(bridge["target"])
            required_union = 0 if bridge is None else int(bridge["required"])
            authored_union = set()
            for track_id in track_ids:
                role = track_role(track_id)
                target |= _target_mask(TRACK_SPECS[role]["kind"], angles[track_id][fi])
                required_union |= required[track_id][fi]
                authored_union.add(sequences[track_id][fi])
            candidates = arc.local_candidates(
                target, required_union, catalog, component_cache, top_k=32
            )
            frame[cell] = min(
                (candidate["base_cost"] - (0.12 if candidate["index"] in authored_union else 0.0),
                 int(catalog["scalars"][candidate["index"]]), candidate["index"])
                for candidate in candidates
            )[2]
        selected.append(frame)
        contributors_manifest.append(frame_contributors)

    hard_violations = 0
    for fi, links in enumerate(port_links_by_frame):
        for cell_a, cell_b, bit_a, bit_b in links:
            index_a = selected[fi][cell_a[1] * GRID_W + cell_a[0]]
            index_b = selected[fi][cell_b[1] * GRID_W + cell_b[0]]
            mask_a = int(catalog["masks"][index_a])
            mask_b = int(catalog["masks"][index_b])
            hard_violations += (mask_a & bit_a) != bit_a
            hard_violations += (mask_b & bit_b) != bit_b

    return {
        "direction": direction,
        "selected": selected,
        "tracks": tracks,
        "cells": cells,
        "angles": angles,
        "required": required,
        "active": active,
        "sequences": sequences,
        "contributors": contributors_manifest,
        "collision_count": collision_count,
        "hard_port_violation_count": int(hard_violations),
    }


def _assembled_component_count(frame, catalog) -> int:
    points = set()
    for cell, index in enumerate(frame):
        mask = int(catalog["masks"][index])
        col, row = cell % GRID_W, cell // GRID_W
        for y in range(8):
            for x in range(8):
                if mask & (1 << (8 * y + x)):
                    points.add((col * 8 + x, row * 8 + y))
    count = 0
    while points:
        count += 1
        queue = deque([points.pop()])
        while queue:
            x, y = queue.popleft()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if not dx and not dy:
                        continue
                    neighbor = (x + dx, y + dy)
                    if neighbor in points:
                        points.remove(neighbor)
                        queue.append(neighbor)
    return count


def metrics(compiled: dict, catalog) -> dict:
    components = []
    unique = set()
    head_changes = 0
    topology_transitions = 0
    direction_signatures = set()
    for direction, result in compiled.items():
        direction_signatures.add(tuple(result["selected"][0]))
        sequence = result["sequences"]["head@0"]
        head_changes += sum(sequence[index] != sequence[(index + 1) % FRAMES]
                            for index in range(FRAMES))
        for track_id, sequence in result["sequences"].items():
            glyph_angles = [
                hm.glyph_angle(int(catalog["masks"][index]), {}) for index in sequence
            ]
            targets = result["angles"][track_id]
            for fi in range(FRAMES):
                glyph_delta = abs(_signed_angle_delta(
                    glyph_angles[(fi - 1) % FRAMES], glyph_angles[fi]
                ))
                target_delta = abs(_signed_angle_delta(
                    targets[(fi - 1) % FRAMES], targets[fi]
                ))
                topology_transitions += (
                    glyph_delta > MAX_UNEXPLAINED_ANGLE_STEP and target_delta < 30.0
                )
        for frame in result["selected"]:
            components.append(_assembled_component_count(frame, catalog))
            unique.update(index for index in frame if index != catalog["space_index"])
    return {
        "ownership_contract": OWNERSHIP_CONTRACT,
        "persistent_track_count_per_direction": sum(
            spec["count"] for spec in TRACK_SPECS.values()
        ),
        "compiled_source_direction_count": len(compiled),
        "distinct_direction_frame0_count": len(direction_signatures),
        "hard_port_violation_count": sum(
            result["hard_port_violation_count"] for result in compiled.values()
        ),
        "collision_compositor_count": sum(
            result["collision_count"] for result in compiled.values()
        ),
        "assembled_component_min": min(components),
        "assembled_component_max": max(components),
        "head_glyph_change_count": head_changes,
        "priced_topology_transition_count": int(topology_transitions),
        "unique_glyph_count": len(unique),
    }


def render_and_write(compiled: dict, catalog, frame_ms: int, metadata: dict):
    from PIL import Image, ImageDraw, ImageFont

    font_chain, font_receipt = rig._canonical_glyph_renderer()
    tile_cache = {}

    def tile(index: int, cell_px: int):
        scalar = int(catalog["scalars"][index])
        if scalar not in tile_cache:
            rendered = font_chain.render(scalar)
            if rendered is None:
                raise ValueError(f"selected U+{scalar:04X} has no pinned font owner")
            rgba = Image.frombytes("RGBA", (font_chain.cell_px, font_chain.cell_px), rendered.rgba8)
            base = Image.new("RGB", rgba.size, (8, 8, 8))
            base.paste(Image.new("RGB", rgba.size, (120, 230, 120)), mask=rgba.getchannel("A"))
            tile_cache[scalar] = base
        return tile_cache[scalar].resize((cell_px, cell_px), Image.Resampling.NEAREST)

    cell_px, label_h, pad = 19, 16, 6
    panel_w, panel_h = GRID_W * cell_px, GRID_H * cell_px
    canvas_w = pad + rig.GRID_COLS * (panel_w + pad)
    canvas_h = pad + rig.GRID_ROWS * (panel_h + label_h + pad)
    label_font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 12)
    gif_frames = []
    manifest_frames = {direction: [] for direction in rig.DISPLAY_DIRS}

    for fi in range(FRAMES):
        canvas = Image.new("RGB", (canvas_w, canvas_h), (8, 8, 8))
        draw = ImageDraw.Draw(canvas)
        for direction_index, direction in enumerate(rig.DISPLAY_DIRS):
            gc, gr = direction_index % rig.GRID_COLS, direction_index // rig.GRID_COLS
            x0 = pad + gc * (panel_w + pad)
            y0 = pad + gr * (panel_h + label_h + pad)
            draw.text((x0, y0), f"{direction} f{fi}", fill=(150, 210, 150), font=label_font)
            rows = []
            for row in range(GRID_H):
                chars = []
                for col in range(GRID_W):
                    index = compiled[direction]["selected"][fi][row * GRID_W + col]
                    chars.append(chr(int(catalog["scalars"][index])))
                    if index != catalog["space_index"]:
                        canvas.paste(tile(index, cell_px),
                                     (x0 + col * cell_px, y0 + label_h + row * cell_px))
                rows.append("".join(chars))
            manifest_frames[direction].append({
                "frame_index": fi,
                "rows": rows,
                "contributors": compiled[direction]["contributors"][fi],
            })
        gif_frames.append(canvas)

    timestamp = datetime.now().strftime("v%Y%m%dT%H%M%S")
    name = f"{timestamp}__active-strokes-parity-{GRID_W}x{GRID_H}__walk_all_directions"
    tracked_gif = TRACK_DIR / f"{name}.gif"
    desktop_gif = DESKTOP / f"{name}.gif"
    tracked_manifest = TRACK_DIR / f"{name}.frames.json"
    gif_frames[0].save(tracked_gif, save_all=True, append_images=gif_frames[1:],
                       duration=frame_ms, loop=0, disposal=2)
    desktop_gif.write_bytes(tracked_gif.read_bytes())

    manifest = {
        "schema": "fl4561.active_strokes_parity.v2",
        "status": "experimental_increment2_operator_review_open",
        "primary_owner": "FL-4561",
        "lineage": ["FL-4512", "FL-4547", "benard-2012-active-strokes"],
        "name": name,
        "timestamp": timestamp,
        "grid": [GRID_W, GRID_H],
        "frames_per_direction": FRAMES,
        "candidate_supply": "canonical pinned non-emoji Unicode census",
        "authored_codebook_role": "candidate preference only",
        "source_checkpoint": str(rig.ACCEPTED_PERPART_CHECKPOINT.relative_to(REPO)),
        "source_checkpoint_sha256": rig.ACCEPTED_PERPART_CHECKPOINT_SHA256,
        "directions": list(GENERATED_DIRECTIONS),
        "direction_generation": "all eight compiled directly from accepted semantic skeleton",
        "font_receipt": font_receipt,
        "descriptor_sha256": catalog["descriptor_sha256"],
        "descriptor_manifest_sha256": catalog["manifest_sha256"],
        "ownership_contract": OWNERSHIP_CONTRACT,
        "normalization": metadata["normalization"],
        "source_hashes": metadata["source_hashes"],
        "metrics": metadata["metrics"],
        "frames": manifest_frames,
        "tracks": {
            direction: {
                track_id: {
                    "cells": [list(cell) for cell in compiled[direction]["cells"][track_id]],
                    "angles": [round(value, 6) for value in compiled[direction]["angles"][track_id]],
                    "glyph_scalars": [int(catalog["scalars"][index])
                                      for index in compiled[direction]["sequences"][track_id]],
                    "active": compiled[direction]["active"][track_id],
                }
                for track_id in sorted(compiled[direction]["tracks"])
            }
            for direction in rig.DISPLAY_DIRS
        },
    }
    tracked_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")

    entry = {
        "name": name,
        "kind": "active-strokes-parity",
        "schema": manifest["schema"],
        "status": manifest["status"],
        "timestamp": timestamp,
        "grid": [GRID_W, GRID_H],
        "frames_per_dir": FRAMES,
        "tracked_gif": str(tracked_gif.relative_to(REPO)),
        "tracked_manifest": str(tracked_manifest.relative_to(REPO)),
        "desktop_gif": str(desktop_gif),
        "gif_sha256": hashlib.sha256(tracked_gif.read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(tracked_manifest.read_bytes()).hexdigest(),
        "metrics": metadata["metrics"],
    }
    registry = json.loads(REGISTRY.read_text())
    registry.append(entry)
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=1) + "\n")
    return tracked_gif, desktop_gif, tracked_manifest


def compile_active_strokes() -> tuple[dict, dict, dict]:
    skeletons, source_hashes, _bboxes = rig.load_accepted_perpart_skeletons(upscale=3)
    raw_by_direction = {}
    visibility_by_direction = {}
    shapes = {}
    for direction in rig.DISPLAY_DIRS:
        raw, visibility = raw_direction_tracks(skeletons[direction])
        raw_by_direction[direction] = raw
        visibility_by_direction[direction] = visibility
        shapes[direction] = next(iter(skeletons[direction][0].values())).shape

    normalized, normalization = normalize_tracks(raw_by_direction, shapes)
    catalog = rig.load_descriptor_catalog()
    codebook_pools, missing = hm.load_codebook(hm.CODEBOOK, catalog)
    if missing:
        print(f"codebook preference skipped unavailable glyphs: {missing}")
    component_cache = {}
    compiled = {}
    for direction in rig.DISPLAY_DIRS:
        compiled[direction] = compile_direction(
            direction, normalized[direction], visibility_by_direction[direction],
            catalog, codebook_pools, component_cache,
        )
        print(f"compiled {direction}: direct semantic arc tracks")
    metadata = {
        "normalization": normalization,
        "source_hashes": source_hashes,
        "metrics": metrics(compiled, catalog),
    }
    return compiled, catalog, metadata


def run(args) -> int:
    compiled, catalog, metadata = compile_active_strokes()
    tracked, desktop, manifest = render_and_write(compiled, catalog, args.frame_ms, metadata)
    print(json.dumps(metadata["metrics"], indent=2))
    print(f"desktop: {desktop}")
    print(f"tracked: {tracked.relative_to(REPO)}")
    print(f"manifest: {manifest.relative_to(REPO)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame-ms", type=int, default=90)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
