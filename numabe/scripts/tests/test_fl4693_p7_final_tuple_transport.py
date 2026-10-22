"""FL-4693 P7 contracts for the sole cross-target final tuple owner."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HEADER = (ROOT / "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.h").read_text()
OWNER = (ROOT / "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp").read_text()
BRIDGE = (ROOT / "engine/game_render_bridge.cpp").read_text()
PRESENTER = (ROOT / "platform/terminal_gl_present.cpp").read_text()
RENDER_SCENE = (ROOT / "engine/render/render_scene.cpp").read_text()
FINAL_CELL_ADAPTER = (
    ROOT / "apps/fl4512-runtime/platform/fl4693_native_final_cell_adapter.cpp"
).read_text()
WEB_CPP = (ROOT / "web/game_web.cpp").read_text()
WEB_PLATFORM = (ROOT / "web/web_platform.cpp").read_text()
WEB_HTML = (ROOT / "web/game_web.html").read_text()
WEB_BUILD = (ROOT / "build-web.sh").read_text()
SPRITE = (ROOT / "engine/render/render_sprite_blit.cpp").read_text()
HANDSHAKE = (ROOT / "apps/fl4512-runtime/platform/fl4683_scene_fact_handshake.h").read_text()
U6_HELPERS = (ROOT / "apps/fl4512-runtime/platform/fl4512_u6_presentation_tuple.h").read_text()
RESOLVER = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text()


def test_final_tuple_is_one_full_width_cross_target_abi():
    assert "struct FinalGlyphResultV2" in HEADER
    assert "sizeof(FinalGlyphResultV2) == 48" in HEADER
    assert "offsetof(FinalGlyphResultV2, final_gid) == 4" in HEADER
    assert "offsetof(FinalGlyphResultV2, failure) == 29" in HEADER
    assert "GetFinalGlyphTupleCensusJsonV1" in HEADER
    for export in (
        "GetFinalGlyphTuplesV1",
        "GetFinalGlyphTupleStrideV1",
        "GetFinalGlyphTupleWidthV1",
        "GetFinalGlyphTupleHeightV1",
    ):
        assert export in HEADER
        assert f'"_{export}"' in WEB_BUILD
    assert '"_GetFinalGlyphTupleCensusJsonV1"' in WEB_BUILD


def test_deep_owner_is_the_only_final_tuple_constructor():
    assert "class ProfileTupleStoreV1" in OWNER
    assert "Fl4693ProfileTupleApplyTemporalV1" in OWNER
    assert "Fl4693ProfileTupleFinalizeV1" in OWNER
    assert "FinalGlyphResultV2 presented" not in PRESENTER
    assert "g_fl4693_profile_tuple_owner" not in PRESENTER
    for retired_writer in (
        "Fl4512PublishMissingFactTupleV1",
        "Fl4652ApplyTonalUnderStrokeTupleV1",
        "Fl4652CopyU6TupleV1",
    ):
        assert retired_writer not in U6_HELPERS
        assert retired_writer not in PRESENTER


def test_frozen_selector_slot_is_read_only_and_nonpresenting_builds_fail_closed():
    assert "const FinalGlyphResultV2* output_tuple" in HANDSHAKE
    assert "FinalGlyphResultV2* output_tuple;" not in HANDSHAKE.replace(
        "const FinalGlyphResultV2* output_tuple;", ""
    )
    partition = RESOLVER.split("Fl4693ClassifyProfilePartitionV1", 1)[1]
    partition = partition.split('extern "C"', 1)[0]
    assert "#if defined(FL4693_PRODUCT_GLYPH_PROGRAM_ENABLED)" in partition
    assert "FL4693_PROFILE_PARTITION_FAILURE_MISSING_POOL_AUTHORITY" in partition

    # Scene state opens separately from presentation tuples. Both cross the
    # neutral tables; the product transport owns reconcile/commit order.
    assert "beg_nrt->begin_frame(beg_nst, r, &beg_frame)" in RENDER_SCENE
    assert "frame_presentation->bridge_reset_for_frame(width, height)" in BRIDGE
    services = (ROOT / "apps/fl4512-runtime/platform/fl4512_presenter_services.cpp").read_text()
    assert "Fl4693FinalTupleBeginFrameV1(w, h)" in services
    assert "close_nrt->close_world_frame(ptr, width, height, width)" in BRIDGE
    epilogue = OWNER.split("void Fl4693FinalTupleCloseWorldFrameV1", 1)[1]
    epilogue = epilogue.split("bool Fl4693FinalTupleCommitFrameV1", 1)[0]
    assert epilogue.index("Fl4693FinalTupleReconcilePostWorldV1") < epilogue.index(
        "Fl4693FinalizeProductU6FrameV1"
    )
    assert "bridge->dispatch_surface[index] = GLYPH_DISPATCH_ANSI_OVERLAY" in OWNER
    assert "bridge->profile_namespace[index] = FL4512_PROFILE_NAMESPACE_NONE" in OWNER
    assert "bridge->fl4693_exact_cell_decision_valid[index] = 0u" in OWNER


def test_exact_harri_measurement_is_observer_only_and_schema_compatible():
    observer = OWNER.split(
        "void ProductObserveExactHarriTemporalV1", 1
    )[1].split("bool ProductJsonCanonicalAppendV1", 1)[0]
    finalizer = OWNER.split(
        "static void Fl4693FinalizeProductU6FrameV1", 1
    )[1].split("void Fl4693FinalTupleCloseWorldFrameV1", 1)[0]

    # The observer owns only a private calibration history and a JSONL stream.
    # It must never become another presentation or retained-track owner.
    for forbidden in (
        "Fl4693ProfileTuple",
        "g_profile_tuples",
        "g_final[",
        "g_committed[",
        "tonal_history",
        "bridge.final_result[index] =",
    ):
        assert forbidden not in observer
    assert "static ProductTemporalMeasurementStateV1 state = {};" in observer
    assert "state.history" in observer
    assert 'getenv("FL4208_TEMPORAL_MEASURE_PATH")' in observer
    assert "if (!state.output)\n\t\treturn;" in observer
    assert "ProductExactPresentationClaimValidV1(bridge, index)" in observer
    claim_gate = OWNER.split(
        "bool ProductExactPresentationClaimValidV1", 1
    )[1].split("bool ProductTemporalCellSurfaceIdV1", 1)[0]
    assert "bridge.final_result[index].valid" in claim_gate
    assert "bridge.fl4693_exact_cell_decision_valid[index]" in claim_gate
    assert "bridge.fl4683_scene_fact[index].valid" in claim_gate
    assert "ProductExactPresentationClaimValidV1(bridge, index)" in OWNER.split(
        "bool ProductTemporalCellSurfaceIdV1", 1
    )[1].split("void ProductWriteSurfaceIdJsonV1", 1)[0]

    # The stream uses the existing analyzer schema and exact integer replay.
    assert observer.count(r'\"fl4208.temporal_measurement.v2\"') == 2
    for field in (
        r'\"record_kind\":\"frame_summary\"',
        r'\"record_kind\":\"cell\"',
        r'\"mesh_surface_id_valid_cells\"',
        r'\"mesh_owner_hash_collision_cells\"',
        r'\"scorer_kind_cur\"',
        r'\"scorer_version_cur\"',
        r'\"margin_u32\"',
        r'\"gate\"',
    ):
        assert field in observer
    assert "Fl4683ExactCellEvaluateGlyphV1" in observer
    assert "replay.winning_score == (uint64_t)current.score_num" in observer
    assert "replay.score >= replay.winning_score" in observer
    assert "ProductTemporalObservationEpochEqualV1" in observer
    assert "it = state.history.erase(it)" in observer
    assert "fflush(state.output)" in observer

    # Product U6 observes the already-materialized current frame before tuple
    # seeding. The observer cannot change either operation's result.
    assert finalizer.index("ProductMaterializeStructuralV1") < finalizer.index(
        "ProductObserveExactHarriTemporalV1"
    ) < finalizer.index("Fl4693ProfileTupleSeedV1")


def test_structural_materialization_fails_when_a_selected_cell_has_no_node():
    materialize = OWNER.split(
        "void ProductMaterializeStructuralV1", 1
    )[1].split("void ProductObserveExactHarriTemporalV1", 1)[0]
    assert "selected_structural_cell_count" in materialize
    assert "nodes.size() != selected_structural_cell_count" in materialize
    assert "PRODUCT_STRUCTURAL_MATERIALIZATION_UNMATERIALIZED_CELL" in materialize
    assert "claims[(size_t)index].empty()" in materialize
    assert "final = FinalGlyphResultV1{}" in materialize
    carrier = OWNER.split("const bool carrier_valid", 1)[1].split(
        "const Fl4652BridgeActiveStrokeFrameObservationV2", 1
    )[0]
    assert "g_product_structural_materialization_complete" in carrier
    assert "PRODUCT_STRUCTURAL_MATERIALIZATION_OK" in carrier
    assert "FL4512_ACTIVE_STROKE_SNAKE_FAILURE_NONE_V2" in carrier
    assert "FL4512_ACTIVE_STROKE_SNAKE_RESET_NONE_V2" in carrier
    assert "capacity_failure" in carrier
    equality = OWNER.split(
        "static bool ProductStructuralClaimContributorEqualV1", 1
    )[1].split("bool ProductBuildStructuralCandidatesV1", 1)[0]
    for field in (
        "child_surface",
        "child_primitive_id",
        "child_local_feature_id",
        "semantic_segment_id",
        "object_local_endpoints_q24_8",
        "projected_endpoints_q24_8",
        "clipped_mask8",
        "chain_phase0_q16",
        "chain_phase1_q16",
        "semantic_chain_projected_extent_q16_16",
        "visibility_sheet",
        "path_closure_class",
        "Fl4512SequenceGlyphTransformFactsEqualV1",
    ):
        assert field in equality
    conflict_match = OWNER.split(
        "static bool ProductStructuralConflictMatchesClaimV1", 1
    )[1].split("bool ProductBuildStructuralCandidatesV1", 1)[0]
    assert "conflict.cell_index == UINT16_MAX" in conflict_match
    assert "conflict.cell_index == claim.cell_index" in conflict_match
    assert "result.conflicts.begin(), result.conflicts.end()" in materialize


def test_structural_candidates_score_the_active_stroke_merged_target():
    builder = OWNER.split("bool ProductBuildStructuralCandidatesV1", 1)[1].split(
        "bool ProductCommitStructuralGlyphV1", 1
    )[0]
    assert "structural_address_valid" in builder
    assert "FL4683_SCENE_FACT_ROLE_MESH_FEATURE" in builder
    assert "FL4683_ROUTE_FAMILY_MESH_FEATURE_BUNDLE_V1" in builder
    assert "if (!structural_address_valid)" in builder
    assert "SceneFactV1 structural_fact = producer_fact" in builder
    assert "structural_fact.valid = 1u" in builder
    assert "structural_fact.mesh_target_valid = 1u" in builder
    assert "structural_fact.mesh_target_mask8 = target_mask8" in builder
    assert "structural_fact.mesh_target_endpoint_count = target_endpoint_count" in builder
    assert "structural_fact.mesh_target_junction_count = target_junction_count" in builder
    assert "&structural_fact" in builder
    assert "producer_fact.mesh_target_mask8 != target_mask8" not in builder


def test_structural_measurement_io_health_is_typed_and_observable():
    measurement = OWNER.split(
        "struct ProductStructuralTurnMeasurementStateV1", 1
    )[1].split("void Fl4693FinalTupleCloseWorldFrameV1", 1)[0]
    assert "PRODUCT_STRUCTURAL_TURN_MEASUREMENT_PATH_MISSING" in measurement
    assert "PRODUCT_STRUCTURAL_TURN_MEASUREMENT_OPEN_FAILED" in measurement
    assert "PRODUCT_STRUCTURAL_TURN_MEASUREMENT_WRITE_FAILED" in measurement
    assert "Fl4512GetStructuralTurnMeasurementStatusJsonV1" in measurement
    assert r'\"written_frame\"' in measurement
    assert '"_Fl4512GetStructuralTurnMeasurementStatusJsonV1"' in WEB_BUILD


def test_compiled_structural_presentation_blanks_only_non_structural_mesh_cells():
    finalizer = OWNER.split("bool Fl4693ProfileTupleFinalizeV1", 1)[1].split(
        "const FinalGlyphResultV2* Fl4693ProfileTupleReadV1", 1
    )[0]
    assert "else if (input->mesh_profile_cell)" in finalizer
    assert "if (tuple.selected_role == FL4512_ROLE_MESH_FEATURE)" in finalizer
    assert "tuple.final_fg = input->ink" in finalizer
    assert "tuple.final_bg = input->source->bk" in finalizer
    assert "tuple.final_fg = input->source->fg" in finalizer
    assert "if (!input->profile_color_valid)" in finalizer
    commit = OWNER.split("bool Fl4693FinalTupleCommitFrameV1", 1)[1].split(
        "extern \"C\" const void* GetFinalGlyphTuplesV1", 1
    )[0]
    assert "presentation.mesh_profile_cell =" in commit
    assert "Fl4693ProfileTupleFinalizeV1(bridge_index, &presentation)" in commit
    assert "tuple = *profile" in commit


def test_structural_snapshot_serializes_the_complete_claim_join_key():
    snapshot = OWNER.split(
        'extern "C" const char* Fl4512GetStructuralTurnSnapshotJsonV1', 1
    )[1]
    claims = snapshot.split('json.append("],\\\"claims\\\":[")', 1)[1].split(
        'json.append("],\\\"conflicts\\\":[")', 1
    )[0]
    assert r'\"semantic_segment_id\"' in claims
    assert r'\"object_local_endpoints_q24_8\"' in claims
    assert "claim.object_local_endpoints_q24_8[5]" in claims
    assert r'\"projected_endpoints_q24_8\"' in claims


def test_full_and_compact_structural_snapshots_share_fail_closed_order_gates():
    helper = OWNER.split("static bool ProductSpacetimeNodeOrderInvalidV1", 1)[1].split(
        "static bool ProductCurrentSpacetimeNodeOrderInvalidV1", 1
    )[0]
    assert "current.node.node_index != expected_index" in helper
    assert "current.node.u_q16 < nodes[index - 1u].node.u_q16" in helper
    snapshot = OWNER.split(
        'extern "C" const char* Fl4512GetStructuralTurnSnapshotJsonV1', 1
    )[1]
    assert "ProductCurrentSpacetimeNodeOrderInvalidV1()" in snapshot
    assert "!observation_rejection_overflow" in snapshot
    assert "!spacetime_node_order_invalid" in snapshot
    assert r'\"observation_rejection_overflow\"' in snapshot
    assert r'\"spacetime_node_order_invalid\"' in snapshot


def test_structural_snapshot_exports_measured_ordered_edit_witnesses():
    snapshot = OWNER.split(
        'extern "C" const char* Fl4512GetStructuralTurnSnapshotJsonV1', 1
    )[1]
    events = snapshot.split('json.append(header);', 1)[1].split(
        'json.append("],\\\"claims\\\":[")', 1
    )[0]
    for field in (
        r'\"ordered_segment_hash_before\"',
        r'\"ordered_segment_hash_after\"',
        r'\"edit_witness_count\"',
        r'\"edit_witnesses\"',
        r'\"operation\"',
        r'\"measurement_kind\"',
        r'\"count_before\"',
        r'\"count_after\"',
        r'\"measured_value\"',
        r'\"threshold_value\"',
    ):
        assert field in events
    assert "kFl4512ActiveStrokeMaxEditWitnessesV2" in events


def test_continuous_structural_measurement_is_written_after_tuple_commit():
    close = OWNER.split("void Fl4693FinalTupleCloseWorldFrameV1", 1)[1].split(
        "bool Fl4693FinalTupleCommitFrameV1", 1
    )[0]
    commit = OWNER.split("bool Fl4693FinalTupleCommitFrameV1", 1)[1].split(
        "void Fl4693FinalTupleObserveNativeConsumerV1", 1
    )[0]
    assert "ProductWriteStructuralTurnMeasurementV1" not in close
    assert commit.index("g_committed_plane_hash = HashFinalTuplePlaneV1") < commit.index(
        "ProductWriteStructuralTurnMeasurementV1(bridge)"
    )
    measurement = OWNER.split(
        "static void ProductWriteStructuralTurnMeasurementV1", 1
    )[1].split("void Fl4693FinalTupleCloseWorldFrameV1", 1)[0]
    assert "g_product_structural_turn_measurement.armed" in measurement
    assert "Fl4512ArmStructuralTurnMeasurementV1" in OWNER
    assert '"_Fl4512ArmStructuralTurnMeasurementV1"' in WEB_BUILD


def test_continuous_structural_measurement_is_compact_but_conservation_complete():
    writer = OWNER.split(
        "static const char* ProductGetCompactStructuralTurnFrameJsonV1", 2
    )[-1].split(
        'extern "C" const char* Fl4512GetStructuralTurnSnapshotJsonV1', 1
    )[0]
    measurement = OWNER.split(
        "static void ProductWriteStructuralTurnMeasurementV1", 1
    )[1].split("void Fl4693FinalTupleCloseWorldFrameV1", 1)[0]
    assert "ProductGetCompactStructuralTurnFrameJsonV1(bridge)" in measurement
    assert "Fl4512GetStructuralTurnSnapshotJsonV1()" not in measurement
    for field in (
        r'\"fl4512.structural_turn_frame.compact.v1\"',
        r'\"claim_hash\"',
        r'\"contributor_hash\"',
        r'\"orphan_count\"',
        r'\"duplicate_count\"',
        r'\"mismatch_count\"',
        r'\"final_join\"',
        r'\"events\"',
        r'\"snakes\"',
        r'\"ordered_segments\"',
        r'\"prior_ordered_segments\"',
        r'\"correspondence\"',
        r'\"spacetime_nodes\"',
        r'\"prior_node_index\"',
        r'\"glyph_id\"',
        r'\"final_gid\"',
        r'\"tuple_final_gid\"',
        r'\"tuple_decision_valid\"',
        r'\"contributor_count\"',
    ):
        assert field in writer
    assert "ProductCompactClaimKeyV1" in writer
    assert "ProductCompactContributorKeyV1" in writer
    assert "ProductCompactAppendEventJsonV1" in writer
    assert "observation.node.cell_index, contributor" in writer


def test_topology_anchor_records_the_committed_glyph_for_same_frame_join():
    materialize = OWNER.split(
        "void ProductMaterializeStructuralV1", 1
    )[1].split("void ProductObserveExactHarriTemporalV1", 1)[0]
    anchor_commit = materialize.split(
        "const Fl4512SequenceAnchorTraceV1& anchor", 1
    )[1].split("for (size_t chain_index", 1)[0]
    assert "observation.node.glyph_id = anchor.sequence_anchor_gid" in anchor_commit


def test_shared_u6_applies_harri_v2_u7_and_non_temporal_s_pf_contracts():
    loader = OWNER.split("bool ProductLoadTemporalPolicyV1", 1)[1].split(
        "enum ProductTemporalDecisionKindV1", 1
    )[0]
    apply = OWNER.split("void ProductApplyTemporalPolicyV1", 1)[1].split(
        "static void Fl4693FinalizeProductU6FrameV1", 1
    )[0]

    # Harri accepts the authored zero-margin tie policy or a fully identified
    # nearest-rank u32 row. Product activation still requires the independent
    # u7 policy. S_pf has no temporal row because its compiled fact declares
    # that temporal policy is not applicable to this structural field.
    assert '"authored_exact_argmin_tie.v1"' in loader
    assert "authored_exact_tie && delta_keep == 0u" in loader
    assert '"calibrated_nearest_rank_p99.v1"' in loader
    assert '"nearest_rank_p99.v1"' in loader
    assert '"mixed_policy_basis.v1"' in loader
    assert 'row, "corpus_hash"' in loader
    assert 'row, "quantile_rank"' in loader
    assert "rank_numerator == 99u && rank_denominator == 100u" in loader
    assert "sample_count > 0u && rank_index < sample_count" in loader
    assert "if (ok && found_harri && found_u7)" in loader
    assert "FL4512_SCORER_PRIMITIVE_FIELD" not in loader

    # Harri history is keyed by the complete producer SurfaceId, exact replay is
    # the sole legality/score source, equal exact scores KEEP, and the selected
    # glyph flows through the sole tuple commit. u7 remains a current-frame FIRST.
    assert "ProductTemporalCellSurfaceIdV1" in apply
    assert "Fl4683ExactCellEvaluateGlyphV1" in apply
    assert "replay.winning_glyph_record_id == current.final_gid" in apply
    assert "replay.winning_score == (uint64_t)current.score_num" in apply
    assert "replay.score >= replay.winning_score" in apply
    assert "replay.score <= replay.winning_score +" in apply
    assert "selected_gid = prior->second.gid" in apply
    assert "Fl4693ProfileTupleApplyTemporalV1(index, &decision)" in apply
    assert "current.scorer_kind == FL4512_SCORER_U7_MASK" in apply
    assert "current.scorer_kind != FL4512_SCORER_SHAPE6_WEIGHTED_L2" in apply

    # S_pf is also a current-frame FIRST, but only for an exact claim that
    # survived post-world reconciliation. It must neither read nor write the
    # SurfaceId history that belongs to temporal Harri decisions.
    s_pf = apply.split(
        "if (current.scorer_kind == FL4512_SCORER_PRIMITIVE_FIELD", 1
    )[1].split(
        "if (current.scorer_kind != FL4512_SCORER_SHAPE6_WEIGHTED_L2", 1
    )[0]
    assert "current.scorer_version == 1u" in s_pf
    assert "ProductExactPresentationClaimValidV1(bridge, index)" in s_pf
    assert "decision.glyph_id = (GlyphId)current.final_gid" in s_pf
    assert "decision.decision_kind = PRODUCT_TEMPORAL_DECISION_FIRST" in s_pf
    assert "decision.presented_live = 1u" in s_pf
    assert "decision.s_star_u32 = current.score_num" in s_pf
    assert "Fl4693ProfileTupleApplyTemporalV1(index, &decision)" in s_pf
    for history_operation in ("history.find", "history[", "history.erase"):
        assert history_operation not in s_pf
    assert "Fl4683ExactCellEvaluateGlyphV1" not in s_pf


def test_rejected_surface_collision_uses_identity_only_to_erase_history():
    rejected = OWNER.split(
        "bool ProductTemporalRejectedCollisionSurfaceIdV1", 1
    )[1].split("void ProductWriteSurfaceIdJsonV1", 1)[0]
    apply = OWNER.split("void ProductApplyTemporalPolicyV1", 1)[1].split(
        "static void Fl4693FinalizeProductU6FrameV1", 1
    )[0]
    for reason in (
        "FL4208_CELL_ID_REASON_MESH_OWNER_HASH_COLLISION",
        "FL4208_CELL_ID_REASON_MESH_ANCHOR_COLLISION",
        "FL4208_CELL_ID_REASON_TERRAIN_ANCHOR_COLLISION",
    ):
        assert reason in rejected
    assert "bridge.fl4208_cell_state[index] != 2u" in rejected
    assert "producer_collision ||" in apply
    assert "const bool history_key_valid = surface_id_valid || producer_collision" in apply
    assert "if (surface_id_valid && !collision && capacity_available)" in apply
    assert "decision.collision = collision ? 1u : 0u" in apply
    assert "history.erase(history_key)" in apply


def test_hud_health_water_and_late_overlays_preserve_authored_identity():
    assert "partition.profile_scoped" in OWNER
    assert "GLYPH_DISPATCH_SPRITE_ACTOR" in OWNER
    assert "SameGlyphIdentity(stage.source_cell, cell)" in OWNER
    # FL-4512 G4 census: sprite retires via the sole owner through the neutral
    # dispatch slot; the transport owns the retire implementation.
    assert "spr_nrt->sprite_dispatch_shape6(this, dst, x, y," in SPRITE
    assert "void Fl4693RetireExactCellDecisionV1(" in OWNER
    assert "Authored text, HUD, health, water" in OWNER
    # FL-4512 G4 census: sprite retires via the sole owner; no direct clear.
    assert "fl4693_exact_cell_decision_valid[idx] = 0u" not in SPRITE
    assert "fl4683_scene_fact[idx].valid = 0u" not in SPRITE

def test_presenters_only_transport_or_copy_committed_tuple():
    # Seam ownership: the shared presenter commits through the neutral slot;
    # the Current adapter owns the sole committer call and identity copy.
    finalizer = PRESENTER.split("static void TermCommitPresentationCellsV1", 1)[1]
    finalizer = finalizer.split("static bool g_term_player_sprite_visible", 1)[0]
    assert "commit_nrt->commit_presentation_cells" in finalizer
    assert "Fl4693FinalTupleCommitFrameV1" in FINAL_CELL_ADAPTER
    assert "GetFinalGlyphTuplesV1" in FINAL_CELL_ADAPTER
    assert "tuples[index].final_gid" in FINAL_CELL_ADAPTER
    # Web commits through its neutral slot; the product bridge stays
    # adapter-side, never in web globals.
    assert "Fl4693FinalTupleCommitFrameV1(" not in WEB_PLATFORM
    assert "commit_nrt->commit_web_frame" in WEB_PLATFORM
    assert "final_tuple_byte_count = render_w * render_h * 48" in WEB_HTML
    assert "render_w * 12, render_h" in WEB_HTML


def test_deleted_gpu_readback_cannot_invalidate_the_exact_owner_tuple():
    assert "Fl4512InvalidateTemporalFrameV1" not in PRESENTER
    assert "HarriInvalidatePackedU3BridgeRegionV1" not in PRESENTER
    finalizer = PRESENTER.split("static void TermCommitPresentationCellsV1", 1)[1]
    finalizer = finalizer.split("static bool g_term_player_sprite_visible", 1)[0]
    assert "commit_nrt->commit_presentation_cells" in finalizer
    assert "Fl4693FinalTupleCommitFrameV1" in FINAL_CELL_ADAPTER



def test_browser_reads_final_gid_and_has_no_identity_reconstruction_fallback():
    shader = WEB_HTML.split('<script id="fs"', 1)[1].split("</script>", 1)[0]
    assert "final_tuple_tex" in shader
    assert "floor(cell_coord.x) * 12.0 + 1.5" in shader
    assert "b0 + b1 * 256.0 + b2 * 65536.0 + b3 * 16777216.0" in shader
    assert "floor(cell.z * 255.0" not in shader
    assert "fallback_glyph_id" not in shader
    assert "color_bytes.b * 255.0" in shader
    assert "color_bytes.a * 255.0" in shader
    assert "float bang" not in shader
    assert "mask = 0.0" in shader
    assert "ak_fallback_glyph_id" not in WEB_HTML
    for stale in (
        "sidecar_tex",
        "fallback_diag_fg",
        "fallback_diag_bg",
        "GetGlyphSidecar",
        "WebRenderGlyphSidecarWrite",
        "WebGlyphSidecarBeginFrame",
        "FL4131RecordFallbackRenderEvent",
    ):
        assert stale not in WEB_CPP + WEB_HTML + WEB_BUILD


def test_retired_native_and_web_output_writers_are_absent():
    runtime = "".join(
        (ROOT / path).read_text()
        for path in (
            "engine/render/render_resolve.cpp",
            "engine/render/render_sprite_blit.cpp",
            "platform/terminal_gl_present.cpp",
            "web/game_web.cpp",
        )
    )
    assert "g_fl4512_temporal_final" not in runtime
    assert "NativeRenderGlyphSidecarWrite" not in runtime
    assert "WebRenderGlyphSidecarWrite" not in runtime
    assert not (ROOT / "engine/render_glyph_sidecar_noop.cpp").exists()
