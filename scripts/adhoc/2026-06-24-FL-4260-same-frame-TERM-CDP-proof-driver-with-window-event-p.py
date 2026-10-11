# Ad hoc script: FL-4260 same-frame TERM++ CDP proof driver with window event pulse
# Created: 2026-06-24
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def pulse_window(pid: int) -> None:
    if pid <= 0:
        return
    subprocess.run([
        "osascript",
        "-e", f'tell application "System Events" to set frontmost of (first process whose unix id is {pid}) to true',
        "-e", 'tell application "System Events" to key code 53',
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def recv_line(sock: socket.socket, timeout_s: float, pid: int) -> str:
    deadline = time.time() + timeout_s
    buf = b""
    while time.time() < deadline:
        pulse_window(pid)
        sock.settimeout(min(1.0, max(0.05, deadline - time.time())))
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            continue
        if not chunk:
            raise RuntimeError("CDP socket closed")
        buf += chunk
        if b"\n" in buf:
            line, _rest = buf.split(b"\n", 1)
            return line.decode("utf-8", errors="replace").strip()
    raise TimeoutError("timed out waiting for CDP response")


def send_command(sock: socket.socket, seq: int, method: str, params: str, pid: int, timeout_s: float) -> dict:
    payload = json.dumps({"id": seq, "method": method, "params": params}) + "\n"
    sock.sendall(payload.encode("utf-8"))
    line = recv_line(sock, timeout_s, pid)
    try:
        msg = json.loads(line)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"bad CDP JSON line: {line!r}") from exc
    if msg.get("id") != seq:
        raise RuntimeError(f"unexpected CDP id: expected {seq}, got {msg.get('id')}: {msg}")
    return msg


def wait_files(paths: list[Path], pid: int, timeout_s: float) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        pulse_window(pid)
        if all(p.exists() and p.stat().st_size > 0 for p in paths):
            return
        time.sleep(0.25)
    missing = [str(p) for p in paths if not (p.exists() and p.stat().st_size > 0)]
    raise TimeoutError(f"timed out waiting for files: {missing}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact-dir", required=True)
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    art = Path(args.artifact_dir)
    art.mkdir(parents=True, exist_ok=True)
    before_png = art / "before.termpp.png"
    before_buf = art / "before.termpp_buffer.jsonl"
    before_bridge = art / "before.bridge_cells.jsonl"
    after_png = art / "after_scoring.termpp.png"
    after_buf = art / "after_scoring.termpp_buffer.jsonl"
    after_bridge = art / "after_scoring.bridge_cells.jsonl"

    transcript = []
    seq = 1
    sock = socket.create_connection((args.host, args.port), timeout=30.0)
    try:
        def run(method: str, params: str = "", timeout_s: float = 90.0):
            nonlocal seq
            t0 = time.time()
            msg = send_command(sock, seq, method, params, args.pid, timeout_s)
            transcript.append({
                "method": method,
                "params": params,
                "ms": int((time.time() - t0) * 1000),
                "result": str(msg.get("result", msg))[:2000],
            })
            seq += 1
            return msg

        run("LOAD_MAP", "assets/a3d/fl4260_fixture_all_materials.a3d", 120.0)
        run("SET_TERMPP_RUNTIME_HARRI_RESOLVE", "1")
        run("FL4260_SET_RENDER_MODE", "1")
        run("OPEN_TERMPP_CURRENT_VIEW", "", 120.0)
        time.sleep(2.5)
        run("FL4260_APPLY_PALETTE_STARTER", "1")
        run("FL4260_POOL_ACTION", "1 select_all")
        run("FL4260_ROLE_BUCKET_AUTOFILL", "1")
        run("FL4260_RESET_SCORING", "1")
        run("CAPTURE_TERMPP_FRAME_WITH_BUFFER", f"{before_png} {before_buf} {before_bridge}", 120.0)
        wait_files([before_png, before_buf, before_bridge], args.pid, 120.0)
        run("FL4260_SET_PROFILE_SCORING", "1 9 0 0 0 0 9")
        time.sleep(0.75)
        run("CAPTURE_TERMPP_FRAME_WITH_BUFFER", f"{after_png} {after_buf} {after_bridge}", 120.0)
        wait_files([after_png, after_buf, after_bridge], args.pid, 120.0)
    finally:
        sock.close()

    (art / "cdp_transcript.json").write_text(json.dumps(transcript, indent=2), encoding="utf-8")
    print(json.dumps({"artifact_dir": str(art), "commands": len(transcript), "before_png": str(before_png), "after_png": str(after_png)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
