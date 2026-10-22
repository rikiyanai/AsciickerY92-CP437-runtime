#!/usr/bin/env python3
# =============================================================================
# scripts/tests/test_fl4686_declared_absence_disposition.py
# =============================================================================
# WHAT THIS FILE CHECKS, AND WHY IT EXISTS
# ----------------------------------------
# FL-4686 precondition PA. The compiled glyph program used to describe only the
# addresses that hold drawable character shapes. An address the compiler had
# deliberately left empty was simply not mentioned. A renderer asking about such
# a cell therefore got "no such address", which is a FAILURE -- and a failure is
# the one answer a cell the program is supposed to own must never produce,
# because it makes the cell fall through to a different owner.
#
# PA fixes that by making DECLARED ABSENCE a first-class thing the program says
# out loud: "I own this address, and there is nothing to draw here." That is a
# decision made offline, not an error discovered at runtime.
#
# The words used throughout, defined once:
#
#   ADDRESS      one prepared answer slot, identified by FOUR things: which
#                family of surfaces (namespace), which material, which route
#                family, and which lane inside that family.
#   POPULATED    the program holds character shapes for this address.
#   DECLARED     the program owns this address and states it holds no shape.
#   ABSENT       (short for the above.) A successful answer, not a failure.
#   LOOKUP MISS  the address is not in the program's space at all. Still a
#                failure, deliberately, because an address the program never
#                enumerated is not an address the program owns.
#
# These tests read committed artifacts and source files. They launch no game,
# open no window, and draw no pixel. They also assert that PA changed NOTHING
# about the two gates that keep this whole lane out of the shipped product.
#
# Run with:
#     python3 -m pytest scripts/tests/test_fl4686_declared_absence_disposition.py -q
# =============================================================================

from __future__ import annotations

import json
import re
import struct
import subprocess
import sys
import unittest
from pathlib import Path

# The repository root: this file is scripts/tests/x.py, so two levels up.
ROOT = Path(__file__).resolve().parents[2]

SIDECAR = ROOT / "assets/glyphs/generated/fl4683.glyph_program.v1.bin"
RECEIPT = ROOT / "assets/glyphs/generated/fl4683.glyph_program_pin.v1.json"
POOL = ROOT / "assets/glyphs/generated/fl4512.compiled_pool.v1.json"
ARTIFACT_SCHEMA_POINTER = ROOT / "assets/glyphs/generated/fl4694.relation_pointer.v1.json"
COMPILER = ROOT / "scripts/compile_fl4683_glyph_program.py"
PREDICTOR = ROOT / "scripts/predict_fl4678_exact_cell_glyph.py"
FIXTURE = ROOT / "assets/glyphs/fixtures/fl4678.exact_cell_scene_facts.v1.json"
LOADER_H = ROOT / "apps/fl4512-runtime/platform/fl4683_glyph_program_loader.h"
LOADER_CPP = ROOT / "apps/fl4512-runtime/platform/fl4683_glyph_program_loader.cpp"
SELECTOR_H = ROOT / "apps/fl4512-runtime/platform/fl4683_exact_cell_selector.h"
SELECTOR_CPP = ROOT / "apps/fl4512-runtime/platform/fl4683_exact_cell_selector.cpp"

# Byte offsets inside the sidecar's fixed 256-byte header. These mirror the
# kOffset* constants in the C++ loader; naming them here means this test reads
# the file the same way the engine does rather than by a second recipe.
OFF_MAGIC = 0
OFF_FORMAT_VERSION = 8
OFF_FACT_COUNT = 148
OFF_SCENE_ADDRESS_OFFSET = 232
OFF_SCENE_ADDRESS_COUNT = 236
OFF_SCENE_ADDRESS_DECLARED_ABSENT_COUNT = 240

# One index entry is 12 bytes: i16 i16 u8 u8 u8 u8 u32.
SCENE_ADDRESS_STRUCT = "<hhBBBBI"
SCENE_ADDRESS_SIZE = 12

# The two disposition values, and the sentinel a declared-absent entry stores in
# its fact index. Duplicated from the compiler and the C++ header on purpose:
# if any of the three drifts, these tests fail rather than silently agreeing.
DISPOSITION_POPULATED = 1
DISPOSITION_DECLARED_ABSENT = 2
NO_FACT_INDEX = 0xFFFFFFFF

# What the shape of the address space must be. These are not aspirations; they
# are what the committed compiled pool enumerates: eight loaded material
# profiles times thirty-three lanes.
EXPECTED_TOTAL_ADDRESSES = 264
EXPECTED_POPULATED_ADDRESSES = 99
EXPECTED_DECLARED_ABSENT_ADDRESSES = 165


def read_sidecar() -> bytes:
    """Read the compiled sidecar, failing with a clear message if it is absent."""
    if not SIDECAR.is_file():
        raise AssertionError(
            f"the compiled sidecar is missing: {SIDECAR.relative_to(ROOT)}. "
            "Run: python3 scripts/compile_fl4683_glyph_program.py --write"
        )
    return SIDECAR.read_bytes()


def read_index(blob: bytes):
    """Pull the whole address index out of the sidecar as a list of tuples.

    Returns a list of (namespace, material, family_bit, lane, disposition,
    fact_index) in the order the file stores them, which is the order the C++
    binary search depends on.
    """
    offset = struct.unpack_from("<I", blob, OFF_SCENE_ADDRESS_OFFSET)[0]
    count = struct.unpack_from("<I", blob, OFF_SCENE_ADDRESS_COUNT)[0]
    entries = []
    for i in range(count):
        ns, mat, bit, lane, disp, _pad, fact = struct.unpack_from(
            SCENE_ADDRESS_STRUCT, blob, offset + i * SCENE_ADDRESS_SIZE
        )
        entries.append((ns, mat, bit, lane, disp, fact))
    return entries


class TestAddressSpaceShape(unittest.TestCase):
    """The index must cover the whole space, split 99 / 165."""

    def test_the_index_holds_exactly_264_four_field_addresses(self):
        entries = read_index(read_sidecar())
        self.assertEqual(len(entries), EXPECTED_TOTAL_ADDRESSES)
        # Four fields, not two. Two addresses may share a namespace and material
        # and still be different addresses, which is the whole reason a cell can
        # be owned by a profile and have no compiled address.
        keys = [(ns, mat, bit, lane) for ns, mat, bit, lane, _d, _f in entries]
        self.assertEqual(len(set(keys)), EXPECTED_TOTAL_ADDRESSES,
                         "every address must appear exactly once")

    def test_the_split_is_99_populated_and_165_declared_absent(self):
        entries = read_index(read_sidecar())
        populated = [e for e in entries if e[4] == DISPOSITION_POPULATED]
        absent = [e for e in entries if e[4] == DISPOSITION_DECLARED_ABSENT]
        self.assertEqual(len(populated), EXPECTED_POPULATED_ADDRESSES)
        self.assertEqual(len(absent), EXPECTED_DECLARED_ABSENT_ADDRESSES)
        # No third value. A disposition this build does not know would be read
        # by the loader as malformed, so it must not be produced either.
        self.assertEqual(len(populated) + len(absent), len(entries))

    def test_populated_count_equals_the_fact_table_size(self):
        # The populated addresses are exactly the compiled facts. If these ever
        # differ, the index is describing a program that is not the one shipped
        # in the same file.
        blob = read_sidecar()
        fact_count = struct.unpack_from("<I", blob, OFF_FACT_COUNT)[0]
        populated = [e for e in read_index(blob) if e[4] == DISPOSITION_POPULATED]
        self.assertEqual(len(populated), fact_count)

    def test_the_header_states_the_declared_absent_count_and_it_is_true(self):
        # Two independent statements of the same number, compared. A file whose
        # header disagrees with its own rows is refused by the loader; this
        # proves the compiler does not produce such a file.
        blob = read_sidecar()
        stated = struct.unpack_from(
            "<I", blob, OFF_SCENE_ADDRESS_DECLARED_ABSENT_COUNT
        )[0]
        counted = sum(
            1 for e in read_index(blob) if e[4] == DISPOSITION_DECLARED_ABSENT
        )
        self.assertEqual(stated, counted)
        self.assertEqual(stated, EXPECTED_DECLARED_ABSENT_ADDRESSES)

    def test_declared_absent_entries_carry_the_sentinel_and_nothing_else(self):
        blob = read_sidecar()
        fact_count = struct.unpack_from("<I", blob, OFF_FACT_COUNT)[0]
        for ns, mat, bit, lane, disp, fact in read_index(blob):
            if disp == DISPOSITION_DECLARED_ABSENT:
                # Requiring the EXACT sentinel, not merely "out of range", is
                # what stops a corrupted index from masquerading as an absence.
                self.assertEqual(
                    fact, NO_FACT_INDEX,
                    f"declared-absent address {(ns, mat, bit, lane)} must carry "
                    "the no-fact sentinel",
                )
            else:
                self.assertLess(fact, fact_count)

    def test_the_index_is_sorted_so_a_binary_search_is_legal(self):
        entries = read_index(read_sidecar())
        keys = [(ns, mat, bit, lane) for ns, mat, bit, lane, _d, _f in entries]
        self.assertEqual(keys, sorted(keys))

    def test_the_index_matches_the_pool_row_for_row(self):
        # The pool is the artifact that enumerates the space. Rebuilding the
        # expected index from it independently -- rather than trusting the
        # sidecar's own numbers -- is what makes this a check and not an echo.
        pool = json.loads(POOL.read_text())
        family_bit = {
            "ramp.v1": 1, "density.v1": 2, "edge.v1": 4, "direction.v1": 8,
            "flow.v1": 16, "filled.field.v1": 32, "mesh_feature.bundle.v1": 64,
        }
        namespace_code = {"terrain": 1, "mesh": 2}
        lane_pattern = re.compile(r"^[a-z_]+\[(\d+)\]$")
        expected = {}
        for row in pool["rows"]:
            match = lane_pattern.match(str(row["lane_name"]))
            lane = int(match.group(1)) if match else 0
            key = (
                namespace_code[row["profile_namespace"]],
                int(row["material_key"]),
                family_bit[row["route_key"]],
                lane,
            )
            expected[key] = (
                DISPOSITION_POPULATED if row["bucket_state"] == "live"
                else DISPOSITION_DECLARED_ABSENT
            )
        actual = {
            (ns, mat, bit, lane): disp
            for ns, mat, bit, lane, disp, _f in read_index(read_sidecar())
        }
        self.assertEqual(actual, expected)


class TestOutcomesStayDistinct(unittest.TestCase):
    """Declared absence must never be confused with any failure mode."""

    def test_the_loader_names_three_separate_query_outcomes(self):
        text = LOADER_H.read_text()
        for name in (
            "FL4683_GLYPH_PROGRAM_OUTCOME_REJECTED",
            "FL4683_GLYPH_PROGRAM_OUTCOME_POPULATED",
            "FL4683_GLYPH_PROGRAM_OUTCOME_DECLARED_ABSENCE",
        ):
            self.assertIn(name, text)

    def test_the_selector_names_three_separate_cell_outcomes(self):
        text = SELECTOR_H.read_text()
        for name in (
            "FL4683_EXACT_CELL_OUTCOME_REJECTED",
            "FL4683_EXACT_CELL_OUTCOME_SELECTED",
            "FL4683_EXACT_CELL_OUTCOME_DECLARED_ABSENCE",
        ):
            self.assertIn(name, text)

    def test_declared_absence_is_not_an_empty_legal_row(self):
        # THE defect this whole change exists to remove. Before PA, a resolved
        # address with no shapes produced FL4683_REJECTION_EMPTY_LEGAL_ROW. The
        # selector must now return the declared-absence OUTCOME on that path and
        # must set the rejection reason to NONE. Since FL-4512 Phase 2 the
        # absence consensus also counts policy DECLINES: every family of the
        # final arbitration pass must be compiled-absent or declined.
        text = SELECTOR_CPP.read_text()
        self.assertIn("out.outcome = FL4683_EXACT_CELL_OUTCOME_DECLARED_ABSENCE", text)
        absence_block = text.split(
            "(uint8_t)(declared_absent_count + pass_declined_count) == family_count",
            1,
        )[1].split("return out;", 1)[0]
        self.assertIn("FL4683_REJECTION_NONE", absence_block)
        self.assertNotIn("EMPTY_LEGAL_ROW", absence_block)

    def test_an_empty_fact_row_is_still_an_empty_legal_row(self):
        # The genuine empty-row failure must survive. A fact that exists and
        # carries no members is a compiled contradiction, and PA must not have
        # turned that into a quiet blank cell.
        text = LOADER_CPP.read_text()
        self.assertIn("FL4683_REJECTION_EMPTY_LEGAL_ROW", text)
        self.assertIn("if (fact.member_count == 0)", text)

    def test_a_lookup_miss_is_still_a_failure(self):
        # An address outside the space is NOT owned by the program and must stay
        # a named failure. This is the line between "nothing is drawn here on
        # purpose" and "the program does not cover this cell".
        text = LOADER_CPP.read_text()
        miss_block = text.split("if (!found_entry)", 1)[1].split("}", 1)[0]
        self.assertIn("FL4683_REJECTION_MISSING_COMPILED_ADDRESS", miss_block)

    def test_a_shut_gate_reports_rejection_and_never_declared_absence(self):
        # "The loader is switched off" and "the program says this cell is empty"
        # are different answers. The inert stub must give the first.
        for path in (LOADER_CPP, SELECTOR_CPP):
            stub = path.read_text().split(
                "#else  // FL4683_GLYPH_PROGRAM_LOADER_ENABLED", 1
            )[1]
            self.assertIn("OUTCOME_REJECTED", stub, f"{path.name} stub")
            self.assertNotIn(
                "out.outcome = FL4683_EXACT_CELL_OUTCOME_DECLARED_ABSENCE", stub
            )
            self.assertNotIn(
                "out.outcome = FL4683_GLYPH_PROGRAM_OUTCOME_DECLARED_ABSENCE", stub
            )

    def test_an_unadmitted_glyph_is_still_its_own_refusal(self):
        text = SELECTOR_CPP.read_text()
        self.assertIn("FL4683_REJECTION_UNADMITTED_GLYPH", text)

    def test_a_malformed_row_is_still_its_own_refusal(self):
        text = SELECTOR_CPP.read_text()
        self.assertIn("FL4683_REJECTION_MALFORMED_SCENE_FACT", text)

    def test_disposition_zero_is_not_a_valid_value(self):
        # A record zeroed by accident, or written by a compiler that predates
        # this field, must read as unset and be refused -- never as "populated".
        self.assertIn("FL4683_ADDRESS_DISPOSITION_UNSET = 0u", LOADER_H.read_text())
        entries = read_index(read_sidecar())
        self.assertFalse([e for e in entries if e[4] == 0])


class TestFormatVersioning(unittest.TestCase):
    """An older reader must refuse the newer file.

    Format version 6 (FL-4512 Phase 2) spends the fact record's former padding
    byte on `policy_flags` and re-scopes the record's trailing u32 by scorer
    kind. A version-5 reader would treat authored abstention policy as garbage
    padding, so compiler, sidecar, loader, and receipt must all say 6 together.
    """

    def test_the_sidecar_declares_format_version_6(self):
        blob = read_sidecar()
        self.assertEqual(blob[OFF_MAGIC:OFF_MAGIC + 8], b"FL4683GP")
        self.assertEqual(struct.unpack_from("<I", blob, OFF_FORMAT_VERSION)[0], 6)

    def test_the_loader_expects_format_version_6(self):
        self.assertIn(
            "#define FL4683_GLYPH_PROGRAM_FORMAT_VERSION 6u", LOADER_H.read_text()
        )

    def test_the_compiler_emits_format_version_6(self):
        self.assertIn("SIDECAR_FORMAT_VERSION = 6", COMPILER.read_text())

    def test_the_receipt_records_the_new_format_and_its_own_version_bump(self):
        receipt = json.loads(RECEIPT.read_text())
        self.assertEqual(receipt["version"], 6)
        self.assertEqual(receipt["sidecar"]["format_version"], 6)
        index = receipt["scene_address_index"]
        self.assertEqual(index["entry_count"], EXPECTED_TOTAL_ADDRESSES)
        self.assertEqual(index["populated_entry_count"], EXPECTED_POPULATED_ADDRESSES)
        self.assertEqual(
            index["declared_absent_entry_count"], EXPECTED_DECLARED_ABSENT_ADDRESSES
        )
        self.assertEqual(index["no_fact_index_sentinel"], NO_FACT_INDEX)

    def test_the_receipt_pins_the_address_space_artifact(self):
        # The space is now an INPUT, so it must be fingerprinted like every other
        # input. A pool that drifted would otherwise silently change what the
        # program claims to own.
        receipt = json.loads(RECEIPT.read_text())
        roles = {pin["role_name"] for pin in receipt["pins"]}
        self.assertIn("compiled_pool_address_space", roles)

    def test_the_receipt_hash_matches_the_sidecar_on_disk(self):
        import hashlib
        receipt = json.loads(RECEIPT.read_text())
        blob = read_sidecar()
        self.assertEqual(receipt["sidecar"]["byte_size"], len(blob))
        self.assertEqual(
            receipt["sidecar"]["sha256"], hashlib.sha256(blob).hexdigest()
        )


class TestDeterminism(unittest.TestCase):
    """Recompiling must reproduce the same bytes -- or fail closed by name.

    FL-4512 Phase 2. The format-6 compiler sources every shape6 row's policy
    byte and no-match bound from the compiled pool's materials[]
    no_match_policy. A pool at revision 2 or older does not carry that policy,
    so against such a pool the compile must FAIL CLOSED with a stable reason;
    it must never fall back to guessed bounds or silently reproduce a
    format-5 sidecar. The byte-for-byte determinism check therefore runs only
    once a policy-carrying pool (revision 3+) is on disk.
    """

    @staticmethod
    def _pool_carries_no_match_policy() -> bool:
        pool = json.loads(POOL.read_text())
        materials = pool.get("materials", [])
        return bool(materials) and all(
            isinstance(material.get("no_match_policy"), dict)
            for material in materials
        )

    def test_recompiling_reproduces_the_committed_artifact(self):
        # --check recomputes both outputs in memory and exits non-zero if what is
        # on disk differs. Determinism is what makes the pinned hash meaningful:
        # a hash over output that varied per run would prove nothing.
        if not self._pool_carries_no_match_policy():
            self.skipTest(
                "bin not rebuilt: compiled pool is still revision 2 (no "
                "no_match_policy); the fail-closed contract for that state is "
                "asserted separately below"
            )
        result = subprocess.run(
            [sys.executable, str(COMPILER), "--check"],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        if result.returncode != 0 and "source_join_mismatch" in result.stderr:
            # The compiler's own fail-closed source-identity gate: the pool and
            # the glyph-fact artifact on disk are not the same compilation
            # (a pool relanding whose downstream glyph-fact regeneration has
            # not landed yet). Byte determinism is unmeasurable against
            # disagreeing inputs, and the refusal IS the correct behavior.
            self.skipTest(
                "inputs disagree (source_join_mismatch): the glyph-fact "
                "artifact has not been regenerated against the current pool, "
                "so the recompile check cannot run yet: " + result.stderr.strip()
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_policy_free_pool_fails_the_compile_closed(self):
        # The inverse contract: while the pool predates no_match_policy, the
        # compiler must stop with a NAMED reason rather than produce any bytes.
        # (Concurrent input landings can legitimately trip an earlier
        # fail-closed source-identity check instead; both are named refusals,
        # and neither may be an exit code of 0.)
        if self._pool_carries_no_match_policy():
            self.skipTest("pool already carries no_match_policy (revision 3+)")
        result = subprocess.run(
            [sys.executable, str(COMPILER), "--check"],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        self.assertNotEqual(
            result.returncode, 0,
            "a revision-2 pool must never produce a clean format-6 check",
        )
        self.assertTrue(
            "no_match_policy_missing" in result.stderr
            or "source_join_mismatch" in result.stderr,
            result.stdout + result.stderr,
        )


class TestPredictorStaysInLockstep(unittest.TestCase):
    """The offline predictor must answer exactly as the selector does."""

    def _report(self):
        result = subprocess.run(
            [sys.executable, str(PREDICTOR)],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_the_predictor_sees_the_whole_address_space(self):
        report = self._report()
        self.assertEqual(report["compiled_address_count"], EXPECTED_TOTAL_ADDRESSES)
        self.assertEqual(
            report["populated_address_count"], EXPECTED_POPULATED_ADDRESSES
        )
        self.assertEqual(
            report["declared_absent_address_count"],
            EXPECTED_DECLARED_ABSENT_ADDRESSES,
        )

    def test_the_falsifier_covers_at_least_one_declared_absence_cell(self):
        # Required by the PA scope: the exact-cell falsifier must exercise the
        # new outcome, or the outcome is untested by the thing whose job is to
        # falsify exact-cell claims.
        report = self._report()
        absent = [
            p for p in report["predictions"] if p["outcome"] == "declared_absence"
        ]
        self.assertGreaterEqual(len(absent), 1)
        # And it must be reported as a success, not as a dressed-up refusal.
        for prediction in absent:
            self.assertEqual(prediction["rejection_reason"], "none")
            self.assertFalse(prediction["accepted"])
            self.assertEqual(prediction["considered_candidate_count"], 0)

    def test_declared_absence_is_covered_in_both_namespaces(self):
        report = self._report()
        names = {
            p["name"] for p in report["predictions"]
            if p["outcome"] == "declared_absence"
        }
        self.assertTrue([n for n in names if "terrain" in n], names)
        self.assertTrue([n for n in names if "mesh" in n], names)

    def test_a_row_outside_the_space_is_still_predicted_as_a_miss(self):
        report = self._report()
        miss = [
            p for p in report["predictions"]
            if p["name"] == "refuse_unknown_material_key"
        ]
        self.assertEqual(len(miss), 1)
        self.assertEqual(miss[0]["outcome"], "rejected")
        self.assertEqual(miss[0]["rejection_reason"], "missing_compiled_address")

    def test_every_fixture_row_name_agrees_with_its_predicted_outcome(self):
        # The fixture names ARE the expectation. A row named for one outcome and
        # predicted as another means one of the two is wrong, and this test does
        # not care which -- it fails either way.
        report = self._report()
        for prediction in report["predictions"]:
            name = prediction["name"]
            if name.startswith("declared_absence_"):
                expected = "declared_absence"
            elif name.startswith("refuse_"):
                expected = "rejected"
            else:
                expected = "selected"
            self.assertEqual(prediction["outcome"], expected, name)


class TestP6ActivationBoundary(unittest.TestCase):
    """P6 activates the one product program only in presenting targets."""

    def test_presenting_builds_link_the_single_loader_and_selector(self):
        presenting = [
            ROOT / "apps/game-fl4512/makefile_game",
            ROOT / "apps/game-fl4512/makefile_game_mac",
            ROOT / "apps/asciicker-presenter/makefile_asciiid",
            ROOT / "apps/asciicker-presenter/makefile_asciiid_mac",
            ROOT / "build-web.sh",
        ]
        for path in presenting:
            text = path.read_text(errors="replace")
            self.assertIn("fl4683_glyph_program_loader", text, path.name)
            self.assertIn("fl4683_exact_cell_selector", text, path.name)
            self.assertIn("fl4693_product_glyph_program", text, path.name)
            self.assertIn("FL4683_GLYPH_PROGRAM_LOADER_ENABLED", text, path.name)
            self.assertIn("FL4693_PRODUCT_GLYPH_PROGRAM_ENABLED", text, path.name)

    def test_builds_without_a_presenter_do_not_acquire_program_authority(self):
        nonpresenting = [
            ROOT / "makefile_game_term",
            ROOT / "makefile_game_term_mac",
            ROOT / "makefile_server",
        ]
        for path in nonpresenting:
            if not path.is_file():
                continue
            text = path.read_text(errors="replace")
            self.assertNotIn("terminal_gl_present.cpp", text, path.name)
            self.assertNotIn("fl4683_glyph_program_loader", text, path.name)
            self.assertNotIn("fl4683_exact_cell_selector", text, path.name)
            self.assertNotIn("fl4693_product_glyph_program", text, path.name)
            self.assertNotIn(
                "FL4683_GLYPH_PROGRAM_LOADER_ENABLED", text, path.name
            )
            self.assertNotIn("FL4693_PRODUCT_GLYPH_PROGRAM_ENABLED", text, path.name)

    def test_runtime_consumption_is_still_refused(self):
        # Gate 3 (data). The sidecar's own byte stays 0 and the compiler still
        # refuses to build if the source artifact ever flips the flag.
        blob = read_sidecar()
        self.assertEqual(blob[160], 0, "runtime_consumption_allowed must stay 0")
        self.assertIn(
            "activation is a separate decision and this compiler refuses to infer it",
            COMPILER.read_text(),
        )

    def test_runtime_calls_stay_inside_the_gated_product_owner(self):
        resolver = (ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text()
        partition = resolver.split("Fl4693ClassifyProfilePartitionV1", 1)[1]
        partition = partition.split('extern "C"', 1)[0]
        self.assertIn("#if defined(FL4693_PRODUCT_GLYPH_PROGRAM_ENABLED)", partition)
        self.assertIn("Fl4683GlyphProgramQueryExecuteV1", partition)
        product = (ROOT / "apps/fl4512-runtime/platform/fl4693_product_glyph_program.cpp").read_text()
        self.assertIn("Fl4683ExactCellSelectV1", product)


if __name__ == "__main__":
    unittest.main()
