#!/usr/bin/env python3
"""
picocad_to_akm.py -- Convert picoCAD2 GLTF export to Asciicker AKM mesh.

Converts a picoCAD2 GLTF (.gltf + .bin + .png) to a valid Asciicker AKM
(ASCII PLY) file with palette-safe vertex colors. No Blender required.

USAGE:
    python3 scripts/picocad_to_akm.py model.gltf
    python3 scripts/picocad_to_akm.py model.gltf --output assets/meshes/prop.akm
    python3 scripts/picocad_to_akm.py model.gltf --merge
    python3 scripts/picocad_to_akm.py --audit some_mesh.akm

PIPELINE:
    1. Parse GLTF JSON + binary buffer
    2. Load PNG texture via Pillow, detect unique colors
    3. Quantize palette to SAFE_LEVELS (6x6x6 terminal-safe cube)
    4. Sample texture at UV coords → per-vertex quantized colors
    5. Apply GLTF→AKM axis conversion
    6. Write ASCII PLY (x,y,z,nx,ny,nz,r,g,b,a per vertex)

[DATA-CONTRACT:AKM] Output is standard ASCII PLY with Asciicker conventions.
Colors are quantized to {0,51,102,153,204,255} per channel (0% dithering).
Alpha = 0 (solid collision) by default; use --passthrough for alpha=255.
"""

import json
import struct
import os
import sys
import argparse
import math

# ---------------------------------------------------------------------------
# SAFE_LEVELS -- The terminal-safe 6x6x6 color cube.
# Colors on these levels need no dithering in the Asciicker renderer.
# ---------------------------------------------------------------------------
SAFE_LEVELS = (0, 51, 102, 153, 204, 255)

# GLTF component type constants
GLTF_BYTE = 5120
GLTF_UNSIGNED_BYTE = 5121
GLTF_SHORT = 5122
GLTF_UNSIGNED_SHORT = 5123
GLTF_UNSIGNED_INT = 5125
GLTF_FLOAT = 5126

# GLTF primitive mode constants
GLTF_TRIANGLES = 4
GLTF_TRIANGLE_STRIP = 5
GLTF_TRIANGLE_FAN = 6

# GLTF component sizes in bytes
COMPONENT_SIZES = {
    GLTF_BYTE: 1,
    GLTF_UNSIGNED_BYTE: 1,
    GLTF_SHORT: 2,
    GLTF_UNSIGNED_SHORT: 2,
    GLTF_UNSIGNED_INT: 4,
    GLTF_FLOAT: 4,
}

COMPONENT_COUNTS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
}

COMPONENT_FORMATS = {
    GLTF_BYTE: "b",
    GLTF_UNSIGNED_BYTE: "B",
    GLTF_SHORT: "h",
    GLTF_UNSIGNED_SHORT: "H",
    GLTF_UNSIGNED_INT: "I",
    GLTF_FLOAT: "f",
}


# ---------------------------------------------------------------------------
# Palette utilities
# ---------------------------------------------------------------------------

def snap_to_palette(value):
    """Snap a single channel value (0-255) to nearest SAFE_LEVELS level."""
    return min(SAFE_LEVELS, key=lambda x: abs(x - value))


def detect_picocad_palette(image):
    """Extract unique RGB colors from a Pillow Image, capped at 16 by frequency.

    Reads all pixels from the full texture image, matching the Blender addon's
    detect_picocad_palette() behavior.

    Args:
        image: PIL.Image in RGB mode.

    Returns:
        (colors, warning): list of (R,G,B) tuples (top-16 if >16), and a
        warning string or None.
    """
    rgb_image = image.convert("RGB")
    raw = rgb_image.tobytes()  # flat [R, G, B, R, G, B, ...] — avoids getdata() deprecation
    color_freq = {}
    for i in range(0, len(raw), 3):
        key = (raw[i], raw[i + 1], raw[i + 2])
        color_freq[key] = color_freq.get(key, 0) + 1

    warning = None
    colors = list(color_freq.keys())

    if len(colors) > 16:
        warning = (
            f"Detected {len(colors)} unique colors (not a picoCAD2 model?). "
            f"Using top 16 by frequency."
        )
        sorted_colors = sorted(color_freq.items(), key=lambda x: x[1], reverse=True)
        colors = [c for c, _ in sorted_colors[:16]]

    return colors, warning


def quantize_picocad_palette(colors):
    """Map each detected color to nearest SAFE_LEVELS^3 entry (per-channel snap).

    Colors are sorted by (R,G,B) before building the map so that collision
    tie-breaking is deterministic and matches the Blender addon's numpy path
    (np.unique returns sorted values).

    Args:
        colors: list of (R,G,B) int tuples in 0-255 range.

    Returns:
        dict: {(R,G,B): (R_snapped, G_snapped, B_snapped)} mapping.
    """
    palette_map = {}
    sorted_colors = sorted(colors)  # Deterministic order matching addon numpy path
    for r, g, b in sorted_colors:
        rs = snap_to_palette(r)
        gs = snap_to_palette(g)
        bs = snap_to_palette(b)
        palette_map[(r, g, b)] = (rs, gs, bs)
    return palette_map


def sample_texture_at_uv(image, u, v):
    """Sample a Pillow Image at texture coordinate (u, v) with GLTF V-flip.

    GLTF uses origin at top-left (V=0 at top). We convert:
        tex_x = u * width
        tex_y = (1 - v) * height
    Clamped to image bounds.

    Returns (R, G, B) int tuple in 0-255 range.
    """
    w, h = image.size
    x = int(u * w) % w
    y = int((1.0 - v) * h) % h
    return image.getpixel((x, y))


# ---------------------------------------------------------------------------
# GLTF parsing (stdlib only)
# ---------------------------------------------------------------------------

def read_gltf_buffers(gltf_dir, gltf_data):
    """Read all binary buffers referenced in the GLTF JSON.

    Args:
        gltf_dir: Directory containing the .gltf file.
        gltf_data: Parsed GLTF JSON.

    Returns:
        List of raw bytes, one per buffer entry.
    """
    buffers = []
    gltf_real = os.path.realpath(gltf_dir)
    for buf_info in gltf_data.get("buffers", []):
        uri = buf_info["uri"]
        bin_path = os.path.realpath(os.path.join(gltf_dir, uri))
        if not bin_path.startswith(gltf_real + os.sep) and bin_path != gltf_real:
            raise ValueError(f"Buffer URI escapes GLTF directory: {uri!r}")
        with open(bin_path, "rb") as f:
            buffers.append(f.read())
    return buffers


def resolve_texture_path(gltf_data, gltf_dir):
    """Find the PNG texture path from the first material's baseColorTexture.

    Returns the absolute path to the PNG, or None if not found.
    """
    # Walk: materials[0] -> pbrMetallicRoughness -> baseColorTexture -> index -> images[]
    materials = gltf_data.get("materials", [])
    if not materials:
        return None
    mat = materials[0]
    pbr = mat.get("pbrMetallicRoughness", {})
    tex_ref = pbr.get("baseColorTexture", {})
    tex_index = tex_ref.get("index")
    if tex_index is None:
        return None
    textures = gltf_data.get("textures", [])
    if tex_index >= len(textures):
        return None
    source_idx = textures[tex_index].get("source")
    if source_idx is None:
        return None
    images = gltf_data.get("images", [])
    if source_idx >= len(images):
        return None
    uri = images[source_idx].get("uri")
    if not uri:
        return None
    tex_path = os.path.realpath(os.path.join(gltf_dir, uri))
    gltf_real = os.path.realpath(gltf_dir)
    if not tex_path.startswith(gltf_real + os.sep) and tex_path != gltf_real:
        raise ValueError(f"Texture URI escapes GLTF directory: {uri!r}")
    return tex_path


class GLTFAccessor:
    """Read typed array data from a GLTF accessor + bufferView.

    Resolves the correct buffer via the bufferView → buffer chain, so multi-buffer
    GLTF files work correctly (not just buffer 0).
    """

    def __init__(self, gltf_data, buffers, accessor_idx):
        acc = gltf_data["accessors"][accessor_idx]
        view = gltf_data["bufferViews"][acc["bufferView"]]
        buf_idx = view.get("buffer", 0)
        buffer_data = buffers[buf_idx]
        offset = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        count = acc["count"]
        comp_type = acc["componentType"]
        type_str = acc["type"]
        num_components = COMPONENT_COUNTS[type_str]
        comp_size = COMPONENT_SIZES[comp_type]
        fmt_char = COMPONENT_FORMATS[comp_type]
        stride = view.get("byteStride", comp_size * num_components)

        self.data = []
        for i in range(count):
            pos = offset + i * stride
            try:
                vals = struct.unpack_from(
                    f"<{num_components}{fmt_char}", buffer_data, pos
                )
            except struct.error as exc:
                raise ValueError(
                    f"GLTF buffer too short at element {i}/{count} "
                    f"(offset {pos}, need {comp_size * num_components} bytes, "
                    f"buffer is {len(buffer_data)} bytes): {exc}"
                ) from exc
            if num_components == 1:
                self.data.append(vals[0])
            else:
                self.data.append(vals)

    def __getitem__(self, idx):
        return self.data[idx]

    def __len__(self):
        return len(self.data)


def convert_triangle_strips_to_triangles(indices):
    """Convert TRIANGLE_STRIP indices to TRIANGLES.

    Each triplet (i, i+1, i+2) forms a triangle; winding order alternates.
    """
    result = []
    for i in range(len(indices) - 2):
        a, b, c = indices[i], indices[i + 1], indices[i + 2]
        if a == b or b == c or a == c:
            continue  # Degenerate
        if i % 2 == 0:
            result.extend([a, b, c])
        else:
            result.extend([a, c, b])  # Reverse winding for even strips
    return result


def convert_triangle_fans_to_triangles(indices):
    """Convert TRIANGLE_FAN indices to TRIANGLES.

    First vertex is the fan center; each subsequent pair with center forms a
    triangle.
    """
    result = []
    center = indices[0]
    for i in range(1, len(indices) - 1):
        a = center
        b = indices[i]
        c = indices[i + 1]
        if a == b or b == c or a == c:
            continue  # Degenerate
        result.extend([a, b, c])
    return result


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def compute_vertex_normals(positions, faces):
    """Compute per-vertex normals via area-weighted face-normal accumulation.

    Each face normal is computed as the cross product of two edges. Face normals
    are accumulated at each vertex, weighted by face area. The accumulated
    vectors are normalized to unit length.

    Args:
        positions: list of (x, y, z) tuples.
        faces: list of (i0, i1, i2) index triples.

    Returns:
        list of (nx, ny, nz) unit-vector tuples, one per vertex.
    """
    n = len(positions)
    accum = [[0.0, 0.0, 0.0] for _ in range(n)]

    for i0, i1, i2 in faces:
        p0 = positions[i0]
        p1 = positions[i1]
        p2 = positions[i2]
        e1 = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
        e2 = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
        # Cross product
        nx = e1[1] * e2[2] - e1[2] * e2[1]
        ny = e1[2] * e2[0] - e1[0] * e2[2]
        nz = e1[0] * e2[1] - e1[1] * e2[0]
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length > 1e-10:
            nx /= length
            ny /= length
            nz /= length
            # Weight by face area (0.5 * |cross|)
            area = 0.5 * length
            for vi in (i0, i1, i2):
                accum[vi][0] += nx * area
                accum[vi][1] += ny * area
                accum[vi][2] += nz * area

    normals = []
    for acc in accum:
        ax, ay, az = acc
        length = math.sqrt(ax * ax + ay * ay + az * az)
        if length > 1e-10:
            normals.append((ax / length, ay / length, az / length))
        else:
            normals.append((0.0, 0.0, 1.0))  # Default up
    return normals


# ---------------------------------------------------------------------------
# Axis conversion
# ---------------------------------------------------------------------------

def gltf_to_akm_axis(x, y, z):
    """Convert a GLTF position or normal to AKM coordinate system.

    GLTF: right-hand Y-up.
    AKM:  Y-forward / Z-up (Blender convention after GLTF import).
    Mapping: AKM.X = GLTF.X, AKM.Y = -GLTF.Z, AKM.Z = GLTF.Y
    """
    return (x, -z, y)


# ---------------------------------------------------------------------------
# AKM writer
# ---------------------------------------------------------------------------

def write_akm(filepath, vertices, faces, has_normals=True, has_colors=True):
    """Write an ASCII PLY AKM file.

    [DATA-CONTRACT:AKM] Standard ASCII PLY 1.0 format with Asciicker
    conventions. Vertex properties: x,y,z [,nx,ny,nz] [,red,green,blue,alpha].
    Faces: property list uchar uint vertex_indices. All faces are triangles.

    Args:
        filepath: Destination .akm path.
        vertices: list of dicts with keys 'position', 'normal' (optional),
                  'color' (optional (r,g,b,a) tuple).
        faces: list of (i0,i1,i2) index triples.
        has_normals: include nx,ny,nz per vertex.
        has_colors: include red,green,blue,alpha per vertex.
    """
    with open(filepath, "w", encoding="utf-8", newline="\n") as f:
        fw = f.write

        fw("ply\n")
        fw("format ascii 1.0\n")
        fw("comment Created by picocad_to_akm.py - Asciicker AKM Export\n")

        fw(f"element vertex {len(vertices)}\n")
        fw("property float x\n")
        fw("property float y\n")
        fw("property float z\n")

        if has_normals:
            fw("property float nx\n")
            fw("property float ny\n")
            fw("property float nz\n")

        if has_colors:
            fw("property uchar red\n")
            fw("property uchar green\n")
            fw("property uchar blue\n")
            fw("property uchar alpha\n")

        fw(f"element face {len(faces)}\n")
        fw("property list uchar uint vertex_indices\n")
        fw("end_header\n")

        # Vertices
        for v in vertices:
            px, py, pz = v["position"]
            fw("%.3f %.3f %.3f" % (px, py, pz))
            if has_normals and "normal" in v:
                nx, ny, nz = v["normal"]
                fw(" %.2f %.2f %.2f" % (nx, ny, nz))
            if has_colors and "color" in v:
                r, g, b, a = v["color"]
                fw(" %u %u %u %u" % (r, g, b, a))
            fw("\n")

        # Faces
        for i0, i1, i2 in faces:
            fw(f"3 {i0} {i1} {i2}\n")

    print(f"Wrote: {filepath} ({len(vertices)} verts, {len(faces)} faces)")


# ---------------------------------------------------------------------------
# Audit mode
# ---------------------------------------------------------------------------

def audit_akm(filepath):
    """Check an AKM file for palette safety of vertex colors.

    Reads the PLY header, finds the 'red' property, then checks every vertex
    color against SAFE_LEVELS. Reports violations.

    Returns 0 if all colors are palette-safe, 1 if violations found.
    """
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Parse header to find vertex count and property layout
    vert_count = 0
    properties = []
    header_ended = False

    for line in lines:
        line = line.strip()
        if header_ended:
            break
        if line.startswith("element vertex "):
            vert_count = int(line.split()[-1])
        elif line.startswith("property "):
            prop = line.split()[-1]
            properties.append(prop)
        elif line == "end_header":
            header_ended = True

    if vert_count == 0:
        print(f"Error: Could not parse vertex count from {filepath}")
        return 1

    # Find color property indices
    try:
        r_idx = properties.index("red")
        g_idx = properties.index("green")
        b_idx = properties.index("blue")
    except ValueError:
        print("No vertex colors in this AKM file")
        return 0

    # Scan vertex lines after header
    header_line_count = 0
    for i, line in enumerate(lines):
        if line.strip() == "end_header":
            header_line_count = i + 1
            break

    violations = 0
    for i in range(vert_count):
        line_idx = header_line_count + i
        if line_idx >= len(lines):
            break
        parts = lines[line_idx].split()
        if len(parts) <= max(r_idx, g_idx, b_idx):
            continue
        r = int(parts[r_idx])
        g = int(parts[g_idx])
        b = int(parts[b_idx])
        if r not in SAFE_LEVELS or g not in SAFE_LEVELS or b not in SAFE_LEVELS:
            violations += 1
            if violations <= 5:  # Only report first 5
                print(f"  Vertex {i}: color ({r},{g},{b}) outside SAFE_LEVELS")

    if violations > 5:
        print(f"  ... and {violations - 5} more")

    if violations == 0:
        print(f"{filepath}: {vert_count} vertices, 0 outside SAFE_LEVELS")
        return 0
    else:
        print(
            f"{filepath}: {violations}/{vert_count} vertices "
            f"({100.0*violations/vert_count:.0f}%) outside SAFE_LEVELS"
        )
        return 1


# ---------------------------------------------------------------------------
# Main conversion
# ---------------------------------------------------------------------------

def convert_gltf_to_akm(
    gltf_path,
    output_path=None,
    merge=False,
    solid=True,
):
    """Convert a picoCAD2 GLTF export to an AKM file.

    Args:
        gltf_path: Path to the .gltf file.
        output_path: Destination .akm path (auto-derived if None).
        merge: If True, combine all GLTF primitives into one AKM.
        solid: If True, alpha=0 (solid); if False, alpha=255 (passthrough).

    Returns:
        True on success, False on failure.
    """
    # Check Pillow availability
    try:
        from PIL import Image
    except ImportError:
        print("Error: Pillow is required for PNG reading.", file=sys.stderr)
        print("  Install with: pip install Pillow", file=sys.stderr)
        return False

    # Read GLTF JSON
    gltf_dir = os.path.dirname(os.path.abspath(gltf_path))
    try:
        with open(gltf_path, "r", encoding="utf-8") as f:
            gltf = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error: Could not read GLTF file: {e}", file=sys.stderr)
        return False

    # Read all binary buffers (resolves multi-buffer GLTF correctly)
    try:
        buffers = read_gltf_buffers(gltf_dir, gltf)
    except (KeyError, FileNotFoundError) as e:
        print(f"Error: Could not read GLTF binary buffer(s): {e}", file=sys.stderr)
        return False

    # Find texture
    tex_path = resolve_texture_path(gltf, gltf_dir)
    if not tex_path or not os.path.exists(tex_path):
        print("Error: No texture found in GLTF material", file=sys.stderr)
        return False

    # Load texture
    try:
        image = Image.open(tex_path).convert("RGB")
    except Exception as e:
        print(f"Error: Could not load texture {tex_path}: {e}", file=sys.stderr)
        return False

    # Detect + quantize palette
    colors, warning = detect_picocad_palette(image)
    if not colors:
        print("Error: No colors detected in texture", file=sys.stderr)
        return False
    if warning:
        print(f"Warning: {warning}")

    palette_map = quantize_picocad_palette(colors)

    # Warn on SAFE_LEVELS collisions
    snapped_set = set(palette_map.values())
    if len(snapped_set) < len(colors):
        print(
            f"Warning: {len(colors)} source colors collapsed to "
            f"{len(snapped_set)} unique snapped colors"
        )

    color_count = len(snapped_set)

    # Process each mesh
    meshes = gltf.get("meshes", [])
    if not meshes:
        print("Error: No meshes found in GLTF file", file=sys.stderr)
        return False

    all_vert_batches = []
    all_face_batches = []

    for mesh in meshes:
        for pi, prim in enumerate(mesh.get("primitives", [])):
            # Check primitive mode
            mode = prim.get("mode", GLTF_TRIANGLES)
            if mode not in (GLTF_TRIANGLES, GLTF_TRIANGLE_STRIP, GLTF_TRIANGLE_FAN):
                print(
                    f"Error: Unsupported primitive mode {mode} "
                    f"(expected TRIANGLES=4, TRIANGLE_STRIP=5, or TRIANGLE_FAN=6)",
                    file=sys.stderr,
                )
                return False

            # Extract position accessor
            pos_acc_idx = prim["attributes"].get("POSITION")
            if pos_acc_idx is None:
                print(f"Error: Primitive {pi} has no POSITION attribute", file=sys.stderr)
                return False

            positions_gl = GLTFAccessor(gltf, buffers, pos_acc_idx)

            # Extract normal accessor (optional)
            normals_gl = None
            norm_acc_idx = prim["attributes"].get("NORMAL")
            if norm_acc_idx is not None:
                normals_gl = GLTFAccessor(gltf, buffers, norm_acc_idx)

            # Extract UV accessor (required for texture sampling)
            uv_acc_idx = prim["attributes"].get("TEXCOORD_0")
            if uv_acc_idx is None:
                print(
                    f"Error: Primitive {pi} has no TEXCOORD_0 attribute",
                    file=sys.stderr,
                )
                return False
            uvs = GLTFAccessor(gltf, buffers, uv_acc_idx)

            # Extract index accessor (or generate sequential indices)
            indices = None
            idx_acc_idx = prim.get("indices")
            if idx_acc_idx is not None:
                indices = list(GLTFAccessor(gltf, buffers, idx_acc_idx))
            else:
                indices = list(range(len(positions_gl)))

            # Convert strip/fan to triangles
            if mode == GLTF_TRIANGLE_STRIP:
                indices = convert_triangle_strips_to_triangles(indices)
            elif mode == GLTF_TRIANGLE_FAN:
                indices = convert_triangle_fans_to_triangles(indices)

            # Build triangles list
            faces = []
            for i in range(0, len(indices) - 2, 3):
                faces.append((indices[i], indices[i + 1], indices[i + 2]))

            # Sample texture at UVs to get per-vertex colors
            vertex_colors = []
            for uv in uvs:
                u, v = uv[0], uv[1]
                src_rgb = sample_texture_at_uv(image, u, v)
                quant_rgb = palette_map.get(
                    src_rgb,
                    (snap_to_palette(src_rgb[0]),
                     snap_to_palette(src_rgb[1]),
                     snap_to_palette(src_rgb[2])),
                )
                vertex_colors.append(quant_rgb)

            # Convert positions and normals to AKM axes
            positions_akm = [
                gltf_to_akm_axis(p[0], p[1], p[2]) for p in positions_gl
            ]

            if normals_gl is not None:
                normals_akm = [
                    gltf_to_akm_axis(n[0], n[1], n[2]) for n in normals_gl
                ]
                needs_normal_compute = False
            else:
                needs_normal_compute = True
                normals_akm = None

            # Compute normals if absent
            if needs_normal_compute:
                print(f"  Computing vertex normals for primitive {pi} (no NORMAL accessor)...")
                normals_akm = compute_vertex_normals(positions_akm, faces)

            # Build vertex list for this primitive
            # Deduplicate by position+normal+color
            alpha = 0 if solid else 255
            vdict = {}
            verts = []
            remapped_faces = []

            for i0, i1, i2 in faces:
                new_indices = []
                for vi in (i0, i1, i2):
                    key = (
                        positions_akm[vi],
                        normals_akm[vi],
                        vertex_colors[vi],
                    )
                    if key not in vdict:
                        vdict[key] = len(verts)
                        verts.append({
                            "position": positions_akm[vi],
                            "normal": normals_akm[vi],
                            "color": vertex_colors[vi] + (alpha,),
                        })
                    new_indices.append(vdict[key])
                remapped_faces.append(tuple(new_indices))

            all_vert_batches.append(verts)
            all_face_batches.append(remapped_faces)

    # Figure out output path
    if output_path is None:
        # Default: assets/meshes/<stem>.akm relative to repo root
        stem = os.path.splitext(os.path.basename(gltf_path))[0]
        repo_root = find_repo_root(gltf_dir)
        meshes_dir = os.path.join(repo_root, "assets", "meshes")
        if not os.path.isdir(meshes_dir):
            os.makedirs(meshes_dir, exist_ok=True)
            print(f"Created {meshes_dir}/")
        output_path = os.path.join(meshes_dir, f"{stem}.akm")
    else:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)

    # Write output
    if merge:
        # Combine all batches into one AKM
        all_verts = []
        all_faces = []
        for batch_verts, batch_faces in zip(all_vert_batches, all_face_batches):
            offset = len(all_verts)
            all_verts.extend(batch_verts)
            for f in batch_faces:
                all_faces.append((f[0] + offset, f[1] + offset, f[2] + offset))
        write_akm(output_path, all_verts, all_faces)
    else:
        if len(all_vert_batches) == 1:
            write_akm(output_path, all_vert_batches[0], all_face_batches[0])
        else:
            # Write one AKM per primitive
            stem = os.path.splitext(output_path)[0]
            for i, (verts, faces) in enumerate(zip(all_vert_batches, all_face_batches)):
                out = f"{stem}_{i}.akm"
                write_akm(out, verts, faces)

    print(
        f"  {len(colors)} source colors → {color_count}/{16} unique SAFE_LEVELS colors"
    )
    return True


def find_repo_root(start_dir):
    """Find repo root by looking for assets/meshes/ in ancestor directories."""
    d = os.path.abspath(start_dir)
    while d != os.path.dirname(d):
        if os.path.isdir(os.path.join(d, "assets", "meshes")):
            return d
        d = os.path.dirname(d)
    return start_dir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert picoCAD2 GLTF export to Asciicker AKM mesh (no Blender required)."
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Path to .gltf file, or --audit path to .akm file",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output .akm path (default: assets/meshes/<stem>.akm)",
    )
    parser.add_argument(
        "--merge", "-m",
        action="store_true",
        help="Merge all GLTF primitives into one AKM",
    )
    parser.add_argument(
        "--passthrough",
        action="store_true",
        help="Set collision alpha=255 (passthrough, no collision). Default is solid (alpha=0).",
    )
    parser.add_argument(
        "--audit",
        metavar="AKM_PATH",
        help="Audit an existing AKM file for palette safety (skip conversion)",
    )

    args = parser.parse_args()

    # Audit mode
    if args.audit:
        sys.exit(audit_akm(args.audit))

    if not args.input:
        parser.print_help()
        sys.exit(1)

    if not os.path.exists(args.input):
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    solid = not args.passthrough

    success = convert_gltf_to_akm(
        args.input,
        output_path=args.output,
        merge=args.merge,
        solid=solid,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
