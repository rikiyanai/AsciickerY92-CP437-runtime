# Ad hoc script: FL-4260 thumbnail visibility proof: focus RENDERING sidebar tab, select a material, capture UI frame showing the cylinder thumbnail
# Created: 2026-06-24
# Canonical gap: <describe what tool should own this>

#!/usr/bin/env python3
"""Open RENDERING tab, select material, capture UI frame to show the cylinder thumbnail."""
import socket, json, os, time
HOST, PORT = "localhost", 8765
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..',
                   'docs/research/ascii/verification/fl4260/2026-06-24-thumbnail-visibility')
OUT = os.path.abspath(OUT)
os.makedirs(OUT, exist_ok=True)

def send(cmd, params="", idle=1.5, hard=10.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        s.connect((HOST, PORT))
        s.sendall((json.dumps({"id":1,"method":cmd,"params":params})+"\n").encode())
        s.settimeout(idle)
        buf=b""; t0=time.time()
        while time.time()-t0<hard:
            try:
                c=s.recv(65536)
                if not c: break
                buf+=c
            except socket.timeout:
                break
        return buf.decode(errors="replace")
    except Exception as e:
        return f"ERR:{e}"
    finally:
        s.close()

print("PING", send("FL4260_GET_RENDER_MODE","")[-60:].replace("\n"," "))
print("FOCUS9", send("FL4260_FOCUS_SIDEBAR_TAB","9")[-60:].replace("\n"," "))
time.sleep(0.5)
print("SELECT", send("FL4260_RENDERING_PROOF","1 -1 0")[-60:].replace("\n"," "))
time.sleep(0.8)
d=os.path.join(OUT,"01_rendering_tab_mat1")
os.makedirs(d, exist_ok=True)
send("CAPTURE_UI_FRAME", d, idle=2.5, hard=12.0)
png=os.path.join(d,"ui_frame.png")
print("CAP exists=", os.path.exists(png), png)
