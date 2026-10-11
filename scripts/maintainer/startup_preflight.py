#!/usr/bin/env python3
"""Hard startup preflight shared by Claude hooks and local CLI wrappers.

This is intentionally strict: if any check fails, startup is blocked.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .install_hooks import verify_hooks
except ImportError:  # pragma: no cover - direct script execution
    from install_hooks import verify_hooks


def _tail(text: str) -> str:
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    return lines[-1] if lines else "no output"


def _check_defs(repo_root: Path) -> list[dict[str, Any]]:
    py = sys.executable
    return [
        {
            "name": "multiplayer-canon-guard",
            "cmd": [py, "scripts/multiplayer_canon_guard.py", "--repo-root", str(repo_root)],
            "timeout": 20,
            "home_override": False,
        },
        {
            "name": "maintainer-smoke",
            "cmd": [py, "-m", "compileall", "-q", "scripts/maintainer"],
            "timeout": int(os.environ.get("MAINTAINER_STARTUP_TEST_TIMEOUT", "180")),
            "home_override": False,
        },
    ]


def _check_hook_integrity() -> tuple[bool, dict[str, str]]:
    if verify_hooks(verbose=False):
        return True, {
            "name": "hooks-integrity",
            "status": "ok",
            "summary": "hook registrations verified in ~/.claude/settings.json",
        }
    return False, {
        "name": "hooks-integrity",
        "status": "warn",
        "summary": (
            "hook registrations missing or malformed — run "
            "`python3 scripts/maintainer/install_hooks.py --apply` to repair"
        ),
    }


def run_checks(repo_root: Path) -> tuple[bool, list[dict[str, str]]]:
    results: list[dict[str, str]] = []
    ok = True

    _hooks_ok, hooks_result = _check_hook_integrity()
    results.append(hooks_result)
    # Hook mismatch is advisory (warn), not a hard block — auto-repair was
    # removed per FL-1812.  The operator must run install_hooks.py manually.

    for check in _check_defs(repo_root):
        env = os.environ.copy()
        if check["home_override"]:
            hook_home = repo_root / ".maintainer-hook-home"
            hook_home.mkdir(parents=True, exist_ok=True)
            env["HOME"] = str(hook_home)
        try:
            proc = subprocess.run(
                check["cmd"],
                cwd=str(repo_root),
                capture_output=True,
                text=True,
                timeout=int(check["timeout"]),
                env=env,
            )
            passed = proc.returncode == 0
            summary = _tail(proc.stdout if passed else (proc.stderr or proc.stdout))
            results.append(
                {
                    "name": str(check["name"]),
                    "status": "ok" if passed else "fail",
                    "summary": summary,
                }
            )
            if not passed:
                ok = False
        except subprocess.TimeoutExpired:
            ok = False
            results.append(
                {
                    "name": str(check["name"]),
                    "status": "timeout",
                    "summary": f"timed out after {check['timeout']}s",
                }
            )
        except Exception as exc:  # pragma: no cover
            ok = False
            results.append(
                {
                    "name": str(check["name"]),
                    "status": "error",
                    "summary": str(exc),
                }
            )
    return ok, results


def write_receipt(repo_root: Path, mode: str, ok: bool, results: list[dict[str, str]]) -> None:
    out_dir = repo_root / "artifacts" / "maintainer"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo_root),
        "mode": mode,
        "ok": ok,
        "results": results,
    }
    (out_dir / "startup_preflight_receipt.json").write_text(json.dumps(payload, indent=2) + "\n")


def build_error(results: list[dict[str, str]]) -> str:
    detail = "; ".join(f"{r['name']}={r['status']} ({r['summary']})" for r in results)
    return (
        "[startup-preflight] BLOCKED: startup checks failed. "
        f"{detail}. "
        "Required: "
        "`python3 scripts/maintainer/install_hooks.py --apply` ; "
        "`python3 -m compileall -q scripts/maintainer`."
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run strict startup preflight checks.")
    p.add_argument("--repo-root", default=".", help="Repository root to validate.")
    p.add_argument("--mode", default="generic", choices=["generic", "claude", "codex"])
    p.add_argument("--json", action="store_true", help="Emit JSON summary to stdout.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).expanduser().resolve()
    ok, results = run_checks(repo_root)
    write_receipt(repo_root, mode=args.mode, ok=ok, results=results)

    if args.json:
        print(json.dumps({"ok": ok, "results": results}))
    else:
        for r in results:
            print(f"[startup-preflight] {r['name']}: {r['status']} - {r['summary']}")

    if ok:
        return 0

    msg = build_error(results)
    sys.stderr.write(msg + "\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
