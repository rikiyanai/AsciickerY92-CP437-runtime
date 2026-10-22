#!/usr/bin/env python3
# =============================================================
# decimate_akm.py  (FL-4265)
# =============================================================
# WHAT THIS DOES (in plain words)
# -------------------------------
# Some downloaded models are FAR too detailed for where the game can use them
# cheaply. The Nausicaa plane (FL-4265) is 17,188 triangles. The flyable-airplane
# lane rebuilds and redraws the plane's triangles every frame, and the authored
# biplane beside it is only 136 triangles. At 17k triangles the plane tanks the
# frame rate (~2 FPS measured) and the render breaks, so it must be simplified.
#
# This simplifies (decimates) an .akm mesh by "vertex clustering":
#   1. Lay an invisible 3-D grid of cubes over the model.
#   2. Snap every vertex to the centre of its cube, merging vertices that share
#      a cube into one representative vertex.
#   3. Drop any triangle whose corners no longer land in three different cubes
#      (it has collapsed to a line or a point).
# Keeps the silhouette while cutting triangles 10x-100x. No external libraries.
# The cube size is searched automatically to land near --target-faces.
#
# USAGE
#   python3 scripts/decimate_akm.py IN.akm --output OUT.akm --target-faces 400
# =============================================================
import argparse
import math
import sys


def parse_akm(path):
    # Read an ASCII-PLY .akm into (verts, faces). verts are 10-tuples
    # (x,y,z, nx,ny,nz, r,g,b,a); columns are read by their declared header order.
    with open(path, "r") as fh:
        lines = fh.read().splitlines()
    vcount = fcount = header_end = 0
    props = []
    in_vertex_block = False
    for idx, line in enumerate(lines):
        p = line.split()
        if not p:
            continue
        if p[0] == "element" and p[1] == "vertex":
            vcount = int(p[2]); in_vertex_block = True
        elif p[0] == "element" and p[1] == "face":
            fcount = int(p[2]); in_vertex_block = False
        elif p[0] == "property" and in_vertex_block:
            props.append(p[-1])
        elif p[0] == "end_header":
            header_end = idx + 1
            break

    def col(name):
        return props.index(name) if name in props else None
    ix, iy, iz = col("x"), col("y"), col("z")
    inx, iny, inz = col("nx"), col("ny"), col("nz")
    # colour channel column indices (r / grn / b / a)
    ir, ig, ib, ia = col("red"), col("green"), col("blue"), col("alpha")

    verts = []
    cur = header_end
    for _ in range(vcount):
        t = lines[cur].split(); cur += 1
        x, y, z = float(t[ix]), float(t[iy]), float(t[iz])
        nx = float(t[inx]) if inx is not None else 0.0
        ny = float(t[iny]) if iny is not None else 1.0
        nz = float(t[inz]) if inz is not None else 0.0
        r = int(t[ir]) if ir is not None else 200
        g = int(t[ig]) if ig is not None else 200
        b = int(t[ib]) if ib is not None else 200
        a = int(t[ia]) if ia is not None else 0
        verts.append((x, y, z, nx, ny, nz, r, g, b, a))

    faces = []
    for _ in range(fcount):
        t = lines[cur].split(); cur += 1
        n = int(t[0])
        idxs = [int(v) for v in t[1:1 + n]]
        for k in range(1, n - 1):
            faces.append((idxs[0], idxs[k], idxs[k + 1]))
    return verts, faces


def cluster_decimate(verts, faces, cell):
    # Merge vertices sharing a cube of side `cell`; average each cluster's
    # position/normal/colour; drop triangles that collapsed; de-duplicate.
    key_to_out = {}
    accum = []
    cluster_of = [0] * len(verts)
    inv = 1.0 / cell
    for i, v in enumerate(verts):
        key = (math.floor(v[0] * inv), math.floor(v[1] * inv), math.floor(v[2] * inv))
        out = key_to_out.get(key)
        if out is None:
            out = len(accum)
            key_to_out[key] = out
            accum.append([0.0] * 10 + [0])
        cluster_of[i] = out
        a = accum[out]
        for c in range(10):
            a[c] += v[c]
        a[10] += 1
    new_verts = []
    for a in accum:
        cnt = max(a[10], 1)
        x, y, z = a[0] / cnt, a[1] / cnt, a[2] / cnt
        nx, ny, nz = a[3] / cnt, a[4] / cnt, a[5] / cnt
        m = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        nx, ny, nz = nx / m, ny / m, nz / m
        new_verts.append((x, y, z, nx, ny, nz,
                          int(round(a[6] / cnt)), int(round(a[7] / cnt)),
                          int(round(a[8] / cnt)), int(round(a[9] / cnt))))
    seen = set()
    new_faces = []
    for (i0, i1, i2) in faces:
        a, b, c = cluster_of[i0], cluster_of[i1], cluster_of[i2]
        if a == b or b == c or a == c:
            continue
        canon = tuple(sorted((a, b, c)))
        if canon in seen:
            continue
        seen.add(canon)
        new_faces.append((a, b, c))
    return new_verts, new_faces


def write_akm(path, verts, faces):
    with open(path, "w") as fh:
        fh.write("ply\nformat ascii 1.0\n")
        fh.write("comment Created by decimate_akm.py FL-4265 - vertex-cluster decimation\n")
        fh.write("element vertex %d\n" % len(verts))
        for name in ("x", "y", "z", "nx", "ny", "nz"):
            fh.write("property float %s\n" % name)
        for name in ("red", "green", "blue", "alpha"):
            fh.write("property uchar %s\n" % name)
        fh.write("element face %d\nproperty list uchar uint vertex_indices\nend_header\n" % len(faces))
        for v in verts:
            fh.write("%.3f %.3f %.3f %.2f %.2f %.2f %d %d %d %d\n" % v)
        for (a, b, c) in faces:
            fh.write("3 %d %d %d\n" % (a, b, c))


def main():
    ap = argparse.ArgumentParser(description="Vertex-cluster decimate an .akm to a target triangle count (FL-4265).")
    ap.add_argument("input")
    ap.add_argument("--output", "-o", required=True)
    ap.add_argument("--target-faces", type=int, default=400)
    args = ap.parse_args()
    verts, faces = parse_akm(args.input)
    if not verts or not faces:
        print("Error: input has no geometry", file=sys.stderr); sys.exit(1)
    xs = [v[0] for v in verts]; ys = [v[1] for v in verts]; zs = [v[2] for v in verts]
    span = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 1e-6)
    lo, hi = span / 400.0, span / 3.0
    best = None
    for _ in range(24):
        cell = (lo + hi) * 0.5
        nv, nf = cluster_decimate(verts, faces, cell)
        count = len(nf)
        if best is None or abs(count - args.target_faces) < abs(best[0] - args.target_faces):
            best = (count, cell, nv, nf)
        if count > args.target_faces:
            lo = cell
        else:
            hi = cell
    count, cell, nv, nf = best
    write_akm(args.output, nv, nf)
    print("decimated %s: %d -> %d faces (%d -> %d verts, cell=%.2f) -> %s" % (
        args.input, len(faces), count, len(verts), len(nv), cell, args.output))


if __name__ == "__main__":
    main()
