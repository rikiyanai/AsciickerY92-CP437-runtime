#!/usr/bin/env python3
"""Build a GLSL check tree with dirty include files restored from HEAD.

The renderer worktree is shared by several lanes.  A compile result from the
whole dirty tree is not attributable when an unrelated lane has edited a
shared ``.glsl``/``.glslinc`` include.  This helper copies the current
``godot_project`` into an isolated directory, then replaces every selected
dirty include with its committed HEAD blob (or removes an untracked include).
The resulting directory is suitable for ``GLSL_PROJ=<out>/godot_project``.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INCLUDE_SUFFIXES = {".glsl", ".glslinc"}
GLSL_ROOT = "godot_project/render/ascii/"


def git(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, check=False
    )


def _nul_paths(*args: str) -> list[str]:
    result = git(*args)
    if result.returncode:
        raise RuntimeError((result.stderr or b"").decode(errors="replace").strip())
    return [part.decode("utf-8") for part in result.stdout.split(b"\0") if part]


def dirty_paths() -> list[str]:
    tracked = _nul_paths("diff", "--name-only", "-z", "HEAD", "--", GLSL_ROOT)
    untracked = _nul_paths(
        "ls-files", "--others", "--exclude-standard", "-z", "--", GLSL_ROOT
    )
    return list(dict.fromkeys(tracked + untracked))


def is_include(path: str) -> bool:
    return Path(path).suffix.lower() in INCLUDE_SUFFIXES


def restore_head_blob(path: str, destination: Path) -> str:
    result = git("show", f"HEAD:{path}")
    if result.returncode == 0:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(result.stdout)
        return "restored"
    if destination.exists():
        destination.unlink()
    return "removed-untracked"


def isolate(out: Path | None, paths: list[str] | None = None) -> Path:
    target = out.resolve() if out else Path(tempfile.mkdtemp(prefix="fl4512-glsl-head-"))
    if target.exists() and any(target.iterdir()):
        raise SystemExit(f"output directory is not empty: {target}")
    target.mkdir(parents=True, exist_ok=True)
    source = ROOT / "godot_project"
    if not source.is_dir():
        raise SystemExit(f"missing source project: {source}")
    shutil.copytree(source, target / "godot_project", dirs_exist_ok=True)

    selected = paths if paths is not None else dirty_paths()
    changed = []
    for path in selected:
        if not path.startswith(GLSL_ROOT) or not is_include(path):
            continue
        action = restore_head_blob(path, target / path)
        changed.append((action, path))
    print(f"HEAD_ISOLATION root={target}")
    for action, path in changed:
        print(f"HEAD_ISOLATION {action} {path}")
    if not changed:
        print("HEAD_ISOLATION no dirty GLSL includes selected")
    return target


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, help="empty output directory (default: a temporary directory)"
    )
    parser.add_argument(
        "--path", action="append", default=None, help="dirty GLSL path to restore; repeatable"
    )
    args = parser.parse_args(argv)
    isolate(args.out, args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
