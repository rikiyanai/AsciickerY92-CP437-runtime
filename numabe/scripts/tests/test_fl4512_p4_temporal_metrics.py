#!/usr/bin/env python3
"""Synthetic contracts for the offline U6 P4 trajectory metric harness."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
HARNESS = REPO / "scripts" / "fl4512_p4_temporal_metrics.py"
HASH_VERSION = "fl4208.surface_id_hash.fnv1a64.packed24.v1"


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def margin_table() -> dict:
    table = {
        "schema": "fl4512.temporal_margin_table.v1",
        "calibration_id": "provisional-n2",
        "method": "nearest_rank_p99.v1",
        "perceptual_claim": False,
        "suppressed_real_change": 0,
        "corpus": [], "corpus_hash": "a" * 64,
        "rows": [{
            "scorer_kind": 2, "scorer_version": 1,
            "score_domain": "u7.mask.v1.score_num.u32",
            "delta_keep": {"type": "u32", "value": 10},
        }],
    }
    table["table_sha256"] = canonical_hash(table)
    return table


def tx(frame: int, gid: int, *, decision: str, sid: dict | None,
       sid_prev: dict | None = None, absent_ctr: int = 0,
       collision: bool = False, presented_live: bool = True) -> dict:
    return {
        "schema": "fl4512.temporal_transaction.v2", "frame": frame,
        "cell": [4, 5], "sid_prev": sid_prev, "sid_cur": sid,
        "identity_gate": "COLLISION" if collision else "VALID",
        "surface_id_hash_version": HASH_VERSION,
        "g_prev": 10, "g_star": gid, "g_selected": gid if decision != "KEEP" else 10,
        "scorer_kind_prev": 2, "scorer_version_prev": 1,
        "scorer_kind_cur": 2, "scorer_version_cur": 1,
        "calibration_id_prev": "provisional-n2",
        "calibration_id_cur": "provisional-n2",
        "margin_table_hash": margin_table()["table_sha256"],
        "score_domain": "u7.mask.v1.score_num.u32",
        "s_prev_u32": 10, "s_star_u32": 5 if decision == "KEEP" else 0,
        "delta_keep_u32": 10, "s_prev_f32_bits": 0, "s_star_f32_bits": 0,
        "delta_keep_f32_bits": 0, "absent_ctr": absent_ctr, "decision": decision,
        "presented_live": presented_live, "previous_legal": True,
        "metadata_valid": not collision, "calibration_match": True,
        "winner_match": True,
        "collision": {"contaminated": collision, "surface_id_cells": 2 if collision else 1},
    }


class P4TemporalMetricsTest(unittest.TestCase):
    def run_harness(self, rows: list[dict], presentation: list[dict] | None = None,
                    expect_success: bool = True) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            table = root / "margin.json"
            transactions = root / "transactions.jsonl"
            replay = root / "replay.json"
            result = root / "result.json"
            table.write_text(json.dumps(margin_table()), encoding="utf-8")
            transactions.write_text("".join(json.dumps(row) + "\n" for row in rows),
                                    encoding="utf-8")
            command = [sys.executable, str(HARNESS), str(transactions),
                       "--margin-table", str(table), "--replay-out", str(replay),
                       "--out", str(result)]
            if presentation is not None:
                presentation_path = root / "presentation.jsonl"
                presentation_path.write_text(
                    "".join(json.dumps(row) + "\n" for row in presentation),
                    encoding="utf-8")
                command.extend(["--presentation-frame", str(presentation_path)])
            completed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0 if expect_success else 1,
                             completed.stderr + completed.stdout)
            return json.loads(result.read_text(encoding="utf-8"))

    def test_tracks_keep_disocclusion_eviction_collision_and_visible_signature(self):
        sid = {"world_generation": 1, "owner_x": 4, "owner_y": 8,
               "anchor_u_q": 3, "anchor_v_q": 7}
        rows = [
            tx(1, 10, decision="FIRST", sid=sid),
            tx(2, 11, decision="KEEP", sid=sid, sid_prev=sid),
            tx(3, 12, decision="FIRST", sid=sid, sid_prev=sid, absent_ctr=1),
            tx(4, 13, decision="EVICT", sid=None, absent_ctr=3, presented_live=False),
            tx(5, 14, decision="REPLACE_IDENTITY", sid=sid, collision=True),
        ]
        presentation = [
            {"schema": "fl4512.temporal_presentation_frame.v1", "frame": 1,
             "cell": [4, 5], "gid": 10, "fg": 7, "bg": 0},
            {"schema": "fl4512.temporal_presentation_frame.v1", "frame": 2,
             "cell": [4, 5], "gid": 10, "fg": 7, "bg": 0},
            {"schema": "fl4512.temporal_presentation_frame.v1", "frame": 3,
             "cell": [4, 5], "gid": 12, "fg": 7, "bg": 0},
            {"schema": "fl4512.temporal_presentation_frame.v1", "frame": 5,
             "cell": [4, 5], "gid": 14, "fg": 1, "bg": 0},
        ]
        result = self.run_harness(rows, presentation)
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["canonical_replay"]["verdict"], "PASS")
        self.assertEqual(result["trajectory"]["event_counts"],
                         {"collision": 1, "disocclusion": 1, "eviction": 1})
        self.assertEqual(result["presentation"]["status"], "MEASURED")
        self.assertEqual(result["presentation"]["visible_signature_switches"], 1)

    def test_absent_presentation_is_unmeasured_not_a_visual_pass(self):
        sid = {"world_generation": 1, "owner_x": 4, "owner_y": 8,
               "anchor_u_q": 3, "anchor_v_q": 7}
        result = self.run_harness([tx(1, 10, decision="FIRST", sid=sid)])
        self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(result["presentation"]["status"], "UNMEASURED")

    def test_rejects_a_collision_that_keeps_an_incumbent(self):
        sid = {"world_generation": 1, "owner_x": 4, "owner_y": 8,
               "anchor_u_q": 3, "anchor_v_q": 7}
        row = tx(1, 11, decision="KEEP", sid=sid, collision=True)
        result = self.run_harness([row], expect_success=False)
        reasons = {reason for failure in result["failures"]
                   for reason in failure.get("reasons", [])}
        self.assertIn("collision_not_identity_replace", reasons)


if __name__ == "__main__":
    unittest.main()
