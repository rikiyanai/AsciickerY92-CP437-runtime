#!/usr/bin/env python3
"""Verify cce42612b: changing sun pitch/yaw changes terrain (material 4) cells in real TERM++."""
import socket, json, os, time
from collections import Counter
HOST,PORT="localhost",8765
OUT="/tmp/fl_light"; os.makedirs(OUT,exist_ok=True)
def send(cmd,params="",idle=2.0,hard=16.0):
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
def cap(t):
    cells=os.path.join(OUT,t+".cells"); bridge=os.path.join(OUT,t+".bridge")
    send("CAPTURE_TERMPP_FRAME_WITH_BUFFER",f"{os.path.join(OUT,t+'.png')} {cells} {bridge}",idle=3.0,hard=20.0); time.sleep(1.3)
    return cells,bridge
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
def dterr(a,b,mat):  # changed cells that are terrain material 4
    return [k for k in a if a.get(k)!=b.get(k) and mat.get(k)==4]
print("OPEN",send("OPEN_TERMPP","harri=1",idle=3.0,hard=16.0)[-30:].replace("\n"," "))
time.sleep(2.5)
send("SET_LIGHT_CONTROL","30 45 12 0.5"); time.sleep(1.2)
a,ba=cap("A_yaw45"); mat=lm(ba)
# noise: re-set same light, capture
send("SET_LIGHT_CONTROL","30 45 12 0.5"); time.sleep(1.2)
a2,_=cap("A2_same")
noise=len(dterr(lc(a),lc(a2),mat))
# flip yaw 180
send("SET_LIGHT_CONTROL","30 -135 12 0.5"); time.sleep(1.2)
b,_=cap("B_yawflip")
dyaw=len(dterr(lc(a2),lc(b),mat))
# steep pitch
send("SET_LIGHT_CONTROL","85 45 12 0.5"); time.sleep(1.2)
c,_=cap("C_pitch85")
dpitch=len(dterr(lc(b),lc(c),mat))
print(f"terrain(mat4) cells changed by SAME-light (noise) = {noise}")
print(f"terrain(mat4) cells changed by YAW 45->-135        = {dyaw}")
print(f"terrain(mat4) cells changed by PITCH 30->85         = {dpitch}")
print(f"VERDICT: {'PASS (sun direction changes terrain)' if dyaw> max(20,noise*3) or dpitch>max(20,noise*3) else 'FAIL (no effect)'}")
