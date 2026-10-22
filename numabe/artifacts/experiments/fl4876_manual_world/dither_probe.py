"""dither_probe.py BUILD_DIR OUT_MESH_ROOT E N RADIUS_M -- render experiment only (decides nothing, writes no owner):
copies BUILD_DIR/meshes to OUT_MESH_ROOT and, for every building mass/shell mesh whose footprint centroid lies within
RADIUS_M of (E, N), replaces vertices carrying the cube-snapped wall colour with the EXACT hand wall colour (lit sample
or hand-judged sample), so headed Classic shows how an off-cube wall renders (Classic dithers off-cube colours).
Question it answers: does an exact cream / greige wall read as cream under Classic's dither, or as noise?"""
import json, math, shutil, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
build, out = Path(sys.argv[1]), Path(sys.argv[2])
E0, N0, R = float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
LEV = (0, 51, 102, 153, 204, 255)
snap_old = lambda rgb: tuple(min(LEV, key=lambda l: abs(l - v)) for v in rgb)
sys.path.insert(0, str(ROOT))
from scripts.pipeline.compile_facade_shell import quantise_safe_cube  # noqa: E402  (CIELAB-nearest, new)
B = ROOT / "assets/source_bundles/numabe_connected_neighborhood_scope_20260915"
bl = json.loads((B / "authored_building_appearance.json").read_text())["buildings"]
inf = json.loads((B / "inferred_building_palette.json").read_text())["buildings"]
canon = json.loads((build / "canonical.world_ir.json").read_text())
if out.exists():
    shutil.rmtree(out)
shutil.copytree(build / "meshes", out)
n_files = n_verts = 0
for f in canon["canonical_features"]:
    if f.get("semantic_class") != "building_footprint":
        continue
    q = f["geometry"]["coordinates"]
    while isinstance(q[0][0], list):
        q = q[0]
    cx, cy = sum(p[0] for p in q) / len(q), sum(p[1] for p in q) / len(q)
    if math.hypot(cx - E0, cy - N0) > R:
        continue
    k = f["feature_id"]
    wall = (bl.get(k) or {}).get("wall_rgb") or (inf.get(k) or {}).get("wall_rgb")
    if not wall:
        continue
    wall = tuple(int(v) for v in wall)
    targets = {snap_old(wall), quantise_safe_cube(wall)}
    tail = k.split(":")[-1].replace("/", "_")
    for mesh in list(out.glob("*%s.akm" % tail)) + list(out.glob("*%s_*.akm" % tail)):
        lines = mesh.read_text().split("\n")
        hdr = lines.index("end_header")
        nv = next(int(l.split()[-1]) for l in lines[:hdr] if l.startswith("element vertex"))
        changed = 0
        for i in range(hdr + 1, hdr + 1 + nv):
            p = lines[i].split()
            if tuple(int(v) for v in p[6:9]) in targets:
                p[6:9] = [str(v) for v in wall]; lines[i] = " ".join(p); changed += 1
        if changed:
            mesh.write_text("\n".join(lines)); n_files += 1; n_verts += changed
print("probe mesh root", out, "| meshes rewritten", n_files, "| wall vertices set to exact colour", n_verts)
