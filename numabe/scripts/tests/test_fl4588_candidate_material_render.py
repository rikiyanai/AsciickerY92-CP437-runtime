import copy
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/fl4588"))

from asciiid_counterfactual import (  # noqa: E402
    ProductionPoolError,
    evaluate,
    production_role_selection,
    value_hash,
)


def test_candidate_shape_observer_is_separate_from_live_resolver_publication():
    header = (ROOT / "engine/render/render_frame_input.h").read_text()
    resolve = (ROOT / "engine/render/render_resolve.cpp").read_text()

    assert "observe_glyph_shape6" in header
    assert "surface_id_valid" in header
    assert "const bool publish_live_bridge = !hooks.isolated_observation" in resolve
    assert "if (hooks.observe_glyph_shape6)" in resolve
    assert resolve.index("if (hooks.observe_glyph_shape6)") < resolve.index(
        "if (!hooks.resolve_glyph_shape6)"
    )
    live_publish = resolve[resolve.index("if (publish_live_bridge)") :]
    assert "WriteFl4131BridgeCellAttribution" in live_publish
    assert "WriteFl4260BridgeCellFacts" in live_publish


# ---------------------------------------------------------------------------
# RETIRED PRODUCER-SIDE TESTS (removed 2026-08-13, FL-4588 delete-first)
#
# Two tests used to open editor/asciiid.cpp, slice out the body of
# `BuildAsciiidMaterialPaintCandidateSnapshot`, and assert how that producer
# built the Material Paint candidate snapshot:
#
#   * test_candidate_material_render_uses_private_document_and_observation_hook
#     asserted the producer rendered a PRIVATE parsed candidate document through
#     `debug_hooks.observe_glyph_shape6` with `isolated_observation = true`, and
#     touched no live mutator (URDO_Open/URDO_Close/TerrainPromoteCandidateFor-
#     LiveRender/TermCaptureArtifactSnapshotC/Fl4260SelectTerrainMaterialFromEdit/
#     CommitAsciiidTerrainMaterialPaint).
#   * test_candidate_render_keeps_mixed_dispatch_rows_for_paper_classification
#     asserted the producer emitted EVERY observed cell with its `dispatch_surface`
#     and `surface_id_valid`, never filtering on
#     `dispatch_surface != GLYPH_DISPATCH_TERRAIN_MATERIAL`, so a mixed-material
#     boundary row reached the evaluator instead of being misclassified as absent.
#
# Commit 8e7c9e6b4 ("retire legacy authoring runtime surfaces") deleted that
# producer, its `Fl4588CandidatePaintStamp`, its application-service registry row
# `set_material_paint_candidate_snapshot_provider`, and the candidate-snapshot
# enrichment block inside `AsciiidApplicationService::PreviewAction`. Both tests
# therefore raised `ValueError: substring not found` before reaching a single
# assertion — they pinned a deleted owner, so they could not fail for a real
# behavioural reason any more.
#
# The producer-side invariant (do not pre-filter rows) has no live owner: there is
# no replacement producer of `candidate_material_paint` anywhere in the tree.
#
# The invariant that still matters and still has a live owner is the CLASSIFICATION
# one: a mixed-material boundary row must produce the named theory blocker rather
# than be treated as absent. That is enforced today, on both evaluator twins, by
#   scripts/tests/test_fl4588_cpp_candidate_material_consumer_parity.py
#     ::test_cpp_python_parity_names_mixed_material_boundary_theory_blocker
# and on the Python evaluator by
#   test_live_candidate_descriptor_replaces_fixture_scene_signature_shortcut
# further down this file. Neither assertion was weakened when these two were
# removed.
# ---------------------------------------------------------------------------


def test_surface_identity_interner_is_renderer_owned_for_candidate_isolation():
    identity = (ROOT / "engine/render/fl4208_surface_identity.h").read_text()
    internal = (ROOT / "engine/render/render_internal.h").read_text()
    scene = (ROOT / "engine/render/render_scene.cpp").read_text()

    assert "g_fl4208_interner" not in identity
    assert "g_fl4208_interner" not in scene
    assert "Fl4208IdentityInterner      fl4208_interner" in internal
    assert "r->fl4208_interner.BeginFrame()" in scene


def test_reflection_phase_is_renderer_owned_for_candidate_isolation():
    internal = (ROOT / "engine/render/render_internal.h").read_text()
    core = (ROOT / "engine/render/render_core.cpp").read_text()
    scene = (ROOT / "engine/render/render_scene.cpp").read_text()
    world = (ROOT / "engine/render/render_world_pass.cpp").read_text()
    sprites = (ROOT / "engine/render/render_sprite_blit.cpp").read_text()

    assert "bool reflection_mode" in internal
    assert "global_refl_mode" not in core
    assert "global_refl_mode" not in internal
    assert "global_refl_mode" not in scene
    assert "global_refl_mode" not in world
    assert "global_refl_mode" not in sprites


def test_candidate_isolation_suppresses_global_trace_publication():
    header = (ROOT / "engine/render/render_frame_input.h").read_text()
    scene = (ROOT / "engine/render/render_scene.cpp").read_text()
    resolve = (ROOT / "engine/render/render_resolve.cpp").read_text()

    assert "bool isolated_observation" in header
    assert "g_fl4208_config.trace_enabled && !isolated_observation" in scene
    assert "!fl4208_cfg_init && !isolated_observation" in scene
    trace_start = resolve.index("static void Fl4512TraceProjectedFeatureExtentV1(")
    trace_end = resolve.index("static bool Fl4512BuildProjectedFeatureBundle", trace_start)
    trace_body = resolve[trace_start:trace_end]
    assert "debug_hooks.isolated_observation" in trace_body
    assert trace_body.index("debug_hooks.isolated_observation") < trace_body.index(
        "static bool initialized"
    )


def test_paper_role_graph_scores_edge_then_falls_to_tonal_for_empty_target_roles():
    pool = json.loads(
        (ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json").read_text()
    )
    edge_row = next(
        row for row in pool["rows"]
        if row.get("profile_namespace") == "terrain"
        and row.get("material_key") == 1
        and row.get("role") == "edge"
        and row.get("lane_name") == "edge[0]"
    )
    assert all("global6_norm" not in candidate
        for candidate in edge_row["candidate_descriptors"])
    tonal_descriptor = next(
        candidate
        for row in pool["rows"]
        if row.get("profile_namespace") == "terrain"
        and row.get("material_key") == 1
        and row.get("role") == "tonal"
        and row.get("lane_name") == "ramp[3]"
        for candidate in row["candidate_descriptors"]
        if "global6_norm" in candidate
    )["global6_norm"]
    observed = {
        "selected_role": 6,
        "pool_address": {"route_key": "density:0/ramp:0"},
        "descriptor": {"global6": tonal_descriptor},
    }
    tonal_observed = production_role_selection(
        observed, pool, "terrain", 1, observed=True
    )
    assert tonal_observed["role_name"] == "tonal_fill"
    assert tonal_observed["route"] == "density:0/ramp:0"

    candidate = {
        "observed_selected_role": 2,
        "observed_pool_address": {"lane_id": 0},
        "pool_address": {"route_key": "density:3/ramp:3"},
        "descriptor": {"global6": tonal_descriptor},
    }
    tonal = production_role_selection(
        candidate, pool, "terrain", 2, observed=False
    )
    assert tonal["role_name"] == "tonal_fill"
    assert tonal["gid"] == 612


def test_paper_role_graph_stops_at_mesh_missing_and_unregistered_descriptor_roles():
    pool = json.loads(
        (ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json").read_text()
    )
    base = {
        "pool_address": {"route_key": "density:3/ramp:3"},
        "descriptor": {"global6": [0, 0, 0, 0, 0.5, 0.5]},
        "vertical_relation": 0,
        "direction": -1,
        "flow": -1,
    }
    with pytest.raises(
        ProductionPoolError,
        match="mesh_feature claim is classified missing",
    ):
        production_role_selection(
            {**base, "fact_mask": (1 << 4) | 1},
            pool,
            "terrain",
            1,
            observed=False,
        )
    with pytest.raises(
        ProductionPoolError,
        match="mesh_feature descriptor prediction is not registered",
    ):
        production_role_selection(
            {**base, "fact_mask": (1 << 3) | 1},
            pool,
            "terrain",
            1,
            observed=False,
        )
    with pytest.raises(
        ProductionPoolError,
        match="filled descriptor prediction is not registered",
    ):
        production_role_selection(
            {**base, "fact_mask": 1 << 5},
            pool,
            "terrain",
            1,
            observed=False,
        )


def test_terrain_surface_identity_uses_queryterrain_world_cell_origin_once():
    world = (ROOT / "engine/render/render_world_pass.cpp").read_text()
    assert "pp->owner_x = fl4208_patch_x + fu" in world
    assert "pp->owner_y = fl4208_patch_y + fv" in world
    assert "fl4208_patch_x * VISUAL_CELLS" not in world
    assert "fl4208_patch_y * VISUAL_CELLS" not in world


def test_terrain_material_paint_has_exactly_one_brush_admission_owner():
    """Single-owner brush admission for the terrain Material Paint commit stamp.

    This test used to be named
    `test_commit_and_candidate_share_one_brush_admission_function` and asserted
    two things: (a) `AsciiidTerrainMaterialPaintCellAdmitted` is defined exactly
    once, and (b) BOTH the commit stamp `TerrainMaterialPaintStamp` and the
    candidate stamp `Fl4588CandidatePaintStamp` call it, so a candidate preview
    could never admit a different cell set than the commit.

    Commit 8e7c9e6b4 deleted `Fl4588CandidatePaintStamp` with the rest of the
    isolated candidate render, so half (b)'s candidate side no longer exists and
    the test raised `ValueError: substring not found`. Half (a) plus the commit
    stamp's use of the shared admission function still have a live owner, so they
    are preserved here verbatim rather than dropped. Nothing was weakened: the
    only removed assertion is the one whose subject was deleted.
    """
    source = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text()
    assert source.count("static bool AsciiidTerrainMaterialPaintCellAdmitted(") == 1
    # The retired candidate stamp must not come back as a second admission owner.
    assert "struct Fl4588CandidatePaintStamp" not in source

    commit_start = source.index("struct TerrainMaterialPaintStamp")
    commit_end = source.index("// Elevation (MAT-elev) stamp only", commit_start)
    assert "AsciiidTerrainMaterialPaintCellAdmitted(" in source[
        commit_start:commit_end
    ]


def test_live_candidate_descriptor_replaces_fixture_scene_signature_shortcut():
    case = json.loads(
        (
            ROOT
            / "assets/asciiid/counterfactual/terrain_material_brush_v1.golden.json"
        ).read_text()
    )
    snapshot = copy.deepcopy(case["snapshot"])
    action = copy.deepcopy(case["action"])
    model = snapshot["document"]["model"]
    model.pop("material_scene_signatures")

    production_cells = []
    candidate_cells = []
    for index, fixture_cell in enumerate(snapshot["renderer"]["model"]["cells"]):
        terrain_cell = model["terrain_cells"][index]
        source_id = f"cell:{terrain_cell['world_x']}:{terrain_cell['world_y']}"
        terrain_cell["terrain_cell_id"] = source_id
        coord = copy.deepcopy(fixture_cell["cell"])
        production_cells.append(
            {
                "cell": coord,
                "profile_namespace": 1,
                "material_id": 4,
                "dispatch_surface": 1,
                "resolve_elev": 0,
                "resolve_shade": 2,
                "eligible": 1,
                "winner_gid": 627,
                "selected_role": 6,
                "presented_gid": 627,
                "pool_address": {
                    "profile_namespace": 1,
                    "material_key": 4,
                    "route_key": 6,
                    "lane_id": 0,
                    "secondary_lane_id": 1,
                },
                "result_kind": 1,
                "absence_reason": 0,
                "descriptor_id": "fl4260.global6.v1",
                "pool_revision": 18,
                "temporal": {"decision": "keep_current"},
                "ramp": 0,
                "density": 1,
                "vertical_relation": -1,
                "direction": -1,
                "flow": -1,
                "fact_mask": 0,
                "surface_id": {
                    "world_generation": 9,
                    "owner_kind": 0,
                    "owner_x": terrain_cell["world_x"],
                    "owner_y": terrain_cell["world_y"],
                    "anchor_u_q": 0,
                    "anchor_v_q": 0,
                },
                "shape6_global": [0, 0, 0, 0, 0.5, 0.5],
                "terrain_source_cell_ids": [source_id],
                "screen": {"fg": 102, "bg": 16, "final_gid": 627},
            }
        )
        candidate_cells.append(
            {
                "cell": coord,
                "material_id": 5 if index == 0 else 4,
                "resolve_elev": 0,
                "resolve_shade": 2,
                "dispatch_surface": 1,
                "terrain_source_cell_id": source_id,
                "shape6_global": [0, 0, 0, 0, 0.5, 0.5],
            }
        )

    snapshot["document"]["sha256"] = value_hash(model)
    snapshot["renderer"]["production_bridge_cells"] = production_cells
    after_hash = case["expected_action_impact"]["expected_artifact_mutations"][0][
        "after_sha256"
    ]
    snapshot["candidate_material_paint"] = {
        "schema": "asciiid.material_paint_candidate.v1",
        "base_snapshot_sha256": snapshot["snapshot_sha256"],
        "candidate_document_sha256": after_hash,
        "changed_terrain_cell_count": 1,
        "cells": candidate_cells,
    }

    impact = evaluate(snapshot, action)
    assert impact["theory_status"] == "defined"
    assert impact["commit_armed"] is True
    assert impact["exact_visible_delta"] == [{"x": 0, "y": 0}]
    assert impact["per_cell"][0]["expected_after"]["material_owner"] == "terrain:5"

    mixed_snapshot = copy.deepcopy(snapshot)
    mixed_snapshot["candidate_material_paint"]["cells"][0]["dispatch_surface"] = 2
    mixed = evaluate(mixed_snapshot, action)
    assert mixed["theory_status"] == "theory_mechanism_unspecified"
    assert mixed["commit_armed"] is False
    assert (
            "mixed-material boundary dispatch is unspecified"
            in mixed["theory_failure_reason"]
    )
