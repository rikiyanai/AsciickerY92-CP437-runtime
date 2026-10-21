#!/usr/bin/env python3
"""Replay FL-4431's conservative b82-to-b73 terrain handoff.

The b82 image is only a conservative work-rejection aid. This contract first
reconstructs the exact subcell ray and authoritative A3D corner lattice from
the settled diagnostic capture, then runs the shader's tile/cell control flow
on the CPU. It also proves the round-6 R=max/G=min tile interval: every exact
triangle vertex lies inside the published pair, and the lower-side predicate
rejects only ray segments strictly below that closed range.
"""

from __future__ import annotations

import importlib.util
import math
import struct
from dataclasses import dataclass
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SHADER = REPO / "godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl"
MAP = REPO / "godot_project/assets/maps/game_map_y8.a3d"
GEOMETRY_GATE = REPO / "scripts/fl4359_geometry_parity_gate.py"

# Product binding 62 is indexed in final-record order: four subcell slots per
# ANSI cell.  Diagnostic record 15467 is therefore ANSI cell 3866, slot 3.
# With 180 ANSI columns, the matching VSB subcell is (86*2+1, 21*2+1).
SUBCELL = (173, 43)
EXTENT = (360, 226)
NEAR_DEPTH = 0.10000000149011612
FAR_DEPTH = 512.0
RAY_EPSILON = 9.999999974752427e-07
FORWARD = (0.5781494379043579, 0.01763126067817211, 0.8157404065132141)
RIGHT = (-0.8158672451972961, 0.0, 0.5782393217086792)
UP = (-0.01019508857280016, 0.9998445510864258, -0.014384767971932888)
TAN_HALF_FOV = (0.8829701542854309, 0.554309070110321)
TILE_CELLS = 8
MAXIMUM_FINE_STEPS = 2048


def _load_heightfield_class():
    """Load the existing byte-parity A3D reader without adding a second parser."""

    spec = importlib.util.spec_from_file_location("fl4359_geometry_gate", GEOMETRY_GATE)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load the canonical A3D heightfield parser")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Heightfield


def _f32(value: float) -> float:
    """Round one arithmetic result to the float width used by the shader."""

    return struct.unpack("<f", struct.pack("<f", value))[0]


def _add(a: tuple[float, float, float], b: tuple[float, float, float]):
    """Add two vectors while preserving shader-width rounding per component."""

    return tuple(_f32(a[index] + b[index]) for index in range(3))


def _scale(a: tuple[float, float, float], scalar: float):
    """Scale one vector while preserving shader-width rounding per component."""

    return tuple(_f32(value * scalar) for value in a)


def _normalize(a: tuple[float, float, float]):
    """Normalize one vector with the same ordered operations used by GLSL."""

    squared = _f32(_f32(a[0] * a[0]) + _f32(a[1] * a[1]))
    squared = _f32(squared + _f32(a[2] * a[2]))
    length = _f32(math.sqrt(squared))
    return tuple(_f32(value / length) for value in a)


def _subcell_ray(
    subcell: tuple[int, int] = SUBCELL,
) -> tuple[float, float, float]:
    """Rebuild record 15467's perspective ray from the captured push constants."""

    uv_x = _f32(_f32(subcell[0] + 0.5) / float(EXTENT[0]))
    uv_y = _f32(_f32(subcell[1] + 0.5) / float(EXTENT[1]))
    ndc_x = _f32(_f32(uv_x * 2.0) - 1.0)
    ndc_y = _f32(1.0 - _f32(uv_y * 2.0))
    direction = _add(
        _add(
            _normalize(FORWARD),
            _scale(_normalize(RIGHT), _f32(ndc_x * TAN_HALF_FOV[0])),
        ),
        _scale(_normalize(UP), _f32(ndc_y * TAN_HALF_FOV[1])),
    )
    return _normalize(direction)


@dataclass
class WalkResult:
    """Record whether traversal stayed valid plus the boundary that decided it."""

    valid: bool
    hit_depth: float | None
    reason: str
    tile: tuple[int, int] | None = None
    cell: tuple[int, int] | None = None
    step_count: int = 0


class TerrainReplica:
    """CPU control-flow replica of VSB's b82 and b73 terrain traversal."""

    def __init__(self) -> None:
        heightfield_class = _load_heightfield_class()
        self.heightfield = heightfield_class(str(MAP))
        self.dims = (self.heightfield.dims_x, self.heightfield.dims_z)
        self.origin = (self.heightfield.origin_x, self.heightfield.origin_z)
        self.corner = [
            [
                self._published_corner_height(x, z)
                for z in range(self.dims[1] + 1)
            ]
            for x in range(self.dims[0] + 1)
        ]
        finite_heights = [
            value
            for column in self.corner
            for value in column
            if value is not None and math.isfinite(value)
        ]
        self.height_min = min(finite_heights)
        self.height_max = max(finite_heights)
        self.coarse_dims = (
            (self.dims[0] + TILE_CELLS - 1) // TILE_CELLS,
            (self.dims[1] + TILE_CELLS - 1) // TILE_CELLS,
        )

    def _published_corner_height(self, x: int, z: int) -> float:
        """Mirror the loader's zero-filled corners outside authored patches."""

        value = self.heightfield.height(
            self.origin[0] + x,
            self.origin[1] + z,
        )
        return 0.0 if value is None else float(value)

    def _tile_bounds(self, tile: tuple[int, int]) -> tuple[float, float]:
        """Derive the inclusive R=max/G=min pair published in b82."""

        cell_min_x = tile[0] * TILE_CELLS
        cell_min_z = tile[1] * TILE_CELLS
        cell_end_x = min(cell_min_x + TILE_CELLS, self.dims[0])
        cell_end_z = min(cell_min_z + TILE_CELLS, self.dims[1])
        values = [
            self.corner[x][z]
            for x in range(cell_min_x, cell_end_x + 1)
            for z in range(cell_min_z, cell_end_z + 1)
        ]
        if any(value is None or not math.isfinite(value) for value in values):
            raise AssertionError(f"non-finite b73 corner inside tile {tile}")
        numeric = [float(value) for value in values]
        return max(numeric), min(numeric)

    def prove_minimum_bound(self) -> tuple[int, int]:
        """Exhaustively prove the lower skip against every retained b73 corner.

        Both exact triangles in a cell are barycentric interpolations of their
        three vertices. If every vertex is at least ``tile_min``, no point on
        either triangle can lie below it. A linear ray segment is bounded by
        its two endpoint heights, so the shader's strict
        ``max(endpoint_y) < tile_min - epsilon`` predicate leaves a positive
        gap and cannot remove an intersection.
        """

        checked_tiles = 0
        checked_corners = 0
        for tile_x in range(self.coarse_dims[0]):
            for tile_z in range(self.coarse_dims[1]):
                tile = (tile_x, tile_z)
                tile_max, tile_min = self._tile_bounds(tile)
                cell_min_x = tile_x * TILE_CELLS
                cell_min_z = tile_z * TILE_CELLS
                cell_end_x = min(cell_min_x + TILE_CELLS, self.dims[0])
                cell_end_z = min(cell_min_z + TILE_CELLS, self.dims[1])
                corners = [
                    float(self.corner[x][z])
                    for x in range(cell_min_x, cell_end_x + 1)
                    for z in range(cell_min_z, cell_end_z + 1)
                ]
                if min(corners) != tile_min or max(corners) != tile_max:
                    raise AssertionError(f"b82 bound mismatch in tile {tile}")
                epsilon = max(RAY_EPSILON, 1.0e-6)
                below_endpoints = (
                    _f32(tile_min - 4.0 * epsilon),
                    _f32(tile_min - 2.0 * epsilon),
                )
                if not (
                    max(below_endpoints) < tile_min - epsilon
                    and max(below_endpoints) < min(corners)
                ):
                    raise AssertionError(
                        f"lower rejection was not strictly disjoint in tile {tile}"
                    )
                boundary_endpoints = (
                    _f32(tile_min - 0.5 * epsilon),
                    _f32(tile_min - 2.0 * epsilon),
                )
                if max(boundary_endpoints) < tile_min - epsilon:
                    raise AssertionError(
                        f"epsilon did not preserve the boundary in tile {tile}"
                    )
                checked_tiles += 1
                checked_corners += len(corners)
        return checked_tiles, checked_corners

    def _cell_hit(
        self,
        ray: tuple[float, float, float],
        cell: tuple[int, int],
        segment_enter: float,
        segment_exit: float,
    ) -> float | None:
        """Intersect the exact w00-w11 pair VSB uses for one retained cell."""

        x, z = cell
        h00 = float(self.corner[x][z])
        h10 = float(self.corner[x + 1][z])
        h01 = float(self.corner[x][z + 1])
        h11 = float(self.corner[x + 1][z + 1])
        vertices = (
            ((self.origin[0] + x, h00, self.origin[1] + z),
             (self.origin[0] + x + 1.0, h10, self.origin[1] + z),
             (self.origin[0] + x + 1.0, h11, self.origin[1] + z + 1.0)),
            ((self.origin[0] + x, h00, self.origin[1] + z),
             (self.origin[0] + x + 1.0, h11, self.origin[1] + z + 1.0),
             (self.origin[0] + x, h01, self.origin[1] + z + 1.0)),
        )
        best = None
        for vertex_0, vertex_1, vertex_2 in vertices:
            edge_1 = tuple(vertex_1[index] - vertex_0[index] for index in range(3))
            edge_2 = tuple(vertex_2[index] - vertex_0[index] for index in range(3))
            p = (
                ray[1] * edge_2[2] - ray[2] * edge_2[1],
                ray[2] * edge_2[0] - ray[0] * edge_2[2],
                ray[0] * edge_2[1] - ray[1] * edge_2[0],
            )
            determinant = sum(edge_1[index] * p[index] for index in range(3))
            epsilon = max(RAY_EPSILON, 1.0e-7)
            if abs(determinant) <= epsilon:
                continue
            inverse = 1.0 / determinant
            from_vertex = tuple(-vertex_0[index] for index in range(3))
            barycentric_u = sum(from_vertex[index] * p[index] for index in range(3)) * inverse
            if barycentric_u < -epsilon or barycentric_u > 1.0 + epsilon:
                continue
            q = (
                from_vertex[1] * edge_1[2] - from_vertex[2] * edge_1[1],
                from_vertex[2] * edge_1[0] - from_vertex[0] * edge_1[2],
                from_vertex[0] * edge_1[1] - from_vertex[1] * edge_1[0],
            )
            barycentric_v = sum(ray[index] * q[index] for index in range(3)) * inverse
            if barycentric_v < -epsilon or barycentric_u + barycentric_v > 1.0 + epsilon:
                continue
            depth = sum(edge_2[index] * q[index] for index in range(3)) * inverse
            if depth < segment_enter - epsilon or depth > segment_exit:
                continue
            if best is None or depth < best:
                best = depth
        return best

    def walk(
        self,
        corrected: bool,
        subcell: tuple[int, int] = SUBCELL,
        minimum_skip_enabled: bool = False,
    ) -> WalkResult:
        """Run one ray through the boundary and lower-bound measurement arms."""

        ray = _subcell_ray(subcell)
        epsilon = _f32(max(RAY_EPSILON, 1.0e-6))
        enter_depth = _f32(NEAR_DEPTH)
        exit_depth = _f32(FAR_DEPTH)
        for origin_axis, direction_axis, lower, upper in (
            (0.0, ray[0], self.origin[0], self.origin[0] + self.dims[0]),
            (0.0, ray[2], self.origin[1], self.origin[1] + self.dims[1]),
        ):
            if abs(direction_axis) <= epsilon:
                if origin_axis < lower or origin_axis > upper:
                    return WalkResult(True, None, "valid XZ miss")
                continue
            depth_a = _f32(_f32(lower - origin_axis) / direction_axis)
            depth_b = _f32(_f32(upper - origin_axis) / direction_axis)
            enter_depth = _f32(max(enter_depth, min(depth_a, depth_b)))
            exit_depth = _f32(min(exit_depth, max(depth_a, depth_b)))
        depth_at_min = _f32(self.height_min / ray[1])
        depth_at_max = _f32(self.height_max / ray[1])
        enter_depth = _f32(max(enter_depth, min(depth_at_min, depth_at_max)))
        exit_depth = _f32(min(exit_depth, max(depth_at_min, depth_at_max)))
        if exit_depth < enter_depth:
            return WalkResult(True, None, "valid height-band miss")

        coarse_size = _f32(float(TILE_CELLS))
        enter_x = _f32(ray[0] * enter_depth)
        enter_z = _f32(ray[2] * enter_depth)
        tile = [
            min(
                max(int(math.floor(_f32(enter_x - self.origin[0]) / coarse_size)), 0),
                self.coarse_dims[0] - 1,
            ),
            min(
                max(int(math.floor(_f32(enter_z - self.origin[1]) / coarse_size)), 0),
                self.coarse_dims[1] - 1,
            ),
        ]
        tile_step = [1 if ray[0] >= 0.0 else -1, 1 if ray[2] >= 0.0 else -1]
        tile_delta = [
            _f32(coarse_size / abs(ray[0])) if abs(ray[0]) > epsilon else math.inf,
            _f32(coarse_size / abs(ray[2])) if abs(ray[2]) > epsilon else math.inf,
        ]
        boundary_cell = [
            min((tile[0] + 1) * TILE_CELLS, self.dims[0])
            if tile_step[0] > 0 else tile[0] * TILE_CELLS,
            min((tile[1] + 1) * TILE_CELLS, self.dims[1])
            if tile_step[1] > 0 else tile[1] * TILE_CELLS,
        ]
        next_tile = [
            _f32(max(_f32(self.origin[0] + boundary_cell[0]) / ray[0], enter_depth))
            if abs(ray[0]) > epsilon else math.inf,
            _f32(max(_f32(self.origin[1] + boundary_cell[1]) / ray[2], enter_depth))
            if abs(ray[2]) > epsilon else math.inf,
        ]
        tile_enter = enter_depth
        fine_steps = 0
        for _ in range(2048):
            if (
                tile[0] < 0
                or tile[1] < 0
                or tile[0] >= self.coarse_dims[0]
                or tile[1] >= self.coarse_dims[1]
            ):
                return WalkResult(False, None, "coarse cell escaped interval", tuple(tile), step_count=fine_steps)
            tile_exit = _f32(min(exit_depth, next_tile[0], next_tile[1]))
            enter_y = _f32(ray[1] * tile_enter)
            exit_y = _f32(ray[1] * tile_exit)
            tile_max, tile_min = self._tile_bounds(tuple(tile))
            tile_proven_above = min(enter_y, exit_y) > tile_max + epsilon
            fine_depth_epsilon = max(epsilon, 1.0e-5)
            lower_guard_enter_y = _f32(
                ray[1] * _f32(tile_enter - fine_depth_epsilon)
            )
            tile_proven_below = (
                minimum_skip_enabled
                and max(lower_guard_enter_y, exit_y) < tile_min - epsilon
            )
            if not tile_proven_above and not tile_proven_below:
                result = self._walk_tile(
                    ray,
                    tuple(tile),
                    tile_enter,
                    tile_exit,
                    epsilon,
                    fine_steps,
                    corrected,
                )
                fine_steps = result.step_count
                if not result.valid or result.hit_depth is not None:
                    return result
            if tile_exit >= exit_depth - epsilon:
                return WalkResult(True, None, "coarse interval exhausted", tuple(tile), step_count=fine_steps)
            cross_x = next_tile[0] <= next_tile[1] + epsilon
            cross_z = next_tile[1] <= next_tile[0] + epsilon
            tile_enter = tile_exit
            if cross_x:
                tile[0] += tile_step[0]
                if corrected:
                    boundary_cell_x = (
                        min((tile[0] + 1) * TILE_CELLS, self.dims[0])
                        if tile_step[0] > 0
                        else tile[0] * TILE_CELLS
                    )
                    boundary_x = _f32(self.origin[0] + boundary_cell_x)
                    next_tile[0] = _f32(
                        max(_f32(boundary_x / ray[0]), tile_enter)
                    )
                else:
                    next_tile[0] = _f32(next_tile[0] + tile_delta[0])
            if cross_z:
                tile[1] += tile_step[1]
                if corrected:
                    boundary_cell_z = (
                        min((tile[1] + 1) * TILE_CELLS, self.dims[1])
                        if tile_step[1] > 0
                        else tile[1] * TILE_CELLS
                    )
                    boundary_z = _f32(self.origin[1] + boundary_cell_z)
                    next_tile[1] = _f32(
                        max(_f32(boundary_z / ray[2]), tile_enter)
                    )
                else:
                    next_tile[1] = _f32(next_tile[1] + tile_delta[1])
        return WalkResult(False, None, "coarse step budget exhausted", tuple(tile), step_count=fine_steps)

    def _walk_tile(
        self,
        ray: tuple[float, float, float],
        tile: tuple[int, int],
        tile_enter: float,
        tile_exit: float,
        epsilon: float,
        fine_steps: int,
        corrected: bool,
    ) -> WalkResult:
        """Reproduce the handoff whose out-of-tile boundary is under review."""

        cell_min = (tile[0] * TILE_CELLS, tile[1] * TILE_CELLS)
        cell_end = (
            min(cell_min[0] + TILE_CELLS, self.dims[0]),
            min(cell_min[1] + TILE_CELLS, self.dims[1]),
        )
        enter_grid = (
            _f32(min(max(_f32(_f32(ray[0] * tile_enter) - self.origin[0]), cell_min[0]), cell_end[0])),
            _f32(min(max(_f32(_f32(ray[2] * tile_enter) - self.origin[1]), cell_min[1]), cell_end[1])),
        )
        cell = [
            min(max(int(math.floor(enter_grid[0])), cell_min[0]), cell_end[0] - 1),
            min(max(int(math.floor(enter_grid[1])), cell_min[1]), cell_end[1] - 1),
        ]
        cell_step = [1 if ray[0] >= 0.0 else -1, 1 if ray[2] >= 0.0 else -1]
        delta = [
            _f32(1.0 / abs(ray[0])) if abs(ray[0]) > epsilon else math.inf,
            _f32(1.0 / abs(ray[2])) if abs(ray[2]) > epsilon else math.inf,
        ]
        boundary = [
            _f32(self.origin[0] + cell[0] + (1 if cell_step[0] > 0 else 0)),
            _f32(self.origin[1] + cell[1] + (1 if cell_step[1] > 0 else 0)),
        ]
        next_depth = [
            _f32(max(_f32(boundary[0] / ray[0]), tile_enter)) if abs(ray[0]) > epsilon else math.inf,
            _f32(max(_f32(boundary[1] / ray[2]), tile_enter)) if abs(ray[2]) > epsilon else math.inf,
        ]
        segment_enter = tile_enter
        for _ in range(TILE_CELLS * 2 + 1):
            if fine_steps >= MAXIMUM_FINE_STEPS:
                return WalkResult(False, None, "fine step budget exhausted", tile, tuple(cell), fine_steps)
            outside = (
                cell[0] < cell_min[0]
                or cell[1] < cell_min[1]
                or cell[0] >= cell_end[0]
                or cell[1] >= cell_end[1]
            )
            if outside:
                return WalkResult(
                    False,
                    None,
                    (
                        "fine cell escaped tile "
                        f"segment_enter={segment_enter:.9f} "
                        f"tile_exit={tile_exit:.9f} "
                        f"next=({next_depth[0]:.9f},{next_depth[1]:.9f})"
                    ),
                    tile,
                    tuple(cell),
                    fine_steps,
                )
            fine_steps += 1
            segment_exit = _f32(min(tile_exit, next_depth[0], next_depth[1]))
            cross_x = next_depth[0] <= next_depth[1] + epsilon
            cross_z = next_depth[1] <= next_depth[0] + epsilon
            hit = self._cell_hit(ray, tuple(cell), segment_enter, segment_exit)
            if hit is not None:
                return WalkResult(True, hit, "terrain hit", tile, tuple(cell), fine_steps)
            if segment_exit >= tile_exit - epsilon:
                return WalkResult(True, None, "fine interval exhausted", tile, tuple(cell), fine_steps)
            segment_enter = segment_exit
            if cross_x:
                cell[0] += cell_step[0]
                next_depth[0] = _f32(next_depth[0] + delta[0])
                if corrected and cell[0] == (
                    cell_end[0] - 1 if cell_step[0] > 0 else cell_min[0]
                ):
                    terminal_x = _f32(
                        self.origin[0]
                        + (cell_end[0] if cell_step[0] > 0 else cell_min[0])
                    )
                    next_depth[0] = _f32(max(_f32(terminal_x / ray[0]), segment_enter))
            if cross_z:
                cell[1] += cell_step[1]
                next_depth[1] = _f32(next_depth[1] + delta[1])
                if corrected and cell[1] == (
                    cell_end[1] - 1 if cell_step[1] > 0 else cell_min[1]
                ):
                    terminal_z = _f32(
                        self.origin[1]
                        + (cell_end[1] if cell_step[1] > 0 else cell_min[1])
                    )
                    next_depth[1] = _f32(max(_f32(terminal_z / ray[2]), segment_enter))
        return WalkResult(False, None, "fine local step bound exhausted", tile, tuple(cell), fine_steps)


def main() -> int:
    """Require the pinned handoff plus the live two-sided b82 contract."""

    replica = TerrainReplica()
    before = replica.walk(corrected=False)
    after = replica.walk(corrected=True)
    minimum_arm = replica.walk(corrected=True, minimum_skip_enabled=True)
    print(f"before={before}")
    print(f"after={after}")
    print(f"minimum_arm={minimum_arm}")
    # The only other fail-visible ANSI cell was (64, 57).  Product's log did
    # not identify its quarter-cell slot, so replay all four candidate rays and
    # keep their terrain disposition visible beside the pinned 0x0104 case.
    secondary = {}
    for slot in range(4):
        secondary_subcell = (
            64 * 2 + (slot & 1),
            57 * 2 + (slot >> 1),
        )
        secondary[slot] = (
            replica.walk(corrected=False, subcell=secondary_subcell),
            replica.walk(corrected=True, subcell=secondary_subcell),
            replica.walk(
                corrected=True,
                subcell=secondary_subcell,
                minimum_skip_enabled=True,
            ),
        )
    print(f"secondary_ansi_64_57={secondary}")
    checked_tiles, checked_corners = replica.prove_minimum_bound()
    print(
        "minimum_bound_witness="
        f"tiles:{checked_tiles} inclusive_corners:{checked_corners}"
    )
    source = SHADER.read_text(encoding="utf-8")
    correction_tokens = (
        "terminal_boundary_cell_x",
        "terminal_boundary_cell_z",
        "next_boundary_cell_x",
        "next_boundary_cell_z",
    )
    failures: list[str] = []
    if before.valid:
        failures.append("the pinned pre-fix replica did not reproduce invalid evidence")
    if not after.valid:
        failures.append("the corrected replica did not preserve a valid boundary exit")
    if after.hit_depth is None:
        failures.append("the corrected replica did not retain record 15467's terrain hit")
    if (
        minimum_arm.valid != after.valid
        or minimum_arm.hit_depth != after.hit_depth
    ):
        failures.append("the b82 minimum arm changed the pinned terrain result")
    for slot, (_old, maximum_arm, lower_arm) in secondary.items():
        if (
            lower_arm.valid != maximum_arm.valid
            or lower_arm.hit_depth != maximum_arm.hit_depth
        ):
            failures.append(
                f"the b82 minimum arm changed secondary slot {slot}"
            )
    if any(token not in source for token in correction_tokens):
        failures.append("the shader does not contain the boundary-exhaustion correction")
    if "heightfield_coarse_bounds_tex, tile, 0" not in source:
        failures.append("the shader does not read the b82 bounds pair")
    if (
        "max(lower_guard_enter_y, exit_y) < tile_min - epsilon"
        not in source
    ):
        failures.append("the shader lower skip does not shrink by epsilon")
    if "tile_enter - fine_depth_epsilon" not in source:
        failures.append("the shader lower skip does not preserve b73's entry tolerance")
    if "if (!tile_proven_above && !tile_proven_below)" not in source:
        failures.append("the shader does not preserve b73 for overlapping ranges")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print(
        "PASS: b82 bounds reject only disjoint intervals and preserve the "
        "exact shared-boundary terrain hit"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
