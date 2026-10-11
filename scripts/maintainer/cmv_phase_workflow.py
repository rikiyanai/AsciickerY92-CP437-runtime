#!/usr/bin/env python3
"""CMV workflow helper for phase execution reliability.

Provides one-command routines for:
- session start (startup gates + CMV snapshot/branch)
- checkpoints during work
- pre-compact safe trim
- recovery branch from a known snapshot

All command output is logged to maintainer/cmv/.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOG_ROOT = PROJECT_ROOT / "artifacts" / "maintainer" / "cmv"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _slug(text: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", text.strip())
    value = value.strip("-._")
    return value or "x"


class Runner:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")

    def run(
        self,
        cmd: Sequence[str],
        cwd: Path = PROJECT_ROOT,
        check: bool = True,
        echo_output: bool = True,
    ) -> str:
        cmd_text = " ".join(shlex.quote(part) for part in cmd)
        print(f"$ {cmd_text}")
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"$ {cmd_text}\n")
            proc = subprocess.run(
                list(cmd),
                cwd=str(cwd),
                text=True,
                capture_output=True,
            )
            if proc.stdout:
                fh.write(proc.stdout)
            if proc.stderr:
                fh.write(proc.stderr)
            fh.write(f"[exit={proc.returncode}]\n\n")
        if echo_output and proc.stdout:
            print(proc.stdout, end="")
        if echo_output and proc.stderr:
            print(proc.stderr, end="", file=sys.stderr)
        if check and proc.returncode != 0:
            raise RuntimeError(f"command failed ({proc.returncode}): {cmd_text}")
        return proc.stdout


def _phase_names(phase: str, ts: str) -> tuple[str, str]:
    p = _slug(phase)
    snapshot = f"phase-{p}-start-{ts}"
    branch = f"phase-{p}-work-{ts}"
    return snapshot, branch


def _ensure_cmv(runner: Runner) -> None:
    runner.run(["cmv", "--version"])


def _latest_project_session(runner: Runner) -> str:
    raw = runner.run(["cmv", "sessions", "--json"], echo_output=False)
    try:
        sessions = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"failed to parse cmv sessions output: {exc}") from exc
    project = str(PROJECT_ROOT)
    for item in sessions:
        if isinstance(item, dict) and item.get("project") == project and item.get("sessionId"):
            return str(item["sessionId"])
    raise RuntimeError(f"no Claude session found for project: {project}")


def cmd_start(args: argparse.Namespace) -> int:
    ts = _utc_stamp()
    phase_slug = _slug(args.phase)
    log_path = LOG_ROOT / f"{ts}-start-phase-{phase_slug}.log"
    runner = Runner(log_path)
    _ensure_cmv(runner)
    session_id = _latest_project_session(runner)

    snapshot_name, branch_name = _phase_names(args.phase, ts)

    runner.run(["python3", "scripts/conductor_tools.py", "status", "--auto-setup"])
    verify_out = runner.run(
        ["python3", "scripts/maintainer/install_hooks.py", "--verify"],
        check=False,
    )
    if "0/" in verify_out or "[MISSING]" in verify_out:
        runner.run(["python3", "scripts/maintainer/install_hooks.py", "--apply"])
        runner.run(["python3", "scripts/maintainer/install_hooks.py", "--verify"])
    runner.run(["python3", "scripts/maintainer/run_tests.py"])

    desc = f"Phase {args.phase} session start baseline ({ts})"
    tags = f"phase-{phase_slug},start,baseline"
    runner.run(
        [
            "cmv",
            "snapshot",
            snapshot_name,
            "--session",
            session_id,
            "--description",
            desc,
            "--tags",
            tags,
        ]
    )

    branch_cmd = ["cmv", "branch", snapshot_name, "--name", branch_name]
    if args.trim:
        branch_cmd.extend(["--trim", "--threshold", str(args.threshold)])
    if args.skip_launch:
        branch_cmd.append("--skip-launch")
    runner.run(branch_cmd)

    print("")
    print("Phase start baseline created:")
    print(f"- snapshot: {snapshot_name}")
    print(f"- branch:   {branch_name}")
    print(f"- log:      {log_path}")
    print("")
    print("Recommended ongoing routine:")
    print(f"1) checkpoint: python3 scripts/maintainer/cmv_phase_workflow.py checkpoint --phase {args.phase} --label <topic>")
    print(f"2) pre-compact: python3 scripts/maintainer/cmv_phase_workflow.py precompact --phase {args.phase}")
    print(f"3) recover: python3 scripts/maintainer/cmv_phase_workflow.py recover --snapshot {snapshot_name}")
    return 0


def cmd_checkpoint(args: argparse.Namespace) -> int:
    ts = _utc_stamp()
    phase_slug = _slug(args.phase)
    label_slug = _slug(args.label)
    snap_name = f"phase-{phase_slug}-ckpt-{label_slug}-{ts}"
    log_path = LOG_ROOT / f"{ts}-checkpoint-phase-{phase_slug}-{label_slug}.log"
    runner = Runner(log_path)
    _ensure_cmv(runner)
    session_id = _latest_project_session(runner)
    desc = f"Phase {args.phase} checkpoint: {args.label}"
    tags = f"phase-{phase_slug},checkpoint,{label_slug}"
    runner.run(
        [
            "cmv",
            "snapshot",
            snap_name,
            "--session",
            session_id,
            "--description",
            desc,
            "--tags",
            tags,
        ]
    )
    print(f"checkpoint snapshot: {snap_name}")
    print(f"log: {log_path}")
    return 0


def cmd_precompact(args: argparse.Namespace) -> int:
    ts = _utc_stamp()
    phase_slug = _slug(args.phase)
    snap_name = f"phase-{phase_slug}-precompact-{ts}"
    branch_name = f"phase-{phase_slug}-postcompact-{ts}"
    log_path = LOG_ROOT / f"{ts}-precompact-phase-{phase_slug}.log"
    runner = Runner(log_path)
    _ensure_cmv(runner)
    session_id = _latest_project_session(runner)

    runner.run(
        [
            "cmv",
            "trim",
            "--session",
            session_id,
            "--name",
            snap_name,
            "--threshold",
            str(args.threshold),
            "--skip-launch",
        ]
    )
    # cmv trim creates a branch name automatically; create a named one as anchor.
    runner.run(
        [
            "cmv",
            "branch",
            snap_name,
            "--name",
            branch_name,
            "--trim",
            "--threshold",
            str(args.threshold),
            "--skip-launch",
        ]
    )
    print(f"pre-compact snapshot: {snap_name}")
    print(f"trimmed branch: {branch_name}")
    print(f"log: {log_path}")
    return 0


def cmd_recover(args: argparse.Namespace) -> int:
    ts = _utc_stamp()
    phase_slug = _slug(args.phase) if args.phase else "x"
    branch_name = args.name or f"phase-{phase_slug}-recover-{ts}"
    log_path = LOG_ROOT / f"{ts}-recover-{_slug(branch_name)}.log"
    runner = Runner(log_path)
    _ensure_cmv(runner)

    cmd = ["cmv", "branch", args.snapshot, "--name", branch_name]
    if args.trim:
        cmd.extend(["--trim", "--threshold", str(args.threshold)])
    if args.skip_launch:
        cmd.append("--skip-launch")
    runner.run(cmd)
    print(f"recovery branch: {branch_name}")
    print(f"log: {log_path}")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    ts = _utc_stamp()
    log_path = LOG_ROOT / f"{ts}-status.log"
    runner = Runner(log_path)
    _ensure_cmv(runner)
    runner.run(["cmv", "list"])
    runner.run(["cmv", "tree"])
    print(f"log: {log_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CMV phase workflow helper for Phase 13/21 reliability."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="Run startup gates + create phase baseline snapshot/branch")
    p_start.add_argument("--phase", required=True, help='Phase label, e.g. "13.4" or "21"')
    p_start.add_argument("--trim", action="store_true", help="Create the initial branch with trimming enabled")
    p_start.add_argument(
        "--threshold", type=int, default=500, help="Trim threshold chars (used with --trim)"
    )
    p_start.add_argument(
        "--skip-launch",
        action="store_true",
        default=True,
        help="Do not auto-launch Claude when creating branch (default: true)",
    )
    p_start.set_defaults(func=cmd_start)

    p_ckpt = sub.add_parser("checkpoint", help="Create a named checkpoint snapshot")
    p_ckpt.add_argument("--phase", required=True)
    p_ckpt.add_argument("--label", required=True, help='Short checkpoint label, e.g. "fix-b3"')
    p_ckpt.set_defaults(func=cmd_checkpoint)

    p_pc = sub.add_parser("precompact", help="Create safe pre-compact snapshot and trimmed branch")
    p_pc.add_argument("--phase", required=True)
    p_pc.add_argument("--threshold", type=int, default=500)
    p_pc.set_defaults(func=cmd_precompact)

    p_rec = sub.add_parser("recover", help="Recover by branching from an existing snapshot")
    p_rec.add_argument("--snapshot", required=True, help="Snapshot name to recover from")
    p_rec.add_argument("--phase", default="", help="Optional phase label for default branch naming")
    p_rec.add_argument("--name", default="", help="Optional explicit branch name")
    p_rec.add_argument("--trim", action="store_true", help="Apply trim while branching")
    p_rec.add_argument("--threshold", type=int, default=500)
    p_rec.add_argument("--skip-launch", action="store_true", default=True)
    p_rec.set_defaults(func=cmd_recover)

    p_status = sub.add_parser("status", help="Show CMV snapshots/tree and log output")
    p_status.set_defaults(func=cmd_status)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
