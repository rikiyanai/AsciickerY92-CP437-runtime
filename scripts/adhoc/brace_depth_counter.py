#!/usr/bin/env python3
"""Count C++ brace depth through a file to find where a function body closes.

Origin: proposal 2277c56e (FL-164) from claude session bc3e41c4
Generalized: accepts any C++ file and optional start line.

Usage:
  python3 scripts/adhoc/brace_depth_counter.py engine/game_input.cpp
  python3 scripts/adhoc/brace_depth_counter.py engine/game_input.cpp --start 1104 --end 1800
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Count C++ brace depth through a file")
    parser.add_argument("path", type=Path, help="Path to .cpp or .h file")
    parser.add_argument("--start", type=int, default=1, help="Start line (1-indexed)")
    parser.add_argument("--end", type=int, default=0, help="End line (0 = end of file)")
    parser.add_argument("--show-depth", action="store_true", help="Print depth per line")
    args = parser.parse_args()

    if not args.path.exists():
        print(f"ERROR: {args.path} not found", file=sys.stderr)
        return 1

    lines = args.path.read_text().splitlines()
    if args.end == 0 or args.end > len(lines):
        args.end = len(lines)

    depth = 0
    in_string = False
    in_char = False
    in_line_comment = False
    in_block_comment = False

    for i, line in enumerate(lines[args.start - 1:args.end], start=args.start):
        orig_depth = depth
        j = 0
        stripped = ""
        while j < len(line):
            c = line[j]
            if in_line_comment:
                break
            if in_block_comment:
                if c == "*" and j + 1 < len(line) and line[j + 1] == "/":
                    in_block_comment = False
                    j += 2
                else:
                    j += 1
                continue
            if c == "/" and j + 1 < len(line):
                if line[j + 1] == "/":
                    break
                if line[j + 1] == "*":
                    in_block_comment = True
                    j += 2
                    continue
            if c == '"' and not in_char:
                in_string = not in_string
            elif c == "'" and not in_string:
                in_char = not in_char
            elif not in_string and not in_char:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
            stripped += c
            j += 1

        if args.show_depth:
            marker = " " * orig_depth + ">" if orig_depth != depth else ""
            print(f"{i:5d} d={orig_depth:3d}{marker}  {line.rstrip()}")

    print(f"\nFinal depth: {depth}")
    print(f"Max depth during scan: {depth}")  # simplified
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
