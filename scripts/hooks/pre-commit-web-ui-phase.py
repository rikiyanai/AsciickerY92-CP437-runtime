#!/usr/bin/env python3
"""
Selective pre-commit verification for web UI phase work.

Runs targeted tests only when staged files touch wizard/workbench/viewer
surfaces tied to phase-13/16 browser workflows.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Set


TEST_GROUPS: Dict[str, Dict[str, object]] = {
    "wizard_layout": {
        "paths": {
            "scripts/pipeline/web_ui/app.js",
            "scripts/pipeline/web_ui/layout_editor.js",
            "scripts/pipeline/web_ui/index.html",
            "scripts/pipeline/web_ui/styles.css",
            "scripts/pipeline/tests/e2e/test_wizard_layout_editor.py",
        },
        "command": (
            'python3 -m pytest scripts/pipeline/tests/e2e/test_wizard_layout_editor.py '
            '-m e2e -o "addopts=" --browser chromium -q'
        ),
        "label": "wizard layout editor browser flow",
    },
    "viewer_model": {
        "prefixes": {
            "scripts/pipeline/web_ui/sprite_viewer/",
        },
        "paths": {
            "scripts/pipeline/tests/test_frame_sequence.py",
            "scripts/pipeline/tests/test_viewer_loaders.py",
        },
        "command": (
            "python3 -m pytest "
            "scripts/pipeline/tests/test_frame_sequence.py "
            "scripts/pipeline/tests/test_viewer_loaders.py -q"
        ),
        "label": "sprite viewer model/loaders",
    },
    "workbench_browser": {
        "paths": {
            "scripts/pipeline/web_ui/workbench.js",
            "scripts/pipeline/web_ui/cell_editor.js",
            "scripts/pipeline/web_ui/merge_panel.js",
            "scripts/pipeline/tests/e2e/test_workbench_behavior.py",
        },
        "command": (
            'python3 -m pytest -o "addopts=" -m e2e --browser chromium '
            "scripts/pipeline/tests/e2e/test_workbench_behavior.py "
            '-k "upload_extract_produces_sprites or '
            'extract_assign_populates_grid_with_thumbnails or '
            'export_produces_xp_file" -q'
        ),
        "label": "workbench browser critical flow",
    },
}


def _repo_root() -> Path:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return Path(proc.stdout.strip())


def _staged_files(root: Path) -> List[str]:
    proc = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=True,
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _matches_group(file_path: str, group: Dict[str, object]) -> bool:
    paths: Set[str] = set(group.get("paths", set()))  # type: ignore[arg-type]
    prefixes: Set[str] = set(group.get("prefixes", set()))  # type: ignore[arg-type]
    if file_path in paths:
        return True
    for prefix in prefixes:
        if file_path.startswith(prefix):
            return True
    return False


def _run(command: str, cwd: Path) -> int:
    proc = subprocess.run(command, cwd=cwd, shell=True)
    return proc.returncode


def main() -> int:
    if os.getenv("SKIP_WEB_UI_PHASE_GATE") == "1":
        print("pre-commit(web-ui): skipped via SKIP_WEB_UI_PHASE_GATE=1")
        return 0

    root = _repo_root()
    staged = _staged_files(root)
    if not staged:
        return 0

    selected_groups: List[str] = []
    for name, group in TEST_GROUPS.items():
        if any(_matches_group(path, group) for path in staged):
            selected_groups.append(name)

    if not selected_groups:
        return 0

    print("pre-commit(web-ui): staged web-ui/workbench/viewer changes detected")
    for group_name in selected_groups:
        group = TEST_GROUPS[group_name]
        label = str(group["label"])
        cmd = str(group["command"])
        print(f"pre-commit(web-ui): running {label}...")
        code = _run(cmd, root)
        if code != 0:
            print("")
            print(f"pre-commit(web-ui): FAIL in group '{group_name}'")
            print(f"  command: {cmd}")
            print("  set SKIP_WEB_UI_PHASE_GATE=1 to bypass in emergencies")
            return code

    print("pre-commit(web-ui): OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        if exc.stderr:
            print(exc.stderr.strip(), file=sys.stderr)
        raise
