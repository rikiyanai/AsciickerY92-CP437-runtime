from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_feature_id_keeps_surface_identity_and_ignores_mesh_provenance(
    tmp_path: Path,
) -> None:
    source = tmp_path / "fl4783_feature_identity_probe.cpp"
    binary = tmp_path / "fl4783_feature_identity_probe"
    source.write_text(
        '#include "engine/render/fl4512_structural_sequence_input.h"\n'
        "int main() {\n"
        "  Fl4512FeatureRefV1 a = {};\n"
        "  a.surface.world_generation = 9;\n"
        "  a.surface.owner_kind = FL4208_OWNER_MESH;\n"
        "  a.surface.owner_x = 21; a.surface.owner_y = -4;\n"
        "  a.surface.anchor_u_q = 17; a.surface.anchor_v_q = 31;\n"
        "  a.semantic_chain_id = 77;\n"
        "  a.provenance.producer_kind = FL4512_FEATURE_PRODUCER_MESH;\n"
        "  a.provenance.mesh.mesh_instance_uid = 100;\n"
        "  a.provenance.mesh.depth_owner = 101;\n"
        "  a.material_key = 3; a.dispatch_surface = 2; a.role = 1;\n"
        "  a.feature_kind = 1; a.aspect_region_id = 4;\n"
        "  Fl4512FeatureRefV1 provenance_change = a;\n"
        "  provenance_change.provenance.mesh.mesh_instance_uid = 200;\n"
        "  provenance_change.provenance.mesh.depth_owner = 201;\n"
        "  if (!Fl4512FeatureRefEqualV1(a, provenance_change)) return 1;\n"
        "  Fl4512FeatureRefV1 different_surface = a;\n"
        "  different_surface.surface.anchor_u_q++;\n"
        "  if (Fl4512FeatureRefEqualV1(a, different_surface)) return 2;\n"
        "  Fl4512FeatureRefV1 terrain = a;\n"
        "  terrain.surface.owner_kind = FL4208_OWNER_TERRAIN;\n"
        "  terrain.provenance = {};\n"
        "  terrain.provenance.producer_kind = FL4512_FEATURE_PRODUCER_TERRAIN;\n"
        "  if (Fl4512FeatureRefEqualV1(a, terrain)) return 3;\n"
        "  if (!Fl4512FeatureRefLessV1(different_surface, a) &&\n"
        "      !Fl4512FeatureRefLessV1(a, different_surface)) return 4;\n"
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


def test_projected_feature_boundary_names_identity_and_provenance() -> None:
    projected = (ROOT / "engine/render/projected_surface_feature.h").read_text(
        encoding="utf-8"
    )
    structural = (
        ROOT / "engine/render/fl4512_structural_sequence_input.h"
    ).read_text(encoding="utf-8")
    resolve = (ROOT / "engine/render/render_resolve.cpp").read_text(
        encoding="utf-8"
    )
    assert "struct Fl4512FeatureIdV1" in projected
    assert "Fl4208SurfaceId surface;" in projected
    assert "struct Fl4512ProjectedSurfaceFeatureV1" in (
        (ROOT / "engine/render/render_internal.h").read_text(encoding="utf-8")
    )
    assert "uint64_t semantic_chain_id;" in projected
    assert "Fl4512SurfaceFeatureProvenanceV1 provenance;" in structural
    assert "ref.surface = f.surface;" in resolve
    assert "ref.provenance = f.provenance;" in resolve
    assert "mesh_producer && f.provenance.mesh.mesh_instance_uid" in resolve
    assert "terrain_producer" in resolve
    track_owner = (
        ROOT / "engine/render/fl4512_spacetime_track_owner.h"
    ).read_text(encoding="utf-8")
    assert "ref.provenance.producer_kind != FL4512_FEATURE_PRODUCER_MESH" in track_owner
    assert "ref.provenance.mesh.depth_owner != 0u" in track_owner
    assert "Fl4512FeatureIdEqualV1(a, b)" in structural
    assert "a.provenance.mesh" not in structural.split(
        "Fl4512FeatureRefEqualV1", 1
    )[1].split("}", 1)[0]
