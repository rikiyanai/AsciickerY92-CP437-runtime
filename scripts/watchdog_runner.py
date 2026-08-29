#!/usr/bin/env python3
"""Phase 5 watchdog front door — single canonical entry point.

Replaces the deleted watchdog_run_canonical.py monolith (14,903 LOC, last at
4f4579e7f^). This file is the single entry point for all watchdog proof runs.

Current state: Phase 5 stub. The scripts/watchdog/ package modules
(source/, recipe_store.py) are extracted and importable, but the full
orchestration surface (PhaseMachine, CLI runner, deploy integration) has not
yet been implemented. All invocations will exit with a clear Phase 5 status
message rather than crashing on import.

Phase 5 design targets (from scripts/watchdog/__init__.py module map):
  cli.py           — argparse, profiles, wizard, RunRequest.from_args()
  phase_machine.py — PhaseMachine with explicit transition table
  receipts.py      — receipt writing, FL stubs
  deploy/          — CandidateDeployPlan, Remote, web, server, manifest, lock

Usage (pending Phase 5 completion):
    python3 scripts/watchdog_runner.py --target candidate --mode full
    python3 scripts/watchdog_runner.py --help

See: docs/plans/2026-03-22-multiplayer-canonical-spec.md §Law 9 (watchdog is
outside the game binary) and §Law 8 (reset means canonical runtime state).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="watchdog_runner.py",
        description="Canonical watchdog proof run front door (Phase 5)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # Core run flags — match historical watchdog_run_canonical.py interface
    p.add_argument("--target", choices=["candidate", "current"],
                   help="VPS slot to run against")
    p.add_argument("--mode", default="watchdog-only",
                   choices=["full", "watchdog-only", "local", "current-smoke"],
                   help="Run mode (default: watchdog-only)")
    p.add_argument("--run-label", default="passive",
                   help="Run provenance label written to receipt (default: passive)")
    p.add_argument("--ssh-target",
                   help="SSH target for VPS (e.g. r@35.226.113.14)")
    p.add_argument("--base-url",
                   help="Public base URL for the slot")
    p.add_argument("--ws-server",
                   help="WebSocket server address")
    # Controller flags
    p.add_argument("--controller-mode", choices=["manual", "recipe", "off"],
                   help="Input controller mode (default: off)")
    p.add_argument("--controller-recipe",
                   help="Name of recipe from watchdog/recipe_store.py to replay")
    # Repeat flags
    p.add_argument("--repeat-exact-run", metavar="RUN_ID",
                   help="Repeat a specific run by ID")
    p.add_argument("--repeat-exact-last", action="store_true",
                   help="Repeat the most recent run")
    p.add_argument("--followup-repeat-with-derived-recipe", action="store_true",
                   help="After manual run, auto-launch derived recipe repeat")
    # Deploy flags
    p.add_argument("--commits",
                   help="Comma-separated commit list for multi-commit deploys")
    p.add_argument("--tmp-clone-source-policy",
                   choices=["clean-clone", "local-worktree"],
                   help="Source policy for tmp-clone deploys")
    # Diff / corpus flags
    p.add_argument("--intent-diff-corpus",
                   choices=["gameplay", "watchdog", "launcher", "all"],
                   help="Diff corpus for intent comparison")
    p.add_argument("--intent-diff-mode",
                   choices=["latest_relevant", "between_runs"],
                   help="Diff mode for intent comparison")
    # Reset / deploy flags
    p.add_argument("--commit-all-and-reset", action="store_true",
                   help="Commit all changes, reset to canonical state, then run")
    p.add_argument("--proof-profile",
                   help="Proof profile selected by launcher proof builder")
    p.add_argument("--auto-fl", action="store_true",
                   help="Allow runner to append FL intent metadata")
    p.add_argument("--observation",
                   help="Operator observation text for run receipt")
    p.add_argument("--intent",
                   help="Structured run intent text")
    p.add_argument("--intent-introduced-by", action="append", default=[],
                   help="Commit or ref suspected to have introduced the issue")
    p.add_argument("--intent-fix-attempt", action="append", default=[],
                   help="Commit or ref that attempts to fix the issue")
    p.add_argument("--intent-baseline-run", action="append", default=[],
                   help="Baseline run id used for comparison")
    p.add_argument("--intent-required-fields", action="append", default=[],
                   help="Required recorder field for proof")
    # Shortcut flags
    p.add_argument("--auto", action="store_true",
                   help="Launch with automatic defaults (non-interactive)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the resolved run plan without executing")
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.dry_run:
        print("watchdog_runner.py [dry-run]")
        print(f"  target:          {args.target or '(not set)'}")
        print(f"  mode:            {args.mode}")
        print(f"  run-label:       {args.run_label}")
        print(f"  ssh-target:      {args.ssh_target or '(not set)'}")
        print(f"  controller-mode: {args.controller_mode or 'off'}")
        print()
        print("[Phase 5 not yet implemented]")
        print("Dry-run complete — no run was launched.")
        return 0

    print("=" * 70)
    print("watchdog_runner.py — Phase 5 front door")
    print("=" * 70)
    print()
    print("STATUS: Phase 5 orchestration not yet implemented.")
    print()
    print("The scripts/watchdog/ package modules are extracted:")
    print("  scripts/watchdog/source/         — SourceIdentity, dirty_tree,")
    print("                                     restoreability_contract, tmp_clone")
    print("  scripts/watchdog/recipe_store.py — recipe list/show/capture")
    print()
    print("Pending Phase 5 modules (not yet written):")
    print("  scripts/watchdog/cli.py          — argparse + RunRequest")
    print("  scripts/watchdog/phase_machine.py — PhaseMachine + transitions")
    print("  scripts/watchdog/receipts.py     — receipt writing")
    print("  scripts/watchdog/deploy/         — deploy integration")
    print()
    print("Invoked with:")
    print(f"  target:          {args.target or '(not set)'}")
    print(f"  mode:            {args.mode}")
    print(f"  run-label:       {args.run_label}")
    print(f"  ssh-target:      {args.ssh_target or '(not set)'}")
    print(f"  controller-mode: {args.controller_mode or 'off'}")
    print()
    print("Exit 1 — no run launched. Implement Phase 5 modules to enable runs.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
