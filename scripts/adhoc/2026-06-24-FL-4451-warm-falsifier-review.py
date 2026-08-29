#!/usr/bin/env python3
"""REVIEW Codex's narrow fix: warm case (the case I proved failing).
   Edit unused materials as first edits; cross-material leak must be ~0 (noise floor 1-2)."""
import socket, json, os, time
from collections import Counter
HOST, PORT = "localhost", 8765
OUT="/tmp/fl4451_review"; os.makedirs(OUT, exist_ok=True)
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

print("OPEN", send("OPEN_TERMPP","harri=1", idle=3.0, hard=16.0)[-40:].replace("\n"," "))
time.sleep(2.5)
base,bbridge = cap("base"); mat=lm(bbridge)
hist=Counter(mat.values())
print("on-screen materials:", dict(hist.most_common(6)))
# noise floor
nb,_ = cap("base2"); noise=len(diff(lc(base),lc(nb)))
print("noise floor (no edit):", noise)
prev=nb
worst=0
for m in [7, 8, 11, 13, 22, 99]:  # all unused (not in on-screen histogram except check)
    on=hist.get(m,0)
    send("FL4260_RENDERING_PROOF", f"{m} 3 0", idle=2.0); time.sleep(2.2)
    cur,_ = cap(f"u{m}")
    ch=diff(lc(prev),lc(cur)); h=Counter(mat.get(k) for k in ch)
    leak={mid:n for mid,n in h.items() if mid!=m}
    leakn=sum(leak.values())
    worst=max(worst,leakn)
    print(f"edit UNUSED {m} (on-screen={on}): changed={len(ch):4d}  leak_to_other_materials={leakn}  hist={dict(h.most_common(4))}")
    prev=cur
print(f"\nWARM FALSIFIER: worst cross-material leak={worst}  (noise floor {noise}) -> {'PASS' if worst<=max(3,noise+2) else 'FAIL'}")
