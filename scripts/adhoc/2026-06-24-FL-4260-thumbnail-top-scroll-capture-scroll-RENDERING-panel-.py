# Ad hoc script: FL-4260 thumbnail top-scroll capture: scroll RENDERING panel to y=0 and capture to show cylinder thumbnail at panel top
# Created: 2026-06-24
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Scroll RENDERING panel to top (y=0) and capture, so the cylinder thumbnail is visible."""
import socket, json, os, time
HOST, PORT = "localhost", 8765
OUT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..',
      'docs/research/ascii/verification/fl4260/2026-06-24-thumbnail-visibility'))
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
print("FOCUS9", send("FL4260_FOCUS_SIDEBAR_TAB","9")[-50:].replace("\n"," "))
print("SELECT", send("FL4260_RENDERING_PROOF","1 -1 0")[-50:].replace("\n"," "))
time.sleep(0.4)
print("SCROLL0", send("FL4260_SCROLL_Y","0")[-70:].replace("\n"," "))
time.sleep(0.5)
d=os.path.join(OUT,"02_panel_top_thumbnail"); os.makedirs(d,exist_ok=True)
send("CAPTURE_UI_FRAME", d, idle=2.5, hard=12.0)
print("CAP", os.path.exists(os.path.join(d,"ui_frame.png")))
