#!/usr/bin/env python3
"""Fail closed when render-loop diagnostic costs escape debug isolation."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


FORBIDDEN = (
    ".readPixels(",
    ".getError(",
    "SampleAnsiBufferBytes(",
    "[POST-DRAW]",
    "[WebGL DEBUG]",
    "[DIAG] frame=",
    "[AK_AUDIO]",
)


def _strip_line_comment(line: str) -> str:
    if "://" in line:
        # Avoid breaking URL-ish strings; this script only needs source-shape
        # checks, so preserving a little too much text is safer than too little.
        return line
    return line.split("//", 1)[0]


def check_file(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if "var AK_DEBUG_ISOLATION = ResolveDebugIsolationEnabled();" not in "\n".join(lines):
        return [f"{path}: missing AK_DEBUG_ISOLATION switch"]

    failures: list[str] = []
    block_stack: list[str] = []

    for lineno, raw in enumerate(lines, start=1):
        line = _strip_line_comment(raw)

        closers = line.count("}")
        for _ in range(min(closers, len(block_stack))):
            block_stack.pop()

        opens = line.count("{")
        condition = ""
        match = re.search(r"\bif\s*\((.*)\)\s*\{", line)
        if match:
            condition = match.group(1)

        active_debug_isolation = any("AK_DEBUG_ISOLATION" in block for block in block_stack)
        inline_debug_isolation = "AK_DEBUG_ISOLATION" in line

        for token in FORBIDDEN:
            if token not in line:
                continue
            if token == "SampleAnsiBufferBytes(" and re.search(r"\bfunction\s+SampleAnsiBufferBytes\b", line):
                continue
            if active_debug_isolation or inline_debug_isolation:
                continue
            failures.append(f"{path}:{lineno}: {token} is not gated by AK_DEBUG_ISOLATION")

        if opens:
            for _ in range(opens):
                block_stack.append(condition)
                condition = ""

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check web render diagnostics are opt-in behind debug isolation."
    )
    parser.add_argument("path", nargs="?", default="web/game_web.html")
    args = parser.parse_args()

    failures = check_file(Path(args.path))
    if failures:
        print("web diagnostic isolation check failed:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("web diagnostic isolation check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
