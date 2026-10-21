#!/usr/bin/env python3
"""Source contract for FL-4512 RoleClaimV1 capability separation."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
RESOLVER = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text(encoding="utf-8")
PRESENTER = (ROOT / "platform/terminal_gl_present.cpp").read_text(encoding="utf-8")
WORLD_PASS = (ROOT / "engine/render/render_world_pass.cpp").read_text(encoding="utf-8")
WORLD_A3D = (ROOT / "engine/world_serialization_a3d.cpp").read_text(encoding="utf-8")
BUNDLE = (ROOT / "engine/render/render_resolve.cpp").read_text(encoding="utf-8")
FINAL_TUPLE_OWNER = (
    ROOT / "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp"
).read_text(encoding="utf-8")
EXACT_SELECTOR = (
    ROOT / "apps/fl4512-runtime/platform/fl4683_exact_cell_selector.cpp"
).read_text(encoding="utf-8")
FINAL_TUPLE_ADAPTER = (
    ROOT / "apps/fl4512-runtime/platform/fl4693_native_final_cell_adapter.cpp"
).read_text(encoding="utf-8")
SEAM_MODULE = (
    ROOT / "apps/fl4512-runtime/engine/fl4512_runtime_module.cpp"
).read_text(encoding="utf-8")
class StructuredPolicyOwnershipTest(unittest.TestCase):
    def test_retired_gpu_capability_mirror_is_absent(self) -> None:
        self.assertNotIn("Fl4260StageProfileRoutedGpuPools", RESOLVER)
        self.assertNotIn("Fl4260ProfileSelectGlyphProbe", RESOLVER)
        self.assertNotIn("mat_structured_policy", RESOLVER)

    def test_cpu_capability_bit_cannot_block_tonal_route(self) -> None:
        selector = RESOLVER.split("static uint32_t Fl4260ProfileSelectGlyph", 1)[1]
        selector = selector.split("GlyphId Fl4260GetRawSelection", 1)[0]
        self.assertNotIn("role_policy_has_any", selector)
        self.assertIn("capability metadata, never a per-cell claim", selector)
        self.assertIn("int ramp_idx = staged_ramp_idx", selector)

    def test_exact_selector_never_consumes_capability_bit_as_cell_claim(self) -> None:
        self.assertNotIn("harri_winner_fs", PRESENTER)
        self.assertNotIn("mat_structured_policy", EXACT_SELECTOR)
        self.assertNotIn("structured_policy", EXACT_SELECTOR)
        self.assertIn("Fl4683ExactCellSelectV1", EXACT_SELECTOR)

    def test_capability_metadata_stays_observational(self) -> None:
        self.assertNotIn("mat_structured_policy", RESOLVER)
        self.assertNotIn("mat_structured_policy", FINAL_TUPLE_OWNER)
        self.assertNotIn("mat_structured_policy", EXACT_SELECTOR)
        self.assertNotIn("mat_structured_policy", PRESENTER)
        self.assertNotIn("structured_policy_missing_fact", PRESENTER)

    def test_presenter_never_reconstructs_missing_fact(self) -> None:
        # Seam ownership: the committed-tuple finalizer moved delete-first to
        # the Current final-cell adapter; the shared presenter reaches it only
        # through the neutral commit slot.
        finalizer = FINAL_TUPLE_ADAPTER
        self.assertIn("Fl4693FinalTupleCommitFrameV1", finalizer)
        self.assertIn("tuples[index].final_gid", finalizer)
        self.assertIn("GLYPH_ID_ABSENT_MISSING_FACT", finalizer)
        self.assertNotIn("final_result.final_gid", finalizer)

        self.assertNotIn("gpu_sidecar_ready", PRESENTER)
        self.assertNotIn("harri_eligible_tex", PRESENTER)
        self.assertNotIn("mat_bucket_dropped", finalizer)

    def test_final_result_uses_logical_mesh_pool_address(self) -> None:
        carrier = FINAL_TUPLE_OWNER.split(
            "bool Fl4693ProfileUnaryPublishExactV1", 1
        )[1].split("bool Fl4693ProfileTupleFinalizeV1", 1)[0]
        self.assertIn("FL4512_POOL_ROUTE_MESH_BUNDLE", carrier)
        self.assertIn("current->pool_address.lane_id = 0", carrier)
        self.assertIn("current->absence_state", carrier)

    def test_final_result_carries_recomputable_role_owned_score_provenance(self) -> None:
        provenance = FINAL_TUPLE_OWNER.split(
            "bool Fl4693ProfileUnaryPublishExactV1", 1
        )[1].split("bool Fl4693ProfileTupleFinalizeV1", 1)[0]
        for token in (
            "FL4512_DESCRIPTOR_MASK8_TOPOLOGY",
            "FL4512_SCORER_U7_MASK",
            "target_mask8",
            "winner_mask8",
            "hamming_distance",
            "endpoint_delta",
            "junction_delta",
            "score_num",
            "__builtin_popcountll",
        ):
            self.assertIn(token, provenance)
        self.assertIn("Fl4683ExactCellCandidatesV1", provenance)
        self.assertNotIn("descriptor_version = 1; // fl4548.describe_cell.v1/global6", PRESENTER)

    def test_descriptor_version_one_is_unambiguous_within_the_role_owned_kind(self) -> None:
        """Pin the four v1 assignments to their non-overlapping result owners.

        ``descriptor_version`` is intentionally local to ``descriptor_kind``.
        This source contract rejects the old global6 fallback shape: a new role
        must select its descriptor kind explicitly before version 1 is written.
        """
        provenance = FINAL_TUPLE_OWNER.split(
            "bool Fl4693ProfileUnaryPublishExactV1", 1
        )[1].split("bool Fl4693ProfileTupleFinalizeV1", 1)[0]
        descriptor_dispatch = provenance.split(
            "if (outcome == FL4683_EXACT_CELL_OUTCOME_SELECTED)", 1
        )[1].split("current->descriptor_version = 1u", 1)[0]
        self.assertIn(
            "current->descriptor_kind = FL4512_DESCRIPTOR_PRIMITIVE_FIELD",
            descriptor_dispatch,
        )
        self.assertIn(
            "current->scorer_kind = FL4512_SCORER_PRIMITIVE_FIELD",
            descriptor_dispatch,
        )
        self.assertIn(
            "current->descriptor_kind = FL4512_DESCRIPTOR_GLOBAL6",
            descriptor_dispatch,
        )
        self.assertIn("current->descriptor_version = 1", provenance)
        self.assertIn("current->scorer_version = 2", descriptor_dispatch)
        self.assertIn(
            "current->scorer_kind = FL4512_SCORER_SHAPE6_WEIGHTED_L2",
            descriptor_dispatch,
        )
        self.assertIn(
            "current->descriptor_kind = FL4512_DESCRIPTOR_MASK8_TOPOLOGY",
            descriptor_dispatch,
        )
        self.assertIn("current->scorer_kind = FL4512_SCORER_U7_MASK", descriptor_dispatch)
        self.assertIn("current->target_mask8", provenance)
        self.assertIn("current->score_num", provenance)

    def test_trace_uses_descriptor_kind_plus_version_not_a_global_version_label(self) -> None:
        """Readback provenance must serialize the role-owned pair intact."""
        # Seam ownership: descriptor/scorer id tables moved with the seam
        # module; the shared presenter carries no id table.
        descriptor_ids = SEAM_MODULE.split("static const char* Fl4512DescriptorIdV1", 1)[1]
        descriptor_ids = descriptor_ids.split("static const char* Fl4512ScorerIdV1", 1)[0]
        for kind, label in (
            ("FL4512_DESCRIPTOR_GLOBAL6", "fl4260.global6.v1"),
            ("FL4512_DESCRIPTOR_MASK8_TOPOLOGY", "fl4512.mask8_topology.v1"),
            ("FL4512_DESCRIPTOR_PRIMITIVE_FIELD", "fl4512.primitive_field.v1"),
        ):
            self.assertIn(kind, descriptor_ids)
            self.assertIn(label, descriptor_ids)
        self.assertIn(
            "Fl4512DescriptorIdV1(final_result.descriptor_kind, final_result.descriptor_version)",
            SEAM_MODULE,
        )
        self.assertIn(
            "Fl4512DescriptorIdV1(trace_result.descriptor_kind, trace_result.descriptor_version)",
            SEAM_MODULE,
        )

    def test_bridge_and_trace_emit_v3_scorer_provenance(self) -> None:
        # Seam ownership: bridge/trace emission moved with the seam module.
        self.assertIn('\\"scorer_id\\":\\"%s\\"', SEAM_MODULE)
        self.assertIn("fl4548.termpp.cell_trace.v7", SEAM_MODULE)
        self.assertIn('\\"H\\":%u,\\"dE\\":%u,\\"dJ\\":%u', SEAM_MODULE)
        self.assertIn("mesh_bundle[0]", SEAM_MODULE)

    def test_mesh_identity_is_persisted_and_transform_independent(self) -> None:
        uid_fn = WORLD_PASS.split("static uint64_t RenderMeshInstanceUid", 1)[1]
        uid_fn = uid_fn.split("static uint32_t RenderStablePrimitiveId", 1)[0]
        self.assertIn("GetInstPersistentUid(inst)", uid_fn)
        self.assertNotIn("tm", uid_fn)
        self.assertNotIn("GetInstStoryID", uid_fn)
        self.assertIn("A3dWriteU64LE(f, GetInstPersistentUid(i))", WORLD_A3D)
        self.assertIn("A3dReadU64LE(f, &persistent_uid)", WORLD_A3D)

    def test_bundle_identity_contains_the_full_owner_key(self) -> None:
        # Seam ownership: the bundle builder moved with the seam module
        # (Fl4512SeamBuildProjectedFeatureBundle); the shared resolver keeps
        # no builder. Producer registration moved with it.
        builder = SEAM_MODULE.split("static bool Fl4512SeamBuildProjectedFeatureBundle", 1)[1]
        builder = builder.split("static void Fl4512SeamResolvePrePassV1", 1)[0]
        for field in (
            "bundle_id", "bundle_state_hash",
            "semantic_chain_hash_by_kind", "kind_hash_by_kind",
            "feature_kind_mask", "child_surface",
            "child_primitive_id", "child_local_feature_id",
            "semantic_segment_id",
        ):
            self.assertIn(field, builder)
        self.assertIn("FL4512_SEAM_BUNDLE_REASON_CAPACITY_EXCEEDED", SEAM_MODULE)
        producer = SEAM_MODULE.split("static void Fl4512RegisterProjectedTriangle", 1)[1]
        producer = producer.split("static void Fl4512ProduceVisibleBoundarySilhouetteLatticeV1", 1)[0]
        self.assertIn("surface.world_generation = r->fl4208_world_gen", producer)
        # FL-4512 U2 delete-first: truncated u64 surface_id = mesh_instance_uid is gone.
        self.assertNotIn("f->surface_id = r->mesh_instance_uid", producer)
        self.assertIn("Fl4208MeshOwnerHashV1", producer)
        self.assertIn("f->surface = surface", producer)

    def test_exact_selector_owns_role_and_absence_result(self) -> None:
        self.assertNotIn("harri_winner_fs", PRESENTER)
        self.assertIn("FL4683_EXACT_CELL_OUTCOME_SELECTED", EXACT_SELECTOR)
        self.assertIn("FL4683_EXACT_CELL_OUTCOME_DECLARED_ABSENCE", EXACT_SELECTOR)
        self.assertIn("FL4683_EXACT_CELL_OUTCOME_REJECTED", EXACT_SELECTOR)
        self.assertIn("Fl4693ProfileUnaryPublishExactV1", RESOLVER)


if __name__ == "__main__":
    unittest.main()
