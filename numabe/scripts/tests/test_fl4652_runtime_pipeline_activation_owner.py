import shutil
import struct
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
RESOLVER_H = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.h").read_text(encoding="utf-8")
RESOLVER_CPP = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text(encoding="utf-8")
BRIDGE = (ROOT / "engine/game_render_bridge.cpp").read_text(encoding="utf-8")
WEB = (ROOT / "web/game_web.cpp").read_text(encoding="utf-8")
WEB_PLATFORM = (ROOT / "web/web_platform.cpp").read_text(encoding="utf-8")
WEB_BUILD = (ROOT / "build-web.sh").read_text(encoding="utf-8")
PRESENTER = (ROOT / "platform/terminal_gl_present.cpp").read_text(encoding="utf-8")
FINAL_TUPLE_OWNER = (
    ROOT / "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp"
).read_text(encoding="utf-8")
EXACT_SELECTOR = (
    ROOT / "apps/fl4512-runtime/platform/fl4683_exact_cell_selector.cpp"
).read_text(encoding="utf-8")
SPACETIME_OWNER = (
    ROOT / "apps/fl4512-runtime/engine/fl4512_spacetime_track_owner.h"
).read_text(encoding="utf-8")
TRANSFORM_V2 = (
    ROOT / "apps/fl4512-runtime/engine/fl4512_glyph_transform_relation_v2.h"
).read_text(encoding="utf-8")
SEAM_MODULE = (
    ROOT / "apps/fl4512-runtime/engine/fl4512_runtime_module.cpp"
).read_text(encoding="utf-8")
FINAL_CELL_ADAPTER = (
    ROOT / "apps/fl4512-runtime/platform/fl4693_native_final_cell_adapter.cpp"
).read_text(encoding="utf-8")
GLYPH_ID_NONE = 0xFFFFFFFF


def _f32_bits(value):
    return struct.unpack("<I", struct.pack("<f", value))[0]


def _bits_f32(value):
    return struct.unpack("<f", struct.pack("<I", value))[0]


def _new_packed_u3_bridge(cell_count):
    return {
        "winner_gid": [12345] * cell_count,
        "winner_runner_gid": [54321] * cell_count,
        "global6": [9.0] * (cell_count * 6),
        "cell_ramp_idx": [7] * cell_count,
        "cell_density_idx": [7] * cell_count,
        "winner_raw_score": [8.0] * (cell_count * 4),
        "winner_raw_meta": [77] * (cell_count * 4),
        "gpu_shader_winner_count": 99,
    }


def _invalidate_packed_u3_model(bridge, w, h, max_w):
    bridge["gpu_shader_winner_count"] = 0
    for y in range(h):
        for x in range(w):
            dst = y * max_w + x
            bridge["winner_gid"][dst] = GLYPH_ID_NONE
            bridge["winner_runner_gid"][dst] = GLYPH_ID_NONE
            for k in range(6):
                bridge["global6"][dst * 6 + k] = 0.0
            bridge["cell_ramp_idx"][dst] = -1
            bridge["cell_density_idx"][dst] = -1
            for k in range(4):
                bridge["winner_raw_score"][dst * 4 + k] = 0.0
                bridge["winner_raw_meta"][dst * 4 + k] = 0


def _publish_packed_u3_model(bridge, dst, cell):
    bridge["winner_gid"][dst] = cell[0]
    bridge["winner_runner_gid"][dst] = cell[1]
    for k in range(4):
        bridge["global6"][dst * 6 + k] = _bits_f32(cell[4 + k])
    bridge["global6"][dst * 6 + 4] = _bits_f32(cell[8])
    bridge["global6"][dst * 6 + 5] = _bits_f32(cell[9])
    bridge["cell_ramp_idx"][dst] = cell[10]
    bridge["cell_density_idx"][dst] = cell[11]
    bridge["winner_raw_score"][dst * 4] = _bits_f32(cell[2])
    bridge["winner_raw_score"][dst * 4 + 1] = 0.0
    bridge["winner_raw_score"][dst * 4 + 2] = 3.402823e38
    bridge["winner_raw_score"][dst * 4 + 3] = float(cell[3])
    bridge["winner_raw_meta"][dst * 4] = GLYPH_ID_NONE
    bridge["winner_raw_meta"][dst * 4 + 1] = 2 if cell[0] != GLYPH_ID_NONE else 0
    bridge["winner_raw_meta"][dst * 4 + 2] = 0
    bridge["winner_raw_meta"][dst * 4 + 3] = 2
    if cell[0] != GLYPH_ID_NONE:
        bridge["gpu_shader_winner_count"] += 1


def _unpack_packed_u3_model(pixels, w, h, max_w, read_ok=True):
    bridge = _new_packed_u3_bridge(max_w * h)
    _invalidate_packed_u3_model(bridge, w, h, max_w)
    if not read_ok:
        return bridge
    packed_w = w * 3
    bridge["gpu_shader_winner_count"] = 0
    for y in range(h):
        for x in range(w):
            src = (y * packed_w + x * 3) * 4
            dst = y * max_w + x
            _publish_packed_u3_model(bridge, dst, pixels[src : src + 12])
    return bridge


def test_pinned_glyph_program_is_the_only_runtime_activation_owner():
    combined = "\n".join((RESOLVER_H, RESOLVER_CPP, BRIDGE, WEB, WEB_BUILD))
    retired_owners = (
        "ASCIICKER_HARRI_RUNTIME",
        "SetHarriRuntimeEnabled",
        "Fl4131RuntimeHarriSetEnabled",
        "Fl4131RuntimeHarriEnableForMaterialGlyphSidecar",
        "Fl4131RuntimeHarriInstallIfEnabled",
    )
    for retired_owner in retired_owners:
        assert retired_owner not in combined

    assert "bool Fl4131RuntimeHarriInstall(void)" in RESOLVER_H
    assert "bool Fl4131RuntimeHarriInstalled(void)" in RESOLVER_H
    assert "const bool glyph_program_loaded = Fl4693ProductGlyphProgramStartV1();" in RESOLVER_CPP
    assert "return glyph_program_loaded;" in RESOLVER_CPP
    assert "Fl4512LoadCompiledPoolArtifact" not in RESOLVER_CPP
    assert "Fl4512ReloadCompiledPoolArtifact" not in combined
    # Seam ownership: activation crosses the neutral install slot at the two
    # former direct-call sites (menu + gameplay); no direct installer call
    # remains in the shared bridge.
    assert BRIDGE.count("install_nrt->ensure_runtime_installed();") == 2
    assert "Fl4131RuntimeHarriInstall();" not in BRIDGE


def test_asciiid_uses_the_shared_runtime_resolver():
    editor = (ROOT / "apps/asciicker-presenter/asciiid.cpp").read_text(encoding="utf-8")
    assert "AsciiidRuntimeGlyphResolveHook" not in editor
    assert "AsciiidInstallRuntimeGlyphResolveHook" not in editor
    assert "Fl4131RuntimeHarriInstall()" in editor


def test_temporal_owner_uses_the_packaged_global_glyph_root():
    # FL-4652 writer cut: legacy presenter temporal margin machinery is fully
    # retired — zero loader, table, path constant, and packaged JSON in the
    # terminal presenter. The packaged global glyph root stays live for the
    # compiler-owned atlas page loader (live Fl4512ResolveGlobalGlyphPathV1).
    assert "FL4512_TEMPORAL_MARGIN_TABLE_PATH" not in PRESENTER
    assert "Fl4512TemporalLoadMarginTableV1" not in PRESENTER
    assert "Fl4512TemporalMarginTableV1" not in PRESENTER
    assert "Fl4512TemporalMarginRowV1" not in PRESENTER
    assert "kFl4512TemporalMarginTableV1Path" not in PRESENTER
    assert "temporal_hysteresis" not in PRESENTER
    assert "Fl4512ResolveGlobalGlyphPathV1" in SEAM_MODULE


def test_authored_profile_result_and_presented_tuple_are_single_owner():
    # FL-4652 writer cut: the legacy presenter temporal metadata comparison
    # retired with Fl4512TemporalSelectFrameV1 — zero temporal tuple writes in
    # the presenter. Single ownership: presenter copies the committed tuple
    # read-only via TermCommitPresentationCellsV1 + Fl4693ReadProfileTupleV1.
    assert "prior.result_kind == current.result_kind" not in PRESENTER
    assert "Fl4693ProfileTupleApplyTemporalV1" not in PRESENTER
    assert "static void TermCommitPresentationCellsV1" in PRESENTER
    assert "Fl4512FinalizePresentationTupleV2" not in PRESENTER
    assert "Fl4693FinalTupleCommitFrameV1(" in FINAL_CELL_ADAPTER
    assert "Fl4693ReadProfileTupleV1" in SEAM_MODULE
    finalizer = FINAL_TUPLE_OWNER.split("bool Fl4693ProfileTupleFinalizeV1", 1)[1]
    finalizer = finalizer.split("const FinalGlyphResultV2* Fl4693ProfileTupleReadV1", 1)[0]
    assert "current.pool_revision > 0u" not in finalizer
    assert "current.pool_revision == 0u" not in finalizer
    assert "else if (input->mesh_profile_cell)" in finalizer
    assert "tuple.selected_role == FL4512_ROLE_MESH_FEATURE" in finalizer
    assert "tuple.final_fg = input->ink" in finalizer
    assert "tuple.final_fg = input->source->fg" in finalizer
    assert "GLYPH_ID_CP437_SPACE" not in finalizer
    assert "u7_mesh_winner_base_preserving" not in PRESENTER


def test_bridge_preserves_selected_glyph_as_provenance_only():
    # Seam ownership: dump/trace U6 projection moved with the seam module;
    # the shared presenter routes the dump_bridge_cells/emit_cell_trace slots.
    bridge = SEAM_MODULE.split("static int Fl4512Term_DumpBridgeCells", 1)[1]
    bridge = bridge.split("static void Fl4512Term_EmitCellTrace", 1)[0]
    # FL-4652 O1 requires bridge and trace to consume the shared U6 projection.
    assert "dump_nrt->dump_bridge_cells" in PRESENTER
    assert "Fl4652ProjectU6ObservationV1(temporal_result)" in bridge
    assert bridge.count("(unsigned)u6_presented_final_gid") == 1
    assert "(unsigned)u6.temporal_presented_gid" in bridge
    assert (
        "(unsigned)presented_role,\n"
		"\t\t\t        (unsigned)u6.temporal_presented_gid" in bridge
    )
    assert (
        "(unsigned)temporal_result.current_gid,\n"
        "\t\t\t        (unsigned)temporal_result.glyph_id" in bridge
    )


def test_u6_tuple_owner_serializes_missing_profile_provenance():
    missing = FINAL_TUPLE_OWNER.split("static FinalGlyphResultV2 MissingAuthorityTuple", 1)[1]
    missing = missing.split("static FinalGlyphResultV2 TupleFromIdentity", 1)[0]
    assert "GLYPH_ID_ABSENT_MISSING_FACT" in missing
    assert "FL4693_FINAL_TUPLE_FAILURE_MISSING_PROFILE_AUTHORITY" in missing
    assert "Fl4693ProfileTuplePublishMissingAuthorityV1" in FINAL_TUPLE_OWNER


def test_final_plane_copier_is_domain_first_and_u6_read_only():
    copier = PRESENTER.split("static void TermCommitPresentationCellsV1", 1)[1]
    copier = copier.split("static bool g_term_player_sprite_visible", 1)[0]
    assert "commit_nrt->commit_presentation_cells" in copier
    assert "Fl4693FinalTupleCommitFrameV1" in FINAL_CELL_ADAPTER
    assert "GetFinalGlyphTuplesV1" in FINAL_CELL_ADAPTER
    assert "tuples[index].final_gid" in FINAL_CELL_ADAPTER
    assert "g_fl4512_temporal_final" not in copier
    assert "g_fl4512_temporal_owner_ready" not in copier
    assert "Fl4652CopyU6TupleV1" not in copier


def test_persistent_structural_backend_requires_v2_table_and_exact_live_facts():
    assert "Fl4512BuildSpacetimeSequencesV4" in SPACETIME_OWNER
    assert "const Fl4693ProductGtrViewV1& gtr" in SPACETIME_OWNER
    assert "claim.glyph_transform_facts" in SPACETIME_OWNER
    assert "Fl4512GlyphTransformClaimFactsValidV2" in SPACETIME_OWNER
    assert "const Fl4512GlyphTransformLiveFactsV2* live_facts" not in SPACETIME_OWNER
    assert "Fl4512FindGlyphTransformLiveFactsV2" not in SPACETIME_OWNER
    assert "FL4512_SPACETIME_FAILURE_PROVENANCE" in SPACETIME_OWNER
    assert "Fl4694GlyphProgramGtrRelationFindV1" in SPACETIME_OWNER
    assert "Fl4512GlyphTransformLiveFactsV2" in TRANSFORM_V2
    assert "Fl4693ProductGlyphProgramGtrViewV1" in FINAL_TUPLE_OWNER
    assert "Fl4512BuildSpacetimeSequencesV4" in FINAL_TUPLE_OWNER
    assert "Fl4693ProductGlyphProgramGtrViewV1" not in PRESENTER
    assert "Fl4512BuildSpacetimeSequencesV4" not in PRESENTER
    assert "TermCommitPresentationCellsV1" in PRESENTER
    assert "Fl4693ProductGlyphProgramImageV1" in SEAM_MODULE
    assert "Fl4512ProductionGlyphTransformRelationV2" not in PRESENTER
    for retired_relation_owner in (
        "Fl4512GlyphTransformRelationTableV1",
        "Fl4512ProductionTransformTableV1",
        "Fl4512BuildSpacetimeSequencesV3",
        "kFl4512GlyphTransformRelationSchemaV1",
        "switch_explained",
    ):
        assert retired_relation_owner not in PRESENTER


def test_presenter_transports_the_committed_tuple_without_rerunning_u6():
    render = PRESENTER.split("void term_render(A3D_WND *wnd)\n{", 1)[1]
    assert render.count("TermCommitPresentationCellsV1(term") == 1
    before_commit = render.split("TermCommitPresentationCellsV1(term", 1)[0]
    assert "Fl4512MaterializeFinalGlyphResults" not in render
    assert "Fl4652MaterializeSpacetimeStructuralV1(" not in before_commit
    assert "Fl4512TemporalSelectFrameV1(" not in before_commit
    assert "Fl4652PrototypeSpacetimeStructuralV1" not in PRESENTER


def test_tuple_transport_is_once_per_bridge_frame_after_game_render():
    render = PRESENTER.split("void term_render", 1)[1]
    tuple_call = "TermCommitPresentationCellsV1(term"
    assert render.count(tuple_call) == 1
    game_render = render.index("term->game->Render(stamp, term->buf, width, height)")
    tuple_materialize = render.index(tuple_call)
    present_upload = render.index("gl3TextureSubImage2D", tuple_materialize)
    assert game_render < tuple_materialize < present_upload


def test_retired_packed_u3_handoff_cannot_restore_or_select_state():
    for token in (
        "HarriReadPackedU3Stage",
        "HarriPublishPackedU3CellV1",
        "harri_u3_pack_fs",
        "packed_u3_ready",
    ):
        assert token not in PRESENTER
    assert "Fl4693FinalTupleCommitFrameV1" in FINAL_CELL_ADAPTER


def test_final_tuple_transport_preserves_compact_presenter_indexing():
    copier = PRESENTER.split("static void TermCommitPresentationCellsV1", 1)[1]
    copier = copier.split("static bool g_term_player_sprite_visible", 1)[0]
    assert "for (int index = 0; index < width * height; ++index)" in FINAL_CELL_ADAPTER
    assert "final_glyphs[index] = tuples[index].final_gid" in FINAL_CELL_ADAPTER


def test_missing_committed_tuple_fails_closed_in_the_presenter():
    copier = PRESENTER.split("static void TermCommitPresentationCellsV1", 1)[1]
    copier = copier.split("static bool g_term_player_sprite_visible", 1)[0]
    assert "if (tuples)" in FINAL_CELL_ADAPTER
    assert "GLYPH_ID_ABSENT_MISSING_FACT" in FINAL_CELL_ADAPTER
    assert "FL4260_ABSENCE_FG_MISSING_FACT" in FINAL_CELL_ADAPTER


def test_failed_packed_readback_publishes_named_missing_fact_tuple():
    missing = FINAL_TUPLE_OWNER.split(
        "static FinalGlyphResultV2 MissingAuthorityTuple", 1
    )[1].split("static FinalGlyphResultV2 TupleFromIdentity", 1)[0]
    assert "GLYPH_ID_ABSENT_MISSING_FACT" in missing
    assert "FL4693_FINAL_TUPLE_FAILURE_MISSING_PROFILE_AUTHORITY" in missing
    assert "Fl4693ProfileTuplePublishMissingAuthorityV1" in FINAL_TUPLE_OWNER


def test_partition_admission_is_owned_before_presenter_transport():
    assert "Fl4693ClassifyProfilePartitionV1" in FINAL_TUPLE_OWNER
    assert "partition.profile_scoped" in FINAL_TUPLE_OWNER
    assert "Fl4693ClassifyProfilePartitionV1" not in PRESENTER
    assert "harri_eligible_tex" not in PRESENTER


def test_compiler_owned_exact_selector_replaces_the_winner_shader():
    assert "harri_winner_fs" not in PRESENTER
    assert "Fl4683ExactCellEvaluateGlyphV1" in EXACT_SELECTOR
    assert "Fl4693RuntimeGlyphResolveHook" in RESOLVER_CPP


def test_runtime_hook_has_no_second_enabled_state_gate():
    assert "g_fl4131_runtime_enabled" not in RESOLVER_CPP
    hook = RESOLVER_CPP.split("static void Fl4131RuntimeHarriResolveHook", 1)[1]
    hook = hook.split("void Fl4131RuntimeHarriFlushProductionCounters", 1)[0]
    assert "if (!in || !out)" in hook
    assert "enabled" not in hook


def test_termpp_presenter_consumes_u6_colors_verbatim():
    shader = PRESENTER.split("const char *term_fs_src", 1)[1]
    shader = shader.split("GLenum term_st", 1)[0]
    assert "contrast_gap" not in shader
    assert "target_gap" not in shader
    assert "fg_color = mix" not in shader
    assert "color = vec4(mix(bg_color, fg_color, glyph_alpha), 1.0);" in shader

def test_legacy_temporal_select_retired_to_single_product_u6_writer():
    # FL-4652: legacy presenter temporal selector is fully retired — zero
    # definition and zero callers in the terminal presenter.
    assert "Fl4512TemporalSelectFrameV1" not in PRESENTER
    # The presenter owns no temporal tuple writes and no product U6 frame
    # writer; a duplicate writer here must fail.
    assert "Fl4693ProfileTupleApplyTemporalV1" not in PRESENTER
    assert "Fl4693FinalizeProductU6FrameV1" not in PRESENTER
    # Single product U6 temporal writer: one definition plus one close-frame
    # call inside the final-tuple owner.
    assert FINAL_TUPLE_OWNER.count("static void Fl4693FinalizeProductU6FrameV1") == 1
    assert (
        FINAL_TUPLE_OWNER.count("Fl4693FinalizeProductU6FrameV1(bridge, width, height, 160)")
        == 1
    )
    # The product writer retains its temporal tuple writes (guards over-deletion).
    assert "Fl4693ProfileTupleApplyTemporalV1(index, &decision)" in FINAL_TUPLE_OWNER
    # Valid terminal/web final-tuple commit consumers are retained: single
    # definition, exactly one terminal slot call plus one web slot call; the
    # Current adapter owns the sole committer invocation.
    assert FINAL_TUPLE_OWNER.count("bool Fl4693FinalTupleCommitFrameV1(") == 1
    assert PRESENTER.count("commit_presentation_cells(term->buf,") == 1
    assert FINAL_CELL_ADAPTER.count("Fl4693FinalTupleCommitFrameV1(") == 1
    assert WEB_PLATFORM.count("commit_web_frame(render_buf,") == 1
