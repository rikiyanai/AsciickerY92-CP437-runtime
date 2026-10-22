#!/usr/bin/env python3
"""FL-4431 D20/D21 retained-semantic selection/fidelity contract.

Activation physically deleted the flat static b5 and dynamic b6 geometry
banks.  The broad activation battery owns that deletion and the cross-stage
binding agreement.  This narrower contract now proves the retained-selector
world that replaced them: the two flat-bank read tokens are absent from the
whole Product shader, every shipping consumer reaches its named retained
handoff, each handoff publishes the exact fields its consumer needs, and the
selection/value laws remain stable.

Vapor has one additional ownership boundary: actor-effect kind 4 must stay out
of VSB primary-surface arbitration, then Product must discover materials 207,
211, and 234 through the retained dynamic selector and alpha-layer them over
the opaque b64 receiver.  This contract pins both sides so a future edit cannot
silently restore vapor as an opaque primary owner.

The legacy consumer list below is history with an explicit disposition per
entry, not a live flat-bank inventory.  A retired function cannot remain in a
closure contract merely to keep a count green.  A renamed behavior is guarded
at its current callable owner, and a kept behavior is guarded under the same
callable name.

Round-2 review additions (this file is the executable record of all three
blockers):

* B1 — traversals must fail closed on placeholder/truncated tree bindings
  (authoritative shape gates) and must terminate on malformed node banks
  (exact pop budget).  `tree_shape_gate_witness` and
  `traversal_budget_witness` execute those laws on CPU fixtures.
* B2 — exact-value ties in dynamic selection must be broken by the STABLE
  mover identity (stable id, mover-local face), never by the publication-
  global legacy face index, because dynamic bank positions depend on
  per-frame publication order.  `dynamic_reorder_witness` rebuilds the bank
  in two layouts and proves the selected identity is invariant while the
  global indices differ.
* B3 — the retained ABI carries the authoritative FORWARD basis rows; the
  shader never reconstructs them by inverting the serialized float32
  inverse (a lossy round trip).  `asymmetric_transform_witness` executes
  both paths with float32 checkpoints and shows the round trip diverges
  while the serialized-forward path reproduces the flatten authority.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


import math
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRODUCT_PATH = ROOT / "godot_project/render/product/cell_owner_product.glsl"
VSB_PATH = ROOT / "godot_project/render/backends/ortho/visibility/visible_surface_builder.glsl"
HOST_PATH = ROOT / "godot_project/render/api/ASCIICompositorEffect.gd"
DB_PATH = ROOT / "godot_project/render/scene/SceneGeometryDB.gd"
BUILDER_PATH = ROOT / "godot_project/render/core/contracts/visibility/VisibleSurfaceABI.gd"

STATIC_TOKEN = "static_world_mesh_faces.face_rows["
DYNAMIC_TOKEN = "dynamic_mover_mesh_faces.face_rows["

# These names are the complete pre-activation flat-bank consumer inventory.
# They remain only so every deletion and ownership move has a durable reason.
LEGACY_FLAT_BANK_CONSUMERS = {
    "compute_product_foliage_fact",
    "product_static_face_geometric_normal",
    "static_mesh_shadow_fact_linear",
    "static_mesh_shadow_fact_at_world",
    "product_bvh_point_light_leaf_blocked",
    "product_bvh_sun_shadow_min_face",
    "product_leaf_emitter_main",
    "compute_static_tree_leaf_cell_product_scan",
    "product_static_mesh_hit_for_face",
    "product_hit_from_probe_record",
    "q3_probe_payload_static_shadow_fact",
    "q3_probe_payload_canopy_coverage_at_cell",
    "dynamic_mover_shadow_fact_at_world",
    "compute_vapor_cell_product",
    "product_dynamic_mesh_hit_for_face",
    "product_airplane_hit_for_ray",
    "resolve_raster_splat_owner",
    "product_hit_is_player_airplane",
}

# One plain-English line per former consumer records why its old entry was
# kept, renamed, and retired.  The category prefix is machine-counted below.
LEGACY_CONSUMER_DISPOSITIONS = {
    "compute_product_foliage_fact": "RETIRED: the retained terrain-foliage winner replaced its per-quarter role; FL-4708 also deleted the old tree-canopy role.",
    "product_static_face_geometric_normal": "RETIRED: Product-side normal reconstruction died; the VSB record now owns world_normal and the activation battery guards that stage boundary.",
    "static_mesh_shadow_fact_linear": "RENAMED: q3_probe_payload_static_shadow_density owns the shipping sun-shadow query through the retained Q3 selector.",
    "static_mesh_shadow_fact_at_world": "RENAMED: q3_probe_payload_static_shadow_density owns the shipping world-space sun-shadow query.",
    "product_bvh_point_light_leaf_blocked": "RENAMED: vsb_product_local_light_visibility owns point-light blocker discovery through the retained static selector plus retained actor-body facts; the deleted pre-cutover Product owner never admitted airplanes as point-light blockers.",
    "product_bvh_sun_shadow_min_face": "RENAMED: q3_probe_payload_static_shadow_density preserves the minimum admitted static-face law while returning only the consumed scalar.",
    "product_leaf_emitter_main": "RETIRED: FL-4708 removed the falling-leaf runtime owner with the old tree implementation.",
    "compute_static_tree_leaf_cell_product_scan": "RETIRED: FL-4708 removed the tree-dependent scan instead of preserving a successor seam.",
    "product_static_mesh_hit_for_face": "RENAMED: vsb_product_prepare_static consumes the VSB-selected identity and retained face facts; VSB owns geometric intersection.",
    "product_hit_from_probe_record": "RETIRED: the probe record and its geometry bridge were physically deleted, leaving no probe subject to adapt.",
    "q3_probe_payload_static_shadow_fact": "RENAMED: q3_probe_payload_static_shadow_density keeps the retained static selector while deleting the discarded caster provenance return.",
    "q3_probe_payload_canopy_coverage_at_cell": "RETIRED: FL-4708 delete-first removed the five-lobe AABB painter before the retained-card successor lands.",
    "dynamic_mover_shadow_fact_at_world": "RENAMED: dynamic_mover_shadow_density_at_world keeps the retained dynamic selector while deleting the discarded caster provenance return.",
    "compute_vapor_cell_product": "RENAMED: vsb_product_finalize_record selects retained vapor independently and layers it over the opaque VSB receiver.",
    "product_dynamic_mesh_hit_for_face": "RENAMED: vsb_product_prepare_dynamic reads mover-local retained face facts after VSB selection.",
    "product_airplane_hit_for_ray": "RETIRED: VSB owns airplane face election; Product consumes its validated mover-local identity through retained point lookup.",
    "resolve_raster_splat_owner": "RETIRED: raster-splat arbitration was deleted with the raster owner; the final Product record has one VSB-selected owner.",
    "product_hit_is_player_airplane": "RETIRED: Product consumes the VSB-selected dynamic identity without a second airframe predicate or geometry selector.",
}

# A handoff row is (accessor, required published fields, discovery selector).
# A shipping owner may need several handoffs, so each owner maps to a tuple.
ConsumerHandoff = tuple[str, set[str], bool]
ConsumerClosureMap = dict[str, tuple[ConsumerHandoff, ...]]
CONSUMER_CLOSURES: ConsumerClosureMap = {
    "q3_probe_payload_static_shadow_density": (
        ("vsb_retained_select_q3_static_shadow_caster", {"valid"}, True),
    ),
    "dynamic_mover_shadow_density_at_world": (
        ("vsb_retained_select_dynamic_shadow_caster", {"valid", "density"}, True),
    ),
    "vsb_product_local_light_visibility": (
        ("vsb_retained_select_point_light_caster", {"valid", "depth", "legacy_face_index"}, True),
    ),
    "vsb_product_prepare_static": (
        ("vsb_retained_static_face_local_facts", {"row_0", "row_1", "row_2", "row_3", "material_id", "legacy_face_index"}, False),
    ),
    "vsb_product_prepare_dynamic": (
        ("vsb_retained_mover_actor_effect_facts", {"row_0", "row_1", "row_2", "row_3", "material_id", "actor_effect_kind", "source_id"}, False),
    ),
    "vsb_product_finalize_record": (
        (
            "vsb_retained_select_vapor_effect_candidates",
            {"bonfire_smoke", "airplane_vapor", "identity", "facts", "depth", "source_id", "actor_effect_kind"},
            True,
        ),
    ),
}

GLSL_SIGNATURE = re.compile(
    r"^(?:precise\s+)?[A-Za-z_][A-Za-z0-9_]*\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.M
)


def strip_comments(source: str) -> str:
    """Remove prose before checking executable ownership tokens."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def glsl_functions(source: str) -> dict[str, str]:
    """Return GLSL definitions with balanced-brace bodies."""
    functions: dict[str, str] = {}
    for match in GLSL_SIGNATURE.finditer(source):
        brace = source.find("{", match.end())
        semicolon = source.find(";", match.end())
        if brace < 0 or (semicolon >= 0 and semicolon < brace):
            continue
        depth = 0
        for cursor in range(brace, len(source)):
            depth += source[cursor] == "{"
            depth -= source[cursor] == "}"
            if depth == 0:
                functions[match.group(1)] = source[match.start() : cursor + 1]
                break
    return functions


def gd_function_body(source: str, name: str) -> str:
    """Extract one top-level GDScript function through the next declaration."""
    match = re.search(rf"^func {re.escape(name)}\([^\n]*", source, re.M)
    assert match, f"missing GDScript function {name}"
    next_function = re.search(r"^func ", source[match.end() :], re.M)
    end = match.end() + next_function.start() if next_function else len(source)
    return source[match.start() : end]


def inventory(functions: dict[str, str], token: str) -> dict[str, int]:
    """Count literal raw-bank loads per owning live closure."""
    return {name: body.count(token) for name, body in functions.items() if token in body}


def reachable_tokens(functions: dict[str, str], root: str) -> str:
    """Concatenate a root's retained-helper closure for output-law checks."""
    seen: set[str] = set()
    pending = [root]
    chunks: list[str] = []
    while pending:
        name = pending.pop()
        if name in seen or name not in functions:
            continue
        seen.add(name)
        body = functions[name]
        chunks.append(body)
        for callee in re.findall(r"\b(vsb_retained_[A-Za-z0-9_]+)\s*\(", body):
            if callee not in seen:
                pending.append(callee)
    return "\n".join(chunks)


def shipping_reachable_tokens(functions: dict[str, str], root: str) -> str:
    """Concatenate every callable shipping adapter reachable from one owner.

    Shipping consumers sometimes cross a small non-retained adapter before
    reaching the retained selector.  Following only names prefixed
    ``vsb_retained_`` would miss that real call edge and reduce the check to a
    same-file name search.  This walk follows definitions known to the parser,
    remains bounded by ``seen``, and therefore proves the end-to-end edge.
    """
    seen: set[str] = set()
    pending = [root]
    chunks: list[str] = []
    while pending:
        name = pending.pop()
        if name in seen or name not in functions:
            continue
        seen.add(name)
        body = functions[name]
        chunks.append(body)
        for callee in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", body):
            if callee in functions and callee not in seen:
                pending.append(callee)
    return "\n".join(chunks)


def shipping_reachable_call_count(
    functions: dict[str, str],
    root: str,
    target: str,
) -> int:
    """Count target call sites across the complete shipping closure.

    Function signatures are excluded by scanning only text after each opening
    brace. This catches a second traversal hidden behind a reachable adapter,
    which a direct-call count in the root function cannot see.
    """
    seen: set[str] = set()
    pending = [root]
    target_calls = 0
    while pending:
        name = pending.pop()
        if name in seen or name not in functions:
            continue
        seen.add(name)
        body = functions[name]
        executable_body = body[body.index("{") + 1 :]
        callees = re.findall(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            executable_body,
        )
        target_calls += sum(callee == target for callee in callees)
        for callee in callees:
            if callee in functions and callee not in seen:
                pending.append(callee)
    return target_calls


def f32(value: float) -> float:
    """Round one arithmetic checkpoint to the shader's IEEE float32 domain."""
    return struct.unpack("<f", struct.pack("<f", value))[0]


def mat_vec(matrix: list[list[float]], vector: tuple[float, float, float]) -> tuple[float, float, float]:
    """Multiply a row-major 3x3 matrix by a vector with float32 checkpoints."""
    return tuple(f32(sum(f32(matrix[row][col] * vector[col]) for col in range(3))) for row in range(3))  # type: ignore[return-value]


def inverse_3x3_f64(matrix: list[list[float]]) -> list[list[float]]:
    """Invert a 3x3 matrix in binary64 (models the CPU-side basis inverse)."""
    a, b, c = matrix[0]
    d, e, f = matrix[1]
    g, h, i = matrix[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    assert det != 0.0
    return [
        [(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
        [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
        [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det],
    ]


def inverse_3x3_f32(matrix: list[list[float]]) -> list[list[float]]:
    """Invert a 3x3 matrix with float32 checkpoints at every arithmetic step.

    This models what GLSL `inverse(mat3)` does to float32 inputs: cofactors,
    determinant, and divisions all round to float32.  It exists to DEMONSTRATE
    the production rounding hazard the retained ABI now avoids by serializing
    the forward rows directly.
    """
    a, b, c = matrix[0]
    d, e, f = matrix[1]
    g, h, i = matrix[2]
    cof = [
        [f32(f32(e * i) - f32(f * h)), f32(f32(c * h) - f32(b * i)), f32(f32(b * f) - f32(c * e))],
        [f32(f32(f * g) - f32(d * i)), f32(f32(a * i) - f32(c * g)), f32(f32(c * d) - f32(a * f))],
        [f32(f32(d * h) - f32(e * g)), f32(f32(b * g) - f32(a * h)), f32(f32(a * e) - f32(b * d))],
    ]
    det = f32(f32(a * cof[0][0]) + f32(b * cof[1][0]) + f32(c * cof[2][0]))
    assert det != 0.0
    return [[f32(cof[row][col] / det) for col in range(3)] for row in range(3)]


def quantize_matrix(matrix: list[list[float]]) -> list[list[float]]:
    """Round every matrix entry to float32 (serialization to the retained ABI)."""
    return [[f32(entry) for entry in row] for row in matrix]


def asymmetric_transform_witness() -> None:
    """Serialized FORWARD rows reproduce the flatten authority; the inverse
    round trip does not.

    The fixture basis is deliberately non-trivial: its entries, cofactors, and
    determinant all round in float32, so `inverse(f32(inverse(B)))` cannot
    recover B bit-for-bit.  The authority is the flatten law itself — float32
    checkpoints of `origin + B * (local * scale)` using the float32 basis rows
    the DB serializes.
    """
    exact_basis = [[0.8, -0.6, 0.1], [0.6, 0.8, -0.2], [0.05, 0.15, 0.9]]
    # The serialized authority is the float32-quantized forward basis — the
    # same rows SceneGeometryDB.encode_float writes at instance offsets 64-108.
    forward = quantize_matrix(exact_basis)
    origin = (7.0, -3.0, 11.0)
    local = (0.25, -0.5, 1.5)
    scale = 2.0
    scaled = tuple(f32(component * scale) for component in local)
    reference_delta = mat_vec(forward, scaled)
    reference = tuple(f32(origin[index] + reference_delta[index]) for index in range(3))
    # Retained-ABI path: the shader reads the SAME serialized forward rows, so
    # its float32 evaluation reproduces the authority exactly by construction.
    abi_delta = mat_vec(forward, scaled)
    abi_result = tuple(f32(origin[index] + abi_delta[index]) for index in range(3))
    assert abi_result == reference, (abi_result, reference)
    # Rejected reconstruction: serialize only the inverse, then invert it in
    # float32 (what the deleted `inverse(inverse_basis)` shader code did).
    serialized_inverse = quantize_matrix(inverse_3x3_f64(forward))
    round_trip = inverse_3x3_f32(serialized_inverse)
    assert round_trip != forward, "float32 inverse round trip unexpectedly exact"
    round_trip_delta = mat_vec(round_trip, scaled)
    round_trip_result = tuple(
        f32(origin[index] + round_trip_delta[index]) for index in range(3)
    )
    assert round_trip_result != reference, (
        "inverse round trip reproduced the flatten authority; "
        "the fixture no longer demonstrates the rounding hazard"
    )
    # The naive transposed shortcut stays wrong as well.
    shortcut_delta = mat_vec(serialized_inverse, scaled)
    shortcut = tuple(f32(origin[index] + shortcut_delta[index]) for index in range(3))
    assert shortcut != reference, "inverse-basis shortcut unexpectedly matched fixture"


@dataclass(frozen=True)
class DynamicFace:
    """One mover-local candidate used by the bank-reorder witness."""

    local_face: int
    density: float
    depth: float
    kind: int


@dataclass(frozen=True)
class DynamicMover:
    """One retained mover: a stable id plus its mover-local faces."""

    object_id: int
    faces: tuple[DynamicFace, ...]


def bank_candidates(layout: list[DynamicMover]) -> list[tuple[int, int, int, DynamicFace]]:
    """Assign publication-global legacy indices for one bank layout.

    Returns (object_id, local_face, legacy_face, face) tuples where
    legacy_face = first_face + local_face depends on the layout order —
    exactly how compositor ranges and SceneGeometryDB first_face behave.
    """
    candidates: list[tuple[int, int, int, DynamicFace]] = []
    first_face = 0
    for mover in layout:
        for face in mover.faces:
            candidates.append((mover.object_id, face.local_face, first_face + face.local_face, face))
        first_face += len(mover.faces)
    return candidates


def select_dynamic(layout: list[DynamicMover], mode: str) -> tuple[tuple[int, int], int]:
    """Mirror the shader's value law plus STABLE-IDENTITY tie law.

    Returns ((object_id, local_face), legacy_face) so callers can assert the
    identity is layout-invariant while the global index is not.
    """
    wanted_kind = 4 if mode == "vapor" else 1
    selected: tuple[int, int] | None = None
    selected_value = None
    selected_legacy = -1
    for object_id, local_face, legacy_face, face in bank_candidates(layout):
        if face.kind != wanted_kind:
            continue
        value = face.density if mode == "shadow" else face.depth
        better = (
            selected_value is None
            or (value > selected_value if mode == "shadow" else value < selected_value)
        )
        # Exact tie: bitwise-equal value resolved by (stable id, local face) —
        # the shader's vsb_retained_dynamic_identity_precedes law.
        tie_earlier = (
            selected is not None
            and struct.pack("<f", value) == struct.pack("<f", selected_value)
            and (object_id, local_face) < selected
        )
        if not better and not tie_earlier:
            continue
        selected = (object_id, local_face)
        selected_value = value
        selected_legacy = legacy_face
    assert selected is not None
    return selected, selected_legacy


def dynamic_reorder_witness() -> None:
    """Selected identity must survive a bank reorder; global indices must not.

    Layout A and layout B publish the same movers in opposite order, so every
    candidate's publication-global legacy index changes.  The witness includes
    bitwise-equal exact ties ACROSS movers — the case where a global-index tie
    law would flip winners between layouts.
    """
    airplane_a = DynamicMover(0x0100002A, (
        DynamicFace(0, 0.75, 7.0, 1),
        DynamicFace(1, 0.25, 9.0, 1),
    ))
    airplane_b = DynamicMover(0x01000007, (
        DynamicFace(0, 0.75, 7.0, 1),  # exact tie with airplane_a face 0
    ))
    # vapor_a carries TWO faces so mover offsets genuinely shift between the
    # two layouts (unequal face counts prevent coincidentally equal offsets).
    vapor_a = DynamicMover(0x09009C40, (
        DynamicFace(0, 0.0, 2.5, 4),
        DynamicFace(1, 0.0, 9.9, 4),
    ))
    vapor_b = DynamicMover(0x09009C41, (DynamicFace(0, 0.0, 2.5, 4),))  # exact tie
    layout_a = [airplane_a, airplane_b, vapor_a, vapor_b]
    layout_b = [vapor_b, vapor_a, airplane_b, airplane_a]
    for mode in ("shadow", "airplane", "vapor"):
        identity_a, legacy_a = select_dynamic(layout_a, mode)
        identity_b, legacy_b = select_dynamic(layout_b, mode)
        assert identity_a == identity_b, (mode, identity_a, identity_b)
        assert legacy_a != legacy_b, (
            f"{mode}: reorder fixture did not move the winner's global index; "
            "the witness would not detect a global-index tie law"
        )
    # The tie winners are decided by stable identity, not publication position:
    # airplane_b has the smaller stable id and must win the shadow/airplane tie
    # in BOTH layouts even though it is published last in layout A's reverse.
    assert select_dynamic(layout_a, "shadow")[0] == (0x01000007, 0)
    assert select_dynamic(layout_a, "vapor")[0] == (0x09009C40, 0)


@dataclass(frozen=True)
class VaporFace:
    """One retained kind-4 face for the composite-selector parity fixture."""

    object_id: int
    local_face: int
    material_id: int
    actor_effect_kind: int
    source_id: int
    depth: float


def vapor_material_lane(material_id: int) -> str | None:
    """Map only the three reviewed vapor materials to their paint lanes."""
    if material_id == 234:
        return "bonfire_smoke"
    if material_id in (207, 211):
        return "airplane_vapor"
    return None


def select_vapor_lane(
    faces: tuple[VaporFace, ...],
    world_depth: float,
    lane: str,
) -> VaporFace | None:
    """Mirror one deleted boolean-partitioned selector pass on the CPU."""
    selected: VaporFace | None = None
    for face in faces:
        if (
            face.actor_effect_kind != 4
            or face.source_id == 0
            or vapor_material_lane(face.material_id) != lane
            or face.depth < 0.0
            or face.depth > world_depth + 0.25
        ):
            continue
        nearer = selected is None or face.depth < selected.depth
        exact_tie_earlier = (
            selected is not None
            and struct.pack("<f", face.depth)
            == struct.pack("<f", selected.depth)
            and (face.object_id, face.local_face)
            < (selected.object_id, selected.local_face)
        )
        if nearer or exact_tie_earlier:
            selected = face
    return selected


def select_vapor_composite(
    faces: tuple[VaporFace, ...],
    world_depth: float,
) -> dict[str, VaporFace | None]:
    """Mirror the new one-pass selector while keeping two winner registers."""
    selected: dict[str, VaporFace | None] = {
        "bonfire_smoke": None,
        "airplane_vapor": None,
    }
    for face in faces:
        lane = vapor_material_lane(face.material_id)
        if (
            lane is None
            or face.actor_effect_kind != 4
            or face.source_id == 0
            or face.depth < 0.0
            or face.depth > world_depth + 0.25
        ):
            continue
        current = selected[lane]
        nearer = current is None or face.depth < current.depth
        exact_tie_earlier = (
            current is not None
            and struct.pack("<f", face.depth)
            == struct.pack("<f", current.depth)
            and (face.object_id, face.local_face)
            < (current.object_id, current.local_face)
        )
        if nearer or exact_tie_earlier:
            selected[lane] = face
    return selected


def vapor_composite_parity_witness() -> None:
    """One traversal must equal two historical passes across edge cases."""
    cases = (
        (
            8.0,
            (
                VaporFace(40, 0, 234, 4, 3, 4.0),
                VaporFace(20, 0, 207, 4, 7, 3.0),
                VaporFace(10, 1, 211, 4, 9, 3.0),
                VaporFace(1, 0, 999, 4, 1, 0.5),
            ),
        ),
        (
            5.0,
            (
                VaporFace(5, 0, 234, 4, 1, 5.25),
                VaporFace(4, 0, 234, 4, 1, 5.2501),
                VaporFace(3, 0, 207, 4, 0, 1.0),
                VaporFace(2, 0, 211, 1, 1, 1.0),
                VaporFace(1, 0, 207, 4, 1, -0.1),
            ),
        ),
        (
            2.0,
            (
                VaporFace(9, 0, 234, 4, 2, 2.26),
                VaporFace(8, 0, 211, 4, 2, 2.25),
                VaporFace(7, 0, 207, 4, 2, 1.75),
            ),
        ),
    )
    for world_depth, faces in cases:
        composite = select_vapor_composite(faces, world_depth)
        for lane in ("bonfire_smoke", "airplane_vapor"):
            assert composite[lane] == select_vapor_lane(
                faces, world_depth, lane
            ), (world_depth, lane, composite[lane])
    first = select_vapor_composite(cases[0][1], cases[0][0])
    assert first["bonfire_smoke"] == cases[0][1][0]
    assert first["airplane_vapor"] == cases[0][1][2]
    second = select_vapor_composite(cases[1][1], cases[1][0])
    assert second["bonfire_smoke"] == cases[1][1][0]
    assert second["airplane_vapor"] is None
    third = select_vapor_composite(cases[2][1], cases[2][0])
    assert third["bonfire_smoke"] is None
    assert third["airplane_vapor"] == cases[2][1][2]


@dataclass(frozen=True)
class TreeBanks:
    """Apparent buffer lengths plus node words for the gate/budget witnesses."""

    leaf_ids: tuple[int, ...]
    instance_count: int
    # Each node = (left_child_word, right_child_word); a leaf child has the
    # high bit set (mirroring product_bvh_is_leaf).
    nodes: tuple[tuple[int, int], ...]


LEAF_BIT = 0x80000000


def traverse_gated(banks: TreeBanks) -> tuple[list[int], int]:
    """CPU mirror of the shader's gated, budgeted TLAS walk.

    Returns (visited leaf slots, pops used).  The shape gates reject before
    any node load; the pop budget bounds any walk that still starts.
    """
    leaf_count = len(banks.leaf_ids)
    # Authoritative shape gate — identical law to the shader.
    if leaf_count == 0 or leaf_count != banks.instance_count:
        return [], 0
    if leaf_count == 1:
        return [0], 0
    if len(banks.nodes) * 2 != (leaf_count - 1) * 2:
        return [], 0
    node_count = leaf_count - 1
    visited: list[int] = []
    stack = [0]
    pops = 0
    while stack and pops < node_count:
        pops += 1
        node = banks.nodes[stack.pop()]
        for child_word in node:
            if child_word & LEAF_BIT:
                leaf_slot = child_word & ~LEAF_BIT
                if leaf_slot < leaf_count:
                    visited.append(leaf_slot)
            elif child_word < node_count and len(stack) < 64:
                stack.append(child_word)
    return visited, pops


def tree_shape_gate_witness() -> None:
    """Placeholder and truncated banks must be rejected before any node load."""
    # The host's shared 64-zero-byte placeholder: 16 apparent leaf ids, zero
    # apparent instances, 2 apparent nodes.  Without the instance cross-check
    # this shape previously walked node 0 forever.
    placeholder = TreeBanks(leaf_ids=(0,) * 16, instance_count=0, nodes=((0, 0), (0, 0)))
    assert traverse_gated(placeholder) == ([], 0), "placeholder banks must fail closed"
    # A truncated node bank (leaf/instance counts agree, node rows short).
    truncated = TreeBanks(leaf_ids=(0, 1, 2, 3), instance_count=4, nodes=((1, 2),))
    assert traverse_gated(truncated) == ([], 0), "truncated tree must fail closed"
    # A well-formed 4-leaf tree still visits every leaf inside the budget.
    valid = TreeBanks(
        leaf_ids=(0, 1, 2, 3),
        instance_count=4,
        nodes=((1, 2), (LEAF_BIT | 0, LEAF_BIT | 1), (LEAF_BIT | 2, LEAF_BIT | 3)),
    )
    visited, pops = traverse_gated(valid)
    assert sorted(visited) == [0, 1, 2, 3] and pops == 3, (visited, pops)


def traversal_budget_witness() -> None:
    """A node cycle inside a correctly sized bank terminates at the budget."""
    # Node 0 lists itself as both children: correct row count, malicious
    # content.  Every pop re-pushes node 0; only the pop budget ends the walk.
    cycle = TreeBanks(
        leaf_ids=(0, 1, 2, 3),
        instance_count=4,
        nodes=((0, 0), (0, 0), (0, 0)),
    )
    visited, pops = traverse_gated(cycle)
    assert visited == [] and pops == 3, (
        "cycle walk must terminate at the node-count budget with no selection"
    )


def swap_adjacent_blocks(
    source: str,
    first_marker: str,
    second_marker: str,
    end_marker: str,
) -> str:
    """Swap two adjacent source blocks while preserving their exact bytes."""
    first = source.index(first_marker)
    second = source.index(second_marker, first)
    end = source.index(end_marker, second)
    assert first < second < end
    return (
        source[:first]
        + source[second:end]
        + source[first:second]
        + source[end:]
    )


def replace_in_source_block(
    source: str,
    start_marker: str,
    end_marker: str,
    old: str,
    new: str,
) -> str:
    """Replace one token only inside the named source block."""
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    block = source[start:end]
    assert block.count(old) == 1
    return source[:start] + block.replace(old, new, 1) + source[end:]


def validate(
    product: str,
    host: str,
    db: str,
    builder: str,
    vsb: str,
    closure_map: ConsumerClosureMap | None = None,
) -> None:
    """Validate deleted banks, live handoffs, selection topology, and values."""
    functions = glsl_functions(product)
    vapor_composite_parity_witness()
    assert STATIC_TOKEN not in product, "D20 static b5 read token reappeared anywhere in Product"
    assert DYNAMIC_TOKEN not in product, "D21 dynamic b6 read token reappeared anywhere in Product"
    assert inventory(functions, STATIC_TOKEN) == {}, "D20 static b5 function inventory must stay empty"
    assert inventory(functions, DYNAMIC_TOKEN) == {}, "D21 dynamic b6 function inventory must stay empty"
    assert set(LEGACY_CONSUMER_DISPOSITIONS) == LEGACY_FLAT_BANK_CONSUMERS
    assert sum(value.startswith("RETIRED:") for value in LEGACY_CONSUMER_DISPOSITIONS.values()) == 9
    assert sum(value.startswith("RENAMED:") for value in LEGACY_CONSUMER_DISPOSITIONS.values()) == 9
    assert sum(value.startswith("KEPT:") for value in LEGACY_CONSUMER_DISPOSITIONS.values()) == 0

    # The source marker was renamed from its historical "activation
    # precondition" wording to "retained semantic authority" when the review
    # round-1 prose fix corrected the stale dormant/b5/b6 claims beside it
    # (cell_owner_product.glsl ~6113). This anchor must track the CURRENT
    # marker text: a missing marker raises ValueError here, which means this
    # entire gate silently stops running — exactly the review round-2 B2
    # verification failure this comment records.
    semantic_start = product.index("FL-4431 D20/D21 retained semantic authority")
    semantic_end = product.index("FL-4431 Q3 SLICE 4", semantic_start)
    semantic_section = product[semantic_start:semantic_end]
    assert STATIC_TOKEN not in semantic_section and DYNAMIC_TOKEN not in semantic_section
    assert not re.search(r"\b[ui]?sampler(?:1D|2D|3D|Cube)", semantic_section)

    # The retained selectors still own discovery, but stage 61 now consumes
    # only scalar density. Reject the deleted aggregate return type and the
    # three old provenance-returning adapter names so a future compatibility
    # shim cannot silently revive discarded caster id/XZ work.
    clean_product = strip_comments(product)
    assert not re.search(r"\bstruct\s+ShadowFact\b", clean_product)
    for retired in (
        "actor_row_shadow_fact_at_world",
        "q3_probe_payload_static_shadow_fact",
        "dynamic_mover_shadow_fact_at_world",
    ):
        assert not re.search(rf"\b{retired}\s*\(", clean_product), (
            f"discarded shadow provenance owner resurrected: {retired}"
        )

    # Consumer-specific closure compatibility: each current owner must exist,
    # must actually reach its named handoff, and must receive every required
    # field from that handoff's reachable retained closure.
    contracts = CONSUMER_CLOSURES if closure_map is None else closure_map
    for consumer, handoffs in contracts.items():
        assert consumer in functions, f"current retained consumer missing: {consumer}"
        consumer_closure = shipping_reachable_tokens(functions, consumer)
        for accessor, required_outputs, is_discovery in handoffs:
            assert accessor in functions, f"{consumer}: missing accessor {accessor}"
            assert re.search(rf"\b{re.escape(accessor)}\b", consumer_closure), (
                f"{consumer}: shipping call graph does not reach {accessor}"
            )
            accessor_closure = reachable_tokens(functions, accessor)
            for output in required_outputs:
                assert re.search(rf"\b{re.escape(output)}\b", accessor_closure), (
                    f"{consumer}: {accessor} does not supply required {output}"
                )
            if is_discovery:
                assert "select_" in accessor or "enumerate_" in accessor, (
                    f"{consumer}: discovery mapped to preselected-identity lookup {accessor}"
                )

    # Static discovery must walk TLAS -> BLAS with one admission owner:
    # primitive range, scan cap, and receiver-self exclusion run before face
    # materialization; material 250 is rejected afterward; the minimum legacy
    # face wins behind authoritative shape gates and pop budgets.
    static_select = reachable_tokens(functions, "vsb_retained_select_static_shadow_caster")
    for token in (
        "vsb_semantic_static_tlas_nodes",
        "vsb_semantic_static_tlas_ids",
        "vsb_semantic_static_blas_nodes",
        "vsb_semantic_static_blas_ids",
        "shadow_ray, vsb_semantic_static_tlas_ids.ids[leaf_slot]",
        "facts.material_id == 250u",
        "legacy_face_index >= selected.legacy_face_index",
        # B1 shape gates: leaf/instance cross-check, exact node-row shape, and
        # BLAS slice bounds — all before the first node load.
        "leaf_count != vsb_semantic_geometry_counts.static_counts_0.x",
        "vsb_semantic_geometry_counts.static_counts_1.x != (leaf_count - 1u) * 2u",
        "asset.offsets_counts.y + leaf_count",
        "asset.offsets_counts.x + (leaf_count - 1u) * 2u",
        # B1 termination budget on every tree walk.
        "stack_size > 0 && pops < node_count",
    ):
        assert token in static_select, f"static retained selection law drifted: {token}"
    static_admission = functions["vsb_retained_static_legacy_candidate_admitted"]
    static_caster = functions["vsb_retained_consider_static_caster"]
    static_blas = functions["vsb_retained_static_blas_select"]
    for token in (
        "scan_cap <= 0",
        "primitive_id >= instance.refs.w",
        "legacy_face_index = instance.refs.z + primitive_id",
        "legacy_face_index >= uint(scan_cap)",
        "int(legacy_face_index) == receiver_face_idx",
    ):
        assert token in static_admission, f"static admission law drifted: {token}"
    assert static_blas.count(
        "vsb_retained_static_legacy_candidate_admitted("
    ) == 2
    assert static_caster.index("vsb_retained_static_face_by_indices(") < (
        static_caster.index("facts.material_id == 250u")
    )
    assert static_caster.index("if (q3_any_hit) return true;") < (
        static_caster.index(
            "selected.legacy_face_index = facts.legacy_face_index"
        )
    )
    assert static_caster.index("if (q3_any_hit) return true;") < (
        static_caster.index("selected.depth = hit_depth")
    )
    assert static_blas.count("if (q3_any_hit && hit) return true;") == 2
    static_tlas = functions["vsb_retained_select_static_shadow_caster"]
    assert static_tlas.count("if (q3_any_hit && hit)") == 2
    assert (
        "FL4270_STATIC_FACE_SCAN_MAX, false"
        in functions["vsb_retained_select_point_light_caster"]
    )
    assert (
        "FL4270_STATIC_FACE_SCAN_MAX, true"
        in functions["vsb_retained_select_q3_static_shadow_caster"]
    )
    assert "vsb_retained_select_sun_shadow_caster" not in functions
    assert "scan_cap" not in static_caster
    assert "receiver_face_idx" not in static_caster
    assert "q3_static_face_cull_masks" not in product
    assert "q3_static_face_cull_masks" not in host
    assert "require_q3_admission" not in product
    assert "require_q3_admission" not in host
    dynamic_select = reachable_tokens(
        functions, "vsb_retained_select_dynamic_shadow_caster"
    )
    for token in (
        "vsb_semantic_dynamic_tlas_nodes",
        "vsb_semantic_dynamic_tlas_ids",
        "vsb_semantic_dynamic_instances",
        "density > selected.density",
        "facts.actor_effect_kind != 1u",
        # B1 gates and budget for the dynamic tree.
        "leaf_count != vsb_semantic_geometry_counts.dynamic_counts.x",
        "vsb_semantic_geometry_counts.auxiliary_counts.x != (leaf_count - 1u) * 2u",
        "stack_size > 0 && pops < node_count",
    ):
        assert token in dynamic_select, f"dynamic retained selection law drifted: {token}"
    vapor_select = reachable_tokens(
        functions, "vsb_retained_select_vapor_effect_candidates"
    )
    for token in (
        "vsb_semantic_dynamic_tlas_nodes",
        "vsb_semantic_dynamic_tlas_ids",
        "vsb_semantic_dynamic_instances",
        "facts.actor_effect_kind != 4u",
        "facts.source_id == 0u",
        "facts.material_id == 234u",
        "facts.material_id == 207u",
        "facts.material_id == 211u",
        "selected.bonfire_smoke",
        "selected.airplane_vapor",
        "depth > world_depth + 0.25",
        "floatBitsToUint(depth) == floatBitsToUint(selected.depth)",
        "vsb_retained_dynamic_identity_precedes(",
        "mover_object_id, local_face,",
        "selected.identity.object_id, selected.identity.primitive_id)",
        "leaf_count != vsb_semantic_geometry_counts.dynamic_counts.x",
        "vsb_semantic_geometry_counts.auxiliary_counts.x",
        "stack_size > 0 && pops < node_count",
    ):
        assert token in vapor_select, (
            f"composite retained vapor selection law drifted: {token}"
        )
    assert "bonfire_smoke_only" not in vapor_select
    assert "vsb_retained_select_vapor_effect_candidate(" not in product

    # Vapor is secondary paint, never a b64 primary surface. VSB must reject
    # actor-effect kind 4 before either triangle can enter opaque arbitration.
    # Product discovers both historical lanes in one retained tree walk, then
    # applies bonfire smoke after local illumination and airplane vapor after
    # glyph light without sharing winner state.
    vsb_functions = glsl_functions(vsb)
    dynamic_leaf = vsb_functions["scene_query_dynamic_leaf"]
    vapor_skip = "if (actor_effect_kind == 4u)"
    assert "uint actor_effect_kind = uint(max(row_2.w, 0.0) + 0.5);" in dynamic_leaf
    assert vapor_skip in dynamic_leaf
    assert dynamic_leaf.index(vapor_skip) < dynamic_leaf.index("intersect_triangle("), (
        "VSB vapor exclusion must precede opaque triangle arbitration"
    )
    prepare_dynamic = functions["vsb_product_prepare_dynamic"]
    assert "vsb_retained_select_vapor_effect_candidates" not in prepare_dynamic
    assert "face.actor_effect_kind == 4u" not in prepare_dynamic
    vapor_selector = functions["vsb_retained_consider_vapor_mover"]
    for token in (
        "facts.material_id == 207u",
        "facts.material_id == 211u",
        "facts.material_id == 234u",
        "facts.source_id == 0u",
        "selected.bonfire_smoke",
        "selected.airplane_vapor",
    ):
        assert token in vapor_selector, f"retained vapor admission drifted: {token}"
    assert vapor_selector.count(
        "vsb_retained_consider_vapor_candidate("
    ) == 2, "unknown kind-4 material gained a composite vapor lane"
    vapor_style = functions["vsb_product_apply_vapor_style"]
    assert "VsbRetainedDynamicSelection vapor" in vapor_style
    assert "vapor.facts" in vapor_style
    assert "input_value.retained_face" not in vapor_style
    finalize = functions["vsb_product_finalize_record"]
    composite_select = (
        "vsb_retained_select_vapor_effect_candidates(\n"
        "\t\t\tinput_value.ray, effect_depth);"
    )
    assert (
        finalize.count("vsb_retained_select_vapor_effect_candidates(") == 1
    )
    assert shipping_reachable_call_count(
        functions,
        "vsb_product_finalize_record",
        "vsb_retained_select_vapor_effect_candidates",
    ) == 1, (
        "shipping Product closure must execute exactly one retained vapor "
        "TLAS traversal per finalized subcell"
    )
    assert composite_select in finalize
    local_light_collector = (
        "vsb_product_collect_local_light_slot_facts(\n"
        "\t\t\tinput_value.ray, framebuffer_size, cell_center_px,"
    )
    assert finalize.count(
        "vsb_product_collect_local_light_slot_facts("
    ) == 1, "Product must collect the immutable local-light table once per subcell"
    assert shipping_reachable_call_count(
        functions,
        "vsb_product_finalize_record",
        "vsb_product_collect_local_light_slot_facts",
    ) == 1, (
        "shipping Product closure must execute exactly one local-light "
        "collector per finalized subcell"
    )
    assert local_light_collector in finalize
    assert (
        finalize.index("effect_depth = actor.depth;")
        < finalize.index(composite_select)
        < finalize.index(local_light_collector)
        < finalize.index("vec3 addition = local_light_facts.point_addition;")
        < finalize.index("local_light_facts.bonfire_contributed")
        < finalize.index("vapor_layers.bonfire_smoke")
        < finalize.index("uint weather_kind")
        < finalize.index("compute_local_light_glyph_cell_product(")
        < finalize.index("vapor_layers.airplane_vapor")
        < finalize.index("if (actor.attack_product.present)")
    ), "retained vapor finalizer order drifted from the current-source Product law"
    assert (
        "vsb_product_apply_vapor_style(\n"
        "\t\tvapor_layers.bonfire_smoke, record);"
    ) in finalize
    assert (
        "vsb_product_apply_vapor_style(\n"
        "\t\tvapor_layers.airplane_vapor, record);"
    ) in finalize
    assert "if (!actor.present)" not in finalize, (
        "vapor must remain depth-tested against actor paint, not disabled by it"
    )

    # Point-light visibility must preserve the deleted Product finalizer's
    # exact receiver exclusions.  A selected static face cannot shadow itself,
    # the receiving actor cannot block its own light, and other actor bodies
    # use the historical three-sphere coarse capsule.  The deleted pre-cutover
    # Product owner never treated airplanes as point-light blockers, so admitting
    # the retained airplane selector here would create a new visual owner.
    point_visibility = functions["vsb_product_local_light_visibility"]
    assert (
        "vsb_retained_select_point_light_caster(\n"
        "\t\t\tshadow_ray, receiver_face_idx);"
    ) in point_visibility
    assert "vsb_retained_select_point_light_caster(shadow_ray, -1)" not in point_visibility
    assert "vsb_retained_select_airplane_hit" not in point_visibility
    assert point_visibility.index("VsbRetainedStaticCasterSelection static_blocker") < (
        point_visibility.index("local_light_actor_blocked(")
    ), "static receiver-face exclusion must run before actor-body blocking"

    actor_blocker = functions["local_light_actor_blocked"]
    for token in (
        "if (row == receiver_actor_row) continue;",
        "float radius = max(world_size.x * 0.34, 0.28);",
        "float height = max(world_size.y, 0.8);",
        "height * 0.12",
        "height * 0.32",
        "height * 0.72",
        "radius * 0.88",
    ):
        assert token in actor_blocker, f"point-light actor blocker law drifted: {token}"
    assert actor_blocker.count("local_light_segment_hits_sphere(") == 3

    # Binding 12 has one live Product reader. It carries the lantern, beam,
    # point addition, and bonfire-contribution facts together so restoring a
    # separate point-light scan cannot hide behind unchanged paint output.
    assert "vsb_product_local_light_addition" not in functions
    local_light_facts = functions[
        "vsb_product_collect_local_light_slot_facts"
    ]
    assert "int receiver_face_idx, int receiver_actor_row" in local_light_facts
    assert (
        "receiver_face_idx, receiver_actor_row);"
    ) in local_light_facts
    assert local_light_facts.count(
        "for (int light_index = 0; "
        "light_index < LOCAL_LIGHT_MAX_RECORDS; light_index += 1)"
    ) == 1
    for token in (
        "facts.lantern_present = false;",
        "facts.beam = empty_effect_cell_product();",
        "facts.point_addition = vec3(0.0);",
        "facts.bonfire_contributed = false;",
        "facts.point_addition = addition;",
    ):
        assert token in local_light_facts, (
            f"single-pass local-light fact drifted: {token}"
        )
    assert (
        "input_value.retained_face.legacy_face_index"
    ) in finalize, "static receiver exclusion must use retained legacy-face provenance"
    assert (
        "actor.present ? actor.row : -1"
    ) in finalize, "actor receiver row must be captured before point-light visibility"
    assert (
        "effect_receiver_face_idx, effect_receiver_actor_row,\n"
        "\t\t\trecord.r0.y != OWNER_SKY);"
    ) in finalize
    assert (
        finalize.index("int effect_receiver_face_idx")
        < finalize.index("if (actor.present)")
        < finalize.index(local_light_collector)
    ), "point-light receiver identity must be captured before actor paint mutates depth"

    # B1 (FL-4431): the pop budget must guard EACH of the three tree walks
    # individually — a closure-wide token check would miss a regression that
    # unbounds only one of the loops.
    for walker in (
        "vsb_retained_static_blas_select",
        "vsb_retained_select_static_shadow_caster",
        "vsb_retained_select_dynamic_shadow_caster",
        "vsb_retained_select_vapor_effect_candidates",
    ):
        assert "stack_size > 0 && pops < node_count" in functions[walker], (
            f"{walker}: traversal lost its termination budget"
        )
        assert "while (stack_size > 0)" not in functions[walker], (
            f"{walker}: unbounded traversal loop reappeared"
        )
    # FL-4708 delete-first removes the complete tree-dependent owner, including
    # the retained canopy enumeration and falling-leaf transport.
    assert "vsb_retained_enumerate_canopy_face" not in functions
    assert "vsb_emit_retained_leaf_bank" not in functions
    assert "vsb_product_selected_canopy" not in functions
    assert "q3_probe_payload_canopy_coverage_at_cell" not in functions
    static_serializer = gd_function_body(db, "build_vsb_semantic_static_fact_bindings")
    assert "canopy_identity_bytes" not in static_serializer

    # Direct lookup fidelity: all four VSB keys remain validated, static world
    # points read the SERIALIZED forward rows (never a float32 inverse round
    # trip), and mover primitive addressing stays range-local.
    static_direct = reachable_tokens(functions, "vsb_retained_static_face_local_facts")
    dynamic_direct = reachable_tokens(functions, "vsb_retained_dynamic_face_local_facts")
    for token in ("identity.object_id", "identity.primitive_id", "identity.material", "identity.stable_world_anchor"):
        assert token in static_direct and token in dynamic_direct, f"selected identity bypassed {token}"
    world_point = functions["vsb_retained_static_world_point"]
    assert "instance.forward_basis_row_0.xyz" in world_point
    assert "instance.forward_basis_row_1.xyz" in world_point
    assert "instance.forward_basis_row_2.xyz" in world_point
    assert "inverse(" not in world_point, "forward basis must never be reconstructed by inversion"
    assert "local_point * instance.origin_scale.w" in world_point
    assert "instance.origin_scale.xyz + forward_basis * scaled_local" in world_point
    assert "mover_range.y + identity.primitive_id" in functions["vsb_retained_dynamic_face_local_facts"]

    # Pin the surviving shadow value equations. Airplane face election belongs
    # exclusively to VSB and has no Product traversal in this closure.
    consider_dynamic = functions["vsb_retained_consider_dynamic_shadow_mover"]
    for token in (
        "1.0 - altitude / 42.0",
        "length(caster_max - caster_min) * 0.36",
        "+ 0.75 + altitude * 0.05",
        "facts.actor_effect_kind != 1u",
    ):
        assert token in consider_dynamic, f"retained semantic value drifted: {token}"
    # Vapor now has one composite tree walk and a dedicated candidate helper.
    # Keep its ring split, receiver-depth slack, and stable tie law exact.
    consider_vapor = functions["vsb_retained_consider_vapor_candidate"]
    for token in (
        "depth > world_depth + 0.25",
        "facts.row_0.xyz, facts.row_1.xyz, facts.row_2.xyz",
        "facts.row_0.xyz, facts.row_2.xyz, facts.row_3.xyz",
        "floatBitsToUint(depth) == floatBitsToUint(selected.depth)",
        "mover_object_id, local_face,",
        "selected.identity.object_id, selected.identity.primitive_id)",
    ):
        assert token in consider_vapor, f"retained vapor value drifted: {token}"
    assert "facts.row_0.xyz, facts.row_1.xyz, facts.row_3.xyz" not in consider_vapor, (
        "the retired bowtie vapor split reappeared"
    )

    # Same rule, static half. A four-cornered patch of world surface can only be
    # shadow-tested as two triangles, and the cut between them has to run across
    # the single corner-0 -> corner-2 diagonal. Every static producer writes its
    # corners as a walk around the patch boundary (World.gd
    # _append_static_world_box, _append_static_world_face, and the degenerate
    # "flat quad" triangles from scripts/bake_akm_to_static_world_faces.py), and
    # the visibility stage splits those very same rows the same way. Cutting on
    # two different diagonals instead leaves one quarter of the patch belonging
    # to no triangle, so that quarter silently stops casting a shadow — measured
    # at 24.7% of a procedural tree box face before this was corrected. Pin the
    # correct split and ban the broken one inside the static caster too; pinning
    # only the dynamic selector let this exact defect live here undetected.
    consider_static = functions["vsb_retained_consider_static_caster"]
    for token in (
        "facts.row_0.xyz, facts.row_1.xyz, facts.row_2.xyz",
        "facts.row_0.xyz, facts.row_2.xyz, facts.row_3.xyz",
    ):
        assert token in consider_static, f"static caster split drifted: {token}"
    assert "facts.row_0.xyz, facts.row_1.xyz, facts.row_3.xyz" not in consider_static, (
        "the bowtie split reappeared in the static shadow caster"
    )

    # SceneGeometryDB publishes true retained trees plus typed enumeration;
    # no compositor pending flat-bank array is a source.  The 128-byte
    # instance record must serialize the authoritative FORWARD rows alongside
    # the inverse rows (B3).
    # The serializer is now THREE functions, not one. The static and dynamic
    # halves were deliberately split so that a moving object cannot force the
    # large static asset, face, and acceleration banks to be rebuilt, and so
    # that a failed allocation in one half cannot mark the other half current.
    # This check therefore searches the union of all three bodies: what matters
    # is that these facts are serialized by the retained DB somewhere, not which
    # of the three siblings happens to hold each one. Pinning them to the single
    # combined function made this contract fail on a REQUESTED architectural
    # change rather than on a defect.
    build = "\n".join(
        gd_function_body(db, name)
        for name in (
            "build_vsb_semantic_static_fact_bindings",
            "build_vsb_semantic_dynamic_fact_bindings",
            "build_vsb_semantic_fact_bindings",
        )
    )
    for token in (
        'asset["blas"]',
        '_tlas.get("node_bytes"',
        "build_dynamic_tlas()",
        "get_dynamic_mover_descriptors_pinned()",
        '"dynamic_instance_bytes"',
        "static_instances.resize(_instances.size() * 128)",
        "Vector3(basis.x.x, basis.y.x, basis.z.x)",
        "Vector3(basis.x.y, basis.y.y, basis.z.y)",
        "Vector3(basis.x.z, basis.y.z, basis.z.z)",
    ):
        assert token in build, f"retained serializer missing {token}"
    assert "flatten_instance_rows" not in build
    assert "_pending_static_world_mesh" not in build and "_pending_dynamic_mover" not in build

    # Kind ABI parity remains exact between retained DB and VSB.
    kind_pairs = re.findall(r'&"([a-z_]+)"\s*:\s*(\d+)', db.split("VSB_DYNAMIC_KIND_IDS", 1)[1].split("}", 1)[0])
    builder_pairs = re.findall(r'&"([a-z_]+)"\s*:\s*(\d+)', builder.split("DYNAMIC_KIND_IDS", 1)[1].split("}", 1)[0])
    assert kind_pairs == builder_pairs and len(kind_pairs) == 10, "dynamic kind ABI drifted"

    # Activation made retained semantic publication mandatory.  The dormant
    # feature gate must stay deleted, and the Product preparation path must
    # synchronize every retained bank before it builds the uniform set.
    assert "scene_db_vsb_semantic_facts_enabled" not in host
    sync = gd_function_body(host, "_sync_vsb_semantic_fact_buffers")
    assert "_pending_static_world_mesh" not in sync and "_pending_dynamic_mover_mesh" not in sync
    dispatch = gd_function_body(host, "_prepare_cell_owner_product_dispatch")
    assert "_sync_vsb_semantic_fact_buffers()" in dispatch
    assert host.count("_sync_vsb_semantic_fact_buffers()") == 2
    for binding in range(70, 82):
        assert f"_probe_u_storage({binding}," in host, f"host missing Product storage b{binding}"
        assert re.search(
            rf"layout\(set = 0, binding = {binding}, std430\) readonly buffer", product
        ), f"shader missing readonly Product storage b{binding}"
    # Metal supplies no trustworthy runtime-array capacity. Product binding 0
    # must be the sole count source, and each host lane must derive from the
    # uploaded bank's byte length plus its real record stride.
    assert ".length()" not in product, "Product runtime-array capacity query reappeared"
    assert "_probe_u_ubo(\n\t\t0, _vsb_semantic_geometry_counts_rd" in dispatch
    assert "binding = 0, std140) uniform VsbSemanticGeometryCounts" in product
    count_builder = gd_function_body(host, "_build_vsb_semantic_geometry_count_publication")
    for token in (
        '["static_instances", 128, 0, 0]',
        '["static_assets", 32, 0, 4]',
        '["static_rows", 16, 0, 8]',
        '["static_tlas_ids", 4, 0, 12]',
        '["static_tlas_nodes", 16, 0, 16]',
        '["static_blas_ids", 4, 0, 20]',
        '["static_blas_nodes", 16, 0, 24]',
        '["dynamic_instances", 48, 0, 32]',
        '["dynamic_rows", 16, 0, 40]',
        '["dynamic_tlas_ids", 4, 0, 44]',
        '["dynamic_tlas_nodes", 16, 0, 48]',
    ):
        assert token in count_builder, f"semantic count-table lane drifted: {token}"
    assert "VSB_SEMANTIC_INDEX_ENTRY_BYTES" in count_builder
    assert "VSB_SEMANTIC_INDEX_HEADER_BYTES" in count_builder

    asymmetric_transform_witness()
    dynamic_reorder_witness()
    tree_shape_gate_witness()
    traversal_budget_witness()


def mutation_witnesses(
    product: str, host: str, db: str, builder: str, vsb: str
) -> list[str]:
    """Require every reviewed shortcut or stable-token drift to turn red."""
    mutants: dict[str, tuple[str, str, str, ConsumerClosureMap | None]] = {
        "raw_static_bank_scan": (
            product.replace(
                "facts = vsb_retained_empty_face_facts();",
                "facts = vsb_retained_empty_face_facts();\n"
                "facts.row_0 = static_world_mesh_faces.face_rows[0];",
                1,
            ), host, db, None,
        ),
        "shipping_handoff_removed": (
            product.replace(
                "vsb_retained_select_dynamic_shadow_caster(receiver_world);",
                "vsb_retained_empty_dynamic_selection();",
                1,
            ), host, db, None,
        ),
        "shadow_provenance_resurrected": (
            product.replace(
                "struct VsbRetainedDynamicSelection {",
                "struct ShadowFact { float density; uint caster_id; vec2 caster_xz; };\n"
                "struct VsbRetainedDynamicSelection {",
                1,
            ),
            host,
            db,
            None,
        ),
        "dynamic_global_primitive": (
            product.replace("mover_range.y + identity.primitive_id", "identity.primitive_id", 1), host, db, None,
        ),
        "anchor_validation_bypass": (
            product.replace(
                "|| !vsb_retained_anchor_matches(retained_anchor, identity.stable_world_anchor)",
                "|| false",
                1,
            ), host, db, None,
        ),
        "semantic_density_value_drift": (
            product.replace(
                "float radius = clamp(length(caster_max - caster_min) * 0.36\n"
                "\t\t\t\t+ 0.75 + altitude * 0.05",
                "float radius = clamp(length(caster_max - caster_min) * 0.36\n"
                "\t\t\t\t+ 0.80 + altitude * 0.05",
                1,
            ), host, db, None,
        ),
        # FL-4431 static shadow coverage: restoring the mixed-diagonal split in
        # the static caster must go red. Cutting a four-cornered patch across
        # two different diagonals instead of one leaves a quarter of it inside
        # no triangle, so that quarter stops casting a shadow — measured at
        # 24.7% of a procedural tree box face. The `shadow_ray` argument makes
        # this replacement match the static caster only, never the dynamic
        # branches that share the same corner-argument shape.
        "static_caster_bowtie_split": (
            product.replace(
                "shadow_ray, facts.row_0.xyz, facts.row_1.xyz, facts.row_2.xyz);",
                "shadow_ray, facts.row_0.xyz, facts.row_1.xyz, facts.row_3.xyz);",
                1,
            ).replace(
                "shadow_ray, facts.row_0.xyz, facts.row_2.xyz, facts.row_3.xyz);",
                "shadow_ray, facts.row_0.xyz, facts.row_3.xyz, facts.row_2.xyz);",
                1,
            ), host, db, None,
        ),
        "q3_static_point_light_any_hit_leak": (
            product.replace(
                "FL4270_STATIC_FACE_SCAN_MAX, false",
                "FL4270_STATIC_FACE_SCAN_MAX, true",
                1,
            ),
            host,
            db,
            None,
        ),
        "q3_static_blas_early_return_removed": (
            product.replace(
                "if (q3_any_hit && hit) return true;",
                "if (q3_any_hit && hit) selected.valid = true;",
                1,
            ),
            host,
            db,
            None,
        ),
        "q3_static_provenance_payload_resurrected": (
            product.replace(
                "if (q3_any_hit) return true;",
                "selected.legacy_face_index = facts.legacy_face_index;\n"
                "\tif (q3_any_hit) return true;",
                1,
            ),
            host,
            db,
            None,
        ),
        "dead_sun_shadow_wrapper_resurrected": (
            product
            + "\nVsbRetainedStaticCasterSelection "
            "vsb_retained_select_sun_shadow_caster(\n"
            "\tViewRay shadow_ray, int receiver_face_idx, int scan_cap\n"
            ") {\n"
            "\treturn vsb_retained_select_static_shadow_caster(\n"
            "\t\tshadow_ray, receiver_face_idx, scan_cap, false);\n"
            "}\n",
            host,
            db,
            None,
        ),
        # B3: reading the inverse rows where the forward rows belong is the
        # rejected lossy shortcut and must go red.
        "inverse_rows_as_forward": (
            product.replace(
                "instance.forward_basis_row_0.xyz,\n\t\tinstance.forward_basis_row_1.xyz,\n\t\tinstance.forward_basis_row_2.xyz",
                "instance.inverse_basis_row_0.xyz,\n\t\tinstance.inverse_basis_row_1.xyz,\n\t\tinstance.inverse_basis_row_2.xyz",
                1,
            ), host, db, None,
        ),
        # B3: the DB silently dropping the forward-row serialization must go red.
        "forward_rows_not_serialized": (
            product,
            host,
            db.replace(
                "Vector3(basis.x.x, basis.y.x, basis.z.x)",
                "Vector3(inverse_basis.x.x, inverse_basis.y.x, inverse_basis.z.x)",
                1,
            ),
            None,
        ),
        # B2: flipping the stable tie direction must go red.
        "dynamic_tie_reversed": (
            product.replace(
                "return primitive_id < selected_primitive_id;",
                "return primitive_id > selected_primitive_id;",
                1,
            ), host, db, None,
        ),
        # B2: reintroducing the publication-global legacy index as the tie key
        # must go red.
        "dynamic_tie_on_legacy_face": (
            product.replace(
                "&& vsb_retained_dynamic_identity_precedes(\n"
                "\t\t\t\t\tmover.identity_range.x, local_face,\n"
                "\t\t\t\t\tselected.identity.object_id, selected.identity.primitive_id);",
                "&& legacy_face < selected.facts.legacy_face_index;",
            ), host, db, None,
        ),
        # B1: deleting the static instance-count cross-check must go red.
        "static_shape_gate_removed": (
            product.replace(
                "\t\t\t|| leaf_count != vsb_semantic_geometry_counts.static_counts_0.x) {\n\t\treturn selected;\n\t}",
                ") {\n\t\treturn selected;\n\t}",
                1,
            ), host, db, None,
        ),
        # Metal's live failure shape: one deep retained bounds check asks the
        # backend for runtime capacity again. The global ban must catch it even
        # when the surrounding tree-shape laws remain intact.
        "runtime_array_length_restored": (
            product.replace(
                "object_id >= vsb_semantic_geometry_counts.static_counts_0.x",
                "object_id >= uint(vsb_semantic_static_instances.values.length())",
                1,
            ), host, db, None,
        ),
        # B1: unbounding any traversal loop must go red.
        "traversal_budget_removed": (
            product.replace(
                "for (uint pops = 0u; stack_size > 0 && pops < node_count; ++pops) {",
                "while (stack_size > 0) {",
                1,
            ), host, db, None,
        ),
        "static_tlas_bypassed": (
            product.replace("vsb_semantic_static_tlas_ids.ids[leaf_slot]", "leaf_slot", 1), host, db, None,
        ),
        # The permanently all-ones b61 mask was physically deleted. Restoring
        # its read would create a second static admission owner.
        "static_b61_mask_resurrected": (
            product.replace(
                "\treturn true;\n}\n\n// Candidate closure shared by every retained static shadow selector.",
                "\tif (q3_static_face_cull_masks.words[64 + "
                "(legacy_face_index >> 5)] == 0u) return false;\n"
                "\treturn true;\n}\n\n// Candidate closure shared by every retained static shadow selector.",
                1,
            ),
            host,
            db,
            None,
        ),
        # The deleted require_q3_admission branch must remain absent too.
        "static_require_q3_branch_resurrected": (
            product.replace(
                "\treturn true;\n}\n\n// Candidate closure shared by every retained static shadow selector.",
                "\tif (require_q3_admission && legacy_face_index < 2048u) "
                "return false;\n"
                "\treturn true;\n}\n\n// Candidate closure shared by every retained static shadow selector.",
                1,
            ),
            host,
            db,
            None,
        ),
        # Reconnecting vapor to primary dynamic preparation revives the exact
        # mixed-ownership boundary the cutover removed.
        "vapor_primary_prepare_restored": (
            product.replace(
                "uint legacy_face = face.legacy_face_index;",
                "vsb_retained_select_vapor_effect_candidate("
                "input_value.ray, record.depth, false);\n"
                "\tuint legacy_face = face.legacy_face_index;",
                1,
            ),
            host,
            db,
            None,
        ),
        # Material 211 is the generic wounded-airplane damage trail. Changing
        # its admission id would erase that blue trail while leaving ordinary
        # contrail and bonfire smoke deceptively green.
        "vapor_damage_material_removed": (
            product.replace(
                "|| facts.material_id == 211u",
                "|| facts.material_id == 212u",
                1,
            ),
            host,
            db,
            None,
        ),
        # Both paint lanes must keep independent winner state. Pointing the
        # airplane branch at bonfire_smoke recreates the cross-material theft
        # that one composite traversal is specifically required to avoid.
        "vapor_shared_winner_state": (
            product.replace(
                "facts, retained_anchor, selected.airplane_vapor);",
                "facts, retained_anchor, selected.bonfire_smoke);",
                1,
            ),
            host,
            db,
            None,
        ),
        # Material 234 owns only bonfire smoke. Moving its branch onto 207
        # makes a contrail candidate compete with fire smoke.
        "vapor_material_partition_collapsed": (
            product.replace(
                "if (facts.material_id == 234u) {",
                "if (facts.material_id == 207u) {",
                1,
            ),
            host,
            db,
            None,
        ),
        # Unknown kind-4 materials fail closed. A third candidate call would
        # silently turn an unreviewed transport tag into visible vapor.
        "vapor_unknown_material_admitted": (
            product.replace(
                "\t\t\tcontinue;\n\t\t}\n\t}\n}\n\n"
                "// Walk the retained dynamic TLAS once",
                "\t\t\tvsb_retained_consider_vapor_candidate(\n"
                "\t\t\t\tray, world_depth, mover.identity_range.x, local_face,\n"
                "\t\t\t\tfacts, retained_anchor, selected.airplane_vapor);\n"
                "\t\t}\n\t}\n}\n\n"
                "// Walk the retained dynamic TLAS once",
                1,
            ),
            host,
            db,
            None,
        ),
        # Repeating the composite call revives the exact second TLAS walk this
        # performance cut deletes, even if both results happen to match.
        "vapor_second_traversal_restored": (
            product.replace(
                "vsb_retained_select_vapor_effect_candidates(\n"
                "\t\t\tinput_value.ray, effect_depth);",
                "vsb_retained_select_vapor_effect_candidates(\n"
                "\t\t\tinput_value.ray, effect_depth);\n"
                "\tVsbRetainedVaporSelections duplicate_vapor_layers =\n"
                "\t\tvsb_retained_select_vapor_effect_candidates(\n"
                "\t\t\tinput_value.ray, effect_depth);",
                1,
            ),
            host,
            db,
            None,
        ),
        # A direct-call count in the finalizer misses a second tree walk hidden
        # behind a reachable adapter. The shipping callgraph count must catch
        # this compile-shaped resurrection too.
        "vapor_indirect_second_traversal_restored": (
            product.replace(
                "VsbRetainedVaporSelections vapor_layers =\n"
                "\t\tvsb_retained_select_vapor_effect_candidates(\n"
                "\t\t\tinput_value.ray, effect_depth);",
                "VsbRetainedVaporSelections vapor_layers =\n"
                "\t\tvsb_retained_select_vapor_effect_candidates(\n"
                "\t\t\tinput_value.ray, effect_depth);\n"
                "\tVsbRetainedVaporSelections duplicate_vapor_layers =\n"
                "\t\tmutant_select_vapor_effect_candidates_again(\n"
                "\t\t\tinput_value.ray, effect_depth);",
                1,
            ).replace(
                "// Product finalization restores the surviving presentation stages",
                "VsbRetainedVaporSelections "
                "mutant_select_vapor_effect_candidates_again(\n"
                "\tViewRay ray, float world_depth\n"
                ") {\n"
                "\treturn vsb_retained_select_vapor_effect_candidates(\n"
                "\t\tray, world_depth);\n"
                "}\n\n"
                "// Product finalization restores the surviving presentation stages",
                1,
            ),
            host,
            db,
            None,
        ),
        # Binding 12 is immutable for one Product dispatch. Restoring a second
        # collector call is valid GLSL and preserves the visible result, but it
        # revives the deleted duplicate light-table scan.
        "local_light_second_collector_restored": (
            product.replace(
                "\tVsbProductLocalLightSlotFacts local_light_facts =\n"
                "\t\tvsb_product_collect_local_light_slot_facts(\n"
                "\t\t\tinput_value.ray, framebuffer_size, cell_center_px,\n"
                "\t\t\teffect_receiver, effect_normal, effect_depth,\n"
                "\t\t\teffect_receiver_face_idx, effect_receiver_actor_row,\n"
                "\t\t\trecord.r0.y != OWNER_SKY);",
                "\tVsbProductLocalLightSlotFacts local_light_facts =\n"
                "\t\tvsb_product_collect_local_light_slot_facts(\n"
                "\t\t\tinput_value.ray, framebuffer_size, cell_center_px,\n"
                "\t\t\teffect_receiver, effect_normal, effect_depth,\n"
                "\t\t\teffect_receiver_face_idx, effect_receiver_actor_row,\n"
                "\t\t\trecord.r0.y != OWNER_SKY);\n"
                "\tVsbProductLocalLightSlotFacts duplicate_local_light_facts =\n"
                "\t\tvsb_product_collect_local_light_slot_facts(\n"
                "\t\t\tinput_value.ray, framebuffer_size, cell_center_px,\n"
                "\t\t\teffect_receiver, effect_normal, effect_depth,\n"
                "\t\t\teffect_receiver_face_idx, effect_receiver_actor_row,\n"
                "\t\t\trecord.r0.y != OWNER_SKY);",
                1,
            ),
            host,
            db,
            None,
        ),
        # Ring-ordered billboard corners require the shared 0->2 diagonal.
        # Restoring the mixed-diagonal bowtie must execute the contract red.
        "vapor_bowtie_split_restored": (
            replace_in_source_block(
                replace_in_source_block(
                    product,
                    "void vsb_retained_consider_vapor_candidate(",
                    "// Inspect one retained mover once",
                    "ray, facts.row_0.xyz, facts.row_1.xyz, facts.row_2.xyz);",
                    "ray, facts.row_0.xyz, facts.row_1.xyz, facts.row_3.xyz);",
                ),
                "void vsb_retained_consider_vapor_candidate(",
                "// Inspect one retained mover once",
                "ray, facts.row_0.xyz, facts.row_2.xyz, facts.row_3.xyz);",
                "ray, facts.row_0.xyz, facts.row_3.xyz, facts.row_2.xyz);",
            ),
            host,
            db,
            None,
        ),
        # Bonfire smoke is a pre-weather layer in the current-source Product
        # law. Moving the complete block behind weather must go red.
        "vapor_bonfire_smoke_after_weather": (
            swap_adjacent_blocks(
                product,
                "\t// Bonfire smoke layers after local illumination and before weather.",
                "\tuint weather_kind = cell_owner_weather_kind();",
                "\tBloodFact blood = actor.present",
            ),
            host,
            db,
            None,
        ),
        # Point illumination historically lands before retained vapor paint.
        # Swapping these complete adjacent blocks keeps the shader valid while
        # changing which foreground and background values receive the light.
        "local_light_point_application_after_vapor": (
            swap_adjacent_blocks(
                product,
                "\tif (record.r0.y != OWNER_SKY) {",
                "\t// Bonfire smoke layers after local illumination and before weather.",
                "\tuint weather_kind = cell_owner_weather_kind();",
            ),
            host,
            db,
            None,
        ),
        # Airplane vapor is a post-glyph-light layer. Swapping the complete
        # adjacent blocks preserves tokens while violating paint semantics.
        "vapor_airplane_before_glyph_light": (
            swap_adjacent_blocks(
                product,
                "\tEffectCellProduct glyph_light =",
                "\t// Ordinary contrails and wounded-airplane damage trails historically layer",
                "\tif (actor.attack_product.present) {",
            ),
            host,
            db,
            None,
        ),
        # Exact-depth ties must keep stable mover/local-face ordering. Reversing
        # the comparator makes publication order visible again.
        "vapor_tie_reversed": (
            product.replace(
                "mover_object_id, local_face,\n"
                "\t\t\tselected.identity.object_id, selected.identity.primitive_id);",
                "selected.identity.object_id, selected.identity.primitive_id,\n"
                "\t\t\tmover_object_id, local_face);",
                1,
            ),
            host,
            db,
            None,
        ),
        # The historical receiver slack is exactly one quarter world unit.
        "vapor_depth_slack_drifted": (
            product.replace(
                "if (depth < 0.0 || depth > world_depth + 0.25) return;",
                "if (depth < 0.0 || depth > world_depth + 0.50) return;",
                1,
            ),
            host,
            db,
            None,
        ),
        # The historical secondary lane depth-tests against actor paint. A
        # presentation-only actor must not disable the vapor layer outright.
        "vapor_actor_gate_restored": (
            product.replace(
                "vsb_product_apply_vapor_style(\n"
                "\t\tvapor_layers.airplane_vapor, record);",
                "if (!actor.present) {\n"
                "\t\tvsb_product_apply_vapor_style(\n"
                "\t\t\tvapor_layers.airplane_vapor, record);\n"
                "\t}",
                1,
            ),
            host,
            db,
            None,
        ),
        # Passing -1 revives the self-shadow regression: the selected static
        # receiver face becomes eligible to block its own point light.
        "point_light_receiver_face_dropped": (
            product.replace(
                "vsb_retained_select_point_light_caster(\n"
                "\t\t\tshadow_ray, receiver_face_idx);",
                "vsb_retained_select_point_light_caster(\n"
                "\t\t\tshadow_ray, -1);",
                1,
            ),
            host,
            db,
            None,
        ),
        # Removing the row exclusion lets the coarse actor capsule black out
        # the very sprite currently receiving the local light.
        "point_light_receiver_actor_not_skipped": (
            product.replace(
                "if (row == receiver_actor_row) continue;",
                "if (row == -1) continue;",
                1,
            ),
            host,
            db,
            None,
        ),
        # The top sphere is the head/upper-body part of the retained current-
        # source coarse actor capsule. Losing it leaves light leaking through
        # actor heads.
        "point_light_actor_top_sphere_removed": (
            product.replace(
                "foot + vec3(0.0, height * 0.72, 0.0),",
                "foot + vec3(0.0, height * 0.32, 0.0),",
                1,
            ),
            host,
            db,
            None,
        ),
        # Airplane blocking was introduced only by the cutover.  Reconnecting
        # that selector would again hide fire and tower light behind airframes.
        "point_light_airplane_blocker_restored": (
            product.replace(
                "if (local_light_actor_blocked(",
                "VsbRetainedDynamicSelection airplane_blocker =\n"
                "\t\tvsb_retained_select_airplane_hit(shadow_ray);\n"
                "\tif (airplane_blocker.valid) return 0.0;\n"
                "\tif (local_light_actor_blocked(",
                1,
            ),
            host,
            db,
            None,
        ),
        # The VSB primitive id is asset-local.  Static self-exclusion needs the
        # retained legacy face index consumed by the static selector.
        "point_light_legacy_receiver_replaced": (
            product.replace(
                "input_value.retained_face.legacy_face_index",
                "input_value.identity.primitive_id",
                1,
            ),
            host,
            db,
            None,
        ),
    }
    reds: list[str] = []
    for name, (mutant_product, mutant_host, mutant_db, mutant_map) in mutants.items():
        try:
            validate(
                mutant_product, mutant_host, mutant_db, builder, vsb, mutant_map
            )
        except AssertionError:
            reds.append(name)
        else:
            raise AssertionError(f"mutation witness stayed green: {name}")

    # Removing the VSB skip would again let a translucent billboard replace
    # the opaque b64 receiver before Product's secondary-paint pass.
    mutant_vsb = vsb.replace(
        "if (actor_effect_kind == 4u) {",
        "if (actor_effect_kind == 4000u) {",
        1,
    )
    try:
        validate(product, host, db, builder, mutant_vsb)
    except AssertionError:
        reds.append("vapor_vsb_primary_resurrected")
    else:
        raise AssertionError(
            "mutation witness stayed green: vapor_vsb_primary_resurrected"
        )
    return reds


def main() -> int:
    """Run the contract and print one stable receipt for parent suites."""
    product = _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    host = HOST_PATH.read_text(encoding="utf-8")
    db = DB_PATH.read_text(encoding="utf-8")
    builder = BUILDER_PATH.read_text(encoding="utf-8")
    vsb = VSB_PATH.read_text(encoding="utf-8")
    validate(product, host, db, builder, vsb)
    reds = mutation_witnesses(product, host, db, builder, vsb)
    retired = sum(
        value.startswith("RETIRED:")
        for value in LEGACY_CONSUMER_DISPOSITIONS.values()
    )
    renamed = sum(
        value.startswith("RENAMED:")
        for value in LEGACY_CONSUMER_DISPOSITIONS.values()
    )
    kept = sum(
        value.startswith("KEPT:")
        for value in LEGACY_CONSUMER_DISPOSITIONS.values()
    )
    print(
        "FL4431_VSB_SEMANTIC_FACTS_CONTRACT_PASS "
        f"closure_owners={len(CONSUMER_CLOSURES)} "
        f"handoffs={sum(len(rows) for rows in CONSUMER_CLOSURES.values())} "
        f"legacy_retired={retired} legacy_renamed={renamed} legacy_kept={kept} "
        f"selection_roots=6 mutations={','.join(reds)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
