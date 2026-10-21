#!/usr/bin/env python3
"""Retag above-water material-2 cells beside canonical A3D water.

Dry-run is the default. ``--write`` atomically replaces the input map after
computing the complete result in memory. Only the low material byte of selected
patch visual words may change; the high visual byte and every other A3D byte
remain copied from the input.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import tempfile


REPO_ROOT = Path(__file__).resolve().parents[2]
A3D_FORMAT_PATH = (
    REPO_ROOT / "addons" / "io_asciicker" / "scene" / "a3d_format.py"
)
PATCH_BYTES = 188
PATCH_VISUAL_OFFSET = 8
MATERIAL_MASK = 0x00FF
SOURCE_MATERIAL = 2
GRASS_MATERIALS = frozenset((0, 1, 3))
VISIBLE_WATER_MATERIAL = 140
# Product WATER_SURFACE_Y is 49/16 world units. Heights sampled halfway
# between A3D height vertices can be quarter-source-unit values, so compare in
# exact quarter-source units and avoid a float threshold disagreement.
WATER_SURFACE_HEIGHT_QUARTERS = 49 * 4
CARDINAL_OFFSETS = ((0, -1), (1, 0), (0, 1), (-1, 0))
# The accepted P3 shoreline pose is world-fixed. These two 128 x 128 squares
# are diagnostic neighborhoods, not camera-frustum claims: each is centered on
# the canonical player/camera XZ and extends exactly eight A3D patches (64
# world units) in every cardinal direction. Counting the same immutable retag
# plan in both squares distinguishes P3-local coverage from unrelated coast.
P3_NEIGHBORHOOD_HALF_EXTENT = 64.0
P3_NEIGHBORHOODS = {
    "player": (-12.257, 108.677),
    "camera": (11.616, 209.902),
}
# FL-4216: The repository map mutation is one immutable-source transaction. Once
# the source snapshot has produced its pinned successor, newly retagged grass
# cells must never become voters for a second pass. A third hash at this exact
# path is neither the reviewed source nor its reviewed result and fails closed.
SHIPPED_MAP_PATH = (
    REPO_ROOT / "godot_project" / "assets" / "maps" / "game_map_y8.a3d"
).resolve()
SHIPPED_SOURCE_SHA256 = (
    "54c1373022eb329d7930cb0d8999459ca9ddad401ca4898d7707044110be75d7"
)
SHIPPED_SUCCESSOR_SHA256 = (
    "22b9c3b9af9b85e8cfff5d8232ca35dcf19da6c47a5fe3eaf6cadcc851d11006"
)
SHIPPED_SOURCE_SELECTED_CELLS_SHA256 = (
    "36f4c6f78d85413b6ecd09bd53596a001843ffbfa8aa6a0c478ea7afad1707c8"
)
SHIPPED_SOURCE_SELECTED_CELL_COUNT = 1166
EMPTY_SELECTION_SHA256 = hashlib.sha256(b"").hexdigest()


def _load_a3d_format():
    spec = importlib.util.spec_from_file_location("shore_a3d_format", A3D_FORMAT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load A3D codec: {A3D_FORMAT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fmt = _load_a3d_format()


@dataclass(frozen=True)
class Cell:
    patch_index: int
    patch_x: int
    patch_y: int
    local_x: int
    local_y: int
    visual: int
    max_height_quarters: int

    @property
    def coord(self) -> tuple[int, int]:
        return (
            self.patch_x * fmt.VISUAL_CELLS + self.local_x,
            self.patch_y * fmt.VISUAL_CELLS + self.local_y,
        )

    @property
    def material(self) -> int:
        return self.visual & MATERIAL_MASK

    @property
    def is_water(self) -> bool:
        return (
            self.material == VISIBLE_WATER_MATERIAL
            or self.max_height_quarters <= WATER_SURFACE_HEIGHT_QUARTERS
        )


@dataclass(frozen=True)
class Retag:
    cell: Cell
    replacement_material: int


@dataclass(frozen=True)
class MapFrame:
    min_patch_x: int
    min_patch_y: int
    patch_width: int
    patch_height: int

    @property
    def center_offset(self) -> tuple[float, float]:
        # Mirrors A3DMapLoader._center_offset(): patch coordinates become
        # source-cell coordinates only after multiplying the complete term.
        return (
            (self.min_patch_x + self.patch_width / 2.0) * fmt.VISUAL_CELLS,
            (self.min_patch_y + self.patch_height / 2.0) * fmt.VISUAL_CELLS,
        )


def _visual_corner_height_quarters(patch, vx: int, vy: int) -> int:
    """Match A3DMapLoader._height_at at an integer visual-grid corner."""
    hx = vx // 2
    hy = vy // 2
    odd_x = vx & 1
    odd_y = vy & 1
    x1 = min(hx + 1, fmt.HEIGHT_CELLS)
    y1 = min(hy + 1, fmt.HEIGHT_CELLS)
    h00 = int(patch.height[hy][hx])
    if not odd_x and not odd_y:
        return h00 * 4
    h10 = int(patch.height[hy][x1])
    if odd_x and not odd_y:
        return (h00 + h10) * 2
    h01 = int(patch.height[y1][hx])
    if not odd_x:
        return (h00 + h01) * 2
    h11 = int(patch.height[y1][x1])
    return h00 + h10 + h01 + h11


def _patch_local_cell_max_height_quarters(patch, x: int, y: int) -> int:
    return max(
        _visual_corner_height_quarters(patch, x, y),
        _visual_corner_height_quarters(patch, x + 1, y),
        _visual_corner_height_quarters(patch, x, y + 1),
        _visual_corner_height_quarters(patch, x + 1, y + 1),
    )


def _map_frame(patches: list[object]) -> MapFrame:
    if not patches:
        raise ValueError("A3D map has no terrain patches")
    patch_xs = [int(patch.x) for patch in patches]
    patch_ys = [int(patch.y) for patch in patches]
    min_x = min(patch_xs)
    min_y = min(patch_ys)
    return MapFrame(
        min_patch_x=min_x,
        min_patch_y=min_y,
        patch_width=max(patch_xs) - min_x + 1,
        patch_height=max(patch_ys) - min_y + 1,
    )


def _authoritative_corner_height_quarters(
    patches_by_coord: dict[tuple[int, int], object],
    frame: MapFrame,
    grid_x: int,
    grid_y: int,
) -> int:
    """Match build_authoritative_corner_height_image's global owner lattice."""
    max_grid_x = frame.patch_width * fmt.VISUAL_CELLS
    max_grid_y = frame.patch_height * fmt.VISUAL_CELLS
    if not (0 <= grid_x <= max_grid_x and 0 <= grid_y <= max_grid_y):
        raise ValueError(f"corner outside authoritative lattice: {(grid_x, grid_y)}")

    patch_x = frame.min_patch_x + grid_x // fmt.VISUAL_CELLS
    patch_y = frame.min_patch_y + grid_y // fmt.VISUAL_CELLS
    local_x = grid_x % fmt.VISUAL_CELLS
    local_y = grid_y % fmt.VISUAL_CELLS
    # Interior shared corners belong to the next/down-right patch. Only the
    # far map edges borrow vx/vy=8 from the preceding patch.
    if grid_x == max_grid_x:
        patch_x -= 1
        local_x = fmt.VISUAL_CELLS
    if grid_y == max_grid_y:
        patch_y -= 1
        local_y = fmt.VISUAL_CELLS
    patch = patches_by_coord.get((patch_x, patch_y))
    if patch is None:
        # The runtime initializes missing rectangular-lattice corners to zero.
        return 0
    return _visual_corner_height_quarters(patch, local_x, local_y)


def _authoritative_cell_max_height_quarters(
    patches_by_coord: dict[tuple[int, int], object],
    frame: MapFrame,
    patch_x: int,
    patch_y: int,
    local_x: int,
    local_y: int,
) -> int:
    grid_x = (patch_x - frame.min_patch_x) * fmt.VISUAL_CELLS + local_x
    grid_y = (patch_y - frame.min_patch_y) * fmt.VISUAL_CELLS + local_y
    return max(
        _authoritative_corner_height_quarters(
            patches_by_coord, frame, grid_x, grid_y
        ),
        _authoritative_corner_height_quarters(
            patches_by_coord, frame, grid_x + 1, grid_y
        ),
        _authoritative_corner_height_quarters(
            patches_by_coord, frame, grid_x, grid_y + 1
        ),
        _authoritative_corner_height_quarters(
            patches_by_coord, frame, grid_x + 1, grid_y + 1
        ),
    )


def _shared_edge_height_mismatches(
    patches_by_coord: dict[tuple[int, int], object],
) -> int:
    mismatches = 0
    edge = fmt.HEIGHT_CELLS
    for (patch_x, patch_y), patch in patches_by_coord.items():
        right = patches_by_coord.get((patch_x + 1, patch_y))
        if right is not None:
            mismatches += sum(
                int(patch.height[row][edge]) != int(right.height[row][0])
                for row in range(edge + 1)
            )
        down = patches_by_coord.get((patch_x, patch_y + 1))
        if down is not None:
            mismatches += sum(
                int(patch.height[edge][column]) != int(down.height[0][column])
                for column in range(edge + 1)
            )
    return mismatches


def _read_cells(
    path: Path,
) -> tuple[
    bytes,
    object,
    list[object],
    dict[tuple[int, int], Cell],
    MapFrame,
    dict[str, int],
]:
    raw = path.read_bytes()
    with path.open("rb") as handle:
        header = fmt.A3DHeader.from_file(handle)
        if header.header_size != fmt.A3DHeader.SIZE:
            raise ValueError(
                f"unsupported A3D header size {header.header_size}; expected {fmt.A3DHeader.SIZE}"
            )
        patches = [fmt.A3DPatch.from_file(handle) for _ in range(header.num_patches)]
    expected_prefix = fmt.A3DHeader.SIZE + header.num_patches * PATCH_BYTES
    if len(raw) < expected_prefix:
        raise ValueError("A3D file ends inside the terrain patch prefix")

    frame = _map_frame(patches)
    patches_by_coord: dict[tuple[int, int], object] = {}
    for patch in patches:
        coord = (int(patch.x), int(patch.y))
        if coord in patches_by_coord:
            raise ValueError(f"duplicate A3D patch coordinate {coord}")
        patches_by_coord[coord] = patch

    cells: dict[tuple[int, int], Cell] = {}
    height_classification_disagreements = 0
    material_2_height_classification_disagreements = 0
    for patch_index, patch in enumerate(patches):
        for local_y in range(fmt.VISUAL_CELLS):
            for local_x in range(fmt.VISUAL_CELLS):
                authoritative_max = _authoritative_cell_max_height_quarters(
                    patches_by_coord,
                    frame,
                    int(patch.x),
                    int(patch.y),
                    local_x,
                    local_y,
                )
                local_max = _patch_local_cell_max_height_quarters(
                    patch, local_x, local_y
                )
                height_disagrees = (
                    authoritative_max <= WATER_SURFACE_HEIGHT_QUARTERS
                ) != (local_max <= WATER_SURFACE_HEIGHT_QUARTERS)
                if height_disagrees:
                    height_classification_disagreements += 1
                    if int(patch.visual[local_y][local_x]) & MATERIAL_MASK == SOURCE_MATERIAL:
                        material_2_height_classification_disagreements += 1
                cell = Cell(
                    patch_index=patch_index,
                    patch_x=int(patch.x),
                    patch_y=int(patch.y),
                    local_x=local_x,
                    local_y=local_y,
                    visual=int(patch.visual[local_y][local_x]),
                    max_height_quarters=authoritative_max,
                )
                if cell.coord in cells:
                    raise ValueError(f"duplicate A3D visual cell coordinate {cell.coord}")
                cells[cell.coord] = cell
    corner_owner_audit = {
        "shared_edge_height_mismatch_count": _shared_edge_height_mismatches(
            patches_by_coord
        ),
        "patch_local_vs_authoritative_height_water_classification_count": (
            height_classification_disagreements
        ),
        "material_2_height_water_classification_disagreement_count": (
            material_2_height_classification_disagreements
        ),
    }
    return raw, header, patches, cells, frame, corner_owner_audit


def _strict_grass_majority(
    cell: Cell, cells: dict[tuple[int, int], Cell]
) -> int | None:
    counts: Counter[int] = Counter()
    cx, cy = cell.coord
    for dx, dy in CARDINAL_OFFSETS:
        neighbor = cells.get((cx + dx, cy + dy))
        if (
            neighbor is not None
            and not neighbor.is_water
            and neighbor.material in GRASS_MATERIALS
        ):
            counts[neighbor.material] += 1
    if not counts:
        return None
    winner, winner_count = min(
        counts.items(), key=lambda item: (-item[1], item[0])
    )
    if winner_count * 2 <= sum(counts.values()):
        return None
    return winner


def _is_shore_candidate(
    cell: Cell, cells: dict[tuple[int, int], Cell]
) -> bool:
    cx, cy = cell.coord
    return any(
        (neighbor := cells.get((cx + dx, cy + dy))) is not None
        and neighbor.is_water
        for dx, dy in CARDINAL_OFFSETS
    )


def plan_retags(cells: dict[tuple[int, int], Cell]) -> tuple[list[Retag], dict[str, int]]:
    retags: list[Retag] = []
    stats = {
        "material_2_cells": 0,
        "material_2_above_water_cells": 0,
        "shore_candidates": 0,
        "no_grass_majority": 0,
    }
    for coord in sorted(cells):
        cell = cells[coord]
        if cell.material != SOURCE_MATERIAL:
            continue
        stats["material_2_cells"] += 1
        if cell.is_water:
            continue
        stats["material_2_above_water_cells"] += 1
        if not _is_shore_candidate(cell, cells):
            continue
        stats["shore_candidates"] += 1
        replacement = _strict_grass_majority(cell, cells)
        if replacement is None:
            stats["no_grass_majority"] += 1
            continue
        retags.append(Retag(cell=cell, replacement_material=replacement))
    return retags, stats


def _visual_low_byte_offset(retag: Retag) -> int:
    visual_index = retag.cell.local_y * fmt.VISUAL_CELLS + retag.cell.local_x
    return (
        fmt.A3DHeader.SIZE
        + retag.cell.patch_index * PATCH_BYTES
        + PATCH_VISUAL_OFFSET
        + visual_index * 2
    )


def apply_retags(raw: bytes, retags: list[Retag]) -> bytes:
    result = bytearray(raw)
    changed_offsets: set[int] = set()
    for retag in retags:
        offset = _visual_low_byte_offset(retag)
        if offset in changed_offsets:
            raise ValueError(f"duplicate material-byte mutation at offset {offset}")
        changed_offsets.add(offset)
        if result[offset] != retag.cell.material:
            raise ValueError(
                f"material byte at offset {offset} changed during planning: "
                f"expected {retag.cell.material}, found {result[offset]}"
            )
        result[offset] = retag.replacement_material

    actual_differences = [
        index for index, (before, after) in enumerate(zip(raw, result)) if before != after
    ]
    if actual_differences != sorted(changed_offsets):
        raise AssertionError("retag changed bytes outside the selected material-byte set")
    return bytes(result)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _input_transaction_state(path: Path, input_sha256: str) -> str:
    if path.resolve() != SHIPPED_MAP_PATH:
        return "generic"
    if input_sha256 == SHIPPED_SOURCE_SHA256:
        return "source_snapshot"
    if input_sha256 == SHIPPED_SUCCESSOR_SHA256:
        return "already_applied"
    raise ValueError(
        "shipped map hash is outside the immutable one-pass contract: "
        f"expected source {SHIPPED_SOURCE_SHA256} or successor "
        f"{SHIPPED_SUCCESSOR_SHA256}, found {input_sha256}"
    )


def _shipped_transaction_contract() -> dict[str, object]:
    return {
        "owner": "immutable_source_snapshot_one_pass",
        "source_sha256": SHIPPED_SOURCE_SHA256,
        "successor_sha256": SHIPPED_SUCCESSOR_SHA256,
        "source_selected_cells_sha256": SHIPPED_SOURCE_SELECTED_CELLS_SHA256,
        "source_selected_cell_count": SHIPPED_SOURCE_SELECTED_CELL_COUNT,
    }


def _already_applied_result(
    path: Path, raw: bytes, input_sha256: str
) -> tuple[bytes, dict[str, object]]:
    report: dict[str, object] = {
        "schema": "asciicker.a3d_shore_material_retag.v3",
        "mode": "dry_run",
        "transaction_state": "already_applied",
        "write_performed": False,
        "no_op_reason": "recognized_shipped_successor_is_terminal",
        "input": str(path),
        "input_sha256": input_sha256,
        "prospective_output_sha256": input_sha256,
        "byte_count": len(raw),
        "selected_cell_count": 0,
        "changed_byte_count": 0,
        "replacement_counts": {},
        "selected_cells_sha256": EMPTY_SELECTION_SHA256,
        "selected_cell_first": [],
        "selected_cell_last": [],
        "shipped_transaction_contract": _shipped_transaction_contract(),
        "invariants": {
            "only_low_material_bytes_change": True,
            "high_visual_bytes_preserved": True,
            "patch_order_preserved": True,
            "height_diag_tail_preserved": True,
            "output_byte_count_matches_input": True,
            "successor_never_becomes_a_new_voter_snapshot": True,
        },
    }
    return raw, report


def _source_cell_bbox(coords: set[tuple[int, int]]) -> dict[str, list[int]] | None:
    if not coords:
        return None
    xs = [coord[0] for coord in coords]
    ys = [coord[1] for coord in coords]
    return {
        "min_inclusive": [min(xs), min(ys)],
        "max_inclusive": [max(xs), max(ys)],
        "cell_extent_max_exclusive": [max(xs) + 1, max(ys) + 1],
    }


def _godot_world_cell_bbox(
    coords: set[tuple[int, int]], frame: MapFrame
) -> dict[str, list[float]] | None:
    source_bbox = _source_cell_bbox(coords)
    if source_bbox is None:
        return None
    offset_x, offset_y = frame.center_offset
    source_min = source_bbox["min_inclusive"]
    source_max = source_bbox["cell_extent_max_exclusive"]
    return {
        "cell_extent_min_inclusive_xz": [
            float(source_min[0]) - offset_x,
            float(source_min[1]) - offset_y,
        ],
        "cell_extent_max_exclusive_xz": [
            float(source_max[0]) - offset_x,
            float(source_max[1]) - offset_y,
        ],
    }


def _connected_components(
    coords: set[tuple[int, int]],
) -> list[tuple[tuple[int, int], ...]]:
    unvisited = set(coords)
    components: list[tuple[tuple[int, int], ...]] = []
    while unvisited:
        seed = min(unvisited)
        unvisited.remove(seed)
        stack = [seed]
        component: list[tuple[int, int]] = []
        while stack:
            coord = stack.pop()
            component.append(coord)
            cx, cy = coord
            for dx, dy in CARDINAL_OFFSETS:
                neighbor = (cx + dx, cy + dy)
                if neighbor in unvisited:
                    unvisited.remove(neighbor)
                    stack.append(neighbor)
        components.append(tuple(sorted(component)))
    components.sort(key=lambda component: (-len(component), component[0]))
    return components


def _component_report(
    components: list[tuple[tuple[int, int], ...]], frame: MapFrame
) -> dict[str, object]:
    sizes = [len(component) for component in components]
    largest = set(components[0]) if components else set()
    return {
        "adjacency": "four_neighbor_source_cells",
        "component_count": len(components),
        "singleton_component_count": sum(size == 1 for size in sizes),
        "smallest_component_size": min(sizes) if sizes else 0,
        "largest_component_size": max(sizes) if sizes else 0,
        "mean_component_size": (
            round(sum(sizes) / len(sizes), 6) if sizes else 0.0
        ),
        "largest_component_sizes_first_12": sizes[:12],
        "largest_component_source_cell_bbox": _source_cell_bbox(largest),
        "largest_component_godot_world_bbox": _godot_world_cell_bbox(
            largest, frame
        ),
    }


def _selection_stage_counts(
    cells: dict[tuple[int, int], Cell],
    selected_coords: set[tuple[int, int]],
    include_coord,
) -> dict[str, int]:
    counts = {
        "map_cell_count": 0,
        "material_2_cell_count": 0,
        "material_2_above_water_cell_count": 0,
        "shore_candidate_count": 0,
        "selected_cell_count": 0,
    }
    for coord, cell in cells.items():
        if not include_coord(coord):
            continue
        counts["map_cell_count"] += 1
        if cell.material != SOURCE_MATERIAL:
            continue
        counts["material_2_cell_count"] += 1
        if cell.is_water:
            continue
        counts["material_2_above_water_cell_count"] += 1
        if not _is_shore_candidate(cell, cells):
            continue
        counts["shore_candidate_count"] += 1
        if coord in selected_coords:
            counts["selected_cell_count"] += 1
    return counts


def _p3_neighborhood_report(
    cells: dict[tuple[int, int], Cell],
    selected_coords: set[tuple[int, int]],
    components: list[tuple[tuple[int, int], ...]],
    frame: MapFrame,
) -> dict[str, object]:
    offset_x, offset_y = frame.center_offset
    component_by_coord = {
        coord: component_index
        for component_index, component in enumerate(components)
        for coord in component
    }
    neighborhoods: dict[str, object] = {}
    for name, center in P3_NEIGHBORHOODS.items():
        min_x = center[0] - P3_NEIGHBORHOOD_HALF_EXTENT
        min_z = center[1] - P3_NEIGHBORHOOD_HALF_EXTENT
        max_x = center[0] + P3_NEIGHBORHOOD_HALF_EXTENT
        max_z = center[1] + P3_NEIGHBORHOOD_HALF_EXTENT

        def includes(coord: tuple[int, int]) -> bool:
            world_x = coord[0] + 0.5 - offset_x
            world_z = coord[1] + 0.5 - offset_y
            return min_x <= world_x <= max_x and min_z <= world_z <= max_z

        stage_counts = _selection_stage_counts(cells, selected_coords, includes)
        touched_components = {
            component_by_coord[coord]
            for coord in selected_coords
            if includes(coord)
        }
        neighborhoods[name] = {
            "center_godot_world_xz": [center[0], center[1]],
            "bbox_godot_world_xz_closed": {
                "min": [min_x, min_z],
                "max": [max_x, max_z],
            },
            **stage_counts,
            "selected_component_count": len(touched_components),
        }
    return {
        "definition": (
            "closed 128x128 Godot-world XZ square centered on the canonical "
            "P3 player/camera coordinate; selection tests each source cell's "
            "unit-square center; half-extent=64 world units=8 A3D patches; "
            "diagnostic neighborhood only, not a frustum/visibility claim"
        ),
        "half_extent_world_units": P3_NEIGHBORHOOD_HALF_EXTENT,
        "neighborhoods": neighborhoods,
    }


def _map_frame_report(frame: MapFrame) -> dict[str, object]:
    source_min_x = frame.min_patch_x * fmt.VISUAL_CELLS
    source_min_y = frame.min_patch_y * fmt.VISUAL_CELLS
    source_max_x = source_min_x + frame.patch_width * fmt.VISUAL_CELLS
    source_max_y = source_min_y + frame.patch_height * fmt.VISUAL_CELLS
    offset_x, offset_y = frame.center_offset
    return {
        "patch_bbox": {
            "min_inclusive": [frame.min_patch_x, frame.min_patch_y],
            "max_inclusive": [
                frame.min_patch_x + frame.patch_width - 1,
                frame.min_patch_y + frame.patch_height - 1,
            ],
        },
        "source_cell_bbox": {
            "min_inclusive": [source_min_x, source_min_y],
            "max_exclusive": [source_max_x, source_max_y],
        },
        "center_offset_source_to_godot_xz": [offset_x, offset_y],
        "godot_world_cell_bbox": {
            "min_inclusive_xz": [source_min_x - offset_x, source_min_y - offset_y],
            "max_exclusive_xz": [source_max_x - offset_x, source_max_y - offset_y],
        },
    }


def build_result(path: Path) -> tuple[bytes, dict[str, object]]:
    path = path.resolve()
    initial_raw = path.read_bytes()
    input_sha256 = _sha256(initial_raw)
    transaction_state = _input_transaction_state(path, input_sha256)
    if transaction_state == "already_applied":
        return _already_applied_result(path, initial_raw, input_sha256)

    raw, header, _patches, cells, frame, corner_owner_audit = _read_cells(path)
    if raw != initial_raw:
        raise ValueError("A3D input changed while the retag plan was being built")
    retags, stats = plan_retags(cells)
    output = apply_retags(raw, retags)
    replacements = Counter(retag.replacement_material for retag in retags)
    changed_cell_rows = [
        f"{retag.cell.coord[0]},{retag.cell.coord[1]}:{retag.replacement_material}"
        for retag in retags
    ]
    changed_cell_digest = _sha256("\n".join(changed_cell_rows).encode("ascii"))
    selected_coords = {retag.cell.coord for retag in retags}
    components = _connected_components(selected_coords)
    report: dict[str, object] = {
        "schema": "asciicker.a3d_shore_material_retag.v3",
        "mode": "dry_run",
        "transaction_state": transaction_state,
        "write_performed": False,
        "input": str(path),
        "input_sha256": input_sha256,
        "prospective_output_sha256": _sha256(output),
        "byte_count": len(raw),
        "patch_count": int(header.num_patches),
        "map_cell_count": len(cells),
        "map_frame": _map_frame_report(frame),
        "corner_height_ownership_audit": corner_owner_audit,
        **stats,
        "selected_cell_count": len(retags),
        "changed_byte_count": len(retags),
        "replacement_counts": {
            str(material): replacements[material] for material in sorted(replacements)
        },
        "selected_cells_sha256": changed_cell_digest,
        "selected_source_cell_bbox": _source_cell_bbox(selected_coords),
        "selected_godot_world_cell_bbox": _godot_world_cell_bbox(
            selected_coords, frame
        ),
        "selected_connected_components": _component_report(components, frame),
        "canonical_p3_neighborhood_counts": _p3_neighborhood_report(
            cells, selected_coords, components, frame
        ),
        "selected_cell_first": changed_cell_rows[:8],
        "selected_cell_last": changed_cell_rows[-8:] if changed_cell_rows else [],
        "invariants": {
            "only_low_material_bytes_change": True,
            "high_visual_bytes_preserved": True,
            "patch_order_preserved": True,
            "height_diag_tail_preserved": True,
            "output_byte_count_matches_input": len(output) == len(raw),
        },
    }
    if transaction_state == "source_snapshot":
        observed_contract = (
            len(retags),
            changed_cell_digest,
            report["prospective_output_sha256"],
        )
        expected_contract = (
            SHIPPED_SOURCE_SELECTED_CELL_COUNT,
            SHIPPED_SOURCE_SELECTED_CELLS_SHA256,
            SHIPPED_SUCCESSOR_SHA256,
        )
        if observed_contract != expected_contract:
            raise ValueError(
                "shipped source plan disagrees with its pinned one-pass contract: "
                f"expected {expected_contract}, found {observed_contract}"
            )
        report["shipped_transaction_contract"] = _shipped_transaction_contract()
    return output, report


def _atomic_replace(path: Path, data: bytes) -> None:
    mode = path.stat().st_mode & 0o777
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        temporary_path = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def execute(path: Path, *, write: bool) -> dict[str, object]:
    path = path.resolve()
    output, report = build_result(path)
    if not write:
        return report
    report["mode"] = "write"
    if report["transaction_state"] == "already_applied":
        # A true no-op preserves bytes, inode metadata, and the immutable voter
        # snapshot. Do not atomically replace the known successor with itself.
        report["written_sha256"] = report["input_sha256"]
        return report
    current_sha256 = _sha256(path.read_bytes())
    if current_sha256 != report["input_sha256"]:
        raise ValueError(
            "A3D input changed after planning and before write: "
            f"planned {report['input_sha256']}, found {current_sha256}"
        )
    _atomic_replace(path, output)
    report["write_performed"] = True
    report["written_sha256"] = _sha256(path.read_bytes())
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", type=Path, help="A3D map to inspect or retag")
    parser.add_argument(
        "--write",
        action="store_true",
        help="atomically replace INPUT; default is a read-only dry-run",
    )
    args = parser.parse_args()
    path = args.input.resolve()
    report = execute(path, write=args.write)
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
