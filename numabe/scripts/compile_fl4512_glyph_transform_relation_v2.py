#!/usr/bin/env python3
"""Compile the immutable FL-4512 GlyphTransformRelation v2 artifact.

The live renderer is deliberately unable to invent glyph relationships.  This
offline compiler reads the already-admitted 32-glyph row, the exact 16-pixel
atlas cells, the pinned font chain, the material profile, the v2 schema, and
the authored 64-view affine chart.  It derives every descriptor and every
ordered relation once, seals the resulting arrays with SHA-256, and writes
canonical JSON that the C++ loader can verify before publication.

Run from the repository root:

    python3 scripts/compile_fl4512_glyph_transform_relation_v2.py --write
    python3 scripts/compile_fl4512_glyph_transform_relation_v2.py --check

``--bootstrap-authoring`` writes the deterministic 64-row chart source.  It is
an explicit bootstrap operation, not a runtime fallback: normal compilation
requires that checked-in source to exist and validates every one of its keys.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

from fl4512_u7_mask import betti_numbers, mask8_from_page16_rgba, topology_counts


ROOT = Path(__file__).resolve().parents[1]
ADMISSION = ROOT / "assets/glyphs/generated/fl4512.mesh_feature_candidate_admission.v2.json"
REGISTRY = ROOT / "assets/glyphs/generated/fl4482.native_atlas_registry.v1.json"
AOA = ROOT / "assets/glyphs/atlases/unicode16.onecell.v1.atlas_of_atlases.json"
PROFILE = ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json"
SCHEMA = ROOT / "assets/glyphs/schema/fl4512.glyph_transform_relation.v2.schema.json"
AUTHORING = ROOT / "assets/glyphs/profiles/fl4512.glyph_transform_relation_affine_authoring.v2.json"
OUTPUT = ROOT / "assets/glyphs/generated/fl4512.glyph_transform_relation.v2.json"
RASTER_SOURCE = ROOT / "scripts/fl4512_u7_mask.py"

SCHEMA_ID = "fl4512.glyph_transform_relation.v2"
AUTHORING_SCHEMA = "fl4512.glyph_transform_relation_affine_authoring.v2"
DESCRIPTOR_ABI = "fl4512.glyph_transform_descriptor.v2"
Q16 = 1 << 16
Q24 = 1 << 24
MASK64 = (1 << 64) - 1
EXPECTED_GLYPHS = 32
EXPECTED_ASPECTS = 64
EXPECTED_RELATIONS = EXPECTED_GLYPHS * EXPECTED_GLYPHS * EXPECTED_ASPECTS

# The order is part of the ABI.  The first eleven terms are invariant glyph
# morphology.  The last seven depend on the selected view and live confidence.
STATIC_COMPONENTS = (
    ("density", 64, 12),
    ("endpoints", 16, 18),
    ("junctions", 16, 18),
    ("beta0", 16, 16),
    ("beta1", 16, 16),
    ("euler", 16, 8),
    ("orientation", 65534, 28),
    ("paths", 32, 18),
    ("cycles", 32, 18),
    ("curvature", 65535, 20),
    ("convexity", 65535, 16),
)
AFFINE_COMPONENTS = (
    ("affine_mask", 64, 80),
    ("reflection", 64, 24),
    ("c4", 64, 24),
    ("determinant", 16777216, 20),
    ("fit", 65536, 32),
    ("involution", 4096, 32),
    ("observed_offset", 262144, 48),
)
CALIBRATION = STATIC_COMPONENTS + AFFINE_COMPONENTS


class CompileError(ValueError):
    """A stable fail-closed compiler diagnostic."""


def _canonical(value: Any) -> bytes:
    """Return the paper's sorted, whitespace-free UTF-8 JSON encoding."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha_bytes(data: bytes) -> str:
    """Return a lowercase SHA-256 hexadecimal digest."""
    return hashlib.sha256(data).hexdigest()


def _sha_value(value: Any) -> str:
    """Hash a value after canonical JSON encoding."""
    return _sha_bytes(_canonical(value))


def _read_json(path: Path) -> dict[str, Any]:
    """Read one JSON object while rejecting duplicate keys and non-objects."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise CompileError(f"duplicate_key:{path}:{key}")
            result[key] = value
        return result

    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompileError(f"json_read_failed:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise CompileError(f"root_not_object:{path}")
    return value


def _exact_keys(value: dict[str, Any], expected: Iterable[str], where: str) -> None:
    """Reject missing and extra object keys so schema drift cannot be ignored."""
    wanted = set(expected)
    observed = set(value)
    if observed != wanted:
        raise CompileError(
            f"exact_keys:{where}:missing={sorted(wanted-observed)}:extra={sorted(observed-wanted)}"
        )


def _integer(value: Any, minimum: int, maximum: int, where: str) -> int:
    """Require a non-Boolean integer inside an exact inclusive range."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise CompileError(f"integer_required:{where}")
    if value < minimum or value > maximum:
        raise CompileError(f"integer_range:{where}:{value}")
    return value


def _rha(numerator: int, denominator: int) -> int:
    """Round a signed rational half away from zero, exactly once."""
    if denominator <= 0:
        raise CompileError("zero_divisor")
    sign = -1 if numerator < 0 else 1
    return sign * ((2 * abs(numerator) + denominator) // (2 * denominator))


def _q016(numerator: int, denominator: int) -> int:
    """Quantize a nonnegative rational into saturated unsigned Q0.16."""
    if numerator < 0:
        raise CompileError("q016_negative")
    return max(0, min(65535, _rha(65535 * numerator, denominator)))


def _q824(value: Fraction, where: str) -> int:
    """Quantize one affine coefficient to signed Q8.24 without saturation."""
    result = _rha(value.numerator * Q24, value.denominator)
    if not -(1 << 31) <= result <= (1 << 31) - 1:
        raise CompileError(f"affine_overflow:{where}")
    # A coefficient touching the representable endpoint would have saturated
    # in a narrower implementation, which the normative contract forbids.
    if result in (-(1 << 31), (1 << 31) - 1):
        raise CompileError(f"affine_saturation:{where}")
    return result


def _q1616_nonnegative(value: Fraction, where: str) -> int:
    """Quantize a nonnegative cell quantity into unsigned Q16.16."""
    if value < 0:
        raise CompileError(f"negative_q1616:{where}")
    result = _rha(value.numerator * Q16, value.denominator)
    if not 0 <= result <= 0xFFFFFFFF:
        raise CompileError(f"q1616_overflow:{where}")
    return result


def _mask_bit(mask: int, x: int, y: int) -> int:
    """Read one mask bit; outside samples are zero."""
    if not 0 <= x < 8 or not 0 <= y < 8:
        return 0
    return (mask >> (8 * y + x)) & 1


def _transform_mask(mask: int, transform: int, reflection: bool) -> int:
    """Apply one exact reflection or C4 coordinate mapping."""
    result = 0
    for y in range(8):
        for x in range(8):
            if not _mask_bit(mask, x, y):
                continue
            if reflection:
                mappings = ((7 - x, y), (x, 7 - y), (y, x), (7 - y, 7 - x))
            else:
                mappings = ((x, y), (y, 7 - x), (7 - x, 7 - y), (7 - y, x))
            nx, ny = mappings[transform]
            result |= 1 << (8 * ny + nx)
    return result


def _orientation(mask: int) -> list[int]:
    """Compute the descriptor's exact doubled-angle orientation pair."""
    a_value = 0
    b_value = 0
    for y in range(8):
        for x in range(8):
            if not _mask_bit(mask, x, y):
                continue
            xx = 2 * x - 7
            yy = 2 * y - 7
            a_value += xx * xx - yy * yy
            b_value += 2 * xx * yy
    length = abs(a_value) + abs(b_value)
    if length == 0:
        return [0, 0]
    return [
        max(-32767, min(32767, _rha(32767 * a_value, length))),
        max(-32767, min(32767, _rha(32767 * b_value, length))),
    ]


def _skeleton_graph(skeleton: int) -> tuple[dict[int, set[int]], int, int]:
    """Build the paper's corner-safe skeleton graph and return paths/cycles."""
    graph: dict[int, set[int]] = {
        index: set() for index in range(64) if (skeleton >> index) & 1
    }
    for index in graph:
        x, y = index % 8, index // 8
        for dx, dy in ((1, 0), (0, 1), (1, 1), (-1, 1)):
            nx, ny = x + dx, y + dy
            neighbor = 8 * ny + nx
            if neighbor not in graph:
                continue
            if dx and dy and (
                _mask_bit(skeleton, x + dx, y) or _mask_bit(skeleton, x, y + dy)
            ):
                continue
            graph[index].add(neighbor)
            graph[neighbor].add(index)

    seen: set[int] = set()
    components = 0
    all_degree_two_cycles = 0
    isolated = 0
    for seed in graph:
        if seed in seen:
            continue
        components += 1
        stack = [seed]
        members: list[int] = []
        seen.add(seed)
        while stack:
            current = stack.pop()
            members.append(current)
            for neighbor in graph[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    stack.append(neighbor)
        all_degree_two_cycles += int(bool(members) and all(len(graph[n]) == 2 for n in members))
        isolated += int(len(members) == 1 and not graph[members[0]])
    edge_count = sum(len(neighbors) for neighbors in graph.values()) // 2
    cycles = edge_count - len(graph) + components
    paths = sum(len(neighbors) for neighbors in graph.values() if len(neighbors) != 2) // 2
    paths += all_degree_two_cycles + isolated
    return graph, paths, cycles


def _curvature(graph: dict[int, set[int]]) -> int:
    """Average the squared degree-two cross product into Q0.16."""
    numerator = 0
    count = 0
    for center, neighbors in graph.items():
        if len(neighbors) != 2:
            continue
        first, second = sorted(neighbors)
        cx, cy = center % 8, center // 8
        px, py = first % 8 - cx, first // 8 - cy
        qx, qy = second % 8 - cx, second // 8 - cy
        numerator += (px * qy - py * qx) ** 2
        count += 1
    return 0 if count == 0 else _q016(numerator, 4 * count)


def _cross(origin: tuple[int, int], a: tuple[int, int], b: tuple[int, int]) -> int:
    """Return the signed doubled triangle area used by the integer hull."""
    return (a[0] - origin[0]) * (b[1] - origin[1]) - (
        a[1] - origin[1]
    ) * (b[0] - origin[0])


def _convexity(mask: int) -> int:
    """Compute the exact pixel-center hull occupancy ratio in Q0.16."""
    points = [(x, y) for y in range(8) for x in range(8) if _mask_bit(mask, x, y)]
    if len(points) <= 2:
        support = len(points)
    else:
        points = sorted(set(points))
        lower: list[tuple[int, int]] = []
        upper: list[tuple[int, int]] = []
        for point in points:
            while len(lower) >= 2 and _cross(lower[-2], lower[-1], point) <= 0:
                lower.pop()
            lower.append(point)
        for point in reversed(points):
            while len(upper) >= 2 and _cross(upper[-2], upper[-1], point) <= 0:
                upper.pop()
            upper.append(point)
        hull = lower[:-1] + upper[:-1]
        if len(hull) < 3:
            support = len(set(points))
        else:
            sign = 0
            support = 0
            for candidate in ((x, y) for y in range(8) for x in range(8)):
                inside = True
                for index, start in enumerate(hull):
                    area = _cross(start, hull[(index + 1) % len(hull)], candidate)
                    if area:
                        current_sign = 1 if area > 0 else -1
                        if sign == 0:
                            sign = current_sign
                        if current_sign != sign:
                            inside = False
                            break
                support += int(inside)
    return _q016(len(points), max(1, support))


def _descriptor(mask: int, glyph_id: int, row_masks: dict[int, int]) -> dict[str, Any]:
    """Compile every exact integer glyph descriptor required by v2."""
    skeleton, endpoints, junctions = topology_counts(mask)
    beta0, beta1 = betti_numbers(mask)
    graph, paths, cycles = _skeleton_graph(skeleton)
    reflections = [_transform_mask(mask, index, True) for index in range(4)]
    rotations = [_transform_mask(mask, index, False) for index in range(4)]
    reverse: dict[int, int] = {}
    for candidate_id, candidate_mask in sorted(row_masks.items()):
        reverse.setdefault(candidate_mask, candidate_id)
    return {
        "beta0": beta0,
        "beta1": beta1,
        "c4_masks_u64_hex": [f"{value:016x}" for value in rotations],
        "c4_partner_glyph_ids": [reverse.get(value) for value in rotations],
        "convexity_q0_16": _convexity(mask),
        "curvature_q0_16": _curvature(graph),
        "density": mask.bit_count(),
        "endpoints": endpoints,
        "euler": beta0 - beta1,
        "glyph_id": glyph_id,
        "junctions": junctions,
        "mask8_u64_hex": f"{mask:016x}",
        "orientation_q1_15": _orientation(mask),
        "paths": paths,
        "cycles": cycles,
        "reflection_masks_u64_hex": [f"{value:016x}" for value in reflections],
        "reflection_partner_glyph_ids": [reverse.get(value) for value in reflections],
        "skeleton_u64_hex": f"{skeleton:016x}",
    }


def _load_masks(admission: dict[str, Any], registry: dict[str, Any]) -> dict[int, int]:
    """Read each admitted atlas cell and reduce its exact RGBA bytes to mask8."""
    selected = admission.get("selected_row")
    if not isinstance(selected, list) or len(selected) != EXPECTED_GLYPHS:
        raise CompileError("selected_row_cardinality")
    glyph_ids = [_integer(row.get("glyph_id"), 1, 0xFFFFFFFF, "glyph_id") for row in selected]
    if glyph_ids != sorted(set(glyph_ids)):
        raise CompileError("selected_row_order")

    pages = registry.get("pages")
    if not isinstance(pages, list):
        raise CompileError("registry_pages_missing")
    page_by_key = {
        (page.get("pack_index"), page.get("page_index")): page for page in pages
    }
    image_cache: dict[Path, tuple[list[int], int]] = {}
    masks: dict[int, int] = {}
    for row in selected:
        page = page_by_key.get((row.get("pack_index"), row.get("page_index")))
        if not isinstance(page, dict):
            raise CompileError(f"atlas_page_missing:{row.get('glyph_id')}")
        page_path = ROOT / str(page.get("page16_path"))
        expected_hash = page.get("page16_sha256")
        if page_path not in image_cache:
            with Image.open(page_path) as image:
                rgba = image.convert("RGBA")
                rgba_bytes = rgba.tobytes()
                # The atlas registry seals decoded RGBA bytes.  This deliberately
                # ignores PNG compressor metadata while still binding every
                # raster sample that can contribute to a glyph mask.
                if _sha_bytes(rgba_bytes) != expected_hash:
                    raise CompileError(f"atlas_page_hash:{page_path}")
                image_cache[page_path] = (list(rgba_bytes), rgba.width)
        bytes_rgba, width = image_cache[page_path]
        x0 = _integer(row.get("source_column"), 0, 15, "source_column") * 16
        y0 = _integer(row.get("source_row"), 0, 65535, "source_row") * 16
        masks[row["glyph_id"]] = mask8_from_page16_rgba(bytes_rgba, width, x0, y0)
    return masks


def _authoring_template() -> dict[str, Any]:
    """Create 64 explicit chart rows, including axis-aligned and oblique views."""
    aspects: list[dict[str, Any]] = []
    sources = ((0, 0), (8 * Q16, 0), (0, 8 * Q16), (8 * Q16, 8 * Q16))
    for address in range(64):
        u, v = address % 8, address // 8
        if address == 0:
            matrix = (Q16, 0, 0, Q16)
        elif address == 1:
            matrix = (0, -Q16, Q16, 0)
        else:
            matrix = (
                (16 + u) * 4096,
                (v - 3) * 4096,
                (u - 3) * 4096,
                (17 + v) * 4096,
            )
        determinant = matrix[0] * matrix[3] - matrix[1] * matrix[2]
        if determinant == 0:
            raise CompileError(f"template_rank_loss:{address}")
        # Center the involution on the 8x8 glyph cell.  The first identity chart
        # therefore reduces exactly to x -> 7-x, and the quarter-turn chart
        # reduces exactly to y -> 7-y.  Small bin offsets keep later charts
        # view-specific without moving the two axis-aligned calibration rows.
        translation = (
            7 * Q16 // 2 + (u * 8192 if address >= 2 else 0),
            7 * Q16 // 2 + (v * 8192 if address >= 2 else 0),
        )
        a00, a01, a10, a11 = (Fraction(value, Q16) for value in matrix)
        det = a00 * a11 - a01 * a10
        s00 = (-a00 * a11 - a01 * a10) / det
        s01 = 2 * a00 * a01 / det
        s10 = -2 * a10 * a11 / det
        s11 = (a00 * a11 + a01 * a10) / det
        bx, by = (Fraction(value, Q16) for value in translation)
        tx, ty = bx - s00 * bx - s01 * by, by - s10 * bx - s11 * by
        landmarks: list[dict[str, Any]] = []
        for landmark_index, (sx_q16, sy_q16) in enumerate(sources):
            sx, sy = Fraction(sx_q16, Q16), Fraction(sy_q16, Q16)
            target_x = _rha((s00 * sx + s01 * sy + tx).numerator * Q16,
                            (s00 * sx + s01 * sy + tx).denominator)
            target_y = _rha((s10 * sx + s11 * sy + ty).numerator * Q16,
                            (s10 * sx + s11 * sy + ty).denominator)
            # Aspect 63 deliberately records one weakly registered landmark.
            # Its low confidence is a compiled fact, not a runtime test hook.
            if address == 63 and landmark_index == 3:
                target_x += 2 * Q16
            landmarks.append(
                {
                    "landmark_id": f"cell_corner_{landmark_index}",
                    "source_q16_16": [sx_q16, sy_q16],
                    "target_q16_16": [target_x, target_y],
                }
            )
        aspects.append(
            {
                "address": address,
                "bin_u": u,
                "bin_v": v,
                "chart_matrix_q16_16": list(matrix),
                "chart_translation_q16_16": list(translation),
                "landmarks": landmarks,
                "region_representative_q16_16": [(2 * u + 1) * 4096, (2 * v + 1) * 4096],
            }
        )
    return {
        "aspect_count": EXPECTED_ASPECTS,
        "aspects": aspects,
        "chart_abi": "fl4512.d6_qv3.aspect_chart.v2",
        "correspondence_abi": "fl4512.d6_qv3.registered_landmarks.v2",
        "schema": AUTHORING_SCHEMA,
    }


def _validate_authoring(root: dict[str, Any]) -> list[dict[str, Any]]:
    """Require the authored chart's exact schema, rows, keys, and ranks."""
    _exact_keys(
        root,
        ("aspect_count", "aspects", "chart_abi", "correspondence_abi", "schema"),
        "authoring",
    )
    if (
        root["schema"] != AUTHORING_SCHEMA
        or root["chart_abi"] != "fl4512.d6_qv3.aspect_chart.v2"
        or root["correspondence_abi"]
        != "fl4512.d6_qv3.registered_landmarks.v2"
    ):
        raise CompileError("authoring_schema")
    _integer(root["aspect_count"], EXPECTED_ASPECTS, EXPECTED_ASPECTS, "aspect_count")
    rows = root["aspects"]
    if not isinstance(rows, list) or len(rows) != EXPECTED_ASPECTS:
        raise CompileError("aspect_cardinality")
    for expected_address, row in enumerate(rows):
        if not isinstance(row, dict):
            raise CompileError(f"aspect_not_object:{expected_address}")
        _exact_keys(
            row,
            (
                "address",
                "bin_u",
                "bin_v",
                "chart_matrix_q16_16",
                "chart_translation_q16_16",
                "landmarks",
                "region_representative_q16_16",
            ),
            f"aspect:{expected_address}",
        )
        if _integer(row["address"], 0, 63, "address") != expected_address:
            raise CompileError(f"aspect_order:{expected_address}")
        if _integer(row["bin_u"], 0, 7, "bin_u") != expected_address % 8:
            raise CompileError(f"aspect_bin_u:{expected_address}")
        if _integer(row["bin_v"], 0, 7, "bin_v") != expected_address // 8:
            raise CompileError(f"aspect_bin_v:{expected_address}")
        matrix = row["chart_matrix_q16_16"]
        translation = row["chart_translation_q16_16"]
        representative = row["region_representative_q16_16"]
        if not isinstance(matrix, list) or len(matrix) != 4:
            raise CompileError(f"matrix_shape:{expected_address}")
        if not isinstance(translation, list) or len(translation) != 2:
            raise CompileError(f"translation_shape:{expected_address}")
        if not isinstance(representative, list) or len(representative) != 2:
            raise CompileError(f"representative_shape:{expected_address}")
        matrix = [_integer(v, -(1 << 30), 1 << 30, "matrix") for v in matrix]
        if matrix[0] * matrix[3] - matrix[1] * matrix[2] == 0:
            raise CompileError(f"aspect_rank_loss:{expected_address}")
        landmarks = row["landmarks"]
        if not isinstance(landmarks, list) or len(landmarks) < 3:
            raise CompileError(f"landmark_cardinality:{expected_address}")
        ids: list[str] = []
        sources: list[tuple[int, int]] = []
        for landmark in landmarks:
            if not isinstance(landmark, dict):
                raise CompileError("landmark_not_object")
            _exact_keys(
                landmark,
                ("landmark_id", "source_q16_16", "target_q16_16"),
                f"landmark:{expected_address}",
            )
            if not isinstance(landmark["landmark_id"], str):
                raise CompileError("landmark_id")
            ids.append(landmark["landmark_id"])
            for coordinate_key in ("source_q16_16", "target_q16_16"):
                coordinate = landmark[coordinate_key]
                if not isinstance(coordinate, list) or len(coordinate) != 2:
                    raise CompileError(f"landmark_coordinate:{expected_address}")
                for value in coordinate:
                    _integer(value, -(1 << 30), 1 << 30, coordinate_key)
            sources.append(tuple(landmark["source_q16_16"]))
        if ids != sorted(set(ids)):
            raise CompileError(f"landmark_order_duplicate:{expected_address}")
        if not any(_cross(sources[0], sources[i], sources[j]) for i in range(1, len(sources)) for j in range(i + 1, len(sources))):
            raise CompileError(f"landmark_rank_loss:{expected_address}")
    return rows


def _compile_aspect(row: dict[str, Any]) -> dict[str, Any]:
    """Derive one Q8.24 chart-induced affine involution and its residuals."""
    matrix = [Fraction(value, Q16) for value in row["chart_matrix_q16_16"]]
    a00, a01, a10, a11 = matrix
    determinant_a = a00 * a11 - a01 * a10
    if determinant_a == 0:
        raise CompileError(f"aspect_rank_loss:{row['address']}")
    # A*diag(-1,1)*A^-1, expanded symbolically to avoid floating point.
    s = [
        (-a00 * a11 - a01 * a10) / determinant_a,
        2 * a00 * a01 / determinant_a,
        -2 * a10 * a11 / determinant_a,
        (a00 * a11 + a01 * a10) / determinant_a,
    ]
    bx, by = (Fraction(value, Q16) for value in row["chart_translation_q16_16"])
    translation = [bx - s[0] * bx - s[1] * by, by - s[2] * bx - s[3] * by]
    sq = [_q824(value, f"aspect:{row['address']}:matrix") for value in s]
    tq = [_q824(value, f"aspect:{row['address']}:translation") for value in translation]
    det_q24 = _rha(sq[0] * sq[3] - sq[1] * sq[2], Q24)
    if not -(1 << 31) <= det_q24 <= (1 << 31) - 1:
        raise CompileError(f"determinant_overflow:{row['address']}")

    fit_q16 = 0
    for landmark in row["landmarks"]:
        sx, sy = landmark["source_q16_16"]
        predicted_x_q16 = _rha(sq[0] * sx + sq[1] * sy, Q24) + _rha(tq[0] * Q16, Q24)
        predicted_y_q16 = _rha(sq[2] * sx + sq[3] * sy, Q24) + _rha(tq[1] * Q16, Q24)
        dx = predicted_x_q16 - landmark["target_q16_16"][0]
        dy = predicted_y_q16 - landmark["target_q16_16"][1]
        residual_q16 = _rha(dx * dx + dy * dy, Q16)
        fit_q16 = max(fit_q16, residual_q16)

    involution_q16 = 0
    for y in range(9):
        for x in range(9):
            xq, yq = x * Q24, y * Q24
            once_x = _rha(sq[0] * xq + sq[1] * yq, Q24) + tq[0]
            once_y = _rha(sq[2] * xq + sq[3] * yq, Q24) + tq[1]
            twice_x = _rha(sq[0] * once_x + sq[1] * once_y, Q24) + tq[0]
            twice_y = _rha(sq[2] * once_x + sq[3] * once_y, Q24) + tq[1]
            dx, dy = twice_x - xq, twice_y - yq
            residual_q16 = _rha(math.isqrt(dx * dx + dy * dy) * Q16, Q24)
            involution_q16 = max(involution_q16, residual_q16)

    column_x = abs(row["chart_matrix_q16_16"][0]) + abs(row["chart_matrix_q16_16"][2])
    column_y = abs(row["chart_matrix_q16_16"][1]) + abs(row["chart_matrix_q16_16"][3])
    q_short = _q016(min(column_x, column_y), max(column_x, column_y))
    bucket_fit = min(255, _rha(255 * min(fit_q16, 65536), 65536))
    bucket_involution = min(255, _rha(255 * min(involution_q16, 4096), 4096))
    q_fit = 65535 - bucket_fit * 257
    q_involution = 65535 - bucket_involution * 257
    q_relation = min(q_fit, q_involution, q_short)

    discrete_kind: str | None = None
    # Identifying exact axis-aligned maps is intentionally exhaustive over the
    # 64 pixel centers.  A name attached to the view cannot declare the result.
    for kind, reflection in (
        *((f"reflection_{i}", True) for i in range(4)),
        *((f"c4_{i}", False) for i in range(4)),
    ):
        transform_index = int(kind.rsplit("_", 1)[1])
        exact = True
        for y in range(8):
            for x in range(8):
                tx = _rha(sq[0] * x + sq[1] * y + tq[0], Q24)
                ty = _rha(sq[2] * x + sq[3] * y + tq[1], Q24)
                probe = _transform_mask(1 << (8 * y + x), transform_index, reflection)
                if probe != (1 << (8 * ty + tx) if 0 <= tx < 8 and 0 <= ty < 8 else 0):
                    exact = False
                    break
            if not exact:
                break
        if exact:
            discrete_kind = kind
            break

    return {
        "address": row["address"],
        "affine_matrix_q8_24": sq,
        "affine_translation_q8_24": tq,
        "chart_matrix_q16_16": row["chart_matrix_q16_16"],
        "chart_translation_q16_16": row["chart_translation_q16_16"],
        "compiled_relation_confidence_q0_16": q_relation,
        "determinant_q8_24": det_q24,
        "discrete_transform": discrete_kind,
        "fit_residual_q16_16": fit_q16,
        "foreshortening_confidence_q0_16": q_short,
        "involution_residual_q16_16": involution_q16,
        "landmarks": row["landmarks"],
        "region_representative_q16_16": row["region_representative_q16_16"],
    }


def _affine_mask(mask: int, aspect: dict[str, Any]) -> int:
    """Rasterize one mask through the quantized view affine map."""
    s = aspect["affine_matrix_q8_24"]
    t = aspect["affine_translation_q8_24"]
    result = 0
    for y in range(8):
        for x in range(8):
            if not _mask_bit(mask, x, y):
                continue
            nx = _rha(s[0] * x + s[1] * y + t[0], Q24)
            ny = _rha(s[2] * x + s[3] * y + t[1], Q24)
            if 0 <= nx < 8 and 0 <= ny < 8:
                result |= 1 << (8 * ny + nx)
    return result


def _byte_component(raw: int, cap: int, maximum: int) -> int:
    """Normalize one raw integer difference and apply its maximum contribution."""
    byte = min(255, _rha(255 * min(raw, cap), cap))
    return _rha(maximum * byte, 255)


def _relation(
    aspect: dict[str, Any],
    source: dict[str, Any],
    target: dict[str, Any],
) -> dict[str, Any]:
    """Compile all eighteen components for one ordered glyph pair."""
    source_id = source["glyph_id"]
    target_id = target["glyph_id"]
    if source_id == target_id:
        return {
            "affine_components": [0] * len(AFFINE_COMPONENTS),
            "aspect": aspect["address"],
            "compiled_transform_cost": 0,
            "from_glyph_id": source_id,
            "relation_confidence_q0_16": aspect["compiled_relation_confidence_q0_16"],
            "static_components": [0] * len(STATIC_COMPONENTS),
            "to_glyph_id": target_id,
        }

    transformed = _affine_mask(int(source["mask8_u64_hex"], 16), aspect)
    # A strongly oblique chart may clip every source pixel.  The empty
    # transformed raster has a legitimate all-zero descriptor for relation
    # pricing; substituting a plausible pixel would be a hidden fallback.
    transformed_descriptor = _descriptor(transformed, source_id, {})
    static_raw = (
        abs(transformed_descriptor["density"] - target["density"]),
        abs(transformed_descriptor["endpoints"] - target["endpoints"]),
        abs(transformed_descriptor["junctions"] - target["junctions"]),
        abs(transformed_descriptor["beta0"] - target["beta0"]),
        abs(transformed_descriptor["beta1"] - target["beta1"]),
        abs(transformed_descriptor["euler"] - target["euler"]),
        abs(transformed_descriptor["orientation_q1_15"][0] - target["orientation_q1_15"][0])
        + abs(transformed_descriptor["orientation_q1_15"][1] - target["orientation_q1_15"][1]),
        abs(transformed_descriptor["paths"] - target["paths"]),
        abs(transformed_descriptor["cycles"] - target["cycles"]),
        abs(transformed_descriptor["curvature_q0_16"] - target["curvature_q0_16"]),
        abs(transformed_descriptor["convexity_q0_16"] - target["convexity_q0_16"]),
    )
    target_mask = int(target["mask8_u64_hex"], 16)
    reflection_raw = min(
        (int(value, 16) ^ target_mask).bit_count()
        for value in source["reflection_masks_u64_hex"]
    )
    c4_raw = min(
        (int(value, 16) ^ target_mask).bit_count() for value in source["c4_masks_u64_hex"]
    )
    affine_raw = (
        (transformed ^ target_mask).bit_count(),
        reflection_raw,
        c4_raw,
        abs(aspect["determinant_q8_24"] + Q24),
        aspect["fit_residual_q16_16"],
        aspect["involution_residual_q16_16"],
        0,
    )
    static_components = [
        _byte_component(raw, cap, maximum)
        for raw, (_name, cap, maximum) in zip(static_raw, STATIC_COMPONENTS)
    ]
    affine_components = [
        _byte_component(raw, cap, maximum)
        for raw, (_name, cap, maximum) in zip(affine_raw, AFFINE_COMPONENTS)
    ]
    confidence = aspect["compiled_relation_confidence_q0_16"]
    explained = _rha(confidence * sum(affine_components), 65535)
    unexplained = _rha(200 * (65535 - confidence), 65535)
    cost = min(65535, sum(static_components) + explained + unexplained)
    return {
        "affine_components": affine_components,
        "aspect": aspect["address"],
        "compiled_transform_cost": cost,
        "from_glyph_id": source_id,
        "relation_confidence_q0_16": confidence,
        "static_components": static_components,
        "to_glyph_id": target_id,
    }


def compile_document() -> dict[str, Any]:
    """Compile and seal the complete 32 x 64 x 32 v2 document."""
    admission = _read_json(ADMISSION)
    registry = _read_json(REGISTRY)
    aoa = _read_json(AOA)
    authoring = _read_json(AUTHORING)
    authoring_rows = _validate_authoring(authoring)
    masks = _load_masks(admission, registry)
    glyph_ids = sorted(masks)
    receipt_ids = admission.get("receipt", {}).get("selected_glyph_ids")
    if glyph_ids != receipt_ids:
        raise CompileError("admitted_row_receipt_mismatch")

    descriptors = [_descriptor(masks[glyph_id], glyph_id, masks) for glyph_id in glyph_ids]
    aspects = [_compile_aspect(row) for row in authoring_rows]
    relations = [
        _relation(aspect, source, target)
        for aspect in aspects
        for source in descriptors
        for target in descriptors
    ]
    if len(relations) != EXPECTED_RELATIONS:
        raise CompileError("relation_cardinality")
    relation_keys = [
        (row["aspect"], row["from_glyph_id"], row["to_glyph_id"]) for row in relations
    ]
    if relation_keys != sorted(set(relation_keys)):
        raise CompileError("relation_order_duplicate")
    for row in relations:
        if row["from_glyph_id"] == row["to_glyph_id"] and (
            row["compiled_transform_cost"]
            or any(row["static_components"])
            or any(row["affine_components"])
        ):
            raise CompileError("identity_nonzero")

    font_payload = bytearray()
    font_chain = aoa.get("font_chain")
    if not isinstance(font_chain, list) or not font_chain:
        raise CompileError("font_chain_missing")
    for font in font_chain:
        path = ROOT / str(font.get("path"))
        data = path.read_bytes()
        if _sha_bytes(data) != font.get("sha256"):
            raise CompileError(f"font_hash:{path}")
        font_payload += len(data).to_bytes(8, "big") + data

    row_identity = {
        "selected_descriptor_hashes": admission["receipt"]["selected_descriptor_hashes"],
        "selected_glyph_ids": receipt_ids,
        "selected_row": admission["selected_row"],
    }
    raster_identity = {
        "executable_sha256": _sha_bytes(RASTER_SOURCE.read_bytes()),
        "options": {
            "background_connectivity": 4,
            "foreground_connectivity": 8,
            "page16_reduction": "coverage_ge_2.v1",
            "skeleton": "zhang_suen.v1",
        },
    }
    descriptor_hash = _sha_value(descriptors)
    aspect_hash = _sha_value(aspects)
    relation_hash = _sha_value(relations)
    calibration = [
        {"cap": cap, "maximum_contribution": maximum, "name": name}
        for name, cap, maximum in CALIBRATION
    ]
    document: dict[str, Any] = {
        "aspect_count": len(aspects),
        "aspect_sha256": aspect_hash,
        "aspects": aspects,
        "bound_sha256": {
            "admitted_row_sha256": _sha_value(row_identity),
            "aspect_asset_sha256": _sha_bytes(AUTHORING.read_bytes()),
            "atlas_registry_sha256": _sha_bytes(REGISTRY.read_bytes()),
            "descriptor_sha256": descriptor_hash,
            "font_bytes_sha256": _sha_bytes(bytes(font_payload)),
            "material_profile_sha256": _sha_bytes(PROFILE.read_bytes()),
            "raster_identity_sha256": _sha_value(raster_identity),
            "schema_sha256": _sha_bytes(SCHEMA.read_bytes()),
        },
        "component_calibration": calibration,
        "content_sha256": "",
        "descriptor_abi": DESCRIPTOR_ABI,
        "descriptor_count": len(descriptors),
        "descriptor_sha256": descriptor_hash,
        "descriptors": descriptors,
        "glyph_ids": glyph_ids,
        "relation_count": len(relations),
        "relation_sha256": relation_hash,
        "relations": relations,
        "relations_per_aspect": EXPECTED_GLYPHS * EXPECTED_GLYPHS,
        "schema": SCHEMA_ID,
        "version": 2,
    }
    preimage = dict(document)
    preimage.pop("content_sha256")
    document["content_sha256"] = _sha_value(preimage)
    return document


def validate_document(document: dict[str, Any]) -> None:
    """Strictly recompute the four folds and all canonical cardinality gates."""
    expected_keys = {
        "aspect_count", "aspect_sha256", "aspects", "bound_sha256",
        "component_calibration", "content_sha256", "descriptor_abi",
        "descriptor_count", "descriptor_sha256", "descriptors", "glyph_ids",
        "relation_count", "relation_sha256", "relations",
        "relations_per_aspect", "schema", "version",
    }
    _exact_keys(document, expected_keys, "compiled_document")
    if document["schema"] != SCHEMA_ID or document["version"] != 2:
        raise CompileError("compiled_schema")
    if document["descriptor_count"] != EXPECTED_GLYPHS or len(document["descriptors"]) != EXPECTED_GLYPHS:
        raise CompileError("compiled_descriptor_cardinality")
    if document["aspect_count"] != EXPECTED_ASPECTS or len(document["aspects"]) != EXPECTED_ASPECTS:
        raise CompileError("compiled_aspect_cardinality")
    if document["relation_count"] != EXPECTED_RELATIONS or len(document["relations"]) != EXPECTED_RELATIONS:
        raise CompileError("compiled_relation_cardinality")
    if document["relations_per_aspect"] != 1024:
        raise CompileError("compiled_relations_per_aspect")
    bound_keys = {
        "admitted_row_sha256", "aspect_asset_sha256",
        "atlas_registry_sha256", "descriptor_sha256", "font_bytes_sha256",
        "material_profile_sha256", "raster_identity_sha256", "schema_sha256",
    }
    _exact_keys(document["bound_sha256"], bound_keys, "compiled_bound_hashes")
    for key, value in document["bound_sha256"].items():
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise CompileError(f"compiled_bound_hash:{key}")
    glyph_ids = document["glyph_ids"]
    if glyph_ids != sorted(set(glyph_ids)) or len(glyph_ids) != EXPECTED_GLYPHS:
        raise CompileError("compiled_glyph_order")
    keys = [(r["aspect"], r["from_glyph_id"], r["to_glyph_id"]) for r in document["relations"]]
    if keys != sorted(set(keys)):
        raise CompileError("compiled_relation_order")
    for row in document["relations"]:
        _exact_keys(
            row,
            (
                "affine_components",
                "aspect",
                "compiled_transform_cost",
                "from_glyph_id",
                "relation_confidence_q0_16",
                "static_components",
                "to_glyph_id",
            ),
            "compiled_relation",
        )
        aspect_address = _integer(row["aspect"], 0, 63, "compiled_relation.aspect")
        _integer(row["from_glyph_id"], 1, 0xFFFFFFFF, "compiled_relation.from")
        _integer(row["to_glyph_id"], 1, 0xFFFFFFFF, "compiled_relation.to")
        _integer(
            row["relation_confidence_q0_16"], 0, 65535,
            "compiled_relation.confidence",
        )
        _integer(
            row["compiled_transform_cost"], 0, 65535,
            "compiled_relation.cost",
        )
        if len(row.get("static_components", [])) != 11 or len(row.get("affine_components", [])) != 7:
            raise CompileError("compiled_component_shape")
        for index, component in enumerate(row["static_components"]):
            _integer(
                component, 0, STATIC_COMPONENTS[index][2],
                f"compiled_relation.static:{index}",
            )
        for index, component in enumerate(row["affine_components"]):
            _integer(
                component, 0, AFFINE_COMPONENTS[index][2],
                f"compiled_relation.affine:{index}",
            )
        if (
            row["relation_confidence_q0_16"]
            != document["aspects"][aspect_address][
                "compiled_relation_confidence_q0_16"
            ]
        ):
            raise CompileError("compiled_relation_confidence")
        if row["from_glyph_id"] == row["to_glyph_id"] and (
            row["compiled_transform_cost"] or any(row["static_components"]) or any(row["affine_components"])
        ):
            raise CompileError("compiled_identity_nonzero")
    if _sha_value(document["descriptors"]) != document["descriptor_sha256"]:
        raise CompileError("descriptor_hash")
    if document["bound_sha256"].get("descriptor_sha256") != document["descriptor_sha256"]:
        raise CompileError("bound_descriptor_hash")
    if _sha_value(document["aspects"]) != document["aspect_sha256"]:
        raise CompileError("aspect_hash")
    if _sha_value(document["relations"]) != document["relation_sha256"]:
        raise CompileError("relation_hash")
    preimage = dict(document)
    expected_content_hash = preimage.pop("content_sha256")
    if _sha_value(preimage) != expected_content_hash:
        raise CompileError("content_hash")


def main() -> int:
    """Dispatch explicit bootstrap, write, and byte-for-byte check operations."""
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--bootstrap-authoring", action="store_true")
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    try:
        if arguments.bootstrap_authoring:
            AUTHORING.parent.mkdir(parents=True, exist_ok=True)
            AUTHORING.write_bytes(_canonical(_authoring_template()) + b"\n")
            print(f"wrote {AUTHORING.relative_to(ROOT)}")
            return 0
        document = compile_document()
        validate_document(document)
        canonical = _canonical(document) + b"\n"
        if arguments.write:
            OUTPUT.parent.mkdir(parents=True, exist_ok=True)
            OUTPUT.write_bytes(canonical)
            print(
                f"wrote {OUTPUT.relative_to(ROOT)} bytes={len(canonical)} "
                f"content_sha256={document['content_sha256']}"
            )
            return 0
        if not OUTPUT.exists() or OUTPUT.read_bytes() != canonical:
            raise CompileError("canonical_output_mismatch")
        print(
            f"checked descriptors={document['descriptor_count']} "
            f"aspects={document['aspect_count']} relations={document['relation_count']} "
            f"content_sha256={document['content_sha256']}"
        )
        return 0
    except (CompileError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
