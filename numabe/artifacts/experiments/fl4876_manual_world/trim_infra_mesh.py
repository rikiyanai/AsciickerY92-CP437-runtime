"""trim_infra_mesh.py INSTANCE SUFFIX HALFWIDTH_M E,N E,N [E,N ...] [--write] -- hand trim of one long infrastructure
mesh instance in the live map (FL-4876, operator 2026-10-18: the railway-side fence runs across the Numabe level
crossing road). Drops every quad whose centroid lies within HALFWIDTH_M of the polyline through the E,N points (metres,
EPSG:32654), writes a NEW mesh file <stem>_<SUFFIX>.akm beside the old one in the build's meshes/ (never overwrites),
and prints the a3d_edit.py {"op": "mesh"} entry to apply. Without --write it only reports. Decides nothing: the
polyline is chosen by hand from the street photos and the top view."""
import math
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import a3d_edit as A  # noqa: E402

MAP = HERE.parents[2] / ".run/fl4876_builds/live_numabe/output.a3d"


def dseg(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy or 1e-9
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / l2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def main(argv):
    write = "--write" in argv
    argv = [a for a in argv if a != "--write"]
    inst, suffix, hw = argv[0], argv[1], float(argv[2])
    pl = [tuple(float(v) for v in a.split(",")) for a in argv[3:]]
    prefix, ver, recs, tail = A.parse(MAP.read_bytes())
    meta = A.meta_for(MAP)
    r = next(q for q in recs if q["kind"] == "mesh" and q["inst"] == inst)
    src = MAP.parent / "meshes" / r["mesh"]
    lines = src.read_text().splitlines()
    nv = int([l for l in lines if l.startswith("element vertex")][0].split()[-1])
    nf = int([l for l in lines if l.startswith("element face")][0].split()[-1])
    k = lines.index("end_header") + 1
    verts = lines[k:k + nv]
    faces = [[int(x) for x in l.split()[1:]] for l in lines[k + nv:k + nv + nf]]
    tf = r["tf"]

    def world(v):
        x, y, z = (float(c) for c in v.split()[:3])
        X = tf[0] * x + tf[4] * y + tf[8] * z + tf[12]
        Y = tf[1] * x + tf[5] * y + tf[9] * z + tf[13]
        return A.to_en(meta, X, Y)

    W = [world(v) for v in verts]
    keep = []
    for f in faces:
        ce = sum(W[i][0] for i in f) / len(f)
        cn = sum(W[i][1] for i in f) / len(f)
        if min(dseg((ce, cn), a, b) for a, b in zip(pl, pl[1:])) > hw:
            keep.append(f)
    used = sorted({i for f in keep for i in f})
    remap = {old: new for new, old in enumerate(used)}
    head = []
    for l in lines[:k]:
        if l.startswith("element vertex"):
            l = "element vertex %d" % len(used)
        elif l.startswith("element face"):
            l = "element face %d" % len(keep)
        head.append(l)
    out = head + [verts[i] for i in used] + ["%d %s" % (len(f), " ".join(str(remap[i]) for i in f)) for f in keep]
    name = src.stem + "_" + suffix + ".akm"
    print("instance %s mesh %s: faces %d -> %d (dropped %d), verts %d -> %d" % (inst, r["mesh"], nf, len(keep), nf - len(keep), nv, len(used)))
    print("new mesh", name)
    if write:
        dst = MAP.parent / "meshes" / name
        text = "\n".join(out) + "\n"
        if dst.exists() and dst.read_text() != text:
            raise SystemExit("refusing to overwrite a different %s" % dst)
        dst.write_text(text)
        print("wrote", dst)


if __name__ == "__main__":
    main(sys.argv[1:])
