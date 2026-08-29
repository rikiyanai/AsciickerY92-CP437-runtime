"""Minimap operations: terrain grid view and ANSI rendering."""

import math
import os
import re
import struct
import sys
from pathlib import Path
from cli_anything.asciiid.core import editor

# Material-to-ANSI color table (matches game.cpp minimap_mat_colors).
# Each entry: (ansi_bg_r, ansi_bg_g, ansi_bg_b, ansi_fg_r, ansi_fg_g, ansi_fg_b, glyph, shade_range)
# RGB values are in 0-5 range for xterm 256-color cube.
MINIMAP_MATERIALS = [
    # mat 0: Water   - dark blue
    {"name": "Water",       "bg": (0, 0, 1), "fg": (0, 0, 3), "gl": "~", "shade": 2},
    # mat 1: Grass   - dark green
    {"name": "Grass",       "bg": (0, 3, 0), "fg": (1, 3, 1), "gl": ".", "shade": 5},
    # mat 2: Dirt    - dark brown
    {"name": "Dirt",        "bg": (2, 1, 0), "fg": (3, 2, 1), "gl": ":", "shade": 3},
    # mat 3: Stone   - dark gray
    {"name": "Stone",       "bg": (2, 2, 2), "fg": (3, 3, 3), "gl": "#", "shade": 3},
    # mat 4: Sand    - tan
    {"name": "Sand",        "bg": (4, 4, 2), "fg": (5, 5, 3), "gl": ".", "shade": 3},
    # mat 5: Blood   - red
    {"name": "Blood",       "bg": (4, 0, 0), "fg": (5, 1, 1), "gl": ".", "shade": 1},
    # mat 6: Mud     - very dark brown
    {"name": "Mud",         "bg": (1, 1, 0), "fg": (2, 1, 1), "gl": "~", "shade": 2},
    # mat 7: Cobblestone - blue-gray
    {"name": "Cobblestone", "bg": (2, 2, 3), "fg": (3, 3, 4), "gl": "o", "shade": 2},
    # mat 8: Gravel  - mid gray
    {"name": "Gravel",      "bg": (3, 3, 3), "fg": (4, 4, 4), "gl": ";", "shade": 2},
]

DEFAULT_MAT = {"name": "Unknown", "bg": (2, 2, 2), "fg": (3, 3, 3), "gl": ":", "shade": 3}

# [ROOT-12 FIX] Game renderer uses water=55 (mainmenu.cpp:1393), not 0x8000.
# 0x8000 was the legacy terrain-encoding threshold — it made valid terrain
# (height=120) appear as water in the minimap.
WATER_LEVEL = 55


def _xterm256(r: int, g: int, b: int) -> int:
    """Convert 0-5 RGB to xterm 256-color index."""
    return 16 + 36 * r + 6 * g + b


def _ansi_cell(bg_rgb: tuple, fg_rgb: tuple, glyph: str) -> str:
    """Return ANSI escape sequence for one colored character."""
    bg = _xterm256(*bg_rgb)
    fg = _xterm256(*fg_rgb)
    return f"\033[48;5;{bg}m\033[38;5;{fg}m{glyph}\033[0m"


def _shade_bg(bg: tuple, shade: int) -> tuple:
    """Add shade offset to the blue component of bg, clamped to 0-5."""
    r, g, b = bg
    b = min(5, b + shade)
    return (r, g, b)


# True-color (24-bit) ANSI helpers — used when material palette is available
_WATER_BG_TC = (0, 25, 80)
_WATER_FG_TC = (40, 100, 200)
_INSTANCE_BG_TC = (0, 30, 30)
_INSTANCE_FG_TC = (0, 210, 210)


def _ansi_tc(fg: tuple, bg: tuple, glyph: str) -> str:
    """24-bit true-color ANSI cell. fg/bg are (r, g, b) in 0-255."""
    return (
        f"\033[48;2;{bg[0]};{bg[1]};{bg[2]}m"
        f"\033[38;2;{fg[0]};{fg[1]};{fg[2]}m"
        f"{glyph}\033[0m"
    )


def _sanitized_marker_name(name: str) -> str:
    name = re.sub(r"\.\d{3}$", "", str(name or "").strip())
    return re.sub(r"\s+", " ", re.sub(r"[_\-\s]+", " ", name)).strip()


def _is_generic_building_marker(marker: dict) -> bool:
    if marker.get("type") != "building":
        return False
    base = re.sub(r"\.\d{3}$", "", str(marker.get("name", "")).strip())
    return re.fullmatch(r"Building_\d+", base) is not None


def _should_render_marker(marker: dict) -> bool:
    return not _is_generic_building_marker(marker)


def _marker_display_label(marker: dict) -> str:
    if marker.get("type") == "building":
        return "" if _is_generic_building_marker(marker) else _sanitized_marker_name(marker.get("name", ""))
    label = marker.get("label")
    if label:
        return str(label)
    return _sanitized_marker_name(marker.get("name", ""))


def query_terrain_grid(cx: float, cy: float, width: int = 48,
                       height: int = 24, scale: float = 16.0) -> dict:
    """Query a grid of terrain material IDs and heights from asciiid.

    Args:
        cx, cy: World-space center of the query.
        width: Grid columns.
        height: Grid rows.
        scale: World units per grid cell.

    Returns:
        Dict with grid data from the MCP backend.
    """
    proc = editor.get_process()
    return proc.send_terrain_grid(cx, cy, width, height, scale)


def query_mesh_footprints(cx: float, cy: float, width: int = 48,
                          height: int = 24, scale: float = 16.0,
                          min_size: float = 16.0) -> dict:
    """Query mesh footprint rectangles from asciiid.

    Args:
        cx, cy: World-space center of the query.
        width: Grid columns.
        height: Grid rows.
        scale: World units per grid cell.
        min_size: Minimum bbox span to include.

    Returns:
        Dict with footprint data from the MCP backend.
    """
    proc = editor.get_process()
    return proc.send_mesh_footprints(cx, cy, width, height, scale, min_size)


MESH_FG = (0, 3, 3)  # cyan — matches game.cpp xterm index 37

# Footprint colors (true-color)
_FP_OUTLINE_BG = (20, 20, 20)
_FP_OUTLINE_FG = (220, 220, 220)
_FP_FILL_BG = (72, 45, 15)   # dark brown interior
_FP_FILL_FG = (110, 70, 25)  # slightly lighter brown glyph


def overlay_mesh_outlines(char_grid: list, footprints: list,
                          cx: float, cy: float, gw: int, gh: int,
                          scale: float) -> None:
    """Overlay mesh footprint outlines (live-editor bbox path, kept for compatibility)."""
    for fp in footprints:
        gx0 = int((fp["x_min"] - cx) / scale) + gw // 2
        gx1 = int((fp["x_max"] - cx) / scale) + gw // 2
        gy0 = int((fp["y_min"] - cy) / scale) + gh // 2
        gy1 = int((fp["y_max"] - cy) / scale) + gh // 2
        gx0c = max(0, min(gx0, gw - 1))
        gx1c = max(0, min(gx1, gw - 1))
        gy0c = max(0, min(gy0, gh - 1))
        gy1c = max(0, min(gy1, gh - 1))
        if gx0c > gx1c or gy0c > gy1c:
            continue
        for sy in range(gy0c, gy1c + 1):
            for sx in range(gx0c, gx1c + 1):
                edge_y = (sy == gy0 or sy == gy1)
                edge_x = (sx == gx0 or sx == gx1)
                if edge_x and edge_y:
                    gl = '+'
                elif edge_y:
                    gl = '-'
                elif edge_x:
                    gl = '|'
                else:
                    continue
                char_grid[sy][sx] = {'_raw': _ansi_tc(_FP_OUTLINE_FG, _FP_OUTLINE_BG, gl)}


def _pt_in_tri(px: float, py: float,
               ax: float, ay: float,
               bx: float, by: float,
               ex: float, ey: float) -> bool:
    """2D point-in-triangle test (barycentric sign method)."""
    d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by)
    d2 = (px - ex) * (by - ey) - (bx - ex) * (py - ey)
    d3 = (px - ax) * (ey - ay) - (ex - ax) * (py - ay)
    has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (has_neg and has_pos)


def _read_ply_local(akm_path: str) -> tuple | None:
    """Read ASCII PLY AKM, return (local_xy_verts, tri_faces) in mesh-local space.

    local_xy_verts: list of (lx, ly) floats (un-transformed)
    tri_faces: list of (i0, i1, i2) index triples (fan-triangulated)
    Returns None on any parse error.
    """
    try:
        with open(akm_path, 'rb') as f:
            n_verts = n_faces = 0
            v_props: list[str] = []
            in_v = in_f = False
            while True:
                line = f.readline().decode('ascii', errors='replace').strip()
                if line == 'end_header':
                    break
                if line.startswith('element vertex'):
                    n_verts = int(line.split()[-1]); in_v = True; in_f = False
                elif line.startswith('element face'):
                    n_faces = int(line.split()[-1]); in_f = True; in_v = False
                elif line.startswith('property') and in_v:
                    v_props.append(line.split()[-1])

            if n_verts == 0 or 'x' not in v_props or 'y' not in v_props:
                return None
            xi = v_props.index('x')
            yi = v_props.index('y')

            local_xy: list[tuple[float, float]] = []
            for _ in range(n_verts):
                parts = f.readline().decode('ascii', errors='replace').split()
                local_xy.append((float(parts[xi]), float(parts[yi])))

            tri_faces: list[tuple[int, int, int]] = []
            for _ in range(n_faces):
                parts = f.readline().decode('ascii', errors='replace').split()
                n = int(parts[0])
                idx = [int(parts[k + 1]) for k in range(n)]
                for k in range(1, n - 1):
                    tri_faces.append((idx[0], idx[k], idx[k + 1]))

        return local_xy, tri_faces
    except Exception:
        return None


def _apply_transform_xy(local_xy: list, transform: list) -> list:
    """Apply 4x4 column-major transform to list of (lx, ly) → list of (wx, wy)."""
    t = transform
    m00, m10 = t[0], t[1]
    m01, m11 = t[4], t[5]
    wtx, wty = t[12], t[13]
    return [(m00 * lx + m01 * ly + wtx, m10 * lx + m11 * ly + wty)
            for (lx, ly) in local_xy]


def _rasterize_mesh(xy_verts: list, tri_faces: list,
                    cx: float, cy: float, gw: int, gh: int,
                    scale: float) -> set:
    """Rasterize mesh triangles onto the minimap grid.

    Returns set of (gx, gy) grid cells covered by at least one triangle.
    Cell center point sampling — accurate enough for building footprints.
    """
    occupied: set[tuple[int, int]] = set()
    hw, hh = gw // 2, gh // 2
    half = scale * 0.5
    for (i0, i1, i2) in tri_faces:
        ax, ay = xy_verts[i0]
        bx, by = xy_verts[i1]
        vx, vy = xy_verts[i2]
        # grid-space bounding box of triangle
        lo_gx = max(0, int((min(ax, bx, vx) - cx) / scale + hw))
        hi_gx = min(gw - 1, int((max(ax, bx, vx) - cx) / scale + hw) + 1)
        lo_gy = max(0, int((min(ay, by, vy) - cy) / scale + hh))
        hi_gy = min(gh - 1, int((max(ay, by, vy) - cy) / scale + hh) + 1)
        for gy in range(lo_gy, hi_gy + 1):
            for gx in range(lo_gx, hi_gx + 1):
                # cell center in world space
                px = cx + (gx - hw) * scale + half
                py = cy + (gy - hh) * scale + half
                if _pt_in_tri(px, py, ax, ay, bx, by, vx, vy):
                    occupied.add((gx, gy))
    return occupied


def overlay_raster_footprint(char_grid: list, occupied: set) -> None:
    """Draw footprint outline from rasterized cell set.

    Border chars: + corners, - top/bottom edges, | left/right edges.
    Interior cells are skipped — terrain shows through.
    """
    for (gx, gy) in occupied:
        top = (gx, gy - 1) in occupied
        bot = (gx, gy + 1) in occupied
        lft = (gx - 1, gy) in occupied
        rgt = (gx + 1, gy) in occupied
        v_border = not top or not bot
        h_border = not lft or not rgt
        if not v_border and not h_border:
            char_grid[gy][gx] = {'_raw': _ansi_tc(_FP_FILL_FG, _FP_FILL_BG, ' ')}
            continue
        gl = '+' if (v_border and h_border) else ('-' if v_border else '|')
        char_grid[gy][gx] = {'_raw': _ansi_tc(_FP_OUTLINE_FG, _FP_OUTLINE_BG, gl)}


def render_ansi(grid_data: dict, markers: list | None = None,
                player_pos: tuple | None = None,
                footprints: list | None = None,
                mat_palette: dict | None = None,
                footprint_cells: set | None = None) -> str:
    """Render terrain grid data as ANSI-colored text for terminal display.

    Args:
        grid_data: Result from query_terrain_grid() or render_minimap_from_a3d().
        markers: Optional list of marker dicts {name, x, y, fg, glyph}.
        player_pos: Optional (x, y) player position to show as @.
        footprints: Optional list of mesh footprint dicts with x_min/x_max/y_min/y_max.
        mat_palette: Optional dict {mat_id: {fg, bg, gl}} with 0-255 RGB tuples.
                     When present, uses 24-bit true-color ANSI for terrain cells.

    Returns:
        Multi-line string with ANSI escape sequences.
    """
    grid = grid_data.get("grid", [])
    gw = grid_data.get("width", 0)
    gh = grid_data.get("height", 0)
    cx = grid_data.get("cx", 0.0)
    cy = grid_data.get("cy", 0.0)
    scale = grid_data.get("scale", 16.0)

    if not grid:
        return "(no terrain data)"

    # A3D-direct renders ship an embedded material palette. In that mode the
    # palette is the ground truth for material 0, so only elevation below the
    # canonical water line should force the water appearance.
    palette_backed_materials = mat_palette is not None

    # Build character grid
    char_grid = []
    for gy, row in enumerate(grid):
        char_row = []
        for gx, (mat_id, height) in enumerate(row):
            is_water = height < WATER_LEVEL or (not palette_backed_materials and mat_id == 0)
            if is_water:
                # Water — palette-backed A3D renders trust the embedded material
                # table, while the legacy no-palette/editor path keeps the older
                # hardcoded mat_id==0 fallback.
                if mat_palette is not None:
                    char_row.append({'_raw': _ansi_tc(_WATER_FG_TC, _WATER_BG_TC, '~')})
                else:
                    mat = MINIMAP_MATERIALS[0]
                    shade = min((WATER_LEVEL - height) // 2048, mat["shade"])
                    bg = _shade_bg(mat["bg"], shade)
                    char_row.append({"bg": bg, "fg": mat["fg"], "gl": mat["gl"]})
            elif mat_palette is not None:
                entry = mat_palette.get(mat_id, {'fg': (128, 128, 128), 'bg': (55, 55, 55), 'gl': ':'})
                # Subtle elevation brightening (3 tiers above water level)
                elev = min((height - WATER_LEVEL) >> 13, 3)
                bg = tuple(min(255, c + elev * 10) for c in entry['bg'])
                char_row.append({'_raw': _ansi_tc(entry['fg'], bg, entry['gl'])})
            else:
                # Legacy xterm-256 path (no palette)
                mat = (
                    {"name": "Snow", "bg": (5, 5, 5), "fg": (5, 5, 5), "gl": "*", "shade": 0}
                    if mat_id == 250
                    else MINIMAP_MATERIALS[mat_id] if 0 <= mat_id < len(MINIMAP_MATERIALS) else DEFAULT_MAT
                )
                shade = min((height - WATER_LEVEL) // 2048, mat["shade"])
                bg = _shade_bg(mat["bg"], shade)
                char_row.append({"bg": bg, "fg": mat["fg"], "gl": mat["gl"]})
        char_grid.append(char_row)

    # Overlay mesh footprint boxes (live-editor bbox path)
    if footprints:
        overlay_mesh_outlines(char_grid, footprints, cx, cy, gw, gh, scale)

    # Overlay rasterized mesh footprint (A3D-direct path)
    if footprint_cells:
        overlay_raster_footprint(char_grid, footprint_cells)

    # Overlay markers (yellow, on top of footprints)
    if markers:
        for m in markers:
            if not _should_render_marker(m):
                continue
            mx = int((m["x"] - cx) / scale) + gw // 2
            my = int((m["y"] - cy) / scale) + gh // 2
            if 0 <= mx < gw and 0 <= my < gh:
                marker_fg = m.get("fg", (5, 5, 1))
                glyph = m.get("glyph", "X")
                char_grid[my][mx] = {"bg": (0, 0, 0), "fg": marker_fg, "gl": glyph}
                name = _marker_display_label(m)
                draw_left = mx > gw // 2
                label_start_x = mx + 1
                if draw_left:
                    label_start_x = mx - len(name)
                for i, ch in enumerate(name):
                    lx = label_start_x + i
                    if 0 <= lx < gw:
                        char_grid[my][lx] = {"bg": (0, 0, 0), "fg": marker_fg, "gl": ch}

    # Overlay player position
    if player_pos:
        px = int((player_pos[0] - cx) / scale) + gw // 2
        py = int((player_pos[1] - cy) / scale) + gh // 2
        if 0 <= px < gw and 0 <= py < gh:
            char_grid[py][px] = {"bg": (0, 0, 0), "fg": (5, 5, 5), "gl": "@"}

    # Render to ANSI string
    lines = []
    lines.append(f"  Minimap [{gw}x{gh}] center=({cx:.0f},{cy:.0f}) scale={scale:.0f}  [terrain-only static view]")
    lines.append("  +" + "-" * gw + "+")
    for row in char_grid:
        cells = ""
        for c in row:
            if '_raw' in c:
                cells += c['_raw']
            else:
                cells += _ansi_cell(c["bg"], c["fg"], c["gl"])
        lines.append("  |" + cells + "|")
    lines.append("  +" + "-" * gw + "+")

    # Legend
    if mat_palette is not None:
        water_sample = _ansi_tc(_WATER_FG_TC, _WATER_BG_TC, '~')
        fp_sample = _ansi_tc(_FP_OUTLINE_FG, _FP_OUTLINE_BG, '+')
        lines.append(
            f"  {water_sample} Water  "
            f"{fp_sample} Mesh footprint  "
            f"(terrain colors from A3D palette)"
        )
    else:
        legend_items = []
        for mat in MINIMAP_MATERIALS:
            sample = _ansi_cell(mat["bg"], mat["fg"], mat["gl"])
            legend_items.append(f"{sample} {mat['name']}")
        lines.append("  " + "  ".join(legend_items))

    return "\n".join(lines)


# --- Marker loading ---

def _fallback_project_root() -> str:
    """Walk up from this file to find project root (contains io_asciicker/ or .git)."""
    p = Path(__file__).resolve()
    for _ in range(10):
        p = p.parent
        if (p / 'io_asciicker').is_dir() or (p / '.git').is_dir():
            return str(p)
    return str(Path(__file__).parent.parent.parent.parent)


def _ansi256_to_rgb6(index: int) -> tuple[int, int, int]:
    """Convert an xterm-256 cube entry to 0-5 RGB, falling back to yellow."""
    if 16 <= index <= 231:
        idx = index - 16
        return (idx // 36, (idx % 36) // 6, idx % 6)
    return (5, 5, 1)


def load_markers(markers_path: str | None = None) -> list[dict]:
    """Reject legacy JSON sidecar marker loading."""
    raise NotImplementedError(
        "Minimap labels are embedded in the selected .a3d map file. "
        "JSON sidecar marker loading is disabled to avoid map-independent labels."
    )


def save_markers(markers: list[dict]) -> None:
    """Reject legacy JSON sidecar marker writes."""
    raise NotImplementedError(
        "Minimap labels are embedded in the selected .a3d map file. "
        "JSON sidecar marker writes are disabled to avoid a second label owner."
    )


def add_marker(name: str, x: float, y: float,
               marker_type: str = "building") -> dict:
    raise NotImplementedError(
        "Minimap markers are embedded in the .a3d map file. "
        "Sidecar mutation is disabled until map-file marker editing exists."
    )


def remove_marker(name: str) -> bool:
    raise NotImplementedError(
        "Minimap markers are embedded in the .a3d map file. "
        "Sidecar mutation is disabled until map-file marker editing exists."
    )


def list_markers(map_path: str | None = None, markers_path: str | None = None) -> list[dict]:
    """List markers embedded in the selected map file."""
    if markers_path:
        raise NotImplementedError(
            "Minimap labels must come from the selected .a3d marker section; "
            "JSON sidecar marker overrides are disabled."
        )
    if not map_path:
        return []
    return load_embedded_markers(map_path)


# --- A3D-direct rendering (no asciiid process needed) ---

def _load_a3d_format():
    """Load a3d_format module via importlib without modifying sys.path."""
    if 'a3d_format' in sys.modules:
        return sys.modules['a3d_format']
    import importlib.util
    root = _fallback_project_root()
    mod_path = os.path.join(root, 'addons', 'io_asciicker', 'scene', 'a3d_format.py')
    spec = importlib.util.spec_from_file_location('a3d_format', mod_path)
    if spec is None:
        raise ImportError(f"Cannot find a3d_format.py at {mod_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules['a3d_format'] = mod
    spec.loader.exec_module(mod)
    return mod


def _find_akm(mesh_name: str, project_root: str) -> str | None:
    """Search for an AKM file by name in known directories."""
    for subdir in ('meshes', 'a3d', ''):
        candidate = os.path.join(project_root, subdir, mesh_name) if subdir else os.path.join(project_root, mesh_name)
        if os.path.exists(candidate):
            return candidate
    return None


def load_embedded_markers(map_path: str) -> list[dict]:
    """Load embedded minimap markers from an .a3d file."""
    fmt = _load_a3d_format()
    markers = []
    with open(map_path, 'rb') as f:
        hdr = fmt.A3DHeader.from_file(f)
        for _ in range(hdr.num_patches):
            fmt.A3DPatch.from_file(f)
        for _ in range(256):
            fmt.A3DMaterial.read(f)

        raw_fmt_version = struct.unpack('<i', f.read(4))[0]
        fmt_version = -raw_fmt_version if raw_fmt_version < 0 else raw_fmt_version
        instance_count = struct.unpack('<i', f.read(4))[0]

        for _ in range(instance_count):
            fmt.A3DInstance.from_file(f, fmt_version)

        if fmt_version >= 4:
            has_player_start_raw = f.read(4)
            if len(has_player_start_raw) < 4:
                return []
            if struct.unpack('<i', has_player_start_raw)[0]:
                fmt.A3DPlayerStart.from_file(f)

        enemygen_raw = f.read(4)
        if len(enemygen_raw) < 4:
            return []
        enemygen_count = struct.unpack('<i', enemygen_raw)[0]
        for _ in range(enemygen_count):
            fmt.A3DEnemyGen.from_file(f)

        marker_raw = f.read(4)
        if len(marker_raw) < 4:
            return []
        marker_count = struct.unpack('<i', marker_raw)[0]
        for _ in range(marker_count):
            marker = fmt.A3DMinimapMarker.from_file(f)
            marker_type = "building"
            if getattr(marker, "marker_type", 0) == getattr(fmt.A3DMinimapMarker, "TYPE_REGION", 2):
                marker_type = "region"
            markers.append({
                "name": marker.name,
                "label": marker.label,
                "x": marker.x,
                "y": marker.y,
                "type": marker_type,
                "fg": _ansi256_to_rgb6(marker.fg),
                "glyph": marker.glyph,
            })
    return markers



def _minimap_scene_from_a3d(
    map_path: str | None = None,
    cx: float = 0.0,
    cy: float = 0.0,
    scale: float = 8.0,
    width: int = 80,
    height: int = 40,
    show_meshes: bool = True,
    min_footprint_cells: float = 4.0,
) -> dict:
    """Read A3D data once and return the normalized minimap render scene."""
    fmt = _load_a3d_format()
    root = _fallback_project_root()

    if map_path is None:
        map_path = os.path.join(root, 'assets', 'a3d', 'game_map_y8.a3d')

    with open(map_path, 'rb') as f:
        hdr = fmt.A3DHeader.from_file(f)
        legacy = (hdr.reserved == fmt.A3DHeader.FORMAT_LEGACY)
        patches = [fmt.A3DPatch.from_file(f) for _ in range(hdr.num_patches)]

        # Read 256 A3DMaterials (512 bytes each = 131072 bytes)
        materials = []
        try:
            for _ in range(256):
                mat = fmt.A3DMaterial.read(f)
                if mat is not None:
                    materials.append(mat)
        except Exception:
            pass

        # Build true-color material palette from ramp 0, shade 8 (mid-brightness)
        mat_palette: dict | None = None
        if materials:
            mat_palette = {}
            for mid, mat in enumerate(materials):
                cell = mat.shade[0][8]
                mat_palette[mid] = {
                    'fg': cell.fg,
                    'bg': cell.bg,
                    'gl': chr(cell.gl) if 32 <= cell.gl < 128 else '.',
                }

        # Read instance list (fmt_version int32 + count int32 + instances)
        instances = []
        embedded_markers = []
        try:
            raw_fmt_version = struct.unpack('<i', f.read(4))[0]
            fmt_version = -raw_fmt_version if raw_fmt_version < 0 else raw_fmt_version
            inst_count = struct.unpack('<i', f.read(4))[0]
            if 0 <= inst_count < 100000:
                instances = [fmt.A3DInstance.from_file(f, fmt_version) for _ in range(inst_count)]
            if fmt_version >= 4:
                has_player_start_raw = f.read(4)
                if len(has_player_start_raw) == 4 and struct.unpack('<i', has_player_start_raw)[0]:
                    fmt.A3DPlayerStart.from_file(f)
            enemygen_raw = f.read(4)
            if len(enemygen_raw) == 4:
                enemygen_count = struct.unpack('<i', enemygen_raw)[0]
                for _ in range(enemygen_count):
                    fmt.A3DEnemyGen.from_file(f)
                marker_raw = f.read(4)
                if len(marker_raw) == 4:
                    marker_count = struct.unpack('<i', marker_raw)[0]
                    for _ in range(marker_count):
                        marker = fmt.A3DMinimapMarker.from_file(f)
                        marker_type = "building"
                        if getattr(marker, "marker_type", 0) == getattr(fmt.A3DMinimapMarker, "TYPE_REGION", 2):
                            marker_type = "region"
                        embedded_markers.append({
                            "name": marker.name,
                            "label": marker.label,
                            "x": marker.x,
                            "y": marker.y,
                            "type": marker_type,
                            "fg": _ansi256_to_rgb6(marker.fg),
                            "glyph": marker.glyph,
                        })
        except Exception:
            pass

    # Sparse world lookup: (wx, wy) -> (mat_id, height)
    world = {}
    for p in patches:
        bx, by = p.x * fmt.VISUAL_CELLS, p.y * fmt.VISUAL_CELLS
        for row in range(fmt.VISUAL_CELLS):
            for col in range(fmt.VISUAL_CELLS):
                h_row = min(row // 2, fmt.HEIGHT_CELLS)
                h_col = min(col // 2, fmt.HEIGHT_CELLS)
                world[(bx + col, by + row)] = (p.visual[row][col] & 0xFF, p.height[h_row][h_col])

    grid = []
    for gy in range(height):
        row_data = []
        for gx in range(width):
            wx = int(cx + (gx - width // 2) * scale)
            wy = int(cy + (gy - height // 2) * scale)
            row_data.append(world.get((wx, wy), (1, fmt.BASE_TERRAIN_HEIGHT if legacy else WATER_LEVEL + 1)))
        grid.append(row_data)

    grid_data = {
        "width": width, "height": height,
        "cx": cx, "cy": cy, "scale": scale, "grid": grid,
    }

    # Rasterize actual mesh geometry onto the minimap grid
    footprint_cells: set | None = None
    if show_meshes and instances:
        footprint_cells = set()
        _mesh_cache: dict[str, tuple | None] = {}
        for inst in instances:
            if inst.variant != 'mesh' or not inst.mesh_name:
                continue
            mn = inst.mesh_name
            if mn not in _mesh_cache:
                akm_path = _find_akm(mn, root)
                _mesh_cache[mn] = _read_ply_local(akm_path) if akm_path else None
            local_mesh = _mesh_cache[mn]
            if local_mesh is None:
                continue
            local_xy, tri_faces = local_mesh
            xy_verts = _apply_transform_xy(local_xy, inst.transform)
            cells = _rasterize_mesh(xy_verts, tri_faces, cx, cy, width, height, scale)
            if len(cells) < int(min_footprint_cells ** 2):
                continue  # too small (e.g. single tree)
            footprint_cells |= cells

    return {
        "grid_data": grid_data,
        "mat_palette": mat_palette,
        "markers": embedded_markers,
        "footprint_cells": footprint_cells if footprint_cells else None,
    }


def render_minimap_from_a3d(
    map_path: str | None = None,
    cx: float = 0.0,
    cy: float = 0.0,
    scale: float = 8.0,
    width: int = 80,
    height: int = 40,
    markers: list | None = None,
    player_pos: tuple | None = None,
    player_dir: float | None = None,
    show_meshes: bool = True,
    min_footprint_cells: float = 4.0,
) -> str:
    """Render minimap directly from an .a3d file. No asciiid process needed.

    Args:
        map_path: Path to .a3d file. Defaults to assets/a3d/copy_game_map_y8.a3d.
        cx, cy: World-space center.
        scale: World units per grid cell (smaller = more zoomed in).
        width, height: Output grid dimensions in terminal cells.
        markers: Marker list override. If None, loads embedded map markers.
        player_pos: Optional (x, y) shown as @. Defaults to (cx, cy).
        player_dir: Back-compat compatibility arg from older CLI callers.
                    Current renderer does not use facing direction here.
        show_meshes: Whether to overlay mesh footprint boxes.
        min_footprint_cells: Skip footprints narrower than this many grid cells
                             on both axes (filters out small objects like trees).

    Returns:
        ANSI-colored minimap string ready for print().
    """
    scene = _minimap_scene_from_a3d(
        map_path=map_path,
        cx=cx,
        cy=cy,
        scale=scale,
        width=width,
        height=height,
        show_meshes=show_meshes,
        min_footprint_cells=min_footprint_cells,
    )

    if markers is None:
        markers = scene["markers"]

    return render_ansi(
        scene["grid_data"], markers=markers, player_pos=player_pos or (cx, cy),
        mat_palette=scene["mat_palette"],
        footprint_cells=scene["footprint_cells"],
    )
