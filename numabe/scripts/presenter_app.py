#!/usr/bin/env python3
"""FL4512 presenter launcher (NOT the ASCIIID Classic editor).

This wraps the current-source `.run/asciicker-presenter` binary, which is the
ASCIID FL4512 native default presenter. The same binary serves the HTML
authoring alternate backend via `--authoring-port`. The wrapper forwards
arguments and environment verbatim and only prepares or launches that
presenter through a minimal macOS app envelope.

ASCIIID Classic keeps the Classic name and separate binary. The presenter must
not take the plain legacy binary path, app bundle, or bundle id.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUN_DIR = PROJECT_ROOT / ".run"
PRESENTER_BIN = RUN_DIR / "asciicker-presenter"
PRESENTER_APP = RUN_DIR / "ASCIID FL4512 Presenter.app"
EDITOR_SOURCE = PROJECT_ROOT / "apps" / "asciicker-presenter" / "asciiid.cpp"

_INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>asciicker-presenter-launcher</string>
  <key>CFBundleIdentifier</key>
  <string>world.riki.asciicker.presenter</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>ASCIID FL4512 Presenter</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>11.0</string>
</dict>
</plist>
"""

_APP_EXEC = """#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname "$0")" && pwd)"
RUN_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/../../../" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "$RUN_DIR/.." && pwd)"
PRESENTER_BIN="${ASCIICKER_PRESENTER_BIN:-$RUN_DIR/asciicker-presenter}"

# The presenter binary parses no --map/.a3d argument, so this envelope
# forwards arguments and environment verbatim without map sniffing.
# Callers that need a run-local mesh root export ASCIICKER_ACTIVE_MESH_ROOT
# explicitly; the C++ mesh resolver consumes it from the environment.
cd "$PROJECT_ROOT"
exec "$PRESENTER_BIN" "$@"
"""


def _write_if_changed(path: Path, content: str) -> None:
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")


def current_native_entrypoint_state() -> str:
    """Describe the current C++ default-entrypoint state without launching it."""
    if not EDITOR_SOURCE.is_file():
        return "unknown_editor_source_missing"
    source = EDITOR_SOURCE.read_text(encoding="utf-8", errors="replace")
    if "--authoring-port" in source and "g_asciiid_authoring_http" in source:
        return "native_presenter_and_html_authoring_backend_available"
    return "authoring_backend_not_detected"


def ensure_presenter_app(
    *,
    binary_path: Path | None = None,
    app_path: Path | None = None,
) -> Path:
    """Create or refresh the local presenter app wrapper and return its path."""
    binary_path = (binary_path or PRESENTER_BIN).resolve()
    app_path = app_path or PRESENTER_APP
    if not binary_path.is_file():
        raise FileNotFoundError(f"missing presenter binary: {binary_path}")

    (app_path / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
    plist_path = app_path / "Contents" / "Info.plist"
    exec_path = app_path / "Contents" / "MacOS" / "asciicker-presenter-launcher"

    _write_if_changed(plist_path, _INFO_PLIST)
    _write_if_changed(exec_path, _APP_EXEC)
    exec_path.chmod(exec_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return app_path


def launch_presenter_gui(
    args: list[str] | tuple[str, ...] | None = None,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    wait: bool = True,
    binary_path: Path | None = None,
    app_path: Path | None = None,
):
    """Launch the presenter in GUI mode, preferring a macOS app bundle when available."""
    args = list(args or [])
    cwd = cwd or PROJECT_ROOT
    binary_path = (binary_path or PRESENTER_BIN).resolve()
    # Verbatim forwarding: the binary parses no --map/.a3d argument, so the
    # wrapper never sniffs map inputs and never synthesizes mesh-root state.
    # Callers export ASCIICKER_ACTIVE_MESH_ROOT explicitly when needed.

    if sys.platform == "darwin":
        app_bundle = ensure_presenter_app(binary_path=binary_path, app_path=app_path)
        cmd = ["open", "-n"]
        if wait:
            cmd.append("-W")
        cmd.extend([str(app_bundle), "--args", *args])
        return subprocess.run(cmd, cwd=str(cwd), env=env, check=False)

    if not binary_path.is_file():
        raise FileNotFoundError(f"missing presenter binary: {binary_path}")
    cmd = [str(binary_path), *args]
    if wait:
        return subprocess.run(cmd, cwd=str(cwd), env=env, check=False)
    return subprocess.Popen(cmd, cwd=str(cwd), env=env)


def _planned_command(args: list[str], *, wait: bool, binary_path: Path, app_path: Path) -> list[str]:
    if sys.platform == "darwin":
        cmd = ["open", "-n"]
        if wait:
            cmd.append("-W")
        cmd.extend([str(app_path), "--args", *args])
        return cmd
    return [str(binary_path), *args]


def dry_run_payload(args: list[str], *, wait: bool, binary_path: Path, app_path: Path) -> dict[str, Any]:
    # No derived environment: args and environment pass through verbatim.
    env: dict[str, str] = {}
    return {
        "schema": "asciicker.presenter_wrapper.dry_run.v1",
        "project_root": str(PROJECT_ROOT),
        "binary_path": str(binary_path),
        "binary_exists": binary_path.is_file(),
        "app_path": str(app_path),
        "platform": sys.platform,
        "wait": wait,
        "argv": args,
        "planned_command": _planned_command(args, wait=wait, binary_path=binary_path, app_path=app_path),
        "environment": env,
        "current_native_entrypoint": current_native_entrypoint_state(),
        "ownership": {
            "wrapper": "scripts/presenter_app.py",
            "launch_target": ".run/asciicker-presenter",
            "runtime_data_contract": "shared/runtime_data/runtime_data_contract.v1.json",
            "a3d_read_versions": [0, 1, 2, 3, 4, 5, 6],
            "a3d_write_version": 6,
            "shared_path_is_shared_session": False,
            "does_not_build": True,
            "does_not_copy_source_tree": True,
            "does_not_select_sprite_profiles": True,
            "does_not_mutate_xp_or_bundle_state": True,
            "html_alternate": "scripts/package_asciicker_presenter_html_mac.py",
            "classic_target": ".run/asciiid-classic",
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Launch the current-source ASCIID FL4512 presenter wrapper around .run/asciicker-presenter. "
            "Use --dry-run for non-headed command/ownership inspection."
        )
    )
    parser.add_argument("--dry-run", action="store_true", help="print planned wrapper action as JSON and exit")
    parser.add_argument("--print-command", action="store_true", help="print shell-quoted planned command and exit")
    parser.add_argument("--no-wait", action="store_true", help="do not wait for the launched process")
    parser.add_argument(
        "--binary",
        default=str(PRESENTER_BIN),
        help="presenter executable path; defaults to .run/asciicker-presenter",
    )
    parser.add_argument(
        "--app",
        default=str(PRESENTER_APP),
        help="macOS app wrapper path; defaults to .run/ASCIID FL4512 Presenter.app",
    )
    parser.add_argument("presenter_args", nargs=argparse.REMAINDER, help="arguments passed after -- to presenter")
    ns = parser.parse_args(argv)

    args = list(ns.presenter_args)
    if args and args[0] == "--":
        args = args[1:]
    wait = not ns.no_wait
    binary_path = Path(ns.binary).resolve()
    app_path = Path(ns.app).resolve()

    if ns.dry_run or ns.print_command:
        payload = dry_run_payload(args, wait=wait, binary_path=binary_path, app_path=app_path)
        if ns.print_command:
            print(" ".join(shlex.quote(part) for part in payload["planned_command"]))
        else:
            print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    try:
        result = launch_presenter_gui(args, wait=wait, binary_path=binary_path, app_path=app_path)
    except FileNotFoundError as exc:
        print(f"presenter_app.py: {exc}", file=sys.stderr)
        return 2
    if isinstance(result, subprocess.Popen):
        return 0
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
