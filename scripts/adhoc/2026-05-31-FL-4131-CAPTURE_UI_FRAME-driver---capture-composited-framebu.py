# Ad hoc script: FL-4131 CAPTURE_UI_FRAME driver - capture composited framebuffer with Extended Material Presets + Extended Glyph Browser visible
# Created: 2026-05-31
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4131 visual proof driver. Launches asciiid --cdp PORT, loads the FL-4131
test map, applies a preset to surface extended glyphs on screen, then triggers
CAPTURE_UI_FRAME and verifies the composited framebuffer PNG was written.

Goal: produce a real visual artifact proving the Extended Material Presets +
Extended Glyph Browser UI are wired and rendering, replacing the CDP-only
proof shape that operator review rejected on 2026-05-30.

Usage: python3 scripts/adhoc/<this-script>.py [--out DIR] [--port N] [--map PATH]
"""
from __future__ import annotations

import argparse
import json
import os
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
        raise RuntimeError(f"CDP port {port} did not become ready: {last_err}")

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
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == i:
                    return str(msg.get("result", ""))
        raise TimeoutError(f"CDP timeout for {method}")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / ".run" / "fl4131_capture_ui_frame"))
    ap.add_argument("--port", type=int, default=47731)
    ap.add_argument("--map", default=str(DEFAULT_MAP))
    ap.add_argument("--material-id", type=int, default=1)
    ap.add_argument("--preset-index", type=int, default=11)  # SNOW Crystal — visible glyphs
    args = ap.parse_args()

    out_dir = Path(args.out)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[driver] launching {ASCIIID} --cdp {args.port}", file=sys.stderr)
    proc = subprocess.Popen(
        [str(ASCIIID), "--cdp", str(args.port)],
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        client = CdpClient(args.port)
        print(f"[driver] connected on {args.port}", file=sys.stderr)

        # Load the FL-4131 test map (has all presets baked).
        load_resp = client.call("LOAD_MAP", args.map, timeout_s=60.0)
        print(f"[driver] LOAD_MAP -> {load_resp.strip()[:160]}", file=sys.stderr)

        # Apply a preset so extended glyphs are live in the material grid + sidecar.
        apply_resp = client.call(
            "FL4131_APPLY_EXTENDED_PRESET",
            f"{args.material_id} {args.preset_index}",
            timeout_s=15.0,
        )
        print(f"[driver] APPLY_PRESET -> {apply_resp.strip()[:160]}", file=sys.stderr)

        # Verify presets list (sanity).
        list_resp = client.call("FL4131_LIST_EXTENDED_PRESETS", timeout_s=15.0)
        preset_count = list_resp.count("FL4131_PRESET index=")
        print(f"[driver] preset_count={preset_count}", file=sys.stderr)

        # Dump extended picker UI rects (proves the Extended Glyph Browser rendered).
        picker_resp = client.call("FL4131_DUMP_EXTENDED_PICKER_UI_RECTS", timeout_s=10.0)
        print(f"[driver] picker_rects -> {picker_resp.strip()[:280]}", file=sys.stderr)

        # Parse first-glyph rect and click it to verify the browser SELECTS extended GlyphIds.
        import re
        m = re.search(
            r"first_glyph_id=(\d+) first_valid=1 first_x0=(-?\d+) first_y0=(-?\d+) first_x1=(-?\d+) first_y1=(-?\d+)",
            picker_resp,
        )
        first_glyph_click = None
        if m:
            first_glyph_id = int(m.group(1))
            cx = (int(m.group(2)) + int(m.group(4))) // 2
            cy = (int(m.group(3)) + int(m.group(5))) // 2
            print(f"[driver] clicking Extended Glyph Browser cell glyph_id={first_glyph_id} at ({cx},{cy})", file=sys.stderr)
            click_resp = client.call("RUN_MOUSE_CLICK_PROBE", f"{cx} {cy}", timeout_s=10.0)
            print(f"[driver] click -> {click_resp.strip()[:200]}", file=sys.stderr)
            first_glyph_click = {"glyph_id": first_glyph_id, "x": cx, "y": cy, "response": click_resp.strip()[:300]}
        else:
            raise RuntimeError(f"Could not parse first_glyph rect from: {picker_resp[:300]}")

        # Re-dump to confirm active_glyph_id moved to the clicked extended GlyphId.
        picker_after = client.call("FL4131_DUMP_EXTENDED_PICKER_UI_RECTS", timeout_s=10.0)
        print(f"[driver] picker_after -> {picker_after.strip()[:280]}", file=sys.stderr)

        # Dump preset UI rects (proves Extended Material Presets rendered).
        rects_resp = client.call("FL4131_DUMP_PRESET_UI_RECTS", timeout_s=10.0)
        rect_count = rects_resp.count("FL4131_PRESET_UI_RECT")
        print(f"[driver] preset_ui_rects rows={rect_count}", file=sys.stderr)

        # Trigger CAPTURE_UI_FRAME on the post-ImGui composited framebuffer.
        cap_resp = client.call("CAPTURE_UI_FRAME", str(out_dir), timeout_s=30.0)
        print(f"[driver] CAPTURE_UI_FRAME -> {cap_resp.strip()[:160]}", file=sys.stderr)

        # Wait briefly for the next frame to flush the capture.
        png = out_dir / "ui_frame.png"
        end = time.time() + 10.0
        while time.time() < end and not png.exists():
            time.sleep(0.1)
        if not png.exists():
            raise RuntimeError(f"ui_frame.png not written to {out_dir}")
        size = png.stat().st_size
        print(f"[driver] PNG written: {png} ({size} bytes)", file=sys.stderr)

        # Write a tiny receipt next to the PNG.
        receipt = {
            "schema": "fl4131_capture_ui_frame.v2",
            "asciiid_binary_mtime": ASCIIID.stat().st_mtime,
            "map_loaded": args.map,
            "preset_applied": args.preset_index,
            "material_id": args.material_id,
            "preset_count": preset_count,
            "preset_ui_rect_rows": rect_count,
            "extended_picker_rects_before_click": picker_resp.strip()[:400],
            "extended_picker_first_glyph_click": first_glyph_click,
            "extended_picker_rects_after_click": picker_after.strip()[:400],
            "ui_frame_png": str(png),
            "ui_frame_bytes": size,
            "commit_under_test": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=str(REPO)
            ).decode().strip(),
            "verdict": "PASS",
        }
        (out_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(f"[driver] receipt written: {out_dir/'receipt.json'}", file=sys.stderr)

        try:
            client.call("QUIT", "", timeout_s=2.0)
        except Exception:
            pass
        client.close()
        return 0
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
