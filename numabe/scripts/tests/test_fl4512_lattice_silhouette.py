"""FL-4512 U2 — the visible-boundary silhouette lattice producer.

This suite covers the first U2 source-semantics cut: kind-1 silhouette records
are produced by walking the finished visibility lattice, not by promoting
projected triangle-edge candidates with a rounded near/far probe.

Two kinds of check live here and they are deliberately different:

* Source-contract checks read the production translation unit and prove the
  deleted owner is physically gone. Deletion is the point of the cut, and a
  suite that only executed the new owner could stay green with the old probe
  still sitting beside it.

* The native harness compiles and RUNS the production entry point over
  synthetic lattices. Executing matters because source text can stay intact
  after a writer call has been disconnected.

Neither kind makes a visual claim. Operator multimodal review remains the only
visual acceptance surface, and nothing here launches the product.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "scripts/tests/fl4512_lattice_silhouette_harness.cpp"
WORLD_PASS = ROOT / "engine/render/render_world_pass.cpp"
# Seam ownership: the lattice producer moved with the seam module; the
# shared world pass holds no producer body.
SEAM_MODULE = ROOT / "apps/fl4512-runtime/engine/fl4512_runtime_module.cpp"

# The seven production and schema surfaces that carried the retired
# topology-candidate promotion observation end to end: the producer, renderer
# storage plus the bridge copy mirror, the bridge record, the TERM++ trace
# serializer, the editor state-snapshot projection, and the snapshot schema.
RETIRED_OWNER_SURFACES = (
    "engine/render/render_world_pass.cpp",
    "engine/render/render_internal.h",
    "engine/render/render_frame_input.h",
    "platform/terminal_gl_present.cpp",
    "apps/asciicker-presenter/asciiid.cpp",
    "schemas/asciiid.state_snapshot.v1.json",
    "scripts/tests/fl4512_silhouette_visibility_owner_harness.cpp",
)

# Names that only ever existed to carry the retired promotion decision.
RETIRED_OBSERVATION_SYMBOLS = (
    "Fl4512SilhouettePromotionDecisionV1",
    "Fl4652BridgeSilhouettePromotionDecisionV1",
    "Fl4512SilhouetteOppositeCauseV1",
    "Fl4512SilhouetteTerminalCauseV1",
    "FL4512_SILHOUETTE_OPPOSITE",
    "FL4512_SILHOUETTE_TERMINAL",
    "silhouette_promotion_observation_valid",
    "silhouette_topology_candidate_count",
    "silhouette_promotion_decision_count",
    "silhouette_promotion_decision_overflow",
    "silhouette_promotion_decision_capacity",
    "silhouette_promotion_decisions",
    "state_snapshot_bridge_promotion_count_mismatch",
    "state_snapshot_bridge_promotion_row_invalid",
    "state_snapshot_bridge_promotion_bounds_invalid",
)

# Every identifier that existed only to serve the deleted projected-edge probe.
# Each one is a distinct piece of the old decision: the promoter itself, its
# side/pair classifier, its trace-row writer, its sample-stepping cap, and the
# authored admission knobs (probe radii, step, and support thresholds) that let
# a profile decide the outline before the visibility data was consulted.
RETIRED_PROBE_SYMBOLS = (
    "Fl4512PromoteScreenExteriorSilhouettesV1",
    "Fl4512ClassifySilhouetteSideV1",
    "Fl4512EvaluateSilhouettePairV1",
    "Fl4512IncrementOppositeCauseV1",
    "Fl4512CanonicalEndpointsV1",
    "Fl4512SilhouetteSideV1",
    "Fl4512SilhouettePairV1",
    "kFl4512SilhouetteUniqueSampleCapV1",
    "Fl4512IntegerSqrtV1",
    "near_radius_q8_8",
    "far_radius_q8_8",
    "sample_step_q8_8",
    "min_unique_support_samples",
    "support_fraction_min_q0_16",
    "singleton_requires_two_radii",
    "near_strong_pair_count",
    "far_strong_pair_count",
    "evaluate_radius",
)


def _strip_comments(source: str) -> str:
    """Return `source` with C and C++ comments blanked out.

    The deletion checks below are about CODE. The history of what was deleted,
    and why, legitimately stays in the comments of the replacement owner, so
    matching raw file text would flag the explanation as if it were the thing
    it explains. Comments are replaced by spaces rather than removed so that
    the offsets used by the ordering assertions stay meaningful.
    """

    out = []
    index = 0
    length = len(source)
    while index < length:
        if source.startswith("//", index):
            end = source.find("\n", index)
            end = length if end < 0 else end
            out.append(" " * (end - index))
            index = end
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            end = length if end < 0 else end + 2
            out.append("".join(
                character if character == "\n" else " "
                for character in source[index:end]
            ))
            index = end
        else:
            out.append(source[index])
            index += 1
    return "".join(out)


def test_projected_edge_probe_owner_is_physically_deleted() -> None:
    """The old promoter and every probe-only helper must be gone from source."""

    code = _strip_comments(WORLD_PASS.read_text())
    for symbol in RETIRED_PROBE_SYMBOLS:
        assert symbol not in code, f"retired probe symbol still present: {symbol}"


def test_retired_promotion_observation_owner_is_gone_end_to_end() -> None:
    """Zero retired promotion symbols across all seven owning surfaces.

    Deleting the producer while leaving its storage, bridge mirror, serializer
    keys, snapshot fields, or schema properties behind would leave a dead
    observation surface that still looks like a live contract to a consumer.
    The fields are deleted, not zeroed and not relabelled.
    """

    for relative in RETIRED_OWNER_SURFACES:
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        code = _strip_comments(text) if path.suffix in {".cpp", ".h"} else text
        for symbol in RETIRED_OBSERVATION_SYMBOLS:
            assert symbol not in code, f"{relative} still carries {symbol}"


def test_lattice_producer_is_the_single_kind_one_writer() -> None:
    """One producer, called from the finalizer, after topology classification.

    The ordering assertion matters: the material-boundary fanout regroups
    existing kind-2 edges into kind 3, so the lattice producer runs after it and
    therefore cannot perturb the kind-2/3/4 semantics this cut must preserve.
    """

    code = _strip_comments(SEAM_MODULE.read_text())
    producer = "Fl4512ProduceVisibleBoundarySilhouetteLatticeV1"
    # Exactly one forward declaration, one definition, one call site.
    assert code.count(producer) == 3, code.count(producer)
    assert f"static void {producer}(Fl4512RuntimeStateV1* st, Renderer* r);" in code
    assert f"static void {producer}(Fl4512RuntimeStateV1* st, Renderer* r)\n{{" in code
    assert f"{producer}(st, r);" in code

    # The call is inside the finalizer and after the material-boundary fanout.
    finalizer = code.index("static void Fl4512World_FinalizeProjectedFeatures")
    fanout = code.index("edges[i]->feature_kind = 3;", finalizer)
    call = code.index(f"{producer}(st, r);", finalizer)
    active_stroke = code.index("Fl4512FinalizeActiveStrokeFrameV2(st, r);", finalizer)
    assert finalizer < fanout < call < active_stroke
    body = code[code.index(f"static void {producer}(Fl4512RuntimeStateV1* st, Renderer* r)\n{{") :]
    body = body[: body.index("\nstatic void Fl4512ClassifyProjectedFeatures")]

    # Kind 1 is written onto the producer's own fresh record and nowhere else.
    assert code.count("feature_kind = 1") == 1
    assert "record.feature_kind = 1;" in code

    # The producer must not resurrect the retired topology-candidate field.
    assert "silhouette_candidate" not in body


def test_keys_come_from_the_paper_algorithms_not_a_second_one() -> None:
    """All three published keys reuse the existing stable-key owners.

    The rework exists because the boundary world key was being built in the
    object frame by a private hash. The fix is to recover the instance
    transform and call the SAME world-edge key algorithm base edges call, not
    to add a boundary-specific variant.
    """

    code = _strip_comments(SEAM_MODULE.read_text())
    start = code.index(
        "static void Fl4512ProduceVisibleBoundarySilhouetteLatticeV1(Fl4512RuntimeStateV1* st, Renderer* r)\n{"
    )
    body = code[start:]
    body = body[: body.index("\nstatic void Fl4512ClassifyProjectedFeatures")]
    assert "Fl4512StableWorldEdgeKey(" in body
    assert "Fl4512StableEdgeKey(" in body
    assert "Fl4512StableProjectedEdgeKey(" in body
    # The private object-frame hash that produced the wrong key is gone.
    assert "Fl4512LatticeStableKeyV1" not in code
    # The transform is recovered from the renderer's own reprojection rows and
    # a boundary without one fails closed instead of falling back.
    assert "Fl4512FindLatticeInstanceTransformV2(" in body
    assert "if (!instance_transform)" in body


def test_surface_identity_is_chain_level_and_anchors_are_child_facts() -> None:
    """One segment must not publish one endpoint's anchor as its surface."""

    code = _strip_comments(SEAM_MODULE.read_text())
    assert "record.surface.anchor_u_q = 0;" in code
    assert "record.surface.anchor_v_q = 0;" in code
    assert "record.surface.anchor_u_q = mesh.anchor_u_q;" not in code
    assert "record.surface.anchor_v_q = mesh.anchor_v_q;" not in code
    # The depth-winning anchors are retained, scoped to the boundary record.
    assert "record.lattice_sample_anchor_u_q = mesh.anchor_u_q;" in code
    assert "record.lattice_sample_anchor_v_q = mesh.anchor_v_q;" in code
    assert "record.lattice_sample_anchor_valid = 1;" in code


def test_no_probe_fallback_or_second_painter_in_the_producer() -> None:
    """Architecture guards, read straight off the source.

    A triangle-edge probe fallback, a runtime glyph lookup, or a progression
    stage mask inside this producer would each reintroduce a second owner for
    the same fact.
    """

    code = _strip_comments(SEAM_MODULE.read_text())
    start = code.index(
        "static void Fl4512ProduceVisibleBoundarySilhouetteLatticeV1(Fl4512RuntimeStateV1* st, Renderer* r)\n{"
    )
    body = code[start:]
    end = body.index("\nstatic void Fl4512ClassifyProjectedFeatures")
    body = body[:end]
    for forbidden in (
        "GlyphId",
        "glyph_id",
        "Fl4512PhaseSAdmissionForProfileV1",
        "silhouette_candidate",
    ):
        assert forbidden not in body, f"producer body must not contain {forbidden}"


def test_native_lattice_producer_executes_every_boundary_rule(tmp_path: Path) -> None:
    """Compile and run the production owner over synthetic lattices.

    The harness covers, in order: mesh against non-mesh with exact horizontal
    and vertical half-lattice geometry; the frame boundary treated as a
    non-mesh neighbour; two distinct visible owners each emitting their own
    record at a shared seam; same-owner adjacency rejected; an unusable
    identity record failing the shared seam shut; a colour/identity material
    disagreement failing that seam shut; missing primitive authority failing
    closed; a complete triangle retaining primitive authority when shared or
    coplanar base records are unobserved and reserved; a degenerate projected
    triangle failing closed; publication order unchanged across twelve input
    permutations; capacity failure publishing nothing at all; and an absent
    lattice producing nothing. Endpoint reconstruction is checked inside every
    case, because the fixture triangle is built so the object-local answer must
    be exactly twice the projected one.
    """

    executable = tmp_path / "fl4512_lattice_silhouette_harness"
    compile_result = subprocess.run(
        [
            "c++",
            "-std=c++17",
            "-O2",
            "-fsanitize=address,undefined",
            "-fno-omit-frame-pointer",
            "-DFL4512_LATTICE_SILHOUETTE_TEST_SEAM",
            "-ffunction-sections",
            "-fdata-sections",
            "-I",
            str(ROOT),
            "-I",
            str(ROOT / "engine"),
            "-I",
            str(ROOT / "engine/render"),
            # actor_visual_profile.h shares the protocol structures used by the
            # production world-pass translation unit.
            "-I",
            str(ROOT / "server"),
            str(HARNESS),
            # Seam ownership: the lattice producer + finalizer live in the
            # seam module, driven behind the neutral slot; the bridge TU
            # carries the shared bridge globals.
            str(ROOT / "apps/fl4512-runtime/engine/fl4512_runtime_module.cpp"),
            str(ROOT / "apps/fl4512-runtime/engine/fl4131_harri_gpu_bridge.cpp"),
            str(ROOT / "apps/fl4512-runtime/engine/fl4512_active_stroke_owner.cpp"),
            str(ROOT / "apps/fl4512-runtime/engine/fl4512_mesh_feature_writes.cpp"),
            str(ROOT / "engine/glyph_coverage_lookup.cpp"),
            str(ROOT / "apps/fl4512-runtime/engine/fl4512_sprite_measure_blit.cpp"),
            str(ROOT / "apps/fl4512-runtime/engine/fl4131_pre_glyph_scene.cpp"),
            str(ROOT / "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp"),
            str(ROOT / "apps/fl4512-runtime/engine/fl4512_scene_fact_builder.cpp"),
            "-Wl,-dead_strip",
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
    assert run_result.returncode == 0, (
        f"stdout:\n{run_result.stdout}\nstderr:\n{run_result.stderr}"
    )
    assert run_result.stdout.strip() == (
        "lattice-silhouette:"
        "nonmesh:4+outside:16+distinct-seam:2+same-owner:6+"
        "invalid-owner:3+material-mismatch:3+missing-primitive:4+"
        "inactive-base-edges:4+degenerate:4+permutations:12+"
        "capacity-atomic:1+no-lattice:1+"
        "world-key-join:1+chain-identity:2+no-transform:4"
    )
