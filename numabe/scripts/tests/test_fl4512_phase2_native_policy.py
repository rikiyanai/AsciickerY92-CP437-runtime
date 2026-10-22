# =============================================================================
# scripts/tests/test_fl4512_phase2_native_policy.py
# =============================================================================
# FL-4512 Phase 2, native side of the role-correct abstention contract.
#
# WHAT IS UNDER TEST
# ------------------
# Two C++ translation units, compiled here into a throwaway harness exactly the
# way scripts/tests/test_fl4678_exact_cell_predictor.py does it (same flags,
# same gate macro, no product build touched, no game launched):
#
#   apps/fl4512-runtime/platform/fl4683_glyph_program_loader.cpp   -- must REJECT malformed policy
#   apps/fl4512-runtime/platform/fl4683_exact_cell_selector.cpp    -- must OBEY well-formed policy
#
# HOW THE TEST IMAGES ARE MADE
# ----------------------------
# The loader validates pins, section geometry, and a payload fingerprint, so a
# "crafted" program image cannot be invented from nothing. Instead each image
# is derived from the committed sidecar by a small, named set of byte
# mutations, after which the header's payload fingerprint is recomputed so the
# image is VALID BY CONSTRUCTION except for exactly the property each test
# wants to exercise:
#
#   * the header format version is set to 6 (the fact-record layout is
#     byte-identical between v5 and v6; only the meaning of the former padding
#     byte and the trailing u32 moved, which is why the version had to move);
#   * the compiler-owned EDGE fact remains an operand-free kind-4 decline;
#   * malformed variants add forbidden policy bytes to named scorer kinds;
#   * for the absence scenario, two tonal index entries are flipped to
#     DECLARED_ABSENT and, to keep the loader's populated == fact_count
#     equation true, two never-queried absent entries are flipped to populated
#     pointing at the two orphaned facts.
#
# The committed format-6 bin is the valid base. Each malformed image changes
# one named policy property and recomputes the payload fingerprint, so a loader
# rejection isolates that property instead of rejecting unrelated stale bytes.
#
# Run with:
#     python3 -m pytest scripts/tests/test_fl4512_phase2_native_policy.py -q
# =============================================================================

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[2]
SIDECAR = ROOT / "assets/glyphs/generated/fl4683.glyph_program.v1.bin"
SELECTOR_SOURCE = ROOT / "apps/fl4512-runtime/platform/fl4683_exact_cell_selector.cpp"
LOADER_SOURCE = ROOT / "apps/fl4512-runtime/platform/fl4683_glyph_program_loader.cpp"

# ---------------------------------------------------------------------------
# Sidecar byte geometry (mirrors the compiler and the loader header)
# ---------------------------------------------------------------------------
OFF_FORMAT_VERSION = 8
OFF_FACT_COUNT = 148
OFF_FACT_TABLE_OFFSET = 164
OFF_PAYLOAD_SIZE = 196
OFF_PAYLOAD_SHA = 200
OFF_SCENE_ADDRESS_OFFSET = 232
OFF_SCENE_ADDRESS_COUNT = 236
HEADER_SIZE = 256

FACT_RECORD_SIZE = 128
# Within one 128-byte fact record (format v6):
FACT_SCORER_KIND = 112
FACT_POLICY_FLAGS = 119
FACT_THRESHOLD_U32 = 124

SCENE_ADDRESS_RECORD_SIZE = 12
SCENE_ADDRESS_STRUCT = "<hhBBBBI"

DISPOSITION_POPULATED = 1
DISPOSITION_DECLARED_ABSENT = 2
NO_FACT_INDEX = 0xFFFFFFFF

POLICY_DEGENERATE_OPERAND_ABSTAIN = 0x1
SCORER_STRUCTURAL_DECLINE = 4

# Route-family bits, as in platform/fl4683_scene_fact_record.h.
FAMILY_RAMP = 1
FAMILY_DENSITY = 2
FAMILY_EDGE = 4
FAMILY_DIRECTION = 8
FAMILY_MESH_BUNDLE = 64
TONAL_PAIR_MASK = FAMILY_RAMP | FAMILY_DENSITY

# Selector outcome codes, as in apps/fl4512-runtime/platform/fl4683_exact_cell_selector.h.
OUTCOME_REJECTED = 0
OUTCOME_SELECTED = 1
OUTCOME_DECLARED_ABSENCE = 2

# The addresses this file drives, all real addresses of the committed pool:
#   terrain material 4 edge[5]      -- the structural row under policy
#   terrain material 4 ramp[2]/density[1] -- the populated tonal fallback pair
#   terrain material 4 ramp[3]/density[3] -- flipped to declared absence
#   terrain material 4 direction[6]/[7]   -- absent, never queried; they take
#                                            the two orphaned fact indices so
#                                            populated == fact_count holds
#   mesh material 200 mesh bundle   -- the single u7 row
EDGE_KEY = (1, 4, FAMILY_EDGE, 5)
TONAL_LIVE_KEYS = ((1, 4, FAMILY_RAMP, 2), (1, 4, FAMILY_DENSITY, 1))
TONAL_FLIPPED_KEYS = ((1, 4, FAMILY_RAMP, 3), (1, 4, FAMILY_DENSITY, 3))
ORPHAN_TAKEOVER_KEYS = ((1, 4, FAMILY_DIRECTION, 6), (1, 4, FAMILY_DIRECTION, 7))
U7_KEY = (2, 200, FAMILY_MESH_BUNDLE, 0)


# ---------------------------------------------------------------------------
# Image crafting
# ---------------------------------------------------------------------------
def read_u32(blob: bytes, offset: int) -> int:
    return struct.unpack_from("<I", blob, offset)[0]


def parse_index(blob: bytes) -> Dict[Tuple[int, int, int, int], Tuple[int, int, int]]:
    """Map every address key to (disposition, fact_index, entry byte offset)."""
    base = read_u32(blob, OFF_SCENE_ADDRESS_OFFSET)
    count = read_u32(blob, OFF_SCENE_ADDRESS_COUNT)
    entries = {}
    for i in range(count):
        offset = base + i * SCENE_ADDRESS_RECORD_SIZE
        ns, mat, bit, lane, disp, _pad, fact = struct.unpack_from(
            SCENE_ADDRESS_STRUCT, blob, offset
        )
        entries[(ns, mat, bit, lane)] = (disp, fact, offset)
    return entries


def populated_fact_index(index, key) -> int:
    disp, fact, _off = index[key]
    assert disp == DISPOSITION_POPULATED, f"{key} is not populated in the base bin"
    return fact


def set_fact_policy(blob: bytearray, fact_index: int, flags: int, bound: int) -> None:
    base = read_u32(blob, OFF_FACT_TABLE_OFFSET) + fact_index * FACT_RECORD_SIZE
    blob[base + FACT_POLICY_FLAGS] = flags
    struct.pack_into("<I", blob, base + FACT_THRESHOLD_U32, bound)


def fact_scorer_kind(blob: bytes, fact_index: int) -> int:
    base = read_u32(blob, OFF_FACT_TABLE_OFFSET) + fact_index * FACT_RECORD_SIZE
    return blob[base + FACT_SCORER_KIND]


def set_entry_disposition(
    blob: bytearray, index, key, disposition: int, fact_index: int
) -> None:
    _disp, _fact, offset = index[key]
    # disposition is byte 6 of the 12-byte entry, fact index is bytes 8..12.
    blob[offset + 6] = disposition
    struct.pack_into("<I", blob, offset + 8, fact_index)


def finalize(blob: bytearray) -> bytes:
    """Recompute the header's payload fingerprint after mutations."""
    payload = bytes(blob[HEADER_SIZE:])
    assert len(payload) == read_u32(blob, OFF_PAYLOAD_SIZE)
    blob[OFF_PAYLOAD_SHA:OFF_PAYLOAD_SHA + 32] = hashlib.sha256(payload).digest()
    return bytes(blob)


def base_image() -> bytearray:
    assert SIDECAR.is_file(), (
        "the committed sidecar is missing; run "
        "python3 scripts/compile_fl4683_glyph_program.py --write"
    )
    blob = bytearray(SIDECAR.read_bytes())
    # The fact-record layout is byte-identical between v5 and v6, so a v5 base
    # becomes a legal v6 image once its policy bytes are set deliberately.
    struct.pack_into("<I", blob, OFF_FORMAT_VERSION, 6)
    return blob


def craft_policy_image() -> bytes:
    """The one valid phase-2 image every selector scenario runs against."""
    blob = base_image()
    index = parse_index(blob)

    edge_fact = populated_fact_index(index, EDGE_KEY)
    # FL-4763: EDGE no longer borrows the tonal shape6 scorer. The compiler
    # emits an operand-free explicit decline, so adding a policy flag or bound
    # would make the image malformed. Keep the fact byte-identical and assert
    # that this fixture is exercising the compiler-owned kind-4 contract.
    assert fact_scorer_kind(blob, edge_fact) == SCORER_STRUCTURAL_DECLINE

    # The tonal fallback pair: never flagged, unbounded. Set explicitly so the
    # scenario holds regardless of what bounds the base bin carries.
    for key in TONAL_LIVE_KEYS:
        set_fact_policy(blob, populated_fact_index(index, key), 0, 0)

    # The absence scenario needs a tonal pair the program declares EMPTY. The
    # committed pool populates every tonal lane, so two lanes are flipped to
    # declared absence here -- and, to keep the loader's
    # populated == fact_count equation true, two never-queried absent
    # direction entries take over the two orphaned fact indices.
    orphans: List[int] = []
    for key in TONAL_FLIPPED_KEYS:
        orphans.append(populated_fact_index(index, key))
        set_entry_disposition(
            blob, index, key, DISPOSITION_DECLARED_ABSENT, NO_FACT_INDEX
        )
    for key, orphan in zip(ORPHAN_TAKEOVER_KEYS, orphans):
        disp, _fact, _off = index[key]
        assert disp == DISPOSITION_DECLARED_ABSENT, (
            f"{key} is not declared absent in the base bin"
        )
        set_entry_disposition(blob, index, key, DISPOSITION_POPULATED, orphan)

    return finalize(blob)


def craft_unknown_policy_bit_image() -> bytes:
    """A policy byte carrying a bit this format does not name."""
    blob = base_image()
    index = parse_index(blob)
    set_fact_policy(blob, populated_fact_index(index, EDGE_KEY), 0x2, 0)
    return finalize(blob)


def craft_u7_flagged_image() -> bytes:
    """The degenerate-operand flag on the U7 mask row, which is illegal."""
    blob = base_image()
    index = parse_index(blob)
    u7_fact = populated_fact_index(index, U7_KEY)
    assert fact_scorer_kind(blob, u7_fact) == 2, "expected the u7 scorer kind"
    set_fact_policy(blob, u7_fact, POLICY_DEGENERATE_OPERAND_ABSTAIN, 0)
    return finalize(blob)


def craft_u7_nonzero_bound_image() -> bytes:
    """A U7 row with no policy flag but a nonzero shape6-unit bound."""
    blob = base_image()
    index = parse_index(blob)
    u7_fact = populated_fact_index(index, U7_KEY)
    assert fact_scorer_kind(blob, u7_fact) == 2, "expected the u7 scorer kind"
    set_fact_policy(blob, u7_fact, 0, 1)
    return finalize(blob)


# ---------------------------------------------------------------------------
# The native harness
# ---------------------------------------------------------------------------
# Same build recipe as test_fl4678_exact_cell_predictor.py; the differences:
#   * argv[4] names the sidecar image relative to argv[1], so crafted images in
#     a temporary directory can be loaded;
#   * a load failure PRINTS its status and exits 0, so the rejection tests can
#     assert the exact named status instead of a bare exit code;
#   * each row line also reports policy_abstained_family_count and the exact
#     policy_abstained_family_mask ("abstained" and "abstain_mask").
HARNESS_SOURCE = r"""
// Native harness for the FL-4512 Phase 2 policy contract. Not product code.
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "apps/fl4512-runtime/platform/fl4683_exact_cell_selector.h"

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
        fprintf(stderr, "usage: harness <root> <size> <sha256> <sidecar-rel> [rows]\n");
        return 2;
    }

    Fl4683GlyphProgramLoadRequestV1 request;
    memset(&request, 0, sizeof(request));
    request.schema_version = FL4683_GLYPH_PROGRAM_LOADER_SCHEMA_VERSION;
    request.repository_root = argv[1];
    request.sidecar_relative_path = argv[4];
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
        return 0;  // the rejection tests assert on the printed status

    if (argc >= 6)
    {
        FILE* rows = fopen(argv[5], "r");
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

            printf("row=%d accepted:%u outcome:%u absent:%u abstained:%u "
                   "abstain_mask:%u reason:%u families:%u family_bit:%u "
                   "glyph:%u considered:%u\n",
                row_index,
                (unsigned int)result.accepted,
                (unsigned int)result.outcome,
                (unsigned int)result.declared_absent_family_count,
                /* FL-4512 Phase 2: how many families a compiled policy caused
                   to DECLINE, across both arbitration passes. */
                (unsigned int)result.policy_abstained_family_count,
                (unsigned int)result.policy_abstained_family_mask,
                (unsigned int)result.rejection_reason,
                (unsigned int)result.resolved_family_count,
                (unsigned int)result.winning_route_family_bit,
                result.winning_glyph_record_id,
                (unsigned int)result.considered_candidate_count);
            row_index++;
        }
        fclose(rows);
    }

    Fl4683GlyphProgramReleaseV1(loaded.image);
    return 0;
}
"""


def _build_harness(tmp_path: Path) -> Path:
    compiler = shutil.which("c++")
    if compiler is None:
        pytest.skip("C++ compiler unavailable")
    source = tmp_path / "fl4512_phase2_harness.cpp"
    binary = tmp_path / "fl4512_phase2_harness"
    source.write_text(HARNESS_SOURCE, encoding="utf-8")
    command = [
        compiler,
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


@pytest.fixture(scope="module")
def harness(tmp_path_factory) -> Path:
    return _build_harness(tmp_path_factory.mktemp("phase2-harness"))


@pytest.fixture(scope="module")
def images(tmp_path_factory) -> Dict[str, Path]:
    """Every crafted image, written once per test session."""
    directory = tmp_path_factory.mktemp("phase2-images")
    crafted = {
        "valid_decline": craft_policy_image(),
        "unknown_policy_bit": craft_unknown_policy_bit_image(),
        "u7_flagged": craft_u7_flagged_image(),
        "u7_nonzero_bound": craft_u7_nonzero_bound_image(),
    }
    paths = {}
    for name, blob in crafted.items():
        path = directory / f"{name}.bin"
        path.write_bytes(blob)
        paths[name] = path
    return paths


def _run(harness_binary: Path, image: Path, rows: List[str], tmp_path: Path):
    """Load one crafted image and (optionally) drive scene-fact rows at it."""
    blob = image.read_bytes()
    command = [
        str(harness_binary),
        str(image.parent),           # repository root = the image's directory
        str(len(blob)),
        hashlib.sha256(blob).hexdigest(),
        image.name,
    ]
    if rows:
        rows_path = tmp_path / f"{image.stem}-rows.txt"
        rows_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        command.append(str(rows_path))
    completed = subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, check=False
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    status = None
    parsed_rows = []
    for line in completed.stdout.splitlines():
        if line.startswith("load_status="):
            status = line.partition("=")[2]
        elif line.startswith("row="):
            fields = {}
            for token in line.split():
                key, _, value = token.partition("=" if "=" in token else ":")
                fields[key] = int(value)
            parsed_rows.append(fields)
    return status, parsed_rows


def _row(mask, ramp, density, edge, harri) -> str:
    """One terrain-material-4 SceneFact row in the harness wire format."""
    values = [2, 1, 1, 4, mask, ramp, density, 0, 0, edge, 1, *harri]
    return " ".join(str(v) for v in values) + " 0 0 0 0 0 0 0"


DEGENERATE = [32768] * 6           # six equal dims: no spatial structure
FAR_FROM_EVERYTHING = [65535, 0, 65535, 0, 65535, 0]


# ---------------------------------------------------------------------------
# (b) loader rejection of malformed policy
# ---------------------------------------------------------------------------
def test_loader_accepts_the_crafted_valid_decline_image(harness, images, tmp_path):
    """The mutation machinery itself must produce a LOADABLE format-6 image;
    without this control, every rejection below could be rejecting the
    crafting rather than the policy."""
    status, _ = _run(harness, images["valid_decline"], [], tmp_path)
    assert status in ("loaded", "loaded_without_local_relation_cache"), status


def test_loader_and_selector_accept_compiler_produced_disposable_v6_sidecar(
    harness, tmp_path
):
    """An actual offline compiler output must cross the same native front door.

    The environment variable is intentionally opt-in because the disposable
    chain lives outside the repository's canonical generated paths. The row is
    the committed terrain:4 edge address, so the check exercises both loader
    acceptance and selector arbitration against the compiler-produced image.
    """
    sidecar_text = os.environ.get("FL4512_PHASE2_V6_SIDECAR")
    if not sidecar_text:
        pytest.skip("disposable compiler-produced v6 sidecar not supplied")
    image = Path(sidecar_text)
    assert image.is_file(), image
    status, rows = _run(
        harness,
        image,
        [_row(FAMILY_EDGE, 2, 1, 5, DEGENERATE)],
        tmp_path,
    )
    assert status in ("loaded", "loaded_without_local_relation_cache"), status
    assert len(rows) == 1, rows
    assert rows[0]["abstain_mask"] == FAMILY_EDGE
    assert rows[0]["absent"] == 0
    assert rows[0]["accepted"] == 1


def test_loader_rejects_an_unknown_policy_bit(harness, images, tmp_path):
    status, _ = _run(harness, images["unknown_policy_bit"], [], tmp_path)
    assert status == "malformed_sidecar", status


def test_loader_rejects_the_flag_on_a_u7_fact(harness, images, tmp_path):
    # The degenerate-operand rule is defined solely over the Harri shape6
    # operand; a flag on the u7 scorer means compiler and loader disagree
    # about policy semantics, which must fail closed at load time.
    status, _ = _run(harness, images["u7_flagged"], [], tmp_path)
    assert status == "malformed_sidecar", status


def test_loader_rejects_a_nonzero_u7_bound_without_a_policy_flag(
    harness, images, tmp_path
):
    # U7's scorer units differ from the authored shape6 weighted-L2 units. A
    # zero policy byte does not make a nonzero threshold legal: the loader
    # must reject the malformed scorer-scoped contract rather than silently
    # interpreting unlike quantities.
    status, _ = _run(harness, images["u7_nonzero_bound"], [], tmp_path)
    assert status == "malformed_sidecar", status


# ---------------------------------------------------------------------------
# (c) selector behavior under well-formed policy
# ---------------------------------------------------------------------------
def _select(harness, images, tmp_path, row: str) -> Dict[str, int]:
    status, rows = _run(harness, images["valid_decline"], [row], tmp_path)
    assert status in ("loaded", "loaded_without_local_relation_cache"), status
    assert len(rows) == 1, rows
    return rows[0]


def test_explicit_edge_decline_falls_back_with_a_degenerate_tonal_operand(
    harness, images, tmp_path
):
    """An explicit structural decline falls back to the tonal pair.

    The flat shape6 operand is present only to prove that tone cannot turn the
    EDGE family into a winner. The decline is compiler-owned and independent
    of that operand's value.
    """
    result = _select(
        harness, images, tmp_path, _row(FAMILY_EDGE, 2, 1, 5, DEGENERATE)
    )
    assert result["accepted"] == 1
    assert result["outcome"] == OUTCOME_SELECTED
    assert result["abstained"] == 1
    assert result["abstain_mask"] == FAMILY_EDGE
    # The final arbitration pass was the tonal pair: two resolved families,
    # and the winner came from one of them, not from the declined edge row.
    assert result["families"] == 2
    assert result["family_bit"] in (FAMILY_RAMP, FAMILY_DENSITY)
    assert result["glyph"] > 0
    assert result["absent"] == 0


def test_a_constant_tonal_operand_selects_normally(harness, images, tmp_path):
    """tonal_constant_operand_legal: the same flat operand that made the edge
    row stand aside is a legal description of a flat surface for the tonal
    pair, so it selects with no policy involvement at all."""
    result = _select(
        harness, images, tmp_path, _row(TONAL_PAIR_MASK, 2, 1, 0, DEGENERATE)
    )
    assert result["accepted"] == 1
    assert result["outcome"] == OUTCOME_SELECTED
    assert result["abstained"] == 0
    assert result["families"] == 2
    assert result["glyph"] > 0


def test_edge_decline_over_an_absent_tonal_pair_is_declared_absence(
    harness, images, tmp_path
):
    """When the structural row declines AND the fallback tonal pair is
    compiled-absent, the program has answered every lane: nothing is to be
    drawn. That is DECLARED_ABSENCE -- a success -- never a failure."""
    result = _select(
        harness, images, tmp_path, _row(FAMILY_EDGE, 3, 3, 5, DEGENERATE)
    )
    assert result["accepted"] == 0
    assert result["outcome"] == OUTCOME_DECLARED_ABSENCE
    assert result["reason"] == 0  # FL4683_REJECTION_NONE
    # The final pass resolved the two tonal lanes, both declared absent, and
    # the abstained count still names the pass-0 structural decline.
    assert result["families"] == 2
    assert result["absent"] == 2
    assert result["abstained"] == 1
    assert result["abstain_mask"] == FAMILY_EDGE
    assert result["glyph"] == 0


def test_nondegenerate_tone_cannot_reactivate_an_explicit_edge_decline(
    harness, images, tmp_path
):
    """A non-degenerate tonal operand cannot reactivate an EDGE scorer.

    With the tonal fallback unavailable, the operand-free explicit decline is
    the whole final pass: declared absence with one counted decline.
    """
    result = _select(
        harness, images, tmp_path,
        _row(FAMILY_EDGE, 255, 255, 5, FAR_FROM_EVERYTHING),
    )
    assert result["accepted"] == 0
    assert result["outcome"] == OUTCOME_DECLARED_ABSENCE
    assert result["families"] == 1
    assert result["absent"] == 0
    assert result["abstained"] == 1
    assert result["abstain_mask"] == FAMILY_EDGE
    assert result["glyph"] == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
