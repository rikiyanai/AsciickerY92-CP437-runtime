from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "assets/glyphs/profiles/material_rendering_profiles.v1.json"
POOL_PATH = ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json"
EXTENT_GOLDEN_PATH = (
    ROOT / "assets/glyphs/fixtures/fl4512_projected_feature_extent_golden_v2.json"
)
PRESENTER_PATH = ROOT / "platform/terminal_gl_present.cpp"
TUPLE_PATH = ROOT / "apps/fl4512-runtime/platform/fl4512_u6_presentation_tuple.h"
RETIRED_TUPLE_PATH = ROOT / "platform/fl4652_revision0_tonal_tuple.h"
FINAL_TUPLE_OWNER_PATH = ROOT / "apps/fl4512-runtime/engine/fl4693_final_tuple_transport.cpp"


def _mesh_200(document: dict) -> dict:
    rows = document.get("profiles", document.get("materials", []))
    return next(
        row
        for row in rows
        if row.get("material_id") == "mesh:200"
        or (
            row.get("profile_namespace") == "mesh"
            and row.get("material_key") == 200
        )
    )


def test_revision_zero_u6_owner_and_name_are_deleted() -> None:
    presenter = PRESENTER_PATH.read_text(encoding="utf-8")
    handshake = (
        ROOT / "apps/fl4512-runtime/platform/fl4683_scene_fact_handshake.h"
    ).read_text(encoding="utf-8")
    assert not RETIRED_TUPLE_PATH.exists()
    assert TUPLE_PATH.exists()
    # Seam ownership: the U6 tuple header is product-owned; the shared
    # presenter names no U6 include.
    assert '#include "fl4512_u6_presentation_tuple.h"' in handshake
    assert '#include "fl4512_u6_presentation_tuple.h"' not in presenter
    assert "fl4652_revision0_tonal_tuple.h" not in presenter
    finalizer = FINAL_TUPLE_OWNER_PATH.read_text(encoding="utf-8").split(
        "bool Fl4693ProfileTupleFinalizeV1", 1
    )[1].split("const FinalGlyphResultV2* Fl4693ProfileTupleReadV1", 1)[0]
    assert "current.pool_revision == 0u" not in finalizer
    assert "current.pool_revision > 0u" not in finalizer
    assert "Fl4512FinalizePresentationTupleV2" not in presenter


def test_mesh_200_authored_extent_matches_existing_golden_authority() -> None:
    profile = _mesh_200(json.loads(PROFILE_PATH.read_text(encoding="utf-8")))
    thresholds = profile["projected_feature_extent_admission"]["mesh_thresholds"]
    golden = json.loads(EXTENT_GOLDEN_PATH.read_text(encoding="utf-8"))
    assert thresholds == golden["mesh_thresholds"]
    # The canonical Pieta trace reaches exactly half a terminal cell. Preserve
    # the existing inclusive silhouette boundary rather than tuning to the run.
    assert thresholds["silhouette"] == {"enter_q": 32768, "exit_q": 24576}


def test_tracked_pool_is_exact_canonical_compiler_output(tmp_path: Path) -> None:
    rebuilt = tmp_path / "fl4512.compiled_pool.v1.json"
    tracked = json.loads(POOL_PATH.read_text(encoding="utf-8"))
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/compile_fl4512_pools.py"),
            "--out",
            str(rebuilt),
            "--aspect-authoring",
            str(
                ROOT
                / "assets/glyphs/profiles/fl4512.aspect_pool_authoring.v1.json"
            ),
            "--compiler-commit",
            tracked["header"]["compiler_commit"],
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "FL4512_COMPILE_PASS rows=264" in result.stdout
    assert rebuilt.read_bytes() == POOL_PATH.read_bytes()
    artifact = json.loads(rebuilt.read_text(encoding="utf-8"))
    assert artifact["header"]["pool_revision"] == 2
    compiled = _mesh_200(artifact)
    golden = json.loads(EXTENT_GOLDEN_PATH.read_text(encoding="utf-8"))
    assert (
        compiled["projected_feature_extent_admission"]["mesh_thresholds"]
        == golden["mesh_thresholds"]
    )


def test_live_compiled_pool_revision_cannot_be_zero() -> None:
    resolver = (
        ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp"
    ).read_text(encoding="utf-8")
    assert "static std::atomic<uint64_t> g_fl4260_profile_revision{1};" in resolver
    assert "artifact_revision < 2" in resolver
    assert (
        "loaded_revision = Fl4260CommitProfileRevisionValue("
        "(uint64_t)artifact_revision);"
    ) in resolver


def test_u6_tuple_helper_preserves_unicode_identity_and_additive_colors() -> None:
    owner = FINAL_TUPLE_OWNER_PATH.read_text(encoding="utf-8")
    finalizer = owner.split("bool Fl4693ProfileTupleFinalizeV1", 1)[1]
    finalizer = finalizer.split("const FinalGlyphResultV2* Fl4693ProfileTupleReadV1", 1)[0]
    assert "tuple.final_gid = tuple.glyph_id" in finalizer
    assert "tuple.final_fg = input->source->fg" in finalizer
    assert "tuple.final_bg = input->source->bk" in finalizer
    assert "tuple.final_fg = input->ink" in finalizer


def test_terrain_and_nonmesh_keep_the_generic_u6_projection() -> None:
    owner = FINAL_TUPLE_OWNER_PATH.read_text(encoding="utf-8")
    finalizer = owner.split(
        "bool Fl4693ProfileTupleFinalizeV1", 1
    )[1].split("const FinalGlyphResultV2* Fl4693ProfileTupleReadV1", 1)[0]
    assert "Fl4260GetActiveProfileColor" in owner
    assert "tuple.final_fg = input->paper" in finalizer
    assert "tuple.final_bg = input->ink" in finalizer
    assert "tuple.final_gid = tuple.glyph_id" in finalizer
    assert "Fl4652ApplyTonalUnderStrokeTupleV1" not in finalizer
