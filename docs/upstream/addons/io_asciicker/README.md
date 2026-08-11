# Asciicker Blender Addon

**Version:** 2.0.0
**Blender:** 4.5+
**Status:** Canonical single addon

The single unified Blender addon for the Asciicker game engine. Combines AKM mesh import/export, A3D map export, terrain painting, color tools, GMap bake, and mesh library into one addon.

> **Migration note:** The standalone addons `io_mesh_akm`, `io_scene_a3d`, and `io_scene_a3d_tools` are deprecated on Blender 4.5+. They no longer register operators and only print deprecation warnings. Remove any stale copies from your Blender addons folder to avoid confusion. See [Migration](#migration-from-legacy-addons) below.

---

## Features

### 1. Asciiid Bypasser (A3D Export)
Export Blender scenes directly to `.a3d` map format, bypassing the need for the asciiid editor.

- **Terrain Export:** 8x8 visual cells, 5x5 height samples per patch
- **Materials:** 256 materials with 4 elevation ramps × 16 shades
- **Mesh Instances:** Object placement with full transform matrices
- **Enemy Generators:** Spawn points with equipment configuration

### 2. Vertex Color Painter
Paint terrain materials using vertex colors with visual feedback.

| ID | Material | Display Color |
|----|----------|---------------|
| 0 | Water | Blue |
| 1 | Grass | Green |
| 2 | Dirt | Brown |
| 3 | Stone | Gray |
| 4 | Sand | Tan |
| 5 | Snow | White |
| 6 | Wood | Dark Brown |
| 7 | Steel | Light Gray |

- Material ID stored in red channel (0-255)
- Visual colors in green/blue for easier painting
- One-click preset buttons for common materials

### 3. AKM Mesh Import/Export
Import and export Asciicker mesh files (PLY-based format).

- Vertex positions, normals, UVs
- Vertex colors (with alpha for collision)
- Freestyle edge marks for special edges
- **Import All from Folder** (`asciicker.import_all_meshes`): Batch imports every `.akm` from the `meshes/` directory

### 4. Color Tools
Ensure vertex colors work with the terminal 6x6x6 palette.

- **Snap to Palette:** Quantize colors to safe values (0, 51, 102, 153, 204, 255)
- **Analyze Colors:** Check how many colors will need dithering
- **Collision Marking:** Set faces as solid or passthrough via vertex groups

---

## Installation

1. Download/clone this `io_asciicker` folder
2. In Blender: Edit → Preferences → Add-ons → Install
3. Navigate to and select the `io_asciicker` folder
4. Enable "Import-Export: Asciicker Tools"

Or create a symlink for development:
```bash
ln -s /path/to/asciicker/io_asciicker ~/.config/blender/4.5/scripts/addons/io_asciicker
```

---

## Usage

### Creating a New Map

1. **Create Terrain:** Sidebar → Asciicker → Create Terrain
   - Set size (rounds to 8-unit patch grid)
   - Terrain is initialized with grass (Material 1)

2. **Paint Materials:** Click "Paint Terrain" to enter vertex paint mode
   - Use material preset buttons to set brush color
   - Paint on terrain to assign material IDs

3. **Place Meshes:** Import AKM meshes and position them in scene
   - Object names become mesh references in export
   - Duplicates are handled automatically (tree.001 → tree.akm)

4. **Add Enemy Spawns:** Create Empty objects named "EnemyGen*"
   - Set custom properties: alive_max, armor, helmet, etc.

5. **Export:** File → Export → Asciicker Map (.a3d)
   - Or use Quick Export (sidebar) for one-click validate + export
   - **Note:** Mesh instance export requires `ASCIICKER_EXPORT_INSTANCES=1` environment variable

### Editing Existing Maps

1. Load terrain in asciiid editor, save as .a3d
2. Materials are extracted from the .a3d file
3. Edit terrain/meshes in Blender
4. Re-export to .a3d

---

## File Format Reference

### A3D Map Format

```
FileHeader (16 bytes)
├── "AS3D" signature (4 bytes)
├── header_size: 16 (4 bytes)
├── num_patches (4 bytes)
└── reserved (4 bytes)

Terrain Patches (N × 188 bytes each)
├── x, y coordinates (8 bytes) - int32
├── visual[8][8] material IDs (128 bytes) - uint16
├── height[5][5] heightmap (50 bytes) - uint16
└── diag triangle flags (2 bytes) - uint16

Materials (256 × 512 bytes = 128KB)
└── MatCell shade[4][16] per material
    └── MatCell: fg[3], glyph, bg[3], flags (8 bytes)

World Instances (variable)
├── format_version: -1 (4 bytes)
├── num_instances (4 bytes)
└── Per instance:
    ├── mesh_name_len + mesh_name string
    ├── inst_name_len + inst_name string
    ├── tm[16] transform matrix (128 bytes, doubles)
    ├── flags (4 bytes)
    └── story_id (4 bytes)

Enemy Generators
├── count (4 bytes)
└── Per generator (44 bytes):
    pos[3], alive_max, revive_min, revive_max,
    armor, helmet, shield, sword, crossbow
```

### AKM Mesh Format

PLY-based ASCII format with extensions:
- Vertex colors (RGBA, alpha = collision weight)
- Freestyle edge marks for special geometry
- Standard PLY vertex/face structure

---

## Architecture

```
io_asciicker/
├── __init__.py              # Main addon registration
├── README.md                # This file
├── mesh/
│   ├── __init__.py
│   ├── export_akm.py        # AKM mesh export
│   └── import_akm.py        # AKM mesh import
├── scene/
│   ├── __init__.py
│   ├── export_a3d.py        # A3D map export (bypasser)
│   ├── a3d_format.py        # Binary format definitions
│   └── default_materials.py # Material extraction
├── tools/
│   ├── __init__.py
│   ├── color_tools.py       # Palette snapping, analysis
│   ├── terrain_tools.py     # Terrain creation
│   └── material_painter.py  # Vertex paint presets
└── ui/
    ├── __init__.py
    └── panels.py            # All UI panels
```

---

## Blender 4.5 Compatibility Notes

Changes from Blender 2.82:

1. **Property Syntax:** Use annotations (`prop: Type = ...`) not assignment
2. **Vertex Colors:** Use `mesh.color_attributes` instead of `mesh.vertex_colors`
3. **Menu Registration:** Updated TOPBAR menu names
4. **Python Version:** 4.5 uses Python 3.11+

---

## Source Code References

| File | Lines | Purpose |
|------|-------|---------|
| terrain.cpp | 2873-2948 | Terrain save/load |
| world.cpp | 4430-4705 | Instance save/load |
| enemygen.cpp | 115-140 | Enemy generator save |
| render.h | 16-40 | MatCell structure |

---

## Migration from Legacy Addons

This addon supersedes three standalone addons that were developed for Blender 2.82:

| Legacy Addon | Equivalent in io_asciicker | Status |
|---|---|---|
| `io_mesh_akm` | `io_asciicker/mesh/` | Deprecated; source files kept for Python import compat |
| `io_scene_a3d` | `io_asciicker/scene/` | Deprecated; source files kept for Python import compat |
| `io_scene_a3d_tools` | `io_asciicker/tools/` + `ui/` | Deprecated; single `__init__.py` |

**To migrate:**
1. In Blender 4.5, disable and remove any installed copies of the legacy addons
2. Remove stale addon folders from `~/.config/blender/4.5/scripts/addons/` (or `~/Library/Application Support/Blender/4.5/scripts/addons/` on macOS)
3. Ensure `io_asciicker` is symlinked or installed
4. The legacy addon folders in the repo still exist for `from io_mesh_akm import import_akm` compatibility, but their `register()` functions are no-ops

---

## Development

### Testing

```bash
# Run Blender with addon
blender --python-use-system-env

# Check console for registration errors
# Window → Toggle System Console (Windows)
# Launch from terminal (Mac/Linux)
```

### Debugging

Enable developer extras in Blender preferences to see Python errors in the UI.

---

## License

GPL v2 or later (same as Blender)
