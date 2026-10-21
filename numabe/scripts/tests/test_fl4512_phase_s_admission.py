#!/usr/bin/env python3
"""Verify the v2 compiled-pool handoff into Phase S.

Phase S receives one immutable Active Stroke block plus the registry identity
that names its compiler publication.  The runtime loader must reject malformed
compiled JSON before the detached staging table is swapped, and it must not
carry the retired silhouette probe into this lookup.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
HEADER_PATH = ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.h"
SOURCE_PATH = ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp"
HEADER = HEADER_PATH.read_text(encoding="utf-8")
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")
ARTIFACT_PATH = (
    ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json"
)


def function_body(name: str, next_anchor: str) -> str:
    """Return one source function slice for focused ownership assertions."""

    return SOURCE.split(name, 1)[1].split(next_anchor, 1)[0]


def canonical_sha(value: object) -> str:
    """Return the compact sorted JSON hash shared by compiler and resolver."""

    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@pytest.fixture(scope="module")
def resolver_parser_harness(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Compile a tiny executable that invokes the production static parser.

    Including the resolver source places its internal parser in the same
    translation unit as this test main.  Linker dead stripping removes the
    unrelated runtime entrypoints, leaving a focused parser harness without
    modifying product visibility or adding a second exported test API.
    """

    temporary = tmp_path_factory.mktemp("fl4512_active_stroke_v2_parser")
    source = temporary / "parser_harness.cpp"
    binary = temporary / "parser_harness"
    source.write_text(
        """
#include <cstdio>
#include <cstdlib>
#include "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp"

int main(int argc, char** argv)
{
    if (argc != 2)
        return 90;
    std::FILE* handle = std::fopen(argv[1], "rb");
    if (!handle)
        return 91;
    std::fseek(handle, 0, SEEK_END);
    const long size = std::ftell(handle);
    std::rewind(handle);
    char* bytes = static_cast<char*>(std::malloc(
        static_cast<size_t>(size) + 1u));
    if (!bytes)
    {
        std::fclose(handle);
        return 92;
    }
    const size_t read_count = std::fread(
        bytes, 1u, static_cast<size_t>(size), handle);
    std::fclose(handle);
    bytes[read_count] = '\\0';
    cJSON* block = cJSON_Parse(bytes);
    std::free(bytes);
    if (!block)
        return 93;
    Fl4260V1RampDensityProfile profile = {};
    const bool accepted = Fl4512LoadActiveStrokeTopologyAdmissionInto(
        &profile, block);
    cJSON_Delete(block);
    return accepted && profile.has_active_stroke_topology_admission &&
        profile.active_stroke_topology_admission.valid ? 0 : 1;
}
""".lstrip(),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "c++",
            "-std=c++17",
            "-ffunction-sections",
            "-fdata-sections",
            "-I.",
            "-Iengine",
            "-Iengine/render",
            str(source),
            str(ROOT / "engine/third_party/cjson/cJSON.c"),
            "-Wl,-dead_strip",
            "-o",
            str(binary),
        ],
        cwd=ROOT,
        check=True,
    )
    return binary


def run_parser(
    harness: Path,
    block: dict,
    path: Path,
) -> subprocess.CompletedProcess[str]:
    """Write one detached compiled block and return the parser result."""

    path.write_text(
        json.dumps(block, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return subprocess.run(
        [str(harness), str(path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def compiled_block() -> dict:
    """Load the checked-in mesh:200 v2 block as a detached fixture."""

    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    material = next(
        row for row in artifact["materials"]
        if row["profile_namespace"] == "mesh"
        and row["material_key"] == 200
    )
    return material["active_stroke_topology"]


def test_v2_parser_requires_exact_shape_hashes_bounds_and_operation_order():
    """Every compiled member is checked before it is copied into staging."""

    parser = function_body(
        "static bool Fl4512LoadActiveStrokeTopologyAdmissionInto",
        "static bool Fl4512LoadProjectedScaleAssets",
    )
    assert '"fl4512.active_stroke_topology.v2"' in parser
    assert "Fl4512JsonObjectHasExactKeys" in parser
    assert "cJSON_GetArraySize(gaussian_table) !=" in parser
    assert "kFl4512ActiveStrokeGaussianTableCountV2" in parser
    assert "Fl4512JsonValueHashMatches" in parser
    assert 'Fl4512CanonicalHashMatches(admission, "content_sha256")' in parser
    assert "gaussian_sigma != 65536u" in parser
    assert "gaussian_distance2_step != 4096u" in parser
    assert "resample_min >= resample_max" in parser
    assert "trim_radius >= coverage_radius" in parser
    assert "coverage_radius >= extend_radius" in parser
    assert "trim_radius >= merge_radius" in parser
    for operation in (
        '"delete"',
        '"split"',
        '"trim"',
        '"extend"',
        '"merge"',
        '"insert"',
    ):
        assert operation in parser
    assert "strcmp(operation, kOperationNames[index]) != 0" in parser
    assert "parsed.gaussian_table_q0_16[index]" in parser
    assert "profile->active_stroke_topology_admission = parsed" in parser
    assert "profile->has_active_stroke_topology_admission = true" in parser


def test_loader_registry_projection_and_phase_s_lookup_are_v2_only():
    """Registry and lookup expose the compiled block without a second policy."""

    registry = function_body(
        "static bool Fl4512ActiveStrokeRegistryHashMatchesV2",
        "// FL-4512 U4 delete-first cutover.",
    )
    assert '"active_stroke_topology_sha256"' in registry
    assert '"material_key"' in registry
    assert '"profile_namespace"' in registry
    assert "silhouette" not in registry

    lookup = function_body(
        "bool Fl4512PhaseSAdmissionForProfileV2",
        "// =============================================================",
    )
    assert "profile.has_active_stroke_topology_admission" in lookup
    assert "g_fl4512_active_stroke_topology_registry_sha256[0]" in lookup
    assert "out->active_stroke = profile.active_stroke_topology_admission" in lookup
    assert "out->active_stroke_topology_registry_sha256" in lookup
    assert "silhouette" not in lookup
    assert "Fl4512PhaseSAdmissionForProfileV1" not in HEADER + SOURCE


def test_resolver_translation_unit_accepts_the_v2_detached_snapshot_abi():
    """Compile the production resolver without linking or launching a product."""

    subprocess.run(
        [
            "c++",
            "-std=c++17",
            "-fsyntax-only",
            "-I.",
            "-Iengine",
            "-Iengine/render",
            str(SOURCE_PATH),
        ],
        cwd=ROOT,
        check=True,
    )


def test_header_capacity_and_operation_arrays_are_fixed_at_the_abi_boundary():
    """The snake sees fixed-size storage, never a borrowed JSON array pointer."""

    assert "kFl4512ActiveStrokeGaussianTableCountV2 = 145" in HEADER
    assert "kFl4512ActiveStrokeTopologyOperationCountV2 = 6" in HEADER
    assert (
        "uint16_t gaussian_table_q0_16"
        "[kFl4512ActiveStrokeGaussianTableCountV2]"
    ) in HEADER
    assert (
        "struct Fl4512ActiveStrokeTopologyAdmissionV2 active_stroke"
    ) in HEADER
    assert "struct Fl4512PhaseSAdmissionLookupV2" in HEADER


def test_production_parser_accepts_the_complete_compiler_block(
    resolver_parser_harness: Path,
    tmp_path: Path,
):
    """The tracked compiler output crosses the real C++ parser boundary."""

    result = run_parser(
        resolver_parser_harness,
        compiled_block(),
        tmp_path / "baseline.json",
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "mutation",
    (
        "gaussian_item",
        "gaussian_permutation",
        "gaussian_duplicate",
        "gaussian_length",
        "gaussian_hash",
        "operation_permutation",
        "operation_duplicate",
        "operation_unknown",
        "content_hash",
    ),
)
def test_production_parser_rejects_each_compiled_mutation_fixture(
    resolver_parser_harness: Path,
    tmp_path: Path,
    mutation: str,
):
    """Each malformed compiled fact fails without a runtime default."""

    block = compiled_block()
    if mutation == "gaussian_item":
        block["gaussian_table_q0_16"][10] += 1
    elif mutation == "gaussian_permutation":
        table = block["gaussian_table_q0_16"]
        table[10], table[11] = table[11], table[10]
        block["gaussian_table_sha256"] = canonical_sha(table)
        block["content_sha256"] = canonical_sha(
            {
                key: value for key, value in block.items()
                if key != "content_sha256"
            }
        )
    elif mutation == "gaussian_duplicate":
        block["gaussian_table_q0_16"][10] = (
            block["gaussian_table_q0_16"][11]
        )
        block["gaussian_table_sha256"] = canonical_sha(
            block["gaussian_table_q0_16"]
        )
        block["content_sha256"] = canonical_sha(
            {
                key: value for key, value in block.items()
                if key != "content_sha256"
            }
        )
    elif mutation == "gaussian_length":
        block["gaussian_table_q0_16"].pop()
    elif mutation == "gaussian_hash":
        block["gaussian_table_sha256"] = "0" * 64
    elif mutation == "operation_permutation":
        order = block["topology_operation_order"]
        order[0], order[1] = order[1], order[0]
        unhashed = {
            key: value for key, value in block.items()
            if key != "content_sha256"
        }
        block["content_sha256"] = canonical_sha(unhashed)
    elif mutation == "operation_duplicate":
        block["topology_operation_order"][-1] = "merge"
        unhashed = {
            key: value for key, value in block.items()
            if key != "content_sha256"
        }
        block["content_sha256"] = canonical_sha(unhashed)
    elif mutation == "operation_unknown":
        block["topology_operation_order"][-1] = "unknown"
        unhashed = {
            key: value for key, value in block.items()
            if key != "content_sha256"
        }
        block["content_sha256"] = canonical_sha(unhashed)
    else:
        block["content_sha256"] = "0" * 64

    result = run_parser(
        resolver_parser_harness,
        block,
        tmp_path / f"{mutation}.json",
    )
    assert result.returncode == 1, mutation
