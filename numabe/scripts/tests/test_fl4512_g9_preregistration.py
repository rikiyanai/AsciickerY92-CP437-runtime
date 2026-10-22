from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts/fl4512_validate_g9_preregistration.py"
FIXTURE = ROOT / "docs/research/ascii/verification/fl4512/evaluation/preregistration.v1.json"


def run(data: dict, *, expected_code: int) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "preregistration.json"
        output = Path(directory) / "result.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, str(VALIDATOR), str(path), "--repo", str(ROOT), "--out", str(output)],
            cwd=ROOT, text=True, capture_output=True,
        )
        assert completed.returncode == expected_code, completed.stdout + completed.stderr
        return json.loads(output.read_text(encoding="utf-8"))


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_g9_preregistration_is_unmeasured_and_valid() -> None:
    result = run(fixture(), expected_code=0)
    assert result["verdict"] == "PASS"
    assert result["measurement_state"] == "NOT_RUN"


def test_g9_preregistration_rejects_scene_hash_drift() -> None:
    data = fixture()
    data["scene_manifest"]["static_scenes"][0]["sha256"] = "0" * 64
    result = run(data, expected_code=1)
    assert "scene_hash:p2_y8_original" in result["failures"]


def test_g9_preregistration_rejects_result_rows_before_measurement() -> None:
    data = fixture()
    data["metric_registry"]["result_rows"] = [{"metric_id": "frame_time_p95_ms", "value": 1.0}]
    result = run(data, expected_code=1)
    assert "result_rows_present" in result["failures"]


def test_g9_preregistration_rejects_perceptual_materials_before_authoring() -> None:
    data = fixture()
    data["perceptual_study_materials"]["participant_records"] = [{"participant_id": "not_allowed"}]
    result = run(data, expected_code=1)
    assert "study_materials_present" in result["failures"]
