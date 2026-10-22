#!/usr/bin/env python3
"""RQ-141 / FL-4251 lane: generic OBJ -> compositor face-row baker.

Converts an authored low-poly OBJ (e.g. the CC0 OpenGameArt lighthouse
tower at assets/world_mesh_src/lighthouse/) into the 16-float-per-face
row layout consumed by the FL-4251 compositor world-mesh buffer — the
same artifact schema as scripts/bake_airplane_airframe.py. The mesh
asset is SOURCE data only; final pixels stay compositor-owned (no
MeshInstance3D final output).

Conventions:
  - OBJ is expected Y-up (Blender's default OBJ export).
  - Output is re-originated: XZ bounding-box center -> 0, min Y -> 0,
    then scaled by --scale.
  - Triangles become degenerate quads (last corner repeated); the splat
    pass covers them as one real triangle + one zero-area triangle.
  - Authored shade per face derives from the face normal's Y component:
    tops bright, sides mid, bottoms dark (same convention the airframe
    baker uses so quantized lambert bands separate).
  - Materials map to matlib column ids via --mat NAME=ID (repeatable),
    or sequentially from --mat-base. Suggested display colors can be
    recorded with --mat-color NAME=R,G,B (0..1 floats) for the runtime
    matlib injection to consume.

Example (lighthouse tower, ~14 u tall):
  python3 scripts/bake_world_mesh_obj.py \
    assets/world_mesh_src/lighthouse/lighthouse_tower_lod.obj \
    godot_project/assets/world_mesh/lighthouse_tower_faces.json \
    --scale 0.45 --mat-base 203 \
    --mat-color "Material.010=0.86,0.86,0.84" \
    --mat-color "Material.007=0.62,0.28,0.20" \
    --mat-color "Material.008=0.16,0.16,0.18"

Face budget warning: the terrain_cell_splat.glsl static-face dispatch
only splats face indices < terrain dims.x (880 on the current map).
The baker prints the face count; the WIRING step must verify
static + dynamic total stays under that cap or widen the dispatch.
"""
from __future__ import annotations

import argparse
import json
import os


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("obj_path")
    ap.add_argument("out_json")
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--mat", action="append", default=[],
                    help="NAME=MATLIB_ID explicit material mapping")
    ap.add_argument("--mat-base", type=int, default=203,
                    help="first matlib id for unmapped materials")
    ap.add_argument("--mat-color", action="append", default=[],
                    help="NAME=R,G,B suggested display color (0..1)")
    ap.add_argument("--shade-top", type=float, default=0.92)
    ap.add_argument("--shade-side", type=float, default=0.55)
    ap.add_argument("--shade-bottom", type=float, default=0.30)
    return ap.parse_args()


def face_shade(normal_y: float, top: float, side: float, bottom: float) -> float:
    if normal_y > 0.5:
        return top
    if normal_y < -0.5:
        return bottom
    # blend side toward top/bottom for slanted faces
    if normal_y >= 0.0:
        return side + (top - side) * (normal_y / 0.5) * 0.5
    return side + (bottom - side) * (-normal_y / 0.5) * 0.5


def main() -> None:
    args = parse_args()
    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[list[int], str]] = []
    current_mat = "default"
    with open(args.obj_path) as fh:
        for line in fh:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "v":
                verts.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif parts[0] == "usemtl":
                current_mat = parts[1]
            elif parts[0] == "f":
                idx = [int(p.split("/")[0]) - 1 for p in parts[1:]]
                if len(idx) == 3:
                    idx = idx + [idx[2]]  # degenerate quad
                if len(idx) != 4:
                    # fan-split n-gons into quads/tris
                    for i in range(1, len(idx) - 1):
                        tri = [idx[0], idx[i], idx[i + 1]]
                        faces.append((tri + [tri[2]], current_mat))
                    continue
                faces.append((idx, current_mat))

    if not verts or not faces:
        raise SystemExit(f"no geometry parsed from {args.obj_path}")

    # Re-origin: XZ center -> 0, min Y -> 0; then scale.
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    cx = (min(xs) + max(xs)) * 0.5
    cz = (min(zs) + max(zs)) * 0.5
    y0 = min(ys)
    s = args.scale
    verts = [((v[0] - cx) * s, (v[1] - y0) * s, (v[2] - cz) * s) for v in verts]

    mat_ids: dict[str, int] = {}
    for entry in args.mat:
        name, mid = entry.split("=")
        mat_ids[name] = int(mid)
    mat_colors: dict[str, list[float]] = {}
    for entry in args.mat_color:
        name, rgb = entry.split("=")
        mat_colors[name] = [float(c) for c in rgb.split(",")]
    next_id = args.mat_base
    for _, mat in faces:
        if mat not in mat_ids:
            while next_id in mat_ids.values():
                next_id += 1
            mat_ids[mat] = next_id
            next_id += 1

    rows: list[float] = []
    for idx, mat in faces:
        p = [verts[i] for i in idx]
        e1 = (p[1][0] - p[0][0], p[1][1] - p[0][1], p[1][2] - p[0][2])
        e2 = (p[3][0] - p[0][0], p[3][1] - p[0][1], p[3][2] - p[0][2])
        if idx[2] == idx[3]:  # triangle: use the real second edge
            e2 = (p[2][0] - p[0][0], p[2][1] - p[0][1], p[2][2] - p[0][2])
        ny = e1[2] * e2[0] - e1[0] * e2[2]
        norm = (
            (e1[1] * e2[2] - e1[2] * e2[1]) ** 2
            + ny ** 2
            + (e1[0] * e2[1] - e1[1] * e2[0]) ** 2
        ) ** 0.5
        normal_y = (ny / norm) if norm > 1e-9 else 1.0
        shade = face_shade(abs(normal_y) if False else normal_y,
                           args.shade_top, args.shade_side, args.shade_bottom)
        rows.extend([p[0][0], p[0][1], p[0][2], float(mat_ids[mat])])
        rows.extend([p[1][0], p[1][1], p[1][2], round(shade, 4)])
        rows.extend([p[2][0], p[2][1], p[2][2], 0.0])
        rows.extend([p[3][0], p[3][1], p[3][2], 0.0])

    dims = (
        round((max(xs) - min(xs)) * s, 2),
        round((max(ys) - min(ys)) * s, 2),
        round((max(zs) - min(zs)) * s, 2),
    )
    payload = {
        "version": 1,
        "source": os.path.relpath(args.obj_path),
        "baker": "scripts/bake_world_mesh_obj.py",
        "scale": s,
        "dimensions_xyz": dims,
        "face_count": len(faces),
        "mats": [
            {"name": name, "id": mid, "color": mat_colors.get(name)}
            for name, mid in sorted(mat_ids.items(), key=lambda kv: kv[1])
        ],
        "rows": rows,
    }
    os.makedirs(os.path.dirname(args.out_json), exist_ok=True)
    with open(args.out_json, "w") as fh:
        json.dump(payload, fh)
    print(f"baked {len(faces)} faces dims={dims} mats={mat_ids} -> {args.out_json}")
    if len(faces) > 700:
        print("WARNING: face count is large; splat dispatch caps static+dynamic "
              "faces at terrain dims.x (880 on the current map)")


if __name__ == "__main__":
    main()
