# Ad hoc script: FL-4131 native gameplay proof - .run/game loads map with coverage-populated extended glyphs
# Created: 2026-05-31
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4131 native gameplay proof driver.

Step 1: Launch asciiid headless, load test map, apply extended preset, save
(this writes the .a3d + .glyph_profile.json sidecar with coverage now
populated by the editor edit path).
Step 2: Launch .run/game with that map. Wait for render.
Step 3: Capture desktop screencapture (game window visible) so we can confirm
the engine native render path renders the extended glyphs without the red
'!' fail-closed diagnostic.
"""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ASCIIID = REPO / ".run" / "asciiid"
GAME = REPO / ".run" / "game"
DEFAULT_MAP = REPO / ".run" / "fl4131_native_proof.a3d"
SEED_MAP = REPO / ".run" / "fl4131_asciiid_cdp_all_presets.a3d"


class CdpClient:
    def __init__(self, port: int, deadline_s: float = 30.0):
        self.next_id = 1
        self.buf = ""
        end = time.time() + deadline_s
        last_err = None
        while time.time() < end:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=1.0)
                self.sock.settimeout(None)
                return
            except OSError as e:
                last_err = e
                time.sleep(0.25)
        raise RuntimeError(f"CDP {port} not ready: {last_err}")

    def call(self, method, params="", timeout_s=30.0):
        i = self.next_id
        self.next_id += 1
        msg = json.dumps({"id": i, "method": method, "params": params}) + "\n"
        self.sock.sendall(msg.encode("utf-8"))
        end = time.time() + timeout_s
        while time.time() < end:
            self.sock.settimeout(max(0.1, end - time.time()))
            try:
                chunk = self.sock.recv(65536).decode("utf-8", errors="replace")
            except socket.timeout:
                continue
            if not chunk:
                raise RuntimeError("CDP closed")
            self.buf += chunk
            while "\n" in self.buf:
                line, self.buf = self.buf.split("\n", 1)
                if not line.strip():
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if parsed.get("id") == i:
                    return str(parsed.get("result", ""))
        raise TimeoutError(method)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def screencap(path: Path):
    subprocess.run(["screencapture", "-x", str(path)], check=True, timeout=10)


def step1_save_map(out_map: Path, port: int, mat_id: int, preset_idx: int):
    """Run asciiid headless, load seed, apply preset, save to out_map."""
    print(f"[step1] launching asciiid for save", file=sys.stderr)
    p = subprocess.Popen(
        [str(ASCIIID), "--cdp", str(port)],
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        c = CdpClient(port)
        c.call("LOAD_MAP", str(SEED_MAP), timeout_s=60.0)
        c.call("FL4131_APPLY_EXTENDED_PRESET", f"{mat_id} {preset_idx}", timeout_s=15.0)
        save_resp = c.call("SAVE_MAP", str(out_map), timeout_s=60.0)
        print(f"[step1] SAVE_MAP -> {save_resp.strip()[:200]}", file=sys.stderr)
        try:
            c.call("QUIT", "", timeout_s=2.0)
        except Exception:
            pass
        c.close()
    finally:
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            p.kill()


def step2_run_game_and_capture(map_path: Path, out_dir: Path, dwell_s: float = 8.0):
    """Launch .run/game with map_path, wait dwell_s, screencapture.

    Sets .run/auto-shot-on-first-frame.flag so the main menu auto-starts the
    level load (otherwise the game sits at the main menu waiting for a click).
    """
    print(f"[step2] launching {GAME} {map_path}", file=sys.stderr)
    log = out_dir / "game_stdout.log"
    log_f = log.open("w")
    flag_path = REPO / ".run" / "auto-shot-on-first-frame.flag"
    flag_path.touch()
    print(f"[step2] auto-shot flag set: {flag_path}", file=sys.stderr)
    p = subprocess.Popen(
        [str(GAME), str(map_path)],
        cwd=str(REPO),
        stdout=log_f,
        stderr=subprocess.STDOUT,
    )
    try:
        time.sleep(dwell_s)
        cap_path = out_dir / "screen_native_game.png"
        screencap(cap_path)
        print(f"[step2] screen captured: {cap_path.stat().st_size} bytes", file=sys.stderr)
        # Sample log
        time.sleep(0.3)
        log_f.flush()
        return cap_path
    finally:
        try:
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            p.kill()
        log_f.close()
        try:
            flag_path.unlink()
        except FileNotFoundError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / ".run" / "fl4131_native_gameplay_proof"))
    ap.add_argument("--cdp-port", type=int, default=47734)
    ap.add_argument("--material-id", type=int, default=1)
    ap.add_argument("--preset-index", type=int, default=11)  # SNOW Crystal
    ap.add_argument("--map-out", default=str(DEFAULT_MAP))
    args = ap.parse_args()

    out_dir = Path(args.out)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    map_out = Path(args.map_out)

    step1_save_map(map_out, args.cdp_port, args.material_id, args.preset_index)
    if not map_out.exists():
        print(f"FAIL: saved map missing at {map_out}", file=sys.stderr)
        return 2

    sidecar = Path(str(map_out) + ".glyph_profile.json")
    sidecar_exists = sidecar.exists()
    sidecar_bytes = sidecar.stat().st_size if sidecar_exists else 0
    print(f"[main] map: {map_out.stat().st_size} bytes, sidecar exists: {sidecar_exists} ({sidecar_bytes} bytes)", file=sys.stderr)

    cap_path = step2_run_game_and_capture(map_out, out_dir)

    receipt = {
        "schema": "fl4131_native_gameplay_proof.v1",
        "game_binary": str(GAME),
        "game_binary_mtime": GAME.stat().st_mtime,
        "asciiid_binary_mtime": ASCIIID.stat().st_mtime,
        "saved_map": str(map_out),
        "saved_map_bytes": map_out.stat().st_size,
        "saved_sidecar": str(sidecar),
        "saved_sidecar_exists": sidecar_exists,
        "saved_sidecar_bytes": sidecar_bytes,
        "preset_applied_index": args.preset_index,
        "material_id": args.material_id,
        "screen_native_game_png": str(cap_path),
        "screen_native_game_bytes": cap_path.stat().st_size,
        "game_stdout_log": str(out_dir / "game_stdout.log"),
        "commit_under_test": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO)
        ).decode().strip(),
        "verdict": "PASS_NEEDS_VISUAL_INSPECTION",
        "interpretation": (
            "Manual visual inspection of screen_native_game.png required: confirm the "
            ".run/game window renders extended-glyph cells (display glyphs from coverage) "
            "and does NOT show the red '!' diagnostic for the applied preset's material cells."
        ),
    }
    (out_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"[main] receipt: {out_dir/'receipt.json'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
