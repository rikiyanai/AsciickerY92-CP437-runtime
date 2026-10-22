#!/usr/bin/env python3
"""Run the headed GODOT-T07 collision behavior experiment.

Each side gets one fresh Colyseus room and one continuous headed Godot runtime.
The Godot-side driver emits authoritative actor snapshots after real key events;
this wrapper only owns process provenance and joins the two side receipts.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GODOT_BIN = Path(os.environ.get("GODOT_BIN", "/opt/homebrew/bin/godot"))
GODOT_PROJECT = ROOT / "godot_project"
COLYSEUS_DIR = ROOT / "colyseus_server"
DRIVER = ROOT / "scripts" / "godot_mesh_collision_behavior_driver.gd"
DEFAULT_OUT = ROOT / ".run" / "fl4781_mesh_collision_gate"
PORT = 2675
SCHEMA = "godot.mesh_collision_behavior.receipt.v1"
ROUTE_IDENTITY = "godot_colyseus_authored_mesh_collision_behavior_v1"
SIDES = ("front", "reverse")
DEFAULT_TARGET = "wall"

_CHECKER_CACHE: Any = None


def _checker():
    """Load the analyzer once.  It is this route's single geometry owner."""
    global _CHECKER_CACHE
    if _CHECKER_CACHE is None:
        _CHECKER_CACHE = _load_checker()
    return _CHECKER_CACHE


def target_ids() -> tuple[str, ...]:
    return tuple(_checker().TARGETS.keys())


def _target_row(target: str) -> dict[str, Any]:
    return _checker().target_row(target)


def _spawns(target: str) -> dict[str, tuple[float, float]]:
    """Per-side spawn poses for ``target``.

    FL-4740: these used to be a hard-coded second copy of the wall approach
    starts.  They are now read from the analyzer's target row, so the spawn the
    server is given and the ``start_authoritative_pose`` the analyzer demands
    can never drift apart.
    """
    row = _target_row(target)
    return {"front": tuple(row["front_from"]), "reverse": tuple(row["reverse_from"])}


def _target_mesh_path(target: str) -> Path:
    """Repo-root-relative mesh asset that owns ``target``'s geometry."""
    return ROOT / _target_row(target)["mesh_path"]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_provenance() -> dict[str, Any]:
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    diff = subprocess.run(
        ["git", "diff", "HEAD", "--binary"], cwd=ROOT, capture_output=True, check=False
    )
    status = subprocess.run(
        ["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    return {
        "git_head": head.stdout.strip() if head.returncode == 0 else "",
        "worktree_diff_sha256": hashlib.sha256(diff.stdout).hexdigest() if diff.returncode == 0 else "",
        "git_status_paths": [line for line in status.stdout.splitlines() if line.strip()]
        if status.returncode == 0 else [],
    }


def _godot_version() -> str:
    result = subprocess.run([str(GODOT_BIN), "--version"], capture_output=True, text=True, check=False)
    return (result.stdout or result.stderr).strip().splitlines()[0] if result.returncode == 0 else ""


def _load_checker():
    path = ROOT / "scripts" / "check_godot_mesh_collision_behavior.py"
    spec = importlib.util.spec_from_file_location("godot_mesh_collision_checker", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("collision checker import unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _server_reader(process: subprocess.Popen[str], log_path: Path, ready: threading.Event, state: dict[str, Any]) -> None:
    """Drain server output and signal only on the explicit LISTENING marker."""
    stream = process.stdout
    if stream is None:
        state["reason"] = "server_stdout_unavailable"
        return
    with log_path.open("w", encoding="utf-8") as log:
        for line in stream:
            log.write(line)
            log.flush()
            if "ASCIICKER_COLYSEUS_SPIKE_LISTENING" in line:
                ready.set()
        if process.poll() is not None and not ready.is_set():
            state["reason"] = f"server_exited_before_listening_rc_{process.returncode}"


def _terminate_server(process: subprocess.Popen[str]) -> dict[str, Any]:
    result: dict[str, Any] = {"pid": process.pid, "signal": "SIGINT", "returncode": None, "forced": False}
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            result["returncode"] = process.wait(timeout=8.0)
        except subprocess.TimeoutExpired:
            result["forced"] = True
            process.kill()
            result["signal"] = "SIGKILL"
            result["returncode"] = process.wait(timeout=3.0)
    else:
        result["returncode"] = process.returncode
    return result


def _runtime_base(
    source: dict[str, Any],
    map_hash: str,
    historical_map_hash: str,
    mesh_hash: str,
    godot_version: str,
    side: str,
    port: int,
    target: str = DEFAULT_TARGET,
) -> dict[str, Any]:
    row = _target_row(target)
    return {
        "schema": "godot.mesh_collision_behavior.runtime.v1",
        "feature_id": "GODOT-T07",
        "route_identity": ROUTE_IDENTITY,
        "side": side,
        # FL-4740: the requested collision target.  The driver echoes the id it
        # actually used; this field is the REQUEST, and a mismatch between the
        # two is itself evidence.
        "target_id": target,
        "source": source,
        "target": {
            "headed": True,
            "godot_version": godot_version,
            "rendering_method": "gl_compatibility",
            "project": str(GODOT_PROJECT),
            "scene": "res://scenes/Main.tscn",
            "godot_user_args_include_colyseus_client": True,
        },
        "map": {
            "runtime_path": "res://assets/maps/game_map_y8.a3d",
            "runtime_sha256": map_hash,
            "historical_mesh_map_path": "assets/a3d/game_map_y8.a3d",
            "historical_mesh_map_sha256": historical_map_hash,
        },
        "mesh": {
            "target_id": target,
            "shape": row["shape"],
            "name": row["mesh_name"],
            "source_path": row["mesh_path"],
            "source_sha256": mesh_hash,
            "face": row["face"],
            "wall_center": list(row["center"]),
            "wall_normal": list(row["normal"]),
            "half_extent": list(row["half"]),
            "canonical_source": "scripts/proofs/proof_fl4128_building_collision.js",
            "canonical_historical_map": "assets/a3d/game_map_y8.a3d",
        },
        "authority": {
            "kind": "colyseus_server",
            "port": port,
            "input_path": "InputEventKey->InputMap->player._process_colyseus_intent_only->Main.notify_local_intent",
            "snapshot_position_field": "actors[*].position",
            "snapshot_authority_owner": "colyseus_server/src/rooms/AuthoritativeRoom.ts",
            "contact_fields_available": False,
        },
        "direct_transform_mutation": False,
        "input_route": "Input.parse_input_event(InputEventKey) only",
    }


def _run_side(
    side: str,
    out_dir: Path,
    source: dict[str, Any],
    map_hash: str,
    historical_map_hash: str,
    mesh_hash: str,
    godot_version: str,
    port: int,
    timeout: float,
    target: str = DEFAULT_TARGET,
) -> dict[str, Any]:
    side_dir = out_dir / side
    side_dir.mkdir(parents=True, exist_ok=True)
    runtime_path = side_dir / "runtime_receipt.json"
    server_log = side_dir / "colyseus_server.log"
    godot_log = side_dir / "godot_client.log"
    spawn_x, spawn_z = _spawns(target)[side]
    env = dict(os.environ)
    env.update(
        {
            "COLYSEUS_PORT": str(port),
            "PLAYER_SPAWN_X": str(spawn_x),
            "PLAYER_SPAWN_Z": str(spawn_z),
            "ASCIICKER_COLYSEUS_A3D_MAP": str(GODOT_PROJECT / "assets/maps/game_map_y8.a3d"),
        }
    )
    server = subprocess.Popen(
        ["node", "--import", "tsx", "src/index.ts"],
        cwd=COLYSEUS_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    ready = threading.Event()
    reader_state: dict[str, Any] = {}
    reader = threading.Thread(
        target=_server_reader,
        args=(server, server_log, ready, reader_state),
        name=f"godot-t07-{side}-server-log",
        daemon=True,
    )
    reader.start()
    process_info: dict[str, Any] = {
        "side": side,
        "target_id": target,
        "server_pid": server.pid,
        "server_log": str(server_log),
        "godot_log": str(godot_log),
        "runtime_receipt": str(runtime_path),
        "completion_signal": "Godot process exit plus runtime_receipt JSON",
        "spawn_xz": [spawn_x, spawn_z],
    }
    runtime: dict[str, Any] = _runtime_base(
        source, map_hash, historical_map_hash, mesh_hash, godot_version, side, port, target
    )
    try:
        if not ready.wait(timeout=max(1.0, timeout / 3.0)):
            runtime["runtime_status"] = "setup_failure"
            runtime["reason"] = reader_state.get("reason", "server_listening_signal_timeout")
            return {"runtime": runtime, "process": process_info}
        godot_command = [
            str(GODOT_BIN),
            "--rendering-method",
            "gl_compatibility",
            "--windowed",
            "--resolution",
            "1280x800",
            "--path",
            str(GODOT_PROJECT),
            "--script",
            str(DRIVER),
            "--",
            "--colyseus-client",
            f"--port={port}",
            f"--mesh-collision-receipt={runtime_path}",
            f"--mesh-collision-side={side}",
            # FL-4740: without this the driver silently falls back to its `wall`
            # row and a pieta run would publish wall geometry.
            f"--mesh-collision-target={target}",
            f"--mesh-collision-build-head={source['git_head']}",
            f"--mesh-collision-diff-sha256={source['worktree_diff_sha256']}",
            f"--mesh-collision-map-sha256={map_hash}",
            f"--mesh-collision-mesh-sha256={mesh_hash}",
            f"--mesh-collision-historical-map-sha256={historical_map_hash}",
        ]
        process_info["godot_command"] = godot_command
        with godot_log.open("w", encoding="utf-8") as log:
            godot = subprocess.Popen(
                godot_command,
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            process_info["godot_pid"] = godot.pid
            try:
                process_info["godot_returncode"] = godot.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process_info["godot_returncode"] = 124
                runtime["runtime_status"] = "setup_failure"
                runtime["reason"] = "headed_godot_timeout_before_completion_receipt"
                godot.kill()
                godot.wait(timeout=5.0)
        if runtime_path.exists():
            try:
                runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                runtime["runtime_status"] = "setup_failure"
                runtime["reason"] = f"runtime_receipt_invalid:{exc}"
        else:
            runtime["runtime_status"] = "setup_failure"
            runtime["reason"] = runtime.get("reason", "runtime_receipt_missing")
        return {"runtime": runtime, "process": process_info}
    finally:
        process_info["server_shutdown"] = _terminate_server(server)
        reader.join(timeout=3.0)


def _aggregate(
    source: dict[str, Any],
    map_hash: str,
    historical_map_hash: str,
    mesh_hash: str,
    godot_version: str,
    port: int,
    runs: dict[str, dict[str, Any]],
    requested_target: str = DEFAULT_TARGET,
) -> dict[str, Any]:
    front = runs["front"]["runtime"]
    reverse = runs["reverse"]["runtime"]
    # FL-4740: the authoritative target id is the one the DRIVER published, not
    # the one this wrapper asked for.  A driver that fell back to `wall` must be
    # judged as a wall run, so the analyzer reads runtime truth rather than the
    # command line.  `requested_target_id` is published beside it precisely so a
    # disagreement stays visible in the receipt instead of being papered over.
    published = front.get("target_id")
    if not isinstance(published, str):
        mesh_block = front.get("mesh")
        published = mesh_block.get("target_id") if isinstance(mesh_block, dict) else None
    effective_target = published if isinstance(published, str) else requested_target
    row = _target_row(effective_target)
    base = {
        "schema": SCHEMA,
        "feature_id": "GODOT-T07",
        "route_identity": ROUTE_IDENTITY,
        "target_id": effective_target,
        "requested_target_id": requested_target,
        "target_id_matches_request": effective_target == requested_target,
        "source": source,
        "target": {
            "headed": True,
            "godot_version": godot_version,
            "rendering_method": "gl_compatibility",
            "project": str(GODOT_PROJECT),
            "scene": "res://scenes/Main.tscn",
            "mode": "colyseus-client",
        },
        "map": {
            "runtime_path": "res://assets/maps/game_map_y8.a3d",
            "runtime_sha256": map_hash,
            "historical_mesh_map_path": "assets/a3d/game_map_y8.a3d",
            "historical_mesh_map_sha256": historical_map_hash,
        },
        "mesh": front.get("mesh", {
            "target_id": effective_target,
            "name": row["mesh_name"],
            "source_path": row["mesh_path"],
            "source_sha256": mesh_hash,
            "face": row["face"],
            "wall_center": list(row["center"]),
            "wall_normal": list(row["normal"]),
        }),
        "authority": front.get("authority", {
            "kind": "colyseus_server",
            "port": port,
            "input_path": "InputEventKey->InputMap->player._process_colyseus_intent_only->Main.notify_local_intent",
            "snapshot_position_field": "actors[*].position",
            "snapshot_authority_owner": "colyseus_server/src/rooms/AuthoritativeRoom.ts",
            "contact_fields_available": False,
        }),
        "direct_transform_mutation": False,
        "runtime_mesh_observation": {
            "front": front.get("runtime_mesh_facts", {}),
            "reverse": reverse.get("runtime_mesh_facts", {}),
        },
        "choreographies": {
            "front": {**front.get("choreography", {}), "contact_fact": front.get("contact_fact", {})},
            "reverse": {**reverse.get("choreography", {}), "contact_fact": reverse.get("contact_fact", {})},
        },
        "free_space_control": front.get("free_space_control", front.get("free_space_control", {})),
        "runtime_runs": {
            side: {
                "runtime_status": runs[side]["runtime"].get("runtime_status", "unknown"),
                "reason": runs[side]["runtime"].get("reason", ""),
                "process": runs[side]["process"],
                "runtime_receipt": str(runs[side]["process"].get("runtime_receipt", "")),
            }
            for side in SIDES
        },
        "contact_facts": {
            "available": False,
            "absence_reason": "colyseus_actor_schema_exposes_no_contact_collider_or_normal_field",
        },
    }
    return base


def run(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = getattr(args, "target", DEFAULT_TARGET)
    map_path = GODOT_PROJECT / "assets/maps/game_map_y8.a3d"
    historical_map_path = ROOT / "assets/a3d/game_map_y8.a3d"
    # FL-4740: the provenance mesh is the ACTIVE target's own asset, so a pieta
    # run hashes the statue rather than the unrelated wall .akm.
    mesh_path = _target_mesh_path(target)
    missing = [str(path) for path in (map_path, historical_map_path, mesh_path) if not path.is_file()]
    source = _source_provenance()
    if missing:
        receipt = {
            "schema": SCHEMA,
            "feature_id": "GODOT-T07",
            "route_identity": ROUTE_IDENTITY,
            "outcome": "setup_failure",
            "pass": False,
            "reason": "provenance_asset_missing",
            "target_id": target,
            "missing_paths": missing,
            "source": source,
        }
        (out_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        return 1, receipt
    if not GODOT_BIN.is_file():
        receipt = {
            "schema": SCHEMA,
            "feature_id": "GODOT-T07",
            "route_identity": ROUTE_IDENTITY,
            "outcome": "setup_failure",
            "pass": False,
            "reason": "godot_binary_missing",
            "target_id": target,
            "godot_binary": str(GODOT_BIN),
            "source": source,
        }
        (out_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        return 1, receipt
    map_hash = _sha256(map_path)
    historical_map_hash = _sha256(historical_map_path)
    mesh_hash = _sha256(mesh_path)
    godot_version = _godot_version()
    if args.preflight_only:
        receipt = _runtime_base(
            source, map_hash, historical_map_hash, mesh_hash, godot_version, "preflight", args.port, target
        )
        spawns = _spawns(target)
        receipt.update({
            "outcome": "preflight",
            "pass": True,
            "reason": "launch_contract_ready",
            # The exact facts a headed run would be launched with, so a preflight
            # can be inspected instead of trusted.
            "planned_spawns": {side: list(spawns[side]) for side in SIDES},
            "planned_driver_target_arg": f"--mesh-collision-target={target}",
            "provenance_mesh_path": str(mesh_path.relative_to(ROOT)),
        })
        (out_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        return 0, receipt
    runs: dict[str, dict[str, Any]] = {}
    for index, side in enumerate(SIDES):
        side_port = args.port + index
        runs[side] = _run_side(
            side, out_dir, source, map_hash, historical_map_hash, mesh_hash, godot_version,
            side_port, args.timeout, target,
        )
    receipt = _aggregate(
        source, map_hash, historical_map_hash, mesh_hash, godot_version, args.port, runs, target
    )
    checker = _load_checker()
    verdict = checker.check_receipt(receipt)
    receipt["outcome"] = verdict.get("outcome", "setup_failure")
    receipt["pass"] = verdict.get("pass", False)
    receipt["reason"] = verdict.get("reason", "checker_no_reason")
    receipt["checker_contract_failures"] = verdict.get("contract_failures", [])
    receipt["checker_choreographies"] = verdict.get("choreographies", {})
    receipt_path = out_dir / "receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt["artifact_paths"] = {
        "receipt": str(receipt_path),
        "front_runtime": str(out_dir / "front" / "runtime_receipt.json"),
        "reverse_runtime": str(out_dir / "reverse" / "runtime_receipt.json"),
    }
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return (0 if receipt["pass"] is True else 1), receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--preflight-only", action="store_true")
    # FL-4740: selects the collision target row owned by
    # scripts/check_godot_mesh_collision_behavior.py TARGETS.  Default keeps the
    # historical wall route byte-identical.
    parser.add_argument("--target", choices=sorted(target_ids()), default=DEFAULT_TARGET)
    args = parser.parse_args(argv)
    code, receipt = run(args)
    print(json.dumps({
        "outcome": receipt.get("outcome"),
        "reason": receipt.get("reason"),
        "target_id": receipt.get("target_id", args.target),
        "artifact": str(Path(args.out).resolve() / "receipt.json"),
    }, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
