#!/usr/bin/env python3
"""Destructively test Baseline-v2 admission at the detached launch boundary.

The detached launcher is the last inexpensive fail-closed boundary before an
acceptance package starts. These tests damage the forwarded receipt argument,
damage receipt bytes, and make the validator reject an otherwise plausible
path. Every rejected case must leave the process-spawn sentinel untouched.

Run with either command:

    python3 -m pytest scripts/test_fl4431_baseline_receipt_v2_detached_launcher.py -q
    python3 scripts/test_fl4431_baseline_receipt_v2_detached_launcher.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = ROOT / "scripts" / "launch_fl4359_acceptance_package_detached.py"
SPEC = importlib.util.spec_from_file_location(
    "fl4431_baseline_v2_detached_launcher",
    LAUNCHER_PATH,
)
assert SPEC is not None and SPEC.loader is not None
launcher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = launcher
SPEC.loader.exec_module(launcher)

SCHEMA = "asciicker.godot_baseline.v2"
DIGEST = "a" * 64


class FakeReceiptValidationError(RuntimeError):
    """Model the production validator's one expected rejection class."""


class SpawnSentinel:
    """Record spawn calls so tests can prove validation happens first."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        assert self.events == ["validated"]
        self.events.append("spawned")
        self.calls.append((cmd, kwargs))
        return SimpleNamespace(pid=43210)


def valid_validator(events: list[str]) -> SimpleNamespace:
    """Return a validator double with the production method and error names."""

    def validate_receipt(path: Path) -> dict[str, Any]:
        assert path.is_absolute()
        events.append("validated")
        return {
            "schema": SCHEMA,
            "self_hash": {"digest": DIGEST},
        }

    return SimpleNamespace(
        ReceiptValidationError=FakeReceiptValidationError,
        validate_receipt=validate_receipt,
    )


def run_args(out_dir: Path, forwarded: list[str], *, dry_run: bool = True) -> list[str]:
    """Build one launcher argv using the real remainder separator."""

    args = ["--out", str(out_dir)]
    if dry_run:
        args.append("--dry-run-launch")
    return [*args, "--", *forwarded]


@pytest.mark.parametrize(
    "forwarded",
    [
        ["--godot-timeout", "9"],
        ["--baseline-receipt"],
        ["--baseline-receipt", "--godot-timeout", "9"],
        ["--baseline-receipt=baseline.json"],
        [
            "--baseline-receipt",
            "one.json",
            "--baseline-receipt",
            "two.json",
        ],
    ],
)
def test_missing_malformed_and_duplicate_receipt_args_fail_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    forwarded: list[str],
) -> None:
    """Argument-shape damage cannot create output state or reach the runner."""

    out_dir = tmp_path / "not-created"

    def forbidden_spawn(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Popen must not run for a rejected receipt argument")

    monkeypatch.setattr(launcher.subprocess, "Popen", forbidden_spawn)
    rc = launcher.main(run_args(out_dir, forwarded))

    assert rc == 2
    assert not out_dir.exists()


def test_damaged_json_is_rejected_by_the_real_v2_validator_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Malformed receipt bytes exercise the production v2 validator itself."""

    damaged = tmp_path / "damaged-v2.json"
    damaged.write_text('{"schema":"asciicker.godot_baseline.v2"', encoding="utf-8")
    out_dir = tmp_path / "not-created"

    def forbidden_spawn(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Popen must not run after validator rejection")

    monkeypatch.setattr(launcher.subprocess, "Popen", forbidden_spawn)
    rc = launcher.main(
        run_args(out_dir, ["--baseline-receipt", str(damaged)])
    )

    assert rc == 2
    assert not out_dir.exists()


def test_validator_rejection_leaves_no_plausible_launch_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A structurally plausible pathname still fails closed on validator error."""

    candidate = tmp_path / "candidate.json"
    candidate.write_text("{}\n", encoding="utf-8")
    out_dir = tmp_path / "not-created"
    events: list[str] = []

    def reject(_path: Path) -> dict[str, Any]:
        events.append("rejected")
        raise FakeReceiptValidationError("self hash mismatch")

    fake_validator = SimpleNamespace(
        ReceiptValidationError=FakeReceiptValidationError,
        validate_receipt=reject,
    )
    monkeypatch.setattr(
        launcher,
        "_load_baseline_v2_validator",
        lambda: fake_validator,
    )

    def forbidden_spawn(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Popen must not run after validator rejection")

    monkeypatch.setattr(launcher.subprocess, "Popen", forbidden_spawn)
    rc = launcher.main(
        run_args(out_dir, ["--baseline-receipt", str(candidate)])
    )

    assert rc == 2
    assert events == ["rejected"]
    assert not out_dir.exists()


def test_dry_run_records_the_exact_validated_receipt_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dry-run receipts expose the exact v2 path, schema, and closed self-hash."""

    candidate = tmp_path / "candidate.json"
    candidate.write_text("{}\n", encoding="utf-8")
    out_dir = tmp_path / "package"
    events: list[str] = []
    monkeypatch.setattr(
        launcher,
        "_load_baseline_v2_validator",
        lambda: valid_validator(events),
    )

    def forbidden_spawn(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Popen must not run during a dry launch")

    monkeypatch.setattr(launcher.subprocess, "Popen", forbidden_spawn)
    rc = launcher.main(
        run_args(
            out_dir,
            [
                "--godot-timeout",
                "9",
                "--baseline-receipt",
                str(candidate),
            ],
        )
    )

    assert rc == 0
    assert events == ["validated"]
    receipt = json.loads(
        (out_dir / "detached_launch_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["baseline_receipt"] == {
        "path": str(candidate.resolve()),
        "schema": SCHEMA,
        "self_hash": DIGEST,
    }
    assert receipt["cmd"].count("--baseline-receipt") == 1
    argument_index = receipt["cmd"].index("--baseline-receipt")
    assert receipt["cmd"][argument_index + 1] == str(candidate.resolve())
    assert receipt["status"] == "dry_run_no_launch"


def test_live_launch_validates_before_exactly_one_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The package process becomes reachable only after v2 validation succeeds."""

    candidate = tmp_path / "candidate.json"
    candidate.write_text("{}\n", encoding="utf-8")
    out_dir = tmp_path / "package"
    events: list[str] = []
    monkeypatch.setattr(
        launcher,
        "_load_baseline_v2_validator",
        lambda: valid_validator(events),
    )
    spawn = SpawnSentinel(events)
    monkeypatch.setattr(launcher.subprocess, "Popen", spawn)

    rc = launcher.main(
        run_args(
            out_dir,
            ["--baseline-receipt", str(candidate)],
            dry_run=False,
        )
    )

    assert rc == 0
    assert events == ["validated", "spawned"]
    assert len(spawn.calls) == 1
    command, kwargs = spawn.calls[0]
    assert command.count("--baseline-receipt") == 1
    assert kwargs["cwd"] == str(ROOT)
    receipt = json.loads(
        (out_dir / "detached_launch_receipt.json").read_text(encoding="utf-8")
    )
    assert receipt["status"] == "launched"
    assert receipt["pid"] == 43210
    assert receipt["baseline_receipt"]["self_hash"] == DIGEST


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
