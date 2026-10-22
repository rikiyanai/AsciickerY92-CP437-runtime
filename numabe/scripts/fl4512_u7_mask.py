#!/usr/bin/env python3
"""Exact compiler-side primitives for the FL-4512 ``u7.mask.v1`` ABI.

This module owns no runtime winner.  It implements the paper-ratified page16
mask reduction, Zhang-Suen skeleton, topology counts, little-endian carrier,
and integer numerator so the U4 compiler can emit byte-stable descriptors
before the delete-first runtime scorer cutover.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Sequence
from pathlib import Path


SCORER_ID = "u7.mask.v1"
REDUCTION_VERSION = "coverage_ge_2.v1"
SKELETON_VERSION = "zhang_suen.v1"
MASK_SIDE = 8
SOURCE_SIDE = 16
MASK_BITS = MASK_SIDE * MASK_SIDE
STROKE_PORT_GRAPH_VERSION = "u7.stroke_port_graph.v1"
STROKE_PORT_MAX_TOTAL = 8
STROKE_PORT_MAX_CONNECTIVITY = STROKE_PORT_MAX_TOTAL * (STROKE_PORT_MAX_TOTAL - 1) // 2
_Q0_16_MAX = 65535
_Q1_15_MAX = 32767


class U7MaskError(ValueError):
    """Fail-closed descriptor construction error with a stable reason."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def _require_binary_mask(mask: int) -> int:
    if not isinstance(mask, int) or isinstance(mask, bool) or not 0 <= mask < (1 << MASK_BITS):
        raise U7MaskError("mask8_invalid", repr(mask))
    return mask


def mask_bit(mask: int, x: int, y: int) -> int:
    """Return bit ``8*y+x``; outside-grid samples are canonical zero."""
    if not 0 <= x < MASK_SIDE or not 0 <= y < MASK_SIDE:
        return 0
    return (mask >> (MASK_SIDE * y + x)) & 1


def mask8_from_page16_rgba(
    rgba8: Sequence[int], page_width: int, x0: int, y0: int
) -> int:
    """Reduce one page16 atlas cell using the frozen ``coverage >= 2`` rule."""
    if page_width <= 0 or x0 < 0 or y0 < 0 or len(rgba8) % 4:
        raise U7MaskError("page16_invalid")
    pixel_count = len(rgba8) // 4
    if pixel_count % page_width:
        raise U7MaskError("page16_invalid")
    page_height = pixel_count // page_width
    if x0 + SOURCE_SIDE > page_width or y0 + SOURCE_SIDE > page_height:
        raise U7MaskError("page16_cell_out_of_bounds", f"x={x0} y={y0}")

    result = 0
    for y in range(MASK_SIDE):
        for x in range(MASK_SIDE):
            coverage = 0
            for dy in range(2):
                for dx in range(2):
                    p = (((y0 + 2 * y + dy) * page_width) + x0 + 2 * x + dx) * 4
                    r, g, b, a = (int(rgba8[p + i]) for i in range(4))
                    if not all(0 <= channel <= 255 for channel in (r, g, b, a)):
                        raise U7MaskError("page16_channel_invalid", f"offset={p}")
                    coverage += int(a > 8 and (r | g | b) != 0)
            if coverage >= 2:
                result |= 1 << (MASK_SIDE * y + x)
    if result == 0:
        raise U7MaskError("mask8_empty")
    return result


def _ring(mask: int, x: int, y: int) -> tuple[int, ...]:
    # p2..p9: N, NE, E, SE, S, SW, W, NW.
    return (
        mask_bit(mask, x, y - 1),
        mask_bit(mask, x + 1, y - 1),
        mask_bit(mask, x + 1, y),
        mask_bit(mask, x + 1, y + 1),
        mask_bit(mask, x, y + 1),
        mask_bit(mask, x - 1, y + 1),
        mask_bit(mask, x - 1, y),
        mask_bit(mask, x - 1, y - 1),
    )


def crossing_number(mask: int, x: int, y: int) -> int:
    """Count zero-to-one transitions around the closed clockwise ring."""
    _require_binary_mask(mask)
    ring = _ring(mask, x, y)
    return sum(ring[i] == 0 and ring[(i + 1) % 8] == 1 for i in range(8))


def zhang_suen(mask: int) -> int:
    """Run the frozen ordered two-subiteration thinning to a fixed point."""
    current = _require_binary_mask(mask)
    while True:
        deleted_in_pair = False
        for step in (0, 1):
            marked = 0
            for y in range(MASK_SIDE):
                for x in range(MASK_SIDE):
                    bit = 1 << (MASK_SIDE * y + x)
                    if not current & bit:
                        continue
                    p2, _p3, p4, _p5, p6, _p7, p8, _p9 = _ring(current, x, y)
                    neighbor_count = sum(_ring(current, x, y))
                    transitions = crossing_number(current, x, y)
                    if not 2 <= neighbor_count <= 6 or transitions != 1:
                        continue
                    if step == 0:
                        deletable = p2 * p4 * p6 == 0 and p4 * p6 * p8 == 0
                    else:
                        deletable = p2 * p4 * p8 == 0 and p2 * p6 * p8 == 0
                    if deletable:
                        marked |= bit
            if marked:
                current &= ~marked
                deleted_in_pair = True
        if not deleted_in_pair:
            return current


def topology_counts(mask: int) -> tuple[int, int, int]:
    """Return ``(skeleton, endpoint_count, junction_cluster_count)``."""
    skeleton = zhang_suen(mask)
    endpoints = 0
    junction_pixels = 0
    for y in range(MASK_SIDE):
        for x in range(MASK_SIDE):
            bit = 1 << (MASK_SIDE * y + x)
            if not skeleton & bit:
                continue
            cn = crossing_number(skeleton, x, y)
            endpoints += int(cn == 1)
            if cn >= 3:
                junction_pixels |= bit

    junctions = 0
    remaining = junction_pixels
    while remaining:
        junctions += 1
        seed = remaining & -remaining
        frontier = seed
        remaining &= ~seed
        while frontier:
            pixel = frontier & -frontier
            frontier &= ~pixel
            index = pixel.bit_length() - 1
            x, y = index % MASK_SIDE, index // MASK_SIDE
            for ny in range(max(0, y - 1), min(MASK_SIDE, y + 2)):
                for nx in range(max(0, x - 1), min(MASK_SIDE, x + 2)):
                    neighbor = 1 << (MASK_SIDE * ny + nx)
                    if remaining & neighbor:
                        remaining &= ~neighbor
                        frontier |= neighbor
    return skeleton, endpoints, junctions


def betti_numbers(mask: int) -> tuple[int, int]:
    """Return foreground components and enclosed background components.

    The digital-topology pair is foreground 8-connectivity with background
    4-connectivity. Background components touching the 8-by-8 border are the
    exterior and do not count as holes.
    """
    mask = _require_binary_mask(mask)

    def component_count(bits: int, diagonal: bool) -> int:
        remaining = bits
        count = 0
        while remaining:
            count += 1
            seed = remaining & -remaining
            frontier = seed
            remaining &= ~seed
            while frontier:
                pixel = frontier & -frontier
                frontier &= ~pixel
                index = pixel.bit_length() - 1
                x, y = index % MASK_SIDE, index // MASK_SIDE
                for ny in range(max(0, y - 1), min(MASK_SIDE, y + 2)):
                    for nx in range(max(0, x - 1), min(MASK_SIDE, x + 2)):
                        if nx == x and ny == y:
                            continue
                        if not diagonal and nx != x and ny != y:
                            continue
                        neighbor = 1 << (MASK_SIDE * ny + nx)
                        if remaining & neighbor:
                            remaining &= ~neighbor
                            frontier |= neighbor
        return count

    beta0 = component_count(mask, True)
    background = ((1 << MASK_BITS) - 1) ^ mask
    border = 0
    for i in range(MASK_SIDE):
        border |= 1 << i
        border |= 1 << ((MASK_SIDE - 1) * MASK_SIDE + i)
        border |= 1 << (i * MASK_SIDE)
        border |= 1 << (i * MASK_SIDE + MASK_SIDE - 1)
    exterior_frontier = background & border
    exterior = 0
    while exterior_frontier:
        pixel = exterior_frontier & -exterior_frontier
        exterior_frontier &= ~pixel
        if exterior & pixel:
            continue
        exterior |= pixel
        index = pixel.bit_length() - 1
        x, y = index % MASK_SIDE, index // MASK_SIDE
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if 0 <= nx < MASK_SIDE and 0 <= ny < MASK_SIDE:
                neighbor = 1 << (MASK_SIDE * ny + nx)
                if background & neighbor and not exterior & neighbor:
                    exterior_frontier |= neighbor
    beta1 = component_count(background & ~exterior, False)
    return beta0, beta1


def score_num(mask: int, candidate: int) -> tuple[int, int, int, int, int, int]:
    """Return ``(score_num, H, E(M), E(G), J(M), J(G))`` exactly."""
    mask = _require_binary_mask(mask)
    candidate = _require_binary_mask(candidate)
    _sm, endpoints_m, junctions_m = topology_counts(mask)
    _sg, endpoints_g, junctions_g = topology_counts(candidate)
    hamming = (mask ^ candidate).bit_count()
    numerator = (
        70 * hamming
        + 15 * abs(endpoints_m - endpoints_g)
        + 15 * abs(junctions_m - junctions_g)
    )
    if not 0 <= numerator <= 6400:
        raise U7MaskError("score_num_out_of_range", str(numerator))
    return numerator, hamming, endpoints_m, endpoints_g, junctions_m, junctions_g


def u64_le_hex(mask: int) -> str:
    """Serialize the canonical u64 as eight little-endian bytes in hex."""
    return _require_binary_mask(mask).to_bytes(8, "little").hex()


def _skeleton_points(skeleton: int) -> set[tuple[int, int]]:
    """Return skeleton coordinates in row-major-independent set form."""
    return {
        (x, y)
        for y in range(MASK_SIDE)
        for x in range(MASK_SIDE)
        if mask_bit(skeleton, x, y)
    }


def _skeleton_neighbors(point: tuple[int, int], points: set[tuple[int, int]]) -> list[tuple[int, int]]:
    x, y = point
    return sorted(
        (
            (x + dx, y + dy)
            for dy in (-1, 0, 1)
            for dx in (-1, 0, 1)
            if (dx or dy) and (x + dx, y + dy) in points
        ),
        key=lambda item: (item[1], item[0]),
    )


def _skeleton_components(points: set[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """Partition skeleton pixels with the same foreground 8-connectivity as beta0."""
    remaining = set(points)
    components: list[list[tuple[int, int]]] = []
    while remaining:
        seed = min(remaining, key=lambda item: (item[1], item[0]))
        frontier = [seed]
        remaining.remove(seed)
        component: list[tuple[int, int]] = []
        while frontier:
            point = frontier.pop()
            component.append(point)
            for neighbor in _skeleton_neighbors(point, points):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    frontier.append(neighbor)
        components.append(sorted(component, key=lambda item: (item[1], item[0])))
    return sorted(components, key=lambda item: (item[0][1], item[0][0]))


def _boundary_label(point: tuple[int, int]) -> str | None:
    """Name an exact 8x8 border contact, with corners taking precedence.

    Glyph coordinates use the atlas convention with row zero at the visual
    top. The little-endian serialized CP437 slash diagonal therefore runs
    from NE to SW after decoding its byte carrier.
    """
    x, y = point
    if x == 0 and y == 0:
        return "NW"
    if x == MASK_SIDE - 1 and y == 0:
        return "NE"
    if x == 0 and y == MASK_SIDE - 1:
        return "SW"
    if x == MASK_SIDE - 1 and y == MASK_SIDE - 1:
        return "SE"
    if y == 0:
        return "N"
    if x == MASK_SIDE - 1:
        return "E"
    if y == MASK_SIDE - 1:
        return "S"
    if x == 0:
        return "W"
    return None


def _boundary_t_q0_16(label: str, point: tuple[int, int]) -> int | None:
    if label in {"NW", "NE", "SE", "SW", "interior"}:
        return None
    coordinate = point[0] if label in {"N", "S"} else point[1]
    return (coordinate * _Q0_16_MAX) // (MASK_SIDE - 1)


def _round_signed(numerator: int, denominator: int) -> int:
    if numerator >= 0:
        return (numerator + denominator // 2) // denominator
    return -((-numerator + denominator // 2) // denominator)


def _tangent_q1_15(
    point: tuple[int, int], component: list[tuple[int, int]], points: set[tuple[int, int]]
) -> tuple[int, int]:
    """Choose an inward local tangent using only integer skeleton geometry."""
    neighbors = _skeleton_neighbors(point, points)
    if not neighbors:
        return (0, 0)
    center_x = sum(item[0] for item in component)
    center_y = sum(item[1] for item in component)
    # The nearest neighbor to the component centroid points inward at a border
    # port. A lexicographic tie keeps symmetric glyphs byte-stable.
    neighbor = min(
        neighbors,
        key=lambda item: (
            (item[0] * len(component) - center_x) ** 2
            + (item[1] * len(component) - center_y) ** 2,
            item[1],
            item[0],
        ),
    )
    dx = neighbor[0] - point[0]
    dy = neighbor[1] - point[1]
    scaled_length = math.isqrt(
        (dx * dx + dy * dy) * _Q1_15_MAX * _Q1_15_MAX
    )
    if not scaled_length:
        return (0, 0)
    return (
        max(
            -_Q1_15_MAX,
            min(
                _Q1_15_MAX,
                _round_signed(dx * _Q1_15_MAX * _Q1_15_MAX, scaled_length),
            ),
        ),
        max(
            -_Q1_15_MAX,
            min(
                _Q1_15_MAX,
                _round_signed(dy * _Q1_15_MAX * _Q1_15_MAX, scaled_length),
            ),
        ),
    )


def _inward_neighbor(
    point: tuple[int, int], component: list[tuple[int, int]], points: set[tuple[int, int]]
) -> tuple[int, int] | None:
    """Return the deterministic neighbor used to extrapolate a terminal port."""
    neighbors = _skeleton_neighbors(point, points)
    if not neighbors:
        return None
    center_x = sum(item[0] for item in component)
    center_y = sum(item[1] for item in component)
    return min(
        neighbors,
        key=lambda item: (
            (item[0] * len(component) - center_x) ** 2
            + (item[1] * len(component) - center_y) ** 2,
            item[1],
            item[0],
        ),
    )


def _project_terminal_to_boundary(
    point: tuple[int, int], component: list[tuple[int, int]], points: set[tuple[int, int]]
) -> tuple[str, tuple[int, int], bool]:
    """Project an inset endpoint to the nearest virtual 8x8 border contact."""
    exact = _boundary_label(point)
    if exact is not None:
        return exact, point, False
    neighbor = _inward_neighbor(point, component, points)
    if neighbor is None:
        return "interior", point, False
    dx = neighbor[0] - point[0]
    dy = neighbor[1] - point[1]
    outward_x = -1 if dx > 0 else 1 if dx < 0 else 0
    outward_y = -1 if dy > 0 else 1 if dy < 0 else 0
    distances: list[int] = []
    if outward_x < 0:
        distances.append(point[0])
    elif outward_x > 0:
        distances.append(MASK_SIDE - 1 - point[0])
    if outward_y < 0:
        distances.append(point[1])
    elif outward_y > 0:
        distances.append(MASK_SIDE - 1 - point[1])
    if not distances:
        return "interior", point, False
    steps = min(distances)
    projected = (
        point[0] + outward_x * steps,
        point[1] + outward_y * steps,
    )
    label = _boundary_label(projected)
    if label is None:
        return "interior", point, False
    return label, projected, True


def _shortest_skeleton_path(
    start: tuple[int, int], end: tuple[int, int], points: set[tuple[int, int]]
) -> list[tuple[int, int]] | None:
    if start == end:
        return [start]
    frontier = [start]
    previous: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    while frontier:
        point = frontier.pop(0)
        for neighbor in _skeleton_neighbors(point, points):
            if neighbor in previous:
                continue
            previous[neighbor] = point
            if neighbor == end:
                path = [end]
                while path[-1] != start:
                    parent = previous[path[-1]]
                    if parent is None:
                        break
                    path.append(parent)
                return list(reversed(path))
            frontier.append(neighbor)
    return None


def _path_metrics(path: list[tuple[int, int]]) -> tuple[int, int, str]:
    arc_length_q8_8 = 0
    directions: list[tuple[int, int]] = []
    for first, second in zip(path, path[1:]):
        dx = second[0] - first[0]
        dy = second[1] - first[1]
        directions.append((dx, dy))
        arc_length_q8_8 += 362 if dx and dy else 256
    turn_count = sum(
        int(first[0] * second[1] != first[1] * second[0])
        for first, second in zip(directions, directions[1:])
    )
    if turn_count == 0:
        curvature_class = "straight"
    elif turn_count == 1:
        curvature_class = "corner"
    else:
        curvature_class = "curved"
    return arc_length_q8_8, turn_count, curvature_class


def stroke_port_graph(mask: int) -> dict[str, object]:
    """Compile a bounded component-labelled port graph from the U7 skeleton.

    Endpoints project to virtual border contacts along their outward tangent.
    Connectivity rows only join ports from one skeleton component.
    """
    mask = _require_binary_mask(mask)
    skeleton = zhang_suen(mask)
    points = _skeleton_points(skeleton)
    components = _skeleton_components(points)
    label_order = ("N", "NE", "E", "SE", "S", "SW", "W", "NW", "interior")
    ports: list[dict[str, object]] = []
    component_points: dict[int, set[tuple[int, int]]] = {}
    port_points: dict[int, tuple[int, int]] = {}
    component_port_ids: dict[int, list[int]] = {}
    next_port_id = 0

    for component_id, component in enumerate(components):
        component_set = set(component)
        component_points[component_id] = component_set
        # Crossing number is the canonical Zhang-Suen topology measure. Raw
        # 8-neighbor degree over-counts pixels around a one-cell corner.
        degree = {
            point: crossing_number(skeleton, point[0], point[1]) for point in component
        }
        # The endpoint coordinate is part of identity. Two terminals may share
        # a projected side or corner, but only repeated observations of the
        # same skeleton terminal/contact are merged.
        selected_by_terminal: dict[
            tuple[str, tuple[int, int]],
            tuple[str, tuple[int, int], tuple[int, int], bool],
        ] = {}
        for point in component:
            if degree[point] != 1:
                continue
            label, projected, virtual = _project_terminal_to_boundary(
                point, component, component_set
            )
            candidate = (label, projected, point, virtual)
            candidate_key = (label, point)
            current = selected_by_terminal.get(candidate_key)
            if current is None or (int(not virtual), point[1], point[0]) < (
                int(not current[3]), current[2][1], current[2][0]
            ):
                selected_by_terminal[candidate_key] = candidate

        # A junction or border run can provide a port when no endpoint lands on
        # that exact border. Select one deterministic representative per label.
        for label in label_order[:-1]:
            if any(candidate[0] == label for candidate in selected_by_terminal.values()):
                continue
            candidates = [point for point in component if _boundary_label(point) == label]
            if candidates:
                point = min(candidates, key=lambda item: (item[1], item[0]))
                selected_by_terminal[(label, point)] = (label, point, point, False)

        if not selected_by_terminal:
            # A closed loop or isolated point has no terminal border direction.
            center_x = sum(item[0] for item in component) / len(component)
            center_y = sum(item[1] for item in component) / len(component)
            center = min(
                component,
                key=lambda item: (
                    (item[0] - center_x) ** 2 + (item[1] - center_y) ** 2,
                    item[1],
                    item[0],
                ),
            )
            selected_by_terminal[("interior", center)] = (
                "interior",
                center,
                center,
                False,
            )

        label_rank = {label: index for index, label in enumerate(label_order)}
        selected = [
            candidate
            for candidate in sorted(
                selected_by_terminal.values(),
                key=lambda item: (
                    label_rank[item[0]],
                    item[2][1],
                    item[2][0],
                    item[1][1],
                    item[1][0],
                ),
            )
        ]
        component_port_ids[component_id] = []
        for label, projected, endpoint, virtual in selected:
            port_id = next_port_id
            next_port_id += 1
            degree_value = degree[endpoint]
            terminal_kind = (
                "endpoint" if degree_value == 1
                else "junction" if degree_value >= 3
                else "boundary"
            )
            ports.append(
                {
                    "port_id": port_id,
                    "component_id": component_id,
                    "side_or_corner": label,
                    "boundary_t_q0_16": _boundary_t_q0_16(label, projected),
                    "position_xy": [projected[0], projected[1]],
                    "endpoint_xy": [endpoint[0], endpoint[1]],
                    "virtual_boundary": virtual,
                    "tangent_q1_15": list(
                        _tangent_q1_15(endpoint, component, component_set)
                    ),
                    "endpoint_or_junction_degree": degree_value,
                    "terminal_kind": terminal_kind,
                }
            )
            component_port_ids[component_id].append(port_id)
            port_points[port_id] = endpoint

    connectivity: list[dict[str, object]] = []
    for component_id in sorted(component_port_ids):
        ids = component_port_ids[component_id]
        for first_index, first_id in enumerate(ids):
            for second_id in ids[first_index + 1:]:
                path = _shortest_skeleton_path(
                    port_points[first_id],
                    port_points[second_id],
                    component_points[component_id],
                )
                if path is None:
                    continue
                arc_length_q8_8, turn_count, curvature_class = _path_metrics(path)
                connectivity.append(
                    {
                        "component_id": component_id,
                        "port_a": first_id,
                        "port_b": second_id,
                        "connected": True,
                        "arc_length_q8_8": arc_length_q8_8,
                        "turn_count": turn_count,
                        "curvature_class": curvature_class,
                    }
                )
    overflow_reason: str | None = None
    if len(ports) > STROKE_PORT_MAX_TOTAL:
        overflow_reason = "capacity_exceeded:boundary_ports"
    emitted_ports = ports[:STROKE_PORT_MAX_TOTAL]
    emitted_port_ids = {int(port["port_id"]) for port in emitted_ports}
    connectivity = [
        edge
        for edge in connectivity
        if int(edge["port_a"]) in emitted_port_ids
        and int(edge["port_b"]) in emitted_port_ids
    ]
    if len(connectivity) > STROKE_PORT_MAX_CONNECTIVITY and overflow_reason is None:
        overflow_reason = "capacity_exceeded:port_connectivity"
    connectivity = connectivity[:STROKE_PORT_MAX_CONNECTIVITY]

    skeleton_edges = sum(len(_skeleton_neighbors(point, points)) for point in points) // 2
    _skeleton, _endpoints, junctions = topology_counts(mask)
    _beta0, holes = betti_numbers(mask)
    return {
        "graph_schema": STROKE_PORT_GRAPH_VERSION,
        "graph_valid": bool(mask) and overflow_reason is None,
        "graph_rejection_reason": overflow_reason,
        "boundary_port_count": len(emitted_ports),
        "port_connectivity_count": len(connectivity),
        "skeleton_component_graph": {
            "components": len(components),
            "holes": holes,
            "junctions": junctions,
            "skeleton_edges": skeleton_edges,
        },
        "boundary_ports": emitted_ports,
        "port_connectivity": connectivity,
    }


def descriptor(mask: int) -> dict[str, object]:
    """Create the compiler record shared by U4 rows, traces, and receipts."""
    mask = _require_binary_mask(mask)
    if mask == 0:
        raise U7MaskError("mask8_empty")
    skeleton, endpoints, junctions = topology_counts(mask)
    # Symmetry is intentionally absent. It is measured after repeated runtime
    # assignments and never admits a candidate upstream.
    beta0, beta1 = betti_numbers(mask)
    return {
        "scorer_id": SCORER_ID,
        "quantization_version": None,
        "reduction_version": REDUCTION_VERSION,
        "skeleton_version": SKELETON_VERSION,
        "mask8_u64_le_hex": u64_le_hex(mask),
        "skeleton_u64_le_hex": u64_le_hex(skeleton),
        "endpoint_count": endpoints,
        "junction_count": junctions,
        "beta0": beta0,
        "beta1": beta1,
        "euler_characteristic": beta0 - beta1,
        "stroke_port_graph": stroke_port_graph(mask),
    }


def predict_mesh_bundle_winners(
    artifact: dict,
    bridge_rows: Sequence[dict],
    material_key: int,
    excluded_glyph_ids: set[int] | frozenset[int] = frozenset(),
) -> list[dict[str, int | str]]:
    """Derive winners from frozen facts and a predeclared candidate removal."""
    matching_rows = [
        row
        for row in artifact.get("rows", [])
        if row.get("material_key") == material_key
        and row.get("role") == "mesh_feature"
        and row.get("route_key") == "mesh_feature.bundle.v1"
        and row.get("lane") == 0
    ]
    if len(matching_rows) != 1:
        raise U7MaskError("mesh_bundle_row_invalid", f"material={material_key}")
    pool = matching_rows[0]
    candidates: list[tuple[int, int]] = []
    for item in pool.get("candidate_descriptors", []):
        glyph_id = item.get("glyph_id")
        encoded = item.get("mask8_u64_le_hex")
        if not isinstance(glyph_id, int) or not isinstance(encoded, str):
            raise U7MaskError("candidate_descriptor_invalid")
        if glyph_id in excluded_glyph_ids:
            continue
        candidate_mask = int.from_bytes(bytes.fromhex(encoded), "little")
        candidates.append((glyph_id, candidate_mask))
    if not candidates:
        raise U7MaskError("declared_empty_pool", f"material={material_key}")

    predicted: list[dict[str, int | str]] = []
    for cell in bridge_rows:
        if (
            cell.get("kind") != "cell"
            or cell.get("material_id") != material_key
            or cell.get("axis_routing") != "mesh_bundle"
            or cell.get("mesh_bundle_valid") != 1
        ):
            continue
        encoded_mask = cell.get("mesh_bundle_merged_mask_8x8")
        if not isinstance(encoded_mask, str):
            raise U7MaskError("mesh_bundle_mask_missing")
        target_mask = int(encoded_mask, 16)
        scored = sorted(
            (score_num(target_mask, candidate_mask)[0], glyph_id)
            for glyph_id, candidate_mask in candidates
        )
        best_score, best_gid = scored[0]
        predicted.append(
            {
                "x": int(cell["x"]),
                "y": int(cell["y"]),
                "material_id": material_key,
                "before_gid": int(cell.get("final_gid", 0)),
                "predicted_gid": best_gid,
                "score_num": best_score,
                "mesh_bundle_merged_mask_8x8": encoded_mask,
                "scorer_id": SCORER_ID,
            }
        )
    return predicted


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    predict = subparsers.add_parser("predict-mesh-bundle")
    predict.add_argument("--artifact", required=True)
    predict.add_argument("--bridge", required=True)
    predict.add_argument("--material", type=int, required=True)
    predict.add_argument(
        "--exclude-gid",
        type=int,
        action="append",
        default=[],
        help="predeclare a candidate removed by the pending user action",
    )
    predict.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        artifact = json.loads(Path(args.artifact).read_text(encoding="utf-8"))
        rows = predict_mesh_bundle_winners(
            artifact,
            _read_jsonl(Path(args.bridge)),
            args.material,
            frozenset(args.exclude_gid),
        )
        output = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
        Path(args.out).write_text(output, encoding="utf-8")
    except (OSError, ValueError, json.JSONDecodeError, U7MaskError) as exc:
        print(f"FL4512_U7_PREDICT_REJECT {exc}")
        return 2
    print(f"FL4512_U7_PREDICT_PASS cells={len(rows)} out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
