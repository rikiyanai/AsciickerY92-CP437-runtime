# Ad hoc script: FL-4131 regenerate proof map at current manifest hash so LOAD_MAP succeeds
# Created: 2026-06-01
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Regenerate .run/fl4131_asciiid_cdp_all_presets.a3d at the current manifest
hash. Launches asciiid --cdp, applies a preset to materialize extended cells,
saves the map (which emits sidecar with current manifest_hash), exits.

Run after manifest_hash regeneration so subsequent LOAD_MAP succeeds rather
than failing closed on stale hash. After regeneration, the FL-4131 CDP proof
chain (CAPTURE_UI_FRAME, cdp_preset_save_reopen, etc.) can run cleanly.
"""
from __future__ import annotations
import json, os, socket, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ASCIIID = REPO / ".run" / "asciiid"
OUT = REPO / ".run" / "fl4131_asciiid_cdp_all_presets.a3d"
SIDECAR = Path(str(OUT) + ".glyph_profile.json")
PORT = 47745

def call(sock, method, params="", t=30.0):
    payload = json.dumps({"id": int.from_bytes(os.urandom(2), "big"), "method": method, "params": params}) + "\n"
    sock.sendall(payload.encode())
    sock.settimeout(t)
    buf = b""
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            return buf.decode(errors="replace")
        buf += chunk
        if b"\n" in chunk:
            break
    return buf.decode(errors="replace")

def main():
    # Pre-flight: confirm no stale file
    if OUT.exists() or SIDECAR.exists():
        print(f"[regen] removing stale {OUT}", file=sys.stderr)
        OUT.unlink(missing_ok=True)
        SIDECAR.unlink(missing_ok=True)
    proc = subprocess.Popen(
        [str(ASCIIID), "--cdp", str(PORT)],
        cwd=str(REPO),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.time() + 30
        sock = None
        while time.time() < deadline:
            try:
                sock = socket.create_connection(("127.0.0.1", PORT), timeout=1.0)
                break
            except OSError:
                time.sleep(0.25)
        if sock is None:
            print("[regen] could not connect", file=sys.stderr)
            return 2
        print("[regen] connected", file=sys.stderr)
        # Apply preset 11 (SNOW Crystal — admitted)
        r = call(sock, "FL4131_APPLY_EXTENDED_PRESET", "1 11", t=15)
        print(f"[regen] APPLY -> {r.strip()[:160]}", file=sys.stderr)
        # Apply a couple more to get diverse content
        r = call(sock, "FL4131_APPLY_EXTENDED_PRESET", "2 0", t=15)
        print(f"[regen] APPLY2 -> {r.strip()[:160]}", file=sys.stderr)
        # Save
        r = call(sock, "SAVE_MAP", str(OUT), t=60)
        print(f"[regen] SAVE_MAP -> {r.strip()[:160]}", file=sys.stderr)
        try:
            call(sock, "QUIT", "", t=2)
        except Exception:
            pass
        sock.close()
    finally:
        try:
            proc.terminate(); proc.wait(timeout=5)
        except Exception:
            proc.kill()
    # Verify outputs
    if not OUT.exists():
        print(f"[regen] ERROR: {OUT} not written", file=sys.stderr)
        return 3
    if not SIDECAR.exists():
        print(f"[regen] ERROR: {SIDECAR} not written", file=sys.stderr)
        return 4
    sidecar = json.loads(SIDECAR.read_text())
    h = sidecar.get("glyph_manifest_hash", "")
    print(f"[regen] OK map={OUT.stat().st_size}B sidecar manifest_hash={h}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
