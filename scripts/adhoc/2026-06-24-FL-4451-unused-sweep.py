#!/usr/bin/env python3
"""Warm-state sweep: edit several UNUSED on-screen materials, each must change only ~noise cells of OTHER materials."""
import socket, json, os, time
from collections import Counter
HOST, PORT = "localhost", 8765
OUT="/tmp/fl4451_sweep"; os.makedirs(OUT, exist_ok=True)
def send(cmd, params="", idle=2.0, hard=18.0):
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
    png=os.path.join(OUT,tag+".png"); cells=os.path.join(OUT,tag+".cells"); bridge=os.path.join(OUT,tag+".bridge")
    send("CAPTURE_TERMPP_FRAME_WITH_BUFFER", f"{png} {cells} {bridge}", idle=3.0, hard=20.0); time.sleep(1.5)
    return cells, bridge
def lc(p):
    d={}
    for ln in open(p):
        o=json.loads(ln)
        if o.get("kind")=="cell": d[(o["x"],o["y"])]=(o["fg"],o["bk"],o.get("final_gid"))
    return d
def lm(p):
    d={}
    for ln in open(p):
        o=json.loads(ln)
        if o.get("kind")=="cell": d[(o["x"],o["y"])]=o.get("material_id")
    return d
def diff(a,b): return [k for k in a if a.get(k)!=b.get(k)]

prev,bprev = cap("warm_base")
mat = lm(bprev)
for m in [7, 8, 11, 13, 7]:  # repeat 7 at end to check determinism
    send("FL4260_RENDERING_PROOF", f"{m} 3 0", idle=2.0); time.sleep(2.2)
    cur,_ = cap(f"after_{m}")
    ch = diff(lc(prev), lc(cur))
    h = Counter(mat.get(k) for k in ch)
    print(f"edit unused {m}: changed={len(ch):4d}  hist={dict(h.most_common(5))}")
    prev = cur
