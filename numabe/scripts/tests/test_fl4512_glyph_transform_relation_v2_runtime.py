"""Focused native-loader and U6-consumer contracts for relation v2."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "assets/glyphs/generated/fl4512.glyph_transform_relation.v2.json"
HARNESS = ROOT / "scripts/tests/fl4512_glyph_transform_relation_v2_harness.cpp"


@pytest.fixture(scope="module")
def native_harness(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build only the focused loader harness, never the product."""
    output = tmp_path_factory.mktemp("fl4512_gtr_v2_native") / "harness"
    completed = subprocess.run(
        [
            "c++", "-std=c++17", "-O2",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
            "-I.", "-Iengine",
            str(HARNESS),
            "apps/fl4512-runtime/engine/fl4512_glyph_transform_relation_v2.cpp",
            "apps/fl4512-runtime/engine/fl4512_profile_workspace.cpp",
            "engine/third_party/cjson/cJSON.c",
            "-o", str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return output


def _run(harness: Path, path: Path) -> subprocess.CompletedProcess[str]:
    """Execute the focused loader against one artifact path."""
    return subprocess.run(
        [str(harness), str(path)],
        cwd=ROOT,
        env={**os.environ, "ASAN_OPTIONS": "detect_leaks=0"},
        text=True,
        capture_output=True,
        check=False,
    )


def test_strict_loader_accepts_checked_in_artifact(native_harness: Path) -> None:
    """The canonical artifact publishes all rows and computes one live cost."""
    completed = _run(native_harness, ARTIFACT)
    assert completed.returncode == 0, completed.stderr
    assert "LOAD_OK" in completed.stdout
    assert "relations=65536" in completed.stdout
    assert "q_eff=" in completed.stdout
    assert "arithmetic=ok" in completed.stdout


@pytest.mark.parametrize(
    "mutation",
    [
        "v1",
        "descriptor_hash",
        "content_hash",
        "permutation",
        "duplicate_relation",
        "missing_aspect",
        "affine_overflow",
    ],
)
def test_strict_loader_rejects_negative_corpus(
    native_harness: Path, tmp_path: Path, mutation: str
) -> None:
    """Schema resurrection, hash tamper, and row reorder all fail closed."""
    document = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    if mutation == "v1":
        document["schema"] = "fl4512.glyph_transform_relation.v1"
    elif mutation == "descriptor_hash":
        document["descriptor_sha256"] = "0" * 64
    elif mutation == "content_hash":
        document["content_sha256"] = "0" * 64
    elif mutation == "permutation":
        document["relations"][1], document["relations"][2] = (
            document["relations"][2],
            document["relations"][1],
        )
    elif mutation == "duplicate_relation":
        document["relations"][2] = document["relations"][1]
    elif mutation == "missing_aspect":
        document["aspects"].pop()
    else:
        document["aspects"][0]["affine_matrix_q8_24"][0] = 2147483647
    path = tmp_path / f"{mutation}.json"
    path.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    completed = _run(native_harness, path)
    assert completed.returncode == 2
    assert "LOAD_REJECT" in completed.stderr


def test_retired_owner_and_flat_switch_branch_are_physically_absent() -> None:
    """P6 production consumes only the compiler-emitted GlyphProgram relation."""
    source = (ROOT / "engine/render/fl4512_spacetime_track_owner.h").read_text()
    assert "switch_explained" not in source
    assert "glyph_transform_relation.v1" not in source
    assert "Fl4693ProductGtrViewV1" in source
    assert "Fl4694GlyphProgramGtrRelationFindV1" in source
    assert "compiled_transform_cost" in source
    assert "Fl4512GlyphTransformRelationTableV2" not in source
    assert "Fl4512GlyphTransformCostV2" not in source
    assert "relation_trace" in source


def test_global_path_is_executable_derived() -> None:
    """Mutable workspaces cannot redirect the generated relation authority."""
    header = (ROOT / "apps/fl4512-runtime/engine/fl4512_profile_workspace.h").read_text()
    source = (ROOT / "apps/fl4512-runtime/engine/fl4512_profile_workspace.cpp").read_text()
    assert "glyph_transform_relation_v2_path" in header
    assert "Fl4512GlobalGlyphTransformRelationV2Path" in header
    assert "generated/fl4512.glyph_transform_relation.v2.json" in source
    assert "staged_global.glyph_transform_relation_v2_path" in source


def test_widened_arithmetic_has_no_retired_boundary_operations() -> None:
    """The sanitizer seam exercises quotient rounding and widened magnitudes."""
    source = (
        ROOT / "apps/fl4512-runtime/engine/fl4512_glyph_transform_relation_v2.cpp"
    ).read_text(encoding="utf-8")
    assert "Fl4512GlyphTransformArithmeticBoundarySelfTestV2" in source
    assert "ARITHMETIC_ROUNDED_RANGE" in source
    assert "ARITHMETIC_UNSIGNED_SUM" in source
    assert "llabs(" not in source
    assert "2u * magnitude" not in source
    assert "predicted_x_q24 - prior_x_q24" not in source
