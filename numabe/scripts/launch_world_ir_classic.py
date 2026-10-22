#!/usr/bin/env python3
"""Launch a compiled World-IR map in the Classic runtime."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import hashlib
import time
import sys
from pathlib import Path

# FL-4814: shared compiler owners use the repository package from a direct CLI.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--receipt", type=Path, required=True)
    ap.add_argument("--game", type=Path, default=Path(".run/game_classic"))
    ap.add_argument('--intermediate-review', action='store_true',
                    help='Explicit macro visual review; does not establish final acceptance')
    ap.add_argument("--capture-render", action="store_true",
                    help="Capture one Classic resolved frame and exit; requires intermediate review")
    args = ap.parse_args(argv)
    # G3 default-route guard: this front door owns the Classic product only.
    # An explicit Current binary passed here would execute under a Classic
    # receipt identity (runtime_product classic_cp437 below), so fail closed
    # and keep explicit Current on make run-game-fl4512 / launcher FL-4512.
    if Path(args.game).name != "game_classic":
        ap.error("--game must be the Classic binary (.run/game_classic); "
                 "explicit Current remains available via make run-game-fl4512")
    if args.capture_render and not args.intermediate_review:
        ap.error("--capture-render requires --intermediate-review")
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    if args.intermediate_review:
        print('INTERMEDIATE CLASSIC VISUAL REVIEW — FULL MAP ACCEPTANCE INCOMPLETE', flush=True)
    if args.intermediate_review:
        subprocess.run(['make','audit-game-classic'], cwd=Path(__file__).resolve().parents[1], check=True)
    map_path = Path(receipt["map_path"])
    if not map_path.is_absolute():
        map_path = args.receipt.parent / map_path
    env = os.environ.copy()
    mesh_root = receipt.get("mesh_root")
    if mesh_root:
        root = Path(mesh_root)
        if not root.is_absolute():
            root = args.receipt.parent / root
        env["ASCIICKER_ACTIVE_MESH_ROOT"] = str(root.resolve())
    command = [str(args.game.resolve()), "--auto-start-map", str(map_path.resolve())]
    if args.capture_render:
        # FL-4814: preserve the actual Classic resolved frame beside this map.
        # This is renderer evidence, not a substituted facade preview or traversal.
        capture = args.receipt.parent / time.strftime('classic_review_%Y%m%d_%H%M%S')
        capture.mkdir(parents=True, exist_ok=False)
        command += ['--observe-render', str(capture.resolve())]
        (capture/'launch_receipt.json').write_text(json.dumps({
            'stage':'intermediate_review_launch_requested',
            'runtime_product':'classic_cp437', 'command':command,
            'map_sha256':hashlib.sha256(map_path.read_bytes()).hexdigest(),
            'runtime_sha256':hashlib.sha256(args.game.read_bytes()).hexdigest(),
            'build_receipt':str(args.receipt.resolve()),
            'final_acceptance':False,
        },indent=2)+'\n')
    return subprocess.call(command, env=env)


if __name__ == "__main__":
    raise SystemExit(main())
