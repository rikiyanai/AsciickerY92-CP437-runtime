# AKM Mesh Import
# AKM files are PLY format with Asciicker-specific conventions
# [DEPENDENCY:BLENDER] [DATA-CONTRACT:AKM]

"""
AKM Mesh Importer -- Asciicker PLY to Blender Pipeline
========================================================

ARCHITECTURE:
    This module reads AKM files (ASCII or binary PLY) and reconstructs them as
    Blender mesh objects.  The parsing is split into three layers:

    1. **PLY spec objects** (``ObjectSpec``, ``ElementSpec``, ``PropertySpec``)
       -- A generic, reusable PLY header parser that builds a schema from the
       ``element`` / ``property`` declarations.

    2. ``read_ply(filepath)`` -- Opens the file, parses the header into spec
       objects, then reads the body (ASCII or binary) and returns a dict of
       element arrays.

    3. ``load_mesh(filepath, mesh_name)`` -- Consumes the parsed PLY data and
       creates a ``bpy.types.Mesh`` with vertices, faces, UV layers, and
       vertex color attributes.

    4. ``load(operator, context, filepath)`` -- Top-level entry point called
       by the import operator; wraps ``load_mesh`` and links the new object
       into the active collection.

KEY EXPORTS:
    - ``load()``       -- Operator-level entry point.
    - ``load_mesh()``  -- Returns a ``bpy.types.Mesh`` without linking it.
    - ``read_ply()``   -- Low-level PLY parser (format-agnostic).

PIPELINE CONTEXT:
    [DATA-CONTRACT:AKM] On import the module reconstructs:
      - Vertex positions (x, y, z).
      - Per-loop UVs (s, t) mapped to a UV layer.
      - Per-loop colors (red, green, blue, alpha) mapped to a Blender 4.x
        ``color_attributes`` FLOAT_COLOR layer (with Blender 3.x fallback to
        ``vertex_colors``).
      - The alpha channel carries the collision weight but is stored as a
        regular color component; it is not automatically decoded back into a
        vertex group on import.
        TODO(PIPELINE-FIX): round-trip the collision alpha back to a
        "collision" vertex group on import so re-export preserves collision
        data without manual artist intervention.
"""

import bpy
import re
import struct


class ElementSpec:
    """Schema for a single PLY ``element`` block (e.g. ``element vertex 1024``).

    Stores the element name, declared count, and an ordered list of
    ``PropertySpec`` objects that describe each column.
    """
    __slots__ = ("name", "count", "properties")

    def __init__(self, name, count):
        """Initialize with element *name* (bytes), expected row *count*, and empty property list."""
        self.name = name
        self.count = count
        self.properties = []

    def load(self, format_type, stream):
        """Read one row of this element from *stream* according to *format_type*.

        For ASCII PLY the stream is the open file (readline + split); for
        binary PLY the stream is the same file but read via ``struct.unpack``.

        Returns:
            A list of parsed property values (one per ``PropertySpec``).
        """
        if format_type == b'ascii':
            stream = stream.readline().split()
        return [p.load(format_type, stream) for p in self.properties]

    def index(self, name):
        """Return the positional index of the property named *name*, or -1."""
        for i, p in enumerate(self.properties):
            if p.name == name:
                return i
        return -1


class PropertySpec:
    """Schema for a single PLY property (scalar or list).

    Attributes:
        name:         Property name from the header (e.g. ``b'x'``, ``b'red'``).
        list_type:    ``struct`` format char for the list-length prefix, or
                      ``None`` for scalar properties.
        numeric_type: ``struct`` format char for element values (``'f'``,
                      ``'B'``, ``'i'``, etc.) or ``'s'`` for strings.
    """
    __slots__ = ("name", "list_type", "numeric_type")

    def __init__(self, name, list_type, numeric_type):
        """Initialize with property *name* (bytes), optional *list_type* prefix, and *numeric_type* format char."""
        self.name = name
        self.list_type = list_type
        self.numeric_type = numeric_type

    def read_format(self, format_type, count, num_type, stream):
        """Read *count* values of *num_type* from *stream* in ASCII or binary PLY format.

        [DATA-CONTRACT:AKM] Handles string, float, and integer PLY type codes.
        For ASCII streams, consumes and removes parsed tokens in-place.
        """
        if format_type == b'ascii':
            if num_type == 's':
                ans = []
                for i in range(count):
                    s = stream[i]
                    if len(s) >= 2 and s.startswith(b'"') and s.endswith(b'"'):
                        ans.append(s[1:-1])
                    else:
                        return None
                stream[:count] = []
                return ans

            mapper = float if num_type in ('f', 'd') else int
            ans = [mapper(x) for x in stream[:count]]
            stream[:count] = []
            return ans
        else:
            if num_type == 's':
                ans = []
                for _ in range(count):
                    fmt = format_type + 'i'
                    data = stream.read(struct.calcsize(fmt))
                    length = struct.unpack(fmt, data)[0]
                    fmt = '%s%is' % (format_type, length)
                    data = stream.read(struct.calcsize(fmt))
                    s = struct.unpack(fmt, data)[0]
                    ans.append(s[:-1])
                return ans
            else:
                fmt = '%s%i%s' % (format_type, count, num_type)
                data = stream.read(struct.calcsize(fmt))
                return struct.unpack(fmt, data)

    def load(self, format_type, stream):
        """Read one property value (scalar) or list from the stream.

        For list properties the length prefix is read first using
        ``list_type``, then that many values are read using ``numeric_type``.
        """
        if self.list_type is not None:
            count = int(self.read_format(format_type, 1, self.list_type, stream)[0])
            return self.read_format(format_type, count, self.numeric_type, stream)
        else:
            return self.read_format(format_type, 1, self.numeric_type, stream)[0]


class ObjectSpec:
    """Top-level PLY schema container -- holds all ``ElementSpec`` objects.

    After parsing the header, ``load()`` reads the entire body and returns a
    dict mapping element names to lists of row-tuples.
    """
    __slots__ = ("specs",)

    def __init__(self):
        """Initialize with an empty list of ``ElementSpec`` objects."""
        self.specs = []

    def load(self, format_type, stream):
        """Read the full PLY body and return ``{element_name: [rows...]}``."""
        return {
            i.name: [i.load(format_type, stream) for _ in range(i.count)]
            for i in self.specs
        }


def read_ply(filepath):
    """Parse a PLY file (ASCII or binary) and return structured data.

    Returns:
        A 3-tuple ``(obj_spec, obj_data, texture)``:
          - *obj_spec*:  ``ObjectSpec`` with the parsed header schema.
          - *obj_data*:  Dict mapping element names to row lists.
          - *texture*:   Bytes string from a ``comment TextureFile`` line,
            or empty bytes if absent.
        All three are ``None`` when the file cannot be parsed.
    """
    # WHY support both ASCII and binary: the exporter writes ASCII, but
    # third-party tools might produce binary PLY that artists want to import.
    format_specs = {
        b'binary_little_endian': '<',
        b'binary_big_endian': '>',
        b'ascii': b'ascii',
    }
    type_specs = {
        b'char': 'b', b'uchar': 'B',
        b'int8': 'b', b'uint8': 'B',
        b'int16': 'h', b'uint16': 'H',
        b'short': 'h', b'ushort': 'H',
        b'int': 'i', b'int32': 'i',
        b'uint': 'I', b'uint32': 'I',
        b'float': 'f', b'float32': 'f',
        b'float64': 'd', b'double': 'd',
        b'string': 's',
    }

    obj_spec = ObjectSpec()
    format_type = b''
    texture = b''

    with open(filepath, 'rb') as f:
        signature = f.readline()
        if not signature.startswith(b'ply'):
            print("Invalid PLY signature")
            return None, None, None

        valid_header = False
        for line in f:
            tokens = re.split(br'[ \r\n]+', line)
            if not tokens:
                continue

            if tokens[0] == b'end_header':
                valid_header = True
                break
            elif tokens[0] == b'comment':
                if len(tokens) >= 3 and tokens[1] == b'TextureFile':
                    texture = tokens[2]
            elif tokens[0] == b'format':
                if len(tokens) >= 3 and tokens[1] in format_specs:
                    format_type = tokens[1]
            elif tokens[0] == b'element':
                if len(tokens) >= 3:
                    obj_spec.specs.append(ElementSpec(tokens[1], int(tokens[2])))
            elif tokens[0] == b'property':
                if obj_spec.specs:
                    if tokens[1] == b'list':
                        obj_spec.specs[-1].properties.append(
                            PropertySpec(tokens[4], type_specs[tokens[2]], type_specs[tokens[3]])
                        )
                    else:
                        obj_spec.specs[-1].properties.append(
                            PropertySpec(tokens[2], None, type_specs[tokens[1]])
                        )

        if not valid_header:
            print("Invalid PLY header")
            return None, None, None

        obj = obj_spec.load(format_specs[format_type], f)

    return obj_spec, obj, texture


def load_mesh(filepath, mesh_name):
    """Load PLY data into a new ``bpy.types.Mesh`` and return it.

    [DATA-CONTRACT:AKM] Vertex color handling:
      - If the PLY contains ``uchar`` color channels, values are divided by
        255 to convert to Blender's 0..1 float range.
      - If ``float`` channels are present they are used as-is.
      - The alpha channel (collision weight) is stored as the 4th color
        component.  It is *not* decoded back into a vertex group here.
        TODO(PIPELINE-FIX): reconstruct the ``collision`` vertex group from
        alpha to enable lossless round-tripping.

    Args:
        filepath:  Absolute path to the ``.akm`` / ``.ply`` file.
        mesh_name: Name for the new ``bpy.types.Mesh`` data-block.

    Returns:
        The created ``bpy.types.Mesh``, or ``None`` on parse failure.
    """
    obj_spec, obj, texture = read_ply(filepath)
    if obj is None:
        return None

    # Resolve property indices from the parsed PLY header.  Each index is a
    # column offset into the per-vertex row returned by ElementSpec.load().
    vindices = colindices = uvindices = None
    colmultiply = None

    for el in obj_spec.specs:
        if el.name == b'vertex':
            vindices = (el.index(b'x'), el.index(b'y'), el.index(b'z'))
            uvindices = (el.index(b's'), el.index(b't'))
            if -1 in uvindices:
                uvindices = None

            # WHY check alpha separately: AKM files encode collision weight
            # in the alpha channel.  Standard PLY files may omit it.
            # [DATA-CONTRACT:AKM]
            alpha_idx = el.index(b'alpha')
            if alpha_idx == -1:
                colindices = (el.index(b'red'), el.index(b'green'), el.index(b'blue'))
            else:
                colindices = (el.index(b'red'), el.index(b'green'), el.index(b'blue'), alpha_idx)

            if -1 in colindices[:3]:
                colindices = None
            elif colindices:
                # WHY conditional multiply: uchar channels (0..255) need
                # scaling to Blender's 0..1 range, while float channels
                # are already normalized.
                colmultiply = [
                    1.0 if el.properties[i].numeric_type in ('f', 'd') else (1.0 / 255.0)
                    for i in colindices
                ]

        elif el.name == b'face':
            findex = el.index(b'vertex_indices')
        elif el.name == b'edge':
            # TODO(PIPELINE-FIX): eindex1/eindex2 are only set when the PLY
            # header declares an ``edge`` element.  If the body contains
            # ``b'edge'`` data without a matching header element, these
            # variables will be unbound and raise NameError at line ~365.
            eindex1, eindex2 = el.index(b'vertex1'), el.index(b'vertex2')

    # WHY: Blender stores UVs and vertex colors per-loop (i.e. per face-corner),
    # not per-vertex.  The PLY format stores them per-vertex, so the loop below
    # expands per-vertex attributes into per-loop arrays by iterating face
    # indices and duplicating the vertex attributes for each loop.
    mesh_faces = []
    mesh_uvs = []
    mesh_colors = []

    verts = obj[b'vertex']

    if b'face' in obj:
        for f in obj[b'face']:
            indices = f[findex]
            mesh_faces.append(indices)

            if uvindices:
                mesh_uvs.extend([
                    (verts[i][uvindices[0]], verts[i][uvindices[1]])
                    for i in indices
                ])

            if colindices:
                for i in indices:
                    if len(colindices) == 3:
                        mesh_colors.append((
                            verts[i][colindices[0]] * colmultiply[0],
                            verts[i][colindices[1]] * colmultiply[1],
                            verts[i][colindices[2]] * colmultiply[2],
                            1.0
                        ))
                    else:
                        mesh_colors.append((
                            verts[i][colindices[0]] * colmultiply[0],
                            verts[i][colindices[1]] * colmultiply[1],
                            verts[i][colindices[2]] * colmultiply[2],
                            verts[i][colindices[3]] * colmultiply[3],
                        ))

    # Create Blender mesh
    mesh = bpy.data.meshes.new(name=mesh_name)

    # WHY: No coordinate-system transform (e.g. Y-up to Z-up) is applied here.
    # The AKM exporter already writes coordinates in Blender's native Z-up
    # right-handed system, so positions are loaded verbatim.
    # [DATA-CONTRACT:AKM]
    mesh.vertices.add(len(verts))
    mesh.vertices.foreach_set("co", [
        c for v in verts for c in (v[vindices[0]], v[vindices[1]], v[vindices[2]])
    ])

    # Add edges if present
    if b'edge' in obj:
        mesh.edges.add(len(obj[b'edge']))
        mesh.edges.foreach_set("vertices", [
            v for e in obj[b'edge'] for v in (e[eindex1], e[eindex2])
        ])

    # WHY: Blender's mesh API requires three parallel arrays -- loop vertex
    # indices, polygon loop-start offsets, and polygon loop-totals -- rather
    # than a simple list of face-index tuples.  This flattening is mandatory
    # for ``foreach_set`` performance; using ``mesh.from_pydata`` would also
    # work but is slower for large meshes.
    # [DEPENDENCY:BLENDER] foreach_set is a C-level bulk setter available
    # since Blender 2.80.
    if mesh_faces:
        loops_vert_idx = []
        faces_loop_start = []
        faces_loop_total = []
        lidx = 0

        for f in mesh_faces:
            loops_vert_idx.extend(f)
            faces_loop_start.append(lidx)
            faces_loop_total.append(len(f))
            lidx += len(f)

        mesh.loops.add(len(loops_vert_idx))
        mesh.polygons.add(len(mesh_faces))
        mesh.loops.foreach_set("vertex_index", loops_vert_idx)
        mesh.polygons.foreach_set("loop_start", faces_loop_start)
        mesh.polygons.foreach_set("loop_total", faces_loop_total)

        # Add UVs
        if uvindices and mesh_uvs:
            uv_layer = mesh.uv_layers.new()
            for i, uv in enumerate(uv_layer.data):
                uv.uv = mesh_uvs[i]

        # Add vertex colors.  [DEPENDENCY:BLENDER] Blender 4.x replaced
        # ``vertex_colors`` with ``color_attributes`` (FLOAT_COLOR in linear
        # space, domain='CORNER' = per-loop storage matching the PLY layout).
        if colindices and mesh_colors:
            if hasattr(mesh, 'color_attributes'):
                # Blender 4.x -- FLOAT_COLOR, per-loop (CORNER) domain
                vcol = mesh.color_attributes.new(
                    name="Color",
                    type='FLOAT_COLOR',
                    domain='CORNER'
                )
                for i, col in enumerate(mesh_colors):
                    vcol.data[i].color = col
            else:
                # Blender 3.x fallback
                vcol = mesh.vertex_colors.new()
                for i, col in enumerate(vcol.data):
                    col.color = mesh_colors[i]

    mesh.update()
    mesh.validate()

    return mesh


def load(operator, context, filepath):
    """Top-level AKM import entry point called by the Blender operator.

    Creates the mesh via ``load_mesh()``, wraps it in a new object, and links
    it into the active collection.  The new object becomes the active
    selection so the artist can immediately inspect it.

    Args:
        operator: The calling ``bpy.types.Operator`` (for ``report()``).
        context:  Current Blender context.
        filepath: Absolute path to the ``.akm`` file.

    Returns:
        ``{'FINISHED'}`` on success, ``{'CANCELLED'}`` on parse failure.
    """
    import os
    import time

    t = time.time()
    mesh_name = bpy.path.display_name_from_filepath(filepath)

    mesh = load_mesh(filepath, mesh_name)
    if not mesh:
        operator.report({'ERROR'}, f"Failed to load {filepath}")
        return {'CANCELLED'}

    obj = bpy.data.objects.new(mesh_name, mesh)
    context.collection.objects.link(obj)
    context.view_layer.objects.active = obj
    obj.select_set(True)

    print(f"Imported {filepath!r} in {time.time() - t:.3f} sec")
    return {'FINISHED'}
