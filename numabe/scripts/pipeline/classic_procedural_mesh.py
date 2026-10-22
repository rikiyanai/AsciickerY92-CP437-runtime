 #!/usr/bin/env python3
"""Shared deterministic Classic mesh construction primitives.

The functions here know the AKM vertex contract and reusable fixture shapes.
They do not select locale policy, canonical truth, or site-specific placement.
"""

from __future__ import annotations

import math
from typing import Any

from scripts.pipeline.compile_facade_shell import ALPHA_NO_COLLISION


# Compact deterministic 5x7 capitals for source-backed place identity.  These
# cells become ordinary Classic vertex-colour geometry; they add no UV or
# runtime text dependency and never claim the photographed sign's typeface.
_BLOCK_FONT_5X7 = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
}


def _add_box(
    vertices: list[tuple],
    faces: list[list[int]],
    bounds: tuple[float, float, float, float, float, float],
    rgb: tuple[int, int, int],
    scale: float,
) -> None:
    x0, x1, y0, y1, z0, z1 = (value * scale for value in bounds)
    planes = (
        ((-1.0, 0.0, 0.0), ((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0))),
        ((1.0, 0.0, 0.0), ((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1))),
        ((0.0, -1.0, 0.0), ((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1))),
        ((0.0, 1.0, 0.0), ((x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0))),
        ((0.0, 0.0, -1.0), ((x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0))),
        ((0.0, 0.0, 1.0), ((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))),
    )
    for normal, points in planes:
        first = len(vertices)
        vertices.extend((*point, *normal, *rgb, ALPHA_NO_COLLISION) for point in points)
        faces.append([first, first + 1, first + 2, first + 3])


def _add_polygon_prism_xz(
    vertices: list[tuple],
    faces: list[list[int]],
    points_xz: list[tuple[float, float]],
    *,
    half_depth_m: float,
    rgb: tuple[int, int, int],
    scale: float,
) -> None:
    """Add a facade-facing polygon prism with its presentation face on -Y."""
    if len(points_xz) < 3:
        raise ValueError("polygon prism needs at least three points")
    points = [(x * scale, z * scale) for x, z in points_xz]
    depth = half_depth_m * scale
    front = len(vertices)
    vertices.extend((x, -depth, z, 0.0, -1.0, 0.0, *rgb, ALPHA_NO_COLLISION)
                    for x, z in points)
    # Classic's AKM loader accepts only lines, triangles, and quads.  These
    # presentation plates are convex, so triangulate each cap as a fan rather
    # than writing an n-gon that the Python inspector accepts but Classic
    # rejects at runtime.
    for index in range(1, len(points) - 1):
        faces.append([front, front + index, front + index + 1])
    back = len(vertices)
    vertices.extend((x, depth, z, 0.0, 1.0, 0.0, *rgb, ALPHA_NO_COLLISION)
                    for x, z in reversed(points))
    for index in range(1, len(points) - 1):
        faces.append([back, back + index, back + index + 1])
    for first_point, second_point in zip(points, [*points[1:], points[0]]):
        x0, z0 = first_point
        x1, z1 = second_point
        dx, dz = x1 - x0, z1 - z0
        length = math.hypot(dx, dz) or 1.0
        normal = (dz / length, 0.0, -dx / length)
        first = len(vertices)
        vertices.extend((x, y, z, *normal, *rgb, ALPHA_NO_COLLISION) for x, y, z in (
            (x0, -depth, z0), (x1, -depth, z1),
            (x1, depth, z1), (x0, depth, z0),
        ))
        faces.append([first, first + 1, first + 2, first + 3])


def _add_block_text(
    vertices: list[tuple],
    faces: list[list[int]],
    text: str,
    *,
    max_width_m: float,
    z_min_m: float,
    z_max_m: float,
    rgb: tuple[int, int, int],
    scale: float,
    y_min_m: float = -0.145,
    y_max_m: float = -0.130,
) -> str | None:
    """Add a centred source-identity label using 5x7 facade-local blocks."""
    normalized = " ".join(text.upper().split())
    if not normalized or any(char != " " and char not in _BLOCK_FONT_5X7 for char in normalized):
        return None
    columns = len(normalized) * 6 - 1
    cell = min((z_max_m - z_min_m) / 7.0, max_width_m / columns)
    if cell < 0.025:
        return None
    text_width = columns * cell
    x0 = -text_width / 2.0
    for char_index, char in enumerate(normalized):
        if char == " ":
            continue
        for row, bits in enumerate(_BLOCK_FONT_5X7[char]):
            for column, bit in enumerate(bits):
                if bit != "1":
                    continue
                left = x0 + (char_index * 6 + column) * cell
                top = z_max_m - row * cell
                _add_box(
                    vertices,
                    faces,
                    (left, left + cell * 0.82, y_min_m, y_max_m,
                     top - cell * 0.82, top),
                    rgb,
                    scale,
                )
    return normalized


def _block_text_boxes(
    text: str,
    *,
    max_width_m: float,
    z_min_m: float,
    z_max_m: float,
    y_min_m: float,
    y_max_m: float,
) -> tuple[str | None, list[tuple[float, float, float, float, float, float]]]:
    """Return facade-local boxes for a readable 5x7 identity label."""
    normalized = " ".join(text.upper().split())
    if not normalized or any(char != " " and char not in _BLOCK_FONT_5X7 for char in normalized):
        return None, []
    columns = len(normalized) * 6 - 1
    cell = min((z_max_m - z_min_m) / 7.0, max_width_m / columns)
    if cell < 0.025:
        return None, []
    text_width = columns * cell
    x0 = -text_width / 2.0
    boxes = []
    for char_index, char in enumerate(normalized):
        if char == " ":
            continue
        for row, bits in enumerate(_BLOCK_FONT_5X7[char]):
            for column, bit in enumerate(bits):
                if bit != "1":
                    continue
                left = x0 + (char_index * 6 + column) * cell
                top = z_max_m - row * cell
                boxes.append((left, left + cell * .82, y_min_m, y_max_m,
                              top - cell * .82, top))
    return normalized, boxes


# FL-4809 NUMABE-METRIC-01: physical metre contracts per archetype subtype.
# Intended extents measured from _build_archetype output at scale 1.0, where
# _add_box bounds are metres. Anchor is the ground centre (builders start at
# z 0 with roughly centred x/y); orientation is yaw-only; PS1 simplification
# never grants metric distortion, so allowed scales stay near 1 for
# standardized objects and widen only for grown/assembled ones (tree, pole).
ARCHETYPE_PHYSICAL = {
    # subtype: (semantic_family, intended_w_m, intended_d_m, intended_h_m,
    #          scale_min, scale_max)
    "traffic_signal": ("traffic_signal", 0.44, 0.32, 3.05, 0.85, 1.15),
    "post_box": ("post_box", 0.68, 0.545, 1.55, 0.85, 1.15),
    "stop_sign": ("stop_sign_panel", 0.84, 0.144, 2.26, 0.85, 1.15),
    "information_board": ("information_board", 1.76, 0.38, 2.12, 0.85, 1.15),
    "public_telephone": ("public_telephone", 0.68, 0.59, 1.85, 0.85, 1.15),
    "bicycle_parking": ("bicycle_parking", 1.58, 0.8, 0.78, 0.85, 1.15),
    "bench": ("bench", 1.6, 0.47, 0.95, 0.85, 1.15),
    "vending_machine": ("vending_machine", 1.1, 0.83, 1.85, 0.85, 1.15),
    "waste_basket": ("waste_basket", 0.5, 0.51, 0.8, 0.85, 1.15),
    "drinking_water": ("drinking_water", 0.6, 0.5, 0.9, 0.85, 1.15),
    "shelter": ("shelter", 3.2, 1.6, 2.45, 0.85, 1.15),
    "clock": ("clock", 0.6, 0.2, 2.7, 0.8, 1.25),
    "fountain": ("fountain", 1.3, 1.3, 0.32, 0.8, 1.25),
    "street_lamp": ("street_lamp", 0.75, 0.4, 5.0, 0.8, 1.25),
    "bus_stop": ("bus_stop", 0.5, 0.12, 2.2, 0.85, 1.15),
    "utility_pole": ("utility_pole", 1.6, 0.3, 7.0, 0.8, 1.25),
    "pedestrian_gate": ("gate_barrier", 2.0, 0.16, 1.1, 0.8, 1.25),
    "lift_gate": ("gate_barrier", 2.9, 0.2, 1.2, 0.8, 1.25),
    "cycle_barrier": ("gate_barrier", 1.5, 0.12, 1.05, 0.8, 1.25),
    "bollard": ("bollard", 0.22, 0.22, 0.86, 0.85, 1.15),
    "turnstile": ("gate_barrier", 1.44, 0.16, 1.15, 0.8, 1.25),
    "railway_signal": ("railway_signal", 0.4, 0.26, 3.2, 0.85, 1.15),
    "railway_switch": ("railway_ground_equipment", 0.68, 0.24, 0.18, 0.8, 1.25),
    "railway_buffer_stop": ("railway_buffer_stop", 1.44, 0.32, 0.75, 0.8, 1.25),
    "railway_derailer": ("railway_ground_equipment", 0.6, 0.32, 0.22, 0.8, 1.25),
    "flagpole": ("flagpole", 0.8, 0.1, 5.0, 0.8, 1.25),
    "street_cabinet": ("street_cabinet", 0.84, 0.56, 1.25, 0.85, 1.15),
    "fire_hydrant": ("fire_hydrant", 0.48, 0.28, 0.65, 0.85, 1.15),
    "traffic_sign": ("stop_sign_panel", 0.68, 0.12, 2.26, 0.85, 1.15),
    "advertising_sign": ("advertising_sign", 1.1, 0.16, 1.3, 0.8, 1.25),
    "pedestrian_crossing_marker": ("pedestrian_crossing_marker", 1.5, 1.24, 0.03, 0.5, 2.0),
    "tree": ("tree", 1.7, 1.7, 3.55, 0.7, 1.5),
    "regulatory_sign": ("stop_sign_panel", 0.68, 0.12, 2.26, 0.85, 1.15),
    "warning_sign": ("stop_sign_panel", 0.8, 0.14, 2.3, 0.85, 1.15),
    "store_signboard": ("advertising_sign", 1.6, 0.12, 2.8, 0.8, 1.25),
    "manhole_cover": ("manhole_cover", 0.6, 0.6, 0.035, 0.85, 1.15),
    "traffic_cone": ("traffic_cone", 0.36, 0.36, 0.7, 0.85, 1.15),
    "banner_sign": ("advertising_sign", 0.7, 0.1, 2.9, 0.8, 1.25),
    "catch_basin_grate": ("catch_basin_grate", 0.6, 0.4, 0.06, 0.85, 1.15),
    "driveway_apron": ("paver", 2.5, 2.0, 0.08, 0.5, 2.0),
    "generic_sign_plate": ("notice_panel", 0.6, 0.1, 2.2, 0.8, 1.25),
    "water_valve_cover": ("manhole_cover", 0.15, 0.15, 0.05, 0.85, 1.15),
}


def _build_archetype(
    subtype: str,
    scale: float,
    parameters: dict[str, Any] | None = None,
) -> tuple[list[tuple], list[list[int]]]:
    """Build one reusable archetype from declarative locale-pack parameters."""
    parameters = parameters or {}
    rgb = lambda key, fallback: tuple(parameters.get(key, fallback))
    vertices: list[tuple] = []
    faces: list[list[int]] = []
    if subtype == "traffic_signal":
        pole_rgb = rgb("pole_rgb", (92, 96, 92))
        housing_rgb = rgb("housing_rgb", (48, 52, 48))
        _add_box(vertices, faces, (-0.07, 0.07, -0.07, 0.07, 0.0, 2.7), pole_rgb, scale)
        _add_box(vertices, faces, (-0.22, 0.22, -0.14, 0.14, 2.15, 3.05), housing_rgb, scale)
        for z, colour in ((2.83, rgb("red_rgb", (208, 44, 36))),
                          (2.59, rgb("amber_rgb", (224, 176, 36))),
                          (2.35, rgb("green_rgb", (48, 168, 72)))):
            _add_box(
                vertices, faces,
                (-0.13, 0.13, -0.18, -0.14, z - 0.09, z + 0.09),
                colour, scale,
            )
    elif subtype == "post_box":
        red = rgb("body_rgb", (188, 37, 42))
        dark = rgb("slot_rgb", (55, 45, 42))
        pole = rgb("pole_rgb", (104, 108, 104))
        # JP urban post boxes read as a compact red cabinet mounted above a
        # narrow support.  The prior ground-level red block failed the
        # FL-4809 Classic visual review.
        _add_box(vertices, faces, (-0.075, 0.075, -0.075, 0.075, 0.0, 0.92), pole, scale)
        _add_box(vertices, faces, (-0.31, 0.31, -0.24, 0.24, 0.72, 1.48), red, scale)
        _add_box(vertices, faces, (-0.34, 0.34, -0.27, 0.27, 1.44, 1.55), red, scale)
        _add_box(vertices, faces, (-0.22, 0.22, -0.275, -0.24, 1.16, 1.28), dark, scale)
    elif subtype == "stop_sign":
        pole = rgb("pole_rgb", (118, 122, 118))
        border = rgb("border_rgb", (239, 238, 224))
        fill = rgb("fill_rgb", (197, 42, 43))
        _add_box(vertices, faces, (-0.045, 0.045, -0.045, 0.045, 0.0, 1.72), pole, scale)
        _add_polygon_prism_xz(
            vertices, faces,
            [(-0.42, 2.26), (0.42, 2.26), (0.0, 1.46)],
            half_depth_m=0.065, rgb=border, scale=scale,
        )
        _add_polygon_prism_xz(
            vertices, faces,
            [(-0.34, 2.18), (0.34, 2.18), (0.0, 1.56)],
            half_depth_m=0.072, rgb=fill, scale=scale,
        )
    elif subtype == "information_board":
        timber = rgb("timber_rgb", (166, 132, 87))
        panel = rgb("panel_rgb", (209, 190, 151))
        roof = rgb("roof_rgb", (91, 71, 49))
        for x in (-0.62, 0.62):
            _add_box(vertices, faces, (x - 0.065, x + 0.065, -0.08, 0.08, 0.0, 1.78), timber, scale)
        _add_box(vertices, faces, (-0.73, 0.73, -0.07, 0.07, 0.78, 1.72), panel, scale)
        _add_box(vertices, faces, (-0.76, 0.76, -0.09, 0.09, 0.72, 0.80), timber, scale)
        _add_polygon_prism_xz(
            vertices, faces,
            [(-0.88, 1.72), (0.88, 1.72), (0.0, 2.12)],
            half_depth_m=0.19, rgb=roof, scale=scale,
        )
    elif subtype == "public_telephone":
        _add_box(vertices, faces, (-0.34, 0.34, -0.28, 0.28, 0.0, 1.85), (92, 132, 102), scale)
        _add_box(vertices, faces, (-0.25, 0.25, -0.31, -0.28, 0.52, 1.55), (150, 190, 174), scale)
    elif subtype == "bicycle_parking":
        for x in (-0.75, 0.0, 0.75):
            _add_box(
                vertices, faces,
                (x - 0.04, x + 0.04, -0.40, -0.32, 0.0, 0.78),
                (116, 122, 126), scale,
            )
            _add_box(
                vertices, faces,
                (x - 0.04, x + 0.04, 0.32, 0.40, 0.0, 0.78),
                (116, 122, 126), scale,
            )
            _add_box(
                vertices, faces,
                (x - 0.04, x + 0.04, -0.32, 0.32, 0.70, 0.78),
                (116, 122, 126), scale,
            )
    elif subtype in {"regulatory_sign", "warning_sign"}:
        # NUMABE-ASSET-01: JP roadside plates. Shape comes from template
        # parameters (round regulatory by default, triangular warning);
        # the emitter passes only template parameters, never per-row tags.
        pole = rgb("pole_rgb", (118, 122, 118))
        shape = str(parameters.get("plate_shape",
                    "round" if subtype == "regulatory_sign" else "triangle"))
        if shape == "round":
            rim = rgb("rim_rgb", (197, 42, 43))
            face = rgb("face_rgb", (239, 238, 224))
            _add_box(vertices, faces, (-0.05, 0.05, -0.05, 0.05, 0.0, 1.7), pole, scale)
            octagon = [(0.34 * math.cos(k * math.pi / 4),
                        1.92 + 0.34 * math.sin(k * math.pi / 4)) for k in range(8)]
            inner = [(0.28 * math.cos(k * math.pi / 4),
                      1.92 + 0.28 * math.sin(k * math.pi / 4)) for k in range(8)]
            _add_polygon_prism_xz(vertices, faces, octagon, half_depth_m=0.06, rgb=rim, scale=scale)
            _add_polygon_prism_xz(vertices, faces, inner, half_depth_m=0.06, rgb=face, scale=scale)
        else:
            rim = rgb("rim_rgb", (58, 58, 58))
            face = rgb("face_rgb", (224, 176, 36))
            _add_box(vertices, faces, (-0.05, 0.05, -0.05, 0.05, 0.0, 1.6), pole, scale)
            _add_polygon_prism_xz(
                vertices, faces,
                [(-0.4, 2.3), (0.4, 2.3), (0.0, 1.5)],
                half_depth_m=0.07, rgb=rim, scale=scale,
            )
            _add_polygon_prism_xz(
                vertices, faces,
                [(-0.32, 2.22), (0.32, 2.22), (0.0, 1.6)],
                half_depth_m=0.07, rgb=face, scale=scale,
            )
    elif subtype in {"bench","vending_machine","waste_basket","drinking_water",
                     "shelter","clock","fountain","street_lamp","bus_stop","utility_pole",
                     "pedestrian_gate","lift_gate","cycle_barrier","bollard",
                     "turnstile","railway_signal","railway_switch","railway_buffer_stop",
                     "railway_derailer","flagpole","street_cabinet","fire_hydrant",
                     "traffic_sign","advertising_sign",
                     "pedestrian_crossing_marker","tree",
                     "store_signboard","manhole_cover","traffic_cone","banner_sign",
                     "catch_basin_grate","driveway_apron","generic_sign_plate",
                     "water_valve_cover"}:
        # does not disappear because it lacked one of the original four meshes.
        grey=(120,124,119); dark=(55,62,65); wood=(132,105,76)
        parts={
            'bench': [((-.8,.8,-.22,.22,.4,.5),wood),((-.8,.8,.17,.25,.5,.95),wood),
                      ((-.65,-.55,-.18,.18,0,.4),grey),((.55,.65,-.18,.18,0,.4),grey)],
            'vending_machine':[((-.55,.55,-.4,.4,0,1.85),(220,218,204)),
                               ((-.45,.30,-.43,-.4,.75,1.7),(86,142,163)),
                               ((.33,.48,-.43,-.4,.75,1.2),dark)],
            'waste_basket':[((-.25,.25,-.25,.25,0,.8),grey),((-.18,.18,-.26,-.25,.55,.7),dark)],
            'drinking_water':[((-.18,.18,-.18,.18,0,.8),grey),((-.3,.3,-.25,.25,.8,.9),grey)],
            'shelter':[((-1.5,-1.4,-.6,.6,0,2.3),grey),((1.4,1.5,-.6,.6,0,2.3),grey),
                       ((-1.6,1.6,-.8,.8,2.3,2.45),dark)],
            'clock':[((-.06,.06,-.06,.06,0,2.4),grey),((-.3,.3,-.1,.1,2.1,2.7),(213,211,196))],
            'fountain':[((-.65,.65,-.65,.65,0,.3),grey),((-.5,.5,-.5,.5,.3,.32),(85,133,148))],
            'street_lamp':[((-.07,.07,-.07,.07,0,5),grey),((-.15,.6,-.2,.2,4.8,5),(215,205,166))],
            'bus_stop':[((-.04,.04,-.04,.04,0,2),grey),((-.25,.25,-.06,.06,1.5,2.2),(206,209,189))],
            'utility_pole':[((-.15,.15,-.15,.15,0,7),grey),((-.8,.8,-.06,.06,6.5,6.6),dark)],
            'stop_sign':[((-.045,.045,-.045,.045,0,2.0),grey),
                         ((-.34,.34,-.06,.06,1.55,2.18),(192,52,48))],
            'pedestrian_gate':[((-1.0,-.92,-.08,.08,0,1.1),grey),
                               ((.92,1.0,-.08,.08,0,1.1),grey),
                               ((-1.0,1.0,-.06,.06,.52,.62),grey)],
            'lift_gate':[((-.10,.10,-.10,.10,0,1.2),grey),
                         ((0,2.8,-.07,.07,.88,1.02),(202,167,38))],
            'cycle_barrier':[((-.75,-.67,-.06,.06,0,1.05),grey),
                             ((.67,.75,-.06,.06,0,1.05),grey),
                             ((-.75,.75,-.05,.05,.72,.82),grey)],
            'bollard':[((-.09,.09,-.09,.09,0,.86),grey),
                       ((-.11,.11,-.11,.11,.70,.82),(210,180,54))],
            'turnstile':[((-.08,.08,-.08,.08,0,1.15),grey),
                         ((-.72,.72,-.04,.04,.78,.86),grey)],
            'railway_signal':[((-.07,.07,-.07,.07,0,3.2),grey),
                              ((-.20,.20,-.13,.13,2.48,3.15),dark)],
            'railway_switch':[((-.34,.34,-.12,.12,0,.18),grey)],
            'railway_buffer_stop':[((-.72,.72,-.16,.16,0,.75),dark)],
            'railway_derailer':[((-.30,.30,-.16,.16,0,.22),(196,158,38))],
            'flagpole':[((-.05,.05,-.05,.05,0,5.0),grey),
                        ((0,.75,-.04,.04,3.75,4.65),(202,205,194))],
            'street_cabinet':[((-.42,.42,-.28,.28,0,1.25),(132,142,132))],
            'fire_hydrant':[((-.14,.14,-.14,.14,0,.65),(176,52,43)),
                            ((-.24,.24,-.10,.10,.38,.53),(176,52,43))],
            'information_board':[((-.05,.05,-.05,.05,0,2.1),grey),
                                 ((-.62,.62,-.06,.06,1.0,2.15),(184,185,171))],
            'traffic_sign':[((-.05,.05,-.05,.05,0,2.2),grey),
                            ((-.34,.34,-.06,.06,1.62,2.26),(194,194,184))],
            'advertising_sign':[((-.55,.55,-.08,.08,.15,1.45),(194,194,184))],
            'store_signboard':[((-.06,.06,-.06,.06,0,1.95),grey),
                              ((-.8,.8,-.06,.06,1.9,2.8),(206,200,170))],
            'manhole_cover':[((-.3,.3,-.3,.3,0,.03),(104,108,110)),
                            ((-.22,.22,-.22,.22,.03,.035),(55,58,60))],
            'traffic_cone':[((-.18,.18,-.18,.18,0,.05),(224,110,40)),
                           ((-.13,.13,-.13,.13,.05,.32),(224,110,40)),
                           ((-.135,.135,-.135,.135,.30,.40),(232,232,225)),
                           ((-.09,.09,-.09,.09,.38,.55),(224,110,40)),
                           ((-.05,.05,-.05,.05,.55,.70),(224,110,40))],
            'banner_sign':[((-.05,.05,-.05,.05,0,2.9),grey),
                          ((-.35,.35,-.05,.05,1.0,2.8),(178,52,48))],
            'catch_basin_grate':[((-.3,.3,-.2,.2,0,.06),(132,136,130)),
                                ((-.26,.26,-.16,.16,.05,.06),(45,50,52))],
            'driveway_apron':[((-1.25,1.25,-1.0,1.0,0,.08),(150,148,140))],
            'generic_sign_plate':[((-.045,.045,-.045,.045,0,2.2),grey),
                                 ((-.3,.3,-.05,.05,1.7,2.2),(194,194,184))],
            'water_valve_cover':[((-.075,.075,-.075,.075,0,.05),(104,108,110))],
            'pedestrian_crossing_marker':[
                ((-.75,.75,-.10,.10,.02,.05),(224,224,213)),
                ((-.75,.75,-.62,-.42,.02,.05),(224,224,213)),
                ((-.75,.75,.42,.62,.02,.05),(224,224,213)),
            ],
            'tree':[((-.12,.12,-.12,.12,0,2.2),(91,70,52)),
                    ((-.85,.85,-.85,.85,1.55,3.55),(67,105,58))],
        }
        for bounds,rgb in parts[subtype]: _add_box(vertices,faces,bounds,rgb,scale)
    else:
        raise ValueError(f"unsupported fixture subtype: {subtype}")
    return vertices, faces
