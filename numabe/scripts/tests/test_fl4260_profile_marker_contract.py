#!/usr/bin/env python3
# =============================================================================
# test_fl4260_profile_marker_contract.py   (FL-4260)
# =============================================================================
# WHAT IS BEING TESTED
# --------------------
# When the renderer cannot honestly choose a real text character for a cell on
# screen, it must not invent a plausible-looking one -- that would hide the
# failure inside a picture that looks fine. Instead it draws a deliberately
# conspicuous "fail marker": an exclamation mark in a reserved colour pair.
#
# There are three failure kinds, each with its OWN reserved colour pair so the
# reason is readable straight off the screen:
#
#   MISSING_POLICY   the cell asked for a material that has no authored policy
#   MISSING_GLYPH    a policy exists, but no glyph reached the cell
#   DIAGNOSTIC       a sandbox cell deliberately excluded from closure
#
# These tests check that the engine really behaves that way, and that the
# generated receipt describing it still agrees with the engine.
#
# WHY THESE ARE "COMPILED" TESTS
# ------------------------------
# The checks below do not read a description of the renderer written by a
# person. They compile the engine source and call the real C++ function
# `Fl4131ProfileMarkerProducer`, then assert on what it actually returned. If
# someone changes the marker colours in the engine and forgets the receipt, the
# drift test fails; if someone edits the receipt to claim something the engine
# does not do, the same test fails from the other direction.
#
# Run with:
#     python3 -m pytest -q scripts/tests/test_fl4260_profile_marker_contract.py
# =============================================================================

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import generate_fl4131_profile_marker_contract as MARKER  # noqa: E402

RECEIPT = REPO_ROOT / "assets/glyphs/generated/fl4131.profile_marker_contract.v1.json"
COVERAGE_GATE = REPO_ROOT / "scripts/fl4260_rq153c_coverage_gate.py"

# Compiling the engine takes a few seconds, so it happens once for the whole
# module rather than once per test.
_ENGINE_CONTRACT: dict | None = None
_ENGINE_ERROR: str | None = None


def setUpModule() -> None:
    """Compile the engine and call the real marker producer, one time."""
    global _ENGINE_CONTRACT, _ENGINE_ERROR
    try:
        _ENGINE_CONTRACT = MARKER.build_contract()
    except RuntimeError as exc:          # no compiler, or the engine changed shape
        _ENGINE_ERROR = str(exc)


def engine_contract() -> dict:
    """The contract as measured from the compiled engine, or skip the test."""
    if _ENGINE_CONTRACT is None:
        raise unittest.SkipTest(f"could not measure the engine: {_ENGINE_ERROR}")
    return _ENGINE_CONTRACT


class TheEngineDrawsAVisibleMarker(unittest.TestCase):
    """Every failure kind must produce a marker a human can actually see."""

    def test_every_marker_class_draws_an_exclamation_mark(self):
        markers = engine_contract()["markers"]
        for name in MARKER.MARKER_CLASS_NAMES:
            with self.subTest(marker_class=name):
                self.assertIn(name, markers, f"engine did not report {name}")
                # 33 is the byte value of '!'. A blank or a space here would
                # mean a failed cell renders as nothing and the failure hides.
                self.assertEqual(markers[name]["glyph_byte"], 33)
                self.assertEqual(markers[name]["glyph_char"], "!")

    def test_the_diagnostic_marker_is_class_three_in_magenta_on_black(self):
        # The specific pair the diagnostic marker uses. Pinned because a silent
        # change here would make sandbox cells indistinguishable from the other
        # two failure kinds on screen.
        diagnostic = engine_contract()["markers"]["FL4131_MARKER_DIAGNOSTIC"]
        self.assertEqual(diagnostic["marker_class_value"], 3)
        self.assertEqual(diagnostic["fg"], 201)
        self.assertEqual(diagnostic["bg"], 16)

    def test_the_producer_returns_the_class_it_was_asked_for(self):
        # The producer echoes the class back in its result. If it ever returned
        # a different class than requested, every downstream count and colour
        # would be attributed to the wrong failure kind.
        for name, marker in engine_contract()["markers"].items():
            with self.subTest(marker_class=name):
                self.assertEqual(marker["marker_class_value"], marker["marker_class_echoed"])


class TheThreeFailureKindsStayDistinguishable(unittest.TestCase):
    """Two failure kinds sharing a colour pair would be unreadable on screen."""

    def test_the_reserved_pairs_are_mutually_distinct(self):
        markers = engine_contract()["markers"]
        pairs = [(markers[name]["fg"], markers[name]["bg"])
                 for name in MARKER.MARKER_CLASS_NAMES]
        self.assertEqual(len(set(pairs)), len(pairs),
                         f"two marker classes share a colour pair: {pairs}")


class ARealGlyphCannotWearMarkerColours(unittest.TestCase):
    """The engine must reject any authored palette that collides with a marker."""

    def test_the_engine_calls_every_marker_pair_reserved(self):
        contract = engine_contract()
        markers = contract["markers"]
        lookup = {(row["fg"], row["bg"]): row["engine_calls_reserved"]
                  for row in contract["reserved_pair_probe"]}
        for name in MARKER.MARKER_CLASS_NAMES:
            pair = (markers[name]["fg"], markers[name]["bg"])
            with self.subTest(marker_class=name, pair=pair):
                self.assertTrue(lookup.get(pair),
                                f"engine does not treat {pair} as reserved")

    def test_an_ordinary_pair_is_not_treated_as_reserved(self):
        # The negative control. Without it, a predicate that returned True for
        # everything would satisfy the test above while rejecting every
        # legitimate authored palette in the game.
        contract = engine_contract()
        markers = contract["markers"]
        marker_pairs = {(markers[name]["fg"], markers[name]["bg"])
                        for name in MARKER.MARKER_CLASS_NAMES}
        ordinary = [row for row in contract["reserved_pair_probe"]
                    if (row["fg"], row["bg"]) not in marker_pairs]
        self.assertTrue(ordinary, "probe recorded no ordinary pair to compare against")
        for row in ordinary:
            with self.subTest(pair=(row["fg"], row["bg"])):
                self.assertFalse(row["engine_calls_reserved"])


class TheMarkerIdentityIsNeverRenderableContent(unittest.TestCase):
    """A marker cell must carry a sentinel, not a real glyph identity."""

    def test_the_sentinel_is_published_and_outside_the_content_range(self):
        contract = engine_contract()
        sentinel = contract["marker_sentinel_glyph_id"]
        # 0xFFFFFFFD is the engine's GLYPH_ID_DIAGNOSTIC_MISSING. It sits far
        # above every real glyph id precisely so that code which forgets to
        # check cannot accidentally treat it as a drawable character.
        self.assertEqual(sentinel, 0xFFFFFFFD)
        self.assertEqual(contract["marker_sentinel_hex"], "0xFFFFFFFD")

    def test_the_engine_header_classes_the_sentinel_as_not_extended(self):
        # glyph_id_is_extended() must exclude the sentinel, otherwise a fail
        # marker would be routed down the extended-glyph sidecar path as though
        # it were real content.
        header = (REPO_ROOT / "engine/glyph_id.h").read_text(encoding="utf-8")
        self.assertIn("g < GLYPH_ID_DIAGNOSTIC_MISSING", header)
        self.assertIn("g == GLYPH_ID_DIAGNOSTIC_MISSING", header)


class ThePresenterFailsVisibly(unittest.TestCase):
    """The emit path must set a visible glyph AND a non-content identity."""

    def test_emit_marker_writes_the_sentinel_and_the_visible_replacement(self):
        source = (REPO_ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text(
            encoding="utf-8")
        # The single helper every PROFILE refusal goes through.
        self.assertIn("static void Fl4131RuntimeEmitMarker(", source)
        # It must publish the sentinel as the identity...
        self.assertIn("out->glyph_id = GLYPH_ID_DIAGNOSTIC_MISSING", source)
        # ...the visible character as the thing actually drawn...
        self.assertIn("out->replacement_glyph = mk.glyph", source)
        # ...and the PER-CLASS palette, so all three kinds do not render in one
        # colour (which is the defect this path carried before).
        self.assertIn("out->marker_fg = mk.fg", source)
        self.assertIn("out->marker_bg = mk.bg", source)
        self.assertIn("out->marker_class = mk.marker_class", source)

    def test_marker_identity_has_a_single_producer(self):
        # Both the CPU resolver and the GPU presenter must read marker identity
        # from one function, otherwise the two surfaces can disagree and a
        # screenshot stops being evidence about the runtime.
        source = (REPO_ROOT / "apps/fl4512-runtime/engine/fl4131_runtime_harri_resolver.cpp").read_text(
            encoding="utf-8")
        self.assertIn("Fl4131ProfileMarkerProducer(marker_class)", source)


class TheCommittedReceiptAgreesWithTheEngine(unittest.TestCase):
    """Drift in either direction must fail."""

    def test_the_receipt_exists(self):
        self.assertTrue(RECEIPT.exists(),
                        "generate with scripts/generate_fl4131_profile_marker_contract.py")

    def test_the_receipt_matches_the_compiled_engine(self):
        contract = engine_contract()
        committed = json.loads(RECEIPT.read_text(encoding="utf-8"))
        self.assertEqual(committed["markers"], contract["markers"])
        self.assertEqual(committed["marker_sentinel_glyph_id"],
                         contract["marker_sentinel_glyph_id"])
        self.assertEqual(committed["reserved_pair_probe"], contract["reserved_pair_probe"])

    def test_the_check_mode_reports_agreement(self):
        result = subprocess.run(
            [sys.executable,
             str(REPO_ROOT / "scripts/generate_fl4131_profile_marker_contract.py"),
             "--check"],
            cwd=REPO_ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         f"drift check failed:\n{result.stdout}\n{result.stderr}")


class TheCoverageGateConsumesTheGeneratedReceipt(unittest.TestCase):
    """The gate must read the generated receipt, not a hand-written document."""

    def test_the_stale_hand_written_owner_is_gone(self):
        gate = COVERAGE_GATE.read_text(encoding="utf-8")
        # The old owner: a constant pointing at a June document under the
        # verification tree, and the parser that read it.
        self.assertNotIn("DIAG_RECEIPT", gate)
        self.assertNotIn("2026-06-15-rq153c-diagnostic-glyph-receipt.json", gate)
        self.assertNotIn('diag.get("verdict"', gate)

    def test_the_gate_reads_the_generated_contract(self):
        gate = COVERAGE_GATE.read_text(encoding="utf-8")
        self.assertIn("fl4131.profile_marker_contract.v1.json", gate)
        self.assertIn("rq153c_profile_marker_contract", gate)

    def test_the_gate_reports_the_marker_contract_as_satisfied(self):
        result = subprocess.run(
            [sys.executable, str(COVERAGE_GATE), "--json"],
            cwd=REPO_ROOT, capture_output=True, text=True)
        payload = json.loads(result.stdout)
        marker_gate = payload["gates"]["rq153c_profile_marker_contract"]
        self.assertTrue(marker_gate["pass"], json.dumps(marker_gate, indent=2))
        self.assertTrue(marker_gate["exists"])
        self.assertTrue(marker_gate["sentinel_outside_content_range"])

    def test_admission_stays_limited_to_drawable_content_glyphs(self):
        # The native admission module answers "is this a drawable content
        # glyph?". A marker sentinel is deliberately NOT one, so the gate must
        # not ask the admission module about it.
        gate = COVERAGE_GATE.read_text(encoding="utf-8")
        marker_section = gate[gate.index("6. runtime fail-marker contract"):]
        marker_section = marker_section[:marker_section.index("mechanical_pass")]
        self.assertNotIn("fl4482_native_admission", marker_section)
        self.assertNotIn("ADMISSION.", marker_section)


class AGreenGateCannotStandInForARunningProduct(unittest.TestCase):
    """FL-4260 / FL-4665.

    On 2026-08-03 this gate reported mechanical coverage PASS while the operator
    had a frozen ASCIIID editor in front of them with dead controls. Every other
    gate here is a query over files on disk and never starts the product, so all
    of them stayed green.

    These tests lock the fail-closed behaviour that stops that from recurring.
    """

    def test_the_gate_requires_evidence_that_the_product_takes_input(self):
        result = subprocess.run(
            [sys.executable, str(COVERAGE_GATE), "--json"],
            cwd=REPO_ROOT, capture_output=True, text=True)
        payload = json.loads(result.stdout)
        self.assertIn("rq153c_runtime_accepts_input", payload["gates"])

    def test_it_fails_closed_while_no_interaction_receipt_exists(self):
        # The receipt does not exist, because the product does not currently
        # run. The gate must therefore FAIL rather than stay silent about it.
        import importlib
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        gate_module = importlib.import_module("fl4260_rq153c_coverage_gate")
        importlib.reload(gate_module)

        if gate_module.RUNTIME_INTERACTION_RECEIPT.exists():
            self.skipTest("an interaction receipt exists; the absent-case is not under test")

        outcome = gate_module.check_runtime_interaction()
        self.assertFalse(outcome["pass"])
        self.assertTrue(outcome["fail_closed"])
        self.assertIn("FL-4665", outcome["owning_fl"])

    def test_a_failing_interaction_gate_takes_the_whole_verdict_down(self):
        # A gate that fails without changing the overall verdict would be
        # decoration. The mechanical verdict must follow it, and the process
        # must exit non-zero so automation cannot ignore it.
        result = subprocess.run(
            [sys.executable, str(COVERAGE_GATE), "--json"],
            cwd=REPO_ROOT, capture_output=True, text=True)
        payload = json.loads(result.stdout)
        if payload["gates"]["rq153c_runtime_accepts_input"]["pass"]:
            self.skipTest("interaction gate is satisfied; the failing case is not under test")
        self.assertEqual(payload["mechanical_coverage_verdict"], "FAIL")
        self.assertNotEqual(result.returncode, 0)

    def test_there_is_no_default_pass_path_in_the_interaction_check(self):
        # Guard the shape of the code itself: the only place `pass: True` may be
        # produced is after a receipt has been read and validated.
        gate = COVERAGE_GATE.read_text(encoding="utf-8")
        body = gate[gate.index("def check_runtime_interaction"):]
        body = body[:body.index("def run_gate")]
        self.assertIn('return {"pass": False', body,
                      "the absent-receipt branch must return a failure")
        self.assertNotIn('"pass": True', body,
                         "no literal pass:True may appear; it must be computed from a receipt")


if __name__ == "__main__":
    unittest.main()
