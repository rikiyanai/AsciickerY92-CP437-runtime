"""FL-4512 visibility-sheet ownership companion diagnostics.

These tests compile a native harness that executes the production mapping,
depth-winning mesh writer, and projected-feature registration path. Executing
the owners matters because source-text assertions can remain green after a
writer call has been disconnected. The suite never launches the product and
makes no visual-acceptance claim; operator multimodal review remains the only
visual acceptance surface.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "scripts/tests/fl4512_silhouette_visibility_owner_harness.cpp"
POOL = ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json"
RESOLVER = ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp"
PROFILE = ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json"


def _canonical_sha(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def test_phase_s_admission_transaction_is_hash_closed_and_complete() -> None:
    """Pin the live Active Stroke topology transaction plus its load boundary."""

    pool = json.loads(POOL.read_text())
    structural_rows = []
    for material in pool["materials"]:
        active_stroke = material["active_stroke_topology"]
        if active_stroke is None:
            continue
        assert active_stroke["content_sha256"] == _canonical_sha(
            {
                key: value
                for key, value in active_stroke.items()
                if key != "content_sha256"
            }
        )
        structural_rows.append(
            {
                "active_stroke_topology_sha256": active_stroke["content_sha256"],
                "material_key": material["material_key"],
                "profile_namespace": material["profile_namespace"],
            }
        )
    structural_rows.sort(
        key=lambda row: (row["profile_namespace"], row["material_key"])
    )
    assert structural_rows
    assert pool["header"]["active_stroke_topology_registry_sha256"] == (
        _canonical_sha(structural_rows)
    )

    # These pins guard ownership and ordering, not spelling alone: the native
    # harness below exercises the admitted policy through the renderer.
    source = RESOLVER.read_text()
    assert "Fl4512LoadActiveStrokeTopologyAdmissionInto(" in source
    assert "active_stroke_topology_registry_hash_mismatch" in source
    assert "active_stroke_topology_registry_hash_mismatch" in source
    assert source.index("Fl4512LoadActiveStrokeTopologyAdmissionInto(") < source.index(
        "memcpy(g_fl4260_v1_profiles, prepared->profiles"
    )


def test_native_visibility_sheet_owners_execute_fail_closed_and_stamp(
    tmp_path: Path,
) -> None:
    """Execute the mapper plus both production raster ownership branches.

    The harness checks all six valid sheet tuples, the invalid-published S3
    tuple, both raster winners, all projected edges, and the native L2
    promotion matrix. The L2 matrix executes direct/reflection/S3 separation,
    fail-closed parity, owner/depth/normal discontinuities, frame-edge and
    singleton evidence, support/capacity thresholds, the independent trace
    denominator, overflow, and permutation stability.
    """

    executable = tmp_path / "fl4512_silhouette_visibility_owner_harness"
    # Compile the actual mesh forwarders with the actual product module. The
    # fixture publishes only the mesh slots it exercises; it does not recreate
    # raster, visibility, topology, or observer policy in a mock renderer.
    adapter = (ROOT / "apps/fl4512-runtime/engine/fl4512_neutral_adapter.cpp").read_text()
    forwarders = adapter[adapter.index("void NeutralRegisterProjectedTriangle("):
                         adapter.index("uint8_t NeutralMeasureDispatchSurface(")]
    product_unit = tmp_path / "product_mesh_unit.cpp"
    product_unit.write_text('''
#include "apps/fl4512-runtime/engine/fl4512_runtime_module.cpp"
static Fl4512RuntimeStateV1* ProdState(AsciickerProductStateV1* state) {
    return static_cast<Fl4512RuntimeStateV1*>(static_cast<void*>(state));
}
static const Fl4512RuntimeHooksV1* ProdHooks() { return Fl4512RuntimeHooksCached(); }
''' + forwarders + '''
void AsciickerProductInstallV1() {
    Fl4512CurrentInstallV1();
    static RendererProductV1 mesh = {};
    mesh.version = 1;
    mesh.register_projected_triangle = NeutralRegisterProjectedTriangle;
    mesh.mesh_submit_begin = NeutralMeshSubmitBegin;
    mesh.mesh_submit_end = NeutralMeshSubmitEnd;
    mesh.capture_stroke_reprojection = NeutralCaptureStrokeReprojection;
    mesh.mesh_tone_begin_face = NeutralMeshToneBeginFace;
    mesh.mesh_tone_write_sample = NeutralMeshToneWriteSample;
    mesh.mesh_tone_compute_vertex = NeutralMeshToneComputeVertex;
    AsciickerProductPortsV1 ports = {};
    ports.version = 1;
    ports.renderer = &mesh;
    AsciickerProductSetPortsV1(&ports);
}
bool Fl4512TestCopyObserverCarriers(const Fl4512RuntimeStateV1* state,
    const Renderer* renderer, Fl4131HarriGpuBridge* bridge) {
    return Fl4652CopyRendererObserverCarriersV1(state, renderer, bridge);
}
''')
    compile_result = subprocess.run(
        [
            "c++",
            "-std=c++17",
            "-O2",
            "-fsanitize=address,undefined",
            "-fno-omit-frame-pointer",
            "-DFL4652_SUGGESTIVE_SHEET_TEST_SEAM",
            "-ffunction-sections",
            "-fdata-sections",
            "-I",
            str(ROOT),
            "-I",
            str(ROOT / "engine"),
            "-I",
            str(ROOT / "engine/render"),
            # actor_visual_profile.h shares the protocol structures used by
            # the production world-pass translation unit.
            "-I",
            str(ROOT / "server"),
            # fl4683_scene_fact_handshake.h lives in platform/; supported builds
            # (build-web.sh, makefile_game) supply -Iplatform.
            "-I",
            str(ROOT / "platform"),
            str(HARNESS),
            str(product_unit),
            str(ROOT / "engine/render/render_world_pass.cpp"),
            str(ROOT / "apps/fl4512-runtime/engine/fl4512_active_stroke_owner.cpp"),
            str(ROOT / "apps/fl4512-runtime/engine/fl4131_harri_gpu_bridge.cpp"),
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
    assert run_result.returncode == 0, run_result.stderr
    assert (
        run_result.stdout.strip()
        == (
            "visibility-sheet:11-map+2-raster+3-reject+"
            "suggestive-sheet:1+active-stroke-carrier:1"
        )
    )


def test_compiled_loader_rejects_mutated_policy_before_profile_becomes_live(
    tmp_path: Path,
) -> None:
    """Execute the production loader with a hash-stale authored policy byte."""

    workspace = tmp_path / "glyph-workspace"
    (workspace / "profiles").mkdir(parents=True)
    (workspace / "generated").mkdir()
    shutil.copyfile(
        PROFILE,
        workspace / "profiles/material_rendering_profiles.v1.json",
    )
    artifact = json.loads(POOL.read_text(encoding="utf-8"))
    mesh_material = next(
        material
        for material in artifact["materials"]
        if material["profile_namespace"] == "mesh"
        and material["material_key"] == 200
    )
    mesh_material["active_stroke_topology"]["relaxation_iterations"] += 1
    # Deliberately preserve every block, registry, and artifact hash. The
    # production transaction must reject this byte mutation before copying its
    # detached staging table into the live profile registry.
    (workspace / "generated/fl4512.compiled_pool.v1.json").write_text(
        json.dumps(artifact, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    cjson_object = tmp_path / "cJSON.o"
    cjson_compile = subprocess.run(
        [
            "cc",
            "-ffunction-sections",
            "-fdata-sections",
            "-I",
            str(ROOT / "engine/third_party/cjson"),
            "-c",
            str(ROOT / "engine/third_party/cjson/cJSON.c"),
            "-o",
            str(cjson_object),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert cjson_compile.returncode == 0, cjson_compile.stderr
    executable = tmp_path / "fl4512_compiled_loader_mutation_probe"
    loader_compile = subprocess.run(
        [
            "c++",
            "-std=c++17",
            "-O2",
            "-fsanitize=address,undefined",
            "-fno-omit-frame-pointer",
            "-ffunction-sections",
            "-fdata-sections",
            "-DFL4512_COMPILED_LOADER_MUTATION_PROBE=1",
            "-I",
            str(ROOT),
            "-I",
            str(ROOT / "engine"),
            "-I",
            str(ROOT / "engine/render"),
            "-I",
            str(ROOT / "server"),
            # fl4683_scene_fact_handshake.h lives in platform/; supported builds
            # (build-web.sh, makefile_game) supply -Iplatform.
            "-I",
            str(ROOT / "platform"),
            str(HARNESS),
            str(RESOLVER),
            str(ROOT / "apps/fl4512-runtime/engine/fl4512_profile_workspace.cpp"),
            str(ROOT / "apps/fl4512-runtime/engine/fl4131_harri_resolver.cpp"),
            str(cjson_object),
            "-Wl,-dead_strip",
            "-o",
            str(executable),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert loader_compile.returncode == 0, loader_compile.stderr
    run_result = subprocess.run(
        [str(executable), str(workspace), str(ROOT / "assets/glyphs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert run_result.returncode == 0, (
        f"stdout:\n{run_result.stdout}\nstderr:\n{run_result.stderr}"
    )
    assert run_result.stdout.strip().endswith(
        "compiled-loader:policy-hash-rejected+profile-not-live"
    )
    # Reuse the same real native transaction for a valid publish followed by a
    # rejected edit. Preparation must neither publish nor clear live authority.
    rejected_pool = workspace / "generated/rejected-pool.json"
    shutil.copyfile(workspace / "generated/fl4512.compiled_pool.v1.json", rejected_pool)
    shutil.copyfile(POOL, workspace / "generated/fl4512.compiled_pool.v1.json")
    next_pool = json.loads(POOL.read_text())
    next_pool["header"]["pool_revision"] += 1
    del next_pool["artifact_sha256"]
    next_pool["artifact_sha256"] = _canonical_sha(next_pool)
    next_pool_path = workspace / "generated/next-pool.json"
    next_pool_path.write_text(json.dumps(next_pool, sort_keys=True, separators=(",", ":")))
    prepared_result = subprocess.run(
        [str(executable), str(workspace), str(ROOT / "assets/glyphs"),
         str(next_pool_path), str(rejected_pool)],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert prepared_result.returncode == 0, (
        f"stdout:\n{prepared_result.stdout}\nstderr:\n{prepared_result.stderr}"
    )
    assert prepared_result.stdout.strip().endswith(
        "compiled-loader:prepare-isolated+publish+rejected-edit-retains-live"
    )
