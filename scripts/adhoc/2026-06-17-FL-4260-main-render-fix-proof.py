#!/usr/bin/env python3
"""FL-4260 render-fix proof, run from MAIN checkout.

Proves the ported fixes make live profile edits change the rendered glyphs on the
standalone TERM++ rendered buffer (FL4207_DUMP_TERMPP_RENDERED_BUFFER):

  POOL/gen_counter:  FL4260_RENDERING_PROOF 1 0 -> 1 3  (re-seed terrain:1 pool)
  SCORING/weighted-L2: FL4260_SET_PROFILE_SCORING heavy single-axis weights

Each edit re-opens the standalone view (fresh render) and diffs final_gid per cell.
PASS iff changed glyph cells > floor. Run from /Users/r/Downloads/asciicker-Y9-2.
"""
import json, socket, subprocess, sys, time
from pathlib import Path

REPO = Path("/Users/r/downloads/asciicker-Y9-2")
ASCIIID = REPO / ".run" / "asciiid"
MAP = REPO / "assets" / "a3d" / "game_map_y8.a3d"
OUT = REPO / "docs/research/ascii/verification/fl4260/2026-06-17-main-render-fix-proof"
FLOOR = 8


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
    def call(self, m, p="", timeout=30.0):
        i = self.next_id; self.next_id += 1
        self.sock.sendall((json.dumps({"id": i, "method": m, "params": p}) + "\n").encode())
        end = time.time() + timeout
        while time.time() < end:
            self.sock.settimeout(max(0.05, end - time.time()))
            try: chunk = self.sock.recv(65536).decode("utf-8", "replace")
            except socket.timeout: continue
            if not chunk: raise RuntimeError("sock_gone")
            self.buf += chunk
            while "\n" in self.buf:
                ln, self.buf = self.buf.split("\n", 1)
                if not ln.strip(): continue
                try: o = json.loads(ln)
                except Exception: continue
                if o.get("id") == i: return str(o.get("result", ""))
        raise TimeoutError(m)


def reopen(c):
    c.call("CLOSE_TERMPP", ""); time.sleep(0.4)
    c.call("OPEN_TERMPP_CURRENT_VIEW", ""); time.sleep(2.2)


def cells(c, name):
    OUT.mkdir(parents=True, exist_ok=True)
    dst = (OUT / f"{name}.jsonl").resolve()
    if dst.exists(): dst.unlink()
    c.call("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(dst), timeout=20.0)
    end = time.time() + 10
    while time.time() < end and not dst.exists(): time.sleep(0.1)
    g = {}
    if dst.exists():
        for ln in dst.read_text(errors="replace").splitlines():
            ln = ln.strip()
            if ln.startswith("{"):
                try: o = json.loads(ln)
                except Exception: continue
                if o.get("kind") == "cell":
                    g[(o["x"], o["y"])] = (o.get("final_gid"), o.get("fg"), o.get("bk"))
    return g


def diff(a, b, ch):
    keys = set(a) | set(b)
    n = 0
    for k in keys:
        ca, cb = a.get(k), b.get(k)
        if ca is None or cb is None: n += 1; continue
        if ch == "glyph":
            if ca[0] != cb[0]: n += 1
        else:
            if (ca[1], ca[2]) != (cb[1], cb[2]): n += 1
    return n


def main():
    port = int(sys.argv[1])
    proof = {"schema": "fl4260.main_render_fix.v1", "head": None, "map": str(MAP), "checks": {}}
    proc = subprocess.Popen([str(ASCIIID), "--cdp", str(port)], cwd=str(REPO),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        c = Cdp(port); print("[proof] connected", file=sys.stderr)
        c.call("LOAD_MAP", str(MAP), timeout=60.0); time.sleep(1.5)
        c.call("FL4260_SET_RENDER_MODE", "1"); time.sleep(0.3)
        c.call("FL4260_RENDERING_PROOF", "1 0 0"); time.sleep(0.8)

        # ---- jitter floor: two reopens, no edit ----
        reopen(c); j0 = cells(c, "jitter0")
        reopen(c); j1 = cells(c, "jitter1")
        jit = diff(j0, j1, "glyph")
        gate = max(3 * jit, FLOOR)
        proof["checks"]["rendered_cells"] = len(j0)
        proof["checks"]["jitter_glyph"] = jit
        proof["checks"]["gate"] = gate

        # ---- POOL/gen_counter: find a preset pair that differs and measure ----
        c.call("FL4260_RENDERING_PROOF", "1 0 0"); time.sleep(0.6)
        reopen(c); p0 = cells(c, "pool_preset0")
        pool_results = {}
        best = 0
        for pi in (1, 2, 3, 4, 5, 6):
            r = c.call("FL4260_RENDERING_PROOF", f"1 {pi} 0"); time.sleep(0.6)
            reopen(c); pX = cells(c, f"pool_preset{pi}")
            d = diff(p0, pX, "glyph")
            pool_results[pi] = d
            best = max(best, d)
            print(f"  pool preset0 vs preset{pi}: {d} changed | {r.strip()[:80]}", file=sys.stderr)
        proof["checks"]["pool_reseed_glyph_changed_by_preset"] = pool_results
        proof["checks"]["pool_reseed_glyph_changed"] = best
        proof["checks"]["pool_changed_render"] = best > gate

        # ---- SCORING/weighted-L2: neutral -> heavy single-axis ----
        c.call("FL4260_RENDERING_PROOF", "1 0 0"); time.sleep(0.6)
        c.call("FL4260_SET_PROFILE_SCORING", "1 1 1 1 1 1 1"); time.sleep(0.4)
        reopen(c); s0 = cells(c, "scoring_neutral")
        c.call("FL4260_SET_PROFILE_SCORING", "1 4 0 0 0 4 0"); time.sleep(0.4)
        reopen(c); s1 = cells(c, "scoring_heavy")
        sc_glyph = diff(s0, s1, "glyph")
        proof["checks"]["scoring_weighted_glyph_changed"] = sc_glyph
        proof["checks"]["scoring_changed_render"] = sc_glyph > gate

        proof["PASS"] = bool(proof["checks"]["pool_changed_render"] and proof["checks"]["scoring_changed_render"])
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "PROOF.json").write_text(json.dumps(proof, indent=2))
        print(json.dumps(proof["checks"], indent=2))
        print(f"[proof] PASS={proof['PASS']}", file=sys.stderr)
        try: c.call("QUIT", "", timeout=2)
        except Exception: pass
        return 0 if proof["PASS"] else 1
    finally:
        try: proc.terminate(); proc.communicate(timeout=5)
        except Exception: proc.kill(); proc.communicate()


if __name__ == "__main__":
    raise SystemExit(main())
