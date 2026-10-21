from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build_godot_feature_registry.py"
sys.path.insert(0, str(SCRIPT.parent))
import build_godot_feature_registry as builder  # noqa: E402
import godot_feature_regression_gate as gate  # noqa: E402


class BuildGodotFeatureRegistryTests(unittest.TestCase):
    def test_canonical_population_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshot = Path(directory) / "failure-log.md"
            snapshot.write_text(builder.DEFAULT_FAILURE_LOG.read_text(encoding="utf-8"), encoding="utf-8")
            registry, census = builder.build_documents(failure_log=snapshot)
            _, _, corrections, v5_corrections, routes = builder.parse_failure_log(snapshot)
        self.assertEqual(len(registry["features"]), 237)
        self.assertEqual(len(census["classified_commits"]), 5945)
        self.assertEqual(len(corrections), 2445)
        self.assertEqual(len(v5_corrections), census["v5_correction_count"])
        self.assertEqual(
            sum(record["semantic_review"] == "RESOLVED" for record in v5_corrections),
            census["v5_resolved_count"],
        )
        self.assertEqual(
            sum(record["semantic_review"] == "RED" for record in v5_corrections),
            census["v5_unresolved_count"],
        )
        self.assertEqual(
            census["remaining_semantic_reviews"],
            builder.EXPECTED_V4_CORRECTION_COUNT - len(v5_corrections),
        )
        outcome_reviewed, outcome_unlocated = builder._v5_outcome_counts(v5_corrections)
        self.assertEqual(census["v5_outcome_reviewed_count"], outcome_reviewed)
        self.assertEqual(census["v5_outcome_unlocated_count"], outcome_unlocated)
        self.assertEqual(census["remaining_outcome_reviews"], outcome_unlocated)
        self.assertEqual(len(routes), 237)
        self.assertEqual(len({route["feature_id"] for route in routes}), 237)
        self.assertEqual(len({row["id"] for row in registry["features"]}), 237)
        self.assertEqual(
            len({record["commit"] for record in census["classified_commits"]}), 5945
        )
        self.assertEqual(census["graph_baseline_commit"], builder.EXPECTED_GRAPH_BASELINE)
        self.assertEqual(census["coverage_through_commit"], builder.EXPECTED_COVERAGE_ENDPOINT)
        self.assertEqual(census["history_paths"], [])
        self.assertEqual(census["retirement_approvals"], [])

    def test_v4_corrections_cover_v3_red_rows_and_both_dialects(self) -> None:
        _, v3, v4, _, _ = builder.parse_failure_log(builder.DEFAULT_FAILURE_LOG)
        red_pairs = {
            (record["commit_sha"], int(record["order_index"]))
            for record in v3
            if record["disposition"] == "no-feature"
            and "semantic_review=RED" in record["evidence_refs"].split(";")
        }
        correction_pairs = {
            (record["commit_sha"], int(record["order_index"])) for record in v4
        }
        self.assertEqual(correction_pairs, red_pairs)
        self.assertEqual(len({record["commit_sha"] for record in v4}), 2445)
        self.assertEqual(sum("disposition" in record for record in v4), 1406)
        self.assertEqual(sum("reviewed_disposition" in record for record in v4), 1039)

    def test_v4_overlay_rejects_missing_duplicate_extra_and_mismatched_rows(self) -> None:
        commit = "a" * 40
        v3 = [
            {
                "commit_sha": commit,
                "order_index": "7",
                "disposition": "no-feature",
                "evidence_refs": "commit_a;semantic_review=RED",
            }
        ]
        correction = {
            "commit_sha": commit,
            "order_index": "7",
            "disposition": "no-feature",
            "feature_ids": "NONE",
            "reason": "unresolved fixture",
            "evidence_refs": "commit_a",
            "process_failure_category": "NONE",
            "semantic_review": "RED",
            "reviewer_status": "UNRESOLVED_RED",
        }
        for corrections in ([], [correction, correction], [{**correction, "commit_sha": "b" * 40}], [{**correction, "order_index": "8"}]):
            with self.assertRaises(builder.RegistryBuildError):
                builder._validate_v4_overlay(v3, corrections, {}, expected_count=None)

    def test_resolved_v4_feature_correction_overrides_matching_v3_row(self) -> None:
        commit = "a" * 40
        v3 = [
            {
                "commit_sha": commit,
                "order_index": "7",
                "disposition": "no-feature",
                "evidence_refs": "commit_a;semantic_review=RED",
            }
        ]
        correction = {
            "commit_sha": commit,
            "order_index": "7",
            "reviewed_disposition": "feature-modifying",
            "feature_ids": "GODOT-X",
            "reason": "resolved exact fixture feature",
            "evidence_refs": "commit_a;exact_feature_GODOT-X",
            "process_failure_category": "NONE",
            "semantic_review": "GREEN",
            "reviewer_status": "RESOLVED",
        }
        normalized = builder._validate_v4_overlay(
            v3, [correction], {"GODOT-X": {}}, expected_count=None
        )
        result = normalized[(commit, 7)]
        self.assertEqual(result["disposition"], "feature-modifying")
        self.assertEqual(result["feature_ids"], ["GODOT-X"])
        self.assertEqual(result["review_state"], "resolved")
        self.assertEqual(result["evidence_refs"], ["row_adjudication_v2:GODOT-X"])

    def test_git_set_and_order_are_preserved(self) -> None:
        _, census = builder.build_documents()
        expected_post_baseline = subprocess.run(
            [
                "git",
                "rev-list",
                f"{builder.EXPECTED_GRAPH_BASELINE}..{builder.EXPECTED_COVERAGE_ENDPOINT}",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        # The source stream deliberately puts the baseline first, followed by
        # the default post-baseline range order used by the canonical checksum.
        expected = [builder.EXPECTED_GRAPH_BASELINE] + expected_post_baseline
        self.assertEqual(
            [record["commit"] for record in census["classified_commits"]], expected
        )

    def test_unresolved_semantics_and_missing_checks_remain_blocking(self) -> None:
        registry, census = builder.build_documents()
        self.assertEqual(
            sum(record["review_state"] == "unresolved" for record in census["classified_commits"]),
            builder.EXPECTED_V4_CORRECTION_COUNT - census["v5_resolved_count"],
        )
        self.assertEqual(
            sum(record["review_state"] == "resolved" for record in census["classified_commits"]),
            len(census["classified_commits"]) - census["remaining_semantic_reviews"]
            - census["v5_unresolved_count"],
        )
        self.assertTrue(all(feature["state"] == "active" for feature in registry["features"]))
        self.assertTrue(all(feature["check"] is None for feature in registry["features"]))
        behavior_counts = {
            behavior_class: sum(
                feature["behavior_class"] == behavior_class
                for feature in registry["features"]
            )
            for behavior_class in builder.EXPECTED_BEHAVIOR_CLASS_COUNTS
        }
        self.assertEqual(behavior_counts, builder.EXPECTED_BEHAVIOR_CLASS_COUNTS)
        shoreline = next(feature for feature in registry["features"] if feature["id"] == "GODOT-W07")
        self.assertEqual(shoreline["behavior_class"], "temporal")
        self.assertIn("check=null", shoreline["observable"])
        self.assertIn("current_state=ACTIVE_REGRESSED", shoreline["observable"])
        self.assertIn("route_state=MISSING", shoreline["observable"])

    def test_route_parser_handles_front_door_concatenation(self) -> None:
        line = (
            "ROUTE_RECORDS_BEGIN||||"
            "record_type=feature_route_census_v1|feature_id=GODOT-X|"
            "human_name=x|behavior_class=runtime|route_state=MISSING|"
            "route_argv=NONE|target=NONE|build=NONE|map=NONE|scene=NONE|"
            "pose=NONE|camera=NONE|mode=NONE|baseline=NONE|timing_window=NONE|"
            "receipt_path=NONE|receipt_schema=NONE|required_observation_keys=NONE|"
            "missing_reason=not_found|evidence_refs=row_x|reviewer_status=UNRESOLVED_RED"
            "||ROUTE_RECORDS_END"
        )
        routes = builder._route_records_from_line(line, 42)
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0]["feature_id"], "GODOT-X")
        self.assertEqual(routes[0]["_line_number"], "42")

    def test_route_stream_rejects_set_and_class_mismatches(self) -> None:
        rows = {f"GODOT-{index}": {} for index in range(237)}
        valid = {
            "feature_id": "GODOT-0",
            "human_name": "x",
            "behavior_class": "runtime",
            "route_state": "MISSING",
            "route_argv": "NONE",
            "target": "NONE",
            "build": "NONE",
            "map": "NONE",
            "scene": "NONE",
            "pose": "NONE",
            "camera": "NONE",
            "mode": "NONE",
            "baseline": "NONE",
            "timing_window": "NONE",
            "receipt_path": "NONE",
            "receipt_schema": "NONE",
            "required_observation_keys": "NONE",
            "missing_reason": "not_found",
            "evidence_refs": "row_x",
            "reviewer_status": "UNRESOLVED_RED",
        }
        routes = [dict(valid, feature_id=feature_id) for feature_id in rows]
        with self.assertRaises(builder.RegistryBuildError):
            builder._validate_route_stream(routes, rows)

    def test_v5_parser_stops_before_wrapping_prose(self) -> None:
        line = (
            "prefix session prose record_type=commit_semantic_v5|slice=fixture|"
            "commit_sha=" + "a" * 40 + "|order_index=7|"
            "prior_v4_ref=commit_semantic_v4_" + "a" * 40 + "_order_index_7|"
            "disposition=feature-introducing|feature_ids=GODOT-X|"
            "intent_from_commit=fixture intent|outcome_from_exact_FL_or_commit=outcome_unlocated|"
            "failure_reason_or_NONE=NONE|evidence_refs=exact_fixture|"
            "process_failure_category=NONE|reviewer_status=RESOLVED|semantic_review=RESOLVED"
            " role=gpt-5.6-luna"
        )
        records = builder._v5_records_from_line(line, 42)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["record_type"], "commit_semantic_v5")
        self.assertEqual(records[0]["semantic_review"], "RESOLVED")
        self.assertEqual(records[0]["_line_number"], "42")
        with self.assertRaises(builder.RegistryBuildError):
            builder._v5_records_from_line(line.split("|semantic_review=")[0], 43)

    def test_v5_overlay_requires_exact_prior_pair_and_preserves_red(self) -> None:
        sha = "a" * 40
        prior = {(sha, 7): {"disposition": "no-feature", "review_state": "unresolved"}}
        base = {
            "record_type": "commit_semantic_v5",
            "slice": "fixture",
            "commit_sha": sha,
            "order_index": "7",
            "prior_v4_ref": f"commit_semantic_v4_{sha}_order_index_7",
            "disposition": "feature-introducing",
            "feature_ids": "GODOT-X",
            "intent_from_commit": "fixture intent",
            "outcome_from_exact_FL_or_commit": "outcome_unlocated",
            "failure_reason_or_NONE": "NONE",
            "evidence_refs": "exact_fixture",
            "process_failure_category": "NONE",
            "reviewer_status": "RESOLVED",
            "semantic_review": "RESOLVED",
        }
        normalized = builder._validate_v5_overlay([base], prior, {"GODOT-X": {}}, expected_count=None)
        self.assertEqual(normalized[(sha, 7)]["disposition"], "feature-introducing")
        self.assertEqual(normalized[(sha, 7)]["feature_ids"], ["GODOT-X"])
        self.assertEqual(normalized[(sha, 7)]["review_state"], "resolved")

        unresolved = dict(
            base,
            disposition="process-failure",
            feature_ids="NONE",
            failure_reason_or_NONE="feature_identity_unresolved",
            process_failure_category="failure",
            reviewer_status="UNRESOLVED_RED",
            semantic_review="RED",
        )
        unresolved_result = builder._validate_v5_overlay(
            [unresolved], prior, {"GODOT-X": {}}, expected_count=None
        )
        self.assertEqual(unresolved_result[(sha, 7)]["review_state"], "unresolved")
        with self.assertRaises(builder.RegistryBuildError):
            builder._validate_v5_overlay([base, base], prior, {"GODOT-X": {}}, expected_count=None)
        with self.assertRaises(builder.RegistryBuildError):
            builder._validate_v5_overlay(
                [dict(base, feature_ids="GODOT-UNKNOWN")], prior, {"GODOT-X": {}}, expected_count=None
            )

    def test_v5_overlay_grows_to_201_and_full_v4_population(self) -> None:
        rows, v3, v4, v5, _ = builder.parse_failure_log(builder.DEFAULT_FAILURE_LOG)
        # FL-4512: exercise a partial fixture even after the live census is
        # complete; growth is not conditional on an unresolved production row.
        v5 = v5[:200]
        rows_by_id, _, _ = builder._validate_population(rows, v3, builder.REPO_ROOT)
        v4_map = builder._validate_v4_overlay(v3, v4, rows_by_id)
        current_pairs = {(record["commit_sha"], int(record["order_index"])) for record in v5}
        extra_pair = next(pair for pair in v4_map if pair not in current_pairs)
        extra_sha, extra_index = extra_pair
        extra = dict(v5[0])
        extra.update(
            commit_sha=extra_sha,
            order_index=str(extra_index),
            prior_v4_ref=f"commit_semantic_v4_{extra_sha}_order_index_{extra_index}",
            disposition="process-only",
            feature_ids="NONE",
            intent_from_commit="synthetic growth fixture",
            outcome_from_exact_FL_or_commit="outcome_unlocated",
            failure_reason_or_NONE="NONE",
            evidence_refs="synthetic_growth_fixture",
            process_failure_category="NONE",
            reviewer_status="RESOLVED",
            semantic_review="RESOLVED",
        )
        grown = builder._validate_v5_overlay(v5 + [extra], v4_map, rows_by_id)
        self.assertEqual(len(grown), len(v5) + 1)

        full = []
        for commit_sha, order_index in v4_map:
            full.append(
                dict(
                    extra,
                    commit_sha=commit_sha,
                    order_index=str(order_index),
                    prior_v4_ref=f"commit_semantic_v4_{commit_sha}_order_index_{order_index}",
                )
            )
        complete = builder._validate_v5_overlay(full, v4_map, rows_by_id)
        self.assertEqual(len(complete), 2445)
        self.assertEqual(builder.EXPECTED_V4_CORRECTION_COUNT - len(complete), 0)

    def test_generated_census_metadata_is_accepted_and_cross_validated_by_gate(self) -> None:
        registry, census = builder.build_documents()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            census_path = target / "census.json"
            registry["census"]["expected_census_path"] = str(census_path)
            census_path.write_text(json.dumps(census), encoding="utf-8")
            result = gate.validate_registry(registry, base_dir=ROOT)
            self.assertFalse(
                any(
                    "v5_" in error and "unknown key" in error
                    for error in result.errors
                )
            )
            self.assertFalse(
                any("remaining_semantic_reviews" in error and "unknown key" in error for error in result.errors)
            )
            self.assertTrue(
                any("unresolved outcome coverage" in error for error in result.errors)
            )
            census["v5_unresolved_count"] += 1
            census_path.write_text(json.dumps(census), encoding="utf-8")
            mismatch = gate.validate_registry(registry, base_dir=ROOT)
            self.assertTrue(
                any("v5 resolved/unresolved counts do not equal" in error for error in mismatch.errors)
            )

    def test_feature_records_reference_only_exact_row_evidence(self) -> None:
        registry, census = builder.build_documents()
        feature_ids = set(census["feature_ids"])
        evidence_ids = set(census["evidence_ids"])
        self.assertEqual(evidence_ids, {builder._feature_evidence_id(value) for value in feature_ids})
        for record in census["classified_commits"]:
            if record["disposition"] in {"no-feature", "process-only", "process-failure"}:
                self.assertEqual(record["feature_ids"], [])
                self.assertEqual(record["evidence_refs"], [])
                continue
            self.assertTrue(record["feature_ids"])
            self.assertEqual(
                record["evidence_refs"],
                [builder._feature_evidence_id(feature_id) for feature_id in record["feature_ids"]],
            )
            self.assertTrue(set(record["evidence_refs"]).issubset(evidence_ids))

    def test_two_writes_are_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            first_registry = target / "first-registry.json"
            first_census = target / "first-census.json"
            second_registry = target / "second-registry.json"
            second_census = target / "second-census.json"
            builder.write_outputs(
                registry_path=first_registry,
                census_path=first_census,
            )
            builder.write_outputs(
                registry_path=second_registry,
                census_path=second_census,
            )
            self.assertEqual(first_registry.read_bytes(), second_registry.read_bytes())
            self.assertEqual(first_census.read_bytes(), second_census.read_bytes())

    def test_unresolved_source_mutation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "failure-log.md"
            path.write_text(
                "row_adjudication_v2|feature_id=GODOT-X|disposition=KEEP|human_name=x|category=x|parent_family=X|admission_source=x|admission_ref=x|historical_exact_ref=x|current_owner=x|row_state=ACTIVE|current_state=ACTIVE|machine_coverage=GREEN|retirement_record=NONE|reason=x\n",
                encoding="utf-8",
            )
            with self.assertRaises(builder.RegistryBuildError):
                builder.build_documents(failure_log=path)


if __name__ == "__main__":
    unittest.main()
