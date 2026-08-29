#!/usr/bin/env python3
"""Install strict startup-gate wrappers for both Codex and Claude CLIs.

This enforces startup preflight before either CLI starts.
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LOCAL_BIN = Path.home() / ".local" / "bin"
ZSHRC = Path.home() / ".zshrc"
ZPROFILE = Path.home() / ".zprofile"
ZSHENV = Path.home() / ".zshenv"
PATH_BLOCK_START = "# >>> asciicker-startup-gate >>>"
PATH_BLOCK_END = "# <<< asciicker-startup-gate <<<"


def _filtered_path() -> str:
    parts = [p for p in os.environ.get("PATH", "").split(":") if p and p != str(LOCAL_BIN)]
    return ":".join(parts)


def _resolve_real_binary(name: str) -> str:
    found = shutil.which(name, path=_filtered_path())
    if not found:
        raise RuntimeError(f"Could not resolve real `{name}` binary outside {LOCAL_BIN}")
    return found


def _wrapper_text(tool: str, real_bin: str) -> str:
    mode = "codex" if tool == "codex" else "claude"
    return f"""#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${{ASCIICKER_STARTUP_REPO:-{REPO_ROOT}}}"
if [[ -z "${{ASCIICKER_SKIP_STARTUP_GATE:-}}" && -f "$REPO_ROOT/scripts/maintainer/startup_preflight.py" ]]; then
  python3 "$REPO_ROOT/scripts/maintainer/startup_preflight.py" --repo-root "$REPO_ROOT" --mode {mode}
fi

exec "{real_bin}" "$@"
"""


def _ensure_path_priority_for(path_file: Path) -> None:
    block = (
        f"{PATH_BLOCK_START}\n"
        'export PATH="$HOME/.local/bin:$PATH"\n'
        f"{PATH_BLOCK_END}\n"
    )
    if path_file.exists():
        content = path_file.read_text()
    else:
        content = ""
    if PATH_BLOCK_START in content and PATH_BLOCK_END in content:
        pre, tail = content.split(PATH_BLOCK_START, 1)
        _, post = tail.split(PATH_BLOCK_END, 1)
        new_content = pre.rstrip() + "\n\n" + block + post.lstrip()
    else:
        new_content = content.rstrip() + ("\n\n" if content.strip() else "") + block
    path_file.write_text(new_content)


def apply() -> int:
    LOCAL_BIN.mkdir(parents=True, exist_ok=True)
    for tool in ("codex", "claude"):
        real_bin = _resolve_real_binary(tool)
        wrapper = LOCAL_BIN / tool
        wrapper.write_text(_wrapper_text(tool, real_bin))
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"[installed] {wrapper} -> {real_bin}")
    for rc in (ZSHRC, ZPROFILE, ZSHENV):
        _ensure_path_priority_for(rc)
        print(f"[updated] {rc} (PATH priority block)")
    print("Restart shell (or `source ~/.zshrc`) so wrappers take effect.")
    return verify()


def verify() -> int:
    ok = True
    for tool in ("codex", "claude"):
        wrapper = LOCAL_BIN / tool
        if not wrapper.exists():
            print(f"[MISSING] {wrapper}")
            ok = False
            continue
        text = wrapper.read_text()
        if "startup_preflight.py" not in text:
            print(f"[INVALID] {wrapper} missing startup preflight call")
            ok = False
        elif not os.access(wrapper, os.X_OK):
            print(f"[INVALID] {wrapper} not executable")
            ok = False
        else:
            print(f"[OK] {wrapper}")
    for rc in (ZSHRC, ZPROFILE, ZSHENV):
        rc_text = rc.read_text() if rc.exists() else ""
        if PATH_BLOCK_START not in rc_text or PATH_BLOCK_END not in rc_text:
            print(f"[MISSING] PATH priority block in {rc}")
            ok = False
        else:
            print(f"[OK] PATH priority block in {rc}")

    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Install/verify CLI startup wrappers for codex + claude.")
    p.add_argument("--apply", action="store_true", help="Install wrappers and update PATH block.")
    p.add_argument("--verify", action="store_true", help="Verify wrappers and PATH block.")
    args = p.parse_args()
    if args.apply:
        return apply()
    if args.verify:
        return verify()
    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
