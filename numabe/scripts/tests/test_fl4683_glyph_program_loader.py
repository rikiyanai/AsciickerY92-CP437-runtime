# =============================================================================
# scripts/tests/test_fl4683_glyph_program_loader.py
# =============================================================================
# WHAT THIS FILE CHECKS
# ---------------------
# It exercises the FL-4683 compiled-side loader:
#
#   scripts/compile_fl4683_glyph_program.py   (offline: JSON -> binary sidecar)
#   apps/fl4512-runtime/platform/fl4683_glyph_program_loader.h    (native API)
#   apps/fl4512-runtime/platform/fl4683_glyph_program_loader.cpp  (native implementation)
#
# The loader reads a compact binary "sidecar" that was compiled offline from a
# 93 MiB JSON artifact, confirms the fingerprint ("SHA-256 hash" -- a short
# value that changes completely if any byte of a file changes) of that sidecar
# and of every file it was built from, and then publishes a read-only view.
#
# The checks below are grouped into five questions:
#
#   1. Does the offline compiler produce the same bytes every time?
#      (determinism -- a fingerprint pin is meaningless otherwise)
#   2. Does the loader accept the real, untouched data and expose the right
#      counts and identities?
#   3. Does a single altered byte produce a NAMED abort rather than a crash,
#      a silent success, or a partial read?
#   4. When the large regenerable caches are absent -- normal on a fresh
#      machine -- does the loader report its own named state rather than
#      guessing or failing generically?
#   5. Do the default-build and serialized-sidecar gates remain distinct from
#      the gate-enabled product startup promotion?
#
# HOW THE NATIVE SIDE IS EXERCISED
# --------------------------------
# A small C++ program (written into a temporary directory below) links against
# the loader, performs one load, runs every kind of query, and prints its
# findings as `key=value` lines. Python compiles that program, runs it, and
# reads the lines back. This keeps the native contract under test without
# adding any C++ test framework to the repository.
# =============================================================================

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Dict, List

import pytest

# `jsonschema` validates the receipt against its schema. If the library is not
# installed, skip rather than fail: its absence is an environment gap, not a
# defect in the code under test.
jsonschema = pytest.importorskip("jsonschema")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
COMPILER = ROOT / "scripts/compile_fl4683_glyph_program.py"
LOADER_HEADER = ROOT / "apps/fl4512-runtime/platform/fl4683_glyph_program_loader.h"
LOADER_SOURCE = ROOT / "apps/fl4512-runtime/platform/fl4683_glyph_program_loader.cpp"
SIDECAR = ROOT / "assets/glyphs/generated/fl4683.glyph_program.v1.bin"
RECEIPT = ROOT / "assets/glyphs/generated/fl4683.glyph_program_pin.v1.json"
RECEIPT_SCHEMA = ROOT / "assets/glyphs/schema/fl4683.glyph_program_pin.v1.schema.json"
GLYPH_FACT_ARTIFACT = ROOT / "assets/glyphs/generated/fl4676.glyph_fact.v1.json"
GTR_V2_ARTIFACT = ROOT / "assets/glyphs/generated/fl4512.glyph_transform_relation.v2.json"

# Build definitions audited by the exact product-activation gate below.
BUILD_FILES = [
    ROOT / "apps/game-fl4512/makefile_game",
    ROOT / "apps/game-fl4512/makefile_game_mac",
    ROOT / "makefile_game_term",
    ROOT / "makefile_game_term_mac",
    ROOT / "apps/asciicker-presenter/makefile_asciiid",
    ROOT / "apps/asciicker-presenter/makefile_asciiid_mac",
    ROOT / "build-web.sh",
    ROOT / "build.sh",
]

# FL-4693 owns one compiled GlyphProgram selector in every build that presents
# the live glyph field. The two unsuffixed Linux targets joined this set when
# their retired GPU-winner owner was deleted; terminal/server-only builds stay
# outside it because they do not compile a user-facing presenter.
P6_PRODUCT_BUILD_NAMES = {
    "apps/game-fl4512/makefile_game",
    "apps/game-fl4512/makefile_game_mac",
    "apps/asciicker-presenter/makefile_asciiid",
    "apps/asciicker-presenter/makefile_asciiid_mac",
    "build-web.sh",
}
PRODUCT_MANIFEST = ROOT / "apps/fl4512-runtime/fl4512_product_sources.mk"
TERMINAL_MANIFEST = ROOT / "apps/terminal-runtime/terminal_runtime_sources.mk"


def _parse_make_lists(text: str) -> Dict[str, List[str]]:
    """Parse every `NAME :=` list selection with make continuations."""
    entries: Dict[str, List[str]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if current is None:
            if ":=" in line:
                name = line.split(":=")[0].strip()
                rest = line.split(":=", 1)[1].strip()
                if name.replace("_", "").isalnum():
                    current = name
                    entries[current] = []
                    if rest and not rest.endswith("\\"):
                        entries[current].append(rest.rstrip("\\").strip())
                        current = None
                    elif rest:
                        item = rest[:-1].strip()
                        if item:
                            entries[current].append(item)
            continue
        if line.endswith("\\"):
            line = line[:-1].strip()
            if line:
                entries[current].append(line)
            continue
        if line:
            entries[current].append(line)
        current = None
    return entries


def _expand_product_manifest_selections(text: str) -> str:
    """Resolve $(...) source selections through the product manifests.

    Build specs select implementation TUs through manifest variables, so a
    literal source-name audit must expand them first. build-web.sh selects
    CORE plus PROGRAM plus PORTS through $FL4512_PARTITION_SRCS; the terminal
    specs select through $(TERMINAL_RUNTIME_SOURCES), which chains through
    the terminal manifest into the product manifest. Expansion runs to a
    fixpoint (three levels max) so chained references resolve.
    """
    manifest = _parse_make_lists(PRODUCT_MANIFEST.read_text(encoding="utf-8"))
    if TERMINAL_MANIFEST.is_file():
        manifest.update(
            _parse_make_lists(TERMINAL_MANIFEST.read_text(encoding="utf-8"))
        )
    # Spec-local aliases (e.g. TERMINAL_RUNTIME_SOURCES in the terminal
    # makefiles) chain into the manifests.
    manifest.update(_parse_make_lists(text))
    partition = (
        manifest.get("FL4512_CORE_SRCS", [])
        + manifest.get("FL4512_PROGRAM_SRCS", [])
        + manifest.get("FL4512_PORTS_ANCHOR", [])
    )
    text = text.replace("$FL4512_PARTITION_SRCS", " ".join(partition))
    for _ in range(3):
        reduced = False
        for name, files in manifest.items():
            token = f"$({name})"
            if token in text:
                text = text.replace(token, " ".join(files))
                reduced = True
        if not reduced:
            break
    return text


# Product sources that sit on or next to the live frame path. None of them may
# reference the loader: that is the call-site gate.
LIVE_PATH_SOURCES = [
    ROOT / "engine/render/render_resolve.cpp",
    ROOT / "platform/terminal_gl_present.cpp",
    ROOT / "web/game_web.cpp",
    ROOT / "web/game_web.html",
    ROOT / "apps/asciicker-presenter/asciiid.cpp",
]

# ---------------------------------------------------------------------------
# The native harness program
# ---------------------------------------------------------------------------
# One load, every query kind, and one handshake pre-flight, reported as
# `key=value` lines on standard output. Argument order:
#
#   argv[1] repository root to load from
#   argv[2] expected sidecar size in bytes
#   argv[3] expected sidecar fingerprint, 64 hex characters
#   argv[4] load flags, as a decimal number
#   argv[5] fingerprint of a fact identifier that must be present
#   argv[6] fingerprint of a compiled address that must be present
#   argv[7] a glyph identifier that must be in the admitted universe
#   argv[8] a glyph identifier that must NOT be in the admitted universe
HARNESS_SOURCE = r"""
// Native harness for the FL-4683 glyph-program loader. Not product code: it
// exists so a Python test can drive the native contract end to end.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "apps/fl4512-runtime/platform/fl4683_glyph_program_loader.h"

// Turn 64 hex characters into the 32 raw bytes they encode. Two hex digits make
// one byte. Returns false on any character that is not a hex digit.
static bool HexTo32(const char* text, unsigned char out[32])
{
    if (text == NULL || strlen(text) != 64)
        return false;
    for (int i = 0; i < 32; ++i)
    {
        unsigned int value = 0;
        for (int nibble = 0; nibble < 2; ++nibble)
        {
            const char c = text[i * 2 + nibble];
            unsigned int digit;
            if (c >= '0' && c <= '9') digit = (unsigned int)(c - '0');
            else if (c >= 'a' && c <= 'f') digit = (unsigned int)(c - 'a') + 10u;
            else if (c >= 'A' && c <= 'F') digit = (unsigned int)(c - 'A') + 10u;
            else return false;
            value = (value << 4) | digit;
        }
        out[i] = (unsigned char)value;
    }
    return true;
}

// Print a 32-byte fingerprint as 64 lowercase hex characters.
static void PrintHex(const char* key, const unsigned char digest[32])
{
    printf("%s=", key);
    for (int i = 0; i < 32; ++i)
        printf("%02x", digest[i]);
    printf("\n");
}

static unsigned long long FnvByte(unsigned long long value, unsigned int byte)
{
    value ^= (unsigned long long)(byte & 0xFFu);
    return value * 1099511628211ull;
}

static unsigned long long FnvU16(unsigned long long value, unsigned int word)
{
    value = FnvByte(value, word);
    return FnvByte(value, word >> 8);
}

static unsigned long long FnvU32(unsigned long long value, unsigned int word)
{
    value = FnvU16(value, word);
    return FnvU16(value, word >> 16);
}

int main(int argc, char** argv)
{
    // Always report the compile-time gate first, so the reader of the output
    // knows which branch of the loader produced everything below.
    printf("compiled_in=%u\n", Fl4683GlyphProgramLoaderCompiledInV1());

    if (argc < 9)
    {
        fprintf(stderr, "harness needs 8 arguments\n");
        return 2;
    }

    Fl4683GlyphProgramLoadRequestV1 request;
    memset(&request, 0, sizeof(request));
    request.schema_version = FL4683_GLYPH_PROGRAM_LOADER_SCHEMA_VERSION;
    request.repository_root = argv[1];
    request.sidecar_relative_path = NULL;   // use the built-in default path
    request.expected_sidecar_byte_size = strtoull(argv[2], NULL, 10);
    if (!HexTo32(argv[3], request.expected_sidecar_sha256))
    {
        fprintf(stderr, "argv[3] is not a 64-character hex digest\n");
        return 2;
    }
    request.flags = (uint32_t)strtoul(argv[4], NULL, 10);

    Fl4683GlyphProgramLoadResultV1 result = Fl4683GlyphProgramLoadV1(&request);
    printf("status=%s\n", Fl4683GlyphProgramStatusNameV1(result.status));
    printf("status_code=%u\n", result.status);
    printf("failed_pin_role=%u\n", result.failed_pin_role);
    printf("failed_pin_path=%s\n", result.failed_pin_path);
    if (result.image == NULL)
    {
        // A failed load must publish nothing at all.
        printf("image=absent\n");
        return 0;
    }
    printf("image=present\n");

    const Fl4683GlyphProgramImageV1* image = result.image;
    printf("local_relation_cache_available=%u\n",
        (unsigned)image->local_relation_cache_available);
    printf("program_schema_version=%u\n", image->program.schema_version);
    printf("glyph_record_count=%u\n", image->program.glyph_record_count);
    printf("fact_count=%u\n", image->program.fact_count);
    printf("max_fact_members=%u\n", (unsigned)image->program.max_fact_members);
    printf("max_relation_edges=%u\n", (unsigned)image->program.max_relation_edges);
    printf("runtime_rights=%u\n", image->program.runtime_rights);
    printf("runtime_consumption_allowed=%u\n",
        (unsigned)image->program.runtime_consumption_allowed);
    printf("declared_future_rights=%u\n", image->declared_future_rights);
    printf("pin_count=%u\n", image->pin_count);
    printf("member_count=%u\n", image->member_count);
    printf("edge_count=%u\n", image->edge_count);
    PrintHex("artifact_sha256", image->program.artifact_sha256);
    PrintHex("artifact_content_sha256", image->program.artifact_content_sha256);
    PrintHex("glyph_universe_sha256", image->program.glyph_universe_sha256);
    PrintHex("scene_fact_schema_sha256", image->program.scene_fact_schema_sha256);

    // FL-4694 E8: traverse all 65,536 aspect-conditioned addresses through the
    // public finder, compare each answer to the loaded canonical row, and fold
    // every semantic field into a deterministic digest. Python independently
    // computes the same fold from the committed GTR-v2 JSON.
    printf("gtr_v2_relation_count=%u\n", image->gtr_v2_relation_count);
    unsigned int gtr_lookup_mismatches = 0u;
    unsigned long long gtr_digest = 14695981039346656037ull;
    for (unsigned int index = 0u; index < image->gtr_v2_relation_count; ++index)
    {
        const Fl4694GlyphProgramGtrRelationV1* row = image->gtr_v2_relations + index;
        const Fl4694GlyphProgramGtrRelationV1* found =
            Fl4694GlyphProgramGtrRelationFindV1(
                image, row->aspect, row->from_glyph_id, row->to_glyph_id);
        if (found == NULL || memcmp(found, row, sizeof(*row)) != 0)
            ++gtr_lookup_mismatches;
        gtr_digest = FnvByte(gtr_digest, row->aspect);
        gtr_digest = FnvU32(gtr_digest, row->from_glyph_id);
        gtr_digest = FnvU32(gtr_digest, row->to_glyph_id);
        for (unsigned int component = 0u; component < 11u; ++component)
            gtr_digest = FnvByte(gtr_digest, row->static_components[component]);
        for (unsigned int component = 0u; component < 7u; ++component)
            gtr_digest = FnvByte(gtr_digest, row->affine_components[component]);
        gtr_digest = FnvU16(gtr_digest, row->relation_confidence_q0_16);
        gtr_digest = FnvU16(gtr_digest, row->compiled_transform_cost);
    }
    printf("gtr_v2_lookup_mismatches=%u\n", gtr_lookup_mismatches);
    printf("gtr_v2_semantic_fnv64=%016llx\n", gtr_digest);
    printf("gtr_v2_unknown_address=%s\n",
        Fl4694GlyphProgramGtrRelationFindV1(image, 64u, 0u, 0u) == NULL
            ? "rejected" : "accepted");

    // ---- Gate 3, demonstrated ------------------------------------------------
    // Feed the freshly loaded program into the handshake's own pre-flight. It
    // must refuse, naming runtime rights as the reason, because the program
    // carries runtime_consumption_allowed = 0.
    {
        FinalGlyphResultV2 output;
        memset(&output, 0, sizeof(output));
        Fl4683SelectorInputV1 selector;
        memset(&selector, 0, sizeof(selector));
        selector.schema_version = FL4683_SELECTOR_INPUT_SCHEMA_VERSION;
        selector.adapter_kind = FL4683_ADAPTER_DESKTOP_TERM_PLUS_PLUS;
        selector.scene_fact.schema_version = FL4683_SCENE_FACT_SCHEMA_VERSION;
        selector.scene_fact.valid = 1;
        selector.glyph_program = image->program;
        selector.output_tuple = &output;
        Fl4683SelectorPreflightV1 preflight =
            Fl4683SelectorInputPreflightV1(&selector);
        printf("preflight_accepted=%u\n", (unsigned)preflight.accepted);
        printf("preflight_rejection=%u\n", (unsigned)preflight.rejection_reason);
    }

    // ---- Query: find a fact by its identifier fingerprint --------------------
    Fl4683GlyphProgramQueryV1 query;
    memset(&query, 0, sizeof(query));
    query.schema_version = FL4683_GLYPH_PROGRAM_LOADER_SCHEMA_VERSION;

    query.kind = FL4683_GLYPH_PROGRAM_QUERY_FACT_BY_FACT_ID_SHA256;
    if (!HexTo32(argv[5], query.key_sha256))
        return 2;
    Fl4683GlyphProgramQueryResultV1 by_id =
        Fl4683GlyphProgramQueryExecuteV1(image, &query);
    printf("query_fact_by_id_found=%u\n", (unsigned)by_id.found);
    printf("query_fact_by_id_index=%u\n", by_id.fact_index);
    printf("query_fact_by_id_members=%u\n", (unsigned)by_id.member_count);
    printf("query_fact_by_id_weight_sum=%u\n",
        (unsigned)by_id.subset_weight_sum_q0_16);
    if (by_id.found != 0 && by_id.members != NULL)
    {
        // Add up the member weights so the test can confirm the compiled
        // normalisation survived the JSON-to-binary conversion.
        unsigned int total = 0;
        for (unsigned int i = 0; i < (unsigned int)by_id.member_count; ++i)
            total += by_id.members[i].membership_weight_q0_16;
        printf("query_fact_by_id_weight_total=%u\n", total);
        printf("query_fact_by_id_first_member=%u\n",
            by_id.members[0].glyph_record_id);
    }

    // ---- Query: find a fact by its compiled address fingerprint --------------
    query.kind = FL4683_GLYPH_PROGRAM_QUERY_FACT_BY_COMPILED_ADDRESS_SHA256;
    if (!HexTo32(argv[6], query.key_sha256))
        return 2;
    Fl4683GlyphProgramQueryResultV1 by_address =
        Fl4683GlyphProgramQueryExecuteV1(image, &query);
    printf("query_address_found=%u\n", (unsigned)by_address.found);
    printf("query_address_index=%u\n", by_address.fact_index);

    // ---- Query: a fingerprint that is deliberately not in the program --------
    memset(query.key_sha256, 0xAB, sizeof(query.key_sha256));
    query.kind = FL4683_GLYPH_PROGRAM_QUERY_FACT_BY_FACT_ID_SHA256;
    Fl4683GlyphProgramQueryResultV1 unknown_fact =
        Fl4683GlyphProgramQueryExecuteV1(image, &query);
    printf("query_unknown_fact_found=%u\n", (unsigned)unknown_fact.found);
    printf("query_unknown_fact_rejection=%u\n",
        (unsigned)unknown_fact.rejection_reason);

    query.kind = FL4683_GLYPH_PROGRAM_QUERY_FACT_BY_COMPILED_ADDRESS_SHA256;
    Fl4683GlyphProgramQueryResultV1 unknown_address =
        Fl4683GlyphProgramQueryExecuteV1(image, &query);
    printf("query_unknown_address_rejection=%u\n",
        (unsigned)unknown_address.rejection_reason);

    // ---- Query: is a glyph identifier part of the admitted universe? ---------
    query.kind = FL4683_GLYPH_PROGRAM_QUERY_GLYPH_RECORD_ADMITTED;
    query.glyph_record_id = (uint32_t)strtoul(argv[7], NULL, 10);
    Fl4683GlyphProgramQueryResultV1 admitted =
        Fl4683GlyphProgramQueryExecuteV1(image, &query);
    printf("query_admitted_found=%u\n", (unsigned)admitted.found);

    query.glyph_record_id = (uint32_t)strtoul(argv[8], NULL, 10);
    Fl4683GlyphProgramQueryResultV1 unadmitted =
        Fl4683GlyphProgramQueryExecuteV1(image, &query);
    printf("query_unadmitted_found=%u\n", (unsigned)unadmitted.found);
    printf("query_unadmitted_rejection=%u\n",
        (unsigned)unadmitted.rejection_reason);

    // ---- Query: malformed inputs must be refused, not guessed at -------------
    Fl4683GlyphProgramQueryResultV1 null_query =
        Fl4683GlyphProgramQueryExecuteV1(image, NULL);
    printf("query_null_rejection=%u\n", (unsigned)null_query.rejection_reason);

    Fl4683GlyphProgramQueryV1 zeroed;
    memset(&zeroed, 0, sizeof(zeroed));
    zeroed.schema_version = FL4683_GLYPH_PROGRAM_LOADER_SCHEMA_VERSION;
    Fl4683GlyphProgramQueryResultV1 zero_kind =
        Fl4683GlyphProgramQueryExecuteV1(image, &zeroed);
    printf("query_zero_kind_found=%u\n", (unsigned)zero_kind.found);

    Fl4683GlyphProgramQueryResultV1 no_image =
        Fl4683GlyphProgramQueryExecuteV1(NULL, &zeroed);
    printf("query_no_image_rejection=%u\n", (unsigned)no_image.rejection_reason);

    Fl4683GlyphProgramReleaseV1(result.image);
    // Releasing null must be harmless, so cleanup paths need no null checks.
    Fl4683GlyphProgramReleaseV1(NULL);
    printf("released=1\n");
    return 0;
}
"""

# Numeric values of the handshake's rejection reasons, restated here so an
# accidental renumbering in the header shows up as a failing test rather than as
# a silently different meaning.
REJECTION_RUNTIME_RIGHTS_BLOCKED = 1
REJECTION_MISSING_ARTIFACT = 2
REJECTION_UNKNOWN_GLYPH_FACT = 6
REJECTION_MISSING_COMPILED_ADDRESS = 8
REJECTION_UNADMITTED_GLYPH = 10
REJECTION_ADAPTER_ABI_MISMATCH = 14

# Load flag bit values, restated for the same reason.
FLAG_ALLOW_ABSENT_LOCAL_RELATION_CACHE = 1 << 0
FLAG_SKIP_PINNED_INPUT_REHASH = 1 << 1


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
def _receipt() -> Dict:
    """Read the committed pin receipt as a dictionary."""
    return json.loads(RECEIPT.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _compiler_module():
    spec = importlib.util.spec_from_file_location(
        "fl4683_compiler_for_test", COMPILER
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def _fresh_compilation() -> tuple[bytes, Dict]:
    """Compile in memory so v6 tests do not trust a stale guarded receipt."""
    module = _compiler_module()
    sidecar, receipt_bytes = module.compile_all()
    return sidecar, json.loads(receipt_bytes)


def _fresh_receipt() -> Dict:
    return _fresh_compilation()[1]


def test_mesh_gtr_candidate_domain_requires_exact_glyph_identity() -> None:
    module = _compiler_module()
    artifact = json.loads(GLYPH_FACT_ARTIFACT.read_text(encoding="utf-8"))
    mesh_fact = next(
        fact
        for fact in artifact["facts"]
        if fact["scene_membership_key"]["route_key"] == "mesh_feature.bundle.v1"
    )
    mesh_ids = [member["glyph_record_id"] for member in mesh_fact["glyph_members"]]
    module.validate_mesh_gtr_candidate_domain(artifact, {"glyph_ids": mesh_ids})

    mismatched = {"glyph_ids": mesh_ids[:-1] + [max(mesh_ids) + 1]}
    with pytest.raises(
        module.CompileError, match=r"^mesh_gtr_candidate_id_mismatch:fact_index="
    ) as raised:
        module.validate_mesh_gtr_candidate_domain(artifact, mismatched)
    diagnostic = str(raised.value)
    assert "route_key=mesh_feature.bundle.v1" in diagnostic
    assert "fact_count=32:gtr_count=32" in diagnostic
    assert "fact_only=[" in diagnostic
    assert "gtr_only=[" in diagnostic

    duplicated = {"glyph_ids": mesh_ids[:-1] + [mesh_ids[0]]}
    with pytest.raises(module.CompileError) as duplicate_failure:
        module.validate_mesh_gtr_candidate_domain(artifact, duplicated)
    duplicate_diagnostic = str(duplicate_failure.value)
    assert f"gtr_duplicates=[{mesh_ids[0]}]" in duplicate_diagnostic


def test_mesh_gtr_candidate_domain_does_not_constrain_other_routes() -> None:
    module = _compiler_module()
    artifact = json.loads(GLYPH_FACT_ARTIFACT.read_text(encoding="utf-8"))
    mesh_fact = next(
        fact
        for fact in artifact["facts"]
        if fact["scene_membership_key"]["route_key"] == "mesh_feature.bundle.v1"
    )
    mesh_ids = [member["glyph_record_id"] for member in mesh_fact["glyph_members"]]
    other_fact = next(
        fact
        for fact in artifact["facts"]
        if fact["scene_membership_key"]["route_key"] != "mesh_feature.bundle.v1"
        and fact["glyph_members"]
    )
    other_fact["glyph_members"][0]["glyph_record_id"] = max(mesh_ids) + 1000
    module.validate_mesh_gtr_candidate_domain(artifact, {"glyph_ids": mesh_ids})


def _compiler() -> str:
    """Return a usable C++ compiler command, or skip the test if none exists."""
    found = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
    if found is None:
        pytest.skip("no C++ compiler available in this environment")
    return found


def _build_harness(tmp_path: Path, enable_loader: bool) -> Path:
    """Compile the native harness and return the path of the binary.

    Parameters:
      tmp_path      -- pytest's per-test temporary directory.
      enable_loader -- when True, define FL4683_GLYPH_PROGRAM_LOADER_ENABLED so
                       the real loading code is compiled in. When False, the
                       inert stub branch is compiled instead, which is exactly
                       used by non-P6/default harness builds.
    """
    compiler = _compiler()
    suffix = "on" if enable_loader else "off"
    source = tmp_path / f"fl4683_loader_harness_{suffix}.cpp"
    binary = tmp_path / f"fl4683_loader_harness_{suffix}"
    source.write_text(HARNESS_SOURCE, encoding="utf-8")

    command = [
        compiler,
        # C++20 matches CXXFLAGS in makefile_game_mac, so the module is compiled
        # under the same language rules the product build uses.
        "-std=c++20",
        "-O2",
        # Include roots copied from CPPFLAGS in makefile_game_mac.
        "-I.",
        "-Iengine",
        "-Iengine/render",
        "-Iplatform",
    ]
    if enable_loader:
        command.append("-DFL4683_GLYPH_PROGRAM_LOADER_ENABLED=1")
    command += [str(source), str(LOADER_SOURCE), "-o", str(binary)]

    completed = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    return binary


def _run_harness(binary: Path, root: Path, flags: int, **overrides) -> Dict[str, str]:
    """Run the harness once and return its `key=value` output as a dictionary.

    Parameters:
      binary    -- the compiled harness.
      root      -- the repository root the loader should read from. Tests pass a
                   temporary directory here to simulate a damaged or fresh
                   machine without touching the real checkout.
      flags     -- load flags, as an integer.
      overrides -- optional `expected_size` / `expected_sha` replacements, used
                   by the tamper checks.
    """
    receipt = _receipt()
    expected_size = overrides.get("expected_size", receipt["sidecar"]["byte_size"])
    expected_sha = overrides.get("expected_sha", receipt["sidecar"]["sha256"])

    # A known-admitted glyph identifier and one that cannot exist. The admitted
    # universe is built from real character shapes; 4294967295 is the largest
    # possible 32-bit value and is deliberately outside it.
    completed = subprocess.run(
        [
            str(binary),
            str(root),
            str(expected_size),
            str(expected_sha),
            str(flags),
            _known_fact_id_sha(),
            _known_compiled_address_sha(),
            "0",
            "4294967295",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    values: Dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
    return values


# The two look-up keys are computed once from the source artifact and cached,
# because reading the 93 MiB file repeatedly would dominate the test runtime.
_CACHED_KEYS: Dict[str, str] = {}


def _load_keys() -> Dict[str, str]:
    """Compute the fact-identifier and compiled-address fingerprints to look up.

    Reads the FIRST fact out of the source artifact and derives the same two
    keys the offline compiler derives. Because Python computes them here and C++
    matches them there, a passing look-up also confirms the two sides agree
    about how a key is formed.
    """
    if _CACHED_KEYS:
        return _CACHED_KEYS
    artifact = json.loads(GLYPH_FACT_ARTIFACT.read_text(encoding="utf-8"))
    first = artifact["facts"][0]
    _CACHED_KEYS["fact_id_sha"] = hashlib.sha256(
        first["fact_id"].encode("utf-8")
    ).hexdigest()
    _CACHED_KEYS["address_sha"] = first["scene_membership_key"]["row_sha256"]
    _CACHED_KEYS["member_count"] = str(len(first["glyph_members"]))
    _CACHED_KEYS["first_member"] = str(first["glyph_members"][0]["glyph_record_id"])
    _CACHED_KEYS["fact_count"] = str(artifact["fact_count"])
    _CACHED_KEYS["glyph_record_count"] = str(artifact["glyph_record_count"])
    _CACHED_KEYS["content_sha"] = artifact["canonical_content_sha256"]
    _CACHED_KEYS["universe_sha"] = artifact["artifact_identity"][
        "glyph_universe_declared_sha256"
    ]
    return _CACHED_KEYS


def _known_fact_id_sha() -> str:
    return _load_keys()["fact_id_sha"]


def _known_compiled_address_sha() -> str:
    return _load_keys()["address_sha"]


def _expected_gtr_v2_semantic_fnv64() -> str:
    """Fold every committed GTR-v2 semantic field like the native harness."""
    table = json.loads(GTR_V2_ARTIFACT.read_text(encoding="utf-8"))
    value = 14695981039346656037
    mask = (1 << 64) - 1

    def fold_byte(current: int, byte: int) -> int:
        return ((current ^ (byte & 0xFF)) * 1099511628211) & mask

    def fold_word(current: int, word: int, width: int) -> int:
        for shift in range(0, width, 8):
            current = fold_byte(current, word >> shift)
        return current

    for row in table["relations"]:
        value = fold_byte(value, row["aspect"])
        value = fold_word(value, row["from_glyph_id"], 32)
        value = fold_word(value, row["to_glyph_id"], 32)
        for component in row["static_components"]:
            value = fold_byte(value, component)
        for component in row["affine_components"]:
            value = fold_byte(value, component)
        value = fold_word(value, row["relation_confidence_q0_16"], 16)
        value = fold_word(value, row["compiled_transform_cost"], 16)
    return f"{value:016x}"


def _mirror_root(tmp_path: Path, include_local_caches: bool) -> Path:
    """Build a stand-in repository root out of symbolic links.

    Parameters:
      tmp_path             -- pytest's temporary directory.
      include_local_caches -- whether to link the three large regenerable
                              caches into the stand-in root.

    WHY: two of the checks need a repository that is missing its local caches,
    or that contains an altered sidecar. Neither may be done to the real
    checkout. Symbolic links let the stand-in share the real (large) files
    without copying hundreds of megabytes, while still allowing individual
    entries to be left out or replaced.
    """
    root = tmp_path / ("mirror_with_caches" if include_local_caches else "mirror_bare")
    generated = root / "assets/glyphs/generated"
    schema = root / "assets/glyphs/schema"
    generated.mkdir(parents=True, exist_ok=True)
    schema.mkdir(parents=True, exist_ok=True)

    committed = [
        ("assets/glyphs/generated/fl4676.glyph_fact.v1.json", generated),
        ("assets/glyphs/generated/fl4694.relation_pointer.v1.json", generated),
        ("assets/glyphs/generated/fl4683.glyph_program.v1.bin", generated),
        # FL-4686 PA added the address-space artifact as pin role 8. A stand-in
        # root that omitted it would fail with missing_pinned_input before ever
        # reaching the local-cache condition these checks are about.
        ("assets/glyphs/generated/fl4512.compiled_pool.v1.json", generated),
        ("assets/glyphs/generated/fl4512.glyph_transform_relation.v2.json", generated),
        ("assets/glyphs/schema/fl4676.glyph_fact.v1.schema.json", schema),
        ("assets/glyphs/schema/fl4683.scene_fact_handshake.v1.schema.json", schema),
        ("assets/glyphs/schema/fl4694.relation_pointer.v1.schema.json", schema),
        ("assets/glyphs/schema/fl4512.glyph_transform_relation.v2.schema.json", schema),
    ]
    for relative, destination in committed:
        link = destination / Path(relative).name
        if not link.exists():
            link.symlink_to(ROOT / relative)

    if include_local_caches:
        local = generated / "local"
        local.mkdir(parents=True, exist_ok=True)
        for name in (
            "fl4694.glyph_relation_knn.v1.jsonl",
            "fl4694.glyph_ramp.v1.jsonl",
            "fl4694.ramp_record_index.v1.jsonl",
        ):
            link = local / name
            if not link.exists():
                link.symlink_to(ROOT / "assets/glyphs/generated/local" / name)
    return root


def _local_caches_present() -> bool:
    """Are all three regenerable caches present in this checkout right now?"""
    receipt = _receipt()
    for pin in receipt["pins"]:
        if pin["local_regenerable"] and not (ROOT / pin["path"]).is_file():
            return False
    return True


# ===========================================================================
# 1. The offline compiler
# ===========================================================================
def test_sidecar_bytes_match_fresh_compile_and_committed_receipt_is_current() -> None:
    """The guarded v7 artifact and receipt describe the same generated bytes."""
    fresh_sidecar, fresh_receipt = _fresh_compilation()
    assert fresh_sidecar == SIDECAR.read_bytes()
    assert fresh_receipt["version"] == 7
    assert _receipt()["version"] == 7


def test_fresh_v7_receipt_validates_against_its_schema() -> None:
    """The in-memory v7 receipt satisfies the policy-carrying schema."""
    jsonschema.Draft202012Validator(
        json.loads(RECEIPT_SCHEMA.read_text(encoding="utf-8"))
    ).validate(_fresh_receipt())


def test_schema_accepts_a_disabled_future_preview_receipt_without_execution_claim() -> None:
    """A future preview may lack product wiring without reviving v4 key meanings."""
    preview = deepcopy(_fresh_receipt())
    promotion = preview["runtime_rights"]["product_startup_promotion"]
    promotion["enabled"] = False
    promotion["enabled_by"] = []
    loader = preview["runtime_rights"]["loader_call_site_measurement"]
    loader["measured_call_site_count"] = 0
    loader["call_sites"] = []
    selector = preview["runtime_rights"]["selector_call_path"]
    selector["enabled"] = False
    selector["steps"] = []
    jsonschema.Draft202012Validator(
        json.loads(RECEIPT_SCHEMA.read_text(encoding="utf-8"))
    ).validate(preview)


def test_schema_rejects_the_old_ambiguous_runtime_rights_keyset() -> None:
    """v7 must fail closed if the retired sibling fields return."""
    ambiguous = deepcopy(_fresh_receipt())
    ambiguous["runtime_rights"] = {
        "runtime_consumption_allowed": False,
        "blocked_by": "FL-4683",
        "declared_future_rights": [],
        "loader_call_sites_in_live_frame_path": 0,
        "compile_time_gate": "FL4683_GLYPH_PROGRAM_LOADER_ENABLED",
        "compile_time_gate_default": "off",
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(
            json.loads(RECEIPT_SCHEMA.read_text(encoding="utf-8"))
        ).validate(ambiguous)


def test_receipt_records_the_sidecar_actually_on_disk() -> None:
    """The receipt's size and fingerprint must describe the real sidecar file."""
    receipt = _receipt()
    payload = SIDECAR.read_bytes()
    assert receipt["sidecar"]["byte_size"] == len(payload)
    assert receipt["sidecar"]["sha256"] == hashlib.sha256(payload).hexdigest()


def test_receipt_pins_match_every_committed_input_on_disk() -> None:
    """Each committed pin must match the file it names.

    The three regenerable caches are skipped when absent, because absence is a
    defined condition rather than a defect.
    """
    for pin in _receipt()["pins"]:
        path = ROOT / pin["path"]
        if pin["local_regenerable"] and not path.is_file():
            continue
        assert path.is_file(), f"pinned input is missing: {pin['path']}"
        assert path.stat().st_size == pin["byte_size"], pin["path"]
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        assert digest.hexdigest() == pin["sha256"], pin["path"]


def test_v7_receipt_separates_sidecar_promotion_and_static_reachability() -> None:
    """Serialized byte zero is blocked; product promotion and static reachability are separate."""
    historical = _receipt()
    assert historical["version"] == 7
    assert historical["sidecar"]["format_version"] == 7
    assert historical["harri_selector_contract"]["no_match_policy_contract"][
        "threshold_u32_scoping"
    ]["u7.mask.v1"] == "always 0 (unbounded), see bound_units"
    rights = _fresh_receipt()["runtime_rights"]
    assert rights["serialized_sidecar"]["runtime_consumption_allowed"] is False
    assert rights["serialized_sidecar"]["header_byte_offset"] == 160
    assert rights["serialized_sidecar"]["header_byte_value"] == 0
    assert rights["product_startup_promotion"]["enabled"] is True
    assert (
        rights["product_startup_promotion"][
            "runtime_consumption_allowed_after_promotion"
        ]
        is True
    )
    assert rights["loader_call_site_measurement"]["measured_call_site_count"] == 1
    call_sites = rights["loader_call_site_measurement"]["call_sites"]
    assert len(call_sites) == 1
    assert len(call_sites) == rights["loader_call_site_measurement"][
        "measured_call_site_count"
    ]
    assert all(site["enabled_by"] for site in call_sites)
    assert rights["selector_call_path"]["enabled"] is True
    assert len(rights["selector_call_path"]["steps"]) == 2
    assert "blocked_by" not in rights
    assert "runtime_consumption_allowed" not in rights
    assert rights["compile_time_gate"] == "FL4683_GLYPH_PROGRAM_LOADER_ENABLED"
    assert rights["compile_time_gate_default"] == "off"


def test_receipt_records_fl4694_exact_gtr_v2_projection() -> None:
    """The receipt must make E8's exact replacement and non-runtime status explicit."""
    projection = _receipt()["gtr_v2_relation_projection"]
    source = json.loads(GTR_V2_ARTIFACT.read_text(encoding="utf-8"))
    assert projection["owning_fl"] == "FL-4694"
    assert projection["relation_count"] == 65536
    assert projection["exact_parity_rows"] == 65536
    assert projection["mismatch_rows"] == 0
    assert projection["relation_sha256"] == source["relation_sha256"]
    assert projection["disposition"] == "EXACT_GTR_V2_PARITY"
    assert projection["delete_list_entry"] == "E8"
    assert projection["delete_list_disposition"] == (
        "MANDATORY_DELETE_DURING_FL_4693_CUTOVER"
    )
    assert projection["runtime_authority"] is False


# ===========================================================================
# 2. Loading the real data
# ===========================================================================
def test_loader_publishes_the_pinned_program_from_real_artifact_data(
    tmp_path: Path,
) -> None:
    """A full load of the untouched checkout exposes the compiled identities.

    Every pinned input is re-read and re-fingerprinted here, which is roughly
    240 MiB of reading when the local caches are present. That is the point:
    this is the check that nothing on disk has drifted from its pin.
    """
    binary = _build_harness(tmp_path, enable_loader=True)
    keys = _load_keys()
    receipt = _receipt()

    # Permit the regenerable caches to be absent so this check reports the same
    # program on a machine that has never generated them. The resulting status
    # still says which of the two situations occurred.
    values = _run_harness(
        binary, ROOT, FLAG_ALLOW_ABSENT_LOCAL_RELATION_CACHE
    )

    assert values["compiled_in"] == "1"
    assert values["image"] == "present"
    if _local_caches_present():
        assert values["status"] == "loaded"
        assert values["local_relation_cache_available"] == "1"
    else:
        assert values["status"] == "loaded_without_local_relation_cache"
        assert values["local_relation_cache_available"] == "0"

    # Counts must come through the JSON-to-binary conversion unchanged.
    assert values["glyph_record_count"] == keys["glyph_record_count"]
    assert values["fact_count"] == keys["fact_count"]
    assert values["pin_count"] == str(len(receipt["pins"]))

    # The four content identities the handshake requires.
    identity = receipt["program_identity"]
    assert values["artifact_sha256"] == identity["artifact_file_sha256"]
    assert values["artifact_content_sha256"] == keys["content_sha"]
    assert values["glyph_universe_sha256"] == keys["universe_sha"]
    assert values["scene_fact_schema_sha256"] == identity["scene_fact_schema_sha256"]

    # The compiled ceilings the artifact declares.
    assert values["max_fact_members"] == "32"
    assert values["max_relation_edges"] == "32"

    # FL-4694 E8: the public loader finder reaches every canonical address and
    # the semantic fold exactly matches an independent fold of committed JSON.
    assert values["gtr_v2_relation_count"] == "65536"
    assert values["gtr_v2_lookup_mismatches"] == "0"
    assert values["gtr_v2_semantic_fnv64"] == _expected_gtr_v2_semantic_fnv64()
    assert values["gtr_v2_unknown_address"] == "rejected"


def test_query_interface_answers_every_lookup_kind(tmp_path: Path) -> None:
    """The single query entry point resolves facts, addresses, and admission."""
    binary = _build_harness(tmp_path, enable_loader=True)
    keys = _load_keys()
    # Skip re-reading the pinned inputs: this check is about the query answers,
    # and the pin behaviour has its own dedicated checks.
    values = _run_harness(
        binary,
        ROOT,
        FLAG_ALLOW_ABSENT_LOCAL_RELATION_CACHE | FLAG_SKIP_PINNED_INPUT_REHASH,
    )

    # Looking a fact up by the fingerprint of its identifier string.
    assert values["query_fact_by_id_found"] == "1"
    assert values["query_fact_by_id_members"] == keys["member_count"]
    assert values["query_fact_by_id_first_member"] == keys["first_member"]
    # Compiled membership weights are normalised to add up to exactly 65535.
    assert values["query_fact_by_id_weight_sum"] == "65535"
    assert values["query_fact_by_id_weight_total"] == "65535"

    # The same fact, found through its compiled address instead.
    assert values["query_address_found"] == "1"
    assert values["query_address_index"] == values["query_fact_by_id_index"]

    # Absent keys produce named rejections, each naming its own kind of fault.
    assert values["query_unknown_fact_found"] == "0"
    assert int(values["query_unknown_fact_rejection"]) == REJECTION_UNKNOWN_GLYPH_FACT
    assert (
        int(values["query_unknown_address_rejection"])
        == REJECTION_MISSING_COMPILED_ADDRESS
    )

    # Admitted-universe membership.
    assert values["query_admitted_found"] == "1"
    assert values["query_unadmitted_found"] == "0"
    assert int(values["query_unadmitted_rejection"]) == REJECTION_UNADMITTED_GLYPH

    # Malformed calls are refused rather than guessed at.
    assert int(values["query_null_rejection"]) == REJECTION_ADAPTER_ABI_MISMATCH
    assert values["query_zero_kind_found"] == "0"
    assert int(values["query_no_image_rejection"]) == REJECTION_MISSING_ARTIFACT

    # And the memory the image owned was handed back.
    assert values["released"] == "1"


# ===========================================================================
# 3. Tampering
# ===========================================================================
def test_one_altered_byte_in_the_sidecar_produces_a_named_abort(
    tmp_path: Path,
) -> None:
    """Flip a single byte in a copy of the sidecar; the loader must refuse it.

    The altered byte sits inside the fact table, so a loader that skipped the
    fingerprint check would happily return subtly wrong compiled data. The
    expected outcome is a NAMED refusal and no published image at all.
    """
    binary = _build_harness(tmp_path, enable_loader=True)
    root = _mirror_root(tmp_path, include_local_caches=False)

    # Replace the symbolic link with a real, altered copy.
    target = root / "assets/glyphs/generated/fl4683.glyph_program.v1.bin"
    payload = bytearray(SIDECAR.read_bytes())
    # Byte 300 is inside the first fact record, well past the 256-byte header.
    payload[300] ^= 0x01  # XOR with 1 flips exactly one bit
    target.unlink()
    target.write_bytes(bytes(payload))

    values = _run_harness(binary, root, FLAG_ALLOW_ABSENT_LOCAL_RELATION_CACHE)
    assert values["status"] == "sidecar_sha_mismatch"
    assert values["image"] == "absent"


def test_a_truncated_sidecar_is_refused_by_size_before_anything_else(
    tmp_path: Path,
) -> None:
    """A short file is rejected on size, which costs nothing and needs no hash."""
    binary = _build_harness(tmp_path, enable_loader=True)
    root = _mirror_root(tmp_path, include_local_caches=False)
    target = root / "assets/glyphs/generated/fl4683.glyph_program.v1.bin"
    payload = SIDECAR.read_bytes()
    target.unlink()
    target.write_bytes(payload[: len(payload) - 64])

    values = _run_harness(binary, root, FLAG_ALLOW_ABSENT_LOCAL_RELATION_CACHE)
    assert values["status"] == "sidecar_byte_size_mismatch"
    assert values["image"] == "absent"


def test_an_absent_sidecar_is_named_rather_than_crashing(tmp_path: Path) -> None:
    """Pointing the loader at a root with no sidecar yields a named status."""
    binary = _build_harness(tmp_path, enable_loader=True)
    empty_root = tmp_path / "empty_root"
    empty_root.mkdir(exist_ok=True)
    values = _run_harness(binary, empty_root, 0)
    assert values["status"] == "missing_sidecar"
    assert values["image"] == "absent"


def test_a_wrong_expected_fingerprint_is_refused(tmp_path: Path) -> None:
    """If the CALLER's pin is wrong, the untouched sidecar is still refused.

    The caller is the authority for the sidecar's own identity, so a caller that
    supplies the wrong fingerprint must be told, not accommodated.
    """
    binary = _build_harness(tmp_path, enable_loader=True)
    values = _run_harness(
        binary,
        ROOT,
        FLAG_ALLOW_ABSENT_LOCAL_RELATION_CACHE,
        expected_sha="0" * 64,
    )
    assert values["status"] == "sidecar_sha_mismatch"
    assert values["image"] == "absent"


def test_a_missing_committed_input_is_named_as_such(tmp_path: Path) -> None:
    """Remove a pinned committed file; the loader names which one was absent."""
    binary = _build_harness(tmp_path, enable_loader=True)
    root = _mirror_root(tmp_path, include_local_caches=False)
    # Drop the handshake schema, whose pin role number is 2.
    (root / "assets/glyphs/schema/fl4683.scene_fact_handshake.v1.schema.json").unlink()

    values = _run_harness(binary, root, FLAG_ALLOW_ABSENT_LOCAL_RELATION_CACHE)
    assert values["status"] == "missing_pinned_input"
    assert values["failed_pin_role"] == "2"
    assert values["failed_pin_path"].endswith(
        "fl4683.scene_fact_handshake.v1.schema.json"
    )
    assert values["image"] == "absent"


def test_an_altered_committed_input_is_named_as_a_pin_mismatch(
    tmp_path: Path,
) -> None:
    """A pinned committed file that exists but differs is refused by fingerprint."""
    binary = _build_harness(tmp_path, enable_loader=True)
    root = _mirror_root(tmp_path, include_local_caches=False)
    schema_path = root / "assets/glyphs/schema/fl4694.relation_pointer.v1.schema.json"
    original = schema_path.read_bytes()
    schema_path.unlink()
    # Append one space: same length would be tidier, but appending also proves
    # the size check and the fingerprint check are both in force.
    schema_path.write_bytes(original + b" ")

    values = _run_harness(binary, root, FLAG_ALLOW_ABSENT_LOCAL_RELATION_CACHE)
    assert values["status"] == "pinned_input_sha_mismatch"
    assert values["failed_pin_role"] == "4"
    assert values["image"] == "absent"


# ===========================================================================
# 4. Absent regenerable caches
# ===========================================================================
def test_absent_local_caches_are_a_named_unavailable_state_not_an_error(
    tmp_path: Path,
) -> None:
    """A root without the three large caches produces its own named status.

    This is the state of a machine that has cloned the repository but never run
    the FL-4694 cache compiler. It must be distinguishable from corruption.
    """
    binary = _build_harness(tmp_path, enable_loader=True)
    root = _mirror_root(tmp_path, include_local_caches=False)

    # Strict mode: absence stops the load, and names which cache was absent.
    strict = _run_harness(binary, root, 0)
    assert strict["status"] == "local_relation_cache_unavailable"
    assert strict["image"] == "absent"
    # Role 5 is the nearest-neighbour relation cache, the first local pin.
    assert strict["failed_pin_role"] == "5"
    assert strict["failed_pin_path"].endswith("fl4694.glyph_relation_knn.v1.jsonl")

    # Permissive mode: the load proceeds, but under a DISTINCT status, so no
    # caller can mistake it for a whole load.
    permitted = _run_harness(binary, root, FLAG_ALLOW_ABSENT_LOCAL_RELATION_CACHE)
    assert permitted["status"] == "loaded_without_local_relation_cache"
    assert permitted["local_relation_cache_available"] == "0"
    assert permitted["image"] == "present"
    # The compiled content is identical either way; only the cache state differs.
    assert permitted["fact_count"] == _load_keys()["fact_count"]


def test_present_local_caches_report_the_whole_loaded_status(tmp_path: Path) -> None:
    """When all three caches are present and match, the status is the plain one."""
    if not _local_caches_present():
        pytest.skip("local regenerable caches are not present on this machine")
    binary = _build_harness(tmp_path, enable_loader=True)
    root = _mirror_root(tmp_path, include_local_caches=True)
    values = _run_harness(binary, root, 0)
    assert values["status"] == "loaded"
    assert values["local_relation_cache_available"] == "1"


def test_a_present_but_altered_local_cache_is_treated_more_seriously_than_absence(
    tmp_path: Path,
) -> None:
    """A cache that exists but does not match its pin gets its own named status.

    Absence can be repaired by regenerating. A mismatch means something drifted,
    which is a different problem and must not be reported the same way.
    """
    if not _local_caches_present():
        pytest.skip("local regenerable caches are not present on this machine")
    binary = _build_harness(tmp_path, enable_loader=True)
    root = _mirror_root(tmp_path, include_local_caches=True)
    ramp = root / "assets/glyphs/generated/local/fl4694.glyph_ramp.v1.jsonl"
    ramp.unlink()
    # A tiny stand-in file: present, readable, and plainly not the pinned bytes.
    ramp.write_bytes(b"{}\n")

    values = _run_harness(binary, root, FLAG_ALLOW_ABSENT_LOCAL_RELATION_CACHE)
    assert values["status"] == "local_relation_cache_sha_mismatch"
    assert values["failed_pin_role"] == "6"
    assert values["image"] == "absent"


# ===========================================================================
# 5. The three gates
# ===========================================================================
def test_gate_one_the_default_build_contains_no_loading_code(tmp_path: Path) -> None:
    """Compiled WITHOUT the macro, every entry point reports the disabled state.

    This is the branch used by non-P6/default build targets. The five current
    P6 targets define the macro and are covered by the separate source audit.
    """
    binary = _build_harness(tmp_path, enable_loader=False)
    values = _run_harness(binary, ROOT, 0)
    assert values["compiled_in"] == "0"
    assert values["status"] == "loader_disabled_at_compile_time"
    assert values["image"] == "absent"


def test_gate_one_macro_is_defined_only_by_the_p6_product_builds() -> None:
    for path in BUILD_FILES:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert ("FL4683_GLYPH_PROGRAM_LOADER_ENABLED" in text) == (
            path.relative_to(ROOT).as_posix() in P6_PRODUCT_BUILD_NAMES
        ), path.name


def test_gate_two_the_loader_is_linked_only_by_the_p6_product_builds(tmp_path: Path) -> None:
    for path in BUILD_FILES:
        if not path.is_file():
            continue
        text = _expand_product_manifest_selections(path.read_text(encoding="utf-8"))
        assert ("fl4683_glyph_program_loader" in text) == (
            path.relative_to(ROOT).as_posix() in P6_PRODUCT_BUILD_NAMES
        ), path.name


def test_gate_two_no_non_product_frame_source_references_the_loader() -> None:
    """No non-product renderer, presenter, web, or editor source reaches it."""
    for path in LIVE_PATH_SOURCES:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert "fl4683_glyph_program_loader" not in text, path.name
        assert "Fl4683GlyphProgramLoadV1" not in text, path.name
        assert "Fl4683GlyphProgramQueryExecuteV1" not in text, path.name


def test_gate_three_the_handshake_preflight_refuses_the_loaded_program(
    tmp_path: Path,
) -> None:
    """Even a fully loaded program cannot pass the handshake pre-flight.

    This is the gate that matters most: it holds even if someone defines the
    macro, links the file, and loads the sidecar. The published program carries
    `runtime_consumption_allowed = 0`, and the pre-flight refuses on exactly
    that ground.
    """
    binary = _build_harness(tmp_path, enable_loader=True)
    values = _run_harness(
        binary,
        ROOT,
        FLAG_ALLOW_ABSENT_LOCAL_RELATION_CACHE | FLAG_SKIP_PINNED_INPUT_REHASH,
    )
    assert values["image"] == "present"
    assert values["runtime_consumption_allowed"] == "0"
    assert values["runtime_rights"] == "0"
    assert values["preflight_accepted"] == "0"
    assert int(values["preflight_rejection"]) == REJECTION_RUNTIME_RIGHTS_BLOCKED
    # The rights the compiler DECLARED for a future day are carried separately,
    # where they cannot be mistaken for a grant. Both declared bits are set.
    assert values["declared_future_rights"] == "3"


def test_the_loader_defines_no_second_copy_of_the_handshake_types() -> None:
    """GlyphProgramV1 must keep exactly one definition, in the handshake header.

    A second definition would be a parallel owner: two structs with the same
    name that could drift apart silently.
    """
    header = LOADER_HEADER.read_text(encoding="utf-8")
    assert '#include "fl4683_scene_fact_handshake.h"' in header
    assert "struct GlyphProgramV1" not in header
    assert "struct SceneFactV1" not in header
    assert "struct Fl4683SelectorInputV1" not in header


def test_the_loader_reuses_the_existing_sha256_owner() -> None:
    """No second SHA-256 implementation is introduced by this module."""
    source = LOADER_SOURCE.read_text(encoding="utf-8")
    assert '#include "fl4208_surface_identity.h"' in source
    assert "Fl4208Sha256Init" in source
    # A hand-rolled copy would need the algorithm's own round constants; their
    # absence is evidence that no second implementation was added here.
    assert "0x428a2f98" not in source
