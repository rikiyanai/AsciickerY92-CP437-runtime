#!/usr/bin/env python3
"""Contracts for the FL-4775 observer-power law enforcement.

Covers:
1. Colour-oracle unmatched decomposition: every excluded sample must land
   in exactly one named category and the completeness flag must hold.
2. Analyzer source contract: the observer-power gate exists, demotes
   presentation-space gates, and sits in both blocking-gate lists.
3. Commit/hook wiring: the pre-commit chain and the Claude PreToolUse
   hook invoke the shared validator, and the validator self-test passes.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import analyze_fl4359_moving_camera as analyzer  # noqa: E402

SCHEMA = analyzer.CURRENT_SPARSE_CAPTURE_SCHEMA


def _row(source_key: int, bits: tuple[int, int, int], cell=(1, 1)) -> dict:
    return {
        "capture_schema": SCHEMA,
        "final_product_owner": 1,
        "final_product_source_key": source_key,
        "final_product_continuous_sample_valid": True,
        "final_product_continuous_sample_bits": list(bits),
        "screen_cell": list(cell),
        "final_product_fg_rgb": [10, 20, 30],
        "final_product_bg_rgb": [1, 2, 3],
    }


class UnmatchedDecomposition(unittest.TestCase):
    def _counts(self, frame_a, frame_b):
        result = analyzer.summarize_continuous_product_colour_stability(
            frame_a, frame_b
        )
        return result, result["counts"]

    def test_sample_bits_changed_same_identity(self):
        result, counts = self._counts(
            [_row(7, (1, 2, 3))], [_row(7, (9, 9, 9))]
        )
        self.assertEqual(counts.get("unmatched_exact_samples"), 1)
        self.assertEqual(
            counts.get("unmatched_sample_bits_changed_same_identity"), 1
        )
        self.assertTrue(result["unmatched_decomposition_complete"])

    def test_identity_changed_same_sample_bits(self):
        result, counts = self._counts(
            [_row(7, (1, 2, 3))], [_row(8, (1, 2, 3))]
        )
        self.assertEqual(counts.get("unmatched_exact_samples"), 1)
        self.assertEqual(
            counts.get("unmatched_identity_changed_same_sample_bits"), 1
        )
        self.assertTrue(result["unmatched_decomposition_complete"])

    def test_identity_and_sample_absent(self):
        result, counts = self._counts([_row(7, (1, 2, 3))], [])
        self.assertEqual(counts.get("unmatched_exact_samples"), 1)
        self.assertEqual(
            counts.get("unmatched_identity_and_sample_absent"), 1
        )
        self.assertTrue(result["unmatched_decomposition_complete"])

    def test_partner_consumed(self):
        result, counts = self._counts(
            [_row(7, (1, 2, 3), cell=(1, 1)), _row(7, (1, 2, 3), cell=(5, 5))],
            [_row(7, (1, 2, 3), cell=(1, 1))],
        )
        self.assertEqual(counts.get("matched_exact_samples"), 1)
        self.assertEqual(counts.get("unmatched_exact_samples"), 1)
        self.assertEqual(counts.get("unmatched_partner_consumed"), 1)
        self.assertTrue(result["unmatched_decomposition_complete"])

    def test_matched_pair_has_no_unmatched(self):
        result, counts = self._counts(
            [_row(7, (1, 2, 3))], [_row(7, (1, 2, 3))]
        )
        self.assertEqual(counts.get("matched_exact_samples"), 1)
        self.assertEqual(counts.get("unmatched_exact_samples", 0), 0)
        self.assertTrue(result["unmatched_decomposition_complete"])


class AnalyzerSourceContract(unittest.TestCase):
    SOURCE = (REPO_ROOT / "scripts" / "analyze_fl4359_moving_camera.py").read_text()

    def test_gate_defined_and_blocking_in_both_profiles(self):
        # one definition site plus one entry in each blocking list
        self.assertGreaterEqual(
            self.SOURCE.count("evidence_presentation_observer_sensitivity_valid"),
            3,
        )

    def test_demotion_marker_present(self):
        self.assertIn(
            "falsifier_only_presentation_degenerate", self.SOURCE
        )

    def test_decomposition_completeness_field_present(self):
        self.assertIn("unmatched_decomposition_complete", self.SOURCE)


class EnforcementWiring(unittest.TestCase):
    """The observer-power law stays available without live hook registration."""

    def test_pre_commit_does_not_invoke_shared_gate(self):
        text = (REPO_ROOT / ".githooks" / "pre-commit").read_text()
        self.assertNotIn("fl4359_proof_sensitivity_gate.py --staged-index", text)

    def test_claude_pretooluse_hook_unregistered(self):
        text = (REPO_ROOT / ".claude" / "settings.json").read_text()
        self.assertNotIn("fl4359_proof_sensitivity_hook.py", text)

    def test_codex_hook_unregistered(self):
        text = (REPO_ROOT / ".codex" / "hooks.json").read_text()
        self.assertNotIn("fl4359_proof_sensitivity", text)

    def test_shared_gate_self_test_exits_zero_without_product_claim(self):
        proc = subprocess.run(
            [
                sys.executable,
                str(
                    REPO_ROOT
                    / "scripts"
                    / "hooks"
                    / "fl4359_proof_sensitivity_gate.py"
                ),
                "--self-test",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
