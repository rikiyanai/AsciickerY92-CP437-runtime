#!/usr/bin/env python3
"""PS1 street-frontage vocabulary: the members that make a facade read Japanese.

WHY THIS EXISTS
---------------
``compile_facade_shell.build_shell`` produces a correct wall shell: storeys,
bays, glazing, an entrance bay and a parapet cap.  Compared against 1990s
Japanese street reference (ZFactorPSX "90s Japanese Alleyway" PS1/PSX kit,
fetched 2026-09-23), that shell is missing every member that carries the
street's identity, so it reads as a generic modernist block:

    reference member                     shell has it?
    continuous awning / canopy band      no
    vertical signage tower at a corner    no
    perpendicular projecting blade sign   no
    rolling storefront shutter            no
    roof clutter (AC, tank, railing)      no

This module owns exactly those five members and nothing else.  It does not
author walls, bays, glazing or entrances -- ``build_shell`` remains the wall
owner, and this module only appends frontage dressing to the vertex/face lists
that ``build_shell`` returned.

UNITS
-----
Every bound below is expressed in METRES and multiplied by ``scale`` on the way
in, matching ``classic_procedural_mesh._add_box``.  ``build_shell`` emits local
Z already multiplied by the same content scale, so dressing and shell share one
local frame: X and Y are world units relative to the footprint centroid, Z is
world units above local ground.

FRONTAGE FRAME
--------------
The caller passes the frontage face as ``front_y_m`` (the local Y of the wall
the street sees) and ``out_sign`` (-1 when the frontage faces -Y, +1 when it
faces +Y).  Every member is then placed relative to that face, so the same code
dresses either side of a street.

ALPHA
-----
All dressing uses ``ALPHA_NO_COLLISION`` for the same reason the shell does:
this is presentation geometry, and mass containment owns collision.
"""

from __future__ import annotations

from scripts.pipeline.classic_procedural_mesh import _add_box


def _mix(a, b, t):
    """Blend two RGB triples, matching compile_facade_shell._mix."""
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def _quantise(rgb):
    """Snap to the terminal-safe 6x6x6 cube so Classic needs no dithering."""
    levels = (0, 51, 102, 153, 204, 255)
    return tuple(min(levels, key=lambda L: abs(L - channel)) for channel in rgb)


def add_awning(vertices, faces, *, width_m, front_y_m, out_sign, head_m,
               awning_rgb, fascia_rgb, scale, projection_m=1.30):
    """Continuous canopy over the shopfront, plus the sign fascia under it.

    This is the single strongest signature in the reference street: an
    unbroken horizontal band at first-floor height running the full frontage,
    with a coloured fascia carrying shop text.  Without it a Japanese street
    reads as an office block.
    """
    half = width_m / 2.0 * 0.98
    y_in = front_y_m + 0.05 * out_sign
    y_out = front_y_m + projection_m * out_sign
    y0, y1 = (y_out, y_in) if out_sign < 0 else (y_in, y_out)

    # Canopy deck, sloping is not worth the triangles at this scale.
    _add_box(vertices, faces, (-half, half, y0, y1, head_m - 0.10, head_m + 0.06),
             awning_rgb, scale)
    # Fascia hanging off the outer edge: this is the band that reads as signage.
    fy0, fy1 = ((y_out, y_out + 0.16) if out_sign > 0
                else (y_out - 0.16, y_out))
    _add_box(vertices, faces, (-half, half, fy0, fy1, head_m - 0.62, head_m + 0.06),
             fascia_rgb, scale)
    # Underside shadow strip, so the canopy reads as solid from below.
    _add_box(vertices, faces, (-half, half, y0, y1,
                               head_m - 0.16, head_m - 0.10),
             _quantise(_mix(awning_rgb, (0, 0, 0), 0.45)), scale)


def add_sign_tower(vertices, faces, *, width_m, front_y_m, out_sign, base_m,
                   roof_m, sign_rgb, frame_rgb, scale):
    """Vertical signage column at the frontage corner, running past the roof.

    Reference streets stack tenant signs in a tall narrow tower at a building
    corner; it is what gives the skyline its vertical rhythm.
    """
    x1 = width_m / 2.0 - 0.10
    x0 = x1 - 0.85
    y_in = front_y_m
    y_out = front_y_m + 0.55 * out_sign
    y0, y1 = (y_out, y_in) if out_sign < 0 else (y_in, y_out)
    top = roof_m + 1.9

    _add_box(vertices, faces, (x0, x1, y0, y1, base_m, top), frame_rgb, scale)
    # Individual lit tenant panels, inset so the frame reads as a surround.
    panel_h = 1.05
    z = base_m + 0.35
    alternate = True
    while z + panel_h < top - 0.3:
        rgb = sign_rgb if alternate else _quantise(_mix(sign_rgb, (255, 255, 255), 0.45))
        inset_y0, inset_y1 = ((y0 - 0.06, y1) if out_sign < 0
                              else (y0, y1 + 0.06))
        _add_box(vertices, faces,
                 (x0 + 0.10, x1 - 0.10, inset_y0, inset_y1, z, z + panel_h),
                 rgb, scale)
        z += panel_h + 0.22
        alternate = not alternate


def add_blade_sign(vertices, faces, *, width_m, front_y_m, out_sign, head_m,
                   blade_rgb, scale, projection_m=1.9):
    """Sign projecting perpendicular to the wall, read from along the street.

    A frontage-parallel fascia is invisible when you walk the street; the
    perpendicular blade is what a pedestrian actually reads, which is why the
    reference is full of them.
    """
    x0 = -width_m / 2.0 + 0.35
    x1 = x0 + 0.22
    y_in = front_y_m + 0.05 * out_sign
    y_out = front_y_m + projection_m * out_sign
    y0, y1 = (y_out, y_in) if out_sign < 0 else (y_in, y_out)
    z0 = head_m + 0.55
    _add_box(vertices, faces, (x0, x1, y0, y1, z0, z0 + 1.75), blade_rgb, scale)
    # Mounting bracket back to the wall.
    by0, by1 = ((front_y_m - 0.28 * 1, front_y_m) if out_sign < 0
                else (front_y_m, front_y_m + 0.28))
    _add_box(vertices, faces, (x0 + 0.04, x1 - 0.04, by0, by1,
                               z0 + 0.7, z0 + 0.95),
             _quantise(_mix(blade_rgb, (0, 0, 0), 0.5)), scale)


def add_shutter(vertices, faces, *, width_m, front_y_m, out_sign,
                shutter_rgb, scale, sill_m=0.15, head_m=2.35, bays=5):
    """Rolling security shutter over part of the shopfront, with slat ribs.

    A closed shutter is how half a real street looks, and the horizontal slat
    ribs are the detail that stops a ground floor reading as blank wall.
    """
    span = width_m * 0.34
    x0 = -width_m / 2.0 + width_m * 0.56
    x1 = min(x0 + span, width_m / 2.0 - 0.25)
    y_in = front_y_m
    y_out = front_y_m + 0.10 * out_sign
    y0, y1 = (y_out, y_in) if out_sign < 0 else (y_in, y_out)

    _add_box(vertices, faces, (x0, x1, y0, y1, sill_m, head_m),
             shutter_rgb, scale)
    # Slat ribs: thin proud bands, alternating shade.
    rib = _quantise(_mix(shutter_rgb, (0, 0, 0), 0.42))
    ry0, ry1 = ((y0 - 0.04, y0) if out_sign < 0 else (y1, y1 + 0.04))
    step = (head_m - sill_m) / max(1, bays * 2)
    z = sill_m + step
    while z < head_m - step:
        _add_box(vertices, faces, (x0 + 0.03, x1 - 0.03, ry0, ry1,
                                   z, z + step * 0.45), rib, scale)
        z += step * 2.0
    # Shutter box housing at the head.
    _add_box(vertices, faces, (x0 - 0.08, x1 + 0.08, ry0 - 0.06 if out_sign < 0 else y0,
                               y1 if out_sign < 0 else ry1 + 0.06,
                               head_m, head_m + 0.30),
             _quantise(_mix(shutter_rgb, (255, 255, 255), 0.22)), scale)


def add_roof_clutter(vertices, faces, *, width_m, depth_m, roof_m,
                     plant_rgb, rail_rgb, scale):
    """AC condensers, a water tank and a parapet rail on the roof.

    Reference roofs are never clean planes; the clutter is what makes a
    rooftop read as inhabited rather than as the top of an extruded polygon.
    """
    hw, hd = width_m / 2.0, depth_m / 2.0
    # Parapet rail around the roof edge.
    t = 0.09
    for bounds in (
        (-hw, hw, hd - t, hd, roof_m, roof_m + 0.55),
        (-hw, hw, -hd, -hd + t, roof_m, roof_m + 0.55),
        (-hw, -hw + t, -hd, hd, roof_m, roof_m + 0.55),
        (hw - t, hw, -hd, hd, roof_m, roof_m + 0.55),
    ):
        _add_box(vertices, faces, bounds, rail_rgb, scale)

    # Two condenser units.
    for cx in (-width_m * 0.22, width_m * 0.16):
        _add_box(vertices, faces,
                 (cx - 0.55, cx + 0.55, -0.45, 0.45, roof_m, roof_m + 0.78),
                 plant_rgb, scale)
        _add_box(vertices, faces,
                 (cx - 0.40, cx + 0.40, 0.45, 0.52, roof_m + 0.18, roof_m + 0.62),
                 _quantise(_mix(plant_rgb, (0, 0, 0), 0.4)), scale)

    # Water tank on short legs, the classic Japanese rooftop silhouette.
    tx = width_m * 0.30
    ty = -depth_m * 0.22
    for lx in (tx - 0.60, tx + 0.45):
        for ly in (ty - 0.45, ty + 0.30):
            _add_box(vertices, faces,
                     (lx, lx + 0.15, ly, ly + 0.15, roof_m, roof_m + 0.75),
                     rail_rgb, scale)
    _add_box(vertices, faces,
             (tx - 0.70, tx + 0.70, ty - 0.55, ty + 0.55,
              roof_m + 0.75, roof_m + 1.65),
             _quantise(_mix(plant_rgb, (255, 255, 255), 0.30)), scale)


def dress_frontage(vertices, faces, *, spec, width_m, depth_m, roof_m,
                   front_y_m, out_sign, scale):
    """Append every frontage member this profile asks for, in place.

    ``spec`` carries the same profile dict the facade shell uses, plus the
    dressing colours and a ``dressing`` set naming which members apply.  A
    warehouse gets no awning or blade sign; a konbini gets all of them.
    """
    members = spec.get("dressing", set())
    head = float(spec.get("shopfront_head_m", 3.1))
    awning = tuple(spec.get("awning_rgb", (204, 51, 51)))
    fascia = tuple(spec.get("fascia_rgb", (255, 255, 255)))
    sign = tuple(spec.get("sign_rgb", (204, 51, 51)))
    frame = tuple(spec.get("trim_rgb", (102, 102, 102)))
    shutter = tuple(spec.get("shutter_rgb", (153, 153, 153)))
    plant = tuple(spec.get("roof_rgb", (102, 102, 102)))

    if "awning" in members:
        add_awning(vertices, faces, width_m=width_m, front_y_m=front_y_m,
                   out_sign=out_sign, head_m=head, awning_rgb=awning,
                   fascia_rgb=fascia, scale=scale)
    if "sign_tower" in members:
        add_sign_tower(vertices, faces, width_m=width_m, front_y_m=front_y_m,
                       out_sign=out_sign, base_m=head + 0.2, roof_m=roof_m,
                       sign_rgb=sign, frame_rgb=frame, scale=scale)
    if "blade" in members:
        add_blade_sign(vertices, faces, width_m=width_m, front_y_m=front_y_m,
                       out_sign=out_sign, head_m=head, blade_rgb=sign,
                       scale=scale)
    if "shutter" in members:
        add_shutter(vertices, faces, width_m=width_m, front_y_m=front_y_m,
                    out_sign=out_sign, shutter_rgb=shutter, scale=scale,
                    head_m=min(2.35, head - 0.5))
    if "roof" in members:
        add_roof_clutter(vertices, faces, width_m=width_m, depth_m=depth_m,
                         roof_m=roof_m, plant_rgb=plant, rail_rgb=frame,
                         scale=scale)
