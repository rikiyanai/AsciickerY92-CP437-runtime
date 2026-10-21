#!/usr/bin/env python3
"""Public launch and integrity front door for the in-repository Numabe workspace.

The imported Classic source is isolated from the root runtime's dirty engine
changes. This bridge is explicit: it is not a claim those engines are unified.
All active map, sprite, train and editor writes belong to this checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WORKSPACE = REPO / "numabe"
BUILD = WORKSPACE / ".run/fl4876_builds/live_numabe"
EDITOR = WORKSPACE / "artifacts/experiments/fl4876_manual_world/map_editor.py"


def check() -> dict:
    """Fail closed when the live map/assets or an active destination path is absent."""
    sys.path.insert(0, str(EDITOR.parent))
    import a3d_edit
    records = a3d_edit.parse((BUILD / "output.a3d").read_bytes())[2]
    missing = sorted({r["mesh"] for r in records if r["kind"] == "mesh"
                      and not (BUILD / "meshes" / r["mesh"]).is_file()
                      and not (WORKSPACE / "assets/meshes" / r["mesh"]).is_file()})
    # A regeneration must not silently resurrect the exact retired slab.
    # New reviewed geometry must have its own versioned mesh identity.
    retired = [r["inst"] for r in records if r.get("mesh") ==
               "facade_feature_facade_plane_numabe_station_entrance_1.akm"]
    receipt = json.loads((BUILD / "build_receipt.json").read_text())
    required = {"map_path": BUILD / "output.a3d", "mesh_root": BUILD / "meshes",
                "train_routes": BUILD / "output.trains.json"}
    def owned_path(value):
        """Published receipts are workspace-relative and survive a fresh clone."""
        path = Path(value)
        return (path if path.is_absolute() else WORKSPACE / path).resolve()

    bad_paths = [k for k, path in required.items()
                 if owned_path(receipt[k]) != path or not path.exists()]
    missing_browser = [name for name in ("index.js", "index.wasm", "index.data")
                       if not (WORKSPACE / ".web-classic" / name).is_file()]
    panos = json.loads((EDITOR.parent / "pano_index.json").read_text())["panoramas"]
    missing_panos = [key for key, row in panos.items()
                     if row.get("local_path") and not (WORKSPACE / row["local_path"]).is_file()]
    if missing or bad_paths or missing_panos or retired or missing_browser:
        raise SystemExit(json.dumps({"missing_meshes": missing, "bad_active_paths": bad_paths,
                                     "missing_panoramas": missing_panos,
                                     "retired_track_obstruction": retired,
                                     "missing_browser_assets": missing_browser}))
    return {"workspace": str(WORKSPACE), "map": str(required["map_path"]),
            "map_sha256": hashlib.sha256(required["map_path"].read_bytes()).hexdigest(),
            "mesh_instances": sum(r["kind"] == "mesh" for r in records),
            "missing_meshes": missing, "panoramas": len(panos),
            "missing_panoramas": missing_panos, "retired_track_obstruction": retired,
            "first_person_gameplay": "unsupported: Classic fixed-tilt camera is not eye-level"}


def main() -> None:
    """Validate ownership before launching the user's editor or actual native game."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=("check", "editor", "game"))
    ap.add_argument("--port", type=int, default=8792)
    args = ap.parse_args()
    print(json.dumps(check(), ensure_ascii=False), flush=True)
    if args.action == "check":
        return
    if args.action == "editor":
        os.execv(sys.executable, [sys.executable, str(EDITOR), "--port", str(args.port),
                                  "--build", str(BUILD)])
    game = WORKSPACE / ".run/game_classic"
    if not game.is_file():
        raise SystemExit("Build the migrated source first: make numabe-game")
    os.chdir(WORKSPACE)
    os.environ.update(ASCIICKER_ACTIVE_MESH_ROOT=str(BUILD / "meshes"),
                      ASCIICKER_TRAIN_ROUTES=str(BUILD / "output.trains.json"))
    os.execv(str(game), [str(game), "--auto-start-map", str(BUILD / "output.a3d")])


if __name__ == "__main__":
    main()
