#!/usr/bin/env python3
# =============================================================
# run_fl4359_card_chroma_pose_capture.py
# =============================================================
# Captures the FL-4359 F4 card-chroma compare (BASELINE / CAPTURED-LIT /
# HEIGHT-HUE) at ONE exact camera pose — three X-dumps + PNGs total.
#
# Default pose is dump 1783653391_144386 (operator-selected card-wall view).
# Each arm is a fresh Godot launch with --fl4359-card-chroma-mode=N so the
# shader loads that mode from the start (no stale-build / F4-miss trap).
#
# This is a DIAGNOSTIC package, not closure proof. Manual visual review of
# the three normal final.png files answers: ground changed? wall changed?
#
# Usage:
#   python3 scripts/run_fl4359_card_chroma_pose_capture.py
#   python3 scripts/run_fl4359_card_chroma_pose_capture.py \
#       --pose-dump .run/final_render_cell_dump/1783653391_144386 \
#       --out .run/fl4359_card_chroma_pose/<stamp>_3cap
# =============================================================
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLYSEUS_DIR = ROOT / "colyseus_server"
GODOT_PROJECT = ROOT / "godot_project"
GODOT_BIN = os.environ.get("GODOT_BIN", "/opt/homebrew/bin/godot")
DRIVER = ROOT / "scripts" / "adhoc" / "fl4359_external_movement_driver.gd"
DEFAULT_POSE_DUMP = ROOT / ".run" / "final_render_cell_dump" / "1783653391_144386"

# Mode enum must match ASCIICompositorEffect / resolve_card_chroma_mode().
# These names appear in arm folder names and the package README.
MODE_NAMES = {
    0: "BASELINE",
    1: "CAPTURED-LIT",
    2: "HEIGHT-HUE",
}

# Each chroma mode is captured twice: highlighter off, then highlighter on.
# "normal" is the primary ground-vs-wall compare arm; "highlight" is the
# diagnostic companion that paints card-writer cells so ownership is visible.
ARM_KINDS = ("normal", "highlight")


def _port_open(port: int) -> bool:
    """Return True if something is already listening on localhost:port.

    We refuse to start a second Colyseus on a busy port — that would mix
    two worlds and contaminate the dump identity.
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _timestamp() -> str:
    """Local wall-clock stamp used in the default package folder name."""
    return time.strftime("%Y%m%d_%H%M%S")


def _arm_dir_name(mode: int) -> str:
    """Build the per-mode folder name."""
    return f"mode_{mode}_{MODE_NAMES[mode]}"


def _load_pose(pose_dump: Path) -> dict:
    """Read the pose contract from an X-dump metadata.json.

    Pose contract (operator clarification): camera world position, yaw/pitch
    look direction, FOV/zoom, and fullscreen presentation size. Nothing else.
    """
    meta_path = pose_dump / "metadata.json"
    if not meta_path.is_file():
        raise SystemExit(f"pose dump metadata missing: {meta_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    cam = meta.get("camera_world") or (meta.get("camera") or {}).get("world") or {}
    player = meta.get("player_world") or {}
    yaw = float(meta.get("camera_yaw", (meta.get("camera") or {}).get("yaw", 0.0)))
    pitch = float(meta.get("camera_pitch", (meta.get("camera") or {}).get("pitch", 0.0)))
    if not all(k in cam for k in ("x", "y", "z")):
        raise SystemExit(f"pose dump missing camera_world xyz: {meta_path}")
    contract = meta.get("pixel_camera_capture_contract") or {}
    # Zoom: prefer the PixelCamera contract FOV (source dump used 58°).
    fov = float(contract.get("camera_fov") or meta.get("camera_fov") or 58.0)
    # Fullscreen presentation: source dump was 3600x2252 on a 3600-wide display.
    pres = contract.get("presentation_size") or []
    if isinstance(pres, list) and len(pres) == 2:
        res_w, res_h = int(pres[0]), int(pres[1])
    else:
        pcs = meta.get("pixel_camera_presentation_size") or {}
        res_w = int(pcs.get("w") or (meta.get("display") or {}).get("window_size", {}).get("w") or 3600)
        res_h = int(pcs.get("h") or (meta.get("display") or {}).get("window_size", {}).get("h") or 2252)
    return {
        "source_dump": str(pose_dump),
        "camera": {"x": float(cam["x"]), "y": float(cam["y"]), "z": float(cam["z"])},
        "player": {
            "x": float(player.get("x", cam["x"])),
            "y": float(player.get("y", 0.0)),
            "z": float(player.get("z", cam["z"])),
        },
        "yaw": yaw,
        "pitch": pitch,
        "fov": fov,
        "presentation_w": res_w,
        "presentation_h": res_h,
    }


def _angle_delta(a: float, b: float) -> float:
    """Smallest absolute difference between two angles in radians."""
    import math

    d = (a - b + math.pi) % (2.0 * math.pi) - math.pi
    return abs(d)


def _pose_matches(dump_meta: dict, pose: dict, *, yaw_tol: float = 0.05, pitch_tol: float = 0.05, pos_tol: float = 0.5) -> tuple[bool, str]:
    """Fail closed if dump camera angle/position/zoom/fullscreen drifted."""
    dyaw = float(dump_meta.get("camera_yaw", 999.0))
    dpitch = float(dump_meta.get("camera_pitch", 999.0))
    dcam = dump_meta.get("camera_world") or {}
    if _angle_delta(dyaw, pose["yaw"]) > yaw_tol:
        return False, f"yaw mismatch: dump={dyaw} want={pose['yaw']}"
    if abs(dpitch - pose["pitch"]) > pitch_tol:
        return False, f"pitch mismatch: dump={dpitch} want={pose['pitch']}"
    for axis in ("x", "y", "z"):
        if abs(float(dcam.get(axis, 1e9)) - float(pose["camera"][axis])) > pos_tol:
            return False, f"camera {axis} mismatch: dump={dcam.get(axis)} want={pose['camera'][axis]}"
    # Fullscreen is part of the pose contract. 1280×800 is the project.godot
    # override and is ALWAYS a fail (wrong zoom vs source ~3600×2252).
    contract = dump_meta.get("pixel_camera_capture_contract") or {}
    pres = contract.get("presentation_size") or []
    if not (isinstance(pres, list) and len(pres) >= 2):
        pcs = dump_meta.get("pixel_camera_presentation_size") or {}
        if isinstance(pcs, dict) and pcs.get("w") and pcs.get("h"):
            pres = [pcs["w"], pcs["h"]]
        else:
            return False, "presentation_size missing"
    pw, ph = int(pres[0]), int(pres[1])
    want_w = int(pose["presentation_w"])
    want_h = int(pose["presentation_h"])
    # Accept within 10% of the source dump presentation (native screen).
    if pw < int(want_w * 0.90) or ph < int(want_h * 0.85):
        return False, f"not fullscreen: dump={pw}x{ph} want~={want_w}x{want_h}"
    if pw <= 1280 and ph <= 800:
        return False, f"project override window stuck: dump={pw}x{ph}"
    return True, "ok"


def _wait_listening(server_log: Path, server: subprocess.Popen, timeout: float) -> bool:
    """Block until Colyseus prints its listening banner, or time out / die."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server.poll() is not None:
            return False
        try:
            if "ASCIICKER_COLYSEUS_SPIKE_LISTENING" in server_log.read_text(encoding="utf-8"):
                return True
        except OSError:
            pass
        time.sleep(0.25)
    return False


def _find_newest_dump_after(before_mtime: float) -> Path | None:
    """Locate the X-dump folder written by --single-frame-cell-dump.

    We only accept dumps whose metadata.json mtime is AFTER the Godot launch
    started, so a previous arm's dump cannot be mistaken for this arm's dump.
    """
    root = ROOT / ".run" / "final_render_cell_dump"
    if not root.is_dir():
        return None
    newest: Path | None = None
    newest_m = before_mtime
    for child in root.iterdir():
        if not child.is_dir():
            continue
        meta = child / "metadata.json"
        png = child / "final.png"
        if not meta.is_file() or not png.is_file():
            continue
        m = meta.stat().st_mtime
        if m > newest_m:
            newest_m = m
            newest = child
    return newest


def _capture_arm(
    mode: int,
    pose: dict,
    out_dir: Path,
    port: int,
    boot_timeout: float,
    godot_timeout: float,
) -> dict:
    """One Godot launch: pin chroma mode and exact pose, then write an X-dump.

    Parameters
    ----------
    mode:
        Card-chroma mode integer 0/1/2 (BASELINE / CAPTURED-LIT / HEIGHT-HUE).
    pose:
        Pose contract from _load_pose (camera, yaw/pitch, fov, presentation).
    out_dir:
        Package root; this arm writes under out_dir / mode_N_NAME.
    port:
        Colyseus listen port for this arm (unique per arm so stale servers
        cannot collide).
    boot_timeout / godot_timeout:
        Seconds to wait for Colyseus LISTENING and for Godot to finish.

    Returns a result dict with ok=True only when ALL of: Godot exit 0, dump
    present, chroma mode matches, and pose/fullscreen are valid.
    """
    name = MODE_NAMES[mode]
    arm_dir = out_dir / _arm_dir_name(mode)
    arm_dir.mkdir(parents=True, exist_ok=True)
    server_log = arm_dir / "colyseus_server.log"
    godot_log = arm_dir / "godot_client.log"
    receipt_path = arm_dir / "receipt.json"

    if _port_open(port):
        return {
            "mode": mode,
            "name": name,
            "ok": False,
            "error": f"port {port} in use",
        }

    env = dict(os.environ)
    env["COLYSEUS_PORT"] = str(port)
    env.pop("ASCIICKER_ENABLE_DOGFIGHT_BOT", None)
    env["ASCIICKER_DISABLE_HUNTER_BOT"] = "1"  # NO mount-triggered hunter either, same reason
    # Spawn near the dump player so the world loads the right region.
    env["PLAYER_SPAWN_X"] = str(int(round(pose["player"]["x"])))
    env["PLAYER_SPAWN_Z"] = str(int(round(pose["player"]["z"])))

    slog = server_log.open("w", encoding="utf-8")
    server = subprocess.Popen(
        ["node", "--import", "tsx", "src/index.ts"],
        cwd=str(COLYSEUS_DIR),
        env=env,
        stdout=slog,
        stderr=subprocess.STDOUT,
    )
    result: dict = {
        "mode": mode,
        "name": name,
        "ok": False,
        "out_dir": str(arm_dir),
    }
    dump_before = time.time()
    try:
        if not _wait_listening(server_log, server, boot_timeout):
            result["error"] = "colyseus did not report LISTENING"
            return result

        cam = pose["camera"]
        player = pose["player"]
        # Pose contract: position + yaw/pitch + FOV + FULLSCREEN presentation.
        # Do NOT pass Godot --resolution — it fights exclusive fullscreen and
        # leaves the dump stuck at project.godot's 1280×800 (wrong zoom).
        # World/driver force native screen size; runner fails closed if not.
        # F3 debug HUD is forced OFF via --fl4359-hide-debug-hud so the PNG
        # is a clean visual surface (no overlay text contaminating deltas).
        cmd = [
            GODOT_BIN,
            "--path",
            str(GODOT_PROJECT),
            "--fullscreen",
            "--script",
            str(DRIVER),
            "--",
            "--colyseus-client",
            f"--port={port}",
            "--colyseus-skip-room-leave",
            "--fl4270-force-perspective",
            f"--fl4359-capture-output-dir={arm_dir}",
            "--fl4359-headed-camera-mode=perspective",
            "--fl4359-capture-window-mode=fullscreen",
            "--fl4359-force-fullscreen",
            "--fl4359-hide-debug-hud",
            "--fl4408-single-frame=true",
            f"--fl4408-single-frame-label=chroma_{mode}_{name}",
            "--fl4408-exact-camera-projection=perspective",
            f"--fl4408-exact-camera-position={cam['x']},{cam['y']},{cam['z']}",
            f"--fl4408-exact-camera-yaw={pose['yaw']}",
            f"--fl4408-exact-camera-pitch={pose['pitch']}",
            f"--fl4408-exact-camera-fov={pose['fov']}",
            f"--fl4408-exact-player-position={player['x']},{player['y']},{player['z']}",
            f"--fl4359-card-chroma-mode={mode}",
            "--single-frame-cell-dump",
        ]
        with godot_log.open("w", encoding="utf-8") as glog:
            try:
                rc = subprocess.run(
                    cmd,
                    cwd=str(ROOT),
                    stdout=glog,
                    stderr=subprocess.STDOUT,
                    timeout=godot_timeout,
                ).returncode
            except subprocess.TimeoutExpired:
                rc = 124
        result["godot_rc"] = rc

        # Prefer the dump written during this capture (mtime after launch).
        dump = _find_newest_dump_after(dump_before - 1.0)
        if dump is not None:
            # Copy/symlink identity into arm_dir for the package index.
            link = arm_dir / "x_dump"
            if not link.exists():
                try:
                    link.symlink_to(dump)
                except OSError:
                    (arm_dir / "x_dump_path.txt").write_text(str(dump) + "\n", encoding="utf-8")
            result["x_dump"] = str(dump)
            meta_path = dump / "metadata.json"
            if meta_path.is_file():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                result["dump_chroma_mode"] = meta.get("fl4359_card_chroma_mode")
                result["dump_chroma_name"] = meta.get("fl4359_card_chroma_mode_name")
                result["dump_camera"] = meta.get("camera_world")
                result["dump_yaw"] = meta.get("camera_yaw")
                result["dump_pitch"] = meta.get("camera_pitch")
                result["dump_fov"] = (meta.get("pixel_camera_capture_contract") or {}).get("camera_fov")
                result["dump_presentation"] = (meta.get("pixel_camera_capture_contract") or {}).get(
                    "presentation_size"
                )
                pose_ok, pose_reason = _pose_matches(meta, pose)
                result["pose_match"] = pose_ok
                result["pose_match_reason"] = pose_reason

                chroma_ok = result.get("dump_chroma_mode") == mode
                result["ok"] = (
                    rc == 0
                    and chroma_ok
                    and (dump / "final.png").is_file()
                    and pose_ok
                )
                if not chroma_ok:
                    result["error"] = (
                        f"dump chroma mode mismatch: expected {mode}, "
                        f"got {result.get('dump_chroma_mode')} "
                        f"({result.get('dump_chroma_name')})"
                    )
                elif not pose_ok:
                    result["error"] = f"pose contract failed: {pose_reason}"
            else:
                result["error"] = "x_dump missing metadata.json"
        else:
            result["error"] = "no new final_render_cell_dump after capture"
            # Still ok-ish if screenshot landed.
            shots = list(arm_dir.glob("chroma_*.png"))
            if shots and rc == 0:
                result["screenshot"] = str(shots[0])
                result["ok"] = False
                result["error"] = "screenshot ok but X-dump missing"

        if receipt_path.is_file():
            try:
                result["receipt"] = json.loads(receipt_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass
        return result
    finally:
        if server.poll() is None:
            server.send_signal(signal.SIGINT)
            try:
                server.wait(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
        slog.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="FL-4359 same-pose 3-cap card chroma X-dump package"
    )
    parser.add_argument(
        "--pose-dump",
        type=Path,
        default=DEFAULT_POSE_DUMP,
        help="X-dump folder whose camera/player pose to replay",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Package output directory (default .run/fl4359_card_chroma_pose/<stamp>_3cap)",
    )
    parser.add_argument("--port", type=int, default=2591)
    parser.add_argument("--server-boot-timeout", type=float, default=45.0)
    # Fullscreen X-dump writes a large cells.jsonl; keep a bounded timeout.
    parser.add_argument("--godot-timeout", type=float, default=600.0)
    parser.add_argument(
        "--modes",
        default="0,1,2",
        help="Comma-separated chroma modes to capture (default all three)",
    )
    args = parser.parse_args()

    pose_dump = args.pose_dump
    if not pose_dump.is_absolute():
        pose_dump = (ROOT / pose_dump).resolve()
    pose = _load_pose(pose_dump)

    out_dir = args.out
    if out_dir is None:
        out_dir = ROOT / ".run" / "fl4359_card_chroma_pose" / f"{_timestamp()}_3cap"
    if not out_dir.is_absolute():
        out_dir = (ROOT / out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    modes = [int(x.strip()) for x in args.modes.split(",") if x.strip() != ""]
    for m in modes:
        if m not in MODE_NAMES:
            raise SystemExit(f"invalid mode {m}; expected 0/1/2")

    package = {
        "fl": "FL-4359",
        "kind": "card_chroma_same_pose_3cap_package",
        "pose": pose,
        "out_dir": str(out_dir),
        "arms": [],
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (out_dir / "pose.json").write_text(json.dumps(pose, indent=2) + "\n", encoding="utf-8")

    print(f"[fl4359-chroma] pose from {pose_dump}", flush=True)
    print(
        f"[fl4359-chroma] camera=({pose['camera']['x']:.3f},{pose['camera']['y']:.3f},{pose['camera']['z']:.3f}) "
        f"yaw={pose['yaw']:.6f} pitch={pose['pitch']:.6f} fov={pose['fov']} "
        f"fullscreen={pose['presentation_w']}x{pose['presentation_h']}",
        flush=True,
    )
    print(f"[fl4359-chroma] out={out_dir}", flush=True)
    print(
        f"[fl4359-chroma] plan={len(modes)} modes = {len(modes)} captures",
        flush=True,
    )

    # Kill stale Godot/Colyseus that would steal the port or contaminate dumps.
    # Preserve nothing here — this package owns the local ports it uses.
    subprocess.run(["pkill", "-f", "launch_fl4359_manual_play"], check=False)
    subprocess.run(["pkill", "-f", "/opt/homebrew/bin/godot.*FL4359_probe"], check=False)
    subprocess.run(["pkill", "-f", "colyseus_server"], check=False)
    # Also clear any leftover node tsx servers on our port range.
    subprocess.run(["pkill", "-f", "tsx src/index.ts"], check=False)
    time.sleep(1.5)

    all_ok = True
    arm_index = 0
    for mode in modes:
        port = args.port + arm_index
        arm_index += 1
        arm_label = _arm_dir_name(mode)
        print(f"[fl4359-chroma] capturing {arm_label} on :{port}", flush=True)
        row = _capture_arm(
            mode=mode,
            pose=pose,
            out_dir=out_dir,
            port=port,
            boot_timeout=args.server_boot_timeout,
            godot_timeout=args.godot_timeout,
        )
        package["arms"].append(row)
        status = "OK" if row.get("ok") else f"FAIL ({row.get('error')})"
        print(
            f"[fl4359-chroma] {arm_label} -> {status} "
            f"dump={row.get('x_dump', '')} "
            f"chroma={row.get('dump_chroma_name')} "
            f"pres={row.get('dump_presentation')} "
            f"pose={row.get('pose_match_reason', '')}",
            flush=True,
        )
        if not row.get("ok"):
            all_ok = False

    package["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    package["pass"] = all_ok
    # Keep a backward-compat "modes" mirror for older readers that expect it;
    # the owned list is "arms".
    package["modes"] = package["arms"]
    (out_dir / "package.json").write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")

    # Operator-facing index: all three PNG paths with presentation.
    lines = [
        "# FL-4359 card chroma same-pose 3-cap package",
        "",
        "Three F4 card-chroma modes at one camera pose.",
        "Same camera pose, FOV 58, fullscreen presentation, F3 HUD off.",
        "",
        f"Pose dump: `{pose['source_dump']}`",
        f"Camera: `{pose['camera']}` yaw={pose['yaw']} pitch={pose['pitch']} fov={pose['fov']}",
        f"Presentation want: `{pose['presentation_w']}x{pose['presentation_h']}`",
        "",
        "| Arm | Mode | X-dump | final.png | presentation | pose_match | ok |",
        "|-----|------|--------|-----------|--------------|------------|----|",
    ]
    for row in package["arms"]:
        dump = row.get("x_dump", "")
        png = f"{dump}/final.png" if dump else ""
        pres = row.get("dump_presentation")
        if isinstance(pres, list) and len(pres) >= 2:
            pres_s = f"{pres[0]}x{pres[1]}"
        else:
            pres_s = str(pres or "")
        lines.append(
            f"| `{_arm_dir_name(int(row.get('mode', -1)))}` "
            f"| {row.get('mode')} {row.get('name')} "
            f"| `{dump}` | `{png}` | {pres_s} "
            f"| {row.get('pose_match')} | {row.get('ok')} |"
        )
    lines.append("")
    lines.append("## Final PNG paths (all 3)")
    lines.append("")
    for row in package["arms"]:
        dump = row.get("x_dump", "")
        arm = _arm_dir_name(int(row.get("mode", -1)))
        if dump:
            lines.append(f"- `{arm}`: `{dump}/final.png`")
        else:
            lines.append(f"- `{arm}`: MISSING")
    lines.append("")
    lines.append("## Review")
    lines.append("")
    lines.append("Open the three final.png files. For each non-baseline mode ask:")
    lines.append("- Did flat ground change?")
    lines.append("- Did the card wall change?")
    lines.append("")
    lines.append(
        "Ground changed + wall identical => wall is not OWNER_TERRAIN paint "
        "(sky/other writer). Wall changed in HEIGHT-HUE => color axis moves, shape remains."
    )
    lines.append("")
    lines.append("Do not claim closure from this package alone.")
    (out_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[fl4359-chroma] package {'PASS' if all_ok else 'FAIL'} -> {out_dir / 'package.json'}", flush=True)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
