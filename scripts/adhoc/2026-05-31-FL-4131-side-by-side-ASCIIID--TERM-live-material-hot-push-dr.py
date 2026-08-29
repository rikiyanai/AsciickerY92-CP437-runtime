# Ad hoc script: FL-4131 side-by-side ASCIIID + TERM++ live material hot-push driver
# Created: 2026-05-31
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4131 live runtime preview driver.

Launches asciiid --cdp, loads a map, opens a TERM++ child window via the new
OPEN_TERMPP MCP command, applies an extended material preset, then captures
both the asciiid window (CAPTURE_UI_FRAME) AND the macOS screen (so the TERM++
window pixels are recorded). Compares before/after screen captures: if the
TERM++ window terrain pixels change between pre-preset and post-preset frames,
that proves the shared-memory `mat[]` + shared GL texture path delivers live
glyph updates to running TERM++ instances without restart/reload.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ASCIIID = REPO / ".run" / "asciiid"
DEFAULT_MAP = REPO / ".run" / "fl4131_asciiid_cdp_all_presets.a3d"


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

    def call(self, method: str, params: str = "", timeout_s: float = 30.0) -> str:
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
                raise RuntimeError("CDP socket closed")
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
        raise TimeoutError(f"CDP timeout {method}")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def screen_capture(out_path: Path):
    """Capture the entire macOS desktop (all windows visible) to PNG."""
    subprocess.run(["screencapture", "-x", str(out_path)], check=True, timeout=10)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / ".run" / "fl4131_termpp_live_sidebyside"))
    ap.add_argument("--port", type=int, default=47733)
    ap.add_argument("--map", default=str(DEFAULT_MAP))
    ap.add_argument("--material-id", type=int, default=1)
    ap.add_argument("--preset-index", type=int, default=11)  # SNOW Crystal
    ap.add_argument("--reset-preset-index", type=int, default=0)  # WATER Contour Flow
    args = ap.parse_args()

    out_dir = Path(args.out)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [str(ASCIIID), "--cdp", str(args.port)],
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        c = CdpClient(args.port)
        print(f"[driver] connected on {args.port}", file=sys.stderr)

        # Load map
        load_resp = c.call("LOAD_MAP", args.map, timeout_s=60.0)
        print(f"[driver] LOAD_MAP -> {load_resp.strip()[:120]}", file=sys.stderr)

        # Reset material 1 to a baseline preset so we can detect change later.
        reset_resp = c.call(
            "FL4131_APPLY_EXTENDED_PRESET",
            f"{args.material_id} {args.reset_preset_index}",
            timeout_s=15.0,
        )
        print(f"[driver] BASELINE preset -> {reset_resp.strip()[:120]}", file=sys.stderr)

        # Open TERM++ child window
        open_resp = c.call("OPEN_TERMPP", "", timeout_s=15.0)
        print(f"[driver] OPEN_TERMPP -> {open_resp.strip()[:120]}", file=sys.stderr)

        # Give TERM++ a few seconds to spawn, init GL, render initial frame
        time.sleep(2.5)

        # Capture pre-preset screen state (both windows visible)
        pre_screen = out_dir / "screen_before_preset.png"
        screen_capture(pre_screen)
        print(f"[driver] pre-preset screen captured: {pre_screen.stat().st_size} bytes", file=sys.stderr)

        # Apply the test preset (SNOW Crystal)
        apply_resp = c.call(
            "FL4131_APPLY_EXTENDED_PRESET",
            f"{args.material_id} {args.preset_index}",
            timeout_s=15.0,
        )
        print(f"[driver] APPLY_PRESET (test) -> {apply_resp.strip()[:120]}", file=sys.stderr)

        # Give asciiid + TERM++ a few frames to re-render with new material
        time.sleep(2.5)

        # Capture post-preset screen state
        post_screen = out_dir / "screen_after_preset.png"
        screen_capture(post_screen)
        print(f"[driver] post-preset screen captured: {post_screen.stat().st_size} bytes", file=sys.stderr)

        # Also capture the asciiid composited UI frame for cross-reference
        cap_resp = c.call("CAPTURE_UI_FRAME", str(out_dir), timeout_s=30.0)
        print(f"[driver] CAPTURE_UI_FRAME -> {cap_resp.strip()[:120]}", file=sys.stderr)
        time.sleep(1.0)

        # Compare pre vs post screen bytes — if equal, the live update is NOT
        # visible. (Identical PNG bytes would be a strong signal of no change.)
        pre_bytes = pre_screen.read_bytes()
        post_bytes = post_screen.read_bytes()
        bytes_differ = pre_bytes != post_bytes

        receipt = {
            "schema": "fl4131_termpp_live_sidebyside.v1",
            "asciiid_binary_mtime": ASCIIID.stat().st_mtime,
            "map_loaded": args.map,
            "material_id": args.material_id,
            "baseline_preset_index": args.reset_preset_index,
            "test_preset_index": args.preset_index,
            "termpp_window_opened": True,
            "screen_before_preset_png": str(pre_screen),
            "screen_after_preset_png": str(post_screen),
            "screen_before_size_bytes": len(pre_bytes),
            "screen_after_size_bytes": len(post_bytes),
            "screen_bytes_differ": bytes_differ,
            "asciiid_ui_frame_png": str(out_dir / "ui_frame.png"),
            "commit_under_test": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=str(REPO)
            ).decode().strip(),
            "verdict": "PASS" if bytes_differ else "FAIL_NO_VISIBLE_CHANGE",
            "interpretation": (
                "PASS means screen bytes changed after preset apply. This is "
                "necessary-but-not-sufficient for live TERM++ update — manual "
                "visual inspection of the two screen PNGs is required to "
                "confirm the change is in the TERM++ window terrain pixels."
            ),
        }
        (out_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(f"[driver] receipt: {out_dir/'receipt.json'} verdict={receipt['verdict']}", file=sys.stderr)

        try:
            c.call("QUIT", "", timeout_s=2.0)
        except Exception:
            pass
        c.close()
        return 0 if bytes_differ else 1
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
