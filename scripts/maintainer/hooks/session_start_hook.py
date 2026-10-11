#!/usr/bin/env python3
"""SessionStart hook — run hard startup preflight and block on failure.

Hook interface:
  stdin: JSON context (ignored for SessionStart)
  stdout: JSON result with statusMessage
  exit 0: preflight passed
  exit 2: preflight failed (session must be fixed before work)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_MAINTAINER_DIR = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _MAINTAINER_DIR.parent.parent

def main() -> None:
    cmd = [
        sys.executable,
        str(_MAINTAINER_DIR / "startup_preflight.py"),
        "--repo-root",
        str(_PROJECT_ROOT),
        "--mode",
        "claude",
        "--json",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired:
        msg = "Maintainer startup preflight timed out"
        print(json.dumps({"statusMessage": msg}))
        sys.stderr.write(f"[maintainer:session-start] {msg}\n")
        sys.exit(2)

    if result.returncode == 0:
        status = "Maintainer startup preflight passed"
        print(json.dumps({"statusMessage": status}))
        sys.stderr.write(f"[maintainer:session-start] {status}\n")
        sys.exit(0)

    failure = (result.stderr or result.stdout or "startup preflight failed").strip().splitlines()[-1]
    status = f"Maintainer startup preflight failed: {failure}"
    print(json.dumps({"statusMessage": status}))
    sys.stderr.write(f"[maintainer:session-start] {status}\n")
    sys.exit(2)


if __name__ == "__main__":
    main()
