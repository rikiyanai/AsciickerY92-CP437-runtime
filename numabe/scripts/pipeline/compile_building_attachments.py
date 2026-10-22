#!/usr/bin/env python3
"""Hand-authored building attachments -> Classic AKM presentation geometry (FL-4876).

WHY: every Numabe building was a bare box; operator 2026-10-13: "there are no hand rails anywhere". Balconies,
railings, window grilles and rooftop railings are authored per building in
authored_building_appearance.json buildings[id]["attachments"] (each item names the wall face by its outward bearing,
the storey, its span along the face and the railing style, with a photo basis). compile_source_palette passes them
through as building_material[...]["authored_appearance"]["attachments"]; the building bake calls build_attachments()
and embeds one non-colliding mesh per building (alpha 255).

Frame: footprint polygon in A3D world units (x east, y north); local z in world units above local ground (metres *
content_scale) so the instance transform keeps the shells' row2 scale.  Bearing = atan2(dx, dy) in degrees.

Item schema (all but kind optional):
  kind            balcony | railing | window_grille | rooftop_railing | stair_handrail
  face_bearing_deg outward normal bearing of the wall face (nearest footprint edge within 50 deg is used)
  storey          1-based floor the item belongs to (balcony floor sits at (storey-1) * storey_m)
  along_frac      centre along the face, 0 = left end and 1 = right end seen from outside;  width_frac  span (0..1]
  depth_m         balcony projection (0.9);  height_m  railing height (1.1; rooftop 1.0)
  style           dark_metal | white_metal | glass | concrete_balustrade | louvre_panel
  rgb             railing colour (else by style)
"""
from __future__ import annotations

import math

ALPHA_NO_COLLISION = 255
STYLE_RGB = {"dark_metal": (58, 52, 48), "white_metal": (228, 228, 224), "glass": (176, 196, 204),
             "concrete_balustrade": (214, 208, 196), "louvre_panel": (92, 70, 54)}
SOLID_STYLES = {"glass", "concrete_balustrade", "louvre_panel"}


def _bearing(dx, dy):
    return math.degrees(math.atan2(dx, dy)) % 360.0


def _edges(poly):
    """(p, q, outward normal (nx, ny), length, outward bearing) for each edge of a CCW-normalised ring."""
    ring = list(poly)
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1]))
    if area < 0:
        ring.reverse()
    out = []
    for i in range(len(ring)):
        p, q = ring[i], ring[(i + 1) % len(ring)]
        dx, dy = q[0] - p[0], q[1] - p[1]
        ln = math.hypot(dx, dy)
        if ln < 1e-6:
            continue
        nx, ny = dy / ln, -dx / ln  # outward for a CCW ring
        out.append((p, q, (nx, ny), ln, _bearing(nx, ny)))
    return out


class _Mesh:
    def __init__(self, origin, scale):
        self.v, self.f, self.o, self.s = [], [], origin, scale

    def quad(self, a, b, c, d, n, rgb, two_sided=True):
        """a..d: (x, y, z_m) world xy / metres z, counter-clockwise seen from n."""
        first = len(self.v)
        for x, y, z in (a, b, c, d):
            self.v.append((x - self.o[0], y - self.o[1], z * self.s, n[0], n[1], n[2], *rgb, ALPHA_NO_COLLISION))
        self.f.append([first, first + 1, first + 2, first + 3])
        if two_sided:
            self.f.append([first + 3, first + 2, first + 1, first])

    def box(self, x0, y0, x1, y1, ux, uy, z0, z1, rgb):
        """thin vertical slab from (x0,y0) to (x1,y1), thickness given by perpendicular (ux, uy) offset."""
        a, b = (x0, y0), (x1, y1)
        c, d = (x1 + ux, y1 + uy), (x0 + ux, y0 + uy)
        n = (uy, -ux, 0.0)
        self.quad((*a, z0), (*b, z0), (*b, z1), (*a, z1), (-ux, -uy, 0.0), rgb)
        self.quad((*d, z0), (*c, z0), (*c, z1), (*d, z1), (ux, uy, 0.0), rgb)
        self.quad((*a, z1), (*b, z1), (*c, z1), (*d, z1), (0.0, 0.0, 1.0), rgb)


def _railing(mesh, pts, z0, h, style, rgb, scale):
    """railing along the polyline pts (world xy) standing on z0 (m): top rail, bottom rail, posts every 0.25 m
    (or a solid panel for glass / balustrade / louvre styles)."""
    t = 0.05 * scale  # bar thickness in world units
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        ln = math.hypot(x1 - x0, y1 - y0)
        if ln < 1e-6:
            continue
        px, py = -(y1 - y0) / ln * t, (x1 - x0) / ln * t
        if style in SOLID_STYLES:
            mesh.box(x0, y0, x1, y1, px, py, z0, z0 + h, rgb)
            continue
        mesh.box(x0, y0, x1, y1, px, py, z0 + h - 0.06, z0 + h, rgb)          # top rail
        mesh.box(x0, y0, x1, y1, px, py, z0 + 0.08, z0 + 0.13, rgb)           # bottom rail
        n = max(1, int(ln / scale / 0.25))
        for k in range(n + 1):
            f = k / n
            cx, cy = x0 + (x1 - x0) * f, y0 + (y1 - y0) * f
            ex, ey = (x1 - x0) / ln * t, (y1 - y0) / ln * t
            mesh.box(cx, cy, cx + ex, cy + ey, px, py, z0 + 0.08, z0 + h, rgb)  # post


def build_attachments(poly, attachments, *, content_scale, storey_m=3.0, roof_m=None, wall_rgb=(200, 200, 200)):
    """Return (verts, faces, origin, receipt) for all attachments of one building; origin = footprint centroid."""
    ring = list(poly)
    ox = sum(p[0] for p in ring) / len(ring); oy = sum(p[1] for p in ring) / len(ring)
    s = float(content_scale)
    mesh = _Mesh((ox, oy), s)
    edges = _edges(ring)
    receipt = []
    for item in attachments or []:
        kind = item.get("kind")
        style = item.get("style") or ("dark_metal" if kind != "rooftop_railing" else "white_metal")
        rgb = tuple(item.get("rgb") or STYLE_RGB.get(style, STYLE_RGB["dark_metal"]))
        if kind == "rooftop_railing":
            if roof_m is None:
                receipt.append({"kind": kind, "emitted": False, "reason": "no roof height"}); continue
            inset = 0.15 * s
            cx, cy = ox, oy
            pts = []
            for p in ring + ring[:1]:
                dx, dy = p[0] - cx, p[1] - cy; d = math.hypot(dx, dy) or 1.0
                pts.append((p[0] - dx / d * inset, p[1] - dy / d * inset))
            _railing(mesh, pts, float(roof_m), float(item.get("height_m") or 1.0), style, rgb, s)
            receipt.append({"kind": kind, "emitted": True}); continue
        want = float(item.get("face_bearing_deg", 0.0))
        diff = lambda e: abs((e[4] - want + 180.0) % 360.0 - 180.0)
        e = min(edges, key=diff) if edges else None
        if e is None or diff(e) > 50.0:
            receipt.append({"kind": kind, "emitted": False, "reason": "no wall face within 50 deg of %.0f" % want}); continue
        (px0, py0), (px1, py1), (nx, ny), ln, brg = e
        # seen from outside (looking against the outward normal) a CCW ring's edge p -> q runs from the viewer's
        # left to right (square CCW, bottom edge (0,0)->(1,0), viewer south facing north: p is on the left)
        L, R = (px0, py0), (px1, py1)
        ux, uy = (R[0] - L[0]) / ln, (R[1] - L[1]) / ln
        wf = max(0.05, min(1.0, float(item.get("width_frac") or 0.5)))
        af = max(wf / 2, min(1 - wf / 2, float(item.get("along_frac") if item.get("along_frac") is not None else 0.5)))
        a0, a1 = (af - wf / 2) * ln, (af + wf / 2) * ln
        A = (L[0] + ux * a0, L[1] + uy * a0); B = (L[0] + ux * a1, L[1] + uy * a1)
        storey = max(1, int(item.get("storey") or 2))
        zf = (storey - 1) * float(storey_m)
        if kind == "balcony":
            d = float(item.get("depth_m") or 0.9) * s
            A2, B2 = (A[0] + nx * d, A[1] + ny * d), (B[0] + nx * d, B[1] + ny * d)
            slab = tuple(item.get("slab_rgb") or wall_rgb)
            mesh.quad((*A, zf + 0.15), (*B, zf + 0.15), (*B2, zf + 0.15), (*A2, zf + 0.15), (0, 0, 1), slab)   # floor
            mesh.quad((*A, zf), (*A2, zf), (*B2, zf), (*B, zf), (0, 0, -1), slab)                            # soffit
            mesh.quad((*A2, zf), (*B2, zf), (*B2, zf + 0.15), (*A2, zf + 0.15), (nx, ny, 0), slab)          # edge
            _railing(mesh, [A, A2, B2, B], zf + 0.15, float(item.get("height_m") or 1.1), style, rgb, s)
        elif kind == "railing" or kind == "stair_handrail":
            off = 0.05 * s
            p0, p1 = (A[0] + nx * off, A[1] + ny * off), (B[0] + nx * off, B[1] + ny * off)
            _railing(mesh, [p0, p1], zf, float(item.get("height_m") or 1.1), style, rgb, s)
        elif kind == "window_grille":
            off = 0.08 * s; t = 0.03 * s
            gw = max(0.6, wf * ln / s) * s
            mid = ((A[0] + B[0]) / 2, (A[1] + B[1]) / 2)
            g0 = (mid[0] - ux * gw / 2 + nx * off, mid[1] - uy * gw / 2 + ny * off)
            zb, zt = zf + 0.32 * storey_m, zf + 0.80 * storey_m
            for k in range(int(gw / s / 0.12) + 1):
                cx, cy = g0[0] + ux * k * 0.12 * s, g0[1] + uy * k * 0.12 * s
                mesh.box(cx, cy, cx + ux * t, cy + uy * t, nx * t, ny * t, zb, zt, rgb)
            for z in (zb, (zb + zt) / 2, zt - 0.04):
                mesh.box(g0[0], g0[1], g0[0] + ux * gw, g0[1] + uy * gw, nx * t, ny * t, z, z + 0.04, rgb)
        else:
            receipt.append({"kind": kind, "emitted": False, "reason": "unknown kind"}); continue
        receipt.append({"kind": kind, "emitted": True, "face_bearing_deg": round(brg, 1), "storey": storey})
    return mesh.v, mesh.f, (ox, oy), receipt
