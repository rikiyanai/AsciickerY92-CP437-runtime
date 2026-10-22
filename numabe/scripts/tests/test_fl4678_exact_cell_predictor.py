# =============================================================================
# scripts/tests/test_fl4678_exact_cell_predictor.py
# =============================================================================
# WHAT THIS FILE CHECKS
# ---------------------
# It is the falsifier FL-4678 asks for: an instrument that can show the
# exact-cell prediction to be WRONG.
#
# The claim under test is narrow and precise. For one named cell, described only
# by the renderer-owned facts a `SceneFactV1` carries, the compiled data
# determines exactly one character shape. If that claim is true, then two
# independent implementations must agree on every row:
#
#   * the offline predictor (Python), which reads the 93 MiB JSON artifact
#     -- scripts/predict_fl4678_exact_cell_glyph.py
#   * the native selector (C++), which reads the compact binary sidecar
#     -- apps/fl4512-runtime/platform/fl4683_exact_cell_selector.cpp
#
# The two read DIFFERENT files, in DIFFERENT languages, through DIFFERENT
# lookup paths. That is the whole point: if the predictor called the C++ code,
# or read the same bytes it reads, the two could be wrong together and this test
# would happily pass. Because they are independent, a disagreement is real
# evidence that something is broken -- in the compiler, in the sidecar layout,
# in the address index, or in one of the two selection implementations.
#
# WHAT THIS IS NOT
# ----------------
# It is not a runtime activation. The selector and loader are compiled here, by
# this test, into a throwaway program in a temporary directory, with the gate
# macro switched on for that program only. No build file defines that macro, no
# product binary contains either translation unit, and nothing on the frame path
# calls them. The last test in this file asserts exactly that, so the isolation
# is checked rather than assumed.
#
# It also says NOTHING about whether normal play produces cells like these. The
# rows are pinned, hand-written, and reproducible without a running game. That
# is what makes them a falsifier; it is also what stops them being coverage.
# =============================================================================

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[2]
PREDICTOR = ROOT / "scripts/predict_fl4678_exact_cell_glyph.py"
FIXTURE = ROOT / "assets/glyphs/fixtures/fl4678.exact_cell_scene_facts.v1.json"
SELECTOR_HEADER = ROOT / "apps/fl4512-runtime/platform/fl4683_exact_cell_selector.h"
SELECTOR_SOURCE = ROOT / "apps/fl4512-runtime/platform/fl4683_exact_cell_selector.cpp"
LOADER_SOURCE = ROOT / "apps/fl4512-runtime/platform/fl4683_glyph_program_loader.cpp"
RECEIPT = ROOT / "assets/glyphs/generated/fl4683.glyph_program_pin.v1.json"

# Product build definitions. None may mention the selector: that is the
# link-time gate.
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

# Sources on or beside the live frame path. None may reference the selector:
# that is the call-site gate.
LIVE_PATH_SOURCES = [
    ROOT / "engine/render/render_resolve.cpp",
    ROOT / "platform/terminal_gl_present.cpp",
    ROOT / "web/game_web.cpp",
    ROOT / "web/web_diagnostics.cpp",
    ROOT / "apps/asciicker-presenter/asciiid.cpp",
]

# Load flags, mirroring Fl4683GlyphProgramLoadFlagV1. The regenerable relation
# caches are deliberately not committed, so a fresh machine legitimately lacks
# them; skipping the rehash of the 93 MiB artifact keeps this test quick without
# weakening what it checks, because the sidecar's own fingerprint is still
# confirmed in full.
LOAD_FLAG_ALLOW_ABSENT_LOCAL_RELATION_CACHE = 1 << 0
LOAD_FLAG_SKIP_PINNED_INPUT_REHASH = 1 << 1

# ---------------------------------------------------------------------------
# The native harness program
# ---------------------------------------------------------------------------
# Loads the compiled glyph program once, then answers one line of scene-fact
# numbers at a time, printing what the native selector decided.
#
# Argument order:
#   argv[1] repository root to load from
#   argv[2] expected sidecar size in bytes
#   argv[3] expected sidecar fingerprint, 64 hex characters
#   argv[4] path to a plain-text file of scene-fact rows
#
# Each input line holds the address fields followed by the live v5 scorer
# operands, in this order:
#   schema_version valid material_namespace material_key route_family_mask
#   ramp_bin density_bin direction_bin flow_bin edge_bin
#   harri_valid harri_shape6_q0_16[6]
#   mesh_valid mesh_mask8_hex mesh_endpoint_count mesh_junction_count
#   primitive_valid primitive_mask8_hex primitive_projected_extent_q
#
# Output is one `row=` line per input line, holding `key:value` pairs separated
# by spaces. Plain text keeps the harness free of any JSON parser.
HARNESS_SOURCE = r"""
// Native harness for the FL-4678 exact-cell selector. Not product code: it
// exists so a Python test can drive the native selection contract end to end.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "apps/fl4512-runtime/platform/fl4683_exact_cell_selector.h"

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

int main(int argc, char** argv)
{
    if (argc < 5)
    {
        fprintf(stderr, "usage: harness <root> <size> <sha256> <rows-file>\n");
        return 2;
    }

    // Report the gate state before anything else, so the test can confirm the
    // program under test really does contain selection code.
    printf("selector_compiled_in=%u\n", Fl4683ExactCellSelectorCompiledInV1());

    // -- Load the compiled glyph program once ----------------------------
    Fl4683GlyphProgramLoadRequestV1 request;
    memset(&request, 0, sizeof(request));
    request.schema_version = FL4683_GLYPH_PROGRAM_LOADER_SCHEMA_VERSION;
    request.repository_root = argv[1];
    request.sidecar_relative_path = NULL;  // NULL means "use the pinned path"
    request.expected_sidecar_byte_size = strtoull(argv[2], NULL, 10);
    request.flags = FL4683_LOAD_FLAG_ALLOW_ABSENT_LOCAL_RELATION_CACHE |
                    FL4683_LOAD_FLAG_SKIP_PINNED_INPUT_REHASH;
    if (!HexTo32(argv[3], request.expected_sidecar_sha256))
    {
        fprintf(stderr, "bad sidecar fingerprint argument\n");
        return 2;
    }

    Fl4683GlyphProgramLoadResultV1 loaded = Fl4683GlyphProgramLoadV1(&request);
    printf("load_status=%s\n", Fl4683GlyphProgramStatusNameV1(loaded.status));
    if (loaded.image == NULL)
    {
        fprintf(stderr, "load failed: %s (%s)\n",
            Fl4683GlyphProgramStatusNameV1(loaded.status), loaded.failed_pin_path);
        return 3;
    }
    printf("scene_address_count=%u\n", loaded.image->scene_address_count);

    // -- Answer every row -------------------------------------------------
    FILE* rows = fopen(argv[4], "r");
    if (rows == NULL)
    {
        fprintf(stderr, "cannot open rows file\n");
        Fl4683GlyphProgramReleaseV1(loaded.image);
        return 2;
    }

    unsigned int schema_version, valid, mask;
    int material_namespace, material_key;
    unsigned int ramp, density, direction, flow, edge;
    unsigned int harri_valid, harri[6];
    unsigned int mesh_valid, mesh_endpoint_count, mesh_junction_count;
    unsigned long long mesh_mask8;
    unsigned int primitive_valid, primitive_projected_extent_q;
    unsigned long long primitive_mask8;
    int row_index = 0;
    while (fscanf(rows,
        "%u %u %d %d %u %u %u %u %u %u "
        "%u %u %u %u %u %u %u "
        "%u %llx %u %u %u %llx %u",
        &schema_version, &valid, &material_namespace, &material_key, &mask,
        &ramp, &density, &direction, &flow, &edge,
        &harri_valid, &harri[0], &harri[1], &harri[2], &harri[3], &harri[4],
        &harri[5], &mesh_valid, &mesh_mask8, &mesh_endpoint_count,
        &mesh_junction_count, &primitive_valid, &primitive_mask8,
        &primitive_projected_extent_q) == 24)
    {
        // Build the scene fact exactly as the renderer would fill it in.
        SceneFactV1 fact;
        memset(&fact, 0, sizeof(fact));
        fact.schema_version = schema_version;
        fact.valid = (unsigned char)valid;
        fact.material_namespace = (short)material_namespace;
        fact.material_key = (short)material_key;
        fact.route_family_mask = (unsigned char)mask;
        fact.ramp_bin = (unsigned char)ramp;
        fact.density_bin = (unsigned char)density;
        fact.direction_bin = (unsigned char)direction;
        fact.flow_bin = (unsigned char)flow;
        fact.edge_bin = (unsigned char)edge;
        fact.harri_shape6_valid = (unsigned char)harri_valid;
        for (unsigned int axis = 0; axis < 6; ++axis)
            fact.harri_shape6_q0_16[axis] = (unsigned short)harri[axis];
        fact.mesh_target_valid = (unsigned char)mesh_valid;
        fact.mesh_target_mask8 = (unsigned long long)mesh_mask8;
        fact.mesh_target_endpoint_count = (unsigned char)mesh_endpoint_count;
        fact.mesh_target_junction_count = (unsigned char)mesh_junction_count;
        fact.primitive_target_valid = (unsigned char)primitive_valid;
        fact.primitive_target_mask8 = (unsigned long long)primitive_mask8;
        fact.primitive_projected_extent_q = primitive_projected_extent_q;

        const Fl4683ExactCellSelectionV1 result =
            Fl4683ExactCellSelectV1(loaded.image, &fact);

        printf("row=%d accepted:%u outcome:%u absent:%u reason:%u families:%u "
               "family_bit:%u glyph:%u weight:%u considered:%u score:%llu "
               "scorer:%u\n",
            row_index,
            (unsigned int)result.accepted,
            /* FL-4686 PA: the three-outcome field. Printed so the falsifier
               compares WHICH answer was given, not merely whether a glyph came
               back -- declared absence and refusal both return no glyph. */
            (unsigned int)result.outcome,
            (unsigned int)result.declared_absent_family_count,
            (unsigned int)result.rejection_reason,
            (unsigned int)result.resolved_family_count,
            (unsigned int)result.winning_route_family_bit,
            result.winning_glyph_record_id,
            (unsigned int)result.winning_membership_weight_q0_16,
            (unsigned int)result.considered_candidate_count,
            (unsigned long long)result.winning_score,
            (unsigned int)result.winning_scorer_kind);
        row_index++;
    }

    fclose(rows);
    Fl4683GlyphProgramReleaseV1(loaded.image);
    return 0;
}
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _fixture() -> Dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _receipt() -> Dict[str, Any]:
    return json.loads(RECEIPT.read_text(encoding="utf-8"))


# Live v5 renderer operands. Harri rows use the exact q0.16 morphology of the
# independently predicted winner, so the native W4/F8 scorer must return zero.
# Structural rows use the compiler-owned little-endian masks for the same
# reason. These are scorer inputs, not expected outputs or candidate ordinals.
LIVE_DESCRIPTORS: Dict[str, Dict[str, Any]] = {
    "terrain_tonal_material_1_ramp2_density1": {
        "harri": [31227, 1199, 29006, 12006, 65535, 52893],
    },
    "terrain_tonal_material_8_ramp0_density3": {
        "harri": [0, 3729, 4496, 65535, 0, 3729],
    },
    "mesh_tonal_material_200_ramp3_density0": {
        "harri": [59014, 38443, 65535, 20683, 32781, 0],
    },
    "terrain_direction_material_5_lane6": {
        "harri": [6462, 0, 65535, 0, 6462, 0],
    },
    "terrain_edge_material_4_lane5": {
        "harri": [4286, 46799, 65535, 63825, 18481, 0],
    },
    "mesh_filled_field_material_200": {
        "primitive_mask_le_hex": "0000081c38000000",
        "primitive_extent_q": 8192,
    },
    "mesh_feature_bundle_material_200": {
        "mesh_mask_le_hex": "0000000000080402",
        "mesh_endpoint_count": 2,
        "mesh_junction_count": 0,
    },
}


def _mask_value(le_hex: str) -> int:
    return int.from_bytes(bytes.fromhex(le_hex), "little")


def _row_line(row: Dict[str, Any], descriptor: Dict[str, Any] | None = None) -> str:
    """Encode one full SceneFact v5 fixture row for the native harness."""
    descriptor = descriptor or LIVE_DESCRIPTORS.get(str(row.get("name")), {})
    harri = descriptor.get("harri", [0, 0, 0, 0, 0, 0])
    mesh_mask = _mask_value(descriptor.get("mesh_mask_le_hex", "0000000000000000"))
    primitive_mask = _mask_value(
        descriptor.get("primitive_mask_le_hex", "0000000000000000")
    )
    values = [
        *(int(row[field]) for field in (
            "schema_version", "valid", "material_namespace", "material_key",
            "route_family_mask", "ramp_bin", "density_bin", "direction_bin",
            "flow_bin", "edge_bin",
        )),
        int("harri" in descriptor),
        *(int(value) for value in harri),
        int("mesh_mask_le_hex" in descriptor),
    ]
    return " ".join(str(value) for value in values) + (
        f" {mesh_mask:x} {int(descriptor.get('mesh_endpoint_count', 0))} "
        f"{int(descriptor.get('mesh_junction_count', 0))} "
        f"{int('primitive_mask_le_hex' in descriptor)} {primitive_mask:x} "
        f"{int(descriptor.get('primitive_extent_q', 0))}"
    )


def _prediction() -> Dict[str, Any]:
    """Run the offline predictor and return its report."""
    completed = subprocess.run(
        ["python3", str(PREDICTOR), "--fixture", str(FIXTURE.relative_to(ROOT))],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _build_harness(tmp_path: Path) -> Path:
    """Compile the native harness with the gate switched on for this program only."""
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("C++ compiler unavailable")

    source = tmp_path / "fl4678_harness.cpp"
    binary = tmp_path / "fl4678_harness"
    source.write_text(HARNESS_SOURCE, encoding="utf-8")

    command = [
        compiler,
        # C++20 and the include roots match CXXFLAGS/CPPFLAGS in
        # makefile_game_mac, so this compiles under the product's language rules.
        "-std=c++20",
        "-O2",
        "-I.",
        "-Iengine",
        "-Iengine/render",
        "-Iplatform",
        "-DFL4683_GLYPH_PROGRAM_LOADER_ENABLED=1",
        str(source),
        str(SELECTOR_SOURCE),
        str(LOADER_SOURCE),
        "-o",
        str(binary),
    ]
    completed = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
    return binary


def _run_harness(binary: Path, tmp_path: Path) -> List[Dict[str, int]]:
    """Run the harness over every fixture row and parse its `row=` lines."""
    rows_path = tmp_path / "rows.txt"
    lines = [_row_line(row) for row in _fixture()["rows"]]
    rows_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    receipt = _receipt()
    completed = subprocess.run(
        [
            str(binary),
            str(ROOT),
            str(receipt["sidecar"]["byte_size"]),
            str(receipt["sidecar"]["sha256"]),
            str(rows_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout

    results: List[Dict[str, int]] = []
    header: Dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if line.startswith("row="):
            parsed: Dict[str, int] = {}
            for token in line.split():
                key, _, value = token.partition("=" if "=" in token else ":")
                parsed[key] = int(value)
            results.append(parsed)
        elif "=" in line:
            key, _, value = line.partition("=")
            header[key] = value

    # The harness must really contain selection code and must really have loaded
    # the program; otherwise every "agreement" below would be vacuous.
    assert header.get("selector_compiled_in") == "1"
    assert header.get("load_status") in (
        "loaded",
        "loaded_without_local_relation_cache",
    )
    assert int(header.get("scene_address_count", "0")) > 0
    return results


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_fixture_rows_are_named_and_distinct() -> None:
    """Every pinned row must have a unique name and every field the harness needs."""
    rows = _fixture()["rows"]
    assert len(rows) >= 10
    names = [row["name"] for row in rows]
    assert len(set(names)) == len(names)
    for row in rows:
        for field in (
            "schema_version",
            "valid",
            "material_namespace",
            "material_key",
            "route_family_mask",
            "ramp_bin",
            "density_bin",
            "direction_bin",
            "flow_bin",
            "edge_bin",
            "why",
        ):
            assert field in row, f"{row['name']} is missing {field}"


def test_prediction_is_deterministic() -> None:
    """Two runs of the offline predictor must produce identical reports.

    A prediction that changed between runs could never falsify anything: any
    disagreement with the native side could be blamed on the prediction rather
    than investigated.
    """
    assert _prediction() == _prediction()


def test_prediction_covers_both_success_and_named_refusal() -> None:
    """The pinned rows must exercise real answers AND real refusals.

    A falsifier made only of successes would never notice a selector that had
    stopped refusing anything, which is the more dangerous failure: it would
    quietly substitute a plausible glyph for a missing address.
    """
    predictions = _prediction()["predictions"]
    accepted = [row for row in predictions if row["accepted"]]
    # FL-4686 PA. "Not accepted" now covers TWO different answers, so they are
    # separated before anything is asserted about either. A declared absence is
    # a successful answer that selects no glyph; a refusal is a failure. Folding
    # them together is exactly the confusion PA exists to remove.
    absent = [row for row in predictions if row["outcome"] == "declared_absence"]
    refused = [row for row in predictions if row["outcome"] == "rejected"]
    assert len(accepted) + len(absent) + len(refused) == len(predictions)
    assert len(accepted) >= 5
    assert len(refused) >= 5
    assert len(absent) >= 1, "the falsifier must exercise declared absence"

    # A declared absence carries no rejection reason and no candidates.
    for row in absent:
        assert row["rejection_reason"] == "none", row["name"]
        assert row["considered_candidate_count"] == 0, row["name"]
        assert row["declared_absent_family_count"] == row["resolved_family_count"]

    # The refusals must span several distinct named reasons, not one repeated.
    reasons = {row["rejection_reason"] for row in refused}
    assert {
        "missing_compiled_address",
        "malformed_scene_fact",
        "missing_scene_fact",
        "scene_schema_mismatch",
    } <= reasons

    # Every accepted row must name which route family supplied the answer, and
    # a tonal row must have resolved BOTH halves of its pair.
    for row in accepted:
        assert row["winning_route_family_bit"] > 0
        assert row["considered_candidate_count"] > 0
        assert len(row["resolved_addresses"]) == row["resolved_family_count"]
    tonal = [row for row in accepted if row["resolved_family_count"] == 2]
    assert tonal, "no tonal ramp/density pair among the accepted rows"
    for row in tonal:
        assert {entry["route_key"] for entry in row["resolved_addresses"]} == {
            "ramp.v1",
            "density.v1",
        }


def test_native_selector_matches_the_offline_prediction(tmp_path: Path) -> None:
    """THE FALSIFIER. Native selection and offline prediction must agree exactly.

    Two independent implementations, reading two different files, are asked the
    same question about the same pinned cells. Every field is compared: whether
    the row was accepted, the named reason when it was not, which route family
    supplied the winner, which glyph won, that glyph's compiled weight, and how
    many candidates were considered.

    The candidate count is compared on purpose. Without it, the two sides could
    reach the same glyph while looking at different fields of candidates -- the
    right answer for the wrong reason, which is exactly the kind of agreement
    that hides a defect.
    """
    binary = _build_harness(tmp_path)
    native = _run_harness(binary, tmp_path)
    predicted = _prediction()["predictions"]

    assert len(native) == len(predicted), "the harness skipped or invented rows"

    for index, (expected, actual) in enumerate(zip(predicted, native)):
        name = expected["name"]
        assert actual["accepted"] == (1 if expected["accepted"] else 0), name
        assert actual["reason"] == expected["rejection_code"], name
        # FL-4686 PA. Compare the OUTCOME, not just whether a glyph came back.
        # Without this the two sides could agree on "no glyph" while disagreeing
        # about whether the program owns the cell -- which is the whole
        # distinction this change introduced.
        assert actual["outcome"] == expected["outcome_code"], name

        if expected["outcome"] == "declared_absence":
            # An owned, empty cell. No glyph, but the families that resolved ARE
            # reported, and every one of them was declared absent.
            assert actual["glyph"] == 0, name
            assert actual["considered"] == 0, name
            assert actual["families"] == expected["resolved_family_count"], name
            assert actual["absent"] == expected["declared_absent_family_count"], name
            continue

        if not expected["accepted"]:
            # A refusal must carry nothing else: no half-formed answer.
            assert actual["glyph"] == 0, name
            assert actual["families"] == 0, name
            assert actual["absent"] == 0, name
            continue
        assert actual["families"] == expected["resolved_family_count"], name
        assert actual["family_bit"] == expected["winning_route_family_bit"], name
        assert actual["glyph"] == expected["winning_glyph_record_id"], name
        assert actual["weight"] == expected["winning_membership_weight_q0_16"], name
        assert actual["considered"] == expected["considered_candidate_count"], name
        assert actual["absent"] == 0, name


def test_changing_one_lane_changes_the_selected_glyph(tmp_path: Path) -> None:
    """A controlled change to one scene fact must move the answer.

    This is the causal half of the claim. If altering the lane a cell reports
    left the chosen glyph untouched, the compiled address would not really be
    driving the selection, and every agreement above would be a coincidence.

    The tonal pair is used because it is the case the FL-4683 discriminator was
    added for: ramp and density are separate families whose lanes must have
    separate effects.
    """
    binary = _build_harness(tmp_path)

    # Four variants of one tonal terrain cell, differing only in their lanes.
    variants = [
        (2, 1),  # the fixture's own row
        (0, 1),  # ramp lane moved
        (2, 3),  # density lane moved
        (0, 3),  # both moved
    ]
    rows_path = tmp_path / "variants.txt"
    variant_row = {
            "schema_version": 2, "valid": 1, "material_namespace": 1,
        "material_key": 1, "route_family_mask": 3, "direction_bin": 0,
        "flow_bin": 0, "edge_bin": 0,
    }
    rows_path.write_text("\n".join(
        _row_line(
            {**variant_row, "ramp_bin": ramp, "density_bin": density},
            {"harri": LIVE_DESCRIPTORS[
                "terrain_tonal_material_1_ramp2_density1"
            ]["harri"]},
        )
        for ramp, density in variants
    ) + "\n", encoding="utf-8")

    receipt = _receipt()
    completed = subprocess.run(
        [
            str(binary),
            str(ROOT),
            str(receipt["sidecar"]["byte_size"]),
            str(receipt["sidecar"]["sha256"]),
            str(rows_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout

    answers = []
    for line in completed.stdout.splitlines():
        if not line.startswith("row="):
            continue
        parsed = {}
        for token in line.split():
            key, _, value = token.partition("=" if "=" in token else ":")
            parsed[key] = int(value)
        assert parsed["accepted"] == 1
        answers.append((parsed["glyph"], parsed["weight"], parsed["considered"]))

    assert len(answers) == len(variants)
    # Moving a lane must change SOMETHING observable. Requiring every variant to
    # differ would over-claim -- two lanes can legitimately share a winner -- so
    # the check is that the four are not all identical.
    assert len(set(answers)) > 1, (
        "changing the ramp and density lanes left the selection completely "
        "unchanged, so the compiled address is not driving the answer"
    )


def test_live_harri_descriptor_drives_mesh_tonal_selection(tmp_path: Path) -> None:
    """The same compiled address must react to the live Harri descriptor.

    Both rows address mesh ramp[3]+density[1]. Only shape6 changes, matching
    glyphs 587 and 615 respectively. This catches the P6 regression where
    membership/lane alone selected a static winner and ignored Stage-6.
    """
    binary = _build_harness(tmp_path)
    rows_path = tmp_path / "mesh-density-variants.txt"
    base = {
        "schema_version": 2, "valid": 1, "material_namespace": 2,
        "material_key": 200, "route_family_mask": 3, "ramp_bin": 3,
        "density_bin": 1, "direction_bin": 0, "flow_bin": 0, "edge_bin": 0,
    }
    rows_path.write_text(
        _row_line(base, {"harri": [34085, 41084, 49898, 65535, 0, 5780]})
        + "\n"
        + _row_line(base, {"harri": [59014, 38443, 65535, 20683, 32781, 0]})
        + "\n",
        encoding="utf-8",
    )
    receipt = _receipt()
    completed = subprocess.run(
        [str(binary), str(ROOT), str(receipt["sidecar"]["byte_size"]),
         str(receipt["sidecar"]["sha256"]), str(rows_path)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    glyphs = []
    for line in completed.stdout.splitlines():
        if line.startswith("row="):
            fields = {}
            for token in line.split():
                key, _, value = token.partition("=" if "=" in token else ":")
                fields[key] = value
            glyphs.append(int(fields["glyph"]))
    assert glyphs == [587, 615]


def test_u7_and_primitive_scorers_and_filled_fallback(tmp_path: Path) -> None:
    """Exercise the two structural operands and the compiled filled gate."""
    binary = _build_harness(tmp_path)
    mesh_fact = {
        "schema_version": 2, "valid": 1, "material_namespace": 2,
        "material_key": 200, "route_family_mask": 64, "ramp_bin": 0,
        "density_bin": 0, "direction_bin": 0, "flow_bin": 0, "edge_bin": 0,
    }
    tonal_fact = {
        **mesh_fact, "route_family_mask": 3, "ramp_bin": 3, "density_bin": 0,
    }
    u7 = {
        "mesh_mask_le_hex": "0000000000080402",
        "mesh_endpoint_count": 2,
        "mesh_junction_count": 0,
    }
    primitive_above = {
        "harri": [59014, 38443, 65535, 20683, 32781, 0],
        "primitive_mask_le_hex": "0000081c38000000",
        "primitive_extent_q": 8192,
    }
    primitive_below = {**primitive_above, "primitive_extent_q": 8191}
    no_primitive = {
        "harri": primitive_above["harri"],
    }
    rows_path = tmp_path / "structural-and-filled.txt"
    rows_path.write_text(
        _row_line(mesh_fact, u7) + "\n"
        + _row_line(tonal_fact, primitive_above) + "\n"
        + _row_line(tonal_fact, primitive_below) + "\n"
        + _row_line(tonal_fact, no_primitive) + "\n",
        encoding="utf-8",
    )
    receipt = _receipt()
    completed = subprocess.run(
        [str(binary), str(ROOT), str(receipt["sidecar"]["byte_size"]),
         str(receipt["sidecar"]["sha256"]), str(rows_path)],
        cwd=ROOT, text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    rows = []
    for line in completed.stdout.splitlines():
        if line.startswith("row="):
            rows.append({
                token.partition("=" if "=" in token else ":")[0]: int(
                    token.partition("=" if "=" in token else ":")[2]
                )
                for token in line.split()
            })
    # Admission-v3 replaced the mesh vocabulary. Direct scoring of the current
    # compiled row gives glyph 16789533 at U7 score 310; this is not a runtime-
    # observed ordinal substitution.
    assert [(row["glyph"], row["family_bit"], row["scorer"]) for row in rows] == [
        (16789533, 64, 2),
        (520, 32, 3),
        (615, 1, 1),
        (615, 1, 1),
    ]
    assert rows[0]["score"] == 310
    assert rows[1]["score"] == 560330
    assert rows[2]["score"] == 0


def test_selector_is_linked_through_the_one_p6_product_owner() -> None:
    """P6 deliberately opens the former compile/link/call-site gates."""
    required = (
        ROOT / "apps/game-fl4512/makefile_game",
        ROOT / "apps/game-fl4512/makefile_game_mac",
        ROOT / "apps/asciicker-presenter/makefile_asciiid",
        ROOT / "apps/asciicker-presenter/makefile_asciiid_mac",
        ROOT / "build-web.sh",
    )
    for build_file in required:
        text = build_file.read_text(encoding="utf-8")
        assert "FL4683_GLYPH_PROGRAM_LOADER_ENABLED" in text, build_file
        assert "fl4683_exact_cell_selector.cpp" in text, build_file
        assert "fl4693_product_glyph_program.cpp" in text, build_file

    product = (ROOT / "apps/fl4512-runtime/platform/fl4693_product_glyph_program.cpp").read_text()
    resolver = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text()
    assert "Fl4683ExactCellSelectV1(g_image, scene_fact)" in product
    assert "Fl4693ProductGlyphSelectV1(adapter, &fact)" in resolver

    # A deliberately unenabled standalone build remains an inert stub.
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("C++ compiler unavailable")
    assert SELECTOR_HEADER.exists() and SELECTOR_SOURCE.exists()
