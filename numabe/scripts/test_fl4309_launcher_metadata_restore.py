#!/usr/bin/env python3
"""FL-4309/FL-4310 launcher metadata must not point at deleted front doors."""

from __future__ import annotations

from scripts.launcher_lib.option_tree import flatten_options
from scripts.launcher_lib.smart_derive import DerivedRunIntent
from scripts.watchdog_runner import build_parser


DELETED_COMMAND_PATHS = {
    "scripts/watchdog_run_canonical.py",
    "scripts/watchdog_recipe_store.py",
}


def _commands_from_option_tree() -> list[list[str]]:
    commands: list[list[str]] = []
    for item in flatten_options():
        command = item.get("command")
        if isinstance(command, list):
            commands.append([str(part) for part in command])
    return commands


def test_launcher_option_tree_does_not_emit_deleted_watchdog_paths() -> None:
    offenders = [
        command
        for command in _commands_from_option_tree()
        if any(part in DELETED_COMMAND_PATHS for part in command)
    ]
    assert offenders == []


def test_smart_derive_composes_watchdog_runner_front_door() -> None:
    args = DerivedRunIntent().to_cli_args()
    assert args[:2] == ["python3", "scripts/watchdog_runner.py"]
    assert "scripts/watchdog_run_canonical.py" not in args


def test_smart_derive_command_is_parseable_by_watchdog_runner() -> None:
    args = DerivedRunIntent(
        baseline_runs=["run-a"],
        fl_targets=["FL-4309"],
        fix_attempt_refs=["abc123"],
        introduced_by_refs=["def456"],
        required_fields=["field-a"],
        suggested_observation="observed",
        suggested_intent="intent",
    ).to_cli_args()

    parsed = build_parser().parse_args(args[2:])

    assert parsed.mode == "full"
    assert parsed.target == "candidate"
