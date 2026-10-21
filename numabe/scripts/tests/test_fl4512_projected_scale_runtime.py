from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json"
RESOLVER = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text()
WORLD = (ROOT / "engine/render/render_world_pass.cpp").read_text()
RESOLVE = (ROOT / "engine/render/render_resolve.cpp").read_text()
ACTIVE_STROKE_OWNER = (
    ROOT / "apps/fl4512-runtime/engine/fl4512_active_stroke_owner.cpp"
).read_text()
FRAME = (ROOT / "engine/render/render_frame_input.h").read_text()
PRESENTER = (ROOT / "platform/terminal_gl_present.cpp").read_text()


def test_native_numeric_reducer_matches_golden_ladder(tmp_path: Path) -> None:
    source = tmp_path / "projected_scale_probe.cpp"
    binary = tmp_path / "projected_scale_probe"
    source.write_text(
        '#include "engine/render/fl4512_projected_scale.h"\n'
        "int main() {\n"
        "  const double values[5] = {2.0,1.0,0.5,0.25,0.125};\n"
        "  const unsigned expected[5] = {131072,65536,32768,16384,8192};\n"
        "  for (int i=0;i<5;i++) {\n"
        "    double j[6] = {values[i],0,0,0,values[i],0};\n"
        "    Fl4512ProjectedScaleFactV1 f=Fl4512ProjectedScaleFromJacobianV1(j);\n"
        "    if (!f.valid || f.projected_scale_q!=expected[i]) return i+1;\n"
        "  }\n"
        "  double zero[6]={0,0,0,0,0,0};\n"
        "  Fl4512ProjectedScaleFactV1 z=Fl4512ProjectedScaleFromJacobianV1(zero);\n"
        "  return z.valid || z.reason!=FL4512_PROJECTED_SCALE_REASON_DEGENERATE_PROJECTION ? 9 : 0;\n"
        "}\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["c++", "-std=c++17", "-I", str(ROOT), str(source), "-o", str(binary)],
        check=True,
        cwd=ROOT,
    )
    subprocess.run([str(binary)], check=True, cwd=ROOT)


def test_native_exact_overflow_boundary_saturates_before_narrowing(
    tmp_path: Path,
) -> None:
    """Exercise the C++ pre-round guard at 2^32 - 0.5 and just below it."""
    source = tmp_path / "projected_scale_boundary_probe.cpp"
    binary = tmp_path / "projected_scale_boundary_probe"
    source.write_text(
        '#include "engine/render/fl4512_projected_scale.h"\n'
        "#include <cmath>\n"
        "#include <cstdint>\n"
        "#include <limits>\n"
        "int main() {\n"
        "  const double scaled_boundary = 4294967296.0 - 0.5;\n"
        "  const double boundary_rho = scaled_boundary / 65536.0;\n"
        "  const double below_rho = std::nextafter(boundary_rho, 0.0);\n"
        "  if (boundary_rho * 65536.0 != scaled_boundary) return 1;\n"
        "  double boundary_j[6] = {boundary_rho, 0, 0, 0, 0, 0};\n"
        "  Fl4512ProjectedScaleFactV1 boundary =\n"
        "      Fl4512ProjectedScaleFromJacobianV1(boundary_j);\n"
        "  if (boundary.valid ||\n"
        "      boundary.reason != FL4512_PROJECTED_SCALE_REASON_SCALE_OVERFLOW ||\n"
        "      boundary.projected_scale_q != std::numeric_limits<uint32_t>::max())\n"
        "    return 2;\n"
        "  double below_j[6] = {below_rho, 0, 0, 0, 0, 0};\n"
        "  Fl4512ProjectedScaleFactV1 below =\n"
        "      Fl4512ProjectedScaleFromJacobianV1(below_j);\n"
        "  if (below_rho * 65536.0 >= scaled_boundary || !below.valid ||\n"
        "      below.reason != FL4512_PROJECTED_SCALE_REASON_NONE ||\n"
        "      below.projected_scale_q != std::numeric_limits<uint32_t>::max())\n"
        "    return 3;\n"
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


def test_projected_extent_uses_terminal_cell_units_and_chain_owner(tmp_path: Path) -> None:
    source = tmp_path / "projected_extent_probe.cpp"
    binary = tmp_path / "projected_extent_probe"
    source.write_text(
        '#include "apps/fl4512-runtime/engine/fl4512_projected_feature_extent.h"\n'
        "int main() {\n"
        "  if (Fl4512QuantizeProjectedFeatureExtentV1(0.5) != 32768) return 1;\n"
        "  if (Fl4512QuantizeProjectedFeatureExtentV1(1.0) != 65536) return 2;\n"
        "  Fl4512ProjectedFeatureExtentAdmissionV2 a = {65536,49152};\n"
        "  return Fl4512ProjectedFeatureExtentAdmissionValidV2(a) ? 0 : 3;\n"
        "}\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["c++", "-std=c++17", "-I", str(ROOT), str(source), "-o", str(binary)],
        check=True,
        cwd=ROOT,
    )
    subprocess.run([str(binary)], check=True, cwd=ROOT)
    assert "Admission is expressed in terminal-cell arc length" in (
        ROOT / "apps/fl4512-runtime/engine/fl4512_projected_feature_extent.h"
    ).read_text()
    assert "TERM++ cell units, so divide that derivative by two." in WORLD
    assert "out.semantic_chain_projected_extent_q16_16 =" in ACTIVE_STROKE_OWNER


def test_compiled_artifact_owns_metric_and_profile_thresholds() -> None:
    artifact = json.loads(ARTIFACT.read_text())
    assert artifact["header"]["scale_quantization_version"] == 1
    assert len(artifact["scale_assets"]) == 22
    assert {
        item["geometry_basename"] for item in artifact["scale_assets"]
    } >= {
        "bush-1.akm",
        "old-tree-1.akm",
        "old-tree-2.akm",
        "sac_and_math_color.akm",
        "torus.akm",
        "tree-1.akm",
        "tree-2.akm",
        "tree-3.akm",
    }
    asset = next(
        item for item in artifact["scale_assets"]
        if item["geometry_basename"] == "pieta.akm"
    )
    assert asset["asset_metric_q24_8"] == [61440, 61440, 245760]
    assert len(asset["asset_id"]) == 64
    assert len(asset["scale_asset_sha256"]) == 64
    material = next(
        item
        for item in artifact["materials"]
        if item["profile_namespace"] == "mesh" and item["material_key"] == 200
    )
    admission = material["projected_feature_extent_admission"]
    assert admission["schema"] == "fl4512.projected_feature_extent_admission.v2"
    assert admission["mesh_thresholds"]["crease"] == {
        "enter_q": 65536,
        "exit_q": 49152,
    }
    assert len(admission["table_sha256"]) == 64


def test_u2_deletes_coverage_only_claim_owner() -> None:
    assert "Fl4512ProjectScaleAtLocalAnchorV1" in WORLD
    assert "Fl4512ProjectedScaleAssetMetricForMesh" in WORLD
    assert "RenderS3Active(r)" in WORLD
    assert "FL4512_PROJECTED_SCALE_REASON_UNSUPPORTED_SCALE_PROJECTION" in WORLD
    assert "Fl4512ProjectedFeatureExtentAdmissionForFeatureV2" in RESOLVE
    # Raw U2 claims admit only at enter_q.  The same feature may remain in
    # the U6 retention carrier down to exit_q; the two tests must stay
    # distinct so hysteresis never revives a current-frame claim.
    assert "const uint32_t extent_q = f.semantic_chain_projected_extent_q16_16" in RESOLVE
    assert "if (extent_q < extent_admission.admission.exit_q)" in RESOLVE
    assert "if (extent_q < extent_admission.admission.enter_q)" in RESOLVE
    assert "retention_entries" in RESOLVE
    assert "extent_rejected_count++" in RESOLVE
    assert "FL4512_BUNDLE_REASON_PROJECTED_SCALE_INVALID" in RESOLVE


def test_live_trace_retains_recomputable_projection_preimage() -> None:
    scale_header = (
        ROOT / "engine/render/fl4512_projected_scale.h"
    ).read_text()
    assert "Fl4512ProjectedScaleProjectionKindV1" in scale_header
    assert "anchor_camera[3]" in scale_header
    assert "focal_cells[2]" in scale_header
    assert "scale_cells[2]" in scale_header
    assert "asset_to_camera_row_major[9]" in scale_header
    assert "FL4512_PROJECTED_FEATURE_EXTENT_TRACE" in RESOLVE
    assert "fl4512.projected_feature_extent_trace.v1" in RESOLVE
    assert '"U6_RETENTION_TARGET"' in RESOLVE
    assert '"NOT_CLAIMED_EXTENT_BELOW_EXIT"' in RESOLVE


def test_projected_extent_trace_emits_one_admission_triplet() -> None:
    start = RESOLVE.index("static void Fl4512TraceProjectedFeatureExtentV1")
    end = RESOLVE.index("static bool Fl4512BuildProjectedFeatureBundle", start)
    trace_writer = RESOLVE[start:end]
    assert trace_writer.count('\\"enter_q\\"') == 1
    assert trace_writer.count('\\"exit_q\\"') == 1
    assert trace_writer.count('\\"feature_extent_admission_sha256\\"') == 1


def test_trace_carries_radial_claim_summary_without_pool_address_axis() -> None:
    assert "mesh_bundle_extent_rejected_count" in FRAME
    assert "mesh_bundle_projected_scale_q_min" in FRAME
    assert "mesh_bundle_projected_scale_q_max" in FRAME
    assert "projected_scale_q_min" in PRESENTER
    artifact = json.loads(ARTIFACT.read_text())
    for row in artifact["rows"]:
        assert "projected_scale_q" not in row
        assert "scale_quantization_version" not in row


def test_u6_retention_uses_the_shared_bundle_state_abi() -> None:
    assert "out->state = FL4512_BUNDLE_VALID;" in RESOLVE
    assert "current.bundle_state" in PRESENTER
    assert "BundleBuildV1::VALID" not in PRESENTER


def test_runtime_loader_has_one_compiled_scale_owner() -> None:
    assert "Fl4512LoadProjectedScaleAssets" in RESOLVER
    assert "g_fl4512_projected_scale_assets" in RESOLVER
    assert "projected_feature_extent_admission" in RESOLVER
    assert "Fl4512ProjectedScaleAssetMetricForMesh" in (
        ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.h"
    ).read_text()


def test_u6_is_sole_consumer_of_the_scale_retention_target() -> None:
    assert "mesh_retention_valid" in FRAME
    assert "mesh_retention_bundle_id" in FRAME
    assert "extent_role_delay" in PRESENTER
    assert '"DELAY_WITHIN_BAND"' in PRESENTER
    assert '"COMMIT_BELOW_EXIT"' in PRESENTER
    assert "raw_selected_role" in PRESENTER
    assert "presented_role" in PRESENTER
    assert "extent_quantization_version\\\":1" in PRESENTER
    assert "const uint8_t auto_fact_mask = projected_feature_valid" in RESOLVE
    assert "WriteFl4260BridgeCellFacts(x, y" in RESOLVE
    assert "b.cell_fact_mask[idx]" in PRESENTER
    assert "mesh_retention" not in RESOLVE.split(
        "const uint8_t auto_fact_mask =", 1
    )[1].split(";", 1)[0]
