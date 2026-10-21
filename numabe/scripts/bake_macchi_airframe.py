#!/usr/bin/env python3
"""FL-4265: Macchi M.33 flying-boat airframe for the hunter dogfighter.

The SketchFab M.33 (19k faces of cockpit detail) cannot decimate to airframe
budget, so like the biplane bake this script IS the authored source: a
low-poly Schneider-style single-hull flying boat with parasol wing, tractor
prop, and wingtip floats, baked to the 16-float-per-face row layout with
prop-face indices for the spin driver.

Model space: +X forward (nose), +Y up, +Z starboard. Origin at the waterline
under the center of gravity (floatplane: sits IN water, not on gear).

Materials (shared airframe IDs):
    200 = body, 201 = accent (wing flashes, fin flash), 202 = windshield,
    208 = propeller blades + hub (time-rotated by World.gd around +X)

Part lane (FL-4902 lane VP): `wing_face_indices` lists the parasol wing with
its tip flashes and the tailplane. World.gd registers them as rigid part 2
(propeller part 1, the rest part 0) so True FF can name a wing contour
VEHICLE_WING. Hull, struts, fin and wingtip floats stay airframe body.
"""
from __future__ import annotations

import json
import os

MAT_BODY = 200
MAT_ACCENT = 201
MAT_CANOPY = 202
MAT_PROP = 208

OUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "godot_project", "assets", "world_mesh", "macchi_airframe_faces.json",
)

faces: list[list[float]] = []
prop_face_indices: list[int] = []
# FL-4902 lane VP: faces of the lifting surfaces (World.gd rigid part 2).
wing_face_indices: list[int] = []


class wing_part:
    """Faces emitted inside this block are wing faces (authored, not inferred)."""

    def __enter__(self):
        self.first = len(faces)

    def __exit__(self, *exc):
        wing_face_indices.extend(range(self.first, len(faces)))


def quad(p0, p1, p2, p3, mat, shade):
    if mat == MAT_PROP:
        prop_face_indices.append(len(faces))
    faces.append([
        p0[0], p0[1], p0[2], float(mat),
        p1[0], p1[1], p1[2], float(shade),
        p2[0], p2[1], p2[2], 0.0,
        p3[0], p3[1], p3[2], 0.0,
    ])


def loft(sections, mat_body=MAT_BODY, shade_side=0.5, shade_top=0.6, shade_bottom=0.35):
    """Hull loft: sections are (x, half_width, deck_y, keel_y)."""
    for a, b in zip(sections, sections[1:]):
        ax, aw, ay, ak = a
        bx, bw, by, bk = b
        # deck strip
        quad((ax, ay, -aw), (ax, ay, aw), (bx, by, bw), (bx, by, -bw),
             mat_body, shade_top)
        # port / starboard sides
        quad((ax, ay, -aw), (bx, by, -bw), (bx, bk, -bw), (ax, ak, -aw),
             mat_body, shade_side)
        quad((bx, by, bw), (ax, ay, aw), (ax, ak, aw), (bx, bk, bw),
             mat_body, shade_side)
        # V bottom (two quads)
        quad((ax, ak, -aw), (bx, bk, -bw), (bx, bk, 0.0), (ax, ak, 0.0),
             mat_body, shade_bottom)
        quad((bx, bk, bw), (ax, ak, aw), (ax, ak, 0.0), (bx, bk, 0.0),
             mat_body, shade_bottom)


def cap(x, w, y_top, y_bot, mat, shade):
    quad((x, y_top, -w), (x, y_top, w), (x, y_bot, w), (x, y_bot, -w),
         mat, shade)


def slab(x0, x1, z0, z1, y, thick, mat, shade_top, shade_edge):
    ht = thick * 0.5
    quad((x0, y + ht, z0), (x0, y + ht, z1), (x1, y + ht, z1), (x1, y + ht, z0),
         mat, shade_top)
    quad((x0, y - ht, z0), (x1, y - ht, z0), (x1, y - ht, z1), (x0, y - ht, z1),
         mat, shade_top * 0.7)
    quad((x1, y - ht, z0), (x1, y + ht, z0), (x1, y + ht, z1), (x1, y - ht, z1),
         mat, shade_edge)
    quad((x0, y + ht, z0), (x0, y - ht, z0), (x0, y - ht, z1), (x0, y + ht, z1),
         mat, shade_edge)
    quad((x0, y - ht, z1), (x1, y - ht, z1), (x1, y + ht, z1), (x0, y + ht, z1),
         mat, shade_edge)
    quad((x1, y + ht, z0), (x1, y - ht, z0), (x0, y - ht, z0), (x0, y + ht, z0),
         mat, shade_edge)


def main() -> None:
    # Boat hull: stern to nose.
    loft([
        (-3.2, 0.55, 1.10, -0.50),
        (-1.6, 0.75, 1.25, -0.75),
        (0.0, 0.85, 1.30, -0.80),
        (1.8, 0.70, 1.15, -0.45),
        (3.4, 0.12, 0.90, 0.10),
    ])
    cap(-3.2, 0.55, 1.10, -0.50, MAT_BODY, 0.45)
    # Windshield hump.
    quad((0.6, 1.30, -0.35), (0.6, 1.30, 0.35), (1.3, 1.05, 0.30), (1.3, 1.05, -0.30),
         MAT_CANOPY, 0.6)
    # Parasol wing on struts.
    with wing_part():
        slab(-0.7, 1.5, -7.5, 7.5, 3.2, 0.18, MAT_BODY, 0.62, 0.5)
        # Red wingtip flashes (accent).
        slab(-0.7, 1.5, -7.5, -6.3, 3.2, 0.20, MAT_ACCENT, 0.6, 0.5)
        slab(-0.7, 1.5, 6.3, 7.5, 3.2, 0.20, MAT_ACCENT, 0.6, 0.5)
    # Cabane struts (4 thin quads).
    for z in (-0.5, 0.5):
        quad((-0.4, 1.3, z), (0.9, 3.1, z * 4.0), (1.0, 3.1, z * 4.0), (-0.3, 1.3, z),
             MAT_BODY, 0.5)
        quad((0.9, 3.1, z * 4.0), (1.6, 1.2, z), (1.7, 1.2, z), (1.0, 3.1, z * 4.0),
             MAT_BODY, 0.5)
    # Tractor prop at the nose: two blades + hub (spun by World.gd).
    quad((3.55, -1.05, -0.09), (3.55, -1.05, 0.09), (3.55, 1.05, 0.09), (3.55, 1.05, -0.09),
         MAT_PROP, 0.4)
    quad((3.55, -0.09, -1.05), (3.55, -0.09, 1.05), (3.55, 0.09, 1.05), (3.55, 0.09, -1.05),
         MAT_PROP, 0.4)
    quad((3.45, -0.18, -0.18), (3.45, -0.18, 0.18), (3.45, 0.18, 0.18), (3.45, 0.18, -0.18),
         MAT_PROP, 0.5)
    # Tailplane + fin flash.
    with wing_part():
        slab(-3.6, -2.2, -2.5, 2.5, 1.35, 0.14, MAT_BODY, 0.6, 0.5)
    quad((-3.6, 1.3, -0.07), (-3.6, 1.3, 0.07), (-2.6, 3.3, 0.07), (-2.6, 3.3, -0.07),
         MAT_ACCENT, 0.6)
    quad((-2.6, 3.3, 0.07), (-2.6, 3.3, -0.07), (-3.6, 1.3, -0.07), (-3.6, 1.3, 0.07),
         MAT_ACCENT, 0.6)
    # Wingtip stabilizing floats.
    for z in (-6.8, 6.8):
        before = len(faces)
        loft([(0.0, 0.28, 1.15, 0.55), (0.9, 0.30, 1.10, 0.50), (1.9, 0.10, 1.00, 0.70)],
             MAT_BODY, 0.5, 0.6, 0.35)
        # shift lofted sections out to the wingtip (loft is x-profiled; offset z)
        for f in faces[before:]:
            f[2] += z
            f[6] += z
            f[10] += z
            f[14] += z

    if set(wing_face_indices) & set(prop_face_indices):
        raise ValueError("a face cannot be both wing and propeller")
    flat: list[float] = [v for f in faces for v in f]
    xs = flat[0::4]
    ys = flat[1::4]
    zs = flat[2::4]
    doc = {
        "version": 1,
        "source": "procedural Macchi-M.33-style flying boat (this script)",
        "baker": "scripts/bake_macchi_airframe.py",
        "mode": "airframe",
        "scale": 1.0,
        "dimensions_xyz": [round(max(xs) - min(xs), 4), round(max(ys) - min(ys), 4),
                           round(max(zs) - min(zs), 4)],
        "face_count": len(faces),
        "mats": {"body": MAT_BODY, "accent": MAT_ACCENT, "canopy": MAT_CANOPY, "prop": MAT_PROP},
        "mat_ids": {"body": MAT_BODY, "accent": MAT_ACCENT, "canopy": MAT_CANOPY, "prop": MAT_PROP},
        "rows": [round(v, 4) for v in flat],
        "prop_face_indices": prop_face_indices,
        "wing_face_indices": wing_face_indices,
        "prop_pivot_x": 3.5,
        "prop_pivot_y": 0.0,
        "prop_pivot_z": 0.0,
        "model_space": "+X forward, +Y up, +Z starboard, origin at waterline under CG",
        "forward_axis": "+x",
    }
    with open(OUT_PATH, "w") as f:
        json.dump(doc, f)
    print(f"macchi: faces={len(faces)} prop={prop_face_indices} wing_faces={len(wing_face_indices)} "
          f"dims={doc['dimensions_xyz']}")


if __name__ == "__main__":
    main()
