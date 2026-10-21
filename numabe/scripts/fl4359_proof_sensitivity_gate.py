#!/usr/bin/env python3
"""Compatibility transport for the consolidated FL-4359 gate owner.

Hook processes loaded before consolidation import this module. It owns no
ledger, audit state, or proof policy. All decisions delegate to
scripts/hooks/fl4359_proof_sensitivity_gate.py.
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OWNER = ROOT / "scripts" / "hooks" / "fl4359_proof_sensitivity_gate.py"


def _load_policy():
    spec = importlib.util.spec_from_file_location("fl4359_proof_sensitivity_policy", OWNER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load consolidated FL-4359 policy owner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POLICY = _load_policy()


def _run(*args: str) -> int:
    return subprocess.run(
        [sys.executable, str(OWNER), *args], cwd=ROOT, check=False
    ).returncode


def command_requires_gate(command: str) -> bool:
    """Compatibility API used by already-loaded Claude/Codex hook code."""
    return POLICY.command_requires_governance(command)


def command_errors(command: str) -> list[str]:
    if not command_requires_gate(command):
        return []
    policy_errors = POLICY.command_policy_errors(command)
    if policy_errors:
        return policy_errors
    if _run("--force") == 0:
        return []
    return ["consolidated FL-4359 readiness or observer-power contract is red"]


def semantic_audit_errors() -> list[str]:
    """Legacy API name; the consolidated blocklist owns audit readiness."""
    return [] if _run("--force") == 0 else [
        "consolidated FL-4359 readiness or observer-power contract is red"
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    command = subparsers.add_parser("check-command")
    command.add_argument("--command", required=True)
    subparsers.add_parser("check-staged")
    subparsers.add_parser("status")
    subparsers.add_parser("bootstrap-audit")
    args = parser.parse_args(argv)

    if args.command == "check-staged":
        return _run("--staged-index")
    if args.command in {"status", "bootstrap-audit"}:
        return _run("--force")
    errors = command_errors(args.command)
    if not errors:
        return 0
    for error in errors:
        print(f"FL4359_PROOF_SENSITIVITY_GUARD_BLOCKED: {error}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
