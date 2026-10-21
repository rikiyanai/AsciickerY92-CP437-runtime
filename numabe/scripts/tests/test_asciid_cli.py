"""The one operator CLI reaches the real A3D and Classic owners."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts/asciid.py"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_help_names_the_three_operator_actions() -> None:
    result = run_cli("--help")
    assert result.returncode == 0
    for command in ("check-contract", "downgrade-to-classic", "launch-classic"):
        assert command in result.stdout


def test_contract_check_crosses_the_cli() -> None:
    result = run_cli("check-contract")
    assert result.returncode == 0
    assert "runtime data contract OK" in result.stdout


def test_classic_launch_plan_crosses_the_cli() -> None:
    result = run_cli("launch-classic", "--dry-run")
    assert result.returncode == 0
    plan = json.loads(result.stdout)
    assert plan["binary_path"].endswith("/.run/asciiid-classic")
    assert plan["ownership"]["shared_path_is_shared_session"] is False


def test_downgrade_cannot_overwrite_source() -> None:
    source = ROOT / "assets/a3d/frog_pieta_4x4.a3d"
    result = run_cli("downgrade-to-classic", str(source), "--out", str(source))
    assert result.returncode != 0
    assert "source overwrite forbidden" in result.stdout
