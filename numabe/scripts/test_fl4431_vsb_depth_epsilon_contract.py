#!/usr/bin/env python3
# =============================================================================
# test_fl4431_vsb_depth_epsilon_contract.py — the VSB depth-tie rule
# =============================================================================
# Run from the repository root:
#   python3 scripts/test_fl4431_vsb_depth_epsilon_contract.py
#
# THE REVIEWED LAW (an FL-4431 activation precondition)
# -----------------------------------------------------
# The VisibleSurfaceBuilder resolves near-equal candidate depths inside the
# band max(request, 1e-6) by a deterministic identity chain (owner kind,
# object id, primitive id). A publication may not WIDEN that band: the host
# rejects any request whose FLOAT32 serialization exceeds the shader's own
# 1e-6 floor, so the effective band is always exactly one reviewed number.
#
# Why the band cannot be larger (review round 1, B1): the legacy published
# depth bias is CONTINUOUS — admitted alpha begins just above 0.045, so
# deliberate legacy separations start near 0.045 * 0.00035 = 1.575e-5 —
# and retained geometry has no validated minimum spacing at all. Only the
# floor itself is provably merge-safe against surviving deliberate
# separations (1e-6 < 1.575e-5). Deliberate legacy separations BELOW the
# floor (splat hash nudge <= 2.55e-7, relative EYE/PREVIOUS preferences)
# belong to owners the activation patch deletes; their reviewed replacement
# is the identity chain itself (recorded disposition, Q4 scope).
#
# All fixture arithmetic below is executed at FLOAT32 granularity: every
# operand and every intermediate is rounded through struct-packed float32,
# and the 1-ulp perturbation bumps the packed uint32 representation, so the
# demonstrated behaviors are the behaviors of 32-bit GPU lanes.
# =============================================================================

from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHADER = ROOT / "godot_project" / "render" / "backends" / "ortho" / "visibility" / "visible_surface_builder.glsl"
HOST = ROOT / "godot_project" / "render" / "core" / "contracts" / "visibility" / "VisibleSurfaceABI.gd"

MIN_LEGACY_DELIBERATE_SEPARATION = 0.045 * 0.00035  # ~1.575e-5

FAILURES: list[str] = []


def check(ok: bool, label: str) -> None:
    if not ok:
        FAILURES.append(label)


# ---------------------------------------------------------------------------
# Exact float32 helpers
# ---------------------------------------------------------------------------

def f32(x: float) -> float:
    """Round a Python double to the nearest float32 value."""
    return struct.unpack("f", struct.pack("f", x))[0]


def f32_ulp_up(x: float) -> float:
    """EXACTLY one float32 ulp above x, via the packed uint32 bits (a
    double-precision nextafter step rounds back to the same float32 — the
    review round-1 defect this replaces)."""
    bits = struct.unpack("I", struct.pack("f", x))[0]
    return struct.unpack("f", struct.pack("I", bits + 1))[0]


def f32_sub(a: float, b: float) -> float:
    return f32(f32(a) - f32(b))


def vsb_precedes(depth_a, ident_a, depth_b, ident_b, request) -> bool:
    """The shader comparator executed at float32 granularity:
    strict-nearer outside the band, identity chain inside it."""
    eps = max(f32(request), f32(1.0e-6))
    if f32(depth_a) < f32_sub(depth_b, eps):
        return True
    if f32(abs(f32_sub(depth_a, depth_b))) > eps:
        return False
    return ident_a < ident_b


def legacy_precedes(depth_a, depth_b) -> bool:
    """The legacy effective ordering: strict float32 depth comparison."""
    return f32(depth_a) < f32(depth_b)


# ---------------------------------------------------------------------------
# R1 — merge safety against every SURVIVING deliberate legacy separation
# ---------------------------------------------------------------------------

def fixture_r1_no_merge_of_surviving_separations() -> None:
    base = f32(12.345678)
    # The smallest surviving deliberate separation (continuous alpha bias
    # at the admission threshold). The floor band must NOT merge it.
    nearer = f32_sub(base, MIN_LEGACY_DELIBERATE_SEPARATION)
    for request in (0.0, 1.0e-7, 1.0e-6):
        legacy = legacy_precedes(nearer, base)
        # Give the nearer surface the LOSING identity so a wrong merge
        # (identity deciding) would flip the outcome visibly.
        vsb = vsb_precedes(nearer, (2, 9, 9), base, (1, 0, 0), request)
        check(legacy and vsb,
              f"R1: the floor band must preserve the minimum surviving legacy separation at request={request}")
    # Demonstrate WHY wider bands were rejected by review: a request of
    # 2e-5 would swallow that same separation and let identity flip it.
    merged = vsb_precedes(nearer, (2, 9, 9), base, (1, 0, 0), 2.0e-5)
    check(not merged,
          "R1: a 2e-5 band lets identity override a deliberate legacy separation — exactly the class the host cap forbids")


# ---------------------------------------------------------------------------
# R2 — in-band determinism where the legacy strict `<` is float-noise
# ---------------------------------------------------------------------------

def fixture_r2_band_determinism() -> None:
    a = f32(3.0)
    b = f32_ulp_up(a)  # exactly one float32 ulp apart: inside the 1e-6 band
    check(a != b and (b - a) < 1.0e-6,
          "R2 precondition: the pair must be one real f32 ulp apart, inside the band")
    # Legacy: the strict comparison genuinely flips when the nearer operand
    # is perturbed by one f32 ulp — the FMA-contraction hazard class.
    check(legacy_precedes(a, b) is True,
          "R2: legacy orders the unperturbed pair by the ulp")
    check(legacy_precedes(f32_ulp_up(a), b) is False,
          "R2: one f32-ulp perturbation flips the legacy outcome (float-noise class)")
    # VSB: identical identities and the same perturbation, outcome constant.
    unperturbed = vsb_precedes(a, (1, 5, 3), b, (2, 0, 0), 0.0)
    perturbed = vsb_precedes(f32_ulp_up(a), (1, 5, 3), b, (2, 0, 0), 0.0)
    check(unperturbed is True and perturbed is True,
          "R2: the identity chain is stable under the same 1-ulp perturbation")


# ---------------------------------------------------------------------------
# R3 — exact float32 boundary semantics
# ---------------------------------------------------------------------------

def fixture_r3_boundary() -> None:
    eps = f32(1.0e-6)
    a = f32(5.0)
    # Construct b so the f32 subtraction lands EXACTLY on the band edge is
    # not generally representable; instead pin both sides of the edge with
    # real f32 values and assert the comparator's > semantics.
    inside = f32(a + eps * 0.5)      # |delta| < eps -> tie path
    outside = f32(a + eps * 4.0)     # |delta| > eps -> strict depth
    check(vsb_precedes(inside, (0, 0, 0), a, (1, 1, 1), 0.0) is True,
          "R3: inside the band the identity chain decides")
    check(vsb_precedes(outside, (0, 0, 0), a, (1, 1, 1), 0.0) is False,
          "R3: outside the band the farther candidate loses on depth")
    # EXACT equality is representable with a zero base: depth_a = f32(1e-6),
    # depth_b = 0.0 makes abs(delta) == epsilon with no rounding anywhere,
    # so the comparator's strict `> epsilon` rejection is False and the
    # identity chain must decide (the reviewed boundary semantics, executed).
    eq_a = f32(1.0e-6)
    check(vsb_precedes(eq_a, (0, 0, 0), 0.0, (1, 1, 1), 0.0) is True,
          "R3: abs(delta) == epsilon exactly must enter identity arbitration (winning identity)")
    check(vsb_precedes(eq_a, (2, 0, 0), 0.0, (1, 1, 1), 0.0) is False,
          "R3: abs(delta) == epsilon exactly must enter identity arbitration (losing identity)")
    # The serialization boundary: the largest f32 not above f32(1e-6) is
    # accepted; one f32 ulp above it must be rejected by the host law.
    floor32 = f32(1.0e-6)
    above = f32_ulp_up(floor32)
    check(f32(floor32) <= f32(1.0e-6) < above,
          "R3: the f32 floor and its ulp-successor bracket the host accept/reject edge")


# ---------------------------------------------------------------------------
# Function-scoped source pins + red-first mutants
# ---------------------------------------------------------------------------

def _shader_comparator_body(shader: str) -> str:
    start = shader.find("bool candidate_precedes(")
    end = shader.find("\n}", start)
    return shader[start:end] if start >= 0 else ""


def _host_push_body(host: str) -> str:
    start = host.find("static func build_push_constant(")
    end = host.find("\nstatic func ", start + 1)
    if end < 0:
        end = host.find("\nfunc ", start + 1)
    return host[start:end] if start >= 0 else ""


def _pin_failures(shader: str, host: str) -> list[str]:
    """All pins as a pure function so the mutants below can exercise them
    against modified sources (red-first falsifiability)."""
    out: list[str] = []
    body = _shader_comparator_body(shader)
    if not body:
        return ["shader must define candidate_precedes"]
    if "float epsilon = max(push_data.ray_parameters.w, 1.0e-6);" not in body:
        out.append("shader pin: the band must be max(requested, 1e-6)")
    if "if (depth < best.depth - epsilon) {" not in body:
        out.append("shader pin: the strict-nearer branch must precede the band test")
    if "if (abs(depth - best.depth) > epsilon) {" not in body:
        out.append("shader pin: the band boundary must use strict > rejection")
    # ORDER pin: the strict-nearer branch must textually precede the band
    # rejection — swapping them would invert valid nearer candidates
    # outside the band while every presence pin stayed green.
    nearer_at = body.find("if (depth < best.depth - epsilon) {")
    band_at = body.find("if (abs(depth - best.depth) > epsilon) {")
    if not (0 <= nearer_at < band_at):
        out.append("shader pin: the strict-nearer branch must precede the band rejection")
    # The lexicographic chain needs BOTH the inequality guards and the `<`
    # results, in owner -> object -> primitive order.
    for clause in ("owner != best.owner_kind", "object_id != best.object_id",
                   "owner < best.owner_kind", "object_id < best.object_id",
                   "primitive_id < best.primitive_id"):
        if clause not in body:
            out.append(f"shader pin: identity chain clause missing: {clause}")
    if not (body.find("owner < best.owner_kind")
            < body.find("object_id < best.object_id")
            < body.find("primitive_id < best.primitive_id")):
        out.append("shader pin: identity clauses must order owner -> object -> primitive")
    push = _host_push_body(host)
    if not push:
        return out + ["host must define build_push_constant"]
    cap_at = push.find("serialized_epsilon_probe[0] > serialized_epsilon_probe[1]")
    if cap_at < 0:
        out.append("host pin: the f32-serialized band cap must be inside build_push_constant")
    else:
        after = push[cap_at:cap_at + 200]
        if "return PackedByteArray()" not in after:
            out.append("host pin: the cap violation must return the empty publication")
    if "PackedFloat32Array([ray_epsilon, DEPTH_TIE_EPSILON_FLOOR])" not in push:
        out.append("host pin: both cap operands must round-trip through float32")
    if "DEPTH_TIE_EPSILON_FLOOR := 1.0e-6" not in host:
        out.append("host pin: the floor constant must be the shader's 1e-6")
    return out


def source_pins_and_mutants() -> None:
    shader = SHADER.read_text(encoding="utf-8")
    host = HOST.read_text(encoding="utf-8")
    for failure in _pin_failures(shader, host):
        check(False, failure)
    # Red-first mutants: each modified source must trip at least one pin.
    mutants = [
        ("nearer/band branch order swapped", shader.replace(
            "if (depth < best.depth - epsilon) {\n        return true;\n    }\n    if (abs(depth - best.depth) > epsilon) {",
            "if (abs(depth - best.depth) > epsilon) {\n        return false;\n    }\n    if (depth < best.depth - epsilon) {"), host),
        ("owner guard removed", shader.replace("owner != best.owner_kind", "false"), host),
        ("boundary >= swap", shader.replace(
            "if (abs(depth - best.depth) > epsilon) {",
            "if (abs(depth - best.depth) >= epsilon) {"), host),
        ("primitive tail removed", shader.replace(
            "primitive_id < best.primitive_id", "true"), host),
        ("band floor changed", shader.replace(
            "max(push_data.ray_parameters.w, 1.0e-6)",
            "max(push_data.ray_parameters.w, 1.0e-4)"), host),
        ("host cap removed", shader, host.replace(
            "serialized_epsilon_probe[0] > serialized_epsilon_probe[1]",
            "false")),
        ("host cap widened", shader, host.replace(
            "DEPTH_TIE_EPSILON_FLOOR := 1.0e-6",
            "DEPTH_TIE_EPSILON_FLOOR := 1.0e-4")),
    ]
    for name, mut_shader, mut_host in mutants:
        if not _pin_failures(mut_shader, mut_host):
            check(False, f"mutant not detected: {name}")


def main() -> int:
    fixture_r1_no_merge_of_surviving_separations()
    fixture_r2_band_determinism()
    fixture_r3_boundary()
    source_pins_and_mutants()
    if FAILURES:
        print("FL4431_VSB_DEPTH_EPSILON_CONTRACT_FAIL failures=%d" % len(FAILURES))
        for f in FAILURES:
            print("  - %s" % f)
        return 1
    print("FL4431_VSB_DEPTH_EPSILON_CONTRACT_PASS rules=R1,R2,R3 band=floor_1e-6_only "
          "min_surviving_separation=1.575e-5 mutants=7 arithmetic=float32_exact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
