#!/usr/bin/env python3
"""Verify default-look thumbnail now shows the material color (not black) and scroll_focus=0 lands on it."""
import socket, json, os, time
HOST, PORT = "localhost", 8765
OUT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..',
      'docs/research/ascii/verification/fl4260/2026-06-24-thumbnail-defaultlook-fix'))
os.makedirs(OUT, exist_ok=True)
def send(cmd, params="", idle=1.5, hard=10.0):
    s=socket.socket(socket.AF_INET,socket.SOCK_STREAM); s.settimeout(3)
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
# DEFAULT LOOK: select material 1, preset -1 (none), scroll_focus 0 (now = true top)
print("SELECT", send("FL4260_RENDERING_PROOF","1 -1 0")[-60:].replace("\n"," "))
time.sleep(0.8)
d=os.path.join(OUT,"01_mat1_default_look_top"); os.makedirs(d,exist_ok=True)
send("CAPTURE_UI_FRAME", d, idle=2.5, hard=12.0)
print("CAP mat1", os.path.exists(os.path.join(d,"ui_frame.png")))
# also material 5 (different material) default look
print("SELECT5", send("FL4260_RENDERING_PROOF","5 -1 0")[-60:].replace("\n"," "))
time.sleep(0.8)
d2=os.path.join(OUT,"02_mat5_default_look_top"); os.makedirs(d2,exist_ok=True)
send("CAPTURE_UI_FRAME", d2, idle=2.5, hard=12.0)
print("CAP mat5", os.path.exists(os.path.join(d2,"ui_frame.png")))
