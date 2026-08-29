#!/usr/bin/env python3
"""FL-4260 keyboard-driven rendered-buffer proof.

For every fixed FL-4260 "25 render-dead sliders" row, prove a REAL injected
keypress on the focused RENDERING control changes the standalone TERM++ rendered
buffer through the UI edit path:

  1. LOAD diverse terrain (game_map_y8.a3d) so cells vary in elv/shade/src6.
  2. Force PROFILE render mode + seed the selected material profile (default terrain:0).
  3. Open standalone TERM++ over the current diverse view.
  4. Measure a no-edit jitter floor (two dumps, no input).
  5. Per slider:  FL4260_KB_FOCUS <label>  (UI focus label in RENDERING panel)
                  dump rendered buffer BEFORE
                  RUN_SDL_KEY <'.'=increment> xN    (REAL SDL keypresses)
                  dump rendered buffer AFTER
     PASS iff the expected rendered-buffer channel changes above jitter.

For §3 color/shade rows, the profile color override must already be active
before the measured keypress. Otherwise the proof only shows first-time profile
color enablement, not the selected row control's independent render effect.

This is rendered-output proof (TermDumpRenderedBuffer = the final per-cell buffer
uploaded before TERM++ present), driven by real SDL key events through the
keyboard path. It is not a selector-function probe and not a direct setter bypass.

Usage: python3 <this> PORT [--out DIR]
"""
import argparse, json, re, socket, subprocess, sys, time
from pathlib import Path

WT = Path(__file__).resolve().parents[2]
MAIN = WT
ASCIIID = WT / ".run" / "asciiid"
MAP = MAIN / "assets" / "a3d" / "game_map_y8.a3d"
PERIOD, COMMA = 55, 54        # '.' increment, ',' decrement
FLOOR = 8                     # min changed cells to call it a real change

# (label, increment_presses, channel) -- presses chosen to span each slider range.
# §3 rows are color/shade controls, so fg/bg rendered color is the expected
# consumer channel. §6 scoring controls select glyph winners, so glyph id is the
# expected channel.
SLIDERS = [
    ("color.fg_str.r0", 14, "color"), ("color.bg_str.r0", 14, "color"),
    ("color.shade_contrast.r0", 8, "color"), ("color.band_thres.r0", 8, "color"),
    ("color.fg_str.r1", 14, "color"), ("color.bg_str.r1", 14, "color"),
    ("color.shade_contrast.r1", 8, "color"), ("color.band_thres.r1", 8, "color"),
    ("color.fg_str.r2", 14, "color"), ("color.bg_str.r2", 14, "color"),
    ("color.shade_contrast.r2", 8, "color"), ("color.band_thres.r2", 8, "color"),
    ("color.fg_str.r3", 14, "color"), ("color.bg_str.r3", 14, "color"),
    ("color.shade_contrast.r3", 8, "color"), ("color.band_thres.r3", 8, "color"),
    ("scoring.detail_contrast", 16, "glyph"), ("scoring.tone_contrast", 16, "glyph"),
    ("scoring.density_bias", 16, "glyph"),
    ("scoring.curve", 8, "glyph"), ("scoring.diagonal", 8, "glyph"),
    ("scoring.horizontal", 8, "glyph"), ("scoring.vertical", 8, "glyph"),
    ("scoring.sparse", 8, "glyph"), ("scoring.dense", 8, "glyph"),
]


class Cdp:
    def __init__(self, port, proc=None, deadline=40.0):
        self.next_id = 1; self.buf = ""
        end = time.time() + deadline
        while time.time() < end:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=1.0)
                self.sock.settimeout(None); return
            except OSError: time.sleep(0.3)
            if proc is not None and proc.poll() is not None:
                out, err = proc.communicate(timeout=1)
                raise RuntimeError(
                    "CDP child exited before listen\n"
                    + out.decode("utf-8", "replace")[-4000:]
                    + err.decode("utf-8", "replace")[-4000:]
                )
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


def key(c, scancode, presses):
    for _ in range(presses):
        c.call("RUN_SDL_KEY", f"{scancode} 1", timeout=10.0)
        time.sleep(0.42)
    time.sleep(0.5)


def kb_status(c):
    txt = c.call("FL4260_KB_STATUS", "", timeout=10.0)
    m = re.search(r"focus=(\S*) last=(\S*) count=(\d+) value_i=(-?\d+) value_f=([-0-9.]+) is_float=(\d+)", txt)
    if not m:
        return {"raw": txt.strip(), "count": -1, "last": ""}
    return {
        "raw": txt.strip(),
        "focus": m.group(1),
        "last": m.group(2),
        "count": int(m.group(3)),
        "value_i": int(m.group(4)),
        "value_f": float(m.group(5)),
        "is_float": int(m.group(6)),
    }


def profile_color_status(c, material):
    txt = c.call("FL4260_PROFILE_COLOR_STATUS", f"{material} 0 0", timeout=10.0)
    m = re.search(r"ok=(\d+) fg=(\d+) bg=(\d+)", txt)
    if not m:
        return {"raw": txt.strip(), "ok": 0}
    return {"raw": txt.strip(), "ok": int(m.group(1)), "fg": int(m.group(2)), "bg": int(m.group(3))}


def dump(c, out, name):
    out.mkdir(parents=True, exist_ok=True)
    dst = (out / f"{name}.jsonl").resolve()
    if dst.exists(): dst.unlink()
    c.call("RENDER_TERMPP_ONCE", "", timeout=20.0)
    time.sleep(0.2)
    c.call("FL4207_DUMP_TERMPP_RENDERED_BUFFER", str(dst), timeout=20.0)
    end = time.time() + 10
    while time.time() < end and not dst.exists(): time.sleep(0.1)
    return dst


def load_cells(path):
    """Return {(x,y): (final_gid, fg, bk)} from a rendered-buffer JSONL dump."""
    g = {}
    if not path.exists(): return g
    for ln in path.read_text(errors="replace").splitlines():
        ln = ln.strip()
        if not ln or ln[0] != "{": continue
        try: o = json.loads(ln)
        except Exception: continue
        if o.get("kind") != "cell": continue
        x = o.get("x"); y = o.get("y")
        if x is None or y is None: continue
        g[(x, y)] = (o.get("final_gid"), o.get("fg"), o.get("bk"))
    return g


def changed_cells(a, b, channel):
    """channel='glyph' -> compare final_gid; 'color' -> compare (fg,bk)."""
    keys = set(a) | set(b)
    n = 0
    for k in keys:
        ca = a.get(k); cb = b.get(k)
        if ca is None or cb is None:
            n += 1; continue
        if channel == "glyph":
            if ca[0] != cb[0]: n += 1
        else:
            if (ca[1], ca[2]) != (cb[1], cb[2]): n += 1
    return n


def try_adjust(c, out, label, presses, channel, gate, material):
    before_path = dump(c, out, f"{label}_before")
    before = load_cells(before_path)
    status_before = kb_status(c)
    color_before = profile_color_status(c, material)
    color_precondition_ok = channel != "color" or color_before.get("ok") == 1
    key(c, PERIOD, presses)
    status_inc = kb_status(c)
    color_inc = profile_color_status(c, material)
    time.sleep(1.0)
    inc_path = dump(c, out, f"{label}_after_inc")
    inc = load_cells(inc_path)
    inc_changed = changed_cells(before, inc, channel)
    if inc_changed > gate:
        key(c, COMMA, presses)
        return {"direction": "period", "changed_cells": inc_changed,
                "before": before_path.name, "after": inc_path.name,
                "status_before": status_before, "status_after": status_inc,
                "profile_color_before": color_before, "profile_color_after": color_inc,
                "color_precondition_ok": color_precondition_ok}

    key(c, COMMA, presses * 2)
    status_dec = kb_status(c)
    color_dec = profile_color_status(c, material)
    time.sleep(1.0)
    dec_path = dump(c, out, f"{label}_after_dec")
    dec = load_cells(dec_path)
    dec_changed = changed_cells(before, dec, channel)
    key(c, PERIOD, presses)
    if dec_changed > inc_changed:
        return {"direction": "comma", "changed_cells": dec_changed,
                "before": before_path.name, "after": dec_path.name,
                "status_before": status_before, "status_after": status_dec,
                "profile_color_before": color_before, "profile_color_after": color_dec,
                "color_precondition_ok": color_precondition_ok}
    return {"direction": "period", "changed_cells": inc_changed,
            "before": before_path.name, "after": inc_path.name,
            "status_before": status_before, "status_after": status_inc,
            "profile_color_before": color_before, "profile_color_after": color_inc,
            "color_precondition_ok": color_precondition_ok}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("port", type=int)
    ap.add_argument("--material", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--fresh-each", action="store_true")
    ap.add_argument("--out", default=str(WT / "docs/research/ascii/verification/fl4260/2026-06-17-keyboard-rendered-glyph-proof"))
    args = ap.parse_args(); out = Path(args.out)
    if args.fresh_each:
        out.mkdir(parents=True, exist_ok=True)
        aggregate = {
            "schema": "fl4260.keyboard_rendered_buffer_25_sliders.fresh_each.v1",
            "material": args.material,
            "sliders": {},
        }
        ok_count = 0
        for idx, (label, _presses, _channel) in enumerate(SLIDERS):
            sub = out / label.replace(".", "_")
            cmd = [
                sys.executable, str(Path(__file__).resolve()), str(args.port + idx + 1),
                "--material", str(args.material),
                "--only", label,
                "--out", str(sub),
            ]
            print(f"[fresh] {label}", file=sys.stderr)
            rc = subprocess.run(cmd, cwd=str(WT), text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
            (sub / "driver.stdout.txt").write_text(rc.stdout)
            (sub / "driver.stderr.txt").write_text(rc.stderr)
            proof_path = sub / "PROOF.json"
            if proof_path.exists():
                proof = json.loads(proof_path.read_text())
                row = proof["sliders"].get(label, {})
                row["returncode"] = rc.returncode
                row["artifact_dir"] = str(sub)
                aggregate["sliders"][label] = row
                if (row.get("changed_render") and row.get("ui_adjusted")
                        and row.get("color_precondition_ok", True)
                        and rc.returncode == 0):
                    ok_count += 1
            else:
                aggregate["sliders"][label] = {
                    "returncode": rc.returncode,
                    "artifact_dir": str(sub),
                    "changed_render": False,
                    "ui_adjusted": False,
                    "error": "missing PROOF.json",
                }
            print(rc.stderr, file=sys.stderr)
        aggregate["summary"] = {
            "sliders_total": len(SLIDERS),
            "sliders_changed_render": ok_count,
            "all_changed_render": ok_count == len(SLIDERS),
        }
        (out / "PROOF.json").write_text(json.dumps(aggregate, indent=2))
        print(json.dumps(aggregate["summary"], indent=2))
        return 0 if ok_count == len(SLIDERS) else 1

    proof = {"schema": "fl4260.keyboard_rendered_buffer_25_sliders.v1", "map": str(MAP),
             "material": args.material,
             "method": "real injected period/comma keypresses on FL4260_KB_FOCUS-focused RENDERING slider; "
                       "delta in standalone TERM++ rendered buffer (FL4207_DUMP_TERMPP_RENDERED_BUFFER). "
                       "Diverse terrain so scoring and color/shade edits produce graded deltas.",
             "floor": FLOOR, "sliders": {}}
    if not MAP.exists(): print(f"[FATAL] no map {MAP}", file=sys.stderr); return 2

    proc = subprocess.Popen([str(ASCIIID), "--cdp", str(args.port)], cwd=str(MAIN),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        c = Cdp(args.port, proc=proc); print("[driver] connected", file=sys.stderr)
        c.call("LOAD_MAP", str(MAP), timeout=60.0); time.sleep(1.5)
        c.call("FL4260_SET_RENDER_MODE", "1"); time.sleep(0.3)
        print("  RENDERING_PROOF:", c.call("FL4260_RENDERING_PROOF", f"{args.material} 0 0").strip()[:140], file=sys.stderr)
        time.sleep(0.8)
        c.call("FL4260_FOCUS_SIDEBAR", ""); time.sleep(0.4)
        # standalone TERM++ over the current diverse view
        c.call("CLOSE_TERMPP", ""); time.sleep(0.4)
        c.call("OPEN_TERMPP_CURRENT_VIEW", ""); time.sleep(2.5)

        # global jitter floor: two dumps, no input (measured on both channels)
        j0 = load_cells(dump(c, out, "jitter_0")); time.sleep(0.6)
        j1 = load_cells(dump(c, out, "jitter_1"))
        jit_g = changed_cells(j0, j1, "glyph")
        jit_c = changed_cells(j0, j1, "color")
        total = len(j0)
        proof["rendered_cells"] = total
        proof["jitter_floor"] = {"glyph": jit_g, "color": jit_c}
        gate_g = max(3 * jit_g, FLOOR)
        gate_c = max(3 * jit_c, FLOOR)
        proof["gate"] = {"glyph": gate_g, "color": gate_c}
        print(f"[driver] rendered_cells={total} jitter(glyph={jit_g},color={jit_c}) gate(glyph={gate_g},color={gate_c})", file=sys.stderr)

        selected = SLIDERS
        if args.only:
            wanted = {x.strip() for x in args.only.split(",") if x.strip()}
            selected = [s for s in SLIDERS if s[0] in wanted]
            proof["only"] = sorted(wanted)

        for label, presses, channel in selected:
            gate = gate_c if channel == "color" else gate_g
            c.call("FL4260_KB_FOCUS", label); time.sleep(0.5)
            result = try_adjust(c, out, label, presses, channel, gate, args.material)
            ch = result["changed_cells"]
            is_ok = ch > gate and result.get("color_precondition_ok", True)
            ui_adjusted = (
                result["status_after"].get("count", -1) > result["status_before"].get("count", -1)
                and result["status_after"].get("last") == label
            )
            proof["sliders"][label] = {"presses": presses, "channel": channel,
                                       "direction": result["direction"],
                                       "changed_cells": ch, "gate": gate,
                                       "ui_adjusted": ui_adjusted,
                                       "status_before": result["status_before"],
                                       "status_after": result["status_after"],
                                       "profile_color_before": result["profile_color_before"],
                                       "profile_color_after": result["profile_color_after"],
                                       "color_precondition_ok": result["color_precondition_ok"],
                                       "changed_render": is_ok,
                                       "before": result["before"], "after": result["after"]}
            ui = "ui" if ui_adjusted else "NOUI"
            val = result["status_after"].get("value_f") if result["status_after"].get("is_float") else result["status_after"].get("value_i")
            print(f"  [{ 'YES' if is_ok else 'no ' }] {label:28s} ch({channel})={ch:5d} gate={gate} dir={result['direction']} {ui} val={val}", file=sys.stderr)
            out.mkdir(parents=True, exist_ok=True)
            (out / "PROOF.json").write_text(json.dumps(proof, indent=2))

        n_ok = sum(1 for v in proof["sliders"].values() if v["changed_render"])
        proof["summary"] = {"sliders_total": len(selected), "sliders_changed_render": n_ok,
                            "all_changed_render": n_ok == len(selected)}
        out.mkdir(parents=True, exist_ok=True)
        (out / "PROOF.json").write_text(json.dumps(proof, indent=2))
        print(json.dumps(proof["summary"], indent=2))
        print(f"[driver] {n_ok}/{len(selected)} sliders changed the rendered buffer", file=sys.stderr)
        try: c.call("QUIT", "", timeout=2)
        except Exception: pass
        c.close(); time.sleep(0.5)
        return 0 if n_ok == len(selected) else 1
    finally:
        try: proc.terminate(); proc.communicate(timeout=5)
        except Exception: proc.kill(); proc.communicate()


if __name__ == "__main__":
    raise SystemExit(main())
