#!/usr/bin/env python3
"""Positive isolation: editing USED material 4 must change ONLY material-4 cells (+noise)."""
import socket, json, os, time
from collections import Counter
HOST, PORT = "localhost", 8765
OUT="/tmp/fl4451_used"; os.makedirs(OUT, exist_ok=True)
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
send("OPEN_TERMPP","harri=1", idle=3.0, hard=16.0); time.sleep(2.5)
a,ba=cap("a"); time.sleep(1.5); a2,_=cap("a2")
noise=len(diff(lc(a),lc(a2)))
mat=lm(ba)
# apply a contrasting preset (stone vertical_angular=3) to used material 4
send("FL4260_RENDERING_PROOF","4 3 0", idle=2.0); time.sleep(2.2)
b,_=cap("b")
ch=diff(lc(a2),lc(b)); h=Counter(mat.get(k) for k in ch)
off={mid:n for mid,n in h.items() if mid!=4}
print(f"noise floor={noise}")
print(f"edit USED material 4: changed={len(ch)}  hist={dict(h.most_common(5))}")
print(f"off-material (non-4) changed={sum(off.values())} -> isolation {'PASS' if sum(off.values())<=noise+2 and len(ch)>100 else 'FAIL'}")
