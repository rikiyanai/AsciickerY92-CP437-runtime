# Asciicker Blender Addon - Progress Log

> **DEPRECATED:** Tasks migrated to [.claude/tasks/blender-addon.md](../.claude/tasks/blender-addon.md)
>
> This file is kept for reference. For current roadmap, see the unified task system.

---

## Completed Features

### A3D Map Export (Working)
- **Terrain export**: Patches with height data and material IDs from vertex colors
- **Mesh instances**: Proper transform matrices (column-major), Z offset (+100), Z scale (x16)
- **Instance flags**: INST_VISIBLE | INST_USE_TREE (flags=3)
- **Enemy generators**: EnemyGen* empties with spawn properties
- **Materials**: Default 8 materials (water, grass, dirt, stone, sand, snow, wood, steel)

### UI Panels (Working)
- **Terrain Panel**: Create Terrain, Paint Terrain buttons
- **Material Brush Panel**: 8 material presets with quick buttons
- **Color Tools Panel**: Snap to Palette, Analyze Colors, Collision tools
- **Mesh Library Panel**: Dropdown of all .akm files, Place Mesh button
- **Export Panel**: Quick Export & Test, A3D/AKM export, AKM import
- **Info Panel**: Workflow guide

### Key Fixes Applied
1. Matrix format: Row-major to column-major conversion
2. Mesh Z position: Added MIN_TERRAIN_HEIGHT (100) offset above water level (55)
3. Mesh Z scale: Applied HEIGHT_SCALE (16x) to match original maps
4. Instance flags: Changed default from 0 to 3
5. Story ID: Changed default from 0 to -1

### Debug Output (Preserved)
- C++: `[RenderMesh]` positions, `[LoadMesh]` status with face counts
- Python: Patch counts, material distribution, instance positions

---

## Roadmap

### 1. AKM Mesh Visualization
Load actual .akm mesh geometry in Blender instead of placeholder cubes.
- Parse AKM binary format
- Create Blender mesh from vertices/faces
- Display vertex colors
- Status: **Pending**

### 2. Color System Research
Understand asciiid's rendering pipeline:
- Color grading (palette conversion)
- Dithering algorithms
- Glyph selection system
- Material paint functions
- Status: **Pending**

### 3. Paint Features Port
Port asciiid material painting to Blender:
- Vertex color painting with material IDs
- Simpler UI with clear buttons
- Visual feedback for material types
- Status: **Pending**

### 4. Camera & Debug Features
- Zoom out capability
- Fly mode for navigation
- Debug overlay showing:
  - Object visibility status
  - Vertex counts
  - Material distribution
  - Collision info
- Status: **Pending**

### 5. Automatic Painter
- Building auto-painter (detect walls, roofs, floors)
- Texture-to-vertex-color converter
- Material suggestions based on geometry
- Status: **Pending**

### 6. Mesh Optimizer
- Vertex count reducer
- Mesh decimation with color preservation
- LOD generation
- Status: **Pending**

---

## File Structure

```
io_asciicker/
  __init__.py          - Addon registration
  ui/
    panels.py          - Sidebar UI panels
  scene/
    export_a3d.py      - A3D map export
    a3d_format.py      - A3D binary format classes
    default_materials.py - Material definitions
  mesh/
    export_akm.py      - AKM mesh export
    import_akm.py      - AKM mesh import
  tools/
    colors.py          - Color tools operators
    terrain.py         - Terrain operators
    paint.py           - Material paint operators
```

## Technical Notes

### A3D Format
- Header: 16 bytes (magic, version, patch count, reserved)
- Patches: 188 bytes each (5x5 heights, 8x8 materials)
- Materials: 256 entries x 512 bytes each
- Instances: Variable (mesh name, inst name, 16 doubles transform, flags, story_id)
- Enemy generators: Variable (position, spawn params, equipment)

### Transform Matrix (Column-Major)
```
[0]  [4]  [8]  [12]   <- X axis, Y axis, Z axis, Translation X
[1]  [5]  [9]  [13]
[2]  [6]  [10] [14]   <- index 10 = Z scale, index 14 = Translation Z
[3]  [7]  [11] [15]
```

### Height System
- Water level: 55
- Safe terrain height: 100+
- HEIGHT_SCALE: 16
- Meshes need Z > 55 to be visible
