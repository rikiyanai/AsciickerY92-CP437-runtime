"""Native byte-shape invariants retained from the deleted FL-4652 V3 mirror.

The transform seam now uses the v2 table, V4 solver, exact live facts, and
fail-closed provenance. These structural diagnostics make no visual claim.
"""

import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

def _compile_native(tmp_path: Path) -> Path:
    source = tmp_path / "fl4652_v3_parity.cpp"
    binary = tmp_path / "fl4652_v3_parity"
    source.write_text(
        textwrap.dedent(
            r"""
            #include <cstdio>
            #include <vector>
            #include "engine/render/fl4512_structural_sequence_input.h"

            static Fl4512SequenceCellClaimV2 claim(
                unsigned kind, unsigned local, unsigned long long semantic)
            {
                Fl4512SequenceCellClaimV2 value = {};
                value.feature_ref.surface.world_generation = 1;
                value.feature_ref.surface.owner_kind = 1;
                value.feature_ref.provenance.producer_kind = FL4512_FEATURE_PRODUCER_MESH;
                value.feature_ref.provenance.mesh.mesh_instance_uid = 1;
                value.feature_ref.material_key = 2;
                value.feature_ref.provenance.mesh.depth_owner = 1;
                value.feature_ref.dispatch_surface = 2;
                value.feature_ref.role = 1;
                value.feature_ref.feature_kind = (uint8_t)kind;
                value.feature_ref.semantic_chain_id = semantic;
                value.clipped_mask8 = 1ull << local;
                value.cell_index = (uint16_t)local;
                value.chain_phase0_q16 = (uint16_t)local;
                value.chain_phase1_q16 = (uint16_t)(local + 1);
                value.semantic_chain_projected_extent_q16_16 = 65536;
                value.child_surface = value.feature_ref.surface;
                value.child_primitive_id = local;
                value.child_local_feature_id = local;
                value.semantic_segment_id = semantic ^ ((uint64_t)local << 32) ^ 1u;
                value.visibility_sheet = 1;
                value.path_closure_class = 1;
                return value;
            }

            static void emit(unsigned case_id,
                const std::vector<const Fl4512SequenceCellClaimV2*>& input)
            {
                const Fl4512SequenceCellMergeResultV2 result =
                    Fl4512MergeSequenceCellClaimsV2(input);
                const unsigned code = result.conflicts.empty()
                    ? 0u : (unsigned)result.conflicts[0].code;
                const Fl4512SequenceCellClaimV2* merged =
                    result.merged_claims.empty()
                        ? nullptr : &result.merged_claims[0];
                std::printf(
                    "{\"case_id\":%u,\"input_valid\":%s,"
                    "\"merged_count\":%zu,\"conflict_count\":%zu,"
                    "\"first_conflict_code\":%u,"
                    "\"extent\":%u,\"closure\":%u,\"sheet\":%u,"
                    "\"phase0\":%u,\"phase1\":%u,"
                    "\"mask\":\"%016llx\"}\n",
                    case_id, result.input_valid ? "true" : "false",
                    result.merged_claims.size(), result.conflicts.size(), code,
                    merged
                        ? (unsigned)merged->semantic_chain_projected_extent_q16_16
                        : 0u,
                    merged ? (unsigned)merged->path_closure_class : 0u,
                    merged ? (unsigned)merged->visibility_sheet : 0u,
                    merged ? (unsigned)merged->chain_phase0_q16 : 0u,
                    merged ? (unsigned)merged->chain_phase1_q16 : 0u,
                    (unsigned long long)(merged ? merged->clipped_mask8 : 0u));
            }

            int main()
            {
                Fl4512SequenceCellClaimV2 a = claim(1, 1, 7);
                Fl4512SequenceCellClaimV2 b = claim(1, 2, 7);
                b.cell_index = a.cell_index;
                emit(0, {&a, &b});
                emit(1, {nullptr});
                for (unsigned code = 2; code <= 7; ++code)
                {
                    a = claim(1, 1, 7);
                    b = claim(1, 2, 7);
                    b.cell_index = a.cell_index;
                    if (code == 2) b.semantic_chain_projected_extent_q16_16 = 131072;
                    if (code == 3) b.path_closure_class = 2;
                    if (code == 4) b.visibility_sheet = 2;
                    if (code == 5) a.semantic_chain_projected_extent_q16_16 =
                        b.semantic_chain_projected_extent_q16_16 = 0;
                    if (code == 6) a.visibility_sheet = b.visibility_sheet = 0;
                    if (code == 7) a.path_closure_class =
                        b.path_closure_class = 0;
                    emit(code, {&a, &b});
                }
                // Native case O: exact FeatureRefs sharing one semantic id stay
                // independent. Native case U: each conflicted exact ref emits
                // its own typed failed observation.
                a = claim(1, 1, 9);
                b = claim(2, 2, 9);
                emit(100, {&a, &b});
                Fl4512SequenceCellClaimV2 a2 = a;
                Fl4512SequenceCellClaimV2 b2 = b;
                a2.visibility_sheet = 2;
                b2.visibility_sheet = 2;
                emit(101, {&a, &a2, &b, &b2});
                a = claim(1, 1, 7);
                b = claim(1, 2, 7);
                b.cell_index = a.cell_index;
                a.semantic_chain_projected_extent_q16_16 =
                    b.semantic_chain_projected_extent_q16_16 = 131072;
                emit(201, {&a, &b});
                a = claim(1, 1, 7);
                b = claim(1, 2, 7);
                b.cell_index = a.cell_index;
                a.path_closure_class = b.path_closure_class = 2;
                emit(202, {&a, &b});
                for (unsigned sheet = 1; sheet <= 6; ++sheet)
                {
                    a = claim(1, 1, 7);
                    b = claim(1, 2, 7);
                    b.cell_index = a.cell_index;
                    a.visibility_sheet = b.visibility_sheet = (uint8_t)sheet;
                    emit(210 + sheet, {&a, &b});
                }
                return 0;
            }
            """
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "c++", "-std=c++17", "-I", str(ROOT),
            str(source), "-o", str(binary),
        ],
        check=True,
        cwd=ROOT,
    )
    return binary


def test_v3_merge_conflict_and_featureref_parity(tmp_path: Path) -> None:
    """The native conflict census retains the retired mirror's byte contract."""
    binary = _compile_native(tmp_path)
    native = [
        json.loads(line)
        for line in subprocess.check_output([binary], text=True).splitlines()
    ]
    assert [row["first_conflict_code"] for row in native[:8]] == list(range(8))
    by_case = {row["case_id"]: row for row in native}
    assert by_case[100]["merged_count"] == 2
    assert by_case[101]["conflict_count"] == 2
    assert by_case[201]["extent"] == 131072
    assert by_case[202]["closure"] == 2
    assert [by_case[210 + sheet]["sheet"] for sheet in range(1, 7)] == list(
        range(1, 7)
    )


def _compile_native_persistent_jsonl(tmp_path: Path) -> Path:
    """Compile the real V3 solve/store boundary into canonical JSONL records."""
    source = tmp_path / "fl4652_v3_persistent_parity.cpp"
    binary = tmp_path / "fl4652_v3_persistent_parity"
    source.write_text(
        textwrap.dedent(
            r"""
            #include <algorithm>
            #include <cstdio>
            #include <cstring>
            #include <vector>
            #include "engine/render/fl4512_spacetime_track_owner.h"

            static bool BeginTestFrame(
                Fl4512SpacetimeTrackStoreV1* store,
                unsigned long long frame, unsigned long long profile_revision)
            {
                Fl4693RuntimeEpochV1 epoch = {};
                epoch.world_generation = 7;
                epoch.profile_revision = profile_revision;
                epoch.frame_id.value = frame;
                std::memset(epoch.active_registry_sha256, 't', 64);
                epoch.active_registry_sha256[64] = '\0';
                return Fl4512SpacetimeStoreBeginFrameV3(store, &epoch);
            }

            static Fl4512FeatureRefV1 feature(unsigned material,
                unsigned long long semantic)
            {
                Fl4512FeatureRefV1 ref = {};
                ref.surface.world_generation = 7;
                ref.surface.owner_kind = FL4208_OWNER_MESH;
                ref.provenance.producer_kind = FL4512_FEATURE_PRODUCER_MESH;
                ref.provenance.mesh.mesh_instance_uid = 9;
                ref.material_key = (int16_t)material;
                ref.provenance.mesh.depth_owner = 9;
                ref.dispatch_surface = 2;
                ref.role = 1;
                ref.feature_kind = 1;
                ref.aspect_region_id = 3;
                ref.semantic_chain_id = semantic;
                return ref;
            }

            static Fl4512SequenceGlyphBoundaryPortV1 candidate_ports[2] = {
                {0, FL4512_SEQUENCE_PORT_SIDE_E, 32768, {32767, 0}, 1, {0, 0, 0}},
                {0, FL4512_SEQUENCE_PORT_SIDE_W, 32768, {-32768, 0}, 1, {0, 0, 0}},
            };
            static Fl4512SequencePortConnectivityV1 candidate_connectivity[1] = {
                {0, 0, 1, 0, 256, {0}},
            };
            static Fl4512SequenceGlyphGraphViewV1 candidate_graph = {
                1, 0, 2, 1, {1, 0, 0, 1}, candidate_ports,
                candidate_connectivity,
            };

            static Fl4512SequenceCandidateV1 candidate(unsigned glyph)
            {
                Fl4512SequenceCandidateV1 value = {};
                value.glyph_id = glyph;
                value.mask8 = UINT64_C(0x0000001818181800);
                value.unary_score_num = 10;
                value.graph_view = &candidate_graph;
                return value;
            }

            static Fl4512SequenceNodeInputV2 node(unsigned material,
                unsigned long long semantic, unsigned cell,
                Fl4512SequenceCandidateV1* glyph)
            {
                Fl4512SequenceNodeInputV2 value = {};
                value.claim.feature_ref = feature(material, semantic);
                value.claim.cell_index = (uint16_t)cell;
                value.claim.chain_phase0_q16 = (uint16_t)(cell * 10);
                value.claim.chain_phase1_q16 = (uint16_t)(cell * 10);
                value.claim.semantic_chain_projected_extent_q16_16 = 65536;
                value.claim.semantic_segment_id = semantic ^ ((uint64_t)cell << 32) ^ 1u;
                value.claim.visibility_sheet = 1;
                value.claim.path_closure_class = 1;
                value.target.present = 1;
                value.target.entry_port = candidate_ports[0];
                value.target.exit_port = candidate_ports[1];
                value.bundle_ref_count = 1;
                value.candidates = glyph;
                value.candidate_count = 1;
                return value;
            }

            const Fl4694GlyphProgramGtrRelationV1*
            Fl4694GlyphProgramGtrRelationFindV1(
                const Fl4683GlyphProgramImageV1* image, uint8_t aspect,
                uint32_t from_glyph_id, uint32_t to_glyph_id)
            {
                if (!image || !image->gtr_v2_relations ||
                    image->gtr_v2_relation_count != FL4694_GTR_V2_RELATION_COUNT)
                    return nullptr;
                unsigned from_index = FL4694_GTR_V2_GLYPH_COUNT;
                unsigned to_index = FL4694_GTR_V2_GLYPH_COUNT;
                for (unsigned i = 0; i < FL4694_GTR_V2_GLYPH_COUNT; ++i)
                {
                    const uint32_t glyph = image->gtr_v2_relations[i].to_glyph_id;
                    if (glyph == from_glyph_id) from_index = i;
                    if (glyph == to_glyph_id) to_index = i;
                }
                if (from_index == FL4694_GTR_V2_GLYPH_COUNT ||
                    to_index == FL4694_GTR_V2_GLYPH_COUNT)
                    return nullptr;
                const size_t index =
                    (size_t)aspect * FL4694_GTR_V2_RELATIONS_PER_ASPECT +
                    (size_t)from_index * FL4694_GTR_V2_GLYPH_COUNT + to_index;
                return &image->gtr_v2_relations[index];
            }

            static Fl4693ProductGtrViewV1 table()
            {
                static std::vector<Fl4694GlyphProgramGtrRelationV1> relations;
                static Fl4683GlyphProgramImageV1 image = {};
                if (relations.empty())
                {
                    uint32_t glyph_ids[FL4694_GTR_V2_GLYPH_COUNT] = {};
                    glyph_ids[0] = 100;
                    glyph_ids[1] = 200;
                    for (unsigned i = 2; i < FL4694_GTR_V2_GLYPH_COUNT; ++i)
                        glyph_ids[i] = 200 + i;
                    relations.resize(FL4694_GTR_V2_RELATION_COUNT);
                    for (unsigned aspect = 0;
                        aspect < FL4694_GTR_V2_ASPECT_COUNT; ++aspect)
                        for (unsigned from = 0;
                            from < FL4694_GTR_V2_GLYPH_COUNT; ++from)
                            for (unsigned to = 0;
                                to < FL4694_GTR_V2_GLYPH_COUNT; ++to)
                            {
                                const size_t index =
                                    aspect * FL4694_GTR_V2_RELATIONS_PER_ASPECT +
                                    from * FL4694_GTR_V2_GLYPH_COUNT + to;
                                auto& row = relations[index];
                                row.aspect = (uint8_t)aspect;
                                row.from_glyph_id = glyph_ids[from];
                                row.to_glyph_id = glyph_ids[to];
                                row.relation_confidence_q0_16 = 65535;
                            }
                    image.gtr_v2_relations = relations.data();
                    image.gtr_v2_relation_count = (uint32_t)relations.size();
                }
                Fl4693ProductGtrViewV1 value = {};
                value.image = &image;
                value.format_version = 4;
                value.valid = 1;
                std::strncpy(value.schema, "fl4694.glyph_program_gtr.v1",
                    sizeof(value.schema) - 1);
                std::memset(value.content_sha256, '1', 64);
                value.content_sha256[64] = 0;
                return value;
            }

            static Fl4512GlyphTransformLiveFactsV2 live_fact(
                const Fl4512SequenceCellClaimV2& claim)
            {
                Fl4512GlyphTransformLiveFactsV2 value = {};
                value.feature_ref = claim.feature_ref;
                value.feature_ref_complete = 1;
                value.aspect_address = 0;
                value.visible_support = 1;
                value.expected_support = 1;
                value.projected_tangent_q1_15[0] = 32767;
                value.projected_tangent_present = 1;
                value.observed_projected_offset_present = 1;
                value.pose_relation = FL4512_GTR_V2_POSE_SAME;
                return value;
            }

            static Fl4512SpacetimeBuildResultV4 build_v4(
                const Fl4512SequenceNodeInputV2* inputs, size_t input_count,
                uint16_t grid_width, Fl4512SpacetimeTrackStoreV1* store,
                const Fl4693ProductGtrViewV1& relation,
                bool /*loop_closing*/)
            {
                std::vector<Fl4512SequenceNodeInputV2> owned_inputs(
                    inputs, inputs + input_count);
                for (size_t i = 0; i < input_count; ++i)
                    owned_inputs[i].claim.glyph_transform_facts =
                        live_fact(owned_inputs[i].claim);
                return Fl4512BuildSpacetimeSequencesV4(
                    owned_inputs.data(), owned_inputs.size(), grid_width,
                    store, relation);
            }

            static void emit_key(const Fl4512FeatureRefV1& ref)
            {
                std::printf(
                    "{\"aspect_region_id\":%u,"
                    "\"depth_owner\":\"%016llx\","
                    "\"dispatch_surface\":%u,\"feature_kind\":%u,"
                    "\"material_key\":%d,"
                    "\"mesh_instance_uid\":\"%016llx\",\"role\":%u,"
                    "\"semantic_chain_id\":\"%016llx\","
                    "\"surface\":{\"anchor_u_q\":%u,\"anchor_v_q\":%u,"
                    "\"owner_kind\":%u,\"owner_x\":%d,\"owner_y\":%d,"
                    "\"world_generation\":%llu},"
                    "\"world_generation\":%llu}",
                    (unsigned)ref.aspect_region_id,
                    (unsigned long long)ref.provenance.mesh.depth_owner,
                    (unsigned)ref.dispatch_surface,
                    (unsigned)ref.feature_kind,
                    (int)ref.material_key,
                    (unsigned long long)ref.provenance.mesh.mesh_instance_uid,
                    (unsigned)ref.role,
                    (unsigned long long)ref.semantic_chain_id,
                    (unsigned)ref.surface.anchor_u_q,
                    (unsigned)ref.surface.anchor_v_q,
                    (unsigned)ref.surface.owner_kind,
                    (int)ref.surface.owner_x,
                    (int)ref.surface.owner_y,
                    (unsigned long long)ref.surface.world_generation,
                    (unsigned long long)ref.surface.world_generation);
            }

            static void emit_store_nodes(
                const std::vector<Fl4512SpacetimeTrackNodeV1>& nodes)
            {
                std::printf("[");
                for (size_t i = 0; i < nodes.size(); ++i)
                {
                    if (i) std::printf(",");
                    std::printf(
                        "{\"glyph_id\":%u,\"mask8\":\"%016llx\","
                        "\"u_q16\":%u}",
                        (unsigned)nodes[i].glyph_id,
                        (unsigned long long)nodes[i].mask8,
                        (unsigned)nodes[i].u_q16);
                }
                std::printf("]");
            }

            static const Fl4512TopologyObservationV1* event_for(
                const Fl4512FeatureRefV1& ref,
                const Fl4512SpacetimeTrackStoreV1& store)
            {
                return Fl4512SpacetimeTopologyObservationFindV1(store, ref);
            }

            static void emit(const char* case_id,
                const Fl4512SequenceNodeInputV2* inputs, size_t input_count,
                const Fl4512SpacetimeBuildResultV4& built,
                const Fl4512SpacetimeTrackStoreV1& store,
                unsigned frame, unsigned retired_count = 0)
            {
                std::vector<const Fl4512SequenceCellClaimV2*> claims;
                for (size_t i = 0; i < input_count; ++i)
                    claims.push_back(&inputs[i].claim);
                const Fl4512SequenceCellMergeResultV2 merge =
                    Fl4512MergeSequenceCellClaimsV2(claims);
                unsigned conflict_code = 0;
                for (const auto& conflict : built.conflicts)
                    if (!conflict_code ||
                        (unsigned)conflict.code < conflict_code)
                        conflict_code = (unsigned)conflict.code;
                unsigned components = 0;
                for (const auto& chain : built.chains)
                    if (chain.failure == FL4512_SPACETIME_FAILURE_NONE)
                        ++components;

                std::printf(
                    "{\"case_id\":\"%s\",\"components\":%u,"
                    "\"conflict_code\":%u,\"effective_feature_refs\":[",
                    case_id, components, conflict_code);
                for (size_t i = 0; i < merge.merged_claims.size(); ++i)
                {
                    if (i) std::printf(",");
                    emit_key(merge.merged_claims[i].feature_ref);
                }
                std::printf(
                    "],\"encoding\":\"canonical-jsonl.v1\","
                    "\"feature_ref_key\":[");
                for (size_t i = 0; i < store.records.size(); ++i)
                {
                    if (i) std::printf(",");
                    emit_key(store.records[i].feature_ref);
                }
                std::printf("],\"final_tuple\":[");
                bool comma = false;
                for (const auto& chain : built.chains)
                    if (chain.failure == FL4512_SPACETIME_FAILURE_NONE)
                        for (const auto& solved : chain.nodes)
                        {
                            if (comma) std::printf(",");
                            std::printf(
                                "{\"glyph_id\":%u,\"material_key\":%d}",
                                (unsigned)solved.glyph_id,
                                (int)chain.feature_ref.material_key);
                            comma = true;
                        }
                std::printf(
                    "],\"frame_ordinal\":%u,\"input_valid\":%s,"
                    "\"merged_claims\":[",
                    frame, merge.input_valid ? "true" : "false");
                for (size_t i = 0; i < merge.merged_claims.size(); ++i)
                {
                    const auto& claim = merge.merged_claims[i];
                    if (i) std::printf(",");
                    std::printf(
                        "{\"closure\":%u,\"extent\":%u,"
                        "\"material_key\":%d,\"phase0\":%u,"
                        "\"phase1\":%u,\"sheet\":%u}",
                        (unsigned)claim.path_closure_class,
                        (unsigned)claim.semantic_chain_projected_extent_q16_16,
                        (int)claim.feature_ref.material_key,
                        (unsigned)claim.chain_phase0_q16,
                        (unsigned)claim.chain_phase1_q16,
                        (unsigned)claim.visibility_sheet);
                }
                std::printf(
                    "],\"schema\":\"fl4652.sealer_v3_product_parity_record.v1\","
                    "\"semantic_chain_id\":\"%016llx\",\"solved_nodes\":[",
                    input_count
                        ? (unsigned long long)
                            inputs[0].claim.feature_ref.semantic_chain_id
                        : 0ull);
                comma = false;
                for (const auto& chain : built.chains)
                    if (chain.failure == FL4512_SPACETIME_FAILURE_NONE)
                        for (const auto& solved : chain.nodes)
                        {
                            if (comma) std::printf(",");
                            std::printf("{\"cell_index\":%u,\"feature_ref_key\":",
                                (unsigned)solved.cell_index);
                            emit_key(chain.feature_ref);
                            std::printf(
                                ",\"glyph_id\":%u,\"node_index\":%u,"
                                "\"spacetime_hash\":%u,\"topology_cost\":%u,"
                                "\"u_q16\":%u}",
                                (unsigned)solved.glyph_id,
                                (unsigned)solved.node_index,
                                (unsigned)chain.spacetime_hash,
                                (unsigned)solved.topology_cost,
                                (unsigned)solved.u_q16);
                            comma = true;
                        }
                std::printf("],\"spacetime_failure\":[");
                comma = false;
                for (const auto& chain : built.chains)
                    if (chain.failure != FL4512_SPACETIME_FAILURE_NONE)
                    {
                        if (comma) std::printf(",");
                        unsigned chain_conflict_code = 0;
                        for (const auto& conflict : built.conflicts)
                            if (Fl4512FeatureRefEqualV1(
                                    chain.feature_ref,
                                    conflict.feature_ref))
                            {
                                chain_conflict_code =
                                    (unsigned)conflict.code;
                                break;
                            }
                        std::printf(
                            "{\"conflict_code\":%u,\"failure\":%u,"
                            "\"feature_ref_key\":",
                            chain_conflict_code, (unsigned)chain.failure);
                        emit_key(chain.feature_ref);
                        std::printf("}");
                        comma = true;
                    }
                size_t pending_count = 0;
                size_t commit_count = 0;
                size_t held_count = 0;
                for (const auto& record : store.records)
                {
                    pending_count += record.pending_nodes.size();
                    if (record.pending_frame == store.current_frame &&
                        !record.pending_nodes.empty())
                        ++commit_count;
                    if (record.last_frame &&
                        store.current_frame - record.last_frame > 1)
                        ++held_count;
                }
                std::printf(
                    "],\"store_projection\":{\"capacity_failure\":false,"
                    "\"commit_count\":%zu,\"epoch_changed\":%s,"
                    "\"frame_regressed\":%s,\"held_track_count\":%zu,"
                    "\"pending_node_count\":%zu,\"record_count\":%zu,"
                    "\"records\":[",
                    commit_count, store.epoch_changed ? "true" : "false",
                    store.frame_regressed ? "true" : "false", held_count,
                    pending_count, store.records.size());
                for (size_t i = 0; i < store.records.size(); ++i)
                {
                    const auto& record = store.records[i];
                    if (i) std::printf(",");
                    std::printf("{\"feature_ref_key\":");
                    emit_key(record.feature_ref);
                    if (record.last_frame)
                        std::printf(",\"last_frame_age\":%llu,",
                            (unsigned long long)
                                (store.current_frame - record.last_frame));
                    else
                        std::printf(",\"last_frame_age\":null,");
                    std::printf("\"pending_nodes\":");
                    emit_store_nodes(record.pending_nodes);
                    std::printf(",\"previous_nodes\":");
                    emit_store_nodes(record.previous_nodes);
                    std::printf("}");
                }
                std::printf(
                    "],\"retired_track_count\":%u,"
                    "\"selection_epoch\":\"%016llx\"",
                    retired_count,
                    (unsigned long long)store.epoch.profile_revision);
                const char* outcome = built.conflicts.empty()
                    ? "solved"
                    : (merge.merged_claims.empty()
                        ? "conflict_suppressed"
                        : "solved_with_suppressed_peer");
                std::printf(
                    "},\"structural_outcome\":\"%s\","
                    "\"suppressed_feature_ref_keys\":[",
                    outcome);
                for (size_t i = 0; i < built.conflicts.size(); ++i)
                {
                    if (i) std::printf(",");
                    emit_key(built.conflicts[i].feature_ref);
                }
                std::printf("],\"topology_event\":[");
                for (size_t i = 0; i < built.chains.size(); ++i)
                {
                    if (i) std::printf(",");
                    const Fl4512TopologyObservationV1* event =
                        event_for(built.chains[i].feature_ref, store);
                    std::printf("{\"feature_ref_key\":");
                    emit_key(built.chains[i].feature_ref);
                    std::printf(",\"kind\":%u,\"magnitude\":%u,"
                        "\"magnitude_measured\":%s}",
                        event ? (unsigned)event->event_kind : 0u,
                        event ? (unsigned)event->magnitude : 0u,
                        event && event->magnitude_measured ? "true" : "false");
                }
                std::printf("]}\n");
            }

            int main()
            {
                const Fl4693ProductGtrViewV1 relation = table();
                Fl4512SequenceCandidateV1 glyph100 = candidate(100);

                Fl4512SpacetimeTrackStoreV1 persistent = {};
                BeginTestFrame(&persistent, 1, 9);
                Fl4512SequenceNodeInputV2 one =
                    node(200, 400, 5, &glyph100);
                {
                    Fl4512SpacetimeTrackStoreV1 missing_store = {};
                    BeginTestFrame(&missing_store, 1, 8);
                    const Fl4512SpacetimeBuildResultV4 missing =
                        Fl4512BuildSpacetimeSequencesV4(
                            &one, 1, 16, &missing_store, relation);
                    if (missing.provenance_ok || !missing.chains.empty() ||
                        !missing.anchors.empty())
                        return 10;
                    Fl4512SpacetimeTrackStoreV1 malformed_store = {};
                    BeginTestFrame(
                        &malformed_store, 1, 8);
                    one.claim.glyph_transform_facts = live_fact(one.claim);
                    one.claim.glyph_transform_facts.feature_ref_complete = 2;
                    const Fl4512SpacetimeBuildResultV4 malformed =
                        Fl4512BuildSpacetimeSequencesV4(
                            &one, 1, 16, &malformed_store, relation);
                    if (malformed.provenance_ok || !malformed.chains.empty() ||
                        !malformed.anchors.empty())
                        return 11;
                    one.claim.glyph_transform_facts = {};
                }
                Fl4512SpacetimeBuildResultV4 first =
                    build_v4(
                        &one, 1, 16, &persistent, relation, false);
                emit("positive", &one, 1, first, persistent, 1);

                BeginTestFrame(&persistent, 2, 9);
                Fl4512SpacetimeBuildResultV4 second =
                    build_v4(
                        &one, 1, 16, &persistent, relation, false);
                emit("lifecycle", &one, 1, second, persistent, 2);

                // The focused event fixture owns nonzero event parity. The
                // full stable-plane sealer deliberately remains NONE/zero.
                Fl4512SpacetimeTrackStoreV1 event_store = {};
                BeginTestFrame(&event_store, 1, 20);
                Fl4512SequenceNodeInputV2 event_input =
                    node(200, 400, 5, &glyph100);
                Fl4512TopologyObservationV1 event_observation = {};
                event_observation.feature_ref = event_input.claim.feature_ref;
                event_observation.epoch = event_store.epoch;
                event_observation.participants[0] =
                    event_input.claim.feature_ref;
                event_observation.magnitude =
                    kFl4512TopologyMagnitudeUnmeasuredV1;
                event_observation.feature_ref_complete = 1;
                event_observation.event_kind =
                    FL4512_TOPOLOGY_EVENT_ENDPOINT_APPEAR;
                event_observation.magnitude_measured = 0;
                event_observation.participant_count = 1;
                event_observation.source_kind =
                    FL4512_OBSERVATION_SOURCE_ACTIVE_STROKE;
                if (!Fl4512SpacetimeObserveTopologyV1(
                        &event_store, event_observation))
                    return 12;
                const Fl4512SpacetimeBuildResultV4 event_result =
                    build_v4(
                        &event_input, 1, 16, &event_store, relation, false);
                emit("event", &event_input, 1, event_result, event_store, 1);

                Fl4512SpacetimeTrackStoreV1 rejection_overflow_store = {};
                BeginTestFrame(&rejection_overflow_store, 1, 19);
                rejection_overflow_store.observation_rejections.resize(
                    kFl4512SpacetimeTrackCapacityV1);
                Fl4512RecordObservationRejectionV1(
                    &rejection_overflow_store, event_input.claim.feature_ref,
                    FL4512_OBSERVATION_FAILURE_CAPACITY_EXHAUSTED,
                    FL4512_OBSERVATION_SOURCE_ACTIVE_STROKE);
                if (!rejection_overflow_store.observation_rejection_overflow)
                    return 13;
                BeginTestFrame(&rejection_overflow_store, 2, 19);
                if (rejection_overflow_store.observation_rejection_overflow)
                    return 14;

                Fl4512SpacetimeTrackStoreV1 extent_store = {};
                BeginTestFrame(&extent_store, 1, 21);
                Fl4512SequenceNodeInputV2 extent_input =
                    node(200, 400, 5, &glyph100);
                extent_input.claim.semantic_chain_projected_extent_q16_16 =
                    131072;
                const Fl4512SpacetimeBuildResultV4 extent_result =
                    build_v4(
                        &extent_input, 1, 16, &extent_store, relation, false);
                emit("extent", &extent_input, 1,
                    extent_result, extent_store, 1);

                for (unsigned closure = 1; closure <= 2; ++closure)
                {
                    Fl4512SpacetimeTrackStoreV1 closure_store = {};
                    BeginTestFrame(
                        &closure_store, 1, 21 + closure);
                    Fl4512SequenceNodeInputV2 closure_input =
                        node(200, 400, 5, &glyph100);
                    closure_input.claim.path_closure_class =
                        (uint8_t)closure;
                    const Fl4512SpacetimeBuildResultV4 closure_result =
                        build_v4(
                            &closure_input, 1, 16, &closure_store,
                            relation, false);
                    emit(closure == 1 ? "closure-open" : "closure-closed",
                        &closure_input, 1, closure_result, closure_store, 1);
                }

                for (unsigned sheet = 1; sheet <= 6; ++sheet)
                {
                    Fl4512SpacetimeTrackStoreV1 sheet_store = {};
                    BeginTestFrame(
                        &sheet_store, 1, 30 + sheet);
                    Fl4512SequenceNodeInputV2 sheet_input =
                        node(200, 400, 5, &glyph100);
                    sheet_input.claim.visibility_sheet = (uint8_t)sheet;
                    const Fl4512SpacetimeBuildResultV4 sheet_result =
                        build_v4(
                            &sheet_input, 1, 16, &sheet_store,
                            relation, false);
                    char case_id[16];
                    std::snprintf(case_id, sizeof(case_id),
                        "sheet-%u", sheet);
                    emit(case_id, &sheet_input, 1,
                        sheet_result, sheet_store, 1);
                }

                Fl4512SpacetimeTrackStoreV1 disconnected_store = {};
                BeginTestFrame(
                    &disconnected_store, 1, 40);
                Fl4512SequenceNodeInputV2 disconnected[2] = {
                    node(200, 400, 5, &glyph100),
                    node(200, 400, 40, &glyph100),
                };
                const Fl4512SpacetimeBuildResultV4 disconnected_result =
                    build_v4(
                        disconnected, 2, 16, &disconnected_store,
                        relation, false);
                emit("lifecycle-disconnected", disconnected, 2,
                    disconnected_result, disconnected_store, 1);

                Fl4512SequenceNodeInputV2 dummy = {};
                Fl4512SpacetimeTrackStoreV1 hold_store = {};
                BeginTestFrame(&hold_store, 1, 41);
                (void)build_v4(
                    &one, 1, 16, &hold_store, relation, false);
                BeginTestFrame(&hold_store, 2, 41);
                BeginTestFrame(&hold_store, 3, 41);
                const Fl4512SpacetimeBuildResultV4 hold_result =
                    build_v4(
                        &dummy, 0, 16, &hold_store, relation, false);
                emit("lifecycle-hold", &dummy, 0,
                    hold_result, hold_store, 3);
                BeginTestFrame(&hold_store, 4, 41);
                const Fl4512SpacetimeBuildResultV4 retire_result =
                    build_v4(
                        &dummy, 0, 16, &hold_store, relation, false);
                emit("lifecycle-retire", &dummy, 0,
                    retire_result, hold_store, 4, 1);

                BeginTestFrame(&persistent, 3, 10);
                const Fl4512SpacetimeBuildResultV4 epoch_result =
                    build_v4(
                        &one, 1, 16, &persistent, relation, false);
                emit("lifecycle-epoch-reset", &one, 1,
                    epoch_result, persistent, 3);

                Fl4512SpacetimeTrackStoreV1 regression_store = {};
                BeginTestFrame(&regression_store, 1, 42);
                (void)build_v4(
                    &one, 1, 16, &regression_store, relation, false);
                BeginTestFrame(&regression_store, 1, 42);
                const Fl4512SpacetimeBuildResultV4 regression_result =
                    build_v4(
                        &one, 1, 16, &regression_store, relation, false);
                emit("lifecycle-frame-regression", &one, 1,
                    regression_result, regression_store, 1);

                for (unsigned shifted = 0; shifted < 2; ++shifted)
                {
                    const unsigned first_frame = shifted ? 101 : 1;
                    Fl4512SpacetimeTrackStoreV1 age_store = {};
                    BeginTestFrame(
                        &age_store, first_frame, 43);
                    (void)build_v4(
                        &one, 1, 16, &age_store, relation, false);
                    BeginTestFrame(
                        &age_store, first_frame + 1, 43);
                    const Fl4512SpacetimeBuildResultV4 age_result =
                        build_v4(
                            &one, 1, 16, &age_store, relation, false);
                    emit(shifted ? "age-offset" : "age-base",
                        &one, 1, age_result, age_store, first_frame + 1);
                }

                Fl4512SpacetimeTrackStoreV1 q_store = {};
                BeginTestFrame(&q_store, 1, 10);
                Fl4512SequenceNodeInputV2 q =
                    node(200, 400, 5, &glyph100);
                q.claim.semantic_chain_projected_extent_q16_16 = 0;
                Fl4512SpacetimeBuildResultV4 q_result =
                    build_v4(
                        &q, 1, 16, &q_store, relation, false);
                emit("Q", &q, 1, q_result, q_store, 1);

                Fl4512SpacetimeTrackStoreV1 o_store = {};
                BeginTestFrame(&o_store, 1, 11);
                Fl4512SequenceNodeInputV2 o[2] = {
                    node(200, 400, 5, &glyph100),
                    node(201, 400, 40, &glyph100),
                };
                Fl4512SpacetimeBuildResultV4 o_result =
                    build_v4(
                        o, 2, 16, &o_store, relation, false);
                emit("O", o, 2, o_result, o_store, 1);
                Fl4512SequenceCandidateV1 glyph200 = candidate(200);
                o[1].candidates = &glyph200;
                BeginTestFrame(&o_store, 2, 11);
                if (!o_store.Find(o[0].claim.feature_ref) ||
                    !o_store.Find(o[1].claim.feature_ref) ||
                    o_store.Find(o[0].claim.feature_ref) ==
                        o_store.Find(o[1].claim.feature_ref))
                    return 2;
                o_result = build_v4(
                    o, 2, 16, &o_store, relation, false);
                if (o_store.Find(o[0].claim.feature_ref)->
                        pending_nodes[0].glyph_id != 100 ||
                    o_store.Find(o[1].claim.feature_ref)->
                        pending_nodes[0].glyph_id != 200)
                    return 3;
                emit("O-second", o, 2, o_result, o_store, 2);
                BeginTestFrame(&o_store, 3, 11);
                o_result = build_v4(
                    o, 1, 16, &o_store, relation, false);
                BeginTestFrame(&o_store, 4, 11);
                (void)build_v4(
                    &dummy, 0, 16, &o_store, relation, false);
                BeginTestFrame(&o_store, 5, 11);
                const Fl4512SpacetimeBuildResultV4 o_retire_one =
                    build_v4(
                        &dummy, 0, 16, &o_store, relation, false);
                emit("O-retire-one", &dummy, 0,
                    o_retire_one, o_store, 5, 1);
                if (!o_store.Find(o[0].claim.feature_ref) ||
                    o_store.Find(o[1].claim.feature_ref))
                    return 4;
                BeginTestFrame(&o_store, 6, 11);
                const Fl4512SpacetimeBuildResultV4 o_retire_both =
                    build_v4(
                        &dummy, 0, 16, &o_store, relation, false);
                emit("O-retire-both", &dummy, 0,
                    o_retire_both, o_store, 6, 1);
                if (o_store.Find(o[0].claim.feature_ref) ||
                    o_store.Find(o[1].claim.feature_ref))
                    return 5;

                for (unsigned permutation = 0; permutation < 2; ++permutation)
                {
                    Fl4512SpacetimeTrackStoreV1 stable_store = {};
                    BeginTestFrame(
                        &stable_store, 1, 44);
                    Fl4512SequenceNodeInputV2 stable[2] = {
                        node(200, 400, 5, &glyph100),
                        node(201, 400, 40, &glyph200),
                    };
                    if (permutation) std::reverse(stable, stable + 2);
                    const Fl4512SpacetimeBuildResultV4 stable_result =
                        build_v4(
                            stable, 2, 16, &stable_store, relation, false);
                    emit(permutation ? "stable-key-permuted" : "stable-key",
                        stable, 2, stable_result, stable_store, 1);
                }

                for (unsigned permutation = 0; permutation < 2; ++permutation)
                {
                    Fl4512SpacetimeTrackStoreV1 u_store = {};
                    BeginTestFrame(&u_store, 1, 12);
                    Fl4512SequenceNodeInputV2 u[4] = {
                        node(200, 900, 5, &glyph100),
                        node(200, 900, 6, &glyph100),
                        node(201, 900, 7, &glyph100),
                        node(201, 900, 8, &glyph100),
                    };
                    u[1].claim.visibility_sheet = 2;
                    u[3].claim.path_closure_class = 2;
                    if (permutation) std::reverse(u, u + 4);
                    Fl4512SpacetimeBuildResultV4 u_result =
                        build_v4(
                            u, 4, 16, &u_store, relation, false);
                    emit(permutation ? "U-permuted" : "U",
                        u, 4, u_result, u_store, 1);
                }
                return 0;
            }
            """
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "c++", "-std=c++17", "-I", str(ROOT),
            "-I", str(ROOT / "engine"),
            # fl4683_scene_fact_handshake.h lives in platform/; supported builds
            # (build-web.sh, makefile_game) supply -Iplatform.
            "-I", str(ROOT / "platform"), str(source),
            str(ROOT / "apps/fl4512-runtime/engine/fl4512_glyph_transform_relation_v2.cpp"),
            str(ROOT / "apps/fl4512-runtime/engine/fl4512_profile_workspace.cpp"),
            str(ROOT / "engine/third_party/cjson/cJSON.c"),
            "-o", str(binary),
        ],
        check=True,
        cwd=ROOT,
    )
    return binary


def test_v3_persistent_record_canonical_jsonl_parity(tmp_path: Path) -> None:
    """V4 native BeginFrame/Solve/Commit bytes are canonical and deterministic."""
    binary = _compile_native_persistent_jsonl(tmp_path)
    native = subprocess.check_output([binary], cwd=ROOT)
    assert subprocess.check_output([binary], cwd=ROOT) == native
    rows = [json.loads(line) for line in native.splitlines()]
    assert native == b"".join(
        (
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        for row in rows
    )
    by_case = {row["case_id"]: row for row in rows}
    assert set(by_case) == {
        "positive", "lifecycle", "event", "extent",
        "closure-open", "closure-closed",
        "sheet-1", "sheet-2", "sheet-3", "sheet-4", "sheet-5", "sheet-6",
        "lifecycle-disconnected", "lifecycle-hold", "lifecycle-retire",
        "lifecycle-epoch-reset", "lifecycle-frame-regression",
        "age-base", "age-offset", "Q", "O", "O-second",
        "O-retire-one", "O-retire-both", "stable-key",
        "stable-key-permuted", "U", "U-permuted",
    }
    expected_fields = [
        "case_id", "components", "conflict_code",
        "effective_feature_refs", "encoding", "feature_ref_key",
        "final_tuple", "frame_ordinal", "input_valid", "merged_claims",
        "schema", "semantic_chain_id", "solved_nodes",
        "spacetime_failure", "store_projection", "structural_outcome",
        "suppressed_feature_ref_keys", "topology_event",
    ]
    assert all(list(row) == expected_fields for row in rows)
    assert by_case["event"]["topology_event"][0]["kind"] == 1
    assert by_case["event"]["topology_event"][0]["magnitude_measured"] is False
    assert by_case["event"]["topology_event"][0]["magnitude"] == 2**32 - 1
    assert by_case["event"]["solved_nodes"][0]["topology_cost"] == 0
    assert by_case["extent"]["merged_claims"][0]["extent"] == 131072
    assert by_case["closure-open"]["merged_claims"][0]["closure"] == 1
    assert by_case["closure-closed"]["merged_claims"][0]["closure"] == 2
    for sheet in range(1, 7):
        assert by_case[f"sheet-{sheet}"]["merged_claims"][0]["sheet"] == sheet

    promoted = by_case["lifecycle"]["store_projection"]["records"][0]
    assert len(promoted["previous_nodes"]) == 1
    assert by_case["lifecycle-disconnected"]["components"] == 1
    disconnected_nodes = by_case["lifecycle-disconnected"]["solved_nodes"]
    assert [node["node_index"] for node in disconnected_nodes] == [0, 1]
    assert [node["u_q16"] for node in disconnected_nodes] == sorted(
        node["u_q16"] for node in disconnected_nodes
    )
    assert (
        by_case["lifecycle-disconnected"]["store_projection"][
            "pending_node_count"
        ]
        == 2
    )
    assert by_case["lifecycle-hold"]["store_projection"][
        "held_track_count"
    ] == 1
    assert by_case["lifecycle-hold"]["store_projection"]["records"][0][
        "last_frame_age"
    ] == 2
    assert by_case["lifecycle-retire"]["store_projection"][
        "retired_track_count"
    ] == 1
    assert by_case["lifecycle-retire"]["store_projection"][
        "record_count"
    ] == 0
    assert by_case["lifecycle-epoch-reset"]["store_projection"][
        "epoch_changed"
    ] is True
    assert by_case["lifecycle-frame-regression"]["store_projection"][
        "frame_regressed"
    ] is True
    assert (
        by_case["age-base"]["store_projection"]
        == by_case["age-offset"]["store_projection"]
    )

    assert by_case["Q"]["store_projection"]["commit_count"] == 0
    assert by_case["Q"]["store_projection"]["pending_node_count"] == 0
    assert by_case["Q"]["store_projection"]["record_count"] == 0
    assert len(by_case["Q"]["spacetime_failure"]) == 1
    assert by_case["Q"]["spacetime_failure"][0] == {
        "conflict_code": 5,
        "failure": 4,
        "feature_ref_key": by_case["Q"]["suppressed_feature_ref_keys"][0],
    }
    assert by_case["O"]["store_projection"]["record_count"] == 2
    assert [row["glyph_id"] for row in by_case["O-second"]["solved_nodes"]] == [
        100,
        200,
    ]
    assert len({
        row["spacetime_hash"] for row in by_case["O-second"]["solved_nodes"]
    }) == 2
    assert by_case["O-retire-one"]["store_projection"]["record_count"] == 1
    assert (
        by_case["O-retire-one"]["store_projection"]["records"][0][
            "feature_ref_key"
        ]["material_key"]
        == 200
    )
    assert by_case["O-retire-both"]["store_projection"]["record_count"] == 0
    assert by_case["stable-key"] == {
        **by_case["stable-key-permuted"],
        "case_id": "stable-key",
    }
    assert by_case["U"] == {**by_case["U-permuted"], "case_id": "U"}
    assert by_case["U"]["store_projection"]["commit_count"] == 0
    assert by_case["U"]["store_projection"]["pending_node_count"] == 0
    assert len(by_case["U"]["spacetime_failure"]) == 2
    assert sorted(
        row["conflict_code"] for row in by_case["U"]["spacetime_failure"]
    ) == [3, 4]
