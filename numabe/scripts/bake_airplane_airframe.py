#!/usr/bin/env python3
"""FL-4265 / FL-4319: biplane airframe with pontoons and propeller.

This script IS the authored airframe source (versioned, reviewable,
editable). It bakes a low-poly biplane floatplane into the exact
16-float-per-face row layout consumed by the FL-4251 compositor world-mesh
buffer (World.gd `_append_static_world_face` convention):

    p0.xyz, mat_id, p1.xyz, authored_shade, p2.xyz, 0, p3.xyz, 0

Model space: +X forward (nose), +Y up, +Z starboard. Origin at the ground
contact point under the center of gravity.

Changes from v1 (FL-4319):
  - Biplane: added upper wing + interplane struts
  - Floatplane: added twin pontoon floats + struts (fixed gear, never retracts)
  - Propeller: separate MAT_PROP geometry at nose, time-rotated by World.gd

Part lane (FL-4902 lane VP): `wing_face_indices` lists the faces of the
lifting surfaces (lower and upper wings with their tip caps, tailplane).
World.gd registers them as rigid part 2 (the propeller is part 1, the rest
of the airframe part 0) so True FF can name a wing contour VEHICLE_WING.
Struts, pontoons, fin and fuselage stay airframe body. Materials are
unchanged: the part is geometry identity, not colour.

Materials:
    200 = airframe body
    201 = airframe accent (canopy, wingtips, fin flash)
    202 = canopy glass (rider-state aware at runtime)
    208 = propeller blades + hub (time-rotated by World.gd)
"""
from __future__ import annotations

import json
import os

MAT_BODY   = 200
MAT_ACCENT = 201
MAT_CANOPY = 202
MAT_PROP   = 208   # FL-4319: propeller — World.gd rotates these around +X axis

OUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "godot_project", "assets", "world_mesh", "airplane_airframe_faces.json",
)

faces: list[tuple[tuple, tuple, tuple, tuple, int, float]] = []
# Track which face indices are propeller faces so World.gd can rotate them.
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
    faces.append((tuple(p0), tuple(p1), tuple(p2), tuple(p3), mat, shade))


def loft(sec_a, sec_b, mat, shade_top, shade_side, shade_bottom):
    ax, aw, ah, ay = sec_a
    bx, bw, bh, by = sec_b
    atl, atr = (ax, ay + ah, -aw), (ax, ay + ah,  aw)
    abl, abr = (ax, ay - ah, -aw), (ax, ay - ah,  aw)
    btl, btr = (bx, by + bh, -bw), (bx, by + bh,  bw)
    bbl, bbr = (bx, by - bh, -bw), (bx, by - bh,  bw)
    quad(atl, btl, btr, atr, mat, shade_top)
    quad(abl, bbl, bbr, abr, mat, shade_bottom)
    quad(atl, btl, bbl, abl, mat, shade_side)
    quad(atr, btr, bbr, abr, mat, shade_side)


def cap(sec, mat, shade):
    x, w, h, cy = sec
    quad((x, cy + h, -w), (x, cy + h, w), (x, cy - h, w), (x, cy - h, -w), mat, shade)


def slab(x0, x1, z0, z1, y_at_z0, y_at_z1, thickness, mat,
         shade_top, shade_bottom, shade_edge, cap_outer=True):
    ht = thickness * 0.5
    r0t, r0b = (x0, y_at_z0 + ht, z0), (x0, y_at_z0 - ht, z0)
    r1t, r1b = (x1, y_at_z0 + ht, z0), (x1, y_at_z0 - ht, z0)
    t0t, t0b = (x0, y_at_z1 + ht, z1), (x0, y_at_z1 - ht, z1)
    t1t, t1b = (x1, y_at_z1 + ht, z1), (x1, y_at_z1 - ht, z1)
    quad(r0t, r1t, t1t, t0t, mat, shade_top)
    quad(r0b, r1b, t1b, t0b, mat, shade_bottom)
    quad(r1t, t1t, t1b, r1b, mat, shade_edge)
    quad(r0t, t0t, t0b, r0b, mat, shade_edge)
    if cap_outer:
        quad(t0t, t1t, t1b, t0b, mat, shade_edge)


def box(cx, cy, cz, sx, sy, sz, mat, shade_top, shade_side, shade_bottom=None):
    hx, hy, hz = sx * 0.5, sy * 0.5, sz * 0.5
    tl0, tr0 = (cx - hx, cy + hy, cz - hz), (cx - hx, cy + hy, cz + hz)
    tl1, tr1 = (cx + hx, cy + hy, cz - hz), (cx + hx, cy + hy, cz + hz)
    bl0, br0 = (cx - hx, cy - hy, cz - hz), (cx - hx, cy - hy, cz + hz)
    bl1, br1 = (cx + hx, cy - hy, cz - hz), (cx + hx, cy - hy, cz + hz)
    quad(tl0, tl1, tr1, tr0, mat, shade_top)
    quad(tl0, tl1, bl1, bl0, mat, shade_side)
    quad(tr0, tr1, br1, br0, mat, shade_side)
    quad(tl1, tr1, br1, bl1, mat, shade_side)
    quad(tl0, tr0, br0, bl0, mat, shade_side)
    if shade_bottom is not None:
        quad(bl0, bl1, br1, br0, mat, shade_bottom)


SH_TOP    = 0.92
SH_SIDE   = 0.55
SH_BELLY  = 0.30
SH_EDGE   = 0.50
SH_PROP   = 0.88  # propeller bright, slightly cooler than top

# ---------------------------------------------------------------------------
# Fuselage
# ---------------------------------------------------------------------------
SEC_TAIL  = (-1.70, 0.10, 0.12, 0.62)
SEC_REAR  = (-0.55, 0.34, 0.36, 0.52)
SEC_FRONT = ( 0.75, 0.36, 0.38, 0.52)
SEC_NOSE  = ( 1.70, 0.16, 0.18, 0.46)
loft(SEC_TAIL,  SEC_REAR,  MAT_BODY, SH_TOP, SH_SIDE, SH_BELLY)
loft(SEC_REAR,  SEC_FRONT, MAT_BODY, SH_TOP, SH_SIDE, SH_BELLY)
loft(SEC_FRONT, SEC_NOSE,  MAT_BODY, SH_TOP, SH_SIDE, SH_BELLY)
cap(SEC_NOSE, MAT_ACCENT, 0.85)
cap(SEC_TAIL, MAT_BODY,   SH_SIDE)

# ---------------------------------------------------------------------------
# Canopy
# ---------------------------------------------------------------------------
box(0.45, 0.99, 0.0, 0.62, 0.30, 0.46, MAT_CANOPY, 0.95, 0.70)

# ---------------------------------------------------------------------------
# Lower wing (same as v1)
# ---------------------------------------------------------------------------
with wing_part():
    slab(-0.30, 0.95,  0.30,  2.45, 0.62, 0.79, 0.14, MAT_BODY, SH_TOP, SH_BELLY, SH_EDGE, cap_outer=False)
    slab(-0.30, 0.95, -0.30, -2.45, 0.62, 0.79, 0.14, MAT_BODY, SH_TOP, SH_BELLY, SH_EDGE, cap_outer=False)
    # accent wingtip caps
    slab(-0.18, 0.83,  2.45,  3.05, 0.79, 0.84, 0.14, MAT_ACCENT, 0.95, 0.45, 0.70)
    slab(-0.18, 0.83, -2.45, -3.05, 0.79, 0.84, 0.14, MAT_ACCENT, 0.95, 0.45, 0.70)

# ---------------------------------------------------------------------------
# Upper wing (FL-4319 biplane addition)
# Slightly shorter span than lower, raised ~0.88 u above lower wing root.
# ---------------------------------------------------------------------------
UPPER_Y = 1.50   # root Y of upper wing surface
with wing_part():
    slab(-0.10, 0.80,  0.20,  2.20, UPPER_Y, UPPER_Y + 0.08, 0.12, MAT_BODY, SH_TOP, SH_BELLY, SH_EDGE, cap_outer=False)
    slab(-0.10, 0.80, -0.20, -2.20, UPPER_Y, UPPER_Y + 0.08, 0.12, MAT_BODY, SH_TOP, SH_BELLY, SH_EDGE, cap_outer=False)
    # accent wingtip caps
    slab(-0.05, 0.72,  2.20,  2.70, UPPER_Y + 0.08, UPPER_Y + 0.11, 0.12, MAT_ACCENT, 0.95, 0.45, 0.70)
    slab(-0.05, 0.72, -2.20, -2.70, UPPER_Y + 0.08, UPPER_Y + 0.11, 0.12, MAT_ACCENT, 0.95, 0.45, 0.70)

# ---------------------------------------------------------------------------
# Interplane struts — two pairs (inner + outer) connecting lower to upper wing
# (FL-4319)
# ---------------------------------------------------------------------------
STRUT_X   = 0.30   # fore-aft centre of strut pair
STRUT_SX  = 0.10   # strut fore-aft depth
STRUT_SZ  = 0.06   # strut side-to-side width
STRUT_BOT = 0.68   # bottom of strut = lower wing top surface approx
STRUT_TOP = UPPER_Y  # top of strut = upper wing root
STRUT_H   = STRUT_TOP - STRUT_BOT

for strut_z in [1.10, -1.10, 1.90, -1.90]:
    box(STRUT_X, STRUT_BOT + STRUT_H * 0.5, strut_z,
        STRUT_SX, STRUT_H, STRUT_SZ,
        MAT_BODY, SH_SIDE, SH_SIDE, SH_SIDE)

# ---------------------------------------------------------------------------
# Tailplane and vertical fin (unchanged from v1)
# ---------------------------------------------------------------------------
# The tailplane is a lifting surface: wing part. The fin stays body.
with wing_part():
    slab(-1.78, -1.30,  0.10,  1.00, 0.66, 0.72, 0.10, MAT_BODY, SH_TOP, SH_BELLY, SH_EDGE)
    slab(-1.78, -1.30, -0.10, -1.00, 0.66, 0.72, 0.10, MAT_BODY, SH_TOP, SH_BELLY, SH_EDGE)
fin = [
    ((-1.80, 0.74, -0.06), (-1.30, 0.74, -0.06), (-1.52, 1.55, -0.06), (-1.80, 1.55, -0.06)),
    ((-1.80, 0.74,  0.06), (-1.30, 0.74,  0.06), (-1.52, 1.55,  0.06), (-1.80, 1.55,  0.06)),
]
for p0, p1, p2, p3 in fin:
    quad(p0, p1, p2, p3, MAT_ACCENT, 0.76)
quad((-1.80, 1.55, -0.06), (-1.52, 1.55, -0.06), (-1.52, 1.55, 0.06), (-1.80, 1.55, 0.06), MAT_ACCENT, 0.82)
quad((-1.30, 0.74, -0.06), (-1.52, 1.55, -0.06), (-1.52, 1.55, 0.06), (-1.30, 0.74, 0.06), MAT_ACCENT, 0.78)

# ---------------------------------------------------------------------------
# Pontoon floats — twin hulls below fuselage (FL-4319 floatplane)
# Fixed gear (never retracts). Low-poly: one long box per float.
# ---------------------------------------------------------------------------
PONTOON_Y     = -0.08   # top of pontoon relative to model origin
PONTOON_H     = 0.22    # height
PONTOON_L     = 2.80    # length (X)
PONTOON_W     = 0.28    # width (Z)
PONTOON_CX    = 0.10    # fore-aft offset (slightly forward of CG)
PONTOON_CZ    = 0.78    # lateral offset

for pz in [PONTOON_CZ, -PONTOON_CZ]:
    box(PONTOON_CX, PONTOON_Y - PONTOON_H * 0.5, pz,
        PONTOON_L, PONTOON_H, PONTOON_W,
        MAT_BODY, SH_BELLY, SH_SIDE, SH_BELLY)

# Pontoon struts: two thin vertical posts per float connecting to lower wing root
PSTRUT_H  = 0.50
PSTRUT_SX = 0.07
PSTRUT_SZ = 0.05
PSTRUT_Y_BOT = PONTOON_Y
PSTRUT_Y_MID = PSTRUT_Y_BOT + PSTRUT_H * 0.5

for pz in [PONTOON_CZ, -PONTOON_CZ]:
    for px in [-0.10, 0.60]:
        box(px, PSTRUT_Y_MID, pz,
            PSTRUT_SX, PSTRUT_H, PSTRUT_SZ,
            MAT_BODY, SH_SIDE, SH_SIDE, SH_SIDE)

# ---------------------------------------------------------------------------
# Propeller hub + blades (FL-4319) — MAT_PROP, World.gd rotates around the
# fuselage axis (+X direction) through the hub centre. The hub sits on the
# nose centreline (NOSE_Y), NOT at the ground-origin y=0 — otherwise the disc
# would be half-buried and World.gd's X-axis spin would orbit, not rotate.
# ---------------------------------------------------------------------------
PROP_X   = 1.85   # just ahead of nose cap
PROP_Y   = SEC_NOSE[3]  # 0.46 — nose section centre height (fuselage axis)
PROP_Z   = 0.0
HUB_R    = 0.13
# Hub: a small flat square cap on the nose centreline
quad((PROP_X, PROP_Y - HUB_R, PROP_Z - HUB_R), (PROP_X, PROP_Y + HUB_R, PROP_Z - HUB_R),
     (PROP_X, PROP_Y + HUB_R, PROP_Z + HUB_R), (PROP_X, PROP_Y - HUB_R, PROP_Z + HUB_R),
     MAT_PROP, SH_PROP)

# Two blades — slabs extending in the ±Y direction from the hub centre.
# World.gd rotates the whole prop group around the +X axis through (PROP_Y, PROP_Z)
# so these spin in place as a disc.
# FL-4319 (longer + non-translucent prop): the original 0.59 radius / 0.06 chord
# disc was sub-cell at the airplane's render distance, so the spin animation was
# barely visible and the thin blades only partially owned ANSI cells (reading as
# a faint, see-through sliver). Blades are now ~2x longer and ~2.6x fatter so the
# rotating disc spans several cells and each blade fully owns the cells it covers
# — opaque, readable spin. The prop still paints through the OPAQUE
# OWNER_DYNAMIC_MESH base path (no alpha blend); width is what makes it read solid.
BLADE_L  = 1.10   # half-length (prop radius ≈ HUB_R + BLADE_L = 1.23)
BLADE_T  = 0.16   # blade "chord" in Z (fat enough to own full cells -> opaque)
BLADE_D  = 0.06   # blade thickness in X

for sign in [1.0, -1.0]:
    tip_y  = PROP_Y + sign * (HUB_R + BLADE_L)
    root_y = PROP_Y + sign * HUB_R
    quad(
        (PROP_X - BLADE_D, root_y, PROP_Z - BLADE_T),
        (PROP_X - BLADE_D, tip_y,  PROP_Z - BLADE_T * 0.4),
        (PROP_X - BLADE_D, tip_y,  PROP_Z + BLADE_T * 0.4),
        (PROP_X - BLADE_D, root_y, PROP_Z + BLADE_T),
        MAT_PROP, SH_PROP,
    )
    quad(
        (PROP_X, root_y, PROP_Z - BLADE_T),
        (PROP_X, tip_y,  PROP_Z - BLADE_T * 0.4),
        (PROP_X, tip_y,  PROP_Z + BLADE_T * 0.4),
        (PROP_X, root_y, PROP_Z + BLADE_T),
        MAT_PROP, SH_PROP,
    )
    # leading-edge cap
    quad(
        (PROP_X - BLADE_D, root_y, PROP_Z - BLADE_T),
        (PROP_X,           root_y, PROP_Z - BLADE_T),
        (PROP_X,           tip_y,  PROP_Z - BLADE_T * 0.4),
        (PROP_X - BLADE_D, tip_y,  PROP_Z - BLADE_T * 0.4),
        MAT_PROP, SH_PROP,
    )


def main() -> None:
    if set(wing_face_indices) & set(prop_face_indices):
        raise ValueError("a face cannot be both wing and propeller")
    rows: list[float] = []
    for p0, p1, p2, p3, mat, shade in faces:
        rows.extend([p0[0], p0[1], p0[2], float(mat)])
        rows.extend([p1[0], p1[1], p1[2], float(shade)])
        rows.extend([p2[0], p2[1], p2[2], 0.0])
        rows.extend([p3[0], p3[1], p3[2], 0.0])
    payload = {
        "version": 2,
        "source": "scripts/bake_airplane_airframe.py",
        "fl": "FL-4319",
        "face_count": len(faces),
        "mat_ids": {
            "body":    MAT_BODY,
            "accent":  MAT_ACCENT,
            "canopy":  MAT_CANOPY,
            "prop":    MAT_PROP,
        },
        "prop_face_indices": prop_face_indices,
        "wing_face_indices": wing_face_indices,
        "prop_pivot_x": PROP_X,
        "prop_pivot_y": PROP_Y,
        "prop_pivot_z": PROP_Z,
        "model_space": "+X forward, +Y up, +Z starboard, origin at ground contact",
        "rows": rows,
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w") as fh:
        json.dump(payload, fh)
    print(f"baked {len(faces)} faces ({len(rows)} floats) "
          f"[{len(prop_face_indices)} prop faces, {len(wing_face_indices)} wing faces] -> {OUT_PATH}")


if __name__ == "__main__":
    main()
