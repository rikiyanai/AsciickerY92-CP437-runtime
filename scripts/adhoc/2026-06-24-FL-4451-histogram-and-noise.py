#!/usr/bin/env python3
"""Diagnostic: on-screen material histogram + no-edit animation noise floor."""
import socket, json, os, time
HOST, PORT = "localhost", 8765
OUT = "/tmp/fl4451_diag"; os.makedirs(OUT, exist_ok=True)
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
    png=os.path.join(OUT,tag+".png"); cells=os.path.join(OUT,tag+".cells"); bridge=os.path.join(OUT,tag+".bridge")
    send("CAPTURE_TERMPP_FRAME_WITH_BUFFER", f"{png} {cells} {bridge}", idle=3.0, hard=20.0); time.sleep(1.5)
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

c0,b0 = cap("n0"); time.sleep(1.0)
c1,_  = cap("n1"); time.sleep(1.0)
c2,_  = cap("n2")
mat = load_mat(b0)
from collections import Counter
hist = Counter(mat.values())
print("ON-SCREEN material histogram (material_id: cell_count), top 15:")
for mid,n in sorted(hist.items(), key=lambda kv:-kv[1])[:15]:
    print(f"   material {mid}: {n}")
all_ids = set(range(0,120))
present = set(hist.keys())
unused = sorted(all_ids - present)[:10]
print("UNUSED on-screen candidates (0 cells):", unused[:10])
n01 = diff(load_cells(c0), load_cells(c1))
n12 = diff(load_cells(c1), load_cells(c2))
# attribute noise to material
noise_by_mat = Counter(mat.get(k) for k in set(n01)|set(n12))
print(f"\nNO-EDIT noise floor: cap0->cap1 changed={len(n01)}, cap1->cap2 changed={len(n12)}")
print("noise changed-cell material histogram:", dict(noise_by_mat.most_common(8)))
