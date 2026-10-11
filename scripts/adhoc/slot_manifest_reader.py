#!/usr/bin/env python3
"""Read and print a slot manifest JSON file.

Origin: proposal de7646f0 from codex session
  rollout-2026-04-02T14-47-15-019d4f85-635a-7da3-b53e-e0ecf95f17b9

Usage:
  python3 scripts/adhoc/slot_manifest_reader.py [path]
  (default: .web/slot_manifest.json)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_PATH = ".web/slot_manifest.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Read and print a slot manifest JSON file")
    parser.add_argument("path", type=Path, nargs="?", default=Path(DEFAULT_PATH),
                        help=f"Path to manifest JSON (default: {DEFAULT_PATH})")
    args = parser.parse_args()

    if not args.path.exists():
        print(f"ERROR: {args.path} not found", file=sys.stderr)
        return 1

    try:
        data = json.loads(args.path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"ERROR: failed to parse {args.path}: {e}", file=sys.stderr)
        return 1

    print(json.dumps(data, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
