#!/usr/bin/env python3
"""PreToolUse hook — warning-only guard for wrong-surface search patterns."""
from __future__ import annotations

import sys
from pathlib import Path

_MAINTAINER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_MAINTAINER_DIR.parent.parent))


def main() -> None:
    try:
        from scripts.maintainer.search_pattern_guard import main as guard_main
    except ImportError:
        sys.path.insert(0, str(_MAINTAINER_DIR))
        from search_pattern_guard import main as guard_main
    sys.argv = [sys.argv[0], "hook"]
    raise SystemExit(guard_main())


if __name__ == "__main__":
    main()
