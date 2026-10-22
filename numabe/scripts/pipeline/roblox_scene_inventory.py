#!/usr/bin/env python3
"""Shallow scene inventory for Roblox-imported geometry (FL-4254).

Offline-safe. Accepts local imported geometry paths only. Does NOT
fetch, scrape, rip, or run Blender. Supports:

  glTF / GLB   — stdlib JSON-chunk parse (nodes, meshes, materials)
  OBJ          — stdlib text parse (objects, materials, faces)
  FBX          — listed only (extraction_status=unsupported_without_blender)
  BLEND        — listed only (extraction_status=unsupported_without_blender)

Emits roblox_objects.json per FL-4254 spec. Each entry includes:
  source_file, parser, name, role_guess, transform, bbox, centroid,
  height, triangle_count, material_names, extraction_status, limitations
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from pathlib import Path


ROLE_KEYWORDS = {
    "terrain": ["terrain", "ground", "land", "topology", "heightmap"],
    "building_meshes": ["building", "bldg", "tower", "hall", "library", "house", "structure"],
    "roads": ["road", "street", "path", "sidewalk", "highway"],
    "plazas": ["plaza", "courtyard", "square", "mall"],
    "landmarks": ["landmark", "monument", "torus", "umbilic", "statue", "fountain"],
    "materials": ["material", "shader"],
    "scene": ["scene", "root", "world"],
}


def _classify_role(name: str) -> str:
    if not name:
        return "unknown"
    lower = name.lower()
    for role, keywords in ROLE_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                return role
    return "unknown"


def _empty_entry(source_file: str, parser: str, name: str = "", status: str = "ok",
                 limitations: list | None = None) -> dict:
    return {
        "source_file": source_file,
        "parser": parser,
        "name": name,
        "role_guess": _classify_role(name),
        "transform": None,
        "bbox": None,
        "centroid": None,
        "height": None,
        "triangle_count": None,
        "material_names": [],
        "extraction_status": status,
        "limitations": list(limitations or []),
    }


def _bbox_from_points(points: list) -> tuple[list, list, float] | None:
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    zs = [p[2] for p in points]
    bbox = [[min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]]
    centroid = [(bbox[0][i] + bbox[1][i]) / 2.0 for i in range(3)]
    height = bbox[1][2] - bbox[0][2]
    return bbox, centroid, height


# ---------------------------------------------------------------------------
# OBJ parser
# ---------------------------------------------------------------------------

def parse_obj(path: Path, rel: str) -> list:
    entries: list = []
    current_name = ""
    current_material_names: list = []
    current_vertices: list = []
    current_triangle_count = 0
    all_vertices: list = []

    def _flush() -> None:
        if current_name or current_triangle_count or current_vertices:
            name = current_name or path.stem
            entry = _empty_entry(rel, "obj_stdlib", name)
            bbox_info = _bbox_from_points(current_vertices)
            if bbox_info:
                entry["bbox"], entry["centroid"], entry["height"] = bbox_info
            entry["triangle_count"] = current_triangle_count
            entry["material_names"] = list(current_material_names)
            entries.append(entry)

    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                tok, *rest = line.split()
                if tok == "v" and len(rest) >= 3:
                    try:
                        pt = (float(rest[0]), float(rest[1]), float(rest[2]))
                        all_vertices.append(pt)
                    except ValueError:
                        pass
                elif tok == "o" or tok == "g":
                    _flush()
                    current_name = " ".join(rest) if rest else ""
                    current_material_names = []
                    current_vertices = []
                    current_triangle_count = 0
                elif tok == "usemtl" and rest:
                    mat = rest[0]
                    if mat not in current_material_names:
                        current_material_names.append(mat)
                elif tok == "f" and rest:
                    n = len(rest)
                    if n >= 3:
                        current_triangle_count += n - 2
                    for face_tok in rest:
                        first = face_tok.split("/")[0]
                        try:
                            idx = int(first)
                            if idx < 0:
                                idx = len(all_vertices) + idx + 1
                            if 1 <= idx <= len(all_vertices):
                                current_vertices.append(all_vertices[idx - 1])
                        except ValueError:
                            pass
        _flush()
    except OSError as exc:
        return [_empty_entry(rel, "obj_stdlib", "", status="error",
                             limitations=[f"read_error: {exc}"])]

    if not entries:
        entry = _empty_entry(rel, "obj_stdlib", path.stem)
        bbox_info = _bbox_from_points(all_vertices)
        if bbox_info:
            entry["bbox"], entry["centroid"], entry["height"] = bbox_info
        entry["triangle_count"] = 0
        entries.append(entry)

    return entries


# ---------------------------------------------------------------------------
# glTF / GLB parser
# ---------------------------------------------------------------------------

def _read_glb_json_chunk(path: Path) -> dict | None:
    with path.open("rb") as fh:
        header = fh.read(12)
        if len(header) < 12:
            return None
        magic, _version, _length = struct.unpack("<III", header)
        if magic != 0x46546C67:
            return None
        chunk_header = fh.read(8)
        if len(chunk_header) < 8:
            return None
        chunk_len, chunk_type = struct.unpack("<II", chunk_header)
        if chunk_type != 0x4E4F534A:
            return None
        payload = fh.read(chunk_len)
        try:
            return json.loads(payload.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return None


def parse_gltf(path: Path, rel: str) -> list:
    suffix = path.suffix.lower()
    try:
        if suffix == ".glb":
            doc = _read_glb_json_chunk(path)
            if doc is None:
                return [_empty_entry(rel, "glb_stdlib", "", status="error",
                                     limitations=["glb_json_chunk_unreadable"])]
        else:
            doc = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        return [_empty_entry(rel, "gltf_stdlib", "", status="error",
                             limitations=[f"read_error: {exc}"])]

    if not isinstance(doc, dict):
        return [_empty_entry(rel, "gltf_stdlib", "", status="error",
                             limitations=["root_not_object"])]

    nodes = doc.get("nodes") or []
    meshes = doc.get("meshes") or []
    materials = doc.get("materials") or []
    accessors = doc.get("accessors") or []

    mat_names = [m.get("name", f"material_{i}") for i, m in enumerate(materials)]

    mesh_summaries = []
    for mi, mesh in enumerate(meshes):
        prims = mesh.get("primitives") or []
        tri_count = 0
        mat_ids: list = []
        mesh_bbox_min = [math.inf, math.inf, math.inf]
        mesh_bbox_max = [-math.inf, -math.inf, -math.inf]
        bbox_seen = False
        for prim in prims:
            mat_id = prim.get("material")
            if isinstance(mat_id, int) and mat_id not in mat_ids:
                mat_ids.append(mat_id)
            indices_id = prim.get("indices")
            position_id = (prim.get("attributes") or {}).get("POSITION")
            if isinstance(indices_id, int) and 0 <= indices_id < len(accessors):
                count = accessors[indices_id].get("count")
                if isinstance(count, int):
                    tri_count += count // 3
            if isinstance(position_id, int) and 0 <= position_id < len(accessors):
                acc = accessors[position_id]
                mn, mx = acc.get("min"), acc.get("max")
                if isinstance(mn, list) and isinstance(mx, list) and len(mn) >= 3 and len(mx) >= 3:
                    for i in range(3):
                        mesh_bbox_min[i] = min(mesh_bbox_min[i], float(mn[i]))
                        mesh_bbox_max[i] = max(mesh_bbox_max[i], float(mx[i]))
                    bbox_seen = True
        mesh_summaries.append({
            "mesh_index": mi,
            "name": mesh.get("name", f"mesh_{mi}"),
            "triangle_count": tri_count,
            "material_ids": mat_ids,
            "bbox": [list(mesh_bbox_min), list(mesh_bbox_max)] if bbox_seen else None,
        })

    entries: list = []
    used_meshes: set = set()
    for ni, node in enumerate(nodes):
        mesh_id = node.get("mesh")
        if not isinstance(mesh_id, int) or mesh_id >= len(mesh_summaries):
            continue
        used_meshes.add(mesh_id)
        summary = mesh_summaries[mesh_id]
        name = node.get("name") or summary["name"] or f"node_{ni}"
        entry = _empty_entry(rel, "gltf_stdlib", name)
        matrix = node.get("matrix")
        if isinstance(matrix, list) and len(matrix) == 16:
            entry["transform"] = list(matrix)
        elif "translation" in node or "rotation" in node or "scale" in node:
            entry["transform"] = {
                "translation": node.get("translation"),
                "rotation": node.get("rotation"),
                "scale": node.get("scale"),
            }
        if summary["bbox"]:
            entry["bbox"] = summary["bbox"]
            entry["centroid"] = [(summary["bbox"][0][i] + summary["bbox"][1][i]) / 2.0 for i in range(3)]
            entry["height"] = summary["bbox"][1][2] - summary["bbox"][0][2]
        entry["triangle_count"] = summary["triangle_count"]
        entry["material_names"] = [
            mat_names[mi] for mi in summary["material_ids"] if 0 <= mi < len(mat_names)
        ]
        entries.append(entry)

    for mi, summary in enumerate(mesh_summaries):
        if mi in used_meshes:
            continue
        entry = _empty_entry(rel, "gltf_stdlib", summary["name"])
        if summary["bbox"]:
            entry["bbox"] = summary["bbox"]
            entry["centroid"] = [(summary["bbox"][0][i] + summary["bbox"][1][i]) / 2.0 for i in range(3)]
            entry["height"] = summary["bbox"][1][2] - summary["bbox"][0][2]
        entry["triangle_count"] = summary["triangle_count"]
        entry["material_names"] = [
            mat_names[mid] for mid in summary["material_ids"] if 0 <= mid < len(mat_names)
        ]
        entry["limitations"].append("orphan_mesh_no_node_reference")
        entries.append(entry)

    if not entries:
        entry = _empty_entry(rel, "gltf_stdlib", path.stem, status="ok",
                             limitations=["no_nodes_or_meshes_found"])
        entries.append(entry)

    return entries


# ---------------------------------------------------------------------------
# Listing-only parsers
# ---------------------------------------------------------------------------

def parse_listed_only(path: Path, rel: str, kind: str) -> list:
    return [_empty_entry(
        rel,
        f"{kind}_listed",
        path.stem,
        status="unsupported_without_blender",
        limitations=[f"{kind}_requires_blender_or_dedicated_parser"],
    )]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

PARSER_BY_SUFFIX = {
    ".obj": ("obj", parse_obj),
    ".gltf": ("gltf", parse_gltf),
    ".glb": ("glb", parse_gltf),
    ".fbx": ("fbx", lambda p, r: parse_listed_only(p, r, "fbx")),
    ".blend": ("blend", lambda p, r: parse_listed_only(p, r, "blend")),
}


def inventory_paths(paths: list, base: Path) -> list:
    entries: list = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_absolute():
            path = (base / path).resolve()
        rel = str(path.relative_to(base)) if base in path.parents or base == path.parent else str(path)
        suffix = path.suffix.lower()
        if not path.is_file():
            entries.append(_empty_entry(rel, "missing", path.stem, status="error",
                                        limitations=["file_not_found"]))
            continue
        if suffix not in PARSER_BY_SUFFIX:
            entries.append(_empty_entry(rel, "unsupported_extension", path.stem,
                                        status="unsupported_extension",
                                        limitations=[f"unknown_extension: {suffix}"]))
            continue
        _kind, parser = PARSER_BY_SUFFIX[suffix]
        entries.extend(parser(path, rel))
    return entries


def inventory_directory(base: Path) -> list:
    paths: list = []
    for ext in PARSER_BY_SUFFIX:
        paths.extend(sorted(base.rglob(f"*{ext}")))
    return inventory_paths([str(p) for p in paths], base)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Shallow scene inventory for Roblox-imported geometry (FL-4254)")
    parser.add_argument("--raw-dir", default="assets/reference/roblox_sbu/raw",
                        help="Directory to scan for geometry files")
    parser.add_argument("--file", action="append", default=[],
                        help="Explicit geometry path (can repeat). Overrides --raw-dir scan when given.")
    parser.add_argument("--output", default="assets/reference/roblox_sbu/normalized/roblox_objects.json",
                        help="Output JSON path")
    parser.add_argument("--json", action="store_true", help="Print result to stdout instead of writing file")
    args = parser.parse_args(argv)

    raw_dir = Path(args.raw_dir).resolve()
    if args.file:
        base = Path.cwd()
        entries = inventory_paths(args.file, base)
    else:
        if not raw_dir.is_dir():
            print(f"[FAIL] raw dir not a directory: {raw_dir}", file=sys.stderr)
            return 2
        entries = inventory_directory(raw_dir)

    payload = {
        "schema_version": "1",
        "fl_ref": "FL-4254",
        "entry_count": len(entries),
        "entries": entries,
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"[OK] wrote {out_path} ({len(entries)} entries)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
