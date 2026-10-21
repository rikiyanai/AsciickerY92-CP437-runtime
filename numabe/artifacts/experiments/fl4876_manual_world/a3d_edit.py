#!/usr/bin/env python3
"""a3d_edit.py -- edit single objects in a built map and save it (FL-4876 MAP-EDIT-01). No rebuild.

Operator 2026-10-16: "HOW DO U MAKE A SINGLE CHANGE? THAT IS WHAT ASCIIID ALLOWS", "EDIT AND SAVE A MAP?".
A full world build regenerates every object from its owners (~25 min). One wrong sign needs one record changed in
the map, which is what the asciiid editor does when it saves. This tool does that to output.a3d directly:

  list   MAP.a3d E N RADIUS_M            mesh instances whose origin lies within RADIUS_M of (E, N) metres
  apply  MAP.a3d EDITS.json [--dry-run]  apply hand edits and save the map in place
  replay MAP.a3d [--dry-run]             re-apply every journalled edit to a fresh build of the map (only where
                                         the build made the same instance as the edit's 'before'; else CONFLICT)

EDITS.json: [{"inst": <instance name>, "op": ..., "basis": <what you saw, with the picture path>, "by": <who>}, ...]
  op "delete"                              remove the instance
  op "move",  "dE_m", "dN_m", "dZ_m"       translate by metres (any of the three; default 0)
  op "to",    "E", "N" [, "Z_m"]           put the origin at E/N metres (Z_m: metres above its current z)
  op "yaw",   "deg"                        rotate about the vertical axis through the origin (+ = counter-clockwise
                                           seen from above, i.e. bearing decreases)
  op "face",  "bearing_deg"                set the facing (local +y, the outward normal) to a compass bearing
  op "mesh",  "mesh"                       draw a different mesh (file name in the build's meshes/)
  op "scale", "x", "y", "z"                multiply the local axes (along, depth, height) by these factors
  op "add",   "mesh", "E", "N", "z_world", "bearing_deg" [, "flags"]   new instance named "inst" (mesh file must
                                           be in the build's meshes/; write NEW mesh names, never overwrite one)
Applying takes an exclusive lock on <map>.lock, so several agents may edit one map.
Every applied edit is appended, with the map's SHA-256 before and after, to map_edits/journal.jsonl beside this
file (the durable owner of hand map edits). The previous map is kept as <map>.pre_edit_<sha8>; only the newest
PRE_EDIT_KEEP (8) copies are kept (keep_pre_edit, shared by the terrain tools).

A3D layout (scripts/a3d_mesh_refs.py): header, patches, materials, then i32 (negative = -format_version, then
i32 count) and the instance records; a mesh record is i32 len, mesh id, i32 len, instance name, 16 f64 transform
(column-major; translation in elements 12..14, world units; z in height units), i32 flags, i32 story (version >0).
Everything after the instance table is copied byte for byte. World units per metre and height units per metre come
from the build's terrain_metadata.json (content_scale; height = content_scale * 16).
"""
from __future__ import annotations

import hashlib
import json
import math
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

PATCH = 8 + (8 * 8 * 2) + (5 * 5 * 2) + 2
MATERIALS = 256 * 512
HERE = Path(__file__).resolve().parent
JOURNAL = HERE / "map_edits" / "journal.jsonl"
HEIGHT_SCALE = 16.0


def _i32(b, o):
    return struct.unpack_from("<i", b, o)[0], o + 4


def parse(data: bytes):
    """-> (prefix bytes up to the count fields, version, records, tail bytes). records: dicts with raw bytes."""
    if data[:4] != b"AS3D":
        raise SystemExit("not an A3D file")
    header, patches = struct.unpack_from("<II", data, 4)
    o = header + patches * PATCH + MATERIALS
    start = o
    first, o = _i32(data, o)
    if first < 0:
        version = -first
        count, o = _i32(data, o)
    else:
        version, count = 0, first
    recs = []
    for _ in range(count):
        r0 = o
        n, o = _i32(data, o)
        if n >= 0:
            mesh = data[o:o + n].decode("utf-8"); o += n
            k, o = _i32(data, o)
            name = data[o:o + k].decode("utf-8"); o += k
            tf = list(struct.unpack_from("<16d", data, o)); o += 128
            flags, o = _i32(data, o)
            story = None
            if version > 0:
                story, o = _i32(data, o)
            recs.append({"kind": "mesh", "mesh": mesh, "inst": name, "tf": tf, "flags": flags, "story": story})
        elif n == -1:
            k, o = _i32(data, o)
            o += k + 12 + 4 + 8 + 16 + 4 + (4 if version > 0 else 0)
            recs.append({"kind": "raw", "raw": data[r0:o]})
        elif n == -2:
            o += 16 + 12 + 4 + 4 + 4
            recs.append({"kind": "raw", "raw": data[r0:o]})
        else:
            raise SystemExit(f"unknown instance discriminant {n} at byte {r0}")
    return data[:start], version, recs, data[o:]


def serialise(prefix, version, recs, tail) -> bytes:
    out = bytearray(prefix)
    if version > 0:
        out += struct.pack("<ii", -version, len(recs))
    else:
        out += struct.pack("<i", len(recs))
    for r in recs:
        if r["kind"] == "raw":
            out += r["raw"]
            continue
        m, n = r["mesh"].encode("utf-8"), r["inst"].encode("utf-8")
        out += struct.pack("<i", len(m)) + m + struct.pack("<i", len(n)) + n
        out += struct.pack("<16d", *r["tf"]) + struct.pack("<i", r["flags"])
        if version > 0:
            out += struct.pack("<i", r["story"])
    return bytes(out + tail)


def meta_for(map_path: Path) -> dict:
    m = json.loads((map_path.parent / "terrain_metadata.json").read_text())
    return {"cs": float(m["content_scale"]), "E0": float(m["origin_easting"]), "N0": float(m["origin_northing"]),
            "ox": float(m["offset_x"]), "oy": float(m["offset_y"])}


def to_en(meta, x, y):
    return meta["E0"] + (x - meta["ox"]) / meta["cs"], meta["N0"] + (y - meta["oy"]) / meta["cs"]


def to_xy(meta, e, n):
    return (e - meta["E0"]) * meta["cs"] + meta["ox"], (n - meta["N0"]) * meta["cs"] + meta["oy"]


def facing_bearing(tf) -> float:
    """Compass bearing of local +y (column 1) in the map plane."""
    return math.degrees(math.atan2(tf[4], tf[5])) % 360.0


def rotate(tf, deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    for col in (0, 1, 2):
        x, y = tf[col * 4], tf[col * 4 + 1]
        tf[col * 4], tf[col * 4 + 1] = c * x - s * y, s * x + c * y


def apply_edit(r, e, meta, mesh_dir: Path):
    op, tf = e["op"], r["tf"]
    if op == "move":
        tf[12] += float(e.get("dE_m", 0)) * meta["cs"]
        tf[13] += float(e.get("dN_m", 0)) * meta["cs"]
        tf[14] += float(e.get("dZ_m", 0)) * meta["cs"] * HEIGHT_SCALE
    elif op == "to":
        tf[12], tf[13] = to_xy(meta, float(e["E"]), float(e["N"]))
        tf[14] += float(e.get("Z_m", 0)) * meta["cs"] * HEIGHT_SCALE
    elif op == "yaw":
        rotate(tf, float(e["deg"]))
    elif op == "face":
        # bearing b: +y should point (sin b, cos b); rotating counter-clockwise by d lowers the bearing by d
        rotate(tf, facing_bearing(tf) - float(e["bearing_deg"]))
    elif op == "scale":
        # multiply the local axes: "x" along the object, "y" depth, "z" height (factors, default 1)
        for col, key in ((0, "x"), (1, "y"), (2, "z")):
            f = float(e.get(key, 1.0))
            for k in range(3):
                tf[col * 4 + k] *= f
    elif op == "mesh":
        if not (mesh_dir / e["mesh"]).is_file():
            raise SystemExit(f"{e['inst']}: mesh {e['mesh']} not in {mesh_dir}")
        r["mesh"] = e["mesh"]
    else:
        raise SystemExit(f"unknown op {op}")


def replay(map_path: Path, recs) -> tuple[list, list]:
    """Re-apply journalled edits to a fresh build: an edit is applied only where the build produced the same
    instance as the edit's 'before' (mesh and transform within 1e-6); otherwise it is reported, never guessed."""
    if not JOURNAL.is_file():
        return recs, []
    by_name = {r["inst"]: r for r in recs if r["kind"] == "mesh"}
    report, drop = [], set()
    for line in JOURNAL.read_text(encoding="utf-8").splitlines():
        for e in json.loads(line)["edits"]:
            r = by_name.get(e["inst"])
            b = e["before"]
            if b is None:  # an added instance
                if r is None:
                    a = e["after"]
                    r = {"kind": "mesh", "mesh": a["mesh"], "inst": e["inst"], "tf": list(a["tf"]),
                         "flags": int(e.get("flags", 3)), "story": int(e.get("story", -1))}
                    recs.append(r)
                    by_name[e["inst"]] = r
                    report.append((e["inst"], "replayed add"))
                else:
                    report.append((e["inst"], "already present"))
                continue
            if r is None:
                report.append((e["inst"], "absent in this build"))
            elif r["mesh"] == b["mesh"] and all(abs(p - q) < 1e-6 for p, q in zip(r["tf"], b["tf"])):
                if e["after"] is None:
                    drop.add(e["inst"])
                else:
                    r["mesh"], r["tf"] = e["after"]["mesh"], list(e["after"]["tf"])
                report.append((e["inst"], "replayed " + e["op"]))
            elif e["after"] and r["mesh"] == e["after"]["mesh"] and all(
                    abs(p - q) < 1e-6 for p, q in zip(r["tf"], e["after"]["tf"])):
                report.append((e["inst"], "already as edited"))
            else:
                report.append((e["inst"], "CONFLICT: build changed this instance; edit not replayed"))
    return [r for r in recs if not (r["kind"] == "mesh" and r["inst"] in drop)], report


def save_atomic(path: Path, data: bytes) -> None:
    """Write data beside path, flush it to disk, then rename it over path: a crash leaves the old map or the new."""
    import os
    tmp = path.with_name(path.name + ".saving")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


PRE_EDIT_KEEP = 8  # operator 2026-10-18 (disk full): 1211 copies of 48 MB filled 56 GB; the journal records every edit


def keep_pre_edit(map_path: Path, data: bytes, sha: str, keep: int = PRE_EDIT_KEEP) -> Path:
    """Keep the map as it was before this edit (<map>.pre_edit_<sha8>) and delete all but the newest `keep` copies.
    Every map edit tool calls this; map_edits/journal.jsonl is the durable record, these copies are only a short undo."""
    copy = map_path.with_name(map_path.name + ".pre_edit_" + sha[:8])
    if not copy.exists():
        copy.write_bytes(data)
    old = sorted(map_path.parent.glob(map_path.name + ".pre_edit_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in old[keep:]:
        p.unlink(missing_ok=True)
    return copy


def main(argv):
    if len(argv) < 2 or argv[0] not in ("list", "apply", "replay"):
        raise SystemExit(__doc__)
    map_path = Path(argv[1]).resolve()
    data = map_path.read_bytes()
    prefix, version, recs, tail = parse(data)
    meta = meta_for(map_path)
    if argv[0] == "list":
        e0, n0, rad = (float(v) for v in argv[2:5])
        for r in recs:
            if r["kind"] != "mesh":
                continue
            e, n = to_en(meta, r["tf"][12], r["tf"][13])
            d = math.hypot(e - e0, n - n0)
            if d <= rad:
                print("%-58s %-58s E%.2f N%.2f d%.1f facing %.0f" % (r["inst"][:58], r["mesh"][:58], e, n, d,
                                                                      facing_bearing(r["tf"])))
        return 0
    if argv[0] == "replay":
        recs, report = replay(map_path, recs)
        for inst, what in report:
            print(inst, "-", what)
        new = serialise(prefix, version, recs, tail)
        if "--dry-run" not in argv and new != data:
            keep_pre_edit(map_path, data, hashlib.sha256(data).hexdigest())
            save_atomic(map_path, new)
            print("saved", map_path)
        return 0
    edits = json.loads(Path(argv[2]).read_text())
    dry = "--dry-run" in argv
    import fcntl
    lock = open(str(map_path) + ".lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)  # several agents edit one map: read-modify-write under one lock
    data = map_path.read_bytes()
    prefix, version, recs, tail = parse(data)
    by_name = {}
    for i, r in enumerate(recs):
        if r["kind"] == "mesh":
            by_name.setdefault(r["inst"], []).append(i)
    drop, done = set(), []
    for e in edits:
        if not e.get("basis") or not e.get("by"):
            raise SystemExit(f"{e.get('inst')}: every edit needs 'basis' and 'by'")
        hits = by_name.get(e["inst"]) or []
        if e["op"] == "add":
            if hits:
                raise SystemExit(f"{e['inst']}: an instance with that name already exists")
            if not (map_path.parent / "meshes" / e["mesh"]).is_file():
                raise SystemExit(f"{e['inst']}: mesh {e['mesh']} not in {map_path.parent / 'meshes'}")
            x, y = to_xy(meta, float(e["E"]), float(e["N"]))
            r = {"kind": "mesh", "mesh": e["mesh"], "inst": e["inst"],
                 "tf": [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, HEIGHT_SCALE, 0, x, y, float(e["z_world"]), 1.0],
                 "flags": int(e.get("flags", 3)), "story": int(e.get("story", -1))}
            rotate(r["tf"], -float(e.get("bearing_deg", 0.0)))
            recs.append(r)
            by_name[e["inst"]] = [len(recs) - 1]
            done.append({**e, "before": None, "after": {"mesh": r["mesh"], "tf": list(r["tf"])}})
            continue
        if len(hits) != 1:
            raise SystemExit(f"{e['inst']}: {len(hits)} instances with that name (need exactly 1)")
        r = recs[hits[0]]
        before = {"mesh": r["mesh"], "tf": list(r["tf"])}
        if e["op"] == "delete":
            drop.add(hits[0])
        else:
            apply_edit(r, e, meta, map_path.parent / "meshes")
        done.append({**e, "before": before, "after": None if e["op"] == "delete" else {"mesh": r["mesh"],
                                                                                        "tf": list(r["tf"])}})
    recs = [r for i, r in enumerate(recs) if i not in drop]
    new = serialise(prefix, version, recs, tail)
    # round-trip guard: the edited table must parse back to the same records
    if len(parse(new)[2]) != len(recs):
        raise SystemExit("round trip failed; map not written")
    old_sha, new_sha = hashlib.sha256(data).hexdigest(), hashlib.sha256(new).hexdigest()
    print(f"{len(done)} edits; instances {len(by_name)} -> {sum(1 for r in recs if r['kind'] == 'mesh')}; "
          f"sha {old_sha[:12]} -> {new_sha[:12]}{' (dry run, not written)' if dry else ''}")
    if dry:
        return 0
    keep = keep_pre_edit(map_path, data, old_sha)
    save_atomic(map_path, new)
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "map": str(map_path),
                             "sha256_before": old_sha, "sha256_after": new_sha, "kept": str(keep),
                             "edits": done}, ensure_ascii=False) + "\n")
    print("saved", map_path, "; journal", JOURNAL)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
