#!/usr/bin/env python3
"""FL-4481: pre-cut the retained airplane meshes into eight physical chunks.

The bake clips source faces into a 2 x 4 X/Z grid. Cut interiors use a dark
airframe material. Runtime only instantiates these immutable chunks.
"""
import hashlib
import json
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "godot_project/assets/world_mesh"


def clip(poly, axis, limit, sign):
    result = []
    for a, b in zip(poly, poly[1:] + poly[:1]):
        da, db = sign * (a[axis] - limit), sign * (b[axis] - limit)
        if da >= -1e-9:
            result.append(a)
        if (da < -1e-9) != (db < -1e-9):
            t = da / (da - db)
            result.append(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))
    return result


def hull(points, axis):
    """Convex cap and collider envelope; never replace exterior source faces."""
    axes = [i for i in range(3) if i != axis]
    points = sorted(set(tuple(round(v, 7) for v in p) for p in points),
                    key=lambda p: (p[axes[0]], p[axes[1]]))
    def cross(a, b, c):
        x, y = axes
        return (b[x] - a[x]) * (c[y] - a[y]) - (b[y] - a[y]) * (c[x] - a[x])
    halves = []
    for sequence in (points, list(reversed(points))):
        half = []
        for point in sequence:
            while len(half) > 1 and cross(half[-2], half[-1], point) <= 1e-10:
                half.pop()
            half.append(point)
        halves.append(half[:-1])
    return halves[0] + halves[1]


def bake(source):
    data = json.loads(source.read_text())
    rows = data["rows"]
    faces = []
    for base in range(0, len(rows), 16):
        points = [tuple(rows[base+c*4:base+c*4+3]) for c in range(4)]
        if points[2] == points[3]:
            points.pop()
        faces.append((points, rows[base+3], rows[base+7]))
    lo = [min(p[a] for face, _, _ in faces for p in face) for a in range(3)]
    hi = [max(p[a] for face, _, _ in faces for p in face) for a in range(3)]
    shards = []
    for x in range(2):
        for z in range(4):
            planes = []
            for axis, cell, count in ((0, x, 2), (2, z, 4)):
                planes.extend([(axis, lo[axis]+(hi[axis]-lo[axis])*cell/count, 1),
                               (axis, lo[axis]+(hi[axis]-lo[axis])*(cell+1)/count, -1)])
            polygons = []
            for face, mat, shade in faces:
                poly = face
                for axis, limit, sign in planes:
                    poly = clip(poly, axis, limit, sign)
                if len(poly) >= 3:
                    polygons.append((poly, mat, shade))
            if not polygons:
                continue
            for axis, limit, sign in planes:
                if abs(limit-lo[axis]) < 1e-7 or abs(limit-hi[axis]) < 1e-7:
                    continue
                cap = hull([p for poly, _, _ in polygons for p in poly
                            if abs(p[axis]-limit) < 1e-7], axis)
                if len(cap) >= 3:
                    if sign > 0:
                        cap.reverse()
                    polygons.append((cap, 200, 0.32))
            points = sorted(set(p for poly, _, _ in polygons for p in poly))
            center = [(min(p[a] for p in points)+max(p[a] for p in points))/2 for a in range(3)]
            packed = []
            for poly, mat, shade in polygons:
                for i in range(1, len(poly)-1):
                    a, b, c = poly[0], poly[i], poly[i+1]
                    for point, attribute in zip((a, b, c, c), (mat, shade, 0, 0)):
                        packed.extend([round(point[k]-center[k], 7) for k in range(3)] + [attribute])
            # Boundary silhouettes are sufficient for a convex physical envelope.
            collider = set()
            for axis in range(3):
                collider.update(hull(points, axis))
            shards.append({"center": center, "rows": packed,
                           "collision_points": [[round(p[a]-center[a], 7) for a in range(3)]
                                                for p in sorted(collider)]})
    return {"schema": "airplane-fracture.v1", "source": source.name,
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "recipe": "2x4 planar cuts; convex interior caps; original exterior materials",
            "shards": shards}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--airframe", choices=("airplane", "nausicaa", "macchi"),
                        action="append", help="Bake only the named authored airframe")
    args = parser.parse_args()
    # FL-4265: the hunter's wreck must retain its Macchi body, not the biplane.
    for name in args.airframe or ("airplane", "nausicaa", "macchi"):
        source = ASSETS / f"{name}_airframe_faces.json"
        output = ASSETS / f"{name}_fracture.json"
        result = bake(source)
        output.write_text(json.dumps(result, separators=(",", ":")) + "\n")
        print(f"{output.relative_to(ROOT)}: {len(result['shards'])} shards")
