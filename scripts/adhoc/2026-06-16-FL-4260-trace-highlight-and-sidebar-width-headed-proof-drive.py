# Ad hoc script: FL-4260 trace-highlight and sidebar-width headed proof driver — recaptures 8 scenarios with rebuilt binary post globals+highlight-loop edits
# Created: 2026-06-16
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""FL-4260 trace-highlight + sidebar-width headed CDP proof driver.

Scenarios:
  termpp_no_crash       — TERM++ panel opens without crash (FBO dim clamp fix proof)
  trace_toggle_off      — RENDERING tab / section 7 Trace / highlight OFF (toggle visible)
  trace_hl_sel_mat      — highlight ON mode 0 (selected material), terrain:1 selected
  trace_hl_ramp_blocked — highlight ON mode 1 (ramp row) but no clicked cell → blocked label visible
  trace_hl_missing_policy — highlight ON mode 3 (missing policy cells)
  trace_hl_all_terrain  — highlight ON mode 4 (all terrain cells) — NEW
  trace_hl_term_panel   — TERM++ panel visible side by side with highlight ON mode 4
  width_narrow          — RENDERING at default width (~460px)
  width_wide            — RENDERING widened to 900px via FL4260_SET_SIDEBAR_WIDTH
  legacy_default_hidden — default launch (no flag), RENDERING tab, legacy surface absent

Usage: python3 <this> PORT [--out DIR]
"""
import argparse, json, os, re, shutil, socket, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ASCIIID = REPO / ".run" / "asciiid"
MAP = REPO / ".run" / "fl4260_fixture_all_materials.a3d"
if not MAP.exists():
    MAP = REPO / ".run" / "big_map.a3d"


class CdpClient:
    def __init__(self, port: int, deadline=30.0):
        self.next_id = 1; self.buf = ""
        end = time.time() + deadline
        while time.time() < end:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=1.0)
                self.sock.settimeout(None); return
            except OSError:
                time.sleep(0.3)
        raise RuntimeError(f"CDP not ready on {port}")

    def call(self, method, params="", timeout=30.0):
        i = self.next_id; self.next_id += 1
        self.sock.sendall((json.dumps({"id": i, "method": method, "params": params}) + "\n").encode())
        end = time.time() + timeout
        while time.time() < end:
            self.sock.settimeout(max(0.05, end - time.time()))
            try: chunk = self.sock.recv(65536).decode("utf-8", errors="replace")
            except socket.timeout: continue
            if not chunk: raise RuntimeError("socket closed")
            self.buf += chunk
            while "\n" in self.buf:
                line, self.buf = self.buf.split("\n", 1)
                if not line.strip(): continue
                try:
                    msg = json.loads(line)
                except Exception: continue
                if msg.get("id") == i:
                    return str(msg.get("result", ""))
        raise TimeoutError(f"CDP timeout: {method}")

    def close(self):
        try: self.sock.close()
        except OSError: pass


def capture(client, out_dir: Path, sleep=0.8):
    out_dir.mkdir(parents=True, exist_ok=True)
    time.sleep(sleep)
    resp = client.call("CAPTURE_UI_FRAME", str(out_dir), timeout=20.0)
    png = out_dir / "ui_frame.png"
    end = time.time() + 10
    while time.time() < end and not png.exists():
        time.sleep(0.1)
    if not png.exists():
        raise RuntimeError(f"ui_frame.png not written: {out_dir}")
    print(f"  [CAPTURE] {png} ({png.stat().st_size} bytes)", file=sys.stderr)
    return png


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", type=int)
    ap.add_argument("--out", default=str(
        REPO / "docs/research/ascii/verification/fl4260/2026-06-16-trace-highlight-width-proof"))
    args = ap.parse_args()
    out_root = Path(args.out)

    proc = subprocess.Popen(
        [str(ASCIIID), "--cdp", str(args.port)],
        cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        c = CdpClient(args.port)
        print("[driver] connected", file=sys.stderr)
        # Load map via CDP (positional arg not supported in --cdp mode)
        load_resp = c.call("LOAD_MAP", str(MAP), timeout=60.0)
        print(f"[driver] LOAD_MAP -> {load_resp.strip()[:120]}", file=sys.stderr)
        time.sleep(1.5)

        # --- 0. termpp_no_crash: open TERM++ panel and survive → FBO dim clamp fix proof ---
        # Before this fix, actual_w/actual_h leaked FBO pixel dims into bridge array
        # indexing when no terrain cells were visible; now Fl4131EditorDomainBridgePopulate()
        # clamps them to ≤160×90 so the overlay doesn't OOB crash.
        c.call("SET_TERMPP_EMBEDDED_VISIBLE", "1")
        time.sleep(0.8)  # allow FBO mount
        c.call("FL4260_RENDERING_PROOF", "1 -1 0")
        capture(c, out_root / "termpp_no_crash")

        # --- 1. trace_toggle_off: viewport highlight checkbox visible, highlight OFF ---
        # scroll_focus=4 scrolls to "Viewport highlight:" controls at bottom of section 7
        c.call("FL4260_RENDERING_PROOF", "1 -1 4")
        c.call("FL4260_TRACE_HIGHLIGHT", "0 0")      # disabled, mode 0
        capture(c, out_root / "trace_toggle_off")

        # --- 2. trace_hl_sel_mat: highlight ON mode 0 (selected material terrain:1) ---
        c.call("FL4260_TRACE_HIGHLIGHT", "1 0")
        capture(c, out_root / "trace_hl_sel_mat")

        # --- 3. trace_hl_ramp_blocked: mode 1 (ramp row), no clicked cell → orange warning ---
        c.call("FL4260_TRACE_HIGHLIGHT", "1 1")
        capture(c, out_root / "trace_hl_ramp_blocked")

        # --- 4. trace_hl_missing_policy: mode 3 (missing policy cells) ---
        c.call("FL4260_TRACE_HIGHLIGHT", "1 3")
        capture(c, out_root / "trace_hl_missing_policy")

        # --- 4b. trace_hl_all_terrain: mode 4 (all terrain cells) — NEW ---
        c.call("FL4260_TRACE_HIGHLIGHT", "1 4")
        capture(c, out_root / "trace_hl_all_terrain")

        # --- 5. trace_hl_term_panel: TERM++ visible side by side, highlight mode 4 ---
        # TERM++ is already visible from step 0. Keep mode 4 active so PNG shows
        # the full terrain highlight with TERM++ panel rendered below the sidebar.
        c.call("FL4260_RENDERING_PROOF", "1 -1 4")   # scroll to highlight section
        capture(c, out_root / "trace_hl_term_panel")

        # --- 6. width_narrow: default width ---
        c.call("FL4260_TRACE_HIGHLIGHT", "0 0")
        c.call("FL4260_RENDERING_PROOF", "1 -1 2")
        capture(c, out_root / "width_narrow")

        # --- 7. width_wide: 900px override ---
        c.call("FL4260_SET_SIDEBAR_WIDTH", "900")
        capture(c, out_root / "width_wide")

        # --- 8. legacy_default_hidden: default launch, no legacy flag ---
        c.call("FL4260_SET_SIDEBAR_WIDTH", "460")
        c.call("FL4260_RENDERING_PROOF", "1 -1 0")
        capture(c, out_root / "legacy_default_hidden")

        print("[driver] all captures done", file=sys.stderr)
        try: c.call("QUIT", "", timeout=2)
        except Exception: pass
        c.close()
        return 0
    finally:
        try: proc.terminate(); proc.wait(timeout=5)
        except Exception: proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
