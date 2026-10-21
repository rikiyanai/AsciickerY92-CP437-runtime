#!/usr/bin/env python3
"""Compile the FL-4789 immutable Y8 terrain and authored-tree asset catalog.

The compiler is an offline producer.  It reads the runtime A3D heightfield, the
provenance-checked FL-4708 authored-tree manifest, and the authored `.akm` tree
meshes those manifest rows name, then emits a JSON catalog plus one immutable
binary payload in the StaticMeshBVHBuilder byte ABI.  Runtime code consumes
these bytes directly.

TREE GEOMETRY OWNER.  Every retained tree face comes from an authored `.akm`
mesh file. This compiler synthesises no trunk or canopy geometry: the deleted
trunk/branch/lobe generators were a stand-in for the authored assets, and their
lobed canopies are what made the published world read as procedural.  What the
compiler still owns is the *mapping* from an authored mesh into the retained
face ABI: the placement-local basis, the trunk/canopy material choice, and the
canopy contour/interior/underside role. FL-4359 understory is appended after
the unchanged authored faces, using the actual trunk base and terrain surface.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import struct
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAP = REPO_ROOT / "godot_project/assets/maps/game_map_y8.a3d"
DEFAULT_TREE_MANIFEST = (
    REPO_ROOT / "godot_project/assets/world_mesh/fl4708_y8_tree_instances.v1.json"
)
DEFAULT_OUTPUT = (
    REPO_ROOT / "godot_project/assets/world_partition/fl4789_chunk_catalog.v1.json"
)
DEFAULT_PAYLOAD = (
    REPO_ROOT / "godot_project/assets/world_partition/fl4789_chunk_assets.v1.bin"
)

# The authored mesh library the original Y8 A3D instances reference by name.
# assets/meshes is the tracked copy that holds all six canonical tree assets;
# godot_project/assets/meshes carries only the subset a Godot scene needed.
# Geometry is baked into the payload here, so the runtime never opens an .akm.
DEFAULT_MESH_ROOT = REPO_ROOT / "assets/meshes"
# The shared ASCII-PLY reader.  Reusing it keeps one .akm parser in the tree
# instead of a second, silently diverging copy inside this compiler.
AKM_READER_PATH = Path(__file__).resolve().parent / "bake_akm_to_static_world_faces.py"

SCHEMA = "fl4789_chunk_catalog.v1"
# 3.2.0: permanent meadow fans follow the unchanged tree registry prefix.
# 3.4.0: FL-4359 meadow sites emit ground-following patch mats, not upright
# blade cards; meadow flowers are raised heads inside those mats.
# 3.5.0: FL-4359/T1 one world-anchored clump field drives each site's crest
# height, authored shade and fan direction together, replacing independent
# per-site draws (Ghost of Tsushima intake G8HH_pMKOhk sect. 9.4).
# 3.6.0: FL-4359/T2 deterministic terrain-area coverage, explicit clump trait
# records and face-to-clump ownership. Flowers are glyph flags on cards, never
# a second low-material geometry family.
COMPILER_VERSION = "3.7.0"
PATCH_BYTES = 188
VISUAL_CELLS = 8
HEIGHT_CELLS = 4
PATCHES_PER_CHUNK = 4
CHUNK_CELLS = PATCHES_PER_CHUNK * VISUAL_CELLS
MATERIAL_BLOCK_BYTES = 256 * 512

# FL-4789 permanent registry contract.  World reserves this range for fixed
# startup instances and adds its base_instance_count to each tree registry_slot.
REGISTRY_SLOT_BASE = 0
FIXED_INSTANCE_RESERVED_BASE = 0
FIXED_INSTANCE_RESERVED_COUNT = 256

PAYLOAD_MAGIC = b"FL4789P1"
PAYLOAD_VERSION = 1
# magic, version, header_bytes, content_count, registry_base, registry_count,
# fixed_registry_count.  The fixed registry base is the semantic zero and is
# carried by the JSON contract; the established runtime byte ABI reserves the
# final u32 for the count at offset 28.
PAYLOAD_HEADER_STRUCT = struct.Struct("<8sIIIIII")
PAYLOAD_HEADER_BYTES = PAYLOAD_HEADER_STRUCT.size
LEAF_FLAG = 0x80000000
FLOATS_PER_FACE = 16
FACE_AABB_PAD = 1.0e-3
MAX_FACES = 32768

# FL-4708's retained tree material range is deliberately outside the terrain
# matlib. The four tree roles come from authored faces. Materials 244/245 are
# explicitly generated understory, never presented as authored .akm geometry.
TREE_TRUNK_MAT = 240
TREE_CANOPY_CONTOUR_MAT = 241
TREE_CANOPY_INTERIOR_MAT = 242
TREE_CANOPY_UNDERSIDE_MAT = 243
ROOT_MOSS_MAT = 244
ROOT_FLOWER_MAT = 245
MEADOW_BLADE_MAT = 255

# FL-4798 authored fixtures.  A map's A3D world section places more than trees:
# benches, street lamps, planters, rocks, bricks, fences, houses.  Those rows
# reach the retained world through the same immutable catalog, as their own
# source kind, drawing the authored .akm geometry at the authored basis.  Their
# faces publish through the authored-colour paint lane cell_owner_product.glsl
# already owns for material 240 with the 4708 colour tag: the face keeps its
# authored vertex RGB and outward normal, and no new shader material branch is
# introduced.  A dedicated fixture id needs that branch (shader owner FL-4359);
# until it exists this is the single paint owner for fixture faces.
AUTHORED_FIXTURE_MAT = TREE_TRUNK_MAT
# FL-4902 lane TB (tree bit): the growth code in the 4708 tag's fraction
# (azimuth byte | elevation << 8, over 2048) is the one lane that separates a
# fixture face from tree bark on the shared material.  Every fixture face
# carries this neutral code (azimuth 0, level elevation); a bark face never
# does (build_tree_local_rows moves a colliding bark code one azimuth step,
# which no reader sees: Product consumes the growth direction only for canopy
# 241-243).  true_ff_gbuffer.glsl publishes "material 240 and code !=
# FIXTURE_GROWTH_CODE" as the retained-tree bit.
FIXTURE_GROWTH_CODE = 0 | (round(0.5 * 7) << 8)
# Fixture registry source ids live in their own namespace: the authored A3D
# instance index plus this base, so they can never collide with the authored
# tree ids (raw A3D indexes) or the fixed lab-grove ids (1201..).  The raw index
# stays on the record as `source_instance_index`.
FIXTURE_SOURCE_ID_BASE = 100000
AUTHORED_FIXTURE_SHADE = 0.78
FIXTURE_SOURCE_KIND = "authored_fixture"

# FL-4359 grass-fields S7 (operator 2026-09-09): the meadow is published as
# FL-4691: separated blades share clump traits, never one wide solid ribbon.
# Each blade has its own planted root and tapered capture/shadow geometry.
MEADOW_BLADES_PER_CLUMP = 5
MEADOW_BLADE_WIDTH = 0.55
MEADOW_CLUMP_RADIUS = 1.05
# Area coverage is a world-anchored lattice. A 32x32 chunk therefore has at
# most roughly 13x13 primary sites before terrain/tree gates, keeping the card
# budget bounded while eliminating the old patch-local 3 nuclei x 4 member
# holes. The spacing and 63k catalog ceiling follow the reviewed FL-4359 card
# budget (the current map compiles to 56,873 meadow faces at this density).
MEADOW_COVERAGE_SPACING = 2.5
MEADOW_COVERAGE_JITTER = 0.65
MEADOW_MAX_FACES_PER_CHUNK = 2048
MEADOW_MAX_TOTAL_FACES = 63000
MEADOW_FLOWER_FLAG = 1 << 0
MEADOW_SPECIES_COUNT = 9
# FL-4359 operator contract: every ordinary grass blade is one ANSI cell wide.
# Authored species 0..2 are the three rooted 1x4 grass stamps; species 3 is the
# three-column fern and therefore cannot enter the ordinary blade pool.
MEADOW_GRASS_SPECIES_COUNT = 3
MEADOW_FLOWER_SPECIES_BASE = 4
MEADOW_FLOWER_SPECIES_COUNT = 5
MEADOW_MOTIF_COUNT = 4
# A ribbon segment may follow a slope but not bridge a step: a card hanging
# over a cliff lip floats, so the segment is dropped and the spine ends there.
MEADOW_BLADE_MAX_STEP = 0.60
# Maximum authored deformation of a card vertex, world units. The runtime
# update uses a contact throw bounded by 1.0 and a horizontal lever capped at
# 1.5, plus wind (|(0.42, 0.19)| = 0.461 per unit of height). Contact press is
# a downward 0.75 fraction of the clump height, so the hierarchy pad includes
# that vertical extent as well as horizontal motion. The pad is intentionally
# uniform in all axes because BLAS nodes have one conservative AABB contract.
MEADOW_MAX_CONTACT_THROW = 1.0
MEADOW_MAX_CONTACT_LEVER = 1.5
MEADOW_WIND_AMPLITUDE = (0.42 ** 2 + 0.19 ** 2) ** 0.5
MEADOW_CLUMP_HEIGHT_MIN = 1.1
MEADOW_CLUMP_HEIGHT_MAX = 2.0
MEADOW_MAX_VERTICAL_PRESS = 0.75 * MEADOW_CLUMP_HEIGHT_MAX
MEADOW_BLAS_PAD = round(
    max(MEADOW_MAX_CONTACT_THROW * MEADOW_MAX_CONTACT_LEVER
        + MEADOW_WIND_AMPLITUDE * MEADOW_CLUMP_HEIGHT_MAX,
        MEADOW_MAX_VERTICAL_PRESS) + 0.05, 2)

# FL-4359/T1, from the Ghost of Tsushima intake
# articles/2026-09-09-godot-vegetation-technique-transcripts-media/G8HH_pMKOhk/
# MEDIA.md sect. 9.4 and 16.2: "real grass isn't simply just random heights ...
# it grows in clumps ... the soil most likely having a higher nitrogen count in
# the area, which can even affect the color of the grass as well", mimicked with
# "a procedural noise algorithm to collect grass blades in an area into clumps
# and influence their various parameters like height color and direction".
#
# The rule is that randomness lives at the CLUMP level and that ONE noise sample
# moves several channels together. Independent per-site draws from one seed --
# what this compiler did before -- are the naive version the intake calls out.
# One world-anchored field value therefore drives height, ink value and direction
# for every site that falls inside it, so neighbouring sites share tendencies and
# the tendency varies smoothly across the meadow instead of per site.
#
# 6.0 world units per field cell: sites inside one nucleus sit within ~1.7 units
# of each other and correlate above 0.9; the correlation reaches ~0.5 at 4 units
# and ~0 past 12, which is the "clump" scale and not a global gradient.
MEADOW_CLUMP_FIELD_SPAN = 6.0

# The authored Y8 tree assets, by the exact `mesh_name` the A3D instance rows
# carry.  This list restates the FL-4708 placement compiler's admitted set; a
# manifest row naming anything else is a source defect, not a fallback case.
CANONICAL_TREE_MESHES = (
    "tree-1.akm",
    "tree-2.akm",
    "tree-3.akm",
    "old-tree-1.akm",
    "old-tree-2.akm",
    "osm-tree-pole.akm",
)

# A3D stores horizontal X/Y in visual cells (1 cell == 1 Godot metre) but stores
# height in 1/16-cell steps (engine/terrain.h: HEIGHT_SCALE 16).  Every authored
# vertical quantity therefore divides by this before it becomes Godot metres,
# exactly as the FL-4708 placement compiler already does for the instance origin.
A3D_HEIGHT_SCALE = 16.0

# Historical Y8 asset-normalization scales, restated from the FL-4708 placement
# compiler.  Multiplying a raw mesh axis by these factors gives the "scale 1.0"
# authored asset (horizontal factor in cells, vertical factor in 1/16 cells).
# The 179 authored placements do NOT read this table: each carries its own full
# A3D basis, which already includes these factors.  Only the three FL-4708
# lab-grove anchors need it, because they have no authored basis at all.
TREE_ASSET_NORMALIZATION = {
    "tree-1.akm": {"xy": 0.36, "z": 4.85},
    "tree-2.akm": {"xy": 0.30, "z": 7.00},
    "tree-3.akm": {"xy": 0.95, "z": 26.00},
    "old-tree-1.akm": {"xy": 0.28, "z": 7.55},
    "old-tree-2.akm": {"xy": 0.32, "z": 7.70},
}

# The three FL-4708 laboratory anchors are synthetic placements with no authored
# A3D instance.  They still must draw authored geometry, so each grove slot takes
# one canonical mesh by its own index.  Deterministic and order-stable.
LAB_GROVE_MESHES = ("tree-1.akm", "tree-2.akm", "tree-3.akm")

# --- Authored-mesh to retained-material rules -------------------------------
# A face is canopy when its averaged authored vertex colour is leaf-dominant AND
# its centroid sits at or above the bark line.  The two conditions are ANDed
# because they answer different failure modes:
#   * colour alone fails on tree-1.akm, whose authored colours are near-black at
#     the base and carry no usable bark contrast, so every triangle would read
#     as canopy and that tree would have no trunk at all.
#   * height alone is the colour-blind rule this repo's other .akm baker uses
#     (bake_akm_to_static_world_faces.py "tree" mode, same 0.32 line), and it
#     would paint old-tree-1/old-tree-2 -- bare dead trees whose every authored
#     vertex is bark brown -- with a canopy they do not have.
# On tree-2/tree-3/osm-tree-pole the height term is inert by measurement: their
# leaf-dominant faces all start above height fraction 0.37.
AKM_TREE_BARK_LINE = 0.32
# Canopy role split.  cell_owner_product.glsl treats 241 as the sparse outer
# shell, 242 as the dense inner mass, and 243 as the dark underside, and its
# comment states 241 must mean "faces away from the crown centre".  These
# thresholds reproduce that fact from authored geometry: the outward-oriented
# face normal, the face's distance from the crown centre, and its alignment with
# the outward direction.  0.36 is the same alignment the deleted lobe generator
# used for its own contour sectors, so the Product's contour stroke density is
# not silently re-tuned by this change.
AKM_CANOPY_UNDERSIDE_NORMAL_UP = -0.25
AKM_CANOPY_CONTOUR_RADIUS_FRACTION = 0.45
AKM_CANOPY_CONTOUR_ALIGNMENT = 0.36

# Per-role base shade, carried in the face ABI's second metadata lane.  These are
# the exact values the deleted generator published for the same four roles, so
# the Product's 16-band shade quantisation keeps its established range.
TREE_TRUNK_SHADE = 0.78
TREE_CANOPY_CONTOUR_SHADE = 0.86
TREE_CANOPY_INTERIOR_SHADE = 0.74
TREE_CANOPY_UNDERSIDE_SHADE = 0.54
# Authored colour luminance modulates the role shade downward only, inside a
# bounded band.  A mesh painted one flat colour (old-tree-1, old-tree-2) lands
# exactly on its role base; a mesh with authored light/dark variation keeps that
# variation as shade-band variety instead of discarding it.
AKM_SHADE_LUMA_FLOOR = 0.86
AKM_SHADE_LUMA_SPAN = 0.14


# Process-lifetime caches.  Both are pure functions of on-disk bytes, so they
# only avoid re-reading and re-classifying the same six mesh files 182 times;
# they never carry state between two different source trees.
_AKM_READER: Any = None
_MESH_CACHE: dict[str, dict[str, Any]] = {}


class CompileError(ValueError):
    """A source or generated-data contract failed; no catalog is emitted."""


class CompiledCatalog(dict[str, Any]):
    """Dict-compatible catalog carrying the sibling payload produced with it."""

    def __init__(self, value: dict[str, Any], payload_bytes: bytes, payload_path: Path):
        super().__init__(value)
        self.payload_bytes = payload_bytes
        self.payload_path = payload_path


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def _v_sub(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _v_mul(a: tuple[float, float, float], scalar: float) -> tuple[float, float, float]:
    return (a[0] * scalar, a[1] * scalar, a[2] * scalar)


def _v_cross(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _v_dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _v_normalized(a: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(_v_dot(a, a))
    if length <= 1.0e-12:
        return (0.0, 0.0, 0.0)
    return _v_mul(a, 1.0 / length)


def _hash_unit(i: int, salt: int) -> float:
    # This is the direct integer form of World._static_world_hash_unit.
    n = int((i + 1) * 1103515245 + salt * 12345)
    n = ((n >> 16) ^ n) & 0x7FFFFFFF
    return float(n % 10000) / 10000.0


def _hash_unit32(i: int, salt: int) -> float:
    """FL-4359 grass-fields S1: `_hash_unit` in 32-bit wrap-around arithmetic.

    The clump field is now evaluated in TWO places from the same root: here,
    to author the card geometry, and in vegetation_deformation.glslinc
    (`vegetation_clump_traits`), to paint, shade and bend it. `_hash_unit` is
    unbounded Python integer math, and a 32-bit GLSL port of it disagrees on
    almost every lattice corner (6,390 of 6,400 measured). This form wraps every
    product and sum at 2^32 with a logical shift, which is exactly what `uint`
    arithmetic does, so the shader can reproduce it bit for bit. `_hash_unit`
    itself is untouched: its other consumers (site scatter, arm jitter) are
    compile-time only and never cross into a shader.
    """
    n = ((i + 1) * 1103515245 + salt * 12345) & 0xFFFFFFFF
    n = ((n >> 16) ^ n) & 0x7FFFFFFF
    return float(n % 10000) / 10000.0


def _meadow_clump_field(x: float, z: float) -> float:
    """FL-4359/T1: one smooth, world-anchored clump value in [0, 1].

    Bilinear value noise on a MEADOW_CLUMP_FIELD_SPAN lattice with a smoothstep
    interpolant. It is keyed on WORLD POSITION, never on a site seed, so two
    sites that happen to share a seed but sit apart get different values while
    two sites that sit together get nearly the same one. That is the property
    that makes a clump a clump.
    """
    u = x / MEADOW_CLUMP_FIELD_SPAN
    v = z / MEADOW_CLUMP_FIELD_SPAN
    cell_u = math.floor(u)
    cell_v = math.floor(v)
    su = (u - cell_u) * (u - cell_u) * (3.0 - 2.0 * (u - cell_u))
    sv = (v - cell_v) * (v - cell_v) * (3.0 - 2.0 * (v - cell_v))

    def corner(du: int, dv: int) -> float:
        # FL-4359 grass-fields S1: every product is wrapped at 2^32 so the key
        # matches `uint(cell) * 73856093u ^ uint(cell) * 19349663u` in GLSL.
        key = (((cell_u + du) * 73856093) & 0xFFFFFFFF) \
            ^ (((cell_v + dv) * 19349663) & 0xFFFFFFFF)
        return _hash_unit32(key, 4359)

    top = corner(0, 0) + (corner(1, 0) - corner(0, 0)) * su
    bottom = corner(0, 1) + (corner(1, 1) - corner(0, 1)) * su
    return top + (bottom - top) * sv


def meadow_clump_traits_from_field(field: float) -> dict[str, float]:
    """Build every meadow trait from one already-sampled clump value.

    Placement admission and visual traits must share this exact sample. Keeping
    the conversion separate lets the catalog compiler sample once per candidate
    and makes a sparse patch, its height, value and comb one clump decision.

    Intake sect. 9.4 names height, colour and direction. Mapped onto this owner:

    * ``rise``  -- the mat crest above the planted root. This is the height
      channel, and it is also the rendered VALUE channel, because
      cell_owner_product.glsl scales the meadow ink by
      ``mix(0.82, 1.26, smoothstep(0.0, 0.42, delta.y))``. One field value
      therefore moves height and ink value together, which is the correlation
      the intake is about.
    * ``shade`` -- the authored shade lane. Kept correlated with the same field
      so the two channels can never diverge. CAVEAT, measured not assumed: for
      material 255 this compiler immediately overwrites lane 7 with ``root.x``
      (the retained face ABI spends all three spare lanes on the local root), so
      today this value reaches no consumer. It is written correlated anyway; if
      the ABI ever frees a lane, it is already right rather than random.
    * ``heading``/``lean``/``comb`` -- the direction channel. The whole clump
      shares one base fan rotation and one comb direction, so arms pointing with
      the clump's lean reach further and arms against it stay short. Neighbouring
      patches therefore comb the same way instead of each choosing its own.

    Colour is a VALUE move only. No hue is authored here, and no per-site colour
    exists at all: the ink hue comes from the root cell's terrain paper in
    ``product_meadow_paint_pair``. Intake sect. 16.4 item 1 forbids a per-blade
    hue, and the visual contract requires flat FG per cell.
    """
    heading = math.tau * field
    return {
        "field": field,
        "rise": 0.17 + 0.30 * field,
        "shade": 0.42 + 0.46 * field,
        "heading": heading,
        "lean_x": math.cos(heading),
        "lean_z": math.sin(heading),
        "comb": 0.10 + 0.22 * field,
        # Centred on 1.0 at field 0.5 so the clump tendency redistributes reach
        # without moving the meadow's mean strip length or its face budget.
        "reach_scale": 1.12 - 0.24 * field,
        # FL-4359 grass-fields S2: the ribbon height of the clump's field card.
        # The shader recomputes this from the root (vegetation_clump_traits) to
        # normalise its height ramp and wind lever, so it must stay a function
        # of the one field value and nothing else.
        "height": MEADOW_CLUMP_HEIGHT_MIN
        + (MEADOW_CLUMP_HEIGHT_MAX - MEADOW_CLUMP_HEIGHT_MIN) * field,
        # A per-clump lag on the shared wind scroll: neighbouring clumps reach
        # the same travelling crest a little apart instead of in lock-step.
        "phase_lag": 0.60 * field,
    }


def meadow_clump_traits(x: float, z: float) -> dict[str, float]:
    """Return traits from the world-anchored clump value at this root."""
    return meadow_clump_traits_from_field(_meadow_clump_field(x, z))


def _append_face(
    rows: list[float],
    p0: tuple[float, float, float],
    p1: tuple[float, float, float],
    p2: tuple[float, float, float],
    p3: tuple[float, float, float],
    mat_id: int,
    shade: float,
) -> None:
    # SceneGeometryDB's 16-float face ABI stores material and shade in the
    # first two vertex metadata lanes; the remaining lanes are zero.
    rows.extend(
        [
            p0[0], p0[1], p0[2], float(mat_id),
            p1[0], p1[1], p1[2], shade,
            p2[0], p2[1], p2[2], 0.0,
            p3[0], p3[1], p3[2], 0.0,
        ]
    )


def _load_akm_reader() -> Any:
    """Import the shared ASCII-PLY reader as a module, once."""
    global _AKM_READER
    if _AKM_READER is None:
        spec = importlib.util.spec_from_file_location(
            "fl4789_akm_ply_reader", AKM_READER_PATH
        )
        if spec is None or spec.loader is None:
            raise CompileError(f"cannot load the shared AKM reader: {AKM_READER_PATH}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _AKM_READER = module
    return _AKM_READER


def _colour_luma(colour: tuple[float, float, float]) -> float:
    """Rec.709 relative luminance of one 0..255 authored vertex colour."""
    return (
        0.2126 * colour[0] + 0.7152 * colour[1] + 0.0722 * colour[2]
    ) / 255.0


def _classify_tree_mesh(
    positions: list[tuple[float, float, float]],
    colours: list[tuple[float, float, float]],
    triangles: list[tuple[int, int, int]],
) -> tuple[list[int], list[float], dict[str, int]]:
    """Choose one retained material and shade per authored triangle.

    Everything here happens in the mesh's own authored space, which is Z-up (the
    Blender convention these .akm files were exported from).  Classifying in mesh
    space rather than in placement space means every instance of one asset shares
    the same per-face roles, so the roles cannot wobble with a placement's yaw,
    scale, or basis handedness.
    """
    if not triangles:
        raise CompileError("authored tree mesh has no triangles")
    z_values = [point[2] for point in positions]
    z_min = min(z_values)
    z_span = max(max(z_values) - z_min, 1.0e-6)

    centroids: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] = []
    lumas: list[float] = []
    is_canopy: list[bool] = []
    for i0, i1, i2 in triangles:
        p0, p1, p2 = positions[i0], positions[i1], positions[i2]
        centroid = (
            (p0[0] + p1[0] + p2[0]) / 3.0,
            (p0[1] + p1[1] + p2[1]) / 3.0,
            (p0[2] + p1[2] + p2[2]) / 3.0,
        )
        colour = (
            (colours[i0][0] + colours[i1][0] + colours[i2][0]) / 3.0,
            (colours[i0][1] + colours[i1][1] + colours[i2][1]) / 3.0,
            (colours[i0][2] + colours[i1][2] + colours[i2][2]) / 3.0,
        )
        # Leaf-dominant means the middle authored channel outweighs both of the
        # outer channels: foliage in these assets is painted olive/leaf, bark is
        # painted red-dominant brown.
        leaf_dominant = colour[1] > max(colour[0], colour[2])
        height_fraction = (centroid[2] - z_min) / z_span
        centroids.append(centroid)
        lumas.append(_colour_luma(colour))
        normals.append(
            _v_normalized(_v_cross(_v_sub(p1, p0), _v_sub(p2, p0)))
        )
        is_canopy.append(leaf_dominant and height_fraction >= AKM_TREE_BARK_LINE)

    # Crown centre and crown radius are measured from the canopy faces only, so a
    # long bare trunk cannot drag the centre downward and mislabel the contour.
    canopy_indices = [index for index, flag in enumerate(is_canopy) if flag]
    crown_center = (0.0, 0.0, 0.0)
    crown_radius = 0.0
    if canopy_indices:
        inverse_count = 1.0 / float(len(canopy_indices))
        crown_center = (
            sum(centroids[index][0] for index in canopy_indices) * inverse_count,
            sum(centroids[index][1] for index in canopy_indices) * inverse_count,
            sum(centroids[index][2] for index in canopy_indices) * inverse_count,
        )
        crown_radius = max(
            math.hypot(
                centroids[index][0] - crown_center[0],
                centroids[index][1] - crown_center[1],
            )
            for index in canopy_indices
        )

    max_luma = max(lumas)
    face_materials: list[int] = []
    face_shades: list[float] = []
    role_counts = {"trunk": 0, "canopy_contour": 0, "canopy_interior": 0, "canopy_underside": 0}
    for index in range(len(triangles)):
        if not is_canopy[index]:
            material = TREE_TRUNK_MAT
            base_shade = TREE_TRUNK_SHADE
            role_counts["trunk"] += 1
        else:
            outward = _v_sub(centroids[index], crown_center)
            normal = normals[index]
            # Authored winding is not guaranteed consistent, and the runtime
            # flips a hit normal back toward the incoming ray anyway, so the role
            # is decided here from an explicitly outward-oriented normal.
            if _v_dot(normal, outward) < 0.0:
                normal = _v_mul(normal, -1.0)
            radial_length = math.hypot(outward[0], outward[1])
            horizontal_length = math.hypot(normal[0], normal[1])
            alignment = 0.0
            if radial_length > 1.0e-6 and horizontal_length > 1.0e-6:
                alignment = (
                    normal[0] * outward[0] + normal[1] * outward[1]
                ) / (radial_length * horizontal_length)
            if normal[2] <= AKM_CANOPY_UNDERSIDE_NORMAL_UP:
                material = TREE_CANOPY_UNDERSIDE_MAT
                base_shade = TREE_CANOPY_UNDERSIDE_SHADE
                role_counts["canopy_underside"] += 1
            elif (
                radial_length >= AKM_CANOPY_CONTOUR_RADIUS_FRACTION * crown_radius
                and alignment >= AKM_CANOPY_CONTOUR_ALIGNMENT
            ):
                material = TREE_CANOPY_CONTOUR_MAT
                base_shade = TREE_CANOPY_CONTOUR_SHADE
                role_counts["canopy_contour"] += 1
            else:
                material = TREE_CANOPY_INTERIOR_MAT
                base_shade = TREE_CANOPY_INTERIOR_SHADE
                role_counts["canopy_interior"] += 1
        luma_ratio = 1.0 if max_luma <= 0.0 else lumas[index] / max_luma
        shade = base_shade * (AKM_SHADE_LUMA_FLOOR + AKM_SHADE_LUMA_SPAN * luma_ratio)
        face_materials.append(material)
        face_shades.append(_f32(min(max(shade, 0.0), 1.0)))
    return face_materials, face_shades, role_counts


def load_tree_mesh(mesh_name: str, mesh_root: Path = DEFAULT_MESH_ROOT) -> dict[str, Any]:
    """Read one authored .akm tree mesh and its per-face retained materials."""
    if mesh_name not in CANONICAL_TREE_MESHES:
        raise CompileError(f"tree mesh {mesh_name!r} is not an authored Y8 tree asset")
    path = (mesh_root / mesh_name).resolve()
    cache_key = f"{path}"
    cached = _MESH_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if not path.is_file():
        raise CompileError(f"authored tree mesh is missing: {path}")
    verts, faces = _load_akm_reader().parse_akm_ply(str(path))
    if not verts or not faces:
        raise CompileError(f"authored tree mesh has no geometry: {path}")
    positions = [tuple(float(value) for value in vert["pos"]) for vert in verts]
    colours = [tuple(float(value) for value in vert["col"]) for vert in verts]
    triangles = [tuple(int(index) for index in face) for face in faces]
    if len(triangles) > MAX_FACES:
        raise CompileError(
            f"authored tree mesh {mesh_name} has {len(triangles)} faces, above the "
            f"{MAX_FACES} BLAS leaf domain"
        )
    face_materials, face_shades, role_counts = _classify_tree_mesh(
        positions, colours, triangles
    )
    mesh = {
        "mesh_name": mesh_name,
        "path": path,
        "sha256": _sha256(path.read_bytes()),
        "positions": positions,
        "colours": colours,
        "triangles": triangles,
        "face_materials": face_materials,
        "face_shades": face_shades,
        "face_count": len(triangles),
        "role_counts": role_counts,
    }
    _MESH_CACHE[cache_key] = mesh
    return mesh


def _authored_local_columns(
    transform: list[float], yaw: float, scale: float
) -> list[tuple[float, float, float]]:
    """Return the placement-LOCAL Godot basis columns of one authored instance.

    The A3D instance transform maps mesh-local coordinates straight into A3D
    world space, whose axes convert to Godot as
    (a3d_x, a3d_y, a3d_z) -> (a3d_x, a3d_z / 16, a3d_y).
    That is exactly the convention the manifest's own origin fields already use
    (world_xz from A3D X/Y, authored_godot_y from A3D Z / 16).

    The retained runtime rebuilds a world face as
    origin + Basis(yaw) * (local_row * procedural_scale).
    So the local rows this compiler must publish are the authored offsets with
    that yaw and that uniform scale divided back out.  The authored basis is NOT
    uniform -- its horizontal and vertical scales differ per instance, and the
    manifest's `procedural_scale` recovers only the vertical one -- so folding
    the remaining, per-instance part into the immutable local rows is what keeps
    every tree at its authored footprint without a runtime placement ABI change.
    """
    cosine = math.cos(yaw)
    sine = math.sin(yaw)
    inverse_scale = 1.0 / scale
    columns: list[tuple[float, float, float]] = []
    for column_index in range(3):
        a3d_x = transform[column_index * 4 + 0]
        a3d_y = transform[column_index * 4 + 1]
        a3d_z = transform[column_index * 4 + 2]
        godot_x = a3d_x
        godot_y = a3d_z / A3D_HEIGHT_SCALE
        godot_z = a3d_y
        # Inverse yaw about Godot +Y, then remove the uniform placement scale.
        columns.append(
            (
                (cosine * godot_x + sine * godot_z) * inverse_scale,
                godot_y * inverse_scale,
                (-sine * godot_x + cosine * godot_z) * inverse_scale,
            )
        )
    return columns


def _normalized_asset_local_columns(mesh_name: str) -> list[tuple[float, float, float]]:
    """Return the scale-1.0 authored asset basis for a placement with no A3D row.

    Mesh-local X and Y are horizontal (Godot X and Z); mesh-local Z is the
    authored up axis and converts through the same 1/16 height step.
    """
    normalization = TREE_ASSET_NORMALIZATION.get(mesh_name)
    if normalization is None:
        raise CompileError(
            f"tree mesh {mesh_name!r} has no Y8 asset normalization for a "
            "placement without an authored A3D basis"
        )
    horizontal = float(normalization["xy"])
    vertical = float(normalization["z"]) / A3D_HEIGHT_SCALE
    return [
        (horizontal, 0.0, 0.0),
        (0.0, 0.0, horizontal),
        (0.0, vertical, 0.0),
    ]


def _mesh_point_to_local(
    point: tuple[float, float, float],
    columns: list[tuple[float, float, float]],
) -> tuple[float, float, float]:
    return (
        columns[0][0] * point[0] + columns[1][0] * point[1] + columns[2][0] * point[2],
        columns[0][1] * point[0] + columns[1][1] * point[1] + columns[2][1] * point[2],
        columns[0][2] * point[0] + columns[1][2] * point[1] + columns[2][2] * point[2],
    )


def build_tree_local_rows(
    tree: dict[str, Any], mesh_root: Path = DEFAULT_MESH_ROOT
) -> list[float]:
    """Emit one authored .akm tree mesh as retained rows in placement-local space.

    `tree` is a compiled placement record.  An authored Y8 row carries its full
    A3D basis; the FL-4708 lab-grove rows carry none and fall back to the
    normalized scale-1.0 asset.  Nothing in this function invents geometry: the
    face count, the vertex positions, and the material roles all come from the
    mesh file.
    """
    mesh_name = str(tree["source_mesh_name"])
    mesh = load_tree_mesh(mesh_name, mesh_root)
    transform = tree.get("authored_transform_column_major_f64")
    if transform is None:
        columns = _normalized_asset_local_columns(mesh_name)
    else:
        if not isinstance(transform, list) or len(transform) != 16:
            raise CompileError(
                f"tree {tree.get('instance_or_source_id')} has a malformed authored transform"
            )
        columns = _authored_local_columns(
            [float(value) for value in transform],
            float(tree["yaw_radians"]),
            float(tree["procedural_scale"]),
        )
    rows: list[float] = []
    positions = mesh["positions"]
    colours = mesh["colours"]
    canopy_vertices = {
        vertex for face, material in zip(mesh["triangles"], mesh["face_materials"])
        if TREE_CANOPY_CONTOUR_MAT <= material <= TREE_CANOPY_UNDERSIDE_MAT
        for vertex in face
    }
    crown_points = [positions[index] for index in canopy_vertices] or positions
    crown_center = tuple(sum(point[axis] for point in crown_points) / len(crown_points)
                         for axis in range(3))
    bark_vertices = {
        vertex for face, material in zip(mesh["triangles"], mesh["face_materials"])
        if material == TREE_TRUNK_MAT for vertex in face
    }
    bark_points = [positions[index] for index in bark_vertices] or positions
    bark_center = tuple(sum(point[axis] for point in bark_points) / len(bark_points)
                        for axis in range(3))
    for face_index, (i0, i1, i2) in enumerate(mesh["triangles"]):
        p0 = _mesh_point_to_local(positions[i0], columns)
        p1 = _mesh_point_to_local(positions[i1], columns)
        p2 = _mesh_point_to_local(positions[i2], columns)
        # The retained face ABI is a quad; an authored triangle repeats its last
        # corner, which is the same degenerate-quad convention the lighthouse,
        # statue, and airframe bakes already use.
        _append_face(
            rows,
            p0,
            p1,
            p2,
            p2,
            mesh["face_materials"][face_index],
            mesh["face_shades"][face_index],
        )
        # FL-4708: preserve authored face color and outward shading orientation
        # in the existing metadata lanes. Geometry and the capture-facing hit
        # normal remain unchanged; neither probe position nor camera yaw owns
        # the tree's lighting normal. RGB24 is exactly representable in float32.
        # FL-4359: parse_akm_ply preserves byte channels, not normalized RGB.
        rgb = [int(math.floor(min(255.0, max(0.0,
               sum(colours[index][channel] for index in (i0, i1, i2)) / 3.0))
               + 0.5)) for channel in range(3)]
        centroid = tuple(sum(positions[index][axis] for index in (i0, i1, i2)) / 3.0
                         for axis in range(3))
        if mesh["face_materials"][face_index] == TREE_TRUNK_MAT:
            # FL-4708: the requested painted bark ramp is height-authored,
            # independent of sun exposure and of the grey source vertex RGB.
            bottom = min(point[2] for point in bark_points)
            top = max(point[2] for point in bark_points)
            height = min(1.0, max(0.0, (centroid[2] - bottom) / max(top - bottom, 0.01)))
            stops = ((0.30, 0.46, 0.20), (0.76, 0.38, 0.16), (0.43, 0.24, 0.47))
            lo, hi, weight = (stops[0], stops[1], height * 2) if height < 0.5 else (stops[1], stops[2], height * 2 - 1)
            rgb = [round((lo[c] + (hi[c] - lo[c]) * weight) * 255) for c in range(3)]
            outward = (centroid[0] - bark_center[0], centroid[1] - bark_center[1], 0.0)
        else:
            outward = _v_sub(centroid, crown_center)
        local_outward = _mesh_point_to_local(outward, columns)
        local_normal = _v_cross(_v_sub(p1, p0), _v_sub(p2, p0))
        rows[-5] = float(rgb[0] | (rgb[1] << 8) | (rgb[2] << 16))
        # FL-4359: grow from the nearest authored bark attachment toward this
        # canopy face. The shader receives growth direction, not a world-grid motif.
        attachment = min(bark_points, key=lambda p: sum((p[a] - centroid[a]) ** 2 for a in range(3)))
        growth = _mesh_point_to_local(_v_sub(centroid, attachment), columns)
        azimuth = math.atan2(growth[2], growth[0]) % math.tau
        elevation = math.atan2(growth[1], max(math.hypot(growth[0], growth[2]), 1e-6))
        direction_code = min(255, int(azimuth / math.tau * 256)) | (min(7, max(0, round((elevation / math.pi + 0.5) * 7))) << 8)
        if mesh["face_materials"][face_index] == TREE_TRUNK_MAT \
                and direction_code == FIXTURE_GROWTH_CODE:
            # FL-4902 lane TB: bark never carries the fixture code.
            direction_code ^= 1
        tag = 4708.0 + direction_code / 2048.0
        rows[-1] = -tag if _v_dot(local_normal, local_outward) < 0.0 else tag
    face_count = len(rows) // FLOATS_PER_FACE
    if face_count != int(mesh["face_count"]):
        raise CompileError(
            f"tree mesh {mesh_name} emitted {face_count} faces, expected "
            f"{mesh['face_count']}"
        )
    return [_f32(value) for value in rows]


def _rows_bytes(rows: Iterable[float]) -> bytes:
    values = list(rows)
    if len(values) % FLOATS_PER_FACE != 0:
        raise CompileError("face rows are not a multiple of 16 floats")
    return struct.pack("<%df" % len(values), *(_f32(value) for value in values))


def _rows_aabb(rows: list[float]) -> tuple[list[float], list[float]]:
    if len(rows) == 0 or len(rows) % FLOATS_PER_FACE != 0:
        raise CompileError("cannot compute AABB for malformed face rows")
    mins = [float("inf")] * 3
    maxs = [float("-inf")] * 3
    for face_offset in range(0, len(rows), FLOATS_PER_FACE):
        for corner in range(4):
            offset = face_offset + corner * 4
            for axis in range(3):
                value = _f32(rows[offset + axis])
                mins[axis] = min(mins[axis], value)
                maxs[axis] = max(maxs[axis], value)
    if any(int(rows[i + 3]) == MEADOW_BLADE_MAT for i in range(0, len(rows), 16)):
        mins = [mins[0] - 2.5, mins[1] - 1.5, mins[2] - 2.5]
        maxs = [maxs[0] + 2.5, maxs[1], maxs[2] + 2.5]
    return [[_f32(value) for value in mins], [_f32(value) for value in maxs]]


def transform_tree_rows(
    rows: list[float],
    world_xz: list[float],
    world_y: float,
    yaw: float,
    scale: float,
) -> list[float]:
    """Apply the same origin + yaw basis * (local * scale) flattening as SceneGeometryDB."""
    cosine = math.cos(yaw)
    sine = math.sin(yaw)
    transformed: list[float] = []
    for face_offset in range(0, len(rows), FLOATS_PER_FACE):
        for corner in range(4):
            offset = face_offset + corner * 4
            local_x = rows[offset]
            local_y = rows[offset + 1]
            local_z = rows[offset + 2]
            transformed.extend(
                [
                    _f32(world_xz[0] + (cosine * local_x - sine * local_z) * scale),
                    _f32(world_y + local_y * scale),
                    _f32(world_xz[1] + (sine * local_x + cosine * local_z) * scale),
                    rows[offset + 3],
                ]
            )
    return transformed


def load_fixture_mesh(mesh_name: str, mesh_root: Path = DEFAULT_MESH_ROOT) -> dict[str, Any]:
    """Read one authored non-tree .akm mesh (FL-4798 fixture) without a role split."""
    if not mesh_name or "/" in mesh_name or not mesh_name.endswith(".akm"):
        raise CompileError(f"fixture mesh name is not an .akm file name: {mesh_name!r}")
    path = (mesh_root / mesh_name).resolve()
    cache_key = f"fixture:{path}"
    cached = _MESH_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if not path.is_file():
        raise CompileError(f"authored fixture mesh is missing: {path}")
    verts, faces = _load_akm_reader().parse_akm_ply(str(path))
    if not verts or not faces:
        raise CompileError(f"authored fixture mesh has no geometry: {path}")
    positions = [tuple(float(value) for value in vert["pos"]) for vert in verts]
    colours = [tuple(float(value) for value in vert["col"]) for vert in verts]
    triangles = [tuple(int(index) for index in face) for face in faces]
    if len(triangles) > MAX_FACES:
        raise CompileError(
            f"authored fixture mesh {mesh_name} has {len(triangles)} faces, above the "
            f"{MAX_FACES} BLAS leaf domain"
        )
    mesh = {
        "mesh_name": mesh_name,
        "path": path,
        "sha256": _sha256(path.read_bytes()),
        "positions": positions,
        "colours": colours,
        "triangles": triangles,
        "face_count": len(triangles),
    }
    _MESH_CACHE[cache_key] = mesh
    return mesh


def build_fixture_local_rows(
    fixture: dict[str, Any], mesh_root: Path = DEFAULT_MESH_ROOT
) -> list[float]:
    """Emit one authored fixture .akm as retained rows in placement-local space.

    The placement keeps the full authored A3D basis (folded into the local rows
    exactly as authored trees do), a unit runtime scale, and the authored yaw.
    Every face carries AUTHORED_FIXTURE_MAT, the mean authored vertex RGB in
    the colour lane, and the 4708 colour tag whose sign records whether the
    face winds outward from the mesh centre, so the Product's authored-colour
    paint path lights it like any other retained authored face.
    """
    mesh_name = str(fixture["source_mesh_name"])
    mesh = load_fixture_mesh(mesh_name, mesh_root)
    transform = fixture.get("authored_transform_column_major_f64")
    if not isinstance(transform, list) or len(transform) != 16:
        raise CompileError(
            f"fixture {fixture.get('instance_or_source_id')} has a malformed authored transform"
        )
    columns = _authored_local_columns(
        [float(value) for value in transform], float(fixture["yaw_radians"]), 1.0
    )
    positions = mesh["positions"]
    colours = mesh["colours"]
    centre = tuple(sum(point[axis] for point in positions) / len(positions) for axis in range(3))
    # Neutral growth code: azimuth 0, level elevation.  Fixtures have no bark
    # attachment; the lane must still parse as a 4708 tag for the colour path.
    # FL-4902 lane TB: this code is also the fixture half of the tree bit.
    tag = 4708.0 + FIXTURE_GROWTH_CODE / 2048.0
    rows: list[float] = []
    for i0, i1, i2 in mesh["triangles"]:
        p0 = _mesh_point_to_local(positions[i0], columns)
        p1 = _mesh_point_to_local(positions[i1], columns)
        p2 = _mesh_point_to_local(positions[i2], columns)
        _append_face(rows, p0, p1, p2, p2, AUTHORED_FIXTURE_MAT, AUTHORED_FIXTURE_SHADE)
        rgb = [int(math.floor(min(255.0, max(0.0,
               sum(colours[index][channel] for index in (i0, i1, i2)) / 3.0))
               + 0.5)) for channel in range(3)]
        centroid = tuple(sum(positions[index][axis] for index in (i0, i1, i2)) / 3.0
                         for axis in range(3))
        local_outward = _mesh_point_to_local(_v_sub(centroid, centre), columns)
        local_normal = _v_cross(_v_sub(p1, p0), _v_sub(p2, p0))
        rows[-5] = float(rgb[0] | (rgb[1] << 8) | (rgb[2] << 16))
        rows[-1] = -tag if _v_dot(local_normal, local_outward) < 0.0 else tag
    face_count = len(rows) // FLOATS_PER_FACE
    if face_count != int(mesh["face_count"]):
        raise CompileError(
            f"fixture mesh {mesh_name} emitted {face_count} faces, expected {mesh['face_count']}"
        )
    return [_f32(value) for value in rows]


def _load_fixture_records(
    path: Path, decoded: dict[str, Any], mesh_root: Path = DEFAULT_MESH_ROOT
) -> list[dict[str, Any]]:
    """FL-4798: every non-tree mesh instance of the manifest, placed at its authored basis.

    The manifest's `other_akm_records` are the A3D world rows the tree lane
    declines.  Each becomes one compiled placement: origin X/Y minus the map's
    centre offset -> Godot X/Z, origin Z / 16 -> Godot Y, yaw from the authored
    local-X column, unit runtime scale (the basis is folded into the rows).
    A row naming a mesh that is not on disk refuses to compile: the fixture
    exists in the map, so a silent omission would misreport the restore.
    """
    value = json.loads(path.read_bytes())
    if value.get("$schema") != "fl4708_y8_tree_instances.v1":
        raise CompileError(f"unexpected authored-tree schema: {path}")
    records = value.get("other_akm_records")
    if not isinstance(records, list):
        raise CompileError("authored manifest carries no other_akm_records list")
    center_x, center_y = decoded["center_offset"]
    compiled: list[dict[str, Any]] = []
    seen: set[int] = set()
    for record in records:
        source_id = int(record.get("source_instance_index", -1))
        transform = record.get("authored_transform_column_major_f64")
        if source_id < 0 or source_id in seen:
            raise CompileError(f"invalid or duplicate authored fixture id {source_id}")
        if not isinstance(transform, list) or len(transform) != 16:
            raise CompileError(f"fixture {source_id} lacks authored transform")
        seen.add(source_id)
        mesh_name = str(record.get("source_mesh_name", ""))
        face_count = int(load_fixture_mesh(mesh_name, mesh_root)["face_count"])
        tm = [float(v) for v in transform]
        compiled.append(
            {
                "instance_or_source_id": FIXTURE_SOURCE_ID_BASE + source_id,
                "source_instance_index": source_id,
                "instance_identity": str(record.get("instance_identity", "")),
                "source_kind": FIXTURE_SOURCE_KIND,
                "source_mesh_name": mesh_name,
                "source_instance_name": str(record.get("source_instance_name", "")),
                "authored_geometry_variant": mesh_name,
                "authored_transform_column_major_f64": tm,
                "world_xz": [tm[12] - center_x, tm[13] - center_y],
                "authored_godot_y": tm[14] / A3D_HEIGHT_SCALE,
                "yaw_radians": math.atan2(tm[1], tm[0]),
                "procedural_scale": 1.0,
                "face_count": face_count,
                "authored_face_count": face_count,
                "root_ecology_face_count": 0,
                "blas_leaf_count": face_count,
                "primitive_id_begin": 0,
                "primitive_id_end_exclusive": face_count,
            }
        )
    compiled.sort(key=lambda row: int(row["instance_or_source_id"]))
    return compiled


def _build_blas(rows: list[float]) -> tuple[bytes, bytes, int]:
    face_count = len(rows) // FLOATS_PER_FACE
    if face_count <= 0 or face_count > MAX_FACES:
        raise CompileError(f"unsupported face count {face_count}")
    boxes: list[tuple[list[float], list[float], tuple[float, float, float]]] = []
    for face_id in range(face_count):
        values: list[tuple[float, float, float]] = []
        face_offset = face_id * FLOATS_PER_FACE
        for corner in range(4):
            offset = face_offset + corner * 4
            values.append(tuple(_f32(rows[offset + axis]) for axis in range(3)))
        raw_min = [min(point[axis] for point in values) for axis in range(3)]
        raw_max = [max(point[axis] for point in values) for axis in range(3)]
        # FL-4359 grass-fields S5: the meadow pad is the computed maximum bend
        # (MEADOW_BLAS_PAD), not a constant; the leaf test deforms the vertices,
        # so a box compiled from the rest pose must contain the bent card.
        pad = MEADOW_BLAS_PAD if int(rows[face_offset + 3]) == MEADOW_BLADE_MAT else FACE_AABB_PAD
        padded_min = [value - pad for value in raw_min]
        padded_max = [value + pad for value in raw_max]
        centroid = tuple((raw_min[axis] + raw_max[axis]) * 0.5 for axis in range(3))
        boxes.append((padded_min, padded_max, centroid))

    if face_count == 1:
        return bytes(32), struct.pack("<I", 0), 1

    node_values: list[tuple[list[float], int, list[float], int] | None] = []
    leaf_ids: list[int] = []
    order = list(range(face_count))

    def append_node(lo: int, hi: int) -> int:
        count = hi - lo
        if count == 1:
            slot = len(leaf_ids)
            leaf_ids.append(order[lo])
            return LEAF_FLAG | slot
        node_index = len(node_values)
        node_values.append(None)
        min_bound = [min(boxes[order[index]][0][axis] for index in range(lo, hi)) for axis in range(3)]
        max_bound = [max(boxes[order[index]][1][axis] for index in range(lo, hi)) for axis in range(3)]
        centroid_min = [min(boxes[order[index]][2][axis] for index in range(lo, hi)) for axis in range(3)]
        centroid_max = [max(boxes[order[index]][2][axis] for index in range(lo, hi)) for axis in range(3)]
        extents = [centroid_max[axis] - centroid_min[axis] for axis in range(3)]
        axis = 0
        if extents[1] > extents[0] and extents[1] >= extents[2]:
            axis = 1
        elif extents[2] > extents[0] and extents[2] > extents[1]:
            axis = 2
        order[lo:hi] = sorted(
            order[lo:hi], key=lambda face_id: (boxes[face_id][2][axis], face_id)
        )
        mid = (lo + hi) >> 1
        left = append_node(lo, mid)
        right = append_node(mid, hi)
        node_values[node_index] = (min_bound, left, max_bound, right)
        return node_index

    append_node(0, face_count)
    if len(leaf_ids) != face_count or sorted(leaf_ids) != list(range(face_count)):
        raise CompileError("BLAS leaf IDs are not a complete face permutation")
    node_bytes = bytearray()
    for node in node_values:
        if node is None:
            raise CompileError("BLAS node placeholder remained unset")
        min_bound, left, max_bound, right = node
        node_bytes.extend(
            struct.pack(
                "<fffIfffI",
                _f32(min_bound[0]), _f32(min_bound[1]), _f32(min_bound[2]), left,
                _f32(max_bound[0]), _f32(max_bound[1]), _f32(max_bound[2]), right,
            )
        )
    id_bytes = struct.pack("<%dI" % len(leaf_ids), *leaf_ids)
    return bytes(node_bytes), id_bytes, len(leaf_ids)


def _decode_map(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < 16 or data[:4] != b"AS3D":
        raise CompileError(f"A3D header or magic invalid: {path}")
    header_size, patch_count = struct.unpack_from("<II", data, 4)
    if header_size != 16 or patch_count <= 0:
        raise CompileError(
            f"unsupported A3D header_size={header_size} patches={patch_count}: {path}"
        )
    terrain_end = header_size + patch_count * PATCH_BYTES
    material_end = terrain_end + MATERIAL_BLOCK_BYTES
    if material_end > len(data):
        raise CompileError(f"A3D terrain or material section truncated: {path}")
    patches: list[dict[str, Any]] = []
    keys: set[tuple[int, int]] = set()
    for source_index in range(patch_count):
        offset = header_size + source_index * PATCH_BYTES
        raw = data[offset : offset + PATCH_BYTES]
        patch_x, patch_y = struct.unpack_from("<ii", raw, 0)
        key = (patch_x, patch_y)
        if key in keys:
            raise CompileError(f"duplicate A3D patch coordinate {key}: {path}")
        keys.add(key)
        patches.append(
            {
                "source_index": source_index,
                "patch_coord": [patch_x, patch_y],
                "visual_words_u16": list(struct.unpack_from("<64H", raw, 8)),
                "corner_heights_u16": list(struct.unpack_from("<25H", raw, 136)),
                "diagonal_bits_u16": struct.unpack_from("<H", raw, 186)[0],
                "record_sha256": _sha256(raw),
                "_raw": raw,
            }
        )
    min_x = min(key[0] for key in keys)
    max_x = max(key[0] for key in keys)
    min_y = min(key[1] for key in keys)
    max_y = max(key[1] for key in keys)
    center_offset = [
        (min_x + max_x + 1) * VISUAL_CELLS / 2.0,
        (min_y + max_y + 1) * VISUAL_CELLS / 2.0,
    ]
    return {
        "path": path,
        "bytes": data,
        "patches": patches,
        "patch_keys": keys,
        "patch_count": patch_count,
        "center_offset": center_offset,
        "bounds": [min_x, min_y, max_x, max_y],
        "material_sha256": _sha256(data[terrain_end:material_end]),
        "terrain_prefix_sha256": _sha256(data[:material_end]),
    }


def _tree_face_count(mesh_name: str, mesh_root: Path = DEFAULT_MESH_ROOT) -> int:
    """Retained face count of one authored tree asset.

    A placement's face count is now a property of the mesh it draws, not of its
    source instance id.  Every instance of one asset therefore reports the same
    count, and the count is whatever the authored file actually contains.
    """
    return int(load_tree_mesh(mesh_name, mesh_root)["face_count"])


def _load_tree_records(
    path: Path, runtime_map_sha256: str, mesh_root: Path = DEFAULT_MESH_ROOT
) -> tuple[list[dict[str, Any]], str]:
    raw = path.read_bytes()
    value = json.loads(raw)
    if value.get("$schema") != "fl4708_y8_tree_instances.v1":
        raise CompileError(f"unexpected authored-tree schema: {path}")
    if value.get("runtime_terrain_sha256") != runtime_map_sha256:
        raise CompileError("authored-tree manifest runtime terrain hash does not match compiler map")
    if bool(value.get("runtime_consumption_blocked", True)):
        raise CompileError(
            "authored-tree manifest blocks consumption: "
            + str(value.get("runtime_consumption_blocker", "missing reason"))
        )
    records = value.get("records")
    # FL-4876 PUBLISH-01: an OSM-pipeline map declares zero canonical trees
    # with tree_records_waived; its population trees compile as fixtures.
    if (isinstance(records, list) and not records
            and str(value.get("tree_records_waived") or "").startswith(
                "no_canonical_y8_tree_meshes")):
        return [], _sha256(raw)
    if not isinstance(records, list) or not records:
        raise CompileError("authored-tree manifest has no records")
    compiled: list[dict[str, Any]] = []
    seen: set[int] = set()
    for record in records:
        source_id = int(record.get("source_instance_index", -1))
        transform = record.get("authored_transform_column_major_f64")
        placement = record.get("runtime_placement")
        if source_id < 0 or source_id in seen:
            raise CompileError(f"invalid or duplicate authored tree id {source_id}")
        if not isinstance(transform, list) or len(transform) != 16:
            raise CompileError(f"tree {source_id} lacks authored transform")
        if not isinstance(placement, dict):
            raise CompileError(f"tree {source_id} lacks runtime placement")
        world_xz = placement.get("world_xz")
        if not isinstance(world_xz, list) or len(world_xz) != 2:
            raise CompileError(f"tree {source_id} lacks world_xz")
        seen.add(source_id)
        mesh_name = str(record.get("source_mesh_name", ""))
        if mesh_name not in CANONICAL_TREE_MESHES:
            # Refuse to compile.  A tree row whose mesh this compiler cannot open
            # has no authored geometry, and inventing a substitute is exactly the
            # stand-in behaviour this compiler no longer performs.
            raise CompileError(
                f"authored tree {source_id} names non-canonical mesh {mesh_name!r}"
            )
        face_count = _tree_face_count(mesh_name, mesh_root)
        compiled.append(
            {
                "instance_or_source_id": source_id,
                "instance_identity": str(record.get("instance_identity", "")),
                "source_kind": "authored_y8",
                "source_mesh_name": mesh_name,
                "authored_geometry_variant": str(record.get("authored_geometry_variant", "")),
                # The full authored basis stays with the record: the vertical
                # column alone (which is all `procedural_scale` recovers) cannot
                # rebuild the authored horizontal footprint.
                "authored_transform_column_major_f64": [
                    float(value) for value in transform
                ],
                "world_xz": [float(world_xz[0]), float(world_xz[1])],
                "authored_godot_y": float(placement.get("authored_godot_y", 0.0)),
                "yaw_radians": float(placement.get("yaw_radians", 0.0)),
                "procedural_scale": float(placement.get("procedural_scale", 1.0)),
                "face_count": face_count,
                "blas_leaf_count": face_count,
                "primitive_id_begin": 0,
                "primitive_id_end_exclusive": face_count,
            }
        )
    compiled.sort(key=lambda row: int(row["instance_or_source_id"]))
    return compiled, _sha256(raw)


def _sample_terrain_at_godot_xz(
    decoded: dict[str, Any], world_xz: list[float]
) -> tuple[float, int] | None:
    """Match A3DMapLoader's patch lookup, material lookup, and bilinear height."""
    center_x, center_y = decoded["center_offset"]
    source_x = float(world_xz[0]) + center_x
    source_y = float(world_xz[1]) + center_y
    patch_x = math.floor(source_x / VISUAL_CELLS)
    patch_y = math.floor(source_y / VISUAL_CELLS)
    if "patch_by_coord" not in decoded:
        decoded["patch_by_coord"] = {tuple(row["patch_coord"]): row for row in decoded["patches"]}
    patch = decoded["patch_by_coord"].get((patch_x, patch_y))
    if patch is None:
        return None
    local_x = source_x - patch_x * VISUAL_CELLS
    local_y = source_y - patch_y * VISUAL_CELLS
    mat_x = max(0, min(VISUAL_CELLS - 1, int(math.floor(local_x))))
    mat_y = max(0, min(VISUAL_CELLS - 1, int(math.floor(local_y))))
    mat_id = int(patch["visual_words_u16"][mat_y * VISUAL_CELLS + mat_x]) & 0xFF
    hx = local_x / 2.0
    hy = local_y / 2.0
    x0 = int(math.floor(hx))
    y0 = int(math.floor(hy))
    x1 = min(x0 + 1, HEIGHT_CELLS)
    y1 = min(y0 + 1, HEIGHT_CELLS)
    tx = hx - x0
    ty = hy - y0
    heights = patch["corner_heights_u16"]
    h00 = float(heights[y0 * (HEIGHT_CELLS + 1) + x0])
    h10 = float(heights[y0 * (HEIGHT_CELLS + 1) + x1])
    h01 = float(heights[y1 * (HEIGHT_CELLS + 1) + x0])
    h11 = float(heights[y1 * (HEIGHT_CELLS + 1) + x1])
    height = ((h00 + (h10 - h00) * tx) * (1.0 - ty)
              + (h01 + (h11 - h01) * tx) * ty) / 16.0
    return height, mat_id


def _sample_capture_ground(decoded: dict[str, Any], x: float, z: float) -> tuple[float, int] | None:
    """Height on capture's one-cell w00-w11 triangle split, not a bilinear patch."""
    center = _sample_terrain_at_godot_xz(decoded, [x, z])
    if center is None:
        return None
    x0, z0 = math.floor(x), math.floor(z)
    samples = [_sample_terrain_at_godot_xz(decoded, [x0 + dx, z0 + dz])
               for dx, dz in ((0, 0), (1, 0), (1, 1), (0, 1))]
    if any(value is None for value in samples):
        return None
    h00, h10, h11, h01 = [value[0] for value in samples]
    u, v = x - x0, z - z0
    height = (h00 + u * (h10 - h00) + v * (h11 - h10) if u >= v
              else h00 + u * (h11 - h01) + v * (h01 - h00))
    return height, center[1]


def build_root_ecology_rows(tree: dict[str, Any], authored_rows: list[float],
                            decoded: dict[str, Any]) -> list[float]:
    """FL-4359: bounded root patches, with every ground vertex terrain-supported."""
    world = transform_tree_rows(authored_rows, tree["world_xz"], tree["authored_godot_y"],
                                tree["yaw_radians"], tree["procedural_scale"])
    trunk = [tuple(world[offset + corner + axis] for axis in range(3))
             for offset in range(0, len(world), 16) if int(world[offset + 3]) == TREE_TRUNK_MAT
             for corner in (0, 4, 8)]
    if not trunk:
        return []
    bottom = min(p[1] for p in trunk)
    base = [p for p in trunk if p[1] <= bottom + 0.15 * (max(p[1] for p in trunk) - bottom)]
    lo_x, hi_x = min(p[0] for p in base), max(p[0] for p in base)
    lo_z, hi_z = min(p[2] for p in base), max(p[2] for p in base)
    cx, cz = (lo_x + hi_x) * 0.5, (lo_z + hi_z) * 0.5
    tree["root_base_world"] = [cx, bottom, cz]
    tree["root_base_half_size"] = [max(0.15, (hi_x - lo_x) * 0.5), max(0.15, (hi_z - lo_z) * 0.5)]
    ground = _sample_capture_ground(decoded, cx, cz)
    if ground is None:
        return []
    radius = max(0.15, math.hypot(hi_x - lo_x, hi_z - lo_z) * 0.5)
    source = int(tree["instance_or_source_id"])
    scale = float(tree["procedural_scale"])
    yaw = float(tree["yaw_radians"])
    cosine, sine = math.cos(yaw), math.sin(yaw)

    def local(p: tuple[float, float, float]) -> tuple[float, float, float]:
        dx, dz = p[0] - tree["world_xz"][0], p[2] - tree["world_xz"][1]
        return ((cosine * dx + sine * dz) / scale,
                (p[1] - tree["authored_godot_y"]) / scale,
                (-sine * dx + cosine * dz) / scale)

    result: list[float] = []
    # Attached lower-trunk moss follows existing bark triangles. Its vertices
    # sit a tiny distance along the signed authored normal, not on floating cards.
    moss_top = bottom + max(0.45, (max(p[1] for p in trunk) - bottom) * 0.12)
    for offset in range(0, len(world), 16):
        if int(world[offset + 3]) != TREE_TRUNK_MAT:
            continue
        points = [tuple(world[offset + corner + axis] for axis in range(3)) for corner in (0, 4, 8)]
        if min(p[1] for p in points) >= moss_top or _hash_unit(source + offset, 337) > 0.7:
            continue
        normal = _v_cross(_v_sub(points[1], points[0]), _v_sub(points[2], points[0]))
        length = math.sqrt(_v_dot(normal, normal))
        if length < 1e-8:
            continue
        sign = -1 if world[offset + 15] < 0 else 1
        clipped = []
        for previous, current in zip(points[-1:] + points[:-1], points):
            previous_in, current_in = previous[1] <= moss_top, current[1] <= moss_top
            if previous_in != current_in:
                t = (moss_top - previous[1]) / (current[1] - previous[1])
                clipped.append(tuple(previous[i] + (current[i] - previous[i]) * t for i in range(3)))
            if current_in:
                clipped.append(current)
        lifted = [tuple(p[i] + normal[i] / length * 0.025 * sign for i in range(3)) for p in clipped]
        for vertex in range(1, len(lifted) - 1):
            triangle = [lifted[0], lifted[vertex], lifted[vertex + 1], lifted[vertex + 1]]
            _append_face(result, *(local(p) for p in triangle), ROOT_MOSS_MAT, 0.8)
            result[-1] = 4709.0 # attached bark support, distinct from ground moss

    for cluster in range(3):
        angle = math.tau * (_hash_unit(source, 307) + cluster / 3.0)
        for leaf in range(3):
            a = angle + (_hash_unit(source + leaf, 311 + cluster) - 0.5) * 0.55
            reach = radius * (0.65 + leaf * 0.18)
            x, z = cx + math.cos(a) * reach, cz + math.sin(a) * reach
            width = 0.65 + 0.45 * _hash_unit(source + leaf, 317 + cluster)
            points = []
            for dx, dz in ((-width, 0.0), (0.0, width * 0.7), (width, 0.0), (0.0, -width * 0.7)):
                sampled = _sample_capture_ground(decoded, x + dx, z + dz)
                if sampled is None or sampled[1] not in (0, 1, 3) or sampled[0] <= 49.0 / 16.0:
                    break
                points.append((x + dx, sampled[0] + 0.03, z + dz))
            if len(points) != 4 or max(p[1] for p in points) - min(p[1] for p in points) > 0.5:
                continue
            _append_face(result, *(local(p) for p in points), ROOT_MOSS_MAT, 0.72)
            if leaf == 1 and cluster == source % 3:
                # A small four-facet mushroom grows from the admitted moss patch.
                h, cap = 0.30, 0.18
                footprints = [(x-0.025,z), (x+0.025,z), (x,z)] + [(x+math.cos(i*math.pi/2)*cap,z+math.sin(i*math.pi/2)*cap) for i in range(4)]
                support = [_sample_capture_ground(decoded, px, pz) for px, pz in footprints]
                if all(g is not None and g[1] in (0, 1, 3) and g[0] > 49.0/16.0 for g in support):
                    stem0 = (footprints[0][0],support[0][0]+0.03,z)
                    stem1 = (footprints[1][0],support[1][0]+0.03,z)
                    _append_face(result, local(stem0), local(stem1), local((stem1[0],stem1[1]+h,z)), local((stem0[0],stem0[1]+h,z)), ROOT_FLOWER_MAT, 0.7)
                    tip = (x, support[2][0]+0.03+h+0.10, z)
                    rim = [(footprints[i+3][0],support[i+3][0]+0.03+h,footprints[i+3][1]) for i in range(4)]
                    for sector in range(4):
                        _append_face(result, local(rim[sector]), local(rim[(sector+1)%4]), local(tip), local(tip), ROOT_FLOWER_MAT, 0.9)
            # One sparse flower cluster per admitted tree, not a bloom at every leaf.
            if cluster != source % 3 or leaf == 0:
                continue
            petal = 0.23
            height = 0.60 + leaf * 0.08
            footprints = [(x - 0.025, z), (x + 0.025, z),
                          (x - petal, z), (x, z + petal), (x + petal, z), (x, z - petal)]
            support = [_sample_capture_ground(decoded, px, pz) for px, pz in footprints]
            if any(p is None or p[1] not in (0, 1, 3) or p[0] <= 49.0 / 16.0 for p in support):
                continue
            stem = [(px, support[i][0] + 0.03, pz) for i, (px, pz) in enumerate(footprints[:2])]
            _append_face(result, local(stem[0]), local(stem[1]),
                         local((stem[1][0], stem[1][1] + height, z)),
                         local((stem[0][0], stem[0][1] + height, z)), ROOT_MOSS_MAT, 0.78)
            petals = [(px, support[i + 2][0] + height + (0.06 if i % 2 else 0.0), pz)
                      for i, (px, pz) in enumerate(footprints[2:])]
            _append_face(result, *(local(p) for p in petals), ROOT_FLOWER_MAT, 0.86)
    return [_f32(value) for value in result]


def _meadow_grid_seed(grid_x: int, grid_z: int) -> int:
    """Return a world-anchored uint seed for one coverage-lattice site."""
    mask = 0xFFFFFFFF
    key = (((grid_x & mask) * 73856093) & mask) \
        ^ (((grid_z & mask) * 19349663) & mask)
    return (key * 1664525 + 1013904223) & mask


def _meadow_tree_blocked(
    x: float, z: float, roots: list[tuple[list[float], list[float]]], margin: float = 0.18
) -> bool:
    return any(
        abs(x - float(base[0])) < float(half[0]) + margin
        and abs(z - float(base[2])) < float(half[1]) + margin
        for base, half in roots
    )


def _meadow_flower_eligible(
    decoded: dict[str, Any], x: float, z: float, y: float,
    roots: list[tuple[list[float], list[float]]], seed: int,
) -> bool:
    """Choose a stable hollow for a card-top flower glyph."""
    if _meadow_tree_blocked(x, z, roots):
        return False
    neighbours = [
        _sample_capture_ground(decoded, x + dx, z + dz)
        for dx, dz in ((-1.0, 0.0), (1.0, 0.0), (0.0, -1.0), (0.0, 1.0))
    ]
    if any(
        g is None or g[1] not in (0, 1, 3) or g[0] <= 49.0 / 16.0
        for g in neighbours
    ):
        return False
    neighbour_heights = [float(g[0]) for g in neighbours if g is not None]
    spread = max(neighbour_heights) - min(neighbour_heights)
    mean = sum(neighbour_heights) / len(neighbour_heights)
    # A flat pocket is a valid low hollow too: the glyph is sparse and stable,
    # while a steep edge is rejected before the runtime gets a flower flag.
    return spread <= 0.35 and y <= mean + 0.05 and _hash_unit32(seed, 113) < 0.18


def build_meadow_records(decoded: dict[str, Any], trees: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[int, list[float]]]:
    """Compile separated tapered blades with shared clump traits, one record per chunk.

    Candidate roots come from one world-anchored coverage lattice rather than
    patch-local nuclei. Terrain, water, tree-base, and step gates are applied
    before a blade is emitted. Every emitted face receives its local
    clump index; flowers are a flag on the clump and therefore add no geometry.
    """
    groups: dict[tuple[int, int], list[tuple[float, float, float, int, float]]] = {}
    center_x, center_z = decoded["center_offset"]
    roots = [
        (t["root_base_world"], t["root_base_half_size"])
        for t in trees if t.get("root_base_world")
    ]
    world_min_x = float(decoded["bounds"][0]) * VISUAL_CELLS - center_x
    world_max_x = float(decoded["bounds"][2] + 1) * VISUAL_CELLS - center_x
    world_min_z = float(decoded["bounds"][1]) * VISUAL_CELLS - center_z
    world_max_z = float(decoded["bounds"][3] + 1) * VISUAL_CELLS - center_z
    spacing = MEADOW_COVERAGE_SPACING
    for grid_x in range(math.floor(world_min_x / spacing) - 1,
                        math.ceil(world_max_x / spacing) + 1):
        for grid_z in range(math.floor(world_min_z / spacing) - 1,
                            math.ceil(world_max_z / spacing) + 1):
            seed = _meadow_grid_seed(grid_x, grid_z)
            x = (grid_x + 0.5) * spacing \
                + (2.0 * _hash_unit32(seed, 11) - 1.0) * MEADOW_COVERAGE_JITTER
            z = (grid_z + 0.5) * spacing \
                + (2.0 * _hash_unit32(seed, 13) - 1.0) * MEADOW_COVERAGE_JITTER
            # One clump field sample controls both admission and every visual
            # trait. The seed only distributes candidates within that local
            # density; it cannot break the neighbouring-clump correlation.
            field = _meadow_clump_field(x, z)
            density = 0.20 + 0.80 * field
            if _hash_unit32(seed, 47) > density:
                continue
            ground = _sample_capture_ground(decoded, x, z)
            if (
                ground is None
                or ground[1] not in (0, 1, 3)
                or ground[0] <= 49.0 / 16.0
                or _meadow_tree_blocked(x, z, roots)
            ):
                continue
            patch_x = math.floor((x + center_x) / VISUAL_CELLS)
            patch_z = math.floor((z + center_z) / VISUAL_CELLS)
            if (patch_x, patch_z) not in decoded["patch_keys"]:
                continue
            chunk = (math.floor(patch_x / PATCHES_PER_CHUNK),
                     math.floor(patch_z / PATCHES_PER_CHUNK))
            groups.setdefault(chunk, []).append((x, ground[0], z, seed, field))

    records: list[dict[str, Any]] = []
    row_sets: dict[int, list[float]] = {}
    total_faces = 0
    for index, (chunk, sites) in enumerate(sorted(groups.items())):
        sites.sort(key=lambda site: (site[2], site[0], site[3]))
        origin = sites[0][:3]
        rows: list[float] = []
        face_clumps: list[int] = []
        clumps: list[dict[str, Any]] = []
        for x, y, z, seed, field in sites:
            traits = meadow_clump_traits_from_field(field)
            height = _f32(traits["height"])
            shade = _f32(traits["shade"])
            phase_lag = _f32(traits["phase_lag"])
            # Heading jitter is authored once and exported with the clump, so
            # the future traits bank has the exact card orientation to use.
            heading = _f32(
                traits["heading"]
                + math.tau / 12.0 * (_hash_unit32(seed, 31) - 0.5)
            )
            local_clump_index = len(clumps)
            card_rows: list[float] = []
            card_face_count = 0
            for blade in range(MEADOW_BLADES_PER_CLUMP):
                angle = heading + math.tau * blade / MEADOW_BLADES_PER_CLUMP
                radius = MEADOW_CLUMP_RADIUS * (0.55 + 0.45 * _hash_unit32(seed, 151 + blade))
                bx, bz = x + math.cos(angle) * radius, z + math.sin(angle) * radius
                ground = _sample_capture_ground(decoded, bx, bz)
                if (ground is None or ground[1] not in (0, 1, 3)
                        or ground[0] <= 49.0 / 16.0
                        or abs(ground[0] - y) > MEADOW_BLADE_MAX_STEP
                        or _meadow_tree_blocked(bx, bz, roots)):
                    continue
                blade_heading = heading + (blade - 2) * 0.31
                dx = math.cos(blade_heading) * MEADOW_BLADE_WIDTH * 0.5
                dz = math.sin(blade_heading) * MEADOW_BLADE_WIDTH * 0.5
                ends = [_sample_capture_ground(decoded, bx + sign * dx, bz + sign * dz)
                        for sign in (-1, 1)]
                if any(g is None or g[1] not in (0, 1, 3)
                       or g[0] <= 49.0 / 16.0
                       or abs(g[0] - ground[0]) > MEADOW_BLADE_MAX_STEP for g in ends):
                    continue
                low_y, high_y = ends[0][0] + 0.02, ends[1][0] + 0.02
                # Tapered tips have real transparent separation in capture,
                # exact visibility and shadows, before any glyph is painted.
                root = (bx - origin[0], (low_y + high_y) * 0.5 - origin[1], bz - origin[2])
                rx, ry, rz = root
                _append_face(card_rows,
                    (rx - dx, low_y - origin[1], rz - dz),
                    (rx + dx, high_y - origin[1], rz + dz),
                    (rx + dx * 0.10, ry + height, rz + dz * 0.10),
                    (rx - dx * 0.10, ry + height, rz - dz * 0.10),
                    MEADOW_BLADE_MAT, shade)
                card_rows[-9], card_rows[-5], card_rows[-1] = root
                card_face_count += 1
            if card_face_count == 0:
                continue
            flags = (
                MEADOW_FLOWER_FLAG
                if _meadow_flower_eligible(decoded, x, z, y, roots, seed)
                else 0
            )
            species = (
                MEADOW_FLOWER_SPECIES_BASE
                + int(_hash_unit32(seed, 137) * MEADOW_FLOWER_SPECIES_COUNT)
                if flags & MEADOW_FLOWER_FLAG
                else int(_hash_unit32(seed, 137) * MEADOW_GRASS_SPECIES_COUNT)
            )
            clumps.append({
                "root": [_f32(x), _f32(y + 0.015), _f32(z)],
                "height": height,
                "heading": heading,
                "shade": shade,
                "phase_lag": phase_lag,
                "species": species,
                "seed": int(seed) & 0xFFFFFFFF,
                "motif": int(_hash_unit32(seed, 139) * MEADOW_MOTIF_COUNT),
                "flags": int(flags),
            })
            rows.extend(card_rows)
            face_clumps.extend([local_clump_index] * card_face_count)
        if not rows:
            continue
        source_id = 8000000 + index
        count = len(rows) // FLOATS_PER_FACE
        if count != len(face_clumps):
            raise CompileError(
                f"meadow chunk {chunk} face/clump mapping mismatch "
                f"faces={count} map={len(face_clumps)}"
            )
        if count > MEADOW_MAX_FACES_PER_CHUNK:
            raise CompileError(
                f"meadow chunk {chunk} exceeds bounded face budget "
                f"{count}>{MEADOW_MAX_FACES_PER_CHUNK}"
            )
        total_faces += count
        if total_faces > MEADOW_MAX_TOTAL_FACES:
            raise CompileError(
                "meadow catalog exceeds bounded face budget "
                f"{total_faces}>{MEADOW_MAX_TOTAL_FACES}"
            )
        records.append({
            "instance_or_source_id": source_id,
            "instance_identity": f"meadow:{chunk[0]}:{chunk[1]}",
            "source_kind": "compiled_meadow",
            "source_mesh_name": "meadow_field_cards",
            "authored_geometry_variant": "area_coverage_distinct_blades",
            "world_xz": [origin[0], origin[2]],
            "authored_godot_y": origin[1],
            "yaw_radians": 0.0,
            "procedural_scale": 1.0,
            "face_count": count,
            "authored_face_count": 0,
            "root_ecology_face_count": 0,
            "blas_leaf_count": count,
            "primitive_id_begin": 0,
            "primitive_id_end_exclusive": count,
            "vegetation_clumps": clumps,
            "vegetation_face_clumps": face_clumps,
        })
        row_sets[source_id] = [_f32(value) for value in rows]
    return records, row_sets


def _chunk_id(chunk_x: int, chunk_y: int) -> str:
    return f"y8:{chunk_x}:{chunk_y}"


def _relative_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def compile_catalog(
    map_path: Path,
    tree_manifest_path: Path,
    payload_path: Path = DEFAULT_PAYLOAD,
    mesh_root: Path = DEFAULT_MESH_ROOT,
    include_fixtures: bool = True,
    lab_grove: bool = True,
    meadow: bool = True,
) -> CompiledCatalog:
    # FL-4798: `include_fixtures` (default on, the operator-requested state of
    # the shipped Y8 map) adds every non-tree authored mesh instance as its own
    # registry family; `lab_grove`/`meadow` let a non-Y8 map (whose terrain
    # rejects the fixed Y8 lab anchors) compile without inventing placements.
    map_path = map_path.resolve()
    tree_manifest_path = tree_manifest_path.resolve()
    payload_path = payload_path.resolve()
    mesh_root = mesh_root.resolve()
    decoded = _decode_map(map_path)
    map_sha256 = _sha256(decoded["bytes"])
    trees, tree_manifest_sha256 = _load_tree_records(
        tree_manifest_path, str(decoded["terrain_prefix_sha256"]), mesh_root
    )
    center_x, center_y = decoded["center_offset"]

    # The three FL-4708 laboratory anchors are also retained geometry.  They
    # use the same admission predicate as World: an in-map patch, terrain
    # material 0/1/3, and sampled ground no higher than 18 units.
    grove_offsets = [(0.0, 0.0), (-5.0, 4.0), (5.0, 4.0)] if lab_grove else []
    lab_trees: list[dict[str, Any]] = []
    for grove_index, (offset_x, offset_z) in enumerate(grove_offsets):
        source_id = 1201 + grove_index
        world_xz = [73.0 + offset_x, 73.0 + offset_z]
        sampled = _sample_terrain_at_godot_xz(decoded, world_xz)
        if sampled is None:
            raise CompileError(f"lab grove tree {source_id} is outside runtime terrain")
        ground_y, terrain_mat = sampled
        if terrain_mat not in (0, 1, 3):
            raise CompileError(
                f"lab grove tree {source_id} rejected terrain material {terrain_mat}"
            )
        if ground_y > 18.0:
            raise CompileError(
                f"lab grove tree {source_id} rejected ground height {ground_y:.6f}"
            )
        scale = 1.28 if grove_index == 0 else 1.16
        # The lab anchors draw authored assets too.  They carry no A3D basis, so
        # they use the normalized scale-1.0 asset and the placement's own scale.
        lab_mesh_name = LAB_GROVE_MESHES[grove_index % len(LAB_GROVE_MESHES)]
        lab_face_count = _tree_face_count(lab_mesh_name, mesh_root)
        lab_trees.append(
            {
                "instance_or_source_id": source_id,
                "instance_identity": f"fl4708-lab-grove-{grove_index}",
                "source_kind": "lab_grove",
                "source_mesh_name": lab_mesh_name,
                "authored_geometry_variant": lab_mesh_name,
                "world_xz": world_xz,
                "authored_godot_y": ground_y,
                "yaw_radians": _hash_unit(source_id, 71) * math.tau,
                "procedural_scale": scale,
                "face_count": lab_face_count,
                "blas_leaf_count": lab_face_count,
                "primitive_id_begin": 0,
                "primitive_id_end_exclusive": lab_face_count,
            }
        )
    trees.extend(lab_trees)

    # One row set per placement, built once.  The authored basis differs per
    # instance, so two placements share a content record only when their whole
    # local geometry matches byte for byte.
    rows_by_source: dict[int, list[float]] = {
        int(tree["instance_or_source_id"]): build_tree_local_rows(tree, mesh_root)
        for tree in trees
    }
    for tree in trees:
        rows = rows_by_source[int(tree["instance_or_source_id"])]
        tree["authored_face_count"] = len(rows) // FLOATS_PER_FACE
        ecology = build_root_ecology_rows(tree, rows, decoded)
        tree["root_ecology_face_count"] = len(ecology) // FLOATS_PER_FACE
        rows.extend(ecology)
        tree["face_count"] = len(rows) // FLOATS_PER_FACE
        tree["blas_leaf_count"] = tree["face_count"]
        tree["primitive_id_end_exclusive"] = tree["face_count"]

    tree_count = len(trees)
    fixtures: list[dict[str, Any]] = []
    fixtures_outside_terrain: list[dict[str, Any]] = []
    if include_fixtures:
        fixtures = _load_fixture_records(tree_manifest_path, decoded, mesh_root)
        for fixture in fixtures:
            # An authored row over water or beyond the patch union (the Y8
            # source places ships, water-crossing bridges and shore houses
            # there; asciicker draws meshes wherever the instance sits) has no
            # terrain chunk of its own.  It is hosted by the nearest terrain
            # chunk so residency still streams it; the choice is reported.
            source_x = float(fixture["world_xz"][0]) + center_x
            source_y = float(fixture["world_xz"][1]) + center_y
            patch = (math.floor(source_x / VISUAL_CELLS), math.floor(source_y / VISUAL_CELLS))
            if patch not in decoded["patch_keys"]:
                fixture["hosted_by_nearest_chunk"] = True
                fixtures_outside_terrain.append(
                    {
                        "instance_or_source_id": int(fixture["instance_or_source_id"]),
                        "source_instance_index": int(fixture["source_instance_index"]),
                        "source_mesh_name": fixture["source_mesh_name"],
                        "world_xz": fixture["world_xz"],
                        "source_patch": list(patch),
                    }
                )
        for fixture in fixtures:
            source_id = int(fixture["instance_or_source_id"])
            if source_id in rows_by_source:
                raise CompileError(f"fixture {source_id} collides with a tree registry source id")
            rows_by_source[source_id] = build_fixture_local_rows(fixture, mesh_root)
        trees.extend(fixtures)
    fixture_count = len(fixtures)
    if meadow:
        meadow_records, meadow_rows = build_meadow_records(decoded, trees)
    else:
        meadow_records, meadow_rows = [], {}
    trees.extend(meadow_records)
    rows_by_source.update(meadow_rows)

    content_by_hash: dict[str, dict[str, Any]] = {}
    for tree in trees:
        source_id = int(tree["instance_or_source_id"])
        rows = rows_by_source[source_id]
        rows_bytes = _rows_bytes(rows)
        node_bytes, id_bytes, leaf_count = _build_blas(rows)
        face_count = len(rows) // FLOATS_PER_FACE
        content_hash = _sha256(struct.pack("<i", face_count) + rows_bytes)
        if content_hash not in content_by_hash:
            local_min, local_max = _rows_aabb(rows)
            content_by_hash[content_hash] = {
                "content_hash": content_hash,
                "content_key": f"fl4789/content/{content_hash}",
                "rows_bytes": rows_bytes,
                "node_bytes": node_bytes,
                "id_bytes": id_bytes,
                "face_count": face_count,
                "blas_leaf_count": leaf_count,
                "local_aabb_min": local_min,
                "local_aabb_max": local_max,
                "source_mesh_names": set(),
                "source_instance_ids": [],
                "source_kinds": set(),
                "registry_slots": [],
            }
        elif (
            content_by_hash[content_hash]["rows_bytes"] != rows_bytes
            or content_by_hash[content_hash]["node_bytes"] != node_bytes
            or content_by_hash[content_hash]["id_bytes"] != id_bytes
        ):
            raise CompileError(f"content checksum collision for source tree {source_id}")
        content_by_hash[content_hash]["source_mesh_names"].add(tree["source_mesh_name"])
        content_by_hash[content_hash]["source_instance_ids"].append(source_id)
        content_by_hash[content_hash]["source_kinds"].add(tree["source_kind"])

    ordered_content = [content_by_hash[key] for key in sorted(content_by_hash)]
    # Authored and lab trees retain slots 0..181. Meadow chunks follow them;
    # residency cannot renumber either family.
    slot_by_source = {
        int(tree["instance_or_source_id"]): slot for slot, tree in enumerate(trees)
    }
    if len(slot_by_source) != len(trees) or len(set(slot_by_source.values())) != len(trees):
        raise CompileError("compiled tree registry has a source or slot collision")
    for tree in trees:
        source_id = int(tree["instance_or_source_id"])
        rows = rows_by_source[source_id]
        content_hash = _sha256(struct.pack("<i", len(rows) // FLOATS_PER_FACE) + _rows_bytes(rows))
        content_by_hash[content_hash]["registry_slots"].append(slot_by_source[source_id])

    payload = bytearray(
        PAYLOAD_HEADER_STRUCT.pack(
            PAYLOAD_MAGIC,
            PAYLOAD_VERSION,
            PAYLOAD_HEADER_BYTES,
            len(ordered_content),
            REGISTRY_SLOT_BASE,
            len(trees),
            FIXED_INSTANCE_RESERVED_COUNT,
        )
    )
    asset_records: list[dict[str, Any]] = []
    for content_index, content in enumerate(ordered_content):
        rows_offset = len(payload)
        payload.extend(content["rows_bytes"])
        node_offset = len(payload)
        payload.extend(content["node_bytes"])
        id_offset = len(payload)
        payload.extend(content["id_bytes"])
        payload_offset = rows_offset
        payload_length = len(content["rows_bytes"]) + len(content["node_bytes"]) + len(content["id_bytes"])
        asset_records.append(
            {
                "content_index": content_index,
                "content_key": content["content_key"],
                "content_hash": content["content_hash"],
                "source_mesh_names": sorted(content["source_mesh_names"]),
                "source_instance_ids": sorted(content["source_instance_ids"]),
                "source_kinds": sorted(content["source_kinds"]),
                "chunk_ids": [],
                "registry_slots": sorted(content["registry_slots"]),
                "face_count": content["face_count"],
                "blas_leaf_count": content["blas_leaf_count"],
                "local_aabb_min": content["local_aabb_min"],
                "local_aabb_max": content["local_aabb_max"],
                "rows_offset": rows_offset,
                "rows_length": len(content["rows_bytes"]),
                "rows_sha256": _sha256(content["rows_bytes"]),
                "blas_node_offset": node_offset,
                "blas_node_length": len(content["node_bytes"]),
                "blas_node_sha256": _sha256(content["node_bytes"]),
                "blas_id_offset": id_offset,
                "blas_id_length": len(content["id_bytes"]),
                "blas_id_sha256": _sha256(content["id_bytes"]),
                "payload_offset": payload_offset,
                "payload_length": payload_length,
                "payload_sha256": _sha256(
                    content["rows_bytes"] + content["node_bytes"] + content["id_bytes"]
                ),
            }
        )
    payload_bytes = bytes(payload)
    payload_sha256 = _sha256(payload_bytes)
    content_record_by_hash = {row["content_hash"]: row for row in asset_records}

    chunks: dict[tuple[int, int], dict[str, Any]] = {}

    def ensure_chunk(chunk_x: int, chunk_y: int) -> dict[str, Any]:
        key = (chunk_x, chunk_y)
        if key not in chunks:
            source_patch_x = chunk_x * PATCHES_PER_CHUNK
            source_patch_y = chunk_y * PATCHES_PER_CHUNK
            world_min_x = source_patch_x * VISUAL_CELLS - center_x
            world_min_y = source_patch_y * VISUAL_CELLS - center_y
            chunks[key] = {
                "chunk_id": _chunk_id(chunk_x, chunk_y),
                "chunk_coord": [chunk_x, chunk_y],
                "source_patch_bounds": [source_patch_x, source_patch_y, source_patch_x + PATCHES_PER_CHUNK, source_patch_y + PATCHES_PER_CHUNK],
                "world_cell_bounds": [world_min_x, world_min_y, world_min_x + CHUNK_CELLS, world_min_y + CHUNK_CELLS],
                "terrain_patches": [],
                "asset_instances": [],
            }
        return chunks[key]

    for patch in decoded["patches"]:
        patch_x, patch_y = patch["patch_coord"]
        chunk = ensure_chunk(math.floor(patch_x / PATCHES_PER_CHUNK), math.floor(patch_y / PATCHES_PER_CHUNK))
        stored = dict(patch)
        del stored["_raw"]
        chunk["terrain_patches"].append(stored)

    for tree in trees:
        source_id = int(tree["instance_or_source_id"])
        source_x = float(tree["world_xz"][0]) + center_x
        source_y = float(tree["world_xz"][1]) + center_y
        patch_x = math.floor(source_x / VISUAL_CELLS)
        patch_y = math.floor(source_y / VISUAL_CELLS)
        chunk_key = (math.floor(patch_x / PATCHES_PER_CHUNK), math.floor(patch_y / PATCHES_PER_CHUNK))
        if (patch_x, patch_y) not in decoded["patch_keys"]:
            if not tree.get("hosted_by_nearest_chunk"):
                raise CompileError(f"authored placement {source_id} is outside runtime terrain")
            if chunk_key not in chunks:
                chunk_key = min(
                    chunks,
                    key=lambda key: (
                        max(abs(key[0] - chunk_key[0]), abs(key[1] - chunk_key[1])),
                        (key[0] - chunk_key[0]) ** 2 + (key[1] - chunk_key[1]) ** 2,
                        key,
                    ),
                )
        chunk = ensure_chunk(*chunk_key)
        rows = rows_by_source[source_id]
        content_hash = _sha256(struct.pack("<i", len(rows) // FLOATS_PER_FACE) + _rows_bytes(rows))
        content = content_record_by_hash[content_hash]
        world_rows = transform_tree_rows(
            rows,
            tree["world_xz"],
            tree["authored_godot_y"],
            tree["yaw_radians"],
            tree["procedural_scale"],
        )
        world_min, world_max = _rows_aabb(world_rows)
        slot = slot_by_source[source_id]
        placement = {
            "chunk_id": chunk["chunk_id"],
            "registry_slot": slot,
            "source_instance_id": source_id,
            "instance_or_source_id": source_id,
            "source_instance_index": int(tree.get("source_instance_index", source_id)),
            "hosted_by_nearest_chunk": bool(tree.get("hosted_by_nearest_chunk", False)),
            "instance_identity": tree["instance_identity"],
            "source_kind": tree["source_kind"],
            "source_mesh_name": tree["source_mesh_name"],
            "authored_geometry_variant": tree["authored_geometry_variant"],
            "asset_key": content["content_key"],
            "content_key": content["content_key"],
            "content_hash": content_hash,
            "world_xz": tree["world_xz"],
            "authored_godot_y": tree["authored_godot_y"],
            "yaw_radians": tree["yaw_radians"],
            "procedural_scale": tree["procedural_scale"],
            "world_aabb_min": world_min,
            "world_aabb_max": world_max,
            "face_count": tree["face_count"],
            "authored_face_count": tree["authored_face_count"],
            "root_ecology_face_count": tree["root_ecology_face_count"],
            "root_base_world": tree.get("root_base_world", []),
            "root_base_half_size": tree.get("root_base_half_size", []),
            "blas_leaf_count": tree["blas_leaf_count"],
            "primitive_id_begin": tree["primitive_id_begin"],
            "primitive_id_end_exclusive": tree["primitive_id_end_exclusive"],
        }
        if tree.get("source_kind") == "compiled_meadow":
            # Clump traits belong to the placement record because the same
            # immutable payload content may be reused at another residency
            # origin in a future map. The face map is local to this record's
            # row range and has exactly one entry per published face.
            placement["vegetation_clumps"] = json.loads(
                json.dumps(tree.get("vegetation_clumps", []))
            )
            placement["vegetation_face_clumps"] = [
                int(value) for value in tree.get("vegetation_face_clumps", [])
            ]
            if len(placement["vegetation_face_clumps"]) != int(tree["face_count"]):
                raise CompileError(
                    f"meadow placement {source_id} face/clump mapping mismatch"
                )
        chunk["asset_instances"].append(placement)

    ordered_chunks: list[dict[str, Any]] = []
    for key in sorted(chunks):
        chunk = chunks[key]
        chunk["terrain_patches"].sort(key=lambda row: (int(row["patch_coord"][1]), int(row["patch_coord"][0])))
        chunk["asset_instances"].sort(key=lambda row: int(row["registry_slot"]))
        chunk["terrain_patch_count"] = len(chunk["terrain_patches"])
        chunk["asset_instance_count"] = len(chunk["asset_instances"])
        chunk["retained_static_face_count"] = sum(int(row["face_count"]) for row in chunk["asset_instances"])
        chunk["terrain_parity_digest"] = _sha256(_canonical_bytes([[row["patch_coord"], row["record_sha256"]] for row in chunk["terrain_patches"]]))
        chunk["asset_parity_digest"] = _sha256(_canonical_bytes(chunk["asset_instances"]))
        ordered_chunks.append(chunk)

    # Content records carry placement provenance without making the immutable
    # payload instance-keyed.  The current Y8 source happens to produce one
    # content record per placement, while this list remains correct if future
    # source rows deduplicate to a shared content key.
    for record in asset_records:
        record["chunk_ids"] = []
    for chunk in ordered_chunks:
        for placement in chunk["asset_instances"]:
            record = content_record_by_hash[placement["content_hash"]]
            record["chunk_ids"].append(chunk["chunk_id"])
    for record in asset_records:
        record["chunk_ids"] = sorted(set(record["chunk_ids"]))

    sorted_patch_rows = sorted(decoded["patches"], key=lambda row: (int(row["patch_coord"][1]), int(row["patch_coord"][0])))
    terrain_union_digest = _sha256(b"".join(struct.pack("<ii", *row["patch_coord"]) + row["_raw"] for row in sorted_patch_rows))
    policy = {
        "patches_per_chunk": PATCHES_PER_CHUNK,
        "visual_cells_per_patch": VISUAL_CELLS,
        "height_cells_per_patch": HEIGHT_CELLS,
        "chunk_cells": CHUNK_CELLS,
        "negative_coordinate_rule": "mathematical_floor_division",
        "meadow_coverage_spacing": MEADOW_COVERAGE_SPACING,
        "meadow_max_faces_per_chunk": MEADOW_MAX_FACES_PER_CHUNK,
        "meadow_max_total_faces": MEADOW_MAX_TOTAL_FACES,
    }
    registry = {
        "registry_slot_base": REGISTRY_SLOT_BASE,
        "registry_slot_count": len(trees),
        "registry_slot_range": [REGISTRY_SLOT_BASE, REGISTRY_SLOT_BASE + len(trees) - 1],
        "authored_registry_slot_range": [0, tree_count - len(lab_trees) - 1],
        "lab_registry_slot_range": [tree_count - len(lab_trees), tree_count - 1],
        "meadow_registry_slot_range": [tree_count + fixture_count, len(trees) - 1],
        "assignment": "authored manifest source-id order, lab-grove order, then meadow chunk order, independent of residency",
        "semantic_object_id_formula": "base_instance_count + registry_slot",
        "fixed_instance_reserved_base": FIXED_INSTANCE_RESERVED_BASE,
        "fixed_instance_reserved_count": FIXED_INSTANCE_RESERVED_COUNT,
    }
    if include_fixtures:
        registry["fixture_registry_slot_range"] = [tree_count, tree_count + fixture_count - 1]
        registry["assignment"] = (
            "authored manifest source-id order, lab-grove order, authored fixture "
            "source-id order, then meadow chunk order, independent of residency"
        )
    # Authored geometry provenance.  One row per .akm actually drawn, with the
    # exact file digest and the retained-material split this compiler derived
    # from it.  A changed mesh file therefore changes the catalog hash.
    tree_mesh_sources: list[dict[str, Any]] = []
    for mesh_name in sorted({str(tree["source_mesh_name"]) for tree in trees[:tree_count]}):
        mesh = load_tree_mesh(mesh_name, mesh_root)
        tree_mesh_sources.append(
            {
                "mesh_name": mesh_name,
                "path": _relative_path(mesh["path"]),
                "sha256": mesh["sha256"],
                "face_count": mesh["face_count"],
                "material_face_counts": {
                    str(TREE_TRUNK_MAT): mesh["role_counts"]["trunk"],
                    str(TREE_CANOPY_CONTOUR_MAT): mesh["role_counts"]["canopy_contour"],
                    str(TREE_CANOPY_INTERIOR_MAT): mesh["role_counts"]["canopy_interior"],
                    str(TREE_CANOPY_UNDERSIDE_MAT): mesh["role_counts"]["canopy_underside"],
                },
                "placement_count": sum(
                    1 for tree in trees if str(tree["source_mesh_name"]) == mesh_name
                ),
            }
        )
    fixture_mesh_sources: list[dict[str, Any]] = []
    for mesh_name in sorted({str(row["source_mesh_name"]) for row in fixtures}):
        mesh = load_fixture_mesh(mesh_name, mesh_root)
        fixture_mesh_sources.append(
            {
                "mesh_name": mesh_name,
                "path": _relative_path(mesh["path"]),
                "sha256": mesh["sha256"],
                "face_count": mesh["face_count"],
                "material_face_counts": {str(AUTHORED_FIXTURE_MAT): mesh["face_count"]},
                "placement_count": sum(
                    1 for row in fixtures if str(row["source_mesh_name"]) == mesh_name
                ),
            }
        )
    asset_payload = {
        "path": _relative_path(payload_path),
        "runtime_path": "res://" + _relative_path(payload_path),
        "magic": PAYLOAD_MAGIC.decode("ascii"),
        "version": PAYLOAD_VERSION,
        "header_bytes": PAYLOAD_HEADER_BYTES,
        "byte_length": len(payload_bytes),
        "sha256": payload_sha256,
        "asset_count": len(asset_records),
    }
    catalog: dict[str, Any] = {
        "$schema": SCHEMA,
        "header": {
            "schema_version": 1,
            "compiler_version": COMPILER_VERSION,
            "source_map_path": _relative_path(map_path),
            "source_map_sha256": map_sha256,
            "tree_manifest_path": _relative_path(tree_manifest_path),
            "asset_table_hash": tree_manifest_sha256,
            "tree_geometry_source": "authored_akm_mesh",
            "understory_geometry_source": "terrain_supported_actual_trunk_base",
            "tree_mesh_root": _relative_path(mesh_root),
            "tree_mesh_sources": tree_mesh_sources,
            "coordinate_convention": "A3D X/Y horizontal minus center_offset -> Godot X/Z; A3D height / 16 -> Godot Y",
            "center_offset_source_cells": decoded["center_offset"],
            "chunking_policy": policy,
            "chunking_policy_hash": _sha256(_canonical_bytes(policy)),
            "material_table_sha256": decoded["material_sha256"],
            "asset_payload": asset_payload,
            "semantic_object_registry": registry,
            "catalog_hash": "",
        },
        "asset_records": asset_records,
        "chunks": ordered_chunks,
        "union_receipt": {
            "chunk_count": len(ordered_chunks),
            "terrain_patch_count": decoded["patch_count"],
            "terrain_patch_union_sha256": terrain_union_digest,
            "asset_instance_count": len(trees),
            "asset_instance_union_sha256": _sha256(_canonical_bytes(trees)),
            "content_asset_count": len(asset_records),
            "asset_payload_byte_length": len(payload_bytes),
            "asset_payload_sha256": payload_sha256,
            "retained_static_face_count": sum(int(row["face_count"]) for row in trees),
            "max_asset_face_count": max(int(row["face_count"]) for row in trees),
            "max_asset_blas_leaf_count": max(int(row["blas_leaf_count"]) for row in trees),
        },
    }
    if include_fixtures:
        catalog["header"]["fixture_geometry_source"] = "authored_akm_mesh"
        catalog["header"]["fixture_material"] = {
            "material_id": AUTHORED_FIXTURE_MAT,
            "paint_owner": "cell_owner_product.glsl authored-colour lane (material 240 + 4708 tag)",
            "shade": AUTHORED_FIXTURE_SHADE,
        }
        catalog["header"]["fixture_mesh_sources"] = fixture_mesh_sources
        catalog["header"]["fixture_placement_count"] = fixture_count
        catalog["header"]["fixtures_hosted_by_nearest_chunk"] = fixtures_outside_terrain
        catalog["header"]["lab_grove_included"] = lab_grove
        catalog["header"]["meadow_included"] = meadow
    hash_input = json.loads(json.dumps(catalog))
    hash_input["header"]["catalog_hash"] = ""
    catalog["header"]["catalog_hash"] = _sha256(_canonical_bytes(hash_input))
    return CompiledCatalog(catalog, payload_bytes, payload_path)


# FL-4876 PUBLISH-01: an indented catalog for an OSM-pipeline map (Numabe,
# 2509 assets) renders 114 MB, over the 100 MB per-file limit of the remote
# with Git LFS banned; the same document is 48 MB compact. catalog_hash is
# taken over canonical bytes, so the rendering choice never changes it, and
# every catalog under the threshold keeps its exact indented bytes.
COMPACT_RENDER_THRESHOLD_BYTES = 90_000_000


def render_catalog(catalog: dict[str, Any]) -> str:
    text = json.dumps(dict(catalog), indent=2, sort_keys=True) + "\n"
    if len(text.encode("utf-8")) <= COMPACT_RENDER_THRESHOLD_BYTES:
        return text
    return json.dumps(dict(catalog), sort_keys=True, separators=(",", ":")) + "\n"


def _write_or_check(catalog: CompiledCatalog, output: Path, payload_path: Path, check: bool) -> int:
    rendered = render_catalog(catalog)
    output = output.resolve()
    payload_path = payload_path.resolve()
    if check:
        if not output.is_file() or output.read_text() != rendered:
            print(f"FL4789_CHUNK_CATALOG_STALE output={output}")
            return 1
        if not payload_path.is_file() or payload_path.read_bytes() != catalog.payload_bytes:
            print(f"FL4789_CHUNK_PAYLOAD_STALE payload={payload_path}")
            return 1
        receipt = catalog["union_receipt"]
        print(
            "FL4789_CHUNK_CATALOG_OK "
            f"chunks={receipt['chunk_count']} patches={receipt['terrain_patch_count']} "
            f"assets={receipt['asset_instance_count']} content={receipt['content_asset_count']} "
            f"faces={receipt['retained_static_face_count']} "
            f"payload_sha256={receipt['asset_payload_sha256']} "
            f"catalog_hash={catalog['header']['catalog_hash']}"
        )
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered)
    payload_path.write_bytes(catalog.payload_bytes)
    print(f"FL4789_CHUNK_CATALOG_WRITTEN output={output} payload={payload_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--tree-manifest", type=Path, default=DEFAULT_TREE_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--payload", type=Path, default=DEFAULT_PAYLOAD)
    parser.add_argument("--mesh-root", type=Path, default=DEFAULT_MESH_ROOT)
    parser.add_argument(
        "--fixtures",
        action="store_true",
        help="FL-4798: compile every non-tree authored .akm instance of the manifest (default)",
    )
    parser.add_argument("--no-fixtures", action="store_true", help="omit authored fixtures")
    parser.add_argument(
        "--no-lab-grove",
        action="store_true",
        help="omit the three fixed FL-4708 Y8 lab-grove anchors (non-Y8 maps)",
    )
    parser.add_argument("--no-meadow", action="store_true", help="omit compiled meadow chunks")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    catalog = compile_catalog(
        args.map,
        args.tree_manifest,
        args.payload,
        args.mesh_root,
        include_fixtures=not args.no_fixtures,
        lab_grove=not args.no_lab_grove,
        meadow=not args.no_meadow,
    )
    return _write_or_check(catalog, args.output, args.payload, args.check)


if __name__ == "__main__":
    raise SystemExit(main())
