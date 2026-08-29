#!/usr/bin/env python3
"""Inventory helper for external ASCII research repositories."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path(os.environ.get("ASCII_RESEARCH_ROOT", str(Path.home() / "Projects" / "ascii research")))
# `alexharri-ascii` is a local clone/copy of notes from
# Alex Harri Jonsson, "ASCII characters are not pixels: a deep dive into ASCII rendering"
# (2026-01-17). Primary external source: https://alexharri.com/blog/ascii-rendering
DEFAULT_TARGETS = [
    "alexharri-ascii",
    "durdraw",
    "gradscii-art",
    "html_ascii-renders",
    "Mage-core",
    "png2rex_rs",
    "ruri Ascii filter tool ",
]
BUILD_MARKERS = [
    "Cargo.toml",
    "package.json",
    "requirements.txt",
    "pyproject.toml",
    "Makefile",
    "CMakeLists.txt",
    "go.mod",
]


def find_git_root(path: Path) -> Path | None:
    if (path / ".git").exists():
        return path
    # Common case in this corpus: wrapper folder with one nested repo.
    for child in sorted(path.iterdir() if path.exists() else []):
        if child.is_dir() and (child / ".git").exists():
            return child
    return None


def scan_repo(path: Path) -> dict[str, Any]:
    exists = path.exists()
    result: dict[str, Any] = {
        "name": path.name,
        "path": str(path),
        "exists": exists,
        "is_dir": path.is_dir(),
        "files": 0,
        "git_root": None,
        "readmes": [],
        "build_markers": [],
        "top_exts": [],
    }
    if not exists or not path.is_dir():
        return result

    git_root = find_git_root(path)
    result["git_root"] = str(git_root) if git_root else None

    readmes = sorted(p.name for p in path.glob("README*") if p.is_file())
    result["readmes"] = readmes
    result["build_markers"] = [m for m in BUILD_MARKERS if (path / m).exists()]

    ext_counts: Counter[str] = Counter()
    file_count = 0
    for p in path.rglob("*"):
        if not p.is_file():
            continue
        pstr = p.as_posix()
        if "/.git/" in pstr or "/node_modules/" in pstr or "__pycache__" in pstr:
            continue
        file_count += 1
        ext_counts[p.suffix.lower() or "<noext>"] += 1

    result["files"] = file_count
    result["top_exts"] = [{"ext": ext, "count": count} for ext, count in ext_counts.most_common(8)]
    return result


def as_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# ASCII Research Inventory",
        "",
        "| Repo | Exists | Git Root | Files | Build Markers | Top Extensions |",
        "|---|---|---|---:|---|---|",
    ]
    for r in rows:
        build = ", ".join(r["build_markers"]) if r["build_markers"] else "-"
        exts = ", ".join(f"{x['ext']}:{x['count']}" for x in r["top_exts"]) if r["top_exts"] else "-"
        git_root = r["git_root"] or "-"
        lines.append(
            f"| {r['name']} | {'yes' if r['exists'] else 'no'} | {git_root} | {r['files']} | {build} | {exts} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan external ASCII research repo roots.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="Root directory containing research repos.")
    parser.add_argument(
        "--targets",
        nargs="*",
        default=DEFAULT_TARGETS,
        help="Specific repo directory names under --root.",
    )
    parser.add_argument("--format", choices=["md", "json"], default="md")
    parser.add_argument("--output", help="Optional output path; defaults to stdout.")
    args = parser.parse_args()

    root = Path(args.root)
    rows = [scan_repo(root / name) for name in args.targets]

    if args.format == "json":
        payload = json.dumps({"root": str(root), "repos": rows}, indent=2)
    else:
        payload = as_markdown(rows)

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
