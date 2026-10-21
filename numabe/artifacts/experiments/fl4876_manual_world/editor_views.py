"""editor_views.py -- server half of the A3D editor's extra views (map_editor_ui/editor_views.js).

FL-4876 operator 2026-10-16: first-person camera, top-down "satellite" view of the A3D world, a split view
with a registered reference map below it, and an overlay with per-layer opacity.

Why a geometry export: the Classic runtime (a3d.html) cannot draw either view. Its renderer has a fixed 30 degree
orthographic tilt, and its perspective mode depth-tests on HEIGHT, not distance (engine/render/
render_world_pass.cpp: xyzf[..][2] = vz in the Euclidean perspective branch), so at street level far roofs
overwrite near walls. The views therefore draw the build's own geometry in WebGL2: the terrain patches and every
mesh instance of output.a3d, the .akm (ASCII PLY) meshes with their vertex colours, and the 256-entry
material table, all read from the same files the runtime loads (map_editor.World.map_bundle). Nothing is written.

Routes (map_editor.Handler, prefix /api/ev/):
  meta      projection (terrain_metadata.json: world = (E - origin) * content_scale + offset), the aerial
            mosaic affine (map_editor.aerial_affine, UTM -> mosaic px) and the terrain grid size
  geometry  binary 'EVG1' bundle (layout in GEOMETRY_DOC below), built once per server process (~10 s)
  osm       vector street map in UTM E/N from the AOI OSM extract: roads, buildings, rail, water, green, labels
"""
from __future__ import annotations

import importlib.util
import json
import math
import struct
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
_fmt_spec = importlib.util.spec_from_file_location("a3d_format", ROOT / "addons" / "io_asciicker" / "scene" / "a3d_format.py")
a3d_format = importlib.util.module_from_spec(_fmt_spec)
_fmt_spec.loader.exec_module(a3d_format)

HC, VC, HS = a3d_format.HEIGHT_CELLS, a3d_format.VISUAL_CELLS, a3d_format.HEIGHT_SCALE  # 4, 8, 16
PATCH_BYTES, MAT_BYTES = 188, 512
GEOMETRY_DOC = """EVG1 layout: b'EVG1', u32 header_len, header JSON (utf-8, space padded to 4 bytes), then the sections the
header lists as {name: [offset, byte_length, dtype, shape]} with offsets from the start of the data block.
  heights  uint16 [gh, gw]  terrain vertex heights in engine z units; vertex (i, j) is world (2 i + 8 ox, 2 j + 8 oy)
                            (patch p covers world [8 p, 8 p + 8), 5 x 5 vertices, HEIGHT_CELLS = 4)
  visual   uint8  [vh, vw]  terrain material id (visual word & 0xFF) of world cell (u, v) = [u + 8 ox, u + 8 ox + 1) x [v + 8 oy, ...)
  header "origin_patch" [ox, oy] = lowest patch coordinate (<= 0); absent in bundles older than the terrain extension = [0, 0]
  matlib   uint8  [256, 4, 16, 8]  material shade ramps: fg r g b, glyph, bg r g b, flags (A3DMaterial)
  pos      float32 [nv, 3]  mesh vertices in world units, z = engine z / 16 (isotropic: 1 m = content_scale)
  nrm      int8   [nv, 4]   unit normal * 127 (w unused)
  col      uint8  [nv, 4]   vertex colour rgb, a = 255
  idx      uint32 [ni]      triangle list (polygons fan-triangulated)"""

_LOCK = threading.Lock()


def _read_ply(path: Path):
    """ASCII PLY (.akm) -> (verts [n, 3], normals [n, 3], colours [n, 3] uint8, triangles [m, 3])."""
    text = path.read_text()
    head, body = text.split("end_header\n", 1)
    props, nv, nf = [], 0, 0
    in_vertex = False
    for line in head.splitlines():
        t = line.split()
        if t[:2] == ["element", "vertex"]:
            nv, in_vertex = int(t[2]), True
        elif t[:2] == ["element", "face"]:
            nf, in_vertex = int(t[2]), False
        elif t and t[0] == "property" and in_vertex:
            props.append(t[-1])
    lines = body.split("\n")
    v = np.array(" ".join(lines[:nv]).split(), dtype=np.float64).reshape(nv, len(props)) if nv else np.zeros((0, len(props)))
    col = lambda *names: [props.index(n) for n in names if n in props]
    xyz = v[:, col("x", "y", "z")]
    nrm = v[:, col("nx", "ny", "nz")] if len(col("nx", "ny", "nz")) == 3 else np.zeros_like(xyz)
    rgb = v[:, col("red", "green", "blue")].astype(np.uint8) if len(col("red", "green", "blue")) == 3 else np.full((nv, 3), 200, np.uint8)
    tris = []
    for line in lines[nv:nv + nf]:
        f = line.split()
        if not f:
            continue
        k = int(f[0])
        ids = [int(x) for x in f[1:1 + k]]
        for i in range(1, k - 1):
            tris.append((ids[0], ids[i], ids[i + 1]))
    return xyz, nrm, rgb, np.array(tris, dtype=np.int64).reshape(-1, 3)


def _read_a3d(path: Path):
    """Terrain patches, material table and mesh instances of an .a3d."""
    with path.open("rb") as f:
        hdr = a3d_format.A3DHeader.from_file(f)
        raw = f.read(hdr.num_patches * PATCH_BYTES)
        matlib = f.read(256 * MAT_BYTES)
        raw_ver = struct.unpack("<i", f.read(4))[0]
        ver = -raw_ver if raw_ver < 0 else raw_ver
        count = struct.unpack("<i", f.read(4))[0]
        insts = [a3d_format.A3DInstance.from_file(f, format_version=ver) for _ in range(count)]
    rec = np.dtype([("x", "<i4"), ("y", "<i4"), ("visual", "<u2", (VC, VC)), ("height", "<u2", (HC + 1, HC + 1)), ("diag", "<u2")])
    patches = np.frombuffer(raw, dtype=rec)
    return patches, matlib, insts


def build_geometry(build: Path) -> bytes:
    """The EVG1 bundle of one build (see GEOMETRY_DOC)."""
    patches, matlib, insts = _read_a3d(build / "output.a3d")
    # Patch coordinates may be negative (a3d_terrain_extend.py adds terrain west/south of the first patch; the engine quadtree
    # supports it, terrain.cpp AddTerrainPatch). The grids start at the lowest patch: heights[0, 0] is world (8 ox, 8 oy).
    ox, oy = min(0, int(patches["x"].min())), min(0, int(patches["y"].min()))
    px, py = int(patches["x"].max()) + 1 - ox, int(patches["y"].max()) + 1 - oy
    gw, gh, vw, vh = HC * px + 1, HC * py + 1, VC * px, VC * py
    heights = np.zeros((gh, gw), np.uint16)
    visual = np.zeros((vh, vw), np.uint8)
    for p in patches:  # 115k-230k patches: plain loop over numpy slices (~1-2 s)
        x, y = int(p["x"]) - ox, int(p["y"]) - oy
        heights[HC * y:HC * y + HC + 1, HC * x:HC * x + HC + 1] = p["height"]
        visual[VC * y:VC * y + VC, VC * x:VC * x + VC] = p["visual"] & 0xFF
    cache, P, N, C, I, base = {}, [], [], [], [], 0
    for inst in insts:
        if inst.variant != "mesh" or not inst.mesh_name:
            continue
        if inst.mesh_name not in cache:
            p = build / "meshes" / inst.mesh_name
            cache[inst.mesh_name] = _read_ply(p) if p.is_file() else None
        m = cache[inst.mesh_name]
        if m is None or not len(m[3]):
            continue
        xyz, nrm, rgb, tri = m
        M = np.array(inst.transform, dtype=np.float64).reshape(4, 4).T  # column-major file -> row-major matrix
        w = xyz @ M[:3, :3].T + M[:3, 3]
        w[:, 2] /= HS  # engine z units -> world units
        R = M[:3, :3].copy()
        R[2, :] /= HS  # same scaling on the normal transform (rotation about z for every instance seen)
        n = nrm @ np.linalg.inv(R)
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
        P.append(w.astype(np.float32)); N.append(np.round(n * 127).astype(np.int8)); C.append(rgb); I.append(tri + base)
        base += len(xyz)
    pos = np.concatenate(P) if P else np.zeros((0, 3), np.float32)
    nrm4 = np.zeros((len(pos), 4), np.int8)
    col4 = np.full((len(pos), 4), 255, np.uint8)
    if P:
        nrm4[:, :3] = np.concatenate(N)
        col4[:, :3] = np.concatenate(C)
    idx = (np.concatenate(I) if I else np.zeros((0, 3), np.int64)).astype(np.uint32).ravel()
    sections, blobs, off = {}, [], 0
    for name, arr in (("heights", heights), ("visual", visual), ("matlib", np.frombuffer(matlib, np.uint8).reshape(256, 4, 16, 8)),
                      ("pos", pos), ("nrm", nrm4), ("col", col4), ("idx", idx)):
        b = np.ascontiguousarray(arr).tobytes()
        sections[name] = [off, len(b), str(arr.dtype), list(arr.shape)]
        blobs.append(b + b"\0" * (-len(b) % 4))
        off += len(blobs[-1])
    head = json.dumps({"schema": "fl4876.editor_views.geometry.v1", "build": str(build), "doc": GEOMETRY_DOC,
                       "patches": [px, py], "origin_patch": [ox, oy],
                       "instances": len(insts), "meshes": len(cache), "sections": sections}).encode()
    head += b" " * (-len(head) % 4)
    return b"EVG1" + struct.pack("<I", len(head)) + head + b"".join(blobs)


# --- OSM vector map -------------------------------------------------------------------------------------------
ROAD_CLASSES = ("motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
                "service", "pedestrian", "footway", "path", "steps", "cycleway", "track", "corridor", "platform")
GREEN = {"park", "grass", "garden", "forest", "wood", "scrub", "meadow", "cemetery", "recreation_ground", "playground",
         "pitch", "village_green", "orchard", "farmland", "allotments", "golf_course", "grassland"}


def build_osm(osm_path: Path, ll_to_utm) -> dict:
    """Ways of the OSM extract as styled E/N features + named point labels (for the 'normal map' layer)."""
    root = ET.parse(osm_path).getroot()
    nodes, pois = {}, []
    for n in root.iter("node"):
        E, N = ll_to_utm(float(n.get("lon")), float(n.get("lat")))
        nodes[n.get("id")] = (round(E, 2), round(N, 2))
        tags = {t.get("k"): t.get("v") for t in n.iter("tag")}
        if tags.get("name") and (tags.get("shop") or tags.get("amenity") or tags.get("railway") or tags.get("public_transport")
                                 or tags.get("tourism") or tags.get("place") or tags.get("historic")):
            pois.append({"n": tags["name"], "k": tags.get("railway") or tags.get("amenity") or tags.get("shop") or tags.get("place") or "poi",
                         "p": nodes[n.get("id")]})
    ways, way_pts = [], {}
    for w in root.iter("way"):
        pts = [nodes[r.get("ref")] for r in w.iter("nd") if r.get("ref") in nodes]
        way_pts[w.get("id")] = pts
        tags = {t.get("k"): t.get("v") for t in w.iter("tag")}
        f = _classify(tags)
        if f and len(pts) >= 2:
            ways.append({**f, "p": pts, "id": "way/" + w.get("id")})
    for r in root.iter("relation"):  # multipolygons: draw each outer ring with the relation's class
        tags = {t.get("k"): t.get("v") for t in r.iter("tag")}
        if tags.get("type") != "multipolygon":
            continue
        f = _classify(tags)
        if not f:
            continue
        for m in r.iter("member"):
            if m.get("type") == "way" and m.get("role") in ("outer", "") and len(way_pts.get(m.get("ref"), [])) >= 3:
                ways.append({**f, "area": True, "p": way_pts[m.get("ref")], "id": "relation/" + r.get("id")})
    order = {"green": 0, "landuse": 0, "water": 1, "parking": 1, "building": 3, "rail": 4, "road": 2, "waterway": 1, "platform": 3}
    ways.sort(key=lambda f: (order.get(f["k"], 5), ROAD_CLASSES.index(f["c"]) * -1 if f["k"] == "road" and f["c"] in ROAD_CLASSES else 0))
    return {"schema": "fl4876.editor_views.osm.v1", "source": str(osm_path.relative_to(ROOT)), "ways": ways, "pois": pois}


def _classify(t: dict):
    """OSM tags -> {k: kind, c: class, n: name, area: bool} or None (not drawn)."""
    name = t.get("name")
    if t.get("building") or t.get("building:part"):
        return {"k": "building", "c": t.get("building") or "yes", "n": name, "area": True}
    if t.get("highway") in ROAD_CLASSES:
        area = t.get("area") == "yes"
        return {"k": "road", "c": t["highway"], "n": name, "area": area}
    if t.get("railway") in ("rail", "subway", "light_rail", "narrow_gauge", "monorail", "tram"):
        return {"k": "rail", "c": t["railway"], "n": name, "area": False}
    if t.get("railway") == "platform" or t.get("public_transport") == "platform":
        return {"k": "platform", "c": "platform", "n": name, "area": True}
    if t.get("waterway"):
        return {"k": "waterway", "c": t["waterway"], "n": name, "area": t["waterway"] in ("riverbank", "dock")}
    if t.get("natural") == "water" or t.get("water") or t.get("landuse") in ("reservoir", "basin"):
        return {"k": "water", "c": "water", "n": name, "area": True}
    if t.get("amenity") == "parking":
        return {"k": "parking", "c": "parking", "n": name, "area": True}
    g = t.get("leisure") or t.get("landuse") or t.get("natural")
    if g in GREEN:
        return {"k": "green", "c": g, "n": name, "area": True}
    if t.get("landuse") or t.get("amenity") in ("school", "university", "kindergarten", "place_of_worship", "hospital"):
        return {"k": "landuse", "c": t.get("landuse") or t.get("amenity"), "n": name, "area": True}
    return None


def route(world, sub: str, q: dict, ll_to_utm, osm_path: Path):
    """(status, body, content type) for /api/ev/<sub>; results are cached on the World object."""
    with _LOCK:
        if sub == "meta":
            P = world.proj
            m = json.loads((world.build / "terrain_metadata.json").read_text())
            return 200, {"proj": {"content_scale": P.s, "origin_easting": P.oe, "origin_northing": P.on, "offset_x": P.dx, "offset_y": P.dy,
                                  "convention": "x = (E - origin_easting) * content_scale + offset_x (terrain_metadata.json)"},
                         "terrain_origin_aware": True,  # geometry carries origin_patch (negative patch coordinates)
                         "terrain_bounds": m.get("terrain_bounds"), "aerial": world.aerial, "build": str(world.build.relative_to(ROOT))}, "application/json"
        if sub == "geometry":
            # MAP-EDIT-01: rebuild after a saved hand edit of output.a3d or a new mesh file
            _stamp = tuple((p.stat().st_mtime_ns, p.stat().st_size) if p.exists() else None
                           for p in (world.build / "output.a3d", world.build / "meshes"))
            if getattr(world, "_ev_geometry", None) is None or getattr(world, "_ev_stamp", None) != _stamp:
                world._ev_geometry = build_geometry(world.build)
                world._ev_stamp = _stamp
            return 200, world._ev_geometry, "application/octet-stream"
        if sub == "osm":
            if getattr(world, "_ev_osm", None) is None:
                world._ev_osm = build_osm(osm_path, ll_to_utm)
            return 200, world._ev_osm, "application/json"
    return 404, {"error": "unknown /api/ev route " + sub}, "application/json"


if __name__ == "__main__":  # timing check: python3 editor_views.py BUILD_DIR
    import sys
    import time
    t = time.time()
    data = build_geometry(Path(sys.argv[1]).resolve())
    print("geometry", len(data) >> 20, "MB in", round(time.time() - t, 1), "s")
