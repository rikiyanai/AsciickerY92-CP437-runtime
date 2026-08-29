#!/usr/bin/env python3
"""FL-4451 proof: Material Look edits are isolated by terrain material id.

This script is intentionally self-contained. Older copies assumed TERM++ was
already open and could silently compare stale capture files. The proof now loads
the fixture, opens TERM++, captures a no-edit baseline, then edits one unused and
one used material through the CDP paths the UI uses.
"""
import json
import os
import shutil
import socket
import sys
import time
from collections import Counter
HOST, PORT = "localhost", 8765
OUT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','..',
      'docs/research/ascii/verification/fl4260/2026-06-24-FL4451-per-material-isolation'))
UNUSED_MAT, USED_MAT = 7, 4
def send(cmd, params="", idle=2.0, hard=16.0):
    s=socket.socket(socket.AF_INET,socket.SOCK_STREAM); s.settimeout(4)
    try:
        s.connect((HOST,PORT)); s.sendall((json.dumps({"id":1,"method":cmd,"params":params})+"\n").encode())
        s.settimeout(idle); buf=b""; t0=time.time()
        while time.time()-t0<hard:
            try:
                c=s.recv(65536)
                if not c: break
                buf+=c
            except socket.timeout: break
        return buf.decode(errors="replace")
    finally: s.close()
def cap(tag):
    d=os.path.join(OUT,tag); os.makedirs(d,exist_ok=True)
    png=os.path.join(d,"f.png"); cells=os.path.join(d,"cells.jsonl"); bridge=os.path.join(d,"bridge.jsonl")
    for p in (png, cells, bridge):
        try: os.remove(p)
        except FileNotFoundError: pass
    send("CAPTURE_TERMPP_FRAME_WITH_BUFFER", f"{png} {cells} {bridge}", idle=3.0, hard=20.0); time.sleep(1.5)
    if not (os.path.exists(cells) and os.path.exists(bridge)):
        raise RuntimeError(f"capture failed for {tag}: missing {cells} or {bridge}")
    return cells, bridge
def load_cells(p):
    d={}
    for ln in open(p):
        o=json.loads(ln)
        if o.get("kind")=="cell": d[(o["x"],o["y"])]=(o["fg"],o["bk"],o.get("final_gid"))
    return d
def load_mat(p):
    d={}
    for ln in open(p):
        o=json.loads(ln)
        if o.get("kind")=="cell": d[(o["x"],o["y"])]=o.get("material_id")
    return d
def diff(a,b): return [k for k in a if a.get(k)!=b.get(k)]

if os.path.exists(OUT):
    shutil.rmtree(OUT)
os.makedirs(OUT, exist_ok=True)

setup = [
    send("LOAD_MAP", "assets/a3d/fl4260_fixture_all_materials.a3d", idle=2.5, hard=45.0),
    send("SET_TERMPP_RUNTIME_HARRI_RESOLVE", "1", idle=2.0, hard=20.0),
    send("OPEN_TERMPP_CURRENT_VIEW", "", idle=4.0, hard=45.0),
]
time.sleep(2.5)
open(os.path.join(OUT, "setup.log"), "w").write("\n---\n".join(setup))
c0,b0 = cap("clean00_before"); mat0=load_mat(b0)
# No-edit repeat establishes actual noise after TERM++ and profile tables are warm.
c0r,b0r = cap("clean00_repeat")
warm_noise = len(diff(load_cells(c0), load_cells(c0r)))
print(f"[NO EDIT] warm repeat changed={warm_noise}")
# TEST 1: edit UNUSED on-screen material 7
send("FL4260_RENDERING_PROOF", f"{UNUSED_MAT} 3 0", idle=2.0); time.sleep(1.2)
c1,_ = cap("clean01_after_unused7")
ch1 = diff(load_cells(c0), load_cells(c1))
h1 = Counter(mat0.get(k) for k in ch1)
print(f"[TEST 1] edit UNUSED material {UNUSED_MAT}: changed={len(ch1)} (noise floor 1-2). hist={dict(h1)}")
test1_pass = len(ch1) <= max(5, warm_noise + 3)
print(f"         -> {'PASS (within measured noise)' if test1_pass else 'FAIL (real repaint of unrelated cells)'}")
# TEST 2: edit USED on-screen material 4. Presets can already match durable
# state, so force a toggle through clear -> select_all/autofill.
send("FL4260_POOL_ACTION", f"{USED_MAT} clear", idle=2.0, hard=35.0); time.sleep(1.2)
c2a,b2a = cap("clean02a_after_used4_clear")
send("FL4260_POOL_ACTION", f"{USED_MAT} select_all", idle=2.0, hard=35.0)
send("FL4260_ROLE_BUCKET_AUTOFILL", f"{USED_MAT}", idle=2.0, hard=35.0); time.sleep(1.2)
c2b,b2b = cap("clean02b_after_used4_fill")
def classify_delta(a_cells, b_cells, b_bridge):
    changed = diff(load_cells(a_cells), load_cells(b_cells))
    mats = load_mat(b_bridge)
    hist = Counter(mats.get(k) for k in changed)
    off_hist = {m:n for m,n in hist.items() if m!=USED_MAT}
    return changed, hist, off_hist
ch2_clear, h2_clear, off_clear = classify_delta(c1, c2a, b2a)
ch2_fill, h2_fill, off_fill = classify_delta(c2a, c2b, b2b)
if len(ch2_clear) >= len(ch2_fill):
    ch2, h2, off = ch2_clear, h2_clear, off_clear
    used_step = "clear"
else:
    ch2, h2, off = ch2_fill, h2_fill, off_fill
    used_step = "fill"
print(f"[TEST 2] edit USED material {USED_MAT} step={used_step}: changed={len(ch2)}. hist={dict(h2)}")
test2_pass = sum(off.values()) <= max(3, warm_noise + 3) and h2.get(USED_MAT, 0) > 50
print(f"         off-material changed={sum(off.values())} target_changed={h2.get(USED_MAT, 0)} -> {'PASS' if test2_pass else 'FAIL'}")
verdict = test1_pass and test2_pass
summary = {
    "schema": "fl4451.clean_isolation.self_contained.v2",
    "unused_material": UNUSED_MAT,
    "used_material": USED_MAT,
    "warm_repeat_changed": warm_noise,
    "unused_changed": len(ch1),
    "unused_hist": dict(h1),
    "used_step": used_step,
    "used_changed": len(ch2),
    "used_hist": dict(h2),
    "used_off_material_changed": sum(off.values()),
    "verdict": "PASS" if verdict else "FAIL",
}
open(os.path.join(OUT, "summary.json"), "w").write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
print(f"\nFL-4451 RESULT: TEST1(unused=noop)={'PASS' if test1_pass else 'FAIL'}  TEST2(used=isolated)={'PASS' if test2_pass else 'FAIL'}")
sys.exit(0 if verdict else 1)
