from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HEADER = ROOT / "engine/render/fl4512_structural_sequence_input.h"
INTERNAL = ROOT / "engine/render/render_internal.h"
WORLD = ROOT / "engine/render/render_world_pass.cpp"
RESOLVE = ROOT / "engine/render/render_resolve.cpp"
FRAME_INPUT = ROOT / "engine/render/render_frame_input.h"


def test_feature_ref_identity_is_value_owned_and_total(tmp_path: Path) -> None:
    source = tmp_path / "sequence_input_probe.cpp"
    binary = tmp_path / "sequence_input_probe"
    source.write_text(
        '#include "engine/render/fl4512_structural_sequence_input.h"\n'
        "int main() {\n"
        "  Fl4512FeatureRefV1 a = {};\n"
        "  a.surface.world_generation=1; a.surface.owner_kind=1; a.surface.owner_x=2; a.surface.owner_y=0; a.material_key=200;\n"
        "  a.provenance.producer_kind=FL4512_FEATURE_PRODUCER_MESH;\n"
        "  a.provenance.mesh.mesh_instance_uid=3; a.provenance.mesh.depth_owner=3;\n"
        "  a.dispatch_surface=4; a.role=5;\n"
        "  a.feature_kind=1; a.aspect_region_id=6; a.semantic_chain_id=7;\n"
        "  Fl4512FeatureRefV1 b = a;\n"
        "  if (!Fl4512FeatureRefEqualV1(a,b)) return 1;\n"
        "  b.semantic_chain_id++;\n"
        "  if (Fl4512FeatureRefEqualV1(a,b)) return 2;\n"
        "  if (!Fl4512FeatureRefLessV1(a,b)) return 3;\n"
        "  Fl4512SequenceCellClaimV2 claim = {};\n"
        "  claim.feature_ref=a; claim.clipped_mask8=1; claim.cell_index=42; claim.child_primitive_id=9;\n"
        "  return claim.feature_ref.material_key==200 ? 0 : 4;\n"
        "}\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["c++", "-std=c++17", "-I", str(ROOT), str(source), "-o", str(binary)],
        check=True,
        cwd=ROOT,
    )
    subprocess.run([str(binary)], check=True, cwd=ROOT)


def test_sequence_input_has_no_winner_and_one_coverage_owner() -> None:
    header = HEADER.read_text(encoding="utf-8")
    projected = (ROOT / "engine/render/projected_surface_feature.h").read_text(
        encoding="utf-8"
    )
    internal = INTERNAL.read_text(encoding="utf-8")
    world = WORLD.read_text(encoding="utf-8")
    resolve = RESOLVE.read_text(encoding="utf-8")
    assert "final_gid" not in header
    assert "glyph_id" not in header
    # No temporal storage field may enter the U2 carrier. Comments may explain
    # that boundary, so test the actual field-shaped spellings.
    assert "history_gid" not in header
    assert "temporal_" not in header
    assert "Fl4512SequenceCellClaimV2* fl4512_sequence_cell_claims" in internal
    assert "projected_endpoints_q24_8[0] = pv[a][0] * 256" in world
    assert resolve.count("ddx*ddx + ddy*ddy > 0.0200f") == 1
    assert "MeshProjectedFeature" not in resolve
    assert "Fl4512ProjectedSurfaceFeatureV1" in resolve
    assert "Fl4512AppendSequenceCellClaims" in resolve
    assert "Fl4512FeatureIdV1" in header
    assert "semantic_chain_id" in projected
    assert "uint64_t semantic_segment_id;" in header
    assert "uint64_t semantic_segment_id;" in internal
    assert "candidate.semantic_segment_id = f.semantic_segment_id;" in resolve
    assert "claim.semantic_segment_id = bundle.entries[i].semantic_segment_id;" in resolve
    assert "FL4512_BUNDLE_REASON_MISSING_SEGMENT_IDENTITY" in resolve
    assert "child_primitive_id" in header
    assert "Fl4512FinalizeActiveStrokeFrameV2(this)" in world


def test_glyph_transform_facts_cross_the_claim_abi_once_from_real_producers() -> None:
    header = HEADER.read_text(encoding="utf-8")
    internal = INTERNAL.read_text(encoding="utf-8")
    resolve = RESOLVE.read_text(encoding="utf-8")
    owner = (
        ROOT / "apps/fl4512-runtime/engine/fl4512_active_stroke_owner.cpp"
    ).read_text(encoding="utf-8")
    presenter = (
        ROOT / "platform/terminal_gl_present.cpp"
    ).read_text(encoding="utf-8")

    claim_v2 = header.split("struct Fl4512SequenceCellClaimV2", 1)[1].split(
        "};", 1
    )[0]
    contributor = header.split(
        "struct Fl4512SequenceSegmentContributorV1", 1
    )[1].split("};", 1)[0]
    assert claim_v2.count(
        "Fl4512GlyphTransformLiveFactsV2 glyph_transform_facts;"
    ) == 1
    assert contributor.count(
        "Fl4512GlyphTransformLiveFactsV2 glyph_transform_facts;"
    ) == 1
    assert internal.count(
        "Fl4512GlyphTransformLiveFactsV2 glyph_transform_facts;"
    ) == 1
    for field in (
        "transform.aspect_address",
        "transform.visible_support",
        "transform.expected_support",
        "transform.projected_tangent_present",
        "transform.prior_projected_anchor_q16_16",
        "transform.observed_projected_offset_q16_16",
        "transform.observed_projected_offset_present",
        "transform.pose_relation",
    ):
        assert field in owner
    assert "candidate.glyph_transform_facts = f.glyph_transform_facts;" in resolve
    assert "candidate.glyph_transform_facts.feature_ref = feature_ref;" in resolve
    assert "declared_topology_event_present" not in header
    assert "declared_topology_event_present" not in resolve
    assert "topology_event_kind" not in header.split(
        "struct Fl4512SequenceCellClaimV2", 1
    )[1].split("};", 1)[0]
    assert "Fl4512GlyphTransformLiveFactsV2" not in presenter
    assert "claim.glyph_transform_facts" not in presenter


def test_v2_merge_conflicts_are_typed_and_independent(tmp_path: Path) -> None:
    source = tmp_path / "sequence_merge_v2_probe.cpp"
    binary = tmp_path / "sequence_merge_v2_probe"
    source.write_text(
        '#include "engine/render/fl4512_structural_sequence_input.h"\n'
        "#include <vector>\n"
        "static Fl4512SequenceCellClaimV2 Claim(unsigned long long id) {\n"
        "  Fl4512SequenceCellClaimV2 c={}; c.feature_ref.surface.world_generation=1;\n"
        "  c.feature_ref.surface.world_generation=1; c.feature_ref.surface.owner_kind=1;\n"
        "  c.feature_ref.semantic_chain_id=id; c.clipped_mask8=1; c.cell_index=2;\n"
        "  c.semantic_segment_id=id;\n"
        "  c.semantic_chain_projected_extent_q16_16=65536; c.visibility_sheet=1;\n"
        "  c.path_closure_class=1; return c;\n"
        "}\n"
        "static int Expect(Fl4512SequenceCellClaimV2 a, Fl4512SequenceCellClaimV2 b,\n"
        "  Fl4512SequenceClaimConflictCodeV2 code) {\n"
        "  std::vector<const Fl4512SequenceCellClaimV2*> in={&a,&b};\n"
        "  Fl4512SequenceCellMergeResultV2 r=Fl4512MergeSequenceCellClaimsV2(in);\n"
        "  return r.conflicts.size()==1 && r.conflicts[0].code==code && r.merged_claims.empty() ? 0 : 1;\n"
        "}\n"
        "int main() {\n"
        "  Fl4512SequenceCellClaimV2 a=Claim(7), b=a;\n"
        "  b.semantic_chain_projected_extent_q16_16++; if(Expect(a,b,FL4512_SEQUENCE_CLAIM_CONFLICT_MAXIMAL_EXTENT)) return 1;\n"
        "  b=a; b.path_closure_class=2; if(Expect(a,b,FL4512_SEQUENCE_CLAIM_CONFLICT_CLOSURE_CLASS)) return 2;\n"
        "  b=a; b.visibility_sheet=2; if(Expect(a,b,FL4512_SEQUENCE_CLAIM_CONFLICT_VISIBILITY_SHEET)) return 3;\n"
        "  Fl4512SequenceCellClaimV2 independent=Claim(8);\n"
        "  b=a; b.visibility_sheet=2;\n"
        "  std::vector<const Fl4512SequenceCellClaimV2*> mixed={&a,&b,&independent};\n"
        "  Fl4512SequenceCellMergeResultV2 mr=Fl4512MergeSequenceCellClaimsV2(mixed);\n"
        "  if(mr.conflicts.size()!=1 || mr.merged_claims.size()!=1 || mr.merged_claims[0].feature_ref.semantic_chain_id!=8) return 4;\n"
        "  std::vector<const Fl4512SequenceCellClaimV2*> with_null={&independent,nullptr};\n"
        "  mr=Fl4512MergeSequenceCellClaimsV2(with_null);\n"
        "  if(mr.input_valid || mr.conflicts.size()!=1 || mr.conflicts[0].code!=FL4512_SEQUENCE_CLAIM_CONFLICT_NULL_CHILD) return 5;\n"
        "  a=Claim(9); b=a; a.child_primitive_id=2; a.clipped_mask8=1; a.chain_phase0_q16=20; a.chain_phase1_q16=30;\n"
        "  a.semantic_segment_id=91; b.child_primitive_id=1; b.semantic_segment_id=92; b.clipped_mask8=2; b.chain_phase0_q16=40; b.chain_phase1_q16=10;\n"
        "  std::vector<const Fl4512SequenceCellClaimV2*> forward={&a,&b}, reverse={&b,&a};\n"
        "  Fl4512SequenceCellMergeResultV2 f=Fl4512MergeSequenceCellClaimsV2(forward), r=Fl4512MergeSequenceCellClaimsV2(reverse);\n"
        "  if(f.merged_claims.size()!=1 || r.merged_claims.size()!=1) return 6;\n"
        "  if(f.merged_claims[0].child_primitive_id!=r.merged_claims[0].child_primitive_id || f.merged_claims[0].clipped_mask8!=r.merged_claims[0].clipped_mask8 || f.merged_claims[0].chain_phase0_q16!=r.merged_claims[0].chain_phase0_q16 || f.merged_claims[0].chain_phase1_q16!=r.merged_claims[0].chain_phase1_q16) return 7;\n"
        "  if(f.segment_contributor_sets.size()!=1 || r.segment_contributor_sets.size()!=1) return 8;\n"
        "  if(f.segment_contributor_sets[0].size()!=2 || r.segment_contributor_sets[0].size()!=2) return 9;\n"
        "  for(unsigned i=0;i<2;++i) {\n"
        "    const auto& fc=f.segment_contributor_sets[0][i]; const auto& rc=r.segment_contributor_sets[0][i];\n"
        "    if(fc.child_primitive_id!=rc.child_primitive_id || fc.child_local_feature_id!=rc.child_local_feature_id || fc.clipped_mask8!=rc.clipped_mask8 || fc.chain_phase0_q16!=rc.chain_phase0_q16 || fc.chain_phase1_q16!=rc.chain_phase1_q16) return 10;\n"
        "  }\n"
        "  if(f.segment_contributor_sets[0][0].child_primitive_id!=1 || f.segment_contributor_sets[0][1].child_primitive_id!=2) return 11;\n"
        "  a=Claim(10); b=a; std::vector<const Fl4512SequenceCellClaimV2*> duplicate={&a,&b};\n"
        "  Fl4512SequenceCellMergeResultV2 d=Fl4512MergeSequenceCellClaimsV2(duplicate);\n"
        "  if(d.conflicts.size()!=1 || d.conflicts[0].cell_index!=2 || d.conflicts[0].code!=FL4512_SEQUENCE_CLAIM_CONFLICT_DUPLICATE_SEGMENT || !d.merged_claims.empty() || !d.segment_contributor_sets.empty()) return 12;\n"
        "  a=Claim(11); b=a; b.cell_index=3; std::vector<const Fl4512SequenceCellClaimV2*> fanout={&b,&a};\n"
        "  Fl4512SequenceCellMergeResultV2 ffan=Fl4512MergeSequenceCellClaimsV2(fanout);\n"
        "  if(ffan.conflicts.size()!=0 || ffan.merged_claims.size()!=2 || ffan.segment_contributor_sets.size()!=2) return 13;\n"
        "  if(ffan.merged_claims[0].cell_index!=2 || ffan.merged_claims[1].cell_index!=3 || ffan.segment_contributor_sets[0].size()!=1 || ffan.segment_contributor_sets[1].size()!=1) return 14;\n"
        "  a=Claim(12); b=a; b.projected_endpoints_q24_8[0]++; std::vector<const Fl4512SequenceCellClaimV2*> collision={&a,&b};\n"
        "  Fl4512SequenceCellMergeResultV2 c=Fl4512MergeSequenceCellClaimsV2(collision);\n"
        "  if(c.conflicts.size()!=1 || c.conflicts[0].cell_index!=2 || c.conflicts[0].code!=FL4512_SEQUENCE_CLAIM_CONFLICT_SEGMENT_IDENTITY_COLLISION || !c.merged_claims.empty() || !c.segment_contributor_sets.empty()) return 15;\n"
        "  return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["c++", "-std=c++17", "-I", str(ROOT), str(source), "-o", str(binary)],
        check=True,
        cwd=ROOT,
    )
    subprocess.run([str(binary)], check=True, cwd=ROOT)


def test_v18_bridge_carriers_copy_exact_facts_and_fail_closed(
    tmp_path: Path,
) -> None:
    """Execute the pointer-free E2 copy and pin the per-kind U2 writes.

    This is a byte/structural companion diagnostic. It does not launch the
    product and makes no visual-acceptance claim.
    """

    resolve = RESOLVE.read_text(encoding="utf-8")
    frame_input = FRAME_INPUT.read_text(encoding="utf-8")
    assert resolve.count("extent_rejected_count_by_kind[kind_index]++") == 1
    assert resolve.count("extent_below_exit_count_by_kind[kind_index]++") == 1
    assert (
        "mesh_bundle_extent_rejected_count_by_kind[kind_idx] =" in resolve
    )
    assert (
        "mesh_bundle_extent_below_exit_count_by_kind[kind_idx] =" in resolve
    )
    assert "mesh_bundle_extent_rejected_count_by_kind[160*90 * 4]" in frame_input
    assert "mesh_bundle_extent_below_exit_count_by_kind[160*90 * 4]" in frame_input
    assert (
        resolve.count("mesh_bundle_extent_rejected_count_by_kind, 0,") == 1
    )
    assert (
        resolve.count("mesh_bundle_extent_below_exit_count_by_kind, 0,") == 1
    )

    source = tmp_path / "fl4652_bridge_carrier_probe.cpp"
    binary = tmp_path / "fl4652_bridge_carrier_probe"
    source.write_text(
        '#include "engine/render/render_internal.h"\n'
        "#include <cstring>\n"
        "#include <memory>\n"
        "int main() {\n"
        "  Renderer renderer;\n"
        "  std::memset(&renderer.fl4512_active_stroke_frame_observation,0,\n"
        "    sizeof(renderer.fl4512_active_stroke_frame_observation));\n"
        "  renderer.fl4512_active_stroke_frame_observation.input_sample_count=61;\n"
        "  renderer.fl4512_active_stroke_frame_observation.state_hash=67;\n"
        "  renderer.fl4512_active_stroke_frame_observation.failure=\n"
        "    FL4512_ACTIVE_STROKE_SNAKE_FAILURE_NONE_V2;\n"
        "  std::memset(&renderer.fl4512_active_stroke_lifecycle_observation,0,\n"
        "    sizeof(renderer.fl4512_active_stroke_lifecycle_observation));\n"
        "  renderer.fl4512_active_stroke_lifecycle_observation.store_frame=71;\n"
        "  renderer.fl4512_active_stroke_lifecycle_observation.world_generation=73;\n"
        "  renderer.fl4512_active_stroke_lifecycle_observation.active_snake_count=79;\n"
        '  std::strcpy(renderer.fl4512_active_stroke_lifecycle_observation.active_registry_sha256,"abc");\n'
        "  renderer.fl4512_active_stroke_store=\n"
        "    reinterpret_cast<Fl4512ActiveStrokeSnakeStoreV2*>(1);\n"
        "  renderer.fl4512_active_stroke_event_count=83;\n"
        "  renderer.fl4512_active_stroke_event_overflow=0;\n"
        "  renderer.mesh_visibility_sheet_failure_count=0;\n"
        "  renderer.mesh_visibility_sheet_last_failure=\n"
        "    RENDER_VISIBILITY_SHEET_FAILURE_NONE;\n"
        "  std::unique_ptr<Fl4131HarriGpuBridge> bridge(\n"
        "    new Fl4131HarriGpuBridge{});\n"
        "  if(!Fl4652CopyRendererObserverCarriersV1(&renderer,bridge.get())) return 1;\n"
        "  if(!bridge->fl4652_observer_carriers_valid) return 2;\n"
        "  if(bridge->fl4512_active_stroke_frame_observation.input_sample_count!=61 ||\n"
        "     bridge->fl4512_active_stroke_frame_observation.state_hash!=67 ||\n"
        "     bridge->fl4512_active_stroke_lifecycle_observation.store_frame!=71 ||\n"
        "     bridge->fl4512_active_stroke_lifecycle_observation.world_generation!=73 ||\n"
        "     bridge->fl4512_active_stroke_lifecycle_observation.active_snake_count!=79 ||\n"
        "     std::strcmp(bridge->fl4512_active_stroke_lifecycle_observation.active_registry_sha256,\"abc\")!=0 ||\n"
        "     bridge->fl4512_active_stroke_event_count!=83) return 5;\n"
        "  renderer.fl4512_active_stroke_store=nullptr;\n"
        "  if(Fl4652CopyRendererObserverCarriersV1(&renderer,bridge.get()) ||\n"
        "     bridge->fl4512_active_stroke_observation_valid) return 7;\n"
        "  renderer.fl4512_active_stroke_store=\n"
        "    reinterpret_cast<Fl4512ActiveStrokeSnakeStoreV2*>(1);\n"
        "  renderer.mesh_visibility_sheet_failure_count=1;\n"
        "  renderer.mesh_visibility_sheet_last_failure=\n"
        "    RENDER_VISIBILITY_SHEET_FAILURE_NONE;\n"
        "  if(Fl4652CopyRendererObserverCarriersV1(&renderer,bridge.get()) ||\n"
        "     bridge->mesh_visibility_sheet_observation_valid) return 8;\n"
        "  return 0;\n"
        "}\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            "c++",
            "-std=c++17",
            "-I",
            str(ROOT),
            "-I",
            str(ROOT / "engine"),
            "-I",
            str(ROOT / "engine/render"),
            "-I",
            str(ROOT / "server"),
            str(source),
            "-o",
            str(binary),
        ],
        check=True,
        cwd=ROOT,
    )
    subprocess.run([str(binary)], check=True, cwd=ROOT)
