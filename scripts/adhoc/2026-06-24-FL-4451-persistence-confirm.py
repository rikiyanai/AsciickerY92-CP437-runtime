#!/usr/bin/env python3
"""Distinguish persistent cross-material bug from transient flicker.
   A=settled, edit unused 9, B=settled, C=settled(no edit). diff(A,B)>>diff(B,C) => bug."""
import socket, json, os, time
from collections import Counter
HOST, PORT = "localhost", 8765
OUT="/tmp/fl4451_persist"; os.makedirs(OUT, exist_ok=True)
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

time.sleep(2.0)
a,ba = cap("A"); time.sleep(2.0)
# settle baseline noise without edit
a2,_ = cap("A2")
noise = diff(lc(a), lc(a2))
print(f"pre-edit settle noise diff(A,A2)={len(noise)}")
# edit unused material 9
send("FL4260_RENDERING_PROOF","9 3 0", idle=2.0); time.sleep(2.5)
b,_ = cap("B"); time.sleep(2.0)
c,_ = cap("C")
mat = lm(ba)
dAB = diff(lc(a2), lc(b))   # effect of editing unused 9
dBC = diff(lc(b), lc(c))    # post-edit settle noise
hAB = Counter(mat.get(k) for k in dAB)
print(f"edit-unused-9 effect diff(A2,B)={len(dAB)}  hist={dict(hAB.most_common(6))}")
print(f"post-edit settle diff(B,C)={len(dBC)}")
verdict = "BUG CONFIRMED: editing unused material persistently repaints unrelated cells" if len(dAB) > 20 and len(dAB) > 5*max(1,len(dBC)) else "transient/noise only"
print("VERDICT:", verdict)
