# Ad hoc script: Find vertical wall faces in game_map_y8 sac_and_math_color for FL-4128 headed collision repro
# Created: 2026-05-28
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
import argparse, importlib.util, math, struct, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
fmt_path = ROOT / 'addons' / 'io_asciicker' / 'scene' / 'a3d_format.py'
spec = importlib.util.spec_from_file_location('a3d_format', fmt_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def read_a3d_instances(path):
    with open(path, 'rb') as f:
        header = mod.A3DHeader.from_file(f)
        for _ in range(header.num_patches):
            mod.A3DPatch.from_file(f)
        for _ in range(256):
            mod.A3DMaterial.read(f)
        raw_fmt = struct.unpack('<i', f.read(4))[0]
        fmt_ver = -raw_fmt if raw_fmt < 0 else raw_fmt
        inst_count = struct.unpack('<i', f.read(4))[0]
        return [mod.A3DInstance.from_file(f, fmt_ver) for _ in range(inst_count)]


def parse_ply_faces(path):
    with open(path, 'r', errors='replace') as f:
        if f.readline().strip() != 'ply':
            raise SystemExit(f'not ply: {path}')
        f.readline()
        nverts = nfaces = 0
        props = []
        mode = None
        for line in f:
            line = line.strip()
            if line == 'end_header':
                break
            parts = line.split()
            if len(parts) >= 3 and parts[0] == 'element' and parts[1] == 'vertex':
                nverts = int(parts[2]); mode = 'vertex'; continue
            if len(parts) >= 3 and parts[0] == 'element' and parts[1] == 'face':
                nfaces = int(parts[2]); mode = 'face'; continue
            if len(parts) >= 3 and parts[0] == 'property' and mode == 'vertex':
                props.append(parts[-1])
        prop = {p:i for i,p in enumerate(props)}
        verts = []
        for _ in range(nverts):
            vals = f.readline().split()
            verts.append((float(vals[prop['x']]), float(vals[prop['y']]), float(vals[prop['z']])))
        faces = []
        for _ in range(nfaces):
            vals = f.readline().split()
            if not vals: continue
            n = int(vals[0])
            idxs = [int(v) for v in vals[1:1+n]]
            if n >= 3:
                for i in range(1, n-1):
                    faces.append((idxs[0], idxs[i], idxs[i+1]))
        return verts, faces


def transform_point(tm, p):
    x,y,z = p
    return (
        tm[0]*x + tm[4]*y + tm[8]*z + tm[12],
        tm[1]*x + tm[5]*y + tm[9]*z + tm[13],
        tm[2]*x + tm[6]*y + tm[10]*z + tm[14],
    )


def sub(a,b): return (a[0]-b[0], a[1]-b[1], a[2]-b[2])
def cross(a,b): return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])
def norm(v):
    l = math.sqrt(v[0]*v[0]+v[1]*v[1]+v[2]*v[2])
    return (v[0]/l, v[1]/l, v[2]/l) if l > 1e-9 else (0,0,0)
def area(a,b,c):
    cr = cross(sub(b,a), sub(c,a)); return 0.5 * math.sqrt(cr[0]*cr[0]+cr[1]*cr[1]+cr[2]*cr[2])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--a3d', default='assets/a3d/game_map_y8.a3d')
    ap.add_argument('--mesh', default='sac_and_math_color.akm')
    ap.add_argument('--limit', type=int, default=40)
    ap.add_argument('--face', type=int, action='append', default=[])
    args = ap.parse_args()
    a3d = ROOT / args.a3d
    insts = [i for i in read_a3d_instances(a3d) if getattr(i, 'variant', '') == 'mesh' and i.mesh_name == args.mesh]
    if not insts:
        raise SystemExit(f'no mesh inst {args.mesh}')
    inst = insts[0]
    mesh = ROOT / 'assets' / 'meshes' / args.mesh
    verts, faces = parse_ply_faces(mesh)
    rows = []
    by_ordinal = {}
    for ordinal, face in enumerate(faces):
        pts = [transform_point(inst.transform, verts[i]) for i in face]
        n = norm(cross(sub(pts[1], pts[0]), sub(pts[2], pts[0])))
        a = area(*pts)
        if a < 1.0: continue
        # vertical or near vertical wall-ish faces, not roofs/floors
        if abs(n[2]) > 0.25: continue
        cx = sum(p[0] for p in pts)/3.0
        cy = sum(p[1] for p in pts)/3.0
        cz = sum(p[2] for p in pts)/3.0
        zmin = min(p[2] for p in pts); zmax = max(p[2] for p in pts)
        if zmax - zmin < 4.0: continue
        # approach from normal side and opposite side, 4 world units away
        row = {
            'ordinal': ordinal, 'area': a, 'n': n, 'center': (cx,cy,cz), 'z': (zmin,zmax),
            'from_pos': (cx + n[0]*6.0, cy + n[1]*6.0, cz),
            'to_pos': (cx - n[0]*2.0, cy - n[1]*2.0, cz),
            'reverse_from': (cx - n[0]*6.0, cy - n[1]*6.0, cz),
            'reverse_to': (cx + n[0]*2.0, cy + n[1]*2.0, cz),
            'dist_from_spawn': math.hypot(cx - -2.8, cy - -73.6),
            'pts': pts,
            'bbox_xy': (
                min(p[0] for p in pts), max(p[0] for p in pts),
                min(p[1] for p in pts), max(p[1] for p in pts),
            ),
        }
        rows.append(row)
        by_ordinal[ordinal] = row
    rows.sort(key=lambda r: (r['dist_from_spawn'], -r['area']))
    print(f'mesh={args.mesh} inst_pos=({inst.transform[12]:.3f},{inst.transform[13]:.3f},{inst.transform[14]:.3f}) vertical_candidates={len(rows)}')
    for face_id in args.face:
        r = by_ordinal.get(face_id)
        if not r:
            print(f'face={face_id} not in filtered vertical candidates')
            continue
        print('DETAIL face={ordinal} area={area:.1f} center=({cx:.2f},{cy:.2f},{cz:.2f}) z=({z0:.2f},{z1:.2f}) bbox_xy=({x0:.2f},{x1:.2f},{y0:.2f},{y1:.2f}) n=({nx:.3f},{ny:.3f},{nz:.3f}) pts={pts}'.format(
            ordinal=r['ordinal'], area=r['area'], cx=r['center'][0], cy=r['center'][1], cz=r['center'][2],
            z0=r['z'][0], z1=r['z'][1], x0=r['bbox_xy'][0], x1=r['bbox_xy'][1],
            y0=r['bbox_xy'][2], y1=r['bbox_xy'][3], nx=r['n'][0], ny=r['n'][1], nz=r['n'][2],
            pts=';'.join(f'({p[0]:.2f},{p[1]:.2f},{p[2]:.2f})' for p in r['pts'])))
    for r in rows[:args.limit]:
        print('face={ordinal} dist_spawn={dist_from_spawn:.1f} area={area:.1f} center=({cx:.2f},{cy:.2f},{cz:.2f}) z=({z0:.2f},{z1:.2f}) n=({nx:.3f},{ny:.3f},{nz:.3f}) from=({fx:.2f},{fy:.2f}) to=({tx:.2f},{ty:.2f}) rev_from=({rfx:.2f},{rfy:.2f}) rev_to=({rtx:.2f},{rty:.2f})'.format(
            ordinal=r['ordinal'], dist_from_spawn=r['dist_from_spawn'], area=r['area'],
            cx=r['center'][0], cy=r['center'][1], cz=r['center'][2], z0=r['z'][0], z1=r['z'][1],
            nx=r['n'][0], ny=r['n'][1], nz=r['n'][2],
            fx=r['from_pos'][0], fy=r['from_pos'][1], tx=r['to_pos'][0], ty=r['to_pos'][1],
            rfx=r['reverse_from'][0], rfy=r['reverse_from'][1], rtx=r['reverse_to'][0], rty=r['reverse_to'][1]))

if __name__ == '__main__':
    main()
