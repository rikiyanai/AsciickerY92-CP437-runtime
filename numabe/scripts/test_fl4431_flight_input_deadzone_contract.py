#!/usr/bin/env python3
"""FL-4431 proof-input deadzone contract.

The proof driver owns physical action choreography only. It declares the roll
axis Player must emit after Godot's deadzone, converts that target to the raw
Input.action_press strength using the live four-action InputMap average, then
lets the real Player.gd translate the action. FL-4431 now accepts the current
source directly, so this front door executes the real current Player path and
has no historical-clone prerequisite.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "godot_project/tools/proofs/run_fl4265_airplane_dogfight_proof.gd"
ENGINE_TEST = ROOT / "godot_project/scripts/tests/test_fl4431_flight_input_deadzone.gd"
RESULT_PREFIX = "FL4431_FLIGHT_INPUT_DEADZONE_RESULT "


def _source_contract(source: str) -> bool:
    """Bind both press sites to one InputMap-derived inverse-deadzone owner."""
    helper_match = re.search(
        r"func _fl4431_raw_strength_for_admitted_flight_axis\("
        r".*?(?=\nfunc |\Z)",
        source,
        flags=re.DOTALL,
    )
    if helper_match is None:
        return False
    helper = helper_match.group(0)
    required_helper_fragments = (
        "for action_name in FL4431_FLIGHT_VECTOR_ACTIONS:",
        "InputMap.action_get_deadzone(action_name)",
        "deadzone_sum / float(FL4431_FLIGHT_VECTOR_ACTIONS.size())",
        "effective_deadzone + admitted_magnitude * (1.0 - effective_deadzone)",
    )
    if any(fragment not in helper for fragment in required_helper_fragments):
        return False
    if "FL4359_TURN_ROLL_AXIS_ADMITTED := 0.35" not in source:
        return False
    if "FL4265_DAMAGE_ROLL_AXIS_ADMITTED := 0.18" not in source:
        return False
    if source.count("_fl4431_raw_strength_for_admitted_flight_axis(") < 5:
        return False
    if (
        "Input.action_press(FL4359_ROUTE_FLIGHT_ACTION, raw_flight_strength)"
        not in source
    ):
        return False
    if (
        "Input.action_press(FL4265_DAMAGE_FLIGHT_ACTION, raw_flight_strength)"
        not in source
    ):
        return False
    forbidden_direct_presses = (
        "Input.action_press("
        "FL4359_ROUTE_FLIGHT_ACTION, FL4359_TURN_ROLL_AXIS_ADMITTED"
        ")",
        "Input.action_press("
        "FL4265_DAMAGE_FLIGHT_ACTION, FL4265_DAMAGE_ROLL_AXIS_ADMITTED"
        ")",
    )
    return all(fragment not in source for fragment in forbidden_direct_presses)


def test_driver_uses_one_live_inputmap_deadzone_inverse() -> None:
    source = HARNESS.read_text(encoding="utf-8")
    assert _source_contract(source)


def test_source_contract_rejects_real_deadzone_regressions() -> None:
    source = HARNESS.read_text(encoding="utf-8")
    mutations = {
        "route_direct_raw": source.replace(
            "Input.action_press(FL4359_ROUTE_FLIGHT_ACTION, raw_flight_strength)",
            "Input.action_press("
            "FL4359_ROUTE_FLIGHT_ACTION, FL4359_TURN_ROLL_AXIS_ADMITTED"
            ")",
            1,
        ),
        "damage_direct_raw": source.replace(
            "Input.action_press(FL4265_DAMAGE_FLIGHT_ACTION, raw_flight_strength)",
            "Input.action_press("
            "FL4265_DAMAGE_FLIGHT_ACTION, FL4265_DAMAGE_ROLL_AXIS_ADMITTED"
            ")",
            1,
        ),
        "deadzone_read_deleted": source.replace(
            "InputMap.action_get_deadzone(action_name)",
            "0.0",
            1,
        ),
        "inverse_deleted": source.replace(
            "effective_deadzone + admitted_magnitude * (1.0 - effective_deadzone)",
            "admitted_magnitude",
            1,
        ),
    }
    assert all(mutated != source for mutated in mutations.values())
    for name, mutated in mutations.items():
        assert not _source_contract(mutated), name


def _run_current_engine() -> dict:
    godot = os.environ.get("GODOT_BIN") or shutil.which("godot")
    assert godot, "godot executable not found"
    assert (ROOT / "godot_project/project.godot").is_file(), ROOT
    # Godot's default rotating log is process-global. A unique log path keeps
    # this engine-executed contract independent from concurrent Godot checks.
    with tempfile.TemporaryDirectory(prefix="fl4431-deadzone-") as temp_dir:
        proc = subprocess.run(
            [
                godot,
                "--headless",
                "--path",
                str(ROOT / "godot_project"),
                "--log-file",
                str(Path(temp_dir) / "godot.log"),
                "--script",
                str(ENGINE_TEST),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=60,
            check=False,
        )
    result_lines = [
        line[len(RESULT_PREFIX) :]
        for line in proc.stdout.splitlines()
        if line.startswith(RESULT_PREFIX)
    ]
    assert proc.returncode == 0, proc.stdout
    assert len(result_lines) == 1, proc.stdout
    result = json.loads(result_lines[0])
    assert result["ok"] is True
    return result


def _semantic_rows(result: dict) -> list[dict]:
    return [
        {
            key: round(float(row[key]), 6)
            for key in (
                "admitted_roll_magnitude",
                "raw_action_strength",
                "pressed_roll",
                "released_roll",
            )
        }
        for row in result["rows"]
    ]


def test_real_current_player_input_translation() -> None:
    current = _run_current_engine()
    expected = [
        {
            "admitted_roll_magnitude": 0.35,
            "raw_action_strength": 0.675,
            "pressed_roll": -0.35,
            "released_roll": 0.0,
        },
        {
            "admitted_roll_magnitude": 0.18,
            "raw_action_strength": 0.59,
            "pressed_roll": -0.18,
            "released_roll": 0.0,
        },
    ]
    assert _semantic_rows(current) == expected


def main() -> int:
    test_driver_uses_one_live_inputmap_deadzone_inverse()
    test_source_contract_rejects_real_deadzone_regressions()
    test_real_current_player_input_translation()
    print(
        "FL4431_FLIGHT_INPUT_DEADZONE_CONTRACT_PASS "
        "arm=current rows=2 mutations=4"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
