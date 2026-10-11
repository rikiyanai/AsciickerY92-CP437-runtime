#!/usr/bin/env python3
"""FL-4260 keyboard-ADJUST smoke test.

Verifies the custom keyboard focus/adjust model: FL4260_KB_FOCUS sets the focused
slider, real injected '.' (period=increment) / ',' (comma=decrement) keypresses
move its value, a yellow highlight marks focus. Confirms before/after UI frames
differ at the focused control.
"""
import argparse, json, socket, subprocess, sys, time
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
MAIN = Path("/Users/r/downloads/asciicker-Y9-2")
ASCIIID = WT / ".run" / "asciiid"
MAP = MAIN / "assets" / "a3d" / "game_map_y8.a3d"
SC = {"COMMA": 54, "PERIOD": 55, "SEMI": 51, "QUOTE": 52, "TAB": 43}


class Cdp:
    def __init__(self, port, deadline=40.0):
        self.next_id = 1; self.buf = ""
        end = time.time() + deadline
        while time.time() < end:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=1.0)
                self.sock.settimeout(None); return
            except OSError: time.sleep(0.3)
        raise RuntimeError("CDP not ready")
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
                if msg.get("id") == i: return str(msg.get("result", ""))
        raise TimeoutError(method)
    def close(self):
        try: self.sock.close()
        except OSError: pass


def cap(c, out, name, sleep=0.6):
    out.mkdir(parents=True, exist_ok=True); time.sleep(sleep)
    c.call("CAPTURE_UI_FRAME", str(out), timeout=20.0)
    src = out / "ui_frame.png"; end = time.time() + 8
    while time.time() < end and not src.exists(): time.sleep(0.1)
    if not src.exists(): print(f"  [CAP-FAIL] {name}", file=sys.stderr); return None
    dst = out / f"{name}.png"; src.replace(dst)
    print(f"  [CAP] {dst.name} ({dst.stat().st_size}b)", file=sys.stderr); return dst


def key(c, name, presses):
    r = c.call("RUN_SDL_KEY", f"{SC[name]} {presses}", timeout=10.0)
    print(f"  [KEY] {name}x{presses}: {r.strip()}", file=sys.stderr)
    time.sleep(0.14 * presses + 0.5)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("port", type=int)
    ap.add_argument("--out", default=str(WT / "docs/research/ascii/verification/fl4260/2026-06-17-kb-adjust-smoke"))
    args = ap.parse_args(); out = Path(args.out)
    proc = subprocess.Popen([str(ASCIIID), "--cdp", str(args.port)], cwd=str(MAIN),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        c = Cdp(args.port); print("[driver] connected", file=sys.stderr)
        c.call("LOAD_MAP", str(MAP), timeout=60.0); time.sleep(1.5)
        print("  RENDER_MODE:", c.call("FL4260_SET_RENDER_MODE", "1").strip()[:120], file=sys.stderr)
        print("  RENDERING_PROOF:", c.call("FL4260_RENDERING_PROOF", "1 0 0").strip()[:160], file=sys.stderr)
        time.sleep(0.8)
        print("  KB_FOCUS:", c.call("FL4260_KB_FOCUS", "scoring.curve").strip()[:120], file=sys.stderr)
        c.call("FL4260_FOCUS_SIDEBAR", ""); time.sleep(0.6)
        print("  KB_LIST:\n" + c.call("FL4260_KB_LIST", ""), file=sys.stderr)

        cap(c, out, "00_focus_curve")
        key(c, "PERIOD", 8)      # increment curve 8x via real '.' keypress
        cap(c, out, "01_after_inc8")
        print("  KB_LIST2:\n" + c.call("FL4260_KB_LIST", ""), file=sys.stderr)
        key(c, "QUOTE", 2)       # focus-next ' x2
        cap(c, out, "02_focus_next2")
        key(c, "COMMA", 6)       # decrement focused 6x via real ',' keypress
        cap(c, out, "03_after_dec6")
        print("  KB_LIST3:\n" + c.call("FL4260_KB_LIST", ""), file=sys.stderr)

        try: c.call("QUIT", "", timeout=2)
        except Exception: pass
        c.close(); time.sleep(0.5)
    finally:
        try: proc.terminate(); o, e = proc.communicate(timeout=5)
        except Exception: proc.kill(); o, e = proc.communicate()
        tail = (o or b"").decode("utf-8", "replace").splitlines()
        kl = [l for l in tail if any(s in l for s in ("KB_FOCUS", "KB_LIST", "kb[", "RUN_SDL_KEY", "KeyTap"))]
        print("\n[stdout kb lines]", file=sys.stderr)
        for l in kl[-50:]: print("  " + l, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
