# Ad hoc script: FL-4260 thumbnail live-color proof: apply preset to material 1, scroll RENDERING to top, capture cylinder thumbnail (bands should turn from black to preset colors)
# Created: 2026-06-24
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Apply a preset to material 1 and capture the thumbnail to prove it's live (bands colored)."""
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
# apply preset index 2 to material 1 (seeds bright band colors via Fl4260ApplyProfileEditFromPreset)
print("APPLY", send("FL4260_RENDERING_PROOF","1 2 0")[-70:].replace("\n"," "))
time.sleep(0.6)
print("SCROLL0", send("FL4260_SCROLL_Y","0")[-60:].replace("\n"," "))
time.sleep(0.6)
d=os.path.join(OUT,"03_thumbnail_preset_applied"); os.makedirs(d,exist_ok=True)
send("CAPTURE_UI_FRAME", d, idle=2.5, hard=12.0)
print("CAP", os.path.exists(os.path.join(d,"ui_frame.png")))
