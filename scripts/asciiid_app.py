#!/usr/bin/env python3
"""Shared asciiid GUI launcher helpers for macOS app-bundle flows.

The raw `.run/asciiid` binary is still the authoritative executable for batch,
MCP, and tests. This module only owns the GUI/app wrapper needed when callers
must launch asciiid as a real `.app` so computer-mode agents can target it.
"""

from __future__ import annotations

import stat
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUN_DIR = PROJECT_ROOT / ".run"
ASCIIID_BIN = RUN_DIR / "asciiid"
ASCIIID_APP = RUN_DIR / "ASCIIID.app"

_INFO_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>asciiid-launcher</string>
  <key>CFBundleIdentifier</key>
  <string>com.asciicker.asciiid</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>ASCIIID</string>
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
ASCIIID_BIN="${ASCIICKER_ASCIIID_BIN:-$RUN_DIR/asciiid}"

resolve_path() {
  case "$1" in
    /*) printf '%s\\n' "$1" ;;
    *) printf '%s/%s\\n' "$PROJECT_ROOT" "$1" ;;
  esac
}

if [ "${ASCIICKER_ACTIVE_MESH_ROOT:-}" = "" ]; then
  map_arg=""
  expect_map=0
  for arg in "$@"; do
    if [ "$expect_map" = 1 ]; then
      map_arg="$arg"
      expect_map=0
      continue
    fi
    if [ "$arg" = "--map" ]; then
      expect_map=1
      continue
    fi
    case "$arg" in
      *.a3d)
        if [ "$map_arg" = "" ]; then
          map_arg="$arg"
        fi
        ;;
    esac
  done

  if [ "$map_arg" != "" ]; then
    map_path="$(resolve_path "$map_arg")"
    map_dir="$(CDPATH= cd -- "$(dirname "$map_path")" && pwd)"
    # WHY prefer the run-local mesh root here: for SBU E2E outputs the terrain
    # and building ratio is only meaningful inside the same run folder. The
    # fallback root meshes may be intentionally stale 1/12-scale copies, so
    # root-vs-run size diffs are not proof that the OSM export itself is wrong.
    env_file="$map_dir/active_mesh_root.env"
    if [ -f "$env_file" ]; then
      while IFS= read -r line; do
        case "$line" in
          ASCIICKER_ACTIVE_MESH_ROOT=*)
            mesh_root="${line#ASCIICKER_ACTIVE_MESH_ROOT=}"
            export ASCIICKER_ACTIVE_MESH_ROOT="$(resolve_path "$mesh_root")"
            break
            ;;
        esac
      done < "$env_file"
    elif [ -d "$map_dir/meshes" ]; then
      export ASCIICKER_ACTIVE_MESH_ROOT="$map_dir/meshes"
    fi
  fi
fi

cd "$PROJECT_ROOT"
exec "$ASCIIID_BIN" "$@"
"""


def _candidate_map_arg(args: list[str] | tuple[str, ...], cwd: Path) -> Path | None:
    """Return the map path implied by CLI args, if any."""
    expect_map = False
    for raw in args:
        if expect_map:
            return (cwd / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
        if raw == "--map":
            expect_map = True
            continue
        if raw.endswith(".a3d") and not raw.startswith("-"):
            return (cwd / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    return None


def _derived_mesh_root(args: list[str] | tuple[str, ...], cwd: Path) -> Path | None:
    """Infer a run-local mesh root from the requested map path.

    SBU E2E OSM runs own an A3D plus sibling meshes as one contract. The
    repo-root `assets/meshes/*.akm` fallback is legacy/stale often enough that
    comparing root-vs-run sizes has already produced false diagnoses multiple
    times; keep the run-local pair together unless a caller overrides it.
    FL-2553 reminder: loading the correct run-local meshes removes one false
    oracle, but it does not by itself prove the runtime visible-size lane is
    closed.
    """
    map_path = _candidate_map_arg(args, cwd)
    if map_path is None:
        return None
    run_root = map_path.parent
    env_file = run_root / "active_mesh_root.env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if not line.startswith("ASCIICKER_ACTIVE_MESH_ROOT="):
                continue
            raw_root = line.split("=", 1)[1].strip()
            if not raw_root:
                continue
            root = Path(raw_root)
            return root.resolve() if root.is_absolute() else (PROJECT_ROOT / root).resolve()
    mesh_root = run_root / "meshes"
    if mesh_root.is_dir():
        return mesh_root.resolve()
    return None


def _merge_mesh_root_env(
    args: list[str] | tuple[str, ...],
    cwd: Path,
    env: dict[str, str] | None,
) -> dict[str, str] | None:
    """Inject a derived mesh root for direct binary launches when callers omit one."""
    if env and env.get("ASCIICKER_ACTIVE_MESH_ROOT"):
        return env
    mesh_root = _derived_mesh_root(args, cwd)
    if mesh_root is None:
        return env
    merged = dict(env or {})
    merged["ASCIICKER_ACTIVE_MESH_ROOT"] = str(mesh_root)
    return merged


def _write_if_changed(path: Path, content: str) -> None:
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")


def ensure_asciiid_app(
    *,
    binary_path: Path | None = None,
    app_path: Path | None = None,
) -> Path:
    """Create or refresh the local ASCIIID.app wrapper and return its path."""
    binary_path = (binary_path or ASCIIID_BIN).resolve()
    app_path = app_path or ASCIIID_APP
    if not binary_path.is_file():
        raise FileNotFoundError(f"missing asciiid binary: {binary_path}")

    (app_path / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
    plist_path = app_path / "Contents" / "Info.plist"
    exec_path = app_path / "Contents" / "MacOS" / "asciiid-launcher"

    _write_if_changed(plist_path, _INFO_PLIST)
    _write_if_changed(exec_path, _APP_EXEC)
    exec_path.chmod(exec_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return app_path


def launch_asciiid_gui(
    args: list[str] | tuple[str, ...] | None = None,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    wait: bool = True,
    binary_path: Path | None = None,
    app_path: Path | None = None,
):
    """Launch asciiid in GUI mode, preferring a macOS app bundle when available."""
    args = list(args or [])
    cwd = cwd or PROJECT_ROOT
    binary_path = (binary_path or ASCIIID_BIN).resolve()
    env = _merge_mesh_root_env(args, cwd, env)

    if sys.platform == "darwin":
        app_bundle = ensure_asciiid_app(binary_path=binary_path, app_path=app_path)
        cmd = ["open", "-n"]
        if wait:
            cmd.append("-W")
        cmd.append(str(app_bundle))
        if args:
            cmd.extend(["--args", *args])
        return subprocess.run(cmd, cwd=str(cwd), env=env, check=False)

    return subprocess.run([str(binary_path), *args], cwd=str(cwd), env=env, check=False)


def launch_asciiid_gui_detached(
    args: list[str] | tuple[str, ...] | None = None,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    binary_path: Path | None = None,
    app_path: Path | None = None,
):
    """Launch asciiid detached, preferring a macOS app bundle when available."""
    args = list(args or [])
    cwd = cwd or PROJECT_ROOT
    binary_path = (binary_path or ASCIIID_BIN).resolve()
    env = _merge_mesh_root_env(args, cwd, env)

    if sys.platform == "darwin":
        app_bundle = ensure_asciiid_app(binary_path=binary_path, app_path=app_path)
        cmd = ["open", "-n", str(app_bundle)]
        if args:
            cmd.extend(["--args", *args])
        return subprocess.Popen(
            cmd,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    return subprocess.Popen(
        [str(binary_path), *args],
        cwd=str(cwd),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
