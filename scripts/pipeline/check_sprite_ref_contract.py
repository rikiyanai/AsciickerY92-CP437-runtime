#!/usr/bin/env python3
"""Check whether engine-loaded sprites share the same frame ref contract.

This is a source-truth helper for composition failures that die inside
`CompositeSpriteFrameOnto()` on `ref[2]` mismatch. It uses the same C++ sprite
loader as runtime through `presentation_overlay_dump.cpp`, so it validates the
loaded contract rather than raw XP bytes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL_SRC = REPO_ROOT / "scripts" / "pipeline" / "presentation_overlay_dump.cpp"
TOOL_BIN = REPO_ROOT / "output" / "presentation_overlay_dump"


def _resolve_dump_binary() -> Path:
    if TOOL_BIN.exists():
        return TOOL_BIN
    compiler = shutil.which("c++")
    if not compiler:
        raise RuntimeError(
            "presentation_overlay_dump is missing and no c++ compiler is available"
        )
    tool_dir = Path(tempfile.mkdtemp(prefix="sprite-ref-contract-"))
    binary = tool_dir / "presentation_overlay_dump"
    compile_result = subprocess.run(
        [
            compiler,
            "-std=c++11",
            "-DNDEBUG",
            "-I",
            str(REPO_ROOT),
            "-I",
            str(REPO_ROOT / "engine"),
            str(TOOL_SRC),
            str(REPO_ROOT / "engine" / "sprite.cpp"),
            "-o",
            str(binary),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if compile_result.returncode != 0:
        raise RuntimeError(compile_result.stdout + compile_result.stderr)
    return binary


def load_sprite_values(sprite_path: Path, dump_binary: Path, field: str) -> list[tuple[int, ...]]:
    raw = subprocess.check_output(
        [str(dump_binary), str(sprite_path)],
        cwd=REPO_ROOT,
        text=True,
    )
    payload = json.loads(raw)
    return sorted({tuple(frame[field]) for frame in payload["frames"]})


def compare_sprite_refs(
    sprite_paths: list[Path],
    *,
    field: str = "ref",
) -> tuple[bool, dict[str, list[tuple[int, ...]]]]:
    dump_binary = _resolve_dump_binary()
    refs_by_path: dict[str, list[tuple[int, ...]]] = {}
    for sprite_path in sprite_paths:
        refs_by_path[str(sprite_path)] = load_sprite_values(sprite_path, dump_binary, field)
    ref_sets = {tuple(refs) for refs in refs_by_path.values()}
    return len(ref_sets) <= 1, refs_by_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare engine-loaded sprite frame refs across multiple XP files."
    )
    parser.add_argument("sprites", nargs="+", help="XP sprite paths to compare")
    parser.add_argument("--meta", action="store_true", help="Compare frame meta_xy values instead of refs")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    sprite_paths = [Path(p) for p in args.sprites]
    missing = [str(path) for path in sprite_paths if not path.exists()]
    if missing:
        parser.error("missing sprite(s): " + ", ".join(missing))

    try:
        field = "meta" if args.meta else "ref"
        matches, refs_by_path = compare_sprite_refs(sprite_paths, field=field)
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
            return 2
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"ok": matches, "field": field, "refs_by_path": refs_by_path}, indent=2))
        return 0 if matches else 1

    for sprite, refs in refs_by_path.items():
        print(f"{sprite}: {refs}")
    if matches:
        print(f"{field.upper()} CONTRACT OK: all sprites share the same loaded frame values")
        return 0
    print(f"{field.upper()} CONTRACT MISMATCH: loaded frame values differ across sprites", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
