# Asciicker A3D Map Format Definitions
# Binary format for terrain, materials, instances, and enemy generators

import struct

# Constants from terrain.h
HEIGHT_SCALE = 16
HEIGHT_CELLS = 4
VISUAL_CELLS = 8

# File signature
A3D_SIGNATURE = b'AS3D'


class A3DHeader:
    """32-byte file header"""
    SIZE = 16  # Actually 16 bytes based on terrain.cpp

    def __init__(self, num_patches=0):
        self.file_sign = A3D_SIGNATURE
        self.header_size = self.SIZE
        self.num_patches = num_patches
        self.reserved = 0

    def write(self, f):
        f.write(self.file_sign)
        f.write(struct.pack('<I', self.header_size))
        f.write(struct.pack('<I', self.num_patches))
        f.write(struct.pack('<I', self.reserved))


class A3DPatch:
    """Terrain patch data - 188 bytes"""
    # visual[8][8] = 128 bytes
    # height[5][5] = 50 bytes
    # diag = 2 bytes
    # x, y = 8 bytes
    # Total = 188 bytes

    def __init__(self, x=0, y=0):
        self.x = x
        self.y = y
        self.visual = [[0] * VISUAL_CELLS for _ in range(VISUAL_CELLS)]  # 8x8 uint16
        self.height = [[0] * (HEIGHT_CELLS + 1) for _ in range(HEIGHT_CELLS + 1)]  # 5x5 uint16
        self.diag = 0

    def write(self, f):
        # Write x, y coordinates
        f.write(struct.pack('<ii', self.x, self.y))

        # Write visual map (8x8 uint16)
        for row in self.visual:
            for val in row:
                f.write(struct.pack('<H', val & 0xFFFF))

        # Write height map (5x5 uint16)
        for row in self.height:
            for val in row:
                f.write(struct.pack('<H', val & 0xFFFF))

        # Write diag
        f.write(struct.pack('<H', self.diag))


class MatCell:
    """Material cell - 8 bytes"""
    SIZE = 8

    def __init__(self, fg=(0, 0, 0), gl=0, bg=(0, 0, 0), flags=0):
        self.fg = fg  # (r, g, b) foreground
        self.gl = gl  # glyph
        self.bg = bg  # (r, g, b) background
        self.flags = flags

    def write(self, f):
        f.write(struct.pack('BBB', self.fg[0], self.fg[1], self.fg[2]))
        f.write(struct.pack('B', self.gl))
        f.write(struct.pack('BBB', self.bg[0], self.bg[1], self.bg[2]))
        f.write(struct.pack('B', self.flags))

    @classmethod
    def read(cls, f):
        data = f.read(8)
        if len(data) < 8:
            return None
        fg = (data[0], data[1], data[2])
        gl = data[3]
        bg = (data[4], data[5], data[6])
        flags = data[7]
        return cls(fg, gl, bg, flags)


class A3DMaterial:
    """Material with 4x16 shade ramp - 512 bytes"""
    SIZE = 4 * 16 * MatCell.SIZE  # 512 bytes

    def __init__(self):
        # shade[4][16] - 4 elevation ramps, 16 shades each
        self.shade = [[MatCell() for _ in range(16)] for _ in range(4)]

    def write(self, f):
        for ramp in self.shade:
            for cell in ramp:
                cell.write(f)

    @classmethod
    def read(cls, f):
        mat = cls()
        for i in range(4):
            for j in range(16):
                cell = MatCell.read(f)
                if cell:
                    mat.shade[i][j] = cell
        return mat


class A3DInstance:
    """Mesh instance with transform"""

    def __init__(self, mesh_name="", inst_name="", transform=None, flags=0, story_id=0):
        self.mesh_name = mesh_name
        self.inst_name = inst_name
        # 4x4 transform matrix as flat list of 16 doubles
        self.transform = transform if transform else [
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0
        ]
        self.flags = flags
        self.story_id = story_id

    def write(self, f):
        # Mesh name
        mesh_bytes = self.mesh_name.encode('utf-8')
        f.write(struct.pack('<i', len(mesh_bytes)))
        if mesh_bytes:
            f.write(mesh_bytes)

        # Instance name
        inst_bytes = self.inst_name.encode('utf-8') if self.inst_name else b''
        f.write(struct.pack('<i', len(inst_bytes)))
        if inst_bytes:
            f.write(inst_bytes)

        # Transform matrix (16 doubles = 128 bytes)
        for val in self.transform:
            f.write(struct.pack('<d', float(val)))

        # Flags and story_id
        f.write(struct.pack('<ii', self.flags, self.story_id))


class A3DEnemyGen:
    """Enemy generator - 44 bytes"""
    SIZE = 44

    def __init__(self):
        self.pos = [0.0, 0.0, 0.0]
        self.alive_max = 1
        self.revive_min = 0
        self.revive_max = 0
        self.armor = 0
        self.helmet = 0
        self.shield = 0
        self.sword = 0
        self.crossbow = 0

    def write(self, f):
        f.write(struct.pack('<fff', self.pos[0], self.pos[1], self.pos[2]))
        f.write(struct.pack('<iiiiiiiii',
                            self.alive_max, self.revive_min, self.revive_max,
                            self.armor, self.helmet, self.shield,
                            self.sword, self.crossbow, 0))  # Extra padding to match 44 bytes


def write_a3d_file(filepath, patches, materials, instances, enemy_gens=None):
    """Write complete A3D file"""
    if enemy_gens is None:
        enemy_gens = []

    with open(filepath, 'wb') as f:
        # 1. Write header
        header = A3DHeader(len(patches))
        header.write(f)

        # 2. Write terrain patches
        for patch in patches:
            patch.write(f)

        # 3. Write materials (256 entries)
        for i in range(256):
            if i < len(materials):
                materials[i].write(f)
            else:
                A3DMaterial().write(f)

        # 4. Write world header and instances
        f.write(struct.pack('<i', -1))  # format_version = -1
        f.write(struct.pack('<i', len(instances)))

        for inst in instances:
            inst.write(f)

        # 5. Write enemy generators
        f.write(struct.pack('<i', len(enemy_gens)))
        for gen in enemy_gens:
            gen.write(f)


def read_materials_from_a3d(filepath):
    """Extract materials from existing A3D file for reuse"""
    materials = []

    with open(filepath, 'rb') as f:
        # Read header
        sig = f.read(4)
        if sig != A3D_SIGNATURE:
            raise ValueError("Invalid A3D file")

        header_size = struct.unpack('<I', f.read(4))[0]
        num_patches = struct.unpack('<I', f.read(4))[0]
        f.read(4)  # reserved

        # Skip terrain patches
        # Each patch: 8 (xy) + 128 (visual) + 50 (height) + 2 (diag) = 188 bytes
        patch_size = 8 + VISUAL_CELLS * VISUAL_CELLS * 2 + (HEIGHT_CELLS + 1) * (HEIGHT_CELLS + 1) * 2 + 2
        f.seek(header_size + num_patches * patch_size)

        # Read 256 materials
        for _ in range(256):
            mat = A3DMaterial.read(f)
            materials.append(mat)

    return materials
