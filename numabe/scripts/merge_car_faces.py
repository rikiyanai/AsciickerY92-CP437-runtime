#!/usr/bin/env python3
"""FL-4831: centred body/wheel banks -> body-frame assembly, including edges.
World rotates body-frame wheel vertices with T(p) R T(-p); pivots alone do
not place a local mesh. --rebuild-tutorial-cars uses the recorded source scenes.
"""
import argparse
import copy
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PART_NAMES = ["body", "wheel_fl", "wheel_fr", "wheel_rl", "wheel_rr"]
RACE_PIVOTS = [[0.999822, -0.182031, -0.78125], [0.999822, -0.182031, 0.78125],
               [-1.375178, -0.182031, -0.78125], [-1.375178, -0.182031, 0.78125]]


def merge_faces(body, wheels, source, pivots=RACE_PIVOTS, wheel_scale=(1, 1, 1)):
    if len(wheels) != 4 or len(pivots) != 4:
        raise ValueError("four wheels and pivots required")
    if len(wheel_scale) != 3 or any(not math.isfinite(x) or x <= 0 for x in wheel_scale):
        raise ValueError("finite positive wheel scale required")
    rows, floats, uints, runs, parts = [], [], [], [], []
    face_start = run_start = 0
    totals = {k: 0 for k in ("count", "runs", "open_edges", "non_manifold_edges", "degenerate_edges")}
    for index, (name, original) in enumerate(zip(PART_NAMES, [body] + wheels)):
        doc = copy.deepcopy(original)
        faces, r = int(doc["face_count"]), doc["rows"]
        if len(r) != faces * 16 or not all(math.isfinite(v) for v in r):
            raise ValueError(f"invalid face rows: {name}")
        pivot = [0, 0, 0] if index == 0 else list(pivots[index - 1])
        if len(pivot) != 3 or not all(math.isfinite(v) for v in pivot):
            raise ValueError(f"invalid pivot: {name}")
        scale = (1, 1, 1) if index == 0 else wheel_scale
        ef, eu, er = (doc["edge_rows"][k] for k in ("floats", "uints", "run_uints"))
        count = int(doc["edge_table"]["count"])
        if (len(ef), len(eu), len(er)) != (count * 16, count * 4, count * 2):
            raise ValueError(f"invalid edge bank: {name}")
        for start in range(0, len(r), 16):
            for corner in (0, 4, 8, 12):
                for axis in range(3):
                    j = start + corner + axis
                    r[j] = r[j] * scale[axis] + pivot[axis]
        for start in range(0, len(ef), 16):
            for corner in (0, 4):
                for axis in range(3):
                    j = start + corner + axis
                    ef[j] = ef[j] * scale[axis] + pivot[axis]
            # Inverse transpose preserves normals under width/radius scaling.
            for corner in (8, 12):
                normal = [ef[start + corner + a] / scale[a] for a in range(3)]
                length = math.sqrt(sum(v * v for v in normal))
                for a in range(3):
                    ef[start + corner + a] = normal[a] / length if length else 0
        for start in range(0, len(eu), 4):
            eu[start] += face_start
            if eu[start + 1] != 0xFFFFFFFF:
                eu[start + 1] += face_start
        for start in range(0, len(er), 2):
            er[start] += run_start
        parts.append({"name": name, "face_start": face_start, "face_count": faces,
                      "row_start_floats": len(rows), "edge_start": len(uints) // 4,
                      "edge_count": count, "run_start": run_start,
                      "run_count": int(doc["edge_table"]["runs"]),
                      "offset_in_body_frame": pivot,
                      "dimensions_xyz": [v * s for v, s in zip(doc["dimensions_xyz"], scale)]})
        rows.extend(r)
        floats.extend(ef)
        uints.extend(eu)
        runs.extend(er)
        face_start += faces
        run_start += int(doc["edge_table"]["runs"])
        for key in totals:
            totals[key] += int(doc["edge_table"].get(key, 0))
    table = dict(body["edge_table"])
    table.update(totals)
    table["bytes_std430"] = totals["count"] * 80
    table["run_mean_length"] = totals["count"] / max(totals["runs"], 1)
    table["run_max_length"] = max(d["edge_table"]["run_max_length"] for d in [body] + wheels)
    bounds = [(min(rows[j + a] for j in range(0, len(rows), 16) for a in (axis, 4+axis, 8+axis, 12+axis)),
               max(rows[j + a] for j in range(0, len(rows), 16) for a in (axis, 4+axis, 8+axis, 12+axis)))
              for axis in range(3)]
    return {"version": 1, "source": source, "baker": "scripts/merge_car_faces.py",
            "mode": "airframe", "forward_axis": "+X", "up_axis": "+Y",
            "model_space": "body_frame", "wheel_spin_axis_baked": "+Z",
            "face_count": face_start, "mats": copy.deepcopy(body["mats"]), "rows": rows,
            "edge_rows": {"floats": floats, "uints": uints, "run_uints": runs},
            "edge_table": table, "prop_face_indices": [], "parts": parts,
            "overall_dimensions_xyz": [hi-lo for lo, hi in bounds],
            # Match race-v1's wheel-ground relation to the shared hover chassis.
            "ride_lift": 0.30 + (-0.65078125 - bounds[1][0]) * 0.9}


def rebuild_tutorial_cars():
    from bake_akm_to_static_world_faces import bake, parse_akm_ply
    source = json.loads((ROOT / "assets/meshes/incoming/tutorial_cars.source.json").read_text())
    work = ROOT / ".run/fl4831_car_rebuild"
    work.mkdir(parents=True, exist_ok=True)
    wheels = []
    for suffix in ("fl", "fr", "rl", "rr"):
        path = work / f"wheel_{suffix}.json"
        bake(str(ROOT / f"assets/meshes/incoming/race_car_wheel_{suffix}.akm"), str(path),
             0.9375, 0.82, "airframe", forward_axis="+z", up_axis="+z",
             airframe_body_mat=217, airframe_accent_mat=217)
        wheels.append(json.loads(path.read_text()))
    for name, model in source["models"].items():
        spec = model["assembly"]
        body_path = work / f"{name}_body.json"
        bake(str(ROOT / model["akm"]), str(body_path), 4.0, 0.82, "airframe",
             forward_axis=spec["forward_axis"], up_axis="+z",
             airframe_body_mat=spec["body_material"], airframe_accent_mat=219)
        body = json.loads(body_path.read_text())
        vertices, _ = parse_akm_ply(str(ROOT / model["akm"]))
        # AKM=(GLB.x,-GLB.z,GLB.y). Root translations matter for attachments,
        # even though centring a body bake cancels them out of its own vertices.
        centre = [(min(v["pos"][a] for v in vertices)+max(v["pos"][a] for v in vertices))/2
                  for a in range(3)]
        centre = [centre[0], centre[2], -centre[1]]
        centre = [a+b for a, b in zip(centre, spec["glb_root_translation"])]
        sign = 1 if spec["forward_axis"] == "-z" else -1
        centre = [centre[0]*sign, centre[1], centre[2]*sign]
        centre = [a+b for a, b in zip(centre, spec["scene_body_translation"])]
        scale = body["scale"]
        pivots = [[-(p[2]-centre[2])*scale,
                   (p[1]-spec["rest_distance"]-centre[1])*scale,
                   (p[0]-centre[0])*scale] for p in spec["wheel_hardpoints_fl_fr_rl_rr"]]
        radius, width = spec["wheel_radius_source"]*scale, spec["wheel_width_source"]*scale
        product = merge_faces(body, wheels, name, pivots,
                              (radius/0.46875, radius/0.46875, width/0.46875))
        product["mats"]["wheel"] = 217
        product["assembly"] = spec
        destination = ROOT / model["faces"]
        destination.write_text(json.dumps(product, separators=(",", ":")) + "\n")
        print(f"wrote {destination.relative_to(ROOT)} faces={product['face_count']} pivots={pivots}")


def main():
    import sys
    if sys.argv[1:] == ["--rebuild-tutorial-cars"]:
        rebuild_tutorial_cars()
        return
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("body")
    ap.add_argument("wheels", nargs=4)
    ap.add_argument("--source", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--pivots", nargs=12, type=float)
    args = ap.parse_args()
    pivots = [args.pivots[i:i+3] for i in range(0, 12, 3)] if args.pivots else RACE_PIVOTS
    product = merge_faces(json.loads(Path(args.body).read_text()),
                          [json.loads(Path(p).read_text()) for p in args.wheels], args.source, pivots)
    Path(args.output).write_text(json.dumps(product, separators=(",", ":")) + "\n")
    print(f"wrote {args.output} faces={product['face_count']}")


if __name__ == "__main__":
    main()
