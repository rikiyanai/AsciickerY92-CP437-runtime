#!/usr/bin/env python3
"""Remove `static` keyword from C++ function definitions in a file.

Origin: proposal 0b862390 (FL-1146) from claude session bc3e41c4
Generalized: accepts any C++ file and function name pattern.

Usage:
  python3 scripts/adhoc/de_staticize_functions.py engine/game.cpp
  python3 scripts/adhoc/de_staticize_functions.py <path> --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove `static` from C++ function definitions"
    )
    parser.add_argument("path", type=Path, help="Path to .cpp file")
    parser.add_argument("--dry-run", "-n", action="store_true",
                        help="Print changes without writing")
    parser.add_argument("--function", "-f", default=None,
                        help="Only de-staticize specific function (regex)")
    args = parser.parse_args()

    if not args.path.exists():
        print(f"ERROR: {args.path} not found", file=sys.stderr)
        return 1

    content = args.path.read_text()

    # Match: `static <return_type> <name>(`
    pattern = re.compile(r'^static\s+(.+\s+\w+\s*\()', re.MULTILINE)
    if args.function:
        fn_re = re.compile(args.function)
        pattern = re.compile(
            rf'^static\s+(.+\s+({args.function})\s*\()', re.MULTILINE
        )

    changes = []
    def replacer(m):
        changes.append(m.group(0)[:80])
        return m.group(1)

    new_content = pattern.sub(replacer, content)
    count = len(changes)

    if count == 0:
        print(f"No `static` function definitions found in {args.path}")
        return 0

    print(f"Found {count} `static` function(s) to de-staticize in {args.path}:")
    for c in changes:
        print(f"  {c}...")

    if args.dry_run:
        print("\nDry-run: no changes written.")
        return 0

    args.path.write_text(new_content)
    print(f"\nWritten {count} change(s) to {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
