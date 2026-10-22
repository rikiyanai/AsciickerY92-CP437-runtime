"""FL-4693 P6 source contracts for the compiled-artifact owner swap.

These checks establish ownership and build linkage. Runtime pixels and visual
acceptance require the separate headed receipts.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESOLVER = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text()
RESOLVER_H = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.h").read_text()
PRESENTER = (ROOT / "platform/terminal_gl_present.cpp").read_text()
FRAME_INPUT = (ROOT / "engine/render/render_frame_input.h").read_text()
RENDER_RESOLVE = (ROOT / "engine/render/render_resolve.cpp").read_text()
GTR_H = (ROOT / "apps/fl4512-runtime/engine/fl4512_glyph_transform_relation_v2.h").read_text()
GTR_CPP = (ROOT / "apps/fl4512-runtime/engine/fl4512_glyph_transform_relation_v2.cpp").read_text()
SPACETIME = (ROOT / "apps/fl4512-runtime/engine/fl4512_spacetime_track_owner.h").read_text()
PRODUCT = (ROOT / "apps/fl4512-runtime/platform/fl4693_product_glyph_program.cpp").read_text()
WEB_CPP = (ROOT / "web/game_web.cpp").read_text()
WEB_HTML = (ROOT / "web/game_web.html").read_text()
WEB_BUILD = (ROOT / "build-web.sh").read_text()
FINAL_TUPLE = (
    ROOT / "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp"
).read_text()


def test_e7_runtime_reload_owner_is_deleted_and_candidates_cannot_publish():
    combined = RESOLVER_H + RESOLVER + PRESENTER
    for retired in (
        "Fl4512ReloadCompiledPoolArtifact",
        "Fl4512RestoreCompiledPoolArtifactSameRevision",
        "Fl4512LoadCompiledPoolArtifact",
        "Fl4693LegacyPartitionBucketV1",
    ):
        assert retired not in combined
    assert "Fl4512LoadCompiledProfileMetadataV1" in RESOLVER
    assert "memset(profile.ramp, 0" in RESOLVER
    assert "memset(&profile.mesh_bundle, 0" in RESOLVER
    assert "g_fl4512_aspect_pool_count = 0" in RESOLVER
    assert "Fl4683GlyphProgramQueryExecuteV1(program, &query)" in RESOLVER


def test_e8_product_consumes_the_format_v4_projection_without_fallback():
    combined = GTR_H + GTR_CPP + PRESENTER + SPACETIME
    assert "Fl4512ProductionGlyphTransformRelationV2" not in combined
    assert "Fl4512LoadGlyphTransformRelationV2(" not in combined
    assert "Fl4512LoadGlyphTransformRelationV2ForTests" in combined
    assert "Fl4693ProductGlyphProgramGtrViewV1" in FINAL_TUPLE
    assert "Fl4694GlyphProgramGtrRelationFindV1" in SPACETIME
    assert "relation->compiled_transform_cost" in SPACETIME
    assert "Fl4512GlyphTransformCostV2(" not in SPACETIME


def test_one_validated_image_grants_rights_and_owns_all_queries():
    assert "g_image->program.runtime_rights = g_image->declared_future_rights" in PRODUCT
    assert "g_image->program.runtime_consumption_allowed = 1u" in PRODUCT
    assert "Fl4693ProductGlyphProgramImageV1" in PRODUCT
    assert "Fl4693ProductGlyphProgramOwnsProfileV1" in PRODUCT
    assert "Fl4693ProductGlyphProgramGtrViewV1" in PRODUCT
    assert "Fl4683ExactCellSelectV1(g_image, scene_fact)" in PRODUCT


def test_all_five_p6_build_targets_link_the_same_owner():
    manifest_path = "apps/fl4512-runtime/fl4512_product_sources.mk"
    manifest = (ROOT / manifest_path).read_text()
    for source in (
        "apps/fl4512-runtime/platform/fl4683_glyph_program_loader.cpp",
        "apps/fl4512-runtime/platform/fl4683_exact_cell_selector.cpp",
        "apps/fl4512-runtime/platform/fl4693_product_glyph_program.cpp",
    ):
        assert manifest.count(source) == 1
    for name in (
        "apps/game-fl4512/makefile_game",
        "apps/game-fl4512/makefile_game_mac",
        "apps/asciicker-presenter/makefile_asciiid",
        "apps/asciicker-presenter/makefile_asciiid_mac",
        "build-web.sh",
    ):
        text = (ROOT / name).read_text()
        assert "FL4683_GLYPH_PROGRAM_LOADER_ENABLED" in text
        assert "FL4693_PRODUCT_GLYPH_PROGRAM_ENABLED" in text
        assert manifest_path in text
        if name != "build-web.sh":
            assert "$(FL4512_PROGRAM_SRCS)" in text
        else:
            assert 'FL4512_MANIFEST=' in text
            assert 'PROGRAM' in text


def test_exact_decision_reaches_deep_tuple_owner_without_reselection():
    assert "Fl4693ProductGlyphSelectV1(adapter, &fact)" in RESOLVER
    assert "fl4693_exact_cell_decision_valid[idx] = 1u" in RESOLVER
    assert "Fl4693ProfileUnaryPublishExactV1(" in RESOLVER
    publisher = FINAL_TUPLE.split("bool Fl4693ProfileUnaryPublishExactV1", 1)[1]
    publisher = publisher.split("bool Fl4693ProfileTuplePublishMissingAuthorityV1", 1)[0]
    assert "Fl4693ProductGlyphSelectV1" not in publisher
    assert "Fl4693ApplyCachedExactSelectionsToU6V1" not in PRESENTER


def test_decline_or_unknown_scorer_cannot_be_published_as_primitive_evidence():
    publisher = FINAL_TUPLE.split("bool Fl4693ProfileUnaryPublishExactV1", 1)[1]
    publisher = publisher.split("bool Fl4693ProfileTuplePublishMissingAuthorityV1", 1)[0]
    assert "selected_scorer_supported" in publisher
    assert "FL4683_GLYPH_PROGRAM_SCORER_STRUCTURAL_DECLINE" in publisher
    assert (
        "else if (scorer_kind == FL4683_GLYPH_PROGRAM_SCORER_PRIMITIVE_FIELD)"
        in publisher
    )
    hook = RESOLVER.split("static void Fl4693RuntimeGlyphResolveHook", 1)[1]
    hook = hook.split('extern "C" const char* Fl4693GetRuntimePredictionSnapshotJson', 1)[0]
    assert "const bool unary_published = Fl4693ProfileUnaryPublishExactV1(" in hook
    assert "FL4683_EXACT_CELL_OUTCOME_REJECTED" in hook
    assert "FL4683_REJECTION_UNSUPPORTED_COMPILED_SCORER" in hook
    assert "GLYPH_ID_ABSENT_MISSING_FACT" in hook


def test_non_profile_compositors_cannot_reuse_a_buried_world_fact():
    hook = RESOLVER.split("static void Fl4693RuntimeGlyphResolveHook", 1)[1]
    hook = hook.split("extern \"C\" const char* Fl4693GetRuntimePredictionSnapshotJson", 1)[0]
    assert "Fl4693ClassifyProfilePartitionV1(in->dispatch_surface" in hook
    assert "Fl4693ClassifySceneFactPartitionV1(&fact)" not in hook
    assert "dispatch_surface[idx] = in->dispatch_surface" in hook
    assert "GLYPH_DISPATCH_WATER" in FRAME_INPUT
    assert "GLYPH_DISPATCH_ANSI_OVERLAY" in FRAME_INPUT
    assert "water_surface_owned" in RENDER_RESOLVE
    assert "terrain_dispatch_surface" in RENDER_RESOLVE
    assert "Fl4693FinalTupleReconcilePostWorldV1" in FINAL_TUPLE
    assert "bridge->dispatch_surface[index] = GLYPH_DISPATCH_ANSI_OVERLAY" in FINAL_TUPLE
    assert "bridge->fl4693_exact_cell_decision_valid[index] = 0u" in FINAL_TUPLE
    assert '\\"ansi_overlay\\":%u' in RESOLVER
    assert '\\"water\\":%u' in RESOLVER
    assert '\\"sprite_actor\\":%u' in RESOLVER


def test_web_presentation_has_no_runtime_glyph_injection_owner():
    combined = WEB_CPP + WEB_HTML + WEB_BUILD
    assert "GlyphSidecarTestInject" not in combined
    assert "fl4131_inject" not in combined
    assert "WebRenderGlyphSidecarWrite" not in WEB_CPP
    assert "GetGlyphSidecar" not in combined
    assert "GetFinalGlyphTuplesV1" in combined
