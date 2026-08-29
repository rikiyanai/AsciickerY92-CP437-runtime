#!/usr/bin/env python3
"""FL-4260 keyboard-nav smoke test.

Empirically verifies the new deterministic RUN_SDL_KEY injection drives ImGui
keyboard nav (NavEnableKeyboard) inside the editor RENDERING tab: focus moves
with Tab/arrows and a focused slider adjusts with arrows. This is the make-or-
break check before building the full keyboard-driven rendered-glyph proof.

Runs the WORKTREE binary with cwd=MAIN repo so it finds assets/ (maps, fonts,
glyph profiles) that are untracked and absent from the worktree checkout.

Usage: python3 <this> PORT [--out DIR]
"""
import argparse, json, os, socket, subprocess, sys, time
from pathlib import Path

WT = Path(__file__).resolve().parents[2]                 # worktree root
MAIN = Path("/Users/r/downloads/asciicker-Y9-2")         # asset-bearing main repo
ASCIIID = WT / ".run" / "asciiid"
MAP = MAIN / "assets" / "a3d" / "game_map_y8.a3d"         # real diverse terrain

# SDL2 scancodes
SC = {"TAB": 43, "RIGHT": 79, "LEFT": 80, "DOWN": 81, "UP": 82,
      "ENTER": 40, "SPACE": 44, "ESC": 41}


class Cdp:
    def __init__(self, port, deadline=40.0):
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
            try: chunk = self.sock.recv(65536).decode("utf-8", "replace")
            except socket.timeout: continue
            if not chunk: raise RuntimeError("socket closed")
            self.buf += chunk
            while "\n" in self.buf:
                ln, self.buf = self.buf.split("\n", 1)
                if not ln.strip(): continue
                try: msg = json.loads(ln)
                except Exception: continue
                if msg.get("id") == i:
                    return str(msg.get("result", ""))
        raise TimeoutError(f"CDP timeout: {method}")

    def close(self):
        try: self.sock.close()
        except OSError: pass


def cap(c, out, name, sleep=0.6):
    out.mkdir(parents=True, exist_ok=True)
    time.sleep(sleep)
    c.call("CAPTURE_UI_FRAME", str(out), timeout=20.0)
    src = out / "ui_frame.png"
    end = time.time() + 8
    while time.time() < end and not src.exists():
        time.sleep(0.1)
    if not src.exists():
        print(f"  [CAP-FAIL] {name}", file=sys.stderr); return None
    dst = out / f"{name}.png"; src.replace(dst)
    print(f"  [CAP] {dst.name} ({dst.stat().st_size}b)", file=sys.stderr)
    return dst


def key(c, name, presses=1, settle=0.0):
    sc = SC[name]
    r = c.call("RUN_SDL_KEY", f"{sc} {presses}", timeout=10.0)
    print(f"  [KEY] {name}x{presses}: {r.strip()}", file=sys.stderr)
    # frame-spanning taps: ~4 frames/press; wait generously
    time.sleep(max(0.12 * presses + 0.4, settle))
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", type=int)
    ap.add_argument("--out", default=str(WT / "docs/research/ascii/verification/fl4260/2026-06-17-keyboard-nav-smoke"))
    args = ap.parse_args()
    out = Path(args.out)
    if not ASCIIID.exists(): print(f"[FATAL] no binary {ASCIIID}", file=sys.stderr); return 2
    if not MAP.exists(): print(f"[FATAL] no map {MAP}", file=sys.stderr); return 2

    proc = subprocess.Popen([str(ASCIIID), "--cdp", str(args.port)],
                            cwd=str(MAIN), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        c = Cdp(args.port)
        print("[driver] connected", file=sys.stderr)
        print("  LOAD_MAP:", c.call("LOAD_MAP", str(MAP), timeout=60.0).strip()[:120], file=sys.stderr)
        time.sleep(1.5)
        print("  SET_RENDER_MODE:", c.call("FL4260_SET_RENDER_MODE", "1").strip()[:160], file=sys.stderr)
        print("  RENDERING_PROOF:", c.call("FL4260_RENDERING_PROOF", "1 0 0").strip()[:200], file=sys.stderr)
        time.sleep(0.8)
        print("  FOCUS_SIDEBAR:", c.call("FL4260_FOCUS_SIDEBAR", "").strip()[:120], file=sys.stderr)
        time.sleep(0.5)

        cap(c, out, "00_baseline")
        key(c, "TAB", 1);   cap(c, out, "01_tab1")
        key(c, "DOWN", 2);  cap(c, out, "02_down2")
        key(c, "DOWN", 4);  cap(c, out, "03_down6")
        key(c, "DOWN", 8);  cap(c, out, "04_down14")
        key(c, "RIGHT", 12);cap(c, out, "05_right12")
        key(c, "LEFT", 6);  cap(c, out, "06_left6")

        try: c.call("QUIT", "", timeout=2)
        except Exception: pass
        c.close()
        time.sleep(0.5)
    finally:
        try: proc.terminate(); o, e = proc.communicate(timeout=5)
        except Exception:
            proc.kill(); o, e = proc.communicate()
        # surface the [MCP] key-tap traces + any nav diagnostics
        tail = (o or b"").decode("utf-8", "replace").splitlines()
        keylines = [l for l in tail if "RUN_SDL_KEY" in l or "Fl4260KeyTap" in l or "RENDERING_PROOF" in l or "SET_RENDER_MODE" in l]
        print("\n[stdout key/render lines]", file=sys.stderr)
        for l in keylines[-40:]:
            print("  " + l, file=sys.stderr)
        (out / "stdout_tail.txt").write_text("\n".join(tail[-400:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
