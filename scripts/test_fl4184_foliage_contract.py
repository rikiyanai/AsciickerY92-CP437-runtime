#!/usr/bin/env python3
"""Tests for the FL-4184 foliage contract validator.

Authority: docs/FAILURE_LOG.md entry FL-4184. These tests verify validator
behavior only; visual acceptance still belongs to headed four-yaw runtime proof.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_fl4184_foliage_contract.py"


def _run_validator(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_static_only_contract_passes() -> None:
    result = _run_validator("--static-only")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "FL4184_FOLIAGE_STATIC_CONTRACT_OK" in result.stdout


def test_default_contract_still_requires_runtime_proof() -> None:
    source = VALIDATOR.read_text(encoding="utf-8")
    static_only_branch = source.index("if args.static_only:")
    runtime_gate = source.index("gate_latest_runtime_proof()", static_only_branch)
    runtime_ok = source.index("FL4184_FOLIAGE_CONTRACT_AND_RUNTIME_PROOF_OK", runtime_gate)
    assert static_only_branch < runtime_gate < runtime_ok
    assert "current-head distinct-yaw runtime dumps" in source


def main() -> int:
    test_static_only_contract_passes()
    test_default_contract_still_requires_runtime_proof()
    print("FL4184_FOLIAGE_CONTRACT_TESTS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
