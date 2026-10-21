#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_resolver_loads_material_morphology_profile_table() -> None:
    runtime = (REPO_ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text(encoding="utf-8")

    assert "material.morphology.v2.profile_tables.json" in runtime
    assert "Fl4131RuntimeLoadMorphologyProfiles" in runtime
    assert "Fl4131RuntimeGlyphAllowedByMorphologyProfile" in runtime


def test_runtime_honors_sidecar_morphology_profile_override() -> None:
    """Step 9 contract: the runtime reads `morphology_profile_name` out of the
    harri sidecar and applies it as the per-material profile name before the
    candidate-pool review gate runs. Load order: morphology table first
    (seeds defaults), then sidecar (applies override). Without the override the
    default profile name per material wins; with the override the sidecar wins.
    """
    runtime = (REPO_ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text(encoding="utf-8")

    assert "morphology_profile_name" in runtime
    assert "g_fl4131_runtime_material_profile_name_storage" in runtime
    # Load order: morphology table first, sidecar second.
    morph_idx = runtime.index("Fl4131RuntimeLoadMorphologyProfiles();")
    sidecar_idx = runtime.index("Fl4131RuntimeLoadProfiles();")
    assert morph_idx < sidecar_idx, (
        "Fl4131RuntimeLoadMorphologyProfiles must run before Fl4131RuntimeLoadProfiles "
        "so the sidecar override is the last writer to "
        "g_fl4131_runtime_material_profile_names[]."
    )
