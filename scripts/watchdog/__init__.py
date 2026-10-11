"""Watchdog Python module tree — extracted typed module system.

This package exists alongside the existing JS modules (lib/, metrics/, tests/)
and the auto-generated ``constants.py``.  The Python modules follow the plan at
``docs/plans/2026-05-10-001-refactor-watchdog-module-extraction-plan.md``.

Module map:
  errors.py       — TypedRecoveryResult, CommandResult, PhaseResult, error rendering
  run_state.py    — RunContext, RunState (placeholder for PhaseMachine in Phase 5)
  cli.py          — argparse, profiles, wizard, RunRequest.from_args() (Phase 5)
  receipts.py     — receipt writing, FL stubs (Phase 5)
  phase_machine.py — PhaseMachine with explicit transition table (Phase 5)
  source/         — SourceIdentity, dirty_tree, restoreability_contract, tmp_clone (Phase 2–3)
  deploy/         — CandidateDeployPlan, Remote, web, server, manifest, lock (Phase 4)

Usage (after Phase 5):
  python3 scripts/watchdog_run_canonical.py --target candidate --mode full
"""

__all__: list[str] = []
