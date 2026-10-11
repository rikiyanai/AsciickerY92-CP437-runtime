#!/usr/bin/env python3
"""Compare makefile source file list against engine/ directory to find unlisted .cpp files.

Origin: proposal f97b8daa (FL-501) from claude session bc3e41c4
Generalized: accepts makefile path and source directory.

Usage:
  python3 scripts/adhoc/find_unlisted_cpp_files.py makefile_game_mac engine/
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find .cpp files in a source dir not listed in a makefile"
    )
    parser.add_argument("makefile", type=Path, help="Path to makefile")
    parser.add_argument("srcdir", type=Path, help="Path to source directory")
    parser.add_argument("--ext", default=".cpp", help="Source file extension (default: .cpp)")
    args = parser.parse_args()

    if not args.makefile.exists():
        print(f"ERROR: {args.makefile} not found", file=sys.stderr)
        return 1
    if not args.srcdir.is_dir():
        print(f"ERROR: {args.srcdir} is not a directory", file=sys.stderr)
        return 1

    makefile_text = args.makefile.read_text()

    # Find all .cpp references in the makefile (both \ and direct paths)
    makefile_cpps = set()
    for m in re.finditer(r'(\S*\.cpp)', makefile_text):
        base = os.path.basename(m.group(1))
        makefile_cpps.add(base)

    # List actual .cpp files in srcdir
    srcdir_cpps = set()
    for f in args.srcdir.iterdir():
        if f.suffix == args.ext and f.is_file():
            srcdir_cpps.add(f.name)

    unlisted = sorted(srcdir_cpps - makefile_cpps)
    listed_but_missing = sorted(makefile_cpps - srcdir_cpps)

    print(f"=== {args.srcdir}/*{args.ext} vs {args.makefile} ===")
    print(f"\nFiles in {args.srcdir}/ NOT in makefile ({len(unlisted)}):")
    for f in unlisted:
        print(f"  {f}")
    print(f"\nFiles in makefile NOT in {args.srcdir}/ ({len(listed_but_missing)}):")
    for f in listed_but_missing:
        print(f"  {f}  (MISSING)")
    print(f"\nTotal in {args.srcdir}/: {len(srcdir_cpps)}")
    print(f"Total in makefile: {len(makefile_cpps)}")

    return 1 if unlisted else 0


if __name__ == "__main__":
    raise SystemExit(main())
