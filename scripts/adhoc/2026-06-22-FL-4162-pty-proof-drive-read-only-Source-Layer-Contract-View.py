# Ad hoc script: FL-4162 pty proof: drive read-only Source Layer Contract Viewer against bigbee, prove bee-body classifier trap is visible (machine guess armor;mount_body_wolf contradicted by proposed bee_body, bee sprite rendered)
# Created: 2026-06-22
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4162 pty proof for source_layer_contract_viewer.py (read-only).

Drives the real viewer through a pseudo-terminal against bigbee-0000 and proves
the bee-body classifier trap is visible on screen: the wrong machine guess
(armor;mount_body_wolf) is contradicted by the proposed role (bee_body), the
contradiction is spelled out, and an actual bee sprite frame is rendered. Also
advances the frame to prove autoplay/frame navigation works. Writes only proof
artifacts; never mutates review data.
"""
from __future__ import annotations
import json, os, pty, re, select, struct, subprocess, sys, time, fcntl, termios
from pathlib import Path

REPO = Path("/Users/r/Downloads/asciicker-Y9-2")
PIPELINE = REPO / "pipeline-v3"
VIEWER = PIPELINE / "scripts" / "source_layer_contract_viewer.py"
SM = REPO / "docs/research/ascii/semantic_maps"
SPRITES = REPO / "assets/sprites"
OUTDIR = REPO / "docs/research/ascii/verification/fl4162/2026-06-22-contract-viewer-pty"
ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")


def _strip(raw: bytes) -> str:
    return ANSI.sub(b"", raw).decode("utf-8", "replace")


def run(keys):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 160, 0, 0))
    proc = subprocess.Popen(
        [sys.executable, str(VIEWER), "bigbee-0000", "--sprites", str(SPRITES), "--sm", str(SM)],
        stdin=slave, stdout=slave, stderr=subprocess.PIPE, cwd=str(PIPELINE),
        close_fds=True, start_new_session=True)
    os.close(slave)
    buf = bytearray()

    def drain(total=1.5, idle=0.3):
        end = time.monotonic() + total
        while time.monotonic() < end:
            r, _, _ = select.select([master], [], [], idle)
            if master not in r:
                break
            try:
                chunk = os.read(master, 65536)
            except OSError:
                break
            if not chunk:
                break
            buf.extend(chunk)

    drain(total=3.0)
    for k in keys:
        try:
            os.write(master, k.encode())
        except OSError:
            break
        time.sleep(0.2)
        drain()
    try:
        proc.wait(timeout=4)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    try:
        os.close(master)
    except OSError:
        pass
    return bytes(buf)


def main() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    raw = run(["n", "n", "q"])      # advance frame twice, then quit
    stripped = _strip(raw)
    frames = [_strip(p) for p in raw.split(b"\x1b[H\x1b[2J")]
    ev = [f for f in frames if "CONTRACT" in f]
    key_frame = ev[-1] if ev else (frames[-1] if frames else stripped)
    (OUTDIR / "transcript.ansi").write_bytes(raw)
    (OUTDIR / "key_frame.log").write_text(key_frame, encoding="utf-8")
    checks = {
        "viewer_started": "SOURCE LAYER CONTRACT VIEWER" in stripped,
        "read_only_banner": "READ-ONLY" in stripped,
        "wrong_machine_guess_shown": "armor;mount_body_wolf" in stripped,
        "proposed_bee_body_shown": "bee_body" in stripped,
        "contradiction_spelled_out": "contradicted by hand" in stripped,
        "bee_sprite_rendered": "< >" in stripped,
        "role_grid_present": "ROLE GRID" in stripped,
        "frame_advanced": ("frame 2/6" in stripped or "frame 3/6" in stripped),
    }
    result = {"all_pass": all(checks.values()), "checks": checks, "outdir": str(OUTDIR)}
    (OUTDIR / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
