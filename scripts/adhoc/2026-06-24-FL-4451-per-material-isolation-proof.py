#!/usr/bin/env python3
"""Independent FL-4451 proof on the real TERM++ buffer:
   (1) editing an UNUSED material changes 0 final cells;
   (2) editing a USED material changes ONLY that material's cells."""
import socket, json, os, time
HOST, PORT = "localhost", 8765
OUT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','..',
      'docs/research/ascii/verification/fl4260/2026-06-24-FL4451-per-material-isolation'))
os.makedirs(OUT, exist_ok=True)
UNUSED_MAT = 77
USED_MAT   = 1
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
    send("CAPTURE_TERMPP_FRAME_WITH_BUFFER", f"{png} {cells} {bridge}", idle=3.0, hard=20.0)
    time.sleep(1.5)
    return cells, bridge
def load_cells(p):
    d={}
    with open(p) as fh:
        for ln in fh:
            o=json.loads(ln)
            if o.get("kind")=="cell": d[(o["x"],o["y"])]=(o["fg"],o["bk"],o.get("final_gid"))
    return d
def load_mat(p):
    d={}
    with open(p) as fh:
        for ln in fh:
            o=json.loads(ln)
            if o.get("kind")=="cell": d[(o["x"],o["y"])]=o.get("material_id")
    return d
def diff(a,b):
    return [k for k in a if a.get(k)!=b.get(k)]

print("OPEN", send("OPEN_TERMPP","harri=1", idle=3.0, hard=16.0)[-50:].replace("\n"," "))
time.sleep(2.5)
c0,b0 = cap("00_before")
matmap = load_mat(b0)
used_total = sum(1 for v in matmap.values() if v==USED_MAT)
unused_total = sum(1 for v in matmap.values() if v==UNUSED_MAT)
print(f"baseline: material {USED_MAT} cells on screen={used_total}; material {UNUSED_MAT} cells on screen={unused_total}")

# (1) edit UNUSED material
print("EDIT-UNUSED", send("FL4260_RENDERING_PROOF", f"{UNUSED_MAT} 3 0", idle=2.0)[-50:].replace("\n"," "))
time.sleep=getattr(time,'sleep'); time.sleep(1.2)
c1,_ = cap("01_after_unused")
ch1 = diff(load_cells(c0), load_cells(c1))
print(f"\n[TEST 1] editing UNUSED material {UNUSED_MAT}: changed_final_cells={len(ch1)}  EXPECT 0  -> {'PASS' if len(ch1)==0 else 'FAIL'}")

# (2) edit USED material
print("EDIT-USED", send("FL4260_RENDERING_PROOF", f"{USED_MAT} 3 0", idle=2.0)[-50:].replace("\n"," "))
time.sleep(1.2)
c2,b2 = cap("02_after_used")
ch2 = diff(load_cells(c1), load_cells(c2))
m2 = load_mat(b2)
by_mat={}
for k in ch2:
    mid=m2.get(k); by_mat[mid]=by_mat.get(mid,0)+1
off = {mid:n for mid,n in by_mat.items() if mid!=USED_MAT}
print(f"[TEST 2] editing USED material {USED_MAT}: changed_final_cells={len(ch2)}")
print(f"         changed-cell material_id histogram={by_mat}")
print(f"         cells NOT material {USED_MAT}={sum(off.values())}  EXPECT 0 -> {'PASS' if not off else 'FAIL'}")
print(f"\nRESULT: TEST1={'PASS' if len(ch1)==0 else 'FAIL'}  TEST2={'PASS' if (len(ch2)>0 and not off) else 'FAIL'}")
