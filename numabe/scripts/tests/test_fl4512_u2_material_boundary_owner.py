#!/usr/bin/env python3
"""Source contracts for the FL-4512 U2 material-boundary producer."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORLD_PASS = (ROOT / "engine/render/render_world_pass.cpp").read_text()
SCENE = (ROOT / "engine/render/render_scene.cpp").read_text()
INTERNAL = (ROOT / "engine/render/render_internal.h").read_text()


def test_candidate_carries_instance_material_and_two_edge_identities():
    assert "int16_t material_key;" in INTERNAL
    assert "uint64_t world_edge_key;" in INTERNAL
    assert "uint64_t projected_edge_key;" in INTERNAL
    assert "f->material_key = r->mesh_material_key;" in WORLD_PASS
    assert "silhouette_candidate" not in INTERNAL


def test_mesh_connectivity_key_does_not_collapse_edges_to_q24_8():
    body = WORLD_PASS.split("static uint64_t Fl4512StableEdgeKey", 1)[1].split(
        "static uint64_t Fl4512StableProjectedEdgeKey", 1
    )[0]
    assert "uint32_t qa[3], qb[3];" in body
    assert "memcpy(&qa[k], &a[k], sizeof(qa[k]))" in body
    assert "lroundf(a[k] * 256.0f)" not in body
    assert "Q24.8 remains the evidence ABI" in body


def test_retired_face_visual_is_not_material_boundary_authority():
    body = WORLD_PASS.split("void Renderer::FinalizeProjectedSurfaceFeatures()", 1)[1].split(
        "static int create_auto_mat", 1
    )[0]
    assert "face visual byte never participates" in body
    assert "visual &" not in body
    assert "f->material_key" in body


def test_exactly_two_materials_admit_boundary_and_ambiguity_fails_closed():
    body = WORLD_PASS.split("void Renderer::FinalizeProjectedSurfaceFeatures()", 1)[1]
    assert "distinct_materials == 2" in body
    assert "feature_kind = 3" in body
    assert "distinct_materials > 2" in body
    assert "eligible = 0" in body


def test_finalization_runs_once_after_all_world_submissions_before_resolve():
    finalizer = SCENE.index("r->FinalizeProjectedSurfaceFeatures();")
    last_world = SCENE.rindex("QueryWorld(", 0, finalizer)
    resolve = SCENE.index("RenderStageResolve(", finalizer)
    assert last_world < finalizer < resolve
    assert SCENE.index("Fl4131BuildPreGlyphScene(r, width, height);") < finalizer
    assert SCENE.count("Fl4131BuildPreGlyphScene(r, width, height);") == 1


def test_screen_exterior_not_facing_split_owns_silhouette_kind():
    classifier = WORLD_PASS.split("static void Fl4512ClassifyProjectedFeatures", 1)[1].split(
        "static double Fl4512ClampDoubleV1", 1
    )[0]
    assert "a.feature_kind = 2;" in classifier
    assert "silhouette_candidate" not in classifier
    assert "occurrences == 1 || facing_split) { a.feature_kind = 1" not in classifier
    # FL-4512 U2 cut: kind 1 is no longer a promotion of a topology candidate.
    # The candidate stays kind 2 above, and the sole kind-1 writer is the
    # visibility-lattice producer, which runs inside the finalizer AFTER the
    # material-boundary fanout so it cannot perturb kind-2/3/4 classification.
    finalizer = WORLD_PASS.split("void Renderer::FinalizeProjectedSurfaceFeatures()", 1)[1]
    assert "Fl4512PromoteScreenExteriorSilhouettesV1(this);" not in finalizer
    assert "Fl4512ProduceVisibleBoundarySilhouetteLatticeV1(this);" in finalizer
    assert finalizer.index("edges[i]->feature_kind = 3;") < finalizer.index(
        "Fl4512ProduceVisibleBoundarySilhouetteLatticeV1(this);"
    )
