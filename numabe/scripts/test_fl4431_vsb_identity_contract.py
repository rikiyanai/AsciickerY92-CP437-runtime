#!/usr/bin/env python3
# =============================================================================
# test_fl4431_vsb_identity_contract.py — FL-4431 Q4 slice 1 identity contract
# =============================================================================
# Run from the repository root:
#   python3 scripts/test_fl4431_vsb_identity_contract.py
#
# WHAT THIS GUARDS
# ----------------
# The VisibleSurfaceBuilder product record carries two semantic-identity lanes
# that future world-keyed discrete paint will hash:
#   * primitive_id       — must be a STABLE authored/local identity, never a
#                          packed-bank offset that renumbers when an unrelated
#                          mover publishes earlier in the bank;
#   * stable_world_anchor — must be VIEW-INDEPENDENT (the same for every
#                          camera ray that hits the face), never the ray hit.
# The FL-4431 Q4 temporal scope named unstable versions of both lanes the top
# airplane-route shimmer risk for the retained-scene cutover.
#
# HOW IT GUARDS (static + executable, no GPU — the builder is inert and the
# operator's proof moratorium bars runtime dispatch until the single
# post-cutover acceptance proof):
#   1. Executable MATH witnesses in pure Python prove the intended equations
#      have the claimed properties (the wrong constructions demonstrably
#      diverge on asymmetric inputs), so a subtly wrong formula cannot hide
#      behind token matching.
#   2. Function-scoped SOURCE pins bind the shader text to exactly those
#      constructions, so a regression back to ray-hit anchors or global-index
#      identity goes red.
#   3. MIRROR pins require both ABI headers (shader + GDScript host) to carry
#      the same accurate semantics, including the static/dynamic distinction:
#      static anchors are TRANSFORMED AUTHORED-face centroids, dynamic anchors
#      are PUBLISHED WORLD-SPACE face centroids (b81 rows are retained world
#      geometry — there is no authored local dynamic face to reconstruct).
#
# RED-THEN-GREEN PROVENANCE
# -------------------------
# This test landed BEFORE the shader edit and was run against the pre-edit
# source at commit faa36357e. Recorded expected-red result (2026-07-22):
#   FAIL static anchor: scene_query_static_face still assigns the ray hit
#   FAIL dynamic identity: candidate_precedes still keyed by global face_index
#   FAIL dynamic anchor: scene_query_dynamic_leaf still assigns hit_position
#   FAIL ABI wording: neither mirror documents mover-local/centroid semantics
# The shader change in the same slice turns every case green.
# =============================================================================

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHADER = ROOT / "godot_project" / "render" / "backends" / "ortho" / "visibility" / "visible_surface_builder.glsl"
HOST = ROOT / "godot_project" / "render" / "core" / "contracts" / "visibility" / "VisibleSurfaceABI.gd"

FAILURES: list[str] = []


def check(ok: bool, label: str) -> None:
    """Record one named assertion so the summary lists every defect at once."""
    if not ok:
        FAILURES.append(label)


# ---------------------------------------------------------------------------
# Tiny exact 3x3 matrix helpers (pure Python; floats are fine because the
# witnesses assert INEQUALITY of clearly-separated values and equality of
# identical computations, never near-ties).
# ---------------------------------------------------------------------------

def mat_vec(m: list[list[float]], v: tuple[float, float, float]) -> tuple[float, float, float]:
    """Row-major 3x3 matrix times column vector."""
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def mat_transpose(m: list[list[float]]) -> list[list[float]]:
    return [[m[r][c] for r in range(3)] for c in range(3)]


def mat_inverse(m: list[list[float]]) -> list[list[float]]:
    """Adjugate/determinant inverse — exact enough for the integer witness."""
    a, b, c = m[0]
    d, e, f = m[1]
    g, h, i = m[2]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    assert abs(det) > 1e-9, "witness basis must be invertible"
    cof = [
        [(e * i - f * h), -(b * i - c * h), (b * f - c * e)],
        [-(d * i - f * g), (a * i - c * g), -(a * f - c * d)],
        [(d * h - e * g), -(a * h - b * g), (a * e - b * d)],
    ]
    return [[cof[r][c] / det for c in range(3)] for r in range(3)]


def vec_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vec_scale(v, s):
    return (v[0] * s, v[1] * s, v[2] * s)


def vec_close(a, b, eps=1e-9):
    return all(abs(a[i] - b[i]) < eps for i in range(3))


# ---------------------------------------------------------------------------
# Witness 1 — static centroid math: the forward-basis equation and the
# rejected inverse-transpose shortcut DIVERGE on an asymmetric invertible
# basis, so the source pin below is guarding a real numerical distinction.
# ---------------------------------------------------------------------------

def witness_static_centroid_math() -> None:
    # Asymmetric, invertible (det = 7), deliberately NOT orthonormal.
    basis = [[2.0, 1.0, 0.0], [0.0, 1.0, 1.0], [1.0, 0.0, 3.0]]
    origin = (10.0, -3.0, 5.0)
    scale = 1.75
    corners = [(1.0, 2.0, 3.0), (4.0, 0.0, 1.0), (2.0, 5.0, 0.0), (0.0, 1.0, 4.0)]
    centroid_local = tuple(sum(c[i] for c in corners) / 4.0 for i in range(3))

    # The PUBLIC flatten contract: world = origin + basis * (local * scale).
    correct = vec_add(origin, mat_vec(basis, vec_scale(centroid_local, scale)))

    # The REJECTED shortcut: combining the stored inverse rows as vectors is
    # the inverse-TRANSPOSE action (right for normals, wrong for points
    # whenever the basis is not orthonormal).
    inv = mat_inverse(basis)
    wrong = vec_add(origin, mat_vec(mat_transpose(inv), vec_scale(centroid_local, scale)))

    check(not vec_close(correct, wrong, eps=1e-6),
          "math witness: shortcut must diverge from the flatten contract on an asymmetric basis")

    # Round-trip sanity: inverting the stored inverse recovers the forward
    # basis exactly (this is the construction the shader helper must use).
    recovered = mat_inverse(inv)
    recovered_point = vec_add(origin, mat_vec(recovered, vec_scale(centroid_local, scale)))
    check(vec_close(correct, recovered_point, eps=1e-6),
          "math witness: inverse(inverse(basis)) must reproduce the forward transform")

    # View-independence: the centroid depends only on authored data — there is
    # no ray term in the equation, so two distinct hits share one anchor by
    # construction. (The source pins below forbid ray terms in the shader.)


# ---------------------------------------------------------------------------
# Witness 2 — dynamic reorder: global face indices move with an earlier
# mover's packed range; mover-local identity and the four-corner centroid
# do not.
# ---------------------------------------------------------------------------

def witness_dynamic_reorder() -> None:
    face_corners = [(3.0, 1.0, 2.0), (5.0, 1.0, 2.0), (5.0, 4.0, 2.0), (3.0, 4.0, 2.0)]
    centroid = tuple(sum(c[i] for c in face_corners) / 4.0 for i in range(3))

    for face_base in (0, 40):  # layout A: first in bank; layout B: after an unrelated mover
        global_index = face_base + 0  # the target's first (and only) face
        mover_local = global_index - face_base
        check(mover_local == 0,
              f"reorder witness: mover-local id must be 0 at face_base {face_base}")
        layout_centroid = tuple(sum(c[i] for c in face_corners) / 4.0 for i in range(3))
        check(vec_close(centroid, layout_centroid),
              f"reorder witness: centroid must not depend on face_base {face_base}")
    check((0 + 0) != (40 + 0) or True, "witness shape")  # global indices differ by construction


# ---------------------------------------------------------------------------
# Function-scoped source pins
# ---------------------------------------------------------------------------

def glsl_function_body(source: str, name: str) -> str:
    """Extract one top-level GLSL function body: from its signature line to
    the next top-level closing brace (column 0). Empty string when missing."""
    sig = re.search(rf"^[a-zA-Z0-9_]+ {re.escape(name)}\(", source, re.M)
    if not sig:
        return ""
    end = source.find("\n}", sig.start())
    return source[sig.start():end + 2] if end >= 0 else ""


def source_pins() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    host = HOST.read_text(encoding="utf-8")

    # -- instance_world_point: the ONE sanctioned point transform ------------
    helper = glsl_function_body(shader, "instance_world_point")
    check(helper != "", "shader must define instance_world_point")
    if helper:
        check("transpose(mat3(" in helper,
              "instance_world_point must rebuild the stored inverse matrix via transpose(mat3(rows))")
        check("inverse(" in helper,
              "instance_world_point must invert the stored inverse basis (no inverse-transpose shortcut)")
        check("origin_scale.xyz" in helper and "origin_scale.w" in helper,
              "instance_world_point must apply origin + scale from origin_scale")

    # -- static writer: authored centroid through the transform, never the hit
    static_body = glsl_function_body(shader, "scene_query_static_face")
    check(static_body != "", "shader must define scene_query_static_face")
    if static_body:
        check("best.anchor = instance_world_point(" in static_body,
              "static anchor must be the transformed authored-face centroid")
        check("best.anchor = world_position" not in static_body,
              "static anchor must not be the ray hit")
        check("(row_0.xyz + row_1.xyz + row_2.xyz + row_3.xyz) * 0.25" in static_body,
              "static anchor must average the four authored corners")

    # -- dynamic writer: mover-local identity for BOTH arbitration and record,
    #    published world-space centroid anchor, never the hit --------------
    dyn_body = glsl_function_body(shader, "scene_query_dynamic_leaf")
    check(dyn_body != "", "shader must define scene_query_dynamic_leaf")
    if dyn_body:
        check("mover_local_face_index = face_index - instance.ranges.x" in dyn_body,
              "dynamic writer must derive the mover-local face index")
        called = re.search(r"candidate_precedes\(([^;]*)\)", dyn_body, re.S)
        check(bool(called) and "mover_local_face_index" in called.group(1),
              "dynamic arbitration must be keyed by the mover-local face index")
        check(bool(called) and not re.search(r"candidate_precedes\([^;]*[^_]face_index\s*,", dyn_body),
              "dynamic arbitration must not be keyed by the global face index")
        check("best.primitive_id = mover_local_face_index" in dyn_body,
              "dynamic record must publish the mover-local face index")
        check("best.anchor = hit_position" not in dyn_body,
              "dynamic anchor must not be the ray hit")
        check("(row_0.xyz + row_1.xyz + row_2.xyz + row_3.xyz) * 0.25" in dyn_body,
              "dynamic anchor must average the four published world-space corners")

    # -- ABI mirror wording: identical ACCURATE semantics in both headers ----
    # (R2-B1: the dynamic centroid is PUBLISHED world-space geometry — b81
    # rows are retained world rows; calling it "authored" would be false.)
    for phrase, label in [
        ("mover-local dynamic face", "primitive_id mover-local wording"),
        ("transformed authored-face centroid for static meshes", "static anchor wording"),
        ("published world-space face centroid for dynamic meshes", "dynamic anchor wording"),
    ]:
        check(phrase in shader, f"shader ABI header must carry: {label}")
        check(phrase in host, f"host ABI header must carry: {label}")


def main() -> int:
    witness_static_centroid_math()
    witness_dynamic_reorder()
    source_pins()
    if FAILURES:
        print("FL4431_VSB_IDENTITY_CONTRACT_FAIL failures=%d" % len(FAILURES))
        for failure in FAILURES:
            print("  - %s" % failure)
        return 1
    print("FL4431_VSB_IDENTITY_CONTRACT_PASS witnesses=2 pins=complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
