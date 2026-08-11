# Asciicker A3D Map Format Definitions
# Binary format for terrain, materials, instances, player-start, enemy generators, and minimap markers
#
# [DATA-CONTRACT:A3D] This module defines the Python-side binary structs that
#     must stay in lockstep with the C++ engine's A3D loader in world.cpp and
#     the terrain data layout in terrain.cpp / terrain.h.
# [DEPENDENCY:BLENDER] Used exclusively within the Blender addon export path;
#     no Blender API imports here -- pure data serialization.
# [FLOW:WORLD] Data structures flow: Blender scene -> a3d_format classes
#     -> binary .a3d file -> engine world.cpp LoadWorldAS3D.

"""
A3D binary format struct definitions.

ARCHITECTURE
============
Each class corresponds to a contiguous binary record in the ``.a3d`` file:

    +-----------------+  offset 0
    | A3DHeader       |  16 bytes (signature, header size, patch count, reserved)
    +-----------------+
    | A3DPatch[N]     |  188 bytes each (x/y coords, 8x8 visual, 5x5 height, diag)
    +-----------------+
    | A3DMaterial[256] |  512 bytes each (4 ramps x 16 shades x 8-byte MatCell)
    +-----------------+
    | format_version  |  4 bytes  (int32, currently -4)
    | instance_count  |  4 bytes  (int32)
    | A3DInstance[M]  |  variable (length-prefixed strings + 128-byte transform + 8 bytes flags)
    +-----------------+
    | has_player_start|  4 bytes  (int32, v4+ only)
    | A3DPlayerStart  |  20 bytes (optional, v4+ only)
    +-----------------+
    | enemygen_count  |  4 bytes  (int32)
    | A3DEnemyGen[K]  |  44 bytes each
    +-----------------+
    | marker_count    |  4 bytes  (int32, optional in older files)
    | A3DMinimapMarker[L] | variable
    +-----------------+

KEY EXPORTS
-----------
- ``A3DHeader``    -- File header with signature ``AS3D``.
- ``A3DPatch``     -- Single terrain tile: height map + visual/material map.
- ``MatCell``      -- 8-byte foreground/background/glyph cell in a material shade ramp.
- ``A3DMaterial``  -- 512-byte material with 4 elevation ramps of 16 shade steps.
- ``A3DInstance``  -- Mesh instance with 4x4 transform and metadata.
- ``A3DPlayerStart`` -- Embedded map-owned player-start position and facing.
- ``A3DEnemyGen``  -- Enemy spawn point with equipment loadout.
- ``A3DMinimapMarker`` -- Embedded minimap marker label and display metadata.
- ``read_materials_from_a3d()`` -- Utility to extract the 256-material palette from
  an existing ``.a3d`` file (used by ``default_materials.py``).

PIPELINE CONTEXT
----------------
``export_a3d.py`` instantiates these classes, populates them from Blender scene
data, and calls ``.write(f)`` to emit the binary.  The C++ engine reads the
same layout in ``world.cpp`` (``LoadWorldAS3D``).

Constant values (``HEIGHT_SCALE``, ``HEIGHT_CELLS``, ``VISUAL_CELLS``) are
mirrored from ``terrain.h`` -- any change there must be reflected here.

TODO(PIPELINE-FIX): Constants are duplicated between this file and terrain.h
    with no automated sync.  A code-gen step or shared header extraction would
    prevent silent drift.
"""

import struct

# ---------------------------------------------------------------------------
# Constants from terrain.h
# [DATA-CONTRACT:A3D] These must match the #defines in terrain.h exactly.
# ---------------------------------------------------------------------------
HEIGHT_SCALE = 16       # Z-steps per visual cell (terrain.h line 8)
HEIGHT_CELLS = 4        # Height-map vertices per patch axis minus one (terrain.h line 9)
VISUAL_CELLS = 8        # Material/visual cells per patch axis (terrain.h line 10)
# Terrain height bake quantizes to multiples of this step.  TERRAIN_EXPORT_BASELINE MUST be
# a multiple of TERRAIN_HEIGHT_QUANTIZATION_STEP.  An off-grid baseline causes edge samples to
# round below baseline, fail the overwrite-height gate, and leave cells as holes (FL-1181).
TERRAIN_HEIGHT_QUANTIZATION_STEP = HEIGHT_SCALE

# ---------------------------------------------------------------------------
# Height levels (terrain uses uint16 heights)
# WHY: The engine treats 0x8000 as the water surface.  Terrain above water
# starts at BASE_TERRAIN_HEIGHT.  Blender Z=0 maps to BASE_TERRAIN_HEIGHT so
# that flat terrain sits above water by default.
# ---------------------------------------------------------------------------
WATER_LEVEL = 0x8000           # Water surface height (32768)
# !! BASELINE CONFUSION HAS CAUSED 5+ FALSE CLOSURES (FL-2533/FL-2549/FL-2553/FL-2554, FL-1181) !!
# BASE_TERRAIN_HEIGHT  = 0xA000 (40960) → LEGACY only, for old mesh instances
# TERRAIN_EXPORT_BASELINE = 128 (8×HEIGHT_SCALE=16) → CORRECT for OSM exports
# If buildings float/are invisible/are wrong scale, you used the LEGACY constant.

BASE_TERRAIN_HEIGHT = 0xA000   # LEGACY mesh-instance Z baseline above water (40960) — do NOT use for new OSM exports
# WHY 128 not 120: 120 % HEIGHT_SCALE (16) = 8, which is off the quantization grid.  Edge
# samples at ground level round DOWN from ~120 to 112.  The bake's overwrite-height=0 gate
# skips writes where quantized_h <= existing_h (112 <= 120), leaving cells at 120 in raised
# terrain — terrain holes (FL-1181, FL-2546, FL-2573).  128 = 8 × HEIGHT_SCALE is the nearest
# on-grid value; edge samples at ~119-127 still round to 128 and get skipped (correct: those
# cells should stay at flat-ground baseline), while raised-terrain samples round to 144+ and
# pass the gate.  ASSERT: TERRAIN_EXPORT_BASELINE % TERRAIN_HEIGHT_QUANTIZATION_STEP == 0.
TERRAIN_EXPORT_BASELINE = 128  # Terrain-patch and deferred-OSM ground baseline — USE THIS for OSM

# ---------------------------------------------------------------------------
# File signature -- first 4 bytes of every valid .a3d file.
# [DATA-CONTRACT:A3D] The engine rejects files that do not start with this.
# ---------------------------------------------------------------------------
A3D_SIGNATURE = b'AS3D'
WORLD_FORMAT_VERSION = -4


class A3DHeader:
    """16-byte file header.

    Layout (little-endian):
        [0:4]   ``AS3D`` signature
        [4:8]   uint32 header_size  (always 16 currently)
        [8:12]  uint32 num_patches  (number of terrain tiles that follow)
        [12:16] uint32 reserved     (zero; future use)
    """
    SIZE = 16
    FORMAT_LEGACY = None  # No legacy format flag defined; reserved is always 0

    def __init__(self, num_patches=0):
        """Initialize header with AS3D signature and the given patch count."""
        self.file_sign = A3D_SIGNATURE
        self.header_size = self.SIZE
        self.num_patches = num_patches
        self.reserved = 0

    @classmethod
    def from_file(cls, f):
        """Deserialize a 16-byte header from an open binary file handle *f*.

        Returns an A3DHeader instance, or raises ValueError on bad signature.
        """
        data = f.read(16)
        if len(data) < 16:
            raise ValueError("Truncated A3D header")
        sig = data[0:4]
        if sig != A3D_SIGNATURE:
            raise ValueError(f"Invalid A3D signature: {sig!r}")
        header_size, num_patches, reserved = struct.unpack_from('<III', data, 4)
        obj = cls(num_patches)
        obj.header_size = header_size
        obj.reserved = reserved
        return obj

    def write(self, f):
        """Serialize the header to an open binary file handle *f*.

        [FLOW:WORLD] First 16 bytes of the .a3d file -- the engine validates
        the signature before reading any further.
        """
        f.write(self.file_sign)
        f.write(struct.pack('<I', self.header_size))
        f.write(struct.pack('<I', self.num_patches))
        f.write(struct.pack('<I', self.reserved))


class A3DPatch:
    """Terrain patch -- 188 bytes.

    Each patch covers an 8x8 world-unit tile and stores:
      - ``x``, ``y``: signed int32 patch coordinates in the world grid.
      - ``visual[8][8]``: uint16 material IDs for the renderer (VISUAL_CELLS).
      - ``height[5][5]``: uint16 vertex heights for the physics/render mesh
        (HEIGHT_CELLS+1 vertices along each axis, forming HEIGHT_CELLS quads).
      - ``diag``: uint16 bitmask controlling triangle-split direction of each
        height-map quad.

    Size breakdown:
        8 (x,y int32) + 128 (8*8*2 visual) + 50 (5*5*2 height) + 2 (diag) = 188
    """

    def __init__(self, x=0, y=0):
        """Initialize a patch at grid coords (*x*, *y*) with zeroed visual/height maps."""
        self.x = x
        self.y = y
        self.visual = [[0] * VISUAL_CELLS for _ in range(VISUAL_CELLS)]  # 8x8 uint16
        self.height = [[0] * (HEIGHT_CELLS + 1) for _ in range(HEIGHT_CELLS + 1)]  # 5x5 uint16
        self.diag = 0

    @classmethod
    def from_file(cls, f):
        """Deserialize a 188-byte patch from an open binary file handle *f*."""
        xy = f.read(8)
        if len(xy) < 8:
            raise ValueError("Truncated patch header")
        x, y = struct.unpack('<ii', xy)
        patch = cls(x, y)
        # Visual map: 8x8 uint16
        vis_data = f.read(VISUAL_CELLS * VISUAL_CELLS * 2)
        vis_vals = struct.unpack(f'<{VISUAL_CELLS * VISUAL_CELLS}H', vis_data)
        for row in range(VISUAL_CELLS):
            for col in range(VISUAL_CELLS):
                patch.visual[row][col] = vis_vals[row * VISUAL_CELLS + col]
        # Height map: 5x5 uint16
        h_count = (HEIGHT_CELLS + 1) * (HEIGHT_CELLS + 1)
        h_data = f.read(h_count * 2)
        h_vals = struct.unpack(f'<{h_count}H', h_data)
        for row in range(HEIGHT_CELLS + 1):
            for col in range(HEIGHT_CELLS + 1):
                patch.height[row][col] = h_vals[row * (HEIGHT_CELLS + 1) + col]
        # Diag bitmask
        patch.diag = struct.unpack('<H', f.read(2))[0]
        return patch

    def write(self, f):
        """Serialize the patch to an open binary file handle *f*.

        [FLOW:WORLD] Each patch writes 188 bytes consumed by terrain.cpp's
        patch loader.
        """
        f.write(struct.pack('<ii', self.x, self.y))

        # Visual map (8x8 uint16) -- material ID per cell
        for row in self.visual:
            for val in row:
                f.write(struct.pack('<H', val & 0xFFFF))

        # Height map (5x5 uint16) -- absolute terrain elevation per vertex
        for row in self.height:
            for val in row:
                f.write(struct.pack('<H', val & 0xFFFF))

        # WHY: diag bitmask -- each bit controls which diagonal is used to
        # split a height-map quad into two triangles.  The engine reads this
        # in terrain.cpp to decide triangulation per cell.
        f.write(struct.pack('<H', self.diag))


class MatCell:
    """Material cell -- 8 bytes.

    Encodes a single character cell in the ASCII renderer's material shade ramp.

    Layout (byte-by-byte):
        [0] fg_r   Foreground red
        [1] fg_g   Foreground green
        [2] fg_b   Foreground blue
        [3] glyph  ASCII code-point (e.g. 32=' ', 46='.', 35='#')
        [4] bg_r   Background red
        [5] bg_g   Background green
        [6] bg_b   Background blue
        [7] flags  Rendering flags (reserved, currently 0)

    The engine composites ``fg`` color onto ``glyph`` over ``bg`` to produce
    the final terrain appearance in the terminal/GL renderer.
    """
    SIZE = 8

    def __init__(self, fg=(0, 0, 0), gl=0, bg=(0, 0, 0), flags=0):
        """Initialize an 8-byte material cell with fg/bg colors, glyph, and flags."""
        self.fg = fg  # (r, g, b) foreground
        self.gl = gl  # glyph
        self.bg = bg  # (r, g, b) background
        self.flags = flags

    def write(self, f):
        """Serialize the cell to an open binary file handle *f*."""
        f.write(struct.pack('BBB', self.fg[0], self.fg[1], self.fg[2]))
        f.write(struct.pack('B', self.gl))
        f.write(struct.pack('BBB', self.bg[0], self.bg[1], self.bg[2]))
        f.write(struct.pack('B', self.flags))

    @classmethod
    def read(cls, f):
        """Deserialize a single MatCell from an open binary file handle *f*.

        Returns ``None`` if fewer than 8 bytes remain.
        """
        data = f.read(8)
        if len(data) < 8:
            return None
        return cls(
            fg=(data[0], data[1], data[2]),
            gl=data[3],
            bg=(data[4], data[5], data[6]),
            flags=data[7]
        )


class A3DMaterial:
    """Material with 4x16 shade ramp -- 512 bytes.

    Each material has 4 *elevation ramps* (flat, gentle slope, moderate, steep)
    and each ramp has 16 *shade steps* (light to dark).  This gives the engine
    enough variety to render terrain with slope-dependent shading.

    Total: 4 ramps * 16 shades * 8 bytes/cell = 512 bytes per material.
    The file stores 256 materials contiguously (256 * 512 = 131072 bytes).
    """
    SIZE = 4 * 16 * MatCell.SIZE

    def __init__(self):
        """Initialize a 512-byte material with 4 ramps of 16 default MatCells."""
        self.shade = [[MatCell() for _ in range(16)] for _ in range(4)]

    def write(self, f):
        """Serialize all 4*16 shade cells to an open binary file handle *f*.

        [DATA-CONTRACT:A3D] The engine reads exactly 512 bytes per material
        (4 ramps * 16 shades * 8 bytes).
        """
        for ramp in self.shade:
            for cell in ramp:
                cell.write(f)

    @classmethod
    def read(cls, f):
        """Deserialize one 512-byte material from an open binary file handle *f*."""
        mat = cls()
        for i in range(4):
            for j in range(16):
                cell = MatCell.read(f)
                if cell:
                    mat.shade[i][j] = cell
        return mat


class A3DInstance:
    """Mesh instance with transform.

    Represents a placed object in the world: a reference to a mesh file
    (``.akm``) plus a 4x4 column-major transform matrix, flags, and an
    optional story/quest identifier.

    Wire format (variable length):
        int32  mesh_name_len
        bytes  mesh_name (UTF-8)
        int32  inst_name_len
        bytes  inst_name (UTF-8)
        double[16] transform (4x4 column-major matrix)
        int32  flags       (e.g. INST_VISIBLE | INST_USE_TREE)
        int32  story_id    (-1 = not story-linked)

    WHY column-major: The C++ engine (world.cpp) stores transforms column-major
    so that matrix[12..14] hold the translation, matching OpenGL convention.
    """

    def __init__(self, mesh_name="", inst_name="", transform=None, flags=0, story_id=0, variant='mesh'):
        """Initialize an instance with an identity transform and optional metadata.

        *variant* is 'mesh', 'sprite', or 'item'.  Sprite/item instances carry
        additional fields set by from_file().
        """
        self.variant = variant
        self.mesh_name = mesh_name
        self.inst_name = inst_name
        self.transform = transform if transform else [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0
        ]
        self.flags = flags
        self.story_id = story_id
        # Sprite-only fields (variant='sprite')
        self.pos = [0.0, 0.0, 0.0]
        self.yaw = 0.0
        self.anim = 0
        self.frame = 0
        self.reps = [0, 0, 0, 0]
        # Item-only fields (variant='item')
        self.item_definition_id = 0
        self.visual_style_id = 0
        self.presentation_kind_id = 0
        self.item_count = 0

    @classmethod
    def from_file(cls, f, format_version=0):
        """Deserialize a variable-length instance from an open binary file handle *f*.

        Mirrors world.cpp:5030-5236.  The first int32 (mesh_id_len) discriminates:
          >= 0  -> mesh instance (name + transform + flags)
          -1    -> sprite instance (pos + yaw + anim + frame + reps + flags)
          -2    -> item instance (v3 bundle ids + count + pos + yaw + flags)
        """
        mesh_id_len = struct.unpack('<i', f.read(4))[0]

        if mesh_id_len >= 0:
            # Mesh instance
            mesh_name = f.read(mesh_id_len).decode('utf-8') if mesh_id_len > 0 else ""
            inst_name_len = struct.unpack('<i', f.read(4))[0]
            inst_name = f.read(inst_name_len).decode('utf-8') if inst_name_len > 0 else ""
            transform = list(struct.unpack('<16d', f.read(128)))
            flags, story_id = struct.unpack('<ii', f.read(8))
            return cls(mesh_name=mesh_name, inst_name=inst_name,
                       transform=transform, flags=flags, story_id=story_id,
                       variant='mesh')

        elif mesh_id_len == -1:
            # Sprite instance
            inst = cls(variant='sprite')
            inst_name_len = struct.unpack('<i', f.read(4))[0]
            inst.inst_name = f.read(inst_name_len).decode('utf-8') if inst_name_len > 0 else ""
            inst.pos = list(struct.unpack('<fff', f.read(12)))
            inst.yaw = struct.unpack('<f', f.read(4))[0]
            inst.anim, inst.frame = struct.unpack('<ii', f.read(8))
            inst.reps = list(struct.unpack('<4i', f.read(16)))
            inst.flags, inst.story_id = struct.unpack('<ii', f.read(8))
            return inst

        elif mesh_id_len == -2:
            # Item instance
            inst = cls(variant='item')
            if format_version >= 3:
                (
                    inst.item_definition_id,
                    inst.visual_style_id,
                    inst.presentation_kind_id,
                    inst.item_count,
                ) = struct.unpack('<iiii', f.read(16))
            else:
                raise ValueError("A3D item records require format_version >= 3 bundle ids")
            inst.pos = list(struct.unpack('<fff', f.read(12)))
            inst.yaw = struct.unpack('<f', f.read(4))[0]
            inst.flags, inst.story_id = struct.unpack('<ii', f.read(8))
            return inst

        else:
            raise ValueError(f"Unknown instance variant discriminant: {mesh_id_len}")

    def write(self, f):
        """Serialize the instance record to an open binary file handle *f*.

        [FLOW:WORLD] Each instance is a variable-length record read by the
        engine's instance loader in world.cpp after the material palette.
        Discriminant: mesh_id_len >= 0 = mesh, -1 = sprite, -2 = item.
        """
        if self.variant == 'sprite':
            f.write(struct.pack('<i', -1))  # sprite discriminant
            inst_bytes = self.inst_name.encode('utf-8') if self.inst_name else b''
            f.write(struct.pack('<i', len(inst_bytes)))
            if inst_bytes:
                f.write(inst_bytes)
            f.write(struct.pack('<fff', *self.pos))
            f.write(struct.pack('<f', self.yaw))
            f.write(struct.pack('<ii', self.anim, self.frame))
            f.write(struct.pack('<4i', *self.reps))
            f.write(struct.pack('<ii', self.flags, self.story_id))

        elif self.variant == 'item':
            f.write(struct.pack('<i', -2))  # item discriminant
            f.write(struct.pack(
                '<iiii',
                self.item_definition_id,
                self.visual_style_id,
                self.presentation_kind_id,
                self.item_count,
            ))
            f.write(struct.pack('<fff', *self.pos))
            f.write(struct.pack('<f', self.yaw))
            f.write(struct.pack('<ii', self.flags, self.story_id))

        else:
            # Mesh instance (default)
            mesh_bytes = self.mesh_name.encode('utf-8')
            f.write(struct.pack('<i', len(mesh_bytes)))
            if mesh_bytes:
                f.write(mesh_bytes)

            inst_bytes = self.inst_name.encode('utf-8') if self.inst_name else b''
            f.write(struct.pack('<i', len(inst_bytes)))
            if inst_bytes:
                f.write(inst_bytes)

            # 4x4 transform as 16 doubles (128 bytes)
            for val in self.transform:
                f.write(struct.pack('<d', float(val)))

            f.write(struct.pack('<ii', self.flags, self.story_id))


class A3DPlayerStart:
    """Embedded map-owned player-start record.

    Layout (little-endian):
        float[3]  pos
        float32   yaw
        float32   dir
    """

    SIZE = 20

    def __init__(self, pos=None, yaw=0.0, dir=0.0):
        self.pos = list(pos) if pos else [0.0, 0.0, 0.0]
        self.yaw = float(yaw)
        self.dir = float(dir)

    @classmethod
    def from_file(cls, f):
        raw = f.read(cls.SIZE)
        if len(raw) < cls.SIZE:
            raise ValueError("Truncated player-start payload")
        x, y, z, yaw, dir = struct.unpack('<fffff', raw)
        return cls(pos=[x, y, z], yaw=yaw, dir=dir)

    def write(self, f):
        f.write(struct.pack(
            '<fffff',
            float(self.pos[0]),
            float(self.pos[1]),
            float(self.pos[2]),
            float(self.yaw),
            float(self.dir),
        ))


class A3DEnemyGen:
    """Enemy generator -- 44 bytes.

    Placed via Blender Empty objects whose names start with ``EnemyGen``.
    Custom properties on the Empty map to equipment loadout fields.

    Layout (little-endian):
        float[3]  pos         (world-space XYZ)
        int32     alive_max   (max simultaneous live enemies)
        int32     revive_min  (min respawn delay ticks)
        int32     revive_max  (max respawn delay ticks)
        int32     armor       (armor item ID)
        int32     helmet      (helmet item ID)
        int32     shield      (shield item ID)
        int32     sword       (sword item ID)
        int32     crossbow    (crossbow item ID)

    Size: 12 (pos) + 32 (8 x int32) = 44 bytes.
    """
    SIZE = 44

    def __init__(self):
        """Initialize a 44-byte enemy generator with default equipment loadout."""
        self.pos = [0.0, 0.0, 0.0]
        self.alive_max = 1
        self.revive_min = 0
        self.revive_max = 0
        self.armor = 0
        self.helmet = 0
        self.shield = 0
        self.sword = 0
        self.crossbow = 0

    @classmethod
    def from_file(cls, f):
        """Deserialize a 44-byte enemy generator from an open binary file handle *f*."""
        gen = cls()
        gen.pos = list(struct.unpack('<fff', f.read(12)))
        (gen.alive_max, gen.revive_min, gen.revive_max,
         gen.armor, gen.helmet, gen.shield,
         gen.sword, gen.crossbow) = struct.unpack('<iiiiiiii', f.read(32))
        return gen

    def write(self, f):
        """Serialize the enemy generator to an open binary file handle *f*.

        [FLOW:WORLD] Written as the final section of the .a3d file; read
        by enemygen.cpp in the engine.
        """
        # WHY: Position is float[3] (12 bytes) while all equipment/config
        # fields are int32 (32 bytes), matching the engine's EnemyGen struct.
        f.write(struct.pack('<fff', self.pos[0], self.pos[1], self.pos[2]))
        f.write(struct.pack('<iiiiiiii',
                            self.alive_max, self.revive_min, self.revive_max,
                            self.armor, self.helmet, self.shield,
                            self.sword, self.crossbow))


class A3DMinimapMarker:
    """Embedded minimap marker with display metadata.

    Stored after the enemy-generator section in newer ``.a3d`` files. Older
    files may stop at EOF after enemy generators and therefore have zero
    embedded minimap markers.

    Layout (little-endian):
        int32     name_len
        uint8[]   name_utf8
        int32     label_len
        uint8[]   label_utf8
        float32   x
        float32   y
        uint8     fg
        uint8     glyph
        uint8     marker_type
        uint8     reserved
    """

    TYPE_CUSTOM = 0
    TYPE_BUILDING = 1
    TYPE_REGION = 2

    def __init__(self, name="", label="", x=0.0, y=0.0, fg=226, glyph="X", marker_type=TYPE_CUSTOM):
        self.name = name
        self.label = label
        self.x = float(x)
        self.y = float(y)
        self.fg = int(fg) & 0xFF
        self.glyph = glyph if glyph else "X"
        self.marker_type = int(marker_type) & 0xFF

    @classmethod
    def from_file(cls, f):
        """Deserialize one minimap marker from an open binary file handle *f*."""
        raw = f.read(4)
        if len(raw) < 4:
            raise ValueError("Truncated minimap marker name length")
        name_len = struct.unpack('<i', raw)[0]
        if name_len < 0:
            raise ValueError(f"Invalid minimap marker name length: {name_len}")
        name = f.read(name_len).decode('utf-8') if name_len > 0 else ""

        raw = f.read(4)
        if len(raw) < 4:
            raise ValueError("Truncated minimap marker label length")
        label_len = struct.unpack('<i', raw)[0]
        if label_len < 0:
            raise ValueError(f"Invalid minimap marker label length: {label_len}")
        label = f.read(label_len).decode('utf-8') if label_len > 0 else ""

        raw = f.read(12)
        if len(raw) < 12:
            raise ValueError("Truncated minimap marker payload")
        x, y, fg, glyph, marker_type, _reserved = struct.unpack('<ffBBBB', raw)
        return cls(
            name=name,
            label=label,
            x=x,
            y=y,
            fg=fg,
            glyph=chr(glyph) if glyph else "X",
            marker_type=marker_type,
        )

    def write(self, f):
        """Serialize the minimap marker to an open binary file handle *f*."""
        name_bytes = self.name.encode('utf-8') if self.name else b''
        label_bytes = self.label.encode('utf-8') if self.label else b''
        glyph_ord = ord(self.glyph[0]) if self.glyph else ord('X')
        f.write(struct.pack('<i', len(name_bytes)))
        if name_bytes:
            f.write(name_bytes)
        f.write(struct.pack('<i', len(label_bytes)))
        if label_bytes:
            f.write(label_bytes)
        f.write(struct.pack(
            '<ffBBBB',
            float(self.x),
            float(self.y),
            self.fg & 0xFF,
            glyph_ord & 0xFF,
            self.marker_type & 0xFF,
            0,
        ))


def read_materials_from_a3d(filepath):
    """Extract materials from an existing A3D file.

    [DATA-CONTRACT:A3D] Reads the 256-material palette that sits immediately
    after the terrain patches in the binary layout.  Used by
    ``default_materials.py`` to clone a known-good palette into newly
    exported maps.

    Args:
        filepath: Path to a ``.a3d`` file.

    Returns:
        A list of 256 :class:`A3DMaterial` instances.

    Raises:
        ValueError: If the file does not start with the ``AS3D`` signature.

    .. note::
        The material count (256) and individual material size (512 bytes) are
        hardcoded to match the engine.  No EOF or size guard is performed, so
        a truncated file will produce silently incomplete materials.
    """
    # TODO(PIPELINE-FIX): No validation that the file is large enough to
    #     contain all 256 materials.  A truncated file yields partial/default
    #     MatCell data without any warning.
    materials = []

    with open(filepath, 'rb') as f:
        sig = f.read(4)
        if sig != A3D_SIGNATURE:
            raise ValueError("Invalid A3D file")

        header_size = struct.unpack('<I', f.read(4))[0]
        num_patches = struct.unpack('<I', f.read(4))[0]
        f.read(4)  # reserved

        # Skip terrain patches (188 bytes each)
        # WHY manual calculation: patch size = 8 (xy) + 8*8*2 (visual)
        #   + 5*5*2 (height) + 2 (diag) = 188
        patch_size = 8 + VISUAL_CELLS * VISUAL_CELLS * 2 + (HEIGHT_CELLS + 1) * (HEIGHT_CELLS + 1) * 2 + 2
        f.seek(header_size + num_patches * patch_size)

        # Read 256 materials (each 512 bytes)
        for _ in range(256):
            mat = A3DMaterial.read(f)
            materials.append(mat)

    return materials
