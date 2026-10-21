#!/usr/bin/env python3
"""FL-4820: convert a .glb mesh into asciicker .akm (ASCII PLY) with vertex colours.

WHY THIS EXISTS
---------------
`bake_akm_to_static_world_faces.py` bakes ONE material id + shade per face. It has
no texture sampler, so a textured model loses its appearance entirely. glTF
Vertex colours survive that bake; a texture does not.

Many PS1-style packs store COLOR_0 as a greyscale ambient-occlusion mask and keep
the real hue in the texture (the birch pack does exactly this: 83 of 101 vertices
are pure white). Carrying COLOR_0 through alone therefore yields WHITE trees. So
this converter BAKES the texture into vertex colour: it samples the embedded image
at each vertex's TEXCOORD_0 and multiplies by COLOR_0 as shading. A mesh with
neither texture nor usable COLOR_0 is refused rather than silently whitened.

COORDINATE SPACE
----------------
glTF is Y-up right-handed; .akm/A3D model space is Z-up. Mapping used (same as
Blender's glTF importer):  akm = (x, -z, y).

SIZE FITTING
------------
An authored FL-4708 instance basis already carries its source asset's historical
normalization, so a replacement mesh must present the SAME model-space height as
the mesh it replaces or every instance transform will scale it wrong. `--fit-to`
rescales uniformly (preserving the replacement's own aspect) so its model Z
extent matches the target's, then centres it on the target's XY and seats its
base at the target's Z minimum.
"""
import argparse, io, json, os, struct, sys

COMPONENT = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2),
             5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def read_glb(path):
    data = open(path, "rb").read()
    if data[:4] != b"glTF":
        raise SystemExit("not a GLB: %s" % path)
    off, js, bin_chunk = 12, None, b""
    while off < len(data):
        clen, ctype = struct.unpack_from("<I4s", data, off)
        off += 8
        if ctype == b"JSON":
            js = json.loads(data[off:off + clen])
        elif ctype == b"BIN\x00":
            bin_chunk = data[off:off + clen]
        off += clen
    return js, bin_chunk


def accessor(gltf, blob, index):
    acc = gltf["accessors"][index]
    fmt, size = COMPONENT[acc["componentType"]]
    ncomp = NCOMP[acc["type"]]
    view = gltf["bufferViews"][acc["bufferView"]]
    base = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = view.get("byteStride") or (size * ncomp)
    out = []
    for i in range(acc["count"]):
        start = base + i * stride
        out.append(struct.unpack_from("<" + fmt * ncomp, blob, start))
    norm = acc.get("normalized", False)
    if norm and acc["componentType"] in (5121, 5123):
        top = 255.0 if acc["componentType"] == 5121 else 65535.0
        out = [tuple(c / top for c in v) for v in out]
    return out


def material_image(gltf, blob, prim):
    """Decoded base-colour image for one primitive, or None."""
    mat_index = prim.get("material")
    if mat_index is None:
        return None
    pbr = gltf["materials"][mat_index].get("pbrMetallicRoughness", {})
    tex_ref = pbr.get("baseColorTexture")
    if not tex_ref:
        return None
    image_index = gltf["textures"][tex_ref["index"]].get("source")
    if image_index is None:
        return None
    image = gltf["images"][image_index]
    if "bufferView" not in image:
        return None
    view = gltf["bufferViews"][image["bufferView"]]
    start = view.get("byteOffset", 0)
    raw = blob[start:start + view["byteLength"]]
    try:
        from PIL import Image
    except ImportError:
        return None
    return Image.open(io.BytesIO(raw)).convert("RGBA")


def sample(img, u, v, fallback=(180, 180, 180)):
    """Alpha-aware sample.

    These packs use alpha-cut foliage cards, so a naive point sample frequently
    lands on a masked-out texel whose RGB is black. That would bake black leaves.
    Search outward for opaque texels and average them; fall back to the mesh's
    mean opaque colour if the whole neighbourhood is cut away.
    """
    w, h = img.size
    cx = int(u % 1.0 * w) % w
    cy = int((1.0 - v % 1.0) * h) % h        # glTF UV origin is top-left
    for radius in (0, 1, 2, 4, 8):
        acc, n = [0, 0, 0], 0
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                px = img.getpixel(((cx + dx) % w, (cy + dy) % h))
                if px[3] > 128 and sum(px[:3]) > 12:
                    acc[0] += px[0]; acc[1] += px[1]; acc[2] += px[2]; n += 1
        if n:
            return (acc[0] // n, acc[1] // n, acc[2] // n)
    return fallback


def mean_opaque(img):
    w, h = img.size
    acc, n = [0, 0, 0], 0
    for y in range(0, h, 4):
        for x in range(0, w, 4):
            px = img.getpixel((x, y))
            if px[3] > 128 and sum(px[:3]) > 12:
                acc[0] += px[0]; acc[1] += px[1]; acc[2] += px[2]; n += 1
    return (acc[0] // n, acc[1] // n, acc[2] // n) if n else (180, 180, 180)


def face_color(img, uv_a, uv_b, uv_c, fallback):
    """Mean opaque colour over a triangle's UV region.

    Alpha-cut foliage cards put their vertices at the CORNERS of a mostly
    transparent sprite rectangle, so those corner texels are empty and per-vertex
    sampling returns black or bark grey. The only colour that means anything for
    such a card is the average of its visible texels, applied flat to the face -
    which is also exactly the granularity bake_akm_to_static_world_faces.py keeps
    (one material id + shade per face).
    """
    w, h = img.size
    us = [uv_a[0] % 1.0, uv_b[0] % 1.0, uv_c[0] % 1.0]
    vs = [uv_a[1] % 1.0, uv_b[1] % 1.0, uv_c[1] % 1.0]
    x0, x1 = int(min(us) * w), max(int(max(us) * w), int(min(us) * w) + 1)
    y0, y1 = int((1.0 - max(vs)) * h), max(int((1.0 - min(vs)) * h), int((1.0 - max(vs)) * h) + 1)
    x0 = max(0, min(w - 1, x0)); x1 = max(1, min(w, x1))
    y0 = max(0, min(h - 1, y0)); y1 = max(1, min(h, y1))
    step = max(1, (x1 - x0) // 24, (y1 - y0) // 24)
    acc, n = [0, 0, 0], 0
    for y in range(y0, y1, step):
        for x in range(x0, x1, step):
            px = img.getpixel((x, y))
            if px[3] > 128 and sum(px[:3]) > 12:
                acc[0] += px[0]; acc[1] += px[1]; acc[2] += px[2]; n += 1
    if n:
        return (acc[0] // n, acc[1] // n, acc[2] // n)
    return sample(img, us[0], vs[0], fallback)


def mesh_geometry(gltf, blob, mesh):
    """Emit one colour per FACE by duplicating vertices per triangle.

    Face count is unchanged - and faces, not vertices, are what the FL-4789
    static-face domain charges for.
    """
    verts, cols, faces = [], [], []
    for prim in mesh.get("primitives", []):
        attrs = prim.get("attributes", {})
        if "POSITION" not in attrs:
            continue
        pos = accessor(gltf, blob, attrs["POSITION"])
        shade = accessor(gltf, blob, attrs["COLOR_0"]) if "COLOR_0" in attrs else None
        uvs = accessor(gltf, blob, attrs["TEXCOORD_0"]) if "TEXCOORD_0" in attrs else None
        img = material_image(gltf, blob, prim)
        if img is None and shade is None:
            raise SystemExit("mesh %r has neither a base-colour texture nor COLOR_0; "
                             "refusing rather than emitting white geometry" % mesh.get("name"))
        fallback = mean_opaque(img) if img is not None else (180, 180, 180)
        idx = [i[0] for i in accessor(gltf, blob, prim["indices"])]
        for t in range(0, len(idx) - 2, 3):
            tri = (idx[t], idx[t + 1], idx[t + 2])
            if img is not None and uvs is not None:
                c = face_color(img, uvs[tri[0]], uvs[tri[1]], uvs[tri[2]], fallback)
            else:
                c = (255, 255, 255)
            if shade is not None:
                m = sum(shade[tri[0]][:3]) / 3.0
                c = tuple(max(0, min(255, int(round(ch * m)))) for ch in c)
            base = len(verts)
            for vi in tri:
                p = pos[vi]
                verts.append((p[0], -p[2], p[1]))   # glTF Y-up -> akm Z-up
                cols.append(c)
            faces.append((base, base + 1, base + 2))
    return verts, cols, faces


def fit(verts, target):
    """Uniform rescale so the replacement fits INSIDE the target's envelope.

    Fitting on height alone is wrong when the replacement has a wider aspect than
    the mesh it replaces: matching Z made the birches 8.8 x 10.8 against tree-3's
    5.7 x 5.7, nearly double the footprint, and 91 of them merged into one canopy.
    Scaling by the tightest of the three axis ratios preserves the replacement's
    own proportions while guaranteeing it never occupies more space than the tree
    it stands in for.
    """
    tx0, tx1, ty0, ty1, tz0, tz1 = target
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    src = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    dst = (tx1 - tx0, ty1 - ty0, tz1 - tz0)
    if min(src) <= 1e-9:
        raise SystemExit("degenerate source extent")
    # Height is what makes a tree read as a tree at isometric distance, so the
    # vertical axis matches the target exactly. The footprint is clamped so the
    # replacement never spreads wider than the tree it replaces - a pure uniform
    # fit on the tightest axis let the birch's flat card layout bind on depth and
    # halved its height.
    sv = dst[2] / src[2]
    sh = min(sv, dst[0] / src[0], dst[1] / src[1])
    cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    tcx, tcy = (tx0 + tx1) / 2.0, (ty0 + ty1) / 2.0
    return [((v[0] - cx) * sh + tcx, (v[1] - cy) * sh + tcy,
             (v[2] - min(zs)) * sv + tz0) for v in verts]


def write_akm(path, verts, cols, faces, comment):
    lines = ["ply", "format ascii 1.0", "comment %s" % comment,
             "element vertex %d" % len(verts),
             "property float x", "property float y", "property float z",
             "property uchar red", "property uchar green", "property uchar blue",
             "property uchar alpha",
             "element face %d" % len(faces),
             "property list uchar uint vertex_indices", "end_header"]
    for v, c in zip(verts, cols):
        lines.append("%.4f %.4f %.4f %d %d %d 255" % (v[0], v[1], v[2], c[0], c[1], c[2]))
    for f in faces:
        lines.append("3 %d %d %d" % f)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write("\n".join(lines) + "\n")


def akm_bbox(path):
    lines = open(path).read().split("\n")
    h = lines.index("end_header")
    nv = next(int(l.split()[2]) for l in lines[:h] if l.startswith("element vertex"))
    v = [tuple(map(float, l.split()[:3])) for l in lines[h + 1:h + 1 + nv]]
    return (min(a[0] for a in v), max(a[0] for a in v),
            min(a[1] for a in v), max(a[1] for a in v),
            min(a[2] for a in v), max(a[2] for a in v))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("glb")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--prefix", default="")
    ap.add_argument("--fit-to", action="append", default=[],
                    help="path to an .akm whose model bbox the output should match; repeatable")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    gltf, blob = read_glb(args.glb)
    meshes = gltf.get("meshes", [])
    if args.list:
        for i, m in enumerate(meshes):
            print(i, m.get("name"))
        return 0

    targets = {os.path.basename(p).replace(".akm", ""): akm_bbox(p) for p in args.fit_to}
    made = []
    for m in meshes:
        verts, cols, faces = mesh_geometry(gltf, blob, m)
        stem = (m.get("name") or "mesh").split("_")[2] if "_" in (m.get("name") or "") else m.get("name")
        stem = "".join(ch for ch in str(stem) if ch.isalnum() or ch in "-_").lower()
        for tname, tbox in (targets.items() or [("", None)]):
            fitted = fit(verts, tbox) if tbox else verts
            name = "%s%s.as-%s.akm" % (args.prefix, stem, tname) if tname else "%s%s.akm" % (args.prefix, stem)
            out = os.path.join(args.out_dir, name)
            write_akm(out, fitted, cols, faces,
                      "FL-4820 converted from %s (COLOR_0 preserved)" % os.path.basename(args.glb))
            made.append((name, len(verts), len(faces)))
    for n, v, f in made:
        print("%-34s verts=%-5d faces=%d" % (n, v, f))
    return 0


if __name__ == "__main__":
    sys.exit(main())
