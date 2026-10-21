#!/usr/bin/env python3
"""FL-4431 Q3 retained-static first-admitted-hit destructive contract.

Q3 consumes one boolean static-shadow density bit. It does not consume caster
identity, selected depth, reconstructed face rows, and provenance. The selected
cut threads a Q3-only early-return mode through the existing candidate, BLAS,
and TLAS owner. The point-light caller retains the full smallest-legacy
selection because its downstream code still reads the selected payload. The
former sun wrapper is dead and must remain physically absent.

The executable model compares the deleted exhaustive Q3 boolean with the early
first-admitted boolean. The source contract pins the production traversal's
selected mode. This file provides structural and arithmetic evidence only; it
makes no runtime, visual, timing, acceptance, and closure claim.
"""

from __future__ import annotations
if __package__ in (None, ""):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.tests.test_fl4691_paired_payload_contract import _expand_includes


from dataclasses import dataclass
from itertools import permutations
import math
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PRODUCT_PATH = ROOT / "godot_project" / "render" / "product" / "cell_owner_product.glsl"
SCAN_CAP = 2048
STACK_CAP = 64


@dataclass(frozen=True)
class Candidate:
    """One materialized static face and its two triangle-test results."""

    legacy_face: int
    material: int
    t_0: float
    t_1: float
    primitive_valid: bool = True


@dataclass(frozen=True)
class TreeNode:
    """Two child words: nonnegative node index, negative encoded leaf index."""

    left: int
    right: int


def _bounded_triangle_result(value: float, ray_length: float) -> float:
    """Mirror product_ray_triangle_t's final finite depth gate.

    Comparisons with NaN are false in both GLSL and Python. A NaN therefore
    reaches the existing pair predicate unchanged; the model must not invent a
    finite-value policy that production does not have.
    """
    if value < 0.0 or value > ray_length:
        return -1.0
    return value


def _candidate_is_admitted(
    candidate: Candidate,
    receiver_face: int,
    scan_cap: int,
    ray_length: float,
) -> bool:
    """Apply the exact cap, self, material, and negative-pair laws."""
    if scan_cap <= 0 or not candidate.primitive_valid:
        return False
    if candidate.legacy_face >= scan_cap:
        return False
    if candidate.legacy_face == receiver_face:
        return False
    if candidate.material == 250:
        return False
    t_0 = _bounded_triangle_result(candidate.t_0, ray_length)
    t_1 = _bounded_triangle_result(candidate.t_1, ray_length)
    if t_0 < 0.0 and t_1 < 0.0:
        return False
    return True


def _old_q3_exhaustive_boolean(
    candidates: tuple[Candidate, ...],
    receiver_face: int,
    scan_cap: int = SCAN_CAP,
    ray_length: float = 1000.0,
) -> bool:
    """Evaluate every candidate like the current full-selection traversal."""
    selected = False
    for candidate in candidates:
        if _candidate_is_admitted(
            candidate,
            receiver_face,
            scan_cap,
            ray_length,
        ):
            selected = True
    return selected


def _new_q3_first_admitted_boolean(
    candidates: tuple[Candidate, ...],
    receiver_face: int,
    scan_cap: int = SCAN_CAP,
    ray_length: float = 1000.0,
) -> bool:
    """Return on the first candidate admitted by the unchanged predicate."""
    for candidate in candidates:
        if _candidate_is_admitted(
            candidate,
            receiver_face,
            scan_cap,
            ray_length,
        ):
            return True
    return False


def _selected_hit_depth(candidate: Candidate, ray_length: float) -> float:
    """Mirror the retained selector's existing two-triangle depth expression."""
    t_0 = _bounded_triangle_result(candidate.t_0, ray_length)
    t_1 = _bounded_triangle_result(candidate.t_1, ray_length)
    if t_0 < 0.0:
        return t_1
    if t_1 < 0.0:
        return t_0
    return min(t_0, t_1)


def _point_light_full_selection(
    candidates: tuple[Candidate, ...],
    receiver_face: int,
    scan_cap: int = SCAN_CAP,
    ray_length: float = 1000.0,
) -> tuple[int, float] | None:
    """Preserve smallest-legacy selection and that winner's computed depth."""
    admitted = [
        candidate
        for candidate in candidates
        if _candidate_is_admitted(
            candidate,
            receiver_face,
            scan_cap,
            ray_length,
        )
    ]
    if not admitted:
        return None
    selected = min(admitted, key=lambda candidate: candidate.legacy_face)
    return selected.legacy_face, _selected_hit_depth(selected, ray_length)


def _bounded_tree_candidate_order(
    nodes: tuple[TreeNode, ...],
    leaf_candidates: tuple[int, ...],
) -> tuple[int, ...]:
    """Walk malformed trees with the shader's node-pop and stack bounds."""
    if not nodes:
        return ()
    stack = [0]
    popped = 0
    result: list[int] = []
    while stack and popped < len(nodes):
        node_index = stack.pop()
        popped += 1
        if node_index < 0 or node_index >= len(nodes):
            continue
        node = nodes[node_index]
        for child in (node.left, node.right):
            if child < 0:
                leaf_slot = -child - 1
                if 0 <= leaf_slot < len(leaf_candidates):
                    result.append(leaf_candidates[leaf_slot])
            elif child < len(nodes) and len(stack) < STACK_CAP:
                stack.append(child)
    return tuple(result)


def _without_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def _compact(source: str) -> str:
    return re.sub(r"\s+", "", _without_comments(source))


def _function_text(source: str, name: str) -> str:
    """Return one GLSL function definition by balanced brace depth."""
    clean = _without_comments(source)
    match = re.search(
        rf"(?m)^\s*(?:precise\s+)?[A-Za-z_]\w*\s+"
        rf"{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{",
        clean,
        re.S,
    )
    if match is None:
        return ""
    brace = clean.find("{", match.start())
    depth = 0
    for index in range(brace, len(clean)):
        if clean[index] == "{":
            depth += 1
        elif clean[index] == "}":
            depth -= 1
            if depth == 0:
                return clean[match.start() : index + 1]
    return ""


def _ordered(body: str, tokens: tuple[str, ...]) -> bool:
    cursor = -1
    for token in tokens:
        cursor = body.find(token, cursor + 1)
        if cursor < 0:
            return False
    return True


def _replace_nth(source: str, old: str, new: str, occurrence: int) -> str:
    """Replace one zero-based occurrence and prove the mutation executed."""
    cursor = -1
    for _ in range(occurrence + 1):
        cursor = source.find(old, cursor + 1)
        assert cursor >= 0
    return source[:cursor] + new + source[cursor + len(old) :]


def _q3_any_hit_source_errors(source: str) -> tuple[str, ...]:
    """Return deviations from the selected Q3-only early-return cut."""
    errors: list[str] = []
    clean = _without_comments(source)
    whole = _compact(clean)
    ray_triangle = _compact(_function_text(clean, "product_ray_triangle_t"))
    admission = _compact(
        _function_text(clean, "vsb_retained_static_legacy_candidate_admitted")
    )
    candidate = _compact(
        _function_text(clean, "vsb_retained_consider_static_caster")
    )
    blas = _compact(_function_text(clean, "vsb_retained_static_blas_select"))
    traversal = _compact(
        _function_text(clean, "vsb_retained_select_static_shadow_caster")
    )
    point = _compact(
        _function_text(clean, "vsb_retained_select_point_light_caster")
    )
    sun = _compact(
        _function_text(clean, "vsb_retained_select_sun_shadow_caster")
    )
    q3 = _compact(
        _function_text(clean, "vsb_retained_select_q3_static_shadow_caster")
    )

    if "if(t<0.0||t>ray.len)return-1.0;" not in ray_triangle:
        errors.append("triangle helper lost the ray-length rejection")
    if (
        "scan_cap<=0" not in admission
        or "primitive_id>=instance.refs.w" not in admission
        or "legacy_face_index>=uint(scan_cap)" not in admission
    ):
        errors.append("candidate cap admission drifted")
    if "int(legacy_face_index)==receiver_face_idx" not in admission:
        errors.append("candidate receiver-self rejection drifted")
    if (
        "selected.valid" not in admission
        or "legacy_face_index>=selected.legacy_face_index" not in admission
    ):
        errors.append("full selector lost smallest-legacy admission")

    if not candidate:
        errors.append("static candidate owner is missing")
    else:
        if (
            "vsb_retained_static_face_by_indices("
            "object_id,primitive_id,facts,retained_anchor,false)"
            not in candidate
        ):
            errors.append("static shadow candidate reconstructs unused anchor")
        candidate_order = (
            "boolq3_any_hit",
            "if(facts.material_id==250u)returnfalse;",
            "if(t_0<0.0&&t_1<0.0)returnfalse;",
            "if(q3_any_hit)returntrue;",
            "selected.valid=true;",
            "selected.legacy_face_index=facts.legacy_face_index;",
            "selected.depth=hit_depth;",
            "returntrue;",
        )
        if not _ordered(candidate, candidate_order):
            errors.append("candidate Q3 early-return admission/order drifted")
        if "isnan(" in candidate or "isinf(" in candidate:
            errors.append("candidate added a non-legacy finite-value gate")

    if not blas:
        errors.append("static BLAS owner is missing")
    else:
        if "boolq3_any_hit" not in blas:
            errors.append("BLAS does not accept Q3 any-hit mode")
        if (
            blas.count("vsb_retained_consider_static_caster(") != 2
            or blas.count("q3_any_hit,selected") != 2
        ):
            errors.append("BLAS does not forward Q3 mode to candidates")
        if blas.count("if(q3_any_hit&&hit)returntrue;") != 2:
            errors.append("BLAS does not propagate a Q3 candidate hit")
        if "pops<node_count" not in blas:
            errors.append("BLAS lost its malformed-tree pop bound")
        if "stack_size<VSB_RETAINED_TREE_STACK" not in blas:
            errors.append("BLAS lost its malformed-tree stack bound")

    if not traversal:
        errors.append("static TLAS owner is missing")
    else:
        if "boolq3_any_hit" not in traversal:
            errors.append("TLAS does not accept Q3 any-hit mode")
        if traversal.count("vsb_retained_static_blas_select(") != 2:
            errors.append("TLAS no longer owns BLAS dispatch")
        if traversal.count("q3_any_hit,selected") != 2:
            errors.append("TLAS does not forward Q3 mode to BLAS")
        if traversal.count("if(q3_any_hit&&hit)") != 2 or not _ordered(
            traversal,
            (
                "vsb_retained_static_blas_select(",
                "if(q3_any_hit&&hit){",
                "selected.valid=true;",
                "returnselected;",
            ),
        ):
            errors.append("TLAS does not propagate a Q3 BLAS hit")
        if "pops<node_count" not in traversal:
            errors.append("TLAS lost its malformed-tree pop bound")
        if "stack_size<VSB_RETAINED_TREE_STACK" not in traversal:
            errors.append("TLAS lost its malformed-tree stack bound")

    point_call = (
        "returnvsb_retained_select_static_shadow_caster("
        "shadow_ray,receiver_face_idx,FL4270_STATIC_FACE_SCAN_MAX,false);"
    )
    if point_call not in point:
        errors.append("point-light wrapper leaked into Q3 any-hit mode")
    if sun:
        errors.append("dead sun-shadow wrapper resurrected")
    q3_call = (
        "returnvsb_retained_select_static_shadow_caster("
        "shadow_ray,receiver_face_idx,FL4270_STATIC_FACE_SCAN_MAX,true);"
    )
    if q3_call not in q3:
        errors.append("Q3 wrapper does not select first-admitted mode")

    definition_count = len(
        re.findall(
            r"(?m)^\s*VsbRetainedStaticCasterSelection\s+"
            r"vsb_retained_select_static_shadow_caster\s*\(",
            clean,
        )
    )
    if definition_count != 1:
        errors.append("retained static traversal must have one TLAS owner")

    for forbidden in ("shared", "barrier(", "cache", "prepass"):
        if forbidden in whole:
            errors.append(
                "Q3 any-hit traversal resurrected forbidden %s coordination"
                % forbidden.rstrip("(")
            )

    q3_return = candidate.find("if(q3_any_hit)returntrue;")
    first_payload = min(
        (
            index
            for index in (
                candidate.find("selected.legacy_face_index="),
                candidate.find("selected.depth="),
            )
            if index >= 0
        ),
        default=-1,
    )
    if q3_return < 0 or first_payload < 0 or first_payload < q3_return:
        errors.append("Q3 path resurrected selected provenance payload")

    if "Q3StaticFaceCullMasks" in whole or "q3_static_face_cull_masks" in whole:
        errors.append("retired Q3 mask owner resurrected")
    return tuple(errors)


SELECTED_SOURCE = r"""
float product_ray_triangle_t(ViewRay ray, vec3 a, vec3 b, vec3 c) {
    float t = 1.0;
    if (t < 0.0 || t > ray.len) return -1.0;
    return t;
}

bool vsb_retained_static_legacy_candidate_admitted(
    VsbSemanticStaticInstance instance, uint primitive_id,
    int receiver_face_idx, int scan_cap,
    VsbRetainedStaticCasterSelection selected
) {
    if (scan_cap <= 0 || primitive_id >= instance.refs.w) return false;
    uint legacy_face_index = instance.refs.z + primitive_id;
    if (legacy_face_index >= uint(scan_cap)
            || int(legacy_face_index) == receiver_face_idx) return false;
    if (selected.valid
            && legacy_face_index >= selected.legacy_face_index) return false;
    return true;
}

bool vsb_retained_consider_static_caster(
    ViewRay shadow_ray, uint object_id, uint primitive_id, bool q3_any_hit,
    inout VsbRetainedStaticCasterSelection selected
) {
    VsbRetainedFaceFacts facts;
    vec4 retained_anchor;
    if (!vsb_retained_static_face_by_indices(
            object_id, primitive_id, facts, retained_anchor, false)) return false;
    if (facts.material_id == 250u) return false;
    float t_0 = product_ray_triangle_t(
        shadow_ray, facts.row_0.xyz, facts.row_1.xyz, facts.row_2.xyz);
    float t_1 = product_ray_triangle_t(
        shadow_ray, facts.row_0.xyz, facts.row_2.xyz, facts.row_3.xyz);
    if (t_0 < 0.0 && t_1 < 0.0) return false;
    float hit_depth = t_0 < 0.0 ? t_1 : (t_1 < 0.0 ? t_0 : min(t_0, t_1));
    if (q3_any_hit) return true;
    selected.valid = true;
    selected.legacy_face_index = facts.legacy_face_index;
    selected.depth = hit_depth;
    return true;
}

bool vsb_retained_static_blas_select(
    ViewRay shadow_ray, uint object_id, int receiver_face_idx, int scan_cap,
    bool q3_any_hit, inout VsbRetainedStaticCasterSelection selected
) {
    uint node_count = 2u;
    int stack_size = 1;
    uint primitive_id = 0u;
    if (vsb_retained_static_legacy_candidate_admitted(
            instance, primitive_id, receiver_face_idx, scan_cap, selected)) {
        bool hit = vsb_retained_consider_static_caster(
            shadow_ray, object_id, primitive_id, q3_any_hit, selected);
        if (q3_any_hit && hit) return true;
    }
    for (uint pops = 0u; stack_size > 0 && pops < node_count; ++pops) {
        primitive_id = 1u;
        if (vsb_retained_static_legacy_candidate_admitted(
                instance, primitive_id, receiver_face_idx, scan_cap, selected)) {
            bool hit = vsb_retained_consider_static_caster(
                shadow_ray, object_id, primitive_id, q3_any_hit, selected);
            if (q3_any_hit && hit) return true;
        }
        if (stack_size < VSB_RETAINED_TREE_STACK) stack_size += 1;
        stack_size -= 1;
    }
    return false;
}

VsbRetainedStaticCasterSelection vsb_retained_select_static_shadow_caster(
    ViewRay shadow_ray, int receiver_face_idx, int scan_cap, bool q3_any_hit
) {
    VsbRetainedStaticCasterSelection selected =
        vsb_retained_empty_static_selection();
    bool hit = vsb_retained_static_blas_select(
            shadow_ray, 0u, receiver_face_idx, scan_cap,
            q3_any_hit, selected);
    if (q3_any_hit && hit) {
        selected.valid = true;
        return selected;
    }
    uint node_count = 2u;
    int stack_size = 1;
    for (uint pops = 0u; stack_size > 0 && pops < node_count; ++pops) {
        hit = vsb_retained_static_blas_select(
                shadow_ray, 1u, receiver_face_idx, scan_cap,
                q3_any_hit, selected);
        if (q3_any_hit && hit) {
            selected.valid = true;
            return selected;
        }
        if (stack_size < VSB_RETAINED_TREE_STACK) stack_size += 1;
        stack_size -= 1;
    }
    return selected;
}

VsbRetainedStaticCasterSelection vsb_retained_select_point_light_caster(
    ViewRay shadow_ray, int receiver_face_idx
) {
    return vsb_retained_select_static_shadow_caster(
        shadow_ray, receiver_face_idx, FL4270_STATIC_FACE_SCAN_MAX, false);
}

VsbRetainedStaticCasterSelection vsb_retained_select_q3_static_shadow_caster(
    ViewRay shadow_ray, int receiver_face_idx
) {
    return vsb_retained_select_static_shadow_caster(
        shadow_ray, receiver_face_idx, FL4270_STATIC_FACE_SCAN_MAX, true);
}
"""


def test_q3_first_admitted_model_matches_exhaustive_boolean() -> None:
    """Compare both booleans across permutations and every admission edge."""
    nan = float("nan")
    candidates = (
        Candidate(5, 1, 4.0, -1.0),
        Candidate(7, 250, 2.0, -1.0),
        Candidate(9, 1, -1.0, -1.0),
        Candidate(11, 1, 1001.0, 1002.0),
        Candidate(13, 1, nan, -1.0),
        Candidate(2048, 1, 3.0, -1.0),
        Candidate(15, 1, 6.0, -1.0, primitive_valid=False),
    )
    for candidate_order in permutations(candidates):
        ordered_candidates = tuple(candidate_order)
        old = _old_q3_exhaustive_boolean(
            ordered_candidates,
            receiver_face=5,
        )
        new = _new_q3_first_admitted_boolean(
            ordered_candidates,
            receiver_face=5,
        )
        assert new == old
        assert new

    rejected = (
        Candidate(3, 1, -1.0, -1.0),
        Candidate(4, 250, 1.0, -1.0),
        Candidate(5, 1, 1.0, -1.0),
        Candidate(6, 1, 1001.0, 1002.0),
        Candidate(2048, 1, 1.0, -1.0),
        Candidate(8, 1, 1.0, -1.0, primitive_valid=False),
    )
    for candidate_order in permutations(rejected):
        ordered_candidates = tuple(candidate_order)
        assert not _old_q3_exhaustive_boolean(
            ordered_candidates,
            receiver_face=5,
        )
        assert not _new_q3_first_admitted_boolean(
            ordered_candidates,
            receiver_face=5,
        )

    assert _old_q3_exhaustive_boolean(
        (Candidate(1, 1, nan, nan),),
        receiver_face=-1,
    )
    assert _new_q3_first_admitted_boolean(
        (Candidate(1, 1, nan, nan),),
        receiver_face=-1,
    )
    assert not _old_q3_exhaustive_boolean(
        (Candidate(1, 1, 1.0, -1.0),),
        receiver_face=-1,
        scan_cap=0,
    )


def test_malformed_bounded_tree_preserves_q3_boolean() -> None:
    """Cycles, invalid children, and truncated leaf ids terminate identically."""
    candidates = (
        Candidate(2, 1, -1.0, -1.0),
        Candidate(4, 1, 3.0, -1.0),
    )
    malformed_trees = (
        (TreeNode(0, 99),),
        (TreeNode(1, -1), TreeNode(0, -2)),
        (TreeNode(-9, 0),),
        (),
    )
    leaf_maps = (
        (0, 1),
        (0, 1),
        (0,),
        (),
    )
    for nodes, leaf_map in zip(malformed_trees, leaf_maps):
        order = _bounded_tree_candidate_order(nodes, leaf_map)
        visited = tuple(
            candidates[index]
            for index in order
            if 0 <= index < len(candidates)
        )
        assert _new_q3_first_admitted_boolean(
            visited,
            receiver_face=-1,
        ) == _old_q3_exhaustive_boolean(
            visited,
            receiver_face=-1,
        )
        assert len(order) <= max(len(nodes) * 2, 0)


def test_point_light_model_retains_smallest_legacy_payload() -> None:
    """Point light keeps full provenance and depth, never nearest-hit Q3 law."""
    candidates = (
        Candidate(19, 1, 2.0, -1.0),
        Candidate(3, 1, 22.0, -1.0),
        Candidate(11, 1, 1.0, -1.0),
    )
    for candidate_order in permutations(candidates):
        assert _point_light_full_selection(
            tuple(candidate_order),
            receiver_face=-1,
        ) == (3, 22.0)


def test_shipping_source_requires_q3_only_first_admitted_cut() -> None:
    """Remain red until production threads the selected mode end to end."""
    errors = _q3_any_hit_source_errors(
        _expand_includes(PRODUCT_PATH.read_text(encoding="utf-8"))
    )
    assert not errors, "RED: " + "; ".join(errors)


def test_q3_any_hit_contract_rejects_each_destructive_mutant() -> None:
    """Execute every gate and ownership mutation against the selected fixture."""
    assert not _q3_any_hit_source_errors(SELECTED_SOURCE)
    mutations = (
        (
            "cap removed",
            SELECTED_SOURCE.replace(
                "legacy_face_index >= uint(scan_cap)",
                "legacy_face_index < 0u",
                1,
            ),
            "candidate cap admission drifted",
        ),
        (
            "nonpositive cap admitted",
            SELECTED_SOURCE.replace(
                "scan_cap <= 0 || primitive_id >= instance.refs.w",
                "primitive_id >= instance.refs.w",
                1,
            ),
            "candidate cap admission drifted",
        ),
        (
            "invalid local primitive admitted",
            SELECTED_SOURCE.replace(
                "scan_cap <= 0 || primitive_id >= instance.refs.w",
                "scan_cap <= 0",
                1,
            ),
            "candidate cap admission drifted",
        ),
        (
            "receiver self removed",
            SELECTED_SOURCE.replace(
                "int(legacy_face_index) == receiver_face_idx",
                "int(legacy_face_index) < -1",
                1,
            ),
            "candidate receiver-self rejection drifted",
        ),
        (
            "material 250 admitted",
            SELECTED_SOURCE.replace(
                "if (facts.material_id == 250u) return false;",
                "",
                1,
            ),
            "candidate Q3 early-return admission/order drifted",
        ),
        (
            "negative pair changed to either-negative",
            SELECTED_SOURCE.replace(
                "if (t_0 < 0.0 && t_1 < 0.0) return false;",
                "if (t_0 < 0.0 || t_1 < 0.0) return false;",
                1,
            ),
            "candidate Q3 early-return admission/order drifted",
        ),
        (
            "ray-length bound removed",
            SELECTED_SOURCE.replace(
                "if (t < 0.0 || t > ray.len) return -1.0;",
                "if (t < 0.0) return -1.0;",
                1,
            ),
            "triangle helper lost the ray-length rejection",
        ),
        (
            "BLAS pop budget widened",
            _replace_nth(
                SELECTED_SOURCE,
                "pops < node_count",
                "pops <= node_count",
                0,
            ),
            "BLAS lost its malformed-tree pop bound",
        ),
        (
            "BLAS stack bound removed",
            _replace_nth(
                SELECTED_SOURCE,
                "stack_size < VSB_RETAINED_TREE_STACK",
                "stack_size >= 0",
                0,
            ),
            "BLAS lost its malformed-tree stack bound",
        ),
        (
            "TLAS pop budget widened",
            _replace_nth(
                SELECTED_SOURCE,
                "pops < node_count",
                "pops <= node_count",
                1,
            ),
            "TLAS lost its malformed-tree pop bound",
        ),
        (
            "TLAS stack bound removed",
            _replace_nth(
                SELECTED_SOURCE,
                "stack_size < VSB_RETAINED_TREE_STACK",
                "stack_size >= 0",
                1,
            ),
            "TLAS lost its malformed-tree stack bound",
        ),
        (
            "NaN-specific rejection added",
            SELECTED_SOURCE.replace(
                "if (t_0 < 0.0 && t_1 < 0.0) return false;",
                "if (isnan(t_0) || isnan(t_1)) return false;\n"
                "    if (t_0 < 0.0 && t_1 < 0.0) return false;",
                1,
            ),
            "candidate added a non-legacy finite-value gate",
        ),
        (
            "candidate early return removed",
            SELECTED_SOURCE.replace(
                "if (q3_any_hit) return true;",
                "if (q3_any_hit) selected.valid = true;",
                1,
            ),
            "candidate Q3 early-return admission/order drifted",
        ),
        (
            "shadow candidate reconstructs unused anchor",
            SELECTED_SOURCE.replace(
                "object_id, primitive_id, facts, retained_anchor, false)",
                "object_id, primitive_id, facts, retained_anchor, true)",
                1,
            ),
            "static shadow candidate reconstructs unused anchor",
        ),
        (
            "BLAS first early propagation removed",
            _replace_nth(
                SELECTED_SOURCE,
                "if (q3_any_hit && hit) return true;",
                "if (q3_any_hit && hit) selected.valid = true;",
                0,
            ),
            "BLAS does not propagate a Q3 candidate hit",
        ),
        (
            "BLAS second early propagation removed",
            _replace_nth(
                SELECTED_SOURCE,
                "if (q3_any_hit && hit) return true;",
                "if (q3_any_hit && hit) selected.valid = true;",
                1,
            ),
            "BLAS does not propagate a Q3 candidate hit",
        ),
        (
            "TLAS first early propagation removed",
            _replace_nth(
                SELECTED_SOURCE,
                "if (q3_any_hit && hit) {",
                "if (false && hit) {",
                0,
            ),
            "TLAS does not propagate a Q3 BLAS hit",
        ),
        (
            "TLAS second early propagation removed",
            _replace_nth(
                SELECTED_SOURCE,
                "if (q3_any_hit && hit) {",
                "if (false && hit) {",
                1,
            ),
            "TLAS does not propagate a Q3 BLAS hit",
        ),
        (
            "point light enters any-hit mode",
            SELECTED_SOURCE.replace(
                "receiver_face_idx, FL4270_STATIC_FACE_SCAN_MAX, false);",
                "receiver_face_idx, FL4270_STATIC_FACE_SCAN_MAX, true);",
                1,
            ),
            "point-light wrapper leaked into Q3 any-hit mode",
        ),
        (
            "dead sun wrapper resurrected",
            SELECTED_SOURCE
            + """
VsbRetainedStaticCasterSelection vsb_retained_select_sun_shadow_caster(
    ViewRay shadow_ray, int receiver_face_idx, int scan_cap
) {
    return vsb_retained_select_static_shadow_caster(
        shadow_ray, receiver_face_idx, scan_cap, false);
}
""",
            "dead sun-shadow wrapper resurrected",
        ),
        (
            "Q3 leaves any-hit mode",
            SELECTED_SOURCE.replace(
                "receiver_face_idx, FL4270_STATIC_FACE_SCAN_MAX, true);",
                "receiver_face_idx, FL4270_STATIC_FACE_SCAN_MAX, false);",
                1,
            ),
            "Q3 wrapper does not select first-admitted mode",
        ),
        (
            "point payload depth removed",
            SELECTED_SOURCE.replace(
                "selected.depth = hit_depth;",
                "",
                1,
            ),
            "candidate Q3 early-return admission/order drifted",
        ),
        (
            "Q3 provenance payload resurrected",
            SELECTED_SOURCE.replace(
                "if (q3_any_hit) return true;",
                "selected.legacy_face_index = facts.legacy_face_index;\n"
                "    if (q3_any_hit) return true;",
                1,
            ),
            "Q3 path resurrected selected provenance payload",
        ),
        (
            "shared cache coordination added",
            SELECTED_SOURCE.replace(
                "bool vsb_retained_static_blas_select(",
                "shared uint q3_shadow_cache;\n"
                "bool vsb_retained_static_blas_select(",
                1,
            ),
            "Q3 any-hit traversal resurrected forbidden shared coordination",
        ),
        (
            "barrier coordination added",
            SELECTED_SOURCE.replace(
                "uint node_count = 2u;",
                "barrier();\n    uint node_count = 2u;",
                1,
            ),
            "Q3 any-hit traversal resurrected forbidden barrier coordination",
        ),
        (
            "cache coordination added",
            SELECTED_SOURCE.replace(
                "bool q3_any_hit, inout VsbRetainedStaticCasterSelection selected",
                "bool q3_any_hit, bool cache_enabled, "
                "inout VsbRetainedStaticCasterSelection selected",
                1,
            ),
            "Q3 any-hit traversal resurrected forbidden cache coordination",
        ),
        (
            "prepass owner added",
            SELECTED_SOURCE.replace(
                "bool q3_any_hit, inout VsbRetainedStaticCasterSelection selected",
                "bool q3_any_hit, bool prepass, "
                "inout VsbRetainedStaticCasterSelection selected",
                1,
            ),
            "Q3 any-hit traversal resurrected forbidden prepass coordination",
        ),
        (
            "second TLAS owner added",
            SELECTED_SOURCE
            + "\n"
            + _function_text(
                SELECTED_SOURCE,
                "vsb_retained_select_static_shadow_caster",
            ),
            "retained static traversal must have one TLAS owner",
        ),
    )
    for label, mutant, expected in mutations:
        errors = _q3_any_hit_source_errors(mutant)
        assert expected in errors, "%s escaped: %s" % (label, errors)


def main() -> int:
    test_q3_first_admitted_model_matches_exhaustive_boolean()
    test_malformed_bounded_tree_preserves_q3_boolean()
    test_point_light_model_retains_smallest_legacy_payload()
    test_shipping_source_requires_q3_only_first_admitted_cut()
    test_q3_any_hit_contract_rejects_each_destructive_mutant()
    print("FL4431_Q3_STATIC_SHADOW_ANY_HIT_CONTRACT_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
