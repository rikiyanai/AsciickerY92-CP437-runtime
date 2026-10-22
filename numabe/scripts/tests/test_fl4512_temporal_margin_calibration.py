"""Fail-closed calibration contracts for exact FL-4512 product scorers."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

ROOT = Path(__file__).resolve().parents[2]
ANALYZER = ROOT / "scripts/analyze_fl4208_temporal_measurement.py"
REPLAY_ANALYZER = ROOT / "scripts/analyze_fl4512_temporal_transactions.py"
SCHEMA = ROOT / "assets/glyphs/schema/fl4512.temporal_margin_table.v1.schema.json"
HISTORICAL_TABLE = ROOT / "assets/glyphs/profiles/temporal_hysteresis.v1.json"


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _summary() -> dict:
    return {
        "schema": "fl4208.temporal_measurement.v2",
        "record_kind": "frame_summary",
        "frame": 1,
        "surface_id_valid_cells": 3,
        "collision_cells": 0,
        "mesh_surface_id_valid_cells": 3,
        "mesh_owner_hash_collision_cells": 0,
    }


def _cell(kind: int, version: int, domain: str, margin: int) -> dict:
    scorer_id = {
        (1, 1): "fl4260.shape6_weighted_l2.v1",
        (1, 2): "fl4512.shape6_f8_w4.v2",
        (2, 1): "u7.mask.v1",
        (3, 1): "S_pf.v1",
    }[(kind, version)]
    return {
        "schema": "fl4208.temporal_measurement.v2",
        "record_kind": "cell",
        "frame": 1,
        "cell": [0, kind],
        "gate": "CALIBRATION_CANDIDATE",
        "g_prev": 10,
        "g_star": 11,
        "replay_gid": 11,
        "sid_cur": {"world_generation": 1, "owner_kind": 1, "owner_id": kind},
        "selected_role": 1 if kind == 1 else 4,
        "profile_namespace": 1,
        "material_key": 1,
        "pool_revision": 123,
        "descriptor_kind": 1,
        "descriptor_version": 1,
        "scorer_kind_prev": kind,
        "scorer_version_prev": version,
        "scorer_kind_cur": kind,
        "scorer_version_cur": version,
        "scorer_id": scorer_id,
        "score_domain": domain,
        "calibration_id_prev": "unassigned",
        "calibration_id_cur": "unassigned",
        "margin_table_hash": None,
        "s_prev_u32": margin + 5,
        "s_star_u32": 5,
        "margin_u32": margin,
        "previous_legal": True,
        "metadata_valid": True,
        "absent_ctr": 0,
        "collision": False,
        "winner_match": True,
    }


def _product_cells() -> list[dict]:
    return [
        _cell(1, 2, "fl4512.shape6_f8_w4.v2.score_int.u32", 1200),
        _cell(2, 1, "u7.mask.v1.score_num.u32", 730),
        _cell(3, 1, "S_pf.v1.score_num.u32", 4096),
    ]


def _run(tmp_path: Path, cells: list[dict]) -> tuple[subprocess.CompletedProcess[str], dict, Path]:
    measurement = tmp_path / "measurement.jsonl"
    analysis = tmp_path / "analysis.json"
    table = tmp_path / "table.json"
    measurement.write_text(
        "".join(json.dumps(row) + "\n" for row in [_summary(), *cells]),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(ANALYZER),
            str(measurement),
            "--out",
            str(analysis),
            "--margin-table-out",
            str(table),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed, json.loads(analysis.read_text(encoding="utf-8")), table


def _run_targeted_harri(tmp_path: Path) -> tuple[subprocess.CompletedProcess[str], dict, Path]:
    measurement = tmp_path / "harri-measurement.jsonl"
    analysis = tmp_path / "harri-analysis.json"
    table = tmp_path / "harri-table.json"
    measurement.write_text(
        "".join(json.dumps(row) + "\n" for row in [
            _summary(),
            _cell(1, 2, "fl4512.shape6_f8_w4.v2.score_int.u32", 1200),
        ]),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(ANALYZER),
            str(measurement),
            "--out",
            str(analysis),
            "--margin-table-out",
            str(table),
            "--target-scorer-id",
            "fl4512.shape6_f8_w4.v2",
            "--base-margin-table",
            str(HISTORICAL_TABLE),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return completed, json.loads(analysis.read_text(encoding="utf-8")), table


def test_authored_exact_argmin_baseline_is_honest_and_spf_stays_absent() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    table = json.loads(HISTORICAL_TABLE.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(table)
    assert table["method"] == "mixed_policy_basis.v1"
    assert table["table_sha256"] == _canonical_hash({
        key: value for key, value in table.items() if key != "table_sha256"
    })
    assert len(table["rows"]) == 2
    identities = {
        (row["scorer_kind"], row["scorer_version"], row["score_domain"])
        for row in table["rows"]
    }
    assert (1, 1, "fl4260.shape6_weighted_l2.v1.binary32") not in identities
    assert (1, 2, "fl4512.shape6_f8_w4.v2.score_int.u32") in identities
    assert (3, 1, "S_pf.v1.score_num.u32") not in identities
    harri = next(row for row in table["rows"] if row["scorer_kind"] == 1)
    assert harri == {
        "scorer_kind": 1,
        "scorer_version": 2,
        "scorer_id": "fl4512.shape6_f8_w4.v2",
        "score_domain": "fl4512.shape6_f8_w4.v2.score_int.u32",
        "calibration_id": "authored-exact-argmin-tie-v1",
        "policy_basis": "authored_exact_argmin_tie.v1",
        "delta_keep": {"type": "u32", "value": 0},
    }
    assert len(harri["calibration_id"]) <= 31


def test_generator_emits_only_complete_exact_product_scorer_set(tmp_path: Path) -> None:
    completed, analysis, table_path = _run(tmp_path, _product_cells())
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert analysis["calibration_ready"] is True
    assert analysis["missing_product_scorers"] == []
    assert analysis["unexpected_scorers"] == []
    table = json.loads(table_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(
        json.loads(SCHEMA.read_text(encoding="utf-8"))
    ).validate(table)
    assert table["table_sha256"] == _canonical_hash({
        key: value for key, value in table.items() if key != "table_sha256"
    })
    assert [
        (row["scorer_kind"], row["scorer_version"], row["score_domain"])
        for row in table["rows"]
    ] == [
        (1, 2, "fl4512.shape6_f8_w4.v2.score_int.u32"),
        (2, 1, "u7.mask.v1.score_num.u32"),
        (3, 1, "S_pf.v1.score_num.u32"),
    ]

    transaction_path = tmp_path / "transactions.jsonl"
    replay_path = tmp_path / "replay.json"
    transaction_path.write_text(json.dumps({
        "schema": "fl4512.temporal_transaction.v2",
        "frame": 1,
        "cell": [0, 0],
        "sid_prev": {"world_generation": 1},
        "sid_cur": {"world_generation": 1},
        "identity_gate": "VALID",
        "surface_id_hash_version": "fl4208.surface_id_hash.fnv1a64.packed24.v1",
        "presented_live": True,
        "g_prev": 10,
        "g_star": 11,
        "g_selected": 10,
        "scorer_kind_prev": 1,
        "scorer_version_prev": 2,
        "scorer_kind_cur": 1,
        "scorer_version_cur": 2,
        "calibration_id_prev": "provisional-n2",
        "calibration_id_cur": "provisional-n2",
        "margin_table_hash": table["table_sha256"],
        "score_domain": "fl4512.shape6_f8_w4.v2.score_int.u32",
        "s_prev_u32": 1205,
        "s_star_u32": 5,
        "delta_keep_u32": 1200,
        "previous_legal": True,
        "metadata_valid": True,
        "calibration_match": True,
        "winner_match": True,
        "collision": {"contaminated": False},
        "absent_ctr": 0,
        "decision": "KEEP",
    }) + "\n", encoding="utf-8")
    replay = subprocess.run(
        [
            sys.executable,
            str(REPLAY_ANALYZER),
            str(transaction_path),
            "--margin-table",
            str(table_path),
            "--out",
            str(replay_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr + replay.stdout
    assert json.loads(replay_path.read_text(encoding="utf-8"))["verdict"] == "PASS"


def test_legacy_harri_corpus_cannot_calibrate_exact_integer_product(tmp_path: Path) -> None:
    cells = _product_cells()
    cells[0] = {
        **_cell(1, 1, "fl4260.shape6_weighted_l2.v1.binary32", 0),
        "margin_f32_bits": 1065353216,
    }
    completed, analysis, table_path = _run(tmp_path, cells)
    assert completed.returncode != 0
    assert not table_path.exists()
    assert analysis["calibration_ready"] is False
    assert analysis["missing_product_scorers"] == [{
        "scorer_kind": 1,
        "scorer_version": 2,
        "scorer_id": "fl4512.shape6_f8_w4.v2",
        "score_domain": "fl4512.shape6_f8_w4.v2.score_int.u32",
    }]
    assert analysis["unexpected_scorers"] == [{
        "scorer_kind": 1,
        "scorer_version": 1,
        "score_domain": "fl4260.shape6_weighted_l2.v1.binary32",
    }]


def test_missing_primitive_field_distribution_emits_no_table(tmp_path: Path) -> None:
    completed, analysis, table_path = _run(tmp_path, _product_cells()[:2])
    assert completed.returncode != 0
    assert not table_path.exists()
    assert analysis["calibration_ready"] is False
    assert analysis["missing_product_scorers"] == [{
        "scorer_kind": 3,
        "scorer_version": 1,
        "scorer_id": "S_pf.v1",
        "score_domain": "S_pf.v1.score_num.u32",
    }]


def test_targeted_harri_calibration_preserves_u7_without_inventing_spf(
    tmp_path: Path,
) -> None:
    completed, analysis, table_path = _run_targeted_harri(tmp_path)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert analysis["calibration_ready"] is True
    assert analysis["complete_product_calibration"] is False
    assert analysis["missing_product_scorers"] == []
    table = json.loads(table_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(
        json.loads(SCHEMA.read_text(encoding="utf-8"))
    ).validate(table)
    identities = [
        (row["scorer_kind"], row["scorer_version"], row["score_domain"])
        for row in table["rows"]
    ]
    assert identities == [
        (1, 2, "fl4512.shape6_f8_w4.v2.score_int.u32"),
        (2, 1, "u7.mask.v1.score_num.u32"),
    ]
    assert table["policy_set_id"] == "calibrated-policy-set-v1"
    assert table["method"] == "nearest_rank_p99.v1"
    assert all(
        row["policy_basis"] == "calibrated_nearest_rank_p99.v1"
        for row in table["rows"]
    )
    assert all(row["scorer_kind"] != 3 for row in table["rows"])
    assert len(table["corpus"]) == 2
    assert table["table_sha256"] == _canonical_hash({
        key: value for key, value in table.items() if key != "table_sha256"
    })


def test_targeted_harri_refuses_an_unreplayable_margin_identity(
    tmp_path: Path,
) -> None:
    measurement = tmp_path / "invalid-harri-measurement.jsonl"
    analysis = tmp_path / "invalid-harri-analysis.json"
    table = tmp_path / "invalid-harri-table.json"
    bad = _cell(1, 2, "fl4512.shape6_f8_w4.v2.score_int.u32", 1200)
    bad["s_prev_u32"] += 1
    measurement.write_text(
        "".join(json.dumps(row) + "\n" for row in [_summary(), bad]),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(ANALYZER),
            str(measurement),
            "--out",
            str(analysis),
            "--margin-table-out",
            str(table),
            "--target-scorer-id",
            "fl4512.shape6_f8_w4.v2",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0
    assert not table.exists()
    result = json.loads(analysis.read_text(encoding="utf-8"))
    assert result["calibration_ready"] is False
    assert result["candidate_replay_failures"][0]["reasons"] == [
        "u32_margin_identity"
    ]


def test_targeted_analysis_bounds_collision_witnesses_without_inference(
    tmp_path: Path,
) -> None:
    measurement = tmp_path / "collision-measurement.jsonl"
    analysis = tmp_path / "collision-analysis.json"
    summary = {**_summary(), "surface_id_valid_cells": 50, "collision_cells": 25}
    collision_rows = []
    for index in range(25):
        row = _cell(1, 2, "fl4512.shape6_f8_w4.v2.score_int.u32", 0)
        row.update({
            "cell": [index, 7],
            "gate": "COLLISION",
            "collision": True,
            "g_star": 10,
            "replay_gid": 0,
            "replay_claimed": False,
        })
        collision_rows.append(row)
    measurement.write_text(
        "".join(json.dumps(row) + "\n" for row in [summary, *collision_rows]),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(ANALYZER),
            str(measurement),
            "--out",
            str(analysis),
            "--target-scorer-id",
            "fl4512.shape6_f8_w4.v2",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    result = json.loads(analysis.read_text(encoding="utf-8"))
    assert result["target_gate_counts"] == {"COLLISION": 25}
    assert len(result["target_collision_samples"]) == 20
    assert result["target_collision_samples"][0]["cell"] == [0, 7]
    assert result["target_collision_samples"][-1]["cell"] == [19, 7]
