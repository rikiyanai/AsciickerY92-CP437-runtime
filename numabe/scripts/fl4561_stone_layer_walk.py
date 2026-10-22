#!/usr/bin/env python3
"""FL-4561 increment 5: held axial layer plus pivoted gait-key limb layers.

This experiment keeps the accepted T140724 semantic skeleton as the direct
eight-direction motion source and T144107 as the visual baseline.  It compiles
one exact held head/spine layer per direction, four pivoted limb layers, and
four symbolic gait keys (contact, passing, opposite contact, opposite passing).
Glyph candidates are discovered against the complete pinned non-emoji Unicode
census.  A finite line-language preference is applied only after discovery.

The 5x5 grid is the presentation surface, never the correspondence owner.
Persistent state is ``(semantic limb, normalized chain slot)``.  Each limb is
attached to an explicit held shoulder/hip pivot.  Non-joint layer collisions
are resolved as occlusions by selecting one layer; masks are never unioned at
those cells.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import fl4547_arc_stroke_walk as active
import fl4547_hemisphere_mirror_walk as glyphlib
import fl4547_skeleton_walk as rig

TRACK_DIR = REPO / "docs/research/ascii/verification/fl4547/skeleton-versions"
REGISTRY = TRACK_DIR / "registry.json"
DESKTOP = Path.home() / "Desktop"
BASELINE_MANIFEST = TRACK_DIR / (
    "v20260717T144107__asciify-part-template-authored__walk_all_directions.frames.json"
)
BASELINE_GIF = TRACK_DIR / (
    "v20260717T144107__asciify-part-template-authored__walk_all_directions.gif"
)
CODEBOOK = REPO / (
    "docs/research/ascii/verification/fl4547/"
    "authored_walk_codebook.v1.json"
)

GRID_W = 5
GRID_H = 5
FRAMES = 18
LIMBS = ("arm_a", "arm_b", "leg_a", "leg_b")
ARM_ROLES = {"arm_a", "arm_b"}
PHASE_NAMES = ("contact", "passing", "opposite_contact", "opposite_passing")

# Discovery stays Unicode-wide.  These finite terms only rank the already
# discovered supply and cannot exclude CJK partial strokes or other admitted
# non-emoji shapes.
DISCOVERY_K = 384
POOL_LIMIT = 30
LOCAL_KEEP = 14
LAMBDA_SHAPE = 0.80
LAMBDA_ANGLE = 0.018
LAMBDA_STROKE = 0.72
LAMBDA_LINE_LANGUAGE = 0.68
LAMBDA_SWITCH = 0.10
LAMBDA_MASK_CHANGE = 0.18
LAMBDA_INTERPOLATION = 0.012

OWNERSHIP_CONTRACT = {
    "accepted_semantic_skeleton_motion_owner": True,
    "all_eight_source_directions_compiled_directly": True,
    "held_head_spine_layer_owner": True,
    "explicit_shoulder_hip_pivot_owner": True,
    "four_symbolic_gait_key_owner": True,
    "persistent_limb_arc_slot_owner": True,
    "full_non_emoji_unicode_discovery_owner": True,
    "soft_line_language_after_discovery": True,
    "hard_limb_cell_adjacency_owner": True,
    "connection_relation_target_owner": True,
    "non_joint_occlusion_suppression_owner": True,
    "cell_is_presentation_surface_only": True,
}


def folded_angle_distance(a: float, b: float) -> float:
    delta = abs(a - b) % 180.0
    return min(delta, 180.0 - delta)


def cyclic_range(start: int, end: int, count: int = FRAMES) -> list[int]:
    values = [start]
    while values[-1] != end:
        values.append((values[-1] + 1) % count)
        if len(values) > count:
            raise ValueError("cyclic interval did not terminate")
    return values


def detect_gait_keys(ankle_a: np.ndarray, ankle_b: np.ndarray) -> dict[str, int]:
    """Find the four ordered symbolic poses from direct distal-limb motion."""
    delta = np.asarray(ankle_a, dtype=float) - np.asarray(ankle_b, dtype=float)
    centered = delta - delta.mean(axis=0, keepdims=True)
    if float(np.linalg.norm(centered)) < 1e-8:
        score = np.sin(np.linspace(0.0, 2.0 * math.pi, len(delta), endpoint=False))
    else:
        _u, _s, vh = np.linalg.svd(centered, full_matrices=False)
        score = centered @ vh[0]
    contact = int(np.argmax(score))
    opposite = int(np.argmin(score))
    forward = cyclic_range(contact, opposite, len(score))
    backward = cyclic_range(opposite, contact, len(score))
    midpoint = float((score[contact] + score[opposite]) * 0.5)
    passing = min(forward[1:-1], key=lambda i: (abs(float(score[i]) - midpoint), i))
    opposite_passing = min(
        backward[1:-1], key=lambda i: (abs(float(score[i]) - midpoint), i)
    )
    return {
        "contact": contact,
        "passing": int(passing),
        "opposite_contact": opposite,
        "opposite_passing": int(opposite_passing),
    }


def ordered_part_path(parts: dict, role: str) -> list[tuple[float, float]]:
    mask = parts.get(role)
    if mask is None:
        return []
    path = rig.longest_skeleton_path(mask)
    if role == "torso" and path and path[0][1] > path[-1][1]:
        path.reverse()
    if role in LIMBS:
        path = rig._path_proximal_first(path, parts["torso"])
    return path


def path_angle(path: list[tuple[float, float]]) -> float:
    if len(path) < 2:
        return 90.0
    a, b = path[0], path[-1]
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0


def distal_points(frames: list[dict], role: str) -> np.ndarray:
    points = []
    last = (0.0, 0.0)
    for parts in frames:
        path = ordered_part_path(parts, role)
        if path:
            last = path[-1]
        points.append(last)
    return np.asarray(points, dtype=float)


def _quantized_offset(path: list[tuple[float, float]], pivot: tuple[int, int],
                      source_shape: tuple[int, int]) -> tuple[int, int]:
    """Project a direct limb vector onto at most two 5x5 cell steps."""
    if len(path) < 2:
        return pivot
    source_h, source_w = source_shape
    dx = (path[-1][0] - path[0][0]) * GRID_W / max(1.0, float(source_w))
    dy = (path[-1][1] - path[0][1]) * GRID_H / max(1.0, float(source_h))
    magnitude = max(abs(dx), abs(dy))
    if magnitude < 0.55:
        raw_dx = path[-1][0] - path[0][0]
        raw_dy = path[-1][1] - path[0][1]
        if abs(raw_dx) >= abs(raw_dy):
            dx, dy = math.copysign(1.0, raw_dx if raw_dx else 1.0), 0.0
        else:
            dx, dy = 0.0, math.copysign(1.0, raw_dy if raw_dy else 1.0)
    scale = min(2.0, max(1.0, magnitude)) / max(1e-8, magnitude)
    ox = int(round(dx * scale))
    oy = int(round(dy * scale))
    ox = max(-2, min(2, ox))
    oy = max(-2, min(2, oy))
    if ox == 0 and oy == 0:
        oy = 1
    x = max(0, min(GRID_W - 1, pivot[0] + ox))
    y = max(0, min(GRID_H - 1, pivot[1] + oy))
    if (x, y) == pivot:
        y = max(0, min(GRID_H - 1, pivot[1] + (1 if pivot[1] < 4 else -1)))
    return x, y


def limb_cells(path: list[tuple[float, float]], pivot: tuple[int, int],
               source_shape: tuple[int, int]) -> list[tuple[int, int]]:
    distal = _quantized_offset(path, pivot, source_shape)
    cells = active._connection_cells(pivot, distal)[1:]
    # One Unicode cell per anatomical segment.  At most two cells maintains the
    # 5-row budget while the Bresenham walk guarantees adjacency to the pivot.
    return cells[:2]


def caricature_limb_cells(parts: dict, pivots: dict[str, tuple[int, int]]) -> dict[str, list[tuple[int, int]]]:
    """Spread direct projected counterparts into legible 5x5 body-plan lanes.

    The direct skeleton still owns side ordering, segment angle, gait phase, and
    foreshortening.  The layout prior owns only separation at the tiny-grid
    boundary: arms use the outer lanes, legs use the inner lanes.  This is the
    deliberate caricature step required to stop a narrow projection from
    quantizing every part onto the spine.
    """
    paths = {role: ordered_part_path(parts, role) for role in LIMBS}

    def distal_x(role: str) -> float:
        path = paths[role]
        return path[-1][0] if path else (0.0 if role.endswith("a") else 1.0)

    arm_order = sorted(ARM_ROLES, key=lambda role: (distal_x(role), role))
    arm_targets = {
        arm_order[0]: (0, 2),
        arm_order[1]: (4, 2),
    }

    leg_roles = ("leg_a", "leg_b")
    leg_order = sorted(leg_roles, key=lambda role: (distal_x(role), role))
    lengths = {
        role: sum(math.dist(path[index - 1], path[index])
                  for index in range(1, len(path)))
        for role, path in paths.items() if role in leg_roles
    }
    longest = max(lengths.values(), default=1.0)
    leg_targets = {}
    for index, role in enumerate(leg_order):
        row = 3 if lengths.get(role, 0.0) < longest * 0.78 else 4
        leg_targets[role] = ((1, 3)[index], row)

    targets = {**arm_targets, **leg_targets}
    return {
        role: active._connection_cells(pivots[role], target)[1:3]
        for role, target in targets.items()
    }


def port_name(a: tuple[int, int], b: tuple[int, int]) -> str:
    dx = max(-1, min(1, b[0] - a[0]))
    dy = max(-1, min(1, b[1] - a[1]))
    table = {
        (-1, -1): "tl", (0, -1): "t", (1, -1): "tr",
        (-1, 0): "l", (1, 0): "r",
        (-1, 1): "bl", (0, 1): "b", (1, 1): "br",
    }
    return table[(dx, dy)]


def has_port(mask: int, port: str) -> bool:
    points = {(x, y) for y in range(8) for x in range(8)
              if mask & (1 << (8 * y + x))}
    if port == "l":
        return any(x == 0 for x, _y in points)
    if port == "r":
        return any(x == 7 for x, _y in points)
    if port == "t":
        return any(y == 0 for _x, y in points)
    if port == "b":
        return any(y == 7 for _x, y in points)
    corners = {
        "tl": (range(0, 3), range(0, 3)),
        "tr": (range(5, 8), range(0, 3)),
        "bl": (range(0, 3), range(5, 8)),
        "br": (range(5, 8), range(5, 8)),
    }
    xs, ys = corners[port]
    return any(x in xs and y in ys for x, y in points)


def target_mask(angle: float, ports: set[str], kind: str = "stroke") -> int:
    mask = active._target_mask("head" if kind == "head" else "stroke", angle)
    rays = {
        "l": ((3, 3), (0, 3)), "r": ((4, 3), (7, 3)),
        "t": ((3, 3), (3, 0)), "b": ((3, 4), (3, 7)),
        "tl": ((3, 3), (0, 0)), "tr": ((4, 3), (7, 0)),
        "bl": ((3, 4), (0, 7)), "br": ((4, 4), (7, 7)),
    }
    for port in ports:
        (x0, y0), (x1, y1) = rays[port]
        for step in range(8):
            t = step / 7.0
            x = int(round(x0 + (x1 - x0) * t))
            y = int(round(y0 + (y1 - y0) * t))
            mask |= 1 << (8 * y + x)
    return mask


def baseline_role_glyphs(baseline: dict, skeletons: dict) -> dict[str, Counter]:
    """Recover a soft per-role line language from T144107 without hard gating."""
    counters = {role: Counter() for role in ("head", "torso", *LIMBS)}
    for direction in rig.DISPLAY_DIRS:
        frames = skeletons[direction]
        for fi, parts in enumerate(frames):
            rows = baseline["frames"][direction][fi]
            source_h, source_w = next(iter(parts.values())).shape
            for row in range(GRID_H):
                for col in range(GRID_W):
                    ch = rows[row][col]
                    if ch == " ":
                        continue
                    # Use a fixed normalized cell only for assigning baseline
                    # vocabulary evidence.  It never owns new-frame placement.
                    x0 = int(round(col * source_w / GRID_W))
                    x1 = int(round((col + 1) * source_w / GRID_W))
                    y0 = int(round(row * source_h / GRID_H))
                    y1 = int(round((row + 1) * source_h / GRID_H))
                    counts = {
                        role: int(mask[y0:y1, x0:x1].sum())
                        for role, mask in parts.items()
                    }
                    if counts:
                        owner, count = max(counts.items(), key=lambda item: (item[1], item[0]))
                        if count:
                            counters[owner][ord(ch)] += 1
    return counters


def _role_codebook_indices(catalog: dict) -> dict[str, set[int]]:
    pools, _missing = glyphlib.load_codebook(CODEBOOK, catalog)
    return {
        "head": set(pools.get("head", [])),
        "torso": set(pools.get("torso", [])),
        "arm_a": set(pools.get("arm", [])),
        "arm_b": set(pools.get("arm", [])),
        "leg_a": set(pools.get("leg_upper", [])) | set(pools.get("leg_lower", [])),
        "leg_b": set(pools.get("leg_upper", [])) | set(pools.get("leg_lower", [])),
    }


def discover(target: int, ports: set[str], role: str, catalog: dict,
             peak_indices: set[int], codebook_indices: set[int],
             cache: dict) -> list[int]:
    key = (target, tuple(sorted(ports)), role)
    if key in cache:
        return cache[key]
    raw = rig.glyph_candidates(target, catalog, DISCOVERY_K)
    angle_target = glyphlib.glyph_angle(target, {})
    scored = []
    raw_cost = {int(candidate["index"]): float(candidate["base_cost"]) for candidate in raw}
    # Candidate discovery runs against the complete census above.  Only after
    # that scan do recurring T144107 and authored line-language candidates join
    # the derived lattice.  This is a preference, never an admission filter.
    derived = set(raw_cost) | peak_indices | codebook_indices
    for index in derived:
        mask = int(catalog["masks"][index])
        value = LAMBDA_SHAPE * raw_cost.get(
            index, (mask ^ target).bit_count() / 64.0
        )
        value += LAMBDA_ANGLE * folded_angle_distance(
            angle_target, glyphlib.glyph_angle(mask, {})
        )
        stroke_weight = 0.04 if role == "head" else LAMBDA_STROKE
        value += stroke_weight * glyphlib.strokeness(mask)
        if role != "head" and index in peak_indices:
            value -= LAMBDA_LINE_LANGUAGE
        if index in codebook_indices:
            value -= LAMBDA_LINE_LANGUAGE * (0.90 if role == "head" else 0.55)
        scored.append((value, int(catalog["scalars"][index]), index))
    if not scored:
        raise ValueError(f"no Unicode candidate discovered for {role}")
    result = [item[2] for item in sorted(scored)[:LOCAL_KEEP]]
    cache[key] = result
    return result


def unary_cost(index: int, target: int, angle: float, role: str, catalog: dict,
               peak_indices: set[int], codebook_indices: set[int]) -> float:
    mask = int(catalog["masks"][index])
    cost = LAMBDA_SHAPE * ((mask ^ target).bit_count() / 64.0)
    cost += LAMBDA_ANGLE * folded_angle_distance(
        angle, glyphlib.glyph_angle(mask, {})
    )
    stroke_weight = 0.04 if role == "head" else LAMBDA_STROKE
    cost += stroke_weight * glyphlib.strokeness(mask)
    if role != "head" and index in peak_indices:
        cost -= LAMBDA_LINE_LANGUAGE
    if index in codebook_indices:
        cost -= LAMBDA_LINE_LANGUAGE * (0.90 if role == "head" else 0.55)
    return cost


def transition_cost(previous: int, current: int, expected_angle: float,
                    catalog: dict) -> float:
    pmask = int(catalog["masks"][previous])
    cmask = int(catalog["masks"][current])
    cost = LAMBDA_SWITCH if previous != current else 0.0
    cost += LAMBDA_MASK_CHANGE * ((pmask ^ cmask).bit_count() / 64.0)
    observed = folded_angle_distance(
        glyphlib.glyph_angle(pmask, {}), glyphlib.glyph_angle(cmask, {})
    )
    cost += LAMBDA_INTERPOLATION * abs(observed - expected_angle)
    return cost


def solve_interval(frames: list[int], candidates: list[list[int]], targets: list[int],
                   angles: list[float], fixed_start: int, fixed_end: int,
                   role: str, catalog: dict, peak: set[int], codebook: set[int]) -> dict[int, int]:
    states = {fixed_start: (unary_cost(fixed_start, targets[frames[0]], angles[frames[0]],
                                      role, catalog, peak, codebook), [fixed_start])}
    for offset, fi in enumerate(frames[1:], start=1):
        allowed = [fixed_end] if fi == frames[-1] else candidates[fi]
        next_states = {}
        source_step = folded_angle_distance(angles[frames[offset - 1]], angles[fi])
        for current in allowed:
            best = min(
                (
                    value[0] + transition_cost(previous, current, source_step, catalog),
                    previous,
                )
                for previous, value in states.items()
            )
            next_states[current] = (
                best[0] + unary_cost(current, targets[fi], angles[fi], role,
                                     catalog, peak, codebook),
                states[best[1]][1] + [current],
            )
        states = next_states
    path = min(states.values(), key=lambda item: item[0])[1]
    return {fi: path[index] for index, fi in enumerate(frames)}


def solve_keyed_track(candidates: list[list[int]], targets: list[int], angles: list[float],
                      keys: dict[str, int], role: str, catalog: dict,
                      peak: set[int], codebook: set[int]) -> tuple[list[int], dict[str, int]]:
    ordered_frames = [keys[name] for name in PHASE_NAMES]
    # Phase labels are defined cyclically starting at contact.  Re-sort by
    # temporal distance from contact to tolerate PCA sign and wrap placement.
    start = keys["contact"]
    ordered_frames = sorted(set(ordered_frames), key=lambda fi: (fi - start) % FRAMES)
    if len(ordered_frames) != 4:
        raise ValueError(f"gait keys are not unique: {keys}")
    fixed = {}
    for fi in ordered_frames:
        fixed[fi] = min(
            candidates[fi],
            key=lambda index: (
                unary_cost(index, targets[fi], angles[fi], role, catalog, peak, codebook),
                int(catalog["scalars"][index]),
            ),
        )
    selected = [catalog["space_index"]] * FRAMES
    for index, a in enumerate(ordered_frames):
        b = ordered_frames[(index + 1) % len(ordered_frames)]
        interval = cyclic_range(a, b)
        solved = solve_interval(interval, candidates, targets, angles, fixed[a], fixed[b],
                                role, catalog, peak, codebook)
        for fi, glyph in solved.items():
            selected[fi] = glyph
    by_phase = {name: fixed[frame] for name, frame in keys.items()}
    return selected, by_phase


def _layer_ports(cells: list[tuple[int, int]], pivot: tuple[int, int]) -> list[set[str]]:
    ports = []
    chain = [pivot] + cells
    for index, cell in enumerate(cells, start=1):
        required = {port_name(cell, chain[index - 1])}
        if index + 1 < len(chain):
            required.add(port_name(cell, chain[index + 1]))
        ports.append(required)
    return ports


def compile_direction(direction: str, frames: list[dict], keys: dict[str, int],
                      catalog: dict, peak_by_role: dict[str, set[int]],
                      codebook_by_role: dict[str, set[int]], discovery_cache: dict) -> dict:
    source_shape = next(iter(frames[0].values())).shape
    contact = keys["contact"]
    torso_angle = path_angle(ordered_part_path(frames[contact], "torso"))
    base_cells = {"head": (2, 0), "torso_upper": (2, 1), "torso_lower": (2, 2)}
    pivots = {
        "arm_a": base_cells["torso_upper"], "arm_b": base_cells["torso_upper"],
        "leg_a": base_cells["torso_lower"], "leg_b": base_cells["torso_lower"],
    }
    cells = {role: [] for role in LIMBS}
    angles = {role: [] for role in LIMBS}
    for parts in frames:
        layout = caricature_limb_cells(parts, pivots)
        for role in LIMBS:
            path = ordered_part_path(parts, role)
            cells[role].append(layout[role])
            angles[role].append(path_angle(path))

    base_required = {
        "head": {"b"},
        "torso_upper": {"t", "b"},
        "torso_lower": {"t"},
    }
    for role in LIMBS:
        key = "torso_upper" if role in ARM_ROLES else "torso_lower"
        for frame_cells in cells[role]:
            if frame_cells:
                base_required[key].add(port_name(pivots[role], frame_cells[0]))

    scalar_to_index = {int(value): index for index, value in enumerate(catalog["scalars"])}
    base_selected = {}
    for base_role, cell in base_cells.items():
        role = "head" if base_role == "head" else "torso"
        angle = 0.0 if role == "head" else torso_angle
        target_ports = set() if role == "head" else base_required[base_role]
        target = target_mask(angle, target_ports, role)
        choices = discover(target, base_required[base_role], role, catalog,
                           peak_by_role[role], codebook_by_role[role], discovery_cache)
        base_selected[cell] = choices[0]

    sequences = {}
    track_meta = {}
    for role in LIMBS:
        for slot in range(2):
            track_id = f"{role}@{slot}"
            candidates = []
            targets = []
            active_frames = []
            ports_by_frame = []
            for fi in range(FRAMES):
                frame_cells = cells[role][fi]
                active_slot = slot < len(frame_cells)
                active_frames.append(active_slot)
                if active_slot:
                    ports = _layer_ports(frame_cells, pivots[role])[slot]
                    angle = angles[role][fi]
                    target = target_mask(angle, ports)
                    choices = discover(target, ports, role, catalog, peak_by_role[role],
                                       codebook_by_role[role], discovery_cache)
                else:
                    ports = set()
                    angle = angles[role][fi]
                    target = 0
                    choices = [catalog["space_index"]]
                candidates.append(choices)
                targets.append(target)
                ports_by_frame.append(sorted(ports))
            selected, key_glyphs = solve_keyed_track(
                candidates, targets, angles[role], keys, role, catalog,
                peak_by_role[role], codebook_by_role[role],
            )
            # Visibility remains part of the source motion contract.  A missing
            # second segment is blank, while its key choice stays in the receipt.
            for fi, active_slot in enumerate(active_frames):
                if not active_slot:
                    selected[fi] = catalog["space_index"]
            sequences[track_id] = selected
            track_meta[track_id] = {
                "cells": [list(frame_cells[slot]) if slot < len(frame_cells) else None
                          for frame_cells in cells[role]],
                "ports": ports_by_frame,
                "angles": [round(value, 6) for value in angles[role]],
                "active": active_frames,
                "key_glyph_scalars": {
                    phase: int(catalog["scalars"][index]) for phase, index in key_glyphs.items()
                },
            }

    selected_frames = []
    contributor_frames = []
    occlusion_suppression_count = 0
    for fi in range(FRAMES):
        frame = [catalog["space_index"]] * (GRID_W * GRID_H)
        contributors = {}
        for cell, index in base_selected.items():
            frame[cell[1] * GRID_W + cell[0]] = index
            contributors[f"{cell[0]},{cell[1]}"] = ["held_base"]
        claims = {}
        for role in LIMBS:
            for slot in range(2):
                if slot >= len(cells[role][fi]):
                    continue
                cell = cells[role][fi][slot]
                if cell == pivots[role]:
                    continue
                track_id = f"{role}@{slot}"
                # Stable occlusion score: the outermost directly projected part
                # wins.  Ties use semantic name, never glyph shape.
                score = (abs(cell[0] - 2), slot, role)
                claims.setdefault(cell, []).append((score, track_id, sequences[track_id][fi]))
        winners = {}
        for cell, options in claims.items():
            winner = max(options, key=lambda item: item[0])
            winners[cell] = winner[1]
        # Occlusion is resolved at layer-prefix granularity.  Once a proximal
        # segment is hidden, every more-distal segment on that limb is hidden as
        # well.  This prevents the disconnected floating fragments exposed by
        # the first pre-correction render of this increment.
        visible_tracks = set()
        for role in LIMBS:
            prefix_visible = True
            for slot, cell in enumerate(cells[role][fi]):
                track_id = f"{role}@{slot}"
                index = cell[1] * GRID_W + cell[0]
                if frame[index] != catalog["space_index"]:
                    prefix_visible = False
                if winners.get(cell) != track_id:
                    prefix_visible = False
                if prefix_visible:
                    visible_tracks.add(track_id)
                else:
                    occlusion_suppression_count += 1
        for cell, options in claims.items():
            winner = max(options, key=lambda item: item[0])
            if winner[1] not in visible_tracks:
                continue
            index = cell[1] * GRID_W + cell[0]
            if frame[index] != catalog["space_index"]:
                continue
            frame[index] = winner[2]
            contributors[f"{cell[0]},{cell[1]}"] = [winner[1]]
            occlusion_suppression_count += max(0, len(options) - 1)
        selected_frames.append(frame)
        contributor_frames.append(contributors)

    adjacency_failures = 0
    visible_connection_failures = 0
    for role in LIMBS:
        for fi, frame_cells in enumerate(cells[role]):
            chain = [pivots[role]] + frame_cells
            for a, b in zip(chain, chain[1:]):
                if max(abs(a[0] - b[0]), abs(a[1] - b[1])) != 1:
                    adjacency_failures += 1
            visible = []
            for slot, cell in enumerate(frame_cells):
                key = f"{cell[0]},{cell[1]}"
                if f"{role}@{slot}" in contributor_frames[fi].get(key, []):
                    visible.append(cell)
            visible_chain = [pivots[role]] + visible
            for a, b in zip(visible_chain, visible_chain[1:]):
                if max(abs(a[0] - b[0]), abs(a[1] - b[1])) != 1:
                    visible_connection_failures += 1

    return {
        "keys": keys,
        "base_cells": {name: list(cell) for name, cell in base_cells.items()},
        "base_required_ports": {name: sorted(ports) for name, ports in base_required.items()},
        "base_glyph_scalars": {
            f"{cell[0]},{cell[1]}": int(catalog["scalars"][index])
            for cell, index in base_selected.items()
        },
        "pivots": {role: list(cell) for role, cell in pivots.items()},
        "tracks": track_meta,
        "selected": selected_frames,
        "contributors": contributor_frames,
        "occlusion_suppression_count": occlusion_suppression_count,
        "adjacency_failure_count": adjacency_failures,
        "visible_connection_failure_count": visible_connection_failures,
    }


def baseline_metrics(baseline: dict) -> dict:
    unique = set()
    changes = 0
    comparisons = 0
    occupied = []
    for direction in rig.DISPLAY_DIRS:
        frames = baseline["frames"][direction]
        for frame in frames:
            unique.update(ch for row in frame for ch in row if ch != " ")
            occupied.append(sum(ch != " " for row in frame for ch in row))
        for fi in range(FRAMES):
            a, b = frames[fi], frames[(fi + 1) % FRAMES]
            for row in range(GRID_H):
                for col in range(GRID_W):
                    if a[row][col] != " " and b[row][col] != " ":
                        comparisons += 1
                        changes += a[row][col] != b[row][col]
    return {
        "unique_glyph_count": len(unique),
        "persistent_cell_change_rate": round(changes / max(1, comparisons), 6),
        "mean_occupied_cells": round(float(np.mean(occupied)), 6),
    }


def output_metrics(compiled: dict, catalog: dict) -> dict:
    unique = set()
    head_changes = 0
    spine_changes = 0
    changes = 0
    comparisons = 0
    distinct_frame0 = set()
    for direction in rig.DISPLAY_DIRS:
        frames = compiled[direction]["selected"]
        distinct_frame0.add(tuple(frames[0]))
        unique.update(index for frame in frames for index in frame
                      if index != catalog["space_index"])
        base_positions = [tuple(value) for value in compiled[direction]["base_cells"].values()]
        for fi in range(FRAMES):
            a, b = frames[fi], frames[(fi + 1) % FRAMES]
            for index, (ga, gb) in enumerate(zip(a, b)):
                if ga != catalog["space_index"] and gb != catalog["space_index"]:
                    comparisons += 1
                    changes += ga != gb
            head = base_positions[0]
            if a[head[1] * GRID_W + head[0]] != b[head[1] * GRID_W + head[0]]:
                head_changes += 1
            for spine in base_positions[1:]:
                if a[spine[1] * GRID_W + spine[0]] != b[spine[1] * GRID_W + spine[0]]:
                    spine_changes += 1
    return {
        "candidate_discovery_catalog_count": len(catalog["rows"]),
        "compiled_source_direction_count": len(compiled),
        "distinct_direction_frame0_count": len(distinct_frame0),
        "symbolic_key_count_per_direction": 4,
        "held_head_glyph_change_count": head_changes,
        "held_spine_glyph_change_count": spine_changes,
        "limb_cell_adjacency_failure_count": sum(
            value["adjacency_failure_count"] for value in compiled.values()
        ),
        "visible_chain_connection_failure_count": sum(
            value["visible_connection_failure_count"] for value in compiled.values()
        ),
        "occlusion_suppression_count": sum(
            value["occlusion_suppression_count"] for value in compiled.values()
        ),
        "non_joint_mask_union_count": 0,
        "unique_glyph_count": len(unique),
        "persistent_cell_change_rate": round(changes / max(1, comparisons), 6),
    }


def render_and_write(compiled: dict, catalog: dict, source_hashes: dict,
                     baseline: dict, frame_ms: int):
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
            ink = Image.new("RGB", rgba.size, (120, 230, 120))
            base.paste(ink, mask=rgba.getchannel("A"))
            tile_cache[scalar] = base
        return tile_cache[scalar].resize((cell_px, cell_px), Image.Resampling.NEAREST)

    cell_px, label_h, pad = 26, 16, 6
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
            phase = next((name for name, frame in compiled[direction]["keys"].items()
                          if frame == fi), None)
            suffix = f" {phase}" if phase else ""
            draw.text((x0, y0), f"{direction} f{fi}{suffix}",
                      fill=(150, 210, 150), font=label_font)
            rows = []
            selected = compiled[direction]["selected"][fi]
            for row in range(GRID_H):
                chars = []
                for col in range(GRID_W):
                    index = selected[row * GRID_W + col]
                    scalar = int(catalog["scalars"][index])
                    chars.append(chr(scalar))
                    if index != catalog["space_index"]:
                        canvas.paste(tile(index, cell_px),
                                     (x0 + col * cell_px, y0 + label_h + row * cell_px))
                rows.append("".join(chars))
            manifest_frames[direction].append({
                "frame_index": fi,
                "phase": phase,
                "rows": rows,
                "contributors": compiled[direction]["contributors"][fi],
            })
        gif_frames.append(canvas)

    timestamp = datetime.now().strftime("v%Y%m%dT%H%M%S")
    name = f"{timestamp}__stone-layer-keyframes-5x5__walk_all_directions"
    tracked_gif = TRACK_DIR / f"{name}.gif"
    tracked_manifest = TRACK_DIR / f"{name}.frames.json"
    keyframe_png = TRACK_DIR / f"{name}__keyframes.png"
    desktop_gif = DESKTOP / f"{name}.gif"
    desktop_keyframes = DESKTOP / f"{name}__keyframes.png"
    gif_frames[0].save(tracked_gif, save_all=True, append_images=gif_frames[1:],
                       duration=frame_ms, loop=0, disposal=2)
    shutil.copy2(tracked_gif, desktop_gif)

    # Four symbolic-pose review rows, one panel per direct direction.
    review = Image.new("RGB", (canvas_w * 2, canvas_h * 2), (8, 8, 8))
    for row, phase in enumerate(PHASE_NAMES):
        for direction_index, direction in enumerate(rig.DISPLAY_DIRS):
            fi = compiled[direction]["keys"][phase]
            source = gif_frames[fi]
            gc, gr = direction_index % rig.GRID_COLS, direction_index // rig.GRID_COLS
            crop = source.crop((
                pad + gc * (panel_w + pad),
                pad + gr * (panel_h + label_h + pad),
                pad + gc * (panel_w + pad) + panel_w,
                pad + gr * (panel_h + label_h + pad) + label_h + panel_h,
            ))
            x = (direction_index % 4) * (panel_w + pad) + pad + (row % 2) * canvas_w
            y = (direction_index // 4) * (panel_h + label_h + pad) + pad + (row // 2) * canvas_h
            review.paste(crop, (x, y))
    review.save(keyframe_png)
    shutil.copy2(keyframe_png, desktop_keyframes)

    metrics = output_metrics(compiled, catalog)
    baseline_summary = baseline_metrics(baseline)
    manifest = {
        "schema": "fl4561.stone_layer_keyframes.v1",
        "status": "experimental_increment5_operator_review_open",
        "primary_owner": "FL-4561",
        "lineage": ["FL-4512", "FL-4547", "xu-2010-2017", "benard-2012"],
        "name": name,
        "timestamp": timestamp,
        "grid": [GRID_W, GRID_H],
        "frames_per_direction": FRAMES,
        "source_checkpoint": str(rig.ACCEPTED_PERPART_CHECKPOINT.relative_to(REPO)),
        "source_checkpoint_sha256": rig.ACCEPTED_PERPART_CHECKPOINT_SHA256,
        "visual_baseline": str(BASELINE_GIF.relative_to(REPO)),
        "visual_baseline_sha256": hashlib.sha256(BASELINE_GIF.read_bytes()).hexdigest(),
        "candidate_supply": "complete canonical pinned non-emoji Unicode census",
        "candidate_policy": "Unicode-wide discovery followed by finite line-language ranking",
        "direction_generation": "eight direct authored direction sources; no generated mirrors",
        "font_receipt": font_receipt,
        "descriptor_sha256": catalog["descriptor_sha256"],
        "descriptor_manifest_sha256": catalog["manifest_sha256"],
        "ownership_contract": OWNERSHIP_CONTRACT,
        "source_hashes": source_hashes,
        "metrics": metrics,
        "baseline_metrics": baseline_summary,
        "directions": {
            direction: {
                "symbolic_keys": compiled[direction]["keys"],
                "held_base_cells": compiled[direction]["base_cells"],
                "held_base_required_ports": compiled[direction]["base_required_ports"],
                "held_base_glyph_scalars": compiled[direction]["base_glyph_scalars"],
                "pivots": compiled[direction]["pivots"],
                "tracks": compiled[direction]["tracks"],
                "frames": manifest_frames[direction],
            }
            for direction in rig.DISPLAY_DIRS
        },
    }
    tracked_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    gif_hash = hashlib.sha256(tracked_gif.read_bytes()).hexdigest()
    manifest_hash = hashlib.sha256(tracked_manifest.read_bytes()).hexdigest()
    registry = json.loads(REGISTRY.read_text())
    registry.append({
        "name": name,
        "kind": "stone-layer-keyframes",
        "schema": manifest["schema"],
        "status": manifest["status"],
        "timestamp": timestamp,
        "grid": [GRID_W, GRID_H],
        "frames_per_dir": FRAMES,
        "tracked_gif": str(tracked_gif.relative_to(REPO)),
        "tracked_manifest": str(tracked_manifest.relative_to(REPO)),
        "tracked_keyframe_png": str(keyframe_png.relative_to(REPO)),
        "desktop_gif": str(desktop_gif),
        "desktop_keyframe_png": str(desktop_keyframes),
        "gif_sha256": gif_hash,
        "manifest_sha256": manifest_hash,
        "metrics": metrics,
        "baseline_metrics": baseline_summary,
    })
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=1) + "\n")
    return tracked_gif, tracked_manifest, keyframe_png, desktop_gif, metrics, baseline_summary


def compile_walk():
    baseline = json.loads(BASELINE_MANIFEST.read_text())
    skeletons, source_hashes, _bboxes = rig.load_accepted_perpart_skeletons(upscale=3)
    catalog = rig.load_descriptor_catalog()
    scalar_to_index = {int(value): index for index, value in enumerate(catalog["scalars"])}
    role_counts = baseline_role_glyphs(baseline, skeletons)
    peak_by_role = {
        role: {scalar_to_index[scalar] for scalar in counts if scalar in scalar_to_index}
        for role, counts in role_counts.items()
    }
    codebook_by_role = _role_codebook_indices(catalog)
    discovery_cache = {}
    compiled = {}
    for direction in rig.DISPLAY_DIRS:
        frames = skeletons[direction]
        keys = detect_gait_keys(distal_points(frames, "leg_a"), distal_points(frames, "leg_b"))
        compiled[direction] = compile_direction(
            direction, frames, keys, catalog, peak_by_role, codebook_by_role, discovery_cache
        )
        print(f"{direction}: keys={keys}")
    return compiled, catalog, source_hashes, baseline


def run(args) -> int:
    compiled, catalog, source_hashes, baseline = compile_walk()
    outputs = render_and_write(compiled, catalog, source_hashes, baseline, args.frame_ms)
    tracked_gif, manifest, keyframes, desktop, metrics, baseline_summary = outputs
    print(json.dumps({"metrics": metrics, "baseline": baseline_summary}, indent=2))
    print(f"desktop: {desktop}")
    print(f"tracked: {tracked_gif.relative_to(REPO)}")
    print(f"manifest: {manifest.relative_to(REPO)}")
    print(f"keyframes: {keyframes.relative_to(REPO)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frame-ms", type=int, default=90)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
