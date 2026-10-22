"""FL-4512 / FL-4652 Active Stroke snake-owner companion diagnostics.

The native harness executes the production V2 snake owner for focused runtime
fixtures: exact integer-boundary checks, admission fail-closed behaviour,
exact-eight-iteration admission, persistent id continuity under reprojection,
non-unit compiler-metric propagation, metric-authority rejection, numeric and
capacity fail-closed handling, and deterministic publication. The source
assertions pin the topology order, metric ownership, and V2 reprojection
ownership. These byte-level diagnostics make no visual acceptance claim; the
current user-reachable viewport plus same-run runtime truth owns visual
verification.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "scripts/tests/fl4512_active_stroke_owner_harness.cpp"
OWNER = ROOT / "apps/fl4512-runtime/engine/fl4512_active_stroke_owner.cpp"
WORLD_PASS = ROOT / "engine/render/render_world_pass.cpp"
SCENE = ROOT / "engine/render/render_scene.cpp"
INTERNAL = ROOT / "engine/render/render_internal.h"
OWNER_HEADER = ROOT / "apps/fl4512-runtime/engine/fl4512_active_stroke_owner.h"
FRAME_INPUT = ROOT / "engine/render/render_frame_input.h"
MODULE = ROOT / "apps/fl4512-runtime/engine/fl4512_runtime_module.cpp"
PRODUCT_PROFILES = ROOT / "apps/fl4512-runtime/engine/fl4512_render_product_profiles.h"
PRODUCT_MANIFEST = "apps/fl4512-runtime/fl4512_product_sources.mk"
LEGACY_OWNER = ROOT / "engine/render/fl4512_active_stroke_owner.cpp"
LEGACY_OWNER_HEADER = ROOT / "engine/render/fl4512_active_stroke_owner.h"
PRESENTER = ROOT / "platform/terminal_gl_present.cpp"
ENGINE_MAKEFILES = (
    "apps/asciicker-presenter/makefile_asciiid",
    "apps/asciicker-presenter/makefile_asciiid_mac",
    "apps/game-fl4512/makefile_game",
    "apps/game-fl4512/makefile_game_mac",
    "apps/terminal-runtime/terminal_runtime_sources.mk",
)


def test_active_stroke_owner_executes_integer_and_lifecycle_fixtures(
    tmp_path: Path,
) -> None:
    """Compile and execute the production snake owner."""

    executable = tmp_path / "fl4512_active_stroke_owner_harness"
    compile_result = subprocess.run(
        [
            "c++",
            "-std=c++17",
            "-O2",
            "-fsanitize=address,undefined",
            "-fno-omit-frame-pointer",
            "-I",
            str(ROOT),
            "-I",
            str(ROOT / "engine"),
            "-I",
            str(ROOT / "engine/render"),
            "-I",
            str(ROOT / "platform"),
            "-I",
            str(ROOT / "server"),
            str(HARNESS),
            str(OWNER),
            "-o",
            str(executable),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    run_result = subprocess.run(
        [str(executable)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert run_result.returncode == 0, run_result.stderr
    assert (
        run_result.stdout.strip()
        == (
                "active-stroke-snake:integer-arithmetic+admission-fail-closed+"
                "exact-eight-iterations+operation-order-fail-closed+"
                "metric-authority+"
                "fragment-recovery+batch-insert-determinism+"
                "pieta-like-fixed-phases+"
                "cold-working-set-retained-bound+"
                "long-segment-endpoint-support+"
                "capacity-transaction+degenerate-candidate-isolation+"
                "output-capacity+"
                "persistent-reprojection+"
                "explicit-retirement+"
                "numeric-fail-closed+capacity-fail-closed+"
                "cold-single-sample-one-pass"
            )
        )


def test_active_stroke_renderer_threading_has_one_live_owner() -> None:
    """Pin the one begin/capture/finalize/free path and all shipped targets."""

    world_source = WORLD_PASS.read_text(encoding="utf-8")
    scene_source = SCENE.read_text(encoding="utf-8")
    internal_source = INTERNAL.read_text(encoding="utf-8")
    owner_source = OWNER.read_text(encoding="utf-8")
    module_source = MODULE.read_text(encoding="utf-8")

    for retired_numeric_path in ("std::sqrt", "std::lround"):
        assert retired_numeric_path not in owner_source
    assert "Fl4512ActiveStrokeArithmeticBoundarySelfTestV2" in owner_source
    publication = owner_source.split("static bool StageSnakeSegments", 1)[1].split(
        "} // namespace", 1
    )[0]
    assert "out.asset_metric_q24_8[axis] = 256u" not in publication
    assert "out.provenance.mesh_detail.asset_metric_q24_8" in publication
    assert "snake.asset_metric_q24_8" in publication
    assert "OwnerUsesMeshDetails(snake.owner)" in publication
    assert owner_source.count("uint32_t asset_metric_q24_8[3];") == 4
    assert "AssetMetricValid(group.samples[0].asset_metric_q24_8)" in owner_source
    assert "AssetMetricEqual(\n\t\t\t\t\tgroup.asset_metric_q24_8" in owner_source
    assert "FL4512_ACTIVE_STROKE_SNAKE_FAILURE_MISSING_AUTHORITY_V2" in owner_source
    assert "stable_control_id" in owner_source
    assert "SegmentIdentity(" in owner_source
    assert "ordered_segment_hash_before" in owner_source
    assert "edit_witnesses" in owner_source
    assert "matched_keys" in owner_source
    assert "matched_t" in owner_source
    assert "rejected_degenerate_projected_segment_count" in owner_source
    for edit_operation in (
        "FL4512_ACTIVE_STROKE_EDIT_BIRTH_V2",
        "FL4512_ACTIVE_STROKE_EDIT_RESAMPLE_INSERT_V2",
        "FL4512_ACTIVE_STROKE_EDIT_RESAMPLE_REMOVE_V2",
        "FL4512_ACTIVE_STROKE_EDIT_DELETE_UNSUPPORTED_V2",
        "FL4512_ACTIVE_STROKE_EDIT_TRIM_V2",
        "FL4512_ACTIVE_STROKE_EDIT_EXTEND_V2",
        "FL4512_ACTIVE_STROKE_EDIT_SPLIT_V2",
        "FL4512_ACTIVE_STROKE_EDIT_MERGE_V2",
        "FL4512_ACTIVE_STROKE_EDIT_RETIRE_V2",
    ):
        assert edit_operation in owner_source
    assert "EncodeSignedQ1_15Witness(dot)" in owner_source
    assert "DecodeSignedQ1_15Witness(" in owner_source
    assert "encoded_negative_one == kFl4512TopologyMagnitudeUnmeasuredV1" in owner_source
    assert "invalid_tangent_witness.valid" in owner_source
    assert "measurement_kind ==\n\t\t\tFL4512_ACTIVE_STROKE_EDIT_MEASUREMENT_TANGENT_Q1_15_V2" in owner_source
    assert "Offset into the positive range" in owner_source
    assert "unsupported ? 0u : (uint64_t)dot" not in owner_source
    assert "two's-complement bit pattern" not in owner_source
    for stale_signed_cast in (
        "(uint64_t)nearest_tangent_dot",
        "(uint64_t)best.tangent_dot_q1_15",
        "(uint64_t)proposal.tangent_dot_q1_15",
    ):
        assert stale_signed_cast not in owner_source
    assert "event.topology_event_magnitude = relation.witness.valid" in owner_source
    assert "? 1u : kFl4512TopologyMagnitudeUnmeasuredV1;" in owner_source
    assert "event.topology_event_magnitude = relation.witness.measured_value" not in owner_source

    assert module_source.count("Fl4512FinalizeActiveStrokeFrameV2(st, r);") == 1
    assert module_source.count("Fl4512AssignActiveStrokeSnakePersistentFrameV2(") == 1
    assert world_source.count("cap_nrt->capture_stroke_reprojection(cap_nst, r);") == 1
    assert "&Fl4512CaptureActiveStrokeReprojectionV2;" in module_source
    assert "st->projected_surface_feature_cap," in module_source
    assert "st->projected_surface_feature_count = ok ? published_feature_count : 0;" in module_source
    # FL-4512 U2 cut: the retired promoter is gone. The visibility-lattice
    # producer is the writer that must land before active-stroke finalization,
    # because the finalizer consumes the complete eligible feature set.
    assert "Fl4512PromoteScreenExteriorSilhouettesV1(this);" not in world_source
    assert (
        module_source.index("Fl4512ProduceVisibleBoundarySilhouetteLatticeV1(st, r);")
        < module_source.index("Fl4512FinalizeActiveStrokeFrameV2(st, r);")
    )
    assert (
        world_source.index("cap_nrt->capture_stroke_reprojection(cap_nst, r);")
        < world_source.index("QueryMesh(m, Renderer::RenderFace, r);")
    )
    assert (
        scene_source.index("beg_nrt->begin_frame(beg_nst, r, &beg_frame);")
        < scene_source.index(
            "QueryWorld(w, planes, clip_world, &cb, r);"
        )
    )
    assert "st->fl4512_active_stroke_event_count = 0;" in module_source
    assert "Fl4512ActiveStrokeSnakeStoreCreateV2();" in module_source
    assert "Fl4512ActiveStrokeSnakeStoreDestroyV2(" in module_source
    assert "Fl4512ActiveStrokeSnakeStore" not in internal_source
    assert "fin_nrt->finalize_projected_features(fin_nst, r);" in scene_source
    assert (
        "const bool reprojection_failed =\n"
        "\t\tst->fl4512_active_stroke_reprojection_failure != 0;"
        in module_source
    )
    assert "const bool pre_admission_ready = store_present && !reprojection_failed;" in module_source
    assert "feature.path_closure_class =" in module_source
    assert "Fl4512ActiveStrokeReprojectionEntryV1" not in internal_source
    assert "Fl4512CaptureActiveStrokeReprojectionV1" not in world_source
    assert "maximal-path" not in owner_source
    assert "admission->relaxation_iterations != 8u" in owner_source
    assert "CountPublishedSegments(" in owner_source
    assert "FL4512_ACTIVE_STROKE_SNAKE_FAILURE_INTERNAL_OPERATION_V2" in owner_source
    assert "FL4512_ACTIVE_STROKE_SNAKE_RESET_INTERNAL_OPERATION_V2" in owner_source
    assert "FL4512_ACTIVE_STROKE_SNAKE_RESET_TOPOLOGY_BUDGET_V2" in owner_source
    assert "required_published_feature_count > feature_capacity" in owner_source
    # The exact-size retry is a transaction over both inputs: candidate
    # filtering cannot alter the raw prefix, and lifecycle/id bookkeeping is
    # restored if the first attempt discovers that publication needs growth.
    owner_group_builder = owner_source.split("static bool BuildOwnerGroups", 1)[1].split(
        "static bool ProcessOwnerGroup", 1
    )[0]
    assert "features[i].eligible = 0u" not in owner_group_builder
    capacity_transaction = owner_source.split(
        "Fl4512ActiveStrokeSnakeStoreV2 store_before_attempt", 1
    )[1]
    assert capacity_transaction.index("store_before_attempt = *store;") < capacity_transaction.index(
        "Fl4693RuntimeEpochTransitionV1("
    )
    capacity_exit = capacity_transaction.split(
        "if (required_published_feature_count > feature_capacity)", 1
    )[1].split("std::vector<Fl4512ProjectedSurfaceFeatureV1> staged_features", 1)[0]
    assert "*store = store_before_attempt;" in capacity_exit
    assert owner_source.index("uint64_t staged_next_semantic_chain_id") < owner_source.index(
        "ProcessOwnerGroup(groups[group_i]"
    )
    assert owner_source.index("store->next_semantic_chain_id = staged_next_semantic_chain_id;") > owner_source.index(
        "features[i] = staged_features[i];"
    )
    assert (
        "if (published_feature_count)\n"
        "\t\t*published_feature_count = (int)staged_count;"
        in owner_source
    )
    process_owner_group = owner_source.split(
        "static bool ProcessOwnerGroup", 1
    )[1].split("static bool AssignFrameInternal", 1)[0]
    topology_rejection = process_owner_group.split(
        "const auto reject_topology_budget", 1
    )[1].split("bool phase_converged", 1)[0]
    assert "return false;" in topology_rejection
    assignment_exit = owner_source.split(
        "if (frame_observation && frame_observation->failure == 0u", 1
    )[1].split("} // namespace", 1)[0]
    assert "FL4512_ACTIVE_STROKE_SNAKE_FAILURE_TOPOLOGY_BUDGET_V2" in assignment_exit
    assert "||" not in assignment_exit.split("return frame_observation", 1)[1]
    phase_markers = (
        "const bool deleted = DeleteUnsupported(",
        "const bool split = SplitSnakes(",
        "if (!TrimEndpoints(",
        "const bool extended = ExtendSnakes(",
        "const bool merged = MergeSnakes(",
        "const size_t uncovered_count =",
    )
    phase_positions = [process_owner_group.index(marker) for marker in phase_markers]
    assert phase_positions == sorted(phase_positions)
    assert process_owner_group.count(
        "for (uint8_t pass = 0; pass < admission.max_topology_passes; ++pass)"
    ) == 1
    assert "size_t delete_pass_limit = snakes.size() + 1u;" in process_owner_group
    assert "delete_pass_limit += snake.controls.size();" in process_owner_group
    assert "if (!delete_valid)\n\t\t\treturn false;" in process_owner_group
    assert "? (size_t)admission.max_tracks - snakes.size() + 1u : 1u;" in process_owner_group
    assert "size_t trim_pass_limit = 1u;" in process_owner_group
    assert "trim_pass_limit += snake.controls.size() - 2u;" in process_owner_group
    assert "const size_t merge_pass_limit = snakes.size() + 1u;" in process_owner_group
    for phase in ("delete", "split", "trim", "merge"):
        phase_loop = process_owner_group.split(
            f"for (size_t pass = 0; pass < {phase}_pass_limit; ++pass)", 1
        )[1].split("phase_converged = false;", 1)[0]
        assert "if (!phase_converged)" in phase_loop
        assert "FL4512_ACTIVE_STROKE_SNAKE_FAILURE_INTERNAL_OPERATION_V2" in phase_loop
        assert "return false;" in phase_loop
    trim_owner = owner_source.split("static bool TrimEndpoints", 1)[1].split(
        "static bool InsertSnakeForSample", 1
    )[0]
    assert "bool changed = false;" in trim_owner
    assert "changed = true;" in trim_owner
    assert "return changed;" in trim_owner
    assert "return true;" not in trim_owner
    assert process_owner_group.index("if (group.prior_rows.empty())") < phase_positions[0]
    insert_phase = process_owner_group.split("size_t control_count_before_insert", 1)[1].split(
        "if (!phase_converged)", 1
    )[0]
    assert "uncovered_count > admission.max_tracks - snakes.size()" in insert_phase
    assert (
        "uncovered_count >\n"
        "\t\t\t\t(admission.max_controls_per_owner -\n"
        "\t\t\t\t\tcontrol_count_before_insert) / 2u"
        in insert_phase
    )
    assert "for (size_t sample_i = 0; sample_i < group.samples.size(); ++sample_i)" in insert_phase
    assert "if (covered_after_merge[sample_i])" in insert_phase
    assert "continue;" in insert_phase
    assert "InsertSnakeForSample(" in insert_phase
    assert "const int32_t bridge_from_a_dot = dist2 == 0u" in owner_source
    assert "const int32_t bridge_from_b_dot = dist2 == 0u" in owner_source
    assert "const auto right_begin = proposal.distance2 == 0u" in owner_source
    assert "right.controls.begin() + 1" in owner_source
    assert "static bool TopologyWithinCapacity(" in owner_source
    assert "extension_step < admission.extension_max_steps" in process_owner_group
    assert "admission.extension_step_q16_16, remaining" in owner_source
    assert "std::vector<Proposal> actions;" in owner_source
    assert "selected_action[proposal.snake_a] = action_index;" in owner_source
    for authored_field in (
        "topology_balance_q0_16",
        "spring_stiffness_q1_15",
        "extension_step_q16_16",
        "extension_max_steps",
    ):
        assert owner_source.count(f"admission.{authored_field}") >= 1
    support = owner_source.split("static void RecomputeSupport", 1)[1].split(
        "static bool DeleteUnsupported", 1
    )[0]
    assert "coverage_tangent_dot_min_q1_15" in support
    assert "AbsoluteTangentDotQ1_15(" in support
    assert "support_count != UINT16_MAX" in support
    assert "SupportGridInsertionBudgetFits(" in support
    assert "kSupportAdaptiveCellMax" in support
    assert "cell = std::min(cell * 2, kSupportAdaptiveCellMax);" in support
    assert "std::min(a[0], b[0]) - radius, cell" in support
    assert 'std::getenv("FL4684_ACTIVE_STROKE_WEDGE_TRACE")' in support
    assert '"[FL-4684-SUPPORT] call=%llu snakes=%zu controls=%llu "' in support
    assert "call_index % 64u == 0u || new_high_water" in support
    assert "gSupportTraceMaxSupportCandidatePairs" in support
    assert "support_candidate_pairs += bucket->second.size();" in support
    assert "coverage_candidate_pairs += bucket->second.size();" in support
    assert "struct CoverageContributors" in owner_source
    assert "CoverageRemovalPreserves(" in trim_owner
    assert "RemoveCoverageSegment(" in trim_owner
    assert "std::vector<SnakeRecord> candidate = *snakes;" in trim_owner
    assert "if (trim_contributors.available)" in trim_owner
    assert "CountVisibleSupportBySnake(" in process_owner_group
    assert "std::vector<SnakeRecord> one_snake(1u, snakes[snake_i]);" in process_owner_group

    registration = "apps/fl4512-runtime/engine/fl4512_active_stroke_owner.cpp"
    assert (ROOT / PRODUCT_MANIFEST).read_text().count(registration) == 1
    for makefile_name in ENGINE_MAKEFILES:
        makefile_text = (ROOT / makefile_name).read_text(encoding="utf-8")
        assert f"include {PRODUCT_MANIFEST}" in makefile_text, makefile_name
        assert "$(FL4512_CORE_SRCS)" in makefile_text, makefile_name
    assert registration not in (ROOT / "makefile_server").read_text(encoding="utf-8")


def test_degenerate_rejection_count_stays_internal_to_the_sealed_bridge_v18_adapter() -> None:
    """Pin the typed carrier without silently changing bridge-v18 JSON."""

    owner_header = OWNER_HEADER.read_text(encoding="utf-8")
    frame_input = FRAME_INPUT.read_text(encoding="utf-8")
    internal = INTERNAL.read_text(encoding="utf-8")
    presenter = PRESENTER.read_text(encoding="utf-8")
    field = "rejected_degenerate_projected_segment_count"
    assert owner_header.count(field) == 1
    assert PRODUCT_PROFILES.read_text().count(field) == 1
    assert MODULE.read_text().count(field) == 2
    assert field not in frame_input
    assert field not in internal
    assert field not in presenter


def test_active_stroke_single_owner_cut_has_no_legacy_duplicate() -> None:
    """Regression: legacy engine/render owner stays deleted; one live owner."""
    assert not LEGACY_OWNER.exists(), "legacy owner source still present"
    assert not LEGACY_OWNER_HEADER.exists(), "legacy owner header still present"
    cached = subprocess.run(
        ["git", "ls-files", "--cached", "--", "engine/render/fl4512_active_stroke_owner.cpp",
         "engine/render/fl4512_active_stroke_owner.h"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert cached.returncode == 0, cached.stderr
    assert "fl4512_active_stroke_owner" not in cached.stdout, cached.stdout
    tree = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD", "--",
         "engine/render/fl4512_active_stroke_owner.cpp",
         "engine/render/fl4512_active_stroke_owner.h"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert tree.returncode == 0, tree.stderr
    if "fl4512_active_stroke_owner" in tree.stdout:
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-status", "--",
             "engine/render/fl4512_active_stroke_owner.cpp",
             "engine/render/fl4512_active_stroke_owner.h"],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert "engine/render/fl4512_active_stroke_owner.cpp" in staged.stdout
        assert "engine/render/fl4512_active_stroke_owner.h" in staged.stdout
        assert staged.stdout.count("engine/render/fl4512_active_stroke_owner") == 2
    assert OWNER.exists(), "current-runtime owner source missing"
    assert OWNER_HEADER.exists(), "current-runtime owner header missing"
    registration = "apps/fl4512-runtime/engine/fl4512_active_stroke_owner.cpp"
    assert (ROOT / PRODUCT_MANIFEST).read_text().count(registration) == 1
    for makefile_name in (*ENGINE_MAKEFILES, "build-web.sh"):
        makefile_text = (ROOT / makefile_name).read_text(encoding="utf-8")
        assert PRODUCT_MANIFEST in makefile_text, makefile_name
        assert registration not in makefile_text, makefile_name
        assert "engine/render/fl4512_active_stroke_owner.cpp" not in makefile_text, makefile_name
