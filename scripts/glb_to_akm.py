#!/usr/bin/env python3
# =============================================================
# glb_to_akm.py
# =============================================================
# WHAT THIS DOES
# --------------
# The asciicker project already ships a converter, `picocad_to_akm.py`,
# that turns a *text* glTF export (a `.gltf` file plus its sidecar
# `.bin` buffer and `.png` texture) into the game's AKM mesh format.
#
# The problem: most 3D models people download from the internet come as
# a single-file **`.glb`** (binary glTF). `picocad_to_akm.py` cannot read
# those directly — it only understands the unpacked text form.
#
# This script is the missing front door. It takes a `.glb`, unpacks it
# into the `.gltf` + `.bin` + image triplet in a temporary folder, and
# then hands that off to `picocad_to_akm.py` to produce the final `.akm`.
#
# In plain terms: **"give me a .glb, get back a .akm"**, with no Blender
# and no manual unpacking required.
#
# WHY A .glb NEEDS UNPACKING
# --------------------------
# A `.glb` file is a tiny binary container. Its layout is:
#
#   [ 12-byte header: the 4 letters "glTF", a version number, total size ]
#   then a sequence of "chunks", each shaped like:
#       [ 4 bytes: chunk length ][ 4 bytes: chunk type ][ chunk bytes ]
#
#   * A chunk whose type code is 0x4E4F534A (the ASCII letters "JSON")
#     holds the glTF scene description as JSON text.
#   * A chunk whose type code is 0x004E4942 (the ASCII letters "BIN\0")
#     holds the raw binary buffer: vertex positions, normals, texture
#     coordinates, triangle indices, and sometimes the embedded images.
#
# To feed `picocad_to_akm.py` we simply split those two chunks back out
# to disk as separate files and rewrite the JSON so its "buffer" and
# "image" references point at the extracted sidecar files instead of at
# offsets inside the original binary blob.
#
# USAGE
# -----
#   python3 scripts/glb_to_akm.py model.glb
#   python3 scripts/glb_to_akm.py model.glb --output assets/meshes/incoming/model.akm
#   python3 scripts/glb_to_akm.py model.glb --keep-unpacked   # keep the .gltf triplet
#
# By default the AKM is written next to the source, and (because most
# downloaded models are multi-part) primitives are MERGED into one AKM
# so you get a single tidy mesh file instead of model_0.akm, model_1.akm...
#
# CAVEAT (read this before trusting the colors)
# ---------------------------------------------
# `picocad_to_akm.py` was tuned for picoCAD2 art, which uses a tiny built-in
# palette. Arbitrary downloaded models use full-color textures, so the
# converter samples their texture down to its top-16 colors and then snaps
# those to the asciicker "safe" palette. The GEOMETRY is preserved exactly,
# but the COLOR is heavily reduced — expect a stylized, few-color look.
# That is usually acceptable (and even on-brand) for the ASCII world, but it
# is not a faithful reproduction of the original model's texture.
# =============================================================

import argparse          # parses the command-line flags (--output, etc.)
import json              # reads/writes the glTF JSON chunk
import os                # file path juggling and existence checks
import struct            # unpacks the little-endian binary GLB header/chunks
import subprocess        # invokes picocad_to_akm.py as a child process
import sys               # exit codes and stderr
import tempfile          # scratch directory for the unpacked triplet
import shutil            # cleanup of the scratch directory
from PIL import Image    # used to synthesize solid-color textures for
                         # materials that ship a plain color but no image


def _synthesize_flat_textures(gltf, out_dir, stem):
    """
    Give every UNTEXTURED material a tiny solid-color texture.

    Why this exists
    ---------------
    `picocad_to_akm.py` colors the mesh by sampling a material's texture
    image at each vertex's UV. Many downloaded models (e.g. low-poly props
    exported from Blender) carry NO texture at all — they paint each surface
    with a single flat color stored in the material's `baseColorFactor`.
    Fed to the converter as-is, those models error out with
    "No texture found in GLTF material".

    The fix: for each such material we bake its flat color into a small
    solid-color PNG and attach it as the material's `baseColorTexture`.
    Because the image is one uniform color, every UV samples the same value,
    so the converter faithfully reproduces the material's intended color —
    and, crucially, DIFFERENT materials keep DIFFERENT colors (e.g. an
    airplane's tan body vs. its dark engine).

    Parameters
    ----------
    gltf : dict
        The parsed glTF document, modified in place (materials/textures/
        images/samplers arrays are extended).
    out_dir : str
        Directory the sidecar PNG files are written into.
    stem : str
        Base filename for generated PNGs, so they stay self-describing.
    """
    materials = gltf.get("materials", [])
    if not materials:
        return  # nothing to do — no materials means the converter path differs

    # Ensure the container arrays we may append to actually exist.
    gltf.setdefault("images", [])
    gltf.setdefault("textures", [])
    gltf.setdefault("samplers", [])

    for mat_idx, material in enumerate(materials):
        pbr = material.setdefault("pbrMetallicRoughness", {})
        # Skip materials that already reference a real texture — we only
        # want to fill the gap for the flat-colored ones.
        if "baseColorTexture" in pbr:
            continue

        # baseColorFactor is [r, g, b, a] in linear 0..1. Default to white
        # when absent (the glTF spec's default), then convert to 0..255 ints.
        rgba = pbr.get("baseColorFactor", [1.0, 1.0, 1.0, 1.0])
        rgb = tuple(max(0, min(255, int(round(c * 255)))) for c in rgba[:3])

        # Write a tiny 8x8 solid-color PNG. 8x8 (not 1x1) avoids any edge
        # cases in downstream image samplers that assume >1px dimensions.
        png_name = f"{stem}_matcolor{mat_idx}.png"
        Image.new("RGB", (8, 8), rgb).save(os.path.join(out_dir, png_name))

        # Register the new image -> texture -> sampler chain and point the
        # material's base color at it. We append and reference by index,
        # matching how glTF cross-references its arrays.
        img_index = len(gltf["images"])
        gltf["images"].append({"uri": png_name})
        if not gltf["samplers"]:
            gltf["samplers"].append({})       # a default sampler is fine
        sampler_index = 0
        tex_index = len(gltf["textures"])
        gltf["textures"].append({"source": img_index, "sampler": sampler_index})
        pbr["baseColorTexture"] = {"index": tex_index, "texCoord": 0}

# Absolute path to this script's own directory, so we can reliably locate
# the sibling converter `picocad_to_akm.py` regardless of the caller's cwd.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PICOCAD_CONVERTER = os.path.join(_THIS_DIR, "picocad_to_akm.py")

# Chunk-type magic numbers from the glTF 2.0 / GLB binary spec. These are
# the 4 ASCII bytes of each chunk name, read as a little-endian uint32.
_CHUNK_JSON = 0x4E4F534A   # "JSON"
_CHUNK_BIN = 0x004E4942    # "BIN\0"

# GLB file magic: the first four bytes of every valid .glb are "glTF".
_GLB_MAGIC = b"glTF"


def unpack_glb(glb_path, out_dir):
    """
    Split a binary .glb into a text .gltf + .bin (+ extracted image files).

    Parameters
    ----------
    glb_path : str
        Path to the source .glb file we want to unpack.
    out_dir : str
        Directory to write the unpacked triplet into. Created by the caller.

    Returns
    -------
    str
        Full path to the written `.gltf` file, ready to hand to the
        picoCAD converter.

    Why it exists
    -------------
    `picocad_to_akm.py` cannot read binary GLB. This function produces the
    exact on-disk shape it expects: a JSON `.gltf` whose buffer/image URIs
    are plain filenames sitting next to it.
    """
    # Read the entire GLB into memory. These files are small (well under the
    # 100 MB the project cares about), so a single read is simplest and safe.
    with open(glb_path, "rb") as fh:
        data = fh.read()

    # Parse the 12-byte header: 4-char magic, uint32 version, uint32 length.
    magic, version, total_len = struct.unpack_from("<4sII", data, 0)
    if magic != _GLB_MAGIC:
        raise ValueError(
            f"{glb_path} is not a binary .glb (magic was {magic!r}, "
            f"expected {_GLB_MAGIC!r}). If it is already a text .gltf, "
            f"feed it straight to picocad_to_akm.py instead."
        )

    # Walk the chunk list that follows the header. We only care about the
    # first JSON chunk and the first BIN chunk (the spec guarantees at most
    # one of each, JSON first).
    offset = 12
    json_bytes = None
    bin_bytes = None
    while offset < total_len:
        # Each chunk starts with its byte length and its type code.
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8                              # step past the 8-byte header
        chunk = data[offset:offset + chunk_len]  # the chunk payload itself
        offset += chunk_len                      # advance to the next chunk
        if chunk_type == _CHUNK_JSON:
            json_bytes = chunk
        elif chunk_type == _CHUNK_BIN:
            bin_bytes = chunk

    if json_bytes is None:
        raise ValueError(f"{glb_path} contained no JSON chunk — corrupt GLB?")

    # Decode the scene description so we can rewrite its file references.
    gltf = json.loads(json_bytes.decode("utf-8"))

    # The output filenames are all derived from the source model's stem so
    # the triplet stays self-describing (e.g. frog.gltf, frog.bin, frog_tex0.png).
    stem = os.path.splitext(os.path.basename(glb_path))[0]

    # --- 1. Write out the binary buffer and repoint every buffer at it ---
    # In a GLB the geometry buffer is embedded (no URI); in a .gltf it must
    # be an external file referenced by a `uri`. We extract it once and make
    # every buffer entry point at that single file.
    if bin_bytes is not None:
        bin_name = f"{stem}.bin"
        with open(os.path.join(out_dir, bin_name), "wb") as fh:
            fh.write(bin_bytes)
        for buf in gltf.get("buffers", []):
            buf["uri"] = bin_name  # replace the (absent) embedded reference

    # --- 2. Extract any embedded images to real files and repoint them ---
    # Textures in a GLB are stored inside the BIN chunk and referenced by a
    # `bufferView`. The text-glTF converter wants them as ordinary image
    # files referenced by `uri`, so we carve each one out and rewrite it.
    for idx, image in enumerate(gltf.get("images", [])):
        if "bufferView" in image and bin_bytes is not None:
            view = gltf["bufferViews"][image["bufferView"]]
            start = view.get("byteOffset", 0)          # where the image begins
            end = start + view["byteLength"]           # ...and where it ends
            # Choose the file extension from the declared MIME type so the
            # downstream PIL image loader recognises it (png vs jpeg).
            mime = image.get("mimeType", "")
            ext = "png" if "png" in mime else ("jpg" if "jpeg" in mime else "png")
            img_name = f"{stem}_tex{idx}.{ext}"
            with open(os.path.join(out_dir, img_name), "wb") as fh:
                fh.write(bin_bytes[start:end])
            # Swap the in-memory reference from "look inside the buffer" to
            # "load this sidecar file", which is what a .gltf expects.
            image.pop("bufferView", None)
            image.pop("mimeType", None)
            image["uri"] = img_name

    # --- 2b. Backfill flat-color textures for untextured materials ---
    # Some models paint surfaces with a plain material color and no image;
    # the converter requires a texture, so synthesize one per such material.
    _synthesize_flat_textures(gltf, out_dir, stem)

    # --- 3. Write the rewritten JSON as the final .gltf ---
    gltf_path = os.path.join(out_dir, f"{stem}.gltf")
    with open(gltf_path, "w") as fh:
        json.dump(gltf, fh)

    return gltf_path


def _prune_gltf_to_node(gltf_path, node_name):
    """
    Keep only the meshes that belong to ONE named node subtree of a glTF file.

    WHY THIS EXISTS
    ---------------
    Some downloaded models pack several *distinct* objects into one file — e.g.
    "arboles_de_baja_resolucion.glb" holds four separate trees (scene nodes
    named Arbol1 / Arbol / Arbol3 / Arbol4). Converting the whole file with
    --merge fuses all four trees into one clump; converting with --no-merge
    splits by *material* (trunk vs. branches), producing fragments, not whole
    trees. Neither is what we want: we want ONE clean tree per output file so
    each can become its own tree variant in the world.

    picocad_to_akm.py iterates the flat `meshes` array and ignores the node
    graph, so to isolate a single tree we simply DELETE every mesh that is not
    reachable from the chosen node, then let the converter run with --merge.
    (This model has no per-node transforms, and the AKM baker re-centres each
    tree on the origin anyway, so dropping the other nodes' geometry is all we
    need — no transform maths required.)

    Parameters
    ----------
    gltf_path : str
        Path to the unpacked .gltf (JSON) file, edited IN PLACE.
    node_name : str
        Name of the scene node whose subtree we keep (case-sensitive, matching
        the glTF "name" field, e.g. "Arbol3").

    Returns
    -------
    int
        How many meshes were kept. Raises ValueError if the node name is not
        found or its subtree contains no meshes.
    """
    with open(gltf_path, "r") as fh:
        gltf = json.load(fh)

    nodes = gltf.get("nodes", [])
    # Find the index of the node whose "name" matches the request.
    start = None
    for i, n in enumerate(nodes):
        if n.get("name") == node_name:
            start = i
            break
    if start is None:
        names = [n.get("name") for n in nodes]
        raise ValueError(
            "node %r not found; available nodes: %s" % (node_name, names))

    # Depth-first walk from the chosen node, collecting every mesh index that
    # hangs off it or any of its descendants. `children` holds node indices.
    keep_mesh_indices = set()
    stack = [start]
    seen_nodes = set()
    while stack:
        ni = stack.pop()
        if ni in seen_nodes:
            continue
        seen_nodes.add(ni)
        node = nodes[ni]
        if "mesh" in node and node["mesh"] is not None:
            keep_mesh_indices.add(node["mesh"])
        for child in node.get("children", []):
            stack.append(child)

    if not keep_mesh_indices:
        raise ValueError("node %r has no meshes in its subtree" % node_name)

    # Rebuild the meshes array with ONLY the kept meshes. picocad_to_akm reads
    # meshes purely by iterating this list (each primitive still points at the
    # untouched accessors/materials arrays by index), so dropping the others is
    # safe and needs no index remapping.
    all_meshes = gltf.get("meshes", [])
    gltf["meshes"] = [all_meshes[i] for i in sorted(keep_mesh_indices)]

    with open(gltf_path, "w") as fh:
        json.dump(gltf, fh)
    return len(gltf["meshes"])


def convert(glb_path, output_path=None, merge=True, keep_unpacked=False,
            passthrough=False, node=None):
    """
    Full GLB -> AKM conversion: unpack, then invoke picocad_to_akm.py.

    Parameters
    ----------
    glb_path : str
        Source .glb model.
    output_path : str or None
        Desired .akm output path. If None, picocad_to_akm.py's default is
        used (assets/meshes/<stem>.akm). For merged output this is the exact
        file written; for un-merged output the converter appends _0, _1, ...
    merge : bool
        If True (default), all sub-meshes are fused into ONE .akm. This is
        almost always what you want for a single downloaded prop.
    keep_unpacked : bool
        If True, the temporary .gltf/.bin/.png triplet is preserved and its
        location printed (useful for debugging a bad conversion).
    passthrough : bool
        If True, mark the mesh as non-colliding (alpha=255). Default is a
        solid, collidable mesh (alpha=0).
    node : str or None
        If set, keep ONLY the meshes under the named scene node before
        converting (used to pull one tree out of a multi-tree file). Implies a
        merged single-mesh output for that node's geometry.

    Returns
    -------
    int
        The child converter's exit code (0 == success).
    """
    # Unpack into a temp dir. We keep it unless the caller asked to retain it,
    # so we don't litter the repo with intermediate .gltf/.bin/.png files.
    work_dir = tempfile.mkdtemp(prefix="glb2akm_")
    try:
        gltf_path = unpack_glb(glb_path, work_dir)

        # If a single node was requested, strip the glTF down to just that
        # node's meshes before handing it to the converter.
        if node:
            kept = _prune_gltf_to_node(gltf_path, node)
            print(f"[glb_to_akm] pruned to node {node!r}: kept {kept} mesh(es)")

        # Assemble the picocad_to_akm.py command line. We shell out (rather
        # than import it) so this wrapper stays robust to that script's
        # internal API and simply reuses its documented CLI contract.
        cmd = [sys.executable, _PICOCAD_CONVERTER, gltf_path]
        if output_path:
            cmd += ["--output", output_path]
        if merge:
            cmd += ["--merge"]
        if passthrough:
            cmd += ["--passthrough"]

        print(f"[glb_to_akm] {os.path.basename(glb_path)} -> "
              f"{output_path or '(default assets/meshes path)'}")
        # Run the converter and let its stdout/stderr flow straight through
        # to our own so the user sees its warnings (e.g. color reduction).
        result = subprocess.run(cmd)
        return result.returncode
    finally:
        if keep_unpacked:
            # Move the scratch triplet somewhere the user can find it and say so.
            print(f"[glb_to_akm] kept unpacked glTF triplet at: {work_dir}")
        else:
            # Normal path: remove the temporary unpacked files.
            shutil.rmtree(work_dir, ignore_errors=True)


def main():
    """Parse CLI flags and run a single GLB -> AKM conversion."""
    parser = argparse.ArgumentParser(
        description="Convert a binary .glb model into an asciicker .akm mesh "
                    "(unpacks GLB, then runs picocad_to_akm.py). No Blender."
    )
    parser.add_argument("input", help="Path to the source .glb file")
    parser.add_argument("--output", "-o",
                        help="Output .akm path (default: assets/meshes/<stem>.akm)")
    parser.add_argument("--no-merge", action="store_true",
                        help="Emit one .akm per sub-mesh instead of merging into one")
    parser.add_argument("--keep-unpacked", action="store_true",
                        help="Preserve and print the temporary .gltf/.bin/.png triplet")
    parser.add_argument("--passthrough", action="store_true",
                        help="Mark mesh as non-colliding (alpha=255) instead of solid")
    parser.add_argument("--node",
                        help="Keep only the meshes under this scene node name "
                             "(e.g. --node Arbol3 pulls one tree from a "
                             "multi-tree file). Forces a merged single output.")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    code = convert(
        args.input,
        output_path=args.output,
        # --node isolates one object, which only makes sense merged into a
        # single mesh, so a node request overrides --no-merge.
        merge=(not args.no_merge) or bool(args.node),
        keep_unpacked=args.keep_unpacked,
        passthrough=args.passthrough,
        node=args.node,
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
