"""Rigid-part inputs for the one True FF feature-bank compiler.

This is an input adapter, not another compiler or command. It splits the
retained source mesh at its authored rigid-part boundaries. The caller uses
true_ff_feature_bank.build_source for the same hierarchy and file ABI as
static geometry. A propeller/wheel/weapon part changes its transform, not its
compiled topology, when it moves.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# RigidMoverGeometry.MAX_PARTS: the 52-part X Bot rig
# (assets/world_mesh/xbot_rig_rigid_mesh.json, FL-4907) is the widest mesh.
MAX_PARTS = 64


def split_parts(rows, parts):
    if len(rows) != len(parts) * 16:
        raise ValueError("rigid face/part count mismatch")
    out = {}
    for face, part in enumerate(parts):
        if not isinstance(part, int) or not 0 <= part < MAX_PARTS:
            raise ValueError("rigid part outside source ABI")
        row = list(rows[face * 16:(face + 1) * 16])
        # RigidMoverGeometry.register_mesh writes this exact source lane.
        row[11] = float(part)
        out.setdefault(part, []).extend(row)
    return out


def part_indices(doc, wing_part, prop_part):
    rows = doc.get("face_rows", doc.get("rows", []))
    count = int(doc.get("face_count", 0))
    if count <= 0 or len(rows) != count * 16:
        raise ValueError("rigid source has no complete face rows")
    if "part_indices" in doc:
        return rows, list(doc["part_indices"])
    parts = [0] * count
    if "parts" in doc:
        covered = set()
        for part, entry in enumerate(doc["parts"]):
            first, length = int(entry["face_start"]), int(entry["face_count"])
            if first < 0 or length <= 0 or first + length > count:
                raise ValueError("rigid part range outside mesh")
            for face in range(first, first + length):
                if face in covered:
                    raise ValueError("overlapping rigid part ranges")
                covered.add(face)
                parts[face] = part
        if len(covered) != count:
            raise ValueError("rigid part ranges leave faces unowned")
    else:
        # World publishes wings first, propellers second. Read part numbers
        # from that geometry owner instead of restating another ABI.
        for key, part in (("wing_face_indices", wing_part), ("prop_face_indices", prop_part)):
            for face in doc.get(key, []):
                if not isinstance(face, int) or not 0 <= face < count:
                    raise ValueError("rigid part face outside mesh")
                parts[face] = part
    return rows, parts


def rigid_sources(project: Path):
    world = (project / "scripts/World.gd").read_text(encoding="utf-8")
    def part_number(name):
        match = re.search(rf"^const {name}\s*:?=\s*(\d+)\s*$", world, re.M)
        if not match:
            raise ValueError(f"missing World part owner: {name}")
        return int(match.group(1))
    wing, prop = part_number("AIRPLANE_WING_PART"), part_number("AIRPLANE_PROP_PART")
    mesh = project / "assets/world_mesh"
    actor = project / "assets/actor_mesh"
    paths = sorted(set(mesh.glob("*airframe_faces.json"))
                   | set(mesh.glob("race_car_faces.json"))
                   | set(mesh.glob("approved_car_*_faces.json"))
                   | set(actor.glob("*weapon.json"))
                   | set(actor.glob("sword_slash.json")))
    for path in paths:
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows, parts = part_indices(doc, wing, prop)
        for part, part_rows in sorted(split_parts(rows, parts).items()):
            yield {"rows": part_rows, "rows32": part_rows,
                   "face_count": len(part_rows) // 16,
                   "source": f"{path.relative_to(project.parent)}#rigid-part={part}"}
    yield from ambient_rigid_sources(project)
    yield from fracture_shard_sources(project)
    yield from boat_rigid_sources(project)


def _part_sources(rows, parts, source):
    for part, part_rows in sorted(split_parts(rows, parts).items()):
        yield {"rows": part_rows, "rows32": part_rows, "face_count": len(part_rows) // 16,
               "source": f"{source}#rigid-part={part}"}


def ambient_rigid_sources(project: Path):
    """FL-4907 N16: `true-ff-rigid-mesh.v1` assets (birds, falling leaves,
    pumpkins). Runtime loads the same rows (RigidMeshAsset.load_asset) and
    registers them through ASCIICompositorEffect._ensure_fact_rigid_mesh."""
    for path in sorted((project / "assets/world_mesh").glob("*_rigid_mesh.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("schema") != "true-ff-rigid-mesh.v1":
            raise ValueError(f"{path.name}: not a true-ff-rigid-mesh.v1 asset")
        rows, parts = part_indices(doc, 0, 0)
        yield from _part_sources(rows, parts, path.relative_to(project.parent))


def fracture_shard_sources(project: Path):
    """FL-4907 N16: AirplaneFracture registers every shard of an
    `airplane-fracture.v1` asset as its own one-part rigid mesh (key
    `<res path>#shard=<i>`) from the shard rows unchanged; the bot material
    remap is a rigid instance override, so it never changes the digest."""
    for path in sorted((project / "assets/world_mesh").glob("*_fracture.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("schema") != "airplane-fracture.v1":
            continue
        for index, shard in enumerate(doc.get("shards", [])):
            rows = list(shard["rows"])
            if not rows or len(rows) % 16:
                raise ValueError(f"{path.name}: shard {index} has no complete face rows")
            yield from _part_sources(rows, [0] * (len(rows) // 16),
                                     f"{path.relative_to(project.parent)}#shard={index}")


_BOAT_VEC = r"Vector3\(([^()]*)\)"


def boat_rigid_sources(project: Path):
    """FL-4907 N16: World._ensure_boat_airframe_rows is the boat_v1 geometry
    owner (one part, registered in _append_boat_source_facts). Its rows are
    literal Vector3 constants and float32 sums, so they are evaluated from
    that function's text here with the same float32 rounding, never restated."""
    import struct

    def f32(value):
        return struct.unpack("<f", struct.pack("<f", float(value)))[0]

    world = (project / "scripts/World.gd").read_text(encoding="utf-8")
    start = world.index("func _ensure_boat_airframe_rows(")
    body = world[start:world.index("\nfunc ", start + 1)]
    consts = {name: float(value) for name, value in re.findall(
        r"^const (BOAT_[A-Z_]+)\s*:?=\s*(-?[0-9.]+)\s*$", world, re.M)}

    def scalar(token):
        token = token.strip()
        return consts[token] if token in consts else float(token)

    def literal(args):
        return tuple(f32(scalar(x)) for x in args.split(","))

    points = {}
    for name, args in re.findall(r"var (\w+) := " + _BOAT_VEC, body):
        points[name] = literal(args)

    def point(expr):
        expr = expr.strip()
        base, _, offset = expr.partition("+")
        match = re.fullmatch(_BOAT_VEC, base.strip())
        value = literal(match.group(1)) if match else points[base.strip()]
        if offset:
            delta = literal(re.fullmatch(_BOAT_VEC, offset.strip()).group(1))
            value = tuple(f32(a + b) for a, b in zip(value, delta))
        return value

    rows = []
    for args in re.findall(r"_append_boat_face\(rows, (.*)\)\s*$", body, re.M):
        parts = [p.strip() for p in re.split(r",(?![^()]*\))", args)]
        if len(parts) != 6:
            raise ValueError(f"boat face is not four corners, material and shade: {args}")
        corners = [point(p) for p in parts[:4]]
        lanes = (scalar(parts[4]), scalar(parts[5]), 0.0, 0.0)
        for corner, lane in zip(corners, lanes):
            rows.extend(list(corner) + [f32(lane)])
    if not rows:
        raise ValueError("World._ensure_boat_airframe_rows publishes no faces")
    yield from _part_sources(rows, [0] * (len(rows) // 16),
                             f"{(project / 'scripts/World.gd').relative_to(project.parent)}#boat_v1")
