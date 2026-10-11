#!/usr/bin/env python3
"""Canonical current runtime promotion.

Single front-door for promoting the verified candidate runtime to the current
release VM. This mirrors the role of reset_candidate_runtime.py, but the
release path is promotion-based rather than rebuilding from the local tree.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
from promote_candidate_to_current import build_parser as build_promote_parser  # noqa: E402


def run_step(label: str, cmd: list[str], cwd: Path) -> int:
    print(f"\n--- {label} ---")
    print("  " + " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(cwd))
    if result.returncode != 0:
        print(f"  FAILED: exit {result.returncode}")
    return result.returncode


def build_parser():
    parser = build_promote_parser()
    parser.description = "Canonical current runtime promotion"
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    print("=" * 72)
    print("CURRENT RUNTIME PROMOTION")
    print(f"  source-host:       {args.source_host}")
    print(f"  source-slot:       {args.source_slot} ({args.source_machine_role})")
    print(f"  dest-host:         {args.dest_host}")
    print(f"  dest-slot:         {args.dest_slot} ({args.dest_machine_role})")
    print(f"  current-hostname:  {args.current_hostname}")
    print(f"  current-link:      {args.current_link}")
    print("=" * 72)

    rc = run_step(
        "Promote verified candidate to current",
        [sys.executable, str(SCRIPTS_DIR / "promote_candidate_to_current.py"), *argv] if argv else
        [sys.executable, str(SCRIPTS_DIR / "promote_candidate_to_current.py")],
        SCRIPTS_DIR.parent,
    )
    if rc != 0:
        return rc

    print("\nPROMOTION COMPLETE — current is serving the promoted release slot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
