#!/usr/bin/env python3
"""FL-4831: approved construction -> retained Godot car geometry and provenance.

This is a geometry/roster adapter, NOT the offline page fitter in the game.
Selected authored paths/negative shapes remain explicit inputs for the open
foreground realization work. A loaded mesh never establishes AA quality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNER = ROOT / "assets/meshes/incoming/tutorial_cars.source.json"
OUTPUT = ROOT / "godot_project/assets/world_mesh"
PARTS = ("body", "wheel_fl", "wheel_fr", "wheel_rl", "wheel_rr")
# Source +X is right, -Z is forward. Runtime +X is forward, +Z is right.
WHEEL_PREFIXES = ("wheel.-1.0.", "wheel.1.0.", "wheel.-1.1.", "wheel.1.1.")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def point(value):
    if len(value) != 3 or not all(math.isfinite(float(v)) for v in value):
        raise ValueError("finite XYZ required")
    x, y, z = map(float, value)
    return [-z, y, x]


def part_for_path(name: str) -> int:
    return next((i + 1 for i, prefix in enumerate(WHEEL_PREFIXES) if name.startswith(prefix)), 0)


def convert(target: dict, construction: dict) -> dict:
    if construction.get("revision") != 4:
        raise ValueError("only the approved revision04 construction is admitted")
    vertices = [point(p) for p in construction["occlusion_vertices"]]
    wheels = {entry["prefix"]: entry for entry in construction["wheel_motion"]["parts"]}
    if set(wheels) != set(WHEEL_PREFIXES):
        raise ValueError("four named wheel attachments required")
    ownership = [0] * len(vertices)
    for index, prefix in enumerate(WHEEL_PREFIXES, 1):
        lo, hi = wheels[prefix]["vertices"]
        if not 0 <= lo < hi <= len(vertices):
            raise ValueError("wheel vertex range outside construction")
        for ordinal in range(lo, hi):
            if ownership[ordinal]:
                raise ValueError("overlapping wheel parts")
            ownership[ordinal] = index
    grouped = [[] for _ in PARTS]
    for triangle in construction["occlusion_triangles"]:
        if len(triangle) != 3 or any(not isinstance(i, int) or not 0 <= i < len(vertices) for i in triangle):
            raise ValueError("invalid construction triangle")
        parts = {ownership[i] for i in triangle}
        if len(parts) != 1:
            raise ValueError("triangle crosses rigid attachments")
        part = parts.pop()
        corners = [vertices[i] for i in triangle]
        # Discard source zero-area cap triangles; no rendered geometry is lost.
        a, b, c = corners
        u = [b[i] - a[i] for i in range(3)]
        v = [c[i] - a[i] for i in range(3)]
        cross = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
        if sum(n*n for n in cross) < 1e-20:
            continue
        row = []
        for corner, w in zip([*corners, corners[2]], [217 if part else 218, 1, part, 0]):
            row.extend([*corner, w])
        grouped[part].extend(row)
    rows, parts = [], []
    for index, name in enumerate(PARTS):
        if not grouped[index]:
            raise ValueError("empty required body/wheel")
        entry = {"name": name, "face_start": len(rows)//16, "face_count": len(grouped[index])//16}
        if index:
            center = point(wheels[WHEEL_PREFIXES[index-1]]["center"])
            entry["offset_in_body_frame"] = center
            entry["radius"] = center[1]  # target wheel bottom is Y=0
        parts.append(entry)
        rows.extend(grouped[index])
    def feature(q):
        return {**q, "part": part_for_path(q["id"]), "points": [point(p) for p in q["points"]]}
    return {
        "version": 1, "source": target["construction"], "baker": "scripts/export_fl4831_approved_runtime_cars.py",
        "model_space": "body_frame", "forward_axis": "+X", "up_axis": "+Y", "wheel_spin_axis_baked": "+Z",
        "face_count": len(rows)//16, "rows": rows, "parts": parts,
        # Preserve the existing tested race chassis contact plane exactly.
        "ride_lift": 0.30 + (-0.65078125 - min(p[1] for p in vertices))*0.9,
        "wheel_radius_world": parts[1]["radius"]*0.9,
        "approved_target": {"id": target["id"], "gif": target["gif"], "gif_sha256": target["gif_sha256"],
                            "construction": target["construction"], "construction_sha256": sha(ROOT/target["construction"])},
        "authored_foreground": {
            "status": "PRESERVED_CONSTRUCTION; runtime native-glyph realization NOT_IMPLEMENTED",
            "paths": [feature(q) for q in construction["paths"]],
            "protected_openings": [feature(q) for q in construction["protected_openings"]],
            "bounded_tone_sites": [{**q, "part": part_for_path(q["id"]), "point": point(q["point"])}
                                   for q in construction["bounded_tone_sites"]],
            "fictional_plates": construction["fictional_plates"],
            "plate_coordinate_frame": "approved construction; source -Z forward; not yet a runtime text binding",
        },
    }


def build_feature_banks(paths: list[Path], check: bool) -> bool:
    """Use the existing compiler/ABI; append only these assets' rigid banks."""
    try:
        from scripts import true_ff_feature_bank as compiler, true_ff_rigid_features as rigid
    except ModuleNotFoundError:
        import true_ff_feature_bank as compiler
        import true_ff_rigid_features as rigid
    index_path = compiler.DEFAULT_OUT / "index.v1.json"
    original = index_path.read_bytes()
    index = json.loads(original)
    different = False
    count = 0
    for path in paths:
        doc = json.loads(path.read_text())
        rows, parts = rigid.part_indices(doc, 2, 1)
        for part, part_rows in rigid.split_parts(rows, parts).items():
            source = f"{path.relative_to(ROOT)}#rigid-part={part}"
            bank = compiler.build_source({"rows": part_rows, "rows32": part_rows,
                                          "face_count": len(part_rows)//16, "source": source})
            digest = bank["content_hash"]
            bank_path = compiler.DEFAULT_OUT / f"{digest}.v1.bin"
            sources = sorted(set(index["banks"].get(digest, {}).get("sources", [])) | {source})
            data = compiler.encode_file(bank, sources)
            entry = {"file": bank_path.name, "sources": sources, "node_count": bank["node_count"],
                     "feature_count": bank["feature_count"], "face_count": bank["face_count"]}
            if check:
                different |= not bank_path.is_file() or bank_path.read_bytes() != data
                different |= index["banks"].get(digest) != entry
            else:
                if not bank_path.is_file() or bank_path.read_bytes() != data:
                    bank_path.write_bytes(data)
                index["banks"][digest] = entry
            count += 1
    if not check:
        if index_path.read_bytes() != original:
            raise RuntimeError("bank inventory changed concurrently; rebuild against its new state")
        index_path.write_text(json.dumps(index, sort_keys=True, indent=1) + "\n")
    print("approved rigid part banks", count, "different", different)
    return different


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--feature-banks", action="store_true")
    args = parser.parse_args()
    targets = json.loads(OWNER.read_text())["authored_turntable_targets"]
    if not targets.get("status", "").startswith("OPERATOR_APPROVED_VISUAL_TARGETS_"):
        raise ValueError("runtime roster requires operator-approved target identities")
    failures, paths = [], []
    for target in targets["targets"]:
        if sha(ROOT/target["gif"]) != target["gif_sha256"]:
            raise ValueError("approved GIF identity changed")
        if sha(ROOT/target["construction"]) != target["construction_sha256"]:
            raise ValueError("construction differs from approved target binding")
        doc = convert(target, json.loads((ROOT/target["construction"]).read_text()))
        path = OUTPUT / f'approved_car_{target["id"]}_faces.json'
        paths.append(path)
        data = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != data:
                failures.append(path.name)
        else:
            path.write_text(data)
        print(target["id"], "faces", doc["face_count"], "bytes", len(data.encode()), "foreground=OPEN")
    if failures:
        print("DIFF", failures)
    if args.feature_banks and not failures and build_feature_banks(paths, args.check):
        failures.append("rigid feature banks")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
