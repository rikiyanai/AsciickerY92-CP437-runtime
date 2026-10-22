#!/usr/bin/env python3
"""FL-4561 rejected cell-owned Unicode walk experiment.

This implementation improved necessary invariants, yet it did not complete the
intended stroke-first ownership change.  Final glyph selection is still keyed
by ``(frame_index, cell_index)``.  Named-bone arc samples only annotate temporal
and counterpart edges between those cell variables.  Consequently this command
cannot falsify a resolution ceiling and cannot run as a new experiment without
an explicit rejected-evidence reproduction flag.

The next valid compiler must own an ordered Unicode sequence per anatomical
chain plus semantic joint states.  Grid cells may receive the compiled result;
they have no authority over candidate discovery, final selection, collision
resolution, temporal identity, and counterpart identity.
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

REPO = Path(__file__).resolve().parents[1]
TRACK_DIR = REPO / "docs/research/ascii/verification/fl4547/skeleton-versions"
REGISTRY = TRACK_DIR / "registry.json"
DESKTOP = Path.home() / "Desktop"

sys.path.insert(0, str(REPO / "scripts"))
import fl4547_skeleton_walk as rig

ARC_T = (0.0, 0.25, 0.5, 0.75, 1.0)
SUBPIXELS = 8
HYSTERESIS_MARGIN = 0.20
HYSTERESIS_SWITCH_COST = 0.42
TOP_K = 18
RECURRING_K = 8
SOLVER_ROUNDS = 8
LAMBDA_TEMPORAL = 1.8
LAMBDA_MIDLINE_TEMPORAL = 5.4
LAMBDA_MIRROR = 1.15

MIRROR_BONE = {
    "upper_arm_a": "upper_arm_b",
    "forearm_a": "forearm_b",
    "thigh_a": "thigh_b",
    "shin_a": "shin_b",
}
MIDLINE_BONES = {"head_axis", "torso_upper", "torso_lower"}

# FL-4561: this readiness surface prevents necessary visual invariants from
# being mislabeled as a completed ownership cutover.  Every field must become
# true in a replacement chain-sequence compiler before another experiment run.
NEXT_RUN_CONTRACT = {
    "chain_sequence_candidate_owner": False,
    "persistent_chain_slot_owner": False,
    "semantic_joint_compositor_owner": False,
    "explicit_collision_occlusion_owner": False,
    "off_corridor_ink_cost": False,
    "sequence_level_temporal_solver": False,
    "sequence_level_counterpart_solver": False,
    "cell_is_raster_destination_only": False,
}


def next_run_readiness() -> dict:
    missing = sorted(name for name, present in NEXT_RUN_CONTRACT.items() if not present)
    return {
        "schema": "fl4561.chain_sequence_next_run_readiness.v1",
        "ready": not missing,
        "current_final_selection_owner": "(frame_index, cell_index)",
        "current_arc_state_role": "edge annotation over cell-owned glyph variables",
        "missing_owners": missing,
    }


def projection_scales(records_by_dir, grid_w: int, grid_h: int):
    """One fixed anatomy projection for every direction and frame."""
    max_up = max_down = max_side = 0.0
    for records in records_by_dir.values():
        for record in records:
            root = record["joints"]["pelvis"]["source_xy"]
            for joint in record["joints"].values():
                x, y = joint["source_xy"]
                max_up = max(max_up, root[1] - y)
                max_down = max(max_down, y - root[1])
                max_side = max(max_side, abs(x - root[0]))
    root_target = (grid_w * 0.5, grid_h * 0.60)
    scales = {
        "x": (grid_w * 0.5 - 0.28) / max(max_side, 1e-9),
        "y_above": (root_target[1] - 0.30) / max(max_up, 1e-9),
        "y_below": (grid_h - root_target[1] - 0.28) / max(max_down, 1e-9),
    }
    return root_target, scales


def project_records(records_by_dir, grid_w: int, grid_h: int):
    """Project stabilized tracks; freeze head, neck, and spine relative to pelvis."""
    import numpy as np

    root_target, scales = projection_scales(records_by_dir, grid_w, grid_h)
    result = {}
    for direction, records in records_by_dir.items():
        midline_mean = {}
        for joint_id in ("head", "neck", "spine"):
            rel = []
            for record in records:
                root = np.asarray(record["joints"]["pelvis"]["source_xy"], dtype=float)
                point = np.asarray(record["joints"][joint_id]["source_xy"], dtype=float)
                rel.append(point - root)
            midline_mean[joint_id] = np.mean(np.asarray(rel), axis=0)

        projected = []
        for record in records:
            root = np.asarray(record["joints"]["pelvis"]["source_xy"], dtype=float)
            joints = {}
            for joint_id, joint in record["joints"].items():
                relative = (
                    midline_mean[joint_id]
                    if joint_id in midline_mean
                    else np.asarray(joint["source_xy"], dtype=float) - root
                )
                sx = scales["x"]
                sy = scales["y_above"] if relative[1] < 0.0 else scales["y_below"]
                xy = (root_target[0] + float(relative[0]) * sx,
                      root_target[1] + float(relative[1]) * sy)
                if not (0.0 <= xy[0] < grid_w and 0.0 <= xy[1] < grid_h):
                    raise ValueError(f"{direction} f{record['frame_index']} {joint_id} clipped: {xy}")
                joints[joint_id] = xy
            projected.append({"frame_index": record["frame_index"], "joints": joints})
        result[direction] = projected
    return result, root_target, scales


def bone_points(record, bone_spec):
    _bone_id, joint_a, joint_b, _part = bone_spec
    return record["joints"][joint_a], record["joints"][joint_b]


def arc_point(a, b, t: float):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def point_cell_candidates(point, grid_w: int, grid_h: int):
    x, y = point
    fx, fy = math.floor(x), math.floor(y)
    xs, ys = {int(fx)}, {int(fy)}
    if x - fx < HYSTERESIS_MARGIN:
        xs.add(int(fx) - 1)
    if x - fx > 1.0 - HYSTERESIS_MARGIN:
        xs.add(int(fx) + 1)
    if y - fy < HYSTERESIS_MARGIN:
        ys.add(int(fy) - 1)
    if y - fy > 1.0 - HYSTERESIS_MARGIN:
        ys.add(int(fy) + 1)
    return sorted((cx, cy) for cx in xs for cy in ys
                  if 0 <= cx < grid_w and 0 <= cy < grid_h)


def solve_cyclic_occupancy(points, grid_w: int, grid_h: int):
    """Exact cyclic DP; cell changes happen only after overcoming hysteresis."""
    candidates = [point_cell_candidates(point, grid_w, grid_h) for point in points]

    def unary(fi, cell):
        px, py = points[fi]
        return (px - (cell[0] + 0.5)) ** 2 + (py - (cell[1] + 0.5)) ** 2

    best = None
    for start in candidates[0]:
        costs = {start: unary(0, start)}
        paths = {start: [start]}
        for fi in range(1, len(points)):
            next_costs, next_paths = {}, {}
            for cell in candidates[fi]:
                choice = min(
                    (cost + unary(fi, cell)
                     + (HYSTERESIS_SWITCH_COST if previous != cell else 0.0), previous)
                    for previous, cost in costs.items()
                )
                next_costs[cell] = choice[0]
                next_paths[cell] = paths[choice[1]] + [cell]
            costs, paths = next_costs, next_paths
        for last, cost in costs.items():
            total = cost + (HYSTERESIS_SWITCH_COST if last != start else 0.0)
            candidate = (total, paths[last])
            if best is None or candidate < best:
                best = candidate
    return best[1]


def build_arc_tracks(records, grid_w: int, grid_h: int):
    tracks = {}
    points_by_frame = [dict() for _ in records]
    for bone_spec in rig.BONE_SPECS:
        bone_id = bone_spec[0]
        for sample_index, t in enumerate(ARC_T):
            state = f"{bone_id}@{t:.2f}"
            points = []
            for fi, record in enumerate(records):
                a, b = bone_points(record, bone_spec)
                point = arc_point(a, b, t)
                points.append(point)
                points_by_frame[fi][state] = point
            tracks[state] = {
                "bone_id": bone_id,
                "sample_index": sample_index,
                "t": t,
                "cells": solve_cyclic_occupancy(points, grid_w, grid_h),
                "points": points,
            }
    return tracks, points_by_frame


def image_to_mask8(image) -> int:
    value = 0
    pixels = image.load()
    for y in range(8):
        for x in range(8):
            if pixels[x, y] > 0:
                value |= 1 << (8 * y + x)
    return value


def raster_targets(record, grid_w: int, grid_h: int, midline_only: bool = False):
    from PIL import Image, ImageDraw

    canvas = Image.new("L", (grid_w * SUBPIXELS, grid_h * SUBPIXELS), 0)
    draw = ImageDraw.Draw(canvas)

    def pixel(point):
        return (int(round(point[0] * SUBPIXELS)), int(round(point[1] * SUBPIXELS)))

    if not midline_only:
        for _attachment_id, joint_a, joint_b, _part in rig.ATTACHMENT_SPECS:
            draw.line((*pixel(record["joints"][joint_a]), *pixel(record["joints"][joint_b])),
                      fill=255, width=2)
    for bone_spec in rig.BONE_SPECS:
        bone_id, joint_a, joint_b, _part = bone_spec
        if midline_only and bone_id not in MIDLINE_BONES:
            continue
        draw.line((*pixel(record["joints"][joint_a]), *pixel(record["joints"][joint_b])),
                  fill=255, width=2)
    head = pixel(record["joints"]["head"])
    draw.ellipse((head[0] - 2, head[1] - 2, head[0] + 2, head[1] + 2),
                 outline=255, width=1)

    masks = []
    for row in range(grid_h):
        for col in range(grid_w):
            crop = canvas.crop((col * 8, row * 8, (col + 1) * 8, (row + 1) * 8))
            masks.append(image_to_mask8(crop))
    return masks


def edge_signature(mask: int, side: str) -> int:
    return rig._edge_signature(mask, side)


def required_anchor_masks(targets, grid_w: int, grid_h: int):
    """Choose shared boundary pixels, including valid eight-neighbor crossings."""
    required = [0 for _ in targets]
    links = []
    pairs = [(1, 0, "right", "left"), (0, 1, "bottom", "top")]
    for row in range(grid_h):
        for col in range(grid_w):
            cell = row * grid_w + col
            for dx, dy, side_a, side_b in pairs:
                nx, ny = col + dx, row + dy
                if nx >= grid_w or ny >= grid_h:
                    continue
                neighbor = ny * grid_w + nx
                sig_a = edge_signature(targets[cell], side_a)
                sig_b = edge_signature(targets[neighbor], side_b)
                positions_a = [bit for bit in range(8) if sig_a & (1 << bit)]
                positions_b = [bit for bit in range(8) if sig_b & (1 << bit)]
                choices = [(abs(a - b), a, b) for a in positions_a for b in positions_b
                           if abs(a - b) <= 1]
                if not choices:
                    continue
                _distance, position_a, position_b = min(choices)
                if side_a == "right":
                    bit_a, bit_b = 8 * position_a + 7, 8 * position_b
                else:
                    bit_a, bit_b = 56 + position_a, position_b
                required[cell] |= 1 << bit_a
                required[neighbor] |= 1 << bit_b
                links.append((cell, neighbor, side_a, position_a, position_b))

            # A one-pixel diagonal crossing is a real eight-connected join.  It
            # must be priced as a hard corner port instead of disappearing from
            # the cardinal-only graph at higher grid resolutions.
            if col + 1 < grid_w and row + 1 < grid_h:
                neighbor = (row + 1) * grid_w + col + 1
                if (targets[cell] & (1 << 63)) and (targets[neighbor] & 1):
                    required[cell] |= 1 << 63
                    required[neighbor] |= 1
                    links.append((cell, neighbor, "bottom_right", 7, 0))
            if col > 0 and row + 1 < grid_h:
                neighbor = (row + 1) * grid_w + col - 1
                if (targets[cell] & (1 << 56)) and (targets[neighbor] & (1 << 7)):
                    required[cell] |= 1 << 56
                    required[neighbor] |= 1 << 7
                    links.append((cell, neighbor, "bottom_left", 0, 7))
    return required, links


def component_count(mask: int) -> int:
    points = {(x, y) for y in range(8) for x in range(8) if mask & (1 << (8 * y + x))}
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
                    point = (x + dx, y + dy)
                    if point in points:
                        points.remove(point)
                        queue.append(point)
    return count


def compatible(index: int, required_mask: int, catalog, component_cache) -> bool:
    mask = int(catalog["masks"][index])
    if mask == 0 or (mask & required_mask) != required_mask:
        return False
    if mask not in component_cache:
        component_cache[mask] = component_count(mask)
    return component_cache[mask] == 1


def local_candidates(target: int, required_mask: int, catalog, component_cache,
                     top_k: int = TOP_K):
    if target == 0:
        return [{"index": catalog["space_index"], "base_cost": 0.0}]
    raw = rig.glyph_candidates(target, catalog, max(256, top_k * 16))
    chosen = [candidate for candidate in raw
              if compatible(candidate["index"], required_mask, catalog, component_cache)]
    if len(chosen) < top_k:
        import numpy as np

        masks = catalog["masks"]
        possible = np.where(
            (masks != 0)
            & ((np.bitwise_and(masks, np.uint64(required_mask))) == np.uint64(required_mask))
        )[0]
        scored = []
        if len(possible):
            target_endpoints, target_junctions = rig._mask8_topology(target)
            score = np.bitwise_count(
                np.bitwise_xor(masks[possible], np.uint64(target))
            ).astype(float) / 64.0
            score += 0.055 * np.minimum(
                6, np.abs(catalog["endpoints"][possible] - target_endpoints)
            )
            score += 0.075 * np.minimum(
                4, np.abs(catalog["junctions"][possible] - target_junctions)
            )
            probe_count = min(len(possible), max(512, top_k * 32))
            probe_local = np.argpartition(score, probe_count - 1)[:probe_count]
            scored = sorted(
                ((float(score[local]), int(catalog["scalars"][possible[local]]),
                  int(possible[local])) for local in probe_local),
                key=lambda item: item[:2],
            )
        seen = {candidate["index"] for candidate in chosen}
        for cost, _scalar, index in scored:
            if index in seen or not compatible(index, required_mask, catalog, component_cache):
                continue
            chosen.append({"index": index, "base_cost": cost})
            seen.add(index)
            if len(chosen) >= top_k:
                break
    if not chosen:
        raise ValueError(f"no connected Unicode glyph satisfies hard port mask 0x{required_mask:016x}")
    return chosen[:top_k]


def populate_owners(targets, tracks, points, grid_w: int):
    owners = defaultdict(set)
    for state, track in tracks.items():
        cell = track["cells"][0]
        owners[cell[1] * grid_w + cell[0]].add(state)
    occupied = [cell for cell, target in enumerate(targets) if target]
    for cell in occupied:
        if owners[cell]:
            continue
        col, row = cell % grid_w, cell // grid_w
        nearest = min(points, key=lambda state: (
            (points[state][0] - (col + 0.5)) ** 2 + (points[state][1] - (row + 0.5)) ** 2,
            state,
        ))
        owners[cell].add(nearest)
    return owners


def add_recurring_candidates(candidates, owners_by_frame, tracks, targets, required,
                             catalog, component_cache):
    for state, track in tracks.items():
        nodes = []
        for fi, cell_xy in enumerate(track["cells"]):
            cell = cell_xy[1] * candidates[fi]["grid_w"] + cell_xy[0]
            if targets[fi][cell]:
                nodes.append((fi, cell))
        if not nodes:
            continue
        pool = {candidate["index"] for fi, cell in nodes for candidate in candidates[fi]["cells"][cell]}
        scored = []
        for index in pool:
            if not all(compatible(index, required[fi][cell], catalog, component_cache)
                       for fi, cell in nodes):
                continue
            cost = sum(rig._glyph_index_cost(targets[fi][cell], index, catalog)
                       for fi, cell in nodes) / len(nodes)
            scored.append((cost, int(catalog["scalars"][index]), index))
        recurring = [index for _cost, _scalar, index in sorted(scored)[:RECURRING_K]]
        for fi, cell in nodes:
            present = {candidate["index"] for candidate in candidates[fi]["cells"][cell]}
            for index in recurring:
                if index not in present:
                    candidates[fi]["cells"][cell].append({
                        "index": index,
                        "base_cost": rig._glyph_index_cost(targets[fi][cell], index, catalog),
                    })


def freeze_midline_cells(candidates, targets, required, midline_targets, catalog,
                         component_cache, grid_w: int):
    """A fixed compiler glyph owns every stable head/spine cell for the cycle."""
    stable_cells = [cell for cell in range(len(midline_targets[0]))
                    if all(frame[cell] for frame in midline_targets)]
    frozen = {}
    for cell in stable_cells:
        required_union = 0
        combined_target = 0
        for fi in range(len(targets)):
            required_union |= required[fi][cell]
            combined_target |= targets[fi][cell]
        common = local_candidates(combined_target, required_union, catalog, component_cache, 64)
        best = min(
            (sum(rig._glyph_index_cost(targets[fi][cell], candidate["index"], catalog)
                 for fi in range(len(targets))),
             int(catalog["scalars"][candidate["index"]]), candidate["index"])
            for candidate in common
        )[2]
        frozen[cell] = best
        for fi in range(len(targets)):
            candidates[fi]["cells"][cell] = [{
                "index": best,
                "base_cost": rig._glyph_index_cost(targets[fi][cell], best, catalog),
            }]
    return frozen


def affine_counterpart_matrix(record, bone_a: str, bone_b: str):
    import numpy as np

    specs = {spec[0]: spec for spec in rig.BONE_SPECS}
    a0, a1 = bone_points(record, specs[bone_a])
    b0, b1 = bone_points(record, specs[bone_b])
    va = np.asarray(a1, dtype=float) - np.asarray(a0, dtype=float)
    vb = np.asarray(b1, dtype=float) - np.asarray(b0, dtype=float)
    la, lb = max(float(np.linalg.norm(va)), 1e-9), max(float(np.linalg.norm(vb)), 1e-9)
    ua, ub = va / la, vb / lb
    na = np.asarray([-ua[1], ua[0]])
    nb = np.asarray([-ub[1], ub[0]])
    scale = min(1.45, max(0.55, lb / la))
    matrix = np.column_stack((scale * ub, -nb)) @ np.column_stack((ua, na)).T
    return matrix


def transform_mask(mask: int, matrix, cache) -> int:
    import numpy as np

    key = (mask, tuple(round(float(value), 2) for value in matrix.flat))
    if key in cache:
        return cache[key]
    result = 0
    center = np.asarray([3.5, 3.5])
    for y in range(8):
        for x in range(8):
            if not mask & (1 << (8 * y + x)):
                continue
            mapped = center + matrix @ (np.asarray([x, y], dtype=float) - center)
            mx, my = int(round(float(mapped[0]))), int(round(float(mapped[1])))
            if 0 <= mx < 8 and 0 <= my < 8:
                result |= 1 << (8 * my + mx)
    cache[key] = result
    return result


def solve_direction(records, grid_w: int, grid_h: int, catalog, component_cache):
    targets = [raster_targets(record, grid_w, grid_h) for record in records]
    midline_targets = [raster_targets(record, grid_w, grid_h, True) for record in records]
    required_and_links = [required_anchor_masks(frame, grid_w, grid_h) for frame in targets]
    required = [item[0] for item in required_and_links]
    hard_links = [item[1] for item in required_and_links]
    tracks, points_by_frame = build_arc_tracks(records, grid_w, grid_h)
    owners = []
    for fi in range(len(records)):
        frame_tracks = {
            state: {**track, "cells": [track["cells"][fi]]}
            for state, track in tracks.items()
        }
        owners.append(populate_owners(targets[fi], frame_tracks, points_by_frame[fi], grid_w))

    cache = {}
    candidates = []
    for fi, frame_targets in enumerate(targets):
        frame = {"grid_w": grid_w, "cells": []}
        for cell, target in enumerate(frame_targets):
            key = (target, required[fi][cell])
            if key not in cache:
                cache[key] = local_candidates(target, required[fi][cell], catalog, component_cache)
            frame["cells"].append([dict(candidate) for candidate in cache[key]])
        candidates.append(frame)

    add_recurring_candidates(candidates, owners, tracks, targets, required,
                             catalog, component_cache)
    frozen = freeze_midline_cells(candidates, targets, required, midline_targets,
                                  catalog, component_cache, grid_w)

    nodes = [(fi, cell) for fi in range(len(records))
             for cell, target in enumerate(targets[fi]) if target]
    temporal_edges = defaultdict(list)
    for state, track in tracks.items():
        weight = LAMBDA_MIDLINE_TEMPORAL if track["bone_id"] in MIDLINE_BONES else LAMBDA_TEMPORAL
        for fi in range(len(records)):
            nxt = (fi + 1) % len(records)
            axy, bxy = track["cells"][fi], track["cells"][nxt]
            a = (fi, axy[1] * grid_w + axy[0])
            b = (nxt, bxy[1] * grid_w + bxy[0])
            if targets[a[0]][a[1]] and targets[b[0]][b[1]]:
                temporal_edges[a].append((b, weight))
                temporal_edges[b].append((a, weight))

    mirror_edges = defaultdict(list)
    transform_receipts = []
    for fi, record in enumerate(records):
        for bone_a, bone_b in MIRROR_BONE.items():
            matrix = affine_counterpart_matrix(record, bone_a, bone_b)
            inverse = __import__("numpy").linalg.inv(matrix)
            transform_receipts.append({
                "frame": fi, "bone_a": bone_a, "bone_b": bone_b,
                "matrix": [[round(float(value), 6) for value in row] for row in matrix],
                "determinant": round(float(__import__("numpy").linalg.det(matrix)), 6),
            })
            for sample_index, _t in enumerate(ARC_T):
                sa = f"{bone_a}@{ARC_T[sample_index]:.2f}"
                sb = f"{bone_b}@{ARC_T[sample_index]:.2f}"
                ca, cb = tracks[sa]["cells"][fi], tracks[sb]["cells"][fi]
                a = (fi, ca[1] * grid_w + ca[0])
                b = (fi, cb[1] * grid_w + cb[0])
                if a == b or not targets[fi][a[1]] or not targets[fi][b[1]]:
                    continue
                mirror_edges[a].append((b, matrix))
                mirror_edges[b].append((a, inverse))

    selected = {node: min(candidates[node[0]]["cells"][node[1]],
                              key=lambda candidate: (candidate["base_cost"],
                                                     int(catalog["scalars"][candidate["index"]])))
                ["index"] for node in nodes}
    transformed_cache = {}
    for _round in range(SOLVER_ROUNDS):
        changed = 0
        for node in nodes:
            choices = []
            for candidate in candidates[node[0]]["cells"][node[1]]:
                index = candidate["index"]
                mask = int(catalog["masks"][index])
                cost = candidate["base_cost"]
                for neighbor, weight in temporal_edges[node]:
                    nindex = selected[neighbor]
                    nmask = int(catalog["masks"][nindex])
                    cost += weight * ((mask ^ nmask).bit_count() / 64.0
                                      + (0.025 if index != nindex else 0.0))
                for neighbor, matrix in mirror_edges[node]:
                    expected = transform_mask(mask, matrix, transformed_cache)
                    nmask = int(catalog["masks"][selected[neighbor]])
                    cost += LAMBDA_MIRROR * ((expected ^ nmask).bit_count() / 64.0)
                choices.append((cost, int(catalog["scalars"][index]), index))
            choice = min(choices)[2]
            changed += choice != selected[node]
            selected[node] = choice
        if not changed:
            break

    frames = []
    for fi in range(len(records)):
        frame = [catalog["space_index"] for _ in range(grid_w * grid_h)]
        for cell, target in enumerate(targets[fi]):
            if target:
                frame[cell] = selected[(fi, cell)]
        frames.append(frame)
    return {
        "targets": targets,
        "required": required,
        "hard_links": hard_links,
        "tracks": tracks,
        "owners": owners,
        "selected": frames,
        "frozen_midline_cells": frozen,
        "transform_receipts": transform_receipts,
        "mirror_edge_count": sum(len(value) for value in mirror_edges.values()) // 2,
    }


def assembled_mask(frame, catalog, grid_w: int, grid_h: int):
    points = set()
    for cell, index in enumerate(frame):
        mask = int(catalog["masks"][index])
        col, row = cell % grid_w, cell // grid_w
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
                    point = (x + dx, y + dy)
                    if point in points:
                        points.remove(point)
                        queue.append(point)
    return count


def necessary_invariant_metrics(compiled, catalog, grid_w: int, grid_h: int):
    violations = 0
    components = []
    midline_changes = 0
    unique = set()
    for direction, result in compiled.items():
        for fi, frame in enumerate(result["selected"]):
            for cell, index in enumerate(frame):
                unique.add(index)
                mask = int(catalog["masks"][index])
                if (mask & result["required"][fi][cell]) != result["required"][fi][cell]:
                    violations += 1
            components.append(assembled_mask(frame, catalog, grid_w, grid_h))
        for cell in result["frozen_midline_cells"]:
            indices = [frame[cell] for frame in result["selected"]]
            midline_changes += sum(indices[fi] != indices[(fi + 1) % len(indices)]
                                   for fi in range(len(indices)))
    return {
        "persistent_arc_state_count": len(rig.BONE_SPECS) * len(ARC_T),
        "hard_port_violation_count": violations,
        "output_component_min": min(components),
        "output_component_max": max(components),
        "midline_glyph_change_count": midline_changes,
        "priced_mirror_edge_count": sum(result["mirror_edge_count"] for result in compiled.values()),
        "unique_glyph_count": len(unique),
        "occupancy_hysteresis": True,
        "pinned_font_render": True,
    }


def render_and_write(compiled, catalog, grid_w: int, grid_h: int, frame_ms: int,
                     metadata):
    from PIL import Image, ImageDraw, ImageFont

    font_chain, font_receipt = rig._canonical_glyph_renderer()
    tile_cache = {}
    missing = []

    def tile(index, cell_px):
        scalar = int(catalog["scalars"][index])
        if scalar not in tile_cache:
            rendered = font_chain.render(scalar)
            if rendered is None:
                missing.append(scalar)
                raise ValueError(f"selected U+{scalar:04X} has no pinned font owner")
            rgba = Image.frombytes("RGBA", (font_chain.cell_px, font_chain.cell_px), rendered.rgba8)
            base = Image.new("RGB", rgba.size, (8, 8, 8))
            ink = Image.new("RGB", rgba.size, (120, 230, 120))
            base.paste(ink, mask=rgba.getchannel("A"))
            tile_cache[scalar] = (base, rendered.font_index, rendered.font_name)
        return tile_cache[scalar][0].resize((cell_px, cell_px), Image.Resampling.NEAREST)

    cell_px, label_h, pad = 24, 16, 6
    panel_w, panel_h = grid_w * cell_px, grid_h * cell_px
    canvas_w = pad + rig.GRID_COLS * (panel_w + pad)
    canvas_h = pad + rig.GRID_ROWS * (panel_h + label_h + pad)
    label_font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 12)
    gif_frames = []
    manifest_frames = {direction: [] for direction in rig.DISPLAY_DIRS}
    for fi in range(18):
        canvas = Image.new("RGB", (canvas_w, canvas_h), (8, 8, 8))
        draw = ImageDraw.Draw(canvas)
        for direction_index, direction in enumerate(rig.DISPLAY_DIRS):
            gc, gr = direction_index % rig.GRID_COLS, direction_index // rig.GRID_COLS
            x0 = pad + gc * (panel_w + pad)
            y0 = pad + gr * (panel_h + label_h + pad)
            draw.text((x0, y0), f"{direction} f{fi}", fill=(150, 210, 150), font=label_font)
            rows = []
            cells = []
            for row in range(grid_h):
                chars = []
                for col in range(grid_w):
                    cell = row * grid_w + col
                    index = compiled[direction]["selected"][fi][cell]
                    scalar = int(catalog["scalars"][index])
                    chars.append(chr(scalar))
                    if index != catalog["space_index"]:
                        canvas.paste(tile(index, cell_px),
                                     (x0 + col * cell_px, y0 + label_h + row * cell_px))
                    cells.append({
                        "cell": [col, row],
                        "owners": sorted(compiled[direction]["owners"][fi].get(cell, [])),
                        "target_mask8_u64": f"0x{compiled[direction]['targets'][fi][cell]:016x}",
                        "required_port_mask8_u64": f"0x{compiled[direction]['required'][fi][cell]:016x}",
                        "glyph_id": int(catalog["glyph_ids"][index]),
                        "unicode_scalar": scalar,
                        "character": chr(scalar),
                        "glyph_mask8_u64": f"0x{int(catalog['masks'][index]):016x}",
                    })
                rows.append("".join(chars))
            manifest_frames[direction].append({"frame_index": fi, "rows": rows, "cells": cells})
        gif_frames.append(canvas)

    timestamp = datetime.now().strftime("v%Y%m%dT%H%M%S")
    name = f"{timestamp}__rejected-cell-owner-hardports-{grid_w}x{grid_h}__walk_all_directions"
    TRACK_DIR.mkdir(parents=True, exist_ok=True)
    tracked_gif = TRACK_DIR / f"{name}.gif"
    desktop_gif = DESKTOP / f"{name}.gif"
    tracked_manifest = TRACK_DIR / f"{name}.frames.json"
    gif_frames[0].save(tracked_gif, save_all=True, append_images=gif_frames[1:],
                       duration=frame_ms, loop=0, disposal=2)
    desktop_gif.write_bytes(tracked_gif.read_bytes())

    manifest = {
        "schema": "fl4561.arc_joint_hardports.v1",
        "status": "rejected_partial_cell_owner_reproduction",
        "primary_owner": "FL-4561",
        "lineage": ["FL-4512", "FL-4547"],
        "name": name,
        "timestamp": timestamp,
        "grid": [grid_w, grid_h],
        "frames_per_direction": 18,
        "palette_mode": "canonical_full_unicode_non_emoji",
        "final_selection_owner": "(frame_index, cell_index)",
        "arc_state_role": "temporal_and_counterpart_edge_annotation",
        "chain_sequence_candidate_owner": False,
        "arc_samples": list(ARC_T),
        "hard_connectivity": "exact shared boundary pixels; disconnected glyph masks excluded",
        "counterpart_relation": "priced local affine reflection with tangent foreshortening",
        "necessary_invariant_metrics": metadata,
        "next_run_readiness": next_run_readiness(),
        "font_receipt": font_receipt,
        "descriptor_sha256": catalog["descriptor_sha256"],
        "descriptor_manifest_sha256": catalog["manifest_sha256"],
        "frames": manifest_frames,
        "transform_receipts": {
            direction: compiled[direction]["transform_receipts"] for direction in rig.DISPLAY_DIRS
        },
    }
    tracked_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    entry = {
        "name": name,
        "kind": "arc-joint-hardports",
        "schema": manifest["schema"],
        "status": manifest["status"],
        "timestamp": timestamp,
        "grid": [grid_w, grid_h],
        "frames_per_dir": 18,
        "palette_mode": manifest["palette_mode"],
        "tracked_gif": str(tracked_gif.relative_to(REPO)),
        "tracked_manifest": str(tracked_manifest.relative_to(REPO)),
        "desktop_gif": str(desktop_gif),
        "gif_sha256": hashlib.sha256(tracked_gif.read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(tracked_manifest.read_bytes()).hexdigest(),
    }
    registry = json.loads(REGISTRY.read_text())
    registry.append(entry)
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=1) + "\n")
    return tracked_gif, desktop_gif, tracked_manifest, missing


def run(args):
    readiness = next_run_readiness()
    if not args.reproduce_rejected_cell_owner:
        raise SystemExit(
            "FL-4561 next run blocked: final glyph selection is still cell-owned; "
            f"missing chain-sequence owners: {', '.join(readiness['missing_owners'])}. "
            "Use --reproduce-rejected-cell-owner only to reproduce historical rejected evidence."
        )
    proposals, source_hashes = rig.load_rig_proposals()
    projected, root_target, scales = project_records(proposals, args.grid_w, args.grid_h)
    catalog = rig.load_descriptor_catalog()
    component_cache = {}
    compiled = {}
    for direction in rig.DISPLAY_DIRS:
        compiled[direction] = solve_direction(
            projected[direction], args.grid_w, args.grid_h, catalog, component_cache
        )
        print(f"  {direction}: fixed arc states + hard ports + priced counterpart relation")
    metrics = necessary_invariant_metrics(compiled, catalog, args.grid_w, args.grid_h)
    metrics.update({
        "source_hashes": source_hashes,
        "root_target": [round(value, 6) for value in root_target],
        "fixed_projection_scales": {key: round(value, 12) for key, value in scales.items()},
    })
    required = {
        "hard_port_violation_count": 0,
        "output_component_max": 1,
        "midline_glyph_change_count": 0,
    }
    failed = {key: (metrics[key], value) for key, value in required.items()
              if metrics[key] != value}
    if metrics["priced_mirror_edge_count"] <= 0:
        failed["priced_mirror_edge_count"] = (metrics["priced_mirror_edge_count"], ">0")
    if failed:
        raise SystemExit(f"necessary invariant failure; rejected reproduction not written: {failed}")
    tracked, desktop, manifest, missing = render_and_write(
        compiled, catalog, args.grid_w, args.grid_h, args.frame_ms, metrics
    )
    if missing:
        raise SystemExit(f"pinned font missing selected scalars: {missing}")
    print(json.dumps(metrics, indent=2))
    print(f"desktop: {desktop}\ntracked: {tracked.relative_to(REPO)}\nmanifest: {manifest.relative_to(REPO)}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reproduce-rejected-cell-owner",
        action="store_true",
        help="explicitly reproduce the rejected cell-owned experiment",
    )
    parser.add_argument("--grid-w", type=int, default=5)
    parser.add_argument("--grid-h", type=int, default=7)
    parser.add_argument("--frame-ms", type=int, default=90)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
