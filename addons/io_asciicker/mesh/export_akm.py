# AKM Mesh Export
# Exports to PLY format with Asciicker conventions
# [DEPENDENCY:BLENDER] [DATA-CONTRACT:AKM]

"""
AKM Mesh Exporter -- Blender to Asciicker PLY Pipeline
========================================================

ARCHITECTURE:
    This module serializes one or more Blender mesh objects into a single
    ASCII PLY file that conforms to the Asciicker engine's AKM mesh contract.
    The pipeline is:

        1. ``save()``        -- Entry point called by the operator.  Merges
           selected objects into one bmesh, triangulates, applies the global
           axis-conversion matrix, validates colors, then delegates to
           ``save_mesh()``.
        2. ``save_mesh()``   -- Low-level writer.  Deduplicates vertices by
           their (normal, uv, color) key, writes the PLY header + vertex /
           face / edge sections.
        3. ``validate_colors()`` -- Pre-flight check that warns when vertex
           colors fall outside the engine's 216-color terminal-safe palette.

KEY EXPORTS:
    - ``save()``             -- High-level export (operator entry point).
    - ``save_mesh()``        -- Write a prepared Blender mesh to disk as PLY.
    - ``validate_colors()``  -- Palette conformance checker.
    - ``SAFE_LEVELS``        -- The six per-channel values (0-255) that form
      the 6x6x6 = 216 terminal-safe color cube.

PIPELINE CONTEXT:
    [DATA-CONTRACT:AKM] The output file is standard ASCII PLY with these
    Asciicker-specific conventions:

    * **Collision alpha** -- The ``alpha`` vertex property stores a collision
      weight read from the ``collision`` vertex group (0 = solid wall,
      255 = fully passthrough).  The C++ physics code in ``physics.cpp``
      reads this value to decide per-triangle collision behavior.

    * **Freestyle face marks** -- Faces whose ``use_freestyle_mark`` flag is
      set are written with a *negative* vertex count.  The engine renderer
      interprets these as wireframe / debug overlays.

    * **Freestyle edge marks** -- Edges with ``use_freestyle_mark`` are
      appended as degenerate 2-vertex faces after the regular face list.

    * **Triangulation** -- ``bmesh.ops.triangulate`` is always applied.  The
      C++ loader assumes every face has exactly 3 vertices.

    * **.blend sidecar** -- (REMOVED) Previously saved a ``.blend`` copy
      alongside each ``.akm`` file.  Disabled because batch pipelines
      (e.g. OSM export of 200+ buildings) would create 200+ sidecar
      files at ~160 MB each, causing a 33 GB+ disk bomb.
"""

import bpy
import bmesh


# WHY these specific values: the Asciicker terminal renderer quantizes vertex
# colors to a 6x6x6 color cube (216 colors).  Each channel may only take one
# of these six levels.  Colors outside the set will require dithering at
# render time, which the engine supports but which degrades visual fidelity.
# [DATA-CONTRACT:AKM]
SAFE_LEVELS = {0, 51, 102, 153, 204, 255}


def validate_colors(mesh, obj):
    """Check vertex colors against the engine's terminal-safe palette.

    Iterates every vertex/loop color and counts how many fall outside the
    ``SAFE_LEVELS`` set.  Each off-palette color will need runtime dithering
    in the C++ renderer, so this function reports a percentage warning.

    [DATA-CONTRACT:AKM] The engine's 216-color cube requires each of R, G, B
    to be one of {0, 51, 102, 153, 204, 255}.

    Args:
        mesh: A ``bpy.types.Mesh`` (already evaluated / triangulated).
        obj:  The source ``bpy.types.Object`` (used only for context here).

    Returns:
        A list of human-readable warning strings (empty if all colors are
        palette-safe or if no color layer exists).
    """
    warnings = []

    # Blender 4.x uses color_attributes; 3.x uses vertex_colors (legacy).
    if hasattr(mesh, 'color_attributes') and mesh.color_attributes:
        vcol = mesh.color_attributes.active
        if vcol:
            dither_count = 0
            total = len(vcol.data)
            is_byte = (vcol.data_type == 'BYTE_COLOR')

            for item in vcol.data:
                # Blender 4.x: BYTE_COLOR has color_srgb, FLOAT_COLOR has color
                if is_byte and hasattr(item, 'color_srgb'):
                    color = item.color_srgb
                elif hasattr(item, 'color'):
                    color = item.color
                else:
                    continue

                r = int(color[0] * 255)
                g = int(color[1] * 255)
                b = int(color[2] * 255)

                if r not in SAFE_LEVELS or g not in SAFE_LEVELS or b not in SAFE_LEVELS:
                    dither_count += 1

            if dither_count > 0:
                pct = (dither_count / total) * 100
                warnings.append(f"{dither_count}/{total} ({pct:.0f}%) colors will need dithering")
    elif hasattr(mesh, 'vertex_colors') and mesh.vertex_colors:
        # Blender 3.x fallback
        vcol = mesh.vertex_colors.active.data
        dither_count = 0
        total = len(vcol)

        for loop_color in vcol:
            r = int(loop_color.color[0] * 255)
            g = int(loop_color.color[1] * 255)
            b = int(loop_color.color[2] * 255)

            if r not in SAFE_LEVELS or g not in SAFE_LEVELS or b not in SAFE_LEVELS:
                dither_count += 1

        if dither_count > 0:
            pct = (dither_count / total) * 100
            warnings.append(f"{dither_count}/{total} ({pct:.0f}%) colors will need dithering")
    else:
        warnings.append("No vertex colors - mesh will use default shading")

    return warnings


def save_mesh(filepath, mesh, obj, use_normals=True, use_uv_coords=True, use_colors=True):
    """Write a Blender mesh to an ASCII PLY file with AKM conventions.

    [DATA-CONTRACT:AKM] Vertex deduplication strategy:
        Each Blender vertex may appear in multiple faces with different
        normals, UVs, or colors.  We build a per-vertex dictionary keyed by
        ``(normal_key, uv_key, color_tuple)`` so that identical attribute
        combinations share a single PLY vertex index, while splits (e.g. at
        hard edges or UV seams) produce distinct PLY vertices.

    Args:
        filepath:       Destination ``.akm`` path.
        mesh:           Evaluated ``bpy.types.Mesh`` (already triangulated).
        obj:            Source object -- used to read the ``collision`` vertex
                        group for the alpha channel.
        use_normals:    Write ``nx ny nz`` per vertex.
        use_uv_coords:  Write ``s t`` per vertex.
        use_colors:     Write ``red green blue alpha`` per vertex.

    Returns:
        ``{'FINISHED'}`` on success.
    """
    import os

    # WHY round to 6 decimals: keeps the ASCII PLY compact while preserving
    # more precision than the engine's fixed-point terrain grid needs.
    def rvec3d(v):
        """Round a 3D vector to 6 decimal places for PLY dedup keys."""
        return round(v[0], 6), round(v[1], 6), round(v[2], 6)

    def rvec2d(v):
        """Round a 2D vector to 6 decimal places for PLY dedup keys."""
        return round(v[0], 6), round(v[1], 6)

    # Get UV layer
    if use_uv_coords and mesh.uv_layers:
        active_uv_layer = mesh.uv_layers.active.data
    else:
        use_uv_coords = False

    # Get vertex colors (Blender 4.x compatible).
    # WHY the three-tier fallback: Blender 4.x renamed the color API from
    # ``vertex_colors`` to ``color_attributes`` and introduced two storage
    # types (BYTE_COLOR in sRGB, FLOAT_COLOR in linear).  We try, in order:
    #   1) The active render color attribute.
    #   2) A layer named "Col" (our gmap_bake_tools output convention).
    #   3) The first attribute whose type we understand.
    # [DEPENDENCY:BLENDER] Blender 3.x fallback via ``vertex_colors``.
    active_col_layer = None
    is_byte_color = False
    if use_colors:
        if hasattr(mesh, 'color_attributes') and mesh.color_attributes:
            # Priority: 1) active_color, 2) "Col" layer, 3) first valid layer
            attr = mesh.color_attributes.active_color
            if attr and attr.data_type in ('BYTE_COLOR', 'FLOAT_COLOR'):
                active_col_layer = attr
                is_byte_color = (attr.data_type == 'BYTE_COLOR')
            else:
                # Try "Col" layer specifically (our bake target)
                attr = mesh.color_attributes.get("Col")
                if attr and attr.data_type in ('BYTE_COLOR', 'FLOAT_COLOR'):
                    active_col_layer = attr
                    is_byte_color = (attr.data_type == 'BYTE_COLOR')
                else:
                    # Fallback to first valid layer
                    for attr in mesh.color_attributes:
                        if attr.data_type in ('BYTE_COLOR', 'FLOAT_COLOR'):
                            active_col_layer = attr
                            is_byte_color = (attr.data_type == 'BYTE_COLOR')
                            break
            use_colors = active_col_layer is not None
        elif hasattr(mesh, 'vertex_colors') and mesh.vertex_colors:
            active_col_layer = mesh.vertex_colors.active.data
        else:
            use_colors = False

    mesh_verts = mesh.vertices
    # WHY per-vertex dict: each Blender vertex index maps to a dict of
    # (normal_key, uv_key, color) -> PLY vertex index.  This deduplicates
    # vertices that share all attributes while splitting those that differ
    # (e.g. at UV seams or hard-edge normals).
    vdict = [{} for _ in range(len(mesh_verts))]
    ply_verts = []
    ply_faces = [[] for _ in range(len(mesh.polygons))]
    vert_count = 0

    # Collect freestyle-marked edges -- these become degenerate 2-vert faces
    # appended after the regular face list.  [DATA-CONTRACT:AKM]
    edges = []
    for e in mesh.edges:
        if e.use_freestyle_mark:
            edges.append([e.vertices[0], e.vertices[1]])

    color = uvcoord = uvcoord_key = normal = normal_key = None

    for i, f in enumerate(mesh.polygons):
        smooth = not use_normals or f.use_smooth
        if not smooth:
            normal = f.normal[:]
            normal_key = rvec3d(normal)

        if use_uv_coords:
            uv = [active_uv_layer[l].uv[:] for l in range(f.loop_start, f.loop_start + f.loop_total)]

        if use_colors:
            if hasattr(active_col_layer, 'data'):
                # color_attributes style (Blender 4.x)
                col = []
                for l in range(f.loop_start, f.loop_start + f.loop_total):
                    item = active_col_layer.data[l]
                    if is_byte_color and hasattr(item, 'color_srgb'):
                        col.append(item.color_srgb[:])
                    else:
                        col.append(item.color[:])
            else:
                # vertex_colors style (Blender 3.x)
                col = [active_col_layer[l].color[:] for l in range(f.loop_start, f.loop_start + f.loop_total)]

        pf = ply_faces[i]

        # WHY negative count: freestyle-marked faces are written with a
        # negative vertex count so the C++ renderer can distinguish them
        # from regular geometry (used for wireframe / debug overlays).
        # [DATA-CONTRACT:AKM]
        if f.use_freestyle_mark:
            pf.append(-len(f.vertices))
        else:
            pf.append(len(f.vertices))

        for j, vidx in enumerate(f.vertices):
            v = mesh_verts[vidx]

            if smooth:
                normal = v.normal[:]
                normal_key = rvec3d(normal)

            if use_uv_coords:
                uvcoord = uv[j][0], uv[j][1]
                uvcoord_key = rvec2d(uvcoord)

            if use_colors:
                # WHY vertex-group weight -> alpha: the engine's physics
                # system reads the PLY alpha channel to decide per-triangle
                # collision behavior.  0 = fully solid wall, 255 = fully
                # passthrough (e.g. vegetation, fences).
                # [DATA-CONTRACT:AKM] maps to physics.cpp collision checks.
                weight = 0.0

                # Look for 'collision' group specifically
                vg_index = -1
                if "collision" in obj.vertex_groups:
                    vg_index = obj.vertex_groups["collision"].index

                if vg_index != -1:
                    for g in v.groups:
                        if g.group == vg_index:
                            weight = g.weight
                            break
                elif obj.vertex_groups.active:
                    # TODO(PIPELINE-FIX): falling back to the active group
                    # is fragile -- if the artist has a paint-weight group
                    # selected, this silently encodes the wrong collision
                    # data.  Consider requiring the "collision" group
                    # explicitly and warning when it is absent.
                    for g in v.groups:
                        if g.group == obj.vertex_groups.active.index:
                            weight = g.weight
                            break

                c = col[j]
                color = (
                    int(round(c[0] * 255.0)),
                    int(round(c[1] * 255.0)),
                    int(round(c[2] * 255.0)),
                    int(round(weight * 255.0)),
                )

            key = normal_key, uvcoord_key, color
            vdict_local = vdict[vidx]
            pf_vidx = vdict_local.get(key)

            if pf_vidx is None:
                pf_vidx = vdict_local[key] = vert_count
                ply_verts.append((vidx, normal, uvcoord, color))
                vert_count += 1

            pf.append(pf_vidx)

    # WHY reindex edges: freestyle-marked edges collected above still carry
    # original Blender vertex indices (mesh_verts-relative).  We must map
    # them through the same dedup dict (vdict) so the PLY file references
    # the correct deduplicated vertex indices.  Without this, edge vertex
    # indices would point to non-existent or wrong PLY vertices.
    for e in edges:
        for idx in range(2):
            vidx = e[idx]
            v = mesh_verts[vidx]

            normal = v.normal[:]
            normal_key = rvec3d(normal)

            # TODO(PIPELINE-FIX): checking 'uv' in dir() is fragile -- it
            # relies on the local variable 'uv' having been set by the face
            # loop above.  If the mesh has UVs but zero faces, 'uv' will be
            # undefined.  Consider initializing uv=None before the face loop
            # and checking explicitly.
            if use_uv_coords and 'uv' in dir():
                uvcoord = uv[0][0], uv[0][1]
                uvcoord_key = rvec2d(uvcoord)
            else:
                uvcoord = None
                uvcoord_key = None

            # TODO(PIPELINE-FIX): same 'in dir()' fragility as the UV check
            # above, and same active-group fallback issue as the face loop
            # (see TODO at line ~296).  Edge collision weights should use
            # the same explicit "collision" group logic without the fallback.
            if use_colors and 'col' in dir():
                weight = 0.0

                # Look for 'collision' group specifically
                vg_index = -1
                if "collision" in obj.vertex_groups:
                    vg_index = obj.vertex_groups["collision"].index
                
                if vg_index != -1:
                    for g in v.groups:
                        if g.group == vg_index:
                            weight = g.weight
                            break
                elif obj.vertex_groups.active:
                    # Fallback
                    for g in v.groups:
                        if g.group == obj.vertex_groups.active.index:
                            weight = g.weight
                            break
                c = col[0]
                color = (
                    int(round(c[0] * 255.0)),
                    int(round(c[1] * 255.0)),
                    int(round(c[2] * 255.0)),
                    int(round(weight * 255.0)),
                )
            else:
                color = None

            key = normal_key, uvcoord_key, color
            vdict_local = vdict[vidx]
            pf_vidx = vdict_local.get(key)

            if pf_vidx is None:
                pf_vidx = vdict_local[key] = vert_count
                ply_verts.append((vidx, normal, uvcoord, color))
                vert_count += 1

            e[idx] = pf_vidx

    # Write ASCII PLY file.  [DATA-CONTRACT:AKM] The engine's C++ PLY
    # loader expects ``format ascii 1.0`` -- binary PLY is not supported.
    with open(filepath, "w", encoding="utf-8", newline="\n") as f:
        fw = f.write

        # Header
        fw("ply\n")
        fw("format ascii 1.0\n")
        fw(f"comment Created by Blender {bpy.app.version_string} - Asciicker AKM Export\n")

        # [DATA-CONTRACT:AKM] Vertex property order must match the C++
        # PLY loader's expected layout: xyz, [normals], [uvs], [rgba].
        fw(f"element vertex {len(ply_verts)}\n")
        fw("property float x\n")
        fw("property float y\n")
        fw("property float z\n")

        if use_normals:
            fw("property float nx\n")
            fw("property float ny\n")
            fw("property float nz\n")

        if use_uv_coords:
            fw("property float s\n")
            fw("property float t\n")

        if use_colors:
            fw("property uchar red\n")
            fw("property uchar green\n")
            fw("property uchar blue\n")
            fw("property uchar alpha\n")

        fw(f"element face {len(mesh.polygons) + len(edges)}\n")
        fw("property list uchar uint vertex_indices\n")
        fw("end_header\n")

        # Vertices
        # WHY different precisions: positions use %.3f (millimeter-level for
        # typical game scale), normals use %.2f (direction vectors tolerate
        # lower precision), UVs use %.3f, and colors are integer 0-255.
        # These choices keep the ASCII PLY compact.  [DATA-CONTRACT:AKM]
        for v in ply_verts:
            fw("%.3f %.3f %.3f" % mesh_verts[v[0]].co[:])
            if use_normals and v[1]:
                fw(" %.2f %.2f %.2f" % v[1])
            if use_uv_coords and v[2]:
                fw(" %.3f %.3f" % v[2])
            if use_colors and v[3]:
                fw(" %u %u %u %u" % v[3])
            fw("\n")

        # Faces
        for pf in ply_faces:
            for v in pf:
                fw(f"{v} ")
            fw("\n")

        # Edges as degenerate 2-vertex "faces".  [DATA-CONTRACT:AKM]
        # The engine treats face entries with exactly 2 indices as edges.
        for e in edges:
            fw(f"2 {e[0]} {e[1]}\n")

    print(f"Exported {filepath}")
    return {'FINISHED'}


def save(operator, context, filepath="", use_selection=False, use_mesh_modifiers=True,
         use_normals=True, use_uv_coords=True, use_colors=True, global_matrix=None,
         apply_world_transform=False, objects=None):
    """High-level AKM export entry point.

    Merges all eligible mesh objects into a single bmesh, triangulates,
    applies the global axis-conversion matrix, validates palette compliance,
    then writes the result via ``save_mesh()``.

    [DATA-CONTRACT:AKM] The output is always fully triangulated because the
    C++ engine's PLY loader does not support n-gons.

    Args:
        operator:              The calling Blender operator (for ``report()``).
        context:               Current Blender context.
        filepath:              Destination ``.akm`` file path.
        use_selection:         If True, export only selected objects.
        use_mesh_modifiers:    If True, evaluate modifiers before export.
        use_normals:           Include per-vertex normals.
        use_uv_coords:         Include UV coordinates.
        use_colors:            Include vertex colors + collision alpha.
        global_matrix:         4x4 axis-conversion matrix (may be ``None``).
        apply_world_transform: Bake each object's world matrix into vertices.
        objects:               Explicit object list (overrides selection logic).

    Returns:
        ``{'FINISHED'}`` or ``{'CANCELLED'}``.
    """

    # Exit edit mode if active -- mesh evaluation requires OBJECT mode.
    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode='OBJECT')

    if objects is not None:
        obs = [o for o in objects if o and o.type == 'MESH']
    elif use_selection:
        obs = context.selected_objects
    else:
        obs = context.scene.objects

    depsgraph = context.evaluated_depsgraph_get()
    bm = bmesh.new()

    for ob in obs:
        if ob.type != 'MESH':
            continue

        if use_mesh_modifiers:
            ob_eval = ob.evaluated_get(depsgraph)
        else:
            ob_eval = ob

        try:
            me = ob_eval.to_mesh()
        except RuntimeError:
            continue

        if apply_world_transform:
            me.transform(ob.matrix_world)
        bm.from_mesh(me)
        ob_eval.to_mesh_clear()

    mesh = bpy.data.meshes.new("TMP_AKM_EXPORT")

    # WHY triangulate here: the Asciicker C++ PLY loader assumes every face
    # has exactly 3 vertices.  Triangulating in Blender avoids a second pass
    # on the engine side and gives artists control over the tessellation.
    # [DATA-CONTRACT:AKM]
    bmesh.ops.triangulate(bm, faces=bm.faces)

    bm.to_mesh(mesh)
    bm.free()

    if global_matrix is not None:
        mesh.transform(global_matrix)

    if use_normals:
        # [DEPENDENCY:BLENDER] Blender 4.x removed calc_normals(), normals
        # are auto-calculated.  This no-op block kept for clarity / compat.
        pass

    obj = None
    if obs:
        if context.active_object in obs:
            obj = context.active_object
        else:
            obj = obs[0]
    else:
        obj = context.active_object

    # Validate and warn about colors.
    # WHY force-enable colors when collision group exists: the collision weight
    # is packed into the PLY alpha channel alongside vertex colors.  If the
    # artist has a "collision" vertex group, we *must* emit color data even if
    # the user unchecked "Vertex Colors" in the export dialog, otherwise the
    # engine will not receive collision information.  [DATA-CONTRACT:AKM]
    has_collision = "collision" in obj.vertex_groups

    if use_colors or has_collision:
        use_colors = True  # Force enable if collision data is needed
        warnings = validate_colors(mesh, obj)
        for w in warnings:
            print(f"AKM Export Warning: {w}")
            operator.report({'WARNING'}, w)

    ret = save_mesh(
        filepath, mesh, obj,
        use_normals=use_normals,
        use_uv_coords=use_uv_coords,
        use_colors=use_colors,
    )

    bpy.data.meshes.remove(mesh)

    return ret
