#!/usr/bin/env python3
"""FL-4265 hunter catch — headed proof that the mount-triggered hunter exists.

Boots the real Colyseus server with the hunter bot ENABLED (the proof
harnesses all disable it; this one must not), launches ONE headed Godot
client on the real --colyseus-client path running the hunter-catch harness,
and reports the receipt. The harness walks the real player to the real
airplane, boards with a physical M key event, and screenshots the hunter in
the live viewport with same-frame runtime truth.

Sequence:
  1. Start colyseus_server (node --import tsx src/index.ts) on COLYSEUS_PORT.
  2. Wait for ASCIICKER_COLYSEUS_SPIKE_LISTENING.
  3. Launch headed Godot with scripts/proofs/fl4265_hunter_catch.gd, which
     joins, walks, boards, waits for the hunter, sweeps the chase camera,
     captures, and writes receipt.json.
  4. Read the receipt, count hunter steering witnesses in the server log,
     tear down the server, and report PASS/FAIL.

Fail-closed: HUNTER_NEVER_SPAWNED means the spawn did not happen on real
terrain; HUNTER_NEVER_IN_FRAME means it spawned but never rendered in frame.
Both are findings, never passes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from godot_proof_publication import (
    PublicationError, apply_finder_tags, deterministic_artifact_name,
    inspect_visual, publish_artifacts, sha256_file, validate_manifest, verify_finder_tags,
)
from run_fl4359_headed_movement import _runtime_source_identity, _persist_runtime_source_identity

ROOT = Path(__file__).resolve().parents[1]
COLYSEUS_DIR = ROOT / "colyseus_server"
GODOT_PROJECT = ROOT / "godot_project"
HARNESS = "res://scripts/proofs/fl4265_hunter_catch.gd"
GODOT_BIN = os.environ.get("GODOT_BIN", "/opt/homebrew/bin/godot")
HUNTER_WITNESS_PREFIX = "AIRPLANE_STEERING_WITNESS "
HUNTER_PICKUP_ID = "pickup_mount_airplane_hunter"


def _publish_captures(run_dir: Path, run_id: str, receipt: dict, build: dict) -> Path:
    """Preserve raw aliases and publish only observed, same-run identities."""
    source = receipt.get("publication_runtime_source_identity", {})
    launch = source.get("launch", {})
    captures = sorted((run_dir / "captures").glob("*.png"))
    if not captures:
        raise PublicationError("hunter route produced no capture images")
    rows = []
    for capture in captures:
        sidecar = capture.with_name(capture.name + ".identity.json")
        if not sidecar.is_file():
            raise PublicationError(f"same-frame identity sidecar missing: {capture.name}")
        row = json.loads(sidecar.read_text())
        if row.get("schema") != "fl4265.capture_identity.v1":
            raise PublicationError(f"capture identity schema missing: {capture.name}")
        if Path(row.get("image_path", "")).resolve() != capture.resolve():
            raise PublicationError(f"capture identity names a different image: {capture.name}")
        w, h, _ = inspect_visual(capture)
        if row.get("image_dimensions") != [w, h]:
            raise PublicationError(f"capture dimensions contradict image: {capture.name}")
        validity = row.get("proof_capture_validity", {})
        if (validity.get("owner") != "Main shared occluder/blank guards"
                or validity.get("occluder") != ""
                or validity.get("blank") is not False
                or not isinstance(validity.get("samples"), int)
                or isinstance(validity.get("samples"), bool)
                or validity["samples"] <= 0
                or not isinstance(validity.get("luma_spread"), (int, float))
                or isinstance(validity.get("luma_spread"), bool)
                or not math.isfinite(validity["luma_spread"])
                or validity["luma_spread"] < 0.06):
            raise PublicationError(f"clean-world capture guard missing or rejected: {capture.name}")
        camera = row.get("pixel_camera", {})
        if (camera.get("owner") != "PixelCameraContext"
                or not isinstance(camera.get("frame_index"), int)
                or isinstance(camera.get("frame_index"), bool)
                or camera["frame_index"] < 0
                or not isinstance(camera.get("frame_time_seconds"), (int, float))
                or not math.isfinite(camera["frame_time_seconds"])):
            raise PublicationError(f"same-frame camera identity incomplete: {capture.name}")
        rows.append(row)
    identities = {(r.get("architecture"), r.get("presentation_mode"),
                   tuple(r.get("pixel_camera", {}).get("cell_size", [])),
                   r.get("engine", {}).get("string"), r.get("map"), r.get("scene"),
                   r.get("target"), r.get("architecture_script"),
                   json.dumps(r.get("atlas_identity"), sort_keys=True)) for r in rows}
    if len(identities) != 1:
        raise PublicationError("capture architecture/mode/cell/engine/map identities changed")
    row = rows[0]
    mode = row.get("presentation_mode")
    mode_map = {"FC": ("FLAT", "CONTINUOUS"), "CF": ("CONTINUOUS", "FLAT"),
                "CC": ("CONTINUOUS", "CONTINUOUS")}
    if mode not in mode_map:
        raise PublicationError("hunter publication requires observed non-FF mode")
    cell = row.get("pixel_camera", {}).get("cell_size", [])
    glyphs = row.get("atlas_identity", {}).get("sources", [])
    if len(cell) != 2 or not glyphs:
        raise PublicationError("capture cell/atlas identity incomplete")
    sizes = sorted({(g["dimensions"]["w"], g["dimensions"]["h"]) for g in glyphs})
    # Exported bound atlas pages are evidence, not a checkout-time substitute.
    from PIL import Image
    for page, glyph in enumerate(glyphs):
        atlas = run_dir / "captures" / "atlas" / f"page_{page}.png"
        if not atlas.is_file() or glyph.get("hash_kind") != "decoded_rgba8":
            raise PublicationError(f"bound atlas witness missing: page_{page}")
        with Image.open(atlas) as decoded:
            dimensions = glyph["dimensions"]
            if (decoded.size != (dimensions["w"], dimensions["h"])
                    or hashlib.sha256(decoded.convert("RGBA").tobytes()).hexdigest() != glyph.get("sha256")):
                raise PublicationError(f"bound atlas contradicts capture identity: page_{page}")
    complete = (launch.get("valid") is True and source.get("scoped_snapshots_match") is True
                and build.get("sha256") and row.get("architecture") not in (None, "UNKNOWN"))
    metadata = {
        "fl_id": "FL-4265", "run_id": run_id, "proof_kind": "RENDER",
        "canonical_proof_id": "proof.fl4265_hunter_catch",
        "provenance_status": "ATTESTED" if complete else "INCOMPLETE_CURRENT_CAPTURE",
        "architecture": row.get("architecture", "UNKNOWN"),
        "foreground_mode": mode_map[mode][0], "background_mode": mode_map[mode][1],
        "capture_source_state": "DIRTY" if launch.get("dirty_paths") or launch.get("untracked_paths") else "CLEAN",
        "source_commit": launch.get("source_commit", "UNKNOWN"),
        "effective_tree_fingerprint": launch.get("effective_tree_fingerprint", "UNKNOWN"),
        "executed_sources": launch.get("executed_source_files", []),
        "build_identity": build,
        "runtime_identity": {"target": row.get("target", "UNKNOWN"),
                             "engine_version": row.get("engine", {}).get("string", "UNKNOWN")},
        "workload_identity": {"runner": "scripts/run_fl4265_hunter_catch.py",
                              "scene": row.get("scene", "UNKNOWN"), "map": row.get("map", "UNKNOWN"),
                              "pose": "hunter-catch: physical walk/board, chase orbit, closeup",
                              "camera": "PixelCameraContext; exact per-image sidecar",
                              "timing": "engine frame_index/frame_time_seconds in each capture sidecar"},
        "cell_resolution": {"width": cell[0], "height": cell[1]},
        "glyph_source_resolution": [{"width": w, "height": h} for w, h in sizes],
        "storage": {"status": "PENDING"},
        "limitations": source.get("limitations", []) + [
            "route execution does not establish visual acceptance; inspect the actual named captures",
            "source/build identity is direct launch, not an exported consumer artifact"],
    }
    destination = run_dir / "published"
    destination.mkdir(exist_ok=True)
    artifacts = []
    for raw in sorted((run_dir / "captures").rglob("*")):
        if not raw.is_file():
            continue
        visual = raw.suffix == ".png"
        draft = {"path": raw, "role": "-".join(raw.relative_to(run_dir / "captures").with_suffix("").parts),
                 "frame_count": 1, "visual": visual}
        if visual:
            w, h, n = inspect_visual(raw)
            draft.update(image_dimensions={"width": w, "height": h}, frame_count=n)
        canonical = destination / deterministic_artifact_name(metadata, draft)
        if canonical.exists():
            if canonical.read_bytes() != raw.read_bytes():
                raise PublicationError(f"canonical collision: {canonical.name}")
        else:
            canonical.hardlink_to(raw)
        artifacts.append({**draft, "path": canonical, "aliases": [raw]})
    manifest = run_dir / "godot_proof_publication.json"
    publish_artifacts(manifest, metadata, artifacts, artifact_root=run_dir, enforce_names=True)
    if sys.platform == "darwin":
        apply_finder_tags(manifest)
        errors = verify_finder_tags(manifest)
        if errors:
            raise PublicationError("; ".join(errors))
    errors = validate_manifest(manifest)
    if errors:
        raise PublicationError("; ".join(errors))
    return manifest


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _wait_for(predicate, timeout: float, interval: float = 0.25) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Headed FL-4265 hunter-catch proof.")
    ap.add_argument("--port", type=int, default=2567)
    ap.add_argument("--server-boot-timeout", type=float, default=60.0)
    ap.add_argument("--godot-timeout", type=float, default=1200.0)
    ap.add_argument("--run-id", default="")
    ap.add_argument("--presentation-mode", choices=("FC", "CF", "CC"), default="CC",
                    help="Explicit non-FF mode; this gameplay route must not launch banned TrueFF")
    args = ap.parse_args()

    run_id = args.run_id or datetime.now(timezone.utc).strftime("fl4265_hunter_catch_%Y%m%d_%H%M%S")
    run_dir = ROOT / ".run" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    server_log = run_dir / "colyseus_server.log"
    godot_log = run_dir / "godot_client.log"
    godot_out = run_dir / "captures"
    godot_out.mkdir(parents=True, exist_ok=True)
    receipt_path = godot_out / "receipt.json"

    if _port_open(args.port):
        print(f"[hunter-catch] ERROR: port {args.port} already in use.", flush=True)
        return 2

    env = dict(os.environ)
    env["COLYSEUS_PORT"] = str(args.port)
    # The hunter MUST be enabled: every capture harness disables it, and this
    # run exists precisely because those runs can never show one.
    env.pop("ASCIICKER_DISABLE_HUNTER_BOT", None)

    scopes = [GODOT_PROJECT, COLYSEUS_DIR / "src", Path(__file__).resolve()]
    launch_identity = _runtime_source_identity(scopes)
    executable = Path(GODOT_BIN).resolve()
    build = {"kind": "direct_source_engine", "path": str(executable),
             "sha256": sha256_file(executable)}

    print("[hunter-catch] starting colyseus server on :%d ..." % args.port, flush=True)
    slog = open(server_log, "w", buffering=1)
    server = subprocess.Popen(
        ["node", "--import", "tsx", "src/index.ts"],
        cwd=str(COLYSEUS_DIR), env=env, stdout=slog, stderr=subprocess.STDOUT,
    )

    def _listening() -> bool:
        if server.poll() is not None:
            return False
        try:
            return "ASCIICKER_COLYSEUS_SPIKE_LISTENING" in server_log.read_text()
        except OSError:
            return False

    godot_rc: int | None = None
    try:
        if not _wait_for(_listening, args.server_boot_timeout):
            print("[hunter-catch] ERROR: server did not report LISTENING.", flush=True)
            try:
                print(server_log.read_text()[-2000:], flush=True)
            except OSError:
                pass
            return 3
        print("[hunter-catch] server listening. launching HEADED godot client ...", flush=True)
        cmd = [
            GODOT_BIN,
            "--path", str(GODOT_PROJECT), "--script", HARNESS, "--",
            "--colyseus-client", f"--port={args.port}",
            f"--fl4265-hunter-catch-output-dir={godot_out}",
            f"--presentation-mode={args.presentation_mode}",
            # Pin solar noon so captures never fall into night: the run is
            # about the hunter existing in frame, not about the day cycle.
            "--fl4408-day-cycle-time=0.5",
        ]
        with open(godot_log, "w", buffering=1) as glog:
            try:
                godot_rc = subprocess.run(
                    cmd, cwd=str(ROOT), stdout=glog, stderr=subprocess.STDOUT,
                    env=env, timeout=args.godot_timeout,
                ).returncode
            except subprocess.TimeoutExpired:
                print("[hunter-catch] ERROR: godot client timed out.", flush=True)
                godot_rc = 124
    finally:
        if server.poll() is None:
            server.send_signal(signal.SIGINT)
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
        slog.close()
        _persist_runtime_source_identity(receipt_path, launch_identity, scopes)

    godot_tail = ""
    if godot_log.exists():
        godot_tail = godot_log.read_text()
        for line in godot_tail.splitlines():
            if "FL4265_HUNTER_CATCH" in line or "GODOT_COLYSEUS" in line:
                print(f"[godot] {line}", flush=True)

    if not receipt_path.exists():
        print(f"[hunter-catch] FAIL: no receipt. godot rc={godot_rc}. log tail:", flush=True)
        print(godot_tail[-2000:], flush=True)
        return 1

    receipt = json.loads(receipt_path.read_text())
    hunter_ticks = 0
    try:
        for line in server_log.read_text().splitlines():
            if line.startswith(HUNTER_WITNESS_PREFIX) and HUNTER_PICKUP_ID in line:
                hunter_ticks += 1
    except OSError:
        pass
    receipt["server_hunter_steering_ticks"] = hunter_ticks
    receipt_path.write_text(json.dumps(receipt, indent="\t"))

    try:
        manifest = _publish_captures(run_dir, run_id, receipt, build)
        print(f"[hunter-catch] publication={manifest} storage=PENDING", flush=True)
    except (PublicationError, KeyError, OSError, ValueError) as error:
        print(f"[hunter-catch] publication incomplete: {error}; raw evidence preserved", flush=True)
        return 4

    captures = receipt.get("captures", [])
    print(f"[hunter-catch] board_frame={receipt.get('board_frame')} "
          f"hunter_first_frame={receipt.get('hunter_first_frame')} "
          f"captures={len(captures)} server_hunter_ticks={hunter_ticks} "
          f"m_taps={receipt.get('m_taps')}", flush=True)
    for cap in captures:
        print(f"[hunter-catch] capture tag={cap.get('tag')} path={cap.get('path')}", flush=True)

    if receipt.get("passed") and godot_rc == 0:
        print(f"[hunter-catch] PASS run={run_id}", flush=True)
        return 0
    print(f"[hunter-catch] FAIL reason={receipt.get('fail_reason')} "
          f"check_failures={receipt.get('check_failures')} godot_rc={godot_rc}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
