#!/usr/bin/env python3
"""Launch the FL-4359 acceptance package detached and write a launch receipt."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_fl4359_acceptance_package.py"


def rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch FL-4359 acceptance package detached")
    parser.add_argument("--out", default=".run/fl4359_acceptance_package/detached-latest")
    parser.add_argument("--port", type=int, default=2610)
    parser.add_argument("--dry-run-launch", action="store_true")
    parser.add_argument("runner_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = (ROOT / out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    extra_args = list(args.runner_args)
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]

    cmd = [
        sys.executable,
        str(RUNNER),
        "--capture",
        "--port",
        str(args.port),
        "--out",
        str(out_dir),
        *extra_args,
    ]
    launcher_log = out_dir / "launcher.log"
    receipt_path = out_dir / "detached_launch_receipt.json"
    receipt: dict[str, Any] = {
        "status": "dry_run_no_launch" if args.dry_run_launch else "launching",
        "timestamp": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "cmd": cmd,
        "out_dir": rel(out_dir),
        "launcher_log": rel(launcher_log),
        "package_manifest": rel(out_dir / "acceptance_package_manifest.json"),
        "pid": None,
    }
    write_json(receipt_path, receipt)

    if not args.dry_run_launch:
        log = launcher_log.open("ab", buffering=0)
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
        receipt["status"] = "launched"
        receipt["pid"] = proc.pid
        write_json(receipt_path, receipt)

    print(receipt_path)
    if receipt["pid"] is not None:
        print(receipt["pid"])
    print(receipt["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
