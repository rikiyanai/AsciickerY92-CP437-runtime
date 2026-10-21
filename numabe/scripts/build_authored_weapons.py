#!/usr/bin/env python3
"""Build the authored low-poly weapon meshes used by the actor asset lane.

The renderer's retained geometry bank stores one triangle in sixteen floats:
``a.xyz/material, b.xyz/shade, c.xyz/0, c.xyz/0``.  These weapons deliberately
use that existing row contract while retaining named parts for later pose
operations (for example, removing the chamber bolt during a reload).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "godot_project" / "assets" / "actor_mesh"
MATERIAL_ID = 237.0
EPSILON = 1.0e-10


Vec3 = tuple[float, float, float]


def add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def mul(a: Vec3, scalar: float) -> Vec3:
    return (a[0] * scalar, a[1] * scalar, a[2] * scalar)


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def length(a: Vec3) -> float:
    return math.sqrt(dot(a, a))


def unit(a: Vec3) -> Vec3:
    magnitude = length(a)
    if magnitude <= EPSILON:
        raise ValueError(f"zero-length direction: {a}")
    return mul(a, 1.0 / magnitude)


def quad_triangles(corners: Sequence[Vec3]) -> Iterable[tuple[Vec3, Vec3, Vec3]]:
    """Split a four-corner face without introducing degenerate triangles."""

    if len(corners) != 4:
        raise ValueError("quad requires four corners")
    yield corners[0], corners[1], corners[2]
    yield corners[0], corners[2], corners[3]


class MeshBuilder:
    def __init__(
        self,
        weapon_id: str,
        description: str,
        coordinate_note: str,
        coordinate_metadata: dict[str, object] | None = None,
        ballistics: dict[str, object] | None = None,
    ):
        self.weapon_id = weapon_id
        self.description = description
        self.coordinate_note = coordinate_note
        self.coordinate_metadata = dict(coordinate_metadata or {})
        self.ballistics = dict(ballistics or {})
        self.rows: list[float] = []
        self.part_indices: list[int] = []
        self.parts: list[dict[str, object]] = []
        self._current_part: int | None = None
        self._point_cloud: list[Vec3] = []

    @property
    def face_count(self) -> int:
        return len(self.rows) // 16

    def begin_part(self, name: str, role: str, description: str) -> None:
        if self._current_part is not None:
            raise RuntimeError("finish the current part before beginning another")
        self._current_part = len(self.parts)
        self.parts.append(
            {
                "part_index": self._current_part,
                "name": name,
                "role": role,
                "description": description,
                "face_start": self.face_count,
                "face_count": 0,
            }
        )

    def finish_part(self) -> None:
        if self._current_part is None:
            raise RuntimeError("no part is active")
        part = self.parts[self._current_part]
        part["face_count"] = self.face_count - int(part["face_start"])
        if int(part["face_count"]) <= 0:
            raise RuntimeError(f"part has no faces: {part['name']}")
        self._current_part = None

    def triangle(self, a: Vec3, b: Vec3, c: Vec3, shade: float) -> None:
        if self._current_part is None:
            raise RuntimeError("a named part must own every face")
        self.rows.extend(
            [
                a[0], a[1], a[2], MATERIAL_ID,
                b[0], b[1], b[2], shade,
                c[0], c[1], c[2], 0.0,
                c[0], c[1], c[2], 0.0,
            ]
        )
        self.part_indices.append(self._current_part)
        self._point_cloud.extend((a, b, c))

    def quad(self, corners: Sequence[Vec3], shade: float) -> None:
        for a, b, c in quad_triangles(corners):
            self.triangle(a, b, c, shade)

    def box(self, low: Vec3, high: Vec3, shade: float) -> None:
        """Add an axis-aligned six-sided box."""

        x0, y0, z0 = low
        x1, y1, z1 = high
        v000 = (x0, y0, z0)
        v100 = (x1, y0, z0)
        v110 = (x1, y1, z0)
        v010 = (x0, y1, z0)
        v001 = (x0, y0, z1)
        v101 = (x1, y0, z1)
        v111 = (x1, y1, z1)
        v011 = (x0, y1, z1)
        for face in (
            (v000, v100, v110, v010),
            (v101, v001, v011, v111),
            (v001, v000, v010, v011),
            (v100, v101, v111, v110),
            (v010, v110, v111, v011),
            (v001, v101, v100, v000),
        ):
            self.quad(face, shade)

    def beam(self, p0: Vec3, p1: Vec3, width: float, height: float, shade: float) -> None:
        """Add a rectangular beam between two points."""

        axis = unit(sub(p1, p0))
        reference: Vec3 = (0.0, 1.0, 0.0)
        if abs(dot(axis, reference)) > 0.92:
            reference = (1.0, 0.0, 0.0)
        side = unit(cross(axis, reference))
        up = unit(cross(side, axis))
        half_side = mul(side, width * 0.5)
        half_up = mul(up, height * 0.5)
        a0 = add(add(p0, half_side), half_up)
        b0 = add(sub(p0, half_side), half_up)
        c0 = sub(sub(p0, half_side), half_up)
        d0 = sub(add(p0, half_side), half_up)
        a1 = add(add(p1, half_side), half_up)
        b1 = add(sub(p1, half_side), half_up)
        c1 = sub(sub(p1, half_side), half_up)
        d1 = sub(add(p1, half_side), half_up)
        for face in (
            (a0, b0, c0, d0),
            (a1, d1, c1, b1),
            (a0, a1, b1, b0),
            (b0, b1, c1, c0),
            (c0, c1, d1, d0),
            (d0, d1, a1, a0),
        ):
            self.quad(face, shade)

    def cone(self, base: Vec3, tip: Vec3, radius: float, sides: int, shade: float) -> None:
        """Add a low-poly cone with a separate base cap."""

        axis = unit(sub(tip, base))
        reference: Vec3 = (0.0, 1.0, 0.0)
        if abs(dot(axis, reference)) > 0.92:
            reference = (1.0, 0.0, 0.0)
        side = unit(cross(axis, reference))
        up = unit(cross(side, axis))
        ring: list[Vec3] = []
        for index in range(sides):
            angle = (2.0 * math.pi * index) / sides
            offset = add(mul(side, math.cos(angle) * radius), mul(up, math.sin(angle) * radius))
            ring.append(add(base, offset))
        for index in range(sides):
            nxt = (index + 1) % sides
            self.triangle(ring[index], ring[nxt], tip, shade)
            self.triangle(base, ring[nxt], ring[index], shade * 0.92)

    def payload(self) -> dict[str, object]:
        if self._current_part is not None:
            raise RuntimeError("an unfinished part cannot be emitted")
        points = self._point_cloud
        low = [min(point[i] for point in points) for i in range(3)]
        high = [max(point[i] for point in points) for i in range(3)]
        coordinate_contract: dict[str, object] = {
            "grip_origin": [0.0, 0.0, 0.0],
            "forward": "+Z",
            "up": "+Y",
            "orientation": self.coordinate_note,
        }
        coordinate_contract.update(self.coordinate_metadata)
        payload: dict[str, object] = {
            "schema": "asciicker.authored_weapon_mesh.v1",
            "weapon_id": self.weapon_id,
            "description": self.description,
            "coordinate_contract": coordinate_contract,
            "geometry_contract": {
                "row_layout": "p0.xyz_material237,p1.xyz_shade,p2.xyz_zero,p3.xyz_zero",
                "primitive": "triangle_as_degenerate_quad_p3_equals_p2",
                "material_id": 237,
                "shade_lane": "p1.w",
            },
            "construction": {
                "geometry_kind": "authored_low_poly_mesh",
                "generator": "scripts/build_authored_weapons.py",
                "validation": [
                    "finite_rows",
                    "nondegenerate_triangles",
                    "material_lane_237",
                    "degenerate_quad_tail",
                    "part_indices_in_range",
                    "all_parts_referenced",
                    "bounds_match_rows",
                ],
            },
            "part_count": len(self.parts),
            "parts": self.parts,
            "face_count": self.face_count,
            "part_indices": self.part_indices,
            "face_rows": self.rows,
            "local_bounds": {"min": low, "max": high},
        }
        if self.ballistics:
            payload["ballistics"] = self.ballistics
        return payload


def crossbow_mesh() -> MeshBuilder:
    mesh = MeshBuilder(
        "crossbow_weapon",
        "Authored low-poly crossbow with a retained stock, curved prod, separated string, trigger, rail, and chamber bolt.",
        "The grip hand point is the origin; stock, rail, and bolt project along +Z, with bow height on +Y.",
        {
            "barrel_axis": [0.0, 0.0, 1.0],
            "bore_axis": [0.0, 0.0, 1.0],
            "muzzle_local": [0.0, 0.385, 1.045],
            "muzzle_source_part": "chamber_bolt",
            "grip_actor": [-0.10, 1.15, -0.04],
            "aim_pivot_actor": [0.0, 1.45, -0.04],
            "aim_pitch_degrees": [-80, -40, 0, 40, 80],
            "string_anchor_z": 0.40,
            "string_nock_z": 0.12,
        },
        {
            "speed_per_tick": 1.25,
            "gravity_per_tick": 0.003,
            "lifetime_ticks": 64,
            "sight_distance": 34.0,
            "reload_ticks": 28,
            "magazine_capacity": 1,
            "starting_spare": 12,
        },
    )

    mesh.begin_part("stock", "static", "Tapered-looking solid stock from the grip to the bow seat.")
    mesh.box((-0.09, 0.13, 0.02), (0.09, 0.29, 0.68), 0.66)
    mesh.box((-0.12, 0.19, 0.48), (0.12, 0.32, 0.66), 0.72)
    mesh.finish_part()

    mesh.begin_part("grip", "static", "Angled hand grip crossing the local origin.")
    mesh.beam((0.0, 0.18, 0.10), (0.0, 0.02, -0.25), 0.12, 0.14, 0.52)
    mesh.finish_part()

    mesh.begin_part("trigger", "trigger", "Visible angled trigger inside the guard.")
    mesh.beam((0.0, 0.15, 0.24), (0.0, 0.05, 0.11), 0.035, 0.055, 0.82)
    mesh.finish_part()

    mesh.begin_part("trigger_guard", "trigger", "Open U-shaped trigger guard around the trigger.")
    mesh.beam((-0.075, 0.12, 0.26), (-0.075, 0.08, 0.07), 0.028, 0.035, 0.58)
    mesh.beam((-0.075, 0.08, 0.07), (0.075, 0.08, 0.07), 0.028, 0.035, 0.58)
    mesh.beam((0.075, 0.08, 0.07), (0.075, 0.12, 0.26), 0.028, 0.035, 0.58)
    mesh.finish_part()

    mesh.begin_part("bolt_rail", "static", "Twin raised rail guides under the chamber bolt.")
    mesh.beam((-0.055, 0.305, 0.18), (-0.055, 0.305, 0.72), 0.035, 0.045, 0.74)
    mesh.beam((0.055, 0.305, 0.18), (0.055, 0.305, 0.72), 0.035, 0.045, 0.68)
    mesh.finish_part()

    mesh.begin_part("bow_center", "static", "Central prod seat and bow riser.")
    mesh.box((-0.095, 0.34, 0.36), (0.095, 0.46, 0.51), 0.76)
    mesh.finish_part()

    mesh.begin_part("prod_left", "static", "Broad segmented left limb of the curved prod.")
    left = [(0.0, 0.40, 0.44), (-0.18, 0.405, 0.48), (-0.35, 0.41, 0.47), (-0.48, 0.405, 0.40)]
    for p0, p1 in zip(left, left[1:]):
        mesh.beam(p0, p1, 0.065, 0.075, 0.71)
    mesh.finish_part()

    mesh.begin_part("prod_right", "static", "Broad segmented right limb of the curved prod.")
    right = [(0.0, 0.40, 0.44), (0.18, 0.405, 0.48), (0.35, 0.41, 0.47), (0.48, 0.405, 0.40)]
    for p0, p1 in zip(right, right[1:]):
        mesh.beam(p0, p1, 0.065, 0.075, 0.71)
    mesh.finish_part()

    mesh.begin_part("bow_string", "reload_string", "Separate two-segment string retained for reload pose changes.")
    nock = (0.0, 0.395, 0.12)
    mesh.beam((-0.48, 0.405, 0.40), nock, 0.014, 0.014, 0.91)
    mesh.beam(nock, (0.48, 0.405, 0.40), 0.014, 0.014, 0.91)
    mesh.finish_part()

    mesh.begin_part("chamber_bolt", "reload_chamber_bolt", "Separate loaded bolt shaft and pointed head for reload pose changes.")
    mesh.beam((0.0, 0.385, 0.49), (0.0, 0.385, 0.93), 0.028, 0.028, 0.86)
    mesh.cone((0.0, 0.385, 0.92), (0.0, 0.385, 1.045), 0.045, 6, 0.93)
    mesh.finish_part()
    return mesh


def longbow_mesh() -> MeshBuilder:
    mesh = MeshBuilder(
        "longbow_weapon",
        "Authored low-poly longbow with curved upper/lower limbs, three retained string draw shapes, and a separate nocked arrow.",
        "The grip hand point is the origin; the bow spans local Y and the arrow flies along +Z.",
        {
            "bore_axis": [0.0, 0.0, 1.0],
            "muzzle_local": [0.0, 0.02, 1.06],
            "grip_actor": [-0.16, 1.22, 0.04],
            "aim_pivot_actor": [0.0, 1.42, 0.02],
            "aim_pitch_degrees": [-80, -40, 0, 40, 80],
            "string_draw_shapes": ["rest", "half", "full"],
            "full_draw_nock_z": -0.46,
        },
        {
            "speed_per_tick": 1.05,
            "gravity_per_tick": 0.0045,
            "lifetime_ticks": 76,
            "sight_distance": 32.0,
            "draw_ticks": 9,
            "hold_ticks": 3,
            "recovery_ticks": 8,
            "starting_arrows": 18,
        },
    )

    mesh.begin_part("grip", "static", "Leather-wrapped hand grip at the bow centre.")
    mesh.beam((0.0, -0.15, 0.0), (0.0, 0.15, 0.0), 0.07, 0.06, 0.55)
    mesh.finish_part()

    mesh.begin_part("upper_limb", "static", "Segmented recurved upper limb.")
    upper = [(0.0, 0.13, 0.0), (0.035, 0.48, 0.025), (0.09, 0.82, 0.10), (0.16, 1.06, 0.20)]
    for p0, p1 in zip(upper, upper[1:]):
        mesh.beam(p0, p1, 0.045, 0.055, 0.72)
    mesh.finish_part()

    mesh.begin_part("lower_limb", "static", "Segmented recurved lower limb.")
    lower = [(0.0, -0.13, 0.0), (-0.035, -0.48, 0.025), (-0.09, -0.82, 0.10), (-0.16, -1.06, 0.20)]
    for p0, p1 in zip(lower, lower[1:]):
        mesh.beam(p0, p1, 0.045, 0.055, 0.68)
    mesh.finish_part()

    tips = ((0.16, 1.06, 0.20), (-0.16, -1.06, 0.20))
    for shape, nock_z in (("rest", 0.18), ("half", -0.16), ("full", -0.46)):
        for side, tip in zip(("upper", "lower"), tips):
            mesh.begin_part(
                f"string_{shape}_{side}",
                f"draw_string_{shape}",
                f"{side.title()} string segment for the retained {shape} draw shape.",
            )
            mesh.beam(tip, (0.0, 0.0, nock_z), 0.011, 0.011, 0.93)
            mesh.finish_part()

    mesh.begin_part("nocked_arrow", "draw_arrow", "Separate arrow shown through draw/hold and hidden after release.")
    mesh.beam((0.0, 0.0, -0.46), (0.0, 0.0, 0.96), 0.022, 0.022, 0.86)
    mesh.cone((0.0, 0.0, 0.94), (0.0, 0.0, 1.06), 0.042, 5, 0.96)
    mesh.finish_part()
    return mesh


def sword_mesh() -> MeshBuilder:
    mesh = MeshBuilder(
        "sword_weapon",
        "Authored low-poly sword with separate pommel, grip, guard, ricasso, blade, and ridge geometry.",
        "The grip hand point is the origin; the blade and its point extend along +Z, with the crossguard spanning ±X.",
        {
            "blade_tip_local": [0.0, 0.0, 1.045],
            "grip_axis": [0.0, 0.0, 1.0],
        },
    )

    mesh.begin_part("pommel", "static", "Weighted pommel below the grip origin.")
    mesh.box((-0.075, -0.065, -0.35), (0.075, 0.065, -0.25), 0.55)
    mesh.finish_part()

    mesh.begin_part("grip", "static", "Short wrapped grip centered on the local hand origin.")
    mesh.beam((0.0, 0.0, -0.27), (0.0, 0.0, -0.015), 0.105, 0.105, 0.49)
    mesh.finish_part()

    mesh.begin_part("guard", "static", "Wide crossguard spanning both sides of the blade.")
    mesh.beam((-0.29, 0.0, 0.02), (0.29, 0.0, 0.02), 0.075, 0.075, 0.77)
    mesh.beam((-0.18, 0.0, 0.03), (-0.28, 0.03, 0.085), 0.055, 0.055, 0.69)
    mesh.beam((0.18, 0.0, 0.03), (0.28, 0.03, 0.085), 0.055, 0.055, 0.69)
    mesh.finish_part()

    mesh.begin_part("ricasso", "static", "Short reinforced blade base above the guard.")
    mesh.box((-0.09, -0.035, 0.055), (0.09, 0.035, 0.16), 0.72)
    mesh.finish_part()

    mesh.begin_part("blade", "static", "Tapered low-poly blade ending in a true point beyond +Z 0.9.")
    lower = [(-0.09, -0.028, 0.145), (0.09, -0.028, 0.145), (0.09, 0.028, 0.145), (-0.09, 0.028, 0.145)]
    upper = [(-0.065, -0.022, 0.76), (0.065, -0.022, 0.76), (0.065, 0.022, 0.76), (-0.065, 0.022, 0.76)]
    for index in range(4):
        nxt = (index + 1) % 4
        mesh.quad((lower[index], lower[nxt], upper[nxt], upper[index]), 0.82 - index * 0.035)
    mesh.quad((lower[0], lower[3], lower[2], lower[1]), 0.67)
    mesh.cone((0.0, 0.0, 0.76), (0.0, 0.0, 1.045), 0.068, 4, 0.86)
    mesh.finish_part()

    mesh.begin_part("blade_ridge", "static", "Raised central fuller ridge that catches the retained shade lane.")
    mesh.beam((0.0, 0.029, 0.19), (0.0, 0.029, 0.75), 0.018, 0.010, 0.91)
    mesh.finish_part()
    return mesh


def sword_slash_mesh() -> MeshBuilder:
    """Build the world-space-following slash as eight retained arc wedges."""

    inner_radius = 1.0
    outer_radius = 1.15
    yaw_start = 1.15
    yaw_end = -1.15
    wedge_count = 8
    mesh = MeshBuilder(
        "sword_slash",
        "Authored eight-wedge sword slash ribbon following the actor weapon in world space.",
        "The slash lies in the XZ plane at Y=0; +Z is forward and positive yaw moves toward +X.",
        {
            "slash_plane": "XZ",
            "yaw_axis": [0.0, 1.0, 0.0],
            "yaw_start": yaw_start,
            "yaw_end": yaw_end,
            "inner_radius": inner_radius,
            "outer_radius": outer_radius,
            "wedge_count": wedge_count,
            "faces_per_wedge": 2,
        },
    )

    def point(radius: float, yaw: float) -> Vec3:
        return (radius * math.sin(yaw), 0.0, radius * math.cos(yaw))

    for wedge_index in range(wedge_count):
        start = yaw_start + (yaw_end - yaw_start) * wedge_index / wedge_count
        end = yaw_start + (yaw_end - yaw_start) * (wedge_index + 1) / wedge_count
        mesh.begin_part(
            f"wedge_{wedge_index:02d}",
            "slash_wedge",
            f"Independent tapered ribbon sector {wedge_index} of {wedge_count}.",
        )
        inner_start = point(inner_radius, start)
        outer_start = point(outer_radius, start)
        outer_end = point(outer_radius, end)
        inner_end = point(inner_radius, end)
        mesh.triangle(inner_start, outer_start, outer_end, 0.82)
        mesh.triangle(inner_start, outer_end, inner_end, 0.94)
        mesh.finish_part()
    return mesh


def validate(payload: dict[str, object]) -> None:
    rows = payload["face_rows"]
    part_indices = payload["part_indices"]
    parts = payload["parts"]
    if not isinstance(rows, list) or len(rows) % 16 != 0:
        raise ValueError("face_rows must be a flat list with sixteen values per face")
    if payload["face_count"] != len(rows) // 16 or payload["face_count"] != len(part_indices):
        raise ValueError("face_count does not agree with rows and part indices")
    if payload["part_count"] != len(parts):
        raise ValueError("part_count does not agree with parts")
    if not parts:
        raise ValueError("a weapon must have named parts")

    referenced = set()
    points: list[Vec3] = []
    for face_index in range(int(payload["face_count"])):
        row = rows[face_index * 16 : (face_index + 1) * 16]
        if any(not math.isfinite(float(value)) for value in row):
            raise ValueError(f"non-finite face row {face_index}")
        if row[3] != MATERIAL_ID or row[11] != 0.0 or row[15] != 0.0:
            raise ValueError(f"row lane contract failed at face {face_index}")
        if row[8:11] != row[12:15]:
            raise ValueError(f"degenerate quad tail failed at face {face_index}")
        a = (float(row[0]), float(row[1]), float(row[2]))
        b = (float(row[4]), float(row[5]), float(row[6]))
        c = (float(row[8]), float(row[9]), float(row[10]))
        if length(cross(sub(b, a), sub(c, a))) <= EPSILON:
            raise ValueError(f"degenerate triangle {face_index}")
        points.extend((a, b, c))
        part_index = int(part_indices[face_index])
        if part_index < 0 or part_index >= int(payload["part_count"]):
            raise ValueError(f"part index out of range at face {face_index}")
        referenced.add(part_index)
    if referenced != set(range(int(payload["part_count"]))):
        raise ValueError("every named part must be referenced by at least one face")

    expected_start = 0
    for expected_index, part in enumerate(parts):
        if int(part["part_index"]) != expected_index:
            raise ValueError("part indices must be contiguous")
        if int(part["face_start"]) != expected_start:
            raise ValueError("part face ranges must be contiguous")
        if int(part["face_count"]) <= 0:
            raise ValueError("part face ranges must be non-empty")
        expected_start += int(part["face_count"])
    if expected_start != int(payload["face_count"]):
        raise ValueError("part face ranges do not cover every face")

    bounds = payload["local_bounds"]
    actual_low = [min(point[i] for point in points) for i in range(3)]
    actual_high = [max(point[i] for point in points) for i in range(3)]
    if bounds["min"] != actual_low or bounds["max"] != actual_high:
        raise ValueError("local bounds do not match the retained rows")
    validate_ballistics(payload)


def validate_ballistics(payload: dict[str, object]) -> None:
    """Require each authored ranged weapon's projectile facts to be usable."""

    weapon_id = payload.get("weapon_id")
    if weapon_id not in {"crossbow_weapon", "longbow_weapon"}:
        if "ballistics" in payload:
            raise ValueError("only an authored ranged weapon may carry ballistics metadata")
        return
    ballistics = payload.get("ballistics")
    if not isinstance(ballistics, dict):
        raise ValueError(f"{weapon_id} ballistics metadata is required")
    positive_real_fields = (
        "speed_per_tick",
        "gravity_per_tick",
        "sight_distance",
    )
    positive_integer_fields = ("lifetime_ticks",)
    if weapon_id == "crossbow_weapon":
        positive_integer_fields += ("reload_ticks", "magazine_capacity", "starting_spare")
    else:
        positive_integer_fields += ("draw_ticks", "hold_ticks", "recovery_ticks", "starting_arrows")
    for field in positive_real_fields:
        value = ballistics.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{weapon_id} ballistics {field} must be numeric")
        if not math.isfinite(float(value)) or float(value) <= 0.0:
            raise ValueError(f"{weapon_id} ballistics {field} must be finite and positive")
    for field in positive_integer_fields:
        value = ballistics.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{weapon_id} ballistics {field} must be a positive integer")


def validate_sword_slash(payload: dict[str, object]) -> None:
    validate(payload)
    if payload["part_count"] != 8 or payload["face_count"] != 16:
        raise ValueError("sword slash must contain eight wedges and sixteen faces")
    if any(int(part["face_count"]) != 2 for part in payload["parts"]):
        raise ValueError("every sword slash wedge must contain exactly two triangles")
    if payload["part_indices"] != [part for part in range(8) for _ in range(2)]:
        raise ValueError("sword slash faces must be grouped under wedge part indices 0..7")
    rows = payload["face_rows"]
    if any(rows[offset + 1] != 0.0 for offset in range(0, len(rows), 16)):
        raise ValueError("sword slash must remain at Y=0")
    if any(rows[offset + 2] < 0.0 for offset in range(0, len(rows), 16)):
        raise ValueError("sword slash must stay in the forward +Z half-plane")


def with_hash(payload: dict[str, object]) -> dict[str, object]:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    output = dict(payload)
    output["content_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return output


def write_mesh(path: Path, mesh: MeshBuilder) -> None:
    payload = mesh.payload()
    validate_sword_slash(payload) if mesh.weapon_id == "sword_slash" else validate(payload)
    path.write_text(json.dumps(with_hash(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def check_mesh(path: Path) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    stored_hash = payload.pop("content_sha256", None)
    expected = with_hash(payload)["content_sha256"]
    if stored_hash != expected:
        raise ValueError(f"content hash mismatch: {path}")
    validate_sword_slash(payload) if payload.get("weapon_id") == "sword_slash" else validate(payload)
    print(f"validated {path}: {payload['part_count']} parts, {payload['face_count']} faces")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate existing generated meshes")
    args = parser.parse_args()
    outputs = {
        ASSET_DIR / "crossbow_weapon.json": crossbow_mesh,
        ASSET_DIR / "longbow_weapon.json": longbow_mesh,
        ASSET_DIR / "sword_weapon.json": sword_mesh,
        ASSET_DIR / "sword_slash.json": sword_slash_mesh,
    }
    if args.check:
        for path in outputs:
            check_mesh(path)
    else:
        ASSET_DIR.mkdir(parents=True, exist_ok=True)
        for path, factory in outputs.items():
            write_mesh(path, factory())
            check_mesh(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
