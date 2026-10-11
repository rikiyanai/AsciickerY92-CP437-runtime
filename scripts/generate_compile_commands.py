#!/usr/bin/env python3
"""Generate compile_commands.json from a Make dry run.

This stays anchored to the real makefile expansion instead of duplicating
compile flags in another maintained surface.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


COMPILER_NAMES = {
    "cc",
    "c++",
    "gcc",
    "g++",
    "clang",
    "clang++",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate compile_commands.json from `make -B -n` output."
    )
    parser.add_argument(
        "--makefile",
        default="makefile_game_mac",
        help="Makefile to inspect. Default: %(default)s",
    )
    parser.add_argument(
        "--target",
        default=".run/game",
        help="Make target to dry-run. Default: %(default)s",
    )
    parser.add_argument(
        "--output",
        default="compile_commands.json",
        help="Output path. Default: %(default)s",
    )
    parser.add_argument(
        "--directory",
        default=".",
        help="Working directory for make and compile DB paths. Default: %(default)s",
    )
    return parser.parse_args()


def looks_like_compile_command(tokens: list[str]) -> bool:
    if not tokens:
        return False
    compiler = os.path.basename(tokens[0])
    return compiler in COMPILER_NAMES and "-c" in tokens and "-o" in tokens


def find_source_file(tokens: list[str], root: Path) -> Path | None:
    for token in reversed(tokens):
        if token.startswith("-"):
            continue
        candidate = root / token
        if candidate.suffix.lower() in {".c", ".cc", ".cpp", ".cxx"}:
            return candidate.resolve()
    return None


def find_output_file(tokens: list[str], root: Path) -> str | None:
    try:
        idx = tokens.index("-o")
    except ValueError:
        return None
    if idx + 1 >= len(tokens):
        return None
    return str((root / tokens[idx + 1]).resolve())


def collect_commands(root: Path, makefile: str, target: str) -> list[dict[str, object]]:
    cmd = ["make", "-B", "-f", makefile, "-n", target]
    proc = subprocess.run(
        cmd,
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    entries: dict[str, dict[str, object]] = {}
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue
        if not looks_like_compile_command(tokens):
            continue
        source = find_source_file(tokens, root)
        if source is None:
            continue
        entry: dict[str, object] = {
            "directory": str(root.resolve()),
            "file": str(source),
            "arguments": tokens,
        }
        output = find_output_file(tokens, root)
        if output is not None:
            entry["output"] = output
        entries[str(source)] = entry
    return [entries[key] for key in sorted(entries)]


def main() -> int:
    args = parse_args()
    root = Path(args.directory).resolve()
    entries = collect_commands(root, args.makefile, args.target)
    if not entries:
        print(
            f"No compile commands found via makefile={args.makefile} target={args.target}",
            file=sys.stderr,
        )
        return 1
    out_path = (root / args.output).resolve()
    out_path.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(entries)} compile commands to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
