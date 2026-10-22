#!/usr/bin/env python3
"""Launch the standalone Godot map viewer/editor without borrowing TrueFF gates.

This is the headed front door for FL-4839.  It deliberately launches only
Y8IsoViewer.tscn, runs that script's parse/type check first, and makes no
claim about gameplay rendering, TrueFF acceptance, or Steam readiness.  The
editor opens in a resizable window and Escape returns to the main menu instead
of terminating the desktop application.
"""
from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "godot_project"
VIEWER_SCENE = "res://scenes/Y8IsoViewer.tscn"
VIEWER_SCRIPT = "res://scripts/map/Y8IsoViewer.gd"
DEFAULT_GODOT = Path("/opt/homebrew/bin/godot")


def _matching_authoring_pids() -> set[int]:
    """Return only this project's standalone Y8 editor processes."""
    result = subprocess.run(
        ["ps", "-axo", "pid=,command="], text=True, capture_output=True,
        check=False,
    )
    matches: set[int] = set()
    project_flag = f"--path {PROJECT}"
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        if (pid_text.isdigit() and project_flag in command
                and VIEWER_SCENE in command and "Godot" in command):
            matches.add(int(pid_text))
    return matches


def _godot_path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise argparse.ArgumentTypeError(f"Godot executable not found: {path}")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Launch the current standalone map viewer in authoring mode."
    )
    parser.add_argument("--godot", type=_godot_path, default=DEFAULT_GODOT)
    parser.add_argument("--resolution", default="1280x800")
    parser.add_argument(
        "--map-id", default="game_map_y8",
        help="Shipped assets/maps map stem to display and edit (default: game_map_y8).",
    )
    parser.add_argument(
        "--read-only", action="store_true",
        help="Open the standalone viewer without the authoring surface.",
    )
    parser.add_argument(
        "--clean-top-down", action="store_true",
        help="Open a full top-down map with every annotation and overlay hidden.",
    )
    parser.add_argument(
        "viewer_args", nargs=argparse.REMAINDER,
        help="Additional viewer args after '--' (for example -- --route-overlay).",
    )
    args = parser.parse_args(argv)

    if re.fullmatch(r"[a-z0-9_]+", args.map_id) is None:
        parser.error("--map-id must contain only lowercase letters, digits, and underscores")
    requested_map = PROJECT / "assets" / "maps" / f"{args.map_id}.a3d"
    if not requested_map.is_file():
        parser.error(f"unknown --map-id {args.map_id!r}: {requested_map} does not exist")

    godot = str(args.godot)
    preflight = subprocess.run(
        [godot, "--headless", "--path", str(PROJECT), "--check-only",
         "--script", VIEWER_SCRIPT],
        cwd=ROOT,
        check=False,
    )
    if preflight.returncode != 0:
        print(
            "MAP_AUTHORING_LAUNCH_REFUSED viewer check-only failed "
            f"exit={preflight.returncode}",
            file=sys.stderr,
            flush=True,
        )
        return preflight.returncode or 1

    user_args = list(args.viewer_args)
    if user_args[:1] == ["--"]:
        user_args.pop(0)
    if not args.read_only and "--edit" not in user_args:
        user_args.insert(0, "--edit")
    if "--return-to-menu" not in user_args:
        user_args.insert(0, "--return-to-menu")
    if not any(value.startswith("--map-id") or value.startswith("--map=")
               or value.startswith("--edit-map") for value in user_args):
        user_args.insert(0, f"--map-id={args.map_id}")
    if args.clean_top_down:
        args.read_only = True
        user_args = [
            "--yaw", "0", "--pitch", "90", "--zoom", "1",
            "--clean-map",
            *[value for value in user_args if value != "--edit"],
        ]

    godot_args = [
        "--path", str(PROJECT),
        "--windowed",
        "--resolution", args.resolution,
        VIEWER_SCENE,
        "--",
        *user_args,
    ]
    command = [godot, *godot_args]
    # A Godot executable started directly on macOS draws a normal window but
    # is not registered with LaunchServices.  That makes the editor invisible
    # to VoiceOver and MCP/UI automation even though a person can click it.
    # Launch the already-preflighted executable through its containing app
    # bundle and wait for that app instance so the headed front door is both
    # accessible and has the same lifetime as this wrapper.
    if sys.platform == "darwin":
        executable = Path(godot).resolve()
        app_bundle = executable.parents[2]
        if app_bundle.suffix == ".app":
            command = [
                "/usr/bin/open", "-W", "-n", "-a", str(app_bundle),
                "--args", *godot_args,
            ]
    print(
        "MAP_AUTHORING_LAUNCH scope=standalone_non_ff "
        f"scene={VIEWER_SCENE} map_id={args.map_id} "
        f"edit={str(not args.read_only).lower()} "
        "windowed_resizable=true escape=main_menu",
        flush=True,
    )
    app_bundle_launch = command[0] == "/usr/bin/open"
    prior_authoring_pids = _matching_authoring_pids() if app_bundle_launch else set()
    process = subprocess.Popen(command, cwd=ROOT)
    if sys.platform == "darwin" and command[0] != "/usr/bin/open":
        subprocess.run(
            [
                "osascript", "-e",
                "tell application \"System Events\" to set frontmost of "
                f"first application process whose unix id is {process.pid} to true",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        if app_bundle_launch:
            # `open -W` is a LaunchServices waiter, not the Godot parent. Stop
            # only the new editor instance this invocation introduced; leave
            # pre-existing Godot/editor/game processes alone.
            for pid in _matching_authoring_pids() - prior_authoring_pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            return 130
        return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
