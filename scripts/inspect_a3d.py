#!/usr/bin/env python3
"""
A3D / AKM Inspector — semantic query tool for Asciicker map and mesh files.

Answers human questions like "what color is the terrain?", "do all mesh files
exist?", "how many faces does this building have?" by importing the canonical
a3d_format.py deserializers (with minimal struct.unpack for section-prefix
integers not covered by a3d_format.py).

Usage:
    # Default summary — "is this map sane?"
    python3 scripts/inspect_a3d.py assets/a3d/osm_e2e_map_output.a3d

    # Terrain colors — "what color is the ground?"
    python3 scripts/inspect_a3d.py assets/a3d/osm_e2e_map_output.a3d --terrain-colors

    # Instance audit — "do all mesh files exist?"
    python3 scripts/inspect_a3d.py assets/a3d/osm_e2e_map_output.a3d --instances

    # Palette health — "is the palette broken?"
    python3 scripts/inspect_a3d.py assets/a3d/osm_e2e_map_output.a3d --material 4

    # AKM mesh audit — "are buildings too complex?"
    python3 scripts/inspect_a3d.py --akm assets/meshes/osm_e2e_map_output_meshes/

    # Single AKM — "how big is this building?"
    python3 scripts/inspect_a3d.py --akm assets/meshes/osm_e2e_map_output_meshes/Building_001.akm

    # JSON output for scripting
    python3 scripts/inspect_a3d.py assets/a3d/osm_e2e_map_output.a3d --json
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cli_style import style, status as _status  # noqa: E402

# Add project root to path
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent

# Import a3d_format directly (bypassing io_asciicker/__init__.py which needs bpy)
import importlib.util
_a3d_fmt_path = _PROJECT_ROOT / "addons" / "io_asciicker" / "scene" / "a3d_format.py"
if not _a3d_fmt_path.exists():
    print(f"Error: a3d_format.py not found at {_a3d_fmt_path}", file=sys.stderr)
    print("Ensure this script is run from the asciicker project tree.", file=sys.stderr)
    sys.exit(1)
_spec = importlib.util.spec_from_file_location("a3d_format", _a3d_fmt_path)
if _spec is None or _spec.loader is None:
    print(f"Error: failed to load a3d_format.py from {_a3d_fmt_path}", file=sys.stderr)
    sys.exit(1)
_a3d_fmt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_a3d_fmt)

A3DHeader = _a3d_fmt.A3DHeader
A3DPatch = _a3d_fmt.A3DPatch
A3DMaterial = _a3d_fmt.A3DMaterial
A3DInstance = _a3d_fmt.A3DInstance
A3DEnemyGen = _a3d_fmt.A3DEnemyGen
A3DMinimapMarker = _a3d_fmt.A3DMinimapMarker
MatCell = _a3d_fmt.MatCell
VISUAL_CELLS = _a3d_fmt.VISUAL_CELLS
HEIGHT_CELLS = _a3d_fmt.HEIGHT_CELLS


# ---------------------------------------------------------------------------
# Color naming — maps common RGB tuples to human-readable terrain names
# ---------------------------------------------------------------------------
_COLOR_NAMES = {
    (0, 153, 0): "green/grass",
    (51, 153, 0): "dark-green/grass",
    (0, 102, 0): "forest/deep-grass",
    (51, 102, 0): "olive/scrub",
    (102, 153, 0): "yellow-green/meadow",
    (128, 128, 128): "grey/road",
    (153, 153, 153): "light-grey/road",
    (102, 102, 102): "dark-grey/road",
    (51, 51, 51): "charcoal/asphalt",
    (0, 0, 153): "blue/water",
    (0, 0, 102): "dark-blue/water",
    (0, 51, 153): "ocean-blue/water",
    (153, 102, 51): "brown/dirt",
    (102, 51, 0): "dark-brown/earth",
    (204, 153, 102): "tan/sand",
    (0, 0, 0): "BLACK",
    (255, 255, 255): "white",
}


def _name_color(r, g, b):
    """Return a human-readable name for an RGB triple, or the raw values."""
    key = (r, g, b)
    if key in _COLOR_NAMES:
        return _COLOR_NAMES[key]
    # Heuristic buckets
    if r == 0 and g == 0 and b == 0:
        return "BLACK"
    if g > r and g > b:
        return "greenish"
    if r > g and r > b:
        return "reddish"
    if b > r and b > g:
        return "bluish"
    if r == g == b:
        return f"grey-{r}"
    return f"({r},{g},{b})"


# ---------------------------------------------------------------------------
# A3D file reader — uses canonical a3d_format.py deserializers
# ---------------------------------------------------------------------------

def read_a3d(filepath):
    """Read an entire A3D file and return all sections as a dict.

    Returns:
        {
            "header": A3DHeader,
            "patches": [A3DPatch, ...],
            "materials": [A3DMaterial x 256],
            "format_version": int,
            "instances": [A3DInstance, ...],
            "enemy_gens": [A3DEnemyGen, ...],
            "minimap_markers": [A3DMinimapMarker, ...],
        }
    """
    import struct

    result = {}
    try:
        with open(filepath, 'rb') as f:
            # Header
            header = A3DHeader.from_file(f)
            result["header"] = header

            # Patches
            patches = []
            for _ in range(header.num_patches):
                patches.append(A3DPatch.from_file(f))
            result["patches"] = patches

            # Materials (256 × 512 bytes = 131072 bytes)
            materials = []
            for _ in range(256):
                materials.append(A3DMaterial.read(f))
            result["materials"] = materials

            # Format version + instances (section-prefix integers not in a3d_format.py)
            data = f.read(4)
            if len(data) < 4:
                raise ValueError(f"Truncated A3D: expected format_version at offset {f.tell() - len(data)}")
            raw_fmt_ver = struct.unpack('<i', data)[0]
            fmt_ver = -raw_fmt_ver if raw_fmt_ver < 0 else raw_fmt_ver
            result["format_version"] = fmt_ver

            data = f.read(4)
            if len(data) < 4:
                raise ValueError(f"Truncated A3D: expected instance_count at offset {f.tell() - len(data)}")
            inst_count = struct.unpack('<i', data)[0]
            instances = []
            for _ in range(inst_count):
                instances.append(A3DInstance.from_file(f, fmt_ver))
            result["instances"] = instances

            # Enemy generators (optional section — may be absent at EOF)
            try:
                data = f.read(4)
                if len(data) < 4:
                    result["enemy_gens"] = []
                else:
                    eg_count = struct.unpack('<i', data)[0]
                    enemy_gens = []
                    for _ in range(eg_count):
                        enemy_gens.append(A3DEnemyGen.from_file(f))
                    result["enemy_gens"] = enemy_gens
            except (struct.error, ValueError):
                result["enemy_gens"] = []

            # Embedded minimap markers (optional section — older files end at EOF)
            try:
                data = f.read(4)
                if len(data) < 4:
                    result["minimap_markers"] = []
                else:
                    marker_count = struct.unpack('<i', data)[0]
                    minimap_markers = []
                    for _ in range(marker_count):
                        minimap_markers.append(A3DMinimapMarker.from_file(f))
                    result["minimap_markers"] = minimap_markers
            except (struct.error, ValueError):
                result["minimap_markers"] = []

    except (struct.error, ValueError, OSError) as e:
        raise ValueError(f"Failed to read A3D file {filepath}: {e}") from e

    return result


# ---------------------------------------------------------------------------
# Terrain analysis (R1–R4)
# ---------------------------------------------------------------------------

def analyze_terrain(patches, materials):
    """Analyze terrain patches for color, coverage, extent, and height."""
    result = {}

    # R2: Material coverage — count visual cells per material ID
    mat_counts = Counter()
    total_cells = 0
    for p in patches:
        for row in p.visual:
            for mat_id in row:
                mat_counts[mat_id] += 1
                total_cells += 1

    # R1 + R2: For each used material, get its color at elv=0, shade=8 (mid-shade, flat ground)
    coverage = {}
    for mat_id, count in mat_counts.most_common():
        pct = (count / total_cells * 100) if total_cells else 0
        mat = materials[mat_id] if mat_id < 256 else None
        fg = (0, 0, 0)
        bg = (0, 0, 0)
        glyph = 0
        if mat:
            cell = mat.shade[0][8]  # elv=0 (flat), shade=8 (mid)
            fg = cell.fg
            bg = cell.bg
            glyph = cell.gl
        coverage[mat_id] = {
            "count": count,
            "pct": round(pct, 1),
            "fg": fg,
            "bg": bg,
            "glyph": glyph,
            "fg_name": _name_color(*fg),
            "bg_name": _name_color(*bg),
        }
    result["coverage"] = coverage
    result["total_cells"] = total_cells

    # R3: Terrain extent
    if patches:
        xs = [p.x for p in patches]
        ys = [p.y for p in patches]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width = (max_x - min_x + 1) * VISUAL_CELLS
        height = (max_y - min_y + 1) * VISUAL_CELLS
        center_x = (min_x + max_x) / 2 * VISUAL_CELLS + VISUAL_CELLS / 2
        center_y = (min_y + max_y) / 2 * VISUAL_CELLS + VISUAL_CELLS / 2
        result["extent"] = {
            "patch_x": (min_x, max_x),
            "patch_y": (min_y, max_y),
            "width_cells": width,
            "height_cells": height,
            "center": (round(center_x, 1), round(center_y, 1)),
            "num_patches": len(patches),
        }
    else:
        result["extent"] = None

    # R4: Height stats
    all_heights = []
    empty_count = 0
    for p in patches:
        for row in p.height:
            for h in row:
                all_heights.append(h)
                if h == 0xA000:
                    empty_count += 1

    if all_heights:
        non_empty = [h for h in all_heights if h != 0xA000]
        result["height"] = {
            "min": min(all_heights),
            "max": max(all_heights),
            "total_vertices": len(all_heights),
            "empty_0xA000": empty_count,
            "empty_pct": round(empty_count / len(all_heights) * 100, 1),
        }
        if non_empty:
            result["height"]["non_empty_min"] = min(non_empty)
            result["height"]["non_empty_max"] = max(non_empty)
            result["height"]["non_empty_median"] = sorted(non_empty)[len(non_empty) // 2]
    else:
        result["height"] = None

    return result


# ---------------------------------------------------------------------------
# Instance analysis (R5–R8)
# ---------------------------------------------------------------------------

def _resolve_mesh_roots(a3d_file, project_root=None, mesh_root=None):
    """Resolve mesh roots for an A3D, preferring per-run folders before root assets/meshes."""
    roots = []
    if mesh_root is not None:
        roots.append(Path(mesh_root))

    a3d_path = Path(a3d_file)
    sibling_meshes = a3d_path.parent / "meshes"
    if sibling_meshes.exists():
        roots.append(sibling_meshes)

    if project_root is None:
        project_root = _PROJECT_ROOT
    project_root = Path(project_root)

    pointer = project_root / "assets" / "meshes" / "osm_runs" / ".active_mesh_root"
    if pointer.exists():
        raw = pointer.read_text(encoding="utf-8").strip()
        if raw:
            active_root = Path(raw)
            if not active_root.is_absolute():
                active_root = project_root / active_root
            active_root = active_root.resolve()
            if active_root.exists():
                roots.append(active_root)

    meshes_dir = project_root / "assets" / "meshes"
    if meshes_dir.exists():
        roots.append(meshes_dir)
    else:
        roots.append(project_root / "meshes")

    deduped = []
    seen = set()
    for root in roots:
        resolved = root.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return deduped


def analyze_instances(instances, project_root=None, a3d_file=None, mesh_root=None):
    """Analyze instance placement, mesh file existence, and clustering."""
    if project_root is None:
        project_root = _PROJECT_ROOT

    result = {}

    # R7: Count by variant
    by_variant = Counter()
    for inst in instances:
        by_variant[inst.variant] += 1
    result["by_variant"] = dict(by_variant)

    # R5 + R6 + R8: Group mesh instances by mesh_name
    by_mesh = defaultdict(lambda: {"count": 0, "positions": [], "inst_names": []})
    sprites = []
    items = []

    for inst in instances:
        if inst.variant == 'mesh':
            g = by_mesh[inst.mesh_name]
            g["count"] += 1
            # Translation is in transform[12], transform[13], transform[14]
            tx, ty, tz = inst.transform[12], inst.transform[13], inst.transform[14]
            g["positions"].append((round(tx, 1), round(ty, 1), round(tz, 1)))
            g["inst_names"].append(inst.inst_name)
        elif inst.variant == 'sprite':
            sprites.append({
                "name": inst.inst_name,
                "pos": [round(v, 1) for v in inst.pos],
                "anim": inst.anim,
                "frame": inst.frame,
            })
        elif inst.variant == 'item':
            items.append({
                "item_definition_id": inst.item_definition_id,
                "visual_style_id": inst.visual_style_id,
                "presentation_kind_id": inst.presentation_kind_id,
                "count": inst.item_count,
                "pos": [round(v, 1) for v in inst.pos],
            })

    # R5: Check mesh file existence
    missing = []
    found = []
    mesh_roots = _resolve_mesh_roots(a3d_file, project_root=project_root, mesh_root=mesh_root)
    result["mesh_roots"] = [str(root) for root in mesh_roots]
    for mesh_name, g in by_mesh.items():
        akm_path = None
        exists = False
        for root in mesh_roots:
            candidate = root / mesh_name
            if candidate.exists():
                akm_path = candidate
                exists = True
                break
        if akm_path is None:
            akm_path = mesh_roots[0] / mesh_name
        g["file_exists"] = exists
        g["resolved_path"] = str(akm_path)
        if not exists:
            missing.append(mesh_name)
        else:
            found.append(mesh_name)

    # R8: Compute spread per group
    for mesh_name, g in by_mesh.items():
        positions = g["positions"]
        if positions:
            xs = [p[0] for p in positions]
            ys = [p[1] for p in positions]
            zs = [p[2] for p in positions]
            g["bbox"] = {
                "x": (min(xs), max(xs)),
                "y": (min(ys), max(ys)),
                "z": (min(zs), max(zs)),
            }
            g["spread"] = {
                "x": round(max(xs) - min(xs), 1),
                "y": round(max(ys) - min(ys), 1),
            }
            # Flag: all same position?
            g["all_same_pos"] = (g["spread"]["x"] == 0 and g["spread"]["y"] == 0 and len(positions) > 1)
        # Don't include full position lists in summary (can be large)
        g["sample_positions"] = positions[:5]
        del g["positions"]
        del g["inst_names"]

    result["by_mesh"] = dict(by_mesh)
    result["missing_meshes"] = missing
    result["found_meshes"] = found
    result["sprites"] = sprites[:10]  # Limit output
    result["sprite_count"] = len(sprites)
    result["items"] = items[:10]
    result["item_count"] = len(items)

    return result


# ---------------------------------------------------------------------------
# Palette / material analysis (R9–R10)
# ---------------------------------------------------------------------------

def analyze_palette(materials, mat_ids=None):
    """Check palette health and report material colors.

    Args:
        materials: List of 256 A3DMaterial objects.
        mat_ids: If given, report detail for these specific material IDs.
    """
    result = {}

    # R9: Broken palette detection — check materials 1–10
    broken_count = 0
    palette_report = {}
    for i in range(1, 11):
        mat = materials[i]
        cell = mat.shade[0][8]  # elv=0, shade=8
        is_black = (cell.bg == (0, 0, 0) and cell.fg == (0, 0, 0))
        palette_report[i] = {
            "fg": cell.fg,
            "bg": cell.bg,
            "glyph": cell.gl,
            "fg_name": _name_color(*cell.fg),
            "bg_name": _name_color(*cell.bg),
            "is_black": is_black,
        }
        if is_black:
            broken_count += 1

    result["broken_count"] = broken_count
    result["is_broken"] = broken_count >= 8  # Most materials are black
    result["sample_materials"] = palette_report

    # R10: Detailed material view
    if mat_ids:
        detail = {}
        for mat_id in mat_ids:
            if 0 <= mat_id < 256:
                mat = materials[mat_id]
                ramps = []
                for ramp_idx in range(4):
                    shades = []
                    for shade_idx in range(16):
                        cell = mat.shade[ramp_idx][shade_idx]
                        shades.append({
                            "fg": cell.fg,
                            "bg": cell.bg,
                            "glyph": cell.gl,
                            "fg_name": _name_color(*cell.fg),
                            "bg_name": _name_color(*cell.bg),
                        })
                    ramps.append(shades)
                detail[mat_id] = {"ramps": ramps}
        result["detail"] = detail

    return result


# ---------------------------------------------------------------------------
# AKM (PLY) parser — pure Python, no Blender dependency (R11–R14)
# ---------------------------------------------------------------------------

def parse_ply(filepath):
    """Parse an ASCII PLY file and return vertex/face data.

    Returns:
        {
            "num_verts": int,
            "num_faces": int,
            "vertices": [(x, y, z, r, g, b, a), ...],  # position + color
            "face_vert_counts": [int, ...],  # verts per face (3 for triangulated)
            "properties": [str, ...],  # vertex property names
        }
    """
    result = {"num_verts": 0, "num_faces": 0, "vertices": [], "face_vert_counts": [],
              "properties": []}

    with open(filepath, 'r', errors='replace') as f:
        # Parse header
        line = f.readline().strip()
        if line != 'ply':
            raise ValueError(f"Not a PLY file: {filepath}")

        # Check format — only ASCII PLY is supported
        format_line = f.readline().strip()
        if 'ascii' not in format_line.lower():
            raise ValueError(f"Binary PLY not supported (got '{format_line}'): {filepath}")

        vert_props = []
        in_vertex_element = False
        in_face_element = False
        num_verts = 0
        num_faces = 0

        while True:  # noqa: format line already consumed above
            line = f.readline().strip()
            if line == 'end_header':
                break
            if line.startswith('element vertex'):
                num_verts = int(line.split()[-1])
                in_vertex_element = True
                in_face_element = False
            elif line.startswith('element face'):
                num_faces = int(line.split()[-1])
                in_vertex_element = False
                in_face_element = True
            elif line.startswith('element edge'):
                in_vertex_element = False
                in_face_element = False
            elif line.startswith('property') and in_vertex_element:
                parts = line.split()
                prop_name = parts[-1]
                vert_props.append(prop_name)

        result["num_verts"] = num_verts
        result["num_faces"] = num_faces
        result["properties"] = vert_props

        # Determine which columns are position and color
        # Standard PLY: x, y, z, nx, ny, nz, s, t, red, green, blue, alpha
        prop_idx = {name: i for i, name in enumerate(vert_props)}

        ix = prop_idx.get('x')
        iy = prop_idx.get('y')
        iz = prop_idx.get('z')
        ir = prop_idx.get('red')
        ig = prop_idx.get('green')
        ib = prop_idx.get('blue')
        ia = prop_idx.get('alpha')

        # Parse vertices
        vertices = []
        for _ in range(num_verts):
            line = f.readline()
            if not line:
                break
            vals = line.split()
            x = float(vals[ix]) if ix is not None else 0.0
            y = float(vals[iy]) if iy is not None else 0.0
            z = float(vals[iz]) if iz is not None else 0.0
            r = int(vals[ir]) if ir is not None else 0
            g = int(vals[ig]) if ig is not None else 0
            b = int(vals[ib]) if ib is not None else 0
            a = int(vals[ia]) if ia is not None else 255
            vertices.append((x, y, z, r, g, b, a))
        result["vertices"] = vertices

        # Parse faces
        face_vert_counts = []
        for _ in range(num_faces):
            line = f.readline()
            if not line:
                break
            vals = line.split()
            n = int(vals[0])
            face_vert_counts.append(n)
        result["face_vert_counts"] = face_vert_counts

    return result


def inspect_akm(filepath):
    """Inspect a single AKM file and return semantic summary.

    Returns dict with: verts, faces, bbox, dimensions, dominant_color, freestyle_faces.
    """
    ply = parse_ply(filepath)
    result = {
        "file": str(filepath),
        "num_verts": ply["num_verts"],
        "num_faces": ply["num_faces"],
    }

    verts = ply["vertices"]
    if verts:
        xs = [v[0] for v in verts]
        ys = [v[1] for v in verts]
        zs = [v[2] for v in verts]
        result["bbox"] = {
            "x": (round(min(xs), 2), round(max(xs), 2)),
            "y": (round(min(ys), 2), round(max(ys), 2)),
            "z": (round(min(zs), 2), round(max(zs), 2)),
        }
        result["dimensions"] = {
            "width": round(max(xs) - min(xs), 2),
            "depth": round(max(ys) - min(ys), 2),
            "height": round(max(zs) - min(zs), 2),
        }

        # Dominant color
        color_counts = Counter()
        for v in verts:
            color_counts[(v[3], v[4], v[5])] += 1
        top_color = color_counts.most_common(1)[0] if color_counts else ((0, 0, 0), 0)
        result["dominant_color"] = {
            "rgb": top_color[0],
            "count": top_color[1],
            "name": _name_color(*top_color[0]),
            "pct": round(top_color[1] / len(verts) * 100, 1) if verts else 0,
        }

        # Top 5 colors
        result["top_colors"] = [
            {"rgb": c, "count": n, "name": _name_color(*c)}
            for c, n in color_counts.most_common(5)
        ]

    # Freestyle faces (negative vertex count)
    negative_faces = sum(1 for n in ply["face_vert_counts"] if n < 0)
    result["freestyle_faces"] = negative_faces

    # Flag: too many faces for a clean-extruded building
    result["is_complex"] = ply["num_faces"] > 100

    return result


def inspect_akm_dir(dirpath):
    """Inspect all AKM files in a directory."""
    results = []
    p = Path(dirpath)
    for akm_file in sorted(p.glob("*.akm")):
        try:
            results.append(inspect_akm(akm_file))
        except Exception as e:
            results.append({"file": str(akm_file), "error": str(e)})
    return results


# ---------------------------------------------------------------------------
# Full A3D inspection — combines all analyses
# ---------------------------------------------------------------------------

def inspect_a3d(filepath, project_root=None, mat_ids=None, mesh_root=None):
    """Run full inspection on an A3D file. Returns combined result dict.

    Args:
        filepath: Path to .a3d file.
        project_root: Project root for mesh file existence checks.
        mat_ids: Optional list of material IDs for detailed ramp output.
    """
    data = read_a3d(filepath)

    terrain = analyze_terrain(data["patches"], data["materials"])
    instances = analyze_instances(
        data["instances"],
        project_root=project_root,
        a3d_file=filepath,
        mesh_root=mesh_root,
    )
    palette = analyze_palette(data["materials"], mat_ids=mat_ids)

    return {
        "file": str(filepath),
        "format_version": data["format_version"],
        "terrain": terrain,
        "instances": instances,
        "palette": palette,
        "enemy_gens": len(data["enemy_gens"]),
        "minimap_markers": data["minimap_markers"],
    }


# ---------------------------------------------------------------------------
# Human-readable formatters
# ---------------------------------------------------------------------------

def _fmt_rgb(rgb):
    return f"({rgb[0]:3d},{rgb[1]:3d},{rgb[2]:3d})"


def print_summary(result):
    """Print a concise human-readable summary of an A3D inspection."""
    print(style(f"=== A3D Inspection: {result['file']} ===", "header"))
    print(f"Format version: {result['format_version']}")
    print()

    # Palette health
    pal = result["palette"]
    if pal["is_broken"]:
        print(style("!! PALETTE IS BROKEN — most materials are BLACK. Needs transplant from game_map_y8.a3d !!", "fail"), file=sys.stderr)
    else:
        print(f"Palette: {style('OK', 'ok')} ({pal['broken_count']}/10 common materials are black)")
    print()

    # Terrain
    t = result["terrain"]
    ext = t.get("extent")
    if ext:
        print(f"Terrain: {ext['num_patches']} patches, {ext['width_cells']}×{ext['height_cells']} visual cells")
        print(f"  Center: ({ext['center'][0]}, {ext['center'][1]})")
        print(f"  Patch range: x=[{ext['patch_x'][0]},{ext['patch_x'][1]}] y=[{ext['patch_y'][0]},{ext['patch_y'][1]}]")

    h = t.get("height")
    if h:
        print(f"  Heights: min={h['min']}, max={h['max']}")
        if "non_empty_min" in h:
            print(f"  Non-empty heights: min={h['non_empty_min']}, max={h['non_empty_max']}, median={h['non_empty_median']}")
        if h["empty_0xA000"] > 0:
            print(style(f"  !! {h['empty_0xA000']} vertices ({h['empty_pct']}%) are 0xA000 (empty default) !!", "warn"), file=sys.stderr)
    print()

    # Terrain colors
    print("Material coverage (top 10):")
    print(f"  {'Mat':>4}  {'%':>6}  {'Count':>7}  {'FG':>14}  {'BG':>14}  Color")
    for mat_id, info in list(t["coverage"].items())[:10]:
        fg_str = _fmt_rgb(info["fg"])
        bg_str = _fmt_rgb(info["bg"])
        color = info["bg_name"] if info["bg"] != (0, 0, 0) else info["fg_name"]
        print(f"  {mat_id:>4}  {info['pct']:>5.1f}%  {info['count']:>7}  {fg_str}  {bg_str}  {color}")
    print()

    # Instances
    inst = result["instances"]
    print(f"Instances: {sum(inst['by_variant'].values())} total")
    for variant, count in inst["by_variant"].items():
        print(f"  {variant}: {count}")
    print(f"  Mesh roots: {', '.join(inst['mesh_roots'])}")

    if inst["missing_meshes"]:
        print(style(f"\n  !! MISSING MESH FILES ({len(inst['missing_meshes'])}):", "fail"), file=sys.stderr)
        for m in inst["missing_meshes"]:
            print(f"     {style(m, 'path')}", file=sys.stderr)

    # Instance groups
    by_mesh = inst["by_mesh"]
    if by_mesh:
        # Separate buildings from fixtures
        buildings = {k: v for k, v in by_mesh.items() if "Building" in k or "building" in k}
        fixtures = {k: v for k, v in by_mesh.items() if k not in buildings}

        if buildings:
            print(f"\n  Buildings: {len(buildings)} meshes, {sum(g['count'] for g in buildings.values())} instances")
            zs = []
            for g in buildings.values():
                for p in g["sample_positions"]:
                    zs.append(p[2])
            if zs:
                print(f"    Z range: {min(zs)} – {max(zs)}")

        if fixtures:
            print(f"\n  Fixtures:")
            for name, g in sorted(fixtures.items(), key=lambda x: -x[1]["count"]):
                exists_str = style("OK", "ok") if g["file_exists"] else style("MISSING", "fail")
                same_str = style(" (ALL SAME POS!)", "warn") if g.get("all_same_pos") else ""
                print(f"    {name}: {g['count']}× [{exists_str}]{same_str}")

    if inst["sprite_count"]:
        print(f"\n  Sprites: {inst['sprite_count']}")
    if inst["item_count"]:
        print(f"\n  Items: {inst['item_count']}")

    # Enemy gens
    if result["enemy_gens"]:
        print(f"\nEnemy generators: {result['enemy_gens']}")
    if result["minimap_markers"]:
        print(f"Minimap markers: {len(result['minimap_markers'])}")
        for marker in result["minimap_markers"][:10]:
            label = f" ({marker.label})" if marker.label else ""
            print(f"  {marker.name}{label} @ ({marker.x:.1f}, {marker.y:.1f}) glyph={marker.glyph}")
    print()


def print_terrain_colors(result):
    """Print detailed terrain color breakdown."""
    t = result["terrain"]
    print(style("=== Terrain Color Analysis ===", "header"))
    print()
    print(f"Total visual cells: {t['total_cells']}")
    print()
    print(f"{'Mat':>4}  {'%':>6}  {'Count':>7}  {'FG RGB':>14}  {'BG RGB':>14}  {'Glyph':>5}  FG Name          BG Name")
    print("-" * 95)
    for mat_id, info in t["coverage"].items():
        fg_str = _fmt_rgb(info["fg"])
        bg_str = _fmt_rgb(info["bg"])
        gl = chr(info["glyph"]) if 32 <= info["glyph"] < 127 else f"x{info['glyph']:02X}"
        print(f"  {mat_id:>4}  {info['pct']:>5.1f}%  {info['count']:>7}  {fg_str}  {bg_str}  {gl:>5}  {info['fg_name']:<17} {info['bg_name']}")


def print_instances(result):
    """Print detailed instance analysis."""
    inst = result["instances"]
    print(style("=== Instance Analysis ===", "header"))
    print()

    for variant, count in inst["by_variant"].items():
        print(f"{variant}: {count}")
    print()

    if inst["missing_meshes"]:
        print(style(f"!! MISSING MESH FILES ({len(inst['missing_meshes'])}):", "fail"), file=sys.stderr)
        for m in inst["missing_meshes"]:
            print(f"   {style(m, 'path')}", file=sys.stderr)
        print()

    print(f"{'Mesh Name':<40}  {'#':>5}  {'Exists':>6}  {'X Range':>20}  {'Y Range':>20}  {'Z Range':>20}  Flags")
    print("-" * 130)
    for name, g in sorted(inst["by_mesh"].items(), key=lambda x: -x[1]["count"]):
        exists = style("OK", "ok") if g["file_exists"] else style("MISS", "fail")
        flags = []
        if g.get("all_same_pos"):
            flags.append(style("SAME_POS", "warn"))
        bbox = g.get("bbox", {})
        xr = f"[{bbox['x'][0]:.0f}, {bbox['x'][1]:.0f}]" if bbox else "?"
        yr = f"[{bbox['y'][0]:.0f}, {bbox['y'][1]:.0f}]" if bbox else "?"
        zr = f"[{bbox['z'][0]:.0f}, {bbox['z'][1]:.0f}]" if bbox else "?"
        print(f"  {name:<40}  {g['count']:>5}  {exists:>6}  {xr:>20}  {yr:>20}  {zr:>20}  {' '.join(flags)}")


def print_material(result, mat_ids):
    """Print detailed material ramps."""
    pal = result["palette"]
    detail = pal.get("detail", {})
    for mat_id in mat_ids:
        if mat_id in detail:
            print(style(f"=== Material {mat_id} ===", "header"))
            print()
            ramps = detail[mat_id]["ramps"]
            ramp_names = ["flat/lo", "gentle", "moderate", "steep"]
            for ramp_idx, ramp in enumerate(ramps):
                print(f"  Ramp {ramp_idx} ({ramp_names[ramp_idx]}):")
                for shade_idx, cell in enumerate(ramp):
                    fg_str = _fmt_rgb(cell["fg"])
                    bg_str = _fmt_rgb(cell["bg"])
                    print(f"    shade {shade_idx:>2}: fg={fg_str} bg={bg_str}  {cell['fg_name']:<20} {cell['bg_name']}")
                print()


def print_akm_summary(results):
    """Print AKM inspection results as a table."""
    print(style("=== AKM Mesh Inspection ===", "header"))
    print()
    print(f"{'File':<45}  {'Verts':>6}  {'Faces':>6}  {'W':>7}  {'D':>7}  {'H':>7}  {'Color':>20}  Flags")
    print("-" * 120)
    for r in results:
        if "error" in r:
            fname = Path(r["file"]).name
            print(f"  {fname:<45}  {style('ERROR:', 'fail')} {r['error']}")
            continue
        fname = Path(r["file"]).name
        dims = r.get("dimensions", {})
        w = f"{dims.get('width', 0):.2f}"
        d = f"{dims.get('depth', 0):.2f}"
        h = f"{dims.get('height', 0):.2f}"
        dom = r.get("dominant_color", {})
        color = dom.get("name", "?")
        flags = []
        if r.get("is_complex"):
            flags.append(style("COMPLEX", "warn"))
        if r.get("freestyle_faces"):
            flags.append(style(f"wire:{r['freestyle_faces']}", "dim"))
        print(f"  {fname:<45}  {r['num_verts']:>6}  {r['num_faces']:>6}  {w:>7}  {d:>7}  {h:>7}  {color:>20}  {' '.join(flags)}")

    # Summary stats
    valid = [r for r in results if "error" not in r]
    if valid:
        total_faces = sum(r["num_faces"] for r in valid)
        complex_count = sum(1 for r in valid if r.get("is_complex"))
        print(f"\nTotal: {len(valid)} meshes, {total_faces} faces")
        if complex_count:
            print(style(f"!! {complex_count} meshes have >100 faces (likely raw blosm, not clean-extruded)", "warn"), file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Inspect A3D map files and AKM mesh files with semantic queries.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("a3d_file", nargs="?", help="Path to .a3d file")
    parser.add_argument("--terrain-colors", action="store_true",
                        help="Detailed terrain material color breakdown")
    parser.add_argument("--instances", action="store_true",
                        help="Detailed instance analysis with file existence checks")
    parser.add_argument("--material", type=int, nargs="+", metavar="ID",
                        help="Show detailed ramps for specific material IDs")
    parser.add_argument("--akm", nargs="?", const=".", metavar="PATH",
                        help="Inspect AKM file(s). If PATH is a directory, inspect all .akm files in it.")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--project-root", type=str, default=None,
                        help="Project root for mesh file existence checks (default: auto-detect)")
    parser.add_argument("--mesh-root", type=str, default=None,
                        help="Explicit mesh root for instance file checks (default: infer from sibling run folder or active OSM mesh root)")

    args = parser.parse_args()

    # Determine project root
    project_root = Path(args.project_root) if args.project_root else _PROJECT_ROOT

    # AKM mode
    if args.akm is not None:
        akm_path = Path(args.akm)
        if akm_path.is_dir():
            results = inspect_akm_dir(akm_path)
        elif akm_path.is_file():
            results = [inspect_akm(akm_path)]
        else:
            print(f"{style('Error:', 'fail')} {akm_path} not found", file=sys.stderr)
            sys.exit(1)

        if args.json:
            print(json.dumps(results, indent=2, default=str))
        else:
            print_akm_summary(results)
        return

    # A3D mode — requires a3d_file
    if not args.a3d_file:
        parser.error("a3d_file is required (unless using --akm)")

    filepath = Path(args.a3d_file)
    if not filepath.exists():
        print(f"{style('Error:', 'fail')} {filepath} not found", file=sys.stderr)
        sys.exit(1)

    # Read and analyze — single code path via inspect_a3d()
    try:
        result = inspect_a3d(filepath, project_root=project_root,
                             mat_ids=args.material, mesh_root=args.mesh_root)
    except ValueError as e:
        print(f"{style('Error:', 'fail')} {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        # Make JSON-safe (tuples → lists)
        print(json.dumps(result, indent=2, default=str))
        return

    # Human-readable output
    if args.terrain_colors:
        print_terrain_colors(result)
    elif args.instances:
        print_instances(result)
    elif args.material:
        print_material(result, args.material)
    else:
        print_summary(result)


if __name__ == "__main__":
    main()
