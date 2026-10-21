#!/usr/bin/env python3
"""Guard the single-owner Active Stroke v2 compiler/runtime boundary.

This source-contract suite prevents the retired v1 coefficients, the retired
silhouette probe hash, and platform floating-point Gaussian generation from
returning as parallel authorities.  It also verifies that the tracked artifact
keeps every aspect asset and aspect row during the v2 pool rebuild.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPILER = (
    ROOT / "scripts/compile_fl4512_pools.py"
).read_text(encoding="utf-8")
RESOLVER_HEADER = (
    ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.h"
).read_text(encoding="utf-8")
RESOLVER_SOURCE = (
    ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp"
).read_text(encoding="utf-8")
PROFILE = json.loads(
    (
        ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json"
    ).read_text(encoding="utf-8")
)
SCHEMA = json.loads(
    (
        ROOT
        / "assets/glyphs/schema/material_rendering_profiles.v1.schema.json"
    ).read_text(encoding="utf-8")
)
ARTIFACT = json.loads(
    (
        ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json"
    ).read_text(encoding="utf-8")
)

RETIRED_V1_FIELDS = {
    "endpoint_projected_tolerance_q16_16",
    "endpoint_object_tolerance_q24_8",
    "depth_link_max_height_q8_8",
    "degree2_tangent_dot_min_q1_15",
    "smoothing_kernel_q0_8",
    "smoothing_iterations",
    "persistence_projected_max_q16_16",
    "persistence_object_max_q24_8",
    "arc_landmark_count",
    "retire_after_missing_frames",
    "max_children_per_track",
}


def test_live_compiler_and_resolver_have_no_v1_topology_owner():
    """The v1 schema, parser, struct, lookup, and scalar names stay deleted."""

    live_source = "\n".join(
        (COMPILER, RESOLVER_HEADER, RESOLVER_SOURCE)
    )
    assert "fl4512.active_stroke_topology.v1" not in live_source
    assert "Fl4512ActiveStrokeTopologyAdmissionV1" not in live_source
    assert "Fl4512PhaseSAdmissionLookupV1" not in live_source
    assert "Fl4512PhaseSAdmissionForProfileV1" not in live_source
    for field in RETIRED_V1_FIELDS:
        assert field not in live_source


def test_retired_silhouette_probe_is_not_compiled_loaded_nor_registered():
    """The deleted probe block cannot affect publication identity or Phase S."""

    live_source = "\n".join(
        (COMPILER, RESOLVER_HEADER, RESOLVER_SOURCE)
    )
    for token in (
        "silhouette_visibility_admission",
        "silhouette_visibility_admission_sha256",
        "Fl4512SilhouetteVisibilityAdmissionV1",
    ):
        assert token not in live_source
    assert "silhouette_visibility_admission" not in SCHEMA["definitions"]
    assert all(
        "silhouette_visibility_admission" not in profile
        for profile in PROFILE["profiles"]
    )
    assert all(
        "silhouette_visibility_admission" not in material
        for material in ARTIFACT["materials"]
    )


def test_compiler_owns_decimal_gaussian_expansion_and_runtime_has_no_libm_copy():
    """Only the compiler evaluates the exponential used by the 145-row table."""

    assert "from decimal import Decimal, ROUND_HALF_UP, localcontext" in COMPILER
    assert "(-Decimal(index) / denominator).exp()" in COMPILER
    assert "math.exp" not in COMPILER
    assert "exp(" not in RESOLVER_SOURCE
    assert "gaussian_table_q0_16" in RESOLVER_SOURCE
    assert "Fl4512JsonValueHashMatches" in RESOLVER_SOURCE


def test_tracked_pool_keeps_canonical_aspect_authoring_rows():
    """The v2 rebuild cannot silently drop the already-admitted aspect data."""

    assert ARTIFACT["header"]["quantization_version"] == 3
    assert ARTIFACT["header"]["aspect_golden_fixture_sha256"] is not None
    assert len(ARTIFACT["aspect_assets"]) == 22
    assert len(ARTIFACT["aspect_rows"]) == 22
    assert {
        asset["geometry_path"] for asset in ARTIFACT["aspect_assets"]
    } >= {
        "assets/meshes/pieta_shaded.akm",
        "assets/meshes/pieta.akm",
        "assets/meshes/bush-1.akm",
        "assets/meshes/old-tree-1.akm",
        "assets/meshes/old-tree-2.akm",
        "assets/meshes/sac_and_math_color.akm",
        "assets/meshes/torus.akm",
        "assets/meshes/tree-1.akm",
        "assets/meshes/tree-2.akm",
        "assets/meshes/tree-3.akm",
    }
